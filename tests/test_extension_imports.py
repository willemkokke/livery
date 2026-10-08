"""Nobody imports an extension but the extensions that require it.

The workshop mounts an extension through its entry point, which no
import names, and a package talks to an extension through data: the
contracts, and the files the extension reads. An extension imports
another only when its extension.toml requires it. A member's source is
held to that; tests are exempt, as the reach test exempts them.

An extension is the module a member's ``workshop.extensions`` entry
point names; its own modules are the ones under that module's
directory, and its name is the entry point's.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _extensions(root: Path) -> dict[str, tuple[str, Path, frozenset[str]]]:
    """Each extension's module, to its name, its directory and what it requires."""
    found: dict[str, tuple[str, Path, frozenset[str]]] = {}
    manifests = [*root.glob("packages/*/pyproject.toml")]
    manifests += root.glob("packages/*/*/pyproject.toml")
    for manifest in sorted(manifests):
        project = tomllib.loads(manifest.read_text(encoding="utf-8"))["project"]
        points = project.get("entry-points", {}).get("workshop.extensions", {})
        for name, module in points.items():
            directory = manifest.parent / "src" / Path(*str(module).split("."))
            declaration = directory / "extension.toml"
            requires: frozenset[str] = frozenset()
            if declaration.is_file():
                identity = tomllib.loads(declaration.read_text(encoding="utf-8")).get(
                    "extension", {}
                )
                requires = frozenset(
                    str(other) for other in identity.get("requires", ())
                )
            found[str(module)] = (str(name), directory, requires)
    return found


def _imported(tree: ast.Module) -> list[tuple[str, int]]:
    """Every module path *tree* imports with its line: a module, each name from it."""
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(alias.name, node.lineno) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append((node.module, node.lineno))
            found += [
                (f"{node.module}.{alias.name}", node.lineno) for alias in node.names
            ]
    return found


def scan(root: Path) -> list[str]:
    """Each import of an extension from outside it that the rule refuses, by place."""
    extensions = _extensions(root)
    sources = [*root.glob("packages/*/src"), *root.glob("packages/*/*/src")]
    problems: dict[tuple[str, int, str], str] = {}
    for src in sorted(sources):
        for path in sorted(src.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError:
                continue  # the syntax gate names it; the scan reads what parses
            importer = next(
                (
                    module
                    for module, (_, directory, _) in extensions.items()
                    if path.is_relative_to(directory)
                ),
                None,
            )
            place = path.relative_to(root).as_posix()
            for name, line in _imported(tree):
                target = next(
                    (
                        module
                        for module in extensions
                        if name == module or name.startswith(f"{module}.")
                    ),
                    None,
                )
                if target is None or target == importer:
                    continue
                wanted = extensions[target][0]
                if importer is not None and wanted in extensions[importer][2]:
                    continue
                problems[place, line, target] = (
                    f"{place}:{line}: imports the extension {wanted} ({target});"
                    " only the workshop mounts an extension, through its entry"
                    " point, and an extension imports another only when its"
                    " extension.toml requires it"
                )
    return [problems[key] for key in sorted(problems)]


def _member(
    root: Path,
    name: str,
    *,
    extension: str = "",
    requires: tuple[str, ...] = (),
    files: dict[str, str],
) -> None:
    directory = root / "packages" / name
    directory.mkdir(parents=True)
    points = ""
    if extension:
        points = (
            f'\n[project.entry-points."workshop.extensions"]\n{name} = "{extension}"\n'
        )
        declaration = directory / "src" / Path(*extension.split(".")) / "extension.toml"
        declaration.parent.mkdir(parents=True)
        listed = ", ".join(f'"{other}"' for other in requires)
        declaration.write_text(
            f"[extension]\napi-version = 1\nrequires = [{listed}]\n", encoding="utf-8"
        )
    (directory / "pyproject.toml").write_text(
        f'[project]\nname = "acme-{name}"\n{points}', encoding="utf-8"
    )
    for relative, text in files.items():
        path = directory / "src" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _workspace(root: Path) -> Path:
    _member(
        root,
        "site",
        extension="acme.extensions.site",
        files={
            "acme/extensions/site/_pages.py": "from acme.extensions.site import _x\n"
        },
    )
    _member(
        root,
        "theme",
        extension="acme.extensions.theme",
        requires=("site",),
        files={
            "acme/extensions/theme/_tasks.py": (
                "from acme.extensions.site._pages import pages\n"
            )
        },
    )
    _member(
        root,
        "lint",
        extension="acme.extensions.lint",
        files={
            "acme/extensions/lint/_checks.py": "import acme.extensions.site._pages\n"
        },
    )
    _member(
        root,
        "bench",
        files={
            "acme/bench/__init__.py": (
                "from acme.extensions import site\n"
                "from acme.extensions.site._pages import pages\n"
            )
        },
    )
    return root


# The refusals first: a package's import of an extension, and an
# extension's import of one it does not require.


def test_an_import_of_an_extension_from_outside_it_refuses_naming_its_place(
    tmp_path: Path,
) -> None:
    assert scan(_workspace(tmp_path)) == [
        "packages/bench/src/acme/bench/__init__.py:1: imports the extension site"
        " (acme.extensions.site); only the workshop mounts an extension, through"
        " its entry point, and an extension imports another only when its"
        " extension.toml requires it",
        "packages/bench/src/acme/bench/__init__.py:2: imports the extension site"
        " (acme.extensions.site); only the workshop mounts an extension, through"
        " its entry point, and an extension imports another only when its"
        " extension.toml requires it",
        "packages/lint/src/acme/extensions/lint/_checks.py:1: imports the extension"
        " site (acme.extensions.site); only the workshop mounts an extension,"
        " through its entry point, and an extension imports another only when its"
        " extension.toml requires it",
    ]


def test_an_extension_imports_its_own_modules_and_one_it_requires(
    tmp_path: Path,
) -> None:
    # site reads its own modules, and theme requires site: neither refuses.
    problems = scan(_workspace(tmp_path))
    assert not [line for line in problems if "/site/" in line or "/theme/" in line]


def test_no_member_imports_an_extension_it_may_not() -> None:
    assert scan(ROOT) == []
