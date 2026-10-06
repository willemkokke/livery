"""The extension walk: one list in the workspace contract, every channel.

The root ``workshop.toml`` names the extensions in precedence order, the
workshop first and the instance implicitly last. Mounting reads that
list and grafts each further extension's footman plugin in order, so a
package installed by accident never changes a repository: discovery
is the list and nothing else.
"""

from __future__ import annotations

import importlib
import re
import tomllib
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

from livery.footman.api import prog

if TYPE_CHECKING:
    from importlib.metadata import EntryPoint

#: The base. Never listed and never mounted by name: importing its
#: plugin module is the base arriving.
SELF = "livery.workshop"

#: The entry point group an extension declares itself in: its name
#: maps to the data module that carries its declarations.
GROUP = "workshop.extensions"

#: The levels an extension may be listed at.
WORKSPACE = "workspace"
PACKAGE = "package"

#: The workshop's plugin API, the version an extension's declaring module may
#: declare as ``API_VERSION``. Registration records grow
#: additively, so an extension that declares nothing is taken at this
#: version; one that declares another refuses at mount with both
#: versions named, a sentence instead of an AttributeError later.
API_VERSION = 1

#: The attribute an extension's plugin module declares its dependencies
#: with: the import paths of the extensions it needs mounted before it.
DEPENDS_ATTRIBUTE = "REQUIRES"

#: The attribute an extension's plugin module declares the tools its own
#: verbs need with: requirement strings, ``docker`` or ``docker>=27``.
#: The derived profile reads it as its fourth site, beside the kinds,
#: the packages and the root contract.
TOOLS_ATTRIBUTE = "TOOLS"

#: The attribute an extension's plugin module declares its contributions
#: with: a map from a target extension's import path to the module that
#: carries the registrations for that target. The mount imports the
#: module once both the owner and the target are mounted, so no mount
#: code branches on what is listed.
FOR_ATTRIBUTE = "FOR"

#: The attribute an extension's declaring module declares its checks
#: with, as data: a tuple of check records. The mount registers them
#: for a listed extension alone, each under the extension's listed
#: name, so the files they deliver and the drift check's widening
#: follow the list.
CHECKS_ATTRIBUTE = "CHECKS"


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
    from livery.footman.api import installed_entry_points

    return {entry.name: entry for entry in installed_entry_points(GROUP)}


def installed_extensions() -> tuple[str, ...]:
    """The names installed distributions declare extensions under, sorted."""
    return tuple(sorted(_declared()))


def declaration(extension: str) -> ModuleType | None:
    """The data module *extension* declares itself with; None when none is installed.

    Loading it imports that module alone, never the extension's
    tasks: footman's ``plugin()`` must stay a plugin's first importer.
    """
    entry = _declared().get(extension)
    if entry is None:
        return None
    module = entry.load()
    return (
        module
        if isinstance(module, ModuleType)
        else importlib.import_module(entry.value.partition(":")[0])
    )


def levels_of(extension: str) -> tuple[str, ...]:
    """The levels *extension* may be listed at; the workspace when it says none."""
    module = declaration(extension)
    declared = getattr(module, "LEVELS", (WORKSPACE,)) if module is not None else ()
    return tuple(str(level) for level in declared)


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


def extension_entries(start: Path | None = None) -> tuple[tuple[str, str], ...]:
    """Each listed extension as ``(name, distribution)``, in list order.

    An entry is a name, or a table ``{ name = "...", for = [...] }``.
    Empty outside a workspace and when the contract has no list: the
    mount refuses that, and a reader has nothing to read.
    """
    root = workspace_root(start)
    workspace = None if root is None else _workspace_table(root)
    if not workspace:
        return ()
    entries: list[tuple[str, str]] = []
    for entry in workspace.get("extensions") or []:
        name = str(entry.get("name", "")) if isinstance(entry, dict) else str(entry)
        entries.append((name, distribution_of(name)))
    return tuple(entries)


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
    """The dotted package *extension* ships from: its declaring module's package.

    The base is its own; an extension no installed distribution
    declares is taken at its name, which a member mid-birth may be.
    """
    if extension == SELF:
        return SELF
    entry = _declared().get(extension)
    if entry is None:
        return extension
    return entry.value.partition(":")[0].rpartition(".")[0] or extension


def extension_names(start: Path | None = None) -> tuple[str, ...]:
    """The extensions the workspace lists, in precedence order."""
    return tuple(name for name, _ in extension_entries(start))


