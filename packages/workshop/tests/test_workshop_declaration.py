"""An extension.toml, read without importing the extension: refusals, then the read."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from livery.workshop._declaration import (
    Additions,
    Declaration,
    DeclarationError,
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
        "  [extension] has no key 'level': it takes after, api-version, before,"
        " compatible, levels, plugin, requires; did you mean 'levels'?"
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
    assert _refusal(package, "[ci.jobs.gate.check]\n") == (
        f"{where}: ci.jobs.gate.check names a job the gate point declares already"
    )


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
