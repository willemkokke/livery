"""Prose fragments: what the layers say to the agent and to a reader, in sections.

A layer's prose, its voice, its standards, what a kind's package looks
like, is a set of fragments, and one set writes the agent's entry file
and the site's development pages. A fragment is a markdown file in the
layer's ``content/fragments/`` named
``<section>.[<kind>.]<topic>[.<audience>].md``, or a registration with
[livery.workshop._prose.register_fragment][] and a render that takes
the audience. The base defines the sections and their order;
[livery.workshop._prose.register_section][] adds one at a declared
position. A kind in the name delivers the fragment only while a
package of that kind, or a kind deriving from it, is present. The
audience is ``agent`` or ``human``, and a name without one serves
both; a render answers each audience itself, and an empty answer
leaves the fragment out for that reader. A topic is never named
``agent`` or ``human``: the last part of a name spells the audience.

``fm sync`` delivers the agent's set flat under ``.workshop/fragments/``
with the audience dropped from the name: a shipped fragment byte for
byte its source, a rendered one under a header naming its origin, each
through the materialiser so an edited copy is kept and named. The
repository's own fragments live in ``fragments/`` at the root under the
same names, are never copied, and come last. Reach for
[livery.workshop._prose.fragments][] for the set a reader gets and
[livery.workshop._prose.deliver][] for the delivery.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import livery.footman as footman
from livery.workshop._materialise import (
    materialise_bytes,
    materialise_file,
    sweep_files,
)

BASE_LAYER = "livery.workshop"
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
    "kinds",
    "tools",
)

#: Where the agent's set lands, under the workspace root: its own
#: directory, so the sweep for a withdrawn fragment owns every file it
#: walks, while ``.workshop/`` itself holds this checkout's state in
#: directories beside it.
DELIVERED = ".workshop/fragments"

#: The fragments directory: a layer's under its content, the repository's
#: own at the root.
OWN = "fragments"

#: The layer name of the repository's own fragments.
REPOSITORY = ""

#: A render: the workspace root and the audience, ``agent``, ``human``
#: or None for no particular reader, to the markdown; empty leaves the
#: fragment out for that reader.
Render = Callable[[Path, "str | None"], str]


class ProseError(ValueError):
    """A fragment or a section the registry refuses; the message names the files."""


@dataclass(frozen=True)
class Prose:
    """One prose fragment: a file a layer ships, or a render from the registries.

    Attributes:
        section: The section it belongs to, one the registry knows.
        topic: What it is about; with the section and the kind, its name.
        kind: The kind a present package must be or derive from, or
            empty for every workspace.
        audience: ``agent`` or ``human`` for a file that serves one
            reader, empty for both; a rendered fragment is empty here
            and answers per audience.
        layer: The layer that ships or registers it; empty for the
            repository's own.
        source: The shipped file, or None for a rendered fragment.
        render: The render, or None for a shipped fragment.
    """

    section: str
    topic: str
    kind: str = ""
    audience: str = ""
    layer: str = BASE_LAYER
    source: Path | None = None
    render: Render | None = None

    @property
    def name(self) -> str:
        """The delivered file name, ``<section>.[<kind>.]<topic>.md``."""
        kind = f"{self.kind}." if self.kind else ""
        return f"{self.section}.{kind}{self.topic}.md"

    @property
    def origin(self) -> str:
        """Where it comes from, for a refusal: the file in posix form, or the layer."""
        if self.source is not None:
            return f"{self.source.as_posix()} ({self.layer or 'this repository'})"
        return f"the render {self.layer} registered"

    def text(self, root: Path, audience: str | None) -> str:
        """The markdown for *audience*: the shipped text, or the render's answer."""
        if self.render is not None:
            return self.render(root, audience)
        if self.source is not None:
            return self.source.read_text(encoding="utf-8")
        return ""


_SECTIONS: list[str] = list(BASE_SECTIONS)
_SECTION_LAYERS: dict[str, str] = dict.fromkeys(BASE_SECTIONS, BASE_LAYER)
#: The rendered fragments by delivered name.
_RENDERED: dict[str, Prose] = {}


