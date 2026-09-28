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
    if root is None:
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
    for layer, dist in layer_entries(start):
        if layer == SELF or layer in builtin:
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
    return tuple(mounted)


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


def layer_dependencies(start: Path | None = None) -> dict[str, tuple[str, ...]]:
    """Each listed layer's declared dependencies, import path to import paths.

    Read from the ``WORKSHOP_DEPENDS`` attribute of each layer's plugin
    module; the workshop itself and a layer that cannot be imported
    declare none here, since the mount teaches the install.
    """
    found: dict[str, tuple[str, ...]] = {}
    for layer in layer_names(start):
        if layer == SELF:
            found[layer] = ()
            continue
        try:
            module = importlib.import_module(layer)
        except ModuleNotFoundError:
            found[layer] = ()
            continue
        declared = getattr(module, DEPENDS_ATTRIBUTE, ())
        found[layer] = tuple(str(name) for name in declared)
    return found


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
    if written:
        contract.write_text(text, encoding="utf-8")
    return written


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
