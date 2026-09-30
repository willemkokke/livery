# --8<-- [start:part-1]
from livery.footman import task, run


@task
def test(coverage: bool = False):
    """Run the test suite."""
    run("pytest --cov" if coverage else "pytest")
    # The typed tool handles live in toolroom (import toolroom as
    # tools), where flags become checked keyword arguments with
    # completion:
    #
    #     tools.pytest(cov=coverage)
    #
    # This page sticks to plain run() commands everyone already knows.
# --8<-- [end:part-1]

# --8<-- [start:part-2]
import shutil

from livery.footman import step


def write_fixtures(): ...  # stand-ins for your own helpers
def build_docs(): ...


@step  # 1. a function that IS a step
def clean():
    shutil.rmtree("build", ignore_errors=True)


with step("prepare fixtures"):  # 2. record a block of your own code
    write_fixtures()

docs = step(build_docs, title="docs")  # 3. wrap an existing function
# --8<-- [end:part-2]

# --8<-- [start:part-3]
from pathlib import Path


def to_webp(image: Path): ...  # your converter


@step
def convert(images: list[Path]):
    view = yield  # the step's own record, mid-work
    for done, image in enumerate(images, start=1):
        view.title = f"converting {done}/{len(images)}"
        to_webp(image)
        yield  # a checkpoint, once per image
# --8<-- [end:part-3]

# --8<-- [start:part-4]
from livery.footman import fail


@test.post_task
def budget(result):
    if result.duration > 60.0:
        fail(f"too slow: {result.duration:.0f}s against the 60s budget")
# --8<-- [end:part-4]

# --8<-- [start:part-5]
from livery import footman

db = footman.lane("database", reason="serialises the shared dev DB")


@task(lanes=(db,))
def migrate(): ...
# --8<-- [end:part-5]
