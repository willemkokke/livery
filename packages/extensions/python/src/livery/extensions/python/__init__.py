"""The python extension's names another extension reads to stamp, build and test wheels.

The python extension builds, versions, publishes and tests a package
whose set holds python. An extension that builds such a package's
wheels with a tool of its own, as nanobind builds them with
cibuildwheel, reads three of its functions.
[livery.extensions.python.stamp][] writes a release's version into the
package's ``pyproject.toml`` and every ``__version__``, and raises
there the floors on the members released with it.
[livery.extensions.python.module_docs][] copies the package's ``docs/``
into its source tree as the wheel's embedded ``_docs`` for one build,
and removes the copy after it. [livery.extensions.python.run_isolated_test][]
installs a built wheel into a fresh venv and runs the package's tests
against the installed copy.
"""

from __future__ import annotations

# The literal-False spelling the checkers honour without importing
# `typing`: the names below are what a checker sees, and at runtime
# `__getattr__` serves each from its module when it is read, so importing
# this module loads none of the extension's modules.
TYPE_CHECKING = False
if TYPE_CHECKING:
    from livery.extensions.python._backend import module_docs as module_docs
    from livery.extensions.python._backend import (
        run_isolated_test as run_isolated_test,
    )
    from livery.extensions.python._backend import stamp as stamp

__version__ = "0.0.0"

__all__ = ["__version__", "module_docs", "run_isolated_test", "stamp"]

# The module each lazily served name lives in.
_EXPORTS: dict[str, str] = {
    "module_docs": "livery.extensions.python._backend",
    "run_isolated_test": "livery.extensions.python._backend",
    "stamp": "livery.extensions.python._backend",
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
            f"module 'livery.extensions.python' has no attribute {name!r}"
        )
    return getattr(importlib.import_module(module), name)
