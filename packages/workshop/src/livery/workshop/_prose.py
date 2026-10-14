"""Prose fragments: what the extensions say to the agent and to a reader, in sections.

An extension's prose, its voice, its standards, its rules, is a set of
fragments, and one set writes the agent's entry file and the site's
development pages. A fragment is a markdown file in the extension's
``content/fragments/`` named ``<section>.<topic>[.<audience>].md``, or
a registration with `register_fragment` and a render that takes the
audience. The base defines the sections and their order;
`register_section` adds one at a declared position. The audience is
``agent`` or ``human``, and a name without one serves both; a render
answers each audience itself, and an empty answer leaves the fragment
out for that reader. A topic is never named ``agent`` or ``human``: the
last part of a name spells the audience.

``fm sync`` delivers the agent's set flat under ``.workshop/fragments/``
with the audience dropped from the name: a shipped fragment byte for
byte its source, a rendered one under a header naming its origin, each
through the fragment engine so an edited copy is kept and named. The
repository's own fragments live in ``fragments/`` at the root under the
same names, are never copied, and come last. Reach for
[livery.workshop._prose.fragments][] for the set a reader gets and
[livery.workshop._shipped_files][] for the delivery.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import livery.footman as footman

BASE_EXTENSION = "livery.workshop"
AGENT = "agent"
HUMAN = "human"
AUDIENCES = (AGENT, HUMAN)

#: The base's sections, in the order the entry file and the site read them.
BASE_SECTIONS = (
    "identity",
    "voice",
    "standards",
    "rules",
    "workflow",
    "gate",
    "verbs",
    "compositions",
    "tools",
)

#: The fragments directory: an extension's under its content, the repository's
#: own at the root.
OWN = "fragments"

#: The extension name of the repository's own fragments.
REPOSITORY = ""

#: A render: the workspace root and the audience, ``agent``, ``human``
#: or None for no particular reader, to the markdown; empty leaves the
#: fragment out for that reader.
Render = Callable[[Path, "str | None"], str]


class ProseError(ValueError):
    """A fragment or a section the registry refuses; the message names the files."""


@dataclass(frozen=True)
class Prose:
    """One prose fragment: a file an extension ships, or a render from the registries.

    Attributes:
        section: The section it belongs to, one the registry knows.
        topic: What it is about; with the section, its name.
        audience: ``agent`` or ``human`` for a file that serves one
            reader, empty for both; a rendered fragment is empty here
            and answers per audience.
        extension: The extension that ships or registers it; empty for the
            repository's own.
        source: The shipped file, or None for a rendered fragment.
        render: The render, or None for a shipped fragment.
    """

    section: str
    topic: str
    audience: str = ""
    extension: str = BASE_EXTENSION
    source: Path | None = None
    render: Render | None = None

    @property
    def name(self) -> str:
        """The delivered file name, ``<section>.<topic>.md``."""
        return f"{self.section}.{self.topic}.md"

    @property
    def origin(self) -> str:
        """Where it comes from, for a refusal: the posix file, or the extension."""
        if self.source is not None:
            return f"{self.source.as_posix()} ({self.extension or 'this repository'})"
        return f"the render {self.extension} registered"

    def text(self, root: Path, audience: str | None) -> str:
        """The markdown for *audience*: the shipped text, or the render's answer."""
        if self.render is not None:
            return self.render(root, audience)
        if self.source is not None:
            return self.source.read_text(encoding="utf-8")
        return ""


_SECTIONS: list[str] = list(BASE_SECTIONS)
_SECTION_EXTENSIONS: dict[str, str] = dict.fromkeys(BASE_SECTIONS, BASE_EXTENSION)
#: The rendered fragments by delivered name.
_RENDERED: dict[str, Prose] = {}


def sections() -> tuple[str, ...]:
    """The sections in the order the entry file and the site read them."""
    return tuple(_SECTIONS)


def register_section(name: str, *, after: str, extension: str) -> None:
    """Add the section *name* right after *after*.

    Refuses a name an extension already registered, naming that extension, and
    an anchor no extension registered, naming the sections.
    """
    if name in _SECTION_EXTENSIONS:
        raise ProseError(
            f"{extension} registers section {name!r}, which"
            f" {_SECTION_EXTENSIONS[name]} already registered"
        )
    if after not in _SECTION_EXTENSIONS:
        raise ProseError(
            f"{extension} registers section {name!r} after {after!r}, which no"
            f" extension registered; the sections are {', '.join(_SECTIONS)}"
        )
    _SECTIONS.insert(_SECTIONS.index(after) + 1, name)
    _SECTION_EXTENSIONS[name] = extension


