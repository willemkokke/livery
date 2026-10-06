"""The basedpyright extension: unlisted it does nothing; listed, it type-checks."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

import livery.extensions.basedpyright._extension as declaration
import livery.toolroom.tools.api as tools
from livery.extensions.basedpyright import _checks
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
    """Both checks registered, as the mount registers ``basedpyright[typecomplete]``."""
    from livery.workshop._extensions import register_declared_checks

    state = registry.snapshot()
    register_declared_checks("basedpyright", declaration, ("typecomplete",))
    try:
        yield
    finally:
        registry.restore(state)


def _workspace(root: Path, extensions: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "workshop.toml").write_text(CONTRACT + f"extensions = {extensions}\n")
    return root


def _member(root: Path, name: str, *, checks: str = "") -> Package:
    """A python member whose namespace root's api declares its public names."""
    directory = root / "packages" / name
    source = directory / "src" / "acme" / name
    source.mkdir(parents=True)
    (directory / "tests").mkdir()
    (source / "api.py").write_text('__all__ = ["VALUE"]\nVALUE = 1\n')
    (source / "py.typed").write_text("")
    turned_off = (("enabled", False),) if checks else ()
    return Package(
        directory=directory,
        path=f"packages/{name}",
        name=f"acme-{name}",
        kind="python",
        depends=(),
        checks=((checks, turned_off),) if checks else (),
    )


def _settings(text: str) -> dict[str, object]:
    """``pyrightconfig.json`` as basedpyright reads it: comments and trailing commas."""
    lines = [line for line in text.splitlines() if not line.lstrip().startswith("//")]
    read: dict[str, object] = json.loads(
        re.sub(r",(\s*[}\]])", r"\1", "\n".join(lines))
    )
    return read


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
        assert "typecheck.basedpyright" not in checks_by_name()
        assert "basedpyright" not in {tool for tool, _ in tools_for_kind("python")}
        deliver(root)
        assert not (root / "pyrightconfig.json").exists()
        # Listed without its option, it type-checks and verifies nothing.
        (root / "workshop.toml").write_text(
            CONTRACT + 'extensions = ["basedpyright"]\n'
        )
        with footman_registry.capture():
            assert mount_extensions(root) == ("basedpyright",)
        checks = checks_by_name()
        assert checks["typecheck.basedpyright"].extension == "basedpyright"
        assert "typecomplete.basedpyright" not in checks
        assert "basedpyright" in {tool for tool, _ in tools_for_kind("python")}
        deliver(root)
        written = _settings((root / "pyrightconfig.json").read_text())
        assert written["typeCheckingMode"] == "standard"
        recommended = (root / ".vscode" / "extensions.json").read_text()
        assert '"detachhead.basedpyright"' in recommended
        # The editor answers with the checker this file configures alone.
        settings = (root / ".vscode" / "settings.json").read_text()
        assert '"python.languageServer": "None"' in settings
        # Listed with it, the type-completeness check joins, under the
        # listed name.
        (root / "workshop.toml").write_text(
            CONTRACT + 'extensions = ["basedpyright[typecomplete]"]\n'
        )
        with footman_registry.capture():
            mount_extensions(root)
        assert checks_by_name()["typecomplete.basedpyright"].extension == "basedpyright"
    finally:
        registry.restore(state)


# The checks: the workshop names the paths and the members, a body calls the tool.


def test_the_whole_is_a_call_with_no_path_and_a_narrowed_run_names_its_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: None
) -> None:
    calls: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    monkeypatch.setattr(
        _checks,
        "run_typecheck",
        lambda paths=(), arguments=(): calls.append((paths, arguments)),
    )
    one = _member(tmp_path, "one")
    two = _member(tmp_path, "two")
    record = registry.check_for("typecheck.basedpyright")
    # Every member reached: basedpyright reads its configured include,
    # which a `.` would replace with every file under the root.
    record.run(GateContext(root=tmp_path, packages=(one, two)))
    assert calls == [((), ())]
    # One member of two: its own directories, in one call.
    calls.clear()
    record.run(GateContext(root=tmp_path, packages=(one, two), subset=(one,)))
    assert calls == [(("packages/one/src", "packages/one/tests"), ())]
    # The words after -- on the check's own verb reach both calls.
    calls.clear()
    words = ("--level", "error")
    record.run(GateContext(root=tmp_path, packages=(one, two), arguments=words))
    record.run(
        GateContext(root=tmp_path, packages=(one, two), subset=(one,), arguments=words)
    )
    assert calls == [((), words), (("packages/one/src", "packages/one/tests"), words)]


