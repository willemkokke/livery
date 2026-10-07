"""Which keys a contract may hold, who owns each, and the judge that refuses the rest.

Every key of a ``workshop.toml`` is declared by its owner: the base
declares its own here, and an extension declares the keys it reads
as ``CONTRACT_KEYS`` in the data module its ``workshop.extensions``
entry point names. Loading a declaration imports that module alone,
never the extension's tasks.

[livery.workshop._contract.load_contract][] judges every contract it
reads against the declarations: a key no owner declares, a key whose
owner the root's ``[workspace] extensions`` does not list, a value of the
wrong type and a value outside its allowed set each refuse, naming
the file, the key, what the table takes, and the nearest match.

A path is dotted. ``*`` stands for any one name in a table whose
keys are the user's (``tools.modes.*``); ``[]`` stands for the
entries of a list (``ci.schedule[].every``).
"""

from __future__ import annotations

import difflib
import functools
from dataclasses import dataclass
from typing import Any, Literal

from livery.footman import fail

#: The two kinds of contract: the workspace's at the root, a package's
#: in its directory.
ContractKind = Literal["root", "package"]

#: The value types a key may take. ``strs`` is a list of strings;
#: ``list`` is a list whose entries the ``[]`` path declares; ``table``
#: is a table whose keys are declared beneath it; ``any`` is a value
#: the reader judges itself, nothing beneath it declared here.
Type = Literal["str", "int", "number", "bool", "strs", "list", "table", "any"]

#: The base extension's name, always mounted.
BASE = "livery.workshop"


@dataclass(frozen=True)
class Declared:
    """One key a contract may hold.

    Attributes:
        contract: The contract it belongs in, ``root`` or ``package``.
        path: The dotted path, with ``*`` and ``[]`` as above.
        types: The value types it takes, any one of them.
        values: The values it takes, when it takes only some.
    """

    contract: ContractKind
    path: str
    types: tuple[Type, ...]
    values: tuple[str, ...] = ()


def _root(path: str, *types: Type, values: tuple[str, ...] = ()) -> Declared:
    return Declared("root", path, types, values)


def _package(path: str, *types: Type, values: tuple[str, ...] = ()) -> Declared:
    return Declared("package", path, types, values)


def _base() -> tuple[Declared, ...]:
    """The base's keys: this module's, and those declared beside their readers."""
    from livery.workshop import (
        _docs_contract,
        _identity,
        _lfs,
        _points,
        _registries,
        _tools,
    )

    return (
        DECLARED
        + _identity.DECLARED
        + _lfs.DECLARED
        + _registries.DECLARED
        + _docs_contract.DECLARED
        + _tools.declared_keys()
        + _points.contract_keys()
    )


#: The base extension's keys, but for those declared beside their readers.
DECLARED: tuple[Declared, ...] = (
    # The workspace.
    _root("workspace", "table"),
    _root("workspace.extensions", "list"),
    _root("workspace.extensions[]", "str", "table"),
    _root("workspace.extensions[].name", "str"),
    _root("workspace.extensions[].for", "strs"),
    _root("forge", "table"),
    _root("forge.kind", "str", values=("github", "gitea", "gitlab")),
    _root("forge.owner", "str"),
    _root("forge.url", "str"),
    _root("issues", "table"),
    _root("issues.assignees", "int"),
    # Review.
    _root("owners", "table"),
    _root("owners.users", "strs"),
    _root("owners.teams", "strs"),
    _root("owners.approvals", "int"),
    # CI.
    _root("ci", "table"),
    _root("ci.runners", "strs"),
    _root("ci.required-context", "str"),
    _root("ci.affected-legs", "bool"),
    _root("ci.speed-marks", "bool"),
    _root("ci.python-versions", "strs"),
    _root("ci.windows-temp", "str", values=("runner", "system")),
    _root("ci.automerge", "bool"),
    _root("ci.profile", "bool"),
    _root("ci.profile-window", "int"),
    _root("ci.profile-keep", "int"),
    _root("ci.profile-into", "str"),
    # The release train: whether the floor leg proves every floor.
    _root("release", "table"),
    _root("release.prove-floors", "bool"),
    # The site's address, which the rendered project files carry; where
    # it publishes is declared beside its reader.
    _root("docs", "table"),
    _root("docs.site-url", "str"),
    # A package.
    _package("kind", "str"),
    _package("extensions", "strs"),
    _package("name", "str"),
    _package("depends", "list"),
    _package("depends[]", "table"),
    _package("depends[].path", "str"),
    _package("depends[].kind", "str", values=("build", "runtime", "test", "tool")),
    _package("depends[].floor", "str"),
    _package("release", "table"),
    _package("release.publish", "bool"),
    _package("release.baseline", "str"),
    _package("release.prove-floors", "bool"),
    _package("categories", "table"),
    _package("categories.*", "strs"),
    # A check's options, [checks.<tool>], [checks.<tool>.<role>] and
    # [roles.<role>]: the check registry judges which tools, roles and
    # options exist.
    _package("checks", "table"),
    _package("checks.*", "table"),
    _package("checks.*.*", "any"),
    _package("checks.*.*.*", "any"),
    _package("roles", "table"),
    _package("roles.*", "table"),
    _package("roles.*.*", "any"),
    _package("qa", "table"),
    _package("qa.coverage-floor", "number", "str"),
    _package("qa.coverage-epsilon", "number"),
    _package("owners", "table"),
    _package("owners.users", "strs"),
    _package("owners.teams", "strs"),
    _package("owners.approvals", "int"),
    _package("ci", "table"),
    _package("ci.wheel-platforms", "strs"),
    _package("docs", "table"),
    _package("docs.generators", "list"),
    _package("docs.generators[]", "str", "table"),
    _package("docs.generators[].verb", "str"),
    _package("docs.generators[].requires", "strs"),
    _package("docs.api", "bool"),
    _package("docs.python-paths", "strs"),
)


