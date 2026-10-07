"""No package's source reaches another distribution's private names.

A private is a module or a name with a leading underscore under another
distribution's root, imported (guarded or under TYPE_CHECKING alike) or
read as an attribute of something imported from there. A test may reach
one; a package's source may not, except through ALLOWED, each entry
with its reason, and an entry no source uses any more refuses, so the
list only shrinks. The distributions and their roots come from each
member's build backend, so the workshop's wheel, which ships
livery.workshop and livery.extensions.docs, is one distribution.

The scan reads names, not types: a private read through a value
(`current()._x`) or named in a string (an entry point, import_module)
is not seen.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Today's reaches, by the reaching distribution and the private it
#: names, each with the public seam that replaces it.
ALLOWED: dict[tuple[str, str], str] = {
    ("livery-toolroom", "livery.footman._context.Invocation"): (
        "the hosted lane asks its host for the run: footman's host(), phase 10d"
    ),
    ("livery-toolroom", "livery.footman._context._current"): (
        "the hosted lane asks whether a task runs: footman's host(), phase 10d"
    ),
    ("livery-toolroom", "livery.footman._context._target_cwd"): (
        "the hosted lane asks for a call's directory: footman's host(), phase 10d"
    ),
    ("livery-toolroom", "livery.footman._globals"): (
        "the hosted lane reads the run's cwd and argv: footman's host(), phase 10d"
    ),
    ("livery-toolroom-bench", "livery.footman._describe.bold"): (
        "styled output: footman's colored(text, style=...), phase 10d"
    ),
    ("livery-toolroom-bench", "livery.footman._describe.cyan"): (
        "styled output: footman's colored(text, style=...), phase 10d"
    ),
    ("livery-toolroom-bench", "livery.footman._describe.wants_color"): (
        "whether to colour: footman's public wants_color(), phase 10d"
    ),
    ("livery-toolroom-bench", "livery.footman._globals"): (
        "whether a task runs: footman's host(), phase 10d"
    ),
    ("livery-toolroom-bench", "livery.toolroom.tools._colordata"): (
        "the probed colour table: toolroom's colour_controls(), phase 10d"
    ),
    ("livery-toolroom-bench", "livery.toolroom.tools._NEGATIONS"): (
        "the stub generator reads the handles' negation table; no seam designed yet"
    ),
    ("livery-toolroom-bench", "livery.toolroom.tools._WRAPPERS"): (
        "the stub generator reads the handles' wrapper table; no seam designed yet"
    ),
    ("livery-toolroom-bench", "livery.toolroom.tools._console_entrypoint"): (
        "a driver asks whether a tool is a console script; no seam designed yet"
    ),
    ("livery-toolroom-bench", "livery.toolroom.store._engine.download"): (
        "a plain download: footman's public fetch, phase 10d"
    ),
    ("livery-workshop", "livery.footman._config"): (
        "the project's builtin families: footman's project_builtins(root), phase 10d"
    ),
    ("livery-workshop", "livery.footman._paths"): (
        "the brand's variables and file names: footman's builtins(),"
        " directory_variable() and tasks_file_name(), phase 10d"
    ),
    ("livery-workshop", "livery.forge._registry.purge_gitlab_packages"): (
        "the loop deletes published versions: the forge's admin protocol, phase 15"
    ),
    ("livery-workshop", "livery.forge._registry.purge_packages"): (
        "the loop deletes published versions: the forge's admin protocol, phase 15"
    ),
}


def _private(part: str) -> bool:
    return part.startswith("_") and not (part.startswith("__") and part.endswith("__"))


def _members(root: Path) -> dict[Path, tuple[str, list[str]]]:
    """Each member's directory, to its distribution and the roots its build ships."""
    found: dict[Path, tuple[str, list[str]]] = {}
    manifests = [*root.glob("packages/*/pyproject.toml")]
    manifests += root.glob("packages/*/*/pyproject.toml")
    for manifest in sorted(manifests):
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
        backend = data.get("tool", {}).get("uv", {}).get("build-backend", {})
        declared = backend.get("module-name")
        modules = [declared] if isinstance(declared, str) else list(declared or [])
        found[manifest.parent] = (data["project"]["name"], modules)
    return found


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


