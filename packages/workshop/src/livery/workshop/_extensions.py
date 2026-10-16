"""The extension walk: the lists in the contracts, every channel.

Every extension is one kind, and two lists name them: the root
``workshop.toml``'s ``[workspace] extensions`` and each package's
``workshop.toml``, the extensions it is composed of. The root's list
takes any extension, and a package's list one whose declarations
show it composes something in a package. A list's set is
the list and every extension it requires. The installed set is the
root's set and every package's set: a sync installs it, and mounting
reads it, the base first, then the installed set in composition
order: each extension after the ones it requires, ties alphabetical.
No list's order decides anything: each list is written in its
canonical form, the minimal one in composition order. So a package
installed by accident never changes a repository: discovery is the
lists and nothing else.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from livery.footman import cwd, prog
from livery.workshop._composition import OrderCycle, closure, order_or_sorted
from livery.workshop._declaration import (
    API_VERSION,
    FILE,
    Additions,
    Declaration,
    DeclarationError,
    declaration_file,
    read,
)

if TYPE_CHECKING:
    from collections.abc import Iterable
    from importlib.metadata import Distribution, EntryPoint

    from livery.toolroom.store import Spec
    from livery.workshop._packages import Package
    from livery.workshop._points import JobContribution
    from livery.workshop._steps import StepRecord

#: The base. Never listed and never mounted by name: importing its
#: plugin module is the base arriving.
SELF = "livery.workshop"

#: The distribution that ships the base.
BASE_DISTRIBUTION = SELF.replace(".", "-")

#: The entry point group an extension declares itself in: its name
#: maps to the package that ships its declaration file.
GROUP = "workshop.extensions"


def workspace_root(start: Path | None = None) -> Path | None:
    """The nearest ancestor carrying the workspace's contract, or None.

    The workspace contract is the marker; a checkout without one is
    not a workspace and gets no extensions. A package's own
    ``workshop.toml``, under ``packages/<name>/`` or
    ``packages/<group>/<name>/`` of a workspace whose root holds a
    contract, is passed over, so a verb run inside a package resolves
    the workspace it belongs to. Without *start*, the search starts at
    the running task's directory, the process's outside a run: a task
    never reads the process's own, which a parallel run shares.
    """
    from livery.workshop._contract import _package_workspace

    origin = (start or cwd()).resolve()
    for candidate in (origin, *origin.parents):
        contract = candidate / "workshop.toml"
        if not contract.is_file():
            continue
        above = _package_workspace(contract)
        if above is not None and (above / "workshop.toml").is_file():
            continue
        return candidate
    return None


def _declared() -> dict[str, EntryPoint]:
    """Every installed extension's entry point, by name."""
    from livery.footman import installed_entry_points

    return {entry.name: entry for entry in installed_entry_points(GROUP)}


def installed_extensions() -> tuple[str, ...]:
    """The names installed distributions declare extensions under, sorted."""
    return tuple(sorted(_declared()))


def shipping_distribution(package: str) -> Distribution | None:
    """The installed distribution whose ``workshop.extensions`` entry names *package*.

    None when no installed distribution declares an extension shipped
    from *package*.
    """
    for entry in _declared().values():
        if entry.value.partition(":")[0] == package and entry.dist is not None:
            return entry.dist
    return None


def normal_name(name: str) -> str:
    """*name* as a distribution or an extra is compared: lower case, one hyphen per run.

    The packaging standards' normal form: ``.``, ``_`` and ``-`` in any
    run become one ``-``.
    """
    return re.sub(r"[-_.]+", "-", name).lower()


def declaration(extension: str) -> Declaration | None:
    """*extension*'s declaration file, read and judged; None when none is installed.

    Read without importing anything of the extension: footman's
    ``plugin()`` stays a plugin's first importer, and a step's code
    is imported when the step runs.

    Raises:
        DeclarationError: when the package the entry point names ships
            no declaration file, as a release written for another
            workshop does, or the file says something this workshop
            cannot take.
    """
    entry = _declared().get(extension)
    if entry is None:
        return None
    package = entry.value.partition(":")[0]
    found = read(extension, package)
    if found is None:
        raise DeclarationError(
            f"extension {extension!r}: its entry point names {package}, which ships"
            f" no {FILE}; install a release of the extension written for this"
            " workshop"
        )
    return found


def _readable(extension: str) -> Declaration | None:
    """*extension*'s declaration where one can be read; None for the base or none.

    An extension no installed distribution declares has none, and so
    has one whose package ships no declaration file: the mount names
    it, and the sync installs the one this workspace builds.
    """
    if extension == SELF or not ships_declaration(extension):
        return None
    return declaration(extension)


def ships_declaration(extension: str) -> bool:
    """Whether an installed distribution declares *extension* and ships its file.

    False for an extension no installed distribution declares, and for
    one whose entry point names a package without a declaration file,
    as an install whose files moved away leaves behind.
    """
    entry = _declared().get(extension)
    return (
        entry is not None
        and declaration_file(entry.value.partition(":")[0]) is not None
    )


def ships_elsewhere(target: str, distribution: str) -> bool:
    """Whether a wheel other than *distribution* ships the extension *target*.

    A relation to an extension of the same wheel needs no dependency
    and no extra: one wheel has one version, so the two cannot drift
    apart.
    """
    return normal_name(distribution_of(target)) != normal_name(distribution)


def distribution_of(extension: str) -> str:
    """The distribution that ships *extension*: its entry point's, else by convention.

    An extension no installed distribution declares, a newborn's list
    written before its environment exists, say, is taken at the
    convention: an import path names its distribution with its dots as
    hyphens, and a short name is one of the base's own family,
    ``<namespace>-extensions-<name>``, never the index's package of
    that bare name. An entry point whose distribution's metadata is
    gone, moved by an install after the scan, has no name to give and
    is taken at the convention the same way.
    """
    entry = _declared().get(extension)
    name = entry.dist.name if entry is not None and entry.dist is not None else None
    if name:
        return name
    if "." in extension:
        return extension.replace(".", "-")
    return f"{SELF.partition('.')[0]}-extensions-{extension}"


def _workspace_table(root: Path) -> dict[str, Any] | None:
    """The root contract's ``[workspace]``, read raw; None without a contract."""
    contract = root / "workshop.toml"
    if not contract.is_file():
        return None
    # Parsed raw on purpose: this read happens while the extensions
    # mount, before any verb runs. The contract loader judges the same
    # file on the first verb read.
    workspace = tomllib.loads(contract.read_text("utf-8")).get("workspace") or {}
    return workspace if isinstance(workspace, dict) else {}


def missing_list(root: Path) -> str:
    """Why *root*'s contract cannot mount for want of its list; empty with one."""
    workspace = _workspace_table(root)
    if workspace is None or "extensions" in workspace:
        return ""
    return (
        f"{root / 'workshop.toml'}: [workspace] extensions is required; add\n"
        "\n  extensions = []\n\n"
        "under [workspace], naming the extensions this workspace uses"
        f" (`{prog()} doctor` lists the installed ones)"
    )


def listing(text: str) -> Spec:
    """An entry of an ``extensions`` list, as the requirement grammar reads it.

    An entry is a name. A spelling the grammar does not read is taken
    whole as a name: no distribution declares it, so the mount names
    it, and ``layering.graph`` refuses the spelling. One it reads with
    brackets, a version or a scope is read as its name, and
    ``layering.graph`` refuses the spelling too
    ([livery.workshop._extensions.listing_problems][]).
    """
    from livery.toolroom.store import Spec, SpecError

    try:
        return Spec.parse(text, where="[workspace] extensions")
    except SpecError:
        return Spec(text)


def _listed(start: Path | None) -> list[Any]:
    """The root contract's ``[workspace] extensions`` entries, raw; empty without."""
    root = workspace_root(start)
    workspace = None if root is None else _workspace_table(root)
    return list(workspace.get("extensions") or []) if workspace else []


def enabled_steps(start: Path | None = None) -> frozenset[str]:
    """The steps the root contract turns on, each ``<phase>.<step>``; empty without.

    A step its extension declares ``enabled = false`` registers only
    while the root contract sets ``[phases.<phase>.<step>] enabled =
    true``. Read raw, as the list is, since the mount reads it before
    any verb judges the contract.
    """
    root = workspace_root(start)
    contract = None if root is None else root / "workshop.toml"
    if contract is None or not contract.is_file():
        return frozenset()
    phases = tomllib.loads(contract.read_text("utf-8")).get("phases")
    if not isinstance(phases, dict):
        return frozenset()
    found: set[str] = set()
    for phase, steps in cast("dict[str, Any]", phases).items():
        if not isinstance(steps, dict):
            continue
        for step, table in cast("dict[str, Any]", steps).items():
            if isinstance(table, dict) and table.get("enabled") is True:
                found.add(f"{phase}.{step}")
    return frozenset(found)


def _spelling(entry: Any) -> str:
    """How an entry spells its extension: the string, or a table entry's ``name``."""
    return str(entry.get("name", "")) if isinstance(entry, dict) else str(entry)


