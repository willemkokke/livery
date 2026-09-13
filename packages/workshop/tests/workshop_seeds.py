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

import os
import shutil
import time
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
    added = {
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "gc.auto",
        "GIT_CONFIG_VALUE_0": "0",
        "GIT_CONFIG_KEY_1": "maintenance.auto",
        "GIT_CONFIG_VALUE_1": "false",
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
    """Copy the seed, lock files left out, retried once over a git still writing."""
    ignore = shutil.ignore_patterns("*.lock")
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


def cliff_config(name: str) -> str:
    """A member's ``cliff.toml``: its own tag pattern and paths, the house groups."""
    body = (
        'body = """\n'
        '{% if version %}## [{{ version | split(pat="/") | last'
        ' | trim_start_matches(pat="v") }}] - '
        '{{ timestamp | date(format="%Y-%m-%d") }}'
        "{% else %}## [Unreleased]{% endif %}\n"
        '{% for group, commits in commits | group_by(attribute="group") %}\n'
        "### {{ group | striptags | trim }}\n"
        "{% for commit in commits %}\n"
        "- {{ commit.message | upper_first }}\n"
        "{%- endfor %}\n"
        "{% endfor %}\n"
        '"""\n'
    )
    return (
        "[bump]\n"
        "features_always_bump_minor = true\n"
        "breaking_always_bump_major = false\n"
        f'initial_tag = "packages/{name}/v0.0.0"\n'
        "\n[git]\n"
        f'tag_pattern = "^packages/{name}/v?(.+)$"\n'
        f'include_paths = ["packages/{name}/**"]\n'
        "conventional_commits = true\n"
        "filter_unconventional = false\n"
        'sort_commits = "oldest"\n'
        "commit_parsers = [\n"
        '  { message = "^chore\\\\(release\\\\)", skip = true },\n'
        '  { message = "^feat", group = "<!-- 0 -->Added" },\n'
        '  { message = "^fix", group = "<!-- 1 -->Fixed" },\n'
        '  { message = ".*", group = "<!-- 2 -->Changed" },\n'
        "]\n"
        "\n[changelog]\n"
        'header = "# Changelog\\n"\n' + body + "trim = true\n"
    )


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
        f'type = "python"\nname = "livery-{name}"\n{depends}'
    )
    (directory / "pyproject.toml").write_text(
        f'[project]\nname = "livery-{name}"\nversion = "0.2.0"\n'
        f"dependencies = [{requirement}]\n"
    )
    (directory / "CHANGELOG.md").write_text("# Changelog\n\n## 0.2.0\n\n- x\n")
    (directory / "src" / "livery" / name / "__init__.py").write_text(
        '__version__ = "0.2.0"\n'
    )
    (directory / "cliff.toml").write_text(cliff_config(name))
