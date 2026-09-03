"""Governance in the workshop: refusals and ladders first."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from footman import Failed

from livery.forge import RepoConfig, Repository
from livery.forge.testing import FakeForge
from livery.workshop._git_ops import GitOps
from livery.workshop._governance import (
    codeowners_file,
    governance_config,
    governance_entries,
    owners_of,
    unknown_owners,
)

_FAILURES = (SystemExit, Failed)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def _workspace(tmp_path: Path, *, owners: bool = True) -> Path:
    root = tmp_path / "ws"
    (root / "packages" / "core").mkdir(parents=True)
    owner_block = (
        '[owners]\nusers = ["alice"]\nteams = ["reviewers"]\napprovals = 2\n'
        if owners
        else ""
    )
    (root / "workshop.toml").write_text(
        f'[workspace]\n\n[forge]\nkind = "github"\nowner = "acme"\n\n{owner_block}'
    )
    (root / "packages" / "core" / "workshop.toml").write_text(
        'type = "python"\nname = "livery-core"\n\n[owners]\nusers = ["bob"]\n'
    )
    (root / "packages" / "core" / "pyproject.toml").write_text(
        '[project]\nname = "livery-core"\nversion = "0.1.0"\n'
    )
    return root


def test_owner_declarations_parse_with_their_defaults() -> None:
    users, teams, approvals = owners_of(
        {"owners": {"users": ["a"], "teams": ["t"], "approvals": 3}}
    )
    assert users == ("a",) and teams == ("t",) and approvals == 3
    users, teams, approvals = owners_of({"owners": {"users": ["a"]}})
    # Declared owners document and route review; they gate nothing
    # until a count is asked for, so a solo repo never deadlocks.
    assert approvals == 0
    users, teams, approvals = owners_of({})
    assert users == () and teams == () and approvals == 0


def test_entries_guard_the_governance_files_and_qualify_teams(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    entries = governance_entries(root)
    paths = [entry.path for entry in entries]
    # Config-as-code guarded by itself: the contract and the file.
    assert "/workshop.toml" in paths
    assert "/.github/CODEOWNERS" in paths
    assert "/packages/core/" in paths
    guard = next(entry for entry in entries if entry.path == "/workshop.toml")
    assert guard.owners == ("alice", "acme/reviewers")
    assert guard.min_approvals == 2


def test_no_declarations_means_no_file_and_no_requirement(
    tmp_path: Path,
) -> None:
    root = tmp_path / "bare"
    (root / "packages").mkdir(parents=True)
    (root / "workshop.toml").write_text(
        '[workspace]\n\n[forge]\nkind = "github"\nowner = "acme"\n'
    )
    assert governance_entries(root) == ()
    assert codeowners_file(root) is None
    assert governance_config(root) == RepoConfig()


def test_the_config_takes_the_highest_declared_count(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    config = governance_config(root)
    assert config.min_approvals == 2  # the root guard's, over core's 0
    assert config.require_codeowner_review is True

    # A workspace whose declarations all sit at zero documents
    # ownership without gating: no codeowner requirement either, or
    # a forge that forbids self-approval deadlocks a solo repo.
    (root / "workshop.toml").write_text(
        (root / "workshop.toml").read_text().replace("approvals = 2", "approvals = 0")
    )
    config = governance_config(root)
    assert config.min_approvals == 0
    assert config.require_codeowner_review is False


def test_the_rendered_file_is_the_forges_dialect(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    rendered = codeowners_file(root)
    assert rendered is not None
    assert rendered.path == ".github/CODEOWNERS"
    assert "/packages/core/ @bob" in rendered.content
    assert "@acme/reviewers" in rendered.content


def test_unknown_owners_are_named_with_where_they_are_declared(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    fake = FakeForge()
    fake.set_members("acme", ("alice", "fake-user"))
    fake.set_teams("acme", ("reviewers",))
    # bob is declared on core and unknown to the forge.
    missing = unknown_owners(root, fake, "acme")
    assert len(missing) == 1
    assert "user bob" in missing[0] and "/packages/core/" in missing[0]
    fake.set_members("acme", ("alice", "bob"))
    assert unknown_owners(root, fake, "acme") == ()


def test_the_admin_ladder_prefers_the_admin_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._forge_lane import admin_forge

    root = _workspace(tmp_path)
    monkeypatch.delenv("GITHUB_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "everyday")
    _forge, var = admin_forge(root)
    assert var == ""  # the fallback: everyday token, nothing extra
    monkeypatch.setenv("GITHUB_ADMIN_TOKEN", "admin")
    _forge, var = admin_forge(root)
    assert var == "GITHUB_ADMIN_TOKEN"


def _reconcile_rig(tmp_path: Path) -> tuple[Path, GitOps]:
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "--initial-branch=main")
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", str(origin), "clone")
    _git(clone, "config", "user.email", "t@l")
    _git(clone, "config", "user.name", "T")
    (clone / "workshop.toml").write_text(
        '[workspace]\n\n[forge]\nkind = "github"\nowner = "acme"\n'
    )
    (clone / "seed.txt").write_text("s\n")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-m", "chore: seed")
    _git(clone, "push", "-u", "origin", "main")
    return clone, GitOps(clone)


def test_the_reconcile_is_silent_when_provably_unneeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _workflow_tasks

    root, git = _reconcile_rig(tmp_path)
    monkeypatch.setattr(
        "livery.workshop._layers.workspace_root", lambda start=None: root
    )
    monkeypatch.chdir(root)
    # A branch that never touched a governance path.
    _git(root, "checkout", "-b", "feat/1-plain")
    (root / "plain.txt").write_text("p\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "feat: plain")
    sha = git.head_sha()
    _git(root, "checkout", "main")
    from types import SimpleNamespace

    ran: list[str] = []

    def _spawn() -> SimpleNamespace:
        ran.append("ran")
        return SimpleNamespace(code=0, stdout="", stderr="")

    monkeypatch.setattr(_workflow_tasks, "_spawn_configure", _spawn)
    _workflow_tasks._reconcile_configuration(git, sha)
    assert ran == []  # untouched: provably unneeded, so silent


def test_the_reconcile_runs_when_governance_paths_moved(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from livery.workshop import _workflow_tasks

    root, git = _reconcile_rig(tmp_path)
    monkeypatch.setattr(
        "livery.workshop._layers.workspace_root", lambda start=None: root
    )
    monkeypatch.chdir(root)
    _git(root, "checkout", "-b", "feat/2-gov")
    (root / "workshop.toml").write_text(
        '[workspace]\n\n[forge]\nkind = "github"\nowner = "acme"\n\n'
        '[owners]\nusers = ["alice"]\n'
    )
    _git(root, "commit", "-am", "feat: owners")
    sha = git.head_sha()
    _git(root, "checkout", "main")
    ran: list[str] = []

    def _refusing() -> SimpleNamespace:
        ran.append("ran")
        return SimpleNamespace(code=1, stdout="", stderr="refused: ADMIN_TOKEN")

    monkeypatch.setattr(_workflow_tasks, "_spawn_configure", _refusing)
    _workflow_tasks._reconcile_configuration(git, sha)
    out = capsys.readouterr().out
    assert ran == ["ran"]
    # The reason arrives verbatim, never summarised away.
    assert "refused: ADMIN_TOKEN" in out
    assert "administrator" in out and "workflow.configure" in out

    # Any other failure carries its reason verbatim too: the text is
    # never read as a boolean to pick a message.
    def _soft() -> SimpleNamespace:
        return SimpleNamespace(code=1, stdout="", stderr="connection reset")

    monkeypatch.setattr(_workflow_tasks, "_spawn_configure", _soft)
    _workflow_tasks._reconcile_configuration(git, sha)
    out = capsys.readouterr().out
    assert "connection reset" in out and "workflow.configure" in out


def test_the_check_title_task_refuses_a_drifted_title(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from livery.workshop._release_driver import workflow_release_check_title

    root, _git_seam = _reconcile_rig(tmp_path)
    monkeypatch.setattr(
        "livery.workshop._layers.workspace_root", lambda start=None: root
    )
    monkeypatch.chdir(root)
    _git(root, "checkout", "-b", "workflow/release/core")
    (root / "r.txt").write_text("r\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "chore(release): livery-core v0.2.0")
    workflow_release_check_title(title="chore(release): released livery-core v0.2.0")
    assert "matches" in capsys.readouterr().out
    with pytest.raises(_FAILURES) as caught:
        workflow_release_check_title(title="chore(release): released nothing")
    assert "does not match" in str(caught.value)


def test_awaiting_approvals_is_a_clean_stop_naming_the_reviewers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.forge import Protection
    from livery.workshop._verdict import classify

    root, git = _reconcile_rig(tmp_path)
    (root / "workshop.toml").write_text(
        '[workspace]\n\n[forge]\nkind = "github"\nowner = "acme"\n\n'
        '[owners]\nusers = ["alice", "author"]\n'
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "chore: owners")
    _git(root, "push", "origin", "main")
    monkeypatch.setattr(
        "livery.workshop._layers.workspace_root", lambda start=None: root
    )
    monkeypatch.chdir(root)
    fake = FakeForge()
    fake.create_repo("acme", "ws", private=True, description="t")
    repo = fake.repository("acme", "ws")
    _git(root, "checkout", "-b", "feat/1-work")
    (root / "workshop.toml").write_text(
        '[workspace]\n\n[forge]\nkind = "github"\nowner = "acme"\n\n'
        '[owners]\nusers = ["alice", "author"]\napprovals = 1\n'
    )
    _git(root, "commit", "-am", "feat: touch governance")
    _git(root, "push", "-u", "origin", "feat/1-work")
    sha = git.head_sha()
    fake.push("acme", "ws", "feat/1-work", sha=sha)
    fake.settle("acme", "ws", sha)
    repo.pr.open("feat/1-work", "main", "feat: touch governance")
    # Protection first: the fake's auto-merge honours it, so the arm
    # parks instead of firing.
    fake.set_protection("acme", "ws", "main", Protection(required_approvals=1))
    repo.pr.arm(1, title="feat: touch governance")
    # Green, armed, unmerged, with protection demanding a review the
    # PR does not have: nobody's error, exit 0's territory.
    verdict = classify(repo, "feat/1-work", git, grace_spent=True)
    assert verdict.state == "awaiting-approvals"
    assert verdict.exit_code == 0
    assert "1 approval" in verdict.detail
    assert "alice" in verdict.detail  # eligible: owner, not the author
    assert "arm survives" in verdict.detail
    # An approval satisfies it: the reading moves on (here, stalled,
    # because the fake never merges without its own trigger).
    fake.review("acme", "ws", 1, "alice", "approved")
    fake.settle("acme", "ws", sha)  # the review was the last gate
    verdict = classify(repo, "feat/1-work", git, grace_spent=True)
    assert verdict.state == "merged"


def test_unreadable_protection_never_asserts_a_review_blocker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._verdict import classify

    root, git = _reconcile_rig(tmp_path)
    monkeypatch.setattr(
        "livery.workshop._layers.workspace_root", lambda start=None: root
    )
    monkeypatch.chdir(root)
    fake = FakeForge()
    fake.create_repo("acme", "ws", private=True, description="t")
    repo = fake.repository("acme", "ws")
    _git(root, "checkout", "-b", "feat/2-work")
    (root / "w.txt").write_text("w\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "feat: work")
    _git(root, "push", "-u", "origin", "feat/2-work")
    sha = git.head_sha()
    fake.push("acme", "ws", "feat/2-work", sha=sha)
    fake.settle("acme", "ws", sha)
    repo.pr.open("feat/2-work", "main", "feat: work")
    from livery.forge import Protection
    from livery.forge.testing import _fake as fake_module

    fake.set_protection("acme", "ws", "main", Protection(required_approvals=1))
    repo.pr.arm(1, title="feat: work")

    def _unreadable(self: object, branch: str) -> object:
        from livery.forge import ForgeError

        raise ForgeError("admin required to read protection", status=403)

    monkeypatch.setattr(fake_module._FakeRepository, "protection", _unreadable)
    # Protection exists but cannot be read: the softer stalled
    # wording holds, a blocker is never asserted from a state that
    # could not be read.
    verdict = classify(repo, "feat/2-work", git, grace_spent=True)
    assert verdict.state == "stalled"


def _configure_rig(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, FakeForge, Repository]:
    """workflow.configure against the fake through the admin seams."""
    root = _workspace(tmp_path)
    monkeypatch.setattr(
        "livery.workshop._layers.workspace_root", lambda start=None: root
    )
    monkeypatch.chdir(root)
    fake = FakeForge()
    fake.create_repo("acme", "ws", private=True, description="t")
    fake.set_members("acme", ("alice", "bob"))
    fake.set_teams("acme", ("reviewers",))
    repo = fake.repository("acme", "ws")
    monkeypatch.setattr(
        "livery.workshop._forge_lane.admin_repository",
        lambda _root: (repo, "GITHUB_ADMIN_TOKEN"),
    )
    monkeypatch.setattr(
        "livery.workshop._forge_lane.admin_forge",
        lambda _root: (fake, "GITHUB_ADMIN_TOKEN"),
    )
    return root, fake, repo


def test_configure_refuses_unknown_owners_before_applying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._workflow_tasks import workflow_configure

    _root, fake, repo = _configure_rig(tmp_path, monkeypatch)
    fake.set_members("acme", ("alice",))  # bob is declared and unknown
    with pytest.raises(_FAILURES) as caught:
        workflow_configure()
    text = str(caught.value)
    assert "does not know" in text and "user bob" in text
    assert repo.protection("main") is None  # nothing was applied


def test_configure_degrades_the_approval_count_with_a_note(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from livery.workshop._workflow_tasks import workflow_configure

    _root, fake, repo = _configure_rig(tmp_path, monkeypatch)
    real = fake.supports
    monkeypatch.setattr(
        fake, "supports", lambda c: False if c == "min_approvals" else real(c)
    )
    workflow_configure()
    out = capsys.readouterr().out
    assert "cannot enforce approval counts" in out
    assert "asserted from the contract" in out
    protection = repo.protection("main")
    assert protection is not None
    assert protection.required_approvals == 0  # degraded, not asserted
    assert "gate" in protection.required_contexts  # the rest applied


def test_configure_degrades_required_contexts_like_gitlab(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from livery.workshop._workflow_tasks import workflow_configure

    _root, fake, _repo = _configure_rig(tmp_path, monkeypatch)
    monkeypatch.setattr(
        fake,
        "supports",
        lambda c: c not in ("min_approvals", "required_contexts"),
    )
    workflow_configure()  # never an uncaught Unsupported
    out = capsys.readouterr().out
    assert "cannot name required check contexts" in out
    assert "asserted from the contract" in out


def test_configure_teaches_the_ladder_on_a_refused_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.forge import ForgeError
    from livery.workshop._workflow_tasks import workflow_configure

    _root, _fake, repo = _configure_rig(tmp_path, monkeypatch)

    def _refuse(config: object) -> None:
        raise ForgeError("403 admin required", status=403)

    monkeypatch.setattr(repo, "configure", _refuse)
    with pytest.raises(_FAILURES) as caught:
        workflow_configure()
    text = str(caught.value)
    assert "403 admin required" in text  # the server's words, verbatim
    assert "GITHUB_ADMIN_TOKEN" in text and "GITLAB_ADMIN_TOKEN" in text


def test_configure_names_an_unpredicted_decline_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.forge import Unsupported
    from livery.workshop._workflow_tasks import workflow_configure

    _root, _fake, repo = _configure_rig(tmp_path, monkeypatch)

    def _decline(config: object) -> None:
        raise Unsupported("cannot store that (capability: whatever)")

    monkeypatch.setattr(repo, "configure", _decline)
    with pytest.raises(_FAILURES) as caught:
        workflow_configure()
    text = str(caught.value)
    assert "declined part of the configuration" in text
    assert "capability: whatever" in text  # verbatim, no token teaching
    assert "ADMIN_TOKEN" not in text


def _contract_root(
    tmp_path: Path,
    kind: str,
    *,
    url: str = "",
    runners: list[str] | None = None,
    floor: str = "3.11",
) -> Path:
    """A scratch workspace whose contract carries the CI facts."""
    root = tmp_path / f"contract-{kind}"
    root.mkdir(exist_ok=True)
    lines = [
        "[workspace]",
        'layers = ["livery.workshop"]',
        "",
        "[forge]",
        f'kind = "{kind}"',
        'owner = "owner"',
    ]
    if url:
        lines.append(f'url = "{url}"')
    labels = ", ".join(f'"{label}"' for label in (runners or ["ubuntu-latest"]))
    lines += ["", "[ci]", f"runners = [{labels}]", 'required_context = "gate"']
    (root / "workshop.toml").write_text("\n".join(lines) + "\n")
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "scratch"\nrequires-python = ">={floor}"\n'
    )
    return root


def test_the_release_title_job_receives_the_actual_title(tmp_path: Path) -> None:
    from livery.workshop._ci_generate import generate

    for kind in ("github", "gitea"):
        gate = generate(_contract_root(tmp_path, kind))[f".{kind}/workflows/ci.yml"]
        job = gate.split("release-title:")[1]
        # The f-string emitter must collapse to the two-brace Actions
        # expression; a single-braced `${ ... }` is a literal string
        # the check would compare against instead of the title.
        assert "TITLE: ${{ github.event.pull_request.title }}" in job
        assert "${ github" not in gate
        # check-title diffs against origin/main: full history needed.
        assert "fetch-depth: 0" in job
        assert "sync" in job and "--no-sync" in job  # locked toolchain


def test_governance_jobs_are_runnable_where_they_land(tmp_path: Path) -> None:
    from livery.workshop._ci_generate import generate

    github = generate(_contract_root(tmp_path, "github"))
    gov = github[".github/workflows/governance.yml"]
    assert "uv sync --locked" in gov
    assert "uv run --no-sync fm workflow.configure" in gov
    # The admin secret is mounted in the governance job and nowhere
    # else.
    for path, content in github.items():
        if "governance" not in path:
            assert "FORGE_ADMIN_TOKEN" not in content
    assert "FORGE_ADMIN_TOKEN" in gov

    gitea = generate(_contract_root(tmp_path, "gitea", runners=["host-linux"]))
    gov = gitea[".gitea/workflows/governance.yml"]
    # act_runner host mode: uv from its installer, on the configured
    # runner label, no setup actions.
    assert "astral.sh/uv/install.sh" in gov
    assert "runs-on: host-linux" in gov
    assert "setup-uv" not in gov
    title_job = gitea[".gitea/workflows/ci.yml"].split("release-title:")[1]
    assert "runs-on: host-linux" in title_job
    assert "astral.sh/uv/install.sh" in title_job

    gitlab = generate(_contract_root(tmp_path, "gitlab", floor="3.12"))
    section = gitlab[".gitlab-ci.yml"].split("governance-apply:")[1]
    # Without an image the job lands on the runner default, where uv
    # does not exist.
    assert "image: ghcr.io/astral-sh/uv:python3.12-bookworm" in section
    assert "uv sync --locked" in section
    assert "uv run --no-sync fm workflow.configure" in section


def test_doctor_prints_the_ladder_and_the_owner_verdicts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from livery.workshop._ci_tasks import doctor_flow

    root = _workspace(tmp_path)
    monkeypatch.setattr(
        "livery.workshop._layers.workspace_root", lambda start=None: root
    )
    monkeypatch.chdir(root)
    monkeypatch.delenv("GITHUB_ADMIN_TOKEN", raising=False)
    fake = FakeForge()
    fake.set_members("acme", ("alice", "bob"))
    fake.set_teams("acme", ("reviewers",))
    doctor_flow(fake)
    out = capsys.readouterr().out
    assert "admin ladder: GITHUB_ADMIN_TOKEN unset" in out
    assert "every declared owner exists" in out

    monkeypatch.setenv("GITHUB_ADMIN_TOKEN", "x")
    fake.set_members("acme", ("alice",))  # bob becomes unknown
    doctor_flow(fake)
    out = capsys.readouterr().out
    assert "admin ladder: GITHUB_ADMIN_TOKEN set" in out
    assert "unknown user bob" in out

    def _broken(owner: str) -> tuple[str, ...]:
        raise RuntimeError("boom")

    monkeypatch.setattr(fake, "members", _broken)
    doctor_flow(fake)
    out = capsys.readouterr().out
    assert "owners: not checked (boom)" in out  # the check fails open
