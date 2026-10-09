"""Each contract's JSON Schema, composed from its owners' keys, and its judge.

Every key a contract may have is declared as a record
([livery.workshop._contract_keys.Declared][]): the base's own, and each
installed extension's from its declaration file. The records compose
into one JSON Schema per contract, for the base and the extensions the
root contract lists. The contract judge validates a contract against
that schema, in its own words, and `fm sync` writes it under
``.workshop/schema/``, where the composed ``.taplo.toml`` points Taplo,
the language server behind an editor's TOML support: the judge and the
editor read one statement of each contract's shape.

A key's types become JSON Schema types, its values an ``enum`` and its
doc the ``description``. A table whose keys the author names (``*`` in
a declared path) takes ``additionalProperties`` with the entry's
schema, and a key the table declares by name wins over that entry;
every other table refuses a key it does not declare. A key of an
installed extension the root does not list stays in the schema as a
key that takes no value, carrying its owner, so the judge and the
editor both name the extension to list.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

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

#: The keyword naming the extension that owns a key the root does not
#: list; a validator that does not know it ignores it, as the draft says.
OWNER = "x-owner"

#: The keyword saying which list leaves that owner out, for the refusal.
UNLISTED_BY = "x-unlisted-by"

#: What each file describes, for its title.
_TITLES: dict[ContractKind, str] = {
    "root": "workshop.toml at a workspace's root",
    "package": "workshop.toml in a package's directory",
    "extension": "extension.toml beside an extension's package",
}

#: The schema of a list of strings: the one array the judge names whole.
_STRINGS: dict[str, Any] = {"type": "array", "items": {"type": "string"}}


@dataclass
class _Node:
    """One key of a contract, with what is declared beneath it.

    Attributes:
        declared: The key's record; None for a table only its keys declare.
        unlisted: The owner of a key the contract's lists leave out;
            empty for a key the composed schema takes.
        unlisted_by: Which list leaves the owner out, as the refusal says
            it: ``[workspace] extensions does not list``.
        children: The keys declared beneath it, ``*`` for the author's.
        items: A list's entries, when the list declares them.
    """

    declared: Declared | None = None
    unlisted: str = ""
    unlisted_by: str = ""
    children: dict[str, _Node] = field(default_factory=dict[str, "_Node"])
    items: _Node | None = None


def _tree(keys: list[tuple[Declared, str, str]]) -> _Node:
    """The contract's keys as a tree: ``*`` a child, ``[]`` a list's entries."""
    root = _Node()
    for declared, unlisted, unlisted_by in keys:
        node = root
        for segment in declared.path.split("."):
            name = segment.removesuffix("[]")
            node = node.children.setdefault(name, _Node())
            if segment.endswith("[]"):
                if node.items is None:
                    node.items = _Node()
                node = node.items
        node.declared = declared
        node.unlisted = unlisted
        node.unlisted_by = unlisted_by
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
        return dict(_STRINGS)
    if kind == "list":
        entries = _schema(node.items) if node.items is not None else {}
        return {"type": "array", "items": entries}
    if kind == "table":
        return _object(node)
    return {}


def _schema(node: _Node) -> dict[str, Any]:
    """A key's schema: each of its types, any one of them, and its doc."""
    if node.unlisted:
        return {
            "description": f"a key of {node.unlisted}, which {node.unlisted_by}",
            "not": {},
            OWNER: node.unlisted,
            UNLISTED_BY: node.unlisted_by,
        }
    types = node.declared.types if node.declared is not None else ("table",)
    branches = [_branch(kind, node) for kind in types]
    found = branches[0] if len(branches) == 1 else {"anyOf": branches}
    if node.declared is not None and node.declared.doc:
        found = {"description": node.declared.doc, **found}
    return found


def compose(contract: ContractKind, listed: frozenset[str] | None) -> dict[str, Any]:
    """The JSON Schema of *contract* for the base and the *listed* extensions.

    *listed* holds the names the contract's lists name the extensions by,
    the base among them; None takes every installed owner as listed. A
    key of an owner they leave out says which list would take it: the
    root's for an extension a workspace lists, a package's for one only
    a package lists.
    The owners in [livery.workshop._contract_keys.ALWAYS][], the base
    and the tool store, count as listed in every workspace. The
    ``extension`` contract is the base's and the tool store's alone,
    since an extension declares keys in a workspace's contracts and
    never in another extension's file: composing it reads no other
    owner's declarations, which a mount would otherwise read for every
    installed extension.
    """
    from livery.workshop._contract_keys import ALWAYS, declarations, extension_keys

    if contract == "extension":
        keys = [(declared, "", "") for declared in extension_keys()]
    else:
        keys = []
        for (kind, _path), owned in sorted(declarations().items()):
            if kind != contract:
                continue
            if listed is None or owned.owner in ALWAYS or owned.owner in listed:
                keys.append((owned.declared, "", ""))
            else:
                keys.append(
                    (owned.declared, owned.owner, _unlisted_by(owned.owner, contract))
                )
    return {
        "$schema": DRAFT,
        "$comment": (
            "Composed by the workshop's sync from the keys the base, the tool"
            " store and the listed extensions declare; written again by every"
            " sync, never edited."
        ),
        "title": _TITLES[contract],
        **_object(_tree(keys)),
    }


