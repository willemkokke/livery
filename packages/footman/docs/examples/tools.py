# --8<-- [start:part-1]
from livery.footman.api import task, run
from livery.toolroom.tools.api import pytest, ruff


@task
def check():
    ruff("check", "src", fix=False)  # subprocess (ruff is a binary)
    pytest("-x")  # in-process via pytest.main
    run("mkdocs build --strict")  # any command at all
# --8<-- [end:part-1]

# --8<-- [start:part-2]
from pathlib import Path
from livery.footman.api import fetch, task


@task
def vendor():
    "Fetch the pinned toolchain."
    fetch(
        "https://example.com/protoc-27.tar.gz",
        sha256="9f86d081884c…",
        into=Path("vendor/protoc"),
    )
# --8<-- [end:part-2]

# --8<-- [start:part-3]
from livery.footman.api import passthrough, task
from livery.toolroom.tools.api import pytest


@task
def test():
    pytest(*passthrough())  # fm test -- -k mytest -x
# --8<-- [end:part-3]

# --8<-- [start:part-4]
from livery.footman.api import Context, task, run


@task
def publish(ctx: Context):
    if ctx.dry_run:  # fm --dry-run publish
        print("would upload the built artifacts")
        return
    run("./upload dist/*")
# --8<-- [end:part-4]
