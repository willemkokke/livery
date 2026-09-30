"""The gate's one walk: a check with no file to read in scope starts no process."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from livery.workshop import _checks
from livery.workshop._checks import (
    PACKAGE,
    ROOT_UNIT,
    CheckRecord,
    Claim,
    GateContext,
    catalogue,
    reads_files,
    register_check,
    run_check,
    with_files,
)
from livery.workshop._packages import discover_packages


def _repository(tmp_path: Path) -> Path:
    """A workspace git knows: one python package with a source file, a tasks.py."""
    root = tmp_path / "ws"
    member = root / "packages" / "one"
    (member / "src" / "one").mkdir(parents=True)
    (member / "src" / "one" / "__init__.py").write_text("x = 1\n")
    (member / "workshop.toml").write_text('kind = "python"\nname = "acme-one"\n')
    (member / "pyproject.toml").write_text('[project]\nname = "acme-one"\n')
    (root / "workshop.toml").write_text("[workspace]\n")
    (root / "tasks.py").write_text("x = 1\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


@pytest.fixture
def registry() -> Iterator[None]:
    state = _checks.snapshot()
    yield
    _checks.restore(state)


def _idle(ctx: GateContext) -> None:
    del ctx


# The fallback first: without a listing, every check that applies runs.


def test_without_a_git_listing_every_check_reads_files(tmp_path: Path) -> None:
    ctx = GateContext(root=tmp_path, packages=())
    assert catalogue(ctx) is None
    record = CheckRecord(
        "acme", "lint", _idle, claims=(Claim("source", suffixes=(".xyz",)),)
    )
    assert reads_files(record, ctx)


# Then the filter: the claims decide, per scope.


def test_a_check_whose_claims_reach_nothing_in_scope_is_said_and_not_started(
    tmp_path: Path, registry: None, capsys: pytest.CaptureFixture[str]
) -> None:
    from dataclasses import replace

    root = _repository(tmp_path)
    packages = discover_packages(root)
    ctx = GateContext(root=root, packages=packages)
    listed = catalogue(ctx)
    assert listed is not None
    assert ("src/one/__init__.py", "source") in listed["packages/one"]
    assert ("tasks.py", "configuration") in listed[ROOT_UNIT]
    ctx = replace(ctx, catalogue=listed)
    register_check(
        CheckRecord(
            "acme-xyz", "lint", _idle, claims=(Claim("source", suffixes=(".xyz",)),)
        )
    )
    register_check(
        CheckRecord(
            "acme-py", "lint", _idle, claims=(Claim("source", suffixes=(".py",)),)
        )
    )
    register_check(CheckRecord("acme-tree", "lint", _idle))
    assert with_files(("acme-xyz", "acme-py", "acme-tree"), ctx) == (
        "acme-py",
        "acme-tree",
    )
    assert (
        "acme-xyz: no file it reads in the workspace; not run"
        in capsys.readouterr().out
    )
    # The root's own files count: tasks.py is configuration there.
    register_check(
        CheckRecord(
            "acme-conf",
            "lint",
            _idle,
            claims=(Claim("configuration", suffixes=(".py",)),),
        )
    )
    assert reads_files(_checks.check_for("acme-conf"), ctx)
    # A scoped run reads its subset and the root's files.
    scoped = replace(ctx, subset=packages)
    assert with_files(("acme-xyz",), scoped) == ()
    assert "in the affected packages; not run" in capsys.readouterr().out


def test_a_package_check_skips_a_package_with_none_of_its_files(
    tmp_path: Path, registry: None, capsys: pytest.CaptureFixture[str]
) -> None:
    from dataclasses import replace

    root = _repository(tmp_path)
    packages = discover_packages(root)
    ctx = GateContext(root=root, packages=packages)
    ctx = replace(ctx, catalogue=catalogue(ctx))
    ran: list[str] = []

    def body(ctx: GateContext) -> None:
        assert ctx.package is not None
        ran.append(ctx.package.path)

    register_check(
        CheckRecord(
            "acme-cpp",
            "format",
            body,
            scope=PACKAGE,
            kinds=("python",),
            claims=(Claim("source", suffixes=(".cpp",)),),
        )
    )
    run_check("acme-cpp", ctx)
    assert ran == []
    assert "acme-cpp: packages/one has no file it reads; not run" in (
        capsys.readouterr().out
    )
    (root / "packages" / "one" / "src" / "one" / "native.cpp").write_text("int x;\n")
    ctx = replace(ctx, catalogue=catalogue(ctx))
    run_check("acme-cpp", ctx)
    assert ran == ["packages/one"]
