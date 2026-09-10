"""The coverage marks: the fall-open paths and refusals first, then the ratchet."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from livery.workshop import _coverage_marks, _state


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture
def work(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "--initial-branch=main", str(origin))
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", str(origin), str(work))
    _git(work, "config", "user.name", "tester")
    _git(work, "config", "user.email", "tester@example.invalid")
    (work / "README.md").write_text("the repository\n")
    _git(work, "add", "README.md")
    _git(work, "commit", "-qm", "init")
    _git(work, "push", "-q", "-u", "origin", "main")
    return work


# --- nothing to judge by, and nothing to write from --------------------------


def test_an_empty_store_has_no_marks_and_no_reason(work: Path) -> None:
    assert _coverage_marks.marks(work) == ({}, "")


def test_an_unreadable_store_names_its_reason(work: Path, tmp_path: Path) -> None:
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    current, why = _coverage_marks.marks(work)
    assert current is None
    assert why.startswith("the coverage/marks series could not be read: ")


def test_a_bad_row_is_skipped_and_the_others_stand(work: Path) -> None:
    ref = _coverage_marks.SERIES.ref
    assert (
        _state.put(
            work,
            ref,
            {
                "20260101T000000.000000Z--packages-x": "not json",
                "20260101T000001.000000Z--packages-y": json.dumps({"schema": 99}),
                "20260101T000002.000000Z--packages-z": json.dumps(
                    {"schema": 1, "package": "packages/z", "value": "high"}
                ),
            },
            message="bad rows",
        )
        == ""
    )
    assert (
        _coverage_marks.write_mark(
            work, package="packages/x", value=91.25, kind="first", by="7"
        )
        == ""
    )
    current, why = _coverage_marks.marks(work)
    assert why == "" and current is not None
    assert set(current) == {"packages/x"}
    assert current["packages/x"].value == 91.25
    assert current["packages/x"].kind == "first"


# --- the verdicts -------------------------------------------------------------


def _mark(
    value: float, kind: str = "ratchet", reason: str = ""
) -> _coverage_marks.Mark:
    return _coverage_marks.Mark("packages/x", value, kind, "7", reason, "2026-09-09")


def test_without_a_mark_the_run_passes_and_records() -> None:
    (verdict,) = _coverage_marks.judge({"packages/x": 80.0}, {}, {})
    assert verdict.ok and verdict.raises and verdict.floor is None
    assert "no mark yet; this run records it" in _coverage_marks.render(verdict)


def test_the_epsilon_absorbs_wobble_in_both_directions() -> None:
    current = {"packages/x": _mark(90.0)}
    down, flat, up, over = (
        _coverage_marks.judge({"packages/x": 89.6}, current, {})
        + _coverage_marks.judge({"packages/x": 90.0}, current, {})
        + _coverage_marks.judge({"packages/x": 90.4}, current, {})
        + _coverage_marks.judge({"packages/x": 90.6}, current, {})
    )
    assert down.ok and not down.raises
    assert flat.ok and not flat.raises
    assert up.ok and not up.raises
    assert over.ok and over.raises
    assert "new mark: 90.6%" in _coverage_marks.render(over)


def test_below_the_floor_fails_and_names_the_mark() -> None:
    (verdict,) = _coverage_marks.judge(
        {"packages/x": 89.4}, {"packages/x": _mark(90.0)}, {"packages/x": 0.5}
    )
    assert not verdict.ok
    line = _coverage_marks.render(verdict)
    assert "BELOW THE FLOOR" in line and "floor 89.5%" in line


def test_a_declared_epsilon_widens_the_floor_for_its_package() -> None:
    (verdict,) = _coverage_marks.judge(
        {"packages/x": 88.0}, {"packages/x": _mark(90.0)}, {"packages/x": 2.5}
    )
    assert verdict.ok and verdict.floor == 87.5


def test_an_accepted_mark_is_judged_from_its_row_and_says_why() -> None:
    (verdict,) = _coverage_marks.judge(
        {"packages/x": 95.0},
        {"packages/x": _mark(90.0, "accept", "a module moved")},
        {},
    )
    line = _coverage_marks.render(verdict)
    assert "accept by 7" in line and "accepted: a module moved" in line
    assert verdict.raises


def test_the_newest_row_per_package_is_the_mark(work: Path) -> None:
    for value, kind in ((80.0, "first"), (85.0, "ratchet"), (70.0, "accept")):
        assert (
            _coverage_marks.write_mark(
                work,
                package="packages/x",
                value=value,
                kind=kind,
                by="7" if kind != "accept" else "willem",
                reason="a reason" if kind == "accept" else "",
            )
            == ""
        )
    assert (
        _coverage_marks.write_mark(
            work, package="packages/y", value=50.0, kind="first", by="8"
        )
        == ""
    )
    current, why = _coverage_marks.marks(work)
    assert why == "" and current is not None
    assert (
        current["packages/x"].value == 70.0 and current["packages/x"].kind == "accept"
    )
    assert current["packages/x"].by == "willem" and current["packages/x"].reason
    assert current["packages/y"].value == 50.0
