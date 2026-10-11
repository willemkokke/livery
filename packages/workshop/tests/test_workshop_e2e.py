"""``fm ci.e2e``'s provisioning: refusals first, then the idempotent path."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import pytest

from livery.forge import Repository
from livery.forge.testing import FakeForge
from livery.workshop import _e2e
from livery.workshop._declaration import Declaration
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
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
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
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
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
                if args[2].startswith(":refs/tags/"):
                    return SimpleNamespace(code=deny["code"], stdout="", stderr="")
            return SimpleNamespace(code=0, stdout="", stderr="")

        return _run

    monkeypatch.setattr(tools, "git", SimpleNamespace(opts=_opts))
    with pytest.raises(_FAILURES, match="deletable on gitlab"):
        _e2e._require_receipt_protected(tmp_path, "packages/loop-echo/v0.1.0", "gitlab")
    assert pushes[-1] == ("push", "origin", "refs/tags/packages/loop-echo/v0.1.0")
    deny["code"] = 1
    _e2e._require_receipt_protected(tmp_path, "packages/loop-echo/v0.1.0", "gitlab")
    assert "delete refused; protection holds" in capsys.readouterr().out


def test_the_pass_turns_signing_off_for_every_git_it_runs() -> None:
    """A signer that waits for a person fails an unattended pass; the setting rides."""
    assert _e2e.unsigned_environment({}) == {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "commit.gpgsign",
        "GIT_CONFIG_VALUE_0": "false",
    }
    # An entry the outer environment carries keeps its index; this one follows.
    outer = {
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "a.b",
        "GIT_CONFIG_KEY_1": "c.d",
    }
    assert _e2e.unsigned_environment(outer) == {
        "GIT_CONFIG_COUNT": "3",
        "GIT_CONFIG_KEY_2": "commit.gpgsign",
        "GIT_CONFIG_VALUE_2": "false",
    }
    assert (
        _e2e.unsigned_environment({"GIT_CONFIG_COUNT": "x"})["GIT_CONFIG_COUNT"] == "1"
    )


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

    def run(argv: list[str], **kw: object) -> SimpleNamespace:
        env = kw["env"]
        assert isinstance(env, dict)
        seen.append((argv, kw["cwd"], str(env.get("FOOTMAN_NO_UV", ""))))
        return SimpleNamespace(
            code=code[0], stdout="  updated pyproject.toml\n", stderr="no index"
        )

    monkeypatch.setattr("livery.footman.run", run)
    with pytest.raises(_FAILURES, match="the pass's render of the loop exited 3"):
        _e2e._render_with_the_pass(tmp_path)  # pyright: ignore[reportPrivateUsage]
    code[0] = 0
    _e2e._render_with_the_pass(tmp_path)  # pyright: ignore[reportPrivateUsage]
    argv = [sys.executable, "-m", "livery.footman", "--yes", "drift.check", "--fix"]
    assert seen == [(argv, tmp_path, "1"), (argv, tmp_path, "1")]
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

    monkeypatch.setattr(tools, "git", SimpleNamespace(opts=_opts))
    _e2e._align_main(tmp_path)
    cleans = [call for call in calls if call[0] == "clean"]
    kept = ("-e", ".venv", "-e", "typings", "-e", ".workshop")
    assert cleans == [("clean", "-ndx", *kept), ("clean", "-fdx", *kept)]
    assert "cleaned: dist/" in capsys.readouterr().out


def test_the_serving_probe_asks_each_member_s_own_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A python member is probed on the simple index, the conan member on its target."""
    from types import SimpleNamespace

    import livery.forge as forge_package
    from livery.workshop import _registries
    from livery.workshop._backends import _cpp_conan

    asked: list[tuple[str, str]] = []
    built: list[tuple[object, Path]] = []

    class _Simple:
        def __init__(self, url: str, *, token: str) -> None:
            assert (url, token) == ("http://gitea:3000/simple", "lane-token")

        def versions(self, name: str) -> tuple[str, ...]:
            asked.append(("python", name))
            return ("0.1.0",)

    class _Conan:
        def __init__(self, target: object, *, root: Path) -> None:
            built.append((target, root))

        def versions(self, name: str) -> tuple[str, ...]:
            asked.append(("conan", name))
            return ()

    monkeypatch.setattr(forge_package, "SimpleRegistry", _Simple)
    monkeypatch.setattr(_cpp_conan, "ConanRegistry", _Conan)
    monkeypatch.setattr(
        _registries, "resolve_registry", lambda root, kind: f"target:{kind}"
    )
    monkeypatch.setattr(_e2e, "_dev_forge", lambda kind: (None, "lane-token"))
    monkeypatch.setattr(
        _e2e,
        "_lane",
        lambda kind: SimpleNamespace(index=lambda: "http://gitea:3000/simple"),
    )
    # New members are named by the namespace, which keeps its
    # underscores; a conan registry matches that name exactly.
    contract = tmp_path / "packages" / "loop-cpp" / "workshop.toml"
    contract.parent.mkdir(parents=True)
    contract.write_text('kind = "cpp-conan"\nname = "ci_e2e_loop-loop-cpp"\n')
    served = _e2e._serving_probe(tmp_path, "gitea")
    assert served("loop-echo") == ("0.1.0",)
    assert served("loop-native") == ("0.1.0",)
    assert served("loop-cpp") == ()
    assert served("loop-cpp") == ()
    assert asked == [
        ("python", "ci-e2e-loop-loop-echo"),
        ("python", "ci-e2e-loop-loop-native"),
        ("conan", "ci_e2e_loop-loop-cpp"),
        ("conan", "ci_e2e_loop-loop-cpp"),
    ]
    # The conan target resolves once, for the wave's whole wait.
    assert built == [("target:conan", tmp_path)]


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
        f'kind = "python"\nname = "livery-{name}"\n' + declared
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
        'kind = "python"\nname = "livery-extensions-ruff"\n'
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
    with pytest.raises(
        _FAILURES, match="no installed distribution declares it"
    ) as caught:
        _e2e.extension_under_test("no-such-extension")
    # The docs extension rides in the workshop's own wheel, so it is
    # installed wherever the workshop is, its release leg included.
    assert "docs" in str(caught.value)


