"""Local development environments: refusals first, then host mode, down and rm."""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.footman.context import Failed
from livery.workshop import _devenv


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A rig under a temporary data directory, with the config directory beside it."""
    data = tmp_path / "data"
    config = tmp_path / "config"
    data.mkdir()
    config.mkdir()
    monkeypatch.setattr("livery.footman.context.data_dir", lambda: data)
    monkeypatch.setattr("livery.footman.context.config_dir", lambda: config)
    return data / "forge-dev"


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


class _Machine:
    """The seams: processes started, alive and stopped, the health and the API."""

    def __init__(self) -> None:
        self.spawned: list[tuple[list[str], Path]] = []
        self.pids: set[int] = set()
        self.stopped: list[int] = []
        self.next_pid = 4000

    def spawn(self, argv: list[str], *, cwd: Path, log: Path) -> int:
        self.spawned.append((list(argv), cwd))
        self.next_pid += 1
        self.pids.add(self.next_pid)
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("started\n")
        return self.next_pid

    def alive(self, pid: int) -> bool:
        return pid in self.pids

    def stop(self, pid: int, **kwargs: object) -> bool:
        self.stopped.append(pid)
        self.pids.discard(pid)
        return True


@pytest.fixture
def machine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Machine:
    fake = _Machine()
    monkeypatch.setattr(_devenv, "SPAWN", fake.spawn)
    monkeypatch.setattr(_devenv, "ALIVE", fake.alive)
    monkeypatch.setattr(_devenv, "STOP", fake.stop)
    monkeypatch.setattr(_devenv, "HEALTHY", lambda url: True)
    monkeypatch.setattr(_devenv, "API", lambda url, path, token, **kw: 200)
    monkeypatch.setattr(_devenv, "this_host", lambda: "macos-arm")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    for name in ("gitea", "gitea-runner"):
        (binaries / name).write_text("#!/bin/sh\n")
    monkeypatch.setattr(
        _devenv,
        "BINARY",
        lambda name, version, **kw: binaries / name.replace("_", "-"),
    )

    def cli(argv: list[str], **kwargs: object) -> object:
        class _Result:
            code = 0
            stderr = ""

            def __init__(self, out: str) -> None:
                self.stdout = out

        if "generate-access-token" in argv:
            return _Result("token-abc\n")
        if "generate-runner-token" in argv:
            return _Result("reg-xyz\n")
        return _Result("")

    import livery.footman as footman

    monkeypatch.setattr(footman, "run", cli)
    monkeypatch.setattr(_devenv, "free_port", lambda: 43210)
    return fake


def test_host_mode_starts_gitea_seeds_it_and_registers_each_runner_once(
    rig: Path, machine: _Machine, capsys: pytest.CaptureFixture[str]
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
    # The caches every environment shares exist, beside the environments.
    assert (rig / "cache" / "footman").is_dir() and (rig / "cache" / "uv").is_dir()
    # The cascade's current forge is this environment.
    from livery.footman.context import config_dir

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
    rig: Path, machine: _Machine, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.footman.context import config_dir

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
    rig: Path, machine: _Machine, monkeypatch: pytest.MonkeyPatch
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