@dataclass(frozen=True)
class _Owned:
    declared: Declared
    owner: str


@functools.cache
def declarations() -> dict[tuple[ContractKind, str], _Owned]:
    """Every installed owner's declarations, by contract and path.

    The base's come from this module and the readers it names; every
    extension's from the ``CONTRACT_KEYS`` of its declaration, installed
    or not listed alike, so a key of an unlisted extension is named as
    that extension's.
    Two owners declaring one path refuse, naming both.
    """
    from livery.footman import installed_entry_points

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

    take(BASE, _base())
    for entry in installed_entry_points("workshop.extensions"):
        loaded: Any = entry.load()
        take(entry.name, tuple(getattr(loaded, "CONTRACT_KEYS", ())))
    return found


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


def _fits(value: object, types: tuple[Type, ...]) -> bool:
    actual = _type_of(value)
    for wanted in types:
        if wanted == "any" or wanted == actual:
            return True
        if wanted == "number" and actual == "int":
            return True
        if wanted == "strs" and isinstance(value, list):
            return all(isinstance(item, str) for item in value)  # pyright: ignore[reportUnknownVariableType]
    return False


#: How a refusal names the type a value has.
_ACTUAL = {
    "str": "a string",
    "int": "an integer",
    "number": "a number",
    "bool": "a boolean",
    "list": "a list",
    "table": "a table",
}


def _spoken(types: tuple[str, ...]) -> str:
    words: dict[str, str] = {
        "str": "a string",
        "int": "an integer",
        "number": "a number",
        "bool": "true or false",
        "strs": "a list of strings",
        "list": "a list",
        "table": "a table",
        "any": "any value",
    }
    return " or ".join(words.get(name, name) for name in types)


class _Judge:
    def __init__(
        self,
        contract: ContractKind,
        where: str,
        listed: frozenset[str] | None,
    ) -> None:
        self.contract: ContractKind = contract
        self.where = where
        self.listed = listed
        self.known = declarations()
        self.problems: list[str] = []

    def _find(self, path: str) -> _Owned | None:
        """The declaration *path* falls under: its own, else the most literal match."""
        found = self.known.get((self.contract, path))
        if found is not None:
            return found
        wanted = path.split(".")
        best: tuple[int, _Owned] | None = None
        for (contract, declared), owned in self.known.items():
            parts = declared.split(".")
            if contract != self.contract or len(parts) != len(wanted):
                continue
            if all(p in ("*", w) for p, w in zip(parts, wanted, strict=True)):
                literal = sum(p != "*" for p in parts)
                if best is None or literal > best[0]:
                    best = (literal, owned)
        return best[1] if best else None

    def _siblings(self, parent: str) -> list[str]:
        prefix = f"{parent}." if parent else ""
        names: set[str] = set()
        for contract, path in self.known:
            if contract != self.contract or not path.startswith(prefix):
                continue
            rest = path[len(prefix) :]
            name = rest.split(".")[0].removesuffix("[]")
            if name and name != "*":
                names.add(name)
        return sorted(names)

    def table(self, data: dict[str, Any], parent: str) -> None:
        for key, value in data.items():
            path = f"{parent}.{key}" if parent else key
            owned = self._find(path)
            if owned is None:
                self._unknown(key, parent)
                continue
            if self.listed is not None and owned.owner not in self.listed:
                self.problems.append(
                    f"{path} is a key of {owned.owner}, which [workspace] extensions"
                    " does not list; list the extension, or remove the key"
                )
                continue
            self.value(value, path, owned.declared)

    def value(self, value: object, path: str, declared: Declared) -> None:
        if not _fits(value, declared.types):
            self.problems.append(
                f"{path} is {_ACTUAL.get(_type_of(value), _type_of(value))}"
                f" ({value!r}); it takes {_spoken(declared.types)}"
            )
            return
        if declared.values and isinstance(value, str) and value not in declared.values:
            near = difflib.get_close_matches(value, declared.values, n=1)
            hint = f"; did you mean {near[0]!r}?" if near else ""
            self.problems.append(
                f"{path} is {value!r}; it takes one of"
                f" {', '.join(declared.values)}{hint}"
            )
            return
        if "any" in declared.types:
            return
        if isinstance(value, dict):
            self.table(value, path)  # pyright: ignore[reportUnknownArgumentType]
        elif isinstance(value, list) and "list" in declared.types:
            entry = self.known.get((self.contract, f"{path}[]"))
            if entry is None:
                return
            for item in value:  # pyright: ignore[reportUnknownVariableType]
                self.value(item, f"{path}[]", entry.declared)

    def _unknown(self, key: str, parent: str) -> None:
        table = f"[{parent.replace('[]', '')}]" if parent else "the top level"
        known = self._siblings(parent)
        near = difflib.get_close_matches(key, known, n=1)
        hint = f"; did you mean {near[0]!r}?" if near else ""
        takes = ", ".join(known) if known else "no keys"
        self.problems.append(f"{table} has no key {key!r}: it takes {takes}{hint}")


def judge(
    data: dict[str, Any],
    *,
    contract: ContractKind,
    where: str,
    listed: frozenset[str] | None,
) -> list[str]:
    """The refusals *data* earns as a *contract*; empty when it is sound.

    Args:
        data: The parsed contract.
        contract: Which contract it is.
        where: The file, for the caller's message.
        listed: The extensions the root lists, the base among them;
            ``None`` takes every installed owner as listed.

    Returns:
        One line per problem, in the contract's order.
    """
    judged = _Judge(contract, where, listed)
    judged.table(data, "")
    return judged.problems


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
