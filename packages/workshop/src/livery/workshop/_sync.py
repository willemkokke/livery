"""``fm sync``: deliver every mounted layer's content to the repository.

Three channels, walked in layer order so a later layer's same-named
file wins and the instance always wins last:

- fragments into ``.workshop/`` (gitignored, regenerated wholesale):
  each layer's ``content/fragments/*``, the files the managed
  ``CLAUDE.md`` stub imports.
- skills and hooks into ``.claude/skills`` and ``.claude/hooks``
  through the materialiser: links where possible, copies where not,
  local overrides kept and named.
- the managed ``CLAUDE.md`` stub itself: one import line per
  materialised fragment, then ``CLAUDE.project.md``, the repository's
  own file that nobody else writes.

Idempotent: a second run changes nothing and says nothing.
"""

from __future__ import annotations

import importlib
from importlib import resources
from pathlib import Path

from footman import fail, task

from livery.workshop._layers import layer_names, workspace_root
from livery.workshop._materialise import materialise, write_lf

_STUB_HEADER = (
    "<!-- Managed by `fm sync`: one import per layer fragment, in layer\n"
    "     order, then the repository's own CLAUDE.project.md, which always\n"
    "     wins. Edit CLAUDE.project.md, never this file. -->\n"
)

#: The fragments every stub imports first, in this order, when a layer
#: ships them: the voice and documentation rules read before any
#: layer's own rules.
_GUIDANCE_FIRST = ("interaction-voice.md", "documentation-standards.md")


def _layer_content(layer: str) -> Path | None:
    """The installed layer's ``content/`` directory, or None.

    A layer is a Python package; its content ships inside the wheel.
    In the monorepo the "wheel" is the editable source tree, which is
    what lets the materialised links point back into the repository.
    """
    try:
        module = importlib.import_module(layer)
    except ModuleNotFoundError:
        return None
    root = resources.files(module)
    content = Path(str(root)) / "content"
    return content if content.is_dir() else None


def sync_workspace(root: Path) -> list[str]:
    """Deliver every layer's content into *root*; the summary lines.

    The engine behind ``fm sync``, separated so tests drive it against
    temporary trees.
    """
    lines: list[str] = []
    layers = layer_names(root)
    contents = [
        (layer, content)
        for layer in layers
        if (content := _layer_content(layer)) is not None
    ]

    workshop_dir = root / ".workshop"
    workshop_dir.mkdir(exist_ok=True)
    fragments: dict[str, Path] = {}
    for _layer, content in contents:
        fragment_dir = content / "fragments"
        if not fragment_dir.is_dir():
            continue
        for fragment in sorted(fragment_dir.iterdir()):
            if fragment.is_file():
                fragments[fragment.name] = fragment
    written = 0
    for name, source in fragments.items():
        target = workshop_dir / name
        body = source.read_bytes()
        if not target.is_file() or target.read_bytes() != body:
            target.write_bytes(body)
            written += 1
    for stale in sorted(workshop_dir.iterdir()):
        if stale.is_file() and stale.name not in fragments:
            stale.unlink()
            lines.append(f"  .workshop: removed {stale.name} (no layer ships it)")
    if written:
        lines.append(f"  .workshop: {written} fragment(s) refreshed")

    for _layer, content in contents:
        lines += materialise(root, content / "skills", "skills")
        lines += materialise(root, content / "hooks", "hooks")

    ordered = [name for name in _GUIDANCE_FIRST if name in fragments]
    ordered += [name for name in sorted(fragments) if name not in _GUIDANCE_FIRST]
    stub = _STUB_HEADER
    stub += "".join(f"@.workshop/{name}\n" for name in ordered)
    stub += "@CLAUDE.project.md\n"
    stub_path = root / "CLAUDE.md"
    current = stub_path.read_text(encoding="utf-8") if stub_path.is_file() else ""
    if current != stub:
        write_lf(stub_path, stub)
        lines.append("  CLAUDE.md: stub regenerated")
    project = root / "CLAUDE.project.md"
    if not project.is_file():
        write_lf(
            project,
            "# This repository\n\nThe repository's own facts: nobody else"
            " writes here.\n",
        )
        lines.append("  CLAUDE.project.md: seeded; put the repository's facts here")
    return lines


@task
def sync() -> None:
    """Materialise every layer's content: fragments, skills, hooks, the stub.

    Idempotent: re-running it is the recovery procedure, and a quiet
    run means everything already matched.
    """
    root = workspace_root()
    if root is None:
        fail("no workspace: no livery.toml above the working directory")
    for line in sync_workspace(root):
        print(line)
