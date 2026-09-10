"""What every consumer tests against: the verified fake and the fixtures.

Three pieces, one purpose: forge-touching code proves itself without a
forge.

- livery.forge.testing.FakeForge answers the whole protocol from
  memory and injects known forge quirks deterministically through
  livery.forge.testing.Faults.
- livery.forge.testing.SCENARIOS is the one conformance suite. It runs
  against the fake and against every real backend, unchanged, through
  a per-backend livery.forge.testing.ForgeDriver; the fake passing it
  is what makes the fake worth testing against.
- livery.forge.testing.Cassette records real HTTP exchanges once and
  replays them forever, secrets scrubbed, so backend tests gate every
  merge without a network.
"""

from __future__ import annotations

from pathlib import Path

from livery.forge.testing._cassette import (
    FORMAT,
    REDACTED,
    VOLATILE,
    Cassette,
    CassetteError,
    Exchange,
    RecordingOpener,
    ReplayOpener,
    UrlOpener,
)
from livery.forge.testing._conformance import (
    SCENARIOS,
    ForgeDriver,
    Outcome,
    Scenario,
)
from livery.forge.testing._fake import FakeDriver, FakeForge, Faults

__all__ = [
    "FORMAT",
    "REDACTED",
    "SCENARIOS",
    "VOLATILE",
    "Cassette",
    "CassetteError",
    "Exchange",
    "FakeDriver",
    "FakeForge",
    "Faults",
    "ForgeDriver",
    "Outcome",
    "RecordingOpener",
    "ReplayOpener",
    "Scenario",
    "UrlOpener",
    "shared_env_path",
]


def shared_env_path() -> Path:
    """The machine's own shared env file: the one live read a test may make.

    The dev containers' credentials live there. A test that needs them
    names this file by design and skips without it; every other read of
    the runner's directories goes through footman, which the workshop's
    test isolation points at a temporary directory for the session.
    """
    import os
    from pathlib import Path

    home = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(home) / "footman" / ".repo.shared.env"
