"""The scoped gate: every verb it schedules actually runs.

The property pinned here is execution, not exit: a built step is not
a run step under footman's block contract, and a gate whose verbs are
built and dropped exits green having checked nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.workshop import _quality
from livery.workshop._backends import _python
from livery.workshop._packages import Package


def _package(tmp_path: Path) -> Package:
    member = tmp_path / "packages" / "one"
    (member / "tests").mkdir(parents=True)
    (member / "pyproject.toml").write_text(
        '[project]\nname = "livery-one"\nversion = "0.1.0"\n'
    )
    return Package(
        directory=member,
        path="packages/one",
        name="livery-one",
        type="python",
        depends=(),
    )


def _record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[list[str], list[dict[str, object]]]:
    ran: list[str] = []
    calls: list[dict[str, object]] = []

    def named(name: str):
        def body(*args: object, **kwargs: object) -> None:
            ran.append(name)
            calls.append({"verb": name, "args": args, **kwargs})

        return body

    monkeypatch.setattr(_quality, "workspace_root", lambda: tmp_path)
    monkeypatch.setattr(_quality, "run_kind_checks", named("kindcheck"))
    monkeypatch.setattr(_python, "run_format", named("format"))
    monkeypatch.setattr(_python, "run_lint", named("lint"))
    monkeypatch.setattr(_python, "run_typecheck", named("typecheck"))
    monkeypatch.setattr(_python, "run_typecomplete", named("typecomplete"))
    monkeypatch.setattr(_python, "run_test", named("test"))
    return ran, calls


def test_a_fixing_gate_refuses_inside_ci(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # CI judges, it never rewrites: a runner's CI variable turns
    # --fix into a taught refusal instead of a silent mutation.
    monkeypatch.setenv("CI", "true")
    with pytest.raises(BaseException, match="never rewritten"):
        _quality.check(fix=True)


def test_the_scoped_gate_runs_every_verb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran, _ = _record(monkeypatch, tmp_path)
    _quality._scoped_check((_package(tmp_path),))
    assert sorted(ran) == [
        "format",
        "kindcheck",
        "lint",
        "test",
        "typecheck",
        "typecomplete",
    ]


def test_the_scoped_fix_mode_rewrites_first_and_still_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The serial rewrites were the silent half of the drift: built
    # outside the block, dropped without even a refusal.
    ran, calls = _record(monkeypatch, tmp_path)
    _quality._scoped_check((_package(tmp_path),), fix=True)
    assert ran[:2] == ["format", "lint"]
    assert sorted(ran) == [
        "format",
        "kindcheck",
        "lint",
        "test",
        "typecheck",
        "typecomplete",
    ]
    rewrites = {c["verb"]: c for c in calls[:2]}
    assert rewrites["format"]["check"] is False
    assert rewrites["lint"]["fix"] is True
