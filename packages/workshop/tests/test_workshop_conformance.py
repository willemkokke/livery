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
from livery.workshop._checks import (
    PACKAGE,
    CheckRecord,
    Claim,
    GateContext,
    check_for,
    register_check,
)
from livery.workshop._fragments import Fragment, package_fragment
from livery.workshop._kinds import Backend, KindRecord, kind_for, register_kind
from livery.workshop._packages import Package
from livery.workshop.testing import CLAUSES, Subject, builtin_subject, judge

EXTENSION = "acme.extension"


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
        _categories.unregister_categories(kind, extension=EXTENSION)


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
    found = _names(Subject(EXTENSION, kinds=(kind,)), "backend-protocol")
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
        for line in _names(Subject(EXTENSION, kinds=(concrete,)), "backend-protocol")
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
                extension=EXTENSION,
                fragments=(Fragment(".clang-tidy", f"# {name}\n", kind="acme-child"),),
            )
        )
    found = _names(Subject(EXTENSION, kinds=(child,)), "nearest-fragment")
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
        "acme-child",
        [("lib/*.py", "source"), ("lib/x*.p*", "test")],
        extension=EXTENSION,
    )
    found = _names(Subject(EXTENSION, kinds=(child,)), "category-table")
    assert any(
        "kind acme-child lib/x.py: 'lib/*.py' (source, acme.extension) and 'lib/x*.p*'"
        " (test, acme.extension) claim it at one specificity for one kind" in line
        for line in found
    )


def test_a_check_after_nothing_or_after_itself_breaks_the_check_order(
    acme: None,
) -> None:
    register_check(
        CheckRecord("late", "lint", _idle, after=("lint.gone",), extension=EXTENSION)
    )
    register_check(
        CheckRecord("a", "lint", _idle, after=("lint.b",), extension=EXTENSION)
    )
    register_check(
        CheckRecord("b", "lint", _idle, after=("lint.a",), extension=EXTENSION)
    )
    checks = tuple(check_for(name) for name in ("lint.late", "lint.a", "lint.b"))
    found = _names(Subject(EXTENSION, checks=checks), "check-order")
    assert found == [
        "check-order: check lint.late: runs after lint.gone, which no registered"
        " check answers to; the gate stops there",
        "check-order: check lint.a: runs after itself through lint.b, lint.a;"
        " the gate would wait on it for ever",
        "check-order: check lint.b: runs after itself through lint.a, lint.b;"
        " the gate would wait on it for ever",
    ]


def test_a_declaration_with_an_unknown_key_or_a_dangling_reference_breaks_the_clause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "acme_kit_extension"
    package.mkdir()
    (package / "_checks.py").write_text("def judged(ctx):\n    del ctx\n")
    declaration = package / "extension.toml"
    declaration.write_text('[extension]\napi-version = 1\nlevel = ["workspace"]\n')
    monkeypatch.syspath_prepend(str(tmp_path))
    assert _names(Subject("acme_kit_extension"), "declaration-validates") == [
        f"declaration-validates: extension acme_kit_extension: {declaration}:\n"
        "  [extension] has no key 'level': it takes api-version, levels, plugin,"
        " requires; did you mean 'levels'?; the mount refuses the extension"
    ]
    declaration.write_text(
        '[checks.acme.lint]\nrun = "acme_kit_extension._checks:judge"\n'
    )
    assert _names(Subject("acme_kit_extension"), "declaration-validates") == [
        f"declaration-validates: extension acme_kit_extension: {declaration}:"
        " checks.acme.lint.run names judge, which acme_kit_extension._checks does"
        " not define at its top level; did you mean 'judged'?; the mount refuses"
        " the extension"
    ]
    declaration.write_text(
        '[checks.acme.lint]\nrun = "acme_kit_extension._checks:judged"\n'
    )
    assert _names(Subject("acme_kit_extension"), "declaration-validates") == []


