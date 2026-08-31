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

    def delete_local_branch(self, branch: str) -> None:
        """Delete the local *branch*, even if unmerged."""
        self._run("branch", "-D", branch)
