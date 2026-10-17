"""The balancer: the reading per platform, the prediction, and the proposal.

The refusals and the edge cases first, then the three platforms of
the measures section proposing 1, 3 and 4.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from livery import git
from livery.workshop import _balance, _metrics
from livery.workshop._ci_context import state_store
from workshop_seeds import Seeds, _seed_home, pushed, seed_copier  # noqa: F401

# The measures section's platforms: F, O and S in seconds, p a ratio.
UBUNTU = _balance.Reading(
    "ubuntu-latest", "3.14", seen=10, rows=10, fixed=31, other=84, tests=534, speed=2.6
)
MACOS = _balance.Reading(
    "macos-latest", "3.14", seen=10, rows=10, fixed=74, other=78, tests=1231, speed=2.2
)
WINDOWS = _balance.Reading(
    "windows-latest",
    "3.14",
    seen=10,
    rows=10,
    fixed=69,
    other=84,
    tests=2514,
    speed=2.9,
)
RUNNERS = ["ubuntu-latest", "macos-latest", "windows-latest"]


def _job(
    *,
    scope: str = "full",
    conclusion: str = "success",
    fixed: float = 31_000.0,
    call: float = 300_000.0,
    test_ms: float = 205_385.0,
    other: dict[str, float] | None = None,
    packages: dict[str, float] | None = None,
    leg: str = "",
    test_step: bool = True,
) -> dict[str, Any]:
    """One check job's row: the forge's steps, the gate's tasks, the package sums."""
    tasks = {"check": call}
    if test_step:
        tasks["check/test.pytest"] = test_ms
    tasks.update({"check/typecheck.mypy": 84_000.0} if other is None else other)
    return {
        "conclusion": conclusion,
        "scope": {"scope": scope, "leg": leg},
        "steps": [
            {"name": "Set up job", "ms": fixed / 2},
            {"name": "Enter the workspace", "ms": fixed / 2},
            {"name": "Check", "ms": call},
            {"name": "Complete job", "ms": None},
        ],
        "tasks": tasks,
        "packages": {
            name: {"tests_ms": ms, "tests": 3}
            for name, ms in (
                packages if packages is not None else {"workshop": 534_000.0}
            ).items()
        },
    }


def _row(run: int, jobs: dict[str, dict[str, Any]], *, day: int = 17) -> dict[str, Any]:
    return {
        "schema": _metrics.SCHEMA,
        "run": str(run),
        "created_at": f"2026-10-{day:02d}T10:00:00Z",
        "jobs": jobs,
    }


def _ubuntu_rows(count: int, **job: Any) -> list[dict[str, Any]]:
    return [
        _row(100 - n, {"check (ubuntu-latest, 3.14)": _job(**job)})
        for n in range(count)
    ]


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture
def work(seeds: Seeds) -> Path:
    """A clone of a bare origin, one commit on main, pushed."""
    return seeds("pushed", pushed) / "work"


