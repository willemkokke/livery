"""The DAG scheduler: parallelism, pre/post deps, dedup, fail/skip, parallel()."""

from __future__ import annotations

import io
import sys
import threading

import pytest

from livery.footman import _manifest, _schedule, parallel, run
from livery.footman._split import ChainError, Segment, split_chain
from livery.footman._step import step
from livery.footman.registry import Group


def drive(build, line, **kw):
    reg = Group("root")
    build(reg)
    tree = _manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, line.split())
    return _schedule.run_plan(reg, segments, **kw)


def _echo(text: str) -> str:
    """A portable `echo <text>` — the suite must not assume coreutils on
    PATH (Windows has an `echo.exe` only when Git's `usr/bin` is there);
    the interpreter running the suite always exists."""
    return f'"{sys.executable}" -c "print(\'{text}\')"'


def _exit(code: int) -> str:
    """A portable `false` (and `true`): exits with *code*, prints nothing."""
    return f'"{sys.executable}" -c "raise SystemExit({code})"'


def test_force_color_survives_a_terminal_but_not_capture(monkeypatch):
    # `--color=always` (force_color) reaches a task's context, but capture
    # (`--json`) strips it — ANSI must never land in the envelope.
    monkeypatch.delenv("NO_COLOR", raising=False)

    class _Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    seg = Segment(task="t", path=["t"])
    live = _schedule._make_ctx(
        seg, {"force_color": True}, sequential=True, capture=False, real=_Tty()
    )
    assert live.force_color is True
    captured = _schedule._make_ctx(
        seg, {"force_color": True}, sequential=True, capture=True, real=_Tty()
    )
    assert captured.force_color is False


def test_terminal_stamp_ignores_colour_policy(monkeypatch):
    # `terminal` is the fact (a capable terminal is watching, nothing
    # captured); `tty` folds the colour policy in on top. NO_COLOR splits
    # the two, and capture takes both down.
    monkeypatch.setenv("NO_COLOR", "1")

    class _Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    seg = Segment(task="t", path=["t"])
    live = _schedule._make_ctx(seg, None, sequential=True, capture=False, real=_Tty())
    assert live.terminal is True
    assert live.tty is False
    captured = _schedule._make_ctx(
        seg, None, sequential=True, capture=True, real=_Tty()
    )
    assert captured.terminal is False


def test_chain_runs_concurrently_by_default():
    barrier = threading.Barrier(2, timeout=3)
    reached = []

    def tasks(reg):
        @reg.task
        def a():
            barrier.wait()
            reached.append("a")

        @reg.task
        def b():
            barrier.wait()
            reached.append("b")

    results = drive(tasks, "a b")  # both must reach the barrier -> concurrent
    assert set(reached) == {"a", "b"}
    assert all(r.ok for r in results)


def test_sequential_flag_does_not_run_concurrently():
    barrier = threading.Barrier(2, timeout=0.3)

    def tasks(reg):
        @reg.task
        def a():
            barrier.wait()

        @reg.task
        def b():
            barrier.wait()

    results = drive(tasks, "a b", sequential=True)
    # Load-independent: true sequential runs a alone (it times out at the
    # barrier), then skips b — which reports as skipped, never as run. A
    # regressed parallel path would run both and yield two real results.
    assert [(r.task, r.state) for r in results] == [("a", ""), ("b", "skipped")]
    assert results[0].ok is False  # a timed out at the barrier by itself


def test_duplicate_explicit_segments_each_run():
    calls = []

    def tasks(reg):
        @reg.task
        def build(target: str):
            calls.append(target)

    results = drive(tasks, "build web build api", sequential=True)
    assert len(results) == 2
    assert calls == ["web", "api"]  # both invocations run, in order


def test_duplicate_explicit_segments_run_in_parallel_too():
    calls = []
    lock = threading.Lock()

    def tasks(reg):
        @reg.task
        def build(target: str):
            with lock:
                calls.append(target)

    results = drive(tasks, "build web build api")  # default parallel
    assert len(results) == 2
    assert set(calls) == {"web", "api"}


def test_pre_runs_before_dependent():
    order = []

    def tasks(reg):
        @reg.task
        def fmt():
            order.append("fmt")

        @reg.task
        def lint():
            order.append("lint")

        @reg.task(pre=[fmt, lint])
        def check():
            order.append("check")

    results = drive(tasks, "check")
    assert order[-1] == "check"
    assert set(order) == {"fmt", "lint", "check"}
    assert results[-1].task == "check"