def _declared(
    *extensions: str,
    requires: tuple[str, ...] = (),
    options: dict[str, str] | None = None,
) -> Declaration:
    """A declaration with one check judging the packages holding *extensions*."""
    from livery.workshop._checks import CheckRecord
    from livery.workshop._declaration import Additions

    def _idle(ctx: object) -> None:
        del ctx

    checks = (
        (CheckRecord("t", "lint", _idle, extensions=extensions),) if extensions else ()
    )
    return Declaration(
        "x",
        "x",
        Path("extension.toml"),
        requires=requires,
        options=options or {},
        additions=Additions(checks),
    )


def _fake_declarations(
    monkeypatch: pytest.MonkeyPatch, declared: dict[str, Declaration]
) -> None:
    monkeypatch.setattr(
        "livery.workshop._extensions.declaration", lambda name: declared.get(name)
    )
    monkeypatch.setattr(
        "livery.workshop._extensions.declared_options",
        lambda name: dict(declared[name].options) if name in declared else {},
    )


def test_an_extension_with_no_check_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_declarations(monkeypatch, {"quiet": _declared()})
    with pytest.raises(_FAILURES, match="registers no check"):
        _e2e.extension_under_test("quiet")


def test_extensions_that_require_each_other_refuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_declarations(
        monkeypatch,
        {
            "a": _declared("python", requires=("b",)),
            "b": _declared("python", requires=("a",)),
        },
    )
    with pytest.raises(_FAILURES, match="a -> b -> a"):
        _e2e.extension_under_test("a")


def test_the_stack_lists_what_an_extension_requires_before_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_declarations(
        monkeypatch,
        {
            "top": _declared("cmake", requires=("mid", "base")),
            "mid": _declared("cmake", requires=("base",)),
            "base": _declared("cmake"),
        },
    )
    stack, kinds = _e2e.extension_under_test("top")
    assert stack == ("base", "mid", "top")
    assert kinds == ("cpp-conan",)


