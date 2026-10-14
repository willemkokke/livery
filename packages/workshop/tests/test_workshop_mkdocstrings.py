"""The API reference: the mkdocstrings extension's pages, tables and listing.

The refusals and fallbacks first: listed without docs, the old paths
key, a package that declines, a package of another language, a
workspace that does not list it and what that workspace installs, and
the workshop wheel's runtime requirements. Then the pages, the
handler's tables and the members policy.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

from livery.extensions.docs._navblocks import GENERATED_DIR
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
    INVENTORIES,
    OPTIONS,
    declared_paths,
    documented,
    pages,
    sources,
    write_reference,
)
from livery.footman import Failed
from livery.workshop._packages import Package, discover_packages
from livery.workshop._steps import steps_by_name
from workshop_composed import composed_groups
from workshop_extension_fakes import fake_extensions

_FAILURES = (SystemExit, Failed)


def _workspace(
    tmp_path: Path, *, listed: str = '["docs", "mkdocstrings"]', docs_table: str = ""
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


def test_mkdocstrings_listed_without_docs_refuses_and_its_fix_lists_docs(
    tmp_path: Path,
) -> None:
    from livery.workshop import _extensions

    root = _workspace(tmp_path, listed='["mkdocstrings"]')
    assert _extensions.closure_problems(root) == [
        "[workspace] extensions lists mkdocstrings, which depends on docs, and"
        " does not list it; the gate's --fix adds it before mkdocstrings"
    ]
    # Its step joins the docs phase, which nothing listed declares.
    with pytest.raises(
        _FAILURES,
        match=r"extension 'mkdocstrings' adds steps to the docs phase, which docs"
        r" declares and the workspace does not list; list docs before mkdocstrings",
    ):
        _mount(root)
    assert _extensions.write_extensions(root) == [
        "  layering: [workspace] extensions gains docs before mkdocstrings, which"
        " requires it"
    ]
    assert (
        'extensions = ["docs", "mkdocstrings"]' in (root / "workshop.toml").read_text()
    )
    assert _extensions.closure_problems(root) == []
    _mount(root)
    assert steps_by_name()["docs.mkdocstrings"].extension == "mkdocstrings"


def test_python_paths_in_the_docs_table_refuses_naming_the_step_option(
    tmp_path: Path,
) -> None:
    from livery.workshop._contract import read_contract

    root = _workspace(tmp_path)
    member = root / "packages" / "core"
    contract = member / "workshop.toml"
    contract.write_text(
        'extensions = ["python"]\nname = "acme-core"\n\n'
        '[docs]\npython-paths = ["docs/_generated/stubs"]\n'
    )
    with pytest.raises(_FAILURES) as refused:
        read_contract(member)
    assert str(refused.value) == (
        f"{contract}:\n  [docs] python-paths is not a key: the directories the API"
        " reference also reads modules from are the paths option of the step that"
        " writes it, [phases.docs.mkdocstrings] paths, read while [workspace]"
        " extensions lists mkdocstrings"
    )
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


def test_a_workspace_without_mkdocstrings_installs_no_mkdocstrings(
    tmp_path: Path,
) -> None:
    # The python handler reaches the venv through the extension's line in
    # the docs group alone: no other line of the project file names it, and
    # the workshop wheel, which the dev group installs for the extensions it
    # ships, requires none of it.
    from importlib.metadata import requires

    root = _workspace(tmp_path, listed='["docs"]')
    contract = root / "workshop.toml"
    contract.write_text('[workspace]\nname = "acme"\nextensions = ["docs"]\n')
    groups = composed_groups(root)
    assert groups["docs"] == ["zensical>=0.0.50"]
    lines = [line for group in groups.values() for line in group]
    assert [line for line in lines if "mkdocstrings" in line] == []
    required = requires("livery-workshop") or []
    assert [line for line in required if "mkdocstrings" in line] == []
    # Listed after docs, its line follows the builder's.
    contract.write_text(
        '[workspace]\nname = "acme"\nextensions = ["docs", "mkdocstrings"]\n'
    )
    assert composed_groups(root)["docs"] == [
        "zensical>=0.0.50",
        "mkdocstrings-python>=1.11",
    ]


def test_the_workshop_wheel_requires_no_griffelib_at_runtime() -> None:
    # The reference check imports griffe inside the function that reads the
    # sources, and a workspace that lists mkdocstrings gets griffe through
    # mkdocstrings-python. So the wheel names griffelib in its test extra
    # alone, which the release's isolated legs install to run this suite.
    from importlib.metadata import requires

    from livery.extensions.python._installed import requirement_name

    markers = [
        marker.strip().replace('"', "'")
        for spec, _, marker in (
            line.partition(";") for line in requires("livery-workshop") or ()
        )
        if requirement_name(spec) == "griffelib"
    ]
    assert markers == ["extra == 'test'"]


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
        '[extension]\nlevels = ["workspace"]\n'
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
    from livery.extensions.docs._config import MEMBERS_POLICIES, PUBLIC_MEMBERS
    from livery.workshop._queries import query_spec

    spec = query_spec("docs.members")
    assert spec.values == MEMBERS_POLICIES
    assert spec.default == PUBLIC_MEMBERS
