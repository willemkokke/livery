"""The repositories the store's tests drive, and the store of one.

A helper module named for its package, since every tests directory
shares one path.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from livery import git

#: The identity every store under test commits with.
IDENTITY = ("tester", "tester@example.invalid")


def store_of(root: Path, *, in_ci: bool = False) -> git.StateStore:
    """The store of *root* with a desk's policy, or a CI run's under *in_ci*."""
    return git.StateStore(
        root, in_ci=in_ci, identity=IDENTITY, snapshot_variable="WORKSHOP_SNAPSHOT"
    )


def run_git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> str:
    """Run git in *cwd* and return its output; a failure raises with its words."""
    done = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, env=env
    )
    return done.stdout


def clone(tmp_path: Path, name: str, origin: Path) -> Path:
    """A clone of *origin* under *tmp_path* as *name*."""
    work = tmp_path / name
    run_git(tmp_path, "clone", "-q", str(origin), str(work))
    return work


def seed(base: Path) -> None:
    """A bare origin with one commit on main and a clone, `work`, under *base*."""
    origin = base / "origin.git"
    run_git(base, "init", "-q", "--bare", "--initial-branch=main", str(origin))
    work = clone(base, "work", origin)
    (work / "README.md").write_text("the repository\n")
    run_git(work, "add", "README.md")
    run_git(work, "commit", "-qm", "init")
    run_git(work, "push", "-q", "-u", "origin", "main")


@pytest.fixture(name="repos")
def repos_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """A bare origin with one commit on main, and a working clone of it."""
    seed(tmp_path)
    return tmp_path / "origin.git", tmp_path / "work"
