"""The process-globals routers: environ, Popen injection, and the os guards."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from livery.footman import _globals, _manifest
from livery.footman._executor import run_chain
from livery.footman._split import split_chain
from livery.footman.context import chdir, run
from livery.footman.registry import Group


def drive(build, line, **cfg):
    reg = Group("root")
    build(reg)
    tree = _manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, line.split())
    return run_chain(reg, segments, ctx_config=cfg)


def test_reads_see_snapshot_plus_overlay(monkeypatch):
    monkeypatch.setenv("BASE", "base")
    monkeypatch.delenv("EXTRA", raising=False)
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def go():
            os.environ["EXTRA"] = "mine"  # scoped to this task
            seen["extra"] = os.environ["EXTRA"]
            seen["base"] = os.environ.get("BASE")
            seen["contains"] = "EXTRA" in os.environ
            seen["getenv"] = os.getenv("EXTRA")
            seen["iter"] = "EXTRA" in set(os.environ)
            seen["copy"] = dict(os.environ).get("EXTRA")

    results = drive(tasks, "go")
    assert results[0].ok
    assert seen == {
        "extra": "mine",
        "base": "base",
        "contains": True,
        "getenv": "mine",
        "iter": True,
        "copy": "mine",
    }
    assert "EXTRA" not in os.environ  # the real environment never mutated


def test_scoped_write_is_invisible_to_the_next_task(monkeypatch):
    monkeypatch.delenv("SCOPED", raising=False)
    seen = {}

    def tasks(reg):
        @reg.task
        def first():
            os.environ["SCOPED"] = "x"

        @reg.task
        def second():
            seen["visible"] = "SCOPED" in os.environ

    results = drive(tasks, "first second")
    assert all(r.ok for r in results)
    assert seen["visible"] is False


def test_scoped_write_reaches_the_child_spawn(monkeypatch):
    # The spawn env is snapshot + overlay, so an os.environ write in the
    # body rides into the child exactly like ctx.env would.
    monkeypatch.delenv("SCOPED", raising=False)

    def tasks(reg):
        @reg.task
        def go():
            os.environ["SCOPED"] = "rides"
            run([sys.executable, "-c", "import os; print(os.environ['SCOPED'])"])

    results = drive(tasks, "go")
    assert results[0].ok
    assert results[0].steps[0].output.strip() == "rides"


def test_delete_removes_it_for_this_task_and_its_children():
    """A task owns its environment outright, so deletion is ordinary. It used
    to be a taught error for any key the task had not set itself, which left
    presence-semantics variables impossible to hide from an in-process tool."""
    seen = {}

    def tasks(reg):
        @reg.task
        def go():
            del os.environ["PATH"]
            seen["gone"] = "PATH" not in os.environ

    results = drive(tasks, "go")
    assert results[0].ok, results[0].error
    assert seen["gone"]
    assert "PATH" in os.environ  # the real process environment is untouched


def test_another_task_keeps_what_one_deleted():
    """The copies are per task: subtraction is as private as addition. Each
    task starts from the run's pinned environment, so one cutting a key away
    cannot reach into another's."""
    seen = {}

    def tasks(reg):
        @reg.task
        def cutter():
            del os.environ["PATH"]
            seen["cutter"] = "PATH" in os.environ

        @reg.task
        def keeper():
            seen["keeper"] = "PATH" in os.environ

    results = drive(tasks, "cutter keeper")
    assert all(r.ok for r in results), [r.error for r in results]
    assert seen == {"cutter": False, "keeper": True}


def test_set_then_delete_round_trips_scoped(monkeypatch):
    # pytest's own dance: main() sets PYTEST_VERSION and deletes it on the
    # way out. A key the task itself set scoped comes back out of the
    # overlay — additive both ways, invisible to siblings throughout.
    monkeypatch.delenv("ROUND_TRIP", raising=False)
    seen = {}

    def tasks(reg):
        @reg.task
        def go():
            os.environ["ROUND_TRIP"] = "up"
            del os.environ["ROUND_TRIP"]
            seen["after"] = "ROUND_TRIP" in os.environ

    results = drive(tasks, "go")
    assert results[0].ok, results[0].error
    assert seen["after"] is False
    assert "ROUND_TRIP" not in os.environ


def test_delete_of_an_overridden_key_removes_it(monkeypatch):
    # There is no base to fall back to any more: the task holds one
    # environment, so overriding then deleting leaves the key absent — which
    # is what `del` means everywhere else in Python.
    monkeypatch.setenv("BASE_VAR", "original")
    seen = {}

    def tasks(reg):
        @reg.task
        def go():
            os.environ["BASE_VAR"] = "override"
            del os.environ["BASE_VAR"]
            seen["after"] = os.environ.get("BASE_VAR")

    results = drive(tasks, "go")
    assert results[0].ok, results[0].error
    assert seen["after"] is None
    assert os.environ["BASE_VAR"] == "original"


