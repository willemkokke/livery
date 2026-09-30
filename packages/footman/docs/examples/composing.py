# --8<-- [start:part-1]
from livery.footman import task


@task(hidden=True)
def ci_publish(): ...
# --8<-- [end:part-1]

# --8<-- [start:part-2]
from livery.footman import group

internal = group("internal", hidden=True)  # the whole subtree, one word


@internal.task
def sweep(): ...  # hidden, like its group


@internal.task(hidden=False)
def status(): ...  # listed again, deliberately
# --8<-- [end:part-2]

# --8<-- [start:part-3]
import sys
from pathlib import Path

if sys.platform == "darwin":

    @task
    def notarize(app: Path): ...
# --8<-- [end:part-3]

# --8<-- [start:part-4]
from livery.footman import task, requires_tool


@task
@requires_tool("docker")
def up(detach: bool = True):
    "Start the dev containers."
# --8<-- [end:part-4]

# --8<-- [start:part-5]
# devkit/tasks.py
from livery.footman import task, requires_dep


@task
@requires_dep("stripe", reason="pip install devkit[release]")
def publish(version: str):
    "Cut and publish a release."
    import stripe  # imported only when publish actually runs

    ...
# --8<-- [end:part-5]

# --8<-- [start:part-6]
# acme_mkdocs/__init__.py
from livery.footman import Group, requires_tool

tasks = Group("mkdocs", help="MkDocs site tasks")


@tasks.task
def build(strict: bool = True): ...


@tasks.task
@requires_tool("mike")
def deploy(version: str): ...
# --8<-- [end:part-6]
