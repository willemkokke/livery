# --8<-- [start:part-1]
# repo/tasks.py
import livery.footman.api as footman
from livery.footman.api import task


@task
def audit(): ...


@footman.pre_tasks
def gate_deploys(inv):
    for t in inv.tasks:
        if t.name.startswith("deploy") and "audit" in inv.tasks:
            t.add_pre(inv.tasks["audit"])
# --8<-- [end:part-1]

# --8<-- [start:part-2]
@footman.pre_tasks
def gate_infra(inv):
    for t in inv.tasks:
        if (t.defining_dir or "").endswith("infra"):
            t.add_pre(inv.tasks["audit"])
# --8<-- [end:part-2]

# --8<-- [start:part-3]
from livery.footman.api import fail, task


@task
def build(target: str = "web"): ...


@build.pre_task  # setup that belongs to build
def warm(): ...


@build.pre_record  # build's reviewer: the draft, before sealing
def review(view):
    view.title = f"build: {view.returned or 'ok'}"


@build.post_task  # watch build's sealed record; veto via fail()
def budget(result):
    if result.duration > 60.0:
        fail(f"too slow: {result.duration:.0f}s")
# --8<-- [end:part-3]

# --8<-- [start:part-4]
from pathlib import Path
from livery.footman.api import GlobalOption

ENV_FILE = GlobalOption("env-file", Path, help="load this .env file first")
AUDIT = GlobalOption("audit", help="report, change nothing")  # bool → a flag
# --8<-- [end:part-4]

# --8<-- [start:part-5]
from livery.footman.compose import plugin

plugin("footman.env_files")
# --8<-- [end:part-5]