def test_delete_of_an_absent_key_is_a_key_error():
    def tasks(reg):
        @reg.task
        def go():
            del os.environ["NEVER_WAS_SET"]

    results = drive(tasks, "go")
    assert not results[0].ok
    assert isinstance(results[0].error, KeyError)


def test_write_note_is_taught_once_per_variable(capfd, monkeypatch):
    # Per variable, deliberately: all issues surface in one run (never fix
    # one and have the next pop up), and a known-harmless write can be
    # classified by name without hiding the next variable. The same
    # variable in a loop still teaches once.
    monkeypatch.delenv("A_KEY", raising=False)
    monkeypatch.delenv("B_KEY", raising=False)

    def tasks(reg):
        @reg.task
        def go():
            os.environ["A_KEY"] = "1"
            os.environ["A_KEY"] = "again"  # same variable: no second note
            os.environ["B_KEY"] = "2"  # another variable: its own note

    assert drive(tasks, "go")[0].ok
    err = capfd.readouterr().err
    assert err.count("scoped it to this task") == 2
    assert "sets A_KEY" in err and "sets B_KEY" in err


def test_setdefault_scopes_like_a_write(monkeypatch):
    monkeypatch.setenv("PRESENT", "kept")
    monkeypatch.delenv("ABSENT", raising=False)
    seen = {}

    def tasks(reg):
        @reg.task
        def go():
            seen["hit"] = os.environ.setdefault("PRESENT", "ignored")
            seen["miss"] = os.environ.setdefault("ABSENT", "scoped")
            seen["readback"] = os.environ["ABSENT"]

    assert drive(tasks, "go")[0].ok
    assert seen == {"hit": "kept", "miss": "scoped", "readback": "scoped"}
    assert "ABSENT" not in os.environ


def test_outside_a_run_environ_is_untouched(monkeypatch):
    monkeypatch.delenv("LOOSE", raising=False)
    os.environ["LOOSE"] = "real"
    assert os.environ["LOOSE"] == "real"
    del os.environ["LOOSE"]
    assert "LOOSE" not in os.environ


def test_install_is_refcounted():
    assert not _globals.active()
    _globals.install()
    _globals.install()
    try:
        _globals.uninstall()
        assert _globals.active()  # the outer install still holds
    finally:
        _globals.uninstall()
    assert not _globals.active()


def test_in_process_callable_reads_the_env_it_was_given(monkeypatch):
    # `env=` is the call's environment, as subprocess means it — what you
    # pass is what it gets, served by the router with no process-global patch
    # and no lock. Spread `os.environ` to add rather than replace.
    monkeypatch.setenv("BASE", "base")
    monkeypatch.delenv("EXTRA", raising=False)
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def go():
            def tool():
                seen["pair"] = (os.environ.get("BASE"), os.environ.get("EXTRA"))
                return 0

            from livery.footman import step

            # exactly this env, nothing inherited — the step's own overlay
            step(tool).opts(env={"EXTRA": "call"})()()
            seen["after"] = os.environ.get("EXTRA")

            def adding():
                seen["added"] = (os.environ.get("BASE"), os.environ.get("EXTRA"))
                return 0

            step(adding).opts(env={**os.environ, "EXTRA": "call"})()()  # add idiom

    assert drive(tasks, "go")[0].ok
    assert seen["pair"] == (None, "call")  # BASE was not inherited
    assert seen["added"] == ("base", "call")
    assert seen["after"] is None  # the call's env ended with the call


# --- the Popen injection ------------------------------------------------------

_PRINT_CWD_AND_SCOPED = "import os; print(os.getcwd()); print(os.environ.get('SCOPED'))"


def test_popen_injects_cwd_and_env(tmp_path, monkeypatch, capfd):
    monkeypatch.delenv("SCOPED", raising=False)
    seen = {}

    def tasks(reg):
        @reg.task
        def go():
            os.environ["SCOPED"] = "rides"
            proc = subprocess.Popen(  # raw spawn: no cwd=, no env=
                [sys.executable, "-c", _PRINT_CWD_AND_SCOPED],
                stdout=subprocess.PIPE,
                text=True,
            )
            out, _ = proc.communicate()
            seen["lines"] = out.splitlines()
            subprocess.Popen([sys.executable, "-c", "pass"]).wait()

    results = drive(tasks, "go", cwd=tmp_path)
    assert results[0].ok, results[0].error
    assert seen["lines"] == [str(tmp_path), "rides"]
    err = capfd.readouterr().err
    assert err.count("raw subprocess") == 1  # taught once, not per spawn