def unregister_section(name: str, *, by: str) -> None:
    """Drop the section *name*.

    Refuses the base's own, an unknown one, another extension's, and one a
    rendered fragment still belongs to.
    """
    owner = _SECTION_EXTENSIONS.get(name)
    if owner is None:
        raise ProseError(
            f"{by} withdraws section {name!r}, which no extension registered"
        )
    if name in BASE_SECTIONS:
        raise ProseError(f"{by} withdraws section {name!r}, which is the base's")
    if owner != by:
        raise ProseError(f"{by} withdraws section {name!r}, which {owner} registered")
    inside = [p.name for p in _RENDERED.values() if p.section == name]
    if inside:
        raise ProseError(
            f"{by} withdraws section {name!r} while {', '.join(inside)} still"
            " belongs to it"
        )
    _SECTIONS.remove(name)
    del _SECTION_EXTENSIONS[name]


def _check_section(where: str, section: str) -> None:
    if section not in _SECTION_EXTENSIONS:
        raise ProseError(
            f"{where}: section {section!r} is not one the registry knows; the"
            f" sections are {', '.join(_SECTIONS)}"
        )


def register_fragment(
    section: str,
    topic: str,
    render: Render,
    *,
    extension: str = BASE_EXTENSION,
) -> None:
    """Register a rendered fragment.

    The render takes the workspace root and the audience, ``agent``,
    ``human`` or None for no particular reader, and returns the
    markdown; an empty answer leaves the fragment out for that reader.
    Refuses an unknown section, a topic spelling an audience, and a name
    an extension already registered, naming that extension.
    """
    prose = Prose(section, topic, "", extension, render=render)
    where = f"{extension} registers fragment {prose.name}"
    _check_section(where, section)
    if topic in AUDIENCES:
        raise ProseError(f"{where}: a topic is never named {topic!r}, an audience is")
    existing = _RENDERED.get(prose.name)
    if existing is not None:
        raise ProseError(f"{where}, which {existing.extension} already registered")
    _RENDERED[prose.name] = prose


def unregister_fragment(name: str, *, by: str) -> None:
    """Drop the rendered fragment delivered as *name*.

    Refuses an unknown one, and one another extension registered.
    """
    prose = _RENDERED.get(name)
    if prose is None:
        raise ProseError(
            f"{by} withdraws fragment {name}, which no extension registered"
        )
    if prose.extension != by:
        raise ProseError(
            f"{by} withdraws fragment {name}, which {prose.extension} registered"
        )
    del _RENDERED[name]


def snapshot() -> tuple[list[str], dict[str, str], dict[str, Prose]]:
    """The registry's state, for [livery.workshop._prose.restore][] to put back."""
    return list(_SECTIONS), dict(_SECTION_EXTENSIONS), dict(_RENDERED)


def restore(state: tuple[list[str], dict[str, str], dict[str, Prose]]) -> None:
    """Put the registry back to *state*."""
    ordered, extensions, rendered = state
    _SECTIONS[:] = ordered
    _SECTION_EXTENSIONS.clear()
    _SECTION_EXTENSIONS.update(extensions)
    _RENDERED.clear()
    _RENDERED.update(rendered)


def parse_name(filename: str, *, where: str = "") -> tuple[str, str, str]:
    """Split a fragment file name into section, topic and audience.

    ``rules.tests.agent.md`` is the rules section, the topic tests, for
    the agent. A third part spells the audience, so a topic is never
    named ``agent`` or ``human``. Refuses a name outside the convention
    and an unknown section, naming *where*, the file by default.
    """
    where = where or filename
    stem, dot, suffix = filename.rpartition(".")
    parts = stem.split(".") if dot and suffix == "md" else []
    audience = ""
    if len(parts) > 2 and parts[-1] in AUDIENCES:
        audience = parts.pop()
    if len(parts) != 2 or not all(parts):
        raise ProseError(
            f"{where}: a fragment is named <section>.<topic>[.<audience>].md"
        )
    section, topic = parts
    _check_section(where, section)
    return section, topic, audience


def shipped(extension: str, content: Path) -> list[Prose]:
    """The fragments *extension* ships under ``content/fragments/``, in name order.

    Every file there is a fragment, so a name outside the convention
    refuses naming the file. The repository's own come from its root
    with the empty extension name.
    """
    directory = content / OWN
    if not directory.is_dir():
        return []
    found: list[Prose] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file():
            continue
        section, topic, audience = parse_name(path.name, where=path.as_posix())
        found.append(Prose(section, topic, audience, extension, source=path))
    return found


