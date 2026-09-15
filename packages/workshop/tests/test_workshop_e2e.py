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


def test_a_kind_with_no_local_containers_refuses_naming_the_lanes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITEA_URL", "http://gitea.example")
    monkeypatch.setenv("GITEA_TOKEN", "t")
    with pytest.raises(
        _FAILURES, match="not a local lane: the loop runs against gitea, gitlab"
    ):
        _e2e.provision("svn")


def test_each_lane_addresses_its_own_registry() -> None:
    gitea, gitlab = _e2e.LANES["gitea"], _e2e.LANES["gitlab"]
    assert gitea.host == "gitea" and gitlab.host == "gitlab"
    # Gitea keeps an owner's registry; GitLab a project's, by its
    # URL-encoded path, so one URL is true on both sides of the loop.
    assert gitea.publish() == "http://gitea:3000/api/packages/livery/pypi"
    assert gitea.index() == "http://gitea:3000/api/packages/livery/pypi/simple"
    assert gitlab.publish() == (
        "http://gitlab:8929/api/v4/projects/livery%2Fci-e2e-loop/packages/pypi"
    )
    assert gitlab.index("http://localhost:8929") == (
        "http://localhost:8929/api/v4/projects/livery%2Fci-e2e-loop/packages/pypi"
        "/simple"
    )


def test_gitlab_provisioning_mints_the_push_token_and_sets_it_masked(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GITLAB_URL", "http://gitlab.example")
    monkeypatch.setenv("GITLAB_TOKEN", "the-lane-token")
    fake = FakeForge()
    monkeypatch.setattr(
        "livery.forge.GitlabForge.connect",
        staticmethod(lambda url, token: fake),
    )
    calls: list[tuple[str, str, object]] = []

    def api(
        method: str, url: str, token: str, body: object = None
    ) -> tuple[int, object]:
        calls.append((method, url, body))
        if method == "GET":
            return 200, [{"id": 4, "name": "livery-loop-push", "active": True}]
        if method == "DELETE":
            return 204, None
        return 201, {"id": 5, "token": "glpat-minted"}

    mint = _e2e._mint_push_token
    monkeypatch.setattr(
        _e2e, "_mint_push_token", lambda url, token: mint(url, token, api)
    )
    _e2e.provision("gitlab")
    out = capsys.readouterr().out
    assert (
        "secrets set: UV_PUBLISH_TOKEN, FORGE_TOKEN, FORGE_ADMIN_TOKEN,"
        " GITLAB_PUSH_TOKEN" in out
    )
    state = fake._repos[(_e2e.E2E_OWNER, _e2e.E2E_REPO)]
    assert state.secrets["GITLAB_PUSH_TOKEN"] == "glpat-minted"
    assert state.secrets["FORGE_TOKEN"] == "the-lane-token"
    # The token minted before, by name, is revoked before a new one is
    # minted with the push scope, since a value is readable at minting alone.
    methods = [(method, url.rsplit("/", 1)[-1]) for method, url, _ in calls]
    assert methods == [
        ("GET", "access_tokens"),
        ("DELETE", "4"),
        ("POST", "access_tokens"),
    ]
    assert calls[-1][2] == {
        "name": "livery-loop-push",
        "scopes": ["api", "write_repository"],
        "access_level": 40,
        "expires_at": calls[-1][2]["expires_at"],  # type: ignore[index]
    }
    assert "livery%2Fci-e2e-loop" in calls[0][1]


def test_a_refused_mint_names_the_status(monkeypatch: pytest.MonkeyPatch) -> None:
    def api(
        method: str, url: str, token: str, body: object = None
    ) -> tuple[int, object]:
        return (200, []) if method == "GET" else (403, "insufficient scope")

    with pytest.raises(_FAILURES, match="push token was not minted: HTTP 403"):
        _e2e._mint_push_token("http://gitlab.example", "t", api)


def test_the_purge_addresses_the_lane(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, ...]] = []

    def gitea(base: str, owner: str, **kw: object) -> list[str]:
        seen.append(("gitea", base, owner))
        return ["a==1"]

    def gitlab(base: str, project: str, **kw: object) -> list[str]:
        seen.append(("gitlab", base, project))
        return ["b==2"]

    monkeypatch.setattr("livery.forge._registry.purge_packages", gitea)
    monkeypatch.setattr("livery.forge._registry.purge_gitlab_packages", gitlab)
    assert _e2e._purge("gitea", "http://h", "t") == ["a==1"]
    assert _e2e._purge("gitlab", "http://h", "t", names=["b"]) == ["b==2"]
    assert seen == [
        ("gitea", "http://h", "livery"),
        ("gitlab", "http://h", "livery/ci-e2e-loop"),
    ]


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


def test_the_registration_gate_sees_only_a_source_checkout() -> None:
    # The verb registers only beside the workshop's own tests: from
    # this source checkout the path arithmetic finds this very suite,
    # and from an installed wheel (the release legs) it finds no tests
    # directory at all, which is what keeps the verb off an instance.
    here = Path(__file__).resolve().parent
    if here == _e2e._WORKSHOP_TESTS:
        assert (_e2e._WORKSHOP_TESTS / "test_workshop_e2e.py").is_file()
    else:
        assert "site-packages" in str(Path(_e2e.__file__).resolve())
        assert not _e2e._WORKSHOP_TESTS.is_dir()


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


