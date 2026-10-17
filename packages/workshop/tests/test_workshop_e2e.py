"""``fm ci.e2e``'s provisioning: refusals first, then the idempotent path."""

from __future__ import annotations

import getpass
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from livery import git
from livery.footman import Failed
from livery.forge import Conclusion, Repository, Run
from livery.forge.testing import FakeForge
from livery.workshop import _devenv, _e2e, _e2e_rows
from livery.workshop._ci_context import state_store
from livery.workshop._declaration import Declaration
from workshop_installed import skip_unless_installed
from workshop_rig import Machine, machine_fixture, modes_bind, rig_fixture  # noqa: F401
from workshop_seeds import Seeds, _seed_home, pushed, seed_copier  # noqa: F401

_FAILURES = (BaseException,)


def test_a_kind_with_no_local_containers_refuses_naming_the_lanes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITEA_URL", "http://gitea.example")
    monkeypatch.setenv("GITEA_TOKEN", "t")
    with pytest.raises(
        _FAILURES, match="not a local lane: the loop runs against gitea, gitlab"
    ):
        _e2e.provision("svn")


def test_each_lane_addresses_its_own_registry() -> None:
    gitea, gitlab = _e2e.LANES["gitea"], _e2e.LANES["gitlab"]
    assert gitea.host == "gitea" and gitlab.host == "gitlab"
    # Gitea keeps an owner's registry; GitLab a project's, by its
    # URL-encoded path, so one URL is true on both sides of the loop.
    assert gitea.publish() == "http://gitea:3000/api/packages/livery/pypi"
    assert gitea.index() == "http://gitea:3000/api/packages/livery/pypi/simple"
    assert gitlab.publish() == (
        "http://gitlab:8929/api/v4/projects/livery%2Fci-e2e-loop/packages/pypi"
    )
    assert gitlab.index("http://localhost:8929") == (
        "http://localhost:8929/api/v4/projects/livery%2Fci-e2e-loop/packages/pypi"
        "/simple"
    )


def test_gitlab_provisioning_mints_the_push_token_and_sets_it_masked(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GITLAB_URL", "http://gitlab.example")
    monkeypatch.setenv("GITLAB_TOKEN", "the-lane-token")
    fake = FakeForge()
    monkeypatch.setattr(
        "livery.forge.GitlabForge.connect",
        staticmethod(lambda url, token: fake),
    )
    calls: list[tuple[str, str, object]] = []

    def api(
        method: str, url: str, token: str, body: object = None
    ) -> tuple[int, object]:
        calls.append((method, url, body))
        if method == "GET":
            return 200, [{"id": 4, "name": "livery-loop-push", "active": True}]
        if method == "DELETE":
            return 204, None
        if method == "PUT":
            return 200, {"visibility": "public"}
        return 201, {"id": 5, "token": "glpat-minted"}

    mint = _e2e._mint_push_token
    monkeypatch.setattr(
        _e2e, "_mint_push_token", lambda url, token: mint(url, token, api)
    )
    public = _e2e._make_project_public
    monkeypatch.setattr(
        _e2e, "_make_project_public", lambda url, token: public(url, token, api)
    )
    _e2e.provision("gitlab")
    out = capsys.readouterr().out
    assert "project: public" in out
    assert (
        "secrets set: UV_PUBLISH_TOKEN, FORGE_TOKEN, FORGE_ADMIN_TOKEN,"
        " GITLAB_PUSH_TOKEN" in out
    )
    state = fake._repos[(_e2e.E2E_OWNER, _e2e.E2E_REPO)]
    assert state.secrets["GITLAB_PUSH_TOKEN"] == "glpat-minted"
    assert state.secrets["FORGE_TOKEN"] == "the-lane-token"
    # The token minted before, by name, is revoked before a new one is
    # minted with the push scope, since a value is readable at minting alone.
    methods = [(method, url.rsplit("/", 1)[-1]) for method, url, _ in calls]
    assert methods == [
        ("PUT", "livery%2Fci-e2e-loop"),
        ("GET", "access_tokens"),
        ("DELETE", "4"),
        ("POST", "access_tokens"),
    ]
    assert calls[0][2] == {"visibility": "public"}
    assert calls[-1][2] == {
        "name": "livery-loop-push",
        "scopes": ["api", "write_repository"],
        "access_level": 40,
        "expires_at": calls[-1][2]["expires_at"],  # type: ignore[index]
    }
    assert "livery%2Fci-e2e-loop" in calls[0][1]


def test_a_refused_visibility_change_names_the_status() -> None:
    # The refusal first: a project that stays private serves its index
    # to nobody without a credential, so the failure names the status.
    def api(
        method: str, url: str, token: str, body: object = None
    ) -> tuple[int, object]:
        return 403, {"message": "403 Forbidden"}

    with pytest.raises(_FAILURES) as caught:
        _e2e._make_project_public("http://gitlab.example", "t", api)
    assert "public answered HTTP 403" in str(caught.value)


def test_a_refused_mint_names_the_status(monkeypatch: pytest.MonkeyPatch) -> None:
    def api(
        method: str, url: str, token: str, body: object = None
    ) -> tuple[int, object]:
        return (200, []) if method == "GET" else (403, "insufficient scope")

    with pytest.raises(_FAILURES, match="push token was not minted: HTTP 403"):
        _e2e._mint_push_token("http://gitlab.example", "t", api)


def test_the_purge_addresses_the_lane(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, ...]] = []

    def gitea(base: str, owner: str, **kw: object) -> list[str]:
        seen.append(("gitea", base, owner))
        return ["a==1"]

    def gitlab(base: str, project: str, **kw: object) -> list[str]:
        seen.append(("gitlab", base, project))
        return ["b==2"]

    monkeypatch.setattr("livery.forge._registry.purge_packages", gitea)
    monkeypatch.setattr("livery.forge._registry.purge_gitlab_packages", gitlab)
    assert _e2e._purge("gitea", "http://h", "t") == ["a==1"]
    assert _e2e._purge("gitlab", "http://h", "t", names=["b"]) == ["b==2"]
    assert seen == [
        ("gitea", "http://h", "livery"),
        ("gitlab", "http://h", "livery/ci-e2e-loop"),
    ]


def test_missing_credentials_teach_the_dev_up_verb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITEA_URL", raising=False)
    monkeypatch.delenv("GITEA_TOKEN", raising=False)
    with pytest.raises(_FAILURES, match=r"devenv\.up"):
        _e2e.provision("gitea")


