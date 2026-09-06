"""`@task(retries=N)` — attempts are records, and failure moves its meaning.

The design is notes/20260807-timeout-and-retry.md. Its shape comes from one
ruling: *"a task is only retried if it failed. we need a separate state as
that is failed but not terminally."* So every attempt is a real row with real
timing and its own audit — nothing merged, nothing hidden — and finality
moves from "a task failed" to "a task failed with no attempts left".

The three rulings under test:

1. A retriable failure does not trigger fail-fast, and does not block a
   dependent — there is no failure to react to until attempts are spent.
2. All attempts count as one unit on the progress bar.
3. Retry is the user's choice, with no theory about what deserves it — a
   deliberate `fail()` retries exactly like a crash.
"""

from __future__ import annotations

import pytest

from livery.footman import _executor, _manifest, _schedule, fail
from livery.footman._split import split_chain
from livery.footman.registry import Group, task_retries


def states(rows):
    """The reported word for each row — an empty `state` means "read it from
    `ok`", which `reported_state` is the one place that resolves."""
    return [_executor.reported_state(r) for r in rows]


def drive(build, line, **kw):
    """Run a chain; hand back `(rows, terminal rows only)`."""
    reg = Group("root")
    build(reg)
    tree = _manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, line.split())
    rows = _schedule.run_plan(reg, segments, **kw)
    return rows, [r for r in rows if r.state != "retried"]


# --- the declaration ---------------------------------------------------------


def test_retries_counts_extra_attempts_not_total_runs():
    """`retries=2` is "retry twice" — up to three runs.

    Reading it as a total would silently halve every declaration, and "retry
    twice" is what an author says out loud.
    """
    reg = Group("root")

    @reg.task(retries=2)
    def flaky(): ...

    @reg.task
    def plain(): ...

    assert task_retries(flaky) == 2
    assert task_retries(plain) == 0


@pytest.mark.parametrize("bad", [-1, 1.5, "2", True])
def test_a_bad_retries_is_refused_at_declaration(bad):
    reg = Group("root")
    with pytest.raises(TypeError, match=r"whole number of EXTRA attempts"):

        @reg.task(retries=bad)
        def nope(): ...


def test_retries_is_visible_in_the_manifest():
    """A caller that can see a task already retries will not wrap it in a
    retry of its own; one that cannot, will. The design note names curl's
    `--retry` multiplying with this as the case to prevent."""
    reg = Group("root")

    @reg.task(retries=3)
    def flaky(): ...

    @reg.task
    def plain(): ...

    tree = _manifest.build_manifest(reg)["tree"]
    assert tree["tasks"]["flaky"]["retries"] == 3
    assert "retries" not in tree["tasks"]["plain"], "absent means none, additively"


# --- attempts are records -----------------------------------------------------


def test_every_attempt_is_its_own_row():
    calls: list[int] = []

    def build(reg):
        @reg.task(retries=2)
        def flaky():
            calls.append(1)
            if len(calls) < 3:
                fail(f"attempt {len(calls)}")

    rows, terminal = drive(build, "flaky")

    assert len(calls) == 3, "the body runs once per attempt"
    assert states(rows) == ["retried", "retried", "ok"]
    assert len(terminal) == 1
    assert terminal[0].ok


def test_the_attempts_carry_their_own_reasons():
    """Nothing is merged: each row keeps the error that ended it."""
    calls: list[int] = []

    def build(reg):
        @reg.task(retries=2)
        def flaky():
            calls.append(1)
            if len(calls) < 3:
                fail(f"attempt {len(calls)} is unlucky")

    rows, _ = drive(build, "flaky")
    retried = [r for r in rows if r.state == "retried"]
    assert "attempt 1" in str(retried[0].error)
    assert "attempt 2" in str(retried[1].error)


def test_attempts_are_reported_in_the_order_they_happened():
    def build(reg):
        @reg.task(retries=2)
        def doomed():
            fail("always broken")

    rows, _ = drive(build, "doomed")
    assert states(rows) == ["retried", "retried", "failed"]
    starts = [r.started for r in rows if r.started is not None]
    assert starts == sorted(starts), "the report reads in the order work happened"


