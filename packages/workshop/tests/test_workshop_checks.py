"""The check registry: refusals first, then a fake check runs, narrows and skips."""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.workshop._checks import (
    NONE,
    PACKAGE,
    PACKAGES,
    PATHS,
    WORKSPACE,
    CheckRecord,
    GateContext,
    check_for,
    check_names,
    judges,
    register_check,
    rewriters,
    roles,
    run_check,
    unregister_check,
    verify_roles,
)
from livery.workshop._kinds import CiContract, KindRecord, register_kind
from livery.workshop._packages import Package

_FAILURES = (BaseException,)


@pytest.fixture
def restored_registries():
    from livery.workshop import _checks, _kinds

    checks = dict(_checks._CHECKS)
    kinds = dict(_kinds._KINDS)
    yield
    _checks._CHECKS.clear()
    _checks._CHECKS.update(checks)
    _kinds._KINDS.clear()
    _kinds._KINDS.update(kinds)


def _package(tmp_path: Path, name: str, kind: str) -> Package:
    directory = tmp_path / "packages" / name
    directory.mkdir(parents=True, exist_ok=True)
    return Package(
        directory=directory,
        path=f"packages/{name}",
        name=f"acme-{name}",
        kind=kind,
        depends=(),
    )


def _noop(ctx: GateContext) -> None:
    del ctx


# The refusals first.


def test_a_record_with_an_unknown_scope_or_narrowing_refuses(restored_registries):
    with pytest.raises(_FAILURES, match="scope is 'workspace' or 'package'"):
        register_check(CheckRecord("odd", "lint", _noop, scope="sometimes"))
    with pytest.raises(_FAILURES, match="narrowing is 'paths', 'packages' or 'none'"):
        register_check(CheckRecord("odd", "lint", _noop, narrowing="somehow"))


def test_a_package_check_naming_no_kind_refuses(restored_registries):
    with pytest.raises(_FAILURES, match="names no kind"):
        register_check(CheckRecord("odd", "lint", _noop, scope=PACKAGE))


def test_an_unknown_check_name_refuses_naming_the_registry(restored_registries):
    with pytest.raises(_FAILURES, match="not a registered check; checks: format"):
        check_for("nothing")
    with pytest.raises(_FAILURES, match="not a registered check"):
        unregister_check("nothing")


def test_a_kind_gating_on_a_role_no_check_implements_refuses(restored_registries):
    register_kind(
        KindRecord(
            name="odd-fake",
            abstract=True,
            ci=CiContract(check_verbs=("format", "divination")),
        )
    )
    with pytest.raises(_FAILURES, match="'odd-fake' gates on divination"):
        verify_roles()


# Then the fake check: registered, run, narrowed, skipped by name.


def test_a_registered_check_runs_narrows_and_is_replaced_by_name(
    restored_registries, tmp_path: Path
) -> None:
    seen: list[tuple[str, tuple[str, ...] | None]] = []

    def spy(ctx: GateContext) -> None:
        seen.append(
            ("spy", None if ctx.subset is None else tuple(p.path for p in ctx.subset))
        )

    def fixer(ctx: GateContext) -> None:
        seen.append(("fix", None))

    register_check(CheckRecord("spy", "lint", spy, narrowing=PATHS, fix=fixer))
    assert "spy" in check_names() and "lint" in roles()
    member = _package(tmp_path, "one", "python")
    whole = GateContext(root=tmp_path, packages=(member,))
    assert "spy" in judges(whole)
    run_check("spy", whole)
    assert seen == [("spy", None)]
    # Narrowed to the subset it is handed.
    seen.clear()
    scoped = GateContext(root=tmp_path, packages=(member,), subset=(member,))
    run_check("spy", scoped)
    assert seen == [("spy", ("packages/one",))]
    # Under --fix the rewriter runs, and is not judged again.
    seen.clear()
    fixing = GateContext(root=tmp_path, packages=(member,), fix=True)
    assert rewriters(fixing)[-1] == "spy"
    run_check("spy", fixing, fix=True)
    assert seen == [("fix", None)]
    assert "spy" not in judges(fixing)
    # Re-registering the name replaces the record: how a layer swaps a tool.
    register_check(CheckRecord("spy", "lint", _noop))
    assert check_for("spy").narrowing == NONE
    unregister_check("spy")
    assert "spy" not in check_names()


def test_a_package_check_runs_for_its_kinds_alone_and_skips_by_name(
    restored_registries, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop._kinds import gated

    ran: list[str] = []

    def spy(ctx: GateContext) -> None:
        assert ctx.package is not None
        ran.append(ctx.package.name)

    register_check(
        CheckRecord("probe", "lint", spy, scope=PACKAGE, kinds=("cpp-conan",))
    )
    py = _package(tmp_path, "member", "python")
    native = _package(tmp_path, "native", "cpp-conan")
    # The probe judges the native package alone, announcing it by name;
    # a workspace of python packages schedules no probe at all.
    assert "probe" in judges(GateContext(root=tmp_path, packages=(py, native)))
    assert "probe" not in judges(GateContext(root=tmp_path, packages=(py,)))
    run_check("probe", GateContext(root=tmp_path, packages=(py, native)))
    assert ran == ["acme-native"]
    assert "  probe: packages/native runs (cpp-conan kind)" in capsys.readouterr().out
    # A role the kind's contract lacks skips by name, as the gate prints it.
    assert gated((py, native), "typecheck") == (py,)
    assert (
        "typecheck: packages/native skips (cpp-conan kind)" in capsys.readouterr().out
    )


def test_the_constants_name_the_vocabulary() -> None:
    assert (WORKSPACE, PACKAGE) == ("workspace", "package")
    assert (PATHS, PACKAGES, NONE) == ("paths", "packages", "none")
