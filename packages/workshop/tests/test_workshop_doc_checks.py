"""The docs extension's checks: the refusals first, then this workspace's verdict."""

from __future__ import annotations

from pathlib import Path

import pytest

# Importing the task module registers the checks, as the mount does.
import livery.extensions.docs._tasks  # noqa: F401
from livery.extensions.docs._checks import (
    assignment_docstrings,
    doclinks_run,
    docstrings_run,
    link_problems,
    undocumented_exports,
)
from livery.footman import Failed
from livery.workshop._checks import GateContext, checks_by_name
from livery.workshop._packages import Package

ROOT = Path(__file__).resolve().parents[3]


def _member(root: Path, init: str, *, name: str = "thing") -> Path:
    member = root / "packages" / name
    package = member / "src" / "acme" / name
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(init)
    return member


def test_a_broken_link_and_a_dead_anchor_are_named(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "index.md").write_text(
        "# Home\n\n[gone](missing.md)\n[bad](other.md#nowhere)\n"
    )
    (docs / "other.md").write_text("# Other\n\n## A real heading\n")
    problems = link_problems(tmp_path)
    assert "docs/index.md: missing.md does not exist" in problems
    assert "docs/index.md: other.md#nowhere anchors nothing" in problems
    ctx = GateContext(root=tmp_path, packages=())
    with pytest.raises(Failed, match="links that resolve nothing"):
        doclinks_run(ctx)
    (docs / "index.md").write_text("# Home\n\n[good](other.md#a-real-heading)\n")
    assert link_problems(tmp_path) == []
    doclinks_run(ctx)


def test_a_mounted_link_maps_to_its_package_and_a_built_one_is_skipped(
    tmp_path: Path,
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    member_docs = tmp_path / "packages" / "core" / "docs"
    member_docs.mkdir(parents=True)
    (member_docs / "guide.md").write_text("# Guide\n")
    (docs / "index.md").write_text(
        "# Home\n\n[guide](packages/core/guide.md)\n"
        "[api](_generated/api/index.md)\n"
        "[gone](packages/core/absent.md)\n"
    )
    # A page the build writes under the root docs is not authored.
    (docs / "tasks").mkdir()
    (docs / "tasks" / "index.md").write_text("[nothing](nowhere.md)\n")
    assert link_problems(tmp_path) == [
        "docs/index.md: packages/core/absent.md does not exist"
    ]


def test_an_undocumented_assignment_is_seen_as_such(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text(
        'A: int = 1\n"""Documented."""\n\nB = 2\n"""Also documented."""\n\nC = 3\n'
    )
    assert assignment_docstrings(src) == {"A", "B"}


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
    package = Package(
        directory=member,
        path="packages/thing",
        name="acme-thing",
        kind="python",
        depends=(),
    )
    ctx = GateContext(root=tmp_path, packages=(package,))
    with pytest.raises(Failed, match=r"exports without a docstring: acme\.thing\.bare"):
        docstrings_run(ctx)
    # A scoped gate judges its subset alone.
    docstrings_run(GateContext(root=tmp_path, packages=(package,), subset=()))


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


def test_the_checks_are_registered_under_the_lint_role() -> None:
    registered = checks_by_name()
    for name in ("lint.doclinks", "lint.docstrings"):
        assert registered[name].extension == "livery.extensions.docs"


def test_this_workspaces_links_resolve() -> None:
    assert link_problems(ROOT) == []
