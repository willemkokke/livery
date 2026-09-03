"""``fm sync``: deliver every mounted layer's content to the repository.

Three channels, walked in layer order so a later layer's same-named
file wins and the instance always wins last:

- fragments into ``.workshop/`` (gitignored, regenerated wholesale):
  each layer's ``content/fragments/*``, the files the managed
  ``CLAUDE.md`` stub imports.
- skills and hooks into ``.claude/skills`` and ``.claude/hooks``
  through the materialiser: links where possible, copies where not,
  local overrides kept and named. A layer's ``settings.json`` lands
  at ``.claude/settings.json`` the same way, always as a copy:
  settings editors write the file in place.
- the managed ``CLAUDE.md`` stub itself: one import line per
  materialised fragment, then ``CLAUDE.project.md``, the repository's
  own file that nobody else writes.

Idempotent: a second run changes nothing and says nothing.
"""

from __future__ import annotations

import importlib
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING

import footman
from footman import fail, task

if TYPE_CHECKING:
    from livery.workshop._git_ops import GitOps

from livery.workshop._layers import layer_names, workspace_root
from livery.workshop._materialise import materialise, materialise_file, write_lf

# Formatted at write time: a module-level f-string would freeze the
# brand at import.
_STUB_HEADER = (
    "<!-- Managed by `{prog} sync`: one import per layer fragment, in layer\n"
    "     order, then the repository's own CLAUDE.project.md, which always\n"
    "     wins. Edit CLAUDE.project.md, never this file. -->\n"
)

#: The fragments every stub imports first, in this order, when a layer
#: ships them: the voice and documentation rules read before any
#: layer's own rules.
_GUIDANCE_FIRST = ("interaction-voice.md", "documentation-standards.md")


def _layer_content(layer: str) -> Path | None:
    """The installed layer's ``content/`` directory, or None.

    A layer is a Python package; its content ships inside the wheel.
    In the monorepo the "wheel" is the editable source tree, which is
    what lets the materialised links point back into the repository.
    """
    try:
        module = importlib.import_module(layer)
    except ModuleNotFoundError:
        return None
    root = resources.files(module)
    content = Path(str(root)) / "content"
    return content if content.is_dir() else None


def sync_workspace(root: Path) -> list[str]:
    """Deliver every layer's content into *root*; the summary lines.

    The engine behind ``fm sync``, separated so tests drive it against
    temporary trees.
    """
    lines: list[str] = []
    layers = layer_names(root)
    contents = [
        (layer, content)
        for layer in layers
        if (content := _layer_content(layer)) is not None
    ]

    workshop_dir = root / ".workshop"
    workshop_dir.mkdir(exist_ok=True)
    fragments: dict[str, Path] = {}
    for _layer, content in contents:
        fragment_dir = content / "fragments"
        if not fragment_dir.is_dir():
            continue
        for fragment in sorted(fragment_dir.iterdir()):
            if fragment.is_file():
                fragments[fragment.name] = fragment
    written = 0
    for name, source in fragments.items():
        target = workshop_dir / name
        body = source.read_bytes()
        if not target.is_file() or target.read_bytes() != body:
            target.write_bytes(body)
            written += 1
    for stale in sorted(workshop_dir.iterdir()):
        if stale.is_file() and stale.name not in fragments:
            stale.unlink()
            lines.append(f"  .workshop: removed {stale.name} (no layer ships it)")
    if written:
        lines.append(f"  .workshop: {written} fragment(s) refreshed")

    for _layer, content in contents:
        lines += materialise(root, content / "skills", "skills")
        lines += materialise(root, content / "hooks", "hooks")
        settings = content / "settings.json"
        if settings.is_file():
            lines += materialise_file(root, settings, ".claude/settings.json")

    ordered = [name for name in _GUIDANCE_FIRST if name in fragments]
    ordered += [name for name in sorted(fragments) if name not in _GUIDANCE_FIRST]
    stub = _STUB_HEADER.format(prog=footman.prog())
    stub += "".join(f"@.workshop/{name}\n" for name in ordered)
    stub += "@CLAUDE.project.md\n"
    stub_path = root / "CLAUDE.md"
    current = stub_path.read_text(encoding="utf-8") if stub_path.is_file() else ""
    if current != stub:
        write_lf(stub_path, stub)
        lines.append("  CLAUDE.md: stub regenerated")
    project = root / "CLAUDE.project.md"
    if not project.is_file():
        write_lf(
            project,
            "# This repository\n\nThe repository's own facts: nobody else"
            " writes here.\n",
        )
        lines.append("  CLAUDE.project.md: seeded; put the repository's facts here")
    return lines


