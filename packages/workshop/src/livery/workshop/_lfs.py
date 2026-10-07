"""Git LFS as a workspace setting.

`[workspace] lfs = true` in `workshop.toml` turns Git LFS on: the
`git_lfs` tool is required, `fm sync` installs LFS's hooks in the
checkout, the emitted CI checks out the LFS objects, and the LFS
lines the listed extensions ship are composed into `.gitattributes`.
While it is off, which is the default, those lines are left out and
`fm sync` names them, so an extension can offer LFS rules without
forcing LFS on anyone.

An LFS line is any attribute line that sets `filter=lfs`.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from livery.workshop._contract import load_contract

KEY = "lfs"
"""The `[workspace]` key that turns LFS on."""

TOOL = "git_lfs"
"""The tool LFS needs, required while it is on."""


def lfs_enabled(root: Path) -> bool:
    """Whether the workspace at *root* turns Git LFS on."""
    path = root / "workshop.toml"
    if not path.is_file():
        return False
    workspace = cast("dict[str, object]", load_contract(path).get("workspace") or {})
    return workspace.get(KEY) is True


def is_lfs_line(line: str) -> bool:
    """Whether *line* is an attribute line that routes its files through LFS."""
    text = line.strip()
    return bool(text) and not text.startswith("#") and "filter=lfs" in text.split()


def without_lfs(text: str) -> tuple[str, list[str]]:
    """*text* without its LFS lines, and the patterns those lines named."""
    kept: list[str] = []
    left: list[str] = []
    for line in text.splitlines(keepends=True):
        if is_lfs_line(line):
            left.append(line.split()[0])
        else:
            kept.append(line)
    return "".join(kept), left


def install_hooks(root: Path) -> list[str]:
    """Install LFS's hooks in the checkout at *root*; a line when it did.

    `git lfs install --local` is idempotent: it writes the hooks and
    the checkout's filter configuration only where they are missing.
    """
    import livery.toolroom.tools as tools

    if not (root / ".git").exists():
        return []
    result = tools.git_lfs.opts(cwd=root, nofail=True, recorded=False)(
        "install", "--local"
    )
    if result.code != 0:
        return [f"  git lfs install --local failed: {result.stderr.strip()}"]
    return []
