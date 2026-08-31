"""The layering lint, run over this workspace.

The engine lives in livery.workshop.verify_workspace; this test keeps
the whole workspace honest on every gate run: contracts present,
declared edges agreeing with the native manifests both ways, the
graph acyclic, and livery.forge stdlib-only at import time. The
member roster comes from the adopted answers file, so a package added
by `fm new.package` is already declared, and a directory dropped in
by hand fails until it is wired.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from livery.workshop import verify_workspace

ROOT = Path(__file__).resolve().parents[1]


def _declared_members() -> set[str]:
    answers = yaml.safe_load((ROOT / ".copier-answers.yml").read_text("utf-8"))
    return {str(member["name"]) for member in answers["packages"]}


def test_the_workspace_keeps_its_layering() -> None:
    packages = verify_workspace(ROOT)
    assert {p.name for p in packages} == _declared_members()
