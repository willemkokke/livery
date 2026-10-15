"""The docs toolchain: rendered config, mounts, wheel-side docs."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest

from livery.extensions.docs._navblocks import NAV_BEGIN, NAV_END
from livery.extensions.docs._packages_page import MOUNT
from livery.extensions.docs._site import (
    mount_package_docs,
    named_package,
    scoped_config,
    zensical_config,
)
from livery.extensions.python._backend import (
    materialise_module_docs,
    module_docs,
    module_docs_dir,
)
from livery.footman import Failed
from livery.workshop._declaration import DeclarationError
from livery.workshop._packages import discover_packages
from workshop_composed import composed_groups
from workshop_coverage_pages import read_page
from workshop_docs_declared import docs_jobs  # noqa: F401
from workshop_extension_fakes import fake_extensions
from workshop_measured import measure

_FAILURES = (SystemExit, Failed)

#: This repository's root, which the tests named for this workspace read.
ROOT = Path(__file__).resolve().parents[3]


def _workspace(tmp_path: Path, *, docs_table: str = "") -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "workshop.toml").write_text(
        "[workspace]\n"
        + ("" if docs_table.startswith("extensions") else 'extensions = ["docs"]\n')
        + docs_table
    )
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


def _package_entries(nav: Any) -> list[Any]:
    """Every entry under the nav's one Packages entry, shallowest first."""
    (packages,) = [e for e in nav if isinstance(e, dict) and "Packages" in e]
    found: list[Any] = []
    level: list[Any] = list(packages["Packages"])
    while level:
        nested: list[Any] = []
        for entry in level:
            if isinstance(entry, dict):
                found.append(entry)
                nested += [
                    v
                    for value in entry.values()
                    if isinstance(value, list)
                    for v in value
                ]
        level = nested
    return found


def _package_entry(nav: Any, name: str) -> Any:
    """The shallowest section labelled *name* under the nav's Packages entry.

    A package's place sits above anything inside its section, a task
    group of the same name included.
    """
    return next(entry for entry in _package_entries(nav) if name in entry)


def _referenced(root: Path) -> list[str]:
    """Write each package's API reference as the docs phase's step writes it.

    The members whose reference was written: the API pages and the
    ``api`` nav block the section reads.
    """
    from livery.extensions.mkdocstrings._pages import documented, write_reference

    return [
        package.member
        for package in discover_packages(root)
        if documented(package) and write_reference(package)
    ]


# The fallbacks first: a workspace without docs, an undeclared table, a
# package without docs.


def test_a_workspace_without_docs_composes_an_empty_docs_group(
    tmp_path: Path,
) -> None:
    # The docs group holds what the listed extensions' site build imports,
    # so a workspace that lists no such extension installs none of it. The
    # group stays declared, since the project file's `default-groups` names
    # it.
    root = tmp_path / "ws"
    root.mkdir()
    contract = root / "workshop.toml"
    contract.write_text('[workspace]\nname = "acme"\nextensions = []\n')
    assert composed_groups(root)["docs"] == []
    # Listed, docs adds the site's builder and nothing else.
    contract.write_text('[workspace]\nname = "acme"\nextensions = ["docs"]\n')
    assert composed_groups(root)["docs"] == ["zensical>=0.0.50"]


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
    # bare ships no pages: the nav names none of its, and nothing mounts.
    assert '= "packages/bare/index.md"' not in zensical_config(root)
    assert mount_package_docs(root) == ["core"]
    assert not (root / "docs/packages/bare").exists()


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


def test_a_root_with_no_public_names_keeps_its_docs_where_the_wheel_ships(
    tmp_path: Path,
) -> None:
    # No __init__.py marks the root, so nothing is found until the build
    # names the module it ships.
    root = _workspace(tmp_path)
    core = next(p for p in discover_packages(root) if p.directory.name == "core")
    module = core.directory / "src" / "acme" / "core"
    (module / "__init__.py").unlink()
    (module / "_extension.py").write_text("API_VERSION = 1\n")
    assert module_docs_dir(core) is None
    # With nowhere to go, no copy is made, and a build carries none.
    assert materialise_module_docs(core) is None
    with module_docs(core) as copied:
        assert copied is None
    assert not list(core.directory.rglob("_docs"))
    (core.directory / "pyproject.toml").write_text(
        '[project]\nname = "acme-core"\n\n'
        '[tool.uv.build-backend]\nmodule-name = "acme.core"\n'
    )
    assert module_docs_dir(core) == module / "_docs"


def test_a_member_in_a_group_directory_mounts_and_stays_mounted(tmp_path: Path) -> None:
    import shutil

    root = _workspace(tmp_path)
    for name in ("demo", "kept"):
        member = root / "packages" / "extensions" / name
        (member / "src" / "acme" / "extensions" / name).mkdir(parents=True)
        (member / "workshop.toml").write_text(
            f'extensions = ["python"]\nname = "acme-extensions-{name}"\n'
        )
        (member / "pyproject.toml").write_text(
            f'[project]\nname = "acme-extensions-{name}"\n'
        )
        (member / "docs").mkdir()
        (member / "docs" / "index.md").write_text(f"# {name}\n")
    assert {"extensions/demo", "extensions/kept"} <= set(mount_package_docs(root))
    assert (root / "docs/packages/extensions/demo/index.md").is_file()
    # Unchanged, both stay as they are: their stamps sit in the group's
    # folder, and the group's folder is no stale mount.
    assert mount_package_docs(root) == []
    # A group's member that left takes its mount with it; the other stays.
    shutil.rmtree(root / "packages" / "extensions" / "demo")
    mount_package_docs(root)
    assert not (root / "docs/packages/extensions/demo").exists()
    assert (root / "docs/packages/extensions/kept/index.md").is_file()


def test_the_mount_rebuilds_whole_when_asked(tmp_path: Path) -> None:
    """An unchanged section keeps its mount; ``full`` rebuilds it whole."""
    root = _workspace(tmp_path)
    mount_package_docs(root)
    stale = root / "docs/packages/core/gone.md"
    stale.write_text("stale\n")
    assert mount_package_docs(root) == []  # the section did not move
    assert stale.exists()
    assert mount_package_docs(root, full=True) == ["core"]
    assert not stale.exists()
    assert (root / "docs/packages/core/guide.md").is_file()


def test_the_config_carries_the_contract_and_the_nav(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path,
        docs_table='[docs]\ntitle = "Acme"\nsite-url = "https://docs.acme.example/home/"\n',
    )
    config = zensical_config(root)
    parsed = tomllib.loads(config)
    assert parsed["project"]["site_name"] == "Acme"
    assert parsed["project"]["site_url"] == "https://docs.acme.example/home/"
    assert NAV_BEGIN in config and NAV_END in config
    nav = parsed["project"]["nav"]
    assert nav[0] == {"Home": "index.md"}
    core = _package_entry(nav, "core")
    # Index first, then the rest sorted, all at the mount path.
    assert core["core"][0] == {"Index": "packages/core/index.md"}
    assert core["core"][1] == {"guide": "packages/core/guide.md"}


def test_a_builds_docs_copy_is_gone_after_the_build_failed_or_not(
    tmp_path: Path,
) -> None:
    # Left in the source tree, the docs' example files read as the
    # package's own code to every checker that walks it.
    root = _workspace(tmp_path)
    core = next(p for p in discover_packages(root) if p.directory.name == "core")
    target = module_docs_dir(core)
    assert target is not None
    with pytest.raises(RuntimeError, match="the build failed"), module_docs(core):
        assert (target / "guide.md").is_file()
        raise RuntimeError("the build failed")
    assert not target.exists()
    with module_docs(core) as copied:
        assert copied == target and (target / "guide.md").is_file()
    assert not target.exists()


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


def test_the_config_is_the_builds_and_never_a_rendered_file(tmp_path: Path) -> None:
    """The build assembles it; nothing committed enumerates the packages."""
    from livery.extensions.docs._site import write_site_config
    from livery.workshop._ci_generate import generate

    root = _workspace(tmp_path)
    for kind in ("github", "gitea", "gitlab"):
        (root / "workshop.toml").write_text(
            f'[workspace]\n[forge]\nkind = "{kind}"\nowner = "acme"\n'
        )
        assert "zensical.toml" not in generate(root)
    written = write_site_config(root)
    assert written == root / "zensical.toml"
    text = written.read_text()
    assert text.startswith("# Assembled by the docs build")
    assert tomllib.loads(text)["project"]["site_name"] == "acme-home"


# The site's config: the docs extension's tables, then each listed
# extension's answer to docs.config. The refusals first.


def _answering(query: str, value: str, requires: str = "") -> str:
    """A workspace-level extension answering *query* with the literal *value*."""
    return (
        "[extension]\n"
        + (f'requires = ["{requires}"]\n' if requires else "")
        + f'[queries."{query}"]\nvalue = {value}\n'
    )


def _relist(root: Path, extensions: str) -> None:
    """Rewrite *root*'s contract listing *extensions*, a TOML list as written."""
    (root / "workshop.toml").write_text(f"[workspace]\nextensions = {extensions}\n")


#: An extension answering docs.config with its _steps module's writer.
_WRITER = '[extension]\n[queries]\n"docs.config" = "{package}._steps:writer"\n'

#: The writer's module: its tables, the scope's root path and its
#: members, as a TOML list, put in for ``{anchor}`` and ``{members}``.
_WRITES = """
def writer(root):
    del root
    return write


def write(scope):
    members = [scope.anchor + package.member for package in scope.packages]
    listed = "[" + ", ".join(f'"{member}"' for member in members) + "]"
    return TABLES.replace("{anchor}", scope.anchor).replace("{members}", listed)
"""


def _writing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **tables: str) -> None:
    """Fake extensions ``acme.<name>`` answering docs.config, each writing *tables*.

    ``{anchor}`` in a table stands for the scope's path to the root and
    ``{members}`` for the members it covers, as a TOML list.
    """
    from workshop_extension_fakes import fake_steps

    fake_extensions(tmp_path, monkeypatch, **dict.fromkeys(tables, _WRITER))
    for name, text in tables.items():
        fake_steps(tmp_path, name, f"TABLES = {text!r}\n" + _WRITES)


def test_two_answers_writing_one_config_table_refuse_naming_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    table = '[project.plugins.carve]\nstyle = "chisel"\n'
    _writing(tmp_path, monkeypatch, one=table, two=table)
    root = _workspace(
        tmp_path, docs_table='extensions = ["docs", "acme.one", "acme.two"]\n'
    )
    refusal = (
        r"acme\.one and acme\.two both write \[project\.plugins\.carve\] in the"
        r" site's config; one extension writes a table"
    )
    with pytest.raises(_FAILURES, match=refusal):
        zensical_config(root)
    with pytest.raises(_FAILURES, match=refusal):
        scoped_config(root, named_package(root, "core"))


def test_an_answer_that_breaks_the_config_refuses_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._queries import QueryError

    # A table the docs extension writes itself, and text that is no TOML.
    _writing(
        tmp_path,
        monkeypatch,
        theme='[project.theme]\nlanguage = "fr"\n',
        broken="[project.plugins.carve\n",
    )
    root = _workspace(tmp_path, docs_table='extensions = ["docs", "acme.theme"]\n')
    with pytest.raises(
        _FAILURES,
        match=r"the site's config does not parse once acme\.theme's tables join it",
    ):
        zensical_config(root)
    _relist(root, '["docs", "acme.broken"]')
    with pytest.raises(
        _FAILURES,
        match=r"acme\.broken writes tables for the site's config that are not TOML",
    ):
        zensical_config(root)
    # An answer is the function that writes the tables, never a value.
    fake_extensions(tmp_path, monkeypatch, value=_answering("docs.config", '"tables"'))
    _relist(root, '["docs", "acme.value"]')
    with pytest.raises(
        QueryError,
        match=r"docs\.config: acme\.value answers 'tables', and an answer is the"
        r" function that writes the extension's tables for a config",
    ):
        zensical_config(root)


