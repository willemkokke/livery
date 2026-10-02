# --8<-- [start:part-1]
# ~/.config/footman/tasks.py
from livery.footman.api import run, task


@task
def scratch():
    """Spin up a throwaway venv here."""
    run(["uv", "venv", ".scratch"])
# --8<-- [end:part-1]

# --8<-- [start:part-2]
@task(expose="project_only")
def sync_upstream():
    """Rebase onto upstream/main."""
    run(["git", "fetch", "upstream"])
# --8<-- [end:part-2]
