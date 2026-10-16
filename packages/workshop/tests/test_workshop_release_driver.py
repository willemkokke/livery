"""The release driver's edge table, forced: refusals, rollback, recovery.

The heavy validation (uv build plus the isolated venv legs) runs for
real on a minimal package, the floor and latest legs once and the head
leg once at a lock's pins; every flow test monkeypatches it, the
flows' own edges are what they force.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from livery import git
from livery.footman import Failed
from livery.forge.testing import FakeForge
from livery.workshop._graph import order_topologically
from livery.workshop._packages import Package, discover_packages
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
from workshop_installed import skip_unless_installed
from workshop_seeds import (  # noqa: F401
    FakeNotes,
    Seeds,
    _seed_home,
    fake_notes,
    member,
    seed_copier,
    wire_pushes,
)

ROOT = Path(__file__).resolve().parents[3]
_FAILURES = (SystemExit, Failed)

OWNER, NAME = "willemkokke", "livery"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True)


def _seed(base: Path) -> None:
    """Two members, tagged and pushed, built once per session."""
    origin = base / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "--initial-branch=main")
    root = base / "ws"
    _git(base, "clone", str(origin), "ws")
    (root / "workshop.toml").write_text("[workspace]\n")
    member(root, "core")
    member(root, "tool", floor_on="core")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "chore: seed")
    _git(root, "tag", "packages/core/v0.2.0")
    _git(root, "tag", "packages/tool/v0.2.0")
    _git(root, "push", "-u", "origin", "main")


@pytest.fixture
def workspace(seeds: Seeds, tmp_path: Path, notes: FakeNotes) -> tuple[FakeForge, Path]:
    # The stand-in provider writes each member's entry, as a listed
    # release-notes extension would.
    del notes
    root = seeds("release-driver", _seed) / "ws"
    fake = FakeForge()
    fake.create_repo(OWNER, NAME, private=True, description="t")
    return fake, root


def _grow(root: Path, name: str, message: str) -> None:
    marker = root / "packages" / name / "src" / "livery" / name / "grown.py"
    marker.write_text("x = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", message)


def test_an_unchanged_member_refuses_before_the_branch_exists(
    workspace: tuple[FakeForge, Path],
) -> None:
    _fake, root = workspace
    members = resolve_set(root, ("core", "tool"))
    _grow(root, "core", "feat: core grows (#1)")
    with pytest.raises(_FAILURES) as caught:
        derive_plans(root, members)
    message = str(caught.value)
    assert "tool" in message and "drop them from the set" in message
    # Nothing to undo, structurally: no workflow branch was ever cut.
    assert git.local_branches(root, "workflow/") == ()


def test_resolve_set_teaches_the_members(workspace: tuple[FakeForge, Path]) -> None:
    _fake, root = workspace
    with pytest.raises(_FAILURES) as caught:
        resolve_set(root, ("ghost",))
    assert "packages/core" in str(caught.value)
    # Bare names and full paths both resolve, dependency order held.
    ordered = resolve_set(root, ("tool", "packages/core"))
    assert [p.directory.name for p in ordered] == ["core", "tool"]


def test_order_topologically_puts_dependencies_first(
    workspace: tuple[FakeForge, Path],
) -> None:
    _fake, root = workspace
    packages = {p.directory.name: p for p in discover_packages(root)}
    ordered = order_topologically((packages["tool"], packages["core"]))
    assert [p.directory.name for p in ordered] == ["core", "tool"]


def test_floors_rise_within_the_set_in_both_homes(
    workspace: tuple[FakeForge, Path],
) -> None:
    _fake, root = workspace
    packages = {p.directory.name: p for p in discover_packages(root)}
    plans = (
        MemberPlan(packages["core"], "0.3.0"),
        MemberPlan(packages["tool"], "0.3.0"),
    )
    changed = bump_set_floors(root, plans)
    tool = root / "packages" / "tool"
    assert '"livery-core>=0.3.0"' in (tool / "pyproject.toml").read_text()
    assert 'floor = "0.3.0"' in (tool / "workshop.toml").read_text()
    assert sorted(changed) == [
        "packages/tool/pyproject.toml",
        "packages/tool/workshop.toml",
    ]


def test_rollback_restores_exactly_what_prepare_writes(
    workspace: tuple[FakeForge, Path],
) -> None:
    skip_unless_installed("python")
    _fake, root = workspace
    members = resolve_set(root, ("core", "tool"))
    before = {
        path: path.read_text()
        for member in members
        for path in [
            member.directory / "CHANGELOG.md",
            member.directory / "pyproject.toml",
            member.directory / "workshop.toml",
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
    assert git.is_clean(root)


def test_rollback_restores_the_version_in_a_roots_init(
    workspace: tuple[FakeForge, Path],
) -> None:
    skip_unless_installed("python")
    # A root keeps __version__ in its __init__.py; the rollback restores
    # what the stamper wrote there.
    _fake, root = workspace
    init = root / "packages" / "core" / "src" / "livery" / "core" / "__init__.py"
    members = resolve_set(root, ("core",))
    from livery.workshop._release import prepare_release

    prepare_release(root, "packages/core", "9.9.9")
    assert '__version__ = "9.9.9"' in init.read_text()
    rollback_prepare(root, members)
    assert '__version__ = "0.2.0"' in init.read_text()
    assert git.is_clean(root)


def test_the_legs_follow_release_prove_package_over_workspace(
    workspace: tuple[FakeForge, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from livery.workshop._release_driver import proved_resolutions, validate_member

    skip_unless_installed("python")
    _fake, root = workspace
    core = resolve_set(root, ("core",))[0]
    ran: list[str] = []

    def _leg(
        package: object, root: Path, *, release_dirs: object, resolution: str
    ) -> dict[str, str]:
        ran.append(resolution)
        return {}

    monkeypatch.setattr("livery.extensions.python._backend.run_isolated_test", _leg)
    # The fallback first: neither contract lists any, so the floor and
    # the latest legs run, and no file is named.
    assert proved_resolutions(root, core) == (("floor", "latest"), "")
    validate_member(root, MemberPlan(core, "0.3.0"), ())
    assert ran == ["floor", "latest"]
    assert "[release] prove in" not in capsys.readouterr().out
    contract = core.directory / "workshop.toml"
    package_text = contract.read_text()
    # The workspace lists the lock's resolution for every member.
    (root / "workshop.toml").write_text('[workspace]\n\n[release]\nprove = ["head"]\n')
    assert proved_resolutions(root, core) == (("head",), "workshop.toml")
    ran.clear()
    validate_member(root, MemberPlan(core, "0.3.0"), ())
    assert ran == ["head"]
    assert (
        "  livery-core: [release] prove in workshop.toml lists head\n"
        in capsys.readouterr().out
    )
    # A package's own list wins, and its legs run floor first whatever
    # the order it lists them in.
    contract.write_text(package_text + '\n[release]\nprove = ["head", "floor"]\n')
    assert proved_resolutions(root, core) == (
        ("floor", "head"),
        "packages/core/workshop.toml",
    )
    ran.clear()
    validate_member(root, MemberPlan(core, "0.3.0"), ())
    assert ran == ["floor", "head"]
    # An empty list runs no leg, and says so.
    contract.write_text(package_text + "\n[release]\nprove = []\n")
    ran.clear()
    validate_member(root, MemberPlan(core, "0.3.0"), ())
    assert ran == []
    assert (
        "  livery-core: [release] prove in packages/core/workshop.toml lists"
        " nothing, so no leg runs\n" in capsys.readouterr().out
    )


def test_validate_member_skips_a_member_with_no_isolated_test(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop._release_driver import validate_member

    # A C++ package: cpp adds no isolated test.
    directory = tmp_path / "packages" / "lib"
    directory.mkdir(parents=True)
    package = Package(
        directory=directory,
        path="packages/lib",
        name="acme-lib",
        depends=(),
        extensions=("cpp",),
    )
    validate_member(tmp_path, MemberPlan(package=package, version="0.1.0"), ())
    out = capsys.readouterr().out
    assert "isolated legs skip (its extensions add no isolated test" in out


def test_the_base_gate_refuses_red_and_teaches(
    workspace: tuple[FakeForge, Path],
) -> None:
    fake, root = workspace
    sha = git.remote_head(root, "main")
    fake.push(OWNER, NAME, "main", outcome="failure", sha=sha)
    fake.settle(OWNER, NAME, sha)
    repo = fake.repository(OWNER, NAME)
    with pytest.raises(_FAILURES) as caught:
        require_verified_base(repo, root, "main")
    message = str(caught.value)
    assert "red" in message and "force-unverified-base" in message
    # And force is the taught override, skipping the wait entirely.
    require_verified_base(repo, root, "main", force=True)


def test_the_base_gate_fails_open_when_the_forge_is_unreachable(
    workspace: tuple[FakeForge, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, root = workspace
    repo = fake.repository(OWNER, NAME)

    def _down(sha: str):
        raise RuntimeError("down")

    monkeypatch.setattr(repo.checks, "status", _down)
    require_verified_base(repo, root, "main")  # returns; the engine's story


def test_local_release_reports_builds_and_restores(
    workspace: tuple[FakeForge, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    skip_unless_installed("python")
    _fake, root = workspace
    _grow(root, "core", "feat: core grows (#1)")
    _grow(root, "tool", "feat: tool grows (#2)")
    validated: list[str] = []
    monkeypatch.setattr(
        "livery.workshop._release_driver.validate_member",
        lambda _root, plan, dirs: validated.append(plan.package.name),
    )
    monkeypatch.setattr(
        "livery.workshop._lifecycle.build_release", lambda package, root, **kw: None
    )
    members = resolve_set(root, ("core", "tool"))
    local_release(root, members)
    out = capsys.readouterr().out
    assert "would release: livery-core v0.3.0, livery-tool v0.3.0" in out
    assert validated == ["livery-core", "livery-tool"]
    assert git.is_clean(root)  # the stamps rolled back


def test_a_local_rehearsal_reads_the_receipts_a_wave_cut_since_the_last_fetch(
    workspace: tuple[FakeForge, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    skip_unless_installed("python")
    # The fallback first: an origin this checkout cannot reach leaves
    # the derivation to its own tags, and says so.
    _fake, root = workspace
    _grow(root, "core", "feat: core grows (#1)")
    _git(root, "push", "origin", "main")
    monkeypatch.setattr(
        "livery.workshop._release_driver.validate_member",
        lambda _root, plan, dirs: None,
    )
    monkeypatch.setattr(
        "livery.workshop._lifecycle.build_release", lambda package, root, **kw: None
    )
    origin = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    _git(root, "remote", "set-url", "origin", str(root.parent / "gone.git"))
    local_release(root, resolve_set(root, ("core",)))
    out = capsys.readouterr().out
    assert "origin could not be fetched" in out
    assert "would release: livery-core v0.3.0" in out
    # A wave elsewhere cuts core's receipt at that commit; this checkout
    # has not fetched it, and the rehearsal reads it all the same.
    _git(root, "remote", "set-url", "origin", origin)
    other = root.parent / "other"
    _git(root.parent, "clone", origin, "other")
    _git(other, "tag", "packages/core/v0.3.0")
    _git(other, "push", "origin", "packages/core/v0.3.0")
    with pytest.raises(_FAILURES, match=r"nothing unreleased touches: core\."):
        local_release(root, resolve_set(root, ("core",)))
    assert git.is_clean(root)


def test_a_failing_member_rolls_the_whole_prepare_back(
    workspace: tuple[FakeForge, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, root = workspace
    _grow(root, "core", "feat: core grows (#1)")
    _grow(root, "tool", "feat: tool grows (#2)")
    _git(root, "push", "origin", "main")
    wire_pushes(monkeypatch, fake, OWNER, NAME)
    sha = git.remote_head(root, "main")
    fake.push(OWNER, NAME, "main", sha=sha)
    fake.settle(OWNER, NAME, sha)

    def _explode(_root: Path, plan: MemberPlan, dirs: tuple[Path, ...]) -> None:
        if plan.package.name == "livery-tool":
            raise SystemExit("tool validation failed")

    monkeypatch.setattr("livery.workshop._release_driver.validate_member", _explode)
    driver = ReleaseDriver(
        root,
        fake.repository(OWNER, NAME),
        resolve_set(root, ("core", "tool")),
        armed=False,
    )
    with pytest.raises(_FAILURES):
        driver.prepare()
    # The base is restored exactly: clean tree, on main, no branch.
    assert git.current_branch(root) == "main"
    assert git.is_clean(root)
    assert not git.local_branch_exists(root, driver.branch)


def test_the_driver_prepares_commits_and_the_engine_lands_it(
    workspace: tuple[FakeForge, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    skip_unless_installed("python")
    fake, root = workspace
    _grow(root, "core", "feat: core grows (#1)")
    _grow(root, "tool", "feat: tool grows (#2)")
    _git(root, "push", "origin", "main")
    wire_pushes(monkeypatch, fake, OWNER, NAME)
    sha = git.remote_head(root, "main")
    fake.push(OWNER, NAME, "main", sha=sha)
    fake.settle(OWNER, NAME, sha)
    monkeypatch.setattr(
        "livery.workshop._release_driver.validate_member",
        lambda _root, plan, dirs: None,
    )
    members = resolve_set(root, ("core", "tool"))
    driver = ReleaseDriver(
        root, fake.repository(OWNER, NAME), members, armed=True, wave_timeout=0
    )
    run_workflow(driver, fake.repository(OWNER, NAME), root, current_user="fake-user")
    pr = fake.repository(OWNER, NAME).pr.get(1)
    assert pr is not None and pr.merged
    assert pr.title == "chore(release): released livery-core v0.3.0, livery-tool v0.3.0"
    assert "- **livery-core** v0.3.0" in pr.body
    # The act returned to main and dropped its local copy of the
    # reserved branch; origin's copy under the pull request holds it.
    assert git.current_branch(root) == "main"
    assert not git.local_branch_exists(root, driver.branch)
    subjects = git.log_paths(root, f"origin/main..origin/{driver.branch}", (".",))
    assert subjects == (
        "chore(release): livery-tool v0.3.0",
        "chore(release): livery-core v0.3.0",
    )
    tool_pyproject = git.file_at(
        root, f"origin/{driver.branch}", "packages/tool/pyproject.toml"
    )
    assert '"livery-core>=0.3.0"' in tool_pyproject


def test_an_armed_release_follows_its_pull_request_and_then_the_wave(
    workspace: tuple[FakeForge, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """From main: the pull request to its squash, then the wave to its verdict."""
    from types import SimpleNamespace

    from livery.workshop import _release_driver, _workflow_engine

    skip_unless_installed("python")
    fake, root = workspace
    _grow(root, "core", "feat: core grows (#1)")
    _git(root, "push", "origin", "main")
    wire_pushes(monkeypatch, fake, OWNER, NAME)
    sha = git.remote_head(root, "main")
    fake.push(OWNER, NAME, "main", sha=sha)
    fake.settle(OWNER, NAME, sha)
    monkeypatch.setattr(
        "livery.workshop._release_driver.validate_member",
        lambda _root, plan, dirs: None,
    )
    merged = iter([False, True])
    monkeypatch.setattr(
        _workflow_engine, "_merged", lambda repo, git, branch: next(merged)
    )
    followed: list[tuple[str, float]] = []

    def following(repo: object, branch: str, git: object, **kw: float) -> None:
        followed.append((branch, kw["timeout"]))

    monkeypatch.setattr("livery.workshop._verdict.follow", following)
    run = SimpleNamespace(id=7, status="completed", url="https://forge.test/run/7")
    monkeypatch.setattr(
        _release_driver, "await_wave", lambda repo, *, before, timeout, transient: 7
    )
    monkeypatch.setattr(_release_driver, "_wave_runs", lambda repo: (run,))
    watched: list[tuple[str, int]] = []

    def watching(repo: object, point: str, seen: object, **kw: float) -> int:
        watched.append((point, seen.id))  # type: ignore[attr-defined]
        return 0

    monkeypatch.setattr("livery.workshop._ci_tasks.follow_run", watching)
    members = resolve_set(root, ("core",))
    driver = ReleaseDriver(
        root, fake.repository(OWNER, NAME), members, armed=True, wave_timeout=90
    )
    run_workflow(driver, fake.repository(OWNER, NAME), root, current_user="fake-user")
    assert followed == [(driver.branch, 90.0)]
    assert watched == [("release", 7)]
    assert git.current_branch(root) == "main"
    # A red wave ends the act with the wave's exit, and everything the
    # release caused joins its own trace before that: a red wave's
    # timeline is the one most worth having. The walk starts at the
    # release branch's commit, whose merge commit it hops to, never at
    # whatever main's tip happens to be.
    chained: list[str] = []

    def chaining(repo: object, git: object, *, commit: str = "", depth: int = 2) -> str:
        chained.append(commit)
        return "profile: the chain joins this trace"

    monkeypatch.setattr("livery.workshop._traces.drop_chain", chaining)
    monkeypatch.setattr("livery.workshop._ci_tasks.follow_run", lambda *a, **k: 13)
    with pytest.raises(SystemExit) as caught:
        driver.on_merged()
    assert caught.value.code == 13
    assert chained == [git.sha_of(root, driver.branch)]
    assert chained[0] != git.head_sha(root)


def test_a_release_already_stamped_on_the_base_reprepares_cleanly(
    workspace: tuple[FakeForge, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    skip_unless_installed("python")
    # A release squash-merged but never published leaves main carrying
    # the stamped versions and entries; re-preparing is the recovery,
    # and a member whose stamp produces no diff is that state, not a
    # fault ("nothing to commit" once failed the whole re-run here).
    fake, root = workspace
    _grow(root, "core", "feat: core grows (#1)")
    _git(root, "push", "origin", "main")
    wire_pushes(monkeypatch, fake, OWNER, NAME)
    sha = git.remote_head(root, "main")
    fake.push(OWNER, NAME, "main", sha=sha)
    fake.settle(OWNER, NAME, sha)
    monkeypatch.setattr(
        "livery.workshop._release_driver.validate_member",
        lambda _root, plan, dirs: None,
    )
    members = resolve_set(root, ("core",))
    driver = ReleaseDriver(
        root, fake.repository(OWNER, NAME), members, armed=True, wave_timeout=0
    )
    run_workflow(driver, fake.repository(OWNER, NAME), root, current_user="fake-user")
    # The squash landed the stamps on main; the publish never ran, so
    # a second armed run re-prepares the same release. The merged
    # branch is gone both sides (the forge deletes on merge, and the
    # taught recovery is fm workflow.abort locally).
    assert git.current_branch(root) == "main"
    _git(root, "pull", "origin", "main")
    _git(root, "push", "origin", "--delete", "workflow/release/core")
    fake.repository(OWNER, NAME).delete_branch("workflow/release/core")
    sha = git.remote_head(root, "main")
    fake.settle(OWNER, NAME, sha)
    members = resolve_set(root, ("core",))
    driver = ReleaseDriver(
        root, fake.repository(OWNER, NAME), members, armed=True, wave_timeout=0
    )
    run_workflow(driver, fake.repository(OWNER, NAME), root, current_user="fake-user")
    pr = fake.repository(OWNER, NAME).pr.get(2)
    assert pr is not None
    assert "livery-core v0.3.0" in pr.title
    # The re-prepared branch touches no changelog (the stamps were on
    # the base), so the manifest is what names the set to the squash's
    # discovery.
    from livery.workshop._publish import MANIFEST, read_manifest

    recorded = read_manifest(git.file_at(root, f"origin/{driver.branch}", MANIFEST))
    assert recorded == (("core", "0.3.0"),)
    # The same members at the same versions, and still a commit: the
    # manifest records the point this prepare mined at, so the branch
    # touches it (a forge refuses a pull request with no commits) and
    # the dispatch finds this squash, never the abandoned one, as the
    # last commit touching it.
    import json

    branch_manifest = json.loads(git.file_at(root, f"origin/{driver.branch}", MANIFEST))
    assert branch_manifest["mined-at"] == sha
    touched = subprocess.run(
        ["git", "log", "--format=%H", f"{sha}..origin/{driver.branch}", "--", MANIFEST],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert touched


def test_recovery_reads_the_branch_and_rebuilds_nothing(
    workspace: tuple[FakeForge, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    skip_unless_installed("python")
    fake, root = workspace
    _grow(root, "core", "feat: core grows (#1)")
    _grow(root, "tool", "feat: tool grows (#2)")
    _git(root, "push", "origin", "main")
    wire_pushes(monkeypatch, fake, OWNER, NAME)
    sha = git.remote_head(root, "main")
    fake.push(OWNER, NAME, "main", sha=sha)
    fake.settle(OWNER, NAME, sha)
    monkeypatch.setattr(
        "livery.workshop._release_driver.validate_member",
        lambda _root, plan, dirs: None,
    )
    members = resolve_set(root, ("core", "tool"))
    driver = ReleaseDriver(root, fake.repository(OWNER, NAME), members, armed=False)
    first = driver.prepare()
    assert first is not None
    rebuilt: list[str] = []
    monkeypatch.setattr(
        "livery.workshop._release_driver.derive_plans",
        lambda _root, _members: rebuilt.append("derived"),
    )
    again = ReleaseDriver(root, fake.repository(OWNER, NAME), members, armed=False)
    recovered = again.prepare()
    assert recovered is not None
    assert recovered.title == first.title  # from the ref, not the tree
    assert rebuilt == []  # recovery derives nothing


def test_recovery_survives_a_rider_and_a_hand_edited_entry(
    workspace: tuple[FakeForge, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    skip_unless_installed("python")
    # Finding 12's scenario, on the fixed shape: the title rebuilds
    # from the branch's changelogs, so a rider commit (even with the
    # reserved prefix) never enters it, and hand-editing an entry on
    # the prepared branch is a supported flow.
    fake, root = workspace
    _grow(root, "core", "feat: core grows (#1)")
    _git(root, "push", "origin", "main")
    wire_pushes(monkeypatch, fake, OWNER, NAME)
    sha = git.remote_head(root, "main")
    fake.push(OWNER, NAME, "main", sha=sha)
    fake.settle(OWNER, NAME, sha)
    monkeypatch.setattr(
        "livery.workshop._release_driver.validate_member",
        lambda _root, plan, dirs: None,
    )
    members = resolve_set(root, ("core",))
    driver = ReleaseDriver(root, fake.repository(OWNER, NAME), members, armed=False)
    first = driver.prepare()
    assert first is not None
    changelog = root / "packages" / "core" / "CHANGELOG.md"
    edited = changelog.read_text().replace("- what changed", "- The 0.3.0 story, told")
    assert edited != changelog.read_text()
    changelog.write_text(edited)
    (root / "uv.lock.marker").write_text("a rider\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "chore(release): the lock records something")
    again = ReleaseDriver(root, fake.repository(OWNER, NAME), members, armed=False)
    recovered = again.prepare()
    assert recovered is not None
    assert recovered.title == first.title
    assert "the lock records" not in recovered.title


def test_recovery_refuses_a_prepared_branch_whose_base_moved(
    workspace: tuple[FakeForge, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    skip_unless_installed("python")
    # Issue #161: reusing prepared commits after main advanced would
    # release code the entries never mined; the recovery refuses and
    # teaches the abandon-and-rerun path instead.
    fake, root = workspace
    _grow(root, "core", "feat: core grows (#1)")
    _git(root, "push", "origin", "main")
    wire_pushes(monkeypatch, fake, OWNER, NAME)
    sha = git.remote_head(root, "main")
    fake.push(OWNER, NAME, "main", sha=sha)
    fake.settle(OWNER, NAME, sha)
    monkeypatch.setattr(
        "livery.workshop._release_driver.validate_member",
        lambda _root, plan, dirs: None,
    )
    members = resolve_set(root, ("core",))
    driver = ReleaseDriver(root, fake.repository(OWNER, NAME), members, armed=False)
    assert driver.prepare() is not None
    _git(root, "checkout", "main")
    moved = root / "packages" / "core" / "src" / "livery" / "core" / "moved.py"
    moved.write_text("y = 2\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "feat: main moves on (#9)")
    _git(root, "push", "origin", "main")
    _git(root, "checkout", driver.branch)
    again = ReleaseDriver(root, fake.repository(OWNER, NAME), members, armed=False)
    with pytest.raises(_FAILURES) as caught:
        again.prepare()
    assert "moved past this prepared release" in str(caught.value)


def test_release_name_sorts_its_members() -> None:
    assert release_name(("workshop", "forge")) == "release/forge+workshop"
    # A long set is named by its size and a digest: short whatever its
    # size, and the same set earns the same name in any order.
    many = tuple(f"extensions/tool-{index}" for index in range(15))
    named = release_name(many)
    assert re.fullmatch(r"release/15-packages-[0-9a-f]{12}", named), named
    assert release_name(tuple(reversed(many))) == named
    assert release_name(many[:-1]) != named


def test_the_base_gate_timeout_names_failed_run_creation(
    workspace: tuple[FakeForge, Path],
) -> None:
    # A tip the forge never mints a run for: not started and hanging
    # are different problems, and this timeout names run creation.
    fake, root = workspace
    repo = fake.repository(OWNER, NAME)
    with pytest.raises(_FAILURES) as caught:
        require_verified_base(repo, root, "main", timeout=0.2, poll=0.01)
    message = str(caught.value)
    assert "never got a CI run" in message
    assert "fresh push event" in message


def test_the_base_gate_timeout_names_the_runners_when_a_run_hangs(
    workspace: tuple[FakeForge, Path],
) -> None:
    # A run exists and never finishes: the reader goes to the
    # runners, not to minting push events.
    fake, root = workspace
    repo = fake.repository(OWNER, NAME)
    git.fetch(root)
    fake.push(OWNER, NAME, "main", sha=git.remote_head(root, "main"))
    with pytest.raises(_FAILURES) as caught:
        require_verified_base(repo, root, "main", timeout=0.2, poll=0.01)
    message = str(caught.value)
    assert "never went green" in message and "runners" in message


def test_derive_plans_judges_by_tags_not_the_stamp(
    workspace: tuple[FakeForge, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The stranded shape: stamped and written, never tagged. The set
    # must still release; only the tag is the receipt.
    _fake, root = workspace
    packages = {p.directory.name: p for p in discover_packages(root)}
    monkeypatch.setattr(
        "livery.workshop._release_driver.derive_version",
        lambda root, package, released="": "0.9.0",
    )
    plans = derive_plans(root, (packages["core"],))
    assert plans and plans[0].version == "0.9.0"


def test_the_driver_builds_the_whole_set_before_any_leg(
    workspace: tuple[FakeForge, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    skip_unless_installed("python")
    # The legs' find-links name each sibling's dist, so a two-member
    # set must build both wheels before either member's legs run.
    fake, root = workspace
    _grow(root, "core", "feat: core grows (#1)")
    _grow(root, "tool", "feat: tool grows (#2)")
    _git(root, "push", "origin", "main")
    wire_pushes(monkeypatch, fake, OWNER, NAME)
    sha = git.remote_head(root, "main")
    fake.push(OWNER, NAME, "main", sha=sha)
    fake.settle(OWNER, NAME, sha)
    events: list[str] = []
    monkeypatch.setattr(
        "livery.workshop._lifecycle.build_release",
        lambda package, root, **kw: events.append(f"build:{package.name}"),
    )

    def _leg(package: Package, root: Path, **kw: object) -> dict[str, str]:
        events.append(f"leg:{package.name}")
        return {}

    monkeypatch.setattr("livery.workshop._lifecycle.isolated_test", _leg)
    driver = ReleaseDriver(
        root,
        fake.repository(OWNER, NAME),
        resolve_set(root, ("core", "tool")),
        armed=False,
    )
    assert driver.prepare() is not None
    first_leg = next(i for i, e in enumerate(events) if e.startswith("leg:"))
    builds_before = {e for e in events[:first_leg] if e.startswith("build:")}
    assert builds_before == {"build:livery-core", "build:livery-tool"}


def test_a_verified_tip_tree_lets_the_release_start_without_waiting(
    workspace: tuple[FakeForge, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _verified

    fake, root = workspace
    sha = git.remote_head(root, "main")
    # The push CI is red, which alone would refuse; the record vouches.
    fake.push(OWNER, NAME, "main", outcome="failure", sha=sha)
    fake.settle(OWNER, NAME, sha)
    repo = fake.repository(OWNER, NAME)
    tree = _verified.tree_id(root, sha)
    asked: list[str] = []

    def _record(root: Path, wanted: str) -> tuple[_verified.Verified | None, str]:
        asked.append(wanted)
        return _verified.Verified(wanted, "77", sha, _verified.FULL, ("check",)), ""

    monkeypatch.setattr("livery.workshop._verified.record", _record)
    require_verified_base(repo, root, "main")  # returns at once
    assert asked == [tree]
    # A narrowed entry does not vouch: the red push CI decides again.
    monkeypatch.setattr(
        "livery.workshop._verified.record",
        lambda root, wanted: (
            _verified.Verified(wanted, "78", sha, _verified.AFFECTED, ("check",)),
            "",
        ),
    )
    with pytest.raises(_FAILURES):
        require_verified_base(repo, root, "main")


def test_a_release_title_names_its_members_until_it_would_pass_the_limit() -> None:
    from livery.workshop._release_driver import TITLE_LIMIT, release_title

    one = release_title([("livery-core", "0.3.0")])
    assert one == "chore(release): released livery-core v0.3.0"
    twelve = [(f"livery-extensions-member-{n}", "0.1.0") for n in range(12)]
    counted = release_title(twelve)
    assert counted == "chore(release): released 12 packages"
    assert len(counted) <= TITLE_LIMIT
    # At the limit the members are named; one character past it, counted.
    name = "x" * (TITLE_LIMIT - len("chore(release): released  v1.0.0"))
    assert release_title([(name, "1.0.0")]).endswith(f"{name} v1.0.0")
    assert release_title([(name + "y", "1.0.0")]) == (
        "chore(release): released 1 package"
    )


def test_the_isolated_legs_run_in_the_listed_order_with_the_sets_dist_directories(
    workspace: tuple[FakeForge, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._release_driver import _wheel_dists, validate_member

    skip_unless_installed("python")
    fake, root = workspace
    packages = {p.directory.name: p for p in discover_packages(root)}
    core, tool = packages["core"], packages["tool"]
    # The edge first: a member whose build writes nothing to dist/ (a
    # conan member's) has no directory for a leg to search, so the
    # set's directories are the ones its builds left, in set order.
    native = Package(
        directory=root / "packages" / "native",
        path="packages/native",
        name="livery-native",
        depends=(),
        extensions=("cmake", "conan"),
    )
    dists = (core.directory / "dist", tool.directory / "dist")
    plans = (
        MemberPlan(core, "0.3.0"),
        MemberPlan(native, "0.1.0"),
        MemberPlan(tool, "0.3.0"),
    )
    assert _wheel_dists(plans) == ()
    for dist in dists:
        dist.mkdir()
    assert _wheel_dists(plans) == dists
    # One member's legs: the floor leg, then the latest leg, each handed
    # the directories as given.
    legs: list[tuple[str, str, tuple[Path, ...]]] = []

    def _leg(
        package: Package, root: Path, *, resolution: str, release_dirs: tuple[Path, ...]
    ) -> dict[str, str]:
        legs.append((package.name, resolution, release_dirs))
        return {}

    monkeypatch.setattr("livery.workshop._lifecycle.isolated_test", _leg)
    validate_member(root, MemberPlan(core, "0.3.0"), dists)
    assert legs == [
        ("livery-core", "floor", dists),
        ("livery-core", "latest", dists),
    ]
    # The driver hands every member's legs the whole set's directories,
    # member after member in set order.
    legs.clear()
    _grow(root, "core", "feat: core grows (#1)")
    _grow(root, "tool", "feat: tool grows (#2)")
    _git(root, "push", "origin", "main")
    wire_pushes(monkeypatch, fake, OWNER, NAME)
    sha = git.remote_head(root, "main")
    fake.push(OWNER, NAME, "main", sha=sha)
    fake.settle(OWNER, NAME, sha)

    def _build(package: Package, root: Path, *, epoch: int = 0) -> Path:
        # A build writes the member's dist/, which a find-links names.
        dist = package.directory / "dist"
        dist.mkdir(exist_ok=True)
        return dist

    for dist in dists:
        dist.rmdir()
    monkeypatch.setattr("livery.workshop._lifecycle.build_release", _build)
    driver = ReleaseDriver(
        root,
        fake.repository(OWNER, NAME),
        resolve_set(root, ("core", "tool")),
        armed=False,
    )
    assert driver.prepare() is not None
    assert legs == [
        ("livery-core", "floor", dists),
        ("livery-core", "latest", dists),
        ("livery-tool", "floor", dists),
        ("livery-tool", "latest", dists),
    ]


def test_each_leg_reports_what_it_resolved_per_co_member_and_sibling(
    workspace: tuple[FakeForge, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from livery.workshop._release_driver import validate_member

    skip_unless_installed("python")
    _fake, root = workspace
    packages = {p.directory.name: p for p in discover_packages(root)}
    core, tool = packages["core"], packages["tool"]
    # The edge first: a leg that resolved no sibling says so.
    monkeypatch.setattr(
        "livery.workshop._lifecycle.isolated_test",
        lambda package, root, **kw: {"livery-core": "0.3.0", "packaging": "25.0"},
    )
    validate_member(root, MemberPlan(core, "0.3.0"), ())
    out = capsys.readouterr().out
    assert "  livery-core floor leg: no siblings\n" in out
    assert "  livery-core latest leg: no siblings\n" in out
    # Each leg names every sibling it resolved, by name and version, and
    # neither the member itself nor a third-party distribution.
    resolved = {
        "livery-core": "0.2.0",
        "livery-tool": "0.3.0",
        "packaging": "25.0",
    }
    monkeypatch.setattr(
        "livery.workshop._lifecycle.isolated_test", lambda package, root, **kw: resolved
    )
    validate_member(root, MemberPlan(tool, "0.3.0"), ())
    out = capsys.readouterr().out
    assert "  livery-tool floor leg: livery-core 0.2.0\n" in out
    assert "  livery-tool latest leg: livery-core 0.2.0\n" in out
    assert out.index("floor leg") < out.index("latest leg")


def test_the_publish_verb_asks_each_member_s_probe_once_through_its_extensions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wave polls one probe per member, the one its extensions answer."""
    from livery.workshop import _lifecycle, _publish, _release_driver

    (tmp_path / "workshop.toml").write_text("[workspace]\n")
    monkeypatch.setattr(
        "livery.workshop._extensions.workspace_root", lambda start=None: tmp_path
    )
    monkeypatch.setenv("PYTHON_REGISTRY_URL", "https://idx.example/simple")
    monkeypatch.setenv("PYTHON_PUBLISH_INDEX", "https://idx.example/")
    monkeypatch.setenv("PYTHON_REGISTRY_TOKEN", "t")
    asked: list[str] = []

    class _Probe:
        def versions(self, name: str) -> tuple[str, ...]:
            return (name,)

    def _answered(package: Package) -> _Probe:
        asked.append(package.path)
        return _Probe()

    monkeypatch.setattr(_lifecycle, "registry", _answered)
    members = [
        Package(
            directory=tmp_path / "packages" / name,
            path=f"packages/{name}",
            name=f"livery-{name}",
            depends=(),
            extensions=("python",),
        )
        for name in ("core", "tool")
    ]
    polled: list[object] = []

    def _wave(
        root: Path,
        registry_for: Callable[[Package], _publish.Registry],
        **_kwargs: object,
    ) -> tuple[()]:
        assert root == tmp_path
        for package in (*members, *members):
            polled.append(registry_for(package))
        return ()

    monkeypatch.setattr(_publish, "publish_release", _wave)
    _release_driver.workflow_release_publish(prebuilt=True)
    assert asked == ["packages/core", "packages/tool"]
    # The second ask of each member polls the probe the first one built.
    assert polled[0] is polled[2] and polled[1] is polled[3]
