"""The conformance kit: a broken subject fails a clause by name; builtins pass."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

import livery.workshop.testing
from livery.workshop import _categories, _steps
from livery.workshop._fragments import Fragment, package_fragment
from livery.workshop._packages import Package
from livery.workshop._phases import PhaseContext
from livery.workshop._steps import (
    PACKAGE,
    Claim,
    StepRecord,
    register_step,
    step_for,
    steps_by_name,
)
from livery.workshop.testing import CLAUSES, Subject, builtin_subject, judge

EXTENSION = "acme.extension"


@pytest.fixture
def acme() -> Iterator[None]:
    """Restore the steps a test registers."""
    steps = _steps.snapshot()
    yield
    _steps.restore(steps)


def _idle(ctx: PhaseContext) -> None:
    del ctx


#: A package-level extension whose one table claims ``lib/x.py`` twice at
#: one specificity, for different categories.
TIED = (
    '[extension]\nlevels = ["package"]\n\n[categories]\n'
    'source = ["lib/*.py"]\ntest = ["lib/x*.p*"]\n'
)


def _names(subject: Subject, clause: str) -> list[str]:
    found = [str(v) for v in judge(subject) if v.clause == clause]
    assert all(line.startswith(f"{clause}: ") for line in found)
    return found


# The refusals first: a broken subject fails each clause with the clause named.


def test_two_steps_carrying_one_file_for_one_extension_break_the_fragment_owner(
    acme: None,
) -> None:
    for name in ("acme-tidy", "acme-tidy-too"):
        register_step(
            StepRecord(
                name,
                "lint",
                _idle,
                scope=PACKAGE,
                extensions=("acme.cpp",),
                extension=EXTENSION,
                fragments=(
                    Fragment(".clang-tidy", f"# {name}\n", extensions=("acme.cpp",)),
                ),
            )
        )
    subject = Subject(EXTENSION, steps=(step_for("lint.acme-tidy"),))
    assert _names(subject, "fragment-owner") == [
        "fragment-owner: extension acme.cpp .clang-tidy: lint.acme-tidy and"
        " lint.acme-tidy-too both carry it for the extension's packages; the render"
        " picks one by name, so one of them yields"
    ]


def test_two_rules_of_one_table_at_one_specificity_break_the_category_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from workshop_extension_fakes import fake_package_extensions

    fake_package_extensions(tmp_path, monkeypatch, lib=TIED)
    found = _names(Subject(EXTENSION, sets=(("acme.lib",),)), "category-table")
    assert found == [
        "category-table: set acme.lib lib/x.py: 'lib/*.py' (source, acme.lib) and"
        " 'lib/x*.p*' (test, acme.lib) claim it at one specificity in one table;"
        " one of them names the file more closely"
    ]


def test_a_subject_naming_no_set_probes_no_package(
    acme: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _quality
    from workshop_extension_fakes import fake_package_extensions

    # acme.lib's table ties, and no set names it, so no probe holds it.
    fake_package_extensions(tmp_path, monkeypatch, lib=TIED)
    assert _names(Subject(EXTENSION), "category-table") == []
    # A walk that judges nothing: the package step has no package to judge,
    # so only the fixer that never rewrote is named.
    monkeypatch.setattr(_quality, "walk", _walked_nothing)
    assert _names(replace(_walkers(), sets=()), "walk-order") == [
        "walk-order: step format.acme-fixer: never rewrote under --fix",
    ]


def test_a_probe_of_the_empty_set_is_named_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A package listing nothing holds the base's table alone, so a tie
    # there is the empty set's.
    tied = tuple(
        _categories.CategoryRule(
            _categories.BASE_TABLE, pattern, category, "livery.workshop"
        )
        for pattern, category in (("lib/*.py", "source"), ("lib/x*.p*", "test"))
    )
    monkeypatch.setattr(_categories, "BASE_RULES", (*tied, *_categories.BASE_RULES))
    assert _names(Subject(EXTENSION, sets=((),)), "category-table") == [
        "category-table: set none lib/x.py: 'lib/*.py' (source, livery.workshop)"
        " and 'lib/x*.p*' (test, livery.workshop) claim it at one specificity in"
        " one table; one of them names the file more closely"
    ]


def test_a_step_after_nothing_or_after_itself_breaks_the_step_order(
    acme: None,
) -> None:
    # The fallback first: a step after one an installed extension declares
    # and the workspace did not mount, cmake's configure, breaks nothing.
    register_step(
        StepRecord(
            "tidy", "lint", _idle, after=("build.configure",), extension=EXTENSION
        )
    )
    assert "build.configure" not in steps_by_name()
    tidy = Subject(EXTENSION, steps=(step_for("lint.tidy"),))
    assert _names(tidy, "step-order") == []
    register_step(
        StepRecord("late", "lint", _idle, after=("lint.gone",), extension=EXTENSION)
    )
    register_step(
        StepRecord("a", "lint", _idle, after=("lint.b",), extension=EXTENSION)
    )
    register_step(
        StepRecord("b", "lint", _idle, after=("lint.a",), extension=EXTENSION)
    )
    steps = tuple(step_for(name) for name in ("lint.late", "lint.a", "lint.b"))
    found = _names(Subject(EXTENSION, steps=steps), "step-order")
    assert found == [
        "step-order: step lint.late: runs after lint.gone, which no registered"
        " step answers to and no installed extension declares; the gate stops"
        " there",
        "step-order: step lint.a: runs after itself through lint.b, lint.a;"
        " the gate would wait on it for ever",
        "step-order: step lint.b: runs after itself through lint.a, lint.b;"
        " the gate would wait on it for ever",
    ]


def test_a_declaration_with_an_unknown_key_or_a_dangling_reference_breaks_the_clause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "acme_kit_extension"
    package.mkdir()
    (package / "_steps.py").write_text("def judged(ctx):\n    del ctx\n")
    declaration = package / "extension.toml"
    declaration.write_text('[extension]\napi-version = 1\nlevel = ["workspace"]\n')
    monkeypatch.syspath_prepend(str(tmp_path))
    assert _names(Subject("acme_kit_extension"), "declaration-validates") == [
        f"declaration-validates: extension acme_kit_extension: {declaration}:\n"
        "  [extension] has no key 'level': it takes api-version, dormant-until,"
        " levels, plugin, requires; did you mean 'levels'?; the mount refuses"
        " the extension"
    ]
    declaration.write_text(
        '[phases.lint.acme]\nmain = "acme_kit_extension._steps:judge"\n'
    )
    assert _names(Subject("acme_kit_extension"), "declaration-validates") == [
        f"declaration-validates: extension acme_kit_extension: {declaration}:"
        " phases.lint.acme.main names judge, which acme_kit_extension._steps does"
        " not define at its top level; did you mean 'judged'?; the mount refuses"
        " the extension"
    ]
    declaration.write_text(
        '[phases.lint.acme]\nmain = "acme_kit_extension._steps:judged"\n'
    )
    assert _names(Subject("acme_kit_extension"), "declaration-validates") == []


def test_a_reference_whose_module_registers_at_import_breaks_the_clause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "acme_kit_registers"
    package.mkdir()
    (package / "_steps.py").write_text(
        "from livery.workshop._steps import StepRecord, register_step\n"
        "\n\n"
        "def judged(ctx):\n"
        "    del ctx\n"
        "\n\n"
        'register_step(StepRecord("acme", "lint", judged))\n'
    )
    (package / "_pure.py").write_text("def judged(ctx):\n    del ctx\n")
    declaration = package / "extension.toml"
    declaration.write_text(
        '[phases.lint.acme]\nmain = "acme_kit_registers._steps:judged"\n'
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    clause = "references-register-nothing"
    assert _names(Subject("acme_kit_registers"), clause) == [
        "references-register-nothing: acme_kit_registers._steps, named by"
        " phases.lint.acme.main: importing it registers a call to"
        " livery.workshop._steps.register_step; the mount registers what the"
        " declaration says, so a reference's module only defines"
    ]
    declaration.write_text(
        '[phases.lint.acme]\nmain = "acme_kit_registers._pure:judged"\n'
    )
    assert _names(Subject("acme_kit_registers"), clause) == []


def test_a_job_entry_naming_a_task_the_extension_does_not_define_breaks_the_clause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "acme_kit_jobs"
    package.mkdir()
    declaration = package / "extension.toml"
    declaration.write_text('[ci.jobs.gate.acme]\nentries = ["acme.build"]\n')
    monkeypatch.syspath_prepend(str(tmp_path))
    clause = "entries-name-defined-tasks"
    # The fallback first: an extension that defines no task names none.
    assert _names(Subject("acme_kit_jobs"), clause) == [
        "entries-name-defined-tasks: ci.jobs.gate.acme.entries: names acme.build,"
        " and the extension defines no task"
    ]
    (package / "_verbs.py").write_text(
        "from livery.footman import group\n\n"
        'acme = group("acme")\n\n\n'
        '@acme.task(name="build")\n'
        "def acme_build() -> None: ...\n"
    )
    declaration.write_text('[ci.jobs.gate.acme]\nentries = ["acme.biuld"]\n')
    assert _names(Subject("acme_kit_jobs"), clause) == [
        "entries-name-defined-tasks: ci.jobs.gate.acme.entries: names acme.biuld,"
        " which the extension does not define; did you mean 'acme.build'?"
    ]
    declaration.write_text('[ci.jobs.gate.acme]\nentries = ["acme.build"]\n')
    assert _names(Subject("acme_kit_jobs"), clause) == []


def test_a_claim_on_an_unknown_category_or_a_bare_suffix_breaks_the_clause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "acme_kit_claims"
    package.mkdir()
    (package / "_steps.py").write_text("def judged(ctx):\n    del ctx\n")
    declaration = package / "extension.toml"
    declaration.write_text(
        '[phases.lint.acme]\nmain = "acme_kit_claims._steps:judged"\n'
        'claims = [{ category = "sources", suffixes = ["py"] }]\n'
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    clause = "claims-name-categories"
    assert _names(Subject("acme_kit_claims"), clause) == [
        "claims-name-categories: phases.lint.acme.claims: names the category"
        " 'sources', which the workshop does not know; did you mean 'source'?;"
        " the step claims no file of it",
        "claims-name-categories: phases.lint.acme.claims: names the suffix 'py';"
        " a suffix starts with a dot, '.py'",
    ]
    declaration.write_text(
        '[phases.lint.acme]\nmain = "acme_kit_claims._steps:judged"\n'
        'claims = [{ category = "source", suffixes = [".py"] }]\n'
    )
    assert _names(Subject("acme_kit_claims"), clause) == []


def _words_subject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str
) -> Subject:
    package = tmp_path / "acme_kit_words"
    package.mkdir(exist_ok=True)
    (package / "extension.toml").write_text(text)
    monkeypatch.syspath_prepend(str(tmp_path))
    return Subject("acme_kit_words")


def test_an_unanswered_placeholder_fails_the_kit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clause = "words-are-answerable"
    subject = _words_subject(
        tmp_path,
        monkeypatch,
        '[phases.lint.acme]\ntools = ["acme"]\n'
        'main = ["acme", "--cache={cache}", "--into={output}"]\n',
    )
    assert _names(subject, clause) == [
        "words-are-answerable: phases.lint.acme: names {output}, which neither the"
        " engine nor its matrix answers; the engine answers cache, package,"
        " compile-commands, paths, arguments"
    ]
    # A tool the step does not require, and a package's placeholder in a
    # step that judges the workspace whole, fail it as well.
    subject = _words_subject(
        tmp_path,
        monkeypatch,
        '[phases.lint.acme]\ntools = ["other"]\nmain = ["acme", "{package}"]\n',
    )
    assert _names(subject, clause) == [
        "words-are-answerable: phases.lint.acme: runs acme, which its tools do not"
        " name (other); a step runs a tool it requires",
        "words-are-answerable: phases.lint.acme: names {package}, which only a call"
        " that judges one package answers, and the step judges the workspace"
        " whole",
    ]
    subject = _words_subject(
        tmp_path,
        monkeypatch,
        '[phases.lint.acme]\ntools = ["acme"]\nscope = "package"\n'
        'main = ["acme", "--cache={cache}", "{package}"]\n',
    )
    assert _names(subject, clause) == []
    # A step's main places {paths} and {arguments} itself: neither is
    # a placeholder left for the engine to answer.
    subject = _words_subject(
        tmp_path,
        monkeypatch,
        '[phases.lint.acme]\ntools = ["other"]\n'
        'main = ["acme", "{paths}", "{arguments}"]\n',
    )
    assert _names(subject, clause) == [
        "words-are-answerable: phases.lint.acme: runs acme, which its tools do not"
        " name (other); a step runs a tool it requires"
    ]


def test_a_matrix_key_absent_from_the_words_fails_the_kit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clause = "words-are-answerable"
    matrix = 'matrix = { platform = ["linux", "win32"] }\n'
    subject = _words_subject(
        tmp_path,
        monkeypatch,
        '[phases.typecheck.acme]\ntools = ["acme"]\nmain = ["acme"]\n' + matrix,
    )
    assert _names(subject, clause) == [
        "words-are-answerable: phases.typecheck.acme: varies platform in its"
        " matrix, which no word names; every call would run the same words"
    ]
    subject = _words_subject(
        tmp_path,
        monkeypatch,
        '[phases.typecheck.acme]\ntools = ["acme"]\n'
        'main = ["acme", "--platform={platform}"]\n' + matrix,
    )
    assert _names(subject, clause) == []


def test_a_plugin_no_distribution_declares_breaks_the_clause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "acme_kit_plugin"
    package.mkdir()
    declaration = package / "extension.toml"
    declaration.write_text('[extension]\nplugin = "acme.nowhere"\n')
    monkeypatch.syspath_prepend(str(tmp_path))
    clause = "plugin-is-an-entry-point"
    assert _names(Subject("acme_kit_plugin"), clause) == [
        "plugin-is-an-entry-point: extension acme_kit_plugin: names the plugin"
        " 'acme.nowhere', which no installed distribution declares in"
        " footman.tasks; the mount cannot mount its verbs"
    ]
    declaration.write_text('[extension]\nplugin = "livery.extensions.docs"\n')
    assert _names(Subject("acme_kit_plugin"), clause) == []


def test_a_contract_key_without_a_doc_breaks_the_clause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "acme_kit_keys"
    package.mkdir()
    declaration = package / "extension.toml"
    declaration.write_text('[contract.package.acme.depth]\ntypes = ["str"]\n')
    monkeypatch.syspath_prepend(str(tmp_path))
    clause = "contract-keys-documented"
    assert _names(Subject("acme_kit_keys"), clause) == [
        "contract-keys-documented: extension acme_kit_keys: declares the package"
        " contract key acme.depth with no doc; the editor shows a key by its doc,"
        " and so will the reference pages"
    ]
    declaration.write_text(
        '[contract.package.acme.depth]\ntypes = ["str"]\ndoc = "how deep acme reads"\n'
    )
    assert _names(Subject("acme_kit_keys"), clause) == []


def test_every_installed_extension_passes_the_declaration_clauses() -> None:
    from livery.footman import installed_entry_points

    packages = sorted(
        {
            entry.value.partition(":")[0]
            for entry in installed_entry_points("workshop.extensions")
        }
    )
    assert packages
    clauses = {
        "references-register-nothing",
        "entries-name-defined-tasks",
        "claims-name-categories",
        "words-are-answerable",
        "plugin-is-an-entry-point",
        "requirements-in-metadata",
        "contract-keys-documented",
    }
    found = [
        str(violation)
        for package in packages
        for violation in judge(Subject(package))
        if violation.clause in clauses
    ]
    assert found == []


def _tidy(text: str = "Checks: acme-*\n") -> Subject:
    """An extension whose step carries a .clang-tidy for its packages."""
    register_step(
        StepRecord(
            "acme-tidy",
            "lint",
            _idle,
            scope=PACKAGE,
            extensions=("acme.cpp",),
            extension=EXTENSION,
            fragments=(Fragment(".clang-tidy", text, extensions=("acme.cpp",)),),
        )
    )
    return Subject(EXTENSION, steps=(step_for("lint.acme-tidy"),))


TIDY = "step lint.acme-tidy .clang-tidy for acme.cpp"


def test_a_fragment_that_breaks_the_composed_file_or_does_not_render_breaks_the_drift(
    acme: None,
) -> None:
    subject = _tidy("{% if %}\n")
    register_step(
        StepRecord(
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
    subject = replace(subject, steps=(*subject.steps, step_for("lint.acme-table")))
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


def test_a_withdrawn_steps_file_kept_unedited_or_removed_edited_breaks_contract_11(
    acme: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _shipped_files

    subject = _tidy()
    assert _names(subject, "withdrawn-file") == []
    real = _shipped_files.settle_package

    def written(directory: Path) -> list[Path]:
        # The subject's own file, which its withdrawn step no longer names.
        return [p for p in directory.iterdir() if p.name == ".clang-tidy"]

    def never_again(directory: Path, extensions: tuple[str, ...]) -> list[str]:
        if written(directory):
            return []
        return real(directory, extensions)

    monkeypatch.setattr(_shipped_files, "settle_package", never_again)
    assert _names(subject, "withdrawn-file") == [
        f"withdrawn-file: {TIDY}: an unedited copy stays once the step is"
        " withdrawn; contract 11 removes it"
    ]

    def always_removes(directory: Path, extensions: tuple[str, ...]) -> list[str]:
        found = written(directory)
        for path in found:
            path.unlink()
        return [f"removed {path.name}" for path in found] or real(directory, extensions)

    monkeypatch.setattr(_shipped_files, "settle_package", always_removes)
    assert _names(subject, "withdrawn-file") == [
        f"withdrawn-file: {TIDY}: an edited copy is removed once the step is"
        " withdrawn; contract 11 keeps it as a local override"
    ]


def _walkers() -> Subject:
    """An extension with a step that fixes and one that judges, both reading python."""
    register_step(
        StepRecord(
            "acme-fixer",
            "format",
            _idle,
            fix=_idle,
            extension=EXTENSION,
            claims=(Claim("source", suffixes=(".py",)),),
        )
    )
    register_step(
        StepRecord(
            "acme-judge",
            "lint",
            _idle,
            scope=PACKAGE,
            extensions=("python",),
            extension=EXTENSION,
            claims=(Claim("source", suffixes=(".py",)),),
        )
    )
    steps = (step_for("format.acme-fixer"), step_for("lint.acme-judge"))
    return Subject(EXTENSION, sets=(("python",),), steps=steps)


def _walked_nothing(ctx: PhaseContext, **kwargs: object) -> None:
    """A gate walk that starts no step."""
    del ctx, kwargs


def test_a_step_registered_without_its_extension_breaks_the_gate_lines(
    acme: None,
) -> None:
    register_step(StepRecord("acme-quiet", "lint", _idle))
    found = _names(
        Subject(EXTENSION, steps=(step_for("lint.acme-quiet"),)), "gate-lines"
    )
    assert found == [
        "gate-lines: step lint.acme-quiet: names livery.workshop as its extension; the"
        " gate says who registered a step by it, and acme.extension registered this"
        " one"
    ]


def test_a_walk_that_starts_a_step_with_nothing_to_read_breaks_the_gate_lines(
    acme: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _quality

    subject = _walkers()
    assert _names(subject, "gate-lines") == []

    def careless(ctx: PhaseContext, **kwargs: object) -> None:
        only = cast("frozenset[str]", kwargs["only"])
        for name in sorted(only):
            _steps.step_for(name).run(ctx)

    monkeypatch.setattr(_quality, "walk", careless)
    assert _names(subject, "gate-lines") == [
        "gate-lines: step format.acme-fixer: the gate does not name acme.extension as"
        " the extension that registered it",
        "gate-lines: step format.acme-fixer: not named when it had no file to"
        ' read; the gate says "no file it reads" and starts nothing',
        "gate-lines: step format.acme-fixer: started with no file to read",
        "gate-lines: step lint.acme-judge: the gate does not name acme.extension as"
        " the extension that registered it",
        "gate-lines: step lint.acme-judge: not named when it had no file to read;"
        ' the gate says "no file it reads" and starts nothing',
        "gate-lines: step lint.acme-judge: started with no file to read",
    ]


def test_a_walk_that_judges_before_it_fixes_breaks_the_walk_order(
    acme: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _quality

    subject = _walkers()
    assert _names(subject, "walk-order") == []

    def backwards(ctx: PhaseContext, **kwargs: object) -> None:
        only = cast("frozenset[str]", kwargs["only"])
        for name in sorted(only):
            record = _steps.step_for(name)
            record.run(ctx)
        for name in sorted(only):
            record = _steps.step_for(name)
            if record.fix is not None:
                record.fix(ctx)

    monkeypatch.setattr(_quality, "walk", backwards)
    assert _names(subject, "walk-order") == [
        "walk-order: step format.acme-fixer: rewrote after a judge started; every"
        " fixer runs before any judge",
        "walk-order: step format.acme-fixer: judged after it rewrote; a step that"
        " rewrote is not judged again",
    ]

    monkeypatch.setattr(_quality, "walk", _walked_nothing)
    assert _names(subject, "walk-order") == [
        "walk-order: step format.acme-fixer: never rewrote under --fix",
        "walk-order: step lint.acme-judge: never judged under --fix",
    ]


# A package fragment goes by extension; the later composed extension wins a
# category tie.


def test_a_package_fragment_renders_for_the_extensions_a_package_holds(
    acme: None,
) -> None:
    for name, extension in (("acme-c-tidy", "acme.c"), ("acme-cpp-tidy", "acme.cpp")):
        register_step(
            StepRecord(
                name,
                "lint",
                _idle,
                scope=PACKAGE,
                extensions=(extension,),
                extension=EXTENSION,
                fragments=(
                    Fragment(
                        ".clang-tidy", f"# {extension}\n", extensions=(extension,)
                    ),
                ),
            )
        )
    assert package_fragment(("acme.cpp",), ".clang-tidy") == (
        "# acme.cpp\n",
        "lint.acme-cpp-tidy",
    )
    # A package holding both takes the first step by name.
    assert package_fragment(("acme.c", "acme.cpp"), ".clang-tidy") == (
        "# acme.c\n",
        "lint.acme-c-tidy",
    )
    assert package_fragment(("acme.rust",), ".clang-tidy") is None
    steps = (step_for("lint.acme-c-tidy"), step_for("lint.acme-cpp-tidy"))
    assert _names(Subject(EXTENSION, steps=steps), "fragment-owner") == []


def test_the_later_composed_extension_s_rule_wins_a_tie_between_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from workshop_extension_fakes import fake_package_extensions

    declared = '[extension]\nlevels = ["package"]\n\n[categories]\n{} = ["gen/**"]\n'
    fake_package_extensions(
        tmp_path,
        monkeypatch,
        early=declared.format("generated"),
        late=declared.format("source"),
    )
    listed = ("acme.early", "acme.late")
    found = _categories.category_of(_checks_package(*listed), "gen/a")
    assert (found.name, found.supplier) == ("source", "acme.late")
    # The kit's probe of a package listing the two finds the same answer.
    assert _names(Subject(EXTENSION, sets=(listed,)), "category-table") == []
    # The rules come nearest table first: the later composed extension's.
    rules = _categories.category_rules(("acme.early", "acme.late"))
    assert rules[0].table == "acme.late"


def _checks_package(*extensions: str) -> Package:
    return Package(Path("."), "packages/probe", "probe", (), extensions=extensions)


# Then the builtins, and the kit's own shape.


def test_the_builtin_steps_pass_every_clause() -> None:
    subject = builtin_subject()
    # A package that lists nothing, and one listing each package-level
    # extension the workshop's wheel ships.
    assert set(subject.sets) >= {
        (),
        ("cmake",),
        ("conan",),
        ("cpp",),
        ("nanobind",),
        ("python",),
    }
    assert {record.name for record in subject.steps} == {
        "drift.check",
        "provenance.check",
        "layering.graph",
        "layering.base-imports-no-extension",
        "layering.sibling-references",
    }
    assert judge(subject) == []


def test_the_clauses_are_named_once_and_state_their_rule() -> None:
    names = [clause.name for clause in CLAUSES]
    assert names == [
        "fragment-owner",
        "category-table",
        "step-order",
        "declaration-validates",
        "references-register-nothing",
        "entries-name-defined-tasks",
        "claims-name-categories",
        "words-are-answerable",
        "plugin-is-an-entry-point",
        "requirements-in-metadata",
        "contract-keys-documented",
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
