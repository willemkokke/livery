"""The docs toolchain: rendered config, mounts, wheel-side docs."""

from __future__ import annotations

import tomllib
from pathlib import Path

from livery.workshop._docs import (
    NAV_BEGIN,
    NAV_END,
    materialise_module_docs,
    module_docs_dir,
    mount_package_docs,
    zensical_config,
)
from livery.workshop._packages import discover_packages


def _workspace(tmp_path: Path, *, docs_table: str = "") -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "workshop.toml").write_text(f"[workspace]\n{docs_table}")
    (root / "pyproject.toml").write_text('[project]\nname = "acme-home"\n')
    (root / "docs").mkdir()
    (root / "docs" / "index.md").write_text("# Home\n")
    for name, pages in (("core", ("index.md", "guide.md")), ("bare", ())):
        member = root / "packages" / name
        (member / "src" / "acme" / name).mkdir(parents=True)
        (member / "src" / "acme" / name / "__init__.py").write_text("")
        (member / "workshop.toml").write_text(
            f'type = "python"\nname = "acme-{name}"\n'
        )
        (member / "pyproject.toml").write_text(f'[project]\nname = "acme-{name}"\n')
        if pages:
            (member / "docs").mkdir()
            for page in pages:
                (member / "docs" / page).write_text(f"# {page}\n")
    return root


# The fallbacks first: an undeclared table, a package without docs.


