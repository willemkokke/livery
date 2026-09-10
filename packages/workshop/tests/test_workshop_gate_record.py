"""The local gate record: refusals first, then the skip it earns."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from livery.workshop import _gate_record, _quality, _state
from livery.workshop._git_ops import GitOps
from livery.workshop._packages import Package


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


def _raw(tree: str, *, when: str, scope: str = "full", base_tree: str = "") -> str:
    """A row as the store writes it, with a chosen stamp."""
    return json.dumps(
        {
            "schema": _gate_record.SERIES.schema,
            "when": when,
            "tree": tree,
            "scope": scope,
            "packages": ["packages/x"] if scope == "affected" else [],
            "base_tree": base_tree,
            "root": "",
        }
    )


def test_a_dirty_tree_records_nothing_and_proves_nothing(work: Path) -> None:
    git = GitOps(work)
    (work / "dirt.txt").write_text("dirt\n")
    line = _gate_record.remember(work, git, packages=None)
    assert line == "  gate record: the tree has uncommitted changes; nothing recorded"
    assert _gate_record.rows(work) == ((), "")
    assert _gate_record.covering(git, affected=False) == (
        None,
        "the tree has uncommitted changes",
    )


def test_a_row_that_is_not_a_proof_is_skipped_and_the_record_still_decides(
    work: Path,
) -> None:
    git = GitOps(work)
    junk = {
        "a": "{not json",
        "b": json.dumps({"schema": 99}),
        "c": json.dumps({"schema": 1, "when": "2026-09-10T00:00:00+00:00"}),
    }
    assert _state.put(work, _gate_record.SERIES.ref, junk, message="junk") == ""
    assert _gate_record.covering(git, affected=False) == (None, "")
    assert "recorded as proved green (full)" in _gate_record.remember(
        work, git, packages=None
    )
    row, why = _gate_record.covering(git, affected=False)
    assert row is not None and row.scope == "full" and why == ""


def test_the_record_never_reaches_the_remote(work: Path) -> None:
    git = GitOps(work)
    assert "recorded" in _gate_record.remember(work, git, packages=None)
    _git(work, "push", "-q", "origin", "main")
    assert "workshop-local" not in _git(work, "ls-remote", "origin")
    assert _gate_record.SERIES.ref.startswith(_state.LOCAL_NAMESPACE)


def test_rows_are_bounded_by_count_and_age(work: Path) -> None:
    git = GitOps(work)
    tree = _git(work, "rev-parse", "HEAD^{tree}").strip()
    old = (datetime.now(UTC) - timedelta(days=8)).isoformat(timespec="seconds")
    stale = {
        f"{n:040d}--full": _raw(f"{n:040d}", when=old)
        for n in range(_gate_record.KEEP + 5)
    }
    stale[f"{tree}--full"] = _raw(tree, when=old)
    assert _state.put(work, _gate_record.SERIES.ref, stale, message="old") == ""
    # Eight days old: the row proves nothing, however exact its tree.
    assert _gate_record.covering(git, affected=False) == (None, "")
    # A write keeps the newest KEEP rows, the new one among them.
    assert "recorded" in _gate_record.remember(work, git, packages=None)
    kept, why = _gate_record.rows(work)
    assert why == "" and len(kept) == _gate_record.KEEP and kept[0].tree == tree
    row, _ = _gate_record.covering(git, affected=False)
    assert row is not None and row.when != old


def test_a_full_row_covers_any_gate_and_an_affected_row_needs_its_base(
    work: Path,
) -> None:
    git = GitOps(work)
    assert "(affected)" in _gate_record.remember(
        work, git, packages=("packages/x",), base="main"
    )
    row, _ = _gate_record.covering(git, affected=True, base="main")
    assert row is not None and row.packages == ("packages/x",)
    # An affected row does not cover a full gate.
    assert _gate_record.covering(git, affected=False) == (None, "")
    # A row narrowed against another base tree proves nothing for
    # this one: the affected set would differ.
    assert _state.drop(work, _gate_record.SERIES.ref) == ""
    tree = _git(work, "rev-parse", "HEAD^{tree}").strip()
    fresh = datetime.now(UTC).isoformat(timespec="seconds")
    foreign = {
        f"{tree}--affected--{'0' * 40}": _raw(
            tree, when=fresh, scope="affected", base_tree="0" * 40
        )
    }
    assert _state.put(work, _gate_record.SERIES.ref, foreign, message="base") == ""
    assert _gate_record.covering(git, affected=True, base="main") == (None, "")
    # A full row covers both needs.
    assert "(full)" in _gate_record.remember(work, git, packages=None)
    for affected in (False, True):
        row, _ = _gate_record.covering(git, affected=affected, base="main")
        assert row is not None and row.scope == "full"


def test_a_green_local_gate_records_and_a_red_one_does_not(
    work: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.footman import Failed

    (work / "workshop.toml").write_text("[workspace]\n")
    _git(work, "add", "workshop.toml")
    _git(work, "commit", "-qm", "chore: contract")
    monkeypatch.setattr("livery.workshop._quality.workspace_root", lambda: work)
    monkeypatch.setattr("livery.workshop._state.run_context", lambda: None)
    thing = Package(
        directory=work / "packages" / "thing",
        path="packages/thing",
        name="livery-thing",
        type="python",
        depends=(),
    )
    other = Package(
        directory=work / "packages" / "other",
        path="packages/other",
        name="livery-other",
        type="python",
        depends=(),
    )
    monkeypatch.setattr("livery.workshop._quality._packages", lambda: (thing, other))
    monkeypatch.setattr(
        "livery.workshop._quality._affected", lambda base="main": (thing,)
    )
    # The local narrowed gate renders and checks provenance too; the
    # toy root has neither, so both stand in.
    monkeypatch.setattr("livery.workshop._quality.template_check", lambda: None)
    monkeypatch.setattr(
        "livery.workshop._provenance.provenance_check", lambda fix=False: None
    )

    def _red(subset: object, *, fix: bool = False) -> None:
        raise Failed("the gate is red")

    monkeypatch.setattr("livery.workshop._quality._scoped_check", _red)
    with pytest.raises(Failed):
        _quality.check(affected=True)
    assert _gate_record.rows(work) == ((), "")
    monkeypatch.setattr(
        "livery.workshop._quality._scoped_check", lambda subset, *, fix=False: None
    )
    _quality.check(affected=True)
    out = capsys.readouterr().out
    assert "gate record: tree" in out and "(affected)" in out
    row, _ = _gate_record.covering(GitOps(work), affected=True, base="main")
    assert row is not None and row.packages == ("packages/thing",)
    # Inside CI the record is never written.
    monkeypatch.setattr(
        "livery.workshop._state.run_context",
        lambda: __import__(
            "livery.workshop._state", fromlist=["RunContext"]
        ).RunContext("gitea", "1", "push", "refs/heads/main"),
    )
