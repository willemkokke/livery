"""Signals: a supervisor's stop, and the stack dump that is not one."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import IO

import pytest

from livery.footman import _app, _paths, _signals

# A task that asks its *own* process to stop, the way `kill` or `docker stop`
# would. The handler check is the seatbelt: an unhandled SIGTERM would kill
# the pytest worker outright, so a missing install has to read as a failed
# assertion rather than a dead process that reports nothing.
STOPPER = '''
import signal

from livery.footman import task

@task
def stop(sig: str = "SIGTERM"):
    """Ask this process to stop."""
    number = getattr(signal, sig)
    if signal.getsignal(number) in (signal.SIG_DFL, signal.SIG_IGN):
        raise AssertionError(f"{sig} reached the run with no handler installed")
    signal.raise_signal(number)
'''


def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tasks: str) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(tasks)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")


def _runner(
    tmp_path: Path, *args: str, stderr: int | IO[str], env: dict[str, str] | None = None
) -> subprocess.Popen[str]:
    """`fm <args>` as a real process in its own session.

    Its own session because these tests are entirely about which signal
    reaches whom: the runner must be signalled by hand, never as a bystander
    of whatever the test process is in.
    """
    environ = {
        **os.environ,
        "FOOTMAN_CACHE_DIR": str(tmp_path / ".cache"),
        **(env or {}),
    }
    environ.pop("VIRTUAL_ENV", None)
    return subprocess.Popen(
        [sys.executable, "-m", "livery.footman", *args],
        cwd=tmp_path,
        env=environ,
        stdout=subprocess.PIPE,
        stderr=stderr,
        text=True,
        start_new_session=True,
    )


def _await(condition, what: str, seconds: float = 30.0, saw=None) -> None:
    """Wait for *condition*, or fail saying what was there instead.

    These tests wait on a real process writing to a real file, so a timeout
    is either a regression or the runner being slow — and the two are told
    apart only by what the file held. *saw* is called on failure to put that
    in the message, because a CI failure nobody can reproduce locally is
    worth as much as the evidence it carries and no more.
    """
    deadline = time.time() + seconds
    while time.time() < deadline:
        if condition():
            return
        time.sleep(0.05)
    detail = ""
    if saw is not None:
        try:
            detail = f"\ninstead, after {seconds:g}s:\n{saw()!r}"
        except Exception as exc:  # pragma: no cover - diagnostics must not mask
            detail = f"\n(could not read what was there: {exc!r})"
    pytest.fail(f"{what} never happened; the test proves nothing{detail}")


# --- a stop asked for by signal ----------------------------------------------


def test_sigterm_exits_143_and_sighup_129(tmp_path, monkeypatch, capsys):
    if sys.platform == "win32":
        # In the body rather than a decorator, so the checkers narrow the
        # POSIX-only names below the same way `_signals._bindings` does.
        pytest.skip("SIGTERM and SIGHUP are POSIX")
    _project(tmp_path, monkeypatch, STOPPER)

    assert _app.run(["stop"]) == 143
    assert "terminated" in capsys.readouterr().err
    assert _app.run(["--sequential", "stop"]) == 143
    assert "terminated" in capsys.readouterr().err
    assert _app.run(["stop", "--sig=SIGHUP"]) == 129
    assert "hung up" in capsys.readouterr().err


def test_the_json_envelope_survives_a_stop(tmp_path, monkeypatch, capsys):
    # The same promise 130 already keeps: task output is buffered, so nothing
    # has reached stdout yet and a machine consumer still gets one document.
    if sys.platform == "win32":
        pytest.skip("SIGTERM is POSIX")
    _project(tmp_path, monkeypatch, STOPPER)

    assert _app.run(["--json", "stop"]) == 143
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == {"code": 143, "message": "terminated"}
    assert payload["items"] == []


def test_only_the_first_stop_raises():
    if sys.platform == "win32":
        pytest.skip("SIGTERM is POSIX")
    with _signals.installed():
        assert callable(signal.getsignal(signal.SIGTERM))
        with pytest.raises(_signals.Stop) as caught:
            signal.raise_signal(signal.SIGTERM)
        assert (caught.value.word, caught.value.code) == ("terminated", 143)
        # A cancelled GitHub job sends SIGINT and then SIGTERM: a second raise
        # would land inside the reap and abandon it half done.
        signal.raise_signal(signal.SIGTERM)


def test_the_handlers_that_were_there_come_back():
    if sys.platform == "win32":
        pytest.skip("SIGTERM is POSIX")
    before = signal.getsignal(signal.SIGTERM)
    with _signals.installed():
        assert signal.getsignal(signal.SIGTERM) is not before
    assert signal.getsignal(signal.SIGTERM) is before


def test_sigterm_reaps_the_child_a_task_was_waiting_on(tmp_path):
    """A supervisor's stop left `fm` dead and its whole tree running.

    SIGTERM at its default disposition kills footman where it stands, and
    the children are in their own process groups on purpose, so `kill`,
    `docker stop` or a cancelled CI job reached the runner and nothing else.
    Driven as a real process, signalled by pid alone — the way a supervisor
    does it, never the group, which is exactly what makes the child survive.
    """
    if sys.platform == "win32":
        pytest.skip("POSIX process groups and SIGTERM")

    pid_file = tmp_path / "child.pid"
    (tmp_path / "tasks.py").write_text(
        textwrap.dedent(f"""
        import sys
        from livery.footman import run, task

        @task
        def slow():
            run([sys.executable, "-c",
                 "import os, time;"
                 "open({str(pid_file)!r}, 'w').write(str(os.getpid()));"
                 "time.sleep(120)"])
        """)
    )
    runner = _runner(tmp_path, "slow", stderr=subprocess.PIPE)
    try:
        # Content, not existence: creating the file and writing the pid are
        # two steps, and `int("")` off the mid-write gap took a CI run down
        # — the third test this same race has bitten, same cure as the rest.
        _await(
            lambda: pid_file.exists() and bool(pid_file.read_text().strip()),
            "the child recorded its pid",
        )
        child = int(pid_file.read_text())

        os.kill(runner.pid, signal.SIGTERM)  # what a supervisor does
        assert runner.wait(timeout=30) == 143
        assert "terminated" in (runner.stderr.read() if runner.stderr else "")

        gone_by = time.time() + 15
        while time.time() < gone_by:
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            os.kill(child, signal.SIGKILL)  # do not leak it into the suite
            pytest.fail("the child outlived the stop")
    finally:
        if runner.poll() is None:  # pragma: no cover - only on a regression
            runner.kill()


def test_an_interrupt_reaps_the_scheduler_pools_children(tmp_path):
    """The parallel scheduler's own abort reap, exercised at last.

    Deleting `_run_parallel`'s except-BaseException block left the whole
    suite green (measured by mutation) while a real Ctrl-C would wait out
    every child in flight: the workers sit in communicate() on
    group-isolated children the terminal's signal never reaches, so only
    the scheduler's reap can end them. Two scheduler nodes in flight, so
    the abort lands in the pool's handler; signalled by pid alone, so the
    children owe their deaths to the reap and nothing else.
    """
    if sys.platform == "win32":
        pytest.skip("POSIX signals")

    pid_a, pid_b = tmp_path / "a.pid", tmp_path / "b.pid"
    (tmp_path / "tasks.py").write_text(
        textwrap.dedent(f"""
        import sys
        from livery.footman import run, task

        def _spawn(pidfile):
            run([sys.executable, "-c",
                 "import os, time, sys;"
                 "open(sys.argv[1], 'w').write(str(os.getpid()));"
                 "time.sleep(120)", pidfile])

        @task
        def a():
            _spawn({str(pid_a)!r})

        @task
        def b():
            _spawn({str(pid_b)!r})
        """)
    )
    runner = _runner(tmp_path, "a", "b", stderr=subprocess.PIPE)
    try:
        _await(
            lambda: all(p.exists() and p.read_text().strip() for p in (pid_a, pid_b)),
            "both children started",
        )
        children = [int(p.read_text()) for p in (pid_a, pid_b)]
        os.kill(runner.pid, signal.SIGINT)  # Ctrl-C, to the pid alone
        assert runner.wait(timeout=30) == 130

        gone_by = time.time() + 15
        still = list(children)
        while time.time() < gone_by:
            still = [pid for pid in children if _alive(pid)]
            if not still:
                break
            time.sleep(0.05)
        else:
            for pid in children:  # do not leak them into the suite
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)
            pytest.fail(f"children outlived the interrupt: {still}")
    finally:
        if runner.poll() is None:  # pragma: no cover - only on a regression
            runner.kill()


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def test_sigterm_spares_an_atomic_child(tmp_path):
    """The atomic carve-out, as documented: a stop ends the run, not the child.

    `atomic=True` promises the subprocess runs to completion so a mid-write
    can't be truncated — and "to completion" has to survive the one signal a
    supervisor actually sends. The child is deliberately unregistered, so
    the stop's reaper never sees it: footman exits 143 and the child
    finishes its write on its own, which is the promise kept, not a leak.
    """
    if sys.platform == "win32":
        pytest.skip("POSIX process groups and SIGTERM")

    pid_file = tmp_path / "child.pid"
    marker = tmp_path / "finished"
    (tmp_path / "tasks.py").write_text(
        textwrap.dedent(f"""
        import sys
        from livery.footman import run, task

        @task(atomic=True)
        def protected():
            run([sys.executable, "-c",
                 "import os, time;"
                 "open({str(pid_file)!r}, 'w').write(str(os.getpid()));"
                 "time.sleep(1.5);"
                 "open({str(marker)!r}, 'w').write('done')"])
        """)
    )
    runner = _runner(tmp_path, "protected", stderr=subprocess.PIPE)
    try:
        _await(pid_file.exists, "the child started")
        os.kill(runner.pid, signal.SIGTERM)  # what a supervisor does
        assert runner.wait(timeout=30) == 143
        _await(
            marker.exists,
            "the atomic child finished its write",
            saw=lambda: sorted(p.name for p in tmp_path.iterdir()),
        )
    finally:
        if runner.poll() is None:  # pragma: no cover - only on a regression
            runner.kill()


# --- the dump, which is diagnostic and not a stop -----------------------------


DUMPEE = """
import time
from pathlib import Path

