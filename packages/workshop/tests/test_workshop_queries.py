"""Queries: the refusals first, then each rule that combines the answers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from livery.workshop import (
    COMPILE_COMMANDS,
    CURRENT_VERSION,
    EXECUTABLES,
    JOB_BUILT_MEMBERS,
    PUBLIC_MODULES,
    REQUIREMENTS,
    VERSION_FILES,
    Query,
    _extensions,
    _queries,
    answer,
)
from livery.workshop._declaration import DeclarationError, DeclaredValue
from livery.workshop._packages import Package
from livery.workshop._queries import (
    EDITOR_EXTENSIONS,
    QueryError,
    QuerySpec,
    answers,
    base_queries,
    workspace_answers,
)
from livery.workshop._release_notes import RELEASE_NOTES, release_notes
from workshop_extension_fakes import (
    fake_extensions,
    fake_steps,
    package_contract,
    root_contract,
)
from workshop_installed import skip_unless_installed

#: An extension's identity, the start of every package fake.
PACKAGE = "[extension]\n"

#: An extension's identity, the start of every workspace fake.
WORKSPACE = "[extension]\n"

#: A workspace-level union query, declared by a fake.
STYLE: Query[tuple[str, ...]] = Query("acme.style")
DECLARES_STYLE = WORKSPACE + '[queries."acme.style"]\nlevel = "workspace"\n'

#: A workspace-level nearest query with a default and values, declared
#: by a fake.
DEPTH: Query[str] = Query("acme.depth")
DECLARES_DEPTH = (
    WORKSPACE + '[queries."acme.depth"]\nlevel = "workspace"\ncombine = "nearest"\n'
    'default = "shallow"\nvalues = ["shallow", "deep"]\n'
)

#: The docs extension's members policy, which the docs extension declares.
MEMBERS: Query[str] = Query("docs.members")


def _this_checkout_or_skip(root: Path) -> None:
    """Skip unless the workshop under test is this checkout's, with its dev group.

    The release train's isolated leg installs the workshop's wheel alone,
    beside no extension, so a test that answers this workspace's listed
    extensions has nothing to answer there.
    """
    import livery.workshop

    if not Path(livery.workshop.__file__).resolve().is_relative_to(root):
        pytest.skip("the test reads this checkout's listed extensions")


def _answering(**answers_by_query: str) -> str:
    """The ``[queries]`` table answering each query with its function in ``_steps``.

    An extension answering ``executables`` adds the run phase's step that
    starts them, as its declaration must.
    """
    lines = [
        f'"{query}" = "{{package}}._steps:{function}"'
        for query, function in answers_by_query.items()
    ]
    run = (
        '\n[phases.run.start]\nmain = "{package}._steps:main"\n'
        if "executables" in answers_by_query
        else ""
    )
    return "\n[queries]\n" + "\n".join(lines) + "\n" + run


def _literal(query: str, value: str) -> str:
    """The ``[queries.<query>]`` table answering with the literal *value*."""
    return f'\n[queries."{query}"]\nvalue = {value}\n'


def _package(root: Path, *extensions: str) -> Package:
    return Package(
        directory=root / "packages" / "core",
        path="packages/core",
        name="core",
        depends=(),
        extensions=extensions,
    )


def _fakes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **declared: tuple[str, str]
) -> Package:
    """Fakes for *declared*, each its declaration and its ``_steps`` source.

    One package lists them all.
    """
    names = [f"acme.{name}" for name in declared]
    fake_extensions(
        tmp_path,
        monkeypatch,
        **{name: declaration for name, (declaration, _source) in declared.items()},
    )
    for name, (declaration, source) in declared.items():
        if "[phases.run.start]" in declaration:
            source += "\n\ndef main(ctx):\n    del ctx\n"
        fake_steps(tmp_path, name, source)
    return _package(tmp_path, *names)


# The refusals first.


def test_two_extensions_naming_one_executable_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve = "def executables(package):\n    return ('serve', 'tool')\n"
    package = _fakes(
        tmp_path,
        monkeypatch,
        a=(PACKAGE + _answering(executables="executables"), serve),
        b=(
            PACKAGE + _answering(executables="executables"),
            "def executables(package):\n    return ('serve',)\n",
        ),
    )
    with pytest.raises(QueryError) as raised:
        answer(package, EXECUTABLES)
    assert str(raised.value) == (
        "packages/core: executables: acme.a and acme.b both answer serve; one"
        " extension of a package answers each"
    )


@pytest.mark.parametrize(
    ("query_name", "first", "second", "refusal"),
    [
        (
            "current-version",
            "'1.0.0'",
            "'2.0.0'",
            "current-version: the answers differ, and they must agree: acme.a"
            " '1.0.0', acme.b '2.0.0'",
        ),
        (
            "requirements",
            "{'zlib': '>=1'}",
            "{'zlib': '>=2'}",
            "requirements: acme.a answers zlib '>=1' and acme.b answers '>=2'; the"
            " answers name each entry once, or alike",
        ),
        (
            "compile-commands",
            "Path('a.json')",
            "Path('b.json')",
            "compile-commands: acme.a and acme.b both answer, and neither requires"
            " the other; one of them answers, or one requires the other",
        ),
    ],
    ids=["disagreeing-versions", "two-constraints", "two-nearest"],
)
def test_answers_a_rule_cannot_combine_refuse_naming_both(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    query_name: str,
    first: str,
    second: str,
    refusal: str,
) -> None:
    source = "from pathlib import Path\n\n\ndef answering(package):\n    return {}\n"
    package = _fakes(
        tmp_path,
        monkeypatch,
        a=(PACKAGE + _answering(**{query_name: "answering"}), source.format(first)),
        b=(PACKAGE + _answering(**{query_name: "answering"}), source.format(second)),
    )
    query: Query[object] = Query(query_name)
    with pytest.raises(QueryError) as raised:
        answer(package, query)
    assert str(raised.value) == f"packages/core: {refusal}"


def test_two_answers_to_a_one_answer_query_refuse_naming_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The release notes' provider is one answer: two listed extensions
    # answering, neither requiring the other, refuse naming both.
    notes = "def notes(root):\n    return '{name} notes'\n"
    _fakes(
        tmp_path,
        monkeypatch,
        a=(
            WORKSPACE + _answering(**{"release-notes": "notes"}),
            notes.format(name="a"),
        ),
        b=(
            WORKSPACE + _answering(**{"release-notes": "notes"}),
            notes.format(name="b"),
        ),
    )
    root_contract(tmp_path, '["acme.a", "acme.b"]')
    with pytest.raises(QueryError) as raised:
        release_notes(tmp_path)
    assert str(raised.value) == (
        "release-notes: acme.a and acme.b both answer, and neither requires the"
        " other; one of them answers, or one requires the other"
    )
    # One requiring the other answers nearest, and one alone answers.
    _fakes(
        tmp_path,
        monkeypatch,
        b=(
            WORKSPACE
            + 'requires = ["acme.a"]\n'
            + _answering(**{"release-notes": "notes"}),
            notes.format(name="b"),
        ),
    )
    nearest: object = release_notes(tmp_path)
    assert nearest == "b notes"
    root_contract(tmp_path, '["acme.a"]')
    alone: object = release_notes(tmp_path)
    assert alone == "a notes"
    root_contract(tmp_path, "[]")
    assert release_notes(tmp_path) is None


def test_an_answer_to_an_undeclared_query_refuses_naming_the_declared_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(
        tmp_path, monkeypatch, odd=WORKSPACE + _literal("acme.none", '["x"]')
    )
    path = tmp_path / "site" / "acme" / "odd" / "extension.toml"
    with pytest.raises(DeclarationError) as raised:
        _extensions.declaration("acme.odd")
    assert str(raised.value) == (
        f'{path}: queries."acme.none" names no query; the queries are'
        f" {', '.join(_queries.declared_queries())}"
    )
    # Asked, an undeclared query refuses the same way.
    with pytest.raises(QueryError) as refused:
        answer(tmp_path, Query("acme.none"))
    assert str(refused.value) == (
        f"acme.none is not a declared query; the queries are"
        f" {', '.join(_queries.declared_queries())}"
    )


@pytest.mark.parametrize(
    ("declared", "refusal"),
    [
        (
            PACKAGE + _answering(**{"public-module": "public_modules"}),
            "queries.public-module names no query; the queries are {queries}; did"
            " you mean 'public-modules'?",
        ),
        (
            PACKAGE + _answering(**{"public-modules": "public_module"}),
            "queries.public-modules names public_module, which acme.odd._steps does"
            " not define at its top level; did you mean 'public_modules'?",
        ),
        (
            WORKSPACE + '[queries."docs.members"]\n',
            'queries."docs.members" is a table without value; an answer is'
            " 'module:function', or a table with value alone",
        ),
        (
            WORKSPACE + '[for.docs.queries."acme.none"]\nvalue = ["x"]\n',
            'for.docs.queries."acme.none" names no query; the queries are {queries}',
        ),
        (
            WORKSPACE + '[for.docs.queries."acme.wide"]\nlevel = "workspace"\n',
            'for.docs.queries."acme.wide" declares a query, and a [for] table only'
            ' answers one; declare it at the top level, [queries."acme.wide"]',
        ),
    ],
    ids=[
        "unknown-query",
        "no-function",
        "no-value",
        "for-unknown-query",
        "for-declares",
    ],
)
def test_an_answer_the_file_alone_shows_wrong_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, declared: str, refusal: str
) -> None:
    if "docs" in declared:
        skip_unless_installed("docs")
    fake_extensions(tmp_path, monkeypatch, odd=declared)
    fake_steps(tmp_path, "odd", "def public_modules(package):\n    return ()\n")
    with pytest.raises(DeclarationError) as raised:
        _extensions.declaration("acme.odd")
    path = tmp_path / "site" / "acme" / "odd" / "extension.toml"
    expected = refusal.format(queries=", ".join(_queries.declared_queries()))
    assert str(raised.value) == f"{path}: {expected}"


def test_a_package_level_extension_answers_a_workspace_query_while_a_package_holds_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A workspace-level query is asked of every extension the workspace
    # mounts, and a package-level one is mounted while a package holds it.
    fake_extensions(
        tmp_path,
        monkeypatch,
        held=PACKAGE + _literal("editor-extensions", '["acme.held"]'),
    )
    root_contract(tmp_path, "[]")
    assert answer(tmp_path, EDITOR_EXTENSIONS) == ()
    package_contract(tmp_path, "core", '["acme.held"]')
    assert answer(tmp_path, EDITOR_EXTENSIONS) == ("acme.held",)


def test_a_member_two_answers_give_different_jobs_refuses_naming_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = '{{ "packages/core" = "{job}" }}'
    fake_extensions(
        tmp_path,
        monkeypatch,
        a=WORKSPACE + _literal("job-built-members", built.format(job="wheels")),
        b=WORKSPACE + _literal("job-built-members", built.format(job="binaries")),
    )
    root_contract(tmp_path, '["acme.a", "acme.b"]')
    with pytest.raises(QueryError) as raised:
        answer(tmp_path, JOB_BUILT_MEMBERS)
    assert str(raised.value) == (
        "job-built-members: acme.a answers packages/core 'wheels' and acme.b"
        " answers 'binaries'; the answers name each entry once, or alike"
    )
    # Alike answers name the member once.
    fake_extensions(
        tmp_path,
        monkeypatch,
        b=WORKSPACE + _literal("job-built-members", built.format(job="wheels")),
    )
    assert answer(tmp_path, JOB_BUILT_MEMBERS) == {"packages/core": "wheels"}


def test_a_checkout_input_the_job_sets_or_one_not_a_string_refuses_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._queries import CHECKOUT_INPUTS

    fake_extensions(
        tmp_path,
        monkeypatch,
        deep=WORKSPACE + _literal("checkout-inputs", '{ fetch-depth = "0" }'),
        flag=WORKSPACE + _literal("checkout-inputs", "{ lfs = true }"),
    )
    root_contract(tmp_path, '["acme.deep"]')
    with pytest.raises(QueryError) as raised:
        answer(tmp_path, CHECKOUT_INPUTS)
    assert str(raised.value) == (
        "checkout-inputs: acme.deep answers fetch-depth, which the checkout step"
        " sets from the job's declaration; answer another input"
    )
    root_contract(tmp_path, '["acme.flag"]')
    with pytest.raises(QueryError) as refused:
        answer(tmp_path, CHECKOUT_INPUTS)
    assert str(refused.value) == (
        "checkout-inputs: acme.flag answers lfs with True, and an input's value is"
        " a string, as the workflow writes it; quote it"
    )


def test_the_checkout_inputs_the_listed_extensions_answer_reach_the_checkout_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._ci_generate import generate
    from livery.workshop._queries import CHECKOUT_INPUTS

    monkeypatch.setattr(
        "livery.workshop._extensions.workspace_root", lambda start=None: tmp_path
    )
    fake_extensions(
        tmp_path,
        monkeypatch,
        a=WORKSPACE + _literal("checkout-inputs", '{ lfs = "true" }'),
        b=WORKSPACE + _literal("checkout-inputs", '{ lfs = "false" }'),
        c=WORKSPACE + _literal("checkout-inputs", '{ lfs = "true", submodules = "x" }'),
    )
    # None listed: the checkout takes the job's own inputs alone.
    root_contract(tmp_path, "[]")
    assert answer(tmp_path, CHECKOUT_INPUTS) == {}
    plain = generate(tmp_path)[".github/workflows/ci.yml"]
    assert "lfs: true" not in plain
    # Two answers giving one input different values refuse, naming both.
    root_contract(tmp_path, '["acme.a", "acme.b"]')
    with pytest.raises(QueryError) as raised:
        answer(tmp_path, CHECKOUT_INPUTS)
    assert str(raised.value) == (
        "checkout-inputs: acme.a answers lfs 'true' and acme.b answers 'false';"
        " the answers name each entry once, or alike"
    )
    # Alike answers name the input once, written before the job's own,
    # on GitHub and on Gitea.
    root_contract(tmp_path, '["acme.a", "acme.c"]')
    assert answer(tmp_path, CHECKOUT_INPUTS) == {"lfs": "true", "submodules": "x"}
    added = "        with:\n          lfs: true\n          submodules: x\n"
    assert added in generate(tmp_path)[".github/workflows/ci.yml"]
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nextensions = ["acme.a", "acme.c"]\n\n[forge]\nkind = "gitea"\n'
    )
    assert added in generate(tmp_path)[".gitea/workflows/ci.yml"]


def test_a_question_asked_at_the_other_level_refuses(tmp_path: Path) -> None:
    root_contract(tmp_path, "[]")
    with pytest.raises(QueryError) as raised:
        answer(tmp_path, PUBLIC_MODULES)
    assert str(raised.value) == (
        "public-modules is a question about a package, which livery.workshop"
        " declares at the package level; ask it with the package"
    )
    with pytest.raises(QueryError) as refused:
        answer(_package(tmp_path), EDITOR_EXTENSIONS)
    assert str(refused.value) == (
        "editor-extensions is a question about the workspace, which"
        " livery.workshop declares at the workspace level; ask it with the"
        " workspace root"
    )


@pytest.mark.parametrize(
    ("text", "refusal"),
    [
        (
            DECLARES_STYLE + 'combine = "longest"\n',
            "queries.\"acme.style\".combine is 'longest'; a query combines its"
            " answers by 'union', 'nearest', 'agreed', or a function 'module:name'",
        ),
        (
            DECLARES_STYLE + 'combine = "{package}._steps:gone"\n',
            'queries."acme.style".combine names gone, which acme.site._steps does'
            " not define at its top level",
        ),
        (
            DECLARES_STYLE.replace('level = "workspace"', 'level = "sometimes"'),
            "queries.\"acme.style\".level is 'sometimes'; a query is asked at one"
            " of package, workspace",
        ),
        (
            DECLARES_STYLE + 'values = "all"\n',
            'queries."acme.style".values is a list of the answers the query takes',
        ),
        (
            DECLARES_STYLE + 'value = ["x"]\n',
            'queries."acme.style" declares the query and answers it in one table; a'
            " declaration takes default for the answer while nobody answers",
        ),
    ],
    ids=["no-rule", "no-function", "no-level", "no-list", "declares-and-answers"],
)
def test_a_declaration_off_its_shape_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str, refusal: str
) -> None:
    # Read from the installed file before any declaration is judged, so
    # the refusal reaches every reader of the declared queries.
    fake_extensions(tmp_path, monkeypatch, site=text)
    path = tmp_path / "site" / "acme" / "site" / "extension.toml"
    with pytest.raises(DeclarationError) as raised:
        _queries.declared_queries()
    assert str(raised.value) == f"{path}: {refusal}"


def test_two_declarers_of_one_query_refuse_naming_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(
        tmp_path,
        monkeypatch,
        site=WORKSPACE + '[queries.public-modules]\nlevel = "package"\n',
    )
    with pytest.raises(
        DeclarationError,
        match=r"^the public-modules query is declared by both acme\.site and"
        r" livery\.workshop; one extension declares a query$",
    ):
        _queries.declared_queries()
    fake_extensions(tmp_path, monkeypatch, site=DECLARES_STYLE, other=DECLARES_STYLE)
    with pytest.raises(
        DeclarationError,
        match=r"^the acme\.style query is declared by both acme\.other and acme\.site;"
        r" one extension declares a query$",
    ):
        _queries.declared_queries()


def test_an_answer_off_the_declared_values_refuses_naming_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A literal off the values refuses as the file is read; a function's
    # answer refuses when the query is asked, naming the extension.
    fake_extensions(
        tmp_path,
        monkeypatch,
        site=DECLARES_DEPTH,
        a=WORKSPACE + _literal("acme.depth", '"wide"'),
    )
    path = tmp_path / "site" / "acme" / "a" / "extension.toml"
    with pytest.raises(DeclarationError) as raised:
        _extensions.declaration("acme.a")
    assert str(raised.value) == (
        f"{path}: queries.\"acme.depth\".value is 'wide'; the query's values are"
        " 'shallow', 'deep'"
    )
    _fakes(
        tmp_path,
        monkeypatch,
        site=(DECLARES_DEPTH, ""),
        b=(
            WORKSPACE + _answering(**{"acme.depth": "depth"}),
            "def depth(root):\n    return 'wide'\n",
        ),
    )
    root_contract(tmp_path, '["acme.site", "acme.b"]')
    with pytest.raises(QueryError) as refused:
        answer(tmp_path, DEPTH)
    assert str(refused.value) == (
        "acme.b answers acme.depth with 'wide', whose values are 'shallow', 'deep'"
    )
    fake_steps(tmp_path, "b", "def depth(root):\n    return 'deep'\n")
    assert answer(tmp_path, DEPTH) == "deep"


def test_a_for_table_s_answer_off_the_declared_values_refuses_naming_its_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fakes(
        tmp_path,
        monkeypatch,
        site=(DECLARES_DEPTH, ""),
        c=(
            WORKSPACE
            + '\n[for."acme.site".queries]\n"acme.depth" = "{package}._steps:depth"\n',
            "def depth(root):\n    return 'wide'\n",
        ),
    )
    root_contract(tmp_path, '["acme.site", "acme.c"]')
    with pytest.raises(QueryError) as refused:
        answer(tmp_path, DEPTH)
    assert str(refused.value) == (
        "acme.c answers acme.depth with 'wide' at"
        ' for."acme.site".queries."acme.depth", whose values are'
        " 'shallow', 'deep'"
    )


def test_a_union_answered_with_a_string_refuses_naming_the_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(
        tmp_path,
        monkeypatch,
        a=WORKSPACE + _literal("editor-extensions", '"acme.editor"'),
    )
    root_contract(tmp_path, '["acme.a"]')
    with pytest.raises(QueryError) as raised:
        answer(tmp_path, EDITOR_EXTENSIONS)
    assert str(raised.value) == (
        "editor-extensions: acme.a answers 'acme.editor', and a union combines"
        " lists; answer a list"
    )


def test_a_for_table_s_answer_is_never_asked_while_its_target_is_unlisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The answer marks the workspace when it is asked, so a call shows.
    _fakes(
        tmp_path,
        monkeypatch,
        site=(DECLARES_STYLE, ""),
        a=(
            WORKSPACE
            + '\n[for."acme.site".queries]\n"acme.style" = "{package}._steps:style"\n',
            "def style(root):\n    (root / 'asked').write_text('')\n"
            "    return ['a-for']\n",
        ),
    )
    asked = tmp_path / "asked"
    # The target unlisted, then listed with the owner's entry leaving it
    # out: the owner's listing contributes nothing, and nothing asks.
    root_contract(tmp_path, '["acme.a"]')
    assert answer(tmp_path, STYLE) == ()
    root_contract(
        tmp_path, '["acme.site", { name = "acme.a", without = ["acme.site"] }]'
    )
    assert answer(tmp_path, STYLE) == ()
    assert not asked.exists()
    # Listed, and the owner's listing contributes to it: asked.
    root_contract(tmp_path, '["acme.site", "acme.a"]')
    assert answer(tmp_path, STYLE) == ("a-for",)
    assert asked.exists()


def test_an_answer_at_the_top_level_and_in_a_for_table_unions_or_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(
        tmp_path,
        monkeypatch,
        site=DECLARES_STYLE,
        deep=DECLARES_DEPTH,
        a=WORKSPACE
        + _literal("acme.style", '["top", "shared"]')
        + _literal("acme.depth", '"deep"')
        + '\n[for."acme.site".queries."acme.style"]\nvalue = ["shared", "for"]\n'
        + '\n[for."acme.site".queries."acme.depth"]\nvalue = "shallow"\n',
        b=WORKSPACE
        + _literal("acme.style", '["b"]')
        + '\n[for."acme.site".queries."acme.style"]\nvalue = "for"\n',
    )
    # A union refuses an answer from either place that is no list.
    root_contract(tmp_path, '["acme.site", "acme.b"]')
    with pytest.raises(QueryError) as unlisted:
        answer(tmp_path, STYLE)
    assert str(unlisted.value) == (
        "acme.b answers acme.style with 'for' at"
        ' for."acme.site".queries."acme.style", and a union combines lists;'
        " answer a list"
    )
    # Both places asked: a query that takes one answer refuses, naming both.
    root_contract(tmp_path, '["acme.site", "acme.deep", "acme.a"]')
    with pytest.raises(QueryError) as raised:
        answer(tmp_path, DEPTH)
    assert str(raised.value) == (
        'acme.a answers acme.depth at queries."acme.depth" and at'
        ' for."acme.site".queries."acme.depth"; only a union query takes two'
        " answers from one extension, so answer it in one place"
    )
    # A union takes every item of both, once each, in the order first met.
    assert answers(tmp_path, STYLE) == {"acme.a": ("top", "shared", "for")}
    assert answer(tmp_path, STYLE) == ("top", "shared", "for")
    # The target unlisted, the top level answers alone.
    root_contract(tmp_path, '["acme.deep", "acme.a"]')
    assert answer(tmp_path, DEPTH) == "deep"
    assert answer(tmp_path, STYLE) == ("top", "shared")


# The rules.


def test_a_union_holds_each_answer_once_in_composition_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _fakes(
        tmp_path,
        monkeypatch,
        z=(
            PACKAGE + _answering(**{"public-modules": "mods"}),
            "def mods(package):\n    return ('z.api', 'shared')\n",
        ),
        a=(
            PACKAGE
            + 'requires = ["acme.z"]\n'
            + _answering(**{"public-modules": "mods"}),
            "def mods(package):\n    return ('shared', 'a.api')\n",
        ),
    )
    assert answer(package, PUBLIC_MODULES) == ("z.api", "shared", "a.api")
    assert answers(package, PUBLIC_MODULES) == {
        "acme.z": ("z.api", "shared"),
        "acme.a": ("shared", "a.api"),
    }
    # A query no extension of the set answers has its rule's answer over
    # nothing: an empty union, no nearest, no entries.
    assert answer(package, VERSION_FILES) == ()
    assert answer(package, COMPILE_COMMANDS) is None
    requirements = answer(package, REQUIREMENTS)
    assert dict(requirements) == {}


def test_agreement_reads_past_an_empty_answer_and_alike_values_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _fakes(
        tmp_path,
        monkeypatch,
        a=(
            PACKAGE
            + _answering(**{"current-version": "version", "requirements": "needs"}),
            "def version(package):\n    return ''\n\n\n"
            "def needs(package):\n    return {'zlib': '>=1'}\n",
        ),
        b=(
            PACKAGE
            + _answering(**{"current-version": "version", "requirements": "needs"}),
            "def version(package):\n    return '1.2.0'\n\n\n"
            "def needs(package):\n    return {'zlib': '>=1', 'fmt': '>=10'}\n",
        ),
    )
    assert answer(package, CURRENT_VERSION) == "1.2.0"
    requirements = answer(package, REQUIREMENTS)
    assert dict(requirements) == {"zlib": ">=1", "fmt": ">=10"}


def test_the_nearest_answer_is_the_one_that_requires_the_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(
        tmp_path,
        monkeypatch,
        cmake=PACKAGE + _answering(**{"compile-commands": "database"}),
        cpp=PACKAGE
        + 'requires = ["acme.cmake"]\n'
        + _answering(**{"compile-commands": "database"}),
    )
    source = "from pathlib import Path\n\n\ndef database(package):\n    return {}\n"
    fake_steps(tmp_path, "cmake", source.format("Path('cmake.json')"))
    fake_steps(tmp_path, "cpp", source.format("Path('cpp.json')"))
    package = _package(tmp_path, "acme.cpp")
    assert answer(package, COMPILE_COMMANDS) == Path("cpp.json")
    # An answer of none leaves the field to the others.
    fake_steps(tmp_path, "cpp", "def database(package):\n    return None\n")
    assert answer(package, COMPILE_COMMANDS) == Path("cmake.json")


def test_executables_of_several_extensions_are_their_union(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _fakes(
        tmp_path,
        monkeypatch,
        a=(
            PACKAGE + _answering(executables="executables"),
            "def executables(package):\n    return ('serve',)\n",
        ),
        b=(
            PACKAGE + _answering(executables="executables"),
            "def executables(package):\n    return ('tool',)\n",
        ),
    )
    lookup = _extensions.installed_declaration
    assert answer(package, EXECUTABLES, lookup=lookup) == ("serve", "tool")


def test_a_set_whose_order_has_no_start_is_asked_alphabetically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _fakes(
        tmp_path,
        monkeypatch,
        b=(
            PACKAGE
            + 'requires = ["acme.a"]\n'
            + _answering(**{"public-modules": "mods"}),
            "def mods(package):\n    return ('b.api',)\n",
        ),
        a=(
            PACKAGE
            + 'requires = ["acme.b"]\n'
            + _answering(**{"public-modules": "mods"}),
            "def mods(package):\n    return ('a.api',)\n",
        ),
    )
    # `layering.graph` names the cycle; the answer is still asked.
    assert answer(package, PUBLIC_MODULES) == ("a.api", "b.api")


# The workspace level: a query a workspace extension declares, answered
# by the installed set in composition order.


def test_a_query_a_workspace_extension_declares_is_answered_in_composition_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(
        tmp_path,
        monkeypatch,
        site=DECLARES_STYLE,
        a=WORKSPACE + _literal("acme.style", '["a-first", "shared"]'),
        b=WORKSPACE + _literal("acme.style", '["shared", "b-first"]'),
    )
    assert _queries.declared_queries()["acme.style"] == QuerySpec(
        "acme.style", "union", "workspace", None, None, "acme.site"
    )
    # The list's order is no answer's order: the answers come in
    # composition order whichever way the list is written.
    for listed in (
        '["acme.site", "acme.b", "acme.a"]',
        '["acme.a", "acme.b", "acme.site"]',
    ):
        root_contract(tmp_path, listed)
        found: dict[str, object] = dict(answers(tmp_path, STYLE))
        assert list(found.items()) == [
            ("acme.a", ["a-first", "shared"]),
            ("acme.b", ["shared", "b-first"]),
        ]
        assert answer(tmp_path, STYLE) == ("a-first", "shared", "b-first")
    # An extension taken off the list takes its answer with it, and a
    # template reads the same answer by the query's name.
    root_contract(tmp_path, '["acme.site", "acme.a"]')
    assert answer(tmp_path, STYLE) == ("a-first", "shared")
    read = workspace_answers(tmp_path)
    assert "acme.style" in read and "public-modules" not in read
    assert read["acme.style"] == ("a-first", "shared")
    # Nobody listed answers: the union of nothing.
    root_contract(tmp_path, '["acme.site"]')
    assert answer(tmp_path, STYLE) == ()


def test_the_nearest_answer_wins_and_the_default_stands_while_nobody_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(
        tmp_path,
        monkeypatch,
        site=DECLARES_DEPTH,
        base=WORKSPACE + _literal("acme.depth", '"deep"'),
        top=WORKSPACE
        + 'requires = ["acme.base"]\n'
        + _literal("acme.depth", '"shallow"'),
    )
    root_contract(tmp_path, '["acme.site"]')
    assert answer(tmp_path, DEPTH) == "shallow"
    root_contract(tmp_path, '["acme.site", "acme.base"]')
    assert answer(tmp_path, DEPTH) == "deep"
    # The extension that requires the other answers nearest, whatever
    # the list's order; two that require neither refuse naming both.
    root_contract(tmp_path, '["acme.site", "acme.top", "acme.base"]')
    assert answer(tmp_path, DEPTH) == "shallow"
    fake_extensions(
        tmp_path, monkeypatch, top=WORKSPACE + _literal("acme.depth", '"shallow"')
    )
    with pytest.raises(QueryError) as raised:
        answer(tmp_path, DEPTH)
    assert str(raised.value) == (
        "acme.depth: acme.base and acme.top both answer, and neither requires the"
        " other; one of them answers, or one requires the other"
    )


def test_a_literal_answer_reads_as_a_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skip_unless_installed("docs")
    fake_extensions(
        tmp_path,
        monkeypatch,
        a=WORKSPACE
        + _literal("editor-extensions", '["acme.editor"]')
        + _literal("docs.members", '"all"'),
    )
    declared = _extensions.declaration("acme.a")
    assert declared is not None
    assert declared.queries == {
        "editor-extensions": DeclaredValue(["acme.editor"]),
        "docs.members": DeclaredValue("all"),
    }
    root_contract(tmp_path, '["acme.a"]')
    assert answer(tmp_path, EDITOR_EXTENSIONS) == ("acme.editor",)
    assert answer(tmp_path, MEMBERS) == "all"


def test_a_function_answer_is_imported_when_the_query_is_first_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fakes(
        tmp_path,
        monkeypatch,
        a=(
            WORKSPACE + _answering(**{"release-notes": "notes"}),
            "def notes(root):\n    return 'notes for ' + root.name\n",
        ),
    )
    root_contract(tmp_path, '["acme.a"]')
    declared = _extensions.declaration("acme.a")
    assert declared is not None and str(declared.queries["release-notes"]) == (
        "acme.a._steps:notes"
    )
    assert "acme.a._steps" not in sys.modules
    answered: object = answer(tmp_path, RELEASE_NOTES)
    assert answered == f"notes for {tmp_path.name}"
    assert "acme.a._steps" in sys.modules


def test_a_fake_s_declared_query_leaves_with_the_test_s_fakes(tmp_path: Path) -> None:
    # The declared queries are read once per process; a test's fakes
    # read them afresh for the test alone, so a query a fake declares
    # never reaches the test after it.
    with pytest.MonkeyPatch.context() as patch:
        fake_extensions(tmp_path, patch, site=DECLARES_STYLE)
        assert "acme.style" in _queries.declared_queries()
    assert "acme.style" not in _queries.declared_queries()


def test_the_base_s_queries_are_read_from_its_contract_file() -> None:
    found = base_queries()
    assert list(found) == [
        "public-modules",
        "compile-commands",
        "module-roots",
        "references",
        "current-version",
        "version-files",
        "requirements",
        "requirement-writers",
        "test-requirements",
        "test-requirement-writers",
        "distributions",
        "registry",
        "executables",
        "host-tools",
        "editor-extensions",
        "python.dev-group",
        "python.docs-group",
        "python.test.addopts",
        "store.series",
        "release-notes",
        "job-built-members",
        "checkout-inputs",
    ]
    assert all(spec.extension == "livery.workshop" for spec in found.values())
    assert [name for name, spec in found.items() if spec.level == "workspace"] == [
        "editor-extensions",
        "python.dev-group",
        "python.docs-group",
        "python.test.addopts",
        "store.series",
        "release-notes",
        "job-built-members",
        "checkout-inputs",
    ]
    assert str(found["requirements"].combine) == "livery.workshop._queries:merged"
    assert str(found["test-requirements"].combine) == "livery.workshop._queries:merged"
    assert str(found["job-built-members"].combine) == "livery.workshop._queries:merged"
    assert str(found["executables"].combine) == "livery.workshop._queries:owned"
    assert (
        str(found["checkout-inputs"].combine)
        == "livery.workshop._ci_generate:checkout_inputs"
    )
    assert found["registry"].combine == "agreed"
    # The base's are among every declared query, as the base declares them.
    declared = _queries.declared_queries()
    assert all(declared[name] == spec for name, spec in found.items())


def test_this_workspace_renders_its_dev_group_from_the_query() -> None:
    from livery.workshop._identity import project_facts
    from livery.workshop._templates import render_injections

    root = Path(__file__).resolve().parents[3]
    _this_checkout_or_skip(root)
    injected = render_injections(root, project_facts(root))
    group = injected["queries"]["python.dev-group"]
    assert group, "this workspace lists extensions that ride the dev group"
    text = (root / "pyproject.toml").read_text()
    for requirement in group:
        assert f'    "{requirement}",\n' in text
    # The template spells no tool's line: the extensions' answers do.
    template = (
        root / "packages/workshop/src/livery/workshop/content/root/pyproject.toml.jinja"
    ).read_text()
    assert '"pytest-xdist>=3.6",' not in template
    assert "addopts" not in template
