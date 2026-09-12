"""End-to-end: the execution path from argv to exit code."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from livery.footman import _app, _manifest, _paths, _progress
from livery.footman._executor import EX_USAGE
from livery.footman._split import Segment

TASKS = '''
from typing import Annotated

from livery.footman import doc, fail, task, group

@task
def hi(name: str = "world"):
    """Say hello."""
    print(f"hello {name}")

@task
def add(a: int, b: int):
    """Print a sum."""
    print(a + b)

@task
def boom():
    """Fail on purpose."""
    raise SystemExit(2)

@task
def refuse():
    """Stop with a reason."""
    raise SystemExit("refused: no open PR to act on")

@task
def refuse_fn():
    """Fail via footman.fail with a reason."""
    fail("no open PR to act on")

@task
def refuse_fn_code():
    """Fail via footman.fail with a custom code."""
    fail("reserved branch", code=3)

@task
def refuse_fn_bare():
    """Fail via footman.fail with no reason."""
    fail()

@task
def flag(fix: bool = False):
    """A flag task."""
    print(f"fix={fix}")

@task
def crash():
    """Raise a real exception."""
    raise RuntimeError("kaboom")

@task
def cancelled():
    """Raise a BaseException that is not a run-level one."""
    import asyncio

    raise asyncio.CancelledError("the loop cancelled us")


@task
def data():
    """Return structured data."""
    return {"n": 1, "flags": [True, False]}

@task
def opaque():
    """Return an unserialisable object."""
    return object()

@task
def code3():
    """Return an int exit code."""
    return 3

@task
def fix(dry: Annotated[bool, doc("plan only, change nothing")] = False):
    """Fix things.

    Args:
        dry: the docstring text that the marker beats
    """

@task
def publish(cache: bool = True):
    """Publish it."""

@task
def sync(force: bool = False):
    """Sync the things.

    Runs the whole pipeline,
    twice if needed.

    Args:
        force: skip the freshness check
    """

tools = group("tools", help="Extra tools")

@tools.task
def echo(*words: str):
    """Echo words."""
    print(" ".join(words))
'''


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(TASKS)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    return tmp_path


def test_run_a_task(project, capsys):
    assert _app.run(["hi", "--name=footman"]) == 0
    assert "hello footman" in capsys.readouterr().out


def test_chain_with_coercion(project, capsys):
    assert _app.run(["add", "2", "3", "hi"]) == 0
    out = capsys.readouterr().out
    assert "5" in out
    assert "hello world" in out


def test_group_task_variadic(project, capsys):
    assert _app.run(["tools.echo", "a", "b", "c"]) == 0
    assert "a b c" in capsys.readouterr().out


def test_version(project, capsys):
    from livery.footman import __version__

    assert _app.run(["--version"]) == 0
    assert __version__ in capsys.readouterr().out


def test_list_with_no_segments(project, capsys):
    assert _app.run([]) == 0
    out = capsys.readouterr().out
    assert "hi" in out and "tools.echo" in out


def test_dry_run_runs_bodies_and_fakes_footmans_work(project, capsys):
    # The rehearsal: inline code executes (it was never footman's to fake);
    # the recorded run() is faked into a receipt and no subprocess spawns.
    (project / "tasks.py").write_text(
        "from livery.footman import run, task\n"
        "@task\n"
        "def ship():\n"
        "    print('inline ran')\n"
        "    run('touch shipped.txt')\n"
    )
    assert _app.run(["--dry-run", "ship"]) == 0
    out = capsys.readouterr().out
    assert "inline ran" in out  # the body executed
    assert "touch shipped.txt" in out  # the faked receipt
    assert not (project / "shipped.txt").exists()  # nothing actually spawned


def test_json_output(project, capsys):
    assert _app.run(["--json", "hi"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == 1
    assert payload["items"][0]["task"] == "hi"
    assert payload["items"][0]["ok"] is True
    assert payload["total_ms"] >= payload["items"][0]["duration_ms"]


def test_single_task_receipt_carries_the_time(project, capsys):
    # The receipt is task-shaped and IS the total — no separate took line.
    assert _app.run(["hi"]) == 0
    err = capsys.readouterr().err
    assert "ok   hi" in err and "(0." in err
    assert "took" not in err


def test_chain_summary_ends_with_total(project, capsys):
    assert _app.run(["hi", "data"]) == 0
    err = capsys.readouterr().err
    assert "ok   hi" in err and "ok   data" in err
    assert "took" in err  # two receipts: the wall total adds information


def test_failing_task_sets_exit_code(project):
    assert _app.run(["boom"]) == 2


def test_crash_task_exits_1(project):
    assert _app.run(["crash"]) == 1  # a raised exception -> flat 1


def test_a_base_exception_from_a_body_is_a_task_failure(project, capsys):
    # An exception leaving the task is a task failure, whichever side of
    # `Exception` it sits on — the rule `sys.exit("reason")` has followed all
    # along. Catching only `Exception` let an `asyncio.CancelledError` past the
    # report entirely: a raw traceback out of `main()`, no row for the task,
    # and the sibling that succeeded never reported either.
    assert _app.run(["hi", "cancelled"]) == 1
    captured = capsys.readouterr()
    assert "hello world" in captured.out  # the sibling ran
    assert "ok   hi" in captured.err  # and still has its row
    assert "cancelled: CancelledError: the loop cancelled us" in captured.err


def test_a_base_exception_reaches_the_json_envelope(project, capsys):
    # The envelope is the whole story for a log or a dashboard, so the row and
    # its stack must be there — an exception nobody planned is exactly what the
    # `traceback` field exists for.
    assert _app.run(["--json", "hi", "cancelled"]) == 1
    rows = {item["task"]: item for item in json.loads(capsys.readouterr().out)["items"]}
    assert rows["hi"]["ok"]
    assert rows["cancelled"]["ok"] is False
    assert rows["cancelled"]["code"] == 1
    assert rows["cancelled"]["error"] == "the loop cancelled us"
    assert "in cancelled" in rows["cancelled"]["traceback"]


def test_systemexit_message_surfaces(project, capsys):
    # F2: `sys.exit("reason")` in a task body must reach the user, not be
    # swallowed into a bare "exited with code 1". Rendered verbatim (the way
    # Python prints it), with no "SystemExit:" type prefix.
    assert _app.run(["refuse"]) == 1
    err = capsys.readouterr().err
    assert "refused: no open PR to act on" in err
    assert "SystemExit" not in err


def test_systemexit_message_reaches_json(project, capsys):
    assert _app.run(["--json", "refuse"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["items"][0]["error"] == "refused: no open PR to act on"


def test_systemexit_int_code_stays_bare(project, capsys):
    # An int code carries no message: unchanged "exited with code N", no reason.
    assert _app.run(["boom"]) == 2
    err = capsys.readouterr().err
    assert "exited with code 2" in err
    assert "SystemExit" not in err


def test_a_bad_marker_is_a_taught_cli_refusal_not_a_traceback(tmp_path):
    # The build-time twin of the strict-completer refusal below: a
    # ManifestError (here a bare callable in Annotated) rides the same
    # `_refuse` door, and nothing at the CLI level pinned it — deleting
    # the except left a raw traceback with the suite green (audit, suite
    # pass).
    import textwrap

    from livery.footman.testing import Runner

    (tmp_path / "tasks.py").write_text(
        textwrap.dedent(
            """
            from typing import Annotated

            from livery.footman import task

            def lister():
                return []

            @task
            def deploy(target: Annotated[str, lister] = ""):
                "Deploy."
            """
        )
    )
    result = Runner().invoke("--list", cwd=tmp_path)
    assert result.exit_code == EX_USAGE
    assert "a bare callable is not a marker" in result.stderr
    assert "suggest(lister)" in result.stderr  # the way out, named
    assert "Traceback" not in result.stderr
    enveloped = Runner().invoke("--json --list", cwd=tmp_path)
    assert enveloped.exit_code == EX_USAGE
    payload = json.loads(enveloped.stdout)
    assert "bare callable" in payload["error"]["message"]


def test_a_raising_strict_completer_is_a_taught_cli_refusal(tmp_path):
    # Audit M96: the CLI's refusal for a strict completer that raises had
    # no CLI-level test — it could regress to a raw traceback (or refuse
    # every other task again, the pre-fix behaviour) with the suite green.
    import textwrap

    from livery.footman.testing import Runner

    (tmp_path / "tasks.py").write_text(
        textwrap.dedent(
            """
            from typing import Annotated

            from livery.footman import task
            from livery.footman.params import suggest

            def boom():
                raise RuntimeError("registry down")

            @task
            def deploy(target: Annotated[str, suggest(boom, strict=True)] = ""):
                "Deploy."

            @task
            def unrelated():
                print("fine")
            """
        )
    )
    result = Runner().invoke("deploy --target=x", cwd=tmp_path)
    assert result.exit_code == EX_USAGE
    assert "dynamic choices from boom() failed" in result.stderr
    assert "strict=False" in result.stderr  # the way out, named
    assert "Traceback" not in result.stderr
    # A broken completer on one task refuses only that task's invocation.
    ok = Runner().invoke("unrelated", cwd=tmp_path)
    assert ok.ok and "fine" in ok.stdout
    # And the --json door keeps its envelope on the same refusal.
    enveloped = Runner().invoke("--json deploy --target=x", cwd=tmp_path)
    assert enveloped.exit_code == EX_USAGE
    payload = json.loads(enveloped.stdout)
    assert "dynamic choices" in payload["error"]["message"]


def test_a_code_the_shell_cannot_carry_still_fails(tmp_path):
    # POSIX keeps only the low byte of an exit status: a task returning 256
    # printed FAIL and exited 0 — `fm deploy || rollback` never rolled back.
    # The process boundary collapses an uncarriable failure to 1; codes the
    # shell can carry pass through untouched, and the receipt line and
    # `--json` row keep the real number.
    import textwrap

    from livery.footman.testing import Runner

    (tmp_path / "tasks.py").write_text(
        textwrap.dedent(
            """
            from livery.footman import task

            @task
            def big() -> int:
                return 256

            @task
            def neg() -> int:
                return -9

            @task
            def plain() -> int:
                return 7
            """
        )
    )
    assert Runner().invoke("big", cwd=tmp_path).exit_code == 1
    assert Runner().invoke("neg", cwd=tmp_path).exit_code == 1
    assert Runner().invoke("plain", cwd=tmp_path).exit_code == 7


def test_fail_surfaces_reason(project, capsys):
    # `footman.fail("reason")` — the blessed task-failure idiom: the reason shows
    # verbatim (no `Failed:` type prefix), exit 1.
    assert _app.run(["refuse-fn"]) == 1
    err = capsys.readouterr().err
    assert "no open PR to act on" in err
    assert "Failed" not in err


def test_fail_honours_a_custom_code(project, capsys):
    # `fail("reason", code=3)` — a reason AND a chosen exit code together.
    assert _app.run(["refuse-fn-code"]) == 3
    assert "reserved branch" in capsys.readouterr().err


def test_fail_bare_falls_back_to_the_code_line(project, capsys):
    # `fail()` with no reason has nothing to render verbatim: it must not print a
    # dangling "task:" — it falls back to the code line.
    assert _app.run(["refuse-fn-bare"]) == 1
    assert "refuse-fn-bare: exited with code 1" in capsys.readouterr().err


def test_fail_reason_reaches_json(project, capsys):
    assert _app.run(["--json", "refuse-fn"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["items"][0]["error"] == "no open PR to act on"


def test_unknown_task_is_teaching_error(project, capsys):
    assert _app.run(["nope"]) == EX_USAGE
    assert "no task named" in capsys.readouterr().err


def test_where(project, capsys):
    assert _app.run(["--where=hi"]) == 0
    out = capsys.readouterr().out.strip()
    # A real pin (not the old `or ":" in out` tautology): the tasks file, and
    # hi's definition line — the decorator (6) on 3.9+, the def (7) on older
    # runtimes, tolerating co_firstlineno variance.
    assert out.startswith(str(project / "tasks.py") + ":")
    assert out.endswith(("tasks.py:6", "tasks.py:7"))


def test_bare_fm_lists_tasks(project, capsys):
    # 11.4: bare `fm` falls through to the task list, not an error.
    assert _app.run([]) == 0
    out = capsys.readouterr().out
    assert "Tasks:" in out and "hi" in out
    # The no-arg path is where a newcomer lands: point at the next step, the
    # same footer `--help` shows.
    assert "--help <task>" in out


def test_bare_fm_no_tasks_file_is_soft(tmp_path, monkeypatch, capsys):
    # 11.4: even with no tasks file, bare `fm` is a warm empty state (exit 0),
    # not the hard error a named task gets.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run([]) == 0
    assert "No tasks file found" in capsys.readouterr().out
    assert _app.run(["hi"]) == EX_USAGE  # a named task still errors


def test_missing_tasks_file(tmp_path, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run(["hi"]) == EX_USAGE
    assert "no tasks file found" in capsys.readouterr().err


def _folds_case(directory: Path) -> bool:
    """Whether *directory*'s filesystem opens one name under another case.

    Asked of the filesystem, not the platform: macOS can be formatted
    case-sensitive and Linux can mount case-insensitive volumes, so
    `sys.platform` would be a proxy for the thing itself. The wrong-case
    *complaint* only exists where the answer is yes — that is where the
    probe hits, where the confirming listing is already in hand, and where
    the mistake is invisible enough to be worth a warning. Where the two
    names are simply different files, the not-found message teaches the
    rule instead (see the case-rule test).
    """
    probe = directory / "FootmanCaseProbe"
    probe.write_text("")
    try:
        return (directory / "footmancaseprobe").exists()
    finally:
        probe.unlink()


def test_a_wrong_case_tasks_file_is_complained_about(tmp_path, monkeypatch, capsys):
    if not _folds_case(tmp_path):
        pytest.skip("the complaint needs a filesystem that folds case")
    # The walk declines to load `Tasks.py` — it opens here and vanishes on
    # the first Linux box. Declining *quietly* was the other half of the
    # bug. The complaint costs nothing: the walk probes, the probe hits
    # (this filesystem folds case), so the listing that confirms the
    # spelling is already in hand at the moment the mismatch is known.
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "Tasks.py").write_text("")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run(["hi"]) == EX_USAGE
    err = capsys.readouterr().err
    assert "Tasks.py" in err  # the spelling that is actually on disk
    assert "case-sensitive" in err
    assert "Rename it to tasks.py" in err


def test_the_complaint_survives_the_builtin_base_answering(
    tmp_path, monkeypatch, capsys
):
    if not _folds_case(tmp_path):
        pytest.skip("the complaint needs a filesystem that folds case")
    # The case the first attempt missed entirely: with built-ins mounted,
    # discovery *succeeds* — global mode answers — and the early return
    # skipped every not-found message. Someone running `fm --list` beside
    # their `Tasks.py` saw the built-ins and not one word about their file.
    # The complaint rides the walk, so it reaches every exit.
    (tmp_path / "Tasks.py").write_text("")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run(["--list"]) == 0
    assert "Tasks.py" in capsys.readouterr().err


def test_a_wrong_case_file_mid_cascade_is_complained_about(
    tmp_path, monkeypatch, capsys
):
    if not _folds_case(tmp_path):
        pytest.skip("the complaint needs a filesystem that folds case")
    # Not just the ends of the walk: checking only the cwd and the cascade
    # top missed exactly the monorepo case, where the file that is not
    # loading is a package's own, several levels down from either.
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text("from livery.footman import task\n")
    middle = tmp_path / "packages" / "web"
    deep = middle / "src"
    deep.mkdir(parents=True)
    (middle / "Tasks.py").write_text("")  # the one that will not load
    monkeypatch.chdir(deep)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run(["--list"]) == 0  # the root's tasks.py still loads
    err = capsys.readouterr().err
    assert str(middle / "Tasks.py") in err


def test_a_correctly_spelled_tasks_file_draws_no_complaint(
    tmp_path, monkeypatch, capsys
):
    # A name that is simply absent is not a case mistake, and a right-cased
    # file beside nothing else must stay silent.
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run(["hi"]) == EX_USAGE
    err = capsys.readouterr().err
    assert "Rename" not in err
    assert "create one or pass -f" in err


def test_the_not_found_message_names_the_case_rule(tmp_path, monkeypatch, capsys):
    # What a case-sensitive filesystem gets: the walk cannot see a variant
    # there without listing every level, so the message teaches the rule
    # instead — the exact name, and that the name is case-sensitive.
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run(["hi"]) == EX_USAGE
    err = capsys.readouterr().err
    assert "tasks.py (exactly — the name is case-sensitive)" in err


def test_missing_tasks_file_with_list_is_soft(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run(["--list"]) == 0
    assert "No tasks file found" in capsys.readouterr().out


def test_missing_tasks_file_with_help_shows_globals(tmp_path, monkeypatch, capsys):
    # F63: `fm --help` with no tasks file shows the globals (so a stuck newcomer
    # learns -f/-C), plus a where-did-I-look note — not a bare one-liner.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run(["--help"]) == 0
    out = capsys.readouterr().out
    assert "globals" in out and "-f" in out  # global help rendered
    assert "no tasks file found" in out  # with the note


def test_tree_output(project, capsys):
    assert _app.run(["--tree"]) == 0
    out = capsys.readouterr().out
    assert "tools." in out
    assert "echo" in out


def test_listings_split_project_from_global(tmp_path, monkeypatch, capsys):
    # Two sections, project first: a person's own tasks are what they came
    # for, and the global rung — built-ins and the personal tasks file —
    # is the backdrop that rides everywhere.
    config = tmp_path / "config" / "footman"
    config.mkdir(parents=True)
    (config / "tasks.py").write_text(
        "from livery.footman import task\n\n@task\ndef scratch():\n    'Mine, everywhere.'\n"
    )
    monkeypatch.setenv("FOOTMAN_CONFIG_DIR", str(config))
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname='x'\n")
    (proj / "tasks.py").write_text(
        "from livery.footman import task\n\n@task\ndef build():\n    'Build it.'\n"
    )
    monkeypatch.chdir(proj)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run(["--list"]) == 0
    out = capsys.readouterr().out
    assert "Tasks:" in out and "Global tasks:" in out
    assert out.index("Tasks:") < out.index("Global tasks:")  # project leads
    project_half, global_half = out.split("Global tasks:")
    assert "build" in project_half and "build" not in global_half
    assert "scratch" in global_half and "scratch" not in project_half
    # --tree splits the same way, from the same sifting.
    assert _app.run(["--tree"]) == 0
    tree_out = capsys.readouterr().out
    assert tree_out.index("Tasks:") < tree_out.index("Global tasks:")


def test_one_section_is_not_labelled_as_one_of_two(tmp_path, monkeypatch, capsys):
    # With nothing on the global rung there is nothing to contrast with, so
    # the listing says `Tasks:` rather than announcing a split that is not
    # there — and never prints a heading over blank space.
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(
        "from livery.footman import task\n\n@task\ndef build():\n    'Build it.'\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run(["--list"]) == 0
    out = capsys.readouterr().out
    assert "Tasks:" in out and "build" in out
    assert "Global tasks:" not in out


def test_a_group_straddling_the_split_heads_both_sections(
    tmp_path, monkeypatch, capsys
):
    # A group is not owned by one side: a personal `docs.notes` and a
    # project's `docs.build` share the name, so the group heads a branch in
    # each section carrying only its own children.
    config = tmp_path / "config"
    (config / "footman").mkdir(parents=True)
    (config / "footman" / "tasks.py").write_text(
        "from livery.footman import group\n\n"
        "docs = group('docs')\n\n"
        "@docs.task\ndef notes():\n    'Personal notes.'\n"
    )
    monkeypatch.setenv("FOOTMAN_CONFIG_DIR", str(config / "footman"))
    (tmp_path / "proj").mkdir()
    (tmp_path / "proj" / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "proj" / "tasks.py").write_text(
        "from livery.footman import group\n\n"
        "docs = group('docs')\n\n"
        "@docs.task\ndef build():\n    'Build the docs.'\n"
    )
    monkeypatch.chdir(tmp_path / "proj")
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run(["--list"]) == 0
    project_half, global_half = capsys.readouterr().out.split("Global tasks:")
    assert "docs.build" in project_half and "docs.notes" not in project_half
    assert "docs.notes" in global_half and "docs.build" not in global_half


def test_timings(project, capsys):
    assert _app.run(["--timings", "hi"]) == 0
    assert "ms)" in capsys.readouterr().err  # the summary is stderr commentary


def test_quiet_suppresses_summary(project, capsys):
    assert _app.run(["--quiet", "hi"]) == 0
    captured = capsys.readouterr()
    assert "hello world" in captured.out  # task output still streams
    assert "ok   hi" not in captured.err  # but the summary line is suppressed


def test_summary_is_commentary_stdout_is_the_answer(project, capsys):
    # The contract behind `fm task > file`: stdout carries exactly what the
    # task produced; the ok/FAIL summary is stderr commentary.
    assert _app.run(["hi"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "hello world\n"
    assert "ok   hi" in captured.err


def test_help_synthesises_an_example(project, capsys):
    # 11.3: --help shows a realistic invocation derived from the signature.
    assert _app.run(["--help", "add"]) == 0
    assert "Example: fm add <a> <b>" in capsys.readouterr().out
    assert _app.run(["--help", "flag"]) == 0
    assert "Example: fm flag --fix" in capsys.readouterr().out  # representative flag


def test_help_example_no_arg_task_has_no_junk(project, capsys):
    assert _app.run(["--help", "crash"]) == 0  # crash() takes no arguments
    examples = [
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("Example:")
    ]
    assert examples == ["Example: fm crash"]


def test_binding_refusals_exit_usage_end_to_end(tmp_path, monkeypatch):
    # F54: a coercion refusal (custom type) and a bounds refusal both surface as
    # exit EX_USAGE through the real CLI path — not a task-failure 1.
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n')
    (tmp_path / "tasks.py").write_text(
        "import uuid\n"
        "from typing import Annotated\n"
        "from livery.footman import task\n"
        "from livery.footman.params import between, env\n"
        "@task\n"
        "def ident(id: uuid.UUID): ...\n"
        "@task\n"
        "def bounded(n: Annotated[int, between(1, 10), env('N')] = 4): ...\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run(["ident", "not-a-uuid"]) == EX_USAGE  # UUID coercion refusal
    monkeypatch.setenv("N", "99")
    assert _app.run(["bounded"]) == EX_USAGE  # env value out of bounds


def test_install_completion_unknown_shell_teaches(project, capsys):
    assert _app.run(["--install-completion=tcsh"]) == EX_USAGE
    assert "bash|zsh|fish" in capsys.readouterr().err


def test_install_completion_detached_value_teaches_the_equals_form(project, capsys):
    # `--install-completion zsh`: the word never attached, and acting on the
    # detected shell instead would install a hook for the wrong shell.
    assert _app.run(["--install-completion", "zsh"]) == EX_USAGE
    assert "--install-completion=zsh" in capsys.readouterr().err


def test_directory_bad(project, capsys):
    assert _app.run([f"-C={project / 'nope'}", "hi"]) == EX_USAGE
    assert "-C" in capsys.readouterr().err


def test_tasks_file_does_not_poison_completion_cache(project):
    # F37: an -f run loads one file; it must not rewrite the cwd's completion
    # manifest (which describes the real cascade), or TAB breaks until the next
    # plain run.
    from pathlib import Path

    assert _app.run(["hi"]) == 0  # plain run writes the cascade's manifest
    cache = _paths.manifest_path(Path.cwd())
    before = cache.read_text()
    assert "hi" in before

    other = project / "other.py"
    other.write_text("from livery.footman import task\n@task\ndef solo(): ...\n")
    assert _app.run([f"-f={other}", "solo"]) == 0
    after = cache.read_text()
    assert after == before  # cache untouched
    assert "solo" not in after


def test_an_unwritable_cache_does_not_break_the_run(project, monkeypatch, capsys):
    # H41: a read-only HOME (a locked-down image, a stray chmod) used to turn
    # every command into a 47-line traceback, because the manifest rewrite ran
    # unguarded on the execution path. The write only makes the next TAB fast;
    # the run already holds the tree it needs.
    def denied(manifest, path):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(_manifest, "write_manifest", denied)

    assert _app.run(["hi", "--name=footman"]) == 0
    assert "hello footman" in capsys.readouterr().out
    assert _app.run(["--help"]) == 0
    assert "hi" in capsys.readouterr().out


def test_directory_restores_cwd(project):
    # F36: -C must not permanently move the host process (e.g. a test runner).
    import os

    sub = project / "sub"
    sub.mkdir()
    (sub / "tasks.py").write_text(
        "from livery.footman import task\n@task\ndef t(): ...\n"
    )
    before = os.getcwd()
    assert _app.run([f"-C={sub}", "t"]) == 0
    assert os.getcwd() == before


def test_unknown_global(project, capsys):
    assert _app.run(["--nope"]) == EX_USAGE
    assert "unknown global option" in capsys.readouterr().err


def test_a_short_option_wearing_its_value_is_taught(project, capsys):
    # `make -j4` muscle memory. The spaced form (`-j 4`) already teaches the
    # attached spelling; the glued one now gets the same sentence, so one
    # canonical form is taught from whichever way a hand reaches for it.
    assert _app.run(["-j1", "hi"]) == EX_USAGE
    err = capsys.readouterr().err
    assert "-j takes its value attached" in err and "did you mean -j=1?" in err


def test_combined_shorts_are_taught_apart(project, capsys):
    assert _app.run(["-sq", "hi"]) == EX_USAGE
    err = capsys.readouterr().err
    assert "combines short options" in err and "write them apart: -s -q" in err


def test_a_genuine_short_typo_stays_unknown(project, capsys):
    # Not every dash token is one of those two habits — a real typo keeps
    # the plain answer instead of being bent into a suggestion.
    assert _app.run(["-zzq", "hi"]) == EX_USAGE
    assert "unknown global option -zz" in capsys.readouterr().err


def test_passthrough_without_varargs_is_accepted(project, capsys):
    assert _app.run(["hi", "--", "x"]) == 0  # available via passthrough(), not an error
    assert "hello world" in capsys.readouterr().out


def test_where_unknown_suggests(project, capsys):
    # 11.1: --where routes its not-found through the same _did_you_mean helper.
    assert _app.run(["--where=hii"]) == EX_USAGE
    assert "did you mean 'hi'?" in capsys.readouterr().err


def test_where_unknown(project, capsys):
    assert _app.run(["--where=nope"]) == EX_USAGE
    assert "unknown task" in capsys.readouterr().err


def test_keep_going_via_cli(project, capsys):
    assert _app.run(["-k", "boom", "hi"]) == 2
    assert "hello world" in capsys.readouterr().out  # hi ran despite boom failing


def test_dry_run_binds_real_values(project, capsys):
    # Rehearsed bodies run with their bound arguments — the parse is proven
    # by execution, not echoed back.
    assert (
        _app.run(["--dry-run", "flag", "--no-fix", "+", "tools.echo", "a", "--", "b"])
        == 0
    )
    out = capsys.readouterr().out
    assert "fix=False" in out
    assert "a" in out


def test_tasks_file_override(tmp_path, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    alt = tmp_path / "custom.py"
    alt.write_text(
        "from livery.footman import task\n\n@task\ndef only():\n    print('only-ran')\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run([f"-f={alt}", "only"]) == 0
    assert "only-ran" in capsys.readouterr().out


def test_config_tasks_file(tmp_path, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='x'\n[tool.footman]\ntasks = 'custom.py'\n"
    )
    (tmp_path / "custom.py").write_text(
        "from livery.footman import task\n\n@task\ndef only():\n    print('cfg-ran')\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run(["only"]) == 0
    assert "cfg-ran" in capsys.readouterr().out


def test_corrupt_pyproject_falls_back_to_default(project, capsys):
    (project / "pyproject.toml").write_text("this is : not valid toml [[[")
    assert _app.run(["hi"]) == 0  # config lookup fails gracefully, tasks.py used
    assert "hello world" in capsys.readouterr().out


def test_tasks_import_failure(tmp_path, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text("raise RuntimeError('boom on import')\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run(["hi"]) == EX_USAGE
    assert "failed to import" in capsys.readouterr().err


def test_exception_is_reported(project, capsys):
    assert _app.run(["crash"]) == 1
    err = capsys.readouterr().err
    assert "RuntimeError" in err and "kaboom" in err


def test_dry_run_shows_true_flag(project, capsys):
    assert _app.run(["--dry-run", "flag", "--fix"]) == 0
    assert "fix=True" in capsys.readouterr().out


def test_empty_task_list(tmp_path, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text("# no tasks here\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run([]) == 0
    out = capsys.readouterr().out
    assert "No tasks defined" in out
    assert "--help <task>" not in out  # no tasks to get help on — no footer


# --- --help ------------------------------------------------------------------


def test_help_alone_lists_tasks(project, capsys):
    assert _app.run(["--help"]) == 0
    out = capsys.readouterr().out
    assert "hi" in out and "tools.echo" in out


def test_help_with_task_shows_usage_without_executing(project, capsys):
    assert _app.run(["--help", "hi"]) == 0
    out = capsys.readouterr().out
    assert "usage: fm hi [--name=VALUE]" in out
    assert "Say hello." in out
    assert "hello world" not in out  # the task did not run


def test_help_never_runs_the_chain(project, capsys):
    # `boom` exits 2 when executed; help over it must be a read-only act.
    assert _app.run(["--help", "boom"]) == 0
    assert "Fail on purpose." in capsys.readouterr().out


def test_help_shows_param_doc(project, capsys):
    # A doc("...") marker leads the option's detail line; mechanics follow —
    # and the marker beats the docstring's Args entry for the same param.
    assert _app.run(["--help", "fix"]) == 0
    out = capsys.readouterr().out
    assert "plan only, change nothing; flag (--no-dry to disable)" in out
    assert "docstring text" not in out


def test_help_shows_long_description_and_docstring_doc(project, capsys):
    assert _app.run(["--help", "sync"]) == 0
    out = capsys.readouterr().out
    assert "Sync the things." in out
    assert "Runs the whole pipeline," in out and "twice if needed." in out
    assert "skip the freshness check" in out  # docstring-sourced option line
    assert "Args:" not in out  # the section header is structure, not prose


def test_a_bare_valued_global_runs_on_its_default(project, capsys):
    # The splitter accepted `--jobs` bare and `_app` then parsed `''` as a
    # width and refused with exit 64 — the grammar and the runner holding two
    # ideas of what a bare mention means.
    assert _app.run(["--jobs", "hi"]) == 0
    assert "hello world" in capsys.readouterr().out
    assert _app.run(["--color", "hi"]) == 0
    assert "hello world" in capsys.readouterr().out


def test_one_source_drives_both_the_help_and_the_run(project, capsys, monkeypatch):
    from livery.footman import _progress

    monkeypatch.setattr(_progress, "default_jobs", lambda: 7)
    assert _app.run(["--help"]) == 0
    jobs_line = next(
        line for line in capsys.readouterr().out.splitlines() if "--jobs=N" in line
    )
    assert "default: 7 (computed)" in jobs_line

    seen: dict[str, object] = {}
    monkeypatch.setattr(
        _app._schedule, "run_plan", lambda *a, **k: seen.update(k) or []
    )
    _app.run(["hi"])
    # The number help printed is the number the run caps at — one value, so a
    # page cannot describe a run that never happens.
    assert seen.get("jobs") == 7


def test_global_help_marks_a_computed_default(project, capsys, monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    assert _app.run(["--help"]) == 0
    out = capsys.readouterr().out
    # A bare number reads as an arbitrary constant; it is this machine's cores
    # minus one, and a reader who copies it should know that. Colour's default
    # is computed too — it reads NO_COLOR/FORCE_COLOR — so with a clean
    # environment it answers auto, and says it worked for the answer. Search
    # the whole page, not one line: a narrow terminal wraps the suffix onto
    # the row's continuation line.
    jobs = out[out.index("--jobs=N") : out.index("--yes")]
    assert "(computed)" in jobs
    assert "default: auto (computed)" in out


def test_global_help_fits_the_terminal(project, capsys, monkeypatch):
    # Seven lines used to overflow 80 columns and hard-wrap in the terminal,
    # shearing the option column apart. Every row now wraps to the reported
    # width with a hanging indent.
    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.setenv("LINES", "24")
    assert _app.run(["--help"]) == 0
    lines = capsys.readouterr().out.splitlines()
    from livery.footman import _describe

    wide = [ln for ln in lines if _describe.display_width(ln) > 80]
    assert wide == []


def test_a_global_that_must_be_given_a_value_shows_no_default(project, capsys):
    assert _app.run(["--help"]) == 0
    lines = capsys.readouterr().out.splitlines()
    where = next(line for line in lines if "--where=TASK" in line)
    assert "default" not in where  # there is no default task to point at


def test_help_shows_the_declared_default(project, capsys):
    # The manifest carried `default` all along and help never printed it, so a
    # reader had to run the task to find out what `--name` would be.
    assert _app.run(["--help", "hi"]) == 0
    assert "default: world" in capsys.readouterr().out


def test_help_leads_with_the_spelling_that_does_something(project, capsys):
    # `--dry` defaults false, so the useful spelling is the positive one.
    assert _app.run(["--help", "fix"]) == 0
    out = capsys.readouterr().out
    assert "--dry " in out
    assert "(--no-dry to disable)" in out
    # A flag's default is said by the label and the parenthetical; saying
    # "default: false" as well would be the same fact three times.
    assert "default: false" not in out


def test_help_leads_a_default_true_flag_with_its_negative(project, capsys):
    # `--cache` defaults true, so typing it changes nothing and `--no-cache` is
    # the only spelling that does. Leading with the inert one buried the useful
    # one in a parenthetical.
    assert _app.run(["--help", "publish"]) == 0
    out = capsys.readouterr().out
    assert "--no-cache" in out
    assert "(--cache to enable)" in out


def test_help_shows_positionals_and_types(project, capsys):
    assert _app.run(["--help", "add"]) == 0
    out = capsys.readouterr().out
    assert "<a>" in out and "<b>" in out
    assert "an integer" in out


def test_help_unknown_target_refuses(project, capsys):
    # `--help nonexistnt` used to degrade to the global listing with exit 0 —
    # the one place the error discipline leaked. Now: a taught refusal.
    assert _app.run(["--help", "nope"]) == EX_USAGE
    err = capsys.readouterr().err
    assert "unknown task or group 'nope'" in err


def test_help_unknown_target_suggests(project, capsys):
    assert _app.run(["--help", "hii"]) == EX_USAGE
    assert "did you mean 'hi'?" in capsys.readouterr().err


def test_help_unknown_target_suggests_groups(project, capsys):
    assert _app.run(["--help", "tols"]) == EX_USAGE
    assert "did you mean 'tools'?" in capsys.readouterr().err


def test_help_with_target_tolerates_arg_tokens(project, capsys):
    # A help line carries task arguments; once a real target is found, extra
    # bare words stay lenient — they are values, not typos.
    assert _app.run(["--help", "add", "junk", "--flag"]) == 0
    assert "usage: fm add" in capsys.readouterr().out


def test_help_takes_the_dotted_address(project, capsys):
    # The one address spelling: `--help` resolves the same dotted token the
    # run grammar does.
    assert _app.run(["--help", "tools.echo"]) == 0
    out = capsys.readouterr().out
    assert "usage: fm tools.echo" in out
    assert "Echo words." in out


def test_help_space_path_is_taught_dotted(project, capsys):
    # `fm --help "tools echo"` — the space form is no longer an address, and
    # the refusal suggests the dotted spelling instead of shrugging.
    assert _app.run(["--help", "tools echo"]) == EX_USAGE
    err = capsys.readouterr().err
    assert "unknown task or group 'tools echo'" in err
    assert "did you mean 'tools.echo'?" in err


def test_help_resolves_before_a_passthrough_boundary(project, capsys):
    # Address resolution stops at `--`: a target before it still resolves,
    # and the tokens after are left verbatim (never counted as strays).
    assert _app.run(["--help", "tools.echo", "--", "raw arg"]) == 0
    assert "usage: fm tools.echo" in capsys.readouterr().out


def test_help_unknown_address_suggests_a_real_neighbour(project, capsys):
    assert _app.run(["--help", "tools.ecko"]) == EX_USAGE
    err = capsys.readouterr().err
    assert "unknown task or group 'tools.ecko'" in err
    assert "did you mean 'tools.echo'?" in err


def test_help_alone_shows_the_global_options(project, capsys):
    assert _app.run(["--help"]) == 0
    out = capsys.readouterr().out
    assert "usage: fm [globals]" in out
    assert "--dry-run" in out and "--keep-going" in out


def test_help_anywhere_on_the_line_wins(project, capsys):
    # `fm boom --help` must be help, not an execution of `boom` (a refusal) and
    # not an "unknown option" error.
    assert _app.run(["boom", "--help"]) == 0
    assert "Fail on purpose." in capsys.readouterr().out
    assert _app.run(["hi", "-h"]) == 0
    assert "usage: fm hi" in capsys.readouterr().out


def test_help_after_passthrough_is_passthrough(project, capsys):
    # After `--` the token belongs to the task, not to fm.
    assert _app.run(["tools.echo", "--", "--help"]) == 0
    assert "--help" in capsys.readouterr().out


def test_help_for_a_group(project, capsys):
    assert _app.run(["--help", "tools"]) == 0
    out = capsys.readouterr().out
    assert "usage: fm tools.<task>" in out
    assert "Extra tools" in out
    assert "tools.echo" in out


# --- import failures name the culprit ----------------------------------------


def test_tasks_import_failure_names_the_file(tmp_path, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text("raise RuntimeError('boom on import')\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run(["hi"]) == EX_USAGE
    err = capsys.readouterr().err
    assert "failed to import" in err and "tasks.py" in err


def test_tasks_syntax_error_reported_cleanly(tmp_path, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text("def broken(:\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run(["hi"]) == EX_USAGE
    err = capsys.readouterr().err
    assert "SyntaxError" in err and "tasks.py" in err


def test_duplicate_task_name_is_a_user_error(tmp_path, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(
        "from livery.footman import task\n"
        "@task\n"
        "def build(): ...\n"
        "@task(name='build')\n"
        "def build2(): ...\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run(["build"]) == EX_USAGE
    err = capsys.readouterr().err
    assert "already has a task named 'build'" in err and "tasks.py" in err
    assert "failed to import" not in err  # a duplicate name, not a crash


# --- config errors are loud ---------------------------------------------------


def test_malformed_cascade_config_warns_and_continues(project, capsys):
    (project / "footman.toml").write_text("this is = not [valid toml\n")
    assert _app.run(["hi"]) == 0
    captured = capsys.readouterr()
    assert "hello world" in captured.out
    assert "ignoring malformed config" in captured.err
    assert "footman.toml" in captured.err


def test_malformed_explicit_config_is_an_error(project, capsys):
    (project / "bad.toml").write_text("this is = not [valid toml\n")
    assert _app.run(["--config", "bad.toml", "hi"]) == EX_USAGE
    err = capsys.readouterr().err
    assert "--config" in err and "bad.toml" in err


def test_non_utf8_cascade_config_warns_and_continues(project, capsys):
    # One non-UTF-8 byte used to reach the user as a raw UnicodeDecodeError
    # and exit 1 — `fm hi` included, so the whole CLI was bricked while that
    # file sat between the repo root and the cwd. It is a malformed config
    # like any other now: warned about, skipped, and the task still runs.
    (project / "footman.toml").write_bytes(b"# caf\xe9\nsort = true\n")
    assert _app.run(["hi"]) == 0
    captured = capsys.readouterr()
    assert "hello world" in captured.out
    assert "ignoring malformed config" in captured.err
    assert "not valid UTF-8" in captured.err and "re-save" in captured.err


def test_non_utf8_explicit_config_is_an_error(project, capsys):
    (project / "bad.toml").write_bytes(b"# caf\xe9\nsort = true\n")
    assert _app.run(["--config=bad.toml", "hi"]) == EX_USAGE
    err = capsys.readouterr().err
    assert "--config" in err and "bad.toml" in err
    assert "not valid UTF-8" in err


def test_utf8_bom_config_is_read_not_refused(project, capsys):
    # A Windows editor's byte-order mark is stripped, so the settings behind
    # it actually apply: `sort` reorders the listing away from file order.
    (project / "footman.toml").write_bytes("sort = true\n".encode("utf-8-sig"))
    assert _app.run(["--list"]) == 0
    captured = capsys.readouterr()
    assert "malformed" not in captured.err
    listed = [ln.split()[0] for ln in captured.out.splitlines() if ln.startswith("  ")]
    assert listed[0] == "add"  # alphabetical; definition order opens with `hi`


def test_missing_explicit_config_is_an_error(project, capsys):
    # F15: a typo'd --config (prod.tmol) must be loud, not silently ignored.
    assert _app.run(["--config=prod.tmol", "hi"]) == EX_USAGE
    err = capsys.readouterr().err
    assert "--config" in err and "no such file" in err and "prod.tmol" in err


# --- Ctrl-C and GeneratorExit: the exits the runner does not own --------------


def test_keyboard_interrupt_exits_130(tmp_path, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(
        "from livery.footman import task\n@task\ndef stop():\n    raise KeyboardInterrupt\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run(["stop"]) == 130
    assert "interrupted" in capsys.readouterr().err
    assert _app.run(["--sequential", "stop"]) == 130
    assert "interrupted" in capsys.readouterr().err


def test_a_generator_exit_is_not_a_task_failure(tmp_path, monkeypatch):
    # The one other name the body's failure catch lets through. It is the
    # interpreter tearing a frame down — footman's own step pump closes
    # generators to cancel them — and a frame that swallows the exit is handed
    # a RuntimeError in place of the exit it refused. So it leaves by the front
    # door like an interrupt, rather than becoming a receipt.
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(
        "from livery.footman import task\n@task\ndef stop():\n    raise GeneratorExit\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    with pytest.raises(GeneratorExit):
        _app.run(["stop"])


# --- the timing story: recording, the eta line, the off switches --------------


def _hi_key(values: dict[str, object] | None = None) -> str:
    seg = Segment(task="hi", path=["hi"], values=values or {})
    return _progress.chain_key([seg], sequential=False, jobs=_progress.default_jobs())


def test_green_runs_record_history(project):
    assert _app.run(["hi"]) == 0
    assert _app.run(["hi"]) == 0
    assert len(_progress.load_runs(project, _hi_key())) == 2


def test_failed_and_dry_runs_record_nothing(project):
    assert _app.run(["boom"]) == 2
    assert _app.run(["--dry-run", "hi"]) == 0
    import json as _json

    times = _paths.times_path(project)
    assert not times.exists() or _json.loads(times.read_text())["chains"] == {}


def test_json_runs_record_too(project, capsys):
    # CI teaches: capture mode never displays, but green runs still count.
    assert _app.run(["--json", "hi"]) == 0
    assert len(_progress.load_runs(project, _hi_key())) == 1


def test_eta_line_prints_without_a_tty(project, capsys):
    for _ in range(5):
        _progress.record(project, _hi_key(), 4.0)
    assert _app.run(["hi"]) == 0
    err = capsys.readouterr().err
    assert "eta" in err and "~4.0s" in err  # the NO_COLOR version, up front


def test_no_progress_flag_turns_it_all_off(project, capsys):
    for _ in range(5):
        _progress.record(project, _hi_key(), 4.0)
    assert _app.run(["--no-progress", "hi"]) == 0
    assert "eta" not in capsys.readouterr().err
    assert len(_progress.load_runs(project, _hi_key())) == 5  # not recorded


def test_config_progress_false_turns_it_off_permanently(project, capsys):
    (project / "pyproject.toml").write_text(
        "[project]\nname='x'\n[tool.footman]\nprogress = false\n"
    )
    for _ in range(5):
        _progress.record(project, _hi_key(), 4.0)
    assert _app.run(["hi"]) == 0
    assert "eta" not in capsys.readouterr().err
    assert len(_progress.load_runs(project, _hi_key())) == 5


def test_jobs_flag_validates_and_runs(project, capsys):
    # The parameter pipeline's own taught refusals — `between(1, None)` for
    # the floor, coercion for the type — not hand-rolled ones.
    assert _app.run(["--jobs=0", "hi"]) == EX_USAGE
    assert "--jobs must be at least 1" in capsys.readouterr().err
    assert _app.run(["-j=abc", "hi"]) == EX_USAGE
    assert "--jobs expects an integer" in capsys.readouterr().err
    assert _app.run(["-j=2", "hi"]) == 0
    assert "hello world" in capsys.readouterr().out


def test_config_sets_the_width_and_the_line_outranks_it(project, monkeypatch):
    (project / "pyproject.toml").write_text(
        "[project]\nname='x'\n[tool.footman]\njobs = 2\n"
    )
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        _app._schedule, "run_plan", lambda *a, **k: seen.update(k) or []
    )
    assert _app.run(["hi"]) == 0
    assert seen.get("jobs") == 2  # the config rung, coerced and bounded
    assert _app.run(["-j=3", "hi"]) == 0
    assert seen.get("jobs") == 3  # the line outranks config


def test_a_broken_config_width_teaches_even_when_the_line_decides(project, capsys):
    # The sort rule, generalised to the whole ladder: a present config key is
    # validated on every invocation, not only the ones it would steer.
    (project / "pyproject.toml").write_text(
        "[project]\nname='x'\n[tool.footman]\njobs = 0\n"
    )
    assert _app.run(["hi"]) == EX_USAGE
    assert "config key 'jobs' must be at least 1" in capsys.readouterr().err
    assert _app.run(["-j=2", "hi"]) == EX_USAGE
    assert "config key 'jobs'" in capsys.readouterr().err


def test_config_sets_the_colour_and_the_line_outranks_it(project, capsys):
    (project / "pyproject.toml").write_text(
        "[project]\nname='x'\n[tool.footman]\ncolor = 'always'\n"
    )
    assert _app.run(["--list"]) == 0
    assert "\033" in capsys.readouterr().out  # painted though not a tty
    assert _app.run(["--color=never", "--list"]) == 0
    assert "\033" not in capsys.readouterr().out
    (project / "pyproject.toml").write_text(
        "[project]\nname='x'\n[tool.footman]\ncolor = 'sepia'\n"
    )
    assert _app.run(["--list"]) == EX_USAGE
    assert "config key 'color' must be one of" in capsys.readouterr().err


def test_config_goes_sequential_and_the_negation_undoes_it(project, monkeypatch):
    (project / "pyproject.toml").write_text(
        "[project]\nname='x'\n[tool.footman]\nsequential = true\n"
    )
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        _app._schedule, "run_plan", lambda *a, **k: seen.update(k) or []
    )
    assert _app.run(["hi"]) == 0
    assert seen.get("sequential") is True
    assert _app.run(["--no-sequential", "hi"]) == 0
    assert seen.get("sequential") is False  # `--no-x` countermands config


def test_jobs_changes_the_timing_key(project):
    # A 3-core CI runner's default width IS 2 — pick one that can't collide.
    other = _progress.default_jobs() + 1
    assert _app.run([f"-j={other}", "hi"]) == 0
    assert _progress.load_runs(project, _hi_key()) == []  # default-width key
    keyed = _progress.chain_key(
        [Segment(task="hi", path=["hi"])], sequential=False, jobs=other
    )
    assert len(_progress.load_runs(project, keyed)) == 1


def test_progress_false_task_opts_the_run_out(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(
        "from livery.footman import task\n"
        "@task(progress=False)\n"
        "def odd():\n"
        '    "No rhyme nor reason to its duration."\n'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run(["odd"]) == 0
    assert not _paths.times_path(tmp_path).exists()  # never recorded


# --- the one-envelope contract: --json ⇒ stdout is one JSON document ----------


def test_json_refusal_envelope(project, capsys):
    # A pre-run refusal used to leave stdout empty in --json mode; now the
    # taught error lands in both channels — text for humans, one envelope
    # for machines.
    assert _app.run(["--json", "nope"]) == EX_USAGE
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["schema"] == 1
    assert payload["error"]["code"] == EX_USAGE
    assert "no task named" in payload["error"]["message"]
    assert payload["items"] == []
    assert "no task named" in captured.err  # stderr keeps the human copy


def test_json_refusal_on_unknown_global(project, capsys):
    # The parse fails *at* --nope, but --json already promised an envelope.
    assert _app.run(["--json", "--nope", "hi"]) == EX_USAGE
    payload = json.loads(capsys.readouterr().out)
    assert "unknown global option" in payload["error"]["message"]


def test_json_refusal_on_import_failure(tmp_path, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text("raise RuntimeError('boom on import')\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run(["--json", "hi"]) == EX_USAGE
    payload = json.loads(capsys.readouterr().out)
    assert "failed to import" in payload["error"]["message"]


def test_json_help_refusal_still_envelopes(project, capsys):
    # Help's *success* output is the one human-only surface; its refusal is a
    # refusal like any other and honours the envelope.
    assert _app.run(["--json", "--help", "nope"]) == EX_USAGE
    payload = json.loads(capsys.readouterr().out)
    assert "unknown task or group 'nope'" in payload["error"]["message"]


def test_json_version(project, capsys):
    from livery.footman import __version__

    assert _app.run(["--json", "--version"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"schema": 1, "name": "footman", "version": __version__}


def test_json_step_rows_redact_a_secret_argument(tmp_path):
    """SECURITY.md puts `--json` output in scope by name. A document that
    leaves the process is a display: the command, the name footman minted
    for it, and the audit's actor all show `***`, while the record inside
    the run keeps the real line for `recording()` and dependents."""
    from livery.footman.testing import Runner

    (tmp_path / "tasks.py").write_text(
        "from livery.footman import run, task\n"
        "from livery.footman.params import Secret\n"
        "\n"
        "@task\n"
        "def login():\n"
        "    run(['python', '-c', 'pass', Secret('hunter2')])\n"
    )
    result = Runner().invoke("--json login", cwd=tmp_path)
    assert result.ok, result.stderr
    assert "hunter2" not in result.stdout
    (step_row,) = [i for i in json.loads(result.stdout)["items"] if "command" in i]
    assert step_row["command"] == "python -c pass ***"
    assert step_row["address"] == "login/python"
    assert step_row["audit"] == [["body", "python -c pass ***", 0]]


def test_json_list_emits_tree(project, capsys):
    assert _app.run(["--json", "--list"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == 1
    assert "hi" in payload["tree"]["tasks"]
    assert "echo" in payload["tree"]["groups"]["tools"]["tasks"]
    (param,) = payload["tree"]["tasks"]["hi"]["params"]
    assert param["name"] == "name" and param["kind"] == "option"


def test_json_bare_emits_tree(project, capsys):
    # An agent's first call: bare `fm --json` is the whole catalog.
    assert _app.run(["--json"]) == 0
    assert "hi" in json.loads(capsys.readouterr().out)["tree"]["tasks"]


def test_json_no_tasks_file(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run(["--json"]) == 0  # warm empty state: an honest empty tree
    payload = json.loads(capsys.readouterr().out)
    assert payload["tree"]["tasks"] == {} and payload["tree"]["groups"] == {}
    assert _app.run(["--json", "hi"]) == EX_USAGE  # a named task still refuses
    payload = json.loads(capsys.readouterr().out)
    assert "no tasks file found" in payload["error"]["message"]


def test_json_dry_run_emits_the_report_envelope(project, capsys):
    # One report, one shape: a rehearsal answers in the same items envelope
    # a real run does — there is no separate plan schema to consume.
    line = ["--json", "-n", "hi", "--name=x", "tools.echo", "a", "--", "b"]
    assert _app.run(line) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == 1
    tasks = [i["task"] for i in payload["items"] if "task" in i]
    assert tasks == ["hi", "tools.echo"]
    assert all(i["ok"] for i in payload["items"])


def test_json_interrupt_envelope(tmp_path, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(
        "from livery.footman import task\n@task\ndef stop():\n    raise KeyboardInterrupt\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run(["--json", "stop"]) == 130
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == {"code": 130, "message": "interrupted"}


# --- returned: a task's return value in the envelope --------------------------


def test_json_returned_value(project, capsys):
    assert _app.run(["--json", "data"]) == 0
    entry = json.loads(capsys.readouterr().out)["items"][0]
    assert entry["ok"] is True
    assert entry["returned"] == {"n": 1, "flags": [True, False]}


def test_json_none_return_omits_key(project, capsys):
    assert _app.run(["--json", "hi"]) == 0
    entry = json.loads(capsys.readouterr().out)["items"][0]
    assert "returned" not in entry and "returned_error" not in entry


def test_json_int_return_is_exit_code_not_data(project, capsys):
    # An int return is the exit-code channel (duty's contract); it never
    # doubles as a returned payload.
    assert _app.run(["--json", "code3"]) == 3
    entry = json.loads(capsys.readouterr().out)["items"][0]
    assert entry["code"] == 3
    assert "returned" not in entry


def test_json_unserialisable_return_teaches(project, capsys):
    # The task succeeded; the payload alone is refused — machine-visibly in
    # the entry, human-visibly on stderr, and the exit code stays the task's.
    assert _app.run(["--json", "opaque"]) == 0
    captured = capsys.readouterr()
    entry = json.loads(captured.out)["items"][0]
    assert entry["ok"] is True
    assert "returned" not in entry
    assert "not JSON-serialisable" in entry["returned_error"]
    assert "not JSON-serialisable" in captured.err


def test_json_returned_mirrors_coercion_types(tmp_path, monkeypatch, capsys):
    # The types footman coerces *in* serialise on the way *out*: Path, Enum,
    # date, UUID, Decimal, dataclass, set.
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(
        "import dataclasses, datetime, decimal, enum, pathlib, uuid\n"
        "from livery.footman import task\n"
        "class Colour(enum.Enum):\n"
        "    RED = 'red'\n"
        "@dataclasses.dataclass\n"
        "class Point:\n"
        "    x: int\n"
        "    src: pathlib.Path\n"
        "@task\n"
        "def artefacts():\n"
        "    return {\n"
        "        'wheel': pathlib.Path('dist') / 'x.whl',\n"
        "        'colour': Colour.RED,\n"
        "        'when': datetime.date(2026, 7, 19),\n"
        "        'id': uuid.UUID('12345678-1234-5678-1234-567812345678'),\n"
        "        'price': decimal.Decimal('1.10'),\n"
        "        'tags': {'b', 'a'},\n"
        "        'point': Point(1, pathlib.Path('src')),\n"
        "    }\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    from pathlib import Path

    assert _app.run(["--json", "artefacts"]) == 0
    returned = json.loads(capsys.readouterr().out)["items"][0]["returned"]
    assert returned["wheel"] == str(Path("dist") / "x.whl")  # OS-native separator
    assert returned["colour"] == "red"
    assert returned["when"] == "2026-07-19"
    assert returned["id"] == "12345678-1234-5678-1234-567812345678"
    assert returned["price"] == "1.10"  # str, not float: precision kept
    assert returned["tags"] == ["a", "b"]  # sets come out sorted
    assert returned["point"] == {"x": 1, "src": "src"}  # dataclass, nested Path


# --- colour: one palette across the CLI ---------------------------------------
# Help, listings, plans, and errors paint when their own stream is a terminal —
# and only then. Piped output, NO_COLOR, and --no-color stay byte-clean; these
# pin both sides so escapes can never leak into captured output.


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def _tty_streams(monkeypatch):
    """Colour-eligible stdout/stderr fakes with a clean colour environment.

    Called inside the test body, not from a fixture: pytest's capture
    re-asserts its own sys.stdout/sys.stderr at the fixture→call phase
    boundary, so fixture-time stream patches silently vanish.
    """
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    out, err = _Tty(), _Tty()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    return out, err


def test_ansi_capable_gates_on_tty_and_windows_console(monkeypatch, tmp_path):
    import os

    from livery.footman import _describe

    # Not a tty: never capable, whatever the platform.
    assert not _describe.ansi_capable(io.StringIO())
    # A POSIX tty is an ANSI surface as-is.
    assert _describe.ansi_capable(_Tty())
    monkeypatch.setattr(os, "name", "nt")
    # A Windows tty without an OS handle keeps its own claim — that shape
    # is a wrapper (or a test fake) answering for its own rendering.
    assert _describe.ansi_capable(_Tty())

    # A handle-backed Windows tty must prove its console interprets escapes
    # (VT processing), or bold prints as `←[1m` noise. A handle with no
    # console behind it must refuse rather than paint.
    class _HandleTty(_Tty):
        def __init__(self, fd: int) -> None:
            super().__init__()
            self._fd = fd

        def fileno(self) -> int:
            return self._fd

    with open(tmp_path / "not-a-console", "w") as fh:
        assert not _describe.ansi_capable(_HandleTty(fh.fileno()))


def test_global_help_paints_on_a_tty(project, monkeypatch):
    out, _ = _tty_streams(monkeypatch)
    assert _app.run(["--help"]) == 0
    text = out.getvalue()
    assert "usage: \033[1mfm\033[0m" in text  # prog bold
    assert "\033[36m<task>\033[0m" in text  # required placeholder cyan
    assert "\033[1mglobals (before the first task):\033[0m" in text
    assert "\033[1m-l, --list\033[0m" in text  # option labels bold


def test_task_help_paints_the_command_line(project, monkeypatch):
    # The one CLI grammar: prog bold, groups bold cyan, task bold, and the
    # synthesised example painted with the same brush as the usage line.
    out, _ = _tty_streams(monkeypatch)
    assert _app.run(["--help", "tools.echo"]) == 0
    text = out.getvalue()
    assert "\033[1mfm\033[0m \033[1mtools.echo\033[0m" in text
    assert "\033[2mExample:\033[0m" in text


def test_list_and_tree_paint_names(project, monkeypatch):
    out, _ = _tty_streams(monkeypatch)
    assert _app.run(["--list"]) == 0
    assert _app.run(["--tree"]) == 0
    text = out.getvalue()
    assert "\033[2mtools.\033[0m\033[1mecho\033[0m" in text  # dim prefix, bold leaf
    assert "\033[1;36mtools.\033[0m" in text  # tree group


def test_error_prefix_is_red_on_a_tty(project, monkeypatch):
    _, err = _tty_streams(monkeypatch)
    assert _app.run(["nosuchtask"]) == EX_USAGE
    assert "\033[31mfm\033[0m:" in err.getvalue()


def test_no_color_flag_wins_even_on_a_tty(project, monkeypatch):
    out, _ = _tty_streams(monkeypatch)
    assert _app.run(["--no-color", "--list"]) == 0
    assert "\033" not in out.getvalue()


def test_resolve_color_precedence(monkeypatch):
    # CLI > --no-color > the bound ladder (config > declared default, which
    # reads NO_COLOR/FORCE_COLOR). This is the unbound half — the pre-run
    # paint, where config does not exist yet; the config rung is pinned end
    # to end in test_config_sets_the_colour_and_the_line_outranks_it.
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    assert _app._resolve_color({"color": "always"}) == "always"
    assert _app._resolve_color({"color": "always", "no_color": True}) == "always"
    assert _app._resolve_color({"no_color": True}) == "never"
    assert _app._resolve_color({}) == "auto"
    monkeypatch.setenv("NO_COLOR", "1")
    assert _app._resolve_color({}) == "never"
    assert _app._resolve_color({"color": "always"}) == "always"  # cli still wins
    monkeypatch.delenv("NO_COLOR")
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert _app._resolve_color({}) == "always"
    monkeypatch.setenv("FORCE_COLOR", "0")  # 0 disables — falls through to auto
    assert _app._resolve_color({}) == "auto"


def test_color_always_paints_when_piped(project, capsys):
    # The whole point of `always`: colour even though stdout is not a terminal
    # (a pipe into `less -R`). capsys' stdout fails isatty, yet the rehearsed
    # receipt paints.
    (project / "tasks.py").write_text(
        "from livery.footman import run, task\n@task\ndef ship():\n    run('touch x')\n"
    )
    assert _app.run(["--color=always", "-n", "ship"]) == 0
    out = capsys.readouterr().out
    assert "touch x" in out and "\033[" in out


def test_color_never_is_byte_clean_on_a_tty(project, monkeypatch):
    # `never` is the `--no-color` twin: no escapes even on a colour-eligible tty.
    out, _ = _tty_streams(monkeypatch)
    assert _app.run(["--color=never", "--list"]) == 0
    assert "\033" not in out.getvalue()


def test_color_rejects_an_unknown_value(project, capsys):
    assert _app.run(["--color=technicolor", "--list"]) == EX_USAGE
    assert "--color must be one of always|never|auto" in capsys.readouterr().err


def test_static_choice_options_name_their_set_once():
    # The set already lives in the option's own spelling (`--mode={dev|prod}`),
    # so the description carries it only where the spelling can't: a
    # positional's row, or a dynamic set worth marking as live.
    from livery.footman import _describe

    option = {"kind": "option", "name": "mode", "choices": ["dev", "prod"]}
    assert "one of" not in _describe._mechanics(option)
    positional = {"kind": "positional", "name": "mode", "choices": ["dev", "prod"]}
    assert "one of dev|prod" in _describe._mechanics(positional)
    dynamic = {
        "kind": "option",
        "name": "branch",
        "choices": ["main"],
        "dynamic": True,
    }
    assert "one of main (dynamic)" in _describe._mechanics(dynamic)


def test_force_color_env_paints_when_piped(project, monkeypatch, capsys):
    # FORCE_COLOR is the environment rung of `always`; NO_COLOR (higher, and the
    # never rung) still wins over it.
    (project / "tasks.py").write_text(
        "from livery.footman import run, task\n@task\ndef ship():\n    run('touch x')\n"
    )
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert _app.run(["-n", "ship"]) == 0
    assert "\033[" in capsys.readouterr().out
    monkeypatch.setenv("NO_COLOR", "1")
    assert _app.run(["-n", "ship"]) == 0
    assert "\033" not in capsys.readouterr().out


def test_piped_output_stays_plain(project, capsys):
    for line in (["--help"], ["--list"], ["--tree"], ["-n", "hi"]):
        assert _app.run(line) == 0
        assert "\033" not in capsys.readouterr().out


# --- the uv handoff -----------------------------------------------------------
# A globally-installed fm hands the invocation to the project's own footman
# via `uv run` when the project's uv.lock pins footman and we're outside its
# environment. These pin the rule's every edge: it fires with exactly the
# right argv, terminates, and stays out of the way everywhere else.

_UV_LOCK = 'version = 1\n\n[[package]]\nname = "livery-footman"\nversion = "0.13.0"\n'


@pytest.fixture
def uv_project(project, monkeypatch):
    (project / "uv.lock").write_text(_UV_LOCK, encoding="utf-8")
    monkeypatch.delenv("FOOTMAN_UV_REEXEC", raising=False)
    monkeypatch.delenv("FOOTMAN_NO_UV", raising=False)
    # The one lookup both handoffs use — footman's own environment first,
    # then PATH; faked here so no test depends on a real uv anywhere.
    monkeypatch.setattr(_app, "_find_uv", lambda: "/fake/uv")
    return project


def _capture_exec(monkeypatch):
    # Forces the POSIX branch so the exec args are observable on every OS —
    # a real Windows runner would otherwise spawn the fake uv for real. The
    # Windows waiter has its own test with _WINDOWS forced the other way.
    monkeypatch.setattr(_app, "_WINDOWS", False)
    calls: list[list[str]] = []

    def fake_execvp(file, args):
        calls.append(list(args))
        raise SystemExit(0)  # execvp never returns; stand in for the child

    monkeypatch.setattr(_app.os, "execvp", fake_execvp)
    return calls


def test_handoff_execs_the_projects_footman(uv_project, monkeypatch):
    calls = _capture_exec(monkeypatch)
    with pytest.raises(SystemExit):
        _app.run(["hi", "--name=x"])
    assert calls == [
        ["/fake/uv", "run", "--project", str(uv_project), "fm", "hi", "--name=x"]
    ]


def test_handoff_probes_the_dash_c_target_without_moving(
    uv_project, tmp_path_factory, monkeypatch
):
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    monkeypatch.chdir(elsewhere)
    calls = _capture_exec(monkeypatch)
    with pytest.raises(SystemExit):
        _app.run([f"-C={uv_project}", "hi"])
    (call,) = calls
    assert call[2:4] == ["--project", str(uv_project)]
    assert call[5:] == [f"-C={uv_project}", "hi"]  # original argv, verbatim
    assert Path.cwd() == elsewhere  # the child repeats -C; we never moved


def test_handoff_skips_every_optout(uv_project, monkeypatch, capsys):
    calls = _capture_exec(monkeypatch)
    for setup in (
        lambda: monkeypatch.setenv("FOOTMAN_UV_REEXEC", "1"),
        lambda: monkeypatch.setenv("FOOTMAN_NO_UV", "1"),
        lambda: (uv_project / "pyproject.toml").write_text(
            "[project]\nname='x'\n[tool.footman]\nuv = false\n"
        ),
    ):
        setup()
        assert _app.run(["hi"]) == 0  # ran here, in this process
        assert "hello world" in capsys.readouterr().out
        monkeypatch.delenv("FOOTMAN_UV_REEXEC", raising=False)
        monkeypatch.delenv("FOOTMAN_NO_UV", raising=False)
    assert calls == []


def test_handoff_never_fires_from_runner_invoke(uv_project, monkeypatch):
    # An embedded invocation must run in-process even when every handoff
    # condition holds: `Runner.invoke` drives a HOST process (pytest — under
    # pytest-xdist, a worker whose stdio is the test-protocol channel), and
    # the execvp would replace the host itself. Regression: Runner-based CLI
    # tests crashed every xdist worker whenever the suite ran under an
    # interpreter outside the project venv (e.g. `uv run --with pytest-xdist`).
    from livery.footman.testing import Runner

    calls = _capture_exec(monkeypatch)
    result = Runner().invoke("hi")
    assert result.ok
    assert "hello world" in result.stdout
    assert calls == []


def test_handoff_needs_footman_in_the_lock(uv_project, monkeypatch, capsys):
    calls = _capture_exec(monkeypatch)
    (uv_project / "uv.lock").write_text(
        'version = 1\n\n[[package]]\nname = "requests"\nversion = "2.0"\n'
    )
    assert _app.run(["hi"]) == 0
    assert "hello world" in capsys.readouterr().out
    assert calls == []


def test_handoff_stays_home_inside_the_projects_venv(uv_project, monkeypatch, capsys):
    calls = _capture_exec(monkeypatch)
    (uv_project / ".venv").mkdir()
    monkeypatch.setattr(_app.sys, "prefix", str(uv_project / ".venv"))
    assert _app.run(["hi"]) == 0
    assert "hello world" in capsys.readouterr().out
    assert calls == []


def test_handoff_never_touches_version(uv_project, monkeypatch, capsys):
    calls = _capture_exec(monkeypatch)
    assert _app.run(["--version"]) == 0
    assert "footman" in capsys.readouterr().out
    assert calls == []


def test_user_level_key_in_project_config_notes_under_verbose(project, capsys):
    (project / "pyproject.toml").write_text(
        "[project]\nname='x'\n[tool.footman]\ngc = false\n"
    )
    assert _app.run(["hi"]) == 0
    assert "user-level" not in capsys.readouterr().err  # quiet by default
    assert _app.run(["-v", "hi"]) == 0
    assert "user-level" in capsys.readouterr().err  # -v teaches the move


def test_handoff_windows_waits_and_carries_the_code(uv_project, monkeypatch):
    class FakeProc:
        def wait(self):
            return 7

    monkeypatch.setattr(_app, "_WINDOWS", True)
    monkeypatch.setattr(_app.subprocess, "Popen", lambda cmd: FakeProc())
    with pytest.raises(SystemExit) as excinfo:
        _app.run(["hi"])
    assert excinfo.value.code == 7


# --- the stale-environment retry ----------------------------------------------
# "Already home" trusts that the project's venv matches its lockfile. A
# dependency locked after the last sync breaks that trust, and the first
# symptom is a failed import — so the run retries through `uv run` (which
# syncs), or at least names the fix. These pin when it fires, with what argv,
# and every edge where it must stay silent instead.


@pytest.fixture
def stale_project(uv_project, monkeypatch):
    """A pinned project we are "already home" in, whose tasks fail to import
    the way a stale venv makes them fail."""
    (uv_project / "tasks.py").write_text(
        "import missing_module_xyz\n", encoding="utf-8"
    )
    (uv_project / ".venv").mkdir()
    monkeypatch.setattr(_app.sys, "prefix", str(uv_project / ".venv"))
    return uv_project


def test_a_failed_import_retries_through_uv_run(stale_project, monkeypatch):
    calls = _capture_exec(monkeypatch)
    with pytest.raises(SystemExit):
        _app.run(["--tree"])
    assert calls == [
        ["/fake/uv", "run", "--project", str(stale_project), "fm", "--tree"]
    ]
    import os

    assert os.environ["FOOTMAN_UV_REEXEC"] == "1"  # the loop belt rides along


def test_a_failed_plugin_import_retries_too(stale_project, monkeypatch):
    # The #530 shape: the tasks file imports fine, a mounted module doesn't.
    (stale_project / "helper.py").write_text("import missing_module_xyz\n")
    (stale_project / "tasks.py").write_text(
        "from livery.footman.compose import include\ninclude('helper')\n"
    )
    calls = _capture_exec(monkeypatch)
    with pytest.raises(SystemExit):
        _app.run(["--tree"])
    (call,) = calls
    assert call[:4] == ["/fake/uv", "run", "--project", str(stale_project)]


def test_the_retry_never_fires_twice(stale_project, monkeypatch, capsys):
    # Arriving through `uv run` means the sync already happened: the failure
    # is real, surfaces unchanged, and carries no hint (it would be wrong).
    calls = _capture_exec(monkeypatch)
    monkeypatch.setenv("FOOTMAN_UV_REEXEC", "1")
    assert _app.run(["--tree"]) == 64
    assert calls == []
    err = capsys.readouterr().err
    assert "missing_module_xyz" in err
    assert "uv sync" not in err


def test_without_uv_the_refusal_names_the_fix(stale_project, monkeypatch, capsys):
    calls = _capture_exec(monkeypatch)
    monkeypatch.setattr(_app, "_find_uv", lambda: None)
    assert _app.run(["--tree"]) == 64
    assert calls == []
    assert "run uv sync" in capsys.readouterr().err


def test_opted_out_of_uv_still_gets_the_hint(stale_project, monkeypatch, capsys):
    calls = _capture_exec(monkeypatch)
    (stale_project / "pyproject.toml").write_text(
        "[project]\nname='x'\n[tool.footman]\nuv = false\n"
    )
    assert _app.run(["--tree"]) == 64
    assert calls == []  # asked to stay out of uv: advise, don't act
    assert "run uv sync" in capsys.readouterr().err


def test_a_non_import_failure_neither_retries_nor_hints(
    stale_project, monkeypatch, capsys
):
    (stale_project / "tasks.py").write_text("raise RuntimeError('boom')\n")
    calls = _capture_exec(monkeypatch)
    assert _app.run(["--tree"]) == 64
    assert calls == []
    assert "uv sync" not in capsys.readouterr().err


def test_the_retry_never_fires_from_runner_invoke(stale_project, monkeypatch, capsys):
    # An embedded invocation must never exec, and its refusal stays clean:
    # the host process's environment is the host's business.
    from livery.footman.testing import Runner

    calls = _capture_exec(monkeypatch)
    result = Runner().invoke("--tree")
    assert not result.ok
    assert calls == []
    assert "uv sync" not in result.stderr


# --- the script handoff (PEP 723) --------------------------------------------
# A tasks file that declares its own dependencies carries its own world: uv
# builds it, and the invocation continues inside it. These pin the rule's
# edges — especially the ones where it must NOT fire, because a file that
# works today has to keep working.

_SCRIPT_BLOCK = '''\
# /// script
# dependencies = ["livery-footman", "cowsay"]
# ///
from livery.footman import task

@task
def hi(name: str = "world"):
    """Say hello."""
    print(f"hello {name}")
'''


@pytest.fixture
def script_project(project, monkeypatch):
    """A tasks file carrying script metadata, and a fake uv that answers."""
    (project / "tasks.py").write_text(_SCRIPT_BLOCK, encoding="utf-8")
    monkeypatch.delenv("FOOTMAN_UV_REEXEC", raising=False)
    monkeypatch.delenv("FOOTMAN_NO_UV", raising=False)
    monkeypatch.setattr(_app, "_find_uv", lambda: "/fake/uv")
    return project


def _fake_uv(monkeypatch, *, sync_code: int = 0, python: str = "/env/bin/python"):
    """Stand in for the two uv calls the script handoff makes."""
    ran: list[list[str]] = []

    class Done:
        def __init__(self, code, out=""):
            self.returncode, self.stdout = code, out

    def fake_run(cmd, **kwargs):
        ran.append(list(cmd))
        if cmd[1] == "sync":
            return Done(sync_code)
        return Done(0, python + "\n")

    monkeypatch.setattr(_app.subprocess, "run", fake_run)
    return ran


def test_script_handoff_execs_the_scripts_own_interpreter(script_project, monkeypatch):
    calls = _capture_exec(monkeypatch)
    ran = _fake_uv(monkeypatch)
    with pytest.raises(SystemExit):
        _app.run(["hi", "--name=x"])
    # uv materialises the environment, then names its interpreter...
    assert ran[0][:3] == ["/fake/uv", "sync", "--script"]
    assert "--quiet" in ran[0]  # silent unless asked
    assert ran[1][:4] == ["/fake/uv", "python", "find", "--script"]
    # ...and the invocation continues inside it, argv verbatim.
    assert calls == [["/env/bin/python", "-m", "livery.footman", "hi", "--name=x"]]


def test_script_handoff_reenters_the_brand_not_stock_footman(
    script_project, monkeypatch
):
    # `-m livery.footman` is the *stock* CLI: a branded child re-ran the handoff —
    # its belt variable is scoped to the brand, so the parent's didn't
    # count — and died on the mismatch refusal, because the script declares
    # the brand's dist and not 'footman'. The brand re-enters through its
    # own console script's entry point instead.
    from livery.footman.app import App

    (script_project / "tasks.py").write_text(
        _SCRIPT_BLOCK.replace('"livery-footman"', '"acme-cli"'), encoding="utf-8"
    )
    calls = _capture_exec(monkeypatch)
    _fake_uv(monkeypatch)
    monkeypatch.delenv("ACME_UV_REEXEC", raising=False)
    monkeypatch.delenv("ACME_NO_UV", raising=False)
    brand = App(name="acme", prog="acme", dist="acme-cli").brand
    with pytest.raises(SystemExit):
        _app.run(["hi", "--name=x"], brand=brand)
    [argv] = calls
    assert argv[0] == "/env/bin/python"
    assert argv[-2:] == ["hi", "--name=x"]
    assert "footman" not in argv  # never the stock door
    assert any("'acme'" in part for part in argv)  # the brand's own entry point


def test_script_handoff_is_quiet_but_teaches_under_verbose(script_project, monkeypatch):
    _capture_exec(monkeypatch)
    ran = _fake_uv(monkeypatch)
    with pytest.raises(SystemExit):
        _app.run(["-v", "hi"])
    assert "--quiet" not in ran[0]  # -v lets uv's own progress through


def test_a_pinned_project_ignores_the_block_entirely(script_project, monkeypatch):
    # Willem's rule: a portable file checked into a project is not a
    # problem to be reported. The project pins the runner, so the block is
    # simply not this run's business — no warning, no refusal.
    (script_project / "uv.lock").write_text(_UV_LOCK, encoding="utf-8")
    calls = _capture_exec(monkeypatch)
    ran = _fake_uv(monkeypatch)
    with pytest.raises(SystemExit):
        _app.run(["hi"])
    assert ran == []  # no script environment was built
    assert calls == [["/fake/uv", "run", "--project", str(script_project), "fm", "hi"]]


def test_a_pinned_project_notes_the_ignored_block_under_verbose(
    script_project, monkeypatch, capsys
):
    (script_project / "uv.lock").write_text(_UV_LOCK, encoding="utf-8")
    (script_project / ".venv").mkdir()
    monkeypatch.setattr(_app.sys, "prefix", str(script_project / ".venv"))
    _fake_uv(monkeypatch)
    assert _app.run(["hi"]) == 0  # ran here: already the project's environment
    assert "declares script dependencies" not in capsys.readouterr().err
    assert _app.run(["-v", "hi"]) == 0
    err = capsys.readouterr().err
    assert "declares script dependencies" in err and "ignored here" in err


def test_script_handoff_skips_every_optout(script_project, monkeypatch, capsys):
    calls = _capture_exec(monkeypatch)
    ran = _fake_uv(monkeypatch)
    for setup in (
        lambda: monkeypatch.setenv("FOOTMAN_UV_REEXEC", "1"),
        lambda: monkeypatch.setenv("FOOTMAN_NO_UV", "1"),
        lambda: (script_project / "pyproject.toml").write_text(
            "[project]\nname='x'\n[tool.footman]\nuv = false\n"
        ),
    ):
        setup()
        assert _app.run(["hi"]) == 0  # ran here, in this process
        assert "hello world" in capsys.readouterr().out
        monkeypatch.delenv("FOOTMAN_UV_REEXEC", raising=False)
        monkeypatch.delenv("FOOTMAN_NO_UV", raising=False)
    assert calls == [] and ran == []


def test_no_uv_runs_in_place_rather_than_refusing(script_project, monkeypatch, capsys):
    # Environmental facts are never refusals: without uv the file runs
    # exactly as it did before this rule existed.
    monkeypatch.setattr(_app, "_find_uv", lambda: None)
    calls = _capture_exec(monkeypatch)
    assert _app.run(["hi"]) == 0
    assert "hello world" in capsys.readouterr().out
    assert calls == []


def test_a_cascade_of_several_files_is_not_a_script(project, monkeypatch, capsys):
    # A script environment can only be *the* environment; two files have no
    # single answer, so the rule stays out of it. The .git anchors the walk's
    # ceiling at the project — without it the nearest marker is sub/tasks.py
    # itself, and whether the cascade spans two files depends on whatever
    # repo marker happens to sit above the machine's temp directory.
    (project / "pyproject.toml").write_text("[project]\nname='x'\n")
    (project / ".git").mkdir()
    monkeypatch.delenv("FOOTMAN_UV_REEXEC", raising=False)
    monkeypatch.delenv("FOOTMAN_NO_UV", raising=False)
    nested = project / "sub"
    nested.mkdir()
    (nested / "tasks.py").write_text(_SCRIPT_BLOCK, encoding="utf-8")
    monkeypatch.chdir(nested)
    monkeypatch.setattr(_app, "_find_uv", lambda: "/fake/uv")
    calls = _capture_exec(monkeypatch)
    ran = _fake_uv(monkeypatch)
    assert _app.run(["hi"]) == 0
    assert "hello world" in capsys.readouterr().out
    assert calls == [] and ran == []


def test_a_block_without_the_runner_is_refused(script_project, monkeypatch, capsys):
    # The one refusal: an environment that provably cannot import the
    # runner. Named, with the fix.
    (script_project / "tasks.py").write_text(
        _SCRIPT_BLOCK.replace('["livery-footman", "cowsay"]', '["cowsay"]'),
        encoding="utf-8",
    )
    calls = _capture_exec(monkeypatch)
    ran = _fake_uv(monkeypatch)
    assert _app.run(["hi"]) == EX_USAGE
    err = capsys.readouterr().err
    assert "declares script dependencies but not 'livery-footman'" in err
    assert calls == [] and ran == []


def test_a_block_with_no_dependencies_asks_for_no_world(script_project, monkeypatch):
    (script_project / "tasks.py").write_text(
        '# /// script\n# requires-python = ">=3.11"\n# ///\n'
        'from livery.footman import task\n\n@task\ndef hi(name: str = "world"):\n'
        '    """Say hello."""\n    print(f"hello {name}")\n',
        encoding="utf-8",
    )
    calls = _capture_exec(monkeypatch)
    ran = _fake_uv(monkeypatch)
    assert _app.run(["hi"]) == 0
    assert calls == [] and ran == []


def test_a_malformed_block_warns_once_and_runs_anyway(
    script_project, monkeypatch, capsys
):
    (script_project / "tasks.py").write_text(
        "# /// script\n# dependencies = [oops\n# ///\n"
        'from livery.footman import task\n\n@task\ndef hi(name: str = "world"):\n'
        '    """Say hello."""\n    print(f"hello {name}")\n',
        encoding="utf-8",
    )
    calls = _capture_exec(monkeypatch)
    assert _app.run(["hi"]) == 0
    captured = capsys.readouterr()
    assert "warning:" in captured.err and "running without it" in captured.err
    assert "hello world" in captured.out
    assert calls == []


def test_a_failing_sync_carries_uvs_own_exit_code(script_project, monkeypatch):
    _capture_exec(monkeypatch)
    _fake_uv(monkeypatch, sync_code=2)
    # uv already said why on its own stderr; footman doesn't paraphrase it,
    # it just carries the code out.
    assert _app.run(["hi"]) == 2


def test_script_handoff_takes_the_dash_f_file(project, tmp_path_factory, monkeypatch):
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    script = elsewhere / "deploy.py"
    script.write_text(_SCRIPT_BLOCK, encoding="utf-8")
    monkeypatch.delenv("FOOTMAN_UV_REEXEC", raising=False)
    monkeypatch.setattr(_app, "_find_uv", lambda: "/fake/uv")
    calls = _capture_exec(monkeypatch)
    ran = _fake_uv(monkeypatch)
    with pytest.raises(SystemExit):
        _app.run([f"-f={script}", "hi"])
    assert ran[0][3] == str(script)  # that file's environment, not the cwd's
    assert calls == [["/env/bin/python", "-m", "livery.footman", f"-f={script}", "hi"]]


def test_a_failed_script_import_teaches_where_the_environment_went(
    script_project, monkeypatch, capsys
):
    # No uv, so no environment was built — and the file's own import of a
    # declared dependency fails. The refusal says why, and how out.
    (script_project / "tasks.py").write_text(
        _SCRIPT_BLOCK.replace(
            "from livery.footman import task",
            "import cowsay\nfrom livery.footman import task",
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(_app, "_find_uv", lambda: None)
    assert _app.run(["hi"]) == EX_USAGE
    err = capsys.readouterr().err
    assert "declares script dependencies" in err
    assert "install uv" in err and "-f" in err


def test_an_ordinary_import_failure_gains_no_script_hint(project, capsys):
    (project / "tasks.py").write_text("import nosuchmodule\n", encoding="utf-8")
    assert _app.run(["hi"]) == EX_USAGE
    assert "script dependencies" not in capsys.readouterr().err


def test_a_branded_cli_without_a_dist_stays_out_of_it(script_project, monkeypatch):
    # footman cannot know which distribution ships someone else's runner,
    # so it never guesses one into an environment.
    from livery.footman.app import App

    calls = _capture_exec(monkeypatch)
    ran = _fake_uv(monkeypatch)
    assert App(name="acme", prog="acme").run(["hi"]) == 0
    assert calls == [] and ran == []


def test_where_takes_the_dotted_address(project, capsys):
    assert _app.run(["--where=tools.echo"]) == 0
    assert "tasks.py:" in capsys.readouterr().out


def test_where_is_strict_about_empty_segments(project, capsys):
    # The shared resolver never silently normalises a malformed address.
    assert _app.run(["--where=tools..echo"]) == EX_USAGE
    assert "unknown task 'tools..echo'" in capsys.readouterr().err


# --- [tool.footman] sort: alphabetical listings, definition order default ----

_UNSORTED_TASKS = """
from livery.footman import group, task


