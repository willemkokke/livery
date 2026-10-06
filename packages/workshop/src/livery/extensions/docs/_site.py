"""The documentation site: its rendered config, mounts, and verbs.

One rendered site per workspace. The config zensical reads is
assembled by the build, into a gitignored ``zensical.toml`` at the
root: the site's identity from the contract, the root pages, and one
section per package from the nav each package emits into its own
generated tree. Nothing committed enumerates the packages. Authors
write ``packages/<name>/docs/`` and the root ``docs/`` tree; every
underscore path this module writes is machine territory, refreshed by
the verbs and never edited by a person. Whether private members are
documented is the extensions' decision through the ``docs.members`` slot:
``public`` keeps each extractor's default filter, ``all`` documents
every member.

A package owns the shape of its own site section through
``docs/nav.toml``: a hand-authored tree the emitter merges under
that package's section of the rendered root config, checked both
ways (an entry naming a missing page refuses, and so does an
authored page absent from the nav). A generator owns a block between
markers in that tree (``tasks``, ``tools``), and the emitter's own
sections, Changelog, Coverage and API, are marker blocks too, which
the author may place; one the author did not place lands in that
order, the changelog and coverage entries ahead of the tasks block
and the API after everything. A package without a ``nav.toml`` gets its pages
enumerated, index first, and the machine sections in the same order.
``fm docs.build --package <name>``
builds a scoped preview of one section into the gitignored
``.docs-preview/`` directory; the workspace site stays the only
deploy artifact.
"""

from __future__ import annotations

import re
import shutil
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import livery.toolroom.tools.api as tools
from livery.footman.api import doc, fail, group
from livery.workshop import _extensions, _slots
from livery.workshop._contract import load_contract
from livery.workshop._docs_contract import (
    GENERATED,
    GENERATED_DIR,
    MOUNT,
    NAV_TOML,
    declines_api,
    docs_table,
    package_generators,
    publish_seam,
    site_reads,
)
from livery.workshop._kinds import ALL_MEMBERS, Extractor
from livery.workshop._navblocks import (
    NAV_BEGIN,
    NAV_END,
    nav_block_file,
)
from livery.workshop._packages import Package, discover_packages
from livery.workshop._prose import (
    HUMAN,
    Prose,
    fragments,
    repository_fragments,
    sections,
    shipped,
)

#: The config the build assembles and zensical reads, at the root; gitignored.
SITE_CONFIG = "zensical.toml"


#: The site tree's generated directories under the root ``docs/``,
#: gitignored: the mounts, the release view, the runner's task aliases
#: and the tool index. A root page never enumerates them.
SITE_TREES = ("packages", "releases", "tasks", "tools", "development")

#: Where the generated API pages live, per package directory name.
#: Where a package's generated API pages live: inside its mount, so
#: they publish under the package's own path.
API_DIR = "api"

#: The page a package whose kind extracts nothing gets instead of a
#: reference: the section names the absence, never an empty page.
NO_REFERENCE = "index.md"


def package_coverage_reports(package: Package) -> list[tuple[str, str]]:
    """The coverage reports *package* declares: (label, path) each.

    The ``[docs]`` table's ``coverage`` list: a label and the
    package-relative path where the package's own tooling writes a
    static HTML tree. The toolchain never learns the producing tool;
    coverage.py's htmlcov and a gcovr or llvm-cov tree are the same
    thing to it. A path reaching outside the package refuses.
    """
    contract_path = package.directory / "workshop.toml"
    contract = load_contract(contract_path)
    table = contract.get("docs") or {}
    declared = table.get("coverage") if isinstance(table, dict) else None
    if declared is None:
        return []
    if not isinstance(declared, list):
        fail(f"{contract_path}: [docs] coverage must be a list")
    reports: list[tuple[str, str]] = []
    for entry in declared:
        if (
            isinstance(entry, dict)
            and isinstance(entry.get("label"), str)
            and entry["label"]
            and isinstance(entry.get("path"), str)
            and entry["path"]
        ):
            path = Path(entry["path"])
            if path.is_absolute() or ".." in path.parts:
                fail(
                    f"{contract_path}: [docs] coverage path {entry['path']!r}"
                    " must stay inside the package"
                )
            reports.append((entry["label"], entry["path"]))
            continue
        fail(
            f"{contract_path}: [docs] coverage entry {entry!r} is not a"
            ' { label = "...", path = "..." } table'
        )
    return reports


def _slug(label: str) -> str:
    """A filesystem-safe slug for a report label."""
    import re

    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "report"


def generate_coverage_pages(root: Path) -> list[str]:
    """Copy declared coverage trees and write each coverage page.

    One page per declaring package, one iframe per report, into the
    package's generated tree, which the mount merges. A declared tree
    that is missing renders its section saying so and the build stays
    green: on a machine that has not measured, or a deploy without the
    artifacts, the absence is stated rather than failing the site.
    """
    written: list[str] = []
    for package in discover_packages(root):
        reports = package_coverage_reports(package)
        if not reports:
            continue
        name = package.member
        mount = package.directory / "docs" / GENERATED_DIR
        mount.mkdir(parents=True, exist_ok=True)
        lines = ["# Coverage", ""]
        for label, path in reports:
            source = package.directory / path
            slug = _slug(label)
            lines += [f"## {label}", ""]
            if (source / "index.html").is_file():
                target = mount / "coverage" / slug
                shutil.rmtree(target, ignore_errors=True)
                shutil.copytree(source, target)
                lines += [
                    f'<iframe src="coverage/{slug}/index.html"'
                    f' title="{label}"'
                    ' style="width: 100%; height: 80vh; border: none;">'
                    "</iframe>",
                    "",
                ]
            else:
                lines += [
                    f"The declared report (`{path}`) was not produced in this build.",
                    "",
                ]
        mount.mkdir(parents=True, exist_ok=True)
        (mount / "coverage.md").write_text(
            "\n".join(lines).rstrip() + "\n", encoding="utf-8"
        )
        written.append(name)
    return written


def run_generators(root: Path) -> list[str]:
    """Run every package's declared docs generators; the verbs run.

    Each verb runs as its own runner invocation at the workspace
    root, so a generator is exactly the task a person would type. A
    failing generator turns the build red with the verb and its
    package named; its own output has already streamed. Generators
    must be idempotent: a second run on unchanged input is a no-op.
    """
    import shutil as _shutil

    import livery.footman.api as footman

    ran: list[str] = []
    runner = ""
    for package in discover_packages(root):
        for verb, _requires in package_generators(package):
            if verb in ran:
                continue  # two declarations of one shared verb run it once
            if not runner:
                runner = _shutil.which(footman.prog()) or ""
                if not runner:
                    fail(
                        f"{footman.prog()} is not on PATH, so the declared"
                        f" docs generators cannot run; enter the"
                        " environment (source setup.sh) first"
                    )
            code = footman.run([runner, verb], cwd=root, nofail=True)
            if int(code) != 0:
                fail(
                    f"docs generator `{footman.prog()} {verb}`"
                    f" ({package.name}) exited {int(code)}"
                )
            ran.append(verb)
    return ran


def _root_pages(root: Path) -> list[str]:
    """The authored pages under the root ``docs/``: the site trees are not pages."""
    return [
        page for page in _pages(root / "docs") if page.split("/")[0] not in SITE_TREES
    ]


def _published(page: str) -> str:
    """*page* as it publishes inside its mount: a generated page loses the prefix."""
    return page.removeprefix(GENERATED)


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


#: The sections the emitter itself renders into a package's nav, in the
#: order they land when the author places none: a placed block fills
#: where its markers sit. Any other marker pair is a generator's block
#: (`tasks`, `tools`): its entries are what sits between the markers,
#: rendered where the author put them.
EMITTED_BLOCKS = ("changelog", "coverage", "api")

