"""Fetch the latest curated tools into a throwaway prefix — the engine behind
`fm tools.provision`.

The stubs are read from the *installed* binaries (`fm tools.sync`), so
telling an editor what the newest release accepts means having the newest
release on `PATH` — across five ecosystems (PyPI, npm, bun, Go, C++), none of
which should be allowed to touch the machine's own environment.

One isolated prefix answers all of it. A tool comes from its own release
when it has one: a forge tier downloads the release asset for the host. PyPI
is the tier for programs that are Python, where the wheel is the release; a
wheel around a Rust or C++ binary is a wrapper with a platform gap wherever
the wheel is missing, so a tool that is not Python never sits on the `uv`
tier (a test pins the tier's members). What the forge tiers do not cover is
bun (its own release), the node CLIs it installs, and the Python tools:

* **uv** — `uv tool install --upgrade <pkg>`, tools and launchers under the
  prefix; nothing lands in `~/.local` or the system site-packages. Python
  programs only.
* **bun** — bun's GitHub release, unpacked into the prefix. Provisioned
  *first*, because the node tier runs through it.
* **node** — `bun add --global` with `BUN_INSTALL` pointed at the prefix.
* **github / gitlab** — the latest release asset for this platform, matched
  from the release's own asset list (so `Darwin`/`x86_64` vs `darwin`/`x64`
  naming needn't be transcribed), unpacked whole, a launcher placed in the prefix.
* **system** — git, docker, the uv running this: already on `PATH`, left be.
* **deferred** — parked, with a reason (tea, until it stops hanging on
  `--help`).

Everything writes under one prefix and `PATH="<prefix>/bin:$PATH"` is all a
`sync` needs to read the newest binaries; deleting the prefix undoes it. This
is a maintainer tool: it shells out and downloads, and it is never on the
completion hot path.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from livery.toolroom.bench._drivers import Driver
from livery.toolroom.store import (
    ARCHIVE_SUFFIXES,
    HOSTS,
    FetchError,
    UnpackError,
    fetch_file,
    fetch_json,
    unpack,
)


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
    if shutil.which("node") is not None:
        return None
    return write_launcher(into, "node", bun, "--bun")


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
    from livery.toolroom.bench import _toolfetch

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
    from livery.toolroom.bench import _toolfetch

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
        launcher = next(
            (
                p
                for p in sorted(placed.iterdir())
                if p.is_file() and p.stem == driver.name
            ),
            None,
        )
        if launcher is None:
            outcomes.append(
                Outcome(
                    driver.key, "docker", "fail", f"{newest.version} placed nothing"
                )
            )
            continue
        target = bin_dir(prefix) / launcher.name
        target.unlink(missing_ok=True)
        shutil.copy2(launcher, target)
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
    from livery.toolroom.bench import _toolfetch

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
        placed = place_binary(archive, driver.name, bin_dir(prefix))
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
    "darwin": ("darwin", "macos", "apple", "osx", "mac"),
    "linux": ("linux",),
    "windows": ("windows", "win"),
}
# `universal` is macOS's one build for both architectures (cmake ships
# `macos-universal`), so it matches either arm64 or x86_64 there.
# `winarm64` is ninja's spelling, the OS and the CPU in one word, which
# the word-start match would otherwise never see.
_ARCH_ALIASES = {
    "arm64": ("arm64", "aarch64", "universal", "winarm64"),
    "aarch64": ("arm64", "aarch64", "universal", "winarm64"),
    "x86_64": ("x86_64", "amd64", "x64", "x86-64", "universal"),
    "amd64": ("x86_64", "amd64", "x64", "x86-64", "universal"),
}
# Every architecture token a release may spell, aliased above or not: an
# asset that names none of them names no architecture at all.
_ARCH_TOKENS = (
    *{token for aliases in _ARCH_ALIASES.values() for token in aliases},
    "i386",
    "i686",
    "386",
    "armv6",
    "armv7",
    "armv7l",
    "ppc64le",
    "s390x",
    "riscv64",
    "loongarch64",
)
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
# bun's `-profile`/`-baseline`, a `-debug` build, a `musl` libc, a MinGW
# build beside the MSVC one (git-cliff ships `-pc-windows-gnu` and
# `-pc-windows-msvc`, and the shorter name would win). Preferred against,
# never excluded — the canonical build is what a task wants.
_VARIANTS = ("profile", "baseline", "debug", "musl", "-static", "windows-gnu")


_SYSTEMS = {"macos": "darwin", "linux": "linux", "windows": "windows"}
_MACHINES = {"x64": "x86_64", "arm": "arm64"}
HOST_TOKENS = {
    host: (_SYSTEMS[host.split("-")[0]], _MACHINES[host.split("-")[1]])
    for host in HOSTS
}
"""Each store host key as an OS and a CPU, in the spellings the alias tables fold."""


def _platform_tokens(host: str = "") -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The OS and CPU aliases for matching an asset name: *host*'s, or this machine's.

    Raises:
        ProvisionError: for a *host* that is not one of `HOST_TOKENS`.
    """
    if host:
        if host not in HOST_TOKENS:
            raise ProvisionError(
                f"unknown host {host!r}; the hosts are {', '.join(HOST_TOKENS)}"
            )
        system, machine = HOST_TOKENS[host]
    else:
        system = platform.system().lower()
        machine = platform.machine().lower()
    return _OS_ALIASES.get(system, (system,)), _ARCH_ALIASES.get(machine, (machine,))


