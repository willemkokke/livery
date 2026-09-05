"""The documentation site: its rendered config, mounts, and verbs.

One rendered site per workspace. The config is emitted the way
``ci.yml`` is: a generated header, drift-checked, the nav owned
between markers. Authors write ``packages/<name>/docs/`` and the
root ``docs/`` tree; every underscore path this module writes is
machine territory, refreshed by the verbs and never edited by a
person.

A package owns the shape of its own site section through
``docs/nav.toml``: a hand-authored tree the emitter merges under
that package's section of the rendered root config, checked both
ways (an entry naming a missing page refuses, and so does an
authored page absent from the nav). A package without one gets its
pages enumerated, index first. ``fm docs.build --package <name>``
builds a scoped preview of one section into the gitignored
``.docs-preview/`` directory; the workspace site stays the only
deploy artifact.
"""

from __future__ import annotations

import shutil
import tomllib
from pathlib import Path
from typing import Annotated

import toolroom
from footman import doc, fail, group

from livery.workshop._packages import Package, discover_packages

#: The nav block the emitter owns; an edit between these is drift.
NAV_BEGIN = "# docs-nav:begin (generated; the emitter owns this block)"
NAV_END = "# docs-nav:end"

#: Where package docs mount inside the site's tree, per package
#: directory name. Gitignored; rebuilt on every docs verb.
MOUNT = "docs/_generated/packages"

#: Where the generated API pages live, per package directory name.
#: Gitignored; rebuilt on every docs verb.
API = "docs/_generated/api"

#: The inventories cross-ecosystem references resolve against.
#: Pinned here so one workshop release moves every project; the
#: footman and toolroom pins retire when those repositories migrate
#: into the workspace.
INVENTORIES = (
    "https://docs.python.org/3/objects.inv",
    "https://willemkokke.github.io/footman/objects.inv",
    "https://willemkokke.github.io/toolroom/objects.inv",
)


def docs_table(root: Path) -> dict[str, object]:
    """The contract's ``[docs]`` table; empty when undeclared."""
    contract = tomllib.loads((root / "workshop.toml").read_text("utf-8"))
    table = contract.get("docs") or {}
    return dict(table) if isinstance(table, dict) else {}


def _pages(directory: Path) -> list[str]:
    """The markdown pages under *directory*, index first, then sorted."""
    if not directory.is_dir():
        return []
    found = sorted(
        page.relative_to(directory).as_posix() for page in directory.rglob("*.md")
    )
    return sorted(found, key=lambda page: (page != "index.md", page))


def _project_name(root: Path) -> str:
    """The workspace's stable name for the site.

    The root project's name, never the directory's: a worktree's
    directory name would make the same contract render differently
    per checkout and turn the drift gate against itself.
    """
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        project = tomllib.loads(pyproject.read_text("utf-8")).get("project") or {}
        name = str(project.get("name", ""))
        if name:
            return name
    return root.resolve().name


def _label(page: str) -> str:
    return "Index" if Path(page).name == "index.md" else Path(page).stem


#: The package-owned nav file inside ``docs/``.
NAV_TOML = "nav.toml"


def nav_block_markers(name: str) -> tuple[str, str]:
    """The begin and end marker lines for a generated block in ``nav.toml``.

    The lines between a pair belong to the generator that owns
    *name*; the surrounding tree stays hand-authored.
    """
    return (f"# nav:begin {name}", f"# nav:end {name}")


