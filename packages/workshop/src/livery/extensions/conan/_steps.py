"""The conan extension's steps in a package's lifecycle phases."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from livery.extensions.conan import _package

if TYPE_CHECKING:
    from collections.abc import Mapping

    from livery.workshop import PhaseContext


def stamp(ctx: PhaseContext) -> None:
    """Write the engine's version into the recipe's ``version`` attribute."""
    version = cast("str", ctx.read("version"))
    ctx.provide("changed", _package.stamp_version(ctx.package).stamp(version))


def build(ctx: PhaseContext) -> None:
    """Build the package into the conan cache; its ``build`` directory.

    Conan writes its own metadata, so the step reads no epoch.
    """
    ctx.provide("dist", _package.build(ctx.package, ctx.root))


def publish(ctx: PhaseContext) -> None:
    """Upload the package to the registry target the engine resolved."""
    from livery.workshop._registries import target_from_table

    version = cast("str", ctx.read("version"))
    target = target_from_table(
        cast("Mapping[str, object]", ctx.read("registry-target"))
    )
    published = _package.publish_artifact(
        ctx.package, ctx.root, version=version, target=target
    )
    ctx.provide("published", published)
