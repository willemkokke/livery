"""``fm coverage.accept``: every refusal first, then the row it writes."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from livery.workshop import _coverage_marks, _quality
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
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nlayers = ["livery.workshop"]\n'
    )
    members = []
    for name, floor in (("x", '"auto-ratchet"'), ("y", "90")):
        directory = tmp_path / "packages" / name
        directory.mkdir(parents=True)
        (directory / "workshop.toml").write_text(
            f'type = "python"\nname = "livery-{name}"\n\n'
            f"[qa]\ncoverage-floor = {floor}\n"
        )
        members.append(
            Package(
                directory=directory,
                path=f"packages/{name}",
                name=f"livery-{name}",
                type="python",
                depends=(),
            )
        )
    monkeypatch.setattr("livery.workshop._quality.workspace_root", lambda: tmp_path)
    monkeypatch.setattr("livery.workshop._quality._packages", lambda: tuple(members))
    monkeypatch.setattr("livery.workshop._quality._git_identity", lambda root: "willem")
    return members[0], members[1]


def _mark(value: float) -> _coverage_marks.Mark:
    return _coverage_marks.Mark("packages/x", value, "ratchet", "run 3", "", "when")


def test_the_refusals_come_before_any_read_of_the_store(
    rig: tuple[Package, Package], monkeypatch: pytest.MonkeyPatch
) -> None:
    def _no_read(root: Path) -> tuple[dict[str, _coverage_marks.Mark] | None, str]:
        raise AssertionError("refused before the store is read")

    monkeypatch.setattr(_coverage_marks, "marks", _no_read)
    assert "a reason is required" in _refusal(
        lambda: _quality.coverage_accept("packages/x", 80.0)
    )
    assert "no package at 'packages/z'" in _refusal(
        lambda: _quality.coverage_accept("packages/z", 80.0, reason="r")
    )
    assert "not under auto-ratchet" in _refusal(
        lambda: _quality.coverage_accept("packages/y", 80.0, reason="r")
    )
    assert "is a percentage" in _refusal(
        lambda: _quality.coverage_accept("packages/x", 180.0, reason="r")
    )


def test_an_unreadable_store_a_missing_mark_and_a_raise_refuse_by_name(
    rig: tuple[Package, Package], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_coverage_marks, "marks", lambda root: (None, "remote down"))
    assert "remote down" in _refusal(
        lambda: _quality.coverage_accept("packages/x", 80.0, reason="r")
    )
    monkeypatch.setattr(_coverage_marks, "marks", lambda root: ({}, ""))
    assert "has no mark yet" in _refusal(
        lambda: _quality.coverage_accept("packages/x", 80.0, reason="r")
    )
    monkeypatch.setattr(
        _coverage_marks, "marks", lambda root: ({"packages/x": _mark(90.0)}, "")
    )
    why = _refusal(lambda: _quality.coverage_accept("packages/x", 90.0, reason="r"))
    assert "does not lower it" in why and "Raising is the ratchet's own move" in why


def test_a_lowering_writes_a_reasoned_row_naming_who(
    rig: tuple[Package, Package],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        _coverage_marks, "marks", lambda root: ({"packages/x": _mark(90.0)}, "")
    )
    written: list[dict[str, object]] = []
    monkeypatch.setattr(_coverage_marks, "write_mark", _record(written))
    _quality.coverage_accept("packages/x", 80.0, reason="  a module moved out  ")
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
    monkeypatch.setattr(
        _coverage_marks, "write_mark", lambda root, **kw: "push refused"
    )
    assert "the mark was not written: push refused" in _refusal(
        lambda: _quality.coverage_accept("packages/x", 70.0, reason="r")
    )
