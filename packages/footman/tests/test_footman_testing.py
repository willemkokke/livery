"""The public testing surface: Runner, recording(), use_context, fixtures.

The pytest fixtures (`fm_project`, `fm_record`) are exercised directly here —
footman's own suite consuming its own plugin is the point.
"""

from __future__ import annotations

import json
import sys
from typing import Literal

import pytest

from livery.footman import Context, run, use_context
from livery.footman._executor import EX_USAGE
from livery.footman.app import App
from livery.footman.context import current
from livery.footman.registry import Group
from livery.footman.testing import Result, Runner, recording


def _echo(text: str) -> str:
    """A portable `echo <text>` — for the one run() here that *executes*.

    The recording() tests keep their `echo`/`git`/`cargo` strings: nothing
    records more honestly than a command that would run anywhere, and they
    never execute. `use_context` runs for real, so it must not assume
    coreutils on PATH (Windows has an `echo.exe` only when Git's `usr/bin`
    is there); the interpreter running the suite always exists."""
    return f'"{sys.executable}" -c "print(\'{text}\')"'


# --- recording(answers=) ------------------------------------------------------


def test_answers_script_stdout_by_command_prefix():
    """A str answers with that stdout and exit 0; the key is a prefix of the
    recorded command, and the step is still recorded."""
    with recording(answers={"uv tool list": "hse v1\n- hse\n"}) as steps:
        listing = run("uv tool list --show-paths")
    assert listing.stdout == "hse v1\n- hse\n"
    assert listing.ok
    assert [s.command for s in steps] == ["uv tool list --show-paths"]


def test_answers_longest_prefix_wins_and_needs_a_word_boundary():
    with recording(answers={"git": 0, "git push": 1, "git pushx": 3}):
        assert run("git status", nofail=True) == 0
        assert run("git push --tags", nofail=True) == 1
        assert run("git pushx", nofail=True) == 3
        assert run("git pushy", nofail=True) == 0  # "git push" is not a prefix


def test_answers_accept_tuple_keys_and_collapse_whitespace():
    with recording(answers={("uv", "build"): 2, "  git   push ": 1}):
        assert run("uv build", nofail=True) == 2
        assert run("git push", nofail=True) == 1


def test_a_non_zero_answer_takes_the_real_failing_lane():
    """Raised as RunFailed unless nofail — the error path under test really
    runs, with footman's own Result carrying the scripted verdict."""
    from livery.footman.context import RunFailed

    with recording(answers={"uv build": Result(1, stderr="boom")}) as steps:
        with pytest.raises(RunFailed) as failed:
            run("uv build")
        soft = run("uv build", nofail=True)
    assert failed.value.result.stderr == "boom"
    assert soft == 1 and soft.stderr == "boom"
    assert [s.code for s in steps] == [1, 1]


def test_an_unmatched_call_keeps_the_blank_success():
    with recording(answers={"git push": 1}) as steps:
        assert run("git tag v1").ok
    assert steps[0].stdout == ""


def test_an_exception_answer_is_raised_by_the_call():
    """The missing-binary case: the call raises what the table holds, so an
    `except OSError:` guard in the task is the code path exercised."""
    with recording(answers={"uv python find": FileNotFoundError("uv")}):
        with pytest.raises(FileNotFoundError):
            run("uv python find 3.12")
        # an unrelated call is untouched
        assert run("git status").ok


def test_a_sequence_answers_in_order_then_refuses_by_name():
    with recording(answers={"git describe": ["v1.0\n", "v1.1\n"]}):
        assert run("git describe").stdout == "v1.0\n"
        assert run("git describe --tags").stdout == "v1.1\n"
        with pytest.raises(LookupError, match=r"'git describe'.*2 entries"):
            run("git describe")


def test_a_sequence_may_mix_answer_kinds():
    from livery.footman.context import RunFailed

    with recording(answers={"git push": [1, "ok\n"]}):
        with pytest.raises(RunFailed):
            run("git push")
        assert run("git push").stdout == "ok\n"  # the retry succeeds


def test_a_matched_answer_intercepts_an_off_record_read():
    """hse's git seam calls with recorded=False, nofail=True everywhere: a
    scripted answer must win over the truthful-read default, or the feature
    retires nothing. An unmatched off-record call still executes."""
    with recording(answers={"git rev-parse": "abc123\n"}) as steps:
        sha = run("git rev-parse HEAD", recorded=False, nofail=True)
        live = run(_echo("live"), recorded=False)
    assert sha.stdout == "abc123\n"
    assert live.stdout.strip() == "live"
    assert steps == []  # neither was a step; the record is untouched