#: How a placed block travels through the parsed tree: a leaf whose
#: label and path both read `nav:<name>`, never a page.
SENTINEL = "nav:"


def package_nav(package: Package) -> list[object] | None:
    """The package's authored nav tree, or None without a ``nav.toml``.

    The file's ``nav`` key mirrors the rendered config's shape: a
    list of one-key tables mapping a label to a page path (relative
    to the package's ``docs/`` tree) or to a nested list. A placed
    machine block travels as a sentinel leaf, `nav:<name>`, where its
    markers sit. Anything else refuses naming the file and the entry.
    """
    authored = authored_nav(package)
    return None if authored is None else authored[0]


def authored_nav(
    package: Package,
) -> tuple[list[object], dict[str, list[object]]] | None:
    """The authored tree with its placed blocks as sentinels, and each block's entries.

    A marker pair names an emitted block or a generator's own; a
    marker without its twin, or a pair out of order, refuses naming
    the file and the block. The entries between a pair (the tasks
    block the task reference writes) come back keyed by the block's
    name, so the emitter renders them in the sentinel's place.
    """
    path = package.directory / "docs" / NAV_TOML
    if not path.is_file():
        return None
    generated = package.directory / "docs" / GENERATED_DIR
    text, blocks = _lift_blocks(path, path.read_text("utf-8"), generated)
    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        fail(f"{path} is not valid TOML: {error}")
    nav = parsed.get("nav")
    if not isinstance(nav, list):
        fail(f"{path} must carry a top-level `nav` list")
    _check_nav_shape(path, nav)
    return list(nav), blocks


_MARKER = re.compile(r"^(\s*)# nav:(begin|end) (\S+)\s*$")


