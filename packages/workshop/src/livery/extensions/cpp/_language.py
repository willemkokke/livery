"""What the C and C++ languages tell the workshop about a package's files.

The suffixes of a C or C++ source, a header and a test source, and the
answers a C or C++ package gives where a python package names modules:
none, since other packages reach its code through headers and
libraries, never an import path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from livery.workshop._packages import Neighbours, Package


def module_roots(package: Package) -> tuple[str, ...]:
    """Nothing: a conan package is referenced by recipe name, not import.

    The name a consumer writes is the recipe's, which the conanfile
    already declares and ``declared_requirements`` already reads.
    """
    del package
    return ()


def public_modules(package: Package) -> tuple[str, ...]:
    """Nothing: a conan package has no importable API to verify."""
    del package
    return ()


def referenced_siblings(package: Package, around: Neighbours) -> dict[str, str]:
    """Nothing: reading a recipe's own sources for references is unwritten.

    The answer this kind owes is an ``#include`` of a header belonging
    to another workspace package with no matching ``requires`` in the
    conanfile. Until it is written the lint finds nothing here, which
    is silence rather than a pass.
    """
    del package, around
    return {}


#: The extensions of a C or C++ test source under ``tests/``.
TEST_SOURCES = (".cpp", ".cc", ".cxx", ".c")


#: The extensions of a C or C++ source or header: what clang-format
#: walks, and what a native check's claims admit.
SOURCE_SUFFIXES = (*TEST_SOURCES, ".hpp", ".h", ".hxx")
