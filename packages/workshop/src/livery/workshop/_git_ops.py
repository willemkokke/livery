"""The local git seam the forge-lane verbs stand on.

One class, plain subprocess, no state: every method runs git in the
repository at ``root`` and returns strings or raises with git's own
words. The forge-lane flows take a livery.workshop._git_ops.GitOps so
tests drive a temporary repository through the same seam the tasks
use.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(RuntimeError):
    """A git command failed; the message carries git's words verbatim."""


class GitOps:
    """Local git operations for one repository.

    Args:
        root: The repository's working directory.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def _run(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise GitError(
                f"git {' '.join(args)} exited {result.returncode}:"
                f"\n{result.stdout}{result.stderr}"
            )
        return result.stdout

    def current_branch(self) -> str:
        """The checked-out branch name; empty when detached."""
        out = self._run("rev-parse", "--abbrev-ref", "HEAD").strip()
        return "" if out == "HEAD" else out

    def head_sha(self) -> str:
        """The commit HEAD points at."""
        return self._run("rev-parse", "HEAD").strip()

    def head_subject(self) -> str:
        """HEAD's commit subject."""
        return self._run("log", "-1", "--format=%s").strip()

    def head_body(self) -> str:
        """HEAD's commit body, without the subject."""
        return self._run("log", "-1", "--format=%b").strip()

    def fetch(self) -> None:
        """Freshen ``origin/*``."""
        self._run("fetch", "origin")

    def push(self, branch: str) -> None:
        """Push *branch* to origin, setting upstream."""
        self._run("push", "-u", "origin", branch)

    def subjects_ahead(self, base: str) -> list[str]:
        """The subjects of the commits HEAD carries beyond ``origin/<base>``."""
        out = self._run("log", "--format=%s", f"origin/{base}..HEAD")
        return [line for line in out.splitlines() if line]

    def behind_base(self, base: str) -> int:
        """How many commits ``origin/<base>`` is ahead of HEAD."""
        out = self._run("rev-list", "--count", f"HEAD..origin/{base}").strip()
        return int(out or "0")

    def conflicts_with_base(self, base: str) -> bool:
        """Whether merging ``origin/<base>`` into HEAD would conflict.

        Probed with ``git merge-tree`` so nothing in the working tree
        moves; exit 1 with conflicts is the answer, any other failure
        raises.
        """
        result = subprocess.run(
            [
                "git",
                "merge-tree",
                "--write-tree",
                "--name-only",
                "HEAD",
                f"origin/{base}",
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode in (0, 1):
            return result.returncode == 1
        raise GitError(
            f"git merge-tree exited {result.returncode}:"
            f"\n{result.stdout}{result.stderr}"
        )

    def integrate(self, base: str) -> None:
        """Merge ``origin/<base>`` into the current branch.

        A conflict raises livery.workshop._git_ops.GitError with git's
        words and leaves the merging state in place for a person to
        resolve; re-running the caller after the resolution is the
        recovery.
        """
        self.fetch()
        self._run("merge", "--no-edit", f"origin/{base}")

    def is_clean(self) -> bool:
        """Whether the working tree has no changes, staged or not."""
        return not self._run("status", "--porcelain").strip()

    def create_branch(self, name: str) -> None:
        """Create and switch to *name*."""
        self._run("checkout", "-b", name)

    def switch(self, name: str) -> None:
        """Switch to the existing branch *name*."""
        self._run("switch", name)

    def commit_all(self, message: str) -> None:
        """Stage everything and commit with *message*."""
        self._run("add", "-A")
        self._run("commit", "-m", message)

    def amend_all(self) -> None:
        """Stage everything and fold it into HEAD, message kept."""
        self._run("add", "-A")
        self._run("commit", "--amend", "--no-edit")

    def local_branch_exists(self, branch: str) -> bool:
        """Whether *branch* exists locally."""
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0

    def remote_head(self, branch: str) -> str:
        """``origin/<branch>``'s commit, or empty when it does not exist.

        Reads the local remote-tracking ref, so call
        livery.workshop._git_ops.GitOps.fetch first for a fresh answer.
        """
        result = subprocess.run(
            ["git", "rev-parse", f"origin/{branch}"],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    def changed_paths(self, base: str) -> list[str]:
        """Repo-relative paths this branch touches, committed or not.

        The committed half diffs against the merge base with
        ``origin/<base>``; the uncommitted half comes from the status
        listing, renames counted on both sides.
        """
        merge_base = self._run("merge-base", "HEAD", f"origin/{base}").strip()
        committed = self._run("diff", "--name-only", merge_base, "HEAD").splitlines()
        pending: list[str] = []
        for line in self._run("status", "--porcelain").splitlines():
            pending.extend(part for part in line[3:].split(" -> ") if part)
        return sorted({path for path in committed + pending if path})

    def tags(self) -> tuple[str, ...]:
        """Every tag name the repository knows."""
        return tuple(self._run("tag", "-l").split())

    def delete_local_branch(self, branch: str) -> None:
        """Delete the local *branch*, even if unmerged."""
        self._run("branch", "-D", branch)

    def local_branches(self, prefix: str) -> tuple[str, ...]:
        """Local branch names under *prefix* (``workflow/``), short form."""
        out = self._run(
            "for-each-ref", "--format=%(refname:short)", f"refs/heads/{prefix}"
        )
        return tuple(line.strip() for line in out.splitlines() if line.strip())

    def remote_branches(self, prefix: str) -> tuple[str, ...]:
        """Branch names under *prefix* on origin, asked of the remote.

        ``ls-remote``, never the remote-tracking refs: a plain fetch
        does not prune, so a stale tracking ref outlives the forge's
        auto-delete and would resurrect a finished workflow.
        """
        out = self._run("ls-remote", "--heads", "origin", f"refs/heads/{prefix}*")
        names: list[str] = []
        for line in out.splitlines():
            _, _, ref = line.partition("refs/heads/")
            if ref.strip():
                names.append(ref.strip())
        return tuple(names)

    def any_head(self, branch: str) -> str:
        """*branch*'s head sha: local when present, else the remote's, else empty."""
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        result = subprocess.run(
            ["git", "ls-remote", "origin", f"refs/heads/{branch}"],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.split()[0]
        return ""

    def log_paths(self, span: str, paths: tuple[str, ...]) -> tuple[str, ...]:
        """The subjects in *span* touching *paths* (``a..b`` git range)."""
        out = self._run("log", "--format=%s", span, "--", *paths)
        return tuple(line for line in out.splitlines() if line.strip())
