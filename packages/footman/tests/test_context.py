"""The run context: run() (subprocess/in-process), ctx injection, tools."""

from __future__ import annotations

import io
import os
import sys
import textwrap
from pathlib import Path
from typing import Annotated, Literal

import pytest

import toolroom as tools
from livery.footman import _manifest
from livery.footman._executor import run_chain
from livery.footman._split import split_chain
from livery.footman._step import step
from livery.footman.context import (
    Context,
    Invocation,
    RunFailed,
    parallel,
    passthrough,
    run,
    use_context,
)
from livery.footman.params import Many, Secret, ask, between, suggest
from livery.footman.registry import Group


def drive(build, line, **cfg):
    reg = Group("root")
    build(reg)
    tree = _manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, line.split())
    return reg, tree, run_chain(reg, segments, ctx_config=cfg)


def _echo(text: str) -> str:
    """A portable `echo <text>`: a run() string every machine can execute.

    The suite must not assume coreutils on PATH — Windows only has an
    `echo.exe` when Git's `usr/bin` happens to be there (CI runners: yes;
    every dev box: no). The interpreter running the suite is the one program
    that always exists, and the quoting survives both grammars a run()
    string meets: CreateProcess parsing on Windows, shlex on POSIX.
    """
    return f'"{sys.executable}" -c "print(\'{text}\')"'


def _exit(code: int) -> str:
    """A portable `false` (and `true`): exits with *code*, prints nothing."""
    return f'"{sys.executable}" -c "raise SystemExit({code})"'


# --- the colour predicate -----------------------------------------------------


def test_colored_predicate(monkeypatch):
    from livery.footman.context import _colored

    monkeypatch.delenv("NO_COLOR", raising=False)
    # never wins over everything; always forces on even off a terminal;
    # otherwise tty decides.
    assert _colored(Context(no_color=True, force_color=True, tty=True)) is False
    assert _colored(Context(force_color=True, tty=False)) is True
    assert _colored(Context(tty=True)) is True
    assert _colored(Context(tty=False)) is False
    # NO_COLOR in the environment bows out the auto path, but not a forced one.
    monkeypatch.setenv("NO_COLOR", "1")
    assert _colored(Context(tty=True)) is False
    assert _colored(Context(force_color=True, tty=False)) is True


# --- the attendance readers ---------------------------------------------------


def test_attended_reader(monkeypatch):
    from livery.footman import context
    from livery.footman.context import attended

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    with use_context(Context()):
        assert attended() is True
    with use_context(Context(no_input=True)):
        assert attended() is False
    with use_context(Context(dry_run=True)):
        assert attended() is False
    # --yes deliberately does not count as unattended: it auto-answers
    # confirm() gates but forbids nothing.
    with use_context(Context(assume_yes=True)):
        assert attended() is True
    monkeypatch.setattr(context, "_stdin_is_tty", lambda: False)
    with use_context(Context()):
        assert attended() is False


def test_tty_reader_ignores_colour_policy():
    from livery.footman.context import tty

    assert tty() is False  # outside a run, nothing is stamped
    # NO_COLOR undresses the output; the person is still watching.
    with use_context(Context(terminal=True, no_color=True)):
        assert tty() is True
    # Forced colour puts no terminal at the end of a pipe.
    with use_context(Context(terminal=False, force_color=True)):
        assert tty() is False


def test_colored_reader(monkeypatch):
    from livery.footman.context import colored

    monkeypatch.delenv("NO_COLOR", raising=False)
    with use_context(Context(tty=True)):
        assert colored() is True
    with use_context(Context(tty=True, no_color=True)):
        assert colored() is False
    with use_context(Context(force_color=True)):
        assert colored() is True


def test_color_env_helper():
    from livery.footman.context import color_env

    assert color_env(True) == {
        "FORCE_COLOR": "1",
        "CLICOLOR_FORCE": "1",
        "CLICOLOR": "1",
    }
    # off sets only NO_COLOR — forcing colour off is the *absence* of
    # FORCE_COLOR (`color_environment` clears it), never `FORCE_COLOR=0`, which a
    # presence-checking tool (ruff) would read as "force on".
    assert color_env(False) == {"NO_COLOR": "1"}


def test_run_colour_on_decision(monkeypatch):
    from livery.footman.context import run_colour_on

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm")
    d = run_colour_on
    assert d(no_color=True, force_color=False, capture=False, isatty=True) is False
    assert (
        d(no_color=False, force_color=True, capture=True, isatty=True) is False
    )  # json
    assert d(no_color=False, force_color=True, capture=False, isatty=False) is True
    assert d(no_color=False, force_color=False, capture=False, isatty=True) is True
    assert d(no_color=False, force_color=False, capture=False, isatty=False) is False


def test_color_environment_sets_once_and_restores(monkeypatch):
    from livery.footman.context import color_environment

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    with color_environment(True):
        assert os.environ["FORCE_COLOR"] == "1" and "NO_COLOR" not in os.environ
    assert "FORCE_COLOR" not in os.environ  # restored
    monkeypatch.setenv("FORCE_COLOR", "1")  # an inherited force...
    with color_environment(False):
        # ...is cleared, not set to "0" — off is FORCE_COLOR's absence.
        assert os.environ["NO_COLOR"] == "1" and "FORCE_COLOR" not in os.environ
    assert os.environ["FORCE_COLOR"] == "1"  # restored
    assert "NO_COLOR" not in os.environ


_READ_ENV = (
    "import os;"
    "print('FC=' + str(os.environ.get('FORCE_COLOR')),"
    "'NC=' + str(os.environ.get('NO_COLOR')))"
)


def _child_env(line, **cfg):
    def tasks(reg):
        @reg.task
        def show():
            print(run([sys.executable, "-c", _READ_ENV]).stdout.strip())

    _, _, results = drive(tasks, line, **cfg)
    return results[0].steps[0].stdout.strip()


def test_run_forces_color_env_for_a_child(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    # always -> force it on; never (and auto-when-piped) -> push monochrome down
    # as NO_COLOR with FORCE_COLOR *absent* (not "0", which ruff reads as on).
    assert _child_env("show", force_color=True) == "FC=1 NC=None"
    assert _child_env("show", no_color=True) == "FC=None NC=1"
    assert _child_env("show") == "FC=None NC=1"  # auto, no tty


def test_a_parallel_childs_environ_write_stays_its_own():
    # A child born inside parallel() owns a COPY of its parent's environment:
    # an os.environ write scopes to the child (and what it spawns), never to
    # the parent or a sibling — the environ router's own promise.
    seen = {}

    def tasks(reg):
        @reg.task
        def outer():
            def writer():
                os.environ["LEAKY"] = "1"

            parallel(step(writer)())
            seen["after"] = os.environ.get("LEAKY")

    _, _, results = drive(tasks, "outer")
    assert results[0].ok
    assert seen == {"after": None}


def test_run_color_overrides_the_run_ambient_per_call(monkeypatch):
    # The per-call twin: explicit beats the run-wide decision in either
    # direction, and auto changes nothing. Off is spelled as removal plus
    # NO_COLOR, exactly like the run boundary's own publish.
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)

    def tasks(reg):
        @reg.task
        def show(mode: str):
            out = run([sys.executable, "-c", _READ_ENV], color=mode)
            print(out.stdout.strip())

    _, _, results = drive(tasks, "show always")  # monochrome run, forced call
    assert "FC=1 NC=None" in results[0].steps[0].stdout
    _, _, results = drive(tasks, "show never", force_color=True)
    assert "FC=None NC=1" in results[0].steps[0].stdout
    _, _, results = drive(tasks, "show auto", force_color=True)
    assert "FC=1 NC=None" in results[0].steps[0].stdout


def test_a_toolroom_color_opt_reaches_an_env_reading_child(monkeypatch):
    # The colour seam end to end, both halves: toolroom (>= 0.4.0) passes
    # .opts(color=) through its private run() channel into run(color=), so a
    # hosted env-reading tool obeys the per-call decision — the argv half
    # always did; the environment half needed run(color=) on this side and
    # the passthrough on theirs.
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)

    def tasks(reg):
        @reg.task
        def probe(mode: Literal["auto", "always", "never"]):
            out = tools.python.opts(color=mode)("-c", _READ_ENV)
            print(out.stdout.strip())

    _, _, results = drive(tasks, "probe always")  # monochrome run, forced call
    assert "FC=1 NC=None" in results[0].steps[0].stdout
    _, _, results = drive(tasks, "probe never", force_color=True)
    assert "FC=None NC=1" in results[0].steps[0].stdout


def test_run_color_merges_on_top_of_an_explicit_env():
    # env= replaces wholesale; color= then paints that replacement, so the
    # two compose instead of the last one winning outright.
    def tasks(reg):
        @reg.task
        def show():
            out = run(
                [sys.executable, "-c", _READ_ENV],
                env={"NO_COLOR": "1"},
                color="always",
            )
            print(out.stdout.strip())

    _, _, results = drive(tasks, "show")
    assert "FC=1 NC=None" in results[0].steps[0].stdout


def test_a_steps_color_reaches_everything_the_body_calls(monkeypatch):
    # The whole point of putting colour on a step rather than on each call:
    # one decision, at the boundary of a body, that every run() inside it and
    # every tool it hosts reads — without threading a keyword through each.
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)

    def tasks(reg):
        @step
        def build():
            first = run([sys.executable, "-c", _READ_ENV])
            second = tools.python("-c", _READ_ENV)
            print(first.stdout.strip(), "|", second.stdout.strip())

        @reg.task
        def outer():
            build.opts(color="always")()()  # build the item, then run it

    _, _, results = drive(tasks, "outer")  # a monochrome run, forced step
    # The two children the body spawned — the plain run() and the hosted
    # tool — neither of them told about colour by its own call site. (The
    # step's own record holds the body's print, which quotes both.)
    children = [s.stdout.strip() for s in results[0].steps if "|" not in s.stdout]
    assert children == ["FC=1 NC=None", "FC=1 NC=None"], results[0].steps
    assert "NC=1" not in "".join(s.stdout for s in results[0].steps)


