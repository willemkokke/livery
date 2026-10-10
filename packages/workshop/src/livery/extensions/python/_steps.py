"""The python extension's steps in a package's lifecycle phases."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from livery.extensions.python import _backend

if TYPE_CHECKING:
    from collections.abc import Mapping

    from livery.workshop import PhaseContext


def stamp(ctx: PhaseContext) -> None:
    """Write the engine's version into ``pyproject.toml`` and every ``__version__``."""
    version = cast("str", ctx.read("version"))
    ctx.provide("changed", _backend.stamp_version(ctx.package).stamp(version))


def build(ctx: PhaseContext) -> None:
    """Build the package's wheel and sdist into its ``dist/``, at the engine's epoch."""
    epoch = cast("int", ctx.read("epoch"))
    ctx.provide("dist", _backend.build(ctx.package, ctx.root, epoch=epoch))


def publish(ctx: PhaseContext) -> None:
    """Upload the built distributions to the registry target the engine resolved."""
    from livery.workshop._registries import target_from_table

    version = cast("str", ctx.read("version"))
    target = target_from_table(
        cast("Mapping[str, object]", ctx.read("registry-target"))
    )
    published = _backend.publish_artifact(
        ctx.package, ctx.root, version=version, target=target
    )
    ctx.provide("published", published)
