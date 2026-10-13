"""Local development environments: refusals first, then host mode, down and rm."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from livery.footman import Failed
from livery.workshop import _devenv
from workshop_rig import Machine, machine_fixture, rig_fixture  # noqa: F401

# --- the refusals ---------------------------------------------------------------


def test_a_bad_name_a_bad_mode_and_a_bad_runner_spec_refuse_by_name(rig: Path) -> None:
    with pytest.raises(Failed, match=r"'bad name' is not an environment name"):
        _devenv.environment("bad name")
    with pytest.raises(Failed, match=r"'-dev' is not an environment name"):
        _devenv.environment("-dev")
    with pytest.raises(Failed, match=r"--mode='vm' is not one of host, docker"):
        _devenv.devenv_up("dev", mode="vm")
    with pytest.raises(Failed, match=r"--runners names 'container:linux-x64'"):
        _devenv.parse_runners("host,container:linux-x64")
    with pytest.raises(Failed, match=r"'host\*0' is not host\*<n>"):
        _devenv.parse_runners("host*0")
    with pytest.raises(Failed, match=r"--runners names no runner"):
        _devenv.parse_runners(" , ")
    assert _devenv.parse_runners("host") == ("host",)
    assert _devenv.parse_runners("host*3, host") == ("host",) * 4


def test_docker_mode_has_one_environment_and_a_mode_never_changes_under_a_name(
    rig: Path,
) -> None:
    with pytest.raises(Failed, match=r"docker mode has one environment, dev"):
        _devenv.up_docker(_devenv.environment("scratch"))
    env = _devenv.environment("scratch")
    env.update({"MODE": "host"})
    with pytest.raises(Failed, match=r"scratch exists in host mode"):
        _devenv.devenv_up("scratch", mode="docker")


def test_down_and_rm_refuse_or_say_nothing_for_an_absent_environment(
    rig: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(Failed, match=r"nonesuch: no such environment"):
        _devenv.devenv_down("nonesuch")
    _devenv.devenv_rm("nonesuch")
    assert "nonesuch: no such environment" in capsys.readouterr().out
    _devenv.devenv_ls()
    assert "no environments under" in capsys.readouterr().out


# --- host mode ------------------------------------------------------------------


def test_host_mode_starts_gitea_seeds_it_and_registers_each_runner_once(
    rig: Path, machine: Machine, capsys: pytest.CaptureFixture[str]
) -> None:
    _devenv.devenv_up("dev", runners="host*2")
    env = _devenv.environment("dev")
    values = env.values()
    assert values["MODE"] == "host" and values["GITEA_PORT"] == "43210"
    assert values["GITEA_URL"] == "http://localhost:43210"
    assert values["GITEA_TOKEN"] == "token-abc"
    assert values["LABELS"] == "dev-macos-arm-01,dev-macos-arm-02"
    # Gitea first, its configuration written, then one daemon per runner.
    argv, _ = machine.spawned[0]
    assert argv[0].endswith("gitea") and argv[-1] == "web"
    assert env.app_ini.is_file() and "HTTP_PORT = 43210" in env.app_ini.read_text()
    daemons = [argv for argv, _ in machine.spawned[1:]]
    assert [a[1] for a in daemons] == ["daemon", "daemon"]
    config = (env.runner_dir(1) / "config.yaml").read_text()
    assert "cache:\n  enabled: true" in config
    assert str(_devenv.cache_dir() / "uv") in config
    assert str(env.runner_dir(1) / "work") in config
    # Conan's home is each runner's own, inside the environment: a release
    # writes a remote there that names this environment's forge. It exists
    # before any job, so a container's bind mount never creates it as root.
    assert f"CONAN_HOME: {env.runner_dir(1) / 'conan'}\n" in config
    second = (env.runner_dir(2) / "config.yaml").read_text()
    assert f"CONAN_HOME: {env.runner_dir(2) / 'conan'}\n" in second
    assert (env.runner_dir(1) / "conan").is_dir()
    assert (env.runner_dir(2) / "conan").is_dir()
    # The caches every environment shares exist, beside the environments,
    # and conan's home is not one of them.
    assert (rig / "cache" / "footman").is_dir() and (rig / "cache" / "uv").is_dir()
    assert not (rig / "cache" / "conan").exists()
    # The cascade's current forge is this environment.
    from livery.footman import config_dir

    shared = (config_dir() / ".repo.shared.env").read_text()
    assert "GITEA_URL=http://localhost:43210" in shared
    assert "GITEA_TOKEN=token-abc" in shared
    out = capsys.readouterr().out
    assert "dev: gitea http://localhost:43210, host mode, 2 runner(s)" in out
    # A second up finds everything running and starts nothing.
    _devenv.devenv_up("dev", runners="host*2")
    assert len(machine.spawned) == 3
    _devenv.devenv_ls()
    assert "dev: host, http://localhost:43210, 3/3 process(es) running" in (
        capsys.readouterr().out
    )


def test_down_stops_the_processes_and_rm_removes_the_directory_and_the_cascade_keys(
    rig: Path, machine: Machine, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.footman import config_dir

    _devenv.devenv_up("dev")
    _devenv.devenv_down("dev")
    assert machine.stopped == [4001, 4002] and not machine.pids
    out = capsys.readouterr().out
    assert "dev: gitea 4001 stopped" in out and "dev: runner 4002 stopped" in out
    # The state stays, and a second down finds nothing running.
    env = _devenv.environment("dev")
    assert env.app_ini.is_file()
    _devenv.devenv_down("dev")
    assert "dev: nothing was running" in capsys.readouterr().out
    # rm removes the directory whole and forgets the cascade's forge; the
    # caches stay unless asked.
    (rig / "cache" / "uv" / "wheel").write_text("cached")
    _devenv.devenv_rm("dev")
    assert not env.directory.exists()
    shared = (config_dir() / ".repo.shared.env").read_text()
    assert "GITEA_URL" not in shared and "GITEA_TOKEN" not in shared
    assert (rig / "cache" / "uv" / "wheel").is_file()
    assert _devenv.environments() == ()
    _devenv.devenv_rm("dev", purge_caches=True)
    out = capsys.readouterr().out
    assert "cache: uv removed" in out
    assert not (rig / "cache" / "uv").exists()
    assert _devenv.purge_cache() == ["  cache: nothing to remove"]


def test_gitea_that_never_answers_or_exits_early_fails_naming_its_log(
    rig: Path, machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_devenv, "HEALTHY", lambda url: False)
    monkeypatch.setattr(_devenv, "HEALTH_TIMEOUT", 0.2)
    with pytest.raises(
        Failed, match=r"did not answer http://localhost:43210/api/healthz"
    ):
        _devenv.devenv_up("dev")
    monkeypatch.setattr(_devenv, "ALIVE", lambda pid: False)
    with pytest.raises(Failed, match=r"exited before it answered; its log is"):
        _devenv.devenv_up("dev")


def test_a_daemon_never_inherits_the_checkout_the_verb_ran_from(tmp_path: Path) -> None:
    # The runner daemon carried the launching shell's VIRTUAL_ENV and a
    # PATH led by that worktree's venv: every job's uv warned, and a
    # tool could resolve from a checkout that may be deleted under it.
    from livery.workshop._devenv import daemon_environment

    venv = tmp_path / "wt" / ".venv"
    environ = {
        "VIRTUAL_ENV": str(venv),
        "UV_RUN_RECURSION_DEPTH": "1",
        "PATH": os.pathsep.join(
            [str(venv / "bin"), "/store/node/bin", str(venv / "Scripts"), "/usr/bin"]
        ),
        "FOOTMAN_DATA_DIR": "/cache/footman",
    }
    cleaned = daemon_environment(environ)
    assert "VIRTUAL_ENV" not in cleaned and "UV_RUN_RECURSION_DEPTH" not in cleaned
    assert cleaned["PATH"] == os.pathsep.join(["/store/node/bin", "/usr/bin"])
    # The entered environment stays: the store's tools are the point.
    assert cleaned["FOOTMAN_DATA_DIR"] == "/cache/footman"
    # Outside any venv there is nothing to strip.
    assert daemon_environment({"PATH": "/usr/bin"}) == {"PATH": "/usr/bin"}


def test_a_spawned_daemon_starts_with_the_cleaned_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    import sys
    import time

    from livery.workshop._devenv import spawn_detached

    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / ".venv"))
    monkeypatch.setenv("UV_RUN_RECURSION_DEPTH", "1")
    monkeypatch.setenv(
        "PATH", os.pathsep.join([str(tmp_path / ".venv" / "bin"), "/usr/bin"])
    )
    log = tmp_path / "daemon.log"
    pid = spawn_detached(
        [sys.executable, "-c", "import json, os; print(json.dumps(dict(os.environ)))"],
        cwd=tmp_path,
        log=log,
    )
    assert pid > 0
    for _ in range(100):
        if log.is_file() and log.read_text().endswith("}\n"):
            break
        time.sleep(0.05)
    seen = json.loads(log.read_text())
    assert "VIRTUAL_ENV" not in seen and "UV_RUN_RECURSION_DEPTH" not in seen
    assert seen["PATH"] == "/usr/bin"


def test_the_liveness_probe_is_footmans() -> None:
    # One probe for every pid file on the machine: the rig's seam is
    # footman's function, and the tasklist spawn is gone with it.
    import livery.footman as footman

    assert _devenv.ALIVE is footman.alive
    assert not hasattr(_devenv, "TASKLIST")
