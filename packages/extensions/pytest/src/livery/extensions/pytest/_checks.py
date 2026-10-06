"""pytest's two checks: each python package's suite, and its documentation examples.

``test.pytest`` narrows by packages: the workshop names the members a
run reaches ([livery.workshop.api.scoped_packages][]), and the python
kind's runner runs their suites in one call
([livery.workshop.api.run_suites][]), with the workspace's own tests
([livery.workshop.api.workspace_suite][]) beside them. A member whose
suite is not worker-safe sets the check's ``parallel`` option to false
and runs in a call of its own, under ``-n 0``. ``examples.pytest`` runs
each member's documentation examples with its kind's examples runner
([livery.workshop.api.kind_examples][]). Each check hands pytest the
words after ``--`` on its own verb (``fm test.pytest -- -k name``).
"""

from __future__ import annotations

from livery.workshop.api import (
    PACKAGES,
    CheckRecord,
    Claim,
    GateContext,
    Option,
    check_option,
    kind_examples,
    run_suites,
    scoped_files,
    scoped_packages,
    workspace_suite,
)

#: The kind whose suites and examples pytest runs, its children with it.
KIND = "python"

#: The suffixes of the python files the checks claim.
SUFFIXES = (".py", ".pyi")

#: The checks' names, as the workshop addresses them.
TEST = "test.pytest"
EXAMPLES = "examples.pytest"


def _test_run(ctx: GateContext) -> None:
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


def _examples_run(ctx: GateContext) -> None:
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


CHECKS = (
    CheckRecord(
        "pytest",
        "test",
        _test_run,
        flags=("point",),
        narrowing=PACKAGES,
        kinds=(KIND,),
        arguments=True,
        # pytest is the record's tool in the store, for the typed handle
        # the runner calls, and its venv copy below is the one that
        # imports the project's environment.
        tools=("pytest",),
        # A package's tests measure its source, so a source change is one
        # the test check reads, and runs the suite for.
        claims=(
            Claim("test"),
            Claim("test-support"),
            Claim("source", suffixes=SUFFIXES),
        ),
        options=(
            Option(
                "parallel",
                "bool",
                True,
                "run the package's suite across cores; false runs it under -n 0",
            ),
        ),
        # pytest and coverage import the project's environment, so they
        # live in the venv: pytest 9 for the pytest.toml it reads,
        # coverage 7.13 for the .pth it installs, which starts the meter
        # in every python the tests start once the runner arms it and
        # imports nothing otherwise, and xdist for -n auto.
        contributions=(
            ("python.dev-group", "pytest>=9.0"),
            ("python.dev-group", "pytest-cov>=5"),
            ("python.dev-group", "coverage>=7.13"),
            ("python.dev-group", "pytest-xdist>=3.6"),
            ("python.test.addopts", "-q"),
            ("python.test.addopts", "-n auto"),
            ("python.test.addopts", "--dist=worksteal"),
            ("python.test.addopts", "--import-mode=importlib"),
        ),
    ),
    CheckRecord(
        "pytest",
        "examples",
        _examples_run,
        narrowing=PACKAGES,
        kinds=(KIND,),
        tools=("pytest",),
        arguments=True,
        claims=(Claim("example", suffixes=SUFFIXES),),
    ),
)