def workspace_entries(start: Path | None = None) -> tuple[tuple[str, str], ...]:
    """Each entry of ``[workspace] extensions`` as ``(name, distribution)``, in order.

    An entry is a name, or a table ``{ name = "...", without = [...] }``.
    Empty outside a workspace and when the contract has no list: the
    mount refuses that, and a reader has nothing to read.
    """
    entries: list[tuple[str, str]] = []
    for entry in _listed(start):
        name = listing(_spelling(entry)).name
        entries.append((name, distribution_of(name)))
    return tuple(entries)


def workspace_names(start: Path | None = None) -> tuple[str, ...]:
    """The extensions ``[workspace] extensions`` lists, in its order."""
    return tuple(name for name, _ in workspace_entries(start))


def installed_declaration(name: str) -> Declaration | None:
    """*name*'s declaration where one is installed; None otherwise.

    The lookup [livery.workshop._composition][] reads the installed
    extensions through.
    """
    return _readable(name)


def composes_per_package(extension: str) -> bool:
    """Whether *extension* composes something in a package whose set holds it.

    The proof reads the installed declarations alone. *extension*
    composes when it, or an extension it requires transitively,
    declares at its top level or in a ``[for.<target>]`` table a step
    in a phase whose level is ``package``, an answer to a query whose
    level is ``package``, or ``[categories]``; or when a step of any
    installed extension, or a step's per-package fragment, names it in
    ``extensions``. Workspace-level steps and answers, contract keys,
    tools, shipped files, CI jobs, verbs, templates and ``requires``
    itself compose nothing in a package. An extension whose code alone
    reads a package's set is not seen: the declarations define what
    composes, so a package's list refuses such an extension. An
    installed declaration that cannot be read names nothing.
    """
    from livery.workshop._phases import PACKAGE_LEVEL, declared_phases
    from livery.workshop._queries import declared_queries

    phases = declared_phases()
    queries = declared_queries()
    named = _named_by_steps()
    for name in closure((extension,), installed_declaration):
        if name in named:
            return True
        found = installed_declaration(name)
        if found is None:
            continue
        # The read refused a step into a phase, and an answer to a query,
        # that no installed declaration declares, so each is known here.
        if any(
            phases[record.phase].level == PACKAGE_LEVEL
            for record in _declared_steps(found)
        ):
            return True
        answered = [*found.queries]
        for held in found.target_queries.values():
            answered += held
        if any(queries[query].level == PACKAGE_LEVEL for query in answered):
            return True
        if found.categories or any(
            table.get("categories") for table in found.target_tables.values()
        ):
            return True
    return False


def _declared_steps(found: Declaration) -> list[StepRecord]:
    """Every step *found* declares: its top-level tables' and its ``[for]`` tables'."""
    records = [record for held in found.phases.values() for record in held]
    for additions in found.targets.values():
        records += additions.steps
    return records


def installed_record(name: str) -> StepRecord | None:
    """The step *name* as an installed extension declares it; None when none does.

    *name* is ``<phase>.<step>``. The installed extensions are read in
    name order, each one's top-level tables and its ``[for.<target>]``
    tables alike, and the first that declares the step answers, its
    record naming it. A declaration that cannot be read declares
    nothing here: the mount and ``layering.graph`` name it.
    """
    for extension in installed_extensions():
        try:
            found = _readable(extension)
        except DeclarationError:
            continue
        if found is None:
            continue
        for record in _declared_steps(found):
            if record.name == name:
                return replace(record, extension=extension)
    return None


def _named_by_steps() -> frozenset[str]:
    """The extensions a step of an installed extension names in ``extensions``.

    A step's own list and each of its per-package fragments' lists. An
    installed declaration that cannot be read names nothing.
    """
    found: set[str] = set()
    for name in installed_extensions():
        try:
            declared = _readable(name)
        except DeclarationError:
            continue
        if declared is None:
            continue
        for record in _declared_steps(declared):
            found.update(record.extensions)
            for fragment in record.fragments:
                found.update(fragment.extensions)
    return frozenset(found)


def why_inert(extension: str) -> str:
    """What *extension* does instead of composing a package; empty when it composes.

    The proof is `composes_per_package`'s. For an extension that
    composes nothing, the words say what its own steps
    judge: every package that holds one of the extensions they name,
    the workspace as a whole when they name none, or that it declares
    no step. A refusal of the extension in a package's list, or in a
    template's, reads them after ``composes nothing in a package:``.
    """
    if composes_per_package(extension):
        return ""
    found = installed_declaration(extension)
    steps = _declared_steps(found) if found is not None else []
    named: dict[str, None] = {}
    for record in steps:
        named.update(dict.fromkeys(record.extensions))
    if named:
        return f"its steps judge every package that holds {_either(list(named))}"
    if steps:
        return "its steps judge the workspace as a whole"
    return "it declares no step"


def _either(names: list[str]) -> str:
    """*names* as a sentence offers them: ``a``, ``a or b``, ``a, b or c``."""
    if len(names) < 2:
        return "".join(names)
    return f"{', '.join(names[:-1])} or {names[-1]}"


def root_set(start: Path | None = None) -> tuple[str, ...]:
    """The root's set: ``[workspace] extensions`` and what it requires.

    In composition order, whatever the list's order and whether an
    implied extension is written in it. A name no installed
    distribution declares stays in the set and requires nothing. A
    declared order with no start falls back to alphabetical, and
    ``layering.graph`` names the cycle. Empty outside a workspace.
    """
    names = closure(workspace_names(start), installed_declaration)
    return order_or_sorted(names, installed_declaration)


def installed_set(start: Path | None = None) -> tuple[str, ...]:
    """The extensions the workspace installs and mounts, in composition order.

    The root's set and every package's set, each a list and every
    extension it requires, whatever that extension declares, in
    composition order ([livery.workshop._composition.order][]): each
    extension after the ones it requires, ties alphabetical. Neither
    list's order changes it. A name no installed distribution declares
    stays in the set: the mount names it, and ``fm sync`` installs it.
    A declared order with no start falls back to alphabetical here, so
    a sync still runs, and ``layering.graph`` names the cycle. Empty
    outside a workspace.
    """
    root = workspace_root(start)
    if root is None:
        return ()
    listed = list(workspace_names(root))
    for _contract, names in _package_lists(root):
        listed += [listing(name).name for name in names]
    found = closure(listed, installed_declaration)
    return order_or_sorted(found, installed_declaration)


def where_listed(start: Path | None = None) -> dict[str, str]:
    """Each extension of the installed set to the list that brings it, as notes say it.

    ``listed in [workspace] extensions``, ``listed in the `extensions`
    of packages/<name>`` naming each package that lists it, or, for an
    extension no list names, ``required by <name>`` naming the first
    listed extension whose set holds it. Empty outside a workspace.
    """
    root = workspace_root(start)
    if root is None:
        return {}
    listed = list(workspace_names(root))
    found = dict.fromkeys(listed, "listed in [workspace] extensions")
    packages: dict[str, dict[str, None]] = {}
    for contract, names in _package_lists(root):
        where = contract.parent.relative_to(root).as_posix()
        for entry in names:
            name = listing(entry).name
            packages.setdefault(name, {})[where] = None
            listed.append(name)
    for name, places in packages.items():
        spoken = _spoken(list(places))
        found.setdefault(name, f"listed in the `extensions` of {spoken}")
    for name in listed:
        for needed in closure((name,), installed_declaration):
            found.setdefault(needed, f"required by {name}")
    return found


def extension_entries(start: Path | None = None) -> tuple[tuple[str, str], ...]:
    """Every extension of the installed set as ``(name, distribution)``, in mount order.

    The set [livery.workshop._extensions.installed_set][] composes, each
    with the distribution that ships it
    ([livery.workshop._extensions.distribution_of][]). Empty outside a
    workspace.
    """
    return tuple((name, distribution_of(name)) for name in installed_set(start))


def stack_entries(start: Path | None = None) -> tuple[tuple[str, str], ...]:
    """The base, then every listed extension in mount order, as name and distribution.

    What a reader of the whole stack walks: the base ships the template
    tree the extensions overlay, and composition follows the mount's
    order. Empty outside a workspace.
    """
    root = workspace_root(start)
    if root is None or not (root / "workshop.toml").is_file():
        return ()
    return ((SELF, SELF.replace(".", "-")), *extension_entries(start))


def stack_names(start: Path | None = None) -> tuple[str, ...]:
    """The base, then every listed extension: whose content a sync delivers."""
    return tuple(name for name, _ in stack_entries(start))


def extension_package(extension: str) -> str:
    """The dotted package *extension* ships from: the one its entry point names.

    The base is its own; an extension no installed distribution
    declares is taken at its name, which a member mid-birth may be.
    """
    if extension == SELF:
        return SELF
    entry = _declared().get(extension)
    if entry is None:
        return extension
    return entry.value.partition(":")[0]


def extension_provider(extension: str, packages: tuple[Package, ...]) -> str:
    """The member package whose sources ship *extension*, by its path; empty if none.

    *extension* is the name it is listed by, which is no module: the
    package its entry point names is looked up instead. An extension
    installed from an index has no member.
    """
    from livery.workshop._influence import provider_of

    return provider_of(extension_package(extension), packages)


