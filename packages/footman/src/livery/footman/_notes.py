"""Misuse notes with levels: the registry, the policy, the record.

Everything footman has to say about how a task treats the process — a raw
`subprocess` spawn, an `os.environ` write, a `getcwd()` that answers the
wrong directory — flows through `emit` here (via `_globals._note`, the
door every interception already used). Each note has a **kind**: a family
plus, where one exists, an instance tail naming *what happened*
(`environ-write:JAVA_HOME`, `popen-inject:git`, `lane-wait:serial`) —
never the task, which every note already carries as half its dedup key
and which the config addresses as its own axis.

Each kind resolves to a **level** — `trace` / `info` / `warning` /
`error` — from the project's `[tool.footman.notes]` table, most specific
match first, else the kind's default from `KINDS` below. `trace` prints
only under `-v` (there is no `off`: invisible-unless-asked is the mute);
`info` and `warning` print once per (task, kind); `error` prints too and
fails the task **at its boundary**, listing every banned note with its
site — all issues in one run, never fix-one-see-the-next. Whatever
printed, every fired note is recorded on the task's context and rides its
row in the `--json` envelope (trace included: the machine channel ignores
print gating).

The design's reasoning and every ruled alternative:
`notes/20260831-misuse-notes-levels.md`.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from livery.footman.context import Context

LEVELS = ("trace", "info", "warning", "error")

# The registry the runtime, the config validator, and the generated docs
# table all read — one source, so a kind cannot ship undocumented or be
# refused as unknown while it fires. Columns: family, instance tail
# (`""` for a bare kind), default level, when it fires + the fix.
KINDS: tuple[tuple[str, str, str, str], ...] = (
    (
        "environ-write",
        "variable",
        "info",
        "A task sets a variable via `os.environ`. {config} scoped the write "
        "to the task (children see it, siblings don't); say it on purpose "
        "with `env=` or `ctx.env`.",
    ),
    (
        "popen-inject",
        "program",
        "warning",
        "A task spawns via raw `subprocess` and {config} filled in `cwd`/"
        "`env` from the task context. Prefer `run()` for capture and "
        "reporting, or pass `cwd=`/`env=` to make it deliberate.",
    ),
    (
        "getcwd",
        "",
        "info",
        "A task reads the process working directory while its own directory "
        "is elsewhere — a parallel run never chdirs. `cwd()` is the "
        "directory you want.",
    ),
    (
        "fork",
        "",
        "warning",
        "A task forks the process. Forking a threaded process is unsafe "
        "(the child can inherit held locks); spawn a subprocess instead.",
    ),
    (
        "mp-start",
        "",
        "warning",
        "A task starts multiprocessing workers in-process. They inherit the "
        "real environment, not the task's overlay; a tool that parallelises "
        "itself loses little in the serial lane — mark the task `serial`.",
    ),
    (
        "global-read",
        "option",
        "warning",
        "A task reads a global option without declaring it — say "
        "`@task(uses=[...])` so its help and provenance show it.",
    ),
    (
        "global-unread",
        "option",
        "info",
        "A task declares a global option in `uses=` but never read it this "
        "run. Read a declared option unconditionally and branch on the "
        "*value*, so a conditional consumer never reads as stale — or prune "
        "the declaration if it truly is.",
    ),
    (
        "lane-wait",
        "lane",
        "info",
        "A task has been waiting on a sole-ownership lane for more than a "
        "couple of seconds; the message names the holder. Visibility, not "
        "misuse — a lane wait must never be a silent hang.",
    ),
    (
        "recorded-title",
        "",
        "info",
        "`title=` on a `recorded=False` call: there is no receipt to label. "
        "`.opts()` merges along a chain, so neither author wrote the "
        "contradiction — said once so one of them can resolve it.",
    ),
    (
        "pre-record-recorded",
        "",
        "info",
        "`pre_record` on a `recorded=False` call: there is no record to "
        "review. Same merge shape as `recorded-title`.",
    ),
    (
        "hook-return",
        "plugin",
        "warning",
        "A plugin's pre hook returned a value; a pre hook's return channel "
        "is reserved (for a pre that supplies the task's result) — keep "
        "per-task state on `task.state`.",
    ),
)

_FAMILIES = {family for family, _, _, _ in KINDS}
_DEFAULTS = {family: default for family, _, default, _ in KINDS}


@dataclass
class Note:
    """One fired note — the record the terminal line, the task's boundary
    verdict, and the envelope row are all views of."""

    kind: str
    level: str
    site: str  # "file.py:41", or "" when no frame outside footman was found
    text: str


class BannedNotes(Exception):
    """A task fired notes its project classifies as errors."""


# --- policy: the [tool.footman.notes] table -----------------------------------


def validate(table: object) -> str | None:
    """The refusal a broken notes table earns, or None for a sound one.

    Keys are `[task/]kind`, either side `*`; the kind's family half must be
    a registered family (an instance tail is runtime data and cannot be
    checked). Values are levels. Anything else is refused by name — an
    ignored policy line is a wall someone thinks is up.
    """
    from livery.footman import _paths

    # This runner's table, not footman's: a branded CLI reads
    # `[tool.<its own stem>]` and nothing else, so naming footman's here
    # would send someone to configure a table their runner never opens.
    stem = _paths.config_table()
    if not isinstance(table, dict):
        return (
            f"the [tool.{stem}] notes key is a table of levels — "
            f'[tool.{stem}.notes] with entries like "popen-inject" = "error"'
        )
    for key, value in table.items():
        if not isinstance(value, str) or value not in LEVELS:
            return (
                f"[tool.{stem}.notes] {key} = {value!r}: the level is one "
                f"of {', '.join(LEVELS)}"
            )
        _, kind_pat = _split_key(str(key))
        family = kind_pat.split(":", 1)[0]
        if family != "*" and family not in _FAMILIES:
            known = ", ".join(sorted(_FAMILIES))
            return (
                f"[tool.{stem}.notes] {key}: unknown note kind "
                f"{family!r} — the kinds are {known} (see the notes docs "
                f"page), '*' for all"
            )
    return None


def _split_key(key: str) -> tuple[str, str]:
    """A config key's two axes: `(task, kind)`, either possibly `*`."""
    if key == "*":
        return "*", "*"
    if "/" in key:
        task, _, kind = key.partition("/")
        return task or "*", kind or "*"
    return "*", key


