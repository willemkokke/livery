"""A check leg's shard: refusals first, then the spelling, the partition and the record.

The store side runs against a bare repository as origin, like the
store's own tests, under a fake CI run so the series may be written.
"""

from __future__ import annotations

import json
import random
import statistics
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from livery import git
from livery.workshop import _shard
from livery.workshop._ci_context import state_store
from workshop_seeds import Seeds, _seed_home, pushed, seed_copier  # noqa: F401

LEG = "check-ubuntu-latest-3.14"


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
    monkeypatch.setenv("GITHUB_RUN_ID", "1013")


def _stamp(hours_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat(
        timespec="microseconds"
    )


def _moment(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp)


def _rows(work: Path, leg: str, rows: dict[str, tuple[str, dict[str, float]]]) -> None:
    """Write *rows* onto *leg*'s durations series: ``name -> (when, files)``."""
    files = {
        name: json.dumps(
            {"schema": _shard.SCHEMA, "when": when, "run": name, "files": per_file}
        )
        for name, (when, per_file) in rows.items()
    }
    assert (
        git.put(
            state_store(work), _shard.DURATIONS.series(leg).ref, files, message="rows"
        )
        == ""
    )


# --- refusals -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("0of2", "counts from 1"),
        ("3of2", "at most the count"),
        ("2of0", "at least 1"),
        ("two-of-four", "spelled <i>of<n>"),
        ("", "spelled <i>of<n>"),
    ],
)
def test_a_shard_spelling_outside_the_form_refuses_naming_the_text(
    text: str, said: str
) -> None:
    with pytest.raises(ValueError, match=said) as caught:
        _shard.Shard.parse(text)
    assert repr(text) in str(caught.value)


def test_a_shard_built_outside_the_form_refuses() -> None:
    with pytest.raises(ValueError, match="counts from 1"):
        _shard.Shard(0, 1)
    with pytest.raises(ValueError, match="at most the count"):
        _shard.Shard(2, 1)
    with pytest.raises(ValueError, match="at least 1"):
        _shard.Shard(1, 0)


def test_a_shard_of_a_label_whose_tail_is_no_shard_refuses() -> None:
    with pytest.raises(ValueError, match="'0of2'"):
        _shard.shard_of("check-a-3.14-0of2")


def test_a_partition_into_no_shards_refuses() -> None:
    with pytest.raises(ValueError, match="count is 0; a partition has at least one"):
        _shard.partition(["a.py"], {}, 0)


def test_durations_of_an_unreadable_store_are_a_reason_not_a_raise(
    work: Path, tmp_path: Path
) -> None:
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    per_file, rows, why = _shard.read_durations(
        work, LEG, before=datetime.now(UTC) + timedelta(days=1)
    )
    assert (per_file, rows) == ({}, ())
    assert why.startswith(f"the durations/{git.slug(LEG)} series could not be read: ")


def test_durations_of_an_absent_series_are_empty_and_no_reason(work: Path) -> None:
    assert _shard.read_durations(work, LEG, before=datetime.now(UTC)) == ({}, (), "")


def test_a_row_stamped_at_or_after_the_bound_is_left_out(work: Path) -> None:
    _rows(
        work,
        LEG,
        {
            "r1.json": (_stamp(3), {"a.py": 1.0}),
            "r2.json": (_stamp(2), {"a.py": 5.0}),
            "r3.json": (_stamp(1), {"a.py": 9.0}),
        },
    )
    listed = _shard.DURATIONS.series(LEG).rows(state_store(work)).rows
    assert [row.name for row in listed] == ["r3.json", "r2.json", "r1.json"]
    # The bound is the newest row's own stamp: at the bound is out.
    at_newest = datetime.fromisoformat(listed[0].when)
    per_file, rows, why = _shard.read_durations(work, LEG, before=at_newest)
    assert why == ""
    assert rows == ("r2.json", "r1.json")
    assert per_file == {"a.py": 3.0}
    # A bound before every row reads none, and that is no failure.
    assert _shard.read_durations(work, LEG, before=_moment(_stamp(4))) == ({}, (), "")


