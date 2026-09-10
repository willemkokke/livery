"""What the workshop leaves in the runner's directories, and the sweep that bounds it.

The runner's data directory holds the workshop's durable state: the
issue worktrees under ``worktrees/<repo>/`` and the loop's workspace.
footman's collector never touches it, by design. This module is the
workshop's sweeper, registered under footman's ``footman.sweepers``
entry point and run by ``fm janitor``, by the daily collector child
unattended, and, for the worktrees alone, by every ``fm issue.start``.
It also runs the state store's own janitor over every series the
workshop keeps ([livery.workshop._series][]), in the scope the run
has: the checkout's local series on a machine, the remote series too
inside CI.

The rules, each saying what went or why it stayed:

- A worktree whose linked git directory is gone (its checkout was
  deleted) goes. This rule is quick and offline, so it runs unattended.
- A worktree whose issue is closed, or whose branch's pull request
  merged, goes when the branch holds nothing that exists nowhere else,
  decided by the same refusal ``fm issue.close`` uses; a tree with
  uncommitted changes or unpushed commits stays and is named, and so is
  a tree whose issue is open or whose forge cannot be asked. This rule
  asks the forge, so it runs on demand only.
- A file or folder no code writes any more (`LEFTOVERS`) goes when
  met.
- Every declared series of the state store is bounded, windows,
  ages, and orphans, as [livery.workshop._state.sweep][] says.
- The config directory is reported, never touched: anything beside the
  user's config, tasks file, and shared env file is named. The loop's
  workspace is reported by size and kept.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from livery.workshop._git_ops import GitError, GitOps

#: The workshop's homes under the data directory.
WORKTREES = "worktrees"
LOOP = "workshop-e2e"

#: Files and folders no code writes any more; they go when met.
LEFTOVERS = ("checkouts.txt", "diagnostics", "livery-workshop")

#: What the config directory is for; anything else is reported.
CONFIG_OWN = ("config.toml", ".repo.shared.env", "__pycache__")


def sweep(
    *,
    data_dir: Path,
    config_dir: Path,
    dry_run: bool,
    unattended: bool,
    now: datetime,
    **facts: Any,
) -> list[str]:
    """The workshop's sweep, footman's sweeper contract; the lines to print.

    The state store's series are the checkout's: the one above the
    working directory, or the ``root`` among *facts* when one is
    given, ``None`` for no checkout at all.
    """
    from livery.workshop._layers import workspace_root

    root = facts["root"] if "root" in facts else workspace_root()
    lines = sweep_worktrees(
        data_dir / WORKTREES, dry_run=dry_run, unattended=unattended
    )
    lines += sweep_leftovers(data_dir, dry_run=dry_run)
    lines += sweep_store(root, dry_run=dry_run, now=now)
    if not unattended:
        lines += report_config(config_dir)
        lines += report_loop(data_dir / LOOP)
    return lines


# --- the worktrees -------------------------------------------------------------


def _gitdir(tree: Path) -> Path | None:
    """The linked git directory a worktree's ``.git`` file names, or None."""
    marker = tree / ".git"
    if not marker.is_file():
        return None
    text = marker.read_text("utf-8").strip()
    if not text.startswith("gitdir:"):
        return None
    return Path(text[len("gitdir:") :].strip())


def _remove_worktree(tree: Path, gitdir: Path | None) -> None:
    """Remove *tree* through its checkout's git when that checkout exists."""
    if gitdir is not None and gitdir.exists():
        # <checkout>/.git/worktrees/<name>: the checkout is three up.
        checkout = gitdir.parent.parent.parent
        try:
            GitOps(checkout)._run("worktree", "remove", "--force", str(tree))
            return
        except GitError:
            pass
    shutil.rmtree(tree, ignore_errors=True)
    if gitdir is not None and gitdir.exists():
        with _quiet():
            GitOps(gitdir.parent.parent.parent)._run("worktree", "prune")


class _quiet:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> bool:
        return True


def _repository(tree: Path) -> Any:
    from livery.workshop._forge_lane import this_repository

    return this_repository(tree)


