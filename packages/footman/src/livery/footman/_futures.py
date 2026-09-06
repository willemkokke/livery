"""Tasks as run-scoped futures: what happens when a task body calls a task.

`artifact = build()` inside a task body used to be a plain function call — it
ran `build` again even if the DAG had just run it, on the caller's thread, with
no context of its own and no `TaskResult` anywhere. That was the one execution
path the framework couldn't see.

Now a call routes through here, and per **(task, resolved arguments, starting
environment)** the run keeps a once-cell:

* already run → the memoised value comes back;
* running on another thread → the caller blocks on its future;
* neither → the caller claims it and runs it **inline, on its own thread**.

So `pre=[build]` plus `build()` in the body is a cache hit, not a second build,
and the callee gets full task treatment: its own context, its own lane
decision, its own reported result.

Sharing is a property of the *request*, not of this layer: an unshared request
(`@task(shared=False)`, `.opts(shared=False)`, or inherited from the task that
asked) neither reads a cell nor becomes one — its execution is its own, and a
later shared request runs fresh rather than reusing it. `schedule` resolves the
same ladder for DAG nodes, so a declared request and a called one behave the
same.

Two things are deliberately *not* here. A call outside a run is a plain call —
importing a tasks file and calling a function must keep working. And lane
acquisition stays at the task boundary, so a body call to a `serial=`/
`exclusive=` task is refused rather than deadlocking mid-body.
"""

from __future__ import annotations

import enum
import io
import itertools
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from livery.footman import _coerce, registry
from livery.footman._split import ChainError

if TYPE_CHECKING:
    from collections.abc import Generator

    from livery.footman._executor import TaskResult

# The memo key's argument half when the arguments cannot be frozen (an
# unhashable value with no obvious frozen form — a live object, a generator).
# Such a call is honest work every time rather than a wrong cache hit.
_UNKEYABLE = object()


class _Settled:
    """The five-method slice of `Future` this module actually uses.

    `concurrent.futures` costs ~4.9 ms to import — it drags `logging` through
    `traceback`, and `logging` drags `_colorize` — and every run that executes
    a task claims a cell, so every run paid it. Nothing here needs the
    executor half of that module: a cell is written once by its owner and read
    by whoever waits, which is a `threading.Event` and two slots.
    """

    __slots__ = ("_done", "_exc", "_value")

    def __init__(self) -> None:
        self._done = threading.Event()
        self._value: Any = None
        self._exc: BaseException | None = None

    def done(self) -> bool:
        return self._done.is_set()

    def set_result(self, value: Any) -> None:
        self._value = value
        self._done.set()

    def set_exception(self, exc: BaseException) -> None:
        self._exc = exc
        self._done.set()

    def result(self) -> Any:
        # No timeout: the wait graph above this layer is what refuses a claim
        # that could not be satisfied, so a wait here is always one that ends.
        self._done.wait()
        if self._exc is not None:
            raise self._exc
        return self._value


class _Cell:
    """One (task, arguments) execution: its future, its owner, its label."""

    __slots__ = ("future", "label", "owner", "record")

    def __init__(self, owner: int, label: str) -> None:
        self.future = _Settled()
        self.owner = owner  # the thread that claimed it (for the wait graph)
        self.label = label
        self.record: Any = None  # the execution's sealed row, for reuse


class _Session:
    """The run's cells, plus the wait-for graph that keeps them honest."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.cells: dict[Any, _Cell] = {}
        self.waits: dict[int, Any] = {}  # thread -> the key it is blocked on
        self.results: list[TaskResult] = []
        # Tasks defined *while this run was in flight* — `(group, name, what
        # was there before)` — put back the way they were when it ends.
        self.ephemeral: list[tuple[Any, str, Any]] = []


_active: _Session | None = None

# The run-wide request counter: every request for task-shaped work takes a
# number at the moment it is *made* — plan order for scheduled segments, the
# written line for a `parallel()` block's queued calls, the call moment for
# body calls. The report is ordered by it outright: the clock cannot do the
# job, because two independent tasks start in whatever order the pool hands
# them workers, and that reshuffles between runs of the same command.
_seq = itertools.count()
# A queued call re-enters `call()` on a pool worker; the queue moment (the
# written line) already took its number, carried here around the invocation.
_pending_seq: ContextVar[int | None] = ContextVar("footman_request_seq", default=None)


@contextmanager
def session() -> Generator[_Session]:
    """Install a fresh cell registry for the duration of one run.

    Run-scoped by construction: memoisation means "this work happened in *this*
    run", never a cache that outlives it. Nested installs (a `Runner` inside a
    task, tests) keep the outer session — one run, one memo.
    """
    global _active
    if _active is not None:
        yield _active
        return
    _active = _Session()
    try:
        yield _active
    finally:
        run, _active = _active, None
        _sweep_ephemeral(run)


def active_session() -> _Session | None:
    """The run's cell registry, or `None` outside a run."""
    return _active