def extension_names(start: Path | None = None) -> tuple[str, ...]:
    """The extensions the workspace installs and mounts, in mount order.

    The root's set and every package's set, in composition order
    ([livery.workshop._extensions.installed_set][]).
    ``workspace_names`` reads the workspace list alone, and
    ``root_set`` the root's set.
    """
    return installed_set(start)


def extension_content(extension: str) -> Path | None:
    """The installed extension's ``content/`` directory, or None.

    Beside the extension's declaration file, or the base's own for
    the base. In the monorepo the "wheel" is the editable source tree,
    which is what lets the materialised links point back into the
    repository. None for an extension that is not installed or ships
    no content.
    """
    if extension == SELF:
        content = base_directory() / "content"
        return content if content.is_dir() else None
    found = declaration(extension)
    if found is None:
        return None
    content = found.path.resolve().parent / "content"
    return content if content.is_dir() else None


def base_directory() -> Path:
    """The directory this process imported the base from.

    The base's own files, its ``content/`` among them, are read from
    here.
    """
    return Path(__file__).resolve().parent


def shipped_content(start: Path | None = None) -> tuple[tuple[str, Path], ...]:
    """Each extension of the workspace's stack with its ``content/`` directory.

    The base first, then the installed set in mount order, each with
    the directory its shipped files live in; an extension that ships no
    content, or that no installed distribution declares, is left out.
    """
    found: list[tuple[str, Path]] = []
    for extension in stack_names(start):
        content = extension_content(extension)
        if content is not None:
            found.append((extension, content))
    return tuple(found)


def left_out(start: Path | None = None) -> dict[str, tuple[str, ...]]:
    """Each root entry's ``without``: the targets its extension contributes nothing to.

    Cooperation is greedy: an extension contributes to every target of
    the installed set it declares a ``[for.<target>]`` table for, a
    target listed later included. A table entry's ``without = [...]``
    is the workspace's opt-out, naming the targets that get nothing
    from it. An entry with no ``without`` leaves out nothing, and the
    fix of ``layering.graph`` writes a table entry that leaves out
    nothing as the name alone.
    """
    found: dict[str, tuple[str, ...]] = {}
    for entry in _listed(start):
        name = listing(_spelling(entry)).name
        written = entry.get("without") if isinstance(entry, dict) else None
        found[name] = (
            tuple(str(target) for target in cast("list[object]", written))
            if isinstance(written, list)
            else ()
        )
    return found


#: Whether mount_extensions ran in this process. The cascade hook reads
#: it: a workspace declaring further extensions whose tasks.py never
#: mounted them is running a pre-composition render, and the gap
#: must teach rather than silently narrow the tree.
MOUNTED: bool = False

#: The listed extensions the last mount found no installed distribution
#: declaring. A sync reads them before it renders anything
#: ([livery.workshop._extensions.unmounted][]).
UNDECLARED: tuple[str, ...] = ()

#: Of those, the ones an installed distribution declares by an entry point
#: whose package ships no declaration file: an environment from before the
#: checkout's, which a sync's ``uv sync`` brings current.
STALE: tuple[str, ...] = ()


def snapshot(*, initial: bool = False) -> tuple[bool, tuple[str, ...], tuple[str, ...]]:
    """What the mount recorded, for [livery.workshop._extensions.restore][] to put back.

    Whether a mount ran, and the listed extensions it found undeclared
    and stale. With *initial*, the record a process starts with
    instead: no mount, and nothing found.
    """
    if initial:
        return False, (), ()
    return MOUNTED, UNDECLARED, STALE


def restore(state: tuple[bool, tuple[str, ...], tuple[str, ...]]) -> None:
    """Put the mount's records back to *state*."""
    global MOUNTED, UNDECLARED, STALE
    MOUNTED, UNDECLARED, STALE = state


def mount_git_plugin() -> str:
    """Mount the git plugin; the reason it could not, printed as a note, or empty.

    The environment every git spawn runs with: one ssh connection per
    host, the tools' identity, an editor that never opens. The mount
    runs on every command, ``fm sync`` among them, which is what
    installs the entry point a checkout that just moved past the
    plugin's arrival lacks, so a refusal here would stop the command
    that repairs it.
    """
    from livery.footman import plugin, prog

    try:
        plugin("livery.git")
    except ValueError as error:
        why = f"the git plugin is not mounted ({error}); `{prog()} sync` installs it"
        _note(why)
        return why
    return ""


def mount_extensions(start: Path | None = None) -> tuple[str, ...]:
    """Mount the plugin of every extension of the installed set, in mount order.

    Returns the names mounted. The installed set mounts in composition
    order ([livery.workshop._extensions.installed_set][]), so an
    extension finds every extension it requires already registered,
    whatever either list's order. Any extension mounts from either
    list. Refuses an extension declared for another workshop API
    version, one whose declaration file says something this workshop
    cannot take, a step into a phase of an extension it does not
    require (``check_phase_declarers``), and a step that replaces or
    deletes a step of an extension it does not require
    (``check_replacements``).
    A contract without ``[workspace] extensions``, an extension no
    installed distribution declares, and one whose installed package
    ships no declaration file are named on stderr, the first of these
    with the list that brings it, and skipped, never refused:
    the mount runs on every command, ``fm sync`` among them, which is
    what installs a missing declaration, so a refusal here would stop
    the command that repairs it. An entry with brackets is named the
    same way and read as its name alone. The gate's ``layering.graph`` and
    ``fm extensions`` refuse the same problems. An extension
    whose declaration names no ``plugin`` registers what it declares
    and mounts no verbs, and one that declares nothing but answers to
    queries, at its top level or in a ``[for.<target>]`` table, is
    mounted all the same, since the listed extensions' answers are read
    while they are listed. A plugin a branded App already mounts as a
    builtin is not mounted again, since claiming its tasks again puts
    the same task in one rung twice; what its declaration adds
    registers as any other extension's does. A dormant extension
    ([livery.workshop._extensions.dormancy][]) registers no step and no
    job, at its top level or in its ``[for.<target>]`` tables, and its
    plugin mounts all the same, so its verbs stay.
    """
    from livery.footman import builtins, plugin

    global MOUNTED, UNDECLARED, STALE
    MOUNTED = True
    undeclared: list[str] = []
    root = workspace_root(start)
    if root is not None and (why := missing_list(root)):
        _note(why)
    builtin = set(builtins())
    mounted = []
    stale: list[str] = []
    declared = declared_targets(start)
    active = resolved_targets(start)
    enabled = enabled_steps(start)
    contributes = _contributing(active)
    asleep = frozenset(_dormant(tuple(active), contributes))
    # The base is present before this walk; every extension joins once
    # its mount ran.
    present = [SELF]
    grafted: set[tuple[str, str]] = set()
    _graft_contributions(present, declared, active, grafted, enabled, asleep)
    named: dict[str, str] | None = None
    for extension, dist in extension_entries(start):
        if extension in builtin:
            # The App mounted it as its own builtin: its plugin is never
            # mounted here a second time, and what its declaration adds
            # registers all the same.
            found = _readable(extension)
            if found is not None:
                check_api_version(extension, found)
                check_phase_declarers(extension, found)
                check_replacements(extension, found)
                if (
                    extension not in asleep
                    and register_declared(extension, found.additions, enabled=enabled)
                ) or _answers_queries(found):
                    mounted.append(extension)
            present.append(extension)
            _graft_contributions(present, declared, active, grafted, enabled, asleep)
            continue
        package = extension_package(extension)
        if extension in _declared() and declaration_file(package) is None:
            # An environment from before this checkout's, or a release
            # written for another workshop: named and skipped, so the
            # sync that installs this checkout's can run.
            undeclared.append(extension)
            stale.append(extension)
            _note(
                f"extension {extension!r}: the installed distribution ({dist})"
                f" names {package}, which ships no {FILE}; `{prog()} sync`"
                " installs the one this workspace builds"
            )
            continue
        found = declaration(extension)
        if found is None:
            undeclared.append(extension)
            if named is None:
                named = where_listed(start)
            _note(
                f"extension {extension!r} is"
                f" {named.get(extension, 'listed')}, and no installed"
                f" distribution declares it in {GROUP}; install the distribution"
                f" that ships it ({dist} by its name), or remove the entry;"
                f" `{prog()} sync` installs it the way this workspace resolves it"
            )
            continue
        check_api_version(extension, found)
        check_phase_declarers(extension, found)
        check_replacements(extension, found)
        if (
            extension not in asleep
            and register_declared(extension, found.additions, enabled=enabled)
        ) or _answers_queries(found):
            mounted.append(extension)
        name = found.plugin
        if name and name not in builtin:
            try:
                plugin(name)
            except Exception as error:
                _note(
                    f"extension {extension!r} did not mount its plugin {name!r}:"
                    f" {error}; its distribution ({dist}) belongs in the dev"
                    f" group, and `{prog()} sync` installs it"
                )
            else:
                if extension not in mounted:
                    mounted.append(extension)
        present.append(extension)
        _graft_contributions(present, declared, active, grafted, enabled, asleep)
    # An extension's steps registered as it mounted; their verbs join the
    # ones the workshop generated as it loaded.
    from livery.workshop._steps import generate_verbs

    generate_verbs()
    UNDECLARED = tuple(undeclared)
    STALE = tuple(stale)
    return tuple(mounted)


