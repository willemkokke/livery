"""Notes with levels: the registry, the policy ladder, the record, the wall."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from livery.footman import _manifest, _notes
from livery.footman._executor import run_chain
from livery.footman._split import split_chain
from livery.footman.registry import Group
from livery.footman.testing import Runner


@pytest.fixture(autouse=True)
def clean_notes():
    """Every test starts with default policy and a fresh dedup set."""
    _notes.install_policy(None)
    _notes.reset()
    yield
    _notes.install_policy(None)
    _notes.reset()


def drive(build, line, **cfg):
    reg = Group("root")
    build(reg)
    tree = _manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, line.split())
    return run_chain(reg, segments, ctx_config=cfg)


# --- the ladder ---------------------------------------------------------------


def test_resolve_walks_most_specific_first():
    _notes.install_policy(
        {
            "deploy/environ-write:JAVA_HOME": "trace",
            "deploy/environ-write": "info",
            "deploy/*": "warning",
            "environ-write:JAVA_HOME": "info",
            "environ-write": "warning",
            "*": "error",
        }
    )
    # Each rung answers exactly when every rung above it does not.
    assert _notes.resolve("deploy", "environ-write:JAVA_HOME") == "trace"
    assert _notes.resolve("deploy", "environ-write:PATH") == "info"
    assert _notes.resolve("deploy", "getcwd") == "warning"
    assert _notes.resolve("build", "environ-write:JAVA_HOME") == "info"
    assert _notes.resolve("build", "environ-write:PATH") == "warning"
    assert _notes.resolve("build", "getcwd") == "error"


def test_resolve_falls_to_the_kinds_default():
    assert _notes.resolve("any", "environ-write:X") == "info"
    assert _notes.resolve("any", "popen-inject:git") == "warning"
    assert _notes.resolve("any", "global-unread:jobs") == "info"
    assert _notes.resolve("any", "lane-wait:serial") == "info"


def test_the_blanket_is_outranked_by_every_named_rule():
    _notes.install_policy({"*": "error", "environ-write:SAFE": "info"})
    assert _notes.resolve("t", "environ-write:SAFE") == "info"  # the whitelist
    assert _notes.resolve("t", "environ-write:OTHER") == "error"  # the wall


def test_split_key_spellings():
    assert _notes._split_key("*") == ("*", "*")
    assert _notes._split_key("getcwd") == ("*", "getcwd")
    assert _notes._split_key("migrate/getcwd") == ("migrate", "getcwd")
    assert _notes._split_key("docs.build/*") == ("docs.build", "*")
    assert _notes._split_key("docs.build/popen-inject:dot") == (
        "docs.build",
        "popen-inject:dot",
    )


# --- validation ---------------------------------------------------------------


def test_validate_accepts_the_documented_spellings():
    assert (
        _notes.validate(
            {
                "*": "error",
                "environ-write:JAVA_HOME": "info",
                "migrate/getcwd": "info",
                "docs.build/*": "warning",
                "lane-wait": "trace",
            }
        )
        is None
    )


def test_validate_refuses_an_unknown_family_by_name():
    error = _notes.validate({"environ-wrote": "error"})
    assert error is not None
    assert "environ-wrote" in error
    assert "environ-write" in error  # the valid kinds are listed


def test_validate_refuses_a_bad_level_and_a_non_table():
    error = _notes.validate({"getcwd": "fatal"})
    assert error is not None and "trace, info, warning, error" in error
    assert _notes.validate(["getcwd"]) is not None
    assert _notes.validate({"getcwd": 3}) is not None


# --- emission: levels, prefixes, sites, records -------------------------------


def test_a_note_prints_its_level_and_site(capfd, monkeypatch):
    monkeypatch.delenv("NOTED_VAR", raising=False)

    def tasks(reg):
        @reg.task
        def go():
            os.environ["NOTED_VAR"] = "1"

    results = drive(tasks, "go")
    assert results[0].ok
    err = capfd.readouterr().err
    assert "info: task go sets NOTED_VAR" in err  # the level is the prefix
    assert "test_notes.py:" in err  # the site names the offending file


def test_trace_is_recorded_but_silent_without_verbose(capfd, monkeypatch):
    monkeypatch.delenv("QUIET_VAR", raising=False)
    _notes.install_policy({"environ-write": "trace"})

    def tasks(reg):
        @reg.task
        def go():
            os.environ["QUIET_VAR"] = "1"

    results = drive(tasks, "go")
    assert results[0].ok
    assert "QUIET_VAR" not in capfd.readouterr().err  # gated…
    (note,) = results[0].notes
    assert note.kind == "environ-write:QUIET_VAR"  # …yet recorded
    assert note.level == "trace"


def test_trace_prints_under_verbose(capfd, monkeypatch):
    monkeypatch.delenv("LOUD_VAR", raising=False)
    _notes.install_policy({"environ-write": "trace"})

    def tasks(reg):
        @reg.task
        def go():
            os.environ["LOUD_VAR"] = "1"

    results = drive(tasks, "go", verbose=True)
    assert results[0].ok
    assert "trace: task go sets LOUD_VAR" in capfd.readouterr().err


def test_the_record_rides_the_task_result(monkeypatch):
    monkeypatch.delenv("RIDER_VAR", raising=False)

    def tasks(reg):
        @reg.task
        def go():
            os.environ["RIDER_VAR"] = "1"
            subprocess.run(
                [sys.executable, "-c", "pass"], capture_output=True, check=False
            )

    results = drive(tasks, "go")
    assert results[0].ok
    kinds = {n.kind.split(":", 1)[0] for n in results[0].notes}
    assert kinds == {"environ-write", "popen-inject"}
    for note in results[0].notes:
        assert note.level in _notes.LEVELS
        assert note.text


# --- the error wall -----------------------------------------------------------


def test_error_collects_every_issue_and_fails_at_the_boundary(capfd, monkeypatch):
    monkeypatch.delenv("WALL_A", raising=False)
    monkeypatch.delenv("WALL_B", raising=False)
    _notes.install_policy({"*": "error"})
    ran = []

    def tasks(reg):
        @reg.task
        def go():
            os.environ["WALL_A"] = "1"
            os.environ["WALL_B"] = "2"
            ran.append("finished")  # the body runs to completion

    results = drive(tasks, "go")
    assert ran == ["finished"]  # nothing stopped early
    assert not results[0].ok and results[0].code == 1
    assert isinstance(results[0].error, _notes.BannedNotes)
    message = str(results[0].error)
    assert "2 banned notes" in message
    assert "environ-write:WALL_A" in message  # both, in one run
    assert "environ-write:WALL_B" in message
    assert "[tool.footman.notes]" in message  # the way out is named
    err = capfd.readouterr().err
    assert err.count("error: task go sets") == 2  # each said as it fired


def test_a_failed_body_keeps_its_own_failure(monkeypatch):
    monkeypatch.delenv("DOOMED_VAR", raising=False)
    _notes.install_policy({"*": "error"})

    def tasks(reg):
        @reg.task
        def go():
            os.environ["DOOMED_VAR"] = "1"
            raise RuntimeError("the real failure")

    results = drive(tasks, "go")
    assert not results[0].ok
    assert isinstance(results[0].error, RuntimeError)  # not rewritten
    assert any(
        n.kind == "environ-write:DOOMED_VAR" for n in results[0].notes
    )  # the notes still ride the row


def test_the_whitelist_survives_the_blanket(monkeypatch):
    monkeypatch.delenv("AUDITED", raising=False)
    monkeypatch.delenv("FRESH", raising=False)
    _notes.install_policy({"*": "error", "environ-write:AUDITED": "info"})

    def audited(reg):
        @reg.task
        def go():
            os.environ["AUDITED"] = "known harmless"

    assert drive(audited, "go")[0].ok  # the pinned entry outranks the wall

    def fresh(reg):
        @reg.task
        def go():
            os.environ["FRESH"] = "a new occurrence"

    assert not drive(fresh, "go")[0].ok  # a new instance hits the wall


def test_the_task_axis_carves_out_one_task(monkeypatch):
    monkeypatch.delenv("AXIS_VAR", raising=False)
    _notes.install_policy({"*": "error", "blessed/environ-write": "info"})

    def tasks(reg):
        @reg.task
        def blessed():
            os.environ["AXIS_VAR"] = "1"

        @reg.task
        def plain():
            os.environ["AXIS_VAR"] = "1"

    results = drive(tasks, "blessed plain")
    by_name = {r.task: r for r in results}
    assert by_name["blessed"].ok
    assert not by_name["plain"].ok


# --- config, envelope, Runner -------------------------------------------------


def test_a_project_table_promotes_and_the_run_refuses_bad_tables(tmp_path):
    (tmp_path / "tasks.py").write_text(
        "import os\nfrom footman import task\n\n"
        "@task\ndef go():\n"
        '    """Set a variable."""\n'
        '    os.environ["PROMOTED_VAR"] = "1"\n'
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname=\'x\'\n[tool.footman.notes]\n"*" = "error"\n'
    )
    result = Runner().invoke("go", cwd=tmp_path)
    assert not result.ok
    assert "banned" in result.stderr

    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='x'\n[tool.footman.notes]\nenviron-wrote = \"error\"\n"
    )
    refused = Runner().invoke("go", cwd=tmp_path)
    assert not refused.ok
    assert "unknown note kind" in refused.stderr


def test_notes_ride_the_json_envelope(monkeypatch):
    monkeypatch.delenv("ENVELOPE_VAR", raising=False)
    reg = Group("root")

    @reg.task
    def go():
        """Set a variable."""
        os.environ["ENVELOPE_VAR"] = "1"

    result = Runner().invoke("--json go", tasks=reg)
    assert result.ok, result.stderr
    envelope = json.loads(result.stdout)
    (item,) = [i for i in envelope["items"] if i.get("task") == "go"]
    (note,) = item["notes"]
    assert note["kind"] == "environ-write:ENVELOPE_VAR"
    assert note["level"] == "info"
    assert "test_notes.py:" in note["site"]
    assert "sets ENVELOPE_VAR" in note["text"]


def test_runner_results_expose_the_notes(monkeypatch):
    monkeypatch.delenv("RUNNER_VAR", raising=False)
    reg = Group("root")

    @reg.task
    def go():
        """Set a variable."""
        os.environ["RUNNER_VAR"] = "1"

    result = Runner().invoke("go", tasks=reg)
    assert result.ok, result.stderr
    (note,) = result.results[0].notes
    assert note.kind == "environ-write:RUNNER_VAR"


# --- instance tails -----------------------------------------------------------


def test_program_name_reads_every_spawn_spelling():
    assert _notes.program_name(["/usr/bin/git", "status"]) == "git"
    assert _notes.program_name(("git", "status")) == "git"
    assert _notes.program_name("git status --short") == "git"
    assert _notes.program_name(b"/bin/echo hi") == "echo"
    assert _notes.program_name(Path("/opt/tools/dot")) == "dot"
    assert _notes.program_name([]) == "?"
    assert _notes.program_name("") == "?"
    assert _notes.program_name(object()) == "?"


def test_a_raw_spawn_notes_its_program(monkeypatch):
    def tasks(reg):
        @reg.task
        def go():
            subprocess.run(
                [sys.executable, "-c", "pass"], capture_output=True, check=False
            )

    results = drive(tasks, "go")
    assert results[0].ok
    (note,) = [n for n in results[0].notes if n.kind.startswith("popen-inject:")]
    program = note.kind.split(":", 1)[1]
    assert program == os.path.basename(sys.executable)


def test_a_hook_return_is_a_levelled_note():
    # The one stray that used to print outside the channel: now a kind like
    # any other — levelled, dedupped, recorded, and addressable by plugin.
    reg = Group("root")

    @reg.pre_task
    def eager(inv, task):
        return "something"

    @reg.task
    def build():
        """Build."""

    result = Runner().invoke("build", tasks=reg)
    assert result.ok, result.stderr
    (note,) = [n for n in result.results[0].notes if n.kind.startswith("hook-return:")]
    assert note.level == "warning"
    assert "reserved" in note.text
    assert "warning:" in result.stdout + result.stderr


def test_the_generated_table_lists_every_kind():
    from livery.footman import markdown

    table = markdown.notes_table()
    for family, instance, default, _ in _notes.KINDS:
        assert (
            f"`{family}:<{instance}>`" in table if instance else f"`{family}`" in table
        )
        assert default in table
