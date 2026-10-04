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


def _composed(lines: list[str]) -> list[str]:
    """*lines* for the composed files alone, the agent's own content left out."""
    return [
        line
        for line in lines
        if not line.split()[1].startswith((".claude", ".workshop", "CLAUDE.md"))
    ]


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
    assert _composed(deliver(root)) == [
        "  wrote .gitattributes",
        "  wrote .gitignore",
        "  wrote .vscode/extensions.json",
        "  wrote .vscode/settings.json",
        "  wrote tasks.py",
    ]
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
    assert _composed(deliver(root)) == ["  updated .gitignore"]
    ignored = (root / ".gitignore").read_text()
    assert "site/" not in ignored
    assert "mine/\n# -- workshop: end rules --" in ignored


def test_the_drift_check_names_a_composed_files_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._templates import apply_generated, drift_check

    root = _workspace(tmp_path / "ws", "[]")
    monkeypatch.setattr("livery.workshop._templates._root", lambda: root)
    with pytest.raises(Failed) as refused:
        drift_check()
    assert "  .gitignore: missing; `fm sync` writes it" in str(refused.value)
    assert "run `fm sync`, which writes them again" in str(refused.value)
    deliver(root)
    # The generated files are judged beside the composed ones.
    with pytest.raises(Failed, match=r"\.github/workflows/ci\.yml: generated, but"):
        drift_check()
    apply_generated(root)
    drift_check()
    (root / ".gitattributes").write_text("* text\n")
    with pytest.raises(Failed, match=r"\.gitattributes: differs from what"):
        drift_check()


def test_lfs_rules_are_left_out_and_named_until_the_workspace_turns_lfs_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _shipped_files

    root = _workspace(tmp_path / "ws", "[]")
    monkeypatch.setattr(
        "livery.workshop._extensions.workspace_root", lambda start=None: root
    )
    real = _shipped_files.shipped

    def with_assets(at: Path) -> tuple[list[Fragment], list[str]]:
        fragments, order = real(at)
        assets = Fragment(
            "assets",
            ".gitattributes",
            ".gitattributes",
            "*.png filter=lfs diff=lfs merge=lfs -text\n*.svg text\n",
        )
        return [*fragments, assets], [*order, "assets"]

    monkeypatch.setattr(_shipped_files, "shipped", with_assets)
    # Off, the default: never refused; the rule is left out and named,
    # and the extension's other lines are composed.
    lines = deliver(root)
    assert lines[0] == (
        "  .gitattributes: Git LFS is off, so the LFS rules for *.png are left"
        " out; `[workspace] lfs = true` in workshop.toml composes them"
    )
    attributes = (root / ".gitattributes").read_text()
    assert "filter=lfs" not in attributes and "*.svg text" in attributes
    assert shipped_drift(root) == []
    # On: the rule is composed, and the tool and the checkout follow.
    (root / "workshop.toml").write_text("[workspace]\nextensions = []\nlfs = true\n")
    assert _composed(deliver(root)) == ["  updated .gitattributes"]
    assert (
        "*.png filter=lfs diff=lfs merge=lfs -text"
        in (root / ".gitattributes").read_text()
    )


def test_lfs_on_requires_the_tool_and_the_checkout_fetches_the_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._ci_generate import generate
    from livery.workshop._tools import requirements

    root = _workspace(tmp_path / "ws", "[]")
    monkeypatch.setattr(
        "livery.workshop._extensions.workspace_root", lambda start=None: root
    )
    assert "git_lfs" not in {r.name for r in requirements(root)}
    assert "lfs: true" not in generate(root)[".github/workflows/ci.yml"]
    (root / "workshop.toml").write_text("[workspace]\nextensions = []\nlfs = true\n")
    (lfs,) = [r for r in requirements(root) if r.name == "git_lfs"]
    assert lfs.site == "workshop.toml [workspace] lfs"
    assert "          lfs: true\n" in generate(root)[".github/workflows/ci.yml"]
    # A value that is not a boolean refuses as the contract's type.
    (root / "workshop.toml").write_text('[workspace]\nextensions = []\nlfs = "yes"\n')
    with pytest.raises(Failed, match=r"workspace.lfs"):
        requirements(root)


