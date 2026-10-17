"""The coverage verbs: every refusal first, then what each does.

``fm coverage.enforce`` judges the floors of the workspace it runs in;
``fm coverage.accept`` writes the row that lowers a mark. The hidden
``coverage.leg`` and ``coverage.union``, which CI runs, gather a leg's
parts and judge the run's union.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

import pytest

from livery import git
from livery.extensions.coverage import _floors as coverage_floors
from livery.extensions.coverage import _leg as coverage_leg
from livery.extensions.coverage import _marks as coverage_marks
from livery.extensions.coverage import _tasks as coverage_tasks
from livery.extensions.coverage import _union as coverage_union
from livery.workshop import LegScope, RunContext
from livery.workshop._packages import Package


def _record(written: list[dict[str, object]]) -> Callable[..., str]:
    """A stand-in for the store's write: it records the row and reports success."""

    def _write(root: Path, **kw: object) -> str:
        written.append(kw)
        return ""

    return _write


def _refusal(action: Callable[[], object]) -> str:
    with pytest.raises(BaseException) as caught:
        action()
    return str(caught.value)


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Package, Package]:
    (tmp_path / "workshop.toml").write_text('[workspace]\nextensions = ["coverage"]\n')
    members = []
    for name, floor in (("x", '"auto-ratchet"'), ("y", "90")):
        directory = tmp_path / "packages" / name
        directory.mkdir(parents=True)
        (directory / "workshop.toml").write_text(
            f'extensions = ["python"]\nname = "livery-{name}"\n\n'
            f"[coverage]\nfloor = {floor}\n"
        )
        members.append(
            Package(
                directory=directory,
                path=f"packages/{name}",
                name=f"livery-{name}",
                depends=(),
                extensions=("python",),
            )
        )
    monkeypatch.setattr(coverage_tasks, "workspace_root", lambda: tmp_path)
    monkeypatch.setattr(
        coverage_tasks, "discover_packages", lambda root: tuple(members)
    )
    monkeypatch.setattr("livery.git.config_get", lambda root, key: "willem")
    return members[0], members[1]


def _mark(value: float) -> coverage_marks.Mark:
    return coverage_marks.Mark("packages/x", value, "ratchet", "run 3", "", "when")


def test_the_refusals_come_before_any_read_of_the_store(
    rig: tuple[Package, Package], monkeypatch: pytest.MonkeyPatch
) -> None:
    def _no_read(root: Path) -> tuple[dict[str, coverage_marks.Mark] | None, str]:
        raise AssertionError("refused before the store is read")

    monkeypatch.setattr(coverage_marks, "marks", _no_read)
    assert "a reason is required" in _refusal(
        lambda: coverage_tasks.coverage_accept("packages/x", 80.0)
    )
    assert "no package at 'packages/z'" in _refusal(
        lambda: coverage_tasks.coverage_accept("packages/z", 80.0, reason="r")
    )
    assert "not under auto-ratchet" in _refusal(
        lambda: coverage_tasks.coverage_accept("packages/y", 80.0, reason="r")
    )
    assert "is a percentage" in _refusal(
        lambda: coverage_tasks.coverage_accept("packages/x", 180.0, reason="r")
    )