def sections() -> tuple[str, ...]:
    """The sections in the order the entry file and the site read them."""
    return tuple(_SECTIONS)


def register_section(name: str, *, after: str, layer: str) -> None:
    """Add the section *name* right after *after*.

    Refuses a name a layer already registered, naming that layer, and
    an anchor no layer registered, naming the sections.
    """
    if name in _SECTION_LAYERS:
        raise ProseError(
            f"{layer} registers section {name!r}, which"
            f" {_SECTION_LAYERS[name]} already registered"
        )
    if after not in _SECTION_LAYERS:
        raise ProseError(
            f"{layer} registers section {name!r} after {after!r}, which no"
            f" layer registered; the sections are {', '.join(_SECTIONS)}"
        )
    _SECTIONS.insert(_SECTIONS.index(after) + 1, name)
    _SECTION_LAYERS[name] = layer


def unregister_section(name: str, *, by: str) -> None:
    """Drop the section *name*.

    Refuses the base's own, an unknown one, another layer's, and one a
    rendered fragment still belongs to.
    """
    owner = _SECTION_LAYERS.get(name)
    if owner is None:
        raise ProseError(f"{by} withdraws section {name!r}, which no layer registered")
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
    del _SECTION_LAYERS[name]


def _check_section(where: str, section: str) -> None:
    if section not in _SECTION_LAYERS:
        raise ProseError(
            f"{where}: section {section!r} is not one the registry knows; the"
            f" sections are {', '.join(_SECTIONS)}"
        )


def _check_kind(where: str, kind: str) -> None:
    if not kind:
        return
    from livery.workshop._kinds import kind_names

    if kind not in kind_names():
        raise ProseError(
            f"{where}: kind {kind!r} is not a registered package kind; kinds:"
            f" {', '.join(kind_names())}"
        )


def register_fragment(
    section: str,
    topic: str,
    render: Render,
    *,
    kind: str = "",
    layer: str = BASE_LAYER,
) -> None:
    """Register a rendered fragment.

    The render takes the workspace root and the audience, ``agent``,
    ``human`` or None for no particular reader, and returns the
    markdown; an empty answer leaves the fragment out for that reader.
    Refuses an unknown section or kind, a topic spelling an audience,
    and a name a layer already registered, naming that layer.
    """
    prose = Prose(section, topic, kind, "", layer, render=render)
    where = f"{layer} registers fragment {prose.name}"
    _check_section(where, section)
    _check_kind(where, kind)
    if topic in AUDIENCES:
        raise ProseError(f"{where}: a topic is never named {topic!r}, an audience is")
    existing = _RENDERED.get(prose.name)
    if existing is not None:
        raise ProseError(f"{where}, which {existing.layer} already registered")
    _RENDERED[prose.name] = prose


def unregister_fragment(name: str, *, by: str) -> None:
    """Drop the rendered fragment delivered as *name*.

    Refuses an unknown one, and one another layer registered.
    """
    prose = _RENDERED.get(name)
    if prose is None:
        raise ProseError(f"{by} withdraws fragment {name}, which no layer registered")
    if prose.layer != by:
        raise ProseError(
            f"{by} withdraws fragment {name}, which {prose.layer} registered"
        )
    del _RENDERED[name]


def snapshot() -> tuple[list[str], dict[str, str], dict[str, Prose]]:
    """The registry's state, for [livery.workshop._prose.restore][] to put back."""
    return list(_SECTIONS), dict(_SECTION_LAYERS), dict(_RENDERED)


def restore(state: tuple[list[str], dict[str, str], dict[str, Prose]]) -> None:
    """Put the registry back to *state*."""
    ordered, layers, rendered = state
    _SECTIONS[:] = ordered
    _SECTION_LAYERS.clear()
    _SECTION_LAYERS.update(layers)
    _RENDERED.clear()
    _RENDERED.update(rendered)


