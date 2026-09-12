"""The package's docs generators: the tool pages and the colour table.

`fm tools.docs`, on the bench's one group, mounted when a workspace
names the bench as a layer; the pages are toolroom's, written into
its docs tree from the checked-in stubs and colour data, so a docs
build needs nothing on PATH and says exactly what ships. toolroom
declares the verb as its docs generator.
"""

from __future__ import annotations

from livery.toolroom.bench._tasks import tasks


@tasks.task(name="docs")
def docs_pages() -> None:
    """Write the per-tool reference pages, the index, and the colour page.

    Into this package's generated docs home, from the checked-in
    stubs and colour data. Idempotent: the same inputs rewrite the
    same bytes.
    """
    from pathlib import Path

    from livery.toolroom.bench._tasks import colour_page, pages

    out = Path("packages/toolroom/docs/_generated")
    pages(out / "tools")
    (out / "colour.md").write_text(colour_page(), encoding="utf-8")
    print(f"  tool pages and the colour table into {out}")