def _pick_asset(assets: list[tuple[str, str]], *, host: str = "") -> tuple[str, str]:
    """The one asset for *host*, or this OS and CPU; archives before bare binaries."""
    os_aliases, arch_aliases = _platform_tokens(host)

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
        # A release that names the OS and no architecture at all is the
        # x64 build on linux and Windows, and the universal build on
        # macOS: the shape a release kept from before its arm builds
        # (ninja's `ninja-linux.zip` beside `ninja-linux-aarch64.zip`).
        # An asset naming any other architecture never matches this way.
        def arch_less(name: str) -> bool:
            low = name.lower()
            return (
                not low.endswith(_SIDECARS)
                and any(hit(o, low) for o in os_aliases)
                and not any(hit(a, low) for a in _ARCH_TOKENS)
            )

        x64 = any(a in ("x86_64", "amd64") for a in arch_aliases)
        if x64 or "darwin" in os_aliases:
            candidates = [(name, url) for name, url in assets if arch_less(name)]
    if not candidates:
        raise ProvisionError(f"no release asset for {host or 'this platform'}")

    def rank(asset: tuple[str, str]) -> tuple[bool, bool, int, str]:
        # Prefer an archive over a bare binary, the canonical build over a
        # variant (bun ships `-profile`/`-baseline` beside the plain one), and
        # then the shortest name — a qualifier only ever lengthens it.
        low = asset[0].lower()
        variant = any(marker in low for marker in _VARIANTS)
        return (not low.endswith(ARCHIVE_SUFFIXES), variant, len(asset[0]), asset[0])

    candidates.sort(key=rank)
    return candidates[0]


# --- download + unpack -------------------------------------------------------


def _get_json(url: str) -> Any:
    """A JSON API response (shape is the endpoint's business), through the store's read.

    Raises:
        ProvisionError: when the URL cannot be read or answers no JSON.
    """
    try:
        return fetch_json(url)
    except FetchError as exc:
        raise ProvisionError(str(exc)) from exc


def _download(url: str, prefix: Path) -> Path:
    """Fetch *url* into the prefix's cache through the store's read; the file.

    Raises:
        ProvisionError: when the URL cannot be read after the store's tries.
    """
    try:
        return fetch_file(url, prefix / ".cache")
    except FetchError as exc:
        raise ProvisionError(str(exc)) from exc


def place_binary(
    archive: Path,
    tool: str,
    into: Path,
    *,
    windows: bool | None = None,
    launcher: bool = True,
) -> Path:
    """Unpack *archive* whole and put its `tool` in *into*; the file placed there.

    An archive is unpacked as it is, into a `trees/<tool>` directory
    beside *into*, and the binary runs where it lies, so an app that
    loads its runtime from beside itself (a PyInstaller directory app,
    conan's release) finds it. What lands in *into* is a launcher that
    runs the binary in its tree: a shell script, or a `.cmd` on Windows
    where `shutil.which` finds it through `PATHEXT`. With *launcher*
    off the binary itself is copied into *into*, for a place that must
    hold the real file (a docker plugin directory). A downloaded bare
    binary is placed as it is either way. The binary is the first file
    member named `tool` or `tool.exe`, so a directory of the same name
    (docker's `docker/docker`) never matches.

    Raises:
        ProvisionError: when the archive will not unpack or holds no
            such file.
    """
    if windows is None:
        windows = os.name == "nt"
    into.mkdir(parents=True, exist_ok=True)
    if not archive.name.lower().endswith(
        ARCHIVE_SUFFIXES
    ):  # a bare binary, downloaded directly
        dest = into / exe(tool, windows=windows)
        dest.write_bytes(archive.read_bytes())
        dest.chmod(0o755)
        return dest
    tree = into.parent / "trees" / tool
    shutil.rmtree(tree, ignore_errors=True)
    try:
        unpack(archive, tree)
    except UnpackError as exc:
        raise ProvisionError(str(exc)) from exc
    found = _find_binary(tree, tool)
    if found is None:
        raise ProvisionError(f"{tool} not found inside {archive.name}")
    found.chmod(found.stat().st_mode | 0o755)
    if not launcher:
        dest = into / exe(tool, windows=windows)
        shutil.copy2(found, dest)
        return dest
    return write_launcher(into, tool, found, windows=windows)


def _find_binary(tree: Path, tool: str) -> Path | None:
    """The first file under *tree* named *tool* or `tool.exe`, in path order."""
    wanted = {tool, f"{tool}.exe"}
    for path in sorted(tree.rglob("*")):
        if path.is_file() and path.name in wanted:
            return path
    return None


def write_launcher(
    into: Path, name: str, target: Path, *args: str, windows: bool | None = None
) -> Path:
    """A launcher in *into* that runs *target* with *args* first; its path.

    A shell script that `exec`s the target, so the process is the target
    itself and finds what sits beside it; on Windows a `.cmd`, which
    `cmd` resolves through `PATHEXT` and forwards `%*` to whole.
    """
    import stat

    if windows is None:
        windows = os.name == "nt"
    into.mkdir(parents=True, exist_ok=True)
    lead = "".join(f" {arg}" for arg in args)
    if windows:
        shim = into / f"{name}.cmd"
        shim.write_text(f'@echo off\r\n"{target}"{lead} %*\r\n', encoding="utf-8")
        return shim
    shim = into / name
    shim.write_text(f'#!/bin/sh\nexec "{target}"{lead} "$@"\n', encoding="utf-8")
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return shim


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