def repository_fragments(root: Path) -> list[Prose]:
    """The repository's own fragments under ``fragments/`` at *root*, in name order."""
    return shipped(REPOSITORY, root)


def in_play(root: Path, order: Iterable[str] | None = None) -> list[Prose]:
    """Every shipped fragment in play: each extension's set in *order*, then the own.

    *order* is the mounted extensions' order, the workspace's stack when
    absent. An extension that ships no content adds nothing.
    """
    from livery.workshop._extensions import extension_content, stack_names

    listed: list[Prose] = []
    for extension in stack_names(root) if order is None else order:
        content = extension_content(extension)
        if content is not None:
            listed += shipped(extension, content)
    return [*listed, *repository_fragments(root)]


def guidance(root: Path, audience: str) -> list[Prose]:
    """The guidance one reader gets in the workspace at *root*, in reading order.

    *audience* is [livery.workshop.AGENT][] or [livery.workshop.HUMAN][].
    The set is every fragment the mounted extensions ship, every
    fragment an extension renders, and the repository's own under
    ``fragments/``: by section, then by extension in mount order, the
    repository's own last, then by name. A fragment written for the
    other reader is left out.

    Raises:
        ValueError: when two fragments in the set deliver under one name,
            naming both.
    """
    return fragments(root, in_play(root), audience)


def present_extensions(root: Path) -> frozenset[str]:
    """The package-level extensions a package present in *root* holds."""
    from livery.workshop._composition import extension_set
    from livery.workshop._packages import discover_packages

    return frozenset(
        name for package in discover_packages(root) for name in extension_set(package)
    )


def fragments(
    root: Path, shipped: Iterable[Prose], audience: str | None
) -> list[Prose]:
    """The fragments *audience* reads: by section, then extension, then name.

    A shipped fragment for the other reader is left out; a rendered
    fragment is always in and answers for itself when rendered. The
    mounted extensions come in mount order, an extension outside the mount
    after them, and the repository's own last. Two fragments of one
    name in the set refuse naming both.
    """
    from livery.workshop._extensions import stack_names

    chosen: list[Prose] = []
    for prose in [*shipped, *_RENDERED.values()]:
        if prose.audience and prose.audience != audience:
            continue
        chosen.append(prose)
    mounted = list(stack_names(root))

    def rank(prose: Prose) -> tuple[int, int, int, str, str]:
        if prose.extension == REPOSITORY:
            tier, index, name = 2, 0, ""
        elif prose.extension in mounted:
            tier, index, name = 0, mounted.index(prose.extension), ""
        else:
            tier, index, name = 1, 0, prose.extension
        return (_SECTIONS.index(prose.section), tier, index, name, prose.name)

    chosen.sort(key=rank)
    seen: dict[str, Prose] = {}
    for prose in chosen:
        other = seen.setdefault(prose.name, prose)
        if other is not prose:
            raise ProseError(
                f"two fragments deliver as {prose.name}: {other.origin} and"
                f" {prose.origin}; a topic is unique across everything delivered"
            )
    return chosen


def render_gate(root: Path, audience: str | None) -> str:
    """The gate's steps for the extensions the members hold.

    A list for the agent, a table for a reader. A step of a phase the
    gate does not run, one declared ``gated = false``, is left out.
    """
    from livery.workshop._steps import _gated, steps_by_name

    present = present_extensions(root)
    records = [
        record
        for record in steps_by_name().values()
        if _gated(record)
        and (not record.extensions or present & set(record.extensions))
    ]
    if not records:
        return ""
    held = ", ".join(sorted(present)) or "none"
    prog = footman.prog()
    lines = ["# The gate's steps", ""]
    if audience == HUMAN:
        lines += [
            f"`{prog} check` runs every step below, each registered by an"
            f" extension; the members hold the package-level extensions {held}.",
            "",
            "| step | tools | packages | rewrites under `--fix` |",
            "| --- | --- | --- | --- |",
        ]
        for record in records:
            tools = ", ".join(record.tools) or "none"
            scope = " or ".join(record.extensions) or "every"
            fix = "yes" if record.fix is not None else "no"
            lines.append(f"| {record.name} | {tools} | {scope} | {fix} |")
    else:
        lines += [
            f"`{prog} check` runs these steps in parallel, the rewriters first"
            f" and one at a time under `--fix`; the members hold the"
            f" package-level extensions {held}.",
            "",
        ]
        for record in records:
            # The name carries the tool; the store's tools are named
            # only where they are more than it.
            tools = (
                f" ({', '.join(record.tools)})"
                if record.tools and record.tools != (record.step,)
                else ""
            )
            where = "the workspace"
            if record.extensions:
                where = f"{' or '.join(record.extensions)} packages"
            fix = "; rewrites under --fix" if record.fix is not None else ""
            lines.append(f"- {record.name}{tools}: judges {where}{fix}")
    return "\n".join(lines) + "\n"


