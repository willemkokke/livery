"""Build the task DAG and run it — in parallel by default, or sequentially.

The chain segments plus each task's `pre`/`post` form a dependency graph
(deduped by task identity). Independent nodes run concurrently on a thread pool;
a node runs once all its prerequisites have succeeded. footman tasks are almost
always I/O-bound (they shell out through `footman.run`, releasing the GIL),
so threads give real concurrency without process isolation.

Output is buffered per task and flushed atomically on completion, so concurrent
tasks never interleave.
"""

from __future__ import annotations

import io
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from itertools import count
from typing import Any, TextIO

from livery.footman import _describe, _executor, _futures, _globals, _progress, context
from livery.footman._manifest import resolved_signature
from livery.footman._split import ChainError, Segment
from livery.footman.registry import (
    Group,
    Task,
    _Opted,
    cli_name,
    default_group,
    fans_out,
    is_infinite,
    is_interactive,
    keeps_going,
    post_deps,
    pre_deps,
    sharing,
    task_confirm,
    task_name,
    task_retries,
    wants_progress,
    work_key,
)


@dataclass
class _Node:
    fn: Task
    seg: Segment
    key: int
    address: str = ""  # assigned once the plan is final, in creation order
    seq: int | None = None  # run-wide request stamp (the _futures counter)
    deps: set[int] = field(default_factory=set)
    state: str = "pending"  # pending / running / done / skipped
    result: _executor.TaskResult | None = None
    attempts: list[_executor.TaskResult] = field(default_factory=list)
    """The non-terminal attempts of a retried task, oldest first.

    `result` is always the LAST attempt — the one the run's verdict, the
    dependents and any sharer read. Every earlier attempt is a real row with
    its own timing, output and audit, kept here and spliced into the report
    ahead of the terminal row. Nothing is merged and nothing is hidden:
    three attempts are three records, because each attempt *is* a record
    (notes/20260807-timeout-and-retry.md)."""
    forwarded: dict[str, Any] = field(default_factory=dict)  # `forward`ed values in
    forwarded_given: set[str] = field(default_factory=set)  # …which were asked for
    forward_targets: list[_Node] = field(default_factory=list)  # …and out
    keep_going: bool = False  # resolved failure policy for THIS node (per-subtree)
    shared: bool = True  # resolved sharing policy: may an earlier run satisfy it?
    # The group this default action was *reached through*, when known.
    # Default-ness is parent-relative: a provider's default mounted into a
    # consumer group must fan out the group it landed in, not the one it was
    # declared on — task fns are shared between trees, so the declaration
    # stamp cannot know where a mount placed it.
    group: Group | None = None


def _default_seg(fn: Task) -> Segment:
    """The synthetic segment for a task reached by reference rather than typed —
    a bare `pre=`/`post=` dependency, or a body call. Named the way the task is
    *addressed* (`import_` is `fm import`), so a report never shows a spelling
    you could not type."""
    name = cli_name(task_name(fn))
    return Segment(task=name, path=[name])


def _dep_key(fn: Task) -> tuple[int, frozenset[tuple[str, Any]]]:
    """The deduplication identity of a DAG dependency: its base task plus its
    frozen option overrides. A bare task is simply "base with no overrides", so
    it shares one uniform key shape with an `.opts()` reference — and an empty
    `.opts()` (no override at all) collapses onto the bare task by construction,
    with no int-vs-tuple asymmetry. Identical policies share a node (a shared
    prerequisite still runs once); a genuinely different policy is a distinct
    node. One shape with the futures memo: `registry.work_key`, sharing
    included — a differently-shared reference is a distinct node here."""
    return work_key(fn, include_shared=True)


def resolve_inherited(declared: bool | None, inherited: bool) -> bool:
    """One rung-walk for a property that flows down the dependency subtree.

    Own declaration (or `.opts()` override) beats what the requester passed
    down, and unset means "whoever asks decides". Sharing is the first user;
    a later property wanting the same shape — propagate to what a task needs,
    let a task pin its own answer — passes its own reader through here rather
    than copying the ladder.
    """
    return declared if declared is not None else inherited


def _as_task(dep: Task | Group | _Opted) -> Task:
    """A `pre`/`post` dependency may name a runnable group; resolve it to the
    group's default action so it runs (and fans out) like any other task. An
    `.opts()`-wrapped group resolves the same way, carrying its overrides onto
    the default task."""
    if isinstance(dep, _Opted):
        base = dep._opted_base
        if isinstance(base, Group):
            return _Opted(_as_task(base), dep._opted_overrides)
        return dep  # opts on a task is already a valid task reference
    if isinstance(dep, Group):
        if dep.default_task is None:
            raise ChainError(
                f"group {dep.name!r} is a prerequisite but has no @group.default"
            )
        return dep.default_task
    return dep


def _owner_of(dep: Task | Group | _Opted) -> Group | None:
    """The group a `pre=`/`post=` reference reaches a default *through*."""
    if isinstance(dep, _Opted):
        base = dep._opted_base
        return base if isinstance(base, Group) else None
    return dep if isinstance(dep, Group) else None


def _segment_group(root: Group, seg: Segment) -> Group | None:
    """The group a bare-group segment names (`fm lint` — its default runs)."""
    node = root
    for name in seg.path:
        sub = node.groups.get(name)
        if sub is None:
            return None  # the path ends on a task, not a group
        node = sub
    return node


