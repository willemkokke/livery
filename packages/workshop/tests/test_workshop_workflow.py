"""The engine's edge table, forced: state, decision, abort, bundles.

The decision layer and the classifier are pure functions, so their
whole tables run with no I/O. The engine and abort run against
livery.forge.testing.FakeForge with a real temporary repository, the
same rig the submit suite uses. Refusals and fallbacks come first,
per the workspace convention: a broken fallback hides until it is
the only path left.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from footman import Failed

from livery.forge import ForgeError
from livery.forge.testing import FakeForge
from livery.workshop._diagnostics import gather_bundle, record
from livery.workshop._git_ops import GitOps
from livery.workshop._verdict import EXIT_DISARMED, Verdict, follow
from livery.workshop._workflow_decision import (
    WorkflowAction,
    workflow_decision,
)
from livery.workshop._workflow_engine import Submission
from livery.workshop._workflow_state import (
    Blocker,
    Signals,
    WorkflowKind,
    WorkflowState,
    WorkflowStatus,
    blocker,
    classify,
    kind_of,
    members_of,
    workflow_states,
)
from livery.workshop._workflow_tasks import abort_policy

_FAILURES = (SystemExit, Failed)

OWNER, NAME = "willemkokke", "livery"


def _wf(
    state: WorkflowState,
    *,
    name: str = "release/forge",
    author: str = "",
    members: tuple[str, ...] = (),
    reopenable: bool = False,
    blocked: Blocker = Blocker.NONE,
    armed: bool | None = False,
) -> WorkflowStatus:
    return WorkflowStatus(
        kind=kind_of(name),
        state=state,
        name=name,
        branch=f"workflow/{name}",
        members=members or members_of(name),
        author=author,
        reopenable=reopenable,
        blocker=blocked,
        armed=armed,
    )


def _decide(**overrides: object):
    defaults: dict[str, object] = {
        "kind": WorkflowKind.RELEASE,
        "name": "release/forge",
        "wf": _wf(WorkflowState.NONE),
        "members": ("forge",),
        "branch": "main",
        "dirty": False,
        "behind_default": 0,
        "default_branch": "main",
        "current_user": "willem",
        "others": (),
    }
    defaults.update(overrides)
    return workflow_decision(**defaults)  # type: ignore[arg-type]


# --- the decision table: every stop teaches, order is load-bearing ---


def test_wrong_branch_stops_and_names_both_homes() -> None:
    decision = _decide(branch="feat/9-thing")
    assert decision.action is WorkflowAction.STOP
    assert "main" in decision.message and "workflow/release/forge" in decision.message


def test_unknown_state_stops_before_anything_acts() -> None:
    decision = _decide(wf=_wf(WorkflowState.UNKNOWN))
    assert decision.action is WorkflowAction.STOP
    assert "status --workflow" in decision.message


def test_a_coworkers_live_workflow_is_theirs() -> None:
    decision = _decide(
        wf=_wf(WorkflowState.IN_PROGRESS, author="colleague"),
        current_user="willem",
    )
    assert decision.action is WorkflowAction.STOP
    assert "colleague" in decision.message
    assert "workflow.abort" in decision.message  # the deliberate option, taught


def test_an_intersecting_release_refuses_naming_author_and_packages() -> None:
    other = _wf(
        WorkflowState.IN_PROGRESS,
        name="release/forge+workshop",
        author="colleague",
    )
    decision = _decide(
        name="release/forge",
        members=("forge",),
        others=(other,),
    )
    assert decision.action is WorkflowAction.STOP
    message = decision.message
    assert "colleague" in message and "forge" in message
    assert "wait" in message and "disjoint" in message and "workflow.abort" in message


def test_an_unknown_other_release_still_refuses_intersection() -> None:
    # Absence cannot be proven from a blip.
    other = _wf(WorkflowState.UNKNOWN, name="release/forge+workshop")
    decision = _decide(members=("forge",), others=(other,))
    assert decision.action is WorkflowAction.STOP


def test_disjoint_releases_coexist() -> None:
    other = _wf(WorkflowState.IN_PROGRESS, name="release/workshop", author="x")
    decision = _decide(name="release/forge", members=("forge",), others=(other,))
    assert decision.action is WorkflowAction.START


def test_a_finished_other_is_tidied_with_its_target_named() -> None:
    other = _wf(WorkflowState.SUCCEEDED, name="release/workshop")
    decision = _decide(others=(other,))
    assert decision.action is WorkflowAction.TIDY_THEN_START
    assert decision.tidy_target is other  # never the driver's own live branch


def test_an_unknown_other_is_left_strictly_alone() -> None:
    other = _wf(WorkflowState.UNKNOWN, name="update/templates")
    decision = _decide(
        members=(), kind=WorkflowKind.UPDATE, name="update/deps", others=(other,)
    )
    assert decision.action is WorkflowAction.START


def test_dirty_tree_stops_except_a_preparing_update_on_its_branch() -> None:
    assert _decide(dirty=True).action is WorkflowAction.STOP
    resumed = _decide(
        kind=WorkflowKind.UPDATE,
        name="update/templates",
        members=(),
        wf=_wf(WorkflowState.PREPARING, name="update/templates"),
        dirty=True,
        branch="workflow/update/templates",
    )
    assert resumed.action is WorkflowAction.START  # mid-conflict resume
    # From any other branch the dirt is unrelated work: preparing
    # would carry it onto the workflow branch, so the stop holds.
    elsewhere = _decide(
        kind=WorkflowKind.UPDATE,
        name="update/templates",
        members=(),
        wf=_wf(WorkflowState.PREPARING, name="update/templates"),
        dirty=True,
    )
    assert elsewhere.action is WorkflowAction.STOP
    assert "uncommitted" in elsewhere.message


def test_a_succeeded_leftover_tidies_before_the_behind_check() -> None:
    # A completed leftover is behind by construction; merging the base
    # into a branch about to be deleted would be wasted motion.
    decision = _decide(wf=_wf(WorkflowState.SUCCEEDED), behind_default=3)
    assert decision.action is WorkflowAction.TIDY_THEN_START


def test_a_stale_set_re_prepares_before_arming() -> None:
    stale = _wf(WorkflowState.AWAITING_REVIEW, blocked=Blocker.STALE_SET)
    decision = _decide(wf=stale)
    assert decision.action is WorkflowAction.REPREPARE
    assert "moved" in decision.message


def test_behind_merges_the_default_in() -> None:
    decision = _decide(behind_default=2)
    assert decision.action is WorkflowAction.MERGE_DEFAULT


def test_ready_arms_failed_retries_closed_reopens() -> None:
    assert _decide(wf=_wf(WorkflowState.AWAITING_REVIEW)).action is WorkflowAction.ARM
    assert _decide(wf=_wf(WorkflowState.FAILED)).action is WorkflowAction.RETRY
    assert (
        _decide(wf=_wf(WorkflowState.FAILED, reopenable=True)).action
        is WorkflowAction.REOPEN
    )
    assert _decide(wf=_wf(WorkflowState.IN_PROGRESS)).action is WorkflowAction.RETRY
    assert _decide().action is WorkflowAction.START


# --- the classifier's table ---


def _sig(**overrides: object) -> Signals:
    defaults: dict[str, object] = {
        "kind": WorkflowKind.RELEASE,
        "remote_branch": False,
        "pr_state": "none",
        "ci_state": "",
        "members": ("forge",),
    }
    defaults.update(overrides)
    return Signals(**defaults)  # type: ignore[arg-type]


def test_classify_maps_the_lifecycle() -> None:
    assert classify(_sig()) is WorkflowState.PREPARING
    assert classify(_sig(remote_branch=True)) is WorkflowState.IN_PROGRESS
    assert classify(_sig(pr_state="open", ci_state="failure")) is WorkflowState.FAILED
    assert classify(_sig(pr_state="open", armed=False)) is WorkflowState.AWAITING_REVIEW
    # An unreadable arm watches like armed: a false "disarmed" is the
    # proven wrong verdict.
    assert classify(_sig(pr_state="open", armed=None)) is WorkflowState.IN_PROGRESS
    assert classify(_sig(pr_state="closed")) is WorkflowState.FAILED


def test_a_release_is_done_when_every_receipt_tag_is_cut() -> None:
    partial = _sig(pr_state="merged", members=("forge", "workshop"), tagged=("forge",))
    assert classify(partial) is WorkflowState.IN_PROGRESS
    done = _sig(
        pr_state="merged",
        members=("forge", "workshop"),
        tagged=("workshop", "forge"),
    )
    assert classify(done) is WorkflowState.SUCCEEDED
    red = _sig(pr_state="merged", members=("forge",), ci_state="failure")
    assert classify(red) is WorkflowState.FAILED
    update = _sig(kind=WorkflowKind.UPDATE, pr_state="merged", members=())
    assert classify(update) is WorkflowState.SUCCEEDED


def test_blocker_order_conflicts_red_stale_disarmed() -> None:
    assert blocker(_sig(pr_state="closed")) is Blocker.CLOSED
    assert blocker(_sig(pr_state="open", mergeable=False)) is Blocker.CONFLICTS
    assert blocker(_sig(pr_state="open", ci_state="failure")) is Blocker.CI_FAILING
    assert (
        blocker(_sig(pr_state="open", ci_state="success", stale_set=True))
        is Blocker.STALE_SET
    )
    assert blocker(_sig(pr_state="open", armed=False)) is Blocker.DISARMED


def test_names_carry_kind_and_members() -> None:
    assert kind_of("release/forge+workshop") is WorkflowKind.RELEASE
    assert kind_of("update/templates") is WorkflowKind.UPDATE
    assert members_of("release/forge+workshop") == ("forge", "workshop")
    assert members_of("update/templates") == ()


# --- gather and abort against the fake, refusals first ---


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True)


@pytest.fixture
def rig(tmp_path: Path) -> tuple[FakeForge, GitOps]:
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "--initial-branch=main")
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", str(origin), "clone")
    _git(clone, "config", "user.email", "t@livery.local")
    _git(clone, "config", "user.name", "T")
    (clone / "seed.txt").write_text("seed\n")
    _git(clone, "add", ".")
    _git(clone, "commit", "-m", "chore: seed")
    _git(clone, "push", "-u", "origin", "main")
    fake = FakeForge()
    fake.create_repo(OWNER, NAME, private=True, description="t")
    return fake, GitOps(clone)


def _repo(fake: FakeForge):
    return fake.repository(OWNER, NAME)


def test_an_unreadable_forge_reads_unknown_never_absent(
    rig: tuple[FakeForge, GitOps], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, git = rig
    git.create_branch("workflow/release/forge")
    (git.root / "x.txt").write_text("x\n")
    git.commit_all("feat: x")
    repo = _repo(fake)

    def _raise(*args: object, **kwargs: object):
        raise ForgeError("down", status=500)

    monkeypatch.setattr(repo.pr, "find_by_head", _raise)
    states = workflow_states(repo, git)
    assert [wf.state for wf in states] == [WorkflowState.UNKNOWN]
    # And the abort refuses the blip without force, teaching the retry.
    with pytest.raises(_FAILURES) as caught:
        abort_policy(repo, git, states, "", force=False)
    assert "blip" in str(caught.value) or "could not be read" in str(caught.value)


def test_abort_refusals_teach_before_anything_tears_down(
    rig: tuple[FakeForge, GitOps], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, git = rig
    repo = _repo(fake)
    live = _wf(WorkflowState.IN_PROGRESS, name="release/forge", author="colleague")
    other = _wf(WorkflowState.PREPARING, name="update/templates")
    # Several, non-interactive: refuse listing each by name.
    with pytest.raises(_FAILURES) as caught:
        abort_policy(repo, git, (live, other), "", force=False, interactive=False)
    message = str(caught.value)
    assert "release/forge" in message and "update/templates" in message
    # A live workflow refuses without force, naming the author.
    with pytest.raises(_FAILURES) as caught:
        abort_policy(repo, git, (live, other), "release/forge", force=False)
    assert "colleague" in str(caught.value) and "--force" in str(caught.value)
    # Bare --force with several refuses: force one by name.
    with pytest.raises(_FAILURES) as caught:
        abort_policy(repo, git, (live, other), "", force=True, interactive=False)
    assert "name" in str(caught.value)
    # An unknown name teaches what is in flight.
    with pytest.raises(_FAILURES) as caught:
        abort_policy(repo, git, (live, other), "release/ghost", force=False)
    assert "release/forge" in str(caught.value)


def test_abort_tears_down_a_safe_leftover_and_reconciles(
    rig: tuple[FakeForge, GitOps], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, git = rig
    repo = _repo(fake)
    git.create_branch("workflow/update/templates")
    (git.root / "u.txt").write_text("u\n")
    git.commit_all("chore: u")
    git.push("workflow/update/templates")
    fake.push(OWNER, NAME, "workflow/update/templates")
    reconciled: list[bool] = []
    monkeypatch.setattr(
        "livery.workshop._workflow_tasks._reconcile_configuration",
        lambda _git, _sha: reconciled.append(True),
    )
    leftover = _wf(WorkflowState.FAILED, name="update/templates")
    abort_policy(repo, git, (leftover,), "", force=False)
    assert not repo.branch_exists("workflow/update/templates")
    assert not git.local_branch_exists("workflow/update/templates")
    assert reconciled == [True]
    # Idempotent: nothing left is quiet success.
    abort_policy(repo, git, (), "", force=False)


# --- diagnostics: a bundle survives a scope-poor token ---


def test_the_bundle_records_a_sections_own_error_as_data(
    rig: tuple[FakeForge, GitOps], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, _git_seam = rig
    repo = _repo(fake)
    fake.push(OWNER, NAME, "feat/1-x")
    repo.pr.open("feat/1-x", "main", "feat: x")

    def _forbidden(number: int):
        raise ForgeError("403 scope", status=403)

    monkeypatch.setattr(repo.pr, "schedule_events", _forbidden)
    bundle = gather_bundle(
        repo, Verdict("disarmed", EXIT_DISARMED, "parked"), branch="feat/1-x"
    )
    assert bundle["pull_request"]["number"] == 1
    assert "403" in bundle["schedule_events"]["error"]  # the diagnosis itself
    assert bundle["verdict"]["exit_code"] == EXIT_DISARMED


def test_record_writes_prunes_and_never_raises(
    rig: tuple[FakeForge, GitOps], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake, _git_seam = rig
    repo = _repo(fake)
    monkeypatch.setattr("footman.data_dir", lambda: tmp_path / "home")
    for index in range(23):
        path = record(
            repo,
            Verdict("stalled", 16, f"n{index}"),
            branch=f"feat/{index}-x",
        )
        assert path is not None
    bundles = list((tmp_path / "home" / "diagnostics").glob("*.json"))
    assert len(bundles) == 20  # newest KEEP kept, older pruned


# --- the follower's two-poll confirmation ---


def test_a_one_poll_blocker_blip_does_not_end_the_watch(
    rig: tuple[FakeForge, GitOps], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The submit-then-arm race in miniature: one read says disarmed,
    # the next says merged. Ending on the first read is the proven
    # false exit 11.
    fake, git = rig
    repo = _repo(fake)
    verdicts = iter(
        [
            Verdict("disarmed", EXIT_DISARMED, "blip", 1),
            Verdict("merged", 0, "PR #1 merged", 1),
        ]
    )
    monkeypatch.setattr(
        "livery.workshop._verdict.classify",
        lambda *a, **k: next(verdicts),
    )
    result = follow(repo, "feat/1-x", git, interval=0, timeout=5)
    assert result.state == "merged"


def test_closed_is_definitive_on_a_single_read(
    rig: tuple[FakeForge, GitOps], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake, git = rig
    repo = _repo(fake)
    monkeypatch.setattr("footman.data_dir", lambda: tmp_path / "home")
    monkeypatch.setattr(
        "livery.workshop._verdict.classify",
        lambda *a, **k: Verdict("closed", 12, "closed unmerged", 1),
    )
    with pytest.raises(SystemExit) as caught:
        follow(repo, "feat/1-x", git, interval=0, timeout=5)
    assert caught.value.code == 12
    # And the non-merged ending wrote its bundle.
    assert list((tmp_path / "home" / "diagnostics").glob("*.json"))


# --- the engine with a stub driver ---


class _EngineGit(GitOps):
    """The submit rig's git seam: a push reaches the fake too."""

    def __init__(self, root: Path, fake: FakeForge) -> None:
        super().__init__(root)
        self.fake = fake
        self.auto_settle = True

    def push(self, branch: str) -> None:
        super().push(branch)
        sha = self.fake.push(OWNER, NAME, branch, sha=self.head_sha())
        if self.auto_settle:
            self.fake.settle(OWNER, NAME, sha)


