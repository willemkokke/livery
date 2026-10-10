"""The files the listed extensions ship, composed by the engine on sync."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from livery.footman import Failed
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
        "  wrote .taplo.toml",
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


def test_the_conan_workspace_comes_with_the_first_conan_member_and_goes_with_the_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path / "ws", "[]")
    monkeypatch.setattr(
        "livery.workshop._extensions.workspace_root", lambda start=None: root
    )
    deliver(root)
    assert not (root / "conanws.yml").exists()
    member = root / "packages" / "geometry"
    member.mkdir(parents=True)
    (member / "workshop.toml").write_text(
        'kind = "cpp-conan"\nname = "acme-geometry"\n'
    )
    assert "  wrote conanws.yml" in deliver(root)
    text = (root / "conanws.yml").read_text()
    assert text.endswith("packages:\n  - path: packages/geometry\n")
    assert shipped_drift(root) == []
    # A member listing conan joins the same file as one listing nothing.
    listing = root / "packages" / "mesh"
    listing.mkdir(parents=True)
    (listing / "workshop.toml").write_text(
        'kind = "cpp-conan"\nname = "acme-mesh"\nextensions = ["cmake", "conan"]\n'
    )
    deliver(root)
    text = (root / "conanws.yml").read_text()
    assert text.endswith(
        "packages:\n  - path: packages/geometry\n  - path: packages/mesh\n"
    )
    assert shipped_drift(root) == []
    # The last members leave, and the file goes with them.
    shutil.rmtree(listing)
    shutil.rmtree(member)
    assert any(line.startswith("  removed conanws.yml") for line in deliver(root))
    assert not (root / "conanws.yml").exists()


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
    # The checkout's own files stay with LFS on, as with it off: a file
    # the outputs leave out is one the delivery withdraws.
    assert any((root / ".workshop" / "schema").iterdir())
    assert shipped_drift(root) == []


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
    owned = rendered.replace(
        '"terminal.integrated.defaultProfile.osx": "ws"',
        '"terminal.integrated.defaultProfile.osx": "zsh"',
    )
    assert owned != rendered
    settings.write_text(owned)
    with pytest.raises(Failed) as refused:
        relocate(root)
    assert "terminal.integrated.defaultProfile.osx: the render sets" in str(
        refused.value
    )
    assert '"zsh"' in str(refused.value)
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


# A file an extension's code writes: the refusal first.


def test_computed_outputs_refuse_a_second_writer_and_write_what_renders_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typing import cast

    from livery.workshop import _shipped_files
    from livery.workshop._declaration import Declaration, DeclaredOutput, Reference

    linked = tmp_path / "shipped"
    linked.mkdir()

    def render(answer: object) -> Reference:
        def answering(root: Path) -> object:
            del root
            return answer

        return cast("Reference", answering)

    declared = Declaration(
        "acme.agent",
        "acme.agent",
        tmp_path / "extension.toml",
        fragments=(
            DeclaredOutput("NOTES.md", render("# Notes\n")),
            DeclaredOutput("EMPTY.md", render("")),
            DeclaredOutput(
                ".acme/", render({"a.txt": "A", "linked": linked}), local=True
            ),
        ),
    )
    monkeypatch.setattr(
        _shipped_files,
        "declaration",
        lambda extension: declared if extension == "acme.agent" else None,
    )
    with pytest.raises(
        Failed,
        match=(
            r"NOTES\.md has one writer, and livery\.workshop:NOTES\.md and"
            r" acme\.agent:NOTES\.md both write it"
        ),
    ):
        _shipped_files.computed_outputs(
            tmp_path, ["acme.agent"], {"NOTES.md": "livery.workshop:NOTES.md"}
        )
    # A file's text, nothing for an empty answer, and a directory's
    # files: text written, a shipped path linked.
    outputs = _shipped_files.computed_outputs(
        tmp_path, ["livery.workshop", "acme.agent"]
    )
    assert [(o.path, o.body, o.owners, o.link, o.local) for o in outputs] == [
        ("NOTES.md", b"# Notes\n", ("acme.agent:NOTES.md",), None, False),
        (".acme/a.txt", b"A", ("acme.agent:.acme/",), None, True),
        (".acme/linked", b"", ("acme.agent:.acme/",), linked, True),
    ]
