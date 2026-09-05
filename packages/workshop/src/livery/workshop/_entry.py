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
``fm template.apply``, judged by the drift gate. POSIX only; a
Windows CI leg runs it under the runner's bash, and a pwsh spelling
is deferred to the tool-store port.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import footman


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
# the lock -> the environment emitted. Source it to enter this shell;
# `setup.sh github` persists the emission (GITHUB_ENV/GITHUB_PATH)
# for the CI steps after it, which then call __PROG__ bare.
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
# An ARRAY, not a string: CI may invoke this with zsh, which does not
# word-split unquoted expansions, so a two-word string would arrive
# as one argument. Arrays expand the same under bash and zsh.
_sync_args=()
[ -f "$_root/uv.lock" ] && _sync_args+=(--locked)
uv sync --project "$_root" "${_sync_args[@]}" >&2 \\
    || { sleep 10; uv sync --project "$_root" "${_sync_args[@]}" >&2; } \\
    || { echo "setup: uv sync failed" >&2; return 1 2>/dev/null || exit 1; }
# The sync receipt: the lock as this venv last saw it. The runner's
# per-command reconcile compares the two and re-syncs on drift.
[ -f "$_root/uv.lock" ] && cp "$_root/uv.lock" "$_root/.venv/.workshop-sync-receipt"
_run() { uv run --project "$_root" --no-sync __PROG__ "$@"; }
if [ "${1:-}" = github ]; then _run env.emit --github >/dev/null
elif (return 0 2>/dev/null); then eval "$(_run env.emit posix)"; fi
"""


def entry_script(root: Path) -> str:
    """The emitted ``setup.sh`` body for *root*, header not included."""
    pin = locked_uv_version(root)
    return _SCRIPT.replace("__PROG__", footman.prog()).replace(
        "__INSTALLER__", installer_url(pin)
    )
