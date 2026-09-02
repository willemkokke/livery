"""The one conformance suite, run against the GitLab backend.

Modes as for the Gitea harness: default replays the committed
cassettes with no container; ``LIVERY_FORGE_RECORD=1`` records from
the live compose container; ``LIVERY_FORGE_LIVE=1`` runs live without
recording.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from _gitlab_driver import GitlabConformanceDriver
from livery.forge import Unsupported
from livery.forge.testing import (
    SCENARIOS,
    Cassette,
    RecordingOpener,
    ReplayOpener,
    Scenario,
)

CASSETTES = Path(__file__).parent / "cassettes" / "gitlab"

RECORD = os.environ.get("LIVERY_FORGE_RECORD") == "1"
LIVE = RECORD or os.environ.get("LIVERY_FORGE_LIVE") == "1"

REPLAY_TOKEN = "replay-token"


def _dev_env() -> dict[str, str]:
    """Container credentials: the live environment, then the shared file.

    Under `fm` the cascade already exported them; a bare pytest run
    reads the shared env file the containers were seeded into.
    """
    import footman

    pairs = {
        key: os.environ[key]
        for key in ("GITEA_URL", "GITEA_TOKEN", "GITLAB_URL", "GITLAB_TOKEN")
        if os.environ.get(key)
    }
    shared = footman.config_dir() / ".repo.shared.env"
    if shared.is_file():
        for line in shared.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, _, value = line.partition("=")
                pairs.setdefault(key.strip(), value.strip())
    return pairs


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
def test_gitlab_conformance(scenario: Scenario) -> None:
    if LIVE:
        _run_live(scenario)
    else:
        _run_replay(scenario)


def _run_live(scenario: Scenario) -> None:
    env = _dev_env()
    if "GITLAB_TOKEN" not in env:
        pytest.skip("no GitLab credentials: run `fm forge.dev.up` first")
    cassette = Cassette()
    opener = RecordingOpener(
        cassette,
        scrub_fields=("runners_token", "temp_clone_token"),
        secrets=(env["GITLAB_TOKEN"],),
    )
    driver = GitlabConformanceDriver(
        scenario.name,
        url=env["GITLAB_URL"],
        token=env["GITLAB_TOKEN"],
        opener=opener if RECORD else None,
        live=True,
    )
    if not scenario.applies_to(driver.forge):
        pytest.skip(f"{scenario.name} is out of scope for gitlab")
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
    driver = GitlabConformanceDriver(
        scenario.name,
        url="http://localhost:8929",
        token=REPLAY_TOKEN,
        opener=opener,
        live=False,
    )
    if not scenario.applies_to(driver.forge):
        pytest.skip(f"{scenario.name} is out of scope for gitlab")
    scenario.run(driver)
    opener.verify_exhausted()
