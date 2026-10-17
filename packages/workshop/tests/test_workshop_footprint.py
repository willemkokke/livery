"""The runner footprint: refusals and fallbacks first, then the priced rendering.

An empty window fails by name; a job without a wall time is counted
apart and priced as nothing; a forge other than GitHub, a private
GitHub repository and a GitHub that does not answer print no price;
a runner label of no class is named as unpriced. Then the whole
rendering on a public GitHub repository, from rows with check legs on
three runners, a sharded platform and the jobs on the default runner.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from livery import git
from livery.footman import Failed
from livery.forge import ForgeError, RepoInfo, Repository
from livery.forge.testing import FakeForge
from livery.workshop import _footprint, _metrics
from livery.workshop._ci_context import state_store
from livery.workshop._ci_tasks import footprint_flow
from workshop_seeds import Seeds, _seed_home, pushed, seed_copier  # noqa: F401

_FAILURES = (SystemExit, Failed)

RUNNERS = '["ubuntu-latest", "macos-latest", "windows-latest"]'


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


def _contract(work: Path, *, kind: str = "github") -> None:
    (work / "pyproject.toml").write_text(
        '[project]\nname = "scratch"\nrequires-python = ">=3.14"\n'
    )
    (work / "workshop.toml").write_text(
        f'[workspace]\nextensions = []\n\n[forge]\nkind = "{kind}"\nowner = "owner"\n'
        f"\n[ci]\nrunners = {RUNNERS}\n"
    )


def _forge(
    monkeypatch: pytest.MonkeyPatch, *, private: bool, known: bool = True
) -> Repository:
    """A fake forge the flow's forge handle reaches, and the repository on it."""
    fake = FakeForge()
    repo = fake.create_repo("owner", "work", private=private)
    if not known:
        fake.delete_repo("owner", "work")
    monkeypatch.setattr("livery.workshop._forge_lane.this_forge", lambda _root: fake)
    return repo


def _job(wall: float | None, *, shard: dict[str, Any] | None = None) -> dict[str, Any]:
    """A job's row as the collect joins it: the forge's times, a fold's shard list."""
    row: dict[str, Any] = {
        "conclusion": "success" if wall is not None else "",
        "status": "completed" if wall is not None else "in_progress",
        "complete": wall is not None,
        "queued_ms": 5000.0,
        "wall_ms": wall,
        "steps": [],
    }
    if shard is not None:
        row["shard"] = shard
    return row


def _entry(
    run: int, jobs: dict[str, dict[str, Any]], *, created: str
) -> dict[str, Any]:
    return {
        "schema": _metrics.SCHEMA,
        "run": str(run),
        "sha": f"{run:040d}",
        "created_at": created,
        "event": "push",
        "run_wall_ms": 1000.0,
        "jobs": jobs,
    }


def _put(work: Path, entries: list[dict[str, Any]]) -> None:
    files = {_metrics.run_file(entry["run"]): json.dumps(entry) for entry in entries}
    assert git.put(state_store(work), _metrics.SERIES.ref, files, message="rows") == ""


def _minute(count: float) -> float:
    return count * 60_000.0


# The three runs of the full rendering: check legs on three runners, the
# windows platform folded from two shards with the fold's own row beside
# them, and the gate job and the matrix picker on the default runner.
WINDOWS_FOLD = {
    "shards": [
        {"job": "check (windows-latest, 3.14, 1of2)"},
        {"job": "check (windows-latest, 3.14, 2of2)"},
    ]
}


def _run(run: int, *, created: str) -> dict[str, Any]:
    return _entry(
        run,
        {
            "check (ubuntu-latest, 3.14)": _job(_minute(10)),
            "check (macos-latest, 3.14)": _job(_minute(20)),
            "check (windows-latest, 3.14)": _job(_minute(15), shard=WINDOWS_FOLD),
            "check (windows-latest, 3.14, 1of2)": _job(_minute(15)),
            "check (windows-latest, 3.14, 2of2)": _job(_minute(15)),
            "gate": _job(_minute(5)),
            "legs": _job(_minute(1)),
        },
        created=created,
    )


