"""The layer walk: one list in the workspace contract, every channel.

The root ``workshop.toml`` names the layers in precedence order, the
workshop first and the instance implicitly last. Mounting reads that
list and grafts each further layer's footman plugin in order, so a
package installed by accident never changes a repository: discovery
is the list and nothing else.
"""

from __future__ import annotations

import contextlib
import importlib
import re
import tomllib
from importlib import resources
from pathlib import Path
from types import ModuleType

#: The layer this package is. Never mounted by name: importing the
#: plugin module IS this layer arriving.
SELF = "livery.workshop"

#: The workshop's plugin API, the version a layer's plugin module may
#: declare as ``WORKSHOP_API_VERSION``. Registration records grow
#: additively, so a layer that declares nothing is taken at this
#: version; one that declares another refuses at mount with both
#: versions named, a sentence instead of an AttributeError later.
API_VERSION = 1

#: The attribute a layer's plugin module declares its dependencies
#: with: the import paths of the layers it needs mounted before it.
DEPENDS_ATTRIBUTE = "WORKSHOP_DEPENDS"

#: The attribute a layer's plugin module declares the tools its own
#: verbs need with: requirement strings, ``docker`` or ``docker>=27``.
#: The derived profile reads it as its fourth site, beside the kinds,
#: the packages and the root contract.
TOOLS_ATTRIBUTE = "WORKSHOP_TOOLS"

#: The attribute a layer's plugin module declares its contributions
#: with: a map from a target layer's import path to the module that
#: carries the registrations for that target. The mount imports the
#: module once both the owner and the target are mounted, so no mount
#: code branches on what is listed.
FOR_ATTRIBUTE = "WORKSHOP_FOR"


def workspace_root(start: Path | None = None) -> Path | None:
    """The nearest ancestor carrying a ``workshop.toml``, or None.

    The workspace contract is the marker; a checkout without one is
    not a workspace and gets no layers.
    """
    origin = (start or Path.cwd()).resolve()
    for candidate in (origin, *origin.parents):
        if (candidate / "workshop.toml").is_file():
            return candidate
    return None


def layer_entries(start: Path | None = None) -> tuple[tuple[str, str], ...]:
    """Each declared layer as ``(import path, distribution)``.

    A string entry derives its distribution by convention: dots
    become dashes, so ``livery.workshop`` is ``livery-workshop``. A
    table entry ``{import = "...", dist = "..."}`` spells both, for
    a layer whose names do not follow the convention. Empty outside
    a workspace, and empty when the contract carries no
    ``[workspace] layers`` list: no guessing, no defaults.
    """
    root = workspace_root(start)
    if root is None or not (root / "workshop.toml").is_file():
        # A root handed in without a contract, as a test's workspace
        # may be, lists no layers: nothing to guess from.
        return ()
    # Parsed raw on purpose: this read happens while the layers mount,
    # before any verb runs, and a refusal here would take every
    # command with it, the migration verb included. The contract
    # loader judges the same file on the first verb read.
    contract = tomllib.loads((root / "workshop.toml").read_text("utf-8"))
    workspace = contract.get("workspace") or {}
    entries: list[tuple[str, str]] = []
    for layer in workspace.get("layers") or []:
        if isinstance(layer, dict):
            import_path = str(layer.get("import", ""))
            dist = str(layer.get("dist", "")) or import_path.replace(".", "-")
            entries.append((import_path, dist))
        else:
            name = str(layer)
            entries.append((name, name.replace(".", "-")))
    return tuple(entries)


def layer_names(start: Path | None = None) -> tuple[str, ...]:
    """The layers the workspace declares, in precedence order.

    The import paths from livery.workshop._layers.layer_entries;
    empty on the same terms.
    """
    return tuple(import_path for import_path, _ in layer_entries(start))


