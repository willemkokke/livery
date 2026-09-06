"""The run context, the `run()` helper, and `parallel()`.

A task never *needs* a context parameter: `run()` reads the current context
from a contextvar footman sets around each running task, so a task body can just
call `run("ruff check src")`. A task MAY declare a first parameter named
`ctx` (or annotated `Context`) to get the object explicitly.

Output is routed through the context so parallel tasks don't interleave: a global
`sys.stdout` proxy dispatches every write to the running task's `sink`. In
sequential mode a task's sink is the real stdout (live); in parallel mode it is a
per-task buffer, flushed atomically when the task finishes.
"""

from __future__ import annotations

import contextlib
import functools
import inspect
import io
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from collections.abc import (
    Callable,
    Generator,
    Iterable,
    Iterator,
    Mapping,
    Sequence,
    Sized,
)
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Literal,
    NamedTuple,
    NoReturn,
    Protocol,
    TextIO,
    TypeAlias,
    TypeVar,
    overload,
)

from livery.footman import _globals

if TYPE_CHECKING:
    from livery.footman import _step
    from livery.footman import registry as _registry_t


class AuditEntry(NamedTuple):
    """One entry of a record's audit: a lifecycle moment that acted on the
    verdict — who acted, and what they set.

    The body entry is always present and carries what the work itself
    produced; a review entry names the reviewer, with `code` None when it
    was involved without writing the verdict (a reviewer that only set the
    title). Entries are in execution order.
    """

    moment: str
    """Where in the record's lifecycle this happened: `"body"` for the work
    itself, `"review"` for a `pre_record` reviewer."""
    actor: str
    """Who acted: the command line for the body, the hook's name for a
    reviewer."""
    code: int | None
    """The code this moment left, or None for involvement without a verdict
    write."""


_Moment: TypeAlias = Literal["bind", "enter", "body", "review", "observe"]
"""The lifecycle vocabulary, closed at the PRODUCER side only: the public
fields stay open strings (the `state` precedent — readers tolerate values
they don't know), while footman's own write sites go through
`_audit_entry`, so a misspelt moment in framework code is a type error
before it is a test failure."""


def _audit_entry(moment: _Moment, actor: str, code: int | None) -> AuditEntry:
    """The one door framework code writes audit entries through."""
    return AuditEntry(moment, actor, code)


class Argv(list[str]):
    """A command line built but not run — what `Result.to_argv()` hands back
    (and what a builder like toolroom's `git.push.argv(force=True)` makes).

    An ordinary `list[str]` everywhere in Python: it indexes, slices,
    iterates, compares and prints exactly like the list it is, and `run(cmd)`
    spawns it as-is. Always the raw tokens — the one shape of a command with
    no shell in it. Passing the tokens on is plain Python: `run(cmd)` runs
    them, a wrapper takes them splatted (`uv.run("--", *cmd)`), and stdlib
    helpers take the list directly (`shlex.join(cmd)`).

    The moment the line is about to cross into a shell, name that shell:

    * `.posix()` — one string, `shlex`-quoted for `sh`/`bash`/`zsh`.
    * `.windows()` — one string, quoted the way `CreateProcess` parses.

    Naming it is the caller's job because footman cannot know it: the same
    handle can target different hosts across calls, the OS does not determine
    the shell, and the payload may reach no shell at all. The quoting is the
    *destination's*, never this machine's — a line built on Windows for a
    Linux box still comes back POSIX-quoted (which is why neither method is
    the local-platform `_shell_quote`).

    Lives here beside `Result` because `run()` accepts one and
    `Result.to_argv()` returns one. toolroom carries its own twin of this
    class on purpose — the seam between the packages speaks plain
    `list[str]`, so neither ever imports the other's.
    """

    __slots__ = ()

    def posix(self) -> str:
        """This command as one string, quoted for a POSIX shell.

        What an `ssh` payload wants: ssh joins its remaining arguments with
        spaces and hands the remote shell **one string** to re-split, so the
        only argument boundaries that survive are the ones quoted into it.

            cmd = git.commit.argv(m="ship 1.2.0")
            ssh("deploy@host", cmd.posix())

        Quoting a built line that already carries a `.posix()` payload quotes
        it once more, which is exactly what each further hop needs.
        """
        return shlex.join(self)

    def windows(self) -> str:
        """This command as one string, quoted the way `CreateProcess` parses.

        The Windows counterpart of `.posix()`, for a payload headed to a
        Windows box — `subprocess.list2cmdline` quoting, which `cmd` and
        PowerShell both read. Chosen by the caller, never sniffed from the
        machine footman happens to be standing on.
        """
        return subprocess.list2cmdline(self)


