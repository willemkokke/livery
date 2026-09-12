"""The merge point's release dispatch: refusals first, then the dispatch."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from livery.forge import ForgeError
from livery.forge.testing import FakeForge
from livery.workshop._git_ops import GitOps
from livery.workshop._publish import MANIFEST
from livery.workshop._release_driver import await_wave, dispatch_flow

OWNER, NAME = "owner", "repo"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture
def rig(tmp_path: Path) -> tuple[FakeForge, Path, GitOps]:
    """A clone with main pushed, and a fake forge knowing the same main."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "--initial-branch=main", str(origin))
    root = tmp_path / "ws"
    _git(tmp_path, "clone", "-q", str(origin), str(root))
    _git(root, "config", "user.name", "tester")
    _git(root, "config", "user.email", "tester@example.invalid")
    (root / "README.md").write_text("the repository\n")
    _git(root, "add", "README.md")
    _git(root, "commit", "-qm", "init")
    _git(root, "push", "-q", "-u", "origin", "main")
    fake = FakeForge()
    fake.create_repo(OWNER, NAME)
    fake.push(OWNER, NAME, "main", sha=_git(root, "rev-parse", "HEAD").strip())
    return fake, root, GitOps(root)


def _stamp(root: Path, fake: FakeForge, *members: tuple[str, str]) -> str:
    """Commit a release manifest naming *members*; the stamping sha."""
    (root / MANIFEST).write_text(
        json.dumps({"members": [{"dir": d, "version": v} for d, v in members]})
    )
    _git(root, "add", MANIFEST)
    _git(root, "commit", "-qm", "chore(release): released the members")
    _git(root, "push", "-q", "origin", "main")
    sha = _git(root, "rev-parse", "HEAD").strip()
    fake.push(OWNER, NAME, "main", sha=sha)
    return sha


# --- refusals, each green and named ----------------------------------------------


def test_no_manifest_is_nothing_to_dispatch(
    rig: tuple[FakeForge, Path, GitOps],
) -> None:
    fake, root, git = rig
    repo = fake.repository(OWNER, NAME)
    assert dispatch_flow(root, repo, git) == [
        "  no release manifest at HEAD: nothing to dispatch"
    ]
    assert repo.checks.runs(event="workflow_dispatch") == ()


def test_every_receipt_cut_is_nothing_to_dispatch(
    rig: tuple[FakeForge, Path, GitOps],
) -> None:
    fake, root, git = rig
    _stamp(root, fake, ("thing", "1.2.0"))
    _git(root, "tag", "packages/thing/v1.2.0")
    _git(root, "push", "-q", "origin", "packages/thing/v1.2.0")
    (line,) = dispatch_flow(root, fake.repository(OWNER, NAME), git)
    assert line == "  every receipt is cut (packages/thing/v1.2.0): nothing to dispatch"


def test_a_wave_in_flight_is_reported_not_redispatched(
    rig: tuple[FakeForge, Path, GitOps],
) -> None:
    fake, root, git = rig
    _stamp(root, fake, ("thing", "1.2.0"))
    repo = fake.repository(OWNER, NAME)
    repo.checks.dispatch("release.yml", ref="main")
    (live,) = repo.checks.runs(event="workflow_dispatch")
    (line,) = dispatch_flow(root, repo, git)
    assert line == (
        f"  the wave is already in flight: run {live.id};"
        " uncut so far: packages/thing/v1.2.0"
    )
    assert len(repo.checks.runs(event="workflow_dispatch")) == 1


