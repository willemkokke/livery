# --8<-- [start:part-1]
from livery.footman import task


@task
def ship(target, port=8000, ratio=1.5, name="web", verbose=False):
    "Ship it."
    print(target, port + 1, ratio, name, verbose)
# --8<-- [end:part-1]

# --8<-- [start:part-2]
@task
def stamp(
    out=None, paths=(), tags=["docs"]
): ...  # out, paths and tags all arrive as `str`
# --8<-- [end:part-2]

# --8<-- [start:part-3]
from livery.footman import task


@task
def scale(factor: int | float): ...
# --8<-- [end:part-3]

# --8<-- [start:part-4]
from livery.footman import Many


@task
def build(targets: Many[str]):
    ...  # fm build web     -> ["web"]
    # fm build web api -> ["web", "api"]
# --8<-- [end:part-4]

# --8<-- [start:part-5]
@task
def label(tags: set[str] = frozenset()): ...  # fm label --tags=a,b,a -> {"a", "b"}
# --8<-- [end:part-5]

# --8<-- [start:part-6]
@task
def release(tags: list[str] = []): ...  # fm release --tags=a,b,c  -> ["a", "b", "c"]
# --8<-- [end:part-6]

# --8<-- [start:part-7]
from livery.footman import NoSplit


@task
def notify(lines: NoSplit[list[str]] = []): ...


# fm notify --lines="Smith, John" --lines="Doe, Jane"  -> two names, commas kept
# --8<-- [end:part-7]

# --8<-- [start:part-8]
@task
def env(vars: dict[str, int | str]): ...  # fm env --vars=port=8080 --vars=name=web
# --8<-- [end:part-8]

# --8<-- [start:part-9]
from typing import NamedTuple
from livery.footman import task


class Size(NamedTuple):
    width: int
    height: int


@task
def render(size: Size = Size(1920, 1080)): ...  # fm render --size=800,600
# --8<-- [end:part-9]

# --8<-- [start:part-10]
class Spot(NamedTuple):
    x: float
    y: float


@task
def route(points: list[Spot] = ()): ...


# fm route --points=1,2 --points=3,4   -> [Spot(1.0, 2.0), Spot(3.0, 4.0)]
# fm route --points=1,2,3,4            -> the same two points
# --8<-- [end:part-10]

# --8<-- [start:part-11]
from dataclasses import dataclass


@dataclass
class Window:
    title: str
    width: int = 800


@task
def open_(window: Window = Window("footman")): ...  # fm open --window=Docs,1024
# --8<-- [end:part-11]

# --8<-- [start:part-12]
from uuid import UUID
from decimal import Decimal
from datetime import datetime


@task
def record(id: UUID, amount: Decimal, when: datetime): ...
# --8<-- [end:part-12]

# --8<-- [start:part-13]
from pathlib import Path
from typing import Annotated
from livery.footman import task, between, check, doc, env, isfile


def semver(value: str) -> None: ...  # your validator: raise ValueError to refuse


@task
def deploy(
    config: Annotated[Path, isfile],  # must exist, be a file
    jobs: Annotated[int, between(1, 32)] = 4,  # inclusive bounds
    target: Annotated[
        str, env("DEPLOY_ENV")
    ] = "staging",  # CLI > $DEPLOY_ENV > default
    version: Annotated[str, check(semver)] = "0.0.0",  # your own validator
    force: Annotated[bool, doc("skip the health check")] = False,  # help text
): ...
# --8<-- [end:part-13]

# --8<-- [start:part-14]
from typing import Annotated
from livery.footman import task
from livery.footman._params import hidden


@task
def publish(target: str, legacy: Annotated[str, hidden] = ""):
    """Ship it."""
# --8<-- [end:part-14]

# --8<-- [start:part-15]
from typing import Annotated
from livery.footman import task, suggest


def shares() -> list[str]:
    return ["main", "scratch", "archive"]


@task
def mount(share: Annotated[str, suggest(shares)]): ...
# --8<-- [end:part-15]

# --8<-- [start:part-16]
def branches() -> list[str]:
    import subprocess  # here, not at module top

    out = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        capture_output=True,
        text=True,
    )
    return out.stdout.split()
# --8<-- [end:part-16]
