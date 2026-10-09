"""Queries: the refusals first, then each rule that combines a package's answers."""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.workshop import (
    COMPILE_COMMANDS,
    CURRENT_VERSION,
    EXECUTABLES,
    PUBLIC_MODULES,
    REQUIREMENTS,
    VERSION_FILES,
    _extensions,
    answer,
)
from livery.workshop._declaration import DeclarationError
from livery.workshop._packages import Package
from livery.workshop._queries import QueryError, answers
from workshop_extension_fakes import fake_extensions, fake_steps

#: A package-level extension's identity, the start of every fake here.
PACKAGE = '[extension]\nlevels = ["package"]\n'


def _answering(**answers_by_query: str) -> str:
    """The ``[queries]`` table answering each query with its function in ``_steps``."""
    lines = [
        f'{query} = "{{package}}._steps:{function}"'
        for query, function in answers_by_query.items()
    ]
    return "\n[queries]\n" + "\n".join(lines) + "\n"


def _package(root: Path, *extensions: str) -> Package:
    return Package(
        directory=root / "packages" / "core",
        path="packages/core",
        name="core",
        kind="python",
        depends=(),
        extensions=extensions,
    )


def _fakes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **declared: tuple[str, str]
) -> Package:
    """Fakes for *declared*, each its declaration and its ``_steps`` source.

    Every fake declares every other compatible, and one package lists them all.
    """
    names = [f"acme.{name}" for name in declared]
    texts: dict[str, str] = {}
    for name, (declaration, _source) in declared.items():
        others = [other for other in names if other != f"acme.{name}"]
        listed = ", ".join(f'"{other}"' for other in others)
        texts[name] = declaration.replace(
            PACKAGE, PACKAGE + f"compatible = [{listed}]\n", 1
        )
    fake_extensions(tmp_path, monkeypatch, **texts)
    for name, (_declaration, source) in declared.items():
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
    from livery.workshop._queries import QUERIES

    source = "from pathlib import Path\n\n\ndef answering(package):\n    return {}\n"
    package = _fakes(
        tmp_path,
        monkeypatch,
        a=(PACKAGE + _answering(**{query_name: "answering"}), source.format(first)),
        b=(PACKAGE + _answering(**{query_name: "answering"}), source.format(second)),
    )
    with pytest.raises(QueryError) as raised:
        answer(package, QUERIES[query_name])
    assert str(raised.value) == f"packages/core: {refusal}"


@pytest.mark.parametrize(
    ("declared", "refusal"),
    [
        (
            PACKAGE + _answering(**{"public-module": "public_modules"}),
            "queries.public-module names no query; the queries are public-modules,"
            " compile-commands, module-roots, current-version, version-files,"
            " requirements, distributions, executables; did you mean"
            " 'public-modules'?",
        ),
        (
            _answering(**{"public-modules": "public_modules"}),
            "queries.public-modules answers a question about a package, and this"
            " extension's levels are workspace; add 'package' to levels, or drop"
            " the answer",
        ),
        (
            PACKAGE + _answering(**{"public-modules": "public_module"}),
            "queries.public-modules names public_module, which acme.odd._steps does"
            " not define at its top level; did you mean 'public_modules'?",
        ),
    ],
    ids=["unknown-query", "workspace-level", "no-function"],
)
def test_an_answer_the_file_alone_shows_wrong_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, declared: str, refusal: str
) -> None:
    fake_extensions(tmp_path, monkeypatch, odd=declared)
    fake_steps(tmp_path, "odd", "def public_modules(package):\n    return ()\n")
    with pytest.raises(DeclarationError) as raised:
        _extensions.declaration("acme.odd")
    path = tmp_path / "site" / "acme" / "odd" / "extension.toml"
    assert str(raised.value) == f"{path}: {refusal}"


# The rules.


def test_a_union_holds_each_answer_once_in_composition_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _fakes(
        tmp_path,
        monkeypatch,
        z=(
            PACKAGE
            + 'before = ["acme.a"]\n'
            + _answering(**{"public-modules": "mods"}),
            "def mods(package):\n    return ('z.api', 'shared')\n",
        ),
        a=(
            PACKAGE + _answering(**{"public-modules": "mods"}),
            "def mods(package):\n    return ('shared', 'a.api')\n",
        ),
    )
    assert answer(package, PUBLIC_MODULES) == ("z.api", "shared", "a.api")
    assert answers(package, PUBLIC_MODULES) == {
        "acme.z": ("z.api", "shared"),
        "acme.a": ("shared", "a.api"),
    }
    # A query no extension of the set answers has its empty answer.
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
            PACKAGE + 'after = ["acme.a"]\n' + _answering(**{"public-modules": "mods"}),
            "def mods(package):\n    return ('b.api',)\n",
        ),
        a=(
            PACKAGE + 'after = ["acme.b"]\n' + _answering(**{"public-modules": "mods"}),
            "def mods(package):\n    return ('a.api',)\n",
        ),
    )
    # The layering check names the cycle; the answer is still asked.
    assert answer(package, PUBLIC_MODULES) == ("a.api", "b.api")
