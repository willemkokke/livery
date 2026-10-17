"""The API reference: the mkdocstrings extension's pages, tables and listing.

The refusals and fallbacks first: listed with docs, the old paths
key, a package that declines, a package of another language, a
workspace that does not list it and what that workspace installs, and
the wheel's runtime requirements. Then the distribution that ships the
extension, the kit, the pages, the handler's tables, the members
policy, and the site the docs extension assembles with them.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

from extensions_mkdocstrings_workspace import (
    composed_groups,
    fake_extensions,
    fake_steps,
    skip_unless_installed,
)
from livery.extensions.docs._site import (
    DOCS_PHASE,
    named_package,
    run_site_phase,
    scoped_config,
    zensical_config,
)
from livery.extensions.mkdocstrings import _pages
from livery.extensions.mkdocstrings._pages import (
    API_DIR,
    GENERATED_DIR,
    INVENTORIES,
    OPTIONS,
    declared_paths,
    documented,
    pages,
    sources,
    write_reference,
)
from livery.footman import Failed
from livery.workshop import Package, discover_packages
from livery.workshop._steps import StepRecord, steps_by_name
from livery.workshop.testing import CLAUSES, Clause, Subject

_FAILURES = (SystemExit, Failed)

#: This repository's root, which the tests named for this workspace read.
ROOT = Path(__file__).resolve().parents[4]


def _workspace(
    tmp_path: Path, *, listed: str = '["mkdocstrings"]', docs_table: str = ""
) -> Path:
    """A workspace listing *listed*, with the python members core and bare.

    core ships pages of its own; bare ships none. Each is a regular
    package, ``acme.<name>``, so the reference has a page of each.
    """
    root = tmp_path / "ws"
    root.mkdir()
    (root / "workshop.toml").write_text(
        f"[workspace]\nextensions = {listed}\n{docs_table}"
    )
    (root / "pyproject.toml").write_text('[project]\nname = "acme-home"\n')
    (root / "docs").mkdir()
    (root / "docs" / "index.md").write_text("# Home\n")
    for name, pages_ in (("core", ("index.md", "guide.md")), ("bare", ())):
        member = root / "packages" / name
        (member / "src" / "acme" / name).mkdir(parents=True)
        (member / "src" / "acme" / name / "__init__.py").write_text("")
        (member / "workshop.toml").write_text(
            f'extensions = ["python"]\nname = "acme-{name}"\n'
        )
        (member / "pyproject.toml").write_text(f'[project]\nname = "acme-{name}"\n')
        if pages_:
            (member / "docs").mkdir()
            for page in pages_:
                (member / "docs" / page).write_text(f"# {page}\n")
    return root


def _member(root: Path, name: str) -> Package:
    return next(p for p in discover_packages(root) if p.member == name)


def _referenced(root: Path) -> list[str]:
    """Write each package's API reference as the docs phase's step writes it."""
    return [
        package.member
        for package in discover_packages(root)
        if documented(package) and write_reference(package)
    ]


def _mount(root: Path) -> None:
    """Mount what *root* lists, so its phases' steps register as a verb finds them."""
    from livery.footman import _registry as registry
    from livery.workshop._extensions import mount_extensions

    with registry.capture():
        mount_extensions(root)


def _handlers(config: str) -> dict[str, Any]:
    """The handler tables of an assembled config, by handler; empty without."""
    project: dict[str, Any] = tomllib.loads(config)["project"]
    plugins: dict[str, Any] = project.get("plugins", {})
    handlers: dict[str, Any] = plugins.get("mkdocstrings", {}).get("handlers", {})
    return handlers


def _python_options(config: str) -> dict[str, Any]:
    """The python handler's options table of an assembled config."""
    options: dict[str, Any] = _handlers(config)["python"]["options"]
    return options


def _api_labels(config: str, name: str) -> list[str]:
    """The labels of the API entries of *name*'s section, empty without one."""
    nav = tomllib.loads(config)["project"]["nav"]
    (packages,) = [entry for entry in nav if "Packages" in entry]
    for entry in packages["Packages"]:
        if name in entry:
            return [
                label
                for part in entry[name]
                if "API" in part
                for item in part["API"]
                for label in item
            ]
    return []


# The refusals first.


