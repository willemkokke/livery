"""The workspace's identity and its members, as the contracts state them.

The root `workshop.toml`'s `[workspace]` table names the project, its
description, its namespace, its authors and its copyright year; each
package's `workshop.toml` names the package, its kind, its description
and the extras its dev-group entry installs, and discovery gives the
extensions its set holds. The renders read these facts under
the names the templates spell (`project_name`, `package_name`, the
`packages` roster), so a template reads the same data whatever stores
it.

Reach for [livery.workshop._identity.project_facts][] for the project's
render data and [livery.workshop._identity.package_facts][] for one
member's.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from livery.workshop._contract import load_contract


def _workspace(root: Path) -> dict[str, Any]:
    path = root / "workshop.toml"
    if not path.is_file():
        return {}
    return cast("dict[str, Any]", load_contract(path).get("workspace") or {})


def is_born(root: Path) -> bool:
    """Whether the workspace at *root* has an identity: its contract names it."""
    return bool(_workspace(root).get("name"))


def project_facts(root: Path) -> dict[str, Any]:
    """The project's identity and roster, under the names the templates spell.

    A fact the contract leaves out takes the default a birth would have
    written: the directory's name, a description and an author derived
    from it, the namespace spelled from the name.
    """
    return {**identity(root), "packages": roster(root)}


def identity(root: Path) -> dict[str, Any]:
    """The project's identity alone, without the roster discovery builds.

    A package's render reads this while a member is still being born,
    before its contract exists for discovery to find.
    """
    workspace = _workspace(root)
    name = str(workspace.get("name") or root.resolve().name)
    authors = cast("list[dict[str, str]]", workspace.get("authors") or [])
    first = authors[0] if authors else {}
    return {
        "project_name": name,
        "project_description": str(
            workspace.get("description") or f"The {name} monorepo (virtual root)."
        ),
        "author_name": str(first.get("name") or f"{name} authors"),
        "author_email": str(first.get("email") or ""),
        "copyright_year": str(workspace.get("copyright-year") or ""),
        "namespace_package": str(
            workspace.get("namespace")
            or name.lower().replace("-", "_").replace(" ", "_")
        ),
    }


def roster(root: Path) -> list[dict[str, Any]]:
    """Each member as the project file lists it, in path order.

    Each carries `extensions`, the extensions its set holds, in
    composition order. A member holding the runtime's language carries
    `dev`, the dev-group entry with its `dev-extras`; any other carries
    none and stays out of the uv workspace.
    """
    from livery.workshop._composition import extension_set, order_or_sorted
    from livery.workshop._extensions import installed_declaration
    from livery.workshop._packages import discover_packages, holds_runtime_language

    if not (root / "packages").is_dir():
        return []
    entries: list[dict[str, Any]] = []
    for package in discover_packages(root):
        entry: dict[str, Any] = {
            "dir": package.member,
            "name": package.name,
            "extensions": list(
                order_or_sorted(extension_set(package), installed_declaration)
            ),
        }
        if holds_runtime_language(package):
            contract = load_contract(package.directory / "workshop.toml")
            extras = cast("list[str]", contract.get("dev-extras") or [])
            entry["dev"] = (
                f"{package.name}[{','.join(extras)}]" if extras else package.name
            )
        entries.append(entry)
    return entries


def python_dirs(root: Path) -> list[str]:
    """The ``src`` and ``tests`` directories that hold a python file.

    Those of each member holding the runtime's language, as
    root-relative posix paths in path order. An extension's ``src`` may
    hold data alone, and a checker such as mypy refuses a named
    directory with no ``.py`` or ``.pyi`` file in it.
    """
    from livery.workshop._packages import discover_packages, holds_runtime_language

    if not (root / "packages").is_dir():
        return []
    found: list[str] = []
    for package in discover_packages(root):
        if not holds_runtime_language(package):
            continue
        for name in ("src", "tests"):
            directory = package.directory / name
            if directory.is_dir() and any(
                next(directory.rglob(f"*{suffix}"), None) for suffix in (".py", ".pyi")
            ):
                found.append(f"packages/{package.member}/{name}")
    return found


def default_description(name: str, project: str) -> str:
    """The one-line description of the package *name* whose contract names none."""
    return f"{name}: a package of the {project} workspace."


def package_facts(root: Path, directory: Path) -> dict[str, Any]:
    """One member's identity, under the names the package templates spell."""
    project = identity(root)
    contract = load_contract(directory / "workshop.toml")
    name = str(contract.get("name") or directory.name)
    return {
        "package_name": name,
        "package_description": str(
            contract.get("description")
            or default_description(name, str(project["project_name"]))
        ),
        "package_dir": directory.relative_to(root / "packages").as_posix(),
        "namespace_package": project["namespace_package"],
        "author_name": project["author_name"],
        "author_email": project["author_email"],
        "copyright_year": project["copyright_year"],
    }
