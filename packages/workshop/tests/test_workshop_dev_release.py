"""The dev act: refusals and degradations first, then the wheel."""

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

import pytest

from livery.footman import Failed
from livery.workshop._dev_release import (
    DevPlan,
    build_dev,
    describe_distance,
    dev_release,
    dev_version,
    public_version,
    sanitise_branch,
    semver_to_pep440,
)
from livery.workshop._packages import Package, discover_packages
from workshop_seeds import FakeNotes, fake_notes, member  # noqa: F401

_FAILURES = (SystemExit, Failed)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True)


def _workspace(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "--initial-branch=main")
    root = tmp_path / "ws"
    _git(tmp_path, "clone", str(origin), "ws")
    (root / "workshop.toml").write_text("[workspace]\n")
    member(root, "core")
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


def test_an_unchanged_package_refuses_with_the_pin_teaching(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _git(root, "checkout", "-b", "feat/9-widget")
    # HEAD moves, but nothing touches core: content, not position.
    (root / "elsewhere.txt").write_text("x\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "feat: elsewhere")
    packages = discover_packages(root)
    with pytest.raises(_FAILURES) as caught:
        dev_version(root, packages[0])
    message = str(caught.value)
    assert "sorts below" in message and "Pin the released 0.2.0" in message
    # The rule itself, for the loop to pin by: the release, or nothing
    # once the package has unreleased changes.
    from livery.workshop._dev_release import unchanged_since_release

    assert unchanged_since_release(root, packages[0]) == "0.2.0"
    marker = root / "packages" / "core" / "src" / "livery" / "core" / "grown.py"
    marker.write_text("x = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "feat: widget")
    assert unchanged_since_release(root, packages[0]) == ""


def test_a_stamped_ahead_pyproject_is_not_an_unchanged_refusal(tmp_path: Path) -> None:
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
    version = dev_version(root, discover_packages(root)[0], stamp="20260901")
    assert version.startswith("0.3.0-dev.feat.9-widget.")


def test_no_index_degrades_to_local_with_the_teaching(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    _grow_on_branch(root)
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
    dev_release(root, discover_packages(root))
    out = capsys.readouterr().out
    assert "--local" in out and "PYTHON_PUBLISH_INDEX" in out
    assert "PyPI is never the fallback" in out
    # The report opens by naming its act, so a dev build can never
    # read as a release.
    assert "act: dev, from branch" in out
    assert len(built) == 1


def test_headless_without_yes_refuses_teaching_the_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    _grow_on_branch(root)
    monkeypatch.setenv("PYTHON_PUBLISH_INDEX", "https://example.test/simple")
    # Off a terminal footman.confirm answers its default no.
    monkeypatch.setattr(
        "livery.workshop._dev_release.footman.confirm", lambda *a, **k: False
    )
    with pytest.raises(SystemExit) as caught:
        dev_release(root, discover_packages(root))
    assert "--yes" in str(caught.value)


def test_local_never_publishes_and_never_asks_even_with_an_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    _grow_on_branch(root)
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
    dev_release(root, discover_packages(root), local=True)
    assert len(built) == 1


def test_a_confirmed_publish_goes_to_the_configured_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    _grow_on_branch(root)
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
    dev_release(root, discover_packages(root))
    assert published == [("https://example.test/simple", "tok")]


def test_a_package_before_its_first_tag_still_describes(tmp_path: Path) -> None:
    # The fallback first: git describe refuses outright with no
    # matching tag, and a package's first release is exactly that
    # state. The whole history's count is the honest age.
    root = _workspace(tmp_path)
    packages = discover_packages(root)
    subprocess.run(
        ["git", "tag", "-d", f"{packages[0].path}/v0.2.0"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    distance, sha = describe_distance(root, packages[0])
    assert distance >= 1  # every commit in history counts
    assert sha


def test_the_version_grammar_and_its_pep440_form(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _grow_on_branch(root)
    packages = discover_packages(root)
    version = dev_version(root, packages[0], stamp="20260901")
    distance, sha = describe_distance(root, packages[0])
    assert distance == 1
    assert version == f"0.3.0-dev.feat.9-widget.1+g{sha}.20260901"
    assert semver_to_pep440(version) == f"0.3.0.dev1+feat.9-widget.g{sha}.20260901"
    # A dirty tree marks the wheel no commit describes.
    (root / "packages" / "core" / "workshop.toml").write_text(
        'extensions = ["python"]\nname = "livery-core"\n# dirt\n'
    )
    assert dev_version(root, packages[0], stamp="20260901").endswith(".dirty")


def test_sanitise_and_pep440_pass_throughs() -> None:
    assert sanitise_branch("feat/my_thing!") == "feat.mything"
    # ASCII only: PEP 440's local segment accepts nothing wider.
    assert sanitise_branch("feat/café-über") == "feat.caf-ber"
    assert semver_to_pep440("1.2.3") == "1.2.3"
    assert semver_to_pep440("1.2.3-dev.b.2") == "1.2.3.dev2+b"


def test_a_terminal_no_skips_the_member_and_continues(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    _grow_on_branch(root)
    monkeypatch.setenv("PYTHON_PUBLISH_INDEX", "https://example.test/simple")
    monkeypatch.setattr(
        "livery.workshop._dev_release.footman.confirm", lambda *a, **k: False
    )
    # A person was asked and said no: skip that member, never a refusal.
    monkeypatch.setattr("livery.workshop._dev_release.footman.attended", lambda: True)
    monkeypatch.setattr(
        "livery.workshop._dev_release.build_dev",
        lambda *a, **k: pytest.fail("a declined member must not build"),
    )
    dev_release(root, discover_packages(root))
    assert "skipped livery-core" in capsys.readouterr().out


def test_no_input_at_a_terminal_refuses_instead_of_skipping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `--no-input` makes confirm take its default no without asking
    # anyone, while the terminal is still real. Judged by the terminal
    # alone that read as a person declining, and the publish was
    # skipped in silence, which is what the refusal exists to prevent.
    root = _workspace(tmp_path)
    _grow_on_branch(root)
    monkeypatch.setenv("PYTHON_PUBLISH_INDEX", "https://example.test/simple")
    monkeypatch.setattr(
        "livery.workshop._dev_release.footman.confirm", lambda *a, **k: False
    )
    monkeypatch.setattr("livery.workshop._dev_release.footman.attended", lambda: False)
    # The terminal is real; only the flag makes the question unanswerable,
    # which is what judging by the terminal alone could not see.
    from types import SimpleNamespace

    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(
        "livery.workshop._dev_release.build_dev",
        lambda *a, **k: pytest.fail("silence must never build"),
    )
    with pytest.raises(SystemExit) as caught:
        dev_release(root, discover_packages(root))
    assert "--yes" in str(caught.value)


def test_a_failing_build_restores_the_dirty_tree_from_snapshots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    _grow_on_branch(root)
    member = root / "packages" / "core"
    # An uncommitted edit in a file the build stamps: a git-based
    # restore would discard it, the snapshot restore must not.
    pyproject = member / "pyproject.toml"
    dirty = pyproject.read_text() + "# uncommitted local edit\n"
    pyproject.write_text(dirty)
    version = dev_version(root, discover_packages(root)[0], stamp="20260901")
    assert version.endswith(".dirty")
    stamped_at = pyproject.stat().st_mtime_ns

    def _boom(*_args: object, **_kwargs: object) -> Path:
        raise Failed("uv build exploded")

    monkeypatch.setattr("livery.workshop._lifecycle.build_release", _boom)
    with pytest.raises(_FAILURES):
        build_dev(root, DevPlan(discover_packages(root)[0], version))
    assert pyproject.read_text() == dirty
    # The timestamp too: uv judges the editable install by it, and a
    # touched pyproject makes the next sync re-install the package.
    assert pyproject.stat().st_mtime_ns == stamped_at


def _set_of_two(tmp_path: Path) -> Path:
    """A workspace on a feature branch: core, and app with a floor on core.

    Both released at 0.2.0 and grown since, so each has a dev version;
    app's requirement and its contract name core at 0.1.0.
    """
    root = tmp_path / "ws"
    root.mkdir()
    _git(root, "init", "-q", "--initial-branch=main")
    (root / "workshop.toml").write_text("[workspace]\n")
    member(root, "core")
    member(root, "app", floor_on="core")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "chore: seed")
    _git(root, "tag", "packages/core/v0.2.0")
    _git(root, "tag", "packages/app/v0.2.0")
    _git(root, "checkout", "-b", "feat/9-widget")
    for name in ("core", "app"):
        grown = root / "packages" / name / "src" / "livery" / name / "grown.py"
        grown.write_text("x = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "feat: widget")
    return root


def _files_under(directory: Path) -> dict[Path, tuple[bytes, int]]:
    """Every file under *directory* but a build's output: its bytes and mtime."""
    return {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(directory.rglob("*"))
        if path.is_file() and "dist" not in path.parts
    }


def test_a_build_failing_mid_set_restores_every_file_the_set_touched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _set_of_two(tmp_path)
    before = _files_under(root / "packages")
    app = root / "packages" / "app"
    seen: list[str] = []

    def _build(package: Package, _root: Path) -> Path:
        # app builds first, with core's floor raised; core's build fails.
        if package.member == "core":
            raise Failed("uv build exploded")
        seen.append((app / "pyproject.toml").read_text())
        seen.append((app / "workshop.toml").read_text())
        dist = package.directory / "dist"
        dist.mkdir()
        return dist

    monkeypatch.setattr("livery.workshop._lifecycle.build_release", _build)
    with pytest.raises(_FAILURES, match="uv build exploded"):
        dev_release(root, discover_packages(root), local=True)
    requirement, contract = seen
    assert '"livery-core>=0.3.0.dev1"' in requirement
    assert 'floor = "0.3.0.dev1"' in contract
    # Every file the raise and the stamps touched is back, byte for
    # byte, with its timestamp.
    assert _files_under(root / "packages") == before


def test_a_member_built_alone_moves_no_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _set_of_two(tmp_path)
    app = root / "packages" / "app"
    seen: list[str] = []

    def _build(package: Package, _root: Path) -> Path:
        seen.append((app / "pyproject.toml").read_text())
        return package.directory / "dist"

    monkeypatch.setattr("livery.workshop._lifecycle.build_release", _build)
    alone = tuple(p for p in discover_packages(root) if p.member == "app")
    dev_release(root, alone, local=True)
    assert len(seen) == 1 and '"livery-core>=0.1.0"' in seen[0]
    assert "floors raised" not in capsys.readouterr().out


def test_the_public_version_drops_the_local_segment() -> None:
    assert public_version("0.0.0-dev.refactor.1727-x.916+gc073261f.20261016") == (
        "0.0.0.dev916"
    )
    assert public_version("1.2.3") == "1.2.3"


#: A python member's pyproject the real build reads: uv_build, the
#: package under its namespace, and the dependencies given.
_BUILT = (
    '[project]\nname = "livery-{name}"\nversion = "{version}"\n'
    'requires-python = ">=3.11"\ndependencies = [{dependencies}]\n'
    "[build-system]\n"
    'requires = ["uv_build>=0.7"]\nbuild-backend = "uv_build"\n'
    "[tool.uv.build-backend]\n"
    'module-name = "livery.{name}"\nnamespace = true\n'
)


def test_a_set_s_requirer_wheel_admits_its_co_member_s_dev_wheel(
    tmp_path: Path,
) -> None:
    import livery.toolroom.tools as tools

    # core was never released: its dev version, 0.0.0.devN, sorts below
    # the 0.0.0 app's floor names, so only a raised floor admits it.
    root = tmp_path / "ws"
    root.mkdir()
    _git(root, "init", "-q", "--initial-branch=main")
    (root / "workshop.toml").write_text("[workspace]\n")
    member(root, "core")
    member(root, "app")
    core, app = root / "packages" / "core", root / "packages" / "app"
    (core / "pyproject.toml").write_text(
        _BUILT.format(name="core", version="0.0.0", dependencies="")
    )
    (core / "src" / "livery" / "core" / "__init__.py").write_text(
        '__version__ = "0.0.0"\n'
    )
    (app / "pyproject.toml").write_text(
        _BUILT.format(name="app", version="0.0.0", dependencies='"livery-core>=0.0.0"')
    )
    (app / "src" / "livery" / "app" / "__init__.py").write_text(
        '__version__ = "0.0.0"\n'
    )
    (app / "workshop.toml").write_text(
        'extensions = ["python"]\nname = "livery-app"\n'
        '[[depends]]\npath = "packages/core"\nkind = "runtime"\nfloor = "0.0.0"\n'
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "feat: seed")
    _git(root, "checkout", "-b", "feat/9-widget")
    before = _files_under(root / "packages")
    dev_release(root, discover_packages(root), local=True)
    assert _files_under(root / "packages") == before

    def _metadata(directory: Path) -> list[str]:
        wheel = next((directory / "dist").glob("*.whl"))
        with zipfile.ZipFile(wheel) as archive:
            name = next(n for n in archive.namelist() if n.endswith("METADATA"))
            return archive.read(name).decode().splitlines()

    (core_version,) = [
        line.removeprefix("Version: ")
        for line in _metadata(core)
        if line.startswith("Version: ")
    ]
    public = core_version.partition("+")[0]
    assert public.startswith("0.0.0.dev")
    assert [line for line in _metadata(app) if "livery-core" in line] == [
        f"Requires-Dist: livery-core>={public}"
    ]
    # The resolver takes core's dev wheel for app's requirement.
    (tmp_path / "requirements.in").write_text("livery-app\n")
    compiled = tools.uv.opts(cwd=tmp_path, nofail=True, recorded=False)(
        "pip",
        "compile",
        "--no-index",
        "--find-links",
        str(core / "dist"),
        "--find-links",
        str(app / "dist"),
        "requirements.in",
    )
    assert compiled.code == 0, compiled.stderr
    assert f"livery-core=={core_version}" in compiled.stdout


def test_a_roots_init_is_stamped_and_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    _grow_on_branch(root)
    # A root keeps its version in its __init__.py.
    init = root / "packages" / "core" / "src" / "livery" / "core" / "__init__.py"
    version = dev_version(root, discover_packages(root)[0], stamp="20260901")
    seen: list[str] = []

    def _boom(*_args: object, **_kwargs: object) -> Path:
        seen.append(init.read_text())
        raise Failed("uv build exploded")

    monkeypatch.setattr("livery.workshop._lifecycle.build_release", _boom)
    with pytest.raises(_FAILURES):
        build_dev(root, DevPlan(discover_packages(root)[0], version))
    assert seen and seen[0] != '__version__ = "0.2.0"\n'
    assert init.read_text() == '__version__ = "0.2.0"\n'


def test_the_branch_routes_the_act(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._release_driver import workflow_release

    root = _workspace(tmp_path)
    monkeypatch.setattr(
        "livery.workshop._extensions.workspace_root", lambda start=None: root
    )
    routed: list[str] = []
    monkeypatch.setattr(
        "livery.workshop._release_driver.local_release",
        lambda _root, _members: routed.append("train-local"),
    )

    def _dev(_root: Path, _members: object, *, local: bool = False) -> None:
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
    _git(root, "checkout", "-b", "workflow/update/dependencies")
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
    tmp_path: Path, notes: FakeNotes
) -> None:
    # The excerpt is the release notes' unreleased entry, which the
    # stand-in provider answers as a listed extension would.
    notes.line = "- Widget"
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
    packages = discover_packages(root)
    version = dev_version(root, packages[0], stamp="20260901")
    before = {
        p: p.read_bytes()
        for p in sorted(member.rglob("*"))
        if p.is_file() and "dist" not in p.parts
    }
    stamps = {p: p.stat().st_mtime_ns for p in before}
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
    assert {p: p.stat().st_mtime_ns for p in before} == stamps  # and unmoved
