# --8<-- [start:part-1]
from typing import Annotated, Literal
from livery.footman import ask, task


@task
def release(version: Annotated[str, ask()]): ...


@task
def deploy(env: Annotated[Literal["staging", "prod"], ask()]): ...
# --8<-- [end:part-1]

# --8<-- [start:part-2]
from livery.footman import suggest


def stale_branches() -> list[str]:
    return ["old/spike", "old/wip"]


@task
def prune(branch: Annotated[str, ask(), suggest(stale_branches)]): ...
# --8<-- [end:part-2]

# --8<-- [start:part-3]
from typing import Annotated
from livery.footman import Secret, Stdout, ask, run, task


@task
def login(token: Annotated[str, ask(secret=True)]): ...


@task
def publish(token: Secret): ...  # a flag or env() value, still redacted
# --8<-- [end:part-3]

# --8<-- [start:part-4]
@task
def upload(token: Secret):
    run(["twine", "upload", "--password", token])  # shows `… --password ***`
# --8<-- [end:part-4]

# --8<-- [start:part-5]
@task
def env_export(token: Secret) -> Stdout[str]:
    return f"export TOKEN={token}"  # emits the real value; no switch needed
# --8<-- [end:part-5]

# --8<-- [start:part-6]
@task
def creds(token: Secret) -> Stdout[dict]:
    return {"token": token.reveal()}  # deliberate; a plain str from here on
# --8<-- [end:part-6]

# --8<-- [start:part-7]
from livery.footman import prompt, select, task


@task(interactive=True)
def scaffold():
    name = prompt("project name? ")
    kind = select("what kind?", ["library", "app", "plugin"])
    ...
# --8<-- [end:part-7]

# --8<-- [start:part-8]
from livery.footman import attended, colored, tty


@task(interactive=True)
def setup(licence: str = "MIT"):
    if attended():
        licence = prompt("licence? ", default=licence)  # someone can answer
    print(f"licence: {licence}")  # CI, a pipe, --no-input: the quiet path
# --8<-- [end:part-8]
