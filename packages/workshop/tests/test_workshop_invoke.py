"""How a check's paths reach its tool: the refusals first, then the engine's counts."""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.footman.api import Failed, fail
from livery.workshop._checks import CheckRecord, GateContext, register_check, run_check
from livery.workshop._invoke import batches, run_batched, runs_whole
from livery.workshop._packages import Package
from workshop_python_checks import python_checks_fixture  # noqa: F401

_FAILURES = (SystemExit, Failed)


def _idle(ctx: GateContext) -> None:
    del ctx


@pytest.fixture
def restored_checks():
    from livery.workshop import _checks

    state = _checks.snapshot()
    yield
    _checks.restore(state)


def test_a_transport_or_threshold_off_the_vocabulary_refuses(restored_checks) -> None:
    del restored_checks
    with pytest.raises(_FAILURES, match="transport is one of argv, file, config"):
        register_check(CheckRecord("acme", "lint", _idle, transport="pipe"))
    with pytest.raises(_FAILURES, match="passes paths by 'argv' alone so far"):
        register_check(CheckRecord("acme", "lint", _idle, transport="file"))
    for threshold in (0.0, 1.5):
        with pytest.raises(_FAILURES, match="threshold is a share of the units"):
            register_check(CheckRecord("acme", "lint", _idle, threshold=threshold))


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


def test_above_the_threshold_the_check_runs_whole() -> None:
    # The fallbacks first: nothing affected never runs whole, and no units
    # at all never does either.
    assert not runs_whole(0, 10, 0.5)
    assert not runs_whole(3, 0, 0.5)
    assert not runs_whole(4, 10, 0.5)
    assert runs_whole(5, 10, 0.5)
    # A threshold of 1 narrows until every unit is affected.
    assert not runs_whole(9, 10, 1.0)
    assert runs_whole(10, 10, 1.0)


def _members(root: Path, count: int) -> tuple[Package, ...]:
    packages = []
    for index in range(count):
        directory = root / "packages" / f"m{index:03d}"
        (directory / "src").mkdir(parents=True)
        (directory / "tests").mkdir()
        packages.append(
            Package(
                directory=directory,
                path=f"packages/{directory.name}",
                name=f"acme-m{index:03d}",
                kind="python",
                depends=(),
            )
        )
    return tuple(packages)


def test_a_workspace_check_runs_once_for_two_hundred_packages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, python_checks: object
) -> None:
    import workshop_python_checks as fake_checks

    monkeypatch.chdir(tmp_path)
    packages = _members(tmp_path, 200)
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        fake_checks,
        "run_lint",
        lambda fix=False, safe_fix=False, paths=(): calls.append(paths),
    )
    # A scoped gate over three quarters of the members: one call carries
    # every affected member's directories.
    subset = packages[:150]
    run_check("lint.fake", GateContext(root=tmp_path, packages=packages, subset=subset))
    assert len(calls) == 1
    assert len(calls[0]) == 300
    # Every member affected: the configured whole, still one call.
    calls.clear()
    run_check(
        "lint.fake", GateContext(root=tmp_path, packages=packages, subset=packages)
    )
    assert calls == [(".",)]
