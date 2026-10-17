"""The python extension's steps and answers, run by the engine that asks for them.

The release train builds, verifies and probes a package through the
phases and queries its set answers, a test edge lands through the
writers the set answers, and ``fm run`` starts a script through the
run phase. Each test here goes through that engine, with uv, the
started script and the index faked where a test would otherwise reach
them. The refusals come first.
"""

from __future__ import annotations

import base64
import sys
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import livery.footman as footman
import livery.toolroom.tools as toolroom
from extensions_python_workspace import member
from livery.footman import Failed
from livery.workshop import Package, discover_packages, scripts_dir


def _package(
    root: Path, name: str = "core", *, listed: tuple[str, ...] = ("python",)
) -> Package:
    """The member ``acme-<name>`` under *root* at 1.0.0, listing *listed*."""
    directory = root / "packages" / name
    module = directory / "src" / "acme" / name
    module.mkdir(parents=True)
    (module / "__init__.py").write_text("")
    (directory / "pyproject.toml").write_text(
        f'[project]\nname = "acme-{name}"\nversion = "1.0.0"\n'
    )
    return Package(
        directory=directory,
        path=f"packages/{name}",
        name=f"acme-{name}",
        depends=(),
        extensions=listed,
    )


# --- the build phase --------------------------------------------------------------


def test_the_release_build_refuses_a_red_uv_and_a_build_that_wrote_no_wheel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _lifecycle

    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    package = _package(tmp_path)
    envs: list[dict[str, str]] = []
    code = [2]
    wrote: list[str] = []

    def _opts(**kwargs: Any) -> object:
        def _run(*args: str) -> object:
            envs.append(kwargs["env"])
            out = Path(args[args.index("--out-dir") + 1])
            out.mkdir(parents=True, exist_ok=True)
            for name in wrote:
                (out / name).write_bytes(b"")
            return SimpleNamespace(code=code[0], stdout="built\n", stderr="refused\n")

        return _run

    monkeypatch.setattr(toolroom, "uv", SimpleNamespace(opts=_opts))
    # The refusal names the package and carries uv's own words.
    with pytest.raises(Failed, match=r"uv build \(acme-core\) exited 2") as refused:
        _lifecycle.build_release(package, tmp_path, epoch=1_700_000_000)
    assert "built\nrefused\n" in str(refused.value)
    # The epoch is passed as SOURCE_DATE_EPOCH, so one tree builds the same bytes.
    assert envs[-1]["SOURCE_DATE_EPOCH"] == "1700000000"
    code[0] = 0
    with pytest.raises(Failed, match="acme-core: the build produced no wheel in"):
        _lifecycle.build_release(package, tmp_path)
    # Without an epoch the build tool takes its own date.
    assert "SOURCE_DATE_EPOCH" not in envs[-1]
    wrote[:] = ["acme_core-1.0.0-py3-none-any.whl", "acme_core-1.0.0.tar.gz"]
    dist = _lifecycle.build_release(package, tmp_path, epoch=1_700_000_000)
    assert dist == package.directory / "dist"
    assert sorted(path.name for path in dist.iterdir()) == sorted(wrote)


# --- the run phase ----------------------------------------------------------------


def _scripted(tmp_path: Path) -> Path:
    """A workspace whose member ``tool`` declares the script ``tool``; its root."""
    root = tmp_path / "ws"
    root.mkdir()
    (root / "workshop.toml").write_text("[workspace]\n")
    member(root, "tool")
    pyproject = root / "packages" / "tool" / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text() + '\n[project.scripts]\ntool = "livery.tool:main"\n'
    )
    return root


def test_fm_run_refuses_a_package_without_scripts_and_a_script_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._run import start

    monkeypatch.setattr(footman, "prog", lambda: "fm")
    root = _scripted(tmp_path)
    pyproject = root / "packages" / "tool" / "pyproject.toml"
    declared = pyproject.read_text()
    # A manifest naming no script gives the package no executable.
    pyproject.write_text(declared.partition("\n[project.scripts]")[0] + "\n")
    with pytest.raises(Failed, match="packages/tool has no executable"):
        start(root, "tool", (), root)
    # A declared script that the environment does not hold refuses,
    # naming the directory and the verb that installs it there.
    pyproject.write_text(declared)
    with pytest.raises(Failed) as refused:
        start(root, "tool", (), root)
    assert str(refused.value) == (
        f"packages/tool: {scripts_dir(root / '.venv')} holds no script tool;"
        " `fm sync` installs the workspace's packages there"
    )