def test_a_steps_color_is_the_same_tri_state_run_takes(monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")

    def tasks(reg):
        @step
        def probe():
            print(run([sys.executable, "-c", _READ_ENV]).stdout.strip())

        @reg.task
        def never():
            probe.opts(color="never")()()

        @reg.task
        def auto():
            probe.opts(color="auto")()()

    _, _, results = drive(tasks, "never")
    assert "FC=None NC=1" in results[0].steps[0].stdout
    _, _, results = drive(tasks, "auto", force_color=True)
    assert "FC=1 NC=None" in results[0].steps[0].stdout


def test_a_steps_color_merges_on_top_of_its_env():
    # Same composition rule as run(): env= replaces wholesale, color= then
    # paints that replacement, so neither silently wins outright.
    def tasks(reg):
        @step
        def probe():
            print(run([sys.executable, "-c", _READ_ENV]).stdout.strip())

        @reg.task
        def outer():
            probe.opts(env={"NO_COLOR": "1"}, color="always")()()

    _, _, results = drive(tasks, "outer")
    assert "FC=1 NC=None" in results[0].steps[0].stdout


def test_a_bad_step_color_is_refused_like_a_bad_run_color():
    @step
    def probe(): ...

    with pytest.raises(ValueError, match=r"expects one of auto\|never\|always"):
        probe.opts(color="yes")


def test_run_color_reaches_the_in_process_lane(monkeypatch):
    # toolroom's hosted in-process lane rides the same door: the overlay the
    # callable reads through os.environ carries the per-call decision.
    monkeypatch.setenv("NO_COLOR", "1")
    seen = {}

    def probe():
        seen["fc"] = os.environ.get("FORCE_COLOR")
        seen["nc"] = os.environ.get("NO_COLOR")
        return 0

    inv = Invocation(parts=(("prog", "probe"),), exact=("probe",))
    run(probe, _show=inv, color="always")
    assert seen == {"fc": "1", "nc": None}


def test_run_color_refuses_an_unknown_mode():
    with pytest.raises(ValueError, match=r"run\(color='blue'\) expects"):
        run([sys.executable, "-c", "pass"], color="blue")


def test_task_env_overrides_the_color_overlay(monkeypatch):
    # The overlay is lowest precedence: a task's own env= still wins.
    monkeypatch.delenv("FORCE_COLOR", raising=False)

    def tasks(reg):
        @reg.task
        def show():
            out = run([sys.executable, "-c", _READ_ENV], env={"FORCE_COLOR": "3"})
            print(out.stdout.strip())

    _, _, results = drive(tasks, "show", force_color=True)
    assert "FC=3" in results[0].steps[0].stdout


def test_in_process_reads_the_run_wide_colour_env(monkeypatch):
    # Colour is published once at the run boundary, so an in-process tool reads
    # it from os.environ — no per-call patch (so no _process_state lock) — and it
    # is restored when the run ends.
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    seen = {}

    def tasks(reg):
        @reg.task
        def probe():
            def inproc():
                seen["fc"] = os.environ.get("FORCE_COLOR")
                seen["nc"] = os.environ.get("NO_COLOR")

            step(inproc)()()

    drive(tasks, "probe", force_color=True)  # always
    assert seen["fc"] == "1"
    assert "FORCE_COLOR" not in os.environ  # restored after the run
    drive(tasks, "probe", no_color=True)  # never
    assert seen["nc"] == "1"
    assert "NO_COLOR" not in os.environ


# --- run() -------------------------------------------------------------------


def test_run_subprocess_records_step():
    cmd = _echo("hi")

    def tasks(reg):
        @reg.task
        def build():
            run(cmd)

    _, _, results = drive(tasks, "build")
    assert results[0].ok
    step = results[0].steps[0]
    assert step.command == cmd and step.code == 0
    assert step.output.strip() == "hi"


def test_run_in_process_callable_captured():
    out = {}

    def tasks(reg):
        @reg.task
        def go():
            def tool():
                print("in-process")
                return 0

            out["value"] = step(tool)()()

    _, _, results = drive(tasks, "go")
    assert out["value"] == 0  # the body's return is data, handed back
    assert results[0].steps[0].output.strip() == "in-process"


def test_run_failed_raises_and_fails_task():
    def tasks(reg):
        @reg.task
        def build():
            run(_exit(1))

    _, _, results = drive(tasks, "build")
    assert results[0].ok is False
    assert isinstance(results[0].error, RunFailed)


def test_run_failure_propagates_command_code():
    def tasks(reg):
        @reg.task
        def build():
            run([sys.executable, "-c", "import sys; sys.exit(3)"])

    _, _, results = drive(tasks, "build")
    assert results[0].ok is False
    assert results[0].code == 3  # the command's own code, not a flat 1
    assert isinstance(results[0].error, RunFailed)


def test_run_nofail_returns_code():
    out = {}

    def tasks(reg):
        @reg.task
        def build():
            out["code"] = run(_exit(1), nofail=True)

    _, _, results = drive(tasks, "build")
    assert results[0].ok is True
    assert out["code"] == 1


def test_result_is_the_exit_code_int():
    # A Result *is* the exit code: the int idioms keep working, and it carries
    # the captured output and the `.ok` shorthand alongside.
    def tasks(reg):
        @reg.task
        def go():
            ok = run([sys.executable, "-c", "pass"])
            assert isinstance(ok, int) and ok == 0 and ok.ok and not bool(ok)
            bad = run([sys.executable, "-c", "import sys; sys.exit(3)"], nofail=True)
            assert bad == 3 and bad.code == 3 and not bad.ok and bool(bad)

    _, _, results = drive(tasks, "go")
    assert results[0].ok


def test_result_separates_subprocess_streams():
    def tasks(reg):
        @reg.task
        def go():
            run(
                [
                    sys.executable,
                    "-c",
                    "import sys; print('to-out'); print('to-err', file=sys.stderr)",
                ]
            )

    _, _, results = drive(tasks, "go")
    step = results[0].steps[0]
    assert step.stdout.strip() == "to-out"
    assert step.stderr.strip() == "to-err"
    # .output is the two joined (stdout first), computed — never stored.
    assert step.output == step.stdout + step.stderr


def test_result_separates_in_process_streams():
    # An in-process callable splits stdout/stderr exactly like a subprocess —
    # no user-visible difference between the two kinds of run().
    def tasks(reg):
        @reg.task
        def go():
            def tool():
                print("in-out")
                print("in-err", file=sys.stderr)

            step(tool)()()

    _, _, results = drive(tasks, "go")
    record = results[0].steps[0]
    assert record.stdout.strip() == "in-out"
    assert record.stderr.strip() == "in-err"


def test_parallel_in_process_separates_streams_under_routing():
    # The delicate path: run() inside a parallel child still splits the step's
    # streams, even though the child's task-level buffer stays combined for the
    # atomic flush.
    def tasks(reg):
        @reg.task
        def go():
            def x():
                print("x-out")
                print("x-err", file=sys.stderr)

            def y():
                print("y-out")
                print("y-err", file=sys.stderr)

            parallel(step(x)(), step(y)())

    _, _, results = drive(tasks, "go")
    steps = results[0].steps
    assert {s.stdout.strip() for s in steps} == {"x-out", "y-out"}
    assert {s.stderr.strip() for s in steps} == {"x-err", "y-err"}


def test_parallel_flush_caps_colour_bleed(capsys, monkeypatch):
    # A child ending mid-colour gets a reset appended so it can't bleed into a
    # sibling's interleaved block — but only when colour is on for the run.
    monkeypatch.delenv("NO_COLOR", raising=False)

    def tasks(reg):
        @reg.task
        def go():
            parallel(
                step(lambda: print("\033[31mred"), title="red").opts(capture=False)(),
                step(lambda: print("plain"), title="plain").opts(capture=False)(),
            )

    drive(tasks, "go", force_color=True)
    out = capsys.readouterr().out
    assert "\033[31mred" in out
    assert out.count("\033[0m") >= 2  # each child's block capped


def test_parallel_flush_no_reset_when_monochrome(capsys):
    def tasks(reg):
        @reg.task
        def go():
            parallel(
                step(lambda: print("plain-a"), title="a").opts(capture=False)(),
                step(lambda: print("plain-b"), title="b").opts(capture=False)(),
            )

    drive(tasks, "go")  # auto, no tty -> byte-clean, no injected reset
    assert "\033" not in capsys.readouterr().out


def test_run_callable_capture_false_is_live_not_buffered(capsys):
    # F60: capture=False streams the callable's output live instead of buffering
    # it into the step — serve-style tasks must not buffer unboundedly.
    def tasks(reg):
        @reg.task
        def serve():
            def tool():
                print("live-line")
                return 0

            step(tool).opts(capture=False)()()

    _, _, results = drive(tasks, "serve")
    assert "live-line" in capsys.readouterr().out  # went live to stdout
    assert results[0].steps[0].output == ""  # nothing captured into the step


def test_run_callable_foreign_cwd_is_a_taught_error(tmp_path):
    # The old F17 contract (chdir the process around the call) is gone:
    # footman never chdirs in a parallel task. A foreign target teaches the
    # exits instead of silently serialising the run.
    seen = {}

    # run(callable) retired; the guard lives on under the tools bridge's
    # in-process lane, so it is pinned at the machinery it protects.
    from livery.footman.context import _run_callable

    def tasks(reg):
        @reg.task
        def go():
            def tool():
                seen["ran"] = True
                return 0

            _run_callable(tool, (), cwd=tmp_path)

    _, _, results = drive(tasks, "go")
    assert not results[0].ok
    assert "no longer chdirs" in str(results[0].error)
    assert "ran" not in seen  # refused before the callable ran


def test_run_callable_matching_cwd_runs(tmp_path):
    # Equal target and live cwd: nothing to apply, the call just runs — the
    # common single-package case is untouched by the breaking change.
    seen = {}

    def tasks(reg):
        @reg.task
        def go():
            def tool():
                seen["cwd"] = os.getcwd()
                return 0

            from livery.footman.context import _run_callable

            _run_callable(tool, (), cwd=Path.cwd())

    drive(tasks, "go")
    assert seen["cwd"] == os.getcwd()


def test_run_callable_unmanaged_skips_the_check(tmp_path):
    # cwd='unmanaged' is the "insensitive at my own risk" declaration: the
    # resolved ctx.cwd is ignored for in-process calls, no error, no chdir.
    seen: dict[str, bool] = {}
    with use_context(Context(cwd=tmp_path, cwd_unmanaged=True)):
        step(lambda: seen.setdefault("ran", True) and 0, title="probe")()()
    assert seen["ran"] is True


def test_footman_cwd_is_concrete(tmp_path):
    import livery.footman as footman

    with use_context(Context(cwd=tmp_path)):
        assert footman.cwd() == tmp_path
    assert footman.cwd() == Path.cwd()  # outside a run: the process cwd


def test_run_callable_honors_the_env_it_was_given(monkeypatch):
    # F17: a callable sees the call's environment — and `env=` *is* that
    # environment, as subprocess means it, so spread to add.
    monkeypatch.setenv("BASE", "base")
    seen = {}

    def tasks(reg):
        @reg.task
        def go():
            def tool():
                seen["env"] = (os.environ.get("BASE"), os.environ.get("EXTRA"))
                return 0

            step(tool).opts(env={**os.environ, "EXTRA": "extra"})()()

    drive(tasks, "go")
    assert seen["env"] == ("base", "extra")


def test_run_callable_restores_env(monkeypatch):
    # The env patch is undone on exit — no leak into the next task. (cwd no
    # longer has a patch to restore: footman never chdirs in parallel.)
    monkeypatch.delenv("EXTRA", raising=False)

    def tasks(reg):
        @reg.task
        def go():
            def tool():
                return 0

            run(tool, env={"EXTRA": "x"})

    drive(tasks, "go")
    assert "EXTRA" not in os.environ


# --- output routing ----------------------------------------------------------


def test_in_process_stderr_is_captured():
    def tasks(reg):
        @reg.task
        def build():
            def tool():
                print("to stdout")
                print("to stderr", file=sys.stderr)
                return 0

            step(tool)()()

    _, _, results = drive(tasks, "build")
    record = results[0].steps[0]
    assert "to stdout" in record.output
    assert "to stderr" in record.output  # stderr merges into the capture


def test_routing_is_reentrant():
    import livery.footman.context as ctxmod

    with ctxmod.routing():
        outer = ctxmod._router
        assert outer is not None
        with ctxmod.routing():
            assert ctxmod._router is not None and ctxmod._router is not outer
        assert ctxmod._router is outer  # nested exit restores, not clears
        assert sys.stdout is outer
    assert ctxmod._router is None


def test_non_ascii_status_survives_cp1252_stdout(monkeypatch):
    wrapper = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", wrapper)

    def tasks(reg):
        @reg.task
        def build():
            run(_echo("hi"))  # run() writes the "→" glyph, absent from cp1252

    _, _, results = drive(tasks, "build")
    assert results[0].ok  # reconfigure(errors='replace') -> no UnicodeEncodeError


def test_subprocess_output_decoded_as_utf8():
    src = "import sys; sys.stdout.buffer.write('résumé ✓\\n'.encode('utf-8'))"

    def tasks(reg):
        @reg.task
        def build():
            run([sys.executable, "-c", src])

    _, _, results = drive(tasks, "build")
    assert "résumé ✓" in results[0].steps[0].output


def test_subprocess_encoding_override():
    src = "import sys; sys.stdout.buffer.write(b'caf\\xe9\\n')"  # latin-1 é

    def tasks(reg):
        @reg.task
        def build():
            run([sys.executable, "-c", src], encoding="latin-1")

    _, _, results = drive(tasks, "build")
    assert "café" in results[0].steps[0].output


def test_dry_run_prints_not_executes(capsys):
    def tasks(reg):
        @reg.task
        def build():
            run("echo SHOULD-NOT-RUN")

    _, _, results = drive(tasks, "build", dry_run=True)
    assert "$ echo SHOULD-NOT-RUN" in capsys.readouterr().out
    # Not executed, but recorded — dry-run steps are the testing surface.
    assert [s.command for s in results[0].steps] == ["echo SHOULD-NOT-RUN"]
    assert results[0].steps[0].code == 0


def test_passthrough_accessor():
    seen = {}

    def tasks(reg):
        @reg.task
        def test():
            seen["pt"] = passthrough()

    drive(tasks, "test -- -k foo -x")
    assert seen["pt"] == ["-k", "foo", "-x"]


# --- env / cwd propagation (subprocess) --------------------------------------
# F40: the env merge and cwd threading are load-bearing but were completely
# unasserted — dropping `ctx.env` entirely left the suite green. Observe them
# through a real subprocess.

_PRINT_CWD = "import os; print(os.getcwd())"
_PRINT_PREC = "import os; print(os.environ['PREC'])"


def test_subprocess_ctx_env_beats_os_environ(monkeypatch):
    monkeypatch.setenv("PREC", "from-os")

    def tasks(reg):
        @reg.task
        def build():
            run([sys.executable, "-c", _PRINT_PREC])

    _, _, results = drive(tasks, "build", env={"PREC": "from-ctx"})
    assert results[0].steps[0].output.strip() == "from-ctx"


def test_subprocess_call_env_beats_ctx_env(monkeypatch):
    monkeypatch.setenv("PREC", "from-os")

    def tasks(reg):
        @reg.task
        def build():
            run([sys.executable, "-c", _PRINT_PREC], env={"PREC": "from-kwarg"})

    # kwarg > ctx.env > os.environ, top to bottom.
    _, _, results = drive(tasks, "build", env={"PREC": "from-ctx"})
    assert results[0].steps[0].output.strip() == "from-kwarg"


def test_subprocess_cwd_via_kwarg(tmp_path):
    def tasks(reg):
        @reg.task
        def build():
            run([sys.executable, "-c", _PRINT_CWD], cwd=tmp_path)

    _, _, results = drive(tasks, "build")
    assert results[0].steps[0].output.strip() == str(tmp_path.resolve())


def test_subprocess_cwd_via_ctx(tmp_path):
    def tasks(reg):
        @reg.task
        def build():
            run([sys.executable, "-c", _PRINT_CWD])

    _, _, results = drive(tasks, "build", cwd=tmp_path)
    assert results[0].steps[0].output.strip() == str(tmp_path.resolve())


# --- opt-in ctx injection ----------------------------------------------------


def test_ctx_injected_and_not_a_cli_param():
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def deploy(ctx: Context, target: str = "prod"):
            seen["ctx"] = ctx
            seen["target"] = target

    _, tree, _ = drive(tasks, "deploy --target=staging")
    assert [p["name"] for p in tree["tasks"]["deploy"]["params"]] == ["target"]
    assert isinstance(seen["ctx"], Context)
    assert seen["target"] == "staging"


def test_ctx_by_bare_name():
    seen = {}

    def tasks(reg):
        @reg.task
        def go(ctx):
            seen["ctx"] = ctx

    _, tree, _ = drive(tasks, "go")
    assert tree["tasks"]["go"]["params"] == []  # ctx skipped entirely
    assert isinstance(seen["ctx"], Context)


# --- tools -------------------------------------------------------------------


def test_run_string_command():
    # A command as a single string is `run(...)` (footman splits and runs it,
    # no shell) — there is no `tools.sh`.
    def tasks(reg):
        @reg.task
        def go():
            run(_echo("tool-ran"))

    _, _, results = drive(tasks, "go")
    assert results[0].steps[0].output.strip() == "tool-ran"


def test_run_string_with_shell_operator_is_taught():
    # A pipe in a run(str) would become a literal argument (run() uses no shell),
    # so the pipeline would silently not happen — footman refuses with guidance.
    def tasks(reg):
        @reg.task
        def deploy():
            run("tar cf - . | ssh host tar xf -")

    _, _, results = drive(tasks, "deploy")
    assert results[0].ok is False
    assert "shell operator" in str(results[0].error)
    assert "shell=True" in str(results[0].error)  # points at the explicit shell


def test_shell_operator_detection_is_precise():
    from livery.footman.context import _shell_operator

    assert _shell_operator("tar cf - . | ssh host") == "|"
    assert _shell_operator("build && test") == "&&"
    assert _shell_operator("cmd > out.txt") == ">"
    assert _shell_operator("cat < in.txt") == "<"
    # Not shell operations: a glued token, an operator inside quotes, an arrow.
    assert _shell_operator("grep a>b file") is None
    assert _shell_operator("echo 'a | b'") is None
    assert _shell_operator("run --from a->b") is None
    assert _shell_operator("ruff check src --fix") is None
    # A split failure (unbalanced quote) defers to the exec path.
    assert _shell_operator('echo "unbalanced') is None


def test_run_list_form_allows_a_literal_operator():
    # The list form bypasses detection: '|' is a literal argument, not a pipe.
    def tasks(reg):
        @reg.task
        def go():
            run([sys.executable, "-c", "print('ok')", "|", "ignored"])

    _, _, results = drive(tasks, "go")
    assert results[0].ok is True  # no ValueError; python -c ran, extra args ignored
    assert results[0].steps[0].output.strip() == "ok"


@pytest.mark.parametrize(
    "make, expected",
    [
        (lambda: tools.ruff("check", "src", fix=True), "ruff check src --fix"),
        (lambda: tools.ruff_format("src", check=True), "ruff format src --check"),
        (lambda: tools.basedpyright("src"), "basedpyright src"),
        (lambda: tools.uv("build"), "uv build"),
        (lambda: tools.pytest("-x", in_process=False), "pytest -x"),
        (lambda: tools.pytest("-x"), "pytest -x"),  # in-process, via title
    ],
)
def test_tools_build_commands(make, expected, capsys):
    def tasks(reg):
        @reg.task
        def go():
            make()

    drive(tasks, "go", dry_run=True)
    assert expected in capsys.readouterr().out


def test_tools_python_uses_interpreter():
    # `tools.python` shows the clean name `python` but runs `sys.executable`.
    def tasks(reg):
        @reg.task
        def go():
            tools.python("-V")

    _, _, results = drive(tasks, "go", dry_run=True)
    step = results[0].steps[0]
    assert step.command == "python -V"  # the name is what's shown
    assert sys.executable in step.raw  # sys.executable is what actually runs


# --- robustness edges ---------------------------------------------------------


def test_non_utf8_subprocess_output_does_not_crash():
    def tasks(reg):
        @reg.task
        def emit():
            run(
                [
                    sys.executable,
                    "-c",
                    "import sys; sys.stdout.buffer.write(b'\\xff ok\\n')",
                ]
            )

    _, _, results = drive(tasks, "emit")
    assert results[0].ok
    assert "ok" in results[0].steps[0].output  # decoded with replacement, not a crash


def test_resolve_shell_kinds_and_strategies(monkeypatch):
    from livery.footman.context import _resolve_shell

    # no hints
    monkeypatch.setattr("livery.footman.context.os.path.isfile", lambda p: False)
    monkeypatch.setattr("shutil.which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(sys, "platform", "linux")
    assert _resolve_shell(True) == ["/usr/bin/bash", "-c"]  # posix policy → bash
    assert _resolve_shell("posix") == ["/usr/bin/bash", "-c"]
    assert _resolve_shell("zsh") == ["/usr/bin/zsh", "-c"]
    assert _resolve_shell("pwsh") == ["/usr/bin/pwsh", "-Command"]  # pwsh's flag
    assert _resolve_shell("native") == ["/bin/sh", "-c"]  # POSIX native
    with pytest.raises(ValueError, match="not a known shell"):
        _resolve_shell("nonsense")
    with pytest.raises(ValueError, match="Windows-only"):
        _resolve_shell("cmd")


def test_resolve_shell_posix_falls_back_to_sh_then_teaches(monkeypatch):
    from livery.footman.context import _resolve_shell

    monkeypatch.setattr("livery.footman.context.os.path.isfile", lambda p: False)
    # No bash, but sh exists → sh.
    monkeypatch.setattr("shutil.which", lambda n: "/bin/sh" if n == "sh" else None)
    assert _resolve_shell(True) == ["/bin/sh", "-c"]
    # Nothing at all → a taught error, never a silent wrong shell.
    monkeypatch.setattr("shutil.which", lambda n: None)
    with pytest.raises(ValueError, match="needs a POSIX shell"):
        _resolve_shell(True)


def test_resolve_shell_windows_cmd_and_native_use_comspec(monkeypatch):
    """The cmd/native-on-Windows branch, platform-independent by design —
    a POSIX runner would otherwise leave it dark everywhere but Windows,
    and the merged coverage would carry a permanently missing line."""
    from livery.footman.context import _resolve_shell

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("COMSPEC", r"C:\WINDOWS\system32\cmd.exe")
    assert _resolve_shell("cmd") == [r"C:\WINDOWS\system32\cmd.exe", "/c"]
    assert _resolve_shell("native") == [r"C:\WINDOWS\system32\cmd.exe", "/c"]


def test_resolve_shell_named_shell_missing_is_taught(monkeypatch):
    from livery.footman.context import _resolve_shell

    # no hints
    monkeypatch.setattr("livery.footman.context.os.path.isfile", lambda p: False)
    monkeypatch.setattr("shutil.which", lambda n: None)
    with pytest.raises(ValueError, match="'zsh' was not found on PATH"):
        _resolve_shell("zsh")
    with pytest.raises(ValueError, match="'pwsh' was not found on PATH"):
        _resolve_shell("pwsh")


def test_run_shell_true_actually_pipes():
    def tasks(reg):
        @reg.task
        def go():
            out = run("echo hi | tr a-z A-Z", shell=True)
            assert out.stdout.strip() == "HI"  # the pipe ran

    _, _, results = drive(tasks, "go")
    assert results[0].ok, results[0].error


def test_run_shell_true_reads_the_configured_policy(monkeypatch):
    # `[shell] default` flows into ctx.shell_default; run(shell=True) resolves it.
    monkeypatch.setattr("livery.footman.context.os.path.isfile", lambda p: False)
    monkeypatch.setattr("shutil.which", lambda n: f"/bin/{n}")
    captured = {}

    def fake(argv, *a, **k):
        captured["argv"] = argv
        return 0, "", "", False

    monkeypatch.setattr("livery.footman.context._run_subprocess", fake)
    with use_context(Context(shell_default="pwsh")):
        run("echo hi", shell=True)
    assert captured["argv"][:2] == ["/bin/pwsh", "-Command"]  # policy honoured


def test_shell_strict_and_clean_prep_per_interpreter():
    from livery.footman.context import _shell_prep

    # strict: bash/zsh get pipefail; sh degrades to errexit-only.
    assert _shell_prep("bash", "x", strict=True, clean=False) == (
        [],
        "set -eo pipefail\nx",
    )
    assert _shell_prep("sh", "x", strict=True, clean=False)[1] == "set -e\nx"
    # clean: the interpreter's no-startup-file flags.
    assert _shell_prep("bash", "x", strict=False, clean=True)[0] == [
        "--norc",
        "--noprofile",
    ]
    assert _shell_prep("pwsh", "x", strict=False, clean=True)[0] == ["-NoProfile"]
    # strict is a taught error where there is no errexit/pipefail.
    with pytest.raises(ValueError, match="errexit"):
        _shell_prep("fish", "x", strict=True, clean=False)


def test_shell_strict_stops_on_error_and_masked_pipe():
    def tasks(reg):
        @reg.task
        def go():
            # errexit: `false` stops the script before `echo after`.
            r = run("false; echo after", shell="bash", strict=True, nofail=True)
            assert r.code != 0 and "after" not in r.stdout
            # pipefail: a failing pipe stage fails the whole pipeline.
            assert run("false | true", shell="bash", strict=True, nofail=True).code != 0
            # without strict, both run to completion.
            r2 = run("false; echo after", shell="bash", nofail=True)
            assert r2.code == 0 and "after" in r2.stdout

    _, _, results = drive(tasks, "go")
    assert results[0].ok, results[0].error


def test_strict_or_clean_without_shell_is_a_taught_error():
    # strict/clean harden a shell run — silently ignoring them shell-free would
    # be a surprise, so it's a taught error.
    def tasks(reg):
        @reg.task
        def go():
            run("echo hi", strict=True)

    _, _, results = drive(tasks, "go")
    assert results[0].ok is False
    assert "only applies with a shell" in str(results[0].error)


def test_run_list_with_shell_is_a_taught_error():
    def tasks(reg):
        @reg.task
        def go():
            run(["echo", "hi"], shell=True)

    _, _, results = drive(tasks, "go")
    assert results[0].ok is False
    assert "command *string*" in str(results[0].error)


def test_shown_line_quotes_the_windows_way(monkeypatch):
    from livery.footman.context import _shell_quote

    # POSIX quoting (pin the platform — this runs on Windows CI too).
    monkeypatch.setattr(sys, "platform", "linux")
    assert _shell_quote("a b") == "'a b'"
    # On Windows, list2cmdline (not POSIX single-quotes), so `.raw`/`--verbose`
    # pastes into cmd/PowerShell; a plain token stays bare, a spaced one gets
    # double quotes, backslash paths are preserved.
    monkeypatch.setattr(sys, "platform", "win32")
    assert _shell_quote("abc") == "abc"
    assert _shell_quote("a b") == '"a b"'
    assert _shell_quote(r"C:\tools\a b") == r'"C:\tools\a b"'


def test_windows_string_commands_are_not_shlex_split(monkeypatch):
    from livery.footman import context as context_mod

    calls = {}

    # **kw, not the exact signature: this stands in for _run_subprocess to
    # inspect one argument, so it should not break every time the real one
    # grows another.
    def fake_run(argv, env, cwd, capture, *a, **kw):
        calls["argv"] = argv
        return 0, "", "", False

    monkeypatch.setattr(context_mod, "_run_subprocess", fake_run)
    monkeypatch.setattr(sys, "platform", "win32")

    def tasks(reg):
        @reg.task
        def copy():
            run(r"copy C:\tools\a.txt dest")

    drive(tasks, "copy")
    # On Windows the command line is one string (CreateProcess); shlex would
    # have eaten the backslashes.
    assert calls["argv"] == r"copy C:\tools\a.txt dest"


def test_dry_run_quiet_is_silent_capture(capsys):
    def tasks(reg):
        @reg.task
        def build():
            run("echo NOPE")

    _, _, results = drive(tasks, "build", dry_run=True, quiet=True)
    assert capsys.readouterr().out == ""
    assert [s.command for s in results[0].steps] == ["echo NOPE"]


def test_parallel_honours_the_sequential_request():
    # -s reaches inside tasks: under a sequential context, parallel() runs
    # its calls one at a time, in submission order — no overlap at all.
    import time as _time

    order: list[str] = []

    def slow():
        order.append("slow-start")
        _time.sleep(0.05)
        order.append("slow-end")

    def fast():
        order.append("fast-start")

    with use_context(Context(sequential=True)):
        assert parallel(step(slow)(), step(fast)()) == [0, 0]
    assert order == ["slow-start", "slow-end", "fast-start"]

    # And without the request, the calls genuinely overlap — proven by
    # construction, not by racing a sleep against a loaded runner: both
    # thunks must reach the barrier at once, which single-file execution
    # never can (a regression trips the timeout instead).
    import threading

    barrier = threading.Barrier(2, timeout=3)

    def hit():
        barrier.wait()

    with use_context(Context()):
        assert parallel(step(hit)(), step(hit)()) == [0, 0]

    # -j caps the pool the same way: width one behaves like sequential.
    order.clear()
    with use_context(Context(jobs=1)):
        parallel(step(slow)(), step(fast)())
    assert order == ["slow-start", "slow-end", "fast-start"]


def test_parallel_collects_systemexit():
    # `raise SystemExit(...)` / sys.exit() is a common "fail this task" idiom, but
    # SystemExit is a BaseException — it used to escape the pool and crash the whole
    # run. Now it is collected like any other failure (its code, then a synthesized
    # RunFailed the gate raises).
    def boom():
        raise SystemExit("nope")

    def fine():
        return 0

    with use_context(Context()):
        assert parallel(step(boom)(), step(fine)(), keep_going=True) == [1, 0]
        with pytest.raises(RunFailed):
            parallel(step(boom)(), step(fine)())


def test_parallel_systemexit_zero_is_success():
    # SystemExit(0) / SystemExit(None) is success — matching run()'s callable path.
    def clean():
        raise SystemExit(0)

    def bare():
        sys.exit()  # SystemExit(None)

    with use_context(Context()):
        assert parallel(step(clean)(), step(bare)()) == [0, 0]


def test_fail_raises_failed_with_reason_and_code():
    from livery.footman import Failed, fail

    with pytest.raises(Failed) as caught:
        fail("boom", code=3)
    assert caught.value.reason == "boom"
    assert caught.value.code == 3
    assert str(caught.value) == "boom"  # str is the reason (verbatim rendering)

    with pytest.raises(Failed) as bare:
        fail()
    assert bare.value.reason == "" and bare.value.code == 1


def test_pre_record_reviews_the_draft_and_the_verdict_follows():
    # The djlint shape: the tool exits 1 meaning "I reformatted", the gate
    # wants that green. The reviewer reads what was captured, sets title and
    # code; the record, the receipt, and the raise decision all read what
    # the review left — no nofail= at the call site.
    from livery.footman import Context, run, use_context

    def reformatted_is_fine(view):
        assert "changed 3 files" in view.stdout  # review sees the capture
        assert view.duration >= 0.0
        assert not view.ok  # derives from the raw code, for now
        view.title = "fmt: reformatted"
        view.code = 0

    ctx = Context()
    with use_context(ctx):
        result = run(
            [sys.executable, "-c", "print('changed 3 files'); raise SystemExit(1)"],
            pre_record=reformatted_is_fine,
        )
    assert result == 0 and result.ok  # post-review verdict, no raise
    assert result.command == "fmt: reformatted"  # the reviewed title is the label
    assert "changed 3 files" in result.stdout  # capture kept, not edited
    assert ctx.steps and ctx.steps[-1].code == 0  # the record sealed reviewed


def test_pre_record_can_fail_a_green_run():
    # The other direction: review reads the post-review code too, so a
    # reviewer may decide a zero exit was a failure by this gate's rules.
    from livery.footman import Context, RunFailed, run, use_context

    def zero_is_sus(view):
        view.code = 3

    with use_context(Context()), pytest.raises(RunFailed) as caught:
        run([sys.executable, "-c", "print('ok')"], pre_record=zero_is_sus)
    assert caught.value.result.code == 3


def test_a_raising_reviewer_fails_the_call_with_its_own_error():
    # A broken reviewer is a broken gate, not a shrug — and the record keeps
    # what the work honestly produced, because review never finished.
    from livery.footman import Context, run, use_context

    def broken(view):
        raise KeyError("oops")

    ctx = Context()
    with (
        use_context(ctx),
        pytest.raises(RuntimeError, match=r"pre_record hook 'broken'.*oops"),
    ):
        run([sys.executable, "-c", "raise SystemExit(0)"], pre_record=broken)
    assert ctx.steps and ctx.steps[-1].code == 0  # the raw record, unreviewed


def test_pre_record_is_a_note_on_an_off_the_record_call(capsys):
    # .opts() merges along a chain, so a shared tool may carry a reviewer
    # while one call site goes off the record — a note, not an error, and
    # the reviewer never fires.
    from livery.footman import Context, ResultView, run, use_context

    fired: list[ResultView] = []
    with use_context(Context()):
        result = run(
            [sys.executable, "-c", "print('sha')"],
            recorded=False,
            pre_record=fired.append,
        )
    assert result == 0 and fired == []


def test_pre_record_with_capture_off_reviews_the_code_alone():
    from livery.footman import Context, run, use_context

    seen = {}

    def reviewer(view):
        seen["stdout"] = view.stdout
        seen["code"] = view.code

    with use_context(Context()):
        run(
            [sys.executable, "-c", "print('to the terminal')"],
            capture=False,
            pre_record=reviewer,
        )
    assert seen == {"stdout": "", "code": 0}


def test_every_step_carries_its_body_audit_entry():
    from livery.footman import Context, run, use_context

    with use_context(Context()):
        result = run([sys.executable, "-c", "print('hi')"])
    assert len(result.audit) == 1
    moment, actor, code = result.audit[0]
    assert moment == "body" and code == 0 and "python" in actor.lower()
    assert result.failed_at is None and result.work_code is None


def test_the_audit_tells_the_whole_verdict_story():
    # The exhibit from the design page: raw 1, reviewed green — and the
    # derived readings answer the common questions without scanning.
    from livery.footman import Context, run, use_context

    def reformatted_is_fine(view):
        view.title = "fmt: reformatted"
        view.code = 0

    ctx = Context()
    with use_context(ctx):
        result = run(
            [sys.executable, "-c", "raise SystemExit(1)"],
            pre_record=reformatted_is_fine,
        )
    body, review = result.audit
    assert body.moment == "body" and body.code == 1
    assert review == ("review", "reformatted_is_fine", 0)
    assert result.failed_at is None  # reviewed green IS green


def test_a_green_run_failed_in_review_keeps_its_work_code():
    from livery.footman import Context, RunFailed, run, use_context

    def zero_is_sus(view):
        view.code = 3

    with use_context(Context()), pytest.raises(RunFailed) as caught:
        run([sys.executable, "-c", "print('ok')"], pre_record=zero_is_sus)
    result = caught.value.result
    assert result.failed_at == "review"  # where the failure came from
    assert result.work_code == 0  # the green the work earned, kept visible


def test_a_title_only_reviewer_is_involved_but_writes_no_verdict():
    from livery.footman import Context, run, use_context

    def rename_only(view):
        view.title = "tidy"

    with use_context(Context()):
        result = run([sys.executable, "-c", "pass"], pre_record=rename_only)
    assert result.audit[1] == ("review", "rename_only", None)
    assert result.command == "tidy"


def test_fail_refuses_code_zero_everywhere():
    # fail() means failure; code 0 is success. The pass-branch spelling was
    # rejected in the task-failure design, and the old verbatim honouring
    # produced an incoherent row (ok=True with an error attached). The
    # refusal teaches the honest spelling: return 0, or a plain return.
    from livery.footman import Failed, fail

    with pytest.raises(ValueError, match=r"fail\(\) means failure") as caught:
        fail("looks fine", code=0)
    assert not isinstance(caught.value, Failed)  # a misuse, not a failure


def test_fail_is_a_function_so_it_is_lint_clean_for_consumers(tmp_path):
    # The whole reason fail() is a function, not `raise Failed(...)`: a call trips
    # no EM101 (flake8-errmsg) or TRY003 (tryceratops) at a consumer's call site,
    # where a `raise SomeError("literal")` would. Guard the property directly.
    import shutil
    import subprocess

    ruff = shutil.which("ruff")
    if ruff is None:
        pytest.skip("ruff not on PATH")
    snippet = tmp_path / "consumer_task.py"
    snippet.write_text(
        "import footman\n\n\ndef t() -> None:\n    footman.fail('a literal reason')\n"
    )
    proc = subprocess.run(
        [ruff, "check", "--isolated", "--select", "EM,TRY", str(snippet)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_step_lines_carry_an_aligned_name_column(capsys):
    # mark · task name (padded to the widest sibling) · command · (time).
    def tasks(reg):
        @reg.task
        def go():
            run(_echo("hi"))

        @reg.task
        def longer():
            run(_echo("ho"))

    drive(tasks, "go longer", verbose=True)
    out = capsys.readouterr().out
    # Padded to len("longer"); the duration varies with the machine (a cold
    # interpreter under a loaded suite can cross 1s), so stop at its "(".
    # The two commands are the same length, so neither pads the other.
    assert f"ok   go      {_echo('hi')}  (" in out
    assert f"ok   longer  {_echo('ho')}  (" in out


def test_progress_and_track_report_to_the_status_line():
    from livery.footman import progress, track
    from livery.footman.context import Context, set_status, use_context

    class FakeStatus:
        def __init__(self):
            self.reports = []
            self.counted = {}

        def unit_counted(self, name, done, total):
            self.reports.append((name, done, total))
            self.counted[name] = (done, total)

        def unit_added(self, count=1):
            pass

        def unit_started(self, name):
            pass

        def unit_finished(self, name, ok):
            pass

        def unit_skipped(self, name):
            pass

        def notify(self, s):
            pass

        def suspend(self):
            pass

        def resume(self):
            pass

        def paint(self):
            pass

    status = FakeStatus()
    set_status(status)
    try:
        with use_context(Context(task="migrate")):
            progress(3, 10)
            assert status.reports[-1] == ("migrate", 3, 10)
            assert list(track(["a", "b"])) == ["a", "b"]
    finally:
        set_status(None)
    # track() reported each step, then cleared on the way out
    assert ("migrate", 1, 2) in status.reports
    assert ("migrate", 2, 2) in status.reports
    assert status.counted == {}


def test_progress_outside_a_run_is_a_noop():
    from livery.footman import progress, track

    progress(1, 2)  # no status line: costs nothing, raises nothing
    assert list(track([1, 2, 3])) == [1, 2, 3]


# --- interactive prompts (prompt / confirm / select) -------------------------


def test_prompt_off_a_terminal_uses_default_then_raises(monkeypatch):
    from livery.footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: False)
    # A default makes an unattended run deterministic instead of hung.
    assert context.prompt("name? ", default="Ada") == "Ada"
    # No default: fail loudly rather than block on input that never comes.
    with pytest.raises(RuntimeError, match=r"no terminal is attached"):
        context.prompt("name? ")


def test_prompt_reads_stdin_and_writes_the_prompt_to_stderr(monkeypatch):
    from livery.footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("Ada\n"))
    err = io.StringIO()
    monkeypatch.setattr(context, "real_stderr", lambda: err)
    out = io.StringIO()
    monkeypatch.setattr(context, "real_stdout", lambda: out)

    assert context.prompt("your name? ") == "Ada"
    # The prompt is commentary: it lands on stderr, never on captured stdout.
    assert err.getvalue() == "your name? "
    assert out.getvalue() == ""


def test_prompt_bypasses_the_capture_sink(monkeypatch):
    # Even when a task's stdout is captured (parallel/JSON), the prompt goes
    # to the real terminal, not into the buffer.
    from livery.footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("blue\n"))
    err = io.StringIO()
    monkeypatch.setattr(context, "real_stderr", lambda: err)

    sink = io.StringIO()
    with use_context(Context(sink=sink)):
        answer = context.prompt("colour? ")
    assert answer == "blue"
    assert sink.getvalue() == ""  # nothing leaked into the captured buffer
    assert "colour? " in err.getvalue()


def test_prompt_empty_line_falls_back_to_default(monkeypatch):
    from livery.footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n"))  # just Enter
    monkeypatch.setattr(context, "real_stderr", io.StringIO)
    assert context.prompt("branch? ", default="main") == "main"


def test_confirm_yes_no_and_default(monkeypatch):
    from livery.footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(context, "real_stderr", io.StringIO)

    def answer(text):
        monkeypatch.setattr(sys, "stdin", io.StringIO(text))
        return context.confirm("proceed?", default=False)

    assert answer("y\n") is True
    assert answer("yes\n") is True
    assert answer("n\n") is False
    assert answer("\n") is False  # Enter takes the default

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: False)
    assert context.confirm("proceed?", default=True) is True  # unattended → default