class Result(int):
    """The outcome of one `run()` call — and the value `run()` returns.

    A `Result` *is* the exit code: it subclasses `int`, so `code = run(...)`,
    `if run(...)`, and `run(...) == 0` all keep working. It also carries the
    captured output, split by stream, and the command that produced it — so
    `run("git rev-parse HEAD").stdout.strip()` reads the hash without the
    stderr noise glued on. `stdout`/`stderr` are separated for both subprocess
    and in-process runs; a streamed run (`capture=False`) leaves them empty.
    """

    _command: str
    _stdout: str
    _stderr: str
    _duration: float
    _raw: str
    _shown: str
    _timed_out: bool
    _address: str
    _audit: tuple[AuditEntry, ...]
    _tokens: tuple[str, ...]
    _started: float | None
    _env: dict[str, str] | None
    _cwd: Path | None

    def __new__(
        cls,
        code: int,
        *,
        command: str = "",
        stdout: str = "",
        stderr: str = "",
        duration: float = 0.0,
        raw: str = "",
        shown: str = "",
        timed_out: bool = False,
        address: str = "",
        audit: tuple[AuditEntry, ...] = (),
        tokens: tuple[str, ...] = (),
        started: float | None = None,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> Result:
        self = super().__new__(cls, code)
        object.__setattr__(self, "_env", env)
        object.__setattr__(self, "_cwd", cwd)
        object.__setattr__(self, "_timed_out", timed_out)
        object.__setattr__(self, "_address", address)
        object.__setattr__(self, "_started", started)
        object.__setattr__(self, "_command", command)
        object.__setattr__(self, "_stdout", stdout)
        object.__setattr__(self, "_stderr", stderr)
        object.__setattr__(self, "_duration", duration)
        object.__setattr__(self, "_raw", raw or command)
        # What every surface that *prints* this call reads, so the record can
        # keep the value the caller passed while nothing shows it. Falls back
        # to the command itself, which is what it is for a call with no
        # `Secret` in it — the overwhelming majority.
        object.__setattr__(self, "_shown", shown or command)
        object.__setattr__(self, "_audit", audit)
        # The argv as separate tokens, kept so `to_argv()` can re-quote for a
        # shell the caller names. `_raw` cannot stand in: it is quoted for the
        # local platform, and `shlex` cannot reliably take a `list2cmdline`
        # string apart again.
        object.__setattr__(self, "_tokens", tokens)
        return self

    def to_argv(self) -> Argv:
        """What this call ran, as the tokens it was spawned from.

        `.raw` shows the command line quoted for the machine footman is
        standing on, which is right for a `--verbose` line and wrong the
        moment the string is sent somewhere else. This returns the same
        command as an `Argv` — raw tokens, re-quotable for whichever shell
        will actually parse them:

            r = git.commit(m="ship 1.2.0")
            r.to_argv()          # ["git", "commit", "-m", "ship 1.2.0"]
            r.to_argv().posix()  # "git commit -m 'ship 1.2.0'"

        Named apart from toolroom's `.argv` builders on purpose: this one describes
        a call that has **already run**, so `git.push().argv(…)` — meaning to
        *build* a command line — is an `AttributeError` rather than a push
        that quietly happened. For the same reason the two can differ by one
        token: what ran may carry a forced-colour switch (git's
        `-c color.ui=always`) that a line built to be sent elsewhere does not.
        """
        if not self._tokens:
            raise ValueError(
                f"to_argv(): no argv was recorded for `{self._shown}`. Only a "
                f"spawned command has separable tokens — an in-process call, a "
                f"Python callable, or a `run()` given a command *string* never "
                f"had them apart. Pass a list (`run(['git', 'push'])`) or use "
                f"a toolroom handle, whose calls always record their argv."
            )
        return Argv(self._tokens)

    @property
    def command(self) -> str:
        """The command line that ran, normalised for reading — options in
        separated form, values shell-quoted. What `recording()` asserts
        against, and what a dependent reads back."""
        return self._command

    @property
    def shown(self) -> str:
        """`command` as it is safe to **print** — every argument that arrived
        as a `Secret` replaced by `***`, everything else identical.

        Equal to `command` unless the call carried a secret, so a record is
        the same string on both sides in the ordinary case. Every surface
        footman shows a command line on reads this one: the step line, the
        `--verbose` announce, `--json`, a profile span, the message a
        `RunFailed` carries. The record keeps the real value, because a
        dependent, `recording()`, and the caller who passed it all read
        `command` and none of them are a display."""
        return self._shown

    @property
    def stdout(self) -> str:
        """Captured standard output; empty when the step streamed instead."""
        return self._stdout

    @property
    def stderr(self) -> str:
        """Captured standard error; empty when the step streamed instead."""
        return self._stderr

    @property
    def duration(self) -> float:
        """Wall-clock seconds the step took."""
        return self._duration

    @property
    def started(self) -> float | None:
        """When the step began, on the run's monotonic clock — the same clock
        a task row's `started` reads, so a step places inside its task's
        span. `None` for a record that never ran (a dry-run's rehearsal)."""
        return self._started

    @property
    def raw(self) -> str:
        """The exact command line executed, shell-quoted — the bytes footman
        handed the tool, which may spell an option `--flag=value` where
        `command` shows `--flag value`. What `--verbose` prints. Equal to
        `command` when there is nothing to normalise."""
        return self._raw

    @property
    def timed_out(self) -> bool:
        """Whether `timeout=` expired and footman killed the tree. The code
        is 124 — the shell convention — and `stdout`/`stderr` hold whatever
        the command managed to say first, which on a hang is the only clue
        there is."""
        return self._timed_out

    @property
    def address(self) -> str:
        """The record's tree-derived name: the requester's path, this
        record's label, and an ordinal once a label repeats among siblings —
        counted in request order as written, so it is deterministic across
        runs and hosts. Empty outside a managed context."""
        return self._address

    @property
    def audit(self) -> tuple[AuditEntry, ...]:
        """The verdict's provenance: every lifecycle moment that acted on
        it, in execution order — the body entry with what the work itself
        produced, a review entry per `pre_record` reviewer. Empty for a
        record that executed nothing (a dry-run plan line)."""
        return self._audit

    @property
    def env(self) -> dict[str, str] | None:
        """The environment this call would have run in — the task's own, or
        the `env=` handed to the call — kept on a **recorded** step only, so
        a `recording()` can assert what a command would have seen (`assert
        "UV_TOOL_DIR" not in steps[0].env`). `None` on a record that ran:
        a live environment can hold secrets, and a record travels into
        `--json` and the report."""
        return self._env

    @property
    def cwd(self) -> Path | None:
        """The directory this call would have run in — the task's resolved
        one, or the call's `cwd=`/`rel=` — kept on a **recorded** step only,
        like `env`. `None` on a record that ran, and for a call that opted
        out with `cwd="unmanaged"`."""
        return self._cwd

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(
            f"Result.{name} is sealed — a committed record is immutable. "
            f"Amend verdicts in the review window (pre_record), or veto "
            f"with fail(reason, code) from an observer."
        )

    @property
    def code(self) -> int:
        """The exit code (0 is success) — the same value the `Result` itself is."""
        return int(self)

    @property
    def ok(self) -> bool:
        """Whether the command succeeded (exit code 0)."""
        return self == 0

    @property
    def output(self) -> str:
        """`stdout` then `stderr`, concatenated — a convenience for "show me
        everything". NOT interleaved in real time (each stream is captured
        whole); when the order *across* the two streams matters, read `stdout`
        and `stderr` separately."""
        return self.stdout + self.stderr

    @property
    def failed_at(self) -> str | None:
        """The lifecycle moment the failure came from, None on success — a
        derived reading of the audit: the last moment that wrote the verdict.
        A red tool reviewed green reads None (the record succeeded); a green
        tool failed by its reviewer reads `"review"`."""
        return _failed_moment(self.code, self.audit)

    @property
    def work_code(self) -> int | None:
        """The code the record carried when the failing moment began — a
        derived reading of the audit, None on success or when nothing came
        before the failure. A green build failed in review keeps its 0 here,
        visible rather than inferred."""
        return _earned_code(self.code, self.audit)


def _failed_moment(code: int, audit: Sequence[AuditEntry]) -> str | None:
    """The moment the final non-zero verdict came from — the derivation both
    record shapes (step `Result`, task row) share."""
    if code == 0 or not audit:
        return None
    for entry in reversed(audit):
        if entry.code is not None:
            return entry.moment
    return None


def _earned_code(code: int, audit: Sequence[AuditEntry]) -> int | None:
    """The code carried when the failing moment began; None on success or
    when nothing code-bearing came before the failure."""
    if _failed_moment(code, audit) is None:
        return None
    coded = [entry for entry in audit if entry.code is not None]
    return coded[-2].code if len(coded) >= 2 else None


class ResultView:
    """A step's record, in the review window: the draft a `pre_record`
    reviewer receives after the work ran and before the record is sealed.

    The verdict is still open — `title` and `code` are plain writable
    attributes, and the raise-on-nonzero decision reads the code the review
    leaves behind, so "fail by this tool's definition of failure" needs no
    `nofail=` at the call site. Everything the run *captured* is read-only:
    review sees what the run kept, never edits it — an uncaptured
    (`capture=False`) call reviews the code alone. `ok` derives from `code`,
    so the two can never disagree.
    """

    __slots__ = (
        "_code",
        "_command",
        "_duration",
        "_raw",
        "_returned",
        "_stderr",
        "_stdout",
        "_title",
        "_touched",
    )

    def __init__(
        self,
        *,
        title: str,
        code: int,
        stdout: str,
        stderr: str,
        duration: float,
        raw: str,
        command: str,
        returned: Any = None,
    ) -> None:
        self._title = title
        self._code = code
        self._stdout = stdout
        self._stderr = stderr
        self._duration = duration
        self._raw = raw
        self._command = command
        self._returned = returned
        self._touched: set[str] = set()

    @property
    def title(self) -> str:
        """The receipt's label. Starts as the shown command (or the call's
        `title=`); what the review leaves here is what the report shows."""
        return self._title

    @title.setter
    def title(self, value: str) -> None:
        self._title = value
        self._touched.add("title")

    @property
    def code(self) -> int:
        """The verdict. What the review leaves here is the step's exit code —
        the raise decision and the receipt both read it. The audit records
        whether a reviewer wrote it or left it alone."""
        return self._code

    @code.setter
    def code(self, value: int) -> None:
        self._code = value
        self._touched.add("code")

    @property
    def ok(self) -> bool:
        """Whether the code, as it stands, is success — derives from `code`."""
        return self.code == 0

    @property
    def stdout(self) -> str:
        """Captured standard output; empty when the call streamed instead."""
        return self._stdout

    @property
    def stderr(self) -> str:
        """Captured standard error; empty when the call streamed instead."""
        return self._stderr

    @property
    def output(self) -> str:
        """`stdout` then `stderr`, concatenated — the same convenience the
        sealed `Result` offers."""
        return self._stdout + self._stderr

    @property
    def duration(self) -> float:
        """Wall-clock seconds the work took — machinery-owned, read-only."""
        return self._duration

    @property
    def raw(self) -> str:
        """The exact command line executed — the review can read what really
        ran while retitling what the report shows."""
        return self._raw

    @property
    def command(self) -> str:
        """The normalised command line as it stood before review."""
        return self._command

    @property
    def returned(self) -> Any:
        """What the body returned, when this draft is a task's row — None by
        circumstance elsewhere (a subprocess step has no return value)."""
        return self._returned

    def set_returned(self, value: Any) -> None:
        """Rewrite the *reported* value — the summary line and the `--json`
        envelope — never what a dependent or a body caller received: that was
        snapshotted the moment the body handed it over. The review window is
        this write's home: reviewed, attributed in the audit, before the
        record seals."""
        self._returned = value
        self._touched.add("returned")

    def _fill(self, stdout: str, stderr: str, duration: float) -> None:
        """Machinery-side: fill the captured streams and timing once the
        work concluded — a generator item held its draft *during* the work,
        before these were knowable. Never a review write."""
        self._stdout = stdout
        self._stderr = stderr
        self._duration = duration


@dataclass
class Context:
    """State for one running task: environment, flags, passthrough, output."""

    address: str = ""
    """This context's place in the run's tree — the path of requests that
    led here. Every record made under it derives its own address from this
    one."""
    _labels: dict[str, int] = field(default_factory=dict)
    """Per-parent label counts, so same-labelled children get ordinals in
    request order as written (the first is bare, the second is `#2`)."""

    notes: list[Any] = field(default_factory=list)
    """The notes this task fired (`_notes.Note` records) — what the level
    system resolved, where each came from, and what the boundary verdict
    and the `--json` envelope read. A task's own, never inherited."""

    env: dict[str, str] = field(default_factory=lambda: _globals.base_env())
    """**This task's environment** — a complete one, not a diff.

    Starts as a copy of the run's pinned environment, and is what `os.environ`
    answers from inside the task, what every child footman spawns receives,
    and what `run(env=…)` replaces. Because it is a whole value rather than an
    overlay, `del os.environ["FOO"]` is ordinary: it removes the key from this
    task's environment and from the children it goes on to spawn, while a
    sibling's copy is untouched."""
    cwd: Path | None = None
    """Where `run()` executes — resolved once per task by the policy ladder
    (`.opts(cwd=)` / `@task(cwd=)` / config `cwd`, default `taskfile`), so it
    is concrete inside a run. A preset value (tests, `use_context`) wins.
    `None` means unresolved (plain calls outside a footman run)."""
    cwd_policy: str = ""
    """`[tool.footman] cwd` — the run-wide default policy token (or absolute
    path) the ladder bottoms out on. Empty means `taskfile`."""
    root_dir: str = ""
    """The `root` token's target: the highest cascade task file's directory
    (`files[0].parent`), pinned by discovery. Empty outside a real run."""
    invoked_dir: str = ""
    """The `asinvoked` token's target: the process cwd where `fm` was
    launched, pinned as a snapshot at startup."""
    cwd_unmanaged: bool = False
    """`cwd="unmanaged"`: footman stays out — subprocesses spawn with
    `cwd=None` (inherit the live process cwd) even though `ctx.cwd` records
    the process cwd at task start for the body to read."""
    serial_active: bool = False
    """This task holds (or inherited, through a fan-out) the serial or
    exclusive lane: it owns the real process globals — the environ router,
    the Popen injection, and the os guards all pass through. Set by the
    executor around a `serial=`/`exclusive=` body; `parallel()` children
    inherit it, because a lineage extends a hold."""
    dry_run: bool = False
    """`--dry-run`: `run()` prints and records the command, executes
    nothing, and reports success."""
    quiet: bool = False
    """`--quiet`: suppress step lines and the per-task summary."""
    verbose: bool = False
    """`--verbose`: replay captured `run()` output even on success."""
    no_color: bool = False
    """`--no-color` / `--color=never` (or `NO_COLOR`): never emit ANSI styling."""
    force_color: bool = False
    """`--color=always` (or `FORCE_COLOR`): colour even when output is not a
    terminal — so `run()` forces the tools it spawns to colour and the shown
    command line paints, for a pipe into `less -R`. Gated off under capture
    (`--json`), where ANSI would corrupt the envelope. Never sets the live
    cursor affordances `tty` governs — those still need a real terminal."""
    machine_read: bool = False
    """`--json`: the envelope is the report. footman's own receipt chrome —
    the step line, the replayed output block, the audit trail — is left out
    of the buffer, because every one of those already has a field on the
    step's own row. Task *output* still lands there: a body's prints are
    what the buffer is for."""
    prog: str = "fm"
    """The invoking CLI's command name — a branded CLI's own `prog`, so
    tasks (the taskdocs plugin, say) can speak the brand's name."""
    sequential: bool = False
    """The *user asked* for one-at-a-time (`-s` or config) — `parallel()`
    honours it too. Deliberately not set by the scheduler's own
    single-node routing, which is presentation, not a request to
    serialise task bodies."""
    assume_yes: bool = False
    """`--yes`: every `confirm()` gate auto-answers yes, for CI and scripts."""
    no_input: bool = False
    """`--no-input`: never prompt — a required prompt errors instead of
    asking, so an unattended run fails loudly rather than hanging."""
    fetch_backend: str = ""
    """`[fetch] backend` from the config ladder — which engine `fetch()`
    downloads with. Empty means the default (stdlib urllib)."""
    shell_default: str = ""
    """`[shell] default` from the config ladder — what `run(shell=True)` resolves
    to. Empty means `posix` (a POSIX shell everywhere: bash, then sh)."""
    jobs: int = 0
    """The effective parallel width (`-j/--jobs`, config `jobs`, or the
    cores-minus-one default) — caps `parallel()` pools in task bodies.
    `0` means unset (plain calls outside a run): no cap."""
    task: str = ""
    """Who is running, for the step lines' name column: the scheduler
    sets the dotted task name, `parallel()` its child's name. Empty
    outside runs."""
    fn: Any = None
    """The running task's own function — what `inherited()` reads to find
    the task this one shadows. `None` outside a run."""
    given: frozenset[str] = frozenset()
    """The parameter names *the caller supplied* — named on the command line,
    passed as a keyword by a body call, or answered at an `ask()` prompt. What
    the `given()` function reads.

    Its complement is everything footman inferred rather than was told: an
    `env()` fallback, a `default(fn)`, the declared default. That split is the
    whole point — a value and the fact that someone asked for it are two
    different things, and only the second can distinguish `--profile` (write
    the default file) from no `--profile` at all (write nothing).

    Empty outside a run, and never inherited: a child task's set is its own,
    stamped where its context is built."""
    name_width: int = 0
    """The widest sibling task name, so step-line columns align."""
    passthrough: list[str] = field(default_factory=list)
    """Everything after `--` on the command line, verbatim."""
    tty: bool = False
    """Output dresses for a terminal (colour, marks). Live in-place
    rewrites additionally require output to be uncaptured."""
    terminal: bool = False
    """The run's real stdout is a terminal (on Windows, one that interprets
    ANSI) and output is not captured into a `--json` envelope — `tty` with
    the colour policy left out. What the `tty()` reader answers: `NO_COLOR`
    turns `tty` off because dressed output would be wrong, but the person
    is still there."""
    sink: TextIO | None = None
    """Where this task's stdout goes: a capture buffer in buffered
    (parallel) mode, `None` for the real stdout (live mode)."""
    err_sink: TextIO | None = None
    """Where this task's stderr goes. At task level it is the *same* buffer as
    `sink` (so the atomic parallel flush keeps stdout/stderr in order); a
    `run()` capturing an in-process callable temporarily points the two at
    separate buffers to split the step's streams for its `Result`."""
    interactive: bool = False
    """`@task(interactive=True)`: the task owns the real terminal — output is
    not captured and it holds sole stdio, so its body may prompt or run a
    REPL. Mid-body `prompt()`/`confirm()`/`select()` are allowed only here."""
    atomic: bool = False
    """`@task(atomic=True)`: this task's subprocesses opt out of fail-fast's
    kill — they run to completion so a mid-write can't be truncated. Total
    on purpose: the child is unregistered and not group-isolated, so a stop
    signal to footman's own pid (`timeout`, `docker stop`) ends the run and
    leaves the child to finish; a terminal's Ctrl-C reaches it only because
    the kernel signals the whole foreground group itself."""
    keep_going: bool = False
    """This task's resolved (per-subtree) failure policy, tagged onto the
    subprocesses it spawns so a fail-fast failure elsewhere reaps only the
    fail-fast trees in a mixed run, sparing a keep-going task's."""
    deadline: float | None = None
    """`@task(timeout=…)`, resolved to a `time.perf_counter()` instant when
    the body starts — an instant rather than a duration because everything
    downstream asks "how long is left?", and a duration would have to be
    re-based at each hand-off.

    It is the fail-fast event scoped to one task: past it, no new work
    starts, in-flight subprocess trees are terminated, and generator steps
    unwind at their next checkpoint. Inherited by the steps and `run()`
    calls a body makes, so a subprocess is bounded by whatever the task has
    left rather than by its own timeout alone."""
    timeout_declared: float | None = None
    """The seconds the author wrote in `@task(timeout=…)`, kept beside the
    resolved `deadline` so a failure line can name the declared number
    rather than the remainder that happened to be left when a call
    started."""
    shared: bool = True
    """Whether an execution the run has already performed may satisfy this
    request. `False` means it gets its own run, and so does everything it asks
    for, unless that task declares its own answer. Resolved per node by the
    scheduler and per call by the cell layer — the sharing twin of
    `keep_going`'s per-subtree policy."""
    in_task: bool = False
    """True while a task *body* runs (the scheduler sets it around the call),
    so the interactive primitives tell a guarded mid-body call from the
    framework's own up-front `ask()` resolution."""
    unit_pending: bool = False
    """A live-progress unit is already counted for this call, and unclaimed:
    `parallel()` counts every child it is handed, then the first task request
    inside that child *claims* the unit instead of counting a second one. A
    thunk that runs no task keeps it; a thunk that is only a wrapper around
    one call (the `lambda: build("web")` spelling) shows as the single piece
    of work it is. Cleared on claim, so the requests after it — and anything
    the callee itself asks for — count their own."""
    answers: Any = None
    """The scripted answers a `recording(answers=…)` block carries — the
    normalised table, shared by reference with every child context so an
    ordered sequence is consumed in one place. `None` outside a scripted
    recording; a raw mapping handed to `Context(answers=…)` is normalised
    on first use."""
    steps: list[Result] = field(default_factory=list)
    """Every `run()` this task made, in order — what `recording()` and
    the `--json` envelope read."""
    sections: list[Section] = field(default_factory=list)
    """Task-authored profiling: what `section()`, `stream()` and `mark()`
    recorded while this task ran, on the run's clock. Rides the task's
    result row; a `parallel()` child's records fold into its requester's,
    the way `steps` do."""

    def time_left(self) -> float | None:
        """Seconds until this task's deadline, or `None` when it has none.

        Clamped at zero rather than going negative: callers hand it to
        `subprocess.communicate(timeout=…)` and to their own comparisons,
        and a negative bound reads as "no bound" to some of them. Zero means
        the deadline has passed and the caller should not start the work.
        """
        if self.deadline is None:
            return None
        return max(0.0, self.deadline - time.perf_counter())

    def overdue(self) -> bool:
        """Has this task's deadline passed? False when there is no deadline."""
        return self.deadline is not None and time.perf_counter() > self.deadline

    def child(self, label: str, **overrides: Any) -> Context:
        """One birth for every child context — a body callee's, a
        `parallel()` child's — so the sites can never drift on which fields
        reset.

        Identity is fresh: the address and sibling labels, the step and
        section records, the unit flag. The environment is a COPY — a
        child's writes are its own, which is the router's "children see it,
        siblings don't" made structural. Everything else inherits until
        *overrides* says otherwise. `given` deliberately inherits here: the
        futures layer stamps the callee's own set where binding computes
        it, and a step child has no binding to compute one.
        """
        born = replace(
            self,
            address=_child_address(self, label),
            _labels={},
            env=dict(self.env),
            steps=[],
            sections=[],
            notes=[],
            task=label,
            unit_pending=False,
        )
        for name, value in overrides.items():
            setattr(born, name, value)
        return born


_current: ContextVar[Context | None] = ContextVar("footman_context", default=None)


def current() -> Context:
    """The context of the running task (a fresh default one outside a run)."""
    ctx = _current.get()
    return ctx if ctx is not None else Context()


_WALL_ANCHOR: tuple[float, float] = (time.time(), time.perf_counter())
"""One sampling of both clocks, taken together, so a wall-clock moment maps
onto the run clock every record in this module keeps: a retroactive
`Stream.section(start=…, end=…)` window lands beside spans that were stamped
live. Module-level on purpose — `perf_counter`'s origin is arbitrary but
process-wide, so one anchor serves every run in the process."""


@dataclass(frozen=True)
class Section:
    """One recorded interval of a task's time — what the `--json` envelope
    carries and a profile renders. `stream` is `""` for the task's own
    timeline (these nest and never overlap, by construction); a named stream
    is its own parallel timeline, where overlap is legal. A `mark()` is a
    section with no duration."""

    name: str
    started: float  # the run's monotonic clock, same as a task row's
    duration: float  # seconds; 0.0 is an instant (a mark)
    stream: str = ""


class _SectionTimer:
    """The context manager `section()` returns: entry stamps the clock, exit
    records — a body that raised still spent the time, so the record is made
    in `__exit__` unconditionally."""

    __slots__ = ("_begin", "_ctx", "name", "stream")

    _ctx: Context
    name: str
    stream: str
    _begin: float

    def __init__(self, ctx: Context, name: str, stream: str) -> None:
        self._ctx = ctx
        self.name = name
        self.stream = stream
        self._begin = 0.0

    def __enter__(self) -> _SectionTimer:
        self._begin = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> None:
        self._ctx.sections.append(
            Section(
                self.name, self._begin, time.perf_counter() - self._begin, self.stream
            )
        )


def _profiled_ctx(where: str) -> Context:
    ctx = _current.get()
    if ctx is None or not ctx.in_task:
        raise RuntimeError(
            f"{where} records timing on the *running task*, so it belongs "
            f"inside a task body, during a run. From a helper thread, make "
            f"the stream in the body and hand the handle over — the handle "
            f"remembers its task."
        )
    return ctx


def _run_clock(moment: datetime | float, arg: str) -> float:
    """A wall-clock moment (datetime, or epoch seconds) on the run clock."""
    if isinstance(moment, datetime):
        wall = moment.timestamp()  # naive reads as local time, aware as itself
    elif isinstance(moment, (int, float)) and not isinstance(moment, bool):
        wall = float(moment)
    else:
        raise TypeError(
            f"Stream.section({arg}=…) takes a datetime or epoch seconds "
            f"(time.time()), got {type(moment).__name__}"
        )
    anchor_wall, anchor_clock = _WALL_ANCHOR
    return anchor_clock + (wall - anchor_wall)


def section(name: str) -> _SectionTimer:
    """Time a section of the running task — `with footman.section("resolve"):`.

    Records onto the task's own timeline, subdividing its span; nested
    blocks nest. For work that overlaps — several waits in flight at once —
    use `stream()`, where overlap is legal."""
    return _SectionTimer(_profiled_ctx("section()"), name, "")


def mark(name: str) -> None:
    """Record an instant on the running task's timeline — a moment worth a
    label, no duration."""
    ctx = _profiled_ctx("mark()")
    ctx.sections.append(Section(name, time.perf_counter(), 0.0))


class Stream:
    """A named parallel timeline of sections under the running task.

    Made in the task body (`ci = footman.stream("ci")`); the handle remembers
    its task, so a helper thread the body spawned may record through it.
    Sections on a stream may overlap — that is what a stream is for."""

    __slots__ = ("_ctx", "name")

    _ctx: Context
    name: str

    def __init__(self, ctx: Context, name: str) -> None:
        self._ctx = ctx
        self.name = name

    @overload
    def section(self, name: str) -> _SectionTimer: ...
    @overload
    def section(
        self, name: str, *, start: datetime | float, end: datetime | float
    ) -> None: ...
    def section(
        self,
        name: str,
        *,
        start: datetime | float | None = None,
        end: datetime | float | None = None,
    ) -> _SectionTimer | None:
        """A section on this stream: bracketing (`with ci.section("poll"):`)
        or retroactive — `ci.section("build", start=t0, end=t1)` records a
        window learned after the fact (a CI check's real run, reported by
        its API), placed by wall clock."""
        if (start is None) != (end is None):
            raise ValueError(
                "Stream.section(): give both start= and end=, or neither — "
                "half a window places nothing"
            )
        if start is None:
            return _SectionTimer(self._ctx, name, self.name)
        assert end is not None
        begin = _run_clock(start, "start")
        finish = _run_clock(end, "end")
        if finish < begin:
            raise ValueError(
                f"Stream.section({name!r}): end is before start — the window "
                f"is {begin - finish:.3f}s inside out"
            )
        self._ctx.sections.append(Section(name, begin, finish - begin, self.name))
        return None


def stream(name: str) -> Stream:
    """A named parallel timeline under the running task — see `Stream`."""
    if not name:
        raise ValueError(
            "stream(''): the empty name is the task's own timeline — "
            "sections land there via footman.section()"
        )
    return Stream(_profiled_ctx("stream()"), name)


@contextlib.contextmanager
def chdir(
    target: str | Path | None = None, *, rel: str | Path | None = None
) -> Generator[None]:
    """Really change the process directory — inside a serial/exclusive task.

    The sugar for bodies that own the globals: the default target is the
    task's own `ctx.cwd`; arguments follow the marker grammar exactly — a
    policy token (`root`, `taskfile`, `asinvoked`) or an absolute path, with
    `rel=` for a relative suffix; a bare relative path is the same taught
    error the markers give. Performs a real `os.chdir`, keeps `ctx.cwd` in
    sync (so a nested `run()` roots where the block does), restores both on
    exit. In a parallel task it is a taught error — the cwd belongs to no
    one there.
    """
    ctx = current()
    if ctx.in_task and not ctx.serial_active:
        raise RuntimeError(
            f"task {ctx.task or '?'} calls footman.chdir() in a parallel "
            f"task — the process directory belongs to no one there. The "
            f"ladder: build paths from footman.cwd() (no chdir at all); or "
            f"claim lanes=(cwd_lane,) if the real directory only needs to "
            f"*be* the task's cwd for the duration; or mark the task serial "
            f"(or exclusive) to own the real globals — footman.chdir() is "
            f"legal there."
        )
    base: Path
    if target is None:
        base = ctx.cwd if ctx.cwd is not None else Path(_globals.real_getcwd())
    elif isinstance(target, str) and target == "root" and ctx.root_dir:
        base = Path(ctx.root_dir)
    elif isinstance(target, str) and target == "asinvoked" and ctx.invoked_dir:
        base = Path(ctx.invoked_dir)
    elif isinstance(target, str) and target == "taskfile":
        from livery.footman._discover import defining_dir

        home = defining_dir(ctx.fn) if ctx.fn is not None else None
        base = Path(home) if home else Path(_globals.real_getcwd())
    elif isinstance(target, str) and target == "unmanaged":
        raise TypeError("chdir(cwd='unmanaged') has no directory to change to")
    else:
        base = Path(target)
        if not base.is_absolute():
            raise TypeError(
                f"chdir({str(target)!r}) is relative — chdir takes a policy "
                f"token or an absolute path; a relative suffix goes in rel=…"
            )
    if rel is not None:
        rel_suffix = Path(rel)
        if rel_suffix.is_absolute() or rel_suffix.anchor:
            raise TypeError(
                f"rel={str(rel)!r} is absolute — rel is a suffix appended "
                f"to the resolved base; an absolute directory goes first."
            )
        base = base / rel
    saved_fs, saved_ctx = _globals.real_getcwd(), ctx.cwd
    _globals.real_chdir(base)
    ctx.cwd = base
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            _globals.real_chdir(saved_fs)
        ctx.cwd = saved_ctx


def cache_dir() -> Path:
    """This CLI's cache directory, created if it isn't there yet.

    Derived data, safe to delete: footman's collector sweeps this folder by
    age, so put nothing here you would mind losing.

    ```python
    (footman.cache_dir() / "index.json").write_text(payload)
    ```

    Where it lands is the branded CLI's business — its own folder, or
    `~/.cache/<name>` by default. A task asks for the kind of folder it
    wants and gets one that exists.
    """
    from livery.footman import _paths

    return _make(_paths.footman_cache_dir())


def data_dir() -> Path:
    """This CLI's data directory, created if it isn't there yet.

    Durable and machine-local — credentials, tokens, generated assets. The
    collector never touches it, which is the whole difference from
    `cache_dir()`. Created owner-only (`0o700`), like `~/.ssh`, because
    credentials are exactly what it is documented to hold.

    ```python
    (footman.data_dir() / "credentials.json").write_text(token)
    ```
    """
    from livery.footman import _paths

    return _make(_paths.footman_data_dir(), mode=0o700)


def prog() -> str:
    """The command this CLI is invoked as — `fm`, or a branded CLI's own.

    For output that has to *call the CLI back*: a generated CI workflow, a
    README line, a task that writes prose naming the command someone will
    type. A branded runner must emit its own name, or the file it writes
    tells the reader to run a command they do not have.

    ```python
    (root / ".github/workflows/ci.yml").write_text(
        f"      - run: {footman.prog()} check\\n"
    )
    ```

    Inside a task body, prefer `ctx.prog` — declare a `ctx: Context`
    parameter and read it. That is the invocation's own answer rather than
    process state, and it is what footman's own taskdocs plugin uses. This
    accessor exists for the code that never receives a context: a plugin
    building its tree at mount time, a helper called from anywhere.
    """
    from livery.footman import _paths

    return _paths.prog()


def dist() -> str | None:
    """The distribution that ships this CLI, or `None` if it never said.

    The name someone would `pip install` — what the uv handoffs reason
    about, and what `self.install` puts on a PATH. `App(dist=…)` sets it;
    stock footman answers `"footman"`.

    `None` is a real answer, not a gap: declaring a distribution is opt-in
    for a branded CLI, and saying `"footman"` for a brand that never
    declared one would name the wrong package.
    """
    from livery.footman import _paths

    return _paths.declared_dist()


def config_dir() -> Path:
    """This CLI's config directory — *your* writing, never footman's.

    The user-level config file and the user-level tasks file live here and
    travel together. Unlike `cache_dir()` and `data_dir()` this one is
    **not** created on demand: an empty config directory says something
    different from a missing one, and inventing it would be footman
    writing where only you should.
    """
    from livery.footman import _paths

    return _paths.footman_config_dir()


def config_file() -> Path:
    """The user-level config file — `~/.config/<brand>/config.toml` by
    default. May not exist; a runner with no config is the ordinary case."""
    from livery.footman import _paths

    return _paths.footman_config_file()


def user_tasks_file() -> Path:
    """The user-level tasks file: the cascade's outermost writable rung,
    riding every project. May not exist."""
    from livery.footman import _paths

    return _paths.user_tasks_file(_paths.tasks_file_name())


def project_root() -> Path | None:
    """The top of this invocation's task cascade, or `None` outside a
    project.

    The same answer `--json` reports and the `root` cwd policy resolves
    against — never a guess from the current directory, which may be
    anywhere beneath it.
    """
    root = current().root_dir
    return Path(root) if root else None


def _make(path: Path, mode: int | None = None) -> Path:
    """Return *path*, having made sure it exists.

    The accessors create rather than merely resolve: every caller would
    otherwise write the same `mkdir`, and forgetting it fails at the write
    rather than here. `_paths` itself stays resolution-only — it is on the
    completion hot path, which must not touch the disk.

    *mode* hardens the leaf at creation time only: a directory that already
    exists keeps whatever permissions its owner gave it, parents follow the
    umask as usual, and Windows ignores the bits.
    """
    if mode is None:
        path.mkdir(parents=True, exist_ok=True)
    else:
        path.mkdir(mode=mode, parents=True, exist_ok=True)
    return path


def cwd() -> Path:
    """The current task's working directory, always concrete.

    `ctx.cwd` as the policy ladder resolved it — the blessed base for a task
    body's own path arithmetic (`footman.cwd() / "dist"`), instead of
    relative paths against the process cwd, which belongs to no one in a
    parallel run. Outside a run it is simply the process cwd.
    """
    resolved = current().cwd
    return resolved if resolved is not None else Path.cwd()


@contextlib.contextmanager
def use_context(ctx: Context | None = None) -> Generator[Context]:
    """Install *ctx* as the current run context for the duration of the block.

    The public seam for calling tasks from other Python code — tests included:
    `run()` and hosted toolroom calls inside the block read this context
    instead of a fresh default. `footman.testing.recording` builds on it.

    ```python
    with use_context(Context(env={"CI": "1"})) as ctx:
        deploy()
    assert ctx.steps[0].code == 0
    ```
    """
    installed = ctx if ctx is not None else Context()
    token = _current.set(installed)
    try:
        yield installed
    finally:
        _current.reset(token)


def passthrough() -> list[str]:
    """Arguments after `--` on the command line, for the running task."""
    return list(current().passthrough)


def given(name: str) -> bool:
    """Whether the caller supplied *name*, as opposed to footman inferring it.

    ```python
    @task
    def build(*, profile: Path = Path("build-profile.json")):
        if given("profile"):   # `fm build --profile` writes the default file;
            trace_to(profile)  # `fm build` writes nothing at all
    ```

    True when the parameter was named on the command line (with or without a
    value — `--profile` alone means its default, and means it on purpose),
    passed as a keyword by a body call, or answered at an `ask()` prompt. False
    when the value came from an `env()` fallback, a `default(fn)`, or the
    declared default, because nobody asked for those.

    That distinction is the only way to tell "the default one, please" from "no
    opinion", which a value alone cannot express: both hand you the same path.

    *name* is the **parameter** name, not its flag spelling — `dry_run`, not
    `--dry-run`. Naming something the running task has no parameter for is an
    error rather than a silent `False`, since a typo would otherwise read as a
    perfectly ordinary "not given".
    """
    ctx = _current.get()
    if ctx is None or not ctx.in_task or ctx.fn is None:
        raise RuntimeError(
            f"given({name!r}) has no answer here — presence is decided when a "
            f"task's arguments are bound, so call it inside a task body during "
            f"a run"
        )
    from livery.footman import _manifest

    known = {
        p.name
        for p in _manifest.call_signature(ctx.fn).parameters.values()
        if p.kind is not inspect.Parameter.VAR_KEYWORD
    }
    if name not in known:
        listed = ", ".join(sorted(known)) or "(none)"
        dashed = name.replace("-", "_")
        hint = (
            f" — did you mean {dashed!r}?" if dashed != name and dashed in known else ""
        )
        raise ValueError(
            f"given({name!r}): {ctx.task or 'this task'} has no parameter "
            f"{name!r}{hint} (it has: {listed})"
        )
    return name in ctx.given


def attended() -> bool:
    """Whether a prompt would reach a human: stdin is a real terminal and the
    run was not declared unattended (`--no-input`, `--dry-run`).

    The question `prompt()`/`confirm()`/`select()` already answer for
    themselves by degrading to their defaults — `attended()` is for changing
    the *shape* of a run instead: skip an optional wizard outright, take the
    quiet path in CI. `--yes` deliberately does not count as unattended: it
    auto-answers `confirm()` gates but forbids nothing.

    Readable anywhere, but it licenses nothing: a mid-body prompt still
    requires `@task(interactive=True)`, because owning the terminal under
    parallelism is a separate question from someone being there to answer.
    """
    ctx = current()
    return not (ctx.no_input or ctx.dry_run) and _stdin_is_tty()


def tty() -> bool:
    """Whether this run's output lands on a live terminal, colour policy
    aside: the real stdout is a terminal (on Windows, one that interprets
    ANSI) and output is not captured into a `--json` envelope. `NO_COLOR` and
    `--color` do not change the answer — a person with colour turned off is
    still watching, so gate pagers and live displays here and colour on
    `colored()`. `False` outside a run: the scheduler stamps it per task."""
    return current().terminal


def colored() -> bool:
    """Whether this task's own output should dress for colour — the same
    `never`/`always`/`auto` cascade footman's own chrome follows:
    `--color=never` (or `NO_COLOR`, or a dumb terminal) says no,
    `--color=always` forces colour even into a pipe (but never into a
    `--json` envelope), and `auto` follows the terminal."""
    return _colored(current())


def progress(done: int, total: int = 0) -> None:
    """Report this task's own progress: *done* of *total* units.

    Some work knows exactly how far along it is — 23 of 150 migrations,
    bytes of a download — and that is better evidence than any duration
    history. A reporting task's counts drive the live bar directly
    (counted beats estimated), so the bar is honest on the very first
    run, where the estimator would still be guessing.

    ```python
    @task
    def migrate():
        for i, record in enumerate(records, 1):
            apply(record)
            progress(i, len(records))
    ```

    A `total` of 0 (or less) clears the report, returning the run to its
    estimate. Outside a run, or with no live status line, this is a
    no-op — plain calls and captured runs cost nothing.
    """
    status = active_status()
    if status is None:
        return
    ctx = current()
    name = ctx.task or "task"
    if total > 0:
        status.unit_counted(name, max(done, 0), total)
    else:  # a cleared report: back to the estimate
        with contextlib.suppress(Exception):
            status.counted.pop(name, None)


def track(iterable: Iterable[_T], total: int | None = None) -> Iterator[_T]:
    """Iterate *iterable*, reporting progress as it goes.

    The ergonomic form of `progress()`: the total comes from `len()` when
    the iterable has one, or from *total* when you know it for a
    generator. Without either, iteration still works — the run simply
    keeps whatever progress it had.

    ```python
    @task
    def migrate():
        for record in track(load_records()):
            apply(record)
    ```
    """
    if total is None:
        total = len(iterable) if isinstance(iterable, Sized) else 0
    done = 0
    try:
        for item in iterable:
            yield item
            done += 1
            if total:
                progress(done, total)
    finally:
        if total:  # leaving early (a break, an exception) resets the report
            progress(0, 0)


def inherited() -> Callable[..., Any]:
    """The task this one shadows in the cascade — footman's `super()`.

    A nearer `tasks.py` overriding a task by name usually wants to *extend*
    it, not replace it. Call this inside the overriding task's body to get
    the task it shadows, then call that like the plain function it is:

    ```python
    # svc/api/tasks.py — the root also defines `check`
    @task
    def check(fix: bool = False, contracts: bool = True):
        inherited()(fix=fix)          # arguments are forwarded explicitly
        if contracts:
            run("./verify-contracts.sh")
    ```

    Forwarding is deliberately manual: the two signatures are independent
    (a leaf usually adds a parameter), so automatic forwarding could only
    drop arguments silently or fail at run time — where spelling the call
    out shows you the mismatch as you type it. Being an ordinary call,
    it also runs to completion
    before the next statement — and composes with `parallel(inherited(),
    extra)` when you want otherwise.

    `fm --where <task>` lists the whole shadow chain; `fm --help <task>`
    shows the inherited task's options, so you can read the forwarding
    call straight off it.
    """
    from livery.footman import _discover

    fn = current().fn
    if fn is None:
        raise RuntimeError(
            "inherited() works inside a running task — footman resolves the "
            "task being shadowed from the one currently running"
        )
    previous = _discover.shadowed(fn)
    if previous is None:
        name = current().task or getattr(fn, "__name__", "this task")
        raise RuntimeError(
            f"{name} does not shadow an inherited task — nothing above it in "
            f"the cascade defines that name (fm --where {name} lists the chain)"
        )

    @functools.wraps(previous)
    def call_inherited(*args: Any, **kwargs: Any) -> Any:
        # Point the context at the task being called, so an `inherited()`
        # inside *it* walks one level further up instead of resolving to
        # itself — a three-deep cascade would otherwise recurse forever.
        from livery.footman import registry

        ctx = current()
        saved = ctx.fn
        ctx.fn = previous
        try:
            # The shadowed *body*, run inside this task: an override chain, the
            # way `super()` is — not a second task. It shares this task's
            # context, result, and reported duration, and never becomes a unit
            # of the run in its own right.
            return registry.task_body(previous)(*args, **kwargs)
        finally:
            ctx.fn = saved

    return call_inherited


class RunFailed(Exception):
    """A `run()` command exited non-zero (and `nofail` was not set).

    The message names the command the way every other surface does — with
    any `Secret` argument redacted — because a failure line is a display,
    and the one place it most reliably lands is somebody's CI log. The
    record it carries, `.result`, is untouched: `.result.command` is the
    real command line, for the handler that reads rather than prints."""

    def __init__(self, result: Result) -> None:
        self.result = result
        super().__init__(f"`{result.shown}` exited with code {result.code}")


class RunTimeout(RunFailed):
    """A `run(timeout=…)` expired and the process tree was killed.

    A `RunFailed`, so every `except RunFailed` keeps catching it; catch this
    to tell a hang from an ordinary non-zero exit — the distinction a version
    or help probe branches on."""

    def __init__(self, result: Result, timeout: float) -> None:
        self.result = result
        self.timeout = timeout
        Exception.__init__(
            self, f"`{result.shown}` timed out after {timeout:g}s and was killed"
        )


class CommandNotFound(FileNotFoundError):
    """The executable for a `run()` does not exist — nothing was spawned.

    A `FileNotFoundError`, so an existing `except FileNotFoundError` keeps
    catching it. Deliberately *not* a `RunFailed`, and not silenced by
    `nofail=True`: there is no exit code to interpret, because no command
    ran — the environment is missing the tool, or the name is misspelled
    (which a toolroom handle cannot catch at attribute time: it mints a
    Tool for any spelling). Carries `.command`."""

    def __init__(self, command: str) -> None:
        self.command = command
        super().__init__(
            f"no executable {command!r} found on PATH — the tool is not "
            f"installed here, or the name is misspelled. Install it, or gate "
            f"the task with @footman.requires_tool({command!r}, reason='…') "
            f"so it lists as unavailable instead of failing mid-run."
        )


class Failed(Exception):
    """A task chose to fail — the exception `footman.fail()` raises.

    A *deliberate* stop with a reason (and optional exit code): the user-facing
    sibling of `RunFailed` (which is a *command*'s failure). Carries `.reason`
    and `.code`; footman renders the reason verbatim — no type prefix — in the
    failure line and the `--json` `error` field. Exported so a task can
    `except footman.Failed:`, but `fail()` is the blessed way to raise it.
    """

    def __init__(self, reason: str = "", *, code: int = 1) -> None:
        self.reason = reason
        self.code = code
        super().__init__(reason)


def effective_timeout(own: float | None, ctx: Context) -> float:
    """The bound that actually applied to a call, for its error message.

    A call may carry its own `timeout=`, or inherit whatever the enclosing
    `@task(timeout=…)` had left. Reporting the call's own would print `0s`
    for a task-imposed deadline, which reads as a broken timeout rather than
    an enforced one. A derived bound is rounded: it is a remainder computed
    to the microsecond, and `0.399666s` in a failure line is noise where
    `0.4s` is the number the author wrote.
    """
    if own is not None:
        return own
    if ctx.timeout_declared is not None:
        return ctx.timeout_declared
    return 0.0


class TimedOut(Failed):
    """A task's `@task(timeout=…)` deadline expired.

    A `Failed`, so `except footman.Failed:` keeps catching it and the reason
    renders verbatim in the failure line and the `--json` error field.

    Carries `.timeout` (the declared seconds) and `.after` — what became of
    the work: `stopped` (footman issued a stop before the body returned) or
    `completed` (no stop was issued and the body finished on its own, just
    late). An open set: `TaskResult.after_deadline` explains which further
    values are designed and why they are not shipped.

    The message never pretends the work did not happen. Where the body
    completed, it says so and says what the body's own verdict was, then
    states that the deadline governs — the receipt tells the whole truth
    while the task still fails on its contract
    (notes/20260807-timeout-and-retry.md).
    """

    def __init__(
        self,
        task: str,
        timeout: float,
        *,
        after: str = "stopped",
        body: str = "",
        at: float = 0.0,
        not_retried: bool = False,
    ) -> None:
        self.timeout = timeout
        self.after = after
        self.not_retried = not_retried
        said = f"{task} exceeded its {timeout:g}s deadline"
        if after == "completed":
            # Report the outcome being discarded rather than presenting this
            # as work that never finished.
            when = f" at {at:g}s" if at else ""
            verdict = f" with {body}" if body else ""
            said += f"; the body completed{when}{verdict} — the deadline governs"
        else:
            said += " and was stopped"
        if not_retried:
            # The diagnosis the author needs is "this body has no
            # checkpoint", and it is only inferable if this reads
            # differently from ordinary retry exhaustion.
            said += " — not retried"
        super().__init__(said, code=124)


def coroutine_refusal(kind: str, fn: Any = None) -> Failed:
    """The taught refusal for an `async def` body footman would never run.

    Calling an `async def` builds a coroutine and executes none of it, so a
    body left like that reports success for work that never happened — the one
    receipt the design refuses to mint (docs/design.md, "No fiction"). footman
    runs no event loop on purpose, and the same page says why, so this names
    the way in rather than pretending to be one.

    The definition site is part of the message because this is a refusal about
    a *declaration*, not about anything the run did: the answer to "which one?"
    is a place in a file, and a chain across a cascade can hold a great many
    bodies. `unwrap` first, so a decorated body points at the `async def`
    somebody wrote rather than at the wrapper that returned its coroutine.

    One phrasing for both callers: tasks go through `_executor`, steps through
    `_step`'s pump, and two copies of a message drift.
    """
    where = ""
    if fn is not None:
        code = getattr(inspect.unwrap(fn), "__code__", None)
        if code is not None:
            where = f" at {code.co_filename}:{code.co_firstlineno}"
    return Failed(
        f"the {kind} body{where} is an `async def` and footman runs no event "
        f"loop, so calling it builds a coroutine and executes nothing. Make "
        f"it a plain `def` and drive the async work from inside it "
        f"(`asyncio.run(...)`)."
    )


def generator_refusal(kind: str, fn: Any = None, *, is_async: bool = False) -> Failed:
    """The taught refusal for a yielding body footman would never run.

    Calling a generator function builds the generator and executes none of
    its body, so a `@task` written with `yield` reported `ok` for work that
    never happened — the receipt the design refuses to mint. The shape is
    refused rather than pumped on purpose: `yield` on a *task* is reserved
    for the service form (a body that yields once it is ready and tears down
    after), so this refusal is the placeholder for that meaning, not a
    ruling against it. Steps already give `yield` a meaning, which is the
    way out the message names.

    An `async def` generator is the coroutine refusal's missed sibling — it
    is not a coroutine, so `iscoroutine` never sees it, and it is not a
    plain generator function either, so a step's pump never takes it. Both
    kinds refuse here, tasks and steps alike, with the async spelling named
    so the fix (drop the `async`) is visible in the message.

    Definition site over traceback, and `unwrap` first, for the same
    reasons as `coroutine_refusal` above; one phrasing for both callers, so
    two copies of a message cannot drift.
    """
    where = ""
    if fn is not None:
        code = getattr(inspect.unwrap(fn), "__code__", None)
        if code is not None:
            where = f" at {code.co_filename}:{code.co_firstlineno}"
    shape = "an `async def` generator" if is_async else "a generator function"
    if kind == "step":
        remedy = "A step yields from a plain `def` body: drop the `async`."
    else:
        remedy = (
            "Lift the yielding work into a `@step`, or build and return the "
            "iterator from a non-yielding body."
        )
    return Failed(
        f"the {kind} body{where} is {shape}, so calling it builds a "
        f"generator and executes nothing. footman does not run a yielding "
        f"{kind} body (the shape is reserved for services to come). {remedy}"
    )


def fail(reason: str = "", *, code: int = 1) -> NoReturn:
    """Fail the current task with a *reason* (and exit *code*, default 1).

    The blessed way to stop a task deliberately: `fail("no open PR to act on")`,
    or `fail("reserved branch", code=3)` to pick the exit code too. A *function*,
    not a `raise`, on purpose — a task lives in your repo under your linter, and
    `raise SomeError("a literal")` trips flake8-errmsg (EM101) and tryceratops
    (TRY003) at the call site, every failure. A call trips neither, the same
    reason `sys.exit()` and `pytest.fail()` are functions. `return N` still
    spells a bare code; `sys.exit(...)` still works — `fail()` is just the
    footman-native one, with a reason and a code together.

    `fail()` means failure, so `code=0` — success — is a contradiction it
    refuses rather than a corner it interprets: `return 0` (or a plain
    `return`) spells stopping early with success.
    """
    if code == 0:
        raise ValueError(
            "fail() means failure and exit code 0 is success — return 0 "
            "(or just return) spells stopping early with success"
        )
    raise Failed(reason, code=code)


def _is_deliberate_stop(err: BaseException) -> bool:
    """Whether *err* is a chosen stop with a message (`fail()`/`sys.exit("…")`)
    rather than a crash — so its reason renders verbatim, no type prefix."""
    return isinstance(err, (SystemExit, Failed))


def _was_expected(err: BaseException) -> bool:
    """Whether footman saw *err* coming — so no stack belongs with it.

    A chosen stop, and a command that exited non-zero or ran out of time: three
    outcomes the runner has words for, where a traceback would describe
    footman's own plumbing rather than anything the reader did. Everything else
    is an exception nobody planned — a bug in the tasks file — and the one
    question it raises is *where*, which is why those carry a location and,
    off a terminal, the stack.
    """
    return isinstance(err, (SystemExit, Failed, RunFailed, RunTimeout))


def context_param_name(sig: inspect.Signature) -> str | None:
    """Name of the task's context parameter (first param `ctx` / `Context`)."""
    params = list(sig.parameters.values())
    if not params:
        return None
    first = params[0]
    if first.name == "ctx" or first.annotation is Context:
        return first.name
    return None


# --- output routing ----------------------------------------------------------


class Status(Protocol):
    """What context asks of a live status line — the duck-typed face of
    `_progress.StatusLine`. context stays ignorant of _progress on purpose;
    this Protocol is the contract, satisfied structurally."""

    counted: dict[str, tuple[int, int]]

    def unit_added(self, count: int = 1) -> None: ...
    def unit_started(self, name: str) -> None: ...
    def unit_counted(self, name: str, done: int, total: int) -> None: ...
    def unit_finished(self, name: str, ok: bool) -> None: ...
    def unit_skipped(self, name: str) -> None: ...
    def notify(self, s: str) -> None: ...
    def suspend(self) -> None: ...
    def resume(self) -> None: ...


# The run's live status line, registered by the scheduler for the duration
# of a run; outside a run there is none.
_status: Status | None = None

# The widest command label seen, for aligning the step lines' time column.
# Seeded from the previous run's history (so alignment is right from the
# first line on a warm run) and grown as a running max on a cold one.
_cmd_width: int = 0


def seed_cmd_width(width: int) -> None:
    global _cmd_width
    _cmd_width = max(0, width)


def cmd_width() -> int:
    return _cmd_width


def _observe_cmd(label: str) -> int:
    """Return the padding width for *label*, learning as labels stream by.

    In terminal cells, like every other column — a `step("构建镜像")` is
    four characters and eight cells wide."""
    from livery.footman._describe import display_width

    global _cmd_width
    _cmd_width = max(_cmd_width, display_width(label))
    return _cmd_width


def set_status(status: Status | None) -> None:
    global _status
    _status = status


def active_status() -> Status | None:
    """The run's live status line, or `None` outside a run."""
    return _status


class _Router:
    """A `sys.stdout`/`sys.stderr` proxy that sends each write to the current
    task's sink — `err_sink` for the stderr router, `sink` for stdout. At task
    level the two point at one buffer (combined, order-preserving); a `run()`
    capturing an in-process callable splits them to record the step's streams."""

    def __init__(self, real: TextIO, *, err: bool = False) -> None:
        self.real = real
        self._err = err
        try:
            self._tty = real.isatty()
        except Exception:
            self._tty = False

    def _sink(self) -> TextIO | None:
        ctx = current()
        return ctx.err_sink if self._err else ctx.sink

    def write(self, s: str) -> int:
        sink = self._sink()
        if sink is not None:
            return sink.write(s)
        # A real-terminal write: the live status line (if any) must clear
        # itself first and learn whether the cursor now sits at column 0.
        if self._tty and _status is not None:
            _status.notify(s)
        return self.real.write(s)

    def flush(self) -> None:
        (self._sink() or self.real).flush()

    def isatty(self) -> bool:
        return self.real.isatty()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.real, name)