def _sweep_ephemeral(run: _Session) -> None:
    """Undo the registrations a run made in its own course.

    A `@task` written inside a task body is ordinary Python and makes a real,
    callable task — but the manifest was written before the run started, so
    such a task is invisible to every listing and would go on shadowing the
    tree for the rest of the process. It lives for the run that made it, and
    the tree it was grafted onto goes back to what it was.
    """
    for group, name, previous in reversed(run.ephemeral):
        if previous is None:
            group.tasks.pop(name, None)
        else:
            group.tasks[name] = previous


def collected() -> list[TaskResult]:
    """Results of tasks run by body calls this run, in completion order."""
    return list(_active.results) if _active is not None else []


def _freeze(value: Any) -> Any:
    """A hashable normal form for one argument value, or `_UNKEYABLE`.

    Collections get frozen shapes (a list and a tuple of the same items are
    the same work), and anything already hashable is its own key. What has no
    frozen form is not forced into one: it reads as unkeyable, and its call
    runs.
    """
    if isinstance(value, enum.Enum):
        # Before the scalar line on purpose: IntEnum and str-valued enums
        # would otherwise freeze as their value face. The normal form
        # canonicalises an enum argument as its token — the member is the
        # meaning, the value is representation — so renumbering a member
        # does not churn work identity, and every spelling that reached
        # binding froze to the same key anyway (they resolve to one member
        # before anything downstream looks).
        return ("enum", type(value).__name__, _coerce.token_of(value))
    if isinstance(value, (str, bytes, int, float, bool, type(None))):
        return value
    if isinstance(value, (list, tuple)):
        parts = tuple(_freeze(v) for v in value)
        return _UNKEYABLE if _UNKEYABLE in parts else parts
    if isinstance(value, (set, frozenset)):
        parts = tuple(sorted((_freeze(v) for v in value), key=repr))
        return _UNKEYABLE if _UNKEYABLE in parts else frozenset(parts)
    if isinstance(value, dict):
        items = tuple(
            sorted(((_freeze(k), _freeze(v)) for k, v in value.items()), key=repr)
        )
        return _UNKEYABLE if any(_UNKEYABLE in p for p in items) else items
    try:
        hash(value)
    except TypeError:
        return _UNKEYABLE
    return value


def unshared(task: Any) -> bool:
    """Whether *this request* must run rather than reuse an execution.

    The sharing ladder for a call: the reference's own `.opts(shared=…)` or the
    task's declaration, then what the calling task inherited (`ctx.shared` — an
    unshared request asks unshared for everything it needs), then shared. The
    scheduler resolves the same ladder for a node.
    """
    from livery.footman import context

    own = registry.sharing(task)
    if own is not None:
        return not own
    ctx = context._current.get()
    return ctx is not None and not ctx.shared


def _env_digest(env: dict[str, str]) -> str:
    """The environment half of a cell key.

    Order-blind (a dict's insertion order must not split cells), case-blind
    exactly where the OS is (`_norm`: Windows), and a digest rather than the
    pairs themselves so cell keys stay small — equality is all a key needs.
    """
    import hashlib

    from livery.footman import _globals

    h = hashlib.sha1(usedforsecurity=False)
    for k, v in sorted((_globals._norm(k), v) for k, v in env.items()):
        h.update(k.encode("utf-8", "surrogateescape"))
        h.update(b"=")
        h.update(v.encode("utf-8", "surrogateescape"))
        h.update(b"\0")
    return h.hexdigest()


