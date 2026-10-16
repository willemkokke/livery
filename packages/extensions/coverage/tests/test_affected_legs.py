"""The coverage leg on an affected leg: which suites it measures beside the gate's.

A check leg that narrowed or skipped its gate measures, after it, the
suites neither its branch's record nor main's holds at the suite's
current closure.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from extensions_coverage_steps import python_steps_fixture  # noqa: F401
from livery import git
from livery.extensions.coverage import _leg as coverage_leg
from livery.extensions.coverage import _record as coverage_record
from livery.workshop import LegScope, _quality, _verified, leg_scope
from livery.workshop._ci_context import RunContext
from livery.workshop._gate_record import Plan
from livery.workshop._packages import Package


def _root(tmp_path: Path, ci: str, listed: str = "[]") -> Path:
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "workshop.toml").write_text(
        f'[workspace]\nextensions = {listed}\n\n[forge]\nkind = "gitea"\n\n[ci]\n{ci}'
    )
    return tmp_path


def _run(event: str, base: str, leg: str = "check-a") -> RunContext:
    return RunContext("gitea", "7", event, "refs/pull/3/merge", "a" * 40, base, leg=leg)


def _member(root: Path, name: str, *, suite: bool = True) -> Package:
    directory = root / "packages" / name
    directory.mkdir(parents=True)
    (directory / "workshop.toml").write_text(
        f'extensions = ["python"]\nname = "livery-{name}"\n'
    )
    if suite:
        (directory / "tests").mkdir()
    return Package(
        directory=directory,
        path=f"packages/{name}",
        name=f"livery-{name}",
        depends=(),
        extensions=("python",),
    )


def _stepping(
    monkeypatch: pytest.MonkeyPatch, *paths: str, bases: list[str] | None = None
) -> list[str]:
    """Hand the leg a plan stepping from tree ``b...`` over *paths*; the bases asked."""
    asked = [] if bases is None else bases

    def _plan(root: Path, base: str, run: RunContext) -> Plan:
        asked.append(base)
        return Plan("step", "t" * 40, base_tree="b" * 40, paths=tuple(paths))

    monkeypatch.setattr("livery.workshop._quality._leg_plan", _plan)
    return asked


# --- the coverage store decides which skipped suites run anyway ---------------


def _measured(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """Stand in for the run-a-phase seam: each run's units, by path."""
    ran: list[tuple[str, ...]] = []

    def _run_phase(phase: str, packages: tuple[Package, ...]) -> None:
        assert phase == "test"
        ran.append(tuple(package.path for package in packages))

    monkeypatch.setattr("livery.workshop.run_phase_over", _run_phase)
    return ran


def test_a_leg_skips_a_suite_on_a_current_row_and_never_on_a_kept_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A kept row lives a day from the write that kept it. A leg that
    # skipped on one could outlast it before the run's union reads, so a
    # leg reads the current rows alone and the union finds the rest.
    root = _root(tmp_path, "affected-legs = true\n")
    x, y = _member(root, "x"), _member(root, "y")
    monkeypatch.setattr(coverage_record, "closure_id", lambda git, ps, p: "k" * 64)
    unit = coverage_record.Unit("packages/y", "k" * 64, "5", "a" * 40, ())
    kept = coverage_record.Kept(
        coverage_record.kept_name(
            "packages/y", "k" * 64, datetime(2026, 10, 1, 2, 5, tzinfo=UTC)
        ),
        unit,
    )
    monkeypatch.setattr(
        coverage_record,
        "recorded",
        lambda root, *, leg, base="main": coverage_record.Record({}, kept=(kept,)),
    )
    widened = coverage_leg.with_unstored_suites(
        root, _run("pull_request", "main"), (x, y), (x,)
    )
    assert widened == (x, y)
    assert (
        "coverage store: packages/y runs, nothing to reuse (on this leg main's"
        " record holds no measurement of it)" in capsys.readouterr().out
    )


