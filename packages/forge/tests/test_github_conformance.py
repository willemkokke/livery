"""The one conformance suite, run against the GitHub backend.

Modes as for the other harnesses: default replays the committed
cassettes with no network; ``FORGE_RECORD=1`` records against
github.com scratch repositories under the signed-in user;
``FORGE_LIVE=1`` runs live without recording. Live modes
resolve the token the backend's own way (``GITHUB_TOKEN``, then
``gh auth token``) and skip when neither answers.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from _github_driver import GithubConformanceDriver
from livery.forge import Unsupported
from livery.forge._github import _resolve_token
from livery.forge.testing import (
    SCENARIOS,
    Cassette,
    RecordingOpener,
    ReplayOpener,
    Scenario,
)

CASSETTES = Path(__file__).parent / "cassettes" / "github"

RECORD = os.environ.get("FORGE_RECORD") == "1"
LIVE = RECORD or os.environ.get("FORGE_LIVE") == "1"

REPLAY_TOKEN = "replay-token"


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
def test_github_conformance(scenario: Scenario) -> None:
    if LIVE:
        _run_live(scenario)
    else:
        _run_replay(scenario)


def _run_live(scenario: Scenario) -> None:
    token = _resolve_token()
    if not token:
        pytest.skip("no GitHub credential: set GITHUB_TOKEN or `gh auth login`")
    cassette = Cassette()
    opener = RecordingOpener(
        cassette,
        secrets=(token,),
        scrub_fields=("runners_token", "temp_clone_token"),
        # Sealed-box payloads carry an ephemeral key: never the same
        # bytes twice, matched by method and URL alone.
        volatile_bodies=("/actions/secrets/",),
    )
    driver = GithubConformanceDriver(
        scenario.name,
        token=token,
        opener=opener if RECORD else None,
        live=True,
    )
    if not scenario.applies_to(driver.forge):
        pytest.skip(f"{scenario.name} is out of scope for github")
    try:
        scenario.run(driver)
    except Unsupported as exc:
        if RECORD:
            raise
        driver.cleanup()
        # A live leg against a server below the interface floor
        # (gitea.com runs 1.27) skips with the server's words; the
        # recorded suites still pin the behaviour where it exists.
        pytest.skip(str(exc))
    if not RECORD:
        # Cloud accounts meter repositories, so a live run deletes its
        # scratch as each scenario ends. Only on success: a failed
        # scenario's scratch is the evidence the diagnostics dump and
        # the next run's leftover sweep both need.
        driver.cleanup()
    if RECORD:
        cassette.save(CASSETTES / f"{scenario.name}.json")


def _run_replay(scenario: Scenario) -> None:
    path = CASSETTES / f"{scenario.name}.json"
    if not path.exists():
        pytest.skip(f"no cassette recorded for {scenario.name}")
    opener = ReplayOpener(Cassette.load(path), secrets=(REPLAY_TOKEN,))
    driver = GithubConformanceDriver(
        scenario.name,
        token=REPLAY_TOKEN,
        opener=opener,
        live=False,
    )
    if not scenario.applies_to(driver.forge):
        pytest.skip(f"{scenario.name} is out of scope for github")
    scenario.run(driver)
    opener.verify_exhausted()
