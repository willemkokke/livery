"""The dev act: refusals and degradations first, then the wheel."""

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

import pytest
from footman import Failed

from livery.workshop._dev_release import (
    DevPlan,
    build_dev,
    describe_distance,
    dev_release,
    dev_version,
    sanitise_branch,
    semver_to_pep440,
)
from livery.workshop._git_ops import GitOps
from livery.workshop._packages import discover_packages
from test_workshop_release_driver import _member

_FAILURES = (SystemExit, Failed)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True)


def _workspace(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "--initial-branch=main")
    root = tmp_path / "ws"
    _git(tmp_path, "clone", str(origin), "ws")
    _git(root, "config", "user.email", "t@livery.local")
    _git(root, "config", "user.name", "T")
    (root / "livery.toml").write_text("[workspace]\n")
    _member(root, "core")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "chore: seed")
    _git(root, "tag", "packages/core/v0.2.0")
    _git(root, "push", "-u", "origin", "main")
    return root


def _grow_on_branch(root: Path) -> None:
    _git(root, "checkout", "-b", "feat/9-widget")
    marker = root / "packages" / "core" / "src" / "livery" / "core" / "grown.py"
    marker.write_text("x = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "feat: widget")


def test_an_unchanged_package_refuses_with_the_pin_teaching(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    _git(root, "checkout", "-b", "feat/9-widget")
    # HEAD moves, but nothing touches core: content, not position.
    (root / "elsewhere.txt").write_text("x\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "feat: elsewhere")
    git = GitOps(root)
    packages = discover_packages(root)
    with pytest.raises(_FAILURES) as caught:
        dev_version(root, git, packages[0])
    message = str(caught.value)
    assert "sorts below" in message and "Pin the released 0.2.0" in message


def test_a_stamped_ahead_pyproject_is_not_an_unchanged_refusal(
    tmp_path: Path,
) -> None:
    # The tag is the record of what was released, not the stamped
    # file: a prepared bump in pyproject whose tag is not cut yet
    # must not read as "nothing unreleased".
    root = _workspace(tmp_path)
    member = root / "packages" / "core"
    stamped = (member / "pyproject.toml").read_text().replace("0.2.0", "0.3.0")
    (member / "pyproject.toml").write_text(stamped)
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "feat: grow past the stamp")
    _git(root, "checkout", "-b", "feat/9-widget")
    git = GitOps(root)
    version = dev_version(root, git, discover_packages(root)[0], stamp="20260901")
    assert version.startswith("0.3.0-dev.feat.9-widget.")


def test_no_index_degrades_to_local_with_the_teaching(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(tmp_path)
    _grow_on_branch(root)
    git = GitOps(root)
    monkeypatch.delenv("PYTHON_PUBLISH_INDEX", raising=False)
    built: list[str] = []

    def _fake_build(_root: Path, plan: DevPlan) -> Path:
        built.append(plan.version)
        return root / "d"

    monkeypatch.setattr("livery.workshop._dev_release.build_dev", _fake_build)
    monkeypatch.setattr(
        "livery.workshop._dev_release.publish_wheels",
        lambda *a, **k: pytest.fail("nothing may publish without an index"),
    )
    monkeypatch.setattr(
        "livery.workshop._dev_release.footman.confirm",
        lambda *a, **k: pytest.fail("a local run never asks"),
    )
    dev_release(root, git, discover_packages(root))
    out = capsys.readouterr().out
    assert "--local" in out and "PYTHON_PUBLISH_INDEX" in out
    assert "PyPI is never the fallback" in out
    assert len(built) == 1


def test_headless_without_yes_refuses_teaching_the_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    _grow_on_branch(root)
    git = GitOps(root)
    monkeypatch.setenv("PYTHON_PUBLISH_INDEX", "https://example.test/simple")
    # Off a terminal footman.confirm answers its default no.
    monkeypatch.setattr(
        "livery.workshop._dev_release.footman.confirm", lambda *a, **k: False
    )
    with pytest.raises(SystemExit) as caught:
        dev_release(root, git, discover_packages(root))
    assert "--yes" in str(caught.value)


def test_local_never_publishes_and_never_asks_even_with_an_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    _grow_on_branch(root)
    git = GitOps(root)
    monkeypatch.setenv("PYTHON_PUBLISH_INDEX", "https://example.test/simple")
    monkeypatch.setattr(
        "livery.workshop._dev_release.publish_wheels",
        lambda *a, **k: pytest.fail("--local publishes nothing"),
    )
    monkeypatch.setattr(
        "livery.workshop._dev_release.footman.confirm",
        lambda *a, **k: pytest.fail("--local never asks"),
    )
    built: list[str] = []

    def _fake_build(_root: Path, plan: DevPlan) -> Path:
        built.append(plan.version)
        return root / "d"

    monkeypatch.setattr("livery.workshop._dev_release.build_dev", _fake_build)
    dev_release(root, git, discover_packages(root), local=True)
    assert len(built) == 1


def test_a_confirmed_publish_goes_to_the_configured_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    _grow_on_branch(root)
    git = GitOps(root)
    monkeypatch.setenv("PYTHON_PUBLISH_INDEX", "https://example.test/simple")
    monkeypatch.setenv("UV_PUBLISH_TOKEN", "tok")
    monkeypatch.setattr(
        "livery.workshop._dev_release.footman.confirm", lambda *a, **k: True
    )
    monkeypatch.setattr(
        "livery.workshop._dev_release.build_dev",
        lambda _root, plan: root / "packages" / "core" / "dist",
    )
    published: list[tuple[str, str]] = []

    def _fake_publish(package: object, *, index_url: str, token: str) -> bool:
        published.append((index_url, token))
        return True

    monkeypatch.setattr("livery.workshop._dev_release.publish_wheels", _fake_publish)
    dev_release(root, git, discover_packages(root))
    assert published == [("https://example.test/simple", "tok")]


def test_the_version_grammar_and_its_pep440_form(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _grow_on_branch(root)
    git = GitOps(root)
    packages = discover_packages(root)
    version = dev_version(root, git, packages[0], stamp="20260901")
    distance, sha = describe_distance(git, packages[0])
    assert distance == 1
    assert version == f"0.3.0-dev.feat.9-widget.1+{sha}.20260901"
    assert semver_to_pep440(version) == f"0.3.0.dev1+feat.9-widget.{sha}.20260901"
    # A dirty tree marks the wheel no commit describes.
    (root / "packages" / "core" / "livery.toml").write_text(
        'type = "python"\nname = "livery-core"\n# dirt\n'
    )
    assert dev_version(root, git, packages[0], stamp="20260901").endswith(".dirty")


def test_sanitise_and_pep440_pass_throughs() -> None:
    assert sanitise_branch("feat/my_thing!") == "feat.mything"
    # ASCII only: PEP 440's local segment accepts nothing wider.
    assert sanitise_branch("feat/café-über") == "feat.caf-ber"
    assert semver_to_pep440("1.2.3") == "1.2.3"
    assert semver_to_pep440("1.2.3-dev.b.2") == "1.2.3.dev2+b"


def test_a_terminal_no_skips_the_member_and_continues(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    root = _workspace(tmp_path)
    _grow_on_branch(root)
    git = GitOps(root)
    monkeypatch.setenv("PYTHON_PUBLISH_INDEX", "https://example.test/simple")
    monkeypatch.setattr(
        "livery.workshop._dev_release.footman.confirm", lambda *a, **k: False
    )
    # A real terminal said no: skip that member, never a refusal.
    monkeypatch.setattr(
        "livery.workshop._dev_release.sys",
        SimpleNamespace(stdin=SimpleNamespace(isatty=lambda: True)),
    )
    monkeypatch.setattr(
        "livery.workshop._dev_release.build_dev",
        lambda *a, **k: pytest.fail("a declined member must not build"),
    )
    dev_release(root, git, discover_packages(root))
    assert "skipped livery-core" in capsys.readouterr().out


def test_a_failing_build_restores_the_dirty_tree_from_snapshots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    _grow_on_branch(root)
    git = GitOps(root)
    member = root / "packages" / "core"
    # An uncommitted edit in a file the build stamps: a git-based
    # restore would discard it, the snapshot restore must not.
    pyproject = member / "pyproject.toml"
    dirty = pyproject.read_text() + "# uncommitted local edit\n"
    pyproject.write_text(dirty)
    version = dev_version(root, git, discover_packages(root)[0], stamp="20260901")
    assert version.endswith(".dirty")

    def _boom(*_args: object, **_kwargs: object) -> Path:
        raise Failed("uv build exploded")

    monkeypatch.setattr("livery.workshop._dev_release._python.build", _boom)
    with pytest.raises(_FAILURES):
        build_dev(root, DevPlan(discover_packages(root)[0], version))
    assert pyproject.read_text() == dirty


def test_the_branch_routes_the_act(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._release_driver import workflow_release

    root = _workspace(tmp_path)
    monkeypatch.setattr("livery.workshop._layers.workspace_root", lambda: root)
    routed: list[str] = []
    monkeypatch.setattr(
        "livery.workshop._release_driver.local_release",
        lambda _root, _members: routed.append("train-local"),
    )

    def _dev(
        _root: Path, _git: GitOps, _members: object, *, local: bool = False
    ) -> None:
        routed.append("dev")

    monkeypatch.setattr("livery.workshop._dev_release.dev_release", _dev)
    # A feature branch is the dev act.
    _git(root, "checkout", "-b", "feat/9-widget")
    workflow_release("core", local=True)
    assert routed == ["dev"]
    # main and the engine's workflow/ namespace run the train.
    _git(root, "checkout", "main")
    workflow_release("core", local=True)
    assert routed == ["dev", "train-local"]
    _git(root, "checkout", "-b", "workflow/update/templates")
    workflow_release("core", local=True)
    assert routed == ["dev", "train-local", "train-local"]
    # Detached HEAD is a taught stop: the branch decides the act.
    _git(root, "checkout", "--detach")
    with pytest.raises(_FAILURES) as caught:
        workflow_release("core", local=True)
    assert "detached" in str(caught.value)


def test_the_release_task_owns_the_terminal() -> None:
    # The dev confirm runs mid-body, and footman refuses a mid-body
    # prompt in a non-interactive task: dropping the stamp would pass
    # every mocked test and break the real confirm at runtime.
    from livery.workshop._release_driver import workflow_release

    assert getattr(workflow_release, "_footman_interactive", False) is True


def test_the_real_build_splices_the_readme_and_restores_the_tree(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    member = root / "packages" / "core"
    (member / "pyproject.toml").write_text(
        '[project]\nname = "livery-core"\nversion = "0.2.0"\n'
        'requires-python = ">=3.11"\ndependencies = []\n'
        'readme = "README.md"\n'
        "[build-system]\n"
        'requires = ["uv_build>=0.7"]\nbuild-backend = "uv_build"\n'
        "[tool.uv.build-backend]\n"
        'module-name = "livery.core"\nnamespace = true\n'
    )
    (member / "README.md").write_text("# livery-core\n\nThe core.\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "chore: readme")
    _grow_on_branch(root)
    git = GitOps(root)
    packages = discover_packages(root)
    version = dev_version(root, git, packages[0], stamp="20260901")
    before = {
        p: p.read_bytes()
        for p in sorted(member.rglob("*"))
        if p.is_file() and "dist" not in p.parts
    }
    dist = build_dev(root, DevPlan(packages[0], version))
    wheel = next(iter(dist.glob("*.whl")))
    # Two commits since the tag (the readme, the growth); uv
    # normalises the local segment's hyphen to a dot in the filename.
    assert ".dev2+feat.9.widget." in wheel.name
    with zipfile.ZipFile(wheel) as archive:
        metadata = next(n for n in archive.namelist() if n.endswith("METADATA"))
        text = archive.read(metadata).decode()
    assert "What's New" in text and "Widget" in text
    after = {
        p: p.read_bytes()
        for p in sorted(member.rglob("*"))
        if p.is_file() and "dist" not in p.parts
    }
    assert before == after  # byte-identical tree, the excerpt only in the wheel
