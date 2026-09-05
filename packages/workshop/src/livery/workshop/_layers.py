"""The layer walk: one list in the workspace contract, every channel.

The root ``workshop.toml`` names the layers in precedence order, the
workshop first and the instance implicitly last. Mounting reads that
list and grafts each further layer's footman plugin in order, so a
package installed by accident never changes a repository: discovery
is the list and nothing else.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

#: The layer this package is. Never mounted by name: importing the
#: plugin module IS this layer arriving.
SELF = "livery.workshop"


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
    from footman import (
        _paths,  # pyright: ignore[reportPrivateUsage]
        plugin,
    )

    builtin = set(_paths.builtin())
    mounted = []
    for layer, dist in layer_entries(start):
        if layer == SELF or layer in builtin:
            continue
        try:
            plugin(layer)
        except Exception as error:
            import importlib

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
            # content, and content needs no mount.
            print(f"  note: layer {layer} contributes content only (no tasks)")
        else:
            mounted.append(layer)
    return tuple(mounted)
