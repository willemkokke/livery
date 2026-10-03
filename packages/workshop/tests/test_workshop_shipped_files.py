"""The files the listed extensions ship, composed by the engine on sync."""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.footman.context import Failed
from livery.workshop import _fragment_engine as engine
from livery.workshop._fragment_engine import Fragment
from livery.workshop._shipped_files import deliver, shipped_drift

_REGION = (
    "# -- workshop: region rules, yours to edit; the render keeps it --\n"
    "# -- workshop: end rules --\n"
)


def _workspace(root: Path, extensions: str) -> Path:
    root.mkdir(exist_ok=True)
    (root / "workshop.toml").write_text(f"[workspace]\nextensions = {extensions}\n")
    return root


def test_the_repositorys_region_comes_after_every_extensions_lines(
    tmp_path: Path,
) -> None:
    base = Fragment("livery.workshop", "i", ".gitignore", f"dist/\n{_REGION}")
    docs = Fragment("docs", "i", ".gitignore", "site/\ndist/\n")
    (out,) = engine.plan(tmp_path, (base, docs), ("livery.workshop", "docs"), {})
    assert out.body.decode() == f"dist/\nsite/\n{_REGION}"


def test_a_withdrawn_extensions_lines_leave_and_the_region_stays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path / "ws", '["docs"]')
    monkeypatch.setattr(
        "livery.workshop._extensions.workspace_root", lambda start=None: root
    )
    assert deliver(root) == ["  wrote .gitattributes", "  wrote .gitignore"]
    ignored = (root / ".gitignore").read_text()
    assert "site/" in ignored and ignored.index("dist/") < ignored.index("site/")
    edited = ignored.replace(
        "the render keeps it --\n", "the render keeps it --\nmine/\n"
    )
    (root / ".gitignore").write_text(edited)
    assert shipped_drift(root) == []
    # The docs extension leaves the list: its lines go, the repository's
    # line stays, and the file is still the engine's to rewrite.
    _workspace(root, "[]")
    assert deliver(root) == ["  updated .gitignore"]
    ignored = (root / ".gitignore").read_text()
    assert "site/" not in ignored
    assert "mine/\n# -- workshop: end rules --" in ignored


def test_the_template_check_names_a_composed_files_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._templates import template_check

    root = _workspace(tmp_path / "ws", "[]")
    monkeypatch.setattr("livery.workshop._templates._root", lambda: root)
    with pytest.raises(Failed) as refused:
        template_check()
    assert "  .gitignore: missing; `fm sync` writes it" in str(refused.value)
    assert "a composed file: run `fm sync`" in str(refused.value)
    deliver(root)
    template_check()
    (root / ".gitattributes").write_text("* text\n")
    with pytest.raises(Failed, match=r"\.gitattributes: differs from what"):
        template_check()
