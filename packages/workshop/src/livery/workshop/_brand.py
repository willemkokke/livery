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

    Workaround: footman installs the running brand into
    ``footman._paths`` and exposes no public accessor yet
    (footman#557 asks for one), so the private attribute is the
    source, with the default brand's ``fm`` as the fallback. Switch
    to the public accessor and delete this note together when the
    ask lands.
    """
    try:
        from footman import _paths

        return str(getattr(_paths, "_prog", "") or "fm")
    except Exception:
        return "fm"
