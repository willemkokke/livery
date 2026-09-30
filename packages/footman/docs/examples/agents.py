# --8<-- [start:part-1]
from dataclasses import dataclass, field
from typing import Annotated
from livery.footman import RunFailed, fail, group, stdin, task

hooks = group("hooks", hidden=True, help="Agent lifecycle hooks")


@task
def format(): ...  # stand-ins for your own gate tasks


@task
def lint(): ...


@task
def check(): ...


@dataclass
class ToolInput:
    file_path: str = ""


@dataclass
class HookEvent:
    tool_input: ToolInput = field(default_factory=ToolInput)
    stop_hook_active: bool = False


@hooks.task
def post_edit(event: Annotated[HookEvent, stdin]) -> None:
    """Format and lint a Python file the agent just edited."""
    if not event.tool_input.file_path.endswith(".py"):
        return
    try:
        format()  # your own format/lint tasks, body-called
        lint()
    except RunFailed:
        fail("format/lint failed; fix it before continuing", code=2)


@hooks.task
def stop(event: Annotated[HookEvent, stdin]) -> None:
    """Refuse to let a session end on a red gate."""
    if event.stop_hook_active:
        return  # this stop already is the retry; never ping-pong
    try:
        check()
    except RunFailed:
        fail("the gate is red; fix it before stopping", code=2)
# --8<-- [end:part-1]
