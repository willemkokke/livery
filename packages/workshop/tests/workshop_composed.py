"""Compose the fragment engine's files into a tree a test rendered."""

from __future__ import annotations

from pathlib import Path


def compose_into(destination: Path) -> Path:
    """Write the composed files into *destination*, as `fm sync` would there.

    The copier render writes the answers and the rendered files; the
    project file, the editor files and the ignore and attribute lines
    come from the listed extensions through the engine. A rendered tree
    names no extensions of its own, so it composes with the base alone.
    """
    from livery.workshop._shipped_files import deliver

    contract = destination / "workshop.toml"
    seeded = not contract.is_file()
    if seeded:
        contract.write_text("[workspace]\nextensions = []\n", encoding="utf-8")
    deliver(destination)
    if seeded:
        contract.unlink()
    return destination
