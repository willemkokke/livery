"""The package's docs generators: the tool pages and the colour table.

Advertised as the ``footman.tasks`` entry point named
``livery.toolroom`` and loaded only through footman's own
``plugin()``, the same exemption ``livery.forge._dev`` carries: the
distribution stays dependency-free, and footman is only imported
where footman itself is the loader. The pages build from the
checked-in stubs and colour data, so a docs build needs nothing on
PATH and says exactly what ships.
"""

from __future__ import annotations

from livery.footman import group

toolroom_group = group("toolroom", help="toolroom's docs generators")


@toolroom_group.task(name="pages")
def docs_pages() -> None:
    """Write the per-tool reference pages, the index, and the colour page.

    Into this package's generated docs home, from the checked-in
    stubs and colour data. Idempotent: the same inputs rewrite the
    same bytes.
    """
    from pathlib import Path

    from livery.toolroom._machinery._tasks import colour_page, pages

    out = Path("packages/toolroom/docs/_generated")
    pages(out / "tools")
    (out / "colour.md").write_text(colour_page(), encoding="utf-8")
    print(f"  tool pages and the colour table into {out}")
