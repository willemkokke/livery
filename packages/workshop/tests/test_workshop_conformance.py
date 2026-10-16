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
from workshop_installed import skip_unless_installed

EXTENSION = "acme.extension"


@pytest.fixture
def acme() -> Iterator[None]:
    """Restore the steps a test registers."""
    steps = _steps.snapshot()
    yield
    _steps.restore(steps)


def _idle(ctx: PhaseContext) -> None:
    del ctx


#: An extension whose one table claims ``lib/x.py`` twice at
#: one specificity, for different categories.
TIED = '[extension]\n\n[categories]\nsource = ["lib/*.py"]\ntest = ["lib/x*.p*"]\n'


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
    skip_unless_installed("cmake")
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
        "  [extension] has no key 'level': it takes api-version, description,"
        " dormant-until, plugin, requires; the mount refuses the extension"
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


def test_a_declaration_is_judged_under_the_name_its_distribution_lists_it_by(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The mount reads an installed extension's file under the name its
    # entry point declares, and so does the kit: a phase the file
    # declares is the extension's own, never another extension's.
    from importlib.metadata import EntryPoint

    from livery.footman import (
        _entries,  # pyright: ignore[reportPrivateUsage]
        installed_entry_points,
    )
    from workshop_extension_fakes import read_afresh

    package = tmp_path / "acme_kit_phased"
    package.mkdir()
    (package / "extension.toml").write_text(
        "[extension]\napi-version = 1\n\n[phases.acme-site]\n"
        'level = "workspace"\ngated = false\nverb = false\n'
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    scanned = installed_entry_points()
    entry = EntryPoint("phased", "acme_kit_phased", "workshop.extensions")
    monkeypatch.setattr(_entries, "_scan", lambda: (*scanned, entry))
    monkeypatch.setattr(_entries, "_SCAN", None)
    read_afresh(monkeypatch)
    assert _names(Subject("acme_kit_phased"), "declaration-validates") == []


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
    # The workshop's wheel declares the base's own plugin.
    declaration.write_text('[extension]\nplugin = "livery.workshop"\n')
    assert _names(Subject("acme_kit_plugin"), clause) == []


def test_an_item_without_its_doc_line_breaks_the_clause_naming_each(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "acme_kit_docs"
    package.mkdir()
    (package / "_steps.py").write_text("def judged(ctx):\n    del ctx\n")
    (package / "_verbs.py").write_text(
        "from livery.footman import group\n\n"
        'acme = group("acme")\n\n\n'
        '@acme.task(name="build")\n'
        "def acme_build() -> None: ...\n\n\n"
        '@acme.task(name="serve")\n'
        "def acme_serve() -> None:\n"
        '    """Serve the acme site."""\n'
    )
    declaration = package / "extension.toml"
    declaration.write_text(
        '[extension]\nplugin = "acme_kit_docs"\n\n'
        '[contract.package.acme.depth]\ntypes = ["str"]\n\n'
        "[templates.acme-app]\nextensions = []\n\n"
        '[templates.acme-app.options.flavour]\nchoices = ["sweet", "sour"]\n\n'
        '[phases.lint.acme]\nmain = "acme_kit_docs._steps:judged"\n\n'
        '[phases.lint.acme.options.strict]\ntype = "bool"\ndefault = false\n\n'
        '[for.docs.phases.lint.acme-pages]\nmain = "acme_kit_docs._steps:judged"\n\n'
        '[for.docs.phases.lint.acme-pages.options.depth]\ntype = "int"\n'
        "default = 1\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    clause = "declaration-documented"
    # Each item without its doc line is named, the template's among them
    # though the mount refuses that declaration for it already.
    assert _names(Subject("acme_kit_docs"), clause) == [
        "declaration-documented: extension acme_kit_docs: declares no [extension]"
        " description; the agent's guidance and the site say what the extension"
        " does from it",
        "declaration-documented: extension acme_kit_docs: defines the verb"
        " acme.build with no docstring; its help, the agent's guidance and the"
        " site show the verb by the docstring's first line",
        "declaration-documented: extension acme_kit_docs: declares the package"
        " contract key acme.depth with no doc; the editor, the agent's guidance"
        " and the site show a key by its doc",
        "declaration-documented: extension acme_kit_docs: declares the template"
        " acme-app with no doc; fm new.package.acme-app, the agent's guidance and"
        " the site show a template by its doc",
        "declaration-documented: extension acme_kit_docs: declares the option"
        " flavour of the template acme-app with no doc; fm new.package.acme-app's"
        " help and completion, the agent's guidance and the site show an option"
        " by its doc",
        "declaration-documented: extension acme_kit_docs: declares the option"
        " strict of [phases.lint.acme] with no doc; the agent's guidance and the"
        " site show an option by its doc",
        "declaration-documented: extension acme_kit_docs: declares the option"
        " depth of [for.docs.phases.lint.acme-pages] with no doc; the agent's"
        " guidance and the site show an option by its doc",
    ]
    # A blank doc line is no doc line.
    declaration.write_text('[extension]\ndescription = "  "\n')
    assert _names(Subject("acme_kit_docs"), clause) == [
        "declaration-documented: extension acme_kit_docs: declares no [extension]"
        " description; the agent's guidance and the site say what the extension"
        " does from it",
    ]
    # Without a plugin the tasks beside the file are no verbs of it.
    declaration.write_text('[extension]\ndescription = "Acme\'s checks."\n')
    assert _names(Subject("acme_kit_docs"), clause) == []
    (package / "_verbs.py").write_text(
        "from livery.footman import group\n\n"
        'acme = group("acme")\n\n\n'
        '@acme.task(name="build")\n'
        "def acme_build() -> None:\n"
        '    """Build the acme site."""\n'
    )
    declaration.write_text(
        '[extension]\ndescription = "Acme\'s checks."\nplugin = "acme_kit_docs"\n\n'
        '[contract.package.acme.depth]\ntypes = ["str"]\ndoc = "how deep acme'
        ' reads"\n\n'
        '[templates.acme-app]\ndoc = "an acme app"\nextensions = []\n\n'
        '[phases.lint.acme]\nmain = "acme_kit_docs._steps:judged"\n\n'
        '[phases.lint.acme.options.strict]\ntype = "bool"\ndefault = false\n'
        'doc = "judge every rule"\n'
    )
    assert _names(Subject("acme_kit_docs"), clause) == []


def test_a_template_the_kit_cannot_judge_breaks_the_clause_saying_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from workshop_extension_fakes import fake_extensions

    lua = (
        '[templates.lua-app]\ndoc = "a lua app"\nextensions = []\n'
        'extends = "lua-base"\n\n'
        '[templates.lua-rock]\ndoc = "a lua rock"\nextensions = ["acme.broken"]\n'
    )
    fake_extensions(
        tmp_path, monkeypatch, lua=lua, broken="[extension]\nbogus = true\n"
    )
    clause = "template-composes"
    broken = tmp_path / "site" / "acme" / "broken" / "extension.toml"
    # A chain that does not resolve, and a list naming an extension whose
    # declaration refuses: each names the refusal.
    app, rock = _names(Subject("acme.lua"), clause)
    assert app.startswith(
        "template-composes: template lua-app: the template lua-app extends"
        " lua-base, which no installed extension declares; the installed"
        " templates are:\n"
    )
    assert rock.startswith(f"template-composes: template lua-rock: {broken}:\n")
    # Two extensions declaring one template leave no template to judge.
    fake_extensions(
        tmp_path,
        monkeypatch,
        lua=lua,
        rocks='[templates.lua-app]\ndoc = "a lua app"\nextensions = []\n',
    )
    assert _names(Subject("acme.lua"), clause) == [
        "template-composes: extension acme.lua: the template lua-app is declared by"
        " both acme.lua and acme.rocks; one extension declares a template, so one"
        " of them renames it"
    ]


def test_the_kit_refuses_a_template_whose_list_composes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from workshop_extension_fakes import fake_extensions

    templates = (
        '[templates.lua-module]\ndoc = "a lua module"\n'
        'extensions = ["acme.lua", "acme.site"]\n\n'
        '[templates.lua-app]\ndoc = "a lua app"\nextensions = ["acme.site"]\n'
        'extends = "lua-module"\n\n'
        '[templates.lua-rock]\ndoc = "a lua rock"\nextensions = ["acme.ghost"]\n'
        'extends = "lua-module"\n'
    )
    # acme.lua's build step composes something in a package; acme.site's
    # lint step does not, and nothing installed declares acme.ghost.
    lua = '[phases.build.lua]\nmain = "{package}._steps:noop"\n\n'
    fake_extensions(
        tmp_path,
        monkeypatch,
        lua=lua + templates,
        site='[phases.lint.site]\nmain = "{package}._steps:noop"\n',
    )
    clause = "template-composes"

    def inert(name: str) -> str:
        return (
            "lists acme.site, which composes nothing in a package: its steps judge"
            f" the workspace as a whole; fm new.package.{name} refuses to create a"
            " package from it"
        )

    def outside(name: str) -> str:
        return (
            f"its chain needs {name}, which the set of its extensions does not"
            " hold; a package born from it holds that set alone, so no step that"
            f" names {name} judges it"
        )

    # lua-module's list holds what its chain needs; the templates
    # extending it list less than lua-module does.
    assert _names(Subject("acme.lua"), clause) == [
        f"template-composes: template lua-app: {inert('lua-app')}",
        f"template-composes: template lua-app: {outside('acme.lua')}",
        f"template-composes: template lua-module: {inert('lua-module')}",
        "template-composes: template lua-rock: lists acme.ghost, which no installed"
        " distribution declares in workshop.extensions, so nothing shows that it"
        " composes something in a package",
        f"template-composes: template lua-rock: {outside('acme.lua')}",
        f"template-composes: template lua-rock: {outside('acme.site')}",
    ]
    # A list of what composes, whose set holds what its chain needs.
    declaration = tmp_path / "site" / "acme" / "lua" / "extension.toml"
    declaration.write_text(
        lua
        + '[templates.lua-module]\ndoc = "a lua module"\nextensions = ["acme.lua"]\n'
        + '\n[templates.lua-app]\ndoc = "a lua app"\nextensions = ["acme.lua"]\n'
        + 'extends = "lua-module"\n'
    )
    assert _names(Subject("acme.lua"), clause) == []


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
        "declaration-validates",
        "references-register-nothing",
        "entries-name-defined-tasks",
        "claims-name-categories",
        "words-are-answerable",
        "plugin-is-an-entry-point",
        "requirements-in-metadata",
        "declaration-documented",
    }
    found = [
        str(violation)
        for package in packages
        for violation in judge(Subject(package))
        if violation.clause in clauses
    ]
    assert found == []


def test_every_template_here_composes() -> None:
    # conan's C++ templates list cmake.
    skip_unless_installed("cmake", "nanobind")
    from livery.footman import installed_entry_points
    from livery.workshop._extensions import extension_package
    from livery.workshop._seeds import available_templates
    from livery.workshop._steps import BASE_EXTENSION

    subjects = sorted(
        {
            BASE_EXTENSION,
            *(
                entry.value.partition(":")[0]
                for entry in installed_entry_points("workshop.extensions")
            ),
        }
    )
    templates = available_templates()
    assert set(templates) >= {
        "cpp-package",
        "nanobind-module",
        "python-library",
        "workshop-extension",
    }
    # Each template's declarer is a subject, so the clause judges every one.
    assert {extension_package(t.extension) for t in templates.values()} <= set(subjects)
    (clause,) = [item for item in CLAUSES if item.name == "template-composes"]
    found = [
        str(item) for subject in subjects for item in clause.judge(Subject(subject))
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


def test_a_step_named_by_the_name_its_package_is_listed_by_keeps_the_gate_lines(
    acme: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A contract lists an installed extension by its entry point's name,
    # which names the package the subject is: the step carries that name.
    monkeypatch.setattr(
        "livery.workshop._extensions.extension_package",
        lambda name: EXTENSION if name == "acme" else name,
    )
    register_step(StepRecord("acme-listed", "lint", _idle, extension="acme"))
    register_step(StepRecord("acme-other", "lint", _idle, extension="acme.other"))
    steps = (step_for("lint.acme-listed"), step_for("lint.acme-other"))
    found = _names(Subject(EXTENSION, steps=steps), "gate-lines")
    assert [
        line for line in found if "names" in line and "as its extension" in line
    ] == [
        "gate-lines: step lint.acme-other: names acme.other as its extension; the"
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


def test_a_step_of_a_phase_the_gate_never_runs_is_judged_by_no_walk_clause(
    acme: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _quality

    # The gate walks nothing: the lint step breaks both clauses, and the
    # sync step, of a phase fm check never runs, breaks neither.
    monkeypatch.setattr(_quality, "walk", _walked_nothing)
    subject = _walkers()
    register_step(StepRecord("acme-hooks", "sync", _idle, extension=EXTENSION))
    hooked = replace(subject, steps=(*subject.steps, step_for("sync.acme-hooks")))
    assert [line for line in _names(hooked, "walk-order") if "sync." in line] == []
    assert [line for line in _names(hooked, "gate-lines") if "sync." in line] == []
    assert _names(hooked, "walk-order") == _names(subject, "walk-order") != []


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

    declared = '[extension]\n\n[categories]\n{} = ["gen/**"]\n'
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
    from livery.workshop._extensions import composes_per_package, installed_extensions

    subject = builtin_subject()
    # A package that lists nothing, and one listing each installed
    # extension that composes something in a package: those the
    # workshop's wheel ships, and those of their own distributions
    # installed here, such as cmake and nanobind.
    composing = {
        (name,) for name in installed_extensions() if composes_per_package(name)
    }
    assert composing >= {("conan",), ("cpp",), ("python",)}
    assert set(subject.sets) == {(), *composing}
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
        "declaration-documented",
        "template-composes",
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