def extension_content(extension: str) -> Path | None:
    """The installed extension's ``content/`` directory, or None.

    Beside the extension's declaring module, or the base's own for
    the base. In the monorepo the "wheel" is the editable source tree,
    which is what lets the materialised links point back into the
    repository. None for an extension that is not installed or ships
    no content.
    """
    if extension == SELF:
        content = Path(__file__).resolve().parent / "content"
        return content if content.is_dir() else None
    module = declaration(extension)
    if module is None or module.__file__ is None:
        return None
    content = Path(module.__file__).resolve().parent / "content"
    return content if content.is_dir() else None


def extension_targets(start: Path | None = None) -> dict[str, tuple[str, ...] | None]:
    """Each listed extension's ``for`` list from its contract entry; None without one.

    A table entry may carry ``for = [...]``, the targets the extension
    contributes to, written once by the layering check's fix and a
    person's to edit from then on: a name deleted from it stays
    deleted, which is a project's opt-out from that target's
    opinions.
    """
    root = workspace_root(start)
    workspace = None if root is None else _workspace_table(root)
    if not workspace:
        return {}
    found: dict[str, tuple[str, ...] | None] = {}
    for entry in workspace.get("extensions") or []:
        if isinstance(entry, dict):
            targets = entry.get("for")
            found[str(entry.get("name", ""))] = (
                None if targets is None else tuple(str(name) for name in targets)
            )
        else:
            found[str(entry)] = None
    return found


#: Whether mount_extensions ran in this process. The cascade hook reads
#: it: a workspace declaring further extensions whose tasks.py never
#: mounted them is running a pre-composition render, and the gap
#: must teach rather than silently narrow the tree.
MOUNTED: bool = False


def mount_extensions(start: Path | None = None) -> tuple[str, ...]:
    """Mount every listed extension's plugin, in order; the names mounted.

    Refuses an extension declared for another workshop API version. A
    contract without ``[workspace] extensions``, an extension no
    installed distribution declares, and one that may not be listed at
    the workspace level are named on stderr and skipped, never refused:
    the mount runs on every command, ``fm sync`` among them, which is
    what installs a missing declaration, so a refusal here would stop
    the command that repairs it. The gate's layering check and
    ``fm extensions`` refuse the same problems. An extension whose
    declaration names
    no ``PLUGIN`` registers what it declares and mounts no verbs. A
    plugin a branded App already mounts as a builtin is skipped:
    claiming its tasks again puts the same task in one rung twice.
    """
    # footman does not expose the brand's builtin set publicly yet;
    # the private read retires when footman joins the workspace.
    from livery.footman import _paths
    from livery.footman.api import plugin

    global MOUNTED
    MOUNTED = True
    root = workspace_root(start)
    if root is not None and (why := missing_list(root)):
        _note(why)
    builtin = set(_paths.builtin())
    mounted = []
    declared = contributions(start)
    active = resolved_targets(start)
    # The base is present before this walk; every extension joins once
    # its mount ran.
    present = [SELF]
    grafted: set[tuple[str, str]] = set()
    _graft_contributions(present, declared, active, grafted)
    for extension, dist in extension_entries(start):
        if extension in builtin:
            # The App mounted it as its own builtin: never imported here.
            present.append(extension)
            _graft_contributions(present, declared, active, grafted)
            continue
        module = declaration(extension)
        if module is None:
            _note(
                f"extension {extension!r} is listed in [workspace] extensions, and no"
                f" installed distribution declares it in {GROUP}; install the"
                f" distribution that ships it ({dist} by its name), or remove the"
                f" entry; `{prog()} sync` installs one this workspace builds"
            )
            continue
        check_api_version(extension, module)
        if WORKSPACE not in levels_of(extension):
            _note(
                f"extension {extension!r} is listed in [workspace] extensions, and it"
                f" declares the levels {', '.join(levels_of(extension)) or 'none'};"
                " list it in each package's `extensions` instead"
            )
            continue
        if register_declared_checks(extension, module):
            mounted.append(extension)
        name = getattr(module, "PLUGIN", None)
        if name and name not in builtin:
            try:
                plugin(str(name))
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
    return tuple(mounted)


def _note(text: str) -> None:
    """Name a problem the mount skips past, on stderr."""
    import sys

    print(f"  note: {text}", file=sys.stderr)


