"""``fm ci.e2e``'s provisioning: refusals first, then the idempotent path."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from livery.forge import Repository
from livery.forge.testing import FakeForge
from livery.workshop import _e2e

_FAILURES = (BaseException,)


def test_an_unbuilt_forge_lane_refuses_naming_the_one_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITEA_URL", "http://gitea.example")
    monkeypatch.setenv("GITEA_TOKEN", "t")
    with pytest.raises(_FAILURES, match="gitea is the one local lane"):
        _e2e.provision("gitlab")


def test_missing_credentials_teach_the_dev_up_verb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITEA_URL", raising=False)
    monkeypatch.delenv("GITEA_TOKEN", raising=False)
    with pytest.raises(_FAILURES, match=r"forge\.dev\.up"):
        _e2e.provision("gitea")


def test_provisioning_creates_then_reuses_and_writes_the_secret(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GITEA_URL", "http://gitea.example")
    monkeypatch.setenv("GITEA_TOKEN", "the-lane-token")
    fake = FakeForge()
    monkeypatch.setattr(
        "livery.forge.GiteaForge.connect",
        staticmethod(lambda url, token: fake),
    )
    _e2e.provision("gitea")
    first = capsys.readouterr().out
    assert f"created {_e2e.E2E_OWNER}/{_e2e.E2E_REPO}" in first
    assert "UV_PUBLISH_TOKEN" in first
    # The fake's private state is the assertion surface: secrets are
    # write-only through the protocol, by design.
    state = fake._repos[(_e2e.E2E_OWNER, _e2e.E2E_REPO)]
    for key in ("UV_PUBLISH_TOKEN", "FORGE_TOKEN", "FORGE_ADMIN_TOKEN"):
        assert state.secrets[key] == "the-lane-token"
    # Re-running is the recovery procedure: the second pass reuses.
    _e2e.provision("gitea")
    assert f"reusing {_e2e.E2E_OWNER}/{_e2e.E2E_REPO}" in capsys.readouterr().out


def test_the_registration_gate_sees_this_source_checkout() -> None:
    # The verb registers only beside the workshop's own tests: the
    # path arithmetic must find this very suite.
    assert _e2e._WORKSHOP_TESTS.is_dir()
    assert (_e2e._WORKSHOP_TESTS / "test_workshop_e2e.py").is_file()


def test_a_hostless_alias_teaches_the_one_liner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.request

    def refuse(*args: object, **kwargs: object) -> object:
        raise OSError("unreachable")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    with pytest.raises(_FAILURES, match="/etc/hosts"):
        _e2e._require_host_alias()


# --- the dev-wheel pins: refusals first, then the read -----------------------

HEAD = "05482de" + "0" * 33
WHEEL = "livery_{member}-{version}-py3-none-any.whl"


def _wheel(root: Path, member: str, version: str, *, age: float = 0.0) -> Path:
    dist = root / "packages" / member / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    wheel = dist / WHEEL.format(member=member, version=version)
    wheel.write_bytes(b"")
    stamp = time.time() - age
    os.utime(wheel, (stamp, stamp))
    return wheel


def test_dev_pins_refuse_a_member_without_a_wheel(tmp_path: Path) -> None:
    (tmp_path / "packages" / "workshop" / "dist").mkdir(parents=True)
    with pytest.raises(_FAILURES, match="built nothing for workshop"):
        _e2e._dev_pins(tmp_path, HEAD)


def test_dev_pins_refuse_a_wheel_another_commit_built(tmp_path: Path) -> None:
    # The stale case the loop measured: a previous pass's wheel, or
    # another branch's, must never pin the loop to yesterday's bytes.
    for member in _e2e.DEV_MEMBERS:
        _wheel(tmp_path, member, "0.2.0.dev96+feat.289.loop.9f8f0d0.20260907")
    with pytest.raises(
        _FAILURES, match=r"built from 9f8f0d0, not from HEAD 05482de0000"
    ):
        _e2e._dev_pins(tmp_path, HEAD)


def test_dev_pins_read_this_commits_newest_wheel(tmp_path: Path) -> None:
    for member in _e2e.DEV_MEMBERS:
        _wheel(tmp_path, member, "0.2.0.dev96+feat.289.loop.9f8f0d0.20260907", age=60)
        _wheel(tmp_path, member, "0.2.0.dev72+feat.314.profile.05482de.20260909")
    _wheel(tmp_path, "footman", "0.53.0.dev14+feat.314.profile.05482de.20260909.dirty")
    pins = _e2e._dev_pins(tmp_path, HEAD)
    assert pins == {
        "livery-workshop": "0.2.0.dev72+feat.314.profile.05482de.20260909",
        "livery-forge": "0.2.0.dev72+feat.314.profile.05482de.20260909",
        "livery-toolroom": "0.2.0.dev72+feat.314.profile.05482de.20260909",
        # The newest wheel wins, dirty or not: it is what the act built.
        "livery-footman": "0.53.0.dev14+feat.314.profile.05482de.20260909.dirty",
    }


# --- the template source follows the invoking worktree -----------------------

CONTRACT = (
    "[workspace]\n"
    'layers = ["livery.workshop"]\n'
    'templates = "/old/worktree/packages/workshop/src/livery/workshop/templates"\n'
    "\n"
    "[forge]\n"
    'kind = "gitea"\n'
)


def test_point_templates_refuses_a_contract_without_a_source() -> None:
    bare = '[workspace]\nlayers = ["livery.workshop"]\n'
    with pytest.raises(_FAILURES, match="names no template source"):
        _e2e._point_templates(bare, Path("/new/templates"))


def test_point_templates_rewrites_the_source_and_settles() -> None:
    pointed = _e2e._point_templates(CONTRACT, Path("/new/templates"))
    assert 'templates = "/new/templates"\n' in pointed
    assert "/old/worktree" not in pointed
    assert pointed.startswith('[workspace]\nlayers = ["livery.workshop"]\n')
    assert pointed.endswith('[forge]\nkind = "gitea"\n')
    assert _e2e._point_templates(pointed, Path("/new/templates")) == pointed


# --- the proofs read the runs' logs: the refusals first ------------------------

_SHA = "c" * 40


def _proof_rig() -> tuple[FakeForge, Repository]:
    fake = FakeForge()
    repo = fake.create_repo(_e2e.E2E_OWNER, _e2e.E2E_REPO, private=False)
    return fake, repo


def test_a_red_run_is_re_run_once_and_red_twice_is_the_verdict(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake, repo = _proof_rig()
    monkeypatch.setattr(_e2e, "_dev_forge", lambda kind: (fake, "t"))
    fake.push(_e2e.E2E_OWNER, _e2e.E2E_REPO, "main", outcome="failure", sha=_SHA)
    fake.settle(_e2e.E2E_OWNER, _e2e.E2E_REPO, _SHA)
    # The retry re-queues the failed run and names it; a green run is
    # left alone.
    runs = repo.checks.runs(head_sha=_SHA)
    assert _e2e._retry_red_once(repo, runs) == ["ci.yml"]
    assert repo.checks.runs(head_sha=_SHA)[0].status == "queued"
    assert "re-running ci.yml" in capsys.readouterr().out
    assert _e2e._retry_red_once(repo, repo.checks.runs(head_sha=_SHA)) == []
    # Watched: the second attempt passes, and the watch ends green.
    real = _e2e._retry_red_once

    def _flaky(repo_: object, runs_: tuple[object, ...]) -> list[str]:
        retried = real(repo, repo.checks.runs(head_sha=_SHA))
        fake.set_outcome(_e2e.E2E_OWNER, _e2e.E2E_REPO, _SHA, "success")
        fake.settle(_e2e.E2E_OWNER, _e2e.E2E_REPO, _SHA)
        return retried

    fake.settle(_e2e.E2E_OWNER, _e2e.E2E_REPO, _SHA)  # the queued attempt, red
    monkeypatch.setattr(_e2e, "_retry_red_once", _flaky)
    _e2e._watch("gitea", "url", _SHA, timeout=5, interval=0)
    assert "ci.yml         success" in capsys.readouterr().out
    # Red twice is the verdict: the second attempt fails too.
    fake.push(_e2e.E2E_OWNER, _e2e.E2E_REPO, "main", outcome="failure", sha="e" * 40)
    fake.settle(_e2e.E2E_OWNER, _e2e.E2E_REPO, "e" * 40)

    def _still_red(repo_: object, runs_: tuple[object, ...]) -> list[str]:
        retried = real(repo, repo.checks.runs(head_sha="e" * 40))
        fake.settle(_e2e.E2E_OWNER, _e2e.E2E_REPO, "e" * 40)
        return retried

    monkeypatch.setattr(_e2e, "_retry_red_once", _still_red)
    with pytest.raises(_FAILURES, match=r"red runs on the loop: ci\.yml"):
        _e2e._watch("gitea", "url", "e" * 40, timeout=5, interval=0)


def test_starting_over_refuses_unpushed_commits_then_deletes_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    fake, repo = _proof_rig()
    root = tmp_path / _e2e.E2E_REPO
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "--initial-branch=main", str(origin)],
        check=True,
    )
    subprocess.run(["git", "clone", "-q", str(origin), str(root)], check=True)
    for key, value in (("user.name", "t"), ("user.email", "t@livery.local")):
        subprocess.run(["git", "-C", str(root), "config", key, value], check=True)
    (root / "a.txt").write_text("a\n")
    subprocess.run(["git", "-C", str(root), "add", "a.txt"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "feat: a"], check=True)
    purged: list[tuple[str, str]] = []

    def _purge(
        base: str, owner: str, *, token: str, kind: str = "pypi", api: object = None
    ) -> list[str]:
        purged.append((base + "/" + owner, token))
        return ["loop-echo==0.1.0"]

    monkeypatch.setattr("livery.forge._registry.purge_packages", _purge)
    # Refusal first: a commit origin has not seen would be lost.
    with pytest.raises(_FAILURES, match="holds commits its origin has not seen"):
        _e2e.start_over(fake, "t", root, url="http://gitea")
    assert repo.pr is not None and purged == []
    subprocess.run(
        ["git", "-C", str(root), "push", "-q", "-u", "origin", "main"], check=True
    )
    lines = _e2e.start_over(fake, "t", root, url="http://gitea")
    assert fake.get_repo(_e2e.E2E_OWNER, _e2e.E2E_REPO) is None
    assert purged == [("http://gitea/" + _e2e.E2E_OWNER, "t")]
    assert not root.exists()
    assert lines == [
        f"  deleted {_e2e.E2E_OWNER}/{_e2e.E2E_REPO} on the dev forge",
        "  purged 1 release(s) from the registry: loop-echo==0.1.0",
        f"  removed {root}",
    ]
    # A second reset finds nothing and says the same: the recovery procedure.
    lines = _e2e.start_over(fake, "t", root, url="http://gitea")
    assert lines[0].startswith("  deleted") and len(lines) == 2


def test_a_red_run_is_re_run_once_and_then_fails_the_proof_naming_its_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake, repo = _proof_rig()
    fake.push(_e2e.E2E_OWNER, _e2e.E2E_REPO, "main", outcome="failure", sha=_SHA)
    fake.settle(_e2e.E2E_OWNER, _e2e.E2E_REPO, _SHA)
    real = _e2e._retry_red_once
    retries: list[int] = []

    def _still_red(repo_: object, runs_: tuple[object, ...]) -> list[str]:
        retried = real(repo, repo.checks.runs(head_sha=_SHA))
        retries.append(len(retried))
        fake.settle(_e2e.E2E_OWNER, _e2e.E2E_REPO, _SHA)  # the attempt, red again
        return retried

    monkeypatch.setattr(_e2e, "_retry_red_once", _still_red)
    with pytest.raises(_FAILURES) as caught:
        _e2e._completed_run(repo, _SHA, event="push", interval=0)
    assert retries == [1]
    assert "ended failure" in str(caught.value)
    assert "/actions/runs/" in str(caught.value)
    # A second attempt that passes is the proof's run.
    fake.push(_e2e.E2E_OWNER, _e2e.E2E_REPO, "main", outcome="failure", sha="e" * 40)
    fake.settle(_e2e.E2E_OWNER, _e2e.E2E_REPO, "e" * 40)

    def _then_green(repo_: object, runs_: tuple[object, ...]) -> list[str]:
        retried = real(repo, repo.checks.runs(head_sha="e" * 40))
        fake.set_outcome(_e2e.E2E_OWNER, _e2e.E2E_REPO, "e" * 40, "success")
        fake.settle(_e2e.E2E_OWNER, _e2e.E2E_REPO, "e" * 40)
        return retried

    monkeypatch.setattr(_e2e, "_retry_red_once", _then_green)
    run, _jobs = _e2e._completed_run(repo, "e" * 40, event="push", interval=0)
    assert run.conclusion == "success"


def test_a_proof_waits_only_until_its_deadline_and_reads_its_event_alone() -> None:
    fake, repo = _proof_rig()
    fake.push(_e2e.E2E_OWNER, _e2e.E2E_REPO, "main", sha=_SHA)
    fake.settle(_e2e.E2E_OWNER, _e2e.E2E_REPO, _SHA)
    with pytest.raises(_FAILURES, match="no completed push run for dddddddddd"):
        _e2e._completed_run(repo, "d" * 40, event="push", timeout=0)
    with pytest.raises(_FAILURES, match="no completed pull_request run"):
        _e2e._completed_run(repo, _SHA, event="pull_request", timeout=0)


def test_a_proof_names_the_job_it_cannot_find_and_the_lines_it_misses() -> None:
    fake, repo = _proof_rig()
    fake.push(_e2e.E2E_OWNER, _e2e.E2E_REPO, "main", sha=_SHA)
    fake.settle(_e2e.E2E_OWNER, _e2e.E2E_REPO, _SHA)
    run, logs = _e2e._completed_run(repo, _SHA, event="push")
    assert [job.name for job in logs] == ["gate"]
    with pytest.raises(_FAILURES, match="has no check job"):
        _e2e._require_lines(repo, run, logs, "check", ("x",))
    with pytest.raises(_FAILURES, match=r"did not say \['absent line'\]"):
        _e2e._require_lines(
            repo, run, logs, "gate", ("concluded success", "absent line")
        )
    _e2e._require_lines(repo, run, logs, "gate", ("concluded success",))
    with pytest.raises(
        _FAILURES, match=r"said \['concluded success'\], which the proof"
    ):
        _e2e._require_lines(
            repo, run, logs, "gate", (), forbidden=("concluded success", "absent")
        )
