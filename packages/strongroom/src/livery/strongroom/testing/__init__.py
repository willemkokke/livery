"""The conformance kit: the scenarios under `spec/conformance`, run against a store.

A scenario is data, so any implementation of the store's API runs the
same suite: [livery.strongroom.testing.load_scenarios][] reads the
scenarios and [livery.strongroom.testing.run_scenario][] runs one
against a store the caller opens. The kit reaches the three seams a
scenario needs through [livery.strongroom.testing.Hooks][], and
[livery.strongroom.testing.PythonHooks][] is the Python store's own. A
failed step raises [livery.strongroom.testing.ConformanceFailure][]
naming the scenario, the step and what was found.

A module of its own, so importing [livery.strongroom.api][] to use a store
never loads the kit.
"""

from __future__ import annotations

from livery.strongroom.testing._conformance import (
    REFUSALS,
    ConformanceFailure,
    Hooks,
    LockHolder,
    PythonHooks,
    Scenario,
    StoreLike,
    load_scenarios,
    run_scenario,
)

__all__ = [
    "REFUSALS",
    "ConformanceFailure",
    "Hooks",
    "LockHolder",
    "PythonHooks",
    "Scenario",
    "StoreLike",
    "load_scenarios",
    "run_scenario",
]
