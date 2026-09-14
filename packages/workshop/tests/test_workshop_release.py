"""The release train and the update wave, on synthetic trees.

verify/prepare run against a scratch workspace with real git tags;
the snapshot publisher runs against a local bare artifact repository,
so the refusal and the idempotency are proven without a network.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from livery.footman import Failed
from livery.workshop._git_ops import GitOps
from livery.workshop._release import (
    prepare_release,
    publish_templates,
    verify_release,
)
from livery.workshop._update import bump_floors, latest_released
from workshop_seeds import Seeds, _seed_home, cliff_config, seed_copier  # noqa: F401

_FAILURES = (SystemExit, Failed)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def _build(base: Path) -> None:
    # No origin and no push: these tests read a workspace, and a remote
    # the real one would not have is a difference they would carry.
    root = base / "ws"
    root.mkdir()
    (root / "workshop.toml").write_text("[workspace]\n")
    _git(root, "init", "--initial-branch=main")
    _git(root, "config", "user.email", "test@livery.local")
    _git(root, "config", "user.name", "Livery Test")
    for name, extra, deps in (
        ("core", "", ""),
        (
            "tool",
            '[[depends]]\npath = "packages/core"\nkind = "build"\nfloor = "0.1.0"\n',
            '"livery-core>=0.1.0"',
        ),
    ):
        directory = root / "packages" / name
        (directory / "src" / "livery" / name).mkdir(parents=True)
        (directory / "workshop.toml").write_text(
            f'type = "python"\nname = "livery-{name}"\n{extra}'
        )
        (directory / "pyproject.toml").write_text(
            f'[project]\nname = "livery-{name}"\nversion = "0.2.0"\n'
            f"dependencies = [{deps}]\n"
        )
        (directory / "CHANGELOG.md").write_text("# Changelog\n\n## 0.2.0\n\n- x\n")
        (directory / "src" / "livery" / name / "__init__.py").write_text(
            '__version__ = "0.2.0"\n'
        )
        (directory / "cliff.toml").write_text(cliff_config(name))
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "chore: seed")
    _git(root, "tag", "packages/core/v0.1.0")


def _workspace(seeds: Seeds) -> Path:
    """The two-package workspace at 0.2.0, core tagged, copied for this test."""
    return seeds("release", _build) / "ws"


def test_verify_passes_a_release_shaped_tree(seeds: Seeds) -> None:
    root = _workspace(seeds)
    plan = verify_release(root, "packages/tool/v0.2.0")
    assert plan.package.name == "livery-tool" and plan.version == "0.2.0"


def test_verify_lists_every_disagreement(seeds: Seeds) -> None:
    root = _workspace(seeds)
    changelog = root / "packages" / "tool" / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\n## 0.1.9\n\n- old\n")
    with pytest.raises(_FAILURES) as caught:
        verify_release(root, "packages/tool/v0.2.0")
    assert "CHANGELOG.md has no '## 0.2.0' entry" in str(caught.value)


def test_verify_refuses_an_unreleased_floor(seeds: Seeds) -> None:
    root = _workspace(seeds)
    contract = root / "packages" / "tool" / "workshop.toml"
    contract.write_text(contract.read_text().replace("0.1.0", "0.9.9"))
    with pytest.raises(_FAILURES) as caught:
        verify_release(root, "packages/tool/v0.2.0")
    assert "floors must name released versions" in str(caught.value)


def test_prepare_stamps_idempotently(seeds: Seeds) -> None:
    root = _workspace(seeds)
    changed = prepare_release(root, "packages/tool", "0.3.0")
    assert "pyproject.toml" in changed
    assert any("CHANGELOG" in name for name in changed)
    assert prepare_release(root, "packages/tool", "0.3.0") == []
    text = (root / "packages" / "tool" / "CHANGELOG.md").read_text()
    assert text.index("## [0.3.0]") < text.index("## 0.2.0")


def test_prepare_refuses_a_package_with_nothing_unreleased(seeds: Seeds) -> None:
    # The refusal first: a package whose tag already covers every
    # commit must not mint a version, or the index gets the same code
    # twice under different numbers.
    root = _workspace(seeds)
    _git(root, "tag", "packages/tool/v0.2.0")
    assert prepare_release(root, "packages/tool") == []


def test_prepare_names_the_missing_changelog_contract(seeds: Seeds) -> None:
    root = _workspace(seeds)
    (root / "packages" / "tool" / "cliff.toml").unlink()
    with pytest.raises(_FAILURES) as caught:
        prepare_release(root, "packages/tool")
    assert "cliff.toml" in str(caught.value)


def test_prepare_derives_the_bump_and_the_entry(seeds: Seeds, tmp_path: Path) -> None:
    root = _workspace(seeds)
    _git(root, "tag", "packages/tool/v0.2.0")
    new_file = root / "packages" / "tool" / "src" / "livery" / "tool" / "extra.py"
    new_file.write_text("x = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "feat: the tool grows a verb (#41)")
    changed = prepare_release(root, "packages/tool")
    assert "pyproject.toml" in changed
    import tomllib

    version = tomllib.loads(
        (root / "packages" / "tool" / "pyproject.toml").read_text()
    )["project"]["version"]
    assert version == "0.3.0"  # feat bumps minor pre-1.0, footman's practice
    text = (root / "packages" / "tool" / "CHANGELOG.md").read_text()
    assert "## [0.3.0]" in text
    assert "### Added" in text
    assert "The tool grows a verb (#41)" in text
    assert "\n\n## 0.2.0" in text  # the previous entry keeps its own block
    verify_release(root, "packages/tool/v0.3.0")
    # The tag closes the release; only then is a re-derivation a no-op.
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "chore: release 0.3.0 (#42)")
    _git(root, "tag", "packages/tool/v0.3.0")
    assert prepare_release(root, "packages/tool") == []


def _artifact_remote(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "artifact.git"
    remote.mkdir()
    _git(remote, "init", "--bare", "--initial-branch=main")
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "copier.yml").write_text("kind:\n  type: str\n")
    (templates / "project").mkdir()
    (templates / "project" / "tasks.py").write_text("plugin\n")
    return remote, templates


def test_the_snapshot_publishes_and_is_idempotent(tmp_path: Path) -> None:
    remote, templates = _artifact_remote(tmp_path)
    first = publish_templates(templates, "0.0.2", str(remote), author="T <t@l>")
    assert first == "published v0.0.2"
    again = publish_templates(templates, "0.0.2", str(remote), author="T <t@l>")
    assert again == "v0.0.2 already published with this content"
    # The acceptance diff: the artifact tree at the tag is templates/.
    check = tmp_path / "check"
    _git(tmp_path, "clone", str(remote), "check")
    _git(check, "checkout", "v0.0.2")
    shutil.rmtree(check / ".git")  # --no-index would walk the pack files
    diff = subprocess.run(
        ["git", "diff", "--no-index", str(templates), str(check)],
        capture_output=True,
        text=True,
    )
    assert diff.returncode == 0 and diff.stdout == ""


def test_the_same_version_with_different_content_refuses(tmp_path: Path) -> None:
    remote, templates = _artifact_remote(tmp_path)
    publish_templates(templates, "0.0.2", str(remote), author="T <t@l>")
    (templates / "copier.yml").write_text("kind:\n  type: str\n  default: x\n")
    with pytest.raises(_FAILURES) as caught:
        publish_templates(templates, "0.0.2", str(remote), author="T <t@l>")
    assert "immutable" in str(caught.value)
    assert (
        publish_templates(templates, "0.0.3", str(remote), author="T <t@l>")
        == "published v0.0.3"
    )


def test_floor_bumps_move_both_homes(seeds: Seeds) -> None:
    root = _workspace(seeds)
    _git(root, "tag", "packages/core/v0.2.0")
    git = GitOps(root)
    assert latest_released(git.tags())["packages/core"] == "0.2.0"
    changed = bump_floors(root, git)
    assert changed == ["packages/tool: floor on packages/core 0.1.0 -> 0.2.0"]
    contract = (root / "packages" / "tool" / "workshop.toml").read_text()
    assert 'floor = "0.2.0"' in contract
    pyproject = (root / "packages" / "tool" / "pyproject.toml").read_text()
    assert "livery-core>=0.2.0" in pyproject
    assert bump_floors(root, git) == []  # already at the newest release


def test_a_scoped_floor_bump_moves_only_the_named_sibling(seeds: Seeds) -> None:
    root = _workspace(seeds)
    _git(root, "tag", "packages/core/v0.2.0")
    git = GitOps(root)
    # A name outside the scope moves nothing, so a dependencies run
    # naming only externals cannot drag every floor along.
    assert bump_floors(root, git, only=("livery-other",)) == []
    contract = (root / "packages" / "tool" / "workshop.toml").read_text()
    assert 'floor = "0.1.0"' in contract
    changed = bump_floors(root, git, only=("livery-core",))
    assert changed == ["packages/tool: floor on packages/core 0.1.0 -> 0.2.0"]


def test_a_stamped_but_unreleased_version_still_releases(
    seeds: Seeds, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The tag is the receipt: a pyproject stamped ahead of its release
    # must not read as released, or that release strands forever. The
    # fixture's packages carry version 0.2.0 with no tag at all, the
    # stranded shape exactly.
    root = _workspace(seeds)
    monkeypatch.setattr(
        "livery.workshop._cliff.bumped_version", lambda root, package: "0.2.0"
    )
    monkeypatch.setattr(
        "livery.workshop._cliff.unreleased_entry",
        lambda root, package, version="": "## [0.2.0]\n\n- Added things.",
    )
    changed = prepare_release(root, "packages/core")
    assert changed, "the stamped-ahead release must proceed"
    text = (root / "packages" / "core" / "CHANGELOG.md").read_text()
    # The stranded entry regenerated to cover everything the receipt
    # will actually name.
    assert "- Added things." in text


def test_an_explicit_version_regenerates_a_stranded_entry(
    seeds: Seeds, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The driver hands prepare the derived version explicitly; a
    # stranded heading must still regenerate on that path, or the
    # member's release commit is empty and the train derails.
    root = _workspace(seeds)
    monkeypatch.setattr(
        "livery.workshop._cliff.unreleased_entry",
        lambda root, package, version="": "## [0.2.0]\n\n- Everything since.",
    )
    changed = prepare_release(root, "packages/core", "0.2.0")
    assert changed
    text = (root / "packages" / "core" / "CHANGELOG.md").read_text()
    assert "- Everything since." in text


def _lockable(root: Path) -> None:
    """Give the fixture workspace a root project and a real lock."""
    (root / "pyproject.toml").write_text(
        '[project]\nname = "ws"\nversion = "0.0.0"\nrequires-python = ">=3.11"\n'
        '\n[tool.uv.workspace]\nmembers = ["packages/*"]\n'
        "\n[tool.uv.sources]\n"
        "livery-core = { workspace = true }\n"
        "livery-tool = { workspace = true }\n"
    )
    subprocess.run(["uv", "lock"], cwd=root, capture_output=True, text=True, check=True)
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "chore: lock")


def test_prepare_refreshes_the_lock_with_the_stamp(seeds: Seeds) -> None:
    # Finding 10: a stamp without a lock refresh leaves uv.lock
    # claiming the old version, and the first sync after the release
    # dirties the tree, which the train's own re-run then refuses.
    root = _workspace(seeds)
    _lockable(root)
    changed = prepare_release(root, "packages/core", "0.3.0")
    assert "uv.lock" in changed
    assert 'version = "0.3.0"' in (root / "uv.lock").read_text()
    # Idempotent: a second prepare changes nothing, the lock included.
    assert "uv.lock" not in prepare_release(root, "packages/core", "0.3.0")


def test_prepare_leaves_a_lockless_workspace_alone(seeds: Seeds) -> None:
    root = _workspace(seeds)
    changed = prepare_release(root, "packages/core", "0.3.0")
    assert not (root / "uv.lock").exists()
    assert "uv.lock" not in changed
