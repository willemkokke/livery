"""``fm sync``: deliver every mounted extension's content to the repository.

Three channels, walked in extension order so a later extension's same-named
file wins and the instance always wins last:

- prose fragments into ``.workshop/fragments/`` (gitignored): the
  agent's set from every extension's ``content/fragments/*`` and the
  registries' renders, in section order, each through the
  materialiser so an edited copy is kept and named; the registry is
  [livery.workshop._prose][]. Their own directory, so the sweep that
  clears a withdrawn fragment owns everything it walks;
  ``.workshop/`` itself holds this checkout's state, in directories
  beside it.
- skills and hooks into ``.claude/skills`` and ``.claude/hooks``
  through the materialiser: links where possible, copies where not,
  local overrides kept and named. An extension's ``settings.json`` lands
  at ``.claude/settings.json`` the same way, always as a copy:
  settings editors write the file in place.
- the managed ``CLAUDE.md`` stub itself: one import line per
  delivered fragment in section order, the repository's own
  ``fragments/`` after them, then ``CLAUDE.project.md``, the
  repository's own file that nobody else writes.

Idempotent: a second run changes nothing and says nothing.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import livery.footman.api as footman
from livery.footman.api import doc, fail, task
from livery.footman.params import Forward

if TYPE_CHECKING:
    from livery.workshop._git_ops import GitOps

from livery.workshop._extensions import (
    workspace_root,
)
from livery.workshop._materialise import write_lf


def sync_workspace(root: Path, *, local_only: bool = False) -> list[str]:
    """Deliver every extension's content into *root*; the summary lines.

    The engine behind ``fm sync``, separated so tests drive it against
    temporary trees. *local_only* writes no file a commit holds: the
    local outputs alone, and no seed.
    """
    from livery.workshop._lfs import install_hooks, lfs_enabled
    from livery.workshop._shipped_files import deliver

    lines: list[str] = deliver(root, local_only=local_only)
    if lfs_enabled(root):
        lines += install_hooks(root)
    project = root / "CLAUDE.project.md"
    if not local_only and not project.is_file():
        write_lf(
            project,
            "# This repository\n\nThe repository's own facts: nobody else"
            " writes here.\n",
        )
        lines.append("  CLAUDE.project.md: seeded; put the repository's facts here")
    return lines


def _foreign_authors(git: GitOps, since: str) -> set[str]:
    """Author emails of the commits after *since* a rebase would rewrite; never mine."""
    me = git._run("config", "user.email").strip()
    authors = git._run("log", "--format=%ae", f"{since}..HEAD").strip()
    return {email for email in authors.splitlines() if email and email != me}


def _rebase_args(onto: str, upstream: str) -> tuple[str, ...]:
    """The rebase's arguments: onto *onto*, the commits after *upstream* when given."""
    return ("--onto", onto, upstream) if upstream else (onto,)


def resolve_receipts(git: GitOps) -> bool:
    """Resolve a stopped merge or rebase whose every conflict is a render receipt.

    Returns whether it did. Each conflicted receipt file is merged key
    by key ([livery.workshop._fragment_engine.merge_receipts][]), with
    the files beside it as the conflict left them, and staged. A
    conflict in any other file leaves everything as it is, for the
    caller's own path.
    """
    from pathlib import PurePosixPath

    from livery.workshop._fragment_engine import (
        RENDERED_MANIFEST,
        merge_receipts,
        receipts_from,
        write_rendered,
    )

    unmerged = git.unmerged_paths()
    if not unmerged or any(
        PurePosixPath(path).name != RENDERED_MANIFEST for path in unmerged
    ):
        return False
    for path in unmerged:
        directory = (git.root / path).parent
        merged = merge_receipts(
            *(receipts_from(git.staged_text(path, stage)) for stage in (1, 2, 3)),
            beside=directory,
        )
        write_rendered(directory, merged)
        git.add(path)
        print(f"  {path}: the two sides' receipts merged, file by file")
    return True


