"""The declared-imports rule: a package's source imports what its manifest declares.

At import time, a module under a package's ``src`` imports only the
standard library, the workspace's own members, and the distributions
the package's ``pyproject.toml`` declares in ``[project.dependencies]``.
Inside a function or a method it may also import a distribution an
optional extra declares, since importing the module does not run that
import. An import under ``TYPE_CHECKING``, or in a ``try`` that handles
``ImportError``, is not judged: no run makes the first, and the source
runs without the second.

A module a program loads as a plugin, named in one of the program's
entry-point groups (``footman.tasks`` and ``footman.builtin`` for the
runner, ``pytest11`` for pytest), may also import what that program
brings: its distribution and the distributions it requires. The
program is present whenever such a module runs. A name another member
owns is left to the sibling-references step, which judges the edges
between members.

Which distribution provides a module is read from the metadata of the
distributions the workspace's environment installed, so a module that
no installed distribution provides refuses, naming it.

The rule is a step of the layering phase,
``[phases.layering.declared-imports]``, over each python package's
``references`` answer: the sources a narrowed run reaches, every one
otherwise.
"""

from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from livery.footman import fail, prog
from livery.workshop import selected_files

if TYPE_CHECKING:
    from collections.abc import Mapping

    from livery.workshop import PhaseContext
    from livery.workshop._packages import (
        Neighbours,
        Package,
        ReferencedName,
        RuleContext,
        SourceReferences,
    )

#: Where a package declares the distributions its sources import at
#: import time.
DEPENDENCIES = "[project.dependencies]"


def declared_imports(ctx: PhaseContext) -> None:
    """The step: refuse each import that no declaration of its package accounts for.

    The sources the run reaches ([livery.workshop.selected_files][])
    are judged; every source when the run knows nothing of what changed.
    """
    from livery.workshop._packages import RuleContext

    context = RuleContext(
        root=ctx.root, packages=ctx.packages, files=selected_files(ctx)
    )
    found = problems(context)
    if found:
        fail("the sources break the declared-imports rule:\n  " + "\n  ".join(found))


@dataclass(frozen=True)
class Manifest:
    """What a package's ``pyproject.toml`` declares, as the rule reads it.

    Attributes:
        path: The file, relative to the workspace root, posix.
        dependencies: The distributions ``[project.dependencies]``
            names, normalized.
        extras: Each optional extra, to the distributions it names,
            normalized.
        plugins: Each module the package names in a plugin entry-point
            group, to the distributions of the programs that load it.
    """

    path: str
    dependencies: frozenset[str]
    extras: Mapping[str, frozenset[str]]
    plugins: Mapping[str, frozenset[str]]

    def loaded_by(self, module: str) -> frozenset[str]:
        """The programs that load *module* as a plugin, or a package above it."""
        found: set[str] = set()
        for plugin, programs in self.plugins.items():
            if module == plugin or module.startswith(plugin + "."):
                found |= programs
        return frozenset(found)

    def extra_naming(self, distributions: frozenset[str]) -> str:
        """The first optional extra naming one of *distributions*; empty for none."""
        return next(
            (extra for extra, names in self.extras.items() if names & distributions),
            "",
        )


def manifest(package: Package) -> Manifest:
    """What *package*'s ``pyproject.toml`` declares that the rule reads."""
    from livery.extensions.python._backend import plugin_hosts
    from livery.extensions.python._installed import requirement_name

    pyproject = package.directory / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text("utf-8")) if pyproject.is_file() else {}
    project = cast("dict[str, Any]", data.get("project", {}))
    dependencies = cast("list[str]", project.get("dependencies", []))
    extras = cast("dict[str, list[str]]", project.get("optional-dependencies", {}))
    return Manifest(
        path=f"{package.path}/pyproject.toml",
        dependencies=frozenset(map(requirement_name, dependencies)),
        extras={
            extra: frozenset(map(requirement_name, requirements))
            for extra, requirements in extras.items()
        },
        plugins=plugin_hosts(package),
    )