def test_a_members_policy_outside_public_and_all_refuses_naming_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _extensions

    fake_extensions(tmp_path, monkeypatch, site=_answering("docs.members", '"some"'))
    path = tmp_path / "site" / "acme" / "site" / "extension.toml"
    with pytest.raises(DeclarationError) as raised:
        _extensions.declaration("acme.site")
    assert str(raised.value) == (
        f"{path}: queries.\"docs.members\".value is 'some'; the query's values are"
        " 'public', 'all'"
    )


def test_a_second_generator_s_tables_reach_the_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _writing(
        tmp_path,
        monkeypatch,
        carve="[project.plugins.mkdocstrings.handlers.carve]\n"
        "paths = {members}\n\n"
        "[project.plugins.mkdocstrings.handlers.carve.options]\n"
        'depth = 2\nstyle = "chisel"\nstrict = true\n',
    )
    root = _workspace(
        tmp_path, docs_table='extensions = ["acme.carve", "mkdocstrings"]\n'
    )
    _referenced(root)
    config = zensical_config(root)
    handlers = tomllib.loads(config)["project"]["plugins"]["mkdocstrings"]["handlers"]
    assert handlers["carve"]["paths"] == ["bare", "core"]
    assert handlers["carve"]["options"] == {
        "depth": 2,
        "style": "chisel",
        "strict": True,
    }
    assert handlers["python"]["paths"] == ["packages/bare/src", "packages/core/src"]
    # The answers join in composition order, after the docs extension's
    # own tables.
    assert (
        config.index("[project.markdown_extensions.pymdownx.snippets]")
        < config.index("[project.plugins.mkdocstrings.handlers.carve]")
        < config.index("[project.plugins.mkdocstrings.handlers.python]")
    )
    # A preview hands the answers its one package, read from two levels down.
    preview = tomllib.loads(scoped_config(root, named_package(root, "core")))
    carved = preview["project"]["plugins"]["mkdocstrings"]["handlers"]["carve"]
    assert carved["paths"] == ["../../core"]


def test_a_package_no_generator_documents_has_no_api_entry(tmp_path: Path) -> None:
    root = _workspace(tmp_path, docs_table='extensions = ["docs", "mkdocstrings"]\n')
    bare = root / "packages" / "bare"
    (bare / "workshop.toml").write_text(
        'extensions = ["cmake", "conan"]\nname = "acme-bare"\n'
    )
    (bare / "pyproject.toml").unlink()
    (bare / "docs").mkdir()
    (bare / "docs" / "index.md").write_text("# bare\n")
    assert _referenced(root) == ["core"]
    # No page states an absence: the section carries its own pages alone.
    assert not (bare / "docs" / "_generated").exists()
    config = tomllib.loads(zensical_config(root))
    section = _package_entry(config["project"]["nav"], "bare")
    assert section["bare"] == [{"Index": "packages/bare/index.md"}]
    core = _package_entry(config["project"]["nav"], "core")
    assert any("API" in part for part in core["core"])
    handlers = config["project"]["plugins"]["mkdocstrings"]["handlers"]
    assert list(handlers) == ["python"]
    assert handlers["python"]["paths"] == ["packages/core/src"]
    # Without its pages a package has no section at all.
    (bare / "docs" / "index.md").unlink()
    config = tomllib.loads(zensical_config(root))
    assert not any("bare" in e for e in _package_entries(config["project"]["nav"]))


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
    from livery.extensions.docs._site import RELEASES, generate_release_pages

    root = _workspace(tmp_path)
    (root / "packages" / "core" / "CHANGELOG.md").write_text("# Changelog\n")
    assert generate_release_pages(root) == ["index.md"]
    assert "No release tags" in (root / RELEASES / "index.md").read_text()
    assert '{ "Releases" = "releases/index.md" }' in zensical_config(root)


def test_no_changelogs_means_no_release_view(tmp_path: Path) -> None:
    from livery.extensions.docs._site import generate_release_pages

    root = _workspace(tmp_path)
    assert generate_release_pages(root) == []
    assert "Releases" not in zensical_config(root)


def test_a_missing_entry_falls_back_to_the_receipt_line(tmp_path: Path) -> None:
    from livery.extensions.docs._site import _entry_body, _release_block

    root = _tagged_workspace(tmp_path)
    changelog = root / "packages" / "core" / "CHANGELOG.md"
    assert _entry_body(changelog, "9.9.9") == ""
    block = _release_block(root, ("2026-01-10", "core", "acme-core", "9.9.9"))
    assert "Released as `packages/core/v9.9.9`." in block


def test_a_changelog_page_survives_a_cliffless_package(tmp_path: Path) -> None:
    # The fallback: no cliff.toml, so the unreleased section cannot
    # derive; the page is the committed file alone, reason printed.
    from livery.extensions.docs._site import changelog_page

    root = _tagged_workspace(tmp_path)
    core = next(p for p in discover_packages(root) if p.directory.name == "core")
    page = changelog_page(root, core)
    assert page is not None and "## [0.2.0]" in page


def test_the_release_view_paginates_by_year(tmp_path: Path) -> None:
    from livery.extensions.docs._site import (
        RELEASES,
        generate_release_pages,
        release_years,
    )

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
    assert releases["Releases"] == "releases/index.md"
    core = _package_entry(nav, "core")
    assert {"Changelog": "packages/core/changelog.md"} in core["core"]


# Phase 5: the publish seams. Fallbacks first.


def test_the_seam_defaults_by_forge_kind(tmp_path: Path) -> None:
    from livery.extensions.docs._contract import publish_seam

    root = _workspace(tmp_path)
    # No [forge] table at all: nothing to publish to.
    assert publish_seam(root) == "none"
    for kind, seam in (
        ("github", "pages"),
        ("gitlab", "pages"),
        ("gitea", "container"),
    ):
        (root / "workshop.toml").write_text(
            f'[workspace]\n[forge]\nkind = "{kind}"\nowner = "acme"\n'
        )
        assert publish_seam(root) == seam


def test_a_declared_seam_wins_and_garbage_refuses(tmp_path: Path) -> None:
    import pytest

    from livery.extensions.docs._contract import publish_seam

    root = _workspace(tmp_path, docs_table='[docs]\npublish = "ssh"\n')
    assert publish_seam(root) == "ssh"
    (root / "workshop.toml").write_text(
        '[workspace]\nextensions = ["docs"]\n[docs]\npublish = "carrier-pigeon"\n'
    )
    with pytest.raises(
        BaseException, match=r"docs\.publish is 'carrier-pigeon'; it takes one of pages"
    ):
        publish_seam(root)


def test_the_ssh_seam_skips_unconfigured(
    tmp_path: Path,
    capsys: object,
    monkeypatch: object,
) -> None:
    from livery.extensions.docs._site import _publish_ssh

    for name in ("DOCS_HOST", "DOCS_USER", "DOCS_ROOT"):
        monkeypatch.delenv(name, raising=False)  # type: ignore[attr-defined]
    root = _workspace(tmp_path)
    _publish_ssh(root)  # no raise is the contract
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "unconfigured" in out and "skipping" in out


def test_the_deploy_emitters_follow_the_seam(tmp_path: Path) -> None:
    from livery.workshop._ci_generate import generate

    root = _workspace(tmp_path)
    (root / "workshop.toml").write_text(
        '[workspace]\nextensions = ["docs"]\n[forge]\nkind = "github"\nowner = "acme"\n'
    )
    files = generate(root)
    # The site is published from the gate job's own entries on a push:
    # the pages seam has the gate job upload the site and adds the
    # forge's pages deploy after it; another seam publishes from the
    # gate job alone, through the seam or saying why not.
    assert ".github/workflows/docs.yml" not in files
    text = files[".github/workflows/ci.yml"]
    assert "--job=deploy" not in text and "  deploy:" not in text
    gate = text.split("\n  gate:\n")[1].split("\n  govern:\n")[0]
    assert "upload-pages-artifact" in gate
    pages = text.split("\n  pages:\n")[1]
    assert "deploy-pages" in pages and "github-pages" in pages
    (root / "workshop.toml").write_text(
        '[workspace]\nextensions = ["docs"]\n[docs]\npublish = "none"\n'
        '[forge]\nkind = "github"\nowner = "acme"\n'
    )
    files = generate(root)
    assert ".github/workflows/docs.yml" not in files
    text = files[".github/workflows/ci.yml"]
    assert "\n  pages:\n" not in text
    assert "deploy-pages" not in text and "github-pages" not in text
    gate = text.split("\n  gate:\n")[1].split("\n  govern:\n")[0]
    assert "download-artifact" in gate and "upload-pages-artifact" not in gate
    (root / "workshop.toml").write_text(
        '[workspace]\n[forge]\nkind = "gitea"\nowner = "acme"\n'
    )
    files = generate(root)
    # The gitea lane publishes from the gate job whatever the seam, and
    # the verb decides.
    assert ".gitea/workflows/docs.yml" not in files
    text = files[".gitea/workflows/ci.yml"]
    assert "\n  deploy:\n" not in text and "\n  pages:\n" not in text
    gate = text.split("\n  gate:\n")[1].split("\n  govern:\n")[0]
    assert "download-artifact" in gate
    (root / "workshop.toml").write_text(
        '[workspace]\n[forge]\nkind = "gitlab"\nowner = "acme"\n'
    )
    pipeline = generate(root)[".gitlab-ci.yml"]
    assert "pages:" in pipeline and "mv site public" in pipeline


def test_configure_asserts_pages_for_the_pages_seam(tmp_path: Path) -> None:
    from livery.forge.testing import FakeForge

    fake = FakeForge()
    fake.create_repo("acme", "home", private=False, description="t")
    repo = fake.repository("acme", "home")
    repo.ensure_pages(build_type="workflow")
    state = fake._repos[("acme", "home")]
    assert state.pages_build_type == "workflow"


# The package-owned nav. Refusals and fallbacks first.


def _nav(root: Path, package: str, body: str) -> Path:
    docs = root / "packages" / package / "docs"
    docs.mkdir(exist_ok=True)
    path = docs / "nav.toml"
    path.write_text(body)
    return path


def test_a_broken_nav_toml_refuses_with_the_file_named(tmp_path: Path) -> None:
    import pytest

    from livery.extensions.docs._site import package_nav

    root = _workspace(tmp_path)
    _nav(root, "core", "nav = [broken\n")
    core = next(p for p in discover_packages(root) if p.directory.name == "core")
    with pytest.raises(BaseException, match=r"nav\.toml is not valid TOML"):
        package_nav(core)
    _nav(root, "core", 'not_nav = "x"\n')
    with pytest.raises(BaseException, match="top-level `nav` list"):
        package_nav(core)
    _nav(root, "core", 'nav = [{ "A" = "a.md", "B" = "b.md" }]\n')
    with pytest.raises(BaseException, match="single"):
        package_nav(core)
    _nav(root, "core", 'nav = [{ "A" = 3 }]\n')
    with pytest.raises(BaseException, match="page path or a nested list"):
        package_nav(core)


