"""pytest's two checks: each python package's suite, and its documentation examples.

The extension's ``extension.toml`` declares both and names the bodies
here. ``test.pytest`` narrows by packages: the workshop names the members a
run reaches ([livery.workshop.scoped_packages][]), and the python
kind's runner runs their suites in one call
([livery.workshop.run_suites][]), with the workspace's own tests
([livery.workshop.workspace_suite][]) beside them. A member whose
suite is not worker-safe sets the check's ``parallel`` option to false
and runs in a call of its own, under ``-n 0``. ``examples.pytest`` runs
each member's documentation examples with its kind's examples runner
([livery.workshop.kind_examples][]). Each check hands pytest the
words after ``--`` on its own verb (``fm test.pytest -- -k name``).
"""

from __future__ import annotations

from livery.workshop import (
    GateContext,
    check_option,
    kind_examples,
    run_suites,
    scoped_files,
    scoped_packages,
    workspace_suite,
)

#: The kind whose suites and examples pytest runs, its children with it.
KIND = "python"

#: The checks' names, as the workshop addresses them.
TEST = "test.pytest"
EXAMPLES = "examples.pytest"


def judge_test(ctx: GateContext) -> None:
    """Run the suites of the packages the run reaches, and the workspace's own tests."""
    # A package whose examples alone changed runs them, not its suite.
    members = tuple(
        package
        for package in scoped_packages(ctx, TEST)
        if package.path not in ctx.examples
    )
    # The workspace's own tests reach any package, so every run runs them.
    unit = workspace_suite(ctx.root)
    suites = (*members, unit) if unit is not None else members
    if not suites:
        print(f"  {TEST}: no python package and no workspace tests to run")
        return
    # A suite that is not worker-safe runs in a call of its own under
    # -n 0, after the words from the verb, so they cannot undo it. Each
    # call collects the packages it is handed and no other.
    serial = tuple(p for p in members if not check_option(TEST, p, "parallel"))
    parallel = tuple(p for p in suites if p not in serial)
    for group, extra in ((parallel, ()), (serial, ("-n", "0"))):
        if group:
            run_suites(
                KIND,
                *ctx.arguments,
                *extra,
                packages=group,
                root=ctx.root,
                selection=ctx.tests,
                point=ctx.point,
            )


def judge_examples(ctx: GateContext) -> None:
    """Run the documentation examples of each package the run reaches."""
    for package in scoped_packages(ctx, EXAMPLES):
        if (
            ctx.scoped
            and package.path in ctx.tests
            and package.path not in ctx.examples
        ):
            continue  # its tests alone changed: the examples did not move
        runner = kind_examples(package.kind)
        if runner is None:
            print(f"  examples: {package.path} skips ({package.kind} kind runs none)")
            continue
        # Only a run over named files narrows the examples; a whole walk
        # runs the package's directory.
        named = (
            tuple(str(path) for path in scoped_files(ctx, EXAMPLES, package))
            if ctx.files
            else ()
        )
        runner(package, ctx.root, named, ctx.arguments)