def install_policy(table: object) -> None:
    """Point this process's note resolution at one project's table.

    Anything but a dict of rules means "the defaults" — the caller has
    already refused a malformed table loudly (`validate`); this door stays
    permissive so an embedded invocation can always reset it."""
    global _policy
    rules: dict[tuple[str, str], str] = {}
    if isinstance(table, dict):
        for key, value in table.items():
            rules[_split_key(str(key))] = str(value)
    _policy = rules


def resolve(task: str, kind: str) -> str:
    """The level *kind* carries for *task*: most specific rule first, then
    the family's default. The ladder is the whole contract — a whitelist
    entry outranks the `"*"` blanket, which is what makes flipping the
    blanket to `error` safe for already-audited instances."""
    family = kind.split(":", 1)[0]
    for probe in (
        (task, kind),
        (task, family),
        (task, "*"),
        ("*", kind),
        ("*", family),
        ("*", "*"),
    ):
        if (level := _policy.get(probe)) is not None:
            return level
    return _DEFAULTS.get(family, "info")


_policy: dict[tuple[str, str], str] = {}


# --- emission -----------------------------------------------------------------

_lock = threading.Lock()
_noted: set[tuple[str, str]] = set()  # (task, kind): teach-once dedup


def reset() -> None:
    """A new run starts clean: every note may teach once more."""
    with _lock:
        _noted.clear()


def emit(kind: str, text: str, ctx: Context | None = None) -> None:
    """Fire one note: dedup, resolve its level, record it, say it.

    *ctx* is the task the note belongs to — the current context when the
    interception runs inside the task (the usual case, and the default),
    passed explicitly where the caller holds a context that may not be
    current (the executor's hook machinery).
    """
    from livery.footman.context import current, real_stderr

    if ctx is None:
        ctx = current()
    task = ctx.task or "?"
    with _lock:
        if (task, kind) in _noted:
            return
        _noted.add((task, kind))
    level = resolve(task, kind)
    note = Note(kind, level, _site(), text)
    ctx.notes.append(note)
    if level == "trace" and not ctx.verbose:
        return  # recorded (the envelope still carries it), not printed
    where = f" [{note.site}]" if note.site else ""
    real_stderr().write(f"{level}: {text}{where}\n")


def _site() -> str:
    """Where the noted call was made: the nearest frame outside footman.

    `file:line`, path relative to the cwd when it is under it — the form a
    whitelist audit reads and an agent can jump to. Empty when every frame
    is footman's own (an interception fired with no user code on the
    stack), and never an error: a note must not fail for want of a frame.
    """
    import sys

    package = os.path.dirname(os.path.abspath(__file__))
    try:
        frame: Any = sys._getframe(1)
        while frame is not None:
            filename = frame.f_code.co_filename
            if not os.path.abspath(filename).startswith(package):
                path = filename
                try:
                    rel = os.path.relpath(path)
                    if not rel.startswith(".."):
                        path = rel
                except ValueError:
                    pass  # Windows: another drive
                return f"{path}:{frame.f_lineno}"
            frame = frame.f_back
    except Exception:
        pass
    return ""


def boundary_error(ctx: Context) -> BannedNotes | None:
    """The failure a task's banned notes add up to, or None.

    Asked at the task boundary, after the body ran to completion: every
    interception already did the safe thing at its site, so nothing is
    gained by stopping early — and everything is gained by not stopping,
    which is how all of a task's issues surface in one run. Same bargain
    `keep-going` embodies: see everything, then fail honestly.
    """
    banned = [n for n in ctx.notes if n.level == "error"]
    if not banned:
        return None
    listed = "; ".join(
        f"{n.kind}" + (f" at {n.site}" if n.site else "") for n in banned
    )
    count = len(banned)
    plural = "note" if count == 1 else "notes"
    from livery.footman import _paths

    return BannedNotes(
        f"{count} banned {plural}: {listed} — fix the site, or classify a "
        f"known-harmless instance in [tool.{_paths.config_table()}.notes]"
    )


def program_name(args: Any) -> str:
    """The instance tail of a raw spawn: the program's basename, best-effort.

    Never raises — a note must not fail for want of a name."""
    try:
        first: Any
        if isinstance(args, (list, tuple)):
            if not args:
                return "?"
            first = args[0]
            if isinstance(first, bytes):
                first = os.fsdecode(first)
            elif not isinstance(first, str):
                first = os.fsdecode(os.fspath(first))
        elif isinstance(args, (str, bytes)):
            text = os.fsdecode(args) if isinstance(args, bytes) else args
            parts = text.split()
            first = parts[0] if parts else text
        else:
            first = os.fsdecode(os.fspath(args))
        return os.path.basename(first.strip()) or "?"
    except Exception:
        return "?"
