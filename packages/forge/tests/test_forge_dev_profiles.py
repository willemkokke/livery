"""The dev rig's per-forge down and restart: the refusal, then each shape."""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.forge import _dev

_FAILURES = (BaseException,)


def _compose_recorder(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(_dev, "_compose", lambda *args, **kwargs: calls.append(args))
    return calls


def test_a_profile_outside_the_forges_is_refused_by_name() -> None:
    # The refusal first: a word that names no forge dies naming the
    # three accepted ones, and touches no container.
    with pytest.raises(_FAILURES, match="unknown profile github"):
        _dev._forges("github")
    assert _dev._forges("all") == ("gitea", "gitlab")
    assert _dev._forges("gitlab") == ("gitlab",)


def test_down_stops_one_forge_alone_and_wipes_only_its_volumes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # One forge: its services stop and are removed by name, since a
    # compose down would take the whole project; the shared env file
    # stays, because it carries the other forge's credentials too.
    calls = _compose_recorder(monkeypatch)
    env = tmp_path / ".repo.shared.env"
    env.write_text("GITEA_TOKEN=x\n")
    monkeypatch.setattr(_dev, "_dev_env_path", lambda: env)
    _dev.dev_down(profile="gitlab", wipe=True)
    profiles = ("--profile", "gitlab", "--profile", "gitlab-runner")
    assert calls == [
        (*profiles, "stop", "gitlab", "gitlab-runner"),
        (*profiles, "rm", "-f", "-v", "gitlab", "gitlab-runner"),
    ]
    assert env.exists()
    assert "gitlab: stopped, volumes deleted" in capsys.readouterr().out
    calls.clear()
    _dev.dev_down(profile="gitea")
    assert calls[1] == (
        "--profile",
        "gitea",
        "--profile",
        "gitea-runner",
        "rm",
        "-f",
        "gitea",
        "act_runner",
    )


def test_down_of_every_forge_is_one_compose_down_and_a_wipe_drops_the_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _compose_recorder(monkeypatch)
    env = tmp_path / ".repo.shared.env"
    env.write_text("GITEA_TOKEN=x\n")
    monkeypatch.setattr(_dev, "_dev_env_path", lambda: env)
    _dev.dev_down()
    every = (
        "--profile",
        "gitea",
        "--profile",
        "gitea-runner",
        "--profile",
        "gitlab",
        "--profile",
        "gitlab-runner",
    )
    assert calls == [(*every, "down")]
    assert env.exists()
    calls.clear()
    _dev.dev_down(wipe=True)
    assert calls == [(*every, "down", "--volumes")]
    assert not env.exists()


def test_restart_restarts_each_forges_runner_and_names_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _compose_recorder(monkeypatch)
    _dev.dev_restart()
    assert calls == [
        ("--profile", "gitea", "--profile", "gitea-runner", "restart", "act_runner"),
        (
            "--profile",
            "gitlab",
            "--profile",
            "gitlab-runner",
            "restart",
            "gitlab-runner",
        ),
    ]
    out = capsys.readouterr().out
    assert "gitea: runner act_runner restarted" in out
    assert "gitlab: runner gitlab-runner restarted" in out
