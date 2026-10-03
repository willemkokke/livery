"""Compose the fragment engine's files into a tree a test rendered."""

from __future__ import annotations

from pathlib import Path

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

    The copier render writes the rendered files; the project file, the
    editor files and the ignore and attribute lines come from the listed
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