def _key(
    task: Any,
    args: Any,
    kwargs: dict[str, Any],
    given: frozenset[str],
    env: dict[str, str],
) -> Any | None:
    """The cell key for this work: the task's dedup identity, its arguments in
    the same normal form binding produces, what its caller actually asked
    for, and the environment it would start from — so a DAG node and a body
    call that resolve to the same arguments the same way name one piece of
    work. `None` when the arguments have no frozen form.

    *given* joins the key because `apply_defaults()` below erases the very
    distinction `given()` reads: `build()` and `build(profile=<the default>)`
    freeze to identical arguments, and a task that branches on presence does
    different work for each. Keyed on values alone they would silently share a
    cell, and the second request would be answered by the first — no error, and
    nothing in the report to say the two were ever different.

    *env* joins it for the same shape of bug with the other input a body can
    observe: a caller that set `ctx.env` before calling would otherwise be
    answered by an execution that never saw its variable — a wrong cache hit
    with no error. Every DAG node starts from the pinned snapshot and an
    untouched caller holds an equal copy, so digests diverge — splitting the
    cell, honestly — exactly when someone wrote the environment on purpose.
    """
    from livery.footman import _manifest

    # The caller's signature: `ctx` is injected at the task boundary, so it is
    # stripped before binding — binding against the declared signature would
    # put the first positional value in the `ctx` slot and key every call on
    # the wrong arguments.
    try:
        bound = _manifest.call_signature(task).bind(*args, **kwargs)
    except TypeError:
        return None  # a call that won't bind: let it raise where it is made
    bound.apply_defaults()
    frozen = tuple((name, _freeze(value)) for name, value in bound.arguments.items())
    if any(value is _UNKEYABLE for _, value in frozen):
        return None
    return (registry.work_key(task), frozen, given, _env_digest(env))


def _deadlock(run: _Session, key: Any, me: int) -> list[str] | None:
    """The wait-for chain proving this wait can never resolve, or `None`.

    The static DAG has `_check_cycles`; a call graph only exists at runtime, so
    it is walked here: follow the key to its owning thread, then that thread's
    own wait, until either the chain ends (fine) or it arrives back at this
    thread (a cycle — nobody would ever finish).
    """
    chain: list[str] = []
    at: Any = key
    seen: set[Any] = set()
    while at is not None and at not in seen:
        seen.add(at)
        cell = run.cells.get(at)
        if cell is None or cell.future.done():
            return None  # finished work blocks nobody
        chain.append(cell.label)
        if cell.owner == me:
            return chain
        at = run.waits.get(cell.owner)
    return None


def _label(task: Any) -> str:
    return registry.cli_name(getattr(task, "__name__", "task"))


