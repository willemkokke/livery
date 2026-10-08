"""Which keys a contract may hold, who owns each, and the judge that refuses the rest.

Every key of a ``workshop.toml`` is declared by its owner: the base
declares its own in ``contract.toml`` beside this module, the tool
store declares the ``[toolroom]`` table in the fragment it ships
([livery.toolroom.store.SCHEMA_FRAGMENT][]), and an extension declares
the keys it reads under ``[contract.<contract>.<table>]`` in its
``extension.toml``, read without importing the extension. The base's
file declares the keys of ``extension.toml`` itself as well, the
``extension`` contract.

[livery.workshop._contract.load_contract][] judges every contract it
reads against the JSON Schema the declarations compose
([livery.workshop._schema.composed][]): a key no owner declares, a key
whose owner the root's ``[workspace] extensions`` does not list, a value
of the wrong type and a value outside its allowed set each refuse,
naming the file, the key, what the table takes, and the nearest match.

A path is dotted. ``*`` stands for any one name in a table whose
keys are the user's (``toolroom.modes.*``); ``[]`` stands for the
entries of a list (``ci.schedule[].every``). A key the user names may
hold a dot itself (``".vscode/settings.json"``): the judge walks a
contract's keys one table at a time, so such a key is one name.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, get_args

from livery.footman import fail

#: The kinds of contract: the workspace's at the root, a package's in
#: its directory, and an extension's declaration file.
ContractKind = Literal["root", "package", "extension"]

#: The value types a key may take. ``strs`` is a list of strings;
#: ``list`` is a list whose entries the ``[]`` path declares; ``table``
#: is a table whose keys are declared beneath it; ``any`` is a value
#: the reader judges itself, nothing beneath it declared here.
Type = Literal["str", "int", "number", "bool", "strs", "list", "table", "any"]

#: The value types, as a declaration file spells them.
TYPES: tuple[str, ...] = get_args(Type)

#: The base extension's name, always mounted.
BASE = "livery.workshop"

#: The owner of the ``[toolroom]`` table: the tool store, a dependency
#: of the base whose schema fragment every workspace composes with the
#: base's own keys, whatever its root lists.
TOOLROOM = "livery.toolroom.store"

#: The owners every workspace composes, whatever its root lists.
ALWAYS: tuple[str, ...] = (BASE, TOOLROOM)


@dataclass(frozen=True)
class Declared:
    """One key a contract may hold.

    Attributes:
        contract: The contract it belongs in, ``root``, ``package`` or
            ``extension``.
        path: The dotted path, with ``*`` and ``[]`` as above.
        types: The value types it takes, any one of them.
        values: The values it takes, when it takes only some.
        doc: One line saying what the key changes.
    """

    contract: ContractKind
    path: str
    types: tuple[Type, ...]
    values: tuple[str, ...] = ()
    doc: str = ""


#: The base's declaration of every key it reads, beside this module.
BASE_FILE = Path(__file__).with_name("contract.toml")

#: The tables of ``extension.toml`` that ``[for.<target>]`` takes again.
_MIRRORED = ("checks", "ci", "contributions")


@functools.cache
def base_keys() -> tuple[Declared, ...]:
    """The base's keys, read from its ``contract.toml``, in every contract.

    ``[for.<target>]`` in an ``extension.toml`` takes the checks, CI
    jobs and contributions of the file's top level again, so the file
    declares each of those keys once and they are repeated here under
    ``for.*``.

    Raises:
        DeclarationError: when the file declares a key off the shape an
            extension's ``[contract]`` table has.
    """
    import tomllib

    from livery.workshop._declaration import contract_keys_of

    found = contract_keys_of(
        tomllib.loads(BASE_FILE.read_text("utf-8")),
        BASE_FILE,
        contracts=("root", "package", "extension"),
    )
    mirrored = tuple(
        replace(item, path=f"for.*.{item.path}")
        for item in found
        if item.contract == "extension" and item.path.split(".")[0] in _MIRRORED
    )
    return found + mirrored


@functools.cache
def toolroom_keys() -> tuple[Declared, ...]:
    """The ``[toolroom]`` keys the tool store's fragment declares, in every contract.

    Raises:
        DeclarationError: when the fragment declares a key off the shape
            an extension's ``[contract]`` table has.
    """
    import tomllib

    from livery.toolroom.store import SCHEMA_FRAGMENT
    from livery.workshop._declaration import contract_keys_of

    return contract_keys_of(
        tomllib.loads(SCHEMA_FRAGMENT.read_text("utf-8")),
        SCHEMA_FRAGMENT,
        contracts=("root", "package", "extension"),
    )


@functools.cache
def extension_keys() -> tuple[Declared, ...]:
    """The keys of ``extension.toml``: the base's and the tool store's alone."""
    return tuple(
        item
        for item in (*base_keys(), *toolroom_keys())
        if item.contract == "extension"
    )


@dataclass(frozen=True)
class _Owned:
    declared: Declared
    owner: str


@functools.cache
def declarations() -> dict[tuple[ContractKind, str], _Owned]:
    """Every installed owner's declarations, by contract and path.

    The base's come from this module and the readers it names, the
    tool store's from its fragment; every
    extension's from the ``[contract]`` tables of its declaration file,
    installed or not listed alike, so a key of an unlisted extension is
    named as that extension's. Two owners declaring one path refuse,
    naming both.
    """
    from livery.footman import installed_entry_points
    from livery.workshop._declaration import contract_keys

    found: dict[tuple[ContractKind, str], _Owned] = {}

    def take(owner: str, items: tuple[Declared, ...]) -> None:
        for item in items:
            key = (item.contract, item.path)
            if key in found and found[key].owner != owner:
                fail(
                    f"the {item.contract} contract key {item.path} is declared by"
                    f" both {found[key].owner} and {owner}; one owner declares a key"
                )
            found[key] = _Owned(item, owner)

    take(BASE, base_keys())
    take(TOOLROOM, toolroom_keys())
    for entry in installed_entry_points("workshop.extensions"):
        take(entry.name, contract_keys(entry.value))
    return found


def shown(path: tuple[str, ...]) -> str:
    """*path* dotted, a name holding a dot quoted, as a reader writes it."""
    return ".".join(f'"{part}"' if "." in part else part for part in path)


def judge(
    data: dict[str, Any],
    *,
    contract: ContractKind,
    where: str,
    listed: frozenset[str] | None,
) -> list[str]:
    """The refusals *data* earns as a *contract*; empty when it is sound.

    Judged against the contract's composed schema
    ([livery.workshop._schema.composed][]), the one `fm sync` writes
    for the editor, in the judge's words
    ([livery.workshop._schema.problems][]).

    Args:
        data: The parsed contract.
        contract: Which contract it is.
        where: The file, for the caller's message.
        listed: The extensions the root lists, the base among them;
            ``None`` takes every installed owner as listed.

    Returns:
        One line per problem, in the contract's order.
    """
    from livery.workshop._schema import composed, problems

    del where
    return problems(composed(contract, listed), data)


def listed_extensions(root_data: dict[str, Any]) -> frozenset[str]:
    """The extensions a root contract lists, by name, the base always among them.

    A name is read without the options its entry turns on.
    """
    names = {BASE}
    workspace = root_data.get("workspace")
    extensions = workspace.get("extensions", []) if isinstance(workspace, dict) else []  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    from livery.workshop._extensions import listing

    for entry in extensions if isinstance(extensions, list) else []:  # pyright: ignore[reportUnknownVariableType]
        if isinstance(entry, str):
            names.add(listing(entry).name)
        elif isinstance(entry, dict) and isinstance(entry.get("name"), str):  # pyright: ignore[reportUnknownMemberType]
            names.add(listing(entry["name"]).name)  # pyright: ignore[reportUnknownArgumentType]
    return frozenset(names)
