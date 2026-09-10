"""The local gate record: refusals first, then the skip it earns."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from livery.workshop import _gate_record, _quality
from livery.workshop._git_ops import GitOps
from livery.workshop._packages import Package


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture
def work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("livery.footman.data_dir", lambda: tmp_path / "home")
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


def test_a_dirty_tree_records_nothing_and_proves_nothing(work: Path) -> None:
    git = GitOps(work)
    (work / "dirt.txt").write_text("dirt\n")
    line = _gate_record.remember(work, git, packages=None)
    assert line == "  gate record: the tree has uncommitted changes; nothing recorded"
    assert not _gate_record.record_path().exists()
    assert _gate_record.covering(git, affected=False) == (
        None,
        "the tree has uncommitted changes",
    )


def test_an_unreadable_record_runs_the_gate_and_is_replaced(work: Path) -> None:
    git = GitOps(work)
    path = _gate_record.record_path()
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    row, why = _gate_record.covering(git, affected=False)
    assert row is None and "could not be read" in why
    path.write_text(json.dumps({"tree": "not a list"}))
    row, why = _gate_record.covering(git, affected=False)
    assert row is None and "not a list of rows" in why
    assert "recorded as proved green (full)" in _gate_record.remember(
        work, git, packages=None
    )
    row, why = _gate_record.covering(git, affected=False)
    assert row is not None and row.scope == "full" and why == ""


def test_rows_are_bounded_by_count_and_age(work: Path) -> None:
    git = GitOps(work)
    tree = _git(work, "rev-parse", "HEAD^{tree}").strip()
    old = (datetime.now(UTC) - timedelta(days=8)).isoformat(timespec="seconds")
    rows = [
        {
            "tree": f"{n:040d}",
            "scope": "full",
            "packages": [],
            "base_tree": "",
            "when": old,
            "root": "",
        }
        for n in range(_gate_record.KEEP + 5)
    ]
    rows.append(
        {
            "tree": tree,
            "scope": "full",
            "packages": [],
            "base_tree": "",
            "when": old,
            "root": "",
        }
    )
    path = _gate_record.record_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(rows))
    # Eight days old: the row proves nothing, however exact its tree.
    assert _gate_record.covering(git, affected=False) == (None, "")
    # A write keeps the newest KEEP rows, the new one among them.
    assert "recorded" in _gate_record.remember(work, git, packages=None)
    kept, why = _gate_record.read_rows()
    assert why == "" and len(kept) == _gate_record.KEEP and kept[-1].tree == tree
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
    path = _gate_record.record_path()
    rows = json.loads(path.read_text("utf-8"))
    rows[-1]["base_tree"] = "0" * 40
    path.write_text(json.dumps(rows))
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
    assert not _gate_record.record_path().exists()
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
