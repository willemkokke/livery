"""A change to a root file reaches the packages it is about, not every package."""

from __future__ import annotations

import subprocess
from pathlib import Path

from livery.workshop._git_ops import GitOps
from livery.workshop._graph import affected_from_paths
from livery.workshop._packages import discover_packages
from livery.workshop._root_attribution import (
    Delta,
    explained,
    lock_affected,
    package_delta,
)


def _delta(*added: tuple[str, str]) -> Delta:
    """A delta naming packages by path and name, as `explained` reads them."""
    return Delta((), tuple(added))


def test_an_unrelated_root_change_is_not_explained() -> None:
    before = 'members = ["packages/a"]\nline-length = 88\n'
    now = 'members = ["packages/a", "packages/b"]\nline-length = 100\n'
    assert not explained(before, now, _delta(("packages/b", "acme-b")))


def test_without_a_delta_nothing_is_explained() -> None:
    assert not explained("x = 1\n", "x = 1\n", Delta((), ()))


def test_a_longer_name_is_not_the_package() -> None:
    before = 'dev = [\n    "acme-b-extra",\n]\n'
    now = 'dev = [\n    "acme-b-extra2",\n]\n'
    assert not explained(before, now, _delta(("packages/b", "acme-b")))


def test_entries_naming_the_added_package_are_explained() -> None:
    before = (
        'members = ["packages/a"]\n'
        "[tool.uv.sources]\nacme-a = { workspace = true }\n"
        'files = [\n    "packages/a/src",\n    "tasks.py",\n]\n'
        'dev = [\n    "acme-a[test]",\n]\n'
    )
    now = (
        'members = ["packages/a", "packages/b"]\n'
        "[tool.uv.sources]\nacme-a = { workspace = true }\n"
        "acme-b = { workspace = true }\n"
        'files = [\n    "packages/a/src",\n    "packages/b/src",\n    "tasks.py",\n]\n'
        'dev = [\n    "acme-a[test]",\n    "acme-b",\n]\n'
    )
    assert explained(before, now, _delta(("packages/b", "acme-b")))


def test_an_ownership_line_is_explained() -> None:
    before = "/workshop.toml @owner\n"
    now = "/workshop.toml @owner\n/packages/b/ @team\n"
    assert explained(before, now, _delta(("packages/b", "acme-b")))


def _entry(name: str, version: str, source: str, *deps: str) -> str:
    listed = ", ".join(f'{{ name = "{dep}" }}' for dep in deps)
    return (
        f'[[package]]\nname = "{name}"\nversion = "{version}"\nsource = {source}\n'
        f"dependencies = [{listed}]\n\n"
    )


PYPI = '{ registry = "https://pypi.org/simple" }'


def _lockfile(*entries: str, members: str = '"acme-a", "acme-b"') -> str:
    return (
        'version = 1\nrequires-python = ">=3.11"\n\n'
        f"[manifest]\nmembers = [{members}]\n\n" + "".join(entries)
    )


def _root(*deps: str) -> str:
    listed = ", ".join(f'{{ name = "{dep}" }}' for dep in deps)
    return (
        '[[package]]\nname = "acme"\nversion = "0.0.1"\nsource = { virtual = "." }\n\n'
        f"[package.dev-dependencies]\ndev = [{listed}]\n\n"
    )


BASE = (
    _root("acme-a", "acme-b", "pytest"),
    _entry("acme-a", "0.1.0", '{ editable = "packages/a" }', "six"),
    _entry("acme-b", "0.1.0", '{ editable = "packages/b" }', "six"),
    _entry("six", "1.16.0", PYPI),
    _entry("pytest", "8.0.0", PYPI),
)


def test_an_unparseable_lock_affects_everything() -> None:
    assert lock_affected("not toml [", _lockfile(*BASE), Delta((), ())) is None


def test_a_resolution_setting_change_affects_everything() -> None:
    before = _lockfile(*BASE)
    now = before.replace('requires-python = ">=3.11"', 'requires-python = ">=3.12"')
    assert lock_affected(before, now, Delta((), ())) is None


def test_a_tool_only_the_root_resolves_affects_everything() -> None:
    before = _lockfile(*BASE)
    now = before.replace(
        'name = "pytest"\nversion = "8.0.0"', 'name = "pytest"\nversion = "9.0.0"'
    )
    assert lock_affected(before, now, Delta((), ())) is None