def test_two_contributions_to_one_key_refuse_naming_both_owners(tmp_path: Path) -> None:
    template = Fragment(
        "livery.workshop", "t", "s.json", "{\n{{ contributed_entries }}}\n"
    )
    one = Fragment("livery.workshop", "a", "s.json", '{"k": 1}', contributes=True)
    two = Fragment("docs", "b", "s.json", '{"k": 2}', contributes=True)
    with pytest.raises(Failed, match=r"k is set by both livery.workshop:a and docs:b"):
        engine.plan(tmp_path, (template, one, two), ("livery.workshop", "docs"), {})
    same = Fragment("docs", "b", "s.json", '{"k": 1, "j": [2]}', contributes=True)
    (out,) = engine.plan(
        tmp_path, (template, one, same), ("livery.workshop", "docs"), {}
    )
    assert out.body.decode() == '{\n  "k": 1,\n  "j": [\n    2\n  ],\n}\n'


def test_check_fix_moves_what_vscode_added_into_the_region(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._shipped_files import relocate

    root = _workspace(tmp_path / "ws", "[]")
    monkeypatch.setattr(
        "livery.workshop._extensions.workspace_root", lambda start=None: root
    )
    deliver(root)
    settings = root / ".vscode/settings.json"
    rendered = settings.read_text()
    # The refusal first: a setting the render owns, changed by hand.
    owned = rendered.replace('"charliermarsh.ruff"', '"ms-python.black-formatter"')
    settings.write_text(owned)
    with pytest.raises(Failed) as refused:
        relocate(root)
    assert "[python]: the render sets" in str(refused.value)
    assert '"ms-python.black-formatter"' in str(refused.value)
    # VS Code's UI adds a key before the closing brace, and a recommendation
    # inside the list; both move into the regions and the files match.
    settings.write_text(rendered.replace("\n}\n", ',\n  "editor.rulers": [88]\n}\n'))
    extensions = root / ".vscode/extensions.json"
    extensions.write_text(
        extensions.read_text().replace(
            '"recommendations": [\n',
            '"recommendations": [\n    "ms-vscode.cpptools",\n',
        )
    )
    assert relocate(root) == [
        "  .vscode/extensions.json: moved ms-vscode.cpptools into the region",
        "  .vscode/settings.json: moved editor.rulers into the region",
    ]
    assert shipped_drift(root) == []
    assert '  "editor.rulers": [\n    88\n  ],\n  // -- workshop: end settings --' in (
        settings.read_text()
    )
    assert deliver(root) == []
    assert relocate(root) == []


def test_the_header_lint_leaves_the_engines_templates_alone(tmp_path: Path) -> None:
    """A composed file's template carries that file's header; the lint adds none."""
    from livery.workshop._provenance import content_lint

    content = tmp_path / "packages/thing/src/livery/thing/content"
    (content / "root").mkdir(parents=True)
    (content / "root/.gitignore").write_text("dist/\n")
    (content / "fragments").mkdir()
    (content / "fragments/voice.md").write_text("# Voice\n")
    assert content_lint(tmp_path) == [
        "packages/thing/src/livery/thing/content/fragments/voice.md: missing its header"
    ]


def test_a_toml_region_and_its_comment_come_after_every_extensions_tables(
    tmp_path: Path,
) -> None:
    base = Fragment(
        "livery.workshop",
        "p",
        "pyproject.toml",
        "[project]\nname = 'acme'\n\n# The repository's own tables.\n"
        "# -- workshop: region tables, yours to edit; the render keeps it --\n"
        "# -- workshop: end tables --\n",
    )
    ruff = Fragment("docs", "t", "pyproject.toml", "[tool.ruff]\nline-length = 88\n")
    (out,) = engine.plan(tmp_path, (base, ruff), ("livery.workshop", "docs"), {})
    assert out.body.decode() == (
        "[project]\nname = 'acme'\n\n[tool.ruff]\nline-length = 88\n\n"
        "# The repository's own tables.\n"
        "# -- workshop: region tables, yours to edit; the render keeps it --\n"
        "# -- workshop: end tables --\n"
    )
