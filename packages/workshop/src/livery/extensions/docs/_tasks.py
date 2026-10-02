"""The docs layer's task surface, loaded through the layer's entry point.

Importing this module registers the ``docs`` group under the layer's
identity, declares the site's override template as a file the CI
render writes, and contributes the site's two jobs to the builtin
points: the gate point's strict build, which its verdict waits for,
and the merge point's deploy. The base's plugin never imports it.
"""

from __future__ import annotations

from livery.extensions.docs._site import docs_group, overrides_template
from livery.workshop._points import Entry, Job, contribute_job
from livery.workshop._site_files import register_site_file

LAYER = "livery.extensions.docs"

register_site_file("overrides/main.html", overrides_template, layer=LAYER)

contribute_job(
    "gate",
    Job(
        "docs",
        docs_tools=True,
        note=(
            "The strict site build: broken links and orphan pages go"
            " red here, required through the gate context, never"
            " inside the local check."
        ),
    ),
    entries=(Entry("gate", "docs", "docs.build", source=LAYER),),
    gates=True,
    layer=LAYER,
)
contribute_job(
    "merge",
    Job(
        "deploy",
        needs=("gate",),
        fetch="tags",
        token="job",
        docs_tools=True,
        deploy=True,
        note=(
            "The site's deploy through the contract's seam, on the"
            " push alone. The release view reads the receipt tags; a"
            " shallow tagless clone renders its no-tags fallback page"
            " instead."
        ),
    ),
    entries=(
        Entry("merge", "deploy", "docs.build", source=LAYER),
        Entry("merge", "deploy", "docs.publish", source=LAYER),
    ),
    layer=LAYER,
)

__all__ = ["docs_group"]