def rewrite_nav_block(path: Path, name: str, entries: list[str]) -> None:
    """Replace the *name* block's lines inside the ``nav.toml`` at *path*.

    *entries* arrive unindented; each is re-indented to the begin
    marker's own indentation, so a generator never bakes indentation
    into its output and the author stays free to move the block. Both
    markers must already exist: where the block sits in the tree is
    the author's decision, and a missing marker refuses naming the
    file and the block. Idempotent: rewriting the same entries leaves
    the file byte-identical.
    """
    begin, end = nav_block_markers(name)
    if not path.is_file():
        fail(f"{path} does not exist: the nav block {name!r} has no home")
    lines = path.read_text("utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == begin]
    ends = [i for i, line in enumerate(lines) if line.strip() == end]
    if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
        fail(
            f"{path} does not carry the {name!r} block markers"
            f" ({begin!r} then {end!r}, once each): add them where the"
            " block belongs in the tree"
        )
    indent = lines[starts[0]][: len(lines[starts[0]]) - len(lines[starts[0]].lstrip())]
    body = [indent + entry if entry else "" for entry in entries]
    rewritten = lines[: starts[0] + 1] + body + lines[ends[0] :]
    path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")


def package_nav(package: Package) -> list[object] | None:
    """The package's authored nav tree, or None without a ``nav.toml``.

    The file's ``nav`` key mirrors the rendered config's shape: a
    list of one-key tables mapping a label to a page path (relative
    to the package's ``docs/`` tree) or to a nested list. Anything
    else refuses naming the file and the entry.
    """
    path = package.directory / "docs" / NAV_TOML
    if not path.is_file():
        return None
    try:
        parsed = tomllib.loads(path.read_text("utf-8"))
    except tomllib.TOMLDecodeError as error:
        fail(f"{path} is not valid TOML: {error}")
    nav = parsed.get("nav")
    if not isinstance(nav, list):
        fail(f"{path} must carry a top-level `nav` list")
    _check_nav_shape(path, nav)
    return list(nav)


def _check_nav_shape(path: Path, entries: list[object]) -> None:
    for entry in entries:
        if not (isinstance(entry, dict) and len(entry) == 1):
            fail(
                f"{path}: nav entry {entry!r} is not a single"
                ' `{ "Label" = ... }` table'
            )
        value = next(iter(entry.values()))
        if isinstance(value, list):
            _check_nav_shape(path, value)
        elif not isinstance(value, str):
            fail(
                f"{path}: nav entry {entry!r} must map its label to a"
                " page path or a nested list"
            )


def _nav_leaves(entries: list[object]) -> list[str]:
    """Every page path an authored nav tree names, in order."""
    leaves: list[str] = []
    for entry in entries:
        if isinstance(entry, dict):
            for value in entry.values():
                if isinstance(value, str):
                    leaves.append(value)
                elif isinstance(value, list):
                    leaves += _nav_leaves(value)
    return leaves


def check_package_nav(package: Package, entries: list[object]) -> None:
    """Refuse an authored nav that disagrees with the docs tree.

    Both directions: an entry naming a missing page, and an authored
    page the nav does not carry. Entries under ``_generated/`` are
    exempt from the missing check (a generator writes them at build
    time, and the strict site build owns them); generated pages are
    likewise not required in the nav.
    """
    docs = package.directory / "docs"
    leaves = _nav_leaves(entries)
    authored = {page for page in _pages(docs) if not page.startswith("_generated/")}
    nav_path = docs / NAV_TOML
    missing = [
        leaf
        for leaf in leaves
        if not leaf.startswith("_generated/") and leaf not in authored
    ]
    if missing:
        fail(f"{nav_path} names pages that do not exist: " + ", ".join(sorted(missing)))
    orphans = sorted(authored - set(leaves))
    if orphans:
        fail(
            f"{nav_path} does not carry these authored pages: "
            + ", ".join(orphans)
            + ". Add them to the nav, or delete them."
        )


def _authored_nav_lines(entries: list[object], prefix: str, indent: str) -> list[str]:
    """The rendered nav lines for an authored tree, paths prefixed."""
    lines: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for label, value in entry.items():
            if isinstance(value, str):
                lines.append(f'{indent}{{ "{label}" = "{prefix}{value}" }},')
            elif isinstance(value, list):
                lines.append(f'{indent}{{ "{label}" = [')
                lines += _authored_nav_lines(value, prefix, indent + "    ")
                lines.append(f"{indent}] }},")
    return lines


def zensical_config(root: Path) -> str:
    """The rendered ``zensical.toml`` body, generated header excluded.

    Site identity comes from the ``[docs]`` table (title defaults to
    the root project's name); the nav enumerates the root
    ``docs/`` pages and every package's ``docs/`` tree at its mount
    path. The whole file is the emitter's: hand customisation goes
    through the contract, and the drift gate refuses an edit here.
    """
    table = docs_table(root)
    title = str(table.get("title", "")) or _project_name(root)
    site_url = str(table.get("site_url", ""))
    lines = ["[project]", f'site_name = "{title}"']
    if site_url:
        lines.append(f'site_url = "{site_url}"')
    lines.append("nav = [")
    lines.append(f"    {NAV_BEGIN}")
    for page in _pages(root / "docs"):
        if page.startswith("_generated/"):
            continue
        label = "Home" if page == "index.md" else _label(page)
        lines.append(f'    {{ "{label}" = "{page}" }},')
    if any(
        (package.directory / "CHANGELOG.md").is_file()
        for package in discover_packages(root)
    ):
        # Committed state only: the tags are absent in a shallow CI
        # clone, and a nav derived from them would make the same
        # contract render differently per checkout. The landing page
        # links its own year archives at build time instead.
        lines.append('    { "Releases" = "_generated/releases/index.md" },')
    handler_paths: list[str] = []
    for package in discover_packages(root):
        section, has_modules = _package_section(package)
        if not section:
            continue
        lines += section
        if has_modules:
            handler_paths.append(f"packages/{package.directory.name}/src")
    lines.append(f"    {NAV_END}")
    lines.append("]")
    if handler_paths:
        lines += _mkdocstrings_lines(handler_paths, INVENTORIES)
    return "\n".join(lines) + "\n"


def _package_section(package: Package, indent: str = "    ") -> tuple[list[str], bool]:
    """One package's nav section lines, and whether it has API modules.

    The authored ``nav.toml`` tree when the package ships one
    (checked both ways first), the enumerated authored pages
    otherwise; then the changelog and API entries, which are machine
    territory in either shape. Empty for a package with nothing to
    show.
    """
    docs = package.directory / "docs"
    pages = [p for p in _pages(docs) if not p.startswith("_generated/")]
    modules = api_modules(package)
    changelog = (package.directory / "CHANGELOG.md").is_file()
    authored = package_nav(package)
    if authored is None and not pages and not modules and not changelog:
        return ([], False)
    name = package.directory.name
    prefix = f"_generated/packages/{name}/"
    inner = indent + "    "
    lines = [f'{indent}{{ "{name}" = [']
    if authored is not None:
        check_package_nav(package, authored)
        lines += _authored_nav_lines(authored, prefix, inner)
    else:
        for page in pages:
            lines.append(f'{inner}{{ "{_label(page)}" = "{prefix}{page}" }},')
    if changelog:
        lines.append(f'{inner}{{ "Changelog" = "{prefix}changelog.md" }},')
    if modules:
        lines.append(f'{inner}{{ "API" = [')
        for page, dotted in modules:
            lines.append(
                f'{inner}    {{ "{dotted}" = "_generated/api/{name}/{page}" }},'
            )
        lines.append(f"{inner}] }},")
    lines.append(f"{indent}] }},")
    return (lines, bool(modules))


def _mkdocstrings_lines(paths: list[str], inventories: tuple[str, ...]) -> list[str]:
    """The mkdocstrings handler block for the given source paths."""
    listed = ", ".join(f'"{path}"' for path in paths)
    linked = ", ".join(f'"{url}"' for url in inventories)
    return [
        "",
        "[project.plugins.mkdocstrings.handlers.python]",
        f"paths = [{listed}]",
        f"inventories = [{linked}]",
        "",
        "[project.plugins.mkdocstrings.handlers.python.options]",
        "# Google style is the house convention; a docstring is",
        "# published the moment it is written, empty ones included.",
        'docstring_style = "google"',
        "show_if_no_docstring = true",
        "show_root_heading = true",
        "show_root_full_path = true",
        "separate_signature = true",
        "show_signature_annotations = true",
        "signature_crossrefs = true",
        'members_order = "source"',
        "merge_init_into_class = true",
        "summary = true",
        "heading_level = 2",
    ]


RELEASES = "docs/_generated/releases"


def _insert_after_title(text: str, block: str) -> str:
    """Insert *block* before the first entry heading, after the title."""
    first = text.find("\n## ")
    if first == -1:
        return text.rstrip("\n") + "\n\n" + block + "\n"
    return text[:first] + "\n" + block + "\n" + text[first:]


def changelog_page(root: Path, package: Package) -> str | None:
    """The package's changelog page; None without a changelog.

    The committed file, with what is unreleased prepended in memory
    (the file on disk never changes). When git-cliff cannot answer,
    an offline checkout or a package without its config, the page is
    the file alone and the reason is printed, never swallowed into a
    broken page.
    """
    changelog = package.directory / "CHANGELOG.md"
    if not changelog.is_file():
        return None
    text = changelog.read_text("utf-8")
    try:
        from livery.workshop import _cliff

        unreleased = _cliff.unreleased_entry(root, package)
    except BaseException as error:
        print(f"  {package.name}: unreleased section skipped: {error}")
        unreleased = ""
    if unreleased:
        text = _insert_after_title(text, unreleased)
    return text


def generate_changelog_pages(root: Path) -> list[str]:
    """Write each package's changelog page into its mount; the names.

    Runs after the mount rebuild, so the pages land in the same tree
    the nav points at.
    """
    generated: list[str] = []
    for package in discover_packages(root):
        page = changelog_page(root, package)
        if page is None:
            continue
        target = root / MOUNT / package.directory.name / "changelog.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(page, encoding="utf-8")
        generated.append(package.directory.name)
    return generated


def _receipt_tags(root: Path) -> list[tuple[str, str, str, str]]:
    """(date, package dir, dist name, version) per release tag, newest first.

    The receipt tags are the release identity (the train cuts them
    after the index confirms), so the view derives from them and the
    changelogs alone; nothing new is committed.
    """
    import re

    result = toolroom.git.opts(cwd=root, nofail=True, recorded=False)(
        "for-each-ref",
        "refs/tags/packages",
        "--format=%(refname:short) %(creatordate:short)",
    )
    if result.code != 0:
        return []
    names = {p.directory.name: p.name for p in discover_packages(root)}
    tags: list[tuple[str, str, str, str]] = []
    for line in result.stdout.splitlines():
        match = re.fullmatch(
            r"packages/([^/]+)/v(\d+\.\d+\.\d+) (\d{4}-\d{2}-\d{2})", line.strip()
        )
        if match is None:
            continue
        directory, version, date = match.groups()
        tags.append((date, directory, names.get(directory, directory), version))
    tags.sort(key=lambda tag: (tag[0], tag[3]), reverse=True)
    return tags


def _entry_body(changelog: Path, version: str) -> str:
    """The ``## [version]`` section's body from *changelog*; empty when absent."""
    if not changelog.is_file():
        return ""
    text = changelog.read_text("utf-8")
    import re

    pattern = re.compile(
        rf"^## \[?{re.escape(version)}\]?[^\n]*\n(.*?)(?=^## |\Z)",
        re.M | re.S,
    )
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def _release_block(root: Path, tag: tuple[str, str, str, str]) -> str:
    date, directory, dist, version = tag
    body = _entry_body(root / "packages" / directory / "CHANGELOG.md", version)
    lines = [f"## {dist} v{version} ({date})", ""]
    if body:
        lines.append(body)
    else:
        lines.append(f"Released as `packages/{directory}/v{version}`.")
    return "\n".join(lines) + "\n"


def release_years(root: Path) -> list[str]:
    """The archive years the view paginates into, newest first.

    Every year with a release except the newest, which lives on the
    landing page.
    """
    years = sorted({tag[0][:4] for tag in _receipt_tags(root)}, reverse=True)
    return years[1:]


def generate_release_pages(root: Path) -> list[str]:
    """Write the site-wide release view; the page paths written.

    Derived from the receipt tags joined with the changelog entries
    they point at, newest first across members. Paginated: the
    newest year's releases on the landing page, each earlier year on
    its own archive page linked from it.
    """
    base = root / RELEASES
    shutil.rmtree(base, ignore_errors=True)
    tags = _receipt_tags(root)
    if not tags:
        if any(
            (package.directory / "CHANGELOG.md").is_file()
            for package in discover_packages(root)
        ):
            # The nav points here whenever a changelog exists, so the
            # page must too: a checkout without the tags (a shallow CI
            # clone) states that plainly instead of serving a 404.
            base.mkdir(parents=True)
            (base / "index.md").write_text(
                "# Releases\n\nNo release tags in this checkout.\n",
                encoding="utf-8",
            )
            return ["index.md"]
        return []
    base.mkdir(parents=True)
    years = sorted({tag[0][:4] for tag in tags}, reverse=True)
    newest, archived = years[0], years[1:]
    written: list[str] = []

    def _page(title: str, year: str, links: list[str]) -> str:
        blocks = [f"# {title}", ""]
        blocks += [_release_block(root, tag) for tag in tags if tag[0][:4] == year]
        blocks += links
        return "\n".join(blocks).rstrip("\n") + "\n"

    links = (
        ["", "Older releases: " + ", ".join(f"[{y}]({y}.md)" for y in archived), ""]
        if archived
        else []
    )
    (base / "index.md").write_text(_page("Releases", newest, links), encoding="utf-8")
    written.append("index.md")
    for year in archived:
        (base / f"{year}.md").write_text(
            _page(f"Releases in {year}", year, []), encoding="utf-8"
        )
        written.append(f"{year}.md")
    return written


def mount_package_docs(root: Path) -> list[str]:
    """Mount every package's ``docs/`` into the site tree; the names.

    The mount is rebuilt whole on every call, so a page deleted from
    a package never lingers in the site. ``nav.toml`` stays behind:
    it configures the nav and is not site content.
    """
    base = root / MOUNT
    shutil.rmtree(base, ignore_errors=True)
    mounted: list[str] = []
    for package in discover_packages(root):
        docs = package.directory / "docs"
        if not docs.is_dir():
            continue
        shutil.copytree(
            docs,
            base / package.directory.name,
            ignore=lambda directory, names, docs=docs: (
                [NAV_TOML] if Path(directory) == docs else []
            ),
        )
        mounted.append(package.directory.name)
    return mounted


#: The scoped previews' home, gitignored; one directory per package,
#: rebuilt whole on every preview build. Never published.
PREVIEW = ".docs-preview"


def named_package(root: Path, name: str) -> Package:
    """The package whose directory is *name*; refuses naming the known."""
    packages = discover_packages(root)
    for package in packages:
        if package.directory.name == name:
            return package
    known = ", ".join(sorted(p.directory.name for p in packages)) or "none"
    fail(f"no package {name!r} in this workspace (known: {known})")


def scoped_config(root: Path, package: Package) -> str:
    """The scoped preview's config body: chrome plus one section.

    The workspace's site name and authored root pages, then only
    *package*'s section. No site URL and no release view: the
    preview is never published, and the release view is site-wide.
    Cross-package references resolve outward through the published
    workspace site's own inventory when the contract declares a
    site URL.
    """
    table = docs_table(root)
    title = str(table.get("title", "")) or _project_name(root)
    lines = ["[project]", f'site_name = "{title}"', "nav = ["]
    for page in _pages(root / "docs"):
        if page.startswith("_generated/"):
            continue
        label = "Home" if page == "index.md" else _label(page)
        lines.append(f'    {{ "{label}" = "{page}" }},')
    section, has_modules = _package_section(package)
    lines += section
    lines.append("]")
    if has_modules:
        inventories: list[str] = [*INVENTORIES]
        site_url = str(table.get("site_url", ""))
        if site_url:
            inventories.append(site_url.rstrip("/") + "/objects.inv")
        lines += _mkdocstrings_lines(
            [f"../../packages/{package.directory.name}/src"], tuple(inventories)
        )
    return "\n".join(lines) + "\n"


def materialise_preview(root: Path, package: Package) -> Path:
    """Materialise one package's preview tree; the scoped config's path.

    Rebuilt whole under ``.docs-preview/<name>/``: the authored root
    pages, the package's freshly generated mount and API trees, and
    the scoped config beside them. The rendered root config is never
    touched. Callers run the mount and generation passes first, so
    the copied trees are current.
    """
    from livery.workshop._provenance import generated_header

    name = package.directory.name
    base = root / PREVIEW / name
    shutil.rmtree(base, ignore_errors=True)
    docs = base / "docs"
    shutil.copytree(
        root / "docs",
        docs,
        ignore=lambda directory, names: (
            ["_generated"] if Path(directory) == root / "docs" else []
        ),
    )
    for source, destination in (
        (root / MOUNT / name, docs / "_generated" / "packages" / name),
        (root / API / name, docs / "_generated" / "api" / name),
    ):
        if source.is_dir():
            shutil.copytree(source, destination)
    config = base / "zensical.toml"
    config.write_text(
        generated_header("#") + scoped_config(root, package), encoding="utf-8"
    )
    return config


def _module_root(package: Package) -> Path | None:
    """The importable module's root: the shallowest ``__init__.py``."""
    src = package.directory / "src"
    if not src.is_dir():
        return None
    inits = sorted(src.rglob("__init__.py"), key=lambda p: len(p.parts))
    return inits[0].parent if inits else None


def api_modules(package: Package) -> list[tuple[str, str]]:
    """(page path, dotted import path) per module, public first.

    Every module gets a page, underscore-private included: the
    standards fragment publishes a docstring the moment it is
    written. Public sorts before private at every level of the
    tree, and a package's ``__init__`` is its index page.
    """
    module_root = _module_root(package)
    if module_root is None:
        return []
    src = package.directory / "src"
    entries: list[tuple[tuple[tuple[bool, str], ...], str, str]] = []
    for path in module_root.rglob("*.py"):
        if path.name == "__main__.py" or "_docs" in path.parts:
            continue
        relative = path.relative_to(module_root).with_suffix("")
        parts = relative.parts
        if parts and parts[-1] == "__init__":
            parts = (*parts[:-1], "")
        key = tuple((part.startswith("_"), part) for part in parts)
        page = (
            "index.md"
            if relative.as_posix() == "__init__"
            else relative.with_suffix(".md")
            .as_posix()
            .replace("/__init__.md", "/index.md")
        )
        dotted = ".".join(path.relative_to(src).with_suffix("").parts)
        dotted = dotted.removesuffix(".__init__")
        entries.append((key, page, dotted))
    entries.sort()
    return [(page, dotted) for _key, page, dotted in entries]


def generate_api_pages(root: Path) -> list[str]:
    """Write the API pages into the site tree; the package names.

    One page per module, each a single directive: mkdocstrings walks
    the members. The tree is rebuilt whole, machine territory.
    """
    base = root / API
    shutil.rmtree(base, ignore_errors=True)
    generated: list[str] = []
    for package in discover_packages(root):
        modules = api_modules(package)
        if not modules:
            continue
        for page, dotted in modules:
            target = base / package.directory.name / page
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"# `{dotted}`\n\n::: {dotted}\n", encoding="utf-8")
        generated.append(package.directory.name)
    return generated