def test_popen_explicit_args_win(tmp_path, monkeypatch):
    monkeypatch.delenv("SCOPED", raising=False)
    other = tmp_path / "other"
    other.mkdir()
    seen = {}
    # A deliberately clean env must stay clean (env={} is not env=None) —
    # keep the one variable Windows needs to boot a child at all.
    base = (
        {"SYSTEMROOT": os.environ["SYSTEMROOT"]} if "SYSTEMROOT" in os.environ else {}
    )

    def tasks(reg):
        @reg.task
        def go():
            os.environ["SCOPED"] = "rides"
            proc = subprocess.Popen(
                [sys.executable, "-c", _PRINT_CWD_AND_SCOPED],
                stdout=subprocess.PIPE,
                text=True,
                cwd=str(other),
                env={**base, "MARKER": "explicit"},
            )
            out, _ = proc.communicate()
            seen["lines"] = out.splitlines()

    results = drive(tasks, "go", cwd=tmp_path)
    assert results[0].ok, results[0].error
    assert seen["lines"] == [str(other), "None"]  # explicit cwd/env untouched


def test_run_is_footmans_own_spawn_and_the_injector_leaves_it_alone(
    tmp_path, monkeypatch, capfd
):
    """run() works out both cwd and env, so it never needs filling in.

    The injector could not tell the `cwd=None` that a per-call
    cwd='unmanaged' resolves to from a kwarg left at its default, so it
    wrote ctx.cwd over the token — the opposite of what the token declares.
    It then told the author to prefer run(), which is what they used.
    """
    live, managed = tmp_path / "live", tmp_path / "managed"
    live.mkdir()
    managed.mkdir()
    monkeypatch.chdir(live)
    seen = {}
    show_cwd = [sys.executable, "-c", "import os; print(os.getcwd())"]

    def tasks(reg):
        @reg.task
        def go():
            os.environ["SCOPED"] = "rides"
            seen["managed"] = run(show_cwd).stdout.strip()
            seen["unmanaged"] = run(show_cwd, cwd="unmanaged").stdout.strip()
            seen["env"] = run(
                [sys.executable, "-c", _PRINT_CWD_AND_SCOPED]
            ).stdout.splitlines()[1]

    results = drive(tasks, "go", cwd=managed)
    assert results[0].ok, results[0].error
    assert Path(seen["managed"]).resolve() == managed.resolve()
    assert Path(seen["unmanaged"]).resolve() == live.resolve()  # the token holds
    assert seen["env"] == "rides"  # run() carried the task's env on its own
    assert "raw subprocess" not in capfd.readouterr().err  # nothing to teach


# --- the os guards ------------------------------------------------------------


def test_chdir_is_a_taught_error():
    def tasks(reg):
        @reg.task
        def go():
            os.chdir("/")

    results = drive(tasks, "go")
    assert not results[0].ok
    assert "footman.cwd()" in str(results[0].error)


def test_chdir_to_the_current_directory_is_a_noop_not_an_error():
    # The defensive restore pattern: pytest's wrap_session re-chdirs to its
    # startpath on the way out even when nothing moved. Where the process
    # already is, nothing changes for any sibling — let it through.
    def tasks(reg):
        @reg.task
        def go():
            os.chdir(_globals.real_getcwd())

    results = drive(tasks, "go")
    assert results[0].ok, results[0].error


def test_chdir_allowed_under_unmanaged(tmp_path):
    seen = {}

    def tasks(reg):
        @reg.task
        def go():
            before = _globals.real_getcwd()
            os.chdir(tmp_path)
            seen["moved"] = _globals.real_getcwd()
            os.chdir(before)

    results = drive(tasks, "go", cwd=tmp_path, cwd_unmanaged=True)
    assert results[0].ok, results[0].error
    assert seen["moved"] == str(tmp_path)


def test_getcwd_warns_once_when_the_answer_is_misleading(capfd, tmp_path):
    """A task rooted somewhere other than the process directory gets a
    `getcwd` that answers about the wrong place — that is worth a note, once."""
    seen = {}

    def tasks(reg):
        @reg.task
        def go():
            seen["cwd"] = os.getcwd()
            os.getcwd()  # second read: no second note

    assert drive(tasks, "go", cwd=tmp_path)[0].ok
    assert seen["cwd"] == _globals.real_getcwd()  # the value is still real
    assert capfd.readouterr().err.count("reads the process cwd") == 1


def test_getcwd_is_silent_when_it_answers_correctly(capfd):
    """The common case: a task whose directory *is* the process directory —
    `ctx.cwd` unset means exactly that, since the spawn inherits it. The read
    is correct, and a note there only teaches people to ignore notes. The
    sibling `chdir` guard learned the same in #180: a move to where the
    process already is changes nothing for anyone."""

    def tasks(reg):
        @reg.task
        def go():
            os.getcwd()

    assert drive(tasks, "go")[0].ok
    assert "reads the process cwd" not in capfd.readouterr().err


def test_putenv_is_a_taught_error():
    def tasks(reg):
        @reg.task
        def go():
            os.putenv("X", "y")

    results = drive(tasks, "go")
    assert not results[0].ok
    assert "bypasses env scoping" in str(results[0].error)


