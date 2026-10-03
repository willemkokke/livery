"""The layering lint, run over this workspace.

The engine lives in livery.workshop.api.verify_workspace; this test keeps
the whole workspace honest on every gate run: contracts present,
declared edges agreeing with the native manifests both ways, the
graph acyclic, and livery.forge stdlib-only at import time. Every
member it finds is a member of the uv workspace the project file
declares, so a package whose contract the sync has not yet composed
into `pyproject.toml` fails until it is.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from livery.workshop.api import verify_workspace

ROOT = Path(__file__).resolve().parents[1]


def _uv_members() -> set[str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    return set(project["tool"]["uv"]["workspace"]["members"])


def test_the_workspace_keeps_its_layering() -> None:
    packages = verify_workspace(ROOT)
    assert {f"packages/{p.directory.name}" for p in packages} == _uv_members()