def _done(tree: Path, number: int, branch: str) -> tuple[bool, str]:
    """Whether the issue is closed or its pull request merged, or why unknown."""
    try:
        repo = _repository(tree)
        work = repo.issue.get(number)
        if work is not None and work.state == "closed":
            return True, f"issue #{number} is closed"
        pull = repo.pr.find_by_head(branch, state="closed")
        if pull is not None and pull.merged:
            return True, f"PR #{pull.number} merged"
    except Exception as error:
        return False, f"the forge could not be asked ({error})"
    return False, f"issue #{number} is open"


def sweep_worktrees(home: Path, *, dry_run: bool, unattended: bool) -> list[str]:
    """Sweep every ``<repo>/<number>-<slug>`` worktree under *home*; the lines."""
    from livery.workshop._issue_tasks import _only_local_work

    lines: list[str] = []
    if not home.is_dir():
        return lines
    verb = "would remove" if dry_run else "removed"
    for repo_dir in sorted(p for p in home.iterdir() if p.is_dir()):
        for tree in sorted(p for p in repo_dir.iterdir() if p.is_dir()):
            name = f"{repo_dir.name}/{tree.name}"
            gitdir = _gitdir(tree)
            if gitdir is None:
                lines.append(f"worktree {name}: not a linked worktree; kept")
                continue
            if not gitdir.exists():
                lines.append(f"worktree {name}: its checkout is gone; {verb}")
                if not dry_run:
                    _remove_worktree(tree, None)
                continue
            if unattended:
                continue
            head, _, _rest = tree.name.partition("-")
            if not head.isdigit():
                lines.append(f"worktree {name}: names no issue; kept")
                continue
            git = GitOps(tree)
            try:
                branch = git.current_branch()
                only_local = _only_local_work(git, tree, branch)
            except GitError as error:
                lines.append(f"worktree {name}: git could not answer ({error}); kept")
                continue
            done, why = _done(tree, int(head), branch)
            if not done:
                lines.append(f"worktree {name}: {why}; kept")
                continue
            if only_local:
                lines.append(f"worktree {name}: {why}, but it holds {only_local}; kept")
                continue
            lines.append(f"worktree {name}: {why}, nothing only here; {verb}")
            if not dry_run:
                _remove_worktree(tree, gitdir)
    return lines


# --- the rest of the data directory --------------------------------------------


def sweep_store(root: Path | None, *, dry_run: bool, now: datetime) -> list[str]:
    """Bound every declared series of the state store in the run's scope; the lines."""
    from livery.workshop._series import DECLARED
    from livery.workshop._state import run_context
    from livery.workshop._state import sweep as sweep_series

    if root is None:
        return ["state store: no checkout here; nothing swept"]
    lines = sweep_series(
        root, DECLARED, remote=run_context() is not None, dry_run=dry_run, now=now
    )
    return [f"state store: {line.strip()}" for line in lines]


def sweep_leftovers(data_dir: Path, *, dry_run: bool) -> list[str]:
    """Remove files and folders no code writes any more; the lines."""
    lines: list[str] = []
    for name in LEFTOVERS:
        path = data_dir / name
        if path.exists():
            if not dry_run and path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            elif not dry_run:
                path.unlink()
            verb = "would remove" if dry_run else "removed"
            lines.append(f"{name}: no code writes it any more; {verb}")
    return lines


def report_config(config_dir: Path) -> list[str]:
    """Name what sits in the config directory beside the user's own files."""
    from livery.footman import _paths  # pyright: ignore[reportPrivateUsage]

    if not config_dir.is_dir():
        return []
    own = {*CONFIG_OWN, _paths.tasks_file_name()}
    foreign = sorted(p.name for p in config_dir.iterdir() if p.name not in own)
    if not foreign:
        return []
    return [
        f"config: {name} is not the config, the tasks file, or the shared env;"
        " yours to remove"
        for name in foreign
    ]


def report_loop(home: Path) -> list[str]:
    """The loop's workspace by size, kept: `fm ci.e2e` reuses it."""
    if not home.is_dir():
        return []
    total = sum(p.stat().st_size for p in home.rglob("*") if p.is_file())
    import livery.footman as footman

    return [
        f"loop workspace: {total / 1e6:.0f} MB at {home};"
        f" kept for `{footman.prog()} ci.e2e`"
    ]


def lines_of(items: Iterable[str]) -> list[str]:
    """A list from any iterable of lines, for callers that print."""
    return list(items)
