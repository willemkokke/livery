"""The provenance partition: every tracked file classifies.

The classifier must answer for anything in the tree, with "yours"
the honest default, and the channels must land where the workspace
knows they belong.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from livery.workshop._provenance import classify, emitted_paths

ROOT = Path(__file__).resolve().parents[1]
SEEDS = ROOT / "packages/workshop/src/livery/workshop/content/seeds"


def _tracked() -> list[Path]:
    listing = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return [Path(line) for line in listing.stdout.splitlines()]


def test_every_tracked_file_classifies() -> None:
    # One emission for the whole tree: classifying hundreds of paths
    # must not render the workflows hundreds of times.
    emitted = emitted_paths(ROOT)
    for path in _tracked():
        answer = classify(ROOT, path, emitted=emitted)
        assert answer.channel and answer.source and answer.edit, path


def test_the_channels_land_where_the_workspace_knows_them() -> None:
    expect = {
        "pyproject.toml": "composed",
        "tasks.py": "composed",
        ".gitignore": "composed",
        ".gitattributes": "composed",
        ".github/workflows/ci.yml": "generated",
        ".github/CODEOWNERS": "generated",
        "workshop.toml": "contract",
        "packages/forge/workshop.toml": "contract",
        ".workshop-rendered": "receipts",
        "packages/forge/cliff.toml": "composed",
        "packages/forge/README.md": "yours",
        "uv.lock": "toolchain",
        "CLAUDE.md": "sync stub",
        "CLAUDE.project.md": "yours",
        "notes/20260830-development-workflows.md": "yours",
        "tests/test_workspace_contracts.py": "seed",
        (
            "packages/workshop/src/livery/workshop/content/"
            "fragments/interaction-voice.md"
        ): "extension content",
    }
    emitted = emitted_paths(ROOT)
    for path, channel in expect.items():
        assert classify(ROOT, Path(path), emitted=emitted).channel == channel, path


def test_the_seed_list_matches_the_project_seeds() -> None:
    from livery.workshop._seeds import PROJECT_SEEDS

    names = set()
    project = SEEDS / "project"
    for path in project.rglob("*"):
        if path.is_file():
            names.add(path.relative_to(project).as_posix().removesuffix(".jinja"))
    assert names == set(PROJECT_SEEDS)


def test_a_precomputed_emission_renders_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Judging hundreds of paths must not render the workflows hundreds
    # of times: with the emitters' set in hand, classify never emits.
    from livery.workshop import _ci_generate

    def _never(root: Path) -> dict[str, str]:
        raise AssertionError("the emission was precomputed; classify must not render")

    monkeypatch.setattr(_ci_generate, "generate", _never)
    emitted = frozenset({".github/workflows/ci.yml"})
    assert classify(ROOT, Path("workshop.toml"), emitted=emitted).channel == "contract"
    assert classify(
        ROOT, Path(".github/workflows/ci.yml"), emitted=emitted
    ).channel == ("generated")
