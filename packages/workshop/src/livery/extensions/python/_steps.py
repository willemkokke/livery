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


def prove(ctx: PhaseContext) -> None:
    """Run one isolated release leg at the engine's resolution; the versions it got."""
    from pathlib import Path

    resolution = cast("str", ctx.read("resolution"))
    dirs = tuple(Path(path) for path in cast("list[Path]", ctx.read("release-dirs")))
    resolved = _backend.prove(
        ctx.package, ctx.root, release_dirs=dirs, resolution=resolution
    )
    ctx.provide("resolved", dict(resolved))


def replay(ctx: PhaseContext) -> None:
    """Install the released version alone and run its tests at the engine's tree."""
    from pathlib import Path

    code = _backend.replay(
        ctx.package,
        tree=Path(cast("Path", ctx.read("tree"))),
        version=cast("str", ctx.read("version")),
        python=cast("str", ctx.read("interpreter")),
        index=cast("str", ctx.read("index")),
        extras=cast("str", ctx.read("extras")),
    )
    ctx.provide("exit-code", code)
