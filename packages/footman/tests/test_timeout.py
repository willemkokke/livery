"""`@task(timeout=…)` — the deadline, and the honesty about what it stopped.

The design is notes/20260807-timeout-and-retry.md. Its central claim is that
a task deadline is the fail-fast event scoped to one task: no new work
starts, in-flight subprocess trees are terminated, generator steps unwind at
their next checkpoint. Its central *limit* is that straight-line Python has
no checkpoint, so a body can outrun its deadline and finish — which the note
insists must be recorded rather than papered over.

Timings are deliberately coarse (0.3-0.5 s deadlines against 5 s sleeps): the
suite runs on loaded CI boxes, and a test that needs the deadline to land
within a few milliseconds would be a flake generator. What each test asserts
is a *verdict*, never a duration, except where the whole point is that the
work stopped early — and there the bound is generous.
"""

from __future__ import annotations

import sys
import time

import pytest

from livery.footman import _manifest, _schedule, parallel, run
from livery.footman._split import split_chain
from livery.footman._step import step
from livery.footman.context import RunTimeout, TimedOut
from livery.footman.registry import Group


def drive(build, line, **kw):
    """Run a chain and hand back `(worst code, rows)`.

    `run_plan` answers with rows alone; the exit code a user would see is
    the first non-zero among them, which is what these tests assert on.
    """
    reg = Group("root")
    build(reg)
    tree = _manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, line.split())
    rows = _schedule.run_plan(reg, segments, **kw)
    # `retried` rows are recorded but never the verdict — the same filter the
    # app applies, so this helper cannot disagree with the real exit code.
    code = next((r.code for r in rows if not r.ok and r.state != "retried"), 0)
    return code, rows


def _sleep(seconds: float) -> str:
    """A portable sleep — the suite must not assume coreutils on PATH."""
    return f'"{sys.executable}" -c "import time; time.sleep({seconds})"'


# --- the declaration ---------------------------------------------------------


def test_timeout_is_declarable_and_readable():
    from livery.footman import registry

    reg = Group("root")

    @reg.task(timeout=30)
    def bounded(): ...

    @reg.task
    def unbounded(): ...

    assert registry.task_timeout(bounded) == 30.0
    assert registry.task_timeout(unbounded) is None


@pytest.mark.parametrize("bad", [0, -1, -0.5])
def test_a_nonpositive_timeout_is_refused_at_declaration(bad):
    reg = Group("root")
    with pytest.raises(TypeError, match=r"must be a positive number"):

        @reg.task(timeout=bad)
        def nope(): ...


def test_timeout_rides_opts_for_a_single_use():
    """`.opts(timeout=…)` is per-use policy, exactly like the other opts."""
    reg = Group("root")

    @reg.task
    def build(): ...

    from livery.footman import registry

    once = build.opts(timeout=2)
    assert registry.task_timeout(once) == 2.0
    # The declaration itself is untouched: policy is per use, not a mutation.
    assert registry.task_timeout(build) is None


# --- the three body shapes ---------------------------------------------------


def test_a_subprocess_is_killed_at_the_task_deadline():
    """The task's deadline bounds a `run()` that declared none of its own."""

    def build(reg):
        @reg.task(timeout=0.4)
        def hangs():
            run(_sleep(5))

    start = time.perf_counter()
    code, results = drive(build, "hangs")
    elapsed = time.perf_counter() - start

    assert code != 0
    (row,) = [r for r in results if r.task == "hangs"]
    assert row.timed_out
    assert row.after_deadline == "stopped", "the tree was killed at the deadline"
    # Generous, but far under the 5 s the sleep asked for: the point is that
    # the deadline cut it off rather than that it cut it off promptly.
    assert elapsed < 4, f"the deadline did not stop the subprocess ({elapsed:.1f}s)"


