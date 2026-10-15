"""Every package of this workspace lists its extensions.

A package that lists none holds none, so no step judges it and nothing
builds or releases it. Each contract here lists its extensions.
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
