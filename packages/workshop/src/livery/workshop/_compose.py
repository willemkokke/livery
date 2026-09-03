"""Overlay composition: the layer stack becomes one template source.

The contract's layers list is a template stack, rendered bottom to
top into one source tree: an upper layer adds files to any kind, or
replaces a base file wholesale, never edits one. A replace is
declared in the overlay's ``overlay.toml`` with a reason, because
jinja-patched content is indistinguishable from a replace and a
wholesale replace ends inheritance for that file. The composed
``copier.yml`` is generated: the base questions plus each overlay's
declared questions, every one defaulted so an update never prompts.

Each layer's template tree lives inside its package module, beside
``content/``: the workspace's own member tree when the layer is
self-hosting (a home edits at HEAD), the installed wheel's data
otherwise, so composition never touches the network.
"""

from __future__ import annotations

import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from footman import fail

from livery.workshop._layers import layer_entries

#: The overlay's declaration file, at its template tree's root.
OVERLAY_MANIFEST = "overlay.toml"


@dataclass(frozen=True)
class ComposedSource:
    """One composed template tree and where each file came from.

    Attributes:
        path: The composed tree, rendered as one copier source.
        owners: Each relative file's owning layer, the later layer
            winning for a declared replace.
    """

    path: Path
    owners: dict[str, str]


def layer_template_tree(root: Path, layer: str) -> Path | None:
    """*layer*'s template tree, or None when it ships none.

    A layer that is a member of this workspace serves its tree from
    the working copy (a home edits at HEAD, contract 12); an
    installed layer serves the wheel's data. A layer without a
    ``templates/`` directory contributes nothing, which is legal.
    Membership is a path probe, not the layering lint: a package
    mid-birth (rendered, not yet wired) must not stop a render.
    """
    module_path = Path(*layer.split("."))
    for src in sorted(root.glob("packages/*/src")):
        candidate = src / module_path / "templates"
        if candidate.is_dir():
            return candidate
    try:
        from importlib.resources import files

        resource = files(layer) / "templates"
    except (ImportError, ModuleNotFoundError):
        return None
    installed = Path(str(resource))
    return installed if installed.is_dir() else None


def read_overlay_manifest(tree: Path) -> tuple[dict[str, str], dict[str, Any]]:
    """The overlay's declared replaces (path to reason) and questions."""
    manifest = tree / OVERLAY_MANIFEST
    if not manifest.is_file():
        return {}, {}
    data = tomllib.loads(manifest.read_text("utf-8"))
    replaces: dict[str, str] = {}
    for entry in data.get("replace", []):
        path = str(entry.get("path", ""))
        reason = str(entry.get("reason", ""))
        if not path or not reason:
            fail(
                f"{manifest}: every [[replace]] carries path and reason;"
                " a wholesale replace ends inheritance for that file and"
                " says why"
            )
        replaces[path] = reason
    questions = data.get("questions", {})
    if not isinstance(questions, dict):
        fail(f"{manifest}: [questions] is a table of question tables")
    return replaces, dict(questions)


def _copy_tree(
    tree: Path, destination: Path, owners: dict[str, str], layer: str
) -> None:
    """Lay *tree* into *destination* as the base, recording ownership."""
    for path in sorted(tree.rglob("*")):
        if not path.is_file() or path.name == OVERLAY_MANIFEST:
            continue
        relative = path.relative_to(tree)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        owners[relative.as_posix()] = layer


def _apply_overlay(
    tree: Path,
    destination: Path,
    owners: dict[str, str],
    layer: str,
    base_kinds: set[str],
) -> dict[str, Any]:
    """Apply one overlay: add or declared replace, never an edit."""
    replaces, questions = read_overlay_manifest(tree)
    for path in sorted(tree.rglob("*")):
        if not path.is_file() or path.name == OVERLAY_MANIFEST:
            continue
        relative = path.relative_to(tree)
        posix = relative.as_posix()
        kind = relative.parts[0] if len(relative.parts) > 1 else ""
        if kind and kind not in base_kinds:
            listed = ", ".join(sorted(base_kinds))
            fail(
                f"{layer}'s overlay targets the {kind!r} kind, which the"
                f" stack does not have: the kinds here are {listed}"
            )
        target = destination / relative
        if target.exists() and posix not in replaces:
            fail(
                f"{layer}'s overlay ships {posix}, which"
                f" {owners.get(posix, 'the base')} already owns: an"
                " overlay adds or replaces wholesale, never edits."
                " Declare the replace, with its reason, in"
                f" {OVERLAY_MANIFEST}."
            )
        if not target.exists() and posix in replaces:
            fail(
                f"{layer}'s {OVERLAY_MANIFEST} declares a replace of"
                f" {posix}, which no lower layer ships: a stale"
                " declaration hides a real edit later; remove it"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        owners[posix] = layer
    return questions


def _compose_questions(
    destination: Path, contributions: dict[str, dict[str, Any]]
) -> None:
    """Write the composed ``copier.yml``: base questions plus overlays'.

    Generated, never merged by hand (contract 17): wholesale-only
    cannot merge a questions file. Every contributed question must
    carry a default or ``when: false``, the same rule the base's own
    comment states, so an instance updates without a prompt.
    """
    config = destination / "copier.yml"
    text = config.read_text("utf-8")
    additions: list[str] = []
    for layer, questions in contributions.items():
        for name, spec in questions.items():
            if not isinstance(spec, dict) or (
                "default" not in spec and spec.get("when") is not False
            ):
                fail(
                    f"{layer}'s question {name!r} has no default and is"
                    " not when = false: an instance must update without"
                    " a prompt"
                )
            additions.append("")
            additions.append(f"# Contributed by the {layer} layer.")
            additions.append(yaml.safe_dump({name: spec}, sort_keys=False).rstrip())
    if additions:
        text = text.rstrip("\n") + "\n" + "\n".join(additions) + "\n"
        config.write_text(text, encoding="utf-8", newline="\n")


def compose_source(root: Path, destination: Path) -> ComposedSource:
    """Compose the contract's template stack into *destination*.

    Bottom to top per the layers list; the base layer must ship a
    tree, upper layers may. Returns the composed source and the
    per-file owners the drift gate names.
    """
    entries = layer_entries(root)
    if not entries:
        fail("workshop.toml declares no [workspace] layers: nothing to compose")
    stack: list[tuple[str, Path]] = []
    for layer, _dist in entries:
        tree = layer_template_tree(root, layer)
        if tree is not None:
            stack.append((layer, tree))
    if not stack:
        fail(
            "no layer in the stack ships a template tree: the base"
            " layer's wheel carries one, so `uv sync` is the likely fix"
        )
    owners: dict[str, str] = {}
    base_layer, base_tree = stack[0]
    _copy_tree(base_tree, destination, owners, base_layer)
    base_kinds = {child.name for child in base_tree.iterdir() if child.is_dir()}
    contributions: dict[str, dict[str, Any]] = {}
    for layer, tree in stack[1:]:
        questions = _apply_overlay(tree, destination, owners, layer, base_kinds)
        if questions:
            contributions[layer] = questions
    _compose_questions(destination, contributions)
    return ComposedSource(destination, owners)


def stack_has_overlays(root: Path) -> bool:
    """Whether any layer above the base ships a template tree."""
    entries = layer_entries(root)
    return any(layer_template_tree(root, layer) is not None for layer, _ in entries[1:])
