"""Host detection: orchestration, not presence — hse's second trap, pinned.

No monkeypatching here: the real `hosted()` answers, in a process where
footman is genuinely imported (the dev dependency; its pytest plugin
auto-loads). Which lane a call takes must depend only on whether footman
is actually orchestrating it.
"""

from __future__ import annotations

import sys

import pytest

from livery import toolroom as tools
from livery.toolroom import ToolError


def test_a_bare_call_is_standalone_even_with_footman_imported():
    """The trap itself: footman is in sys.modules, no footman context is
    live, so the call takes the standalone lane and a failure raises
    toolroom's ToolError — never footman's RunFailed — deterministically,
    whatever some other module imported.
    """
    assert "footman" in sys.modules
    with pytest.raises(ToolError):
        tools.python("-c", "raise SystemExit(5)")


def test_recording_is_orchestration_and_cannot_be_bypassed():
    """The other half of the coin: inside recording(), footman IS
    orchestrating, the call routes hosted and is faked into the record —
    detection cannot be sidestepped where the honesty machinery depends
    on it.
    """
    from livery.footman.testing import recording

    with recording() as steps:
        tools.git("definitely-not-run", "for-real")
    assert len(steps) == 1
    assert steps[0].command == "git definitely-not-run for-real"


def test_a_value_read_executes_truthfully_under_recording():
    """The harness-infrastructure lane: `.opts(recorded=False)` is a value
    read, not a story step — it EXECUTES under recording() instead of being
    faked, which is exactly what an availability probe needs. (The GPU-probe
    case from the consumer sweep: convert the probe with recorded=False and
    it answers truthfully inside every recording block.)
    """
    from livery.footman.testing import recording

    with recording() as steps:
        r = tools.python.opts(recorded=False)("-c", "print('truth')")
    assert steps == []
    assert r.stdout.strip() == "truth"
