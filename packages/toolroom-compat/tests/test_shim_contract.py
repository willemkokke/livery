"""The shim's whole contract: one dependency, the surface forwarded.

The real package has no ``__all__``: its surface is the stub plus a
``__getattr__`` that mints any name as a tool, so the shim forwards
attributes rather than re-exporting a list, and the contract is
identity per name.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import toolroom
from livery import toolroom as real


def test_the_shim_depends_on_exactly_the_real_distribution() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    parsed = tomllib.loads(pyproject.read_text("utf-8"))
    dependencies = parsed["project"]["dependencies"]
    assert len(dependencies) == 1
    assert dependencies[0].startswith("livery-toolroom>=")


def test_every_declared_name_forwards_identically() -> None:
    for name in dir(real):
        if name.startswith("_"):
            continue
        assert getattr(toolroom, name) is getattr(real, name), name


def test_the_dynamic_minting_forwards() -> None:
    # Any executable is a tool, through the shim exactly as directly:
    # the same class mints the handle, so the chain grammar is one.
    # (No attribute compare: on a Tool every attribute mints a
    # sub-verb, which is the very behaviour under test.)
    assert type(toolroom.terraform) is real.Tool
    assert type(toolroom.terraform) is type(real.terraform)


def test_the_testing_module_forwards() -> None:
    import livery.toolroom.testing as real_testing
    import toolroom.testing

    for name in dir(real_testing):
        if name.startswith("_"):
            continue
        assert getattr(toolroom.testing, name) is getattr(real_testing, name), name
