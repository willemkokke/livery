"""Parse each python source once, and say what it references.

The layering phase reads every python source through this module: the
python extension's ``references`` answer and its layering steps,
private-reaches, runner-terminal and declared-imports. ``parsed_source``
parses a file once for a given content, memoised by the file's path,
size and modification time, so the steps, which run in one process,
read one tree, and an unchanged file is never parsed twice.
``parsed_modules`` hands a rule the sources in its scope, every one or
the files a narrowed run selects, `RuleContext` is what a rule judges,
and ``graph_changed`` is what each step's inputs widen on.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

from livery.workshop import ReferencedName

if TYPE_CHECKING:
    from livery.workshop import Changes, Package


@dataclass(frozen=True)
class Parsed:
    """One parsed source.

    Attributes:
        tree: The module's syntax tree.
        references: Every dotted name it imports, once for each place
            it imports it from: at import time or lazily, and under
            which guard.
    """

    tree: ast.Module
    references: tuple[ReferencedName, ...]


_PARSED: dict[tuple[Path, int, int], Parsed] = {}


def parsed_source(path: Path) -> Parsed | None:
    """*path* parsed once for its content; None when it does not parse.

    The memo key is the path with its size and modification time, so
    an edited file parses again and an unchanged one never does.
    """
    try:
        stat = path.stat()
    except OSError:
        return None
    key = (path, stat.st_size, stat.st_mtime_ns)
    found = _PARSED.get(key)
    if found is None:
        found = _parse(path)
        if found is None:
            return None
        _PARSED[key] = found
    return found


def _parse(path: Path) -> Parsed | None:
    """Read and parse *path*: the one call site, which a test may count."""
    try:
        tree = ast.parse(path.read_text("utf-8"), filename=str(path))
    except SyntaxError:
        return None  # the syntax gate names it; the rules read what parses
    return Parsed(tree, references_of(tree))


@dataclass(frozen=True)
class _Where:
    """Where an import statement sits.

    Attributes:
        lazy: Inside a function or a method body.
        type_checking: In the body of ``if TYPE_CHECKING:``.
        optional: In the body of a ``try`` that handles the module's
            absence.
    """

    lazy: bool = False
    type_checking: bool = False
    optional: bool = False


def references_of(tree: ast.Module) -> tuple[ReferencedName, ...]:
    """Every dotted name *tree* imports, in source order, once for each way.

    A ``from`` import references the module and each name imported
    from it: under a PEP 420 namespace the module alone can be the
    namespace, and the package that owns the code is one segment
    further down. A name imported twice the same way is listed once. A
    relative import names the source's own package and is left out.

    An import inside a function or a method body is lazy: importing the
    module does not run it. One in the body of ``if TYPE_CHECKING:`` is
    made by a type checker alone. One in the body of a ``try`` with a
    handler for ``ImportError``, ``ModuleNotFoundError`` or every
    exception is optional: the source runs without the module.

    Only a statement imports, so the walk reads the statements and
    never descends into an expression.
    """
    found: dict[ReferencedName, None] = {}
    pending = [(statement, _Where()) for statement in reversed(tree.body)]
    while pending:
        statement, where = pending.pop()
        if isinstance(statement, ast.Import):
            names = [alias.name for alias in statement.names]
        elif isinstance(statement, ast.ImportFrom):
            module = statement.module
            names = (
                [module, *(f"{module}.{alias.name}" for alias in statement.names)]
                if module and statement.level == 0
                else []
            )
        else:
            pending.extend(reversed(list(_inside(statement, where))))
            continue
        for name in names:
            found.setdefault(
                ReferencedName(
                    name,
                    lazy=where.lazy,
                    type_checking=where.type_checking,
                    optional=where.optional,
                )
            )
    return tuple(found)


def _inside(statement: ast.stmt, where: _Where) -> Iterator[tuple[ast.stmt, _Where]]:
    """The statements *statement* holds, in source order, each with where it sits."""
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
        where = replace(where, lazy=True)
    for field, value in ast.iter_fields(statement):
        if not isinstance(value, list):
            continue
        held = where
        if field == "body":
            held = replace(
                where,
                type_checking=where.type_checking or _only_for_type_checking(statement),
                optional=where.optional or _handles_absence(statement),
            )
        for item in cast("list[object]", value):
            if isinstance(item, ast.stmt):
                yield item, held
            elif isinstance(item, (ast.ExceptHandler, ast.match_case)):
                # A handler's body and a case's body sit where the
                # statement does: a try's guard covers its own body alone.
                for inner in item.body:
                    yield inner, where


def _only_for_type_checking(statement: ast.stmt) -> bool:
    """Whether *statement* is ``if TYPE_CHECKING:``, the name bare or qualified."""
    if not isinstance(statement, ast.If):
        return False
    test = statement.test
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _handles_absence(statement: ast.stmt) -> bool:
    """Whether *statement* is a ``try`` with a handler for an absent module.

    A handler for ``ImportError``, ``ModuleNotFoundError`` or every
    exception says the code runs without what the body imports, which
    is a declaration of its own and needs no other.
    """
    if not isinstance(statement, ast.Try):
        return False
    return any(
        handler.type is None
        or (
            isinstance(handler.type, ast.Name)
            and handler.type.id in ("ImportError", "ModuleNotFoundError")
        )
        for handler in statement.handlers
    )


@dataclass(frozen=True)
class RuleContext:
    """What a layering rule over the python sources judges: the workspace and a scope.

    Attributes:
        root: The workspace root.
        packages: Every package the workspace carries.
        files: The root-relative sources the run judges; None for every
            one.
    """

    root: Path
    packages: tuple[Package, ...]
    files: frozenset[str] | None = None

    def packages_in_scope(self) -> tuple[Package, ...]:
        """The packages holding a source this run judges, every one without a scope."""
        if self.files is None:
            return self.packages
        files = self.files
        return tuple(
            package
            for package in self.packages
            if any(path.startswith(f"{package.path}/") for path in files)
        )


@dataclass(frozen=True)
class ParsedModule:
    """One python source as a rule sees it.

    Attributes:
        package: The package the file belongs to; None for the root's
            own ``tasks.py``.
        path: The file, absolute.
        relative: The file, relative to the workspace root, posix.
        area: ``src``, ``tests``, or ``root``.
        dotted: The module's dotted path under ``src``; empty elsewhere.
        tree: The syntax tree.
    """

    package: Package | None
    path: Path
    relative: str
    area: str
    dotted: str
    tree: ast.Module


def parsed_modules(
    root: Path, packages: tuple[Package, ...], files: frozenset[str] | None = None
) -> tuple[ParsedModule, ...]:
    """Every python source the workspace holds, or the named ones, parsed once.

    The root's own ``tasks.py``, then each package's ``src`` and
    ``tests`` trees in package order. *files* keeps the root-relative
    sources it names, and a named file that no longer exists is left
    out. A file that does not parse is left out: the syntax gate names
    it.
    """

    def wanted(source: Path) -> bool:
        return files is None or source.relative_to(root).as_posix() in files

    found: list[ParsedModule] = []
    tasks = root / "tasks.py"
    if tasks.is_file() and wanted(tasks):
        _collect(found, None, tasks, root, "root", tasks.parent)
    for package in packages:
        for area in ("src", "tests"):
            base = package.directory / area
            if not base.is_dir():
                continue
            if files is not None:
                prefix = f"{package.path}/{area}/"
                named = sorted(
                    root / path
                    for path in files
                    if path.startswith(prefix)
                    and path.endswith(".py")
                    and (root / path).is_file()
                )
                for source in named:
                    _collect(found, package, source, root, area, base)
                continue
            for source in sorted(base.rglob("*.py")):
                _collect(found, package, source, root, area, base)
    return tuple(found)


def dotted_name(source: Path, src: Path) -> str:
    """The dotted module path of *source*, a file under the ``src`` tree *src*.

    ``livery/forge/_dev/__init__.py`` under ``src`` is ``livery.forge._dev``.
    """
    dotted = ".".join(source.relative_to(src).parts)
    return dotted.removesuffix(".py").removesuffix(".__init__")


def _collect(
    found: list[ParsedModule],
    package: Package | None,
    source: Path,
    root: Path,
    area: str,
    base: Path,
) -> None:
    parsed = parsed_source(source)
    if parsed is None:
        return
    found.append(
        ParsedModule(
            package=package,
            path=source,
            relative=source.relative_to(root).as_posix(),
            area=area,
            dotted=dotted_name(source, base) if area == "src" else "",
            tree=parsed.tree,
        )
    )


def graph_changed(changes: Changes) -> bool:
    """Whether a contract, a native manifest or a root ``src/`` changed.

    The layering steps' inputs widen on them: the distributions' roots
    and declarations come from the manifests, and the allowances from
    the root contract, so a change there judges every source, as the
    base's layering steps do. The workshop answers it
    ([livery.workshop.graph_changed][]); a step's ``widen`` reference
    names the extension's own code, so this function hands it on.
    """
    from livery.workshop import graph_changed as workspace_graph_changed

    return workspace_graph_changed(changes)