def test_a_leg_without_a_label_or_an_identity_runs_every_suite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _root(tmp_path, "affected-legs = true\n")
    x, y = _member(root, "x"), _member(root, "y")
    z = _member(root, "z", suite=False)
    monkeypatch.setattr(coverage_record, "closure_id", lambda git, ps, p: "k" * 64)
    widened = coverage_leg.with_unstored_suites(
        root, _run("pull_request", "main", ""), (x, y, z), (x,)
    )
    assert widened == (x, y)  # z has no suite to run
    assert (
        "packages/y runs, nothing to reuse (this leg has no label)"
        in capsys.readouterr().out
    )
    monkeypatch.setattr(
        coverage_record,
        "recorded",
        lambda root, *, leg, base="main": coverage_record.Record({}),
    )

    def _no_identity(root: object, ps: object, p: object) -> str:
        raise git.GitError("HEAD:packages/y is not in HEAD")

    monkeypatch.setattr(coverage_record, "closure_id", _no_identity)
    widened = coverage_leg.with_unstored_suites(
        root, _run("pull_request", "main"), (x, y, z), (x,)
    )
    assert widened == (x, y)
    assert "its closure has no identity" in capsys.readouterr().out


def test_a_suite_the_store_holds_stays_skipped_and_a_miss_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _root(tmp_path, "affected-legs = true\n")
    x, y, z = _member(root, "x"), _member(root, "y"), _member(root, "z")
    monkeypatch.setattr("livery.workshop._quality.workspace_root", lambda: root)
    monkeypatch.setattr("livery.workshop._quality._packages", lambda: (x, y, z))
    monkeypatch.setattr(
        "livery.workshop._ci_context.run_context",
        lambda environ=None: _run("pull_request", "main"),
    )
    _stepping(monkeypatch, "packages/x/a.py")
    monkeypatch.setattr("livery.workshop._quality._affected", lambda root, step: (x,))
    monkeypatch.setattr(coverage_record, "closure_id", lambda git, ps, p: "k" * 64)
    # The record holds y at the current closure and z at another: one
    # read decides both, and only z runs.
    held = {
        "packages/y": coverage_record.Unit("packages/y", "k" * 64, "5", "a" * 40, ()),
        "packages/z": coverage_record.Unit("packages/z", "j" * 64, "4", "a" * 40, ()),
    }
    reads: list[tuple[str, str]] = []

    def _recorded(
        root: Path, *, leg: str, base: str = "main"
    ) -> coverage_record.Record:
        reads.append((leg, base))
        return coverage_record.Record(held)

    monkeypatch.setattr(coverage_record, "recorded", _recorded)
    gated: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        "livery.workshop._quality._scoped_check",
        lambda subset, *, fix=False, tests=None, examples=(), **_rest: gated.append(
            tuple(p.path for p in subset)
        ),
    )
    # The gate judges what the change affects and reads no record.
    _quality.check()
    out = capsys.readouterr().out
    assert gated == [("packages/x",)]
    assert reads == []
    assert "affected: packages/x" in out
    marker = leg_scope(root)
    assert marker == LegScope("affected", ("packages/x",), "check-a", "b" * 40)
    # The coverage leg then measures the suite no record can supply.
    ran = _measured(monkeypatch)
    measured = coverage_leg.top_up(root, (x, y, z), marker)
    out = capsys.readouterr().out
    assert ran == [("packages/z",)]
    assert reads == [("check-a", "main")]  # no branch on this run: main's alone
    assert (
        "coverage store: packages/z runs, nothing to reuse (on this leg main's"
        " record holds it at closure jjjjjjjjjjjj, not kkkkkkkkkkkk)" in out
    )
    assert "packages/y runs" not in out
    assert "measuring: packages/z run for their lines alone\n" in out
    assert measured == LegScope(
        "affected", ("packages/x", "packages/z"), "check-a", "b" * 40
    )
    # An unreadable record runs every skipped suite and names the reason.
    monkeypatch.setattr(
        coverage_record,
        "recorded",
        lambda root, *, leg, base="main": coverage_record.Record(
            {}, failed=True, reason="down"
        ),
    )
    widened = coverage_leg.with_unstored_suites(
        root, _run("pull_request", "main"), (x, y, z), (x,)
    )
    out = capsys.readouterr().out
    assert widened == (x, y, z)
    assert (
        "coverage store: packages/y runs, nothing to reuse (on this leg main's"
        " record could not be read (down))" in out
    )
    assert "packages/z runs, nothing to reuse (on this leg main's record" in out
    # A pull request's leg asks its own branch's record before main's, and
    # a unit either holds at its closure is skipped.
    reads.clear()
    own = _run("pull_request", "main")
    own = RunContext(
        own.forge,
        own.run_id,
        own.event,
        own.ref,
        own.head_sha,
        own.base_ref,
        leg=own.leg,
        head_ref="feat/x",
    )
    by_base = {
        "feat/x": coverage_record.Record(
            {
                "packages/y": coverage_record.Unit(
                    "packages/y", "k" * 64, "9", "a" * 40, ()
                )
            }
        ),
        "main": coverage_record.Record(
            {
                "packages/z": coverage_record.Unit(
                    "packages/z", "k" * 64, "5", "a" * 40, ()
                )
            }
        ),
    }

    def _by_base(root: Path, *, leg: str, base: str = "main") -> coverage_record.Record:
        reads.append((leg, base))
        return by_base[base]

    monkeypatch.setattr(coverage_record, "recorded", _by_base)
    assert coverage_leg.with_unstored_suites(root, own, (x, y, z), (x,)) == (x,)
    assert reads == [("check-a", "feat/x"), ("check-a", "main")]
    assert "runs, nothing to reuse" not in capsys.readouterr().out


