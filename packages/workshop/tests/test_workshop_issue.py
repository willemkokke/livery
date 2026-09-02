"""The issue family: refusals first, then the lifecycle on the fake."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from footman import Failed

from livery.forge.testing import FakeForge
from livery.workshop._git_ops import GitOps
from livery.workshop._issue_tasks import (
    assignee_limit,
    branch_name,
    issue_close,
    issue_create,
    issue_start,
    issue_stop,
    parse_ref,
    worktree_path,
)
from livery.workshop._shell import default_kind, shell_launch_plan, shell_prepare

_FAILURES = (SystemExit, Failed)

ROOT = Path(__file__).resolve().parents[3]


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True)


def _instance(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "--initial-branch=main")
    root = tmp_path / "ws"
    _git(tmp_path, "clone", str(origin), "ws")
    _git(root, "config", "user.email", "t@livery.local")
    _git(root, "config", "user.name", "T")
    (root / "livery.toml").write_text("[workspace]\n")
    (root / "seed.txt").write_text("s\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "chore: seed")
    _git(root, "push", "-u", "origin", "main")
    return root


@pytest.fixture()
def rig(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, FakeForge, GitOps]:
    root = _instance(tmp_path)
    fake = FakeForge()
    fake.create_repo("willemkokke", "livery", private=True, description="t")
    repo = fake.repository("willemkokke", "livery")
    git = GitOps(root)
    monkeypatch.setattr("livery.workshop._layers.workspace_root", lambda: root)
    monkeypatch.setattr(
        "livery.workshop._forge_lane.this_repository", lambda _root: repo
    )
    monkeypatch.setattr("livery.workshop._issue_tasks._me", lambda _repo: "fake-user")
    monkeypatch.setattr("footman.data_dir", lambda: tmp_path / "home")
    monkeypatch.chdir(root)
    return root, fake, git


def test_ref_parsing_and_branch_grammar() -> None:
    assert parse_ref("#123") == (123, "")
    assert parse_ref("123") == (123, "")
    assert parse_ref("fix the flaky watch") == (0, "fix the flaky watch")
    long_title = "a " + "very " * 30 + "long name"
    branch = branch_name("feat", 7, long_title)
    assert branch.startswith("feat/7-a-very-")
    assert len(branch.split("-", 1)[1]) <= 40 + len("very")  # capped slug


def test_the_assignee_limit_is_workspace_policy(tmp_path: Path) -> None:
    assert assignee_limit(tmp_path) == 1  # no contract: the default
    (tmp_path / "livery.toml").write_text("[issues]\nassignees = 3\n")
    assert assignee_limit(tmp_path) == 3
    (tmp_path / "livery.toml").write_text("[workspace]\n")
    assert assignee_limit(tmp_path) == 1


def test_the_worktree_lives_under_the_runners_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The home is the runner's own data directory, asked of footman:
    # a footman plugin owns no home of its own.
    monkeypatch.setattr("footman.data_dir", lambda: tmp_path / "runner")
    path = worktree_path(tmp_path / "repo", 9, "Fix It Now")
    assert path == tmp_path / "runner" / "worktrees" / "repo" / "9-fix-it-now"


def test_start_at_the_limit_warns_and_continues(
    rig: tuple[Path, FakeForge, GitOps],
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Assignment is documentation, not a gate: at the limit the work
    # still starts; only the listing is lost, and the warning says
    # to communicate.
    _root, fake, git = rig
    repo = fake.repository("willemkokke", "livery")
    created = repo.issue.create("held work")
    repo.issue.assign(created.number, "colleague")
    issue_start(str(created.number), worktree=False)
    out = capsys.readouterr().out
    assert "Warning" in out and "colleague" in out
    assert "[issues] assignees" in out and "livery.toml" in out
    assert git.current_branch().startswith(f"feat/{created.number}-")
    live = repo.issue.get(created.number)
    assert live is not None and live.assignees == ("colleague",)


def test_start_refuses_a_missing_number_and_a_bad_type(
    rig: tuple[Path, FakeForge, GitOps],
) -> None:
    with pytest.raises(_FAILURES) as caught:
        issue_start("999", worktree=False)
    assert "does not exist" in str(caught.value)
    with pytest.raises(_FAILURES) as caught:
        issue_start("999", type="wat", worktree=False)
    assert "unknown type" in str(caught.value)


def test_start_in_this_checkout_refuses_dirt_naming_both_escapes(
    rig: tuple[Path, FakeForge, GitOps],
) -> None:
    root, fake, _git_seam = rig
    repo = fake.repository("willemkokke", "livery")
    created = repo.issue.create("dirty start")
    (root / "stray.txt").write_text("dirty\n")
    with pytest.raises(_FAILURES) as caught:
        issue_start(str(created.number), worktree=False)
    message = str(caught.value)
    assert "--wip" in message and "worktree" in message


def test_start_assigns_and_branches_from_the_fetched_base(
    rig: tuple[Path, FakeForge, GitOps],
) -> None:
    _root, fake, git = rig
    repo = fake.repository("willemkokke", "livery")
    created = repo.issue.create("build the widget")
    issue_start(str(created.number), worktree=False)
    live = repo.issue.get(created.number)
    assert live is not None and "fake-user" in live.assignees
    assert git.current_branch() == f"feat/{created.number}-build-the-widget"


def test_start_wip_parks_the_dirty_tree_then_branches(
    rig: tuple[Path, FakeForge, GitOps],
) -> None:
    root, fake, git = rig
    repo = fake.repository("willemkokke", "livery")
    created = repo.issue.create("park then go")
    (root / "stray.txt").write_text("dirty\n")
    issue_start(str(created.number), worktree=False, wip=True)
    assert git.current_branch().startswith(f"feat/{created.number}-")
    parked = subprocess.run(
        ["git", "log", "--format=%s", "main", "-1"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "chore(wip): parked" in parked


def test_start_opens_a_worktree_by_default_and_provisions_it(
    rig: tuple[Path, FakeForge, GitOps], monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    root, fake, git = rig
    repo = fake.repository("willemkokke", "livery")
    created = repo.issue.create("tree work")
    provisioned: list[str] = []

    def _run(argv: list[str], **kwargs: object) -> SimpleNamespace:
        provisioned.append(" ".join(argv))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        "livery.workshop._issue_tasks.subprocess", SimpleNamespace(run=_run)
    )
    issue_start(str(created.number))
    path = worktree_path(root, created.number, "tree work")
    assert path.is_dir()
    head = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert head == f"feat/{created.number}-tree-work"
    assert any("fm sync" in call for call in provisioned)
    # This checkout never moved: the worktree is where the work is.
    assert git.current_branch() == "main"


def _started(
    rig: tuple[Path, FakeForge, GitOps], title: str = "the work"
) -> tuple[Path, FakeForge, GitOps, int, str]:
    root, fake, git = rig
    repo = fake.repository("willemkokke", "livery")
    created = repo.issue.create(title)
    issue_start(str(created.number), worktree=False)
    branch = git.current_branch()
    return root, fake, git, created.number, branch


def test_stop_refuses_an_unpushed_delta_naming_the_remote(
    rig: tuple[Path, FakeForge, GitOps],
) -> None:
    root, _fake, git, number, branch = _started(rig)
    (root / "work.txt").write_text("w\n")
    git.commit_all("feat: local only")
    _git(root, "push", "origin", branch)  # remote exists...
    (root / "more.txt").write_text("m\n")
    git.commit_all("feat: newer, unpushed")
    with pytest.raises(_FAILURES) as caught:
        issue_stop(str(number))
    message = str(caught.value)
    assert "not on the remote branch" in message or "remote branch does" in message
    assert "--discard" in message


def test_stop_refuses_when_the_remote_vanished_while_open(
    rig: tuple[Path, FakeForge, GitOps],
) -> None:
    root, _fake, git, number, _branch = _started(rig)
    (root / "work.txt").write_text("w\n")
    git.commit_all("feat: local only")
    with pytest.raises(_FAILURES) as caught:
        issue_stop(str(number))
    message = str(caught.value)
    assert "exceptional" in message or "someone removed" in message


def test_stop_names_the_closure_when_the_issue_is_closed(
    rig: tuple[Path, FakeForge, GitOps],
) -> None:
    root, fake, git, number, _branch = _started(rig)
    (root / "work.txt").write_text("w\n")
    git.commit_all("feat: local only")
    fake.repository("willemkokke", "livery").issue.close(number)
    with pytest.raises(_FAILURES) as caught:
        issue_stop(str(number))
    assert "already closed" in str(caught.value)


def test_stop_unassigns_cleans_local_and_leaves_the_remote(
    rig: tuple[Path, FakeForge, GitOps],
) -> None:
    root, fake, git, number, branch = _started(rig)
    (root / "work.txt").write_text("w\n")
    git.commit_all("feat: pushed work")
    _git(root, "push", "origin", branch)
    issue_stop()  # bare: inferred from the branch
    live = fake.repository("willemkokke", "livery").issue.get(number)
    assert live is not None and live.assignees == ()
    assert git.current_branch() == "main"
    assert not git.local_branch_exists(branch)
    remote = subprocess.run(
        ["git", "ls-remote", "--heads", "origin", branch],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert branch in remote  # the pool's copy stays


def test_stop_discard_destroys_deliberately(
    rig: tuple[Path, FakeForge, GitOps],
) -> None:
    root, _fake, git, number, branch = _started(rig)
    (root / "work.txt").write_text("w\n")
    git.commit_all("feat: local only")
    issue_stop(str(number), discard=True)
    assert not git.local_branch_exists(branch)


def test_close_tears_down_the_open_pr_and_records_the_sha(
    rig: tuple[Path, FakeForge, GitOps], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, fake, git, number, branch = _started(rig)
    repo = fake.repository("willemkokke", "livery")
    (root / "work.txt").write_text("w\n")
    git.commit_all("feat: submitted work")
    _git(root, "push", "origin", branch)
    tip = git.head_sha()
    fake.push("willemkokke", "livery", branch, sha=tip)
    repo.pr.open(branch, "main", "feat: submitted work")
    torn: list[str] = []

    def _teardown(
        _repo: object,
        _git: GitOps,
        target: str,
        _base: str,
        *,
        keep_branches: bool = False,
    ) -> None:
        _ = keep_branches
        torn.append(target)
        repo.pr.close(1)
        _git._run("push", "origin", "--delete", target)
        if _git.local_branch_exists(target):
            if _git.current_branch() == target:
                _git.switch("main")
            _git.delete_local_branch(target)

    monkeypatch.setattr("livery.workshop._submit.teardown_branch", _teardown)
    issue_close(str(number), reason="wontfix", message="superseded by 12")
    assert torn == [branch]
    live = repo.issue.get(number)
    assert live is not None and live.state == "closed"
    # The close comment carries the reason, the message, and the sha
    # read before the teardown could delete the branch.
    comments = fake.comment_bodies("willemkokke", "livery", number, kind="issue")
    record = comments[-1]
    assert "closed: wontfix" in record and "superseded by 12" in record
    assert tip in record


def test_close_on_a_merged_pr_degrades_to_already_resolved(
    rig: tuple[Path, FakeForge, GitOps], capsys: pytest.CaptureFixture[str]
) -> None:
    root, fake, git, number, branch = _started(rig)
    repo = fake.repository("willemkokke", "livery")
    (root / "work.txt").write_text("w\n")
    git.commit_all("feat: landed work")
    _git(root, "push", "origin", branch)
    tip = git.head_sha()
    fake.push("willemkokke", "livery", branch, sha=tip)
    repo.pr.open(branch, "main", "feat: landed work")
    fake.settle("willemkokke", "livery", tip)
    repo.pr.merge_now(1, title="feat: landed work")
    issue_close(str(number), reason="wontfix")
    out = capsys.readouterr().out
    assert "issue already resolved, nothing left to do" in out
    assert "reason does not apply" in out
    live = repo.issue.get(number)
    assert live is not None and live.state == "closed"


def test_close_keep_branch_retains_both_copies(
    rig: tuple[Path, FakeForge, GitOps],
) -> None:
    root, _fake, git, number, branch = _started(rig)
    (root / "work.txt").write_text("w\n")
    git.commit_all("feat: kept work")
    _git(root, "push", "origin", branch)
    issue_close(str(number), reason="stale", keep_branch=True)
    assert git.local_branch_exists(branch)
    remote = subprocess.run(
        ["git", "ls-remote", "--heads", "origin", branch],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert branch in remote


def test_close_refuses_to_destroy_an_only_copy_without_discard(
    rig: tuple[Path, FakeForge, GitOps],
) -> None:
    root, _fake, git, number, _branch = _started(rig)
    (root / "work.txt").write_text("w\n")
    git.commit_all("feat: only local")
    with pytest.raises(_FAILURES) as caught:
        issue_close(str(number), reason="stale")
    message = str(caught.value)
    assert "--keep-branch" in message and "--discard" in message


def test_create_falls_back_when_the_label_is_refused(
    rig: tuple[Path, FakeForge, GitOps],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from livery.forge import ForgeError
    from livery.forge.testing import _fake as fake_module

    real_create = fake_module._FakeIssues.create
    calls = {"n": 0}

    def _refusing(self: object, title: str, **kwargs: object) -> object:
        calls["n"] += 1
        if kwargs.get("labels"):
            raise ForgeError("labels are refused here", status=422)
        return real_create(self, title, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(fake_module._FakeIssues, "create", _refusing)
    issue_create("labelled work")
    out = capsys.readouterr().out
    assert "filed #" in out and "without it" in out
    assert calls["n"] == 2  # refused with the label, filed without


# --- the shell plans ---


def test_cmd_is_refused_naming_pwsh(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as caught:
        shell_launch_plan("cmd", root=tmp_path, tmp_dir=tmp_path)
    assert "pwsh" in str(caught.value)
    with pytest.raises(SystemExit) as caught:
        shell_launch_plan("fish", root=tmp_path, tmp_dir=tmp_path)
    assert "bash, zsh, pwsh" in str(caught.value)


def test_the_bash_plan_writes_an_rc_that_chains_and_enters(
    tmp_path: Path,
) -> None:
    plan = shell_launch_plan("bash", root=tmp_path, tmp_dir=tmp_path)
    assert plan.argv[1] == "--rcfile" and plan.argv[-1] == "-i"
    rc = next(iter(plan.files.values()))
    assert '. "$HOME/.bashrc"' in rc
    assert "env.emit posix" in rc


def test_the_zsh_plan_needs_env_and_prepare_refuses_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The binary is stubbed before any plan is built: the CI runners
    # have no zsh, and the plan's shape is what is under test.
    monkeypatch.setattr("livery.workshop._shell.shutil.which", lambda _k: "/bin/zsh")
    plan = shell_launch_plan("zsh", root=tmp_path, tmp_dir=tmp_path)
    assert "ZDOTDIR" in plan.env
    zshrc = next(
        content for path, content in plan.files.items() if ".zshrc" in str(path)
    )
    assert 'ZDOTDIR="$HOME"' in zshrc  # nested shells behave
    monkeypatch.setattr("livery.workshop._shell.shutil.which", lambda _k: "/bin/zsh")
    with pytest.raises(SystemExit) as caught:
        shell_prepare("zsh")
    assert "ZDOTDIR" in str(caught.value)


def test_a_hostile_root_path_is_quoted_into_the_eval(tmp_path: Path) -> None:
    hostile = tmp_path / "we ird'name"
    hostile.mkdir()
    plan = shell_launch_plan("bash", root=hostile, tmp_dir=tmp_path)
    rc = next(iter(plan.files.values()))
    # The path rides as one shell word: the embedded quote is
    # spliced (shlex's '"'"' form), so it does not end the string.
    assert "we ird" in rc and "'\"'\"'" in rc


def test_default_kind_follows_the_shell_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SHELL", "/bin/zsh")
    assert default_kind() == "zsh"
    monkeypatch.setenv("SHELL", "/opt/fish")
    assert default_kind() == "bash"


# --- the audit-close forcing tests ---


def _started_in_worktree(
    rig: tuple[Path, FakeForge, GitOps],
    monkeypatch: pytest.MonkeyPatch,
    title: str = "tree work",
) -> tuple[Path, FakeForge, GitOps, int, str, Path]:
    from types import SimpleNamespace

    root, fake, git = rig
    repo = fake.repository("willemkokke", "livery")
    created = repo.issue.create(title)
    monkeypatch.setattr(
        "livery.workshop._issue_tasks.subprocess",
        SimpleNamespace(run=lambda *a, **k: SimpleNamespace(returncode=0)),
    )
    issue_start(str(created.number))
    path = worktree_path(root, created.number, title)
    branch = f"feat/{created.number}-{'-'.join(title.split())}"
    return root, fake, git, created.number, branch, path


def test_close_refuses_before_any_teardown_when_work_is_only_local(
    rig: tuple[Path, FakeForge, GitOps],
) -> None:
    # The ordering hole the audit found: the refusal must fire while
    # the PR is still open and the branch still exists, never after a
    # teardown already destroyed both.
    root, fake, git, number, branch = _started(rig)
    repo = fake.repository("willemkokke", "livery")
    (root / "pushed.txt").write_text("p\n")
    git.commit_all("feat: pushed part")
    _git(root, "push", "origin", branch)
    fake.push("willemkokke", "livery", branch, sha=git.head_sha())
    repo.pr.open(branch, "main", "feat: pushed part")
    (root / "unpushed.txt").write_text("u\n")
    git.commit_all("feat: the unpushed delta")
    with pytest.raises(_FAILURES) as caught:
        issue_close(str(number), reason="wontfix")
    assert "--discard" in str(caught.value)
    live = repo.pr.get(1)
    assert live is not None and live.state == "open"  # nothing torn down
    assert git.local_branch_exists(branch)


def test_close_keep_branch_ends_the_pr_and_keeps_both_branches(
    rig: tuple[Path, FakeForge, GitOps],
) -> None:
    root, fake, git, number, branch = _started(rig)
    repo = fake.repository("willemkokke", "livery")
    (root / "work.txt").write_text("w\n")
    git.commit_all("feat: kept work")
    _git(root, "push", "origin", branch)
    fake.push("willemkokke", "livery", branch, sha=git.head_sha())
    repo.pr.open(branch, "main", "feat: kept work")
    issue_close(str(number), reason="stale", keep_branch=True)
    live = repo.pr.get(1)
    assert live is not None and live.state == "closed" and not live.merged
    assert git.local_branch_exists(branch)
    remote = subprocess.run(
        ["git", "ls-remote", "--heads", "origin", branch],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert branch in remote


def test_close_on_merged_keeps_an_only_local_delta_without_discard(
    rig: tuple[Path, FakeForge, GitOps], capsys: pytest.CaptureFixture[str]
) -> None:
    root, fake, git, number, branch = _started(rig)
    repo = fake.repository("willemkokke", "livery")
    (root / "work.txt").write_text("w\n")
    git.commit_all("feat: landed work")
    _git(root, "push", "origin", branch)
    tip = git.head_sha()
    fake.push("willemkokke", "livery", branch, sha=tip)
    repo.pr.open(branch, "main", "feat: landed work")
    fake.settle("willemkokke", "livery", tip)
    repo.pr.merge_now(1, title="feat: landed work")
    (root / "leftover.txt").write_text("l\n")
    git.commit_all("feat: a leftover past the merge")
    issue_close(str(number), reason="done")
    out = capsys.readouterr().out
    assert "kept" in out and "--discard" in out
    assert git.local_branch_exists(branch)  # the leftover survived


def test_stop_from_main_removes_a_clean_worktree(
    rig: tuple[Path, FakeForge, GitOps], monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, _fake, git, number, branch, path = _started_in_worktree(rig, monkeypatch)
    _git(path, "push", "-u", "origin", branch)
    issue_stop(str(number))
    assert not path.exists()
    assert not git.local_branch_exists(branch)


def test_stop_sees_uncommitted_worktree_changes_from_the_main_checkout(
    rig: tuple[Path, FakeForge, GitOps], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The audit's second hole: the dirt lives in the worktree, stop
    # runs from the main checkout, and the refusal must still fire.
    _root, _fake, _git_seam, number, branch, path = _started_in_worktree(
        rig, monkeypatch
    )
    _git(path, "push", "-u", "origin", branch)
    (path / "precious.txt").write_text("uncommitted\n")
    with pytest.raises(_FAILURES) as caught:
        issue_stop(str(number))
    message = str(caught.value)
    assert "worktree" in message and "--discard" in message
    assert (path / "precious.txt").is_file()
    issue_stop(str(number), discard=True)
    assert not path.exists()


def test_stop_off_an_issue_branch_teaches(
    rig: tuple[Path, FakeForge, GitOps],
) -> None:
    with pytest.raises(_FAILURES) as caught:
        issue_stop()
    assert "not on an issue branch" in str(caught.value)


def test_stop_with_a_remote_only_branch_reports_honestly(
    rig: tuple[Path, FakeForge, GitOps], capsys: pytest.CaptureFixture[str]
) -> None:
    root, fake, git, number, branch = _started(rig)
    _git(root, "push", "origin", branch)
    git.switch("main")
    git.delete_local_branch(branch)
    issue_stop(str(number))
    out = capsys.readouterr().out
    assert "nothing local to remove" in out
    live = fake.repository("willemkokke", "livery").issue.get(number)
    assert live is not None and live.assignees == ()


def test_start_reruns_resume_instead_of_erroring(
    rig: tuple[Path, FakeForge, GitOps], capsys: pytest.CaptureFixture[str]
) -> None:
    _root, _fake, git, number, branch = _started(rig)
    issue_start(str(number), worktree=False)  # the re-run
    assert "already started" in capsys.readouterr().out
    assert git.current_branch() == branch


def test_start_with_a_title_survives_a_label_refusal(
    rig: tuple[Path, FakeForge, GitOps],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from livery.forge import ForgeError
    from livery.forge.testing import _fake as fake_module

    real_create = fake_module._FakeIssues.create

    def _refusing(self: object, title: str, **kwargs: object) -> object:
        if kwargs.get("labels"):
            raise ForgeError("labels are refused here", status=422)
        return real_create(self, title, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(fake_module._FakeIssues, "create", _refusing)
    issue_start("titled work", worktree=False)
    out = capsys.readouterr().out
    assert "filed #" in out and "without it" in out


def test_the_fail_opens_note_and_continue(
    rig: tuple[Path, FakeForge, GitOps],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from livery.forge import ForgeError
    from livery.forge.testing import _fake as fake_module

    root, fake, git = rig
    repo = fake.repository("willemkokke", "livery")
    created = repo.issue.create("scoped token work")

    def _refuse(*_a: object, **_k: object) -> None:
        raise ForgeError("token lacks issue-write", status=403)

    # assign fail-open: the branch is still cut.
    monkeypatch.setattr(fake_module._FakeIssues, "assign", _refuse)
    issue_start(str(created.number), worktree=False)
    out = capsys.readouterr().out
    assert "could not assign" in out
    assert git.current_branch().startswith(f"feat/{created.number}-")
    # unassign fail-open: the local cleanup still happens.
    _git(root, "push", "origin", git.current_branch())
    monkeypatch.setattr(fake_module._FakeIssues, "unassign", _refuse)
    issue_stop(str(created.number))
    out = capsys.readouterr().out
    assert "could not unassign" in out
    assert git.current_branch() == "main"


def test_the_provision_failure_is_a_note_not_a_refusal(
    rig: tuple[Path, FakeForge, GitOps],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    root, fake, _git = rig
    repo = fake.repository("willemkokke", "livery")
    created = repo.issue.create("cold tree")
    monkeypatch.setattr(
        "livery.workshop._issue_tasks.subprocess",
        SimpleNamespace(run=lambda *a, **k: SimpleNamespace(returncode=1)),
    )
    issue_start(str(created.number))
    out = capsys.readouterr().out
    assert "fm sync` in the worktree failed" in out
    assert worktree_path(root, created.number, "cold tree").is_dir()


def test_completion_offline_costs_the_suggestions_never_the_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from livery.workshop._issue_tasks import _open_numbers

    def _broken() -> object:
        raise RuntimeError("offline")

    monkeypatch.setattr("livery.workshop._layers.workspace_root", _broken)
    assert _open_numbers() == []


def test_open_code_missing_binary_is_a_note(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from livery.workshop._issue_tasks import _open_work

    def _missing(*_a: object, **_k: object) -> object:
        raise FileNotFoundError("code")

    monkeypatch.setattr(
        "livery.workshop._issue_tasks.subprocess", SimpleNamespace(run=_missing)
    )
    _open_work(tmp_path, "code")
    assert "not on PATH" in capsys.readouterr().out


def test_the_agent_not_installed_teaching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._issue_tasks import _launch_agent

    monkeypatch.setattr("livery.workshop._issue_tasks.os.chdir", lambda _p: None)

    def _no_exec(*_a: object) -> None:
        raise OSError("not found")

    monkeypatch.setattr("livery.workshop._issue_tasks.os.execvp", _no_exec)
    with pytest.raises(_FAILURES) as caught:
        _launch_agent("claude", tmp_path, 1, "t", "b", "feat/1-t", "")
    assert "not installed" in str(caught.value)


def test_the_pwsh_plan_enters_through_invoke_expression(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "livery.workshop._shell.shutil.which", lambda _k: "/usr/bin/pwsh"
    )
    hostile = tmp_path / "odd'name"
    hostile.mkdir()
    plan = shell_launch_plan("pwsh", root=hostile, tmp_dir=tmp_path)
    command = plan.argv[-1]
    assert "Invoke-Expression" in command and "env.emit pwsh" in command
    # The single quote is doubled, PowerShell's own splice.
    assert "odd''name" in command
    assert plan.files == {} and plan.env == {}