def layer_content(layer: str) -> Path | None:
    """The installed layer's ``content/`` directory, or None.

    A layer is a Python package; its content ships inside the wheel.
    In the monorepo the "wheel" is the editable source tree, which is
    what lets the materialised links point back into the repository.
    None for a layer that is not installed or ships no content.
    """
    try:
        module = importlib.import_module(layer)
    except ModuleNotFoundError:
        return None
    root = resources.files(module)
    content = Path(str(root)) / "content"
    return content if content.is_dir() else None


def layer_targets(start: Path | None = None) -> dict[str, tuple[str, ...] | None]:
    """Each listed layer's ``for`` list from its contract entry; None without one.

    A table entry may carry ``for = [...]``, the targets the layer
    contributes to, written once by the layering check's fix and a
    person's to edit from then on: a name deleted from it stays
    deleted, which is a project's opt-out from that target's
    opinions.
    """
    root = workspace_root(start)
    if root is None or not (root / "workshop.toml").is_file():
        return {}
    contract = tomllib.loads((root / "workshop.toml").read_text("utf-8"))
    workspace = contract.get("workspace") or {}
    found: dict[str, tuple[str, ...] | None] = {}
    for layer in workspace.get("layers") or []:
        if isinstance(layer, dict):
            targets = layer.get("for")
            found[str(layer.get("import", ""))] = (
                None if targets is None else tuple(str(name) for name in targets)
            )
        else:
            found[str(layer)] = None
    return found


#: Whether mount_layers ran in this process. The cascade hook reads
#: it: a workspace declaring further layers whose tasks.py never
#: mounted them is running a pre-composition render, and the gap
#: must teach rather than silently narrow the tree.
MOUNTED: bool = False


def mount_layers(start: Path | None = None) -> tuple[str, ...]:
    """Graft every further layer's plugin, in order; the names mounted.

    The workshop itself is skipped: importing this package's plugin
    module is how the base layer arrives, and mounting it again from
    inside itself would recurse. A branded App's builtin layers are
    skipped the same way: footman mounts the builtin set as the
    cascade's base rung, this function runs inside that very mount
    (importing the workshop is how the App mounts it), and claiming a
    sibling builtin's tasks here puts the same task in one rung twice,
    which footman refuses the moment the layer has a real task.
    """
    # footman does not expose the brand's builtin set publicly yet;
    # the private read retires when footman joins the workspace.
    from livery.footman import (
        _paths,  # pyright: ignore[reportPrivateUsage]
        plugin,
    )

    global MOUNTED
    MOUNTED = True
    builtin = set(_paths.builtin())
    mounted = []
    declared = contributions(start)
    active = resolved_targets(start)
    # The workshop and the App's builtins are present before this walk;
    # every other layer joins once its mount ran, content-only ones too.
    present = [
        layer for layer in layer_names(start) if layer == SELF or layer in builtin
    ]
    grafted: set[tuple[str, str]] = set()
    for layer, dist in layer_entries(start):
        if layer == SELF or layer in builtin:
            _graft_contributions(present, declared, active, grafted)
            continue
        # An absent layer is refused by the mount below, which names the install.
        with contextlib.suppress(ModuleNotFoundError):
            check_api_version(layer, importlib.import_module(layer))
        try:
            plugin(layer)
        except Exception as error:
            try:
                importlib.import_module(layer)
            except ModuleNotFoundError:
                message = (
                    f"layer {layer!r} did not mount: {error}\n"
                    f"  the contract lists it in [workspace] layers, so"
                    f" its distribution ({dist}) belongs in the dev"
                    " group; `uv sync` installs it"
                )
                raise RuntimeError(message) from error
            # Importable, but its plugin offers no tasks (or none are
            # advertised yet): a young layer legitimately ships only
            # content, and content needs no mount. The refusal rides
            # the note, since a layer with tasks that fails to mount
            # reads the same way otherwise.
            print(f"  note: layer {layer} contributes content only (no tasks): {error}")
        else:
            mounted.append(layer)
        present.append(layer)
        _graft_contributions(present, declared, active, grafted)
    # A layer's checks registered as it mounted; their verbs join the
    # ones the workshop generated as it loaded.
    from livery.workshop._checks import generate_verbs

    generate_verbs()
    return tuple(mounted)


