"""The agent-facing files: the index, the full text, the defects forced.

Footman's four shipped defect classes (audit M34-M37), each forced
against a fixture that exhibits it, plus the changelog-leak class
from footman#549: history concatenated into a context file teaches
superseded behaviour as current.
"""

from __future__ import annotations

import re
from pathlib import Path

from livery.workshop._llms import (
    first_sentence,
    llms_files,
    page_url,
    resolve_snippets,
    write_llms_files,
)


def _workspace(tmp_path: Path, *, docs_table: str = "") -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "workshop.toml").write_text(f"[workspace]\n{docs_table}")
    (root / "pyproject.toml").write_text('[project]\nname = "acme-home"\n')
    (root / "docs").mkdir()
    (root / "docs" / "index.md").write_text("# Home\n\nThe home page's prose.\n")
    member = root / "packages" / "core"
    (member / "src" / "acme" / "core").mkdir(parents=True)
    (member / "src" / "acme" / "core" / "__init__.py").write_text("")
    (member / "workshop.toml").write_text('type = "python"\nname = "acme-core"\n')
    (member / "pyproject.toml").write_text('[project]\nname = "acme-core"\n')
    (member / "docs").mkdir()
    (member / "docs" / "index.md").write_text("# core\n\nThe core manual.\n")
    (member / "CHANGELOG.md").write_text("# Changelog\n\n## [1.0.0]\n\n- was\n")
    return root


def _mounted(root: Path) -> Path:
    from livery.workshop._docs import (
        generate_api_pages,
        generate_changelog_pages,
        mount_package_docs,
    )

    mount_package_docs(root)
    generate_changelog_pages(root)
    generate_api_pages(root)
    return root


# Defect class: structure served as a description.


def test_descriptions_skip_structure_for_prose(tmp_path: Path) -> None:
    text = (
        "# Title\n\n"
        "!!! note\n    An admonition's body is chrome.\n\n"
        "| a | b |\n| - | - |\n\n"
        "1. a numbered step\n\n"
        "```sh\ncode\n```\n\n"
        "The real first sentence. And the second.\n"
    )
    summary = first_sentence(text, "https://s.example/", "index.md")
    assert summary == "The real first sentence."


def test_a_page_with_no_prose_has_no_description(tmp_path: Path) -> None:
    assert first_sentence("# Only a title\n", "https://s.example/", "a.md") == ""


# Defect class: relative links that 404 against the site root.


def test_relative_links_resolve_against_the_page(tmp_path: Path) -> None:
    text = "Read [the sibling](gitea.md) first.\n"
    summary = first_sentence(
        text, "https://s.example/", "_generated/packages/forge/protocol.md"
    )
    assert "(https://s.example/_generated/packages/forge/gitea/)" in summary


# Defect class: a directory index published at /index/.


def test_a_directory_index_publishes_at_the_directory() -> None:
    site = "https://s.example/"
    assert page_url(site, "index.md") == site
    assert page_url(site, "guide/index.md") == f"{site}guide/"
    assert page_url(site, "guide/page.md") == f"{site}guide/page/"


# Defect class: raw snippet directives. The fallback first.


def test_a_missing_snippet_keeps_the_directive_visible(tmp_path: Path) -> None:
    text = 'Before.\n\n--8<-- "absent/file.md"\n'
    assert '--8<-- "absent/file.md"' in resolve_snippets(text, tmp_path)


def test_snippets_resolve_recursively(tmp_path: Path) -> None:
    (tmp_path / "outer.md").write_text('Outer.\n\n--8<-- "inner.md"\n')
    (tmp_path / "inner.md").write_text("Inner section.\n")
    resolved = resolve_snippets('--8<-- "outer.md"\n', tmp_path)
    assert "Inner section." in resolved
    assert "--8<--" not in resolved


# Defect class (footman#549): history in the context file.


def test_the_full_file_excludes_the_machine_sections(tmp_path: Path) -> None:
    root = _mounted(_workspace(tmp_path))
    index, full = llms_files(root)
    # The index links everything, the changelog and API included.
    assert "changelog/" in index
    assert "_generated/api/core/" in index
    # The full file carries only the authored pages.
    assert "The home page's prose." in full
    assert "The core manual." in full
    assert "[1.0.0]" not in full
    assert "_generated/api" not in full
    assert "_generated/releases" not in full


def test_the_files_land_at_the_site_root(tmp_path: Path) -> None:
    root = _mounted(
        _workspace(
            tmp_path,
            docs_table='[docs]\ntitle = "Acme"\n'
            'site-url = "https://docs.acme.example/home/"\n'
            'description = "Acme, described."\n',
        )
    )
    assert write_llms_files(root) == ["llms.txt", "llms-full.txt"]
    index = (root / "site" / "llms.txt").read_text()
    assert index.startswith("# Acme\n\n> Acme, described.\n")
    # A page line: title, absolute URL, one-line description.
    assert (
        "- [Index](https://docs.acme.example/home/_generated/packages/core/):"
        " The core manual." in index
    )
    # The audited shapes, asserted absent over the whole index.
    assert not re.search(r"\): (\d+\.|\|)", index)
    assert not re.search(r"\]\((?!https?://)[^)]*\.md\)", index)
    assert "/index/" not in index
    full = (root / "site" / "llms-full.txt").read_text()
    assert "--8<--" not in full


def test_without_a_site_url_links_are_root_relative(tmp_path: Path) -> None:
    root = _mounted(_workspace(tmp_path))
    index, _full = llms_files(root)
    assert "- [Home](/)" in index.replace(": The home page's prose.", "")


def test_a_wrapped_list_item_is_not_served_as_prose() -> None:
    # A bullet wrapped across lines continues without its marker; the
    # tail is structure, not the page's description.
    text = (
        "# Title\n\n"
        "- a bullet whose text wraps onto\n"
        "  the next line without a marker\n\n"
        "The actual prose sentence. More.\n"
    )
    summary = first_sentence(text, "https://s.example/", "a.md")
    assert summary == "The actual prose sentence."
