"""The test speed marks: the fall-open paths and refusals first, then the ratchet."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from livery.footman import Failed
from livery.workshop import _metrics, _speed, _state

_FAILURES = (SystemExit, Failed)

UBUNTU = "check (ubuntu-latest, 3.14)"
MACOS = "check (macos-latest, 3.14)"
LEG = "check-ubuntu-latest-3.14"
RUN = _state.RunContext("github", "50", "pull_request", "refs/pull/9/merge")


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


def _leg(
    seconds: float,
    *,
    conclusion: str = "success",
    tests: int = 40,
    slowest: list[tuple[str, float]] | None = None,
    package: str = "forge",
) -> dict[str, Any]:
    return {
        "conclusion": conclusion,
        "packages": {package: {"tests_ms": seconds * 1000.0, "tests": tests}},
        "slowest": [{"test": name, "s": s} for name, s in (slowest or [])],
    }


def _entry(run: int, jobs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema": _metrics.SCHEMA,
        "run": str(run),
        "sha": f"{run:040d}",
        "jobs": jobs,
    }


def _store(work: Path, entries: list[dict[str, Any]]) -> None:
    files = {_metrics.run_file(str(e["run"])): json.dumps(e) for e in entries}
    assert _state.put(work, _metrics.SERIES.ref, files, message="rows") == ""


def _history(
    values: list[float], *, job: str = UBUNTU, first_run: int = 40, **leg: Any
) -> list[dict[str, Any]]:
    """Green runs numbered from *first_run*, oldest first, one leg each."""
    return [_entry(first_run + i, {job: _leg(v, **leg)}) for i, v in enumerate(values)]


# --- the switch ------------------------------------------------------------------


def _contract(root: Path, ci: str) -> Path:
    (root / "workshop.toml").write_text(
        '[workspace]\nlayers = ["livery.workshop"]\n\n[forge]\nkind = "gitea"\n'
        f"\n[ci]\n{ci}"
    )
    return root


def test_the_marks_are_off_unless_the_contract_declares_them(tmp_path: Path) -> None:
    # The refusal first: a string is not a switch. Then the default,
    # off, and the one spelling that turns it on.
    with pytest.raises(_FAILURES, match=r"\[ci\] speed-marks must be true or false"):
        _speed.enabled(_contract(tmp_path, 'speed-marks = "yes"\n'))
    assert _speed.enabled(_contract(tmp_path, 'runners = ["ubuntu-latest"]\n')) is False
    assert _speed.enabled(_contract(tmp_path, "speed-marks = false\n")) is False
    assert _speed.enabled(_contract(tmp_path, "speed-marks = true\n")) is True


def test_the_judge_and_the_accept_do_nothing_while_the_marks_are_off(
    work: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop import _speed_tasks

    _contract(work, 'runners = ["ubuntu-latest"]\n')
    monkeypatch.setattr(_speed_tasks, "workspace_root", lambda: work)
    monkeypatch.setattr(_speed_tasks, "run_context", lambda: RUN)

    def _never(_root: Path, _run: object) -> list[str]:
        raise AssertionError("the judge must not read the rows while off")

    monkeypatch.setattr(_speed_tasks, "judge_flow", _never)
    _speed_tasks.speed_judge()
    assert "speed marks are off: declare [ci] speed-marks = true" in (
        capsys.readouterr().out
    )
    with pytest.raises(_FAILURES, match="speed marks are off"):
        _speed_tasks.speed_accept("packages/forge", 90.0, reason="why")
    # Declared, the judge reads the rows: an empty store judges
    # nothing and says so.
    _contract(work, "speed-marks = true\n")
    monkeypatch.setattr(_speed_tasks, "judge_flow", lambda root, run: [])
    _speed_tasks.speed_judge()


# --- the shapes -----------------------------------------------------------------


def test_leg_label_reads_a_matrix_display_name_and_keeps_a_plain_one() -> None:
    assert _speed.leg_label(UBUNTU) == LEG
    assert _speed.leg_label("check (macos-latest, 3.11)") == "check-macos-latest-3.11"
    assert _speed.leg_label("docs") == "docs"
    assert _speed.is_reference(LEG) and not _speed.is_reference(
        "check-macos-latest-3.14"
    )


def test_the_limit_is_the_margin_or_the_floor_whichever_is_higher() -> None:
    sample = _speed.Sample("packages/forge", LEG, 10.0, 3, ())
    small = _speed.Mark("packages/forge", LEG, 10.0, "first", "run 1", "", "")
    assert _speed.Verdict(sample, small).limit == 30.0  # 15 % of 10 s is under 20 s
    big = _speed.Mark("packages/forge", LEG, 400.0, "first", "run 1", "", "")
    assert _speed.Verdict(sample, big).limit == 460.0
    assert _speed.Verdict(sample, None).limit is None
    assert not _speed.Verdict(sample, None).over


# --- nothing to judge by, and nothing to write from -------------------------------


def test_an_empty_store_has_no_marks_and_an_unreadable_one_names_its_reason(
    work: Path, tmp_path: Path
) -> None:
    assert _speed.marks(work) == ({}, "")
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    current, why = _speed.marks(work)
    assert current is None
    assert why.startswith("the speed/marks series could not be read: ")


def test_judge_run_falls_open_on_unreadable_rows_and_says_when_nothing_ran(
    work: Path, tmp_path: Path
) -> None:
    verdicts, lines = _speed.judge_run(work, RUN)
    assert verdicts == []
    assert lines == ["  speed: run 50 left no check leg with tests; nothing to judge"]
    _store(work, [_entry(50, {"docs": {"conclusion": "success"}})])
    verdicts, lines = _speed.judge_run(work, RUN)
    assert verdicts == [] and "nothing to judge" in lines[0]
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    verdicts, lines = _speed.judge_run(work, RUN)
    assert verdicts == []
    assert lines[0].startswith(
        "  speed: not judged (the metrics series could not be read"
    )


def test_a_bad_mark_row_is_skipped_and_the_others_stand(work: Path) -> None:
    ref = _speed.SERIES.ref
    assert (
        _state.put(
            work,
            ref,
            {
                "20260101T000000.000000Z--a--b": "not json",
                "20260101T000001.000000Z--c--d": json.dumps(
                    {
                        "schema": 1,
                        "package": "packages/x",
                        "leg": LEG,
                        "seconds": "slow",
                    }
                ),
            },
            message="junk",
        )
        == ""
    )
    assert (
        _speed.write_mark(
            work,
            package="packages/forge",
            leg=LEG,
            seconds=30.0,
            kind="first",
            by="run 1",
        )
        == ""
    )
    current, why = _speed.marks(work)
    assert why == "" and current is not None
    assert set(current) == {("packages/forge", LEG)}
    assert current[("packages/forge", LEG)].seconds == 30.0


# --- the samples: the run's legs and each leg's green history ------------------


def test_samples_take_the_legs_green_history_and_skip_red_or_unrun_packages() -> None:
    entries = [
        _entry(
            50, {UBUNTU: _leg(52.0, slowest=[("packages/forge/tests/t.py::a", 3.5)])}
        ),
        _entry(49, {UBUNTU: _leg(90.0, conclusion="failure")}),  # red: no sample
        _entry(48, {UBUNTU: _leg(48.0)}),
        _entry(
            47,
            {
                UBUNTU: {
                    "conclusion": "success",
                    "packages": {"forge": {"tests_ms": 0, "tests": 0}},
                }
            },
        ),
        _entry(46, {MACOS: _leg(200.0)}),  # another leg
        _entry(45, {UBUNTU: _leg(47.0)}),
        _entry(44, {UBUNTU: _leg(46.0)}),
        _entry(43, {UBUNTU: _leg(45.0)}),
        _entry(42, {UBUNTU: _leg(44.0)}),  # beyond the sample
    ]
    (sample,) = _speed.samples(entries, "50")
    assert sample.package == "packages/forge" and sample.leg == LEG
    assert sample.seconds == 52.0 and sample.tests == 40
    assert sample.previous == (48.0, 47.0, 46.0, 45.0)
    assert sample.slowest == (("packages/forge/tests/t.py::a", 3.5),)
    assert _speed.samples(entries, "99") == []


def test_the_grown_tests_are_those_over_their_own_median() -> None:
    now = {
        "packages/forge/tests/t.py::slow": 9.0,
        "packages/forge/tests/t.py::same": 2.0,
        "packages/forge/tests/t.py::new": 5.0,
        "packages/toolroom/tests/t.py::other": 30.0,
    }
    earlier = [
        {
            "packages/forge/tests/t.py::slow": 4.0,
            "packages/forge/tests/t.py::same": 2.0,
        },
        {
            "packages/forge/tests/t.py::slow": 5.0,
            "packages/forge/tests/t.py::same": 2.1,
        },
    ]
    assert _speed._grown(now, earlier, "packages/forge") == (
        ("packages/forge/tests/t.py::slow", 4.5, 9.0),
    )


# --- the verdicts, and what they write -----------------------------------------------


def test_no_mark_counts_the_green_runs_then_records_the_median_of_five(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop._speed_tasks import judge_flow

    _store(work, [*_history([40.0, 41.0, 39.0]), _entry(50, {UBUNTU: _leg(42.0)})])
    assert judge_flow(work, RUN) == []
    out = capsys.readouterr().out
    assert (
        "speed packages/forge on check-ubuntu-latest-3.14: 42.0s, no mark yet"
        " (4/5 green runs)" in out
    )
    assert _speed.marks(work) == ({}, "")
    _store(
        work, [*_history([40.0, 41.0, 39.0, 43.0]), _entry(50, {UBUNTU: _leg(42.0)})]
    )
    assert judge_flow(work, RUN) == []
    out = capsys.readouterr().out
    assert "42.0s, no mark yet; this run records 41.0s" in out
    assert "recorded (packages/forge on check-ubuntu-latest-3.14, first 41.0s)" in out
    current, _ = _speed.marks(work)
    assert current is not None
    mark = current[("packages/forge", LEG)]
    assert mark.seconds == 41.0 and mark.kind == "first" and mark.by == "run 50"


def test_one_run_over_warns_naming_the_culprits_and_the_second_in_a_row_is_red(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop._speed_tasks import judge_flow

    assert (
        _speed.write_mark(
            work,
            package="packages/forge",
            leg=LEG,
            seconds=100.0,
            kind="first",
            by="run 1",
        )
        == ""
    )
    slow = [
        ("packages/forge/tests/t.py::heavy", 20.0),
        ("packages/forge/tests/t.py::b", 1.0),
    ]
    earlier = _history(
        [100.0, 101.0], slowest=[("packages/forge/tests/t.py::heavy", 2.0)]
    )
    _store(work, [*earlier, _entry(50, {UBUNTU: _leg(130.0, slowest=slow)})])
    assert judge_flow(work, RUN) == []
    out = capsys.readouterr().out
    assert (
        "speed packages/forge on check-ubuntu-latest-3.14: 130.0s (mark 100.0s first"
        " by run 1, limit 120.0s) OVER (a second run over in a row is red)"
    ) in out
    assert (
        "slowest: packages/forge/tests/t.py::heavy 20.0s,"
        " packages/forge/tests/t.py::b 1.0s" in out
    )
    assert "grew: packages/forge/tests/t.py::heavy 2.0s -> 20.0s" in out
    # The next run over, right after: red, and the mark did not move.
    _store(
        work,
        [
            *earlier,
            _entry(50, {UBUNTU: _leg(130.0, slowest=slow)}),
            _entry(51, {UBUNTU: _leg(125.0)}),
        ],
    )
    red = judge_flow(work, _state.RunContext("github", "51", "push", "refs/heads/main"))
    assert red == [
        "packages/forge on check-ubuntu-latest-3.14: 125.0s over the limit 120.0s for"
        " the second run in a row"
    ]
    assert "OVER for the second run in a row: red" in capsys.readouterr().out
    current, _ = _speed.marks(work)
    assert current is not None and current[("packages/forge", LEG)].seconds == 100.0


def test_a_red_run_between_does_not_make_a_pair_and_the_macos_leg_warns_only(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop._speed_tasks import judge_flow

    for leg in (LEG, "check-macos-latest-3.14"):
        assert (
            _speed.write_mark(
                work,
                package="packages/forge",
                leg=leg,
                seconds=100.0,
                kind="first",
                by="run 1",
            )
            == ""
        )
    # ubuntu: under, then a red run over, then over: the red run is no
    # sample, so run 50 is the first over in a row, a warning.
    _store(
        work,
        [
            _entry(47, {UBUNTU: _leg(100.0)}),
            _entry(48, {UBUNTU: _leg(110.0)}),
            _entry(49, {UBUNTU: _leg(140.0, conclusion="failure")}),
            _entry(50, {UBUNTU: _leg(135.0), MACOS: _leg(300.0)}),
        ],
    )
    assert judge_flow(work, RUN) == []
    out = capsys.readouterr().out
    assert (
        "check-ubuntu-latest-3.14: 135.0s (mark 100.0s first by run 1, limit"
        " 120.0s) OVER (a second run over in a row is red)"
    ) in out
    assert (
        "check-macos-latest-3.14: 300.0s (mark 100.0s first by run 1, limit"
        " 120.0s) OVER (this leg warns only)"
    ) in out


def test_the_mark_ratchets_down_when_the_median_of_five_beats_it(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop._speed_tasks import judge_flow

    assert (
        _speed.write_mark(
            work,
            package="packages/forge",
            leg=LEG,
            seconds=100.0,
            kind="first",
            by="run 1",
        )
        == ""
    )
    # One fast run among slow ones moves nothing.
    _store(
        work, [*_history([99.0, 98.0, 100.0, 97.0]), _entry(50, {UBUNTU: _leg(60.0)})]
    )
    assert judge_flow(work, RUN) == []
    assert "new mark" not in capsys.readouterr().out
    # Five fast runs move the mark to their median.
    _store(
        work, [*_history([90.0, 91.0, 89.0, 92.0]), _entry(50, {UBUNTU: _leg(88.0)})]
    )
    assert judge_flow(work, RUN) == []
    out = capsys.readouterr().out
    assert "new mark: 90.0s (the median of 5 beats 100.0s by more than 5%)" in out
    assert "recorded (packages/forge on check-ubuntu-latest-3.14, ratchet 90.0s)" in out
    current, _ = _speed.marks(work)
    assert current is not None
    assert current[("packages/forge", LEG)].seconds == 90.0
    assert current[("packages/forge", LEG)].kind == "ratchet"


# --- the person's raise ----------------------------------------------------------


def test_accept_refuses_then_raises_the_mark(work: Path, tmp_path: Path) -> None:
    packages = ("packages/forge", "packages/toolroom")

    def accept(
        seconds: float, reason: str, package: str = "packages/forge"
    ) -> list[str]:
        return _speed.accept(
            work,
            package=package,
            leg=LEG,
            seconds=seconds,
            reason=reason,
            by="tester",
            packages=packages,
        )

    with pytest.raises(_FAILURES, match="a reason is required"):
        accept(90.0, " ")
    with pytest.raises(_FAILURES, match="no package at 'packages/x'"):
        accept(90.0, "why", package="packages/x")
    with pytest.raises(_FAILURES, match="a time in seconds"):
        accept(0.0, "why")
    assert (
        _speed.write_mark(
            work,
            package="packages/forge",
            leg=LEG,
            seconds=100.0,
            kind="first",
            by="run 1",
        )
        == ""
    )
    with pytest.raises(_FAILURES, match="does not raise it"):
        accept(100.0, "why")
    assert accept(150.0, "a new heavy test") == [
        "  speed packages/forge on check-ubuntu-latest-3.14: mark 100.0s -> 150.0s"
        " accepted by tester",
        "    reason: a new heavy test",
    ]
    current, _ = _speed.marks(work)
    assert current is not None
    mark = current[("packages/forge", LEG)]
    assert mark.seconds == 150.0 and mark.kind == "accept"
    assert mark.reason == "a new heavy test"
    # A package with no mark yet can still be given one by hand.
    assert accept(20.0, "why", package="packages/toolroom")[0].endswith(
        "mark none -> 20.0s accepted by tester"
    )
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    with pytest.raises(_FAILURES, match="a write from an unread store"):
        accept(200.0, "why")


# --- the readers ---------------------------------------------------------------------


def test_render_marks_shows_each_mark_beside_the_newest_runs_time(
    work: Path, tmp_path: Path
) -> None:
    assert _speed.render_marks(work) == [
        "  speed marks: none yet; the gate job records one per package and leg"
    ]
    assert (
        _speed.write_mark(
            work,
            package="packages/forge",
            leg=LEG,
            seconds=100.0,
            kind="accept",
            by="tester",
        )
        == ""
    )
    _store(work, [_entry(50, {UBUNTU: _leg(130.0)})])
    lines = _speed.render_marks(work)
    assert lines[0] == "  speed marks"
    assert "packages/forge on check-ubuntu-latest-3.14" in lines[2]
    assert (
        "100.0s" in lines[2] and "130.0s" in lines[2] and "accept by tester" in lines[2]
    )
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    assert _speed.render_marks(work)[0].startswith("  speed marks: not read (")


def test_the_plugin_sums_every_phase_per_package_from_the_controller_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _pytest_speed

    _pytest_speed._sums.clear()
    target = tmp_path / "speed.json"

    def report(nodeid: str, when: str, duration: float) -> Any:
        return SimpleNamespace(nodeid=nodeid, when=when, duration=duration)

    def session(*, worker: bool) -> Any:
        config = SimpleNamespace(workerinput={}) if worker else SimpleNamespace()
        return SimpleNamespace(config=config)

    # Without the variable nothing is summed and nothing is written.
    monkeypatch.delenv(_pytest_speed.FILE_VARIABLE, raising=False)
    _pytest_speed.pytest_runtest_logreport(
        report("packages/forge/tests/t.py::a", "call", 2.0)
    )
    _pytest_speed.pytest_sessionfinish(session(worker=False), 0)
    assert not target.exists()
    monkeypatch.setenv(_pytest_speed.FILE_VARIABLE, str(target))
    for when, seconds in (("setup", 0.5), ("call", 2.0), ("teardown", 0.25)):
        _pytest_speed.pytest_runtest_logreport(
            report("packages/forge/tests/t.py::a", when, seconds)
        )
    _pytest_speed.pytest_runtest_logreport(
        report("packages/forge/tests/t.py::b", "call", 1.0)
    )
    _pytest_speed.pytest_runtest_logreport(report("tests/test_root.py::c", "call", 9.0))
    # A worker writes nothing; the controller writes the sums.
    _pytest_speed.pytest_sessionfinish(session(worker=True), 0)
    assert not target.exists()
    _pytest_speed.pytest_sessionfinish(session(worker=False), 0)
    assert json.loads(target.read_text()) == {
        "packages/forge": {"seconds": 3.8, "tests": 2}
    }


def test_speed_lines_print_the_sums_beside_the_marks(
    work: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._backends._python import speed_lines

    monkeypatch.setattr("livery.workshop._points.check_legs", lambda root: [LEG])
    sums = tmp_path / "speed.json"
    assert speed_lines(work, sums) == []  # the plugin did not run
    sums.write_text(json.dumps({"packages/forge": {"seconds": 12.3, "tests": 45}}))
    assert speed_lines(work, sums) == [
        "  speed packages/forge: 12.3s over 45 tests"
        " (no mark on check-ubuntu-latest-3.14)"
    ]
    assert (
        _speed.write_mark(
            work,
            package="packages/forge",
            leg=LEG,
            seconds=14.0,
            kind="first",
            by="run 1",
        )
        == ""
    )
    assert speed_lines(work, sums) == [
        "  speed packages/forge: 12.3s over 45 tests"
        " (mark 14.0s on check-ubuntu-latest-3.14)"
    ]
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    lines = speed_lines(work, sums, leg=LEG)
    assert lines[0].startswith("  speed marks: not read (")
    assert lines[1] == "  speed packages/forge: 12.3s over 45 tests (mark unknown)"