from livery.footman import task

@task
def wait():
    Path({ready!r}).write_text("here")
    deadline = time.time() + 30
    while time.time() < deadline and not Path({go!r}).exists():
        time.sleep(0.05)
    Path({done!r}).write_text("finished")
"""


def test_sigquit_dumps_every_thread_and_the_run_carries_on(tmp_path):
    # Hitting it twice and watching whether the frames moved is how a hang
    # tells itself apart from slow progress, so the dump must not shut
    # anything down.
    if sys.platform == "win32":
        pytest.skip("SIGQUIT is POSIX")

    ready, go, done = (tmp_path / n for n in ("ready", "go", "done"))
    (tmp_path / "tasks.py").write_text(
        DUMPEE.format(ready=str(ready), go=str(go), done=str(done))
    )
    err_file = tmp_path / "stderr.txt"
    with err_file.open("w") as sink:
        runner = _runner(tmp_path, "wait", stderr=sink)
        try:
            _await(ready.exists, "the task started")
            os.kill(runner.pid, signal.SIGQUIT)
            # Wait for the frame, not for the header. faulthandler writes the
            # "Current thread …" line and the frames under it as separate
            # writes, so a reader that stops at the header can catch the file
            # between them and see a dump with no stack in it — which is what
            # a loaded CI runner does, and a laptop almost never does.
            _await(
                lambda: "in wait" in err_file.read_text(),
                "a dump naming the task's own frame",
                saw=err_file.read_text,
            )
            go.write_text("go")
            assert runner.wait(timeout=30) == 0
        finally:
            if runner.poll() is None:  # pragma: no cover - only on a regression
                runner.kill()
    assert done.exists()  # it kept running after the dump, and finished


@pytest.mark.skipif(
    sys.version_info < (3, 13),
    reason="CPython <=3.12: the faulthandler watchdog can be lost to a "
    "tstate teardown race (reproduced bare: a dump lands on '<tstate is "
    "freed>', truncates mid-line, and the repeat loop wedges — after which "
    "cancel_dump_traceback_later deadlocks its caller). Fixed in 3.13. "
    "The test cannot tell that interpreter race from a product regression, "
    "and it fired exactly that way on a loaded macOS 3.11 runner.",
)
def test_the_timer_dumps_where_there_is_no_key_to_press(tmp_path):
    # Nobody is at a keyboard when CI hangs, and Windows has no SIGQUIT, so
    # the same dump is reachable without a signal at all.
    ready, go, done = (tmp_path / n for n in ("ready", "go", "done"))
    (tmp_path / "tasks.py").write_text(
        DUMPEE.format(ready=str(ready), go=str(go), done=str(done))
    )
    err_file = tmp_path / "stderr.txt"
    with err_file.open("w") as sink:
        runner = _runner(
            tmp_path, "wait", stderr=sink, env={"FOOTMAN_STACKS_AFTER": "0.25"}
        )
        try:
            # Two waits, two clocks. Startup first, on the task's own
            # `ready` file and a generous window: a loaded CI runner can
            # spend most of a minute just reaching the body, and that is
            # slowness, not a dump that failed to land (a macOS runner
            # proved it by spending the whole old window starting up).
            _await(
                ready.exists,
                "the task started",
                seconds=60,
                saw=err_file.read_text,
            )
            # Then the dump that names the task's own frame, not merely the
            # first dump — the timer arms at startup and repeats, so early
            # ones fire inside `runpy` and name nothing useful. With the
            # body provably running and the timer repeating every 250 ms,
            # this clock is fair. (Free-threaded Windows found the
            # first-dump trap: 250 ms is less than its startup.)
            _await(
                lambda: "in wait" in err_file.read_text(),
                "a dump naming the task's own frame",
                saw=err_file.read_text,
            )
            go.write_text("go")
            assert runner.wait(timeout=30) == 0
        finally:
            if runner.poll() is None:  # pragma: no cover - only on a regression
                runner.kill()
    assert done.exists()


def test_a_typo_in_the_stacks_variable_says_so(monkeypatch, capsys):
    name = _paths.env_var(_signals.STACKS_AFTER)
    monkeypatch.setenv(name, "30")
    assert _signals._seconds_from_env() == 30.0
    monkeypatch.setenv(name, "soon")
    assert _signals._seconds_from_env() is None
    assert f"{name}=soon" in capsys.readouterr().err
    monkeypatch.setenv(name, "0")
    assert _signals._seconds_from_env() is None
    assert f"{name}=0" in capsys.readouterr().err
    monkeypatch.delenv(name)
    assert _signals._seconds_from_env() is None
    assert capsys.readouterr().err == ""
