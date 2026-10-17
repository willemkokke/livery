"""The gate job's fold: the refusals first, then the fold and what reads it.

The store side runs against a bare repository as origin, like the
metrics tests; the forge side of the collect is a hand-made repository
whose jobs are the assertion surface.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import pytest

from livery import git
from livery.footman import Failed
from livery.forge import Conclusion
from livery.workshop import _ci_context, _fold, _metrics, _shard, _speed, _verified
from livery.workshop._ci_context import state_store
from livery.workshop._queries import Folder
from workshop_seeds import Seeds, _seed_home, pushed, seed_copier  # noqa: F401

RUN = _ci_context.RunContext("gitea", "1013", "push", "refs/heads/main")
WINDOWS = "check-windows-latest-3.14"
WIN1 = f"{WINDOWS}-1of2"
WIN2 = f"{WINDOWS}-2of2"
UBUNTU = "check-ubuntu-latest-3.14"
DISPLAY = {
    WINDOWS: "check (windows-latest, 3.14)",
    WIN1: "check (windows-latest, 3.14, 1of2)",
    WIN2: "check (windows-latest, 3.14, 2of2)",
    UBUNTU: "check (ubuntu-latest, 3.14)",
}
BEFORE = "2026-10-17T09:30:00+00:00"
FILES = ("packages/a/tests/test_a.py", "packages/a/tests/test_b.py", "tests/test_c.py")
SET = _shard.files_digest(FILES)
SUITES = ("packages/a", "tests")

_FAILURES = (SystemExit, Failed)


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
    for name in ("GITHUB_EVENT_PATH", "GITHUB_SHA", "GITHUB_REF", "GITHUB_JOB"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITEA_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_RUN_ID", RUN.run_id)


def _marker(
    shard: str,
    files: Sequence[str],
    *,
    digest: str = SET,
    rows: Sequence[str] = ("r1.json",),
    before: str = BEFORE,
    suites: Sequence[str] = SUITES,
    attempt: str = "",
) -> dict[str, Any]:
    return {
        "shard": shard,
        "digest": digest,
        "files": list(files),
        "rows": list(rows),
        "before": before,
        "suites": list(suites),
        "attempt": attempt,
    }


def _row(
    label: str,
    *,
    scope: str = "full",
    packages: Sequence[str] = (),
    marker: Mapping[str, Any] | None = None,
    sums: Mapping[str, tuple[float, int]] | None = None,
    files: Mapping[str, float] | None = None,
    slowest: Sequence[tuple[str, float]] = (),
    total: float = 100.0,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "job": DISPLAY[label],
        "total_ms": total,
        "tasks": {"check": total},
        "task_steps": {},
        "waits_ms": {},
        "packages": {
            name: {"tests_ms": ms, "tests": tests}
            for name, (ms, tests) in (sums or {}).items()
        },
        "files": dict(files or {}),
        "slowest": [{"test": name, "s": s} for name, s in slowest],
        "scope": {
            "scope": scope,
            "packages": list(packages),
            "leg": label,
            "base_tree": "",
        },
    }
    if marker is not None:
        row["shard"] = dict(marker)
    return row


def _put(
    work: Path,
    label: str,
    row: Mapping[str, Any] | None,
    **files: Mapping[str, Any],
) -> str:
    """Put *row* and *files* on *label*'s per-run ref; its ref."""
    series = _metrics.RUNS.series(RUN.run_id, label)
    rows: dict[str, Mapping[str, Any]] = dict(files)
    if row is not None:
        rows[_metrics.ROW_FILE] = row
    assert series.put(state_store(work), rows, message="a shard's write") == ""
    return series.ref


def _coverage(leg: str, units: Mapping[str, list[str]]) -> dict[str, Any]:
    """A leg's coverage file, each unit's parts named by the shard for the test."""
    return {
        "leg": leg,
        "scope": "full",
        "packages": [],
        "units": {path: {"parts": list(parts)} for path, parts in units.items()},
    }


