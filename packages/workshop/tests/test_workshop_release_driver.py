"""The release driver's edge table, forced: refusals, rollback, recovery.

The heavy validation (uv build plus two isolated venv legs) runs for
real exactly once, on a minimal package; every flow test monkeypatches
it, the flows' own edges are what they force.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from footman import Failed

from livery.forge.testing import FakeForge
from livery.workshop._git_ops import GitOps
from livery.workshop._graph import order_topologically
from livery.workshop._packages import discover_packages
from livery.workshop._release_driver import (
    MemberPlan,
    ReleaseDriver,
    bump_set_floors,
    derive_plans,
    local_release,
    release_name,
    require_verified_base,
    resolve_set,
    rollback_prepare,
)
from livery.workshop._workflow_engine import run_workflow

_FAILURES = (SystemExit, Failed)

OWNER, NAME = "willemkokke", "livery"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True)


def _cliff_config(name: str) -> str:
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


def _member(root: Path, name: str, *, floor_on: str = "") -> None:
    directory = root / "packages" / name
    (directory / "src" / "livery" / name).mkdir(parents=True)
    depends = (
        f'[[depends]]\npath = "packages/{floor_on}"\nkind = "build"\nfloor = "0.1.0"\n'
        if floor_on
        else ""
    )
    requirement = f'"livery-{floor_on}>=0.1.0"' if floor_on else ""
    (directory / "livery.toml").write_text(
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
    (directory / "cliff.toml").write_text(_cliff_config(name))


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[FakeForge, GitOps, Path]:
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "--initial-branch=main")
    root = tmp_path / "ws"
    _git(tmp_path, "clone", str(origin), "ws")
    _git(root, "config", "user.email", "t@livery.local")
    _git(root, "config", "user.name", "T")
    (root / "livery.toml").write_text("[workspace]\n")
    _member(root, "core")
    _member(root, "tool", floor_on="core")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "chore: seed")
    _git(root, "tag", "packages/core/v0.2.0")
    _git(root, "tag", "packages/tool/v0.2.0")
    _git(root, "push", "-u", "origin", "main")
    fake = FakeForge()
    fake.create_repo(OWNER, NAME, private=True, description="t")
    return fake, GitOps(root), root


class _RigGit(GitOps):
    def __init__(self, root: Path, fake: FakeForge) -> None:
        super().__init__(root)
        self.fake = fake

    def push(self, branch: str) -> None:
        super().push(branch)
        sha = self.fake.push(OWNER, NAME, branch, sha=self.head_sha())
        self.fake.settle(OWNER, NAME, sha)


def _grow(root: Path, name: str, message: str) -> None:
    marker = root / "packages" / name / "src" / "livery" / name / "grown.py"
    marker.write_text("x = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", message)


def test_an_unchanged_member_refuses_before_the_branch_exists(
    workspace: tuple[FakeForge, GitOps, Path],
) -> None:
    _fake, git, root = workspace
    members = resolve_set(root, ("core", "tool"))
    _grow(root, "core", "feat: core grows (#1)")
    with pytest.raises(_FAILURES) as caught:
        derive_plans(root, members)
    message = str(caught.value)
    assert "tool" in message and "drop them from the set" in message
    # Nothing to undo, structurally: no workflow branch was ever cut.
    assert git.local_branches("workflow/") == ()


def test_resolve_set_teaches_the_members(
    workspace: tuple[FakeForge, GitOps, Path],
) -> None:
    _fake, _git_seam, root = workspace
    with pytest.raises(_FAILURES) as caught:
        resolve_set(root, ("ghost",))
    assert "packages/core" in str(caught.value)
    # Bare names and full paths both resolve, dependency order held.
    ordered = resolve_set(root, ("tool", "packages/core"))
    assert [p.directory.name for p in ordered] == ["core", "tool"]


def test_order_topologically_puts_dependencies_first(
    workspace: tuple[FakeForge, GitOps, Path],
) -> None:
    _fake, _git_seam, root = workspace
    packages = {p.directory.name: p for p in discover_packages(root)}
    ordered = order_topologically((packages["tool"], packages["core"]))
    assert [p.directory.name for p in ordered] == ["core", "tool"]


def test_floors_rise_within_the_set_in_both_homes(
    workspace: tuple[FakeForge, GitOps, Path],
) -> None:
    _fake, _git_seam, root = workspace
    packages = {p.directory.name: p for p in discover_packages(root)}
    plans = (
        MemberPlan(packages["core"], "0.3.0"),
        MemberPlan(packages["tool"], "0.3.0"),
    )
    changed = bump_set_floors(root, plans)
    tool = root / "packages" / "tool"
    assert '"livery-core>=0.3.0"' in (tool / "pyproject.toml").read_text()
    assert 'floor = "0.3.0"' in (tool / "livery.toml").read_text()
    assert sorted(changed) == [
        "packages/tool/livery.toml",
        "packages/tool/pyproject.toml",
    ]


def test_rollback_restores_exactly_what_prepare_writes(
    workspace: tuple[FakeForge, GitOps, Path],
) -> None:
    _fake, git, root = workspace
    members = resolve_set(root, ("core", "tool"))
    before = {
        path: path.read_text()
        for member in members
        for path in [
            member.directory / "CHANGELOG.md",
            member.directory / "pyproject.toml",
            member.directory / "livery.toml",
        ]
    }
    from livery.workshop._release import prepare_release

    prepare_release(root, "packages/core", "9.9.9")
    bump_set_floors(
        root,
        (MemberPlan(members[0], "9.9.9"), MemberPlan(members[1], "9.9.9")),
    )
    rollback_prepare(root, members)
    after = {path: path.read_text() for path in before}
    assert after == before
    assert git.is_clean()


def test_the_base_gate_refuses_red_and_teaches(
    workspace: tuple[FakeForge, GitOps, Path],
) -> None:
    fake, git, _root_dir = workspace
    sha = git.remote_head("main")
    fake.push(OWNER, NAME, "main", outcome="failure", sha=sha)
    fake.settle(OWNER, NAME, sha)
    repo = fake.repository(OWNER, NAME)
    with pytest.raises(_FAILURES) as caught:
        require_verified_base(repo, git, "main")
    message = str(caught.value)
    assert "red" in message and "force-unverified-base" in message
    # And force is the taught override, skipping the wait entirely.
    require_verified_base(repo, git, "main", force=True)


def test_the_base_gate_fails_open_when_the_forge_is_unreachable(
    workspace: tuple[FakeForge, GitOps, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, git, _root_dir = workspace
    repo = fake.repository(OWNER, NAME)

    def _down(sha: str):
        raise RuntimeError("down")

    monkeypatch.setattr(repo.checks, "status", _down)
    require_verified_base(repo, git, "main")  # returns; the engine's story


def test_local_release_reports_builds_and_restores(
    workspace: tuple[FakeForge, GitOps, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _fake, git, root = workspace
    _grow(root, "core", "feat: core grows (#1)")
    _grow(root, "tool", "feat: tool grows (#2)")
    validated: list[str] = []
    monkeypatch.setattr(
        "livery.workshop._release_driver.validate_member",
        lambda _root, plan, dirs: validated.append(plan.package.name),
    )
    members = resolve_set(root, ("core", "tool"))
    local_release(root, members)
    out = capsys.readouterr().out
    assert "would release: livery-core v0.3.0, livery-tool v0.3.0" in out
    assert validated == ["livery-core", "livery-tool"]
    assert git.is_clean()  # the stamps rolled back


def test_a_failing_member_rolls_the_whole_prepare_back(
    workspace: tuple[FakeForge, GitOps, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, _git_seam, root = workspace
    _grow(root, "core", "feat: core grows (#1)")
    _grow(root, "tool", "feat: tool grows (#2)")
    _git(root, "push", "origin", "main")
    rig = _RigGit(root, fake)
    sha = rig.remote_head("main")
    fake.push(OWNER, NAME, "main", sha=sha)
    fake.settle(OWNER, NAME, sha)

    def _explode(_root: Path, plan: MemberPlan, dirs: tuple[Path, ...]) -> None:
        if plan.package.name == "livery-tool":
            raise SystemExit("tool validation failed")

    monkeypatch.setattr("livery.workshop._release_driver.validate_member", _explode)
    driver = ReleaseDriver(
        root,
        fake.repository(OWNER, NAME),
        rig,
        resolve_set(root, ("core", "tool")),
        armed=False,
    )
    with pytest.raises(_FAILURES):
        driver.prepare()
    # The base is restored exactly: clean tree, on main, no branch.
    assert rig.current_branch() == "main"
    assert rig.is_clean()
    assert not rig.local_branch_exists(driver.branch)


def test_the_driver_prepares_commits_and_the_engine_lands_it(
    workspace: tuple[FakeForge, GitOps, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, _git_seam, root = workspace
    _grow(root, "core", "feat: core grows (#1)")
    _grow(root, "tool", "feat: tool grows (#2)")
    _git(root, "push", "origin", "main")
    rig = _RigGit(root, fake)
    sha = rig.remote_head("main")
    fake.push(OWNER, NAME, "main", sha=sha)
    fake.settle(OWNER, NAME, sha)
    monkeypatch.setattr(
        "livery.workshop._release_driver.validate_member",
        lambda _root, plan, dirs: None,
    )
    members = resolve_set(root, ("core", "tool"))
    driver = ReleaseDriver(root, fake.repository(OWNER, NAME), rig, members, armed=True)
    run_workflow(driver, fake.repository(OWNER, NAME), rig, current_user="fake-user")
    pr = fake.repository(OWNER, NAME).pr.get(1)
    assert pr is not None and pr.merged
    assert pr.title == "chore(release): released livery-core v0.3.0, livery-tool v0.3.0"
    assert "- **livery-core** v0.3.0" in pr.body
    subjects = rig.log_paths("origin/main..HEAD", (".",))
    assert subjects == (
        "chore(release): livery-tool v0.3.0",
        "chore(release): livery-core v0.3.0",
    )
    tool_pyproject = (root / "packages" / "tool" / "pyproject.toml").read_text()
    assert '"livery-core>=0.3.0"' in tool_pyproject


def test_recovery_reads_the_branch_and_rebuilds_nothing(
    workspace: tuple[FakeForge, GitOps, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, _git_seam, root = workspace
    _grow(root, "core", "feat: core grows (#1)")
    _grow(root, "tool", "feat: tool grows (#2)")
    _git(root, "push", "origin", "main")
    rig = _RigGit(root, fake)
    sha = rig.remote_head("main")
    fake.push(OWNER, NAME, "main", sha=sha)
    fake.settle(OWNER, NAME, sha)
    monkeypatch.setattr(
        "livery.workshop._release_driver.validate_member",
        lambda _root, plan, dirs: None,
    )
    members = resolve_set(root, ("core", "tool"))
    driver = ReleaseDriver(
        root, fake.repository(OWNER, NAME), rig, members, armed=False
    )
    first = driver.prepare()
    assert first is not None
    rebuilt: list[str] = []
    monkeypatch.setattr(
        "livery.workshop._release_driver.derive_plans",
        lambda _root, _members: rebuilt.append("derived"),
    )
    again = ReleaseDriver(root, fake.repository(OWNER, NAME), rig, members, armed=False)
    recovered = again.prepare()
    assert recovered is not None
    assert recovered.title == first.title  # from the ref, not the tree
    assert rebuilt == []  # recovery derives nothing


def test_release_name_sorts_its_members() -> None:
    assert release_name(("workshop", "forge")) == "release/forge+workshop"


def test_the_base_gate_times_out_naming_the_runners(
    workspace: tuple[FakeForge, GitOps, Path],
) -> None:
    # A tip the forge never mints a run for: the gate waits, then the
    # timeout names the condition instead of prescribing patience.
    fake, git, _root_dir = workspace
    repo = fake.repository(OWNER, NAME)
    with pytest.raises(_FAILURES) as caught:
        require_verified_base(repo, git, "main", timeout=0.2, poll=0.01)
    assert "did not report green" in str(caught.value)


def test_the_isolated_legs_run_for_real_on_a_dependency_free_member(
    workspace: tuple[FakeForge, GitOps, Path],
) -> None:
    # The one unmocked validation: uv build, two fresh venvs, the
    # installed copy's tests. Everything else in this suite may stub
    # it because this proves the machinery.
    _fake, _git_seam, root = workspace
    member = root / "packages" / "core"
    (member / "pyproject.toml").write_text(
        '[project]\nname = "livery-core"\nversion = "0.2.0"\n'
        'requires-python = ">=3.11"\ndependencies = []\n'
        "[build-system]\n"
        'requires = ["uv_build>=0.7"]\nbuild-backend = "uv_build"\n'
        "[tool.uv.build-backend]\n"
        'module-name = "livery.core"\nnamespace = true\n'
    )
    tests = member / "tests"
    tests.mkdir()
    (tests / "test_installed.py").write_text(
        "from livery.core import answer\n\n\n"
        "def test_answer() -> None:\n    assert answer() == 42\n"
    )
    (member / "src" / "livery" / "core" / "answer.py").write_text(
        "def answer() -> int:\n    return 42\n"
    )
    (member / "src" / "livery" / "core" / "__init__.py").write_text(
        'from livery.core.answer import answer\n\n__all__ = ["answer"]\n'
        '__version__ = "0.2.0"\n'
    )
    packages = {p.directory.name: p for p in discover_packages(root)}
    plan = MemberPlan(packages["core"], "0.2.0")
    from livery.workshop._release_driver import validate_member

    validate_member(root, plan, (member / "dist",))
    assert list((member / "dist").glob("*.whl"))


def test_an_unresolvable_floor_fails_the_floor_leg_by_name(
    workspace: tuple[FakeForge, GitOps, Path],
) -> None:
    # A solo release whose declared floor names a version no index
    # serves: the floor leg's install refuses, and the refusal is the
    # release's answer, never a silent pass against the tree.
    from livery.workshop._backends import _python

    _fake, _git_seam, root = workspace
    member = root / "packages" / "tool"
    (member / "pyproject.toml").write_text(
        '[project]\nname = "livery-tool"\nversion = "0.2.0"\n'
        'requires-python = ">=3.11"\n'
        'dependencies = ["livery-core>=9.9.9"]\n'
        "[build-system]\n"
        'requires = ["uv_build>=0.7"]\nbuild-backend = "uv_build"\n'
        "[tool.uv.build-backend]\n"
        'module-name = "livery.tool"\nnamespace = true\n'
    )
    packages = {p.directory.name: p for p in discover_packages(root)}
    _python.build(packages["tool"], root)
    with pytest.raises(_FAILURES) as caught:
        _python.run_isolated_test(packages["tool"], root, resolution="lowest-direct")
    assert "isolated install" in str(caught.value)
