"""The step lifter, the built item, and the pump — stage D1's surface."""

from __future__ import annotations

import time
from asyncio import CancelledError  # a BaseException, unlike futures' namesake

import pytest

from livery.footman import Context, fail, parallel, step, use_context
from livery.footman.context import Failed, RunTimeout


def test_calling_a_step_maker_builds_and_runs_nothing():
    ran: list[str] = []

    @step
    def clean():
        ran.append("ran")

    item = clean()
    assert ran == []  # built, not run — the range(10) precedent
    assert "built, not run" in repr(item)

    with use_context(Context()):
        item()
    assert ran == ["ran"]


def test_an_executed_item_earns_a_full_record():
    @step
    def greet(name: str) -> str:
        print(f"hello {name}")
        return name.upper()

    ctx = Context()
    with use_context(ctx):
        value = greet("world")()
    assert value == "WORLD"  # the value is data, never an exit code
    record = ctx.steps[-1]
    assert record.code == 0 and record.command == "greet"
    assert "hello world" in record.stdout
    assert [tuple(e) for e in record.audit] == [("body", "greet", 0)]


def test_parallel_takes_items_and_bare_zero_arg_makers():
    ran: list[str] = []

    @step
    def one():
        ran.append("one")

    @step
    def two(tag: str):
        ran.append(tag)

    ctx = Context()
    with use_context(ctx):
        codes = parallel(one, two("two"))
    assert codes == [0, 0]
    assert sorted(ran) == ["one", "two"]


def test_a_generator_item_writes_its_record_mid_work():
    @step
    def convert(n: int):
        view = yield
        for done in range(n):
            view.title = f"converting {done + 1}/{n}"
            yield
        return n

    ctx = Context()
    with use_context(ctx):
        value = convert(3)()
    assert value == 3
    record = ctx.steps[-1]
    assert record.command == "converting 3/3"  # the title decided mid-work
    assert record.code == 0


def test_a_generator_items_own_timeout_lands_at_a_checkpoint():
    @step
    def slow():
        yield
        while True:
            time.sleep(0.02)
            yield

    cleaned: list[str] = []

    @step
    def slow_but_tidy():
        try:
            yield
            while True:
                time.sleep(0.02)
                yield
        finally:
            cleaned.append("finally")

    with use_context(Context()):
        with pytest.raises(RunTimeout):
            slow.opts(timeout=0.05)()()
        with pytest.raises(RunTimeout):
            slow_but_tidy.opts(timeout=0.05)()()
    assert cleaned == ["finally"]  # cancellation unwinds, cleanup runs


def test_the_makers_reviewer_and_observer_ride_every_item():
    @step
    def gate() -> int:
        print("reformatted 3 files")
        raise SystemExit(1)

    # SystemExit is BaseException — the pump treats a plain-function step
    # like a body: exceptions fail it. Use a return-code shape instead.
    @step
    def gate2():
        print("reformatted 3 files")
        return 1

    del gate

    @gate2.pre_record
    def reformatted_is_fine(view):
        if "reformatted" in view.stdout:
            view.title = "fmt: reformatted"
            view.code = 0

    seen: list[tuple[str, int]] = []

    @gate2.post_step
    def watch(result):
        seen.append((result.command, result.code))

    ctx = Context()
    with use_context(ctx):
        gate2()()
    record = ctx.steps[-1]
    # The body returned 1 as a VALUE (data, not a code): the step is green
    # on its own; the reviewer's involvement is still on the record.
    assert record.code == 0
    assert seen and seen[0][1] == 0


def test_a_step_observer_vetoes_with_its_own_code():
    @step
    def fine():
        return None

    @fine.post_step
    def budget(result):
        fail("not buying it", code=5)

    ctx = Context()
    with use_context(ctx), pytest.raises(Exception, match="not buying it"):
        fine()()
    record = ctx.steps[-1]
    assert record.code == 5 and record.failed_at == "observe"
    assert record.work_code == 0


def test_the_block_form_records_where_it_stands():
    ctx = Context()
    with use_context(ctx), step("prepare fixtures") as s:
        s.title = "prepared 3 fixtures"
    record = ctx.steps[-1]
    assert record.command == "prepared 3 fixtures" and record.code == 0
    assert record.duration >= 0.0

    with use_context(ctx), pytest.raises(ValueError, match="boom"), step("doomed"):
        raise ValueError("boom")
    assert ctx.steps[-1].code == 1  # the record sealed before the raise


def test_the_expression_form_lifts_a_function_you_did_not_write():
    ctx = Context()
    with use_context(ctx):
        lifted = step(sorted, title="sort things")
        value = lifted([3, 1, 2])()
    assert value == [1, 2, 3]
    assert ctx.steps[-1].command == "sort things"


def test_a_secret_title_is_shown_redacted_and_recorded_whole():
    """A title stands where a command line would, so it answers to the same
    rule: the receipt, the address and the audit read `***`, the record
    keeps what the author passed."""
    from livery.footman.params import Secret

    ctx = Context()
    ctx.address = "release"
    with use_context(ctx):
        step(sorted, title=Secret("sort hunter2"))([3, 1, 2])()
        with step(Secret("prepare hunter2")):
            pass

    for record in ctx.steps:
        assert record.shown == "***"
        assert "hunter2" not in record.address
        assert "hunter2" not in record.audit[0].actor
        assert "hunter2" in record.command  # the record, untouched


