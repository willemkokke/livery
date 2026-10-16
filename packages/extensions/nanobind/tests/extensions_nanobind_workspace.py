"""What the nanobind suite builds its workspaces with.

A template's seeds written into a test's tree, the wheels job nanobind
declares added to the release point for one test, and the skip for a
test that mounts an extension this distribution does not depend on.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from livery.workshop import _extensions

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


@contextmanager
def jobs_restored() -> Iterator[None]:
    """Put the contributed jobs back as they were on entry, whatever ran inside."""
    from livery.workshop import _points

    state = _points.snapshot()
    try:
        yield
    finally:
        _points.restore(state)


@pytest.fixture
def nanobind_jobs() -> Iterator[None]:
    """Add nanobind's jobs from its declaration file, for the test alone.

    The mount adds them for a workspace whose packages hold nanobind,
    and a test that renders the release point calls the code directly,
    with no mount before it. The contributed jobs are put back as they
    were after the test.
    """
    from livery.workshop._extensions import declaration, register_jobs

    found = declaration("nanobind")
    assert found is not None
    with jobs_restored():
        register_jobs("nanobind", found.additions.jobs)
        yield


def skip_unless_installed(*extensions: str) -> None:
    """Skip the running test while an extension of *extensions* is not installed.

    The nanobind suite mounts extensions this distribution does not
    depend on, cmake and docs among them. A run of this wheel alone, as
    a release leg makes, installs none of them. The reason names each
    missing extension and the distribution that ships it.
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
