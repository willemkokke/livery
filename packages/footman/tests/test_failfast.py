"""Failure policy: the tri-state keep-going resolution and `--fail-fast`."""

from __future__ import annotations

import sys

import pytest

from livery.footman import _manifest
from livery.footman._executor import EX_USAGE
from livery.footman._schedule import dag_wants_progress, resolve_keep_going
from livery.footman._split import _parse_globals, split_chain
from livery.footman.registry import Group


def _tree(build):
    reg = Group("root")
    build(reg)
    return reg, _manifest.build_manifest(reg)["tree"]


def _segs(tree, line):
    return split_chain(tree, line.split())[1]


def test_cli_choice_wins_over_a_declared_default():
    def tasks(reg):
        @reg.task(keep_going=True)
        def check(): ...

    reg, tree = _tree(tasks)
    segs = _segs(tree, "check")
    assert resolve_keep_going(reg, segs, None) is True  # unspecified -> declared
    assert resolve_keep_going(reg, segs, False) is False  # --fail-fast overrides
    assert resolve_keep_going(reg, segs, True) is True  # -k overrides


def test_no_declaration_falls_back_to_built_in_fail_fast():
    def tasks(reg):
        @reg.task
        def plain(): ...

    reg, tree = _tree(tasks)
    segs = _segs(tree, "plain")
    assert resolve_keep_going(reg, segs, None) is False
    assert resolve_keep_going(reg, segs, True) is True


def test_fail_fast_is_a_recognised_global():
    # It parses as a leading global (before the first task), like --keep-going.
    globals_, i = _parse_globals(["--fail-fast", "check"], 0)
    assert globals_ == ["--fail-fast"] and i == 1


def test_run_summaries_are_inert_on_a_malformed_chain(fm_project):
    # M16: both pre-run summaries build the DAG speculatively, so a chain that
    # is about to be refused must not blow up in them — the taught refusal is
    # run_plan's to give. `dag_wants_progress` raised the ChainError as a
    # traceback (exit 1) on the default path; only `-f` and `--no-progress`,
    # which skip the call, ever showed the real message.
    def tasks(reg):
        lint = reg.group("lint")  # no @lint.default: unusable as a prerequisite

        @lint.task
        def ruff(): ...

        @reg.task(pre=[lint])
        def build(): ...

    reg, tree = _tree(tasks)
    segs = _segs(tree, "build")
    assert resolve_keep_going(reg, segs, None) is False
    assert dag_wants_progress(reg, segs) is False

    fm = fm_project(
        """
        from livery.footman import task, group

        lint = group("lint")

        @lint.task
        def ruff(): ...

        @task(pre=[lint])
        def build(): ...
        """
    )
    result = fm.invoke("build")  # the default path: cascade + progress on
    assert result.exit_code == EX_USAGE
    assert "group 'lint' is a prerequisite but has no @group.default" in result.stderr


# --- per-subtree scoping ------------------------------------------------------


def _mixed(reg):
    """A keep-going gate (`check`) and an independent fail-fast task (`deploy`)."""

    @reg.task
    def lint(): ...

    @reg.task
    def unit(): ...

    @reg.task(keep_going=True, pre=[lint, unit])
    def check(): ...

    @reg.task
    def build(): ...

    @reg.task(pre=[build])  # declares nothing -> built-in fail-fast
    def deploy(): ...


def test_scope_keep_going_isolates_a_gate_from_a_fail_fast_task():
    from livery.footman._schedule import _build_dag, _scope_keep_going

    reg, tree = _tree(_mixed)
    nodes = _build_dag(reg, _segs(tree, "check deploy"))
    _scope_keep_going(nodes, None)  # no CLI override -> per-node
    kg = {n.seg.task: n.keep_going for n in nodes}
    # The gate and its whole subtree keep going...
    assert kg["check"] and kg["lint"] and kg["unit"]
    # ...while the independent fail-fast task and its subtree do not.
    assert not kg["deploy"] and not kg["build"]


