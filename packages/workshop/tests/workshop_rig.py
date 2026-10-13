"""A rig under a temporary data directory, and a machine whose processes are fakes.

An environment of `livery.workshop._devenv` keeps its state under
footman's data directory and starts its processes through the seams
that module names: how a process starts, whether one lives, how one
stops, the forge's health and API, and where the binaries come from.
The `rig` fixture points the data and config directories at the
test's own; the `machine` fixture replaces every seam, so an
environment comes up, goes down and is removed with no process
started. A test module imports `rig_fixture` and `machine_fixture` to
register them; this is not a conftest, for the reason `workshop_seeds`
gives. `modes_bind` marks a test that makes a directory this user may
not change.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from livery.workshop import _devenv


def _modes_bind() -> bool:
    """Whether a directory's mode stops this process removing what it holds.

    Linux and macOS honour it for every user but root; Windows decides by
    other rules.
    """
    if sys.platform == "win32":
        return False
    return os.geteuid() != 0


#: The mark of a test that makes a directory this user may not change: it
#: runs where a directory's mode binds the process, and skips elsewhere.
modes_bind = pytest.mark.skipif(
    not _modes_bind(), reason="a directory's mode binds only a POSIX user, not root"
)


@pytest.fixture(name="rig")
def rig_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A rig under a temporary data directory, with the config directory beside it."""
    data = tmp_path / "data"
    config = tmp_path / "config"
    data.mkdir()
    config.mkdir()
    monkeypatch.setattr("livery.footman._context.data_dir", lambda: data)
    monkeypatch.setattr("livery.footman._context.config_dir", lambda: config)
    return data / "forge-dev"


class Machine:
    """The seams: processes started, alive and stopped, the health and the API."""

    def __init__(self) -> None:
        self.spawned: list[tuple[list[str], Path]] = []
        self.pids: set[int] = set()
        self.stopped: list[int] = []
        self.next_pid = 4000

    def spawn(self, argv: list[str], *, cwd: Path, log: Path) -> int:
        """Record *argv* as started in *cwd*, write its log, and give it a new pid."""
        self.spawned.append((list(argv), cwd))
        self.next_pid += 1
        self.pids.add(self.next_pid)
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("started\n")
        return self.next_pid

    def alive(self, pid: int) -> bool:
        """Whether *pid* was started and not stopped."""
        return pid in self.pids

    def stop(self, pid: int, **kwargs: object) -> bool:
        """Record *pid* as stopped; it always goes."""
        self.stopped.append(pid)
        self.pids.discard(pid)
        return True


@pytest.fixture(name="machine")
def machine_fixture(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Machine:
    """Every seam of the rig replaced: Gitea takes port 43210 and seeds token-abc."""
    fake = Machine()
    monkeypatch.setattr(_devenv, "SPAWN", fake.spawn)
    monkeypatch.setattr(_devenv, "ALIVE", fake.alive)
    monkeypatch.setattr(_devenv, "STOP", fake.stop)
    monkeypatch.setattr(_devenv, "HEALTHY", lambda url: True)
    monkeypatch.setattr(_devenv, "API", lambda url, path, token, **kw: 200)
    monkeypatch.setattr(_devenv, "this_host", lambda: "macos-arm")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    for name in ("gitea", "gitea-runner"):
        (binaries / name).write_text("#!/bin/sh\n")
    monkeypatch.setattr(
        _devenv,
        "BINARY",
        lambda name, version, **kw: binaries / name.replace("_", "-"),
    )

    def cli(argv: list[str], **kwargs: object) -> object:
        class _Result:
            code = 0
            stderr = ""

            def __init__(self, out: str) -> None:
                self.stdout = out

        if "generate-access-token" in argv:
            return _Result("token-abc\n")
        if "generate-runner-token" in argv:
            return _Result("reg-xyz\n")
        return _Result("")

    import livery.footman as footman

    monkeypatch.setattr(footman, "run", cli)
    monkeypatch.setattr(_devenv, "free_port", lambda: 43210)
    return fake
