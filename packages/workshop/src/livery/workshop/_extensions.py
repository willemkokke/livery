"""The extension walk: one list in the workspace contract, every channel.

The root ``workshop.toml`` names the extensions in precedence order, the
workshop first and the instance implicitly last. Mounting reads that
list and grafts each further extension's footman plugin in order, so a
package installed by accident never changes a repository: discovery
is the list and nothing else.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from livery.footman import prog
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
    from importlib.metadata import EntryPoint

    from livery.toolroom.store import Spec

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


def extension_entries(start: Path | None = None) -> tuple[tuple[str, str], ...]:
    """Each listed extension as ``(name, distribution)``, in list order.

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
    """The base, then every listed extension, as ``(name, distribution)``.

    What a reader of the whole stack walks: the base ships the template
    tree the extensions overlay. Empty outside a workspace.
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


def extension_names(start: Path | None = None) -> tuple[str, ...]:
    """The extensions the workspace lists, in precedence order."""
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


def mount_extensions(start: Path | None = None) -> tuple[str, ...]:
    """Mount every listed extension's plugin, in order; the names mounted.

    Refuses an extension declared for another workshop API version, and
    one whose declaration file says something this workshop cannot take.
    A contract without ``[workspace] extensions``, an extension no
    installed distribution declares, one whose installed package ships
    no declaration file, and one that may not be listed at the
    workspace level are named on stderr and skipped, never refused:
    the mount runs on every command, ``fm sync`` among them, which is
    what installs a missing declaration, so a refusal here would stop
    the command that repairs it. An option the extension does not
    declare is named the same way and left off. The gate's layering
    check and ``fm extensions`` refuse the same problems. An extension
    whose declaration names no ``plugin`` registers what it declares
    and mounts no verbs. A plugin a branded App already mounts as a
    builtin is skipped: claiming its tasks again puts the same task in
    one rung twice.
    """
    # footman does not expose the brand's builtin set publicly; this
    # private read is one of the reaches issue #1204 closes with a seam.
    from livery.footman import _paths, plugin

    global MOUNTED, UNDECLARED
    MOUNTED = True
    undeclared: list[str] = []
    root = workspace_root(start)
    if root is not None and (why := missing_list(root)):
        _note(why)
    builtin = set(_paths.builtin())
    mounted = []
    declared = declared_targets(start)
    active = resolved_targets(start)
    options = extension_options(start)
    # The base is present before this walk; every extension joins once
    # its mount ran.
    present = [SELF]
    grafted: set[tuple[str, str]] = set()
    _graft_contributions(present, declared, active, grafted)
    for extension, dist in extension_entries(start):
        if extension in builtin:
            # The App mounted it as its own builtin: its plugin is never
            # mounted here a second time.
            present.append(extension)
            _graft_contributions(present, declared, active, grafted)
            continue
        package = extension_package(extension)
        if extension in _declared() and declaration_file(package) is None:
            # An environment from before this checkout's, or a release
            # written for another workshop: named and skipped, so the
            # sync that installs this checkout's can run.
            undeclared.append(extension)
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
        if WORKSPACE not in found.levels:
            _note(
                f"extension {extension!r} is listed in [workspace] extensions, and it"
                f" declares the levels {', '.join(found.levels) or 'none'};"
                " list it in each package's `extensions` instead"
            )
            continue
        listed = options.get(extension, ())
        if why := _undeclared(extension, listed):
            _note(f"{why}; the mount leaves it off")
        if register_declared(extension, found.additions, listed):
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
        _graft_contributions(present, declared, active, grafted)
    # An extension's checks registered as it mounted; their verbs join the
    # ones the workshop generated as it loaded.
    from livery.workshop._checks import generate_verbs

    generate_verbs()
    UNDECLARED = tuple(undeclared)
    return tuple(mounted)


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
    process starts. An entry point whose package ships no declaration
    file is not counted: a sync installs the one that does.
    """
    if not names:
        return ()
    from livery.footman import rescan_entry_points

    rescan_entry_points()
    declared = _declared()
    return tuple(
        name
        for name in names
        if name in declared
        and declaration_file(declared[name].value.partition(":")[0]) is not None
    )


def _note(text: str) -> None:
    """Name a problem the mount skips past, on stderr."""
    import sys

    print(f"  note: {text}", file=sys.stderr)


def _graft_contributions(
    present: list[str],
    declared: dict[str, dict[str, Additions]],
    active: dict[str, tuple[str, ...]],
    grafted: set[tuple[str, str]],
) -> None:
    """Register every ``[for.<target>]`` table whose owner and target are both present.

    Called after each extension mounts, so a contribution lands whichever
    of the two mounts later. A ``for`` entry naming a target the owner
    declares nothing for registers nothing: the layering check names
    that entry, and it can only run inside a mount that went on.
    """
    for owner in present:
        for target in active.get(owner, ()):
            if target not in present or (owner, target) in grafted:
                continue
            additions = declared.get(owner, {}).get(target)
            if additions is None:
                continue
            register_declared(owner, additions)
            grafted.add((owner, target))