@pytest.mark.skipif(sys.platform == "win32", reason="fork is POSIX-only")
@pytest.mark.filterwarnings("ignore::DeprecationWarning")  # the taught unsafety
def test_fork_notes_the_serial_lane(capfd):
    def tasks(reg):
        @reg.task
        def go():
            if sys.platform == "win32":  # pragma: no cover — skipif holds
                raise RuntimeError("fork is POSIX-only")
            pid = os.fork()
            if pid == 0:  # the child: touch nothing, leave immediately
                os._exit(0)
            os.waitpid(pid, 0)

    assert drive(tasks, "go")[0].ok
    assert "forking a threaded process is unsafe" in capfd.readouterr().err


def _mp_noop():
    pass


def test_multiprocessing_start_notes_the_serial_lane(capfd):
    import multiprocessing

    def tasks(reg):
        @reg.task
        def go():
            proc = multiprocessing.get_context("spawn").Process(target=_mp_noop)
            proc.start()
            proc.join()

    assert drive(tasks, "go")[0].ok
    assert "worker processes in-process" in capfd.readouterr().err


# --- the arbiter lanes --------------------------------------------------------


def _hold(policy, name, entered, release, inherited=False):
    def body():
        with _globals.lane(policy, name=name, inherited=inherited):
            entered.set()
            release.wait(timeout=10)

    t = threading.Thread(target=body, daemon=True)
    t.start()
    return t


def test_serial_lane_is_mutually_exclusive():
    _globals.install()
    try:
        e1, r1 = threading.Event(), threading.Event()
        e2, r2 = threading.Event(), threading.Event()
        t1 = _hold("serial", "a", e1, r1)
        assert e1.wait(5)
        t2 = _hold("serial", "b", e2, r2)
        time.sleep(0.15)
        assert not e2.is_set()  # one lane, one owner
        r1.set()
        assert e2.wait(5)  # b takes the lane once a leaves
        r2.set()
        t1.join(5)
        t2.join(5)
    finally:
        _globals.uninstall()


def test_exclusive_drains_and_bars_new_starts():
    _globals.install()
    try:
        en, rn = threading.Event(), threading.Event()
        ee, re_ = threading.Event(), threading.Event()
        e2, r2 = threading.Event(), threading.Event()
        tn = _hold(None, "normal", en, rn)
        assert en.wait(5)
        te = _hold("exclusive", "big", ee, re_)
        time.sleep(0.15)
        assert not ee.is_set()  # a running body blocks the drain
        t2 = _hold(None, "late", e2, r2)
        time.sleep(0.15)
        assert not e2.is_set()  # new starts bar while exclusive waits
        rn.set()
        assert ee.wait(5)  # drained: exclusive enters
        assert not e2.is_set()  # and still owns the world
        re_.set()
        assert e2.wait(5)  # the barred start proceeds after
        r2.set()
        for t in (tn, te, t2):
            t.join(5)
    finally:
        _globals.uninstall()


def test_parked_bodies_are_exempt_from_the_drain():
    _globals.install()
    try:
        ep, rp = threading.Event(), threading.Event()
        ee, re_ = threading.Event(), threading.Event()

        def parked_body():
            with _globals.lane(None, name="parent"), _globals.parked():
                ep.set()
                rp.wait(timeout=10)

        tp = threading.Thread(target=parked_body, daemon=True)
        tp.start()
        assert ep.wait(5)
        te = _hold("exclusive", "big", ee, re_)
        assert ee.wait(5)  # the parked parent does not block the drain
        re_.set()
        rp.set()
        tp.join(5)
        te.join(5)
    finally:
        _globals.uninstall()


def test_inherited_lineage_bypasses_the_bars():
    _globals.install()
    try:
        ee, re_ = threading.Event(), threading.Event()
        ei, ri = threading.Event(), threading.Event()
        te = _hold("exclusive", "holder", ee, re_)
        assert ee.wait(5)
        ti = _hold(None, "child", ei, ri, inherited=True)
        assert ei.wait(5)  # a lineage child extends the hold, never contends
        ri.set()
        re_.set()
        ti.join(5)
        te.join(5)
    finally:
        _globals.uninstall()


# --- serial/exclusive tasks own the real globals ------------------------------


def test_serial_task_owns_the_real_globals(tmp_path, monkeypatch):
    monkeypatch.delenv("SERIAL_ENV", raising=False)
    (tmp_path / "sub").mkdir()
    before = _globals.real_getcwd()
    seen = {}

    def tasks(reg):
        @reg.task(serial=True)
        def own():
            seen["cwd"] = _globals.real_getcwd()  # really chdir-ed
            os.environ["SERIAL_ENV"] = "real"  # passthrough, no scoping
            seen["visible"] = os.environ["SERIAL_ENV"]
            os.chdir(tmp_path / "sub")  # the guards stand down
            seen["moved"] = _globals.real_getcwd()

    results = drive(tasks, "own", cwd=tmp_path)
    assert results[0].ok, results[0].error
    assert seen["cwd"] == str(tmp_path)
    assert seen["visible"] == "real"
    assert seen["moved"] == str(tmp_path / "sub")
    assert _globals.real_getcwd() == before  # both restored after the body
    assert "SERIAL_ENV" not in os.environ


