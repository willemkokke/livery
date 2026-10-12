"""The package imports, carries its version, and pins its surface."""

from __future__ import annotations

import livery.git as package
from livery.git import _environment, _marks, _ops, _ssh, _store


def test_imports_and_carries_a_version() -> None:
    from importlib.metadata import version

    # Against the installed metadata, never a literal: the release
    # train stamps the version, and a spelled copy here would need a
    # hand edit every release.
    assert package.__version__ == version("livery-git")


def test_the_public_surface_is_every_function_of_the_root() -> None:
    functions = {
        name
        for module in (_ops, _ssh, _environment, _marks, _store)
        for name, value in vars(module).items()
        if callable(value)
        and not name.startswith("_")
        and getattr(value, "__module__", "") == module.__name__
    }
    constants = {
        "__version__",
        "FETCHED",
        "WHOLE",
        "NAMESPACE",
        "LOCAL_NAMESPACE",
        "TRACE_NAMESPACE",
        "FETCHED_NAMESPACE",
        "RUN_PREFIX",
    }
    assert set(package.__all__) == functions | constants
    assert "GitError" in functions
