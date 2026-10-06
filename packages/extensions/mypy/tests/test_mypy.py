"""The mypy extension: unlisted it does nothing; listed, it checks every platform."""

from __future__ import annotations

import configparser
from collections.abc import Iterator
from pathlib import Path

import pytest

import livery.extensions.mypy._extension as declaration
import livery.toolroom.tools.api as tools
from livery.extensions.mypy import _checks
from livery.workshop import _checks as registry
from livery.workshop.api import GateContext, Package

ROOT = Path(__file__).resolve().parents[4]

CONTRACT = (
    "[workspace]\n"
    'name = "acme"\n'
    'namespace = "acme"\n'
    'authors = [{ name = "Acme", email = "dev@acme.test" }]\n'
    'copyright-year = "2026"\n'
)


@pytest.fixture
def registered() -> Iterator[None]:
    """Mypy's check registered, as the mount registers a listed extension's."""
    from livery.workshop._extensions import register_declared_checks

    state = registry.snapshot()
    register_declared_checks("mypy", declaration)
    try:
        yield
    finally:
        registry.restore(state)


def _workspace(root: Path, extensions: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "workshop.toml").write_text(CONTRACT + f"extensions = {extensions}\n")
    return root


def _member(root: Path, name: str) -> Package:
    directory = root / "packages" / name
    (directory / "src").mkdir(parents=True)
    (directory / "tests").mkdir()
    return Package(
        directory=directory,
        path=f"packages/{name}",
        name=f"acme-{name}",
        kind="python",
        depends=(),
    )


def _dev_group(slots: dict[str, object]) -> list[str]:
    """The dev group's lines, as the slots compose them."""
    group = slots["python.dev-group"]
    assert isinstance(group, list)
    return [str(line) for line in group]  # pyright: ignore[reportUnknownVariableType]


def _read(path: Path) -> configparser.ConfigParser:
    written = configparser.ConfigParser()
    written.read_string(path.read_text())
    return written


# The refusals first: unlisted, the extension does nothing; a platform
# that fails fails the call.


def test_unlisted_it_registers_no_check_requires_no_tool_and_writes_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.footman import registry as footman_registry
    from livery.workshop._checks import checks_by_name, tools_for_kind
    from livery.workshop._extensions import mount_extensions
    from livery.workshop._shipped_files import deliver
    from livery.workshop._slots import all_composed

    root = _workspace(tmp_path / "ws", "[]")
    monkeypatch.setattr(
        "livery.workshop._extensions.workspace_root", lambda start=None: root
    )
    state = registry.snapshot()
    try:
        with footman_registry.capture():
            assert mount_extensions(root) == ()
        assert "typecheck.mypy" not in checks_by_name()
        assert "mypy" not in {tool for tool, _ in tools_for_kind("python")}
        assert "mypy>=1.14" not in _dev_group(all_composed())
        deliver(root)
        assert not (root / "mypy.ini").exists()
        # Listed, the mount registers the check under the listed name,
        # the tool joins the profile, mypy rides the dev group, and the
        # sync writes the file.
        (root / "workshop.toml").write_text(CONTRACT + 'extensions = ["mypy"]\n')
        with footman_registry.capture():
            assert mount_extensions(root) == ("mypy",)
        assert checks_by_name()["typecheck.mypy"].extension == "mypy"
        assert "mypy" in {tool for tool, _ in tools_for_kind("python")}
        assert "mypy>=1.14" in _dev_group(all_composed())
        deliver(root)
        cache = _read(root / "mypy.ini")["mypy"]["cache_dir"]
        assert cache == ".workshop/.cache/mypy/linux"
    finally:
        registry.restore(state)


def test_a_platform_that_fails_fails_the_call_and_the_others_still_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from livery.footman.api import fail

    ran: list[str] = []

    def mypy(*paths: str, platform: str, cache_dir: str) -> None:
        del paths, cache_dir
        ran.append(platform)
        if platform == "win32":
            fail("mypy --platform win32 exited with code 1")

    monkeypatch.setattr(tools, "mypy", mypy)
    with pytest.raises(BaseException, match="win32"):
        _checks.run_typecheck()
    assert sorted(ran) == ["darwin", "linux", "win32"]


# The check: the workshop names the paths, and every call checks each platform.


def test_every_call_checks_each_platform_with_a_cache_of_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[str, ...], dict[str, str]]] = []
    monkeypatch.setattr(tools, "mypy", lambda *paths, **kw: calls.append((paths, kw)))
    _checks.run_typecheck(("packages/one/src",))
    assert sorted(calls, key=lambda call: call[1]["platform"]) == [
        (("packages/one/src",), {"platform": p, "cache_dir": f"{_checks.CACHE}/{p}"})
        for p in ("darwin", "linux", "win32")
    ]


def test_the_whole_is_a_call_with_no_path_and_a_narrowed_run_names_its_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: None
) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(_checks, "run_typecheck", lambda paths=(): calls.append(paths))
    one = _member(tmp_path, "one")
    two = _member(tmp_path, "two")
    record = registry.check_for("typecheck.mypy")
    # Every member reached: mypy reads the files mypy.ini names.
    record.run(GateContext(root=tmp_path, packages=(one, two)))
    assert calls == [()]
    calls.clear()
    record.run(GateContext(root=tmp_path, packages=(one, two), subset=(one,)))
    assert calls == [("packages/one/src", "packages/one/tests")]


# The file: the workshop writes it, and a bare mypy reads what the gate reads.


def test_the_file_names_the_root_tests_only_while_they_exist(tmp_path: Path) -> None:
    from livery.workshop._shipped_files import deliver

    bare = _workspace(tmp_path / "bare", '["mypy"]')
    deliver(bare)
    written = _read(bare / "mypy.ini")
    assert written["mypy"]["files"].split() == ["tasks.py"]
    assert written["mypy"]["mypy_path"].split() == ["typings"]
    tested = _workspace(tmp_path / "tested", '["mypy"]')
    (tested / "tests").mkdir()
    deliver(tested)
    written = _read(tested / "mypy.ini")
    assert written["mypy"]["files"].split() == ["tests,", "tasks.py"]
    assert written["mypy-acme.*"]["disallow_untyped_defs"] == "True"


def test_this_repository_s_configuration_is_what_a_bare_mypy_reads() -> None:
    written = _read(ROOT / "mypy.ini")
    assert "packages/extensions/mypy/src," in written["mypy"]["mypy_path"].split()
    assert "[tool.mypy]" not in (ROOT / "pyproject.toml").read_text()
    assert ".mypy_cache" not in (ROOT / ".gitignore").read_text()
