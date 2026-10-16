"""What the conan suite builds its workspaces with.

A template's seeds written into a test's tree, cmake's steps registered
for one test, what a faked conan call answers, and the skip for a test
that mounts an extension this distribution does not depend on.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from livery.workshop import _extensions
from livery.workshop._steps import StepRecord, restore, snapshot

#: This repository's identity, for a scratch workspace's `[workspace]` table.
IDENTITY = (
    'name = "livery"\n'
    'description = "The livery ecosystem monorepo (virtual root)."\n'
    'namespace = "livery"\n'
    'authors = [{ name = "Willem Kokke", email = "mail@willem.net" }]\n'
    'copyright-year = "2026"\n'
)

#: What a birth's data carries when a test names none: this repository's
#: identity, and the facts the contract and the process inject.
SEED_DEFAULTS: dict[str, Any] = {
    "project_name": "livery",
    "project_description": "The livery ecosystem monorepo (virtual root).",
    "namespace_package": "livery",
    "author_name": "Willem Kokke",
    "author_email": "mail@willem.net",
    "copyright_year": "2026",
    "runner_prog": "fm",
    "python_floor": "3.11",
    "forge_kind": "github",
    "forge_owner": "",
    "forge_url": "",
}


def seed_into(destination: Path, template: str, data: Mapping[str, Any]) -> Path:
    """Write the files a creation from *template* writes into *destination*.

    The `package-base` seeds, then the files of the template's chain.
    They come from a scratch workspace listing what the template needs
    and nothing else, since a creation installs nothing; *data*
    overrides `SEED_DEFAULTS`, and the derived names follow the
    package's name.
    """
    from livery.workshop._identity import default_description
    from livery.workshop._seeds import (
        chain_needs,
        create_package,
        derived,
        template_chain,
    )

    full = {**SEED_DEFAULTS, **data}
    full.setdefault(
        "package_description",
        default_description(full["package_name"], full["project_name"]),
    )
    full.update(derived(full))
    listed = ", ".join(f'"{name}"' for name in chain_needs(template_chain(template)))
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        (root / "workshop.toml").write_text(
            "[workspace]\n" + IDENTITY + f"extensions = [{listed}]\n",
            encoding="utf-8",
        )
        create_package(root, destination, template, full)
    return destination


class Said:
    """What a faked conan call answers: its exit code and its output.

    Attributes:
        code: The exit code.
        stdout: What conan printed on its standard output.
        stderr: What conan printed on its standard error.
    """

    def __init__(self, code: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.code = code
        self.stdout = stdout
        self.stderr = stderr


def skip_unless_installed(*extensions: str) -> None:
    """Skip the running test while an extension of *extensions* is not installed.

    The conan suite mounts extensions this distribution does not depend
    on: cmake, which conan's template lists, and nanobind, which
    requires conan. A run of this wheel alone, as a release leg makes,
    installs neither. The reason names each missing extension and the
    distribution that ships it.
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


@pytest.fixture(name="cmake_steps")
def cmake_steps_fixture() -> Iterator[tuple[StepRecord, ...]]:
    """Register cmake's steps from its declaration for the test; the records.

    The mount registers cmake's build and test steps for a workspace
    whose packages hold cmake, and a test that reads them calls the
    registry with no mount before it. The registry is put back after
    the test, which skips while cmake is not installed.
    """
    from livery.workshop._extensions import declaration, register_declared

    skip_unless_installed("cmake")
    found = declaration("cmake")
    assert found is not None
    state = snapshot()
    register_declared("cmake", found.additions)
    try:
        yield found.additions.steps
    finally:
        restore(state)
