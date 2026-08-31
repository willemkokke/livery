"""The layering lint, run over this workspace.

The engine lives in livery.workshop.verify_workspace; this test keeps
the whole workspace honest on every gate run: contracts present,
declared edges agreeing with the native manifests both ways, the
graph acyclic, and livery.forge stdlib-only at import time.
"""

from __future__ import annotations

from pathlib import Path

from livery.workshop import verify_workspace

ROOT = Path(__file__).resolve().parents[1]


def test_the_workspace_keeps_its_layering() -> None:
    packages = verify_workspace(ROOT)
    assert {p.name for p in packages} == {"livery-forge", "livery-workshop"}
