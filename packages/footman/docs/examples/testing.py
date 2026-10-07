# --8<-- [start:part-1]
from livery.footman.testing import Runner

TASKS = """
from livery.footman import task, run

@task
def format():
    "Format the tree."
    run("ruff format .")

@task
def lint(fix: bool = False):
    "Lint it."
    run("ruff check ." + (" --fix" if fix else ""))

@task
def test():
    "Run the suite."
    run("pytest -q")
"""


def test_the_check_pipeline(tmp_path):
    (tmp_path / "tasks.py").write_text(TASKS)
    result = Runner().invoke("--dry-run format lint --fix test", cwd=tmp_path)
    assert result.ok
    assert [t.task for t in result.results] == ["format", "lint", "test"]
    assert "ruff check . --fix" in result.stdout
# --8<-- [end:part-1]

# --8<-- [start:part-2]
def test_release_dry(fm_project):
    fm = fm_project("""
        from livery.footman import task, run

        @task
        def release(version: str, push: bool = False):
            "Tag and optionally push."
            run(f"git tag v{version}")
            if push:
                run("git push --tags")
    """)
    result = fm.invoke("--dry-run release 1.2.0 --push")
    assert result.ok


def test_release_records_the_tag(fm_record):
    from tasks import release

    release("1.2.0")
    assert fm_record[0].command == "git tag v1.2.0"
    assert len(fm_record) == 1  # --push not given: no push
# --8<-- [end:part-2]

# --8<-- [start:part-3]
import json


def test_check_pipeline_shape(fm):
    payload = json.loads(fm.invoke("--json check").stdout)
    tasks = [(t["task"], t["ok"]) for t in payload["items"] if "task" in t]
    commands = [s["command"] for s in payload["items"] if "command" in s]
    assert tasks == [("lint", True), ("test", True)]
    assert commands == ["ruff check .", "pytest -q"]
# --8<-- [end:part-3]

# --8<-- [start:part-4]
from livery.footman import App
from livery.footman.testing import Runner


def test_acme_teaches_with_its_own_name(tmp_path):
    acme = Runner(App(name="Acme", prog="acme", version="1.4.0"))
    result = acme.invoke("nope", cwd=tmp_path)
    assert result.stderr.startswith("acme:")
# --8<-- [end:part-4]
