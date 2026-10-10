"""The private-reaches rule: a source reaching another distribution's privates.

A private is a module or a name with a leading underscore under another
distribution's root, imported (guarded or under ``TYPE_CHECKING``
alike) or read as an attribute of something imported from there. A
test may reach one; a package's source may not, except through the
root contract's ``[[python.private-reaches]]``, each entry with its
reason. An entry no source uses any more refuses, so the list only
shrinks. Each distribution's roots are what its build ships, so the
roots one wheel ships are one distribution's.

The scan reads names, not types: a private read through a value
(``current()._x``) or named in a string (an entry point,
``import_module``) is not seen.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from livery.workshop._ast_rules import ParsedModule, RuleContext
    from livery.workshop._packages import Package

#: Where the allowance lives in the root contract.
ALLOWANCE = "[[python.private-reaches]]"


def private_reaches(
    modules: tuple[ParsedModule, ...], context: RuleContext
) -> list[str]:
    """Each reach outside the allowance, by its place; each allowance entry unused.

    An unused entry is judged only when the run reads every source: a
    narrowed run cannot tell an entry nothing uses from one its scope
    leaves out.
    """
    reaches = scan(modules, context.packages)
    allowed = allowance(context.root)
    problems = [
        f"{place}: {source} reaches {name}, another distribution's private; make"
        f" the name public where it lives, or allow the reach in the root"
        f" contract's {ALLOWANCE} with its reason"
        for (source, name), places in sorted(reaches.items())
        if (source, name) not in allowed
        for place in places
    ]
    if context.files is None:
        problems += [
            f"workshop.toml: {ALLOWANCE} allows {source} to reach {name}, and no"
            " source reaches it any more; delete the entry"
            for source, name in sorted(allowed)
            if (source, name) not in reaches
        ]
    return problems


def scan(
    modules: tuple[ParsedModule, ...], packages: tuple[Package, ...]
) -> dict[tuple[str, str], list[str]]:
    """Every reach in *modules*' sources, by distribution and private, to its places."""
    owners = _owners(packages)
    reaches: dict[tuple[str, str], list[str]] = {}
    for module in modules:
        if module.package is None or module.area != "src":
            continue
        source = module.package.name
        for name, line in _named(module.tree):
            roots = [
                root for root in owners if name == root or name.startswith(root + ".")
            ]
            if not roots:
                continue
            owner = max(roots, key=len)
            below = name[len(owner) :].split(".")
            if owners[owner] == source or not any(map(_private, below)):
                continue
            reaches.setdefault((source, name), []).append(f"{module.relative}:{line}")
    return reaches


def allowance(root: Path) -> dict[tuple[str, str], str]:
    """The root contract's allowed reaches: each distribution and private, to why."""
    from livery.workshop._contract import load_contract

    table = load_contract(root / "workshop.toml").get("python", {})
    entries = table.get("private-reaches", []) if isinstance(table, dict) else []
    return {
        (str(entry["from"]), str(entry["reaches"])): str(entry.get("reason", ""))
        for entry in entries
        if isinstance(entry, dict)
    }


def _owners(packages: tuple[Package, ...]) -> dict[str, str]:
    """Each module root a distribution ships, to the distribution."""
    from livery.workshop import _lifecycle

    return {
        root: package.name
        for package in packages
        for root in _lifecycle.module_roots(package)
    }


def _private(part: str) -> bool:
    return part.startswith("_") and not (part.startswith("__") and part.endswith("__"))


def _dotted(node: ast.expr, imported: dict[str, str]) -> str | None:
    """The dotted path an attribute chain names, when it starts at an import."""
    if isinstance(node, ast.Name):
        return imported.get(node.id)
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value, imported)
        return f"{base}.{node.attr}" if base else None
    return None


def _named(tree: ast.Module) -> list[tuple[str, int]]:
    """Every dotted path *tree* imports or reads through an import, with its line."""
    named: list[tuple[str, int]] = []
    imported: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                named.append((alias.name, node.lineno))
                local = alias.asname or alias.name.partition(".")[0]
                imported[local] = alias.name if alias.asname else local
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for alias in node.names:
                path = f"{node.module}.{alias.name}"
                named.append((path, node.lineno))
                imported[alias.asname or alias.name] = path
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and _private(node.attr):
            read = _dotted(node, imported)
            if read is not None:
                named.append((read, node.lineno))
    return named
