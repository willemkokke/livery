"""The docs extension's names another extension reads to add to the site.

An extension that writes tables into the site's config answers the
``docs.config`` query with a [livery.extensions.docs.Writer][]: the
function that writes its tables, as TOML, for the config a
[livery.extensions.docs.Scope][] describes. The ``docs.members`` query,
[livery.extensions.docs.MEMBERS][], answers whether the API reference
documents private members: [livery.extensions.docs.PUBLIC_MEMBERS][]
or [livery.extensions.docs.ALL_MEMBERS][]. The root ``docs/`` tree's
generated directories, [livery.extensions.docs.SITE_TREES][], hold no
authored page. The task reference renders while the root contract turns
its step, [livery.extensions.docs.TASK_REFERENCE][], on: each task of a
provider a package advertises
([livery.extensions.docs.advertised_providers][]) publishes on the
page [livery.extensions.docs.task_page][] names.

A generator writes its nav block as data, a ``nav.<name>.toml`` file
in the package's generated tree, and imports nothing from here for it.
"""

from __future__ import annotations

# The literal-False spelling the checkers honour without importing
# `typing`: the names below are what a checker sees, and at runtime
# `__getattr__` serves each from its module when it is read, so importing
# this module loads none of the extension's modules.
TYPE_CHECKING = False
if TYPE_CHECKING:
    from livery.extensions.docs._config import ALL_MEMBERS as ALL_MEMBERS
    from livery.extensions.docs._config import MEMBERS as MEMBERS
    from livery.extensions.docs._config import PUBLIC_MEMBERS as PUBLIC_MEMBERS
    from livery.extensions.docs._config import Scope as Scope
    from livery.extensions.docs._config import Writer as Writer
    from livery.extensions.docs._site import SITE_TREES as SITE_TREES
    from livery.extensions.docs._taskref import TASK_REFERENCE as TASK_REFERENCE
    from livery.extensions.docs._taskref import (
        advertised_providers as advertised_providers,
    )
    from livery.extensions.docs._taskref import task_page as task_page

__version__ = "0.0.0"

__all__ = [
    "ALL_MEMBERS",
    "MEMBERS",
    "PUBLIC_MEMBERS",
    "SITE_TREES",
    "TASK_REFERENCE",
    "Scope",
    "Writer",
    "__version__",
    "advertised_providers",
    "task_page",
]

# The module each lazily served name lives in.
_EXPORTS: dict[str, str] = {
    "ALL_MEMBERS": "livery.extensions.docs._config",
    "MEMBERS": "livery.extensions.docs._config",
    "PUBLIC_MEMBERS": "livery.extensions.docs._config",
    "SITE_TREES": "livery.extensions.docs._site",
    "Scope": "livery.extensions.docs._config",
    "TASK_REFERENCE": "livery.extensions.docs._taskref",
    "Writer": "livery.extensions.docs._config",
    "advertised_providers": "livery.extensions.docs._taskref",
    "task_page": "livery.extensions.docs._taskref",
}


def __getattr__(name: str) -> object:
    """Serve a public name from its module, each time it is read.

    The value is never kept here, so a test that patches the name in
    its module is read through.
    """
    import importlib

    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(
            f"module 'livery.extensions.docs' has no attribute {name!r}"
        )
    return getattr(importlib.import_module(module), name)