def test_cli_choice_forces_the_policy_run_wide_over_scope():
    from livery.footman._schedule import _build_dag, _scope_keep_going

    reg, tree = _tree(_mixed)
    segs = _segs(tree, "check deploy")

    nodes = _build_dag(reg, segs)
    _scope_keep_going(nodes, False)  # --fail-fast: even the keep-going gate bails
    assert not any(n.keep_going for n in nodes)

    nodes = _build_dag(reg, segs)
    _scope_keep_going(nodes, True)  # -k: everything keeps going
    assert all(n.keep_going for n in nodes)


def test_mixed_chain_gate_surfaces_siblings_while_fail_fast_task_bails():
    import time

    from livery.footman._schedule import run_plan

    ran = []
    reg = Group("root")

    @reg.task
    def lint():
        ran.append("lint")
        raise SystemExit(1)  # fails immediately

    @reg.task
    def unit():
        ran.append("unit")  # check's other prerequisite

    @reg.task(keep_going=True, pre=[lint, unit])
    def check(): ...

    @reg.task
    def build():
        time.sleep(0.2)  # slow, so deploy can't start before lint has failed
        ran.append("build")

    @reg.task(pre=[build])  # fail-fast
    def deploy():
        ran.append("deploy")

    tree = _manifest.build_manifest(reg)["tree"]
    segs = split_chain(tree, ["check", "deploy"])[1]
    run_plan(reg, segs, sequential=False)
    assert "unit" in ran  # the keep-going gate surfaced its sibling despite lint
    assert "deploy" not in ran  # the fail-fast task bailed on the failure


def test_fail_fast_reaps_only_the_fail_fast_in_flight_tree_in_a_mixed_run(tmp_path):
    import time

    from livery.footman._schedule import run_plan
    from livery.footman.context import run

    kept = tmp_path / "kept"
    killed = tmp_path / "killed"
    # The victim sleeps long, so the kill has ample margin to land before its
    # marker even on a slow Windows runner (`taskkill` has real latency); the
    # keeper sleeps briefly, so once it is spared the run returns promptly.
    long = [sys.executable, "-c", "import time; time.sleep(30)"]
    brief = [sys.executable, "-c", "import time; time.sleep(0.5)"]

    reg = Group("root")

    @reg.task(keep_going=True)
    def keeper():
        run(brief)
        kept.write_text("done")  # keep-going: its subprocess is spared, completes

    @reg.task
    def victim():
        run(long)
        killed.write_text("done")  # fail-fast: reaped long before this

    @reg.task
    def boom():
        raise SystemExit(1)

    tree = _manifest.build_manifest(reg)["tree"]
    segs = split_chain(tree, ["keeper", "victim", "boom"])[1]
    started = time.perf_counter()
    run_plan(reg, segs, sequential=False)
    assert kept.exists()  # the keep-going tree ran to completion
    assert not killed.exists()  # the fail-fast tree was reaped on the failure
    assert time.perf_counter() - started < 20  # reaped fast, not the victim's 30s


def test_fail_fast_kills_an_in_flight_sibling_subprocess(tmp_path):
    import sys
    import time

    from livery.footman._schedule import run_plan
    from livery.footman.context import run

    marker = tmp_path / "finished"
    sleep = [sys.executable, "-c", "import time; time.sleep(30)"]  # portable, killable

    def tasks(reg):
        @reg.task
        def slow():
            run(sleep)
            marker.write_text("done")  # reached only if the sleep was NOT killed

        @reg.task
        def boom():
            raise SystemExit(1)  # fails fast

    reg, tree = _tree(tasks)
    segs = _segs(tree, "slow boom")  # parallel; boom fails → slow's sleep is killed
    started = time.perf_counter()
    run_plan(reg, segs, sequential=False)
    assert time.perf_counter() - started < 10  # did not wait out the 30s sleep
    assert not marker.exists()  # slow was cut off before it could finish


def test_keep_going_lets_an_in_flight_sibling_finish(tmp_path):
    import sys

    from livery.footman._schedule import run_plan
    from livery.footman.context import run

    marker = tmp_path / "finished"
    sleep = [sys.executable, "-c", "import time; time.sleep(0.4)"]

    def tasks(reg):
        @reg.task
        def slow():
            run(sleep)
            marker.write_text("done")

        @reg.task
        def boom():
            raise SystemExit(1)

    reg, tree = _tree(tasks)
    segs = _segs(tree, "slow boom")
    run_plan(reg, segs, sequential=False, keep_going=True)
    assert marker.exists()  # keep-going: the sibling ran to completion, not killed


