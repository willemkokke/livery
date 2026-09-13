"""The local gate record: refusals first, then the chain and the skip it earns."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from livery.workshop import _gate_record, _state
from livery.workshop._git_ops import GitOps
from livery.workshop._verified import tree_id


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


@pytest.fixture
def unverified(monkeypatch: pytest.MonkeyPatch) -> None:
    """CI's record holds nothing: every chain must root on a full row."""
    monkeypatch.setattr(
        _gate_record, "verified_roots", lambda root, git, base: (set(), "no record")
    )


def _raw(tree: str, *, when: str, scope: str = "full", base_tree: str = "") -> str:
    """A row as the store writes it, with a chosen stamp."""
    return json.dumps(
        {
            "schema": _gate_record.SERIES.schema,
            "when": when,
            "tree": tree,
            "scope": scope,
            "packages": ["packages/x"] if scope != "full" else [],
            "base_tree": base_tree,
            "root": "",
        }
    )


def _commit(work: Path, name: str) -> str:
    (work / name).write_text(f"{name}\n")
    _git(work, "add", name)
    _git(work, "commit", "-qm", f"feat: {name}")
    return tree_id(GitOps(work))


# --- the working tree's id --------------------------------------------------------


def test_the_working_tree_id_is_heads_when_clean_and_moves_with_the_files(
    work: Path,
) -> None:
    git = GitOps(work)
    assert git.working_tree_id() == tree_id(git)
    (work / "dirt.txt").write_text("dirt\n")
    dirty = git.working_tree_id()
    assert dirty != tree_id(git)
    # The real index was never touched: nothing is staged.
    assert _git(work, "diff", "--cached", "--name-only").strip() == ""
    # An ignored file does not count; a commit that takes everything
    # has the id the dirty tree had.
    (work / ".gitignore").write_text("ignored.txt\n")
    (work / "ignored.txt").write_text("x\n")
    with_ignore = git.working_tree_id()
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "feat: dirt")
    assert tree_id(git) == with_ignore
    assert git.tree_diff(dirty, with_ignore) == [".gitignore"]


# --- refusals: what proves nothing ------------------------------------------------


def test_junk_rows_and_a_rootless_chain_prove_nothing(
    work: Path, unverified: None
) -> None:
    git = GitOps(work)
    junk = {
        "a": "{not json",
        "b": json.dumps({"schema": 99}),
        "c": json.dumps({"schema": 1, "when": "2026-09-10T00:00:00+00:00"}),
    }
    assert _state.put(work, _gate_record.SERIES.ref, junk, message="junk") == ""
    proof, why = _gate_record.covering(git)
    assert proof is None and "no chain of green gates" in why
    # A step whose base no row proves is not a proof either.
    tree = tree_id(git)
    fresh = datetime.now(UTC).isoformat(timespec="seconds")
    orphan = {
        f"{tree}--step--{'0' * 40}": _raw(
            tree, when=fresh, scope="step", base_tree="0" * 40
        )
    }
    assert _state.put(work, _gate_record.SERIES.ref, orphan, message="orphan") == ""
    assert _gate_record.covering(git)[0] is None
    # Nor a cycle.
    loop = {
        f"{tree}--step--{'1' * 40}": _raw(
            tree, when=fresh, scope="step", base_tree="1" * 40
        ),
        f"{'1' * 40}--step--{tree}": _raw(
            "1" * 40, when=fresh, scope="step", base_tree=tree
        ),
    }
    assert _state.put(work, _gate_record.SERIES.ref, loop, message="loop") == ""
    assert _gate_record.covering(git)[0] is None


def test_the_record_never_reaches_the_remote(work: Path) -> None:
    git = GitOps(work)
    assert "recorded" in _gate_record.remember(
        work, git, tree=tree_id(git), packages=None
    )
    _git(work, "push", "-q", "origin", "main")
    assert "workshop-local" not in _git(work, "ls-remote", "origin")
    assert _gate_record.SERIES.ref.startswith(_state.LOCAL_NAMESPACE)


