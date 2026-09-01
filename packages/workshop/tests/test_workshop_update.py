"""The update wave's flow, the arming ladder, and the package generator."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from footman import Failed

from livery.forge import Repository
from livery.forge.testing import FakeForge
from livery.workshop._git_ops import GitOps
from livery.workshop._submit import arming_reason, ci_automerge
from livery.workshop._templates import new_package
from livery.workshop._update import _align_answers_source
from livery.workshop._update_driver import UpdateDriver, run_gate, wait_for_releases
from livery.workshop._workflow_engine import run_workflow

# Bound before the autouse no-op fixture patches the module attribute,
# so the red-gate test can put the real gate back.
_REAL_GATE = run_gate

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


@pytest.fixture(autouse=True)
def _no_toolchain(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep uv and the gate out of the toy instance; tests override."""
    monkeypatch.setattr(
        "livery.workshop._update_driver.run_uv", lambda *args, root: None
    )
    monkeypatch.setattr("livery.workshop._update_driver.run_gate", lambda root: None)


def _wire_drive(
    monkeypatch: pytest.MonkeyPatch, root: Path, repo: object, git: GitOps
) -> None:
    """Point ``_drive``'s discovery at the toy instance and its fake."""
    monkeypatch.setattr("livery.workshop._layers.workspace_root", lambda: root)
    monkeypatch.setattr(
        "livery.workshop._forge_lane.this_repository", lambda _root: repo
    )
    monkeypatch.setattr("livery.workshop._update_driver.GitOps", lambda _root: git)
    monkeypatch.setattr("livery.workshop._update_driver.BOUNDED_WAIT", 0.05)
    monkeypatch.setattr("livery.workshop._update_driver.WAIT_POLL", 0.01)
    # The fake's PRs are authored by its own user; the identity the
    # engine would look up belongs to the real forge, not this rig.
    monkeypatch.setattr(
        "livery.workshop._workflow_engine._forge_user", lambda _repo: "fake-user"
    )


def _open_release(fake: FakeForge, git: GitOps, root: Path) -> None:
    """A live release on the fake: branch pushed, PR open, unsettled."""
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
    fake.repository("willemkokke", "livery").pr.open(
        "workflow/release/forge", "main", "feat: r"
    )
    git.switch("main")


def _finish_release(fake: FakeForge, root: Path) -> None:
    """The release completes: merged, receipt tagged, branches gone."""
    repo = fake.repository("willemkokke", "livery")
    live = repo.pr.get(1)
    assert live is not None
    fake.settle("willemkokke", "livery", live.head_sha)
    repo.pr.merge_now(1, title="feat: r")
    fake.create_tag("willemkokke", "livery", "packages/forge/v9.9.9")
    # In production the forge IS origin; mirror its branch deletion.
    subprocess.run(
        ["git", "push", "origin", ":workflow/release/forge"],
        cwd=root,
        capture_output=True,
        check=True,
    )


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


def test_named_dependencies_route_siblings_to_floors_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    root = _instance(tmp_path)
    _fake, git = _fake_pair(root)
    invoked: list[tuple[str, ...]] = []
    floors: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        "livery.workshop._update_driver.run_uv",
        lambda *args, root: invoked.append(args),
    )
    monkeypatch.setattr(
        "livery.workshop._update_driver.discover_packages",
        lambda _root: (SimpleNamespace(name="livery-forge"),),
    )

    def _floors(_root: Path, _git: GitOps, *, only: tuple[str, ...] = ()) -> list[str]:
        floors.append(only)
        return []

    monkeypatch.setattr("livery.workshop._update_driver.bump_floors", _floors)
    driver = UpdateDriver(
        root, git, "dependencies", armed=False, names=("requests", "livery-forge")
    )
    git.create_branch(driver.branch)
    driver._work()
    # The external name moves through the lock; the sibling never
    # reaches it (the lock cannot move a source-resolved member), and
    # only the named floor moves.
    assert ("lock", "--upgrade-package", "requests") in invoked
    assert all("livery-forge" not in call for call in invoked)
    assert ("lock", "--upgrade") not in invoked
    assert floors == [("livery-forge",)]


