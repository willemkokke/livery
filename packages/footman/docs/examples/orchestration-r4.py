# The page shows the part below. The lines above it give the part the
# names the page's earlier examples defined.
from livery.footman import task


@task
def lint(): ...


@task
def typecheck(): ...


@task
def test(): ...


# --8<-- [start:part-1]
from livery.footman import task, parallel, step


def clean(): ...


@task
def check():
    parallel(lint, typecheck, test, step(clean)())
# --8<-- [end:part-1]