class _StubDriver:
    """An update-shaped driver whose work is one committed file."""

    kind = WorkflowKind.UPDATE
    members: tuple[str, ...] = ()

    def __init__(self, git: GitOps, *, armed: bool) -> None:
        self.name = "update/templates"
        self.armed = armed
        self._git = git
        self.merged_calls = 0

    @property
    def branch(self) -> str:
        return f"workflow/{self.name}"

    @property
    def base(self) -> str:
        return "main"

    def prepare(self) -> Submission | None:
        from livery.workshop._workflow_engine import Submission

        if self._git.current_branch() != self.branch:
            if self._git.local_branch_exists(self.branch):
                self._git.switch(self.branch)
            else:
                self._git.create_branch(self.branch)
        marker = self._git.root / "update.txt"
        if not marker.exists():
            marker.write_text("updated\n")
            self._git.commit_all("chore: the update")
        return Submission(title="chore: the update", body="")

    def on_merged(self) -> None:
        self.merged_calls += 1


@pytest.fixture
def engine_rig(tmp_path: Path) -> tuple[FakeForge, _EngineGit]:
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "--initial-branch=main")
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", str(origin), "clone")
    _git(clone, "config", "user.email", "t@livery.local")
    _git(clone, "config", "user.name", "T")
    (clone / "seed.txt").write_text("seed\n")
    _git(clone, "add", ".")
    _git(clone, "commit", "-m", "chore: seed")
    _git(clone, "push", "-u", "origin", "main")
    fake = FakeForge()
    fake.create_repo(OWNER, NAME, private=True, description="t")
    return fake, _EngineGit(clone, fake)


