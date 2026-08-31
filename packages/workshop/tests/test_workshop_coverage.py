"""The coverage floors: parent mode, the grace, and the contract read."""

from __future__ import annotations

from pathlib import Path

import pytest
from footman import Failed

from livery.workshop._backends import _python
from livery.workshop._packages import Package

_FAILURES = (SystemExit, Failed)


def _package(tmp_path: Path, name: str, extra: str = "") -> Package:
    directory = tmp_path / "packages" / name
    directory.mkdir(parents=True)
    (directory / "livery.toml").write_text(
        f'type = "python"\nname = "livery-{name}"\n{extra}'
    )
    return Package(
        directory=directory,
        path=f"packages/{name}",
        name=f"livery-{name}",
        type="python",
        depends=(),
    )


def test_the_floor_comes_from_the_contract(tmp_path: Path) -> None:
    bare = _package(tmp_path, "bare")
    assert _python.coverage_floor(bare) is None
    floored = _package(tmp_path, "floored", "[qa]\ncoverage_floor = 87\n")
    assert _python.coverage_floor(floored) == 87.0


def test_enforcement_grants_the_grace_and_no_more(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _package(tmp_path, "thing", "[qa]\ncoverage_floor = 90\n")
    measured = {"packages/thing": 89.6}
    monkeypatch.setattr(_python, "measured_coverage", lambda root, packages: measured)
    _python.enforce_coverage(tmp_path, (package,))  # inside the grace
    measured["packages/thing"] = 89.4
    with pytest.raises(_FAILURES) as caught:
        _python.enforce_coverage(tmp_path, (package,))
    assert "below the committed floor" in str(caught.value)


def test_a_measuring_parent_suspends_the_local_meter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _package(tmp_path, "thing", "[qa]\ncoverage_floor = 1\n")
    seen: list[tuple[str, ...]] = []

    class FakeTool:
        def opts(self, **_kwargs: object) -> FakeTool:
            return self

        def __call__(self, *args: str) -> None:
            seen.append(args)

    monkeypatch.setattr(_python, "pytest", FakeTool())

    def refuse(*_args: object) -> None:
        raise AssertionError("enforcement belongs to the aggregating job")

    monkeypatch.setattr(_python, "enforce_coverage", refuse)
    monkeypatch.setenv("LIVERY_COVERAGE_PARENT", "1")
    _python.run_test(packages=(package,), root=tmp_path)
    assert seen and not any("--cov" in arg for arg in seen[0])


def test_without_a_parent_the_meter_and_the_preview_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _package(tmp_path, "thing", "[qa]\ncoverage_floor = 1\n")
    seen: list[tuple[str, ...]] = []
    enforced: list[Path] = []

    class FakeTool:
        def opts(self, **_kwargs: object) -> FakeTool:
            return self

        def __call__(self, *args: str) -> None:
            seen.append(args)

    monkeypatch.setattr(_python, "pytest", FakeTool())
    monkeypatch.setattr(
        _python, "report_coverage", lambda root, packages: enforced.append(root)
    )
    monkeypatch.delenv("LIVERY_COVERAGE_PARENT", raising=False)
    _python.run_test(packages=(package,), root=tmp_path, scoped=True)
    assert any("--cov=livery" in arg for arg in seen[0])
    assert "packages/thing/tests" not in seen[0]  # no tests dir exists
    assert enforced == [tmp_path]
