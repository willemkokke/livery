"""The local gate record: refusals first, then the chain and the skip it earns."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from livery import git
from livery.git import _objects
from livery.workshop import _ci_context, _gate_record, _quality
from livery.workshop._ci_context import state_store
from livery.workshop._verified import tree_id
from workshop_seeds import Seeds, _seed_home, pushed, seed_copier  # noqa: F401
from workshop_spawns import counting_spawns


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture
def work(seeds: Seeds) -> Path:
    """A clone of a bare origin, one commit on main, pushed."""
    return seeds("pushed", pushed) / "work"


@pytest.fixture
def unverified(monkeypatch: pytest.MonkeyPatch) -> None:
    """CI's record holds nothing: every chain must root on a full row."""
    monkeypatch.setattr(
        _gate_record, "verified_roots", lambda root, base: (set(), "no record")
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
    return tree_id(work)


# --- the working tree's id --------------------------------------------------------


def test_the_working_tree_id_is_heads_when_clean_and_moves_with_the_files(
    work: Path,
) -> None:
    assert git.working_tree_id(work) == tree_id(work)
    (work / "dirt.txt").write_text("dirt\n")
    dirty = git.working_tree_id(work)
    assert dirty != tree_id(work)
    # The real index was never touched: nothing is staged.
    assert _git(work, "diff", "--cached", "--name-only").strip() == ""
    # An ignored file does not count; a commit that takes everything
    # has the id the dirty tree had.
    (work / ".gitignore").write_text("ignored.txt\n")
    (work / "ignored.txt").write_text("x\n")
    with_ignore = git.working_tree_id(work)
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "feat: dirt")
    assert tree_id(work) == with_ignore
    assert git.tree_diff(work, dirty, with_ignore) == [".gitignore"]


def test_the_working_tree_id_reads_a_rewrite_the_cached_stat_calls_clean(
    work: Path,
) -> None:
    # The forcing: a rewrite of the same size whose stat is identical to
    # the one the index cached, so nothing but git's racy-clean rule can
    # tell it apart. ctime cannot be set from here, so it leaves the
    # comparison the way a repository may configure it.
    _git(work, "config", "core.trustctime", "false")
    module = work / "mod.py"
    module.write_text("x = 1\n")
    # Stamped well before the measurement, so the scratch copy of the
    # index is taken in a later second than the cached stat.
    stamp = os.stat(module).st_mtime_ns - 5_000_000_000
    os.utime(module, ns=(stamp, stamp))
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "feat: mod")
    committed = tree_id(work)
    module.write_text("x = 2\n")
    os.utime(module, ns=(stamp, stamp))
    os.utime(work / ".git" / "index", ns=(stamp, stamp))
    assert git.working_tree_id(work) != committed


# --- refusals: what proves nothing ------------------------------------------------


def test_junk_rows_and_a_rootless_chain_prove_nothing(
    work: Path, unverified: None
) -> None:
    junk = {
        "a": "{not json",
        "b": json.dumps({"schema": 99}),
        "c": json.dumps({"schema": 1, "when": "2026-09-10T00:00:00+00:00"}),
    }
    assert (
        git.put(state_store(work), _gate_record.SERIES.ref, junk, message="junk") == ""
    )
    proof, why = _gate_record.covering(work)
    assert proof is None and "no chain of green gates" in why
    # A step whose base no row proves is not a proof either.
    tree = tree_id(work)
    fresh = datetime.now(UTC).isoformat(timespec="seconds")
    orphan = {
        f"{tree}--step--{'0' * 40}": _raw(
            tree, when=fresh, scope="step", base_tree="0" * 40
        )
    }
    assert (
        git.put(state_store(work), _gate_record.SERIES.ref, orphan, message="orphan")
        == ""
    )
    assert _gate_record.covering(work)[0] is None
    # Nor a cycle.
    loop = {
        f"{tree}--step--{'1' * 40}": _raw(
            tree, when=fresh, scope="step", base_tree="1" * 40
        ),
        f"{'1' * 40}--step--{tree}": _raw(
            "1" * 40, when=fresh, scope="step", base_tree=tree
        ),
    }
    assert (
        git.put(state_store(work), _gate_record.SERIES.ref, loop, message="loop") == ""
    )
    assert _gate_record.covering(work)[0] is None


