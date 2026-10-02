"""Configuration fragments: refusals first, then what the records render."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from livery.workshop import _checks, _fragments
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
        "docs_site_url": "",
        "runner_prog": "fm",
        "template_source_label": "t",
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
        _FAILURES, match="applies to the whole workspace and names no kind"
    ):
        register_check(
            CheckRecord(
                "acme",
                "lint",
                _noop,
                fragments=(Fragment("pyproject.toml", "x", kind="python"),),
            )
        )
    with pytest.raises(_FAILURES, match="rendered per package and names the kind"):
        register_check(
            CheckRecord(
                "acme", "lint", _noop, fragments=(Fragment(".clang-tidy", "x"),)
            )
        )


def test_a_hand_edited_section_is_drift(tmp_path: Path) -> None:
    from livery.workshop._templates import _drift_line

    rendered = b"[tool.ruff]\nline-length = 88\n"
    (line,) = _drift_line(
        "pyproject.toml", b"[tool.ruff]\nline-length = 100\n", rendered
    )
    assert line.startswith("pyproject.toml: differs from its render")


def test_a_withdrawn_checks_file_is_kept_when_edited_and_removed_when_unedited(
    tmp_path: Path, restored_checks
) -> None:
    from livery.workshop._templates import (
        judge_fragment_file,
        read_rendered,
        settle_fragment_file,
    )

    data = {**_data(), "kind": "cpp-conan"}
    member = tmp_path / "packages" / "native"
    member.mkdir(parents=True)
    # The render writes the file and its receipt.
    assert settle_fragment_file(member, ".clang-tidy", data) == "rendered: .clang-tidy"
    assert ".clang-tidy" in read_rendered(member)
    assert settle_fragment_file(member, ".clang-tidy", data) == ""
    # The check withdrawn: the edited arm first, kept and named.
    unregister_check("lint.clang-tidy", by="acme.brand")
    (member / ".clang-tidy").write_text("Checks: mine\n")
    assert (
        judge_fragment_file(member, ".clang-tidy", data, "packages/native/.clang-tidy")
        == []
    )
    assert settle_fragment_file(member, ".clang-tidy", data).startswith(
        "kept: .clang-tidy"
    )
    assert (member / ".clang-tidy").is_file()
    # The unedited arm: stale, named as drift, removed by the apply.
    register_check(_checks._CHECKS.get("lint.clang-tidy") or _restore_clang_tidy())
    settle_fragment_file(member, ".clang-tidy", data)
    unregister_check("lint.clang-tidy", by="acme.brand")
    (line,) = judge_fragment_file(
        member, ".clang-tidy", data, "packages/native/.clang-tidy"
    )
    assert "written for a check that is withdrawn" in line
    assert settle_fragment_file(member, ".clang-tidy", data).startswith(
        "removed: .clang-tidy"
    )
    assert not (member / ".clang-tidy").is_file()
    assert ".clang-tidy" not in read_rendered(member)


def _restore_clang_tidy() -> CheckRecord:
    from livery.workshop._checks import _register_builtin

    _register_builtin()
    return _checks._CHECKS["lint.clang-tidy"]


def test_an_unreceipted_copy_is_adopted_when_equal_and_kept_when_not(
    tmp_path: Path,
) -> None:
    from livery.workshop._templates import read_rendered, settle_fragment_file

    data = {**_data(), "kind": "cpp-conan"}
    member = tmp_path / "packages" / "native"
    member.mkdir(parents=True)
    rendered = compose_package("cpp-conan", ".clang-format", data)
    assert rendered is not None
    (member / ".clang-format").write_text(rendered)
    assert settle_fragment_file(member, ".clang-format", data) == ""
    assert ".clang-format" in read_rendered(member)  # adopted
    other = member / ".clang-tidy"
    other.write_text("Checks: mine\n")
    assert settle_fragment_file(member, ".clang-tidy", data).startswith(
        "kept: .clang-tidy (a local override"
    )
    assert other.read_text() == "Checks: mine\n"


# Then what the records render.


def test_this_workspace_composes_its_tool_tables_from_the_records() -> None:
    from livery.workshop._templates import read_answers, render_injections

    root = Path(__file__).resolve().parents[3]
    injected = render_injections(root, read_answers(root / ".copier-answers.yml"))
    composed = injected["fragments"]["pyproject.toml"]
    for table in (
        "[tool.ruff]",
        "[tool.ruff.lint]",
        "[tool.basedpyright]",
        "[tool.mypy]",
        "[tool.pytest.ini_options]",
    ):
        assert table in composed
    assert "packages/workshop/src" in composed  # the roster reaches the fragments
    template = (
        root
        / "packages/workshop/src/livery/workshop/templates/project/pyproject.toml.jinja"
    ).read_text()
    assert "[tool.ruff]" not in template and "[tool.pytest.ini_options]" not in template
    assert injected["extensions"] == ["charliermarsh.ruff", "detachedfork.basedpyright"]


def test_unregistering_the_ruff_checks_removes_every_trace(restored_checks) -> None:
    from livery.workshop._checks import editor_extensions, tools_for_kind

    unregister_check("format.ruff", by="acme.brand")
    unregister_check("lint.ruff", by="acme.brand")
    composed = compose_project(_data())
    assert "[tool.ruff" not in composed["pyproject.toml"]
    assert "ruff" not in composed[".vscode/settings.json"]
    assert "charliermarsh.ruff" not in editor_extensions()
    assert "ruff" not in {tool for tool, _ in tools_for_kind("python")}
    assert "[tool.basedpyright]" in composed["pyproject.toml"]


def test_a_native_fragment_resolves_down_the_kind_chain(restored_checks) -> None:
    data = _data()
    cpp = compose_package("cpp-conan", ".clang-tidy", {**data, "kind": "cpp-conan"})
    nano = compose_package(
        "python-nanobind", ".clang-tidy", {**data, "kind": "python-nanobind"}
    )
    assert cpp is not None and nano is not None
    assert "for the cpp-conan kind" in cpp and "for the python-nanobind kind" in nano
    assert package_fragment("python", ".clang-tidy") is None
    # An extension's fragment for one kind differs from the other kind's.
    register_check(
        CheckRecord(
            "acme-tidy",
            "lint",
            _noop,
            scope="package",
            kinds=("python-nanobind",),
            extension="acme.brand",
            fragments=(
                Fragment(".clang-tidy", "Checks: acme-*\n", kind="python-nanobind"),
            ),
        )
    )
    found = package_fragment("python-nanobind", ".clang-tidy")
    assert found is not None and found[1] == "lint.acme-tidy"
    assert package_fragment("cpp-conan", ".clang-tidy") == (
        _fragments.CLANG_TIDY,
        "lint.clang-tidy",
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
