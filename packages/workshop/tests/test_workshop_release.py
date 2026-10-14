"""The release train and the update wave, on synthetic trees.

verify/prepare run against a scratch workspace with real git tags;
the snapshot publisher runs against a local bare artifact repository,
so the refusal and the idempotency are proven without a network.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from livery import git
from livery.footman import Failed
from livery.workshop._release import (
    prepare_release,
    verify_release,
)
from livery.workshop._update import bump_floors, latest_released
from workshop_seeds import (  # noqa: F401
    FakeNotes,
    Seeds,
    _seed_home,
    fake_notes,
    seed_copier,
)

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
            f'extensions = ["python"]\nname = "livery-{name}"\n{extra}'
        )
        (directory / "pyproject.toml").write_text(
            f'[project]\nname = "livery-{name}"\nversion = "0.2.0"\n'
            f"dependencies = [{deps}]\n"
        )
        (directory / "CHANGELOG.md").write_text("# Changelog\n\n## 0.2.0\n\n- x\n")
        (directory / "src" / "livery" / name / "__init__.py").write_text(
            '__version__ = "0.2.0"\n'
        )
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


def test_verify_lists_every_disagreement(seeds: Seeds, notes: FakeNotes) -> None:
    root = _workspace(seeds)
    changelog = root / "packages" / "tool" / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\n## 0.1.9\n\n- old\n")
    with pytest.raises(_FAILURES) as caught:
        verify_release(root, "packages/tool/v0.2.0")
    assert "CHANGELOG.md has no '## 0.2.0' entry" in str(caught.value)


def test_verify_reads_the_version_where_the_stamper_writes_it(seeds: Seeds) -> None:
    # The refusal first: a root module that does not declare the version.
    root = _workspace(seeds)
    package = root / "packages" / "tool" / "src" / "livery" / "tool"
    (package / "__init__.py").write_text('"""The tool."""\n')
    with pytest.raises(_FAILURES) as caught:
        verify_release(root, "packages/tool/v0.2.0")
    assert 'no __init__.py under src/ declares __version__ = "0.2.0"' in str(
        caught.value
    )
    # A root keeps its version in its __init__.py.
    (package / "__init__.py").write_text('__version__ = "0.2.0"\n')
    assert verify_release(root, "packages/tool/v0.2.0").version == "0.2.0"
    # A root with no public names has no module to carry it: the
    # manifest alone does.
    (package / "__init__.py").unlink()
    (package / "_extension.py").write_text("API_VERSION = 1\n")
    assert verify_release(root, "packages/tool/v0.2.0").version == "0.2.0"


def test_verify_refuses_an_unreleased_floor(seeds: Seeds) -> None:
    root = _workspace(seeds)
    contract = root / "packages" / "tool" / "workshop.toml"
    contract.write_text(contract.read_text().replace("0.1.0", "0.9.9"))
    with pytest.raises(_FAILURES) as caught:
        verify_release(root, "packages/tool/v0.2.0")
    assert "floors must name released versions" in str(caught.value)


def test_prepare_stamps_idempotently(seeds: Seeds, notes: FakeNotes) -> None:
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


def test_prepare_derives_the_bump_and_the_entry(seeds: Seeds, notes: FakeNotes) -> None:
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
    assert notes.recorded == [("0.3.0", "## [0.3.0]\n\n- what changed")]
    assert "\n\n## 0.2.0" in text  # the previous entry keeps its own block
    verify_release(root, "packages/tool/v0.3.0")
    # The tag closes the release; only then is a re-derivation a no-op.
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "chore: release 0.3.0 (#42)")
    _git(root, "tag", "packages/tool/v0.3.0")
    assert prepare_release(root, "packages/tool") == []


def test_floor_bumps_move_both_homes(seeds: Seeds) -> None:
    root = _workspace(seeds)
    _git(root, "tag", "packages/core/v0.2.0")
    assert latest_released(git.tags(root))["packages/core"] == "0.2.0"
    changed = bump_floors(root)
    assert changed == ["packages/tool: floor on packages/core 0.1.0 -> 0.2.0"]
    contract = (root / "packages" / "tool" / "workshop.toml").read_text()
    assert 'floor = "0.2.0"' in contract
    pyproject = (root / "packages" / "tool" / "pyproject.toml").read_text()
    assert "livery-core>=0.2.0" in pyproject
    assert bump_floors(root) == []  # already at the newest release


def test_a_scoped_floor_bump_moves_only_the_named_sibling(seeds: Seeds) -> None:
    root = _workspace(seeds)
    _git(root, "tag", "packages/core/v0.2.0")
    # A name outside the scope moves nothing, so a dependencies run
    # naming only externals cannot drag every floor along.
    assert bump_floors(root, only=("livery-other",)) == []
    contract = (root / "packages" / "tool" / "workshop.toml").read_text()
    assert 'floor = "0.1.0"' in contract
    changed = bump_floors(root, only=("livery-core",))
    assert changed == ["packages/tool: floor on packages/core 0.1.0 -> 0.2.0"]


def test_a_stamped_but_unreleased_version_still_releases(
    seeds: Seeds, monkeypatch: pytest.MonkeyPatch, notes: FakeNotes
) -> None:
    # The tag is the receipt: a pyproject stamped ahead of its release
    # must not read as released, or that release strands forever. The
    # fixture's packages carry version 0.2.0 with no tag at all, the
    # stranded shape exactly. The provider is handed the entry for the
    # version again, so it can regenerate the stranded one.
    root = _workspace(seeds)
    monkeypatch.setattr(
        "livery.workshop._release.derive_version", lambda root, package: "0.2.0"
    )
    notes.line = "- Added things."
    changed = prepare_release(root, "packages/core")
    assert changed, "the stamped-ahead release must proceed"
    assert notes.recorded == [("0.2.0", "## [0.2.0]\n\n- Added things.")]


def test_an_explicit_version_hands_the_provider_its_entry(
    seeds: Seeds, notes: FakeNotes
) -> None:
    # The driver hands prepare the derived version explicitly; the
    # provider must still be handed the entry on that path, so it can
    # regenerate a stranded heading, or the member's release commit is
    # empty and the train derails.
    root = _workspace(seeds)
    notes.line = "- Everything since."
    changed = prepare_release(root, "packages/core", "0.2.0")
    assert changed
    assert notes.recorded == [("0.2.0", "## [0.2.0]\n\n- Everything since.")]


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


def test_a_release_without_the_changelog_extension_writes_no_notes(
    seeds: Seeds, capsys: pytest.CaptureFixture[str]
) -> None:
    # The fallback first: a workspace listing no release notes extension
    # still releases; the train stamps the version and says why no
    # entry was written.
    from livery.workshop import _release_notes

    root = _workspace(seeds)
    assert _release_notes.release_notes(root) is None
    changelog = root / "packages" / "core" / "CHANGELOG.md"
    before = changelog.read_text() if changelog.is_file() else None
    changed = prepare_release(root, "packages/core", "0.3.0")
    assert changed and not any("CHANGELOG" in line for line in changed)
    assert (changelog.read_text() if changelog.is_file() else None) == before
    assert _release_notes.NO_PROVIDER in capsys.readouterr().out


def test_a_declared_provider_is_imported_when_the_train_first_asks_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    import workshop_seeds
    from livery.workshop import _extensions, _release_notes
    from livery.workshop._packages import Package
    from workshop_extension_fakes import fake_extensions, fake_steps, root_contract

    package = Package(
        directory=tmp_path,
        path="packages/thing",
        name="acme-thing",
        kind="python",
        depends=(),
        extensions=("python",),
    )
    # The fallback first: a workspace listing no extension that answers
    # has no provider.
    root_contract(tmp_path, "[]")
    assert _release_notes.release_notes(tmp_path) is None
    # A listed extension answers the query with a function in its own
    # package, imported when the train first asks, never at the read.
    fake_extensions(
        tmp_path,
        monkeypatch,
        notes='[extension]\nlevels = ["workspace"]\n'
        '[queries]\nrelease-notes = "{package}._steps:notes"\n',
    )
    fake_steps(
        tmp_path,
        "notes",
        "import workshop_seeds\n\n\ndef notes(root):\n"
        "    return workshop_seeds.DECLARED_NOTES\n",
    )
    root_contract(tmp_path, '["acme.notes"]')
    found = _extensions.declaration("acme.notes")
    assert found is not None and "release-notes" in found.queries
    assert "acme.notes._steps" not in sys.modules
    provider = FakeNotes("- Born.")
    monkeypatch.setattr(workshop_seeds, "DECLARED_NOTES", provider, raising=False)
    declared = _release_notes.release_notes(tmp_path)
    assert declared is not None and declared is provider
    assert "acme.notes._steps" in sys.modules
    entry = declared.entry(tmp_path, package, "1.0.0")
    assert entry == "## [1.0.0]\n\n- Born."
    assert declared.record(package, "1.0.0", entry) == ["CHANGELOG.md"]
    assert declared.verify(package, "1.0.0") == []
    assert provider.recorded == [("1.0.0", entry)]
