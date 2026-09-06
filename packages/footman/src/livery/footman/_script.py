"""PEP 723 inline script metadata — detection, and the uv command lines.

A tasks file that carries a `# /// script` block declares its own
dependencies, and footman runs it inside the script's uv-managed
environment (the script handoff in `_app._uv_handoff`, and the
self-re-exec in the completion children). This module is the one place
that reads the block and spells the uv commands, so the run path and
both children can never drift apart.

Stdlib only: the completion children import it before anything heavy,
and the block is comments — reading it never imports the file.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

from livery.footman import _paths

# The reference regex from PEP 723, verbatim: a comment block fenced by
# `# /// script` and `# ///`, every line a comment.
_BLOCK = re.compile(
    r"(?m)^# /// (?P<type>[a-zA-Z0-9-]+)$\s(?P<content>(^#(| .*)$\s)+)^# ///$"
)


def read_block(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """The file's `script` metadata: `(metadata, warning)`.

    `(None, None)` when the file has no block (or cannot be read — a
    missing file is simply not a script). `(dict, None)` for a sound
    block. `(None, message)` when the file *declares* a block that cannot
    be honoured — malformed TOML, or two `script` blocks — because a
    declared intent must never be dropped in silence: the run path prints
    the warning and proceeds as if the block were absent.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None, None
    blocks = [m for m in _BLOCK.finditer(text) if m.group("type") == "script"]
    if not blocks:
        return None, None
    if len(blocks) > 1:
        return None, (
            f"{path.name} carries {len(blocks)} script blocks — PEP 723 "
            f"allows one; running without them"
        )
    content = "".join(
        line[2:] if line.startswith("# ") else line[1:]
        for line in blocks[0].group("content").splitlines(keepends=True)
    )
    try:
        return tomllib.loads(content), None
    except tomllib.TOMLDecodeError as exc:
        return None, (
            f"{path.name} carries a script block footman can't read "
            f"({exc}) — running without it"
        )


def _canonical(name: str) -> str:
    """PEP 503 name normalization, after cutting the requirement's name off
    its specifier/extras/marker tail."""
    head = name.strip()
    for i, ch in enumerate(head):
        if ch in " [<>=!~;@(":
            head = head[:i]
            break
    return re.sub(r"[-_.]+", "-", head).lower()


def declares(meta: dict[str, Any], dist: str) -> bool:
    """Whether the block's dependencies name the distribution *dist*.

    The portability rule rides on this: a tasks file must declare the
    runner it imports, or the script environment cannot contain it.
    """
    deps = meta.get("dependencies")
    if not isinstance(deps, list):
        return False
    want = _canonical(dist)
    return any(isinstance(d, str) and _canonical(d) == want for d in deps)


def sync_argv(
    uv: str, file: Path, *, quiet: bool = True, offline: bool = False
) -> list[str]:
    """`uv sync --script` — materialize (or no-op check) the script env.

    `offline` is the completion children's mode: a keystroke never touches
    the network, so an unmaterialized env fails fast and the child falls
    back to running in place.
    """
    cmd = [uv, "sync", "--script", str(file)]
    if quiet:
        cmd.append("--quiet")
    if offline:
        cmd.append("--offline")
    return cmd


def find_argv(uv: str, file: Path) -> list[str]:
    """`uv python find --script` — the script env's interpreter.

    Only meaningful after a sync: on an unmaterialized env uv answers with
    a *base* interpreter (exit 0), which is exactly not the script env —
    so every caller syncs first and treats this as a lookup, not a probe.
    """
    return [uv, "python", "find", "--script", str(file)]


def find_uv() -> str | None:
    """The uv to hand off to: this runner's own environment first, then PATH.

    Installing `footman[uv]` puts a uv binary in the same scripts directory
    as `fm` itself, so a globally-installed runner carries its own — no
    PATH hunt, and nothing to install separately.
    """
    import shutil
    import sysconfig

    scripts = sysconfig.get_path("scripts")
    if scripts:
        exe = Path(scripts) / ("uv.exe" if os.name == "nt" else "uv")
        if exe.is_file():
            return str(exe)
    return shutil.which("uv")


