"""The workspace entry contract: the emitted ``setup.sh`` at the root.

One choke point owns machine readiness. The script ensures uv at the
lock's pinned version, syncs the venv against the lock, records the
sync receipt, and emits the environment: sourced by a shell it enters
that shell, and ``setup.sh github`` persists the emission into
``GITHUB_ENV``/``GITHUB_PATH`` so every later CI step calls the
runner bare. A GitLab job sources ``setup.sh gitlab``, and its shell
keeps the emission for the job's later lines. On a CI job of either
kind, conan's home is the job's own. Between entries the per-command reconcile
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
# then call __PROG__ bare, and a GitLab job sources `setup.sh gitlab`,
# whose shell runs the job's later lines.
_root="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
# On a CI job conan's home is always the job's own: conan's cache takes
# one process at a time, and a runner may run several jobs at once, so
# a home the runner sets for every job is not used. A GitHub job keeps
# it under its temp, RUNNER_TEMP. A GitLab job keeps it in its checkout,
# CI_PROJECT_DIR, a path no two jobs running at once share, under
# .workshop/, which git ignores. The native members build through conan
# in `__PROG__ sync --locked` below, so the home is placed before it, and
# a job whose runner names no such directory stops here. Called with
# the variable's name, its value, and the home under it.
_job_conan_home() {
    if [ -z "$2" ]; then
        echo "setup: this job's runner sets no $1; a job keeps conan's home" \\
            "in a directory of its own, so run it on a runner that sets one" >&2
        return 1
    fi
    if [ ! -d "$2" ]; then
        echo "setup: $1 names $2, which is not a directory; a job keeps" \\
            "conan's home in it, so its runner must create it first" >&2
        return 1
    fi
    if [ -n "${CONAN_HOME:-}" ] && [ "$CONAN_HOME" != "$3" ]; then
        echo "setup: conan's home is $3, this job's own; the runner's" \\
            "CONAN_HOME, $CONAN_HOME, is not used, since conan's cache takes" \\
            "one process at a time" >&2
    fi
    CONAN_HOME="$3"
    export CONAN_HOME
}
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
if [ "${1:-}" = github ]; then
    _job_conan_home RUNNER_TEMP "${RUNNER_TEMP:-}" "${RUNNER_TEMP:-}/conan" \\
        || { return 1 2>/dev/null || exit 1; }
    UV_CACHE_DIR="${UV_CACHE_DIR:-$RUNNER_TEMP/uv-cache}"
    __DATA_DIR_VAR__="${__DATA_DIR_VAR__:-$RUNNER_TEMP/footman}"
    export UV_CACHE_DIR __DATA_DIR_VAR__
elif [ "${1:-}" = gitlab ]; then
    _job_conan_home CI_PROJECT_DIR "${CI_PROJECT_DIR:-}" \\
        "${CI_PROJECT_DIR:-}/.workshop/conan" \\
        || { return 1 2>/dev/null || exit 1; }
fi
# uv at the lock's pin, the version the venv carries: every sync in
# this job, this script's and __PROG__'s own, runs the same uv and
# fills one cache. A uv on PATH at the pin serves; any other version
# is left where it is, and the pinned one is installed once, under
# the runner's data directory, and put ahead of it. Without a lock
# there is no pin, and any uv serves.
_pin="__UV_PIN__"
_uv_at_pin() {
    command -v uv >/dev/null 2>&1 || return 1
    [ -z "$_pin" ] || [ "$(uv --version 2>/dev/null | cut -d' ' -f2)" = "$_pin" ]
}
if ! _uv_at_pin; then
    _uv_dir="${__DATA_DIR_VAR__:-$HOME/.local/share/footman}/uv/${_pin:-any}"
    if [ ! -x "$_uv_dir/uv" ]; then
        curl -LsSf __INSTALLER__ | env UV_UNMANAGED_INSTALL="$_uv_dir" sh >&2
    fi
    PATH="$_uv_dir:$PATH"
    export PATH
    # Name the install that failed here. Left to fall through, uv's
    # absence surfaces below as "uv sync failed", which sends the
    # reader after the venv instead of after the network.
    if ! _uv_at_pin; then
        echo "setup: could not install uv $_pin: check network access to astral.sh" >&2
        return 1 2>/dev/null || exit 1
    fi
fi
# An ARRAY, not a string: CI may invoke this with zsh, which does not
# word-split unquoted expansions, so a two-word string would arrive
# as one argument. Arrays expand the same under bash and zsh.
_sync_args=()
[ -f "$_root/uv.lock" ] && _sync_args+=(--locked)
# A native member builds its extension at install against the tools
# the store supplies below (the compiler's helpers, the conan
# provider), so this sync leaves it out; the workshop's sync below
# enters the environment itself and its own uv sync builds it.
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
# not fatal: the gate says which step that cost. The emission below
# puts the receipts' paths on PATH, so a tool the venv does not carry
# (an archive from its own release) resolves for the gate.
_run sync --locked >&2 \\
    || { echo "setup: __PROG__ sync --locked refused; its message names why" >&2; \\
         return 1 2>/dev/null || exit 1; }
# Entered here, whether sourced or run, in one call: a sourcing shell
# keeps it, and under `github` the same call persists the emission
# for the steps after. Nothing installs after this line: the sync
# above left the venv complete.
if [ "${1:-}" = github ]; then
    eval "$(_run env.emit posix --github)"
else
    eval "$(_run env.emit posix)"
fi
"""


def entry_script(root: Path) -> str:
    """The emitted ``setup.sh`` body for *root*, header not included."""
    from livery.footman import directory_variable

    pin = locked_uv_version(root)
    skipped = " ".join(f"--no-install-package {name}" for name in native_members(root))
    return (
        _SCRIPT.replace("__PROG__", footman.prog())
        .replace("__INSTALLER__", installer_url(pin))
        .replace("__DATA_DIR_VAR__", directory_variable("DATA_DIR"))
        .replace("__UV_PIN__", pin)
        .replace("__NATIVE_SKIP__", skipped)
    )


def native_members(root: Path) -> tuple[str, ...]:
    """The distribution names of the job-built members uv installs, in path order.

    A member a job of the release point builds before the wave, the
    workspace's ``job-built-members`` answer, carries native code its
    install builds against the store's tools, so the entry installs it
    after the tools are in the environment; every other member installs
    in the first sync. A job-built member the uv workspace does not hold,
    a conan package, is no uv install and is left out.
    """
    from livery.workshop._identity import roster
    from livery.workshop._queries import JOB_BUILT_MEMBERS, answer

    if not (root / "packages").is_dir():
        return ()
    built = answer(root, JOB_BUILT_MEMBERS)
    # The roster's dev entry marks a member of the uv workspace.
    return tuple(
        entry["name"]
        for entry in roster(root)
        if "dev" in entry and f"packages/{entry['dir']}" in built
    )