def test_interactive_primitives_are_guarded_in_a_plain_task():
    from livery.footman import context

    # Inside a non-interactive task body the prompt would be swallowed by the
    # capture buffer — so it is a loud, taught error naming both fixes. (No
    # stdin/tty mocking needed: the guard raises before any input is read.)
    with use_context(Context(task="deploy", in_task=True, interactive=False)):
        with pytest.raises(RuntimeError, match=r"@task\(interactive=True\)"):
            context.prompt("x? ")
        with pytest.raises(RuntimeError, match=r"not interactive"):
            context.confirm("x?")
        with pytest.raises(RuntimeError, match=r"not interactive"):
            context.select("x?", ["a", "b"])


def test_interactive_primitives_allowed_in_an_interactive_task(monkeypatch):
    from livery.footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("Ada\n"))
    monkeypatch.setattr(context, "real_stderr", io.StringIO)
    # A task that owns the terminal may prompt mid-body.
    with use_context(Context(task="wizard", in_task=True, interactive=True)):
        assert context.prompt("name? ") == "Ada"


def test_no_input_refuses_to_prompt(monkeypatch):
    from livery.footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)  # even on a tty
    with use_context(Context(no_input=True)):
        assert context.prompt("x? ", default="d") == "d"  # a default still works
        with pytest.raises(RuntimeError, match=r"no-input"):
            context.prompt("x? ")
        assert context.confirm("ok?", default=True) is True  # answer is the default


