"""The one conformance suite, run against the Gitea backend.

Three modes, chosen by environment:

- default: replay each scenario's committed cassette; no container, no
  network. This is the merge-path mode.
- ``LIVERY_FORGE_RECORD=1``: run live against the seeded compose
  container and rewrite the cassettes.
- ``LIVERY_FORGE_LIVE=1``: run live without recording.

Live modes read the credentials `fm forge.dev.up` writes to
.forge.dev.env and skip when the file is missing.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from _gitea_driver import ROOT, GiteaConformanceDriver
from livery.forge.testing import (
    SCENARIOS,
    Cassette,
    RecordingOpener,
    ReplayOpener,
    Scenario,
)

CASSETTES = Path(__file__).parent / "cassettes" / "gitea"

RECORD = os.environ.get("LIVERY_FORGE_RECORD") == "1"
LIVE = RECORD or os.environ.get("LIVERY_FORGE_LIVE") == "1"

#: The stand-in credential a replay run holds; scrubbing maps it onto
#: the recording's REDACTED values.
REPLAY_TOKEN = "replay-token"


def _dev_env() -> dict[str, str]:
    env_file = ROOT / ".forge.dev.env"
    if not env_file.is_file():
        return {}
    pairs = {}
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            pairs[key] = value
    return pairs


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
def test_gitea_conformance(scenario: Scenario) -> None:
    if LIVE:
        _run_live(scenario)
    else:
        _run_replay(scenario)


def _run_live(scenario: Scenario) -> None:
    env = _dev_env()
    if "GITEA_TOKEN" not in env:
        pytest.skip("no .forge.dev.env: run `fm forge.dev.up` first")
    cassette = Cassette()
    opener = RecordingOpener(cassette, secrets=(env["GITEA_TOKEN"],))
    driver = GiteaConformanceDriver(
        scenario.name,
        url=env["GITEA_URL"],
        token=env["GITEA_TOKEN"],
        opener=opener if RECORD else None,
        live=True,
    )
    if not scenario.applies_to(driver.forge):
        pytest.skip(f"{scenario.name} is out of scope for gitea")
    scenario.run(driver)
    if RECORD:
        cassette.save(CASSETTES / f"{scenario.name}.json")


def _run_replay(scenario: Scenario) -> None:
    path = CASSETTES / f"{scenario.name}.json"
    if not path.exists():
        pytest.skip(f"no cassette recorded for {scenario.name}")
    opener = ReplayOpener(Cassette.load(path), secrets=(REPLAY_TOKEN,))
    driver = GiteaConformanceDriver(
        scenario.name,
        url="http://localhost:3000",
        token=REPLAY_TOKEN,
        opener=opener,
        live=False,
    )
    if not scenario.applies_to(driver.forge):
        pytest.skip(f"{scenario.name} is out of scope for gitea")
    scenario.run(driver)
    opener.verify_exhausted()
