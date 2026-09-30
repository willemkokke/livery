"""Birth end to end: idempotent, interruption-proof, taught refusals."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from livery.forge.testing import FakeForge
from livery.workshop import _new_project as _newborn_module
from livery.workshop._new_project import new_project

#: The birth's tool sync, bound at import, before the module's fixture
#: stubs it for every birth below: the one test of the real function
#: calls this.
_REAL_SYNC_TOOLS = _newborn_module._sync_tools

ROOT = Path(__file__).resolve().parents[3]
TEMPLATES = ROOT / "packages/workshop/src/livery/workshop/templates"

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
    monkeypatch.setattr(
        "livery.workshop._new_project._connect", lambda kind, url: (fake, "")
    )
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
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("livery.workshop._uv.run_uv", lambda *args, root: None)
    # The first lock resolves against the published index over the
    # network and installs what it names; the suite records that it was
    # asked for and writes the lock.
    locked: list[Path] = []

    def _locked(root: Path) -> None:
        locked.append(root)
        # A lock of the schema with nothing in it: the sync's renders read
        # it, and a file that is not a lock would refuse there.
        (root / "tools.lock").write_text('{"schema": 1, "hosts": [], "tools": {}}\n')

    monkeypatch.setattr("livery.workshop._new_project._sync_tools", _locked)
    monkeypatch.setattr(
        "livery.workshop._new_project._locked_roots", locked, raising=False
    )
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
        "templates": str(TEMPLATES),
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
    assert (root / "docs" / "index.md").is_file()  # the docs seed, at birth
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


def test_the_layer_arm_scaffolds_a_self_hosting_home(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _birth(local=True, owner="", layer="brand")
    root = tmp_path / "acme-tools"
    member = root / "packages" / "brand"
    assert (member / "src" / "acme_tools" / "brand" / "_tasks.py").is_file()
    assert (
        member / "src" / "acme_tools" / "brand" / "templates" / "overlay.toml"
    ).is_file()
    fragment = member / "src" / "acme_tools" / "brand" / "content" / "fragments"
    assert (fragment / "rules.brand.md").is_file()
    contract = (root / "workshop.toml").read_text()
    assert (
        'layers = ["livery.workshop", { import = "livery.workshop.layers.docs",'
        ' dist = "livery-workshop" }, "acme_tools.brand"]'
    ) in contract
    pyproject = (member / "pyproject.toml").read_text()
    assert "footman.tasks" in pyproject
    assert '"acme_tools.brand" = "acme_tools.brand._tasks"' in pyproject
    # The roster carries the member; the home's dev group derives it.
    # The convention folds dots to dashes; the namespace keeps its
    # own spelling (acme_tools.brand is acme_tools-brand).
    assert "acme_tools-brand" in (root / ".copier-answers.yml").read_text()
    out = capsys.readouterr().out
    assert "self-hosted, last in the stack" in out
    # The second run walks past the scaffold.
    _birth(local=True, owner="", layer="brand")
    out = capsys.readouterr().out
    assert "already scaffolded" in out


def test_the_push_target_carries_the_token_on_git_transport_only() -> None:
    # The git lane goes over git transport with a token, never the
    # API (the workflows note's definition): an http remote carries
    # the credential in the push URL, anything else pushes to the
    # remote as-is, and a tokenless resolution stays credential-free
    # for a human's own helper.
    from livery.workshop._new_project import _push_target

    url = "http://gitea:3000/livery/loop.git"
    assert _push_target(url, "t") == "http://oauth2:t@gitea:3000/livery/loop.git"
    assert _push_target(url, "") == "origin"
    assert _push_target("git@gitea:livery/loop.git", "t") == "origin"


def test_a_newborn_names_the_index_and_holds_a_lock(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A project born here can run its own gate on the machine that bore it.

    The gate's checkers come from the store, which supplies what a lock
    names, and nothing can be locked until the contract names a catalogue
    to resolve against. The birth seeds both, in that order, so the
    sequence a person had to run by hand is the birth's own.
    """
    from livery.workshop._new_project import PUBLISHED_INDEX

    _birth(local=True)
    contract = (tmp_path / "acme-tools" / "workshop.toml").read_text()
    assert f'index = "{PUBLISHED_INDEX}"' in contract
    # The index is named before the lock is asked for, since the lock
    # resolves against it.
    assert contract.index("[tools]") < contract.index("[ci]")
    assert (tmp_path / "acme-tools" / "tools.lock").is_file()


def test_the_newborn_tool_sync_takes_the_root_and_changes_no_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The birth runs inside a task, and a task may not change directory."""
    from livery.workshop import _tool_tasks

    seen: list[Path] = []
    monkeypatch.setattr(
        _tool_tasks, "sync_tools", lambda root, **kwargs: seen.append(root)
    )
    before = Path.cwd()
    _REAL_SYNC_TOOLS(tmp_path)
    assert seen == [tmp_path]
    assert Path.cwd() == before