def stale_declarations(start: Path | None = None) -> tuple[str, ...]:
    """The listed extensions the mount found installed from before this checkout.

    Each has an entry point whose package ships no declaration file, so
    the environment's metadata is older than the source: ``uv sync``
    brings it current.
    """
    listed = extension_names(start)
    return tuple(name for name in STALE if name in listed)


def unmounted(start: Path | None = None) -> tuple[str, ...]:
    """The listed extensions the mount found no installed distribution declaring.

    An extension the contract no longer lists is not counted.
    """
    listed = extension_names(start)
    return tuple(name for name in UNDECLARED if name in listed)


def declared_now(names: tuple[str, ...]) -> tuple[str, ...]:
    """Of *names*, the extensions an installed distribution declares now.

    Reads the installed entry points again, since the process scanned
    them once at its start, and imports nothing: a distribution
    installed after the interpreter started may not be importable in
    it, as an editable install's path joins `sys.path` only when a
    process starts.
    """
    if not names:
        return ()
    from livery.footman import rescan_entry_points

    rescan_entry_points()
    declared = _declared()
    return tuple(name for name in names if name in declared)


def install_places(names: Iterable[str]) -> dict[str, tuple[str, str, str]]:
    """Where each of *names*, the base or an extension, is installed, as read here.

    Each place is three parts: the package the extension's entry point
    names, the declaration file this process reads for it, and the
    origin the installer recorded in the distribution's
    ``direct_url.json``, which is empty for a distribution from an
    index. The file is empty when the directory this process found the
    package in no longer holds one, and every part is empty for an
    extension no installed distribution declares. The base, named by
    ``SELF``, has the same three parts: its package, the directory this
    process imported it from (empty once that directory holds no
    ``__init__.py``), and the origin of ``BASE_DISTRIBUTION``; every
    part is empty when no distribution of that name is installed.

    Read before an install and again after it, a place that differs
    names an extension this process cannot read where the environment
    installs it now. The directory a package was found in is kept for
    the process, and an editable install's path joins ``sys.path`` only
    when a process starts. The origin tells an editable install's tree
    that is still on disk from the tree the environment installs.
    Imports nothing.
    """
    declared = _declared()
    places: dict[str, tuple[str, str, str]] = {}
    for name in names:
        if name == SELF:
            places[name] = _base_place()
            continue
        entry = declared.get(name)
        if entry is None:
            places[name] = ("", "", "")
            continue
        package = entry.value.partition(":")[0]
        found = declaration_file(package)
        dist = entry.dist
        origin = dist.read_text("direct_url.json") if dist is not None else None
        places[name] = (package, str(found or ""), origin or "")
    return places


def _base_place() -> tuple[str, str, str]:
    """Where the base is installed, as this process reads it; see `install_places`."""
    from importlib.metadata import PackageNotFoundError, distribution

    try:
        dist = distribution(BASE_DISTRIBUTION)
    except PackageNotFoundError:
        return ("", "", "")
    directory = base_directory()
    found = str(directory) if (directory / "__init__.py").is_file() else ""
    return (SELF, found, dist.read_text("direct_url.json") or "")


def _note(text: str) -> None:
    """Name a problem the mount skips past, on stderr."""
    import sys

    print(f"  note: {text}", file=sys.stderr)


def _reached(extension: str, found: Declaration) -> frozenset[str]:
    """The owners whose steps *extension*'s top-level tables may reach.

    The base, *extension* itself and every extension it requires,
    transitively: each is in every set that holds *extension*, and
    mounts before it. A ``[for.<target>]`` table reaches its target as
    well, since the table registers once both are mounted.
    """
    return frozenset({SELF, extension, *closure(found.requires, installed_declaration)})


def check_phase_declarers(extension: str, found: Declaration) -> None:
    """Refuse a step of *extension* into a phase of an extension it does not require.

    A phase is declared by the base or by an extension. A step of
    *extension*'s top-level tables goes into a phase the base,
    *extension* itself or an extension it requires declares; a step of
    a ``[for.<target>]`` table also into one *target* declares. Judged
    from the declarations alone, every ``[for]`` table whether it
    applies or not, so the verdict follows no list and no list's order.

    Raises:
        Failed: naming the extension, the phase and its declarer, with
            the two ways to write the step.
    """
    from livery.footman import fail
    from livery.workshop import _phases

    reached = _reached(extension, found)
    added = {
        (phase, reached)
        for phase in (
            *(record.phase for record in found.additions.steps),
            *found.phases,
        )
    }
    for target, additions in found.targets.items():
        added |= {(record.phase, reached | {target}) for record in additions.steps}
    specs = _phases.declared_phases()
    for phase, allowed in sorted(added, key=lambda pair: (pair[0], sorted(pair[1]))):
        declarer = specs[phase].extension if phase in specs else SELF
        if declarer not in allowed:
            fail(
                f"extension {extension!r} adds steps to the {phase} phase, which"
                f" {declarer} declares, and does not require {declarer}; add"
                f" {declarer} to its [extension] requires, or move the steps into"
                f" [for.{declarer}]"
            )


def check_replacements(extension: str, found: Declaration) -> None:
    """Refuse a step of *extension* that replaces or deletes one it cannot reach.

    A step names each step it replaces or deletes as
    ``<owner>:<phase>.<step>``. In *extension*'s top-level tables the
    owner is the base or an extension *extension* requires, so the
    owner's step registers first in every set that holds both; in a
    ``[for.<target>]`` table it may also be *target*. Judged from the
    declarations alone, whatever the lists hold or their order.

    Raises:
        Failed: naming the step, the one it replaces or deletes, and the
            two ways to write it.
    """
    from livery.footman import fail

    reached = _reached(extension, found)
    tables: list[tuple[frozenset[str], list[StepRecord]]] = [
        (
            reached,
            [
                *found.additions.steps,
                *(record for held in found.phases.values() for record in held),
            ],
        )
    ]
    tables += [
        (reached | {target}, list(additions.steps))
        for target, additions in found.targets.items()
    ]
    for allowed, records in tables:
        for record in records:
            for verb, named in (
                ("replaces", record.replaces),
                ("deletes", record.deletes),
            ):
                for address, _reason in named:
                    owner = address.partition(":")[0]
                    if owner not in allowed:
                        fail(
                            f"extension {extension!r}: {record.name} {verb}"
                            f" {address}, and {extension} does not require {owner};"
                            f" add {owner} to its [extension] requires, or move the"
                            f" step into [for.{owner}]"
                        )


def _graft_contributions(
    present: list[str],
    declared: dict[str, dict[str, Additions]],
    active: dict[str, tuple[str, ...]],
    grafted: set[tuple[str, str]],
    enabled: frozenset[str] = frozenset(),
    asleep: frozenset[str] = frozenset(),
) -> None:
    """Register every ``[for.<target>]`` table whose owner and target are both present.

    Called after each extension mounts, so a contribution lands whichever
    of the two mounts later. *active* holds each owner's targets, so a
    target its root entry's ``without`` names registers nothing. A step
    in the table declared off registers under the steps the root
    contract turns on, *enabled*, as one at the file's top level does.
    An owner among *asleep*, the dormant extensions, registers none of
    its tables.
    """
    for owner in present:
        if owner in asleep:
            continue
        for target in active.get(owner, ()):
            if target not in present or (owner, target) in grafted:
                continue
            additions = declared.get(owner, {}).get(target)
            if additions is None:
                continue
            register_declared(owner, additions, enabled=enabled)
            grafted.add((owner, target))


def _answers_queries(found: Declaration) -> bool:
    """Whether *found* answers a query, at its top level or in a ``[for]`` table.

    A ``[for]`` table's answer counts whether its target is listed or
    not: the mount counts what the declaration answers, and the query
    decides which places it asks.
    """
    return bool(found.queries or found.target_queries)


def register_declared(
    extension: str, additions: Additions, *, enabled: frozenset[str] = frozenset()
) -> bool:
    """Register what *additions* declares for *extension*; whether it declares a step.

    Each step registers under the extension's listed name, since the
    list is what decides which extension a step belongs to, and each
    job joins its point under the same name. A step declared
    ``enabled = false`` registers only when
    *enabled*, the steps the root contract turns on
    ([livery.workshop._extensions.enabled_steps][]), names it.
    """
    from dataclasses import replace

    from livery.workshop._steps import register_step

    for record in additions.steps:
        if not record.enabled and record.name not in enabled:
            continue
        register_step(replace(record, extension=extension))
    register_jobs(extension, additions.jobs)
    return bool(additions.steps)


def register_jobs(extension: str, jobs: tuple[JobContribution, ...]) -> None:
    """Add each of *jobs* to its builtin point, its entries' source *extension*.

    Raises:
        Failed: when another extension contributed a job of the same name
            to the same point.
    """
    from dataclasses import replace

    from livery.workshop._points import contribute_job

    for item in jobs:
        contribute_job(
            item.point,
            item.job,
            entries=tuple(replace(entry, source=extension) for entry in item.entries),
            gates=item.gates,
            extension=extension,
        )


