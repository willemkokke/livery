"""The site's assembly: what the declarations, the nav files and the trees give it.

The refusals and fallbacks first: a declaration of the wrong shape, a
nav block that begins twice or is no TOML, entries that name no page,
a generated file at an authored file's path. Then what renders: the
placed blocks, the changelog's unreleased entry, the release view read
from the tags, and a preview of a package without docs.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from livery.extensions.docs import _site
from livery.extensions.docs._site import (
    RELEASES,
    _nav_leaves,
    abbreviation_files,
    changelog_page,
    generate_release_pages,
    materialise_preview,
    mount_package_docs,
    named_package,
    package_coverage_reports,
    package_docs_extras,
    package_nav,
    zensical_config,
)
from livery.footman import Failed
from livery.workshop import Package
from livery.workshop._packages import discover_packages

_FAILURES = (SystemExit, Failed)


def _workspace(tmp_path: Path) -> Path:
    """A workspace listing docs: core with an index and a guide, bare without docs."""
    root = tmp_path / "ws"
    root.mkdir()
    (root / "workshop.toml").write_text('[workspace]\nextensions = ["docs"]\n')
    (root / "pyproject.toml").write_text('[project]\nname = "acme-home"\n')
    (root / "docs").mkdir()
    (root / "docs" / "index.md").write_text("# Home\n")
    for name, pages in (("core", ("index.md", "guide.md")), ("bare", ())):
        member = root / "packages" / name
        (member / "src" / "acme" / name).mkdir(parents=True)
        (member / "src" / "acme" / name / "__init__.py").write_text("")
        (member / "workshop.toml").write_text(
            f'extensions = ["python"]\nname = "acme-{name}"\n'
        )
        (member / "pyproject.toml").write_text(f'[project]\nname = "acme-{name}"\n')
        if pages:
            (member / "docs").mkdir()
            for page in pages:
                (member / "docs" / page).write_text(f"# {page}\n")
    return root


def _declare(root: Path, member: str, table: str) -> None:
    """Rewrite *member*'s contract with *table* as its ``[docs]`` body."""
    (root / "packages" / member / "workshop.toml").write_text(
        f'extensions = ["python"]\nname = "acme-{member}"\n\n[docs]\n{table}'
    )


def _nav(root: Path, member: str, body: str) -> None:
    (root / "packages" / member / "docs" / "nav.toml").write_text(body)


def _package(root: Path, name: str) -> Package:
    return next(p for p in discover_packages(root) if p.directory.name == name)


def _section(config: str, name: str) -> list[Any]:
    """The entries of the section labelled *name* under the nav's Packages entry."""
    nav = tomllib.loads(config)["project"]["nav"]
    (packages,) = [entry["Packages"] for entry in nav if "Packages" in entry]
    (section,) = [entry[name] for entry in packages if name in entry]
    return list(section)


#: The core section's authored pages, as its nav names them.
_PAGES = '    { "Index" = "index.md" },\n    { "Guide" = "guide.md" },\n'


class _Notes:
    """Release notes whose unreleased entry for every package is *entry*.

    An exception for *entry* is raised instead, as notes that cannot
    reach their source raise.
    """

    def __init__(self, entry: str | Exception) -> None:
        self._entry = entry

    def entry(self, root: Path, package: Package) -> str:
        """The unreleased entry, or the exception raised."""
        del root, package
        if isinstance(self._entry, Exception):
            raise self._entry
        return self._entry


# The refusals and fallbacks first. The contract's judge refuses these
# shapes before a reader sees them; each reader refuses them on its
# own as well, naming the file.


_READERS: dict[str, Callable[[Package], object]] = {
    "coverage": package_coverage_reports,
    "extras": package_docs_extras,
}


@pytest.mark.parametrize(
    ("reader", "table", "refusal"),
    [
        ("coverage", {"coverage": "htmlcov"}, r"\[docs\] coverage must be a list"),
        (
            "extras",
            {"extra-css": "site.css"},
            r"\[docs\] extra-css must be a list of paths",
        ),
        (
            "extras",
            {"extra-javascript": "site.js"},
            r"\[docs\] extra-javascript must be a list",
        ),
    ],
)
def test_declarations_of_the_wrong_shape_refuse_without_the_judge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reader: str,
    table: dict[str, object],
    refusal: str,
) -> None:
    root = _workspace(tmp_path)
    core = _package(root, "core")
    monkeypatch.setattr(_site, "read_contract", lambda directory: {"docs": table})
    with pytest.raises(_FAILURES, match=rf"workshop\.toml: {refusal}"):
        _READERS[reader](core)