def test_a_row_without_a_stamp_or_without_files_is_passed_over(work: Path) -> None:
    store = state_store(work)
    ref = _shard.DURATIONS.series(LEG).ref
    files = {
        "unstamped.json": json.dumps({"schema": _shard.SCHEMA, "files": {"a.py": 8.0}}),
        "nofiles.json": json.dumps({"schema": _shard.SCHEMA, "when": _stamp(2)}),
        "junk.json": json.dumps(
            {"schema": _shard.SCHEMA, "when": _stamp(1), "files": {"a.py": "slow"}}
        ),
        "good.json": json.dumps(
            {"schema": _shard.SCHEMA, "when": _stamp(1.5), "files": {"a.py": 2.0}}
        ),
    }
    assert git.put(store, ref, files, message="rows") == ""
    per_file, rows, why = _shard.read_durations(
        work, LEG, before=datetime.now(UTC) + timedelta(hours=1)
    )
    assert why == ""
    # The stamped rows are read, newest first; the unstamped one cannot
    # be placed before the bound, so it is not.
    assert rows == ("junk.json", "good.json", "nofiles.json")
    assert per_file == {"a.py": 2.0}


def test_a_missing_shard_file_is_none_and_a_corrupt_one_a_reason(
    tmp_path: Path,
) -> None:
    assert _shard.read_shard(tmp_path) == (None, "")
    path = tmp_path / _shard.SHARD_FILE
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    left, why = _shard.read_shard(tmp_path)
    assert left is None and "does not parse" in why
    path.write_text("[1, 2]", encoding="utf-8")
    assert _shard.read_shard(tmp_path)[1].endswith("is not a JSON object")
    path.write_text(json.dumps({"shard": "0of2", "digest": "", "before": ""}))
    left, why = _shard.read_shard(tmp_path)
    assert left is None and "'0of2'" in why
    path.write_text(json.dumps({"shard": "1of2", "digest": "", "before": "yesterday"}))
    left, why = _shard.read_shard(tmp_path)
    assert left is None and "'yesterday'" in why


# --- the spelling -------------------------------------------------------------


def test_a_shard_spells_and_parses_as_index_of_count() -> None:
    shard = _shard.Shard.parse("2of4")
    assert (shard.index, shard.count) == (2, 4)
    assert str(shard) == "2of4"
    assert not shard.first
    assert _shard.Shard.parse("1of4").first
    assert _shard.Shard(1, 1) == _shard.ONE and str(_shard.ONE) == "1of1"
    assert _shard.ONE.first


def test_the_platform_leg_is_the_label_without_its_shard() -> None:
    assert (
        _shard.platform_leg("check-windows-latest-3.14-2of4")
        == "check-windows-latest-3.14"
    )
    assert (
        _shard.platform_leg("check-windows-latest-3.14") == "check-windows-latest-3.14"
    )
    assert (
        _shard.platform_leg("check-ubuntu-latest-3.14t") == "check-ubuntu-latest-3.14t"
    )
    assert _shard.shard_of("check-windows-latest-3.14-2of4") == _shard.Shard(2, 4)
    assert _shard.shard_of("check-windows-latest-3.14") == _shard.ONE


# --- the partition ------------------------------------------------------------

_DURATIONS = {
    "big.py": 10.0,
    "mid_a.py": 4.0,
    "mid_b.py": 4.0,
    "small.py": 1.0,
    "tiny.py": 0.5,
}


def test_every_file_lands_in_exactly_one_shard_longest_first_ties_by_name() -> None:
    files = list(_DURATIONS)
    shards = _shard.partition(files, _DURATIONS, 2)
    assert len(shards) == 2
    assert sorted(name for shard in shards for name in shard) == sorted(files)
    # The longest opens the first shard; the two 4.0s tie and go by
    # name, mid_a before mid_b, each to the shard with the least so far,
    # and the rest follow while the second shard stays under ten.
    assert shards[0][0] == "big.py"
    assert shards[1][:2] == ("mid_a.py", "mid_b.py")
    assert shards == (("big.py",), ("mid_a.py", "mid_b.py", "small.py", "tiny.py"))


