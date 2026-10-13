"""A credential in a command line reaches a profile trace as ``***``.

Each command that carries a token or a password runs here under a
real profile, the trace is read back, and the credential is absent
from it while the program still receives the value. Then the builders
themselves: each hands git its URL as a `livery.footman.Secret`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import livery.footman as footman
import livery.toolroom.tools as tools
from livery import git
from livery.footman import Secret
from livery.workshop import _devenv, _e2e, _new_project
from livery.workshop._packages import Package
from livery.workshop._publish import publish_wheels
from workshop_traced import traced

TOKEN = "s3cret-token-value"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def _work(tmp_path: Path) -> Path:
    """A repository with one commit on main and no remote."""
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q", "--initial-branch=main")
    for key, value in (
        ("user.name", "tester"),
        ("user.email", "tester@example.invalid"),
        ("commit.gpgsign", "false"),
    ):
        _git(work, "config", key, value)
    (work / "README.md").write_text("the repository\n")
    _git(work, "add", "README.md")
    _git(work, "commit", "-q", "-m", "init")
    return work


def test_the_loops_token_remote_reaches_a_trace_as_stars(tmp_path: Path) -> None:
    # The fault first: the loop embeds the lane token in its remote's
    # URL. The trace names the call with *** where the URL stands, and
    # git holds the real URL.
    work = _work(tmp_path)
    _git(work, "remote", "add", "origin", "http://localhost:3000/livery/loop.git")
    text, names = traced(tmp_path, lambda: _e2e._authenticate_remote(work, TOKEN))
    assert TOKEN not in text
    assert "git remote set-url origin ***" in names
    bare = _e2e._lane("gitea").alias.removeprefix("http://")
    assert git.remote_url(work) == (
        f"http://oauth2:{TOKEN}@{bare}/{_e2e.E2E_OWNER}/{_e2e.E2E_REPO}.git"
    )


def test_a_birth_push_reaches_a_trace_as_stars_and_lands(tmp_path: Path) -> None:
    # The fault first: a birth pushes to a URL carrying the forge token.
    # The rewrite sends exactly that URL to a local bare repository, so
    # the push lands only when git receives the real value, while the
    # trace names the push with *** where the URL stands.
    work = _work(tmp_path)
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "--initial-branch=main", str(origin))
    url = f"http://oauth2:{TOKEN}@forge.example/acme/tools.git"
    _git(work, "config", f"url.{origin.as_posix()}.insteadOf", url)

    def push() -> None:
        target = _new_project._push_target("http://forge.example/acme/tools.git", TOKEN)
        _new_project._git(work, "push", "-q", "--force", "-u", target, "main")

    text, names = traced(tmp_path, push)
    assert TOKEN not in text
    assert "git push -q --force -u *** main" in names
    assert _git(origin, "rev-parse", "main") == _git(work, "rev-parse", "HEAD")


def test_a_publish_token_reaches_a_trace_as_stars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The fault first: uv publish takes the index token as an argument.
    # Python stands in for uv and runs the file named like uv's
    # subcommand, which exits 0; the shown line keeps uv's name.
    member = tmp_path / "thing"
    (member / "dist").mkdir(parents=True)
    (member / "dist" / "thing-0.1.0-py3-none-any.whl").write_bytes(b"")
    (member / "publish").write_text("pass\n")
    monkeypatch.setattr(tools, "uv", tools.uv.at(sys.executable))
    package = Package(
        directory=member,
        path="packages/thing",
        name="thing",
        kind="python",
        depends=(),
    )
    text, names = traced(
        tmp_path,
        lambda: publish_wheels(
            package, index_url="https://index.test/upload", token=TOKEN
        ),
    )
    assert TOKEN not in text
    (shown,) = [name for name in names if name.startswith("uv publish ")]
    assert "--token *** " in shown


#: The stand-in for gitea and its runner: a token for each mint, and
#: nothing for any other call. A file, so its text is not in the trace.
_MINTS = (
    "import sys\n"
    "if 'generate-access-token' in sys.argv:\n"
    "    print('minted-api-token')\n"
    "elif 'generate-runner-token' in sys.argv:\n"
    "    print('minted-runner-token')\n"
)


def test_the_dev_seeds_password_and_runner_token_reach_a_trace_as_stars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The fault first: host mode creates the admin with a password and
    # registers a runner with the minted registration token, both as
    # arguments. A python script stands in for both programs, and
    # footman's own run records each call.
    env = _devenv.Environment("dev", "host", tmp_path / "dev")
    env.gitea_dir.mkdir(parents=True)
    gitea, runner = tmp_path / "gitea", tmp_path / "gitea-runner"
    mints = tmp_path / "mints.py"
    mints.write_text(_MINTS)
    real_run = footman.run

    def stand_in(cmd: Any, *args: Any, **kwargs: Any) -> footman.Result:
        # The program alone changes: every other call passes as it came.
        if isinstance(cmd, list) and cmd and cmd[0] in (str(gitea), str(runner)):
            cmd = [sys.executable, str(mints), *cmd[1:]]
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(footman, "run", stand_in)
    monkeypatch.setattr(_devenv, "API", lambda url, path, token, **kwargs: 200)
    monkeypatch.setattr(_devenv, "SPAWN", lambda argv, **kwargs: 4001)
    monkeypatch.setattr(_devenv, "cache_dir", lambda: tmp_path / "cache")
    url = "http://localhost:43210"

    def up() -> None:
        values = _devenv._seed(env, gitea, url)
        _devenv._start_runner(env, runner, 1, url, values["GITEA_RUNNER_TOKEN"])

    text, names = traced(tmp_path, up)
    assert env.values()["GITEA_RUNNER_TOKEN"] == "minted-runner-token"
    assert "livery-dev-password" not in text
    assert "minted-runner-token" not in text
    assert any(" --password *** " in name for name in names)
    assert any(f" register --instance {url} --token *** " in name for name in names)


def test_each_url_builder_hands_git_a_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A Secret is the one form footman shows as ***: a string operation
    # on the way to git would hand it a plain str, shown in the clear.
    target = _new_project._push_target("https://forge.example/acme/tools.git", TOKEN)
    assert isinstance(target, Secret)
    assert target == f"https://oauth2:{TOKEN}@forge.example/acme/tools.git"
    handed: list[str] = []
    monkeypatch.setattr(
        git, "set_remote_url", lambda root, name, url: handed.append(url)
    )
    _e2e._authenticate_remote(tmp_path, TOKEN)
    assert [type(url) for url in handed] == [Secret]