def module_docs_dir(package: Package) -> Path | None:
    """Where *package*'s wheel-embedded ``_docs`` lives; None without src.

    The module root is the shallowest ``__init__.py`` under ``src``,
    which is the importable package uv_build ships.
    """
    module_root = _module_root(package)
    return None if module_root is None else module_root / "_docs"


def materialise_module_docs(package: Package) -> Path | None:
    """Refresh the wheel-embedded ``_docs`` from the package's docs.

    Machine territory: the copy is rebuilt whole so the wheel can
    never carry docs older than the tree it was built from, and a
    package without ``docs/`` gets its stale copy removed rather
    than shipped. Returns the materialised path, or None when the
    package has no module to carry it.
    """
    target = module_docs_dir(package)
    if target is None:
        return None
    shutil.rmtree(target, ignore_errors=True)
    docs = package.directory / "docs"
    if not docs.is_dir():
        return None
    shutil.copytree(docs, target)
    return target


#: The publish seam each forge kind defaults to.
DEFAULT_SEAMS = {"github": "pages", "gitlab": "pages", "gitea": "container"}


def publish_seam(root: Path) -> str:
    """The declared publish seam: pages, container, ssh, or none.

    The contract's ``[docs] publish`` wins; without it the forge kind
    picks its default. An unknown declaration fails naming the four.
    """
    table = docs_table(root)
    declared = str(table.get("publish", ""))
    if declared:
        if declared not in ("pages", "container", "ssh", "none"):
            fail(
                f"[docs] publish = {declared!r} is not a seam: use"
                " pages, container, ssh, or none"
            )
        return declared
    contract = tomllib.loads((root / "workshop.toml").read_text("utf-8"))
    kind = str((contract.get("forge") or {}).get("kind", ""))
    return DEFAULT_SEAMS.get(kind, "none")


