# The page shows the part below. The lines above it give the part the
# names the page's earlier examples defined.
from livery.footman.api import task


# --8<-- [start:part-1]
@task
def deploy(target: str, fix: bool = False):
    """Ship a build.

    Checks out, builds, and uploads — see the release runbook.

    Args:
        target: where to deploy
        fix: apply fixes first
    """
# --8<-- [end:part-1]