def parse_name(filename: str, *, where: str = "") -> tuple[str, str, str, str]:
    """Split a fragment file name into section, kind, topic and audience.

    ``rules.python.tests.agent.md`` is the rules section, the python
    kind, the topic tests, for the agent. With three parts a last part
    spelling an audience is one, so a topic is never named ``agent``
    or ``human``. Refuses a name outside the convention, an unknown
    section and an unknown kind, naming *where*, the file by default.
    """
    where = where or filename
    stem, dot, suffix = filename.rpartition(".")
    parts = stem.split(".") if dot and suffix == "md" else []
    audience = ""
    if len(parts) > 2 and parts[-1] in AUDIENCES:
        audience = parts.pop()
    if len(parts) not in (2, 3) or not all(parts):
        raise ProseError(
            f"{where}: a fragment is named <section>.[<kind>.]<topic>[.<audience>].md"
        )
    section, topic = parts[0], parts[-1]
    kind = parts[1] if len(parts) == 3 else ""
    _check_section(where, section)
    _check_kind(where, kind)
    return section, kind, topic, audience


def shipped(layer: str, content: Path) -> list[Prose]:
    """The fragments *layer* ships under ``content/fragments/``, in name order.

    Every file there is a fragment, so a name outside the convention
    refuses naming the file. The repository's own come from its root
    with the empty layer name.
    """
    directory = content / OWN
    if not directory.is_dir():
        return []
    found: list[Prose] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file():
            continue
        section, kind, topic, audience = parse_name(path.name, where=path.as_posix())
        found.append(Prose(section, topic, kind, audience, layer, source=path))
    return found


def repository_fragments(root: Path) -> list[Prose]:
    """The repository's own fragments under ``fragments/`` at *root*, in name order."""
    return shipped(REPOSITORY, root)


def present_kinds(root: Path) -> frozenset[str]:
    """The kinds a package present in *root* is or derives from."""
    from livery.workshop._kinds import kind_chain, kind_names
    from livery.workshop._packages import discover_packages

    names = set(kind_names())
    present: set[str] = set()
    for package in discover_packages(root):
        if package.kind in names:
            present.update(r.name for r in kind_chain(package.kind) if not r.abstract)
    return frozenset(present)


def fragments(
    root: Path, shipped: Iterable[Prose], audience: str | None
) -> list[Prose]:
    """The fragments *audience* reads, in section order, then layer order, then name.

    A shipped fragment for the other reader is left out, as is one
    gated on a kind no present package is or derives from; a rendered
    fragment is always in and answers for itself when rendered. The
    mounted layers come in mount order, a layer outside the mount
    after them, and the repository's own last. Two fragments of one
    name in the set refuse naming both.
    """
    from livery.workshop._layers import layer_names

    present = present_kinds(root)
    chosen: list[Prose] = []
    for prose in [*shipped, *_RENDERED.values()]:
        if prose.kind and prose.kind not in present:
            continue
        if prose.audience and prose.audience != audience:
            continue
        chosen.append(prose)
    mounted = list(layer_names(root))

    def rank(prose: Prose) -> tuple[int, int, int, str, str]:
        if prose.layer == REPOSITORY:
            tier, index, name = 2, 0, ""
        elif prose.layer in mounted:
            tier, index, name = 0, mounted.index(prose.layer), ""
        else:
            tier, index, name = 1, 0, prose.layer
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


@dataclass(frozen=True)
class Delivery:
    """What one delivery did.

    Attributes:
        lines: The summary lines, empty when nothing changed.
        delivered: The names under ``.workshop/fragments/``, in reading order.
        own: The repository's own names under ``fragments/``, in reading order.
    """

    lines: list[str]
    delivered: tuple[str, ...]
    own: tuple[str, ...]


def _rendered_header(prose: Prose) -> str:
    return (
        f"<!-- Rendered by `{footman.prog()} sync` from the registries"
        f" {prose.layer} fills;\n"
        "     the source is the code. An edited copy is a local override,\n"
        "     kept and named until it is deleted. -->\n"
    )


