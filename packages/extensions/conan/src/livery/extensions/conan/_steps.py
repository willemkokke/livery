"""The conan extension's steps in a package's lifecycle phases."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from livery.extensions.conan import _package

if TYPE_CHECKING:
    from collections.abc import Mapping

    from livery.workshop import PhaseContext


def profile(ctx: PhaseContext) -> None:
    """Make conan's default profile name a compiler before a build runs through it.

    The cmake-conan provider writes a default profile itself when there
    is none, with no compiler in it, so a profile detected first is the
    one the provider finds. Prints what was done, nothing when the
    profile already names a compiler.
    """
    for line in _package.ensure_profile(ctx.root):
        print(line)


def stamp(ctx: PhaseContext) -> None:
    """Write the engine's version into the recipe and raise its floors there.

    The version goes into the recipe's ``version`` attribute; each
    requirement on one of the engine's floors, a member released with
    this one, takes that member's version.
    """
    version = cast("str", ctx.read("version"))
    floors = cast("Mapping[str, str]", ctx.read("floors"))
    changed = _package.stamp_version(ctx.member).stamp(version)
    changed += _package.raise_floors(ctx.member, floors)
    ctx.provide("changed", list(dict.fromkeys(changed)))


def build(ctx: PhaseContext) -> None:
    """Build the package into the conan cache; its ``build`` directory.

    Conan writes its own metadata, so the step reads no epoch.
    """
    ctx.provide("dist", _package.build(ctx.member, ctx.root))


def publish(ctx: PhaseContext) -> None:
    """Upload the package to the registry target the engine resolved.

    A package a job of the run built, which the engine gives as
    ``prebuilt``, is restored into this job's conan cache from every
    host's file the run collected before the upload reads it, so the
    upload sends every host's binary.
    """
    from livery.workshop import RegistryTarget

    version = cast("str", ctx.read("version"))
    target = RegistryTarget.from_table(
        cast("Mapping[str, object]", ctx.read("registry-target"))
    )
    published = _package.publish_artifact(
        ctx.member,
        ctx.root,
        version=version,
        target=target,
        prebuilt=cast("bool", ctx.read("prebuilt")),
    )
    ctx.provide("published", published)
