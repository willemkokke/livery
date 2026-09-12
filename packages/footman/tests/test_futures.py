"""Body calls as run-scoped futures: sharing, waiting, refusals, reporting."""

from __future__ import annotations

import threading
from typing import Annotated, Literal

import pytest

from livery.footman import _executor, registry
from livery.footman._split import ChainError
from livery.footman._step import step
from livery.footman.params import ask, between, env, stdin
from livery.footman.registry import Group, RegistrationError
from livery.footman.testing import Runner


def test_a_base_exception_answers_the_cell_it_claimed(tmp_path):
    """An unresolved cell blocks its sharer for the rest of the run.

    `_call` deliberately does not catch `BaseException` — a KeyboardInterrupt
    must abort the run, not become a task failure — but leaving by that door
    skipped the `resolve` that answers anyone waiting. The claimant died and
    the sharer waited for a value nobody would ever set, past a second Ctrl-C,
    since the wait is not interruptible.

    Driven as a subprocess with a timeout: the failure mode is a hang, and a
    hang in-process takes the suite with it.
    """
    import os
    import subprocess
    import sys
    import textwrap

    (tmp_path / "tasks.py").write_text(
        textwrap.dedent("""
        import time
        from livery.footman import task

        class Abrupt(BaseException):
            pass

        @task
        def claimant():
            time.sleep(0.4)      # the sharer reaches the cell first
            raise Abrupt("out through the cell")

        @task
        def sharer():
            time.sleep(0.15)
            claimant()           # joins the cell the node claimed
        """)
    )
    env = {**os.environ, "FOOTMAN_CACHE_DIR": str(tmp_path / ".cache")}
    env.pop("VIRTUAL_ENV", None)
    try:
        done = subprocess.run(
            [sys.executable, "-m", "livery.footman", "claimant", "sharer"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:  # pragma: no cover - the regression
        pytest.fail("the sharer is still waiting on a cell nobody resolved")
    assert done.returncode != 0
    assert "Abrupt" in done.stderr  # failed by what failed the claimant


def drive(reg: Group, line: str):
    """Run *line* against *reg* through the in-process CLI."""
    return Runner().invoke(line, tasks=reg)


def test_a_body_callee_carries_only_its_own_sections():
    # The callee's context is its own birth, not a window onto the caller's:
    # its row must never snapshot sections the caller recorded before the
    # call, and the caller's row keeps its own either way.
    import livery.footman as footman

    reg = Group("root")

    @reg.task
    def callee():
        with footman.section("theirs"):
            pass

    @reg.task
    def caller():
        with footman.section("mine"):
            pass
        callee()

    result = drive(reg, "caller")
    assert result.ok, result.stderr
    rows = {r.task: r for r in result.results}
    assert [s.name for s in rows["callee"].sections] == ["theirs"]
    assert [s.name for s in rows["caller"].sections] == ["mine"]


def test_a_body_call_shares_the_runs_execution():
    # `pre=[build]` then `build()` in the body is ONE build: the prerequisite's
    # body's return is memoised under (task, arguments), so the call is a
    # cache hit rather than a second execution.
    reg = Group("root")
    runs: list[str] = []

    @reg.task
    def build() -> str:
        runs.append("build")
        return "dist/app"

    @reg.task(pre=[build])
    def publish():
        artifact = build()  # the value pre= could never hand over
        print(f"published {artifact}")

    result = drive(reg, "publish")
    assert result.ok, result.stderr
    assert runs == ["build"]  # once, not twice
    assert "published dist/app" in result.stdout


def test_a_body_call_without_the_task_in_the_plan_runs_it():
    # No prerequisite: the call is the first execution, so it happens — and
    # returns its value like a plain call always did.
    reg = Group("root")
    runs: list[str] = []

    @reg.task
    def measure() -> int:
        runs.append("measure")
        return 7

    @reg.task
    def report():
        print(f"got {measure()}")

    result = drive(reg, "report")
    assert result.ok, result.stderr
    assert runs == ["measure"] and "got 7" in result.stdout


def test_the_memo_keys_on_arguments_not_just_the_task():
    # Same task, different arguments, is different work; the same arguments
    # spelled positionally or by keyword is the same work.
    reg = Group("root")
    seen: list[str] = []

    @reg.task
    def render(target: str = "web") -> str:
        seen.append(target)
        return target.upper()

    @reg.task
    def build_all():
        assert render("web") == "WEB"
        assert render(target="web") == "WEB"  # same work, other spelling
        assert render("api") == "API"  # different work
        # Omitting is *not* the same request as passing the default: `given()`
        # tells the two apart, so identity has to as well, or a body that
        # branches on presence would be answered by the wrong execution.
        assert render() == "WEB"

    assert drive(reg, "build-all").ok
    assert seen == ["web", "api", "web"]


def test_the_memo_keys_on_the_environment_too():
    # The other input a body can observe. A caller that wrote its env before
    # calling must not be answered by an execution that never saw the
    # variable — that would be a wrong cache hit with no error. The DAG ran
    # `probe` from the pinned snapshot; the caller's write makes its request
    # different work, honestly, and the report shows both runs.
    import os

    reg = Group("root")
    seen: list[str | None] = []

    @reg.task
    def probe() -> str:
        value = os.environ.get("FUTURES_TARGET")
        seen.append(value)
        return value or "unset"

    @reg.task(pre=[probe])
    def deploy():
        os.environ["FUTURES_TARGET"] = "arm64"  # scoped to this task
        assert probe() == "arm64"  # a fresh run that really saw the write

    result = drive(reg, "deploy")
    assert result.ok, result.stderr
    assert seen == [None, "arm64"]  # the prerequisite, then the fresh run


def test_an_untouched_environment_still_shares():
    # Every DAG node starts from the pinned snapshot, and a caller that never
    # wrote its env holds an equal copy — equal digests, one cell. The whole
    # point of keying on content: the common case must stay a cache hit.
    reg = Group("root")
    runs: list[str] = []

    @reg.task
    def build() -> str:
        runs.append("build")
        return "dist/app"

    @reg.task(pre=[build])
    def publish():
        assert build() == "dist/app"

    result = drive(reg, "publish")
    assert result.ok, result.stderr
    assert runs == ["build"]  # once: the body call was answered by the memo


def test_equal_env_content_shares_a_single_execution():
    # Sharing is keyed by env *content*, not by lineage or claim order: two
    # siblings that made the same write converge on one cell.
    import os

    reg = Group("root")
    runs: list[str] = []

    @reg.task
    def probe() -> str:
        runs.append(os.environ.get("FUTURES_TARGET") or "?")
        return "ok"

    @reg.task
    def left():
        os.environ["FUTURES_TARGET"] = "arm64"
        probe()

    @reg.task
    def right():
        os.environ["FUTURES_TARGET"] = "arm64"
        probe()

    result = drive(reg, "left right")
    assert result.ok, result.stderr
    assert runs == ["arm64"]  # one execution answered both


def test_different_env_values_split_the_cell():
    import os

    reg = Group("root")
    runs: list[str] = []

    @reg.task
    def probe() -> str:
        runs.append(os.environ.get("FUTURES_TARGET") or "?")
        return "ok"

    @reg.task
    def arm():
        os.environ["FUTURES_TARGET"] = "arm64"
        probe()

    @reg.task
    def intel():
        os.environ["FUTURES_TARGET"] = "x86_64"
        probe()

    result = drive(reg, "arm intel")
    assert result.ok, result.stderr
    assert sorted(runs) == ["arm64", "x86_64"]  # each caller's run saw its own


def test_the_env_digest_is_order_blind_and_value_sensitive():
    from livery.footman import _futures

    a = _futures._env_digest({"A": "1", "B": "2"})
    assert _futures._env_digest({"B": "2", "A": "1"}) == a  # insertion order
    assert _futures._env_digest({"A": "1", "B": "3"}) != a  # a value counts
    assert _futures._env_digest({"A": "1"}) != a  # so does presence
    assert _futures._env_digest({}) != a


def test_an_unshared_task_runs_on_every_call():
    reg = Group("root")
    runs: list[int] = []

    @reg.task(shared=False)
    def notify() -> None:
        runs.append(1)

    @reg.task
    def ship():
        notify()
        notify()
        notify()

    assert drive(reg, "ship").ok
    assert len(runs) == 3


def test_an_unshared_prerequisite_is_never_shared():
    # `shared=False` is one rule for every spelling:
    # two dependents each get their own run, exactly as two calls would. No
    # one has to remember whether they reached the task by declaration or by
    # call.
    reg = Group("root")
    runs: list[int] = []

    @reg.task(shared=False)
    def stamp() -> None:
        runs.append(1)

    @reg.task(pre=[stamp])
    def build_web(): ...

    @reg.task(pre=[stamp])
    def build_api(): ...

    assert drive(reg, "build-web build-api").ok
    assert len(runs) == 2  # one per requester


def test_unsharedness_propagates_down_the_subtree():
    # "Give me a fresh build" has to mean its inputs are fresh too, or fresh
    # is a half-truth: the property flows from a requester into what it needs.
    reg = Group("root")
    runs: list[str] = []

    @reg.task
    def compile_() -> None:
        runs.append("compile")

    @reg.task(pre=[compile_])
    def bundle() -> None:
        runs.append("bundle")

    @reg.task(shared=False, pre=[bundle])
    def release_() -> None:
        runs.append("release")

    @reg.task(pre=[bundle])
    def preview() -> None:
        runs.append("preview")

    assert drive(reg, "release preview").ok  # `release_` addresses as `release`
    # `release-` was requested freshly, so its bundle (and that bundle's
    # compile) are its own; `preview` gets the shared pair.
    assert runs.count("bundle") == 2
    assert runs.count("compile") == 2


def test_an_own_declaration_beats_an_inherited_one():
    # The pin for an expensive step that genuinely is reusable: declaring
    # shared=True keeps it shared even under an unshared parent.
    reg = Group("root")
    runs: list[str] = []

    @reg.task(shared=True)
    def fetch_deps() -> None:
        runs.append("fetch")

    @reg.task(shared=False, pre=[fetch_deps])
    def build_web() -> None: ...

    @reg.task(shared=False, pre=[fetch_deps])
    def build_api() -> None: ...

    assert drive(reg, "build-web build-api").ok
    assert runs.count("fetch") == 1  # shared despite two unshared parents


def test_a_per_call_override_asks_for_one_unshared_run():
    # `.opts(shared=False)` is the per-request spelling, and works on a
    # declared edge just as well as on a call.
    reg = Group("root")
    runs: list[int] = []

    @reg.task
    def stamp() -> int:
        runs.append(1)
        return len(runs)

    @reg.task
    def go():
        first = stamp()  # runs, and fills the cell
        again = stamp()  # shared: the cell answers
        own = stamp.opts(shared=False)()  # asked unshared: runs again
        assert (first, again, own) == (1, 1, 2)

    assert drive(reg, "go").ok
    assert len(runs) == 2


def test_the_first_result_is_the_one_the_run_remembers():
    # First-write-wins: an unshared re-run gets its own value, but never
    # rewrites what the run already remembers, so a later request is stable.
    reg = Group("root")
    runs: list[int] = []

    @reg.task
    def stamp() -> int:
        runs.append(1)
        return len(runs)

    @reg.task
    def go():
        assert stamp() == 1
        assert stamp.opts(shared=False)() == 2  # its own, unshared value
        assert stamp() == 1  # …and the remembered first result stands

    assert drive(reg, "go").ok
    assert len(runs) == 2


def test_a_failing_callee_raises_in_the_caller():
    reg = Group("root")

    @reg.task
    def flaky() -> None:
        raise RuntimeError("boom")

    @reg.task
    def caller():
        flaky()

    result = drive(reg, "caller")
    assert not result.ok
    assert "boom" in result.stderr


def test_a_body_call_is_reported_as_its_own_unit():
    # The body-call hole in the result table closes: the callee ran as a real
    # task, so it has a TaskResult of its own.
    reg = Group("root")

    @reg.task
    def inner() -> str:
        return "v1"

    @reg.task
    def outer():
        inner()

    result = drive(reg, "outer")
    assert result.ok, result.stderr
    assert [r.task for r in result.results] == ["outer", "inner"]
    assert next(r for r in result.results if r.task == "inner").returned == "v1"


def test_self_recursion_is_a_taught_refusal():
    # Today this is a stack overflow; as a future it is a wait on itself,
    # caught and named.
    reg = Group("root")

    @reg.task
    def loop():
        loop()

    result = drive(reg, "loop")
    assert not result.ok
    assert "can never return" in result.stderr and "loop" in result.stderr


def test_a_call_cycle_between_two_tasks_is_taught():
    reg = Group("root")

    @reg.task
    def ping():
        pong()

    @reg.task
    def pong():
        ping()

    result = drive(reg, "ping")
    assert not result.ok
    assert "can never return" in result.stderr


def test_calling_a_serial_task_from_a_body_is_refused():
    # The arbiter lane is acquired at the task boundary, never mid-body —
    # the invariant that keeps it deadlock-free. The refusal names the fix.
    reg = Group("root")

    @reg.task(serial=True)
    def migrate(): ...

    @reg.task
    def deploy():
        migrate()

    result = drive(reg, "deploy")
    assert not result.ok
    assert "serial/exclusive lane" in result.stderr
    assert "pre=[migrate]" in result.stderr


def test_calling_an_infinite_task_from_a_body_is_refused():
    reg = Group("root")

    @reg.task(infinite=True)
    def serve(): ...

    @reg.task
    def dev():
        serve()

    result = drive(reg, "dev")
    assert not result.ok
    assert "infinite task" in result.stderr


def test_a_call_outside_a_run_is_a_plain_call():
    # Importing a tasks file and calling a function must keep working: no run,
    # no machinery, no context — just the function.
    reg = Group("root")

    @reg.task
    def double(n: int = 2) -> int:
        return n * 2

    assert double(21) == 42


def test_unhashable_arguments_run_rather_than_pretend_to_be_cached():
    # A value with no frozen form can't key a cell. The call runs — honest
    # work every time — instead of guessing at a cache hit.
    reg = Group("root")
    runs: list[int] = []

    class Opaque:
        __hash__ = None  # type: ignore[assignment]

    @reg.task
    def consume(thing: object = None) -> None:
        runs.append(1)

    @reg.task
    def go():
        blob = Opaque()
        consume(blob)
        consume(blob)

    assert drive(reg, "go").ok
    assert len(runs) == 2


def test_list_arguments_key_by_value():
    # Collections get a frozen shape, so the same list contents are the same
    # work — the common case for a `list[str]` parameter.
    reg = Group("root")
    runs: list[int] = []

    @reg.task
    def lint(paths: list[str] | None = None) -> int:
        runs.append(1)
        return len(paths or [])

    @reg.task
    def gate():
        assert lint(["a", "b"]) == 2
        assert lint(["a", "b"]) == 2  # same contents: the memo answers

    assert drive(reg, "gate").ok
    assert len(runs) == 1


def test_two_threads_calling_one_task_share_a_single_execution():
    # The second caller blocks on the first's future rather than duplicating
    # the work; both see the same value.
    reg = Group("root")
    runs: list[int] = []
    seen: list[str] = []

    @reg.task
    def slow() -> str:
        runs.append(1)
        threading.Event().wait(0.05)
        return "once"

    @reg.task
    def fan():
        from livery.footman import parallel

        def one() -> None:
            seen.append(slow())

        parallel(step(one)(), step(one)(), step(one)())

    assert drive(reg, "fan").ok
    assert len(runs) == 1
    assert seen == ["once", "once", "once"]


def test_sharing_is_a_tri_state():
    # Unset is a third state, not False: it means "whoever asks decides", which
    # is what lets the property propagate and what makes shared=True a
    # deliberate pin rather than a no-op.
    reg = Group("root")

    @reg.task(shared=False)
    def always(): ...

    @reg.task(shared=True)
    def never(): ...

    @reg.task
    def unset(): ...

    assert registry.sharing(always) is False
    assert registry.sharing(never) is True
    assert registry.sharing(unset) is None
    # `.opts()` overrides the declaration for one request.
    assert registry.sharing(unset.opts(shared=False)) is False
    assert registry.sharing(always.opts(shared=True)) is True


def test_the_machinery_refuses_before_it_memoises():
    # A refusal is not an execution: nothing is cached, so the second call
    # teaches the same lesson rather than silently returning None.
    reg = Group("root")

    @reg.task(serial=True)
    def locked(): ...

    with pytest.raises(ChainError):
        from livery.footman import _futures

        with _futures.session():
            _futures.call(locked, (), {})


# --- one gate, however the task was reached -----------------------------------
# Nobody should have to know whether a task arrived through the declared DAG or
# the runtime one, so every gate a prerequisite passes, a call passes too.


def test_an_unavailable_task_refuses_a_body_call():
    reg = Group("root")

    @reg.task
    @registry.requires(lambda: False, reason="no toolchain here")
    def compile_(): ...

    @reg.task
    def build():
        compile_()

    result = drive(reg, "build")
    assert not result.ok
    assert "no toolchain here" in result.stderr
    # …and the refusal is reported, not swallowed into the caller's failure.
    assert any("compile" in r.task and not r.ok for r in result.results)


def test_an_unavailable_task_refuses_a_prerequisite_the_same_way():
    reg = Group("root")

    @reg.task
    @registry.requires(lambda: False, reason="no toolchain here")
    def compile_(): ...

    @reg.task(pre=[compile_])
    def build(): ...

    result = drive(reg, "build")
    assert not result.ok
    assert "no toolchain here" in result.stderr


def test_a_confirm_gate_is_asked_at_a_body_call():
    # The scheduler asks a segment's gate up front; a call can't be known that
    # early, so it asks at the call — but it does ask.
    reg = Group("root")
    ran: list[int] = []

    @reg.task(confirm="Really deploy?")
    def deploy() -> None:
        ran.append(1)

    @reg.task
    def release_():
        deploy()

    denied = drive(reg, "release")
    assert not denied.ok  # no terminal, no input: the gate cannot be answered
    assert not ran
    confirmed = Runner().invoke("--yes release", tasks=reg)  # a global, so it leads
    assert confirmed.ok, confirmed.stderr
    assert ran == [1]  # --yes answers it, exactly as it does for a segment


# --- the report reads in the order the run happened ---------------------------


def test_results_are_chronological_and_a_call_lands_where_it_ran():
    # A dependency listing has no slot for a task reached by a body call; a
    # chronological one does, because the call had a moment.
    reg = Group("root")

    @reg.task
    def setup_() -> str:
        return "ready"

    @reg.task
    def probe() -> str:
        return "probed"

    @reg.task(pre=[setup_])
    def deploy():
        probe()  # runs here, between deploy's start and its end

    result = drive(reg, "deploy")
    assert result.ok, result.stderr
    assert [r.task for r in result.results] == ["setup", "deploy", "probe"]
    stamps = [r.started for r in result.results]
    assert all(s is not None for s in stamps)
    timed = [s for s in stamps if s is not None]
    assert timed == sorted(timed)  # ordered by when they began


def test_a_refusal_lands_at_the_moment_it_refused():
    # An unavailable task never ran, but it *was* asked at a point in time, so
    # it needs no placement rule — it carries that moment and sorts by it.
    # Ordered by dependency here on purpose: between two INDEPENDENT tasks a
    # chronological report is only as deterministic as the run was, and
    # asserting an order there would be asserting the thread scheduler.
    reg = Group("root")

    @reg.task
    def first_() -> None: ...

    @reg.task(pre=[first_])
    @registry.requires(lambda: False, reason="not here")
    def second() -> None: ...

    @reg.task(pre=[second])
    def both(): ...

    result = drive(reg, "both")
    assert not result.ok
    refusal = next(r for r in result.results if r.task == "second")
    assert refusal.started is not None  # it had a moment of its own
    assert not refusal.blocked_by  # nothing prevented it; it refused itself
    order = [r.task for r in result.results]
    assert order.index("first") < order.index("second")  # causally ordered


def test_something_that_never_began_sits_after_what_prevented_it():
    # The placement rule, on its own: with no moment of its own, a result goes
    # directly after the one it blames, so the report reads cause then
    # consequence. (Skipped nodes become results in their own right with the
    # post_tasks hook; the ordering contract is here and pinned now.)
    from livery.footman import _schedule
    from livery.footman._executor import TaskResult

    ran_first = TaskResult(task="build", ok=False, code=1, started=1.0)
    ran_later = TaskResult(task="notify", ok=True, started=2.0)
    skipped = TaskResult(task="publish", ok=False, blocked_by="build")
    denied = TaskResult(task="deploy", ok=False)  # nothing to blame: it leads

    ordered = _schedule._in_request_order([ran_later, skipped, ran_first, denied])
    assert [r.task for r in ordered] == ["deploy", "build", "publish", "notify"]


# --- a request the run had already satisfied is reported, not invisible -------


def test_a_memo_hit_is_reported_as_shared():
    # The work happened once; the second request was answered rather than
    # performed, and the report says so instead of the request vanishing.
    reg = Group("root")
    runs: list[int] = []

    @reg.task
    def build() -> str:
        runs.append(1)
        return "dist/app"

    @reg.task(pre=[build])
    def publish():
        build()  # answered from the run's own earlier execution

    result = drive(reg, "publish")
    assert result.ok, result.stderr
    assert len(runs) == 1
    states = [(r.task, _executor.reported_state(r)) for r in result.results]
    # The request has its own moment — the instant it was answered, mid-way
    # through publish's body — so it seats exactly where an executed
    # body-callee would: after the caller that made it.
    assert states == [("build", "ok"), ("publish", "ok"), ("build", "shared")]
    hit = result.results[2]
    assert hit.returned == "dist/app"  # it carries the value it answered with
    assert hit.ok and hit.started is not None  # answered at a real moment
    assert not hit.blocked_by  # nothing blocked it: blame belongs to holes


def test_a_shared_entry_does_not_change_the_exit_code():
    reg = Group("root")

    @reg.task
    def probe() -> int:
        return 3  # an int return is a segment's exit code, a call's value

    @reg.task
    def gate():
        assert probe() == 3
        assert probe() == 3  # the second is answered by the first

    result = drive(reg, "gate")
    assert result.ok and result.exit_code == 0
    assert [_executor.reported_state(r) for r in result.results] == [
        "ok",
        "ok",
        "shared",
    ]


def test_reported_state_resolves_one_word_from_the_parts():
    # `ok`/`code` stay the exit-code channel; this is the reported spelling, so
    # a new outcome becomes another value here rather than another boolean.
    from livery.footman._executor import TaskResult

    assert _executor.reported_state(TaskResult(task="a", ok=True)) == "ok"
    assert _executor.reported_state(TaskResult(task="a", ok=False)) == "failed"
    assert (
        _executor.reported_state(TaskResult(task="a", ok=False, cancelled=True))
        == "cancelled"
    )
    assert (
        _executor.reported_state(TaskResult(task="a", ok=True, state="shared"))
        == "shared"
    )


def test_the_ladder_resolver_is_shared():
    # Sharing is the first user of the down-the-subtree ladder; a later
    # property (a cross-run "never cached") reuses this rather than copying it.
    from livery.footman import _schedule

    assert _schedule.resolve_inherited(True, False) is True  # own wins
    assert _schedule.resolve_inherited(False, True) is False  # even over a parent
    assert _schedule.resolve_inherited(None, True) is True  # else inherited
    assert _schedule.resolve_inherited(None, False) is False  # else shared


# --- one rule, whichever way the task was reached ------------------------------


def test_a_repeated_chain_segment_is_one_execution_when_shared():
    # Each mention is its own request, and identical requests to a shared task
    # are one execution — the same rule a prerequisite and a body call follow.
    reg = Group("root")
    runs: list[int] = []

    @reg.task
    def check() -> None:
        runs.append(1)

    result = drive(reg, "check check")
    assert result.ok, result.stderr
    assert len(runs) == 1
    assert [_executor.reported_state(r) for r in result.results] == ["ok", "shared"]


def test_a_repeated_chain_segment_runs_twice_when_unshared():
    # …and `shared=False` makes every mention run, predictably.
    reg = Group("root")
    runs: list[int] = []

    @reg.task(shared=False)
    def notify() -> None:
        runs.append(1)

    result = drive(reg, "notify notify")
    assert result.ok, result.stderr
    assert len(runs) == 2
    assert [_executor.reported_state(r) for r in result.results] == ["ok", "ok"]


def test_a_node_reuses_what_a_body_call_already_did():
    # The mirror of `pre=[build]` plus `build()`: here the call comes first and
    # the node second, and it still happens once. Nobody has to know which way
    # round a task was reached.
    reg = Group("root")
    runs: list[int] = []

    @reg.task
    def survey() -> str:
        runs.append(1)
        return "measured"

    @reg.task
    def early():
        assert survey() == "measured"  # the first execution, from a body

    result = drive(reg, "early survey")  # …then survey as a segment of its own
    assert result.ok, result.stderr
    assert len(runs) == 1
    states = [(r.task, _executor.reported_state(r)) for r in result.results]
    # `early` and the survey execution race on parallel workers, so only the
    # invariants are asserted: one execution, one share, and the share
    # concluded after the execution it joined — never their order vs `early`.
    assert sorted(states) == [
        ("early", "ok"),
        ("survey", "ok"),
        ("survey", "shared"),
    ]
    assert states.index(("survey", "ok")) < states.index(("survey", "shared"))


def test_a_share_copies_what_a_body_claimed_execution_reported():
    # The claimed body call hands its sealed row to the cell BEFORE the
    # future resolves, so a later sharer copies the reviewed report — not a
    # bare value with the title lost.
    from livery.footman import pre_record

    reg = Group("root")

    def label(view):
        view.title = "surveyed: 3 sites"

    @reg.task
    def survey() -> str:
        return "measured"

    pre_record(label)(survey)

    @reg.task
    def early():
        assert survey() == "measured"

    result = drive(reg, "early survey")
    assert result.ok, result.stderr
    shared = next(r for r in result.results if _executor.reported_state(r) == "shared")
    assert shared.title == "surveyed: 3 sites"
    assert [m for m, _a, _c in shared.audit] == ["body", "review"]


def test_a_different_policy_is_different_work_and_runs():
    # A policy override makes a genuinely different invocation, so it is never
    # answered by the shared one — the sharing flag is the only override left
    # out of the work's identity, because it says "do not reuse", not
    # "this is different".
    reg = Group("root")
    runs: list[int] = []

    @reg.task
    def step() -> None:
        runs.append(1)

    @reg.task(pre=[step])
    def plain(): ...

    @reg.task(pre=[step.opts(atomic=True)])
    def guarded(): ...

    assert drive(reg, "plain guarded").ok
    assert len(runs) == 2


def test_an_unshared_execution_is_not_the_runs_answer():
    # An unshared run neither reads a cell nor becomes one. Otherwise whether a
    # shared request reused would depend on which node the scheduler started
    # first, and how much work a run does would stop being predictable.
    reg = Group("root")
    runs: list[int] = []

    @reg.task
    def compile_() -> None:
        runs.append(1)

    @reg.task(shared=False, pre=[compile_])
    def own(): ...

    @reg.task(pre=[compile_])
    def plain(): ...

    assert drive(reg, "own plain").ok
    # `own` unshares its subtree, so it compiles for itself; `plain` gets a
    # shared compile. Two, in either scheduling order.
    assert len(runs) == 2


def test_two_racing_requests_are_one_execution_whichever_starts_first():
    # The reason a node joins the cell protocol rather than peeking at it: a
    # peek can only see a *finished* cell, so an independent node and a body
    # call racing each other both ran — duplicated work, and unpredictable
    # because it depended on which started first. Whoever arrives second waits.
    reg = Group("root")
    runs: list[int] = []

    @reg.task
    def survey() -> str:
        runs.append(1)
        threading.Event().wait(0.05)  # wide enough for the other to arrive
        return "measured"

    @reg.task
    def early():
        assert survey() == "measured"

    for line in ("early survey", "survey early"):  # both orders, same answer
        runs.clear()
        result = drive(reg, line)
        assert result.ok, result.stderr
        assert len(runs) == 1, f"{line}: ran {len(runs)} times"
        states = [_executor.reported_state(r) for r in result.results]
        assert states.count("shared") == 1


# --- a call binds like a segment ---------------------------------------------


def test_a_ctx_tasks_call_keys_on_the_callers_arguments():
    # `ctx` is injected at the task boundary, never passed by a caller, so a
    # call's arguments bind against the signature *without* it. Before that,
    # the first positional value landed in the `ctx` slot and every call keyed
    # on the defaults — `render("api")` memo-hit `render("web")`.
    reg = Group("root")
    seen: list[str] = []

    @reg.task
    def render(ctx, target: str = "web") -> str:
        seen.append(target)
        return target.upper()

    @reg.task
    def build_all():
        assert render("web") == "WEB"
        assert render("api") == "API"  # different work, not a stale hit
        # ctx is injected, never passed — the static signature still lists it.
        # Its own execution, because omitting a parameter and passing its
        # default are different requests (see the memo-keying test).
        assert render() == "WEB"  # type: ignore[call-arg]

    assert drive(reg, "build-all").ok
    assert seen == ["web", "api", "web"]


def test_a_call_reads_the_env_fallback_when_the_parameter_is_omitted(monkeypatch):
    # An omitted parameter consults the same ladder binding would: the env
    # string is coerced and validated exactly as a CLI token is.
    monkeypatch.setenv("FUTURES_JOBS", "4")
    reg = Group("root")
    got: list[int] = []

    @reg.task
    def build(jobs: Annotated[int, env("FUTURES_JOBS")] = 1) -> int:
        got.append(jobs)
        return jobs

    @reg.task
    def go():
        assert build() == 4

    assert drive(reg, "go").ok
    assert got == [4]  # the int the env string coerced to, not the default


def test_an_explicit_value_beats_env_even_when_it_equals_the_default(monkeypatch):
    # The handle sees the call before Python applies defaults, so passing the
    # default's value explicitly is not the same request as omitting it.
    monkeypatch.setenv("FUTURES_TARGET", "prod")
    reg = Group("root")
    seen: list[str] = []

    @reg.task
    def build(target: Annotated[str, env("FUTURES_TARGET")] = "dev") -> str:
        seen.append(target)
        return target

    @reg.task
    def go():
        assert build("dev") == "dev"  # explicit: env never consulted
        assert build() == "prod"  # omitted: env wins over the default

    assert drive(reg, "go").ok
    assert seen == ["dev", "prod"]


def test_a_segment_and_a_call_that_resolve_the_same_values_share(monkeypatch):
    # Resolution happens before the work key is computed, so a prerequisite
    # bound from env and a body call that omits the parameter are one work.
    monkeypatch.setenv("FUTURES_TARGET", "prod")
    reg = Group("root")
    runs: list[str] = []

    @reg.task
    def build(target: Annotated[str, env("FUTURES_TARGET")] = "dev") -> str:
        runs.append(target)
        return target

    @reg.task(pre=[build])
    def deploy():
        assert build() == "prod"  # the prerequisite's execution, not a second

    assert drive(reg, "deploy").ok
    assert runs == ["prod"]


def test_an_explicit_value_runs_the_annotations_checks():
    # The annotation is the contract however the task is asked for: a value
    # the constraints refuse on the command line is refused at a call too,
    # taught with the call's own shape.
    reg = Group("root")

    @reg.task
    def scale(replicas: Annotated[int, between(1, 10)] = 1) -> int:
        return replicas

    @reg.task
    def grow():
        scale(20)

    result = drive(reg, "grow")
    assert not result.ok
    assert "scale(replicas=…) must be between 1 and 10 (got 20)" in result.stderr


def test_a_choices_annotation_refuses_a_wrong_explicit_value():
    reg = Group("root")

    @reg.task
    def deploy(target: Literal["dev", "prod"] = "dev") -> str:
        return target

    @reg.task
    def go():
        deploy("staging")  # type: ignore[arg-type]  # deliberately wrong

    result = drive(reg, "go")
    assert not result.ok
    assert "deploy(target=…) must be one of dev|prod" in result.stderr


def test_an_explicit_value_is_validated_but_never_coerced():
    # A Python caller passed a real value under the signature's types; the
    # static contract polices those, so no string-to-type coercion happens.
    reg = Group("root")
    got: list[object] = []

    @reg.task
    def build(jobs: Annotated[int, env("FUTURES_UNSET_VAR")] = 1) -> None:
        got.append(jobs)

    @reg.task
    def go():
        build("5")  # type: ignore[arg-type]  # deliberately wrong, stays a str

    assert drive(reg, "go").ok
    assert got == ["5"] and type(got[0]) is str


def test_a_bad_env_value_fails_the_caller_with_the_env_label(monkeypatch):
    # The env string flows through the same coercion and bounds a CLI token
    # gets, and the refusal names the variable it came from.
    monkeypatch.setenv("FUTURES_JOBS", "40")
    reg = Group("root")

    @reg.task
    def build(jobs: Annotated[int, env("FUTURES_JOBS"), between(1, 10)] = 1) -> None:
        raise AssertionError("never runs")

    @reg.task
    def go():
        build()

    result = drive(reg, "go")
    assert not result.ok
    assert "from $FUTURES_JOBS" in result.stderr
    assert "must be between 1 and 10" in result.stderr


def test_a_call_reads_stdin_for_an_omitted_parameter():
    # CLI beats stdin beats env, and the boundary payload serves a body call
    # exactly as it serves a prerequisite.
    reg = Group("root")
    got: list[str] = []

    @reg.task
    def ingest(name: Annotated[str, stdin("name")] = "anon") -> str:
        got.append(name)
        return name

    @reg.task
    def go():
        assert ingest() == "piped"

    result = Runner().invoke("go", tasks=reg, stdin='{"name": "piped"}')
    assert result.ok, result.stderr
    assert got == ["piped"]


def test_a_required_ask_parameter_off_a_terminal_fails_the_caller():
    # A required parameter nothing filled would prompt at the call — the same
    # internal lane `confirm=` uses. Off a terminal it is the same taught
    # refusal the CLI path gives.
    reg = Group("root")

    @reg.task
    def name_it(name: Annotated[str, ask(prompt="Name?")]) -> str:
        return name

    @reg.task
    def go():
        name_it()  # type: ignore[call-arg]  # nothing filled it: the refusal under test

    result = drive(reg, "go")
    assert not result.ok
    assert "--name is required" in result.stderr


# --- body calls on the live status line ---------------------------------------


class _FakeStatus:
    """Collects unit events the way the real StatusLine receives them."""

    def __init__(self):
        self.events: list[tuple[str | int | bool, ...]] = []
        self.total = 0
        self.counted: dict[str, tuple[int, int]] = {}

    def unit_added(self, count: int = 1) -> None:
        self.total += count
        self.events.append(("added", count))

    def unit_started(self, name: str) -> None:
        self.events.append(("started", name))

    def unit_finished(self, name: str, ok: bool) -> None:
        self.events.append(("finished", name, ok))

    def unit_skipped(self, name: str) -> None:
        self.events.append(("skipped", name))

    def unit_counted(self, name: str, done: int, total: int) -> None:
        pass

    def notify(self, s: str) -> None:
        pass

    def paint(self) -> None:
        pass

    def suspend(self) -> None:
        pass

    def resume(self) -> None:
        pass


def _with_status(reg: Group, line: str) -> _FakeStatus:
    """Drive *line* with a fake status installed; the scheduler builds no
    line of its own off a terminal, so the fake stays the run's status and
    collects exactly what the futures layer feeds it."""
    from livery.footman import context

    status = _FakeStatus()
    context.set_status(status)
    try:
        result = drive(reg, line)
        assert result.ok, result.stderr
    finally:
        context.set_status(None)
    return status


def test_a_body_call_is_a_unit_on_the_status_line():
    reg = Group("root")

    @reg.task
    def build() -> str:
        return "dist/app"

    @reg.task
    def publish():
        build()

    status = _with_status(reg, "publish")
    assert ("started", "build") in status.events
    assert ("finished", "build", True) in status.events
    assert status.total == 1  # the call; the scheduler's node fed no fake


def test_a_shared_body_call_is_a_unit_too():
    # The request satisfied by the prerequisite's execution still counts —
    # a scheduler node satisfied by sharing counts, so a call does too.
    reg = Group("root")

    @reg.task
    def build() -> str:
        return "dist/app"

    @reg.task(pre=[build])
    def publish():
        build()  # shared with the prerequisite's execution

    status = _with_status(reg, "publish")
    assert ("started", "build") in status.events
    assert ("finished", "build", True) in status.events


def test_parallel_task_children_count_once():
    # parallel(build) is one request: the machinery counts it; parallel()
    # counts only children it alone can see (plain thunks).
    from livery.footman import parallel

    reg = Group("root")

    @reg.task
    def compile() -> int:
        return 0

    @reg.task
    def fanout():
        parallel(compile, step(lambda: 0)())

    status = _with_status(reg, "fanout")
    started = [e for e in status.events if e[0] == "started"]
    assert started.count(("started", "compile")) == 1
    assert ("started", "…") in started  # the lambda: parallel()'s own unit
    assert status.total == 2  # one task request + one anonymous thunk


# --- one piece of work, one unit ---------------------------------------------
#
# `parallel()` counts every child it is handed; the first request inside a
# child claims that unit instead of counting a second one. What the child
# does cannot be read from the outside — a closure is opaque — so no spelling
# may count differently from another.


def _units(reg: Group, line: str) -> tuple[int, list[str | int | bool]]:
    status = _with_status(reg, line)
    return status.total, [e[1] for e in status.events if e[0] == "started"]


def test_every_parallel_spelling_counts_the_same():
    # The regression this pins: a lambda wrapping a call used to count twice —
    # once as parallel()'s anonymous thunk, once as the request inside it.

    from livery.footman import parallel

    for label, body in (
        ("handle", lambda w: parallel(w)),
        ("item", lambda w: parallel(step(lambda: w(tag="l"))())),
    ):
        reg = Group("root")

        @reg.task
        def work(tag: str = "plain"): ...

        @reg.task
        def go():
            body(work)

        total, _started = _units(reg, "go")
        assert total == 1, f"{label} counted {total} units for one piece of work"


def test_a_plain_thunk_keeps_its_own_unit():
    # Nothing claims it, so parallel()'s unit stands — a thunk that runs no
    # task is the only thing on the line for that child.
    from livery.footman import parallel

    reg = Group("root")
    ran: list[str] = []

    def plain():
        ran.append("plain")

    @reg.task
    def go():
        parallel(step(plain)())

    total, started = _units(reg, "go")
    assert (total, started, ran) == (1, ["plain"], ["plain"])


def test_a_thunk_that_runs_two_tasks_counts_both():
    # The claim is one-shot: the first request takes the child's unit, the
    # second is its own piece of work.
    from livery.footman import parallel

    reg = Group("root")

    @reg.task
    def leaf(tag: str = "x"): ...

    @reg.task
    def go():
        parallel(step(lambda: (leaf("a"), leaf("b")))())

    total, _started = _units(reg, "go")
    assert total == 2


def test_the_claim_does_not_reach_the_callee():
    # A parallel child's task claims the unit; the calls that task then makes
    # are its own requests and count for themselves.
    from livery.footman import parallel

    reg = Group("root")

    @reg.task
    def leaf(tag: str = "x"): ...

    @reg.task
    def mid():
        leaf("inner-a")
        leaf("inner-b")

    @reg.task
    def go():
        parallel(mid)

    total, started = _units(reg, "go")
    assert total == 3  # mid claimed the child's unit; its two calls added theirs
    assert started == ["mid", "leaf", "leaf"]


def test_a_shared_request_is_still_its_own_unit():
    # Two identical calls: one executes, one is satisfied by it. Both are
    # requests, so both count — the wait is visible.
    from livery.footman import parallel

    reg = Group("root")

    @reg.task
    def leaf(tag: str = "x"): ...

    @reg.task
    def go():
        parallel(step(lambda: leaf("same"))(), step(lambda: leaf("same"))())

    total, _started = _units(reg, "go")
    assert total == 2


# --- the parallel block -------------------------------------------------------


def test_a_block_runs_its_calls_together_and_hands_back_values():
    from livery.footman import parallel

    reg = Group("root")
    order: list[str] = []

    @reg.task
    def build(target: str = "x") -> str:
        order.append(target)
        return f"dist/{target}"

    @reg.task
    def go():
        with parallel() as p:
            build("web")
            build("api")
        assert p.results == ["dist/web", "dist/api"]  # written order, not finish
        assert list(p) == [0, 0]  # and still the exit codes parallel() returns

    result = drive(reg, "go")
    assert result.ok, result.stderr
    assert sorted(order) == ["api", "web"]


def test_a_queued_value_cannot_be_used_inside_the_block():
    from livery.footman import parallel

    reg = Group("root")

    @reg.task
    def build(target: str = "x") -> str:
        return f"dist/{target}"

    @reg.task
    def go():
        with parallel():
            artifact = build("web")
            artifact.upper()  # no value yet: taught, never a silent None

    result = drive(reg, "go")
    assert not result.ok
    assert "has not run yet" in result.stderr


def test_an_item_built_in_the_block_but_never_handed_over_is_taught():
    # Building runs nothing, so an item born inside the block that never
    # reaches p() is a forgotten hand-off — the block refuses to run
    # rather than silently dropping the work.
    from livery.footman import parallel, step

    reg = Group("root")
    ran: list[str] = []

    @step
    def tidy() -> None:
        ran.append("tidy")

    @reg.task
    def build() -> None:
        ran.append("build")

    @reg.task
    def go():
        with parallel():
            build()
            tidy()  # built, never handed over: dead

    result = drive(reg, "go")
    assert not result.ok
    assert "never handed to it: tidy" in result.stderr
    assert ran == []  # nothing ran, the queued task included


def test_an_item_pumped_in_place_inside_the_block_is_not_dead():
    from livery.footman import parallel, step

    reg = Group("root")
    ran: list[str] = []

    @step
    def tidy() -> None:
        ran.append("tidy")

    @reg.task
    def go():
        with parallel() as p:
            p(tidy.opts(title="handed")())
            tidy()()  # run in place: claimed by the call, not the block

    result = drive(reg, "go")
    assert result.ok, result.stderr
    assert ran == ["tidy", "tidy"]


def test_a_queued_call_is_a_real_request():
    # Queued or not, what runs is a task: it earns a row and shares with the
    # run, exactly as a call written outside the block would.
    from livery.footman import parallel

    reg = Group("root")
    runs: list[str] = []

    @reg.task
    def build() -> str:
        runs.append("build")
        return "dist/app"

    @reg.task(pre=[build])
    def go():
        with parallel() as p:
            build()
        assert p.results == ["dist/app"]

    result = drive(reg, "go")
    assert result.ok, result.stderr
    assert runs == ["build"]  # shared with the prerequisite's execution


def test_a_failing_queued_call_fails_the_block():
    from livery.footman import fail, parallel

    reg = Group("root")

    @reg.task
    def boom():
        fail("nope")

    @reg.task
    def go():
        with parallel():
            boom()

    result = drive(reg, "go")
    assert not result.ok


def test_a_raising_block_body_runs_nothing():
    from livery.footman import parallel

    reg = Group("root")
    runs: list[str] = []

    @reg.task
    def build():
        runs.append("build")

    @reg.task
    def go():
        with parallel():
            build()
            raise RuntimeError("changed my mind")

    result = drive(reg, "go")
    assert not result.ok
    assert runs == []  # queued, never launched


def test_an_empty_splat_is_still_an_empty_list_of_codes():
    # `parallel(*thunks)` over an empty sequence is the documented dynamic
    # fan-out; it must not become a context manager.
    from livery.footman import parallel

    assert parallel(*[]) == []


def test_also_queues_a_plain_callable_into_the_block():
    # The straggler case: one lambda or plain function alongside the tasks,
    # in the same fan-out, its value in the same results list.
    from livery.footman import parallel

    reg = Group("root")
    ran: list[str] = []

    @reg.task
    def build(target: str = "x") -> str:
        return f"dist/{target}"

    @reg.task
    def go():
        with parallel() as p:
            build("web")
            p(step(ran.append, title="straggler")("straggler"))
            p(step(lambda: "from a lambda", title="lifted")())
        assert p.results == ["dist/web", None, "from a lambda"]

    result = drive(reg, "go")
    assert result.ok, result.stderr
    assert ran == ["straggler"]


def test_queueing_an_item_outside_a_block_is_taught():
    from livery.footman import parallel

    with pytest.raises(RuntimeError, match=r"inside the `with`"):
        parallel()(step(lambda: 0, title="x")())


def test_the_partial_footgun_is_a_taught_refusal():
    # A partial of a task silently defeated interception once (footman's own
    # tasks.py did it). Under the ban it teaches instead.
    import functools

    from livery.footman import parallel

    reg = Group("root")

    @reg.task
    def work(tag: str = "plain"): ...

    @reg.task
    def go():
        parallel(functools.partial(work, tag="p"))  # type: ignore[call-overload]

    result = drive(reg, "go")
    assert not result.ok
    assert "runs tasks and steps" in result.stderr


# --- a task defined while a run is in flight ---------------------------------


def test_a_task_defined_in_a_body_runs_and_is_swept():
    # `@task` inside a body is ordinary Python: the decorator runs when the
    # body does, and the task it makes is real. It must not outlive the run —
    # the manifest was written before any of it happened.
    reg = Group("root")
    ran: list[str] = []

    @reg.task
    def outer():
        @reg.task
        def nested(word: str = "hi") -> str:
            ran.append(word)
            return f"nested-{word}"

        assert nested("yo") == "nested-yo"  # a real request, with a real value

    result = drive(reg, "outer")
    assert result.ok, result.stderr
    assert ran == ["yo"]
    assert sorted(reg.tasks) == ["outer"]  # swept: the tree is as it was


def test_a_clashing_name_is_numbered_only_while_a_run_is_in_flight():
    # A duplicate written in a tasks file is a mistake and stays taught. One
    # made mid-run is not: the task is ad-hoc, and the name is incidental.
    import livery.footman as footman

    reg = Group("root")
    ran: list[str] = []

    @reg.task
    def outer():
        def tidy() -> str:
            ran.append("tidy")
            return "done"

        footman.task(tidy)()
        footman.task(tidy)()  # same name, same run: numbered, not refused

    result = drive(reg, "outer")
    assert result.ok, result.stderr
    assert ran == ["tidy", "tidy"]
    assert "tidy-2" in result.stderr  # and told apart in the report
    assert "tidy" not in registry.root.tasks  # both swept

    with pytest.raises(RegistrationError, match=r"already has a task named"):

        @reg.task(name="outer")  # at import time, still a mistake
        def clash(): ...


def test_anonymous_adhoc_tasks_in_a_loop_all_run():
    # Every lambda is `<lambda>`; numbering is what keeps them distinct.
    import livery.footman as footman

    reg = Group("root")
    seen: list[int] = []

    @reg.task
    def go():
        with footman.parallel() as p:
            for i in range(3):
                footman.task(lambda n=i: seen.append(n))()
        assert len(p.results) == 3

    result = drive(reg, "go")
    assert result.ok, result.stderr
    assert sorted(seen) == [0, 1, 2]


def test_an_adhoc_task_from_a_plain_callable_joins_a_block():
    # `task(fn)(args)` makes a named callable into a real task for this run —
    # so it queues in a block like any other call, and is swept after.
    import livery.footman as footman

    reg = Group("root")
    ran: list[str] = []

    def tidy_up(where: str) -> str:
        ran.append(where)
        return f"tidied {where}"

    @reg.task
    def build(target: str = "x") -> str:
        return f"dist/{target}"

    @reg.task
    def go():
        with footman.parallel() as p:
            build("web")
            footman.task(tidy_up)("stale")
        assert p.results == ["dist/web", "tidied stale"]

    result = drive(reg, "go")
    assert result.ok, result.stderr
    assert ran == ["stale"]
    assert "tidy-up" not in registry.root.tasks  # swept from the root too


def test_a_body_calls_output_reaches_an_uncaptured_run(capsys):
    # The callee runs with its own buffer so its block stays contiguous. A
    # capturing parent takes that block; an uncaptured one is streaming to the
    # terminal, and the block must land there rather than in the buffer's bin.
    from livery.footman import _app

    reg = Group("root")

    @reg.task
    def callee():
        print("CALLEE PRINTED")

    @reg.task
    def caller():
        print("CALLER PRINTED")
        callee()

    assert _app.run_group(reg, ["caller"]) == 0
    out = capsys.readouterr().out
    assert "CALLER PRINTED" in out
    assert "CALLEE PRINTED" in out