def check_api_version(extension: str, found: Declaration) -> None:
    """Refuse an extension whose declared API version is not this workshop's.

    An extension declares ``[extension] api-version`` in its declaration
    file; one that declares none is taken at the current version.
    """
    declared = found.api_version
    if declared != API_VERSION:
        raise RuntimeError(
            f"extension {extension!r} declares workshop API version {declared!r}; this"
            f" workshop is version {API_VERSION}. Install a release of the"
            " extension written for this workshop, or of the workshop the extension"
            " was written for."
        )


def extension_dependencies(start: Path | None = None) -> dict[str, tuple[str, ...]]:
    """Each listed extension's ``[extension] requires``, name to names.

    The base declares none here, and an extension no installed
    distribution declares has none to read: the mount teaches the
    install.
    """
    found: dict[str, tuple[str, ...]] = {}
    for extension in extension_names(start):
        declared = _readable(extension)
        found[extension] = declared.requires if declared is not None else ()
    return found


def extension_tools(start: Path | None = None) -> dict[str, tuple[str, ...]]:
    """Each listed extension's ``[toolroom] requires``, name to requirement strings.

    The tools the extension runs, such as cmake's ``cmake`` and
    ``ninja`` or a verb's ``docker>=27``, then the tools each of its
    ``[for.<target>]`` tables requires while the table applies, as
    [livery.workshop.contributions_for][] decides it: a measurer's tool
    is required while the workspace lists what it measures for, and
    never otherwise. An extension no installed distribution declares
    declares none here, and the base declares its own in its contract
    file, which the tool requirements read at a site of its own.
    """
    active = resolved_targets(start)
    applies = _contributing(active)
    found: dict[str, tuple[str, ...]] = {}
    for extension in active:
        declared = _readable(extension)
        if declared is None:
            found[extension] = ()
            continue
        found[extension] = (
            *declared.tools,
            *(
                text
                for target in applies.get(extension, ())
                for text in declared.target_tools.get(target, ())
            ),
        )
    return found


def notes_tools(start: Path | None = None) -> dict[str, tuple[str, ...]]:
    """Each listed extension that writes release notes, to its ``[toolroom] requires``.

    A release's entries must not depend on the machine that wrote
    them, so no host allowance reaches these tools
    ([livery.workshop._tools.host_allowed][]).
    """
    from livery.workshop._release_notes import RELEASE_NOTES

    found: dict[str, tuple[str, ...]] = {}
    for extension in extension_names(start):
        declared = _readable(extension)
        if declared is not None and RELEASE_NOTES.name in declared.queries:
            found[extension] = declared.tools
    return found


def declared_targets(start: Path | None = None) -> dict[str, dict[str, Additions]]:
    """Each listed extension's ``[for.<target>]`` tables, owner to target to tables."""
    found: dict[str, dict[str, Additions]] = {}
    for extension in extension_names(start):
        declared = _readable(extension)
        found[extension] = dict(declared.targets) if declared is not None else {}
    return found


def resolved_targets(start: Path | None = None) -> dict[str, tuple[str, ...]]:
    """Each extension of the installed set to its active targets, in composition order.

    Every target of the installed set the extension declares a
    ``[for.<target>]`` table for, except those its root entry's
    ``without`` names: cooperation is greedy, and an opt-out names what
    is off (``left_out``).
    """
    names = extension_names(start)
    declared = declared_targets(start)
    off = left_out(start)
    return {
        owner: tuple(
            name
            for name in names
            if name in declared.get(owner, {}) and name not in off.get(owner, ())
        )
        for owner in names
    }


def contributions_for(
    target: str, start: Path | None = None
) -> dict[str, dict[str, Any]]:
    """What the installed extensions declare for *target*, by extension.

    The extensions come in composition order. An extension contributes
    its ``[for.<target>]`` table while *target* is listed and the
    extension's listing contributes to it (every listed target it
    declares a table for, except those its root entry's ``without``
    names), the rule the mount grafts by; an extension that requires
    *target* contributes its top-level tables. The tables come as the
    declaration file holds them, and an extension that declares nothing
    for *target* is left out, as is every extension when *target* is
    not listed. *start* is a directory in the workspace, the working
    directory when absent.
    """
    names = extension_names(start)
    if target not in names:
        return {}
    contributes = contributing(start)
    found: dict[str, dict[str, Any]] = {}
    for owner in names:
        declared = _readable(owner)
        if declared is None or owner == target:
            continue
        if target in declared.requires:
            found[owner] = dict(declared.tables)
        elif target in contributes.get(owner, ()):
            found[owner] = dict(declared.target_tables[target])
    return found


def contributing(start: Path | None = None) -> dict[str, tuple[str, ...]]:
    """Each listed extension to the listed targets its ``[for]`` tables apply to now.

    A target is listed, the owner declares a ``[for.<target>]`` table for
    it, and the owner's root entry does not name it in ``without``. An
    extension that contributes to nothing is left out. The rule the
    mount grafts by, which [livery.workshop.contributions_for][], the
    delivery of a table's files and dormancy read through.
    """
    return _contributing(resolved_targets(start))


