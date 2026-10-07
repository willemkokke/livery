"""What the docs extension reads from a workspace's contracts.

The root contract's ``[docs]`` table, which the extension owns; the
generators a package declares under ``[docs] generators``, with the
system packages each needs on a docs machine; and the seam the site
publishes through. The extension's declaration file names
`docs_requirements` as both jobs' ``installs`` and `publish_seam` as
the deploy's ``deploy``, so the CI render asks them and reads no
``[docs]`` table itself. The site's assembly reads the same two.
"""

from __future__ import annotations

from pathlib import Path

from livery.footman import fail
from livery.workshop import Package, discover_packages, read_contract

#: Where the site may publish: the forge's own hosting, a container, a
#: host over ssh, or nowhere.
SEAMS = ("pages", "container", "ssh", "none")

#: The publish seam each forge kind defaults to.
DEFAULT_SEAMS = {"github": "pages", "gitlab": "pages", "gitea": "container"}


def docs_table(root: Path) -> dict[str, object]:
    """The root contract's ``[docs]`` table; empty when undeclared."""
    contract = read_contract(root)
    table = contract.get("docs") or {}
    return dict(table) if isinstance(table, dict) else {}


def package_generators(package: Package) -> list[tuple[str, tuple[str, ...]]]:
    """The docs generators *package* declares: (verb, requirements) each.

    A package's ``workshop.toml`` ``[docs]`` table lists them under
    ``generators``: a verb name (a footman task the package ships),
    or a table naming the verb and the system tools the generator
    needs on a docs machine (``{ verb = "...", requires = [...] }``).
    Anything else refuses naming the file and the entry. Empty
    without a declaration.
    """
    contract_path = package.directory / "workshop.toml"
    contract = read_contract(package.directory)
    table = contract.get("docs") or {}
    declared = table.get("generators") if isinstance(table, dict) else None
    if declared is None:
        return []
    if not isinstance(declared, list):
        fail(f"{contract_path}: [docs] generators must be a list")
    generators: list[tuple[str, tuple[str, ...]]] = []
    for entry in declared:
        if isinstance(entry, str) and entry:
            generators.append((entry, ()))
            continue
        if isinstance(entry, dict) and isinstance(entry.get("verb"), str):
            requires = entry.get("requires", [])
            if isinstance(requires, list) and all(
                isinstance(tool, str) for tool in requires
            ):
                generators.append((entry["verb"], tuple(requires)))
                continue
        fail(
            f"{contract_path}: [docs] generators entry {entry!r} is not a"
            ' verb name or a { verb = "...", requires = [...] } table'
        )
    return generators


def docs_requirements(root: Path) -> tuple[str, ...]:
    """The union of every declared generator's system requirements, sorted.

    What the site's CI jobs install before they build. Empty for a
    workspace without a ``packages`` directory.
    """
    if not (root / "packages").is_dir():
        return ()
    union: set[str] = set()
    for package in discover_packages(root):
        for _verb, requires in package_generators(package):
            union.update(requires)
    return tuple(sorted(union))


def publish_seam(root: Path) -> str:
    """The seam the site publishes through: pages, container, ssh, or none.

    The root contract's ``[docs] publish`` wins, one of `SEAMS`, which
    the contract's judge holds it to; without it the forge kind picks
    its default from `DEFAULT_SEAMS`.
    """
    contract = read_contract(root)
    table = contract.get("docs") or {}
    declared = str(table.get("publish", "")) if isinstance(table, dict) else ""
    if declared:
        return declared
    kind = str((contract.get("forge") or {}).get("kind", ""))
    return DEFAULT_SEAMS.get(kind, "none")
