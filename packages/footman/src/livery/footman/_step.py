"""Steps you make yourself: the lifter, the built item, and the pump.

`step()` is one name in three grammatical positions. Decorating a local
function (plain or generator) makes a **maker**: calling the maker BUILDS
a bound, deferrable piece of work — the `range(10)` precedent, owned — it
runs nothing. `with step("title"):` records a block of your own code
where it stands. `step(fn, title=…)` is the expression form of the
decorator, for functions you didn't write.

A built item is callable — calling it executes it here, under the current
task's management — which is exactly what `parallel()` does with one. The
pump drives generator items: every bare `yield` is a checkpoint, the one
kind of place a running item can be cancelled (fail-fast, Ctrl-C, or its
own `timeout=` — all three arrive as "the loop never resumes"), and every
yield evaluates to the item's own draft, so a title decided mid-work is a
plain attribute write.
"""

from __future__ import annotations

import inspect
import io
import time
from collections.abc import Callable, Generator
from contextvars import ContextVar
from typing import Any, Generic, Literal, ParamSpec, TypeVar, cast, overload

from livery.footman import _describe, _signals
from livery.footman import context as _context
from livery.footman.context import (
    AuditEntry,
    Failed,
    Result,
    ResultView,
    RunFailed,
    RunTimeout,
    _audit_entry,
)

P = ParamSpec("P")
R = TypeVar("R")
R_co = TypeVar("R_co", covariant=True)

# Births inside a `with parallel()` block, so the block can refuse to run
# while an item built in it was never handed over: building runs nothing,
# and a dead item is almost always a forgotten `p(...)`. `None` outside a
# block — the common case pays one contextvar read per build.
_born: ContextVar[list[WorkItem[Any]] | None] = ContextVar(
    "footman_step_births", default=None
)

# The closed policy vocabulary a step maker's `.opts()` accepts — execution
# policy only: a step is anonymous, so boundary policy (confirm, gates,
# sharing) has no request boundary to resolve at and is not spellable here.
_STEP_OPTS = (
    "title",
    "capture",
    "recorded",
    "timeout",
    "env",
    "color",
    "lanes",
    "pre_record",
)