def test_a_body_that_never_succeeds_fails_terminally():
    calls: list[int] = []

    def build(reg):
        @reg.task(retries=2)
        def doomed():
            calls.append(1)
            fail("always broken")

    _rows, terminal = drive(build, "doomed")
    assert len(calls) == 3, "retries=2 means three runs, then stop"
    assert len(terminal) == 1
    assert not terminal[0].ok
    assert terminal[0].state != "retried"


def test_no_retries_means_one_attempt():
    calls: list[int] = []

    def build(reg):
        @reg.task
        def once():
            calls.append(1)
            fail("nope")

    rows, _ = drive(build, "once")
    assert len(calls) == 1
    assert states(rows) == ["failed"]


# --- ruling 3: no theory about what deserves a retry -------------------------


def test_a_deliberate_fail_retries_like_anything_else():
    """footman has no private theory of which failures are real.

    The rejected alternative would have made `fail()` mean two different
    things depending on a decorator argument.
    """
    calls: list[int] = []

    def build(reg):
        @reg.task(retries=1)
        def deliberate():
            calls.append(1)
            fail("I meant this")

    drive(build, "deliberate")
    assert len(calls) == 2


def test_a_crash_retries_the_same_way():
    calls: list[int] = []

    def build(reg):
        @reg.task(retries=1)
        def crashes():
            calls.append(1)
            raise RuntimeError("boom")

    rows, _terminal = drive(build, "crashes")
    assert len(calls) == 2
    assert states(rows) == ["retried", "failed"]


# --- ruling 1: a retriable failure is not yet a failure -----------------------


def test_a_dependent_is_not_blocked_while_attempts_remain():
    """The heart of ruling 1: nothing has failed, so nothing is blocked —
    and no `skipped`/`blocked_by` row is written."""
    calls: list[int] = []
    ran: list[str] = []

    def build(reg):
        @reg.task(retries=2)
        def flaky():
            calls.append(1)
            if len(calls) < 3:
                fail("not yet")

        @reg.task(pre=[flaky])
        def after():
            ran.append("after")

    rows, terminal = drive(build, "after")

    assert ran == ["after"], "the dependent ran once its prerequisite succeeded"
    assert all(r.state != "skipped" for r in rows)
    assert all(not r.blocked_by for r in terminal)


def test_a_dependent_is_blocked_once_the_attempts_are_spent():
    """The other half: when finality *is* reached, propagation is normal."""
    ran: list[str] = []

    def build(reg):
        @reg.task(retries=1)
        def doomed():
            fail("always")

        @reg.task(pre=[doomed])
        def after():
            ran.append("after")

    rows, _ = drive(build, "after")
    assert ran == [], "a terminal failure blocks its dependent as it always did"
    skipped = [r for r in rows if r.state == "skipped"]
    assert skipped and skipped[0].blocked_by == "doomed"


def test_a_retried_attempt_does_not_latch_fail_fast_for_a_sibling():
    """A failed attempt with attempts left must not reap the run.

    Fail-fast means "no new work"; an attempt that will be retried has not
    established that anything is wrong.
    """
    calls: list[int] = []
    ran: list[str] = []

    def build(reg):
        @reg.task(retries=2)
        def flaky():
            calls.append(1)
            if len(calls) < 3:
                fail("not yet")

        @reg.task
        def sibling():
            ran.append("sibling")

    _rows, terminal = drive(build, "flaky sibling")
    assert ran == ["sibling"], "the sibling was never cut off"
    assert all(r.ok for r in terminal)


# --- what is NOT re-run -------------------------------------------------------


def test_prerequisites_run_once_across_attempts():
    """`pre=` runs once: prerequisites already ran and are shared. A retried
    attempt re-runs the body only."""
    pre_calls: list[int] = []
    body_calls: list[int] = []

    def build(reg):
        @reg.task
        def setup():
            pre_calls.append(1)

        @reg.task(pre=[setup], retries=2)
        def flaky():
            body_calls.append(1)
            if len(body_calls) < 3:
                fail("not yet")

    drive(build, "flaky")
    assert body_calls == [1, 1, 1], "three attempts"
    assert pre_calls == [1], "the prerequisite ran once, not once per attempt"