#: The trees this process listed, by root and the stamps of the tasks
#: file and the contract: a listing is a runner process, and a sync
#: renders the verbs fragment for two readers on each of its passes.
_TREES: dict[tuple[Any, ...], dict[str, Any] | None] = {}


def _tree_key(root: Path) -> tuple[Any, ...]:
    from livery.workshop._contract import _stamp

    return (str(root), _stamp(root / "tasks.py"), _stamp(root / "workshop.toml"))


def forget_trees() -> None:
    """Drop every tree this process listed, so the next read lists again."""
    _TREES.clear()


def _own_root() -> Path | None:
    """This process's own project root, the one tree it holds already."""
    return footman.project_root()


def composed_tree(root: Path) -> dict[str, Any] | None:
    """The workspace's composed task tree, as ``--json --list`` reports it.

    This process's own workspace answers from the tree it discovered
    ([livery.footman.task_tree][]). Another root, a newborn a birth syncs
    from its parent's task, is listed by running the runner there: its
    tree lives in its own venv's entry points, which only a process in
    that venv can list. None without a tasks file at the root, without
    the runner on PATH, and when the listing fails: the fragment is then
    left out rather than guessed. Listed once per process; a later read
    answers from the first until the tasks file or the contract changes.
    """
    if not (root / "tasks.py").is_file():
        return None
    key = _tree_key(root)
    if key in _TREES:
        return _TREES[key]
    own = _own_root()
    if own is not None and own.resolve() == root.resolve():
        tree = footman.task_tree()
        _TREES[key] = tree
        return tree
    runner = shutil.which(footman.prog())
    if runner is None:
        return None
    result = footman.run(
        [runner, "--json", "--list"], cwd=root, nofail=True, recorded=False
    )
    if int(result) != 0:
        return None
    tree = cast("dict[str, Any]", json.loads(result.stdout)["tree"])
    _TREES[key] = tree
    return tree


def _collect(
    node: dict[str, Any],
    prefix: str,
    inherited: str,
    by_extension: dict[str, list[str]],
) -> None:
    """Every visible task under *node* by the extension that mounted it.

    Addresses carry *prefix*; a group's ``default`` task is the group's
    own address, the way it is run.
    """
    tasks = node.get("tasks")
    if isinstance(tasks, dict):
        for name, task in tasks.items():
            if not isinstance(task, dict) or task.get("hidden") is True:
                continue
            extension = str(task.get("mounted_from") or inherited)
            address = prefix[:-1] if name == "default" and prefix else f"{prefix}{name}"
            by_extension.setdefault(extension, []).append(address)
    groups = node.get("groups")
    if isinstance(groups, dict):
        for name, sub in groups.items():
            if not isinstance(sub, dict) or sub.get("hidden") is True:
                continue
            extension = str(sub.get("mounted_from") or inherited)
            _collect(sub, f"{prefix}{name}.", extension, by_extension)


def render_verbs(root: Path, audience: str | None) -> str:
    """The verbs by the extension that provides them, the repository's own last."""
    from livery.workshop._extensions import stack_names

    tree = composed_tree(root)
    if tree is None:
        return ""
    by_extension: dict[str, list[str]] = {}
    _collect(tree, "", REPOSITORY, by_extension)
    if not by_extension:
        return ""
    mounted = [
        extension for extension in stack_names(root) if extension in by_extension
    ]
    others = sorted(
        extension
        for extension in by_extension
        if extension not in mounted and extension != REPOSITORY
    )
    ordered = [*mounted, *others, *([REPOSITORY] if REPOSITORY in by_extension else [])]
    prog = footman.prog()
    lines = ["# The verbs by extension", ""]
    if audience == HUMAN:
        lines.append(
            f"`{prog}` composes these verbs from the mounted extensions and the"
            f" repository's own tasks file; `{prog} <verb> --help` describes one."
        )
    else:
        lines.append(
            f"Every job goes through `{prog}`; these are its verbs, by the extension"
            f" that provides them. Verbs hidden from `--help`, which CI and other"
            f" verbs call, are not listed; `{prog} --all --help` shows them:"
        )
    lines.append("")
    for extension in ordered:
        lines.append(
            f"- {extension or 'this repository'}:"
            f" {', '.join(sorted(by_extension[extension]))}"
        )
    return "\n".join(lines) + "\n"


