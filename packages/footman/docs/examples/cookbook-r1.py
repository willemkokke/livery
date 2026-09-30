# The page shows the part below. The lines above it give the part the
# names the page's earlier examples defined.
from livery.footman import task
from livery.toolroom.tools import pytest


# --8<-- [start:part-1]
@task
def test(*pytest_args: str):
    "Run the test suite."
    pytest(*pytest_args)
# --8<-- [end:part-1]
