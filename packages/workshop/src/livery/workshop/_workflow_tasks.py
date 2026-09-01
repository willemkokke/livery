"""The ``workflow`` group: the reserved-branch lifecycles' surface.

``workflow.abort`` stops a reserved workflow, policy layered over the
same teardown mechanism ``fm abandon`` uses. The hidden
``workflow.configure`` asserts repository settings from the workspace
contract, run at birth, as release aftercare, and after an abort.
The lifecycle drivers (``workflow.release``, ``workflow.update``)
arrive with their own modules; this one owns what every kind shares.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from footman import doc, fail, group, suggest

from livery.forge import RepoConfig, Repository
from livery.workshop._git_ops import GitOps
from livery.workshop._layers import workspace_root
from livery.workshop._workflow_state import (
    TEARDOWN_SAFE,
    WorkflowState,
    WorkflowStatus,
    workflow_states,
)

workflow = group("workflow", help="The reserved-branch lifecycles")


def _root() -> Path:
    root = workspace_root()
    if root is None:
        fail("no workspace: no livery.toml above the working directory")
    return root


def _resolved() -> tuple[Repository, GitOps]:
    from livery.workshop._forge_lane import this_repository

    root = _root()
    return this_repository(root), GitOps(root)


def render_workflows(states: tuple[WorkflowStatus, ...]) -> None:
    """Print one line per workflow: name, state, author, the way forward."""
    if not states:
        print("  no reserved workflows in flight")
        return
    for wf in states:
        author = f" ({wf.author})" if wf.author else ""
        extra = f" - {wf.detail}" if wf.detail else ""
        tagged = (
            f" [tagged: {', '.join(wf.tagged)}]"
            if wf.tagged and wf.state is not WorkflowState.SUCCEEDED
            else ""
        )
        print(f"  {wf.name}: {wf.state.value}{author}{extra}{tagged}")


def abort_policy(
    repo: Repository,
    git: GitOps,
    states: tuple[WorkflowStatus, ...],
    name: str,
    *,
    force: bool,
    base: str = "main",
    interactive: bool = False,
) -> None:
    """The abort's state gates, then the shared teardown.

    Bare with one workflow targets it; several ask interactively or
    refuse by name, silence never picks a teardown target. UNKNOWN
    refuses without force, never tear down on a blip; a live state
    refuses without force; a bare ``--force`` with several in flight
    refuses, force one by name.
    """
    if not states:
        print("  no reserved workflows to abort")
        return
    target: WorkflowStatus | None = None
    if name:
        target = next((wf for wf in states if wf.name == name), None)
        if target is None:
            known = ", ".join(wf.name for wf in states)
            fail(f"no workflow named {name!r}; in flight: {known}")
    elif len(states) == 1:
        target = states[0]
    elif interactive:
        print("  several workflows are in flight:")
        for index, wf in enumerate(states, 1):
            author = wf.author or "unknown author"
            print(f"    {index}. {wf.name} ({author}, {wf.state.value})")
        answer = input("  abort which? (number, or empty to stop) ").strip()
        if not answer:
            raise SystemExit("  nothing aborted")
        try:
            target = states[int(answer) - 1]
        except (ValueError, IndexError):
            fail(f"{answer!r} names none of the listed workflows")
    else:
        listed = "\n".join(
            f"    fm workflow.abort {wf.name}"
            + (f"  ({wf.author}, {wf.state.value})" if wf.author else "")
            for wf in states
        )
        fail(
            "several workflows are in flight, and silence must not pick a"
            f" teardown target. Abort one by name:\n{listed}"
        )
    assert target is not None
    if force and not name and len(states) > 1:
        fail(
            "--force with several workflows in flight needs a name: a bare"
            " force must not sweep what someone else is running"
        )
    if target.state is WorkflowState.UNKNOWN and not force:
        fail(
            f"{target.name}'s state could not be read, and a blip must never"
            " look like permission to tear down. Retry when the forge"
            f" answers, or `fm workflow.abort {target.name} --force` if you"
            " know it is dead."
        )
    if target.state not in TEARDOWN_SAFE and not force:
        who = target.author or "someone"
        fail(
            f"{target.name} is {target.state.value} ({who}); aborting it"
            " loses in-flight work. Wait for it, coordinate with its"
            f" author, or `fm workflow.abort {target.name} --force` to tear"
            " it down deliberately."
        )
    from livery.workshop._submit import teardown_branch

    teardown_branch(repo, git, target.branch, base)
    _reconcile_configuration()


def _active_names() -> tuple[str, ...]:
    """The in-flight workflow names, for completion; empty off a workspace."""
    try:
        repo, git = _resolved()
        return tuple(wf.name for wf in workflow_states(repo, git))
    except Exception:
        return ()


@workflow.task(name="abort")
def workflow_abort(
    name: Annotated[
        str,
        suggest(_active_names, strict=False),
        doc("the workflow to stop; bare targets the only one in flight"),
    ] = "",
    force: Annotated[bool, doc("tear down a live or unreadable workflow")] = False,
) -> None:
    """Stop a reserved workflow: disarm, close its PR, tear its branch down.

    Bare targets the only workflow in flight; with several, an
    interactive run lists them and asks, a non-interactive run
    refuses naming each. A live or unreadable workflow refuses
    without ``--force``, and a bare ``--force`` never sweeps several.
    Ends by re-asserting the repository configuration, so an aborted
    workflow that had moved settings forward cannot leave protection
    demanding contexts that never report again.
    """
    import sys

    repo, git = _resolved()
    states = workflow_states(repo, git)
    abort_policy(
        repo,
        git,
        states,
        name,
        force=force,
        interactive=sys.stdin.isatty(),
    )


def contract_config(root: Path) -> RepoConfig:
    """The repository settings the workspace contract states."""
    import tomllib

    contract = tomllib.loads((root / "livery.toml").read_text("utf-8"))
    ci = contract.get("ci") or {}
    context = str(ci.get("required_context") or "gate")
    return RepoConfig(
        squash_only=True,
        delete_branch_on_merge=True,
        allow_auto_merge=True,
        required_contexts=(context,),
    )


@workflow.task(name="configure", hidden=True)
def workflow_configure() -> None:
    """Assert the contract's repository settings; idempotent drift repair.

    Run at repository birth, as release aftercare, and after an
    abort. A token the forge will not let administer refuses with the
    grant it needed.
    """
    root = _root()
    from livery.workshop._forge_lane import this_repository

    this_repository(root).configure(contract_config(root))
    print("  repository configuration asserted from the contract")


def _reconcile_configuration() -> None:
    """Re-assert configuration after an abort, in a fresh process.

    A fresh process because the abort may run inside a workflow whose
    update rewrote this very toolchain. Best effort: a refusal is
    reported, never fatal, the teardown already completed.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "footman", "workflow.configure"],
        capture_output=True,
        text=True,
        check=False,
        cwd=_root(),
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        print(
            "  note: the configuration reconcile was refused; an"
            f" administrator runs `fm workflow.configure` to repair.\n"
            f"  {detail}"
        )