@task
def zebra():
    "First in the file, last in the alphabet."


@task
def alpha():
    "Last in the file, first in the alphabet."


wash = group("wash")


@wash.task
def rinse(): ...


@wash.task
def dry(): ...
"""


@pytest.fixture
def unsorted_project(tmp_path, monkeypatch):
    """A project whose file order disagrees with the alphabet everywhere;
    returns a hook to rewrite the `[tool.footman]` table."""
    (tmp_path / "tasks.py").write_text(_UNSORTED_TASKS)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")

    def configure(body: str) -> None:
        (tmp_path / "pyproject.toml").write_text(
            f"[project]\nname='x'\n[tool.footman]\n{body}"
        )

    configure("")
    return configure


def test_list_defaults_to_definition_order(unsorted_project, capsys):
    assert _app.run(["--list"]) == 0
    out = capsys.readouterr().out
    assert out.index("zebra") < out.index("alpha")
    assert out.index("wash.rinse") < out.index("wash.dry")


def test_sort_lists_alphabetically_tasks_still_before_groups(unsorted_project, capsys):
    unsorted_project("sort = true")
    assert _app.run(["--list"]) == 0
    out = capsys.readouterr().out
    assert out.index("alpha") < out.index("zebra")
    assert out.index("wash.dry") < out.index("wash.rinse")
    assert out.index("zebra") < out.index("wash.dry")  # the two-band shape holds
    assert _app.run(["--tree"]) == 0  # the same one setting orders --tree
    tree_out = capsys.readouterr().out
    assert tree_out.index("alpha") < tree_out.index("zebra")


def test_sort_orders_the_json_catalog(unsorted_project, capsys):
    unsorted_project("sort = true")
    assert _app.run(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert list(payload["tree"]["tasks"]) == ["alpha", "zebra"]
    assert list(payload["tree"]["groups"]["wash"]["tasks"]) == ["dry", "rinse"]


def test_sort_must_be_a_boolean(unsorted_project, capsys):
    unsorted_project("sort = 'yes'")
    assert _app.run(["--list"]) == EX_USAGE
    assert "config key 'sort' expects true or false" in capsys.readouterr().err


def test_completion_max_age_typo_teaches_on_a_run(unsorted_project, capsys):
    # The one config key that used to parse quietly to its default: a run
    # refuses it by name like every other key. The refresh child keeps the
    # quiet fallback — a keystroke is nobody's moment to learn about a typo.
    unsorted_project('completion.max_age = "tenminutes"')
    assert _app.run(["--list"]) == EX_USAGE
    err = capsys.readouterr().err
    assert "completion.max_age" in err
    assert "duration" in err
    assert "tenminutes" in err


def test_completion_max_age_child_reading_stays_quiet():
    from livery.footman import _config

    cfg = {"completion": {"max_age": "tenminutes"}}
    assert _config.completion_max_age(cfg) == _config.DEFAULT_COMPLETION_MAX_AGE_S


def test_completion_max_age_valid_spellings_survive_strict(unsorted_project, capsys):
    unsorted_project('completion.max_age = "30s"')
    assert _app.run(["--list"]) == 0


def test_sort_never_reorders_the_run(unsorted_project, capsys):
    # Presentation only: a chain still runs in the order it was written.
    unsorted_project("sort = true")
    assert _app.run(["-s", "zebra", "alpha"]) == 0


def test_sort_flag_orders_one_invocation(unsorted_project, capsys):
    # No config at all: --sort alone sorts this listing.
    assert _app.run(["--sort", "--list"]) == 0
    out = capsys.readouterr().out
    assert out.index("alpha") < out.index("zebra")


def test_sort_flag_never_masks_a_broken_config_value(unsorted_project, capsys):
    unsorted_project("sort = 'yes'")
    assert _app.run(["--sort", "--list"]) == EX_USAGE
    assert "config key 'sort' expects true or false" in capsys.readouterr().err


_HIDDEN_TASKS = """
from livery.footman import group, task


