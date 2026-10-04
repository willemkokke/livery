"""A release pull request merges only while its base has not moved under its set."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from livery.footman.api import Failed
from livery.forge.api import Repository
from livery.forge.testing import FakeForge
from livery.workshop._git_ops import GitOps
from livery.workshop._packages import discover_packages
from livery.workshop._publish import MANIFEST
from livery.workshop._release_driver import (
    REDERIVE_LIMIT,
    MemberPlan,
    ReleaseDriver,
    rollback_prepare,
    run_release,
    workflow_release_check_fresh,
)
from livery.workshop._workflow_state import base_moved_in_paths
from workshop_seeds import member

_FAILURES = (SystemExit, Failed)
OWNER, NAME = "acme", "ws"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def _manifest(mined: str, *members: tuple[str, str]) -> str:
    payload: dict[str, object] = {
        "schema": 1,
        "members": [
            {"dir": name, "name": f"livery-{name}", "version": version}
            for name, version in members
        ],
    }
    if mined:
        payload["mined-at"] = mined
    return json.dumps(payload) + "\n"


def _rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, str]:
    """A release branch mined at main, and a second clone that moves main."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "--initial-branch=main")
    root = tmp_path / "ws"
    _git(tmp_path, "clone", str(origin), "ws")
    _git(root, "config", "user.email", "t@livery.local")
    _git(root, "config", "user.name", "T")
    (root / "workshop.toml").write_text("[workspace]\n")
    member(root, "core")
    member(root, "tool")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "chore: seed")
    _git(root, "push", "-u", "origin", "main")
    mined = _git(root, "rev-parse", "HEAD")
    other = tmp_path / "other"
    _git(tmp_path, "clone", str(origin), "other")
    _git(other, "config", "user.email", "t@livery.local")
    _git(other, "config", "user.name", "T")
    _git(root, "checkout", "-b", "workflow/release/core")
    (root / MANIFEST).write_text(_manifest(mined, ("core", "0.3.0")))
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "chore(release): the set manifest")
    monkeypatch.setattr(
        "livery.workshop._extensions.workspace_root", lambda start=None: root
    )
    monkeypatch.chdir(root)
    return root, other, mined


def _move(other: Path, relative: str, subject: str) -> None:
    _git(other, "pull", "--quiet", "origin", "main")
    path = other / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(subject + "\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-m", subject)
    _git(other, "push", "origin", "main")


def test_a_base_moved_under_the_set_refuses_naming_the_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, other, _mined = _rig(tmp_path, monkeypatch)
    _move(other, "packages/core/src/livery/core/new.py", "feat(core): a late feature")
    with pytest.raises(_FAILURES) as caught:
        workflow_release_check_fresh(head="workflow/release/core", base="main")
    message = str(caught.value)
    assert "feat(core): a late feature" in message
    assert "workflow.release core" in message