def deliver(root: Path, shipped: Iterable[Prose]) -> Delivery:
    """Deliver the agent's set under ``.workshop/fragments/``.

    The reader's set is validated too, so a collision a human reader
    would meet refuses here as well. A shipped fragment is copied byte
    for byte and a rendered one written under its header, each through
    the materialiser: a current copy is quiet, a stale one refreshes,
    an edited one is kept and named. A file nobody delivers any more is
    removed. The repository's own fragments stay where they are and are
    named in the result.
    """
    listed = list(shipped)
    chosen = fragments(root, listed, AGENT)
    fragments(root, listed, HUMAN)
    lines: list[str] = []
    delivered: list[str] = []
    own: list[str] = []
    for prose in chosen:
        if prose.layer == REPOSITORY:
            own.append(prose.name)
            continue
        relative = f"{DELIVERED}/{prose.name}"
        if prose.render is not None:
            text = prose.render(root, AGENT)
            if not text:
                continue
            body = (_rendered_header(prose) + text).encode("utf-8")
            lines += materialise_bytes(root, body, relative)
        elif prose.source is not None:
            lines += materialise_file(root, prose.source, relative)
        delivered.append(prose.name)
    for name in sweep_files(root / DELIVERED, set(delivered)):
        lines.append(f"  fragments: removed {name} (no layer ships it)")
    return Delivery(lines, tuple(delivered), tuple(own))


def render_gate(root: Path, audience: str | None) -> str:
    """The gate's checks for the kinds present.

    A list for the agent, a table for a reader.
    """
    from livery.workshop._checks import checks_by_name

    present = present_kinds(root)
    records = [
        record
        for record in checks_by_name().values()
        if not record.kinds or any(kind in present for kind in record.kinds)
    ]
    if not records:
        return ""
    kinds = ", ".join(sorted(present)) or "none"
    prog = footman.prog()
    lines = ["# The gate's checks", ""]
    if audience == HUMAN:
        lines += [
            f"`{prog} check` runs every check below, each a record a layer"
            f" registered; the package kinds present are {kinds}.",
            "",
            "| check | role | tools | kinds | rewrites under `--fix` |",
            "| --- | --- | --- | --- | --- |",
        ]
        for record in records:
            tools = ", ".join(record.tools) or "none"
            scope = ", ".join(record.kinds) or "every"
            fix = "yes" if record.fix is not None else "no"
            lines.append(
                f"| {record.name} | {record.role} | {tools} | {scope} | {fix} |"
            )
    else:
        lines += [
            f"`{prog} check` runs these checks in parallel, the rewriters first"
            f" and one at a time under `--fix`; the package kinds present are"
            f" {kinds}.",
            "",
        ]
        for record in records:
            tools = f" ({', '.join(record.tools)})" if record.tools else ""
            scope = f" on {', '.join(record.kinds)} packages" if record.kinds else ""
            fix = "; rewrites under --fix" if record.fix is not None else ""
            lines.append(f"- {record.name}{tools}: {record.role}{scope}{fix}")
    return "\n".join(lines) + "\n"


def composed_tree(root: Path) -> dict[str, Any] | None:
    """The workspace's composed task tree, as ``--json --list`` reports it.

    None without a tasks file at the root, without the runner on PATH,
    and when the listing fails: the fragment is then left out rather
    than guessed.
    """
    if not (root / "tasks.py").is_file():
        return None
    runner = shutil.which(footman.prog())
    if runner is None:
        return None
    result = footman.run(
        [runner, "--json", "--list"], cwd=root, nofail=True, recorded=False
    )
    if int(result) != 0:
        return None
    return cast("dict[str, Any]", json.loads(result.stdout)["tree"])