def test_dev_pins_read_a_sha_of_digits_alone_through_its_g(tmp_path: Path) -> None:
    # A build backend reads a local segment of digits alone as a
    # number and drops its leading zero; the g git describe puts first
    # keeps the sha a word, and the pin reads it back through the g.
    head = "0442877" + "a" * 33
    for member in _e2e.DEV_MEMBERS:
        _wheel(tmp_path, member, "0.3.0.dev6+feat.486.store.g0442877.20260912")
    pins = _e2e._dev_pins(tmp_path, head)
    assert pins["livery-workshop"] == "0.3.0.dev6+feat.486.store.g0442877.20260912"


def test_dev_pins_read_only_the_members_asked_for(tmp_path: Path) -> None:
    for member in ("workshop", "toolroom", "footman"):
        _wheel(tmp_path, member, "0.2.0.dev72+feat.314.profile.05482de.20260909")
    # forge built nothing, and is not asked for: no refusal.
    pins = _e2e._dev_pins(tmp_path, HEAD, ("workshop", "toolroom", "footman"))
    assert set(pins) == {"livery-workshop", "livery-toolroom", "livery-footman"}


def test_the_dev_act_pins_a_released_member_and_drops_its_stale_wheels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from types import SimpleNamespace

    for member in _e2e.DEV_MEMBERS:
        home = tmp_path / "packages" / member
        home.mkdir(parents=True)
        (home / "workshop.toml").write_text(
            f'type = "python"\nname = "livery-{member}"\n'
        )
        (home / "pyproject.toml").write_text(
            f'[project]\nname = "livery-{member}"\nversion = "0"\n'
        )
    (tmp_path / "workshop.toml").write_text("[workspace]\n")
    monkeypatch.setattr(
        "livery.workshop._layers.workspace_root", lambda start=None: tmp_path
    )
    monkeypatch.setattr(_e2e, "_dev_forge", lambda kind: (None, "t"))
    monkeypatch.setattr(
        "livery.workshop._git_ops.GitOps",
        lambda root: SimpleNamespace(head_sha=lambda: HEAD),
    )
    monkeypatch.setattr(
        "livery.workshop._dev_release.unchanged_since_release",
        lambda root, git, package: "0.3.0" if package.name == "livery-forge" else "",
    )
    ran: list[list[str]] = []
    monkeypatch.setattr("livery.footman.run", lambda argv, **kwargs: ran.append(argv))
    purged: list[tuple[str, object]] = []

    def _purge(base: str, owner: str, **kwargs: object) -> list[str]:
        purged.append((owner, kwargs.get("names")))
        return ["livery-forge==0.3.0.dev4"]

    monkeypatch.setattr("livery.forge._registry.purge_packages", _purge)
    monkeypatch.setattr(
        _e2e,
        "_dev_pins",
        lambda root, head, members: {f"livery-{m}": "dev" for m in members},
    )
    monkeypatch.setenv("GITEA_URL", "http://localhost:3000")
    pins = _e2e._publish_dev_wheels("gitea")
    # forge is pinned to its release, the others rebuilt, and the
    # forge rehearsal wheels go so the release resolves past them.
    assert ran == [
        ["fm", "--yes", "workflow.release", "workshop", "toolroom", "footman"]
    ]
    assert purged == [("livery", {"livery-forge": "0.3.0"})]
    assert pins == {
        "livery-workshop": "dev",
        "livery-toolroom": "dev",
        "livery-footman": "dev",
        "livery-forge": "0.3.0",
    }
    out = capsys.readouterr().out
    assert "forge: nothing unreleased since 0.3.0; the loop pins the release" in out
    assert "1 stale rehearsal release(s)" in out


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


class _SlowDeleteForge(FakeForge):
    """A forge whose delete outruns the client; *finishes*: the server completes it."""

    finishes = True

    def delete_repo(self, owner: str, name: str) -> None:
        from livery.forge import ForgeError

        if self.finishes:
            super().delete_repo(owner, name)
        raise ForgeError("server unreachable on DELETE /repos/x: timed out")


def test_a_delete_that_outruns_the_client_is_waited_for_or_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.forge import ForgeError

    monkeypatch.setattr(
        "livery.forge._registry.purge_packages",
        lambda base, owner, *, token, kind="pypi", api=None: [],
    )
    root = tmp_path / "never-born"
    # The server never finishes: the reset refuses, naming the wait.
    stuck = _SlowDeleteForge()
    stuck.finishes = False
    stuck.create_repo(_e2e.E2E_OWNER, _e2e.E2E_REPO)
    with pytest.raises(
        ForgeError, match="still on the dev forge after the delete's wait"
    ):
        _e2e.start_over(stuck, "t", root, url="http://gitea", wait=0)
    # The server finishes after the client gave up: the reset goes on.
    slow = _SlowDeleteForge()
    slow.create_repo(_e2e.E2E_OWNER, _e2e.E2E_REPO)
    lines = _e2e.start_over(slow, "t", root, url="http://gitea", wait=0)
    assert lines[0].endswith(
        "(the delete outran the client's wait and finished on the server)"
    )
    assert slow.get_repo(_e2e.E2E_OWNER, _e2e.E2E_REPO) is None


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
