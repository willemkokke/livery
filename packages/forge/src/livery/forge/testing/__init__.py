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
    "RIG_RECORD",
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
    "rig_record_path",
    "shared_env_path",
]

#: The dev containers' own record of their credentials, beside the
#: shared env file: their seed writes it, and nothing else does.
RIG_RECORD = ".forge-dev.env"


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


def rig_record_path() -> Path:
    """The dev containers' own record of their URLs and tokens.

    Their seed writes it, and nothing else does. The shared env file
    beside it names the cascade's current forge, which a local loop
    environment rewrites with its own Gitea's keys, so a test that
    drives the containers reads their credentials here, and skips
    without it.
    """
    return shared_env_path().parent / RIG_RECORD