@task
def visible():
    "Typed by humans."


@task(hidden=True)
def machine_only():
    "Called by CI, never typed."


internal = group("internal", hidden=True)


@internal.task
def cleanup():
    "Hidden by the group it lives in."


@internal.task(hidden=False)
def rescued():
    "Opted back into the listings."


ops = group("ops")


@ops.task
def deploy():
    "An ordinary nested task."
"""


@pytest.fixture
def hidden_project(tmp_path, monkeypatch):
    (tmp_path / "tasks.py").write_text(_HIDDEN_TASKS)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")


def test_hidden_leaves_the_listings_but_still_runs(hidden_project, capsys):
    assert _app.run(["--list"]) == 0
    out = capsys.readouterr().out
    assert "visible" in out
    assert "machine-only" not in out  # the task nobody is meant to type

    # ...and it is a perfectly ordinary address.
    assert _app.run(["machine-only"]) == 0


def test_hidden_is_inherited_and_overridable(hidden_project, capsys):
    assert _app.run(["--list"]) == 0
    out = capsys.readouterr().out
    assert "internal.cleanup" not in out  # inherited from the group
    assert "internal.rescued" in out  # hidden=False is a real way back
    assert _app.run(["internal.cleanup"]) == 0  # still callable


def test_hidden_is_marked_not_omitted_under_json(hidden_project, capsys):
    assert _app.run(["--json"]) == 0
    tree = json.loads(capsys.readouterr().out)["tree"]
    # A machine is exactly who calls these, so the catalog keeps them —
    # flagged, so a consumer can tell them apart.
    assert tree["tasks"]["machine-only"]["hidden"] is True
    assert "hidden" not in tree["tasks"]["visible"]
    assert tree["groups"]["internal"]["hidden"] is True
    assert "hidden" not in tree["groups"]["internal"]["tasks"]["rescued"]


def test_hidden_still_completes(hidden_project, capsys):
    """`hidden` is a listings word. TAB offers every address that runs —
    you are already typing a name, and a machine-facing one is exactly the
    one worth being spelled for you."""
    from livery.footman import _complete

    assert _app.run(["--json"]) == 0
    tree = json.loads(capsys.readouterr().out)["tree"]

    top = " ".join(_complete.complete(tree, [""]))
    assert "visible" in top
    assert "machine-only" in top
    assert "internal." in top
    inside = " ".join(_complete.complete(tree, ["internal."]))
    assert "internal.rescued" in inside
    assert "internal.cleanup" in inside  # hidden by its group, still typed


def test_all_shows_hidden_in_the_listings(hidden_project, capsys):
    assert _app.run(["--list", "--all"]) == 0
    out = capsys.readouterr().out
    assert "machine-only" in out
    assert "internal.cleanup" in out

    assert _app.run(["--tree", "-a"]) == 0
    tree_out = capsys.readouterr().out
    assert "machine-only" in tree_out and "cleanup" in tree_out

    # ...and the default listing is unchanged by its existence.
    assert _app.run(["--list"]) == 0
    assert "machine-only" not in capsys.readouterr().out


def test_all_reaches_help_listings(hidden_project, capsys):
    assert _app.run(["--help", "--all"]) == 0
    assert "machine-only" in capsys.readouterr().out
    assert _app.run(["--help"]) == 0
    assert "machine-only" not in capsys.readouterr().out

    # A group's own help honours it too, so one rule covers every listing.
    assert _app.run(["--all", "--help", "internal"]) == 0
    assert "internal.cleanup" in capsys.readouterr().out
    assert _app.run(["--help", "internal"]) == 0
    assert "internal.cleanup" not in capsys.readouterr().out


def test_did_you_mean_knows_hidden_addresses(hidden_project, capsys):
    """The typo index answers about everything a human can type — a
    machine-facing task mistyped by hand earns the same suggestion."""
    assert _app.run(["--help", "machine-onlyy"]) == 64
    assert "machine-only" in capsys.readouterr().err


def test_tree_draws_branches_and_skips_hidden(hidden_project, capsys):
    assert _app.run(["--tree"]) == 0
    out = capsys.readouterr().out
    assert "├─" in out or "└─" in out  # a drawn tree, not an indented listing
    assert "deploy" in out and "ops." in out
    # The nested leaf shows its own name; the address lives in --list.
    assert "ops.deploy" not in out
    assert "machine-only" not in out and "cleanup" not in out


def test_tree_aligns_the_description_column(project, capsys):
    # The two-band layout --list draws: every description starts at one
    # column, whatever depth its name sits at — and no separator glyph,
    # exactly like --list.
    assert _app.run(["--tree"]) == 0
    out = capsys.readouterr().out
    assert "—" not in out
    columns = set()
    for line in out.splitlines():
        for help_text in ("Say hello.", "Extra tools", "Echo words."):
            if help_text in line:
                columns.add(line.index(help_text))
    assert len(columns) == 1


def test_a_single_dash_misspelling_refuses_instead_of_acting(tmp_path):
    # `-version=1` drove --version and exited 0; `-install-completion=zsh`
    # would have edited a shell rc — the lenient pre-discovery walk carried
    # the unknown token through and the dash-stripping normalisation made
    # it ACT before the authoritative parse could refuse it (audit L7).
    import textwrap

    from livery.footman.testing import Runner

    (tmp_path / "tasks.py").write_text(
        textwrap.dedent(
            """
            from livery.footman import task

            @task
            def hi():
                print("hi")
            """
        )
    )
    refused = Runner().invoke("-version=1", cwd=tmp_path)
    assert refused.exit_code == EX_USAGE
    assert "did you mean --version?" in refused.stderr  # taught, not acted on
    assert "footman" not in refused.stdout  # the version never printed
    # Both legitimate spellings still answer.
    assert Runner().invoke("--version", cwd=tmp_path).exit_code == 0
    assert Runner().invoke("-V", cwd=tmp_path).exit_code == 0
    # And a long name missing one dash hints the long spelling — not the
    # comic fused-short reading ("did you mean -j=obs?").
    hinted = Runner().invoke("-jobs=2 hi", cwd=tmp_path)
    assert hinted.exit_code == EX_USAGE
    assert "did you mean --jobs?" in hinted.stderr


def test_a_mistyped_tasks_config_key_refuses_loudly(tmp_path):
    # `tasks = 123` silently behaved as if unset while every option-backed
    # key refused its wrong TOML type by name (audit L5).
    from livery.footman.testing import Runner

    (tmp_path / "tasks.py").write_text(
        "from livery.footman import task\n\n\n@task\ndef hi():\n    print('hi')\n"
    )
    (tmp_path / "footman.toml").write_text("tasks = 123\n")
    r = Runner().invoke("--list", cwd=tmp_path)
    assert r.exit_code == EX_USAGE
    assert "config key 'tasks' expects a filename string" in r.stderr


def test_a_deleted_working_directory_is_one_taught_line(tmp_path):
    # A shell sitting in a directory another process removed: every step
    # needs a cwd, and each used to crash as its own raw FileNotFoundError
    # traceback (audit L6). One taught line, at the door.
    if sys.platform == "win32":
        pytest.skip("directory-in-use semantics differ on Windows")
    import os
    import subprocess
    import textwrap

    gone = tmp_path / "gone"
    gone.mkdir()
    helper = textwrap.dedent(
        """
        import os, subprocess, sys
        os.chdir(sys.argv[1])
        os.rmdir(sys.argv[1])
        r = subprocess.run(
            [sys.executable, "-m", "livery.footman", "--list"],
            capture_output=True, text=True,
        )
        print(r.returncode)
        print(r.stderr.strip())
        """
    )
    env = {**os.environ, "FOOTMAN_NO_UV": "1"}
    env.pop("VIRTUAL_ENV", None)
    done = subprocess.run(
        [sys.executable, "-c", helper, str(gone)],
        capture_output=True,
        text=True,
        env=env,
    )
    lines = done.stdout.strip().splitlines()
    assert lines[0] == "1"
    assert "the working directory no longer exists" in done.stdout
    assert "Traceback" not in done.stdout
