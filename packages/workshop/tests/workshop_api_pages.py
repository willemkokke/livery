"""Write a reference generator's pages and its nav block by hand.

The docs extension places a generator's pages and its nav block as
data, whichever extension wrote them. A test of that placement writes
them here, in the shape the mkdocstrings extension's step writes them,
without importing that extension: it ships in a distribution of its
own, which the workshop's suite does not depend on.
"""

from __future__ import annotations

from pathlib import Path


def write_api_pages(root: Path) -> list[str]:
    """Write an API page and the ``api`` nav block of each member with a python root.

    A member under *root*'s ``packages/`` whose ``src/acme/<member>/``
    holds an ``__init__.py`` gets ``docs/_generated/api/index.md``, one
    directive for ``acme.<member>``, and ``docs/_generated/nav.api.toml``,
    the block that lists that page.

    Returns:
        The members written, by directory name, sorted.
    """
    written: list[str] = []
    for member in sorted((root / "packages").iterdir()):
        if not (member / "src" / "acme" / member.name / "__init__.py").is_file():
            continue
        dotted = f"acme.{member.name}"
        generated = member / "docs" / "_generated"
        (generated / "api").mkdir(parents=True, exist_ok=True)
        (generated / "api" / "index.md").write_text(
            f"# `{dotted}`\n\n::: {dotted}\n", encoding="utf-8"
        )
        (generated / "nav.api.toml").write_text(
            "nav = [\n"
            '{ "API" = [\n'
            f'    {{ "{dotted}" = "_generated/api/index.md" }},\n'
            "] },\n"
            "]\n",
            encoding="utf-8",
        )
        written.append(member.name)
    return written
