"""The ty extension: unlisted it does nothing; listed, it checks every platform."""

from __future__ import annotations

import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest

import livery.extensions.ty._extension as declaration
import livery.toolroom.tools.api as tools
from livery.extensions.ty import _checks
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
    """Ty's check registered, as the mount registers a listed extension's."""
    from livery.workshop._extensions import register_declared_checks

    state = registry.snapshot()
    register_declared_checks("ty", declaration)
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
    from livery.footman import registry as footman_registry
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
        assert "typecheck.ty" not in checks_by_name()
        assert "ty" not in {tool for tool, _ in tools_for_kind("python")}
        deliver(root)
        assert not (root / "ty.toml").exists()
        # Listed, the mount registers the check under the listed name,
        # the tool joins the profile, and the sync writes the file and
        # recommends the editor extension.
        (root / "workshop.toml").write_text(CONTRACT + 'extensions = ["ty"]\n')
        with footman_registry.capture():
            assert mount_extensions(root) == ("ty",)
        assert checks_by_name()["typecheck.ty"].extension == "ty"
        assert "ty" in {tool for tool, _ in tools_for_kind("python")}
        deliver(root)
        written = tomllib.loads((root / "ty.toml").read_text())
        assert written["environment"]["python-platform"] == "all"
        recommended = (root / ".vscode" / "extensions.json").read_text()
        assert '"astral-sh.ty"' in recommended
    finally:
        registry.restore(state)


# The check: the configured whole, whatever a run reaches.


def test_a_run_checks_the_configured_whole_whatever_it_reaches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: None
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(_checks, "run_typecheck", lambda: calls.append("whole"))
    member = Package(
        directory=tmp_path / "packages" / "one",
        path="packages/one",
        name="acme-one",
        kind="python",
        depends=(),
    )
    record = registry.check_for("typecheck.ty")
    record.run(GateContext(root=tmp_path, packages=(member,)))
    record.run(GateContext(root=tmp_path, packages=(member,), subset=(member,)))
    assert calls == ["whole", "whole"]


def test_the_check_calls_ty_s_own_check(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []

    class _Ty:
        def check(self) -> None:
            called.append("check")

    monkeypatch.setattr(tools, "ty", _Ty())
    _checks.run_typecheck()
    assert called == ["check"]


# The file: the workshop writes it, and a bare ty reads what the gate reads.


def test_a_workspace_without_python_members_checks_its_tasks_file(
    tmp_path: Path,
) -> None:
    from livery.workshop._shipped_files import deliver

    bare = _workspace(tmp_path / "bare", '["ty"]')
    deliver(bare)
    written = tomllib.loads((bare / "ty.toml").read_text())
    assert written["src"]["include"] == ["tasks.py"]


def test_this_repository_s_configuration_is_what_a_bare_ty_reads() -> None:
    written = tomllib.loads((ROOT / "ty.toml").read_text())
    assert "packages/extensions/ty/src" in written["src"]["include"]
    assert written["environment"]["extra-paths"] == ["typings"]
    assert "[tool.ty]" not in (ROOT / "pyproject.toml").read_text()
