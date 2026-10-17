"""The verified-tree record: the fallbacks that run the gate, then the skip."""

from __future__ import annotations

import dataclasses
import json
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

from livery import git
from livery.workshop import (
    LegScope,
    VerifiedBranch,
    _ci_context,
    _metrics,
    _quality,
    _verified,
    leg_scope,
    verified_branch,
)
from livery.workshop._ci_context import state_store
from workshop_seeds import Seeds, _seed_home, pushed, seed_copier  # noqa: F401

RUN = _ci_context.RunContext("gitea", "1013", "push", "refs/heads/main")


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


def _trace(path: Path) -> Path:
    events = [
        {"ph": "M", "name": "process_name", "pid": 1, "args": {"name": "fm"}},
        {
            "ph": "X",
            "cat": "task",
            "name": "check",
            "pid": 1,
            "tid": 1,
            "ts": 0,
            "dur": 1000,
        },
    ]
    path.write_text(json.dumps({"traceEvents": events}))
    return path


# --- the fallbacks first: every one runs the gate -----------------------------


def test_an_absent_record_and_an_unknown_tree_are_quiet(work: Path) -> None:
    tree = _verified.tree_id(work)
    assert _verified.record(work, tree) == (None, "")
    assert _verified.stamp(work, RUN, tree=tree, sha="a" * 40, legs=("check",)) == ""
    assert _verified.record(work, "f" * 40) == (None, "")


def _rmtree(path: Path) -> None:
    """Remove *path* whole; git's pack files are read-only, which Windows honours."""
    for entry in path.rglob("*"):
        if entry.is_file():
            entry.chmod(entry.stat().st_mode | stat.S_IWRITE)
    shutil.rmtree(path)


def test_an_unreadable_store_and_a_foreign_entry_name_their_reason(
    work: Path, tmp_path: Path
) -> None:
    tree = _verified.tree_id(work)
    assert (
        git.put(
            state_store(work),
            _verified.SERIES.ref,
            {tree: "not json", "other": json.dumps({"schema": 99})},
            message="junk",
            ci_only=True,
        )
        == ""
    )
    found, why = _verified.record(work, tree)
    assert found is None and "does not parse" in why
    found, why = _verified.record(work, "other")
    assert found is None and "this reader speaks" in why
    _rmtree(tmp_path / "origin.git")
    found, why = _verified.record(work, tree)
    assert found is None and "could not be read" in why


def test_held_answers_for_many_trees_in_one_read_and_falls_open(
    work: Path, tmp_path: Path
) -> None:
    tree = _verified.tree_id(work)
    assert _verified.held(work, [tree, "f" * 40]) == (set(), "")
    assert _verified.stamp(work, RUN, tree=tree, sha="a" * 40, legs=("check",)) == ""
    assert _verified.held(work, [tree, "f" * 40, tree]) == ({tree}, "")
    _rmtree(tmp_path / "origin.git")
    found, why = _verified.held(work, [tree])
    assert found == set() and "could not be read" in why