def test_a_refused_dispatch_names_the_reason(
    rig: tuple[FakeForge, Path, GitOps], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, root, git = rig
    _stamp(root, fake, ("thing", "1.2.0"))
    repo = fake.repository(OWNER, NAME)

    def refuse(*args: object, **kwargs: object) -> None:
        raise ForgeError("dispatch needs actions: write", status=403)

    monkeypatch.setattr(repo.checks, "dispatch", refuse)
    (line,) = dispatch_flow(root, repo, git)
    assert line.startswith(
        "  the forge refused the dispatch: dispatch needs actions: write"
    )
    assert "uncut: packages/thing/v1.2.0" in line


def test_an_accepted_dispatch_without_a_run_times_out_naming_the_rerun(
    rig: tuple[FakeForge, Path, GitOps], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, root, git = rig
    _stamp(root, fake, ("thing", "1.2.0"))
    repo = fake.repository(OWNER, NAME)
    monkeypatch.setattr(repo.checks, "dispatch", lambda *a, **k: None)
    (line,) = dispatch_flow(root, repo, git, timeout=0.2, interval=0.05)
    assert "no wave run appeared within 0s" in line
    assert "workflow.release.dispatch" in line


# --- the dispatch --------------------------------------------------------------


def test_the_wave_is_dispatched_at_the_stamping_commit_and_confirmed(
    rig: tuple[FakeForge, Path, GitOps], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, root, git = rig
    stamping = _stamp(root, fake, ("thing", "1.2.0"), ("other", "0.3.0"))
    # An unrelated merge lands after the stamp: the wave still goes
    # to the stamping commit, never to HEAD.
    (root / "README.md").write_text("moved on\n")
    _git(root, "commit", "-qam", "docs: unrelated")
    _git(root, "push", "-q", "origin", "main")
    fake.push(OWNER, NAME, "main", sha=_git(root, "rev-parse", "HEAD").strip())
    repo = fake.repository(OWNER, NAME)
    asked: list[tuple[str, str, dict[str, str] | None]] = []
    real = repo.checks.dispatch

    def record(
        workflow: str, *, ref: str, inputs: dict[str, str] | None = None
    ) -> None:
        asked.append((workflow, ref, inputs))
        real(workflow, ref=ref, inputs=inputs)

    monkeypatch.setattr(repo.checks, "dispatch", record)
    (line,) = dispatch_flow(root, repo, git, timeout=5, interval=0.05)
    assert asked == [("release.yml", "main", {"ref": stamping})]
    (run,) = repo.checks.runs(event="workflow_dispatch")
    assert line == (
        f"  dispatched the wave at {stamping[:12]}: run {run.id};"
        " uncut: packages/thing/v1.2.0, packages/other/v0.3.0"
    )
    # The second call reports the wave in flight, never a second one.
    (again,) = dispatch_flow(root, repo, git)
    assert again.startswith(f"  the wave is already in flight: run {run.id}")


def test_an_older_squash_is_dispatched_at_itself_with_a_named_driver(
    rig: tuple[FakeForge, Path, GitOps], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The recovery for a squash a later release moved past: the wave
    # goes to the older squash, reads its manifest there, and names
    # the released workshop that drives it in place of the squash's.
    fake, root, git = rig
    older = _stamp(root, fake, ("thing", "1.2.0"))
    _stamp(root, fake, ("other", "0.3.0"))
    repo = fake.repository(OWNER, NAME)
    asked: list[tuple[str, str, dict[str, str] | None]] = []
    real = repo.checks.dispatch

    def record(
        workflow: str, *, ref: str, inputs: dict[str, str] | None = None
    ) -> None:
        asked.append((workflow, ref, inputs))
        real(workflow, ref=ref, inputs=inputs)

    monkeypatch.setattr(repo.checks, "dispatch", record)
    (line,) = dispatch_flow(
        root, repo, git, at=older, workshop="0.2.0", timeout=5, interval=0.05
    )
    assert asked == [("release.yml", "main", {"ref": older, "workshop": "0.2.0"})]
    (run,) = repo.checks.runs(event="workflow_dispatch")
    assert line == (
        f"  dispatched the wave at {older[:12]} driven by 0.2.0: run {run.id};"
        " uncut: packages/thing/v1.2.0"
    )


def test_the_rendered_release_workflow_carries_the_driver_pin() -> None:
    from livery.workshop._ci_generate import generate

    release = generate(Path(__file__).resolve().parents[3])[
        ".github/workflows/release.yml"
    ]
    assert "      workshop:\n" in release
    assert 'default: ""' in release
    assert release.count("- name: Pin the driver") == 2  # publish, templates
    assert "if: inputs.workshop != ''" in release
    assert 'uv pip install "livery-workshop==${{ inputs.workshop }}"' in release
    # The receipt push needs a credential with the workflows scope at
    # a squash the tip has moved past; the job token cannot push it.
    assert "token: ${{ secrets.FORGE_TOKEN || github.token }}" in release
    # A token when the repository has one, trusted publishing otherwise.
    assert "UV_PUBLISH_TOKEN: ${{ secrets.PYPI_TOKEN }}" in release


def test_await_wave_answers_a_wave_newer_than_the_merge_or_none(
    rig: tuple[FakeForge, Path, GitOps],
) -> None:
    fake, root, _ = rig
    repo = fake.repository(OWNER, NAME)
    assert await_wave(repo, before=set(), timeout=0.2, interval=0.05) is None
    repo.checks.dispatch("release.yml", ref="main")
    (run,) = repo.checks.runs(event="workflow_dispatch")
    # A wave that already finished still counts: the confirmation is
    # the run's existence, never its liveness.
    fake.settle(OWNER, NAME, _git(root, "rev-parse", "HEAD").strip())
    assert await_wave(repo, before=set(), timeout=1, interval=0.05) == run.id
    # A wave known before the merge is not this merge's.
    assert await_wave(repo, before={run.id}, timeout=0.2, interval=0.05) is None