def render_compositions(root: Path, audience: str | None) -> str:
    """The sets of extensions the present packages hold, each with its packages.

    A set is named by the shortest list that composes it, the extensions
    joined with ``+``, and is listed with every extension it holds and
    the gate phases that judge a package holding it. A package holding
    no package-level extension is listed under ``none``.
    """
    from livery.workshop._composition import extension_set, implied, order_or_sorted
    from livery.workshop._extensions import installed_declaration
    from livery.workshop._packages import discover_packages
    from livery.workshop._phases import declared_phases
    from livery.workshop._steps import judges_extensions, steps_by_name

    gated = {name for name, spec in declared_phases().items() if spec.gated}
    members: dict[tuple[str, ...], list[str]] = {}
    for package in discover_packages(root):
        held = order_or_sorted(extension_set(package), installed_declaration)
        members.setdefault(held, []).append(package.path)
    if not members:
        return ""
    lines = ["# The compositions present", ""]
    if audience == HUMAN:
        lines += [
            "| composition | extensions | packages | gate phases |",
            "| --- | --- | --- | --- |",
        ]
    else:
        lines += [
            "The sets of package-level extensions the packages hold, each named by"
            " the shortest list that composes it, with its packages and the gate"
            " phases that judge them:",
            "",
        ]
    named: dict[tuple[str, ...], str] = {}
    for held in members:
        required = implied(held, installed_declaration)
        # The shortest list leaves out what another member requires; a
        # cycle among the requires leaves nothing out of the whole set.
        shortest = [name for name in held if name not in required] or held
        named[held] = "+".join(shortest) or "none"
    for held in sorted(members, key=named.__getitem__):
        extensions = ", ".join(held) or "none"
        paths = ", ".join(sorted(members[held]))
        # A set's phases are those of the gate's steps that judge a
        # package holding it; a step naming no extensions judges the
        # workspace, never one set, and a step of a phase the gate never
        # runs, the docs phase's, is no gate's.
        phases = (
            ", ".join(
                sorted(
                    {
                        r.phase
                        for r in steps_by_name().values()
                        if r.phase in gated
                        and r.extensions
                        and judges_extensions(r, held)
                    }
                )
            )
            or "none"
        )
        if audience == HUMAN:
            lines.append(f"| {named[held]} | {extensions} | {paths} | {phases} |")
        else:
            lines.append(
                f"- {named[held]} ({paths}): holds {extensions}; gate phases {phases}"
            )
    return "\n".join(lines) + "\n"


def render_tools(root: Path, audience: str | None) -> str:
    """The tools the lock pins, each with its version and the sites requiring it."""
    from livery.workshop._tools import current_lock, requirements

    lock = current_lock(root)
    if lock is None or not lock.tools:
        return ""
    sites: dict[str, set[str]] = {}
    for requirement in requirements(root):
        sites.setdefault(requirement.name, set()).add(requirement.site)
    prog = footman.prog()
    hosts = ", ".join(lock.hosts)
    lines = ["# The locked tools", ""]
    if audience == HUMAN:
        lines.append(
            f"`toolroom.lock` pins these for {hosts}; `{prog} toolroom.sync` installs"
            f" them into the tool store, and `{prog} sync` runs it."
        )
    else:
        lines.append(
            f"The tool store holds these versions, pinned by `toolroom.lock` for"
            f" {hosts}; `{prog} sync` installs them, never a hand install:"
        )
    lines.append("")
    for name in sorted(lock.tools):
        where = ", ".join(sorted(sites.get(name, ())))
        scope = ", ".join(lock.tools[name].on)
        lines.append(
            f"- {name} {lock.tools[name].version}"
            + (f": {where}" if where else "")
            + (f" ({scope} only)" if scope else "")
        )
    return "\n".join(lines) + "\n"


def _register_builtin() -> None:
    """The base's rendered fragments: what the registries say, never written by hand."""
    register_fragment("gate", "checks", render_gate)
    register_fragment("verbs", "extensions", render_verbs)
    register_fragment("compositions", "present", render_compositions)
    register_fragment("tools", "locked", render_tools)


_register_builtin()