def test_nav_drift_refuses_both_directions_and_restores(tmp_path: Path) -> None:
    import pytest

    from livery.extensions.docs._site import zensical_config

    root = _workspace(tmp_path)
    # Forced red, direction one: an entry naming a missing page.
    _nav(
        root,
        "core",
        'nav = [\n    { "Index" = "index.md" },\n'
        '    { "Guide" = "guide.md" },\n    { "Ghost" = "ghost.md" },\n]\n',
    )
    with pytest.raises(BaseException, match=r"ghost\.md"):
        zensical_config(root)
    # Forced red, direction two: an authored page absent from the nav.
    _nav(root, "core", 'nav = [\n    { "Index" = "index.md" },\n]\n')
    with pytest.raises(BaseException, match=r"guide\.md"):
        zensical_config(root)
    # Restored: the full nav renders green.
    _nav(
        root,
        "core",
        'nav = [\n    { "Index" = "index.md" },\n    { "Guide" = "guide.md" },\n]\n',
    )
    assert "guide.md" in zensical_config(root)


def test_generated_pages_are_exempt_both_ways(tmp_path: Path) -> None:
    from livery.extensions.docs._site import zensical_config

    root = _workspace(tmp_path)
    # A generator's page may be listed before it exists, and a
    # generated page on disk is never demanded in the nav.
    (root / "packages/core/docs/_generated").mkdir()
    (root / "packages/core/docs/_generated/tool.md").write_text("# tool\n")
    _nav(
        root,
        "core",
        'nav = [\n    { "Index" = "index.md" },\n'
        '    { "Guide" = "guide.md" },\n'
        "    # nav:begin tools\n"
        '    { "Ghost tool" = "_generated/ghost.md" },\n'
        "    # nav:end tools\n]\n",
    )
    config = zensical_config(root)
    assert "packages/core/ghost.md" in config


def test_a_glossary_under_includes_is_not_an_orphan(tmp_path: Path) -> None:
    # docs/includes/abbreviations.md is a source the extension set
    # auto-appends site-wide, never a standalone page, so the orphan
    # check must not demand a nav entry for it.
    from livery.extensions.docs._site import zensical_config

    root = _workspace(tmp_path)
    glossary = root / "packages/core/docs/includes"
    glossary.mkdir(parents=True)
    (glossary / "abbreviations.md").write_text("*[CI]: Continuous integration\n")
    config = zensical_config(root)
    assert "abbreviations.md" in config  # appended, not navigated


def test_the_authored_nav_drives_the_section(tmp_path: Path) -> None:
    import tomllib as toml

    from livery.extensions.docs._site import zensical_config

    root = _workspace(tmp_path)
    (root / "packages/core/docs/deep").mkdir()
    (root / "packages/core/docs/deep/one.md").write_text("# one\n")
    _nav(
        root,
        "core",
        "nav = [\n"
        '    { "Guide" = "guide.md" },\n'
        '    { "Start here" = "index.md" },\n'
        '    { "Deep" = [\n        { "One" = "deep/one.md" },\n    ] },\n'
        "]\n",
    )
    parsed = toml.loads(zensical_config(root))
    core = _package_entry(parsed["project"]["nav"], "core")["core"]
    # Authored order wins (no index-first re-sort), nesting survives,
    # and every path lands under the package's mount.
    assert core[0] == {"Guide": "packages/core/guide.md"}
    assert core[1] == {"Start here": "packages/core/index.md"}
    assert core[2] == {"Deep": [{"One": "packages/core/deep/one.md"}]}


def test_a_navless_package_keeps_the_enumerated_fallback(tmp_path: Path) -> None:
    import tomllib as toml

    from livery.extensions.docs._site import zensical_config

    root = _workspace(tmp_path)
    parsed = toml.loads(zensical_config(root))
    core = _package_entry(parsed["project"]["nav"], "core")["core"]
    assert core[0] == {"Index": "packages/core/index.md"}
    assert core[1] == {"guide": "packages/core/guide.md"}


def test_the_mount_leaves_nav_toml_behind(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _nav(
        root,
        "core",
        'nav = [\n    { "Index" = "index.md" },\n    { "Guide" = "guide.md" },\n]\n',
    )
    mount_package_docs(root)
    assert (root / "docs/packages/core/guide.md").is_file()
    assert not (root / "docs/packages/core/nav.toml").exists()


# The scoped preview. Refusal first.


def test_an_unknown_preview_package_names_the_known(tmp_path: Path) -> None:
    import pytest

    from livery.extensions.docs._site import named_package

    root = _workspace(tmp_path)
    with pytest.raises(BaseException, match="bare"):
        named_package(root, "ghost")


def test_the_scoped_config_carries_chrome_and_one_section(tmp_path: Path) -> None:
    import tomllib as toml

    from livery.extensions.docs._site import named_package, scoped_config

    root = _workspace(
        tmp_path,
        docs_table='extensions = ["docs", "mkdocstrings"]\n'
        '[docs]\ntitle = "Acme"\nsite-url = "https://docs.acme.example/home/"\n',
    )
    config = scoped_config(root, named_package(root, "core"))
    parsed = toml.loads(config)
    assert parsed["project"]["site_name"] == "Acme"
    # A preview is never published: no canonical URL.
    assert "site_url" not in parsed["project"]
    nav = parsed["project"]["nav"]
    assert nav[0] == {"Home": "index.md"}
    assert any("core" in entry for entry in nav)
    assert not any("bare" in entry for entry in nav)
    assert "Releases" not in config
    handler = parsed["project"]["plugins"]["mkdocstrings"]["handlers"]["python"]
    # Sources resolve from the preview directory back into the
    # workspace; cross-package references resolve through the
    # published site's inventory.
    assert handler["paths"] == ["../../packages/core/src"]
    assert "https://docs.acme.example/home/objects.inv" in handler["inventories"]


def test_the_preview_tree_rebuilds_whole_and_stays_scoped(tmp_path: Path) -> None:
    from livery.extensions.docs._site import (
        generate_changelog_pages,
        materialise_preview,
        named_package,
    )

    root = _workspace(tmp_path)
    generate_changelog_pages(root)
    _referenced(root)
    mount_package_docs(root)
    core = named_package(root, "core")
    stale = root / ".docs-preview" / "core" / "stale.md"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale\n")
    config = materialise_preview(root, core)
    assert not stale.exists()
    base = config.parent
    assert config.read_text().startswith("# Generated")
    assert (base / "docs" / "index.md").is_file()  # the chrome
    assert (base / "docs" / "packages" / "core" / "guide.md").is_file()
    assert (base / "docs" / "packages" / "core" / "api" / "index.md").is_file()
    # Only the scoped package's generated trees travel.
    assert not (base / "docs" / "packages" / "bare").exists()
    assert not (base / "docs" / "packages" / "bare" / "api").exists()


# The generator seam. Refusals and fallbacks first.


def _declare_generators(root: Path, package: str, table: str) -> None:
    contract = root / "packages" / package / "workshop.toml"
    contract.write_text(
        f'extensions = ["python"]\nname = "acme-{package}"\n\n[docs]\n{table}'
    )


def _package(root: Path, name: str):
    return next(p for p in discover_packages(root) if p.directory.name == name)


def _mount(root: Path) -> None:
    """Mount what *root* lists, so its phases' steps register as a verb finds them."""
    from livery.footman import _registry as registry
    from livery.workshop._extensions import mount_extensions

    with registry.capture():
        mount_extensions(root)


def _run_docs_phase(root: Path) -> None:
    """Run the docs phase's registered steps over *root*, as every docs verb does."""
    from livery.extensions.docs._site import DOCS_PHASE, run_site_phase

    run_site_phase(root, DOCS_PHASE)


def test_a_broken_generator_declaration_refuses(tmp_path: Path) -> None:
    import pytest

    from livery.extensions.docs._contract import package_generators

    root = _workspace(tmp_path)
    _declare_generators(root, "core", 'generators = "not-a-list"\n')
    with pytest.raises(BaseException, match=r"docs\.generators is a string"):
        package_generators(_package(root, "core"))
    _declare_generators(root, "core", "generators = [3]\n")
    with pytest.raises(BaseException, match=r"docs.generators\[\] is an integer"):
        package_generators(_package(root, "core"))
    _declare_generators(root, "core", 'generators = [{ requires = ["zsh"] }]\n')
    with pytest.raises(BaseException, match="verb name"):
        package_generators(_package(root, "core"))


def test_no_declaration_means_no_generators(tmp_path: Path) -> None:
    from livery.extensions.docs._contract import docs_requirements, package_generators

    root = _workspace(tmp_path)
    assert package_generators(_package(root, "core")) == []
    assert docs_requirements(root) == ()


def test_declarations_parse_and_requirements_union(tmp_path: Path) -> None:
    from livery.extensions.docs._contract import docs_requirements, package_generators

    root = _workspace(tmp_path)
    _declare_generators(
        root,
        "core",
        'generators = [\n    "docsgen.plain",\n'
        '    { verb = "docsgen.casts", requires = ["zsh", "fish"] },\n]\n',
    )
    _declare_generators(
        root, "bare", 'generators = [{ verb = "docsgen.other", requires = ["zsh"] }]\n'
    )
    assert package_generators(_package(root, "core")) == [
        ("docsgen.plain", ()),
        ("docsgen.casts", ("zsh", "fish")),
    ]
    # The union, sorted, so the rendered workflow is deterministic.
    assert docs_requirements(root) == ("fish", "zsh")


def test_a_failing_generator_names_the_verb_and_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil as shutil_module

    import pytest

    import livery.footman as footman

    root = _workspace(tmp_path)
    _declare_generators(root, "core", 'generators = ["docsgen.broken"]\n')
    _mount(root)
    monkeypatch.setattr(shutil_module, "which", lambda name: "/stub/fm")
    monkeypatch.setattr(footman, "run", lambda *a, **k: 3)
    with pytest.raises(BaseException, match=r"docsgen\.broken.*acme-core.*exited 3"):
        _run_docs_phase(root)


def test_a_missing_runner_refuses_with_the_remedy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil as shutil_module

    import pytest

    from livery.extensions.docs._steps import run_generators

    root = _workspace(tmp_path)
    _declare_generators(root, "core", 'generators = ["docsgen.any"]\n')
    monkeypatch.setattr(shutil_module, "which", lambda name: None)
    with pytest.raises(BaseException, match=r"setup\.sh"):
        run_generators(root)


def test_generators_run_in_declaration_order_at_the_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import shutil as shutil_module

    import livery.footman as footman

    root = _workspace(tmp_path)
    _declare_generators(root, "core", 'generators = ["docsgen.one", "docsgen.two"]\n')
    _mount(root)
    monkeypatch.setattr(shutil_module, "which", lambda name: "/stub/fm")
    calls: list[tuple[list[str], object]] = []

    def _record(argv: list[str], **kwargs: object) -> int:
        calls.append((list(argv), kwargs.get("cwd")))
        return 0

    monkeypatch.setattr(footman, "run", _record)
    _run_docs_phase(root)
    assert "  generators: docsgen.one, docsgen.two\n" in capsys.readouterr().out
    assert [argv[-1] for argv, _cwd in calls] == ["docsgen.one", "docsgen.two"]
    assert all(cwd == root for _argv, cwd in calls)


def test_the_docs_jobs_install_the_declared_requirements(tmp_path: Path) -> None:
    from livery.workshop._ci_generate import generate

    root = _workspace(tmp_path)
    (root / "docs" / "index.md").write_text("# Home\n")
    # The fallback first: no declaration, no install step anywhere.
    for kind in ("github", "gitea", "gitlab"):
        (root / "workshop.toml").write_text(
            f'[workspace]\n[forge]\nkind = "{kind}"\nowner = "acme"\n'
        )
        for content in generate(root).values():
            assert "System packages" not in content
            assert "apt-get install" not in content
    _declare_generators(
        root, "core", 'generators = [{ verb = "docsgen.casts", requires = ["zsh"] }]\n'
    )
    (root / "workshop.toml").write_text(
        '[workspace]\nextensions = ["docs"]\n[forge]\nkind = "github"\nowner = "acme"\n'
    )
    files = generate(root)
    gate = files[".github/workflows/ci.yml"]
    docs_job = gate.split("  docs:")[1].split("  gate:")[0]
    assert "sudo apt-get update -q && sudo apt-get install -y -q zsh" in docs_job
    # Only the docs-building jobs pay the install.
    check_job = gate.split("  docs:")[0]
    assert "apt-get" not in check_job
    (root / "workshop.toml").write_text(
        '[workspace]\nextensions = ["docs"]\n[forge]\nkind = "gitlab"\nowner = "acme"\n'
    )
    pipeline = generate(root)[".gitlab-ci.yml"]
    # Root in the container image: no sudo.
    assert "- apt-get update -q && apt-get install -y -q zsh" in pipeline
    assert "sudo" not in pipeline


def test_the_docs_seeds_live_once_in_the_base_tree() -> None:
    # Every package's birth writes package-base first, the one home of
    # the docs seeds; a copy in a template would shadow the base's and
    # rot separately.
    from livery.workshop._seeds import available_templates, template_files

    seeds = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "livery"
        / "workshop"
        / "content"
        / "seeds"
    )
    base = seeds / "package-base" / "docs"
    assert (base / "nav.toml").is_file()
    assert (base / "index.md.jinja").is_file()
    assert sorted(tree.name for tree in seeds.iterdir()) == ["package-base", "project"]
    for template in available_templates().values():
        assert not (template_files(template) / "docs").exists(), template.name