def _try_rebase(git: GitOps, onto: str, upstream: str = "") -> tuple[str, str]:
    """Attempt a rebase onto *onto*; how it ended and, when it failed, git's words.

    The outcome is ``clean``, ``conflict`` or ``failed``.

    With *upstream*, only the commits after it move. A stop whose
    conflicts are all render receipts resolves them and goes on
    ([livery.workshop._sync.resolve_receipts][]). Any other stop is
    aborted, so the branch is exactly as it was: the caller decides
    whether a person resolves it. It stopped on a conflict when paths
    are left unmerged. Anything else that stops it, a commit the signer
    refused or a hook, is ``failed`` with git's own words: each needs
    another next act, and only git's words say which.
    """
    from livery.workshop._git_ops import GitError

    try:
        git._run("rebase", *_rebase_args(onto, upstream))
        return "clean", ""
    except GitError as error:
        stopped = error
    while resolve_receipts(git):
        try:
            git._run("-c", "core.editor=true", "rebase", "--continue")
        except GitError as error:
            stopped = error
            continue
        return "clean", ""
    unmerged = git.unmerged_paths()
    git._run("rebase", "--abort")
    return ("conflict", "") if unmerged else ("failed", str(stopped))


def _rebase_step(
    git: GitOps, onto: str, *, interactive: bool, upstream: str = ""
) -> bool:
    """One rebase of the current branch onto *onto*; whether it landed.

    With *upstream*, only the commits after it move: a stacked branch's
    own. Foreign-authored commits gate the rebase: rewriting them
    orphans every other copy of the branch, so the shared-branch case
    goes through ``fm integrate`` (a merge) unless a person says
    otherwise. A conflicted rebase is never entered silently: an
    interactive run may choose to resolve it now, everything else
    parks with the teaching. A rebase stopped by anything other than a
    conflict leaves the branch as it was and prints git's words.
    """
    import livery.footman.api as footman

    branch = git.current_branch()
    foreign = _foreign_authors(git, upstream or onto)
    if foreign:
        listed = ", ".join(sorted(foreign))
        if not (
            interactive
            and footman.confirm(
                f"rebasing {branch} onto {onto} rewrites commits by"
                f" {listed}; their copies would be orphaned. Rebase anyway?"
            )
        ):
            print(
                f"  left {branch} behind {onto}: it carries commits by"
                f" {listed}, and rewriting them orphans every other copy."
                f" Bring the base in by merge instead: `{footman.prog()} integrate`."
            )
            return False
    outcome, said = _try_rebase(git, onto, upstream)
    if outcome == "clean":
        print(f"  rebased {branch} onto {onto}")
        return True
    if outcome == "failed":
        print(
            f"  left {branch} behind {onto}: the rebase stopped, and not on a"
            " conflict. git said:"
        )
        for line in said.splitlines():
            print(f"    {line}")
        print(f"  Fix what git names, then run `{footman.prog()} sync` again.")
        return False
    if interactive and footman.confirm(
        f"rebasing {branch} onto {onto} hits conflicts. Start the rebase"
        " and resolve them now?"
    ):
        import contextlib

        from livery.workshop._git_ops import GitError

        with contextlib.suppress(GitError):
            git._run("rebase", *_rebase_args(onto, upstream))
        raise SystemExit(
            "  the rebase is started and waiting on you: resolve the"
            f" conflicts, `git rebase --continue`, then run `{footman.prog()} sync`"
            " again."
        )
    print(
        f"  left {branch} behind {onto}: the rebase has conflicts. Run"
        f" `{footman.prog()} sync` interactively to resolve them, or bring the base in"
        f" by merge with `{footman.prog()} integrate`."
    )
    return False


def restack(git: GitOps, branch: str, *, interactive: bool) -> bool | None:
    """Move a stacked branch's own commits onto its parent, or onto main once it merged.

    A branch ``fm start --from`` began carries its parent's commits
    under its own. The ones after the recorded start commit are the
    branch's own whatever the parent became since, so they move alone:
    onto the parent's new tip when the parent moved, onto
    ``origin/main`` once the parent merged and origin deleted it. A
    parent squashed from several commits then moves nothing it
    carried, where a plain rebase replays those commits against their
    squash and conflicts. A branch started before the start commit was
    recorded, or moved by hand off it since, falls back on git's own
    matching, which drops a parent that landed as one commit. A branch
    whose parent is still open and unchanged is left where it is: main
    reaches it through the parent.

    Returns whether the branch moved; None when it was left behind; a
    branch that is not stacked returns False.
    """
    parent, tip = git.stack(branch)
    if not parent:
        return False
    if tip and not git.is_ancestor(tip, "HEAD"):
        tip = ""  # moved by hand since: its history no longer holds the start
    merged = parent not in git.remote_branches(parent)
    onto = "origin/main" if merged else f"origin/{parent}"
    if not merged:
        target = git.sha_of(onto)
        if tip == target or (not tip and git.is_ancestor(target, "HEAD")):
            if not tip:
                git.record_stack(branch, parent, target)
            return False
    if not _rebase_step(git, onto, interactive=interactive, upstream=tip):
        return None
    if merged:
        git.forget_stack(branch)
        print(f"  {parent} has merged: {branch} stands on main now")
    else:
        git.record_stack(branch, parent, git.sha_of(onto))
    return True


