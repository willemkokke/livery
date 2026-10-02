# --8<-- [start:part-1]
from pathlib import Path
from livery.footman.api import task


@task
def coverage() -> dict:
    "Measure coverage."
    ...
    return {"percent": 94.2, "failed": [], "report": Path("htmlcov/index.html")}
# --8<-- [end:part-1]

# --8<-- [start:part-2]
from dataclasses import dataclass
from livery.footman.api import task


@dataclass
class Affected:
    tasks: list[str]
    reason: str
    since: str


@task
def affected() -> Affected:
    """The tasks a change reaches.

    Returns:
        Which tasks the change reaches, and why.
    """
    ...
# --8<-- [end:part-2]

# --8<-- [start:part-3]
from livery.footman.api import Stdout, task


@task
def status() -> Stdout[dict]:
    "Where the repo stands."
    return {"branch": "main", "dirty": False}
# --8<-- [end:part-3]