def test_the_engine_starts_submits_and_runs_on_merged(
    engine_rig: tuple[FakeForge, _EngineGit],
) -> None:
    from livery.workshop._workflow_engine import run_workflow

    fake, git = engine_rig
    driver = _StubDriver(git, armed=True)
    run_workflow(driver, _repo(fake), git, current_user="fake-user")
    pr = _repo(fake).pr.get(1)
    assert pr is not None and pr.merged
    assert driver.merged_calls == 1


def test_a_checkup_on_an_armed_inflight_workflow_refuses(
    engine_rig: tuple[FakeForge, _EngineGit],
) -> None:
    # The idempotent submit disarms when the arm choice is off, so a
    # bare status-check re-run would take the live schedule away.
    from livery.workshop._workflow_engine import run_workflow

    fake, git = engine_rig
    git.auto_settle = False  # CI pending: the armed PR stays open
    driver = _StubDriver(git, armed=True)
    run_workflow(driver, _repo(fake), git, current_user="fake-user")
    pr = _repo(fake).pr.get(1)
    assert pr is not None and not pr.merged
    assert _repo(fake).pr.is_armed(1)
    checkup = _StubDriver(git, armed=False)
    with pytest.raises(_FAILURES) as caught:
        run_workflow(checkup, _repo(fake), git, current_user="fake-user")
    assert "already in flight and armed" in str(caught.value)
    assert _repo(fake).pr.is_armed(1)  # the schedule survived the checkup