def test_the_confirm_gate_is_asked_once_not_once_per_attempt():
    """Gates are per call, not per attempt.

    A retry that re-prompts a human is a bug found late and read as broken
    rather than as an oversight — so the gate resolves before the attempts
    begin and never again.
    """

    def run_with(retries: int) -> tuple[int, int]:
        asked: list[str] = []
        calls: list[int] = []

        def build(reg):
            @reg.task(confirm="really?", retries=retries)
            def guarded():
                calls.append(1)
                if len(calls) < retries + 1:
                    fail("not yet")

        # The spy counts how often the gate is *consulted*. The absolute
        # number is the scheduler's own business — it resolves gates in a
        # planning pass before anything runs — so what must hold is that the
        # count does not grow with the attempts.
        import livery.footman._schedule as sched

        original = sched.task_confirm

        def spy(fn):
            prompt = original(fn)
            if prompt:
                asked.append(prompt)
            return prompt

        sched.task_confirm = spy
        try:
            drive(build, "guarded", ctx_config={"assume_yes": True})
        finally:
            sched.task_confirm = original
        return len(calls), len(asked)

    attempts_none, asked_none = run_with(0)
    attempts_two, asked_two = run_with(2)

    assert attempts_none == 1
    assert attempts_two == 3, "the retried task really did run three times"
    assert asked_two == asked_none, (
        "the gate is per call, not per attempt: three attempts consulted it "
        f"{asked_two} times where one attempt consulted it {asked_none}"
    )


# --- ruling 2: one unit on the progress bar ----------------------------------


def test_all_attempts_count_as_one_unit_of_progress():
    """The report stays honest at N rows; the bar stays stable because a
    retried task is still one piece of work the user asked for. This
    preserves progress.md's promise: "how you spell a call never changes
    the total"."""
    started: list[str] = []
    finished: list[tuple[str, bool]] = []

    class Spy:
        def unit_started(self, name):
            started.append(name)

        def unit_finished(self, name, ok):
            finished.append((name, ok))

        def __getattr__(self, _name):
            return lambda *a, **k: None

    calls: list[int] = []

    def build(reg):
        @reg.task(retries=2)
        def flaky():
            calls.append(1)
            if len(calls) < 3:
                fail("not yet")

    reg = Group("root")
    build(reg)
    tree = _manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, ["flaky"])
    import livery.footman._schedule as sched

    original = sched._make_status
    sched._make_status = lambda *a, **k: Spy()  # type: ignore[assignment]
    try:
        sched.run_plan(reg, segments)
    finally:
        sched._make_status = original

    assert len(calls) == 3, "three attempts really ran"
    assert started.count("flaky") == 1, "one unit started, not one per attempt"
    assert finished.count(("flaky", True)) == 1, "and one finished, successfully"


# --- the run's verdict, through the real CLI ---------------------------------
# `run_plan` returns rows; the exit code is decided a layer up, in `_app`.
# These drive the whole thing so the two cannot disagree — a retried row must
# never reach the exit code, and a terminal failure always must.


def test_a_retried_then_successful_run_exits_zero():
    """The user-visible verdict. Attempts are recorded, not counted against
    the run: only the terminal attempt decides."""
    from livery.footman.testing import Runner

    reg = Group("root")
    calls: list[int] = []

    @reg.task(retries=2)
    def flaky():
        calls.append(1)
        if len(calls) < 3:
            fail("not yet")

    result = Runner().invoke("flaky", tasks=reg)
    assert len(calls) == 3
    assert result.exit_code == 0, result.stderr
    assert result.ok


def test_a_run_whose_attempts_are_spent_exits_nonzero():
    from livery.footman.testing import Runner

    reg = Group("root")

    @reg.task(retries=2)
    def doomed():
        fail("always broken")

    result = Runner().invoke("doomed", tasks=reg)
    assert result.exit_code != 0
    assert "always broken" in result.stderr


def test_the_json_report_distinguishes_retry_from_failure():
    """json.md promised `state` is an open set — "tolerate values you don't
    know" — so `retried` is additive and pre-sanctioned. A consumer can tell
    an attempt from an outcome without knowing anything else."""
    import json

    from livery.footman.testing import Runner

    reg = Group("root")
    calls: list[int] = []

    @reg.task(retries=2)
    def flaky():
        calls.append(1)
        if len(calls) < 3:
            fail("not yet")

    result = Runner().invoke("--json flaky", tasks=reg)
    payload = json.loads(result.stdout)
    states = [item["state"] for item in payload["items"]]
    assert states == ["retried", "retried", "ok"]
    assert [item["ok"] for item in payload["items"]] == [False, False, True]