def test_a_recorded_step_keeps_the_env_and_cwd_it_would_have_run_with(tmp_path):
    with recording(env={"HOME": "/h"}, cwd=tmp_path) as steps:
        run("git status")
        run("uv build", env={"UV_TOOL_DIR": "/t"}, rel="pkg")
        run("ls", cwd="unmanaged")
    assert steps[0].env == {"HOME": "/h"} and steps[0].cwd == tmp_path
    assert steps[1].env == {"UV_TOOL_DIR": "/t"}
    assert steps[1].cwd == tmp_path / "pkg"
    assert steps[2].cwd is None
    seen_by_git = steps[0].env
    assert seen_by_git is not None  # a recorded step always keeps it
    assert "UV_TOOL_DIR" not in seen_by_git  # the assertion hse writes


def test_a_live_record_keeps_no_env():
    with use_context(Context()) as ctx:
        run(_echo("x"))
    assert ctx.steps[0].env is None and ctx.steps[0].cwd is None


def test_a_reviewer_sees_a_scripted_answer():
    """An adjudicator is tested *against* scripted exits: pre_record runs on
    the scripted draft, may amend the verdict, and the audit says so."""
    from livery.footman.context import RunFailed

    def adjudicate(view):
        if "changes required" in view.stdout:
            view.code = 3

    with (
        recording(answers={"djlint": "changes required\n"}) as steps,
        pytest.raises(RunFailed) as failed,
    ):
        run("djlint .", pre_record=adjudicate)
    assert failed.value.result == 3
    assert [e.moment for e in steps[0].audit] == ["body", "review"]


def test_an_unscripted_step_is_still_not_reviewed():
    seen: list[object] = []
    with recording() as steps:
        run("git push", pre_record=seen.append)
    assert seen == [] and steps[0].audit == ()


def test_answers_refuse_a_bad_key_or_value():
    with pytest.raises(TypeError, match="command prefix"):
        recording(answers={"": 0}).__enter__()
    with pytest.raises(TypeError, match="exception class"):
        recording(answers={"uv": FileNotFoundError}).__enter__()
    with pytest.raises(TypeError, match="must be a str"):
        recording(answers={"uv": 1.5}).__enter__()
    with pytest.raises(TypeError, match="non-empty list"):
        recording(answers={"uv": []}).__enter__()


def test_children_share_the_script():
    """A parallel() child is born by replace(): the table rides by reference,
    so a sequence consumed in a child advances the same cursor."""
    with recording(answers={"git describe": ["a", "b"]}) as steps:
        parent = current()
        child = parent.child("x")
        assert child.answers is parent.answers
        with use_context(child):
            run("git describe")
        run("git describe")
    assert steps[0].stdout == "b"


def test_answers_cover_a_bridge_call_under_a_live_context():
    """Under a recording, host detection routes a toolroom handle through
    run(), so one table answers plain calls and handles alike."""
    from toolroom import git

    with recording(answers={"git branch": "main\n"}) as steps:
        out = git.branch(show_current=True)
    assert out.stdout == "main\n"
    assert steps[0].command.startswith("git branch")


def test_toolrooms_table_wins_when_both_are_nested():
    from toolroom import git
    from toolroom.testing import answers

    with (
        recording(answers={"git branch": 1}) as steps,
        answers({("git", "branch"): "main\n"}) as calls,
    ):
        out = git.branch()
    assert out.stdout == "main\n" and out == 0
    assert steps == [] and len(calls) == 1


def test_runner_invoke_answers_script_the_whole_invocation():
    """`invoke(answers=)` implies --dry-run and reaches the executor's own
    contexts, so the CLI runs end to end against a scripted world."""
    tree = Group("t")

    @tree.task
    def describe():
        print(run("git describe").stdout.strip())

    result = Runner().invoke("describe", tasks=tree, answers={"git describe": "v9\n"})
    assert result.ok
    assert "v9" in result.stdout


def test_runner_invoke_without_answers_keeps_the_process_clean():
    from livery.footman import context as _context

    assert _context._injected_answers is None
    Runner().invoke("greet", tasks=_demo_group(), answers={"x": 0})
    assert _context._injected_answers is None


# --- recording() / use_context ------------------------------------------------