# --- the workspace's own tests, a unit of the engine ---------------------------


def test_a_workspace_tests_change_narrows_to_that_unit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    python_steps: object,
) -> None:
    from livery.workshop._packages import workspace_suite

    # The root lists python, so a test step naming python runs its own
    # tests, which hold the root's set.
    root = _root(tmp_path, "affected-legs = true\n", '["python"]')
    x, y = _member(root, "x"), _member(root, "y")
    (root / "tests").mkdir()
    unit = workspace_suite(root)
    assert unit is not None
    monkeypatch.setattr("livery.workshop._quality.workspace_root", lambda: root)
    monkeypatch.setattr("livery.workshop._quality._packages", lambda: (x, y))
    monkeypatch.setattr(
        "livery.workshop._ci_context.run_context",
        lambda environ=None: _run("pull_request", "main"),
    )
    _stepping(monkeypatch, "tests/test_it.py")
    monkeypatch.setattr(
        "livery.workshop._quality._affected", lambda root, step: (unit,)
    )
    monkeypatch.setattr(coverage_record, "closure_id", lambda git, ps, p: "k" * 64)
    held = {
        path: coverage_record.Unit(path, "k" * 64, "5", "a" * 40, ())
        for path in ("packages/x", "packages/y")
    }
    monkeypatch.setattr(
        coverage_record,
        "recorded",
        lambda root, *, leg, base="main": coverage_record.Record(held),
    )
    gated: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        "livery.workshop._quality._scoped_check",
        lambda subset, *, fix=False, tests=None, examples=(), **_rest: gated.append(
            tuple(p.path for p in subset)
        ),
    )
    _quality.check()
    out = capsys.readouterr().out
    assert gated == [("tests",)]
    assert "affected: tests" in out
    assert leg_scope(root) == LegScope("affected", ("tests",), "check-a", "b" * 40)
    # A suite the records cannot supply runs in the coverage leg, beside
    # the unit, which stays in the scope its file names.
    del held["packages/y"]
    gated.clear()
    _quality.check()
    assert gated == [("tests",)]
    ran = _measured(monkeypatch)
    measured = coverage_leg.top_up(root, (x, y), leg_scope(root))
    assert ran == [("packages/y",)]
    assert measured.packages == ("packages/y", "tests")
    assert (
        "coverage store: packages/y runs, nothing to reuse" in capsys.readouterr().out
    )


