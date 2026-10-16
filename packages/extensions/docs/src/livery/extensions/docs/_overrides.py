"""The site's theme override at the root, and the extensions' site assets it reads.

The extension's ``[fragments."overrides/main.html"]`` render is
`overrides_template`, which ``fm sync`` writes at the root while docs is
listed. A module a declaration names registers nothing when it is
imported, so this one holds no verb: the site's verbs live in the
module that assembles the site, which reads the assets from here.
"""

from __future__ import annotations

from pathlib import Path

from livery.extensions.docs._contract import docs_table, project_name
from livery.workshop import shipped_content

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


def extension_assets(root: Path) -> list[tuple[str, Path]]:
    """Each mounted extension that ships site assets, with its assets directory.

    In the workspace's extension order. An extension ships site assets under
    ``content/docs/assets/`` in its wheel; an extension without that
    directory, or not installed, ships none and is not listed.
    """
    found: list[tuple[str, Path]] = []
    for extension, content in shipped_content(root):
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


def overrides_template(root: Path) -> str:
    """The site's theme override: link-preview tags, instance facts.

    The extension's ``[fragments]`` render for ``overrides/main.html``,
    so ``fm sync`` writes the file at the root while docs is listed,
    and the drift check keeps it matching. It opens with the
    provenance header every generated file carries, in Jinja's comment
    form.

    The brand leads on the home page, because that is the URL that
    gets posted and a feed truncates from the right; elsewhere the
    page leads. The image tags name the workspace's own card, else the
    card an extension ships, else are left out. Every URL is absolute:
    a preview is fetched by someone else's server, which has no page to
    resolve a relative path against.
    """
    # Imported here: the module that serves the header registers the
    # base's provenance verbs, which the docs plugin's tasks must not carry.
    from livery.workshop import generated_header

    header = "".join(
        "  " + line.removeprefix("# ") + "\n"
        for line in generated_header("#").splitlines()
    )
    table = docs_table(root)
    title = str(table.get("title", "")) or project_name(root)
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
        "{#\n"
        + header
        + "#}\n"
        + (
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
    )