_router: _Router | None = None
_err_router: _Router | None = None


def real_stdout() -> TextIO:
    """The underlying stdout, bypassing the routing proxy."""
    return _router.real if _router is not None else sys.stdout


def real_stderr() -> TextIO:
    """The underlying stderr, bypassing the routing proxy."""
    return _err_router.real if _err_router is not None else sys.stderr


def real_stdin() -> TextIO:
    """The underlying stdin, bypassing the guard proxy.

    The framework's own prompts read here — they already write through
    `real_stderr()` — because the guard exists for *task-body* reads, and the
    managed window now opens before binding, where `ask()` legitimately asks.
    """
    real = getattr(sys.stdin, "_real", None)
    return real if real is not None else sys.stdin


_T = TypeVar("_T")
_V = TypeVar("_V")

_UNSET: Any = object()  # "no default given" — None is a valid default/value

_prompt_lock = threading.Lock()

# --- the boundary's one read of stdin ----------------------------------------
#
# stdin is the fourth process global: task bodies never read it (the guard in
# _globals refuses), and parameters marked `stdin` are filled here, at the
# boundary. The stream is read once, fully, into memory, and the same payload
# serves every parameter in the run that asks — stdin is consumable, so two
# tasks in a chain could never each read it. `None` means "not provided": a
# terminal, a missing stream, or an embedded `Runner.invoke` that injected
# nothing. The read is lazy — only a bind that meets a `stdin` parameter
# calls `stdin_payload()`, so a run without one never touches the stream.