THREE_RUNS = [
    _run(1, created="2026-10-11T00:00:00Z"),
    _run(2, created="2026-10-13T00:00:00Z"),
    _run(3, created="2026-10-14T00:00:00Z"),
]


# --- the refusals and the fallbacks -----------------------------------------


def test_an_empty_window_fails_naming_what_fills_it(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _contract(work)
    repo = _forge(monkeypatch, private=False)
    with pytest.raises(_FAILURES, match="the metrics window is empty") as caught:
        footprint_flow(work, repo, since=0)
    assert "the gate job of a run on the forge writes one row per run" in str(
        caught.value
    )


def test_a_negative_since_is_refused_before_the_store_is_read(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _contract(work)
    repo = _forge(monkeypatch, private=False)
    with pytest.raises(_FAILURES, match="--since=-1 names no count of runs"):
        footprint_flow(work, repo, since=-1)


def test_a_job_without_a_wall_time_is_counted_apart_and_not_priced() -> None:
    entries = [
        _entry(
            7,
            {
                "check (ubuntu-latest, 3.14)": _job(_minute(2)),
                "gate": _job(None),
                "docs": {"conclusion": "", "status": "queued", "complete": False},
            },
            created="2026-10-11T00:00:00Z",
        )
    ]
    summed = _footprint.footprint(entries, default_runner="ubuntu-latest")
    assert (summed.untimed, summed.jobs, summed.minutes) == (2, 3, 2.0)
    assert summed.lines == (_footprint.Line("ubuntu-latest", "check legs", 1, 2.0),)
    lines = _footprint.render(summed, default_runner="ubuntu-latest")
    assert lines[0] == (
        "  1 run(s) in the window, 2026-10-11 to 2026-10-11, 1.0 day(s); 3 job(s)"
    )
    (untimed,) = [line for line in lines if line.startswith("  no wall time")]
    assert untimed.split()[:5] == ["no", "wall", "time", "2", "-"]
    assert "still running when the gate job collected, or never started" in untimed
    priced = _footprint.price(summed)
    assert f"  {'total':<24} {'':<12} {'':<8} {'':>8} {0.012:>9.2f}" in priced
    assert not any("no wall time" in line for line in priced)


def test_another_forge_prints_the_minutes_and_no_price(
    work: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _contract(work, kind="gitea")
    repo = _forge(monkeypatch, private=False)
    _put(work, THREE_RUNS)
    footprint_flow(work, repo, since=0)
    out = capsys.readouterr().out
    assert (
        "  3 run(s) in the window, 2026-10-11 to 2026-10-14, 3.0 day(s); 18 job(s)"
    ) in out
    assert "USD" not in out
    assert "priced" not in out
    assert _footprint.SOURCE not in out


def test_a_private_github_repository_prints_no_price(
    work: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _contract(work)
    repo = _forge(monkeypatch, private=True)
    _put(work, THREE_RUNS)
    footprint_flow(work, repo, since=0)
    out = capsys.readouterr().out
    assert "  rate: 1980 minutes per 30 days" in out
    assert "USD" not in out
    assert "priced" not in out


def test_a_github_that_does_not_answer_names_why_nothing_is_priced(
    work: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _contract(work)
    _put(work, THREE_RUNS)
    repo = _forge(monkeypatch, private=False, known=False)
    footprint_flow(work, repo, since=0)
    out = capsys.readouterr().out
    assert "  not priced: GitHub knows no repository owner/work" in out
    assert "USD" not in out

    class Unreachable(FakeForge):
        def get_repo(self, owner: str, name: str) -> RepoInfo | None:
            raise ForgeError("connection refused", status=None)

    monkeypatch.setattr(
        "livery.workshop._forge_lane.this_forge", lambda _root: Unreachable()
    )
    footprint_flow(work, repo, since=0)
    out = capsys.readouterr().out
    assert (
        "  not priced: GitHub did not say whether owner/work is public: connection"
        " refused"
    ) in out
    assert "USD" not in out


def test_a_runner_label_of_no_class_is_named_as_unpriced() -> None:
    entries = [
        _entry(
            9,
            {
                "check (self-hosted-arm, 3.14)": _job(_minute(10)),
                "check (ubuntu-latest, 3.14)": _job(_minute(10)),
            },
            created="2026-10-11T00:00:00Z",
        )
    ]
    summed = _footprint.footprint(entries, default_runner="ubuntu-latest")
    assert _footprint.runner_class("self-hosted-arm") == ""
    assert [line.label for line in summed.unpriced] == ["self-hosted-arm"]
    priced = _footprint.price(summed)
    assert (
        f"  {'self-hosted-arm':<24} {'check legs':<12} unpriced: the label names no"
        " class (ubuntu or linux, windows, macos)"
    ) in priced
    # The unpriced label adds nothing: the total is the ubuntu minutes alone.
    assert f"  {'total':<24} {'':<12} {'':<8} {'':>8} {0.06:>9.2f}" in priced


# --- the whole rendering on a public GitHub repository ----------------------


def test_a_public_github_repository_renders_the_minutes_and_the_price(
    work: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _contract(work)
    repo = _forge(monkeypatch, private=False)
    _put(work, THREE_RUNS)
    footprint_flow(work, repo, since=0)
    lines = capsys.readouterr().out.splitlines()
    # Per run: ubuntu 10, macos 20, two windows shards of 15 (the fold's
    # own row is not a job the forge ran), the gate 5 and the picker 1
    # on ubuntu: 66 minutes over 6 jobs; three runs over three days.
    assert lines[:3] == [
        "  3 run(s) in the window, 2026-10-11 to 2026-10-14, 3.0 day(s); 18 job(s)",
        "  a job outside the check matrix is counted on ubuntu-latest, the first of"
        " [ci] runners, where the workflow places it",
        f"  {'label':<24} {'kind':<12} {'jobs':>6} {'minutes':>9}",
    ]
    assert [line.split() for line in lines[3:8]] == [
        ["macos-latest", "check", "legs", "3", "60.0"],
        ["ubuntu-latest", "check", "legs", "3", "30.0"],
        ["ubuntu-latest", "other", "jobs", "6", "18.0"],
        ["windows-latest", "check", "legs", "6", "90.0"],
        ["total", "18", "198.0"],
    ]
    assert lines[8] == "  rate: 1980 minutes per 30 days"
    assert lines[9] == (
        "  a public repository on GitHub is not billed for standard runners; the"
        " same minutes on a private one, at GitHub's rates:"
    )
    # macos 60 * 0.062 = 3.72, linux 48 * 0.006 = 0.288, windows 90 * 0.010 = 0.9.
    assert [line.split() for line in lines[11:16]] == [
        ["macos-latest", "check", "legs", "macos", "0.062", "3.72"],
        ["ubuntu-latest", "check", "legs", "linux", "0.006", "0.18"],
        ["ubuntu-latest", "other", "jobs", "linux", "0.006", "0.11"],
        ["windows-latest", "check", "legs", "windows", "0.010", "0.90"],
        ["total", "4.91"],
    ]
    assert lines[16] == (
        "  rate: 49.08 USD per 30 days; larger runners cost more per minute"
    )
    assert lines[17] == f"  source: {_footprint.SOURCE}"
    assert len(lines) == 18


def test_since_reads_the_newest_runs_alone(
    work: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _contract(work)
    repo = _forge(monkeypatch, private=False)
    _put(work, THREE_RUNS)
    footprint_flow(work, repo, since=2)
    out = capsys.readouterr().out
    # Runs 2 and 3, a day apart: 132 minutes over one day.
    assert (
        "  2 run(s) in the window, 2026-10-13 to 2026-10-14, 1.0 day(s); 12 job(s)"
    ) in out
    assert "  rate: 3960 minutes per 30 days" in out


def test_a_span_under_a_day_is_taken_as_one() -> None:
    entries = [
        _run(1, created="2026-10-11T00:00:00Z"),
        _run(2, created="2026-10-11T12:00:00Z"),
    ]
    summed = _footprint.footprint(entries, default_runner="ubuntu-latest")
    assert summed.span_days == 1.0
    assert summed.per_30_days(summed.minutes) == 132.0 * 30.0
