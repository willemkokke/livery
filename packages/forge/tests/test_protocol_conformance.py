"""The conformance suite, run against the fake in two capability shapes."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from livery.forge import Forge, Registry, Repository
from livery.forge.testing import SCENARIOS, FakeDriver, FakeForge, ForgeDriver, Scenario


def _full() -> ForgeDriver:
    return FakeDriver()


def _gitlab_shaped() -> ForgeDriver:
    return FakeDriver(FakeForge(capabilities=("auto_merge", "ci_secrets")))


def _github_shaped() -> ForgeDriver:
    return FakeDriver(
        FakeForge(capabilities=("auto_merge", "force_cancel", "required_contexts"))
    )


_DRIVERS: dict[str, Callable[[], ForgeDriver]] = {
    "fake": _full,
    "fake-github-shaped": _github_shaped,
    "fake-gitlab-shaped": _gitlab_shaped,
}


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
@pytest.mark.parametrize("driver_name", sorted(_DRIVERS))
def test_conformance(driver_name: str, scenario: Scenario) -> None:
    driver = _DRIVERS[driver_name]()
    if not scenario.applies_to(driver.forge):
        pytest.skip(f"{scenario.name} is out of scope for {driver_name}")
    scenario.run(driver)


def test_the_fake_satisfies_the_protocols() -> None:
    # The assignments are the assertion: all four type checkers verify
    # FakeForge structurally satisfies the protocols, so a signature
    # drift in the fake fails the gate, not a consumer.
    forge: Forge = FakeForge()
    repo: Repository = forge.repository("acme", "example")
    assert repo.owner == "acme"
    assert repo.name == "example"


def test_every_scenario_name_is_unique() -> None:
    names = [scenario.name for scenario in SCENARIOS]
    assert len(names) == len(set(names))


def test_registry_protocol_is_importable() -> None:
    # No registry backend exists yet; the protocol must still be a
    # complete, importable contract.
    assert callable(Registry.versions)
