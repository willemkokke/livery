"""The running CLI's identity, asked from footman, never configured.

The emitters and the rendered project prose spell the runner by the
name this module answers, so a branded CLI regenerates files that
call itself and ``fm`` regenerates files that call ``fm``.
Rebranding an instance is running the update under the branded CLI:
the managed generated files re-emit with the new name.
"""

from __future__ import annotations


def runner_prog() -> str:
    """The command name of the CLI this process runs under.

    ``footman.prog()``, public since footman 0.50, which the
    lockfile floors; the default brand answers ``fm``.
    """
    import footman

    return footman.prog()
