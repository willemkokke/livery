"""How a step's paths reach its tool: the refusals first, then the engine's counts."""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.footman import Failed, fail
from livery.workshop._invoke import batches, run_batched
from livery.workshop._packages import Package
from livery.workshop._phases import PhaseContext
from livery.workshop._steps import run_step
from workshop_python_steps import python_steps_fixture  # noqa: F401

_FAILURES = (SystemExit, Failed)


def test_every_failing_batch_runs_and_one_refusal_names_them_all() -> None:
    ran: list[tuple[str, ...]] = []

    def call(batch: tuple[str, ...]) -> None:
        ran.append(batch)
        if "bad" in batch[0]:
            fail(f"{batch[0]} failed")

    with pytest.raises(_FAILURES) as caught:
        run_batched(["bad-1", "ok", "bad-2"], call, limit=6)
    assert len(ran) == 3  # the first failure stops nothing
    assert "2 calls failed:\nbad-1 failed\nbad-2 failed" in str(caught.value)
    run_batched([], call)  # nothing to pass runs nothing
    assert len(ran) == 3


def test_an_overflowing_path_list_splits_into_the_fewest_calls() -> None:
    # Each path costs eleven bytes with its separator, so four fit in 50.
    paths = [f"p{index:09d}" for index in range(25)]
    calls = batches(paths, limit=50)
    assert [len(call) for call in calls] == [4, 4, 4, 4, 4, 4, 1]
    assert [path for call in calls for path in call] == paths  # order kept
    # A path over the limit on its own still goes, in a call of its own.
    assert batches(["x" * 80, "y"], limit=50) == (("x" * 80,), ("y",))
    assert batches([], limit=50) == ()


def _members(root: Path, count: int) -> tuple[Package, ...]:
    packages = []
    for index in range(count):
        directory = root / "packages" / f"m{index:03d}"
        (directory / "src").mkdir(parents=True)
        (directory / "tests").mkdir()
        # A file in each: a step never names a directory with none to judge.
        (directory / "src" / "module.py").write_text("")
        (directory / "tests" / "test_module.py").write_text("")
        packages.append(
            Package(
                directory=directory,
                path=f"packages/{directory.name}",
                name=f"acme-m{index:03d}",
                depends=(),
                extensions=("python",),
            )
        )
    return tuple(packages)


def test_a_workspace_step_runs_once_for_two_hundred_packages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, python_steps: object
) -> None:
    import workshop_python_steps as fake_steps

    monkeypatch.chdir(tmp_path)
    packages = _members(tmp_path, 200)
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        fake_steps,
        "run_lint",
        lambda fix=False, safe_fix=False, paths=(): calls.append(paths),
    )
    # A scoped gate over three quarters of the members: one call carries
    # every affected member's directories.
    subset = packages[:150]
    run_step("lint.fake", PhaseContext(root=tmp_path, packages=packages, subset=subset))
    assert len(calls) == 1
    assert len(calls[0]) == 300
    # Every member affected: the configured whole, still one call, as
    # an unscoped run has it.
    calls.clear()
    run_step(
        "lint.fake", PhaseContext(root=tmp_path, packages=packages, subset=packages)
    )
    assert calls == [(".",)]
    calls.clear()
    run_step("lint.fake", PhaseContext(root=tmp_path, packages=packages))
    assert calls == [(".",)]