def test_mkdocstrings_alone_installs_docs(tmp_path: Path) -> None:
    from livery.workshop import _extensions

    # Listed beside it, docs is out of the canonical list: mkdocstrings
    # requires it, and the fix leaves it out.
    root = _workspace(tmp_path, listed='["docs", "mkdocstrings"]')
    assert _extensions.closure_problems(root) == [
        '[workspace] extensions is ["docs", "mkdocstrings"], and its canonical'
        ' list is ["mkdocstrings"]; docs is required by another listed'
        " extension; the gate's --fix rewrites it"
    ]
    assert _extensions.write_extensions(root) == [
        '  layering: [workspace] extensions is ["mkdocstrings"], its canonical list'
    ]
    assert 'extensions = ["mkdocstrings"]' in (root / "workshop.toml").read_text()
    # Listed alone, its set takes docs in: the workspace installs and
    # mounts it first, and the step joins the docs phase it declares.
    assert _extensions.closure_problems(root) == []
    assert _extensions.root_set(root) == ("docs", "mkdocstrings")
    entries = _extensions.extension_entries(root)
    assert ("docs", "livery-extensions-docs") in entries
    assert ("mkdocstrings", "livery-extensions-mkdocstrings") in entries
    _mount(root)
    assert steps_by_name()["docs.mkdocstrings"].extension == "mkdocstrings"
    assert steps_by_name()["lint.doclinks"].extension == "docs"


