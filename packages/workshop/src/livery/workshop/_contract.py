"""Read a workshop contract; its keys are kebab-case, and the read enforces it.

``workshop.toml`` is the workspace contract at the root and a
package's contract in its directory. Every reader parses one through
[livery.workshop._contract.load_contract][], or
[livery.workshop._contract.parse_contract][] for text already in
hand, and a key spelled with an underscore refuses at any depth,
naming the key, its kebab-case spelling, and the file to fix. There
is no rewrite: a wrong spelling is one edit where the refusal points.

A contract read from git history is the one read that cannot be
edited; [livery.workshop._contract.normalise_keys][] reads it as if
its keys were spelled right, since the spelling may predate the rule.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

from livery.footman import fail


def toml_string(value: str) -> str:
    """*value* as a TOML basic string, quotes and escapes included.

    A Windows path carries backslashes, which a basic string must
    escape; JSON's string syntax is the subset of TOML's that a
    path or a URL needs, so the JSON encoder is the writer.
    """
    return json.dumps(value)


#: The contract file's name, at the root and in each package directory.
CONTRACT = "workshop.toml"


def kebab(key: str) -> str:
    """*key* with every underscore replaced by a hyphen."""
    return key.replace("_", "-")


def underscore_keys(table: dict[str, Any], *, prefix: str = "") -> list[str]:
    """The dotted paths of every key under *table* spelled with an underscore.

    Walks nested tables and arrays of tables, so ``[[depends]]``
    entries and inline tables are judged like the top level.
    """
    found: list[str] = []
    for key, value in table.items():
        path = f"{prefix}{key}"
        if "_" in key:
            found.append(path)
        if isinstance(value, dict):
            found += underscore_keys(value, prefix=f"{path}.")
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    found += underscore_keys(item, prefix=f"{path}.")
    return found


def normalise_keys(table: dict[str, Any]) -> dict[str, Any]:
    """*table* with every key, at any depth, in kebab-case.

    The lenient read for a contract that cannot be edited: one from
    git history, where a checkout from before the rule may still
    spell its keys with underscores.
    """
    normalised: dict[str, Any] = {}
    for key, value in table.items():
        if isinstance(value, dict):
            value = normalise_keys(value)
        elif isinstance(value, list):
            value = [
                normalise_keys(item) if isinstance(item, dict) else item
                for item in value
            ]
        normalised[kebab(key)] = value
    return normalised


def parse_contract(text: str, *, where: str) -> dict[str, Any]:
    """Parse contract *text*; refuse a key spelled with an underscore.

    Args:
        text: The contract's TOML.
        where: How a refusal names the contract: its path, or the git
            ref it was read at.

    Returns:
        The parsed tables.
    """
    data = tomllib.loads(text)
    wrong = underscore_keys(data)
    if wrong:
        listed = ", ".join(
            f"{key} (spell it {kebab(key.split('.')[-1])})" for key in wrong
        )
        fail(
            f"{where}: contract keys are kebab-case; found {listed}."
            f" Rename each key in {where}."
        )
    return data


def load_contract(path: Path) -> dict[str, Any]:
    """Parse the contract at *path*; a refusal names the file."""
    return parse_contract(path.read_text("utf-8"), where=str(path))


def contract_paths(root: Path) -> list[Path]:
    """The root contract and every member's, the ones that exist."""
    paths = [root / CONTRACT]
    packages = root / "packages"
    if packages.is_dir():
        paths += sorted(
            directory / CONTRACT
            for directory in packages.iterdir()
            if directory.is_dir()
        )
    return [path for path in paths if path.is_file()]