def _repository(root: Path) -> Any:
    from livery.workshop._forge_lane import this_repository

    return this_repository(root)


def _leave_merged_reserved(root: Path, git: GitOps, branch: str) -> None:
    """Step off a reserved branch whose pull request merged; else the engine owns it.

    The release act returns to the branch it started from, so a
    checkout standing on a merged reserved branch is one an older act
    left there. The teardown every stop verb wears removes the branch
    and steps back onto an up-to-date main; a branch holding something
    the merge did not take is kept and named.
    """
    from livery.workshop._submit import (
        merged_pull_request,
        only_local_work,
        teardown_branch,
    )

    try:
        repo = _repository(root)
        pull = merged_pull_request(repo, git, branch)
    except Exception:
        repo = None
        pull = None
    if repo is None or pull is None:
        print(
            f"  {branch}: a reserved branch; the engine owns it until its pull"
            " request merges"
        )
        return
    why = only_local_work(git, root, branch, merged_head=pull.head_sha)
    if why:
        print(f"  {branch}: PR #{pull.number} merged, but the branch holds {why}; kept")
        return
    print(f"  {branch}: PR #{pull.number} merged; stepping off")
    teardown_branch(repo, git, branch, "main")


def continue_on_moved_code(
    root: Path, before: str, after: str, *, verb: str = "sync"
) -> bool:
    """Hand the command to a fresh process when the checkout moved under it.

    The modules this process imported are the checkout's old code;
    what follows a move reads the new source, and a mixed process
    fails on the first changed signature. The handoff re-runs the
    same command through uv on the code now on disk; *verb* names it
    in the line. Returns False when nothing moved; when the re-run
    cannot start it is named and the command continues on the loaded
    code, which returns True.
    """
    if after == before:
        return False
    from livery.workshop import _reconcile

    print(
        f"  the checkout moved from {before[:12]} to {after[:12]}; the {verb}"
        " continues on that code"
    )
    _reconcile._reexec(root)  # pyright: ignore[reportPrivateUsage]
    return True


