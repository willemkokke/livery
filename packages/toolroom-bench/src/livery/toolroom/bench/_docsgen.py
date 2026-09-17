"""The package's docs generators: the tool pages and the colour table.

`fm tools.docs`, on the bench's one group, mounted when a workspace
names the bench as a layer; the pages are toolroom's, written into
its docs tree from the records and the colour data, so a docs build
needs nothing on PATH and says exactly what the records hold. toolroom
declares the verb as its docs generator.
"""

from __future__ import annotations

from livery.toolroom.bench._tasks import tasks


@tasks.task(name="docs")
def docs_pages() -> None:
    """Write the per-tool reference pages, the index, and the colour page.

    Into this package's generated docs home, from the records and the
    colour data; the stubs the pages point at are rendered beside them.
    Idempotent: the same inputs rewrite the same bytes.
    """
    from pathlib import Path

    from livery.toolroom.bench._tasks import STUBS_MODULE, colour_page, pages

    out = Path("packages/toolroom/docs/_generated")
    pages(out / "tools", stubs=out / "stubs" / STUBS_MODULE)
    (out / "colour.md").write_text(colour_page(), encoding="utf-8")
    print(f"  tool pages and the colour table into {out}")
