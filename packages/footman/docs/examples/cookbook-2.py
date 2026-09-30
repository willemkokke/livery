# --8<-- [start:part-1]
from pathlib import Path
from typing import Annotated
from livery.footman import task, run
from livery.footman.params import between, check, env, isfile


def semver(value: str) -> None:
    import re

    if not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ValueError(f"expected MAJOR.MINOR.PATCH, got {value!r}")


@task
def deploy(
    config: Annotated[Path, isfile],
    version: Annotated[str, check(semver)],
    workers: Annotated[int, between(1, 32)] = 4,
    target: Annotated[str, env("DEPLOY_ENV")] = "staging",
):
    "Roll out."
    run(f"./rollout.sh {target} {version} --config {config} -j {workers}")
# --8<-- [end:part-1]

# --8<-- [start:part-2]
from typing import Annotated
from livery.footman import task
from livery.footman.params import check


def current_version(name: str) -> str: ...  # your lookup (pyproject, git…)
def newer(version: str, current: str) -> bool: ...  # your comparison; none bundled


def newer_than_current(version, params):
    current = current_version(params["name"])
    if not newer(version, current):
        raise ValueError(f"{version} is not newer than {current}")


@task
def release(name: str, version: Annotated[str, check(newer_than_current)]):
    "Cut a release, but only forward."
    ...
# --8<-- [end:part-2]

# --8<-- [start:part-3]
from typing import Annotated
from livery.footman import task, run
from livery.footman.params import suggest
from livery.toolroom.tools import docker


def branches() -> list[str]:
    import subprocess  # inside the body, so importing tasks.py stays cheap

    out = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        capture_output=True,
        text=True,
    )
    return out.stdout.split()


@task
def review(branch: Annotated[str, suggest(branches)]):
    "Check out and gate a branch."
    run(f"git switch {branch}")
    run("fm check")
# --8<-- [end:part-3]

# --8<-- [start:part-4]
@task
def image(tag: str, build_args: dict[str, str] | None = None):
    "Build the container image."
    docker.build(
        ".", tag=tag, build_arg=[f"{k}={v}" for k, v in (build_args or {}).items()]
    )
# --8<-- [end:part-4]

# --8<-- [start:part-5]
from pathlib import Path


@task
def bundle(*entries: str, out: Path):
    "Bundle entry points into one artifact."
    run(f"./bundle.sh {' '.join(entries)} -o {out}")
# --8<-- [end:part-5]
