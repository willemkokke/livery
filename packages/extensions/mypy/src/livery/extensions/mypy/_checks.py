"""mypy's check: the type checker, once per platform, over the python files it claims.

The extension's ``extension.toml`` declares it and names the body here.
``typecheck.mypy`` narrows by paths: the workshop names the paths a run
reaches ([livery.workshop.scoped_paths][]) and splits them into
calls ([livery.workshop.run_batched][]); a run that reaches the
whole calls mypy with no path, so it reads the ``files`` of
``mypy.ini``. mypy has no all-platforms mode, so every call checks
linux, darwin and win32 in parallel, each with a cache of its own:
mypy's SQLite cache takes one writer. The check hands mypy the words
after ``--`` on its own verb (``fm typecheck.mypy -- --strict``), in
every platform's call. A body resolves its runner on this module when
it runs, so a test that replaces `run_typecheck` here sees its
replacement called.
"""

from __future__ import annotations

from functools import partial

import livery.toolroom.tools as tools
from livery.footman import parallel, step
from livery.workshop import WHOLE, GateContext, run_batched, scoped_paths

#: The platforms every call checks, linux first: a bare ``mypy`` checks
#: linux, the platform ``mypy.ini`` names.
PLATFORMS = ("linux", "darwin", "win32")

#: Where the caches live, a directory per platform, with the workshop's
#: state and never in the root.
CACHE = ".workshop/.cache/mypy"


def run_typecheck(paths: tuple[str, ...] = (), arguments: tuple[str, ...] = ()) -> None:
    """Type-check *paths* with mypy on every platform, in parallel.

    No path checks the configured ``files``. Each platform's verdict is
    its own, and every one gates. *arguments* go to mypy before the
    paths.
    """

    def on(platform: str) -> None:
        tools.mypy(
            *arguments, *paths, platform=platform, cache_dir=f"{CACHE}/{platform}"
        )

    parallel(*(step(on, title=f"mypy_{platform}")(platform) for platform in PLATFORMS))


def judge_typecheck(ctx: GateContext) -> None:
    """Type-check the paths the run reaches, or the configured files, per platform."""
    chosen = scoped_paths(ctx, "typecheck.mypy")
    if chosen == WHOLE:
        run_typecheck(arguments=ctx.arguments)
        return
    run_batched(chosen, partial(run_typecheck, arguments=ctx.arguments))