def test_off_a_release_branch_the_freshness_check_is_green(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _root, other, _mined = _rig(tmp_path, monkeypatch)
    _move(other, "packages/core/x.txt", "feat(core): moved")
    workflow_release_check_fresh(head="feat/12-something", base="main")
    assert "not a release branch" in capsys.readouterr().out


def test_a_manifest_without_a_mining_point_is_green(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, other, _mined = _rig(tmp_path, monkeypatch)
    (root / MANIFEST).write_text(_manifest("", ("core", "0.3.0")))
    _git(root, "commit", "-am", "chore(release): an older manifest")
    _move(other, "packages/core/x.txt", "feat(core): moved")
    workflow_release_check_fresh(head="workflow/release/core", base="main")
    assert "records no mining point" in capsys.readouterr().out


def test_a_base_moved_elsewhere_stays_fresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _root, other, _mined = _rig(tmp_path, monkeypatch)
    _move(other, "packages/tool/x.txt", "feat(tool): elsewhere")
    workflow_release_check_fresh(head="workflow/release/core", base="main")
    assert "fresh:" in capsys.readouterr().out


def test_a_release_tip_this_clone_lacks_reads_unmoved(tmp_path: Path) -> None:
    origin = tmp_path / "o.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "--initial-branch=main")
    _git(tmp_path, "clone", str(origin), "c")
    clone = tmp_path / "c"
    _git(
        clone,
        "-c",
        "user.email=t@x",
        "-c",
        "user.name=T",
        "commit",
        "--allow-empty",
        "-m",
        "seed",
    )
    _git(clone, "push", "origin", "main")
    _git(clone, "fetch")
    assert not base_moved_in_paths(
        GitOps(clone), "main", ("packages/core",), head="0" * 40
    )


def test_a_failed_commits_staged_stamp_and_manifest_roll_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _other, mined = _rig(tmp_path, monkeypatch)
    changelog = root / "packages" / "core" / "CHANGELOG.md"
    before = changelog.read_text()
    changelog.write_text(before + "\n## 0.3.0\n")
    (root / MANIFEST).write_text(_manifest(mined, ("core", "0.4.0")))
    # What a commit a signing agent refused leaves: everything staged.
    _git(root, "add", "-A")
    core = next(p for p in discover_packages(root) if p.directory.name == "core")
    rollback_prepare(root, (core,))
    assert _git(root, "status", "--porcelain") == ""
    assert changelog.read_text() == before


def test_a_first_releases_manifest_goes_on_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _other, mined = _rig(tmp_path, monkeypatch)
    _git(root, "checkout", "main")
    (root / MANIFEST).write_text(_manifest(mined, ("core", "0.3.0")))
    _git(root, "add", "-A")
    core = next(p for p in discover_packages(root) if p.directory.name == "core")
    rollback_prepare(root, (core,))
    assert not (root / MANIFEST).exists()
    assert _git(root, "status", "--porcelain") == ""


def _core(root: Path) -> MemberPlan:
    core = next(p for p in discover_packages(root) if p.directory.name == "core")
    return MemberPlan(core, "0.3.0")


def _driver(root: Path) -> ReleaseDriver:
    fake = FakeForge()
    fake.create_repo(OWNER, NAME, private=True, description="t")
    return ReleaseDriver(
        root,
        fake.repository(OWNER, NAME),
        GitOps(root),
        (_core(root).package,),
        armed=True,
    )


def test_discard_keeps_the_given_up_manifest_and_drops_the_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _other, _mined = _rig(tmp_path, monkeypatch)
    _git(root, "push", "origin", "workflow/release/core")
    driver = _driver(root)
    driver.discard()
    assert _git(root, "branch", "--show-current") == "main"
    assert "workflow/release/core" not in _git(root, "branch", "--list")
    plan = _core(root)
    head = _git(root, "rev-parse", "HEAD")
    assert driver.legs_kept((plan,), head) == {"core"}
    # A new version anywhere in the set moves the floors: every leg runs.
    moved = MemberPlan(plan.package, "0.4.0")
    assert driver.legs_kept((moved,), head) == set()


def test_a_member_the_base_moved_runs_its_legs_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, other, _mined = _rig(tmp_path, monkeypatch)
    _git(root, "push", "origin", "workflow/release/core")
    _move(other, "packages/core/x.txt", "feat(core): moved")
    driver = _driver(root)
    driver.discard()
    _git(root, "pull", "--quiet", "origin", "main")
    head = _git(root, "rev-parse", "HEAD")
    assert driver.legs_kept((_core(root),), head) == set()


def test_without_a_given_up_branch_no_leg_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _other, _mined = _rig(tmp_path, monkeypatch)
    head = _git(root, "rev-parse", "HEAD")
    assert _driver(root).legs_kept((_core(root),), head) == set()


class _Driver:
    name = "release/core"
    branch = "workflow/release/core"


def _release(*, armed: bool) -> None:
    run_release(
        cast("Callable[[], ReleaseDriver]", _Driver),
        cast("Repository", None),
        cast("GitOps", None),
        armed=armed,
    )


def _counting_release(
    monkeypatch: pytest.MonkeyPatch, *, moved: bool, fails: int
) -> list[int]:
    runs: list[int] = []

    def run_workflow(driver: object, repo: object, git: object) -> None:
        runs.append(1)
        if len(runs) <= fails:
            raise SystemExit(13)

    monkeypatch.setattr("livery.workshop._release_driver.run_workflow", run_workflow)
    monkeypatch.setattr(
        "livery.workshop._release_driver.set_moved", lambda repo, git, name: moved
    )
    return runs


def test_a_red_that_is_not_a_moved_set_is_the_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runs = _counting_release(monkeypatch, moved=False, fails=1)
    with pytest.raises(SystemExit):
        _release(armed=True)
    assert len(runs) == 1


def test_an_unarmed_release_never_re_derives_by_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runs = _counting_release(monkeypatch, moved=True, fails=1)
    with pytest.raises(SystemExit):
        _release(armed=False)
    assert len(runs) == 1


def test_a_base_that_keeps_moving_stops_at_the_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runs = _counting_release(monkeypatch, moved=True, fails=REDERIVE_LIMIT)
    with pytest.raises(SystemExit):
        _release(armed=True)
    assert len(runs) == REDERIVE_LIMIT


def test_an_armed_release_re_derives_a_moved_set_and_lands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runs = _counting_release(monkeypatch, moved=True, fails=1)
    _release(armed=True)
    assert len(runs) == 2