def _lift_blocks(
    path: Path, text: str, generated: Path
) -> tuple[str, dict[str, list[object]]]:
    """*text* with each marker pair replaced by its sentinel; the pairs' entries.

    A block's entries come from the file its generator emitted under
    *generated* when there is one, else from between the markers.
    """
    lines = text.splitlines()
    found: dict[str, tuple[int, int, str]] = {}
    open_name: str | None = None
    open_at = 0
    for index, line in enumerate(lines):
        match = _MARKER.match(line)
        if match is None:
            continue
        indent, kind, name = match.groups()
        if kind == "begin":
            if open_name is not None or name in found:
                fail(f"{path}: the nav block {name!r} begins twice")
            open_name, open_at = name, index
            found[name] = (index, index, indent)
            continue
        if open_name != name:
            fail(f"{path}: the nav block {name!r} ends without beginning")
        found[name] = (open_at, index, found[name][2])
        open_name = None
    if open_name is not None:
        fail(f"{path}: the nav block {open_name!r} begins without ending")
    blocks: dict[str, list[object]] = {}
    lifted = list(lines)
    for name, (begin, end, indent) in sorted(
        found.items(), key=lambda item: -item[1][0]
    ):
        emitted = nav_block_file(generated, name)
        source = emitted if emitted.is_file() else path
        inner = (
            emitted.read_text("utf-8")
            if emitted.is_file()
            else f"nav = [\n{chr(10).join(lines[begin + 1 : end])}\n]"
        )
        try:
            parsed = tomllib.loads(inner)
        except tomllib.TOMLDecodeError as error:
            fail(f"{source}: the nav block {name!r} is not valid TOML: {error}")
        entries = parsed.get("nav")
        blocks[name] = list(entries) if isinstance(entries, list) else []
        lifted[begin : end + 1] = [
            f'{indent}{{ "{SENTINEL}{name}" = "{SENTINEL}{name}" }},'
        ]
    return "\n".join(lifted) + "\n", blocks


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
    likewise not required in the nav. ``includes/`` is exempt from the
    orphan check: its pages are snippet and glossary sources the
    extension set consumes (``docs/includes/abbreviations.md`` is
    auto-appended site-wide), never standalone pages.
    """
    docs = package.directory / "docs"
    leaves = _nav_leaves(entries)
    authored = {
        page
        for page in _pages(docs)
        if not page.startswith(("_generated/", "includes/"))
    }
    nav_path = docs / NAV_TOML
    missing = [
        leaf
        for leaf in leaves
        if not leaf.startswith(("_generated/", SENTINEL)) and leaf not in authored
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


def _authored_nav_lines(
    entries: list[object],
    prefix: str,
    indent: str,
    fill: Callable[[str, str], list[str]] | None = None,
) -> list[str]:
    """The rendered nav lines for an authored tree, paths prefixed.

    *fill* renders a sentinel leaf's block at the leaf's indentation;
    without it a sentinel renders as the leaf it is.
    """
    lines: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for label, value in entry.items():
            if isinstance(value, str) and value.startswith(SENTINEL) and fill:
                lines += fill(value.removeprefix(SENTINEL), indent)
            elif isinstance(value, str):
                page = _published(value)
                lines.append(f'{indent}{{ "{label}" = "{prefix}{page}" }},')
            elif isinstance(value, list):
                lines.append(f'{indent}{{ "{label}" = [')
                lines += _authored_nav_lines(value, prefix, indent + "    ", fill)
                lines.append(f"{indent}] }},")
    return lines


#: The workspace's own css, listed last while it exists, so its rules
#: win over every extension's: a file the workspace adds when it wants
#: one.
WORKSPACE_CSS = ("assets/site.css",)
#: Where the build stages the extensions' site assets, relative to the
#: docs tree, one directory per extension: machine territory, rebuilt
#: whole by every build and gitignored.
EXTENSION_ASSETS = "_extensions"
#: The directory of an extension's content that holds its site assets.
CONTENT_ASSETS = "docs/assets"
#: The workspace's own link-preview card image. Without it the site
#: uses the card the last extension ships under its site assets, and
#: without either it emits no image tags.
CARD_IMAGE = "docs/assets/og-card.png"


def abbreviation_files(root: Path) -> list[str]:
    """Every package's committed glossary, for the site-wide append.

    ``docs/includes/abbreviations.md`` per package, as paths relative
    to *root*. Tooltips are site-wide, so a term two packages define
    differently refuses naming the term and both packages; the same
    definition twice is allowed.
    """
    import re

    definitions: dict[str, tuple[str, str]] = {}
    files: list[str] = []
    for package in discover_packages(root):
        glossary = package.directory / "docs" / "includes" / "abbreviations.md"
        if not glossary.is_file():
            continue
        files.append(glossary.relative_to(root).as_posix())
        for line in glossary.read_text("utf-8").splitlines():
            match = re.match(r"\*\[(.+?)\]:\s*(.+)", line)
            if match is None:
                continue
            term, definition = match.group(1), match.group(2).strip()
            known = definitions.get(term)
            if known is not None and known[0] != definition:
                fail(
                    f"abbreviation {term!r} is defined differently by"
                    f" {known[1]} and {package.name}; tooltips are"
                    " site-wide, so one definition must win: agree on"
                    " one, or rename the terms apart"
                )
            definitions.setdefault(term, (definition, package.name))
    return files


#: The slot deciding whether private members are documented: ``public``
#: keeps each extractor's default filter, ``all`` documents every
#: member. A scalar the nearest extension decides; the base contributes
#: nothing, so its default stands until an extension says otherwise.
MEMBERS_SLOT = "docs.members"


def members_policy() -> str:
    """The composed private-members policy, ``public`` or ``all``."""
    return str(_slots.composed(MEMBERS_SLOT))


def package_docs_extras(package: Package) -> tuple[list[str], list[object]]:
    """The css and javascript a package's ``[docs]`` table declares.

    ``extra-css`` entries are paths relative to the package's
    ``docs/`` tree; ``extra-javascript`` entries are the same, or
    tables carrying ``path`` with ``type``, ``defer``, and ``async``.
    Anything else refuses naming the file and the entry.
    """
    contract_path = package.directory / "workshop.toml"
    contract = load_contract(contract_path)
    table = contract.get("docs") or {}
    if not isinstance(table, dict):
        return ([], [])
    css_declared = table.get("extra-css", [])
    js_declared = table.get("extra-javascript", [])
    if not isinstance(css_declared, list) or not all(
        isinstance(entry, str) for entry in css_declared
    ):
        fail(f"{contract_path}: [docs] extra-css must be a list of paths")
    allowed = {"path", "type", "defer", "async"}
    for entry in js_declared if isinstance(js_declared, list) else ():
        if isinstance(entry, str):
            continue
        if (
            isinstance(entry, dict)
            and isinstance(entry.get("path"), str)
            and set(entry) <= allowed
        ):
            continue
        fail(
            f"{contract_path}: [docs] extra-javascript entry {entry!r} is"
            " not a path or a { path, type, defer, async } table"
        )
    if not isinstance(js_declared, list):
        fail(f"{contract_path}: [docs] extra-javascript must be a list")
    return (list(css_declared), list(js_declared))


def _js_line(entry: object, prefix: str) -> str:
    """One rendered ``extra_javascript`` entry, path prefixed."""
    if isinstance(entry, str):
        return f'    "{prefix}{entry}",'
    assert isinstance(entry, dict)
    parts = [f'path = "{prefix}{entry["path"]}"']
    for key in ("type",):
        if key in entry:
            parts.append(f'{key} = "{entry[key]}"')
    for key in ("defer", "async"):
        if key in entry:
            parts.append(f"{key} = {'true' if entry[key] else 'false'}")
    return "    { " + ", ".join(parts) + " },"


def extension_assets(root: Path) -> list[tuple[str, Path]]:
    """Each mounted extension that ships site assets, with its assets directory.

    In the workspace's extension order. An extension ships site assets under
    ``content/docs/assets/`` in its wheel; an extension without that
    directory, or not installed, ships none and is not listed.
    """
    found: list[tuple[str, Path]] = []
    for extension in _extensions.stack_names(root):
        content = _extensions.extension_content(extension)
        if content is None:
            continue
        assets = content / CONTENT_ASSETS
        if assets.is_dir():
            found.append((extension, assets))
    return found


def card_path(root: Path) -> str:
    """The link-preview card's path in the site, or empty when there is none.

    The workspace's own ``docs/assets/og-card.png`` first, then the card
    the last extension in the workspace's order ships, at the path the
    build stages it under.
    """
    if (root / CARD_IMAGE).is_file():
        return "assets/og-card.png"
    name = Path(CARD_IMAGE).name
    shipped = [
        extension
        for extension, assets in extension_assets(root)
        if (assets / name).is_file()
    ]
    if not shipped:
        return ""
    return f"{EXTENSION_ASSETS}/{shipped[-1]}/assets/{name}"


def stage_extension_assets(root: Path) -> list[str]:
    """Copy every extension's site assets under ``docs/_extensions/``; those staged.

    Rebuilt whole: a file an extension no longer ships leaves no stale
    copy, and an extension that left the workspace loses its directory.
    """
    base = root / "docs" / EXTENSION_ASSETS
    shutil.rmtree(base, ignore_errors=True)
    staged: list[str] = []
    for extension, assets in extension_assets(root):
        shutil.copytree(assets, base / extension / "assets")
        staged.append(extension)
    return staged


def _extra_asset_lines(root: Path) -> list[str]:
    """``extra_css`` and ``extra_javascript`` for the whole site.

    The css in cascade order: every extension's staged sheets in extension
    order, then each package's declared entries at its mounted paths,
    then the workspace's own sheet while it exists, so the instance's
    rules win. Top-level ``[project]`` keys, so they render before
    any subtable.
    """
    css = [
        f"{EXTENSION_ASSETS}/{extension}/assets/{sheet.name}"
        for extension, assets in extension_assets(root)
        for sheet in sorted(assets.glob("*.css"))
    ]
    js: list[str] = []
    for package in discover_packages(root):
        declared_css, declared_js = package_docs_extras(package)
        prefix = f"packages/{package.member}/"
        css += [prefix + _published(entry) for entry in declared_css]
        js += [_js_line(entry, prefix) for entry in declared_js]
    css += [name for name in WORKSPACE_CSS if (root / "docs" / name).is_file()]
    lines: list[str] = []
    if css:
        lines.append("extra_css = [")
        lines += [f'    "{entry}",' for entry in css]
        lines.append("]")
    if js:
        lines.append("extra_javascript = [")
        lines += js
        lines.append("]")
    return lines


#: The slot deciding the theme block's values. The base's block is
#: the default; a theme extension contributes a table of the keys it
#: changes, and contributions merge key by key in contribution order,
#: so the nearest extension's keys win.
THEME_SLOT = "docs.theme"
#: The base's theme: the stock fonts, and a palette that follows the
#: OS with a manual light/dark/auto toggle cycle.
THEME_DEFAULT: dict[str, object] = {
    "language": "en",
    "font.text": "Inter",
    "font.code": "Fira Code",
    "features": [
        "announce.dismiss",
        "content.code.annotate",
        "content.code.copy",
        "content.tooltips",
        "navigation.footer",
        "navigation.indexes",
        "navigation.instant",
        "navigation.instant.prefetch",
        "navigation.sections",
        "navigation.tabs",
        "navigation.tabs.sticky",
        "navigation.top",
        "navigation.tracking",
        "search.highlight",
        "toc.follow",
    ],
    "palette": [
        {
            "media": "(prefers-color-scheme)",
            "toggle.icon": "lucide/sun-moon",
            "toggle.name": "Switch to light mode",
        },
        {
            "media": "(prefers-color-scheme: light)",
            "scheme": "default",
            "toggle.icon": "lucide/sun",
            "toggle.name": "Switch to dark mode",
        },
        {
            "media": "(prefers-color-scheme: dark)",
            "scheme": "slate",
            "toggle.icon": "lucide/moon",
            "toggle.name": "Switch to system preference",
        },
    ],
}


def _merge_theme(values: list[object]) -> object:
    """The theme's values: the default, each contribution's keys over it.

    Raises:
        SlotError: when a contribution is not a table, or names a key
            outside the block's vocabulary.
    """
    theme: dict[str, object] = dict(THEME_DEFAULT)
    for value in values:
        if not isinstance(value, dict):
            raise _slots.SlotError(
                f"slot {THEME_SLOT!r}: a contribution is a table of the theme's"
                f" keys, not {value!r}"
            )
        table: dict[object, object] = dict(value)
        for key, setting in table.items():
            if not isinstance(key, str) or key not in THEME_DEFAULT:
                raise _slots.SlotError(
                    f"slot {THEME_SLOT!r}: unknown key {key!r}; the keys are"
                    f" {', '.join(THEME_DEFAULT)}"
                )
            theme[key] = setting
    return theme


def theme_values() -> dict[str, object]:
    """The composed theme block's values, by key."""
    composed = _slots.composed(THEME_SLOT)
    assert isinstance(composed, dict)
    table: dict[object, object] = dict(composed)
    return {str(key): value for key, value in table.items()}


def _theme_lines(theme: dict[str, object]) -> list[str]:
    """The theme block from its composed values.

    The override directory is the assembly's own: it carries the
    rendered link-preview template.
    """
    lines = [
        "",
        "[project.theme]",
        f'language = "{theme["language"]}"',
        f'font.text = "{theme["font.text"]}"',
        f'font.code = "{theme["font.code"]}"',
        'custom_dir = "overrides"',
        "features = [",
    ]
    features = theme["features"]
    assert isinstance(features, list)
    lines += [f'    "{feature}",' for feature in features]
    lines.append("]")
    palettes = theme["palette"]
    assert isinstance(palettes, list)
    for palette in palettes:
        assert isinstance(palette, dict)
        entries: dict[object, object] = dict(palette)
        lines.append("")
        lines.append("[[project.theme.palette]]")
        lines += [f'{key} = "{value}"' for key, value in entries.items()]
    return lines


