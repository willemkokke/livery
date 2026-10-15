"""Start a package's executable after a development build of what it needs.

``fm run <package>[:<executable>] -- <arguments>`` names a package and
one of the executables its extensions answer under ``executables``
([livery.workshop.EXECUTABLES][]). It first builds the package's
dependency closure, dependencies first, each member through the steps
its set adds to the ``build`` phase's tree side, in the ``debug``
configuration, and skips each member whose files and dependencies are
unchanged since its last good build in this checkout; the record of
those builds lives under ``.workshop/.cache/run/``. Then it runs the
``run`` phase through the one walk
([livery.workshop._phases.run_phase][]): every step the
package's set adds to it, except that of the extensions answering
``executables`` only the owner's steps run. The engine gives the phase
the executable under ``executable``, which a step reads as it reads
any engine key, and the words after ``--`` are the context's
``arguments``. The verb exits with the code the step that started the
executable recorded.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from livery import git
from livery.footman import Arg, cwd, doc, fail, passthrough, prog, suggest, task

if TYPE_CHECKING:
    from livery.workshop._packages import Package
    from livery.workshop._steps import StepRecord

#: The record of the development builds, under the workspace root.
RECORD = Path(".workshop") / ".cache" / "run" / "built.json"

#: What the development build gives the build phase: the tree side, in
#: the debug configuration.
DEVELOP_INPUTS: dict[str, dict[str, object]] = {
    "build": {"isolated": False, "configuration": "debug"}
}


def _targets() -> list[str]:
    """Each ``package:executable`` the workspace's packages answer, for completion.

    A package with one executable is offered by its name alone too. A
    package whose extensions answer in a way the query refuses is left
    out: completion offers what runs.
    """
    from livery.workshop._extensions import workspace_root
    from livery.workshop._packages import discover_packages
    from livery.workshop._queries import EXECUTABLES, QueryError, answer

    root = workspace_root()
    if root is None:
        return []
    found: list[str] = []
    for package in discover_packages(root):
        try:
            names = answer(package, EXECUTABLES)
        except QueryError:
            continue
        if len(names) == 1:
            found.append(package.member)
        found += [f"{package.member}:{name}" for name in names]
    return found


@task(cwd="asinvoked")
def run(
    target: Annotated[
        Arg[str],
        doc(
            "package[:executable]; the package may be left out inside its directory,"
            " and the executable when the package has one"
        ),
        suggest(_targets, strict=False),
    ] = "",
) -> int:
    """Build what a package's executable needs, start it, and exit with its code.

    The build phase's tree side runs over the package's dependency
    closure first, skipping what is unchanged since its last good
    build; then the run phase starts the executable with the words
    after ``--``.
    """
    from livery.workshop._extensions import workspace_root

    here = cwd()
    root = workspace_root(here)
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    return start(root, target, tuple(passthrough()), here)


def start(root: Path, target: str, arguments: tuple[str, ...], cwd: Path) -> int:
    """Run *target*'s executable in the workspace at *root*; its exit code.

    *cwd* decides the package a target that names none means. The
    development build runs first ([livery.workshop._run.develop][]).
    """
    from livery.workshop._composition import closure
    from livery.workshop._extensions import installed_declaration
    from livery.workshop._packages import discover_packages
    from livery.workshop._phases import PhaseContext, PhaseError, phase_steps, run_phase
    from livery.workshop._queries import EXECUTABLES, QueryError, answer, answers

    packages = discover_packages(root)
    package, executable = _resolve(root, packages, target, cwd)
    try:
        names = answer(package, EXECUTABLES)
    except QueryError as error:
        fail(str(error))
    if not names:
        fail(f"{package.path} has no executable: no extension of its set answers one")
    if not executable:
        if len(names) != 1:
            fail(
                f"{package.path} has the executables {', '.join(names)}; name one:"
                f" `{prog()} run {package.member}:{names[0]}`"
            )
        (executable,) = names
    answering = answers(package, EXECUTABLES)
    owners = {name for name, held in answering.items() if executable in held}
    if not owners:
        fail(
            f"{package.path} has no executable named {executable}; its executables"
            f" are {', '.join(names)}"
        )
    develop(root, package, packages)
    # The run steps of an extension answering executables start them,
    # so of those the owner's run alone; every other extension's run.
    members = closure(package.extensions, installed_declaration)
    steps = tuple(
        record
        for record in phase_steps("run", members, installed_declaration)
        if record.extension not in answering or record.extension in owners
    )
    ctx = PhaseContext(
        root=root,
        packages=(package,),
        package=package,
        phase="run",
        arguments=arguments,
    )
    try:
        ctx = run_phase(ctx, steps, inputs={"run": {"executable": executable}})
    except PhaseError as error:
        fail(str(error))
    return ctx.exit_code


def _resolve(
    root: Path, packages: tuple[Package, ...], target: str, cwd: Path
) -> tuple[Package, str]:
    """The package and the executable *target* names; an empty name for none.

    ``package:executable`` names both. A bare word is a package when one
    is named so, by its directory under ``packages/``, its path or its
    distribution's name; else, inside a package's directory, an
    executable of that package. Inside it, nothing at all names the
    package.
    """
    named, colon, executable = target.partition(":")
    package = _named(packages, named) if named else None
    if package is not None:
        return package, executable
    here = None if named and colon else _holding(root, packages, cwd)
    if here is not None:
        # Inside a package, a bare word names one of its executables.
        return here, executable if colon else named
    if named:
        fail(f"no package is named {named}: {_known(packages)}")
    fail(
        f"name a package, `{prog()} run <package>[:<executable>]`, or run it"
        f" inside a package's directory: {_known(packages)}"
    )


def _named(packages: tuple[Package, ...], name: str) -> Package | None:
    """The package named *name* by its member, its path or its distribution; None."""
    for package in packages:
        if name in (package.member, package.path, package.name):
            return package
    return None


def _holding(root: Path, packages: tuple[Package, ...], cwd: Path) -> Package | None:
    """The package whose directory holds *cwd*; None outside every package."""
    here = cwd.resolve()
    for package in packages:
        directory = (root / package.path).resolve()
        if here == directory or directory in here.parents:
            return package
    return None


def _known(packages: tuple[Package, ...]) -> str:
    """The packages a target may name, for a refusal."""
    if not packages:
        return "the workspace has no packages"
    return "the packages are " + ", ".join(package.member for package in packages)


def develop(root: Path, package: Package, packages: tuple[Package, ...]) -> list[str]:
    """Build *package*'s dependency closure in the build phase; the paths built.

    Dependencies come first, each through the steps its set adds to the
    build phase that serve the tree side, in the ``debug``
    configuration ([livery.workshop._run.DEVELOP_INPUTS][]), run
    through the one walk as the release train runs a package's phase.
    A member is built when its files, or the fingerprint of a
    dependency, differ from its last good build in this checkout, so a
    dependency that changed rebuilds what depends on it; a member whose
    set adds no such step is never built. With nothing changed, no
    build step runs. A checkout git cannot read is built whole, and
    says so.
    """
    from livery.workshop._graph import dependencies_closure, order_topologically

    closure = order_topologically(dependencies_closure(packages, {package.path}))
    try:
        tree = git.working_tree_id(root)
    except git.GitError as error:
        print(
            f"  run: git cannot read this checkout, so every member builds: {error}",
            file=sys.stderr,
        )
        tree = ""
    ids = _subtrees(root, tree, closure)
    record = _read(root)
    prints: dict[str, str] = {}
    built: list[str] = []
    for member in closure:
        own = ids.get(member.path, "")
        needs = sorted(
            prints[edge.path] for edge in member.depends if edge.path in prints
        )
        fingerprint = hashlib.sha256("\n".join((own, *needs)).encode()).hexdigest()
        prints[member.path] = fingerprint
        steps = _build_steps(member)
        if not steps or (own and record.get(member.path) == fingerprint):
            continue
        _build(root, member, steps)
        built.append(member.path)
        if own:
            record[member.path] = fingerprint
            _write(root, record)
    return built


def _build_steps(member: Package) -> tuple[StepRecord, ...]:
    """The steps *member*'s set adds to the build phase that serve the tree side."""
    from livery.workshop._composition import extension_set
    from livery.workshop._extensions import installed_declaration
    from livery.workshop._phases import phase_steps, serves

    side = DEVELOP_INPUTS["build"]
    return tuple(
        record
        for record in phase_steps("build", extension_set(member), installed_declaration)
        if serves(record, side)
    )


def _build(root: Path, member: Package, steps: tuple[StepRecord, ...]) -> None:
    """Run *steps* over *member*, the development build's inputs given the phase.

    Raises:
        Failed: when the steps cannot run as declared, with the walk's
            reason.
    """
    from livery.workshop._phases import PhaseContext, PhaseError, run_phase

    ctx = PhaseContext(root=root, packages=(member,), package=member, phase="build")
    try:
        run_phase(ctx, steps, inputs=DEVELOP_INPUTS)
    except PhaseError as error:
        fail(str(error))


def _subtrees(root: Path, tree: str, members: tuple[Package, ...]) -> dict[str, str]:
    """Each of *members*' paths that *tree* holds, to its id, read in one process.

    Empty without a tree, so every member builds.
    """
    if not tree:
        return {}
    return git.object_ids(root, tree, [member.path for member in members])


def _read(root: Path) -> dict[str, str]:
    """The record of good builds: each member's path to its fingerprint."""
    path = root / RECORD
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return (
        {str(key): str(value) for key, value in data.items()}
        if isinstance(data, dict)
        else {}
    )


def _write(root: Path, record: dict[str, str]) -> None:
    """Keep *record* for the next run in this checkout."""
    path = root / RECORD
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", "utf-8")
