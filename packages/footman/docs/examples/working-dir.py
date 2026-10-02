# --8<-- [start:part-1]
from livery.footman.api import run, task


@task(cwd="root", rel="services/api")
def deploy():
    run("docker compose up -d")  # spawned from <root>/services/api
# --8<-- [end:part-1]

# --8<-- [start:part-2]
import livery.footman.api as footman


@task
def bundle():
    out = footman.cwd() / "dist"  # the task's own directory, not the
    out.mkdir(exist_ok=True)  # process's; safe under parallelism
# --8<-- [end:part-2]

# --8<-- [start:part-3]
import livery.toolroom.tools.api as tools

run("npm run build", rel="web")  # this one call, in <cwd>/web
tools.npm.opts(rel="web").run("build")  # same, through the handle
web_npm = tools.npm.opts(rel="web")  # or bind it once
# --8<-- [end:part-3]

# --8<-- [start:part-4]
@task(serial=True)
def legacy_build():
    with footman.chdir(rel="vendor"):  # a real chdir, legal here
        run("make")
# --8<-- [end:part-4]
