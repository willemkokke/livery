"""The console-control section: named where the status appears, quiet elsewhere."""

from __future__ import annotations

import subprocess
import sys
from importlib.metadata import entry_points

import pytest

from livery.workshop import _pytest_console


def test_the_status_is_known_as_python_and_the_shell_spell_it() -> None:
    assert _pytest_console.control_exit(3221225786)
    assert _pytest_console.control_exit(3221225786 - 2**32)
    assert not _pytest_console.control_exit(1)
    assert not _pytest_console.control_exit(True)  # a bool is no code
    assert not _pytest_console.control_exit(None)


def test_exit_codes_walk_the_cause_chain_and_read_the_number_in_prose() -> None:
    inner = subprocess.CalledProcessError(3221225786, ["git", "tag", "v1"])
    outer = RuntimeError("the seed failed")
    outer.__cause__ = inner
    assert _pytest_console.exit_codes(outer) == [3221225786]
    prose = RuntimeError("git exited 3221225786 while tagging")
    assert _pytest_console.exit_codes(prose) == [3221225786]
    assert _pytest_console.exit_codes(None) == []
    assert _pytest_console.exit_codes(subprocess.CalledProcessError(2, ["x"])) == [2]


def test_the_diagnosis_names_the_command_the_process_the_run_and_the_console(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw3")
    monkeypatch.setenv("GITHUB_RUN_ID", "35050711742")
    monkeypatch.setenv("GITHUB_JOB", "check")
    for runner_own in ("RUNNER_NAME", "RUNNER_OS"):  # a CI runner sets these itself
        monkeypatch.delenv(runner_own, raising=False)
    monkeypatch.setattr(_pytest_console, "_EVENTS", [(0.0, 0), (1.0, 1)])
    error = subprocess.CalledProcessError(
        3221225786, ["git", "tag", "packages/tool/v0.2.0"]
    )
    text = _pytest_console.diagnosis(error)
    assert text.startswith(
        "a child ended with STATUS_CONTROL_C_EXIT (0xC000013A, 3221225786)"
    )
    assert "command: ['git', 'tag', 'packages/tool/v0.2.0']" in text
    assert "worker gw3" in text
    assert "run: {'GITHUB_RUN_ID': '35050711742', 'GITHUB_JOB': 'check'}" in text
    assert (
        "control events this process saw:\n  00:00:00Z CTRL_C_EVENT\n"
        "  00:00:01Z CTRL_BREAK_EVENT" in text
    )
    if sys.platform == "win32":
        assert "console processes:" in text and "python" in text.lower()
    else:
        assert "console processes: not a Windows console" in text
    monkeypatch.setattr(_pytest_console, "_EVENTS", [])
    monkeypatch.delenv("GITHUB_RUN_ID")
    monkeypatch.delenv("GITHUB_JOB")
    bare = _pytest_console.diagnosis(RuntimeError("no command"))
    assert "command:" not in bare and "run: not a CI run" in bare
    assert "control events this process saw: none" in bare


def test_a_failed_test_whose_child_took_the_event_gets_the_section(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(
        test_took="""
        import subprocess

        def test_tag():
            raise subprocess.CalledProcessError(3221225786, ["git", "tag", "v1"])
        """,
        test_plain="""
        import subprocess

        def test_tag():
            raise subprocess.CalledProcessError(1, ["git", "tag", "v1"])
        """,
    )
    # The entry point loads the plugin; naming it again would register it twice.
    took = pytester.runpytest("-p", "no:cacheprovider", "test_took.py")
    took.assert_outcomes(failed=1)
    text = "\n".join(took.outlines)
    assert "console control event" in text
    assert "a child ended with STATUS_CONTROL_C_EXIT" in text
    plain = pytester.runpytest("-p", "no:cacheprovider", "test_plain.py")
    plain.assert_outcomes(failed=1)
    assert "console control event" not in "\n".join(plain.outlines)


def test_the_plugin_rides_the_pytest11_entry_point() -> None:
    names = {entry.name: entry.value for entry in entry_points(group="pytest11")}
    assert names.get("livery-workshop-console") == "livery.workshop._pytest_console"


@pytest.mark.skipif(
    sys.platform != "win32", reason="a console control handler is Windows' own"
)
def test_the_handler_records_an_event_and_passes_it_on() -> None:
    _pytest_console._install_handler()
    assert _pytest_console._HANDLER is not None
    assert isinstance(_pytest_console.console_processes(), list)
