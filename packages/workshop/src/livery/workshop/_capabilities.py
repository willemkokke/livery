"""What each extension of the installed set provides, read from its declaration.

The verbs fragment, ``verbs.extensions.md``, gives each extension of
the installed set a block of its own, in composition order: what the
extension does, its verbs with the first line of each one's help, the
templates ``fm new.package`` creates a package from, the contract keys
it reads and the step options a package may set, each with its doc and
where it is written, the steps it declares off with the switch that
turns each on, and the installed extensions it contributes to and
those its root entry leaves out with ``without``. Every line is data
the extension's ``extension.toml`` and its plugin's docstrings hold
already, so a workspace that lists an extension needs no other step
for a reader to learn it.

`verbs_by_provider` reads the verbs from a composed task tree, `blocks`
reads every block of a workspace, and `render_block` writes one for a
reader.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import livery.footman as footman
from livery.workshop._prose import HUMAN, REPOSITORY

if TYPE_CHECKING:
    from livery.workshop._declaration import Declaration
    from livery.workshop._steps import StepRecord

#: The kinds of line a block holds, in the order a block lists them.
VERB = "verb"
TEMPLATE = "template"
KEY = "key"
OPTION = "option"
SWITCH = "switch"


@dataclass(frozen=True)
class Verb:
    """One verb of a composed task tree.

    Attributes:
        address: How it is run: ``docs.build`` for ``fm docs.build``.
        help: The first line of its help; empty for none.
    """

    address: str
    help: str = ""


@dataclass(frozen=True)
class Item:
    """One line of a block: a verb, a template, a key, an option or a switch.

    Attributes:
        kind: What it is: ``verb``, ``template``, ``key``, ``option`` or
            ``switch``.
        name: How a reader writes it: ``docs.build``, ``python-library``,
            ``[coverage] floor``, ``[phases.test.pytest] parallel``.
        where: The contract it is written in, ``root`` or ``package``;
            empty for a verb and a template.
        detail: An option's type and default, ``bool, default true``;
            empty for every other kind.
        doc: Its doc line: a verb's first line of help, the doc of a
            template, a key or an option, what a switch does.
    """

    kind: str
    name: str
    where: str = ""
    detail: str = ""
    doc: str = ""


@dataclass(frozen=True)
class Block:
    """What one extension of the installed set provides.

    Attributes:
        extension: The name it is listed by.
        description: What it does, from ``[extension] description``.
        installed: Whether an installed distribution declares it; a
            listed extension the sync has not installed yet is named
            alone.
        items: Its verbs, templates, keys, options and switches, in
            that order.
        contributes: The installed extensions its ``[for.<target>]``
            tables apply to, in composition order.
        leaves_out: The targets its root entry names in ``without``.
    """

    extension: str
    description: str = ""
    installed: bool = True
    items: tuple[Item, ...] = ()
    contributes: tuple[str, ...] = ()
    leaves_out: tuple[str, ...] = ()


def verbs_by_provider(tree: Mapping[str, Any]) -> dict[str, list[Verb]]:
    """Every visible verb of *tree* by the plugin that mounted it.

    *tree* is a composed task tree as ``--json --list`` reports it. A
    group's ``default`` task is the group's own address, the way it is
    run. A task the repository's own tasks file defines comes under the
    empty provider. Hidden tasks and the tasks of hidden groups are left
    out.
    """
    found: dict[str, list[Verb]] = {}
    _collect(tree, "", REPOSITORY, found)
    return found


def _collect(
    node: Mapping[str, Any],
    prefix: str,
    inherited: str,
    found: dict[str, list[Verb]],
) -> None:
    """Every visible task under *node*, by provider, its address carrying *prefix*."""
    tasks = node.get("tasks")
    if isinstance(tasks, dict):
        for name, task in cast("dict[str, Any]", tasks).items():
            if not isinstance(task, dict):
                continue
            spec = cast("dict[str, Any]", task)
            if spec.get("hidden") is True:
                continue
            provider = str(spec.get("mounted_from") or inherited)
            address = prefix[:-1] if name == "default" and prefix else f"{prefix}{name}"
            lines = str(spec.get("help") or "").strip().splitlines()
            found.setdefault(provider, []).append(
                Verb(address, lines[0] if lines else "")
            )
    groups = node.get("groups")
    if isinstance(groups, dict):
        for name, sub in cast("dict[str, Any]", groups).items():
            if not isinstance(sub, dict):
                continue
            group = cast("dict[str, Any]", sub)
            if group.get("hidden") is True:
                continue
            provider = str(group.get("mounted_from") or inherited)
            _collect(group, f"{prefix}{name}.", provider, found)


def blocks(
    root: Path, verbs: Mapping[str, Sequence[Verb]]
) -> tuple[list[Block], dict[str, list[Verb]]]:
    """Each extension of *root*'s installed set as a block, and the verbs left over.

    *verbs* are the composed tree's, by provider
    (`verbs_by_provider`). An extension's block takes every verb its
    plugin mounts, and each verb the base generates for a step the
    extension declares: ``lint.ruff`` is ruff's. The verbs no block
    takes come back by provider, the base's and those of the plugins
    no extension of the set carries, a provider with none left out.
    The blocks come in composition order.
    """
    from livery.workshop._extensions import (
        SELF,
        installed_declaration,
        installed_set,
        left_out,
        resolved_targets,
    )

    names = installed_set(root)
    targets = resolved_targets(root)
    off = left_out(root)
    declared = {name: installed_declaration(name) for name in names}
    steps = {
        name: _active_steps(found, targets.get(name, ()))
        for name, found in declared.items()
        if found is not None
    }
    owners = _step_owners(steps)
    rest = {provider: list(found) for provider, found in verbs.items()}
    taken: dict[str, list[Verb]] = {}
    kept: list[Verb] = []
    for verb in rest.get(SELF, []):
        owner = owners.get(verb.address)
        if owner is None:
            kept.append(verb)
        else:
            taken.setdefault(owner, []).append(verb)
    if SELF in rest:
        rest[SELF] = kept
    found_blocks: list[Block] = []
    for name in names:
        found = declared[name]
        contributes = tuple(
            target for target in targets.get(name, ()) if target != name
        )
        leaves = off.get(name, ())
        if found is None:
            found_blocks.append(
                Block(name, "", False, (), contributes=contributes, leaves_out=leaves)
            )
            continue
        own = [*taken.get(name, ())]
        if found.plugin:
            own += rest.pop(found.plugin, [])
        items = [
            Item(VERB, verb.address, doc=verb.help)
            for verb in sorted(own, key=lambda verb: verb.address)
        ]
        items += _items(found, steps[name])
        found_blocks.append(
            Block(
                name,
                found.description,
                True,
                tuple(items),
                contributes=contributes,
                leaves_out=leaves,
            )
        )
    return found_blocks, {provider: found for provider, found in rest.items() if found}


def _active_steps(found: Declaration, targets: Sequence[str]) -> tuple[StepRecord, ...]:
    """The steps *found* declares at the top level and in its tables for *targets*."""
    records = [record for held in found.phases.values() for record in held]
    for target in targets:
        additions = found.targets.get(target)
        if additions is not None:
            records += additions.steps
    return tuple(records)


def _step_owners(steps: Mapping[str, Sequence[StepRecord]]) -> dict[str, str]:
    """Each step address to the extension that declares it.

    The first in composition order, unless a later one's step replaces
    the step at that address, ``<owner>:<phase>.<step>`` in its
    ``replaces``.
    """
    owners: dict[str, str] = {}
    for name, records in steps.items():
        for record in records:
            replaces = any(
                ref.partition(":")[2] == record.name for ref, _why in record.replaces
            )
            if replaces or record.name not in owners:
                owners[record.name] = name
    return owners


def _items(found: Declaration, steps: Sequence[StepRecord]) -> list[Item]:
    """*found*'s templates, contract keys, step options and switches, in that order."""
    from livery.workshop._declaration import contract_keys, template_declarations

    items = [
        Item(TEMPLATE, template.name, doc=template.doc)
        for template in template_declarations(found.extension, found.package)
    ]
    items += [
        Item(KEY, _key_name(key.path), key.contract, doc=key.doc)
        for key in contract_keys(found.package)
    ]
    for record in steps:
        items += [
            Item(
                OPTION,
                f"[phases.{record.phase}.{record.step}] {option.name}",
                "package",
                f"{option.kind}, default {json.dumps(option.default)}",
                option.doc,
            )
            for option in record.options
        ]
    items += [
        Item(
            SWITCH,
            f"[phases.{record.phase}.{record.step}] enabled = true",
            "root",
            doc="turns on the step, which the extension declares off",
        )
        for record in steps
        if not record.enabled
    ]
    return items