def child_python(file: Path) -> str | None:
    """The interpreter of *file*'s script environment — if uv can produce
    one without the network.

    The completion children's half of the script rule. A keystroke that
    downloaded the world would be a broken keystroke, so the sync runs
    `--offline`: an environment already built answers instantly, one whose
    wheels are all in uv's cache is built there and then (no network, so
    it is still honest), and anything else simply means "not yet" — the
    first real run materialises it, and the TAB after that is accurate.

    `None` means "carry on in this process", which is always safe: the
    child either completes from what it can import, or (as it always has)
    quietly builds nothing.
    """
    if os.environ.get(_paths.env_var("UV_REEXEC")) or os.environ.get(
        _paths.env_var("NO_UV")
    ):
        return None
    meta, _warning = read_block(file)
    if meta is None or not meta.get("dependencies"):
        return None
    uv = find_uv()
    if uv is None:
        return None
    try:
        synced = subprocess.run(
            sync_argv(uv, file, offline=True), capture_output=True, timeout=10
        )
        if synced.returncode != 0:
            return None  # not materialised, and we won't reach for the network
        found = subprocess.run(
            find_argv(uv, file), capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    python = found.stdout.strip()
    if found.returncode != 0 or not python or Path(python) == Path(sys.executable):
        return None  # already here: re-execing would only loop
    return python


def reexec_child(python: str, args: list[str]) -> None:
    """Replace this completion child with the same work, run under *python*.

    Carries the loop belt, so the re-executed child never re-enters this
    door. Failure to exec is not an error worth making: the caller's own
    guard swallows it and the child carries on in place.
    """
    os.environ[_paths.env_var("UV_REEXEC")] = "1"
    os.environ.pop("VIRTUAL_ENV", None)  # the script env is not the active one
    os.execv(python, [python, *args])


def maybe_reexec(files: list[Path], argv: list[str]) -> None:
    """Continue in a script file's own environment, when one already exists —
    the rule both completion children (`_suggest`, `_refresh`) share, kept
    here so it cannot drift: only a single file has an environment to be
    right about, `child_python` never touches the network (a keystroke must
    not), and with nothing to re-exec into the caller carries on in place.
    """
    if len(files) != 1:
        return  # a cascade has no single environment to be right about
    python = child_python(files[0])
    if python is not None:
        reexec_child(python, argv)


# --- the lock rule's primitives, shared by the run path and the children ------
# `_app._uv_handoff` decides where a real run belongs; the completion children
# must reach the same verdict about the same directory, or TAB builds its
# manifest in an environment the run would never use (a globally-installed
# runner's own env, say) and completion silently answers from the wrong world.


def locked_project(probe: Path) -> Path | None:
    """The nearest ancestor holding a `uv.lock` — one existence walk, no read."""
    return next((p for p in (probe, *probe.parents) if (p / "uv.lock").is_file()), None)


def pins_dist(root: Path, dist: str) -> bool:
    """Whether *root*'s lockfile pins *dist* — the question that decides
    whether an invocation belongs to that project's environment.

    Reading the lock is the expensive half (a real project's `uv.lock` is
    megabytes of TOML: ~21 ms measured here), so callers ask this only once
    the cheap answers are exhausted.
    """
    try:
        with open(root / "uv.lock", "rb") as fh:
            lock = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return False
    return any(p.get("name") == dist for p in lock.get("package", []))


def inside(venv: Path) -> bool:
    """Whether this interpreter is already running out of *venv*."""
    with contextlib.suppress(OSError):
        return venv.is_dir() and Path(sys.prefix).resolve().is_relative_to(
            venv.resolve()
        )
    return False


def import_caused(exc: BaseException) -> bool:
    """Whether *exc* is, or was caused by, a failed import — the shape a
    stale environment produces, and the only one a sync can mend."""
    seen = 0
    cause: BaseException | None = exc
    while cause is not None and seen < 10:
        if isinstance(cause, ImportError):
            return True
        cause = cause.__cause__
        seen += 1
    return False


def project_home(cwd: Path) -> Path | None:
    """The pinned project that owns *cwd*, or None — the children's half of
    the lock rule's question. When this answers, the script rule stays out
    of the way, exactly as it does on the run path."""
    root = locked_project(cwd)
    if root is None or not pins_dist(root, _paths.dist()):
        return None
    return root


def venv_python(root: Path) -> Path:
    """Where *root*'s project environment keeps its interpreter."""
    if os.name == "nt":
        return root / ".venv" / "Scripts" / "python.exe"
    return root / ".venv" / "bin" / "python"


def heal_project(uv: str, root: Path) -> bool:
    """Bring *root*'s environment up to its lockfile — without the network.

    A completion child runs on a keystroke's behalf, so this is strictly
    offline: an environment already current costs one ~15 ms check, one
    whose missing wheels are all in uv's cache is mended there and then,
    and anything else stays as it is — the next real run's own retry syncs
    for real. Returns True when a sync actually ran and succeeded.
    """
    try:
        fresh = subprocess.run(
            [uv, "sync", "--check", "--quiet", "--offline", "--project", str(root)],
            capture_output=True,
            timeout=10,
        )
        if fresh.returncode == 0:
            return False
        synced = subprocess.run(
            [uv, "sync", "--quiet", "--offline", "--project", str(root)],
            capture_output=True,
            timeout=30,
        )
        return synced.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def project_reexec(root: Path, argv: list[str], *, heal: bool) -> None:
    """Continue this completion child inside *root*'s own environment.

    The children's spelling of `_app._uv_handoff`'s lock rule: whichever
    runner answered the TAB, the manifest must be built by the project's
    interpreter, from the project's packages — or completion describes a
    world the run refuses. A foreign interpreter re-execs into the
    project's venv; an interpreter already home stays. *heal* additionally
    mends a stale venv offline on the way (`heal_project`) — the detached
    refresh can afford it and asks for it unless the project opted out of
    uv; the keystroke-facing suggest child never does. The re-exec itself
    runs no uv at all, so the opt-out doesn't bind it.
    """
    if os.environ.get(_paths.env_var("UV_REEXEC")) or os.environ.get(
        _paths.env_var("NO_UV")
    ):
        return
    home = inside(root / ".venv")
    if heal:
        uv = find_uv()
        if uv is not None and heal_project(uv, root) and home:
            # Packages landed in site-packages after this interpreter first
            # imported from it; the finders' directory caches predate them.
            import importlib

            importlib.invalidate_caches()
    if home:
        return
    python = venv_python(root)
    if python.is_file():
        reexec_child(str(python), argv)