def test_fm_run_starts_the_script_with_the_words_and_exits_with_its_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._queries import EXECUTABLES, answer
    from livery.workshop._run import start

    root = _scripted(tmp_path)
    (tool,) = [p for p in discover_packages(root) if p.member == "tool"]
    assert answer(tool, EXECUTABLES) == ("tool",)
    script = scripts_dir(root / ".venv") / (
        "tool.exe" if sys.platform == "win32" else "tool"
    )
    script.parent.mkdir(parents=True)
    script.write_text("")
    started: list[tuple[list[str], dict[str, Any]]] = []
    original = footman.run

    def _run(argv: list[str] | str, *args: Any, **options: Any) -> object:
        if isinstance(argv, str) or argv[:1] != [str(script)]:
            return original(argv, *args, **options)
        started.append((argv, options))
        return SimpleNamespace(code=5)

    monkeypatch.setattr(footman, "run", _run)
    assert start(root, "tool", ("one", "two"), root) == 5
    # The console is shared and a red exit is the code, never a raise.
    assert started == [
        ([str(script), "one", "two"], {"capture": False, "nofail": True})
    ]


# --- the verify phase -------------------------------------------------------------


def test_the_verify_phase_runs_the_tests_once_the_install_is_green(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.extensions.python import _verify
    from livery.workshop import _lifecycle

    package = _package(tmp_path / "ws")
    # The tree checked out at the receipt tag holds the package's module.
    tree = tmp_path / "scratch" / "tree"
    module = tree / "packages" / "core" / "src" / "acme" / "core"
    module.mkdir(parents=True)
    (module / "__init__.py").write_text("")
    installs: list[tuple[Path, str, str, str]] = []
    tested: list[tuple[Path, Path, str, str]] = []

    def _install(venv: Path, python: str, requirement: str, index: str) -> int:
        installs.append((venv, python, requirement, index))
        return 0

    def _test(venv: Path, tree: Path, member: str, module: str) -> int:
        tested.append((venv, tree, member, module))
        return 3

    monkeypatch.setattr(_verify, "install", _install)
    monkeypatch.setattr(_verify, "run_tests", _test)
    code = _lifecycle.verify(
        package,
        tmp_path / "ws",
        tree=tree,
        version="1.2.0",
        python="3.14",
        index="",
        extras="",
    )
    # The tests' code is the phase's.
    assert code == 3
    venv = tmp_path / "scratch" / ".verify"
    assert installs == [(venv, "3.14", "acme-core==1.2.0", "")]
    assert tested == [(venv, tree, "core", "acme.core")]


# --- the registry probe -----------------------------------------------------------


class _Answer:
    """What a faked ``urlopen`` answers: *body*, as a context manager."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _Answer:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def test_the_registry_probe_reads_the_resolved_index_with_its_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _lifecycle

    root = tmp_path / "ws"
    root.mkdir()
    (root / "workshop.toml").write_text("[workspace]\n")
    package = _package(root)
    requests: list[urllib.request.Request] = []

    def _urlopen(request: urllib.request.Request, timeout: float = 0) -> _Answer:
        requests.append(request)
        return _Answer(b'{"versions": ["1.0.0", "1.1.0"]}')

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    monkeypatch.setenv("PYTHON_REGISTRY_URL", "https://index.test/simple")
    monkeypatch.delenv("PYTHON_PUBLISH_INDEX", raising=False)
    # The fallback first: with no credential the probe reads anonymously.
    monkeypatch.delenv("PYTHON_REGISTRY_TOKEN", raising=False)
    probe = _lifecycle.registry(package)
    assert probe.versions("acme-core") == ("1.0.0", "1.1.0")
    assert requests[-1].full_url == "https://index.test/simple/acme-core/"
    assert requests[-1].get_header("Authorization") is None
    # With the target's credential, the probe sends it.
    monkeypatch.setenv("PYTHON_REGISTRY_TOKEN", "secret")
    _lifecycle.registry(package).versions("acme-core")
    expected = base64.b64encode(b"__token__:secret").decode()
    assert requests[-1].get_header("Authorization") == f"Basic {expected}"


# --- the requirement writers ------------------------------------------------------


def test_a_test_edge_on_a_python_sibling_lands_in_the_test_extra(
    tmp_path: Path,
) -> None:
    from livery.workshop import _lifecycle

    core = _package(tmp_path, "core")
    tool = _package(tmp_path, "tool")
    stranger = _package(tmp_path, "native", listed=())
    # The refusal first: python's writer takes a sibling holding python
    # alone, and nothing is written.
    before = (tool.directory / "pyproject.toml").read_text()
    with pytest.raises(Failed, match="no writer of its set takes a test requirement"):
        _lifecycle.declare_requirement(tool, stranger, "1.0.0", kind="test")
    assert (tool.directory / "pyproject.toml").read_text() == before
    assert _lifecycle.declare_requirement(tool, core, "1.0.0", kind="test") == [
        "pyproject.toml"
    ]
    assert _lifecycle.declared_test_requirements(tool) == {"acme-core": ">=1.0.0"}
    # Carried at the floor already: nothing to write.
    assert _lifecycle.declare_requirement(tool, core, "1.0.0", kind="test") == []