def test_prompt_typed_coerces_and_re_asks(monkeypatch, capfd):
    from livery.footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("abc\n7\n"))
    with use_context(Context(task="wizard", in_task=True, interactive=True)):
        assert context.prompt("n? ", type=int) == 7
    assert "expects an integer" in capfd.readouterr().err  # taught, then re-asked


def test_prompt_typed_runs_the_marker_checks(monkeypatch, capfd):
    from livery.footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("9\n3\n"))
    with use_context(Context(task="wizard", in_task=True, interactive=True)):
        got = context.prompt("n? ", type=Annotated[int, between(1, 5)])
    assert got == 3
    assert "between 1 and 5" in capfd.readouterr().err


def test_prompt_typed_literal_is_a_choice(monkeypatch, capfd):
    from livery.footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("dev\nprod\n"))
    with use_context(Context(task="wizard", in_task=True, interactive=True)):
        got = context.prompt("env? ", type=Literal["staging", "prod"])
    assert got == "prod"
    assert "must be one of staging|prod" in capfd.readouterr().err


def test_prompt_typed_empty_and_unattended_take_the_default(monkeypatch):
    from livery.footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n"))
    with use_context(Context(task="wizard", in_task=True, interactive=True)):
        assert context.prompt("port? ", type=int, default=8080) == 8080
    monkeypatch.setattr(context, "_stdin_is_tty", lambda: False)  # no terminal
    with use_context(Context(task="wizard", in_task=True, interactive=True)):
        assert context.prompt("port? ", type=int, default=8080) == 8080


