"""The ssh environment: the fallbacks first, then the command it builds."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from livery import git
from livery.git import _ssh


@pytest.fixture
def sockets() -> Iterator[Path]:
    """A socket directory short enough for the path limit: pytest's own is not."""
    made = Path(tempfile.mkdtemp(prefix="s", dir=tempfile.gettempdir()))
    yield made
    shutil.rmtree(made, ignore_errors=True)


def test_a_persons_own_ssh_command_wins_and_says_nothing(sockets: Path) -> None:
    environment, reason = git.ssh_environment(
        sockets, environ={"GIT_SSH_COMMAND": "ssh -i ~/.ssh/work"}
    )
    assert environment == {} and reason == ""
    environment, reason = git.ssh_environment(
        sockets, environ={}, configured="ssh -i ~/.ssh/work"
    )
    assert environment == {} and reason == ""


def test_the_librarys_own_command_already_present_is_not_a_persons(
    sockets: Path,
) -> None:
    own = git.ssh_environment(sockets, environ={})[0]
    again, reason = git.ssh_environment(sockets, environ=own)
    assert again == own and reason == ""


def test_a_directory_too_long_for_a_socket_names_the_limit(tmp_path: Path) -> None:
    deep = tmp_path / ("d" * 120)
    environment, reason = git.ssh_environment(deep, environ={})
    assert environment == {}
    assert str(deep) in reason and "unix socket path limit" in reason
    assert not deep.exists()


def test_a_recorded_refusal_turns_multiplexing_off_with_sshs_words(
    sockets: Path,
) -> None:
    verdict = git.record_unsupported(
        sockets, "ControlPersist: the master kept the pipe\n"
    )
    assert verdict == sockets / "unsupported"
    environment, reason = git.ssh_environment(sockets, environ={})
    assert environment == {}
    assert "ssh multiplexing off" in reason
    assert "ControlPersist: the master kept the pipe" in reason
    verdict.unlink()
    assert "GIT_SSH_COMMAND" in git.ssh_environment(sockets, environ={})[0]


def test_the_command_holds_the_five_options_and_the_quoted_path(sockets: Path) -> None:
    spaced = sockets / "App Support"
    environment, reason = git.ssh_environment(spaced, environ={}, idle="3m")
    assert reason == ""
    assert spaced.is_dir()
    command = environment["GIT_SSH_COMMAND"]
    assert command.startswith("ssh ")
    for option in (
        "-o ControlMaster=auto",
        "-o ControlPersist=3m",
        "-o ServerAliveInterval=15",
        "-o ServerAliveCountMax=2",
    ):
        assert option in command
    assert f"""-o 'ControlPath="{spaced.as_posix()}/%r@%h:%p"'""" in command


def test_the_remote_host_is_read_from_both_ssh_spellings_and_no_other() -> None:
    assert git.remote_host("git@github.com:willemkokke/livery.git") == (
        "git",
        "github.com",
        22,
    )
    assert git.remote_host("ssh://git@gitea.local:2222/livery/loop.git") == (
        "git",
        "gitea.local",
        2222,
    )
    assert git.remote_host("ssh://host/owner/repo.git") == ("git", "host", 22)
    assert git.remote_host("https://github.com/willemkokke/livery.git") is None
    assert git.remote_host("http://oauth2:token@gitea:3000/livery/loop.git") is None
    assert git.remote_host("/srv/git/repo.git") is None
    assert git.remote_host("file:///srv/git/repo.git") is None


def test_a_master_is_asked_over_its_socket_and_a_dead_ssh_answers_no(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[list[str]] = []

    class _Done:
        returncode = 0

    def run(argv: list[str], **kwargs: object) -> _Done:
        asked.append(argv)
        return _Done()

    monkeypatch.setattr(subprocess, "run", run)
    assert git.master_up(tmp_path, "git@github.com:w/l.git")
    assert asked[0][:3] == ["ssh", "-O", "check"]
    assert asked[0][-1] == "git@github.com" and "-p" in asked[0]
    assert not git.master_up(tmp_path, "https://github.com/w/l.git")
    assert asked[1:] == []

    def dead(argv: list[str], **kwargs: object) -> _Done:
        raise OSError("no ssh")

    monkeypatch.setattr(subprocess, "run", dead)
    assert not git.master_up(tmp_path, "git@github.com:w/l.git")

    def slow(argv: list[str], **kwargs: object) -> _Done:
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(subprocess, "run", slow)
    assert not git.master_up(tmp_path, "git@github.com:w/l.git")


def test_the_socket_path_is_posix_on_every_platform(tmp_path: Path) -> None:
    assert _ssh._socket_path(tmp_path).endswith("/%r@%h:%p")
    assert "\\" not in _ssh._socket_path(tmp_path)


def test_the_sockets_live_under_the_runners_data_directory(tmp_path: Path) -> None:
    assert git.ssh_sockets(tmp_path) == tmp_path / "ssh"