def test_recording_captures_without_executing(tmp_path):
    marker = tmp_path / "should-not-exist"

    def deploy():
        run(f"touch {marker}")
        run("git push --tags")

    with recording() as steps:
        deploy()
    assert [s.command for s in steps] == [f"touch {marker}", "git push --tags"]
    assert not marker.exists()


def test_recording_holds_the_secret_the_display_hides(capsys):
    """`recording()` is a record, not a display: a test asserting on what a
    task builds must see the value it built with. The `$` rehearsal line
    above it is a display, and shows `***`."""
    from livery.footman.params import Secret

    with recording(quiet=False) as steps:
        run(["git", "push", Secret("hunter2")])
    assert steps[0].command == "git push hunter2"
    assert steps[0].shown == "git push ***"
    assert "hunter2" not in capsys.readouterr().out


def test_recording_is_silent(capsys):
    with recording():
        run("echo NOPE")
    assert capsys.readouterr().out == ""


def test_recording_can_override_quiet(capsys):
    # F51: dry_run/quiet were positional defaults, so passing either as an
    # override raised "got multiple values for keyword argument". Overridable now.
    with recording(quiet=False) as steps:
        run("echo hi")
    assert steps[0].command == "echo hi"  # still recorded, not executed
    assert "$ echo hi" in capsys.readouterr().out  # quiet=False took effect


def test_use_context_installs_and_restores():
    cmd = _echo("hi")
    ctx = Context(env={"MODE": "test"})
    with use_context(ctx) as installed:
        assert installed is ctx
        run(cmd)  # a step, which is what this asserts on; pytest swallows the line
    assert ctx.steps[0].command == cmd
    # Outside the block a fresh default context applies again.
    with recording() as steps:
        run("echo bye")
    assert steps[0].command == "echo bye"
    assert len(ctx.steps) == 1


# --- Runner with an in-memory Group --------------------------------------------


def _demo_group() -> Group:
    g = Group("root")

    @g.task
    def greet(name: str = "world"):
        """Say hello."""
        print(f"hello {name}")

    @g.task
    def fail():
        """Exit non-zero."""
        raise SystemExit(3)

    return g


def test_runner_group_invoke():
    result = Runner().invoke("greet --name=tester", tasks=_demo_group())
    assert result.ok
    assert "hello tester" in result.stdout
    assert [r.task for r in result.results] == ["greet"]


def test_runner_group_failure_is_returned_not_raised():
    result = Runner().invoke("fail", tasks=_demo_group())
    assert result.exit_code == 3
    assert not result.ok


def test_runner_group_chain_error_teaches():
    result = Runner().invoke("nope", tasks=_demo_group())
    assert result.exit_code == EX_USAGE
    assert "no task named" in result.stderr


def test_runner_group_dry_run_matches_cli_semantics():
    result = Runner().invoke("--dry-run greet --name=x", tasks=_demo_group())
    assert result.ok
    assert "hello x" in result.stdout  # rehearsed: the body runs, bound


# Group mode now shares the real CLI's post-manifest tail (`run_group`), so
# --help/--version/--list/--tree/--json/--where all behave identically — the
# old drifted re-implementation executed tasks on --help and emitted nothing
# for the rest (F18/F36 testing-surface bug).


def test_runner_group_help_never_executes():
    ran: list[str] = []
    g = Group("root")

    @g.task
    def greet(name: str = "world"):
        """Say hello."""
        ran.append(name)

    result = Runner().invoke("--help greet", tasks=g)
    assert result.ok
    assert ran == []  # asking for help must never run the task
    assert "usage:" in result.stdout and "greet" in result.stdout


def test_runner_group_version_uses_brand():
    app = App(name="Acme", prog="acme", version="9.9.9")
    result = Runner(app).invoke("--version", tasks=_demo_group())
    assert result.ok
    assert result.stdout.strip() == "Acme 9.9.9"


def test_runner_group_list_and_tree_render():
    listing = Runner().invoke("--list", tasks=_demo_group())
    assert listing.ok
    assert "greet" in listing.stdout and "Say hello." in listing.stdout

    tree = Runner().invoke("--tree", tasks=_demo_group())
    assert tree.ok
    assert "greet" in tree.stdout


def test_empty_tree_reports_no_tasks():
    # F35: an empty --tree used to print zero bytes and exit 0; now it mirrors
    # --list's "No tasks defined."
    result = Runner().invoke("--tree", tasks=Group("root"))
    assert result.ok
    assert "No tasks defined." in result.stdout


