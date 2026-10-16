"""The docs extension's steps: the refusals first, then this workspace's verdict."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from livery.extensions.docs._steps import doclinks_run, link_problems
from livery.footman import Failed
from livery.workshop import _steps as registry
from livery.workshop._phases import PhaseContext
from livery.workshop._steps import steps_by_name

ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture(autouse=True)
def registered() -> Iterator[None]:
    """The docs steps registered, as the mount registers the listed extension's."""
    from livery.workshop._extensions import declaration, register_declared

    state = registry.snapshot()
    found = declaration("docs")
    assert found is not None
    register_declared("docs", found.additions)
    try:
        yield
    finally:
        registry.restore(state)


def test_a_broken_link_and_a_dead_anchor_are_named(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "index.md").write_text(
        "# Home\n\n[gone](missing.md)\n[bad](other.md#nowhere)\n"
    )
    (docs / "other.md").write_text("# Other\n\n## A real heading\n")
    problems = link_problems(tmp_path)
    assert "docs/index.md: missing.md does not exist" in problems
    assert "docs/index.md: other.md#nowhere anchors nothing" in problems
    # The context names the step, as a run's does: the step asks it
    # which files it judges.
    ctx = PhaseContext(root=tmp_path, packages=(), step="lint.doclinks")
    with pytest.raises(Failed, match="links that resolve nothing"):
        doclinks_run(ctx)
    (docs / "index.md").write_text("# Home\n\n[good](other.md#a-real-heading)\n")
    assert link_problems(tmp_path) == []
    doclinks_run(ctx)


def test_a_mounted_link_maps_to_its_package_and_a_built_one_is_skipped(
    tmp_path: Path,
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    # A member at packages/core, and one in a group directory.
    for member in ("core", "extensions/widgets"):
        member_docs = tmp_path / "packages" / member / "docs"
        member_docs.mkdir(parents=True)
        name = member.rpartition("/")[2]
        (member_docs.parent / "workshop.toml").write_text(f'name = "acme-{name}"\n')
        (member_docs / "guide.md").write_text("# Guide\n")
    (docs / "index.md").write_text(
        "# Home\n\n[guide](packages/core/guide.md)\n"
        "[widgets](packages/extensions/widgets/guide.md#guide)\n"
        "[api](_generated/api/index.md)\n"
        "[gone](packages/core/absent.md)\n"
        "[group](packages/extensions/guide.md)\n"
    )
    # A page the build writes under the root docs is not authored.
    (docs / "tasks").mkdir()
    (docs / "tasks" / "index.md").write_text("[nothing](nowhere.md)\n")
    assert link_problems(tmp_path) == [
        "docs/index.md: packages/core/absent.md does not exist",
        "docs/index.md: packages/extensions/guide.md does not exist",
    ]


def test_the_steps_are_declared_under_the_lint_phase() -> None:
    registered = steps_by_name()
    assert registered["lint.doclinks"].extension == "docs"
    # The docstrings' steps are the API reference's.
    assert not {"lint.docrefs", "lint.docstrings"} & set(registered)


def test_this_workspaces_links_resolve() -> None:
    assert link_problems(ROOT) == []
