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

    checks = _checks.snapshot()
    kinds = dict(_kinds._KINDS)
    yield
    _checks.restore(checks)
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
    assert "lint.spy" in check_names() and "lint" in roles()
    member = _package(tmp_path, "one", "python")
    whole = GateContext(root=tmp_path, packages=(member,))
    assert "lint.spy" in judges(whole)
    run_check("lint.spy", whole)
    assert seen == [("spy", None)]
    # Narrowed to the subset it is handed.
    seen.clear()
    scoped = GateContext(root=tmp_path, packages=(member,), subset=(member,))
    run_check("lint.spy", scoped)
    assert seen == [("spy", ("packages/one",))]
    # Under --fix the rewriter runs, and is not judged again.
    seen.clear()
    fixing = GateContext(root=tmp_path, packages=(member,), fix=True)
    assert rewriters(fixing)[-1] == "lint.spy"
    run_check("lint.spy", fixing, fix=True)
    assert seen == [("fix", None)]
    assert "lint.spy" not in judges(fixing)
    # Re-registering the name replaces the record: how an extension swaps a tool.
    register_check(CheckRecord("spy", "lint", _noop))
    assert check_for("lint.spy").narrowing == NONE
    unregister_check("lint.spy")
    assert "lint.spy" not in check_names()


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
    assert "lint.probe" in judges(GateContext(root=tmp_path, packages=(py, native)))
    assert "lint.probe" not in judges(GateContext(root=tmp_path, packages=(py,)))
    run_check("lint.probe", GateContext(root=tmp_path, packages=(py, native)))
    assert ran == ["acme-native"]
    assert (
        "  lint.probe: packages/native runs (cpp-conan kind)" in capsys.readouterr().out
    )
    # A role the kind's contract lacks skips by name, as the gate prints it.
    assert gated((py, native), "typecheck") == (py,)
    assert (
        "typecheck: packages/native skips (cpp-conan kind)" in capsys.readouterr().out
    )


def test_the_constants_name_the_vocabulary() -> None:
    assert (WORKSPACE, PACKAGE) == ("workspace", "package")
    assert (PATHS, PACKAGES, NONE) == ("paths", "packages", "none")


# The options a package may set, refusals first.


def _member_with(tmp_path: Path, checks: str) -> Package:
    from livery.workshop._packages import discover_packages

    member = tmp_path / "packages" / "x"
    member.mkdir(parents=True, exist_ok=True)
    member.joinpath("workshop.toml").write_text(
        f'kind = "python"\nname = "livery-x"\n{checks}'
    )
    member.joinpath("pyproject.toml").write_text('[project]\nname = "livery-x"\n')
    (package,) = discover_packages(tmp_path)
    return package


def test_an_option_on_an_unknown_check_or_an_undeclared_option_refuses(
    tmp_path: Path,
) -> None:
    from livery.workshop._checks import check_for, option_problems, option_value

    package = _member_with(tmp_path, "[checks.nothing.x]\nenabled = false\n")
    (problem,) = option_problems((package,))
    assert problem.startswith(
        "packages/x/workshop.toml: [checks.nothing.x] names no registered check"
    )
    package = _member_with(tmp_path, "[checks.test.pytest]\nworkers = 3\n")
    (problem,) = option_problems((package,))
    assert "declares no option 'workers'; its options are parallel, enabled" in problem
    package = _member_with(tmp_path, '[checks.test.pytest]\nparallel = "no"\n')
    (problem,) = option_problems((package,))
    assert problem.endswith("[checks.test.pytest] parallel is bool, not 'no'")
    with pytest.raises(_FAILURES, match="parallel is bool"):
        option_value(check_for("test.pytest"), package, "parallel")
    with pytest.raises(_FAILURES, match="declares no option 'workers'"):
        option_value(check_for("test.pytest"), package, "workers")


def test_a_checks_table_off_the_shape_refuses(tmp_path: Path) -> None:
    from livery.workshop._packages import discover_packages

    member = tmp_path / "packages" / "x"
    member.mkdir(parents=True)
    member.joinpath("workshop.toml").write_text(
        'kind = "python"\nname = "livery-x"\nchecks = 3\n'
    )
    member.joinpath("pyproject.toml").write_text('[project]\nname = "livery-x"\n')
    with pytest.raises(
        _FAILURES, match=r"checks is an integer \(3\); it takes a table"
    ):
        discover_packages(tmp_path)
    # A check's options live in its own table, never on its role: the
    # role's table holds one table per tool.
    member.joinpath("workshop.toml").write_text(
        'kind = "python"\nname = "livery-x"\n[checks.test]\nparallel = false\n'
    )
    with pytest.raises(
        _FAILURES,
        match=r"checks.test.parallel is a boolean \(False\); it takes a table",
    ):
        discover_packages(tmp_path)
    member.joinpath("workshop.toml").write_text(
        'kind = "python"\nname = "livery-x"\n[checks]\ntest = 3\n'
    )
    with pytest.raises(
        _FAILURES, match=r"checks.test is an integer \(3\); it takes a table"
    ):
        discover_packages(tmp_path)


def test_a_package_turns_a_check_off_and_is_skipped_by_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop._checks import check_for, enabled, option_value

    package = _member_with(
        tmp_path, "[checks.typecomplete.basedpyright]\nenabled = false\n"
    )
    record = check_for("typecomplete.basedpyright")
    assert option_value(record, package, "enabled") is False
    assert option_value(check_for("test.pytest"), package, "enabled") is True
    assert enabled("typecomplete.basedpyright", (package,)) == ()
    assert (
        "typecomplete.basedpyright: packages/x skips (turned off in"
        " packages/x/workshop.toml)" in (capsys.readouterr().out)
    )
    assert enabled("test.pytest", (package,)) == (package,)


def test_a_package_that_is_not_parallel_safe_runs_its_suite_under_n_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._backends import _python
    from livery.workshop._checks import GateContext, check_for

    serial = _member_with(tmp_path, "[checks.test.pytest]\nparallel = false\n")
    other = tmp_path / "packages" / "y"
    other.mkdir()
    other.joinpath("workshop.toml").write_text('kind = "python"\nname = "livery-y"\n')
    other.joinpath("pyproject.toml").write_text('[project]\nname = "livery-y"\n')
    from livery.workshop._packages import discover_packages

    packages = discover_packages(tmp_path)
    runs: list[tuple[tuple[str, ...], tuple[str, ...]]] = []

    def fake_run_test(
        *args: str, packages: tuple[Package, ...], **kwargs: object
    ) -> None:
        del kwargs
        runs.append((args, tuple(p.path for p in packages)))

    monkeypatch.setattr(_python, "run_test", fake_run_test)
    monkeypatch.setattr("livery.workshop._quality.workspace_root", lambda: tmp_path)
    ctx = GateContext(root=tmp_path, packages=packages)
    check_for("test.pytest").run(ctx)
    assert runs == [
        ((), ("packages/y",)),
        (("-n", "0"), ("packages/x",)),
    ]
    del serial