def test_exclusive_task_owns_the_real_globals(tmp_path):
    seen = {}

    def tasks(reg):
        @reg.task(exclusive=True)
        def big():
            seen["cwd"] = _globals.real_getcwd()

    results = drive(tasks, "big", cwd=tmp_path)
    assert results[0].ok, results[0].error
    assert seen["cwd"] == str(tmp_path)


# --- footman.chdir() ----------------------------------------------------------


def test_chdir_cm_errors_in_a_parallel_task(tmp_path):
    def tasks(reg):
        @reg.task
        def go():
            with chdir(tmp_path):
                pass

    results = drive(tasks, "go")
    assert not results[0].ok
    assert "footman.chdir()" in str(results[0].error)


def test_chdir_cm_in_a_serial_task(tmp_path):
    (tmp_path / "sub").mkdir()
    seen = {}

    def tasks(reg):
        @reg.task(serial=True)
        def go():
            with chdir(rel="sub"):
                seen["inside"] = _globals.real_getcwd()
            seen["after"] = _globals.real_getcwd()

    results = drive(tasks, "go", cwd=tmp_path)
    assert results[0].ok, results[0].error
    assert seen["inside"] == str(tmp_path / "sub")
    assert seen["after"] == str(tmp_path)  # restored to the task's own cwd


def test_chdir_cm_outside_a_run(tmp_path):
    before = _globals.real_getcwd()
    with chdir(tmp_path):
        assert _globals.real_getcwd() == str(tmp_path)
    assert _globals.real_getcwd() == before


def test_chdir_cm_relative_target_is_a_taught_error(tmp_path):
    with pytest.raises(TypeError, match="rel="), chdir("somewhere/relative"):
        pass  # pragma: no cover


# --- the stdin router ---------------------------------------------------------


def test_stdin_read_is_a_taught_error_in_a_parallel_task():
    def tasks(reg):
        @reg.task
        def go():
            input()

    results = drive(tasks, "go")
    assert not results[0].ok
    assert "ask()" in str(results[0].error)


def test_every_stdin_door_is_guarded_not_just_input():
    # `input()` reaches the guard through `readline`; `read`, `readlines`
    # and iteration are their own doors, and any of them deleted from
    # `_GuardedStdin` left the suite green (audit, suite pass) — a parallel
    # task could then consume the one terminal through the unguarded door.
    def build(reader):
        def tasks(reg):
            @reg.task
            def go():
                reader()

        return tasks

    doors = {
        "read": lambda: sys.stdin.read(),
        "readline": lambda: sys.stdin.readline(),
        "readlines": lambda: sys.stdin.readlines(),
        "iter": lambda: next(iter(sys.stdin)),
    }
    for name, reader in doors.items():
        results = drive(build(reader), "go")
        assert not results[0].ok, f"{name} went unguarded"
        assert "ask()" in str(results[0].error), name


def test_stdin_passes_through_for_an_interactive_task(monkeypatch):
    import io as _io

    monkeypatch.setattr(sys, "stdin", _io.StringIO("typed answer\n"))
    seen = {}

    def tasks(reg):
        @reg.task(interactive=True)
        def wizard():
            seen["line"] = sys.stdin.readline().strip()

    results = drive(tasks, "wizard")
    assert results[0].ok, results[0].error
    assert seen["line"] == "typed answer"


def test_stdin_passes_through_for_a_serial_task(monkeypatch):
    import io as _io

    monkeypatch.setattr(sys, "stdin", _io.StringIO("serial line\n"))
    seen = {}

    def tasks(reg):
        @reg.task(serial=True)
        def own():
            seen["line"] = sys.stdin.readline().strip()

    results = drive(tasks, "own")
    assert results[0].ok, results[0].error
    assert seen["line"] == "serial line"


def test_stdin_untouched_outside_a_run():
    before = sys.stdin
    _globals.install()
    try:
        assert sys.stdin is not before  # wrapped for the run
    finally:
        _globals.uninstall()
    assert sys.stdin is before  # and restored


# --- the console lane (interactive overlaps the pool) -------------------------


def test_interactive_overlaps_the_parallel_pool():
    # A cross-handshake that only completes when the two nodes truly run
    # concurrently: the old model (interactive forces the whole run
    # sequential) would deadlock both waits and fail loudly.
    from livery.footman import _schedule

    e_wizard, e_sibling = threading.Event(), threading.Event()
    reg = Group("root")

    @reg.task(interactive=True)
    def wizard():
        e_wizard.set()
        assert e_sibling.wait(5), "sibling never ran while the wizard held"

    @reg.task
    def sibling():
        assert e_wizard.wait(5), "wizard never started alongside"
        e_sibling.set()

    tree = _manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, ["wizard", "sibling"])
    results = _schedule.run_plan(reg, segments)
    assert all(r.ok for r in results), [str(r.error) for r in results]