def _unlisted_by(owner: str, contract: ContractKind) -> str:
    """Which list leaves *owner* out, as a refusal of its key in *contract* says it."""
    from livery.workshop._extensions import PACKAGE, WORKSPACE, levels_of

    levels = levels_of(owner)
    if PACKAGE not in levels or WORKSPACE in levels:
        return "[workspace] extensions does not list"
    if contract == "package":
        return "this package's `extensions` does not list"
    return "no package's `extensions` lists"


_COMPOSED: dict[tuple[ContractKind, frozenset[str] | None], tuple[object, Any]] = {}


def composed(contract: ContractKind, listed: frozenset[str] | None) -> dict[str, Any]:
    """`compose`, once per set of declarations: what the judge validates against.

    The judge reads the composition the schema files are written from,
    never the files, so a file written before an extension was
    installed or listed cannot misjudge a key until the next sync.
    """
    from livery.workshop._contract_keys import declarations, extension_keys

    known: object = extension_keys() if contract == "extension" else declarations()
    held = _COMPOSED.get((contract, listed))
    if held is None or held[0] is not known:
        held = (known, compose(contract, listed))
        _COMPOSED[(contract, listed)] = held
    return cast("dict[str, Any]", held[1])


def contract_of(root: Path, relative: Path) -> ContractKind | None:
    """Which contract the file at *relative* is; None for a file that is no contract.

    The root's ``workshop.toml``, a package's beneath ``packages/``, or
    any ``extension.toml``.
    """
    from livery.workshop._contract import _package_workspace

    if relative.name == "extension.toml":
        return "extension"
    if relative.name != "workshop.toml":
        return None
    if relative == Path("workshop.toml"):
        return "root"
    return "package" if _package_workspace(root / relative) == root else None


def judged_by(root: Path, relative: Path) -> str:
    """The line naming the schema the file at *relative* is judged by; empty for none.

    The file under ``.workshop/schema/``, and the owners whose keys it
    holds: the base and the tool store, then the extensions the root
    lists, in list order. An ``extension.toml`` is judged by the base's
    and the tool store's keys alone.
    """
    from livery.workshop._contract_keys import ALWAYS

    contract = contract_of(root, relative)
    if contract is None:
        return ""
    owners = list(ALWAYS)
    if contract != "extension":
        from livery.workshop._extensions import extension_names

        owners += extension_names(root)
    return f"schema: {DIRECTORY}/{FILES[contract]} ({', '.join(owners)})"


def schema_files(root: Path) -> dict[str, bytes]:
    """Each contract's composed schema for *root*, by its path under the root."""
    from livery.workshop._contract import _root_tables
    from livery.workshop._contract_keys import listed_extensions
    from livery.workshop._extensions import package_extensions

    listed = listed_extensions(_root_tables(root / "workshop.toml")) | set(
        package_extensions(root)
    )
    return {
        f"{DIRECTORY}/{name}": (
            json.dumps(composed(contract, listed), indent=2) + "\n"
        ).encode("utf-8")
        for contract, name in FILES.items()
    }


# The judge: a contract against its schema, refused in the judge's words.


def problems(schema: dict[str, Any], data: dict[str, Any]) -> list[str]:
    """Each refusal *data* earns against *schema*, in the contract's order.

    An unknown key names its table, the keys the table takes and the
    nearest spelling; a key of an extension the contract's lists leave
    out names the extension and the list that would take it; a value of
    the wrong type names what the key takes; a value outside its set
    names the set and the nearest. A key that takes any value is judged
    by its reader, and nothing beneath it here.
    """
    found: list[str] = []
    _table(schema, data, (), found)
    return found