def _graft_contributions(
    present: list[str],
    declared: dict[str, dict[str, str]],
    active: dict[str, tuple[str, ...]],
    grafted: set[tuple[str, str]],
) -> None:
    """Import every contribution whose owner and target are both present.

    Called after each extension mounts, so a contribution lands whichever
    of the two mounts later. A module that does not import refuses
    naming the owner, the target and the module. A ``for`` entry
    naming a target the owner declares nothing for mounts nothing:
    the layering check names that entry, and it can only run inside a
    mount that went on.
    """
    for owner in present:
        for target in active.get(owner, ()):
            if target not in present or (owner, target) in grafted:
                continue
            module = declared.get(owner, {}).get(target)
            if module is None:
                continue
            try:
                importlib.import_module(module)
            except ModuleNotFoundError as error:
                raise RuntimeError(
                    f"extension {owner!r} contributes {module!r} for {target}, and"
                    f" the module does not import: {error}"
                ) from error
            grafted.add((owner, target))


def register_declared_checks(extension: str, module: ModuleType) -> bool:
    """Register the checks *module* declares for *extension*; whether it declares any.

    Each record registers under the extension's listed name, whatever
    it says, since the list is what decides which extension a check
    belongs to.

    Raises:
        RuntimeError: when the declaration is not a tuple of check
            records, an extension's mistake no sync repairs, said in
            one sentence rather than an error further on, as a wrong
            API version is.
    """
    from dataclasses import replace

    from livery.workshop._checks import CheckRecord, register_check

    declared = getattr(module, CHECKS_ATTRIBUTE, ())
    if not isinstance(declared, tuple):
        wrong = f"as a {type(declared).__name__}"
    else:
        strays = [type(r).__name__ for r in declared if not isinstance(r, CheckRecord)]
        wrong = f"with a {strays[0]} among them" if strays else ""
    if wrong:
        raise RuntimeError(
            f"extension {extension!r} declares {CHECKS_ATTRIBUTE} {wrong}; it"
            " takes a tuple of check records. Install a release of the"
            " extension written for this workshop."
        )
    for record in declared:
        register_check(replace(record, extension=extension))
    return bool(declared)


def check_api_version(extension: str, module: ModuleType) -> None:
    """Refuse an extension whose declared API version is not this workshop's.

    An extension declares ``API_VERSION`` in its declaring module; one
    that declares nothing is taken at the current version.
    """
    declared = getattr(module, "API_VERSION", API_VERSION)
    if declared != API_VERSION:
        raise RuntimeError(
            f"extension {extension!r} declares workshop API version {declared!r}; this"
            f" workshop is version {API_VERSION}. Install a release of the"
            " extension written for this workshop, or of the workshop the extension"
            " was written for."
        )


def _plugin_module(extension: str) -> ModuleType | None:
    """The listed extension's declaring module; None for the base or an absent one.

    The base declares nothing about itself here, and an absent
    extension is the mount's to refuse, naming the install.
    """
    if extension == SELF:
        return None
    return declaration(extension)


def extension_dependencies(start: Path | None = None) -> dict[str, tuple[str, ...]]:
    """Each listed extension's declared dependencies, import path to import paths.

    Read from the ``WORKSHOP_DEPENDS`` attribute of each extension's plugin
    module; the workshop itself and an extension that cannot be imported
    declare none here, since the mount teaches the install.
    """
    found: dict[str, tuple[str, ...]] = {}
    for extension in extension_names(start):
        module = _plugin_module(extension)
        declared = () if module is None else getattr(module, DEPENDS_ATTRIBUTE, ())
        found[extension] = tuple(str(name) for name in declared)
    return found


def extension_tools(start: Path | None = None) -> dict[str, tuple[str, ...]]:
    """Each listed extension's declared tools, import path to requirement strings.

    Read from ``WORKSHOP_TOOLS`` on the plugin module, a tuple of
    requirement strings such as ``("docker>=27",)``. The workshop
    itself and an extension that cannot be imported declare none here. A
    value of another shape refuses naming the extension: a tool declared
    in a shape the lock cannot read is a tool the environment lacks in
    silence.

    Raises:
        RuntimeError: when an extension's declaration is not a tuple of
            strings.
    """
    found: dict[str, tuple[str, ...]] = {}
    for extension in extension_names(start):
        module = _plugin_module(extension)
        declared = () if module is None else getattr(module, TOOLS_ATTRIBUTE, ())
        if isinstance(declared, str) or not (
            isinstance(declared, (tuple, list))
            and all(isinstance(text, str) for text in declared)
        ):
            raise RuntimeError(
                f"extension {extension!r} declares {TOOLS_ATTRIBUTE} as {declared!r};"
                ' it is a tuple of requirement strings, ("docker>=27",)'
            )
        found[extension] = tuple(declared)
    return found