def test_rows_are_bounded_by_count_and_age(work: Path, unverified: None) -> None:
    git = GitOps(work)
    tree = tree_id(git)
    old = (datetime.now(UTC) - timedelta(days=8)).isoformat(timespec="seconds")
    stale = {
        f"{n:040d}--full": _raw(f"{n:040d}", when=old)
        for n in range(_gate_record.KEEP + 5)
    }
    stale[f"{tree}--full"] = _raw(tree, when=old)
    assert _state.put(work, _gate_record.SERIES.ref, stale, message="old") == ""
    # Eight days old: the row proves nothing, however exact its tree.
    assert _gate_record.covering(git)[0] is None
    # A write keeps the newest KEEP rows, the new one among them.
    assert "recorded" in _gate_record.remember(work, git, tree=tree, packages=None)
    kept, why = _gate_record.rows(work)
    assert why == "" and len(kept) == _gate_record.KEEP and kept[0].tree == tree
    proof, _ = _gate_record.covering(git)
    assert proof is not None and proof.steps[0].when != old


# --- the chain ----------------------------------------------------------------------


def test_a_chain_of_steps_proves_its_tree_back_to_a_full_row(
    work: Path, unverified: None
) -> None:
    git = GitOps(work)
    first = tree_id(git)
    assert "(full)" in _gate_record.remember(work, git, tree=first, packages=None)
    second = _commit(work, "a.txt")
    assert "(step)" in _gate_record.remember(
        work, git, tree=second, packages=("packages/x",), base_tree=first
    )
    third = _commit(work, "b.txt")
    _gate_record.remember(work, git, tree=third, packages=(), base_tree=second)
    proof, why = _gate_record.covering(git)
    assert proof is not None and why == ""
    assert [row.tree for row in proof.steps] == [third, second, first]
    assert proof.root == "full" and proof.root_tree == first
    assert proof.describe() == f"2 step(s) on a full gate of {first[:12]}"
    # The earlier spelling's affected row is a step too.
    fourth = _commit(work, "c.txt")
    fresh = datetime.now(UTC).isoformat(timespec="seconds")
    legacy = {
        f"{fourth}--affected--{third}": _raw(
            fourth, when=fresh, scope="affected", base_tree=third
        )
    }
    assert _state.put(work, _gate_record.SERIES.ref, legacy, message="legacy") == ""
    proof, _ = _gate_record.covering(git)
    assert proof is not None and proof.steps[0].tree == fourth


