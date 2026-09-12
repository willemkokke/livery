"""Fetch the latest curated tools into a throwaway prefix — the engine behind
`fm tools.provision`.

The stubs are read from the *installed* binaries (`fm tools.sync`), so
telling an editor what the newest release accepts means having the newest
release on `PATH` — across five ecosystems (PyPI, npm, bun, Go, C++), none of
which should be allowed to touch the machine's own environment.

One isolated prefix answers all of it. Almost every curated tool ships an
installable PyPI wheel — including the Rust ones (ruff, uv, prek, git-cliff)
and the C++ ones (cmake, ninja) — so `uv tool install --upgrade` into a
private `UV_TOOL_DIR`/`UV_TOOL_BIN_DIR` covers the majority and cleans up with
one `rm -rf`. What's left is bun (its own release), the node CLIs it installs,
and the Go CLIs (a prebuilt release asset):

* **uv** — `uv tool install --upgrade <pkg>`, tools and launchers under the
  prefix; nothing lands in `~/.local` or the system site-packages.
* **bun** — bun's GitHub release, unpacked into the prefix. Provisioned
  *first*, because the node tier runs through it.
* **node** — `bun add --global` with `BUN_INSTALL` pointed at the prefix.
* **github / gitlab** — the latest release asset for this platform, matched
  from the release's own asset list (so `Darwin`/`x86_64` vs `darwin`/`x64`
  naming needn't be transcribed), unpacked, binary placed in the prefix.
* **system** — git, docker, the uv running this: already on `PATH`, left be.
* **deferred** — parked, with a reason (tea, until it stops hanging on
  `--help`).

Everything writes under one prefix and `PATH="<prefix>/bin:$PATH"` is all a
`sync` needs to read the newest binaries; deleting the prefix undoes it. This
is a maintainer tool: it shells out and downloads, and it is never on the
completion hot path.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from livery.toolroom.tools._machinery._drivers import Driver


class ProvisionError(Exception):
    """A tool could not be fetched — reported per tool, never fatal."""


@dataclass(frozen=True)
class Outcome:
    """What became of one tool: the line `provision` prints."""

    key: str
    kind: str
    status: str  # "ok" | "fail" | "skip" | "deferred"
    detail: str = ""


def bin_dir(prefix: Path) -> Path:
    """The one directory to put on `PATH`; every tier lands its launchers here."""
    return prefix / "bin"


def exe(name: str, *, windows: bool | None = None) -> str:
    """*name* as this platform spells an executable.

    Windows resolves a command through `PATHEXT`, so an extensionless PE is
    invisible to `shutil.which` and to every reader that follows it. Every
    tier that names a binary needs the same suffix, and each one that grew
    its own copy of the conditional was a separate Windows bug — the placed
    file gained `.exe` while the tier still looked for the bare name.
    """
    if windows is None:
        windows = os.name == "nt"
    return f"{name}.exe" if windows else name


def write_node_shim(into: Path, bun: Path) -> Path | None:
    """A `node` that is really bun, written beside the launchers.

    What the npm tier installs is a launcher beginning `#!/usr/bin/env node`.
    bun stands in for node when bun itself runs a script, but a launcher
    spawned as a subprocess has its shebang resolved by the operating system,
    with bun nowhere in the chain. So on a machine without node the tier is
    unrunnable, and the prefix this writes into is the thing every reader
    reaches for — `sync`, `audit`, `spec`, and anyone who follows the
    `export PATH=<prefix>/bin` line provisioning prints.

    Left to the caller to remember, it is forgotten: a `sync` on a node-less
    machine recorded cspell and markdownlint as version `unknown`, and that
    reading then sat at the floor of the chain where `prime` could not walk
    past it — "unknown is not among the listed releases". Re-syncing fixed
    the base and left the poison underneath, so the cost of the omission
    outlived its cause. It belongs next to the launchers it exists for.

    Written only where there is no real node, so a machine that has one keeps
    using it, and one with neither is no worse off than before.
    """
    import stat

    if shutil.which("node") is not None:
        return None
    into.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        # cmd resolves `node` through PATHEXT, so the shim is a .cmd that
        # forwards; %* keeps the argument list intact, quotes and all.
        shim = into / "node.cmd"
        shim.write_text(f'@echo off\r\n"{bun}" --bun %*\r\n', encoding="utf-8")
    else:
        shim = into / "node"
        shim.write_text(f'#!/bin/sh\nexec "{bun}" --bun "$@"\n', encoding="utf-8")
        shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return shim


def provision(
    drivers: tuple[Driver, ...], prefix: Path, *, only: str = ""
) -> list[Outcome]:
    """Materialise the latest of each curated tool under *prefix*.

    Tiers run in the one order that matters: bun before the node CLIs that
    need it. Each tool's failure is its own line, never the run's — a missing
    binary should read as one skipped hint, not a broken provision.
    """
    prefix = Path(prefix)
    bin_dir(prefix).mkdir(parents=True, exist_ok=True)
    wanted = {name.strip() for name in only.split(",") if name.strip()}
    chosen = [d for d in drivers if not wanted or d.key in wanted]
    outcomes: list[Outcome] = []
    by_kind: dict[str, list[Driver]] = {}
    for driver in chosen:
        if driver.source == "manual":
            # A hand-written stub (the shells): its stub is curated, not read
            # from a binary, so there is nothing to fetch — skip it rather than
            # try `uv tool install bash` and print a spurious failure.
            outcomes.append(Outcome(driver.key, "manual", "skip", "hand-written"))
            continue
        by_kind.setdefault(driver.provision.kind, []).append(driver)

    for driver in by_kind.get("deferred", []):
        outcomes.append(
            Outcome(driver.key, "deferred", "deferred", driver.provision.note)
        )
    outcomes += _uv_tier(prefix, by_kind.get("uv", []))
    outcomes += _python_tier(prefix, by_kind.get("python", []))
    for driver in by_kind.get("bun", []):  # before node: node runs through bun
        outcomes.append(_release(prefix, driver, host="github"))
    outcomes += _node_tier(prefix, by_kind.get("node", []))
    forges = ("github", "gitlab", "gitea")
    for driver in [d for kind in forges for d in by_kind.get(kind, [])]:
        outcomes.append(_release(prefix, driver, host=driver.provision.kind))
    outcomes += _docker_tier(prefix, by_kind.get("docker", []))
    outcomes += _man_tier(prefix, by_kind.get("man", []))
    return outcomes


def _man_tier(prefix: Path, drivers: list[Driver]) -> list[Outcome]:
    """The newest manual, unpacked where a prefix read will find it.

    Nothing is installed and nothing is run: for a tool read from its
    manual the pages *are* the tool, so provisioning fetches the newest
    set and `tools.sync` reads those rather than the machine's own git.
    """
    from livery.toolroom.tools._machinery import _toolfetch

    outcomes: list[Outcome] = []
    for driver in drivers:
        try:
            found = _toolfetch.releases(driver)
        except _toolfetch.Unreachable as blocked:
            outcomes.append(Outcome(driver.key, "man", "fail", str(blocked)))
            continue
        if not found:
            outcomes.append(Outcome(driver.key, "man", "fail", "no manuals listed"))
            continue
        newest = found[0]
        # Staged per driver and *merged* into the shared tree: the tier holds
        # more than one tool's pages (git's man1/… beside ssh.1), so a
        # replace-the-tree copy would leave only whichever driver ran last.
        placed = _toolfetch.install(driver, newest, prefix / ".man" / driver.key)
        if placed is None:
            outcomes.append(
                Outcome(driver.key, "man", "fail", f"{newest.version} unavailable")
            )
            continue
        shutil.copytree(placed, prefix / "man", dirs_exist_ok=True)
        outcomes.append(Outcome(driver.key, "man", "ok", newest.version))
    return outcomes


def _docker_tier(prefix: Path, drivers: list[Driver]) -> list[Outcome]:
    """The newest static build docker publishes for this platform.

    Its own tier because docker indexes by platform and architecture rather
    than by release: there is no asset list to pick from, only a directory
    of every version this machine could run. `_toolfetch` already knows how
    to read that index, so provisioning asks it for the newest and installs
    exactly as a walk would.
    """
    from livery.toolroom.tools._machinery import _toolfetch

    outcomes: list[Outcome] = []
    for driver in drivers:
        try:
            found = _toolfetch.releases(driver)
        except _toolfetch.Unreachable as blocked:
            outcomes.append(Outcome(driver.key, "docker", "fail", str(blocked)))
            continue
        if not found:
            outcomes.append(Outcome(driver.key, "docker", "fail", "no builds listed"))
            continue
        newest = found[0]
        placed = _toolfetch.install(driver, newest, prefix / ".docker")
        if placed is None:
            outcomes.append(
                Outcome(
                    driver.key, "docker", "fail", f"{newest.version} would not install"
                )
            )
            continue
        binary = exe(driver.name)
        target = bin_dir(prefix) / binary
        target.unlink(missing_ok=True)
        shutil.copy2(placed / binary, target)
        # The plugins came down beside the staged binary; a reader finds
        # them from the binary it resolves to, which here is the prefix's.
        home = _toolfetch.home_beside(placed)
        if home.is_dir():
            shutil.copytree(
                home, _toolfetch.home_beside(bin_dir(prefix)), dirs_exist_ok=True
            )
        outcomes.append(Outcome(driver.key, "docker", "ok", newest.version))
    return outcomes


# --- uv tier -----------------------------------------------------------------


def _uv_env(prefix: Path) -> dict[str, str]:
    """Uv's install targets, redirected so nothing escapes the prefix."""
    return {
        **os.environ,
        "UV_TOOL_DIR": str(prefix / "uv-tools"),
        "UV_TOOL_BIN_DIR": str(bin_dir(prefix)),
    }


