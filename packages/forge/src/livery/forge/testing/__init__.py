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

from livery.forge.testing._cassette import (
    FORMAT,
    REDACTED,
    Cassette,
    CassetteError,
    Exchange,
    RecordingOpener,
    ReplayOpener,
    UrlOpener,
)
from livery.forge.testing._conformance import SCENARIOS, ForgeDriver, Scenario
from livery.forge.testing._fake import FakeDriver, FakeForge, Faults

__all__ = [
    "FORMAT",
    "REDACTED",
    "SCENARIOS",
    "Cassette",
    "CassetteError",
    "Exchange",
    "FakeDriver",
    "FakeForge",
    "Faults",
    "ForgeDriver",
    "RecordingOpener",
    "ReplayOpener",
    "Scenario",
    "UrlOpener",
]