def _publish_container(root: Path) -> None:
    """Build the site image and push it to the forge's registry.

    The image is the site: a pinned nginx serving ``site/`` on port
    80, tagged ``<registry>/<owner>/<repo>-docs:latest``, deployable
    anywhere. The push rides the ambient docker credential; a denied
    push teaches ``docker login`` rather than handling the token
    here.
    """
    import tempfile

    from livery.workshop._forge_lane import remote_repo_name
    from livery.workshop._registries import resolve_registry

    target = resolve_registry(root, "container")
    repo = remote_repo_name(root)
    image = f"{target.url}/{repo}-docs:latest".lower()
    dockerfile = "FROM nginx:1.27-alpine\nCOPY site /usr/share/nginx/html\n"
    with tempfile.NamedTemporaryFile("w", suffix=".Dockerfile", delete=False) as handle:
        handle.write(dockerfile)
        spec = handle.name
    docker = toolroom.docker.opts(cwd=root, nofail=True, recorded=False)
    built = docker("build", "-f", spec, "-t", image, ".")
    if built.code != 0:
        fail(f"docker build exited {built.code}:\n{built.stdout}{built.stderr}")
    pushed = docker("push", image)
    if pushed.code != 0:
        registry_host = target.url.split("/", 1)[0]
        fail(
            f"docker push {image} exited {pushed.code}:\n"
            f"{pushed.stdout}{pushed.stderr}"
            f"  A denied push wants `docker login {registry_host}` first."
        )
    print(f"  pushed {image}")


