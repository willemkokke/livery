"""The docs extension's verbs, loaded through the extension's entry point.

Importing this module registers the ``docs`` group under the extension's
identity, declares the site's override template as a file the CI render
writes, and registers the ``pre_tasks`` hook that links task names to
their pages on the site. The extension's checks, slots and CI jobs are
declared in its ``extension.toml``. The base's plugin never imports it.
"""

from __future__ import annotations

import tomllib

from livery.extensions.docs._site import docs_group, overrides_template
from livery.extensions.docs._taskref import task_links
from livery.footman import Invocation, pre_tasks
from livery.workshop import workspace_root
from livery.workshop._site_files import register_site_file

EXTENSION = "livery.extensions.docs"

register_site_file("overrides/main.html", overrides_template, extension=EXTENSION)


@pre_tasks
def link_task_pages(inv: Invocation) -> None:
    """Link task names to their pages on the site, when the contract names it.

    The root contract's ``[docs] site-url`` gives the address, and each
    task links to its redirect page there. A configured ``docs-url`` and
    the contract's ``[workspace] docs-url`` come first, whichever hook
    runs first. The contract is read raw: the hook runs on every
    command, ``fm sync`` among them, so an address a link template
    cannot start with is named on stderr and links nothing, and the
    command goes on.
    """
    import sys

    root = workspace_root()
    if inv.docs_url is not None or root is None:
        return
    try:
        contract = tomllib.loads((root / "workshop.toml").read_text("utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return
    workspace = contract.get("workspace")
    docs = contract.get("docs")
    if isinstance(workspace, dict) and "docs-url" in workspace:
        return
    site = docs.get("site-url") if isinstance(docs, dict) else None
    if not isinstance(site, str) or not site:
        return
    try:
        inv.docs_url = task_links(site)
    except ValueError as error:
        print(
            f"  note: workshop.toml [docs] site-url: {site!r} cannot start a task"
            f" link ({error}); no task links",
            file=sys.stderr,
        )


__all__ = ["docs_group"]