def _key_name(path: str) -> str:
    """A contract key's dotted *path* as a reader finds it: ``[coverage] floor``."""
    table, _, rest = path.partition(".")
    return f"[{table}] {rest}" if rest else f"[{table}]"


def render_block(block: Block, audience: str | None) -> list[str]:
    """*block*'s markdown lines for *audience*, ending in an empty line.

    A heading naming the extension and its description, then one line
    per item: a list for the agent, a table for a reader of the site.
    The extensions it contributes to and those its root entry leaves out
    close it.
    """
    prog = footman.prog()
    lines = [f"## {block.extension}", ""]
    if not block.installed:
        lines += [
            f"No installed distribution declares it yet; `{prog} sync` installs it.",
            "",
        ]
    elif block.description:
        lines += [block.description, ""]
    if audience == HUMAN:
        if block.items:
            lines += ["| item | kind | what it does |", "| --- | --- | --- |"]
            lines += [
                f"| `{_written(item, prog)}` | {_kind(item)} | {_cell(item.doc)} |"
                for item in block.items
            ]
            lines.append("")
        if block.contributes:
            lines += [f"Contributes to {_spoken(block.contributes)}.", ""]
        if block.leaves_out:
            lines += [
                f"Its root entry leaves out {_spoken(block.leaves_out)} with"
                " `without`.",
                "",
            ]
        return lines
    listed = [f"- {_agent_line(item, prog)}" for item in block.items]
    if block.contributes:
        listed.append(f"- contributes to {', '.join(block.contributes)}")
    if block.leaves_out:
        listed.append(
            f"- leaves out {', '.join(block.leaves_out)}, named in `without` in"
            " its root entry"
        )
    return [*lines, *listed, ""] if listed else lines