# The config surface and the theme. Refusals first.


def test_a_duplicate_abbreviation_refuses_and_restores(tmp_path: Path) -> None:
    from livery.extensions.docs._site import abbreviation_files, zensical_config

    root = _workspace(tmp_path)
    for package, definition in (("core", "One thing."), ("bare", "Another thing.")):
        includes = root / "packages" / package / "docs" / "includes"
        includes.mkdir(parents=True)
        (includes / "abbreviations.md").write_text(f"*[TERM]: {definition}\n")
    # Forced red: one term, two definitions, both packages named.
    with pytest.raises(BaseException, match=r"TERM.*acme-"):
        abbreviation_files(root)
    with pytest.raises(BaseException, match="TERM"):
        zensical_config(root)
    # Restored: agreeing definitions aggregate site-wide.
    (root / "packages/bare/docs/includes/abbreviations.md").write_text(
        "*[TERM]: One thing.\n"
    )
    files = abbreviation_files(root)
    assert files == [
        "packages/bare/docs/includes/abbreviations.md",
        "packages/core/docs/includes/abbreviations.md",
    ]
    config = zensical_config(root)
    assert "auto_append = [" in config
    assert "packages/core/docs/includes/abbreviations.md" in config


def test_broken_extras_declarations_refuse(tmp_path: Path) -> None:
    from livery.extensions.docs._site import package_docs_extras

    root = _workspace(tmp_path)
    _declare_generators(root, "core", 'extra-css = "not-a-list"\n')
    with pytest.raises(BaseException, match="extra-css"):
        package_docs_extras(_package(root, "core"))
    _declare_generators(root, "core", "extra-javascript = [3]\n")
    with pytest.raises(BaseException, match="extra-javascript"):
        package_docs_extras(_package(root, "core"))
    _declare_generators(
        root, "core", 'extra-javascript = [{ path = "a.js", rogue = true }]\n'
    )
    with pytest.raises(BaseException, match="rogue"):
        package_docs_extras(_package(root, "core"))


def test_declared_extras_render_at_the_mounted_paths(tmp_path: Path) -> None:
    from livery.extensions.docs._site import zensical_config

    root = _workspace(tmp_path)
    _declare_generators(
        root,
        "core",
        'extra-css = ["assets/core.css"]\n'
        'extra-javascript = [\n    "assets/plain.js",\n'
        '    { path = "assets/mod.js", type = "module", defer = true },\n]\n',
    )
    config = zensical_config(root)
    assert '"packages/core/assets/core.css",' in config
    assert '"packages/core/assets/plain.js",' in config
    assert (
        '{ path = "packages/core/assets/mod.js",'
        ' type = "module", defer = true },' in config
    )


def test_the_workspace_sheet_is_listed_last_while_it_exists(tmp_path: Path) -> None:
    root = _workspace(tmp_path, docs_table='extensions = ["docs"]\n')
    # The fallback first: no workspace sheet, the base's own alone.
    base = tomllib.loads(zensical_config(root))["project"]["extra_css"]
    assert base and all(
        sheet.startswith("_extensions/livery.workshop/") for sheet in base
    )
    assets = root / "docs" / "assets"
    assets.mkdir(parents=True)
    (assets / "site.css").write_text("/* mine */\n")
    (root / "packages" / "core" / "workshop.toml").write_text(
        'extensions = ["python"]\nname = "acme-core"\n'
        '[docs]\nextra-css = ["extra.css"]\n'
    )
    config = tomllib.loads(zensical_config(root))
    # A package's declared sheet comes before the workspace's own.
    assert config["project"]["extra_css"] == [
        *base,
        "packages/core/extra.css",
        "assets/site.css",
    ]
    # Deleting the sheet is the opt-out: no stale reference survives.
    (assets / "site.css").unlink()
    assert "site.css" not in zensical_config(root)


# The extensions' site assets. The extension without any first.


