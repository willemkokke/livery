# The page shows the part below. The lines above it give the part the
# names the page's earlier examples defined.
from livery.footman.api import task


# --8<-- [start:part-1]
@task(confirm="Deploy to production?")
def deploy(): ...
# --8<-- [end:part-1]
