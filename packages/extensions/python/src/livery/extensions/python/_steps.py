"""The python extension's steps: a package's lifecycle phases and its isolated test."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from livery.extensions.python import _backend

if TYPE_CHECKING:
    from collections.abc import Mapping

    from livery.workshop import PhaseContext


def stamp(ctx: PhaseContext) -> None:
    """Write the engine's version into ``pyproject.toml`` and raise its floors there.

    The version goes into ``pyproject.toml`` and every ``__version__``;
    each requirement on one of the engine's floors, a member released
    with this one, takes that member's version.
    """
    version = cast("str", ctx.read("version"))
    floors = cast("Mapping[str, str]", ctx.read("floors"))
    ctx.provide("changed", _backend.stamp(ctx.member, version, floors))


def build(ctx: PhaseContext) -> None:
    """Build the package's wheel and sdist into its ``dist/``, at the engine's epoch."""
    epoch = cast("int", ctx.read("epoch"))
    ctx.provide("dist", _backend.build(ctx.member, ctx.root, epoch=epoch))


def publish(ctx: PhaseContext) -> None:
    """Upload the built distributions to the registry target the engine resolved."""
    from livery.workshop import RegistryTarget

    version = cast("str", ctx.read("version"))
    target = RegistryTarget.from_table(
        cast("Mapping[str, object]", ctx.read("registry-target"))
    )
    published = _backend.publish_artifact(
        ctx.member, ctx.root, version=version, target=target
    )
    ctx.provide("published", published)


def isolated_test(ctx: PhaseContext) -> None:
    """Test the built wheel in a fresh venv at the engine's resolution; what it got."""
    from pathlib import Path

    resolution = cast("str", ctx.read("resolution"))
    dirs = tuple(Path(path) for path in cast("list[Path]", ctx.read("release-dirs")))
    resolved = _backend.isolated_test(
        ctx.member, ctx.root, release_dirs=dirs, resolution=resolution
    )
    ctx.provide("resolved", dict(resolved))


def run(ctx: PhaseContext) -> None:
    """Start the script the engine names with the words after the dashes; its code."""
    name = cast("str", ctx.read("executable"))
    ctx.exit(_backend.run_script(ctx.member, ctx.root, name, ctx.arguments))


def verify(ctx: PhaseContext) -> None:
    """Install the released version alone and run its tests at the engine's tree."""
    from pathlib import Path

    code = _backend.verify(
        ctx.member,
        tree=Path(cast("Path", ctx.read("tree"))),
        version=cast("str", ctx.read("version")),
        python=cast("str", ctx.read("interpreter")),
        index=cast("str", ctx.read("index")),
        extras=cast("str", ctx.read("extras")),
    )
    ctx.provide("exit-code", code)