@pytest.fixture
def extensioned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A workspace listing the base and a theme extension whose content is faked."""
    from livery.extensions.docs import _site

    root = _workspace(tmp_path, docs_table='extensions = ["acme.theme"]\n')
    content = tmp_path / "acme-theme-content"
    (content / "docs" / "assets").mkdir(parents=True)
    (content / "docs" / "assets" / "theme.css").write_text("body { color: red }\n")
    real = _site.shipped_content

    def faked(start: Path | None = None) -> tuple[tuple[str, Path], ...]:
        return (*real(start), ("acme.theme", content))

    monkeypatch.setattr(_site, "shipped_content", faked)
    return root


def test_a_extension_without_site_assets_stages_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.extensions.docs import _site
    from livery.extensions.docs._site import extension_assets, stage_extension_assets

    root = _workspace(tmp_path, docs_table='extensions = ["acme.bare"]\n')
    monkeypatch.setattr(
        _site, "shipped_content", lambda start=None: (("acme.bare", tmp_path / "none"),)
    )
    assert extension_assets(root) == []
    assert stage_extension_assets(root) == []
    assert not (root / "docs" / "_extensions").exists()
    # An uninstalled extension ships nothing either.
    monkeypatch.setattr(_site, "shipped_content", lambda start=None: ())
    assert extension_assets(root) == []
    assert "extra_css" not in zensical_config(root)


def test_extension_assets_are_staged_whole_and_listed_in_cascade_order(
    tmp_path: Path, extensioned: Path
) -> None:
    from livery.extensions.docs._site import stage_extension_assets

    root = extensioned
    (root / "docs" / "assets").mkdir(parents=True)
    (root / "docs" / "assets" / "site.css").write_text("")
    assert stage_extension_assets(root) == ["livery.workshop", "acme.theme"]
    staged = root / "docs" / "_extensions"
    theme = staged / "acme.theme" / "assets" / "theme.css"
    assert theme.read_text() == "body { color: red }\n"
    assert (staged / "livery.workshop" / "assets" / "palette.css").is_file()
    config = tomllib.loads(zensical_config(root))
    assert config["project"]["extra_css"] == [
        "_extensions/livery.workshop/assets/palette.css",
        "_extensions/livery.workshop/assets/type.css",
        "_extensions/acme.theme/assets/theme.css",
        "assets/site.css",
    ]
    # Rebuilt whole: a sheet the extension no longer ships leaves no copy.
    (tmp_path / "acme-theme-content" / "docs" / "assets" / "theme.css").unlink()
    assert stage_extension_assets(root) == ["livery.workshop", "acme.theme"]
    assert not theme.exists()
    assert "theme.css" not in zensical_config(root)


# The theme block, a query a theme extension answers. Refusals first.


def test_a_theme_answer_outside_the_vocabulary_refuses_naming_the_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.extensions.docs._site import theme_values
    from livery.workshop._queries import QueryError

    fake_extensions(
        tmp_path, monkeypatch, site=_answering("docs.theme", '{ "font.body" = "Lato" }')
    )
    root = _workspace(tmp_path, docs_table='extensions = ["docs", "acme.site"]\n')
    # The asker names the query, once; the theme says what is wrong.
    with pytest.raises(
        QueryError,
        match=r"^docs\.theme: acme\.site answers the unknown key 'font\.body'; the"
        r" keys are language, font\.text, font\.code, features, palette$",
    ):
        theme_values(root)
    fake_extensions(tmp_path, monkeypatch, site=_answering("docs.theme", '"Lato"'))
    with pytest.raises(
        QueryError,
        match=r"^docs\.theme: acme\.site answers 'Lato', and an answer is a table of"
        r" the theme's keys$",
    ):
        theme_values(root)


def test_two_theme_answers_setting_one_key_refuse_unless_one_requires_the_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.extensions.docs._site import theme_values
    from livery.workshop._queries import QueryError

    fake_extensions(
        tmp_path,
        monkeypatch,
        bold=_answering("docs.theme", '{ "font.text" = "Lato", "language" = "nl" }'),
        calm=_answering("docs.theme", '{ "font.text" = "Inter Light" }'),
    )
    root = _workspace(
        tmp_path, docs_table='extensions = ["acme.bold", "acme.calm", "docs"]\n'
    )
    with pytest.raises(
        QueryError,
        match=r"^docs\.theme: acme\.bold and acme\.calm both set font\.text, and"
        r" neither requires the other; one of them sets it, or one requires the"
        r" other$",
    ):
        theme_values(root)
    # The one that requires the other wins the key, whatever the list's
    # order, and a key only one sets is that one's.
    fake_extensions(
        tmp_path,
        monkeypatch,
        bold=_answering(
            "docs.theme",
            '{ "font.text" = "Lato", "language" = "nl" }',
            requires="acme.calm",
        ),
    )
    for listed in ('["acme.bold", "docs"]', '["docs", "acme.bold"]'):
        _relist(root, listed)
        values = theme_values(root)
        assert values["font.text"] == "Lato" and values["language"] == "nl"


def test_a_theme_answer_changes_the_fonts_and_keeps_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(
        tmp_path, monkeypatch, site=_answering("docs.theme", '{ "font.text" = "Lato" }')
    )
    root = _workspace(tmp_path)
    theme = tomllib.loads(zensical_config(root))["project"]["theme"]
    assert theme["font"] == {"text": "Inter", "code": "Fira Code"}
    _relist(root, '["docs", "acme.site"]')
    theme = tomllib.loads(zensical_config(root))["project"]["theme"]
    assert theme["font"] == {"text": "Lato", "code": "Fira Code"}
    assert theme["language"] == "en" and len(theme["palette"]) == 3
    assert "navigation.tabs" in theme["features"]
    assert theme["custom_dir"] == "overrides"
    # The scoped preview follows, and taking the extension off the list
    # restores the base's.
    preview = tomllib.loads(scoped_config(root, named_package(root, "core")))
    assert preview["project"]["theme"]["font"]["text"] == "Lato"
    _relist(root, '["docs"]')
    restored = tomllib.loads(zensical_config(root))["project"]["theme"]
    assert restored["font"]["text"] == "Inter"


def test_the_standard_extension_set_is_emitted(tmp_path: Path) -> None:
    from livery.extensions.docs._site import zensical_config

    root = _workspace(tmp_path)
    config = zensical_config(root)
    assert "[project.markdown_extensions.pymdownx.snippets]" in config
    assert "check_paths = true" in config
    # Every package's snippet-source home joins the search path.
    assert '"packages/core/_generated",' in config
    assert 'name = "mermaid"' in config
    assert "[project.markdown_extensions.pymdownx.tabbed]" in config
    assert "[project.markdown_extensions.pymdownx.arithmatex]" in config
    assert "[project.theme]" in config
    assert 'custom_dir = "overrides"' in config


def test_the_override_template_follows_the_workspace_card_then_the_shipped_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.extensions.docs import _site
    from livery.extensions.docs._site import overrides_template

    root = _workspace(
        tmp_path,
        docs_table='[docs]\ntitle = "Acme"\ndescription = "Acme, described."\n',
    )
    # The fallback first: no card anywhere, no image tags, and the
    # plain summary card instead of the large one.
    monkeypatch.setattr(_site, "extension_assets", lambda root: [])
    rendered = overrides_template(root)
    assert "og:image" not in rendered
    assert 'content="summary"' in rendered
    assert "og:site_name" in rendered
    # The docs extension ships a card, staged under its own directory.
    assets = tmp_path / "shipped"
    assets.mkdir()
    (assets / "og-card.png").write_bytes(b"\x89PNG")
    monkeypatch.setattr(_site, "extension_assets", lambda root: [("docs", assets)])
    rendered = overrides_template(root)
    assert 'config.site_url ~ "_extensions/docs/assets/og-card.png"' in rendered
    assert 'content="summary_large_image"' in rendered
    # The workspace's own card wins.
    (root / "docs" / "assets").mkdir(parents=True)
    (root / "docs" / "assets" / "og-card.png").write_bytes(b"\x89PNG")
    rendered = overrides_template(root)
    assert 'config.site_url ~ "assets/og-card.png"' in rendered
    assert 'og:image" content="{{ image }}"' in rendered
    # The alt line is the instance's own description.
    assert "Acme, described." in rendered


def test_the_docs_extension_ships_a_card() -> None:
    from livery.workshop._extensions import extension_content

    content = extension_content("docs")
    assert content is not None
    assert (content / "docs" / "assets" / "og-card.png").is_file()


def test_the_scoped_preview_carries_the_surface_two_levels_up(
    tmp_path: Path,
) -> None:
    from livery.extensions.docs._site import named_package, scoped_config

    root = _workspace(tmp_path)
    includes = root / "packages" / "core" / "docs" / "includes"
    includes.mkdir(parents=True)
    (includes / "abbreviations.md").write_text("*[TERM]: One thing.\n")
    config = scoped_config(root, named_package(root, "core"))
    # Repo-anchored sources resolve from the preview directory.
    assert '"../../packages/core/_generated",' in config
    assert '"../../packages/core/docs/includes/abbreviations.md",' in config
    assert "[project.theme]" in config


# The coverage seam. Refusals and the absent fallback first.


def test_broken_coverage_declarations_refuse(tmp_path: Path) -> None:
    from livery.extensions.docs._site import package_coverage_reports

    root = _workspace(tmp_path)
    _declare_generators(root, "core", 'coverage = "not-a-list"\n')
    with pytest.raises(BaseException, match=r"docs\.coverage is a string"):
        package_coverage_reports(_package(root, "core"))
    _declare_generators(root, "core", 'coverage = [{ label = "x" }]\n')
    with pytest.raises(BaseException, match="label"):
        package_coverage_reports(_package(root, "core"))
    _declare_generators(
        root, "core", 'coverage = [{ label = "x", path = "../../etc" }]\n'
    )
    with pytest.raises(BaseException, match="inside the package"):
        package_coverage_reports(_package(root, "core"))


def test_a_missing_report_states_the_absence_and_stays_green(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.extensions.docs._site import generate_coverage_pages

    # A desk's build, wherever the test runs: on a push to main the
    # page frames the tree the gate job renders later instead.
    monkeypatch.setattr("livery.workshop.ci_run", lambda: None)
    root = _workspace(tmp_path)
    _declare_generators(
        root, "core", 'coverage = [{ label = "Python", path = "htmlcov" }]\n'
    )
    assert generate_coverage_pages(root) == ["core"]
    page = (root / "packages/core/docs/_generated/coverage.md").read_text()
    assert "was not produced in this build" in page
    assert "iframe" not in page


def test_on_a_push_the_page_frames_a_tree_the_gate_job_renders_later(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from types import SimpleNamespace

    from livery.extensions.docs import _site as _docs
    from livery.workshop import PhaseContext
    from livery.workshop._steps import StepRecord, register_step

    root = _workspace(tmp_path)
    _declare_generators(
        root, "core", 'coverage = [{ label = "Python", path = "htmlcov" }]\n'
    )
    # No site built yet: nothing to refresh, and no tree to frame.
    assert _docs.refresh_site_coverage(root) == []
    monkeypatch.setattr("livery.workshop.ci_run", lambda: SimpleNamespace(event="push"))
    assert _docs.generate_coverage_pages(root) == ["core"]
    page = (root / "packages/core/docs/_generated/coverage.md").read_text()
    assert 'src="coverage/python/index.html"' in page
    # The built site: the publish puts a report with no tree into it as
    # a page saying so, where the page frames it.
    site = root / "site"
    (site / "packages" / "core" / "coverage").mkdir(parents=True)
    (site / "index.html").write_text("<h1>site</h1>\n")
    monkeypatch.chdir(root)
    _docs.docs_publish()
    placed = site / "packages/core/coverage/python/index.html"
    assert "No measured data for core (Python)" in placed.read_text()
    # A step of the deploy phase renders the tree after the build, and
    # the publish puts it there.
    report = root / "packages/core/htmlcov"

    def render(ctx: PhaseContext) -> None:
        del ctx
        report.mkdir(exist_ok=True)
        (report / "index.html").write_text("<h1>report</h1>\n")

    register_step(StepRecord("tree", "deploy", render, extension="acme.tree"))
    _docs.docs_publish()
    assert placed.read_text() == "<h1>report</h1>\n"
    out = capsys.readouterr().out
    assert out.count("  site refreshed for core\n") == 2
    assert "  publish seam is none: skipping by declaration\n" in out


#: A workspace listing the site and the extension that draws its
#: coverage pages, and a measurer, so that extension is awake and adds
#: its step to the docs phase.
_DRAWN = 'extensions = ["docs", "acme.meter", "coverage"]\n'


def test_a_workspace_that_lists_no_page_drawer_states_the_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.extensions.cmake._readings import Reading, write_part
    from livery.extensions.docs import _site as _docs
    from livery.workshop._steps import steps_by_name
    from workshop_coverage_meter import install_meter

    install_meter(tmp_path, monkeypatch)
    monkeypatch.setattr("livery.workshop._ci_context.run_context", lambda: None)
    monkeypatch.setattr("livery.workshop.ci_run", lambda: None)
    root = _workspace(tmp_path)
    _declare_generators(
        root, "core", 'coverage = [{ label = "Python", path = "htmlcov" }]\n'
    )
    # Measured data the drawer reads itself, so the listing alone differs
    # below: a native measurer's lines.
    source = root / "packages" / "core" / "src" / "acme" / "core" / "mod.py"
    source.write_text("a = 1\n")
    write_part(
        root,
        "packages/core",
        {"packages/core/src/acme/core/mod.py": Reading(lines={1: 1})},
        "gcov",
    )
    report = root / "packages" / "core" / "htmlcov"
    page = root / "packages" / "core" / "docs" / "_generated" / "coverage.md"
    # Docs alone, and docs beside an awake coverage whose `for` list
    # leaves docs out, so its [for.docs] table adds no step: nothing in
    # the docs phase draws, and the declared report states the absence.
    for listed in (
        '["docs"]',
        '["docs", "acme.meter", { name = "coverage", for = [] }]',
    ):
        (root / "workshop.toml").write_text(f"[workspace]\nextensions = {listed}\n")
        _mount(root)
        assert "docs.coverage" not in steps_by_name()
        _run_docs_phase(root)
        assert not report.exists()
        assert _docs.generate_coverage_pages(root) == ["core"]
        assert "was not produced in this build" in page.read_text()
    # Both listed, coverage's step draws from the same data, and the
    # page frames what it drew.
    (root / "workshop.toml").write_text(f"[workspace]\n{_DRAWN}")
    _mount(root)
    _run_docs_phase(root)
    assert (report / "index.html").is_file()
    assert _docs.generate_coverage_pages(root) == ["core"]
    assert 'src="coverage/python/index.html"' in page.read_text()


def test_the_deploy_phase_draws_main_s_record_where_the_legs_left_no_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.extensions.cmake._readings import Reading, write_part
    from livery.extensions.coverage import _pages
    from workshop_coverage_meter import stored_unit

    root = _workspace(tmp_path, docs_table='extensions = ["docs", "coverage"]\n')
    _declare_generators(
        root, "core", 'coverage = [{ label = "Python", path = "htmlcov" }]\n'
    )
    source = root / "packages" / "core" / "src" / "core" / "mod.c"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("int a = 1;\nint b = 2;\n")
    # The docs phase's step pulls nothing: no data, nothing drawn.
    monkeypatch.setattr("livery.workshop._ci_context.run_context", lambda: None)
    assert _pages.draw(root, merged=False) == []
    # The deploy phase's step draws the store's units in the legs' place:
    # the parts a leg made, as main's record keeps them.
    written = write_part(
        root,
        "packages/core",
        {"packages/core/src/core/mod.c": Reading(lines={1: 1, 2: 0})},
        "gcov",
    )
    stored = stored_unit("packages/core", (json.loads(written.read_text()),))
    written.unlink()
    monkeypatch.setattr(
        "livery.extensions.coverage._pages._stored",
        lambda root: (
            {"check-a": {"packages/core": stored}},
            ["main/check-b: nothing recorded"],
        ),
    )
    # A declared package the data never touched states the absence.
    _declare_generators(
        root, "bare", 'coverage = [{ label = "Python", path = "htmlcov" }]\n'
    )
    assert _pages.draw(root, merged=True) == ["core"]
    out = capsys.readouterr().out
    assert "  coverage: main/check-b: nothing recorded\n" in out
    assert "bare: no measured data; its page states the absence" in out
    assert (root / "packages" / "core" / "htmlcov" / "index.html").is_file()
    assert not (root / "packages" / "bare" / "htmlcov").exists()


def _region_rows(rows: list[dict[str, str]], noun: str) -> list[tuple[str, ...]]:
    """The rows of a region index for the fixture's file, as a reader reads them.

    Each row: the region, the line its link lands on, then statements,
    missing, excluded, branches, partial, and the total coverage.
    """
    return [
        (
            row[noun],
            row["href"].partition("#")[2],
            row["statements"],
            row["missing"],
            row["excluded"],
            row["branches"],
            row["partial"],
            row["coverage"],
        )
        for row in rows
        if row.get("File", "").endswith("measured.py")
    ]


def test_a_partial_line_s_page_names_the_jump_that_never_ran(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # coverage.py's arcs reach the pages through the pytest extension's
    # reduction, a distribution of its own: a run of this wheel alone
    # has none.
    pytest.importorskip("livery.extensions.pytest")
    from livery.extensions.coverage import _pages

    root = _workspace(
        tmp_path, docs_table='extensions = ["docs", "pytest", "coverage"]\n'
    )
    _declare_generators(
        root, "core", 'coverage = [{ label = "Python", path = "htmlcov" }]\n'
    )
    measure(root / "packages" / "core" / "src" / "acme" / "core" / "measured.py", root)
    monkeypatch.setattr("livery.workshop._ci_context.run_context", lambda: None)
    assert _pages.draw(root, merged=False) == ["core"]
    report = root / "packages" / "core" / "htmlcov"
    # Each function and class has its row, narrowed to its own lines and
    # linked to its first; the lines outside every one have a row too.
    functions = read_page(report / "function_index.html").rows
    assert _region_rows(functions, "function") == [
        ("Tally.__init__", "t2", "1", "0", "0", "0", "0", "100%"),
        ("Tally.note", "t5", "2", "0", "0", "2", "1", "75%"),
        ("total", "t10", "6", "2", "0", "4", "2", "60%"),
        ("unused", "t19", "0", "0", "1", "0", "0", "100%"),
        ("(no function)", "", "9", "0", "1", "2", "0", "100%"),
    ]
    assert _region_rows(read_page(report / "class_index.html").rows, "class") == [
        ("Tally", "t1", "3", "0", "0", "2", "1", "80%"),
        ("(no class)", "", "15", "2", "2", "6", "2", "81%"),
    ]
    # The file's page, as the index links it: each partial line carries
    # the partial category and names its untaken jump, with the sentence
    # that says why. The line whose untaken jump `# pragma: no branch`
    # waives is not partial.
    page = next(row["href"] for row in functions if row.get("function") == "total")
    lines = read_page(report / page.partition("#")[0]).lines
    partial = {number: line for number, line in lines.items() if line.category == "par"}
    assert sorted(partial) == [6, 12, 14]
    for number, jump, sentence in (
        (
            6,
            "6 ↛ exit",
            "line 6 didn't return from function 'note' because the condition on"
            " line 6 was always true",
        ),
        (
            12,
            "12 ↛ 13",
            "line 12 didn't jump to line 13 because the loop on line 12 never started",
        ),
        (
            14,
            "14 ↛ 15",
            "line 14 didn't jump to line 15 because the condition on line 14 was"
            " never true",
        ),
    ):
        assert partial[number].jumps == [jump]
        assert partial[number].whys == [sentence]


def test_a_generated_page_at_an_authored_pages_url_refuses_naming_both(
    tmp_path: Path,
) -> None:
    from livery.extensions.docs._site import published_url

    assert published_url("index.md") == ""
    assert published_url("api.md") == "api/"
    assert published_url("api/index.md") == "api/"
    assert published_url("api/_host.md") == "api/_host/"
    root = _workspace(tmp_path)
    docs = root / "packages" / "core" / "docs"
    (docs / "api.md").write_text("# The curated reference\n")
    generated = docs / "_generated" / "api"
    generated.mkdir(parents=True)
    (generated / "index.md").write_text("# `acme.core`\n")
    with pytest.raises(
        _FAILURES,
        match=r"_generated/api/index\.md and the authored api\.md would publish at"
        r" one path",
    ):
        mount_package_docs(root, full=True)
    # The same path is the same refusal.
    (docs / "api.md").unlink()
    (docs / "api").mkdir()
    (docs / "api" / "index.md").write_text("# Mine\n")
    with pytest.raises(_FAILURES, match=r"and the authored api/index\.md would"):
        mount_package_docs(root, full=True)
    # Apart, both publish.
    (docs / "api" / "index.md").rename(docs / "reference.md")
    (docs / "api").rmdir()
    assert "core" in mount_package_docs(root, full=True)


def test_a_build_that_left_no_site_is_red(tmp_path: Path) -> None:
    from livery.extensions.docs._site import require_site

    with pytest.raises(
        _FAILURES, match=r"left no .*site/index\.html: nothing to publish"
    ):
        require_site(tmp_path)
    (tmp_path / "site").mkdir()
    with pytest.raises(_FAILURES, match="nothing to publish"):
        require_site(tmp_path)
    (tmp_path / "site" / "index.html").write_text("<html></html>")
    require_site(tmp_path)


def test_a_checkout_without_the_site_sources_is_refused_before_the_build(
    tmp_path: Path,
) -> None:
    from livery.extensions.docs._site import require_sources, source_summary

    with pytest.raises(_FAILURES, match=r"docs, workshop\.toml missing"):
        require_sources(tmp_path)
    (tmp_path / "docs").mkdir()
    with pytest.raises(_FAILURES, match=r"workshop\.toml missing"):
        require_sources(tmp_path)
    (tmp_path / "workshop.toml").write_text("[workspace]\n")
    require_sources(tmp_path)
    assert source_summary(tmp_path) == (
        "  site sources: 0 page(s) under docs/, the config assembled"
    )
    (tmp_path / "docs" / "index.md").write_text("# hi\n")
    (tmp_path / "docs" / "deep").mkdir()
    (tmp_path / "docs" / "deep" / "page.md").write_text("# deep\n")
    assert source_summary(tmp_path).startswith("  site sources: 2 page(s)")


def test_the_generators_output_is_printed_in_ci_only() -> None:
    from livery.extensions.docs._site import generator_lines

    assert generator_lines("Build started\n", "", in_ci=False) == []
    assert generator_lines("", "", in_ci=True) == []
    both = generator_lines(
        "Build started\nBuild finished in 0.04s\n", "warn\n", in_ci=True
    )
    assert both == [
        "    Build started",
        "    Build finished in 0.04s",
        "    warn",
    ]


def test_a_present_report_copies_whole_and_iframes(tmp_path: Path) -> None:
    from livery.extensions.docs._site import (
        generate_coverage_pages,
        mount_package_docs,
    )

    root = _workspace(tmp_path)
    _declare_generators(
        root,
        "core",
        'coverage = [\n    { label = "Python", path = "htmlcov" },\n'
        '    { label = "Native (fixture)", path = "fixtures/native" },\n]\n',
    )
    for tree in ("htmlcov", "fixtures/native"):
        report = root / "packages" / "core" / tree
        report.mkdir(parents=True)
        (report / "index.html").write_text("<h1>report</h1>\n")
        (report / "style.css").write_text("body {}\n")
    assert generate_coverage_pages(root) == ["core"]
    mount_package_docs(root)
    mount = root / "docs/packages/core"
    page = (mount / "coverage.md").read_text()
    assert 'src="coverage/python/index.html"' in page
    assert 'src="coverage/native-fixture/index.html"' in page
    assert (mount / "coverage/python/style.css").is_file()
    # The copy rebuilds whole in the package's tree: a stale file never lingers.
    generated = root / "packages/core/docs/_generated"
    stale = generated / "coverage/python/gone.html"
    stale.write_text("stale\n")
    generate_coverage_pages(root)
    assert not stale.exists()


def test_the_coverage_nav_entry_appends_like_the_changelog(
    tmp_path: Path,
) -> None:
    from livery.extensions.docs._site import zensical_config

    root = _workspace(tmp_path)
    _declare_generators(
        root, "core", 'coverage = [{ label = "Python", path = "htmlcov" }]\n'
    )
    config = zensical_config(root)
    assert '{ "Coverage" = "packages/core/coverage.md" },' in config


def test_the_coverage_page_stays_out_of_the_context_file(tmp_path: Path) -> None:
    from livery.extensions.docs._llms import _machine_page

    members = ("core", "extensions/widgets")
    # The refusals first: an authored page, and an authored page in a
    # subdirectory that shares a generated page's name.
    assert not _machine_page("packages/core/guide.md", members)
    assert not _machine_page("packages/core/guide/changelog.md", members)
    assert _machine_page("packages/core/coverage.md", members)
    # A package in a group directory is read like any other.
    assert _machine_page("packages/extensions/widgets/changelog.md", members)
    assert _machine_page("packages/extensions/widgets/api/widgets.md", members)


def test_the_emitted_plumbing_follows_the_declaration(tmp_path: Path) -> None:
    from livery.workshop._ci_generate import generate

    root = _workspace(tmp_path)
    (root / "docs" / "index.md").write_text("# Home\n")
    (root / "workshop.toml").write_text(
        '[workspace]\nextensions = ["docs"]\n'
        '[forge]\nkind = "github"\nowner = "acme"\n'
        '[docs]\npublish = "pages"\n'
    )
    # The fallback first: nothing declared, nothing plumbed, the
    # deploy still triggers on push.
    files = generate(root)
    # The gate job's floors union always downloads; the DOCS job only
    # plumbs when a package declares a report.
    bare_docs_job = (
        files[".github/workflows/ci.yml"].split("  docs:")[1].split("  gate:")[0]
    )
    assert "coverage-data" not in bare_docs_job
    assert "needs:" not in bare_docs_job
    assert ".github/workflows/docs.yml" not in files
    _declare_generators(
        root, "core", 'coverage = [{ label = "Python", path = "htmlcov" }]\n'
    )
    (root / "workshop.toml").write_text(
        '[workspace]\nextensions = ["docs"]\n'
        '[forge]\nkind = "github"\nowner = "acme"\n'
        '[docs]\npublish = "pages"\n'
    )
    files = generate(root)
    gate = files[".github/workflows/ci.yml"]
    docs_job = gate.split("  docs:")[1].split("  gate:")[0]
    # The pull request's docs job builds beside the legs and never
    # waits for their data: nobody reads a pull request's coverage
    # page. The gate job on main renders the trees from main's coverage
    # record on the store, so no job downloads coverage.
    assert "needs:" not in docs_job and "coverage-*" not in docs_job
    gate_job = gate.split("\n  gate:\n")[1].split("\n  govern:\n")[0]
    assert "coverage" not in gate_job
    assert "workflow_run" not in gate and "run-id:" not in gate


def test_the_mount_merges_the_generated_tree_and_keeps_every_link_true(
    tmp_path: Path,
) -> None:
    """``_generated`` is a directory on disk and never a published path."""
    root = _workspace(tmp_path)
    docs = root / "packages/core/docs"
    (docs / "guide.md").write_text("see [the api](_generated/api.md)\n")
    generated = docs / "_generated"
    (generated / "tasks" / "docs").mkdir(parents=True)
    (generated / "api.md").write_text("back to [the guide](../guide.md)\n")
    (generated / "tasks" / "index.md").write_text(
        "[guide](../../guide.md) [docs](docs/build.md)\n"
    )
    (generated / "tasks" / "docs" / "build.md").write_text(
        "[up](../index.md) [guide](../../../guide.md)\n"
    )
    assert mount_package_docs(root) == ["core"]
    mount = root / MOUNT / "core"
    assert not (mount / "_generated").exists()
    assert (mount / "guide.md").read_text() == "see [the api](api.md)\n"
    assert (mount / "api.md").read_text() == "back to [the guide](guide.md)\n"
    assert (mount / "tasks" / "index.md").read_text() == (
        "[guide](../guide.md) [docs](docs/build.md)\n"
    )
    assert (mount / "tasks" / "docs" / "build.md").read_text() == (
        "[up](../index.md) [guide](../../guide.md)\n"
    )
    # A generated page an authored page already holds the path of refuses.
    (docs / "api.md").write_text("# authored\n")
    with pytest.raises(_FAILURES, match=r"_generated/api\.md and the authored api\.md"):
        mount_package_docs(root)


def test_no_published_path_carries_generated(tmp_path: Path) -> None:
    """The scheme, pinned: the config's every path and the alias tree are clean."""
    from livery.extensions.docs._site import RELEASES, generate_release_pages

    root = _workspace(tmp_path)
    _referenced(root)
    (root / "packages/core/docs/_generated/tool.md").write_text("# tool\n")
    config = zensical_config(root)
    parsed = tomllib.loads(config)
    # Every nav leaf and every asset path publishes clean; the snippet
    # base paths name the source tree, which is where the name lives.
    from livery.extensions.docs._site import _nav_leaves

    assert not [
        leaf for leaf in _nav_leaves(parsed["project"]["nav"]) if "_generated" in leaf
    ]
    assert not [
        entry
        for entry in parsed["project"].get("extra_css", [])
        if "_generated" in str(entry)
    ]
    core = _package_entry(parsed["project"]["nav"], "core")
    api = next(part for part in core["core"] if "API" in part)
    assert api["API"][0]["acme.core"].startswith("packages/core/api/")
    assert RELEASES == "docs/releases"
    generate_release_pages(root)
    assert (root / "docs/releases/index.md").is_file() or not (
        root / "docs/releases"
    ).exists()