def test_the_extension_under_test_is_listed_with_every_option_it_declares(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_declarations(
        monkeypatch,
        {
            "top": _declared(
                "python",
                requires=("base",),
                options={"deep": "judges deeper", "wide": "judges wider"},
            ),
            "base": _declared("python", options={"x": "an option"}),
        },
    )
    # What it requires is listed plainly: its options are not under test.
    stack, _kinds = _e2e.extension_under_test("top")
    assert stack == ("base", "top[deep,wide]")


def test_the_members_are_the_loop_s_whose_kinds_stand_for_what_the_checks_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_declarations(
        monkeypatch,
        {"typed": _declared("python"), "both": _declared("python", "conan")},
    )
    stack, kinds = _e2e.extension_under_test("typed")
    assert stack == ("typed",)
    # A kind standing for an extension a check names, not every kind
    # deriving from it.
    assert [name for name, _seed in _e2e.members_for(kinds)] == ["loop-echo"]
    _stack, kinds = _e2e.extension_under_test("both")
    assert kinds == ("python", "cpp-conan")
    assert [name for name, _seed in _e2e.members_for(kinds)] == [
        "loop-echo",
        "loop-cpp",
    ]
    assert _e2e.members_for(()) == _e2e.LOOP_MEMBERS


def test_the_birth_lists_the_stack_of_the_pass(
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
    assert not any(arg.startswith("--stack") for arg in seen[-1])
    _e2e._birth("gitea", "http://localhost:1", stack=("base", "top"))
    assert "--stack=base,top" in seen[-1]


def test_the_loop_s_own_fm_never_asks_a_signer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    import livery.toolroom.tools as toolroom

    # The pass's own write lands in the task's environment, so the
    # process's carries no setting: the child must get it anyway.
    for key in ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0"):
        monkeypatch.delenv(key, raising=False)
    handed: list[dict[str, str]] = []

    class _Uv:
        def opts(self, *, env: dict[str, str], **_: object) -> _Uv:
            handed.append(env)
            return self

        def __call__(self, *args: str) -> object:
            return SimpleNamespace(code=0, stdout="", stderr="")

    monkeypatch.setattr(toolroom, "uv", _Uv())
    _e2e._loop_fm(tmp_path, "status")
    (env,) = handed
    count = int(env["GIT_CONFIG_COUNT"])
    pairs = {
        env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"] for i in range(count)
    }
    assert pairs["commit.gpgsign"] == "false"


def test_a_contract_s_list_is_read_as_each_entry_spells_it(tmp_path: Path) -> None:
    assert _e2e._listed_extensions(tmp_path) == ()  # pyright: ignore[reportPrivateUsage]
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nextensions = ["docs", { name = "acme.house", for = [] },'
        ' "basedpyright[typecomplete]"]\n'
    )
    listed = _e2e._listed_extensions(tmp_path)  # pyright: ignore[reportPrivateUsage]
    assert listed == ("docs", "acme.house", "basedpyright[typecomplete]")


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
    from types import SimpleNamespace

    for member in MEMBERS:
        home = tmp_path / "packages" / member
        home.mkdir(parents=True)
        (home / "workshop.toml").write_text(
            f'kind = "python"\nname = "livery-{member}"\n'
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
    monkeypatch.setattr(
        "livery.workshop._git_ops.GitOps",
        lambda root: SimpleNamespace(
            head_sha=lambda: HEAD,
            is_clean=lambda: clean,
            # The loop runs from a feature branch, where the release
            # verb is the dev act.
            current_branch=lambda: "chore/loop",
        ),
    )
    monkeypatch.setattr(
        "livery.workshop._dev_release.unchanged_since_release",
        lambda root, git, package: "0.3.0" if package.name == "livery-forge" else "",
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


def test_the_checkout_index_refuses_a_member_without_a_version(
    tmp_path: Path,
) -> None:
    from livery.workshop._packages import Package

    (tmp_path / "pyproject.toml").write_text('[project]\nname = "livery-x"\n')
    member = Package(
        directory=tmp_path,
        path="packages/x",
        name="livery-x",
        kind="python",
        depends=(),
    )
    with pytest.raises(_FAILURES, match="no version line"):
        _e2e._stamp_content(member, tmp_path)  # pyright: ignore[reportPrivateUsage]


def test_the_checkout_index_builds_what_a_newborn_installs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import livery.toolroom.tools as toolroom

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
    for key, value in (("user.name", "t"), ("user.email", "t@livery.local")):
        subprocess.run(["git", "-C", str(root), "config", key, value], check=True)
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


def test_a_proof_names_the_job_it_cannot_find_and_the_lines_it_misses() -> None:
    fake, repo = _proof_rig()
    fake.push(_e2e.E2E_OWNER, _e2e.E2E_REPO, "main", sha=_SHA)
    fake.settle(_e2e.E2E_OWNER, _e2e.E2E_REPO, _SHA)
    run, logs = _e2e._completed_run(repo, _SHA, event="push")
    assert [job.name for job in logs] == ["gate"]
    with pytest.raises(_FAILURES, match="has no check job"):
        _e2e._require_lines(repo, run, logs, "check", ("x",))
    with pytest.raises(_FAILURES, match=r"did not say \['absent line'\]"):
        _e2e._require_lines(
            repo, run, logs, "gate", ("concluded success", "absent line")
        )
    _e2e._require_lines(repo, run, logs, "gate", ("concluded success",))
    with pytest.raises(
        _FAILURES, match=r"said \['concluded success'\], which the proof"
    ):
        _e2e._require_lines(
            repo, run, logs, "gate", (), forbidden=("concluded success", "absent")
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
    assert names == ["birth", "verified-skip", "members", "ratchet", "scoped-leg"]
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
    from livery.workshop._series import DECLARED

    assert _e2e.LOOP_SERIES in DECLARED and _e2e.LOOP_SERIES.local
    assert not _e2e.LOOP_SERIES.ci_only


def test_a_pass_records_its_rows_on_the_local_loop_series(seeds: Seeds) -> None:
    work = seeds("pushed", pushed) / "work"
    pass_ = _e2e.Pass("gitea", "http://gitea:3000")
    pass_.timings.append(_e2e.Timing("birth", 12.34, 3))
    line = _e2e.record_pass(work, pass_, "develop")
    assert line.startswith("  loop series: pass ") and line.endswith("(1 scenario(s))")
    found = _e2e.LOOP_SERIES.rows(work)
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
    # The proofs read their lines with the environment's label in place
    # of the hosted name.
    pinned = ("coverage record: main/check-ubuntu-latest-3.14: 0 fresh",)
    assert _e2e.with_label(pinned) == (
        "coverage record: main/check-scratch-macos-arm-01-3.14: 0 fresh",
    )
    monkeypatch.setattr(_e2e, "CURRENT", _e2e.Current())
    assert _e2e.with_label(("check-ubuntu-latest-3.14",)) == (
        "check-ubuntu-latest-3.14",
    )
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
    git("config", "user.email", "dev@acme.test")
    git("config", "user.name", "Acme")
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
    git("config", "user.email", "dev@acme.test")
    git("config", "user.name", "Acme")
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
    from livery.workshop._git_ops import GitOps

    root = _main_repo(tmp_path)
    git = GitOps(root)
    with _e2e.scratch_branch(root, enabled=True):
        inside = git.current_branch()
        assert inside.startswith("chore/loop-")
        # The loop's own refusal lets the pass go on from here.
        _e2e.require_dev_branch(root)
    assert git.current_branch() == "main"
    assert not git.local_branch_exists(inside)
    out = capsys.readouterr().out
    assert f"from main: left 'main' for {inside}" in out
    assert f"back on main; {inside} deleted" in out


def test_from_main_keeps_a_scratch_branch_the_pass_committed_on(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop._git_ops import GitOps

    root = _main_repo(tmp_path)
    git = GitOps(root)
    with _e2e.scratch_branch(root, enabled=True):
        inside = git.current_branch()
        (root / "made.txt").write_text("by the pass\n")
        git.commit_all("chore: what the pass made")
    assert git.current_branch() == "main"
    assert git.local_branch_exists(inside)
    assert f"{inside} holds 1 commit(s) and stays" in capsys.readouterr().out


def test_from_main_leaves_a_dev_branch_and_an_unasked_checkout_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop._git_ops import GitOps

    root = _main_repo(tmp_path)
    git = GitOps(root)
    with _e2e.scratch_branch(root, enabled=False):
        assert git.current_branch() == "main"
    git.create_branch("chore/loop")
    with _e2e.scratch_branch(root, enabled=True):
        assert git.current_branch() == "chore/loop"
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