def register_declared(
    extension: str, additions: Additions, options: tuple[str, ...] = ()
) -> bool:
    """Register what *additions* declares for *extension*; whether it declares a check.

    Each check registers under the extension's listed name, since the
    list is what decides which extension a check belongs to, and each
    value goes into its slot under the same name. A check that names an
    option in ``listed-with`` registers only when *options*, the
    entry's, turn it on.
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
    return bool(additions.checks)


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


def describe_extensions(start: Path | None = None) -> list[str]:
    """The lines ``fm extensions`` prints: each extension with what it declares."""
    names = extension_names(start)
    who = requirers(start)
    tools = extension_tools(start)
    targets = resolved_targets(start)
    listed = extension_options(start)
    lines: list[str] = [f"  {SELF} (the base)"]
    for name in names:
        needed = who.get(name, ())
        by = f" (required by {', '.join(needed)})" if needed else ""
        lines.append(f"  {name}{by}")
        if tools.get(name):
            lines.append(f"    tools: {', '.join(tools[name])}")
        if targets.get(name):
            lines.append(f"    for: {', '.join(targets[name])}")
        for option, text in declared_options(name).items():
            state = "on" if option in listed.get(name, ()) else "off"
            lines.append(f"    option {option} ({state}): {text}")
    return lines


def closure_problems(start: Path | None = None) -> list[str]:
    """Why ``[workspace] extensions`` is not closed under the extensions' declarations.

    Empty when every declared dependency is listed before its
    dependent and every extension is listed at a level it declares. A
    missing one names the fix; one listed after its dependent names the
    move, which is a person's edit.
    """
    root = workspace_root(start)
    names = extension_names(start)
    order = {name: index for index, name in enumerate(names)}
    problems: list[str] = []
    for extension, depends in extension_dependencies(start).items():
        for needed in depends:
            if needed not in order:
                problems.append(
                    f"[workspace] extensions lists {extension}, which depends on"
                    f" {needed}, and does not list it; the gate's --fix adds"
                    f" it before {extension}"
                )
            elif order[needed] > order[extension]:
                problems.append(
                    f"[workspace] extensions lists {needed} after {extension}, which"
                    f" depends on it; move {needed} before {extension}"
                )
    declared = declared_targets(start)
    for owner, targets in extension_targets(start).items():
        for target in targets or ():
            if target not in order:
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
    return problems


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


def _package_lists(root: Path) -> list[tuple[Path, tuple[str, ...]]]:
    """Each package contract under *root* with the extensions it lists, read raw."""
    from livery.workshop._packages import package_directories

    found: list[tuple[Path, tuple[str, ...]]] = []
    for directory in package_directories(root):
        contract = directory / "workshop.toml"
        if not contract.is_file():
            continue
        listed = tomllib.loads(contract.read_text("utf-8")).get("extensions") or []
        found.append((contract, tuple(str(name) for name in listed)))
    return found


def level_problems(root: Path) -> list[str]:
    """Each listed extension nothing installed declares, or listed at a wrong level."""
    problems: list[str] = []
    for name in extension_names(root):
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
    """Add each missing declared dependency to ``[workspace] extensions``.

    Returns the lines written, one per entry added.

    The entry lands before its first dependent, with a comment naming
    who requires it, so the list stays the whole truth a reader sees.
    A dependency listed out of order is not moved: that is a person's
    edit, and the judge that follows names it. Idempotent. A root
    without a contract lists no extensions, so nothing is written.
    """
    contract = root / "workshop.toml"
    if not contract.is_file():
        return []
    text = contract.read_text(encoding="utf-8")
    written: list[str] = []
    names = list(extension_names(root))
    for extension, depends in extension_dependencies(root).items():
        for needed in depends:
            if needed in names:
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
        package_text = package_contract.read_text(encoding="utf-8")
        own = list(listed)
        for extension in listed:
            found = declaration(extension)
            depends = found.requires if found is not None else ()
            for needed in depends:
                if needed in own or needed in names:
                    continue
                where = package_contract.parent.relative_to(root).as_posix()
                if PACKAGE in levels_of(needed):
                    package_text = _append_to_list(package_text, needed)
                    own.append(needed)
                    written.append(
                        f"  layering: {where} `extensions` gains {needed},"
                        f" which {extension} requires"
                    )
                else:
                    text = _append_to_list(text, needed)
                    names.append(needed)
                    written.append(
                        f"  layering: [workspace] extensions gains {needed},"
                        f" which {extension} in {where} requires"
                    )
        if own != list(listed):
            package_contract.write_text(package_text, encoding="utf-8")
    recorded = extension_targets(root)
    for owner, targets in resolved_targets(root).items():
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


def _insert_extension(text: str, needed: str, *, before: str) -> tuple[str, bool]:
    """*text* with *needed* listed before *before*'s entry, and whether it was."""
    pattern = re.compile(rf'^(\s*)"{_entry_pattern(before)}",?\s*(#.*)?$', re.M)
    match = pattern.search(text)
    if match is not None:
        indent = match.group(1)
        line = f'{indent}"{needed}",  # required by {before}\n'
        return text[: match.start()] + line + text[match.start() :], True
    match = _INLINE.search(text)
    if match is None:
        return text, False
    items = _items(match.group(2))
    index = _string_item(items, before)
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