def test_the_config_defaults_without_a_docs_table(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    config = zensical_config(root)
    parsed = tomllib.loads(config)
    # The stable identity: the root project's name, never the
    # checkout directory's, which differs per worktree.
    assert parsed["project"]["site_name"] == "acme-home"
    assert "site_url" not in parsed["project"]


def test_a_package_without_docs_mounts_nothing(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    # bare still appears in nav for its API, but mounts no docs pages.
    assert "_generated/packages/bare" not in zensical_config(root)
    assert mount_package_docs(root) == ["core"]
    assert not (root / "docs/_generated/packages/bare").exists()


def test_a_docsless_package_gets_its_stale_wheel_copy_removed(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    bare = next(p for p in discover_packages(root) if p.directory.name == "bare")
    target = module_docs_dir(bare)
    assert target is not None
    target.mkdir(parents=True)
    (target / "stale.md").write_text("stale\n")
    assert materialise_module_docs(bare) is None
    assert not target.exists()


def test_the_mount_rebuilds_whole(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    mount_package_docs(root)
    stale = root / "docs/_generated/packages/core/gone.md"
    stale.write_text("stale\n")
    mount_package_docs(root)
    assert not stale.exists()
    assert (root / "docs/_generated/packages/core/guide.md").is_file()


def test_the_config_carries_the_contract_and_the_nav(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path,
        docs_table='[docs]\ntitle = "Acme"\nsite_url = "https://docs.acme.example/home/"\n',
    )
    config = zensical_config(root)
    parsed = tomllib.loads(config)
    assert parsed["project"]["site_name"] == "Acme"
    assert parsed["project"]["site_url"] == "https://docs.acme.example/home/"
    assert NAV_BEGIN in config and NAV_END in config
    nav = parsed["project"]["nav"]
    assert nav[0] == {"Home": "index.md"}
    core = next(entry for entry in nav if "core" in entry)
    # Index first, then the rest sorted, all at the mount path.
    assert core["core"][0] == {"Index": "_generated/packages/core/index.md"}
    assert core["core"][1] == {"guide": "_generated/packages/core/guide.md"}


def test_the_wheel_side_docs_refresh_whole(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    core = next(p for p in discover_packages(root) if p.directory.name == "core")
    target = materialise_module_docs(core)
    assert target is not None and (target / "guide.md").is_file()
    (target / "stale.md").write_text("stale\n")
    (core.directory / "docs" / "guide.md").write_text("# edited\n")
    target = materialise_module_docs(core)
    assert target is not None
    assert not (target / "stale.md").exists()
    assert (target / "guide.md").read_text() == "# edited\n"


def test_the_rendered_config_joins_the_generated_set(tmp_path: Path) -> None:
    # Drift protection comes from membership: whatever generate()
    # returns is applied and drift-checked by the template machinery,
    # so the config being in the set is the whole guarantee.
    from livery.workshop._ci_generate import generate

    root = _workspace(tmp_path)
    for kind in ("github", "gitea", "gitlab"):
        (root / "workshop.toml").write_text(
            f'[workspace]\n[forge]\nkind = "{kind}"\nowner = "acme"\n'
        )
        files = generate(root)
        assert "zensical.toml" in files
        assert files["zensical.toml"].startswith("# Generated")


# Phase 2: the API reference. Fallbacks first.


def test_a_srcless_package_has_no_api(tmp_path: Path) -> None:
    import shutil

    from livery.workshop._docs import api_modules

    root = _workspace(tmp_path)
    bare = next(p for p in discover_packages(root) if p.directory.name == "bare")
    shutil.rmtree(bare.directory / "src")
    assert api_modules(bare) == []
    assert "_generated/api/bare" not in zensical_config(root)


def test_api_modules_sort_public_first_and_skip_the_machinery(
    tmp_path: Path,
) -> None:
    from livery.workshop._docs import api_modules

    root = _workspace(tmp_path)
    core = next(p for p in discover_packages(root) if p.directory.name == "core")
    module = core.directory / "src" / "acme" / "core"
    (module / "_private.py").write_text('"""Private."""\n')
    (module / "public.py").write_text('"""Public."""\n')
    (module / "__main__.py").write_text("")
    (module / "_docs").mkdir()
    (module / "_docs" / "index.md").write_text("# stale\n")
    (module / "sub").mkdir()
    (module / "sub" / "__init__.py").write_text('"""Sub."""\n')
    (module / "sub" / "_inner.py").write_text('"""Inner."""\n')
    modules = api_modules(core)
    pages = [page for page, _dotted in modules]
    dotted = [d for _page, d in modules]
    # The package index first, then public before private per level;
    # __main__ and the machine _docs never appear.
    assert pages[0] == "index.md" and dotted[0] == "acme.core"
    assert pages.index("public.md") < pages.index("_private.md")
    assert pages.index("sub/index.md") < pages.index("_private.md")
    assert "__main__.md" not in pages
    assert all("_docs" not in page for page in pages)
    assert ("sub/_inner.md", "acme.core.sub._inner") in modules


def test_api_pages_rebuild_whole_with_one_directive_each(tmp_path: Path) -> None:
    from livery.workshop._docs import API, generate_api_pages

    root = _workspace(tmp_path)
    stale = root / API / "core" / "gone.md"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale\n")
    assert generate_api_pages(root) == ["bare", "core"]
    assert not stale.exists()
    index = (root / API / "core" / "index.md").read_text()
    assert "::: acme.core" in index


def test_the_config_wires_mkdocstrings_only_when_modules_exist(
    tmp_path: Path,
) -> None:
    from livery.workshop._docs import INVENTORIES

    root = _workspace(tmp_path)
    config = zensical_config(root)
    parsed = tomllib.loads(config)
    handler = parsed["project"]["plugins"]["mkdocstrings"]["handlers"]["python"]
    assert handler["paths"] == ["packages/bare/src", "packages/core/src"]
    assert list(handler["inventories"]) == list(INVENTORIES)
    assert handler["options"]["docstring_style"] == "google"
    assert handler["options"]["show_if_no_docstring"] is True
    core = next(entry for entry in parsed["project"]["nav"] if "core" in entry)
    api = next(part for part in core["core"] if "API" in part)
    assert api["API"][0] == {"acme.core": "_generated/api/core/index.md"}


# Phase 3: changelogs and the release view. Fallbacks first.


def _tagged_workspace(tmp_path: Path) -> Path:
    import subprocess

    root = _workspace(tmp_path)
    (root / "packages" / "core" / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [0.2.0] - 2026-01-10\n\n- newer\n"
        "\n## [0.1.0] - 2025-03-01\n\n- older\n"
    )

    def _git(*args: str, date: str = "2026-01-10T12:00:00") -> None:
        subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            check=True,
            env={
                **__import__("os").environ,
                "GIT_AUTHOR_NAME": "T",
                "GIT_AUTHOR_EMAIL": "t@livery.local",
                "GIT_COMMITTER_NAME": "T",
                "GIT_COMMITTER_EMAIL": "t@livery.local",
                "GIT_COMMITTER_DATE": date,
                "GIT_AUTHOR_DATE": date,
                "HOME": str(tmp_path),
            },
        )

    _git("init", "--initial-branch=main")
    _git("add", "-A")
    _git("commit", "-m", "chore: seed", date="2025-03-01T12:00:00")
    _git(
        "tag",
        "-a",
        "packages/core/v0.1.0",
        "-m",
        "packages/core/v0.1.0",
        date="2025-03-01T12:00:00",
    )
    _git(
        "tag",
        "-a",
        "packages/core/v0.2.0",
        "-m",
        "packages/core/v0.2.0",
        date="2026-01-10T12:00:00",
    )
    return root


def test_no_tags_still_serves_the_page_the_nav_points_at(tmp_path: Path) -> None:
    # A shallow clone has no tags, but the nav derives from committed
    # state alone, so the landing page must exist and say so.
    from livery.workshop._docs import RELEASES, generate_release_pages

    root = _workspace(tmp_path)
    (root / "packages" / "core" / "CHANGELOG.md").write_text("# Changelog\n")
    assert generate_release_pages(root) == ["index.md"]
    assert "No release tags" in (root / RELEASES / "index.md").read_text()
    assert '{ "Releases" = "_generated/releases/index.md" }' in zensical_config(root)


def test_no_changelogs_means_no_release_view(tmp_path: Path) -> None:
    from livery.workshop._docs import generate_release_pages

    root = _workspace(tmp_path)
    assert generate_release_pages(root) == []
    assert "Releases" not in zensical_config(root)


def test_a_missing_entry_falls_back_to_the_receipt_line(tmp_path: Path) -> None:
    from livery.workshop._docs import _entry_body, _release_block

    root = _tagged_workspace(tmp_path)
    changelog = root / "packages" / "core" / "CHANGELOG.md"
    assert _entry_body(changelog, "9.9.9") == ""
    block = _release_block(root, ("2026-01-10", "core", "acme-core", "9.9.9"))
    assert "Released as `packages/core/v9.9.9`." in block


def test_a_changelog_page_survives_a_cliffless_package(tmp_path: Path) -> None:
    # The fallback: no cliff.toml, so the unreleased section cannot
    # derive; the page is the committed file alone, reason printed.
    from livery.workshop._docs import changelog_page

    root = _tagged_workspace(tmp_path)
    core = next(p for p in discover_packages(root) if p.directory.name == "core")
    page = changelog_page(root, core)
    assert page is not None and "## [0.2.0]" in page


def test_the_release_view_paginates_by_year(tmp_path: Path) -> None:
    from livery.workshop._docs import RELEASES, generate_release_pages, release_years

    root = _tagged_workspace(tmp_path)
    assert release_years(root) == ["2025"]
    written = generate_release_pages(root)
    assert written == ["index.md", "2025.md"]
    landing = (root / RELEASES / "index.md").read_text()
    archive = (root / RELEASES / "2025.md").read_text()
    assert "acme-core v0.2.0 (2026-01-10)" in landing
    assert "v0.1.0" not in landing.replace("[2025](2025.md)", "")
    assert "[2025](2025.md)" in landing
    assert "acme-core v0.1.0 (2025-03-01)" in archive
    assert "- older" in archive and "- newer" in landing


def test_the_nav_carries_releases_and_changelogs(tmp_path: Path) -> None:
    root = _tagged_workspace(tmp_path)
    config = zensical_config(root)
    parsed = tomllib.loads(config)
    nav = parsed["project"]["nav"]
    releases = next(entry for entry in nav if "Releases" in entry)
    # One nav entry from committed state; the landing links the
    # year archives itself, so a tagless checkout renders the same.
    assert releases["Releases"] == "_generated/releases/index.md"
    core = next(entry for entry in nav if "core" in entry)
    assert {"Changelog": "_generated/packages/core/changelog.md"} in core["core"]