# Phase 2 of the modular docs plan: the machine sections are marker blocks.


def _nav_labels(config: str, name: str) -> list[str]:
    parsed = tomllib.loads(config)
    section = _package_entry(parsed["project"]["nav"], name)
    return [next(iter(part)) for part in section[name]]


def test_an_unpaired_nav_block_refuses_naming_it(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    nav = root / "packages/core/docs/nav.toml"
    nav.write_text(
        'nav = [\n    { "Index" = "index.md" },\n    { "guide" = "guide.md" },\n'
        "    # nav:begin changelog\n]\n"
    )
    with pytest.raises(_FAILURES, match="nav block 'changelog' begins without ending"):
        zensical_config(root)
    nav.write_text(
        'nav = [\n    { "Index" = "index.md" },\n    { "guide" = "guide.md" },\n'
        "    # nav:end api\n]\n"
    )
    with pytest.raises(_FAILURES, match="nav block 'api' ends without beginning"):
        zensical_config(root)


def test_a_generators_own_block_renders_its_entries_where_placed(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    _referenced(root)
    (root / "packages/core/docs/_generated/tools").mkdir(parents=True)
    (root / "packages/core/docs/nav.toml").write_text(
        'nav = [\n    { "Index" = "index.md" },\n'
        "    # nav:begin tools\n"
        '    { "Tools" = [\n'
        '        { "ruff" = "_generated/tools/ruff.md" },\n'
        "    ] },\n"
        "    # nav:end tools\n"
        '    { "guide" = "guide.md" },\n]\n'
    )
    config = zensical_config(root)
    assert _nav_labels(config, "core") == ["Index", "Tools", "guide", "API"]
    assert '{ "ruff" = "packages/core/tools/ruff.md" }' in config


def test_a_placed_block_fills_where_its_markers_sit(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _referenced(root)
    (root / "packages/core/CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n")
    (root / "packages/core/docs/nav.toml").write_text(
        'nav = [\n    { "Index" = "index.md" },\n'
        "    # nav:begin changelog\n    # nav:end changelog\n"
        '    { "guide" = "guide.md" },\n'
        "    # nav:begin api\n    # nav:end api\n"
        "]\n"
    )
    assert _nav_labels(zensical_config(root), "core") == [
        "Index",
        "Changelog",
        "guide",
        "API",
    ]


def test_unplaced_blocks_land_in_order_around_the_tasks_block(tmp_path: Path) -> None:
    """Changelog and Coverage ahead of the tasks block, the API after everything."""
    root = _workspace(tmp_path)
    _referenced(root)
    (root / "packages/core/CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n")
    _declare_generators(
        root, "core", 'coverage = [{ label = "Python", path = "htmlcov" }]\n'
    )
    (root / "packages/core/docs/nav.toml").write_text(
        'nav = [\n    { "Index" = "index.md" },\n    { "guide" = "guide.md" },\n'
        "    # nav:begin tasks\n"
        '    { "Tasks" = [\n'
        '        { "Overview" = "_generated/tasks/index.md" },\n'
        "    ] },\n"
        "    # nav:end tasks\n]\n"
    )
    config = zensical_config(root)
    assert _nav_labels(config, "core") == [
        "Index",
        "guide",
        "Changelog",
        "Coverage",
        "Tasks",
        "API",
    ]
    assert '{ "Overview" = "packages/core/tasks/index.md" }' in config
    # Without a tasks block the sections append in the same order.
    (root / "packages/core/docs/nav.toml").write_text(
        'nav = [\n    { "Index" = "index.md" },\n    { "guide" = "guide.md" },\n]\n'
    )
    assert _nav_labels(zensical_config(root), "core") == [
        "Index",
        "guide",
        "Changelog",
        "Coverage",
        "API",
    ]
    # The enumerated fallback keeps it too.
    (root / "packages/core/docs/nav.toml").unlink()
    assert _nav_labels(zensical_config(root), "core")[-3:] == [
        "Changelog",
        "Coverage",
        "API",
    ]


@pytest.fixture
def no_development_section(monkeypatch: pytest.MonkeyPatch) -> None:
    """Assemble this repository's site config without its development section.

    The development section renders the verbs fragment through this
    repository's own fm, which syncs a checkout that never synced: a
    release leg's fresh clone, whose links would then point into the
    leg's scratch venv. The tests that take this read the packages'
    sections and the handler, never the development section.
    """
    monkeypatch.setattr(
        "livery.extensions.docs._site.development_nav_lines", lambda root: []
    )


def test_this_workspaces_sidebars_read_changelog_then_tasks_then_api(
    no_development_section: None,
) -> None:
    """The order holds among the sections a checkout has.

    The tasks block is emitted by its generator, so a fresh checkout
    may not carry it yet.
    """
    config = zensical_config(ROOT)
    for name in ("footman", "workshop", "forge"):
        labels = _nav_labels(config, name)
        present = [label for label in ("Changelog", "Tasks", "API") if label in labels]
        assert "Changelog" in present, (name, labels)
        assert [labels.index(label) for label in present] == sorted(
            labels.index(label) for label in present
        ), (name, labels)


#: The python handler's options as this repository's site renders them.
_HANDLER_OPTIONS = {
    "docstring_style": "google",
    "show_if_no_docstring": True,
    "show_root_heading": True,
    "show_root_full_path": True,
    "separate_signature": True,
    "show_signature_annotations": True,
    "signature_crossrefs": True,
    "members_order": "source",
    "merge_init_into_class": True,
    "summary": True,
    "heading_level": 2,
}

#: The inventory every python reference here resolves against.
_PYTHON_INVENTORY = "https://docs.python.org/3/objects.inv"


def test_this_workspaces_handler_reads_the_documented_members_and_toolrooms_stubs(
    no_development_section: None,
) -> None:
    """The handler of this repository's site, and of toolroom's preview.

    A path for each member that ships python modules, in the packages'
    order, with toolroom's rendered stubs after its sources. footman
    declines its reference, and the members that ship no module give
    no path: clang-format, mypy, pyrefly, ruff and ty. No extension here
    answers the members policy, so the options carry no filter.
    """
    site = tomllib.loads(zensical_config(ROOT))
    assert site["project"]["plugins"]["mkdocstrings"]["handlers"] == {
        "python": {
            "paths": [
                "packages/extensions/basedpyright/src",
                "packages/extensions/changelog/src",
                "packages/extensions/clang-tidy/src",
                "packages/extensions/claude/src",
                "packages/extensions/pytest/src",
                "packages/forge/src",
                "packages/git/src",
                "packages/strongroom/src",
                "packages/toolroom/src",
                "packages/toolroom/docs/_generated/stubs",
                "packages/toolroom-bench/src",
                "packages/toolroom-store/src",
                "packages/workshop/src",
            ],
            "inventories": [_PYTHON_INVENTORY],
            "options": _HANDLER_OPTIONS,
        }
    }
    # The preview reads the same paths from two levels down, and
    # resolves the rest of the site through its published inventory.
    preview = tomllib.loads(scoped_config(ROOT, named_package(ROOT, "toolroom")))
    assert preview["project"]["plugins"]["mkdocstrings"]["handlers"] == {
        "python": {
            "paths": [
                "../../packages/toolroom/src",
                "../../packages/toolroom/docs/_generated/stubs",
            ],
            "inventories": [
                _PYTHON_INVENTORY,
                "https://docs.willem.net/livery/objects.inv",
            ],
            "options": _HANDLER_OPTIONS,
        }
    }


# Phase 3 of the modular docs plan: the section is the package's, the site assembles.


def test_the_mount_keeps_an_unchanged_section_and_full_rebuilds_it(
    tmp_path: Path,
) -> None:
    from livery.extensions.docs._site import BUILD_DIR

    root = _workspace(tmp_path)
    assert mount_package_docs(root) == ["core"]
    assert (root / BUILD_DIR / "core.digest").is_file()
    # Unchanged: nothing copied, the mount stays.
    assert mount_package_docs(root) == []
    assert (root / MOUNT / "core" / "guide.md").is_file()
    # A rewrite with the same bytes moves nothing; new bytes do; --full
    # copies regardless.
    page = root / "packages/core/docs/guide.md"
    page.write_text(page.read_text())
    assert mount_package_docs(root) == []
    page.write_text("# guide, moved\n")
    assert mount_package_docs(root) == ["core"]
    assert mount_package_docs(root, full=True) == ["core"]
    # A measured coverage report is stamped with its time: it moves no
    # digest, and an unchanged mount still gets the fresh copy.
    report = root / "packages/core/docs/_generated/coverage/python"
    report.mkdir(parents=True)
    (report / "index.html").write_text("<h1>run 1</h1>\n")
    assert mount_package_docs(root) == []  # the report never moves the digest
    assert (root / MOUNT / "core/coverage/python/index.html").is_file()
    (report / "index.html").write_text("<h1>run 2</h1>\n")
    assert mount_package_docs(root) == []
    assert (
        root / MOUNT / "core/coverage/python/index.html"
    ).read_text() == "<h1>run 2</h1>\n"
    # A package that left the workspace loses its mount.
    import shutil

    shutil.rmtree(root / "packages/core")
    assert mount_package_docs(root) == []
    assert not (root / MOUNT / "core").exists()


def test_the_workshops_pages_land_in_the_packages_generated_tree(
    tmp_path: Path,
) -> None:
    from livery.extensions.docs._site import (
        emit_section_navs,
        generate_changelog_pages,
    )

    root = _workspace(tmp_path)
    (root / "packages/core/CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n")
    assert generate_changelog_pages(root) == ["core"]
    assert _referenced(root) == ["bare", "core"]
    generated = root / "packages/core/docs/_generated"
    assert (generated / "changelog.md").is_file()
    assert (generated / "api" / "index.md").is_file()
    assert emit_section_navs(root) == ["bare", "core"]
    section = (generated / "nav.toml").read_text()
    assert section.startswith("# This section as the site assembles it")
    parsed = tomllib.loads(section)["nav"]
    assert list(parsed[0]) == ["core"]
    leaves = _nav_leaves_of(parsed)
    assert "packages/core/changelog.md" in leaves
    assert "packages/core/api/index.md" in leaves
    # The mount carries them, and the assembled config points at them.
    mount_package_docs(root)
    assert (root / "docs/packages/core/api/index.md").is_file()
    assert "packages/core/changelog.md" in zensical_config(root)


def _nav_leaves_of(entries: list[object]) -> list[str]:
    from livery.extensions.docs._site import _nav_leaves

    return _nav_leaves(entries)


def test_a_build_leaves_a_seeded_git_tree_clean(tmp_path: Path) -> None:
    """Contract 2: everything a build writes is gitignored."""
    import subprocess

    from livery.extensions.docs import _site as _docs

    root = _workspace(tmp_path, docs_table='extensions = ["docs", "mkdocstrings"]\n')
    (root / "packages/core/CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n")
    # The rules under test are the ones the base and the listed
    # extensions compose, so a build output an extension forgets to
    # ignore fails here instead of dirtying every checkout.
    from livery.workshop._shipped_files import outputs

    listing = tmp_path / "listing"
    listing.mkdir()
    (listing / "workshop.toml").write_text(
        '[workspace]\nextensions = ["docs", "mkdocstrings"]\n'
    )
    (ignore,) = [o for o in outputs(listing) if o.path == ".gitignore"]
    (root / ".gitignore").write_bytes(ignore.body)
    git = [
        "git",
        "-c",
        "commit.gpgsign=false",
    ]
    subprocess.run([*git, "init", "-q"], cwd=root, check=True)
    subprocess.run([*git, "add", "-A"], cwd=root, check=True)
    subprocess.run([*git, "commit", "-qm", "seed"], cwd=root, check=True)
    # The listed extensions' steps of the docs phase run as a verb runs them.
    _mount(root)
    _docs._generate_all(root)
    assert (root / "packages/core/docs/_generated/api/index.md").is_file()
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=root, check=True, capture_output=True
    ).stdout.decode()
    assert status == "", status


# The development section. The fallback first: no fragments, no pages.


def test_without_human_fragments_the_development_section_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.extensions.docs._site import (
        development_nav_lines,
        generate_development_pages,
    )
    from livery.workshop import _extensions, _prose

    root = _workspace(tmp_path, docs_table='extensions = ["acme.bare"]\n')
    monkeypatch.setattr(_extensions, "extension_content", lambda extension: None)
    monkeypatch.setattr(_prose, "_RENDERED", {})
    assert generate_development_pages(root) == []
    assert not (root / "docs" / "development").exists()
    assert development_nav_lines(root) == []
    assert "Development" not in zensical_config(root)


def test_the_development_section_renders_one_page_per_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.extensions.docs._site import (
        development_nav_lines,
        generate_development_pages,
    )
    from livery.workshop import _extensions, _prose

    root = _workspace(tmp_path, docs_table='extensions = ["acme.prose"]\n')
    content = tmp_path / "acme-prose-content"
    fragments = content / "fragments"
    fragments.mkdir(parents=True)
    (fragments / "voice.tone.human.md").write_text(
        "# Tone\n\nSay it plainly.\n\n## Words\n\nShort ones.\n"
    )
    (fragments / "rules.width.md").write_text("# Width\n\n88 columns.\n")
    (fragments / "rules.style.agent.md").write_text(
        "# Agent only\n\nNever on a page.\n"
    )
    monkeypatch.setattr(
        _extensions,
        "extension_content",
        lambda extension: content if extension == "acme.prose" else None,
    )
    monkeypatch.setattr(_prose, "_RENDERED", {})
    # The nav reads the fragments, not a page an earlier build wrote.
    assert '        { "Rules" = "development/rules.md" },' in development_nav_lines(
        root
    )
    written = generate_development_pages(root)
    assert written == ["index.md", "voice.md", "rules.md"]
    voice = (root / "docs" / "development" / "voice.md").read_text()
    assert voice.startswith("# Voice\n\n## Tone\n\nSay it plainly.\n\n### Words\n")
    rules = (root / "docs" / "development" / "rules.md").read_text()
    assert "## Width" in rules and "Agent only" not in rules
    index = (root / "docs" / "development" / "index.md").read_text()
    assert "[Voice](voice.md)" in index and "[Rules](rules.md)" in index
    nav = development_nav_lines(root)
    assert nav[0] == '    { "Development" = ['
    assert '        { "Voice" = "development/voice.md" },' in nav
    config = tomllib.loads(zensical_config(root))
    entry = next(e for e in config["project"]["nav"] if "Development" in e)
    assert entry["Development"][0] == {"Overview": "development/index.md"}
    # Rebuilt whole: a fragment that leaves takes its page with it.
    (fragments / "voice.tone.human.md").unlink()
    assert generate_development_pages(root) == ["index.md", "rules.md"]
    assert not (root / "docs" / "development" / "voice.md").exists()
    assert not any("Voice" in line for line in development_nav_lines(root))


# The Packages section: the refusals first, then the tree, the nav and
# the landing page.


def _name(root: Path, member: str, name: str) -> None:
    contract = root / "packages" / member / "workshop.toml"
    contract.write_text(contract.read_text() + f'\n[docs]\nname = "{name}"\n')


def test_a_docs_name_that_is_no_dotted_path_refuses_naming_the_file(
    tmp_path: Path,
) -> None:
    from livery.extensions.docs._packages_page import package_tree

    root = _workspace(tmp_path)
    _name(root, "core", "not a path!")
    with pytest.raises(
        _FAILURES,
        # The file in the platform's own spelling: a backslash on Windows.
        match=(
            r"core[/\\]workshop\.toml: \[docs\] name 'not a path!'"
            r" is not a dotted path"
        ),
    ):
        package_tree(discover_packages(root))


def test_two_packages_at_one_place_refuse_naming_both(tmp_path: Path) -> None:
    from livery.extensions.docs._packages_page import package_tree

    root = _workspace(tmp_path)
    _name(root, "core", "bare")
    with pytest.raises(
        _FAILURES, match=r"packages/bare and packages/core both sit at bare"
    ):
        package_tree(discover_packages(root))


def test_the_tree_places_packages_and_orders_them_by_their_dependencies(
    tmp_path: Path,
) -> None:
    from livery.extensions.docs._packages_page import package_tree

    root = _workspace(tmp_path)
    # A package with no import path sits at its folder under packages/.
    group = root / "packages" / "native" / "engine"
    group.mkdir(parents=True)
    (group / "workshop.toml").write_text(
        'extensions = ["python"]\nname = "acme-engine"\n'
    )
    (group / "pyproject.toml").write_text('[project]\nname = "acme-engine"\n')
    # bare depends on core, so core comes first though bare sorts first.
    contract = root / "packages" / "bare" / "workshop.toml"
    contract.write_text(
        contract.read_text()
        + '\n[[depends]]\npath = "packages/core"\nkind = "runtime"\nfloor = "0"\n'
    )
    tree = package_tree(discover_packages(root))
    # The namespace every import path shares, acme, is left out.
    assert [node.label for node in tree.ordered()] == ["core", "bare", "native"]
    (engine,) = tree.children["native"].ordered()
    assert engine.label == "engine" and engine.package is not None
    assert tree.children["native"].package is None
    # A [docs] name places a package inside another one's entry.
    _name(root, "bare", "core.extras.bare")
    tree = package_tree(discover_packages(root))
    core = tree.children["core"]
    assert core.package is not None and core.package.member == "core"
    bare = core.children["extras"].children["bare"]
    assert bare.package is not None and bare.package.member == "bare"


def test_a_multi_root_package_sits_at_its_shallowest_root(tmp_path: Path) -> None:
    from livery.extensions.docs._packages_page import package_tree
    from livery.workshop._lifecycle import module_roots

    root = _workspace(tmp_path)
    # core's build ships two roots, and the deeper one is answered first.
    extras = root / "packages" / "core" / "src" / "acme" / "base" / "extras"
    extras.mkdir(parents=True)
    (extras / "__init__.py").write_text("")
    (root / "packages" / "core" / "pyproject.toml").write_text(
        '[project]\nname = "acme-core"\n\n[tool.uv.build-backend]\n'
        'module-name = ["acme.core", "acme.base.extras"]\n'
    )
    core = next(p for p in discover_packages(root) if p.member == "core")
    assert module_roots(core) == ("acme.base.extras", "acme.core")
    # core sits at acme.core, less the shared acme, beside bare.
    tree = package_tree(discover_packages(root))
    assert [node.label for node in tree.ordered()] == ["bare", "core"]
    placed = tree.children["core"].package
    assert placed is not None and placed.member == "core"


def test_this_workspaces_packages_tree_places_every_member_where_it_sits() -> None:
    """Each member at its place in the Packages tree, a golden for this repository.

    A python package sits at its import path less the shared
    ``livery``; each extension package names its place inside the
    workshop's entry. A member added here, or moved, moves this golden
    in the same change.
    """
    from livery.extensions.docs._packages_page import Node, package_tree

    found: dict[str, str] = {}

    def walk(node: Node, place: tuple[str, ...]) -> None:
        for child in node.ordered():
            here = (*place, child.label)
            if child.package is not None:
                found[child.package.member] = ".".join(here)
            walk(child, here)

    walk(package_tree(discover_packages(ROOT)), ())
    assert found == {
        "footman": "footman",
        "forge": "forge",
        "git": "git",
        "strongroom": "strongroom",
        "toolroom": "toolroom.tools",
        "toolroom-bench": "toolroom.bench",
        "toolroom-store": "toolroom.store",
        "workshop": "workshop",
        "extensions/basedpyright": "workshop.extensions.basedpyright",
        "extensions/changelog": "workshop.extensions.changelog",
        "extensions/clang-format": "workshop.extensions.clang-format",
        "extensions/clang-tidy": "workshop.extensions.clang-tidy",
        "extensions/claude": "workshop.extensions.claude",
        "extensions/mypy": "workshop.extensions.mypy",
        "extensions/pyrefly": "workshop.extensions.pyrefly",
        "extensions/pytest": "workshop.extensions.pytest",
        "extensions/ruff": "workshop.extensions.ruff",
        "extensions/ty": "workshop.extensions.ty",
    }


def test_the_nav_has_one_packages_entry_with_its_landing_page(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    # bare's section is its API reference alone.
    _referenced(root)
    _name(root, "bare", "core.extras.bare")
    nav = tomllib.loads(zensical_config(root))["project"]["nav"]
    (packages,) = [entry for entry in nav if "Packages" in entry]
    assert packages["Packages"][0] == {"Index": "packages/index.md"}
    assert not any("core" in entry or "bare" in entry for entry in nav)
    core = _package_entry(nav, "core")["core"]
    # The package's own pages first, then the places below it, whose
    # pages keep their URLs.
    assert core[0] == {"Index": "packages/core/index.md"}
    extras = next(entry for entry in core if "extras" in entry)["extras"]
    assert next(iter(extras[0])) == "bare"
    assert "packages/bare/" in str(extras[0]["bare"])


def test_the_landing_page_lists_the_tree_with_each_description(tmp_path: Path) -> None:
    from livery.extensions.docs._packages_page import write_packages_page

    root = _workspace(tmp_path)
    contract = root / "packages" / "core" / "workshop.toml"
    contract.write_text(contract.read_text() + 'description = "The core."\n')
    _name(root, "bare", "core.extras.bare")
    mount_package_docs(root)
    page = write_packages_page(root)
    assert page is not None and page == root / "docs" / "packages" / "index.md"
    text = page.read_text(encoding="utf-8")
    assert text.startswith("# Packages\n")
    assert "- [core](core/index.md): The core.\n" in text
    assert "    - extras\n        - bare\n" in text
    # A later mount keeps the landing page: it is no stale mount.
    mount_package_docs(root)
    assert page.is_file()