def test_a_docs_value_that_is_no_table_declares_no_extras(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    core = _package(root, "core")
    monkeypatch.setattr(_site, "read_contract", lambda directory: {"docs": "site"})
    assert package_docs_extras(core) == ([], [])


def test_a_script_table_without_a_path_refuses_naming_the_entry(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    _declare(root, "core", 'extra-javascript = [{ type = "module" }]\n')
    with pytest.raises(
        _FAILURES,
        match=r"\[docs\] extra-javascript entry \{'type': 'module'\} is not a path",
    ):
        package_docs_extras(_package(root, "core"))


def test_a_nav_block_that_begins_twice_refuses_naming_it(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _nav(
        root,
        "core",
        f"nav = [\n{_PAGES}    # nav:begin tasks\n    # nav:begin tasks\n"
        "    # nav:end tasks\n]\n",
    )
    with pytest.raises(
        _FAILURES, match=r"nav\.toml: the nav block 'tasks' begins twice"
    ):
        package_nav(_package(root, "core"))


def test_a_nav_block_that_is_no_toml_refuses_naming_its_file(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    core = _package(root, "core")
    _nav(
        root,
        "core",
        f'nav = [\n{_PAGES}    # nav:begin tools\n    {{ "Tool" =\n'
        "    # nav:end tools\n]\n",
    )
    with pytest.raises(
        _FAILURES, match=r"nav\.toml: the nav block 'tools' is not valid TOML"
    ):
        package_nav(core)
    # The block its generator emitted is read in the markers' place, and
    # is the file named when it is the broken one.
    _nav(
        root,
        "core",
        f"nav = [\n{_PAGES}    # nav:begin tools\n    # nav:end tools\n]\n",
    )
    emitted = root / "packages" / "core" / "docs" / "_generated" / "nav.tools.toml"
    emitted.parent.mkdir()
    emitted.write_text("nav = [\n")
    with pytest.raises(
        _FAILURES,
        match=r"nav\.tools\.toml: the nav block 'tools' is not valid TOML",
    ):
        package_nav(core)


def test_nav_leaves_pass_over_what_names_no_page() -> None:
    # An authored tree has had its shape checked; a block's entries have
    # not, and the leaves of either are only the page paths.
    entries: list[object] = [
        "stray",
        {"Odd": 3, "Page": "a.md"},
        {"Group": [{"Deep": "b/c.md"}]},
    ]
    assert _nav_leaves(entries) == ["a.md", "b/c.md"]


def test_an_emitted_block_renders_its_page_entries_alone(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _nav(
        root,
        "core",
        f"nav = [\n{_PAGES}    # nav:begin tools\n    # nav:end tools\n]\n",
    )
    emitted = root / "packages" / "core" / "docs" / "_generated" / "nav.tools.toml"
    emitted.parent.mkdir()
    emitted.write_text(
        'nav = [\n"stray",\n{ "Odd" = 3 },\n{ "Run" = "_generated/tools/run.md" },\n]\n'
    )
    assert _section(zensical_config(root), "core") == [
        {"Index": "packages/core/index.md"},
        {"Guide": "packages/core/guide.md"},
        {"Run": "packages/core/tools/run.md"},
    ]


def test_a_glossary_line_that_defines_nothing_is_passed_over(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    for member, text in (
        ("core", "*[CI]: Continuous integration\n"),
        ("bare", "Terms:\n\n*[CI] is spelled out on first use.\n"),
    ):
        includes = root / "packages" / member / "docs" / "includes"
        includes.mkdir(parents=True)
        (includes / "abbreviations.md").write_text(text)
    # Only a definition can disagree with another, and bare's line is none.
    assert abbreviation_files(root) == [
        "packages/bare/docs/includes/abbreviations.md",
        "packages/core/docs/includes/abbreviations.md",
    ]


def test_a_generated_file_at_an_authored_files_path_refuses_naming_both(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    docs = root / "packages" / "core" / "docs"
    (docs / "diagram.svg").write_text("<svg/>\n")
    (docs / "_generated").mkdir()
    (docs / "_generated" / "diagram.svg").write_text("<svg/>\n")
    with pytest.raises(
        _FAILURES,
        match=r"_generated/diagram\.svg and the authored diagram\.svg would publish"
        r" at one path",
    ):
        mount_package_docs(root, full=True)


def test_an_unreleased_entry_the_notes_cannot_write_leaves_the_page_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _workspace(tmp_path)
    changelog = root / "packages" / "core" / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\n## [0.1.0]\n\n- first\n")
    monkeypatch.setattr(
        "livery.workshop.release_notes",
        lambda at: _Notes(RuntimeError("the remote is unreachable")),
    )
    assert changelog_page(root, _package(root, "core")) == changelog.read_text()
    # The reason reaches the build's output, never the page.
    assert "the remote is unreachable" in capsys.readouterr().out


def test_a_release_tag_the_view_cannot_place_is_passed_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery import git

    root = _workspace(tmp_path)
    (root / "packages" / "core" / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [0.2.0] - 2026-01-10\n\n- newer\n"
    )
    tags = [
        ("packages/core/v0.2.0", "2026-01-10"),
        ("packages/core/latest", "2026-01-11"),
        ("packages/core/v0.3.0", "unknown"),
        ("packages/gone/v1.0.0", "2025-06-01"),
    ]
    monkeypatch.setattr(git, "tag_dates", lambda at, prefix: tags)
    assert generate_release_pages(root) == ["index.md", "2025.md"]
    landing = (root / RELEASES / "index.md").read_text()
    assert "## acme-core v0.2.0 (2026-01-10)\n\n- newer" in landing
    # A tag that names no version, or carries no date, is no release.
    assert "latest" not in landing and "0.3.0" not in landing
    # A package that left the workspace keeps its receipt line.
    archive = (root / RELEASES / "2025.md").read_text()
    assert "## gone v1.0.0 (2025-06-01)\n\nReleased as `packages/gone/v1.0.0`." in (
        archive
    )


def test_a_preview_of_a_package_without_docs_carries_the_overrides_alone(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    (root / "overrides").mkdir()
    (root / "overrides" / "main.html").write_text("{% extends 'base.html' %}\n")
    config = materialise_preview(root, named_package(root, "bare"))
    preview = config.parent
    assert (preview / "overrides" / "main.html").read_text() == (
        "{% extends 'base.html' %}\n"
    )
    assert (preview / "docs" / "index.md").is_file()
    assert not (preview / "docs" / "packages").exists()


def test_a_workspace_without_packages_has_no_packages_entry(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "index.md").write_text("# Home\n")
    (root / "workshop.toml").write_text('[workspace]\nextensions = ["docs"]\n')
    nav = tomllib.loads(zensical_config(root))["project"]["nav"]
    assert {"Home": "index.md"} in nav
    assert not any("Packages" in entry for entry in nav)


def test_a_group_whose_packages_show_nothing_gets_no_entry(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    # bare has no pages and no changelog, so the group above it has
    # nothing to show either.
    _declare(root, "bare", 'name = "hidden.bare"\n')
    nav = tomllib.loads(zensical_config(root))["project"]["nav"]
    (packages,) = [entry["Packages"] for entry in nav if "Packages" in entry]
    assert [next(iter(entry)) for entry in packages] == ["Index", "core"]


# Then what renders.


def test_the_authored_nav_reads_with_each_placed_block_as_its_sentinel(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    _nav(
        root,
        "core",
        f"nav = [\n{_PAGES}    # nav:begin tasks\n    # nav:end tasks\n]\n",
    )
    assert package_nav(_package(root, "core")) == [
        {"Index": "index.md"},
        {"Guide": "guide.md"},
        {"nav:tasks": "nav:tasks"},
    ]
    assert package_nav(_package(root, "bare")) is None


def test_a_placed_coverage_entry_keeps_its_place_beside_a_placed_tasks_block(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    _declare(root, "core", 'coverage = [{ label = "Python", path = "htmlcov" }]\n')
    (root / "packages" / "core" / "CHANGELOG.md").write_text("# Changelog\n")
    _nav(
        root,
        "core",
        f"nav = [\n{_PAGES}    # nav:begin coverage\n    # nav:end coverage\n"
        "    # nav:begin tasks\n    # nav:end tasks\n]\n",
    )
    labels = [next(iter(entry)) for entry in _section(zensical_config(root), "core")]
    # The changelog the author left unplaced lands ahead of the tasks
    # block; the coverage entry stays where the author put it.
    assert labels == ["Index", "Guide", "Coverage", "Changelog"]


def test_a_script_table_renders_the_attributes_it_sets_alone(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _declare(
        root,
        "core",
        'extra-javascript = [{ path = "assets/late.js", defer = false }]\n',
    )
    scripts = tomllib.loads(zensical_config(root))["project"]["extra_javascript"]
    assert scripts == [{"path": "packages/core/assets/late.js", "defer": False}]


def test_an_unreleased_entry_lands_before_the_first_release_or_after_the_title(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    core = _package(root, "core")
    changelog = root / "packages" / "core" / "CHANGELOG.md"
    unreleased = "## [Unreleased]\n\n- coming\n"
    monkeypatch.setattr("livery.workshop.release_notes", lambda at: _Notes(unreleased))
    changelog.write_text("# Changelog\n\n## [0.1.0]\n\n- first\n")
    page = changelog_page(root, core)
    assert page is not None
    assert page.startswith("# Changelog\n\n## [Unreleased]\n\n- coming\n")
    assert page.index("[Unreleased]") < page.index("[0.1.0]")
    # A changelog with no release yet takes the entry after its title.
    changelog.write_text("# Changelog\n")
    assert changelog_page(root, core) == "# Changelog\n\n" + unreleased + "\n"
    # The file on disk never changes.
    assert changelog.read_text() == "# Changelog\n"