def _foreign_authors(git: GitOps, onto: str) -> set[str]:
    """Author emails a rebase onto *onto* would rewrite; never this user's."""
    me = git._run("config", "user.email").strip()
    authors = git._run("log", "--format=%ae", f"{onto}..HEAD").strip()
    return {email for email in authors.splitlines() if email and email != me}


def _try_rebase(git: GitOps, onto: str) -> str:
    """Attempt a rebase onto *onto*: ``clean`` or ``conflict``.

    A conflicted attempt is aborted, so the branch is exactly as it
    was: the caller decides whether a person resolves it.
    """
    from livery.workshop._git_ops import GitError

    try:
        git._run("rebase", onto)
    except GitError:
        git._run("rebase", "--abort")
        return "conflict"
    return "clean"


def _rebase_step(git: GitOps, onto: str, *, interactive: bool) -> bool:
    """One rebase of the current branch onto *onto*; whether it landed.

    Foreign-authored commits gate the rebase: rewriting them orphans
    every other copy of the branch, so the shared-branch case goes
    through ``fm integrate`` (a merge) unless a person says
    otherwise. A conflicted rebase is never entered silently: an
    interactive run may choose to resolve it now, everything else
    parks with the teaching.
    """
    import footman

    branch = git.current_branch()
    foreign = _foreign_authors(git, onto)
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
    outcome = _try_rebase(git, onto)
    if outcome == "clean":
        print(f"  rebased {branch} onto {onto}")
        return True
    if interactive and footman.confirm(
        f"rebasing {branch} onto {onto} hits conflicts. Start the rebase"
        " and resolve them now?"
    ):
        import contextlib

        from livery.workshop._git_ops import GitError

        with contextlib.suppress(GitError):
            git._run("rebase", onto)
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


def bring_current(root: Path, git: GitOps, *, interactive: bool) -> None:
    """Bring the current checkout up to date; the one-stop's first act.

    ``main`` only ever fast-forwards. A reserved ``workflow/`` branch
    belongs to the engine, a detached HEAD names no branch, and a
    dirty tree is never moved: each skips with its note. A feature
    branch fast-forwards onto its moved remote, rebases onto it when
    diverged, then rebases onto the base; a rebase of a pushed
    branch finishes the job with the leased force-push, because
    rebased-locally with a stale remote is the worst state.
    """
    _ = root
    branch = git.current_branch()
    if not branch:
        print("  detached HEAD: nothing to bring current")
        return
    git.fetch()
    if branch == "main":
        try:
            git._run("merge", "--ff-only", "origin/main")
        except Exception:
            print(
                "  main has local commits origin does not: never rebased,"
                " never merged here. Move them to a branch."
            )
        return
    if branch.startswith("workflow/"):
        return  # the engine owns a workflow branch's staleness
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
    if behind_base != "0":
        if not _rebase_step(git, "origin/main", interactive=interactive):
            return
        rebased = True
    if rebased and remote_exists:
        # The same commits, rewritten: the lease guards anything a
        # colleague pushed since the fetch above.
        git.push_force(branch)
        print(f"  origin/{branch} follows (leased force-push)")


@task(interactive=True)
def sync() -> None:
    """Bring the checkout current, materialise content, match the lock.

    The one-stop: fast-forward or rebase the current branch (asking
    before anything conflicted or shared), then every layer's
    fragments, skills, and hooks, then ``uv sync`` so the
    environment agrees with ``uv.lock``. Idempotent: re-running it
    is the recovery procedure.
    """
    import sys

    from livery.workshop._git_ops import GitOps
    from livery.workshop._uv import run_uv

    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    bring_current(root, GitOps(root), interactive=sys.stdin.isatty())
    for line in sync_workspace(root):
        print(line)
    run_uv("sync", root=root)


@task
def integrate() -> None:
    """Bring ``origin/main`` into the current branch by merge.

    The shared-branch spelling: a merge never rewrites, so every
    other copy of the branch stays valid, and the squash erases the
    merge commit at landing. A conflict stops with git's own words;
    resolve, commit, and re-run.
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
        fail(
            f"the merge stopped on conflicts:\n{error}\n  Resolve them,"
            " `git commit`, and the branch is current; re-running any verb"
            " is the recovery."
        )
    if git.head_sha() == before:
        print("  already current with origin/main")
    else:
        print(f"  merged origin/main into {branch}")