def test_a_reference_whose_module_registers_at_import_breaks_the_clause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "acme_kit_registers"
    package.mkdir()
    (package / "_checks.py").write_text(
        "from livery.workshop._checks import CheckRecord, register_check\n"
        "\n\n"
        "def judged(ctx):\n"
        "    del ctx\n"
        "\n\n"
        'register_check(CheckRecord("acme", "lint", judged))\n'
    )
    (package / "_pure.py").write_text("def judged(ctx):\n    del ctx\n")
    declaration = package / "extension.toml"
    declaration.write_text(
        '[checks.acme.lint]\nrun = "acme_kit_registers._checks:judged"\n'
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    clause = "references-register-nothing"
    assert _names(Subject("acme_kit_registers"), clause) == [
        "references-register-nothing: acme_kit_registers._checks, named by"
        " checks.acme.lint.run: importing it registers a call to"
        " livery.workshop._checks.register_check; the mount registers what the"
        " declaration says, so a reference's module only defines"
    ]
    declaration.write_text(
        '[checks.acme.lint]\nrun = "acme_kit_registers._pure:judged"\n'
    )
    assert _names(Subject("acme_kit_registers"), clause) == []


def test_every_installed_extensions_references_register_nothing() -> None:
    from livery.footman import installed_entry_points

    packages = sorted(
        {
            entry.value.partition(":")[0]
            for entry in installed_entry_points("workshop.extensions")
        }
    )
    assert packages
    found = [
        line
        for package in packages
        for line in _names(Subject(package), "references-register-nothing")
    ]
    assert found == []


def _tidy(text: str = "Checks: acme-*\n") -> Subject:
    """An extension whose check carries a .clang-tidy for the child kind."""
    _parent, child = _family()
    register_check(
        CheckRecord(
            "acme-tidy",
            "lint",
            _idle,
            scope=PACKAGE,
            kinds=("acme-child",),
            extension=EXTENSION,
            fragments=(Fragment(".clang-tidy", text, kind="acme-child"),),
        )
    )
    return Subject(EXTENSION, kinds=(child,), checks=(check_for("lint.acme-tidy"),))


TIDY = "check lint.acme-tidy .clang-tidy for acme-child"


def test_a_fragment_that_breaks_the_composed_file_or_does_not_render_breaks_the_drift(
    acme: None,
) -> None:
    subject = _tidy("{% if %}\n")
    register_check(
        CheckRecord(
            "acme-table",
            "lint",
            _idle,
            extension=EXTENSION,
            fragments=(
                Fragment(
                    "pyproject.toml", "[tool.acme]\nstrict = []\n\n[tool.acme]\nx = 1\n"
                ),
            ),
        )
    )
    subject = replace(subject, checks=(*subject.checks, check_for("lint.acme-table")))
    found = _names(subject, "fragment-drift")
    assert len(found) == 2, found
    assert found[0].startswith(
        "fragment-drift: pyproject.toml: composed with the fragment of"
        " lint.acme-table, it is not TOML: Cannot declare ('tool', 'acme') twice"
    )
    assert found[1].startswith(f"fragment-drift: {TIDY}: does not render: ")


def test_a_file_that_drifts_from_its_render_or_hides_an_edit_breaks_the_drift(
    acme: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The render and the drift judge are the workshop's; the clause
    # names the extension's file wherever the two disagree about it.
    from livery.workshop import _shipped_files

    subject = _tidy()
    assert _names(subject, "fragment-drift") == []
    monkeypatch.setattr(_shipped_files, "judge_package", lambda *args: [])
    assert _names(subject, "fragment-drift") == [
        f"fragment-drift: {TIDY}: a hand edit of the rendered file is not named as"
        " drift"
    ]
    monkeypatch.setattr(
        _shipped_files, "judge_package", lambda *args: ["probe: differs"]
    )
    assert _names(subject, "fragment-drift") == [
        f"fragment-drift: {TIDY}: drifts from its own render: probe: differs"
    ]


def test_a_withdrawn_checks_file_kept_unedited_or_removed_edited_breaks_contract_11(
    acme: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _shipped_files

    subject = _tidy()
    assert _names(subject, "withdrawn-file") == []
    real = _shipped_files.settle_package

    def written(directory: Path) -> list[Path]:
        # The subject's own file, which its withdrawn check no longer names.
        return [p for p in directory.iterdir() if p.name == ".clang-tidy"]

    def never_again(directory: Path, kind: str) -> list[str]:
        if written(directory):
            return []
        return real(directory, kind)

    monkeypatch.setattr(_shipped_files, "settle_package", never_again)
    assert _names(subject, "withdrawn-file") == [
        f"withdrawn-file: {TIDY}: an unedited copy stays once the check is"
        " withdrawn; contract 11 removes it"
    ]

    def always_removes(directory: Path, kind: str) -> list[str]:
        found = written(directory)
        for path in found:
            path.unlink()
        return [f"removed {path.name}" for path in found] or real(directory, kind)

    monkeypatch.setattr(_shipped_files, "settle_package", always_removes)
    assert _names(subject, "withdrawn-file") == [
        f"withdrawn-file: {TIDY}: an edited copy is removed once the check is"
        " withdrawn; contract 11 keeps it as a local override"
    ]


def _walkers() -> Subject:
    """An extension with a check that fixes and one that judges, both reading python."""
    register_check(
        CheckRecord(
            "acme-fixer",
            "format",
            _idle,
            fix=_idle,
            extension=EXTENSION,
            claims=(Claim("source", suffixes=(".py",)),),
        )
    )
    register_check(
        CheckRecord(
            "acme-judge",
            "lint",
            _idle,
            scope=PACKAGE,
            kinds=("python",),
            extension=EXTENSION,
            claims=(Claim("source", suffixes=(".py",)),),
        )
    )
    checks = (check_for("format.acme-fixer"), check_for("lint.acme-judge"))
    return Subject(EXTENSION, checks=checks)


def test_a_check_registered_without_its_extension_breaks_the_gate_lines(
    acme: None,
) -> None:
    register_check(CheckRecord("acme-quiet", "lint", _idle))
    found = _names(
        Subject(EXTENSION, checks=(check_for("lint.acme-quiet"),)), "gate-lines"
    )
    assert found == [
        "gate-lines: check lint.acme-quiet: names livery.workshop as its extension; the"
        " gate says who registered a check by it, and acme.extension registered this"
        " one"
    ]


def test_a_walk_that_starts_a_check_with_nothing_to_read_breaks_the_gate_lines(
    acme: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _quality

    subject = _walkers()
    assert _names(subject, "gate-lines") == []

    def careless(ctx: GateContext, **kwargs: object) -> None:
        only = cast("frozenset[str]", kwargs["only"])
        for name in sorted(only):
            _checks.check_for(name).run(ctx)

    monkeypatch.setattr(_quality, "walk", careless)
    assert _names(subject, "gate-lines") == [
        "gate-lines: check format.acme-fixer: the gate does not name acme.extension as"
        " the extension that registered it",
        "gate-lines: check format.acme-fixer: not named when it had no file to"
        ' read; the gate says "no file it reads" and starts nothing',
        "gate-lines: check format.acme-fixer: started with no file to read",
        "gate-lines: check lint.acme-judge: the gate does not name acme.extension as"
        " the extension that registered it",
        "gate-lines: check lint.acme-judge: not named when it had no file to read;"
        ' the gate says "no file it reads" and starts nothing',
        "gate-lines: check lint.acme-judge: started with no file to read",
    ]


def test_a_walk_that_judges_before_it_fixes_breaks_the_walk_order(
    acme: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _quality

    subject = _walkers()
    assert _names(subject, "walk-order") == []

    def backwards(ctx: GateContext, **kwargs: object) -> None:
        only = cast("frozenset[str]", kwargs["only"])
        for name in sorted(only):
            record = _checks.check_for(name)
            record.run(ctx)
        for name in sorted(only):
            record = _checks.check_for(name)
            if record.fix is not None:
                record.fix(ctx)

    monkeypatch.setattr(_quality, "walk", backwards)
    assert _names(subject, "walk-order") == [
        "walk-order: check format.acme-fixer: rewrote after a judge started; every"
        " fixer runs before any judge",
        "walk-order: check format.acme-fixer: judged after it rewrote; a check that"
        " rewrote is not judged again",
    ]

    def idle(ctx: GateContext, **kwargs: object) -> None:
        del ctx, kwargs

    monkeypatch.setattr(_quality, "walk", idle)
    assert _names(subject, "walk-order") == [
        "walk-order: check format.acme-fixer: never rewrote under --fix",
        "walk-order: check lint.acme-judge: never judged under --fix",
    ]


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
                extension=EXTENSION,
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
    assert _names(Subject(EXTENSION, kinds=(parent, child)), "nearest-fragment") == []


def test_the_nearer_kinds_rule_wins_a_tie_between_kinds(acme: None) -> None:
    parent, child = _family()
    _categories.register_categories(
        "acme-parent", [("gen/**", "generated")], extension=EXTENSION
    )
    _categories.register_categories(
        "acme-child", [("gen/**", "source")], extension=EXTENSION
    )
    probe = _checks_package("acme-child")
    assert _categories.category_of(probe, "gen/a").name == "source"
    assert _categories.category_of(_checks_package("acme-parent"), "gen/a").name == (
        "generated"
    )
    assert _names(Subject(EXTENSION, kinds=(parent, child)), "category-table") == []
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
        "build.compile",
        "test.ctest",
    }
    assert judge(subject) == []


def test_the_clauses_are_named_once_and_state_their_rule() -> None:
    names = [clause.name for clause in CLAUSES]
    assert names == [
        "backend-protocol",
        "nearest-fragment",
        "category-table",
        "check-order",
        "declaration-validates",
        "references-register-nothing",
        "fragment-drift",
        "withdrawn-file",
        "walk-order",
        "gate-lines",
    ]
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
