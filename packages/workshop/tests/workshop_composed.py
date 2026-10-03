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


def seed_into(destination: Path, tree: str, data: Mapping[str, Any]) -> Path:
    """Write the seeds of *tree* into *destination*, as a birth would.

    A package tree (`package-python`, ...) writes its kind's whole chain,
    `package-base` first. The seeds come from the base alone, through a
    scratch workspace listing no extensions; *data* overrides
    `SEED_DEFAULTS`, and the derived names follow the package's name.
    """
    from livery.workshop._kinds import template_chain
    from livery.workshop._seeds import create, derived

    full = {**SEED_DEFAULTS, **data}
    if "package_name" in full:
        full.setdefault(
            "package_description",
            f"{full['package_name']}: a {full['project_name']} workspace package.",
        )
        full.update(derived(full))
    trees = template_chain(tree) if tree.startswith("package-") else (tree,)
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        (root / "workshop.toml").write_text(
            "[workspace]\n" + IDENTITY + "extensions = []\n", encoding="utf-8"
        )
        create(root, destination, trees, full)
    return destination