_STDIN_UNREAD: Any = object()
_stdin_payload: Any = _STDIN_UNREAD


def stdin_payload() -> bytes | None:
    """The process's piped stdin as bytes, read once and cached; `None` when
    stdin is a terminal (interactive input is `ask()`'s job, and a pipe
    target must never block on a terminal read)."""
    global _stdin_payload
    if _stdin_payload is _STDIN_UNREAD:
        if _stdin_is_tty() or sys.stdin is None:
            _stdin_payload = None
        else:
            try:
                # `.buffer` reaches the raw stream through the guard proxy —
                # this is the boundary, exactly who may read.
                _stdin_payload = sys.stdin.buffer.read()
            except (AttributeError, OSError, ValueError):
                # No byte buffer (an embedded/captured stream) or a stream
                # that refuses to read: nothing was provided.
                _stdin_payload = None
    payload: bytes | None = _stdin_payload  # the sentinel is gone by here
    return payload


# --- scripted answers: the table a recording answers run() from ---------------


class _Answers:
    """A `recording(answers=…)` table, normalised once and shared by reference.

    Keys are command prefixes matched against the normalised shown command
    (`Result.command` — what a recording asserts on): a key matches when
    the command *is* it or starts with it followed by a space, and the
    longest matching key wins. No tokenising: a command string is never
    split (footman hands it to the OS unsplit on Windows), so the rule is
    the same on every platform and can never disagree with what would run.
    A tuple key is joined with spaces; a string key has its whitespace
    collapsed.

    Values: a `str` is stdout with exit 0; an `int` is an exit code; a
    `Result` (footman's or toolroom's) sets code and both streams; an
    exception *instance* is raised at the door — the missing-binary case; a
    `list` is consumed in order across repeated matches, and refuses by
    name once exhausted. Sequence cursors live here, under one lock, so a
    `parallel()` child consuming an entry advances the same script.
    """

    __slots__ = ("_cursors", "_lock", "_table")

    def __init__(self, table: Mapping[Any, Any]) -> None:
        canned: dict[str, Any] = {}
        for key, value in table.items():
            canned[self._key(key)] = self._checked(value, key)
        self._table = canned
        self._cursors: dict[str, int] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(key: Any) -> str:
        if isinstance(key, str):
            tokens: list[Any] = key.split()
        else:
            try:
                tokens = list(key)
            except TypeError:
                tokens = []
        if not tokens or not all(isinstance(t, str) for t in tokens):
            raise TypeError(
                f"recording(answers=): a key is a command prefix — one string "
                f'("uv tool list") or a tuple of tokens — not {key!r}'
            )
        return " ".join(tokens)

    @classmethod
    def _checked(cls, value: Any, key: Any) -> Any:
        if isinstance(value, list):
            if not value or any(isinstance(v, list) for v in value):
                raise TypeError(
                    f"recording(answers=): the sequence for {key!r} must be a "
                    f"non-empty list of single answers — one per match"
                )
            return [cls._checked(v, key) for v in value]
        if isinstance(value, type) and issubclass(value, BaseException):
            raise TypeError(
                f"recording(answers=): the answer for {key!r} is an exception "
                f"class — pass an instance ({value.__name__}(...)), which is "
                f"what the call will raise"
            )
        if isinstance(value, (str, int, BaseException)) or hasattr(value, "stdout"):
            return value
        raise TypeError(
            f"recording(answers=): the answer for {key!r} must be a str "
            f"(stdout), an int (exit code), a Result, an exception instance, "
            f"or a list of those — not {type(value).__name__}"
        )

    def answer(self, command: str) -> tuple[int, str, str] | None:
        """The scripted (code, stdout, stderr) for *command*, `None` when no
        key matches; raises the scripted exception, or `LookupError` when a
        sequence has run dry."""
        best: str | None = None
        for key in self._table:
            if (command == key or command.startswith(key + " ")) and (
                best is None or len(key) > len(best)
            ):
                best = key
        if best is None:
            return None
        value = self._table[best]
        if isinstance(value, list):
            with self._lock:
                n = self._cursors.get(best, 0)
                if n >= len(value):
                    plural = "y" if len(value) == 1 else "ies"
                    raise LookupError(
                        f"recording(answers=): the sequence for {best!r} "
                        f"answered its {len(value)} entr{plural} and "
                        f"`{command}` asked for one more — the script is "
                        f"shorter than the story; add an entry, or make it a "
                        f"single value to answer every match"
                    )
                self._cursors[best] = n + 1
            value = value[n]
        if isinstance(value, BaseException):
            raise value
        if isinstance(value, str):
            return 0, value, ""
        if hasattr(value, "stdout"):  # a Result — footman's or toolroom's twin
            return int(value), value.stdout, value.stderr
        return int(value), "", ""


# The table `Runner.invoke(answers=…)` sets for the span of one invocation:
# the executor mints its own contexts, so the ambient one is never consulted
# there, and `run()` falls back to this when its context carries no table.
_injected_answers: _Answers | None = None


def _inject_answers(table: _Answers | None) -> _Answers | None:
    global _injected_answers
    previous = _injected_answers
    _injected_answers = table
    return previous


def _scripted_answer(ctx: Context, command: str) -> tuple[int, str, str] | None:
    table = ctx.answers if ctx.answers is not None else _injected_answers
    if table is None:
        return None
    if not isinstance(table, _Answers):  # a raw mapping given to Context()
        table = _Answers(table)
        ctx.answers = table
    return table.answer(command)


def _inject_stdin(payload: bytes | None) -> Any:
    """Set the boundary payload directly (the testing seam) and return the
    previous cache state for `_restore_stdin_payload`. `Runner.invoke` always
    injects — under a test runner the real stream is the harness's, never the
    test's — so `stdin=None` means "a terminal", not "read the harness"."""
    global _stdin_payload
    previous = _stdin_payload
    _stdin_payload = payload
    return previous


def _restore_stdin_payload(previous: Any) -> None:
    global _stdin_payload
    _stdin_payload = previous


def _stdin_is_tty() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except Exception:
        return False


def _scrub(text: str) -> str:
    """Drop control characters (ESC included) from text echoed to the terminal,
    so an untrusted `select()` label or message can't inject ANSI escapes — the
    terminal-injection class that has bitten other CLIs."""
    return "".join(c for c in text if c.isprintable() or c == "\t")


def _prompt_core(
    message: str = "", *, default: str | None = None, secret: bool = False
) -> str:
    """The prompt mechanics, unguarded. Writes to the real terminal on stderr
    (never captured, never in `--json` stdout), serialises on `_prompt_lock`,
    clears the live status line, and degrades off a tty (returns `default`, or
    raises). The framework's `ask()` resolution calls this directly; user code
    goes through the guarded `prompt()`."""
    if not _stdin_is_tty():
        if default is not None:
            return default
        raise RuntimeError(
            "no terminal is attached, so there is no one to ask. Pass a "
            "default for unattended runs, or take the value as a task "
            "parameter (a CLI flag) instead."
        )
    err = real_stderr()
    status = active_status()
    with _prompt_lock:
        if status is not None:
            status.notify(message or " ")  # clear the live status line
        closed = (
            "stdin closed mid-prompt (Ctrl-D, or piped input ran out) — "
            "nothing left to ask. Pass the value as a flag, or rerun with "
            "a terminal attached."
        )
        if secret:
            import getpass

            try:
                value = getpass.getpass(message, stream=err).rstrip("\n")
            except EOFError:
                raise RuntimeError(closed) from None
        else:
            err.write(message)
            err.flush()
            line = real_stdin().readline()
            if line == "":  # EOF: a re-ask loop would spin on it forever
                raise RuntimeError(closed)
            value = line.rstrip("\n")
        if status is not None:
            status.notify("\n")  # Enter returned the cursor to column 0
    return default if value == "" and default is not None else value


def _guard_interactive(what: str) -> Context:
    """Refuse a mid-body interactive call in a non-interactive task.

    Inside an ordinary (captured, possibly parallel) task body the prompt
    would be swallowed by the capture buffer or race a sibling for the
    terminal, so it is a loud, taught error rather than a silent hang. The
    framework's own up-front `ask()` resolution runs with `in_task` unset and
    is never caught here. Returns the active context (for `--no-input`/`--yes`)."""
    ctx = current()
    if ctx.in_task and not ctx.interactive:
        raise RuntimeError(
            f"{what} was called inside task {ctx.task or '?'!r}, which is not "
            f"interactive. Either mark it `@task(interactive=True)` so it owns "
            f"the terminal, or declare the value as a parameter with `ask()` so "
            f"footman asks before the task runs."
        )
    return ctx


def _refuse_prompt_markers(peeled: Any) -> None:
    """Markers that decide how a *parameter* gets filled have no meaning once
    a prompt is already asking — refused by name, so a copy-pasted annotation
    teaches instead of half-working."""
    offers = (
        (peeled.ask is not None, "ask()"),
        (peeled.env is not None, "env()"),
        (peeled.stdin is not None, "stdin"),
        (peeled.forward, "forward"),
        (peeled.hidden, "hidden"),
        (peeled.default_fn is not None, "default(fn)"),
        (peeled.completer is not None, "suggest()"),
    )
    named = [name for present, name in offers if present]
    if named:
        raise ValueError(
            f"prompt(type=…): {', '.join(named)} decides how a parameter is "
            f"filled, which a live prompt already settles — declare the value "
            f"as a task parameter instead (or use select() for a menu)"
        )


@overload
def prompt(
    message: str = ..., *, default: str | None = ..., secret: bool = ...
) -> str: ...
@overload
def prompt(
    message: str = ...,
    *,
    type: type[_T],
    default: _T | None = ...,
    secret: bool = ...,
) -> _T: ...
@overload
def prompt(
    message: str = ..., *, type: Any, default: Any = ..., secret: bool = ...
) -> Any: ...


def prompt(
    message: str = "",
    *,
    type: Any = str,
    default: Any = None,
    secret: bool = False,
) -> Any:
    """Ask the person running the task for a line of input.

    A bare `input()` doesn't work in a task: its prompt goes to stdout, which
    footman buffers per task so parallel output never interleaves (and `--json`
    stays one envelope), so the prompt is swallowed and the task looks hung.
    `prompt()` writes to the real terminal on stderr instead — never captured —
    and serialises concurrent prompts.

    Usable only inside an `@task(interactive=True)` task; called in an ordinary
    task body it raises a taught error naming the two fixes. Off a terminal,
    under `--no-input` or `--dry-run`, or when it would otherwise block, it
    returns `default` if given, else raises — an unattended run fails loudly,
    and a rehearsal is unattended by nature. For a value a
    script must supply, take it as a task parameter (a CLI flag) instead.

    `type=` reuses the parameter type language as the validator: the answer
    runs through the same coercion pipeline a flag does — `int`, `Path`, an
    enum, `Literal[…]`, a union, or `Annotated[…]` carrying `between()` /
    `check()` / `exists` — and a bad answer is taught and re-asked rather
    than raised. An empty answer takes `default` (returned as given,
    uncoerced), and an unattended run takes it the same way. One value per
    prompt: collections, and the markers that decide how a *parameter* gets
    filled (`ask()`, `env()`, `suggest()`, …), are refused by name — declare
    a parameter, or reach for `select()`.

    `secret=True` hides the typing (getpass) *and* returns a `Secret`, so the
    answer redacts in tracebacks and structured output the same way
    `ask(secret=True)` does — hiding a value while it is typed and then
    printing it in the first traceback would be a strange kind of secret.
    A default returned unattended is wrapped too: where the value came from
    doesn't change what it is. A secret is text by nature, so `secret=True`
    with a non-`str` `type=` is refused — `ask(secret=True)` on a typed
    parameter is that spelling.
    """
    from livery.footman.params import Secret

    if secret and type is not str:
        raise ValueError(
            "prompt(): a secret answer is text — drop type=, or declare the "
            "value as a typed parameter with ask(secret=True)"
        )
    ctx = _guard_interactive("prompt()")
    peeled = None
    if type is not str:
        # Refuse a bad type= before the unattended branch can paper over it
        # with the default: a wrong annotation is a programming error, and
        # those fail the same way attended or not.
        from livery.footman import _coerce

        peeled = _coerce.peel(type)
        _refuse_prompt_markers(peeled)
        if peeled.multiple or peeled.mapping:
            raise ValueError(
                "prompt() takes one value — for several, use "
                "select(multiple=True) or declare a parameter (Many[…])"
            )
    if ctx.no_input or ctx.dry_run:
        if default is not None:
            return Secret(default) if secret else default
        why = "--no-input is set" if ctx.no_input else "a dry-run is unattended"
        raise RuntimeError(
            f"prompt(): {why}, so nothing can be asked. Pass a "
            f"default, or supply the value as a task parameter (a CLI flag)."
        )
    if peeled is None:
        answer = _prompt_core(message, default=default, secret=secret)
        return Secret(answer) if secret else answer

    from livery.footman._executor import _coerce_extra

    err = real_stderr()
    while True:
        # An empty answer means the default when one exists, so hand
        # _prompt_core "" as its degrade value too: off a terminal the same
        # default comes back through the same door.
        raw = _prompt_core(message, default="" if default is not None else None)
        if raw == "" and default is not None:
            return default
        try:
            # ask()'s own pipeline for a token the splitter never saw:
            # strict coercion, then choices / bounds / path / check(fn).
            return _coerce_extra(raw, peeled, "the answer")
        except ValueError as exc:
            err.write(_scrub(f"  {exc}") + "\n")
            err.flush()


def confirm(message: str, *, default: bool = False) -> bool:
    """Ask a yes/no question. `--yes` auto-answers yes; Enter alone takes
    `default`; off a terminal or under `--no-input`/`--dry-run` the answer
    is `default`. Guarded like `prompt()` — interactive tasks only."""
    ctx = _guard_interactive("confirm()")
    if ctx.assume_yes:
        return True
    if ctx.no_input or ctx.dry_run:
        return default
    reply = _prompt_core(
        f"{message} {'[Y/n]' if default else '[y/N]'} ",
        default="y" if default else "n",
    )
    return reply.strip().lower() in ("y", "yes")


@overload
def select(
    message: str,
    options: Sequence[str],
    *,
    multiple: Literal[True],
    default: list[str] = ...,
) -> list[str]: ...
@overload
def select(
    message: str,
    options: Sequence[str],
    *,
    multiple: Literal[False] = False,
    default: str = ...,
) -> str: ...
@overload
def select(
    message: str,
    options: Sequence[str | tuple[str, _V]],
    *,
    multiple: Literal[True],
    default: list[str | _V] = ...,
) -> list[str | _V]: ...
@overload
def select(
    message: str,
    options: Sequence[str | tuple[str, _V]],
    *,
    multiple: Literal[False] = False,
    default: str | _V = ...,
) -> str | _V: ...


def select(
    message: str,
    options: Sequence[Any],
    *,
    multiple: bool = False,
    default: Any = _UNSET,
) -> Any:
    """Let the person pick from a runtime-computed list — the one interactive
    case a flag can't cover, because the options aren't known until the task
    runs (which changed packages to release, which stale branches to delete).

    `options` are strings, or `(label, value)` pairs to show one thing and
    return another. `multiple=True` returns the chosen subset as a list;
    otherwise one value is returned. Guarded like `prompt()` (interactive tasks
    only), and off a terminal or under `--no-input`/`--dry-run` it returns
    `default`, or raises if none was given.
    """
    ctx = _guard_interactive("select()")
    return _select_core(
        message,
        options,
        multiple=multiple,
        default=default,
        no_input=ctx.no_input or ctx.dry_run,
    )


def _select_core(
    message: str,
    options: Sequence[Any],
    *,
    multiple: bool = False,
    default: Any = _UNSET,
    no_input: bool = False,
) -> Any:
    """The menu mechanics, unguarded — the framework's `ask()` menus call this
    directly; user code goes through the guarded `select()`. Reads the real
    stream, like every framework prompt."""
    opts = list(options)
    if not opts:
        raise ValueError("select(): no options to choose from")
    labels = [o[0] if isinstance(o, tuple) and len(o) == 2 else str(o) for o in opts]
    values = [o[1] if isinstance(o, tuple) and len(o) == 2 else o for o in opts]
    if no_input or not _stdin_is_tty():
        if default is not _UNSET:
            return default
        raise RuntimeError(
            "select(): nothing can be asked (no terminal, or --no-input). Pass "
            "default=…, or take the choice as a task parameter."
        )
    err = real_stderr()
    status = active_status()
    with _prompt_lock:
        if status is not None:
            status.notify(" ")
        err.write(_scrub(message.rstrip()) + "\n")
        for i, label in enumerate(labels, 1):
            err.write(f"  {i}) {_scrub(label)}\n")
        hint = "numbers, comma-separated; 'all'; 'none'" if multiple else "a number"
        err.write(f"select ({hint}): ")
        err.flush()
        line = real_stdin().readline().strip()
        if status is not None:
            status.notify("\n")
    if line == "" and default is not _UNSET:
        return default
    if multiple:
        return [values[i] for i in _parse_multi(line, len(values))]
    return values[_parse_one(line, len(values))]


