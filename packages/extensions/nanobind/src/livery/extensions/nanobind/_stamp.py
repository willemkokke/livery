"""Stamp a nanobind package's release: its version, and its floors in both manifests.

The stamp phase's step for a package whose set holds nanobind. The
package carries two manifests: python's ``pyproject.toml``, which
declares the version, and conan's recipe, which names the requirements
its extension module builds against and no version. One step of a
package reports the files the phase changed, so nanobind's declaration
deletes python's and conan's stamp steps in the same set, and this
step does the work of both through their entry modules. The recipe is
the one file it writes beyond python's, so nanobind names it among the
files the stamp phase writes (`version_files`): the release keeps it
and restores it, and the local act refuses while it has changes of its
own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from livery.workshop import Package, PhaseContext

#: The recipe the package's extension module builds against, which
#: names no version.
RECIPE_FILE = "conanfile.py"


def version_files(package: Package) -> tuple[Path, ...]:
    """The recipe, the file the stamp writes beyond python's; empty with none.

    nanobind's answer to the ``version-files`` query. python answers
    with ``pyproject.toml`` and the modules, and conan answers no file
    for a recipe that names no version, so without this answer the
    recipe the stamp raises floors in would be among no file the
    release keeps.
    """
    recipe = package.directory / RECIPE_FILE
    return (recipe,) if recipe.is_file() else ()


def stamp(ctx: PhaseContext) -> None:
    """Write the engine's version and raise its floors, in the pyproject and the recipe.

    python writes the version into ``pyproject.toml`` and every
    ``__version__`` and raises the pyproject's floors
    ([livery.extensions.python.stamp][]); conan raises the recipe's
    ([livery.extensions.conan.raise_floors][]). Each floor is one of the
    engine's, a member released with this one.
    """
    # Two distributions ship these entry modules under the one namespace,
    # and basedpyright resolves a from-import that names both in one of
    # them alone, so each is imported by its own path.
    import livery.extensions.conan as conan
    import livery.extensions.python as python

    version = cast("str", ctx.read("version"))
    floors = cast("Mapping[str, str]", ctx.read("floors"))
    changed = python.stamp(ctx.member, version, floors)
    changed += conan.raise_floors(ctx.member, floors)
    ctx.provide("changed", changed)
