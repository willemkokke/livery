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

import footman
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
        fail("no workspace: no workshop.toml above the working directory")
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
        options: list[tuple[str, WorkflowStatus | None]] = [
            (f"{wf.name} ({wf.author or 'unknown author'}, {wf.state.value})", wf)
            for wf in states
        ]
        options.append(("nothing - leave them all running", None))
        picked = footman.select("abort which?", options)
        # The select's typing admits a bare-label pick; every option
        # here carries a value, so a str can only mean no target.
        target = picked if not isinstance(picked, str) else None
        if target is None:
            raise SystemExit("  nothing aborted")
    else:
        listed = "\n".join(
            f"    {footman.prog()} workflow.abort {wf.name}"
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
            f" answers, or `{footman.prog()} workflow.abort {target.name}"
            " --force` if you"
            " know it is dead."
        )
    if target.state not in TEARDOWN_SAFE and not force:
        who = target.author or "someone"
        fail(
            f"{target.name} is {target.state.value} ({who}); aborting it"
            " loses in-flight work. Wait for it, coordinate with its"
            f" author, or `{footman.prog()} workflow.abort {target.name}"
            " --force` to tear"
            " it down deliberately."
        )
    from livery.workshop._submit import teardown_branch

    head_sha = ""
    try:
        head_sha = git.any_head(target.branch)
    except Exception:
        head_sha = ""
    teardown_branch(repo, git, target.branch, base)
    _reconcile_configuration(git, head_sha)


def _active_names() -> tuple[str, ...]:
    """The in-flight workflow names, for completion; empty off a workspace."""
    try:
        repo, git = _resolved()
        return tuple(wf.name for wf in workflow_states(repo, git))
    except Exception:
        return ()


@workflow.task(name="abort", interactive=True)
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
    """The repository settings the workspace contract states.

    The approvals half derives from the owner declarations
    (livery.workshop._governance): the highest declared count, with
    codeowner review required whenever anyone is declared. A forge
    without the min_approvals capability gets the config without
    that half rather than a refusal: governance degrades to the
    codeowners file alone, which is what the forge can honour.
    """
    import tomllib

    from livery.workshop._governance import governance_config

    contract = tomllib.loads((root / "workshop.toml").read_text("utf-8"))
    ci = contract.get("ci") or {}
    context = str(ci.get("required_context") or "gate")
    approvals = governance_config(root)
    return RepoConfig(
        squash_only=True,
        delete_branch_on_merge=True,
        allow_auto_merge=True,
        required_contexts=(context,),
        min_approvals=approvals.min_approvals,
        require_codeowner_review=approvals.require_codeowner_review,
    )


@workflow.task(name="configure", hidden=True)
def workflow_configure() -> None:
    """Assert the contract's repository settings; idempotent drift repair.

    Run at repository birth, as release aftercare, after an abort,
    and by the post-merge governance job. Resolves the admin ladder
    (``FORGE_ADMIN_TOKEN``, host-qualified first, the everyday token
    as the fallback); a refused write teaches the grant and the
    variable. Declared owners the forge does not know refuse before
    anything is applied: a review chain pointing at nobody is worse
    than unapplied settings.
    """
    assert_configuration(_root())


def assert_configuration(root: Path) -> None:
    """The configure flow, root-taking so birth can call it too."""
    import tomllib

    from livery.forge import ForgeError, Unsupported
    from livery.workshop._forge_lane import admin_forge, admin_repository
    from livery.workshop._governance import unknown_owners

    repo, admin_var = admin_repository(root)
    forge, _ = admin_forge(root)
    contract = tomllib.loads((root / "workshop.toml").read_text("utf-8"))
    owner = str((contract.get("forge") or {}).get("owner", ""))
    missing = unknown_owners(root, forge, owner)
    if missing:
        listed = "\n".join(f"    {entry}" for entry in missing)
        fail(
            "the owner declarations name people or teams this forge does"
            f" not know:\n{listed}\n  Fix the declarations (or the org"
            " membership) and re-run."
        )
    from dataclasses import replace

    config = contract_config(root)
    if config.min_approvals is not None and not forge.supports("min_approvals"):
        # The forge cannot enforce the count (GitLab's paid tiers);
        # the codeowners file still documents it, so the rest of the
        # contract applies rather than nothing.
        print(
            "  note: this instance cannot enforce approval counts"
            " (capability: min_approvals); the codeowners file still"
            " names the reviewers"
        )
        config = replace(config, min_approvals=None, require_codeowner_review=None)
    if config.required_contexts is not None and not forge.supports("required_contexts"):
        # GitLab: protection cannot name check contexts; the
        # pipeline's own rules gate merges there instead.
        print(
            "  note: this forge cannot name required check contexts"
            " (capability: required_contexts); the pipeline's own rules"
            " gate merges instead"
        )
        config = replace(config, required_contexts=None)
    try:
        repo.configure(config)
    except Unsupported as error:
        # A decline the probes above did not predict: name it
        # verbatim, never half-teach a token that would not help.
        fail(
            f"the forge declined part of the configuration:\n{error}\n"
            f"  `{footman.prog()} doctor` says what this forge grants."
        )
    except ForgeError as error:
        used = admin_var or "the everyday token"
        fail(
            f"the forge refused the configuration using {used}:\n{error}\n"
            "  An administrator's token applies it: set FORGE_ADMIN_TOKEN"
            " (host-qualified FORGE_ADMIN_TOKEN__<HOST> where one machine"
            f" serves several forges) and re-run"
            f" `{footman.prog()} workflow.configure`."
        )
    print("  repository configuration asserted from the contract")


def _spawn_configure() -> footman.Result:
    """Run the configure verb in a fresh process; the reconcile's seam.

    Fresh, because the abort may sit inside a workflow whose update
    rewrote this very toolchain.
    """
    import sys

    return footman.run(
        [sys.executable, "-m", "footman", "workflow.configure"],
        nofail=True,
        recorded=False,
        cwd=_root(),
    )


def _reconcile_configuration(git: GitOps, head_sha: str) -> None:
    """Re-assert configuration after an abort; silent when provably unneeded.

    The ladder: an offline diff of the governance paths from the
    aborted PR's head sha (which persists after the branch deletion)
    skips everything untouched with certainty; touched paths run the
    configure in a fresh process (the abort may sit inside a
    workflow whose update rewrote this toolchain). A failed
    configure prints its reason verbatim under a conditional note,
    never an asserted problem: the abort already succeeded, and the
    repair verb is the same either way.
    """
    from livery.workshop._governance import governance_paths

    if head_sha:
        try:
            # The branch side of the diff: an aborted branch that
            # touched governance may have applied protection before
            # its merge (the rename heal), and the abort leaves main
            # demanding the old contract.
            changed = git._run(
                "diff",
                "--name-only",
                f"origin/main...{head_sha}",
                "--",
                "workshop.toml",
                "packages/*/workshop.toml",
                *[p for p in governance_paths(_root()) if "*" not in p],
            ).strip()
        except Exception:
            changed = "unknown"
        if not changed:
            return  # nothing config-implying moved: provably unneeded
    result = _spawn_configure()
    if result.code != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "(no output)"
        indented = "\n".join(f"  {line}" for line in detail.splitlines())
        print(
            "  note: the configuration could not be re-asserted from here;"
            f" if governance settings changed, `{footman.prog()} workflow.configure`"
            " repairs them (an administrator's token may be needed):\n"
            f"{indented}"
        )