def _build_dag(root: Group, segments: list[Segment]) -> list[_Node]:
    """Nodes for the chain plus their transitive pre/post deps.

    Explicit chain segments each get their own node, so each mention is its own
    request (`build web build api` builds twice — different arguments, different
    work). Shared pre/post prerequisites dedupe by task identity, so a
    prerequisite mounted in twice is one node. Two requests that resolve to the
    same work are one execution whether they are two nodes, a node and a body
    call, or two calls: the second is answered by the first and reported as
    `shared`, unless the task (or the reference) declared `shared=False`. Node
    keys are serial ints; `dep_nodes` maps a task to the node its bare deps
    resolve to.
    """
    nodes: list[_Node] = []
    dep_nodes: dict[object, _Node] = {}
    counter = count()
    seen_explicit: set[object] = set()

    def new_node(fn: Task, seg: Segment, shared: bool) -> _Node:
        node = _Node(fn, seg, next(counter))
        node.shared = shared
        nodes.append(node)
        return node

    def add_dep(
        fn: Task, owner: Group | None = None, *, asked_by: bool = False
    ) -> _Node:
        # The sharing ladder, resolved here rather than in a pass of its own:
        # unlike `keep_going`, sharing decides node *identity*, and identity
        # has to be settled while the node is being made. Own declaration (or
        # `.opts()` override) wins over what the requester passed down.
        shared = resolve_inherited(sharing(fn), asked_by)
        if not shared:
            # Unshared, by definition: its own node per requester, and never
            # registered as the one a later dependency lookup could reuse.
            node = new_node(fn, _default_seg(fn), False)
            node.group = owner
            _link(node)
            return node
        dep_node = dep_nodes.get(_dep_key(fn))
        if dep_node is None:
            dep_node = new_node(fn, _default_seg(fn), True)
            dep_node.group = owner
            dep_nodes[_dep_key(fn)] = dep_node
            _link(dep_node)
        return dep_node

    splits: dict[object, _Node] = {}

    def _thread(
        owner: _Node, dep: _Node, fmap: dict[str, Any], fgiven: frozenset[str]
    ) -> None:
        # A forwarded value reaches only a dispatched task that *declares* the
        # parameter (partial reach). Two dispatchers sending different values
        # to a shared prerequisite are asking for different WORK: one identity
        # rule, everywhere — requests that resolve to different arguments are
        # different nodes, silently and correctly, never a refusal.
        if not fmap:
            return
        declared = {p.name for p in resolved_signature(dep.fn).parameters.values()}
        wanted = {name: value for name, value in fmap.items() if name in declared}
        if not wanted:
            return
        asked = fgiven & wanted.keys()
        # Compatible with what is already threaded: a name the dep has not seen
        # has nothing to disagree with, and one it has must match on *both*
        # channels — the same value asked for and merely defaulted are different
        # requests wherever the callee reads `given()`.
        if all(
            name not in dep.forwarded
            or (
                dep.forwarded[name] == value
                and (name in dep.forwarded_given) == (name in asked)
            )
            for name, value in wanted.items()
        ):
            dep.forwarded.update(wanted)
            dep.forwarded_given.update(asked)
            return
        # The split: this dispatcher's resolved arguments name their own node.
        # Same-argument dispatchers share the split (the plan key mirrors the
        # execution key's normal form: declaration + frozen resolved values,
        # plus which of them were asked for — two dispatchers sending the same
        # value, one because it was typed and one because it was defaulted, are
        # asking for different work wherever the callee reads `given()`).
        frozen = tuple(
            (name, _futures._freeze(wanted[name])) for name in sorted(wanted)
        )
        split_key = (_dep_key(dep.fn), frozen, frozenset(asked))
        clone = splits.get(split_key)
        if clone is None:
            clone = new_node(dep.fn, dep.seg, dep.shared)
            clone.group = dep.group
            clone.deps = set(dep.deps)
            clone.forwarded = dict(wanted)
            clone.forwarded_given = set(asked)
            clone.forward_targets = list(dep.forward_targets)
            clone.keep_going = dep.keep_going
            splits[split_key] = clone
            # The clone forwards onward with its own values, so its subtree
            # sees what THIS request meant.
            clone_map, clone_given = _executor.forward_map(
                clone.fn, clone.seg, clone.forwarded, frozenset(clone.forwarded_given)
            )
            for target in clone.forward_targets:
                _thread(clone, target, clone_map, clone_given)
        if dep.key in owner.deps:  # a pre: the owner now waits on the clone
            owner.deps.discard(dep.key)
            owner.deps.add(clone.key)
        elif owner.key in dep.deps:  # a post: the clone follows the owner
            clone.deps.add(owner.key)
        owner.forward_targets = [
            clone if t is dep else t for t in owner.forward_targets
        ]

    def _link(node: _Node) -> None:
        """Attach *node*'s prerequisites, propagating its sharing policy down:
        a freshly-requested task's inputs are freshly requested too, or
        "fresh" would be a half-truth."""
        pre = list(pre_deps(node.fn))
        # An empty-body group default fans out the group's own tasks: they become
        # implicit prerequisites, so the scheduler runs them (in parallel) and the
        # default's forward-marked values thread into the ones that declare them.
        # The `default` child is the fan-out itself — excluded from its own set.
        group = node.group or default_group(node.fn)
        if group is not None and fans_out(node.fn):
            pre = [
                *(fn for name, fn in group.tasks.items() if name != "default"),
                *pre,
            ]
        for dep in pre:
            d = add_dep(_as_task(dep), _owner_of(dep), asked_by=node.shared)
            node.deps.add(d.key)
            node.forward_targets.append(d)  # forwarding threaded in a later pass
        for dep in post_deps(node.fn):
            d = add_dep(_as_task(dep), _owner_of(dep), asked_by=node.shared)
            d.deps.add(node.key)
            node.forward_targets.append(d)

    for seg in segments:
        fn = _executor.resolve(root, seg.path)
        key = _dep_key(fn)
        # A chain segment is a root: nothing asked for it, so only its own
        # declaration can make it unshared.
        shared = resolve_inherited(sharing(fn), inherited=True)
        existing = dep_nodes.get(key) if shared else None
        if existing is not None and key not in seen_explicit:
            # First explicit mention of a task already mounted in as a bare dep:
            # adopt this segment's args instead of creating a duplicate.
            existing.seg = seg
            seen_explicit.add(key)
            continue
        node = new_node(fn, seg, shared)
        node.group = _segment_group(root, seg)
        if existing is None and node.shared:
            dep_nodes[key] = node
        seen_explicit.add(key)
        _link(node)

    # Thread forwarded values in a second pass, dependents before their deps (the
    # reverse of the run order), so a node's *received* values are complete before
    # it forwards on — this is what makes forwarding chain through a group default
    # into its surfaces. It runs after segment adoption above, so each node's seg
    # (hence its forward map) is final.
    for node in reversed(_toposort(nodes)):
        fmap, fgiven = _executor.forward_map(
            node.fn, node.seg, node.forwarded, frozenset(node.forwarded_given)
        )
        for target in list(node.forward_targets):
            _thread(node, target, fmap, fgiven)
    # Addresses, once the plan is final: creation order IS request order as
    # written (segments, then dependencies as declared, then splits), so the
    # names are deterministic across runs and hosts.
    labels: dict[str, int] = {}
    for node in nodes:
        node.address = context._next_label(labels, node.seg.task)
    # Request stamps, in topological order and from the same run-wide counter
    # body calls draw on: within one instant the report's tie-break then
    # reads cause-before-consequence (a prerequisite outranks its dependent)
    # and plan-before-body-call, deterministically across runs.
    for node in _toposort(nodes):
        node.seq = next(_futures._seq)
    return nodes


