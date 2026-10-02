# --8<-- [start:part-1]
# footman
from livery.footman.api import run, task


@task
def lint(fix: bool = False):
    run("ruff check ." + (" --fix" if fix else ""))
# --8<-- [end:part-1]
