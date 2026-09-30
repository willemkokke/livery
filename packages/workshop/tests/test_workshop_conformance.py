"""The conformance kit: a broken subject fails a clause by name; builtins pass."""

from __future__ import annotations

import types
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

import livery.workshop.testing
from livery.workshop import _categories, _checks, _kinds
from livery.workshop._checks import PACKAGE, CheckRecord, GateContext, register_check
from livery.workshop._fragments import Fragment, package_fragment
from livery.workshop._kinds import Backend, KindRecord, kind_for, register_kind
from livery.workshop._packages import Package
from livery.workshop.testing import CLAUSES, Subject, builtin_subject, judge

LAYER = "acme.layer"


@pytest.fixture
def acme() -> Iterator[None]:
    """Restore the kinds, checks and category tables a test registers into."""
    kinds = dict(_kinds._KINDS)  # pyright: ignore[reportPrivateUsage]
    checks = _checks.snapshot()
    yield
    _kinds._KINDS.clear()  # pyright: ignore[reportPrivateUsage]
    _kinds._KINDS.update(kinds)  # pyright: ignore[reportPrivateUsage]
    _checks.restore(checks)
    for kind in ("acme-parent", "acme-child"):
        _categories.unregister_categories(kind, layer=LAYER)


def _idle(ctx: GateContext) -> None:
    del ctx


def _family() -> tuple[KindRecord, KindRecord]:
    """A parent kind and its child, both python underneath."""
    python = kind_for("python")
    parent = replace(python, name="acme-parent", parent="python", template="")
    child = replace(python, name="acme-child", parent="acme-parent", template="")
    register_kind(parent)
    register_kind(child)
    return parent, child


def _names(subject: Subject, clause: str) -> list[str]:
    found = [str(v) for v in judge(subject) if v.clause == clause]
    assert all(line.startswith(f"{clause}: ") for line in found)
    return found


# The refusals first: a broken subject fails each clause with the clause named.


def test_a_backend_missing_a_method_or_taking_the_wrong_call_breaks_the_protocol(
    acme: None,
) -> None:
    python = kind_for("python")
    members: dict[str, object] = {
        name: getattr(python.backend, name)
        for name in dir(python.backend)
        if not name.startswith("_")
    }
    del members["gate_build"]

    def build(package: object, *, epoch: int = 0) -> Path:
        del package, epoch
        return Path()

    def test(package: object, root: Path, selection: tuple[str, ...]) -> None:
        del package, root, selection

    def current_version(package: object, strict: bool) -> str:
        del package, strict
        return ""

    members.update(build=build, test=test, current_version=current_version)
    # A namespace stands in for a backend module; the kit reads it the
    # way the gate reads a module, by attribute.
    broken = cast("Backend", types.SimpleNamespace(**members))
    kind = replace(python, name="acme-broken", backend=broken)
    found = _names(Subject(LAYER, kinds=(kind,)), "backend-protocol")
    assert any("defines no gate_build()" in line for line in found)
    assert any(
        "build(): takes (package) where the protocol passes (package, root)" in line
        for line in found
    )
    assert any(
        "test(): requires 'selection', which the protocol lets a caller leave out"
        in line
        for line in found
    )
    assert any(
        "current_version(): requires 'strict', which the protocol never passes" in line
        for line in found
    )
    concrete = replace(python, name="acme-none", backend=None)
    assert any(
        "kind acme-none: names no backend" in line
        for line in _names(Subject(LAYER, kinds=(concrete,)), "backend-protocol")
    )


def test_two_checks_carrying_one_file_for_one_kind_break_the_nearest_fragment(
    acme: None,
) -> None:
    _parent, child = _family()
    for name in ("acme-tidy", "acme-tidy-too"):
        register_check(
            CheckRecord(
                name,
                "lint",
                _idle,
                scope=PACKAGE,
                kinds=("acme-child",),
                layer=LAYER,
                fragments=(Fragment(".clang-tidy", f"# {name}\n", kind="acme-child"),),
            )
        )
    found = _names(Subject(LAYER, kinds=(child,)), "nearest-fragment")
    assert found == [
        "nearest-fragment: kind acme-child .clang-tidy: lint.acme-tidy and"
        " lint.acme-tidy-too both carry it for the kind; the render would pick one"
        " by name, so one of them yields"
    ]


def test_two_rules_of_one_kind_at_one_specificity_break_the_category_table(
    acme: None,
) -> None:
    _parent, child = _family()
    _categories.register_categories(
        "acme-child", [("lib/*.py", "source"), ("lib/x*.p*", "test")], layer=LAYER
    )
    found = _names(Subject(LAYER, kinds=(child,)), "category-table")
    assert any(
        "kind acme-child lib/x.py: 'lib/*.py' (source, acme.layer) and 'lib/x*.p*'"
        " (test, acme.layer) claim it at one specificity for one kind" in line
        for line in found
    )


# The nearest kind wins, for fragments and for categories alike.


def test_the_nearest_kinds_fragment_renders(acme: None) -> None:
    parent, child = _family()
    for name, kind in (
        ("acme-parent-tidy", "acme-parent"),
        ("acme-child-tidy", "acme-child"),
    ):
        register_check(
            CheckRecord(
                name,
                "lint",
                _idle,
                scope=PACKAGE,
                kinds=(kind,),
                layer=LAYER,
                fragments=(Fragment(".clang-tidy", f"# {kind}\n", kind=kind),),
            )
        )
    assert package_fragment("acme-child", ".clang-tidy") == (
        "# acme-child\n",
        "lint.acme-child-tidy",
    )
    assert package_fragment("acme-parent", ".clang-tidy") == (
        "# acme-parent\n",
        "lint.acme-parent-tidy",
    )
    assert _names(Subject(LAYER, kinds=(parent, child)), "nearest-fragment") == []


def test_the_nearer_kinds_rule_wins_a_tie_between_kinds(acme: None) -> None:
    parent, child = _family()
    _categories.register_categories(
        "acme-parent", [("gen/**", "generated")], layer=LAYER
    )
    _categories.register_categories("acme-child", [("gen/**", "source")], layer=LAYER)
    probe = _checks_package("acme-child")
    assert _categories.category_of(probe, "gen/a").name == "source"
    assert _categories.category_of(_checks_package("acme-parent"), "gen/a").name == (
        "generated"
    )
    assert _names(Subject(LAYER, kinds=(parent, child)), "category-table") == []
    # The table lists the kind's own rules first, then each ancestor's.
    rules = _categories.category_rules("acme-child")
    assert rules[0].kind == "acme-child"


def _checks_package(kind: str) -> Package:
    return Package(Path("."), "packages/probe", "probe", kind, ())


# Then the builtins, and the kit's own shape.


def test_the_builtin_kinds_and_checks_pass_every_clause() -> None:
    subject = builtin_subject()
    assert {kind.name for kind in subject.kinds} >= {
        "python",
        "python-nanobind",
        "cpp-conan",
    }
    assert {record.name for record in subject.checks} >= {
        "format.ruff",
        "lint.ruff",
        "test.pytest",
    }
    assert judge(subject) == []


def test_the_clauses_are_named_once_and_state_their_rule() -> None:
    names = [clause.name for clause in CLAUSES]
    assert names == ["backend-protocol", "nearest-fragment", "category-table"]
    assert all(clause.rule.endswith(".") for clause in CLAUSES)


def test_the_kits_surface_is_declared() -> None:
    assert livery.workshop.testing.__all__ == [
        "CLAUSES",
        "Clause",
        "Subject",
        "Violation",
        "builtin_subject",
        "judge",
    ]
