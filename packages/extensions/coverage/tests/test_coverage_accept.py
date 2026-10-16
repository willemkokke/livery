"""The verbs a person runs: every refusal first, then what each does.

``fm coverage.enforce`` judges the floors of the workspace it runs in;
``fm coverage.accept`` writes the row that lowers a mark.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from livery.extensions.coverage import _marks as coverage_marks
from livery.extensions.coverage import _tasks as coverage_tasks
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
    from livery.extensions.coverage import _floors as coverage_floors

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
