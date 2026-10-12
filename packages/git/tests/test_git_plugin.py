"""The plugin's hook: what it writes into a task's environment, the fallbacks first."""

from __future__ import annotations

import importlib
import shutil
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from livery import git


@pytest.fixture
def data(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A data directory short enough for a socket path: pytest's own is not."""
    made = Path(tempfile.mkdtemp(prefix="d", dir=tempfile.gettempdir()))
    yield made
    shutil.rmtree(made, ignore_errors=True)


@pytest.fixture
def plugin(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    """The plugin module, imported fresh into a captured registry and left unloaded."""
    from livery.footman import _registry as registry

    monkeypatch.delitem(sys.modules, "livery.git._plugin", raising=False)
    with registry.capture():
        module = importlib.import_module("livery.git._plugin")
    yield module
    sys.modules.pop("livery.git._plugin", None)


class _Task:
    """A task handle as the hook sees it: an environment, marks, state."""

    def __init__(self, env: dict[str, str], **marks: Any) -> None:
        self.env = env
        self.name = "build"
        self.state = type("State", (), {})()
        self._marks = marks

    def mark(self, name: str, default: Any = None) -> Any:
        return self._marks.get(name, default)


def _hook(
    plugin: ModuleType, monkeypatch: pytest.MonkeyPatch, data: Path, **config: str
):
    """The hook with footman's facts and git's config answered from *config*."""
    monkeypatch.setattr(plugin.footman, "data_dir", lambda: data)
    monkeypatch.setattr(plugin.footman, "prog", lambda: "fm")
    monkeypatch.setattr(plugin.footman, "cwd", lambda: data)
    monkeypatch.setattr(plugin, "config_get", lambda root, key: config.get(key, ""))
    plugin._READ.clear()
    return plugin.git_environment


def test_a_persons_identity_and_ssh_command_stand(
    plugin: ModuleType, monkeypatch: pytest.MonkeyPatch, data: Path
) -> None:
    hook = _hook(
        plugin,
        monkeypatch,
        data,
        **{"user.email": "me@example.test", "core.sshCommand": "ssh -i k"},
    )
    task = _Task({})
    hook(None, task)
    assert task.env == {"GIT_EDITOR": "true"}
    kept = _Task({"GIT_EDITOR": "vim"})
    hook(None, kept)
    assert kept.env["GIT_EDITOR"] == "vim"


def test_a_missing_identity_is_the_tools_and_the_ssh_command_is_written(
    plugin: ModuleType, monkeypatch: pytest.MonkeyPatch, data: Path
) -> None:
    hook = _hook(plugin, monkeypatch, data)
    task = _Task({})
    hook(None, task)
    assert task.env["GIT_AUTHOR_NAME"] == "fm"
    assert task.env["GIT_COMMITTER_EMAIL"] == "fm@fm.invalid"
    assert "ControlMaster=auto" in task.env["GIT_SSH_COMMAND"]
    assert (data / "ssh").is_dir()


def test_the_marks_force_an_identity_and_unsigned_commits(
    plugin: ModuleType, monkeypatch: pytest.MonkeyPatch, data: Path
) -> None:
    hook = _hook(plugin, monkeypatch, data, **{"user.email": "me@example.test"})
    task = _Task(
        {},
        livery_git_identity=("loop", "loop@loop.invalid"),
        livery_git_unsigned=True,
    )
    hook(None, task)
    assert task.env["GIT_AUTHOR_EMAIL"] == "loop@loop.invalid"
    assert task.env["GIT_CONFIG_COUNT"] == "2"
    assert task.env["GIT_CONFIG_KEY_0"] == "commit.gpgsign"
    assert task.env["GIT_CONFIG_VALUE_1"] == "false"


def test_the_config_is_read_once_per_process(
    plugin: ModuleType, monkeypatch: pytest.MonkeyPatch, data: Path
) -> None:
    reads: list[str] = []
    hook = _hook(plugin, monkeypatch, data)

    def counted(root: Path, key: str) -> str:
        reads.append(key)
        return ""

    monkeypatch.setattr(plugin, "config_get", counted)
    hook(None, _Task({}))
    hook(None, _Task({}))
    assert sorted(reads) == ["core.sshCommand", "user.email"]


def test_a_refusal_is_said_once_per_task(
    plugin: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    data: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    hook = _hook(plugin, monkeypatch, data)
    git.record_unsupported(data / "ssh", "the master kept the pipe")
    task = _Task({})
    hook(None, task)
    hook(None, task)
    assert "GIT_SSH_COMMAND" not in task.env
    assert capsys.readouterr().out.count("ssh multiplexing off") == 1
