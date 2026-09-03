"""Birth end to end: idempotent, interruption-proof, taught refusals."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from livery.forge.testing import FakeForge
from livery.workshop._new_project import new_project

ROOT = Path(__file__).resolve().parents[3]

_FAILURES = (BaseException,)


@pytest.fixture(autouse=True)
def _birth_rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeForge:
    """A fake forge, a bare origin, and a local template source."""
    fake = FakeForge()
    origin = tmp_path / "origin.git"
    origin.mkdir()
    subprocess.run(
        ["git", "init", "-q", "--bare", "--initial-branch=main"],
        cwd=origin,
        check=True,
    )
    monkeypatch.setattr("livery.workshop._new_project._connect", lambda kind, url: fake)
    monkeypatch.setattr(
        "livery.workshop._new_project._clone_url",
        lambda kind, url, owner, name: str(origin),
    )
    monkeypatch.setattr(
        "livery.workshop._workflow_tasks.assert_configuration",
        lambda root: print("  repository configuration asserted from the contract"),
    )
    from livery.workshop import _new_project as birth

    real_git = birth._git

    def informed(root: Path, *args: str) -> str:
        # The fake never sees a real push; mirror pushes into its
        # branch state so pr.open finds the head, as the forge would.
        out = real_git(root, *args)
        if args and args[0] == "push":
            for ref in args[1:]:
                if not ref.startswith("-") and ref != "origin":
                    fake.push("acme", "acme-tools", ref)
        return out

    monkeypatch.setattr("livery.workshop._new_project._git", informed)
    monkeypatch.setattr("footman.cwd", lambda: tmp_path)
    monkeypatch.setattr("livery.workshop._uv.run_uv", lambda *args, root: None)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    (tmp_path / "gitconfig").write_text(
        "[user]\n\temail = t@l\n\tname = T\n[init]\n\tdefaultBranch = main\n"
    )
    return fake


def _birth(**overrides: Any) -> None:
    arguments: dict[str, Any] = {
        "name": "acme-tools",
        "forge": "gitea",
        "owner": "acme",
        "url": "https://forge.acme.example",
        "templates": str(ROOT / "templates"),
    }
    arguments.update(overrides)
    new_project(**arguments)


def test_birth_end_to_end_and_the_second_run_resumes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _birth()
    root = tmp_path / "acme-tools"
    assert (root / "workshop.toml").is_file()
    assert (root / "pyproject.toml").is_file()
    assert (root / "README.md").is_file() and (root / "LICENSE").is_file()
    assert not (root / "docs").exists()  # docs seeds wait for the toolchain
    assert (root / ".gitea" / "workflows" / "ci.yml").is_file()
    assert (root / "CLAUDE.project.md").is_file()
    out = capsys.readouterr().out
    assert "setup PR: opened" in out
    _birth()
    out = capsys.readouterr().out
    for line in (
        "workshop.toml: already seeded",
        "render: already born",
        "git: already initialised",
        "setup PR: already open",
    ):
        assert line in out


_BOMBS = (
    ("render", "livery.workshop._templates.render"),
    ("sync", "livery.workshop._sync.sync_workspace"),
    ("apply", "livery.workshop._templates.apply_project"),
    ("configure", "livery.workshop._workflow_tasks.assert_configuration"),
    ("setup-pr", "livery.workshop._new_project._open_setup_pr"),
)


@pytest.mark.parametrize(("boundary", "target"), _BOMBS)
def test_a_kill_at_any_boundary_resumes_on_rerun(
    boundary: str,
    target: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _Killed(RuntimeError):
        pass

    def _bomb(*args: Any, **kwargs: Any) -> None:
        raise _Killed(boundary)

    with monkeypatch.context() as scoped:
        scoped.setattr(target, _bomb)
        with pytest.raises((_Killed, *(_FAILURES if boundary else ()))):
            _birth()
    capsys.readouterr()
    _birth()  # the rerun IS the recovery procedure
    out = capsys.readouterr().out
    assert "done: merge the setup PR" in out


def test_local_touches_no_forge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def _never(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("--local must not touch the forge")

    monkeypatch.setattr("livery.workshop._new_project._connect", _never)
    _birth(local=True, owner="")
    out = capsys.readouterr().out
    assert "--local: done" in out and "Skipped" in out
    assert "pushed" not in out


def test_headless_without_owner_refuses_listing_the_answers() -> None:
    with pytest.raises(_FAILURES) as caught:
        _birth(owner="")
    text = str(caught.value)
    assert "--owner" in text and "--local" in text


def test_a_foreign_repository_refuses_before_any_push(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _birth_rig: FakeForge
) -> None:
    fake = _birth_rig
    fake.create_repo("acme", "acme-tools", private=True, description="foreign")
    # The bare origin gains history this checkout does not know.
    foreign = tmp_path / "foreign"
    subprocess.run(
        ["git", "clone", "-q", str(tmp_path / "origin.git"), str(foreign)],
        check=True,
        env={"GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"), "PATH": "/usr/bin:/bin"},
    )
    (foreign / "theirs.txt").write_text("foreign history\n")
    for args in (
        ["git", "add", "-A"],
        ["git", "commit", "-qm", "foreign"],
        ["git", "push", "-q", "origin", "main"],
    ):
        subprocess.run(
            args,
            cwd=foreign,
            check=True,
            env={
                "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"),
                "PATH": "/usr/bin:/bin",
            },
        )
    with pytest.raises(_FAILURES) as caught:
        _birth()
    assert "already exists on the forge with its own" in str(caught.value)


def test_gitea_without_a_url_teaches() -> None:
    with pytest.raises(_FAILURES) as caught:
        _birth(url="")
    assert "--url" in str(caught.value)


def test_a_bad_name_refuses() -> None:
    with pytest.raises(_FAILURES) as caught:
        _birth(name="Bad Name")
    assert "lowercase" in str(caught.value)