def _extension_block(root: Path, *, relative_to: str = ".") -> list[str]:
    """The markdown extension set, the standard every site carries.

    ``relative_to`` prefixes the repo-anchored paths (snippet homes,
    glossaries) so the scoped preview, whose config lives two levels
    down, resolves the same committed sources.
    """
    anchor = "" if relative_to == "." else relative_to.rstrip("/") + "/"
    snippet_paths = [relative_to] + [
        f"{anchor}packages/{package.member}/_generated"
        for package in discover_packages(root)
    ]
    appended = [f"{anchor}{path}" for path in abbreviation_files(root)]
    lines = [
        "",
        "# Markdown extensions: the workshop's standard set.",
        "[project.markdown_extensions.abbr]",
        "[project.markdown_extensions.admonition]",
        "[project.markdown_extensions.attr_list]",
        "[project.markdown_extensions.def_list]",
        "[project.markdown_extensions.footnotes]",
        "[project.markdown_extensions.md_in_html]",
        "[project.markdown_extensions.toc]",
        "permalink = true",
        "[project.markdown_extensions.pymdownx.arithmatex]",
        "generic = true",
        "[project.markdown_extensions.pymdownx.betterem]",
        "[project.markdown_extensions.pymdownx.caret]",
        "[project.markdown_extensions.pymdownx.details]",
        "[project.markdown_extensions.pymdownx.emoji]",
        'emoji_generator = "zensical.extensions.emoji.to_svg"',
        'emoji_index = "zensical.extensions.emoji.twemoji"',
        "[project.markdown_extensions.pymdownx.highlight]",
        "anchor_linenums = true",
        'line_spans = "__span"',
        "pygments_lang_class = true",
        "[project.markdown_extensions.pymdownx.inlinehilite]",
        "[project.markdown_extensions.pymdownx.keys]",
        "[project.markdown_extensions.pymdownx.magiclink]",
        "[project.markdown_extensions.pymdownx.mark]",
        "[project.markdown_extensions.pymdownx.smartsymbols]",
        "[project.markdown_extensions.pymdownx.superfences]",
        "custom_fences = [",
        '    { name = "mermaid", class = "mermaid",'
        ' format = "pymdownx.superfences.fence_code_format" },',
        "]",
        "[project.markdown_extensions.pymdownx.tabbed]",
        "alternate_style = true",
        "combine_header_slug = true",
        "[project.markdown_extensions.pymdownx.tasklist]",
        "custom_checkbox = true",
        "[project.markdown_extensions.pymdownx.tilde]",
        "[project.markdown_extensions.pymdownx.snippets]",
        "base_path = [",
    ]
    lines += [f'    "{path}",' for path in snippet_paths]
    lines += ["]", "check_paths = true"]
    if appended:
        lines.append("auto_append = [")
        lines += [f'    "{path}",' for path in appended]
        lines.append("]")
    return lines


def overrides_template(root: Path) -> str:
    """The rendered theme override: link-preview tags, instance facts.

    The brand leads on the home page, because that is the URL that
    gets posted and a feed truncates from the right; elsewhere the
    page leads. The image tags name the workspace's own card, else the
    card an extension ships, else are left out. Every URL is absolute:
    a preview is fetched by someone else's server, which has no page to
    resolve a relative path against.
    """
    table = docs_table(root)
    title = str(table.get("title", "")) or _project_name(root)
    description = str(table.get("description", ""))
    card = card_path(root)
    image_block = (
        (
            """
  {% set image = config.site_url ~ "__CARD__" %}
  <meta property="og:image" content="{{ image }}">
  <meta property="og:image:type" content="image/png">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta property="og:image:alt" content="__ALT__">
  <meta name="twitter:image" content="{{ image }}">"""
            if card
            else ""
        )
        .replace("__ALT__", description or title)
        .replace("__CARD__", card)
    )
    return (
        (
            """{% extends "base.html" %}

{% block extrahead %}
  {% set page_title = page.meta.title
                      if page.meta and page.meta.title
                      else (page.title | striptags
                            if page.title else config.site_name) %}
  {% set title = (config.site_name ~ " - " ~ page_title)
                 if page.canonical_url == config.site_url
                 else (page_title ~ " - " ~ config.site_name) %}
  {% set description = page.meta.description if page.meta and page.meta.description
                       else config.site_description %}

  <meta property="og:type" content="website">
  <meta property="og:site_name" content="{{ config.site_name }}">
  <meta property="og:title" content="{{ title }}">
  <meta property="og:description" content="{{ description }}">
  <meta property="og:url" content="{{
    page.canonical_url if page.canonical_url else config.site_url }}">
  <meta name="twitter:card" content="__CARD__">
  <meta name="twitter:title" content="{{ title }}">
  <meta name="twitter:description" content="{{ description }}">__IMAGE__
{% endblock %}
"""
        )
        .replace("__IMAGE__", image_block)
        .replace("__CARD__", "summary_large_image" if card else "summary")
    )