def test_post_runs_after():
    order = []

    def tasks(reg):
        @reg.task
        def notify():
            order.append("notify")

        @reg.task(post=[notify])
        def deploy():
            order.append("deploy")

    drive(tasks, "deploy")
    assert order == ["deploy", "notify"]


def test_shared_dependency_runs_once():
    calls = []

    def tasks(reg):
        @reg.task
        def setup():
            calls.append(1)

        @reg.task(pre=[setup])
        def a(): ...

        @reg.task(pre=[setup])
        def b(): ...

    drive(tasks, "a b")
    assert calls == [1]  # deduped despite two dependents


def test_failed_pre_skips_dependent():
    def tasks(reg):
        @reg.task
        def bad():
            raise RuntimeError("boom")

        @reg.task(pre=[bad])
        def check():
            raise AssertionError("must not run")

    results = drive(tasks, "check")
    # The dependent never runs — and says so: a `skipped` row, blamed.
    assert [(r.task, r.state) for r in results] == [("bad", ""), ("check", "skipped")]
    assert results[0].ok is False
    assert results[1].blocked_by == "bad"


def test_keep_going_runs_independent_branches():
    ran = []

    def tasks(reg):
        @reg.task
        def bad():
            ran.append("bad")
            raise RuntimeError("x")

        @reg.task
        def good():
            ran.append("good")

    drive(tasks, "bad good", keep_going=True)
    assert set(ran) == {"bad", "good"}


def test_parallel_helper_runs_concurrently():
    barrier = threading.Barrier(3, timeout=3)

    def hit():
        barrier.wait()

    def tasks(reg):
        @reg.task
        def build():
            parallel(step(hit)(), step(hit)(), step(hit)())

    results = drive(tasks, "build")
    assert results[0].ok


def test_parallel_helper_propagates_failure():
    def tasks(reg):
        @reg.task
        def build():
            parallel(step(lambda: run(_exit(1)))(), step(lambda: run(_exit(0)))())

    results = drive(tasks, "build")
    assert results[0].ok is False


def test_parallel_fails_on_a_failing_item():
    # A step fails by raising (or by its reviewer's word) — its return value
    # is data. sys.exit(1) is the classic spelling, honoured in the pump.
    def tasks(reg):
        @reg.task
        def build():
            parallel(step(lambda: sys.exit(1))(), step(lambda: 0)())

    results = drive(tasks, "build")
    assert results[0].ok is False


def test_parallel_keep_going_collects_all_codes():
    # F42: first coverage of the keep_going branch — codes returned, no raise.
    codes = {}

    def tasks(reg):
        @reg.task
        def build():
            codes["got"] = parallel(
                step(lambda: sys.exit(1), title="red")(),
                step(lambda: 0, title="green")(),
                keep_going=True,
            )

    results = drive(tasks, "build")
    assert results[0].ok is True
    assert codes["got"] == [1, 0]  # pool.map preserves call order


def test_parallel_failure_exit_code_is_the_thunks_code():
    # D16: with 1.1 + 6.2 both in, a failing parallel() thunk exits with its own
    # code (not a flat 1).
    def tasks(reg):
        @reg.task
        def build():
            parallel(
                step(lambda: run([sys.executable, "-c", "import sys; sys.exit(7)"]))()
            )

    results = drive(tasks, "build")
    assert results[0].ok is False
    assert results[0].code == 7


def test_parallel_child_steps_surface_on_parent():
    # F12: run()s inside parallel() used to vanish from --json/recording; they
    # now land on the parent task's steps (completion order — assert as a set).
    def tasks(reg):
        @reg.task
        def build():
            parallel(
                step(lambda: run(_echo("one")))(), step(lambda: run(_echo("two")))()
            )

    results = drive(tasks, "build")
    commands = {s.command for s in results[0].steps}
    # The lifted items add their own receipts beside the inner runs'.
    assert {_echo("one"), _echo("two")} <= commands


def test_single_node_runs_live(capsys):
    # One node has nothing to parallelise: it takes the sequential-live path
    # (sink=None → output streams; run()'s TTY mode can apply). `fm check`
    # is this shape — buffering it gave one uncoloured block at the end.
    seen = {}

    def tasks(reg):
        @reg.task
        def solo():
            from livery.footman import context

            seen["sink"] = context.current().sink

    drive(tasks, "solo")
    assert seen["sink"] is None