def test_console_lane_has_one_owner_at_a_time():
    from livery.footman import _schedule

    holds: list[tuple[str, float]] = []
    guard = threading.Lock()

    def _mark(tag):
        with guard:
            holds.append((tag, time.monotonic()))

    reg = Group("root")

    @reg.task(interactive=True)
    def first():
        _mark("first-in")
        time.sleep(0.2)
        _mark("first-out")

    @reg.task(interactive=True)
    def second():
        _mark("second-in")
        time.sleep(0.2)
        _mark("second-out")

    tree = _manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, ["first", "second"])
    results = _schedule.run_plan(reg, segments)
    assert all(r.ok for r in results), [str(r.error) for r in results]
    stamps = dict(holds)
    windows = sorted(
        [
            (stamps["first-in"], stamps["first-out"]),
            (stamps["second-in"], stamps["second-out"]),
        ]
    )
    assert windows[0][1] <= windows[1][0]  # the terminal has one owner


def test_console_gate_queues_until_the_console_frees():
    _globals.install()
    try:
        entered, release = threading.Event(), threading.Event()
        passed = threading.Event()
        holder = _hold(None, "wizard", entered, release)
        assert entered.wait(5)

        def flusher():
            with _globals.console_gate():
                passed.set()

        # No console owner yet from _hold (console=False): the gate is open.
        t = threading.Thread(target=flusher, daemon=True)
        t.start()
        assert passed.wait(5)
        release.set()
        holder.join(5)

        # Now with a real console owner: the gate queues.
        entered2, release2 = threading.Event(), threading.Event()

        def console_holder():
            with _globals.lane(None, name="wizard", console=True):
                entered2.set()
                release2.wait(timeout=10)

        h = threading.Thread(target=console_holder, daemon=True)
        h.start()
        assert entered2.wait(5)
        passed2 = threading.Event()

        def flusher2():
            with _globals.console_gate():
                passed2.set()

        t2 = threading.Thread(target=flusher2, daemon=True)
        t2.start()
        time.sleep(0.15)
        assert not passed2.is_set()  # queued behind the wizard
        release2.set()
        assert passed2.wait(5)  # and lands when the terminal frees
        h.join(5)
        t2.join(5)
    finally:
        _globals.uninstall()


# --- the abort latch is run-scoped --------------------------------------------


def test_abort_latch_clears_after_a_failed_run():
    # A run that ends in a failed task latches fail-fast; the latch must die
    # with the run — a *later* bare run() (no scheduler, so no start-of-run
    # reset) must not have its freshly registered child reaped at birth.
    from livery.footman.context import Context, use_context

    def tasks(reg):
        @reg.task
        def boom():
            raise RuntimeError("x")

    results = drive(tasks, "boom")
    assert not results[0].ok
    with use_context(Context()):
        r = run([sys.executable, "-c", "print('alive')"], recorded=False)
    assert r == 0
    assert r.stdout.strip() == "alive"


# --- the status line suspends for a console owner -----------------------------


def test_console_lane_suspends_the_status_line():
    from livery.footman import context

    calls = []

    class _FakeStatus:
        def __init__(self):
            self.counted: dict[str, tuple[int, int]] = {}

        def suspend(self):
            calls.append("suspend")

        def resume(self):
            calls.append("resume")

        def unit_added(self, count=1):
            pass

        def unit_started(self, name):
            pass

        def unit_counted(self, name, done, total):
            pass

        def unit_finished(self, name, ok):
            pass

        def unit_skipped(self, name):
            pass

        def notify(self, s):
            pass

    _globals.install()
    context.set_status(_FakeStatus())
    try:
        with _globals.lane(None, name="wizard", console=True):
            assert calls == ["suspend"]  # paused for exactly the ownership
        assert calls == ["suspend", "resume"]
    finally:
        context.set_status(None)
        _globals.uninstall()


def test_status_line_suspend_stops_painting():
    import io as _io

    from livery.footman._progress import StatusLine

    err = _io.StringIO()
    line = StatusLine(err, None, color=False)
    line.unit_added(1)
    line.unit_started("wizard")  # paints
    assert err.getvalue()
    line.suspend()
    before = err.getvalue()
    line.paint()  # a tick while a wizard owns the terminal: no repaint
    assert err.getvalue() == before
    line.resume()  # repaints immediately, and truthfully
    assert len(err.getvalue()) > len(before)


# --- the argv router ----------------------------------------------------------


