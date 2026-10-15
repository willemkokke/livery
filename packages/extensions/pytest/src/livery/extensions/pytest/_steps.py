"""pytest's two steps: each python package's suite, and its documentation examples.

The extension's ``extension.toml`` declares both and names the bodies
here. Each step asks the python extension, through
[livery.workshop.answer][], for the function that runs what it judges:
the answer to ``python.test.runner`` runs several packages' suites in
one call, and the answer to ``python.examples.runner`` one package's
examples. The python extension answers both while a package holds it.
With no answer the test step runs nothing and the examples step skips
each package, and each says why.

``test.pytest`` narrows by packages: the workshop names the members a
run reaches ([livery.workshop.scoped_packages][]), and their suites run
in one call, with the workspace's own tests beside them while the
root's set holds python ([livery.workshop.scoped_suite][]), measured
while this extension contributes its ``[for.coverage]`` table
([livery.workshop.contributions_for][]). A member whose suite is not
worker-safe sets the step's ``parallel`` option to false and runs in a
call of its own, under ``-n 0``. ``examples.pytest`` runs each member's
documentation examples. Each step hands pytest the words after ``--``
on its own verb (``fm test.pytest -- -k name``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from livery.workshop import (
    PhaseContext,
    Query,
    answer,
    contributions_for,
    scoped_files,
    scoped_packages,
    scoped_suite,
    step_option,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from livery.workshop import Package

#: The name a workspace lists this extension by.
NAME = "pytest"

#: The extension this one measures the suites for, while its
#: ``[for.coverage]`` table applies.
MEASURED_FOR = "coverage"

#: The steps' names, as the workshop addresses them.
TEST = "test.pytest"
EXAMPLES = "examples.pytest"

#: How the suites of several packages run in one call: the function the
#: python extension answers with, called with the words for pytest, then
#: the packages, the workspace root, the test files that stand for each
#: package's suite, the CI point and whether the run is measured. None
#: while nothing answers.
SUITE_RUNNER: Query[Callable[..., None] | None] = Query("python.test.runner")

#: How one package's documentation examples run: the function the python
#: extension answers with, called with the package, the workspace root,
#: the example files the run names (empty for all of them) and the words
#: for pytest. None while nothing answers.
EXAMPLES_RUNNER: Query[
    Callable[[Package, Path, tuple[str, ...], tuple[str, ...]], None] | None
] = Query("python.examples.runner")


def judge_test(ctx: PhaseContext) -> None:
    """Run the suites of the packages the run reaches, and the workspace's own tests.

    The workspace's own tests run while the root's set holds python,
    and a line says why when it does not. The suites run measured
    while coverage is listed and this extension's listing contributes
    its ``[for.coverage]`` table, and with no meter otherwise. While
    nothing answers ``python.test.runner``, nothing runs, and the step
    says why.
    """
    # A package whose examples alone changed runs them, not its suite.
    members = tuple(
        package
        for package in scoped_packages(ctx, TEST)
        if package.path not in ctx.examples
    )
    # The workspace's own tests reach any package, so every run that
    # judges them runs them.
    unit = scoped_suite(ctx, TEST)
    suites = (*members, unit) if unit is not None else members
    if not suites:
        print(f"  {TEST}: no python package and no workspace tests to run")
        return
    run = answer(ctx.root, SUITE_RUNNER)
    if run is None:
        print(
            f"  {TEST}: runs nothing; nothing answers {SUITE_RUNNER.name}, which"
            " the python extension answers while the root's set or a package's"
            " holds python"
        )
        return
    # A suite that is not worker-safe runs in a call of its own under
    # -n 0, after the words from the verb, so they cannot undo it. Each
    # call collects the packages it is handed and no other.
    serial = tuple(p for p in members if not step_option(TEST, p, "parallel"))
    parallel = tuple(p for p in suites if p not in serial)
    measured = NAME in contributions_for(MEASURED_FOR, ctx.root)
    for group, extra in ((parallel, ()), (serial, ("-n", "0"))):
        if group:
            run(
                *ctx.arguments,
                *extra,
                packages=group,
                root=ctx.root,
                selection=ctx.tests,
                point=ctx.point,
                measured=measured,
            )


def judge_examples(ctx: PhaseContext) -> None:
    """Run the documentation examples of each package the run reaches.

    While nothing answers ``python.examples.runner``, each package
    skips, named.
    """
    packages = tuple(
        package
        for package in scoped_packages(ctx, EXAMPLES)
        # Its tests alone changed: the examples did not move.
        if not (
            ctx.scoped
            and package.path in ctx.tests
            and package.path not in ctx.examples
        )
    )
    if not packages:
        return
    run = answer(ctx.root, EXAMPLES_RUNNER)
    for package in packages:
        if run is None:
            print(
                f"  examples: {package.path} skips (nothing answers"
                f" {EXAMPLES_RUNNER.name})"
            )
            continue
        # Only a run over named files narrows the examples; a whole walk
        # runs the package's directory.
        named = (
            tuple(str(path) for path in scoped_files(ctx, EXAMPLES, package))
            if ctx.files
            else ()
        )
        run(package, ctx.root, named, ctx.arguments)
