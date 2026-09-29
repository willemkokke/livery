"""The layering check's one source parse, and the rules that read it.

Every python source the layering check judges is parsed once for a
given content: the parse is memoised by the file's path, size and
modification time, so the rules that walk their own file lists and a
rule a kind or a layer registers all read the same tree. A registered
rule receives every parsed module once and returns its problems; a
rule may carry a fix, which the layering check runs inside its own
rewrite, and the judgments run inside the check's judge, so the gate
walk sees one check whatever a layer registers.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from livery.workshop._packages import Package


@dataclass(frozen=True)
class Parsed:
    """One parsed source.

    Attributes:
        tree: The module's syntax tree.
        imports: The dotted names it imports, guarded ones left out.
    """

    tree: ast.Module
    imports: tuple[str, ...]


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
    return Parsed(tree, tuple(imports_of(tree)))


def imports_of(tree: ast.AST) -> list[str]:
    """The dotted names *tree* imports, guarded ones left out.

    A ``from`` import contributes the module and each imported name
    under it: under a PEP 420 namespace the module alone can be the
    namespace, and the package that owns the code is one segment
    further down.
    """
    guarded = _guarded_imports(tree)
    names: list[str] = []
    for node in ast.walk(tree):
        if id(node) in guarded:
            continue
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.append(node.module)
            names += [f"{node.module}.{alias.name}" for alias in node.names]
    return names


def _guarded_imports(tree: ast.AST) -> set[int]:
    """The ids of import nodes under a ``try`` that catches an absent module.

    An import written that way says the code runs without what it
    imports, which is a declaration of its own and needs no other.
    """
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        catches = any(
            handler.type is None
            or (
                isinstance(handler.type, ast.Name)
                and handler.type.id in ("ImportError", "ModuleNotFoundError")
            )
            for handler in node.handlers
        )
        if not catches:
            continue
        for statement in node.body:
            for inner in ast.walk(statement):
                if isinstance(inner, (ast.Import, ast.ImportFrom)):
                    guarded.add(id(inner))
    return guarded


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
        imports: The dotted names it imports, guarded ones left out.
    """

    package: Package | None
    path: Path
    relative: str
    area: str
    dotted: str
    tree: ast.Module
    imports: tuple[str, ...]


@dataclass(frozen=True)
class RuleContext:
    """What a rule may read beside the modules.

    Attributes:
        root: The workspace root.
        packages: Every package the workspace carries.
    """

    root: Path
    packages: tuple[Package, ...]


Judge = Callable[[tuple[ParsedModule, ...], RuleContext], list[str]]


@dataclass(frozen=True)
class AstRule:
    """One rule over the parsed sources.

    Attributes:
        name: The rule's name, which prefixes each problem it reports.
        judge: Receives every parsed module once and the context;
            returns the problems, empty when the rule holds.
        fix: Runs inside the layering check's rewrite, before the
            judge; returns the lines it wrote, printed as the check's.
        layer: The layer that registered the rule; empty for a
            builtin one.
    """

    name: str
    judge: Judge
    fix: Judge | None = None
    layer: str = ""


_RULES: dict[str, AstRule] = {}


def register_ast_rule(rule: AstRule) -> None:
    """Register *rule*; a name already registered is replaced.

    Raises:
        ValueError: when the rule has no name.
    """
    if not rule.name:
        raise ValueError("an AST rule needs a name; it prefixes the rule's problems")
    _RULES[rule.name] = rule


def unregister_ast_rule(name: str) -> None:
    """Withdraw the rule named *name*; nothing when there is none."""
    _RULES.pop(name, None)


def ast_rules() -> tuple[AstRule, ...]:
    """Every registered rule, in registration order."""
    return tuple(_RULES.values())


def parsed_modules(
    root: Path, packages: tuple[Package, ...]
) -> tuple[ParsedModule, ...]:
    """Every python source the workspace holds, parsed once.

    The root's own ``tasks.py``, then each package's ``src`` and
    ``tests`` trees in package order. A file that does not parse is
    left out: the syntax gate names it.
    """
    found: list[ParsedModule] = []
    tasks = root / "tasks.py"
    if tasks.is_file():
        _collect(found, None, tasks, root, "root", tasks.parent)
    for package in packages:
        for area in ("src", "tests"):
            base = package.directory / area
            if not base.is_dir():
                continue
            for source in sorted(base.rglob("*.py")):
                _collect(found, package, source, root, area, base)
    return tuple(found)


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
    dotted = ""
    if area == "src":
        dotted = ".".join(source.relative_to(base).parts)
        dotted = dotted.removesuffix(".py").removesuffix(".__init__")
    found.append(
        ParsedModule(
            package=package,
            path=source,
            relative=source.relative_to(root).as_posix(),
            area=area,
            dotted=dotted,
            tree=parsed.tree,
            imports=parsed.imports,
        )
    )