def _check_cycles(nodes: list[_Node]) -> None:
    """Reject a cyclic dependency graph with a taught error naming the cycle.

    Without this check the run loop would find no ready node, run nothing, and
    exit 0 — a silent success that lies.
    """
    by_key = {n.key: n for n in nodes}
    state: dict[int, int] = {}  # 1 = on the current path, 2 = fully explored

    def visit(node: _Node, path: list[str]) -> None:
        state[node.key] = 1
        path.append(node.seg.task)
        for dep in node.deps:
            child = by_key.get(dep)
            if child is None:
                continue
            mark = state.get(child.key, 0)
            if mark == 1:
                cycle = [*path[path.index(child.seg.task) :], child.seg.task]
                raise ChainError(
                    f"dependency cycle: {' -> '.join(cycle)} "
                    f"(check the pre/post declarations of these tasks)"
                )
            if mark == 0:
                visit(child, path)
        path.pop()
        state[node.key] = 2

    for node in nodes:
        if state.get(node.key, 0) == 0:
            visit(node, [])


def _toposort(nodes: list[_Node]) -> list[_Node]:
    """Deps before dependents, stable by appearance order."""
    by_key = {n.key: n for n in nodes}
    result: list[_Node] = []
    seen: set[int] = set()

    def visit(node: _Node) -> None:
        if node.key in seen:
            return
        seen.add(node.key)
        for dep in node.deps:
            if dep in by_key:
                visit(by_key[dep])
        result.append(node)

    for node in nodes:
        visit(node)
    return result


def _plain_output(no_color: bool) -> bool:
    """No colour at all: the `--no-color` flag, `NO_COLOR`, or a dumb terminal.

    Per D6 this means the live rewrite is *absent*, not rewritten without escape
    codes — the same output a pipe gets.
    """
    return no_color or "NO_COLOR" in os.environ or os.environ.get("TERM") == "dumb"


def _make_ctx(
    seg: Segment,
    ctx_config: dict[str, Any] | None,
    *,
    sequential: bool,
    capture: bool,
    real: TextIO,
    name_width: int = 0,
    keep_going: bool = False,
    shared: bool = True,
    address: str = "",
) -> context.Context:
    ctx = context.Context(**(ctx_config or {}), passthrough=list(seg.passthrough or []))
    ctx.address = address or seg.task
    ctx.keep_going = keep_going  # per-subtree policy; tags this task's subprocesses
    # The sharing policy this node resolved to, carried so anything its body
    # asks for inherits it: an unshared request asks unshared too.
    ctx.shared = shared
    # One buffer for both streams at task level: the atomic flush keeps this
    # task's stdout/stderr in order, while a run() inside it still splits the
    # step's streams via a temporary swap of the two.
    ctx.sink = ctx.err_sink = None if (sequential and not capture) else io.StringIO()
    # Step lines dress for their *destination*: a buffered block replays
    # onto `real`, so its children style exactly as parallel() children
    # style for their parent's terminal — both engines, one look. Only
    # liveness (sink is None, judged in run()) gates in-place rewrites.
    # `ansi_capable`, not bare tty-ness: the live line repaints with escape
    # codes, and a Windows console that cannot interpret them would show
    # the cursor dance as noise rather than a status line.
    # Two stamps, one measurement: `terminal` is the fact (a capable terminal
    # is at the end of this run's output, nothing captured), `tty` folds the
    # colour policy in on top — `NO_COLOR` unsets how output *dresses*, not
    # whether someone is watching, and the `tty()` reader answers the latter.
    ctx.terminal = not capture and _describe.ansi_capable(real)
    ctx.tty = ctx.terminal and not _plain_output(ctx.no_color)
    # `--color=always` forces colour even off a terminal, but not into a captured
    # envelope: ANSI in `--json` stdout would corrupt it, so capture wins here
    # exactly as it does for `tty` above.
    ctx.force_color = ctx.force_color and not capture
    ctx.task = seg.task
    ctx.name_width = name_width
    return ctx


def resolve_keep_going(root: Group, segments: list[Segment], cli: bool | None) -> bool:
    """A run-wide *summary* of the failure policy: does anything keep going?

    An explicit command-line choice (`-k` / `--fail-fast`) wins; unspecified,
    true if any invoked task — a chain task or a `pre`/`post` prerequisite —
    declares (or `.opts()`-overrides) `keep_going=True`. The scheduler resolves
    the actual *per-node* policy with `_scope_keep_going`; this summary is for
    callers that just want the one-bit answer.
    """
    if cli is not None:
        return cli
    try:
        nodes = _build_dag(root, segments)
    except (KeyError, IndexError, ChainError):
        return False  # a malformed chain surfaces its real error in run_plan
    return any(keeps_going(n.fn) is True for n in nodes)


