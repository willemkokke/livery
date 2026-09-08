"""The quality verbs: the gate and its parts, dispatched by contract.

Each verb discovers the packages by their ``workshop.toml``, refuses
any type without a backend, and hands the work to the type's backend
module. ``check`` is the whole local gate; CI runs the same command.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

if TYPE_CHECKING:
    from pathlib import Path

import livery.footman as footman
from livery.footman import Forward, doc, fail, group, parallel, task
from livery.workshop._backends import _python, require_backends
from livery.workshop._kinds import gated
from livery.workshop._layers import workspace_root
from livery.workshop._packages import Package, discover_packages
from livery.workshop._templates import template_check


def _packages() -> tuple[Package, ...]:
    """The workspace's packages, backends verified before anything runs."""
    root = workspace_root()
    if root is None:
        raise ValueError("no workspace: no workshop.toml above the working directory")
    packages = discover_packages(root)
    require_backends(packages)
    return packages


def run_kind_checks(packages: tuple[Package, ...], root: Path) -> None:
    """Run each kind's own per-package gate, in package order.

    Only kinds whose contract names ``kind_verbs`` take part; the
    announcement carries the package and the verbs, so the gate
    output says what ran beside what skipped.
    """
    from livery.workshop._kinds import CiContract, kind_for

    default_verbs = CiContract().check_verbs
    for package in packages:
        record = kind_for(package.type)
        if not record.ci.kind_verbs:
            continue
        skipped = [v for v in default_verbs if v not in record.ci.check_verbs]
        print(
            f"  {package.path} ({record.name}):"
            f" {', '.join(record.ci.kind_verbs)} run;"
            f" {', '.join(skipped)} skip"
        )
        record.backend.check(package, root)


def _refuse_both(fix: bool, safe_fix: bool) -> None:
    if fix and safe_fix:
        fail(
            "--fix and --safe-fix are two answers to one question: --fix"
            " applies every safe fix, --safe-fix withholds the"
            " code-removing ones. Pass one."
        )


@task
def lint(
    *paths: str,
    fix: Annotated[bool, doc("apply safe fixes in place")] = False,
    safe_fix: Annotated[
        bool, doc("apply fixes safe for in-progress edits (keeps imports)")
    ] = False,
) -> None:
    """Lint every package with its type's linter.

    With *paths*, lints exactly those files (foreign filetypes pass
    through); without, every package. ``--safe-fix`` is the
    edit-in-flight fix, documented as safe to apply mid-edit.
    """
    _refuse_both(fix, safe_fix)
    if not paths:
        _packages()
    _python.run_lint(fix=fix, safe_fix=safe_fix, paths=paths or _python.SRC)


@task
def format(
    *paths: str,
    fix: Annotated[bool, doc("rewrite instead of reporting")] = False,
    safe_fix: Annotated[
        bool, doc("rewrite; safe to apply to in-progress edits")
    ] = False,
) -> None:
    """Check every package's formatting; ``--fix`` rewrites.

    With *paths*, formats exactly those files (foreign filetypes
    pass through); without, every package.
    """
    _refuse_both(fix, safe_fix)
    if not paths:
        _packages()
    _python.run_format(check=not fix, safe_fix=safe_fix, paths=paths or _python.SRC)


@task
def typecheck() -> None:
    """Type-check every package with its type's gating checkers."""
    _packages()
    _python.run_typecheck()


@task
def typecomplete() -> None:
    """Verify every package's public API is 100% type-complete."""
    _python.run_typecomplete(gated(_packages(), "typecomplete"))


@task
def test(*pytest_args: str) -> None:
    """Run the test suite, coverage floors enforced.

    Args:
        pytest_args: forwarded to pytest verbatim
    """
    packages = gated(_packages(), "test")
    root = workspace_root()
    _python.run_test(*pytest_args, packages=packages, root=root)


@task
def kindcheck() -> None:
    """Run each non-python kind's own gate over its packages.

    Quiet in a pure-python workspace: no kind declares its own
    verbs, so nothing runs and nothing prints.
    """
    packages = _packages()
    root = workspace_root()
    assert root is not None
    run_kind_checks(packages, root)


