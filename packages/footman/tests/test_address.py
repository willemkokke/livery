"""Addresses: every record's tree-derived, deterministic name."""

from __future__ import annotations

import json
import textwrap

from livery.footman import Context, parallel, run, step, use_context
from livery.footman.params import Secret
from livery.footman.testing import Runner


def test_an_address_is_minted_from_the_shown_command():
    """A name is printed wherever the record travels — `--json`, a profile
    track — so it is minted from the shown line. A `Secret` in the argv would
    otherwise ride into the tree as a node name, and the record still holds
    the real command for anyone reading rather than printing."""
    ctx = Context()
    ctx.address = "login"
    with use_context(ctx):
        run(["git", Secret("hunter2")], nofail=True)
    assert ctx.steps[0].address == "login/git"
    assert ctx.steps[0].command == "git hunter2"


def test_same_labelled_steps_take_ordinals_in_written_order():
    ctx = Context()
    ctx.address = "build"
    with use_context(ctx):
        run("git --version", nofail=True)
        run("git --version", nofail=True)
    assert [s.address for s in ctx.steps] == ["build/git", "build/git#2"]


def test_step_items_and_blocks_join_the_same_tree():
    @step
    def clean(): ...

    ctx = Context()
    ctx.address = "release"
    with use_context(ctx):
        clean()()
        with step("prepare"):
            pass
    assert [s.address for s in ctx.steps] == ["release/clean", "release/prepare"]


def test_rows_and_body_calls_carry_the_request_path(tmp_path):
    (tmp_path / "tasks.py").write_text(
        textwrap.dedent(
            """
            from livery.footman import task

            @task
            def child() -> int:
                return 1

            @task
            def parent():
                child()
            """
        )
    )
    result = Runner().invoke("--json parent", cwd=tmp_path)
    assert result.ok, result.stderr
    rows = [i for i in json.loads(result.stdout)["items"] if "task" in i]
    addresses = {r["task"]: r["address"] for r in rows}
    assert addresses["parent"] == "parent"
    assert addresses["child"] == "parent/child"


def test_split_nodes_are_distinct_by_ordinal(tmp_path):
    (tmp_path / "tasks.py").write_text(
        textwrap.dedent(
            """
            from livery.footman import Forward, task

            @task
            def shared(fix: bool = False): ...

            @task(pre=[shared])
            def a(fix: Forward[bool] = True): ...

            @task(pre=[shared])
            def b(fix: Forward[bool] = False): ...
            """
        )
    )
    result = Runner().invoke("--json a b", cwd=tmp_path)
    assert result.ok, result.stderr
    rows = [i for i in json.loads(result.stdout)["items"] if "task" in i]
    shared_addresses = sorted(r["address"] for r in rows if r["task"] == "shared")
    assert shared_addresses == ["shared", "shared#2"]


def test_parallel_children_branch_the_parents_path():
    seen: dict[str, str] = {}

    def probe():
        from livery.footman.context import current

        seen[current().task] = current().address

    ctx = Context()
    ctx.address = "go"
    with use_context(ctx):
        parallel(step(probe, title="x")(), step(probe, title="y")())
    assert seen == {"x": "go/x", "y": "go/y"}


def test_command_leaves_carry_the_verb_and_never_the_flags():
    ctx = Context()
    ctx.address = "release"
    with use_context(ctx):
        run("git fetch", nofail=True)
        run("git push", nofail=True)
        run("git --version", nofail=True)
    assert [s.address for s in ctx.steps] == [
        "release/git-fetch",
        "release/git-push",
        "release/git",  # a flag is not a verb
    ]


def test_leaves_are_parse_safe():
    from livery.footman.context import _leaf

    assert _leaf("./ship") == "ship"  # the separator cannot fake a level
    assert _leaf("prepared 3 fixtures") == "prepared-3-fixtures"
    assert _leaf("make target#2") == "make-target-2"  # ordinals stay ours
    assert _leaf("///") == "step"  # never empty


def test_a_row_that_never_ran_still_has_its_address(tmp_path):
    """Addresses are assigned when the plan is final, not when a task runs.
    A skipped row used to arrive with `address: ""`, which broke the one
    lookup the envelope promises: `blocked_by` and `after` name addresses,
    and an empty one matches nothing."""
    (tmp_path / "tasks.py").write_text(
        textwrap.dedent(
            """
            from livery.footman import task

            @task
            def build():
                raise SystemExit(3)

            @task(pre=[build])
            def publish(): ...
            """
        )
    )
    result = Runner().invoke("--json publish", cwd=tmp_path)
    assert not result.ok
    rows = {i["task"]: i for i in json.loads(result.stdout)["items"] if "task" in i}
    assert rows["publish"]["state"] == "skipped"
    assert rows["publish"]["address"] == "publish"
    assert rows["publish"]["blocked_by"] == rows["build"]["address"]


def test_the_envelope_carries_task_output_and_not_footman_chrome(tmp_path):
    """A failing step used to print its receipt line into the task's capture
    buffer, so `output` arrived as `"FAIL build  echo hi  (0.0s)\n"` — a
    human's line, in a machine's field, duplicating the step row right below
    it. A body's own prints are what the buffer is for, and they stay."""
    (tmp_path / "tasks.py").write_text(
        textwrap.dedent(
            """
            from livery.footman import run, task

            @task
            def build():
                print("a body's own words")
                run(["python", "-c", "raise SystemExit(2)"])
            """
        )
    )
    result = Runner().invoke("--json build", cwd=tmp_path)
    assert not result.ok
    items = json.loads(result.stdout)["items"]
    (row,) = [i for i in items if "task" in i]
    assert row["output"] == "a body's own words\n"
    assert "FAIL" not in row["output"]
    # The receipt itself is not lost — it is the step's own row, in fields.
    (step_row,) = [i for i in items if "command" in i]
    assert step_row["code"] == 2
    assert step_row["address"].startswith("build/")


def test_a_lifted_steps_receipt_stays_out_of_the_envelope_too(tmp_path):
    """The twin of the case above, for `@step`: a lifted item took the same
    receipt line and a replay of its own streams into the task's buffer,
    because only `run()`'s copy of the guard knew about `--json`. Both
    already have a step row carrying exactly those bytes in fields."""
    (tmp_path / "tasks.py").write_text(
        textwrap.dedent(
            """
            from livery.footman import step, task

            @step
            def bundle():
                print("a step's own words")
                raise RuntimeError("boom")

            @task
            def build():
                print("a body's own words")
                bundle()()
            """
        )
    )
    result = Runner().invoke("--json build", cwd=tmp_path)
    assert not result.ok
    items = json.loads(result.stdout)["items"]
    (row,) = [i for i in items if "task" in i]
    assert row["output"] == "a body's own words\n"
    assert "FAIL" not in row["output"]
    # The step's own prints belong to the step's row, not the task's buffer.
    (step_row,) = [i for i in items if "command" in i]
    assert step_row["command"] == "bundle"
    assert step_row["code"] == 1
    assert step_row["stdout"] == "a step's own words\n"
    assert "RuntimeError: boom" in step_row["stderr"]
