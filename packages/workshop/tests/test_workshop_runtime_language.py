"""The runtime's language: which members are its distributions.

The workshop runs on python, and one constant in the base names that
language's extension. A member holds it when the extensions it lists,
and those they require, include it. A member holding it carries a
``pyproject.toml`` and joins the uv workspace; any other member does
neither.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from livery.workshop._identity import python_dirs
from livery.workshop._packages import discover_packages
from workshop_extension_fakes import fake_extensions


def _member(root: Path, name: str, contract: str, *files: str) -> None:
    """Write the member *name* under *root*: its *contract*, and each of *files* empty.

    The contract names the member ``acme-<name>`` before *contract*'s
    lines.
    """
    directory = root / "packages" / name
    directory.mkdir(parents=True)
    (directory / "workshop.toml").write_text(f'name = "acme-{name}"\n{contract}')
    for file in files:
        (directory / file).parent.mkdir(parents=True, exist_ok=True)
        (directory / file).write_text("")


def test_a_member_holding_the_runtime_s_language_without_a_pyproject_refuses_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An extension that cannot work without python.
    fake_extensions(
        tmp_path,
        monkeypatch,
        binding='[extension]\nrequires = ["python"]\n',
    )
    root = tmp_path / "acme"
    # No member here has a pyproject.toml. Two hold the runtime's
    # language: one by its list, and one through what its list requires.
    _member(root, "by-list", 'extensions = ["python"]\n')
    _member(root, "by-requirement", 'extensions = ["acme.binding"]\n')
    # Two hold something else: a list without it, and a member that
    # lists nothing and holds nothing.
    _member(root, "listed-native", 'extensions = ["cmake", "conan"]\n')
    _member(root, "plain", "")
    holding = ("by-list", "by-requirement")
    with pytest.raises(ValueError) as refused:
        discover_packages(root)
    assert str(refused.value).splitlines() == [
        "packages missing a file they need:",
        *(f"  {name}: holds python and has no pyproject.toml" for name in holding),
    ]
    for name in holding:
        (root / "packages" / name / "pyproject.toml").write_text("")
    assert [package.member for package in discover_packages(root)] == [
        *holding,
        "listed-native",
        "plain",
    ]


def test_a_member_not_holding_it_joins_no_uv_workspace(tmp_path: Path) -> None:
    from livery.workshop._shipped_files import outputs

    root = tmp_path / "acme"
    root.mkdir()
    (root / "workshop.toml").write_text('[workspace]\nname = "acme"\nextensions = []\n')
    # Two members hold the runtime's language, each by its list.
    _member(root, "echo", 'extensions = ["python"]\n', "pyproject.toml", "src/echo.py")
    _member(
        root,
        "plain",
        'extensions = ["python"]\n',
        "pyproject.toml",
        "tests/test_plain.py",
    )
    # Three do not, each with python files of its own: a list without it;
    # a member that lists nothing; and the C++ library's list.
    _member(root, "listed-native", 'extensions = ["cpp"]\n', "src/a.py")
    _member(root, "native", "", "conanfile.py", "tests/test_n.py")
    _member(
        root,
        "lib",
        'extensions = ["cmake", "conan"]\n',
        "conanfile.py",
        "src/b.py",
    )
    (composed,) = [
        output for output in outputs(root) if output.path == "pyproject.toml"
    ]
    project = tomllib.loads(composed.body.decode())
    joined = ["packages/echo", "packages/plain"]
    assert project["tool"]["uv"]["workspace"]["members"] == joined
    assert sorted(project["tool"]["uv"]["sources"]) == ["acme-echo", "acme-plain"]
    members = [line for line in project["dependency-groups"]["dev"] if "acme-" in line]
    assert members == ["acme-echo", "acme-plain"]
    # The checkers read the same members' directories alone.
    assert python_dirs(root) == ["packages/echo/src", "packages/plain/tests"]
