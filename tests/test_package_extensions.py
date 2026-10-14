"""Every package of this workspace lists its package-level extensions.

A package that lists none holds only what its kind's fallback gives
it. Each contract here lists its extensions, so none depends on that
fallback.
"""

from __future__ import annotations

from pathlib import Path

from livery.workshop import discover_packages, read_contract

ROOT = Path(__file__).resolve().parents[1]


def test_every_package_here_lists_its_extensions() -> None:
    unlisted = [
        package.path
        for package in discover_packages(ROOT)
        if not read_contract(package.directory).get("extensions")
    ]
    assert unlisted == [], (
        "these packages list no extensions in their workshop.toml; list"
        f' them, for example `extensions = ["python"]`: {unlisted}'
    )
