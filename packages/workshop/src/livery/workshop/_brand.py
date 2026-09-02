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

    ``footman.prog()`` is the source (public since footman 0.50);
    on an older footman the same value is read from the installed
    brand's module state (footman#557 records that arrangement).
    The default brand's ``fm`` is the fallback either way.
    """
    try:
        import footman

        prog = getattr(footman, "prog", None)
        if callable(prog):
            return str(prog() or "fm")
        from footman import _paths

        return str(getattr(_paths, "_prog", "") or "fm")
    except Exception:
        return "fm"
