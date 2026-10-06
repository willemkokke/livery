# --8<-- [start:part-1]
from livery.footman.api import plugin

plugin("footman.docs")
# --8<-- [end:part-1]

# --8<-- [start:part-2]
from pathlib import Path
from livery.footman.api import group

docs = group("docs", help="Documentation")


@docs.task(name="build")
def docs_build(check: bool = False):
    "Build the docs site; regenerates the task reference first."
    from livery.footman.docs import globals_, page, site
    from livery.toolroom.tools.api import zensical

    site(Path("docs/tasks"))
    page(target="docs", heading=3, out=Path("_generated/tasks-page.md"))
    globals_(out=Path("_generated/globals.md"))
    zensical.build(clean=True, strict=check)
# --8<-- [end:part-2]
