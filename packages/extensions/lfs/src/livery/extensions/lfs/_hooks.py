"""Install Git LFS's hooks and filter configuration in the checkout: the sync step."""

from __future__ import annotations

from typing import TYPE_CHECKING

from livery.footman import fail

if TYPE_CHECKING:
    from livery.workshop import PhaseContext


def install(ctx: PhaseContext) -> None:
    """Install LFS's hooks and filter configuration in the checkout at the root.

    ``git lfs install --local`` writes the hooks and the checkout's
    filter configuration only where they are missing, so a sync that
    finds them in place changes nothing. A workspace that is not a git
    checkout gets nothing.

    Raises:
        Failed: when ``git lfs install --local`` exits non-zero, with
            git-lfs's own words.
    """
    import livery.toolroom.tools as tools

    if not (ctx.root / ".git").exists():
        return
    result = tools.git_lfs.opts(cwd=ctx.root, nofail=True, recorded=False)(
        "install", "--local"
    )
    if result.code != 0:
        fail(f"git lfs install --local failed: {result.stderr.strip()}")
