"""The workspace's identity and its members, as the contracts state them.

The root `workshop.toml`'s `[workspace]` table names the project, its
description, its namespace, its authors and its copyright year; each
package's `workshop.toml` names the package, its kind, its description,
the extras its dev-group entry installs, and the template it renders
from when that differs from its kind's own. The renders read these
facts under the names the templates spell (`project_name`,
`package_name`, the `packages` roster), so a template reads the same
data whatever stores it.

Reach for [livery.workshop._identity.project_facts][] for the project's
render data and [livery.workshop._identity.package_facts][] for one
member's.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from livery.workshop._contract import load_contract
from livery.workshop._contract_keys import Declared

DECLARED: tuple[Declared, ...] = (
    Declared("root", "workspace.name", ("str",)),
    Declared("root", "workspace.description", ("str",)),
    Declared("root", "workspace.namespace", ("str",)),
    Declared("root", "workspace.authors", ("list",)),
    Declared("root", "workspace.authors[]", ("table",)),
    Declared("root", "workspace.authors[].name", ("str",)),
    Declared("root", "workspace.authors[].email", ("str",)),
    Declared("root", "workspace.copyright-year", ("str",)),
    Declared("package", "description", ("str",)),
    Declared("package", "dev-extras", ("strs",)),
    Declared("package", "template", ("str",)),
)
"""The contract keys this module reads."""


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
        "kind": "project",
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


def _python_based(kind: str) -> bool:
    """Whether a package of *kind* is a uv workspace member: python down its chain."""
    from livery.workshop._kinds import is_python_kind, kind_names

    return kind in kind_names() and is_python_kind(kind)


def roster(root: Path) -> list[dict[str, str]]:
    """Each member as the project file lists it, in path order.

    A python member carries `dev`, the dev-group entry with its
    `dev-extras`; a native one carries none and stays out of the uv
    workspace.
    """
    from livery.workshop._packages import discover_packages

    if not (root / "packages").is_dir():
        return []
    entries: list[dict[str, str]] = []
    for package in discover_packages(root):
        entry = {
            "dir": package.member,
            "name": package.name,
            "kind": package.kind,
        }
        if _python_based(package.kind):
            contract = load_contract(package.directory / "workshop.toml")
            extras = cast("list[str]", contract.get("dev-extras") or [])
            entry["dev"] = (
                f"{package.name}[{','.join(extras)}]" if extras else package.name
            )
        entries.append(entry)
    return entries


def template_of(directory: Path) -> str:
    """The template a package renders from: its `template`, or its kind's own."""
    from livery.workshop._kinds import kind_for

    contract = load_contract(directory / "workshop.toml")
    declared = contract.get("template")
    if declared:
        return str(declared)
    return kind_for(str(contract["kind"])).template


def package_facts(root: Path, directory: Path) -> dict[str, Any]:
    """One member's identity, under the names the package templates spell."""
    project = identity(root)
    contract = load_contract(directory / "workshop.toml")
    name = str(contract.get("name") or directory.name)
    return {
        "kind": template_of(directory),
        "package_name": name,
        "package_description": str(
            contract.get("description")
            or f"{name}: a {project['project_name']} workspace package."
        ),
        "package_dir": directory.relative_to(root / "packages").as_posix(),
        "namespace_package": project["namespace_package"],
        "author_name": project["author_name"],
        "author_email": project["author_email"],
        "copyright_year": project["copyright_year"],
    }