def test_provisioning_creates_then_reuses_and_writes_the_secret(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GITEA_URL", "http://gitea.example")
    monkeypatch.setenv("GITEA_TOKEN", "the-lane-token")
    fake = FakeForge()
    monkeypatch.setattr(
        "livery.forge.GiteaForge.connect",
        staticmethod(lambda url, token: fake),
    )
    _e2e.provision("gitea")
    first = capsys.readouterr().out
    assert f"created {_e2e.E2E_OWNER}/{_e2e.E2E_REPO}" in first
    assert "UV_PUBLISH_TOKEN" in first
    # The fake's private state is the assertion surface: secrets are
    # write-only through the protocol, by design.
    state = fake._repos[(_e2e.E2E_OWNER, _e2e.E2E_REPO)]
    for key in ("UV_PUBLISH_TOKEN", "FORGE_TOKEN", "FORGE_ADMIN_TOKEN"):
        assert state.secrets[key] == "the-lane-token"
    # Re-running is the recovery procedure: the second pass reuses.
    _e2e.provision("gitea")
    assert f"reusing {_e2e.E2E_OWNER}/{_e2e.E2E_REPO}" in capsys.readouterr().out


def test_the_registration_gate_sees_only_a_source_checkout() -> None:
    # The verb registers only beside the workshop's own tests: from
    # this source checkout the path arithmetic finds this very suite,
    # and from an installed wheel (the release legs) it finds no tests
    # directory at all, which is what keeps the verb off an instance.
    here = Path(__file__).resolve().parent
    if here == _e2e._WORKSHOP_TESTS:
        assert (_e2e._WORKSHOP_TESTS / "test_workshop_e2e.py").is_file()
    else:
        assert "site-packages" in str(Path(_e2e.__file__).resolve())
        assert not _e2e._WORKSHOP_TESTS.is_dir()


def test_a_hostless_alias_teaches_the_one_liner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.request

    def refuse(*args: object, **kwargs: object) -> object:
        raise OSError("unreachable")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    with pytest.raises(_FAILURES, match="/etc/hosts"):
        _e2e._require_host_alias()
    with pytest.raises(_FAILURES, match=r"echo \"127.0.0.1 gitlab\""):
        _e2e._require_host_alias("gitlab")
    # An HTTP refusal is an answer: the host resolved the alias, and
    # GitLab's version endpoint answers an anonymous read with 401.
    import urllib.error
    from email.message import Message

    def answer_401(*args: object, **kwargs: object) -> object:
        raise urllib.error.HTTPError(
            "http://gitlab:8929", 401, "Unauthorized", Message(), None
        )

    monkeypatch.setattr(urllib.request, "urlopen", answer_401)
    _e2e._require_host_alias("gitlab")


def test_the_runner_probe_refuses_a_missing_runner_and_a_missing_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The refusals first: a runner that is not up, then one without
    # the socket, each teaching the up verb with its flag; a mounted
    # socket passes quietly.
    from types import SimpleNamespace

    import livery.toolroom.tools as tools

    answer = {"code": 1, "stdout": ""}
    seen: list[tuple[str, ...]] = []

    def _opts(**kwargs: object) -> object:
        def _run(*args: str) -> object:
            seen.append(args)
            return SimpleNamespace(code=answer["code"], stdout=answer["stdout"])

        return _run

    monkeypatch.setattr(tools, "docker", SimpleNamespace(opts=_opts))
    with pytest.raises(_FAILURES, match=r"gitlab-runner-1\) is not up"):
        _e2e._require_runner_docker("gitlab")
    assert seen[-1][-1] == "livery-forge-dev-gitlab-runner-1"
    answer.update(code=0, stdout='[{"Destination": "/etc/gitlab-runner"}]')
    with pytest.raises(_FAILURES, match="--profile=gitlab --with-docker"):
        _e2e._require_runner_docker("gitlab")
    answer.update(stdout="not json")
    with pytest.raises(_FAILURES, match="has no docker socket"):
        _e2e._require_runner_docker("gitea")
    assert seen[-1][-1] == "livery-forge-dev-act_runner-1"
    answer.update(stdout='[{"Destination": "/var/run/docker.sock", "Type": "bind"}]')
    _e2e._require_runner_docker("gitea")


def test_a_deletable_receipt_on_gitlab_is_the_contracts_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The refusal first: GitLab refuses a protected tag's deletion to
    # everyone over git, so a delete that lands means no protection,
    # and the receipt is pushed back before the failure names it. A
    # refused delete holds on every forge.
    from types import SimpleNamespace

    import livery.toolroom.tools as tools

    pushes: list[tuple[str, ...]] = []
    deny = {"code": 0}

    def _opts(**kwargs: object) -> object:
        def _run(*args: str) -> object:
            if args[0] == "push":
                pushes.append(args)
                if args[-1].startswith(":refs/tags/"):
                    return SimpleNamespace(code=deny["code"], stdout="", stderr="")
            return SimpleNamespace(code=0, stdout="", stderr="")

        return _run

    monkeypatch.setattr(tools, "git", SimpleNamespace(opts=_opts))
    with pytest.raises(_FAILURES, match="deletable on gitlab"):
        _e2e._require_receipt_protected(tmp_path, "packages/loop-echo/v0.1.0", "gitlab")
    assert pushes[-1] == (
        "push",
        "--quiet",
        "origin",
        "refs/tags/packages/loop-echo/v0.1.0",
    )
    deny["code"] = 1
    _e2e._require_receipt_protected(tmp_path, "packages/loop-echo/v0.1.0", "gitlab")
    assert "delete refused; protection holds" in capsys.readouterr().out


def test_a_birth_that_never_reached_the_forge_resumes_as_a_first_birth(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The forge refused the repository, so the workspace has a commit
    # and no remote; authenticating that remote would fail every rerun.
    import subprocess

    root = tmp_path / _e2e.E2E_REPO
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    calls: list[str] = []
    monkeypatch.setattr(_e2e, "_dev_forge", lambda kind: (object(), "token-abc"))
    monkeypatch.setattr(_e2e, "_loop_home", lambda kind: tmp_path)
    monkeypatch.setattr(_e2e, "_dev_index", lambda kind, stack=(): "")

    def _authenticate(root: Path, token: str, kind: str = "gitea") -> None:
        calls.append("authenticate")

    def _birth(
        kind: str,
        url: str,
        index: str = "",
        stack: tuple[str, ...] = (),
        driver: Path | None = None,
    ) -> Path:
        calls.append("birth")
        return root

    monkeypatch.setattr(_e2e, "_authenticate_remote", _authenticate)
    monkeypatch.setattr(_e2e, "_align_main", lambda root: calls.append("align"))
    monkeypatch.setattr(_e2e, "_birth", _birth)

    class _Stop(Exception):
        pass

    def _stop(kind: str) -> None:
        raise _Stop

    monkeypatch.setattr(_e2e, "provision", _stop)
    with pytest.raises(_Stop):
        _e2e._born(_e2e.Pass("gitea", "http://localhost:1"))  # pyright: ignore[reportPrivateUsage]
    assert calls == ["birth", "authenticate"]
    # A workspace the forge holds authenticates and aligns before it resumes.
    subprocess.run(
        ["git", "remote", "add", "origin", "http://localhost:1/x.git"],
        cwd=root,
        check=True,
    )
    calls.clear()
    with pytest.raises(_Stop):
        _e2e._born(_e2e.Pass("gitea", "http://localhost:1"))  # pyright: ignore[reportPrivateUsage]
    assert calls == ["authenticate", "align", "birth", "authenticate"]


def test_the_pass_renders_the_loop_with_its_own_workshop_and_no_handoff(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The render the lock needs runs before the loop's venv holds the dev
    # wheels: a failed one stops the pass with its own words.
    import sys
    from types import SimpleNamespace

    seen: list[tuple[list[str], object, str]] = []
    code = [3]
    builtins: list[str] = []

    def run(argv: list[str], **kw: object) -> SimpleNamespace:
        env = kw["env"]
        assert isinstance(env, dict)
        seen.append((argv, kw["cwd"], str(env.get("FOOTMAN_NO_UV", ""))))
        # The render runs with no pyproject, so the project rung offers
        # nothing: the child's own config names the workshop instead.
        config = Path(str(env["FOOTMAN_CONFIG_DIR"])) / "config.toml"
        builtins.append(config.read_text("utf-8"))
        if argv[-1] == "--plugins":
            # The listing a failure prints: what mounted, from which rung.
            return SimpleNamespace(
                code=0, stdout="livery-workshop 0.8.0\n  livery.workshop\n", stderr=""
            )
        return SimpleNamespace(
            code=code[0], stdout="  updated pyproject.toml\n", stderr="no index"
        )

    monkeypatch.setattr("livery.footman.run", run)
    with pytest.raises(
        _FAILURES, match="the pass's render of the loop exited 3"
    ) as caught:
        _e2e._render_with_the_pass(tmp_path)  # pyright: ignore[reportPrivateUsage]
    assert "footman's plugins in the loop:\nlivery-workshop 0.8.0" in str(caught.value)
    code[0] = 0
    _e2e._render_with_the_pass(tmp_path)  # pyright: ignore[reportPrivateUsage]
    argv = [sys.executable, "-m", "livery.footman", "--yes", "drift.check", "--fix"]
    plugins = [sys.executable, "-m", "livery.footman", "--plugins"]
    assert seen == [
        (argv, tmp_path, "1"),
        (plugins, tmp_path, "1"),
        (argv, tmp_path, "1"),
    ]
    assert len(builtins) == 3
    assert all('[builtins]\nuser = ["livery.workshop"]\n' in text for text in builtins)
    assert "updated pyproject.toml" in capsys.readouterr().out


def test_the_tree_reset_keeps_what_sync_materialises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A reset cleans a pass's residue and never the stubs, receipts or venv."""
    from types import SimpleNamespace

    import livery.toolroom.tools as tools

    calls: list[tuple[str, ...]] = []

    def _opts(**kwargs: object) -> object:
        def _run(*args: str) -> object:
            calls.append(args)
            if args[:2] == ("clean", "-ndx"):
                return SimpleNamespace(code=0, stdout="Would remove dist/\n", stderr="")
            return SimpleNamespace(code=0, stdout="", stderr="")

        return _run

    # The branch sweep reads the checkout's refs in-process, so the
    # root is a repository with no branch beyond main.
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    monkeypatch.setattr(tools, "git", SimpleNamespace(opts=_opts))
    _e2e._align_main(tmp_path)
    cleans = [call for call in calls if call[0] == "clean"]
    kept = ("-e", ".venv", "-e", "typings", "-e", ".workshop")
    assert cleans == [("clean", "-ndx", *kept), ("clean", "-fdx", *kept)]
    assert "cleaned: dist/" in capsys.readouterr().out


def _loop_member(
    root: Path, member: str, contract: str, *, pyproject: bool = False, recipe: str = ""
) -> None:
    """Write the loop member *member* of *root*: its *contract* lines and its files.

    With *pyproject*, a ``pyproject.toml``, which discovery asks of a
    python member; *recipe* is its ``conanfile.py`` when one is given.
    """
    directory = root / "packages" / member
    directory.mkdir(parents=True)
    (directory / "workshop.toml").write_text(
        f'{contract}name = "ci_e2e_loop-{member}"\n'
    )
    if pyproject:
        (directory / "pyproject.toml").write_text(
            f'[project]\nname = "ci_e2e_loop-{member}"\nversion = "0.1.0"\n'
        )
    if recipe:
        (directory / "conanfile.py").write_text(recipe)


def test_the_loop_probes_each_member_through_its_extensions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Each member's probe is its extensions' answer, asked by its contract's name.

    The python index normalises the name, so the name the wave publishes
    a member under finds it there. Each probe is built at the target the
    loop's workspace resolves, and polled through the wave's wait. The
    conan extension's suite judges the probe of the loop's conan member.
    """
    skip_unless_installed("nanobind")
    import livery.forge as forge_package

    index = "http://gitea:3000/pypi/simple"
    asked: list[tuple[str, str]] = []
    built: list[tuple[str, str]] = []

    class _Simple:
        def __init__(self, url: str, *, token: str = "") -> None:
            built.append(("python", f"{url} {token}"))

        def versions(self, name: str) -> tuple[str, ...]:
            asked.append(("python", name))
            return ("0.1.0",)

    monkeypatch.setattr(forge_package, "SimpleRegistry", _Simple)
    monkeypatch.setenv("PYTHON_REGISTRY_URL", index)
    monkeypatch.setenv("PYTHON_REGISTRY_TOKEN", "lane-token")
    root = tmp_path / "loop"
    root.mkdir()
    (root / "workshop.toml").write_text("[workspace]\n")
    # The python members as their seeds write them: the echo lists
    # python, and the native member lists nanobind and consumes conan.
    _loop_member(
        root,
        "loop-echo",
        'extensions = ["python"]\n',
        pyproject=True,
    )
    _loop_member(
        root,
        "loop-native",
        'extensions = ["nanobind"]\n',
        pyproject=True,
        recipe='class N:\n    requires = ("fmt/[>=11.0]",)\n',
    )
    served = _e2e._serving_probe(root)
    assert served("loop-echo") == ("0.1.0",)
    assert served("loop-native") == ("0.1.0",)
    assert asked == [
        ("python", "ci_e2e_loop-loop-echo"),
        ("python", "ci_e2e_loop-loop-native"),
    ]
    assert built == [
        ("python", f"{index} lane-token"),
        ("python", f"{index} lane-token"),
    ]


# --- the dev-wheel pins: refusals first, then the read -----------------------

HEAD = "05482de" + "0" * 33
WHEEL = "livery_{member}-{version}-py3-none-any.whl"
#: The closure the fake workspaces below declare: the workshop and its edges.
MEMBERS = ("workshop", "forge", "toolroom", "footman")


def _wheel(root: Path, member: str, version: str, *, age: float = 0.0) -> Path:
    dist = root / "packages" / member / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    wheel = dist / WHEEL.format(member=member, version=version)
    wheel.write_bytes(b"")
    stamp = time.time() - age
    os.utime(wheel, (stamp, stamp))
    return wheel


_EDGES = "".join(
    f'\n[[depends]]\npath = "packages/{member}"\nkind = "runtime"\nfloor = "0"\n'
    for member in ("toolroom", "footman", "forge")
)


def _member(root: Path, name: str, *edges: str) -> None:
    home = root / "packages" / name
    home.mkdir(parents=True)
    declared = "".join(
        f'\n[[depends]]\npath = "packages/{edge}"\nkind = "runtime"\nfloor = "0"\n'
        for edge in edges
    )
    (home / "workshop.toml").write_text(
        f'extensions = ["python"]\nname = "livery-{name}"\n' + declared
    )
    (home / "pyproject.toml").write_text(
        f'[project]\nname = "livery-{name}"\nversion = "0.1.0"\n'
    )


def test_dev_members_refuse_a_workspace_without_the_workshop(tmp_path: Path) -> None:
    _member(tmp_path, "cbor")
    with pytest.raises(_FAILURES, match="no packages/workshop member"):
        _e2e.dev_members(tmp_path)


def test_dev_members_are_the_workshop_s_closure_once_each(tmp_path: Path) -> None:
    """Every member a wheel of the workshop resolves, and none the closure misses."""
    _member(tmp_path, "workshop", "toolroom", "footman", "forge", "toolroom-store")
    _member(tmp_path, "toolroom", "footman")
    _member(tmp_path, "footman")
    _member(tmp_path, "forge", "footman", "toolroom")
    _member(tmp_path, "toolroom-store", "toolroom", "strongroom")
    _member(tmp_path, "strongroom")
    _member(tmp_path, "cbor")
    assert _e2e.dev_members(tmp_path) == (
        "workshop",
        "toolroom",
        "footman",
        "forge",
        "toolroom-store",
        "strongroom",
    )


def test_dev_members_take_the_extensions_a_birth_lists(tmp_path: Path) -> None:
    # An extension depends on the workshop, so the workshop's closure
    # never reaches it, and a newborn listing it would install nothing.
    _member(tmp_path, "workshop", "toolroom")
    _member(tmp_path, "toolroom")
    _member(tmp_path, "unlisted", "workshop")
    home = tmp_path / "packages" / "extensions" / "ruff"
    home.mkdir(parents=True)
    (home / "workshop.toml").write_text(
        'extensions = ["python"]\nname = "livery-extensions-ruff"\n'
        '\n[[depends]]\npath = "packages/workshop"\nkind = "runtime"\nfloor = "0"\n'
    )
    (home / "pyproject.toml").write_text('[project]\nname = "livery-extensions-ruff"\n')
    assert _e2e.dev_members(tmp_path) == ("workshop", "extensions/ruff", "toolroom")
    # An entry that turns options on names its extension all the same.
    assert _e2e.dev_members(tmp_path, ("ruff[deep]",)) == (
        "workshop",
        "extensions/ruff",
        "toolroom",
    )


# The extension under test: the refusals first.


def test_an_extension_nothing_declares_refuses_naming_the_installed() -> None:
    skip_unless_installed("python")
    with pytest.raises(
        _FAILURES, match="no installed distribution declares it"
    ) as caught:
        _e2e.extension_under_test("no-such-extension")
    # The refusal names the installed extensions, python among them.
    assert "python" in str(caught.value)


def _declared(
    *extensions: str, requires: tuple[str, ...] = (), phase: str = "lint"
) -> Declaration:
    """One step of *phase* judging the packages that hold *extensions*."""
    from livery.workshop._declaration import Additions
    from livery.workshop._steps import StepRecord

    def _idle(ctx: object) -> None:
        del ctx

    steps = (
        (StepRecord("t", phase, _idle, extensions=extensions),) if extensions else ()
    )
    return Declaration(
        "x",
        "x",
        Path("extension.toml"),
        requires=requires,
        additions=Additions(steps),
    )


def _fake_declarations(
    monkeypatch: pytest.MonkeyPatch, declared: dict[str, Declaration]
) -> None:
    """Install *declared* beside the real extensions, which the members' sets read.

    The members' lists and needs come from the real templates, read
    before the fakes take the installed extensions' place.
    """
    from livery.workshop import _extensions, _seeds

    real = _extensions.declaration
    templates = _seeds.available_templates()
    monkeypatch.setattr(_seeds, "available_templates", lambda: templates)

    def found(name: str) -> Declaration | None:
        return declared[name] if name in declared else real(name)

    monkeypatch.setattr("livery.workshop._extensions.declaration", found)
    monkeypatch.setattr(
        "livery.workshop._extensions.installed_extensions",
        lambda: tuple(sorted(declared)),
    )


def test_an_extension_with_no_step_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_declarations(monkeypatch, {"quiet": _declared()})
    with pytest.raises(_FAILURES, match="registers no step"):
        _e2e.extension_under_test("quiet")


def test_the_list_is_the_extension_under_test_and_what_its_members_need(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skip_unless_installed("cmake", "nanobind")
    # Its set takes in what it requires, so the list names beside it only
    # what the members' templates need, as their canonical list: a
    # creation installs nothing, so the root lists them before the
    # members are created. loop-cpp's cpp-package needs cmake and conan.
    _fake_declarations(
        monkeypatch,
        {
            "top": _declared("cmake", requires=("mid", "base")),
            "mid": _declared("cmake", requires=("base",)),
            "base": _declared("cmake"),
        },
    )
    listed, members = _e2e.extension_under_test("top")
    assert listed == ("cmake", "conan", "top")
    assert members == ("loop-cpp",)
    # An extension that runs the tests installed beside it rides along no
    # more: a gate that meters no suite runs no coverage leg, so the pass
    # lands the members the steps name alone.
    _fake_declarations(
        monkeypatch,
        {
            "top": _declared("cmake", requires=("mid", "base")),
            "mid": _declared("cmake", requires=("base",)),
            "base": _declared("cmake"),
            "pytest": _declared("python", phase="test"),
        },
    )
    listed, members = _e2e.extension_under_test("top")
    assert listed == ("cmake", "conan", "top")
    assert members == ("loop-cpp",)


def test_the_steps_the_extension_under_test_declares_off_are_turned_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from livery.workshop._declaration import Additions
    from livery.workshop._steps import StepRecord

    def _idle(ctx: object) -> None:
        del ctx

    declared = Declaration(
        "x",
        "x",
        Path("extension.toml"),
        additions=Additions(
            (
                StepRecord("t", "lint", _idle, extensions=("python",)),
                StepRecord(
                    "t", "typecomplete", _idle, extensions=("python",), enabled=False
                ),
            )
        ),
    )
    _fake_declarations(monkeypatch, {"top": declared})
    assert _e2e.steps_declared_off("top") == ("typecomplete.t",)
    assert _e2e.steps_declared_off("none") == ()
    # The contract gains one table per step, at the step's address,
    # once: a table already there is kept as it is.
    text = '[workspace]\nextensions = ["top"]\n'
    enabled = _e2e._enable_steps(text, ("typecomplete.t",))  # pyright: ignore[reportPrivateUsage]
    assert enabled.endswith("[phases.typecomplete.t]\nenabled = true\n")
    assert enabled.startswith(text + "\n")
    assert _e2e._enable_steps(enabled, ("typecomplete.t",)) == enabled  # pyright: ignore[reportPrivateUsage]
    assert _e2e._enable_steps(text, ()) == text  # pyright: ignore[reportPrivateUsage]


def test_the_members_are_the_loop_s_whose_sets_hold_what_the_steps_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skip_unless_installed("cmake", "nanobind")
    _fake_declarations(
        monkeypatch,
        {"typed": _declared("python"), "both": _declared("python", "conan")},
    )
    stack, members = _e2e.extension_under_test("typed")
    assert stack == ("python", "typed")
    # loop-native's set holds loop-echo's, so a step naming python lands
    # the smaller member alone.
    assert [member.name for member in _e2e.members_for(members)] == ["loop-echo"]
    stack, members = _e2e.extension_under_test("both")
    # loop-native's set holds conan beside cpp, as a consumer of its
    # packages, and loop-echo's does not, so a step naming conan lands
    # that member too.
    assert members == ("loop-echo", "loop-native", "loop-cpp")
    # nanobind requires python and conan, so the list names it and cmake.
    assert stack == ("both", "cmake", "nanobind")
    # The test extension is listed as any other: as the extension under
    # test, and never beside another, whose set takes it in where that
    # one requires it.
    _fake_declarations(
        monkeypatch,
        {
            "pytest": _declared("python", phase="test"),
            "wrap": _declared("python", requires=("pytest",)),
            "typed": _declared("python"),
        },
    )
    assert _e2e.extension_under_test("pytest")[0] == ("pytest", "python")
    assert _e2e.extension_under_test("wrap")[0] == ("python", "wrap")
    assert _e2e.extension_under_test("typed") == (("python", "typed"), ("loop-echo",))
    assert [member.name for member in _e2e.members_for(members)] == [
        "loop-echo",
        "loop-native",
        "loop-cpp",
    ]
    assert _e2e.members_for(()) == _e2e.LOOP_MEMBERS


def test_each_loop_member_holds_the_set_its_born_contract_lists(tmp_path: Path) -> None:
    skip_unless_installed("cmake", "nanobind")
    import tomllib

    from livery.workshop._templates import render_member

    # The root lists what the members' templates need before they are
    # created, as the loop's own stack does.
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nname = "acme"\nnamespace = "acme"\n'
        'extensions = ["cmake", "nanobind"]\n'
        'authors = [{ name = "A", email = "a@example.com" }]\n'
        '\n[forge]\nkind = "github"\nowner = "acme"\n'
    )
    for member in _e2e.LOOP_MEMBERS:
        render_member(tmp_path, member.name, member.template)
        contract = tmp_path / "packages" / member.name / "workshop.toml"
        born = tomllib.loads(contract.read_text("utf-8"))["extensions"]
        assert member.listed() == tuple(born)
    # Each member's set, as a pass reads it.
    assert {member.name: member.holds() for member in _e2e.LOOP_MEMBERS} == {
        "loop-echo": ("python",),
        "loop-native": ("nanobind", "python", "cpp", "conan"),
        "loop-cpp": ("cmake", "conan", "cpp"),
    }


def test_the_birth_lists_the_extensions_of_the_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    seen: list[list[str]] = []

    def _run(argv: list[str], **_: object) -> object:
        seen.append(argv)
        return SimpleNamespace(code=0, stdout="", stderr="")

    monkeypatch.setattr("livery.footman.run", _run)
    monkeypatch.setattr(_e2e, "_loop_home", lambda kind: tmp_path)
    _e2e._birth("gitea", "http://localhost:1")
    assert not any(arg.startswith("--extensions") for arg in seen[-1])
    _e2e._birth("gitea", "http://localhost:1", stack=("base", "top"))
    assert "--extensions=base,top" in seen[-1]


def test_the_loop_s_own_fm_never_asks_a_signer() -> None:
    # The pass marks itself unsigned: the git plugin puts the setting in
    # the task's environment, which every child, the loop's own fm and
    # the driver's git, inherits.
    if not hasattr(_e2e, "e2e"):
        # The loop's task exists only beside the workshop's own tests;
        # the release train's isolated leg installs the wheel alone.
        pytest.skip("the loop's task exists only in the workshop's checkout")
    assert getattr(_e2e.e2e, "livery_git_unsigned", False) is True


def test_a_contract_s_root_set_is_read_by_name(tmp_path: Path) -> None:
    assert _e2e._listed_extensions(tmp_path) == frozenset()  # pyright: ignore[reportPrivateUsage]
    skip_unless_installed("mkdocstrings")
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nextensions = [{ name = "acme.house", without = ["docs"] },'
        ' "basedpyright", "mkdocstrings"]\n'
    )
    # The set, so the docs extension mkdocstrings requires counts though
    # the list leaves it out.
    listed = _e2e._listed_extensions(tmp_path)  # pyright: ignore[reportPrivateUsage]
    assert listed == {"acme.house", "basedpyright", "docs", "mkdocstrings"}


def test_dev_pins_refuse_a_member_without_a_wheel(tmp_path: Path) -> None:
    (tmp_path / "packages" / "workshop" / "dist").mkdir(parents=True)
    with pytest.raises(_FAILURES, match="built nothing for workshop"):
        _e2e._dev_pins(tmp_path, HEAD, MEMBERS)


def test_dev_pins_refuse_a_wheel_another_commit_built(tmp_path: Path) -> None:
    # The stale case the loop measured: a previous pass's wheel, or
    # another branch's, must never pin the loop to yesterday's bytes.
    for member in MEMBERS:
        _wheel(tmp_path, member, "0.2.0.dev96+feat.289.loop.9f8f0d0.20260907")
    with pytest.raises(
        _FAILURES, match=r"built from 9f8f0d0, not from HEAD 05482de0000"
    ):
        _e2e._dev_pins(tmp_path, HEAD, MEMBERS)


def test_dev_pins_read_a_sha_of_digits_alone_through_its_g(tmp_path: Path) -> None:
    # A build backend reads a local segment of digits alone as a
    # number and drops its leading zero; the g git describe puts first
    # keeps the sha a word, and the pin reads it back through the g.
    head = "0442877" + "a" * 33
    for member in MEMBERS:
        _wheel(tmp_path, member, "0.3.0.dev6+feat.486.store.g0442877.20260912")
    pins = _e2e._dev_pins(tmp_path, head, MEMBERS)
    assert pins["livery-workshop"] == "0.3.0.dev6+feat.486.store.g0442877.20260912"


def test_dev_pins_read_only_the_members_asked_for(tmp_path: Path) -> None:
    for member in ("workshop", "toolroom", "footman"):
        _wheel(tmp_path, member, "0.2.0.dev72+feat.314.profile.05482de.20260909")
    # forge built nothing, and is not asked for: no refusal.
    pins = _e2e._dev_pins(tmp_path, HEAD, ("workshop", "toolroom", "footman"))
    assert set(pins) == {"livery-workshop", "livery-toolroom", "livery-footman"}


@pytest.mark.parametrize("clean", [False, True], ids=["dirty", "clean"])
def test_the_dev_act_pins_a_released_member_and_drops_its_stale_wheels(
    clean: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:

    for member in MEMBERS:
        home = tmp_path / "packages" / member
        home.mkdir(parents=True)
        (home / "workshop.toml").write_text(
            f'extensions = ["python"]\nname = "livery-{member}"\n'
            + (_EDGES if member == "workshop" else "")
        )
        (home / "pyproject.toml").write_text(
            f'[project]\nname = "livery-{member}"\nversion = "0"\n'
        )
    (tmp_path / "workshop.toml").write_text("[workspace]\n")
    monkeypatch.setattr(
        "livery.workshop._extensions.workspace_root", lambda start=None: tmp_path
    )
    monkeypatch.setattr(_e2e, "_dev_forge", lambda kind: (None, "t"))
    monkeypatch.setattr(git, "head_sha", lambda root: HEAD)
    monkeypatch.setattr(git, "is_clean", lambda root: clean)
    # The loop runs from a feature branch, where the release verb is
    # the dev act.
    monkeypatch.setattr(git, "current_branch", lambda root: "chore/loop")
    monkeypatch.setattr(
        "livery.workshop._dev_release.unchanged_since_release",
        lambda root, package: "0.3.0" if package.name == "livery-forge" else "",
    )
    ran: list[list[str]] = []
    monkeypatch.setattr("livery.footman.run", lambda argv, **kwargs: ran.append(argv))
    purged: list[tuple[str, object]] = []

    def _purge(base: str, owner: str, **kwargs: object) -> list[str]:
        purged.append((owner, kwargs.get("names")))
        return ["livery-forge==0.3.0.dev4"]

    monkeypatch.setattr("livery.forge._registry.purge_packages", _purge)
    monkeypatch.setattr(
        _e2e,
        "_dev_pins",
        lambda root, head, members: {f"livery-{m}": "dev" for m in members},
    )
    monkeypatch.setenv("GITEA_URL", "http://localhost:3000")
    pins = _e2e._publish_dev_wheels("gitea")
    # forge is pinned to its release, the others rebuilt, and the
    # forge rehearsal wheels go so the release resolves past them.
    assert ran == [
        ["fm", "--yes", "workflow.release", "workshop", "toolroom", "footman"]
    ]
    # A dirty tree's dev version never changes, so its earlier wheels
    # go before the act, and the rebuilt members are the ones dropped.
    dirty = [("livery", {"livery-workshop", "livery-toolroom", "livery-footman"})]
    assert purged == [("livery", {"livery-forge": "0.3.0"}), *([] if clean else dirty)]
    assert pins == {
        "livery-workshop": "dev",
        "livery-toolroom": "dev",
        "livery-footman": "dev",
        "livery-forge": "0.3.0",
    }
    out = capsys.readouterr().out
    assert "forge: nothing unreleased since 0.3.0; the loop pins the release" in out
    assert "1 stale rehearsal release(s)" in out
    assert ("dev wheel(s) of the dirty tree dropped" in out) is not clean


def test_the_dev_index_lays_out_each_project_with_its_wheels(tmp_path: Path) -> None:
    built = tmp_path / "dist"
    built.mkdir()
    wheels = [
        built / "livery_workshop-0.6.0.dev1+feat.x.gabc1234.20261005-py3-none-any.whl",
        built
        / "livery_extensions_ruff-0.1.0.dev1+feat.x.gabc1234.20261005-py3-none-any.whl",
    ]
    for wheel in wheels:
        wheel.write_bytes(b"wheel")
    folder = tmp_path / "index"
    (folder / "stale").mkdir(parents=True)
    assert _e2e.write_dev_index(folder, wheels) == folder
    # An earlier pass's layout is gone, and each project is its
    # normalised name with its wheel and a page linking it.
    assert sorted(path.name for path in folder.iterdir()) == [
        "index.html",
        "livery-extensions-ruff",
        "livery-workshop",
    ]
    page = (folder / "livery-workshop" / "index.html").read_text()
    assert f'<a href="{wheels[0].name}">' in page
    assert (folder / "livery-workshop" / wheels[0].name).read_bytes() == b"wheel"
    root_page = (folder / "index.html").read_text()
    assert '<a href="livery-extensions-ruff/">' in root_page
    assert '<a href="livery-workshop/">' in root_page


def test_the_birth_reads_the_dev_index_before_any_other(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    seen: list[dict[str, str]] = []

    def _run(argv: list[str], *, env: dict[str, str], **kwargs: object) -> object:
        seen.append(dict(env))
        return SimpleNamespace(code=0, stdout="", stderr="")

    monkeypatch.setattr("livery.footman.run", _run)
    monkeypatch.setattr(_e2e, "_loop_home", lambda kind: tmp_path)
    monkeypatch.setenv("UV_INDEX", "https://mirror.example/simple")
    _e2e._birth("gitea", "http://localhost:1")
    assert seen[-1]["UV_INDEX"] == "https://mirror.example/simple"
    _e2e._birth("gitea", "http://localhost:1", index="file:///dev-index")
    assert seen[-1]["UV_INDEX"] == "file:///dev-index https://mirror.example/simple"


def test_a_child_the_pass_starts_by_hand_takes_the_task_s_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The git plugin writes the tools' identity into the task's
    # environment alone; a hosted runner has no identity of its own, so
    # a child spawned from the process environment commits nothing.
    import os
    from types import SimpleNamespace

    from livery.footman._context import Context, use_context

    seen: list[dict[str, str]] = []

    def _run(argv: list[str], *, env: dict[str, str], **kwargs: object) -> object:
        seen.append(dict(env))
        return SimpleNamespace(code=0, stdout="", stderr="")

    monkeypatch.setattr("livery.footman.run", _run)
    monkeypatch.setattr(_e2e, "_loop_home", lambda kind: tmp_path)
    monkeypatch.delenv("GIT_AUTHOR_NAME", raising=False)
    # The task started with a stale token; the pass wrote its forge's
    # own into the process environment after, which is the one a child
    # must carry: a stale one is HTTP 401 at the birth.
    task = Context(
        env={**os.environ, "GIT_AUTHOR_NAME": "the tools", "GITEA_TOKEN": "stale"}
    )
    monkeypatch.setenv("GITEA_TOKEN", "fresh")
    with use_context(task):
        _e2e._birth("gitea", "http://localhost:1")
    assert seen[-1]["GIT_AUTHOR_NAME"] == "the tools"
    assert seen[-1]["GITEA_TOKEN"] == "fresh"


def test_the_checkout_index_refuses_a_member_without_a_version(tmp_path: Path) -> None:
    from livery.workshop._packages import Package

    skip_unless_installed("python")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "livery-x"\n')
    member = Package(
        directory=tmp_path,
        path="packages/x",
        name="livery-x",
        depends=(),
        extensions=("python",),
    )
    with pytest.raises(_FAILURES, match="no version line"):
        _e2e._stamp_content(member, tmp_path)  # pyright: ignore[reportPrivateUsage]


def test_the_checkout_index_builds_what_a_newborn_installs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import livery.toolroom.tools as toolroom

    skip_unless_installed("python")
    _member(tmp_path, "workshop", "toolroom")
    _member(tmp_path, "toolroom")
    _member(tmp_path, "unlisted")
    built: list[tuple[str, str]] = []

    class _Uv:
        def opts(self, **_: object) -> _Uv:
            return self

        def __call__(self, *args: str) -> None:
            out, copy = Path(args[3]), Path(args[4])
            version = re.search(
                r'version = "([^"]+)"', (copy / "pyproject.toml").read_text()
            )
            assert version is not None
            built.append((copy.name, version.group(1)))
            out.mkdir(parents=True, exist_ok=True)
            dist = f"livery_{copy.name.replace('-', '_')}"
            (out / f"{dist}-{version.group(1)}-py3-none-any.whl").write_bytes(b"wheel")

    monkeypatch.setattr(toolroom, "uv", _Uv())
    folder = tmp_path / "index"
    assert _e2e.checkout_index(tmp_path, folder) == folder.as_uri()
    # The workshop and its closure, each a copy stamped with its content,
    # and the checkout's own files untouched.
    assert [name for name, _version in built] == ["toolroom", "workshop"]
    assert all(re.fullmatch(r"0\.1\.0\+checkout\.[0-9a-f]{12}", v) for _n, v in built)
    assert (
        'version = "0.1.0"\n'
        in (tmp_path / "packages/workshop/pyproject.toml").read_text()
    )
    assert sorted(entry.name for entry in folder.iterdir()) == [
        "index.html",
        "livery-toolroom",
        "livery-workshop",
    ]
    # An unchanged tree keeps its name: the same build, the same wheel.
    first = dict(built)
    built.clear()
    _e2e.checkout_index(tmp_path, folder)
    assert dict(built) == first


def test_a_tree_rebuilt_at_one_version_installs_its_own_code(tmp_path: Path) -> None:
    # uv caches an installed wheel by its file name and the place it
    # came from, and installs the cached copy again without reading a
    # newer file of that name. Two trees built at one version and
    # served from one folder: the second install carries the second
    # tree's code, because each build names its content.
    import sys

    import livery.toolroom.tools as toolroom
    from livery.workshop._packages import discover_packages

    skip_unless_installed("python")
    _member(tmp_path, "probe")
    member = tmp_path / "packages" / "probe"
    with (member / "pyproject.toml").open("a") as handle:
        handle.write(
            'requires-python = ">=3.11"\n'
            "[build-system]\n"
            'requires = ["uv_build>=0.7"]\n'
            'build-backend = "uv_build"\n'
        )
    module = member / "src" / "livery_probe" / "__init__.py"
    module.parent.mkdir(parents=True)
    (package,) = discover_packages(tmp_path)
    served = tmp_path / "served"
    install = toolroom.uv.opts(
        env={**os.environ, "UV_CACHE_DIR": str(tmp_path / "uv-cache")},
        recorded=False,
    )
    names: list[str] = []
    for answer in ("first", "second"):
        module.write_text(f'ANSWER = "{answer}"\n')
        shutil.rmtree(served, ignore_errors=True)
        names.append(_e2e.build_content_named(package, tmp_path, served).name)
        site = tmp_path / f"site-{answer}"
        install(
            "pip",
            "install",
            "--python",
            sys.executable,
            "--target",
            str(site),
            "--no-index",
            "--find-links",
            str(served),
            "livery-probe",
        )
        installed = (site / "livery_probe" / "__init__.py").read_text()
        assert installed == f'ANSWER = "{answer}"\n'
    assert names[0] != names[1]
    named = r"livery_probe-0\.1\.0\+checkout\.[0-9a-f]{12}-py3-none-any\.whl"
    assert all(re.fullmatch(named, name) for name in names)
    # The member's own files keep their version.
    assert 'version = "0.1.0"\n' in (member / "pyproject.toml").read_text()


def test_dev_pins_read_this_commits_newest_wheel(tmp_path: Path) -> None:
    for member in MEMBERS:
        _wheel(tmp_path, member, "0.2.0.dev96+feat.289.loop.9f8f0d0.20260907", age=60)
        _wheel(tmp_path, member, "0.2.0.dev72+feat.314.profile.05482de.20260909")
    _wheel(tmp_path, "footman", "0.53.0.dev14+feat.314.profile.05482de.20260909.dirty")
    pins = _e2e._dev_pins(tmp_path, HEAD, MEMBERS)
    assert pins == {
        "livery-workshop": "0.2.0.dev72+feat.314.profile.05482de.20260909",
        "livery-forge": "0.2.0.dev72+feat.314.profile.05482de.20260909",
        "livery-toolroom": "0.2.0.dev72+feat.314.profile.05482de.20260909",
        # The newest wheel wins, dirty or not: it is what the act built.
        "livery-footman": "0.53.0.dev14+feat.314.profile.05482de.20260909.dirty",
    }


# --- the proofs read the runs' logs: the refusals first ------------------------

_SHA = "c" * 40


def _proof_rig() -> tuple[FakeForge, Repository]:
    fake = FakeForge()
    repo = fake.create_repo(_e2e.E2E_OWNER, _e2e.E2E_REPO, private=False)
    return fake, repo


def test_a_red_run_is_re_run_once_and_red_twice_is_the_verdict(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake, repo = _proof_rig()
    monkeypatch.setattr(_e2e, "_dev_forge", lambda kind: (fake, "t"))
    fake.push(_e2e.E2E_OWNER, _e2e.E2E_REPO, "main", outcome="failure", sha=_SHA)
    fake.settle(_e2e.E2E_OWNER, _e2e.E2E_REPO, _SHA)
    # The retry re-queues the failed run and names it; a green run is
    # left alone.
    runs = repo.checks.runs(head_sha=_SHA)
    assert _e2e._retry_red_once(repo, runs) == ["ci.yml"]
    assert repo.checks.runs(head_sha=_SHA)[0].status == "queued"
    assert "re-running ci.yml" in capsys.readouterr().out
    assert _e2e._retry_red_once(repo, repo.checks.runs(head_sha=_SHA)) == []
    # Watched: the second attempt passes, and the watch ends green.
    real = _e2e._retry_red_once

    def _flaky(repo_: object, runs_: tuple[object, ...]) -> list[str]:
        retried = real(repo, repo.checks.runs(head_sha=_SHA))
        fake.set_outcome(_e2e.E2E_OWNER, _e2e.E2E_REPO, _SHA, "success")
        fake.settle(_e2e.E2E_OWNER, _e2e.E2E_REPO, _SHA)
        return retried

    fake.settle(_e2e.E2E_OWNER, _e2e.E2E_REPO, _SHA)  # the queued attempt, red
    monkeypatch.setattr(_e2e, "_retry_red_once", _flaky)
    _e2e._watch("gitea", "url", _SHA, timeout=5, interval=0)
    assert "ci.yml         success" in capsys.readouterr().out
    # Red twice is the verdict: the second attempt fails too.
    fake.push(_e2e.E2E_OWNER, _e2e.E2E_REPO, "main", outcome="failure", sha="e" * 40)
    fake.settle(_e2e.E2E_OWNER, _e2e.E2E_REPO, "e" * 40)

    def _still_red(repo_: object, runs_: tuple[object, ...]) -> list[str]:
        retried = real(repo, repo.checks.runs(head_sha="e" * 40))
        fake.settle(_e2e.E2E_OWNER, _e2e.E2E_REPO, "e" * 40)
        return retried

    monkeypatch.setattr(_e2e, "_retry_red_once", _still_red)
    with pytest.raises(_FAILURES, match=r"red runs on the loop: ci\.yml"):
        _e2e._watch("gitea", "url", "e" * 40, timeout=5, interval=0)


def test_the_birth_watch_judges_the_runs_it_caused_and_names_a_foreign_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Refusal first: a dispatched red run the watch requires is its own,
    # and red is the verdict. A dispatched red run it did not ask for,
    # at the same commit (a wave the merge point re-dispatched), is
    # named once and left to its scenario; the push's green run judges.
    fake, repo = _proof_rig()
    monkeypatch.setattr(_e2e, "_dev_forge", lambda kind: (fake, "t"))
    monkeypatch.setattr(_e2e, "_retry_red_once", lambda repo_, runs_: [])
    sha = "d" * 40
    fake.push(_e2e.E2E_OWNER, _e2e.E2E_REPO, "main", outcome="success", sha=sha)
    fake.settle(_e2e.E2E_OWNER, _e2e.E2E_REPO, sha)
    # The wave, dispatched at the same commit after the push's run
    # settled green, is the one run the outcome below reaches.
    repo.checks.dispatch("release.yml", ref="main")
    fake.set_outcome(_e2e.E2E_OWNER, _e2e.E2E_REPO, sha, "failure")
    fake.settle(_e2e.E2E_OWNER, _e2e.E2E_REPO, sha)
    with pytest.raises(_FAILURES, match=r"red runs on the loop: release\.yml"):
        _e2e._watch(
            "gitea", "url", sha, timeout=5, interval=0, require=("release.yml",)
        )
    capsys.readouterr()
    _e2e._watch("gitea", "url", sha, timeout=5, interval=0)
    out = capsys.readouterr().out
    assert out.count("is not this pass's: dispatched at") == 1
    assert "ci.yml         success" in out
    assert "release.yml    failure" not in out


class _SlowDeleteForge(FakeForge):
    """A forge whose delete outruns the client; *finishes*: the server completes it."""

    finishes = True

    def delete_repo(self, owner: str, name: str) -> None:
        from livery.forge import ForgeError

        if self.finishes:
            super().delete_repo(owner, name)
        raise ForgeError("server unreachable on DELETE /repos/x: timed out")


def test_a_delete_that_outruns_the_client_is_waited_for_or_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.forge import ForgeError

    monkeypatch.setattr(
        "livery.forge._registry.purge_packages",
        lambda base, owner, *, token, kind="pypi", api=None: [],
    )
    root = tmp_path / "never-born"
    # The server never finishes: the reset refuses, naming the wait.
    stuck = _SlowDeleteForge()
    stuck.finishes = False
    stuck.create_repo(_e2e.E2E_OWNER, _e2e.E2E_REPO)
    with pytest.raises(
        ForgeError, match="still on the dev forge after the delete's wait"
    ):
        _e2e.start_over(stuck, "t", root, url="http://gitea", wait=0)
    # The server finishes after the client gave up: the reset goes on.
    slow = _SlowDeleteForge()
    slow.create_repo(_e2e.E2E_OWNER, _e2e.E2E_REPO)
    lines = _e2e.start_over(slow, "t", root, url="http://gitea", wait=0)
    assert lines[0].endswith(
        "(the delete outran the client's wait and finished on the server)"
    )
    assert slow.get_repo(_e2e.E2E_OWNER, _e2e.E2E_REPO) is None


def test_starting_over_refuses_unpushed_commits_then_deletes_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    fake, repo = _proof_rig()
    root = tmp_path / _e2e.E2E_REPO
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "--initial-branch=main", str(origin)],
        check=True,
    )
    subprocess.run(["git", "clone", "-q", str(origin), str(root)], check=True)
    (root / "a.txt").write_text("a\n")
    subprocess.run(["git", "-C", str(root), "add", "a.txt"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "feat: a"], check=True)
    purged: list[tuple[str, str]] = []

    def _purge(
        base: str, owner: str, *, token: str, kind: str = "pypi", api: object = None
    ) -> list[str]:
        purged.append((base + "/" + owner, token))
        return ["loop-echo==0.1.0"]

    monkeypatch.setattr("livery.forge._registry.purge_packages", _purge)
    # Refusal first: a commit origin has not seen would be lost.
    with pytest.raises(_FAILURES, match="holds commits its origin has not seen"):
        _e2e.start_over(fake, "t", root, url="http://gitea")
    assert repo.pr is not None and purged == []
    subprocess.run(
        ["git", "-C", str(root), "push", "-q", "-u", "origin", "main"], check=True
    )
    lines = _e2e.start_over(fake, "t", root, url="http://gitea")
    assert fake.get_repo(_e2e.E2E_OWNER, _e2e.E2E_REPO) is None
    assert purged == [("http://gitea/" + _e2e.E2E_OWNER, "t")]
    assert not root.exists()
    assert lines == [
        f"  deleted {_e2e.E2E_OWNER}/{_e2e.E2E_REPO} on the dev forge",
        "  purged 1 release(s) from the registry: loop-echo==0.1.0",
        f"  removed {root}",
    ]
    # A second reset finds nothing and says the same: the recovery procedure.
    lines = _e2e.start_over(fake, "t", root, url="http://gitea")
    assert lines[0].startswith("  deleted") and len(lines) == 2


def test_a_red_run_is_re_run_once_and_then_fails_the_proof_naming_its_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake, repo = _proof_rig()
    fake.push(_e2e.E2E_OWNER, _e2e.E2E_REPO, "main", outcome="failure", sha=_SHA)
    fake.settle(_e2e.E2E_OWNER, _e2e.E2E_REPO, _SHA)
    real = _e2e._retry_red_once
    retries: list[int] = []

    def _still_red(repo_: object, runs_: tuple[object, ...]) -> list[str]:
        retried = real(repo, repo.checks.runs(head_sha=_SHA))
        retries.append(len(retried))
        fake.settle(_e2e.E2E_OWNER, _e2e.E2E_REPO, _SHA)  # the attempt, red again
        return retried

    monkeypatch.setattr(_e2e, "_retry_red_once", _still_red)
    with pytest.raises(_FAILURES) as caught:
        _e2e._completed_run(repo, _SHA, event="push", interval=0)
    assert retries == [1]
    assert "ended failure" in str(caught.value)
    assert "/actions/runs/" in str(caught.value)
    # A second attempt that passes is the proof's run.
    fake.push(_e2e.E2E_OWNER, _e2e.E2E_REPO, "main", outcome="failure", sha="e" * 40)
    fake.settle(_e2e.E2E_OWNER, _e2e.E2E_REPO, "e" * 40)

    def _then_green(repo_: object, runs_: tuple[object, ...]) -> list[str]:
        retried = real(repo, repo.checks.runs(head_sha="e" * 40))
        fake.set_outcome(_e2e.E2E_OWNER, _e2e.E2E_REPO, "e" * 40, "success")
        fake.settle(_e2e.E2E_OWNER, _e2e.E2E_REPO, "e" * 40)
        return retried

    monkeypatch.setattr(_e2e, "_retry_red_once", _then_green)
    run, _jobs = _e2e._completed_run(repo, "e" * 40, event="push", interval=0)
    assert run.conclusion == "success"


def test_a_proof_waits_only_until_its_deadline_and_reads_its_event_alone() -> None:
    fake, repo = _proof_rig()
    fake.push(_e2e.E2E_OWNER, _e2e.E2E_REPO, "main", sha=_SHA)
    fake.settle(_e2e.E2E_OWNER, _e2e.E2E_REPO, _SHA)
    with pytest.raises(_FAILURES, match="no completed push run for dddddddddd"):
        _e2e._completed_run(repo, "d" * 40, event="push", timeout=0)
    with pytest.raises(_FAILURES, match="no completed pull_request run"):
        _e2e._completed_run(repo, _SHA, event="pull_request", timeout=0)


def test_the_one_log_read_names_the_job_it_cannot_find_and_the_line_it_misses() -> None:
    # The publish job's prebuilt line is the one fact a proof still reads
    # from a log, until conan's per-host artifact gives it a row.
    fake, repo = _proof_rig()
    fake.push(_e2e.E2E_OWNER, _e2e.E2E_REPO, "main", sha=_SHA)
    fake.settle(_e2e.E2E_OWNER, _e2e.E2E_REPO, _SHA)
    run, logs = _e2e._completed_run(repo, _SHA, event="push")
    assert [job.name for job in logs] == ["gate"]
    with pytest.raises(_FAILURES, match="has no check job"):
        _e2e._log_says(repo, run, logs, "check", "x")
    with pytest.raises(_FAILURES, match="did not say 'absent line'"):
        _e2e._log_says(repo, run, logs, "gate", "absent line")
    _e2e._log_says(repo, run, logs, "gate", "concluded success")


# The release act's proof that the wave published the wheels the wheels
# job built, read from the publish job's log: the refusals first.

_NATIVE = "acme-native"


def _prove_the_wave(publish: Conclusion, log: str) -> None:
    """Run the proof on a wave whose publish job ended *publish* and logged *log*.

    The stand-in forge serves the run's two jobs, a green wheels leg
    and the publish job.
    """
    from types import SimpleNamespace

    from livery.forge import Job, Run

    run = Run(
        18, "release.yml", "5730fa48b2", "workflow_dispatch", "completed", "success"
    )
    jobs = (
        Job(7, "wheels (ubuntu-latest)", "completed", "success"),
        Job(8, "publish", "completed", publish),
    )
    logs = {7: "", 8: log}
    repo = SimpleNamespace(
        checks=SimpleNamespace(
            jobs=lambda run_id: jobs, job_log=lambda job_id: logs[job_id]
        ),
        web_url=lambda: "http://forge/livery/loop",
    )
    _e2e._require_prebuilt(repo, run, _NATIVE)  # pyright: ignore[reportArgumentType]


def test_a_wave_whose_publish_job_never_names_the_member_fails_naming_the_run() -> None:
    # The wave built the member's wheels itself: the line never came.
    with pytest.raises(_FAILURES, match=r"run 18's publish job did not say") as caught:
        _prove_the_wave("success", "  wave: 1 member(s) published\n")
    assert "http://forge/livery/loop/actions/runs/18" in str(caught.value)
    # The wheels job fed the wave another member alone.
    with pytest.raises(_FAILURES, match=r"run 18's publish job did not say"):
        _prove_the_wave(
            "success",
            "  prebuilt: acme-other: the run collected what the wheels job built\n",
        )


def test_a_wave_whose_publish_job_did_not_end_green_fails_naming_how_it_ended() -> None:
    line = f"  prebuilt: {_NATIVE}: the run collected what the wheels job built\n"
    # The line came and the job went red after it: the line proves nothing.
    with pytest.raises(_FAILURES, match=r"run 18's publish job ended failure"):
        _prove_the_wave("failure", line)
    # A red wheels leg leaves the publish job skipped, with no log.
    with pytest.raises(_FAILURES, match=r"run 18's publish job ended skipped"):
        _prove_the_wave("skipped", "")


def test_a_wave_that_published_the_wheels_jobs_wheels_passes_and_says_so(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A forge's log opens each line with its time.
    _prove_the_wave(
        "success",
        "2026-10-12T10:00:00.0000000Z   release/publish: workflow.release.publish"
        " (builtin)\n"
        f"2026-10-12T10:00:01.0000000Z   prebuilt: {_NATIVE}: the run collected"
        " what the wheels job built\n"
        "2026-10-12T10:00:09.0000000Z   wave: 1 member(s) published\n",
    )
    assert (
        "release: the wave published the wheels the wheels job built for"
        f" {_NATIVE} (run 18)" in capsys.readouterr().out
    )


# --- the scenarios ---------------------------------------------------------------


def test_an_unknown_scenario_refuses_naming_the_sets_and_the_scenarios() -> None:
    from livery.footman import Failed

    with pytest.raises(
        Failed, match=r"'nonesuch' is not a scenario or a set; the sets are develop"
    ):
        _e2e.scenarios_for("develop,nonesuch")
    with pytest.raises(Failed, match=r"--scenario names nothing"):
        _e2e.scenarios_for(" , ")


def test_a_choice_resolves_its_needs_once_in_the_registry_s_order() -> None:
    names = [scenario.name for scenario in _e2e.scenarios_for("develop")]
    assert names == [
        "birth",
        "verified-skip",
        "members",
        "ratchet",
        "scoped-leg",
        "unproven-push",
    ]
    # A scenario alone brings what it builds on, whether or not it was named.
    assert [s.name for s in _e2e.scenarios_for("scoped-leg")] == [
        "birth",
        "members",
        "ratchet",
        "scoped-leg",
    ]
    assert [s.name for s in _e2e.scenarios_for("nightly")] == [
        "birth",
        "members",
        "release",
        "nightly",
    ]
    # `all` is the registry, and a set beside a name adds nothing twice.
    everything = [s.name for s in _e2e.SCENARIOS]
    assert [s.name for s in _e2e.scenarios_for("all")] == everything
    assert [s.name for s in _e2e.scenarios_for("points, release")] == [
        name for name in everything if name not in ("prose-leg", "tests-leg")
    ]
    # Only the release needs the runner's docker socket.
    assert not _e2e.daemon_needed(_e2e.scenarios_for("develop"))
    assert _e2e.daemon_needed(_e2e.scenarios_for("release"))


def test_a_scenario_is_timed_with_its_runs_and_a_failure_is_marked() -> None:
    from livery.footman import Failed

    pass_ = _e2e.Pass("gitea", "http://gitea:3000")

    def proves(p: _e2e.Pass) -> None:
        _e2e.RUNS.count += 2

    def refuses(p: _e2e.Pass) -> None:
        raise Failed("the runner said no")

    _e2e.RUNS.count = 0
    _e2e.run_scenario(pass_, _e2e.Scenario("quick", (), proves))
    with pytest.raises(Failed, match="the runner said no"):
        _e2e.run_scenario(pass_, _e2e.Scenario("slow", (), refuses))
    assert [(t.name, t.runs, t.failed) for t in pass_.timings] == [
        ("quick", 2, False),
        ("slow", 0, True),
    ]
    lines = _e2e.timing_table(
        [_e2e.Timing("quick", 61.24, 2), _e2e.Timing("slow", 3.0, 0, failed=True)]
    )
    assert lines == [
        "  quick     61.2s  2 run(s)",
        "  slow       3.0s  0 run(s)  failed",
        "  total     64.2s  2 run(s)",
    ]
    assert _e2e.timing_table([]) == ["  timing: nothing ran"]


def test_the_loop_series_is_declared_with_the_others() -> None:
    from livery.workshop._series import BASE

    assert _e2e.LOOP_SERIES in BASE and _e2e.LOOP_SERIES.local
    assert not _e2e.LOOP_SERIES.ci_only


def test_a_pass_records_its_rows_on_the_local_loop_series(seeds: Seeds) -> None:
    work = seeds("pushed", pushed) / "work"
    pass_ = _e2e.Pass("gitea", "http://gitea:3000")
    pass_.timings.append(_e2e.Timing("birth", 12.34, 3))
    line = _e2e.record_pass(work, pass_, "develop")
    assert line.startswith("  loop series: pass ") and line.endswith("(1 scenario(s))")
    found = _e2e.LOOP_SERIES.rows(state_store(work))
    assert not found.failed and len(found.rows) == 1
    (row,) = found.rows
    assert row.name.startswith("pass-") and row.data["asked"] == "develop"
    assert row.data["scenarios"] == [
        {"name": "birth", "seconds": 12.3, "runs": 3, "failed": False}
    ]


# --- the environment ------------------------------------------------------------


@pytest.fixture
def host_environment(monkeypatch: pytest.MonkeyPatch) -> _e2e.Current:
    """A pass on a host-mode environment with its own runner label."""
    current = _e2e.Current(
        "scratch", "host", "http://localhost:43210", "token-abc", "scratch-macos-arm-01"
    )
    monkeypatch.setattr(_e2e, "CURRENT", current)
    return current


def test_a_host_environment_is_the_lane_s_alias_the_forge_and_the_workspace_s_home(
    host_environment: _e2e.Current, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("livery.footman._context.data_dir", lambda: tmp_path)
    lane = _e2e._lane("gitea")  # pyright: ignore[reportPrivateUsage]
    assert lane.alias == "http://localhost:43210"
    home = _e2e._loop_home("gitea")  # pyright: ignore[reportPrivateUsage]
    assert home == tmp_path / "workshop-e2e" / "scratch" / "gitea"
    # The alias requirement is the docker rig's; a localhost URL needs none.
    _e2e._require_host_alias("gitea")  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(_e2e, "CURRENT", _e2e.Current())
    lane = _e2e._lane("gitea")  # pyright: ignore[reportPrivateUsage]
    assert lane.alias == "http://gitea:3000"
    home = _e2e._loop_home("gitea")  # pyright: ignore[reportPrivateUsage]
    assert home == tmp_path / "workshop-e2e" / "gitea"


def test_a_host_pass_hands_its_children_the_environments_own_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The cascade read the shared file when the pass started, before the
    # bring-up seeded a new token; a child copying the pass's environment
    # would reach the forge with the stale one and get a 401.
    import os

    monkeypatch.setattr(_e2e, "CURRENT", _e2e.Current())
    monkeypatch.setenv("GITEA_URL", "http://localhost:1")
    monkeypatch.setenv("GITEA_TOKEN", "stale")
    # A hosted job carries the real forge's tokens in the generic names
    # the workshop reads first; inside the pass the forge is the local one.
    monkeypatch.setenv("FORGE_TOKEN", "github-job-token")
    monkeypatch.setenv("FORGE_ADMIN_TOKEN", "github-admin-token")
    values = {
        "GITEA_URL": "http://localhost:43210",
        "GITEA_TOKEN": "token-abc",
        "LABELS": "scratch-macos-arm-01,macos",
    }
    _e2e._enter_host(values, "scratch")  # pyright: ignore[reportPrivateUsage]
    assert (os.environ["GITEA_URL"], os.environ["GITEA_TOKEN"]) == (
        "http://localhost:43210",
        "token-abc",
    )
    assert (os.environ["FORGE_TOKEN"], os.environ["FORGE_ADMIN_TOKEN"]) == (
        "token-abc",
        "token-abc",
    )
    assert (
        _e2e.Current(
            "scratch",
            "host",
            "http://localhost:43210",
            "token-abc",
            "scratch-macos-arm-01",
        )
        == _e2e.CURRENT
    )


def test_the_forge_credentials_come_from_the_environment_in_host_mode(
    host_environment: _e2e.Current, monkeypatch: pytest.MonkeyPatch
) -> None:
    connected: list[tuple[str, str, str]] = []

    def connect(kind: str, url: str, token: str) -> object:
        connected.append((kind, url, token))
        return object()

    monkeypatch.setattr("livery.workshop._forge_lane._connect", connect)
    monkeypatch.delenv("GITEA_URL", raising=False)
    monkeypatch.delenv("GITEA_TOKEN", raising=False)
    _forge, token = _e2e._dev_forge("gitea")  # pyright: ignore[reportPrivateUsage]
    assert token == "token-abc"
    assert connected == [("gitea", "http://localhost:43210", "token-abc")]


# --- a pass after another: every client names this pass's forge ---------------


def _registry(port: int) -> str:
    """The forge's own conan registry, for a forge on *port*."""
    return f"http://localhost:{port}/api/packages/{_e2e.E2E_OWNER}/conan"


def _point_the_remote(url: str, cwd: Path) -> None:
    """Point conan's ``workshop`` remote at *url*, as a release does first.

    The release's publish and its serving probe both run this command
    before they upload or list. It runs the store's conan, and goes to
    the conan home the process environment names.
    """
    from livery.toolroom.tools import conan

    added = conan.opts(cwd=cwd, env=dict(os.environ), nofail=True, recorded=False)(
        "remote", "add", "workshop", url, "--force"
    )
    assert added.code == 0, added.stderr


def _remotes(home: Path, cwd: Path) -> dict[str, str]:
    """The remotes conan lists in *home*, name to URL, read from its JSON."""
    import json

    from livery.toolroom.tools import conan

    listed = conan.opts(
        cwd=cwd,
        env={**os.environ, "CONAN_HOME": str(home)},
        nofail=True,
        recorded=False,
    )("remote", "list", "--format=json")
    assert listed.code == 0, listed.stderr
    entries = json.loads(listed.stdout)
    return {str(entry["name"]): str(entry["url"]) for entry in entries}


@pytest.fixture
def pass_environment(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """What entering an environment writes, put back after the test; the task's env.

    Each variable is set first, so the test's end restores it whether
    or not the process had it.
    """
    from types import SimpleNamespace

    names = (
        "GITEA_URL",
        "GITEA_TOKEN",
        "FORGE_TOKEN",
        "FORGE_ADMIN_TOKEN",
        "CONAN_HOME",
    )
    for name in names:
        monkeypatch.setenv(name, "from-the-job")
    monkeypatch.setattr(_e2e, "CURRENT", _e2e.Current())
    task_env: dict[str, str] = {}
    monkeypatch.setattr("livery.footman.current", lambda: SimpleNamespace(env=task_env))
    return task_env


@modes_bind
def test_a_fresh_pass_stops_at_a_home_it_may_not_empty_and_changes_nothing(
    rig: Path,
    machine: Machine,
    pass_environment: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The first pass's release built its native wheel in a container that
    # runs as root and mounts the pass's conan home: fmt's sources stayed
    # there owned by root, and the fresh pass after it could not remove
    # them, so it built on a half-removed home.
    ports = iter((47615, 57713))
    monkeypatch.setattr(_devenv, "free_port", lambda: next(ports))
    first = _e2e.enter_environment("e2e", ())
    held = (
        first.directory / "conan" / "p" / "fmt80a1ffe0392cb" / "s" / "src" / "support"
    )
    held.mkdir(parents=True)
    (held / "build.gradle").write_text("written by root\n")
    held.chmod(0o555)
    try:
        with pytest.raises(Failed) as refused:
            _e2e.enter_environment("e2e", (), fresh=True)
        said = str(refused.value)
        assert f"e2e: {held} belongs to {getpass.getuser()}" in said
        assert "nothing was stopped or removed" in said
        assert f"sudo chown -R {getpass.getuser()} {first.directory}" in said
        # The environment is as it was: its forge runs on its own port.
        assert machine.stopped == []
        assert _devenv.environment("e2e").url == "http://localhost:47615"
    finally:
        held.chmod(0o755)
    # Handed back, the fresh pass takes the whole environment and a new forge.
    second = _e2e.enter_environment("e2e", (), fresh=True)
    assert second.url == "http://localhost:57713"
    assert not held.exists()


def test_a_fresh_pass_finishes_a_removal_an_earlier_one_could_not(
    rig: Path,
    machine: Machine,
    pass_environment: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A delete refused after the check, a file held open or written while
    # the removal ran: the pass stops, and what stayed holds no environment.
    ports = iter((47615, 57713))
    monkeypatch.setattr(_devenv, "free_port", lambda: next(ports))
    first = _e2e.enter_environment("e2e", ())
    held = first.directory / "conan" / "p" / "fmt80a1ffe0392cb" / "s" / "build.gradle"
    held.parent.mkdir(parents=True)
    held.write_text("held\n")

    def refusing(path: str) -> None:
        if Path(path) == held:
            raise PermissionError(1, "Operation not permitted", path)
        os.unlink(path)

    monkeypatch.setattr(_devenv, "UNLINK", refusing)
    with pytest.raises(Failed, match=r"is not removed whole"):
        _e2e.enter_environment("e2e", (), fresh=True)
    assert held.is_file() and not first.env_file.exists()
    # A pass that is not fresh refuses to bring an environment up there.
    with pytest.raises(Failed, match=r"holds what a refused removal left"):
        _e2e.enter_environment("e2e", ())
    # A fresh pass removes what stayed, though no environment is named there.
    monkeypatch.setattr(_devenv, "UNLINK", os.unlink)
    second = _e2e.enter_environment("e2e", (), fresh=True)
    assert second.url == "http://localhost:57713"
    assert not held.exists()


def test_a_pass_after_another_never_reaches_the_earlier_forge_through_conan(
    rig: Path,
    machine: Machine,
    pass_environment: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    skip_unless_installed("python", "conan")
    # Two passes in one hosted job, the second fresh: the first's forge
    # listened on 47615 and its release pointed conan's remote there; the
    # second's forge listens on 57713, and its members' build asked
    # conan's remotes for a binary and could not reach 47615.
    ports = iter((47615, 57713))
    monkeypatch.setattr(_devenv, "free_port", lambda: next(ports))
    # The home a hosted job's entry places under the runner's temp: every
    # pass of the job inherits it.
    job_home = tmp_path / "job-conan"
    monkeypatch.setenv("CONAN_HOME", str(job_home))
    first = _e2e.enter_environment("e2e", ("conan",))
    assert first.url == "http://localhost:47615"
    # The release points the remote at the pass's forge from the host, in
    # the home the pass runs conan with. A runner's job points it in its
    # own home, under the job's temp, which goes with the job: the
    # runner's configuration hands its jobs no home.
    _point_the_remote(_registry(47615), tmp_path)
    assert "CONAN_HOME" not in (first.runner_dir(1) / "config.yaml").read_text()
    first_home = Path(os.environ["CONAN_HOME"])
    assert _remotes(first_home, tmp_path)["workshop"] == _registry(47615)
    second = _e2e.enter_environment("e2e", ("conan",), fresh=True)
    assert second.url == "http://localhost:57713"
    # Before the second pass's release runs, the home the pass builds
    # with names no earlier forge.
    pass_home = Path(os.environ["CONAN_HOME"])
    assert not [url for url in _remotes(pass_home, tmp_path).values() if "47615" in url]
    _point_the_remote(_registry(57713), tmp_path)
    assert _remotes(pass_home, tmp_path)["workshop"] == _registry(57713)
    # Both homes are the environment's own, placed there by conan's
    # declaration, and the job's home never held the loop's remote.
    assert first_home == pass_home == second.directory / "conan"
    assert Path(pass_environment["CONAN_HOME"]) == second.directory / "conan"
    assert "CONAN_HOME" not in (second.runner_dir(1) / "config.yaml").read_text()
    assert "workshop" not in _remotes(job_home, tmp_path)
    # A pass that is not fresh keeps the environment, its forge, and the
    # remote that names it.
    third = _e2e.enter_environment("e2e", ("conan",))
    assert third.url == "http://localhost:57713"
    assert _remotes(third.directory / "conan", tmp_path)["workshop"] == _registry(57713)


def test_a_workspace_s_origin_names_this_pass_s_forge_before_the_start_over_asks_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A fresh pass took a new forge, and the workspace an earlier pass
    # left names the earlier forge in its origin. A pass for another stack
    # starts over, and the start-over asks the origin for the commits it
    # has not seen: the origin names this pass's forge and token first.
    root = tmp_path / _e2e.E2E_REPO
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    earlier = "http://oauth2:token-old@localhost:47615/livery/ci-e2e-loop.git"
    subprocess.run(["git", "remote", "add", "origin", earlier], cwd=root, check=True)
    seen: list[str] = []

    class _Stop(Exception):
        pass

    def start_over(
        lane: object, token: str, root: Path, *, url: str, kind: str = "gitea"
    ) -> list[str]:
        asked = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
        seen.append(asked.stdout.strip())
        raise _Stop

    monkeypatch.setattr(
        _e2e,
        "CURRENT",
        _e2e.Current("e2e", "host", "http://localhost:57713", "token-new", "e2e-01"),
    )
    monkeypatch.setattr(_e2e, "_dev_forge", lambda kind: (object(), "token-new"))
    monkeypatch.setattr(_e2e, "_loop_home", lambda kind: tmp_path)
    monkeypatch.setattr(_e2e, "_listed_extensions", lambda root: frozenset({"docs"}))
    monkeypatch.setattr(_e2e, "start_over", start_over)
    fresh = _e2e.Pass("gitea", "http://localhost:57713", fresh=True, stack=("ruff",))
    with pytest.raises(_Stop):
        _e2e._born(fresh)  # pyright: ignore[reportPrivateUsage]
    assert seen == ["http://oauth2:token-new@localhost:57713/livery/ci-e2e-loop.git"]


def test_a_docker_pass_runs_conan_with_the_docker_environment_s_own_home(
    rig: Path, pass_environment: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    skip_unless_installed("conan")
    # The compose rig is long-lived and keeps its address, so nothing is
    # brought up or removed, fresh or not; conan's home is still the
    # environment's, never the person's own.
    monkeypatch.setenv("CONAN_HOME", str(rig / "person-conan"))
    docker = _devenv.environment("dev")
    docker.update({"MODE": "docker", "GITEA_URL": "http://localhost:3000"})
    entered = _e2e.enter_environment("dev", ("conan",), fresh=True)
    assert entered.directory == docker.directory and docker.env_file.is_file()
    assert (_e2e.CURRENT.name, _e2e.CURRENT.mode) == ("dev", "docker")
    # The declared value joins its placeholder with a slash, which Windows
    # takes as a separator too, so the homes compare as paths.
    assert Path(os.environ["CONAN_HOME"]) == docker.directory / "conan"
    assert Path(pass_environment["CONAN_HOME"]) == docker.directory / "conan"


def test_the_loop_refuses_a_branch_whose_release_verb_is_the_train(
    tmp_path: Path,
) -> None:
    # The loop builds its dev wheels with workflow.release, and on main
    # or a reserved workflow/ branch that verb opens a real release.
    import subprocess

    from livery.footman import Failed

    root = tmp_path / "ws"
    root.mkdir()

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "commit.gpgsign=false", *args],
            cwd=root,
            check=True,
            capture_output=True,
        )

    git("init", "-q", "-b", "main")
    (root / "seed.txt").write_text("x\n")
    git("add", "-A")
    git("commit", "-qm", "chore: seed")
    with pytest.raises(Failed, match=r"on 'main' that is the release train"):
        _e2e.require_dev_branch(root)
    git("checkout", "-q", "-b", "workflow/release/core")
    with pytest.raises(Failed, match=r"on 'workflow/release/core' that is the release"):
        _e2e.require_dev_branch(root)
    git("checkout", "-q", "--detach")
    with pytest.raises(Failed, match=r"on a detached HEAD that is the release train"):
        _e2e.require_dev_branch(root)
    # A feature branch's release verb is the dev act, and the loop goes on.
    git("checkout", "-q", "-b", "chore/loop")
    _e2e.require_dev_branch(root)


def _main_repo(tmp_path: Path) -> Path:
    """A seeded repository on main, as a nightly's checkout stands."""
    import subprocess

    root = tmp_path / "driver"
    root.mkdir()

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "commit.gpgsign=false", *args],
            cwd=root,
            check=True,
            capture_output=True,
        )

    git("init", "-q", "-b", "main")
    (root / "seed.txt").write_text("x\n")
    git("add", "-A")
    git("commit", "-qm", "chore: seed")
    return root


def test_from_main_refuses_a_dirty_checkout_naming_the_paths(tmp_path: Path) -> None:
    from livery.footman import Failed

    root = _main_repo(tmp_path)
    (root / "seed.txt").write_text("y\n")
    with (
        pytest.raises(
            Failed, match=r"working tree has changes: seed.txt; commit or discard"
        ),
        _e2e.scratch_branch(root, enabled=True),
    ):
        pass


def test_from_main_runs_the_pass_on_a_scratch_branch_and_returns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:

    root = _main_repo(tmp_path)
    with _e2e.scratch_branch(root, enabled=True):
        inside = git.current_branch(root)
        assert inside.startswith("chore/loop-")
        # The loop's own refusal lets the pass go on from here.
        _e2e.require_dev_branch(root)
    assert git.current_branch(root) == "main"
    assert not git.local_branch_exists(root, inside)
    out = capsys.readouterr().out
    assert f"from main: left 'main' for {inside}" in out
    assert f"back on main; {inside} deleted" in out


def test_from_main_keeps_a_scratch_branch_the_pass_committed_on(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:

    root = _main_repo(tmp_path)
    with _e2e.scratch_branch(root, enabled=True):
        inside = git.current_branch(root)
        (root / "made.txt").write_text("by the pass\n")
        git.commit_all(root, "chore: what the pass made")
    assert git.current_branch(root) == "main"
    assert git.local_branch_exists(root, inside)
    assert f"{inside} holds 1 commit(s) and stays" in capsys.readouterr().out


def test_from_main_leaves_a_dev_branch_and_an_unasked_checkout_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:

    root = _main_repo(tmp_path)
    with _e2e.scratch_branch(root, enabled=False):
        assert git.current_branch(root) == "main"
    git.create_branch(root, "chore/loop")
    with _e2e.scratch_branch(root, enabled=True):
        assert git.current_branch(root) == "chore/loop"
    with _e2e.scratch_branch(None, enabled=True):
        pass
    assert "from main" not in capsys.readouterr().out


def test_the_birth_runs_from_the_driver_with_an_absolute_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    seen: list[tuple[list[str], object]] = []

    def _run(argv: list[str], **kwargs: object) -> object:
        seen.append((argv, kwargs.get("cwd")))
        return SimpleNamespace(code=0, stdout="", stderr="")

    monkeypatch.setattr("livery.footman.run", _run)
    home = tmp_path / "home"
    monkeypatch.setattr(_e2e, "_loop_home", lambda kind: home)
    driver = tmp_path / "driver"
    # From a driver checkout: the verb its project mounts, the folder absolute.
    _e2e._birth("gitea", "http://localhost:1", driver=driver)
    argv, cwd = seen[-1]
    assert cwd == driver
    assert argv[argv.index("new.project") + 1] == str(home / "ci-e2e-loop")
    # Without one: the machine's footman, from the loop's home.
    _e2e._birth("gitea", "http://localhost:1")
    argv, cwd = seen[-1]
    assert cwd == home
    assert argv[argv.index("new.project") + 1] == "ci-e2e-loop"


def test_the_pass_removes_ci_markers_from_its_children_environment() -> None:
    # Nothing to remove first: a desk's environment is left as it is.
    desk = {"PATH": "/usr/bin", "GITHUB_TOKEN": "t"}
    assert _e2e.scrub_ci_markers(desk) == ()
    assert desk == {"PATH": "/usr/bin", "GITHUB_TOKEN": "t"}
    # A hosted job's markers go, every other key stays: a child reading
    # CI would refuse a fix and stamp the loop's runs as the runner's.
    hosted = {
        "CI": "true",
        "GITHUB_ACTIONS": "true",
        "GITEA_ACTIONS": "true",
        "GITLAB_CI": "true",
        "RUNNER_TEMP": "/t",
        "PATH": "/usr/bin",
    }
    assert _e2e.scrub_ci_markers(hosted) == (
        "CI",
        "GITHUB_ACTIONS",
        "GITEA_ACTIONS",
        "GITLAB_CI",
    )
    assert hosted == {"RUNNER_TEMP": "/t", "PATH": "/usr/bin"}


def test_the_pass_edits_the_process_and_the_task_environments_alike(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A git the pass runs through the store spawns from the task's own
    # environment, footman's run from ctx.env: both must carry the edit.
    from types import SimpleNamespace

    task_env: dict[str, str] = {"CI": "true", "KEEP": "1"}
    monkeypatch.setattr("livery.footman.current", lambda: SimpleNamespace(env=task_env))
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv("GITEA_ACTIONS", raising=False)
    removed = _e2e.edit_environments(
        {"GIT_CONFIG_COUNT": "1"}, remove=("CI", "GITEA_ACTIONS")
    )
    assert removed == ("CI",)
    assert os.environ["GIT_CONFIG_COUNT"] == "1"
    assert "CI" not in os.environ and os.environ["GITHUB_ACTIONS"] == "true"
    assert task_env == {"KEEP": "1", "GIT_CONFIG_COUNT": "1"}


def test_the_members_land_through_one_pull_request_or_none_when_all_landed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The skip first: every member already on main submits nothing.
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(_e2e, "_align_main", lambda root: calls.append(("align",)))
    monkeypatch.setattr(
        _e2e, "_fresh_branch", lambda root, name: calls.append(("branch", name))
    )

    def loop_fm(root: Path, *args: str, **kw: object) -> int:
        calls.append(("fm", *args))
        if args[0].startswith("new.package."):
            member = root / "packages" / args[1]
            member.mkdir(parents=True)
            hosted = '["ubuntu-latest", "macos-latest", "windows-latest"]'
            (member / "workshop.toml").write_text(
                f"[nanobind]\nwheel-platforms = {hosted}\n", "utf-8"
            )
        return 0

    monkeypatch.setattr(_e2e, "_loop_fm", loop_fm)

    def commit_all(root: Path, message: str) -> None:
        calls.append(("commit", message.splitlines()[0]))

    monkeypatch.setattr(git, "commit_all", commit_all)
    for member in _e2e.LOOP_MEMBERS:
        (tmp_path / "packages" / member.name).mkdir(parents=True)
        (tmp_path / "packages" / member.name / "workshop.toml").write_text("", "utf-8")
    _e2e._ensure_members(tmp_path)  # pyright: ignore[reportPrivateUsage]
    assert calls == [("align",)]
    assert "member loop-echo: already landed" in capsys.readouterr().out
    # Two missing members: one branch, one commit, one submit for both.
    calls.clear()
    shutil.rmtree(tmp_path / "packages" / "loop-native")
    shutil.rmtree(tmp_path / "packages" / "loop-cpp")
    _e2e._ensure_members(tmp_path)  # pyright: ignore[reportPrivateUsage]
    assert calls == [
        ("align",),
        ("branch", "feat/members"),
        ("fm", "new.package.nanobind-module", "loop-native"),
        ("fm", "new.package.cpp-package", "loop-cpp"),
        ("fm", "drift.check", "--fix"),
        ("commit", "feat: the loop's members"),
        ("fm", "submit", "--force", "--armed"),
        ("align",),
    ]
    native = (tmp_path / "packages" / "loop-native" / "workshop.toml").read_text(
        "utf-8"
    )
    assert f'[nanobind]\nwheel-platforms = ["{_e2e.CURRENT.label}"]' in native
    assert 'baseline = "0.1.0"' in native
    out = capsys.readouterr().out
    assert "members loop-native, loop-cpp: landed through the loop's own gate" in out


def _wiring_rig(
    monkeypatch: pytest.MonkeyPatch, root: Path, *, refuse_the_lock: bool = False
) -> list[tuple[str, ...]]:
    """The calls `_eat_dev_wheels` makes at *root*, its forge and loop faked.

    The birth's contract is written first: a root that lists cmake, a
    ``[toolroom]`` table without the allowance, and the ``[ci]`` table
    a birth seeds. The loop's own fm records each call, and its
    ``toolroom.lock`` call records whether the contract it locks against
    holds the allowance. *refuse_the_lock* makes that call refuse, as
    the loop's fm does when its lock cannot be written.
    """
    from types import SimpleNamespace

    (root / "workshop.toml").write_text(
        "[workspace]\n"
        'name = "ci-e2e-loop"\n'
        'extensions = ["cmake"]\n'
        "\n[toolroom]\n"
        'index = "https://tools.invalid/"\n'
        "\n[ci]\n"
        'runners = ["ubuntu-latest"]\n',
        "utf-8",
    )
    calls: list[tuple[str, ...]] = []
    lane = SimpleNamespace(
        url_var="LOOP_TEST_FORGE_URL",
        alias="http://forge.invalid:3000",
        index=lambda: "http://forge.invalid:3000/api/packages/livery/pypi/simple",
        publish=lambda: "http://forge.invalid:3000/api/packages/livery/pypi",
    )
    monkeypatch.setattr(_e2e, "_lane", lambda kind: lane)
    monkeypatch.setattr(
        _e2e, "_fresh_branch", lambda root, name: calls.append(("branch", name))
    )
    monkeypatch.setattr(
        _e2e, "_render_with_the_pass", lambda root: calls.append(("render",))
    )
    monkeypatch.setattr(_e2e, "_lock_pins", lambda root, pins: calls.append(("uv",)))
    monkeypatch.setattr(_e2e, "_listed_extensions", lambda root: frozenset({"cmake"}))

    def loop_fm(root: Path, *args: str, **kw: object) -> int:
        calls.append(("fm", *args))
        if args == ("toolroom.lock",):
            contract = (root / "workshop.toml").read_text("utf-8")
            allowed = 'host-allowed = ["cmake", "ninja"]' in contract
            calls.append(("allowance in the contract", str(allowed)))
            if refuse_the_lock:
                raise Failed("the loop's `fm toolroom.lock` exited 1")
        return 0

    monkeypatch.setattr(_e2e, "_loop_fm", loop_fm)
    monkeypatch.setattr(git, "is_clean", lambda root: False)
    monkeypatch.setattr(
        git, "commit_all", lambda root, message: calls.append(("commit",))
    )
    monkeypatch.setattr(git, "remote_head", lambda root, branch: "")
    monkeypatch.setattr(
        git, "push_force", lambda root, branch: calls.append(("push", branch))
    )
    monkeypatch.setattr(git, "head_sha", lambda root: "5730fa48b2")
    return calls


def test_a_tool_lock_the_loop_s_fm_cannot_write_stops_the_wiring_before_its_push(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _wiring_rig(monkeypatch, tmp_path, refuse_the_lock=True)
    with pytest.raises(Failed, match=r"`fm toolroom.lock` exited 1"):
        _e2e._eat_dev_wheels(tmp_path, {})  # pyright: ignore[reportPrivateUsage]
    assert ("commit",) not in calls
    assert not [call for call in calls if call[0] == "push"]


def test_the_wiring_writes_the_tool_lock_after_its_allowance_and_before_its_push(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """CI's setup refuses a tool lock the contract has moved past.

    The birth writes the lock before the wiring adds the allowance for
    cmake and ninja, and the allowance joins each allowed tool's entry,
    so a root whose set requires cmake has a lock that moves. The
    wiring pushes without `fm submit`'s lock check, so it writes the
    lock itself, against the contract it edited.
    """
    from livery.workshop._new_project import _SETUP_BRANCH

    calls = _wiring_rig(monkeypatch, tmp_path)
    head = _e2e._eat_dev_wheels(tmp_path, {})  # pyright: ignore[reportPrivateUsage]
    assert head == "5730fa48b2"
    assert calls == [
        ("branch", _SETUP_BRANCH),
        ("render",),
        ("uv",),
        ("fm", "drift.check", "--fix"),
        ("fm", "toolroom.lock"),
        ("allowance in the contract", "True"),
        ("uv",),
        ("commit",),
        ("push", _SETUP_BRANCH),
    ]


def test_the_plain_pass_lists_the_stock_but_the_site_and_what_members_need() -> None:
    """The site is the docs extension and every stock entry that requires it.

    The pass proves the gate, the release train and the points, and
    nothing in it reads a site. A creation installs nothing, so the root
    lists what the members' templates need before they are created. The
    stack is pinned too, since the hosted rehearsal's plain pass births
    it.
    """
    from livery.workshop._extensions import SELF, declaration
    from livery.workshop._new_project import birth_extensions, canonical_extensions

    # The site is what mkdocstrings' declaration requires.
    skip_unless_installed("mkdocstrings", "nanobind")
    stock = tuple(birth_extensions([SELF]))

    def builds_the_site(name: str) -> bool:
        found = declaration(name)
        needed = found.requires if found is not None else ()
        return name == "docs" or any(builds_the_site(other) for other in needed)

    site = tuple(name for name in stock if builds_the_site(name))
    assert site == ("mkdocstrings",)
    needs = {member.name: member.needs() for member in _e2e.LOOP_MEMBERS}
    assert needs == {
        "loop-echo": ("python",),
        "loop-native": ("python", "nanobind"),
        "loop-cpp": ("cmake", "conan"),
    }
    kept = [name for name in stock if name not in site]
    assert _e2e.loop_stack() == tuple(
        canonical_extensions([*kept, *(n for held in needs.values() for n in held)])
    )
    # nanobind requires python and conan, so the list names neither.
    assert _e2e.loop_stack() == (
        "basedpyright",
        "changelog",
        "cmake",
        "coverage",
        "pytest",
        "nanobind",
        "ruff",
    )


def test_a_red_run_prints_its_failed_jobs_log_tails_and_nothing_of_the_green() -> None:
    from types import SimpleNamespace

    # No failed job first: the header alone, so a red run with every
    # job green (a cancelled run) still says which run it was.
    logs = {7: "a\nb\n", 8: "\n".join(str(i) for i in range(100)) + "\n"}
    jobs = (
        SimpleNamespace(id=6, name="check", conclusion="success"),
        SimpleNamespace(id=7, name="wheels (e2e-linux-x64-01)", conclusion="failure"),
        SimpleNamespace(id=8, name="publish", conclusion="failure"),
    )
    checks = SimpleNamespace(jobs=lambda run: jobs, job_log=lambda job: logs[job])
    repo = SimpleNamespace(checks=checks, web_url=lambda: "http://forge/livery/loop")
    from livery.forge import Run

    run = Run(18, "release.yml", "5730fa48b2", "push", "completed", "failure")
    quiet = SimpleNamespace(
        checks=SimpleNamespace(jobs=lambda run: (jobs[0],), job_log=lambda job: ""),
        web_url=lambda: "http://forge/livery/loop",
    )
    assert _e2e.red_jobs_text(quiet, run) == (  # pyright: ignore[reportArgumentType]
        "  run 18 ended failure: http://forge/livery/loop"
    )
    text = _e2e.red_jobs_text(repo, run, lines=3)  # pyright: ignore[reportArgumentType]
    assert text.splitlines() == [
        "  run 18 ended failure: http://forge/livery/loop",
        "  wheels (e2e-linux-x64-01): failure; the last 2 line(s) of its log:",
        "    a",
        "    b",
        "  publish: failure; the last 3 line(s) of its log:",
        "    97",
        "    98",
        "    99",
    ]
    assert "check" not in text


def test_a_tree_whose_file_goes_under_the_walk_is_still_removed_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # git's background maintenance drops .git/objects/maintenance.lock
    # while a clone is torn down: the chmod meets a file that is gone and
    # the removal goes on.
    root = tmp_path / "clone"
    (root / ".git" / "objects").mkdir(parents=True)
    lock = root / ".git" / "objects" / "maintenance.lock"
    lock.write_text("")
    (root / "kept").write_text("k")
    real = Path.chmod

    def vanishing(self: Path, mode: int, *, follow_symlinks: bool = True) -> None:
        if self == lock:
            lock.unlink()
            raise FileNotFoundError(str(lock))
        real(self, mode, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "chmod", vanishing)
    _e2e._rmtree(root)
    assert not root.exists()


# --- the shards: the proofs, then the seeding --------------------------------

_LEG = "check-ubuntu-latest-3.14"
_JOB = "check (ubuntu-latest, 3.14)"
_SHARD_JOBS = ("check (ubuntu-latest, 3.14, 1of2)", "check (ubuntu-latest, 3.14, 2of2)")


def _fold(
    files: tuple[int, ...] = (2, 1),
    *,
    count: int = 2,
    rows: tuple[str, ...] = ("20261017T090000Z-76.json",),
    digest: str = "ab" * 32,
) -> _e2e_rows.FoldFacts:
    """The fold's record of *count* shards that ran *files* each."""
    shards = tuple(
        _e2e_rows.ShardFacts(
            f"{index}of{count}", f"{_LEG}-{index}of{count}", _SHARD_JOBS[index - 1], n
        )
        for index, n in enumerate(files, 1)
    )
    return _e2e_rows.FoldFacts(count, digest, rows, shards)


def _facts(
    fold: _e2e_rows.FoldFacts | None, *, shard_jobs: tuple[str, ...] = _SHARD_JOBS
) -> _e2e_rows.RunFacts:
    leg = _e2e_rows.LegFacts(_JOB, "affected", (), "", _LEG, (), fold=fold)
    return _e2e_rows.RunFacts("77", {_JOB: leg}, {}, shard_jobs)


def _run() -> Run:
    return Run(
        77, "ci.yml", "a" * 40, "push", "completed", "success", url="http://r/77"
    )


def test_the_shards_proof_refuses_a_leg_that_is_not_the_fold_of_the_shards() -> None:
    run = _run()
    # The leg ran as one shard of one.
    with pytest.raises(_FAILURES, match="is not a fold"):
        _e2e._require_shards(run, _facts(None), count=2, ran=True)
    # Another count than the contract's.
    with pytest.raises(_FAILURES, match=r"folds 1 shard\(s\) and lists 1 \("):
        _e2e._require_shards(run, _facts(_fold((3,), count=1)), count=2, ran=True)
    # A shard whose forge row the collect did not keep beside the fold's.
    with pytest.raises(_FAILURES) as caught:
        _e2e._require_shards(
            run, _facts(_fold(), shard_jobs=_SHARD_JOBS[:1]), count=2, ran=True
        )
    assert f"the forge's rows of {_SHARD_JOBS[1]} are not beside the folded row" in str(
        caught.value
    )
    # The leg ran, and the shards partitioned nothing or ran no file.
    with pytest.raises(_FAILURES, match="ran no test file"):
        _e2e._require_shards(run, _facts(_fold((0, 0))), count=2, ran=True)
    with pytest.raises(_FAILURES, match="recorded no partition"):
        _e2e._require_shards(run, _facts(_fold(digest="")), count=2, ran=True)
    # Every shard skipped, and the fold records files ran or rows read.
    with pytest.raises(_FAILURES) as caught:
        _e2e._require_shards(run, _facts(_fold()), count=2, ran=False)
    assert (
        "every shard skipped, and the fold records 3 test file(s) ran and 1"
        " durations row(s) read" in str(caught.value)
    )
    with pytest.raises(_FAILURES, match=r"0 test file\(s\) ran and 1 durations"):
        _e2e._require_shards(run, _facts(_fold((0, 0))), count=2, ran=False)
    # Then the shapes the loop proves: a leg that ran on a row, the
    # first pass with no row yet (the first shard ran every file), and
    # a leg whose every shard skipped.
    _e2e._require_shards(run, _facts(_fold()), count=2, ran=True)
    _e2e._require_shards(run, _facts(_fold((3, 0), rows=())), count=2, ran=True)
    _e2e._require_shards(run, _facts(_fold((0, 0), rows=())), count=2, ran=False)


def test_the_durations_proof_refuses_a_missing_row_on_a_run_and_a_row_on_a_skip(
    seeds: Seeds, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop import _ci_context, _metrics, _shard

    work = seeds("pushed", pushed) / "work"
    ctx = _ci_context.RunContext("gitea", "77", "push", "refs/heads/main")
    monkeypatch.setattr(_ci_context, "run_context", lambda environ=None: ctx)
    run = _run()
    series = _shard.DURATIONS.series(_LEG)
    name = _metrics.run_file("77")
    # Nothing on the store: the leg ran, so its row is missing.
    with pytest.raises(_FAILURES) as caught:
        _e2e._require_durations(work, run, f"{_LEG}-1of2", written=True)
    assert f"no row {name} naming a test file on {series.ref}" in str(caught.value)
    _e2e._require_durations(work, run, f"{_LEG}-1of2", written=False)
    _e2e._prove_shards(work, run, _facts(_fold((0, 0), rows=())), ran=False)
    assert (
        f"  shards: run 77's check leg skipped on every shard, folded into {_LEG};"
        " no durations row written" in capsys.readouterr().out
    )
    # The row on the store: every shard skipped, so a row is wrong.
    rows = {name: {"run": "77", "files": {"tests/test_a.py": 1.5}}}
    assert series.put(state_store(work), rows, message="durations: run 77") == ""
    with pytest.raises(_FAILURES) as caught:
        _e2e._require_durations(work, run, _LEG, written=False)
    assert f"{series.ref} holds {name} with 1 file(s), and every shard skipped" in (
        str(caught.value)
    )
    _e2e._require_durations(work, run, f"{_LEG}-2of2", written=True)
    _e2e._prove_shards(work, run, _facts(_fold()), ran=True)
    assert (
        f"  shards: run 77's check leg ran as 2 shards folded into {_LEG}; its"
        " durations row is on the store" in capsys.readouterr().out
    )


def test_the_wiring_seeds_two_shards_on_the_pass_s_runner_and_keeps_a_declared_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import tomllib

    _wiring_rig(monkeypatch, tmp_path)
    monkeypatch.setattr(_e2e.CURRENT, "label", "e2e-linux-x64-01")
    _e2e._eat_dev_wheels(tmp_path, {})  # pyright: ignore[reportPrivateUsage]
    contract = (tmp_path / "workshop.toml").read_text("utf-8")
    # The count names the environment's runner, the one the runners
    # list names, inside the [ci] table the matrix reads.
    assert 'runners = ["e2e-linux-x64-01"]' in contract
    assert contract.count("shards =") == 1
    assert tomllib.loads(contract)["ci"]["shards"] == {"e2e-linux-x64-01": _e2e.SHARDS}
    # Wired again, nothing moves; a count set by hand stands.
    _e2e._eat_dev_wheels(tmp_path, {})  # pyright: ignore[reportPrivateUsage]
    assert (tmp_path / "workshop.toml").read_text("utf-8") == contract
    by_hand = contract.replace(
        f'shards = {{ "e2e-linux-x64-01" = {_e2e.SHARDS} }}',
        'shards = { "e2e-linux-x64-01" = 3 }',
    )
    assert by_hand != contract
    (tmp_path / "workshop.toml").write_text(by_hand, "utf-8")
    _e2e._eat_dev_wheels(tmp_path, {})  # pyright: ignore[reportPrivateUsage]
    assert (tmp_path / "workshop.toml").read_text("utf-8") == by_hand