def _contributing(active: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    """What `contributing` answers, from *active*: each listed extension's targets.

    *active* is [livery.workshop._extensions.resolved_targets][]'s
    answer, which holds every extension of the installed set, in
    composition order, so a caller that already has it reads no
    contract again.
    """
    found: dict[str, tuple[str, ...]] = {}
    for owner, resolved in active.items():
        declared = _readable(owner)
        if declared is None:
            continue
        targets = tuple(
            target
            for target in resolved
            if target != owner and target in active and target in declared.target_tables
        )
        if targets:
            found[owner] = targets
    return found


def dormancy(start: Path | None = None) -> dict[str, str]:
    """Each listed extension that is dormant now, to what it waits for.

    An extension whose ``[extension] dormant-until`` names what another
    does for it, ``measures`` for coverage, is dormant while no listed
    extension contributes a ``[for.<it>]`` table to it
    ([livery.workshop._extensions.contributing][]): it registers no step
    and no job, and its plugin still mounts. The text says what it waits
    for and names the installed extensions whose declarations carry such
    a table, which would wake it: ``nothing listed measures; cmake and
    pytest would``. A listing kept for a contributor still to come is a
    statement of intent, so dormancy is named, never refused.
    """
    active = resolved_targets(start)
    return _dormant(tuple(active), _contributing(active))


def _dormant(
    names: tuple[str, ...], contributes: dict[str, tuple[str, ...]]
) -> dict[str, str]:
    """`dormancy` over the listed *names*, given what each contributes to."""
    woken = {target for targets in contributes.values() for target in targets}
    found: dict[str, str] = {}
    for name in names:
        declared = _readable(name)
        if declared is None or not declared.dormant_until or name in woken:
            continue
        wakers = _wakers(name)
        would = f"{_spoken(wakers)} would" if wakers else "no installed extension would"
        found[name] = f"nothing listed {declared.dormant_until}; {would}"
    return found


def _wakers(target: str) -> list[str]:
    """The installed extensions whose declarations carry a ``[for.<target>]`` table.

    Sorted. An installed declaration that cannot be read is left out:
    an extension the workspace does not list is not its to judge.
    """
    found: list[str] = []
    for name in installed_extensions():
        if name == target:
            continue
        try:
            declared = _readable(name)
        except DeclarationError:
            continue
        if declared is not None and target in declared.target_tables:
            found.append(name)
    return found


def _spoken(names: list[str]) -> str:
    """*names* as a sentence lists them: ``a``, ``a and b``, ``a, b and c``."""
    if len(names) < 2:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


def describe_extensions(start: Path | None = None) -> list[str]:
    """The lines ``fm extensions`` prints: each extension with what it declares.

    The base, then each extension of the installed set in mount order;
    under each, who requires it, the packages whose sets hold it, its
    tools, the targets it contributes to, the targets its root entry
    leaves out, whether it is dormant and what would wake it, and the
    steps it declares off, each with whether it is on.
    """
    names = extension_names(start)
    who = requirers(start)
    tools = extension_tools(start)
    targets = resolved_targets(start)
    off = left_out(start)
    enabled = enabled_steps(start)
    holders = _holders(start)
    asleep = dormancy(start)
    lines: list[str] = [f"  {SELF} (the base)"]
    for name in names:
        needed = who.get(name, ())
        by = f" (required by {', '.join(needed)})" if needed else ""
        lines.append(f"  {name}{by}")
        if holders.get(name):
            lines.append(f"    packages: {', '.join(holders[name])}")
        if tools.get(name):
            lines.append(f"    tools: {', '.join(tools[name])}")
        if targets.get(name):
            lines.append(f"    for: {', '.join(targets[name])}")
        if off.get(name):
            lines.append(f"    without: {', '.join(off[name])}")
        if name in asleep:
            lines.append(f"    dormant: {asleep[name]}")
        for step in _declared_off(name):
            state = "on" if step in enabled else "off"
            phase, _, name_in_phase = step.partition(".")
            lines.append(
                f"    step {step} ({state}): declared off; the root contract turns"
                f" it on with [phases.{phase}.{name_in_phase}] enabled = true"
            )
    return lines


def _declared_off(extension: str) -> tuple[str, ...]:
    """The steps *extension* declares ``enabled = false``, each ``<phase>.<step>``.

    Those of its top-level tables and of its ``[for.<target>]`` tables
    alike, in the file's order; empty for the base and for an extension
    nothing installed declares.
    """
    found = _readable(extension)
    if found is None:
        return ()
    steps = [*found.additions.steps]
    for additions in found.targets.values():
        steps += additions.steps
    return tuple(record.name for record in steps if not record.enabled)


def enabled_problems(root: Path) -> list[str]:
    """Each root ``[phases.<phase>.<step>]`` table turning on no step declared off.

    The root contract turns on a step a listed extension declares
    ``enabled = false``; a table naming any other step, one no listed
    extension declares or one that is on already, is refused naming
    the steps declared off.
    """
    off = {step: name for name in extension_names(root) for step in _declared_off(name)}
    problems: list[str] = []
    for step in sorted(enabled_steps(root)):
        if step in off:
            continue
        phase, _, name = step.partition(".")
        declared = ", ".join(f"{other} ({by})" for other, by in sorted(off.items()))
        problems.append(
            f"[phases.{phase}.{name}] turns on {step}, which no listed extension"
            " declares off; the steps declared off are"
            f" {declared or 'none'}"
        )
    return problems


def _holders(start: Path | None) -> dict[str, tuple[str, ...]]:
    """Each extension a package's set holds to the packages holding it, by path."""
    root = workspace_root(start)
    if root is None:
        return {}
    found: dict[str, list[str]] = {}
    for contract, listed in _package_lists(root):
        where = contract.parent.relative_to(root).as_posix()
        names = [listing(entry).name for entry in listed]
        for name in closure(names, installed_declaration):
            found.setdefault(name, []).append(where)
    return {name: tuple(where) for name, where in found.items()}


def closure_problems(start: Path | None = None) -> list[str]:
    """Why the contracts' lists are not valid compositions in their canonical form.

    Empty when every target a root entry's ``without`` names is in the
    installed set and its extension declares a contribution for it;
    when every listed extension is installed and every entry is a name
    alone (``entry_problems``); when every step the root contract turns
    on is one a listed extension declares off (``enabled_problems``);
    when the root's list is its canonical one (``root_list_problems``);
    and when every package's list is a valid composition in its
    canonical form, each of its entries composing something in a
    package (``composition_problems``). The gate's ``--fix`` writes
    each list in its canonical form and moves a package's entry that
    composes nothing to the root's list (``write_extensions``).
    """
    root = workspace_root(start)
    installed = set(installed_set(start))
    problems: list[str] = []
    declared = declared_targets(start)
    for owner, targets in left_out(start).items():
        if installed_declaration(owner) is None:
            # entry_problems names an extension nothing installed declares.
            continue
        for target in targets:
            if target not in declared.get(owner, {}):
                problems.append(
                    f"[workspace] extensions: the entry for {owner} names {target}"
                    f" in `without`, and {owner} declares no contribution for it;"
                    " remove it from `without`"
                )
            elif target not in installed:
                problems.append(
                    f"[workspace] extensions: the entry for {owner} names {target}"
                    " in `without`, and the installed set does not hold it; list"
                    " it, or remove it from `without`"
                )
    if root is not None:
        if why := missing_list(root):
            problems.append(why)
        problems += entry_problems(root)
        problems += listing_problems(root)
        problems += enabled_problems(root)
        problems += root_list_problems(root)
        problems += composition_problems(root)
    return problems


#: An entry of the root's list as the contract holds it: a name, or a
#: table of the name and the targets it leaves out.
Entry = str | dict[str, Any]


@dataclass(frozen=True)
class _RootList:
    """The root's ``[workspace] extensions`` as written, and its canonical form.

    Attributes:
        written: Each entry as the contract holds it, in the written order.
        wanted: Each entry of the canonical list, with the index of the
            written entry it comes from.
        why: Why the two differ, after a semicolon; empty when they agree.
    """

    written: tuple[Entry, ...]
    wanted: tuple[tuple[int, Entry], ...]
    why: str


def _root_list(root: Path) -> _RootList | str | None:
    """The root's list and its canonical form; a cycle's refusal; None when not judged.

    Judged once every entry is the name of an installed extension, or a
    table of that name and ``without``: the other problems name any
    other entry first. The canonical list holds each extension of the
    root's set once, in composition order. It leaves out an entry
    another listed extension requires, unless the entry carries a
    ``without``: an implied extension's opt-out has no other place to
    be written. A table entry whose ``without`` is empty or absent is
    written as the name alone.
    """
    from livery.workshop._composition import implied, order

    written: tuple[Entry, ...] = tuple(_listed(root))
    names: list[str] = []
    for entry in written:
        name = _spelling(entry)
        if isinstance(entry, dict) and not _judged_table(entry):
            return None
        if listing(name).name != name or installed_declaration(name) is None:
            return None
        names.append(name)
    members = closure(names, installed_declaration)
    try:
        ordered = order(members, installed_declaration)
    except OrderCycle as error:
        return f"[workspace] extensions: {error}"
    required = implied(members, installed_declaration)

    def canonical(name: str, entry: Entry) -> Entry:
        return entry if isinstance(entry, dict) and entry.get("without") else name

    chosen: dict[str, tuple[int, Entry]] = {}
    for index, (name, entry) in enumerate(zip(names, written, strict=True)):
        wanted = canonical(name, entry)
        held = chosen.get(name)
        if held is None or (isinstance(wanted, dict) and isinstance(held[1], str)):
            chosen[name] = (index, wanted)
    kept = tuple(
        chosen[name]
        for name in ordered
        if name in chosen
        and (name not in required or isinstance(chosen[name][1], dict))
    )
    if [entry for _index, entry in kept] == list(written):
        return _RootList(written, kept, "")
    gone = [name for name in chosen if name not in {_spelling(e) for _i, e in kept}]
    plain = [
        _spelling(written[index])
        for index, entry in kept
        if isinstance(entry, str) and isinstance(written[index], dict)
    ]
    if gone:
        verb = "is" if len(gone) == 1 else "are"
        why = f"; {', '.join(gone)} {verb} required by another listed extension"
    elif len(plain) == 1:
        why = (
            f"; the entry for {plain[0]} leaves out no target, which the name"
            " alone says"
        )
    elif plain:
        why = (
            f"; the entries for {' and '.join(plain)} leave out no target,"
            " which a name alone says"
        )
    elif len(chosen) != len(names):
        why = "; it lists an extension twice"
    else:
        why = (
            "; its order is composition order: each extension after what it"
            " requires, ties alphabetical"
        )
    return _RootList(written, kept, why)


def _judged_table(entry: dict[str, Any]) -> bool:
    """Whether the root's table entry *entry* is a name and a ``without`` of names.

    Any other table is the contract judge's to name, and the list is
    judged canonical once it is gone.
    """
    targets = entry.get("without", [])
    return (
        set(entry) <= {"name", "without"}
        and isinstance(targets, list)
        and all(isinstance(target, str) for target in cast("list[object]", targets))
    )


def _spelled_entries(entries: Iterable[Entry]) -> str:
    """*entries* as a contract spells the root's list."""
    spelled: list[str] = []
    for entry in entries:
        if isinstance(entry, str):
            spelled.append(f'"{entry}"')
            continue
        parts = [f'name = "{_spelling(entry)}"']
        if "without" in entry:
            targets = ", ".join(f'"{target}"' for target in entry["without"])
            parts.append(f"without = [{targets}]")
        spelled.append("{ " + ", ".join(parts) + " }")
    return "[" + ", ".join(spelled) + "]"


def root_list_problems(root: Path) -> list[str]:
    """Why ``[workspace] extensions`` is not its canonical list; empty when it is.

    The canonical list holds each extension once, in composition order.
    It leaves out an entry another listed extension requires unless the
    entry carries a ``without``, and writes a table entry whose
    ``without`` is empty or absent as the name alone. No other order
    means anything, so the list is written in this one, which the
    gate's ``--fix`` writes. A list whose set's declared order has no
    start names the cycle. An entry that is not an installed
    extension's name is named by ``entry_problems`` and
    ``listing_problems``, and the list is judged once they pass.
    """
    found = _root_list(root)
    if found is None:
        return []
    if isinstance(found, str):
        return [found]
    if not found.why:
        return []
    wanted = (entry for _index, entry in found.wanted)
    return [
        f"[workspace] extensions is {_spelled_entries(found.written)}, and its"
        f" canonical list is {_spelled_entries(wanted)}{found.why}; the gate's"
        " --fix rewrites it"
    ]


def _composable(listed: tuple[str, ...]) -> bool:
    """Whether every entry of a package's list is a name something installed declares.

    A package whose list holds anything else is
    [livery.workshop._extensions.entry_problems][]'s to name, and its
    composition is judged once that is fixed.
    """
    return all(
        listing(entry).name == entry and installed_declaration(entry) is not None
        for entry in listed
    )


def _spelled(names: tuple[str, ...] | list[str]) -> str:
    """*names* as a contract spells the list."""
    return "[" + ", ".join(f'"{name}"' for name in names) + "]"


def composition_problems(root: Path) -> list[str]:
    """Why each package's list is not a valid composition in its canonical form.

    Judged for each package whose entries are names installed
    distributions declare: every extension its set requires is
    installed, each lifecycle phase can run as the set's extensions
    declare it ([livery.workshop._phases.phase_problems][]), each entry
    no other entry requires composes something in a package, itself or
    through what it requires (`why_inert`), the set's declared order
    has a start, and the list is the canonical one
    ([livery.workshop._composition.canonical][]). The gate's ``--fix``
    writes the canonical list and moves an entry that composes nothing
    to ``[workspace] extensions`` (`write_extensions`). A requirement
    joins the set whatever it declares, so no other list needs to name
    it. A cycle of requirements shows in every package whose set holds
    one of its members, since a set is closed under requires.
    """
    from livery.workshop._composition import canonical, implied
    from livery.workshop._phases import lifecycle_phases, phase_problems, phase_steps

    problems: list[str] = []
    inert: dict[str, str] = {}
    for contract, listed in _package_lists(root):
        if not _composable(listed):
            continue
        where = contract.parent.relative_to(root).as_posix()
        names = list(listed)
        members = closure(names, installed_declaration)
        for name in members:
            found = installed_declaration(name)
            for needed in found.requires if found is not None else ():
                if installed_declaration(needed) is None:
                    problems.append(
                        f"{where}: {name} requires {needed}, which no installed"
                        f" distribution declares in {GROUP}; install it"
                    )
        for phase in lifecycle_phases():
            steps = phase_steps(phase, members, installed_declaration)
            problems += [f"{where}: {line}" for line in phase_problems(phase, steps)]
        required = implied(members, installed_declaration)
        for name in dict.fromkeys(names):
            if name in required:
                continue
            if name not in inert:
                inert[name] = why_inert(name)
            if inert[name]:
                problems.append(
                    f"{where}: `extensions` lists {name}, which composes nothing in a"
                    f" package: {inert[name]}; list it in [workspace] extensions,"
                    " where the gate's --fix moves it"
                )
        try:
            wanted = canonical(names, installed_declaration)
        except OrderCycle as error:
            problems.append(f"{where}: {error}")
            continue
        if tuple(names) != wanted:
            problems.append(
                f"{where}: `extensions` is {_spelled(names)}, and its canonical list"
                f" is {_spelled(wanted)}{_why(names, wanted)}; the gate's --fix"
                " rewrites it"
            )
    return problems


def _why(names: list[str], wanted: tuple[str, ...]) -> str:
    """Why a package's list differs from its canonical one, after a semicolon."""
    implied = [name for name in dict.fromkeys(names) if name not in wanted]
    if implied:
        verb = "is" if len(implied) == 1 else "are"
        return f"; {', '.join(implied)} {verb} required by another listed extension"
    if len(set(names)) != len(names):
        return "; it lists an extension twice"
    return (
        "; its order is composition order: each extension after what it requires,"
        " ties alphabetical"
    )


def listing_problems(root: Path) -> list[str]:
    """Each ``[workspace] extensions`` entry spelled as more than a name.

    An entry is a name. It takes no brackets, version, ``?`` or scope:
    an extension's version comes from its wheel's metadata, and the
    lock pins it. An extension no installed distribution declares is
    [livery.workshop._extensions.entry_problems][]'s to name.
    """
    from livery.toolroom.store import Spec, SpecError

    problems: list[str] = []
    for entry in _listed(root):
        spelled = _spelling(entry)
        try:
            spec = Spec.parse(spelled, where="[workspace] extensions")
        except SpecError:
            spec = None
        if spec is None or spec.options or spec.optional or spec.floor or spec.scope:
            problems.append(
                f"[workspace] extensions lists {spelled!r}; an entry is a name alone,"
                " with no brackets, version or scope: the lock pins an extension's"
                " version from its wheel"
            )
    return problems


_LISTS: dict[tuple[Path, str], tuple[str, ...]] = {}


def _package_lists(root: Path) -> list[tuple[Path, tuple[str, ...]]]:
    """Each package contract under *root* with the extensions it lists.

    Read raw, as the root's list is, since the mount reads it before any
    verb judges a contract. A contract that is not TOML, or whose
    ``extensions`` is no list, lists nothing here: the judge names it
    when the package is read, and every command, ``fm sync`` among
    them, still mounts. Each contract is parsed once per content: every
    command's mount asks, and a package's list changes only when its
    text does.
    """
    from livery.workshop._packages import package_directories

    found: list[tuple[Path, tuple[str, ...]]] = []
    for directory in package_directories(root):
        contract = directory / "workshop.toml"
        if not contract.is_file():
            continue
        text = contract.read_text("utf-8")
        listed = _LISTS.get((contract, text))
        if listed is None:
            try:
                table = tomllib.loads(text)
            except tomllib.TOMLDecodeError:
                table = {}
            entries = table.get("extensions", [])
            listed = (
                tuple(str(name) for name in entries)
                if isinstance(entries, list)
                else ()
            )
            _LISTS[(contract, text)] = listed
        found.append((contract, listed))
    return found


def entry_problems(root: Path) -> list[str]:
    """Each listed name nothing installed declares, and each package entry with options.

    Every extension is one kind, which either list names. A package's
    entry is the extension's name alone, with no options.
    """
    problems: list[str] = []
    for name in workspace_names(root):
        if declaration(name) is None:
            problems.append(
                f"[workspace] extensions lists {name}, which no installed"
                f" distribution declares in {GROUP}"
            )
    for contract, listed in _package_lists(root):
        where = contract.parent.relative_to(root).as_posix()
        for name in listed:
            if listing(name).name != name:
                problems.append(
                    f"{where}: `extensions` lists {name!r}; a package's entry is the"
                    " extension's name alone, and takes no options"
                )
                continue
            if declaration(name) is None:
                problems.append(
                    f"{where}: `extensions` lists {name}, which no installed"
                    f" distribution declares in {GROUP}"
                )
    return problems


def requirers(start: Path | None = None) -> dict[str, tuple[str, ...]]:
    """Each listed extension to the listed extensions that depend on it."""
    found: dict[str, list[str]] = {name: [] for name in extension_names(start)}
    for extension, depends in extension_dependencies(start).items():
        for needed in depends:
            found.setdefault(needed, []).append(extension)
    return {name: tuple(who) for name, who in found.items()}


def write_extensions(root: Path) -> list[str]:
    """Write each package's list and the root's in its canonical form.

    Returns the lines written, one per list changed.

    Each package's list becomes its canonical one
    ([livery.workshop._composition.canonical][]) without the entries
    that compose nothing in a package (`why_inert`), which move to the
    root's list, and the root's list then becomes its canonical one
    (``root_list_problems``). Each entry keeps the comment lines above
    it and the comment after it on its line. A moved entry takes them
    to the root's list when that list does not hold it yet, and an
    entry left out takes its comments with it. A list the judge refuses
    for another reason, one whose order has no start, and one whose
    array this does not read are left for a person, and the judge names
    each; no entry moves while the root's array is not read.
    Idempotent. A root without a contract lists no extensions, so
    nothing is written.
    """
    from livery.workshop._composition import canonical

    contract = root / "workshop.toml"
    if not contract.is_file():
        return []
    movable = _read_array(contract.read_text(encoding="utf-8"), "workspace") is not None
    moved: dict[str, _Entry] = {}
    inert: dict[str, str] = {}
    written: list[str] = []
    for package_contract, listed in _package_lists(root):
        if not _composable(listed):
            continue
        where = package_contract.parent.relative_to(root).as_posix()
        try:
            wanted = canonical(listed, installed_declaration)
        except OrderCycle:
            continue
        leaving: list[str] = []
        for name in wanted if movable else ():
            if name not in inert:
                inert[name] = why_inert(name)
            if inert[name]:
                leaving.append(name)
        kept = tuple(name for name in wanted if name not in leaving)
        if kept == listed:
            continue
        package_text = package_contract.read_text(encoding="utf-8")
        array = _read_array(package_text, None)
        if array is None or not _reads_as(array, listed):
            continue
        # The canonical list holds each extension once, each one listed.
        entries = [array.entries[listed.index(name)] for name in kept]
        package_contract.write_text(
            package_text[: array.start]
            + _array_text(array, entries)
            + package_text[array.end :],
            encoding="utf-8",
        )
        for name in leaving:
            moved.setdefault(name, array.entries[listed.index(name)])
        if leaving:
            verb = "composes" if len(leaving) == 1 else "compose"
            written.append(
                f"  layering: {where} `extensions` is {_spelled(kept)}, and"
                f" [workspace] extensions lists {_spoken(leaving)}, which {verb}"
                " nothing in a package"
            )
        else:
            written.append(
                f"  layering: {where} `extensions` is {_spelled(kept)},"
                " its canonical list"
            )
    return _write_root(root, moved) + written


def _write_root(root: Path, moved: dict[str, _Entry]) -> list[str]:
    """Add *moved* to the root's list, then write it in its canonical form.

    *moved* maps each extension a package's list gave up to its entry
    there, with its comments; an extension the root's list holds
    already is not added again. Returns the line written for the
    canonical rewrite, if any: the line of each package's list names
    what moved.
    """
    contract = root / "workshop.toml"
    text = contract.read_text(encoding="utf-8")
    held = set(workspace_names(root))
    added = [entry for name, entry in moved.items() if name not in held]
    array = _read_array(text, "workspace")
    if added and array is not None:
        text = (
            text[: array.start]
            + _array_text(array, [*array.entries, *added])
            + text[array.end :]
        )
        contract.write_text(text, encoding="utf-8")
    found = _root_list(root)
    if not isinstance(found, _RootList) or not found.why:
        return []
    array = _read_array(text, "workspace")
    if array is None or not _reads_as(array, found.written):
        return []
    entries = [
        replace(array.entries[index], text=f'"{entry}"')
        if isinstance(entry, str) and isinstance(found.written[index], dict)
        else array.entries[index]
        for index, entry in found.wanted
    ]
    contract.write_text(
        text[: array.start] + _array_text(array, entries) + text[array.end :],
        encoding="utf-8",
    )
    spelled = _spelled_entries(entry for _index, entry in found.wanted)
    return [f"  layering: [workspace] extensions is {spelled}, its canonical list"]


def list_in_root(root: Path, names: Iterable[str]) -> list[str]:
    """Add *names* to *root*'s ``[workspace] extensions``, as its canonical list.

    Each name the list does not hold yet joins it as an entry of its
    own, and the list is then written in its canonical form, as the
    gate's ``--fix`` writes it (`write_extensions`): each entry keeps
    its comments, and an entry another listed extension requires
    leaves the list. A list naming an extension no installed
    distribution declares keeps the order it is given, and the gate
    names it. Returns what `write_extensions` returns: a line for each
    list it wrote.

    Raises:
        Failed: when the contract has no ``[workspace] extensions``
            array this reads.
    """
    from livery.footman import fail

    contract = root / "workshop.toml"
    text = contract.read_text(encoding="utf-8")
    array = _read_array(text, "workspace")
    if array is None:
        fail(f"{contract}: no [workspace] extensions array to add to")
    held = set(workspace_names(root))
    added = [_Entry(f'"{name}"') for name in dict.fromkeys(names) if name not in held]
    if added:
        contract.write_text(
            text[: array.start]
            + _array_text(array, [*array.entries, *added])
            + text[array.end :],
            encoding="utf-8",
        )
    return write_extensions(root)


@dataclass(frozen=True)
class _Entry:
    """One entry of an ``extensions`` array as written, with its comments.

    Attributes:
        text: The entry itself, ``"ruff"`` or
            ``{ name = "pytest", without = ["coverage"] }``.
        above: The whole lines between the entry before it and this one,
            its comment lines and blank lines, as written.
        after: The comment after the entry on its own line; empty without one.
    """

    text: str
    above: tuple[str, ...] = ()
    after: str = ""


@dataclass(frozen=True)
class _Array:
    """An ``extensions`` array as a contract writes it, read for a rewrite.

    Attributes:
        start: Where the key's line starts in the contract's text.
        end: Where the array ends, after its closing bracket.
        key: The text from the line's start to the opening bracket.
        head: The comment after the opening bracket on its line; empty
            without one.
        entries: The entries, in the written order.
        tail: The whole lines after the last entry, before the closing
            bracket.
        indent: What stands before an entry on a line of its own.
        multiline: Whether the array spans more than one line.
    """

    start: int
    end: int
    key: str
    head: str
    entries: tuple[_Entry, ...]
    tail: tuple[str, ...]
    indent: str
    multiline: bool


#: A table header: the line's first character after its indent is ``[``.
_HEADER = re.compile(r"^[ \t]*\[", re.M)

#: The ``extensions`` key, up to its array's opening bracket.
_KEY = re.compile(r"^[ \t]*extensions[ \t]*=[ \t]*\[", re.M)


def _region(text: str, table: str | None) -> tuple[int, int] | None:
    """Where *table*'s keys stand in *text*; the top level's for None.

    None when *text* has no header for *table*.
    """
    if table is None:
        header = _HEADER.search(text)
        return 0, header.start() if header is not None else len(text)
    found = re.compile(
        rf"^[ \t]*\[[ \t]*{re.escape(table)}[ \t]*\][ \t]*(?:#.*)?$", re.M
    ).search(text)
    if found is None:
        return None
    after = _HEADER.search(text, found.end())
    return found.end(), after.start() if after is not None else len(text)


def _read_array(text: str, table: str | None) -> _Array | None:
    """The ``extensions`` array of *table* in *text*, as written; None when not read.

    *table* is ``workspace`` for the root's list and None for a
    package's, which stands at the top level. An entry is a string or
    an inline table; an array holding anything else, a multi-line
    string among them, is not read, and neither is a key spelled
    otherwise.
    """
    region = _region(text, table)
    if region is None:
        return None
    key = _KEY.search(text, *region)
    if key is None:
        return None
    entries: list[_Entry] = []
    pending: list[str] = []
    head = ""
    indent = ""
    line = key.start()
    # Whether the line read holds the opening bracket, holds an entry or
    # a comma, and holds nothing but space so far.
    first, taken, blank = True, False, True
    at = key.end()
    while at < len(text):
        char = text[at]
        if char in " \t\r":
            at += 1
        elif char == "\n":
            if blank and not first:
                pending.append("")
            at += 1
            line, first, taken, blank = at, False, False, True
        elif char == "]":
            return _Array(
                key.start(),
                at + 1,
                key.group(0),
                head,
                tuple(entries),
                tuple(pending),
                indent or "    ",
                "\n" in text[key.end() : at],
            )
        elif char == "#":
            stop = text.find("\n", at)
            stop = len(text) if stop < 0 else stop
            if taken and entries:
                entries[-1] = replace(entries[-1], after=text[at:stop].rstrip())
            elif first:
                head = text[at:stop].rstrip()
            else:
                pending.append(text[line:stop].rstrip())
            blank = False
            at = stop
        elif char == ",":
            taken, blank = True, False
            at += 1
        else:
            end = _item_end(text, at)
            if end is None:
                return None
            if not taken and not first and not indent:
                indent = text[line:at]
            entries.append(_Entry(text[at:end], tuple(pending)))
            pending = []
            taken, blank = True, False
            at = end
    return None


def _item_end(text: str, at: int) -> int | None:
    """Where the string or inline table at *at* ends; None for anything else.

    A multi-line string is not read. The text parses as TOML already,
    so every string and table here closes.
    """
    char = text[at]
    if char in "\"'":
        if text.startswith(char * 3, at):
            return None
        index = at + 1
        while index < len(text) and text[index] != char:
            index += 2 if char == '"' and text[index] == "\\" else 1
        return index + 1
    if char != "{":
        return None
    depth = 0
    index = at
    while index < len(text):
        char = text[index]
        if char in "\"'":
            stop = _item_end(text, index)
            if stop is None:
                return None
            index = stop
            continue
        if char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return None


def _reads_as(array: _Array, values: Iterable[object]) -> bool:
    """Whether *array*'s entries are *values*, one for one, as TOML reads them."""
    found = list(values)
    if len(array.entries) != len(found):
        return False
    # The text parses as TOML already, so each entry's text does too.
    return all(
        tomllib.loads(f"value = {entry.text}")["value"] == value
        for entry, value in zip(array.entries, found, strict=True)
    )


def _array_text(array: _Array, entries: Iterable[_Entry]) -> str:
    """*array* written again holding *entries*, each with its comments.

    One line stays one line. A multi-line array puts each entry on a
    line of its own, after the lines above it and with its comment
    after it, and keeps the comment after its opening bracket and the
    lines before its closing one.
    """
    if not array.multiline:
        return array.key + ", ".join(entry.text for entry in entries) + "]"
    margin = array.key[: len(array.key) - len(array.key.lstrip())]
    lines = [array.key + (f"  {array.head}" if array.head else "")]
    for entry in entries:
        lines += entry.above
        after = f"  {entry.after}" if entry.after else ""
        lines.append(f"{array.indent}{entry.text},{after}")
    lines += array.tail
    lines.append(f"{margin}]")
    return "\n".join(lines)


def available_extensions(
    listed: tuple[str, ...], advertised: list[tuple[str, str]] | None = None
) -> list[tuple[str, str]]:
    """Installed extensions the contract does not list, as (name, distribution).

    *advertised* is every ``workshop.extensions`` entry point as
    (name, distribution); read from the installed metadata when None.
    A footman plugin that declares no extension is not one, and is
    never offered. Listing is the only activation channel, so these
    do nothing until the contract names them, and the doctor says so.
    """
    if advertised is None:
        advertised = [
            (name, entry.dist.name if entry.dist is not None else "")
            for name, entry in sorted(_declared().items())
        ]
    return [(name, dist) for name, dist in advertised if name not in listed]
