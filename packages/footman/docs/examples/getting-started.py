# --8<-- [start:part-1]
from livery.footman.api import task, group


@task
def lint(fix: bool = False):
    "Run ruff over the project."
    ...


@task
def test(marker: str = "", *pytest_args):
    "Run the test suite (extra pytest args after --)."
    ...


docs = group("docs", help="Documentation")


@docs.task(infinite=True)
def serve(port: int = 8000):
    "Serve the docs locally."
    ...
# --8<-- [end:part-1]