def _scope_keep_going(nodes: list[_Node], cli: bool | None) -> None:
    """Assign each node its failure policy — the per-subtree scoping.

    A command-line `-k`/`--fail-fast` wins run-wide. Otherwise each node takes
    its own declared (or `.opts()`-overridden) `keep_going`, and a keep-going
    node propagates that down its own subtree — its `pre`/`post` prerequisites
    keep going with it — so a mixed chain honours each side: a keep-going gate
    surfaces all of its own failures while an independent fail-fast task still
    bails on the first. A node's own policy always wins over an inherited one, so
    an explicit fail-fast prerequisite stays a fail-fast boundary. Unspecified
    everywhere is the built-in fail-fast.
    """
    if cli is not None:
        for node in nodes:
            node.keep_going = cli
        return
    inherited: set[int] = set()  # keys a keep-going dependent has reached
    for node in reversed(_toposort(nodes)):  # dependents resolve before their deps
        own = keeps_going(node.fn)  # True / False / None (reads declared + opted)
        node.keep_going = own if own is not None else node.key in inherited
        if node.keep_going:
            inherited.update(node.deps)  # keep this subtree's prerequisites going


def dag_wants_progress(root: Group, segments: list[Segment]) -> bool:
    """Whether every task in the expanded DAG — pre/post deps included —
    consented to timing. One `@task(progress=False)` opts the run out of
    recording and of a determinate bar (the pulse still shows)."""
    try:
        nodes = _build_dag(root, segments)
    except (KeyError, IndexError, ChainError):
        return False  # a malformed chain surfaces its real error in run_plan
    return all(wants_progress(n.fn) for n in nodes)


class NotConfirmed(Exception):
    """A `@task(confirm=…)` gate was declined (or unanswerable off a terminal)."""

    def __init__(self, task: str) -> None:
        super().__init__(f"{task}: not confirmed")


def _ask_confirm(message: str, *, no_input: bool) -> bool:
    """The `@task(confirm=)` gate. Off a terminal or under `--no-input` the
    answer is no — like just and go-task, a confirm fails without `--yes`
    rather than proceeding unasked. Asked on stderr before output routing."""
    if no_input or not context._stdin_is_tty():
        return False
    reply = context._prompt_core(f"{message} [y/N] ", default="n")
    return reply.strip().lower() in ("y", "yes")


def _gate_confirms(
    root: Group, segments: list[Segment], ctx_config: dict[str, Any] | None
) -> tuple[list[Segment], list[_executor.TaskResult], dict[Any, bool]]:
    """Resolve each invoked task's `@task(confirm=)` before the DAG is built —
    asked in invocation order, before any prerequisite runs. A confirmed task
    is kept; a denied one is dropped (so its exclusive pre-deps are pruned with
    it) and reported as a failed 'not confirmed' result, so the run exits
    non-zero. `--yes` auto-confirms every gate. One reference, one question:
    the answers come back so the node-level gate (prerequisites, fan-out
    members) never re-asks what the invocation already answered."""
    cfg = ctx_config or {}
    assume_yes = bool(cfg.get("assume_yes"))
    no_input = bool(cfg.get("no_input"))
    dry_run = bool(cfg.get("dry_run"))
    kept: list[Segment] = []
    denied: list[_executor.TaskResult] = []
    answers: dict[Any, bool] = {}
    for seg in segments:
        fn = _executor.resolve(root, seg.path)
        message = task_confirm(fn)
        if not message:
            kept.append(seg)
            continue
        key = _dep_key(fn)
        if key not in answers:
            answers[key] = _answer_confirm(
                seg.task,
                message,
                assume_yes=assume_yes,
                no_input=no_input,
                dry_run=dry_run,
            )
        if answers[key]:
            kept.append(seg)
        else:
            denied.append(_not_confirmed(seg))
    return kept, denied, answers


def _answer_confirm(
    task: str, message: str, *, assume_yes: bool, no_input: bool, dry_run: bool
) -> bool:
    """The one confirm ladder, whichever moment asks — a segment gate, a
    node gate, a body call: `--yes` answers first, a rehearsal assumes yes
    out loud, and only then is a human asked. Three sites used to spell it
    separately."""
    return (
        assume_yes
        or (dry_run and _dry_confirm(task, message))
        or _ask_confirm(message, no_input=no_input)
    )


def _dry_confirm(task: str, message: str) -> bool:
    """A rehearsal answers every gate yes — a gate answered no would hide
    the very work the rehearsal exists to show — and says so, once."""
    from livery.footman import _globals

    _globals._note(f"dry-confirm:{task}", f"dry-run: {message!r} — assumed yes")
    return True


def _gate_node_confirms(
    nodes: list[_Node],
    ctx_config: dict[str, Any] | None,
    answers: dict[Any, bool],
) -> None:
    """Ask the plan's remaining `@task(confirm=)` gates — prerequisites and
    fan-out members — up front, in dependency order: a task that asks for
    confirmation gets it however it was reached. One reference, one question;
    an answer given for a segment covers every node resolving to the same
    reference. A denial becomes the node's result before anything runs, so
    the node never launches and its dependents skip with the denial as their
    cause."""
    cfg = ctx_config or {}
    assume_yes = bool(cfg.get("assume_yes"))
    no_input = bool(cfg.get("no_input"))
    dry_run = bool(cfg.get("dry_run"))
    for n in _toposort(nodes):
        message = task_confirm(n.fn)
        if not message or n.result is not None:
            continue
        key = _dep_key(n.fn)
        if key not in answers:
            answers[key] = _answer_confirm(
                n.seg.task,
                message,
                assume_yes=assume_yes,
                no_input=no_input,
                dry_run=dry_run,
            )
        if not answers[key]:
            n.result = _not_confirmed(n.seg)
            n.result.address = n.address
            n.result.seq = n.seq
            n.state = "done"


