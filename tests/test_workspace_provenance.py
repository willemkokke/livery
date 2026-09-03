"""The provenance partition: every tracked file classifies.

The classifier must answer for anything in the tree, with "yours"
the honest default, and the channels must land where the workspace
knows they belong.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from livery.workshop._provenance import PROJECT_RENDERED, classify

ROOT = Path(__file__).resolve().parents[1]


def _tracked() -> list[Path]:
    listing = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return [Path(line) for line in listing.stdout.splitlines()]


def test_every_tracked_file_classifies() -> None:
    for path in _tracked():
        answer = classify(ROOT, path)
        assert answer.channel and answer.source and answer.edit, path


def test_the_channels_land_where_the_workspace_knows_them() -> None:
    expect = {
        "pyproject.toml": "rendered",
        "tasks.py": "rendered",
        ".gitignore": "rendered",
        ".github/workflows/ci.yml": "generated",
        ".github/CODEOWNERS": "generated",
        "workshop.toml": "contract",
        "packages/forge/workshop.toml": "contract",
        ".copier-answers.yml": "receipts",
        "packages/forge/.copier-answers.yml": "receipts",
        "packages/forge/cliff.toml": "rendered",
        "packages/forge/README.md": "yours",
        "uv.lock": "toolchain",
        "CLAUDE.md": "sync stub",
        "CLAUDE.project.md": "yours",
        "notes/20260830-development-workflows.md": "yours",
        "tests/test_workspace_contracts.py": "seed",
        (
            "packages/workshop/src/livery/workshop/content/"
            "fragments/interaction-voice.md"
        ): "layer content",
    }
    for path, channel in expect.items():
        assert classify(ROOT, Path(path)).channel == channel, path


def test_the_rendered_list_matches_the_template_tree() -> None:
    from livery.workshop._templates import PROJECT_SEEDS

    names = set()
    project = ROOT / "templates" / "project"
    for path in project.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(project).as_posix()
        if relative.endswith(".jinja"):
            relative = relative[: -len(".jinja")]
        if "_copier_conf" in relative:
            continue  # the answers file: receipts, its own header
        names.add(relative)
    assert names == set(PROJECT_RENDERED) | set(PROJECT_SEEDS)