def test_prompt_typed_refusals_are_loud_even_unattended():
    from livery.footman import context

    # Programming errors refuse by name regardless of attendance — a default
    # must not paper over a wrong type= in CI.
    with use_context(Context(no_input=True)):
        with pytest.raises(ValueError, match="secret answer is text"):
            context.prompt("t? ", type=int, secret=True, default=1)
        with pytest.raises(ValueError, match="takes one value"):
            context.prompt("xs? ", type=list[str], default="d")
        with pytest.raises(ValueError, match=r"ask\(\) decides how a parameter"):
            context.prompt("x? ", type=Annotated[str, ask()], default="d")


def test_assume_yes_auto_confirms():
    from livery.footman import context

    # --yes answers every confirm without reading stdin (none is provided).
    with use_context(Context(assume_yes=True)):
        assert context.confirm("ship it?", default=False) is True


def test_select_single_multiple_and_pairs(monkeypatch):
    from livery.footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(context, "real_stderr", io.StringIO)

    def pick(line, **kw):
        monkeypatch.setattr(sys, "stdin", io.StringIO(line))
        return context.select("pick", ["core", "cli", "docs"], **kw)

    assert pick("2\n") == "cli"  # single-select, 1-indexed
    assert pick("1,3\n", multiple=True) == ["core", "docs"]
    assert pick("all\n", multiple=True) == ["core", "cli", "docs"]
    assert pick("none\n", multiple=True) == []
    # (label, value) pairs show the label and return the value:
    monkeypatch.setattr(sys, "stdin", io.StringIO("1\n"))
    assert context.select("p", [("Core pkg", "core"), ("CLI", "cli")]) == "core"


def test_select_rejects_bad_input_and_degrades(monkeypatch):
    from livery.footman import context

    monkeypatch.setattr(context, "real_stderr", io.StringIO)
    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)

    monkeypatch.setattr(sys, "stdin", io.StringIO("x\n"))
    with pytest.raises(RuntimeError, match=r"not a number"):
        context.select("p", ["a", "b"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("9\n"))
    with pytest.raises(RuntimeError, match=r"out of range"):
        context.select("p", ["a", "b"])

    # Off a terminal: default, or a loud error.
    monkeypatch.setattr(context, "_stdin_is_tty", lambda: False)
    assert context.select("p", ["a", "b"], default="a") == "a"
    with pytest.raises(RuntimeError, match=r"no terminal|no-input"):
        context.select("p", ["a", "b"])


def test_prompt_guard_fires_in_a_real_run():
    from livery.footman import context

    def build(reg):
        @reg.task
        def asks():
            context.prompt("name? ")  # illegal: not an interactive task

    _, _, results = drive(build, "asks")
    assert not results[0].ok
    assert "interactive" in str(results[0].error)


def test_interactive_task_may_prompt(monkeypatch):
    from livery.footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("Ada\n"))
    monkeypatch.setattr(context, "real_stderr", io.StringIO)

    captured = {}

    def build(reg):
        @reg.task(interactive=True)
        def wizard():
            captured["name"] = context.prompt("name? ")

    _, _, results = drive(build, "wizard")
    assert results[0].ok
    assert captured["name"] == "Ada"


# --- ask(): typed parameters that prompt -------------------------------------


def test_ask_prompts_a_required_param(monkeypatch):
    from livery.footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("1.2.3\n"))
    monkeypatch.setattr(context, "real_stderr", io.StringIO)

    got = {}

    def build(reg):
        @reg.task
        def release(version: Annotated[str, ask()]):
            got["v"] = version

    _, _, results = drive(build, "release")
    assert results[0].ok
    assert got["v"] == "1.2.3"


def test_ask_cli_value_wins_over_the_prompt(monkeypatch):
    from livery.footman import context

    # A value on the line means no prompt — the (wrong) stdin is never read.
    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("WRONG\n"))
    monkeypatch.setattr(context, "real_stderr", io.StringIO)

    got = {}

    def build(reg):
        @reg.task
        def release(version: Annotated[str, ask()]):
            got["v"] = version

    _, _, results = drive(build, "release --version=9.9.9")
    assert results[0].ok
    assert got["v"] == "9.9.9"


def test_ask_offers_the_default_and_enter_accepts_it(monkeypatch):
    from livery.footman import context

    # A default no longer silences the question — it becomes the offer, so
    # `ask()` is usable on any parameter rather than defaultless ones only.
    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n"))  # Enter
    monkeypatch.setattr(context, "real_stderr", io.StringIO)

    got = {}

    def build(reg):
        @reg.task
        def release(version: Annotated[str, ask()] = "patch"):
            got["v"] = version

    _, _, results = drive(build, "release")
    assert results[0].ok
    assert got["v"] == "patch"


def test_ask_takes_the_answer_over_the_default(monkeypatch):
    from livery.footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("minor\n"))
    monkeypatch.setattr(context, "real_stderr", io.StringIO)

    got = {}

    def build(reg):
        @reg.task
        def release(version: Annotated[str, ask()] = "patch"):
            got["v"] = version

    _, _, results = drive(build, "release")
    assert results[0].ok
    assert got["v"] == "minor"


def test_ask_with_a_default_falls_back_where_nobody_can_be_asked(monkeypatch):
    from livery.footman import context

    # No terminal, but there *is* another answer, so this is not a refusal:
    # a person gets asked, an unattended run gets the default.
    monkeypatch.setattr(context, "_stdin_is_tty", lambda: False)

    got = {}

    def build(reg):
        @reg.task
        def release(version: Annotated[str, ask()] = "patch"):
            got["v"] = version

    _, _, results = drive(build, "release")
    assert results[0].ok
    assert got["v"] == "patch"


def test_ask_re_asks_on_a_bad_value(monkeypatch):
    from livery.footman import context

    # A typed param re-asks until the answer coerces — "abc" then "5".
    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("abc\n5\n"))
    monkeypatch.setattr(context, "real_stderr", io.StringIO)

    got = {}

    def build(reg):
        @reg.task
        def scale(replicas: Annotated[int, ask()]):
            got["n"] = replicas

    _, _, results = drive(build, "scale")
    assert results[0].ok
    assert got["n"] == 5


def test_ask_validates_a_literal_choice(monkeypatch):
    from livery.footman import context

    # A Literal is a typed choice: "dev" is rejected, "prod" accepted.
    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("dev\nprod\n"))
    monkeypatch.setattr(context, "real_stderr", io.StringIO)

    got = {}

    def build(reg):
        @reg.task
        def deploy(env: Annotated[Literal["staging", "prod"], ask()]):
            got["e"] = env

    _, _, results = drive(build, "deploy")
    assert results[0].ok
    assert got["e"] == "prod"


def test_ask_off_a_terminal_fails_loudly(monkeypatch):
    from livery.footman import context
    from livery.footman._split import ChainError

    # No tty, no default: the required value can't be prompted. Since asks
    # front-load, the whole run refuses before anything starts — naming the
    # flag — rather than hanging or failing one task mid-run.
    monkeypatch.setattr(context, "_stdin_is_tty", lambda: False)

    def build(reg):
        @reg.task
        def release(version: Annotated[str, ask()]): ...

    with pytest.raises(ChainError, match="--version is required"):
        drive(build, "release")


# --- @task(confirm=) gate -----------------------------------------------------


def test_confirm_gate_runs_when_confirmed(monkeypatch):
    from livery.footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("y\n"))
    monkeypatch.setattr(context, "real_stderr", io.StringIO)

    ran = {}

    def build(reg):
        @reg.task(confirm="deploy to prod?")
        def deploy():
            ran["it"] = True

    _, _, results = drive(build, "deploy")
    assert results[0].ok
    assert ran.get("it")


def test_confirm_gate_denied_skips_the_task(monkeypatch):
    from livery.footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("n\n"))
    monkeypatch.setattr(context, "real_stderr", io.StringIO)

    ran = {}

    def build(reg):
        @reg.task(confirm="deploy to prod?")
        def deploy():
            ran["it"] = True

    _, _, results = drive(build, "deploy")
    assert not results[0].ok
    assert "not confirmed" in str(results[0].error)
    assert not ran.get("it")  # the body never ran


def test_confirm_gate_yes_bypasses():
    ran = {}

    def build(reg):
        @reg.task(confirm="sure?")
        def deploy():
            ran["it"] = True

    _, _, results = drive(build, "deploy", assume_yes=True)  # --yes
    assert results[0].ok
    assert ran.get("it")


def test_confirm_gate_under_dry_run_assumes_yes(monkeypatch, capsys):
    # A rehearsal answers every gate yes — a gate answered no would hide
    # the very work the rehearsal exists to show — and notes it did.
    from livery.footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: False)

    ran = {}

    def build(reg):
        @reg.task(confirm="deploy to prod?")
        def deploy():
            ran["it"] = True

    _, _, results = drive(build, "deploy", dry_run=True)
    assert results[0].ok
    assert ran.get("it")  # the body rehearsed
    assert "assumed yes" in capsys.readouterr().err


def test_prompt_layer_is_unattended_under_dry_run():
    # A rehearsal is unattended by nature: defaults answer, and a prompt
    # with no default fails loudly instead of hanging on input.
    import pytest as _pytest

    from livery.footman import confirm as fm_confirm
    from livery.footman import prompt as fm_prompt
    from livery.footman import select as fm_select
    from livery.footman.context import Context, use_context

    ctx = Context(dry_run=True, interactive=True, in_task=True)
    with use_context(ctx):
        assert fm_prompt("tag?", default="v1") == "v1"
        assert fm_confirm("push?", default=True) is True
        assert fm_select("which?", ["a", "b"], default="b") == "b"
        with _pytest.raises(RuntimeError, match="dry-run is unattended"):
            fm_prompt("tag?")


def test_confirm_gate_off_a_terminal_denies(monkeypatch):
    from livery.footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: False)

    ran = {}

    def build(reg):
        @reg.task(confirm="sure?")
        def deploy():
            ran["it"] = True

    _, _, results = drive(build, "deploy")  # no --yes, no terminal → denied
    assert not results[0].ok
    assert not ran.get("it")


