# --8<-- [start:part-1]
from livery.footman import task, parallel
from livery.toolroom.tools import basedpyright, pytest, ruff


@task
def lint(fix: bool = False):
    "Lint with ruff."
    ruff.check("src", "tests", fix=fix)


@task
def typecheck():
    "Type-check with basedpyright."
    basedpyright()


@task
def test(*pytest_args: str):
    "Run the test suite."
    pytest(*pytest_args)


@task
def check():
    "Lint, typecheck, and test, in parallel."
    # A call with arguments goes in the block form; bare tasks ride along.
    with parallel():
        lint(fix=False)
        typecheck()
        test()
# --8<-- [end:part-1]

# --8<-- [start:part-2]
from typing import Literal


@task
def deploy(target: Literal["dev", "staging", "prod"]):
    "Ship to an environment."
# --8<-- [end:part-2]
