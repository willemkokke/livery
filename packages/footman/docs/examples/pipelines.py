# --8<-- [start:part-1]
from typing import Annotated
from livery.footman.api import Stdout, stdin, task


@task
def summarise(diff: Annotated[str, stdin] = "") -> Stdout[dict]:
    "Reduce a diff to the numbers."
    added = sum(1 for line in diff.splitlines() if line.startswith("+"))
    return {"added": added}
# --8<-- [end:part-1]

# --8<-- [start:part-2]
from dataclasses import dataclass, field


@dataclass
class ToolInput:
    file_path: str = ""


@dataclass
class Event:
    tool_input: ToolInput = field(default_factory=ToolInput)
    stop_hook_active: bool = False


@task(hidden=True)
def on_edit(event: Annotated[Event, stdin]) -> None:
    if event.tool_input.file_path.endswith(".py"):
        ...
# --8<-- [end:part-2]

# --8<-- [start:part-3]
from livery.footman.api import run, task


@task
def install(requirement: str) -> None:
    run("uv pip install -r -", input=requirement)
# --8<-- [end:part-3]
