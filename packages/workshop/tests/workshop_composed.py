"""Write a birth's seeds and the fragment engine's files into a test's tree."""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

IDENTITY = (
    'name = "livery"\n'
    'description = "The livery ecosystem monorepo (virtual root)."\n'
    'namespace = "livery"\n'
    'authors = [{ name = "Willem Kokke", email = "mail@willem.net" }]\n'
    'copyright-year = "2026"\n'
)
"""This repository's identity, for a fixture's `[workspace]` table."""


def compose_into(destination: Path) -> Path:
    """Write the composed files into *destination*, as `fm sync` would there.

    The seeds are written by `seed_into`; the project file, the editor
    files and the ignore and attribute lines come from the listed
    extensions through the engine. A tree with no contract of its own
    gets this repository's identity and no extensions, so it composes
    with the base alone.
    """
    from livery.workshop._shipped_files import deliver

    contract = destination / "workshop.toml"
    seeded = not contract.is_file()
    if seeded:
        contract.write_text(
            "[workspace]\n" + IDENTITY + "extensions = []\n", encoding="utf-8"
        )
    deliver(destination)
    if seeded:
        contract.unlink()
    return destination


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


def seed_into(destination: Path, seeded: str, data: Mapping[str, Any]) -> Path:
    """Write a birth's files into *destination*: the project's seeds or a template's.

    ``project`` is the workspace's seed tree. Any other name is a
    template (`python-library`, ...), whose birth writes the
    `package-base` seeds, then the files of its chain. The seeds come
    from the base alone, through a scratch workspace listing what the
    template needs and nothing else, since a birth installs nothing;
    *data* overrides `SEED_DEFAULTS`, and the derived names follow the
    package's name.
    """
    from livery.workshop._identity import default_description
    from livery.workshop._seeds import (
        PROJECT,
        chain_needs,
        create,
        create_package,
        derived,
        template_chain,
    )

    full = {**SEED_DEFAULTS, **data}
    if "package_name" in full:
        full.setdefault(
            "package_description",
            default_description(full["package_name"], full["project_name"]),
        )
        full.update(derived(full))
    needs = () if seeded == PROJECT else chain_needs(template_chain(seeded))
    listed = ", ".join(f'"{name}"' for name in needs)
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        (root / "workshop.toml").write_text(
            "[workspace]\n" + IDENTITY + f"extensions = [{listed}]\n",
            encoding="utf-8",
        )
        if seeded == PROJECT:
            create(root, destination, PROJECT, full)
        else:
            create_package(root, destination, seeded, full)
    return destination


def composed_groups(root: Path) -> dict[str, list[str]]:
    """The dependency groups of the project file the engine composes for *root*.

    Read from the engine's outputs, so nothing is written: the contract
    at *root* says what the workspace lists.
    """
    import tomllib

    from livery.workshop._shipped_files import outputs

    (composed,) = [
        output for output in outputs(root) if output.path == "pyproject.toml"
    ]
    groups: dict[str, list[str]] = tomllib.loads(composed.body.decode())[
        "dependency-groups"
    ]
    return groups
