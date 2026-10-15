"""The cmake extension as a distribution of its own: what it composes, and the kit."""

from __future__ import annotations

import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest

from livery.workshop._steps import StepRecord, restore, snapshot, steps_by_name
from livery.workshop.testing import CLAUSES, Clause, Subject

CONTRACT = """\
[workspace]
name = "acme"
description = "The acme monorepo (virtual root)."
namespace = "acme"
authors = [{ name = "Acme", email = "dev@acme.test" }]
copyright-year = "2026"
extensions = []
"""


@pytest.fixture
def registered() -> Iterator[tuple[StepRecord, ...]]:
    """Cmake's steps registered, as the mount registers a listed extension's.

    The records registered under cmake's name; the registry is put back
    after the test.
    """
    from livery.workshop._extensions import declaration, register_declared

    state = snapshot()
    found = declaration("cmake")
    assert found is not None
    register_declared("cmake", found.additions)
    try:
        yield tuple(
            record for record in steps_by_name().values() if record.extension == "cmake"
        )
    finally:
        restore(state)


def test_its_own_distribution_ships_cmake_as_a_package_level_extension() -> None:
    from importlib.metadata import distribution

    from livery.workshop._extensions import declaration, shipping_distribution

    found = {
        entry.name: entry.value
        for entry in distribution("livery-extensions-cmake").entry_points
        if entry.group == "workshop.extensions"
    }
    assert found == {"cmake": "livery.extensions.cmake"}
    shipped = shipping_distribution("livery.extensions.cmake")
    assert shipped is not None and shipped.name == "livery-extensions-cmake"
    declared = declaration("cmake")
    assert declared is not None
    assert declared.levels == ("package",)
    # cmake names no language: a package lists it alone and builds with
    # the compilers the host provides.
    assert declared.requires == ()


def test_a_member_listing_cmake_composes_livery_extensions_cmake_into_the_dev_group(
    tmp_path: Path,
) -> None:
    from livery.workshop._shipped_files import outputs

    root = tmp_path / "acme"
    member = root / "packages" / "lib"
    member.mkdir(parents=True)
    (root / "workshop.toml").write_text(CONTRACT)

    def dev_group() -> list[str]:
        (composed,) = [o for o in outputs(root) if o.path == "pyproject.toml"]
        groups = tomllib.loads(composed.body.decode())["dependency-groups"]
        return list(groups["dev"])

    # A member that lists conan alone holds no cmake, and nothing of
    # cmake's distribution reaches the dev group.
    (member / "workshop.toml").write_text('extensions = ["conan"]\nname = "acme-lib"\n')
    assert "livery-extensions-cmake" not in dev_group()
    # Listed, cmake's distribution joins the dev group, so a sync
    # installs what the member's steps run.
    (member / "workshop.toml").write_text(
        'extensions = ["cmake", "conan"]\nname = "acme-lib"\n'
    )
    assert "livery-extensions-cmake" in dev_group()


@pytest.mark.parametrize("clause", CLAUSES, ids=lambda clause: clause.name)
def test_cmake_conforms(clause: Clause, registered: tuple[StepRecord, ...]) -> None:
    assert {record.name for record in registered} == {
        "build.configure",
        "build.compile",
        "test.ctest",
    }
    subject = Subject(
        "livery.extensions.cmake",
        sets=(("cmake",), ("cmake", "conan")),
        steps=registered,
    )
    assert clause.judge(subject) == []