def test_atomic_task_is_not_killed_by_fail_fast(tmp_path):
    import sys

    from livery.footman._schedule import run_plan
    from livery.footman.context import run

    marker = tmp_path / "finished"
    sleep = [sys.executable, "-c", "import time; time.sleep(0.4)"]

    def tasks(reg):
        @reg.task(atomic=True)
        def protected():
            run(sleep)
            marker.write_text("done")  # its subprocess must not be cut off

        @reg.task
        def boom():
            raise SystemExit(1)

    reg, tree = _tree(tasks)
    segs = _segs(tree, "protected boom")
    run_plan(reg, segs, sequential=False)  # fail-fast, but `protected` is atomic
    assert marker.exists()  # ran to completion despite the failing sibling


def test_a_killed_task_reports_cancelled_not_failed():
    from livery.footman._schedule import run_plan
    from livery.footman.context import run

    def tasks(reg):
        @reg.task
        def slow():
            run([sys.executable, "-c", "import time; time.sleep(30)"])

        @reg.task
        def boom():
            raise SystemExit(7)  # a genuine failure, code 7

    reg, tree = _tree(tasks)
    segs = _segs(tree, "slow boom")
    results = {r.task: r for r in run_plan(reg, segs, sequential=False)}
    assert results["boom"].cancelled is False and results["boom"].code == 7
    assert results["slow"].cancelled is True  # cut off by fail-fast, not a failure