def test_a_file_the_durations_do_not_name_takes_their_median_or_one_second() -> None:
    # The median of the named durations is 4.0: an unseen file weighs
    # that much, so it is placed with the 4.0s, after them by name,
    # before the 1.0 and the 0.5.
    shards = _shard.partition([*_DURATIONS, "unseen.py"], _DURATIONS, 3)
    assert shards == (
        ("big.py",),
        ("mid_a.py", "unseen.py"),
        ("mid_b.py", "small.py", "tiny.py"),
    )
    # Without any named duration every file weighs one second, so the
    # files alternate by name.
    assert _shard.partition(["c.py", "a.py", "b.py"], {}, 2) == (
        ("a.py", "c.py"),
        ("b.py",),
    )


def test_the_partition_is_the_same_whatever_the_input_order() -> None:
    files = [f"test_{index:03d}.py" for index in range(60)]
    seconds = {
        name: float((index * 7919) % 97) / 3.0 for index, name in enumerate(files)
    }
    expected = _shard.partition(files, seconds, 4)
    shuffled = list(files)
    for seed in range(5):
        random.Random(seed).shuffle(shuffled)
        assert _shard.partition(shuffled, seconds, 4) == expected
    # A file given twice is one file.
    assert _shard.partition([*files, files[0]], seconds, 4) == expected


def test_the_shards_balance_within_the_largest_file() -> None:
    files = [f"test_{index:03d}.py" for index in range(200)]
    seconds = {
        name: float((index * 7919) % 173) / 2.0 for index, name in enumerate(files)
    }
    for count in (2, 3, 4, 7):
        shards = _shard.partition(files, seconds, count)
        assert len(shards) == count
        sums = [sum(seconds[name] for name in shard) for shard in shards]
        assert max(sums) - min(sums) <= max(seconds.values())
    # More shards than files: the surplus shards are empty, not missing.
    shards = _shard.partition(["a.py", "b.py"], {}, 4)
    assert shards == (("a.py",), ("b.py",), (), ())


def test_the_digest_of_a_file_set_ignores_order_and_repeats() -> None:
    one = _shard.files_digest(["b.py", "a.py"])
    assert one == _shard.files_digest(["a.py", "b.py", "a.py"])
    assert one != _shard.files_digest(["a.py"])
    assert len(one) == 64 and int(one, 16) >= 0


# --- the record ---------------------------------------------------------------


def test_the_durations_family_is_one_series_per_platform_leg() -> None:
    assert _shard.DURATIONS.keys == ("leg",)
    assert _shard.DURATIONS.window == 30
    # The leg's label is made ref-safe, as every family's key part is.
    assert _shard.DURATIONS.series(LEG).ref == (
        f"{git.NAMESPACE}durations/{git.slug(LEG)}"
    )
    assert _shard.DURATIONS.current is not None


def test_the_durations_family_keeps_the_legs_the_matrix_produces(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "scratch"\nrequires-python = ">=3.14"\n'
    )
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nextensions = []\n\n[forge]\nkind = "gitea"\nowner = "owner"\n'
        '\n[ci]\nrunners = ["ubuntu-latest", "windows-latest"]\n'
    )
    assert _shard.DURATIONS.current is not None
    assert _shard.DURATIONS.current(tmp_path) == {
        ("check-ubuntu-latest-3-14",),
        ("check-windows-latest-3-14",),
    }
    # A root whose contract cannot be read tells the janitor nothing.
    (tmp_path / "workshop.toml").write_text("[ci\n")
    assert _shard.DURATIONS.current(tmp_path) is None


def test_the_family_joins_the_series_the_store_lists() -> None:
    from livery.workshop import _series

    assert _shard.DURATIONS in _series.BASE


