"""Compose each contract's JSON Schema from the keys its owners declare.

The contract judge holds every key a contract may have as a record
([livery.workshop._contract_keys.Declared][]): the base's own, and each
installed extension's from its declaration file. `fm sync` writes them
as JSON Schema under ``.workshop/schema/``, one file per contract, from
the base and the extensions the root contract lists, and the composed
``.taplo.toml`` points Taplo, the language server behind an editor's
TOML support, at them: a contract is completed and validated as it is
typed. The schema says what shape a contract has; the judge keeps the
rules a schema cannot say.

A key's types become JSON Schema types, its values an ``enum`` and its
doc the ``description``. A table whose keys the author names (``*``
in a declared path) takes ``additionalProperties`` with the entry's
schema; every other table refuses a key it does not declare, as the
judge does.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from livery.workshop._contract_keys import ContractKind, Declared

#: Where the composed schemas live: this checkout's own, written by the
#: sync and never committed, since they follow what is installed.
DIRECTORY = ".workshop/schema"

#: Each contract's schema file, by contract.
FILES: dict[ContractKind, str] = {
    "root": "workshop.json",
    "package": "package.json",
    "extension": "extension.json",
}

#: The JSON Schema draft the files declare: the newest Taplo validates.
DRAFT = "http://json-schema.org/draft-07/schema#"

#: What each file describes, for its title.
_TITLES: dict[ContractKind, str] = {
    "root": "workshop.toml at a workspace's root",
    "package": "workshop.toml in a package's directory",
    "extension": "extension.toml beside an extension's package",
}


@dataclass
class _Node:
    """One key of a contract, with what is declared beneath it."""

    declared: Declared | None = None
    children: dict[str, _Node] = field(default_factory=dict[str, "_Node"])
    items: _Node | None = None


def _tree(keys: list[Declared]) -> _Node:
    """The contract's keys as a tree: ``*`` a child, ``[]`` a list's entries."""
    root = _Node()
    for declared in keys:
        node = root
        for segment in declared.path.split("."):
            name = segment.removesuffix("[]")
            node = node.children.setdefault(name, _Node())
            if segment.endswith("[]"):
                if node.items is None:
                    node.items = _Node()
                node = node.items
        node.declared = declared
    return root


def _object(node: _Node) -> dict[str, Any]:
    """A table: its declared keys, and its author-named ones when it has them."""
    found: dict[str, Any] = {"type": "object"}
    properties = {
        name: _schema(child)
        for name, child in sorted(node.children.items())
        if name != "*"
    }
    if properties:
        found["properties"] = properties
    wild = node.children.get("*")
    found["additionalProperties"] = _schema(wild) if wild is not None else False
    return found


def _branch(kind: str, node: _Node) -> dict[str, Any]:
    """The schema of one of a key's types."""
    values = node.declared.values if node.declared is not None else ()
    if kind == "str":
        return (
            {"type": "string", "enum": list(values)} if values else {"type": "string"}
        )
    if kind == "int":
        return {"type": "integer"}
    if kind == "number":
        return {"type": "number"}
    if kind == "bool":
        return {"type": "boolean"}
    if kind == "strs":
        return {"type": "array", "items": {"type": "string"}}
    if kind == "list":
        entries = _schema(node.items) if node.items is not None else {}
        return {"type": "array", "items": entries}
    if kind == "table":
        return _object(node)
    return {}


def _schema(node: _Node) -> dict[str, Any]:
    """A key's schema: each of its types, any one of them, and its doc."""
    types = node.declared.types if node.declared is not None else ("table",)
    branches = [_branch(kind, node) for kind in types]
    found = branches[0] if len(branches) == 1 else {"anyOf": branches}
    if node.declared is not None and node.declared.doc:
        found = {"description": node.declared.doc, **found}
    return found


def compose(contract: ContractKind, listed: frozenset[str] | None) -> dict[str, Any]:
    """The JSON Schema of *contract*, from the keys the base and *listed* declare.

    *listed* holds the names the root contract lists the extensions by,
    the base among them; None takes every installed owner. An
    extension's keys in the ``extension`` contract are the base's alone,
    since an extension declares keys in a workspace's contracts and
    never in another extension's file.
    """
    from livery.workshop._contract_keys import BASE, declarations

    keys = [
        owned.declared
        for (kind, _path), owned in sorted(declarations().items())
        if kind == contract
        and (listed is None or owned.owner == BASE or owned.owner in listed)
    ]
    return {
        "$schema": DRAFT,
        "$comment": (
            "Composed by the workshop's sync from the keys the base and the"
            " listed extensions declare; written again by every sync, never"
            " edited."
        ),
        "title": _TITLES[contract],
        **_object(_tree(keys)),
    }


def schema_files(root: Path) -> dict[str, bytes]:
    """Each contract's composed schema for *root*, by its path under the root."""
    from livery.workshop._contract import _root_tables
    from livery.workshop._contract_keys import listed_extensions

    listed = listed_extensions(_root_tables(root / "workshop.toml"))
    return {
        f"{DIRECTORY}/{name}": (
            json.dumps(compose(contract, listed), indent=2) + "\n"
        ).encode("utf-8")
        for contract, name in FILES.items()
    }