def test_multi_node_still_buffers(capsys):
    # Two independent nodes: parallel path, per-task buffers (the
    # non-interleaving contract) — unchanged.
    seen = {}

    def tasks(reg):
        @reg.task
        def a():
            from livery.footman import context

            seen["a"] = context.current().sink

        @reg.task
        def b():
            from livery.footman import context

            seen["b"] = context.current().sink

    drive(tasks, "a b")
    assert seen["a"] is not None and seen["b"] is not None


def test_both_engines_feed_the_same_status_line(monkeypatch):
    # A CLI chain and a task-body parallel() are the same kind of run:
    # units appear on the live line the moment they start, either way.
    err = _Tty()
    monkeypatch.setattr(sys, "stderr", err)

    def chain(reg):
        @reg.task
        def alpha(): ...

        @reg.task
        def bravo(): ...

    drive(chain, "alpha bravo")
    frames = err.getvalue()
    assert "alpha" in frames and "bravo" in frames
    assert "/2" in frames  # two scheduler nodes

    err2 = _Tty()
    monkeypatch.setattr(sys, "stderr", err2)

    def fanout(reg):
        @reg.task
        def combo():
            from livery.footman.context import parallel

            def alpha(): ...

            def bravo(): ...

            parallel(step(alpha)(), step(bravo)())

    drive(fanout, "combo")
    frames = err2.getvalue()
    assert "alpha" in frames and "bravo" in frames  # children reach the line
    assert "/3" in frames  # one node + two parallel() children


def test_parallel_without_a_run_is_a_noop(capsys):
    # Plain calls and recording() have no status line to feed — parallel()
    # must not care.
    from livery.footman.context import parallel

    def a(): ...

    assert parallel(step(a)()) == [0]


def test_parallel_output_is_grouped_not_interleaved(capsys):
    def tasks(reg):
        @reg.task
        def a():
            print("A1")
            print("A2")

        @reg.task
        def b():
            print("B1")
            print("B2")

    drive(tasks, "a b")
    out = capsys.readouterr().out
    assert "A1\nA2\n" in out  # each task's lines stay contiguous
    assert "B1\nB2\n" in out


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_parallel_progress_line_on_stderr_tty(monkeypatch):
    # Status is commentary: the live line renders on stderr, task blocks land
    # on stdout — so `fm check > log` keeps the spinner visible.
    out_fake, err_fake = io.StringIO(), _Tty()
    monkeypatch.setattr(sys, "stdout", out_fake)
    monkeypatch.setattr(sys, "stderr", err_fake)

    def tasks(reg):
        @reg.task
        def a():
            print("A-OUT")

        @reg.task
        def b():
            print("B-OUT")

    results = drive(tasks, "a b")
    err = err_fake.getvalue()
    assert all(r.ok for r in results)
    assert "\r\x1b[K" in err  # the status line rendered and cleared
    assert "running:" in err
    assert err.endswith("\r\x1b[K")  # the line never outlives the run
    out = out_fake.getvalue()
    assert "A-OUT" in out and "B-OUT" in out  # task blocks land intact
    assert "\r" not in out  # stdout carries blocks only — never the spinner


def test_progress_absent_without_a_tty(capsys):
    def tasks(reg):
        @reg.task
        def a(): ...

        @reg.task
        def b(): ...

    drive(tasks, "a b")
    assert "\r" not in capsys.readouterr().err  # buffers aren't TTYs: no spinner


def test_progress_absent_when_quiet(monkeypatch):
    fake = _Tty()
    monkeypatch.setattr(sys, "stderr", fake)

    def tasks(reg):
        @reg.task
        def a(): ...

        @reg.task
        def b(): ...

    drive(tasks, "a b", ctx_config={"quiet": True})
    assert "\r" not in fake.getvalue()


def test_progress_absent_under_no_color(monkeypatch):
    # F41/D6: the live line is absent (like piped output), not rewritten plain.
    fake = _Tty()
    monkeypatch.setattr(sys, "stderr", fake)

    def tasks(reg):
        @reg.task
        def a(): ...

        @reg.task
        def b(): ...

    drive(tasks, "a b", ctx_config={"no_color": True})
    assert "\r" not in fake.getvalue()


def test_progress_absent_under_no_color_env(monkeypatch):
    fake = _Tty()
    monkeypatch.setattr(sys, "stderr", fake)
    monkeypatch.setenv("NO_COLOR", "1")

    def tasks(reg):
        @reg.task
        def a(): ...

        @reg.task
        def b(): ...

    drive(tasks, "a b")
    assert "\r" not in fake.getvalue()