def _publish_ssh(root: Path) -> None:
    """Tar the site over ssh to the configured docs host, or skip.

    Unconfigured is a skip with exit 0, never a refusal: docs must
    not deploy where they can never be removed, and CI runs this
    unconditionally.
    """
    import os

    import footman

    host = os.environ.get("DOCS_HOST", "")
    user = os.environ.get("DOCS_USER", "")
    docs_root = os.environ.get("DOCS_ROOT", "")
    if not (host and user and docs_root):
        print("  ssh seam unconfigured (DOCS_HOST/DOCS_USER/DOCS_ROOT): skipping")
        return
    from livery.workshop._forge_lane import remote_repo_name

    target = f"{docs_root}/{remote_repo_name(root)}"
    destination = f"{user}@{host}"
    prepare = footman.run(
        ["ssh", destination, f"rm -rf {target} && mkdir -p {target}"],
        cwd=root,
        nofail=True,
        recorded=False,
    )
    if prepare != 0:
        fail(f"preparing {destination}:{target} exited {int(prepare)}")
    shipped = footman.run(
        f'tar -cf - -C site . | ssh {destination} "tar -xf - -C {target}"',
        shell=True,
        cwd=root,
        nofail=True,
        recorded=False,
    )
    if shipped != 0:
        fail(f"shipping the site exited {int(shipped)}")
    print(f"  deployed to {destination}:{target}")


