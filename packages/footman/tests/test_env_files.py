"""The env_files built-in: a mounted .env loader, env-wins, taught refusals."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

from livery.footman.testing import Runner

TASKS = textwrap.dedent(
    """
    import os
    from livery.footman import task
    from livery.footman.compose import plugin

    plugin("footman.env_files")

    @task
    def show(name: str = "TOKEN"):
        print(f"{name}={os.environ.get(name, '<unset>')}")
    """
)


def _project(tmp_path: Path, env: str | None) -> Path:
    src = tmp_path / "tasks.py"
    src.write_text(TASKS)
    if env is not None:
        (tmp_path / ".env").write_text(env)
    return src


def test_the_default_env_file_loads_and_env_wins(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TOKEN", raising=False)
    src = _project(tmp_path, "TOKEN=from-file\n# a comment\n")
    result = Runner().invoke("show", tasks=src)
    assert result.ok, result.stderr
    assert "TOKEN=from-file" in result.stdout

    monkeypatch.setenv("TOKEN", "from-env")
    result = Runner().invoke("show", tasks=src)
    assert result.ok, result.stderr
    assert "TOKEN=from-env" in result.stdout  # a real variable always wins


def test_interpolation_is_off(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LITERAL", raising=False)
    src = _project(tmp_path, "LITERAL=${HOME}/x\n")
    result = Runner().invoke("show --name=LITERAL", tasks=src)
    assert result.ok, result.stderr
    assert "LITERAL=${HOME}/x" in result.stdout  # the text on the line


def test_a_named_file_loads_and_a_missing_one_refuses(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TOKEN", raising=False)
    src = _project(tmp_path, None)
    (tmp_path / "prod.env").write_text("TOKEN=prod\n")
    result = Runner().invoke("--env-file=prod.env show", tasks=src)
    assert result.ok, result.stderr
    assert "TOKEN=prod" in result.stdout

    result = Runner().invoke("--env-file=missing.env show", tasks=src)
    assert not result.ok
    assert "missing.env does not exist" in result.stderr


def test_a_bare_mention_loads_the_default_loudly(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TOKEN", raising=False)
    src = _project(tmp_path, "TOKEN=from-file\n")
    result = Runner().invoke("--env-file show", tasks=src)
    assert result.ok, result.stderr
    assert "TOKEN=from-file" in result.stdout


def test_a_bare_mention_with_no_default_refuses_by_its_real_name(tmp_path, monkeypatch):
    # Asking for the default out loud is still asking: plain absence shrugs,
    # the bare mention refuses — and names the file it looked for, never the
    # "." that Path("") used to smuggle in.
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path, None)
    result = Runner().invoke("--env-file show", tasks=src)
    assert not result.ok
    assert ".env does not exist" in result.stderr
    assert ": . does not exist" not in result.stderr


def test_no_file_is_nothing_to_do(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TOKEN", raising=False)
    src = _project(tmp_path, None)
    result = Runner().invoke("show", tasks=src)
    assert result.ok, result.stderr
    assert "TOKEN=<unset>" in result.stdout


def test_a_missing_dotenv_teaches_by_name(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path, "TOKEN=x\n")
    monkeypatch.setitem(sys.modules, "dotenv", None)  # imports now fail
    result = Runner().invoke("show", tasks=src)
    assert not result.ok
    assert "python-dotenv" in result.stderr
