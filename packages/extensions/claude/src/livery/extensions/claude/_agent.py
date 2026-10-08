"""What Claude Code reads in a workspace, answered for the workshop's engine.

The extension's ``extension.toml`` names these functions as its
``[fragments]`` renders: the workshop calls each with the workspace
root while a workspace lists the extension, and writes what it
answers. ``CLAUDE.md`` is committed; ``.workshop/fragments/`` and
``.claude/`` belong to the checkout alone.
"""

from __future__ import annotations

from pathlib import Path

import livery.footman as footman
from livery.footman import fail
from livery.workshop import AGENT, HUMAN, Prose, guidance, shipped_content

#: Where the agent's guidance lands, under the workspace root: a
#: directory of its own, so the sweep for a withdrawn fragment owns
#: every file it walks.
DELIVERED = ".workshop/fragments"

#: The repository's own fragments' directory, at the workspace root.
OWN = "fragments"

STUB_HEADER = (
    "<!-- Managed by `{prog} sync`: one import per fragment, in section\n"
    "     order, the repository's own fragments/ after them, then its\n"
    "     CLAUDE.project.md, which always wins. Edit CLAUDE.project.md,\n"
    "     never this file. -->\n"
)
"""The ``CLAUDE.md`` stub's header, formatted with the runner's name."""


def _rendered_header(prose: Prose) -> str:
    """The header a rendered fragment's copy opens with."""
    return (
        f"<!-- Rendered by `{footman.prog()} sync` from the registries"
        f" {prose.extension} fills;\n"
        "     the source is the code. An edited copy is a local override,\n"
        "     kept and named until it is deleted. -->\n"
    )


def _delivered(root: Path) -> tuple[dict[str, str], list[str]]:
    """The agent's guidance by delivered name, and the repository's own names.

    Both readers' sets are composed, so a name two fragments would
    deliver as refuses here as it does for the site. A render that
    answers nothing for the agent is left out. The repository's own
    fragments are named, never copied: they live in ``fragments/``
    already.
    """
    chosen = guidance(root, AGENT)
    guidance(root, HUMAN)
    files: dict[str, str] = {}
    own: list[str] = []
    for prose in chosen:
        if not prose.extension:
            own.append(prose.name)
            continue
        text = prose.text(root, AGENT)
        if prose.render is not None:
            if not text:
                continue
            text = _rendered_header(prose) + text
        files[prose.name] = text.replace("\r\n", "\n")
    return files, own


def entry_file(root: Path) -> str:
    """``CLAUDE.md``: an import per delivered fragment, the own ones, the project's."""
    files, own = _delivered(root)
    stub = STUB_HEADER.format(prog=footman.prog())
    stub += "".join(f"@{DELIVERED}/{name}\n" for name in files)
    stub += "".join(f"@{OWN}/{name}\n" for name in own)
    return stub + "@CLAUDE.project.md\n"


def guidance_files(root: Path) -> dict[str, str]:
    """``.workshop/fragments/``: each fragment the agent reads, by delivered name."""
    return _delivered(root)[0]


def claude_files(root: Path) -> dict[str, str | Path]:
    """``.claude/``: each listed extension's skills and hooks, and one settings.json.

    A skill or a hook is a link into the shipping extension's content,
    and a later extension's entry of one name takes an earlier one's
    place. The settings are a copy, which the agent's tooling may edit,
    and at most one extension ships them.
    """
    found: dict[str, str | Path] = {}
    settings = ""
    for extension, content in shipped_content(root):
        for kind in ("skills", "hooks"):
            shipped = content / kind
            if not shipped.is_dir():
                continue
            for entry in sorted(shipped.iterdir()):
                if entry.name.startswith(".") or entry.name == "__pycache__":
                    continue
                found[f"{kind}/{entry.name}"] = entry
        source = content / "settings.json"
        if source.is_file():
            if settings:
                fail(
                    f".claude/settings.json has one owner, and {settings}"
                    f" and {extension} both ship it"
                )
            settings = extension
            text = source.read_text(encoding="utf-8")
            found["settings.json"] = text.replace("\r\n", "\n")
    return found
