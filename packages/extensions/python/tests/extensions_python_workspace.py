"""What the python suite builds its workspaces with.

The contracts a test writes, a python member at a version, the
workspace of two members the release legs build and install, and the
skip for a test that mounts an extension this distribution does not
depend on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.workshop import _extensions


def skip_unless_installed(*extensions: str) -> None:
    """Skip the running test while an extension of *extensions* is not installed.

    The suite mounts extensions this distribution does not depend on,
    such as pytest, which asks python's runners. A run of this wheel
    alone, as a release leg makes, installs none of them. The reason
    names each missing extension and the distribution that ships it.
    """
    missing = [
        name
        for name in dict.fromkeys(extensions)
        if _extensions.installed_declaration(name) is None
    ]
    if missing:
        named = ", ".join(
            f"{name} ({_extensions.distribution_of(name)})" for name in missing
        )
        pytest.skip(f"not installed here: {named}")


def root_contract(root: Path, extensions: str) -> None:
    """Write *root*'s contract listing *extensions*, a TOML list as written."""
    (root / "workshop.toml").write_text(f"[workspace]\nextensions = {extensions}\n")


def package_contract(root: Path, member: str, extensions: str) -> Path:
    """Write the contract of ``packages/<member>`` listing *extensions*; its path."""
    directory = root / "packages" / member
    directory.mkdir(parents=True, exist_ok=True)
    contract = directory / "workshop.toml"
    contract.write_text(f"extensions = {extensions}\n")
    return contract


def member(root: Path, name: str, *, floor_on: str = "") -> None:
    """A python member *name* under *root* at 0.2.0, with a floor on *floor_on*."""
    directory = root / "packages" / name
    (directory / "src" / "livery" / name).mkdir(parents=True)
    depends = (
        f'[[depends]]\npath = "packages/{floor_on}"\nkind = "build"\nfloor = "0.1.0"\n'
        if floor_on
        else ""
    )
    requirement = f'"livery-{floor_on}>=0.1.0"' if floor_on else ""
    (directory / "workshop.toml").write_text(
        f'extensions = ["python"]\nname = "livery-{name}"\n{depends}'
    )
    (directory / "pyproject.toml").write_text(
        f'[project]\nname = "livery-{name}"\nversion = "0.2.0"\n'
        f"dependencies = [{requirement}]\n"
    )
    (directory / "src" / "livery" / name / "__init__.py").write_text(
        '__version__ = "0.2.0"\n'
    )


@pytest.fixture(name="workspace")
def two_members(tmp_path: Path) -> Path:
    """A workspace of two python members, ``core`` and ``tool`` on it; its root."""
    root = tmp_path / "ws"
    root.mkdir()
    (root / "workshop.toml").write_text("[workspace]\n")
    member(root, "core")
    member(root, "tool", floor_on="core")
    return root