def test_the_stamp_refuses_outside_ci(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS")
    monkeypatch.delenv("GITEA_ACTIONS")
    why = _verified.stamp(work, RUN, tree="a" * 40, sha="b" * 40, legs=("check",))
    assert "only a CI run writes" in why


def test_leg_scope_is_unknown_when_the_leg_left_no_marker(work: Path) -> None:
    unknown = LegScope("unknown")
    assert leg_scope(work) == unknown
    # A marker that does not parse, or holds no object, says nothing either.
    (work / _verified.MARKER).write_text("junk")
    assert leg_scope(work) == unknown
    (work / _verified.MARKER).write_text('["full"]')
    assert leg_scope(work) == unknown


def test_verified_branch_says_why_an_unreadable_row_decided_nothing(
    work: Path, tmp_path: Path
) -> None:
    # A directory git cannot name a tree in.
    plain = tmp_path / "plain"
    plain.mkdir()
    nameless = verified_branch(plain)
    assert nameless.branch == "" and nameless.reason != ""
    # A row that is not of the record's schema, then a store that is gone:
    # the reason is the record's own.
    tree = _verified.tree_id(work)
    assert (
        git.put(
            state_store(work),
            _verified.SERIES.ref,
            {tree: "not json"},
            message="junk",
            ci_only=True,
        )
        == ""
    )
    _, why = _verified.record(work, tree)
    assert why != ""
    assert verified_branch(work) == VerifiedBranch("", why)
    _rmtree(tmp_path / "origin.git")
    _, why = _verified.record(work, tree)
    assert why != ""
    assert verified_branch(work) == VerifiedBranch("", why)


def test_a_narrowed_leg_or_a_missing_row_leaves_the_tree_unstamped(work: Path) -> None:
    line = _verified.stamp_from_metrics(work, RUN, sha="a" * 40)
    assert line == f"  verified: no stamp, run {RUN.run_id} left no row"
    narrowed = {
        "schema": _metrics.SCHEMA,
        "jobs": {
            "check (a)": {"scope": {"scope": "full", "packages": []}},
            "check (b)": {"scope": {"scope": "affected", "packages": ["packages/x"]}},
        },
    }
    assert (
        git.put(
            state_store(work),
            _metrics.SERIES.ref,
            {_metrics.run_file(RUN.run_id): json.dumps(narrowed)},
            message="rows",
            ci_only=True,
        )
        == ""
    )
    line = _verified.stamp_from_metrics(work, RUN, sha="a" * 40)
    # A narrowed leg composes only with the tree it measured from, and
    # this one named none, so the tree stays unstamped.
    assert "check (b) named no tree its narrowed gate measured from" in line
    assert _verified.record(work, _verified.tree_id(work)) == (None, "")


def test_the_gate_runs_when_the_record_cannot_decide_or_names_a_narrowed_tree(
    work: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "livery.workshop._verified.record",
        lambda root, tree: (None, "the record could not be read: down"),
    )
    assert not _quality.verified_already(work)
    assert "could not be read: down; running the gate" in capsys.readouterr().out
    monkeypatch.setattr(
        "livery.workshop._verified.record",
        lambda root, tree: (
            _verified.Verified(tree, "5", "c" * 40, _verified.AFFECTED, ()),
            "",
        ),
    )
    assert not _quality.verified_already(work)
    assert capsys.readouterr().out == ""


# --- the skip ----------------------------------------------------------------


def test_a_full_stamp_is_read_back_and_skips_the_gate(
    work: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    full = {
        "schema": _metrics.SCHEMA,
        "jobs": {
            "check (a)": {"scope": {"scope": "full", "packages": []}},
            "docs": {"scope": {"scope": "unknown", "packages": []}},
        },
    }
    assert (
        git.put(
            state_store(work),
            _metrics.SERIES.ref,
            {_metrics.run_file(RUN.run_id): json.dumps(full)},
            message="rows",
            ci_only=True,
        )
        == ""
    )
    line = _verified.stamp_from_metrics(work, RUN, sha="a" * 40)
    tree = _verified.tree_id(work)
    assert line == f"  verified: tree {tree[:12]} recorded as proved green by run 1013"
    found, why = _verified.record(work, tree)
    assert why == "" and found is not None
    assert found.scope == _verified.FULL and found.legs == ("check (a)",)
    assert _quality.verified_already(work)
    assert "proved green by run 1013" in capsys.readouterr().out
    # The gate itself: a verified tree ends the run before any step, marker left.
    (work / "workshop.toml").write_text("[workspace]\nextensions = []\n")
    monkeypatch.setattr("livery.workshop._quality.workspace_root", lambda: work)
    monkeypatch.setattr("livery.workshop._ci_context.run_context", lambda: RUN)
    monkeypatch.setattr(
        "livery.workshop._quality._leg_plan",
        lambda root, base, run: pytest.fail("ran"),
    )
    _quality.check()
    assert leg_scope(work).scope == "verified"


def test_verified_branch_names_the_branch_whose_run_proved_the_tree(
    work: Path,
) -> None:
    # No row for the tree: the record names no branch, and says no reason.
    assert verified_branch(work) == VerifiedBranch("")
    tree = _verified.tree_id(work)
    stamped = _verified.stamp(
        work, PULL, tree=tree, sha="a" * 40, legs=("check-a",), branch="feat/x"
    )
    assert stamped == ""
    assert verified_branch(work) == VerifiedBranch("feat/x")
    # Main's own run names no branch.
    assert _verified.stamp(work, RUN, tree=tree, sha="a" * 40, legs=("check-a",)) == ""
    assert verified_branch(work) == VerifiedBranch("")


def test_the_marker_reaches_the_legs_row(work: Path) -> None:
    _verified.write_marker(work, "affected", ("packages/x",))
    assert leg_scope(work) == LegScope("affected", ("packages/x",))
    _verified.write_marker(work, "full", leg="check-a")
    assert leg_scope(work) == LegScope("full", leg="check-a")
    _verified.write_marker(
        work, _verified.AFFECTED, ("packages/x",), base_tree="b" * 40
    )
    trace = _trace(work / "fm-profile.json")
    assert (
        _metrics.put_leg(work, RUN, job="check (a)", label="check-a", trace=trace)[1]
        == ""
    )
    found = git.read(state_store(work), _metrics.run_ref(RUN, "check-a"))
    assert found.files is not None
    row = json.loads(found.files[_metrics.ROW_FILE])
    assert row["scope"] == {
        "scope": "affected",
        "packages": ["packages/x"],
        "leg": "",
        "base_tree": "b" * 40,
    }


# --- the composed stamp: a narrowed run on a verified base --------------------

PULL = _ci_context.RunContext(
    "gitea",
    "1014",
    "pull_request",
    "refs/pull/1/merge",
    base_ref="main",
    head_ref="feat/one",
)


def _rows(
    work: Path,
    run: _ci_context.RunContext,
    scopes: dict[str, str],
    bases: dict[str, str] | None = None,
    labels: dict[str, str] | None = None,
) -> None:
    """The run's metrics row: one check leg per name, with the scope it left.

    *bases* names, per leg, the tree its narrowed gate measured from;
    *labels* the leg label a job's marker carried, under ``scope.leg``.
    """
    entry = {
        "schema": _metrics.SCHEMA,
        "jobs": {
            name: {
                "scope": {
                    "scope": scope,
                    "packages": [],
                    "base_tree": (bases or {}).get(name, ""),
                    "leg": (labels or {}).get(name, ""),
                }
            }
            for name, scope in scopes.items()
        },
    }
    assert (
        git.put(
            state_store(work),
            _metrics.SERIES.ref,
            {_metrics.run_file(run.run_id): json.dumps(entry)},
            message="rows",
            ci_only=True,
        )
        == ""
    )


def _branch_commit(work: Path, name: str) -> None:
    """A commit on a new branch off the checkout, as a pull request run sees it."""
    _git(work, "checkout", "-q", "-b", name)
    (work / f"{name.replace('/', '-')}.txt").write_text(f"{name}\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", f"feat: {name}")


def test_a_narrowed_run_stays_unstamped_without_a_verified_base(work: Path) -> None:
    # A leg that left no scope: no stamp, the leg named.
    _rows(work, PULL, {"check (a)": "affected", "check (b)": "unknown"})
    line = _verified.stamp_from_metrics(work, PULL, sha="a" * 40)
    assert line.startswith("  verified: no stamp") and "check (b) left no scope" in line
    # Narrowed legs on a base the record does not name: no stamp, the
    # base named, so a reader knows which tree's proof is missing.
    _branch_commit(work, "feat/one")
    base = _verified.tree_id(work, "origin/main")
    tree = _verified.tree_id(work)
    both = {"check (a)": base, "check (b)": base}
    _rows(work, PULL, {"check (a)": "affected", "check (b)": "nothing"}, both)
    line = _verified.stamp_from_metrics(work, PULL, sha="a" * 40)
    assert line.startswith("  verified: no stamp")
    assert f"base tree {base[:12]}" in line and "not proved in full" in line
    assert _verified.record(work, tree) == (None, "")
    # Legs that measured from different trees compose with neither.
    apart = {"check (a)": base, "check (b)": "c" * 40}
    _rows(work, PULL, {"check (a)": "affected", "check (b)": "nothing"}, apart)
    line = _verified.stamp_from_metrics(work, PULL, sha="a" * 40)
    assert "the legs measured from different trees (check (a) from" in line
    assert "check (b) from cccccccccccc" in line
    assert _verified.record(work, tree) == (None, "")
    _rows(work, PULL, {"check (a)": "affected", "check (b)": "nothing"}, both)
    # A base row of another scope is no proof either.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            "livery.workshop._verified.record",
            lambda root, tree: (
                _verified.Verified(tree, "5", "c" * 40, _verified.AFFECTED, ()),
                "",
            ),
        )
        line = _verified.stamp_from_metrics(work, PULL, sha="a" * 40)
    assert line.startswith("  verified: no stamp") and f"base tree {base[:12]}" in line
    assert _verified.record(work, tree) == (None, "")
    # A base row the store cannot read: no stamp, its words.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            "livery.workshop._verified.record",
            lambda root, tree: (None, "the record could not be read: down"),
        )
        line = _verified.stamp_from_metrics(work, PULL, sha="a" * 40)
    assert line.startswith("  verified: no stamp")
    assert f"the base tree {base[:12]} could not be read" in line
    assert _verified.record(work, tree) == (None, "")