def test_an_update_parks_unarmed_while_a_release_flies(
    engine_rig: tuple[FakeForge, _EngineGit],
    capsys: pytest.CaptureFixture[str],
) -> None:
    from livery.workshop._workflow_engine import run_workflow

    fake, git = engine_rig
    # A live release: its branch on both sides, its PR open and armed,
    # CI pending so nothing merges.
    git.auto_settle = False
    git.create_branch("workflow/release/forge")
    (git.root / "r.txt").write_text("r\n")
    git.commit_all("feat: r")
    git.push("workflow/release/forge")
    _repo(fake).pr.open("workflow/release/forge", "main", "feat: r")
    _repo(fake).pr.arm(1, title="feat: r")
    git.switch("main")
    driver = _StubDriver(git, armed=True)
    run_workflow(driver, _repo(fake), git, current_user="fake-user")
    out = capsys.readouterr().out
    assert "submitting" in out and "unarmed" in out
    assert "raises floors" in out  # the note teaches why the re-run is a gain
    update_pr = _repo(fake).pr.find_by_head("workflow/update/templates")
    assert update_pr is not None
    assert not _repo(fake).pr.is_armed(update_pr.number)


def test_a_merged_workflow_is_found_by_sha_after_branch_deletion(
    engine_rig: tuple[FakeForge, _EngineGit],
) -> None:
    # A forge that auto-deletes the head branch strips head_branch off
    # the merged PR; the gather must fall back to the branch head sha
    # or a completed workflow reads as having no PR at all.
    from livery.forge import RepoConfig

    fake, git = engine_rig
    repo = _repo(fake)
    repo.configure(RepoConfig(delete_branch_on_merge=True))
    driver = _StubDriver(git, armed=True)
    from livery.workshop._workflow_engine import run_workflow

    run_workflow(driver, _repo(fake), git, current_user="fake-user")
    merged = repo.pr.get(1)
    assert merged is not None and merged.merged
    assert merged.head_branch == ""  # the forge stripped it at the merge
    assert repo.pr.find_by_head("workflow/update/templates") is None
    states = workflow_states(repo, git)
    assert [wf.state for wf in states] == [WorkflowState.SUCCEEDED]