def _written(item: Item, prog: str) -> str:
    """How a reader writes *item*: a verb with the runner's name before it."""
    return f"{prog} {item.name}" if item.kind == VERB else item.name


def _agent_line(item: Item, prog: str) -> str:
    """*item* as one line of the agent's list, without its bullet."""
    doc = f": {item.doc}" if item.doc else ""
    if item.kind == VERB:
        return f"`{prog} {item.name}`{doc}"
    if item.kind == TEMPLATE:
        return f"template `{item.name}`, `{prog} new.package <name> {item.name}`{doc}"
    where = "the root contract" if item.where == "root" else "a package contract"
    detail = f", {item.detail}" if item.detail else ""
    return f"`{item.name}` in {where}{detail}{doc}"


def _kind(item: Item) -> str:
    """*item*'s kind as a table cell for a reader of the site."""
    if item.kind in (VERB, TEMPLATE):
        return item.kind
    if item.kind == OPTION:
        return f"package step option, {item.detail}"
    noun = "switch" if item.kind == SWITCH else "key"
    return f"{item.where} contract {noun}"


def _cell(text: str) -> str:
    """*text* safe in a table cell: a pipe would end the cell."""
    return text.replace("|", "\\|")


def _spoken(names: Sequence[str]) -> str:
    """*names* as a sentence lists them: ``a``, ``a and b``, ``a, b and c``."""
    if len(names) < 2:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"