def _not_confirmed(seg: Segment) -> _executor.TaskResult:
    # Denied at a moment (before the run for a segment, at the call for a call),
    # so it carries that moment and needs no special placement.
    return _executor.TaskResult(
        task=seg.task,
        ok=False,
        code=1,
        error=NotConfirmed(seg.task),
        started=time.perf_counter(),
    )


def _in_request_order(
    results: list[_executor.TaskResult],
) -> list[_executor.TaskResult]:
    """The run's results in the order the work was created.

    Request order has a place for everything: a task reached by a body call
    has no slot in a dependency listing, but it drew a stamp from the same
    run-wide counter the plan did, at the moment it was asked for. Sequential
    runs are unchanged, since requesting in dependency order *is* the order
    they run in.

    It is also the only ordering a reader can rely on. The clock cannot do
    this job: two independent tasks in a parallel run start in whatever order
    the pool hands them workers, so a report keyed on `started` reshuffles
    between runs of the same command — and did, intermittently, on a
    free-threaded build. Sorting by start time within a 10ms bucket hid it
    for pairs that shared a bucket and left the pair that straddled a
    boundary to chance, because the buckets are aligned to `perf_counter`'s
    arbitrary origin rather than to anything about the run.

    Something that never began has no stamp of its own, so it sits directly
    after whatever prevented it — the report reads as cause, then consequence.
    With nothing to blame (a gate answered before any task ran) it comes first.
    """
    # `started` breaks a tie only for a row minted outside the request
    # pipeline, which carries no stamp to sort by.
    ran = sorted(
        (r for r in results if r.started is not None),
        key=lambda r: (
            r.seq if r.seq is not None else float("inf"),
            r.started or 0.0,
        ),
    )
    never: list[_executor.TaskResult] = [r for r in results if r.started is None]
    ordered = [r for r in never if not r.blocked_by] + ran
    for result in (r for r in never if r.blocked_by):
        after = [i for i, r in enumerate(ordered) if r.task == result.blocked_by]
        ordered.insert(after[-1] + 1 if after else len(ordered), result)
    return ordered


def confirm_gate(
    fn: Task, seg: Segment, ctx: context.Context
) -> _executor.TaskResult | None:
    """Ask *fn*'s `@task(confirm=)` gate now; a denial is the refusal to report.

    The scheduler resolves a segment's gate before the run starts, so the human
    answers everything up front. A body call cannot be known that early, so it
    asks at the moment of the call instead — the gate itself is the same, and a
    task that asks for confirmation gets it however it was reached.
    """
    message = task_confirm(fn)
    if not message:
        return None
    if _answer_confirm(
        seg.task,
        message,
        assume_yes=ctx.assume_yes,
        no_input=ctx.no_input,
        dry_run=ctx.dry_run,
    ):
        return None
    return _not_confirmed(seg)


def run_plan(
    root: Group,
    segments: list[Segment],
    *,
    sequential: bool = False,
    keep_going: bool | None = None,
    capture: bool = False,
    ctx_config: dict[str, Any] | None = None,
    estimate: _progress.Estimate | None = None,
    progress: bool = True,
    jobs: int = 0,
) -> list[_executor.TaskResult]:
    """Build and run the DAG; return results in dependency order."""
    with _futures.session():
        results = _run_plan(
            root,
            segments,
            sequential=sequential,
            keep_going=keep_going,
            capture=capture,
            ctx_config=ctx_config,
            estimate=estimate,
            progress=progress,
            jobs=jobs,
        )
        # A task reached by a body call ran as a real task, so its result joins
        # the run's. Every execution the run performed is reported, however it
        # was reached — and the whole report reads in the order it happened.
        return _in_request_order([*results, *_futures.collected()])