# --- the dark shells, lit: renderer, picker, configure, engine paths ---


def test_render_workflows_prints_rows_and_the_empty_case(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from livery.workshop._workflow_tasks import render_workflows

    render_workflows(())
    assert "no reserved workflows" in capsys.readouterr().out
    mid_publish = _wf(
        WorkflowState.IN_PROGRESS,
        name="release/forge+workshop",
        author="willem",
    )
    mid_publish = WorkflowStatus(
        **{**mid_publish.__dict__, "tagged": ("forge",), "detail": "publishing"}
    )
    render_workflows((mid_publish,))
    out = capsys.readouterr().out
    assert "release/forge+workshop: in_progress (willem)" in out
    assert "tagged: forge" in out  # partial success is legible


def test_the_interactive_picker_asks_and_silence_stops(
    rig: tuple[FakeForge, GitOps], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, git = rig
    repo = _repo(fake)
    first = _wf(WorkflowState.FAILED, name="release/forge")
    second = _wf(WorkflowState.PREPARING, name="update/templates")
    # Declining the select aborts nothing, ever.
    monkeypatch.setattr("footman.select", lambda message, options: None)
    with pytest.raises(SystemExit) as caught:
        abort_policy(repo, git, (first, second), "", force=False, interactive=True)
    assert "nothing aborted" in str(caught.value)
    # A picked number tears that one down.
    git.create_branch("workflow/update/templates")
    (git.root / "u.txt").write_text("u\n")
    git.commit_all("chore: u")
    git.switch("main")
    monkeypatch.setattr("footman.select", lambda message, options: options[1][1])
    monkeypatch.setattr(
        "livery.workshop._workflow_tasks._reconcile_configuration",
        lambda _git, _sha: None,
    )
    abort_policy(repo, git, (first, second), "", force=False, interactive=True)
    assert not git.local_branch_exists("workflow/update/templates")


def test_contract_config_reads_the_required_context(tmp_path: Path) -> None:
    from livery.workshop._workflow_tasks import contract_config

    (tmp_path / "livery.toml").write_text(
        '[workspace]\n\n[ci]\nrequired_context = "the-gate"\n'
    )
    config = contract_config(tmp_path)
    assert config.required_contexts == ("the-gate",)
    assert config.squash_only is True
    assert config.min_approvals is None  # no owners: no requirement
    (tmp_path / "livery.toml").write_text("[workspace]\n")
    assert contract_config(tmp_path).required_contexts == ("gate",)


def test_the_reconcile_reports_a_refusal_and_never_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from livery.workshop import _workflow_tasks

    monkeypatch.setattr(_workflow_tasks, "_root", lambda: tmp_path)

    def _refused() -> object:
        from types import SimpleNamespace

        return SimpleNamespace(
            code=1,
            stdout="",
            stderr="refused: 403, not an administrator (ADMIN_TOKEN)",
        )

    monkeypatch.setattr(_workflow_tasks, "_spawn_configure", _refused)

    class _NoDiffGit:
        def _run(self, *args: str) -> str:
            raise RuntimeError("no repository here")

    _workflow_tasks._reconcile_configuration(_NoDiffGit(), "abc123")  # type: ignore[arg-type]
    out = capsys.readouterr().out
    assert "workflow.configure" in out and "403" in out


def test_engine_merges_the_base_in_then_proceeds(
    engine_rig: tuple[FakeForge, _EngineGit],
) -> None:
    from livery.workshop._workflow_engine import run_workflow

    fake, git = engine_rig
    # Advance main from a second clone so the driver starts behind.
    other = git.root.parent / "other"
    _git(git.root.parent, "clone", str(git.root.parent / "origin.git"), "other")
    _git(other, "config", "user.email", "o@livery.local")
    _git(other, "config", "user.name", "O")
    (other / "ahead.txt").write_text("ahead\n")
    _git(other, "add", ".")
    _git(other, "commit", "-m", "feat: ahead")
    _git(other, "push", "origin", "main")
    driver = _StubDriver(git, armed=True)
    run_workflow(driver, _repo(fake), git, current_user="fake-user")
    pr = _repo(fake).pr.get(1)
    assert pr is not None and pr.merged
    assert (git.root / "ahead.txt").exists()  # the base came in first


def test_engine_reopens_a_closed_pr_instead_of_duplicating(
    engine_rig: tuple[FakeForge, _EngineGit],
) -> None:
    from livery.workshop._workflow_engine import run_workflow

    fake, git = engine_rig
    git.auto_settle = False
    driver = _StubDriver(git, armed=False)
    run_workflow(driver, _repo(fake), git, current_user="fake-user")
    _repo(fake).pr.close(1)
    git.switch("main")
    again = _StubDriver(git, armed=False)
    run_workflow(again, _repo(fake), git, current_user="fake-user")
    pr = _repo(fake).pr.get(1)
    assert pr is not None and pr.state == "open"  # reopened, not #2
    assert _repo(fake).pr.get(2) is None


def test_engine_says_nothing_to_submit_when_prepare_answers_none(
    engine_rig: tuple[FakeForge, _EngineGit],
    capsys: pytest.CaptureFixture[str],
) -> None:
    from livery.workshop._workflow_engine import run_workflow

    fake, git = engine_rig

    class _Done(_StubDriver):
        def prepare(self) -> Submission | None:
            return None

    run_workflow(_Done(git, armed=True), _repo(fake), git, current_user="fake-user")
    assert "nothing to submit" in capsys.readouterr().out


def test_engine_tidies_its_own_leftover_then_starts_fresh(
    engine_rig: tuple[FakeForge, _EngineGit],
) -> None:
    from livery.forge import RepoConfig
    from livery.workshop._workflow_engine import run_workflow

    fake, git = engine_rig
    _repo(fake).configure(RepoConfig(delete_branch_on_merge=True))
    driver = _StubDriver(git, armed=True)
    run_workflow(driver, _repo(fake), git, current_user="fake-user")
    first = _repo(fake).pr.get(1)
    assert first is not None and first.merged
    git.switch("main")
    # In production the forge IS origin, so its branch deletion removes
    # the real ref; the split rig mirrors that by hand.
    _git(git.root, "push", "origin", ":workflow/update/templates")
    # The fake's squash never reaches the real origin, so main still
    # lacks the marker and the next update genuinely has work.
    again = _StubDriver(git, armed=True)
    run_workflow(again, _repo(fake), git, current_user="fake-user")
    second = _repo(fake).pr.get(2)
    assert second is not None and second.merged  # leftover tidied, fresh PR ran


def test_a_merge_conflict_pulling_the_base_teaches_the_resume(
    engine_rig: tuple[FakeForge, _EngineGit],
) -> None:
    from livery.workshop._workflow_engine import _merge_default

    _fake, git = engine_rig
    other = git.root.parent / "other2"
    _git(git.root.parent, "clone", str(git.root.parent / "origin.git"), "other2")
    _git(other, "config", "user.email", "o@livery.local")
    _git(other, "config", "user.name", "O")
    (other / "seed.txt").write_text("their line\n")
    _git(other, "add", ".")
    _git(other, "commit", "-m", "feat: theirs")
    _git(other, "push", "origin", "main")
    (git.root / "seed.txt").write_text("my line\n")
    git.commit_all("feat: mine")
    with pytest.raises(SystemExit) as caught:
        _merge_default(git, "main")
    assert "conflict" in str(caught.value)


def test_gather_reads_receipts_and_an_unreadable_arm(
    engine_rig: tuple[FakeForge, _EngineGit], monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._workflow_state import gather, status_of

    fake, git = engine_rig
    git.auto_settle = False
    name = "release/forge+workshop"
    git.create_branch(f"workflow/{name}")
    (git.root / "r.txt").write_text("r\n")
    git.commit_all("feat: r")
    git.push(f"workflow/{name}")
    repo = _repo(fake)
    pr = repo.pr.open(f"workflow/{name}", "main", "feat: r")
    # An unreadable arm is None, and the detail says so.
    monkeypatch.setattr(
        repo.pr,
        "is_armed",
        lambda number: (_ for _ in ()).throw(ForgeError("403", status=403)),
    )
    signals = gather(repo, git, name)
    assert signals is not None and signals.armed is None
    assert "unreadable" in signals.detail
    monkeypatch.undo()
    # Merged with one member tagged: in progress, the ledger legible.
    fresh = repo.pr.get(pr.number)
    assert fresh is not None
    fake.settle(OWNER, NAME, fresh.head_sha)
    repo.pr.merge_now(pr.number, title="feat: r")
    fake.create_tag(OWNER, NAME, "packages/forge/v9.9.9")
    wf = status_of(gather(repo, git, name), name)
    assert wf.state is WorkflowState.IN_PROGRESS
    assert wf.tagged == ("forge",)
    fake.create_tag(OWNER, NAME, "packages/workshop/v9.9.9")
    wf = status_of(gather(repo, git, name), name)
    assert wf.state is WorkflowState.SUCCEEDED


def test_active_names_degrade_to_local_when_the_remote_is_gone(
    rig: tuple[FakeForge, GitOps], monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._workflow_state import active_workflow_names

    fake, git = rig
    git.create_branch("workflow/update/templates")
    (git.root / "u.txt").write_text("u\n")
    git.commit_all("chore: u")
    monkeypatch.setattr(
        type(git),
        "remote_branches",
        lambda self, prefix: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    assert active_workflow_names(_repo(fake), git) == ("update/templates",)
