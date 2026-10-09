"""Ask a package a typed question that its package-level extensions answer.

A package-level extension answers questions about a package in its
``extension.toml``, ``[queries]``: a query's name to the function that
answers it, called with the package. [livery.workshop.answer][] asks each
extension of the package's set that answers, in composition order, and
combines the answers by the query's rule: their union, the one value
they agree on, or the nearest answer by requires. The workshop defines
the queries, each a [livery.workshop.Query][] typed by its answer.
"""

from __future__ import annotations

import types
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generic, TypeVar, cast

if TYPE_CHECKING:
    from livery.workshop._composition import Lookup
    from livery.workshop._packages import Package

T = TypeVar("T")
Item = TypeVar("Item")


class QueryError(RuntimeError):
    """A package's extensions answer a query in ways its rule cannot combine."""


@dataclass(frozen=True)
class Query(Generic[T]):
    """A typed question about a package, answered by its extensions under ``[queries]``.

    Attributes:
        name: The key an extension answers it under: ``public-modules``.
        empty: The answer when no extension of the package answers.
        combine: How the answers, by extension in composition order,
            make the package's one answer. It raises
            [livery.workshop._queries.QueryError][] naming the
            extensions when the answers break its rule.
    """

    name: str
    empty: T
    combine: Callable[[Mapping[str, T], Lookup], T]


def _union(answers: Mapping[str, tuple[Item, ...]], lookup: Lookup) -> tuple[Item, ...]:
    """Every item any answer holds, once each, in the order first met."""
    del lookup
    return tuple(dict.fromkeys(item for value in answers.values() for item in value))


def _agreed(answers: Mapping[str, str], lookup: Lookup) -> str:
    """The one value every answer that is not empty holds; empty when none does."""
    del lookup
    named = {name: value for name, value in answers.items() if value}
    if len(set(named.values())) > 1:
        listed = ", ".join(f"{name} {value!r}" for name, value in named.items())
        raise QueryError(f"the answers differ, and they must agree: {listed}")
    return next(iter(named.values()), "")


def _merged(
    answers: Mapping[str, Mapping[str, str]], lookup: Lookup
) -> Mapping[str, str]:
    """Every name any answer holds, to its one value; a name valued twice refuses."""
    del lookup
    merged: dict[str, str] = {}
    by: dict[str, str] = {}
    for name, value in answers.items():
        for key, held in value.items():
            if key in merged and merged[key] != held:
                raise QueryError(
                    f"{by[key]} answers {key} {merged[key]!r} and {name} answers"
                    f" {held!r}; the answers name each entry once, or alike"
                )
            merged[key] = held
            by.setdefault(key, name)
    return merged


def _nearest(answers: Mapping[str, Path | None], lookup: Lookup) -> Path | None:
    """The answer of the one extension no other answering one requires.

    Its answer is the nearest: it builds on the others. None when no
    extension answers with a value.
    """
    from livery.workshop._composition import implied

    named = {name: value for name, value in answers.items() if value is not None}
    required = implied(named, lookup)
    nearest = [name for name in named if name not in required]
    if len(nearest) > 1:
        raise QueryError(
            f"{' and '.join(nearest)} both answer, and neither requires the other;"
            " one of them answers, or one requires the other"
        )
    return named[nearest[0]] if nearest else None


def _owned(answers: Mapping[str, tuple[str, ...]], lookup: Lookup) -> tuple[str, ...]:
    """Every item any answer holds; an item two extensions answer refuses."""
    del lookup
    owners: dict[str, str] = {}
    for name, value in answers.items():
        for item in value:
            if item in owners and owners[item] != name:
                raise QueryError(
                    f"{owners[item]} and {name} both answer {item}; one extension"
                    " of a package answers each"
                )
            owners[item] = name
    return tuple(owners)


#: The modules that declare the package's public API, by import path:
#: what a type-completeness check verifies. The union of the answers.
PUBLIC_MODULES: Query[tuple[str, ...]] = Query("public-modules", (), _union)

#: Where the package's build writes its compilation database, which a
#: tool such as clang-tidy reads. The nearest answer by requires.
COMPILE_COMMANDS: Query[Path | None] = Query("compile-commands", None, _nearest)

#: The names other packages reference this one's code by. The union of
#: the answers.
MODULE_ROOTS: Query[tuple[str, ...]] = Query("module-roots", (), _union)

#: The version the package's own manifests declare. Every answer that is
#: not empty agrees on it.
CURRENT_VERSION: Query[str] = Query("current-version", "", _agreed)

#: Every file the ``stamp`` phase may write a version into, so a caller
#: can keep and restore them. The union of the answers.
VERSION_FILES: Query[tuple[Path, ...]] = Query("version-files", (), _union)

#: What the package's native manifests require, each distribution's name
#: to its constraint. The union of the answers; a name two answers
#: constrain differently refuses.
REQUIREMENTS: Query[Mapping[str, str]] = Query(
    "requirements", types.MappingProxyType({}), _merged
)

#: The distributions the package releases, each as its ecosystem and its
#: name. The union of the answers.
DISTRIBUTIONS: Query[tuple[tuple[str, str], ...]] = Query("distributions", (), _union)

#: The executables ``fm run`` may start, by name. The union of the
#: answers; a name two extensions answer refuses.
EXECUTABLES: Query[tuple[str, ...]] = Query("executables", (), _owned)

#: Every query the workshop defines, by the name an extension answers it
#: under.
QUERIES: Mapping[str, Query[Any]] = {
    query.name: query
    for query in (
        PUBLIC_MODULES,
        COMPILE_COMMANDS,
        MODULE_ROOTS,
        CURRENT_VERSION,
        VERSION_FILES,
        REQUIREMENTS,
        DISTRIBUTIONS,
        EXECUTABLES,
    )
}


def answers(
    package: Package, query: Query[T], *, lookup: Lookup | None = None
) -> dict[str, T]:
    """Each extension of *package*'s set that answers *query*, to its answer.

    In composition order, each declaration read through *lookup*, the
    installed ones when absent. A set whose declared order has no
    start is asked alphabetically; the layering check names the cycle.
    """
    from livery.workshop._composition import OrderCycle, order, package_set

    if lookup is None:
        from livery.workshop._extensions import installed_declaration

        lookup = installed_declaration
    members = package_set(package.extensions, lookup)
    try:
        walk = order(members, lookup)
    except OrderCycle:
        walk = tuple(sorted(members))
    found: dict[str, T] = {}
    for name in walk:
        declared = lookup(name)
        reference = declared.queries.get(query.name) if declared is not None else None
        if reference is not None:
            found[name] = cast("T", reference(package))
    return found


def answer(package: Package, query: Query[T], *, lookup: Lookup | None = None) -> T:
    """*package*'s answer to *query*: its extensions' answers, combined by its rule.

    With no extension answering, the query's empty answer.

    Raises:
        QueryError: when the answers break the query's rule, naming the
            package, the query and the extensions.
    """
    if lookup is None:
        from livery.workshop._extensions import installed_declaration

        lookup = installed_declaration
    found = answers(package, query, lookup=lookup)
    if not found:
        return query.empty
    try:
        return query.combine(found, lookup)
    except QueryError as error:
        raise QueryError(f"{package.path}: {query.name}: {error}") from None