def test_type_completeness_verifies_each_judged_member_s_public_modules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    registered: None,
) -> None:
    from livery.workshop._coverage_store import workspace_suite

    verified: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    monkeypatch.setattr(
        _checks,
        "run_typecomplete",
        lambda modules, arguments: verified.append((modules, arguments)),
    )
    # A member that turned the check off is skipped by name, and the
    # workspace's own tests are no member: neither is verified.
    off = _member(tmp_path, "off", checks="checks.basedpyright.typecomplete")
    one = _member(tmp_path, "one")
    (tmp_path / "tests").mkdir()
    unit = workspace_suite(tmp_path)
    assert unit is not None
    record = registry.check_for("typecomplete.basedpyright")
    record.run(GateContext(root=tmp_path, packages=(off, one, unit)))
    assert verified == [(("acme.one.api",), ())]
    assert (
        "typecomplete.basedpyright: packages/off skips (turned off in"
        " packages/off/workshop.toml)" in capsys.readouterr().out
    )
    # The words after -- on the check's own verb reach each verification.
    verified.clear()
    record.run(GateContext(root=tmp_path, packages=(one,), arguments=("--outputjson",)))
    assert verified == [(("acme.one.api",), ("--outputjson",))]


def test_each_public_module_is_verified_with_its_dependencies_external(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        tools, "basedpyright", lambda *words, **kw: calls.append((words, kw))
    )
    _checks.run_typecomplete(())
    assert calls == []
    _checks.run_typecomplete(("acme.one.api", "acme.two"))
    assert calls == [
        ((), {"verifytypes": "acme.one.api", "ignoreexternal": True}),
        ((), {"verifytypes": "acme.two", "ignoreexternal": True}),
    ]
    calls.clear()
    _checks.run_typecomplete(("acme.one.api",), ("--outputjson",))
    assert calls == [
        (("--outputjson",), {"verifytypes": "acme.one.api", "ignoreexternal": True})
    ]


def test_warnings_gate_as_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        tools, "basedpyright", lambda *paths, **kw: calls.append((paths, kw))
    )
    _checks.run_typecheck()
    _checks.run_typecheck(("packages/one/src",))
    _checks.run_typecheck(("packages/one/src",), ("--level", "error"))
    assert calls == [
        ((), {"warnings": True}),
        (("packages/one/src",), {"warnings": True}),
        (("--level", "error", "packages/one/src"), {"warnings": True}),
    ]


# The file: the workshop writes it, and a bare basedpyright reads what the gate reads.


def test_the_file_names_the_root_tests_only_while_they_exist_and_skips_natives(
    tmp_path: Path,
) -> None:
    from livery.workshop._shipped_files import deliver

    bare = _workspace(tmp_path / "bare", '["basedpyright"]')
    deliver(bare)
    assert _settings((bare / "pyrightconfig.json").read_text())["include"] == [
        "tasks.py"
    ]
    tested = _workspace(tmp_path / "tested", '["basedpyright"]')
    (tested / "tests").mkdir()
    native = tested / "packages" / "native"
    native.mkdir(parents=True)
    (native / "workshop.toml").write_text('kind = "cpp-conan"\nname = "acme-native"\n')
    deliver(tested)
    written = _settings((tested / "pyrightconfig.json").read_text())
    assert written["include"] == ["packages", "tests", "tasks.py"]
    assert written["exclude"] == [
        "packages/native",
        "packages/**/conanfile.py",
        "packages/**/docs/examples",
    ]


def test_this_repository_s_configuration_is_what_a_bare_basedpyright_reads() -> None:
    text = (ROOT / "pyrightconfig.json").read_text()
    written = _settings(text)
    assert written["venv"] == ".venv"
    # The repository's own settings sit in the region, after the
    # rendered keys, so a key there wins over the same key above.
    region = text.index("-- workshop: region settings")
    assert text.index('"executionEnvironments"') > region
    assert text.index('"typeCheckingMode"') < region
    assert "[tool.basedpyright]" not in (ROOT / "pyproject.toml").read_text()
