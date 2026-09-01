"""The update wave's flow, the arming ladder, and the package generator."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from footman import Failed

from livery.forge.testing import FakeForge
from livery.workshop._git_ops import GitOps
from livery.workshop._submit import arming_reason, ci_automerge
from livery.workshop._templates import new_package
from livery.workshop._update import _align_answers_source
from livery.workshop._update_driver import UpdateDriver, wait_for_releases
from livery.workshop._workflow_engine import run_workflow

_FAILURES = (SystemExit, Failed)

ROOT = Path(__file__).resolve().parents[3]


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True)


def _instance(tmp_path: Path) -> Path:
    """A clean clone of a bare origin, current with its own render."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "--initial-branch=main")
    root = tmp_path / "ws"
    _git(tmp_path, "clone", str(origin), "ws")
    _git(root, "config", "user.email", "t@livery.local")
    _git(root, "config", "user.name", "T")
    import shutil

    shutil.copytree(ROOT / "templates", root / "templates")
    shutil.copy(ROOT / ".copier-answers.yml", root / ".copier-answers.yml")
    # The renderer reads the contract for its source, so the seed line
    # comes first; the render then overwrites the file completely.
    (root / "livery.toml").write_text('[workspace]\ntemplates = "templates"\n')
    from livery.workshop._sync import sync_workspace
    from livery.workshop._templates import apply_project

    apply_project(root)
    (root / "packages").mkdir(exist_ok=True)
    # The first sync seeds CLAUDE.project.md; committing it makes the
    # instance current, the state the no-op assertions need.
    sync_workspace(root)
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "chore: seed")
    _git(root, "push", "-u", "origin", "main")
    return root


class _UpdateGit(GitOps):
    """The engine rig's git: a push reaches the fake and settles."""

    def __init__(self, root: Path, fake: FakeForge) -> None:
        super().__init__(root)
        self.fake = fake

    def push(self, branch: str) -> None:
        super().push(branch)
        sha = self.fake.push("willemkokke", "livery", branch, sha=self.head_sha())
        self.fake.settle("willemkokke", "livery", sha)


def _fake_pair(root: Path) -> tuple[FakeForge, _UpdateGit]:
    fake = FakeForge()
    fake.create_repo("willemkokke", "livery", private=True, description="t")
    return fake, _UpdateGit(root, fake)


def test_a_current_instance_updates_to_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _instance(tmp_path)
    fake, git = _fake_pair(root)
    driver = UpdateDriver(root, git, "templates", armed=False)
    run_workflow(
        driver, fake.repository("willemkokke", "livery"), git, current_user="fake-user"
    )
    out = capsys.readouterr().out
    assert "nothing to submit" in out
    assert git.current_branch() == "main"  # the empty branch was tidied


def test_the_driver_refuses_a_feature_branch_and_a_dirty_tree(
    tmp_path: Path,
) -> None:
    root = _instance(tmp_path)
    fake, git = _fake_pair(root)
    git.create_branch("feat/elsewhere")
    driver = UpdateDriver(root, git, "templates", armed=False)
    with pytest.raises(_FAILURES) as caught:
        run_workflow(
            driver,
            fake.repository("willemkokke", "livery"),
            git,
            current_user="fake-user",
        )
    assert "workflow/update/templates" in str(caught.value)
    _git(root, "checkout", "main")
    (root / "stray.txt").write_text("dirty\n")
    with pytest.raises(_FAILURES) as caught:
        run_workflow(
            driver,
            fake.repository("willemkokke", "livery"),
            git,
            current_user="fake-user",
        )
    assert "uncommitted" in str(caught.value)


def test_a_changed_instance_submits_through_the_engine(
    tmp_path: Path,
) -> None:
    root = _instance(tmp_path)
    # Drift one rendered file; the update re-renders and submits.
    (root / "livery.toml").write_text('[workspace]\ntemplates = "templates"\n')
    _git(root, "commit", "-am", "chore: drift")
    _git(root, "push", "origin", "main")
    fake, git = _fake_pair(root)
    driver = UpdateDriver(root, git, "templates", armed=True)
    run_workflow(
        driver, fake.repository("willemkokke", "livery"), git, current_user="fake-user"
    )
    pr = fake.repository("willemkokke", "livery").pr.get(1)
    assert pr is not None and pr.merged
    assert pr.title == "chore: update templates"


