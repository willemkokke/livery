"""The seam between the bridge and whatever executes its calls.

toolroom is vocabulary-complete on its own, and the seam speaks stdlib:
the bridge hands this module a `list[str]` plus run policy, and gets
back an int-with-attributes. When footman is present in the process,
every call routes through footman's `run()` — receipts, capture,
dry-run, `recording()`, lanes — without footman ever importing (or even
knowing about) toolroom. When it is not, a plain subprocess executor
answers with toolroom's own `Result`.

Detection keys on *orchestration*, not mere presence: a call routes
hosted only when a footman context is actually live — a task body, a
`parallel()` worker, a `recording()` block — because that is when
receipts, dry-run, and recording must be impossible to bypass. A bare
call in a process that merely *imported* footman (a pytest run
auto-loading footman's plugin, an app embedding both) takes the
standalone executor and standalone semantics, deterministically: which
exception a failure raises must never depend on what some other module
imported. An installed-but-never-imported footman does not make this a
footman process either, and importing it here would change the process
just to answer a question about it.

The hosted branch's lazy imports reach the same names the in-tree
bridge imports today. When footman grows a named executor contract,
this module is the only place that changes.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any


class Argv(list[str]):
    """A command line built but not run — `toolroom.git.push.argv(force=True)`.

    An ordinary `list[str]` everywhere in Python: it indexes, slices,
    iterates, compares and prints exactly like the list it is, and any
    runner spawns it as-is. Always the raw tokens — the one shape of a
    command with no shell in it. Passing the tokens on is plain Python:
    a wrapper takes them splatted (`uv.run("--", *cmd)`), and stdlib
    helpers take the list directly (`shlex.join(cmd)`).

    The moment the line is about to cross into a shell, name that shell:

    * `.posix()` — one string, `shlex`-quoted for `sh`/`bash`/`zsh`.
    * `.windows()` — one string, quoted the way `CreateProcess` parses.

    Naming it is the caller's job because toolroom cannot know it: the
    same handle can target different hosts across calls, the OS does not
    determine the shell, and the payload may reach no shell at all. The
    quoting is the *destination's*, never this machine's.

    footman keeps a twin of this class for its receipts
    (`Result.to_argv()`); both are ordinary `list[str]`, so they compare
    equal token-for-token and either flows anywhere the other does.
    """

    __slots__ = ()

    def posix(self) -> str:
        """This command as one string, quoted for a POSIX shell."""
        return shlex.join(self)

    def windows(self) -> str:
        """This command as one string, quoted the way `CreateProcess` parses."""
        return subprocess.list2cmdline(self)


class Result(int):
    """The outcome of one standalone tool call — and the value it returns.

    A `Result` *is* the exit code: it subclasses `int`, so
    `code = git.push()`, `if git.push()`, and `== 0` all read naturally.
    It also carries the captured output, split by stream, and the tokens
    that ran. Under a footman host the bridge returns footman's own
    `Result` instead — a superset of this surface (receipts, `--json`,
    replay-on-failure); the two are twins by design, never the same
    class, and code written against this surface runs under both.
    """

    # No __slots__: int is a variable-length base, which CPython refuses to
    # pair with nonempty slots — instances carry a tiny __dict__ instead.
    _stdout: str
    _stderr: str
    _tokens: tuple[str, ...]

    def __new__(
        cls,
        code: int,
        *,
        stdout: str = "",
        stderr: str = "",
        tokens: tuple[str, ...] = (),
    ) -> Result:
        self = super().__new__(cls, code)
        self._stdout = stdout
        self._stderr = stderr
        self._tokens = tokens
        return self

    @property
    def ok(self) -> bool:
        """True when the tool exited zero."""
        return int(self) == 0

    @property
    def code(self) -> int:
        """The exit code as a plain int — the value this Result *is*."""
        return int(self)

    @property
    def stdout(self) -> str:
        """The captured standard output (empty when `capture=False`)."""
        return self._stdout

    @property
    def stderr(self) -> str:
        """The captured standard error (empty when `capture=False`)."""
        return self._stderr

    @property
    def command(self) -> str:
        """The command that ran, as one readable line.

        A token that redacts itself — footman's `Secret`, duck-typed on
        `reveal` so nothing here imports it — reads back as `***`: this
        line exists to be shown, and a secret leaves through `.to_argv()`
        or the caller's own unwrap, never through display.
        """
        return " ".join(
            "***" if hasattr(token, "reveal") else shlex.quote(token)
            for token in self._tokens
        )

    def to_argv(self) -> Argv:
        """The executed command as an `Argv` — raw tokens, re-quotable for
        whichever shell a further hop will parse them with.
        """
        return Argv(self._tokens)

    def __repr__(self) -> str:
        return f"Result({int(self)}, command={self.command!r})"


class ToolError(RuntimeError):
    """A standalone tool call exited non-zero (and `nofail` was not set).

    Carries the `Result` so the handler keeps the streams and the
    command. Under a footman host failures take footman's lane instead
    (the task fails, output replays); this error exists only where there
    is no host to do that.
    """

    def __init__(self, result: Result) -> None:
        tail = result.stderr.strip() or result.stdout.strip()
        tail = f"\n{tail}" if tail else ""
        super().__init__(f"`{result.command}` exited {int(result)}{tail}")
        self.result = result


def hosted() -> bool:
    """Whether footman is orchestrating this call.

    True only with a live footman context — a task body, a `parallel()`
    worker, a `recording()` block — where routing through `run()` is
    what keeps receipts, dry-run, and recording honest. Presence alone
    (footman merely imported somewhere in the process) is not
    orchestration: a standalone-minded call keeps standalone semantics
    however the process came to hold a footman.
    """
    # The runner's real module is livery.footman; a process holding only
    # the old `footman` shim name has no host to route through.
    if "livery.footman" not in sys.modules:
        return False
    from livery.footman.context import _current

    return _current.get() is not None


def container_error(value: Any, where: str, *, example: str = "") -> str:
    """The taught refusal for a bare container in an argv slot.

    toolroom's own copy of the wording footman's `run()` door teaches —
    shared *wording*, per the split ruling, never shared code — so the
    lesson reads the same whichever door refuses. An `Argv` gets its own
    text: it is the one container that plausibly lands here on purpose,
    and the fix differs by what was meant.
    """
    if isinstance(value, Argv) or callable(getattr(value, "posix", None)):
        return (
            f"{where}: a built command line (Argv) was passed as one "
            f"positional argument, and that spelling is ambiguous. Say which "
            f"you meant: splat it (`*cmd`) to pass its tokens — what a "
            f"wrapper like `uv run` takes — or serialise it (`cmd.posix()` / "
            f"`cmd.windows()`) to pass one quoted line for the shell that "
            f"will parse it — what an `ssh` payload is."
        )
    kind = type(value).__name__
    if isinstance(value, dict):
        fix = f"Spread it with `**` to mean flags: `{example}`. " if example else ""
    else:
        star = example or f"{where}(*value)"
        fix = f"Spread it with `*` to mean arguments: `{star}`. "
    return (
        f"{where}: a bare {kind} in an argument slot is ambiguous. {fix}"
        f"A container is never a single argument."
    )


# --- colour: one bit in, per-tool forcing out ---------------------------------
#
# The colour seam is two environment variable names and nothing else. Whoever
# decides — a footman run publishing its run-wide answer at the run boundary, a
# user exporting one, or this module reading the terminal — spells the decision
# `FORCE_COLOR` or `NO_COLOR`, and toolroom translates it into what each tool
# actually needs: the whole force set for the children that read the
# environment, the tool's own switch for the few that ignore it (see
# `_colordata`). Nothing here imports footman: the answer is already in the
# environment by the time a call reaches the seam.

# Every colour variable toolroom speaks. Emitted by presence/absence — never
# `FORCE_COLOR=0`, which some tools (ruff) read as "force on" — and consumed the
# same way, save that `FORCE_COLOR` is read by truthiness so an explicit `"0"`
# does not force.
_COLOR_VARS = ("FORCE_COLOR", "CLICOLOR_FORCE", "CLICOLOR", "NO_COLOR")
_FORCE_VARS = ("FORCE_COLOR", "CLICOLOR_FORCE", "CLICOLOR")


def color_on() -> bool:
    """The ambient colour answer for this process — the ladder's bottom rung.

    Read from the environment, hosted or not: `NO_COLOR` wins, any of the
    force variables forces, and otherwise a terminal on stdout decides (a
    dumb one is no terminal). Inside a footman run that reads the answer
    the run published at its boundary; outside one, whatever the user
    exported. One rule, no import, and nothing that varies with whether
    footman happens to be in the process.

    Ambient is the only tier the environment carries. A decision meant for
    one call travels *on* the call — `.opts(color=…)` — because
    `os.environ` is one per process while calls are not, and two threads
    wanting different answers cannot both have one here.
    """
    if "NO_COLOR" in os.environ:
        return False
    if any(os.environ.get(var) not in (None, "", "0") for var in _FORCE_VARS):
        return True
    out = sys.stdout
    if out is None or not out.isatty():
        return False
    return os.environ.get("TERM") != "dumb"


def colour_wanted(mode: str) -> bool:
    """A colour mode resolved to an answer — the ladder, one rung at a time.

    `always`/`never` are the answer; `auto` takes the ambient one. The
    seam owns this because both lanes need it: the argv half must pick a
    tool's on- or off-switch here whatever executes the call.
    """
    if mode == "always":
        return True
    if mode == "never":
        return False
    return color_on()


def color_env(on: bool) -> dict[str, str]:
    """The colour variables to set on a child to force colour on (or off).

    A spawned tool's stdout is a pipe whenever toolroom captures it, so
    `isatty()` is false and a well-behaved tool goes monochrome — exactly
    when the bytes are headed for a terminal anyway. This is what those
    variables are for, so forcing on sets the whole force set and forcing
    off sets `NO_COLOR`; the direction is completed by *removing* the
    other side's variables (see `child_env`), never by setting `"0"`.
    """
    if on:
        return dict.fromkeys(_FORCE_VARS, "1")
    return {"NO_COLOR": "1"}


def child_env(env: dict[str, str] | None, mode: str) -> dict[str, str] | None:
    """The child's environment with this call's colour answer written in.

    *env* follows `run(env=…)`: None inherits, a mapping replaces. A
    *decided* colour merges on top of whichever of those the call has —
    an instruction aimed at this child outranks the environment it was
    handed, exactly as `run(color=…)` treats it hosted. `auto` leaves an
    explicit environment alone, because ambient is what that environment
    already carries; inheriting, it writes the ambient answer in, since
    standalone has no run boundary to have published it.

    Every colour variable is cleared before this direction's are set, so
    off leaves no inherited `FORCE_COLOR` for a presence-checking tool to
    honour and on leaves no stray `NO_COLOR`.
    """
    if mode == "auto" and env is not None:
        return env
    base = env if env is not None else os.environ
    composed = {k: v for k, v in base.items() if k not in _COLOR_VARS}
    composed.update(color_env(colour_wanted(mode)))
    return composed


def note(text: str) -> None:
    """A quiet aside about how a call ran (demotion notes).

    Hosted, it reaches the real stderr when the run is `--verbose`;
    standalone there is no verbose lane, so the note is dropped — the
    demotions it describes are behaviour-preserving either way.
    """
    if hosted():
        from livery.footman.context import current, real_stderr

        if current().verbose:
            real_stderr().write(text)


_RUN_COLOUR: bool | None = None


def _run_takes_colour(fm_run: Any) -> bool:
    """Whether this footman's `run()` takes the colour keyword.

    Probed once per process rather than pinned by a version floor: the
    keyword arrived in a footman later than the one toolroom's floor
    names, and a bridge that reads the signature works against both
    without either package waiting on the other's release.
    """
    global _RUN_COLOUR
    if _RUN_COLOUR is None:
        import inspect

        try:
            _RUN_COLOUR = "color" in inspect.signature(fm_run).parameters
        except (TypeError, ValueError):  # unintrospectable — assume the older shape
            _RUN_COLOUR = False
    return _RUN_COLOUR


def run(
    target: list[str] | Any,
    *,
    parts: tuple[tuple[str, str], ...],
    exact: tuple[str, ...],
    handed: tuple[tuple[Any, ...], dict[str, Any]] | None = None,
    nofail: bool = False,
    capture: bool = True,
    input: str | None = None,
    env: dict[str, str] | None = None,
    title: str | None = None,
    pre_record: Any = None,
    recorded: bool = True,
    timeout: float | None = None,
    cwd: str | Path | None = None,
    rel: str | Path | None = None,
    color: str = "auto",
) -> Any:
    """The one door every bridge call leaves through.

    *target* is the argv to spawn — or, hosted-only, the in-process
    callable the bridge prepared. *parts*/*exact* are the two spellings
    of the same call (role-tagged for painting, literal for `--verbose`);
    standalone execution has no receipt to paint, so it reads neither.
    *handed* is the call as the caller wrote it — positionals and
    keywords pre-render, a False flag included where the argv omits it.
    Execution ignores it, like `probe()`'s *shown*: it exists for the
    testing seam, which records what the code under test decided to
    pass, not only what would have run.

    *color* is the call's colour mode, unresolved — `auto` means "follow
    whoever owns the ambient", and hosted that is the run, so the mode
    travels rather than an answer computed from the environment. The
    argv half is already decided by the time a call reaches here; this is
    the environment half, which hosted belongs to footman: `run(color=)`
    applies it to that one child, merging over `env=` and reaching the
    in-process lane too.
    """
    if hosted():
        from livery.footman.context import Invocation
        from livery.footman.context import run as fm_run

        painted: dict[str, Any] = {}
        if _run_takes_colour(fm_run):
            painted["color"] = color
        elif color != "auto":
            # A footman too old for the keyword: the tool's own switch still
            # carries the decision, so a flag tool obeys and an env-reading
            # one follows the run. Worth a word, not a failure.
            note(
                f"colour: this footman has no run(color=), so color={color!r} "
                f"reaches only the tools that take a switch\n"
            )
        return fm_run(
            target,
            nofail=nofail,
            capture=capture,
            input=input,
            env=env,
            title=title,
            pre_record=pre_record,
            recorded=recorded,
            timeout=timeout,
            cwd=cwd,
            rel=rel,
            **painted,
            _show=Invocation(parts, exact),
        )
    if not isinstance(target, list):
        raise TypeError(
            "an in-process call needs a footman host; standalone toolroom always spawns"
        )
    return _standalone(
        target,
        nofail=nofail,
        capture=capture,
        input=input,
        env=env,
        color=color,
        timeout=timeout,
        cwd=cwd,
        rel=rel,
    )


def probe(
    argv: list[str],
    *,
    shown: tuple[str, ...],
    env: dict[str, str] | None,
    timeout: float | None,
) -> Result:
    """A truthful value read outside any host — `installed_version`'s spawn.

    Deliberately not `run()`: a version read is not a step of anyone's
    story, so it must answer the same under `--dry-run` and `recording()`
    as it does bare — no receipt, no colour ladder, no footman at all.
    It lives at the seam so the testing double (`toolroom.testing`) has
    one door to intercept; *shown* is the name-led spelling of the same
    call, which that double matches canned answers against — execution
    ignores it, and never raises for a non-zero exit: the caller reads
    the code.
    """
    # Decode as UTF-8 with replacement (F39): a tool that prints a
    # non-ASCII glyph in its --version must not crash the read on a
    # locale-encoded pipe (cp1252 on Windows).
    proc = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
    )
    return Result(
        proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        tokens=tuple(argv),
    )


def _standalone(
    argv: list[str],
    *,
    nofail: bool,
    capture: bool,
    input: str | None,
    env: dict[str, str] | None,
    color: str,
    timeout: float | None,
    cwd: str | Path | None,
    rel: str | Path | None,
) -> Result:
    """Spawn *argv* with plain `subprocess` and answer in toolroom's `Result`.

    The reporting-lane policy (`title`, `recorded`, `pre_record`) has no
    meaning without a host and is accepted upstream then ignored here;
    the execution-lane policy is honoured: `capture` holds the streams,
    `input` feeds stdin, `env` is the child's whole environment exactly
    as `run(env=…)` means it hosted — what you pass is what the child
    gets, never a merge over the parent's — `cwd`/`rel` root the call,
    `timeout` bounds it, and a non-zero exit raises `ToolError` unless
    `nofail`.

    An inherited environment is normalised for this call's colour answer
    on the way out (`child_env`): hosted, footman publishes the run's once
    at the run boundary and every child inherits it; standalone there is
    no run boundary, so the seam writes the answer per spawn.
    """
    base = None if cwd in (None, "unmanaged") else Path(cwd)
    directory = (base or Path(os.getcwd())) / rel if rel is not None else base
    proc = subprocess.run(
        argv,
        capture_output=capture,
        text=True,
        encoding="utf-8",
        errors="replace",
        input=input,
        env=child_env(env, color),
        cwd=directory,
        timeout=timeout,
    )
    result = Result(
        proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        tokens=tuple(argv),
    )
    if not result.ok and not nofail:
        raise ToolError(result)
    return result