def _run_plan(
    root: Group,
    segments: list[Segment],
    *,
    sequential: bool = False,
    keep_going: bool | None = None,
    capture: bool = False,
    ctx_config: dict[str, Any] | None = None,
    estimate: _progress.Estimate | None = None,
    progress: bool = True,
    jobs: int = 0,
) -> list[_executor.TaskResult]:
    context.reset_abort()  # clear any latched fail-fast from a previous run
    segments, denied, answers = _gate_confirms(root, segments, ctx_config)
    nodes = _build_dag(root, segments)
    _check_cycles(nodes)
    _gate_node_confirms(nodes, ctx_config, answers)
    _scope_keep_going(nodes, keep_going)  # per-node failure policy (tri-state + scope)
    # Ask-serial, run-parallel — as early as correct: every promptable ask()
    # across the DAG answers up front (confirms were gated above), so the
    # human answers once and walks away; only a live-suggest question waits
    # for its prerequisites, resolving at node launch. Think-time can never
    # land inside any task's recorded duration, and an unanswerable question
    # (--no-input, no terminal) refuses the run before anything starts.
    ask_ctx = context.Context(**(ctx_config or {}))
    try:
        for n in _toposort(nodes):
            if n.result is not None:  # denied its confirm: it will not run
                continue
            _executor.resolve_asks(n.fn, n.seg, ask_ctx)
    except ValueError as exc:
        raise ChainError(str(exc)) from exc
    # One node has nothing to parallelise — run it on the sequential-live
    # path instead: output streams as it happens, and run()'s TTY mode
    # (colour, in-place step rewrite) applies. `fm check` is this shape. An
    # interactive task also forces sequential: it owns the terminal, so it
    # can't share with parallel siblings (a human-wait is the bottleneck).
    # An interactive task owns the real terminal: it forces sequential (it can't
    # share with parallel siblings) and suppresses the status line, whose
    # clear-line repaints would otherwise erase its prompt.
    # An interactive task neither forces the run sequential nor costs it
    # the status line: it claims the arbiter's console lane (one owner,
    # real stdio), the parallel pool keeps running around it captured, and
    # the lane suspends the status line for exactly the ownership window.
    sequential = sequential or len(nodes) == 1
    # A run containing an infinite task has no progress to show — its
    # duration isn't late, it's intentional. The status line yields to a
    # one-time hint (printed at the node's start) saying how this ends.
    endless = any(is_infinite(n.fn) for n in nodes)
    # Resolve colour once for the whole run and publish it into os.environ, so
    # every tool — subprocess (inherits) and in-process (reads it) — sees one
    # answer set once, with no per-call environment patching. sys.stdout is the
    # run's real stdout here (routing captures it next), so its tty-ness is the
    # same input `_make_ctx` uses per node.
    cfg = ctx_config or {}
    try:
        stdout_tty = sys.stdout.isatty()
    except Exception:
        stdout_tty = False
    colour_on = context.run_colour_on(
        no_color=bool(cfg.get("no_color")),
        force_color=bool(cfg.get("force_color")),
        capture=capture,
        isatty=stdout_tty,
    )
    with context.routing() as (real, err):
        # Decide the status line and the infinite-task hint from the *user's*
        # environment — before `color_environment` publishes footman's own
        # NO_COLOR/FORCE_COLOR for the children, which `_plain_output` would
        # otherwise read back and mistake for a user's monochrome request (a
        # `fm check > log` still wants its spinner on the stderr terminal).
        status = _make_status(
            err,
            ctx_config,
            capture,
            estimate,
            # An interactive run keeps its status line now: the console
            # lane suspends it while a wizard owns the terminal and resumes
            # it after — instead of sacrificing it for the whole run.
            progress and not endless,
        )
        hint_err = (
            err
            if sequential
            and endless
            and not capture
            and not cfg.get("quiet")
            and err.isatty()
            and not _plain_output(bool(cfg.get("no_color")))
            else None
        )
        if status is not None:
            status.unit_added(sum(1 for n in nodes if n.result is None))
            context.set_status(status)  # parallel() and the routers find it
            status.open()
        try:
            with context.color_environment(colour_on):
                # The process-globals routers arm inside the colour publish,
                # so the pinned env snapshot carries it; refcounted, so a
                # nested run (a task body driving a Runner) shares one install.
                _globals.install()
                try:
                    if sequential:
                        try:
                            _run_sequential(
                                nodes, real, capture, ctx_config, status, hint_err
                            )
                        except BaseException:
                            # Ctrl-C mid-task: the running child is group-isolated,
                            # so it missed the terminal's SIGINT — reap its tree by
                            # hand before the interrupt propagates.
                            context.terminate_live_children()
                            raise
                    else:
                        _run_parallel(
                            nodes, real, err, capture, ctx_config, status, jobs
                        )
                finally:
                    _globals.uninstall()
        finally:
            if status is not None:
                context.set_status(None)
                status.close()
    # The abort latch is run-scoped: leaving it set after a normal return
    # would make `_register_child` reap a *later* bare run()'s child (a
    # latched fail-fast from one test killing the next test's echo). The
    # Ctrl-C/internal-error path unwinds by exception and keeps the latch,
    # which the interrupted-reporting above this layer still reads.
    context.reset_abort()
    ordered = _toposort(nodes)
    by_key = {n.key: n for n in ordered}
    already = {r.task for r in denied}

    def _lost(key: int) -> bool:
        # Judged on final states, after the run: a dependency is the cause
        # whether it failed having completed, was cancelled mid-flight, was
        # itself skipped — or never ran under a name the run denied (its
        # refusal is a pre-run row, so its node reads as merely pending;
        # the result-None guard keeps a same-named node that *did* run from
        # being blamed for it).
        m = by_key.get(key)
        return m is not None and (
            m.state == "skipped"
            or (m.result is None and m.seg.task in already)
            or (m.result is not None and not m.result.ok)
        )

    def _blocked_by(n: _Node) -> str:
        for dep in n.deps:
            if _lost(dep):
                return by_key[dep].seg.task
        # A fail-fast sweep: nothing of its own was lost — blame the run's
        # first genuine failure, the same cause the exit code reports.
        for m in ordered:
            if m.result is not None and not m.result.ok:
                return m.seg.task
        return ""

    # A node the run never started still gets a row — `skipped`, blamed on
    # what prevented it — so the report and the `--json` envelope account
    # for every node the plan had, not only the ones that ran. Marked
    # `skipped` by a sweep, or simply left pending when the run stopped
    # reaching for new work: either way it never began, so it has no moment
    # of its own and `_in_request_order` seats it after its cause. A node whose
    # confirm was denied already has its refusal row.
    for n in ordered:
        # Launch latency, derivable after the fact: a node became eligible
        # the moment its last prerequisite finished — same monotonic clock
        # `started` reads, so `started - eligible` is the time it sat
        # waiting for a worker. Roots have no prerequisites and no latency.
        if n.result is None or n.result.started is None or not n.deps:
            continue
        # The edges themselves, by row address — what a profile draws its
        # dependency arrows from, and the `after` a `--json` reader gets.
        n.result.after = tuple(
            m.result.address or m.seg.task
            for d in n.deps
            if (m := by_key.get(d)) is not None and m.result is not None
        )
        finishes = [
            m.result.started + m.result.duration
            for d in n.deps
            if (m := by_key.get(d)) is not None
            and m.result is not None
            and m.result.started is not None
        ]
        if finishes:
            n.result.eligible = max(finishes)
    skipped = [
        _executor.TaskResult(
            task=n.seg.task,
            # Addresses are assigned when the plan is final, not when a task
            # runs — so a row that never ran still has one, and `blocked_by`
            # and `after` point at names a reader can look up.
            address=n.address,
            ok=False,
            state="skipped",
            blocked_by=_blocked_by(n),
        )
        for n in ordered
        if n.result is None and n.seg.task not in already
    ]
    # A retried task contributes its earlier attempts ahead of its terminal
    # row, so the report reads in the order the work happened: attempt, then
    # attempt, then the outcome. They are ordinary rows — real timing, real
    # output, their own audit — distinguished only by `state="retried"`.
    ran_rows: list[_executor.TaskResult] = []
    for n in ordered:
        if n.result is None:
            continue
        for earlier in n.attempts:
            # One request, so one stamp — the sort then falls to `started`,
            # which is chronological because a node's attempts run in
            # sequence on one thread.
            if earlier.seq is None:
                earlier.seq = n.result.seq
            ran_rows.append(earlier)
        ran_rows.append(n.result)
    return denied + ran_rows + skipped