def test_a_killed_update_resumes_without_redoing_the_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _instance(tmp_path)
    (root / "livery.toml").write_text('[workspace]\ntemplates = "templates"\n')
    _git(root, "commit", "-am", "chore: drift")
    _git(root, "push", "origin", "main")
    fake, git = _fake_pair(root)
    # The kill: prepare commits, then the process dies before submit.
    dead = UpdateDriver(root, git, "templates", armed=False)
    submission = dead.prepare()
    assert submission is not None  # committed, never submitted
    # The re-run resumes: the work function must not run again.
    reworked: list[str] = []

    def _tracked_work(self: UpdateDriver) -> list[str]:
        reworked.append("ran")
        return []

    monkeypatch.setattr(UpdateDriver, "_work", _tracked_work)
    again = UpdateDriver(root, git, "templates", armed=False)
    run_workflow(
        again, fake.repository("willemkokke", "livery"), git, current_user="fake-user"
    )
    assert reworked == []
    assert "resuming the committed update" in capsys.readouterr().out
    assert fake.repository("willemkokke", "livery").pr.get(1) is not None


def test_foreign_commits_on_the_update_branch_stop_with_options(
    tmp_path: Path,
) -> None:
    root = _instance(tmp_path)
    _fake, git = _fake_pair(root)
    git.create_branch("workflow/update/templates")
    (root / "hand.txt").write_text("hand-made\n")
    git.commit_all("feat: someone's own work")
    driver = UpdateDriver(root, git, "templates", armed=False)
    with pytest.raises(_FAILURES) as caught:
        driver.prepare()
    message = str(caught.value)
    assert "someone's own work" in message
    assert "fm submit" in message and "workflow.abort" in message


def test_named_dependencies_upgrade_exactly_those(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _instance(tmp_path)
    _fake, git = _fake_pair(root)
    invoked: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        "livery.workshop._update_driver.run_uv",
        lambda *args, root: invoked.append(args),
    )
    driver = UpdateDriver(root, git, "dependencies", armed=False, names=("requests",))
    git.create_branch(driver.branch)
    driver._work()
    assert ("lock", "--upgrade-package", "requests") in invoked
    assert ("lock", "--upgrade") not in invoked
    assert ("sync",) in invoked


def test_the_parked_update_waits_and_the_bounded_wait_parks(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _instance(tmp_path)
    fake, git = _fake_pair(root)
    repo = fake.repository("willemkokke", "livery")
    # A live release: branch on the fake, PR open and armed, pending.
    git.create_branch("workflow/release/forge")
    (root / "r.txt").write_text("r\n")
    git.commit_all("feat: r")
    subprocess.run(
        ["git", "push", "origin", "workflow/release/forge"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    fake.push("willemkokke", "livery", "workflow/release/forge")
    repo.pr.open("workflow/release/forge", "main", "feat: r")
    git.switch("main")
    # Non-interactive bounded wait: parks with the teaching, True never.
    cleared = wait_for_releases(repo, git, interactive=False, timeout=0.05, poll=0.01)
    out = capsys.readouterr().out
    assert cleared is False
    assert "finished automatically" in out and "Ctrl-C is safe" in out
    # The release completes (merge it); the wait clears.
    live = repo.pr.get(1)
    assert live is not None
    fake.settle("willemkokke", "livery", live.head_sha)
    repo.pr.merge_now(1, title="feat: r")
    fake.create_tag("willemkokke", "livery", "packages/forge/v9.9.9")
    assert wait_for_releases(repo, git, interactive=False, timeout=1, poll=0.01)


def test_align_answers_source_follows_the_contract(tmp_path: Path) -> None:
    answers = tmp_path / ".copier-answers.yml"
    answers.write_text("_src_path: templates\nkind: project\n")
    notes = _align_answers_source(tmp_path, "https://example.com/fork")
    assert notes and "follows the contract" in notes[0]
    assert "_src_path: https://example.com/fork" in answers.read_text()
    assert _align_answers_source(tmp_path, "https://example.com/fork") == []
    assert _align_answers_source(tmp_path / "absent", "x") == []


def test_the_arming_ladder_names_its_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LIVERY_AUTOMERGE", raising=False)
    assert "this invocation" in arming_reason(armed=True, flag_given=True)
    assert "--no-armed" in arming_reason(armed=False, flag_given=True)
    assert "nothing configured" in arming_reason(armed=False, flag_given=False)
    (tmp_path / "livery.toml").write_text("[ci]\nautomerge = true\n")
    assert "committed repo policy" in arming_reason(armed=True, flag_given=False)
    assert ci_automerge() is True
    monkeypatch.setenv("LIVERY_AUTOMERGE", "1")
    assert "standing preference" in arming_reason(armed=True, flag_given=False)


def test_new_package_renders_and_wires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _instance(tmp_path)
    monkeypatch.chdir(root)

    real_run = subprocess.run

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        if cmd[0] == "uv":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr("livery.workshop._uv.subprocess.run", fake_run)
    with pytest.raises(_FAILURES):
        new_package("Bad Name")
    new_package("scratch")
    assert (root / "packages" / "scratch" / "livery.toml").is_file()
    assert "livery-scratch" in (root / "pyproject.toml").read_text()
    assert "dir: scratch" in (root / ".copier-answers.yml").read_text()
    with pytest.raises(_FAILURES):
        new_package("scratch")  # already exists