@pytest.fixture(autouse=True)
def _in_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    # A fake run, so the store reads origin and not the mirror a sync
    # would have fetched; the runner's own payload and refs scrubbed.
    for name in ("GITHUB_EVENT_PATH", "GITHUB_SHA", "GITHUB_REF", "GITHUB_JOB"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITEA_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_RUN_ID", "1013")


# --- the reading: refusals first ---------------------------------------------


def test_fewer_than_three_full_rows_keeps_the_count_and_says_so() -> None:
    (reading,) = _balance.readings(_ubuntu_rows(2))
    assert (reading.seen, reading.rows) == (2, 2)
    assert not reading.enough
    lines = _balance.report([reading], cap=4, runners=["ubuntu-latest"])
    assert "  ubuntu-latest, 3.14: keeps its count: 2 full green row(s), 3 needed" in (
        lines
    )
    assert (
        "  nothing proposed: no platform has 3 full green rows with the test steps'"
        " wall"
    ) in lines
    assert not any(line.startswith("  [ci] shards") for line in lines)


def test_a_row_without_the_test_steps_wall_is_seen_and_not_read() -> None:
    rows = _ubuntu_rows(2) + _ubuntu_rows(3, test_step=False)
    (reading,) = _balance.readings(rows)
    assert (reading.seen, reading.rows) == (5, 2)
    lines = _balance.report([reading], cap=4, runners=["ubuntu-latest"])
    assert (
        "  ubuntu-latest, 3.14: keeps its count: 5 full green row(s), 2 with the"
        " test steps' wall (check/test.*), 3 needed"
    ) in lines


def test_a_runner_the_contract_names_without_a_row_keeps_its_count() -> None:
    lines = _balance.report([UBUNTU], cap=4, runners=["ubuntu-latest", "macos-latest"])
    assert "  macos-latest: keeps its count: 0 full green row(s), 3 needed" in lines


def test_an_affected_or_red_row_is_not_read() -> None:
    rows = (
        _ubuntu_rows(1, scope="affected", packages={"workshop": 9_000_000.0})
        + _ubuntu_rows(1, conclusion="failure", packages={"workshop": 9_000_000.0})
        + _ubuntu_rows(3)
    )
    (reading,) = _balance.readings(rows)
    assert (reading.seen, reading.rows) == (3, 3)
    assert reading.tests == 534.0


def test_a_job_that_is_not_a_check_leg_is_skipped() -> None:
    rows = _ubuntu_rows(3)
    for row in rows:
        row["jobs"]["gate"] = {"conclusion": "success", "wall_ms": 1.0}
        row["jobs"]["docs"] = _job()
    (reading,) = _balance.readings(rows)
    assert reading.os == "ubuntu-latest"


def test_the_newest_ten_full_green_rows_are_read() -> None:
    old = _ubuntu_rows(3, packages={"workshop": 9_000_000.0})
    (reading,) = _balance.readings(_ubuntu_rows(10) + old)
    assert (reading.seen, reading.rows) == (10, 10)
    assert reading.tests == 534.0


def test_a_gitlab_job_reads_its_platform_from_the_rows_leg() -> None:
    rows = [_row(n, {"check": _job(leg="check-ubuntu-latest-3.14")}) for n in range(3)]
    (reading,) = _balance.readings(rows)
    assert (reading.os, reading.python) == ("ubuntu-latest", "3.14")


# --- the fold ----------------------------------------------------------------


def test_shards_of_one_platform_fold_into_one_reading() -> None:
    # The first shard carries the setup and the unsplit steps; the
    # second shard's own steps are not the platform's F or O.
    rows = [
        _row(
            n,
            {
                "check (ubuntu-latest, 3.14, 2of2)": _job(
                    fixed=99_000.0,
                    other={"check/typecheck.mypy": 999_000.0},
                    test_ms=100_000.0,
                    packages={"forge": 300_000.0},
                ),
                "check (ubuntu-latest, 3.14, 1of2)": _job(
                    fixed=31_000.0,
                    other={"check/typecheck.mypy": 84_000.0},
                    test_ms=100_000.0,
                    packages={"workshop": 200_000.0},
                ),
            },
        )
        for n in range(3)
    ]
    (reading,) = _balance.readings(rows)
    assert (reading.seen, reading.rows) == (3, 3)
    assert (reading.fixed, reading.other, reading.tests) == (31.0, 84.0, 500.0)
    # p is the median over shards of each shard's own S over its test wall.
    assert reading.speed == 2.5


def test_a_steps_own_command_is_not_a_gate_step_and_the_test_phase_sums() -> None:
    # A step's own command rides under check/ too, spelled as the
    # command; it is not the gate's step and never O. Two steps of the
    # test phase make one test wall.
    rows = _ubuntu_rows(
        3,
        other={
            "check/typecheck.mypy": 84_000.0,
            "check/mypy --platform=linux": 80_000.0,
            "check/pytest packages/workshop/tests": 900_000.0,
            "check/test.ctest": 100_000.0,
        },
        test_ms=100_000.0,
        packages={"workshop": 500_000.0},
    )
    (reading,) = _balance.readings(rows)
    assert (reading.other, reading.speed) == (84.0, 2.5)


def test_a_platform_whose_shard_is_red_or_narrowed_is_not_read_for_that_row() -> None:
    rows = [
        _row(
            n,
            {
                "check (ubuntu-latest, 3.14, 1of2)": _job(packages={"a": 1.0}),
                "check (ubuntu-latest, 3.14, 2of2)": _job(
                    conclusion="failure" if n else "success",
                    scope="full" if n < 2 else "affected",
                    packages={"b": 1.0},
                ),
            },
        )
        for n in range(3)
    ]
    (reading,) = _balance.readings(rows)
    assert (reading.seen, reading.rows) == (1, 1)


# --- the prediction -----------------------------------------------------------


def test_the_wall_floors_at_the_setup_and_the_longest_other_step() -> None:
    assert _balance.wall(WINDOWS, 1) == pytest.approx(935.9, abs=0.1)
    assert _balance.wall(WINDOWS, 4) == pytest.approx(285.7, abs=0.1)
    # Past the point where the tests fit under O, more shards save nothing.
    assert _balance.wall(UBUNTU, 3) == _balance.wall(UBUNTU, 4) == 115.0
    assert _balance.runner_seconds(WINDOWS, 4) == pytest.approx(1226.9, abs=0.1)


# --- the proposal: the rules ------------------------------------------------


def test_the_slowest_platforms_count_stops_at_the_first_step_saving_less_than_f() -> (
    None
):
    # Windows: 936, 502, 358, 286, 242 s; the fifth shard saves 43 s
    # against a setup of 69 s, so the count stops at 4 whatever the cap.
    assert _balance.propose([WINDOWS], cap=5).counts == {"windows-latest": 4}
    assert _balance.propose([WINDOWS], cap=3).counts == {"windows-latest": 3}
    # A second shard that saves less than the setup keeps one.
    light = _balance.Reading("a", "3.14", 3, 3, fixed=100, other=10, tests=150, speed=1)
    assert _balance.propose([light], cap=4).counts == {"a": 1}


def test_every_other_platform_takes_the_smallest_count_within_the_tolerance() -> None:
    proposal = _balance.propose([UBUNTU, MACOS, WINDOWS], cap=4)
    assert proposal.counts == {
        "ubuntu-latest": 1,
        "macos-latest": 3,
        "windows-latest": 4,
    }
    assert proposal.slowest == "windows-latest, 3.14"
    assert proposal.wall == pytest.approx(285.7, abs=0.1)
    assert proposal.unsharded == pytest.approx(935.9, abs=0.1)
    assert proposal.capped == ()


def test_a_platform_that_cannot_reach_the_tolerance_at_the_cap_is_named() -> None:
    # Its longest other step holds its wall at 400 s at every count,
    # over 15% above the slowest platform's 286 s.
    flat = _balance.Reading(
        "flat", "3.14", 3, 3, fixed=100, other=300, tests=100, speed=1
    )
    proposal = _balance.propose([WINDOWS, flat], cap=4)
    assert proposal.counts == {"windows-latest": 4, "flat": 4}
    assert proposal.capped == ("flat, 3.14",)
    lines = _balance.report([WINDOWS, flat], cap=4, runners=["windows-latest", "flat"])
    assert "  flat, 3.14 takes the cap, 4: 400 s is not within 15% of 286 s" in lines


def test_a_platform_without_enough_rows_takes_no_part() -> None:
    short = _balance.Reading(
        "windows-latest", "3.14", 2, 2, fixed=69, other=84, tests=2514, speed=2.9
    )
    proposal = _balance.propose([UBUNTU, MACOS, short], cap=4)
    assert proposal.counts == {"ubuntu-latest": 1, "macos-latest": 3}
    assert proposal.slowest == "macos-latest, 3.14"


def test_the_pull_request_counts_apply_over_the_narrower_runner_list() -> None:
    lines = _balance.report(
        [UBUNTU, MACOS, WINDOWS],
        cap=4,
        runners=RUNNERS,
        pull_request_runners=["ubuntu-latest", "macos-latest"],
    )
    assert '  [ci] shards = { "macos-latest" = 3, "windows-latest" = 4 }' in lines
    assert "  predicted run wall: 286 s for the slowest leg, from 936 s" in lines
    # macos is the slowest of the two: 634, 354, 261, 214 s; the fourth
    # shard saves 47 s against a setup of 74 s.
    assert '  [ci] pull-request-shards = { "macos-latest" = 3 }' in lines
    assert "  predicted pull request wall: 261 s for the slowest leg, from 634 s" in (
        lines
    )


def test_the_pull_request_counts_are_the_counts_over_the_same_runners() -> None:
    lines = _balance.report(
        [UBUNTU, MACOS, WINDOWS], cap=4, runners=RUNNERS, pull_request_runners=RUNNERS
    )
    assert "  [ci] pull-request-shards: the same" in lines
    assert not any("pull request wall" in line for line in lines)


def test_a_proposal_of_ones_says_so() -> None:
    # Alone, a platform is the slowest: 250 s, then 175 s at two
    # shards, a saving of 75 s against a setup of 100 s.
    light = _balance.Reading("a", "3.14", 3, 3, fixed=100, other=10, tests=150, speed=1)
    lines = _balance.report([light], cap=4, runners=["a"])
    assert "  [ci] shards: none, every platform runs as one shard" in lines
    assert "  predicted run wall: 250 s for the slowest leg, from 250 s" in lines


# --- the store ---------------------------------------------------------------


def _contract(work: Path, pull_request: str = "") -> None:
    (work / "pyproject.toml").write_text(
        '[project]\nname = "scratch"\nrequires-python = ">=3.14"\n'
    )
    (work / "workshop.toml").write_text(
        '[workspace]\nextensions = []\n\n[forge]\nkind = "gitea"\nowner = "owner"\n'
        '\n[ci]\nrunners = ["ubuntu-latest", "macos-latest", "windows-latest"]\n'
        + pull_request
    )


def test_a_store_that_cannot_be_read_prints_why_and_proposes_nothing(
    work: Path, tmp_path: Path
) -> None:
    _contract(work)
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    lines = _balance.render(work, cap=4)
    assert lines[0].startswith("  the metrics series could not be read: ")
    assert lines[1:] == ["  nothing proposed: the record could not be read"]


def test_an_empty_store_proposes_nothing(work: Path) -> None:
    _contract(work)
    assert _balance.render(work, cap=4) == [
        "  no timing rows yet: the gate job writes one per run",
        "  nothing proposed: the record holds no row",
    ]


def test_the_measures_rows_propose_one_three_and_four_through_the_store(
    work: Path,
) -> None:
    _contract(work, 'pull-request-runners = ["ubuntu-latest", "macos-latest"]\n')
    shapes = {
        "ubuntu-latest": (31_000.0, 84_000.0, 534_000.0, 2.6),
        "macos-latest": (74_000.0, 78_000.0, 1_231_000.0, 2.2),
        "windows-latest": (69_000.0, 84_000.0, 2_514_000.0, 2.9),
    }
    files = {}
    for n in range(1, 5):
        jobs = {
            f"check ({os}, 3.14)": _job(
                fixed=fixed,
                other={"check/typecheck.mypy": other},
                test_ms=tests / speed,
                packages={"workshop": tests},
            )
            for os, (fixed, other, tests, speed) in shapes.items()
        }
        files[_metrics.run_file(str(n))] = json.dumps(_row(n, jobs, day=10 + n))
    # An affected row and a red one ride in the window and are not read.
    files[_metrics.run_file("5")] = json.dumps(
        _row(5, {"check (windows-latest, 3.14)": _job(scope="affected")}, day=16)
    )
    files[_metrics.run_file("6")] = json.dumps(
        _row(6, {"check (windows-latest, 3.14)": _job(conclusion="failure")}, day=17)
    )
    assert git.put(state_store(work), _metrics.SERIES.ref, files, message="rows") == ""
    lines = _balance.render(work, cap=4)
    assert lines[0] == (
        "  6 run(s) in the window, 2026-10-11 to 2026-10-17; the newest 10 full"
        " green row(s) per platform are read"
    )
    assert "  windows-latest, 3.14: 4 row(s): F 69 s, O 84 s, S 2514 s, p 2.9" in lines
    table = lines[
        lines.index("  windows-latest, 3.14: 4 row(s): F 69 s, O 84 s, S 2514 s, p 2.9")
        + 1 :
    ][:5]
    assert table[0].split() == ["shards", "wall", "runner", "s"]
    assert table[1].split() == ["1", "936", "s", "1020", "s"]
    assert table[4].split() == ["4", "286", "s", "1227", "s"]
    assert '  [ci] shards = { "macos-latest" = 3, "windows-latest" = 4 }' in lines
    assert '  [ci] pull-request-shards = { "macos-latest" = 3 }' in lines
    assert "  predicted run wall: 286 s for the slowest leg, from 936 s" in lines
