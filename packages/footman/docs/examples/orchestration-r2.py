# The page shows the part below. The lines above it give the part the
# names the page's earlier examples defined.
from livery.footman.api import task


@task
def fmt(): ...


@task
def lint(): ...


@task
def typecheck(): ...


@task
def test(): ...


@task
def notify(): ...


# --8<-- [start:part-1]
@task(pre=[fmt, lint, typecheck, test])  # all four run before check
def check(): ...


@task(post=[notify])  # notify runs after deploy succeeds
def deploy(): ...
# --8<-- [end:part-1]