def test_progress_absent_under_dumb_term(monkeypatch):
    fake = _Tty()
    monkeypatch.setattr(sys, "stdout", fake)
    monkeypatch.setenv("TERM", "dumb")

    def tasks(reg):
        @reg.task
        def a(): ...

        @reg.task
        def b(): ...

    drive(tasks, "a b")
    assert "\r" not in fake.getvalue()


def test_no_color_suppresses_sequential_live_rewrite(monkeypatch):
    # The single-task live path (ctx.tty) goes absent too: no \r rewrite, no
    # escape codes — the same output a pipe gets.
    fake = _Tty()
    monkeypatch.setattr(sys, "stdout", fake)

    def tasks(reg):
        @reg.task
        def build():
            run(_echo("hi"))

    drive(tasks, "build", sequential=True, ctx_config={"no_color": True})
    out = fake.getvalue()
    assert "\r" not in out and "\x1b[" not in out


def test_dependency_cycle_is_a_taught_error():
    def tasks(reg):
        @reg.task
        def a(): ...

        # pre=[a] makes b depend on a; post=[a] makes a depend on b: a cycle.
        @reg.task(pre=[a], post=[a])
        def b(): ...

    with pytest.raises(ChainError, match="dependency cycle"):
        drive(tasks, "b")
    with pytest.raises(ChainError, match="dependency cycle"):
        drive(tasks, "b", sequential=True)


def test_buffered_blocks_dress_for_the_terminal(monkeypatch):
    # Engine parity: a chain's captured step lines style exactly like
    # parallel()-in-a-body children — ✓ marks and colour when the replay
    # destination is a terminal — while in-place rewrites (\r, ESC[K) and
    # the announce arrow stay out of capture buffers entirely.
    monkeypatch.delenv("NO_COLOR", raising=False)
    out_fake = _Tty()
    monkeypatch.setattr(sys, "stdout", out_fake)
    monkeypatch.setattr(sys, "stderr", io.StringIO())

    def tasks(reg):
        @reg.task
        def a():
            run(_echo("one"), title="one")

        @reg.task
        def b():
            run(_echo("two"), title="two")

    drive(tasks, "a b", ctx_config={"verbose": True})
    text = out_fake.getvalue()
    assert "\033[32m✓\033[0m" in text  # styled mark, buffered engine
    assert "\r" not in text and "\033[K" not in text  # no live control bytes
    assert "→" not in text  # the announce line is live-only


def test_buffered_blocks_stay_plain_when_piped(monkeypatch):
    out_fake = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out_fake)
    monkeypatch.setattr(sys, "stderr", io.StringIO())

    def tasks(reg):
        @reg.task
        def a():
            run(_echo("one"), title="one")

        @reg.task
        def b():
            run(_echo("two"), title="two")

    drive(tasks, "a b", ctx_config={"verbose": True})
    text = out_fake.getvalue()
    assert "ok   " in text and "\033" not in text


