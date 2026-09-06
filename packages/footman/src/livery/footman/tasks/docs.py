"""Render the invoking project's task tree as markdown — `fm docs …`.

`page` prints (or writes) one document; `site` writes linked pages with an
`index.md` per group. Both rebuild the project's tree exactly the way `fm`
itself does — the cascade, the config, the mounted plugins — so the output
can't drift from what `fm --list` shows.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal, NamedTuple

from livery.footman import (
    _config,
    _describe,
    _discover,
    _manifest,
    _paths,
    _shellcomp,
    context,
    markdown,
    registry,
)
from livery.footman.params import between, default, doc
from livery.footman.registry import Group, requires, requires_dep


def invoking_prog() -> str:
    """The name the CLI was invoked as, so a branded runner documents itself."""
    return context.current().prog


_invoking_cli = default(invoking_prog)
"""The default for every `prog`/`cmd` parameter here. Resolved when the task
runs — it is a property of the invocation, which is exactly what an import-time
default cannot see, and what `--help` now prints instead of prose."""


tasks: Group = Group("docs", help="Generate markdown docs for this project's tasks")


def _project_tree(include_self: bool) -> dict[str, Any]:
    """The invoking project's manifest tree, rebuilt the way `fm` builds it.

    Plugin tasks run from the invocation directory (the composing contract),
    so `Path.cwd()` is the right anchor for the cascade walk. Re-importing
    the tasks files inside a running task is the same same-process repeat
    `Runner` performs — `discover` isolates each file per import.
    """
    cwd = Path.cwd()
    ceiling = _paths.find_repo_root(cwd)
    cfg = _config.load_config(
        cwd, ceiling, None, on_warning=lambda m: print(m, file=sys.stderr)
    )
    name = cfg.get("tasks")
    filename = name if isinstance(name, str) else _paths.DEFAULT_TASKS_FILE
    files = _paths.task_files(cwd, ceiling, filename)
    base = registry.Group("root")
    reg = _discover.load_tree(files, base=base)
    if not include_self:
        _prune_first_party(reg)
    # The one surface that bakes: a page renders every parameter's choices and
    # has no reader to resolve them later, so the completers run here.
    tree: dict[str, Any] = _manifest.build_manifest(reg, bake_completers=True)["tree"]
    if _config.sort_listing(cfg):  # the pages follow the same one setting
        tree = _describe.sort_tree(tree)
    return tree


def _prune_first_party(node: registry.Group) -> None:
    """Don't document the documenter (opted back in with --all): drop every
    *task* mounted from footman's own plugins, wherever the author mounted
    it — the per-fn provenance stamp is the identity, never a mount name.
    Task-level on purpose: a mounted group may carry the author's own tasks
    grafted onto it (`docs = plugin("footman.docs")` plus `@docs.task`),
    and those are theirs to document. A group emptied by the prune was the
    bare mount point and goes too."""
    for name, sub in list(node.groups.items()):
        _prune_first_party(sub)
        if not sub.tasks and not sub.groups and sub.default_task is None:
            del node.groups[name]
    for name, fn in list(node.tasks.items()):
        if str(getattr(fn, registry._MOUNTED, "")).startswith("footman."):
            del node.tasks[name]


def _path_of(target: str) -> tuple[str, ...]:
    return tuple(target.replace(".", " ").split())


@tasks.task
def page(
    target: Annotated[str, doc("dotted task/group to scope to; empty = all")] = "",
    heading: Annotated[int, between(1, 6), doc("top heading level")] = 1,
    flavor: Annotated[
        Literal["plain", "material"],
        doc("plain CommonMark, or material/zensical extras"),
    ] = "plain",
    out: Annotated[
        Path | None, doc("file to write the page into; omitted = stdout")
    ] = None,
    prog: Annotated[str, _invoking_cli, doc("command name in usage and examples")] = "",
    all: Annotated[bool, doc("include footman's own mounted tasks")] = False,
) -> list[str] | None:
    """Render the task tree (or one group/task) as one markdown page.

    Without --out the page is the task's stdout, ready to redirect or pipe
    (into pandoc, say); with --out it is written to the file. The heading
    level makes the page nest under a host site's own structure, so it
    drops into zensical/mkdocs via a snippet include.
    """
    tree = _project_tree(all)
    try:
        text = markdown.render_page(
            tree, path=_path_of(target), heading=heading, flavor=flavor, prog=prog
        )
    except ValueError as exc:
        # An unknown --target: the resolver's message already teaches (the
        # menu of what it does know), so it only needs delivering as the
        # deliberate refusal it is, not as a raw ValueError with a traceback.
        context.fail(f"--target: {exc}")
    if out is None:
        print(text, end="")
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    # Inside a task, stderr merges into task output by contract — a plain
    # print is the honest note here; `returned` carries the machine copy.
    print(f"wrote {out}")
    return [str(out)]


@tasks.task
def site(
    out: Annotated[Path, doc("directory to write the pages into")],
    target: Annotated[str, doc("dotted group to scope to; empty = all")] = "",
    flavor: Annotated[
        Literal["plain", "material"],
        doc("material fits zensical/mkdocs; plain is portable"),
    ] = "material",
    prog: Annotated[str, _invoking_cli, doc("command name in usage and examples")] = "",
    all: Annotated[bool, doc("include footman's own mounted tasks")] = False,
) -> list[str]:
    """Render the task tree as linked pages: index.md per group, one file per task.

    Made for docs sites — point <out> into your docs tree and add the pages
    to the nav. Regenerate on each docs build so they can't drift.
    """
    tree = _project_tree(all)
    try:
        files = markdown.render_site(
            tree, path=_path_of(target), flavor=flavor, prog=prog
        )
    except ValueError as exc:
        # Same delivery as `page` above: teach, don't traceback.
        context.fail(f"--target: {exc}")
    written: list[str] = []
    for rel, content in files.items():
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        written.append(str(dest))
    print(f"wrote {len(written)} pages under {out}")
    return written


_CLEAR = "\x1b[K"


def reduce_frames(raw: str) -> str:
    """Collapse a pty capture to the final frame of every line.

    footman's live output repaints a line in place — `\\r` then a full
    rewrite (step lines, the status bar) — and clears with `ESC[K`; a pty
    records every intermediate frame. Keeping only the text after the last
    `\\r` of each physical line, and dropping the clear sequences, leaves
    what a human saw once the run settled. Colour (SGR) sequences pass
    through untouched.
    """
    text = raw.replace("\r\n", "\n")  # the pty's ONLCR translation, undone
    lines = [seg.rsplit("\r", 1)[-1].replace(_CLEAR, "") for seg in text.split("\n")]
    return "\n".join(lines)


@tasks.task(name="shots")
@requires_dep("rich")
@requires(lambda: sys.platform != "win32", reason="needs a POSIX pseudo-terminal")
def shots(
    *argv: str,
    out: Annotated[Path, doc("the SVG file to write")],
    width: Annotated[int, between(40, 200), doc("terminal columns")] = 72,
    cmd: Annotated[str, _invoking_cli, doc("executable to run")] = "",
    # After `cmd`, deliberately: a default reads only what is to its left, and
    # this one is the command line it is about to screenshot.
    title: Annotated[
        str,
        default(lambda p: " ".join([p["cmd"], *p["argv"]])),
        doc("window title"),
    ] = "",
) -> list[str]:
    """Run the CLI on a pseudo-terminal and save a framed SVG screenshot.

    Runs `<cmd> <argv…>` on a real pty — colours, receipts, taught errors,
    exactly as a terminal shows them — collapses live rewrites to their
    final frame, and renders the capture with rich as an SVG in a
    macOS-style window. Regenerate on every docs build and a screenshot
    can never drift from the CLI: it *is* the CLI.

    The command really executes, so don't screenshot tasks whose side
    effects you don't want. A failing command still renders — a taught
    error message is a perfectly good screenshot.
    """
    if sys.platform == "win32":  # the @requires gate already refused; belt
        raise RuntimeError("docs shots needs a POSIX pseudo-terminal")
    import fcntl
    import pty
    import struct
    import termios

    prog = cmd
    exe = shutil.which(prog)
    if exe is None:
        raise RuntimeError(f"{prog!r} is not on PATH")

    env = os.environ.copy()
    env.pop("NO_COLOR", None)  # the pty asks for colour; let it answer
    env["TERM"] = "xterm-256color"
    env["COLUMNS"] = str(width)
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, width, 0, 0))
    proc = subprocess.Popen(
        [exe, *argv], stdin=slave, stdout=slave, stderr=slave, env=env
    )
    os.close(slave)
    chunks: list[bytes] = []
    while True:
        try:
            data = os.read(master, 65536)
        except OSError:  # EIO: the child hung up (how Linux spells EOF)
            break
        if not data:
            break
        chunks.append(data)
    os.close(master)
    proc.wait()

    # The blessed lazy import: rich is docs tooling, never a dependency —
    # @requires_dep("rich") lists this task as unavailable when it's absent.
    from rich.console import Console
    from rich.text import Text

    capture = reduce_frames(b"".join(chunks).decode("utf-8", "replace"))
    console = Console(record=True, width=width, file=io.StringIO(), force_terminal=True)
    console.print(Text.from_ansi(capture.rstrip("\n")))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(console.export_svg(title=title), encoding="utf-8")
    print(f"wrote {out}")
    return [str(out)]


# --- animated casts -----------------------------------------------------------
# An interactive session (TAB completion!) can't be a static screenshot or a
# line-based reduction: shells paint menus with real cursor movement. A cast
# drives a live shell on the pty with scripted keystrokes, replays the byte
# stream through a terminal emulator (pyte) into screen states, renders each
# state with rich, and stacks the frames in one self-contained SVG animated
# by CSS keyframes with the capture's own timing. No JavaScript; an <img>
# plays it.

_KEY_TOKENS = {
    "<TAB>": b"\t",
    "<ENTER>": b"\r",
    "<SPACE>": b" ",
    "<BACKSPACE>": b"\x7f",
    "<CTRL-C>": b"\x03",
    # PSReadLine's menu swallows Ctrl-C while it is open — the line survives
    # and the next keystrokes land *inside* it (`fm build -- fm deploy.` ran
    # together in one recording). Escape dismisses the menu first, so the
    # cancel that follows reaches the line.
    "<ESC>": b"\x1b",
    # Arrows, so a cast can *walk* a completion menu rather than only open
    # one. PowerShell's MenuComplete grid, nushell's menu and fish's pager
    # are all navigable by default, and a still frame of a menu says much
    # less than a selection moving through it. The CSI sequences a terminal
    # sends, which is what the pty is being handed.
    "<UP>": b"\x1b[A",
    "<DOWN>": b"\x1b[B",
    "<RIGHT>": b"\x1b[C",
    "<LEFT>": b"\x1b[D",
}


# A negative "delay" marks a <SETTLE> step: instead of waiting a fixed time, the
# pty loop holds the next key until output has gone quiet (see _pty_session) — so
# a prompt whose render time you can't predict is waited on, not guessed at.
_SETTLE = -1.0
_SETTLE_GAP = 0.5  # seconds of silence that count as "settled"
# The same idea between events: after a key, the capture waits for the shell
# to stop redrawing and takes the frame there. Shorter than _SETTLE_GAP —
# this runs after every single key, and a shell answering a keystroke is far
# quicker than one rendering a prompt from cold.
_SNAP_GAP = 0.18
# How long an ordinary key gets to draw nothing at all before its frame is
# taken anyway: a character either echoes promptly or never will. A Tab is
# given _SETTLE_MAX instead — it is the key that sends a shell away to
# compute, and the first Tab of a pwsh session pays for .NET's JIT on top,
# which on a loaded runner outlasted any bound worth applying to a keystroke.
# Once output starts, _SNAP_GAP decides for both.
_SNAP_IDLE = 1.5
_SETTLE_MAX = 10.0  # hard cap: fire anyway, so a never-quiet stream can't hang


# How a key is captioned in the recording's corner. A reader watching a
# completion menu appear needs to know whether a human pressed Tab or simply
# typed — the terminal itself shows neither. Arrows get their glyph; the rest
# get the name printed on the key.
_KEY_CAPTIONS = {
    "<TAB>": "Tab",
    "<ENTER>": "Enter",
    "<SPACE>": "Space",
    "<BACKSPACE>": "Backspace",
    "<ESC>": "Esc",
    "<CTRL-C>": "Ctrl-C",
    "<UP>": "↑",
    "<DOWN>": "↓",
    "<LEFT>": "←",
    "<RIGHT>": "→",
}


class Send(NamedTuple):
    """One write into the pty, and what the recording should make of it.

    *snap* marks the end of a script step: the pty loop waits there for the
    shell to finish responding and the recording takes one frame. Everything
    between snaps — the characters of a typed word — goes in without being
    filmed, so a recording is a sequence of *events* rather than a sampling
    of a redraw.
    """

    delay: float
    data: bytes
    caption: str
    snap: bool


def keystrokes(script: tuple[str, ...]) -> list[Send]:
    """Compile a cast script into (delay-before-send, bytes, caption) steps.

    Each script argument is either literal text — typed one character at a
    time at a human-ish cadence — or a token: `<TAB>`, `<ENTER>`, `<SPACE>`,
    `<BACKSPACE>`, `<CTRL-C>`, the arrows `<UP>`/`<DOWN>`/`<LEFT>`/`<RIGHT>`
    (for walking a shell's completion menu), `<WAIT>` (pause 0.8 s),
    `<WAIT:ms>`, or `<SETTLE>` (wait until output stops changing —
    timing-independent, for a prompt whose render time you can't predict).

    The caption is what the recording shows in its corner for that step:
    a key's name, the character typed, or empty for a pause — a wait carries
    no caption of its own, so the key that opened a menu stays legible for
    as long as the menu is on screen.
    """
    sends: list[Send] = []
    for part in script:
        if part in _KEY_TOKENS:
            sends.append(
                Send(0.3, _KEY_TOKENS[part], _KEY_CAPTIONS.get(part, part), True)
            )
        elif part == "<SETTLE>":
            sends.append(Send(_SETTLE, b"", "", False))
        elif part == "<WAIT>":
            sends.append(Send(0.8, b"", "", False))
        elif part.startswith("<WAIT:") and part.endswith(">"):
            sends.append(Send(int(part[6:-1]) / 1000.0, b"", "", False))
        else:
            # Typed text is one event, not one per letter: the frame that
            # matters is the finished word, and a frame per character is
            # what made a recording read as a flicker. The characters still
            # go in one at a time, so the shell sees ordinary typing.
            chars = list(part)
            sends.extend(
                Send(0.02, ch.encode("utf-8"), part, i == len(chars) - 1)
                for i, ch in enumerate(chars)
            )
    return sends


# Modern interactive stacks interrogate their terminal before painting a
# prompt — fish asks for capabilities (XTGETTCAP), PSReadLine and reedline
# need cursor-position answers (DSR), kitty-keyboard and colour queries
# round it out — and block or bail when nothing replies. The session
# answers like a plain xterm; DSR answers come from a live emulator so the
# reported cursor is the truth.
_TERM_QUERIES: list[tuple[re.Pattern[bytes], bytes]] = [
    (re.compile(rb"\x1b\[0?c"), b"\x1b[?62;22c"),  # primary DA
    (re.compile(rb"\x1b\[>0?c"), b"\x1b[>1;95;0c"),  # secondary DA
    (re.compile(rb"\x1b\[\?u"), b"\x1b[?0u"),  # kitty keyboard
    (re.compile(rb"\x1b\[\?2026\$p"), b"\x1b[?2026;0$y"),  # sync output
    (re.compile(rb"\x1bP\+q[0-9a-fA-F;]*(?:\x1b\\|\x07)"), b"\x1bP0+r\x1b\\"),
]
_DSR = re.compile(rb"\x1b\[6n")
# Device Control Strings — terminal protocol, never screen content. pyte
# doesn't consume them, so their payload (fish's XTGETTCAP capability
# names, hex-encoded) would render as stray text for one frame.
_DCS = re.compile(r"\x1bP.*?(?:\x1b\\|\x07)", re.S)
# CSI sequences with a *private* marker — `ESC [ > … m`, `ESC [ ? … m`. They
# are modes, not styling: `ESC[>4;2m` is modifyOtherKeys, which fish and
# nushell turn on at startup. pyte ignores the marker and reads the
# parameters as SGR, so that one turns on underline (4) and `ESC[>1m` turns
# on bold — and every cell drawn afterwards inherits them. That is what
# underlined a recording's prompt in shells that emit no underline at all,
# and why the whole listing looked ruled. Dropped before the emulator sees
# them; pyte handles the non-`m` private sequences (`ESC[?2004h`) correctly.
_PRIVATE_SGR = re.compile(r"\x1b\[[<=>?][\d;:]*m")


class _Descs:
    """Strip DCS replies from a stream that arrives in pieces.

    The pattern has to see a whole sequence, and a pty hands back whatever
    happened to be readable — so a reply split across two reads was stripped
    from neither half, and its debris went to the terminal emulator as text.
    pyte then parsed the fragments: a capability answer landing across a read
    boundary set attributes nothing had asked for, and every cell drawn
    afterwards inherited them. That is what underlined a recording's prompt
    in shells that never emit an underline at all.

    So the tail of a possibly-incomplete sequence is held back and prepended
    to the next read, exactly as the query answerer already does.
    """

    __slots__ = ("_tail",)

    def __init__(self) -> None:
        self._tail = ""

    def feed(self, text: str) -> str:
        buf = _PRIVATE_SGR.sub("", _DCS.sub("", self._tail + text))
        start = buf.rfind("\x1bP")
        if start == -1:
            self._tail = ""
            return buf
        # A sequence that never terminates must not grow without bound: past
        # a sane length it is not a reply, it is a stuck stream.
        self._tail = buf[start:] if len(buf) - start < 4096 else ""
        return buf[:start]


_OSC_COLOUR = re.compile(rb"\x1b\](1[012]);\?(?:\x07|\x1b\\)")


# Which line editors need a cursor-position answer to paint at all.
# PSReadLine and reedline hang without one — with DSR unanswered, pwsh
# and nushell produce no output whatever. fish is the opposite: it asks
# mid-session and then *types the answer into the command line*, so an
# `fm che` becomes `fm ch77e`. bash and zsh don't care either way.
_NEEDS_CURSOR_REPLY = frozenset({"pwsh", "nushell"})


def _answer_queries(
    buf: bytes,
    new_from: int,
    cursor_at: Callable[[], tuple[int, int]],
    reply: Callable[[bytes], object],
    *,
    cursor: bool = True,
) -> None:
    """Answer terminal queries in *buf* that end at or past *new_from* —
    earlier bytes were answered on a previous read (the buffer overlaps so
    a sequence split across reads still matches exactly once)."""
    for m in _DSR.finditer(buf) if cursor else ():
        if m.end() > new_from:
            row, col = cursor_at()
            reply(f"\x1b[{row};{col}R".encode())
    for m in _OSC_COLOUR.finditer(buf):
        if m.end() > new_from:
            reply(b"\x1b]" + m.group(1) + b";rgb:2828/2c2c/3434\x07")
    for pattern, answer in _TERM_QUERIES:
        for m in pattern.finditer(buf):
            if m.end() > new_from:
                reply(answer)


def _pty_session(
    argv: list[str],
    *,
    width: int,
    height: int,
    sends: list[Send],
    settle: float,
    env_extra: dict[str, str] | None = None,
    keep_echo: bool = False,
    cwd: Path | None = None,
    answer_cursor: bool = True,
    key_log: list[tuple[float, str]] | None = None,
    steady: float = 1.0,
) -> list[tuple[float, bytes]]:
    """Run *argv* on a pty, play the keystroke script, and record
    (elapsed-seconds, bytes) chunks until output has settled."""
    if sys.platform == "win32":  # pragma: no cover — the @requires gates hold
        raise RuntimeError("terminal recording needs a POSIX pseudo-terminal")
    import fcntl
    import pty
    import select
    import struct
    import termios
    import time as _time

    import pyte  # tracks the live cursor for honest DSR answers

    env = os.environ.copy()
    env.pop("NO_COLOR", None)
    env["TERM"] = "xterm-256color"
    env["COLUMNS"] = str(width)
    env["LINES"] = str(height)
    env.update(env_extra or {})
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", height, width, 0, 0))
    # Query replies are written into the pty during boot, before the shell
    # enters raw mode — with kernel ECHO on, the tty prints them back (the
    # digits of a cursor-position reply flashing on screen for a frame).
    # Shells that interrogate the terminal render their own input anyway,
    # so echo goes off — except where the caller says otherwise: readline
    # honours the tty's echo flag and falls silent without it.
    if not keep_echo:
        attrs = termios.tcgetattr(slave)
        attrs[3] &= ~termios.ECHO
        termios.tcsetattr(slave, termios.TCSANOW, attrs)

    def _own_the_tty() -> None:  # child, pre-exec: the pty.fork() idiom
        # A new session *and* the slave as controlling terminal — fish,
        # nushell, and PSReadLine all refuse interactive mode without one
        # (zsh and bash merely grumble).
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    proc = subprocess.Popen(
        argv,
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
        cwd=cwd,
        preexec_fn=_own_the_tty,
    )
    os.close(slave)
    start = _time.monotonic()
    chunks: list[tuple[float, bytes]] = []
    queue = list(sends)
    tracker = pyte.Screen(width, height)
    tracker_stream = pyte.Stream(tracker)
    pending = b""  # tail kept so a query split across reads still matches
    # Typing begins only after the boot has *settled*: keys written before
    # the line editor exists are half-echoed raw and eaten (a TAB pressed
    # then never completes anything), and a slow rc — compinit, the hook's
    # own `--setup-completion` subprocess — paints in bursts. Every boot
    # chunk pushes the start out another half second of silence.
    typing_started = False
    next_at: float | None = None
    last_output = start  # for <SETTLE>: when the pty last emitted anything
    deadline = None if queue else start + settle
    # Set between writing a snap step and the shell going quiet after it:
    # while it holds a caption, no further keys go in and the frame for that
    # event has not been marked yet.
    awaiting: str | None = None
    awaiting_since = start
    # `steady` widens the quiet-detection windows without touching the hard
    # caps: on a loaded machine "output went quiet" is often the shell being
    # CPU-starved mid-redraw, and a key sent into that gap types into a menu
    # that has not opened yet — the interaction diverges and the beats never
    # render at all. Slower judgement, same script.
    snap_gap = _SNAP_GAP * steady
    settle_gap = _SETTLE_GAP * steady
    awaiting_bound = _SNAP_IDLE * steady
    awaiting_screen: tuple[str, ...] = ()
    try:
        while True:
            now = _time.monotonic()
            # Silence straight after a key does not mean the shell has
            # finished — it usually means it has not started. A Tab sends the
            # shell off to run `--complete` in a subprocess, and the quiet
            # while it does that is longer than the quiet after it finishes
            # painting. So a frame is taken once the *screen has changed* and
            # then gone quiet; a key that genuinely draws nothing falls
            # through on the idle bound instead.
            #
            # Screen, not bytes. Counting any output as the answer let a
            # keystroke's own echo — or a terminal query pwsh fires the
            # instant Tab is pressed — end the wait before the completion it
            # was waiting for had been computed, so the recording showed a
            # prompt with nothing completed on it.
            answered = tuple(tracker.display) != awaiting_screen
            if awaiting is not None and (
                (answered and now - last_output >= snap_gap)
                or (not answered and now - awaiting_since >= awaiting_bound)
                or now - awaiting_since >= _SETTLE_MAX
            ):
                if key_log is not None:
                    key_log.append((now - start, awaiting))
                awaiting = None
                if queue:
                    nd = queue[0].delay
                    next_at = now if nd < 0 else now + nd
                else:
                    next_at = now
                    deadline = now + settle
            if awaiting is None and queue and next_at is not None:
                if queue[0].delay < 0:
                    # A <SETTLE> step (negative delay) waits for a prompt to
                    # finish rendering: output quiet for _SETTLE_GAP *and* the
                    # cursor sitting past column 0 — a prompt on the line, not
                    # the col-0 lull of a program still starting up (Python
                    # boot, task discovery), which is silent but not ready.
                    # Capped at _SETTLE_MAX so a prompt that never lands — or
                    # one that ends at column 0 — can't hang the cast; next_at
                    # holds when the step became due, so the cap counts there.
                    ready = (
                        now - last_output >= settle_gap and tracker.cursor.x > 0
                    ) or now - next_at >= _SETTLE_MAX
                else:
                    # Before the *first* key, a prompt has to be on the line —
                    # not merely a lull. A boot's last act is often silent:
                    # pwsh's rc ends by running `--setup-completion`, a Python
                    # subprocess that prints nothing to the terminal, so
                    # "output has stopped" arrives while completion is still
                    # being registered. Typing into that gap gives a session
                    # whose keys echo and whose Tab is bound to nothing —
                    # which is what a loaded runner recorded while a warm
                    # laptop never did. Capped, so a prompt that genuinely
                    # ends at column 0 cannot hang the recording.
                    ready = now >= next_at and (
                        typing_started
                        or tracker.cursor.x > 0
                        or now - start >= _SETTLE_MAX
                    )
                if ready:
                    typing_started = True
                    step = queue.pop(0)
                    if step.data:
                        os.write(master, step.data)
                    if step.snap:
                        # One event, one frame. Nothing more is sent until
                        # the shell has finished answering this key, and the
                        # frame is taken at that settled state — so a fast
                        # machine and a loaded runner record the same frames
                        # and only the wall clock differs, which playback
                        # throws away anyway.
                        awaiting = step.caption
                        awaiting_since = now
                        awaiting_screen = tuple(tracker.display)
                        # How long this key gets to draw *nothing at all*
                        # before its frame is taken anyway. A Tab is the one
                        # key that reliably sends the shell away to compute —
                        # a subprocess, and on a cold pwsh the JIT as well —
                        # so it is given the full settle. An ordinary
                        # character either echoes at once or never will, and
                        # waiting on it only makes every recording slower.
                        awaiting_bound = (
                            _SETTLE_MAX if step.data == b"\t" else _SNAP_IDLE * steady
                        )
                        continue
                    if queue:
                        nd = queue[0].delay
                        next_at = now if nd < 0 else now + nd
                    else:
                        next_at = now
                        deadline = now + settle
            readable, _, _ = select.select([master], [], [], 0.03)
            if readable:
                try:
                    data = os.read(master, 65536)
                except OSError:  # EIO: the child hung up
                    break
                if not data:
                    break
                last_output = _time.monotonic()
                chunks.append((last_output - start, data))
                # Per chunk, deliberately: this screen exists to answer the
                # shell's cursor-position queries *live*, and the stateful
                # stripper holds back a possibly-incomplete tail — which
                # leaves the tracker a read behind and answers PSReadLine
                # with a stale cursor, after which it stops completing at
                # all. Debris from a split DCS costs this screen nothing; it
                # is never rendered. The frames get the careful stripping.
                tracker_stream.feed(_DCS.sub("", data.decode("utf-8", "replace")))
                buf = pending + data
                _answer_queries(
                    buf,
                    len(pending),
                    lambda: (tracker.cursor.y + 1, tracker.cursor.x + 1),
                    lambda answer: os.write(master, answer),
                    cursor=answer_cursor,
                )
                pending = buf[-64:]
                if not typing_started and queue:
                    nd = queue[0].delay
                    next_at = last_output if nd < 0 else last_output + 0.5 + nd
                if deadline is not None:  # let late repaints settle too
                    deadline = last_output + settle
            if deadline is not None and _time.monotonic() >= deadline:
                break
            if proc.poll() is not None and not readable:
                break
    finally:
        import contextlib as _contextlib

        with _contextlib.suppress(ProcessLookupError):
            proc.kill()
        os.close(master)
        proc.wait()
    return chunks


def _cell_style(char: Any) -> str:
    """A pyte cell's attributes as a rich style string ('' = default)."""
    bits: list[str] = []
    if char.bold:
        bits.append("bold")
    if char.italics:
        bits.append("italic")
    if char.underscore:
        bits.append("underline")

    def named(attr: str) -> str:
        # pyte names ansi colours ("red") and spells the rest as bare
        # 6-digit hex ("87d7ff") — rich wants a `#` on the hex form.
        return f"#{attr}" if _HEX6.fullmatch(attr) else _BRIGHT.sub(r"bright_\1", attr)

    fg = named(char.fg) if char.fg and char.fg != "default" else _SVG_FG
    bg = named(char.bg) if char.bg and char.bg != "default" else _SVG_BG
    if char.reverse:
        # Swapped here, with concrete colours, rather than left to rich's
        # `reverse`. On the SVG export that painted the background but not
        # the text, so a selected menu entry — which is how zsh's
        # `menu select` marks its choice — came out as a solid block with
        # its own text invisible inside it.
        fg, bg = bg, fg
    if bg != _SVG_BG and _contrast(fg, bg) < 4.5:
        # Text too close in tone to its own background is not text. Shells
        # paint a highlight by setting one half of the pair and trusting the
        # terminal's default for the other — a menu selection with an
        # explicit light background and a default (light) foreground came out
        # as a solid block with its text hidden inside it. Exact equality was
        # far too narrow a test, and so was "nearly equal": PSReadLine's
        # selected row came out at 2.34:1, which is not the same colour by any
        # measure and is still unreadable at terminal sizes — it reads as a
        # blank white bar until you select the text. The bar is WCAG AA for
        # body text; below it, take whichever of the palette's two ends the
        # background is furthest from.
        #
        # Only where a background was actually painted. Dim text on the
        # ordinary background is *meant* to be low contrast — that is what
        # fish's grey autosuggestion is — and rescuing it would render it as
        # ordinary typed characters, which is the bug the bright-colour test
        # guards against.
        fg = _SVG_BG if _contrast(_SVG_BG, bg) > _contrast(_SVG_FG, bg) else _SVG_FG
    if fg != _SVG_FG:
        bits.append(fg)
    if bg != _SVG_BG:
        bits.append(f"on {bg}")
    return " ".join(bits)


def _event_screens(
    chunks: list[tuple[float, bytes]],
    key_log: list[tuple[float, str]],
    *,
    width: int,
    height: int,
) -> list[tuple[str, list[list[tuple[str, str]]]]]:
    """One screen per event: the settled state after each key, captioned.

    The alternative — a frame per output chunk — records how a shell happened
    to redraw rather than what it did. Menus that a shell paints and clears
    within one chunk vanish; a shell that paints in bursts loses keystrokes
    into a single frame; and the same script gives a different recording on a
    loaded machine. Playing back one frame per keypress at a fixed pace
    removes all of that: the wall clock only decided *when* the shell went
    quiet, never what is on screen.
    """
    import pyte

    screen = pyte.Screen(width, height)
    stream = pyte.Stream(screen)
    descs = _Descs()
    frames: list[tuple[str, list[list[tuple[str, str]]]]] = []
    fed = 0
    for at, caption in key_log:
        while fed < len(chunks) and chunks[fed][0] <= at:
            stream.feed(descs.feed(chunks[fed][1].decode("utf-8", "replace")))
            fed += 1
        frames.append(
            (
                caption,
                [
                    [
                        (screen.buffer[y][x].data, _cell_style(screen.buffer[y][x]))
                        for x in range(width)
                    ]
                    for y in range(height)
                ],
            )
        )
    return frames


def _screens(
    chunks: list[tuple[float, bytes]], *, width: int, height: int
) -> list[tuple[float, list[list[tuple[str, str]]]]]:
    """Replay timed chunks through a terminal emulator; return the deduped
    (time, screen) states, each screen a grid of (char, style) cells."""
    import pyte

    screen = pyte.Screen(width, height)
    stream = pyte.Stream(screen)
    frames: list[tuple[float, list[list[tuple[str, str]]]]] = []
    last: list[list[tuple[str, str]]] | None = None
    descs = _Descs()
    for t, data in chunks:
        stream.feed(descs.feed(data.decode("utf-8", "replace")))
        snap = [
            [
                (cell.data, _cell_style(cell))
                for x in range(width)
                for cell in (screen.buffer[y][x],)
            ]
            for y in range(height)
        ]
        if snap != last:
            # Skip the blank screens before the shell first paints: a
            # recording should open on the prompt, not on an empty frame.
            if frames or any(cell[0].strip() for row in snap for cell in row):
                frames.append((t, snap))
            last = snap
    return frames


_HEX6 = re.compile(r"[0-9a-fA-F]{6}")
# pyte spells the bright ANSI colours "brightblack"; rich spells them
# "bright_black" and silently ignores anything it cannot parse — which
# renders dim text in the normal foreground. That is how fish's grey
# autosuggestion came to look like characters typed into the prompt.
_BRIGHT = re.compile(r"^bright([a-z]+)$")
# rich's own SVG export palette, named here so a reversed cell can be swapped
# to concrete colours: "default" means these two, and a swap has to say which
# colour it landed on rather than leave it to be resolved later.
_SVG_FG = "#c5c8c6"
_SVG_BG = "#292929"
_LUMA: dict[str, float] = {}


def _luminance(colour: str) -> float:
    """Relative luminance, 0 (black) to 1 (white).

    Resolved through **rich's own export palette**, never a table of our own.
    That theme is not the one the names suggest: its ANSI `black` is `#4b4e55`
    and its `white` is `#c5c8c6`. Weighing a private approximation instead
    said a selected menu row was black-on-white — near-perfect contrast — when
    what rich painted was dark grey on light grey at 2.34:1, which reads as a
    blank bar until you select the text.
    """
    if colour not in _LUMA:
        from rich.color import Color
        from rich.terminal_theme import SVG_EXPORT_THEME

        try:
            triplet = Color.parse(colour).get_truecolor(SVG_EXPORT_THEME)
            rgb = (triplet.red, triplet.green, triplet.blue)
        except Exception:  # an unparseable name should not fail a recording
            rgb = (128, 128, 128)
        r, g, b = (c / 255 for c in rgb)
        _LUMA[colour] = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return _LUMA[colour]


def _contrast(one: str, two: str) -> float:
    """WCAG-style contrast ratio, 1 (identical) to 21 (black on white)."""
    a, b = sorted((_luminance(one), _luminance(two)), reverse=True)
    return (a + 0.05) / (b + 0.05)


# rich centres the window title at y=27; the caption sits on that line at
# the other end of the chrome, right-aligned a little in from the edge.
_SVG_OPEN = re.compile(r"<svg[^>]*viewBox=\"0 0 ([\d.]+) [\d.]+\"[^>]*>")
_CAPTION_INSET = 30.0


def _with_caption(svg: str, caption: str, uid: str) -> str:
    """Draw *caption* in the recording's top-right corner.

    A terminal shows what a keystroke *did*, never that one happened — so a
    completion menu appearing out of a still line reads as magic rather than
    as Tab. The caption is added here, per frame, rather than by the shell:
    nothing in the session has to know it is being filmed.
    """
    if not caption:
        return svg
    match = _SVG_OPEN.search(svg)
    if match is None:  # unknown geometry: a caption in the wrong place is worse
        return svg
    x = float(match.group(1)) - _CAPTION_INSET
    text = (
        f'<text class="{uid}-title" fill="#c5c8c6" text-anchor="end" '
        f'x="{x:.1f}" y="27">{_xml_escape(caption)}</text>'
    )
    return svg.replace("</svg>", f"{text}</svg>", 1)


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


_SVG_SHELL = re.compile(r"^\s*<svg[^>]*>|</svg>\s*$")


def compose_animation(
    svgs: list[str], times: list[float], *, hold: float = 1.6, prefix: str = "cf"
) -> str:
    """Stack per-frame SVGs into one, cycled by CSS keyframes.

    Each frame plays over its captured window; the last holds for *hold*
    seconds before the loop restarts. `step-end` opacity keeps the switch
    discrete, and every frame carries its own opaque background, so the
    topmost visible frame is the whole picture.

    *prefix* names this recording's classes and keyframes, and **must be
    unique per recording on a page**. An inlined SVG's `<style>` is not
    scoped to it: put five recordings on one page under the same names and
    the last one's rules win for all of them — every cast animating to
    another's timings, and every cell painted in another's palette. That is
    why five recordings each correct on their own were wrong together.
    """
    total = times[-1] + hold
    head = svgs[0]
    match = re.search(r"<svg[^>]*>", head)
    shell_open = match.group(0) if match else "<svg>"
    # `visibility` alongside `opacity`, because a frame at opacity 0 is still
    # *there*: it hit-tests, so devtools' Inspect lands on the last frame
    # wherever you click, and its text joins any selection — so copying a
    # line out of a recording returns text from frames nobody can see. Both
    # sent two separate diagnoses down the wrong path. `visibility` is
    # animatable and steps discretely, which is exactly what is wanted here.
    css: list[str] = [".cast-frame{opacity:0;visibility:hidden}"]
    body: list[str] = []
    for i, (svg, t) in enumerate(zip(svgs, times, strict=True)):
        a = 100.0 * t / total
        b = 100.0 * (times[i + 1] / total) if i + 1 < len(times) else 100.0
        on = "opacity:1;visibility:visible"
        gone = "opacity:0;visibility:hidden"
        window = f"{a:.3f}%{{{on}}}" if a > 0 else f"0%{{{on}}}"
        off = f"{b:.3f}%{{{gone}}}" if b < 100.0 else ""
        pre = f"0%{{{gone}}}" if a > 0 else ""
        css.append(f"@keyframes {prefix}{i}{{{pre}{window}{off}}}")
        css.append(
            f".{prefix}{i}{{animation:{prefix}{i} {total:.3f}s step-end infinite}}"
        )
        inner = _SVG_SHELL.sub("", svg.strip())
        body.append(f'<g class="cast-frame {prefix}{i}">{inner}</g>')
    style = f"<style>{''.join(css)}</style>"
    # The frame boundaries, in milliseconds, stated rather than left to be
    # re-derived. The docs' player steps a reader through a recording one
    # keypress at a time, and reading these back out of the keyframes meant
    # parsing CSS whose shape varies — the final frame has no closing
    # `opacity:0` because it holds until the loop — which silently cost that
    # last frame. Whoever writes the timeline should publish it.
    stamps = ",".join(f"{t * 1000:.0f}" for t in times)
    shell_open = shell_open.replace(
        "<svg",
        f'<svg data-cast-frames="{stamps}" data-cast-total="{total * 1000:.0f}"',
        1,
    )
    return f"{shell_open}{style}{''.join(body)}</svg>"


_CAST_BOOT: dict[str, str] = {
    "zsh": "zsh",
    "bash": "bash",
    "fish": "fish",
    "pwsh": "pwsh",
    "nushell": "nu",
}


def _boot_shell(
    shell: str, prog: str, scratch: Path
) -> tuple[list[str], dict[str, str]]:
    """(argv, extra env) for an interactive *shell* with completion loaded.

    Each shell boots from a scratch config dir — the user's own dotfiles
    never run — with a minimal green prompt and footman's hook installed
    via the same `--setup-completion` path users eval. The scratch HOME
    would also hide the completion cache (TAB answers from cache alone),
    so the invoker's real cache dir is passed through FOOTMAN_CACHE_DIR —
    the override doing exactly the job it was built for.
    """
    env = {
        "HOME": str(scratch),
        "XDG_CONFIG_HOME": str(scratch),
        _paths.env_var("CACHE_DIR"): str(_paths.footman_cache_dir()),
    }
    # A system rc (/etc/zsh/*, /etc/profile) may rebuild PATH and lose the
    # venv that owns *prog* \u2014 then the rc's `eval "$(prog \u2026)"` silently
    # produces nothing and the hook never loads. Pin the interpreter's own
    # bin dir first, the same lesson the functional shell tests carry.
    bin_dir = str(Path(sys.executable).parent)
    if shell == "zsh":
        (scratch / ".zshrc").write_text(
            f"path=({bin_dir!r} $path)\n"
            "PROMPT='%F{green}\u276f%f '\n"
            "autoload -Uz compinit && compinit -u\n"
            # Menu selection, so Tab *walks* the candidates instead of only
            # listing them \u2014 the same story pwsh's MenuComplete binding
            # records. It is one documented line rather than a tuned setup,
            # and the completion page says so: a recording must not promise
            # a terminal the reader cannot have.
            "zstyle ':completion:*' menu select\n"
            # LIST_AMBIGUOUS suppresses the listing whenever a Tab managed to
            # extend the common prefix — so `-`<Tab> inserted `--` and showed
            # nothing, and the next character was typed without the reader
            # ever seeing that an option existed. Off, the candidates appear
            # with the prefix, which is the point of pressing Tab.
            "unsetopt LIST_AMBIGUOUS\n"
            # ...but without underlining half the screen while it is up.
            # zle_highlight's defaults underline the region zle considers
            # "special" during menu selection, which in a recording came out
            # as a rule under every row — the selected entry's standout is
            # the highlight worth keeping.
            "zle_highlight=(region:standout special:standout suffix:none "
            "isearch:none paste:none)\n"
            f'eval "$({prog} --setup-completion=zsh)"\n',
            encoding="utf-8",
        )
        env["ZDOTDIR"] = str(scratch)
        # --no-globalrcs: some machine images (GitHub runners among them)
        # ship an /etc/zsh rc that runs a bare compinit, which stops the
        # boot at an interactive compaudit question — and the first typed
        # key of the script gets eaten answering it. A recording wants a
        # hermetic shell: scratch rc only.
        return ["zsh", "--no-globalrcs", "-i"], env
    if shell == "bash":
        # macOS prints a "default shell is now zsh" advert into interactive
        # bash; Apple's own switch silences it — the recording is about
        # bash, not about Apple's feelings toward it.
        env["BASH_SILENCE_DEPRECATION_WARNING"] = "1"
        rc = scratch / "bashrc"
        rc.write_text(
            f'PATH="{bin_dir}:$PATH"\n'
            "PS1='\\[\\e[32m\\]\u276f\\[\\e[0m\\] '\n"
            # Vanilla bash rings the bell on the first Tab and only lists on
            # the second, so a recording had to press twice for every menu \u2014
            # which in an event-per-keypress capture spends two frames
            # saying what the other shells say in one. `show-all-if-ambiguous`
            # is readline's own one-line setting for exactly this, the same
            # class of documented tweak as zsh's `menu select` and pwsh's
            # MenuComplete binding.
            "bind 'set show-all-if-ambiguous on'\n"
            f'eval "$({prog} --setup-completion=bash)"\n',
            encoding="utf-8",
        )
        return ["bash", "--rcfile", str(rc), "-i"], env
    if shell == "fish":
        boot = (
            "set -g fish_greeting ''; "
            # Autosuggestions draw on whatever is installed beside footman:
            # the build machine offered `factor` on macOS and `f77` (the
            # Fortran compiler) on Linux, so the same script recorded
            # differently per box. A recording should show footman's
            # completion, not the host's PATH.
            "set -g fish_autosuggestion_enabled 0; "
            f"fish_add_path --prepend {bin_dir!r}; "
            f"{prog} --setup-completion=fish | source; "
            "function fish_prompt; set_color green; echo -n '\u276f '; "
            "set_color normal; end"
        )
        return ["fish", "-i", "-C", boot], env
    if shell == "pwsh":
        # MenuComplete: the grid-with-tooltip menu \u2014 the completion story
        # worth recording. The hook loads exactly as the docs teach.
        boot = "$env.PATH = $null; "  # placeholder never reached; see below
        boot = (
            f"$env:PATH = '{bin_dir}' + [IO.Path]::PathSeparator + $env:PATH; "
            'function prompt { "\u276f " }; '
            "Set-PSReadLineKeyHandler -Key Tab -Function MenuComplete; "
            f"{prog} --setup-completion=pwsh | Out-String | Invoke-Expression"
        )
        return ["pwsh", "-NoLogo", "-NoProfile", "-NoExit", "-Command", boot], env
    if shell == "nushell":
        hook = scratch / "hook.nu"
        hook.write_text(_shellcomp.script_for("nushell", prog), encoding="utf-8")
        env_nu = scratch / "env.nu"
        env_nu.write_text(
            f"$env.PATH = ($env.PATH | prepend {bin_dir!r})\n"
            '$env.PROMPT_COMMAND = {|| "" }\n'
            '$env.PROMPT_COMMAND_RIGHT = {|| "" }\n'
            '$env.PROMPT_INDICATOR = {|| $"(ansi green)\u276f (ansi reset)" }\n',
            encoding="utf-8",
        )
        config_nu = scratch / "config.nu"
        config_nu.write_text(
            f"$env.config.show_banner = false\nsource {hook}\n",
            encoding="utf-8",
        )
        return ["nu", "--env-config", str(env_nu), "--config", str(config_nu)], env
    raise RuntimeError(f"cast drives zsh, bash, fish, pwsh, or nushell (got {shell!r})")


@tasks.task(name="cast")
@requires_dep("rich", "pyte")
@requires(lambda: sys.platform != "win32", reason="needs a POSIX pseudo-terminal")
def cast(
    *keys: str,
    out: Annotated[Path, doc("the animated SVG file to write")],
    shell: Annotated[
        Literal["zsh", "bash", "fish", "pwsh", "nushell"],
        doc("interactive shell to drive"),
    ] = "zsh",
    title: Annotated[
        str,
        # `shell` is declared to the left, so it is resolved by the time this
        # runs — which is the whole reason a default may read its siblings.
        default(lambda p: f"{p['shell']} · completion"),
        doc("window title"),
    ] = "",
    width: Annotated[int, between(40, 200), doc("terminal columns")] = 72,
    height: Annotated[int, between(4, 50), doc("terminal rows")] = 14,
    prog: Annotated[str, _invoking_cli, doc("CLI whose completion is installed")] = "",
    cwd: Annotated[
        Path | None, doc("directory the shell starts in; empty takes here")
    ] = None,
    pace: Annotated[
        float, between(0.2, 5.0), doc("seconds each keypress stays on screen")
    ] = 1.2,
    steady: Annotated[
        float,
        between(1.0, 5.0),
        doc("scale the quiet-detection windows; raise on a loaded machine"),
    ] = 1.0,
) -> list[str]:
    """Record an animated SVG of a real interactive shell session.

    Boots the shell from a scratch config with footman completion loaded
    (via `--setup-completion`; nushell sources the generated hook), types
    the script — everything after `--`, where `<TAB>`, `<ENTER>`,
    `<WAIT>` and friends are keys — and replays
    the capture through a terminal emulator into an animated, dependency-
    free SVG with the session's real timing. TAB completion, in motion,
    regenerated on every docs build so it cannot drift.
    """
    if sys.platform == "win32":  # the @requires gate already refused; belt
        raise RuntimeError("docs cast needs a POSIX pseudo-terminal")
    import tempfile

    if shutil.which(prog) is None:
        raise RuntimeError(f"{prog!r} is not on PATH")
    if shutil.which(_CAST_BOOT.get(shell, shell)) is None:
        raise RuntimeError(f"{shell!r} is not on PATH")

    with tempfile.TemporaryDirectory() as scratch:
        argv, env_extra = _boot_shell(shell, prog, Path(scratch))
        # A cold shell on a loaded CI runner — pwsh especially, with its
        # .NET startup — can take longer than one settle window to draw
        # anything at all, and an empty capture is indistinguishable from
        # a dead session. Retry once with a much longer settle before
        # declaring failure; the happy path pays nothing extra.
        frames = []
        key_log: list[tuple[float, str]] = []
        for settle in (1.5, 5.0):
            key_log.clear()  # a retry re-plays the script; keep only its keys
            chunks = _pty_session(
                argv,
                width=width,
                height=height,
                sends=keystrokes(keys),
                settle=settle,
                key_log=key_log,
                env_extra=env_extra,
                # bash: readline honours the tty's echo flag and types
                # invisibly without it — and bash sends no queries, so
                # nothing can flash. Every other shell self-renders.
                keep_echo=shell == "bash",
                cwd=cwd,
                steady=steady,
                answer_cursor=shell in _NEEDS_CURSOR_REPLY,
            )
            frames = _event_screens(chunks, key_log, width=width, height=height)
            if frames:
                break
    if not frames:
        raise RuntimeError(f"the {shell} session produced no output")
    # No thinning: every frame is an event the script asked for, so dropping
    # one drops a keypress from the story rather than a redundant redraw.

    from rich.console import Console
    from rich.text import Text

    # Every class and keyframe this file defines is namespaced by the file's
    # own name. rich's ids are unique within one export, not between two —
    # and an inlined SVG's <style> is not scoped to that SVG, so five
    # recordings on one page all defined `.cf0`, `.cf7-r3`, `@keyframes cf0`
    # and the last one in the document won for every one of them: casts
    # animating to another recording's timings, cells painted in another's
    # palette. Each was correct alone and wrong together.
    uid = re.sub(r"[^A-Za-z0-9_-]", "-", out.stem) or "cast"

    svgs: list[str] = []
    for i, (caption, grid) in enumerate(frames):
        console = Console(
            record=True, width=width, file=io.StringIO(), force_terminal=True
        )
        for row in grid:
            text = Text()
            for ch, style in row:
                text.append(ch, style or None)
            console.print(text)
        svgs.append(
            _with_caption(
                console.export_svg(title=title, unique_id=f"{uid}-cf{i}"),
                caption,
                f"{uid}-cf{i}",
            )
        )
    # A fixed beat per event, not the timing of the capture. What the machine
    # was doing while it recorded is not part of the story, and pacing on it
    # made a shell's own quirks — fish clearing its pager, pwsh painting in
    # bursts — decide how long a reader got to look at each step.
    times = [i * pace for i in range(len(frames))]
    out.parent.mkdir(parents=True, exist_ok=True)
    # The last frame gets a beat exactly like every other one. The default
    # hold is longer, which made sense when frames ran at the capture's own
    # timing and the end needed marking — under a fixed pace it is just the
    # recording sitting still before it loops.
    out.write_text(
        compose_animation(svgs, times, hold=pace, prefix=f"{uid}-cf"),
        encoding="utf-8",
    )
    print(f"wrote {out} ({len(frames)} frames)")
    return [str(out)]


@tasks.task
def errors(
    out: Annotated[
        Path | None, doc("file to write the page into; omitted = stdout")
    ] = None,
) -> list[str] | None:
    """Render every runtime error and note as a markdown reference page.

    The entries are read from the source itself rather than written by
    hand: every taught error and every teach-once note the runner can
    print, with the runtime-filled parts shown as \u27e8like this\u27e9. A page
    that regenerates this on each docs build can never drift from what the
    runner actually says. Without --out the page is the task's stdout.
    """
    import ast

    import livery.footman as footman

    def template(node: ast.expr) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            parts: list[str] = []
            for value in node.values:
                if isinstance(value, ast.Constant):
                    parts.append(str(value.value))
                elif isinstance(value, ast.FormattedValue):
                    parts.append("\u27e8" + ast.unparse(value.value) + "\u27e9")
            return "".join(parts)
        return None

    src = Path(footman.__file__).parent
    entries: list[tuple[str, str, str]] = []  # (module, kind, message)
    titles: dict[str, str] = {}  # module stem -> its docstring's first line
    for py in sorted(src.glob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        summary = (ast.get_docstring(tree) or "").partition("\n")[0].strip()
        titles[py.stem] = summary.rstrip(".")
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
                call = node.exc
                func = call.func
                kind = getattr(func, "id", None) or getattr(func, "attr", None)
                if not call.args or kind is None:
                    continue
                message = template(call.args[0])
                # Length filters the trivia (AttributeError(name), re-raises);
                # every taught error is a sentence or three.
                if message and len(message) >= 40:
                    entries.append((py.stem, kind, message))
            elif isinstance(node, ast.Call):
                func_name = getattr(node.func, "id", None)
                if func_name == "_note" and len(node.args) >= 2:
                    message = template(node.args[1])
                    if message:
                        entries.append((py.stem, "note", message))

    lines = [
        "# Errors & notes",
        "",
        "Everything footman can say when it refuses, warns, or teaches —",
        "extracted from the source on every docs build, so this page cannot",
        "drift from the runner. Angle-bracketed parts (\u27e8like this\u27e9) are",
        "filled in at runtime; **note** entries print once per task on",
        "stderr and never stop the run.",
        "",
    ]
    for module in sorted({m for m, _, _ in entries}):
        # The heading a reader can use: the module's own one-line summary,
        # not its private filename. The stem is the fallback, never the face.
        lines += [f"## {titles.get(module) or f'`{module}`'}", ""]
        module_entries = sorted((k, msg) for m, k, msg in entries if m == module)
        for kind, message in module_entries:
            lines += [f"**{kind}**", "", "``` text", message.rstrip(), "```", ""]
    text = "\n".join(lines)
    if out is None:
        print(text, end="")
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return [str(out)]


@tasks.task
def config(
    out: Annotated[
        Path | None, doc("file to write the table into; omitted = stdout")
    ] = None,
    prog: Annotated[str, _invoking_cli, doc("command name in the table")] = "",
) -> list[str] | None:
    """Render this runner's config keys as a markdown table.

    The rows come from the runner's own list of recognised keys, so a
    reference page that regenerates this on each docs build can neither
    describe a key the runner lacks nor miss one it has — and it names the
    table *this* runner reads (`[tool.acme]` under a branded CLI), because
    a runner opens its own table and no other. Without --out the table is
    the task's stdout; with --out it is written to the file.
    """
    text = markdown.config_table(prog=prog)
    if out is None:
        print(text, end="")
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out}")
    return [str(out)]


@tasks.task
def notes(
    out: Annotated[
        Path | None, doc("file to write the table into; omitted = stdout")
    ] = None,
    prog: Annotated[str, _invoking_cli, doc("command name in the table")] = "",
) -> list[str] | None:
    """Render the note kinds as a markdown table.

    The rows come from the runner's own kind registry — what the level
    system resolves against and the config validator refuses unknown kinds
    by — so a reference page that regenerates this on each docs build can
    neither list a kind footman lacks nor miss one it has. Without --out
    the table is the task's stdout; with --out it is written to the file.
    """
    text = markdown.notes_table(prog=prog)
    if out is None:
        print(text, end="")
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out}")
    return [str(out)]


@tasks.task(name="globals")
def globals_(
    out: Annotated[
        Path | None, doc("file to write the table into; omitted = stdout")
    ] = None,
    prog: Annotated[str, _invoking_cli, doc("command name in the table")] = "",
) -> list[str] | None:
    """Render the runner's global options as a markdown table.

    The rows come straight from the CLI grammar — the same table `--help`
    prints — so a reference page that regenerates this on each docs build
    can never drift from the runner. Without --out the table is the task's
    stdout; with --out it is written to the file.
    """
    text = markdown.globals_table(prog=prog)
    if out is None:
        print(text, end="")
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out}")
    return [str(out)]