def test_a_chain_rests_on_a_tree_cis_record_holds(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git = GitOps(work)
    base = tree_id(git)
    monkeypatch.setattr(
        _gate_record, "verified_roots", lambda root, git, b: ({base}, "")
    )
    # HEAD's own tree is CI's: a chain with no step at all.
    proof, _ = _gate_record.covering(git)
    assert proof is not None and proof.steps == () and proof.root == "verified"
    assert proof.describe() == f"CI's record of {base[:12]}"
    # One step from it.
    second = _commit(work, "a.txt")
    _gate_record.remember(
        work, git, tree=second, packages=("packages/x",), base_tree=base
    )
    proof, _ = _gate_record.covering(git)
    assert proof is not None and proof.root_tree == base and len(proof.steps) == 1
    assert proof.describe() == f"1 step(s) on CI's record of {base[:12]}"


def test_verified_roots_are_the_trees_of_heads_history_cis_record_holds(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _verified

    git = GitOps(work)
    base = tree_id(git)
    parent = _commit(work, "a.txt")
    tip = _commit(work, "b.txt")
    monkeypatch.setattr(
        "livery.workshop._state.remote_snapshot",
        lambda root, **kw: __import__("contextlib").nullcontext(),
    )
    # The record could not be read: rootless, and the line says why.
    monkeypatch.setattr(_verified, "held", lambda root, trees: (set(), "down"))
    roots, why = _gate_record.verified_roots(work, git, "main")
    assert roots == set() and "could not be read (down)" in why
    # The record holds none of them: the merge base and the reach are named.
    asked: list[list[str]] = []

    def noting(root: Path, trees: list[str]) -> tuple[set[str], str]:
        asked.append(list(trees))
        return set(), ""

    monkeypatch.setattr(_verified, "held", noting)
    roots, why = _gate_record.verified_roots(work, git, "main")
    assert roots == set()
    assert why == (
        f"CI's record holds neither the merge base's tree {base[:12]}"
        " nor one of HEAD's last 3 first-parent trees"
    )
    assert asked == [[base, tip, parent, base]]
    # The tip's run is red or unfinished: the parent's tree carries the chain.
    held = {parent}
    monkeypatch.setattr(
        _verified, "held", lambda root, trees: ({t for t in trees if t in held}, "")
    )
    assert _gate_record.verified_roots(work, git, "main") == ({parent}, "")
    # The merge base is a root as well.
    held.add(base)
    assert _gate_record.verified_roots(work, git, "main") == ({base, parent}, "")


# --- the reflex's plan --------------------------------------------------------------


def test_the_plan_steps_from_the_nearest_tree_cis_record_holds(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git = GitOps(work)
    parent = _commit(work, "a.txt")
    _commit(work, "b.txt")
    monkeypatch.setattr(
        _gate_record, "verified_roots", lambda root, git, base: ({parent}, "")
    )
    # The tip's own run is not in the record: the step is from its parent.
    step = _gate_record.plan(work, git)
    assert step.mode == "step" and step.base_tree == parent
    assert step.paths == ("b.txt",)
    # An edit rides in the same step.
    (work / "c.txt").write_text("c\n")
    step = _gate_record.plan(work, git)
    assert step.base_tree == parent and step.paths == ("b.txt", "c.txt")
    # Once the record holds the tip, the clean tree is proved outright.
    monkeypatch.setattr(
        _gate_record,
        "verified_roots",
        lambda root, git, base: ({parent, tree_id(git)}, ""),
    )
    (work / "c.txt").unlink()
    proved = _gate_record.plan(work, git)
    assert proved.mode == "proved"
    assert proved.why == f"CI's record of {tree_id(git)[:12]}"


def test_the_plan_steps_from_the_proved_tree_nearest_by_delta(
    work: Path, unverified: None
) -> None:
    git = GitOps(work)
    first = _gate_record.plan(work, git)
    _gate_record.remember(work, git, tree=first.tree, packages=None)
    _commit(work, "a.txt")
    _commit(work, "b.txt")
    # A green check of a dirty tree: its row is one path from the next
    # edit, where the history's proved tree is four.
    (work / "c.txt").write_text("c\n")
    dirty = git.working_tree_id()
    _gate_record.remember(work, git, tree=dirty, packages=(), base_tree=first.tree)
    # A row whose tree git does not have is passed over.
    _gate_record.remember(work, git, tree="f" * 40, packages=(), base_tree=first.tree)
    (work / "d.txt").write_text("d\n")
    step = _gate_record.plan(work, git)
    assert step.mode == "step" and step.base_tree == dirty
    assert step.paths == ("d.txt",)


def test_the_plan_is_proved_a_step_or_full(work: Path, unverified: None) -> None:
    git = GitOps(work)
    # Nothing in reach: the whole gate runs and roots a chain.
    first = _gate_record.plan(work, git)
    assert first.mode == "full" and first.tree == tree_id(git)
    _gate_record.remember(work, git, tree=first.tree, packages=None)
    # The same tree again: proved, nothing to run.
    assert _gate_record.plan(work, git).mode == "proved"
    # A dirty tree: a step from HEAD's proved tree, the delta named.
    (work / "packages").mkdir()
    (work / "packages" / "x.txt").write_text("x\n")
    step = _gate_record.plan(work, git)
    assert step.mode == "step" and step.base_tree == first.tree
    assert step.paths == ("packages/x.txt",)
    assert step.tree == git.working_tree_id()
    _gate_record.remember(
        work, git, tree=step.tree, packages=("packages/x",), base_tree=first.tree
    )
    # The commit that takes everything has the proved tree: nothing to run.
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "feat: x")
    assert _gate_record.plan(work, git).mode == "proved"
    # Two commits later, the nearest proved tree in the history is the base.
    _commit(work, "a.txt")
    _commit(work, "b.txt")
    later = _gate_record.plan(work, git)
    assert later.mode == "step" and later.base_tree == step.tree
    assert later.paths == ("a.txt", "b.txt")
