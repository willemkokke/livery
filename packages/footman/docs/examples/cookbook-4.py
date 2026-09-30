# --8<-- [start:part-1]
from livery.footman import parallel, run, task

TARGETS = ("linux-x86_64", "linux-arm64", "darwin-arm64")


@task
def build(target: str):
    "Compile one target."
    run(f"cargo zigbuild --target {target}")


@task
def matrix():
    "Compile every target."
    with parallel(keep_going=True) as p:
        for t in TARGETS:
            build(t)
    if any(p):  # the block is its list of codes
        raise SystemExit(1)
# --8<-- [end:part-1]

# --8<-- [start:part-2]
@task(infinite=True)
def serve(port: int = 8000):
    "Run the dev server until Ctrl-C."
    run(f"uvicorn app:api --reload --port {port}")
# --8<-- [end:part-2]

# --8<-- [start:part-3]
from livery.toolroom.tools import docker, mkdocs, terraform


@task
def up(detach: bool = True):
    "Start the stack."
    docker.compose.up(detach=detach)


@task
def plan(out: str = "tf.plan"):
    "Terraform plan, saved."
    terraform.plan(out=out, input_=False)


@task
def site():
    "Build the docs."
    mkdocs.build(strict=True)  # in-process: no interpreter spawn
# --8<-- [end:part-3]

# --8<-- [start:part-4]
from livery.footman import task, run


@task(cwd="root", rel="services/api")
def deploy():
    "Deploy the api service, wherever fm was invoked from."
    run("docker compose up -d")


@task
def bundle():
    "Build the web bundle from a subdirectory of this task's own dir."
    run("npm run build", rel="web")  # <task cwd>/web, this call only
# --8<-- [end:part-4]

# --8<-- [start:part-5]
from livery.footman import project_root, task, run


@task
def audit(path: str = ".", affected: bool = False):
    "Audit a path as given, or with --affected the whole tree."
    target = project_root() if affected else path
    run(f"pytest {target}", shell=False)
# --8<-- [end:part-5]

# --8<-- [start:part-6]
from livery import footman
from livery.footman import task, run


@task(serial=True)
def legacy_build():
    "One serial task at a time; the parallel pool keeps running around it."
    with footman.chdir(rel="vendor"):  # a real chdir, legal here
        run("make")
# --8<-- [end:part-6]

# --8<-- [start:part-7]
@task(exclusive=True)
def bench():
    "Timings mean nothing with a build running next door."
    run("pytest tests/bench --benchmark-only")
# --8<-- [end:part-7]

# --8<-- [start:part-8]
@task
def publish(ctx):
    ctx.env["TWINE_NON_INTERACTIVE"] = "1"  # every child of this task
    run("twine upload dist/*", shell=True, env={"TWINE_VERBOSE": "1"})  # one call
# --8<-- [end:part-8]

# --8<-- [start:part-9]
from typing import Annotated
from livery.footman import ask, task, run


@task(confirm="Publish to PyPI?")
def release(version: Annotated[str, ask()]):
    "fm release → asks version up front, confirms, then runs unattended."
    run(f"uv version {version}", shell=False)
    run("uv build")
    run("uv publish")
# --8<-- [end:part-9]

# --8<-- [start:part-10]
# svc/api/tasks.py; the repo root also defines `check`
from livery.footman import inherited, run, task


@task
def check(fix: bool = False, contracts: bool = True):
    "The shared gate, plus this service's contracts."
    inherited()(fix=fix)  # the root's check, arguments forwarded
    if contracts:
        run("./verify-contracts.sh")
# --8<-- [end:part-10]

# --8<-- [start:part-11]
from pathlib import Path
from livery.footman import task, track, progress


def load_records() -> list: ...  # your own work, whatever shape it takes
def apply(record): ...
def build_index(path): ...


@task
def migrate():
    "Apply pending migrations."
    for record in track(load_records()):  # total from len()
        apply(record)


@task
def index(path: Path):
    "Rebuild the search index."
    for done, total in build_index(path):
        progress(done, total)  # the explicit form
# --8<-- [end:part-11]

# --8<-- [start:part-12]
from pathlib import Path
from livery.footman import fetch, parallel, step, task

TOOLCHAIN = {
    "protoc": ("https://example.com/protoc-27.tar.gz", "9f86d081884c…"),
    "buf": ("https://example.com/buf-1.34.tar.gz", "2c26b46b68ff…"),
}


@task
def vendor():
    "Fetch the pinned toolchain, in parallel."
    # step(fetch) lifts the helper into an owned item, so each download
    # gets its own receipt and its own worker.
    parallel(
        *(
            step(fetch)(url, sha256=digest, into=Path("vendor") / name)
            for name, (url, digest) in TOOLCHAIN.items()
        )
    )
# --8<-- [end:part-12]

# --8<-- [start:part-13]
@task
def coverage() -> dict:
    "Measure test coverage."
    run("pytest --cov=app --cov-report=json -q")
    import json

    percent = json.load(open("coverage.json"))["totals"]["percent_covered"]
    return {"percent": round(percent, 2)}
# --8<-- [end:part-13]

# --8<-- [start:part-14]
# acme_cli.py
from livery.footman import App

app = App(name="Acme", prog="acme", version="1.4.0")


def main() -> None:
    raise SystemExit(app.run())
# --8<-- [end:part-14]