def test_keep_going_reports_a_later_failure_as_its_own_not_cancelled():
    # The twin of the test above, and the case the abort latch got wrong. The
    # flags are process-wide, but fail-fast's abort is per-subtree: `boom`
    # setting the latch does not doom a keep-going sibling, which is spared
    # the reap and runs to its own conclusion. Reading the latch alone called
    # that conclusion a cancellation — so `--keep-going`, whose whole job is
    # to collect every failure, reported the first and hid the rest: the
    # "cancelled" line *replaces* the error rather than joining it.
    from livery.footman import context
    from livery.footman._schedule import run_plan

    def tasks(reg):
        @reg.task
        def late():
            # Deterministic, and no sleep: wait for the very latch that used
            # to mislabel this task, then fail on this task's own terms.
            assert context._aborting.wait(timeout=10), "sibling never aborted"
            raise RuntimeError("my own fault")

        @reg.task
        def boom():
            raise SystemExit(7)

    reg, tree = _tree(tasks)
    segs = _segs(tree, "late boom")
    results = {
        r.task: r for r in run_plan(reg, segs, sequential=False, keep_going=True)
    }
    assert results["late"].cancelled is False
    assert isinstance(results["late"].error, RuntimeError)  # the error survives
    assert results["boom"].cancelled is False and results["boom"].code == 7


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX process groups; Windows uses taskkill /T"
)
def test_fail_fast_kills_grandchildren_not_just_the_direct_child(tmp_path):
    # A task's subprocess spawns its OWN child (a tool's worker: pytest-xdist,
    # `make -j`). fail-fast must reap the whole tree, not orphan the grandchild.
    # The direct child leads its own process group and the group-wide SIGTERM
    # reaches the grandchild too. (Windows gets the same via taskkill /T, which
    # the non-skipped sibling-kill test already drives on that platform.)
    import os
    import signal
    import time

    from livery.footman._schedule import run_plan
    from livery.footman.context import run

    pidfile = tmp_path / "grandchild.pid"
    grandchild = tmp_path / "grandchild.py"
    grandchild.write_text(
        "import os, sys, time\n"
        "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
        "time.sleep(30)\n"  # long: outlives the run unless the group kill reaps it
    )
    child = tmp_path / "child.py"
    child.write_text(
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]]).wait()\n"
    )

    def tasks(reg):
        @reg.task
        def slow():
            run([sys.executable, str(child), str(grandchild), str(pidfile)])

        @reg.task
        def boom():
            # Wait for the pid *content*, not the file: creation and write
            # are two steps, and failing fast between them killed the tree
            # mid-write — the bottom poll then read an empty file forever
            # (seen on a slow CI runner). Generous ceiling on purpose: the
            # grandchild sleeps 30 s, so waiting up to that is free, and a
            # loaded runner spawning python slowly is not a failure.
            deadline = time.time() + 30
            while time.time() < deadline:
                if pidfile.exists() and pidfile.read_text().strip():
                    break  # the kill targets an established, recorded tree
                time.sleep(0.02)
            raise SystemExit(1)  # then fail fast → slow's whole tree is killed

    reg, tree = _tree(tasks)
    segs = _segs(tree, "slow boom")
    run_plan(reg, segs, sequential=False)

    # Wait for the *content*, not the file: creating it and writing the pid are
    # two steps, so polling `exists()` can win the race and read an empty file —
    # which it has, on a loaded free-threaded runner.
    recorded = ""
    for _ in range(200):  # the grandchild records its pid as it starts
        recorded = pidfile.read_text().strip() if pidfile.exists() else ""
        if recorded:
            break
        time.sleep(0.02)
    assert recorded, "grandchild never started — the test isn't exercising it"
    gc_pid = int(recorded)

    def alive() -> bool:
        try:
            os.kill(gc_pid, 0)  # signal 0 just probes; ESRCH once it's gone
            return True
        except ProcessLookupError:
            return False

    for _ in range(200):  # let the group signal propagate to the grandchild
        if not alive():
            break
        time.sleep(0.02)
    leaked = alive()
    if sys.platform == "win32":  # pragma: no cover — the skipif above holds
        pytest.skip("group kill is POSIX-only here")
    if leaked:  # don't strand a 30s sleeper if the assertion is about to fail
        os.kill(gc_pid, signal.SIGKILL)
    assert not leaked, "grandchild survived fail-fast — the group kill missed it"


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX process-group ownership guard"
)
def test_kill_signals_a_shared_group_child_alone_never_the_group():
    # A child that shares footman's process group (spawned WITHOUT its own
    # session) must be signalled individually — a killpg here would take out the
    # runner. That this test's own process survives to make its assertion is the
    # proof the group was spared; the child dies from a plain, individual SIGTERM.
    import signal
    import subprocess

    from livery.footman import context as ctx

    proc = subprocess.Popen(  # no start_new_session → shares pytest's group
        [sys.executable, "-c", "import time; time.sleep(30)"], text=True
    )
    ctx.reset_abort()
    try:
        ctx._register_child(proc)
        ctx.terminate_live_children(grace=5)  # individual SIGTERM, not group-wide
        proc.wait(timeout=5)
        assert proc.returncode == -signal.SIGTERM  # the child alone, not the group
    finally:
        ctx._forget_child(proc)
        ctx.reset_abort()
        if proc.poll() is None:
            proc.kill()


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM is unignorable on Windows")
def test_fail_fast_escalates_to_sigkill_when_sigterm_is_ignored(tmp_path):
    import signal
    import subprocess
    import time

    from livery.footman import context as ctx

    ready = tmp_path / "ready"
    src = (
        "import signal, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"open({str(ready)!r}, 'w').close(); "
        "time.sleep(30)"
    )
    # start_new_session so it leads its own group, exactly like a real killable
    # child — the group SIGTERM/SIGKILL escalation is what's under test, and it
    # keeps the kill off pytest's own process group.
    proc = subprocess.Popen(
        [sys.executable, "-c", src], text=True, start_new_session=True
    )  # Popen[str]
    ctx.reset_abort()
    try:
        for _ in range(200):  # wait until the child has installed its SIG_IGN
            if ready.exists():
                break
            time.sleep(0.02)
        ctx._register_child(proc)
        ctx.terminate_live_children(grace=0.2)  # SIGTERM ignored → SIGKILL follows
        proc.wait(timeout=5)
        if sys.platform == "win32":  # pragma: no cover — the skipif holds
            pytest.skip("SIGKILL is POSIX-only")
        assert proc.returncode == -signal.SIGKILL  # forced, not the ignored SIGTERM
    finally:
        ctx._forget_child(proc)
        ctx.reset_abort()
        if proc.poll() is None:
            proc.kill()
