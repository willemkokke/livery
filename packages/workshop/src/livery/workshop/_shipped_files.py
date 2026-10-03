"""Deliver the files the listed extensions ship, through the fragment engine.

An extension ships a workspace file as a template in its wheel, under
`content/root/<path>` for a file at the workspace root and
`content/package/<path>` for that file in every package listing the
extension. `fm sync` composes them with
[livery.workshop._fragment_engine.plan][] and writes them with
[livery.workshop._fragment_engine.apply][]; the gate's template check
judges them with [livery.workshop._fragment_engine.drift][].

The prose fragments, skills and hooks under the same `content/`
directory are delivered by `livery.workshop._prose` and
`livery.workshop._materialise` instead.
"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any, cast

from livery.footman import api as footman
from livery.footman.api import fail
from livery.workshop._extensions import extension_content, stack_names
from livery.workshop._fragment_engine import (
    PACKAGE_PREFIX,
    Fragment,
    Output,
    apply,
    drift,
    plan,
)

ROOT_CONTENT = "root"
"""The `content/` subdirectory whose files land at the workspace root."""

PACKAGE_CONTENT = "package"
"""The `content/` subdirectory whose files land in each package."""


def shipped(root: Path) -> tuple[list[Fragment], list[str]]:
    """Every listed extension's shipped files as fragments, and the order they apply."""
    order = list(stack_names(root))
    fragments: list[Fragment] = []
    for extension in order:
        content = extension_content(extension)
        if content is None:
            continue
        for kind, prefix in ((ROOT_CONTENT, ""), (PACKAGE_CONTENT, PACKAGE_PREFIX)):
            base = content / kind
            if not base.is_dir():
                continue
            for source in sorted(base.rglob("*")):
                if not source.is_file() or "__pycache__" in source.parts:
                    continue
                relative = source.relative_to(base).as_posix()
                fragments.append(
                    Fragment(extension, relative, f"{prefix}{relative}", source=source)
                )
    fragments += _check_contributions(order)
    return fragments, order


def _check_contributions(order: list[str]) -> list[Fragment]:
    """What each registered check says to the editor, as data its extension contributes.

    A check's settings land in `.vscode/settings.json` and its marketplace
    id in `.vscode/extensions.json`, each a fragment of its own, so a check
    that is unregistered takes its lines with it.
    """
    from livery.workshop._checks import checks_by_name

    found: list[Fragment] = []
    for name, record in checks_by_name().items():
        if record.extension not in order:
            continue
        for fragment in record.fragments:
            if fragment.file == ".vscode/settings.json":
                found.append(
                    Fragment(
                        record.extension,
                        f"check {name} settings",
                        fragment.file,
                        fragment.text,
                        contributes=True,
                    )
                )
        if record.editor_extension:
            found.append(
                Fragment(
                    record.extension,
                    f"check {name} recommendation",
                    ".vscode/extensions.json",
                    json.dumps({"recommendations": [record.editor_extension]}),
                    contributes=True,
                )
            )
    return found


def _data(root: Path) -> dict[str, str]:
    """What every shipped template reads: the runner's name and the project's."""
    from livery.workshop._templates import read_answers

    answers = root / ".copier-answers.yml"
    named = read_answers(answers).get("project_name") if answers.is_file() else None
    return {"prog": footman.prog(), "project_name": str(named or root.resolve().name)}


def outputs(root: Path) -> tuple[Output, ...]:
    """The shipped files as the listed extensions render them for *root*."""
    return _composed(root)[0]


def _composed(root: Path) -> tuple[tuple[Output, ...], list[str]]:
    """The shipped files for *root*, and a line naming the LFS rules left out.

    While the workspace has Git LFS off, an `.gitattributes` keeps no
    LFS line; the line says which patterns were left out and the
    setting that composes them.
    """
    from livery.workshop._lfs import KEY, lfs_enabled, without_lfs

    fragments, order = shipped(root)
    planned = plan(root, fragments, order, _data(root))
    if lfs_enabled(root):
        return planned, []
    kept: list[Output] = []
    notes: list[str] = []
    for output in planned:
        if PurePosixPath(output.path).name == ".gitattributes":
            text, left = without_lfs(output.body.decode())
            if left:
                notes.append(
                    f"  {output.path}: Git LFS is off, so the LFS rules for"
                    f" {', '.join(left)} are left out; `[workspace] {KEY} = true`"
                    " in workshop.toml composes them"
                )
                output = Output(output.path, text.encode(), output.owners)
        kept.append(output)
    return tuple(kept), notes


def deliver(root: Path) -> list[str]:
    """Write the shipped files into *root*; one line per file that changed."""
    planned, notes = _composed(root)
    return notes + apply(root, planned)


def shipped_drift(root: Path) -> list[str]:
    """One line per shipped file whose committed bytes are not its render."""
    return [line.strip() for line in drift(root, outputs(root))]


def relocate(root: Path) -> list[str]:
    """Move what a composed JSON file has and its render lacks into its region.

    VS Code writes a setting changed through its UI into `settings.json`
    just before the closing brace, and a recommendation into the list,
    both outside the repository's region. A top-level key the render does
    not produce, or an item a list holds that the render's list does not,
    is the repository's: it moves into the file's first region, and the
    file is written as the render with that region. A key the render does
    produce, set here to another value, is an override of an extension's
    or a check's setting; it refuses, naming the key and both values,
    since which one should stand is a person's call.

    Returns:
        One line per key or item moved.

    Raises:
        Failed: for a key the render owns that is set to another value.
    """
    from livery.workshop._regions import regions_in

    lines: list[str] = []
    for output in outputs(root):
        target = root / output.path
        if not output.path.endswith(".json") or not target.is_file():
            continue
        rendered = output.body.decode()
        regions = regions_in(rendered)
        theirs = _jsonc(target.read_text(encoding="utf-8"))
        ours = _jsonc(rendered)
        if not regions or theirs is None or ours is None:
            continue
        added: list[str] = []
        clashes: list[str] = []
        for key, value in theirs.items():
            if key not in ours:
                body = json.dumps(value, indent=2).replace("\n", "\n  ")
                added.append(f"  {json.dumps(key)}: {body},")
                lines.append(f"  {output.path}: moved {key} into the region")
            elif isinstance(value, list) and isinstance(ours[key], list):
                for item in cast("list[object]", value):
                    if item not in ours[key]:
                        added.append(f"    {json.dumps(item)},")
                        lines.append(f"  {output.path}: moved {item} into the region")
            elif value != ours[key]:
                clashes.append(
                    f"{key}: the render sets {json.dumps(ours[key])}, the file"
                    f" {json.dumps(value)}"
                )
        if clashes:
            fail(
                f"{output.path}: a setting the render owns was changed here; keep"
                " the render's value, or change it where its extension takes"
                " configuration:\n  " + "\n  ".join(clashes)
            )
        if not added:
            continue
        text = rendered.split("\n")
        text[regions[0].last - 1 : regions[0].last - 1] = added
        target.write_text("\n".join(text), encoding="utf-8")
    return lines


def _jsonc(text: str) -> dict[str, Any] | None:
    """*text* read as JSON with whole-line comments and trailing commas, or None."""
    import re

    kept = "\n".join(
        line for line in text.split("\n") if not line.lstrip().startswith("//")
    )
    kept = re.sub(r",(\s*[}\]])", r"\1", kept)
    try:
        loaded = json.loads(kept)
    except ValueError:
        return None
    return cast("dict[str, Any]", loaded) if isinstance(loaded, dict) else None