def _uv_tier(prefix: Path, drivers: list[Driver]) -> list[Outcome]:
    """`uv tool install --upgrade` each distinct package into the prefix.

    A driver's `provision.plugins` ride along as `--with` packages in the tool's
    own isolated environment, so a plugin-extended CLI (pytest + pytest-cov) is
    installed whole and its plugin flags are there to read.
    """
    env = _uv_env(prefix)
    installed: dict[tuple[str, tuple[str, ...]], bool] = {}
    outcomes: list[Outcome] = []
    for driver in drivers:
        package = driver.provision.target(driver.name)
        plugins = driver.provision.plugins
        key = (package, plugins)
        if key not in installed:
            withs = [f"--with={p}" for p in plugins]
            installed[key] = _run(
                ["uv", "tool", "install", "--upgrade", package, *withs], env=env
            )
        ok = installed[key]
        detail = package if not plugins else f"{package} (+{', '.join(plugins)})"
        outcomes.append(Outcome(driver.key, "uv", "ok" if ok else "fail", detail))
    return outcomes


# --- python tier (an interpreter to read `--help` from) ----------------------


def _newest_python(driver: Driver) -> str:
    """The newest release the option history's own index reports.

    Asked of that index rather than left to `uv python install 3`, for two
    reasons. Inside a project uv resolves a loose request against the active
    environment first — `find 3` here answers with the venv's 3.13 — so the
    snapshot would quietly describe whatever interpreter the checkout uses.
    And the provisioned head must be a release the listing can place, or a
    prime cannot position the floor it is walking back from.
    """
    from livery.toolroom.tools._machinery import _toolfetch

    found = _toolfetch.releases(driver)
    return found[0].version if found else "3"


