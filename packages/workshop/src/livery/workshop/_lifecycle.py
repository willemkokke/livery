"""What the engine asks of a package: build, versions, publish, requirements.

The release train, the layering check and the docs reach a package's
artifacts and manifests through these functions alone: its build, the
version its manifest declares, the files a version is written into,
writing a version, publishing what it built, its declared requirements,
writing one, the names other packages reference its code by, and the
siblings its sources use. Each answers from the package's kind backend
([livery.workshop._kinds.backend_for][]), so a caller never names a
backend. A function a backend may leave out answers None or an empty
tuple when the backend has none, and so does an abstract kind, which
has no backend.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from livery.workshop._packages import Neighbours, Package
    from livery.workshop._registries import RegistryTarget


def build(package: Package, root: Path, *, epoch: int = 0) -> Path:
    """Build *package*'s artifacts into its ``dist/``; that directory.

    *epoch* is the build's source date, so two builds of one tree are
    the same bytes; 0 takes the build tool's own.
    """
    from livery.workshop._kinds import backend_for

    return backend_for(package).build(package, root, epoch=epoch)


def current_version(package: Package) -> str:
    """The version *package*'s own manifest declares."""
    from livery.workshop._kinds import backend_for

    return backend_for(package).current_version(package)


def version_files(package: Package) -> tuple[Path, ...]:
    """Every file a version may be written into.

    A caller that writes a version for one build keeps these files
    first, then restores them.
    """
    from livery.workshop._kinds import backend_for

    return tuple(backend_for(package).stamp_version(package).homes())


def stamp(package: Package, version: str) -> list[str]:
    """Write *version* into *package*'s version files; the files it changed."""
    from livery.workshop._kinds import backend_for

    return backend_for(package).stamp_version(package).stamp(version)


def publish(
    package: Package, root: Path, *, version: str, target: RegistryTarget
) -> bool:
    """Upload *package*'s built artifacts to *target*; False when all were there.

    A re-run walks past what an earlier attempt uploaded, so False is
    a success that uploaded nothing.
    """
    from livery.workshop._kinds import backend_for

    return backend_for(package).publish_artifact(
        package, root, version=version, target=target
    )


def declared_requirements(package: Package) -> dict[str, str] | None:
    """What *package*'s native manifest requires, each name to its constraint.

    None for a package whose kind reads no manifest, which the layering
    check names, since it cannot compare the package's edges.
    """
    reader = _optional(package, "declared_requirements")
    return None if reader is None else reader(package)


def declare_requirement(package: Package, dependency: Package, floor: str) -> list[str]:
    """Write *dependency* at *floor* into *package*'s manifest; the files changed.

    Empty when the manifest names the dependency already.
    """
    from livery.workshop._kinds import backend_for

    return backend_for(package).declare_requirement(package, dependency, floor)


def module_roots(package: Package) -> tuple[str, ...]:
    """The names other packages reference *package*'s code by; empty for none."""
    roots = _optional(package, "module_roots")
    return () if roots is None else tuple(roots(package))


def public_modules(package: Package) -> tuple[str, ...]:
    """The modules that declare *package*'s public API, by import path.

    What a type-completeness check verifies. A package whose kind has
    no importable API answers nothing.
    """
    from livery.workshop._kinds import backend_for

    return backend_for(package).public_modules(package)


def compile_commands(package: Package) -> Path | None:
    """Where *package*'s gate build writes its compilation database.

    None for a package whose build writes none. The file exists once
    the build is configured.
    """
    from livery.workshop._kinds import backend_for

    return backend_for(package).compile_commands(package)


def plugin_modules(package: Package) -> tuple[str, ...]:
    """The modules *package* declares as footman task entry points; empty for none."""
    reader = _optional(package, "plugin_modules")
    return () if reader is None else tuple(reader(package))


def referenced_siblings(package: Package, around: Neighbours) -> dict[str, str] | None:
    """The siblings *package*'s sources use and nothing accounts for, by area.

    The area is ``src`` or ``tests``, which tells a runtime edge from
    a test one. None for a package whose kind reads no references.
    """
    reader = _optional(package, "referenced_siblings")
    return None if reader is None else reader(package, around)


def _optional(package: Package, name: str) -> Any:
    """The backend function *name* of *package*'s kind; None when it has none."""
    from livery.workshop._kinds import kind_for

    return getattr(kind_for(package.kind).backend, name, None)
