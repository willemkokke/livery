"""Ask a typed question that extensions answer, combined by the query's declared rule.

A query is declared, by the base in the ``[queries]`` section of its
``contract.toml`` and by an extension under ``[queries.<name>]`` in its
``extension.toml``, in one grammar ([livery.workshop._queries.QuerySpec][]):
``combine``, how the answers make one, ``union``, ``nearest``, ``agreed``
or a function ``module:name``; ``level``, ``package`` for a question
about one package and ``workspace`` for one about the workspace;
``default``, the answer while no extension answers; and ``values``, the
only answers it takes. [livery.workshop._queries.declared_queries][] is
every declared query by name, the base's and then each installed
extension's; two extensions declaring one query refuse naming both.

An extension answers a query under ``[queries]`` in its declaration
file: ``<name> = "module:function"``, a function called with the
package or the workspace root, or a literal, ``[queries.<name>] value``.
[livery.workshop.answer][] asks a package's set in composition order,
or the listed extensions in list order, and combines the answers by
the query's rule. A handle, [livery.workshop.Query][], names a query
and types its answer; the base's handles are the constants here, and
an extension names its own with ``Query("docs.members")``.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generic, TypeVar, cast

if TYPE_CHECKING:
    from livery.workshop._composition import Lookup
    from livery.workshop._declaration import Reference
    from livery.workshop._packages import Package

T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)

#: The levels a query is asked at: about one package, or about the
#: workspace.
PACKAGE_LEVEL = "package"
WORKSPACE_LEVEL = "workspace"
LEVELS = (PACKAGE_LEVEL, WORKSPACE_LEVEL)

#: The rules a query may combine its answers by, beside a function.
UNION = "union"
NEAREST = "nearest"
AGREED = "agreed"

#: The extension that declares the base's queries.
BASE = "livery.workshop"

#: How answers, by extension, make one answer; the lookup reads the
#: declarations a rule needs, the requirements for ``nearest``.
Combine = Callable[[Mapping[str, Any], "Lookup"], Any]


class QueryError(RuntimeError):
    """A query asked or answered in a way its declaration cannot take.

    A name no extension declares, a question asked at the other level,
    an answer off the query's values, or answers its rule cannot
    combine; the message names the query and the extensions.
    """


@dataclass(frozen=True)
class Query(Generic[T_co]):
    """A handle naming a declared query, typed by its answer.

    The handle is covariant in its answer: one typed by a tuple serves
    a reader that takes any iterable.

    Attributes:
        name: The key an extension answers it under: ``public-modules``.
    """

    name: str


@dataclass(frozen=True)
class QuerySpec:
    """A query as its declarer declares it: one ``[queries.<name>]`` table.

    Attributes:
        name: The key an extension answers it under: ``public-modules``.
        combine: How the answers make one. ``union``: every item any
            answer holds, once each, in the order first met, each
            answer a list. ``nearest``: the answer of the one extension
            no other answering one requires. ``agreed``: the one value
            every answer that is not empty holds. Or a function, called
            with the answers by extension and the declarations' lookup,
            which raises [livery.workshop._queries.QueryError][] or
            ``ValueError`` naming the extensions when the answers break
            its rule.
        level: ``package`` for a question about one package, asked with
            the package and answered by its set in composition order;
            ``workspace`` for one about the workspace, asked with its
            root and answered by the listed extensions in list order.
        default: The answer while no extension answers; None takes the
            rule's answer over no answers, an empty union, no nearest,
            no agreement, a function's answer over nothing.
        values: The only answers the query takes; None takes any.
        extension: The extension that declares it; the base for its own.
    """

    name: str
    combine: str | Reference = UNION
    level: str = PACKAGE_LEVEL
    default: object = None
    values: tuple[object, ...] | None = None
    extension: str = BASE


def _union(answers: Mapping[str, Any], lookup: Lookup) -> tuple[Any, ...]:
    """Every item any answer holds, once each, in the order first met."""
    del lookup
    found: dict[Any, None] = {}
    for name, value in answers.items():
        if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
            raise QueryError(
                f"{name} answers {value!r}, and a union combines lists; answer a list"
            )
        found.update(dict.fromkeys(value))
    return tuple(found)


def _agreed(answers: Mapping[str, Any], lookup: Lookup) -> Any:
    """The one value every answer that is not empty holds; empty when none does."""
    del lookup
    named = {name: value for name, value in answers.items() if value}
    if len({repr(value) for value in named.values()}) > 1:
        listed = ", ".join(f"{name} {value!r}" for name, value in named.items())
        raise QueryError(f"the answers differ, and they must agree: {listed}")
    return next(iter(named.values()), "")


def _nearest(answers: Mapping[str, Any], lookup: Lookup) -> Any:
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


def merged(answers: Mapping[str, Any], lookup: Lookup) -> Mapping[str, str]:
    """Every name any answer holds, to its one value; a name valued twice refuses.

    The ``requirements`` query's rule, which the base's contract file
    names.
    """
    del lookup
    found: dict[str, str] = {}
    by: dict[str, str] = {}
    for name, value in answers.items():
        for key, held in cast("Mapping[str, str]", value).items():
            if key in found and found[key] != held:
                raise QueryError(
                    f"{by[key]} answers {key} {found[key]!r} and {name} answers"
                    f" {held!r}; the answers name each entry once, or alike"
                )
            found[key] = held
            by.setdefault(key, name)
    return found


def owned(answers: Mapping[str, Any], lookup: Lookup) -> tuple[str, ...]:
    """Every item any answer holds; an item two extensions answer refuses.

    The ``executables`` query's rule, which the base's contract file
    names.
    """
    del lookup
    owners: dict[str, str] = {}
    for name, value in answers.items():
        for item in cast("Iterable[str]", value):
            if item in owners and owners[item] != name:
                raise QueryError(
                    f"{owners[item]} and {name} both answer {item}; one extension"
                    " of a package answers each"
                )
            owners[item] = name
    return tuple(owners)


#: Each rule by its name in a declaration.
RULES: Mapping[str, Combine] = {UNION: _union, NEAREST: _nearest, AGREED: _agreed}

#: The modules that declare the package's public API, by import path:
#: what a type-completeness step verifies.
PUBLIC_MODULES: Query[tuple[str, ...]] = Query("public-modules")

#: Where the package's build writes its compilation database, which a
#: tool such as clang-tidy reads.
COMPILE_COMMANDS: Query[Path | None] = Query("compile-commands")

#: The names other packages reference this one's code by.
MODULE_ROOTS: Query[tuple[str, ...]] = Query("module-roots")

#: The version the package's own manifests declare.
CURRENT_VERSION: Query[str] = Query("current-version")

#: Every file the ``stamp`` phase may write a version into, so a caller
#: can keep and restore them.
VERSION_FILES: Query[tuple[Path, ...]] = Query("version-files")

#: What the package's native manifests require, each distribution's name
#: to its constraint.
REQUIREMENTS: Query[Mapping[str, str]] = Query("requirements")

#: The distributions the package releases, each as its ecosystem and its
#: name.
DISTRIBUTIONS: Query[tuple[tuple[str, str], ...]] = Query("distributions")

#: The executables ``fm run`` may start, by name.
EXECUTABLES: Query[tuple[str, ...]] = Query("executables")

#: The marketplace ids of the editor extensions the listed extensions
#: recommend, which the rendered ``.vscode/extensions.json`` lists.
EDITOR_EXTENSIONS: Query[tuple[str, ...]] = Query("editor-extensions")


@functools.cache
def base_queries() -> dict[str, QuerySpec]:
    """The base's queries, read from the ``[queries]`` section of its contract file.

    In the file's order, each spec naming the base as its declarer.

    Raises:
        DeclarationError: when a section is off the shape a query's
            declaration has.
    """
    import tomllib

    from livery.workshop._contract_keys import BASE_FILE
    from livery.workshop._declaration import query_specs_of

    data = tomllib.loads(BASE_FILE.read_text("utf-8"))
    return query_specs_of(data, BASE_FILE, extension=BASE, package=BASE)


@functools.cache
def declared_queries() -> dict[str, QuerySpec]:
    """Every declared query by name: the base's, then each installed extension's.

    An installed extension's declaration is read whether the workspace
    lists it or not, so an answer to its query reads the same anywhere;
    a query nobody listed declares is asked by nobody.

    Raises:
        DeclarationError: when two extensions declare one query.
    """
    from livery.footman import installed_entry_points
    from livery.workshop._declaration import DeclarationError, query_declarations

    found = dict(base_queries())
    for entry in installed_entry_points("workshop.extensions"):
        for spec in query_declarations(entry.name, entry.value.partition(":")[0]):
            held = found.get(spec.name)
            if held is not None and held.extension != spec.extension:
                both = " and ".join(sorted((held.extension, spec.extension)))
                raise DeclarationError(
                    f"the {spec.name} query is declared by both {both}; one"
                    " extension declares a query"
                )
            found[spec.name] = spec
    return found


def query_spec(name: str) -> QuerySpec:
    """The declared query *name*; refuses naming the declared ones.

    Raises:
        QueryError: when no extension declares *name*.
    """
    specs = declared_queries()
    found = specs.get(name)
    if found is None:
        raise QueryError(
            f"{name} is not a declared query; the queries are {', '.join(specs)}"
        )
    return found


def _walk(subject: Package | Path, spec: QuerySpec, lookup: Lookup) -> tuple[str, ...]:
    """The extensions asked *spec* about *subject*, in the order they answer.

    A package's set in composition order, or the listed extensions in
    list order; a set whose declared order has no start is asked
    alphabetically, and ``layering.graph`` names the cycle.

    Raises:
        QueryError: when *subject* is at the other level from the query.
    """
    if isinstance(subject, Path):
        if spec.level != WORKSPACE_LEVEL:
            raise QueryError(
                f"{spec.name} is a question about a package, which {spec.extension}"
                " declares at the package level; ask it with the package"
            )
        from livery.workshop._extensions import extension_names

        return extension_names(subject)
    if spec.level != PACKAGE_LEVEL:
        raise QueryError(
            f"{spec.name} is a question about the workspace, which {spec.extension}"
            " declares at the workspace level; ask it with the workspace root"
        )
    from livery.workshop._composition import OrderCycle, order, package_set

    members = package_set(subject.extensions, lookup)
    try:
        return order(members, lookup)
    except OrderCycle:
        return tuple(sorted(members))


def answers(
    subject: Package | Path, query: Query[T], *, lookup: Lookup | None = None
) -> dict[str, T]:
    """Each extension that answers *query* about *subject*, to its answer.

    *subject* is the package for a package-level query, and the
    workspace root, or a directory in the workspace, for a
    workspace-level one. Each declaration is read through *lookup*,
    the installed ones when absent.

    Raises:
        QueryError: when no extension declares the query, when *subject*
            is at the other level, or when an answer is off the query's
            values, naming the extension.
    """
    from livery.workshop._declaration import DeclaredValue

    if lookup is None:
        from livery.workshop._extensions import installed_declaration

        lookup = installed_declaration
    spec = query_spec(query.name)
    found: dict[str, T] = {}
    for name in _walk(subject, spec, lookup):
        declared = lookup(name)
        held = declared.queries.get(query.name) if declared is not None else None
        if held is None:
            continue
        value = held.value if isinstance(held, DeclaredValue) else held(subject)
        if spec.values is not None and value not in spec.values:
            listed = ", ".join(repr(known) for known in spec.values)
            raise QueryError(
                f"{name} answers {query.name} with {value!r}, whose values are {listed}"
            )
        found[name] = cast("T", value)
    return found


def answer(
    subject: Package | Path, query: Query[T], *, lookup: Lookup | None = None
) -> T:
    """The answer to *query* about *subject*: the extensions' answers, combined.

    *subject* is the package for a package-level query, answered by its
    set in composition order, and the workspace root, or a directory in
    the workspace, for a workspace-level one, answered by the listed
    extensions in list order. With no extension answering, the query's
    declared default, or its rule's answer over nothing.

    Raises:
        QueryError: when no extension declares the query, when *subject*
            is at the other level, when an answer is off the query's
            values, or when the answers break the query's rule; the
            message names the query and the extensions, and a package's
            path for a package-level query.
    """
    if lookup is None:
        from livery.workshop._extensions import installed_declaration

        lookup = installed_declaration
    spec = query_spec(query.name)
    found = answers(subject, query, lookup=lookup)
    if not found and spec.default is not None:
        return cast("T", spec.default)
    combine: Combine = (
        RULES[spec.combine] if isinstance(spec.combine, str) else spec.combine
    )
    where = "" if isinstance(subject, Path) else f"{subject.path}: "
    try:
        return cast("T", combine(found, lookup))
    except (QueryError, ValueError) as error:
        raise QueryError(f"{where}{query.name}: {error}") from None


def workspace_answers(root: Path, *, lookup: Lookup | None = None) -> dict[str, object]:
    """Every workspace-level query's answer about *root*, by name.

    A rendered template reads ``queries["python.dev-group"]`` through
    it; a name no extension declares at the workspace level is absent.
    """
    return {
        name: answer(root, Query(name), lookup=lookup)
        for name, spec in declared_queries().items()
        if spec.level == WORKSPACE_LEVEL
    }
