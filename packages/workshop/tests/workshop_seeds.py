"""Seed repositories built once, for every workshop test file that imports them.

A rig that inits a bare origin, clones it, commits, and pushes spends
one to two seconds in git processes before its test starts. The
`seeds` fixture builds such a rig once per session (once per xdist
worker) and copies it into the test's own directory, with every
clone's remote re-pointed at the copy, so a test still owns a
repository nobody else touches and a push from one test never
reaches another test's origin. A test module imports `seed_copier` and
`_seed_home` to register them, and `fake_notes` for a stand-in
release-notes provider; this is not a conftest, since
footman's suite already has one of that name and the type checkers
read the workspace as one program.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from livery.workshop import Package

#: A seed builder: makes the repositories under the directory it is given;
#: whatever it returns is ignored, so a helper that returns a path serves.
Build = Callable[[Path], object]

#: What the `seeds` fixture hands a test: copy the *key* seed, built by *build*.
Seeds = Callable[[str, Build], Path]

#: Writes the files of a seed's first commit into the clone it is given.
Fill = Callable[[Path], None]


def copy_seed(home: Path, key: str, build: Build, destination: Path) -> Path:
    """Build the *key* seed under *home* once, copy it into *destination*.

    The copy keeps the seed's relative layout. Every git config in the
    copy that named the seed's path names the copy's instead, so the
    clones' remotes are the copy's own bare repositories.
    """
    seed = home / key
    if not seed.is_dir():
        # Built in place, so the clones' remotes name the seed's own
        # path, which the copy below rewrites; a build that fails
        # leaves nothing behind for the next test to mistake for a seed.
        seed.mkdir()
        try:
            _without_auto_maintenance(build, seed)
        except BaseException:
            shutil.rmtree(seed, ignore_errors=True)
            raise
    _copy(seed, destination)
    # Git spells a path in its config as it was given, forward slashes
    # on every platform where it normalises, and a backslash escaped as
    # two; every spelling the seed's path could have taken is renamed.
    spellings = [
        (str(seed), str(destination)),
        (seed.as_posix(), destination.as_posix()),
        (str(seed).replace("\\", "\\\\"), str(destination).replace("\\", "\\\\")),
    ]
    for config in destination.rglob("config"):
        bare = (config.parent / "HEAD").is_file()
        if config.parent.name != ".git" and not bare:
            continue
        text = config.read_text("utf-8")
        renamed = text
        for old, new in spellings:
            renamed = renamed.replace(old, new)
        if renamed != text:
            config.write_text(renamed, "utf-8")
    return destination


def _without_auto_maintenance(build: Build, seed: Path) -> None:
    """Run *build* with git's automatic gc and maintenance off.

    A commit or push may detach `git gc --auto`, which rewrites the
    object store while the first copy reads it (seen on a macOS
    runner: `objects/maintenance.lock` vanished mid-copy). The
    variables reach every git the build spawns and nothing after it.
    """
    # Appended after the keys already in the environment (the suite's
    # signing overlay), never in their place.
    first = int(os.environ.get("GIT_CONFIG_COUNT", "0") or "0")
    added = {
        "GIT_CONFIG_COUNT": str(first + 2),
        f"GIT_CONFIG_KEY_{first}": "gc.auto",
        f"GIT_CONFIG_VALUE_{first}": "0",
        f"GIT_CONFIG_KEY_{first + 1}": "maintenance.auto",
        f"GIT_CONFIG_VALUE_{first + 1}": "false",
    }
    before = {key: os.environ.get(key) for key in added}
    os.environ.update(added)
    try:
        build(seed)
    finally:
        for key, value in before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _copy(seed: Path, destination: Path) -> None:
    """Copy the seed, git's locks left out, retried once over a git still writing."""

    def ignore(directory: str, names: list[str]) -> set[str]:
        # Only git's own lock files go: a `uv.lock` in the work tree is
        # a file the repository tracks, and a copy without it leaves
        # the clone one deletion away from clean.
        if not _inside_git(Path(directory), seed):
            return set()
        return {name for name in names if name.endswith(".lock")}

    for attempt in range(3):
        try:
            shutil.copytree(
                seed, destination, dirs_exist_ok=True, symlinks=True, ignore=ignore
            )
            return
        except shutil.Error:
            if attempt == 2:
                raise
            time.sleep(0.5)


def _inside_git(directory: Path, seed: Path) -> bool:
    """Whether *directory* is one of git's own, or lies inside one.

    A git directory is `.git`, or a bare repository, which is any
    directory holding a `HEAD` file. The walk stops at *seed*, so a
    directory outside it is never read.
    """
    current = directory
    while True:
        if current.name == ".git" or (current / "HEAD").is_file():
            return True
        if current == seed or current.parent == current:
            return False
        current = current.parent


@pytest.fixture(scope="session")
def _seed_home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("seeds")