NOT_RECORDED = "; the next gate measures from the last recorded tree"


def test_a_record_write_that_raises_prints_why_and_the_gate_stays_green(
    work: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(root: Path) -> git.StateStore:
        raise RuntimeError("the store broke")

    monkeypatch.setattr(_gate_record, "state_store", broken)
    tree = tree_id(work)
    assert _gate_record.remember(work, tree=tree, packages=None) == (
        f"  gate record: not recorded (the store broke){NOT_RECORDED}"
    )
    # The gate's own call returns, so the verdict the steps reached stands.
    _quality._remember_local(work, None, tree=tree, packages=None)
    assert capsys.readouterr().out == (
        f"  gate record: not recorded (the store broke){NOT_RECORDED}\n"
    )
    monkeypatch.undo()
    assert _gate_record.rows(work) == ((), "")


def test_a_record_write_that_finds_the_ref_locked_past_the_wait_says_so(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_objects, "LOCK_WAIT", 0.1)
    lock = work / ".git" / f"{_gate_record.SERIES.ref}.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_bytes(b"")
    line = _gate_record.remember(work, tree=tree_id(work), packages=None)
    assert line.startswith(
        f"  gate record: not recorded (update refused: {lock} stayed locked for "
    )
    assert line.endswith(NOT_RECORDED)
    lock.unlink()
    assert _gate_record.rows(work) == ((), "")


def test_the_record_never_reaches_the_remote(work: Path) -> None:
    assert "recorded" in _gate_record.remember(work, tree=tree_id(work), packages=None)
    _git(work, "push", "-q", "origin", "main")
    assert "workshop-local" not in _git(work, "ls-remote", "origin")
    assert _gate_record.SERIES.ref.startswith(git.LOCAL_NAMESPACE)


def test_rows_are_bounded_by_count_and_age(work: Path, unverified: None) -> None:
    tree = tree_id(work)
    old = (datetime.now(UTC) - timedelta(days=8)).isoformat(timespec="seconds")
    stale = {
        f"{n:040d}--full": _raw(f"{n:040d}", when=old)
        for n in range(_gate_record.KEEP + 5)
    }
    stale[f"{tree}--full"] = _raw(tree, when=old)
    assert (
        git.put(state_store(work), _gate_record.SERIES.ref, stale, message="old") == ""
    )
    # Eight days old: the row proves nothing, however exact its tree.
    assert _gate_record.covering(work)[0] is None
    # A write keeps the newest KEEP rows, the new one among them.
    assert "recorded" in _gate_record.remember(work, tree=tree, packages=None)
    kept, why = _gate_record.rows(work)
    assert why == "" and len(kept) == _gate_record.KEEP and kept[0].tree == tree
    proof, _ = _gate_record.covering(work)
    assert proof is not None and proof.steps[0].when != old


# --- the chain ----------------------------------------------------------------------


def test_a_chain_of_steps_proves_its_tree_back_to_a_full_row(
    work: Path, unverified: None
) -> None:
    first = tree_id(work)
    assert "(full)" in _gate_record.remember(work, tree=first, packages=None)
    second = _commit(work, "a.txt")
    assert "(step)" in _gate_record.remember(
        work, tree=second, packages=("packages/x",), base_tree=first
    )
    third = _commit(work, "b.txt")
    _gate_record.remember(work, tree=third, packages=(), base_tree=second)
    proof, why = _gate_record.covering(work)
    assert proof is not None and why == ""
    assert [row.tree for row in proof.steps] == [third, second, first]
    assert proof.root == "full" and proof.root_tree == first
    assert proof.describe() == f"2 step(s) on a full gate of {first[:12]}"


def test_a_chain_rests_on_a_tree_cis_record_holds(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tree_id(work)
    monkeypatch.setattr(_gate_record, "verified_roots", lambda root, b: ({base}, ""))
    # HEAD's own tree is CI's: a chain with no step at all.
    proof, _ = _gate_record.covering(work)
    assert proof is not None and proof.steps == () and proof.root == "verified"
    assert proof.describe() == f"CI's record of {base[:12]}"
    # One step from it.
    second = _commit(work, "a.txt")
    _gate_record.remember(work, tree=second, packages=("packages/x",), base_tree=base)
    proof, _ = _gate_record.covering(work)
    assert proof is not None and proof.root_tree == base and len(proof.steps) == 1
    assert proof.describe() == f"1 step(s) on CI's record of {base[:12]}"


def test_verified_roots_are_the_trees_of_heads_history_cis_record_holds(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _verified

    base = tree_id(work)
    parent = _commit(work, "a.txt")
    tip = _commit(work, "b.txt")
    monkeypatch.setattr(
        "livery.git.remote_snapshot",
        lambda root, **kw: __import__("contextlib").nullcontext(),
    )
    # The record could not be read: rootless, and the line says why.
    monkeypatch.setattr(_verified, "held", lambda root, trees: (set(), "down"))
    roots, why = _gate_record.verified_roots(work, "main")
    assert roots == set() and "could not be read (down)" in why
    # The record holds none of them: the merge base and the reach are named.
    asked: list[list[str]] = []

    def noting(root: Path, trees: list[str]) -> tuple[set[str], str]:
        asked.append(list(trees))
        return set(), ""

    monkeypatch.setattr(_verified, "held", noting)
    roots, why = _gate_record.verified_roots(work, "main")
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
    assert _gate_record.verified_roots(work, "main") == ({parent}, "")
    # The merge base is a root as well.
    held.add(base)
    assert _gate_record.verified_roots(work, "main") == ({base, parent}, "")


def test_verified_roots_read_cis_record_from_the_mirror_and_never_origin(
    work: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A machine's gate roots its chain on what the last sync fetched, offline."""
    from livery.workshop import _verified

    base = tree_id(work)
    run = _ci_context.RunContext("github", "7", "push", "refs/heads/main")
    monkeypatch.setattr(_ci_context, "run_context", lambda environ=None: run)
    assert (
        _verified.SERIES.put(state_store(work), {base: {"scope": "full"}}, message="ci")
        == ""
    )
    monkeypatch.setattr(_ci_context, "run_context", lambda environ=None: None)
    from livery.workshop._series import mirrored_series

    assert "verified" in mirrored_series(work)
    assert git.fetch_store(state_store(work), series=mirrored_series(work))[:2] == (
        1,
        "",
    )
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    with counting_spawns() as spawned, git.fetched_snapshot(state_store(work)):
        assert _gate_record.verified_roots(work, "main") == ({base}, "")
    assert spawned["git fetch"] == 0 and spawned["git ls-remote"] == 0
    # A stamp of another schema: rootless, and the line names the sync.
    older = git.Series("fetched", window=1, ci_only=False, schema=1, local=True)
    assert older.put(state_store(work), {"origin": {"refs": 1}}, message="old") == ""
    with git.fetched_snapshot(state_store(work)):
        roots, why = _gate_record.verified_roots(work, "main")
    assert roots == set() and "has not been fetched" in why and "sync`" in why
    # Never fetched: the same.
    _git(work, "update-ref", "-d", git.FETCHED.ref)
    with git.fetched_snapshot(state_store(work)):
        roots, why = _gate_record.verified_roots(work, "main")
    assert roots == set() and "has not been fetched" in why and "sync`" in why


# --- the reflex's plan --------------------------------------------------------------


def test_the_plan_steps_from_the_nearest_tree_cis_record_holds(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = _commit(work, "a.txt")
    _commit(work, "b.txt")
    monkeypatch.setattr(
        _gate_record, "verified_roots", lambda root, base: ({parent}, "")
    )
    # The tip's own run is not in the record: the step is from its parent.
    step = _gate_record.plan(work)
    assert step.mode == "step" and step.base_tree == parent
    assert step.paths == ("b.txt",)
    # An edit rides in the same step.
    (work / "c.txt").write_text("c\n")
    step = _gate_record.plan(work)
    assert step.base_tree == parent and step.paths == ("b.txt", "c.txt")
    # Once the record holds the tip, the clean tree is proved outright.
    monkeypatch.setattr(
        _gate_record,
        "verified_roots",
        lambda root, base: ({parent, tree_id(root)}, ""),
    )
    (work / "c.txt").unlink()
    proved = _gate_record.plan(work)
    assert proved.mode == "proved"
    assert proved.why == f"CI's record of {tree_id(work)[:12]}"


def test_the_plan_steps_from_the_proved_tree_nearest_by_delta(
    work: Path, unverified: None
) -> None:
    first = _gate_record.plan(work)
    _gate_record.remember(work, tree=first.tree, packages=None)
    _commit(work, "a.txt")
    _commit(work, "b.txt")
    # A green check of a dirty tree: its row is one path from the next
    # edit, where the history's proved tree is four.
    (work / "c.txt").write_text("c\n")
    dirty = git.working_tree_id(work)
    _gate_record.remember(work, tree=dirty, packages=(), base_tree=first.tree)
    # A row whose tree git does not have is passed over.
    _gate_record.remember(work, tree="f" * 40, packages=(), base_tree=first.tree)
    (work / "d.txt").write_text("d\n")
    step = _gate_record.plan(work)
    assert step.mode == "step" and step.base_tree == dirty
    assert step.paths == ("d.txt",)


def test_the_plan_is_proved_a_step_or_full(work: Path, unverified: None) -> None:
    # Nothing in reach: the whole gate runs and roots a chain.
    first = _gate_record.plan(work)
    assert first.mode == "full" and first.tree == tree_id(work)
    _gate_record.remember(work, tree=first.tree, packages=None)
    # The same tree again: proved, nothing to run.
    assert _gate_record.plan(work).mode == "proved"
    # A dirty tree: a step from HEAD's proved tree, the delta named.
    (work / "packages").mkdir()
    (work / "packages" / "x.txt").write_text("x\n")
    step = _gate_record.plan(work)
    assert step.mode == "step" and step.base_tree == first.tree
    assert step.paths == ("packages/x.txt",)
    assert step.tree == git.working_tree_id(work)
    _gate_record.remember(
        work, tree=step.tree, packages=("packages/x",), base_tree=first.tree
    )
    # The commit that takes everything has the proved tree: nothing to run.
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "feat: x")
    assert _gate_record.plan(work).mode == "proved"
    # Two commits later, the nearest proved tree in the history is the base.
    _commit(work, "a.txt")
    _commit(work, "b.txt")
    later = _gate_record.plan(work)
    assert later.mode == "step" and later.base_tree == step.tree
    assert later.paths == ("a.txt", "b.txt")


# --- a CI leg's plan: the reflex's rule over CI's record ---------------------------

PULL = _ci_context.RunContext(
    "github",
    "8",
    "pull_request",
    "refs/pull/1/merge",
    base_ref="main",
    head_ref="feat/one",
)


def _stamped(
    work: Path,
    tree: str,
    *,
    merged: tuple[str, str] | None = None,
    legs: tuple[str, ...] = ("check (a)",),
) -> None:
    """*tree* on CI's record in full, as a run of feat/one stamps it, naming *legs*."""
    from livery.workshop import _verified

    with pytest.MonkeyPatch.context() as ci:
        ci.setattr(_ci_context, "run_context", lambda environ=None: PULL)
        why = _verified.stamp(
            work,
            PULL,
            tree=tree,
            sha="a" * 40,
            legs=legs,
            branch="feat/one",
            merged=merged,
        )
    assert why == ""


def _merged(work: Path, branch: str) -> tuple[str, str, str]:
    """Check out *branch* merged onto origin/main, as a pull request's run does.

    Returns the base commit, the head commit, and the merge's tree.
    """
    base = _git(work, "rev-parse", "origin/main").strip()
    head = _git(work, "rev-parse", branch).strip()
    _git(work, "checkout", "-q", "--detach", base)
    _git(work, "merge", "-q", "--no-ff", "--no-edit", branch)
    return base, head, tree_id(work)


def _pushed_again(work: Path, name: str) -> None:
    """A further commit on feat/one, and its merge checked out."""
    _git(work, "checkout", "-q", "feat/one")
    _commit(work, name)
    _merged(work, "feat/one")


def test_a_leg_with_no_proved_tree_steps_from_the_merge_base_and_says_why(
    work: Path, tmp_path: Path
) -> None:
    main = tree_id(work)
    _git(work, "checkout", "-q", "-b", "feat/one")
    _commit(work, "one.txt")
    _merged(work, "feat/one")
    step = _gate_record.leg_plan(work, base="main", branch="feat/one")
    assert step.mode == _gate_record.MERGE_BASE
    assert (step.base_tree, step.paths) == (main, ("one.txt",))
    assert step.why == "CI's record proves no tree in reach"
    # A record the store cannot read steps the same way, in its words.
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    step = _gate_record.leg_plan(work, base="main", branch="feat/one")
    assert step.mode == _gate_record.MERGE_BASE and step.base_tree == main
    assert step.why.startswith("CI's record could not be read (")


def test_a_leg_without_a_merge_base_runs_everything(work: Path) -> None:
    step = _gate_record.leg_plan(work, base="gone", branch="feat/one")
    assert step.mode == "full"
    assert step.why.startswith("no merge base with origin/gone (")


def test_a_merge_row_counts_only_when_its_commits_rebuild_its_tree(work: Path) -> None:
    _git(work, "checkout", "-q", "-b", "feat/one")
    _commit(work, "one.txt")
    base, head, first = _merged(work, "feat/one")
    # A head this clone lacks, as after a force-push, and commits that
    # rebuild another tree than the row's: neither row is a candidate.
    _stamped(work, first, merged=(base, "f" * 40))
    _stamped(work, "e" * 40, merged=(base, head))
    _pushed_again(work, "two.txt")
    step = _gate_record.leg_plan(work, base="main", branch="feat/one")
    assert step.mode == _gate_record.MERGE_BASE
    assert step.paths == ("one.txt", "two.txt")
    # Another branch's merge is not rebuilt for this one.
    _stamped(work, first, merged=(base, head))
    step = _gate_record.leg_plan(work, base="main", branch="feat/two")
    assert step.mode == _gate_record.MERGE_BASE


def test_a_second_push_steps_from_the_first_pushs_merge(work: Path) -> None:
    main = tree_id(work)
    _git(work, "checkout", "-q", "-b", "feat/one")
    _commit(work, "one.txt")
    base, head, first = _merged(work, "feat/one")
    assert git.merge_parents(work) == (base, head)
    # main's own run proved its tree: the first push steps from it.
    _stamped(work, main)
    step = _gate_record.leg_plan(work, base="main", branch="feat/one")
    assert (step.mode, step.base_tree, step.paths) == ("step", main, ("one.txt",))
    assert step.why == "proved by run 8"
    # The first push's run proved its merge and named the two commits.
    _stamped(work, first, merged=(base, head))
    _pushed_again(work, "two.txt")
    step = _gate_record.leg_plan(work, base="main", branch="feat/one")
    assert (step.mode, step.base_tree, step.paths) == ("step", first, ("two.txt",))
    assert step.why == f"run 8's merge of {head[:12]} onto {base[:12]}"


def test_a_leg_steps_only_from_a_tree_the_record_proves_for_its_platform(
    work: Path,
) -> None:
    main = tree_id(work)
    _git(work, "checkout", "-q", "-b", "feat/one")
    _commit(work, "one.txt")
    _merged(work, "feat/one")
    # The refusal first: main's tree proved on linux alone is no base
    # for the macos leg, and the line names the leg.
    _stamped(work, main, legs=("check-ubuntu-latest-3.14",))
    step = _gate_record.leg_plan(
        work, base="main", branch="feat/one", leg="check-macos-latest-3.14"
    )
    assert step.mode == _gate_record.MERGE_BASE and step.base_tree == main
    assert step.why == "CI's record proves no tree in reach for check-macos-latest-3.14"
    # The leg the row names steps from it.
    step = _gate_record.leg_plan(
        work, base="main", branch="feat/one", leg="check-ubuntu-latest-3.14"
    )
    assert (step.mode, step.base_tree, step.paths) == ("step", main, ("one.txt",))
    assert step.why == "proved by run 8"
    # A row written with the forge's display name proves the leg it names.
    _stamped(work, main, legs=("check (macos-latest, 3.14)",))
    step = _gate_record.leg_plan(
        work, base="main", branch="feat/one", leg="check-macos-latest-3.14"
    )
    assert (step.mode, step.base_tree) == ("step", main)
    step = _gate_record.leg_plan(
        work, base="main", branch="feat/one", leg="check-ubuntu-latest-3.14"
    )
    assert step.mode == _gate_record.MERGE_BASE
    # Without a leg, any full row is a base.
    step = _gate_record.leg_plan(work, base="main", branch="feat/one")
    assert (step.mode, step.base_tree) == ("step", main)