def test_the_median_per_file_is_over_the_newest_five_rows(work: Path) -> None:
    rows = {
        f"r{index}.json": (
            _stamp(10 - index),
            {"a.py": float(index), "b.py": 2.0 * index}
            | ({"c.py": 7.0} if index > 5 else {}),
        )
        for index in range(1, 8)
    }
    _rows(work, LEG, rows)
    per_file, read, why = _shard.read_durations(
        work, LEG, before=datetime.now(UTC) + timedelta(hours=1)
    )
    assert why == ""
    # The newest five are r7 down to r3; r1 and r2 are older than the five.
    assert read == ("r7.json", "r6.json", "r5.json", "r4.json", "r3.json")
    assert per_file == {
        "a.py": statistics.median([3.0, 4.0, 5.0, 6.0, 7.0]),
        "b.py": statistics.median([6.0, 8.0, 10.0, 12.0, 14.0]),
        # A file only two rows name takes the median of those two.
        "c.py": 7.0,
    }
    # A shard's own label reads its platform leg's series.
    assert (
        _shard.read_durations(
            work, f"{LEG}-2of4", before=datetime.now(UTC) + timedelta(hours=1)
        )[0]
        == per_file
    )


def test_the_checkout_time_is_heads_committer_time(work: Path) -> None:
    when = _shard.checkout_time(work)
    assert when.tzinfo is not None
    spelled = _git(work, "log", "-1", "--format=%cI", "HEAD").strip()
    assert when == datetime.fromisoformat(spelled)


def test_a_record_written_without_the_suites_key_names_no_suite(
    tmp_path: Path,
) -> None:
    path = tmp_path / _shard.SHARD_FILE
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "shard": "1of2",
                "digest": "d",
                "files": ["a.py"],
                "rows": [],
                "before": "2026-10-17T09:30:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    left, why = _shard.read_shard(tmp_path)
    assert why == "" and left is not None
    assert left.suites == ()
    # A record written before attempts were recorded names none.
    assert left.attempt == ""
    # Written again, the record carries the keys.
    assert _shard.marker_fields(left)["suites"] == []
    assert _shard.marker_fields(left)["attempt"] == ""
    # A record left without naming suites names none the same way.
    _shard.leave_shard(
        tmp_path,
        shard=_shard.Shard(1, 2),
        digest="d",
        files=["a.py"],
        rows=[],
        before=datetime(2026, 10, 17, 9, 30, tzinfo=UTC),
    )
    left, why = _shard.read_shard(tmp_path)
    assert why == "" and left is not None and left.suites == ()
    assert left.attempt == ""
    # A shard that ran in the run's second attempt says so.
    _shard.leave_shard(
        tmp_path,
        shard=_shard.Shard(1, 2),
        digest="d",
        files=["a.py"],
        rows=[],
        before=datetime(2026, 10, 17, 9, 30, tzinfo=UTC),
        attempt="2",
    )
    left, why = _shard.read_shard(tmp_path)
    assert why == "" and left is not None and left.attempt == "2"
    assert _shard.marker_fields(left)["attempt"] == "2"


def test_the_shard_file_round_trips_under_the_workshop_directory(
    tmp_path: Path,
) -> None:
    before = datetime(2026, 10, 17, 9, 30, tzinfo=UTC)
    # The directory is made on the way: a fresh checkout has none.
    path = _shard.leave_shard(
        tmp_path,
        shard=_shard.Shard(2, 4),
        digest="ab" * 32,
        files=["b.py", "a.py"],
        rows=["r2.json", "r1.json"],
        before=before,
        suites=["tests", "packages/b"],
    )
    assert path == tmp_path / ".workshop" / "fm-shard.json"
    left, why = _shard.read_shard(tmp_path)
    assert why == "" and left is not None
    assert left.shard == _shard.Shard(2, 4)
    assert left.digest == "ab" * 32
    assert left.files == ("b.py", "a.py")
    assert left.rows == ("r2.json", "r1.json")
    assert left.before == before
    assert left.suites == ("tests", "packages/b")
    assert _shard.marker_fields(left)["suites"] == ["tests", "packages/b"]
