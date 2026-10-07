"""The package skeleton holds together."""

from __future__ import annotations

import livery.forge


def test_imports_and_carries_a_version() -> None:
    from importlib.metadata import version

    # Against the installed metadata, never a literal: the release
    # train stamps the version, and a spelled copy here would need a
    # hand edit every release.
    assert livery.forge.__version__ == version("livery-forge")


def test_unsupported_is_an_exception() -> None:
    assert issubclass(livery.forge.Unsupported, Exception)


def test_namespace_is_pep420() -> None:
    # The namespace half must NOT be a regular package: an
    # __init__.py at livery/ would stop sibling distributions
    # (livery-fabric, livery-workshop) sharing the namespace.
    import livery

    assert not hasattr(livery, "__version__")
    assert getattr(livery, "__file__", None) is None