def _run_sequential(
    nodes: list[_Node],
    real: TextIO,
    capture: bool,
    ctx_config: dict[str, Any] | None,
    status: _progress.StatusLine | None,
    err: TextIO | None = None,
) -> None:
    done: dict[int, bool] = {}
    failed = False
    width = max((_describe.display_width(n.seg.task) for n in nodes), default=0)
    for node in _toposort(nodes):
        if node.result is not None:  # answered before the run: a denied confirm
            done[node.key] = node.result.ok
            failed = failed or not node.result.ok
            continue
        if any(not done.get(d) for d in node.deps) or (failed and not node.keep_going):
            node.state = "skipped"
            if status is not None:
                status.unit_skipped(node.seg.task)
            continue
        ctx = _make_ctx(
            node.seg,
            ctx_config,
            sequential=True,
            capture=capture,
            real=real,
            name_width=width,
            keep_going=node.keep_going,
            shared=node.shared,
            address=node.address,
        )
        if status is not None:
            status.unit_started(node.seg.task)
        if err is not None and is_infinite(node.fn):
            hint = f"{node.seg.task} runs until you stop it — Ctrl-C"
            err.write(_describe.dim(hint, True) + "\n")
            err.flush()
        node.result = _run_attempts(node, ctx)
        if node.result.seq is None or node.seq is None:
            node.result.seq = node.seq
        else:  # a shared row keeps its above-the-record floor
            node.result.seq = max(node.result.seq, node.seq)
        node.state = "done"
        if status is not None:
            status.unit_finished(node.seg.task, node.result.ok)
        done[node.key] = node.result.ok
        failed = failed or not node.result.ok


def _run_attempts(node: _Node, ctx: Any) -> _executor.TaskResult:
    """Run one node's body, retrying a failed attempt while attempts remain.

    Every attempt is a real record. The non-terminal ones are kept on the
    node (`attempts`) and reported as their own `retried` rows; the last one
    becomes `node.result` — the row the run's verdict, the dependents and any
    sharer read. Nothing is merged, so `records are never fiction` holds
    without special pleading: each attempt IS a record.

    The retry lives here, above `run_task`, for the property ruling 1 needs:
    a retriable failure never reaches the scheduler's failure handling, so it
    latches no fail-fast and blocks no dependent. There is no failure to
    react to until the attempts are spent — *"it hasn't failed yet"*.

    What is NOT re-run: `pre=` (prerequisites are their own nodes and already
    ran), and every gate that guards an attempt rather than performing it —
    availability, `expose`, and above all the confirm prompt, which
    resolved before this is called. A retry that re-prompts a human is a bug
    read as broken rather than as an oversight.

    Fail-fast still wins over a pending retry: an abort latched by a
    *different* task's terminal failure means "no new work", and an unstarted
    attempt is new work.

    **A timeout footman could not stop is terminal**, whatever attempts
    remain. With `stopped=True` the worst a retry costs is a repeated side
    effect: attempt 1 finished doing whatever it did, then attempt 2 does it
    again. With `stopped=False` the body is *still running*, so attempt 2
    would race a live copy of itself — two writers on one file, two calls
    against one API, at the same time. That is a fork, not a retry.

    The practical cost is worse than the semantic one: `retries=3` on a body
    with no checkpoints leaks three hung workers, which under a bounded
    `--jobs` exhausts the pool and can wedge the run at exit on non-daemon
    threads. And it cannot succeed anyway — a body that blew its deadline
    without reaching a checkpoint has no checkpoint to reach, so the next
    attempt hangs identically.

    This does not contradict ruling 3 ("no theory about what deserves
    retry"). That forbids footman judging whether a *failure* deserves
    another chance; this is footman observing it cannot coherently *start*
    another attempt, because the previous one never ended — the same
    category as fail-fast beating a pending retry. Deliberately not
    configurable: a flag would be API surface for a case unsafe by
    construction (notes/20260807-timeout-and-retry.md).
    """
    left = task_retries(node.fn)
    attempt = 0
    while True:
        result = _executor.run_task(
            node.fn,
            node.seg,
            ctx,
            node.forwarded,
            frozenset(node.forwarded_given),
            attempt=attempt,
        )
        if result.ok or left <= 0:
            return result
        if result.timed_out and result.after_deadline != "stopped":
            # Only a *stopped* timeout is retriable — cut off mid-flight, so
            # possibly slow for a transient reason and definitively not
            # running now. A `completed` one is terminal through futility:
            # the body finished, so there is nothing transient to retry, it
            # outran the deadline once and will again, and another attempt
            # repeats work that already happened.
            #
            # This is not footman forming a theory about which failures
            # deserve another chance (ruling 3) — it is a structural fact
            # about whether another attempt can coherently start, the same
            # category as fail-fast beating a pending retry.
            return result
        result.state = "retried"
        node.attempts.append(result)
        left -= 1
        attempt += 1
        # Each attempt keeps the node's request stamp — they are one request —
        # so the report's `(seq, started)` sort would place them by start time
        # alone. That is already chronological, but only because every attempt
        # of one node runs on one thread; pinning the stamp here says so
        # deliberately rather than relying on it.


def _make_status(
    err: TextIO,
    ctx_config: dict[str, Any] | None,
    capture: bool,
    estimate: _progress.Estimate | None,
    enabled: bool,
) -> _progress.StatusLine | None:
    """The run's live line — bar or pulse — or None when it can't show.

    Status is commentary, so it lives on stderr: piping stdout
    (`fm check > log`) keeps the line visible on the terminal. Applies to
    every run shape, single node included — that's `fm check`.
    """
    cfg = ctx_config or {}
    if (
        not enabled
        or capture
        or cfg.get("quiet")
        or not err.isatty()  # the status stream's own tty-ness decides
        or _plain_output(bool(cfg.get("no_color")))
    ):
        return None
    # Past the guard the run is colourful by definition (no_color/NO_COLOR/dumb
    # all bail above), so the live line always renders with escapes.
    return _progress.StatusLine(err, estimate, color=True)