def problems(context: RuleContext) -> list[str]:
    """Each import no declaration accounts for, in package and source order.

    A package that holds the python extension is judged, each of its
    sources under ``src`` that the run reaches; a package whose kind is
    not registered is left out, since ``layering.graph`` names it.
    """
    from livery.workshop import _lifecycle
    from livery.workshop._kinds import extension_set, kind_names
    from livery.workshop._packages import neighbours

    around = neighbours(context.packages)
    known = kind_names()
    found: list[str] = []
    for package in context.packages_in_scope():
        if package.kind not in known or "python" not in extension_set(package):
            continue
        declared = manifest(package)
        shipped = f"{package.path}/src/"
        for source in _lifecycle.references(package):
            if not source.path.startswith(shipped):
                continue
            if context.files is not None and source.path not in context.files:
                continue
            found += judged(source, declared, around)
    return found


def judged(
    source: SourceReferences, declared: Manifest, around: Neighbours
) -> list[str]:
    """What *source* imports that *declared* does not account for, one line each.

    Import-time references are judged before lazy ones, and each
    distribution is named once per source, at the first reference that
    needs it. A namespace several distributions share is judged through
    the names under it that the source references, since those decide
    which distribution the source needs.
    """
    from livery.extensions.python._installed import installed

    judgeable = [
        reference
        for reference in source.references
        if not reference.type_checking and not reference.optional
    ]
    ordered = [
        *(reference for reference in judgeable if not reference.lazy),
        *(reference for reference in judgeable if reference.lazy),
    ]
    names = {reference.name for reference in judgeable}
    programs = declared.loaded_by(source.module)
    reported: set[object] = set()
    found: list[str] = []
    for reference in ordered:
        name = reference.name
        if _standard(name) or _member(name, around):
            continue
        environment = installed()
        providers = environment.providers(name)
        if not providers:
            top = name.partition(".")[0]
            if top not in reported:
                reported.add(top)
                found.append(
                    f"{source.path} imports {name}, and no distribution installed"
                    " in the workspace's environment provides it: declare the"
                    f" distribution that does in {declared.path}, then"
                    f" `{prog()} sync` installs it; one an optional extra declares"
                    " installs once the package's dev-extras lists that extra"
                )
            continue
        if len(providers) > 1 and any(other.startswith(name + ".") for other in names):
            continue
        allowed = set(declared.dependencies)
        for program in programs:
            allowed |= environment.brings(program)
        if reference.lazy:
            for extra in declared.extras.values():
                allowed |= extra
        if providers & allowed or providers in reported:
            continue
        reported.add(providers)
        found.append(_refusal(source, reference, providers, declared))
    return found


def _refusal(
    source: SourceReferences,
    reference: ReferencedName,
    providers: frozenset[str],
    declared: Manifest,
) -> str:
    """The line that names *reference*'s module, its distribution and the manifest."""
    named = " or ".join(sorted(providers))
    if reference.lazy:
        return (
            f"{source.path} imports {reference.name} inside a function, and"
            f" {named} provides it, which {declared.path} declares neither in"
            f" {DEPENDENCIES} nor in an optional extra: declare it in one of them"
        )
    extra = declared.extra_naming(providers)
    if extra:
        return (
            f"{source.path} imports {reference.name} at import time, and {named}"
            f" provides it, which {declared.path} declares in its optional extra"
            f" {extra!r} alone: import it inside the function that uses it, or"
            f" declare it in {DEPENDENCIES}"
        )
    return (
        f"{source.path} imports {reference.name} at import time, and {named}"
        f" provides it, which {declared.path} does not declare in {DEPENDENCIES}:"
        " declare it there"
    )


def _standard(name: str) -> bool:
    """Whether *name* is in the standard library of the running interpreter."""
    return name.partition(".")[0] in sys.stdlib_module_names


def _member(name: str, around: Neighbours) -> bool:
    """Whether a workspace member owns *name*, or *name* is a namespace above one."""
    return bool(around.owner_of(name)) or any(
        root.startswith(name + ".") for root in around.owners
    )
