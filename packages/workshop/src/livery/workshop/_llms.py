"""The agent-facing site files: ``llms.txt`` and ``llms-full.txt``.

Ported from footman's generator. ``llms.txt``
(https://llmstxt.org) is the index: every page of the merged nav in
order, each with a one-line description pulled from its first prose
paragraph and an absolute link. ``llms-full.txt`` is the authored
text in one file, snippet includes resolved inline. The
machine-appended sections (package changelogs, the release view, the
API trees) are excluded from the full file by construction: a
context file that concatenates history teaches superseded behaviour
as current, so only what an author put in a nav belongs in a
model's context. The index still links every page, the changelogs
included.

Both files are written into the built site's root after the site
build; they are derived, never hand-edited, and never committed.
"""

from __future__ import annotations

import posixpath
import re
import tomllib
from pathlib import Path

from livery.workshop._docs import _project_name, docs_table, zensical_config

#: Site-tree prefixes whose pages never enter ``llms-full.txt``:
#: machine territory whose content is history or reference the
#: authored pages already link.
MACHINE_PREFIXES = ("_generated/releases/", "_generated/api/")


def _machine_page(path: str) -> bool:
    """Whether *path* is machine-appended rather than authored."""
    if path.startswith(MACHINE_PREFIXES):
        return True
    return bool(re.fullmatch(r"_generated/packages/[^/]+/changelog\.md", path))


def _nav_pages(nav: list[object]) -> list[tuple[str, str]]:
    """(title, page path) per nav entry, in nav order, depth-first."""
    pages: list[tuple[str, str]] = []
    for entry in nav:
        if not isinstance(entry, dict):
            continue
        for title, value in entry.items():
            if isinstance(value, list):
                pages += _nav_pages(value)
            elif isinstance(value, str):
                pages.append((str(title), value))
    return pages


def page_url(site: str, page: str) -> str:
    """The published URL for *page*: a directory index lives at the directory."""
    if page == "index.md" or page.endswith("/index.md"):
        stem = page.removesuffix("index.md").rstrip("/")
        return f"{site}{stem + '/' if stem else ''}"
    return f"{site}{page.removesuffix('.md')}/"


def _absolute_links(prose: str, site: str, page: str) -> str:
    """Rewrite relative ``.md`` links to published URLs.

    Resolved against the page's own directory, because a relative
    target means the sibling, and a link an agent resolves against
    the site root 404s.
    """

    def rewrite(match: re.Match[str]) -> str:
        target = posixpath.normpath(
            posixpath.join(posixpath.dirname(page), match.group(1))
        )
        return f"]({page_url(site, target)})"

    return re.sub(r"\]\((?!https?://)([^)#]+\.md)\)", rewrite, prose)


def first_sentence(text: str, site: str, page: str) -> str:
    """The first sentence of the page's first prose paragraph.

    Prose, strictly: headings, list items, table rows, admonition
    bodies, and fenced code are structure, not description; served as
    a page's one-line summary they read as junk in every agent index
    built on the file.
    """
    if text.startswith("---\n"):
        _, _, text = text.partition("\n---\n")
    skip = ("#", "---", "[![", "!!!", ">", "<", "--8<--", "|", "- ", "* ")
    fenced = False
    admonition = False
    hanging = False
    paragraph: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if admonition:
            if not line or raw.startswith((" ", "\t")):
                continue
            admonition = False
        if line.startswith("!!!"):
            admonition = True
            continue
        if line.startswith("```"):
            fenced = not fenced
            continue
        digits = line.split(".", 1)[0]
        numbered = digits.isdigit() and line.startswith(f"{digits}.")
        if fenced or line.startswith(skip) or numbered:
            if paragraph:
                break
            # A wrapped list item or table row continues on the next
            # line without its marker; serving that tail as prose put
            # mid-sentence fragments in the index.
            hanging = True
            continue
        if not line:
            if paragraph:
                break
            hanging = False
            continue
        if hanging:
            continue
        paragraph.append(line)
    prose = _absolute_links(" ".join(paragraph), site, page)
    end = prose.find(". ")
    return prose[: end + 1] if end != -1 else prose


def resolve_snippets(text: str, root: Path) -> str:
    """Inline ``--8<-- "path"`` includes, the way the site build does.

    Paths are repo-relative, matching the snippets configuration; a
    missing file keeps the directive, so the gap is visible rather
    than silent.
    """

    def inline(match: re.Match[str]) -> str:
        path = root / match.group(1)
        if not path.is_file():
            return match.group(0)
        return resolve_snippets(path.read_text(encoding="utf-8").rstrip(), root)

    return re.sub(r"""^--8<-- ["']([^"']+)["']$""", inline, text, flags=re.MULTILINE)


def llms_files(root: Path) -> tuple[str, str]:
    """The two files' bodies: (index, full).

    Derived from the same emitted config the site builds from, so
    the index can never disagree with the nav. Without a declared
    site URL the links are root-relative, which still resolves on
    whatever host serves the site.
    """
    table = docs_table(root)
    title = str(table.get("title", "")) or _project_name(root)
    description = str(table.get("description", ""))
    site = str(table.get("site_url", "")) or "/"
    if not site.endswith("/"):
        site += "/"
    nav = tomllib.loads(zensical_config(root))["project"]["nav"]
    pages = _nav_pages(nav)
    index = [f"# {title}", ""]
    if description:
        index += [f"> {description}", ""]
    index += ["## Docs", ""]
    full = [f"# {title} - full documentation", ""]
    for page_title, page in pages:
        source = root / "docs" / page
        if not source.is_file():
            continue
        resolved = resolve_snippets(source.read_text(encoding="utf-8").rstrip(), root)
        url = page_url(site, page)
        summary = first_sentence(resolved, site, page)
        index.append(
            f"- [{page_title}]({url}): {summary}"
            if summary
            else f"- [{page_title}]({url})"
        )
        if _machine_page(page):
            continue
        full += ["", "---", "", f"<!-- {page_title} - {url} -->", "", resolved]
    return ("\n".join(index) + "\n", "\n".join(full) + "\n")


def write_llms_files(root: Path) -> list[str]:
    """Write both files into the built site's root; the names written.

    The site must already be built: these land beside its pages the
    way any root asset does, and a clean rebuild regenerates them.
    """
    site_dir = root / "site"
    site_dir.mkdir(exist_ok=True)
    index, full = llms_files(root)
    (site_dir / "llms.txt").write_text(index, encoding="utf-8")
    (site_dir / "llms-full.txt").write_text(full, encoding="utf-8")
    return ["llms.txt", "llms-full.txt"]
