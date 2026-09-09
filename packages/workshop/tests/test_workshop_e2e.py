"""``fm ci.e2e``'s provisioning: refusals first, then the idempotent path."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

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
