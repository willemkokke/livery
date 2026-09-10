"""The seed copies: built once, owned per test, remotes their own."""

from __future__ import annotations

import subprocess
from pathlib import Path

from workshop_seeds import copy_seed


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def _build(base: Path) -> None:
    origin = base / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "--initial-branch=main")
    _git(base, "clone", str(origin), "clone")
    clone = base / "clone"
    _git(clone, "config", "user.email", "t@livery.local")
    _git(clone, "config", "user.name", "T")
    (clone / "seed.txt").write_text("seed\n")
    _git(clone, "add", ".")
    _git(clone, "commit", "-m", "chore: seed")
    _git(clone, "push", "-u", "origin", "main")


def test_a_copy_owns_its_remote_and_the_seed_is_built_once(tmp_path: Path) -> None:
    # Refusals first: a push from a copy must never reach the seed's
    # origin or another copy's, or tests would see each other's work.
    home = tmp_path / "home"
    home.mkdir()
    builds: list[Path] = []

    def build(base: Path) -> None:
        builds.append(base)
        _build(base)

    first = copy_seed(home, "rig", build, tmp_path / "one")
    second = copy_seed(home, "rig", build, tmp_path / "two")
    assert len(builds) == 1
    for copy in (first, second):
        url = _git(copy / "clone", "remote", "get-url", "origin")
        assert url == str(copy / "origin.git"), url
        assert str(home / "rig") not in (copy / "clone" / ".git" / "config").read_text()
    (first / "clone" / "more.txt").write_text("more\n")
    _git(first / "clone", "add", ".")
    _git(first / "clone", "commit", "-m", "feat: more")
    _git(first / "clone", "push", "origin", "main")
    assert _git(first / "origin.git", "rev-list", "--count", "main") == "2"
    assert _git(second / "origin.git", "rev-list", "--count", "main") == "1"
    assert _git(home / "rig" / "origin.git", "rev-list", "--count", "main") == "1"
    # The copy is a working repository: clean, on main, history intact.
    assert _git(second / "clone", "status", "--porcelain") == ""
    assert _git(second / "clone", "log", "--format=%s", "-1") == "chore: seed"