def test_a_generator_step_unwinds_at_its_first_checkpoint_past_the_deadline():
    """Every bare `yield` is a checkpoint; the loop simply never resumes."""
    ran: list[int] = []

    def build(reg):
        @reg.task(timeout=0.4)
        def loops():
            def body():
                for i in range(200):
                    ran.append(i)
                    time.sleep(0.02)
                    yield

            parallel(step(body, title="body")())

    code, results = drive(build, "loops")

    assert code != 0
    (row,) = [r for r in results if r.task == "loops"]
    assert row.timed_out
    assert row.after_deadline == "stopped", "the checkpoint cancelled it"
    assert ran, "the body should have started"
    assert len(ran) < 200, "the body should not have run to completion"


def test_a_straightline_body_outruns_its_deadline_and_says_so():
    """The stated limit, asserted rather than hoped for.

    A body of straight-line Python has no checkpoint and no subprocess, so
    footman cannot stop it — it runs to its own end. The breach is still
    reported, and `stopped` is False because footman did not cause the stop.
    That distinction is the note's "do not spell timed out as though it
    implied did not run".
    """
    finished: list[bool] = []

    def build(reg):
        @reg.task(timeout=0.3)
        def uninterruptible():
            time.sleep(0.6)
            finished.append(True)

    code, results = drive(build, "uninterruptible")

    assert code != 0
    (row,) = [r for r in results if r.task == "uninterruptible"]
    assert row.timed_out
    assert row.after_deadline == "completed", "it finished on its own, just late"
    assert finished == [True], "the body ran to completion, as documented"


def test_a_task_inside_its_deadline_is_untouched():
    def build(reg):
        @reg.task(timeout=30)
        def quick():
            run(_sleep(0.01))

    code, results = drive(build, "quick")
    assert code == 0
    (row,) = [r for r in results if r.task == "quick"]
    assert not row.timed_out
    assert row.ok


def test_no_timeout_means_no_deadline_anywhere():
    """The default must not smuggle a bound in: an unbounded task's context
    carries no deadline, so nothing downstream narrows a subprocess."""
    seen: list[object] = []

    def build(reg):
        @reg.task
        def plain(ctx):
            seen.append((ctx.deadline, ctx.time_left(), ctx.overdue()))

    code, _ = drive(build, "plain")
    assert code == 0
    assert seen == [(None, None, False)]


# --- the verdict --------------------------------------------------------------


def test_the_failure_names_the_declared_seconds_not_the_remainder():
    """A task-imposed bound reports the number the author wrote.

    The remainder left when a call started is a microsecond-precision
    accident (`0.399666s`); the declared `0.4s` is what the reader can act
    on. Reporting the call's own bound would print `0s`, which reads as a
    broken timeout rather than an enforced one.
    """

    def build(reg):
        @reg.task(timeout=0.4)
        def hangs():
            run(_sleep(5))

    _, results = drive(build, "hangs")
    (row,) = [r for r in results if r.task == "hangs"]
    assert isinstance(row.error, RunTimeout)
    assert "0.4s" in str(row.error)
    assert "0s and" not in str(row.error)


def test_timed_out_task_answers_124():
    """124 is the shell's convention for "killed by a timeout" — the same
    code `run(timeout=…)` already answers with."""

    def build(reg):
        @reg.task(timeout=0.3)
        def uninterruptible():
            time.sleep(0.6)

    code, results = drive(build, "uninterruptible")
    (row,) = [r for r in results if r.task == "uninterruptible"]
    assert row.code == 124
    assert code == 124


def test_timed_out_is_catchable_as_failed():
    """`TimedOut` is a `Failed`, so `except footman.Failed:` keeps working
    and a caller can still tell a deadline from any other deliberate stop."""
    from livery.footman.context import Failed

    assert issubclass(TimedOut, Failed)
    err = TimedOut("deploy", 5.0)
    assert err.code == 124
    assert err.timeout == 5.0
    assert err.after == "stopped"
    assert "5s" in str(err)