def test_select_scrubs_control_characters_in_labels(monkeypatch):
    from livery.footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("1\n"))
    err = io.StringIO()
    monkeypatch.setattr(context, "real_stderr", lambda: err)

    # A label carrying an ANSI escape is neutralised before it reaches the tty.
    context.select("pick", ["\x1b[31mred\x1b[0m", "green"])
    assert "\x1b" not in err.getvalue()
    assert "red" in err.getvalue()  # the visible text survives


_TRACE: list[str] = []


def _traced_options():
    _TRACE.append("ask")
    return ["pick"]


def _live_options():  # module-level: `from __future__ import annotations` makes
    return ["pick"]  # annotations strings, so names must resolve at module scope


def test_ask_front_loads_before_any_body_runs(monkeypatch):
    from livery.footman import _schedule, context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    order = []

    def fake_prompt(text, **kw):
        order.append("ask")
        return "42"

    monkeypatch.setattr(context, "_prompt_core", fake_prompt)
    reg = Group("root")

    @reg.task
    def alpha(a: Annotated[int, ask()]):
        order.append("body")

    @reg.task
    def beta(b: Annotated[int, ask()]):
        order.append("body")

    tree = _manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, ["alpha", "beta"])
    results = _schedule.run_plan(reg, segments)
    assert all(r.ok for r in results), [str(r.error) for r in results]
    assert order[:2] == ["ask", "ask"]  # every question first, then the work
    assert order[2:] == ["body", "body"]


def test_ask_with_live_suggest_resolves_after_its_prereqs(monkeypatch):
    from livery.footman import _schedule, context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("1\n"))  # pick from the menu
    monkeypatch.setattr(context, "real_stderr", io.StringIO)
    reg = Group("root")

    @reg.task
    def dep():
        _TRACE.append("dep")

    @reg.task(pre=[dep])
    def choose(which: Annotated[str, ask(), suggest(_traced_options)]):
        _TRACE.append("body")

    tree = _manifest.build_manifest(reg)["tree"]
    _TRACE.clear()  # the manifest build bakes choices (one completer run)
    _, segments = split_chain(tree, ["choose"])
    results = _schedule.run_plan(reg, segments)
    assert all(r.ok for r in results), [str(r.error) for r in results]
    # The completer runs at ask time — which must be after the dep, before
    # the body: a live-suggest question may need the dep's effects.
    assert _TRACE.index("dep") < _TRACE.index("ask") < _TRACE.index("body")


def test_ask_refuses_up_front_without_a_terminal(monkeypatch):
    from livery.footman import _schedule, context
    from livery.footman._split import ChainError

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: False)
    ran = []
    reg = Group("root")

    @reg.task
    def noop():
        ran.append("noop")

    @reg.task
    def release(version: Annotated[str, ask()]):
        ran.append("body")

    tree = _manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, ["noop", "release"])
    with pytest.raises(ChainError, match="--version"):
        _schedule.run_plan(reg, segments)
    assert ran == []  # refused before anything started, noop included


def test_ask_with_live_suggest_under_no_input_fails_that_task_loudly(monkeypatch):
    # The late corner: a live-suggest question resolves at node launch (its
    # menu may need a dep's output), so --no-input can't refuse it up front —
    # the task fails loudly at launch instead, and can never hang. A sibling
    # is untouched.
    from livery.footman import _schedule, context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    ran = []
    reg = Group("root")

    @reg.task
    def sibling():
        ran.append("sibling")

    @reg.task
    def choose(which: Annotated[str, ask(), suggest(_live_options)]):
        ran.append("body")

    tree = _manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, ["sibling", "choose"])
    results = {
        r.task: r
        for r in _schedule.run_plan(reg, segments, ctx_config={"no_input": True})
    }
    assert not results["choose"].ok
    assert "--which is required" in str(results["choose"].error)
    assert "body" not in ran


def _menu_opts():
    return ["alpha", "beta", "gamma"]


def test_ask_with_strict_suggest_is_a_menu(monkeypatch):
    from livery.footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("2\n"))
    monkeypatch.setattr(context, "real_stderr", io.StringIO)
    got = {}

    def build(reg):
        @reg.task
        def deploy(target: Annotated[str, ask(), suggest(_menu_opts)]):
            got["t"] = target

    _, _, results = drive(build, "deploy")
    assert results[0].ok, results[0].error
    assert got["t"] == "beta"  # picked by number, not typed


def test_ask_with_strict_suggest_multi_select(monkeypatch):
    from livery.footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("1,3\n"))
    monkeypatch.setattr(context, "real_stderr", io.StringIO)
    got = {}

    def build(reg):
        @reg.task
        def deploy(targets: Annotated[Many[str], ask(), suggest(_menu_opts)]):
            got["t"] = targets

    _, _, results = drive(build, "deploy")
    assert results[0].ok, results[0].error
    assert got["t"] == ["alpha", "gamma"]


def test_ask_menu_re_asks_on_a_bad_number(monkeypatch):
    from livery.footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("9\n1\n"))
    monkeypatch.setattr(context, "real_stderr", io.StringIO)
    got = {}

    def build(reg):
        @reg.task
        def deploy(target: Annotated[str, ask(), suggest(_menu_opts)]):
            got["t"] = target

    _, _, results = drive(build, "deploy")
    assert results[0].ok, results[0].error
    assert got["t"] == "alpha"  # out-of-range taught, then the retry took 1


def test_ask_with_best_effort_suggest_stays_free_text(monkeypatch, capfd):
    from livery.footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("custom\n"))
    got = {}

    def build(reg):
        @reg.task
        def deploy(
            target: Annotated[str, ask(), suggest(_menu_opts, strict=False)],
        ):
            got["t"] = target

    _, _, results = drive(build, "deploy")
    assert results[0].ok, results[0].error
    assert got["t"] == "custom"  # suggestions hint, never bind
    assert "alpha" in capfd.readouterr().err  # and they were shown


def test_secret_answers_arrive_redacting(monkeypatch):
    from livery.footman import _executor, context
    from livery.footman._coerce import peel

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(context, "_prompt_core", lambda *a, **k: "hunter2")
    peeled = peel(Annotated[str, ask(secret=True)])
    raw, value = _executor._prompt_param("token", peeled, None)
    assert isinstance(raw, Secret) and isinstance(value, Secret)
    assert repr(value) == "Secret('***')"  # what logs and tracebacks see
    assert str(value) == "hunter2"  # what the body uses


def test_redact_walks_containers():
    from livery.footman._describe import redact

    tangled = ["a", Secret("s"), {"k": Secret("t"), "n": 1}, (Secret("u"),)]
    assert redact(tangled) == ["a", "***", {"k": "***", "n": 1}, ("***",)]


def test_secret_params_publish_no_values():
    reg = Group("root")

    @reg.task
    def login(token: Annotated[str, ask(secret=True), suggest(_menu_opts)]):
        pass

    spec = _manifest.build_manifest(reg)["tree"]["tasks"]["login"]["params"][0]
    assert spec["secret"] is True
    assert "choices" not in spec  # the completer never ran, nothing baked


def test_prompt_eof_is_a_taught_error_not_a_spin(monkeypatch):
    from livery.footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))  # closed pipe
    monkeypatch.setattr(context, "real_stderr", io.StringIO)
    with pytest.raises(RuntimeError, match="stdin closed"):
        context._prompt_core("name? ")


def test_prompt_echo_scrubs_reflected_input(monkeypatch, capfd):
    from livery.footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("\x1b[31mbad\n5\n"))
    got = {}

    def build(reg):
        @reg.task
        def scale(replicas: Annotated[int, ask()]):
            got["n"] = replicas

    _, _, results = drive(build, "scale")
    assert results[0].ok, results[0].error
    assert got["n"] == 5
    assert "\x1b" not in capfd.readouterr().err  # the bad input echoed scrubbed


def test_prompt_secret_answers_redact_like_ask_does(monkeypatch):
    """Hiding a value while it is typed and then printing it in the first
    traceback would be a strange kind of secret — the mid-task question and
    the declared parameter answer the same way."""
    from livery.footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(context, "_prompt_core", lambda *a, **k: "hunter2")

    answer = context.prompt("token? ", secret=True)
    assert isinstance(answer, Secret)
    assert repr(answer) == "Secret('***')"
    assert str(answer) == "hunter2"  # the body still gets the real thing

    plain = context.prompt("name? ")
    assert not isinstance(plain, Secret)  # only a secret question redacts


def test_prompt_secret_wraps_an_unattended_default(monkeypatch):
    # Where the value came from doesn't change what it is.
    from livery.footman import context

    monkeypatch.setattr(context, "_stdin_is_tty", lambda: False)
    answer = context.prompt("token? ", default="fallback", secret=True)
    assert isinstance(answer, Secret) and str(answer) == "fallback"


def test_reveal_is_the_unwrap_said_out_loud():
    """A `Secret` that reaches a structured surface redacts; `reveal()` is how
    a task that *means* to emit one (an `export …` line for `eval`) says so —
    greppable, unlike a run-wide switch."""
    from livery.footman._describe import redact

    token = Secret("hunter2")
    assert redact({"token": token}) == {"token": "***"}
    assert redact({"token": token.reveal()}) == {"token": "hunter2"}
    assert type(token.reveal()) is str  # a plain str, nothing left to redact

    # Deliberate bytes were never redacted: every string operation on a
    # Secret already yields a plain str, which is what makes the filter case
    # work without a flag to disarm.
    assert f"export TOKEN={token}" == "export TOKEN=hunter2"


# --- a Secret handed straight to run(): shown redacted, recorded whole ---------


def _login(token: str) -> list[str]:
    """A portable command that takes the token as its own argv element —
    the case where footman does the joining and so owns the display."""
    return [sys.executable, "-c", "pass", token]


def test_a_secret_argument_never_reaches_a_shown_command_line(capsys):
    """The caller never unwrapped it, so footman must not print it: the
    announce line and the step receipt both name the call `***`."""
    token = Secret("hunter2")

    def tasks(reg):
        @reg.task
        def login():
            run(_login(token))

    _, _, results = drive(tasks, "login", verbose=True)
    assert results[0].ok, results[0].error
    out = capsys.readouterr().out
    assert "hunter2" not in out
    assert out.count(f"{sys.executable} -c pass ***") == 2  # announce, receipt


def test_the_exact_spelling_redacts_like_the_readable_one():
    """`--verbose` shows a bridged call in its exact, paste-able form, and the
    bridge keeps a `Secret` whole in that argv on purpose — the child needs the
    real value. So `exact` is the one rendering path where the marker is still
    present to act on, and it went out unredacted: a toolroom call under `-v`
    printed the token while its own receipt, built from `parts`, said `***`.
    Asking for the paste-able spelling is not asking to be shown a secret."""
    inv = Invocation(
        parts=(("prog", "git"), ("opt", "--author"), ("value", "***")),
        exact=("git", Secret("hunter2")),
    )
    assert "hunter2" not in inv.text(exact=True)
    assert "hunter2" not in inv.painted(color=False, exact=True)
    assert "hunter2" not in inv.painted(color=True, exact=True)
    assert inv.exact[1] == "hunter2"  # the record still carries it


def test_the_record_keeps_the_secret_the_display_hid(capsys):
    """Redaction is display policy over a committed record, not a rewrite of
    it: what `recording()`, a dependent, and the caller read is the value
    that was passed."""
    token = Secret("hunter2")

    def tasks(reg):
        @reg.task
        def login():
            run(_login(token))

    _, _, results = drive(tasks, "login")
    step = results[0].steps[0]
    assert step.command == f"{sys.executable} -c pass hunter2"
    assert step.raw.endswith("hunter2")
    assert step.to_argv()[-1] == "hunter2"
    assert step.shown == f"{sys.executable} -c pass ***"
    # The name footman mints for the record is printed wherever the record
    # goes, so it is minted from the shown line.
    assert "hunter2" not in step.address
    assert "hunter2" not in step.audit[0].actor


def test_a_secret_argument_is_out_of_the_failure_message(capsys):
    """The place a failed command line most reliably lands is somebody's CI
    log. The exception still carries the record, for a handler that reads
    rather than prints."""
    token = Secret("hunter2")

    def tasks(reg):
        @reg.task
        def login():
            run([sys.executable, "-c", "raise SystemExit(1)", token])

    _, _, results = drive(tasks, "login")
    err = results[0].error
    assert isinstance(err, RunFailed)
    assert "hunter2" not in str(err)
    assert "***` exited with code 1" in str(err)
    assert err.result.command.endswith("hunter2")  # the record, untouched
    assert "hunter2" not in capsys.readouterr().out  # nor the FAIL receipt


def test_a_timed_out_secret_command_redacts_too():
    from livery.footman.context import RunTimeout

    token = Secret("hunter2")

    def tasks(reg):
        @reg.task
        def login():
            run(
                [sys.executable, "-c", "import time; time.sleep(30)", token],
                timeout=0.1,
            )

    _, _, results = drive(tasks, "login")
    err = results[0].error
    assert isinstance(err, RunTimeout)
    assert "hunter2" not in str(err) and "***" in str(err)
    assert err.result.command.endswith("hunter2")


