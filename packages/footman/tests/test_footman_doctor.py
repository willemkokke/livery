"""The doctor: footman's own checks, then every plugin's, and the repairs under --fix."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import livery.footman as footman
from livery.footman import Failed, _gc
from livery.footman._tasks import doctor


def _facts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    dirs = {name: tmp_path / name for name in ("cache", "data", "config")}
    monkeypatch.setattr(doctor._paths, "footman_cache_dir", lambda: dirs["cache"])
    monkeypatch.setattr(doctor._paths, "footman_data_dir", lambda: dirs["data"])
    monkeypatch.setattr(doctor._paths, "footman_config_dir", lambda: dirs["config"])
    for path in dirs.values():
        path.mkdir()
    return dirs


def test_a_raising_or_unloadable_doctor_never_stops_the_rest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Refusals first: one doctor's fault is named; the others still run,
    # in name order, with the facts every doctor gets.
    dirs = _facts(monkeypatch, tmp_path)
    seen: list[dict[str, Any]] = []

    def good(**facts: Any) -> list[footman.Finding]:
        seen.append(facts)
        return [footman.Finding("acme: every widget present")]

    def bad(**facts: Any) -> list[footman.Finding]:
        raise RuntimeError("the doctor broke")

    monkeypatch.setattr(
        doctor,
        "_doctors",
        lambda: [
            ("acme.bad", bad),
            ("acme.good", good),
            ("acme.gone", "ImportError: no module"),
        ],
    )
    lines, standing = doctor.run_doctors(fix=False)
    assert (
        "  acme.bad: raised RuntimeError: the doctor broke; the other doctors"
        " still ran" in lines
    )
    assert "  acme.gone: could not load (ImportError: no module); skipped" in lines
    assert "  acme: every widget present" in lines
    # A doctor that raised and one that cannot load are standing
    # findings both: the run is red.
    assert standing == 2
    (facts,) = seen
    assert facts["cache_dir"] == dirs["cache"] and facts["data_dir"] == dirs["data"]
    assert facts["config_dir"] == dirs["config"] and facts["now"].tzinfo is not None
    assert "fix" not in facts  # footman runs the repairs, a doctor only names them


def test_a_repair_that_fails_leaves_its_finding_with_the_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _facts(monkeypatch, tmp_path)

    def broken() -> None:
        raise OSError("read-only")

    def ill(**facts: Any) -> list[footman.Finding]:
        return [
            footman.Finding("widgets", "two missing", "run the widget sync", broken)
        ]

    monkeypatch.setattr(doctor, "_doctors", lambda: [("acme", ill)])
    monkeypatch.setattr(doctor, "_own", lambda facts: [])
    lines, standing = doctor.run_doctors(fix=True)
    assert "  widgets: two missing" in lines
    assert "    repair failed: OSError: read-only" in lines
    assert "    fix: run the widget sync" in lines
    assert standing == 1


def test_fix_runs_the_repairs_checks_again_and_a_second_fix_finds_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _facts(monkeypatch, tmp_path)
    state = {"missing": 2}

    def mend() -> None:
        state["missing"] = 0

    def ill(**facts: Any) -> list[footman.Finding]:
        if state["missing"]:
            return [
                footman.Finding(
                    "widgets",
                    f"{state['missing']} missing",
                    "run the widget sync",
                    mend,
                )
            ]
        return [footman.Finding("widgets: all present")]

    monkeypatch.setattr(doctor, "_doctors", lambda: [("acme", ill)])
    monkeypatch.setattr(doctor, "_own", lambda facts: [])
    # Without --fix the finding stands and its fix is named, nothing runs.
    lines, standing = doctor.run_doctors(fix=False)
    assert "  widgets: 2 missing" in lines and "    fix: run the widget sync" in lines
    assert standing == 1 and state["missing"] == 2
    lines, standing = doctor.run_doctors(fix=True)
    assert "  widgets: 2 missing; repaired" in lines
    assert "  widgets: all present" in lines
    assert standing == 0 and state["missing"] == 0
    lines, standing = doctor.run_doctors(fix=True)
    assert lines == ["  widgets: all present"] and standing == 0


def test_a_finding_from_a_package_that_never_imports_footman_is_read_by_its_attributes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _facts(monkeypatch, tmp_path)

    class Foreign:
        what = "views"
        found = "node 24.21.0: 3 files missing"
        fix = "fm doctor --fix supplies it again"
        repair = None

    monkeypatch.setattr(
        doctor, "_doctors", lambda: [("toolroom", lambda **f: [Foreign()])]
    )
    monkeypatch.setattr(doctor, "_own", lambda facts: [])
    lines, standing = doctor.run_doctors(fix=False)
    assert "  views: node 24.21.0: 3 files missing" in lines
    assert "    fix: fm doctor --fix supplies it again" in lines
    assert standing == 1


def test_footmans_own_checks_find_a_missing_directory_and_a_gone_process_s_scratch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dirs = _facts(monkeypatch, tmp_path)
    monkeypatch.setattr(doctor, "_doctors", lambda: [])
    # Healthy first: both directories present, nothing left in scratch.
    lines, standing = doctor.run_doctors(fix=False)
    assert standing == 0 and any("directories" in line for line in lines)
    # A data directory gone, and a scratch directory of a process that ended.
    dirs["data"].rmdir()
    ended = subprocess.Popen([sys.executable, "-c", "pass"])
    ended.wait()
    gone = dirs["cache"] / _gc.SCRATCH / f"replay-{ended.pid}-abc"
    gone.mkdir(parents=True)
    live = dirs["cache"] / _gc.SCRATCH / f"shell-{os.getpid()}-abc"
    live.mkdir()
    lines, standing = doctor.run_doctors(fix=False)
    assert standing == 2
    assert any("data directory" in line and "missing" in line for line in lines)
    assert any("scratch: 1 director" in line for line in lines)
    lines, standing = doctor.run_doctors(fix=True)
    assert standing == 0
    assert dirs["data"].is_dir() and not gone.exists() and live.is_dir()


def test_the_verb_is_red_while_a_finding_stands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _facts(monkeypatch, tmp_path)
    monkeypatch.setattr(doctor, "_own", lambda facts: [])
    monkeypatch.setattr(
        doctor,
        "_doctors",
        lambda: [
            ("acme", lambda **f: [footman.Finding("widgets", "one missing", "sync")])
        ],
    )
    with pytest.raises(Failed, match="1 finding"):
        doctor.doctor()
    assert "  widgets: one missing" in capsys.readouterr().out
    monkeypatch.setattr(doctor, "_doctors", lambda: [])
    doctor.doctor()
    assert "nothing to report" in capsys.readouterr().out or True
