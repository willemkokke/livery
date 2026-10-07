"""The workspace entry contract: the emitted ``setup.sh`` at the root.

One choke point owns machine readiness. The script ensures uv at the
lock's pinned version, syncs the venv against the lock, records the
sync receipt, and emits the environment: sourced by a shell it enters
that shell, and ``setup.sh github`` persists the emission into
``GITHUB_ENV``/``GITHUB_PATH`` so every later CI step calls the
runner bare. Between entries the per-command reconcile
(``livery.workshop._reconcile``) keeps the venv following the lock.

The script is a generated artifact like the CI workflows: emitted by
``livery.workshop._ci_generate.generate``, written by
``fm sync``, judged by the drift check. POSIX only; a
Windows CI leg runs it under the runner's bash, and a pwsh spelling
is deferred to the tool-store port.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import livery.footman as footman


def locked_uv_version(root: Path) -> str:
    """The uv version the workspace lock pins, or "".

    uv rides the dev group, so ``uv.lock`` pins it exactly. A
    workspace without a lock, or whose lock does not carry uv, has no
    pin to derive and answers "": the emitters then leave the
    bootstrap unpinned rather than inventing a version.
    """
    lock = root / "uv.lock"
    if not lock.is_file():
        return ""
    try:
        parsed = tomllib.loads(lock.read_text("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return ""
    for package in parsed.get("package", []):
        if isinstance(package, dict) and package.get("name") == "uv":
            return str(package.get("version", ""))
    return ""


def installer_url(pin: str) -> str:
    """The uv installer URL, versioned when a *pin* exists."""
    if pin:
        return f"https://astral.sh/uv/{pin}/install.sh"
    return "https://astral.sh/uv/install.sh"


# __PROG__ and __INSTALLER__ are substituted at emission; a template
# with markers instead of an f-string, because the script itself is
# full of shell braces.
_SCRIPT = """\
# The entry contract: uv at the lock's pin -> the venv synced against
# the lock, its native members left for later -> `__PROG__ sync --locked`,
# the one a person runs, changing nothing a commit holds: the tools
# installed, the stubs and the checkout's own files written -> the
# environment entered here -> the native members built against it.
# Source it to enter this shell; `setup.sh github` persists the
# emission (GITHUB_ENV/GITHUB_PATH) for the CI steps after it, which
# then call __PROG__ bare.
_root="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf __INSTALLER__ | sh >&2
    PATH="$HOME/.local/bin:$PATH"
    export PATH
    # Name the install that failed here. Left to fall through, uv's
    # absence surfaces below as "uv sync failed", which sends the
    # reader after the venv instead of after the network.
    if ! command -v uv >/dev/null 2>&1; then
        echo "setup: could not install uv - check network access to astral.sh" >&2
        return 1 2>/dev/null || exit 1
    fi
fi
# On a GitHub job uv's cache and the runner's data directory, the tool
# store under it, live under the runner's temp, the working drive. On
# a Windows runner the working drive is not the home directory's: a
# store on the home drive cannot hardlink its entry points into the
# checkout and copies instead, and so would uv's cache into the venv.
# The workflow restores the store before this script runs and saves
# it after the job; uv's cache is placed for the drive alone. Exported
# before the sync and the materialise, and persisted by the emission
# below for every later step. A placement the job already carries
# stands: a runner that keeps its caches across jobs sets them in its
# own environment, and the temp is only where nothing is set.
if [ "${1:-}" = github ] && [ -n "${RUNNER_TEMP:-}" ]; then
    UV_CACHE_DIR="${UV_CACHE_DIR:-$RUNNER_TEMP/uv-cache}"
    __DATA_DIR_VAR__="${__DATA_DIR_VAR__:-$RUNNER_TEMP/footman}"
    export UV_CACHE_DIR __DATA_DIR_VAR__
fi
# An ARRAY, not a string: CI may invoke this with zsh, which does not
# word-split unquoted expansions, so a two-word string would arrive
# as one argument. Arrays expand the same under bash and zsh.
_sync_args=()
[ -f "$_root/uv.lock" ] && _sync_args+=(--locked)
# A native member builds its extension at install against the tools
# the store supplies below (the compiler's helpers, the conan
# provider), so the first sync leaves it out and the second, after
# the environment is entered here, builds it.
_native=(__NATIVE_SKIP__)
_sync() { uv sync --project "$_root" "${_sync_args[@]}" "$@" >&2; }
_sync "${_native[@]}" \\
    || { sleep 10; _sync "${_native[@]}"; } \\
    || { echo "setup: uv sync failed" >&2; return 1 2>/dev/null || exit 1; }
# The sync receipt: the lock as this venv last saw it. The runner's
# per-command reconcile compares the two and re-syncs on drift.
[ -f "$_root/uv.lock" ] && cp "$_root/uv.lock" "$_root/.venv/.workshop-sync-receipt"
_run() { uv run --project "$_root" --no-sync __PROG__ "$@"; }
# The sync a person runs, in the mode that changes nothing a commit
# holds: the branch stays, no tracked file is written. It installs
# the tools the lock holds, with a receipt each, and the stubs the type
# checkers read into typings/, and writes the checkout's own untracked
# files (the agent's fragments, skills and settings), so a job holds
# what a person's checkout holds. A lock that is not current refuses
# here, naming the lock. A tool the store could not supply is named and
# not fatal: the gate says which check that cost. The emission below
# puts the receipts' paths on PATH, so a tool the venv does not carry
# (an archive from its own release) resolves for the gate.
_run sync --locked >&2 \\
    || { echo "setup: __PROG__ sync --locked refused; its message names why" >&2; \\
         return 1 2>/dev/null || exit 1; }
# Entered here, whether sourced or run: the native members build in
# this shell, and a sourcing shell keeps it.
eval "$(_run env.emit posix)"
if [ ${#_native[@]} -gt 0 ]; then
    _sync || { sleep 10; _sync; } \\
        || { echo "setup: a native member did not build against the tools" >&2; \\
             return 1 2>/dev/null || exit 1; }
fi
if [ "${1:-}" = github ]; then _run env.emit --github >/dev/null; fi
"""


def entry_script(root: Path) -> str:
    """The emitted ``setup.sh`` body for *root*, header not included."""
    from livery.footman import _paths  # pyright: ignore[reportPrivateUsage]

    pin = locked_uv_version(root)
    skipped = " ".join(f"--no-install-package {name}" for name in native_members(root))
    return (
        _SCRIPT.replace("__PROG__", footman.prog())
        .replace("__INSTALLER__", installer_url(pin))
        .replace("__DATA_DIR_VAR__", _paths.env_var("DATA_DIR"))
        .replace("__NATIVE_SKIP__", skipped)
    )


def native_members(root: Path) -> tuple[str, ...]:
    """The distribution names of the members whose install builds an extension.

    A kind whose wheel is a platform wheel builds at install against
    the store's tools, so the entry installs it after the tools are
    in the environment; every other member installs in the first
    sync.
    """
    from livery.workshop._kinds import kind_for
    from livery.workshop._packages import discover_packages

    if not (root / "packages").is_dir():
        return ()
    return tuple(
        package.name
        for package in discover_packages(root)
        if kind_for(package.kind).wheel_identity == "platform"
    )
