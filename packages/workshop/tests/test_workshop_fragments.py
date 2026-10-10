"""Configuration fragments: refusals first, then what the records render."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from livery.workshop import _checks
from livery.workshop._checks import (
    CheckRecord,
    GateContext,
    register_check,
    unregister_check,
)
from livery.workshop._fragments import (
    Fragment,
    compose_package,
    compose_project,
    package_fragment,
)
from livery.workshop._slots import all_composed

_FAILURES = (BaseException,)


@pytest.fixture
def restored_checks():
    before, withdrawn = dict(_checks._CHECKS), dict(_checks._WITHDRAWN)
    yield
    _checks._CHECKS.clear()
    _checks._CHECKS.update(before)
    _checks._WITHDRAWN.clear()
    _checks._WITHDRAWN.update(withdrawn)


def _noop(ctx: GateContext) -> None:
    del ctx


def _data() -> dict[str, object]:
    return {
        "packages": [],
        "py": [],
        "native": [],
        "python_floor": "3.14",
        "namespace_package": "livery",
        "runner_prog": "fm",
        "project_name": "x",
        "kind": "",
        "slots": all_composed(),
    }


# The refusals first.


def test_a_fragment_for_a_file_the_render_does_not_write_refuses(
    restored_checks,
) -> None:
    with pytest.raises(
        _FAILURES, match="'Makefile' names a file the render does not write"
    ):
        register_check(
            CheckRecord("acme", "lint", _noop, fragments=(Fragment("Makefile", "x"),))
        )
    with pytest.raises(
        _FAILURES, match="applies to the whole workspace and names no extension"
    ):
        register_check(
            CheckRecord(
                "acme",
                "lint",
                _noop,
                fragments=(Fragment("pyproject.toml", "x", extensions=("python",)),),
            )
        )
    # A per-package file is one a fragment names extensions for; without
    # them, the file is no project file the render writes.
    with pytest.raises(_FAILURES, match="a fragment that names extensions is rendered"):
        register_check(
            CheckRecord(
                "acme", "lint", _noop, fragments=(Fragment(".clang-tidy", "x"),)
            )
        )


def _native_style() -> CheckRecord:
    """A base check carrying a per-package file for each C or C++ package."""
    record = CheckRecord(
        "native-style",
        "lint",
        _noop,
        scope="package",
        extensions=("cpp",),
        fragments=(
            Fragment(
                ".native-style", "# for a C or C++ package\n", extensions=("cpp",)
            ),
        ),
    )
    register_check(record)
    return record


def test_a_withdrawn_checks_file_is_kept_when_edited_and_removed_when_unedited(
    tmp_path: Path, restored_checks, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._fragment_engine import read_rendered
    from livery.workshop._shipped_files import deliver, shipped_drift

    record = _native_style()
    root = tmp_path / "ws"
    member = root / "packages" / "native"
    member.mkdir(parents=True)
    (root / "workshop.toml").write_text("[workspace]\nextensions = []\n")
    (member / "workshop.toml").write_text('kind = "cpp-conan"\nname = "acme-native"\n')
    monkeypatch.setattr(
        "livery.workshop._extensions.workspace_root", lambda start=None: root
    )
    # The engine writes the file and its receipt in the package.
    assert "  wrote packages/native/.native-style" in deliver(root)
    assert ".native-style" in read_rendered(member)
    assert "C or C++" in (member / ".native-style").read_text()
    assert deliver(root) == []
    # The check withdrawn: the edited arm first, kept and named.
    unregister_check("lint.native-style", by="acme.brand")
    (member / ".native-style").write_text("mine\n")
    # The gate's prose follows the withdrawn check too; the package's
    # lines are the ones under test.
    assert [line for line in deliver(root) if "packages/" in line] == [
        "  kept packages/native/.native-style: no listed extension renders it,"
        " and it was edited here, so it stays as the repository's own"
    ]
    assert (member / ".native-style").is_file()
    assert ".native-style" not in read_rendered(member)
    # The unedited arm: removed by the next delivery, and no drift after.
    (member / ".native-style").unlink()
    register_check(record)
    deliver(root)
    unregister_check("lint.native-style", by="acme.brand")
    assert [line for line in deliver(root) if "packages/" in line] == [
        "  removed packages/native/.native-style: no listed extension renders it"
    ]
    assert not (member / ".native-style").is_file()
    assert shipped_drift(root) == []


def test_an_unreceipted_copy_is_adopted_when_equal_and_kept_when_not(
    tmp_path: Path, restored_checks: None
) -> None:
    from livery.workshop._fragment_engine import read_rendered
    from livery.workshop._shipped_files import settle_package

    # A package file is any file a registered check's fragment for
    # extensions names: a native check's, and an extension's style beside it.
    _native_style()
    register_check(
        CheckRecord(
            "acme-style",
            "format",
            _noop,
            scope="package",
            extensions=("cpp",),
            fragments=(Fragment(".acme-style", "acme style\n", extensions=("cpp",)),),
        )
    )
    member = tmp_path / "packages" / "native"
    member.mkdir(parents=True)
    held = ("cmake", "conan", "cpp")
    rendered = compose_package(held, ".acme-style", _data())
    assert rendered == "acme style\n"
    (member / ".acme-style").write_bytes(rendered.encode())
    other = member / ".native-style"
    other.write_text("mine\n")
    assert settle_package(member, held) == [
        "  kept .native-style: edited here, so it is not rewritten; delete it to"
        " take the rendered file"
    ]
    assert ".acme-style" in read_rendered(member)  # adopted
    assert other.read_text() == "mine\n"


# Then what the records render.


def test_this_workspace_composes_no_tool_table_from_the_base_s_records() -> None:
    from livery.workshop._identity import project_facts
    from livery.workshop._templates import render_injections

    root = Path(__file__).resolve().parents[3]
    injected = render_injections(root, project_facts(root))
    # Each tool's configuration is a file of its extension's own, and no
    # extension is mounted here: the base's records add no table to the
    # project file and recommend no editor extension.
    assert injected["fragments"]["pyproject.toml"] == ""
    assert injected["extensions"] == []


def test_unregistering_a_tools_checks_removes_every_trace(restored_checks) -> None:
    from livery.workshop._checks import editor_extensions, tools_for

    register_check(
        CheckRecord(
            "bystander",
            "lint",
            _noop,
            fragments=(Fragment("pyproject.toml", "[tool.bystander]\nx = 1\n"),),
        )
    )
    for role in ("typecheck", "typecomplete"):
        register_check(
            CheckRecord(
                "acme",
                role,
                _noop,
                extensions=("python",),
                tools=("acme",),
                fragments=(Fragment("pyproject.toml", "[tool.acme]\nstrict = true\n"),)
                if role == "typecheck"
                else (),
                editor_extension="acme.checker",
            )
        )
    composed = compose_project(_data())
    assert "[tool.acme]" in composed["pyproject.toml"]
    assert "acme.checker" in editor_extensions()
    unregister_check("typecheck.acme", by="acme.brand")
    unregister_check("typecomplete.acme", by="acme.brand")
    composed = compose_project(_data())
    assert "[tool.acme" not in composed["pyproject.toml"]
    assert "acme.checker" not in editor_extensions()
    assert "acme" not in {tool for tool, _ in tools_for(("python",))}
    assert "[tool.bystander]" in composed["pyproject.toml"]


def test_a_package_fragment_resolves_by_the_extensions_a_package_holds(
    restored_checks,
) -> None:
    record = _native_style()
    data = _data()
    native = compose_package(("cmake", "conan", "cpp"), ".native-style", data)
    nano = compose_package(("python", "cpp"), ".native-style", data)
    assert native == nano == "# for a C or C++ package\n"
    assert package_fragment(("python",), ".native-style") is None
    # Two checks carrying one file for what one package holds: the first
    # by name renders it there, and the other wherever it alone applies.
    register_check(
        CheckRecord(
            "acme-tidy",
            "lint",
            _noop,
            scope="package",
            extensions=("cmake",),
            extension="acme.brand",
            fragments=(
                Fragment(".native-style", "Checks: acme-*\n", extensions=("cmake",)),
            ),
        )
    )
    found = package_fragment(("cmake", "conan", "cpp"), ".native-style")
    assert found is not None and found[1] == "lint.acme-tidy"
    assert package_fragment(("python", "cpp"), ".native-style") == (
        record.fragments[0].text,
        "lint.native-style",
    )


def test_the_extension_ids_come_from_the_records_and_are_well_formed() -> None:
    import re

    from livery.workshop._checks import checks_by_name, editor_extensions

    carried = {
        record.editor_extension
        for record in checks_by_name().values()
        if record.editor_extension
    }
    assert set(editor_extensions()) == carried
    assert all(
        re.fullmatch(r"[a-z0-9-]+\.[a-z0-9-]+", name) for name in editor_extensions()
    )
    assert json.dumps(list(editor_extensions()))  # what the rendered list carries