def test_the_parked_update_waits_and_the_bounded_wait_parks(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _instance(tmp_path)
    fake, git = _fake_pair(root)
    repo = fake.repository("willemkokke", "livery")
    _open_release(fake, git, root)
    # Non-interactive bounded wait: parks with the teaching, True never.
    cleared = wait_for_releases(repo, git, interactive=False, timeout=0.05, poll=0.01)
    out = capsys.readouterr().out
    assert cleared is False
    assert "Release(s) in progress: release/forge" in out
    assert "finished automatically" in out and "Ctrl-C is safe" in out
    # The release completes (merge it); the wait clears.
    _finish_release(fake, root)
    assert wait_for_releases(repo, git, interactive=False, timeout=1, poll=0.01)


def test_a_red_gate_stops_before_the_commit_with_the_resume_teaching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    root = _instance(tmp_path)
    _fake, git = _fake_pair(root)
    (root / "livery.toml").write_text('[workspace]\ntemplates = "templates"\n')
    _git(root, "commit", "-am", "chore: drift")
    _git(root, "push", "origin", "main")
    # The real run_gate over a stub subprocess: exit 1 is the verdict.
    monkeypatch.setattr(
        "livery.workshop._update_driver.subprocess",
        SimpleNamespace(run=lambda *a, **k: SimpleNamespace(returncode=1)),
    )
    monkeypatch.setattr("livery.workshop._update_driver.run_gate", _REAL_GATE)
    driver = UpdateDriver(root, git, "templates", armed=False)
    with pytest.raises(_FAILURES) as caught:
        driver.prepare()
    message = str(caught.value)
    assert "gate is red" in message and "resume" in message
    # Nothing was committed: the changes wait on the branch.
    assert git.current_branch() == "workflow/update/templates"
    assert git.subjects_ahead("main") == []
    assert not git.is_clean()


def test_sync_and_the_gate_run_between_the_work_and_the_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _instance(tmp_path)
    _fake, git = _fake_pair(root)
    (root / "livery.toml").write_text('[workspace]\ntemplates = "templates"\n')
    _git(root, "commit", "-am", "chore: drift")
    _git(root, "push", "origin", "main")
    events: list[str] = []
    monkeypatch.setattr(
        "livery.workshop._update_driver.run_uv",
        lambda *args, root: events.append(args[0]),
    )
    monkeypatch.setattr(
        "livery.workshop._update_driver.run_gate",
        lambda root: events.append("gate"),
    )
    driver = UpdateDriver(root, git, "templates", armed=False)
    submission = driver.prepare()
    assert submission is not None
    assert events == ["sync", "gate"]
    assert git.subjects_ahead("main") == ["chore: update templates"]


def test_the_reexec_guard_prevents_a_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _instance(tmp_path)
    _fake, git = _fake_pair(root)
    (root / "livery.toml").write_text('[workspace]\ntemplates = "templates"\n')
    _git(root, "commit", "-am", "chore: drift")
    _git(root, "push", "origin", "main")
    versions = iter(("0.0.1", "0.0.2"))
    monkeypatch.setattr(
        "livery.workshop._update_driver._locked_workshop",
        lambda _root: next(versions),
    )
    spawned: list[list[str]] = []

    def _no_spawn(command: list[str], _root: Path, _env: dict[str, str]) -> int:
        spawned.append(command)
        return 0

    monkeypatch.setattr("livery.workshop._update_driver._spawn", _no_spawn)
    # The guard: inside the re-executed child, never spawn again.
    monkeypatch.setenv("LIVERY_UPDATE_REEXEC", "1")
    driver = UpdateDriver(root, git, "templates", armed=False)
    assert driver.prepare() is not None
    assert spawned == []


def test_an_update_moving_the_workshop_finishes_in_a_fresh_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _instance(tmp_path)
    _fake, git = _fake_pair(root)
    (root / "livery.toml").write_text('[workspace]\ntemplates = "templates"\n')
    _git(root, "commit", "-am", "chore: drift")
    _git(root, "push", "origin", "main")
    versions = iter(("0.0.1", "0.0.2"))
    monkeypatch.setattr(
        "livery.workshop._update_driver._locked_workshop",
        lambda _root: next(versions),
    )
    spawned: list[tuple[list[str], str]] = []

    def _record(command: list[str], _root: Path, env: dict[str, str]) -> int:
        spawned.append((command, env.get("LIVERY_UPDATE_REEXEC", "")))
        return 7

    monkeypatch.setattr("livery.workshop._update_driver._spawn", _record)
    monkeypatch.delenv("LIVERY_UPDATE_REEXEC", raising=False)
    driver = UpdateDriver(root, git, "templates", armed=True)
    with pytest.raises(SystemExit) as caught:
        driver.prepare()
    assert caught.value.code == 7  # the child's verdict is the verdict
    command, flag = spawned[0]
    assert command == ["uv", "run", "fm", "workflow.update.templates", "--armed"]
    assert flag == "1"


def test_a_non_interactive_drive_parks_at_exit_zero_with_the_prose(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from livery.workshop._update_driver import _drive

    root = _instance(tmp_path)
    fake, git = _fake_pair(root)
    repo = fake.repository("willemkokke", "livery")
    _wire_drive(monkeypatch, root, repo, git)
    _open_release(fake, git, root)
    (root / "livery.toml").write_text('[workspace]\ntemplates = "templates"\n')
    _git(root, "commit", "-am", "chore: drift")
    _git(root, "push", "origin", "main")
    _drive("templates", armed=True)  # returns, no SystemExit: exit 0
    out = capsys.readouterr().out
    assert "submitting unarmed" in out
    assert "still parked" in out
    parked = repo.pr.get(2)
    assert parked is not None and not parked.merged


def test_one_invocation_parks_waits_refreshes_floors_and_arms_to_merged(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import livery.workshop._update_driver as ud

    root = _instance(tmp_path)
    fake, git = _fake_pair(root)
    repo = fake.repository("willemkokke", "livery")
    _wire_drive(monkeypatch, root, repo, git)
    _open_release(fake, git, root)
    (root / "livery.toml").write_text('[workspace]\ntemplates = "templates"\n')
    _git(root, "commit", "-am", "chore: drift")
    _git(root, "push", "origin", "main")
    released = {"done": False}

    def _floors(_root: Path, _git: GitOps, *, only: tuple[str, ...] = ()) -> list[str]:
        # Before the release lands there is nothing to raise; after it,
        # the fresh floor is a real tree change the re-run must commit.
        if not released["done"]:
            return []
        (root / "floors.md").write_text("forge floor -> 9.9.9\n")
        return ["packages/x: floor on packages/forge -> 9.9.9"]

    monkeypatch.setattr("livery.workshop._update_driver.bump_floors", _floors)
    real_wait = ud.wait_for_releases

    def _finishing_wait(
        repo: Repository,
        git: GitOps,
        *,
        interactive: bool,
        timeout: float = 1.0,
        poll: float = 0.01,
    ) -> bool:
        _finish_release(fake, root)
        released["done"] = True
        return real_wait(repo, git, interactive=interactive, timeout=timeout, poll=poll)

    monkeypatch.setattr(
        "livery.workshop._update_driver.wait_for_releases", _finishing_wait
    )
    ud._drive("templates", armed=True)
    out = capsys.readouterr().out
    # The parked submit names the release; the wait's own announcement
    # is asserted in the bounded-wait test, where a release is live
    # when the wait starts.
    assert "Release release/forge is in flight: submitting unarmed" in out
    parked = repo.pr.get(2)
    assert parked is not None and parked.merged
    # The fresh floor landed as a second commit on the update branch
    # before the arm. (The fake's squash never reaches the real
    # origin, so the branch is where the refresh is observable.)
    log = subprocess.run(
        ["git", "log", "--format=%s", "--stat", "workflow/update/templates"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "floors.md" in log
    assert log.count("chore: update templates") == 2


def test_a_rerun_after_the_wait_was_killed_resumes_from_parked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from livery.workshop._update_driver import _drive

    root = _instance(tmp_path)
    fake, git = _fake_pair(root)
    repo = fake.repository("willemkokke", "livery")
    _wire_drive(monkeypatch, root, repo, git)
    _open_release(fake, git, root)
    (root / "livery.toml").write_text('[workspace]\ntemplates = "templates"\n')
    _git(root, "commit", "-am", "chore: drift")
    _git(root, "push", "origin", "main")
    # First invocation parks (bounded wait, then exit 0): the kill.
    _drive("templates", armed=True)
    parked = repo.pr.get(2)
    assert parked is not None and not parked.merged
    # The release finishes while nothing is running; the re-run
    # resumes from parked and completes.
    _finish_release(fake, root)
    _drive("templates", armed=True)
    resumed = repo.pr.get(2)
    assert resumed is not None and resumed.merged


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