docs_group = group("docs", help="The workspace's documentation site")


def _root() -> Path:
    from livery.workshop._layers import workspace_root

    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    return root


def _generate_all(root: Path) -> None:
    """Run the mount and generation passes, saying what was made."""
    mounted = mount_package_docs(root)
    if mounted:
        print(f"  mounted docs for {', '.join(mounted)}")
    logged = generate_changelog_pages(root)
    if logged:
        print(f"  changelogs for {', '.join(logged)}")
    documented = generate_api_pages(root)
    if documented:
        print(f"  API pages for {', '.join(documented)}")


@docs_group.task(name="build")
def docs_build(
    package: Annotated[
        str, doc("build a scoped preview of this package's section only")
    ] = "",
) -> None:
    """Mount every package's docs and build the site, strict.

    Strict is the point: a broken link or an orphan page fails here,
    on the machine, before CI says the same thing. ``--package``
    builds a scoped preview of one section instead, into the
    gitignored ``.docs-preview/`` directory; the preview is not
    strict, because chrome pages may link into sections it does not
    carry, and the workspace build owns strictness.
    """
    root = _root()
    _generate_all(root)
    if package:
        config = materialise_preview(root, named_package(root, package))
        result = toolroom.zensical.opts(cwd=config.parent, nofail=True).build(
            clean=True, config_file=str(config)
        )
        if result.code != 0:
            fail(
                f"zensical build exited {result.code}:\n{result.stdout}{result.stderr}"
            )
        print(f"  preview built at {config.parent / 'site'} (never published)")
        return
    releases = generate_release_pages(root)
    if releases:
        print(f"  release view: {', '.join(releases)}")
    result = toolroom.zensical.opts(cwd=root, nofail=True).build(
        clean=True, strict=True
    )
    if result.code != 0:
        fail(f"zensical build exited {result.code}:\n{result.stdout}{result.stderr}")
    print(f"  site built at {root / 'site'}")


