"""livery-git's pytest plugin: every test's git reads the session's own config.

The plugin rides the ``pytest11`` entry point, so the inner sessions carry
it without a conftest.
"""

from __future__ import annotations

import os
from importlib.metadata import entry_points
from pathlib import Path, PureWindowsPath
from typing import cast

import pytest

from livery.git import _pytest_isolation

pytest_plugins = ("pytester",)


def _run(pytester: pytest.Pytester, *args: str) -> pytest.RunResult:
    """Run the inner session in this process, with a base temp of its own.

    Without one the inner session numbers a directory under the
    shared ``pytest-of-<user>`` base and prunes the oldest, which is
    the outer session's: a source a test wrote there is gone when the
    gate's coverage report reads it.
    """
    base = pytester.path / "basetemp"
    return pytester.runpytest("-p", "no:cacheprovider", f"--basetemp={base}", *args)


class _Config:
    """The one part of a pytest config the plugin's hooks read."""

    def __init__(self) -> None:
        self.stash = pytest.Stash()


def test_the_plugin_rides_the_entry_point() -> None:
    points = {point.name: point.value for point in entry_points(group="pytest11")}
    assert points["livery-git-isolation"] == "livery.git._pytest_isolation"


def test_an_outer_global_config_is_kept_and_nothing_is_left_to_remove(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The fallback first: an outer redirect stands, so the session
    # writes no config and its end removes nothing.
    outer = tmp_path / "outer.gitconfig"
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(outer))
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "runner@example.invalid")
    config = cast("pytest.Config", _Config())
    _pytest_isolation.pytest_configure(config)
    assert os.environ["GIT_CONFIG_GLOBAL"] == str(outer)
    assert "GIT_AUTHOR_EMAIL" not in os.environ
    _pytest_isolation.pytest_unconfigure(config)
    assert os.environ["GIT_CONFIG_GLOBAL"] == str(outer)


def test_the_session_s_config_lives_in_the_temporary_folder_and_goes_at_its_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tempfile

    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    config = cast("pytest.Config", _Config())
    _pytest_isolation.pytest_configure(config)
    gitconfig = Path(os.environ["GIT_CONFIG_GLOBAL"])
    home = gitconfig.parent
    assert home.parent == Path(tempfile.gettempdir())
    assert home.name.startswith("livery-git-isolation-")
    templates = home / _pytest_isolation.TEMPLATES
    assert gitconfig.read_text("utf-8") == _pytest_isolation.session_config(templates)
    assert templates.is_dir()
    assert list(templates.iterdir()) == []
    _pytest_isolation.pytest_unconfigure(config)
    assert not home.exists()
    assert "GIT_CONFIG_GLOBAL" not in os.environ


#: The identity variables git reads ahead of every config.
IDENTITY_VARIABLES = (
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
)


#: The inner sessions' way to run git and read its answer.
GIT_HELPER = """
import subprocess


def git(*args, cwd=None):
    done = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return done.stdout.strip()
"""


def test_a_child_git_reads_the_tester_identity_from_the_global_config(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No outer redirect: the session writes a global config of its own,
    # and a git the test starts reads the tester identity from it.
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    pytester.makepyfile(
        GIT_HELPER
        + """

def test_identity():
    assert git("config", "--global", "user.name") == "tester"
    assert git("config", "--global", "user.email") == "tester@example.invalid"
"""
    )
    _run(pytester).assert_outcomes(passed=1)


def test_an_outer_global_git_config_is_kept(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    outer = tmp_path / "outer.gitconfig"
    outer.write_text(
        "[user]\n\tname = outer\n\temail = outer@example.invalid\n", "utf-8"
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(outer))
    pytester.makepyfile(
        GIT_HELPER
        + f"""
import os


def test_kept():
    assert os.environ["GIT_CONFIG_GLOBAL"] == {str(outer)!r}
    assert git("config", "--global", "user.email") == "outer@example.invalid"
"""
    )
    _run(pytester).assert_outcomes(passed=1)


def test_the_identity_variables_are_gone_in_the_session(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in IDENTITY_VARIABLES:
        monkeypatch.setenv(name, "runner")
    pytester.makepyfile(
        f"""
        import os

        def test_gone():
            assert [n for n in {IDENTITY_VARIABLES!r} if n in os.environ] == []
        """
    )
    _run(pytester).assert_outcomes(passed=1)


def test_a_clone_s_own_config_beats_the_tester_identity(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A test that needs a second author, a colleague's clone, configures
    # that clone, and its config wins over the session's global one, as
    # it would over a person's. An identity variable the outer
    # environment carries would beat both, so the session drops them.
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    for name in IDENTITY_VARIABLES:
        monkeypatch.setenv(name, "runner")
    pytester.makepyfile(
        GIT_HELPER
        + """

def test_colleague(tmp_path):
    git("init", "-q", cwd=tmp_path)
    git("config", "user.name", "colleague", cwd=tmp_path)
    git("config", "user.email", "colleague@example.invalid", cwd=tmp_path)
    git("commit", "-q", "--allow-empty", "-m", "first", cwd=tmp_path)
    who = git("log", "-1", "--format=%an <%ae>|%cn <%ce>", cwd=tmp_path)
    assert who.split("|") == ["colleague <colleague@example.invalid>"] * 2
"""
    )
    _run(pytester).assert_outcomes(passed=1)


def test_a_windows_template_path_is_written_with_forward_slashes() -> None:
    # git reads a backslash in a config value as an escape, so a Windows
    # path written as it is would be a bad config line.
    written = _pytest_isolation.session_config(
        PureWindowsPath(r"C:\Users\runner\AppData\Local\Temp\x\templates")
    )
    assert "\\" not in written
    assert 'templateDir = "C:/Users/runner/AppData/Local/Temp/x/templates"' in written


def test_a_repository_a_test_creates_or_clones_holds_no_template_files(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No outer redirect: the session's config names its empty template
    # directory, so neither a new repository nor a clone of it gets the
    # sample hooks, the description or info/exclude, and both still
    # commit as the tester.
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    pytester.makepyfile(
        GIT_HELPER
        + """

TEMPLATE_FILES = ("hooks", "info", "description")


def test_bare_of_templates(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    git("init", "-q", cwd=origin)
    assert [n for n in TEMPLATE_FILES if (origin / ".git" / n).exists()] == []
    git("commit", "-q", "--allow-empty", "-m", "first", cwd=origin)
    git("clone", "-q", str(origin), str(tmp_path / "clone"))
    clone = tmp_path / "clone" / ".git"
    assert [n for n in TEMPLATE_FILES if (clone / n).exists()] == []
    assert git("log", "-1", "--format=%an", cwd=tmp_path / "clone") == "tester"
"""
    )
    _run(pytester).assert_outcomes(passed=1)
