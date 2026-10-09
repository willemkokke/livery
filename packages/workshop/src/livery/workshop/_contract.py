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

    Raises:
        Failed: when *text* is not TOML, or a key is spelled with an
            underscore, naming *where*.
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        fail(f"{where}: not valid TOML: {error}")
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
    """Parse the contract at *path* and judge its keys; a refusal names the file.

    A contract under ``packages/<name>/`` or ``packages/<group>/<name>/``
    is a package's, judged against the extensions its workspace's root
    contract lists and its own set of package-level extensions; any
    other is a root contract, judged against the extensions it lists
    itself and the package-level ones its packages' sets hold. Every
    problem is listed in one refusal
    ([livery.workshop._contract_keys.judge][]).
    """
    from livery.workshop._contract_keys import judge, listed_extensions
    from livery.workshop._extensions import composed_set, package_extensions

    data = parse_contract(path.read_text("utf-8"), where=str(path))
    workspace = _package_workspace(path)
    if workspace is not None:
        root = workspace / CONTRACT
        listed = None
        if root.is_file():
            own = data.get("extensions")
            entries = [str(name) for name in own] if isinstance(own, list) else []
            listed = listed_extensions(_root_tables(root)) | set(composed_set(entries))
        problems = judge(data, contract="package", where=str(path), listed=listed)
    else:
        listed = listed_extensions(data) | set(package_extensions(path.parent))
        problems = judge(data, contract="root", where=str(path), listed=listed)
    if problems:
        fail(f"{path}:\n" + "\n".join(f"  {line}" for line in problems))
    return data


def read_contract(directory: Path) -> dict[str, Any]:
    """The contract in *directory*, its keys judged: the workspace's or a package's.

    *directory* is the workspace root or a package's directory, and the
    contract is its ``workshop.toml``. A package's contract is judged
    against the extensions the workspace lists, the root's against its
    own list, so a key of an extension the workspace does not list
    refuses naming the extension.

    Raises:
        Failed: when the contract holds a key no listed extension and
            not the workshop declares, or a value of the wrong type,
            naming the file and every such key.
        FileNotFoundError: when *directory* holds no contract.
    """
    return load_contract(directory / CONTRACT)


def _package_workspace(path: Path) -> Path | None:
    """The workspace root of the package contract at *path*; None for a root's.

    A package sits at ``packages/<name>/`` or, in a group directory, at
    ``packages/<group>/<name>/``; a group has no contract of its own.
    """
    if path.parent.parent.name == "packages":
        return path.parent.parent.parent
    group = path.parent.parent
    if group.parent.name == "packages" and not (group / CONTRACT).is_file():
        return group.parent.parent
    return None


def _root_tables(path: Path) -> dict[str, Any]:
    """The root contract's tables, unjudged: a package's read needs its list."""
    try:
        return tomllib.loads(path.read_text("utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def contract_paths(root: Path) -> list[Path]:
    """The root contract and every member's, the ones that exist."""
    paths = [root / CONTRACT]
    packages = root / "packages"
    if packages.is_dir():
        found: list[Path] = []
        for directory in (p for p in packages.iterdir() if p.is_dir()):
            if (directory / CONTRACT).is_file():
                found.append(directory / CONTRACT)
            else:
                # A group directory: its packages are one level down.
                found += (p / CONTRACT for p in directory.iterdir() if p.is_dir())
        paths += sorted(found)
    return [path for path in paths if path.is_file()]