def _toml_value(value: object) -> str:
    """*value* as a TOML literal: a bool, a number, a string, or a list of those."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        items: list[object] = list(value)
        return "[" + ", ".join(_toml_value(item) for item in items) + "]"
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def handler_lines(
    extractor: Extractor,
    paths: list[str],
    inventories: tuple[str, ...],
    members: str,
) -> list[str]:
    """The mkdocstrings handler block for *extractor* over *paths*.

    The handler's search paths and inventories, then its options as
    the extractor carries them, then the members policy: ``all``
    writes an empty filter list, so every member is documented, and
    ``public`` leaves the handler's own default filter standing.
    """
    listed = ", ".join(f'"{path}"' for path in paths)
    linked = ", ".join(f'"{url}"' for url in inventories)
    lines = [
        "",
        f"[project.plugins.mkdocstrings.handlers.{extractor.name}]",
        f"paths = [{listed}]",
        f"inventories = [{linked}]",
    ]
    if extractor.options or members == ALL_MEMBERS:
        lines += [
            "",
            f"[project.plugins.mkdocstrings.handlers.{extractor.name}.options]",
        ]
        lines += [
            f"{key} = {_toml_value(value)}" for key, value in extractor.options.items()
        ]
        if members == ALL_MEMBERS:
            lines.append("filters = []")
    return lines


def zensical_config(root: Path) -> str:
    """The assembled ``zensical.toml`` body, header excluded.

    Site identity comes from the ``[docs]`` table (title defaults to
    the root project's name); the nav carries the root ``docs/``
    pages, the release view and every package's section at its mount
    path; the theme, extension set, and asset lists follow. The whole
    file is the build's: hand customisation goes through the contract
    and the packages' ``nav.toml`` files.
    """
    table = docs_table(root)
    title = str(table.get("title", "")) or _project_name(root)
    site_url = str(table.get("site-url", ""))
    description = str(table.get("description", ""))
    lines = ["[project]", f'site_name = "{title}"']
    if site_url:
        lines.append(f'site_url = "{site_url}"')
    if description:
        lines.append(f'site_description = "{description}"')
    lines.append("nav = [")
    lines.append(f"    {NAV_BEGIN}")
    for page in _root_pages(root):
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
        lines.append('    { "Releases" = "releases/index.md" },')
    lines += development_nav_lines(root)
    from livery.workshop._kinds import kind_extractor

    handlers: dict[str, tuple[Extractor, list[str]]] = {}
    for package in discover_packages(root):
        section, _extracts = _package_section(package)
        if not section:
            continue
        lines += section
        extractor = kind_extractor(package.kind)
        if extractor is None:
            continue
        # One handler block per extractor, over every package that
        # extracts through it, in the packages' order.
        _, paths = handlers.setdefault(extractor.name, (extractor, []))
        paths += extractor.sources(package)
    lines.append(f"    {NAV_END}")
    lines.append("]")
    lines += _extra_asset_lines(root)
    lines += _theme_lines(theme_values())
    lines += _extension_block(root)
    members = members_policy()
    for extractor, paths in handlers.values():
        if paths:
            lines += handler_lines(extractor, paths, extractor.inventories, members)
    return "\n".join(lines) + "\n"


def _package_section(package: Package, indent: str = "    ") -> tuple[list[str], bool]:
    """One package's nav section lines, and whether it has API modules.

    The authored ``nav.toml`` tree when the package ships one
    (checked both ways first), the enumerated authored pages
    otherwise; then the changelog and API entries, which are machine
    territory in either shape. Empty for a package with nothing to
    show.
    """
    from livery.workshop._kinds import kind_extractor

    docs = package.directory / "docs"
    pages = [p for p in _pages(docs) if not p.startswith("_generated/")]
    modules = api_modules(package)
    # A kind that extracts nothing still gets an API entry, to the
    # page that names the absence, unless the package declined.
    absent = kind_extractor(package.kind) is None and not declines_api(package)
    changelog = (package.directory / "CHANGELOG.md").is_file()
    authored = authored_nav(package)
    if authored is None and not pages and not modules and not changelog and not absent:
        return ([], False)
    name = package.member
    prefix = f"packages/{name}/"
    inner = indent + "    "
    tree, blocks = authored if authored is not None else ([], {})

    def section(block: str, at: str) -> list[str]:
        if block == "changelog" and changelog:
            return [f'{at}{{ "Changelog" = "{prefix}changelog.md" }},']
        if block == "coverage" and package_coverage_reports(package):
            return [f'{at}{{ "Coverage" = "{prefix}coverage.md" }},']
        if block not in EMITTED_BLOCKS and blocks.get(block):
            return _authored_nav_lines(blocks[block], prefix, at)
        if block == "api" and modules:
            api = [f'{at}{{ "API" = [']
            api += [
                f'{at}    {{ "{dotted}" = "{prefix}{API_DIR}/{page}" }},'
                for page, dotted in modules
            ]
            api.append(f"{at}] }},")
            return api
        if block == "api" and absent:
            return [f'{at}{{ "API" = "{prefix}{API_DIR}/{NO_REFERENCE}" }},']
        return []

    placed = {
        leaf.removeprefix(SENTINEL)
        for leaf in _nav_leaves(tree)
        if leaf.startswith(SENTINEL)
    }
    unplaced = [block for block in EMITTED_BLOCKS if block not in placed]

    def fill(block: str, at: str) -> list[str]:
        # The changelog and coverage entries the author left unplaced
        # land ahead of the tasks block: a reader parses what follows
        # the tasks tree as part of it.
        lead: list[str] = []
        if block == "tasks":
            for early in ("changelog", "coverage"):
                if early in unplaced:
                    lead += section(early, at)
        return lead + section(block, at)

    lines = [f'{indent}{{ "{name}" = [']
    if authored is not None:
        check_package_nav(package, tree)
        lines += _authored_nav_lines(tree, prefix, inner, fill)
        if "tasks" in placed:
            unplaced = [b for b in unplaced if b not in ("changelog", "coverage")]
    else:
        for page in pages:
            lines.append(f'{inner}{{ "{_label(page)}" = "{prefix}{page}" }},')
    for block in unplaced:
        lines += section(block, inner)
    lines.append(f"{indent}] }},")
    return (lines, bool(modules))


RELEASES = "docs/releases"


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
    from livery.workshop._release_notes import release_notes

    notes = release_notes()
    try:
        unreleased = notes.entry(root, package) if notes is not None else ""
    except BaseException as error:
        print(f"  {package.name}: unreleased section skipped: {error}")
        unreleased = ""
    if unreleased:
        text = _insert_after_title(text, unreleased)
    return text


def generate_changelog_pages(root: Path) -> list[str]:
    """Write each package's changelog page into its generated tree; the names.

    The mount merges the tree, so the page lands where the nav points.
    """
    generated: list[str] = []
    for package in discover_packages(root):
        page = changelog_page(root, package)
        if page is None:
            continue
        target = package.directory / "docs" / GENERATED_DIR / "changelog.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(page, encoding="utf-8")
        generated.append(package.member)
    return generated


def _receipt_tags(root: Path) -> list[tuple[str, str, str, str]]:
    """(date, package dir, dist name, version) per release tag, newest first.

    The receipt tags are the release identity (the train cuts them
    after the index confirms), so the view derives from them and the
    changelogs alone; nothing new is committed.
    """
    import re

    result = tools.git.opts(cwd=root, nofail=True, recorded=False)(
        "for-each-ref",
        "refs/tags/packages",
        "--format=%(refname:short) %(creatordate:short)",
    )
    if result.code != 0:
        return []
    names = {p.member: p.name for p in discover_packages(root)}
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


#: The build's own scratch, gitignored: one digest stamp per mounted
#: package, so an unchanged section is not copied again.
BUILD_DIR = ".docs-build"


#: The coverage report tree a package's generated docs carry: coverage.py
#: stamps its creation time into every page, so the tree never digests
#: the same twice, and the mount refreshes it on every run instead.
COVERAGE_TREE = f"{GENERATED}coverage/"


def section_digest(docs: Path) -> str:
    """A digest of a package's docs tree: every file's path and bytes.

    Content, never mtimes: a generator rewrites its pages on every
    run, and an unchanged page must not move the digest. The coverage
    report tree is left out, since a measured report is stamped with
    its time and would move the digest on every build.
    """
    import hashlib

    digest = hashlib.sha256()
    for path in sorted(p for p in docs.rglob("*") if p.is_file()):
        relative = path.relative_to(docs).as_posix()
        if relative.startswith(COVERAGE_TREE):
            continue
        digest.update(relative.encode() + b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\n")
    return digest.hexdigest()


def mount_package_docs(root: Path, *, full: bool = False) -> list[str]:
    """Mount every package's ``docs/`` into the site tree; the names copied.

    A package's mount is rebuilt whole when its docs tree's digest
    moved since the last mount, or with *full*; an unchanged section
    is left in place with its coverage report refreshed, and a package
    that left the workspace loses its mount. ``nav.toml`` stays behind:
    it configures the nav and is not site content. The package's
    generated tree merges into the mount's root and every link into it
    in a mounted page loses the prefix, so ``_generated`` names a
    directory on disk and never a published path. A generated page
    that an authored page already holds the path of refuses, naming
    both.
    """
    base = root / MOUNT
    stamps = root / BUILD_DIR
    stamps.mkdir(exist_ok=True)
    if full:
        shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True, exist_ok=True)
    present = {package.member for package in discover_packages(root)}
    for stale in _stale_mounts(base, present):
        shutil.rmtree(base / stale, ignore_errors=True)
        (stamps / f"{stale}.digest").unlink(missing_ok=True)
    mounted: list[str] = []
    for package in discover_packages(root):
        docs = package.directory / "docs"
        name = package.member
        target = base / name
        stamp = stamps / f"{name}.digest"
        if not docs.is_dir():
            shutil.rmtree(target, ignore_errors=True)
            stamp.unlink(missing_ok=True)
            continue
        digest = section_digest(docs)
        if (
            not full
            and target.is_dir()
            and stamp.is_file()
            and stamp.read_text(encoding="utf-8") == digest
        ):
            _refresh_coverage(docs, target)
            continue
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(
            docs,
            target,
            ignore=lambda directory, names, docs=docs: (
                [NAV_TOML, GENERATED_DIR]
                if Path(directory) == docs
                # The package's setup around its examples is not site content.
                else ["conftest.py"]
                if Path(directory) == docs / "examples"
                else []
            ),
        )
        _merge_generated(docs / GENERATED_DIR, target, package.path)
        _strip_generated_links(target)
        # A member in a group directory keeps its stamp in the group's.
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(digest, encoding="utf-8")
        mounted.append(name)
    return mounted


def _stale_mounts(base: Path, present: set[str]) -> list[str]:
    """The mounts under *base* no present member owns, by their path under it.

    A member is ``<name>`` or ``<group>/<name>``: a top-level directory
    that is no member and no member's group is stale whole, and inside
    a group each directory that is no member is stale on its own.
    """
    groups = {member.partition("/")[0] for member in present if "/" in member}
    stale: list[str] = []
    for entry in sorted(base.iterdir()):
        if entry.name in present:
            continue
        if entry.name not in groups:
            stale.append(entry.name)
            continue
        stale += [
            f"{entry.name}/{child.name}"
            for child in sorted(entry.iterdir())
            if f"{entry.name}/{child.name}" not in present
        ]
    return stale


def _refresh_coverage(docs: Path, target: Path) -> None:
    """Copy the coverage report tree into an unchanged mount; the digest skips it."""
    source = docs / COVERAGE_TREE.rstrip("/")
    destination = target / "coverage"
    if source.is_dir():
        shutil.rmtree(destination, ignore_errors=True)
        shutil.copytree(source, destination)


def published_url(page: str) -> str:
    """The URL path a markdown page publishes at with directory URLs.

    ``a/b.md`` and ``a/b/index.md`` are both ``a/b/``; the root
    ``index.md`` is the empty path.
    """
    stem = page.removesuffix(".md")
    if stem == "index":
        return ""
    if stem.endswith("/index"):
        return stem.removesuffix("index")
    return stem + "/"


def _merge_generated(generated: Path, target: Path, package_path: str) -> None:
    """Copy the package's generated tree into the mount's root, path by path.

    A generated page links upward with the generated directory counted
    in: ``../input.md`` from ``_generated/api.md`` names the authored
    page beside the tree. Merged one level up, such a link loses one
    ``../``; a link that stays inside the generated tree keeps its
    shape, since both ends moved together.
    """
    if not generated.is_dir():
        return
    # With directory URLs a page publishes at its path less ``.md``, and
    # an ``index.md`` at its directory: ``api.md`` and ``api/index.md``
    # are one URL, and the build would serve whichever it read last.
    authored = {
        published_url(page.relative_to(target).as_posix()): page.relative_to(
            target
        ).as_posix()
        for page in target.rglob("*.md")
    }
    for source in sorted(generated.rglob("*")):
        if source.is_dir() or source.suffix == ".toml":
            continue  # the nav files configure the section; not site content
        relative = source.relative_to(generated).as_posix()
        destination = target / relative
        holder = (
            authored.get(published_url(relative)) if source.suffix == ".md" else None
        )
        if holder is None and destination.exists():
            holder = relative
        if holder is not None:
            fail(
                f"{package_path}/docs: {GENERATED}{relative} and the authored"
                f" {holder} would publish at one path; rename one"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix == ".md":
            depth = relative.count("/")
            text = source.read_text(encoding="utf-8")
            destination.write_text(_climb_one_less(text, depth), encoding="utf-8")
        else:
            shutil.copy2(source, destination)


_UPWARD_LINK = re.compile(r"\]\(((?:\.\./)+)")


def _climb_one_less(text: str, depth: int) -> str:
    """*text* with one ``../`` fewer on every link that climbs above *depth*."""

    def shorten(match: re.Match[str]) -> str:
        climbs = match.group(1).count("../")
        if climbs <= depth:
            return match.group(0)
        return "](" + "../" * (climbs - 1)

    return _UPWARD_LINK.sub(shorten, text)


def _strip_generated_links(target: Path) -> None:
    """Every link into the generated tree in a mounted page loses the prefix."""
    for page in target.rglob("*.md"):
        text = page.read_text(encoding="utf-8")
        stripped = text.replace(f"]({GENERATED}", "](")
        if stripped != text:
            page.write_text(stripped, encoding="utf-8")


#: The scoped previews' home, gitignored; one directory per package,
#: rebuilt whole on every preview build. Never published.
PREVIEW = ".docs-preview"


def named_package(root: Path, name: str) -> Package:
    """The package whose directory is *name*; refuses naming the known."""
    packages = discover_packages(root)
    for package in packages:
        if package.member == name:
            return package
    known = ", ".join(sorted(p.member for p in packages)) or "none"
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
    for page in _root_pages(root):
        label = "Home" if page == "index.md" else _label(page)
        lines.append(f'    {{ "{label}" = "{page}" }},')
    from livery.workshop._kinds import kind_extractor

    section, extracts = _package_section(package)
    lines += section
    lines.append("]")
    lines += _theme_lines(theme_values())
    lines += _extension_block(root, relative_to="../..")
    extractor = kind_extractor(package.kind)
    if extracts and extractor is not None:
        inventories: list[str] = [*extractor.inventories]
        site_url = str(table.get("site-url", ""))
        if site_url:
            inventories.append(site_url.rstrip("/") + "/objects.inv")
        paths = [f"../../{path}" for path in extractor.sources(package)]
        lines += handler_lines(extractor, paths, tuple(inventories), members_policy())
    return "\n".join(lines) + "\n"


def materialise_preview(root: Path, package: Package) -> Path:
    """Materialise one package's preview tree; the scoped config's path.

    Rebuilt whole under ``.docs-preview/<name>/``: the authored root
    pages, the package's freshly generated mount with its API pages,
    and the scoped config beside them. The rendered root config is never
    touched. Callers run the mount and generation passes first, so
    the copied trees are current.
    """
    from livery.workshop._provenance import generated_header

    name = package.member
    base = root / PREVIEW / name
    shutil.rmtree(base, ignore_errors=True)
    docs = base / "docs"
    shutil.copytree(
        root / "docs",
        docs,
        ignore=lambda directory, names: (
            list(SITE_TREES) if Path(directory) == root / "docs" else []
        ),
    )
    mount = root / MOUNT / name
    if mount.is_dir():
        shutil.copytree(mount, docs / "packages" / name)
    overrides = root / "overrides"
    if overrides.is_dir():
        shutil.copytree(overrides, base / "overrides")
    config = base / "zensical.toml"
    config.write_text(
        generated_header("#") + scoped_config(root, package), encoding="utf-8"
    )
    return config


def api_modules(package: Package) -> list[tuple[str, str]]:
    """The reference pages of *package*, `(page path, dotted name)`, by its kind.

    Extraction belongs to the kind: the pages come from the kind's
    extractor, and a kind without one has no pages, which the site
    says by name on the absence page.
    """
    from livery.workshop._kinds import kind_extractor

    extractor = kind_extractor(package.kind)
    if extractor is None:
        return []
    return extractor.pages(package)


def generate_api_pages(root: Path) -> list[str]:
    """Write the API pages into each package's generated tree; the package names.

    One page per module, each a single directive: mkdocstrings walks
    the members. Written under ``api/`` in the tree the mount merges,
    so the pages publish under the package's own path.
    """
    from livery.workshop._kinds import kind_extractor

    generated: list[str] = []
    for package in discover_packages(root):
        base = package.directory / "docs" / GENERATED_DIR / API_DIR
        if kind_extractor(package.kind) is None and not declines_api(package):
            # The absence, named: a kind that extracts nothing gets
            # one page saying so, never an empty section.
            shutil.rmtree(base, ignore_errors=True)
            base.mkdir(parents=True, exist_ok=True)
            (base / NO_REFERENCE).write_text(
                "# API reference\n\nNo API reference: the"
                f" `{package.kind}` kind declares no extractor.\n",
                encoding="utf-8",
            )
            generated.append(package.member)
            continue
        modules = api_modules(package)
        # The generated reference is rebuilt whole: a package that
        # declines, or extracts nothing now, keeps no stale pages.
        shutil.rmtree(base, ignore_errors=True)
        if not modules:
            continue
        for page, dotted in modules:
            target = base / page
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"# `{dotted}`\n\n::: {dotted}\n", encoding="utf-8")
        generated.append(package.member)
    return generated


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
    docker = tools.docker.opts(cwd=root, nofail=True, recorded=False)
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

    import livery.footman.api as footman
    import livery.toolroom.tools.api as tools

    host = os.environ.get("DOCS_HOST", "")
    user = os.environ.get("DOCS_USER", "")
    docs_root = os.environ.get("DOCS_ROOT", "")
    if not (host and user and docs_root):
        print("  ssh seam unconfigured (DOCS_HOST/DOCS_USER/DOCS_ROOT): skipping")
        return
    from livery.workshop._forge_lane import remote_repo_name

    target = f"{docs_root}/{remote_repo_name(root)}"
    destination = f"{user}@{host}"
    prepare = tools.ssh.opts(cwd=root, nofail=True, recorded=False)(
        destination, f"rm -rf {target} && mkdir -p {target}"
    )
    if prepare.code != 0:
        fail(f"preparing {destination}:{target} exited {prepare.code}")
    # The one spawn here that stays shell: the site rides a pipe into
    # the far side's tar, and a handle builds one command line, not a
    # pipeline. Streaming the archive instead would need bytes on
    # stdin, where a handle's `input` is text.
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


def render_coverage_pages(root: Path) -> list[str]:
    """Hand each package's declared coverage report to its kind's renderer.

    A package that declares an ``htmlcov`` report and whose kind names
    a coverage page renderer is rendered by it; the packages sharing a
    renderer go to it together, since it reads the measured data once.
    Returns the names of the packages rendered.
    """
    from livery.workshop._kinds import kind_coverage_pages

    by_renderer: dict[
        Callable[[Path, tuple[Package, ...]], list[str]], list[Package]
    ] = {}
    for package in discover_packages(root):
        if not any(
            path == "htmlcov" for _label_, path in package_coverage_reports(package)
        ):
            continue
        renderer = kind_coverage_pages(package.kind)
        if renderer is None:
            print(
                f"  coverage: {package.member}: the {package.kind} kind"
                " renders no coverage pages; its page states the absence"
            )
            continue
        by_renderer.setdefault(renderer, []).append(package)
    rendered: list[str] = []
    for renderer, members in by_renderer.items():
        rendered += renderer(root, tuple(members))
    return rendered


docs_group = group("docs", help="The workspace's documentation site")


def _root() -> Path:
    from livery.workshop._extensions import workspace_root

    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    return root


def emit_section_navs(root: Path) -> list[str]:
    """Emit each package's complete section nav into its generated tree; the names.

    The section as the assembled config carries it: the authored tree
    with every block filled, paths as they publish. One file per
    package, ``docs/_generated/nav.toml``, gitignored, so the section
    is whole on disk beside its pages.
    """
    emitted: list[str] = []
    for package in discover_packages(root):
        section, _ = _package_section(package, indent="")
        if not section:
            continue
        generated = package.directory / "docs" / GENERATED_DIR
        generated.mkdir(parents=True, exist_ok=True)
        (generated / NAV_TOML).write_text(
            "# This section as the site assembles it, emitted by the build;"
            " the authored\n# nav.toml beside it is the source.\nnav = [\n"
            + "\n".join(section)
            + "\n]\n",
            encoding="utf-8",
        )
        emitted.append(package.member)
    return emitted


def write_site_config(root: Path) -> Path:
    """Assemble the config zensical reads and write it to the root; the path.

    Gitignored and rebuilt on every docs verb: the site's identity from
    the contract, the root pages, one section per package, the theme
    and the extension set. The header says so, since the file reads
    like a rendered one.
    """
    from livery.workshop._provenance import format_header

    header = format_header(
        (
            "Assembled by the docs build from workshop.toml and every package's",
            "section; gitignored and rewritten on every docs verb, never edited.",
        ),
        "#",
    )
    path = root / SITE_CONFIG
    path.write_text(header + zensical_config(root), encoding="utf-8")
    return path


def _generate_all(root: Path, *, full: bool = False) -> None:
    """Run the generation passes, saying what was made.

    Declared package generators run first, then the workshop's own
    pages and the section nav into each package's generated tree, so
    the mount copies everything a section holds; then the config the
    build reads.
    """
    generated = run_generators(root)
    if generated:
        print(f"  generators: {', '.join(generated)}")
    logged = generate_changelog_pages(root)
    if logged:
        print(f"  changelogs for {', '.join(logged)}")
    documented = generate_api_pages(root)
    if documented:
        print(f"  API pages for {', '.join(documented)}")
    covered = generate_coverage_pages(root)
    if covered:
        print(f"  coverage pages for {', '.join(covered)}")
    sections = emit_section_navs(root)
    if sections:
        print(f"  section navs for {', '.join(sections)}")
    mounted = mount_package_docs(root, full=full)
    if mounted:
        print(f"  mounted docs for {', '.join(mounted)}")
    else:
        print("  mounts current: no package's docs moved")
    staged = stage_extension_assets(root)
    if staged:
        print(f"  extension assets for {', '.join(staged)}")
    print(f"  assembled {write_site_config(root).name}")


@docs_group.task(name="build")
def docs_build(
    package: Annotated[
        str, doc("build a scoped preview of this package's section only")
    ] = "",
    full: Annotated[bool, doc("rebuild every package's mount whole")] = False,
) -> None:
    """Mount every package's docs, assemble the config, and build the site, strict.

    Strict is the point: a broken link or an orphan page fails here,
    on the machine, before CI says the same thing. ``--package``
    builds a scoped preview of one section instead, into the
    gitignored ``.docs-preview/`` directory; the preview is not
    strict, because chrome pages may link into sections it does not
    carry, and the workspace build owns strictness. A package whose
    docs did not move keeps its mount; ``--full`` rebuilds them all.
    """
    root = _root()
    if not package and not full:
        skip = unread_by_the_site(root)
        if skip:
            print(skip)
            return
    require_sources(root)
    _generate_all(root, full=full)
    if package:
        config = materialise_preview(root, named_package(root, package))
        result = tools.zensical.opts(cwd=config.parent, nofail=True).build(
            clean=True, config_file=str(config)
        )
        if result.code != 0:
            fail(
                f"zensical build exited {result.code}:\n{result.stdout}{result.stderr}"
            )
        print(f"  preview built at {config.parent / 'site'} (never published)")
        return
    developed = generate_development_pages(root)
    if developed:
        print(f"  development pages: {', '.join(developed)}")
    releases = generate_release_pages(root)
    if releases:
        print(f"  release view: {', '.join(releases)}")
    from livery.workshop._state import run_context

    print(source_summary(root))
    result = tools.zensical.opts(cwd=root, nofail=True).build(clean=True, strict=True)
    if result.code != 0:
        fail(f"zensical build exited {result.code}:\n{result.stdout}{result.stderr}")
    in_ci = run_context() is not None
    for line in generator_lines(result.stdout, result.stderr, in_ci=in_ci):
        print(line)
    from livery.extensions.docs._llms import write_llms_files

    written = write_llms_files(root)
    print(f"  agent files: {', '.join(written)}")
    require_site(root)
    print(f"  site built at {root / 'site'}")


def require_sources(root: Path) -> None:
    """Refuse before the build when the site's sources are not in the checkout.

    The generator has exited 0 in a fraction of its usual time with
    nothing on disk. The first thing to rule out is a checkout
    without the docs tree or the contract, so their absence is named
    here, before the generator runs, instead of surfacing as an
    empty site after it.
    """
    missing = [name for name in ("docs", "workshop.toml") if not (root / name).exists()]
    if missing:
        fail(
            f"no site sources at {root}: {', '.join(missing)} missing; the"
            " build reads the docs tree and assembles its config from the contract"
        )


def source_summary(root: Path) -> str:
    """One line on what the build reads: the pages under ``docs/``.

    Printed before every workspace build, so a run that produced no
    site says what its checkout held.
    """
    pages = sum(1 for _ in (root / "docs").rglob("*.md"))
    return f"  site sources: {pages} page(s) under docs/, the config assembled"


def generator_lines(stdout: str, stderr: str, *, in_ci: bool) -> list[str]:
    """The generator's own output, indented, for a run in CI; nothing otherwise.

    A local build stays quiet on success. In CI the lines are the
    evidence a build that left no site leaves behind, so they are
    printed whether or not the build succeeded.
    """
    if not in_ci:
        return []
    text = (stdout + stderr).strip()
    return [f"    {line}" for line in text.splitlines()] if text else []


def require_site(root: Path) -> None:
    """Refuse when the build left no site to publish.

    The site generator has exited 0 in a fraction of its usual time
    with nothing on disk, and the publish that followed was the one
    to fail; the build is the one that knows, so it says so.
    """
    import livery.footman.api as footman

    index = root / "site" / "index.html"
    if not index.is_file():
        fail(
            f"the site build exited 0 but left no {index.as_posix()}: nothing"
            " to publish;"
            f" run `{footman.prog()} docs.build` again"
        )


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


@docs_group.task(name="coverage-pages")
def docs_coverage_pages() -> None:
    """Render the coverage report pages the packages declare.

    The generator verb a package declares beside a coverage report
    its kind renders, such as a python package's ``htmlcov``. Each
    package whose kind names a renderer is handed to it. Idempotent:
    re-rendering from the same data rewrites the same tree.
    """
    root = _root()
    rendered = render_coverage_pages(root)
    if rendered:
        print(f"  coverage pages for {', '.join(rendered)}")
    else:
        print("  no measured data: declared reports will state the absence")


@docs_group.task(name="task-reference")
def docs_task_reference() -> None:
    """Render every providing package's task reference.

    The generator verb a task-providing package declares: an index
    per group and a page per public task into the owner's generated
    tree, the owner's ``tasks`` nav block rewritten, and the alias
    tree the runner's ``docs_url`` links through refreshed.
    Idempotent: re-rendering the same tree rewrites the same pages.
    """
    from livery.extensions.docs._taskref import generate_task_reference

    root = _root()
    rendered = generate_task_reference(root)
    if rendered:
        print(f"  task reference for {', '.join(rendered)}")
    else:
        print("  no workspace package provides tasks")


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
        tools.zensical.opts(cwd=config.parent).serve(config_file=str(config))
        return
    generate_release_pages(root)
    tools.zensical.opts(cwd=root).serve()


def unread_by_the_site(root: Path) -> str:
    """The line that skips the build when nothing the site reads changed, or empty.

    Only a pull request's docs job on a workspace declaring
    ``[ci] affected-legs`` skips, the same terms the check legs
    narrow on; the merge point's build and a person's own run always
    build. The diff against the base branch is read the way the check
    legs read it.
    """
    from livery.workshop._git_ops import GitError, GitOps
    from livery.workshop._packages import discover_packages
    from livery.workshop._quality import ci_affected_base
    from livery.workshop._state import run_context

    run = run_context()
    base = ci_affected_base(root, run) if run is not None else ""
    if not base:
        return ""
    git = GitOps(root)
    try:
        git.fetch()
        paths = git.changed_paths(base)
    except GitError as error:
        print(f"  docs: no diff against origin/{base}; building ({error})")
        return ""
    packages = discover_packages(root) if (root / "packages").is_dir() else ()
    read = [path for path in paths if site_reads(root, packages, path)]
    if read:
        return ""
    return (
        f"  docs: nothing the site reads changed against origin/{base}"
        f" ({len(paths)} path(s) changed); the build is skipped"
    )


def _register_builtin() -> None:
    """Declare the docs assembly's slots, until the docs extension declares them."""
    from livery.workshop._kinds import MEMBERS_POLICIES, PUBLIC_MEMBERS

    _slots.register_slot(
        MEMBERS_SLOT,
        compose=_slots.NEAREST,
        default=PUBLIC_MEMBERS,
        values=MEMBERS_POLICIES,
    )
    _slots.register_slot(THEME_SLOT, compose=_merge_theme, default=THEME_DEFAULT)


_register_builtin()


#: The development section: one page per prose section, from the
#: human-audience fragments the mounted extensions ship and the repository's
#: own, under the root ``docs/`` tree. Gitignored, rebuilt whole.
DEVELOPMENT = "docs/development"


def _shipped_prose(root: Path) -> list[Prose]:
    """Every fragment in play: the mounted extensions' shipped sets, then the own."""
    listed: list[Prose] = []
    for extension in _extensions.stack_names(root):
        content = _extensions.extension_content(extension)
        if content is not None:
            listed += shipped(extension, content)
    listed += repository_fragments(root)
    return listed


def _demoted(text: str) -> str:
    """*text* with every ATX heading one level deeper, under the page's own title."""
    return re.sub(r"^(#{1,5}) ", r"#\1 ", text, flags=re.M)


def section_title(section: str) -> str:
    """The page title of a prose section: its name, capitalised."""
    return section.replace("-", " ").capitalize()


def development_content(root: Path) -> dict[str, list[str]]:
    """Each section's markdown for a human reader, in section order, empty left out.

    The pages and the nav both read this, so the nav entry never waits
    on a page a previous build wrote.
    """
    by_section: dict[str, list[str]] = {}
    for prose in fragments(root, _shipped_prose(root), HUMAN):
        text = prose.text(root, HUMAN).strip()
        if text:
            by_section.setdefault(prose.section, []).append(text)
    return {
        section: by_section[section] for section in sections() if section in by_section
    }


def generate_development_pages(root: Path) -> list[str]:
    """Write the development section, one page per prose section; the pages written.

    A section with no fragment for a human reader gets no page; a
    page carries each fragment's markdown in the order
    [livery.workshop._prose.fragments][] gives, headings demoted under
    the page's title. The index lists the pages. Rebuilt whole.
    """
    base = root / DEVELOPMENT
    shutil.rmtree(base, ignore_errors=True)
    content = development_content(root)
    if not content:
        return []
    base.mkdir(parents=True)
    written: list[str] = []
    for section, texts in content.items():
        blocks = [f"# {section_title(section)}", ""]
        for text in texts:
            blocks += [_demoted(text), ""]
        page = base / f"{section}.md"
        page.write_text("\n".join(blocks).rstrip("\n") + "\n", encoding="utf-8")
        written.append(f"{section}.md")
    index = [
        "# Development",
        "",
        "How this workspace is developed, one page per section:",
        "",
    ]
    index += [f"- [{section_title(page[:-3])}]({page})" for page in written]
    (base / "index.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    return ["index.md", *written]


def development_nav_lines(root: Path) -> list[str]:
    """The nav entry for the development section, empty without a section to show."""
    content = development_content(root)
    if not content:
        return []
    lines = [
        '    { "Development" = [',
        '        { "Overview" = "development/index.md" },',
    ]
    lines += [
        f'        {{ "{section_title(section)}" = "development/{section}.md" }},'
        for section in content
    ]
    lines.append("    ] },")
    return lines
