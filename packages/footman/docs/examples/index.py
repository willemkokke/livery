# --8<-- [start:part-1]
# tasks.py
from typing import Annotated, Literal

from livery.footman import doc, suggest, task
from livery.toolroom.tools import git


def branches() -> list[str]:
    "Every branch in this repo, asked of git rather than written down."
    return git.branch(format="%(refname:short)").stdout.split()


@task
def deploy(
    branch: Annotated[str, suggest(branches), doc("branch to ship")] = "main",
    region: Annotated[Literal["eu", "us", "ap"], doc("region")] = "eu",
):
    "Ship a branch to a region."


@task
def build(release: bool = False, jobs: int = 4):
    """Compile and bundle.

    Args:
        release: optimise and strip symbols
        jobs: parallel compile jobs
    """


@task
def test(watch: bool = False):
    """Run the test suite.

    Args:
        watch: re-run on every file change
    """
# --8<-- [end:part-1]

# --8<-- [start:part-2]
from livery.footman import task, group


@task
def lint(fix: bool = False):
    "Run ruff over the project."
    ...


docs = group("docs", help="Documentation")


@docs.task(infinite=True)
def serve(port: int = 8000):
    "Serve the docs locally."
    ...
# --8<-- [end:part-2]