def bring_current(root: Path, git: GitOps, *, interactive: bool) -> None:
    """Bring the current checkout up to date; the one-stop's first act.

    ``main`` only ever fast-forwards. A reserved ``workflow/`` branch
    belongs to the engine until its pull request merges, after which
    the checkout steps off it; a detached HEAD names no branch, a
    clone with no ``origin`` has nothing to follow, and a dirty tree
    is never moved: each skips with its note. A feature
    branch fast-forwards onto its moved remote, rebases onto it when
    diverged, then rebases onto the base; a stacked branch follows its
    parent instead ([livery.workshop._sync.restack][]). A rebase of a
    pushed branch finishes the job with the leased force-push, because
    rebased-locally with a stale remote is the worst state.
    """
    _ = root
    branch = git.current_branch()
    if not branch:
        print("  detached HEAD: nothing to bring current")
        return
    if not git.has_remote("origin"):
        print("  no origin remote: nothing to bring current")
        return
    git.fetch()
    if branch == "main":
        ahead = git._run("rev-list", "--count", "origin/main..main").strip()
        if ahead != "0":
            print(
                "  main has local commits origin does not: never rebased,"
                " never merged here. Move them to a branch."
            )
            return
        behind = git._run("rev-list", "--count", "main..origin/main").strip()
        if behind == "0":
            return
        try:
            git._run("merge", "--ff-only", "origin/main")
        except Exception as error:
            # The two states need different acts, so the refusal is
            # named with git's own reason, never read as "local
            # commits" (usually it is uncommitted changes in the way).
            print(f"  main is {behind} behind and the fast-forward was refused:")
            print(f"  {error}")
        return
    if branch.startswith("workflow/"):
        _leave_merged_reserved(root, git, branch)
        return
    if not git.is_clean():
        print("  uncommitted changes: the branch stays where it is")
        return
    rebased = False
    remote_exists = branch in git.remote_branches("")
    if remote_exists:
        ahead_remote = git._run(
            "rev-list", "--count", f"origin/{branch}..{branch}"
        ).strip()
        behind_remote = git._run(
            "rev-list", "--count", f"{branch}..origin/{branch}"
        ).strip()
        if behind_remote != "0":
            if ahead_remote == "0":
                git._run("merge", "--ff-only", f"origin/{branch}")
                print(f"  fast-forwarded {branch} to origin/{branch}")
            elif not _rebase_step(git, f"origin/{branch}", interactive=interactive):
                return
            else:
                rebased = True
    behind_base = git._run("rev-list", "--count", f"{branch}..origin/main").strip()
    parent, _tip = git.stack(branch)
    if parent:
        moved = restack(git, branch, interactive=interactive)
        if moved is None:
            return
        if moved:
            rebased = True
        elif behind_base != "0":
            print(
                f"  {branch} is stacked on {parent}: main reaches it when"
                f" {parent} merges"
            )
    elif behind_base != "0":
        if not _rebase_step(git, "origin/main", interactive=interactive):
            return
        rebased = True
    if rebased and remote_exists:
        # The same commits, rewritten: the lease guards anything a
        # colleague pushed since the fetch above.
        git.push_force(branch)
        print(f"  origin/{branch} follows (leased force-push)")


def _uv_flags(
    *,
    frozen: bool = False,
    locked: bool = False,
    offline: bool = False,
    upgrade: bool = False,
) -> tuple[str, ...]:
    """The uv flags for the options this verb was given, in uv's spelling."""
    flags: list[str] = []
    if frozen:
        flags.append("--frozen")
    if locked:
        flags.append("--locked")
    if offline:
        flags.append("--offline")
    if upgrade:
        flags.append("--upgrade")
    return tuple(flags)


@task
def lock(
    upgrade: Forward[bool] = False,
    upgrade_tool: Annotated[
        list[str] | None, doc("move just these tools to their newest")
    ] = None,
    check: Forward[bool] = False,
) -> None:
    """Write both locks: `tools.lock` for the tools, `uv.lock` for the venv.

    The pair to `sync`, in uv's shape: this decides what a checkout
    installs and nothing on the machine changes, and `sync` installs
    what these say. An entry that still satisfies its declarations
    stands, so a newer release moves nothing until an upgrade asks.

    ``--upgrade`` moves every entry of both locks to its newest eligible
    version, and ``--check`` reports whether both locks are current and
    writes nothing, exiting non-zero when either would move. Both mean
    the same thing to each half, so both reach both.

    A name does not. ``--upgrade-tool`` names tools and reaches the
    tools alone, because a tool is not a package in ``uv.lock``: for one
    package there, ``uv lock --upgrade-package`` is the verb.
    """
    from livery.workshop._tool_tasks import tools_lock
    from livery.workshop._uv import run_uv

    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    tools_lock(upgrade_tool=upgrade_tool)
    run_uv(
        "lock",
        *_uv_flags(upgrade=upgrade),
        *(("--check",) if check else ()),
        root=root,
    )


