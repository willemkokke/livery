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
    """Nothing: other packages reach C or C++ code through headers, not imports.

    A consumer names the package by its recipe or its CMake target,
    which the package's own build files declare.
    """
    del package
    return ()


def public_modules(package: Package) -> tuple[str, ...]:
    """Nothing: a C or C++ package has no importable API to verify."""
    del package
    return ()


def referenced_siblings(package: Package, around: Neighbours) -> dict[str, str]:
    """Nothing: the layering check reads no C or C++ source for references.

    A header of another member, included with no requirement on that
    member, goes unseen: the empty answer is no verdict.
    """
    del package, around
    return {}


#: The extensions of a C or C++ test source under ``tests/``.
TEST_SOURCES = (".cpp", ".cc", ".cxx", ".c")


#: The extensions of a C or C++ source or header: what clang-format
#: walks, and what a native check's claims admit.
SOURCE_SUFFIXES = (*TEST_SOURCES, ".hpp", ".h", ".hxx")