def test_argv_router_gives_each_thread_its_own_view():
    import sys as _s

    _globals.install()
    try:
        alias = _s.argv  # a `from sys import argv`-style alias: same object
        seen = {}
        barrier = threading.Barrier(2, timeout=5)

        def worker(name, args):
            with _globals.argv_override(args):
                barrier.wait()  # both overrides live at once
                seen[name] = (list(_s.argv), alias[0], len(_s.argv))

        a = threading.Thread(target=worker, args=("a", ["tool-a", "--x"]))
        b = threading.Thread(target=worker, args=("b", ["tool-b"]))
        a.start()
        b.start()
        a.join(5)
        b.join(5)
        assert seen["a"] == (["tool-a", "--x"], "tool-a", 2)
        assert seen["b"] == (["tool-b"], "tool-b", 1)
    finally:
        _globals.uninstall()
    assert not isinstance(_s.argv, _globals._ArgvProxy)  # restored


def test_argv_override_mutations_stay_in_the_view(monkeypatch):
    import sys as _s

    _globals.install()
    try:
        real_before = list(_globals._argv_saved)
        with _globals.argv_override(["legacy", "one", "two"]):
            _s.argv.pop(0)  # the classic legacy-main idiom
            _s.argv.append("three")
            assert list(_s.argv) == ["one", "two", "three"]
        assert list(_globals._argv_saved) == real_before  # the real argv untouched
    finally:
        _globals.uninstall()


def test_every_argv_mutation_stays_in_the_view():
    # The proxy overrides every Python-level list operation, and several
    # could be deleted with the suite staying green (audit, suite pass) —
    # a legacy main using exactly that operation would then mutate the
    # process's real argv from inside its private view.
    import sys as _s

    _globals.install()
    try:
        base_before = list(_s.argv)
        with _globals.argv_override(["tool", "one", "two"]):
            _s.argv[1] = "ONE"  # __setitem__
            assert list(_s.argv) == ["tool", "ONE", "two"]
            del _s.argv[2]  # __delitem__
            assert list(_s.argv) == ["tool", "ONE"]
            _s.argv.extend(["x", "y"])  # extend
            _s.argv.insert(1, "zero")  # insert
            assert list(_s.argv) == ["tool", "zero", "ONE", "x", "y"]
            assert "zero" in _s.argv  # __contains__
            assert _s.argv == ["tool", "zero", "ONE", "x", "y"]  # __eq__
            # Concatenation on purpose: `+` IS `__add__`, the operation
            # under test — a splat would test nothing.
            added = _s.argv + ["tail"]  # noqa: RUF005
            assert added == ["tool", "zero", "ONE", "x", "y", "tail"]
            assert "zero" in repr(_s.argv)  # __repr__ shows the view
            _s.argv.clear()  # clear
            assert len(_s.argv) == 0
        assert list(_s.argv) == base_before  # the base never moved
    finally:
        _globals.uninstall()


def test_zero_arg_entry_parallelises_via_the_router(monkeypatch):
    # Two legacy zero-arg mains overlapping, each reading its own argv —
    # the cross-handshake deadlocks-and-fails if they serialise on a lock.
    import sys as _s

    import livery.toolroom as _tools

    e1, e2 = threading.Event(), threading.Event()
    seen = {}

    def make_entry(name, wait_for, then_set):
        def entry():  # zero-arg: reads sys.argv like an old argparse main
            then_set.set()
            assert wait_for.wait(5), "the sibling never ran alongside"
            seen[name] = list(_s.argv)
            return 0

        return entry

    entries = {
        "tool-a": make_entry("a", e2, e1),
        "tool-b": make_entry("b", e1, e2),
    }

    class _EP:
        def __init__(self, target):
            self._t = target

        def load(self):
            return self._t

    monkeypatch.setattr(
        _tools, "_console_entrypoint", lambda name: _EP(entries.get(name))
    )

    def tasks(reg):
        @reg.task
        def one():
            _tools.Tool("tool-a", in_process=True)("--x")

        @reg.task
        def two():
            _tools.Tool("tool-b", in_process=True)()

    from livery.footman import _schedule

    reg = Group("root")
    tasks(reg)
    tree = _manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, ["one", "two"])
    results = {r.task: r for r in _schedule.run_plan(reg, segments)}
    assert all(r.ok for r in results.values()), [str(r.error) for r in results.values()]
    assert seen["a"] == ["tool-a", "--x"]
    assert seen["b"] == ["tool-b"]


def _mp_read_force_color(path):
    import os as _os

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(str(_os.environ.get("FORCE_COLOR")))


def test_multiprocessing_workers_inherit_the_run_wide_colour(tmp_path, monkeypatch):
    # A spawn worker bypasses the env router and reads the *real*
    # environment — which carries the run-wide colour decision, because
    # color_environment publishes into it before any task runs. The note's
    # warning is only about the task overlay.
    import multiprocessing

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    out = tmp_path / "fc.txt"

    def tasks(reg):
        @reg.task
        def spawn_worker():
            proc = multiprocessing.get_context("spawn").Process(
                target=_mp_read_force_color, args=(str(out),)
            )
            proc.start()
            proc.join()

    results = drive(tasks, "spawn-worker", force_color=True)
    assert results[0].ok, results[0].error
    assert out.read_text(encoding="utf-8") == "1"  # colour rode the real env