# --- a proved tree measures what main's record cannot supply ------------------


def _proved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, tuple[Package, ...], list[tuple[str, ...]]]:
    """A root whose tree a check leg proved, two members in it; the runs measured.

    The root lists python, and its own tests hold the root's set.
    """
    root = _root(tmp_path, "affected-legs = true\n", '["python"]')
    x, y = _member(root, "x"), _member(root, "y")
    (root / "tests").mkdir()
    monkeypatch.setattr("livery.workshop._quality.workspace_root", lambda: root)
    monkeypatch.setattr("livery.workshop._quality._packages", lambda: (x, y))
    monkeypatch.setattr(
        "livery.workshop._ci_context.run_context",
        lambda environ=None: _run("push", "", "check-a"),
    )
    proved = _verified.Verified(
        "t", "5", "a" * 40, "full", ("check-a",), branch="feat/x"
    )
    monkeypatch.setattr(
        "livery.workshop._quality.verified_already", lambda root: proved
    )
    monkeypatch.setattr(_verified, "tree_id", lambda root, ref="HEAD": "t")
    monkeypatch.setattr(_verified, "record", lambda root, tree: (proved, ""))
    monkeypatch.setattr(coverage_record, "closure_id", lambda git, ps, p: "k" * 64)
    return root, (x, y), _measured(monkeypatch)


def test_a_proved_tree_with_no_test_step_listed_measures_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A suite no listed step runs has no record to miss.
    root, packages, ran = _proved(tmp_path, monkeypatch)
    monkeypatch.setattr(
        coverage_record,
        "recorded",
        lambda root, *, leg, base="main": coverage_record.Record({}),
    )
    _quality.check()
    marker = leg_scope(root)
    assert marker.scope == "verified"
    assert coverage_leg.top_up(root, packages, marker) == marker
    assert ran == []


def test_a_proved_tree_measures_the_units_the_record_cannot_supply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    python_steps: object,
) -> None:
    root, packages, ran = _proved(tmp_path, monkeypatch)
    # Every unit recorded at its closure: the leg runs nothing.
    every = {
        path: coverage_record.Unit(path, "k" * 64, "5", "a" * 40, ())
        for path in ("packages/x", "packages/y", "tests")
    }
    reads: list[tuple[str, str]] = []

    def _every(root: Path, *, leg: str, base: str = "main") -> coverage_record.Record:
        reads.append((leg, base))
        return coverage_record.Record(every)

    monkeypatch.setattr(coverage_record, "recorded", _every)
    _quality.check()
    marker = leg_scope(root)
    assert marker.scope == "verified"
    assert coverage_leg.top_up(root, packages, marker) == marker
    assert ran == []
    # The branch the verified row names is asked first, then main.
    assert reads == [("check-a", "feat/x"), ("check-a", "main")]
    # y and the workspace tests moved: the leg runs their tests alone,
    # measured, and its file names the measured scope.
    moved = dict(every)
    moved["packages/y"] = coverage_record.Unit(
        "packages/y", "j" * 64, "4", "a" * 40, ()
    )
    del moved["tests"]
    monkeypatch.setattr(
        coverage_record,
        "recorded",
        lambda root, *, leg, base="main": coverage_record.Record(moved),
    )
    _quality.check()
    assert leg_scope(root).scope == "verified"
    measured = coverage_leg.top_up(root, packages, leg_scope(root))
    out = capsys.readouterr().out
    assert ran == [("packages/y", "tests")]
    assert "measuring: packages/y, tests run for their lines alone" in out
    assert measured == LegScope("measured", ("packages/y", "tests"), "check-a")