def _collect(
    node: dict[str, Any], prefix: str, inherited: str, by_layer: dict[str, list[str]]
) -> None:
    """Every visible task under *node* by the layer that mounted it.

    Addresses carry *prefix*; a group's ``default`` task is the group's
    own address, the way it is run.
    """
    tasks = node.get("tasks")
    if isinstance(tasks, dict):
        for name, task in tasks.items():
            if not isinstance(task, dict) or task.get("hidden") is True:
                continue
            layer = str(task.get("mounted_from") or inherited)
            address = prefix[:-1] if name == "default" and prefix else f"{prefix}{name}"
            by_layer.setdefault(layer, []).append(address)
    groups = node.get("groups")
    if isinstance(groups, dict):
        for name, sub in groups.items():
            if not isinstance(sub, dict) or sub.get("hidden") is True:
                continue
            layer = str(sub.get("mounted_from") or inherited)
            _collect(sub, f"{prefix}{name}.", layer, by_layer)


def render_verbs(root: Path, audience: str | None) -> str:
    """The verbs by the layer that provides them, the repository's own last."""
    from livery.workshop._layers import layer_names

    tree = composed_tree(root)
    if tree is None:
        return ""
    by_layer: dict[str, list[str]] = {}
    _collect(tree, "", REPOSITORY, by_layer)
    if not by_layer:
        return ""
    mounted = [layer for layer in layer_names(root) if layer in by_layer]
    others = sorted(
        layer for layer in by_layer if layer not in mounted and layer != REPOSITORY
    )
    ordered = [*mounted, *others, *([REPOSITORY] if REPOSITORY in by_layer else [])]
    prog = footman.prog()
    lines = ["# The verbs by layer", ""]
    if audience == HUMAN:
        lines.append(
            f"`{prog}` composes these verbs from the mounted layers and the"
            f" repository's own tasks file; `{prog} <verb> --help` describes one."
        )
    else:
        lines.append(
            f"Every job goes through `{prog}`; these are its verbs, by the layer"
            " that provides them:"
        )
    lines.append("")
    for layer in ordered:
        lines.append(
            f"- {layer or 'this repository'}: {', '.join(sorted(by_layer[layer]))}"
        )
    return "\n".join(lines) + "\n"


def render_kinds(root: Path, audience: str | None) -> str:
    """The kinds the present packages are, what each derives from, its gate roles."""
    from livery.workshop._kinds import kind_chain, kind_for, kind_names
    from livery.workshop._packages import discover_packages

    names = set(kind_names())
    members: dict[str, list[str]] = {}
    for package in discover_packages(root):
        if package.kind in names:
            members.setdefault(package.kind, []).append(package.path)
    if not members:
        return ""
    lines = ["# The kinds present", ""]
    if audience == HUMAN:
        lines += [
            "| kind | derives from | packages | gate roles |",
            "| --- | --- | --- | --- |",
        ]
    else:
        lines += [
            "A package's kind, declared in its workshop.toml, decides its template,"
            " its tools and the gate roles that apply to it:",
            "",
        ]
    for kind in sorted(members):
        parents = (
            ", ".join(
                r.name for r in kind_chain(kind) if r.name != kind and not r.abstract
            )
            or "nothing"
        )
        paths = ", ".join(sorted(members[kind]))
        roles = ", ".join(kind_for(kind).ci.check_verbs)
        if audience == HUMAN:
            lines.append(f"| {kind} | {parents} | {paths} | {roles} |")
        else:
            lines.append(
                f"- {kind} ({paths}): derives from {parents}; gate roles {roles}"
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
            f"`tools.lock` pins these for {hosts}; `{prog} tools.sync` installs"
            f" them into the tool store, and `{prog} sync` runs it."
        )
    else:
        lines.append(
            f"The tool store holds these versions, pinned by `tools.lock` for"
            f" {hosts}; `{prog} sync` installs them, never a hand install:"
        )
    lines.append("")
    for name in sorted(lock.tools):
        where = ", ".join(sorted(sites.get(name, ())))
        lines.append(
            f"- {name} {lock.tools[name].version}" + (f": {where}" if where else "")
        )
    return "\n".join(lines) + "\n"


def _register_builtin() -> None:
    """The base's rendered fragments: what the registries say, never written by hand."""
    register_fragment("gate", "checks", render_gate)
    register_fragment("verbs", "layers", render_verbs)
    register_fragment("kinds", "present", render_kinds)
    register_fragment("tools", "locked", render_tools)


_register_builtin()
