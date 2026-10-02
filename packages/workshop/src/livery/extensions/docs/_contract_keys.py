"""The contract keys the docs layer reads, declared for the contract's judge.

Named under the ``workshop.contract`` entry point group, so the judge
([livery.workshop._contract_keys][]) loads this data alone and never
the layer's tasks.
"""

from __future__ import annotations

from livery.workshop._contract_keys import Declared

#: The site's title, its description, and a package's pages beyond
#: the generated ones.
DECLARED: tuple[Declared, ...] = (
    Declared("root", "docs.title", ("str",)),
    Declared("root", "docs.description", ("str",)),
    Declared("package", "docs.coverage", ("list",)),
    Declared("package", "docs.coverage[]", ("table",)),
    Declared("package", "docs.coverage[].label", ("str",)),
    Declared("package", "docs.coverage[].path", ("str",)),
    Declared("package", "docs.extra-css", ("strs",)),
    Declared("package", "docs.extra-javascript", ("list",)),
    Declared("package", "docs.extra-javascript[]", ("str", "table")),
    Declared("package", "docs.extra-javascript[].path", ("str",)),
    Declared("package", "docs.extra-javascript[].type", ("str",)),
    Declared("package", "docs.extra-javascript[].defer", ("bool",)),
    Declared("package", "docs.extra-javascript[].async", ("bool",)),
)