def call(task: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """A task called from Python — the body-call path.

    Outside a run this is the plain function call it always was. Inside one it
    is the once-cell: hit the memo, wait for the thread that is running it, or
    claim it and run it here.
    """
    from livery.footman import context as _context

    # The request takes its number now, in the caller's thread — for a
    # queued call this IS the written line; the thunk carries the number
    # back in through `_pending_seq` when the pool later runs it.
    carried = _pending_seq.get()
    seq = carried if carried is not None else next(_seq)

    # A `with parallel()` block collects instead of running: the call is
    # queued and answers with a `Pending`. Checked first, because the block
    # is explicit intent — it holds whether or not a run is in flight.
    if (queued := _context._queue_call(task, args, kwargs, seq)) is not None:
        return queued

    run = _active
    if run is None:
        _refuse_wide_moment(task)
        if isinstance(task, registry._Opted):
            return task._plain_call(args, kwargs)
        return registry.task_body(task)(*args, **kwargs)
    _refuse_unrunnable(task)
    from livery.footman import _executor, _schedule, context

    label = _label(task)
    seg = _schedule._default_seg(task)
    # Availability is a per-request gate on the declared path (`run_task`
    # checks it before the window opens), so a call checks it in the same
    # place: before any hook fires.
    if (refusal := _executor.unavailable(task, seg)) is not None:
        refusal.seq = seq
        _record(refusal)
        raise refusal.error or ChainError(f"{label} is unavailable")

    # A call binds like a segment: omitted parameters consult the same sources
    # binding would (stdin, env, a required `ask()`), explicit values are
    # validated against their annotation, and resolution happens before the
    # key is computed so identity reads the values the body will receive.
    # With a lifecycle armed, the callee's context is born first and made
    # current around binding, so a `pre_bind` hook's `task.env` writes reach
    # `env()` fallbacks here exactly as they do on the declared path.
    life = _executor._lifecycle
    child: Any = None
    handle: Any = None
    if life is not None or registry.has_own_hooks(task):
        parent = context.current()
        buf = io.StringIO()
        child = parent.child(
            label,
            fn=task,
            cwd=None,  # let the callee's own cwd policy resolve
            sink=buf,
            err_sink=buf,
        )
        handle = _executor.TaskHandle(task, seg, child)
        if (err := _executor._enter_bind_hooks(life, handle)) is not None:
            # The attempt concluded before binding: the posts fire, the row
            # is recorded, and the failure raises at the call site.
            result = _executor._result(seg, 1, None, err, 0.0)
            result.seq = seq
            _executor._exit_task_hooks(life, handle, result)
            _record(result)
            raise err
        token = context._current.set(child)
        child.in_task = True
        try:
            args, kwargs, supplied = _executor.bind_call(task, args, kwargs)
        except Exception as exc:
            result = _executor._result(seg, _executor.EX_USAGE, None, exc, 0.0)
            result.seq = seq
            _executor._exit_task_hooks(life, handle, result)
            _record(result)
            raise
        finally:
            context._current.reset(token)
        child.given = supplied
    else:
        args, kwargs, supplied = _executor.bind_call(task, args, kwargs)
    # The environment the callee would start from, as known right here: the
    # already-born child's (post `pre_bind`, exactly what the declared path
    # keys on), else a copy of the caller's — which is what `child()` would
    # copy at birth. An untouched caller holds the run snapshot, so this
    # digests equal to a DAG node's and sharing is preserved.
    birth_env = (child if child is not None else context.current()).env
    key = _key(task, args, kwargs, supplied, birth_env)
    if key is None:  # arguments with no frozen form: honest work every time
        return _run_now(
            task,
            args,
            kwargs,
            seg=seg,
            child=child,
            handle=handle,
            seq=seq,
            given=supplied,
        )
    if unshared(task):
        # Asked for unshared: it neither reads a cell nor becomes one. Its
        # result is its own — the run's answer is only ever an execution that
        # was itself shareable, so how much work a run does cannot depend on
        # which of two nodes the scheduler happened to start first.
        return _run_now(
            task,
            args,
            kwargs,
            shared=False,
            seg=seg,
            child=child,
            handle=handle,
            seq=seq,
            given=supplied,
        )

    me = threading.get_ident()
    claimed, cell = _claim(run, key, me, label)
    if claimed:  # nobody had run it: this thread owns it, inline, right here
        try:
            # The cell rides along so the sealed row lands on it *before* the
            # future resolves — a later sharer copies what this execution
            # reported (title, audit, reported value) and seats after it.
            value = _run_now(
                task,
                args,
                kwargs,
                seg=seg,
                child=child,
                handle=handle,
                cell=cell,
                seq=seq,
                given=supplied,
            )
        except BaseException as exc:
            cell.future.set_exception(exc)
            raise
        cell.future.set_result(value)
        return value
    # The pair is per request — only the body is shared. The pre fires before
    # the wait, so a span honestly covers it; the post closes the request
    # with its `shared` row. A crashing hook must not pass silently here
    # either: it fails this request, at the call site.
    if handle is not None:
        handle._bind(args, kwargs)
        if (hook_error := _executor._enter_task_hooks(life, handle)) is not None:
            row = _executor._result(seg, 1, None, hook_error, 0.0)
            row.seq = seq
            _executor._exit_task_hooks(life, handle, row)
            _record(row)
            with run.lock:
                run.waits.pop(me, None)
            raise hook_error
    # The waiting request is a unit like any other — a scheduler node
    # satisfied by sharing counts, so a body call satisfied by sharing does
    # too, and the caller's block on the share is visible on the line.
    status = _claim_unit(label)
    try:
        # A finished cell answers instantly (the memo); a live one blocks until
        # the thread that claimed it is done, and both hand back one value.
        value = cell.future.result()
    except BaseException:
        if status is not None:
            status.unit_finished(label, False)
        raise
    finally:
        with run.lock:
            run.waits.pop(me, None)
    if status is not None:
        status.unit_finished(label, True)
    row = _shared_result(cell.label, value, cell.record, seq=seq)
    row.address = (
        child.address
        if child is not None
        else context._child_address(context.current(), label)
    )
    if handle is not None:
        post_error = _executor._exit_task_hooks(life, handle, row)
        folded = _executor.fold_post_error(row, post_error, clear_state=True)
        if folded is not None:
            _record(row)
            raise folded
    _record(row)
    return value


def _claim(run: _Session, key: Any, me: int, label: str) -> tuple[bool, _Cell]:
    """Take ownership of *key*'s cell, or hand back the cell already there.

    Returns `(claimed, cell)` — `claimed` means this thread must run the work.
    Waiting on a cell is registered in the wait-for graph while the lock is
    held, so a cycle is caught before anyone blocks on it.
    """
    with run.lock:
        cell = run.cells.get(key)
        if cell is None:
            cell = _Cell(me, label)
            run.cells[key] = cell
            return True, cell
        if cell.future.done():
            return False, cell  # already run this run: the memo answers
        if (chain := _deadlock(run, key, me)) is not None:
            raise ChainError(
                f"{label}: this call can never return — "
                f"{' → '.join([*chain, chain[0]])} waits on itself. "
                f"A task cannot call (directly or through another task) "
                f"a task that is waiting for it; declare the order with "
                f"pre= instead."
            )
        run.waits[me] = key
        return False, cell


def work_of(
    task: Any,
    args: Any,
    kwargs: dict[str, Any],
    given: frozenset[str],
    env: dict[str, str],
) -> Any | None:
    """This request's cell key, or `None` when it cannot have one."""
    return _key(task, tuple(args), kwargs, given, env)


def retire(key: Any) -> None:
    """Drop a finished cell so the next request for it runs fresh.

    The one caller is the retry loop: an attempt that failed with attempts
    left must not be the answer a later request receives. Left in place, the
    memo would hand every subsequent attempt the first attempt's failure —
    the body would run once and be reported N times, which is the opposite
    of "each attempt IS a record".

    Only ever called between attempts of work this thread owns, and only on
    a resolved cell. A requester that joined *during* the failed attempt has
    already been handed it: sharing binds to the terminal attempt for
    everyone who asks from here on, not retroactively.
    """
    run = _active
    if run is None or key is None:
        return
    with run.lock:
        cell = run.cells.get(key)
        if cell is not None and cell.future.done():
            del run.cells[key]


def claim(key: Any, label: str) -> tuple[bool, Any]:
    """Take this work, or hand back the cell that already holds it.

    `(claimed, cell)` — `claimed` means the caller must perform the work and
    then `resolve` the cell. Otherwise the cell is someone else's: `join` it.
    `(True, None)` when there is no run to share within, so the caller simply
    proceeds.

    A node comes through here exactly as a body call does. Peeking would not do:
    it can only see a *finished* cell, so two independent requests racing each
    other would both run — the duplication the cell exists to prevent, and
    unpredictable because it depends on which one started first.
    """
    run = _active
    if run is None or key is None:
        return True, None
    return _claim(run, key, threading.get_ident(), label)


def join(cell: Any) -> Any:
    """Wait for the execution that holds this cell, and return its value.

    Raises whatever the work raised, so a request answered by a failed
    execution fails too. Occupies this thread while it waits, bounded by
    `jobs`, exactly as a waiting body call does.
    """
    me = threading.get_ident()
    try:
        return cell.future.result()
    finally:
        if (run := _active) is not None:
            with run.lock:
                run.waits.pop(me, None)


def resolve(
    cell: Any, value: Any, error: BaseException | None, record: Any = None
) -> None:
    """Hand this claimed cell its outcome, so anyone waiting is answered.

    Always called for a claimed cell, on every path out — an unresolved cell
    would leave a waiter blocked for the rest of the run. *record* is the
    execution's sealed row, when there is one: a shared answer is the record
    reused, so a later request's row copies what this one *reported* —
    reviewed title, reported value, audit — not just the body's value.
    """
    if cell is None or cell.future.done():
        return
    cell.record = record
    if error is not None:
        cell.future.set_exception(error)
    else:
        cell.future.set_result(value)


def shared_result(label: str, value: Any, record: Any = None) -> TaskResult:
    """The report entry for a request an earlier execution satisfied."""
    return _shared_result(label, value, record)


def _record(result: TaskResult) -> None:
    """Add a called task's outcome to the run's report — ran, refused, denied,
    or satisfied by an execution the run had already performed."""
    if (run := _active) is not None:
        run.results.append(result)


def _shared_result(
    label: str, value: Any, record: Any = None, seq: int | None = None
) -> TaskResult:
    """The report entry for a request the run had already satisfied.

    Recorded rather than left invisible: the work happened, and a reader (or a
    journal) should see that a second request for it was answered instead of
    silently vanishing. Nothing blocked it — it was answered, instantly or
    after a wait — so it carries no blame; `blocked_by` belongs to rows that
    are holes. The *request* has a real moment, the instant it concluded, and
    that is its `started`: the report seats it where it actually happened,
    exactly as an executed body-callee seats after its caller. `ok` is true
    because the work did succeed, just earlier, so the exit code is untouched.
    """
    import time

    from livery.footman._executor import TaskResult

    row = TaskResult(
        task=label,
        ok=True,
        returned=value,
        state="shared",
        started=time.perf_counter(),
    )
    if record is not None:
        # A shared answer is the record reused: the row a reader sees carries
        # what the execution *reported* — a reviewed title, a rewritten
        # reported value, the audit — never a fresher, less honest copy.
        row.returned = record.returned
        row.title = record.title
        row.audit = list(record.audit)
    # The share concluded after the execution it joined, and the tie-break
    # must never say otherwise: the row's stamp is floored just above its
    # record's, whatever number the request itself took.
    floor = record.seq + 1 if record is not None and record.seq is not None else None
    row.seq = (
        max(seq, floor)
        if seq is not None and floor is not None
        else (seq if floor is None else floor)
    )
    return row


def _claim_unit(label: str) -> Any:
    """Start this request's unit on the live status line, or claim the one
    already counted for it; `None` when no line is running.

    A request is a unit — a scheduler node, a `parallel()` child, a body
    call. The one thing that must never happen is counting a single piece of
    work twice, and `parallel()` cannot tell from the outside whether the
    thunk it was handed is a task call in disguise: a closure is opaque. So
    it counts every child and hands the unit down (`Context.unit_pending`),
    and the first request inside takes it over rather than adding its own.
    Cleared on claim, so a second call in the same thunk — and everything
    the callee goes on to ask for — counts honestly.
    """
    from livery.footman import context

    status = context.active_status()
    if status is None:
        return None
    ctx = context.current()
    if ctx.unit_pending:
        ctx.unit_pending = False
        return None  # counted and displayed by whoever handed it down
    status.unit_added(1)
    status.unit_started(label)
    return status


def _refuse_wide_moment(task: Any) -> None:
    """Refuse a task call from `pre_tasks` / `post_tasks`.

    Both moments sit outside the run — `pre_tasks` at discovery, `post_tasks`
    once the plan is finished — so there is no boundary to give a call: no
    result row, no sharing, no availability gate, and a task declaring `ctx`
    would take the first argument into that slot. Worse for `pre_tasks`, which
    also runs in the child that rebuilds the completion manifest: a call there
    executes the task on a <kbd>Tab</kbd> press.

    A call from *outside footman entirely* (a REPL, an import of the tasks
    module) is untouched — it is the plain function call it looks like.
    """
    from livery.footman._executor import _wide_moment

    moment = _wide_moment.get()
    if moment is None:
        return
    name = _label(task)
    when = (
        "runs at discovery — including in the child that rebuilds the "
        "completion manifest, where it would run on a Tab press"
        if moment == "pre_tasks"
        else "runs after the plan is finished, when the report is already built"
    )
    raise ChainError(
        f"{name} cannot be called from @{moment}, which {when}. Edit the tree "
        f"instead (inv.tasks — say pre=[{name}] on the tasks that need it), or "
        f"move the call to a per-task moment (@pre_task / @post_task), which "
        f"runs inside the run and gives the call a real task boundary."
    )


def _refuse_unrunnable(task: Any) -> None:
    """Refuse the two body calls that cannot be given a task boundary.

    A `serial=`/`exclusive=` task's lane is acquired at the boundary and never
    mid-body — the invariant that keeps the arbiter deadlock-free — and an
    `infinite` task is a future that never resolves.
    """
    name = _label(task)
    if registry.task_lane(task) is not None:
        raise ChainError(
            f"{name} declares a serial/exclusive lane, so it cannot be called "
            f"from a task body — its lane is taken at the task boundary, never "
            f"mid-body. Declare it as pre=[{name}] instead."
        )
    if registry.is_infinite(task):
        raise ChainError(
            f"{name} is an infinite task, so calling it from a task body would "
            f"never return. Run it as its own segment, or declare it as "
            f"pre=[{name}]."
        )


def _run_now(
    task: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    shared: bool = True,
    seg: Any = None,
    child: Any = None,
    handle: Any = None,
    cell: Any = None,
    seq: int | None = None,
    given: frozenset[str] = frozenset(),
) -> Any:
    """Run *task* here and now with full task semantics, and return its value.

    A failure raises in the caller, exactly as a direct call always did — and
    the callee's own `TaskResult` still joins the run's report, so the work is
    visible even though the value came back through Python.

    Availability was checked in `call()`, before any hook fired; `confirm=`
    is the one gate that cannot be resolved up front the way the scheduler
    resolves a segment's — a call is not knowable before the run — so it is
    asked here, at the moment of execution (a request the run has already
    answered never re-asks).
    """
    from livery.footman import _executor, _globals, _schedule, context

    parent = context.current()
    label = _label(task)
    if seg is None:
        seg = _schedule._default_seg(task)
    if (denial := _schedule.confirm_gate(task, seg, parent)) is not None:
        denial.seq = seq
        _record(denial)
        raise denial.error or ChainError(f"{label} was not confirmed")
    if child is None:
        buf = io.StringIO()
        child = parent.child(
            label,
            fn=task,
            cwd=None,  # let the callee's own cwd policy resolve
            sink=buf,
            err_sink=buf,
        )
    else:
        buf = child.sink
    # Presence is the callee's own, never the caller's: the birth inherits
    # every unlisted field, so a child born from a parent that was given
    # `--agent` would otherwise inherit the claim that *it* was given one.
    child.given = given
    # Unsharedness propagates: what this callee asks for is asked the same
    # way, unless that task declares its own answer.
    child.shared = parent.shared and shared
    # A body call is a request like a scheduler node or a parallel() child:
    # a unit on the live status line, counted the moment it becomes real
    # work (a confirm= denial above never was one).
    status = _claim_unit(label)
    try:
        result = _executor.run_bound(
            task, seg, child, list(args), dict(kwargs), as_call=True, handle=handle
        )
    except BaseException:
        if status is not None:
            status.unit_finished(label, False)
        raise
    if status is not None:
        status.unit_finished(label, result.ok)
    result.seq = seq
    _record(result)
    if cell is not None:
        cell.record = result  # the sealed row, for later requests to reuse
    if text := buf.getvalue():
        # The callee ran with its own buffer so its output stays one block.
        # Where that block goes depends on the caller: a capturing parent
        # (`--json`, a document run, a `parallel()` child) owns it and takes
        # it; an uncaptured parent is streaming to the terminal, so it goes
        # there — the same handoff `parallel()` makes for its children,
        # status line warned first because this write bypasses the routers.
        if parent.sink is not None:
            parent.sink.write(text)
        else:
            dest = context.real_stdout()
            with _globals.console_gate():
                if status is not None:
                    status.notify(text)
                dest.write(text)
                dest.flush()
    if result.error is not None:
        # A failure raises in the caller, exactly as a direct call always did.
        raise result.error
    if not result.ok:  # a bare `sys.exit(N)`, which carries no error object
        raise ChainError(f"{label} exited with code {result.code}")
    # The body's own return, never the reported one: what a body caller
    # receives was snapshotted at the body's exit, whatever a reviewer
    # later did to the report.
    return result.body_returned
