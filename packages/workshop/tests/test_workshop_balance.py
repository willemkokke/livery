"""The balancer: the reading, the prediction, the proposal, the write and the submit.

The refusals and the edge cases first, then the three platforms of
the measures section proposing 1, 3 and 4; the write's refusals and
the rewrite on text, then the submit's refusals and its happy path
against the fake forge.
"""

from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from livery import git
from livery.footman import Failed
from livery.forge.testing import FakeForge
from livery.workshop import _balance, _metrics
from livery.workshop._ci_context import state_store
from livery.workshop._points import check_shards, pull_request_shards
from workshop_seeds import (  # noqa: F401
    Seeds,
    _seed_home,
    pushed,
    seed_copier,
    wire_pushes,
)

_FAILURES = (SystemExit, Failed)

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
    task_steps: dict[str, float] | None = None,
) -> dict[str, Any]:
    """One check job's row: the forge's steps, the gate's tasks, the package sums."""
    tasks = {"check": call}
    if test_step:
        tasks["check/test.pytest"] = test_ms
    tasks.update({"check/typecheck.mypy": 84_000.0} if other is None else other)
    job: dict[str, Any] = {
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
    if task_steps is not None:
        job["task_steps"] = task_steps
    return job


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


def test_the_test_steps_wall_is_read_from_the_rows_task_steps() -> None:
    # A leg's row carries the gate's steps under task_steps, each by
    # its title inside the check task; the test wall is read there.
    rows = _ubuntu_rows(
        3,
        test_step=False,
        task_steps={"check/test.pytest": 100_000.0},
        packages={"workshop": 500_000.0},
    )
    (reading,) = _balance.readings(rows)
    assert (reading.seen, reading.rows, reading.speed) == (3, 3, 5.0)


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
    # command; it is not the gate's step and never in O. The unsplit
    # steps sum into O, and two steps of the test phase make one test
    # wall.
    rows = _ubuntu_rows(
        3,
        other={
            "check/typecheck.mypy": 70_000.0,
            "check/lint.ruff": 14_000.0,
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


def test_the_wall_floors_at_the_setup_and_the_unsplit_steps() -> None:
    # The first shard's slice is lighter by the tests O holds back, so
    # the shards split S / p + O: at one shard, 69 + 866.9 + 84 s.
    assert _balance.wall(WINDOWS, 1) == pytest.approx(1019.9, abs=0.1)
    assert _balance.wall(WINDOWS, 4) == pytest.approx(306.7, abs=0.1)
    # Past the count where the first shard keeps no test, more shards
    # save nothing: its wall is F + O.
    assert _balance.wall(UBUNTU, 4) == _balance.wall(UBUNTU, 5) == 115.0
    assert _balance.runner_seconds(WINDOWS, 4) == pytest.approx(1226.9, abs=0.1)


# --- the proposal: the rules ------------------------------------------------


def test_the_slowest_platforms_count_stops_at_the_first_step_saving_less_than_f() -> (
    None
):
    # Windows: 1020, 544, 386, 307, 259 s; the fifth shard saves 47 s
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
    assert proposal.wall == pytest.approx(306.7, abs=0.1)
    assert proposal.unsharded == pytest.approx(1019.9, abs=0.1)
    assert proposal.capped == ()


def test_a_platform_that_cannot_reach_the_tolerance_at_the_cap_is_named() -> None:
    # Its unsplit steps hold its wall at 400 s from two shards on, over
    # 15% above the slowest platform's 307 s.
    flat = _balance.Reading(
        "flat", "3.14", 3, 3, fixed=100, other=300, tests=100, speed=1
    )
    proposal = _balance.propose([WINDOWS, flat], cap=4)
    assert proposal.counts == {"windows-latest": 4, "flat": 4}
    assert proposal.capped == ("flat, 3.14",)
    lines = _balance.report([WINDOWS, flat], cap=4, runners=["windows-latest", "flat"])
    assert "  flat, 3.14 takes the cap, 4: 400 s is not within 15% of 307 s" in lines


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
    assert "  predicted run wall: 307 s for the slowest leg, from 1020 s" in lines
    # macos is the slowest of the two: 712, 393, 287, 233 s; the fourth
    # shard saves 53 s against a setup of 74 s.
    assert '  [ci] pull-request-shards = { "macos-latest" = 3 }' in lines
    assert "  predicted pull request wall: 287 s for the slowest leg, from 712 s" in (
        lines
    )


def test_the_pull_request_counts_are_the_counts_over_the_same_runners() -> None:
    lines = _balance.report(
        [UBUNTU, MACOS, WINDOWS], cap=4, runners=RUNNERS, pull_request_runners=RUNNERS
    )
    assert "  [ci] pull-request-shards: the same" in lines
    assert not any("pull request wall" in line for line in lines)


def test_a_proposal_of_ones_says_so() -> None:
    # Alone, a platform is the slowest: 260 s, then 180 s at two
    # shards, a saving of 80 s against a setup of 100 s.
    light = _balance.Reading("a", "3.14", 3, 3, fixed=100, other=10, tests=150, speed=1)
    lines = _balance.report([light], cap=4, runners=["a"])
    assert "  [ci] shards: none, every platform runs as one shard" in lines
    assert "  predicted run wall: 260 s for the slowest leg, from 260 s" in lines


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


#: The measures section's platforms as the store's rows carry them: F, O
#: and S in milliseconds, p a ratio.
SHAPES = {
    "ubuntu-latest": (31_000.0, 84_000.0, 534_000.0, 2.6),
    "macos-latest": (74_000.0, 78_000.0, 1_231_000.0, 2.2),
    "windows-latest": (69_000.0, 84_000.0, 2_514_000.0, 2.9),
}


def _measures_files(
    shapes: dict[str, tuple[float, float, float, float]],
) -> dict[str, str]:
    """Four full green rows of *shapes*, then an affected row and a red one."""
    files: dict[str, str] = {}
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
    return files


def _store(work: Path, files: dict[str, str]) -> None:
    assert git.put(state_store(work), _metrics.SERIES.ref, files, message="rows") == ""


def test_the_measures_rows_propose_one_three_and_four_through_the_store(
    work: Path,
) -> None:
    _contract(work, 'pull-request-runners = ["ubuntu-latest", "macos-latest"]\n')
    _store(work, _measures_files(SHAPES))
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
    assert table[1].split() == ["1", "1020", "s", "1020", "s"]
    assert table[4].split() == ["4", "307", "s", "1227", "s"]
    assert '  [ci] shards = { "macos-latest" = 3, "windows-latest" = 4 }' in lines
    assert '  [ci] pull-request-shards = { "macos-latest" = 3 }' in lines
    assert "  predicted run wall: 307 s for the slowest leg, from 1020 s" in lines


# --- the write: refusals first ----------------------------------------------

#: A contract whose [ci] table has comments between its keys and a
#: table after it, as this repository's does.
CONTRACT = """\
[workspace]
extensions = []

[forge]
kind = "gitea"
owner = "owner"

[ci]
# Every tier-1 platform gates.
runners = ["ubuntu-latest", "macos-latest", "windows-latest"]
# A pull request's legs run on two of them.
pull-request-runners = ["ubuntu-latest", "macos-latest"]
# The one status name branch protection requires.
required-context = "gate"

# The refresh, nightly.
[[ci.schedule]]
point = "nightly"
task = "toolroom.refresh"
once = true
"""

SHARDS_COMMENT = (
    "# The shards each runner's check leg runs as; fm ci.balance --write sets them."
)
PULLS_COMMENT = (
    "# The shards a pull request's check legs run as; fm ci.balance --write sets them."
)


def _contract_text(work: Path, text: str) -> None:
    (work / "pyproject.toml").write_text(
        '[project]\nname = "scratch"\nrequires-python = ">=3.14"\n'
    )
    (work / "workshop.toml").write_text(text, encoding="utf-8")


def _lines(work: Path) -> list[str]:
    return (work / "workshop.toml").read_text(encoding="utf-8").splitlines()


def test_a_write_with_no_rows_writes_nothing_and_says_why(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _contract_text(work, CONTRACT)
    before = _lines(work)
    assert _balance.write(work, _balance.read(work)) is None
    assert capsys.readouterr().out == "  nothing written: the record holds no row\n"
    assert _lines(work) == before


def test_a_write_with_too_few_rows_writes_nothing_and_says_why(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _contract_text(work, CONTRACT)
    _store(
        work,
        {
            _metrics.run_file(str(n)): json.dumps(row)
            for n, row in enumerate(_ubuntu_rows(2), start=1)
        },
    )
    before = _lines(work)
    assert _balance.write(work, _balance.read(work)) is None
    assert capsys.readouterr().out == (
        "  nothing written: no platform has 3 full green rows with the test steps'"
        " wall\n"
    )
    assert _lines(work) == before


def test_a_changed_count_whose_wall_moves_within_ten_percent_writes_nothing(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # F 10 s, O 60 s, S 100 s, p 1: the walls are 170, 90, 70 and 70 s,
    # so the proposal is 3; the contract holds 4, and the wall does not
    # move between them. Writing 3 would only start the flapping the
    # hysteresis is there to stop.
    _contract_text(
        work,
        '[workspace]\nextensions = []\n\n[forge]\nkind = "gitea"\nowner = "owner"\n'
        '\n[ci]\nrunners = ["ubuntu-latest"]\nshards = { "ubuntu-latest" = 4 }\n',
    )
    rows = _ubuntu_rows(
        3,
        fixed=10_000.0,
        other={"check/typecheck.mypy": 60_000.0},
        test_ms=100_000.0,
        packages={"workshop": 100_000.0},
    )
    _store(
        work,
        {_metrics.run_file(str(n)): json.dumps(row) for n, row in enumerate(rows, 1)},
    )
    before = _lines(work)
    balance = _balance.read(work)
    assert balance.shards is not None
    assert (balance.shards.current, balance.shards.proposed) == (
        {"ubuntu-latest": 4},
        {"ubuntu-latest": 3},
    )
    assert _balance.write(work, balance) is None
    assert capsys.readouterr().out == (
        "  nothing written: the predicted run wall moves from 70 s to 70 s,"
        " within 10%\n"
    )
    assert _lines(work) == before


def test_a_max_above_the_contracts_cap_refuses_by_name(work: Path) -> None:
    _contract_text(work, CONTRACT)
    _store(work, _measures_files(SHAPES))
    with pytest.raises(_FAILURES) as caught:
        _balance.read(work, cap=6)
    assert str(caught.value).startswith("--max=6 is above [ci] shards-max, 4")


def test_a_key_spelled_as_its_own_table_refuses_by_name(work: Path) -> None:
    _contract_text(work, CONTRACT + '\n[ci.shards]\n"windows-latest" = 2\n')
    _store(work, _measures_files(SHAPES))
    before = _lines(work)
    with pytest.raises(_FAILURES) as caught:
        _balance.write(work, _balance.read(work))
    assert "[ci] shards is not a key of the [ci] table" in str(caught.value)
    assert _lines(work) == before


# --- the write ----------------------------------------------------------------


def test_a_changed_count_over_the_threshold_rewrites_the_two_keys_and_keeps_the_rest(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _contract_text(work, CONTRACT)
    _store(work, _measures_files(SHAPES))
    before = _lines(work)
    written = _balance.write(work, _balance.read(work))
    assert written == {
        "shards": {"macos-latest": 3, "windows-latest": 4},
        "pull-request-shards": {"macos-latest": 3},
    }
    at = before.index('required-context = "gate"') + 1
    assert _lines(work) == [
        *before[:at],
        SHARDS_COMMENT,
        'shards = { "macos-latest" = 3, "windows-latest" = 4 }',
        PULLS_COMMENT,
        'pull-request-shards = { "macos-latest" = 3 }',
        *before[at:],
    ]
    out = capsys.readouterr().out.splitlines()
    assert out == [
        '  [ci] shards: none becomes { "macos-latest" = 3, "windows-latest" = 4 }',
        '  [ci] pull-request-shards: none becomes { "macos-latest" = 3 }',
        "  the predicted run wall moves from 1020 s to 320 s",
    ]
    # The contract reads back as the legs the matrix fans out to.
    assert check_shards(work) == [
        "check-ubuntu-latest-3.14",
        *(f"check-macos-latest-3.14-{i}of3" for i in (1, 2, 3)),
        *(f"check-windows-latest-3.14-{i}of4" for i in (1, 2, 3, 4)),
    ]
    assert pull_request_shards(work) == [
        "check-ubuntu-latest-3.14",
        *(f"check-macos-latest-3.14-{i}of3" for i in (1, 2, 3)),
    ]
    # A second write finds the contract at the counts and leaves it.
    after = _lines(work)
    assert _balance.write(work, _balance.read(work)) is None
    assert capsys.readouterr().out == (
        "  nothing written: every count is the contract's\n"
    )
    assert _lines(work) == after


def test_a_count_of_one_removes_the_key_with_its_comment(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # F 100 s, O 10 s, S 150 s, p 1: a second shard saves 80 s against
    # a setup of 100 s, so the proposal is one; the contract holds 2.
    _contract_text(
        work,
        '[workspace]\nextensions = []\n\n[forge]\nkind = "gitea"\nowner = "owner"\n'
        '\n[ci]\n# One runner.\nrunners = ["ubuntu-latest"]\n# Set by hand.\n'
        'shards = { "ubuntu-latest" = 2 }\n# Traces on.\nprofile = true\n',
    )
    rows = _ubuntu_rows(
        3,
        fixed=100_000.0,
        other={"check/typecheck.mypy": 10_000.0},
        test_ms=150_000.0,
        packages={"workshop": 150_000.0},
    )
    _store(
        work,
        {_metrics.run_file(str(n)): json.dumps(row) for n, row in enumerate(rows, 1)},
    )
    before = _lines(work)
    assert _balance.write(work, _balance.read(work)) == {"shards": {}}
    at = before.index("# Set by hand.")
    assert _lines(work) == [*before[:at], *before[at + 2 :]]
    assert capsys.readouterr().out.splitlines() == [
        '  [ci] shards: { "ubuntu-latest" = 2 } becomes none',
        "  the predicted run wall moves from 180 s to 260 s",
    ]
    assert check_shards(work) == ["check-ubuntu-latest-3.14"]


def _counts(key: str, current: int, proposed: int, before: float, after: float):
    return _balance.Counts(
        key, ("a",), {"a": current}, {"a": proposed}, before=before, after=after
    )


def test_the_judge_writes_nothing_without_a_proposal_or_a_change() -> None:
    verdict = _balance.judge(_balance.Balance([], reason="the record holds no row"))
    assert (verdict.tables, verdict.why) == (
        None,
        "nothing written: the record holds no row",
    )
    same = _balance.Balance([], _counts("shards", 3, 3, 300.0, 300.0))
    assert _balance.judge(same).why == "nothing written: every count is the contract's"


def test_the_judge_reads_the_pull_request_wall_when_only_those_counts_change() -> None:
    shards = _counts("shards", 3, 3, 300.0, 300.0)
    moved = _balance.Balance(
        [], shards, _counts("pull-request-shards", 1, 3, 600.0, 300.0)
    )
    verdict = _balance.judge(moved)
    assert verdict.tables == {"shards": {"a": 3}, "pull-request-shards": {"a": 3}}
    assert (verdict.what, verdict.before, verdict.after) == (
        "pull request",
        600.0,
        300.0,
    )
    still = _balance.Balance(
        [], shards, _counts("pull-request-shards", 2, 3, 300.0, 280.0)
    )
    assert _balance.judge(still).why == (
        "nothing written: the predicted pull request wall moves from 300 s to"
        " 280 s, within 10%"
    )


# --- the rewrite, on text alone ----------------------------------------------


def test_the_rewrite_replaces_a_key_in_place_and_keeps_the_lines_around_it() -> None:
    text = (
        '[ci]\n# a\nrunners = [\n    "a",\n]\n# b\n'
        'shards = {\n  "a" = 2,\n}  # by hand\n# c\nprofile = true\n'
        '\n[[ci.schedule]]\npoint = "nightly"\n'
    )
    out = _balance.rewrite(text, "shards", {"a": 3})
    assert out == text.replace(
        'shards = {\n  "a" = 2,\n}  # by hand', 'shards = { "a" = 3 }  # by hand'
    )
    # The same table again is the same text.
    assert _balance.rewrite(out, "shards", {"a": 3}) == out


def test_the_rewrite_adds_a_missing_key_after_the_last_key_of_the_table() -> None:
    text = (
        '[ci]  # the table\n# a\nrunners = ["a"]\n\n# The refresh.\n'
        '[[ci.schedule]]\npoint = "nightly"\n'
    )
    out = _balance.rewrite(text, "pull-request-shards", {"a": 2})
    assert out == (
        '[ci]  # the table\n# a\nrunners = ["a"]\n'
        f'{PULLS_COMMENT}\npull-request-shards = {{ "a" = 2 }}\n'
        '\n# The refresh.\n[[ci.schedule]]\npoint = "nightly"\n'
    )
    # A table with no key takes it right under the header.
    assert _balance.rewrite("[ci]\n\n[x]\n", "shards", {"a": 2}) == (
        f'[ci]\n{SHARDS_COMMENT}\nshards = {{ "a" = 2 }}\n\n[x]\n'
    )


def test_the_rewrite_adds_the_table_when_the_contract_has_none() -> None:
    assert _balance.rewrite('[workspace]\nname = "x"\n', "shards", {"a": 2}) == (
        f'[workspace]\nname = "x"\n\n[ci]\n{SHARDS_COMMENT}\nshards = {{ "a" = 2 }}\n'
    )
    # An absent key with an empty table is nothing to write.
    for text in ('[workspace]\nname = "x"\n', '[ci]\nrunners = ["a"]\n'):
        assert _balance.rewrite(text, "shards", {}) == text


# --- the submit: refusals first ---------------------------------------------

DAY = date(2026, 10, 17)
BRANCH = "chore/balance-20261017"


@pytest.fixture
def forge(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[FakeForge, Any, list[Path]]:
    """The fake forge as this workspace's, every push reaching it; gate and sync seamed.

    The gate is the submit's own and runs elsewhere; the sync stands in
    for the render, leaving a file that proves it ran before the commit.
    """
    fake = FakeForge()
    fake.create_repo("owner", "scratch", private=True, description="t")
    repo = fake.repository("owner", "scratch")
    monkeypatch.setattr(
        "livery.workshop._forge_lane.this_repository", lambda _root: repo
    )
    wire_pushes(monkeypatch, fake, "owner", "scratch")
    monkeypatch.setattr("livery.workshop._submit._gate", lambda *a, **k: None)
    synced: list[Path] = []

    def rendering(root: Path, **_: object) -> bool:
        synced.append(root)
        (root / "rendered.txt").write_text("rendered\n")
        return True

    monkeypatch.setattr("livery.workshop._sync.sync_checkout", rendering)
    monkeypatch.chdir(work)
    return fake, repo, synced


def _no_forge(monkeypatch: pytest.MonkeyPatch) -> None:
    def refused(_root: Path) -> Any:
        raise AssertionError("the forge was asked")

    monkeypatch.setattr("livery.workshop._forge_lane.this_repository", refused)


def test_a_submit_off_the_default_branch_refuses_by_name(
    work: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _contract_text(work, CONTRACT)
    _store(work, _measures_files(SHAPES))
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "chore: the contract")
    git.create_branch(work, "feat/elsewhere")
    (work / "w.txt").write_text("w\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "feat: elsewhere")
    _no_forge(monkeypatch)
    before = _lines(work)
    _balance.submit(work, on=DAY)
    assert capsys.readouterr().out == (
        "  not on origin's default branch (on feat/elsewhere): nothing is written"
        " or submitted; the balancer submits from the default branch alone\n"
    )
    assert _lines(work) == before
    assert git.current_branch(work) == "feat/elsewhere"
    assert not git.local_branch_exists(work, BRANCH)


def test_a_submit_with_nothing_written_opens_nothing(
    work: Path,
    forge: tuple[FakeForge, Any, list[Path]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, repo, synced = forge
    _contract_text(work, CONTRACT)
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "chore: the contract")
    _git(work, "push", "-q", "origin", "main")
    _balance.submit(work, on=DAY)
    out = capsys.readouterr().out.splitlines()
    assert out[-2:] == [
        "  nothing written: the record holds no row",
        "  no branch, no pull request",
    ]
    assert repo.pr.find_by_head(BRANCH) is None
    assert not git.local_branch_exists(work, BRANCH)
    assert git.current_branch(work) == "main"
    assert synced == []


def test_a_submit_on_a_dirty_tree_refuses_before_any_branch(
    work: Path, forge: tuple[FakeForge, Any, list[Path]]
) -> None:
    _, repo, _synced = forge
    _contract_text(work, CONTRACT)
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "chore: the contract")
    _git(work, "push", "-q", "origin", "main")
    _store(work, _measures_files(SHAPES))
    (work / "loose.txt").write_text("loose\n")
    before = _lines(work)
    with pytest.raises(_FAILURES) as caught:
        _balance.submit(work, on=DAY)
    assert str(caught.value).startswith("the working tree has uncommitted changes")
    assert _lines(work) == before
    assert not git.local_branch_exists(work, BRANCH)
    assert repo.pr.find_by_head(BRANCH) is None


def _origin_holding(work: Path, counts: str) -> None:
    """Push the day's branch to origin holding *counts* in its contract, from main."""
    _git(work, "switch", "-qc", BRANCH, "origin/main")
    text = (work / "workshop.toml").read_text(encoding="utf-8")
    anchor = 'required-context = "gate"\n'
    assert anchor in text
    (work / "workshop.toml").write_text(
        text.replace(anchor, anchor + counts), encoding="utf-8"
    )
    _git(work, "commit", "-qam", "chore(ci): an earlier balance")
    _git(work, "push", "-q", "origin", BRANCH)
    _git(work, "switch", "-q", "main")


def test_a_second_submit_the_same_day_with_the_same_counts_opens_nothing(
    work: Path,
    forge: tuple[FakeForge, Any, list[Path]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, repo, synced = forge
    _contract_text(work, CONTRACT)
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "chore: the contract")
    _git(work, "push", "-q", "origin", "main")
    _store(work, _measures_files(SHAPES))
    _origin_holding(
        work,
        'shards = { "macos-latest" = 3, "windows-latest" = 4 }\n'
        'pull-request-shards = { "macos-latest" = 3 }\n',
    )
    before = _lines(work)
    _balance.submit(work, on=DAY)
    out = capsys.readouterr().out.splitlines()
    assert out[-1] == (
        f"  the day's balance is already submitted: {BRANCH} on origin holds these"
        " counts; nothing is written"
    )
    assert _lines(work) == before
    assert git.current_branch(work) == "main"
    assert repo.pr.find_by_head(BRANCH) is None
    assert synced == []


def test_a_second_submit_the_same_day_with_other_counts_refuses_by_name(
    work: Path, forge: tuple[FakeForge, Any, list[Path]]
) -> None:
    _contract_text(work, CONTRACT)
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "chore: the contract")
    _git(work, "push", "-q", "origin", "main")
    _store(work, _measures_files(SHAPES))
    _origin_holding(work, 'shards = { "windows-latest" = 2 }\n')
    with pytest.raises(_FAILURES) as caught:
        _balance.submit(work, on=DAY)
    assert str(caught.value) == (
        f'{BRANCH} on origin holds other counts ([ci] shards {{ "windows-latest" = 2'
        " }; [ci] pull-request-shards none) than this reading proposes ([ci]"
        ' shards { "macos-latest" = 3, "windows-latest" = 4 }; [ci]'
        ' pull-request-shards { "macos-latest" = 3 }): merge or close its pull'
        " request, then run the balancer again; nothing is written"
    )
    assert git.current_branch(work) == "main"


# --- the submit ---------------------------------------------------------------


def test_a_due_change_is_committed_on_the_days_branch_and_submitted_unarmed(
    work: Path,
    forge: tuple[FakeForge, Any, list[Path]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, repo, synced = forge
    _contract_text(work, CONTRACT)
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "chore: the contract")
    _git(work, "push", "-q", "origin", "main")
    _store(work, _measures_files(SHAPES))
    main = git.head_sha(work)
    _balance.submit(work, on=DAY)
    out = capsys.readouterr().out.splitlines()
    title = "chore(ci): the check legs run as macos-latest 3, windows-latest 4"
    assert f"  on {BRANCH}" in out
    assert "  arming: off - decided by the balancer, which never arms" in out
    assert f"  opened PR #1: {title}" in out
    assert "  submitted unarmed: PR #1 waits for a person" in out
    assert out[-1] == "  back on main"
    # The branch holds one commit on main's tip: the contract and what
    # the sync rendered, so the sync ran before the commit.
    assert synced == [work]
    assert _git(work, "rev-parse", f"{BRANCH}~1").strip() == main
    assert _git(work, "log", "-1", "--format=%s", BRANCH).strip() == title
    assert _git(work, "log", "-1", "--format=%b", BRANCH).strip() == (
        "the predicted run wall moves from 1020 s to 320 s"
    )
    changed = _git(work, "show", "--name-only", "--format=", BRANCH).split()
    assert sorted(changed) == ["rendered.txt", "workshop.toml"]
    held = _git(work, "show", f"{BRANCH}:workshop.toml")
    assert 'shards = { "macos-latest" = 3, "windows-latest" = 4 }' in held
    assert 'pull-request-shards = { "macos-latest" = 3 }' in held
    # The pull request is open and parked: never armed.
    pr = repo.pr.find_by_head(BRANCH)
    assert pr is not None and pr.title == title and pr.state == "open"
    assert not pr.merged and not repo.pr.is_armed(pr.number)
    # The checkout is back where it started, clean, with main untouched.
    assert git.current_branch(work) == "main"
    assert git.head_sha(work) == main
    assert git.is_clean(work)
    # The same day again finds the branch on origin at these counts.
    _balance.submit(work, on=DAY)
    assert capsys.readouterr().out.splitlines()[-1] == (
        f"  the day's balance is already submitted: {BRANCH} on origin holds these"
        " counts; nothing is written"
    )
    assert repo.pr.find_by_head(BRANCH) is not None
    assert pr.number == 1
