"""The extension walk: the lists in the contracts, every channel.

The root ``workshop.toml`` names the workspace's extensions in
precedence order, the workshop first and the instance implicitly last,
and each package's ``workshop.toml`` names the package-level extensions
it is composed of. Mounting reads those lists: the base, then the
package-level extensions in composition order over every package's
set, then the workspace list in its order. So a package installed by
accident never changes a repository: discovery is the lists and
nothing else.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from livery.footman import prog
from livery.workshop._composition import OrderCycle, order, package_set
from livery.workshop._declaration import (
    API_VERSION,
    FILE,
    Additions,
    Declaration,
    DeclarationError,
    DeclaredSlot,
    Reference,
    declaration_file,
    read,
)

if TYPE_CHECKING:
    from importlib.metadata import Distribution, EntryPoint

    from livery.toolroom.store import Spec
    from livery.workshop._packages import Package
    from livery.workshop._points import JobContribution

#: The base. Never listed and never mounted by name: importing its
#: plugin module is the base arriving.
SELF = "livery.workshop"

#: The entry point group an extension declares itself in: its name
#: maps to the package that ships its declaration file.
GROUP = "workshop.extensions"

#: The levels an extension may be listed at.
WORKSPACE = "workspace"
PACKAGE = "package"


def workspace_root(start: Path | None = None) -> Path | None:
    """The nearest ancestor carrying a ``workshop.toml``, or None.

    The workspace contract is the marker; a checkout without one is
    not a workspace and gets no extensions.
    """
    origin = (start or Path.cwd()).resolve()
    for candidate in (origin, *origin.parents):
        if (candidate / "workshop.toml").is_file():
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


def extension_extras(extension: str, present: tuple[str, ...]) -> tuple[str, ...]:
    """The extras of *extension*'s wheel that *present* puts in use, sorted.

    One per extension of *present* that *extension* declares compatible
    or contributes to through ``[for.<target>]``: its wheel declares
    each such claim as an extra requiring the target's distribution,
    so installing the extra holds the two to the range the wheel
    states. Empty for an extension nothing installed declares.
    """
    found = _readable(extension)
    if found is None:
        return ()
    claimed = (*found.compatible, *found.target_tables)
    return tuple(sorted({normal_name(name) for name in claimed if name in present}))


def declaration(extension: str) -> Declaration | None:
    """*extension*'s declaration file, read and judged; None when none is installed.

    Read without importing anything of the extension: footman's
    ``plugin()`` stays a plugin's first importer, and a check's code
    is imported when the check runs.

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
    if extension == SELF:
        return None
    entry = _declared().get(extension)
    if entry is None or declaration_file(entry.value.partition(":")[0]) is None:
        return None
    return declaration(extension)


def levels_of(extension: str) -> tuple[str, ...]:
    """The levels *extension* may be listed at; the workspace when it says none."""
    found = declaration(extension)
    return found.levels if found is not None else ()


def distribution_of(extension: str) -> str:
    """The distribution that ships *extension*: its entry point's, else by convention.

    An extension no installed distribution declares, a newborn's list
    written before its environment exists, say, is taken at the
    convention: an import path names its distribution with its dots as
    hyphens, and a short name is one of the base's own family,
    ``<namespace>-extensions-<name>``, never the index's package of
    that bare name.
    """
    entry = _declared().get(extension)
    if entry is not None and entry.dist is not None:
        return entry.dist.name
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

    An entry is a name, or a name with the options it turns on,
    ``basedpyright[typecomplete]``. A spelling the grammar does not
    read is taken whole as a name: no distribution declares it, so
    the mount names it, and the layering check refuses the spelling.
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


def _spelling(entry: Any) -> str:
    """How an entry spells its extension: the string, or a table entry's ``name``."""
    return str(entry.get("name", "")) if isinstance(entry, dict) else str(entry)


def workspace_entries(start: Path | None = None) -> tuple[tuple[str, str], ...]:
    """Each entry of ``[workspace] extensions`` as ``(name, distribution)``, in order.

    An entry is a name, or a table ``{ name = "...", for = [...] }``;
    either name may carry options, which
    [livery.workshop._extensions.extension_options][] reads. Empty
    outside a workspace and when the contract has no list: the mount
    refuses that, and a reader has nothing to read.
    """
    entries: list[tuple[str, str]] = []
    for entry in _listed(start):
        name = listing(_spelling(entry)).name
        entries.append((name, distribution_of(name)))
    return tuple(entries)