def _table(
    schema: dict[str, Any],
    data: dict[str, Any],
    parent: tuple[str, ...],
    found: list[str],
) -> None:
    from livery.workshop._contract_keys import shown

    properties = cast("dict[str, dict[str, Any]]", schema.get("properties", {}))
    named = schema.get("additionalProperties", False)
    for key, value in data.items():
        path = (*parent, key)
        child = properties.get(key)
        if child is None and isinstance(named, dict):
            child = cast("dict[str, Any]", named)
        if child is None:
            found.append(_unknown(key, parent, sorted(properties)))
            continue
        owner = child.get(OWNER)
        if owner is not None:
            found.append(
                f"{shown(path)} is a key of {owner}, which {child.get(UNLISTED_BY)};"
                " list the extension, or remove the key"
            )
            continue
        _value(child, value, path, found)


def _unknown(key: str, parent: tuple[str, ...], known: list[str]) -> str:
    from livery.workshop._contract_keys import shown

    named = shown(tuple(part.removesuffix("[]") for part in parent))
    table = f"[{named}]" if parent else "the top level"
    near = difflib.get_close_matches(key, known, n=1)
    hint = f"; did you mean {near[0]!r}?" if near else ""
    takes = ", ".join(known) if known else "no keys"
    return f"{table} has no key {key!r}: it takes {takes}{hint}"


def _value(
    schema: dict[str, Any], value: object, path: tuple[str, ...], found: list[str]
) -> None:
    from livery.workshop._contract_keys import shown

    branches = cast("list[dict[str, Any]]", schema.get("anyOf", [schema]))
    named = shown(path)
    fitting = [branch for branch in branches if _fits(branch, value)]
    if not fitting:
        actual = _type_of(value)
        found.append(
            f"{named} is {_ACTUAL.get(actual, actual)} ({value!r});"
            f" it takes {' or '.join(_spoken(branch) for branch in branches)}"
        )
        return
    if isinstance(value, str):
        allowed = next((branch["enum"] for branch in fitting if "enum" in branch), None)
        if allowed is not None and value not in allowed:
            near = difflib.get_close_matches(value, allowed, n=1)
            hint = f"; did you mean {near[0]!r}?" if near else ""
            found.append(
                f"{named} is {value!r}; it takes one of {', '.join(allowed)}{hint}"
            )
            return
    if any(_takes_any(branch) for branch in fitting):
        return
    if isinstance(value, dict):
        table = next(branch for branch in fitting if branch.get("type") == "object")
        _table(table, cast("dict[str, Any]", value), path, found)
    elif isinstance(value, list):
        array = next(branch for branch in fitting if branch.get("type") == "array")
        entries = cast("dict[str, Any]", array.get("items", {}))
        if _is_strings(array) or _takes_any(entries):
            return
        listed_at = (*path[:-1], f"{path[-1]}[]")
        for item in cast("list[object]", value):
            _value(entries, item, listed_at, found)


def _is_strings(branch: dict[str, Any]) -> bool:
    """Whether *branch* is a list of strings, its doc set aside."""
    return {key: value for key, value in branch.items() if key != "description"} == (
        _STRINGS
    )


def _takes_any(schema: dict[str, Any]) -> bool:
    """Whether *schema* takes every value: no type, no choices, nothing beneath."""
    return not any(key in schema for key in ("type", "anyOf", "enum", "not"))


def _fits(branch: dict[str, Any], value: object) -> bool:
    """Whether *value* has the type *branch* takes; a list of strings, whole."""
    kind = branch.get("type")
    if kind is None:
        return _takes_any(branch)
    if kind == "string":
        return isinstance(value, str)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "object":
        return isinstance(value, dict)
    if kind == "array":
        if not isinstance(value, list):
            return False
        if _is_strings(branch):
            return all(isinstance(item, str) for item in cast("list[object]", value))
        return True
    return False


def _type_of(value: object) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "table"
    return type(value).__name__


#: How a refusal names the type a value has.
_ACTUAL = {
    "str": "a string",
    "int": "an integer",
    "number": "a number",
    "bool": "a boolean",
    "list": "a list",
    "table": "a table",
}

#: How a refusal names what a key takes, by JSON Schema type.
_WORDS = {
    "string": "a string",
    "integer": "an integer",
    "number": "a number",
    "boolean": "true or false",
    "array": "a list",
    "object": "a table",
}


def _spoken(branch: dict[str, Any]) -> str:
    """What *branch* takes, as a refusal says it."""
    if _is_strings(branch):
        return "a list of strings"
    kind = branch.get("type")
    return _WORDS.get(str(kind), "any value") if kind is not None else "any value"
