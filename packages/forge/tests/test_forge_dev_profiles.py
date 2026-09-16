"""The dev rig's per-forge down and restart: the refusal, then each shape."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

_FAILURES = (BaseException,)


@pytest.fixture
def dev(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    """The dev plugin, imported into a captured registry and left unloaded.

    The plugin registers its groups at import, and a workspace mounts
    it by importing it fresh through footman's ``plugin()``: a copy
    left in ``sys.modules`` would make a later mount in the same
    process read the layer as content only. The entry is popped
    directly at teardown: a monkeypatched delete would be undone, and
    the module put back. The setup's monkeypatched delete restores
    whatever ``sys.modules`` held before.
    """
    from livery.footman import registry

    monkeypatch.delitem(sys.modules, "livery.forge._dev", raising=False)
    with registry.capture():
        module = importlib.import_module("livery.forge._dev")
    yield module
    sys.modules.pop("livery.forge._dev", None)


def _compose_recorder(
    monkeypatch: pytest.MonkeyPatch, dev: ModuleType
) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(dev, "_compose", lambda *args, **kwargs: calls.append(args))
    return calls


def test_a_profile_outside_the_forges_is_refused_by_name(dev: ModuleType) -> None:
    # The refusal first: a word that names no forge dies naming the
    # three accepted ones, and touches no container.
    with pytest.raises(_FAILURES, match="unknown profile github"):
        dev._forges("github")
    assert dev._forges("all") == ("gitea", "gitlab")
    assert dev._forges("gitlab") == ("gitlab",)


def test_down_stops_one_forge_alone_and_wipes_only_its_volumes(
    dev: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # One forge: its services stop and are removed by name, since a
    # compose down would take the whole project; the shared env file
    # stays, because it carries the other forge's credentials too.
    calls = _compose_recorder(monkeypatch, dev)
    env = tmp_path / ".repo.shared.env"
    env.write_text("GITEA_TOKEN=x\n")
    monkeypatch.setattr(dev, "_dev_env_path", lambda: env)
    dev.dev_down(profile="gitlab", wipe=True)
    profiles = ("--profile", "gitlab", "--profile", "gitlab-runner")
    assert calls == [
        (*profiles, "stop", "gitlab", "gitlab-runner"),
        (*profiles, "rm", "-f", "-v", "gitlab", "gitlab-runner"),
    ]
    assert env.exists()
    assert "gitlab: stopped, volumes deleted" in capsys.readouterr().out
    calls.clear()
    dev.dev_down(profile="gitea")
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
    dev: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _compose_recorder(monkeypatch, dev)
    env = tmp_path / ".repo.shared.env"
    env.write_text("GITEA_TOKEN=x\n")
    monkeypatch.setattr(dev, "_dev_env_path", lambda: env)
    dev.dev_down()
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
    dev.dev_down(wipe=True)
    assert calls == [(*every, "down", "--volumes")]
    assert not env.exists()


def test_restart_restarts_each_forges_runner_and_names_it(
    dev: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _compose_recorder(monkeypatch, dev)
    dev.dev_restart()
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


def test_the_docker_overlay_rides_only_with_the_flag(dev: ModuleType) -> None:
    # Without the flag the compose set is the one packaged file; with
    # it, the socket overlay follows, before the subcommand.
    plain = dev._compose_cmd("ps")
    assert plain[:2] == ["compose", "-f"]
    assert plain[2].endswith("compose.yaml")
    assert plain[3:] == ["ps"]
    armed = dev._compose_cmd("ps", with_docker=True)
    assert armed[:3] == plain[:3]
    assert armed[3] == "-f"
    assert armed[4].endswith("compose.docker.yaml")
    assert armed[5:] == ["ps"]
    assert dev._docker_overlay().is_file()


def test_conformance_replays_by_default_and_probes_the_forges_when_live(
    dev: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The refusals first: an unknown backend, then a live run whose
    # local forge is not up, refused by name before any suite starts.
    # Then the shapes: replay carries no switch, live carries
    # FORGE_LIVE and leaves the cassettes alone, and the recorder's
    # switch is FORGE_RECORD; GitHub's suite names the e2e owner.
    from types import SimpleNamespace
    from typing import cast

    from livery.toolroom import tools

    runs: list[tuple[tuple[str, ...], dict[str, str]]] = []

    def _opts(**kwargs: object) -> object:
        def _run(*args: str) -> None:
            runs.append((args, dict(cast("dict[str, str]", kwargs["env"]))))

        return _run

    monkeypatch.setattr(tools, "pytest", SimpleNamespace(opts=_opts))
    with pytest.raises(_FAILURES, match="unknown backend 'bitbucket'"):
        dev._run_suites("", "bitbucket", {}, live=False)
    up: set[str] = set()

    def _probe(kind: str) -> None:
        if kind not in up:
            dev.fail(f"the local {kind} is not up")

    monkeypatch.setattr(dev, "_require_forge_up", _probe)
    with pytest.raises(_FAILURES, match="the local gitea is not up"):
        dev._run_suites("", "", {"FORGE_LIVE": "1"}, live=True)
    assert runs == []
    dev._run_suites("branches", "gitea", {}, live=False)
    assert runs[-1][0][0].endswith("test_gitea_conformance.py")
    assert runs[-1][0][1:] == ("-k", "branches")
    assert "FORGE_LIVE" not in runs[-1][1] and "FORGE_RECORD" not in runs[-1][1]
    up.update({"gitea", "gitlab"})
    runs.clear()
    dev._run_suites("", "", {"FORGE_LIVE": "1"}, live=True)
    assert [args[0].rsplit("/", 1)[-1] for args, _ in runs] == [
        "test_gitea_conformance.py",
        "test_gitlab_conformance.py",
        "test_github_conformance.py",
    ]
    assert all(env["FORGE_LIVE"] == "1" for _, env in runs)
    assert runs[1][0][1:3] == ("-n", "4")
    assert runs[2][1]["FORGE_E2E_OWNER"] == "livery-forge-e2e"
    runs.clear()
    dev._run_suites("", "gitea", {"FORGE_RECORD": "1"}, live=True)
    assert runs[-1][1]["FORGE_RECORD"] == "1"


def test_the_forge_probe_reads_a_refusal_as_up_and_a_dead_port_as_down(
    dev: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    import urllib.error
    import urllib.request
    from email.message import Message

    def refuse(*args: object, **kwargs: object) -> object:
        raise urllib.error.HTTPError("http://x", 401, "Unauthorized", Message(), None)

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    dev._require_forge_up("gitlab")

    def dead(*args: object, **kwargs: object) -> object:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", dead)
    with pytest.raises(_FAILURES, match=r"forge\.dev\.up --profile=gitea"):
        dev._require_forge_up("gitea")