def contributions(start: Path | None = None) -> dict[str, dict[str, str]]:
    """Each listed extension's declared contributions, owner to target to module.

    Read from ``WORKSHOP_FOR`` on the plugin module, a map from a
    target extension's import path to the module carrying the
    registrations for that target. A value of another shape refuses
    naming the extension.

    Raises:
        RuntimeError: when an extension's declaration is not a map of
            strings to strings.
    """
    found: dict[str, dict[str, str]] = {}
    for extension in extension_names(start):
        module = _plugin_module(extension)
        declared = {} if module is None else getattr(module, FOR_ATTRIBUTE, {})
        if not isinstance(declared, dict) or not all(
            isinstance(target, str) and isinstance(name, str)
            for target, name in declared.items()
        ):
            raise RuntimeError(
                f"extension {extension!r} declares {FOR_ATTRIBUTE} as {declared!r}; it"
                " is a map from a target extension's import path to the module"
                ' carrying the registrations for it, {"livery.workshop.api.python":'
                ' "acme.house.python"}'
            )
        found[extension] = dict(declared)
    return found


def resolved_targets(start: Path | None = None) -> dict[str, tuple[str, ...]]:
    """Each listed extension's active targets, in list order.

    The entry's ``for`` list when it has one, since that is the truth
    once written; otherwise every declared target that is listed.
    """
    names = extension_names(start)
    declared = contributions(start)
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
    lines: list[str] = [f"  {SELF} (the base)"]
    for name in names:
        needed = who.get(name, ())
        by = f" (required by {', '.join(needed)})" if needed else ""
        lines.append(f"  {name}{by}")
        if tools.get(name):
            lines.append(f"    tools: {', '.join(tools[name])}")
        if targets.get(name):
            lines.append(f"    for: {', '.join(targets[name])}")
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
    declared = contributions(start)
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
            module = declaration(extension)
            depends = (
                getattr(module, DEPENDS_ATTRIBUTE, ()) if module is not None else ()
            )
            for needed in (str(name) for name in depends):
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

    Written once: the string entry becomes a table entry, a table entry
    gains the key, and an entry in a shape this does not read is left
    for a person, with the judge naming nothing since a missing ``for``
    is not a problem.
    """
    listed = ", ".join(f'"{target}"' for target in targets)
    table = f'{{ name = "{owner}", for = [{listed}] }}'
    line = re.compile(rf'^(\s*)"{re.escape(owner)}",?\s*(#.*)?$', re.M)
    match = line.search(text)
    if match is not None:
        comment = f"  {match.group(2)}" if match.group(2) else ""
        return (
            text[: match.start()]
            + f"{match.group(1)}{table},{comment}"
            + text[match.end() :],
            True,
        )
    entry = re.compile(
        rf'^(\s*\{{ name = "{re.escape(owner)}"(?:, [a-z]+ = [^,}}]+)*)'
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
    inline = re.compile(r"^(extensions = \[)(.*?)(\])", re.M | re.S)
    match = inline.search(text)
    if match is None:
        return text, False
    items = [item.strip() for item in match.group(2).split(",") if item.strip()]
    target = f'"{owner}"'
    if target not in items:
        return text, False
    items[items.index(target)] = table
    return text[: match.start()] + "extensions = [" + ", ".join(items) + "]" + text[
        match.end() :
    ], True


def _insert_extension(text: str, needed: str, *, before: str) -> tuple[str, bool]:
    """*text* with *needed* listed before *before*'s entry, and whether it was."""
    pattern = re.compile(rf'^(\s*)"{re.escape(before)}",?\s*(#.*)?$', re.M)
    match = pattern.search(text)
    if match is not None:
        indent = match.group(1)
        line = f'{indent}"{needed}",  # required by {before}\n'
        return text[: match.start()] + line + text[match.start() :], True
    inline = re.compile(r"^(extensions = \[)(.*?)(\])", re.M | re.S)
    match = inline.search(text)
    if match is None:
        return text, False
    items = [item.strip() for item in match.group(2).split(",") if item.strip()]
    target = f'"{before}"'
    if target not in items:
        return text, False
    items.insert(items.index(target), f'"{needed}"')
    return text[: match.start()] + "extensions = [" + ", ".join(items) + "]" + text[
        match.end() :
    ], True


def _append_to_list(text: str, name: str) -> str:
    """*text* with *name* last in its ``extensions`` list, added when absent."""
    found = re.search(r"^(extensions = \[)(.*?)(\])", text, re.M | re.S)
    if found is None:
        # A top-level key: before the first table, or it joins that table.
        line = f'extensions = ["{name}"]\n'
        table = re.search(r"^\[", text, re.M)
        if table is None:
            return text.rstrip("\n") + "\n" + line
        return text[: table.start()] + line + "\n" + text[table.start() :]
    items = [item.strip() for item in found.group(2).split(",") if item.strip()]
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
