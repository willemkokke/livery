"""One ssh connection per remote host, shared by every git the library spawns.

Every network git call opens a fresh ssh connection, and the handshake
is about half of a quiet fetch. OpenSSH multiplexes connections over
one master when `ControlMaster` is on: [livery.git.ssh_environment][]
builds the `GIT_SSH_COMMAND` that turns it on for a spawned git, with
the sockets in one owner-only directory, and a master lingers for the
idle window so the next verb reuses it.

Two fixed names in the socket directory are the whole memory: the
socket itself, `<user>@<host>:<port>`, which is the proof a master is
up, and `unsupported`, written with ssh's words when a probe found
multiplexing broken on this host, which every later call reads first.
Delete the file to probe again.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

#: The file whose presence turns multiplexing off; it holds ssh's words.
UNSUPPORTED = "unsupported"

#: ssh's own template for a socket name: user, host and port, 17 bytes for
#: `git@github.com:22`.
SOCKET = "%r@%h:%p"

#: The longest socket name the path guard allows for, past the directory:
#: `git@github.com:22` is 17 bytes, a forge on a long host with a port 27.
_NAME_BUDGET = 32

#: The unix socket path limit, with its terminator: 104 bytes on macOS and
#: the BSDs, 108 on Linux.
_LIMIT = 104 if sys.platform == "darwin" else 108

#: A master whose TCP connection died (sleep, a network change) notices
#: within 30 s and exits, so the next call opens fresh instead of hanging
#: to the TCP timeout.
_KEEPALIVE = ("ServerAliveInterval=15", "ServerAliveCountMax=2")


def ssh_sockets(data_dir: Path) -> Path:
    """Where the plugin keeps its sockets: `ssh` under the runner's *data_dir*.

    The data directory is owner-only and durable, which a socket wants,
    and short enough on every platform for the socket path limit below.
    """
    return data_dir / "ssh"


def _socket_path(socket_dir: Path) -> str:
    """The `ControlPath` under *socket_dir*, as ssh expands it per host.

    A POSIX spelling on every platform: git hands the command to a
    shell, and Git for Windows' shell and ssh read a drive path only in
    that form.
    """
    return f"{socket_dir.as_posix()}/{SOCKET}"


def ssh_command(socket_dir: Path, *, idle: str) -> str:
    """The `GIT_SSH_COMMAND` that multiplexes over a master under *socket_dir*.

    `ControlMaster=auto` reuses a master when one is up and opens one
    otherwise, replacing a stale socket from an unclean exit; two
    spawns racing to open a master is allowed, one wins and the other
    connects directly. `ControlPersist=<idle>` keeps the master that
    long after the last client. The path is quoted twice over: git
    hands the command to a shell, and ssh then splits an option's
    value on spaces unless it is double-quoted, and footman's data
    directory on macOS has a space in it.
    """
    options = (
        "ControlMaster=auto",
        shlex.quote(f'ControlPath="{_socket_path(socket_dir)}"'),
        f"ControlPersist={idle}",
        *_KEEPALIVE,
    )
    return "ssh " + " ".join(f"-o {option}" for option in options)


def ssh_environment(
    socket_dir: Path,
    *,
    idle: str = "10m",
    environ: Mapping[str, str] | None = None,
    configured: str = "",
) -> tuple[dict[str, str], str]:
    """The environment that multiplexes a spawned git's ssh, or why not.

    Args:
        socket_dir: Where the sockets live; owner-only and short.
        idle: How long a master lingers after its last client, in ssh's
            own spelling (`10m`).
        environ: The environment the spawn would inherit; the process's
            own when omitted.
        configured: What git's `core.sshCommand` says, empty when unset.

    Returns:
        The mapping to merge into the spawn's environment, holding
        `GIT_SSH_COMMAND`, and an empty reason; or an empty mapping and
        the reason multiplexing is not used. The reason is empty when
        the person's own setting won, which is theirs to know about.
    """
    present = (os.environ if environ is None else environ).get("GIT_SSH_COMMAND", "")
    if present and socket_dir.as_posix() not in present:
        return {}, ""
    if configured:
        return {}, ""
    directory = socket_dir.as_posix().encode()
    if len(directory) + 1 + _NAME_BUDGET > _LIMIT:
        return {}, (
            f"ssh multiplexing off: {socket_dir} leaves no room for a socket name"
            f" under the {_LIMIT}-byte unix socket path limit"
        )
    verdict = socket_dir / UNSUPPORTED
    if verdict.is_file():
        words = verdict.read_text("utf-8").strip()
        return {}, f"ssh multiplexing off ({verdict}): {words}"
    socket_dir.mkdir(parents=True, exist_ok=True)
    return {"GIT_SSH_COMMAND": ssh_command(socket_dir, idle=idle)}, ""


def record_unsupported(socket_dir: Path, words: str) -> Path:
    """Turn multiplexing off under *socket_dir* with *words*; the file written."""
    socket_dir.mkdir(parents=True, exist_ok=True)
    verdict = socket_dir / UNSUPPORTED
    verdict.write_text(words.strip() + "\n", "utf-8")
    return verdict


def remote_host(url: str) -> tuple[str, str, int] | None:
    """The user, host and port an ssh remote *url* names; None for any other transport.

    Both spellings ssh remotes take: `git@github.com:owner/repo.git`
    and `ssh://git@host:2222/owner/repo.git`. An `http`, `https` or
    `file` remote opens no ssh connection.
    """
    if "://" in url:
        parts = urlsplit(url)
        if parts.scheme not in ("ssh", "git+ssh", "ssh+git"):
            return None
        return parts.username or "git", parts.hostname or "", parts.port or 22
    head, sep, _ = url.partition(":")
    if not sep or "/" in head:
        return None
    user, _, host = head.rpartition("@")
    return user or "git", host, 22


def master_up(socket_dir: Path, url: str, *, timeout: float = 10.0) -> bool:
    """Whether a master for *url*'s host is up under *socket_dir*.

    `ssh -O check` asks the master over its socket and opens no
    connection; a remote that is not ssh has no master and answers
    False.
    """
    named = remote_host(url)
    if named is None:
        return False
    user, host, port = named
    try:
        checked = subprocess.run(
            [
                "ssh",
                "-O",
                "check",
                "-o",
                f"ControlPath={_socket_path(socket_dir)}",
                "-p",
                str(port),
                f"{user}@{host}",
            ],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return checked.returncode == 0