def test_runner_group_json_output():
    result = Runner().invoke("--json greet --name=J", tasks=_demo_group())
    assert result.ok
    payload = json.loads(result.stdout)
    assert payload["items"][0]["task"] == "greet"
    assert "hello J" in payload["items"][0]["output"]


def test_help_example_uses_choice_values():
    # 11.3: a synthesised example fills a choice param with its first choice.
    # (Literal is imported at module level so eval_str can resolve the string
    # annotation under `from __future__ import annotations`.)
    g = Group("root")

    @g.task
    def release(part: Literal["major", "minor", "patch"]):
        """Cut a release."""

    result = Runner().invoke("--help release", tasks=g)
    assert "Example: fm release major" in result.stdout


def test_runner_group_where_locates_source():
    result = Runner().invoke("--where=greet", tasks=_demo_group())
    assert result.ok
    assert "test_footman_testing.py:" in result.stdout  # file:line of the task body


# --- Runner against a project on disk ------------------------------------------

TASKS = """
from livery.footman import task, run

@task
def hi(name: str = "world"):
    "Say hello."
    print(f"hello {name}")
"""


def test_runner_tasks_path_uses_single_file(tmp_path):
    tasks = tmp_path / "mytasks.py"
    tasks.write_text(TASKS)
    result = Runner().invoke("hi --name=path", tasks=tasks, cwd=tmp_path)
    assert result.ok
    assert "hello path" in result.stdout
    assert result.results[0].task == "hi"


def test_runner_file_path_propagates_keyboard_interrupt(tmp_path):
    # F52: the invoke docstring promises Ctrl-C passes through — a test runner
    # must let pytest handle it, not swallow it into a 130 exit code. Group mode
    # already propagates (run_group); the file path did not until it stopped
    # going through the CLI's KI wrapper.
    import pytest

    tasks = tmp_path / "tasks.py"
    tasks.write_text(
        "from footman import task\n@task\ndef boom():\n    raise KeyboardInterrupt\n"
    )
    with pytest.raises(KeyboardInterrupt):
        Runner().invoke("boom", tasks=tasks, cwd=tmp_path)


def test_runner_discovers_cascade_from_cwd(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n')
    (tmp_path / "tasks.py").write_text(TASKS)
    result = Runner().invoke("hi", cwd=tmp_path)
    assert result.ok
    assert "hello world" in result.stdout


def test_runner_branded_app_prefixes_errors(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n')
    (tmp_path / "tasks.py").write_text(TASKS)
    acme = Runner(App(name="Acme", prog="acme", version="9.9.9"))
    result = acme.invoke("nope", cwd=tmp_path)
    assert result.exit_code == EX_USAGE
    assert result.stderr.startswith("acme:")
    version = acme.invoke("--version", cwd=tmp_path)
    assert "Acme 9.9.9" in version.stdout


# --- the pytest fixtures (dogfooding the plugin) --------------------------------


def test_fm_project_fixture_scaffolds_and_runs(fm_project):
    fm = fm_project(
        """
        from livery.footman import task

        @task
        def ping():
            "Pong."
            print("pong")
        """
    )
    result = fm.invoke("ping")
    assert result.ok
    assert "pong" in result.stdout


def test_fm_project_fixture_custom_tasks_filename(fm_project):
    fm = fm_project(
        """
        from livery.footman import task

        @task
        def jobs_only():
            print("via-jobs")
        """,
        name="jobs.py",
    )
    result = fm.invoke("jobs-only")
    assert result.ok
    assert "via-jobs" in result.stdout


def test_fm_record_fixture_captures_steps(fm_record):
    def build():
        run("cargo build --release")

    build()
    assert fm_record[0].command == "cargo build --release"


def test_an_in_memory_run_records_no_timing_history(tmp_path, monkeypatch):
    # A synthetic tree pollutes no cache, times included — the rule `-f`
    # runs already follow. Left unguarded, every Runner drive in a
    # consumer's test suite wrote `*.times.json` (keyed by an ephemeral
    # pytest cwd) into the real user cache, and mkdir'd it to do so.
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    # Force the estimate/record gate open so the guard is what's tested,
    # not whether this tiny tree happened to consent to progress.
    monkeypatch.setattr("livery.footman._schedule.dag_wants_progress", lambda *a: True)
    root = Group("root")

    @root.task
    def hi():
        "Say hello."

    result = Runner(App(name="acme", prog="acme")).invoke("hi", tasks=root)
    assert result.exit_code == 0
    assert not list(tmp_path.rglob("*.times.json"))