def _parse_one(line: str, n: int) -> int:
    try:
        i = int(line)
    except ValueError:
        raise RuntimeError(f"select(): {line!r} is not a number 1-{n}.") from None
    if not 1 <= i <= n:
        raise RuntimeError(f"select(): {i} is out of range 1-{n}.")
    return i - 1


def _parse_multi(line: str, n: int) -> list[int]:
    low = line.lower()
    if low in ("all", "*"):
        return list(range(n))
    if low == "none":
        return []
    return sorted({_parse_one(tok, n) for tok in line.replace(",", " ").split()})


@contextlib.contextmanager
def routing() -> Generator[tuple[TextIO, TextIO]]:
    """Install stdout/stderr routers for the duration of a run.

    Both streams proxy through the running task's sink, so an in-process tool's
    stderr is captured alongside its stdout (matching the merged subprocess
    capture) instead of leaking to the terminal. The routers are *stacked*, not
    reset to None: a nested run — e.g. toolroom's `pytest(in_process=True)` driving
    the shipped `fm` fixture — restores the outer routers on exit, so the outer
    run's capture keeps working afterwards.
    """
    global _router, _err_router
    prev_out, prev_err = _router, _err_router
    real_out, real_err = sys.stdout, sys.stderr
    # A tool (or footman's own status line) may emit non-ASCII on a
    # locale-encoded pipe (cp1252 on Windows CI, errors='strict' by default);
    # degrade unencodable glyphs to '?' instead of crashing the run.
    for stream in (real_out, real_err):
        with contextlib.suppress(Exception):
            # getattr, not hasattr-then-call: hasattr narrowing is not
            # portable across checkers, the getattr is.
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure is not None:
                reconfigure(errors="replace")
    _router, _err_router = _Router(real_out), _Router(real_err, err=True)
    sys.stdout, sys.stderr = _router, _err_router
    try:
        # (real stdout, real stderr): task blocks land on the first, the live
        # status line on the second — stdout is the answer, stderr commentary.
        yield real_out, real_err
    finally:
        sys.stdout, sys.stderr = real_out, real_err
        _router, _err_router = prev_out, prev_err


# --- run() -------------------------------------------------------------------


def _is_code(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True)
class Invocation:
    """What a `run()` call *is*, apart from how it's spelled to execute.

    toolroom's bridge builds one of these so `run()` can show a readable,
    syntax-highlighted command line — options in separated form, tagged by
    role — while executing whatever the tool actually needs (attached flags,
    or an in-process callable). `parts` is the normalised, human form;
    `exact` is the literal argv, shown under `--verbose` and always
    copy-pasteable. Passing it is how the two are kept from drifting: both
    come from one translation of one call.
    """

    parts: tuple[tuple[str, str], ...]
    exact: tuple[str, ...]

    def text(self, *, exact: bool) -> str:
        """The plain command line — the width-measured, non-colour form."""
        if exact:
            # `exact` is the literal argv, and the bridge keeps a `Secret`
            # whole in it on purpose — the child needs the real value. That
            # makes this the one rendering path where the marker is still
            # here to act on, and `--verbose` is a showing like any other: a
            # secret must not appear just because the reader asked for the
            # paste-able spelling. `parts` was redacted upstream by the
            # bridge; this is the same rule reaching the other form.
            from livery.footman._describe import redact

            return " ".join(_shell_quote(str(redact(a))) for a in self.exact)
        return " ".join(text for _, text in self.parts)

    def painted(self, *, color: bool, exact: bool) -> str:
        """The shown command line, role-coloured when the stream wants it."""
        if exact or not color:
            return self.text(exact=exact)
        from livery.footman._describe import paint_cli

        return paint_cli(list(self.parts), color)


def _shell_quote(text: str) -> str:
    """Quote one token so the shown command line pastes back into a real shell.

    Per-platform, so a Windows `.raw`/`--verbose` line actually round-trips:
    POSIX uses `shlex.quote`; Windows uses stdlib `subprocess.list2cmdline` (the
    exact inverse of the parsing `CreateProcess` does), never `shlex` — which
    emits POSIX single-quotes that cmd/PowerShell can't read. `list2cmdline`
    handles spaces/quotes/backslashes, not cmd metacharacters (`& | ^`), which
    is fine for a display line that already ran."""
    if sys.platform == "win32":
        return subprocess.list2cmdline([text])
    return shlex.quote(text)


def argv_tokens(cmd: Iterable[Any]) -> list[str]:
    """A `run()` argv list as tokens, with a bare container refused.

    An element that is itself a container has no command-line spelling of its
    own — stringified it becomes the one token `"['a', 'b']"`, which survives
    to the tool and fails there, late and confusingly. `*` already says
    "these are tokens" (`run(["ssh", host, *cmd])`), and a whole command line
    headed for a remote shell is one *quoted* token (`cmd.posix()`), so the
    bare spelling can only be a mistake. Everything else keeps `str()`, which
    is what `Path` and `int` want.
    """
    out: list[str] = []
    for item in cmd:
        if isinstance(item, _CONTAINERS):
            raise TypeError(container_error(item, "run()"))
        out.append(str(item))
    return out


# Concrete containers only — never `Iterable`, which would catch `str` and
# explode it into characters. `dict` is here because a mapping has no
# positional reading at all; `set`/`frozenset` because a splat of an
# unordered value would produce a nondeterministic command line.
_CONTAINERS = (list, tuple, set, frozenset, dict)