def test_a_narrowed_run_on_a_verified_base_stamps_its_tree_naming_the_base(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _verified.tree_id(work)
    stamped = _verified.stamp(work, RUN, tree=base, sha="b" * 40, legs=("check (a)",))
    assert stamped == ""
    _branch_commit(work, "feat/one")
    tree = _verified.tree_id(work)
    _rows(work, PULL, {"check (a)": "affected"}, {"check (a)": base})
    line = _verified.stamp_from_metrics(work, PULL, sha="a" * 40)
    assert line == (
        f"  verified: tree {tree[:12]} recorded as proved green by run 1014"
        f" on top of tree {base[:12]} (run 1013)"
    )
    found, why = _verified.record(work, tree)
    assert why == "" and found is not None
    assert found.scope == _verified.FULL and found.legs == ("check (a)",)
    assert found.base_tree == base and found.base_run == "1013"
    # The row names the branch whose run proved the tree, so main's run
    # after the squash finds the branch's coverage record to copy; the
    # base row, stamped by main's own push, names none.
    assert found.branch == "feat/one"
    base_row, _ = _verified.record(work, base)
    assert base_row is not None and base_row.branch == ""
    # The legs read a composed row like a full one, and say what it rests on.
    assert _quality.verified_already(work)
    out = capsys.readouterr().out
    assert "proved green by run 1014" in out and f"on top of tree {base[:12]}" in out
    # A composed row is a full row for the next narrowed run: the proof
    # chains, and a note-only diff composes the same way.
    _git(work, "push", "-q", "origin", "feat/one:main")
    _git(work, "fetch", "-q", "origin")
    _branch_commit(work, "feat/two")
    later = _ci_context.RunContext(
        "gitea", "1015", "pull_request", "refs/pull/2/merge", base_ref="main"
    )
    _rows(work, later, {"check (a)": "nothing"}, {"check (a)": tree})
    line = _verified.stamp_from_metrics(work, later, sha="a" * 40)
    assert f"on top of tree {tree[:12]} (run 1014)" in line
    found, _ = _verified.record(work, _verified.tree_id(work))
    assert found is not None and found.base_tree == tree
    # Every leg skipped on the record already: nothing to add.
    _rows(work, later, {"check (a)": "verified"})
    line = _verified.stamp_from_metrics(work, later, sha="a" * 40)
    assert "already recorded" in line


def test_a_merge_checkouts_stamp_names_the_two_commits_it_merged(work: Path) -> None:
    base = _git(work, "rev-parse", "HEAD").strip()
    _branch_commit(work, "feat/one")
    head = _git(work, "rev-parse", "HEAD").strip()
    _git(work, "checkout", "-q", "--detach", base)
    _git(work, "merge", "-q", "--no-ff", "--no-edit", "feat/one")
    _rows(work, PULL, {"check (a)": "full"})
    line = _verified.stamp_from_metrics(work, PULL, sha="a" * 40)
    tree = _verified.tree_id(work)
    assert line == f"  verified: tree {tree[:12]} recorded as proved green by run 1014"
    found, _ = _verified.record(work, tree)
    assert found is not None
    assert (found.base_sha, found.head_sha, found.branch) == (base, head, "feat/one")
    # A checkout that is no merge names none.
    _git(work, "checkout", "-q", "feat/one")
    _rows(work, PULL, {"check (a)": "full"})
    _verified.stamp_from_metrics(work, PULL, sha="a" * 40)
    found, _ = _verified.record(work, _verified.tree_id(work))
    assert found is not None and (found.base_sha, found.head_sha) == ("", "")


# --- the proof per platform: a row proves its tree for the legs it names -----

UBUNTU = "check-ubuntu-latest-3.14"
MACOS = "check-macos-latest-3.14"
WINDOWS = "check-windows-latest-3.14"

#: The legs' display names as GitHub and Gitea list the jobs, by label.
DISPLAY = {
    UBUNTU: "check (ubuntu-latest, 3.14)",
    MACOS: "check (macos-latest, 3.14)",
    WINDOWS: "check (windows-latest, 3.14)",
}


def _leg(label: str) -> _ci_context.RunContext:
    """Main's push run as the entries of the check leg *label* see it."""
    return dataclasses.replace(RUN, leg=label)


def test_a_full_row_naming_another_platforms_leg_proves_nothing_for_this_one(
    work: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tree = _verified.tree_id(work)
    assert _verified.stamp(work, RUN, tree=tree, sha="a" * 40, legs=(UBUNTU,)) == ""
    # The read for a leg leaves the row out; the read without one keeps it.
    assert _verified.proved(work, MACOS) == ({}, "")
    rows, why = _verified.proved(work)
    assert why == "" and set(rows) == {tree}
    assert _verified.proves(rows[tree], UBUNTU)
    assert not _verified.proves(rows[tree], MACOS)
    # The leg's own check names the row it found, and runs the gate.
    assert _quality.verified_already(work, leg=MACOS) is None
    assert (
        f"verified: tree {tree[:12]} proved green by run 1013 on {UBUNTU}, not on"
        f" {MACOS}; running the gate" in capsys.readouterr().out
    )
    # The gate itself walks the registry and leaves a full marker, not a
    # verified one.
    (work / "workshop.toml").write_text("[workspace]\nextensions = []\n")
    monkeypatch.setattr("livery.workshop._quality.workspace_root", lambda: work)
    monkeypatch.setattr("livery.workshop._ci_context.run_context", lambda: _leg(MACOS))
    walked: list[str] = []
    monkeypatch.setattr(
        "livery.workshop._quality.walk",
        lambda ctx, **kwargs: walked.append("walked"),
    )
    _quality.check()
    assert walked == ["walked"]
    assert leg_scope(work) == LegScope("full", leg=MACOS)


def test_a_row_with_display_names_proves_the_leg_it_names(work: Path) -> None:
    tree = _verified.tree_id(work)
    display = DISPLAY[UBUNTU]
    assert _verified.stamp(work, RUN, tree=tree, sha="a" * 40, legs=(display,)) == ""
    found, why = _verified.record(work, tree)
    assert why == "" and found is not None and found.legs == (display,)
    assert _verified.proves(found, UBUNTU) and not _verified.proves(found, WINDOWS)
    rows, why = _verified.proved(work, UBUNTU)
    assert why == "" and set(rows) == {tree}
    assert _verified.proved(work, WINDOWS) == ({}, "")
    assert _quality.verified_already(work, leg=UBUNTU) == found
    assert _quality.verified_already(work, leg=WINDOWS) is None


def test_the_stamp_names_the_legs_labels(work: Path) -> None:
    # A leg writes its label into its row on every forge; GitLab's job
    # is the bare name, so its label is what names the platform; a row
    # without a label reads the forge's display name as the label.
    _rows(
        work,
        RUN,
        {DISPLAY[UBUNTU]: "full", "check": "full", DISPLAY[WINDOWS]: "full"},
        labels={DISPLAY[UBUNTU]: UBUNTU, "check": MACOS},
    )
    line = _verified.stamp_from_metrics(work, RUN, sha="a" * 40)
    tree = _verified.tree_id(work)
    assert line == f"  verified: tree {tree[:12]} recorded as proved green by run 1013"
    found, _ = _verified.record(work, tree)
    assert found is not None and found.legs == (MACOS, UBUNTU, WINDOWS)


def test_the_stamp_skips_the_forges_rows_of_a_platforms_shards(work: Path) -> None:
    # The fold left one row under the platform's name; the forge's rows
    # of its shards carry no scope and block nothing.
    entry: dict[str, Any] = {
        "schema": _metrics.SCHEMA,
        "jobs": {
            DISPLAY[WINDOWS]: {
                "scope": {"scope": "full", "packages": [], "leg": WINDOWS}
            },
            "check (windows-latest, 3.14, 1of2)": {"conclusion": "success"},
            "check (windows-latest, 3.14, 2of2)": {"conclusion": "success"},
            "check: [2of2]": {"conclusion": "success"},
            DISPLAY[UBUNTU]: {
                "scope": {"scope": "full", "packages": [], "leg": UBUNTU}
            },
        },
    }
    assert (
        git.put(
            state_store(work),
            _metrics.SERIES.ref,
            {_metrics.run_file(RUN.run_id): json.dumps(entry)},
            message="rows",
            ci_only=True,
        )
        == ""
    )
    tree = _verified.tree_id(work)
    line = _verified.stamp_from_metrics(work, RUN, sha="a" * 40)
    assert line == f"  verified: tree {tree[:12]} recorded as proved green by run 1013"
    found, _ = _verified.record(work, tree)
    assert found is not None and found.legs == (UBUNTU, WINDOWS)
    # A check row without a scope that spells no shard is a leg that
    # left none, as before.
    entry["jobs"]["check (macos-latest, 3.14)"] = {"conclusion": "success"}
    assert (
        git.put(
            state_store(work),
            _metrics.SERIES.ref,
            {_metrics.run_file(RUN.run_id): json.dumps(entry)},
            message="rows",
            ci_only=True,
        )
        == ""
    )
    line = _verified.stamp_from_metrics(work, RUN, sha="a" * 40)
    assert line == "  verified: no stamp, check (macos-latest, 3.14) left no scope"


def test_the_merges_stamp_names_every_platform_after_a_one_platform_pull_request(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tree = _verified.tree_id(work)
    labels = {display: label for label, display in DISPLAY.items()}
    # The pull request ran on linux alone: its row names one platform,
    # and the other platforms' legs find no proof in it.
    _rows(work, PULL, {DISPLAY[UBUNTU]: "full"}, labels=labels)
    line = _verified.stamp_from_metrics(work, PULL, sha="a" * 40)
    assert line == f"  verified: tree {tree[:12]} recorded as proved green by run 1014"
    found, _ = _verified.record(work, tree)
    assert found is not None and found.legs == (UBUNTU,)
    assert _quality.verified_already(work, leg=UBUNTU) == found
    assert _quality.verified_already(work, leg=MACOS) is None
    assert _quality.verified_already(work, leg=WINDOWS) is None
    capsys.readouterr()
    # The merge's run on the same tree: the linux leg skipped on the
    # record, the others ran the whole gate. One row per tree, so the
    # stamp replaces the pull request's row with one naming all three.
    _rows(
        work,
        RUN,
        {DISPLAY[UBUNTU]: "verified", DISPLAY[MACOS]: "full", DISPLAY[WINDOWS]: "full"},
        labels=labels,
    )
    line = _verified.stamp_from_metrics(work, RUN, sha="b" * 40)
    assert line == f"  verified: tree {tree[:12]} recorded as proved green by run 1013"
    rows, why = _verified.proved(work)
    assert why == "" and list(rows) == [tree]
    assert rows[tree].run == "1013" and rows[tree].sha == "b" * 40
    assert rows[tree].legs == (MACOS, UBUNTU, WINDOWS)
    for leg in (UBUNTU, MACOS, WINDOWS):
        assert _quality.verified_already(work, leg=leg) == rows[tree]
        assert set(_verified.proved(work, leg)[0]) == {tree}