def test_infinite_task_hints_and_suppresses_the_status_line(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    err_fake = _Tty()
    monkeypatch.setattr(sys, "stderr", err_fake)
    monkeypatch.setattr(sys, "stdout", io.StringIO())

    def tasks(reg):
        @reg.task(infinite=True)
        def serve():
            run(_echo("up"), title="up")

    drive(tasks, "serve")
    err = err_fake.getvalue()
    assert "serve runs until you stop it — Ctrl-C" in err
    assert "\r" not in err  # no status-line repaints: nothing is "in progress"


def test_interactive_task_suspends_the_status_line_for_its_body(monkeypatch):
    # The console lane suspends the status line for exactly the ownership
    # window: not one byte of repaint may land while the wizard's body owns
    # the terminal (a \r + clear-line would erase its prompt) — but the line
    # itself lives on around the body, instead of costing the whole run.
    monkeypatch.delenv("NO_COLOR", raising=False)
    err_fake = _Tty()
    monkeypatch.setattr(sys, "stderr", err_fake)
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    marks = {}

    def tasks(reg):
        @reg.task(interactive=True)
        def wizard():
            marks["start"] = err_fake.getvalue()
            run(_echo("hi"), title="hi")
            marks["end"] = err_fake.getvalue()

    drive(tasks, "wizard")
    assert marks["start"] == marks["end"]  # silent while the body owns it
    assert "\r" in err_fake.getvalue()  # and alive around it


def test_infinite_hint_respects_quiet(monkeypatch):
    err_fake = _Tty()
    monkeypatch.setattr(sys, "stderr", err_fake)
    monkeypatch.setattr(sys, "stdout", io.StringIO())

    def tasks(reg):
        @reg.task(infinite=True)
        def serve(): ...

    drive(tasks, "serve", ctx_config={"quiet": True})
    assert "Ctrl-C" not in err_fake.getvalue()


# --- tasks wear their names: threads for profilers ---------------------------


def test_a_task_wears_its_name_while_it_runs():
    from livery.footman.testing import Runner

    reg = Group("root")
    seen: dict[str, str] = {}

    @reg.task
    def build():
        seen["build"] = threading.current_thread().name

    @reg.task(serial=True)
    def migrate():
        seen["migrate"] = threading.current_thread().name

    result = Runner().invoke("build migrate", tasks=reg)
    assert result.ok, result.stderr
    assert seen["build"] == "fm:build"
    assert seen["migrate"] == "fm:migrate [serial]"  # lane occupancy, visible


def test_the_worker_is_recorded_on_the_result():
    from livery.footman.testing import Runner

    reg = Group("root")

    @reg.task
    def build() -> str:
        return "dist"

    @reg.task(pre=[build])
    def publish():
        build()

    result = Runner().invoke("publish", tasks=reg)
    assert result.ok, result.stderr
    rows = {(r.task, r.state): r for r in result.results}
    ran = rows[("build", "")]
    assert ran.thread.startswith("fm-worker")  # the stable name, not fm:build
    assert ran.thread_id != 0
    shared = rows[("build", "shared")]
    assert shared.thread == ""  # nothing of it executed
    assert shared.thread_id == 0


def test_the_name_is_restored_after_the_body():
    from livery.footman.testing import Runner

    reg = Group("root")
    names: list[str] = []

    @reg.task
    def build() -> str:
        return "x"

    @reg.task
    def report():
        build()  # the callee wears its own name, then hands the thread back
        names.append(threading.current_thread().name)

    result = Runner().invoke("report", tasks=reg)
    assert result.ok, result.stderr
    assert names == ["fm:report"]  # not fm:build — the call restored it


# --- the report's tie-break ----------------------------------------------------


def test_near_simultaneous_starts_seat_in_request_order():
    # Starts inside one instant are worker-thread noise; the request stamp
    # decides, so a rerun cannot shuffle the report.
    from livery.footman._executor import TaskResult

    def row(task, started, seq):
        return TaskResult(task=task, ok=True, code=0, started=started, seq=seq)

    burst = [
        row("late-written", 10.0004, 7),
        row("first-written", 10.0001, 5),
        row("mid-written", 10.0092, 6),
    ]
    ordered = _schedule._in_request_order(burst)
    assert [r.task for r in ordered] == ["first-written", "mid-written", "late-written"]


def test_the_clock_never_overrules_the_request_stamp():
    """A task requested first reports first, however late the pool ran it.

    The clock used to rule across 10ms buckets, which made the report of a
    parallel run a reading of the thread scheduler: two independent tasks
    seat in whatever order they got workers, and that flips between runs of
    the same command. It flipped in CI on a free-threaded build. The order
    the work was created is the one thing a reader can rely on, so it is the
    one the report uses — this row is half a second late and still first.
    """
    from livery.footman._executor import TaskResult

    def row(task, started, seq):
        return TaskResult(task=task, ok=True, code=0, started=started, seq=seq)

    rows = [
        row("requested-first-ran-last", 10.5, 1),
        row("requested-last-ran-first", 10.0, 9),
    ]
    ordered = _schedule._in_request_order(rows)
    assert [r.task for r in ordered] == [
        "requested-first-ran-last",
        "requested-last-ran-first",
    ]


def test_block_children_carry_the_written_order_as_their_stamp():
    from livery.footman import parallel
    from livery.footman.testing import Runner

    reg = Group("root")

    @reg.task
    def zulu() -> None: ...

    @reg.task
    def alpha() -> None: ...

    @reg.task
    def mike() -> None: ...

    @reg.task
    def gate():
        with parallel():
            zulu()
            alpha()
            mike()

    result = Runner().invoke("gate", tasks=reg)
    assert result.ok, result.stderr
    seqs = {r.task: r.seq for r in result.results}
    stamped = [seqs["gate"], seqs["zulu"], seqs["alpha"], seqs["mike"]]
    assert None not in stamped
    # The queue moment is the request: written order, whatever the pool did.
    assert stamped == sorted(stamped)  # type: ignore[type-var]