def test_the_error_says_whether_the_work_was_stopped():
    stopped = TimedOut("deploy", 5.0, after="stopped")
    late = TimedOut("deploy", 5.0, after="completed", body="success", at=5.2)
    assert "was stopped" in str(stopped)
    # No fiction: a body that finished says so, and says what it decided.
    assert "the body completed at 5.2s with success" in str(late)
    assert "the deadline governs" in str(late)
    # Only two values ship. `escaped` and `unknown` are designed but not
    # emittable — see TaskResult.after_deadline — and a value nothing can
    # emit is a claim the system can never make.


# --- what the deadline does NOT do -------------------------------------------


def test_a_deadline_does_not_reach_a_sibling_task():
    """Scoped to one task: a slow neighbour is not collateral.

    This is the difference between a task deadline and fail-fast — the same
    machinery, a different blast radius.
    """

    def build(reg):
        @reg.task(timeout=0.3)
        def bounded():
            time.sleep(0.6)

        @reg.task
        def neighbour():
            run(_sleep(0.05))

    code, results = drive(build, "bounded neighbour", keep_going=True)
    rows = {r.task: r for r in results}
    assert rows["bounded"].timed_out
    assert not rows["neighbour"].timed_out
    assert rows["neighbour"].ok, "an unbounded sibling runs to its own conclusion"
    assert code != 0  # the run still fails, on the bounded task's account


def test_the_deadline_starts_at_the_body_not_at_the_request():
    """Prerequisites are work the caller asked for *around* this task.

    Charging a slow `pre=` to the body's deadline would make `timeout=` mean
    "the whole subtree", which is not what it says — and would make a task's
    own bound depend on how long its neighbours took.

    Asserted on the remaining time rather than on the outcome: an outcome
    test needs the deadline to sit between the body's cost and the
    prerequisite's, and on Windows the body's cost is dominated by process
    creation, so that window is a flake generator. What is left on the clock
    when the body starts answers the question directly.
    """
    left: list[float] = []

    def build(reg):
        @reg.task
        def slow_prereq():
            time.sleep(0.3)

        @reg.task(pre=[slow_prereq], timeout=30)
        def after(ctx):
            left.append(ctx.time_left() or 0.0)

    code, results = drive(build, "after")
    (row,) = [r for r in results if r.task == "after"]

    assert code == 0
    assert not row.timed_out
    # Nearly the whole 30 s is still there: the prerequisite's 0.3 s was
    # never charged against it.
    assert left[0] > 29, (
        f"the prerequisite's time was charged to the body ({left[0]:.1f}s left)"
    )


# --- inheritance into the calls a body makes ---------------------------------


def test_a_run_with_its_own_shorter_timeout_keeps_it():
    """Whichever is tighter wins: a call may bound itself harder than the
    task does, and the task can never be outlived."""
    seen: list[float] = []

    def build(reg):
        @reg.task(timeout=30)
        def bounded(ctx):
            seen.append(ctx.time_left() or 0.0)
            with pytest.raises(RunTimeout):
                run(_sleep(5), timeout=0.3)

    code, _ = drive(build, "bounded")
    assert code == 0
    assert 0 < seen[0] <= 30


def test_time_left_counts_down():
    seen: list[float] = []

    def build(reg):
        @reg.task(timeout=5)
        def bounded(ctx):
            seen.append(ctx.time_left() or 0.0)
            time.sleep(0.2)
            seen.append(ctx.time_left() or 0.0)

    drive(build, "bounded")
    assert seen[0] > seen[1], "the remaining time should shrink"
    assert seen[1] > 4, "0.2s of a 5s deadline leaves plenty"


def test_time_left_clamps_at_zero_rather_than_going_negative():
    """Callers hand it to `communicate(timeout=…)`, where a negative bound
    reads as "no bound" — the opposite of what an expired deadline means."""
    from livery.footman.context import Context

    ctx = Context()
    ctx.deadline = time.perf_counter() - 5
    assert ctx.time_left() == 0.0
    assert ctx.overdue() is True