@docs_group.task(name="publish")
def docs_publish() -> None:
    """Publish the built site through the contract's seam.

    ``pages`` is the forge workflow's own act and skips here;
    ``container`` pushes the site image to the forge registry;
    ``ssh`` tars the site to the configured host, skipping when
    unconfigured; ``none`` skips by declaration.
    """
    root = _root()
    if not (root / "site" / "index.html").is_file():
        fail(f"no built site at {root / 'site'}: run `docs.build` first")
    seam = publish_seam(root)
    if seam == "container":
        _publish_container(root)
    elif seam == "ssh":
        _publish_ssh(root)
    elif seam == "pages":
        print("  pages seam: the forge's own workflow deploys; nothing to do here")
    else:
        print("  publish seam is none: skipping by declaration")


@docs_group.task(name="serve", infinite=True)
def docs_serve(
    package: Annotated[
        str, doc("serve a scoped preview of this package's section only")
    ] = "",
) -> None:
    """Mount every package's docs and serve the site live.

    ``--package`` serves the scoped preview instead, from the
    gitignored ``.docs-preview/`` directory. Generated pages are
    copied at materialisation, so an edit to a package page needs a
    re-run to appear in the preview.
    """
    root = _root()
    _generate_all(root)
    if package:
        config = materialise_preview(root, named_package(root, package))
        toolroom.zensical.opts(cwd=config.parent).serve(config_file=str(config))
        return
    generate_release_pages(root)
    toolroom.zensical.opts(cwd=root).serve()