def test_argv_in_place_mutation_stays_in_the_view():
    """`sys.argv += [...]` is a plausible thing for a legacy `main()` to do —
    append a default, then read it back. Without `__iadd__` it falls through
    to `list.__iadd__`, which mutates the proxy's *base* storage: the append
    vanishes from the caller's own view (reads consult the override) and
    leaks into every call that has none."""
    import sys as _s

    _globals.install()
    try:
        base_before = list(_s.argv)
        with _globals.argv_override(["tool", "--x"]):
            _s.argv += ["--added"]
            assert list(_s.argv) == ["tool", "--x", "--added"]  # the caller sees it
        assert list(_s.argv) == base_before  # and nothing leaked into the base
    finally:
        _globals.uninstall()


def test_argv_reordering_stays_in_the_view():
    """`sort`/`reverse` are the same shape as `__iadd__`: unoverridden, they
    reorder the base list while the caller's view is untouched."""
    import sys as _s

    _globals.install()
    try:
        base_before = list(_s.argv)
        with _globals.argv_override(["tool", "b", "a"]):
            _s.argv.reverse()
            assert list(_s.argv) == ["a", "b", "tool"]
            _s.argv.sort()
            assert list(_s.argv) == ["a", "b", "tool"]
            assert list(reversed(_s.argv)) == ["tool", "b", "a"]
        assert list(_s.argv) == base_before
    finally:
        _globals.uninstall()


def test_base_env_drops_the_host_interpreter(monkeypatch):
    """PYTHONHOME/PYTHONEXECUTABLE never reach a task; PYTHONPATH does.

    On Windows `uv run` exports PYTHONHOME pointing at footman's own
    environment. Inherited by a console script from any other Python, it
    loads the wrong stdlib and dies during startup — 107 tools read as holes
    in one walk. PYTHONPATH stays because people export it deliberately.
    """
    monkeypatch.setenv("PYTHONHOME", "/nonexistent")
    monkeypatch.setenv("PYTHONEXECUTABLE", "/nonexistent/python")
    monkeypatch.setenv("PYTHONPATH", "/deliberate")

    base = _globals.base_env()

    assert "PYTHONHOME" not in base
    assert "PYTHONEXECUTABLE" not in base
    assert base["PYTHONPATH"] == "/deliberate"


def test_a_task_never_inherits_the_host_interpreter(monkeypatch):
    monkeypatch.setenv("PYTHONHOME", "/nonexistent")
    monkeypatch.setenv("PYTHONPATH", "/deliberate")
    seen = {}

    def tasks(reg):
        @reg.task
        def go():
            seen["home"] = os.environ.get("PYTHONHOME", "(absent)")
            seen["path"] = os.environ.get("PYTHONPATH", "(absent)")

    results = drive(tasks, "go")
    assert results[0].ok
    assert seen == {"home": "(absent)", "path": "/deliberate"}


def test_the_child_of_a_task_never_inherits_it(monkeypatch):
    monkeypatch.setenv("PYTHONHOME", "/nonexistent")

    def tasks(reg):
        @reg.task
        def go():
            run(
                [
                    sys.executable,
                    "-c",
                    "import os; print(os.environ.get('PYTHONHOME', '(absent)'))",
                ]
            )

    results = drive(tasks, "go")
    assert results[0].ok
    assert results[0].steps[0].output.strip() == "(absent)"


def test_a_deliberate_setting_still_reaches_the_child(monkeypatch):
    """Inheritance is what is dropped, not the variable. A task that means it
    gets exactly what it asked for — by either spelling.

    Asserted on the environment footman hands over rather than on a child's
    own report: a real interpreter handed a bogus PYTHONHOME dies during
    startup, which would test CPython rather than footman.
    """
    from livery.footman import context as context_mod

    monkeypatch.delenv("PYTHONHOME", raising=False)
    hand_over: dict[str, dict[str, str]] = {}

    def fake_run(argv, env, cwd, capture, *a, **kw):
        hand_over[argv[-1]] = dict(env)
        return 0, "", "", False

    monkeypatch.setattr(context_mod, "_run_subprocess", fake_run)

    def tasks(reg):
        @reg.task
        def by_environ():
            os.environ["PYTHONHOME"] = "chosen"
            run(["tool", "by_environ"])

        @reg.task
        def by_env_argument():
            run(["tool", "by_env_argument"], env={**os.environ, "PYTHONHOME": "handed"})

        @reg.task
        def by_neither():
            run(["tool", "by_neither"])

    for name in ("by-environ", "by-env-argument", "by-neither"):
        assert drive(tasks, name)[0].ok

    assert hand_over["by_environ"]["PYTHONHOME"] == "chosen"
    assert hand_over["by_env_argument"]["PYTHONHOME"] == "handed"
    assert "PYTHONHOME" not in hand_over["by_neither"]
