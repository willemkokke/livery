"""Birth end to end: idempotent, interruption-proof, taught refusals."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from livery.forge.testing import FakeForge
from livery.workshop import _new_project as _newborn_module
from livery.workshop._new_project import new_project

#: The hand-off to the newborn's own runner, bound at import, before the
#: module's fixture stubs it for every birth below: the tests of the
#: real function call this.
_REAL_HAND_OFF = _newborn_module._hand_off

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

    # A lock resolves against the published index over the network and
    # installs what it names. Every step of a birth reaches it through
    # one engine, the newborn's first lock and a wired member's alike,
    # so the suite fakes the engine and writes an empty lock.
    def _locked(root: Path, **_flags: object) -> None:
        # A lock of the schema with nothing in it: the sync's renders read
        # it, and a file that is not a lock would refuse there.
        (root / "tools.lock").write_text('{"schema": 1, "hosts": [], "tools": {}}\n')

    monkeypatch.setattr("livery.workshop._tool_tasks.sync_tools", _locked)

    # The finish runs with the runner the newborn's environment holds,
    # which the suite never builds: here it runs in this process.
    monkeypatch.setattr(
        "livery.workshop._new_project._hand_off",
        lambda root, **answers: birth._finish(root, **answers),
    )

    # Nothing in a birth test reaches the network: a fetch that escapes
    # the fakes refuses here, naming its host, instead of reading the
    # live site, whose answers a deploy can change mid-run.
    def offline(address: object, *_args: object, **_kwargs: object) -> object:
        raise AssertionError(f"a birth test reached the network: {address}")

    monkeypatch.setattr("socket.create_connection", offline)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    (tmp_path / "gitconfig").write_text(
        "[user]\n\temail = t@l\n\tname = T\n[init]\n\tdefaultBranch = main\n"
    )
    return fake


def _birth(**overrides: Any) -> None:
    arguments: dict[str, Any] = {
        "folder": "acme-tools",
        "forge": "gitea",
        "owner": "acme",
        "url": "https://forge.acme.example",
    }
    arguments.update(overrides)
    new_project(**arguments)


def test_a_birth_test_that_reaches_the_network_refuses_naming_the_host() -> None:
    # The fallback before the births: a fetch the fakes miss fails here,
    # by name, rather than passing or failing with whatever the live
    # site answers at that moment.
    from livery.strongroom.api import fetch_url

    with (
        pytest.raises(
            AssertionError, match=r"reached the network: .*docs\.willem\.net"
        ),
        fetch_url(
            "https://docs.willem.net/livery/tools/pointer.json",
            connect_timeout=5,
            transfer_timeout=5,
        ),
    ):
        pass


def test_the_verb_is_offered_outside_a_project_and_inside_one(tmp_path: Path) -> None:
    # Outside one it starts a birth, inside one it resumes: a task of a
    # package is offered inside a project only unless it says otherwise.
    import os
    import subprocess
    import sys

    bridge = tmp_path / "bridge-config"
    bridge.mkdir()
    (bridge / "tasks.py").write_text(
        'from livery.footman.api import plugin\n\nplugin("livery.workshop")\n'
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    env = {**os.environ, "FOOTMAN_CONFIG_DIR": str(bridge)}
    shown = subprocess.run(
        [sys.executable, "-m", "livery.footman", "new.project", "--help"],
        cwd=outside,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert shown.returncode == 0, shown.stdout + shown.stderr
    # Hidden from the options: the refusal and the description teach it.
    options = [line.strip() for line in shown.stdout.splitlines()]
    assert not any(line.startswith("--resume") for line in options)


# The refusals first: where no birth may start, nothing is written.


def test_a_folder_holding_a_project_refuses_and_names_resume(
    tmp_path: Path,
) -> None:
    _birth(local=True)
    with pytest.raises(_FAILURES) as caught:
        _birth(local=True)
    assert "new.project --resume" in str(caught.value)


def test_a_folder_that_is_not_empty_refuses(tmp_path: Path) -> None:
    folder = tmp_path / "acme-tools"
    (folder / "packages").mkdir(parents=True)
    (folder / "pyproject.toml").write_text("[project]\n")
    with pytest.raises(_FAILURES) as caught:
        _birth()
    assert "is not empty" in str(caught.value)
    assert not (folder / "workshop.toml").exists()


def test_resume_without_a_project_refuses(tmp_path: Path) -> None:
    with pytest.raises(_FAILURES) as caught:
        _birth(resume=True)
    assert "holds no project to resume" in str(caught.value)
    assert not (tmp_path / "acme-tools").exists()


def test_the_folder_names_the_project_unless_a_name_is_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A birth with no folder is born where the caller stands.
    here = tmp_path / "Odd_Dir"
    here.mkdir()
    monkeypatch.chdir(here)
    with pytest.raises(_FAILURES) as caught:
        new_project(local=True)
    assert "--name" in str(caught.value)
    assert list(here.iterdir()) == []
    new_project(name="odd-dir", local=True)
    assert 'name = "odd-dir"' in (here / "workshop.toml").read_text()


def test_a_birth_lists_the_stack_it_is_given(tmp_path: Path) -> None:
    _birth(local=True, stack="ruff, basedpyright[typecomplete]")
    contract = (tmp_path / "acme-tools" / "workshop.toml").read_text()
    assert 'extensions = ["ruff", "basedpyright[typecomplete]"]' in contract


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
    _birth(resume=True)
    out = capsys.readouterr().out
    for line in (
        "seeds: already written",
        "git: already initialised",
        "setup PR: already open",
    ):
        assert line in out


_BOMBS = (
    ("seeds", "livery.workshop._seeds.create"),
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
    _birth(resume=True)  # the resume IS the recovery procedure
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


def test_the_extension_arm_scaffolds_a_self_hosting_home(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _birth(local=True, owner="", extension="brand")
    root = tmp_path / "acme-tools"
    member = root / "packages" / "brand"
    assert (member / "src" / "acme_tools" / "brand" / "_tasks.py").is_file()
    fragment = member / "src" / "acme_tools" / "brand" / "content" / "fragments"
    assert (fragment / "rules.brand.md").is_file()
    contract = (root / "workshop.toml").read_text()
    # The base is never listed; the site's extension rides in its wheel.
    assert (
        'extensions = ["docs", "ruff", "basedpyright", "acme_tools.brand"]' in contract
    )
    pyproject = (member / "pyproject.toml").read_text()
    assert "footman.tasks" in pyproject
    assert '"acme_tools.brand" = "acme_tools.brand._tasks"' in pyproject
    # The member's contract names it; the home's dev group derives it.
    # The convention folds dots to dashes; the namespace keeps its
    # own spelling (acme_tools.brand is acme_tools-brand).
    assert '"acme_tools-brand' in (root / "pyproject.toml").read_text()
    out = capsys.readouterr().out
    assert "self-hosted, last in the stack" in out
    # The resume walks past the scaffold.
    _birth(local=True, owner="", extension="brand", resume=True)
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


def test_the_birth_finishes_with_the_runner_its_environment_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The birth runs inside a task, and a task may not change directory."""
    import sys

    from livery.workshop._pythons import scripts_dir

    # The refusal first: an environment with no runner of its own.
    with pytest.raises(_FAILURES) as caught:
        _REAL_HAND_OFF(tmp_path)
    assert "environment has no fm to finish the birth with" in str(caught.value)
    runner = scripts_dir(tmp_path / ".venv") / (
        "fm.exe" if sys.platform == "win32" else "fm"
    )
    runner.parent.mkdir(parents=True)
    runner.write_text("")
    seen: list[tuple[list[str], object, object]] = []

    def _run(
        argv: list[str], *, cwd: object = None, capture: object = True, **_: object
    ) -> None:
        seen.append((argv, cwd, capture))

    monkeypatch.setattr("livery.footman.api.run", _run)
    before = Path.cwd()
    _REAL_HAND_OFF(tmp_path, owner="acme", local=True)
    # Inside the project, sharing the console, passing what the
    # contract does not hold.
    argv = [str(runner), "new.project", "--resume", "--owner=acme", "--local"]
    assert seen == [(argv, tmp_path, False)]
    assert Path.cwd() == before


