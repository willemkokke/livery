"""The docs layer's task surface, loaded through the layer's entry point.

Importing this module registers the ``docs`` group under the layer's
identity and declares the site's override template as a file the CI
render writes. The base's plugin never imports it.
"""

from __future__ import annotations

from livery.workshop._site_files import register_site_file
from livery.workshop.layers.docs._site import docs_group, overrides_template

LAYER = "livery.workshop.layers.docs"

register_site_file("overrides/main.html", overrides_template, layer=LAYER)

__all__ = ["docs_group"]