def _affected() -> tuple[Package, ...] | None:
    """The affected subset for the gate; None means everything."""
    from livery.workshop._git_ops import GitOps
    from livery.workshop._graph import affected_packages

    root = workspace_root()
    if root is None:
        raise ValueError("no workspace: no workshop.toml above the working directory")
    git = GitOps(root)
    git.fetch()
    return affected_packages(root, git)


@task
def check(
    affected: Annotated[
        bool, doc("scope the gate to the branch's affected packages")
    ] = False,
    fix: Forward[bool] = False,
) -> None:
    """Run the gate: format, lint, types, tests, render gate, in parallel.

    ``--fix`` runs format and lint in their fix modes: each prints
    what it found, rewrites what is mechanical, and still fails on
    what is not. The two rewrite the same files, so under ``--fix``
    they run one after the other before the rest of the gate.

    ``--affected`` narrows every verb to the packages this branch's
    changes can influence (their dependents' closure). A change
    outside the packages configures every gate, so the narrowing
    falls back to everything; ty and pyrefly always check their
    configured whole either way.

    ``--fix`` refuses inside CI: a runner's checkout is judged,
    never rewritten, because a fix there mutates a copy nobody
    keeps and hides the finding from the verdict. Run the fix
    locally and push the result.
    """
    import os

    if fix and os.environ.get("CI"):
        fail(
            "check --fix inside CI: the runner's checkout is judged,"
            " never rewritten. A fix here would mutate a copy nobody"
            " keeps and hide the finding from the verdict. Run"
            f" `{footman.prog()} check --fix` locally and push the result."
        )
    subset = _affected() if affected else None
    if affected and subset is not None:
        packages = _packages()
        if not subset:
            print("  nothing affected: the branch changes no files")
            return
        if len(subset) < len(packages):
            names = ", ".join(package.path for package in subset)
            print(f"  affected: {names}")
            _scoped_check(subset, fix=fix)
            return
    from livery.workshop._provenance import provenance_check

    if fix:
        format(fix=True)
        lint(fix=True)
        provenance_check(fix=True)
        with parallel():
            typecheck()
            typecomplete()
            test()
            kindcheck()
            template_check()
        return
    with parallel():
        format()
        lint()
        typecheck()
        typecomplete()
        test()
        kindcheck()
        template_check()
        provenance_check()


def _scoped_check(subset: tuple[Package, ...], *, fix: bool = False) -> None:
    """The gate over *subset* only: this routes, the backends compose.

    The render gate is skipped: its inputs are the root answers and
    the template source, which a package-scoped change cannot touch
    (touching them makes the change root-scoped, and the full gate
    runs instead). ``fix`` runs every kind's rewriters serially
    before any check reads the tree, exactly as the whole gate does;
    what each kind checks, and in what parallel shape, is its
    backend's knowledge, not this router's.
    """
    from livery.footman import step

    root = workspace_root()
    assert root is not None
    if fix:
        _python.scoped_rewrite(subset)
    with parallel() as p:
        p(
            step(_python.scoped_gate, title="python")(
                subset, root=root, check_style=not fix
            )
        )
        run_kind_checks(subset, root)


coverage = group("coverage", help="The measured union and its floors")


@coverage.task(name="enforce")
def coverage_enforce() -> None:
    """Enforce every package's floor on the combined coverage data.

    Runs where a merged ``.coverage`` file already exists, the
    aggregating CI job after it combines every leg's data; the same
    floors `fm test` checks quickly on one machine, here judged on
    the cross-platform union.
    """
    root = workspace_root()
    if root is None:
        raise ValueError("no workspace: no workshop.toml above the working directory")
    _python.enforce_coverage(root, _packages())


caches = group("caches", help="The workspace's derived caches")


@caches.task(name="clear")
def caches_clear() -> None:
    """Remove build artifacts and checker caches."""
    import shutil

    root = workspace_root()
    if root is None:
        return
    for name in ("dist", ".pytest_cache", ".ruff_cache", ".mypy_cache"):
        shutil.rmtree(root / name, ignore_errors=True)
