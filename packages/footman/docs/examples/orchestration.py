# --8<-- [start:part-1]
from livery.footman import task


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
# --8<-- [end:part-1]

# --8<-- [start:part-2]
from livery.footman import task, fail


def open_pr() -> bool: ...  # your own lookup


@task
def release(armed: bool = False):
    if not open_pr():
        fail("no open PR for setup — run `fm create repo` first")
    ...
# --8<-- [end:part-2]

# --8<-- [start:part-3]
@task(keep_going=True)  # this gate wants to surface every problem at once
def check(): ...
# --8<-- [end:part-3]
