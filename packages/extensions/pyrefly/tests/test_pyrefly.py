"""The pyrefly extension: unlisted it does nothing; listed, it checks every platform."""

from __future__ import annotations

import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest

import livery.toolroom.tools as tools
from livery.extensions.pyrefly import _checks
from livery.workshop import GateContext, Package
from livery.workshop import _checks as registry

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
    """Pyrefly's check registered, as the mount registers a listed extension's."""
    from livery.workshop._extensions import declaration, register_declared

    state = registry.snapshot()
    found = declaration("pyrefly")
    assert found is not None
    register_declared("pyrefly", found.additions)
    try:
        yield
    finally:
        registry.restore(state)


def _workspace(root: Path, extensions: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "workshop.toml").write_text(CONTRACT + f"extensions = {extensions}\n")
    return root


# The refusal first: installed and not listed, the extension does nothing.


def test_unlisted_it_registers_no_check_requires_no_tool_and_writes_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.footman import _registry as footman_registry
    from livery.workshop._checks import checks_by_name, tools_for_kind
    from livery.workshop._extensions import mount_extensions
    from livery.workshop._shipped_files import deliver

    root = _workspace(tmp_path / "ws", "[]")
    monkeypatch.setattr(
        "livery.workshop._extensions.workspace_root", lambda start=None: root
    )
    state = registry.snapshot()
    try:
        with footman_registry.capture():
            assert mount_extensions(root) == ()
        assert "typecheck.pyrefly" not in checks_by_name()
        assert "pyrefly" not in {tool for tool, _ in tools_for_kind("python")}
        deliver(root)
        assert not (root / "pyrefly.toml").exists()
        # Listed, the mount registers the check under the listed name,
        # the tool joins the profile, and the sync writes the file.
        (root / "workshop.toml").write_text(CONTRACT + 'extensions = ["pyrefly"]\n')
        with footman_registry.capture():
            assert mount_extensions(root) == ("pyrefly",)
        assert checks_by_name()["typecheck.pyrefly"].extension == "pyrefly"
        assert "pyrefly" in {tool for tool, _ in tools_for_kind("python")}
        deliver(root)
        written = tomllib.loads((root / "pyrefly.toml").read_text())
        assert written["errors"]["deprecated"] == "error"
    finally:
        registry.restore(state)


# The check: the configured whole, whatever a run reaches.


def test_a_run_checks_the_configured_whole_whatever_it_reaches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: None
) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(_checks, "run_typecheck", calls.append)
    member = Package(
        directory=tmp_path / "packages" / "one",
        path="packages/one",
        name="acme-one",
        kind="python",
        depends=(),
    )
    record = registry.check_for("typecheck.pyrefly")
    record.run(GateContext(root=tmp_path, packages=(member,)))
    record.run(GateContext(root=tmp_path, packages=(member,), subset=(member,)))
    # The words after -- on the check's own verb reach pyrefly.
    words = ("--summarize-errors",)
    record.run(GateContext(root=tmp_path, packages=(member,), arguments=words))
    assert calls == [(), (), words]


def test_the_check_calls_pyrefly_check(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[tuple[str, ...]] = []
    monkeypatch.setattr(tools, "pyrefly", lambda *args: called.append(args))
    _checks.run_typecheck()
    _checks.run_typecheck(("--summarize-errors",))
    assert called == [("check",), ("check", "--summarize-errors")]


# The file: the workshop writes it, and a bare pyrefly reads what the gate reads.


def test_a_workspace_without_python_members_checks_its_tasks_file(
    tmp_path: Path,
) -> None:
    from livery.workshop._shipped_files import deliver

    bare = _workspace(tmp_path / "bare", '["pyrefly"]')
    deliver(bare)
    written = tomllib.loads((bare / "pyrefly.toml").read_text())
    assert written["project-includes"] == ["tasks.py"]


def test_this_repository_s_configuration_is_what_a_bare_pyrefly_reads() -> None:
    written = tomllib.loads((ROOT / "pyrefly.toml").read_text())
    assert "packages/extensions/pyrefly/src" in written["project-includes"]
    assert written["search-path"] == ["typings"]
    assert "[tool.pyrefly]" not in (ROOT / "pyproject.toml").read_text()