def _run_parallel(
    nodes: list[_Node],
    real: TextIO,
    err: TextIO | None,
    capture: bool,
    ctx_config: dict[str, Any] | None,
    status: _progress.StatusLine | None,
    jobs: int,
) -> None:
    by_key = {n.key: n for n in nodes}
    lock = threading.Lock()
    # A confirm denied before the run is already a failure on the board.
    failed = any(n.result is not None and not n.result.ok for n in nodes)
    width = max((_describe.display_width(n.seg.task) for n in nodes), default=0)

    def dep_ok(n: _Node) -> bool:
        return all(
            by_key[d].state == "done"
            and (res := by_key[d].result) is not None
            and res.ok
            for d in n.deps
            if d in by_key
        )

    def dep_lost(n: _Node) -> bool:
        def lost(m: _Node) -> bool:
            return m.state == "skipped" or (
                m.state == "done" and (res := m.result) is not None and not res.ok
            )

        return any(lost(by_key[d]) for d in n.deps if d in by_key)

    def run_node(n: _Node) -> None:
        ctx = _make_ctx(
            n.seg,
            ctx_config,
            sequential=False,
            capture=capture,
            real=real,
            name_width=width,
            keep_going=n.keep_going,
            shared=n.shared,
            address=n.address,
        )
        if is_interactive(n.fn) and not capture:
            # A console owner runs on the real terminal even inside the
            # parallel pool: the arbiter's console lane guarantees one owner,
            # and captured siblings' flushes queue on the gate below.
            ctx.sink = ctx.err_sink = None
        n.result = _run_attempts(n, ctx)
        if n.result.seq is None or n.seq is None:
            n.result.seq = n.seq
        else:  # a shared row keeps its above-the-record floor
            n.result.seq = max(n.result.seq, n.seq)
        if not capture and ctx.sink is not None:
            # Flush this task's buffered output as one block — queued while a
            # wizard owns the terminal, so it never splats over a prompt.
            with _globals.console_gate(), lock:
                # The per-task sink is always the StringIO buffer set above;
                # the isinstance states the invariant where the type (TextIO)
                # can't.
                sink = ctx.sink
                blob = sink.getvalue() if isinstance(sink, io.StringIO) else ""
                if status is not None:
                    # A direct real-stream write (bypasses the routers): the
                    # status line clears itself and tracks the column.
                    status.notify(blob)
                real.write(blob)
                real.flush()

    # A one-node plan has no sibling to overlap with, so the pool would be a
    # thread to wait on and nothing to gain. Run it on this thread instead and
    # skip the import below entirely — the single most common shape (`fm test`,
    # a bare task with no prerequisites) stops paying `concurrent.futures`.
    # The *regime* is unchanged: run_node still builds a parallel context, so a
    # task behaves the same whether it was named alone or in a chain.
    if len(nodes) == 1:
        node = nodes[0]
        if node.result is None and not failed:
            node.state = "running"
            if status is not None:
                status.unit_started(node.seg.task)
            try:
                run_node(node)
            except BaseException:
                # Same reap as the pool's abort path: the child is
                # group-isolated and missed the terminal's SIGINT.
                context.terminate_live_children()
                raise
            node.state = "done"
            if status is not None:
                status.unit_finished(
                    node.seg.task, bool(node.result and node.result.ok)
                )
        return

    # Imported at the pool, not at module scope: `concurrent.futures` costs
    # ~5.9 ms (it drags `logging` through `traceback`), and a line that never
    # runs a plan — `--list`, `--help`, a refusal — should not pay it.
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

    with ThreadPoolExecutor(
        max_workers=jobs if jobs > 0 else None, thread_name_prefix="fm-worker"
    ) as pool:
        futures: dict[Any, _Node] = {}
        try:
            while True:
                for n in nodes:
                    if n.state == "pending" and (
                        dep_lost(n) or (failed and not n.keep_going)
                    ):
                        n.state = "skipped"
                        if status is not None:
                            status.unit_skipped(n.seg.task)
                for n in nodes:
                    if n.state == "pending" and dep_ok(n):
                        n.state = "running"
                        if status is not None:
                            status.unit_started(n.seg.task)
                        futures[pool.submit(run_node, n)] = n
                if not futures:
                    break
                completed, _ = wait(list(futures), return_when=FIRST_COMPLETED)
                for fut in completed:
                    node = futures.pop(fut)
                    # `run_task` catches task exceptions itself; anything the
                    # future carries (KeyboardInterrupt in the worker, an
                    # internal error) must propagate, not read as success.
                    exc = fut.exception()
                    if exc is not None:
                        raise exc
                    node.state = "done"
                    ok = bool(node.result and node.result.ok)
                    if status is not None:
                        status.unit_finished(node.seg.task, ok)
                    if not ok:
                        failed = True
                        # True fail-fast: stop launching new nodes (the skip pass
                        # above) *and* reap the FAIL-FAST siblings already in
                        # flight, so a doomed branch dies now instead of waiting
                        # out a five-minute test suite. `failfast_only` spares a
                        # keep-going task in a mixed run — it isn't doomed.
                        context.terminate_live_children(failfast_only=True)
        except BaseException:
            # Abort (Ctrl-C, or an internal error surfaced above): drop
            # everything not yet started, then kill in-flight subprocess trees.
            # This must happen *before* the pool's `with` exit joins the worker
            # threads: each is blocked in communicate() on a group-isolated child
            # that no longer receives the terminal's SIGINT, so without an
            # explicit kill the join — and the whole Ctrl-C — would hang. The app
            # layer reports "interrupted" and exits 130; run_plan's finally
            # clears the status line.
            context.terminate_live_children()
            pool.shutdown(wait=False, cancel_futures=True)
            raise
