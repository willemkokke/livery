"""The tools a workspace requires: three declaration sites, one lock.

A tool requirement is a name with a floor, `ruff` or `ruff>=0.16`, and
three sites declare them: a package kind, in its record, for the tools
its checks run; a package instance, in its `workshop.toml` under
`[tools] requires`, for what its kind cannot know; and the project, in
the root contract's `[tools] requires`, for what belongs to the
repository. The sites' requirements union, and `tools.lock` at the
root holds one version per tool for the whole repository, the newest
the catalogue lists that satisfies every floor and resolves on every
locked host. The root contract's `[tools] hosts` names those hosts,
the three gated ones by default, and `[tools] index` names where the
catalogue is read from: the published index by URL, a directory
holding one, or the records that build one.
"""

from __future__ import annotations

from pathlib import Path

from livery.footman import fail
from livery.toolroom.store import (
    LOCK_FILE,
    POINTER,
    TOOL_FILE,
    Catalogue,
    CatalogueError,
    Home,
    Lock,
    LockError,
    Requirement,
    resolve_lock,
)
from livery.workshop._contract import load_contract
from livery.workshop._kinds import kind_chain
from livery.workshop._packages import discover_packages

TOOLS = "tools"
"""The contract table the requirements, the hosts and the index live under."""

DEFAULT_HOSTS = ("linux-x64", "macos-arm", "windows-x64")
"""The hosts locked for unless the contract names others: the gated three."""


def tools_table(path: Path) -> dict[str, object]:
    """The `[tools]` table of the contract at *path*; empty when absent."""
    if not path.is_file():
        return {}
    table = load_contract(path).get(TOOLS) or {}
    if not isinstance(table, dict):
        fail(f"{path}: [tools] is not a table")
    return dict(table)


def _requires(table: dict[str, object], *, site: str) -> list[Requirement]:
    declared = table.get("requires", [])
    if not isinstance(declared, list) or not all(isinstance(r, str) for r in declared):
        fail(f"{site}: [tools] requires is not a list of strings")
    try:
        return [Requirement.parse(text, site=site) for text in declared]
    except LockError as error:
        fail(str(error))


def requirements(root: Path) -> tuple[Requirement, ...]:
    """Every requirement the three sites declare: kinds, packages, then the project.

    A workspace with no package types requires what the python kind
    does: its own `tasks.py` runs on python.
    """
    packages = discover_packages(root) if (root / "packages").is_dir() else ()
    types = {package.type for package in packages} or {"python"}
    found: list[Requirement] = []
    for type_name in sorted(types):
        for record in kind_chain(type_name):
            for text in record.tools:
                found.append(Requirement.parse(text, site=f"kind {record.name}"))
    for package in packages:
        contract = package.directory / "workshop.toml"
        found += _requires(tools_table(contract), site=f"{package.path}/workshop.toml")
    found += _requires(tools_table(root / "workshop.toml"), site="workshop.toml")
    return tuple(found)


def tool_names(root: Path) -> tuple[str, ...]:
    """The tools the sites require, each once, in the order first declared."""
    return tuple(dict.fromkeys(requirement.name for requirement in requirements(root)))


def locked_hosts(root: Path) -> tuple[str, ...]:
    """The hosts the repository locks for: `[tools] hosts`, or the gated three."""
    declared = tools_table(root / "workshop.toml").get("hosts")
    if declared is None:
        return DEFAULT_HOSTS
    if not isinstance(declared, list) or not all(isinstance(h, str) for h in declared):
        fail("workshop.toml: [tools] hosts is not a list of strings")
    return tuple(declared)


def index_source(root: Path) -> str:
    """Where the catalogue is read from, as `[tools] index` names it.

    A URL is read as the published index; a path, relative to the root,
    is a directory holding an index or the records that build one.
    """
    declared = tools_table(root / "workshop.toml").get("index")
    if not isinstance(declared, str) or not declared:
        fail(
            "workshop.toml: [tools] index names no source; set it to the published"
            " index's URL, or to a directory holding an index or the records"
        )
    if "://" in declared:
        return declared
    return str(root / declared)


def catalogue(root: Path, *, offline: bool = False) -> Catalogue:
    """The catalogue the repository resolves against, from `[tools] index`.

    A directory of records is read as the authoring site reads it; an
    index, by URL or directory, through the machine's store, which
    keeps what it fetched so a second read is offline.
    """
    source = index_source(root)
    if "://" not in source:
        directory = Path(source)
        if not (directory / POINTER).is_file() and any(
            (child / TOOL_FILE).is_file()
            for child in (directory.iterdir() if directory.is_dir() else ())
        ):
            return Catalogue.of_records(directory)
    try:
        return Catalogue.of_index(source, home=_home(), offline=offline)
    except CatalogueError as error:
        fail(str(error))


def _home() -> Home:
    from livery.footman.context import data_dir

    return Home(data_dir() / "toolroom")


def lock_path(root: Path) -> Path:
    """The lock's file, `tools.lock` at the root."""
    return root / LOCK_FILE


def current_lock(root: Path) -> Lock | None:
    """The lock as committed, or `None` when the repository has none yet."""
    path = lock_path(root)
    if not path.is_file():
        return None
    try:
        return Lock.load(path)
    except LockError as error:
        fail(str(error))


def write_lock(root: Path, *, upgrade: tuple[str, ...] = ()) -> Lock:
    """Resolve the sites' requirements and write the lock; the lock written.

    An entry the lock already holds stands unless it is named in
    *upgrade* or no longer satisfies; a refusal names the tool, each
    floor with its site, and the host at fault.
    """
    try:
        lock = resolve_lock(
            catalogue(root),
            requirements(root),
            hosts=locked_hosts(root),
            keep=current_lock(root),
            upgrade=upgrade,
        )
    except LockError as error:
        fail(str(error))
    lock.save(lock_path(root))
    return lock


def declare(root: Path, text: str) -> bool:
    """Add *text* to the project's `[tools] requires`; whether the contract changed.

    A requirement already declared at the project site, in the same
    spelling, is left as it is. The contract is edited in place: the
    `[tools]` table gains the entry, or is added at the end with it.
    """
    try:
        Requirement.parse(text, site="workshop.toml")
    except LockError as error:
        fail(str(error))
    path = root / "workshop.toml"
    declared = tools_table(path).get("requires", [])
    if isinstance(declared, list) and text in declared:
        return False
    source = path.read_text(encoding="utf-8") if path.is_file() else ""
    lines = source.split("\n")
    header = next(
        (n for n, line in enumerate(lines) if line.strip() == f"[{TOOLS}]"), None
    )
    if header is None:
        trimmed = source.rstrip("\n")
        path.write_text(
            (trimmed + "\n\n" if trimmed else "")
            + f'[{TOOLS}]\nrequires = ["{text}"]\n',
            encoding="utf-8",
        )
        return True
    end = next(
        (n for n in range(header + 1, len(lines)) if lines[n].startswith("[")),
        len(lines),
    )
    for n in range(header + 1, end):
        if lines[n].split("=", 1)[0].strip() == "requires":
            if lines[n].rstrip().endswith("]"):
                body = lines[n].split("=", 1)[1].strip()[1:-1].strip()
                items = f"{body}, " if body else ""
                lines[n] = f'requires = [{items}"{text}"]'
            else:
                close = next(m for m in range(n + 1, end) if lines[m].strip() == "]")
                lines.insert(close, f'    "{text}",')
            path.write_text("\n".join(lines), encoding="utf-8")
            return True
    lines.insert(header + 1, f'requires = ["{text}"]')
    path.write_text("\n".join(lines), encoding="utf-8")
    return True
