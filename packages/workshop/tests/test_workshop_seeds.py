"""The seed copies: built once, owned per test, remotes their own."""

from __future__ import annotations

import subprocess
from pathlib import Path

from workshop_seeds import cliff_config, copy_seed, pushed


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def _build(base: Path) -> None:
    pushed(base, clone="clone")


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


def test_the_copy_keeps_a_tracked_lock_and_drops_only_gits_own(tmp_path: Path) -> None:
    # Refusal first: a `uv.lock` the repository tracks must reach the
    # copy. Without it the clone starts one deletion from clean, and
    # the next `commit -a` carries that deletion instead of the edit
    # the test meant to make.
    home = tmp_path / "home"
    home.mkdir()

    def build(base: Path) -> None:
        _build(base)
        clone = base / "clone"
        (clone / "uv.lock").write_text("version = 1\n")
        _git(clone, "add", "uv.lock")
        _git(clone, "commit", "-m", "chore: lock")
        # What a git killed mid-write leaves behind, in both repositories.
        (clone / ".git" / "index.lock").write_text("")
        (base / "origin.git" / "gc.pid.lock").write_text("")

    copy = copy_seed(home, "locked", build, tmp_path / "one")
    assert (copy / "clone" / "uv.lock").read_text() == "version = 1\n"
    assert not (copy / "clone" / ".git" / "index.lock").exists()
    assert not (copy / "origin.git" / "gc.pid.lock").exists()
    assert _git(copy / "clone", "status", "--porcelain") == ""


TEMPLATE = (
    Path(__file__).resolve().parents[3]
    / "packages/workshop/src/livery/workshop/templates"
    / "package-python/cliff.toml.jinja"
)


def test_the_cliff_helper_carries_every_rule_the_template_decides_by() -> None:
    # The helper stands in for the rendered contract, so every rule
    # that decides a version or a changelog entry is the template's
    # own. Drift here passes a suite while the real train behaves
    # differently. What it leaves out is what needs a forge.
    rendered = cliff_config("thing")
    template = TEMPLATE.read_text("utf-8")
    for rule in (
        "features_always_bump_minor = true",
        "breaking_always_bump_major = false",
        "conventional_commits = true",
        "filter_unconventional = false",
        "protect_breaking_commits = true",
        'sort_commits = "oldest"',
        '{ message = "^chore\\\\(release\\\\)", skip = true },',
        '{ message = "^feat", group = "<!-- 0 -->Added" },',
        '{ message = "^fix", group = "<!-- 1 -->Fixed" },',
        '{ message = ".*", group = "<!-- 2 -->Changed" },',
    ):
        assert rule in template, rule
        assert rule in rendered, rule
    assert "[remote" not in rendered
    assert "commit_preprocessors" not in rendered