def test_an_unreadable_store_a_missing_mark_and_a_raise_refuse_by_name(
    rig: tuple[Package, Package], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(coverage_marks, "marks", lambda root: (None, "remote down"))
    assert "remote down" in _refusal(
        lambda: coverage_tasks.coverage_accept("packages/x", 80.0, reason="r")
    )
    monkeypatch.setattr(coverage_marks, "marks", lambda root: ({}, ""))
    assert "has no mark yet" in _refusal(
        lambda: coverage_tasks.coverage_accept("packages/x", 80.0, reason="r")
    )
    monkeypatch.setattr(
        coverage_marks, "marks", lambda root: ({"packages/x": _mark(90.0)}, "")
    )
    why = _refusal(
        lambda: coverage_tasks.coverage_accept("packages/x", 90.0, reason="r")
    )
    assert "does not lower it" in why and "Raising is the ratchet's own move" in why


def test_a_lowering_writes_a_reasoned_row_naming_who(
    rig: tuple[Package, Package],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        coverage_marks, "marks", lambda root: ({"packages/x": _mark(90.0)}, "")
    )
    written: list[dict[str, object]] = []
    monkeypatch.setattr(coverage_marks, "write_mark", _record(written))
    coverage_tasks.coverage_accept("packages/x", 80.0, reason="  a module moved out  ")
    assert written == [
        {
            "package": "packages/x",
            "value": 80.0,
            "kind": "accept",
            "by": "willem",
            "reason": "a module moved out",
        }
    ]
    out = capsys.readouterr().out
    assert "mark 90.00% -> 80.00% accepted by willem" in out
    assert "reason: a module moved out" in out
    monkeypatch.setattr(coverage_marks, "write_mark", lambda root, **kw: "push refused")
    assert "the mark was not written: push refused" in _refusal(
        lambda: coverage_tasks.coverage_accept("packages/x", 70.0, reason="r")
    )


def test_a_verb_outside_a_workspace_refuses_naming_the_missing_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(coverage_tasks, "workspace_root", lambda: None)
    assert _refusal(coverage_tasks.coverage_enforce) == (
        "no workspace: no workshop.toml above the working directory"
    )


def test_coverage_enforce_judges_the_packages_of_the_workspace_it_runs_in(
    rig: tuple[Package, Package], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[Path, tuple[str, ...]]] = []
    monkeypatch.setattr(
        coverage_floors,
        "enforce_coverage",
        lambda root, packages: seen.append(
            (root, tuple(package.path for package in packages))
        ),
    )
    coverage_tasks.coverage_enforce()
    assert seen == [(tmp_path, ("packages/x", "packages/y"))]


# --- the verbs CI runs --------------------------------------------------------


def _outside_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    # A runner's own environment would make this a CI run: scrubbed,
    # so the test reads the same on a runner as on a desk.
    for name in ("GITHUB_ACTIONS", "GITEA_ACTIONS", "GITLAB_CI"):
        monkeypatch.delenv(name, raising=False)


def test_the_leg_gathers_its_parts_under_the_scope_its_top_up_measured(
    rig: tuple[Package, Package], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = LegScope("affected", ("packages/x",), "check-a")
    widened = LegScope("affected", ("packages/x", "packages/y"), "check-a")
    monkeypatch.setattr("livery.workshop.leg_scope", lambda root: gate)
    topped: list[tuple[Path, tuple[str, ...], LegScope]] = []
    gathered: list[tuple[Path, tuple[str, ...], LegScope | None]] = []

    def _top_up(
        root: Path, packages: tuple[Package, ...], marker: LegScope
    ) -> LegScope:
        topped.append((root, tuple(package.path for package in packages), marker))
        return widened

    def _gather(
        root: Path, packages: tuple[Package, ...], *, marker: LegScope | None = None
    ) -> None:
        gathered.append((root, tuple(package.path for package in packages), marker))

    monkeypatch.setattr(coverage_leg, "top_up", _top_up)
    monkeypatch.setattr(coverage_leg, "gather_leg", _gather)
    coverage_tasks.coverage_leg()
    assert topped == [(tmp_path, ("packages/x", "packages/y"), gate)]
    assert gathered == [(tmp_path, ("packages/x", "packages/y"), widened)]


def test_the_union_outside_ci_refuses_and_lists_no_store(
    rig: tuple[Package, Package], monkeypatch: pytest.MonkeyPatch
) -> None:
    _outside_ci(monkeypatch)

    def _no_listing(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("outside CI there is no run's refs to list")

    monkeypatch.setattr("livery.git.remote_snapshot", _no_listing)
    assert "outside CI there is no run" in _refusal(coverage_tasks.coverage_union)


def test_the_union_reads_inside_one_listing_and_leaves_the_rounded_figures(
    rig: tuple[Package, Package],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    x, _y = rig
    run = RunContext("gitea", "7", "push", "refs/heads/main", leg="gate")
    monkeypatch.setattr("livery.workshop.ci_run", lambda: run)
    events: list[str] = []
    fetched: list[tuple[str, ...]] = []

    @contextmanager
    def _snapshot(store: object, *, fetch: Iterable[str] = ()) -> Iterator[None]:
        fetched.append(tuple(fetch))
        events.append("listed")
        yield None
        events.append("closed")

    judged: tuple[Package, ...] = (x,)

    def _combine(root: Path, packages: tuple[Package, ...]) -> tuple[Package, ...]:
        events.append("unioned")
        return judged

    enforced: list[tuple[str, ...]] = []

    def _enforce(root: Path, packages: tuple[Package, ...]) -> dict[str, float]:
        enforced.append(tuple(package.path for package in packages))
        return {"packages/x": 87.6543}

    left: list[tuple[Path, str, dict[str, float], str]] = []

    def _leave(
        root: Path, name: str, packages: Mapping[str, float], *, unit: str = ""
    ) -> str:
        left.append((root, name, dict(packages), unit))
        return "the coverage section"

    monkeypatch.setattr("livery.git.remote_snapshot", _snapshot)
    monkeypatch.setattr(coverage_union, "combine_union", _combine)
    monkeypatch.setattr(coverage_floors, "enforce_coverage", _enforce)
    monkeypatch.setattr("livery.workshop.leave_section", _leave)
    coverage_tasks.coverage_union()
    # One listing holds the run's refs and main's record, around the union:
    # main's run names no head branch.
    assert fetched == [("run/7/", git.NAMESPACE + "coverage/main/")]
    assert events == ["listed", "unioned", "closed"]
    assert enforced == [("packages/x",)]
    assert left == [(tmp_path, "coverage", {"packages/x": 87.65}, "%")]
    assert "  the coverage section\n" in capsys.readouterr().out
    # A union with nothing to judge enforces nothing and leaves no section.
    judged = ()
    coverage_tasks.coverage_union()
    assert enforced == [("packages/x",)] and len(left) == 1
