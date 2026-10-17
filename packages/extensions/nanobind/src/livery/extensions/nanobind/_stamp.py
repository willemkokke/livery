"""Stamp a nanobind package's release: its version, and its floors in both manifests.

The stamp phase's step for a package whose set holds nanobind. The
package carries two manifests: python's ``pyproject.toml``, which
declares the version, and conan's recipe, which names the requirements
its extension module builds against and no version. One step of a
package reports the files the phase changed, so nanobind's declaration
deletes python's and conan's stamp steps in the same set, and this
step does the work of both through their entry modules.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Mapping

    from livery.workshop import PhaseContext


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
