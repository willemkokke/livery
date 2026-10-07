"""Which keys a contract may hold, who owns each, and the judge that refuses the rest.

Every key of a ``workshop.toml`` is declared by its owner: the base
declares its own here, and an extension declares the keys it reads
under ``[contract.<contract>.<table>]`` in its ``extension.toml``,
read without importing the extension. The base also declares the keys
of ``extension.toml`` itself, the ``extension`` contract.

[livery.workshop._contract.load_contract][] judges every contract it
reads against the JSON Schema the declarations compose
([livery.workshop._schema.composed][]): a key no owner declares, a key
whose owner the root's ``[workspace] extensions`` does not list, a value
of the wrong type and a value outside its allowed set each refuse,
naming the file, the key, what the table takes, and the nearest match.

A path is dotted. ``*`` stands for any one name in a table whose
keys are the user's (``tools.modes.*``); ``[]`` stands for the
entries of a list (``ci.schedule[].every``). A key the user names may
hold a dot itself (``".vscode/settings.json"``): the judge walks a
contract's keys one table at a time, so such a key is one name.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
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


def _root(path: str, *types: Type, values: tuple[str, ...] = ()) -> Declared:
    return Declared("root", path, types, values)


def _package(path: str, *types: Type, values: tuple[str, ...] = ()) -> Declared:
    return Declared("package", path, types, values)


def _extension(path: str, *types: Type, values: tuple[str, ...] = ()) -> Declared:
    return Declared("extension", path, types, values)


def _base() -> tuple[Declared, ...]:
    """The base's keys: this module's, and those declared beside their readers."""
    from livery.workshop import _identity, _lfs, _points, _registries, _tools

    return (
        DECLARED
        + EXTENSION
        + _identity.DECLARED
        + _lfs.DECLARED
        + _registries.DECLARED
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
    _package("docs.api", "bool"),
    _package("docs.python-paths", "strs"),
)


def _input_keys(owner: str) -> tuple[Declared, ...]:
    """The keys of the ``inputs`` table under *owner*: the files a check or a job reads.

    The affected engine asks them: what is read, whether each changed
    file is judged on its own, what widens to everything, and what
    never counts.
    """
    return (
        _extension(f"{owner}.inputs", "table"),
        _extension(f"{owner}.inputs.reads", "strs"),
        _extension(f"{owner}.inputs.per-file", "bool"),
        _extension(f"{owner}.inputs.widens", "strs"),
        _extension(f"{owner}.inputs.on-removal", "bool"),
        _extension(f"{owner}.inputs.widen", "str"),
        _extension(f"{owner}.inputs.ignores", "strs"),
    )


def _check_keys(prefix: str) -> tuple[Declared, ...]:
    """The keys of the checks an extension declares under *prefix*.

    A check sits at ``<prefix>.<tool>.<role>``, the address a contract
    configures it by.
    """
    check = f"{prefix}.*.*"
    return (
        _extension(prefix, "table"),
        _extension(f"{prefix}.*", "table"),
        _extension(check, "table"),
        _extension(f"{check}.run", "str"),
        _extension(f"{check}.fix", "str"),
        _extension(f"{check}.scope", "str", values=("workspace", "package")),
        _extension(f"{check}.narrowing", "str", values=("paths", "packages", "none")),
        _extension(f"{check}.transport", "str", values=("argv", "file", "config")),
        _extension(f"{check}.threshold", "number"),
        _extension(f"{check}.kinds", "strs"),
        _extension(f"{check}.tests-only", "bool"),
        _extension(f"{check}.after", "strs"),
        _extension(f"{check}.tools", "strs"),
        _extension(f"{check}.arguments", "bool"),
        _extension(f"{check}.flags", "strs"),
        _extension(f"{check}.roles", "strs"),
        _extension(f"{check}.listed-with", "str"),
        _extension(f"{check}.editor-extension", "str"),
        _extension(f"{check}.claims", "list"),
        _extension(f"{check}.claims[]", "table"),
        _extension(f"{check}.claims[].category", "str"),
        _extension(f"{check}.claims[].suffixes", "strs"),
        _extension(f"{check}.claims[].ignore", "strs"),
        *_input_keys(check),
        _extension(f"{check}.options", "table"),
        _extension(f"{check}.options.*", "table"),
        _extension(f"{check}.options.*.type", "str", values=("bool", "str", "int")),
        _extension(f"{check}.options.*.default", "any"),
        _extension(f"{check}.options.*.doc", "str"),
        # A project file's fragment is its text; a package file's is
        # its text and the kinds whose packages render it.
        _extension(f"{check}.fragments", "table"),
        _extension(f"{check}.fragments.*", "str", "table"),
        _extension(f"{check}.fragments.*.kinds", "strs"),
        _extension(f"{check}.fragments.*.text", "str"),
    )


def _job_keys(prefix: str) -> tuple[Declared, ...]:
    """The keys of the CI jobs an extension declares under *prefix*.

    A job sits at ``<prefix>.jobs.<point>.<name>``, the address a
    contract's ``[ci]`` table has. It names its tasks, whether the
    point's verdict waits for it, the jobs it waits for, its checkout
    depth, the run's own token, the functions that name what it
    installs and where it deploys, the files it reads, and the comment
    above it. A grant beyond the run's own token is the root contract's
    to give, so a job takes no other.
    """
    job = f"{prefix}.jobs.*.*"
    return (
        _extension(prefix, "table"),
        _extension(f"{prefix}.jobs", "table"),
        _extension(f"{prefix}.jobs.*", "table"),
        _extension(job, "table"),
        _extension(f"{job}.entries", "strs"),
        _extension(f"{job}.gates", "bool"),
        _extension(f"{job}.needs", "strs"),
        _extension(f"{job}.fetch", "str", values=("full", "tags", "2")),
        _extension(f"{job}.token", "str", values=("job",)),
        _extension(f"{job}.installs", "str"),
        _extension(f"{job}.deploy", "str"),
        *_input_keys(job),
        _extension(f"{job}.note", "str"),
    )


#: The keys of an extension's ``extension.toml``: what it is, the tools
#: its verbs need, the options a listing may turn on, its checks, its CI
#: jobs, the values it puts into slots, the contract keys it owns, and
#: what it adds to another extension while that one is listed.
EXTENSION: tuple[Declared, ...] = (
    _extension("extension", "table"),
    _extension("extension.api-version", "int"),
    _extension("extension.levels", "list"),
    _extension("extension.levels[]", "str", values=("workspace", "package")),
    _extension("extension.plugin", "str"),
    _extension("extension.requires", "strs"),
    _extension("toolroom", "table"),
    _extension("toolroom.requires", "strs"),
    _extension("options", "table"),
    _extension("options.*", "str"),
    *_check_keys("checks"),
    *_job_keys("ci"),
    _extension("contributions", "table"),
    _extension("contributions.*", "strs"),
    # The slots the extension declares, which others put values into.
    _extension("slots", "table"),
    _extension("slots.*", "table"),
    _extension("slots.*.compose", "str"),
    _extension("slots.*.default", "any"),
    _extension("slots.*.values", "list"),
    # A key's declaration is judged where it is read: the reader knows
    # the contracts and the types.
    _extension("contract", "table"),
    _extension("contract.*", "table"),
    _extension("contract.*.*", "any"),
    _extension("for", "table"),
    _extension("for.*", "table"),
    *_check_keys("for.*.checks"),
    *_job_keys("for.*.ci"),
    _extension("for.*.contributions", "table"),
    _extension("for.*.contributions.*", "strs"),
    # An earlier extension's shipped file, ``<owner>:<name>``, replaced
    # by this one's file of the same name or deleted, to the reason.
    _extension("replaces", "table"),
    _extension("replaces.*", "str"),
    _extension("deletes", "table"),
    _extension("deletes.*", "str"),
)


@dataclass(frozen=True)
class _Owned:
    declared: Declared
    owner: str


@functools.cache
def declarations() -> dict[tuple[ContractKind, str], _Owned]:
    """Every installed owner's declarations, by contract and path.

    The base's come from this module and the readers it names; every
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

    take(BASE, _base())
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