# The fixture's name is `seeds`; the function has another name so a
# test module can import it beside a rig whose parameter is `seeds`.
@pytest.fixture(name="seeds")
def seed_copier(_seed_home: Path, tmp_path: Path) -> Seeds:
    """Copy the *key* seed, built once by *build*, into this test's directory.

    Returns the test's directory; the repositories sit at the relative
    paths *build* made them at.
    """

    def get(key: str, build: Build) -> Path:
        return copy_seed(_seed_home, key, build, tmp_path)

    return get


def pushed(base: Path, *, clone: str = "work", fill: Fill | None = None) -> Path:
    """Make a bare origin under *base* and a clone of it whose main is pushed.

    The clone has a committer identity and one commit. Returns the
    clone, which sits at `base / clone`, beside `base / "origin.git"`.

    Args:
        base: the directory the origin and the clone are made under.
        clone: the clone's directory name, which a suite asserting on
            paths chooses to read well in its own messages.
        fill: writes the first commit's files into the clone. The
            default writes one `seed.txt`, which a test may edit and
            commit with `-a`.
    """
    origin = base / "origin.git"
    _git(base, "init", "-q", "--bare", "--initial-branch=main", str(origin))
    repo = base / clone
    _git(base, "clone", "-q", str(origin), str(repo))
    _git(repo, "config", "user.name", "tester")
    _git(repo, "config", "user.email", "tester@example.invalid")
    if fill is None:
        (repo / "seed.txt").write_text("seed\n")
    else:
        fill(repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "chore: seed")
    _git(repo, "push", "-q", "-u", "origin", "main")
    return repo


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


class FakeNotes:
    """A release-notes provider standing in for an extension's.

    Each entry is headed by its version and says `line`. The history is
    the package's ``CHANGELOG.md``, newest entry first, created at the
    first record; an entry already under its version is replaced by a
    different one, as a provider regenerates a stranded entry.
    `recorded` keeps every entry the train handed it.
    """

    def __init__(self, line: str = "- what changed") -> None:
        self.line = line
        self.recorded: list[tuple[str, str]] = []

    def entry(self, root: Path, package: Package, version: str = "") -> str:
        """The entry for *version*, or for what is unreleased."""
        del root, package
        return f"## [{version or 'Unreleased'}]\n\n{self.line}"

    def record(self, package: Package, version: str, entry: str) -> list[str]:
        """Write *entry* under *version*'s heading; what changed."""
        self.recorded.append((version, entry))
        changelog = package.directory / "CHANGELOG.md"
        text = changelog.read_text("utf-8") if changelog.is_file() else "# Changelog\n"
        head, *blocks = re.split(r"\n(?=## )", text)
        block = (entry or f"## [{version}]\n\n-").strip() + "\n"
        heading = re.compile(rf"## \[?{re.escape(version)}\]?(\s|$)")
        mine = [index for index, found in enumerate(blocks) if heading.match(found)]
        if not mine:
            blocks.insert(0, block)
        elif entry and blocks[mine[0]].strip() != entry.strip():
            blocks[mine[0]] = block
        else:
            return []
        changelog.write_text("\n".join([head, *blocks]), encoding="utf-8")
        return ["CHANGELOG.md"]

    def verify(self, package: Package, version: str) -> list[str]:
        """A missing ``## <version>`` entry, or nothing."""
        changelog = package.directory / "CHANGELOG.md"
        body = changelog.read_text("utf-8") if changelog.is_file() else ""
        if f"## {version}" in body or f"## [{version}]" in body:
            return []
        return [f"CHANGELOG.md has no '## {version}' entry"]


@pytest.fixture(name="notes")
def fake_notes(monkeypatch: pytest.MonkeyPatch) -> FakeNotes:
    """A stand-in provider, registered as a listed extension registers its own."""
    from livery.workshop import _release_notes

    provider = FakeNotes()
    monkeypatch.setattr(_release_notes, "_PROVIDER", [(provider, "acme.notes")])
    return provider


def member(root: Path, name: str, *, floor_on: str = "") -> None:
    """A python member *name* under *root* at 0.2.0, with a floor on *floor_on*."""
    directory = root / "packages" / name
    (directory / "src" / "livery" / name).mkdir(parents=True)
    depends = (
        f'[[depends]]\npath = "packages/{floor_on}"\nkind = "build"\nfloor = "0.1.0"\n'
        if floor_on
        else ""
    )
    requirement = f'"livery-{floor_on}>=0.1.0"' if floor_on else ""
    (directory / "workshop.toml").write_text(
        f'kind = "python"\nname = "livery-{name}"\n{depends}'
    )
    (directory / "pyproject.toml").write_text(
        f'[project]\nname = "livery-{name}"\nversion = "0.2.0"\n'
        f"dependencies = [{requirement}]\n"
    )
    (directory / "CHANGELOG.md").write_text("# Changelog\n\n## 0.2.0\n\n- x\n")
    (directory / "src" / "livery" / name / "__init__.py").write_text(
        '__version__ = "0.2.0"\n'
    )
