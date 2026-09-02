"""``fm clean``: restore this working tree to a fresh checkout of HEAD.

``clean`` restores the working tree; ``workflow.abort`` stops a
workflow. This verb's object is the thing people mean by "clean":
the working tree in front of them.

Two safety properties, both about irreversibility:

- **A machine secret is never collectable.** ``*.env.local`` holds
  values that exist in no git history, so removing one cannot be
  undone by any checkout. It survives ``--all``, which is otherwise
  the take-the-gitignored-output-too switch.
- **The destructive step is deliberate.** What will be discarded is
  listed first and confirmed. Not a ``--force`` flag, a verb whose
  whole purpose is discarding must not refuse in the exact case you
  would reach for it, but not silent either.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import footman
from footman import doc, fail

#: Never removed, at any depth, under any flag. A machine secret is
#: the one thing here that no checkout can restore.
PROTECTED_SUFFIX = ".env.local"


@dataclass(frozen=True)
class CleanPlan:
    """What a clean would do, before it does any of it."""

    modified: tuple[str, ...]
    untracked: tuple[str, ...]
    protected: tuple[str, ...]

    @property
    def empty(self) -> bool:
        """Whether there is nothing to discard or remove."""
        return not (self.modified or self.untracked)


def _query(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False
    )


def _protected(path: str) -> bool:
    """Whether *path* is a machine secret the clean must never take."""
    return Path(path).name.endswith(PROTECTED_SUFFIX)


def _protected_within(root: Path, candidate: str) -> tuple[str, ...]:
    """Protected files beneath *candidate*, relative to *root*.

    The untracked enumeration collapses an untracked directory into
    one entry, so a secret nested inside it never reaches the name
    check and would be removed with its parent. Directories are
    searched so the caller can take the contents one file at a time
    instead.
    """
    target = root / candidate
    if not target.is_dir():
        return ()
    return tuple(
        sorted(
            # POSIX separators, like every other path here: these are
            # git pathspecs, and on Windows `str(Path)` yields
            # backslashes, which git reads as escapes rather than
            # directories.
            found.relative_to(root).as_posix()
            for found in target.rglob(f"*{PROTECTED_SUFFIX}")
            if found.is_file()
        )
    )


def plan_clean(root: Path, *, everything: bool) -> CleanPlan:
    """What ``clean`` would discard here; a pure read, no changes.

    ``--all`` (*everything*) widens the untracked sweep to gitignored
    files too (build output, caches), the only difference between
    the two modes besides the count.
    """
    status = _query(root, "status", "--porcelain")
    modified = tuple(
        line[3:]
        for line in status.stdout.splitlines()
        if line and not line.startswith("??")
    )
    # `git ls-files --others --directory -z` lists exactly what
    # `git clean -nd` would take, and lists it as data: NUL-separated
    # raw bytes. The dry run prints "Would remove <path>" instead,
    # which is translated (a non-English locale yields no candidates
    # at all) and C-quotes any path outside ASCII, which then fails
    # the removal round-trip. Dropping --exclude-standard widens the
    # sweep to gitignored paths, matching `clean -ndx`.
    args = ["ls-files", "--others", "--directory", "-z"]
    if not everything:
        args.append("--exclude-standard")
    listing = _query(root, *args)
    candidates = tuple(
        entry.rstrip("/") for entry in listing.stdout.split("\0") if entry
    )
    removable: list[str] = []
    protected: list[str] = []
    for candidate in candidates:
        if _protected(candidate):
            protected.append(candidate)
            continue
        nested = _protected_within(root, candidate)
        if not nested:
            removable.append(candidate)
            continue
        # The directory holds a secret, so it cannot go as one entry:
        # keep the secret and take the rest file by file.
        protected.extend(nested)
        removable.extend(
            posix
            for found in sorted((root / candidate).rglob("*"))
            if found.is_file()
            and (posix := found.relative_to(root).as_posix()) not in nested
        )
    return CleanPlan(
        modified=modified,
        untracked=tuple(removable),
        protected=tuple(protected),
    )


def render_plan(plan: CleanPlan) -> list[str]:
    """The listing shown before the confirmation."""
    lines: list[str] = []
    for path in plan.modified:
        lines.append(f"  discard changes  {path}")
    for path in plan.untracked:
        lines.append(f"  remove           {path}")
    for path in plan.protected:
        lines.append(f"  KEEP (secret)    {path}")
    return lines


def clean_tree(
    root: Path, *, everything: bool = False, assume_yes: bool = False
) -> None:
    """Restore the tree, after listing and confirming what that costs."""
    plan = plan_clean(root, everything=everything)
    if plan.empty:
        print("  Nothing to clean - the tree already matches HEAD")
        for line in render_plan(plan):
            print(line)  # a protected file is still worth naming
        return
    for line in render_plan(plan):
        print(line)
    if not assume_yes and not footman.confirm(
        f"Discard {len(plan.modified)} change(s) and remove"
        f" {len(plan.untracked)} file(s)?"
    ):
        print("  Left alone")
        return
    if plan.modified:
        restore = _query(root, "checkout", "--", ".")
        if restore.returncode != 0:
            fail(f"could not restore tracked files: {restore.stderr.strip()}")
    for path in plan.untracked:
        # One path at a time, so the protected files simply never
        # appear in the argv: `git clean` has no "except this" and an
        # -e pattern would put the secret's safety in a string nobody
        # re-reads. Mirror the plan's scope: without -x git silently
        # refuses every gitignored path it just listed, and the count
        # below would claim removals that never happened.
        remove_args = ["clean", "-fdq"]
        if everything:
            remove_args.append("-x")
        removed = _query(root, *remove_args, "--", path)
        if removed.returncode != 0:
            print(f"  could not remove {path}: {removed.stderr.strip()}")
    print(
        f"  Restored: {len(plan.modified)} change(s) discarded,"
        f" {len(plan.untracked)} file(s) removed"
    )
    if plan.protected:
        print(
            f"  Kept {len(plan.protected)} machine-secret file(s) - never collectable"
        )


@footman.task(interactive=True)
def clean(
    all: Annotated[bool, doc("also remove gitignored files (build output)")] = False,
    yes: Annotated[bool, doc("skip the confirmation")] = False,
) -> None:
    """Restore the working tree to a fresh checkout of HEAD.

    Lists what a clean would discard, asks, then restores tracked
    files and removes untracked ones. ``*.env.local`` files are kept
    at any depth under any flag: a machine secret exists in no git
    history, so removing one cannot be undone.
    """
    from livery.workshop._layers import workspace_root

    root = workspace_root()
    if root is None:
        fail("no workspace: no livery.toml above the working directory")
    clean_tree(root, everything=all, assume_yes=yes)