@task(interactive=True)
def sync(
    frozen: Forward[bool] = False,
    locked: Forward[bool] = False,
    offline: Forward[bool] = False,
) -> None:
    """Bring the checkout current, materialise content, match the lock.

    The one-stop: fast-forward or rebase the current branch (asking
    before anything conflicted or shared; a branch started on another
    with ``fm start --from`` follows its parent; a directory with no git
    history, no repository or no commit, has nothing to follow), remove
    what a removed package left under ``packages/``, fetch origin's
    state store into the checkout's mirror for the gate to read, then
    every extension's fragments, skills, and hooks, then the two locks.
    Both halves match their lock the way uv does: `tools.sync` for the
    tools, ``uv sync`` for the environment, each writing its lock when
    there is none or the declarations have moved past it. Last, the
    composed and generated files are written again, since both read the
    locks. Idempotent: re-running it is the recovery procedure.

    The three options are uv's and reach both halves: ``--frozen``
    installs each lock as it is and resolves nothing, ``--locked``
    refuses when a lock is not current, and ``--offline`` uses what the
    machine already holds. As uv's two leave its lock unchanged, ours
    change nothing a commit holds: the branch is not moved and no
    tracked file is written, while the checkout's own untracked files
    are. They differ as uv's do, in whether the locks are judged:
    ``--locked`` refuses a stale one, so CI's setup runs it and a job
    holds what a person's checkout holds; ``--frozen`` takes them as
    they are, which is how a command repairs a checkout that lacks its
    own files. Judging the tracked files stays the drift check's.

    A checkout the first act moved holds code this process has not
    loaded, so the rest of the sync is handed to a fresh process on
    that code; the handoff is named, and a process that cannot be
    started continues on the loaded code and says so.
    """
    from livery.workshop._git_ops import GitOps
    from livery.workshop._tool_tasks import sync_tools
    from livery.workshop._uv import run_uv

    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    if locked:
        # Refused before anything runs, naming the lock and the sync that
        # writes it: uv's own refusal would name uv's command instead.
        problems = stale_locks(root)
        if problems:
            fail(
                "--locked: "
                + "; ".join(problems)
                + f". `{footman.prog()} sync` writes the locks; commit them"
            )
    # Neither mode changes what a commit holds.
    local_only = locked or frozen
    git = GitOps(root)
    if not local_only and not git.has_commits():
        print("  no git history here: nothing to bring current")
    elif not local_only:
        before = git.head_sha()
        bring_current(root, git, interactive=footman.attended())
        continue_on_moved_code(root, before, git.head_sha())
    # Before anything discovers packages: a removed package's leftover
    # directory refuses discovery until it goes.
    for line in sweep_residue(root):
        print(line)
    for line in fetch_store_lines(root):
        print(line)
    for line in sync_workspace(root, local_only=local_only):
        print(line)
    # The tools come before `uv sync`: a native member's build under uv
    # runs cmake, conan and the provider the store supplies, and a
    # sibling library is consumed at HEAD only once it is registered.
    # The engine, not the `tools.sync` task: this task owns the console,
    # and a task called from inside it waits for the console to free,
    # which it never does while its caller runs.
    sync_tools(root, frozen=frozen, locked=locked, offline=offline)
    for line in conan_editables(root):
        print(line)
    run_uv("sync", *_uv_flags(frozen=frozen, locked=locked, offline=offline), root=root)
    # The locks just moved, and composed and generated files read them
    # (the locked tools' fragment, the uv pin in setup.sh), so the sync
    # ends by writing both again: the tree it leaves is settled.
    from livery.workshop._shipped_files import deliver
    from livery.workshop._templates import apply_generated

    for line in deliver(root, local_only=local_only):
        print(line)
    if not local_only:
        for path in apply_generated(root):
            print(f"  generated: {path}")
    # The receipt records this sync, so the next command's reconcile
    # compares instead of syncing again.
    from livery.workshop._reconcile import record_receipt

    record_receipt(root)


def stale_locks(root: Path) -> list[str]:
    """What is not current about *root*'s locks; empty when both are.

    The two locks `sync --locked` refuses on: `uv.lock` against the
    project's declarations (``uv lock --check``) and `tools.lock`
    against its sites. A workspace without a lock has nothing here to
    judge.
    """
    import livery.toolroom.tools.api as tools
    from livery.toolroom.store.api import LOCK_FILE
    from livery.workshop._tools import lock_is_current

    problems: list[str] = []
    if (root / "uv.lock").is_file():
        result = tools.uv.opts(cwd=root, nofail=True, recorded=False)("lock", "--check")
        if result.code != 0:
            said = [
                line.strip()
                for line in (result.stderr or result.stdout).splitlines()
                if line.strip()
            ]
            if any("needs to be updated" in line for line in said):
                problems.append(
                    "uv.lock is not current: the declarations moved past it"
                )
            else:
                # Not a verdict on the lock: uv could not resolve to
                # compare (no network, an index refused), in its words.
                error = next((line for line in said if line.startswith("error")), "")
                problems.append(
                    "uv.lock could not be checked"
                    + (f" ({error or said[-1]})" if said else "")
                )
    if (root / LOCK_FILE).is_file():
        current, why = lock_is_current(root)
        if not current:
            problems.append(f"{LOCK_FILE} is not current ({why})")
    return problems