def _python_tier(prefix: Path, drivers: list[Driver]) -> list[Outcome]:
    """`uv python install` each requested interpreter, linked into the prefix.

    python is provisioned like any other tool — an interpreter whose `--help`
    is read for the stub. The *runtime* `tools.python` always targets
    `sys.executable`; provisioning only supplies versions to extract from, so
    the stub reflects real pythons rather than whatever `python`/`python3` a
    machine happens to have on PATH.
    """
    outcomes: list[Outcome] = []
    for driver in drivers:
        version = driver.provision.package or _newest_python(driver)
        if not _run(["uv", "python", "install", version], env=dict(os.environ)):
            outcomes.append(
                Outcome(driver.key, "python", "fail", f"uv python install {version}")
            )
            continue
        try:
            found = _fm_run(
                ["uv", "python", "find", version],
                recorded=False,  # a lookup, not part of the run's story
                timeout=60,
                nofail=True,
                env=dict(os.environ),
            )
            path = Path(found.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            path = Path()
        if not path.name or not path.exists():
            outcomes.append(
                Outcome(driver.key, "python", "fail", f"no python {version} found")
            )
            continue
        placed = _place_interpreter(bin_dir(prefix), path)
        if placed is None:
            outcomes.append(
                Outcome(driver.key, "python", "fail", f"could not place {version}")
            )
            continue
        outcomes.append(Outcome(driver.key, "python", "ok", f"{version} ({path})"))
    return outcomes


def _place_interpreter(bindir: Path, target: Path) -> Path | None:
    """Put *target* on the prefix's PATH as `python`, however this OS allows.

    A symlink where symlinks are free. Windows grants them only with
    developer mode or elevation, so there it falls back to a one-line
    launcher — a copy would be a broken interpreter, since CPython finds its
    standard library relative to the real executable and a lone copied
    `python.exe` finds nothing.
    """
    bindir.mkdir(parents=True, exist_ok=True)
    link = bindir / exe("python")
    link.unlink(missing_ok=True)
    try:
        link.symlink_to(target)
        return link
    except (OSError, NotImplementedError):
        pass
    if os.name != "nt":  # pragma: no cover - POSIX symlinks do not fail
        return None
    shim = bindir / "python.cmd"
    shim.write_text(f'@echo off\r\n"{target}" %*\r\n', encoding="utf-8")
    return shim


# --- node tier (through the provisioned bun) ---------------------------------


def _node_tier(prefix: Path, drivers: list[Driver]) -> list[Outcome]:
    """`bun add --global` each package, with bun's install dir the prefix."""
    if not drivers:
        return []
    bun = bin_dir(prefix) / exe("bun")
    if not bun.exists():
        return [
            Outcome(d.key, "node", "fail", "bun was not provisioned first")
            for d in drivers
        ]
    # Beside the launchers, before they are installed: what `bun add` writes
    # into this directory cannot be run without it.
    write_node_shim(bin_dir(prefix), bun)
    env = {
        **os.environ,
        "BUN_INSTALL": str(prefix),  # global bin lands in <prefix>/bin
        "PATH": f"{bin_dir(prefix)}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    packages = sorted({d.provision.target(d.name) for d in drivers})
    ok = _run([str(bun), "add", "--global", *packages], env=env)
    return [
        Outcome(d.key, "node", "ok" if ok else "fail", d.provision.target(d.name))
        for d in drivers
    ]


# --- release tier (github / gitlab, and bun) ---------------------------------


def _release(prefix: Path, driver: Driver, *, host: str) -> Outcome:
    """Download the latest release asset for this platform and unpack it."""
    kind = driver.provision.kind
    try:
        assets = _latest_assets(host, driver.provision.repo)
        name, url = _pick_asset(assets)
        archive = _download(url, prefix)
        placed = _extract_binary(archive, driver.name, bin_dir(prefix))
    except ProvisionError as exc:
        return Outcome(driver.key, kind, "fail", str(exc))
    return Outcome(driver.key, kind, "ok", f"{placed.name} ({name})")


def _latest_assets(host: str, repo: str) -> list[tuple[str, str]]:
    """`[(asset name, download url)]` for *repo*'s latest release."""
    return assets_for(host, repo)


def assets_for(host: str, repo: str, tag: str = "") -> list[tuple[str, str]]:
    """`[(asset name, download url)]` for one release — latest when *tag* is
    empty, and a specific tag when priming a tool's history.
    """
    if not repo:
        raise ProvisionError("no repo to fetch from")
    if host == "github":
        where = f"tags/{tag}" if tag else "latest"
        data = _get_json(f"https://api.github.com/repos/{repo}/releases/{where}")
        assets = data.get("assets", [])
        return [(a["name"], a["browser_download_url"]) for a in assets]
    if host == "gitlab":
        quoted = urllib.parse.quote(repo, safe="")
        where = urllib.parse.quote(tag, safe="") if tag else "permalink/latest"
        data = _get_json(
            f"https://gitlab.com/api/v4/projects/{quoted}/releases/{where}"
        )
        links = data.get("assets", {}).get("links", [])
        return [(a["name"], a.get("direct_asset_url") or a["url"]) for a in links]
    if host == "gitea":
        # GitHub-shaped: same endpoints, same asset fields, gitea.com base.
        where = f"tags/{tag}" if tag else "latest"
        data = _get_json(f"https://gitea.com/api/v1/repos/{repo}/releases/{where}")
        assets = data.get("assets", [])
        return [(a["name"], a["browser_download_url"]) for a in assets]
    raise ProvisionError(f"unknown release host {host!r}")


# The alias sets that fold one platform's many spellings into a match: bun
# says `darwin`/`aarch64`, goreleaser `Darwin`/`x86_64`, gh `macOS`/`amd64`.
_OS_ALIASES = {
    "darwin": ("darwin", "macos", "apple", "osx"),
    "linux": ("linux",),
    "windows": ("windows", "win"),
}
_ARCH_ALIASES = {
    "arm64": ("arm64", "aarch64"),
    "aarch64": ("arm64", "aarch64"),
    "x86_64": ("x86_64", "amd64", "x64", "x86-64"),
    "amd64": ("x86_64", "amd64", "x64", "x86-64"),
}
_ARCHIVES = (".tar.gz", ".tgz", ".tar.xz", ".tar.bz2", ".zip")
# Sidecar files that ride alongside a real asset — never the binary.
_SIDECARS = (
    ".sha256",
    ".sha256sum",
    ".sig",
    ".asc",
    ".txt",
    ".pem",
    ".sbom",
    ".json",  # compose ships provenance, sbom and sigstore beside each build
)
# Build variants that sit beside the canonical asset for the same platform:
# bun's `-profile`/`-baseline`, a `-debug` build, a `musl` libc. Preferred
# against, never excluded — the canonical build is what a task wants.
_VARIANTS = ("profile", "baseline", "debug", "musl", "-static")


def _platform_tokens() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """This machine's OS and CPU aliases, for matching an asset name."""
    os_aliases = _OS_ALIASES.get(
        platform.system().lower(), (platform.system().lower(),)
    )
    machine = platform.machine().lower()
    arch_aliases = _ARCH_ALIASES.get(machine, (machine,))
    return os_aliases, arch_aliases


def _pick_asset(assets: list[tuple[str, str]]) -> tuple[str, str]:
    """The one asset for this OS and CPU, archives before bare binaries."""
    os_aliases, arch_aliases = _platform_tokens()

    def hit(alias: str, low: str) -> bool:
        # At a word start only: `win` must find `windows` and `win64` but
        # never the tail of `darwin` — which is also the *shorter* name, so
        # the length tiebreak below would prefer the wrong OS forever.
        return re.search(rf"(?<![a-z]){re.escape(alias)}", low) is not None

    def matches(name: str) -> bool:
        low = name.lower()
        if low.endswith(_SIDECARS):
            return False
        return any(hit(o, low) for o in os_aliases) and any(
            hit(a, low) for a in arch_aliases
        )

    candidates = [(name, url) for name, url in assets if matches(name)]
    if not candidates:
        raise ProvisionError("no release asset for this platform")

    def rank(asset: tuple[str, str]) -> tuple[bool, bool, int, str]:
        # Prefer an archive over a bare binary, the canonical build over a
        # variant (bun ships `-profile`/`-baseline` beside the plain one), and
        # then the shortest name — a qualifier only ever lengthens it.
        low = asset[0].lower()
        variant = any(marker in low for marker in _VARIANTS)
        return (not low.endswith(_ARCHIVES), variant, len(asset[0]), asset[0])

    candidates.sort(key=rank)
    return candidates[0]


# --- download + unpack -------------------------------------------------------


def api_headers(url: str) -> dict[str, str]:
    """What to send an index — a User-Agent, and a token when one is offered.

    GitHub allows 60 unauthenticated API calls an hour *per IP* and 5,000 with
    a token. Sixty sounds ample for a set with two forge-hosted tools until
    the IP is a shared CI runner, where the budget is spent by whoever else is
    on it; a prime that walks ten releases each is then throttled by strangers.

    Read from the environment rather than fetched from `gh`, so nothing here
    depends on a CLI being installed: `GH_TOKEN=$(gh auth token)` locally, and
    `secrets.GITHUB_TOKEN` in Actions. Absent, everything still works — just
    against the smaller budget.

    Sent to GitHub's **API host only**. urllib carries headers across
    redirects, and a release asset redirects to a CDN that has no business
    seeing a credential.
    """
    import os

    headers = {"User-Agent": "footman-provision"}
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _get_json(url: str) -> Any:
    """A JSON API response (shape is the endpoint's business) — GitHub and
    GitLab both want a User-Agent.
    """
    request = urllib.request.Request(url, headers=api_headers(url))
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError) as exc:
        raise ProvisionError(f"{url}: {exc}") from exc


def _download(url: str, prefix: Path) -> Path:
    """Fetch *url* into the prefix's cache, reusing a prior download by name."""
    cache = prefix / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    dest = cache / url.rsplit("/", 1)[-1]
    if dest.exists() and dest.stat().st_size:
        return dest
    request = urllib.request.Request(url, headers={"User-Agent": "footman-provision"})
    # Three tries, because a dropped connection says nothing about the asset.
    # A refresh leg died on `Remote end closed connection without response`
    # part-way through gh's zip: the release was there, the download was not
    # finished. Retrying costs a second; not retrying cost a platform's
    # observations.
    for attempt in range(_TRIES):
        try:
            with (
                urllib.request.urlopen(request, timeout=120) as response,
                open(dest, "wb") as out,
            ):
                shutil.copyfileobj(response, out)
            return dest
        except (urllib.error.URLError, OSError) as exc:
            dest.unlink(missing_ok=True)  # a part-written file is not a cache hit
            if attempt + 1 == _TRIES or not _worth_retrying(exc):
                raise ProvisionError(f"{url}: {exc}") from exc
            time.sleep(_BACKOFF * (attempt + 1))
    raise ProvisionError(f"{url}: unreachable")  # pragma: no cover - loop returns


_TRIES = 3
_BACKOFF = 1.0


def _worth_retrying(exc: BaseException) -> bool:
    """Whether this failure is about the connection rather than the asset.

    A 404 is an answer — the asset is not there and asking again will not
    change that. A dropped connection, a timeout, a 5xx or a 429 are the
    network having a moment.
    """
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in (408, 429) or 500 <= exc.code < 600
    return True


def _extract_binary(
    archive: Path, tool: str, into: Path, *, windows: bool | None = None
) -> Path:
    """Unpack *archive* and place its `tool` binary in *into*, executable.

    Release archives nest the binary under a versioned directory, so the
    whole tree is searched for a file named `tool` (or `tool.exe`); a bare
    downloaded binary is taken as-is. Only file members are eligible: an
    archive that nests the binary under a directory of the same name
    (docker ships `docker/docker`) would otherwise match the directory and
    write nothing. On Windows the placed file is named
    `tool.exe` whatever the archive called it — `shutil.which` resolves
    through `PATHEXT`, and an extensionless PE is invisible to it.
    """
    if windows is None:
        windows = os.name == "nt"
    wanted = {tool, f"{tool}.exe"}
    into.mkdir(parents=True, exist_ok=True)
    dest = into / exe(tool, windows=windows)
    if archive.name.lower().endswith((".tar.gz", ".tgz", ".tar.xz", ".tar.bz2")):
        with tarfile.open(archive) as tar:
            member = next(
                (
                    m
                    for m in tar.getmembers()
                    if m.isfile() and Path(m.name).name in wanted
                ),
                None,
            )
            if member is None:
                raise ProvisionError(f"{tool} not found inside {archive.name}")
            source = tar.extractfile(member)
            if source is None:
                raise ProvisionError(f"{tool} is not a file inside {archive.name}")
            dest.write_bytes(source.read())
    elif archive.name.lower().endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            name = next(
                (
                    n
                    for n in zf.namelist()
                    if not n.endswith("/") and Path(n).name in wanted
                ),
                None,
            )
            if name is None:
                raise ProvisionError(f"{tool} not found inside {archive.name}")
            dest.write_bytes(zf.read(name))
    else:  # a bare binary, downloaded directly
        dest.write_bytes(archive.read_bytes())
    dest.chmod(0o755)
    return dest


# --- subprocess --------------------------------------------------------------


def _fm_run(*args: Any, **kwargs: Any) -> Any:
    """`context.run`, imported at call time — provisioning is reachable from
    the stub generator, which has no interest in the run machinery.
    """
    from livery.footman.context import run

    return run(*args, **kwargs)


def _run(argv: list[str], *, env: dict[str, str]) -> bool:
    """Run an install command, quietly; its success is all the caller needs."""
    try:
        done = _fm_run(argv, recorded=False, timeout=600, nofail=True, env=env)
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(done.code == 0)