def test_an_interpolated_secret_still_prints_in_the_clear(capsys):
    """A `str` operation on a `Secret` yields a plain `str`, deliberately —
    which is what makes a task that must emit one work without a switch to
    disarm. There is nothing left for the display to recognise, and that is
    the documented answer, not an oversight."""
    token = Secret("hunter2")

    def tasks(reg):
        @reg.task
        def login():
            run(f'"{sys.executable}" -c "pass" {token}')
            run([sys.executable, "-c", "pass", token.reveal()])

    _, _, results = drive(tasks, "login", verbose=True)
    assert results[0].ok, results[0].error
    assert all("hunter2" in s.shown for s in results[0].steps)
    assert "hunter2" in capsys.readouterr().out


def test_a_secret_title_redacts_like_the_command_would(capsys):
    """`title=` renames the record, and is shown in the command's place — so
    it answers to the same rule. A title built *from* a secret is a plain
    `str` by then, and prints, exactly as an interpolated command does."""

    def plain_title(reg):
        @reg.task
        def login():
            run(_login("x"), title=f"login {Secret('hunter2').reveal()}")

    _, _, results = drive(plain_title, "login", verbose=True)
    step = results[0].steps[0]
    assert step.command == "login hunter2"  # the record keeps the title given
    assert step.shown == "login hunter2"  # a str title is a str, not a Secret
    assert "hunter2" in capsys.readouterr().out

    def secret_title(reg):
        @reg.task
        def login():
            run(_login("x"), title=Secret("login hunter2"))

    _, _, results = drive(secret_title, "login", verbose=True)
    step = results[0].steps[0]
    assert step.shown == "***" and str(step.command) == "login hunter2"
    assert "hunter2" not in capsys.readouterr().out


# --- recorded=False: a call that is not part of the task's story ------------------


def test_a_non_step_call_runs_and_returns_but_reports_nothing(capsys):
    def tasks(reg):
        @reg.task
        def release():
            r = run(_echo("abc123"), recorded=False)
            assert r == 0
            assert r.stdout.strip() == "abc123"  # the value is the whole point
            run(_echo("tagged"))  # a real step, for contrast

    _, _, results = drive(tasks, "release")
    assert results[0].ok, results[0].error
    # Only the real step is recorded — the value read is absent from what
    # `--json`, the report and `recording()` all read.
    assert [s.command for s in results[0].steps] == [_echo("tagged")]
    out = capsys.readouterr().out
    assert "abc123" not in out  # no receipt, no output replayed


def test_a_non_step_call_executes_under_recording():
    # A step is faked under dry_run; a non-step is not the story being
    # recorded, and faking it would corrupt the story that is — the real
    # steps downstream would record whatever a blank answer produced.
    from livery.footman.testing import recording

    with recording() as steps:
        real = run(_echo("live"), recorded=False)
        run("echo pretend")

    assert real.stdout.strip() == "live"  # actually ran
    assert [s.command for s in steps] == ["echo pretend"]  # only the step


def test_a_non_step_call_still_fails_the_task():
    # Unreported is not unmanaged: a non-zero exit still raises unless nofail.
    def tasks(reg):
        @reg.task
        def build():
            run([sys.executable, "-c", "raise SystemExit(3)"], recorded=False)

    _, _, results = drive(tasks, "build")
    assert not results[0].ok
    assert results[0].code == 3

    def tolerant(reg):
        @reg.task
        def build():
            r = run(
                [sys.executable, "-c", "raise SystemExit(3)"],
                recorded=False,
                nofail=True,
            )
            assert r == 3

    _, _, results = drive(tolerant, "build")
    assert results[0].ok, results[0].error


def test_a_title_on_a_non_step_call_is_noted_once(capsys):
    # Not an error: `.opts()` merges along a chain, so a shared tool can carry
    # a title while a call site adds recorded=False — neither author wrote the
    # contradiction. Said once, on stderr.
    from livery.footman import _notes

    _notes.reset()

    def tasks(reg):
        @reg.task
        def build():
            run(_echo("one"), recorded=False, title="labelled")
            run(_echo("two"), recorded=False, title="labelled")

    _, _, results = drive(tasks, "build")
    assert results[0].ok, results[0].error
    err = capsys.readouterr().err
    assert err.count("title= is ignored on a recorded=False call") == 1


# --- timeout: a bound the caller declares ------------------------------------


def _sleeper(seconds: float) -> list[str]:
    """A portable "sleep then print" — the interpreter is the one program
    every machine running this suite is guaranteed to have."""
    return [sys.executable, "-c", f"import time; time.sleep({seconds}); print('done')"]


def test_a_timeout_kills_the_call_and_raises_runtimeout():
    from livery.footman.context import Context, RunFailed, RunTimeout, use_context

    with use_context(Context()), pytest.raises(RunTimeout) as caught:
        run(_sleeper(30), timeout=0.5)

    assert isinstance(caught.value, RunFailed)  # old handlers keep working
    assert caught.value.timeout == 0.5
    result = caught.value.result
    assert result.timed_out
    assert result.code == 124  # the shell convention, not a private sentinel
    assert "timed out after 0.5s" in str(caught.value)


def test_a_timeout_under_nofail_returns_the_result():
    from livery.footman.context import Context, use_context

    with use_context(Context()):
        result = run(_sleeper(30), timeout=0.5, nofail=True)
    assert result.timed_out
    assert result.code == 124


# --- a missing executable: the taught error ----------------------------------


def test_a_missing_executable_raises_a_taught_command_not_found():
    from livery.footman.context import CommandNotFound, Context, use_context

    with use_context(Context()), pytest.raises(CommandNotFound) as caught:
        run(["definitely-not-installed-anywhere", "--version"])

    assert isinstance(caught.value, FileNotFoundError)  # old handlers keep working
    assert caught.value.command == "definitely-not-installed-anywhere"
    message = str(caught.value)
    assert "no executable 'definitely-not-installed-anywhere'" in message
    assert "@footman.requires_tool" in message


def test_a_missing_executable_is_not_silenced_by_nofail():
    from livery.footman.context import CommandNotFound, Context, use_context

    # No command ran, so there is no exit code for nofail to accept — the
    # environment defect raises either way.
    with use_context(Context()), pytest.raises(CommandNotFound):
        run(["definitely-not-installed-anywhere"], nofail=True)


def test_a_missing_cwd_keeps_the_honest_os_error(tmp_path):
    from livery.footman.context import CommandNotFound, Context, use_context

    # POSIX spells it FileNotFoundError, Windows NotADirectoryError
    # ([WinError 267]) — either way the interpreter exists and the
    # directory does not, and blaming the tool would teach the wrong fix.
    with (
        use_context(Context()),
        pytest.raises(OSError) as caught,
    ):
        run([sys.executable, "--version"], cwd=tmp_path / "nowhere")

    assert not isinstance(caught.value, CommandNotFound)


def test_a_call_inside_its_timeout_is_untouched():
    from livery.footman.context import Context, use_context

    with use_context(Context()):
        result = run(_sleeper(0), timeout=30)
    assert not result.timed_out
    assert result.ok
    assert result.stdout.strip() == "done"


def test_run_refuses_a_bare_callable_and_teaches_the_lift():
    # run() runs commands; in-process work is a step. (A step's own
    # timeout= is honoured at checkpoints — test_step pins it.)
    from livery.footman.context import Context, use_context

    with use_context(Context()), pytest.raises(TypeError, match=r"work is a step"):
        run(lambda: 0, timeout=5)


def test_run_refuses_trailing_arguments_it_would_have_dropped():
    # The subprocess-style spelling this door does not have: the extras only
    # ever reached the label, so `run("echo", "hi")` passed green having
    # printed nothing, and `run("sh", "-c", …)` ran a bare shell on the
    # caller's terminal. Refused now, naming the spelling that works.
    from livery.footman.context import Context, use_context

    with use_context(Context()):
        with pytest.raises(TypeError, match=r"run\(\['echo', 'hi'\]\)"):
            run("echo", "hi")
        with pytest.raises(TypeError, match=r"run\(\['sh', '-c', 'exit 1'\]\)"):
            run("sh", "-c", "exit 1")
        # A list command with extras teaches the joined list too.
        with pytest.raises(TypeError, match=r"run\(\['git', 'log', '-1'\]\)"):
            run(["git", "log"], "-1")


def test_run_input_feeds_the_childs_stdin():
    # The write side of the process boundary: the payload arrives whole and
    # the pipe closes, so a child reading to EOF finishes rather than hangs.
    from livery.footman.context import Context, use_context

    reader = "import sys; print(sys.stdin.read().upper(), end='')"
    with use_context(Context()):
        result = run([sys.executable, "-c", reader], input="fed via stdin\n")
    assert result.stdout == "FED VIA STDIN\n"


def test_run_without_input_leaves_stdin_alone():
    # No payload, no pipe: the child sees whatever stdin the process had —
    # here not-a-terminal, and crucially not an instantly-EOF pipe footman
    # opened on its behalf.
    from livery.footman.context import Context, use_context

    probe = "import sys; print(sys.stdin is not None)"
    with use_context(Context()):
        result = run([sys.executable, "-c", probe], input=None)
    assert result.stdout.strip() == "True"


def test_run_input_on_an_in_process_tool_is_a_taught_error():
    # Reached only through the tools bridge's in-process lane: a subprocess
    # has a stdin to feed, a Python call does not.
    from livery.footman.context import Context, Invocation, use_context

    show = Invocation(parts=(("prog", "demo"),), exact=("demo",))
    with (
        use_context(Context()),
        pytest.raises(TypeError, match=r"in-process tool has none"),
    ):
        run(lambda: 0, input="payload", _show=show)


def test_a_timed_out_call_is_still_a_step_unless_it_says_otherwise():
    from livery.footman.context import Context, use_context

    ctx = Context()
    with use_context(ctx):
        run(_sleeper(30), timeout=0.5, nofail=True)
    assert len(ctx.steps) == 1  # it happened and it failed; the receipt says so

    quiet_ctx = Context()
    with use_context(quiet_ctx):
        run(_sleeper(30), timeout=0.5, nofail=True, recorded=False)
    assert quiet_ctx.steps == []  # step governs reporting, never behaviour


def test_a_captured_windows_child_gets_no_console_window(monkeypatch):
    """Windows Terminal hands each spawn a visible window, and a tool that
    interrogates the terminal at start-up hangs against the caller's console.
    A captured read has no business owning one — so it gets a hidden console
    (CREATE_NO_WINDOW), never a detached one, which would leave pwsh and
    git-bash with no console at all."""
    import subprocess as sp

    from livery.footman import context as context_mod

    seen: dict[str, object] = {}

    def fake_run(argv, env, cwd, capture, *a, **k):
        seen["no_window"] = k.get("no_window")
        return 0, "", "", False

    monkeypatch.setattr(context_mod, "_run_subprocess", fake_run)

    def tasks(reg):
        @reg.task
        def go():
            run(_echo("hi"))

    drive(tasks, "go")
    assert seen["no_window"] is True

    def streaming(reg):
        @reg.task
        def go():
            run(_echo("hi"), capture=False)  # reaching for the real terminal

    drive(streaming, "go")
    assert seen["no_window"] is False

    assert hasattr(sp, "CREATE_NO_WINDOW") == (sys.platform == "win32")


def test_to_argv_returns_what_ran_as_requotable_tokens():
    # `.raw` is quoted for the machine footman is standing on; to_argv() is
    # the tokens themselves, which serialise for whichever shell will parse
    # them — the one that matters when the string is going somewhere else.
    from livery.footman.testing import recording
    from toolroom import git

    with recording():
        result = git.commit(m="a message")
    assert result.to_argv() == ["git", "commit", "-m", "a message"]
    assert result.to_argv().posix() == "git commit -m 'a message'"
    assert result.to_argv().windows() == 'git commit -m "a message"'


def test_to_argv_teaches_when_no_argv_was_recorded():
    # A command *string* was never taken apart, and splitting one back is
    # platform-dependent guesswork — say so rather than guess.
    from livery.footman.testing import recording

    with recording():
        result = run("echo hi")
    with pytest.raises(ValueError, match=r"no argv was recorded"):
        result.to_argv()


def test_run_takes_a_built_command_line_as_its_argv():
    # An Argv IS run()'s input type — no adapter between building and running.
    from livery.footman.testing import recording
    from toolroom import docker

    payload = docker.compose.up.argv(detach=True)
    with recording() as steps:
        run(payload)
    assert steps[0].to_argv() == ["docker", "compose", "up", "--detach"]


def test_run_serialised_payloads_spell_the_boundary():
    # A payload inside a hand-written list crosses a machine boundary as one
    # quoted token, named at the call site.
    from livery.footman.testing import recording
    from toolroom import docker

    payload = docker.compose.up.argv(detach=True)
    with recording() as steps:
        run(["ssh", "app@host", payload.posix()])
    assert steps[0].to_argv() == ["ssh", "app@host", "docker compose up --detach"]