def sweep_residue(root: Path) -> list[str]:
    """Remove what removed packages left under ``packages/``; the lines.

    A directory under ``packages/`` without a ``workshop.toml`` goes
    when git tracks nothing under it and every file it holds is one git
    ignores: the leftovers of a package the checkout moved past the
    removal of ([livery.workshop._packages.Leftover][]). A directory
    holding a file git neither tracks nor ignores, or a machine secret,
    is named and kept; one where git tracks a file is a package missing
    its contract, which discovery refuses by name. Says nothing when
    there is nothing to sweep.
    """
    import shutil

    from livery.workshop._packages import (
        RESIDUE,
        SECRET,
        UNKNOWN,
        UNTRACKED,
        leftover,
        package_directories,
    )

    lines: list[str] = []
    for directory in package_directories(root):
        if (directory / "workshop.toml").is_file():
            continue
        found = leftover(root, directory)
        name = directory.relative_to(root).as_posix()
        shown = ", ".join(found.paths[:3]) + (
            f" and {len(found.paths) - 3} more" if len(found.paths) > 3 else ""
        )
        if found.state == RESIDUE:
            try:
                shutil.rmtree(directory)
            except OSError as error:
                lines.append(
                    f"  {name}: what a removed package left behind could not be"
                    f" removed ({error}); delete the directory by hand"
                )
                continue
            lines.append(
                f"  {name}: removed {len(found.paths)} ignored file(s) a removed"
                " package left behind"
            )
        elif found.state == UNTRACKED:
            lines.append(
                f"  {name}: no workshop.toml, and it holds files git neither tracks"
                f" nor ignores ({shown}); kept"
            )
        elif found.state == SECRET:
            lines.append(
                f"  {name}: no workshop.toml, and it holds a machine secret no"
                f" checkout restores ({shown}); kept: move the secret out, then"
                " delete the directory"
            )
        elif found.state == UNKNOWN:
            lines.append(
                f"  {name}: no workshop.toml, and git could not say what it holds"
                f" ({found.reason}); kept"
            )
    return lines


def fetch_store_lines(root: Path) -> list[str]:
    """Mirror origin's state store into the checkout; the line to print.

    The mirror is what a machine's ``check`` reads
    ([livery.workshop._state.fetched_snapshot][]), so a sync or a
    start refreshes it while it has the network. A fetch that fails
    prints its reason and stops nothing: the gate falls open on a
    store it cannot read, and the last mirror stands.
    """
    from livery.workshop._state import fetch_store

    count, why = fetch_store(root)
    if why:
        return [
            f"  store: origin's state store not fetched ({why}); the last"
            " snapshot stands"
        ]
    return [f"  store: {count} ref(s) of origin's state store fetched"]


def conan_editables(root: Path) -> list[str]:
    """Register every cpp-conan member of the workspace as a conan editable; the lines.

    Conan's editable mode is its workspace source: a consumer's
    `find_package` resolves the member's reference to the member's
    source tree, built from HEAD, never to a package in the cache, the
    same as uv's workspace sources for python members. The contract
    floor stays the drift guard between the two. A workspace with no
    such member does nothing; a machine without conan deployed says
    so and registers nothing, since the store supplies conan when the
    environment is entered. That case is a line, not a refusal: a
    sync is how conan arrives, so failing on its absence would make
    the fix unreachable.
    """
    import livery.toolroom.tools.api as tools
    from livery.workshop._packages import discover_packages

    members = [p for p in discover_packages(root) if p.kind == "cpp-conan"]
    if not members:
        return []
    conan = tools.conan.opts(nofail=True, recorded=False)
    lines: list[str] = []
    for member in members:
        try:
            result = conan("editable", "add", str(member.directory))
        except OSError:
            # The handle spawns by name; an undeployed conan raises
            # here rather than answering with a failing result.
            return [
                "  conan editables: conan is not on PATH, so none registered; enter"
                f" the environment and re-run `{footman.prog()} sync`"
            ]
        if result.code == 0:
            lines.append(f"  conan editable: {member.path} at HEAD")
        else:
            tail = (result.stdout + result.stderr).strip().splitlines()
            why = tail[-1] if tail else f"exit {result.code}"
            lines.append(f"  conan editable: {member.path} refused: {why}")
    return lines


