"""Seed repositories built once, for every workshop test file that imports them.

A rig that inits a bare origin, clones it, commits, and pushes spends
one to two seconds in git processes before its test starts. The
`seeds` fixture builds such a rig once per session (once per xdist
worker) and copies it into the test's own directory, with every
clone's remote re-pointed at the copy, so a test still owns a
repository nobody else touches and a push from one test never
reaches another test's origin. A test module imports `seed_copier` and
`_seed_home` to register them; this is not a conftest, since
footman's suite already has one of that name and the type checkers
read the workspace as one program.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

#: A seed builder: makes the repositories under the directory it is given;
#: whatever it returns is ignored, so a helper that returns a path serves.
Build = Callable[[Path], object]

#: What the `seeds` fixture hands a test: copy the *key* seed, built by *build*.
Seeds = Callable[[str, Build], Path]


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
            build(seed)
        except BaseException:
            shutil.rmtree(seed, ignore_errors=True)
            raise
    shutil.copytree(seed, destination, dirs_exist_ok=True, symlinks=True)
    for config in destination.rglob("config"):
        bare = (config.parent / "HEAD").is_file()
        if config.parent.name != ".git" and not bare:
            continue
        text = config.read_text("utf-8")
        if str(seed) in text:
            config.write_text(text.replace(str(seed), str(destination)), "utf-8")
    return destination


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