class WorkItem(Generic[R_co]):
    """A bound, deferrable piece of work with a record to come.

    Building one runs nothing. Calling it executes it under the current
    task — captured, timed, recorded, reviewable — and returns the body's
    value; a non-zero verdict raises `RunFailed`, exactly as `run()` does,
    so a failing item fails whatever asked for it.
    """

    __slots__ = ("_args", "_claimed", "_kwargs", "_maker")

    def __init__(
        self, maker: StepFn[..., R_co], args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> None:
        self._maker = maker
        self._args = args
        self._kwargs = kwargs
        self._claimed = False
        born = _born.get()
        if born is not None:
            born.append(self)

    @property
    def __name__(self) -> str:  # the label status lines and reports use
        return self._maker.__name__

    def __call__(self) -> R_co:
        self._claimed = True
        return cast(R_co, _pump(self))

    def __repr__(self) -> str:
        return f"<step {self.__name__} (built, not run)>"


class StepFn(Generic[P, R_co]):
    """What the lifter returns: calling it builds the bound item.

    The maker carries the step's identity and policy: `.opts()` refines
    execution policy per use; `.pre_record` attaches its reviewer and
    `.post_step` its observer — permanently, wherever the maker travels.
    """

    __slots__ = ("_fn", "_is_gen", "_observers", "_opts", "_reviewers")

    def __init__(
        self,
        fn: Callable[..., Any],
        opts: dict[str, Any] | None = None,
        reviewers: list[Callable[[ResultView], None]] | None = None,
        observers: list[Callable[[Result], None]] | None = None,
    ) -> None:
        self._fn = fn
        self._is_gen = inspect.isgeneratorfunction(fn)
        self._opts: dict[str, Any] = opts or {}
        # Shared by reference across `.opts()` copies: attachment is
        # permanent — it changes what the step does for every user of the
        # maker, exactly like a task's handle attachments.
        self._reviewers = reviewers if reviewers is not None else []
        self._observers = observers if observers is not None else []

    @property
    def __name__(self) -> str:
        title = self._opts.get("title")
        if isinstance(title, str) and title:
            return title
        return getattr(self._fn, "__name__", "step")

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> WorkItem[R_co]:
        return WorkItem(self, args, kwargs)

    def opts(self, **overrides: Any) -> StepFn[P, R_co]:
        """Per-use execution policy; the maker stays callable as itself."""
        unknown = sorted(set(overrides) - set(_STEP_OPTS))
        if unknown:
            valid = ", ".join(_STEP_OPTS)
            raise TypeError(
                f"step .opts() got unknown option(s) {unknown}; valid options "
                f"are {valid}. A step is anonymous, so boundary policy "
                f"(confirm, sharing, gates) needs a declared task to live on."
            )
        if (color := overrides.get("color", "auto")) not in ("auto", "never", "always"):
            # The same tri-state `run(color=)` takes, refused the same way:
            # one vocabulary for colour wherever a caller decides it.
            raise ValueError(
                f"step .opts(color={color!r}) expects one of auto|never|always "
                f"— auto follows the run's own decision"
            )
        if overrides.get("lanes") is not None:
            from livery.footman.registry import validate_lanes

            overrides["lanes"] = validate_lanes(overrides["lanes"])
        return StepFn(
            self._fn,
            {**self._opts, **overrides},
            self._reviewers,
            self._observers,
        )

    def pre_record(
        self, hook: Callable[[ResultView], None], /
    ) -> Callable[[ResultView], None]:
        """Attach this step's reviewer — the draft, before the record seals."""
        self._reviewers.append(hook)
        return hook

    def post_step(self, hook: Callable[[Result], None], /) -> Callable[[Result], None]:
        """Watch this step's sealed record — read-only; veto via `fail()`."""
        self._observers.append(hook)
        return hook


class _StepBlock:
    """`with step("title") as s:` — record a block of your own code.

    The record-only lift: no execution boundary is created (the block's
    statements run exactly as they would bare, dry-run included), so what
    the record keeps is the title, the verdict, and the honest duration.
    The handle is the block's own draft — self-review is writing to it.
    """

    __slots__ = ("_start", "_view")

    def __init__(self, title: str) -> None:
        self._view = ResultView(
            title=title,
            code=0,
            stdout="",
            stderr="",
            duration=0.0,
            raw="",
            command=title,
        )
        self._start = 0.0

    def __enter__(self) -> ResultView:
        self._start = time.perf_counter()
        return self._view

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> Literal[False]:
        duration = time.perf_counter() - self._start
        view = self._view
        code = view.code
        if exc_type is not None and "code" not in view._touched:
            code = 1  # the block failed; a self-review that already ruled wins
        ctx = _context.current()
        shown = str(_describe.redact(view.title))
        result = Result(
            code,
            command=view.title,
            shown=shown,
            duration=duration,
            address=_context._child_address(ctx, shown),
            audit=(_audit_entry("body", shown, code),),
        )
        ctx.steps.append(result)
        return False  # an exception propagates; the record sealed first


@overload
def step(
    target: Callable[P, Generator[None, ResultView, R]],
    /,
    *,
    title: str | None = None,
) -> StepFn[P, R]: ...
@overload
def step(target: Callable[P, R], /, *, title: str | None = None) -> StepFn[P, R]: ...
@overload
def step(target: str, /) -> _StepBlock: ...
def step(target: Any = None, /, *, title: str | None = None) -> Any:
    """One name, three positions: `@step` on a def, `with step("title"):`
    over a block, `step(fn, title=…)` around a function you didn't write.

    Decorator and expression positions are the same expression, so both
    return the maker — calling it builds the item; nothing runs until
    something executes it (`parallel()`, or calling the built item).
    """
    if isinstance(target, str):
        if title is not None:
            raise TypeError(
                "step('title') is the block form — pass the title once, "
                "positionally; title= belongs to the decorator/expression "
                "forms (step(fn, title=…))"
            )
        return _StepBlock(target)
    if target is None:
        raise TypeError(
            "step() needs its subject: a function to lift (@step / "
            "step(fn, title=…)) or a title string for the block form "
            "(with step('…'):)"
        )
    if not callable(target):
        raise TypeError(
            f"step() lifts a callable or opens a titled block; got "
            f"{type(target).__name__}"
        )
    opts: dict[str, Any] = {}
    if title is not None:
        opts["title"] = title
    return StepFn(target, opts)


def _pump(item: WorkItem[Any]) -> Any:
    """Execute a built item under the current task: capture, drive, review,
    seal, observe — one record, committed once."""
    maker = item._maker
    ctx = _context.current()
    label = maker.__name__
    o = maker._opts
    capture: bool = o.get("capture", True)
    recorded: bool = o.get("recorded", True)
    timeout: float | None = o.get("timeout")
    env: dict[str, str] | None = o.get("env")
    color: str = o.get("color", "auto")
    lanes: tuple[Any, ...] = tuple(o.get("lanes", ()))
    if any(getattr(ln, "name", "") == "console" for ln in lanes):
        raise TypeError(
            "the console follows interactive task bodies — claim it with "
            "@task(interactive=True) (or lanes=(console_lane,) on the task); "
            "a step-level console hold waits for a payload that needs one."
        )

    # A title is a plain string the author wrote, so it redacts by the same
    # rule a command line does: the record keeps it, everything printed —
    # the receipt, the address, the audit — reads the shown form.
    shown = str(_describe.redact(label))
    addr = _context._child_address(ctx, shown)
    if ctx.dry_run and recorded:
        # Dry-run fakes what footman owns and records: a deferred maker is
        # exactly that. Declared, not executed — an empty audit says so.
        result = Result(0, command=label, shown=shown, address=addr)
        ctx.steps.append(result)
        return None

    view = ResultView(
        title=label, code=0, stdout="", stderr="", duration=0.0, raw="", command=label
    )
    start = time.perf_counter()
    deadline = start + timeout if timeout is not None else None
    value: Any = None
    error: BaseException | None = None
    stack_text = ""
    timed_out = False
    out_buf, err_buf = io.StringIO(), io.StringIO()

    def drive() -> Any:
        nonlocal timed_out
        if not maker._is_gen:
            produced = maker._fn(*item._args, **item._kwargs)
            if inspect.iscoroutine(produced):
                # An `async def` is not a generator function, so it lands here
                # and the call builds a coroutine that nothing drives: the step
                # recorded a receipt for work that never happened. A generator
                # body is the supported way to yield, and it is a cancellation
                # point rather than a scheduling one.
                produced.close()  # or "was never awaited" trails the refusal
                raise _context.coroutine_refusal("step", maker._fn)
            if inspect.isasyncgen(produced):
                # The coroutine refusal's missed sibling: `async def` plus
                # `yield` builds an async generator — not a coroutine, so the
                # check above never sees it, and not a generator function, so
                # the pump never takes it. Sealed `ok` for zero work, same as
                # the coroutine case, refused the same way.
                raise _context.generator_refusal("step", maker._fn, is_async=True)
            return produced
        gen: Generator[Any, Any, Any] = maker._fn(*item._args, **item._kwargs)
        payload: Any = None
        while True:
            try:
                yielded = gen.send(payload)
            except StopIteration as stop:
                return stop.value
            if yielded is not None:
                # The vocabulary is closed: a bare yield is a checkpoint and
                # evaluates to the draft — the value channel is reserved for
                # a future that earns it, so using it is an error, not noise.
                gen.close()
                raise TypeError(
                    f"step {label!r} yielded {yielded!r} — a step's yields "
                    f"are checkpoints and carry nothing out. Write a bare "
                    f"`yield`; read the draft with `view = yield`; return "
                    f"the step's value with `return`."
                )
            payload = view  # every yield evaluates to the item's own draft
            # The checkpoint: the only place a running item is cancelled —
            # fail-fast/Ctrl-C aborts, or the item's own timeout. All three
            # arrive the same way: the loop never resumes, the generator's
            # own try/finally runs, nothing is left half-held.
            if deadline is not None and time.perf_counter() > deadline:
                timed_out = True
                gen.close()
                return None
            # The enclosing task's deadline, checked beside the step's own:
            # `@task(timeout=…)` is the fail-fast event scoped to one task, so
            # it stops a generator step at exactly the point a sibling's
            # failure would. The step records `timed_out` either way — what
            # ran out is a property of the record above it, not of this one.
            # The enclosing task's deadline, checked beside the step's own:
            # `@task(timeout=…)` is the fail-fast event scoped to one task, so
            # it stops a generator step at exactly the point a sibling's
            # failure would. The step records `timed_out` either way — what
            # ran out is a property of the record above it, not of this one.
            if ctx.overdue():
                timed_out = True
                gen.close()
                return None
            if _context._aborting.is_set() and not ctx.keep_going:
                gen.close()
                return None

    # Same env rule as every run(): `env=` is the whole environment, absent
    # inherits the task's own — applied through the same overlay machinery.
    # One honest exception keeps bare steps parallel: outside a routed run,
    # with no env asked for, there is nothing to overlay — and the global
    # patch would take a process-wide lock that serialises the pool.
    from contextlib import nullcontext

    from livery.footman import _globals

    overlay = dict(env) if env is not None else dict(ctx.env)
    if color != "auto":
        # The same door `run(color=)` opens, on the work item that wraps a
        # whole body: the tri-state lands in the environment this step runs
        # under, so anything it calls — a nested run(), an in-process tool
        # reading the variables — sees one decision. Off means REMOVING the
        # inherited force variables, never writing "0".
        for key in _context._COLOR_VARS:
            overlay.pop(key, None)
        overlay.update(_context.color_env(color == "always"))
    if _globals.active():
        state: Any = _context._env_overlay(ctx, overlay)
    elif env is not None or color != "auto":
        state = _context._process_state(overlay)
    else:
        state = nullcontext()
    exit_code: int | None = None
    try:
        with _globals.named_lanes(lanes, name=label), state:
            if capture:
                with _context._captured_streams(out_buf, err_buf):
                    value = drive()
            else:
                value = drive()
    except SystemExit as exc:  # the classic "fail this step" idiom, honoured
        if isinstance(exc.code, int):
            exit_code = exc.code
        elif exc.code is None:
            exit_code = 0
        else:  # a string reason: print it, fail with 1 — sys.exit's contract
            err_buf.write(f"{exc.code}\n")
            exit_code = 1
    except Failed as exc:
        # A deliberate stop, not a crash: `footman.fail()` and the refusals
        # built on it carry a reason written for the person reading it, and
        # `Failed` promises that reason renders verbatim. A traceback here
        # buried it under footman's own frames and made a considered stop look
        # like an internal error. The reason reaches the report through the
        # sealed record, so it is not written twice.
        error = exc
    except (KeyboardInterrupt, GeneratorExit, _signals.Stop):
        # Control flow, not a failing step — the same three `_call` re-raises,
        # for the same reasons. GeneratorExit especially: the pump below raises
        # it into a step to cancel one, so sealing a record around it here would
        # swallow footman's own cancellation. `Stop` is a supervisor's SIGTERM
        # arriving in whichever step happened to be running; a step that
        # answered for it would be reporting someone else's decision as its own.
        raise
    except BaseException as exc:  # the step failed; the record still seals
        # Everything else that leaves a step body is that step failing, which
        # is the rule tasks already follow. `except Exception` let a
        # BaseException past the record, so no step row sealed and the receipt
        # was lost — the task still failed, correctly, but with nothing to say
        # which step did it.
        error = exc
        # Trimmed of footman's own leading frames, so the first line is the one
        # somebody wrote. It goes into the record unconditionally — `--json`
        # and `recording()` read that, and a machine consumer is not reading
        # for noise — while the *display* below follows the same rule the task
        # summary does. Kept in hand as well as written, so the display can
        # take it back out again without guessing where it starts.
        stack_text = _describe.user_traceback(exc)
        err_buf.write(stack_text)

    duration = time.perf_counter() - start
    if timed_out:
        code = 124
    elif exit_code is not None:
        code = exit_code
    elif error is not None:
        code = 1
    else:
        code = 0
    view._fill(out_buf.getvalue(), err_buf.getvalue(), duration)
    view.code = code
    view._touched.discard("code")  # machinery write, not a review verdict

    audit: tuple[AuditEntry, ...] = (_audit_entry("body", shown, code),)
    reviewers = list(maker._reviewers)
    if o.get("pre_record") is not None:
        reviewers.append(o["pre_record"])  # the use site keeps the final word
    if recorded:
        for reviewer in reviewers:
            name = getattr(reviewer, "__name__", repr(reviewer))
            try:
                reviewer(view)
            except Exception as exc:
                ctx.steps.append(
                    Result(
                        code,
                        command=label,
                        shown=shown,
                        stdout=out_buf.getvalue(),
                        stderr=err_buf.getvalue(),
                        duration=duration,
                        timed_out=timed_out,
                        address=addr,
                        audit=(*audit, _audit_entry("review", name, None)),
                    )
                )
                raise RuntimeError(
                    f"pre_record hook {name!r} failed reviewing {shown!r}: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            audit = (
                *audit,
                _audit_entry(
                    "review", name, view.code if "code" in view._touched else None
                ),
            )

    # A reviewer may rename the record; the name it goes out under is shown
    # by the same rule the original was.
    if view.title != label:
        shown = str(_describe.redact(view.title))
    result = Result(
        view.code,
        command=view.title,
        shown=shown,
        stdout=out_buf.getvalue(),
        stderr=err_buf.getvalue(),
        duration=duration,
        timed_out=timed_out,
        address=addr,
        audit=audit,
    )
    if recorded:
        ctx.steps.append(result)
        # Task grain at normal verbosity: the receipt shows under --verbose
        # (and for uncaptured, live items) — and always when it failed.
        # Under --json the item has a row of its own, so the receipt would
        # only arrive twice: once as chrome inside the task's `output`, once
        # as the fields a reader parses. Same call as run()'s step line.
        if (
            not ctx.quiet
            and not ctx.machine_read
            and (ctx.verbose or not capture or result.code != 0)
        ):
            import sys as _sys

            out = _sys.stdout
            out.write(
                _context._step_line(ctx, result.code == 0, result.shown, duration)
            )
            combined = result.stdout + result.stderr
            if stack_text:
                # The stack stays in the record — `--json` and `recording()`
                # read that — and out of the display, which the run summary
                # owns. One place decides how a failure is placed, so a task
                # and a step answer identically and neither says it twice.
                # What the body printed before it fell over is untouched.
                combined = combined.replace(stack_text, "")
            if capture and combined and (result.code != 0 or ctx.verbose):
                out.write(combined if combined.endswith("\n") else combined + "\n")
            if result.code != 0 and len(result.audit) > 1:
                trail = " → ".join(
                    f"{e.moment} {e.actor}"
                    + (f" {e.code}" if e.code is not None else "")
                    for e in result.audit
                )
                out.write(
                    _context._dim(f"     audit: {trail}", _context._colored(ctx)) + "\n"
                )
            out.flush()
        for observer in maker._observers:
            name = getattr(observer, "__name__", repr(observer))
            try:
                observer(result)
            except _context.Failed as exc:
                amended = Result(
                    exc.code,
                    command=result.command,
                    shown=result.shown,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    duration=result.duration,
                    timed_out=result.timed_out,
                    address=result.address,
                    audit=(*result.audit, _audit_entry("observe", name, exc.code)),
                )
                ctx.steps[-1] = amended
                raise
            except Exception as exc:
                amended = Result(
                    result.code if result.code != 0 else 1,
                    command=result.command,
                    shown=result.shown,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    duration=result.duration,
                    timed_out=result.timed_out,
                    address=result.address,
                    audit=(*result.audit, _audit_entry("observe", name, None)),
                )
                ctx.steps[-1] = amended
                raise RuntimeError(
                    f"post_step hook {name!r} failed observing {result.shown!r}: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc

    if result.code != 0:
        if timed_out:
            raise RunTimeout(result, _context.effective_timeout(timeout, ctx))
        if error is not None:
            raise error
        raise RunFailed(result)
    return value