def test_run_refuses_a_bare_container_in_its_list():
    # Stringified it becomes the one token "['a', 'b']", which fails late at
    # the tool; `*` and `.posix()` are the two meant spellings, and the
    # refusal names them.
    from toolroom import docker

    payload = docker.compose.up.argv(detach=True)
    with pytest.raises(TypeError, match=r"splat it \(`\*cmd`\)"):
        run(["ssh", "app@host", payload])  # type: ignore[list-item]
    with pytest.raises(TypeError, match=r"Spread it with `\*`"):
        run(["echo", ["a", "b"]])  # type: ignore[list-item]


def test_an_async_body_is_refused_rather_than_reported_ok():
    # Calling an `async def` builds a coroutine and runs none of it, so this
    # used to report `ok` for a body that never executed — a receipt with no
    # work behind it. footman runs no event loop on purpose (docs/design.md,
    # "No event loop"), so the refusal names the way in instead.
    ran: dict[str, bool] = {}

    def build(reg):
        @reg.task
        async def sleeper():
            ran["it"] = True

    _, _, results = drive(build, "sleeper")
    assert not results[0].ok
    assert not ran.get("it")  # it never ran, and never claimed to
    assert "async def" in str(results[0].error)
    assert "asyncio.run" in str(results[0].error)


def test_a_yielding_body_is_refused_rather_than_reported_ok():
    # Calling a generator function builds the generator and runs none of it,
    # so this reported `ok` for a body that never executed — the audit's
    # measured hole. The shape is reserved (`yield` on a task is the coming
    # service form), so the refusal names the reservation and the way out
    # that exists today.
    ran: dict[str, bool] = {}

    def build(reg):
        @reg.task
        def pump():
            ran["it"] = True
            yield

    _, _, results = drive(build, "pump")
    assert not results[0].ok
    assert not ran.get("it")  # it never ran, and never claimed to
    said = str(results[0].error)
    assert "generator function" in said
    assert "@step" in said
    assert "test_context.py:" in said  # the definition site, not a traceback


def test_an_async_generator_body_is_refused_not_reported_ok():
    # `async def` plus `yield` is neither a coroutine (`iscoroutine` misses
    # it) nor a plain generator function — without its own check it sealed
    # `ok` for zero work: the coroutine hole through a second door.
    ran: dict[str, bool] = {}

    def build(reg):
        @reg.task
        async def pump():
            ran["it"] = True
            yield

    _, _, results = drive(build, "pump")
    assert not results[0].ok
    assert not ran.get("it")
    said = str(results[0].error)
    assert "async def" in said and "generator" in said


def test_a_body_may_still_return_an_iterator_it_built():
    # The refusal keys off the declaration, not the returned object: a plain
    # body that builds and returns a generator did real work and keeps its
    # receipt. This is the boundary between the reserved shape and a value.
    def build(reg):
        @reg.task
        def maker():
            return (n * n for n in range(3))

    _, _, results = drive(build, "maker")
    assert results[0].ok


def test_an_unexpected_exception_places_itself_in_the_users_code(fm_project):
    # A task that raised used to say what happened and never where — the type
    # and message, in a file of forty tasks. Captured output is not a terminal,
    # so this is the log-destined path: the whole stack, with footman's own
    # leading frames off the front so the first line is one somebody wrote.
    fm = fm_project("""
        from livery.footman import task

        def helper(n):
            return 10 // n

        @task
        def boom():
            helper(0)
    """)
    result = fm.invoke("boom")
    assert result.exit_code != 0
    said = result.stderr
    assert "ZeroDivisionError" in said
    assert "in helper" in said  # the innermost frame the caller wrote
    assert "src/footman" not in said  # and never the plumbing that called it


def test_a_step_and_a_task_report_an_exception_the_same_way(fm_project):
    # The two halves of the runner answering one question two ways is how this
    # started: a task said only the type, a step printed footman's frames.
    fm = fm_project("""
        from livery.footman import step, task

        def helper(n):
            return 10 // n

        @task
        def in_task():
            helper(0)

        @step
        def _inner():
            helper(0)

        @task
        def in_step():
            _inner()()
    """)
    task_said = fm.invoke("in-task").stderr
    step_said = fm.invoke("in-step").stderr
    for said in (task_said, step_said):
        assert "ZeroDivisionError" in said
        assert "in helper" in said
        assert "src/footman" not in said
    # And the placement is not said twice for the step, whose own receipt
    # already carried it.
    assert step_said.count("in helper") == 1


def test_an_expected_failure_carries_no_stack(fm_project):
    # A command exiting non-zero is not a bug in the tasks file, and a location
    # would point at the line that ran it as though it were the fault.
    fm = fm_project("""
        import sys
        from livery.footman import run, task

        @task
        def failing():
            run([sys.executable, "-c", "raise SystemExit(3)"])
    """)
    result = fm.invoke("failing")
    assert result.exit_code != 0
    assert "Traceback" not in result.stderr
    assert " at " not in result.stderr


def test_the_json_row_carries_a_stack_only_for_a_real_bug(fm_project):
    # A consumer of the envelope is a log or a dashboard, never someone who
    # can re-run with -v, so the stack rides along regardless of the terminal.
    # But only for an exception nobody planned: a command exiting non-zero has
    # nothing to place, and pointing at the line that ran it would be a lie.
    import json as json_mod

    fm = fm_project("""
        import sys
        from livery.footman import fail, run, task

        def helper(n):
            return 10 // n

        @task
        def boom():
            helper(0)

        @task
        def stopped():
            fail("chose to stop")

        @task
        def command_failed():
            run([sys.executable, "-c", "raise SystemExit(3)"])
    """)
    rows = {}
    for name in ("boom", "stopped", "command-failed"):
        envelope = json_mod.loads(fm.invoke(f"--json {name}").stdout)
        rows[name] = next(i for i in envelope["items"] if i.get("task"))

    trace = rows["boom"]["traceback"]
    assert "in helper" in trace  # the caller's own frame
    assert "src/footman" not in trace  # never the plumbing that called it
    assert "traceback" not in rows["stopped"]  # a chosen stop
    assert "traceback" not in rows["command-failed"]  # a command's exit code


def _await_pids(*files: Path, timeout: float = 30.0) -> list[int] | None:
    """The pids in *files*, once every one holds a whole number.

    Waiting on `exists()` is the tempting spelling and it is a race: the child
    creates the file, then writes to it, so between those two syscalls the path
    exists and reads back empty. `int("")` then fails the test for a reason
    that has nothing to do with what it tests. Parsing IS the readiness check.
    """
    import time

    deadline = time.time() + timeout
    while True:
        try:
            return [int(f.read_text()) for f in files]
        except (OSError, ValueError):
            if time.time() >= deadline:
                return None
            time.sleep(0.05)


def test_ctrl_c_reaps_the_child_a_task_was_waiting_on(tmp_path):
    """`fm` exited 130 while the thing it started kept running.

    The child is spawned into its own process group on purpose, so the
    terminal's SIGINT never reaches it and footman must reap it by hand — but
    the `finally` that unregisters it ran first, as the interrupt unwound, so
    the reaper upstairs found an empty registry.

    Driven as a real process in its own session: the bug is entirely about
    which signal reaches whom, and nothing in-process can pose that question.
    """
    import os
    import signal
    import subprocess
    import time

    if sys.platform == "win32":
        # In the body rather than a decorator: `killpg`/`SIGKILL` do not exist
        # on Windows, and the checkers read this narrowing the same way the
        # source's own `_kill_tree` relies on.
        pytest.skip("POSIX process groups and SIGINT")

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
    env = {**os.environ, "FOOTMAN_CACHE_DIR": str(tmp_path / ".cache")}
    env.pop("VIRTUAL_ENV", None)
    runner = subprocess.Popen(
        [sys.executable, "-m", "footman", "slow"],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        # The runner is a cold interpreter loading a tree before the
        # task can spawn the child, and a loaded machine legitimately
        # stretches that; the deadline covers load, and a miss fails
        # with the runner's own words instead of proving nothing.
        pids = _await_pids(pid_file, timeout=120.0)
        if pids is None:
            runner.kill()
            _, stderr = runner.communicate(timeout=10)
            pytest.fail(
                "the child never started within 120s; the runner said:\n"
                + (stderr or "(nothing on stderr)")
            )
        (child,) = pids

        os.killpg(runner.pid, signal.SIGINT)  # what a terminal does
        runner.wait(timeout=30)

        gone_by = time.time() + 15
        while time.time() < gone_by:
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            os.kill(child, signal.SIGKILL)  # do not leak it into the suite
            pytest.fail("the child outlived the interrupt")
    finally:
        if runner.poll() is None:  # pragma: no cover - only on a regression
            runner.kill()


def test_ctrl_c_does_not_wait_out_an_in_body_parallel(tmp_path):
    """Ctrl-C during an in-body `parallel()` waited out the work it cancelled.

    The fan-out pool had no abort arm, so the interrupt unwound in the main
    thread and then blocked in the pool's `with` exit, joining workers that
    each sat in communicate() on a group-isolated child the terminal's SIGINT
    never reached. The scheduler's pool has had that arm all along; this one
    only shows when the task holding the fan-out runs on the main thread — a
    bare `fm <task>`, or any -s run — with no outer pool to save it.

    A real process, for the same reason as the sibling test above: the bug is
    about which signal reaches whom. And a hang in-process would take the
    whole suite with it.
    """
    import os
    import signal
    import subprocess
    import time

    if sys.platform == "win32":
        pytest.skip("POSIX process groups and SIGINT")

    (tmp_path / "tasks.py").write_text(
        textwrap.dedent(f"""
        import sys
        from livery.footman import parallel, run, step, task

        HERE = {str(tmp_path)!r}

        def sleeper(n):
            run([sys.executable, "-c",
                 "import os, sys, time;"
                 "open(sys.argv[1], 'w').write(str(os.getpid()));"
                 "time.sleep(120)",
                 HERE + "/child-" + str(n) + ".pid"])

        @task
        def slow():
            parallel(step(sleeper)(1), step(sleeper)(2))
        """)
    )
    env = {**os.environ, "FOOTMAN_CACHE_DIR": str(tmp_path / ".cache")}
    env.pop("VIRTUAL_ENV", None)
    runner = subprocess.Popen(
        [sys.executable, "-m", "footman", "slow"],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    pid_files = [tmp_path / "child-1.pid", tmp_path / "child-2.pid"]
    children: list[int] = []
    try:
        waited = _await_pids(*pid_files)
        assert waited is not None, (
            "both children never started; the test proves nothing"
        )
        children = waited

        os.killpg(runner.pid, signal.SIGINT)  # what a terminal does
        try:
            # Well under the 120s the children sleep: a runner that waits for
            # them is the bug, and one Ctrl-C is all the user gets to press.
            runner.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pytest.fail("the interrupt waited out the fan-out's children")

        gone_by = time.time() + 15
        while time.time() < gone_by:
            if not any(_alive(pid) for pid in children):
                break
            time.sleep(0.05)
        else:
            pytest.fail("a child outlived the interrupt")
    finally:
        if runner.poll() is None:  # pragma: no cover - only on a regression
            runner.kill()
        for pid in children:  # never leak a sleeper into the rest of the suite
            if _alive(pid):  # pragma: no cover - only on a regression
                os.kill(pid, signal.SIGKILL)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_the_stack_rule_reads_verbose_or_a_log():
    from livery.footman import _describe

    assert not _describe.stack_wanted(verbose=False, is_terminal=True)
    assert _describe.stack_wanted(verbose=True, is_terminal=True)
    assert _describe.stack_wanted(verbose=False, is_terminal=False)  # a log
    assert _describe.stack_wanted(verbose=True, is_terminal=False)


def test_the_async_refusal_reads_the_call_not_the_function():
    # Deliberately checked on what the body *returned*, not on whether the
    # function is a coroutine function. A sync wrapper hides the latter both
    # ways round: one forgets to await (the body never runs, and the wrapper
    # looks synchronous), the other drives it properly (and must be allowed).
    import asyncio
    import functools

    ran: dict[str, bool] = {}

    def leaks(f):
        @functools.wraps(f)
        def inner(*a, **k):
            return f(*a, **k)  # never awaited

        return inner

    def runs(f):
        @functools.wraps(f)
        def inner(*a, **k):
            return asyncio.run(f(*a, **k))

        return inner

    def build_leaky(reg):
        @reg.task
        @leaks
        async def leaky():
            ran["leaky"] = True

    def build_proper(reg):
        @reg.task
        @runs
        async def proper():
            ran["proper"] = True

    # Driven apart: chained, the first failure would fail-fast the second and
    # the assertion below would pass for the wrong reason.
    _, _, leaky_results = drive(build_leaky, "leaky")
    assert not leaky_results[0].ok and not ran.get("leaky")

    _, _, proper_results = drive(build_proper, "proper")
    assert proper_results[0].ok and ran.get("proper")  # awaited: real work