def workspace_names(start: Path | None = None) -> tuple[str, ...]:
    """The extensions ``[workspace] extensions`` lists, in its order."""
    return tuple(name for name, _ in workspace_entries(start))


def _lookup(name: str) -> Declaration | None:
    """*name*'s declaration where one is installed; None otherwise."""
    return _readable(name)


def package_extensions(start: Path | None = None) -> tuple[str, ...]:
    """The package-level extensions every package's set holds, in composition order.

    Each package's set is its own list and the package-level extensions
    the list requires; this is their union, ordered as
    [livery.workshop._composition.order][] orders it. A declared order
    with no start falls back to alphabetical here, so a sync still
    runs, and the layering check names the cycle. Empty outside a
    workspace.
    """
    root = workspace_root(start)
    if root is None:
        return ()
    listed = [
        listing(name).name
        for _contract, names in _package_lists(root)
        for name in names
    ]
    members = package_set(listed, _lookup)
    try:
        return order(members, _lookup)
    except OrderCycle:
        return tuple(sorted(members))


def composed_set(listed: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """The installed package-level extensions a package's list *listed* stands for.

    The list and the package-level extensions it requires, as
    [livery.workshop._composition.package_set][] reads them from the
    installed declarations; an entry's options are read past.
    """
    return package_set([listing(entry).name for entry in listed], _lookup)


def extension_entries(start: Path | None = None) -> tuple[tuple[str, str], ...]:
    """Every listed extension as ``(name, distribution)``, in mount order.

    The package-level extensions every package's set holds come first,
    in composition order ([livery.workshop._extensions.package_extensions][]),
    then ``[workspace] extensions`` in its order; an extension listed at
    both levels takes its first place. Empty outside a workspace.
    """
    entries = {name: distribution_of(name) for name in package_extensions(start)}
    for name, dist in workspace_entries(start):
        entries.setdefault(name, dist)
    return tuple(entries.items())


def extension_options(start: Path | None = None) -> dict[str, tuple[str, ...]]:
    """Each listed extension's options, as its entry spells them, in list order.

    ``basedpyright[typecomplete]`` lists ``basedpyright`` with
    ``typecomplete``; an entry without brackets has none. Empty
    outside a workspace.
    """
    found: dict[str, tuple[str, ...]] = {}
    for entry in _listed(start):
        spec = listing(_spelling(entry))
        found[spec.name] = spec.options
    return found


def declared_options(extension: str) -> dict[str, str]:
    """The options *extension* declares, each name to what it turns on.

    Empty for the base, for an extension that declares none, and for
    one no installed distribution declares.
    """
    found = _readable(extension)
    return {} if found is None else dict(found.options)


def _undeclared(extension: str, options: tuple[str, ...]) -> str:
    """Why *extension* cannot take *options*; empty when it declares every one."""
    known = declared_options(extension)
    unknown = [option for option in options if option not in known]
    if not unknown:
        return ""
    return (
        f"[workspace] extensions lists {extension} with"
        f" {', '.join(repr(option) for option in unknown)}, which it does not"
        f" declare; its options are {', '.join(known) or 'none'}"
    )


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
    """The extensions the workspace's contracts list, in mount order.

    The package-level extensions every package's set holds, then
    ``[workspace] extensions``; ``workspace_names`` reads the workspace
    list alone.
    """
    return tuple(name for name, _ in extension_entries(start))


def extension_content(extension: str) -> Path | None:
    """The installed extension's ``content/`` directory, or None.

    Beside the extension's declaration file, or the base's own for
    the base. In the monorepo the "wheel" is the editable source tree,
    which is what lets the materialised links point back into the
    repository. None for an extension that is not installed or ships
    no content.
    """
    if extension == SELF:
        content = Path(__file__).resolve().parent / "content"
        return content if content.is_dir() else None
    found = declaration(extension)
    if found is None:
        return None
    content = found.path.resolve().parent / "content"
    return content if content.is_dir() else None


def shipped_content(start: Path | None = None) -> tuple[tuple[str, Path], ...]:
    """Each extension of the workspace's stack with its ``content/`` directory.

    The base first, then the listed extensions in list order, each with
    the directory its shipped files live in; an extension that ships no
    content, or that no installed distribution declares, is left out.
    """
    found: list[tuple[str, Path]] = []
    for extension in stack_names(start):
        content = extension_content(extension)
        if content is not None:
            found.append((extension, content))
    return tuple(found)


def extension_targets(start: Path | None = None) -> dict[str, tuple[str, ...] | None]:
    """Each listed extension's ``for`` list from its contract entry; None without one.

    A table entry may carry ``for = [...]``, the targets the extension
    contributes to, written once by the layering check's fix and a
    person's to edit from then on: a name deleted from it stays
    deleted, which is a project's opt-out from that target's
    opinions.
    """
    found: dict[str, tuple[str, ...] | None] = {}
    for entry in _listed(start):
        name = listing(_spelling(entry)).name
        targets = entry.get("for") if isinstance(entry, dict) else None
        found[name] = None if targets is None else tuple(str(t) for t in targets)
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


def mount_extensions(start: Path | None = None) -> tuple[str, ...]:
    """Mount every listed extension's plugin, in mount order; the names mounted.

    The package-level extensions every package's set holds mount first,
    in composition order, then ``[workspace] extensions`` in its order,
    so a workspace extension finds a package-level one it names already
    registered. Refuses an extension declared for another workshop API
    version, and one whose declaration file says something this workshop
    cannot take.
    A contract without ``[workspace] extensions``, an extension no
    installed distribution declares, one whose installed package ships
    no declaration file, and one listed at a level it does not declare
    are named on stderr and skipped, never refused:
    the mount runs on every command, ``fm sync`` among them, which is
    what installs a missing declaration, so a refusal here would stop
    the command that repairs it. An option the extension does not
    declare is named the same way and left off. The gate's layering
    check and ``fm extensions`` refuse the same problems. An extension
    whose declaration names no ``plugin`` registers what it declares
    and mounts no verbs. A plugin a branded App already mounts as a
    builtin is not mounted again, since claiming its tasks again puts
    the same task in one rung twice; what its declaration adds
    registers as any other extension's does.
    """
    # footman does not expose the brand's builtin set publicly; this
    # private read is one of the reaches issue #1204 closes with a seam.
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
    options = extension_options(start)
    # The base is present before this walk; every extension joins once
    # its mount ran.
    present = [SELF]
    grafted: set[tuple[str, str]] = set()
    _graft_contributions(present, declared, active, grafted, options)
    packaged = package_extensions(start)
    for extension, dist in extension_entries(start):
        level = PACKAGE if extension in packaged else WORKSPACE
        if extension in builtin:
            # The App mounted it as its own builtin: its plugin is never
            # mounted here a second time, and what its declaration adds
            # registers all the same.
            found = _readable(extension)
            if found is not None:
                check_api_version(extension, found)
                declare_slots(extension, found.slots)
                notes = declare_release_notes(extension, found.release_notes)
                if (
                    register_declared(
                        extension, found.additions, options.get(extension, ())
                    )
                    or notes
                ):
                    mounted.append(extension)
            present.append(extension)
            _graft_contributions(present, declared, active, grafted, options)
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
            _note(
                f"extension {extension!r} is listed in [workspace] extensions, and no"
                f" installed distribution declares it in {GROUP}; install the"
                f" distribution that ships it ({dist} by its name), or remove the"
                f" entry; `{prog()} sync` installs one this workspace builds"
            )
            continue
        check_api_version(extension, found)
        # A package's set holds package-level extensions alone, so only a
        # workspace entry can name the wrong level here.
        if level not in found.levels:
            _note(
                f"extension {extension!r} is listed in [workspace] extensions, and it"
                f" declares the levels {', '.join(found.levels) or 'none'};"
                " list it in each package's `extensions` instead"
            )
            continue
        listed = options.get(extension, ())
        if why := _undeclared(extension, listed):
            _note(f"{why}; the mount leaves it off")
        declare_slots(extension, found.slots)
        notes = declare_release_notes(extension, found.release_notes)
        if register_declared(extension, found.additions, listed) or notes:
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
        _graft_contributions(present, declared, active, grafted, options)
    # An extension's checks registered as it mounted; their verbs join the
    # ones the workshop generated as it loaded.
    from livery.workshop._checks import generate_verbs

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


def _note(text: str) -> None:
    """Name a problem the mount skips past, on stderr."""
    import sys

    print(f"  note: {text}", file=sys.stderr)


def _graft_contributions(
    present: list[str],
    declared: dict[str, dict[str, Additions]],
    active: dict[str, tuple[str, ...]],
    grafted: set[tuple[str, str]],
    options: dict[str, tuple[str, ...]],
) -> None:
    """Register every ``[for.<target>]`` table whose owner and target are both present.

    Called after each extension mounts, so a contribution lands whichever
    of the two mounts later. A ``for`` entry naming a target the owner
    declares nothing for registers nothing: the layering check names
    that entry, and it can only run inside a mount that went on. A check
    in the table registers under the options the owner's listing turns
    on, *options*, as a check at the file's top level does.
    """
    for owner in present:
        for target in active.get(owner, ()):
            if target not in present or (owner, target) in grafted:
                continue
            additions = declared.get(owner, {}).get(target)
            if additions is None:
                continue
            register_declared(owner, additions, options.get(owner, ()))
            grafted.add((owner, target))


def register_declared(
    extension: str, additions: Additions, options: tuple[str, ...] = ()
) -> bool:
    """Register what *additions* declares for *extension*; whether it declares a check.

    Each check registers under the extension's listed name, since the
    list is what decides which extension a check belongs to, and each
    value goes into its slot and each job joins its point under the
    same name. A check that names an option in ``listed-with`` registers
    only when *options*, the entry's, turn it on.
    """
    from dataclasses import replace

    from livery.workshop import _slots
    from livery.workshop._checks import register_check

    for record in additions.checks:
        if record.listed_with and record.listed_with not in options:
            continue
        register_check(replace(record, extension=extension))
    for slot in {slot for slot, _ in additions.contributions}:
        _slots.withdraw(slot, by=extension)
    for slot, value in additions.contributions:
        _slots.contribute(slot, value, extension=extension, by=extension)
    register_jobs(extension, additions.jobs)
    return bool(additions.checks)


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


def declare_release_notes(extension: str, reference: Reference | None) -> bool:
    """Make the provider *reference* names the release notes' writer, for *extension*.

    The provider is imported when the release train first asks it, so a
    mount imports none of the extension's code.

    Returns:
        Whether a provider was registered: False when *reference* is None.
    """
    if reference is None:
        return False
    from livery.workshop._release_notes import DeclaredNotes, register_release_notes

    register_release_notes(DeclaredNotes(reference), extension=extension)
    return True


def declare_slots(extension: str, slots: tuple[DeclaredSlot, ...]) -> None:
    """Declare *slots* for *extension*, before anything contributes to them."""
    from livery.workshop import _slots

    for slot in slots:
        _slots.register_slot(
            slot.name,
            compose=slot.compose,
            default=slot.default,
            extension=extension,
            values=slot.values,
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

    The tools the extension's own verbs need, such as ``docker>=27``.
    The base and an extension no installed distribution declares
    declare none here.
    """
    found: dict[str, tuple[str, ...]] = {}
    for extension in extension_names(start):
        declared = _readable(extension)
        found[extension] = declared.tools if declared is not None else ()
    return found


def notes_tools(start: Path | None = None) -> dict[str, tuple[str, ...]]:
    """Each listed extension that writes release notes, to its ``[toolroom] requires``.

    A release's entries must not depend on the machine that wrote
    them, so no host allowance reaches these tools
    ([livery.workshop._tools.host_allowed][]).
    """
    found: dict[str, tuple[str, ...]] = {}
    for extension in extension_names(start):
        declared = _readable(extension)
        if declared is not None and declared.release_notes is not None:
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
    """Each listed extension's active targets, in list order.

    The entry's ``for`` list when it has one, since that is the truth
    once written; otherwise every declared target that is listed.
    """
    names = extension_names(start)
    declared = declared_targets(start)
    written = extension_targets(start)
    found: dict[str, tuple[str, ...]] = {}
    for owner in names:
        targets = written.get(owner)
        if targets is None:
            targets = tuple(name for name in names if name in declared.get(owner, {}))
        found[owner] = targets
    return found


def contributions_for(
    target: str, start: Path | None = None
) -> dict[str, dict[str, Any]]:
    """What the listed extensions declare for *target*, by extension, in list order.

    An extension contributes its ``[for.<target>]`` table while
    *target* is listed and the extension's listing contributes to it
    (its ``for`` list, or every listed target it declares a table for
    when it has none), the rule the mount grafts by; an extension
    that requires *target* contributes its top-level tables. The
    tables come as the declaration file holds them, and an extension
    that declares nothing for *target* is left out, as is every
    extension when *target* is not listed. *start* is a directory in
    the workspace, the working directory when absent.
    """
    names = extension_names(start)
    if target not in names:
        return {}
    active = resolved_targets(start)
    found: dict[str, dict[str, Any]] = {}
    for owner in names:
        declared = _readable(owner)
        if declared is None or owner == target:
            continue
        if target in declared.requires:
            found[owner] = dict(declared.tables)
        elif target in active.get(owner, ()) and target in declared.target_tables:
            found[owner] = dict(declared.target_tables[target])
    return found


def describe_extensions(start: Path | None = None) -> list[str]:
    """The lines ``fm extensions`` prints: each extension with what it declares.

    The base, then each extension in mount order: the package-level
    ones with the packages whose sets hold them, then the workspace
    list.
    """
    names = extension_names(start)
    who = requirers(start)
    tools = extension_tools(start)
    targets = resolved_targets(start)
    listed = extension_options(start)
    holders = _holders(start)
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
        for option, text in declared_options(name).items():
            state = "on" if option in listed.get(name, ()) else "off"
            lines.append(f"    option {option} ({state}): {text}")
    return lines


def _holders(start: Path | None) -> dict[str, tuple[str, ...]]:
    """Each package-level extension to the packages whose sets hold it, by path."""
    root = workspace_root(start)
    if root is None:
        return {}
    found: dict[str, list[str]] = {}
    for contract, listed in _package_lists(root):
        where = contract.parent.relative_to(root).as_posix()
        names = [listing(entry).name for entry in listed]
        for name in package_set(names, _lookup):
            found.setdefault(name, []).append(where)
    return {name: tuple(where) for name, where in found.items()}


def _package_only(name: str) -> bool:
    """Whether *name* is installed and may be listed by a package alone."""
    levels = levels_of(name)
    return PACKAGE in levels and WORKSPACE not in levels


def closure_problems(start: Path | None = None) -> list[str]:
    """Why the contracts' lists are not closed under the extensions' declarations.

    Empty when every extension a workspace entry requires is listed
    before it, or is a package-level one some package's set holds;
    when every extension is listed at a level it declares; and when
    every package's list is a valid composition in its canonical form
    ([livery.workshop._extensions.composition_problems][]). A missing
    workspace entry names the fix; one listed after its dependent names
    the move, which is a person's edit.
    """
    root = workspace_root(start)
    names = workspace_names(start)
    order_at = {name: index for index, name in enumerate(names)}
    packaged = set(package_extensions(start))
    problems: list[str] = []
    for extension in names:
        found = _readable(extension)
        for needed in found.requires if found is not None else ():
            if needed in order_at:
                if order_at[needed] > order_at[extension]:
                    problems.append(
                        f"[workspace] extensions lists {needed} after {extension},"
                        f" which depends on it; move {needed} before {extension}"
                    )
            elif needed in packaged:
                continue
            elif _package_only(needed):
                problems.append(
                    f"[workspace] extensions lists {extension}, which requires"
                    f" {needed}, a package-level extension no package lists; list"
                    f" {needed} in the `extensions` of each package {extension}"
                    " serves"
                )
            else:
                problems.append(
                    f"[workspace] extensions lists {extension}, which depends on"
                    f" {needed}, and does not list it; the gate's --fix adds"
                    f" it before {extension}"
                )
    declared = declared_targets(start)
    for owner, targets in extension_targets(start).items():
        for target in targets or ():
            if target not in order_at and target not in packaged:
                problems.append(
                    f"[workspace] extensions: the entry for {owner} names {target}"
                    " in `for`, and does not list it; list it, or remove it"
                    " from `for`"
                )
            elif target not in declared.get(owner, {}):
                problems.append(
                    f"[workspace] extensions: the entry for {owner} names {target}"
                    f" in `for`, and {owner} declares no contribution for it;"
                    " remove it from `for`"
                )
    if root is not None:
        if why := missing_list(root):
            problems.append(why)
        problems += level_problems(root)
        problems += listing_problems(root)
        problems += composition_problems(root)
    return problems


def _composable(listed: tuple[str, ...]) -> bool:
    """Whether every entry of a package's list is an installed package-level name.

    A package whose list holds anything else is
    [livery.workshop._extensions.level_problems][]'s to name, and its
    composition is judged once that is fixed.
    """
    return all(
        listing(entry).name == entry and PACKAGE in levels_of(entry) for entry in listed
    )


def _spelled(names: tuple[str, ...] | list[str]) -> str:
    """*names* as a contract spells the list."""
    return "[" + ", ".join(f'"{name}"' for name in names) + "]"


def composition_problems(root: Path) -> list[str]:
    """Why each package's list is not a valid composition in its canonical form.

    Judged for each package whose entries are installed package-level
    extensions: every extension its set requires is installed and
    listed at its level, every pair in the set knows each other, the
    set's declared order has a start, and the list is the canonical
    one, which the gate's ``--fix`` writes
    ([livery.workshop._composition.canonical][]).
    """
    from livery.workshop._composition import canonical, unconnected

    workspace = set(workspace_names(root))
    problems: list[str] = []
    cycled = False
    every: list[str] = []
    for contract, listed in _package_lists(root):
        if not _composable(listed):
            continue
        where = contract.parent.relative_to(root).as_posix()
        names = list(listed)
        every += names
        members = package_set(names, _lookup)
        for name in members:
            found = _lookup(name)
            for needed in found.requires if found is not None else ():
                if _lookup(needed) is None:
                    problems.append(
                        f"{where}: {name} requires {needed}, which no installed"
                        f" distribution declares in {GROUP}; install it"
                    )
                elif PACKAGE not in levels_of(needed) and needed not in workspace:
                    problems.append(
                        f"{where}: {name} requires {needed}, which [workspace]"
                        " extensions does not list; the gate's --fix adds it"
                    )
        for first, second in unconnected(members, _lookup):
            problems.append(
                f"{where}: `extensions` composes {first} and {second}, and neither"
                " requires the other, contributes to it, nor declares it"
                " compatible; list only one of them, or ask either extension to"
                " declare the other compatible"
            )
        try:
            wanted = canonical(names, _lookup)
        except OrderCycle as error:
            problems.append(f"{where}: {error}")
            cycled = True
            continue
        if tuple(names) != wanted:
            problems.append(
                f"{where}: `extensions` is {_spelled(names)}, and its canonical list"
                f" is {_spelled(wanted)}{_why(names, wanted)}; the gate's --fix"
                " rewrites it"
            )
    if not cycled:
        try:
            order(package_set(every, _lookup), _lookup)
        except OrderCycle as error:
            problems.append(f"the package-level extensions the packages list: {error}")
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
        "; its order is composition order: each extension after what it requires"
        " and names in after, before what it names in before, ties alphabetical"
    )


def listing_problems(root: Path) -> list[str]:
    """Each ``[workspace] extensions`` entry spelled wrong or with an undeclared option.

    An entry is a name with the options it turns on. It takes no
    version, ``?`` or scope: an extension's version comes from its
    wheel's metadata, and the lock pins it. An extension no installed
    distribution declares is
    [livery.workshop._extensions.level_problems][]'s to name.
    """
    from livery.toolroom.store import Spec, SpecError

    problems: list[str] = []
    for entry in _listed(root):
        spelled = _spelling(entry)
        try:
            spec = Spec.parse(spelled, where="[workspace] extensions")
        except SpecError:
            spec = None
        if spec is None or spec.optional or spec.floor or spec.scope:
            problems.append(
                f"[workspace] extensions lists {spelled!r}; an entry is a name with"
                " the options it turns on, `name` or `name[option,option]`, and no"
                " version or scope: the lock pins an extension's version from its"
                " wheel"
            )
            continue
        if declaration(spec.name) is not None and (
            why := _undeclared(spec.name, spec.options)
        ):
            problems.append(why)
    return problems


_LISTS: dict[tuple[Path, str], tuple[str, ...]] = {}


def _package_lists(root: Path) -> list[tuple[Path, tuple[str, ...]]]:
    """Each package contract under *root* with the extensions it lists, read raw.

    Read raw, as the root's list is, since the mount reads it before any
    verb judges a contract. A contract that is not TOML, or whose
    ``extensions`` is no list, lists nothing here: the judge names it
    when the package is read, and every command, ``fm sync`` among
    them, still mounts. Each contract is
    parsed once per content: every command's mount asks, and a
    package's list changes only when its text does.
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
                entries = tomllib.loads(text).get("extensions")
            except tomllib.TOMLDecodeError:
                entries = None
            listed = (
                tuple(str(name) for name in entries)
                if isinstance(entries, list)
                else ()
            )
            _LISTS[(contract, text)] = listed
        found.append((contract, listed))
    return found


def level_problems(root: Path) -> list[str]:
    """Each listed extension nothing installed declares, or listed at a wrong level.

    A package's entry is the extension's name alone: a package-level
    extension takes no options.
    """
    problems: list[str] = []
    for name in workspace_names(root):
        levels = levels_of(name)
        if not levels:
            problems.append(
                f"[workspace] extensions lists {name}, which no installed"
                f" distribution declares in {GROUP}"
            )
        elif WORKSPACE not in levels:
            problems.append(
                f"[workspace] extensions lists {name}, which is listed in a"
                " package's `extensions` alone; move it there"
            )
    for contract, listed in _package_lists(root):
        where = contract.parent.relative_to(root).as_posix()
        for name in listed:
            if listing(name).name != name:
                problems.append(
                    f"{where}: `extensions` lists {name!r}; a package's entry is the"
                    " extension's name alone, and a package-level extension takes"
                    " no options"
                )
                continue
            levels = levels_of(name)
            if not levels:
                problems.append(
                    f"{where}: `extensions` lists {name}, which no installed"
                    f" distribution declares in {GROUP}"
                )
            elif PACKAGE not in levels:
                problems.append(
                    f"{where}: `extensions` lists {name}, which is listed in"
                    " [workspace] extensions alone; move it there"
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
    """Close the lists under the declarations, and write each package's canonically.

    Returns the lines written, one per change.

    A workspace extension's missing requirement lands before its first
    dependent, with a comment naming who requires it, so the list stays
    the whole truth a reader sees. A dependency listed out of order is
    not moved: that is a person's edit, and the judge that follows
    names it. A package-level extension's requirement listed at the
    workspace alone joins the end of ``[workspace] extensions``. Each
    package's list becomes its canonical one
    ([livery.workshop._composition.canonical][]), which drops an
    extension another listed one requires. A package whose list the
    judge refuses for another reason, or whose order has no start, is
    left for a person. Idempotent. A root without a contract lists no
    extensions, so nothing is written.
    """
    from livery.workshop._composition import canonical

    contract = root / "workshop.toml"
    if not contract.is_file():
        return []
    text = contract.read_text(encoding="utf-8")
    written: list[str] = []
    names = list(workspace_names(root))
    for extension in list(names):
        found = _readable(extension)
        for needed in found.requires if found is not None else ():
            if needed in names or _package_only(needed):
                continue
            text, done = _insert_extension(text, needed, before=extension)
            if not done:
                continue
            names.insert(names.index(extension), needed)
            written.append(
                f"  layering: [workspace] extensions gains {needed} before {extension},"
                " which requires it"
            )
    for package_contract, listed in _package_lists(root):
        if not _composable(listed):
            continue
        where = package_contract.parent.relative_to(root).as_posix()
        for extension in package_set(listed, _lookup):
            found = _lookup(extension)
            for needed in found.requires if found is not None else ():
                if needed in names or _lookup(needed) is None:
                    continue
                if PACKAGE in levels_of(needed):
                    continue
                text = _append_to_list(text, needed)
                names.append(needed)
                written.append(
                    f"  layering: [workspace] extensions gains {needed},"
                    f" which {extension} in {where} requires"
                )
        try:
            wanted = canonical(listed, _lookup)
        except OrderCycle:
            continue
        if wanted == listed:
            continue
        package_text = package_contract.read_text(encoding="utf-8")
        rewritten = _rewrite_list(package_text, wanted)
        if rewritten == package_text:
            continue
        package_contract.write_text(rewritten, encoding="utf-8")
        written.append(
            f"  layering: {where} `extensions` is {_spelled(wanted)},"
            " its canonical list"
        )
    recorded = extension_targets(root)
    resolved = resolved_targets(root)
    for owner in workspace_names(root):
        targets = resolved.get(owner, ())
        if recorded.get(owner) is not None or not targets:
            continue
        text, done = _write_for(text, owner, targets)
        if not done:
            continue
        written.append(
            f"  layering: [workspace] extensions records {owner}"
            f" for {', '.join(targets)}"
        )
    if written:
        contract.write_text(text, encoding="utf-8")
    return written


def _rewrite_list(text: str, names: tuple[str, ...]) -> str:
    """*text* with its ``extensions`` list holding *names*; unchanged without one."""
    found = _INLINE.search(text)
    if found is None:
        return text
    return (
        text[: found.start()] + f"extensions = {_spelled(names)}" + text[found.end() :]
    )


def _write_for(text: str, owner: str, targets: tuple[str, ...]) -> tuple[str, bool]:
    """*text* with *owner*'s entry carrying ``for = [targets]``, and whether it does.

    Written once: the string entry becomes a table entry with the
    same spelling, options included, a table entry gains the key, and
    an entry in a shape this does not read is left for a person, with
    the judge naming nothing since a missing ``for`` is not a problem.
    """
    listed = ", ".join(f'"{target}"' for target in targets)
    line = re.compile(rf'^(\s*)"({_entry_pattern(owner)})",?\s*(#.*)?$', re.M)
    match = line.search(text)
    if match is not None:
        comment = f"  {match.group(3)}" if match.group(3) else ""
        table = f'{{ name = "{match.group(2)}", for = [{listed}] }}'
        return (
            text[: match.start()]
            + f"{match.group(1)}{table},{comment}"
            + text[match.end() :],
            True,
        )
    entry = re.compile(
        rf'^(\s*\{{ name = "{_entry_pattern(owner)}"(?:, [a-z]+ = [^,}}]+)*)'
        r" \}(,?\s*(?:#.*)?)$",
        re.M,
    )
    match = entry.search(text)
    if match is not None:
        return (
            text[: match.start()]
            + f"{match.group(1)}, for = [{listed}] }}{match.group(2)}"
            + text[match.end() :],
            True,
        )
    match = _INLINE.search(text)
    if match is None:
        return text, False
    items = _items(match.group(2))
    index = _string_item(items, owner)
    if index is None:
        return text, False
    items[index] = f"{{ name = {items[index]}, for = [{listed}] }}"
    return text[: match.start()] + "extensions = [" + ", ".join(items) + "]" + text[
        match.end() :
    ], True


#: An inline ``extensions = [...]`` list: its body runs to the bracket
#: that closes it, past an entry's own brackets and a table's.
_INLINE = re.compile(r'^(extensions = \[)((?:"[^"]*"|\{[^}]*\}|[^\]"{}])*)(\])', re.M)


def _entry_pattern(name: str) -> str:
    """A pattern for *name* as an entry spells it: the name, with its options if any."""
    return rf'{re.escape(name)}(?:\[[^\]"]*\])?'


def _items(body: str) -> list[str]:
    """The entries of an inline ``extensions = [...]`` body, as written."""
    return re.findall(r'\{[^}]*\}|"[^"]*"', body)


def _string_item(items: list[str], name: str) -> int | None:
    """Where *name*'s string entry is among *items*; None without one."""
    for index, item in enumerate(items):
        if item.startswith('"') and listing(item.strip('"')).name == name:
            return index
    return None


def _table_name(item: str) -> str:
    """The ``name`` a table entry ``{ name = "..." }`` spells; empty without one."""
    found = re.search(r'\bname\s*=\s*"([^"]*)"', item)
    return found.group(1) if found is not None else ""


def _entry_item(items: list[str], name: str) -> int | None:
    """Where *name*'s entry is among *items*, a string or a table; None without one."""
    for index, item in enumerate(items):
        spelled = item.strip('"') if item.startswith('"') else _table_name(item)
        if spelled and listing(spelled).name == name:
            return index
    return None


def _insert_extension(text: str, needed: str, *, before: str) -> tuple[str, bool]:
    """*text* with *needed* listed before *before*'s entry, and whether it was.

    The entry is a string or a table, on a line of its own or in an
    inline list.
    """
    entry = _entry_pattern(before)
    pattern = re.compile(
        rf'^(\s*)(?:"{entry}"|\{{\s*name\s*=\s*"{entry}"[^}}\n]*\}}),?\s*(#.*)?$',
        re.M,
    )
    match = pattern.search(text)
    if match is not None:
        indent = match.group(1)
        line = f'{indent}"{needed}",  # required by {before}\n'
        return text[: match.start()] + line + text[match.start() :], True
    match = _INLINE.search(text)
    if match is None:
        return text, False
    items = _items(match.group(2))
    index = _entry_item(items, before)
    if index is None:
        return text, False
    items.insert(index, f'"{needed}"')
    return text[: match.start()] + "extensions = [" + ", ".join(items) + "]" + text[
        match.end() :
    ], True


def _append_to_list(text: str, name: str) -> str:
    """*text* with *name* last in its ``extensions`` list, added when absent."""
    found = _INLINE.search(text)
    if found is None:
        # A top-level key: before the first table, or it joins that table.
        line = f'extensions = ["{name}"]\n'
        table = re.search(r"^\[", text, re.M)
        if table is None:
            return text.rstrip("\n") + "\n" + line
        return text[: table.start()] + line + "\n" + text[table.start() :]
    items = _items(found.group(2))
    items.append(f'"{name}"')
    return (
        text[: found.start()]
        + "extensions = ["
        + ", ".join(items)
        + "]"
        + text[found.end() :]
    )


def combination_names() -> tuple[str, ...]:
    """Every valid combination of the installed package-level extensions, by name.

    What a package's ``extensions`` may compose from what is installed
    ([livery.workshop._composition.combinations][]).

    Raises:
        DeclarationError: when an installed extension's declaration file
            says something this workshop cannot take.
    """
    from livery.workshop._composition import combinations

    return combinations(installed_extensions(), _lookup)


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