def test_a_resume_inside_the_project_finishes_it_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A local birth, then its forge half from inside the project: this
    # process is the project's own runner, so nothing is handed off.
    _birth(local=True)
    root = tmp_path / "acme-tools"

    def _never(root: Path, **answers: object) -> None:
        raise AssertionError("a resume inside the project finishes in place")

    monkeypatch.setattr("livery.workshop._new_project._hand_off", _never)
    monkeypatch.chdir(root)
    capsys.readouterr()
    new_project(resume=True)
    assert "setup PR: opened" in capsys.readouterr().out


def test_a_birth_lists_the_site_first_and_a_brand_after_it() -> None:
    from livery.workshop._new_project import birth_extensions

    # The fallback first: an App with no builtins of its own is stock,
    # and lists the site's extension, the python formatter's and the
    # type checker's.
    assert birth_extensions(()) == ["docs", "ruff", "basedpyright"]
    assert birth_extensions(("footman.profile", "livery.workshop")) == [
        "docs",
        "ruff",
        "basedpyright",
    ]
    # A brand's extension follows the stock ones, so it wins.
    assert birth_extensions(("dummy.brandx", "livery.workshop")) == [
        "docs",
        "ruff",
        "basedpyright",
        "dummy.brandx",
    ]
    # An App that does not carry the base lists its own alone.
    assert birth_extensions(("acme.only",)) == ["acme.only"]
