# The page shows the part below. The lines above it give the part the
# names the page's earlier examples defined.
from livery.footman.api import task


@task
def fmt(): ...


@task
def lint(): ...


@task
def test(): ...


# --8<-- [start:part-1]
from typing import Annotated
from livery.footman.api import task
from livery.footman.params import forward


@task(pre=[fmt, lint, test])
def check(fix: Annotated[bool, forward] = False):
    "fm check --fix reaches fmt & lint; test (no `fix`) runs defaulted."
# --8<-- [end:part-1]
