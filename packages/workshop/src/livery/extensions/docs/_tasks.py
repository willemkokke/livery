"""The docs extension's verbs, loaded through the extension's entry point.

Importing this module registers the ``docs`` group under the extension's
identity and declares the site's override template as a file the CI
render writes. The extension's checks, slots and CI jobs are declared in
its ``extension.toml``. The base's plugin never imports it.
"""

from __future__ import annotations

from livery.extensions.docs._site import docs_group, overrides_template
from livery.workshop._site_files import register_site_file

EXTENSION = "livery.extensions.docs"

register_site_file("overrides/main.html", overrides_template, extension=EXTENSION)

__all__ = ["docs_group"]
