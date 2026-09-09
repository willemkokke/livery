"""Read a workshop contract; its keys are kebab-case, and the read enforces it.

``workshop.toml`` is the workspace contract at the root and a
package's contract in its directory. Every reader parses one through
[livery.workshop._contract.load_contract][], or
[livery.workshop._contract.parse_contract][] for text already in
hand, and a key spelled with an underscore refuses at any depth,
naming the key, its kebab-case spelling, and the verb that rewrites
it. [livery.workshop._contract.migrate_contracts][] is that rewrite:
textual, so comments and values stay as written, and idempotent. The
render verbs run it before anything parses the file.

A contract read from git history is the one read that cannot be
rewritten; [livery.workshop._contract.normalise_keys][] reads it as
if it were migrated instead of refusing.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import livery.footman as footman
from livery.footman import fail

#: The contract file's name, at the root and in each package directory.
CONTRACT = "workshop.toml"

#: A ``key = value`` line's key, with the whitespace around it. Table
#: headers, comments, and the lines inside a multi-line value do not
#: match: a header has no ``=``, a comment starts with ``#``, and an
#: inline table's line starts with ``{``.
_KEY_LINE = re.compile(r"^([ \t]*)([A-Za-z0-9_-]+)([ \t]*=)", re.M)


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

    The lenient read for a contract that cannot be rewritten: one
    from git history, where a checkout before the migration still
    spells its keys with underscores.
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
            f" Run `{footman.prog()} template.apply` to rewrite the keys of"
            " every contract in this workspace."
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


def migrate_keys(text: str) -> tuple[str, list[str]]:
    """Contract *text* with every underscore key in kebab-case; what moved.

    Only the key of a ``key = value`` line changes: comments, values,
    and table headers stay byte for byte, so a string value with an
    underscore in it is never touched. A key this rewrite cannot
    reach (one inside an inline table, or in a table header) is left
    for the caller's check.
    """
    moved: list[str] = []

    def swap(match: re.Match[str]) -> str:
        key = match.group(2)
        if "_" not in key:
            return match.group(0)
        moved.append(f"{key} -> {kebab(key)}")
        return f"{match.group(1)}{kebab(key)}{match.group(3)}"

    return _KEY_LINE.sub(swap, text), moved


def migrate_contracts(root: Path) -> list[str]:
    """Rewrite the keys of every contract under *root* to kebab-case; what changed.

    One line per rewritten file, naming each key that moved. A
    rewrite is verified before it is written: parsed, the new text
    must equal the old tree with its keys normalised, and a key the
    textual rewrite cannot reach refuses naming it, so a contract is
    never half-migrated. Idempotent: a kebab-case contract changes
    nothing.
    """
    notes: list[str] = []
    for path in contract_paths(root):
        text = path.read_text("utf-8")
        rewritten, moved = migrate_keys(text)
        expected = normalise_keys(tomllib.loads(text))
        if tomllib.loads(rewritten) != expected:
            stubborn = ", ".join(underscore_keys(tomllib.loads(rewritten)))
            fail(
                f"{path}: {stubborn} cannot be rewritten in place (a key in"
                " a table header or an inline table); spell it kebab-case"
                " by hand"
            )
        if not moved:
            continue
        path.write_text(rewritten, encoding="utf-8")
        notes.append(f"{path.relative_to(root).as_posix()}: {', '.join(moved)}")
    return notes