def materialise_tools(root: Path, *, offline: bool = False) -> list[str]:
    """Supply every locked tool through the store and write the receipts; the lines.

    The bundle the sites require, materialised as `fm sync` and
    `fm tools.materialise` enter the environment, and the stubs the
    checkers read written after it: a checkout with no `tools.lock`
    yet has nothing to materialise and says so. *offline* supplies
    from the machine's store and its sources alone.
    """
    from livery.workshop._tools import current_lock, materialise, stub_lines

    if current_lock(root) is None:
        return [f"  tools: no tools.lock; `{footman.prog()} tools.lock` writes one"]
    done = materialise(root, strict=False, offline=offline)
    supplied = [m for m in done if m.receipt is not None]
    installed = [m.receipt.tool for m in supplied if m.installed and m.receipt]
    hosted = [
        f"{m.receipt.tool} {m.receipt.answered}".rstrip()
        for m in supplied
        if m.receipt is not None and m.receipt.source == "host"
    ]
    lines = [
        f"  tools: {len(supplied)} receipt(s)"
        + (f", installed {', '.join(installed)}" if installed else ", all present")
        + (f", from the host: {', '.join(hosted)}" if hosted else "")
    ]
    lines += [f"  tools: {m.note}" for m in done if m.note]
    lines += [f"  tools: could not materialise: {m.failure}" for m in done if m.failure]
    return lines + stub_lines(root, strict=False, offline=offline)


@task
def integrate() -> None:
    """Bring ``origin/main`` into the current branch by merge.

    The shared-branch spelling: a merge never rewrites, so every
    other copy of the branch stays valid, and the squash erases the
    merge commit at landing. A conflict in the render's receipts alone
    is merged key by key and committed; any other conflict stops with
    git's own words: resolve, commit, and re-run.
    """
    from livery.workshop._git_ops import GitError, GitOps

    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    git = GitOps(root)
    branch = git.current_branch()
    if not branch or branch == "main" or branch.startswith("workflow/"):
        fail(
            "integrate brings origin/main into a feature branch, and"
            f" you are on {branch or '(detached)'!r}."
        )
    if not git.is_clean():
        fail(
            "the working tree has uncommitted changes: commit them first,"
            " so the merge has one parent to speak for you."
        )
    before = git.head_sha()
    try:
        git.integrate("main")
    except GitError as error:
        if not resolve_receipts(git):
            fail(
                f"the merge stopped on conflicts:\n{error}\n  Resolve them,"
                " `git commit`, and the branch is current; re-running any verb"
                " is the recovery."
            )
        git._run("-c", "core.editor=true", "commit", "--no-edit")
    if git.head_sha() == before:
        print("  already current with origin/main")
        return
    print(f"  merged origin/main into {branch}")
    for line in sweep_residue(root):
        print(line)
    match_lock(root, git, since=before)


#: The files whose change across a HEAD move leaves the venv behind:
#: the lock, the root manifest, and every member's.
LOCK_FILES = ("uv.lock", "pyproject.toml", "packages/*/pyproject.toml")


def match_lock(root: Path, git: GitOps, *, since: str) -> None:
    """Match the venv to the lock when HEAD moved across a change to it; say so.

    *since* is the commit HEAD moved from. A merge that changed the
    lock, the root manifest, or a member's manifest leaves the venv
    installed from the old ones (a new entry point the venv never
    learned made the next gate red until a sync); the sync runs here,
    at the verb's end, and the receipts record it, so the next
    command's reconcile finds nothing to do.
    """
    from livery.workshop._reconcile import record_receipt
    from livery.workshop._uv import run_uv

    changed = git.changed_between(since, "HEAD", LOCK_FILES)
    if not changed:
        return
    print(f"  matching the lock: the merge changed {', '.join(changed)}")
    run_uv("sync", root=root)
    record_receipt(root)
    print("  matched the lock; the environment agrees with the merge")