def test_the_paths_option_refuses_another_type_and_reaches_the_handler(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    contract = root / "packages" / "core" / "workshop.toml"
    # A value of another type refuses naming the option.
    contract.write_text(
        'extensions = ["python"]\nname = "acme-core"\n\n'
        '[phases.docs.mkdocstrings]\npaths = "docs/_generated/stubs"\n'
    )
    with pytest.raises(
        _FAILURES,
        match=r"packages/core/workshop\.toml: \[phases\.docs\.mkdocstrings\] paths is"
        r" strs, not 'docs/_generated/stubs'",
    ):
        declared_paths(_member(root, "core"))
    # The step option reaches the handler, after the package's sources.
    contract.write_text(
        'extensions = ["python"]\nname = "acme-core"\n\n'
        '[phases.docs.mkdocstrings]\npaths = ["docs/_generated/stubs"]\n'
    )
    core = _member(root, "core")
    assert declared_paths(core) == ["docs/_generated/stubs"]
    assert sources(core) == [
        "packages/core/src",
        "packages/core/docs/_generated/stubs",
    ]


def test_a_package_that_declines_gets_no_pages_and_no_handler_path(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    assert _referenced(root) == ["bare", "core"]
    generated = root / "packages" / "core" / "docs" / GENERATED_DIR
    assert (generated / API_DIR / "index.md").is_file()
    (root / "packages" / "core" / "workshop.toml").write_text(
        'extensions = ["python"]\nname = "acme-core"\n\n[docs]\napi = false\n'
    )
    core = _member(root, "core")
    assert pages(core) == []
    assert sources(core) == []
    # A rewrite takes the pages it wrote before with it, and the block.
    assert write_reference(core) == []
    assert not (generated / API_DIR).exists()
    assert not (generated / "nav.api.toml").exists()
    config = zensical_config(root)
    assert _api_labels(config, "core") == []
    assert _handlers(config)["python"]["paths"] == ["packages/bare/src"]


def test_a_cpp_package_is_never_asked_by_the_python_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    native = root / "packages" / "native"
    (native / "src").mkdir(parents=True)
    (native / "src" / "geometry.cpp").write_text("int x;\n")
    (native / "docs").mkdir()
    (native / "docs" / "index.md").write_text("# native\n")
    (native / "workshop.toml").write_text(
        'extensions = ["cmake", "conan"]\nname = "native"\n'
    )
    asked: list[str] = []
    writer = _pages.write_reference

    def recorded(package: Package) -> list[str]:
        asked.append(package.member)
        return writer(package)

    monkeypatch.setattr(_pages, "write_reference", recorded)
    _mount(root)
    run_site_phase(root, DOCS_PHASE)
    assert sorted(asked) == ["bare", "core"]
    assert not documented(_member(root, "native"))
    assert not (native / "docs" / GENERATED_DIR).exists()
    config = zensical_config(root)
    assert _api_labels(config, "native") == []
    assert _handlers(config)["python"]["paths"] == [
        "packages/bare/src",
        "packages/core/src",
    ]


def test_a_workspace_without_mkdocstrings_renders_no_reference_and_no_handler_block(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path, listed='["docs"]')
    _mount(root)
    assert not {"docs.mkdocstrings", "lint.docrefs", "lint.docstrings"} & set(
        steps_by_name()
    )
    run_site_phase(root, DOCS_PHASE)
    for name in ("core", "bare"):
        assert not (root / "packages" / name / "docs" / GENERATED_DIR).exists()
    config = zensical_config(root)
    assert "mkdocstrings" not in config
    assert "plugins" not in tomllib.loads(config)["project"]
    assert _api_labels(config, "core") == []
    preview = scoped_config(root, named_package(root, "core"))
    assert "mkdocstrings" not in preview
    # Listed, the same workspace gets both.
    (root / "workshop.toml").write_text(
        '[workspace]\nextensions = ["docs", "mkdocstrings"]\n'
    )
    _mount(root)
    run_site_phase(root, DOCS_PHASE)
    config = zensical_config(root)
    assert _api_labels(config, "core") == ["acme.core"]
    assert list(_handlers(config)) == ["python"]


def test_a_workspace_without_mkdocstrings_installs_none(tmp_path: Path) -> None:
    # Unlisted, neither the extension's distribution nor the python
    # handler reaches the venv: no line of any group the project file
    # composes names either, and neither the workshop wheel nor the docs
    # extension's, which the dev group installs, requires any of it.
    from importlib.metadata import requires

    root = _workspace(tmp_path, listed='["docs"]')
    contract = root / "workshop.toml"
    contract.write_text('[workspace]\nname = "acme"\nextensions = ["docs"]\n')
    groups = composed_groups(root)
    assert groups["docs"] == ["zensical>=0.0.50"]
    lines = [line for group in groups.values() for line in group]
    assert [line for line in lines if "mkdocstrings" in line] == []
    for distribution in ("livery-workshop", "livery-extensions-docs"):
        required = requires(distribution) or []
        assert [line for line in required if "mkdocstrings" in line] == []
    # Listed, the dev group installs the extension's distribution, and
    # its handler's line follows the builder's in the docs group.
    contract.write_text('[workspace]\nname = "acme"\nextensions = ["mkdocstrings"]\n')
    groups = composed_groups(root)
    assert "livery-extensions-mkdocstrings" in groups["dev"]
    assert groups["docs"] == ["zensical>=0.0.50", "mkdocstrings-python>=1.11"]


def test_the_wheel_requires_griffelib_for_its_tests_alone() -> None:
    # The reference check imports griffe inside the function that reads the
    # sources, and a workspace that lists mkdocstrings gets griffe through
    # mkdocstrings-python. So the wheel names griffelib in its test extra
    # alone, which the release's isolated legs install to run this suite.
    from importlib.metadata import requires

    markers = [
        marker.strip().replace('"', "'")
        for spec, _, marker in (
            line.partition(";")
            for line in requires("livery-extensions-mkdocstrings") or ()
        )
        if spec.strip().startswith("griffelib")
    ]
    assert markers == ["extra == 'test'"]


# Then the distribution that ships it, and the kit.


def test_its_own_distribution_ships_mkdocstrings() -> None:
    from importlib.metadata import distribution, requires

    from livery.workshop._extensions import declaration, shipping_distribution

    found = {
        entry.name: entry.value
        for entry in distribution("livery-extensions-mkdocstrings").entry_points
        if entry.group == "workshop.extensions"
    }
    assert found == {"mkdocstrings": "livery.extensions.mkdocstrings"}
    shipped = shipping_distribution("livery.extensions.mkdocstrings")
    assert shipped is not None and shipped.name == "livery-extensions-mkdocstrings"
    declared = declaration("mkdocstrings")
    assert declared is not None and declared.requires == ("docs",)
    # It requires docs, which livery-extensions-docs ships, so its wheel
    # requires that distribution.
    assert any(
        line.startswith("livery-extensions-docs")
        for line in requires("livery-extensions-mkdocstrings") or ()
    )


@pytest.mark.parametrize("clause", CLAUSES, ids=lambda clause: clause.name)
def test_mkdocstrings_conforms(clause: Clause, tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _mount(root)
    steps: tuple[StepRecord, ...] = tuple(
        record
        for record in steps_by_name().values()
        if record.extension == "mkdocstrings"
    )
    assert {record.name for record in steps} == {
        "docs.mkdocstrings",
        "lint.docrefs",
        "lint.docstrings",
    }
    subject = Subject(
        "livery.extensions.mkdocstrings",
        sets=(("python",),),
        steps=steps,
    )
    assert clause.judge(subject) == []


# Then the pages, fallbacks first.


def test_a_srcless_package_has_no_api(tmp_path: Path) -> None:
    import shutil

    root = _workspace(tmp_path)
    bare = _member(root, "bare")
    shutil.rmtree(bare.directory / "src")
    assert pages(bare) == []
    assert sources(bare) == []
    assert write_reference(bare) == []
    assert "packages/bare/src" not in zensical_config(root)


def test_a_root_with_no_init_starts_at_the_first_module_the_package_owns(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    module = root / "packages" / "core" / "src" / "acme" / "core"
    (module / "__init__.py").unlink()
    (module / "_steps.py").write_text('"""Steps."""\n')
    # No __init__.py marks a root and nothing names a module: no pages.
    assert pages(_member(root, "core")) == []
    # The build names two modules. The first has no directory, so the
    # reference starts at the second.
    (root / "packages" / "core" / "pyproject.toml").write_text(
        '[project]\nname = "acme-core"\n\n[tool.uv.build-backend]\n'
        'module-name = ["acme.absent", "acme.core"]\n'
    )
    assert pages(_member(root, "core")) == [("_steps.md", "acme.core._steps")]


def test_api_modules_sort_public_first_and_skip_the_machinery(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    core = _member(root, "core")
    module = core.directory / "src" / "acme" / "core"
    (module / "_private.py").write_text('"""Private."""\n')
    (module / "public.py").write_text('"""Public."""\n')
    (module / "__main__.py").write_text("")
    (module / "_docs").mkdir()
    (module / "_docs" / "index.md").write_text("# stale\n")
    (module / "sub").mkdir()
    (module / "sub" / "__init__.py").write_text('"""Sub."""\n')
    (module / "sub" / "_inner.py").write_text('"""Inner."""\n')
    modules = pages(core)
    paths = [page for page, _dotted in modules]
    dotted = [d for _page, d in modules]
    # The package index first, then public before private per level;
    # __main__ and the machine _docs never appear.
    assert paths[0] == "index.md" and dotted[0] == "acme.core"
    assert paths.index("public.md") < paths.index("_private.md")
    assert paths.index("sub/index.md") < paths.index("_private.md")
    assert "__main__.md" not in paths
    assert all("_docs" not in page for page in paths)
    assert ("sub/_inner.md", "acme.core.sub._inner") in modules


def test_a_package_shipping_two_roots_documents_both(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    core = _member(root, "core")
    # A second root on its own branch: acme/extras carries no __init__.py.
    second = core.directory / "src" / "acme" / "extras" / "site"
    second.mkdir(parents=True)
    (second / "__init__.py").write_text('"""The second root."""\n')
    (second / "_inner.py").write_text('"""Inner."""\n')
    modules = pages(core)
    # The shallowest root keeps the reference's top; the further one sits
    # under its dotted path, after it.
    assert modules[0] == ("index.md", "acme.core")
    assert modules[-2:] == [
        ("acme.extras.site/index.md", "acme.extras.site"),
        ("acme.extras.site/_inner.md", "acme.extras.site._inner"),
    ]


def test_api_pages_rebuild_whole_with_one_directive_each(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    generated = root / "packages" / "core" / "docs" / GENERATED_DIR
    stale = generated / API_DIR / "gone.md"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale\n")
    assert _referenced(root) == ["bare", "core"]
    assert not stale.exists()
    index = (generated / API_DIR / "index.md").read_text()
    assert "::: acme.core" in index
    # The block lists the pages, as the site places it.
    block = tomllib.loads((generated / "nav.api.toml").read_text())["nav"]
    assert block == [{"API": [{"acme.core": "_generated/api/index.md"}]}]


def test_the_config_wires_mkdocstrings_only_when_modules_exist(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    _referenced(root)
    config = zensical_config(root)
    handler = _handlers(config)["python"]
    assert handler["paths"] == ["packages/bare/src", "packages/core/src"]
    assert list(handler["inventories"]) == list(INVENTORIES)
    assert handler["options"] == OPTIONS
    assert handler["options"]["docstring_style"] == "google"
    assert handler["options"]["show_if_no_docstring"] is True
    assert _api_labels(config, "core") == ["acme.core"]
    nav = tomllib.loads(config)["project"]["nav"]
    (packages,) = [entry for entry in nav if "Packages" in entry]
    (core,) = [entry for entry in packages["Packages"] if "core" in entry]
    api = next(part for part in core["core"] if "API" in part)
    assert api["API"][0] == {"acme.core": "packages/core/api/index.md"}
    # Without a module anywhere, no handler table at all.
    import shutil

    for name in ("core", "bare"):
        shutil.rmtree(root / "packages" / name / "src")
    assert _handlers(zensical_config(root)) == {}


# The members policy, a slot the extensions fill: the default, then a
# contribution and the nearest rule.


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


def test_without_an_answer_the_handler_keeps_its_default_filter(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    assert "filters" not in _python_options(zensical_config(root))
    preview = scoped_config(root, named_package(root, "core"))
    assert "filters" not in _python_options(preview)


def test_all_members_writes_an_empty_filter_and_the_nearest_extension_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(
        tmp_path,
        monkeypatch,
        base=_answering("docs.members", '"all"'),
        site=_answering("docs.members", '"public"', requires="acme.base"),
    )
    root = _workspace(tmp_path, listed='["docs", "mkdocstrings", "acme.base"]')
    assert _python_options(zensical_config(root))["filters"] == []
    preview = scoped_config(root, named_package(root, "core"))
    assert _python_options(preview)["filters"] == []
    # The nearest extension decides: the one that requires the other
    # wins over it, and taking it off the list restores the other's.
    _relist(root, '["docs", "mkdocstrings", "acme.base", "acme.site"]')
    assert "filters" not in _python_options(zensical_config(root))
    _relist(root, '["docs", "mkdocstrings", "acme.base"]')
    assert _python_options(zensical_config(root))["filters"] == []


def test_the_members_policies_are_the_query_s_values() -> None:
    from livery.extensions.docs import ALL_MEMBERS, MEMBERS, PUBLIC_MEMBERS
    from livery.workshop._queries import query_spec

    spec = query_spec(MEMBERS.name)
    assert spec.values == (PUBLIC_MEMBERS, ALL_MEMBERS)
    assert spec.default == PUBLIC_MEMBERS


# The site the docs extension assembles: the reference's pages and
# tables beside another answer's, in a preview, in this repository's
# own site, and a build's outputs in a git tree.


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
    fake_extensions(tmp_path, monkeypatch, **dict.fromkeys(tables, _WRITER))
    for name, text in tables.items():
        fake_steps(tmp_path, name, f"TABLES = {text!r}\n" + _WRITES)


def _section(config: str, name: str) -> list[Any] | None:
    """The entries of *name*'s section under the Packages entry; None without one."""
    nav = tomllib.loads(config)["project"]["nav"]
    (packages,) = [entry for entry in nav if "Packages" in entry]
    for entry in packages["Packages"]:
        if name in entry:
            found: list[Any] = entry[name]
            return found
    return None


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
    root = _workspace(tmp_path, listed='["acme.carve", "mkdocstrings"]')
    _referenced(root)
    config = zensical_config(root)
    handlers = _handlers(config)
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
    preview = scoped_config(root, named_package(root, "core"))
    assert _handlers(preview)["carve"]["paths"] == ["../../core"]


def test_a_package_no_generator_documents_has_no_api_entry(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    bare = root / "packages" / "bare"
    (bare / "workshop.toml").write_text(
        'extensions = ["cmake", "conan"]\nname = "acme-bare"\n'
    )
    (bare / "pyproject.toml").unlink()
    (bare / "docs").mkdir()
    (bare / "docs" / "index.md").write_text("# bare\n")
    assert _referenced(root) == ["core"]
    # No page states an absence: the section carries its own pages alone.
    assert not (bare / "docs" / GENERATED_DIR).exists()
    config = zensical_config(root)
    assert _section(config, "bare") == [{"Index": "packages/bare/index.md"}]
    assert _api_labels(config, "core") == ["acme.core"]
    handlers = _handlers(config)
    assert list(handlers) == ["python"]
    assert handlers["python"]["paths"] == ["packages/core/src"]
    # Without its pages a package has no section at all.
    (bare / "docs" / "index.md").unlink()
    assert _section(zensical_config(root), "bare") is None


def test_a_preview_s_handler_reads_from_two_levels_down_and_links_the_site(
    tmp_path: Path,
) -> None:
    root = _workspace(
        tmp_path,
        docs_table='[docs]\ntitle = "Acme"\nsite-url = "https://docs.acme.example/home/"\n',
    )
    handler = _handlers(scoped_config(root, named_package(root, "core")))["python"]
    # Sources resolve from the preview directory back into the
    # workspace; cross-package references resolve through the
    # published site's inventory.
    assert handler["paths"] == ["../../packages/core/src"]
    assert "https://docs.acme.example/home/objects.inv" in handler["inventories"]


def test_a_build_leaves_a_seeded_git_tree_clean(tmp_path: Path) -> None:
    """Everything a build writes is gitignored, the API reference among it."""
    import subprocess

    from livery.extensions.docs import _site as _docs
    from livery.workshop._shipped_files import outputs

    root = _workspace(tmp_path)
    # The rules under test are the ones the base and the listed
    # extensions compose, so a build output an extension forgets to
    # ignore fails here instead of dirtying every checkout.
    listing = tmp_path / "listing"
    listing.mkdir()
    (listing / "workshop.toml").write_text(
        '[workspace]\nextensions = ["mkdocstrings"]\n'
    )
    (ignore,) = [o for o in outputs(listing) if o.path == ".gitignore"]
    (root / ".gitignore").write_bytes(ignore.body)
    git = ["git", "-c", "commit.gpgsign=false"]
    subprocess.run([*git, "init", "-q"], cwd=root, check=True)
    subprocess.run([*git, "add", "-A"], cwd=root, check=True)
    subprocess.run([*git, "commit", "-qm", "seed"], cwd=root, check=True)
    # The listed extensions' steps of the docs phase run as a verb runs them.
    _mount(root)
    _docs._generate_all(root)
    assert (
        root / "packages/core/docs" / GENERATED_DIR / API_DIR / "index.md"
    ).is_file()
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=root, check=True, capture_output=True
    ).stdout.decode()
    assert status == "", status


@pytest.fixture
def no_development_section(monkeypatch: pytest.MonkeyPatch) -> None:
    """Assemble this repository's site config without its development section.

    The development section renders the verbs fragment through this
    repository's own fm, which syncs a checkout that never synced: a
    release leg's fresh clone, whose links would then point into the
    leg's scratch venv. The tests that take this read the handler,
    never the development section.
    """
    monkeypatch.setattr(
        "livery.extensions.docs._site.development_nav_lines", lambda root: []
    )


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
    from livery.workshop._extensions import installed_set

    # The site is read from this checkout's packages, whose contracts
    # read right only beside every extension the workspace mounts.
    skip_unless_installed(*installed_set(ROOT))
    assert _handlers(zensical_config(ROOT)) == {
        "python": {
            "paths": [
                "packages/extensions/basedpyright/src",
                "packages/extensions/changelog/src",
                "packages/extensions/clang-tidy/src",
                "packages/extensions/claude/src",
                "packages/extensions/cmake/src",
                "packages/extensions/conan/src",
                "packages/extensions/coverage/src",
                "packages/extensions/cpp/src",
                "packages/extensions/docs/src",
                "packages/extensions/lfs/src",
                "packages/extensions/mkdocstrings/src",
                "packages/extensions/nanobind/src",
                "packages/extensions/pytest/src",
                "packages/extensions/python/src",
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
    preview = scoped_config(ROOT, named_package(ROOT, "toolroom"))
    assert _handlers(preview) == {
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
