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

from pathlib import Path, PurePosixPath

from livery.footman import api as footman
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
    return fragments, order


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
    planned = plan(root, fragments, order, {"prog": footman.prog()})
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