def scan(root: Path) -> dict[tuple[str, str], list[str]]:
    """Every reach under *root*, by distribution and private, to its places."""
    members = _members(root)
    owners = {module: name for name, modules in members.values() for module in modules}
    reaches: dict[tuple[str, str], list[str]] = {}
    for directory, (source, _modules) in members.items():
        for path in sorted((directory / "src").rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError:
                continue  # the syntax gate names it; the scan reads what parses
            for name, line in _named(tree):
                roots = [m for m in owners if name == m or name.startswith(m + ".")]
                if not roots:
                    continue
                module = max(roots, key=len)
                below = name[len(module) :].split(".")
                if owners[module] == source or not any(map(_private, below)):
                    continue
                place = f"{path.relative_to(root).as_posix()}:{line}"
                reaches.setdefault((source, name), []).append(place)
    return reaches


def judge(
    reaches: dict[tuple[str, str], list[str]], allowed: dict[tuple[str, str], str]
) -> list[str]:
    """What the scan refuses: each reach outside *allowed*, each entry unused."""
    problems = [
        f"{place}: {source} reaches {name}, another distribution's private;"
        " make the name public where it lives, or allow it here with its reason"
        for (source, name), places in sorted(reaches.items())
        if (source, name) not in allowed
        for place in places
    ]
    problems += [
        f"ALLOWED: {source} no longer reaches {name}; delete the entry"
        for source, name in sorted(allowed)
        if (source, name) not in reaches
    ]
    return problems


def _member(root: Path, name: str, modules: list[str], files: dict[str, str]) -> None:
    directory = root / "packages" / name
    directory.mkdir(parents=True)
    names = ", ".join(f'"{module}"' for module in modules)
    (directory / "pyproject.toml").write_text(
        f'[project]\nname = "acme-{name}"\n\n'
        f"[tool.uv.build-backend]\nmodule-name = [{names}]\n",
        encoding="utf-8",
    )
    for relative, text in files.items():
        path = directory / "src" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


REACHING = """\
from typing import TYPE_CHECKING

import acme.alpha._impl
import acme.alpha as alpha
import acme.alpha.public
from acme.alpha import _impl, thing, __version__
from acme.alpha._impl import helper
from acme.alpha.public import _hidden
from acme.beta import _own
from . import _local

try:
    from acme.alpha import _guarded
except ImportError:
    pass
if TYPE_CHECKING:
    from acme.alpha._types import Shape

alpha._secret
acme.alpha.public._deep.value
alpha.__doc__
"""


def _reaching(tmp_path: Path) -> Path:
    _member(tmp_path, "alpha", ["acme.alpha"], {"acme/alpha/__init__.py": ""})
    _member(
        tmp_path,
        "beta",
        ["acme.beta", "acme.extensions.gamma"],
        {
            "acme/beta/__init__.py": REACHING,
            "acme/beta/_own.py": "",
            "acme/extensions/gamma/_checks.py": "from acme.beta import _own\n",
        },
    )
    return tmp_path


# The refusals first: each form the scan must see.


def test_every_form_of_a_reach_is_seen_with_its_line(tmp_path: Path) -> None:
    reaches = scan(_reaching(tmp_path))
    where = "packages/beta/src/acme/beta/__init__.py"
    assert reaches == {
        ("acme-beta", "acme.alpha._impl"): [f"{where}:3", f"{where}:6"],
        ("acme-beta", "acme.alpha._impl.helper"): [f"{where}:7"],
        ("acme-beta", "acme.alpha.public._hidden"): [f"{where}:8"],
        ("acme-beta", "acme.alpha._guarded"): [f"{where}:13"],
        ("acme-beta", "acme.alpha._types.Shape"): [f"{where}:17"],
        ("acme-beta", "acme.alpha._secret"): [f"{where}:19"],
        ("acme-beta", "acme.alpha.public._deep"): [f"{where}:20"],
    }


def test_a_reach_outside_the_allowance_refuses_naming_its_place(
    tmp_path: Path,
) -> None:
    reaches = scan(_reaching(tmp_path))
    allowed = {key: "a reason" for key in reaches if key[1] != "acme.alpha._secret"}
    assert judge(reaches, allowed) == [
        "packages/beta/src/acme/beta/__init__.py:19: acme-beta reaches"
        " acme.alpha._secret, another distribution's private; make the name"
        " public where it lives, or allow it here with its reason"
    ]


def test_an_allowance_no_source_uses_refuses(tmp_path: Path) -> None:
    reaches = scan(_reaching(tmp_path))
    allowed = dict.fromkeys(reaches, "a reason")
    allowed["acme-beta", "acme.alpha._gone"] = "a reason"
    assert judge(reaches, allowed) == [
        "ALLOWED: acme-beta no longer reaches acme.alpha._gone; delete the entry"
    ]


def test_a_distribution_reaches_its_own_privates_freely(tmp_path: Path) -> None:
    # gamma ships in beta's wheel, so its reach into beta is no reach.
    reaches = scan(_reaching(tmp_path))
    assert not any(name.startswith("acme.beta") for _, name in reaches)


def test_this_repository_reaches_nothing_beyond_its_allowance() -> None:
    assert judge(scan(ROOT), ALLOWED) == []
