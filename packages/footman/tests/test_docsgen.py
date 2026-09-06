"""The docs generators that moved in from the origin repository's tasks.

The workspace site build runs them through `fm footman.pages`; these
drive the pieces with logic of their own directly.
"""

from __future__ import annotations

from pathlib import Path

from livery.footman import registry as _registry

# capture(): a bare import registers the generator group in the
# process-global registry, and a later mount_layers() in the same
# process (an xdist worker runs many suites) would then read the
# whole layer as spent.
with _registry.capture():
    from livery.footman import _docsgen


def test_a_changelog_with_no_release_yet_writes_nothing(tmp_path, monkeypatch):
    """A fresh fork has only `[Unreleased]`. Skipped quietly rather than
    failing a docs build over a file that is simply young."""
    monkeypatch.setattr(_docsgen, "_PACKAGE", tmp_path)
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n", encoding="utf-8"
    )
    out = tmp_path / "_generated" / "latest-changes.md"
    _docsgen._write_latest_changes(out)
    assert out.read_text(encoding="utf-8") == ""


def test_the_home_page_quotes_the_newest_release(tmp_path, monkeypatch):
    """Rolling the changelog for a release updates the home page by
    construction — there is no second copy to forget."""
    monkeypatch.setattr(_docsgen, "_PACKAGE", tmp_path)
    # The newest heading uses the hyphen Keep a Changelog specifies; the
    # older one the em dash this file used to. Both have to parse, or the
    # newest is skipped for an older match — which is what shipped.
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n- Pending.\n\n"
        "## [0.23.0] - 2026-07-27\n\n### Added\n\n- The newest thing.\n\n"
        "## [0.22.0] — 2026-07-01\n\n- Older.\n",
        encoding="utf-8",
    )
    out = tmp_path / "_generated" / "latest-changes.md"
    _docsgen._write_latest_changes(out)

    text = out.read_text(encoding="utf-8")
    assert "Latest release: 0.23.0 — 2026-07-27" in text
    assert "The newest thing." in text
    assert "Older." not in text  # only the newest section
    assert "Pending." not in text  # and never the unreleased one


def test_the_api_page_speaks_the_public_import_path(tmp_path):
    """Directives carry the contract spelling, so anchors and the
    inventory link `livery.footman.run`, never the defining module."""
    page = _docsgen._api_markdown()
    assert "::: livery.footman.run" in page
    assert "::: footman." not in page


def test_the_api_page_refuses_an_undocumented_export(monkeypatch):
    # The validation is the point: a new export cannot ship without a
    # section, and the refusal names it.
    import pytest

    from livery.footman.context import Failed

    monkeypatch.setattr(_docsgen, "_API_SECTIONS", [("Everything", "", ["task"])])
    monkeypatch.setattr(_docsgen, "_API_EXTRA", {})
    with pytest.raises(Failed, match="exported but undocumented"):
        _docsgen._api_markdown()


def test_the_example_render_failure_names_the_child(tmp_path, monkeypatch):
    # The child invocation can break (a bad probe, a broken venv); the
    # refusal must carry its output rather than a bare exit code.
    import pytest

    from livery.footman.context import Failed

    class _Done(int):
        stdout = "child out"
        stderr = "child err"

    monkeypatch.setattr(_docsgen, "run", lambda *a, **k: _Done(3))
    with pytest.raises(Failed, match="child err"):
        _docsgen._write_tasks_page(tmp_path / "tasks-page.md")


def test_pages_writes_every_promised_file(tmp_path, monkeypatch):
    """The one advertised verb writes the whole set: the site pages and
    the snippet sources the authored pages include."""
    monkeypatch.setattr(_docsgen, "_PACKAGE", tmp_path)
    package = Path(__file__).resolve().parents[1]
    (tmp_path / "CHANGELOG.md").write_text(
        (package / "CHANGELOG.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "src" / "livery" / "footman").mkdir(parents=True)
    (tmp_path / "src" / "livery" / "footman" / "__init__.py").write_text(
        (package / "src" / "livery" / "footman" / "__init__.py").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    written: list[Path] = []
    monkeypatch.setattr(_docsgen, "_write_tasks_page", lambda out: written.append(out))
    _docsgen.docs_pages()
    assert (tmp_path / "docs" / "_generated" / "api.md").is_file()
    assert (tmp_path / "docs" / "_generated" / "errors.md").is_file()
    for name in ("globals.md", "config.md", "notes.md", "latest-changes.md"):
        assert (tmp_path / "_generated" / name).is_file(), name
    assert written == [tmp_path / "_generated" / "tasks-page.md"]
