# The page shows the part below. The lines above it give the part the
# names the page's earlier examples defined.
from livery.footman import task


# --8<-- [start:part-1]
@task
def deploy(target: str, fix: bool = False):
    """Ship a build.

    Parameters
    ----------
    target : str
        where to deploy
    fix : bool
        apply fixes first
    """
# --8<-- [end:part-1]