def test_an_amended_record_keeps_the_shown_line():
    """An observer's veto replaces the committed record, and a copy that
    dropped the shown line would put the secret back on every surface that
    reads the amended one."""
    from livery.footman.params import Secret

    lifted = step(sorted, title=Secret("sort hunter2"))

    @lifted.post_step
    def budget(result):
        fail("not buying it", code=5)

    ctx = Context()
    with use_context(ctx), pytest.raises(Exception, match="not buying it"):
        lifted([3, 1, 2])()
    record = ctx.steps[-1]
    assert record.code == 5 and record.shown == "***"
    assert "hunter2" in record.command


def test_a_failing_item_raises_like_run_does():
    @step
    def broken():
        raise ValueError("kaput")

    ctx = Context()
    with use_context(ctx), pytest.raises(ValueError, match="kaput"):
        broken()()
    record = ctx.steps[-1]
    assert record.code == 1 and record.failed_at == "body"
    assert "kaput" in record.stderr  # the traceback was captured


def test_dry_run_fakes_the_deferred_maker():
    ran: list[str] = []

    @step
    def deploy():
        ran.append("deployed")

    ctx = Context(dry_run=True)
    with use_context(ctx):
        parallel(deploy)
    assert ran == []  # owned and recorded → faked
    assert ctx.steps == [] or all(s.duration == 0.0 for s in ctx.steps)


def test_step_opts_is_a_closed_vocabulary():
    @step
    def thing(): ...

    with pytest.raises(TypeError, match="boundary policy"):
        thing.opts(confirm="really?")


def test_the_block_title_goes_positionally():
    with pytest.raises(TypeError, match="block form"):
        step("title", title="again")  # type: ignore[call-overload]


def test_off_the_record_items_leave_no_trace():
    @step
    def probe() -> str:
        return "value"

    ctx = Context()
    with use_context(ctx):
        value = probe.opts(recorded=False)()()
    assert value == "value"
    assert ctx.steps == []


def test_a_sealed_record_refuses_writes():
    @step
    def quick() -> None: ...

    ctx = Context()
    with use_context(ctx):
        quick()()
    record = ctx.steps[-1]
    with pytest.raises(AttributeError, match="sealed"):
        # the static seal (read-only property) and the runtime one agree;
        # the ignore lets the runtime half be exercised at all
        record.command = "rewritten"  # type: ignore[misc]


def test_a_step_yielding_a_value_is_taught():
    @step
    def chatty():
        yield
        yield "progress"

    with use_context(Context()), pytest.raises(TypeError, match="checkpoints"):
        chatty()()


def test_an_async_step_body_is_refused_and_names_where_it_lives():
    # A generator body is the supported way for a step to yield; an `async
    # def` is not a generator function, so it fell through to the plain call
    # and built a coroutine nothing drove — a sealed record for work that
    # never happened. The refusal carries the definition site, because the
    # question it answers is "which one?" and the answer is a place in a file.
    ran: list[str] = []

    async def sleeper():
        ran.append("no")

    with use_context(Context()), pytest.raises(Failed) as caught:
        # Bound, because both checkers otherwise report the unused coroutine —
        # which is exactly the mistake under test, spotted statically. It never
        # binds: the call raises.
        _ = step(sleeper)()()
    assert not ran  # it never ran, and never claimed to
    said = str(caught.value)
    assert "async def" in said
    assert "asyncio.run" in said
    assert "test_step.py:" in said  # the definition site, not a traceback


def test_an_async_generator_step_is_refused_like_the_coroutine():
    # `async def` with `yield` builds an async generator: not a coroutine,
    # so the refusal above never fires, and not a generator function, so
    # the pump never takes it — a sealed record for work that never
    # happened, the same hole through a second door.
    ran: list[str] = []

    async def pump():
        ran.append("no")
        yield

    with use_context(Context()), pytest.raises(Failed) as caught:
        _ = step(pump)()()
    assert not ran  # it never ran, and never claimed to
    said = str(caught.value)
    assert "async def" in said and "generator" in said
    assert "plain `def`" in said
    assert "test_step.py:" in said  # the definition site, not a traceback


def test_a_deliberate_step_failure_is_not_dressed_as_a_crash():
    # `fail()` carries a reason written for the person reading it, and
    # `Failed` promises it renders verbatim. The pump used to print a full
    # traceback first — footman's own frames included — which buried the
    # reason and made a considered stop look like an internal error.
    @step
    def stopper():
        fail("this step chose to stop")

    ctx = Context()
    with use_context(ctx), pytest.raises(Failed, match="this step chose to stop"):
        stopper()()
    assert "Traceback" not in ctx.steps[-1].stderr


def test_a_base_exception_leaving_a_step_still_seals_its_record():
    # The same rule tasks follow: an exception leaving the body is that body
    # failing, whichever side of `Exception` it sits on. Catching only
    # `Exception` here let a BaseException past the record, so no step row
    # sealed — the task still failed, correctly, but with nothing to say which
    # step did it, and `--json` and `recording()` read that record.
    @step
    def cancelled():
        raise CancelledError("the loop cancelled us")

    ctx = Context()
    with use_context(ctx), pytest.raises(CancelledError):
        cancelled()()
    record = ctx.steps[-1]  # the receipt that used to be missing entirely
    assert record.code != 0
    assert "the loop cancelled us" in record.stderr


def test_footmans_own_cancellation_is_not_a_step_failure():
    # GeneratorExit is the pump's own cancel signal, raised INTO a step by
    # `gen.close()`. Sealing a record around it would swallow the cancellation
    # footman just issued, so it re-raises like KeyboardInterrupt does.
    before = None

    @step
    def torn_down():
        raise GeneratorExit

    ctx = Context()
    before = len(ctx.steps)
    with use_context(ctx), pytest.raises(GeneratorExit):
        torn_down()()
    assert len(ctx.steps) == before  # no receipt: nothing failed