def _graft_contributions(
    present: list[str],
    declared: dict[str, dict[str, str]],
    active: dict[str, tuple[str, ...]],
    grafted: set[tuple[str, str]],
) -> None:
    """Import every contribution whose owner and target are both present.

    Called after each layer mounts, so a contribution lands whichever
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
                    f"layer {owner!r} contributes {module!r} for {target}, and"
                    f" the module does not import: {error}"
                ) from error
            grafted.add((owner, target))


def check_api_version(layer: str, module: ModuleType) -> None:
    """Refuse a layer whose declared plugin API version is not this workshop's.

    A layer declares ``WORKSHOP_API_VERSION`` in its plugin module;
    one that declares nothing is taken at the current version.
    """
    declared = getattr(module, "WORKSHOP_API_VERSION", API_VERSION)
    if declared != API_VERSION:
        raise RuntimeError(
            f"layer {layer!r} declares workshop API version {declared!r}; this"
            f" workshop is version {API_VERSION}. Install a release of the"
            " layer written for this workshop, or of the workshop the layer"
            " was written for."
        )


def _plugin_module(layer: str) -> ModuleType | None:
    """The listed layer's plugin module; None for the workshop itself or an absent one.

    The workshop declares nothing about itself here, and an absent
    layer is the mount's to refuse, naming the install.
    """
    if layer == SELF:
        return None
    try:
        return importlib.import_module(layer)
    except ModuleNotFoundError:
        return None


def layer_dependencies(start: Path | None = None) -> dict[str, tuple[str, ...]]:
    """Each listed layer's declared dependencies, import path to import paths.

    Read from the ``WORKSHOP_DEPENDS`` attribute of each layer's plugin
    module; the workshop itself and a layer that cannot be imported
    declare none here, since the mount teaches the install.
    """
    found: dict[str, tuple[str, ...]] = {}
    for layer in layer_names(start):
        module = _plugin_module(layer)
        declared = () if module is None else getattr(module, DEPENDS_ATTRIBUTE, ())
        found[layer] = tuple(str(name) for name in declared)
    return found


def layer_tools(start: Path | None = None) -> dict[str, tuple[str, ...]]:
    """Each listed layer's declared tools, import path to requirement strings.

    Read from ``WORKSHOP_TOOLS`` on the plugin module, a tuple of
    requirement strings such as ``("docker>=27",)``. The workshop
    itself and a layer that cannot be imported declare none here. A
    value of another shape refuses naming the layer: a tool declared
    in a shape the lock cannot read is a tool the environment lacks in
    silence.

    Raises:
        RuntimeError: when a layer's declaration is not a tuple of
            strings.
    """
    found: dict[str, tuple[str, ...]] = {}
    for layer in layer_names(start):
        module = _plugin_module(layer)
        declared = () if module is None else getattr(module, TOOLS_ATTRIBUTE, ())
        if isinstance(declared, str) or not (
            isinstance(declared, (tuple, list))
            and all(isinstance(text, str) for text in declared)
        ):
            raise RuntimeError(
                f"layer {layer!r} declares {TOOLS_ATTRIBUTE} as {declared!r};"
                ' it is a tuple of requirement strings, ("docker>=27",)'
            )
        found[layer] = tuple(declared)
    return found


def contributions(start: Path | None = None) -> dict[str, dict[str, str]]:
    """Each listed layer's declared contributions, owner to target to module.

    Read from ``WORKSHOP_FOR`` on the plugin module, a map from a
    target layer's import path to the module carrying the
    registrations for that target. A value of another shape refuses
    naming the layer.

    Raises:
        RuntimeError: when a layer's declaration is not a map of
            strings to strings.
    """
    found: dict[str, dict[str, str]] = {}
    for layer in layer_names(start):
        module = _plugin_module(layer)
        declared = {} if module is None else getattr(module, FOR_ATTRIBUTE, {})
        if not isinstance(declared, dict) or not all(
            isinstance(target, str) and isinstance(name, str)
            for target, name in declared.items()
        ):
            raise RuntimeError(
                f"layer {layer!r} declares {FOR_ATTRIBUTE} as {declared!r}; it"
                " is a map from a target layer's import path to the module"
                ' carrying the registrations for it, {"livery.workshop.python":'
                ' "acme.house.python"}'
            )
        found[layer] = dict(declared)
    return found


def resolved_targets(start: Path | None = None) -> dict[str, tuple[str, ...]]:
    """Each listed layer's active targets, in list order.

    The entry's ``for`` list when it has one, since that is the truth
    once written; otherwise every declared target that is listed.
    """
    names = layer_names(start)
    declared = contributions(start)
    written = layer_targets(start)
    found: dict[str, tuple[str, ...]] = {}
    for owner in names:
        targets = written.get(owner)
        if targets is None:
            targets = tuple(name for name in names if name in declared.get(owner, {}))
        found[owner] = targets
    return found


def describe_layers(start: Path | None = None) -> list[str]:
    """The lines ``fm layers`` prints: each layer with what it declares."""
    names = layer_names(start)
    who = requirers(start)
    tools = layer_tools(start)
    targets = resolved_targets(start)
    lines: list[str] = []
    for name in names:
        marker = " (this package)" if name == SELF else ""
        needed = who.get(name, ())
        by = f" (required by {', '.join(needed)})" if needed else ""
        lines.append(f"  {name}{marker}{by}")
        if tools.get(name):
            lines.append(f"    tools: {', '.join(tools[name])}")
        if targets.get(name):
            lines.append(f"    for: {', '.join(targets[name])}")
    return lines


def closure_problems(start: Path | None = None) -> list[str]:
    """Why ``[workspace] layers`` is not closed under the layers' declarations.

    Empty when every declared dependency is listed before its
    dependent. A missing one names the fix; one listed after its
    dependent names the move, which is a person's edit.
    """
    names = layer_names(start)
    order = {name: index for index, name in enumerate(names)}
    problems: list[str] = []
    for layer, depends in layer_dependencies(start).items():
        for needed in depends:
            if needed not in order:
                problems.append(
                    f"[workspace] layers lists {layer}, which depends on"
                    f" {needed}, and does not list it; the gate's --fix adds"
                    f" it before {layer}"
                )
            elif order[needed] > order[layer]:
                problems.append(
                    f"[workspace] layers lists {needed} after {layer}, which"
                    f" depends on it; move {needed} before {layer}"
                )
    declared = contributions(start)
    for owner, targets in layer_targets(start).items():
        for target in targets or ():
            if target not in order:
                problems.append(
                    f"[workspace] layers: the entry for {owner} names {target}"
                    " in `for`, and does not list it; list it, or remove it"
                    " from `for`"
                )
            elif target not in declared.get(owner, {}):
                problems.append(
                    f"[workspace] layers: the entry for {owner} names {target}"
                    f" in `for`, and {owner} declares no contribution for it;"
                    " remove it from `for`"
                )
    return problems


def requirers(start: Path | None = None) -> dict[str, tuple[str, ...]]:
    """Each listed layer to the listed layers that declare a dependency on it."""
    found: dict[str, list[str]] = {name: [] for name in layer_names(start)}
    for layer, depends in layer_dependencies(start).items():
        for needed in depends:
            found.setdefault(needed, []).append(layer)
    return {name: tuple(who) for name, who in found.items()}


def write_layers(root: Path) -> list[str]:
    """Add each missing declared dependency to ``[workspace] layers``.

    Returns the lines written, one per entry added.

    The entry lands before its first dependent, with a comment naming
    who requires it, so the list stays the whole truth a reader sees.
    A dependency listed out of order is not moved: that is a person's
    edit, and the judge that follows names it. Idempotent. A root
    without a contract lists no layers, so nothing is written.
    """
    contract = root / "workshop.toml"
    if not contract.is_file():
        return []
    text = contract.read_text(encoding="utf-8")
    written: list[str] = []
    names = list(layer_names(root))
    for layer, depends in layer_dependencies(root).items():
        for needed in depends:
            if needed in names:
                continue
            text, done = _insert_layer(text, needed, before=layer)
            if not done:
                continue
            names.insert(names.index(layer), needed)
            written.append(
                f"  layering: [workspace] layers gains {needed} before {layer},"
                " which requires it"
            )
    recorded = layer_targets(root)
    for owner, targets in resolved_targets(root).items():
        if recorded.get(owner) is not None or not targets:
            continue
        text, done = _write_for(text, owner, targets)
        if not done:
            continue
        written.append(
            f"  layering: [workspace] layers records {owner} for {', '.join(targets)}"
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
    table = f'{{ import = "{owner}", for = [{listed}] }}'
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
        rf'^(\s*\{{ import = "{re.escape(owner)}"(?:, [a-z]+ = [^,}}]+)*)'
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
    inline = re.compile(r"^(layers = \[)(.*?)(\])", re.M | re.S)
    match = inline.search(text)
    if match is None:
        return text, False
    items = [item.strip() for item in match.group(2).split(",") if item.strip()]
    target = f'"{owner}"'
    if target not in items:
        return text, False
    items[items.index(target)] = table
    return text[: match.start()] + "layers = [" + ", ".join(items) + "]" + text[
        match.end() :
    ], True


def _insert_layer(text: str, needed: str, *, before: str) -> tuple[str, bool]:
    """*text* with *needed* listed before *before*'s entry, and whether it was."""
    pattern = re.compile(rf'^(\s*)"{re.escape(before)}",?\s*(#.*)?$', re.M)
    match = pattern.search(text)
    if match is not None:
        indent = match.group(1)
        line = f'{indent}"{needed}",  # required by {before}\n'
        return text[: match.start()] + line + text[match.start() :], True
    inline = re.compile(r"^(layers = \[)(.*?)(\])", re.M | re.S)
    match = inline.search(text)
    if match is None:
        return text, False
    items = [item.strip() for item in match.group(2).split(",") if item.strip()]
    target = f'"{before}"'
    if target not in items:
        return text, False
    items.insert(items.index(target), f'"{needed}"')
    return text[: match.start()] + "layers = [" + ", ".join(items) + "]" + text[
        match.end() :
    ], True


def available_layers(
    listed: tuple[str, ...], advertised: list[tuple[str, str]] | None = None
) -> list[tuple[str, str]]:
    """Installed layers the contract does not list, as (import path, distribution).

    *advertised* is every distribution advertising a ``footman.tasks``
    entry point, as (import path, distribution); read from the
    installed metadata when None. Listing is the only activation
    channel, so these do nothing until the contract names them, and
    the doctor says so.
    """
    if advertised is None:
        from importlib.metadata import entry_points

        advertised = []
        for entry in entry_points(group="footman.tasks"):
            module = entry.value.split(":", 1)[0]
            dist = entry.dist.name if entry.dist is not None else ""
            advertised.append((module, dist))
    from livery.footman import _paths  # pyright: ignore[reportPrivateUsage]

    builtin = set(_paths.builtin())
    seen: set[str] = set()
    found: list[tuple[str, str]] = []
    for module, dist in advertised:
        top = module.split(".")[0]
        if module in listed or module in builtin or module == SELF or top in seen:
            continue
        if any(module == name or module.startswith(name + ".") for name in listed):
            continue
        if top in ("livery", "footman") and module.startswith(
            ("livery.footman", "footman")
        ):
            continue
        seen.add(top)
        found.append((module, dist))
    return found