def _concat(shards: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """A stand-in folder: every unit's parts concatenated in shard order."""
    units: dict[str, list[str]] = {}
    for shard in shards:
        held = cast("dict[str, dict[str, list[str]]]", shard["units"])
        for path, unit in held.items():
            units.setdefault(path, []).extend(unit["parts"])
    return {"leg": _shard.platform_leg(str(shards[0]["leg"])), "units": units}


FOLDERS: dict[str, Folder] = {"coverage.json": _concat}


def _two_shards(
    work: Path, *, second: Mapping[str, Any] | None = None
) -> tuple[str, str]:
    """Windows as two shards of two, every file in one, with coverage files."""
    first = _put(
        work,
        WIN1,
        _row(
            WIN1,
            marker=_marker("1of2", FILES[:1]),
            sums={"a": (1000.0, 3)},
            files={FILES[0]: 1.0},
            slowest=[("t1", 0.9)],
        ),
        **{"coverage.json": _coverage(WIN1, {"packages/a": ["a1"]})},
    )
    other = _put(
        work,
        WIN2,
        second
        if second is not None
        else _row(
            WIN2,
            marker=_marker("2of2", FILES[1:]),
            sums={"a": (500.0, 2), "b": (200.0, 1)},
            files={FILES[1]: 0.3, FILES[2]: 0.2},
            slowest=[("t2", 1.5)],
            total=70.0,
        ),
        **{"coverage.json": _coverage(WIN2, {"packages/a": ["a2"], "tests": ["c2"]})},
    )
    return first, other


def _refs(work: Path) -> set[str]:
    listed = git.list_refs(state_store(work), git.RUN_PREFIX)
    assert listed is not None
    return set(listed)


def _clear(work: Path) -> None:
    """Drop every per-run ref: a put keeps the files a ref already holds."""
    for ref in _refs(work):
        assert git.drop(state_store(work), ref) == ""


def _contract(work: Path, shards: str) -> None:
    (work / "workshop.toml").write_text(
        "[workspace]\nextensions = []\n\n[ci]\n"
        'runners = ["ubuntu-latest", "windows-latest"]\n'
        'python-versions = ["3.14"]\n'
        f"{shards}\n"
    )


# --- the refusals first -----------------------------------------------------


def test_an_unlistable_remote_refuses_naming_it(work: Path, tmp_path: Path) -> None:
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    with pytest.raises(_FAILURES, match="the remote could not be listed; the fold"):
        _fold.fold_run(work, RUN, folders=FOLDERS)


def test_a_shard_the_matrix_produces_that_left_no_ref_refuses_naming_it(
    work: Path,
) -> None:
    _contract(work, 'shards = { "windows-latest" = 2 }')
    # Every shard the matrix produces must write: none did.
    _put(work, UBUNTU, _row(UBUNTU, sums={"a": (10.0, 1)}))
    with pytest.raises(
        _FAILURES,
        match=rf"{WINDOWS}: the matrix splits it into 2 shards \({WIN1}, {WIN2}\)"
        " and none left a ref",
    ):
        _fold.fold_run(work, RUN, folders=FOLDERS)
    # One did: the other is named, and nothing is assumed from it.
    _put(work, WIN1, _row(WIN1, marker=_marker("1of2", FILES[:1])))
    with pytest.raises(
        _FAILURES, match=rf"{WINDOWS}: {WIN2} left no ref \(1 of 2 shards did: {WIN1}\)"
    ):
        _fold.fold_run(work, RUN, folders=FOLDERS)
    # The refs it did read are untouched: the shards' refs stay until
    # the folded ref is put.
    assert _refs(work) == {
        _metrics.run_ref(RUN, UBUNTU),
        _metrics.run_ref(RUN, WIN1),
    }


def test_without_a_matrix_the_records_count_names_the_missing_shard(
    work: Path,
) -> None:
    _put(work, WIN2, _row(WIN2, marker=_marker("2of2", FILES[1:])))
    with pytest.raises(
        _FAILURES, match=rf"{WINDOWS}: {WIN1} left no ref \(1 of 2 shards did: {WIN2}\)"
    ):
        _fold.fold_run(work, RUN, folders=FOLDERS)


def test_a_shard_without_a_row_or_a_record_refuses_naming_it(work: Path) -> None:
    # A shard's ref carrying its coverage file and no row: the leg
    # died before its write.
    ref = _put(work, WIN1, None, **{"coverage.json": _coverage(WIN1, {})})
    _put(work, WIN2, _row(WIN2, marker=_marker("2of2", FILES[1:])))
    with pytest.raises(_FAILURES, match=rf"{WINDOWS}: {ref} left no row"):
        _fold.fold_run(work, RUN, folders=FOLDERS)
    # A row under a shard's label without the record of what it ran.
    _put(work, WIN1, _row(WIN1))
    with pytest.raises(
        _FAILURES, match=rf"{WINDOWS}: {WIN1} left no shard record \(.workshop/fm-shard"
    ):
        _fold.fold_run(work, RUN, folders=FOLDERS)
    # A record that is not one.
    _put(work, WIN1, _row(WIN1, marker={"shard": "0of2"}))
    with pytest.raises(_FAILURES, match=rf"{WINDOWS}: {WIN1}: .*shard record: '0of2'"):
        _fold.fold_run(work, RUN, folders=FOLDERS)
    # A record whose shard is not the label's.
    _put(work, WIN1, _row(WIN1, marker=_marker("2of2", FILES[:1])))
    with pytest.raises(_FAILURES, match=rf"{WINDOWS}: {WIN1} ran as 2of2; a shard's"):
        _fold.fold_run(work, RUN, folders=FOLDERS)


def test_shards_naming_different_scopes_refuse(work: Path) -> None:
    _two_shards(
        work,
        second=_row(
            WIN2,
            scope="affected",
            packages=("packages/a",),
            marker=_marker("2of2", FILES[1:]),
        ),
    )
    with pytest.raises(
        _FAILURES,
        match=rf"{WINDOWS}: the shards name different scopes \({WIN1} full,"
        rf" {WIN2} affected \(packages/a\)\)",
    ):
        _fold.fold_run(work, RUN, folders=FOLDERS)


def test_shards_of_different_sets_rows_or_bounds_refuse_naming_what_differs(
    work: Path,
) -> None:
    other = _shard.files_digest(FILES[1:])
    _two_shards(
        work, second=_row(WIN2, marker=_marker("2of2", FILES[1:], digest=other))
    )
    with pytest.raises(
        _FAILURES,
        match=rf"{WINDOWS}: the shards partitioned different test-file sets"
        rf" \({WIN1} {SET[:12]}, {WIN2} {other[:12]}\)",
    ):
        _fold.fold_run(work, RUN, folders=FOLDERS)
    # The suites partitioned are one more field the shards must agree on.
    _two_shards(
        work,
        second=_row(WIN2, marker=_marker("2of2", FILES[1:], suites=("packages/a",))),
    )
    with pytest.raises(
        _FAILURES,
        match=rf"{WINDOWS}: the shards partitioned different suites"
        rf" \({WIN1} packages/a, tests, {WIN2} packages/a\)",
    ):
        _fold.fold_run(work, RUN, folders=FOLDERS)
    _two_shards(work, second=_row(WIN2, marker=_marker("2of2", FILES[1:], suites=())))
    with pytest.raises(
        _FAILURES,
        match=rf"{WINDOWS}: the shards partitioned different suites"
        rf" \({WIN1} packages/a, tests, {WIN2} none\)",
    ):
        _fold.fold_run(work, RUN, folders=FOLDERS)
    _two_shards(work, second=_row(WIN2, marker=_marker("2of2", FILES[1:], rows=())))
    with pytest.raises(
        _FAILURES,
        match=rf"{WINDOWS}: the shards read different durations rows \({WIN1} r1.json,"
        rf" {WIN2} none\)",
    ):
        _fold.fold_run(work, RUN, folders=FOLDERS)
    later = "2026-10-17T10:00:00+00:00"
    _two_shards(
        work, second=_row(WIN2, marker=_marker("2of2", FILES[1:], before=later))
    )
    with pytest.raises(
        _FAILURES,
        match=rf"{WINDOWS}: the shards read the durations under different bounds"
        rf" \({WIN1} {re.escape(BEFORE)}, {WIN2} {re.escape(later)}\)",
    ):
        _fold.fold_run(work, RUN, folders=FOLDERS)


def _attempted(work: Path, first: dict[str, Any], second: dict[str, Any]) -> None:
    """Windows as two shards whose records are *first* and *second*."""
    _put(work, WIN1, _row(WIN1, marker=first, files={FILES[0]: 1.0}))
    _put(work, WIN2, _row(WIN2, marker=second, files={FILES[1]: 0.3, FILES[2]: 0.2}))


ATTEMPTS = (
    rf"{WINDOWS}: its shards come from different attempts of the run"
    rf" \({WIN1} attempt 1,"
    rf" {WIN2} attempt 2\); a rerun of some jobs partitions afresh, so rerun the"
    r" whole run \(\S+ ci.rerun\)"
)


def test_shards_from_different_attempts_whose_partitions_differ_name_the_attempts(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A rerun of the failed jobs keeps the passing shards' refs from the
    # first attempt, and the rerun shard partitions afresh: whatever
    # differs, the refusal names the attempts and the recovery, since
    # the partitions cannot be reconciled from here.
    one = _marker("1of2", FILES[:1], attempt="1")
    other = _shard.files_digest(FILES[1:])
    cases = [
        _marker("2of2", FILES[1:], digest=other, attempt="2"),
        _marker("2of2", FILES[1:], suites=("packages/a",), attempt="2"),
        _marker("2of2", FILES[1:], rows=("r2.json",), attempt="2"),
        _marker("2of2", FILES[1:], before="2026-10-17T10:00:00+00:00", attempt="2"),
        _marker("2of2", FILES, attempt="2"),
        _marker("2of2", FILES[1:2], attempt="2"),
    ]
    for second in cases:
        _attempted(work, one, second)
        capsys.readouterr()
        with pytest.raises(_FAILURES, match=ATTEMPTS):
            _fold.fold_run(work, RUN, folders=FOLDERS)
        out = capsys.readouterr().out
        assert re.search(rf"^  fold: {ATTEMPTS}$", out, re.MULTILINE), out
    # A record without an attempt reads as attempt "", which differs
    # from a recorded one: the refusal names it as unknown.
    _attempted(work, _marker("1of2", FILES[:1]), cases[0])
    with pytest.raises(
        _FAILURES, match=rf"\({WIN1} attempt unknown, {WIN2} attempt 2\)"
    ):
        _fold.fold_run(work, RUN, folders=FOLDERS)


def test_shards_of_one_attempt_refuse_as_before_and_of_two_attempts_fold_alike(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # One attempt, two partitions: the proof names what differs, as it
    # does for records without an attempt.
    other = _shard.files_digest(FILES[1:])
    _attempted(
        work,
        _marker("1of2", FILES[:1], attempt="2"),
        _marker("2of2", FILES[1:], digest=other, attempt="2"),
    )
    with pytest.raises(
        _FAILURES, match=rf"{WINDOWS}: the shards partitioned different test-file sets"
    ):
        _fold.fold_run(work, RUN, folders=FOLDERS)
    _attempted(
        work,
        _marker("1of2", FILES[:1], attempt="2"),
        _marker("2of2", FILES, attempt="2"),
    )
    with pytest.raises(_FAILURES, match=rf"{WINDOWS}: 1 file\(s\) ran in two shards"):
        _fold.fold_run(work, RUN, folders=FOLDERS)
    # Two attempts with one partition fold: the proof does not care
    # about the attempt by itself.
    _attempted(
        work,
        _marker("1of2", FILES[:1], attempt="1"),
        _marker("2of2", FILES[1:], attempt="2"),
    )
    capsys.readouterr()
    _fold.fold_run(work, RUN, folders={})
    assert capsys.readouterr().out == (
        f"  fold: {WINDOWS}: 2 shard(s) folded, 3 file(s) ran\n"
    )
    assert _refs(work) == {_metrics.run_ref(RUN, WINDOWS)}


def test_a_file_in_two_shards_or_in_none_refuses(work: Path) -> None:
    _two_shards(work, second=_row(WIN2, marker=_marker("2of2", FILES)))
    with pytest.raises(
        _FAILURES,
        match=rf"{WINDOWS}: 1 file\(s\) ran in two shards: {FILES[0]} \({WIN1} and"
        rf" {WIN2}\); every file lands in exactly one shard",
    ):
        _fold.fold_run(work, RUN, folders=FOLDERS)
    _two_shards(work, second=_row(WIN2, marker=_marker("2of2", FILES[1:2])))
    together = _shard.files_digest(FILES[:2])
    with pytest.raises(
        _FAILURES,
        match=rf"{WINDOWS}: the files the shards ran together have digest"
        rf" {together[:12]}, and the set they partitioned has {SET[:12]}: a file",
    ):
        _fold.fold_run(work, RUN, folders=FOLDERS)


def test_a_file_nobody_folds_one_shard_lacks_or_a_folder_refuses_is_red_by_name(
    work: Path,
) -> None:
    _two_shards(work)
    with pytest.raises(
        _FAILURES, match=rf"{WINDOWS}: nobody folds coverage.json: an extension"
    ):
        _fold.fold_run(work, RUN, folders={})
    _clear(work)
    _put(work, WIN1, _row(WIN1, marker=_marker("1of2", FILES[:1])))
    _put(
        work,
        WIN2,
        _row(WIN2, marker=_marker("2of2", FILES[1:])),
        **{"coverage.json": _coverage(WIN2, {})},
    )
    with pytest.raises(
        _FAILURES,
        match=rf"{WINDOWS}: coverage.json is on {WIN2} and not on {WIN1}; a leg's",
    ):
        _fold.fold_run(work, RUN, folders=FOLDERS)

    def refusing(shards: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
        raise ValueError("the units differ")

    _two_shards(work)
    with pytest.raises(
        _FAILURES, match=rf"{WINDOWS}: coverage.json does not fold \(the units differ\)"
    ):
        _fold.fold_run(work, RUN, folders={"coverage.json": refusing})
    # Every refusal left the shards' refs where they were.
    assert _refs(work) == {_metrics.run_ref(RUN, WIN1), _metrics.run_ref(RUN, WIN2)}


def test_a_shard_that_fell_open_beside_one_that_sliced_refuses(work: Path) -> None:
    _two_shards(work, second=_row(WIN2, marker=_marker("1of1", FILES)))
    with pytest.raises(
        _FAILURES,
        match=rf"{WINDOWS}: {WIN2} ran the whole set as one shard of one while {WIN1}"
        " ran a slice",
    ):
        _fold.fold_run(work, RUN, folders=FOLDERS)
    # Both fell open, and one says it ran the whole set while its files
    # are not the set.
    _put(work, WIN1, _row(WIN1, marker=_marker("1of1", FILES[:2])))
    _put(work, WIN2, _row(WIN2, marker=_marker("1of1", FILES)))
    own = _shard.files_digest(FILES[:2])
    with pytest.raises(
        _FAILURES,
        match=rf"{WINDOWS}: {WIN1} says it ran the whole set, and its files' digest"
        rf" {own[:12]} is not the set's {SET[:12]}",
    ):
        _fold.fold_run(work, RUN, folders=FOLDERS)


# --- the fallback: nothing sharded ------------------------------------------


def test_nothing_sharded_folds_nothing_and_says_so(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ref = _put(work, UBUNTU, _row(UBUNTU, sums={"a": (10.0, 1)}))
    before = git.read(state_store(work), ref).files
    _fold.fold_run(work, RUN, folders={})
    assert (
        capsys.readouterr().out
        == "  fold: no check leg ran as shards; nothing to fold\n"
    )
    assert git.read(state_store(work), ref).files == before
    # An empty run says the same.
    assert git.drop(state_store(work), ref) == ""
    _fold.fold_run(work, RUN, folders={})
    assert "nothing to fold" in capsys.readouterr().out


# --- the fold -----------------------------------------------------------------


def test_the_fold_sums_the_shards_rows_folds_their_files_and_drops_their_refs(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first, second = _two_shards(work)
    stamps = {
        label: git.read(state_store(work), ref).files
        for label, ref in ((WIN1, first), (WIN2, second))
    }
    ubuntu = _put(work, UBUNTU, _row(UBUNTU, sums={"a": (10.0, 1)}))
    _fold.fold_run(work, RUN, folders=FOLDERS)
    out = capsys.readouterr().out
    assert out == f"  fold: {WINDOWS}: 2 shard(s) folded, 3 file(s) ran\n"
    folded = _metrics.RUNS.series(RUN.run_id, WINDOWS)
    assert _refs(work) == {ubuntu, folded.ref}
    row, why = folded.row(state_store(work), _metrics.ROW_FILE)
    assert why == "" and row is not None
    # The platform's own display name and label; the first shard's
    # tasks and wall; every shard's sums added and files merged; the
    # slowest tests of every shard, slowest first.
    assert row.data["job"] == DISPLAY[WINDOWS]
    assert row.data["scope"] == {
        "scope": "full",
        "packages": [],
        "leg": WINDOWS,
        "base_tree": "",
    }
    assert row.data["total_ms"] == 100.0 and row.data["tasks"] == {"check": 100.0}
    assert row.data["packages"] == {
        "a": {"tests_ms": 1500.0, "tests": 5},
        "b": {"tests_ms": 200.0, "tests": 1},
    }
    assert row.data["files"] == {FILES[0]: 1.0, FILES[1]: 0.3, FILES[2]: 0.2}
    assert row.data["slowest"] == [{"test": "t2", "s": 1.5}, {"test": "t1", "s": 0.9}]
    shard = row.data["shard"]
    assert shard["count"] == 2 and shard["digest"] == SET
    assert shard["rows"] == ["r1.json"] and shard["before"] == BEFORE
    assert shard["suites"] == list(SUITES)
    listed = shard["shards"]
    assert [item["leg"] for item in listed] == [WIN1, WIN2]
    assert [item["job"] for item in listed] == [DISPLAY[WIN1], DISPLAY[WIN2]]
    assert [item["files"] for item in listed] == [1, 2]
    assert [item["total_ms"] for item in listed] == [100.0, 70.0]
    for item in listed:
        held = stamps[item["leg"]]
        assert held is not None
        assert item["when"] == json.loads(held[_metrics.ROW_FILE])["when"]
    # The coverage file folded by the folder, in shard order.
    coverage, why = folded.row(state_store(work), "coverage.json")
    assert why == "" and coverage is not None
    assert coverage.data["leg"] == WINDOWS
    assert coverage.data["units"] == {"packages/a": ["a1", "a2"], "tests": ["c2"]}
    # Run again: the fold is its own recovery, and finds its work done.
    _fold.fold_run(work, RUN, folders=FOLDERS)
    assert capsys.readouterr().out == f"  fold: {WINDOWS}: already folded, 2 shard(s)\n"
    assert _refs(work) == {ubuntu, folded.ref}


def test_a_fold_that_died_before_its_drops_is_rerun_as_its_own_recovery(
    work: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _first, second = _two_shards(work)
    real = git.drop

    def refusing(store: git.StateStore, ref: str) -> str:
        return (
            "delete refused: the remote hung up" if ref == second else real(store, ref)
        )

    monkeypatch.setattr(git, "drop", refusing)
    _fold.fold_run(work, RUN, folders=FOLDERS)
    out = capsys.readouterr().out
    assert f"  fold: {second}: not dropped (delete refused: the remote hung up)" in out
    folded = _metrics.RUNS.series(RUN.run_id, WINDOWS).ref
    assert _refs(work) == {second, folded}
    # The next run knows the leftover by the stamp it recorded, drops
    # it, and proves nothing again.
    monkeypatch.setattr(git, "drop", real)
    _fold.fold_run(work, RUN, folders=FOLDERS)
    assert capsys.readouterr().out == (
        f"  fold: {WINDOWS}: already folded, 2 shard(s); 1 ref(s) a failed drop"
        " left behind dropped\n"
    )
    assert _refs(work) == {folded}
    # A shard written anew beside the folded ref is a new run of the
    # leg, not a leftover: the fold proves again, and one shard alone
    # proves nothing.
    _put(work, WIN2, _row(WIN2, marker=_marker("2of2", FILES[1:])))
    with pytest.raises(_FAILURES, match=rf"{WINDOWS}: {WIN1} left no ref"):
        _fold.fold_run(work, RUN, folders=FOLDERS)


def test_shards_that_each_fell_open_to_the_whole_set_fold_as_the_first(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    whole: dict[str, float] = dict.fromkeys(FILES, 0.5)
    _put(
        work,
        WIN1,
        _row(
            WIN1,
            marker=_marker("1of1", FILES, rows=()),
            sums={"a": (9.0, 3)},
            files=whole,
        ),
        **{"coverage.json": _coverage(WIN1, {"packages/a": ["a1"]})},
    )
    _put(
        work,
        WIN2,
        _row(
            WIN2,
            marker=_marker("1of1", FILES, rows=()),
            sums={"a": (8.0, 3)},
            files=whole,
        ),
        **{"coverage.json": _coverage(WIN2, {"packages/a": ["a2"]})},
    )
    _fold.fold_run(work, RUN, folders=FOLDERS)
    assert capsys.readouterr().out == (
        f"  fold: {WINDOWS}: 2 shard(s) folded, 3 file(s) ran\n"
        f"  fold: {WINDOWS}: each shard ran the whole set as one shard of one;"
        f" {WIN1} stands for the platform\n"
    )
    folded = _metrics.RUNS.series(RUN.run_id, WINDOWS)
    row, _ = folded.row(state_store(work), _metrics.ROW_FILE)
    assert row is not None and row.data["packages"] == {
        "a": {"tests_ms": 9.0, "tests": 3}
    }
    coverage, _ = folded.row(state_store(work), "coverage.json")
    assert coverage is not None and coverage.data["units"] == {"packages/a": ["a1"]}


def test_the_matrix_s_sharded_platform_folds_and_the_folded_ref_counts_as_present(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _contract(work, 'shards = { "windows-latest" = 2 }')
    _two_shards(work)
    _put(work, UBUNTU, _row(UBUNTU))
    _fold.fold_run(work, RUN, folders=FOLDERS)
    assert "2 shard(s) folded" in capsys.readouterr().out
    # The folded ref stands for the platform the matrix splits.
    _fold.fold_run(work, RUN, folders=FOLDERS)
    assert capsys.readouterr().out == f"  fold: {WINDOWS}: already folded, 2 shard(s)\n"
    # A pull request's matrix is the pull request's: no shards there.
    _contract(
        work,
        'shards = { "windows-latest" = 2 }\npull-request-runners = ["ubuntu-latest"]\n'
        "pull-request-shards = {}",
    )
    pull = _ci_context.RunContext("gitea", "1013", "pull_request", "refs/pull/3/merge")
    assert _fold.expected_shards(work, pull) == {}
    assert _fold.expected_shards(work, RUN) == {WINDOWS: (WIN1, WIN2)}


# --- what reads the folded rows ---------------------------------------------


def _forge(jobs: Sequence[tuple[str, Conclusion, str]]) -> Any:
    """A repository whose one run has *jobs*: name, conclusion, started."""
    from livery.forge._types import Job, Run

    forge_run = Run(
        id=int(RUN.run_id),
        workflow="ci.yml",
        head_sha="d" * 40,
        event="push",
        status="running",
        conclusion="",
        created_at="2026-10-17T10:00:00Z",
        started_at="2026-10-17T10:00:05Z",
    )
    held = tuple(
        Job(index, name, "completed", conclusion, started, "2026-10-17T10:05:00Z", ())
        for index, (name, conclusion, started) in enumerate(jobs, 1)
    )

    class _Checks:
        def runs(self, *, head_sha: str = "", event: str = "") -> tuple[Run, ...]:
            return (forge_run,)

        def jobs(self, run_id: int) -> tuple[Job, ...]:
            return held

    class _Repo:
        checks = _Checks()

    return _Repo()


def test_the_collect_joins_the_folded_row_with_the_first_shard_and_writes_the_durations(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _two_shards(work)
    _put(work, UBUNTU, _row(UBUNTU, sums={"a": (10.0, 1)}, files={FILES[0]: 2.0}))
    _fold.fold_run(work, RUN, folders=FOLDERS)
    capsys.readouterr()
    repo = _forge(
        [
            (DISPLAY[WIN1], "success", "2026-10-17T10:00:10Z"),
            (DISPLAY[WIN2], "failure", "2026-10-17T10:00:20Z"),
            (DISPLAY[UBUNTU], "success", "2026-10-17T10:00:30Z"),
            ("gate", "success", "2026-10-17T10:04:00Z"),
        ]
    )
    lines = _metrics.collect(work, repo, RUN, sha="d" * 40)
    assert (
        f"  {DISPLAY[WINDOWS]}: the forge times of {DISPLAY[WIN1]}, the first of 2"
        " shard(s)" in lines
    )
    assert f"  {_metrics.SERIES.ref}: run 1013 recorded, 5 job(s)" in lines
    windows = _shard.DURATIONS.series(WINDOWS)
    ubuntu = _shard.DURATIONS.series(UBUNTU)
    assert f"  {windows.ref}: 3 file(s) recorded for run 1013" in lines
    assert f"  {ubuntu.ref}: 1 file(s) recorded for run 1013" in lines
    rows = git.read(state_store(work), _metrics.SERIES.ref).files
    assert rows is not None
    entry = json.loads(rows[_metrics.run_file(RUN.run_id)])
    folded = entry["jobs"][DISPLAY[WINDOWS]]
    # The first shard's queue and wall, the shards' worst conclusion,
    # the summed packages, the platform leg, and the shards listed.
    assert folded["queued_ms"] == 10000.0 and folded["wall_ms"] == 290000.0
    assert folded["conclusion"] == "failure"
    assert folded["packages"]["a"] == {"tests_ms": 1500.0, "tests": 5}
    assert folded["scope"]["leg"] == WINDOWS
    assert [item["leg"] for item in folded["shard"]["shards"]] == [WIN1, WIN2]
    # The forge's rows of the shards stand beside it, times alone.
    for label in (WIN1, WIN2):
        assert "scope" not in entry["jobs"][DISPLAY[label]]
        assert entry["jobs"][DISPLAY[label]]["complete"] is True
    assert _speed.forge_shard_row(DISPLAY[WIN1], entry["jobs"][DISPLAY[WIN1]])
    assert not _speed.forge_shard_row(DISPLAY[WINDOWS], folded)
    # One durations row per platform leg, the folded files on the
    # sharded one.
    found = windows.rows(state_store(work))
    assert [row.data["run"] for row in found.rows] == ["1013"]
    assert found.rows[0].data["files"] == {FILES[0]: 1.0, FILES[1]: 0.3, FILES[2]: 0.2}
    found = ubuntu.rows(state_store(work))
    assert found.rows[0].data["files"] == {FILES[0]: 2.0}
    # A red shard keeps the refs for the forge's re-run; green drops them.
    assert _refs(work) == {
        _metrics.run_ref(RUN, WINDOWS),
        _metrics.run_ref(RUN, UBUNTU),
    }


def test_the_stamp_and_the_speed_judge_read_the_platform_leg_past_the_shards_rows(
    work: Path,
) -> None:
    _two_shards(work)
    _put(work, UBUNTU, _row(UBUNTU, sums={"a": (10.0, 1)}))
    _fold.fold_run(work, RUN, folders=FOLDERS)
    repo = _forge(
        [
            (DISPLAY[WIN1], "success", "2026-10-17T10:00:10Z"),
            (DISPLAY[WIN2], "success", "2026-10-17T10:00:20Z"),
            (DISPLAY[UBUNTU], "success", "2026-10-17T10:00:30Z"),
        ]
    )
    lines = _metrics.collect(work, repo, RUN, sha="d" * 40)
    # The folded platform row, its two shards' forge rows and ubuntu's.
    assert f"  {_metrics.SERIES.ref}: run {RUN.run_id} recorded, 4 job(s)" in lines
    # The drop, the gate job's last entry, takes the folded ref too.
    lines = _metrics.drop_halves(work, repo, RUN, sha="d" * 40, job="gate", writers=())
    assert f"  {_metrics.run_ref(RUN, WINDOWS)}: dropped" in lines
    # The stamp names the platform legs: the shards' forge rows carry
    # no scope and block nothing.
    tree = _verified.tree_id(work)
    line = _verified.stamp_from_metrics(work, RUN, sha="d" * 40)
    assert line == f"  verified: tree {tree[:12]} recorded as proved green by run 1013"
    found, why = _verified.record(work, tree)
    assert why == "" and found is not None and found.legs == (UBUNTU, WINDOWS)
    # The speed judge samples the platform leg with the shards' sums.
    verdicts, why_not = _speed.judge_run(work, RUN)
    assert why_not == []
    assert sorted(
        (v.sample.leg, v.sample.package, v.sample.seconds) for v in verdicts
    ) == [
        (UBUNTU, "packages/a", 0.0),
        (WINDOWS, "packages/a", 1.5),
        (WINDOWS, "packages/b", 0.2),
    ]


# --- shards that partitioned nothing ----------------------------------------


def test_shards_none_of_which_partitioned_fold_on_their_scopes_and_files_alone(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A gate that skipped on every shard left no record, and has no partition to prove.

    The shards' one scope and their files are the whole proof; the
    folded row records no digest, rows or bound, and the collect writes
    no durations row for it. One shard with a record beside one
    without is red by name, and without a matrix the labels' count
    names a shard that left no ref.
    """
    _put(
        work,
        WIN1,
        _row(WIN1, scope="verified"),
        **{"coverage.json": _coverage(WIN1, {})},
    )
    _put(
        work,
        WIN2,
        _row(WIN2, scope="verified", total=70.0),
        **{"coverage.json": _coverage(WIN2, {})},
    )
    _fold.fold_run(work, RUN, folders=FOLDERS)
    assert capsys.readouterr().out == (
        f"  fold: {WINDOWS}: 2 shard(s) folded, 0 file(s) ran\n"
        f"  fold: {WINDOWS}: no shard partitioned test files (verified); folded"
        " without a partition\n"
    )
    folded = _metrics.RUNS.series(RUN.run_id, WINDOWS)
    assert _refs(work) == {folded.ref}
    row, why = folded.row(state_store(work), _metrics.ROW_FILE)
    assert why == "" and row is not None
    assert row.data["job"] == DISPLAY[WINDOWS]
    assert (
        row.data["scope"]["leg"] == WINDOWS and row.data["scope"]["scope"] == "verified"
    )
    assert row.data["files"] == {} and row.data["total_ms"] == 100.0
    shard = row.data["shard"]
    assert shard["count"] == 2 and shard["digest"] == "" and shard["suites"] == []
    assert shard["rows"] == [] and shard["before"] == ""
    assert [item["leg"] for item in shard["shards"]] == [WIN1, WIN2]
    coverage, why = folded.row(state_store(work), "coverage.json")
    assert why == "" and coverage is not None
    assert coverage.data["leg"] == WINDOWS and coverage.data["units"] == {}
    repo = _forge(
        [
            (DISPLAY[WIN1], "success", "2026-10-17T10:00:10Z"),
            (DISPLAY[WIN2], "success", "2026-10-17T10:00:20Z"),
        ]
    )
    lines = _metrics.collect(work, repo, RUN, sha="d" * 40)
    assert (
        f"  {DISPLAY[WINDOWS]}: the forge times of {DISPLAY[WIN1]}, the first of 2"
        " shard(s)" in lines
    )
    assert not [line for line in lines if "durations" in line]
    # One shard with a record beside one without.
    _clear(work)
    _put(work, WIN1, _row(WIN1, scope="verified"))
    _put(work, WIN2, _row(WIN2, marker=_marker("2of2", FILES[1:])))
    with pytest.raises(
        _FAILURES,
        match=rf"{WINDOWS}: {WIN1} left no shard record \(.workshop/fm-shard.json"
        rf" rides the leg's row\) and {WIN2} did;",
    ):
        _fold.fold_run(work, RUN, folders=FOLDERS)
    # Without a matrix, the labels' count names the shard that left no ref.
    _clear(work)
    _put(work, WIN1, _row(WIN1, scope="verified"))
    with pytest.raises(
        _FAILURES, match=rf"{WINDOWS}: {WIN2} left no ref \(1 of 2 shards did: {WIN1}\)"
    ):
        _fold.fold_run(work, RUN, folders=FOLDERS)