def container_error(value: Any, where: str, *, example: str = "") -> str:
    """The taught refusal for a bare container in an argv slot.

    toolroom's door teaches the same wording (its own copy, per the twin
    ruling), so the lesson reads the same wherever it is met. An `Argv` —
    footman's own, or any twin carrying the shell renderers (`.posix()`)
    — gets its own wording: it is the one container that plausibly lands
    here on purpose, and the fix differs by what was meant.
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
        # `**` means flags at a bridge call; inside a `run()` list there is
        # no dict spelling at all, so there is nothing to suggest spreading.
        fix = f"Spread it with `**` to mean flags: `{example}`. " if example else ""
    else:
        star = example or "run(['…', *value])"
        fix = f"Spread it with `*` to mean arguments: `{star}`. "
    return (
        f"{where}: a {kind} was passed as a positional argument, and a "
        f"container has no command-line spelling of its own — it would "
        f"become the one token {str(value)!r}. {fix}To build a whole "
        f"command line to pass on, use `.argv`."
    )


def _exact(cmd: Any, args: tuple[Any, ...]) -> str:
    """The exact executed command line for a direct (non-bridge) `run()`.

    A string is already a command line; a list is shell-quoted so it pastes;
    a callable has no command line, so its label stands in.
    """
    if callable(cmd):
        return _label(cmd, args)
    if isinstance(cmd, str):
        return cmd
    return " ".join(_shell_quote(token) for token in argv_tokens(cmd))


def _label(cmd: Any, args: tuple[Any, ...]) -> str:
    if callable(cmd):
        name = getattr(cmd, "__qualname__", getattr(cmd, "__name__", repr(cmd)))
        return " ".join([f"{name}()", *map(str, args)]).strip()
    return cmd if isinstance(cmd, str) else " ".join(argv_tokens(cmd))


def _shown(
    cmd: Any, args: tuple[Any, ...], title: str | None
) -> tuple[str, str | None]:
    """The label and title this call is *shown* by: the same renderer, run
    over a command whose `Secret` arguments have become `***`.

    The one place a command line is prepared for showing — the announce line,
    the step receipt, `--json`, a profile span and a `RunFailed` message all
    read what this produced. Redaction belongs here and not at the record's
    birth: a record scrubbed on the way in loses the value for `recording()`,
    for a dependent reading `.command`, and for the caller who passed it,
    which is a great deal more than a display gets to decide.

    A secret the task *interpolated* is untouched, because a `str` operation
    on a `Secret` already yielded a plain `str` — `run(f"login {token}")`
    prints in the clear on purpose, and `reveal()` is that same intent said
    out loud.
    """
    from livery.footman._describe import redact

    safe_title = None if title is None else str(redact(title))
    return safe_title or _label(redact(cmd), redact(args)), safe_title


def _exit_code(exc: SystemExit) -> int:
    """A `SystemExit`'s exit code: its int code, 0 for `None`, else 1 (a message).

    `sys.exit()` / `raise SystemExit(...)` is a common "fail this step" idiom, and
    `SystemExit` is a `BaseException` — so every place that treats a call's
    outcome as a code must normalise it the same way, or it escapes uncaught."""
    return exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)


def _call_for_code(cmd: Callable[..., Any], args: tuple[Any, ...]) -> int:
    try:
        returned = cmd(*args)
    except SystemExit as exc:
        return _exit_code(exc)
    if isinstance(returned, int) and not isinstance(returned, bool):
        return returned
    return 0


_state_lock = threading.RLock()


@contextlib.contextmanager
def _process_state(env: dict[str, str]) -> Generator[None]:
    """Patch `os.environ` around an in-process callable — the *bare-call
    fallback only*.

    Inside a run the environ router serves reads from the task's overlay
    (`_env_overlay` below: thread-confined, lock-free). Outside a routed run
    (bare calls in scripts/tests) there is no router to lean on, so fall
    back to the classic global patch, guarded by a re-entrant lock and
    restored on exit — exactly the shape of the output router's own
    fallback. The common case (no overlay) is lock-free either way.

    The patch *replaces* rather than updates: *env* is a whole environment
    (`dict(ctx.env)`, or the caller's `env=`), the same wholesale meaning
    `env=` has on the subprocess and router lanes — and replacement is what
    lets an absent key mean absent, which `color="never"` spells by
    *removing* the force variables rather than writing `"0"`.

    The process **cwd** is never touched: footman does not chdir. A call
    that needs a different directory runs as a subprocess (explicit `cwd=`,
    fully parallel) — `_run_callable` raises the taught error for the
    in-process case.
    """
    if not env:
        yield
        return
    with _state_lock:
        saved_env = os.environ.copy()
        try:
            os.environ.clear()
            os.environ.update(env)
            yield
        finally:
            os.environ.clear()
            os.environ.update(saved_env)


@contextlib.contextmanager
def _env_overlay(ctx: Context, overlay: dict[str, str]) -> Generator[None]:
    """Thread-confined env for an in-process call inside a run: swap
    `ctx.env` for the call's merged overlay — the environ router serves the
    callable's reads from it, and any child it spawns inherits it. No
    process global is touched, so concurrent calls never serialise."""
    saved = ctx.env
    ctx.env = overlay
    try:
        yield
    finally:
        ctx.env = saved


def _run_callable(
    cmd: Callable[..., Any],
    args: tuple[Any, ...],
    *,
    capture: bool = True,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> tuple[int, str, str]:
    """Run a callable — parallel-safe under the router, honoring env/cwd.

    With the router installed, every write this thread makes already
    dispatches through `current().sink`/`err_sink`, so capture is a
    thread-confined swap of the two to fresh buffers — concurrent in-process
    tools never touch each other's output, and the callable's stdout and stderr
    land in separate buffers for the step's `Result`. Outside a routed run
    (bare calls in scripts/tests) there is no router to lean on, so fall back to
    the classic global redirect (still split, into two buffers).

    `capture=False` skips the buffers entirely (live output, returns `('', '')`
    like the subprocess branch) — for serve-style tasks that must not buffer
    unboundedly. The env overlay is applied process-globally via
    `_process_state`; the `capture=False` short-circuit runs *inside* it so
    uncaptured callables keep env too.

    cwd is **checked, never applied**: footman does not chdir in a parallel
    task. Equal target and live cwd (the common single-package case) runs
    untouched; a *foreign* target is a taught error naming the exits —
    exactly the case the old chdir silently serialised the run for.
    """
    ctx = current()
    # Colour is decided once for the whole run and published into os.environ at
    # the run boundary (`color_environment`), so an in-process tool reads it
    # straight from the environment — no per-call patch here, so the lock-free
    # fast path in `_process_state` is kept in every colour mode.
    # Same rule as the subprocess branch: `env=` replaces, absent inherits.
    overlay = dict(env) if env is not None else dict(ctx.env)
    # `cwd` arrives resolved by `_target_cwd`: the task's managed directory
    # or an explicit per-call target. None means footman stays out (the
    # `unmanaged` token, task-level or per-call): no cwd opinion for the
    # callable, exactly as the subprocess branch spawns with cwd=None.
    if cwd is not None:
        live = Path(_globals.real_getcwd())
        if cwd.resolve() != live:
            raise ValueError(
                f"this in-process call needs cwd {cwd} but the process "
                f"is at {live} — footman no longer chdirs in a parallel task. "
                f"Run it as a subprocess (which gets cwd= for free), build "
                f"paths from footman.cwd(), declare @task(cwd='unmanaged'), "
                f"or pass cwd='unmanaged' on this call if it genuinely "
                f"doesn't care."
            )
    state = _env_overlay(ctx, overlay) if _globals.active() else _process_state(overlay)
    with state:
        if not capture:
            return _call_for_code(cmd, args), "", ""
        out_buf, err_buf = io.StringIO(), io.StringIO()
        # The dual capture strategy lives once, in `_captured_streams`.
        with _captured_streams(out_buf, err_buf):
            code = _call_for_code(cmd, args)
        return code, out_buf.getvalue(), err_buf.getvalue()


def _leaf(text: str) -> str:
    """An address leaf, made parse-safe: the address grammar reserves `/`
    (tree levels) and `#` (ordinals), and leaves are minted from
    user-influenced text — command tokens, titles. Anything outside the
    safe alphabet maps to `-`, runs collapse, leading `.`/`-` strip (so
    `./ship` names `ship`), and a leaf is never empty. Names only: the
    record's `command`/title still show the original verbatim."""
    out = []
    last_dash = False
    for ch in text:
        if ch.isalnum() or ch in "_.-":
            out.append(ch)
            last_dash = ch == "-"
        elif not last_dash:
            out.append("-")
            last_dash = True
    leaf = "".join(out).strip(".-")
    return leaf or "step"


def _next_label(labels: dict[str, int], label: str) -> str:
    """The next same-labelled sibling's spelling: bare first, `#2` after."""
    n = labels.get(label, 0) + 1
    labels[label] = n
    return label if n == 1 else f"{label}#{n}"


def _addr_leaf(title: str | None, label: str) -> str:
    """A run() record's address leaf: a titled call is named whole; a raw
    command names by its tool word plus its verb when it has one
    (`git-push`, `uv-sync`) — descriptive enough to read and to keep
    `git fetch`/`git push` distinct across runs, while flags stay out so
    an option tweak never re-identifies the step."""
    if title:
        return title
    tokens = label.split()
    leaf = tokens[0] if tokens else "run"
    if len(tokens) > 1 and not tokens[1].startswith("-"):
        leaf = f"{leaf}-{tokens[1]}"
    return leaf


def _child_address(parent: Context, label: str) -> str:
    """The tree-derived name of the next child with this label under
    *parent* — deterministic, because a parent's requests are made from its
    own control flow in written order."""
    leaf = _next_label(parent._labels, _leaf(label))
    return f"{parent.address}/{leaf}" if parent.address else leaf


@contextlib.contextmanager
def _captured_streams(out_buf: io.StringIO, err_buf: io.StringIO) -> Generator[None]:
    """Capture this thread's stdout/stderr into the two buffers — THE dual
    capture strategy: a thread-confined sink swap under the router
    (parallel-safe), the classic global redirect outside a routed run.
    `_run_callable` and the step pump both drive through here."""
    ctx = current()
    if _router is not None:
        saved_out, saved_err = ctx.sink, ctx.err_sink
        ctx.sink, ctx.err_sink = out_buf, err_buf
        try:
            yield
        finally:
            ctx.sink, ctx.err_sink = saved_out, saved_err
        return
    with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
        yield


# Live subprocesses footman has spawned, so fail-fast can terminate the ones
# still running when a sibling fails. A run in-process (a `tools` entry point,
# a callable) registers nothing — there is no child to kill, and it finishes.
# Each child records its task's keep-going policy, so a fail-fast failure can
# reap the fail-fast trees in a mixed run while a keep-going tree runs on.
_live_children: dict[subprocess.Popen[str], bool] = {}  # proc -> keep_going
_children_lock = threading.Lock()
_aborting = threading.Event()  # set once *any* termination (fail-fast/Ctrl-C) fired
_abort_full = threading.Event()  # set when the abort spares nothing (Ctrl-C, error)


def abort_reaches(keep_going: bool) -> bool:
    """Does the live abort reach work under this failure policy?

    The abort flags are process-wide latches, but fail-fast's abort is
    per-subtree: a keep-going subtree is not doomed by a sibling's failure, so
    nothing about it — its children, its verdicts — belongs to that abort. A
    *full* abort (Ctrl-C, an internal error) spares nothing.

    Every reader of the latches asks this question, so it is answered here
    once. `_register_child` calls it under `_children_lock` to keep its
    snapshot atomic; the executor calls it to decide whether a failure was
    genuine or cut off.
    """
    return _aborting.is_set() and (_abort_full.is_set() or not keep_going)


def _kill_tree(proc: subprocess.Popen[str], *, force: bool) -> None:
    """Signal a spawned child *and its descendants*, not just the child itself.

    A killable child leads its own process group (POSIX) / group (Windows), and
    its grandchildren inherit it — so a group-wide signal reaps the workers a
    bare `terminate()` would orphan: pytest-xdist, `make -j`, a shell script's
    background jobs. POSIX sends SIGTERM, or SIGKILL once *force*. Windows has no
    SIGTERM/SIGKILL split, so `taskkill /T` (walk the tree by PID) `/F` (force)
    is one forceful shot for both — the escalation below is then a harmless
    re-run against a tree that is already gone.
    """
    with contextlib.suppress(ProcessLookupError, OSError):
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return
        sig = signal.SIGKILL if force else signal.SIGTERM
        # Kill the whole group only when this child actually *leads* one (spawned
        # with start_new_session, so pgid == pid) — that is how the group reaches
        # its grandchildren. A child that shares footman's group — an interactive
        # task's child, or one a caller spawned without isolation — is signalled
        # alone: never killpg a group we don't own, or fail-fast could take out
        # the runner itself.
        if os.getpgid(proc.pid) == proc.pid:
            os.killpg(proc.pid, sig)
        else:
            os.kill(proc.pid, sig)


def _register_child(proc: subprocess.Popen[str], keep_going: bool = False) -> None:
    # Under the one lock: recording the child and reading the abort flags can't
    # interleave with `terminate_live_children` setting them and snapshotting — so
    # a child is killed either by that sweep or by this check, never missed.
    with _children_lock:
        _live_children[proc] = keep_going
        # Read inside the lock, so the answer belongs to the same instant as
        # the registration above.
        doomed = abort_reaches(keep_going)
    # A child spawned after an abort fired self-terminates, so the doomed run
    # can't outrun the kill — but a keep-going child spawned after a *fail-fast*
    # abort (not a full Ctrl-C) is spared, matching the per-subtree policy.
    if doomed:
        _kill_tree(proc, force=False)


def _forget_child(proc: subprocess.Popen[str]) -> None:
    with _children_lock:
        _live_children.pop(proc, None)


def reset_abort() -> None:
    """Clear the abort flags at the start of a run."""
    _aborting.clear()
    _abort_full.clear()


# How long a terminated child gets to exit before the kill is forced —
# shared by fail-fast and by a timeout, so "ask, then insist" means the
# same interval whichever one asked.
_KILL_GRACE = 2.0


def terminate_live_children(
    grace: float = _KILL_GRACE, *, failfast_only: bool = False
) -> None:
    """Terminate still-running spawned subprocess *trees* — fail-fast's teeth.

    With *failfast_only* (a per-node fail-fast failure) only fail-fast children
    are reaped, so a keep-going task in a mixed run keeps running; the default
    (a full abort — Ctrl-C, an internal error) reaps everything.

    Each killable child was spawned in its own process group, so the SIGTERM
    (POSIX) / `taskkill /T` (Windows) here reaches its grandchildren too; the
    `communicate()` blocking each task's thread then returns and the task
    unwinds. The abort is *latched*, so a subprocess a still-running task spawns
    *after* this fires self-terminates on registration (the doomed run can't
    outrun the kill; `_register_child` applies the same failfast_only sparing). A
    group that ignores SIGTERM is SIGKILLed after *grace* seconds by a daemon
    watcher — a hung tool can't wedge the run. In-process runs register nothing,
    so they finish on their own — un-killable for free, which is the intended
    behaviour. This is also the Ctrl-C reaper: a group-isolated child no longer
    receives the terminal's SIGINT, so the abort paths call this by hand.
    """
    with _children_lock:
        _aborting.set()
        if not failfast_only:
            _abort_full.set()
        procs = [p for p, kg in _live_children.items() if not (failfast_only and kg)]
    for proc in procs:
        _kill_tree(proc, force=False)
    if not procs:
        return

    def _escalate() -> None:
        time.sleep(grace)
        for proc in procs:
            if proc.poll() is None:  # still alive → it ignored SIGTERM, force it
                _kill_tree(proc, force=True)

    threading.Thread(target=_escalate, daemon=True, name="fm-fail-fast-kill").start()


def _argv0(argv: list[str] | str) -> str:
    """The executable a spawn would have run — for the taught missing-tool
    error. A string argv is the Windows single-command-line spelling, where
    the program may sit in quotes ahead of its arguments."""
    if isinstance(argv, list):
        return argv[0]
    line = argv.strip()
    if line.startswith('"') and (end := line.find('"', 1)) > 0:
        return line[1:end]
    return line.split(None, 1)[0] if line else line


def _run_subprocess(
    argv: list[str] | str,
    env: dict[str, str],
    cwd: Path | None,
    capture: bool,
    encoding: str | None = "utf-8",
    input: str | None = None,
    killable: bool = True,
    isolate: bool = True,
    keep_going: bool = False,
    timeout: float | None = None,
    no_window: bool = False,
) -> tuple[int, str, str, bool]:
    # Dev tools (pytest, ruff, git, uv) emit UTF-8 regardless of the OS code
    # page, so decode as UTF-8 by default rather than the locale encoding
    # (cp1252 on Windows would mojibake the capture). `encoding=None` restores
    # locale behavior. `errors="replace"` is the never-crash net either way.
    #
    # Popen + a live registry (not subprocess.run) so a concurrent fail-fast can
    # terminate this child while its thread is blocked in communicate().
    #
    # An isolated child leads its own process group (POSIX session / Windows
    # group) so `terminate_live_children` can kill the whole tree, not just the
    # child — a tool's own workers (pytest-xdist, `make -j`) die with it. The
    # cost: it no longer receives the terminal's Ctrl-C, so the scheduler's abort
    # paths reap it by hand. Two children opt out and stay in footman's group:
    # `atomic` (fail-fast never kills it, so in-group keeps its Ctrl-C behaviour
    # unchanged) and `interactive` (it owns the real terminal — setsid would strip
    # its controlling tty and a full-screen program would misbehave). The kill
    # guard signals such an in-group child alone, never the shared group.
    group: dict[str, Any] = {}
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if isolate else 0
        # A *captured* read has no business owning a console. Without this,
        # Windows Terminal hands each spawn a visible window, and a tool that
        # interrogates the terminal at start-up blocks forever waiting for a
        # reply no pipe will carry — so whether a read hung depended on which
        # window the run was launched from. CREATE_NO_WINDOW gives it a fresh
        # *hidden* console, which is determinism rather than a cure: a
        # determined interrogator still queries the hidden one. Deliberately
        # not DETACHED_PROCESS, which leaves console-hosted runtimes with no
        # console at all — pwsh dies at start-up, git-bash goes mute.
        #
        # Streaming and interactive runs are exempt: they mean to reach the
        # caller's terminal, which is the thing this takes away.
        if no_window:
            flags |= subprocess.CREATE_NO_WINDOW
        if flags:
            group["creationflags"] = flags
    elif isolate:
        group["start_new_session"] = True
    # Footman's own spawn, not a body's: run() has already worked out both
    # the environment and the directory, so the Popen injector must keep its
    # hands off. Left to it, `cwd=None` reads as "left at the default" and
    # gets ctx.cwd written over it — the exact opposite of what a per-call
    # cwd='unmanaged' declares — and the note would tell the author to prefer
    # run() when run() is what they used. Thread-local, so a sibling task's
    # raw Popen in another thread still earns both.
    try:
        with _globals.internal():
            proc = subprocess.Popen(
                argv,
                env=env,
                cwd=cwd,
                # A fed child reads a pipe; otherwise stdin is inherited
                # untouched, so an uncaptured child keeps the terminal it
                # always had.
                stdin=subprocess.PIPE if input is not None else None,
                stdout=subprocess.PIPE if capture else None,
                stderr=subprocess.PIPE if capture else None,
                text=True,
                encoding=encoding,
                errors="replace",
                **group,
            )
    except FileNotFoundError:
        if cwd is not None and not cwd.is_dir():
            raise  # the *directory* is what's missing — keep the honest OS error
        raise CommandNotFound(_argv0(argv)) from None
    # An `@task(atomic=True)` opts its child out of the registry: fail-fast
    # never kills it, so a mid-write (a formatter rewriting a file) can't be
    # truncated. It runs to completion; the run waits for it.
    if killable:
        _register_child(proc, keep_going)
    timed_out = False
    try:
        try:
            # `input` is delivered whole and the pipe closed, so a child that
            # reads to EOF (`uv pip install -r -`) never blocks waiting for
            # more. The post-kill retries below stay bare: after a timeout
            # the convention is to finish the reads without resending.
            out, err = proc.communicate(input=input, timeout=timeout)
        except subprocess.TimeoutExpired:
            # The whole tree, not just the child: a hung tool's own workers
            # would otherwise outlive the call that bounded it. Same escalation
            # fail-fast uses — ask, then insist — and `atomic` does not spare
            # it, because a timeout is this call's own declared bound rather
            # than a sibling's failure reaching across.
            timed_out = True
            _kill_tree(proc, force=False)
            try:
                out, err = proc.communicate(timeout=_KILL_GRACE)
            except subprocess.TimeoutExpired:
                _kill_tree(proc, force=True)
                out, err = proc.communicate()
        except BaseException:
            # An abort unwinding through here — Ctrl-C, most of the time. The
            # `finally` below runs on the way out and takes this child off the
            # registry, so by the time the abort handler upstairs calls
            # `terminate_live_children` there is nothing left to reap and the
            # child outlives the run: `fm` exits 130 while the thing it started
            # keeps going. (Measured: register, forget, then a reaper finding an
            # empty registry.)
            #
            # Killed here instead, by the frame that owns it and knows which
            # one it is. `killable` is the same gate registration used, so an
            # `@task(atomic=True)` child still opts out — a formatter mid-write
            # is exactly what that promise is for.
            if killable:
                _kill_tree(proc, force=False)
            raise
    finally:
        if killable:
            _forget_child(proc)
    if not capture:
        return proc.returncode, "", "", timed_out
    # Whatever it managed to say before the kill: on a hang that partial
    # output is usually the only diagnostic there is.
    return proc.returncode, out or "", err or "", timed_out


def _dim(text: str, color: bool) -> str:
    return f"\033[2m{text}\033[0m" if color else text


def _colored(ctx: Context) -> bool:
    """The one colour predicate: does footman dress this run's output — its own
    chrome and the tools it spawns — for colour?

    `never` (`no_color`) always wins; `always` (`force_color`) forces colour on
    even off a terminal (a pipe into `less -R`); otherwise `auto` follows the
    run's tty-ness (`NO_COLOR` in the environment still bows out)."""
    if ctx.no_color:
        return False
    if ctx.force_color:
        return True
    return ctx.tty and "NO_COLOR" not in os.environ


# Every colour variable footman speaks. `color_environment` clears the whole set
# before setting a direction, so forcing colour *off* is the *absence* of
# FORCE_COLOR, not `FORCE_COLOR=0` — some tools (ruff) read the mere presence of
# FORCE_COLOR as "force on", ignoring `NO_COLOR`, so `"0"` would fail to silence
# them. footman emits by presence/absence; it consumes `NO_COLOR` by presence and
# `FORCE_COLOR` by truthiness (`_resolve_color`).
_COLOR_VARS = ("FORCE_COLOR", "CLICOLOR_FORCE", "CLICOLOR", "NO_COLOR")


def color_env(on: bool) -> dict[str, str]:
    """The colour variables to *set* to force colour on (or off) for a child.

    Without a PTY a child's stdout is a pipe, so `isatty()` is false and a
    well-behaved tool auto-disables colour — footman captures the bytes and
    replays them onto its own terminal, so it wants the colour back. `on` sets
    `FORCE_COLOR`/`CLICOLOR_FORCE`; off sets only `NO_COLOR`. Forcing off is
    completed by *removing* any inherited `FORCE_COLOR` (see `_COLOR_VARS` /
    `color_environment`), never by setting it to `"0"`."""
    if on:
        return {"FORCE_COLOR": "1", "CLICOLOR_FORCE": "1", "CLICOLOR": "1"}
    return {"NO_COLOR": "1"}


def run_colour_on(
    *, no_color: bool, force_color: bool, capture: bool, isatty: bool
) -> bool:
    """The run-wide colour decision, computed once from its run-wide inputs.

    Colour cannot change during a run, so this is `_colored` for every task at
    once — letting the environment be set once at the run boundary rather than
    per call. Capture (a `--json` run) is byte-clean and gates `force_color` off,
    exactly as the scheduler does per task; the rest defers to `_colored`."""
    if capture:
        return False
    tty = isatty and os.environ.get("TERM") != "dumb"
    return _colored(Context(no_color=no_color, force_color=force_color, tty=tty))


@contextlib.contextmanager
def color_environment(on: bool) -> Generator[None]:
    """Publish the run-wide colour decision into `os.environ` for the run.

    One decision, set once: a subprocess inherits it, an in-process tool reads
    it — so no `run()` call has to patch the environment itself (and none takes
    the `_process_state` lock for colour). Every colour variable is cleared
    first, then this direction's are set — so *off* leaves no `FORCE_COLOR` for a
    presence-checking tool to honour, and *on* leaves no stray `NO_COLOR`.
    Restored on exit; nested runs stack, each restoring the value it found."""
    saved = {key: os.environ.get(key) for key in _COLOR_VARS}
    for key in _COLOR_VARS:
        os.environ.pop(key, None)
    os.environ.update(color_env(on))
    try:
        yield
    finally:
        for key, previous in saved.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous


def _name_col(ctx: Context) -> str:
    """The step line's task-name column, padded so siblings align.

    Bold on colour terminals; empty (no column at all) outside a run, so a
    plain `run()` call keeps its old shape.
    """
    if not ctx.task:
        return ""
    from livery.footman import _describe

    wide = max(ctx.name_width, _describe.display_width(ctx.task))
    padded = _describe.pad_to(ctx.task, wide)
    return (f"\033[1m{padded}\033[0m" if _colored(ctx) else padded) + "  "


def _step_line(ctx: Context, ok: bool, label: str, duration: float) -> str:
    """One completed step: mark · name · dimmed command · aligned time."""
    from livery.footman._describe import pad_to
    from livery.footman._progress import fmt_secs

    color = _colored(ctx)
    time_text = f"({fmt_secs(duration)})"
    name = _name_col(ctx)
    # Times align to the widest command — remembered from the previous run
    # of this chain (a warm run aligns from its first line), learned as a
    # running max on a cold one. Never the terminal edge: that reads absurd
    # on wide terminals.
    label = pad_to(label, _observe_cmd(label))

    if not ctx.tty:
        return f"{pad_to('ok' if ok else 'FAIL', 4)} {name}{label}  {time_text}\n"

    mark = (
        ("\033[32m✓\033[0m" if ok else "\033[31m✗\033[0m")
        if color
        else ("ok" if ok else "FAIL")
    )
    shown = f"\033[36m{time_text}\033[0m" if color else time_text
    # The time sits right after the command — a right-aligned column reads
    # absurd on wide terminals, with the time a screen away from its line.
    return f"{mark} {name}{_dim(label, color)}  {shown}\n"


# The tokens that mean "shell" and nothing else. `run(str)` splits and execs
# directly — no shell — so any of these would ride along as a *literal* argument,
# silently breaking a pipeline or redirect (a `tar … | ssh …` that never pipes).
_SHELL_OPERATORS = frozenset(
    {
        "|",
        "||",
        "|&",
        "&",
        "&&",
        ";",
        ";;",
        ">",
        ">>",
        "<",
        "<<",
        "<<<",
        "<>",
        "2>",
        "2>>",
        "&>",
    }
)


def _shell_operator(cmd: str) -> str | None:
    """The first bare shell-operator token in *cmd*, or `None`.

    Only an operator standing as *its own token* counts — the spaced form a
    shell would honour (`… | …`, `… > out`). A glued `a>b` stays one token and
    is left alone, as is any operator inside quotes. Split failures (unbalanced
    quotes) defer to the exec path, which surfaces them.
    """
    try:
        # posix=False on Windows: keep backslash paths intact (they'd otherwise
        # be eaten), so a real path token never looks like an operator.
        tokens = shlex.split(cmd, posix=(os.name != "nt"))
    except ValueError:
        return None
    return next((t for t in tokens if t in _SHELL_OPERATORS), None)


# A modern bash where PATH alone might miss it (a GUI/cron launch, or Windows,
# where git's bash is not on PATH). Checked before `shutil.which("bash")`.
_BASH_HINTS = (
    "/opt/homebrew/bin/bash",  # macOS Apple Silicon Homebrew
    "/usr/local/bin/bash",  # macOS Intel Homebrew
    r"C:\Program Files\Git\bin\bash.exe",  # Windows git bash
    r"C:\Program Files\Git\usr\bin\bash.exe",
)


def _find_exe(name: str, hints: tuple[str, ...] = ()) -> str | None:
    """A concrete path for *name*: a known *hints* location first, then PATH."""
    import shutil

    for hint in hints:
        if os.path.isfile(hint):
            return hint
    return shutil.which(name)


def _resolve_shell(kind: bool | str, policy: str = "posix") -> list[str]:
    """The interpreter argv prefix — `[executable, run-a-string-flag]` — for a
    `run(shell=…)` request.

    `True` follows *policy* (POSIX-everywhere by default: bash, then plain sh,
    with git bash on Windows). A string is a concrete shell (`bash`/`zsh`/`sh`/
    `fish`/`nu`/`pwsh`/`cmd`) or a strategy (`posix`/`native`). Raises a taught
    `ValueError` when the shell can't be found or does not fit the platform —
    never a silent wrong-shell.
    """
    strat = policy if kind is True else str(kind)
    win = sys.platform == "win32"
    if strat == "posix":
        # bash first (pipefail + POSIX word-splitting, and everywhere incl. git
        # bash on Windows), then plain sh. zsh is excluded — its default word
        # splitting is not POSIX, so ask for it by name if you want it.
        exe = _find_exe("bash", _BASH_HINTS) or _find_exe("sh")
        if exe is None:
            raise ValueError(
                "shell=True needs a POSIX shell and none was found. Install one "
                "(git bash on Windows), or use shell='pwsh' / shell='cmd'."
            )
        return [exe, "-c"]
    if strat == "native":
        return (
            [os.environ.get("COMSPEC", "cmd.exe"), "/c"] if win else ["/bin/sh", "-c"]
        )
    if strat == "cmd":
        if not win:
            raise ValueError("shell='cmd' is Windows-only; use 'bash' or 'pwsh'.")
        return [os.environ.get("COMSPEC", "cmd.exe"), "/c"]
    if strat in ("bash", "sh", "zsh", "fish", "nu"):
        exe = _find_exe(strat, _BASH_HINTS if strat == "bash" else ())
        if exe is None:
            raise ValueError(f"shell={strat!r}: {strat!r} was not found on PATH.")
        return [exe, "-c"]
    if strat in ("pwsh", "powershell"):
        exe = _find_exe(strat)
        if exe is None:
            raise ValueError(f"shell={strat!r}: {strat!r} was not found on PATH.")
        return [exe, "-Command"]  # pwsh's own run-a-string flag (accepts -c too)
    raise ValueError(
        f"shell={kind!r} is not a known shell. Use True (the policy), a strategy "
        f"('posix' / 'native'), or a shell name "
        f"('bash', 'zsh', 'sh', 'fish', 'nu', 'pwsh', 'cmd')."
    )


# `clean=True`: run the interpreter without the user's startup files, so a task's
# shell behaves the same on every machine. For `-c` most POSIX shells already
# skip their rc, but pwsh/cmd load a profile and bash honours $BASH_ENV — so it
# is both these flags and (POSIX) dropping BASH_ENV/ENV from the child env.
_CLEAN_FLAGS = {
    "bash": ("--norc", "--noprofile"),
    "zsh": ("-f",),
    "fish": ("--no-config",),
    "nu": ("-n",),
    "pwsh": ("-NoProfile",),
    "powershell": ("-NoProfile",),
    "cmd": ("/d",),
}

# `strict=True`: fail on the first error and on a failing pipe stage. Well-defined
# only for POSIX shells and PowerShell — bash/zsh get pipefail, plain sh cannot
# (dash has none) so it degrades to errexit-only with a one-time note; fish/nu/
# cmd have no errexit at all, so strict there is a taught error, not a silent no-op.
_STRICT_PROLOGUE = {
    "bash": "set -eo pipefail\n",
    "zsh": "set -eo pipefail\n",
    "sh": "set -e\n",
    "pwsh": (
        "$ErrorActionPreference = 'Stop'\n"
        "$PSNativeCommandUseErrorActionPreference = $true\n"
    ),
    "powershell": "$ErrorActionPreference = 'Stop'\n",
}

_strict_sh_noted = False


def _shell_kind_of(exe: str) -> str:
    """The shell family from its executable path — `/usr/bin/bash` → `bash`."""
    return os.path.basename(exe).lower().removesuffix(".exe")


def _shell_prep(
    kind: str, script: str, *, strict: bool, clean: bool
) -> tuple[list[str], str]:
    """Interpreter flags (from *clean*) and the script (from *strict*) for a shell
    run. Raises a taught error when *strict* can't be honoured (fish/nu/cmd have
    no errexit/pipefail)."""
    flags = list(_CLEAN_FLAGS.get(kind, ())) if clean else []
    if strict:
        prologue = _STRICT_PROLOGUE.get(kind)
        if prologue is None:
            raise ValueError(
                f"strict=True is not supported for the {kind!r} shell — it has no "
                f"errexit/pipefail. Use bash, zsh, sh, or pwsh, or drop strict."
            )
        if kind == "sh":
            global _strict_sh_noted
            if not _strict_sh_noted:
                _strict_sh_noted = True
                real_stderr().write(
                    "note: strict under sh has no pipefail; using `set -e` only "
                    "(install bash for errexit + pipefail).\n"
                )
        script = prologue + script
    return flags, script


def _target_cwd(
    ctx: Context, cwd: str | Path | None, rel: str | Path | None
) -> Path | None:
    """The effective directory for one call. Explicit `cwd=` wins; otherwise
    the task's resolved `ctx.cwd` (or none at all under the `unmanaged`
    policy). The literal `cwd="unmanaged"` opts this one call out — the
    same word, the same meaning as the task-level token: no base at all, so
    a child inherits the live process cwd and an in-process callable runs
    under it. `rel=` is a relative suffix on whatever base is in force at
    this call — `ctx.cwd` in the common case."""
    if cwd == "unmanaged":
        if rel is not None:
            raise ValueError(
                "rel=… needs a managed base and cwd='unmanaged' has none — "
                "pass cwd=… explicitly, or use the asinvoked policy"
            )
        return None
    base = Path(cwd) if cwd is not None else (None if ctx.cwd_unmanaged else ctx.cwd)
    if rel is None:
        return base
    rel_path = Path(rel)
    if rel_path.is_absolute() or rel_path.anchor:  # anchored = absolute on win
        raise ValueError(
            f"rel={str(rel)!r} is absolute — rel is a suffix on the call's cwd "
            f"base; pass an absolute directory as cwd=…"
        )
    if base is None and ctx.cwd_unmanaged:
        raise ValueError(
            "rel=… needs a managed base and cwd='unmanaged' has none — "
            "pass cwd=… explicitly, or use the asinvoked policy"
        )
    return (base if base is not None else Path.cwd()) / rel_path


def _reviewed(
    ctx: Context,
    pre_record: Callable[[ResultView], None],
    cmd: Any,
    args: tuple[Any, ...],
    label: str,
    show_label: str,
    code: int,
    out_s: str,
    err_s: str,
    duration: float,
    raw: str,
    tokens: tuple[str, ...],
    addr: str,
    audit: tuple[AuditEntry, ...],
    timed_out: bool,
    start: float | None,
) -> tuple[int, str, str, tuple[AuditEntry, ...]]:
    """The review window: the work ran (or was scripted) and the record is
    still a draft. The reviewer reads what was captured and may amend the
    verdict — title and code — before anything is sealed, shown, or raised.
    A raising reviewer fails the call with the hook's own error: a broken
    reviewer is a broken gate, not a shrug. The record keeps what the work
    honestly produced in that case — review never finished, so nothing it
    half-did is kept. Returns the reviewed (code, label, shown label, audit).
    """
    hook = getattr(pre_record, "__name__", repr(pre_record))
    view = ResultView(
        title=label,
        code=code,
        stdout=out_s,
        stderr=err_s,
        duration=duration,
        raw=raw,
        command=label,
    )
    try:
        pre_record(view)
    except Exception as exc:
        ctx.steps.append(
            Result(
                code,
                command=label,
                stdout=out_s,
                stderr=err_s,
                duration=duration,
                raw=raw,
                shown=show_label,
                timed_out=timed_out,
                address=addr,
                audit=(*audit, _audit_entry("review", hook, None)),
                tokens=tokens,
                started=start,
            )
        )
        raise RuntimeError(
            f"pre_record hook {hook!r} failed reviewing {show_label!r}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    audit = (
        *audit,
        _audit_entry("review", hook, view.code if "code" in view._touched else None),
    )
    code = view.code
    if view.title != label:
        # A reviewer may rename the record; the name it goes out under is
        # shown by the same rule the original was.
        show_label = _shown(cmd, args, view.title)[0]
    return code, view.title, show_label, audit


def run(
    cmd: str | list[str] | Callable[..., Any],
    *args: Any,
    nofail: bool = False,
    recorded: bool = True,
    capture: bool = True,
    input: str | None = None,
    timeout: float | None = None,
    title: str | None = None,
    pre_record: Callable[[ResultView], None] | None = None,
    env: dict[str, str] | None = None,
    color: str = "auto",
    cwd: str | Path | None = None,
    rel: str | Path | None = None,
    encoding: str | None = "utf-8",
    shell: bool | str = False,
    strict: bool = False,
    clean: bool = False,
    _show: Invocation | None = None,
) -> Result:
    """Run a command or a Python callable in the current task's context.

    Subprocess output is decoded as UTF-8 by default; pass `encoding=` for a
    tool that speaks another code page, or `encoding=None` for the locale
    default. Ignored for callables (in-process, no bytes boundary).

    `input=` feeds the child's standard input and closes it — the one lane a
    payload can't take through argv (`uv pip install -r -` reads its
    requirements there). A string, encoded the way the capture is decoded
    (`encoding=`). The *read* side of the process boundary is a task
    parameter marked `stdin`; this is the write side, so a task can sit in
    the middle of a pipeline without a shell between it and either
    neighbour. An in-process tool has no standard input to feed — `input=`
    on one is a taught `TypeError`.

    `cwd=` roots this one call somewhere other than the task's directory;
    `rel=` appends a relative suffix to the base in force (`ctx.cwd`, or the
    explicit `cwd=`), so `run("npm run build", rel="web")` is the ergonomic
    spelling of `cwd=ctx.cwd / "web"`. `cwd="unmanaged"` opts this one call
    out instead — the task-level token, per call: a subprocess spawns
    inheriting the live process cwd, an in-process callable runs under it —
    for a call that touches no paths while its task keeps `ctx.cwd`
    everywhere else.

    `color=` decides what this one child emits: `"always"` forces the colour
    variables into its environment, `"never"` writes `NO_COLOR` and *removes*
    any inherited force variables (a tool reading mere presence would honour
    one straight past `NO_COLOR`), and the default `"auto"` follows the run's
    own decision. Explicit beats ambient, so `color="always"` holds under an
    exported `NO_COLOR`. It merges on top of `env=` when both are given, and
    the in-process lane's overlay reads it too — so a per-call colour reaches
    both halves of a tool's colour, its flags and its environment, through
    one door. footman's own chrome (the step lines) stays on the run-wide
    decision: this is about the child's bytes, not the frame around them.

    **`recorded=False` runs this call off the record.** A call is a *step*
    by default: it earns a receipt line, a row in `--json`, a `recording()`
    entry, and its output joins the task's block. Some calls aren't part of
    the task's story, though — they are how a task *knows* something (`git
    rev-parse HEAD` in a release task). Those return their `Result` and
    report nothing: no record, no receipt, no output replayed.

    Everything else still applies, because the call is not unmanaged — only
    unreported. It runs in `ctx.cwd` with `ctx.env`, inside the task's lane,
    with colour resolved the same way, it is terminated with the rest on a
    fail-fast, and it still raises unless `nofail=True`. It also *executes*
    under `recording()`, where a step would be faked — unless the recording
    carries a scripted answer for it (`recording(answers=…)`), in which case
    the script wins: a value read is not the story being recorded, and
    faking it would corrupt the story that is —
    the real steps downstream would record whatever a blank answer produced.

    **`pre_record=` reviews the record before it is sealed.** Some tools
    speak exit codes that need interpreting — `djlint --reformat` exits 1
    when it changed files, which is success for a formatting gate. The
    reviewer receives the draft (`ResultView`) after the work ran: it reads
    what was captured and may set `title` and `code`; the receipt, the
    record, and the raise-on-nonzero decision all read what the review
    leaves behind, so the call site writes no `nofail=`. A reviewer that
    raises fails the call with its own error — a broken reviewer is a
    broken gate — and the record keeps what the work honestly produced.
    Dry-run never reviews (nothing ran, nothing captured), and a
    `recorded=False` call has no record to review (a note, not an error).

    In-process work is a **step** now: `run()` runs commands. Lift a
    callable instead — `@step` / `step(fn, title=…)` builds an item that
    earns a receipt, and `with step("…"):` records a block where it
    stands. (toolroom keeps its in-process lane through its own
    private channel.)

    `_show` is an internal channel from toolroom's bridge: a structured
    view of the call, so the shown command line can be normalised and
    role-coloured while execution runs whatever the tool needs. An explicit
    `title` still wins; a direct `run([...])` is unaffected.
    """
    ctx = current()
    if args and not callable(cmd):
        # The subprocess-style spelling this door does not have. The extra
        # arguments only ever reached the *label*, so the child ran without
        # them and said so in green: `run("echo", "hi")` printed nothing and
        # passed, `run("sh", "-c", "exit 1")` ran a bare shell that sat on
        # the caller's terminal. Silence is the one thing that must not
        # happen here, so it is a refusal naming the spelling that works.
        meant = [cmd, *args] if isinstance(cmd, str) else [*cmd, *args]
        spelled = ", ".join(repr(str(token)) for token in meant)
        raise TypeError(
            f"run() takes one command — a string, or a list of tokens — and "
            f"the {len(args)} after it would be dropped rather than passed "
            f"to it. Put them in the list: run([{spelled}])"
        )
    if color not in ("auto", "never", "always"):
        raise ValueError(
            f"run(color={color!r}) expects one of auto|never|always — "
            f"auto follows the run's own decision"
        )
    if color != "auto":
        # The per-call twin of the run boundary's publish: the same tri-state
        # applied to this one child's environment — a private dict, no process
        # global, so it is thread-safe by construction. Off means REMOVING the
        # inherited force variables, never writing "0": some tools honour mere
        # presence straight past NO_COLOR.
        painted_env = dict(env) if env is not None else dict(ctx.env)
        for key in _COLOR_VARS:
            painted_env.pop(key, None)
        painted_env.update(color_env(color == "always"))
        env = painted_env
    if (strict or clean) and not shell:
        # strict/clean harden a *shell* run — they mean nothing shell-free, and a
        # silent no-op would be exactly the surprise footman avoids elsewhere.
        which = "strict" if strict else "clean"
        raise ValueError(
            f"run({which}=True) only applies with a shell — it hardens a shell "
            f"run. Pass shell=True (or a shell name), or drop {which}."
        )
    if callable(cmd) and _show is None:
        raise TypeError(
            "run() runs commands; in-process work is a step. Lift the "
            "callable — @step on a def, step(fn, title='…') around one you "
            "didn't write, or `with step('…'):` over inline code — and it "
            "earns a real receipt."
        )
    if input is not None and callable(cmd):
        # Reached only through toolroom's in-process lane: a
        # subprocess has a stdin to feed, a Python call does not.
        raise TypeError(
            "run(input=…) feeds a subprocess's standard input, and an "
            "in-process tool has none — spawn it (in_process=False) to "
            "feed it, or pass the value as an argument"
        )
    out = sys.stdout
    paint = _colored(ctx)
    if _show is not None and title is None:
        # `label` (recorded as .command, and the step-line receipt) is always
        # the normalised form, so a recording() assertion never depends on
        # --verbose. Only the live "about to / now running" line switches to
        # the exact spelling under --verbose; .raw always carries it.
        label = _show.text(exact=False)
        raw = _show.text(exact=True)
        # A bridged call arrives already rendered to strings, so no `Secret`
        # survives the crossing for `_shown` to find — the tool library owns
        # that half of the redaction.
        show_label, show_title = label, None
        shown = _show.painted(color=paint, exact=ctx.verbose)
        shown_plain = _show.text(exact=ctx.verbose)
        # The bridge already separated the argv; keep it for `to_argv()`.
        tokens = tuple(_show.exact)
    else:
        label = title or _label(cmd, args)
        raw = _exact(cmd, args)
        show_label, show_title = _shown(cmd, args, title)
        shown = _dim(show_label, paint)
        shown_plain = show_label
        # A list `run()` has its tokens apart; a command *string* does not,
        # and splitting one back is platform-dependent guesswork — `to_argv()`
        # teaches that rather than guessing.
        tokens = (
            () if callable(cmd) or isinstance(cmd, str) else tuple(argv_tokens(cmd))
        )

    # A scripted answer is looked up before the recording branch decides,
    # because an explicit match wins even for an off-record call (below).
    # The lookup itself may raise: an exception value, or a sequence run dry.
    scripted = _scripted_answer(ctx, label) if ctx.dry_run else None
    if ctx.dry_run and (recorded or scripted is not None):
        # Record the step even when not executing: `dry_run` + `quiet` is the
        # silent-capture mode `footman.testing` builds on. The recorded label
        # is normalised; only the shown line colours or (under -v) goes exact.
        #
        # A non-step call runs anyway. It is not the story being recorded — it
        # is how the task learns something — and faking it would corrupt the
        # story that *is*: the real steps downstream would go on to record
        # whatever a blank answer produced (`git tag ` for a missing sha).
        # Unless the test scripted that very answer: a value read the table
        # names is intercepted, off the record or not — the author declaring
        # the world beats honest execution — and only unmatched reads run.
        addr = _child_address(ctx, _addr_leaf(show_title, show_label))
        # What the call *would* have seen — the same choice the live lane
        # makes below — kept on the record so a recording can assert on it.
        would_env = dict(env) if env is not None else dict(ctx.env)
        try:
            would_cwd = _target_cwd(ctx, cwd, rel)
        except ValueError:
            would_cwd = None  # the live lane refuses it; a rehearsal records
        if scripted is None:
            result = Result(
                0,
                command=label,
                raw=raw,
                shown=show_label,
                address=addr,
                tokens=tokens,
                env=would_env,
                cwd=would_cwd,
            )
            ctx.steps.append(result)
            if not ctx.quiet:
                out.write(f"$ {shown if paint else shown_plain}\n")
            return result
        code, out_s, err_s = scripted
        # A scripted answer is a draft like a live one: reviewers see it —
        # an adjudicator is tested *against* scripted exits — and a
        # non-zero takes the real failing lane. The audit says so.
        audit: tuple[AuditEntry, ...] = (_audit_entry("body", show_label, code),)
        if pre_record is not None and recorded:
            code, label, show_label, audit = _reviewed(
                ctx,
                pre_record,
                cmd,
                args,
                label,
                show_label,
                code,
                out_s,
                err_s,
                0.0,
                raw,
                tokens,
                addr,
                audit,
                False,
                None,
            )
        result = Result(
            code,
            command=label,
            stdout=out_s,
            stderr=err_s,
            raw=raw,
            shown=show_label,
            address=addr,
            audit=audit,
            tokens=tokens,
            env=would_env,
            cwd=would_cwd,
        )
        if recorded:
            ctx.steps.append(result)
            if not ctx.quiet:
                out.write(f"$ {shown if paint else shown_plain}\n")
        if code != 0 and not nofail:
            raise RunFailed(result)
        return result

    if title is not None and not recorded:
        # Not an error: `.opts()` merges along a chain, so a shared tool may
        # carry a title from where it was configured while a call site adds
        # `recorded=False` — neither author wrote the contradiction. Say it once.
        from livery.footman import _globals as _pg_note

        _pg_note._note(
            "recorded-title",
            f"title= is ignored on a recorded=False call ({show_label}): there is "
            f"no receipt to label.",
        )

    if pre_record is not None and not recorded:
        # Same shape as the title note: `.opts()` merges along a chain, so a
        # shared tool may carry a reviewer while one call site goes off the
        # record — neither author wrote the contradiction. Say it once.
        from livery.footman import _globals as _pg_note2

        _pg_note2._note(
            "pre-record-recorded",
            f"pre_record is ignored on a recorded=False call ({show_label}): "
            f"there is no record to review.",
        )

    show = recorded and not ctx.quiet and (ctx.verbose or not capture)
    # `ctx.tty` means "this output dresses for a terminal" (colour, marks);
    # liveness is `sink is None`. A captured block styles for the terminal
    # it will replay onto, but in-place rewrites and the announce line stay
    # live-only: control bytes must never land in a capture buffer.
    live = ctx.sink is None
    if show:
        # The arrow announces what is *running now* — worth a line only
        # while output is live (a TTY rewrites it in place; a streamed CI
        # log may wait minutes under it). A captured block flushes when
        # the task is already done, where "starting X" directly above
        # "finished X" says nothing — the completion line carries it all.
        if ctx.tty and live:
            out.write(f"→ {_name_col(ctx)}{shown}")
            out.flush()
        elif live:
            out.write(f"→ {_name_col(ctx)}{shown_plain}\n")
            out.flush()

    start = time.perf_counter()
    # Bound before the branch: the in-process path never spawns, so it keeps
    # this call's own (refused below if given), while the subprocess path
    # narrows it to whatever the enclosing task has left.
    spawn_timeout = timeout
    if callable(cmd):
        if timeout is not None:
            # A Python callable cannot be interrupted safely — there is no
            # process to signal and no safe way to unwind another thread — so
            # a bound footman cannot honour is refused rather than ignored.
            # toolroom demotes an in-process *tool* to its subprocess
            # twin instead, exactly as it does for a foreign cwd.
            raise ValueError(
                "run(timeout=…) needs a process to bound, and this call runs "
                "in-process. Spawn it (a list/str command, or "
                ".opts(in_process=False) on a tool), or drop the timeout."
            )
        code, out_s, err_s = _run_callable(
            cmd, args, capture=capture, env=env, cwd=_target_cwd(ctx, cwd, rel)
        )
        timed_out = False
    else:
        argv: list[str] | str
        shell_kind = ""
        if shell:
            # An explicit shell: run the whole string through the resolved
            # interpreter — `[bash, -c, "<cmd>"]` — so pipes/redirects/globs
            # work. A list is the shell-free form; it can't be a shell script.
            if not isinstance(cmd, str):
                raise ValueError(
                    "run(shell=…) runs a command *string* through a shell; pass a "
                    "str, not a list (a list is the shell-free form)."
                )
            exe, run_flag = _resolve_shell(shell, ctx.shell_default or "posix")
            shell_kind = _shell_kind_of(exe)
            clean_flags, script = _shell_prep(
                shell_kind, cmd, strict=strict, clean=clean
            )
            argv = [exe, *clean_flags, run_flag, script]
        elif isinstance(cmd, str):
            if (op := _shell_operator(cmd)) is not None:
                raise ValueError(
                    f"run({cmd!r}): {op!r} is a shell operator, but run() does not "
                    f"use a shell, so it would be passed as a literal argument (the "
                    f"pipeline/redirect would silently not happen). Ask for a shell "
                    f"— run(..., shell=True) or shell='bash' — split into separate "
                    f"run() steps, or pass a list to use {op!r} as a literal argument."
                )
            # POSIX shells split on shlex rules; Windows command lines are a
            # single string (CreateProcess) and shlex would mangle backslash
            # paths — hand the string straight to subprocess there.
            argv = cmd if sys.platform == "win32" else shlex.split(cmd)
        else:
            # Tokens as given, with a bare container refused rather than
            # stringified into the one token `"['a', 'b']"` — `*cmd` and
            # `cmd.posix()` are the two meant spellings.
            argv = argv_tokens(cmd)
        # `env=` is the child's environment, exactly as `subprocess` means it —
        # what you pass is what it gets. Otherwise the task's own, which
        # already carries the run-wide colour decision published at the run
        # boundary (`color_environment`) and anything the body has set or
        # deleted. To add rather than replace, say so: `{**os.environ, …}` —
        # inside a task `os.environ` *is* this environment, so the copy is
        # exact rather than approximate.
        run_env = dict(env) if env is not None else dict(ctx.env)
        # A clean POSIX shell means no startup files: `--norc`/`--noprofile`
        # cover interactive/login rc, but bash/sh also source $BASH_ENV/$ENV for
        # a non-interactive `-c`, so drop those from the child env too.
        if clean and shell_kind in ("bash", "sh", "zsh"):
            run_env = {k: v for k, v in run_env.items() if k not in ("BASH_ENV", "ENV")}
        # `unmanaged` spawns with cwd=None (inherit the live process cwd),
        # task-level or per-call; a per-call cwd=/rel= override wins — see
        # `_target_cwd`.
        cwd_path = _target_cwd(ctx, cwd, rel)
        # The enclosing task's deadline bounds this spawn too, whichever is
        # tighter. A task that says `timeout=5` means the whole task, so a
        # `run()` with no bound of its own inherits what is left, and one
        # with a longer bound of its own cannot outlive the task. The
        # subprocess sees only a duration — the task's instant is converted
        # here, at the last moment, so time spent queueing is not charged
        # twice.
        if (left := ctx.time_left()) is not None:
            spawn_timeout = left if timeout is None else min(timeout, left)
        code, out_s, err_s, timed_out = _run_subprocess(
            argv,
            run_env,
            cwd_path,
            capture,
            encoding,
            input=input,
            timeout=spawn_timeout,
            killable=not ctx.atomic,
            # An interactive task owns the real terminal: keep its child in
            # footman's group so it keeps its controlling tty (and its Ctrl-C).
            isolate=not ctx.atomic and not ctx.interactive,
            # Tag the child with its task's policy: a fail-fast failure elsewhere
            # reaps this tree only if the task is fail-fast, not keep-going.
            keep_going=ctx.keep_going,
            # A captured child writes to pipes, so it needs no console of its
            # own; an uncaptured or interactive one is reaching for the real
            # terminal on purpose.
            no_window=capture and not ctx.interactive,
        )
    duration = time.perf_counter() - start
    if timed_out:
        # 124 is the shell convention for "killed by a timeout", so a code
        # travelling out through --json or a branded CLI reads as the thing it
        # is rather than a sentinel of footman's own invention. A Result *is*
        # its exit code, so this is chosen here, not assigned afterwards.
        code = 124
    # The address label is the stable identity — a title names the record,
    # the address names the node (`_addr_leaf` for the naming rule). Minted
    # from the *shown* label: an address is a name, printed wherever the
    # record is, and a name has no business carrying a secret.
    addr = _child_address(ctx, _addr_leaf(show_title, show_label))
    # The audit: the verdict's provenance. The body entry is always present
    # and carries what the work itself produced; review entries follow. The
    # body actor names the work, which is the command line — so it is the
    # shown one, for the same reason the address is: a name is printed
    # wherever the record travels.
    audit = (_audit_entry("body", show_label, code),)
    if pre_record is not None and recorded:
        code, label, show_label, audit = _reviewed(
            ctx,
            pre_record,
            cmd,
            args,
            label,
            show_label,
            code,
            out_s,
            err_s,
            duration,
            raw,
            tokens,
            addr,
            audit,
            timed_out,
            start,
        )
    result = Result(
        code,
        command=label,
        stdout=out_s,
        stderr=err_s,
        duration=duration,
        raw=raw,
        shown=show_label,
        timed_out=timed_out,
        address=addr,
        audit=audit,
        tokens=tokens,
        started=start,
    )
    if recorded:
        ctx.steps.append(result)  # what --json, the report and recording() read

    # Task grain at normal verbosity: a step's receipt shows under
    # --verbose (and for uncaptured, live steps, whose output needs its
    # label) — and ALWAYS when it failed. Green is collapsible; failure is
    # never hidden.
    if (
        recorded
        and not ctx.quiet
        and not ctx.machine_read
        and (ctx.verbose or not capture or code != 0)
    ):
        ok = code == 0
        prefix = "\r\033[K" if ctx.tty and live else ""
        out.write(f"{prefix}{_step_line(ctx, ok, show_label, duration)}")
        # Join the two streams only to *display* them (stdout then stderr);
        # nothing merged is stored — the Result keeps them apart.
        combined = out_s + err_s
        if capture and combined and (not ok or ctx.verbose):
            out.write(combined if combined.endswith("\n") else combined + "\n")
        if not ok and len(result.audit) > 1:
            # The verdict's story, when anyone touched it: who acted, what
            # they set — the audit, one dim line under the failure.
            trail = " → ".join(
                f"{e.moment} {e.actor}" + (f" {e.code}" if e.code is not None else "")
                for e in result.audit
            )
            out.write(_dim(f"     audit: {trail}", _colored(ctx)) + "\n")
        out.flush()

    if code != 0 and not nofail:
        if result.timed_out:
            # The bound that actually applied: this call's own, or what the
            # enclosing `@task(timeout=…)` had left when the spawn started.
            # Reporting the call's own would print `0s` for a task-imposed
            # deadline, which reads as a bug in the timeout rather than a
            # deadline being enforced.
            raise RunTimeout(result, effective_timeout(timeout, ctx))
        raise RunFailed(result)
    return result


# --- parallel() --------------------------------------------------------------


def _call_name(call: Callable[..., Any]) -> str:
    """The name a fan-out child shows on the status line and its step lines.

    A `functools.partial` unwraps to what it wraps; a lambda has no honest
    name of its own, so it shows as an ellipsis rather than `<lambda>`.
    """
    if isinstance(call, functools.partial):  # partial(fmt, check=True)
        call = call.func
    name = getattr(call, "__name__", "task")
    return "…" if name == "<lambda>" else name


class Pending:
    """What a task call hands back *inside* a `with parallel()` block.

    The call has been queued, not run, so there is no value yet — and a
    silent `None` here would be a bug you find much later. Every use is a
    taught error; the values arrive as `p.results` when the block ends.
    """

    __slots__ = ("_task",)

    _task: str

    def __init__(self, task: str) -> None:
        object.__setattr__(self, "_task", task)

    def _refuse(self, *_a: Any, **_k: Any) -> NoReturn:
        raise RuntimeError(
            f"{self._task} has not run yet — inside a `with parallel()` block a "
            f"call is queued, not executed, so it has no value to use there. "
            f"Read the values from the block's results after it ends."
        )

    __getattr__ = _refuse
    __bool__ = _refuse
    __iter__ = _refuse
    __len__ = _refuse
    __int__ = _refuse
    __float__ = _refuse
    __lt__ = _refuse
    __add__ = _refuse
    __hash__: ClassVar[None] = None  # type: ignore[assignment]  # unusable too

    def __str__(self) -> NoReturn:
        self._refuse()

    def __eq__(self, other: object) -> NoReturn:
        self._refuse()

    def __repr__(self) -> str:  # debuggers and tracebacks stay usable
        return f"<pending {self._task}>"


# The queue a `with parallel()` block collects into: a contextvar, so the
# calls a *queued task* makes when it later runs (on a pool thread, which
# starts from contextvar defaults) are ordinary calls, never re-collected.
_collecting: ContextVar[
    list[tuple[Any, tuple[Any, ...], dict[str, Any], int | None]] | None
] = ContextVar("footman_parallel_block", default=None)


class Fanout(list[Result]):
    """The `with parallel() as p:` block — task calls inside it are queued,
    then run together when the block ends.

    A `list` underneath — of sealed records, each of which IS its exit code,
    so everything that read the block as a list of codes keeps working — and
    empty, so `parallel(*items)` with nothing to do still answers with the
    empty list it always did.
    """

    def __init__(self, keep_going: bool = False) -> None:
        super().__init__()
        self.keep_going: bool = keep_going
        self.results: list[Any] = []
        """What each queued call returned, in the order they were written."""
        self._queued: list[tuple[Any, tuple[Any, ...], dict[str, Any], int | None]] = []
        self._token: Any = None
        self._births: list[_step.WorkItem[Any]] = []
        self._birth_token: Any = None

    def __enter__(self) -> Fanout:
        from livery.footman import _step as _step_mod

        self._token = _collecting.set(self._queued)
        self._birth_token = _step_mod._born.set(self._births)
        return self

    def __call__(self, item: _step.WorkItem[Any], /) -> Pending:
        """Queue a built step item for the block — the lifted counterpart of
        a task call, which queues itself."""
        from livery.footman import _step as _step_mod

        if not isinstance(item, _step_mod.WorkItem):
            raise TypeError(
                f"a parallel block queues built step items — p(step(fn)(…)) "
                f"— got {type(item).__name__}. Task calls queue themselves; "
                f"write them as ordinary calls."
            )
        if _collecting.get() is not self._queued:
            raise RuntimeError(
                "p(item) queues work for a block, so call it inside the "
                "`with` — outside one there is nothing to join."
            )
        item._claimed = True
        self._queued.append((item, (), {}, None))
        return Pending(_call_name(item))

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> Literal[False]:
        from livery.footman import _step as _step_mod

        _collecting.reset(self._token)
        _step_mod._born.reset(self._birth_token)
        if exc_type is not None:
            return False  # the block itself failed: nothing to run
        dead = [w for w in self._births if not w._claimed]
        if dead:
            names = ", ".join(w.__name__ for w in dead)
            raise RuntimeError(
                f"built inside the block but never handed to it: {names}. "
                f"Building an item runs nothing — hand it to the block, "
                f"p(item), or call it to run it in place. Nothing ran."
            )
        values: list[Any] = [None] * len(self._queued)

        def thunk(
            index: int,
            task: Any,
            args: tuple[Any, ...],
            kwargs: dict[str, Any],
            seq: int | None,
        ) -> Callable[[], None]:
            def run() -> None:
                # The value goes to the slot, never through the return: a task
                # returning an int would read as an exit code out here. The
                # queue moment's request number rides back in with the call.
                from livery.footman import _futures

                token = _futures._pending_seq.set(seq)
                try:
                    values[index] = task(*args, **kwargs)
                finally:
                    _futures._pending_seq.reset(token)

            run.__name__ = _call_name(task)
            return run

        codes = _run_thunks(
            [thunk(i, t, a, k, q) for i, (t, a, k, q) in enumerate(self._queued)],
            keep_going=self.keep_going,
        )
        self.results = values
        self.extend(codes)  # the block *is* its list of records (codes included)
        return False


def _queue_call(
    task: Any, args: tuple[Any, ...], kwargs: dict[str, Any], seq: int | None = None
) -> Any:
    """Queue a call for the enclosing `with parallel()` block, or `None` when
    there is none — the one hook `_futures.call` needs. The queue moment IS
    the request moment, so the caller's `seq` rides the tuple and the thunk
    hands it back to the re-entering call."""
    queue = _collecting.get()
    if queue is None:
        return None
    queue.append((task, args, kwargs, seq))
    from livery.footman import registry

    return Pending(registry.cli_name(getattr(task, "__name__", "task")))


@overload
def parallel(*, keep_going: bool = False) -> Fanout: ...
@overload
def parallel(
    *calls: _step.WorkItem[Any]
    | _step.StepFn[..., Any]
    | _registry_t.TaskFn[..., Any]
    | _registry_t.Group,
    keep_going: bool = False,
) -> list[Result]: ...


def parallel(
    *calls: _step.WorkItem[Any]
    | _step.StepFn[..., Any]
    | _registry_t.TaskFn[..., Any]
    | _registry_t.Group,
    keep_going: bool = False,
) -> Fanout | list[Result]:
    """Run tasks and steps concurrently; wait; fail if any fail.

    Each one runs in a child of the current context with its own output
    buffer, flushed atomically on completion so concurrent output never
    interleaves. Pass task functions directly (`parallel(lint, typecheck)`)
    and built step items for everything else — `parallel(convert(images))`,
    or `parallel(step(fn)(args))` for a function you didn't write. A bare
    zero-argument maker is welcome too: `parallel(clean)` builds and runs
    `clean()`.

    Nothing anonymous runs: footman only schedules, records, and safely
    cancels work it owns, and a bare callable is a stranger — no name for
    the report, no place in the plan, no way to stop it cleanly. The lift
    is one word, and it buys the step a receipt.

    With no arguments it is a **block** instead, and the calls inside it are
    the fan-out — written as ordinary calls, with their values afterwards:

        with parallel() as p:
            build("web")
            p(step(shutil.rmtree)(tmp, ignore_errors=True))
        web, cleaned = p.results

    Inside the block a task call is *queued*, so it has no value there (using
    one is a taught error); a built step item joins through `p(item)`;
    everything runs when the block ends, under the same rules — sharing,
    hooks, `-s`/`-j`.
    """
    from livery.footman import _step

    if not calls:
        # Nothing to run: the block form, which is also an empty list of exit
        # codes — so `parallel(*items)` over an empty sequence is unchanged.
        return Fanout(keep_going=keep_going)

    from livery.footman import registry as _registry

    accepted: list[Callable[[], Any]] = []
    for c in calls:
        if isinstance(c, _step.StepFn):
            # A zero-argument maker is welcome bare: build its item here, so
            # `parallel(covered)` and `parallel(covered())` mean the same.
            c = c()
        if isinstance(c, _step.WorkItem):
            accepted.append(c)
            continue
        if getattr(c, "_footman_pre", None) is not None or isinstance(
            c, _registry.Group
        ):
            accepted.append(c)  # a task, an opted reference, a runnable group
            continue
        label = _call_name(c) if callable(c) else type(c).__name__
        raise TypeError(
            f"parallel() runs tasks and steps — {label!r} is neither. "
            f"footman only schedules, records, and safely cancels work it "
            f"owns, and a bare callable is a stranger. Lift it and it earns "
            f"a receipt too: parallel(step(fn)(…)), or step(fn, "
            f"title='…') to name a lambda."
        )
    return _run_thunks(accepted, keep_going=keep_going)


def _run_thunks(
    calls: list[Callable[[], Any]], *, keep_going: bool = False
) -> list[Result]:
    """The fan-out engine: run zero-argument callables the caller vouches
    for. `parallel()` validates and lands here; the block's `__exit__`
    drives it directly with the closures footman itself wrote."""
    from concurrent.futures import ThreadPoolExecutor

    parent = current()
    dest = parent.sink or real_stdout()
    dest_is_real = parent.sink is None
    lock = threading.Lock()

    # parallel() children are units on the live status line, exactly like
    # scheduler nodes — a chain and a task-body fan-out present identically.
    # Every child counts once here, whatever it is: a task, a partial, a
    # lambda wrapping a call, a plain thunk. What the child *does* can't be
    # known from the outside — a closure is opaque — so the count can't
    # depend on reading it. Instead the unit is handed to the child (see
    # `Context.unit_pending`), and the first task request inside claims it
    # rather than counting a second one for the same piece of work.
    status = _status
    if status is not None:
        status.unit_added(len(calls))

    # Sibling names are known up front, so their step lines can align.
    width = max((len(_call_name(c)) for c in calls), default=0)

    def invoke(call: Callable[[], Any]) -> tuple[Result, BaseException | None]:
        name = _call_name(call)
        child_start = time.perf_counter()
        if status is not None:
            status.unit_started(name)
        # One buffer for both streams at task level, so the atomic flush keeps
        # this child's stdout/stderr in order; a run() inside it still splits the
        # step's streams via a temporary swap.
        buf = io.StringIO()
        child = parent.child(
            name,
            sink=buf,
            err_sink=buf,
            name_width=width,
            # This child's unit is counted above; the first task request
            # inside claims it instead of counting its own.
            unit_pending=True,
        )
        token = _current.set(child)
        try:
            # The arbiter counts every child body (an exclusive drain must
            # see them); a lineage child of a lane holder bypasses the bars —
            # `serial_active` rode in through `replace(parent, …)`.
            with _globals.lane(None, name=name, inherited=child.serial_active):
                returned = call()
            code = returned if _is_code(returned) else 0
            error: BaseException | None = None
            # A thunk that *returns* a non-zero code failed just as surely as one
            # that raised RunFailed. Synthesize the failure here so the gate below
            # treats both uniformly; `keep_going` still collects the code.
            if code != 0:
                thunk = _label(call, ())
                error = RunFailed(Result(code, command=thunk, raw=thunk))
        except RunFailed as exc:
            code, error = exc.result.code or 1, exc
        except SystemExit as exc:
            # `sys.exit()` / `raise SystemExit(...)` is a common "fail this task"
            # idiom, but a BaseException — without this it escapes the pool
            # instead of being collected. Treat its code like a returned one:
            # 0 succeeds, non-zero is a synthesized failure the gate below raises.
            # (A string reason is not carried through here: parallel() deliberately
            # normalises failures to a catchable RunFailed, so it must not re-raise
            # a BaseException. A task body's own sys.exit("reason") keeps its
            # message — see executor._call.)
            code = _exit_code(exc)
            error = None
            if code != 0:
                thunk = _label(call, ())
                error = RunFailed(Result(code, command=thunk, raw=thunk))
        except Failed as exc:
            # `footman.fail("reason")` in a thunk: an Exception (not a
            # BaseException), so the gate can re-raise it and its reason surfaces
            # at the task level — better than the sys.exit-in-parallel corner.
            # Its own code rides the return list too.
            code, error = exc.code, exc
        except Exception as exc:  # a failed call must not crash the pool
            code, error = 1, exc
        finally:
            _current.reset(token)
        gate = _globals.console_gate() if dest_is_real else contextlib.nullcontext()
        with gate, lock:
            blob = buf.getvalue()
            # A child that ended mid-colour (a crash, an unterminated SGR) must
            # not bleed into the next child's block when they interleave: cap a
            # colourful run's block with a reset. Only when colour is on, so a
            # byte-clean run stays byte-clean.
            if blob and _colored(parent):
                blob += "\033[0m"
            if status is not None and dest_is_real:
                # This write bypasses the routers (dest is the raw stream):
                # tell the status line to get out of the way itself.
                status.notify(blob)
            dest.write(blob)
            dest.flush()
            # Surface the child's run() steps on the parent, in completion order,
            # so they appear in `--json` and `recording()` (F12).
            parent.steps.extend(child.steps)
            parent.sections.extend(child.sections)
        if status is not None:
            status.unit_finished(name, error is None)
        # The caller's view of this child: a sealed record that IS its exit
        # code (so every code-reader keeps working), named and addressed.
        record = Result(
            code,
            command=name,
            address=child.address,
            duration=time.perf_counter() - child_start,
            started=child_start,
        )
        return record, error

    # -s reaches inside tasks (one worker serialises the calls in
    # submission order), and -j caps the width; same code path either way.
    if parent.sequential:
        workers = 1
    elif parent.jobs > 0:
        workers = max(1, min(parent.jobs, len(calls)))
    else:
        workers = max(1, len(calls))
    # Parked for the pool wait: this body is blocked in footman code and
    # cannot touch globals, so an exclusive drain may exempt it (the
    # ancestry exemption — its own children still count on their own).
    with (
        _globals.parked(),
        ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="fm-parallel"
        ) as pool,
    ):
        try:
            outcomes = list(pool.map(invoke, calls))
        except BaseException:
            # The same abort arm the scheduler has: on Ctrl-C the interrupt
            # unwinds here, in the main thread, while every worker is still
            # blocked in communicate() on a group-isolated child that never
            # saw the terminal's signal. Without this the pool's `with` exit
            # joins those threads and the interrupt waits out the work it
            # just cancelled (measured: a 300-second child held it 25s+
            # before a second Ctrl-C escaped and orphaned the tree).
            terminate_live_children()
            pool.shutdown(wait=False, cancel_futures=True)
            raise

    if not keep_going:
        for _record, error in outcomes:
            if error is not None:
                raise error
    return [record for record, _ in outcomes]
