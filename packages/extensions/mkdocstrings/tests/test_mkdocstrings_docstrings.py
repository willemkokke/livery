"""The API reference's docstring step: every export the reference publishes has one."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from livery.extensions.mkdocstrings._docstrings import (
    assignment_docstrings,
    docstrings_run,
    undocumented_exports,
)
from livery.footman import Failed
from livery.workshop import Package, PhaseContext
from livery.workshop import _steps as registry
from livery.workshop._steps import steps_by_name


@pytest.fixture(autouse=True)
def registered() -> Iterator[None]:
    """The reference's steps registered, as the mount registers a listed one's."""
    from livery.workshop._extensions import declaration, register_declared

    state = registry.snapshot()
    found = declaration("mkdocstrings")
    assert found is not None
    register_declared("mkdocstrings", found.additions)
    try:
        yield
    finally:
        registry.restore(state)


def _member(root: Path, init: str, *, name: str = "thing") -> Path:
    member = root / "packages" / name
    package = member / "src" / "acme" / name
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(init)
    return member


def _package(member: Path, *extensions: str) -> Package:
    """*member* as a package listing *extensions*, python when none is named."""
    return Package(
        directory=member,
        path=f"packages/{member.name}",
        name=f"acme-{member.name}",
        depends=(),
        extensions=extensions or ("python",),
    )


def test_an_undocumented_assignment_is_seen_as_such(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text(
        'A: int = 1\n"""Documented."""\n\nB = 2\n"""Also documented."""\n\nC = 3\n'
    )
    assert assignment_docstrings(src) == {"A", "B"}


def test_a_string_under_a_statement_that_binds_no_name_documents_nothing(
    tmp_path: Path,
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text(
        'import os\n"""Under an import."""\n\n'
        'os.sep: str = "/"\n"""Under an attribute."""\n\n'
        'os.name = "posix"\n"""Under another attribute."""\n'
    )
    assert assignment_docstrings(src) == set()


def test_an_undocumented_export_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    member = _member(
        tmp_path,
        '__all__ = ["documented", "bare", "ALIAS"]\n\n'
        'ALIAS = int\n"""An alias, documented under its assignment."""\n\n\n'
        'def documented() -> None:\n    """Say something."""\n\n\n'
        "def bare() -> None:\n    pass\n",
    )
    monkeypatch.setenv("PYTHONPATH", str(member / "src"))
    assert undocumented_exports(tmp_path, [member]) == ["acme.thing.bare"]
    package = _package(member)
    ctx = PhaseContext(root=tmp_path, packages=(package,))
    with pytest.raises(Failed, match=r"exports without a docstring: acme\.thing\.bare"):
        docstrings_run(ctx)
    # A scoped gate judges its subset alone.
    docstrings_run(PhaseContext(root=tmp_path, packages=(package,), subset=()))


def test_an_undocumented_export_of_each_root_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A wheel shipping a second root, a package under a namespace beside
    # the first, publishes the second's exports as well.
    member = _member(
        tmp_path, '__all__ = ["bare"]\n\n\ndef bare() -> None:\n    pass\n'
    )
    second = member / "src" / "acme" / "extensions" / "site"
    second.mkdir(parents=True)
    (second / "__init__.py").write_text(
        '__all__ = ["bare", "documented"]\n\n\n'
        'def documented() -> None:\n    """Say something."""\n\n\n'
        "def bare() -> None:\n    pass\n"
    )
    # A package under a root is that root's, never a root of its own.
    (member / "src" / "acme" / "thing" / "sub").mkdir()
    (member / "src" / "acme" / "thing" / "sub" / "__init__.py").write_text(
        '__all__ = ["bare"]\n\n\ndef bare() -> None:\n    pass\n'
    )
    monkeypatch.setenv("PYTHONPATH", str(member / "src"))
    assert undocumented_exports(tmp_path, [member]) == [
        "acme.thing.bare",
        "acme.extensions.site.bare",
    ]


def test_a_member_that_does_not_import_refuses_naming_the_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    member = _member(tmp_path, "raise RuntimeError('no import here')\n")
    monkeypatch.setenv("PYTHONPATH", str(member / "src"))
    with pytest.raises(Failed, match="no import here"):
        undocumented_exports(tmp_path, [member])


def test_a_member_that_is_not_installed_is_named_and_passed_over(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    member = _member(
        tmp_path, '__all__ = ["bare"]\n\n\ndef bare() -> None:\n    pass\n'
    )
    assert undocumented_exports(tmp_path, [member]) == []
    assert "acme.thing skips (not installed;" in capsys.readouterr().out


def test_a_member_without_a_package_is_passed_over(tmp_path: Path) -> None:
    member = tmp_path / "packages" / "native"
    (member / "src").mkdir(parents=True)
    (member / "src" / "geometry.cpp").write_text("int x;\n")
    assert undocumented_exports(tmp_path, [member]) == []


def test_a_member_without_a_source_tree_is_passed_over(tmp_path: Path) -> None:
    member = tmp_path / "packages" / "docs-only"
    (member / "docs").mkdir(parents=True)
    assert undocumented_exports(tmp_path, [member]) == []


def test_a_package_of_another_language_is_never_imported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A C++ package's set holds no python, so the step names it and
    # leaves it out; its python-shaped source is never imported.
    member = _member(tmp_path, "raise RuntimeError('imported')\n", name="native")
    monkeypatch.setenv("PYTHONPATH", str(member / "src"))
    docstrings_run(
        PhaseContext(root=tmp_path, packages=(_package(member, "cmake", "conan"),))
    )
    assert (
        "lint.docstrings: packages/native skips (not a python package)"
        in capsys.readouterr().out
    )


def test_the_steps_are_the_reference_s() -> None:
    registered = steps_by_name()
    for name in ("docs.mkdocstrings", "lint.docrefs", "lint.docstrings"):
        assert registered[name].extension == "mkdocstrings"
    assert registered["docs.mkdocstrings"].scope == "package"
    assert registered["docs.mkdocstrings"].extensions == ("python",)
    assert registered["lint.docstrings"].extensions == ("python",)
