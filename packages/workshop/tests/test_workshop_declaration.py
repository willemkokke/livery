"""An extension.toml, read without importing the extension: refusals, then the read."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from livery.workshop._declaration import (
    Additions,
    CodedFix,
    Declaration,
    DeclarationError,
    Reference,
    read,
)

BODIES = """\
def judge(ctx):
    del ctx


def mend(ctx):
    del ctx
"""


@pytest.fixture
def package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """An importable package ``acme_declared`` with check bodies and no declaration."""
    directory = tmp_path / "site" / "acme_declared"
    directory.mkdir(parents=True)
    (directory / "_checks.py").write_text(BODIES)
    monkeypatch.syspath_prepend(str(tmp_path / "site"))
    importlib.invalidate_caches()
    yield directory
    sys.modules.pop("acme_declared._checks", None)
    sys.modules.pop("acme_declared", None)


def _declare(package: Path, text: str) -> Declaration:
    (package / "extension.toml").write_text(text)
    found = read("acme", "acme_declared")
    assert found is not None
    return found


def _refusal(package: Path, text: str) -> str:
    (package / "extension.toml").write_text(text)
    with pytest.raises(DeclarationError) as caught:
        read("acme", "acme_declared")
    return str(caught.value)


CHECK = '[checks.acme.lint]\nrun = "acme_declared._checks:judge"\n'


# The refusals first.


def test_an_unknown_key_in_extension_toml_refuses_naming_the_file_and_the_nearest(
    package: Path,
) -> None:
    refused = _refusal(package, '[extension]\nlevel = ["workspace"]\n')
    assert refused == (
        f"{package / 'extension.toml'}:\n"
        "  [extension] has no key 'level': it takes api-version, levels, plugin,"
        " requires; did you mean 'levels'?"
    )
    refused = _refusal(package, CHECK + 'narowing = "paths"\n')
    assert "[checks.acme.lint] has no key 'narowing'" in refused
    assert "did you mean 'narrowing'?" in refused


def test_the_old_tools_table_in_extension_toml_refuses_naming_toolroom(
    package: Path,
) -> None:
    refused = _refusal(package, '[tools]\nrequires = ["docker>=27"]\n')
    assert "the top level has no key 'tools'" in refused
    assert "did you mean 'toolroom'?" in refused


def test_a_value_of_the_wrong_type_or_outside_its_set_refuses(package: Path) -> None:
    assert "extension.api-version is a string ('1'); it takes an integer" in _refusal(
        package, '[extension]\napi-version = "1"\n'
    )
    refused = _refusal(package, CHECK + 'scope = "packages"\n')
    assert (
        "checks.acme.lint.scope is 'packages'; it takes one of workspace, package"
        in (refused)
    )
    assert "did you mean 'package'?" in refused
    assert "checks.acme is a string" in _refusal(package, '[checks]\nacme = "lint"\n')


def test_a_key_spelled_with_an_underscore_refuses_naming_its_spelling(
    package: Path,
) -> None:
    refused = _refusal(package, "[extension]\napi_version = 1\n")
    assert refused == (
        f"{package / 'extension.toml'}: keys are kebab-case; found"
        " extension.api_version (spell it api-version)"
    )
    # A name the author chooses is data: a fragment's file, a slot, a
    # shipped file's reference.
    _declare(
        package,
        CHECK
        + '[checks.acme.lint.fragments]\n".vscode/settings.json" = "{}"\n'
        + '[replaces]\n"acme.base:root/my_file.txt" = "ours"\n',
    )


def test_a_reference_that_does_not_import_refuses_at_mount(package: Path) -> None:
    where = package / "extension.toml"
    assert _refusal(package, CHECK.replace(":judge", ":jduge")) == (
        f"{where}: checks.acme.lint.run names jduge, which acme_declared._checks"
        " does not define at its top level; did you mean 'judge'?"
    )
    assert _refusal(package, CHECK.replace("._checks:", "._chekcs:")) == (
        f"{where}: checks.acme.lint.run names acme_declared._chekcs, which"
        " acme_declared does not contain"
    )
    assert _refusal(package, CHECK.replace("acme_declared._checks", "json")) == (
        f"{where}: checks.acme.lint.run names json, outside the extension's package"
        " acme_declared; a reference names the extension's own code"
    )
    assert "a reference is 'module:function'" in _refusal(
        package, CHECK.replace(":judge", "")
    )
    # Read from the source: nothing of the extension was imported.
    assert "acme_declared._checks" not in sys.modules


def test_a_check_with_both_judge_and_run_refuses_naming_the_check(
    package: Path,
) -> None:
    where = package / "extension.toml"
    assert _refusal(package, CHECK + 'judge = ["acme", "--check"]\n') == (
        f"{where}: checks.acme.lint declares both judge and run: a check is its"
        " tool's words or a reference to its code, never both"
    )


def test_words_and_code_do_not_mix_and_words_name_one_tool(package: Path) -> None:
    where = package / "extension.toml"
    words = '[checks.acme.lint]\ntools = ["acme"]\njudge = ["acme", "--check"]\n'
    # A check in words fixes in words; one that runs code takes no words.
    assert _refusal(package, words + 'fix = "acme_declared._checks:mend"\n') == (
        f"{where}: checks.acme.lint.fix is 'acme_declared._checks:mend', a"
        " reference to code; in a check in words, fix is words: the tool's name,"
        " then its arguments"
    )
    assert _refusal(package, CHECK + 'fix = ["acme", "--fix"]\n') == (
        f"{where}: checks.acme.lint.fix is words, and run is a reference to code:"
        " a check that runs code fixes with code, module:function"
    )
    assert _refusal(package, CHECK + 'matrix = { platform = ["linux"] }\n') == (
        f"{where}: checks.acme.lint.matrix belongs to a check in words, and this"
        " one runs code; declare judge instead of run, or drop the key"
    )
    # Words name their tool first, every mode the same one.
    assert _refusal(package, "[checks.acme.lint]\njudge = []\n") == (
        f"{where}: checks.acme.lint.judge is empty: the tool's name comes first"
    )
    assert _refusal(package, words + 'fix = ["other", "--fix"]\n') == (
        f"{where}: checks.acme.lint.fix runs other, and judge runs acme: a check"
        " runs one tool"
    )
    assert _refusal(package, words + 'safe-fix = ["acme", "--safe"]\n') == (
        f"{where}: checks.acme.lint.safe-fix is declared without fix: a check that"
        " fixes safely fixes too"
    )
    assert _refusal(package, words + "matrix = { platform = [] }\n") == (
        f"{where}: checks.acme.lint.matrix.platform has no values: a matrix key"
        " runs one call per value"
    )


def test_a_check_in_words_reads_its_words_and_imports_nothing(package: Path) -> None:
    found = _declare(
        package,
        '[checks.acme.typecheck]\ntools = ["acme"]\narguments = true\n'
        'judge = ["acme", "--platform={platform}"]\n'
        'fix = ["acme", "--fix"]\nsafe-fix = ["acme", "--fix", "--safe"]\n'
        'env = { ACME_COLOR = "never" }\n'
        'matrix = { platform = ["linux", "win32"] }\n',
    )
    (record,) = found.additions.checks
    assert record.words is not None
    assert record.words.judge == ("acme", "--platform={platform}")
    assert record.words.env == (("ACME_COLOR", "never"),)
    assert record.words.matrix == (("platform", ("linux", "win32")),)
    assert str(record.run) == "acme --platform={platform}"
    assert record.fix is not None and str(record.fix) == "acme --fix"
    assert "acme_declared._checks" not in sys.modules


def test_a_check_without_run_or_with_an_undeclared_option_refuses(
    package: Path,
) -> None:
    where = package / "extension.toml"
    assert _refusal(package, "[checks.acme.lint]\narguments = true\n") == (
        f"{where}: checks.acme.lint names neither judge nor run: a check is its"
        " tool's words, judge, or a reference to its code, run"
    )
    assert _refusal(package, CHECK + 'listed-with = "deep"\n') == (
        f"{where}: checks.acme.lint.listed-with is 'deep', which [options] does not"
        " declare; its options are none"
    )
    assert _refusal(package, CHECK + 'claims = [{ suffixes = [".py"] }]\n') == (
        f"{where}: checks.acme.lint.claims[0] names no category: a claim has one"
    )
    assert "is a package file's fragment" in _refusal(
        package,
        CHECK + '[checks.acme.lint.fragments.".clang-tidy"]\ntext = "x"\n',
    )


STEP = '[phases.lint.acme]\nmain = "acme_declared._checks:judge"\n'
WORDS_STEP = (
    '[phases.lint.acme]\ntools = ["acme"]\nmain = ["acme", "check", "{paths}"]\n'
)


def test_a_removed_key_refuses_naming_the_new_spelling(package: Path) -> None:
    where = package / "extension.toml"
    said = {
        'narrowing = "paths"': "is said by {paths} in main",
        'transport = "argv"': "the paths go on the command line, where {paths}",
        "threshold = 0.5": "a template with {paths} runs over the paths",
        'roles = ["format"]': "a step belongs to one phase",
        'listed-with = "deep"': "is enabled = false on the step, which a workspace"
        " turns on with [phases.lint.acme] enabled = true in its root contract",
        'judge = ["acme"]': "is main: the step's body, a template or a reference",
        'run = "acme_declared._checks:judge"': "is main: the step's body",
        "arguments = true": "is said by {arguments} in main, where the words after"
        " -- go",
        'pre = "acme_declared._checks:judge"': "a step that runs after it names"
        " it in after",
        'post = "acme_declared._checks:judge"': "does its own cleanup on failure",
    }
    for line, spelling in said.items():
        key = line.partition(" = ")[0]
        refused = _refusal(package, STEP + line + "\n")
        assert refused.startswith(f"{where}: phases.lint.acme.{key} "), key
        assert spelling in refused, key
    # The refusal comes before the contract's, which cannot name the spelling.
    assert "has no key" not in _refusal(package, STEP + 'narrowing = "paths"\n')


def test_a_step_reads_its_body_in_either_shape(package: Path) -> None:
    where = package / "extension.toml"
    # The refusals: no body, a body of the wrong kind for its phase, a
    # fix in the other shape, and a template's keys on a reference.
    assert _refusal(package, '[phases.lint.acme]\ntools = ["acme"]\n') == (
        f"{where}: phases.lint.acme names no main: a step's body is a template,"
        " the tool's name then its arguments, or a reference to its code,"
        " module:function"
    )
    assert _refusal(package, "[phases.lint.acme]\nmain = []\n") == (
        f"{where}: phases.lint.acme.main is empty: the tool's name comes first"
    )
    assert _refusal(package, WORDS_STEP + 'fix = "acme_declared._checks:mend"\n') == (
        f"{where}: phases.lint.acme.fix is 'acme_declared._checks:mend', a"
        " reference to code; in a step in words, fix is words: the tool's name,"
        " then its arguments"
    )
    assert _refusal(package, STEP + 'fix = ["acme", "--fix"]\n') == (
        f"{where}: phases.lint.acme.fix is words, and main is a reference to code:"
        " a step that runs code fixes with code, module:function"
    )
    assert _refusal(package, STEP + 'safe-fix = "acme_declared._checks:mend"\n') == (
        f"{where}: phases.lint.acme.safe-fix is declared without fix: a step that"
        " fixes safely fixes too"
    )
    assert _refusal(package, STEP + 'env = { ACME_COLOR = "never" }\n') == (
        f"{where}: phases.lint.acme.env belongs to a step in words, and this one"
        " runs code; declare main as words, the tool's name then its arguments,"
        " or drop the key"
    )
    assert _refusal(package, WORDS_STEP + 'fix = ["acme", "--files={paths}"]\n') == (
        f"{where}: phases.lint.acme.fix names {{paths}} in '--files={{paths}}';"
        " it stands alone as one word, where the run's paths go"
    )
    # A template: {paths} narrows by the paths the run reaches, and the
    # fix and safe-fix are templates too.
    found = _declare(
        package,
        WORDS_STEP + 'fix = ["acme", "fix", "{paths}"]\n'
        'safe-fix = ["acme", "fix", "--safe", "{paths}"]\n'
        'env = { ACME_COLOR = "never" }\nmatrix = { platform = ["linux", "win32"] }\n',
    )
    (record,) = found.additions.checks
    assert (record.name, record.phase, record.step, record.placed) == (
        "lint.acme",
        "lint",
        "acme",
        True,
    )
    assert record.words is not None
    assert record.words.judge == ("acme", "check", "{paths}")
    assert record.words.safe_fix == ("acme", "fix", "--safe", "{paths}")
    assert record.words.env == (("ACME_COLOR", "never"),)
    assert record.words.matrix == (("platform", ("linux", "win32")),)
    assert str(record.run) == "acme check {paths}"
    assert record.fix is not None and str(record.fix) == "acme fix {paths}"
    assert (record.narrowing, record.arguments) == ("paths", False)
    assert "acme_declared._checks" not in sys.modules
    # A template without {paths} runs whole, and {arguments} takes the
    # words after --.
    found = _declare(
        package,
        '[phases.lint.acme]\ntools = ["acme"]\nmain = ["acme", "{arguments}"]\n',
    )
    (record,) = found.additions.checks
    assert (record.narrowing, record.arguments) == ("none", True)
    # A reference: its fix and safe-fix are references, the safe one
    # run under --safe-fix.
    found = _declare(
        package,
        STEP + 'fix = "acme_declared._checks:mend"\n'
        'safe-fix = "acme_declared._checks:judge"\n',
    )
    (record,) = found.additions.checks
    assert (str(record.run), record.words, record.narrowing, record.placed) == (
        "acme_declared._checks:judge",
        None,
        "none",
        True,
    )
    assert isinstance(record.fix, CodedFix)
    assert (str(record.fix.fix), str(record.fix.safe_fix)) == (
        "acme_declared._checks:mend",
        "acme_declared._checks:judge",
    )
    found = _declare(package, STEP + 'fix = "acme_declared._checks:mend"\n')
    (record,) = found.additions.checks
    assert isinstance(record.fix, Reference)


def test_a_steps_own_keys_refuse_off_their_shape(package: Path) -> None:
    where = package / "extension.toml"
    assert _refusal(
        package, STEP + 'provides = { wheel = "path" }\nreads = ["wheel"]\n'
    ) == (
        f"{where}: phases.lint.acme.reads names wheel, which its provides names"
        " too; a step reads what another step provides"
    )
    assert _refusal(package, STEP + 'replaces = { "lint.acme" = "faster" }\n') == (
        f"{where}: phases.lint.acme.replaces names 'lint.acme'; a step of another"
        " extension is <owner>:<phase>.<step>"
    )
    assert _refusal(package, STEP + 'deletes = { "acme.base:lint" = "gone" }\n') == (
        f"{where}: phases.lint.acme.deletes names 'acme.base:lint'; a step of"
        " another extension is <owner>:<phase>.<step>"
    )
    assert _refusal(package, STEP + 'replaces = { "acme.base:lint.acme" = " " }\n') == (
        f'{where}: phases.lint.acme.replaces."acme.base:lint.acme" states no reason;'
        " a step names another with the reason it takes its place"
    )
    # A gate phase's table holds steps alone.
    assert _refusal(package, STEP + '[phases.lint]\nmain = "x"\n') == (
        f"{where}: phases.lint.main is not a step, and lint is not a lifecycle"
        " phase; its table holds steps, [phases.lint.<step>], each with a main"
    )


def test_both_spellings_read_into_one_record(package: Path) -> None:
    # Every key the two tables share, in one text each; the step's own
    # keys beside them. The read judges the text against the contract,
    # so the keys the step table declares pass it.
    shared = (
        'scope = "workspace"\nextensions = ["acme-kind"]\ntools = ["acme"]\n'
        'after = ["lint.other"]\ntests-only = true\nflags = ["point"]\n'
        'editor-extension = "acme.vscode"\n'
        'claims = [{ category = "source", ignore = ["X1"], suffixes = [".py"] }]\n'
        'inputs = { reads = ["docs/**/*.md"], widens = ["mkdocs.yml"],'
        ' on-removal = true, widen = "acme_declared._checks:mend",'
        ' ignores = ["docs/README.md"], per-file = false }\n'
    )
    tables = (
        '[{table}.options.strict]\ntype = "bool"\ndefault = false\n'
        'doc = "refuses warnings too"\n'
        '[{table}.fragments.".acme"]\nextensions = ["acme-kind"]\n'
        'text = "strict: true\\n"\n'
    )
    old = _declare(
        package,
        '[checks.acme.lint]\nrun = "acme_declared._checks:judge"\n'
        'fix = "acme_declared._checks:mend"\n'
        + shared
        + tables.format(table="checks.acme.lint"),
    )
    new = _declare(
        package,
        STEP
        + 'fix = "acme_declared._checks:mend"\n'
        + shared
        + 'enabled = false\nisolated = true\nreads = ["wheel"]\n'
        'replaces = { "acme.base:lint.acme" = "it reads faster" }\n'
        'deletes = { "acme.base:lint.old" = "it reads nothing" }\n'
        '[phases.lint.acme.provides]\nreport = "path"\n'
        + tables.format(table="phases.lint.acme"),
    )
    (before,) = old.additions.checks
    (after,) = new.additions.checks
    assert (after.name, after.placed, before.placed) == ("lint.acme", True, False)
    assert (
        after.enabled,
        after.isolated,
        after.reads,
        after.provides,
        after.replaces,
        after.deletes,
    ) == (
        False,
        True,
        ("wheel",),
        (("report", "path"),),
        (("acme.base:lint.acme", "it reads faster"),),
        (("acme.base:lint.old", "it reads nothing"),),
    )
    assert after.inputs is not None and after.inputs.per_file is False
    assert (
        replace(
            after,
            placed=False,
            enabled=True,
            isolated=None,
            reads=(),
            provides=(),
            replaces=(),
            deletes=(),
        )
        == before
    )


def test_a_lifecycle_phases_step_reads_as_its_main_and_two_refuse(
    package: Path,
) -> None:
    where = package / "extension.toml"
    identity = '[extension]\nlevels = ["package"]\n'
    # A lifecycle phase's step runs code, one per extension, and never
    # beside the phase table's own main.
    assert _refusal(
        package, identity + '[phases.build.acme]\nmain = ["acme", "build"]\n'
    ) == (
        f"{where}: phases.build.acme.main is words, and build is a lifecycle"
        " phase, whose steps run code; main is a reference, module:function"
    )
    assert _refusal(package, identity + '[phases.build.acme]\nreads = ["epoch"]\n') == (
        f"{where}: phases.build.acme names no main: a lifecycle phase's step is a"
        " reference to its code, module:function"
    )
    assert _refusal(
        package,
        identity + '[phases.build.acme]\nmain = "acme_declared._checks:judge"\n'
        '[phases.build.other]\nmain = "acme_declared._checks:mend"\n',
    ) == (
        f"{where}: phases.build.other is a second step of the build phase, beside"
        " acme; an extension declares one step per lifecycle phase, since the"
        " phase's walk runs one main per extension"
    )
    assert _refusal(
        package,
        identity + '[phases.build]\nmain = "acme_declared._checks:judge"\n'
        '[phases.build.acme]\nmain = "acme_declared._checks:mend"\n',
    ) == (
        f"{where}: phases.build names main beside its step acme; a phase's body"
        " is its step's, [phases.build.<step>] main, or its own, never both"
    )
    assert _refusal(
        package,
        identity + '[phases.build.acme]\nmain = "acme_declared._checks:judge"\n'
        'scope = "package"\n',
    ) == (
        f"{where}: phases.build.acme.scope is not read on a lifecycle phase's"
        " step, which takes main, provides and reads"
    )
    # The step reads as the phase table's main does, and registers no check.
    found = _declare(
        package,
        identity + '[phases.build.acme]\nmain = "acme_declared._checks:judge"\n'
        'provides = { wheel = "path" }\nreads = ["epoch"]\n',
    )
    assert found.additions.checks == ()
    phase = found.phases["build"]
    assert (str(phase.main), phase.pre, phase.post) == (
        "acme_declared._checks:judge",
        None,
        None,
    )
    assert (phase.provides, phase.reads) == ({"wheel": "path"}, ("epoch",))
    assert "acme_declared._checks" not in sys.modules


def test_a_checks_inputs_name_their_reads_and_a_widen_that_resolves(
    package: Path,
) -> None:
    where = package / "extension.toml"
    assert _refusal(package, CHECK + "inputs = { on-removal = true }\n") == (
        f"{where}: checks.acme.lint.inputs names no reads: inputs name the files"
        " it reads"
    )
    assert "names gone, which acme_declared._checks does not define" in _refusal(
        package,
        CHECK
        + 'inputs = { reads = ["**/*.md"], widen = "acme_declared._checks:gone" }\n',
    )
    found = _declare(
        package,
        CHECK + 'inputs = { reads = ["docs/**/*.md"], widens = ["mkdocs.yml"],'
        ' on-removal = true, widen = "acme_declared._checks:mend",'
        ' ignores = ["docs/README.md"] }\n',
    )
    (record,) = found.additions.checks
    assert record.inputs is not None
    assert record.inputs.reads == ("docs/**/*.md",)
    assert record.inputs.widens == ("mkdocs.yml",)
    assert record.inputs.on_removal is True
    assert record.inputs.per_file is True
    assert str(record.inputs.widen) == "acme_declared._checks:mend"
    assert record.inputs.ignores == ("docs/README.md",)


def test_a_slot_composes_by_a_rule_or_a_reference_that_resolves(
    package: Path,
) -> None:
    where = package / "extension.toml"
    assert _refusal(package, '[slots."acme.style"]\ncompose = "longest"\n') == (
        f"{where}: slots.\"acme.style\".compose is 'longest'; a slot composes by"
        " 'union', 'nearest', or a reference 'module:function'"
    )
    assert "names gone, which acme_declared._checks does not define" in _refusal(
        package, '[slots."acme.style"]\ncompose = "acme_declared._checks:gone"\n'
    )
    found = _declare(
        package,
        '[slots."acme.depth"]\ncompose = "nearest"\ndefault = "shallow"\n'
        'values = ["shallow", "deep"]\n'
        '[slots."acme.style"]\ncompose = "acme_declared._checks:mend"\n'
        '[slots."acme.paths"]\n',
    )
    depth, style, paths = found.slots
    assert (depth.name, depth.compose, depth.default, depth.values) == (
        "acme.depth",
        "nearest",
        "shallow",
        ("shallow", "deep"),
    )
    assert str(style.compose) == "acme_declared._checks:mend"
    assert (paths.compose, paths.values) == ("union", None)


JOB = '[ci.jobs.gate.prose]\nentries = ["acme.prose"]\n'


def test_a_job_joins_a_builtin_point_under_a_name_the_point_does_not_have(
    package: Path,
) -> None:
    where = package / "extension.toml"
    assert _refusal(package, '[ci.jobs.weekly.prose]\nentries = ["acme"]\n') == (
        f"{where}: ci.jobs.weekly.prose names the point 'weekly', which is not"
        " builtin; a job joins one of gate, merge, nightly, release"
    )
    # A table naming a job the point has joins it, and a join that
    # gates refuses: the verdict's needs are the job's own.
    assert _refusal(package, "[ci.jobs.gate.check]\ngates = true\n") == (
        f"{where}: ci.jobs.gate.check.gates joins the gate point's check job and"
        " gates; a join adds entries and plumbing, and the verdict's needs stay"
        " the job's own"
    )


def test_a_table_naming_a_job_the_point_has_joins_it(package: Path) -> None:
    found = _declare(
        package,
        '[ci.jobs.gate.prose]\nentries = ["acme.prose"]\n'
        '[ci.jobs.merge.prose]\npublishes = "book"\npaths = ["book"]\n'
        '[ci.jobs.merge.gate]\nentries = ["acme.ship"]\ncollects = "book"\n',
    )
    jobs = {(item.point, item.job.name): item for item in found.additions.jobs}
    assert not jobs[("gate", "prose")].job.joins
    # Declared earlier in the same file, or inherited from the gate
    # point: both read as joins, with the artifact keys they carry.
    assert jobs[("merge", "prose")].job.joins
    assert jobs[("merge", "prose")].job.publishes == "book"
    assert jobs[("merge", "prose")].job.paths == ("book",)
    assert jobs[("merge", "gate")].job.joins
    assert jobs[("merge", "gate")].job.collects == "book"
    assert [e.task for e in jobs[("merge", "gate")].entries] == ["acme.ship"]


def test_an_unknown_key_or_a_grant_in_a_job_table_refuses_naming_the_file(
    package: Path,
) -> None:
    where = str(package / "extension.toml")
    unknown = _refusal(package, JOB + "writes = true\n")
    assert unknown.startswith(where) and "has no key 'writes'" in unknown
    secret = _refusal(package, JOB + 'token = "secret"\n')
    assert secret.startswith(where)
    assert "ci.jobs.gate.prose.token is 'secret'; it takes one of job" in secret


def test_a_jobs_inputs_name_their_reads(package: Path) -> None:
    where = package / "extension.toml"
    assert _refusal(package, JOB + "inputs = { on-removal = true }\n") == (
        f"{where}: ci.jobs.gate.prose.inputs names no reads: inputs name the"
        " files it reads"
    )
    found = _declare(package, JOB + 'inputs = { reads = ["docs/**"] }\n')
    (prose,) = found.additions.jobs
    assert prose.job.inputs is not None
    assert prose.job.inputs.reads == ("docs/**",)


def test_a_jobs_functions_resolve_against_the_extensions_sources(
    package: Path,
) -> None:
    assert "names gone, which acme_declared._checks does not define" in _refusal(
        package, JOB + 'installs = "acme_declared._checks:gone"\n'
    )
    assert "outside the extension's package acme_declared" in _refusal(
        package, JOB + 'deploy = "elsewhere._seams:pages"\n'
    )


def test_a_contract_key_outside_a_workspace_contract_refuses(package: Path) -> None:
    where = package / "extension.toml"
    assert _refusal(
        package, '[contract.extension.extension]\nx = { types = ["str"] }\n'
    ) == (
        f"{where}: [contract.extension] names no contract; an extension declares"
        " keys in root, package"
    )
    assert "[contract.root.acme] types is ['text']" in _refusal(
        package, '[contract.root.acme]\ntypes = ["text"]\n'
    )


# Then the read.


def test_every_key_is_optional(package: Path) -> None:
    found = _declare(package, "")
    assert found.api_version == 1
    assert found.levels == ("workspace",)
    assert found.plugin == ""
    assert found.additions.checks == ()


def test_a_declaration_reads_into_records_named_for_the_extension(
    package: Path,
) -> None:
    found = _declare(
        package,
        '[extension]\nlevels = ["workspace", "package"]\nplugin = "acme.tasks"\n'
        'requires = ["docs"]\n'
        '[toolroom]\nrequires = ["docker>=27"]\n'
        '[options]\ndeep = "judges every file, not only the changed ones"\n'
        + CHECK
        + 'fix = "acme_declared._checks:mend"\nnarrowing = "paths"\n'
        'extensions = ["python"]\ntools = ["acme"]\narguments = true\n'
        'listed-with = "deep"\n'
        'claims = [{ category = "source", ignore = ["X1"], suffixes = [".py"] }]\n'
        "[checks.acme.lint.options.strict]\n"
        'type = "bool"\ndefault = false\ndoc = "refuses warnings too"\n'
        '[checks.acme.lint.fragments.".acme"]\nextensions = ["cpp", "cmake"]\n'
        'text = "strict: true\\n"\n'
        '[contributions]\n"python.dev-group" = ["acme>=1"]\n'
        '[contract.root.acme.mode]\ntypes = ["str"]\nvalues = ["fast", "slow"]\n'
        'doc = "how hard acme looks"\n'
        '[for.docs.contributions]\n"docs.theme" = ["acme"]\n',
    )
    assert found.levels == ("workspace", "package")
    assert (found.plugin, found.requires, found.tools) == (
        "acme.tasks",
        ("docs",),
        ("docker>=27",),
    )
    assert found.options == {"deep": "judges every file, not only the changed ones"}
    (record,) = found.additions.checks
    assert (record.name, record.extension, record.listed_with) == (
        "lint.acme",
        "acme",
        "deep",
    )
    assert (str(record.run), str(record.fix)) == (
        "acme_declared._checks:judge",
        "acme_declared._checks:mend",
    )
    assert [(c.category, c.ignore, c.suffixes) for c in record.claims] == [
        ("source", ("X1",), (".py",))
    ]
    assert [(o.name, o.kind, o.default) for o in record.options] == [
        ("strict", "bool", False)
    ]
    assert [(f.file, f.extensions) for f in record.fragments] == [
        (".acme", ("cpp", "cmake")),
    ]
    assert record.extensions == ("python",)
    assert found.additions.contributions == (("python.dev-group", "acme>=1"),)
    assert found.targets["docs"].contributions == (("docs.theme", "acme"),)


def test_a_job_reads_into_a_contribution_named_for_the_extension(
    package: Path,
) -> None:
    found = _declare(
        package,
        '[ci.jobs.merge.book]\nentries = ["acme.book.build", "acme.book.publish"]\n'
        'needs = ["gate"]\nfetch = "tags"\ntoken = "job"\n'
        'installs = "acme_declared._checks:judge"\n'
        'deploy = "acme_declared._checks:mend"\nnote = "The book."\n'
        '[for.other.ci.jobs.gate.prose]\nentries = ["acme.prose"]\ngates = true\n',
    )
    (book,) = found.additions.jobs
    assert (book.point, book.job.name, book.gates, book.extension) == (
        "merge",
        "book",
        False,
        "acme",
    )
    assert (book.job.needs, book.job.fetch, book.job.token, book.job.note) == (
        ("gate",),
        "tags",
        "job",
        "The book.",
    )
    assert str(book.job.installs) == "acme_declared._checks:judge"
    assert str(book.job.deploy) == "acme_declared._checks:mend"
    assert [(e.point, e.job, e.task, e.source) for e in book.entries] == [
        ("merge", "book", "acme.book.build", "acme"),
        ("merge", "book", "acme.book.publish", "acme"),
    ]
    (prose,) = found.targets["other"].jobs
    assert (prose.point, prose.job.name, prose.gates) == ("gate", "prose", True)
    assert (prose.job.installs, prose.job.deploy) == (None, None)


def test_a_reference_imports_when_it_runs(package: Path) -> None:
    found = _declare(package, CHECK)
    (record,) = found.additions.checks
    assert "acme_declared._checks" not in sys.modules
    record.run(object())  # type: ignore[arg-type]
    assert "acme_declared._checks" in sys.modules


def test_the_contract_keys_a_declaration_owns_are_read_without_the_rest(
    package: Path,
) -> None:
    from livery.workshop._declaration import contract_keys

    (package / "extension.toml").write_text(
        '[contract.package.acme."paths[]"]\ntypes = ["table"]\n'
        'path = { types = ["str"], doc = "a path" }\n'
    )
    assert [
        (k.contract, k.path, k.types, k.doc) for k in contract_keys("acme_declared")
    ] == [
        ("package", "acme.paths[]", ("table",), ""),
        ("package", "acme.paths[].path", ("str",), "a path"),
    ]
    assert contract_keys("acme_absent_package") == ()


def test_declared_checks_register_under_the_listed_name(package: Path) -> None:
    from livery.workshop import _checks
    from livery.workshop._extensions import register_declared

    found = _declare(package, CHECK)
    state = _checks.snapshot()
    try:
        assert register_declared("acme.listed", found.additions) is True
        assert _checks.checks_by_name()["lint.acme"].extension == "acme.listed"
        assert register_declared("acme.listed", Additions()) is False
    finally:
        _checks.restore(state)


def test_declared_jobs_join_their_points_under_the_listed_name(package: Path) -> None:
    from livery.workshop._extensions import register_declared
    from livery.workshop._points import (
        Entry,
        builtin_schedule,
        point_by_name,
        verdict_needs,
        withdraw_job,
    )

    found = _declare(package, JOB + "gates = true\n")
    try:
        assert register_declared("acme.listed", found.additions) is False
        names = [job.name for job in point_by_name(None)["gate"].jobs]
        assert names.index("prose") == names.index("gate") - 1
        assert "prose" in verdict_needs("gate")
        assert (
            Entry("gate", "prose", "acme.prose", source="acme.listed")
            in builtin_schedule()
        )
    finally:
        withdraw_job("gate", "prose")


def test_the_tasks_an_extension_defines_are_read_from_its_sources(
    tmp_path: Path,
) -> None:
    from livery.workshop._declaration import defined_tasks

    package = tmp_path / "acme_tasks"
    package.mkdir()
    (package / "_verbs.py").write_text(
        "from livery.footman import group\n\n"
        'acme = group("acme", help="the acme verbs")\n'
        'deep = acme.group("deep")\n\n\n'
        '@acme.task(name="build")\n'
        "def acme_build() -> None: ...\n\n\n"
        "@acme.task\n"
        "def publish_site_() -> None: ...\n\n\n"
        "@deep.task\n"
        "def dig() -> None: ...\n\n\n"
        "def helper() -> None: ...\n"
    )
    (package / "_more.py").write_text(
        "from acme_tasks._verbs import acme\n\n\n@acme.task\ndef serve() -> None: ...\n"
    )
    # A match object's group is no footman group.
    (package / "_unrelated.py").write_text(
        "import re\n\n"
        'found = re.match("x", "x")\n'
        'first = found.group(0) if found else ""\n'
    )
    assert defined_tasks(package) == {
        "acme.build",
        "acme.publish-site",
        "acme.deep.dig",
        "acme.serve",
    }
    assert defined_tasks(tmp_path / "acme_tasks" / "_none_here") == frozenset()


# A file the extension's code writes: the refusals first.


def test_a_fragment_without_its_render_refuses_naming_it(package: Path) -> None:
    refused = _refusal(package, '[fragments."NOTES.md"]\nlocal = true\n')
    assert "NOTES.md" in refused and "names no render" in refused
    refused = _refusal(package, '[fragments."NOTES.md"]\nrender = "elsewhere:judge"\n')
    assert "outside the extension's package acme_declared" in refused


def test_a_fragment_declares_its_target_its_render_and_whether_it_is_local(
    package: Path,
) -> None:
    from livery.workshop._declaration import DeclaredOutput, Reference

    found = _declare(
        package,
        '[fragments."NOTES.md"]\nrender = "acme_declared._checks:judge"\n\n'
        '[fragments.".acme/"]\nrender = "acme_declared._checks:mend"\nlocal = true\n',
    )
    assert found.fragments == (
        DeclaredOutput("NOTES.md", Reference("acme_declared._checks", "judge")),
        DeclaredOutput(
            ".acme/", Reference("acme_declared._checks", "mend"), local=True
        ),
    )