def test_a_shared_dependency_moves_every_member_resolving_it() -> None:
    before = _lockfile(*BASE)
    now = before.replace(
        'name = "six"\nversion = "1.16.0"', 'name = "six"\nversion = "1.17.0"'
    )
    assert lock_affected(before, now, Delta((), ())) == {"packages/a", "packages/b"}


def test_an_added_member_moves_only_itself() -> None:
    before = _lockfile(*BASE)
    now = _lockfile(
        _root("acme-a", "acme-b", "acme-c", "pytest"),
        *BASE[1:],
        _entry("acme-c", "0.1.0", '{ editable = "packages/c" }', "attrs"),
        _entry("attrs", "24.1.0", PYPI),
        members='"acme-a", "acme-b", "acme-c"',
    )
    assert lock_affected(
        now=now, before=before, delta=_delta(("packages/c", "acme-c"))
    ) == {"packages/c"}


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def _member(root: Path, member: str, *, kind: str = "python") -> None:
    directory = root / "packages" / member
    (directory / "src").mkdir(parents=True)
    name = "acme-" + member.replace("/", "-")
    (directory / "workshop.toml").write_text(f'kind = "{kind}"\nname = "{name}"\n')
    (directory / "pyproject.toml").write_text(f'[project]\nname = "{name}"\n')
    (directory / "src" / "x.py").write_text("X = 1\n")


def _workspace(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "ws"
    root.mkdir()
    _git(root, "init", "-q", "--initial-branch=main")
    _git(root, "config", "user.email", "t@acme.test")
    _git(root, "config", "user.name", "T")
    (root / "workshop.toml").write_text("[workspace]\n")
    _member(root, "a")
    (root / "pyproject.toml").write_text('members = ["packages/a"]\nline-length = 88\n')
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "seed")
    return root, _git(root, "rev-parse", "HEAD")


def _paths(root: Path, before: str) -> list[str]:
    _git(root, "add", "-A")
    return _git(root, "diff", "--cached", "--name-only", before).splitlines()


def test_a_root_change_without_a_before_point_affects_everything(
    tmp_path: Path,
) -> None:
    root, _before = _workspace(tmp_path)
    packages = discover_packages(root)
    assert affected_from_paths(root, packages, ["pyproject.toml"]) is None


def test_an_unexplained_root_change_still_affects_everything(tmp_path: Path) -> None:
    root, before = _workspace(tmp_path)
    _member(root, "b")
    (root / "pyproject.toml").write_text(
        'members = ["packages/a", "packages/b"]\nline-length = 100\n'
    )
    paths = _paths(root, before)
    scope = affected_from_paths(
        root, discover_packages(root), paths, git=GitOps(root), before=before
    )
    assert scope is None


def test_adding_a_package_affects_that_package_alone(tmp_path: Path) -> None:
    root, before = _workspace(tmp_path)
    _member(root, "extensions/ruff")
    (root / "pyproject.toml").write_text(
        'members = ["packages/a", "packages/extensions/ruff"]\nline-length = 88\n'
    )
    paths = _paths(root, before)
    scope = affected_from_paths(
        root, discover_packages(root), paths, git=GitOps(root), before=before
    )
    assert scope is not None
    assert [package.path for package in scope.packages] == ["packages/extensions/ruff"]


def test_removing_a_package_affects_nothing_else(tmp_path: Path) -> None:
    root, _seed = _workspace(tmp_path)
    _member(root, "b")
    (root / "pyproject.toml").write_text(
        'members = ["packages/a", "packages/b"]\nline-length = 88\n'
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "b")
    before = _git(root, "rev-parse", "HEAD")
    _git(root, "rm", "-q", "-r", "packages/b")
    (root / "pyproject.toml").write_text('members = ["packages/a"]\nline-length = 88\n')
    paths = _paths(root, before)
    delta = package_delta(GitOps(root), before, discover_packages(root))
    assert delta.removed == (("packages/b", "acme-b"),)
    scope = affected_from_paths(
        root, discover_packages(root), paths, git=GitOps(root), before=before
    )
    assert scope is not None and scope.packages == ()