# --- timeout meets retry ------------------------------------------------------
# Ruled 2026-08-20: a timeout footman could NOT stop is terminal, whatever
# attempts remain. With `stopped=True` a retry costs at worst a repeated side
# effect; with `stopped=False` the body may still be running, so a second
# attempt races a live copy of itself — a fork, not a retry.


def test_an_unstoppable_timeout_is_not_retried():
    """The body has no checkpoint, so it is still running. Starting attempt 2
    would put two copies of it on the same file, API or state at once."""
    calls: list[int] = []

    def build(reg):
        @reg.task(timeout=0.3, retries=2)
        def straggler():
            calls.append(1)
            time.sleep(0.6)

    code, results = drive(build, "straggler")

    assert len(calls) == 1, "one attempt only — a retry would fork the body"
    assert code == 124
    rows = [r for r in results if r.task == "straggler"]
    assert len(rows) == 1, "no retried rows: there was no second attempt"
    assert rows[0].timed_out and rows[0].after_deadline == "completed"


def test_a_late_body_reports_what_it_actually_did():
    """No fiction: where the body completed, the receipt says so — and says
    what the body decided — rather than presenting it as work that never
    finished. The task still fails, on its contract."""

    def build(reg):
        @reg.task(timeout=0.3)
        def late():
            time.sleep(0.6)

    _, results = drive(build, "late")
    (row,) = [r for r in results if r.task == "late"]
    said = str(row.error)
    assert "the body completed" in said
    assert "with success" in said, "the outcome being discarded is named"
    assert "the deadline governs" in said
    assert not row.ok, "the deadline is the verdict — flaky is worse than strict"


def test_a_late_body_that_also_failed_keeps_its_own_reason():
    """The deadline is the verdict, but the body's reason is what the author
    needs to read — so it rides along rather than being replaced."""
    from livery.footman import fail

    def build(reg):
        @reg.task(timeout=0.3)
        def late_and_broken():
            time.sleep(0.6)
            fail("the real problem")

    _, results = drive(build, "late-and-broken")
    (row,) = [r for r in results if r.task == "late-and-broken"]
    said = str(row.error)
    assert "the real problem" in said, "the body's own reason survives"
    assert "the deadline governs" in said


def test_a_stoppable_timeout_still_retries():
    """The distinction is whether footman could stop the work, never whether
    the failure was a timeout: work cut off at a checkpoint certainly did not
    finish, so another attempt is safe and happens.

    A generator step rather than a subprocess: the checkpoint is in-process
    and deterministic, where a subprocess needs to *start* inside a
    sub-second deadline — which on Windows is a race against process
    creation, not a test of anything.
    """
    calls: list[int] = []

    def build(reg):
        @reg.task(timeout=0.4, retries=1)
        def hangs():
            calls.append(1)

            def body():
                for _ in range(200):
                    time.sleep(0.02)
                    yield

            parallel(step(body, title="body")())

    _, results = drive(build, "hangs")

    assert len(calls) == 2, "a stoppable timeout is retried like any failure"
    rows = [r for r in results if r.task == "hangs"]
    assert [r.state for r in rows[:-1]] == ["retried"]
    assert all(r.after_deadline == "stopped" for r in rows), "each was cut off"


def test_an_ordinary_failure_under_a_timeout_still_retries():
    """Ruling 3 stands: footman has no theory about which *failures* deserve
    another chance. The unstoppable-timeout rule is not about failure kinds —
    it is footman observing it cannot coherently start another attempt."""
    from livery.footman import fail

    calls: list[int] = []

    def build(reg):
        @reg.task(timeout=30, retries=2)
        def flaky():
            calls.append(1)
            if len(calls) < 3:
                fail("not yet")

    code, _ = drive(build, "flaky")
    assert len(calls) == 3
    assert code == 0
