"""The tools a workspace requires: four declaration sites, one lock, receipts.

A tool requirement is a name with a floor, `git` or `git>=2.40`, and
four sites declare them: a package kind, in its record, for the tools
its checks run; a listed extension, as `WORKSHOP_TOOLS` on its plugin
module, for what its own verbs need; a package instance, in its
`workshop.toml` under `[toolroom] requires`, for what its kind cannot
know; and the project, in the root contract's `[toolroom] requires`,
for what belongs to the repository. The sites' requirements union, and
`toolroom.lock` at the root holds one version per tool for the whole
repository, the newest the catalogue lists that satisfies every floor
and resolves on every supported host. The root contract's
`[workspace] hosts` names those hosts, every host key when it is
absent, and `[toolroom] index` names where the catalogue is read from:
the published index by URL, a directory holding one, or the records
that build one.

Entering the environment materialises the bundle the sites require, on
`fm sync` and on `fm toolroom.add`: each locked tool is supplied through
the machine's store in one of three modes, its entry points linked
into the checkout's bin directory, its own directories on PATH, or no
PATH at all, and a receipt under `.workshop/receipts/` records the
exact version, the host, the deployment's digest and what reached
PATH. A receipt is a checkout's statement of what it installed, as the
release train's receipt is a release's; `fm env.check` reads them
back and names the drift when the lock's deployment has moved under
one.

The stubs the type checkers read are materialised too, into `typings/`
at the root, pyright's default stub path and a search path the rendered
configuration hands the other three checkers: one rendering per tool
the lock holds, at the locked version, as `livery.toolroom.stubs`
modules with `livery.toolroom.handles` beside them declaring each
handle, which the installed tools package's index imports. Both live
under the `livery.toolroom` namespace package, beside the tools package
and never inside its directory: a tree inside it would shadow the
package for a checker run on explicit paths. The store renders each
stub from the locked version's own surface, from the records or from
the index, so no source holds a stub. A tool the workspace does not
deploy gets no stub. `fm toolroom.restub` writes them, and so do `fm sync`
and every lock verb.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from livery.strongroom import Source
    from livery.toolroom.store import (
        Catalogue,
        Deployment,
        Ensured,
        Graph,
        Home,
        Listed,
        Lock,
        Locked,
        Requirement,
    )

import hashlib
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, cast

from livery.footman import Failed, fail, prog, run
from livery.workshop._contract import load_contract
from livery.workshop._kinds import kind_chain
from livery.workshop._packages import discover_packages

WORKSPACE = "workspace"
"""The contract table the supported hosts live under."""


DEFAULT_HOSTS: tuple[str, ...] = ()
"""The hosts supported when `[workspace] hosts` is absent; empty for
every host key, which the store names and which is read only then."""

VERDICT_ROLES = ("format", "lint", "typecheck", "typecomplete", "test")
"""The check roles whose tools take no host allowance: the version is the verdict."""

PINNED_TOOLS = ("uv",)
"""The tools no allowance reaches by name: the entry pins uv."""

ROOT_KIND = "python"
"""The kind of the workspace's own files, whatever its members are.

`tasks.py` and the root's tests run on the base's runtime, and the
checks of that kind judge them, so the tools those checks run are
required with no member of the kind present.
"""

TYPINGS = "typings"
"""The directory under the root the stubs are written into, pyright's default."""

TYPINGS_PACKAGE = ("livery", "toolroom")
"""The namespace package the stubs and handles live under, beside the tools package."""


def tools_table(path: Path) -> dict[str, object]:
    """The `[toolroom]` table of the contract at *path*, judged; empty when absent."""
    from livery.toolroom.store import TABLE

    if not path.is_file():
        return {}
    return dict(load_contract(path).get(TABLE) or {})


def _requires(table: dict[str, object], *, site: str) -> list[Requirement]:
    from livery.toolroom.store import LockError, Requirement

    declared = cast("list[str]", table.get("requires", []))
    try:
        return [Requirement.parse(text, site=site) for text in declared]
    except LockError as error:
        fail(str(error))


def requirements(root: Path) -> tuple[Requirement, ...]:
    """Every requirement the six sites declare, in site order.

    A workspace requires what `ROOT_KIND` does with or without a
    member of it: its own `tasks.py` and tests run on it, and the
    checks of that kind judge them. A kind's checks bring the tools
    they run, each requirement naming `check <name>` as its site. An
    extension's site is `extension <name>`, read from its
    ``extension.toml``'s ``[toolroom] requires``; an unlisted extension declares nothing
    here, since listing is the only activation channel. A plugin the
    project mounts through its direct dependencies is `plugin <name>`,
    read from its entry module
    ([livery.workshop._tools.plugin_tools][]).
    """
    from livery.toolroom.store import LockError, Requirement
    from livery.workshop._extensions import extension_tools

    packages = discover_packages(root) if (root / "packages").is_dir() else ()
    kinds = {package.kind for package in packages} | {ROOT_KIND}
    found: list[Requirement] = []
    for kind_name in sorted(kinds):
        for record in kind_chain(kind_name):
            for text in record.tools:
                found.append(Requirement.parse(text, site=f"kind {record.name}"))
    # The checks that judge each present kind bring their tools, each
    # naming its check: unregistering a check removes its tool.
    from livery.workshop._checks import tools_for_kind

    for kind_name in sorted(kinds):
        for tool, check in tools_for_kind(kind_name):
            found.append(Requirement.parse(tool, site=f"check {check}"))
    try:
        declared_by_extension = extension_tools(root)
    except RuntimeError as error:
        fail(str(error))
    for extension, declared in declared_by_extension.items():
        try:
            found += [
                Requirement.parse(text, site=f"extension {extension}")
                for text in declared
            ]
        except LockError as error:
            fail(str(error))
    for plugin, declared in plugin_tools(root).items():
        try:
            found += [
                Requirement.parse(text, site=f"plugin {plugin}") for text in declared
            ]
        except LockError as error:
            fail(str(error))
    for package in packages:
        contract = package.directory / "workshop.toml"
        found += _requires(tools_table(contract), site=f"{package.path}/workshop.toml")
    found += _requires(tools_table(root / "workshop.toml"), site="workshop.toml")
    from livery.workshop._lfs import TOOL, lfs_enabled

    if lfs_enabled(root):
        found.append(Requirement.parse(TOOL, site="workshop.toml [workspace] lfs"))
    return tuple(found)


def plugin_tools(root: Path) -> dict[str, tuple[str, ...]]:
    """The tools each plugin the project mounts declares, by its entry point name.

    The plugins are the ``footman.builtin`` names the project's direct
    dependencies offer, as footman's project rung mounts them. A plugin
    declares the tools its verbs need in a data module that its
    ``workshop.tools`` entry point names, under the plugin's own name:
    ``"livery.forge" = "livery.forge._dev_tools:TOOLS"``, a tuple of
    requirement strings, ``("docker?",)`` for one it can do without.
    Loading it imports that module alone, never the plugin's tasks.

    Raises:
        Failed: when a declaration is not a tuple or list of strings,
            naming the plugin.
    """
    from livery.footman import installed_entry_points, project_builtins

    declared = {entry.name: entry for entry in installed_entry_points("workshop.tools")}
    found: dict[str, tuple[str, ...]] = {}
    for name in project_builtins(root):
        entry = declared.get(name)
        if entry is None:
            continue
        value: object = entry.load()
        if not isinstance(value, (tuple, list)) or not all(
            isinstance(text, str)
            for text in value  # pyright: ignore[reportUnknownVariableType]
        ):
            fail(
                f"plugin {name}: its workshop.tools entry point ({entry.value}) is"
                ' not a tuple of requirement strings, ("docker?",)'
            )
        found[name] = tuple(str(text) for text in value)  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    return found


def host_allowed(root: Path) -> tuple[str, ...]:
    """The tools whose copy on the machine may serve: the contract's and the kinds'.

    The root contract's list and the present kinds' `host_allowed`
    union, `ROOT_KIND` always among them, sorted. A name no site
    requires yet is kept and named by the lock as an allowance ahead
    of its requirement: a workspace may allow `cmake` before its first
    C++ package arrives, and the lock's line is where a misspelling
    shows. A tool a check reads its verdict
    from refuses, naming the checks that read it: a linter that varies
    by machine makes the gate disagree with CI. `uv` refuses by name,
    since the entry pins it. A tool an extension that writes release
    notes requires refuses, naming the extension: a release's entries
    must not depend on the machine. A download whose executable is
    not named like the tool, or that has none, refuses too: the
    allowance finds the tool on PATH by its name.
    """
    from livery.toolroom.store import LockError, Requirement
    from livery.workshop._checks import check_for, tools_for_kind
    from livery.workshop._extensions import notes_tools
    from livery.workshop._kinds import kind_host_allowed

    declared = cast(
        "list[str]", tools_table(root / "workshop.toml").get("host-allowed", [])
    )
    packages = discover_packages(root) if (root / "packages").is_dir() else ()
    kinds = {package.kind for package in packages} | {ROOT_KIND}
    names = tuple(sorted({*declared, *kind_host_allowed(kinds)}))
    if not names:
        return ()
    verdicts: dict[str, set[str]] = {}
    for kind_name in sorted(kinds):
        for tool, check in tools_for_kind(kind_name):
            if check_for(check).role in VERDICT_ROLES:
                verdicts.setdefault(tool, set()).add(check)
    writers: dict[str, str] = {}
    for extension, requires in notes_tools(root).items():
        for line in requires:
            try:
                parsed = Requirement.parse(line, site=f"extension {extension}")
            except LockError as error:
                fail(str(error))
            writers[parsed.name] = extension
    for name in names:
        where = f"workshop.toml: [toolroom] host-allowed names {name}"
        if name in PINNED_TOOLS:
            fail(f"{where}, which takes no allowance: the entry pins {name}")
        if name in writers:
            fail(
                f"{where}, which takes no allowance: extension {writers[name]}"
                " writes the release notes with it"
            )
        if name in verdicts:
            readers = sorted(verdicts[name])
            fail(
                f"{where}, whose version is a verdict: {', '.join(readers)}"
                f" {'reads' if len(readers) == 1 else 'read'} it"
            )
    return names


def unrequired_allowances(root: Path) -> tuple[str, ...]:
    """The host-allowed names no site requires yet, sorted: allowances ahead of need."""
    required = {requirement.name for requirement in requirements(root)}
    return tuple(name for name in host_allowed(root) if name not in required)


def _host_probe_gap(listing: Catalogue, name: str, version: str, host: str) -> str:
    """Why *name* cannot be found on PATH by its own name; empty when it can."""
    from livery.toolroom.store import DOWNLOAD_KINDS, CatalogueError, RecordError

    listed = listing.listed(name)
    if listed.kind not in DOWNLOAD_KINDS:
        return ""
    try:
        deployment = listing.deployment(name, version, host)
    except (CatalogueError, RecordError):
        return ""
    if not deployment.entry_points:
        return (
            f"{name} has no executable; the allowance finds a tool on PATH by its name"
        )
    first = Path(deployment.entry_points[0]).name
    if first.lower().removesuffix(".exe") != name:
        return (
            f"{name}'s executable is {first}; the allowance finds a tool on PATH by"
            " its name"
        )
    return ""


def tool_names(root: Path, host: str = "") -> tuple[str, ...]:
    """The tools the sites require, each once, in the order first declared.

    With *host*, the tools required there: a requirement scoped to other
    hosts alone (``dotnet_coverage@windows``) requires nothing on it.
    """
    return tuple(
        dict.fromkeys(
            requirement.name
            for requirement in requirements(root)
            if not host or requirement.on((host,))
        )
    )


def this_host() -> str:
    """This machine's host key as the store names it: ``macos-arm``."""
    import platform

    from livery.toolroom.store import host_key

    return host_key(platform.system(), platform.machine())


def supported_hosts(root: Path) -> tuple[str, ...]:
    """The hosts the workspace supports and locks for, in host-key order.

    `[workspace] hosts` names them in the requirement scope tokens:
    a platform (`macos`), a host key (`linux-arm`), and either with `!`
    to remove it, a list of removals alone starting from every host.
    Absent, every host key is supported.

    Raises a refusal for a token that is neither a platform nor a host
    key, and for a list that is empty or leaves no host.
    """
    from livery.toolroom.store import HOSTS, Scope, SpecError

    every = DEFAULT_HOSTS or HOSTS
    path = root / "workshop.toml"
    table = load_contract(path).get(WORKSPACE) if path.is_file() else None
    declared = cast("dict[str, object]", table or {}).get("hosts")
    if declared is None:
        return every
    where = "workshop.toml: [workspace] hosts"
    tokens = cast("list[str]", declared)
    if not tokens:
        fail(f"{where} is empty; name the hosts, or drop the key for every host")
    try:
        scope = Scope.parse(",".join(tokens), where=where, spelled=str(tokens))
    except SpecError as error:
        fail(str(error))
    hosts = scope.hosts(every)
    if not hosts:
        fail(f"{where} {tokens} leaves no host; name at least one host")
    return hosts


def index_source(root: Path) -> str:
    """Where the catalogue is read from, as `[toolroom] index` names it.

    A URL is read as the published index; a path, relative to the root,
    is a directory holding an index or the records that build one.
    """
    declared = tools_table(root / "workshop.toml").get("index")
    if not isinstance(declared, str) or not declared:
        fail(
            "workshop.toml: [toolroom] index names no source; set it to the published"
            " index's URL, or to a directory holding an index or the records"
        )
    if "://" in declared:
        return declared
    return str(root / declared)


def has_index(root: Path) -> bool:
    """Whether the root contract names a catalogue source at all."""
    declared = tools_table(root / "workshop.toml").get("index")
    return isinstance(declared, str) and bool(declared)


def receipt_gap(tool: str, receipt: Receipt, bin_dir: Path) -> str:
    """Why what *receipt* claims does not hold here; empty when it does.

    A receipt is the store's own record of what it installed for this
    checkout, so it is the thing to check: looking for an executable by
    a tool's name instead answers a different question, and answers it
    wrongly for a tool that is not a program.

    What a receipt claims depends on how the tool was supplied. One
    that puts directories on PATH claims they exist and that each entry
    point runs from one of them, the venv's own bin, or PATH, since a
    name is not always a binary's (``git_cliff`` installs
    ``git-cliff``). One that carries environment values claims each
    value that is a path is there: the cmake-conan provider is a file
    CMake reads, and no program by that name exists anywhere. One that
    claims nothing was verified on the host rather than installed, so
    the host must still answer for it.

    Returns:
        The first claim that does not hold, as a line to print after
        the tool's name, or empty when every claim does.
    """
    for spelled in receipt.paths:
        if not Path(spelled).is_dir():
            return f"MISSING (the receipt's path {spelled} is not there)"
    for name in receipt.entry_points:
        if not _entry_point_runs(name, receipt, bin_dir):
            return f"MISSING (the receipt names {name}, which does not run)"
    for key, value in sorted(receipt.env.items()):
        # A value that is a path is checked, and a path is judged by
        # `is_absolute`: a Windows receipt names `C:\...`, which starts
        # with a drive and not a slash.
        named = Path(value)
        if named.is_absolute() and not named.exists():
            return f"MISSING ({key} names {value}, which is not there)"
    # A receipt claiming nothing was verified on the host rather than
    # installed, so the host has to answer for the tool now.
    claims_nothing = not (receipt.paths or receipt.entry_points or receipt.env)
    if claims_nothing and not (shutil.which(tool) or (bin_dir / tool).is_file()):
        return "MISSING (verified on the host, and not on PATH now)"
    return ""


def _entry_point_runs(name: str, receipt: Receipt, bin_dir: Path) -> bool:
    """Whether *name* runs: from the receipt's paths, the venv's bin, or PATH."""
    own = (Path(spelled) / name for spelled in receipt.paths)
    return (
        any(candidate.is_file() for candidate in own)
        or (bin_dir / name).is_file()
        or bool(shutil.which(name))
    )


def store_cannot_supply(root: Path) -> str:
    """Why the store supplies nothing here; empty when it can.

    The sites require tools whatever a contract says, because a kind
    brings its own: a workspace with no packages still runs on the
    python kind, whose gate checkers come from the store. Nothing is
    supplied until a lock names versions, and nothing can be locked
    until the contract names a catalogue to resolve against, so the
    two are one question with one answer a reader can act on.

    Empty when a lock is present, and empty for a workspace that
    requires no tool at all.
    """
    required = tool_names(root)
    if not required or current_lock(root) is not None:
        return ""
    named = ", ".join(required[:4])
    rest = f", and {len(required) - 4} more" if len(required) > 4 else ""
    lines = [
        f"the tools this workspace requires are not locked: {named}{rest}."
        " Nothing is materialised from a store without a lock, so the"
        " first verb that reaches for one fails with no executable found."
    ]
    if not has_index(root):
        lines += [
            "",
            "Name the catalogue to resolve against in workshop.toml:",
            "",
            "  [toolroom]",
            '  index = "<the published index\'s URL, or a directory of records>"',
        ]
    lines += [
        "",
        f"Then `{prog()} toolroom.lock` writes the lock and"
        f" `{prog()} sync` supplies what it names.",
    ]
    return "\n".join(lines)


def _is_records(source: str) -> bool:
    """Whether *source* is a directory of records rather than an index."""
    from livery.toolroom.store import POINTER, records_in

    if "://" in source:
        return False
    directory = Path(source)
    return not (directory / POINTER).is_file() and bool(records_in(directory))


def catalogue(root: Path, *, offline: bool = False) -> Catalogue:
    """The catalogue the repository resolves against, from `[toolroom] index`.

    A directory of records is read as the authoring site reads it; an
    index, by URL or directory, through the machine's store, which
    keeps what it fetched so a second read is offline.
    """
    return _read_catalogue(index_source(root), offline=offline)


def _read_catalogue(source: str, *, offline: bool) -> Catalogue:
    """The catalogue at *source*, records or index, with no build first."""
    from livery.toolroom.store import Catalogue, CatalogueError

    if _is_records(source):
        return Catalogue.of_records(Path(source))
    try:
        return Catalogue.of_index(source, home=_home(), offline=offline)
    except CatalogueError as error:
        fail(str(error))


def _home() -> Home:
    return store_home()


def store_home() -> Home:
    """The store's home on this machine: `toolroom` in the runner's data directory."""
    from livery.footman import data_dir
    from livery.toolroom.store import Home

    return Home(data_dir() / "toolroom")


def lock_path(root: Path) -> Path:
    """The lock's file, `toolroom.lock` at the root."""
    from livery.toolroom.store import LOCK_FILE

    return root / LOCK_FILE


def current_lock(root: Path) -> Lock | None:
    """The lock as committed, or `None` when the repository has none yet."""
    from livery.toolroom.store import Lock, LockError

    path = lock_path(root)
    if not path.is_file():
        return None
    try:
        return Lock.load(path)
    except LockError as error:
        fail(str(error))


def lock_is_current(root: Path, *, offline: bool = False) -> tuple[bool, str]:
    """Whether `toolroom.lock` is what the sites resolve to now; and what moved.

    The question `--locked` and `--check` ask, and nothing is written to
    answer it. An entry that still satisfies every floor stands, so a
    newer version published since the lock was written moves nothing:
    that is what an upgrade is for. What moves the answer is a
    requirement the lock does not hold, or holds at a version that no
    longer satisfies.

    The graphs are not compared: a delegated tool's graph is resolved
    when its version enters the lock and written beside it, so a fresh
    resolution never carries one, and reading its absence as a move
    would move every delegated entry on every check.

    Returns:
        Whether the lock on disk is current, and why not when it is
        not: no lock at all, a requirement nothing satisfies, or the
        tools whose entries would move.
    """
    from livery.toolroom.store import LOCK_FILE, LockError, resolve_lock

    held = current_lock(root)
    if held is None:
        return False, f"there is no {LOCK_FILE}"
    listing = catalogue(root, offline=offline)
    try:
        fresh = resolve_lock(
            listing,
            with_runtimes(tuple(requirements(root)), listing),
            hosts=supported_hosts(root),
            keep=held,
            host_allowed=host_allowed(root),
        )
    except LockError as error:
        return False, str(error)
    current, resolved = _entries(held), _entries(fresh)
    if fresh.hosts == held.hosts and resolved == current:
        return True, ""
    names = set(resolved) | set(current)
    moved = sorted(name for name in names if resolved.get(name) != current.get(name))
    return False, f"the lock would move: {', '.join(moved)}"


def _entries(lock: Lock) -> dict[str, dict[str, Any]]:
    """The lock's entries without their graphs: what the current check compares."""
    return {
        name: {key: value for key, value in entry.to_json().items() if key != "graph"}
        for name, entry in lock.tools.items()
    }


def write_lock(
    root: Path,
    *,
    upgrade: tuple[str, ...] = (),
    relock: tuple[str, ...] = (),
    offline: bool = False,
) -> Lock:
    """Resolve the sites' requirements and write the lock; the lock written.

    An entry the lock already holds stands unless it is named in
    *upgrade* or no longer satisfies; a refusal names the tool, each
    floor with its site, and the host at fault. A delegated tool whose
    version entered the lock has its graph resolved with it, and one
    named in *relock* has its graph resolved again though its version
    stands, which is what an install the graph could not satisfy asks
    for.
    """
    from livery.toolroom.store import LockError, resolve_lock

    listing = catalogue(root, offline=offline)
    kept = current_lock(root)
    allowed = host_allowed(root)
    try:
        lock = resolve_lock(
            listing,
            with_runtimes(tuple(requirements(root)), listing),
            hosts=supported_hosts(root),
            keep=kept,
            upgrade=upgrade,
            host_allowed=allowed,
        )
    except LockError as error:
        fail(str(error))
    # What the resolution left out, an optional tool it could not serve.
    for note in lock.notes:
        print(f"  {note}")
    for name in allowed:
        if name not in lock.tools:
            continue
        for host in lock.tools[name].on or lock.hosts:
            if gap := _host_probe_gap(listing, name, lock.tools[name].version, host):
                fail(f"workshop.toml: [toolroom] host-allowed: {gap}")
    lock, notes = with_graphs(root, lock, listing, kept=kept, relock=relock)
    lock.save(lock_path(root))
    for note in notes:
        print(f"  {note}")
    for name in unrequired_allowances(root):
        print(f"  host-allowed: {name} is allowed and no site requires it yet")
    for note in unreached_scopes(root, lock):
        print(f"  {note}")
    return lock


def unreached_scopes(root: Path, lock: Lock) -> list[str]:
    """One line per scoped requirement no locked host matches, and so locks nothing."""
    lines: list[str] = []
    seen: set[str] = set()
    for requirement in requirements(root):
        if not requirement.hosts or requirement.name in lock.tools:
            continue
        spelled = str(requirement)
        if spelled in seen or requirement.on(lock.hosts):
            continue
        seen.add(spelled)
        lines.append(
            f"{spelled} ({requirement.site}): no locked host is"
            f" {', '.join(requirement.hosts)}; not locked"
        )
    return lines


def resolve_graph(
    root: Path,
    name: str,
    package: str,
    version: str,
    *,
    kind: str,
    runtime: str,
) -> tuple[Graph | None, str]:
    """Resolve *package* at *version* and write its graph; the graph or why not.

    A `pypi` graph is uv's universal hashed requirements, resolved
    against the workspace's Python floor, so one file covers every
    interpreter the workspace supports. An `npm` graph is the
    runtime's own lockfile, resolved without installing anything.
    Neither runs unless what resolves it is here: a checkout that has
    not materialised its tools yet locks the version and says the
    graph waits.
    """
    from livery.strongroom import digest_of
    from livery.toolroom.store import Graph

    directory = graphs_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / _graph_file(name, kind)
    writer = _pypi_graph if kind == "pypi" else _npm_graph
    by, why = writer(root, package, version, target, runtime=runtime)
    if not by:
        return None, why
    return Graph(target.name, digest_of(target.read_bytes()), by=by), ""


def _pypi_graph(
    root: Path, package: str, version: str, target: Path, *, runtime: str
) -> tuple[str, str]:
    """Uv's universal hashed requirements for *package*; what resolved it."""
    _ = runtime
    import livery.toolroom.tools as toolroom
    from livery.workshop._pythons import python_floor

    with tempfile.TemporaryDirectory() as scratch:
        wanted = Path(scratch) / "in.txt"
        wanted.write_text(f"{package}=={version}\n", encoding="utf-8")
        done = toolroom.uv.opts(nofail=True, recorded=False)(
            "pip",
            "compile",
            "--generate-hashes",
            "--universal",
            "--quiet",
            # The header names the command that wrote it, the output
            # path included, and each annotation names the input file,
            # whose directory is new on every run: either would make two
            # resolutions of one version differ in bytes, and the lock
            # would churn.
            "--no-header",
            "--no-annotate",
            f"--python-version={python_floor(root)}",
            f"--output-file={target}",
            str(wanted),
        )
    if done.code != 0:
        return "", f"uv could not resolve it (exit {done.code})"
    return f"uv {_tool_version(root, 'uv')}", ""


def _npm_graph(
    root: Path, package: str, version: str, target: Path, *, runtime: str
) -> tuple[str, str]:
    """The runtime's own lockfile for *package*, resolved without installing."""
    if runtime != "node":
        return "", f"a graph of a {runtime} tool is not written yet"
    held = receipts(root).get("node")
    if held is None:
        return "", "node is not materialised here; the next lock writes it"
    node = Path(held.tool_dir) / "bin" / exe("node")
    if not node.is_file():
        node = Path(held.tool_dir) / exe("node")
    if not node.is_file():
        return "", f"the node receipt names no executable under {held.tool_dir}"
    from livery.toolroom.store import npm_cli

    with tempfile.TemporaryDirectory() as scratch:
        where = Path(scratch)
        (where / "package.json").write_text(
            json.dumps(
                {
                    "name": "graph",
                    "version": "0.0.0",
                    "dependencies": {package: version},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        done = run(
            [
                str(node),
                str(npm_cli(node)),
                "install",
                "--package-lock-only",
                "--no-audit",
                "--no-fund",
            ],
            cwd=where,
            nofail=True,
            recorded=False,
            capture=True,
        )
        written = where / "package-lock.json"
        if done.code != 0 or not written.is_file():
            return "", f"npm could not resolve it (exit {done.code})"
        target.write_bytes(written.read_bytes())
    return f"node {held.version}", ""


def _tool_version(root: Path, name: str) -> str:
    """The version the lock pins for *name*, or empty when it holds none."""
    lock = current_lock(root)
    held = lock.tools.get(name) if lock is not None else None
    return held.version if held is not None else ""


def exe(name: str) -> str:
    """*name* as this platform spells an executable."""
    return f"{name}.exe" if sys.platform == "win32" else name


def _graph_path(root: Path, locked: Locked) -> Path | None:
    """The graph file the lock names for this version, when it is here and whole.

    A graph named but missing, or whose bytes are not the ones the
    lock recorded, is not installed from: the tool installs the way
    it did, and the next lock writes the graph again.
    """
    from livery.strongroom import digest_of

    if locked.graph is None:
        return None
    path = graphs_dir(root) / locked.graph.file
    if not path.is_file() or digest_of(path.read_bytes()) != locked.graph.digest:
        return None
    return path


def graphs_dir(root: Path) -> Path:
    """Where the resolved graphs live, beside the lock."""
    from livery.toolroom.store import GRAPHS

    return root / GRAPHS


def _graph_file(name: str, kind: str) -> str:
    """The graph's file name for *name*: the installer's own format."""
    return f"{name}.txt" if kind == "pypi" else f"{name}.json"


def _kept_graph(
    root: Path, name: str, locked: Locked, kept: Lock | None
) -> Graph | None:
    """The graph the lock already had for this version, when it still stands.

    A graph is resolved once, when a version enters the lock, and kept
    after: a lock names artefacts and their hashes, so an install
    re-runs no resolution and a newer installer installs the same
    graph. A version that moved, a graph never written, and a file
    edited or gone since are each resolved again.
    """
    from livery.strongroom import digest_of

    before = kept.tools.get(name) if kept is not None else None
    if before is None or before.version != locked.version or before.graph is None:
        return None
    path = graphs_dir(root) / before.graph.file
    if not path.is_file() or digest_of(path.read_bytes()) != before.graph.digest:
        return None
    return before.graph


def with_graphs(
    root: Path,
    lock: Lock,
    listing: Catalogue,
    *,
    kept: Lock | None = None,
    relock: tuple[str, ...] = (),
) -> tuple[Lock, list[str]]:
    """*lock* with a graph per delegated tool, and what to say about it.

    A `pypi` tool's graph is uv's hashed requirements, a `npm` tool's
    is its runtime's lockfile, each written under `GRAPHS` beside the
    lock and named there by its digest; a `dotnet` tool has none, since
    its package carries its dependencies and the version pins the
    whole. A graph that cannot be
    resolved now, the runtime it needs being absent on a checkout
    that has not materialised yet, leaves the tool as it was: it
    installs the way it did and says so, and the next lock writes it.

    A tool named in *relock* is resolved again though its version
    stands. Nothing here re-resolves on its own: a resolve is a fresh
    answer from an index, so it moves the lock, and a lock moves when
    a person says so, never as a side effect of installing.
    """
    from livery.toolroom.store import CatalogueError

    directory = graphs_dir(root)
    tools: dict[str, Locked] = {}
    notes: list[str] = []
    wanted: set[str] = set()
    for name, locked in lock.tools.items():
        try:
            kind = listing.listed(name).kind
        except CatalogueError:
            tools[name] = locked
            continue
        if kind not in ("pypi", "npm"):
            tools[name] = locked
            continue
        standing = None if name in relock else _kept_graph(root, name, locked, kept)
        if standing is not None:
            wanted.add(standing.file)
            tools[name] = replace(locked, graph=standing)
            continue
        listed = listing.listed(name)
        graph, why = resolve_graph(
            root,
            name,
            listed.package or name,
            locked.version,
            kind=kind,
            runtime=runtime_of(listed),
        )
        if graph is None:
            notes.append(f"graphs: {name} {locked.version}: {why}")
            tools[name] = replace(locked, graph=None)
            continue
        wanted.add(graph.file)
        notes.append(f"graphs: {name} {locked.version} resolved by {graph.by}")
        tools[name] = replace(locked, graph=graph)
    if directory.is_dir():
        # A graph the lock no longer names is a file nothing installs
        # from: it goes with the tool or the version it belonged to.
        for stale in sorted(directory.iterdir()):
            if stale.is_file() and stale.name not in wanted:
                stale.unlink()
                notes.append(f"graphs: removed {stale.name}")
    return replace(lock, tools=tools), notes


def runtime_of(listed: Listed) -> str:
    """The runtime a tool runs on: an `npm` tool's record's, node unless it says bun.

    A `dotnet` tool runs on dotnet, the SDK. Empty for every other
    kind, which runs on nothing the lock supplies.
    """
    if listed.kind == "npm":
        return listed.runtime or "node"
    if listed.kind == "dotnet":
        return "dotnet"
    return ""


def with_runtimes(
    found: tuple[Requirement, ...], listing: Catalogue
) -> tuple[Requirement, ...]:
    """*found*, plus each runtime a tool among them runs on and nothing names.

    An `npm` or `dotnet` tool is installed through its runtime and
    runs on it, so the runtime is the tool's dependency and the lock
    holds it as such, on the hosts the tool is required on: the site
    named is the tool that needs it. A runtime some site requires on
    every host needs no more; otherwise one requirement per distinct
    scope is added, and the lock unions them. A requirement the
    catalogue does not list is left for the resolver to refuse by
    name.
    """
    from livery.toolroom.store import CatalogueError, Requirement

    everywhere = {requirement.name for requirement in found if not requirement.hosts}
    present = {(requirement.name, requirement.hosts) for requirement in found}
    added: list[Requirement] = []
    for requirement in found:
        try:
            listed = listing.listed(requirement.name)
        except CatalogueError:
            continue
        runtime = runtime_of(listed)
        if (
            not runtime
            or runtime in everywhere
            or (runtime, requirement.hosts) in present
        ):
            continue
        present.add((runtime, requirement.hosts))
        added.append(
            Requirement(
                runtime,
                site=f"{requirement.name} ({listed.kind})",
                hosts=requirement.hosts,
            )
        )
    return (*found, *added)


def declare(root: Path, text: str) -> bool:
    """Add *text* to the project's `[toolroom] requires`; whether the contract changed.

    A requirement already declared at the project site, in the same
    spelling, is left as it is. The contract is edited in place: the
    `[toolroom]` table gains the entry, or is added at the end with it.
    """
    from livery.toolroom.store import TABLE, LockError, Requirement

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
        (n for n, line in enumerate(lines) if line.strip() == f"[{TABLE}]"), None
    )
    if header is None:
        trimmed = source.rstrip("\n")
        path.write_text(
            (trimmed + "\n\n" if trimmed else "")
            + f'[{TABLE}]\nrequires = ["{text}"]\n',
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


# --- receipts and materialisation -------------------------------------------------

RECEIPTS = ".workshop/receipts"
"""Where a checkout keeps its receipts, one file per tool, gitignored."""

BIN = ".workshop/bin"
"""The checkout's bin directory: the entry points of every tool in `link` mode."""

RECEIPT_SCHEMA = 1
"""A receipt's shape. Bumped when a reader must know."""


@dataclass(frozen=True)
class Receipt:
    """What this checkout installed of one tool, as its file records it.

    Attributes:
        tool: The tool's name.
        version: The exact version installed, the lock's.
        host: The host key it was installed for, this machine's.
        kind: The installer kind.
        mode: How it reached PATH, one of `MODES`.
        deployment: The digest of the deployment installed; empty for a
            delegated kind, whose installer resolves the host.
        tool_dir: Where the tool is, under the machine's store home.
        paths: The absolute directories the tool puts on PATH in `path`
            mode; empty otherwise.
        env: The variables the tool sets, resolved against its directory.
        entry_points: The executables' names the tool puts on PATH:
            linked into the checkout's bin directory in `link` mode,
            found on `paths` in `path` mode; empty in `none` mode. A
            check resolves the tool by these, never by its name, since
            a name (`git_cliff`) is not always a binary (`git-cliff`).
        source: `store` for a tool the store supplied, `host` for a
            copy already on the machine that served under the
            allowance; `version` is the lock's either way, what the
            stubs render for and the drift check speaks of.
        answered: The version the host's copy printed when it served;
            empty for a store install.
        stamp: The host copy's identity when it served: its `path`,
            `size` and `mtime`, so a sync that finds the same file
            probes nothing; empty for a store install.
    """

    tool: str
    version: str
    host: str
    kind: str
    mode: str
    deployment: str = ""
    tool_dir: str = ""
    paths: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    entry_points: tuple[str, ...] = ()
    source: str = "store"
    answered: str = ""
    stamp: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        """The receipt as a JSON object."""
        return {
            "schema": RECEIPT_SCHEMA,
            "tool": self.tool,
            "version": self.version,
            "host": self.host,
            "kind": self.kind,
            "mode": self.mode,
            "deployment": self.deployment,
            "tool_dir": self.tool_dir,
            "paths": list(self.paths),
            "env": dict(self.env),
            "entry_points": list(self.entry_points),
            "source": self.source,
            "answered": self.answered,
            "stamp": dict(self.stamp),
        }

    @classmethod
    def load(cls, path: Path) -> Receipt:
        """The receipt at *path*.

        Raises:
            ValueError: for a file that is not a receipt of this schema,
                naming the file.
        """
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError(f"{path}: not a receipt ({error})") from None
        if not isinstance(data, dict) or data.get("schema") != RECEIPT_SCHEMA:
            raise ValueError(f"{path}: not a receipt of schema {RECEIPT_SCHEMA}")
        try:
            return cls(
                str(data["tool"]),
                str(data["version"]),
                str(data["host"]),
                str(data["kind"]),
                str(data["mode"]),
                str(data.get("deployment", "")),
                str(data.get("tool_dir", "")),
                tuple(str(p) for p in data.get("paths", [])),
                {str(k): str(v) for k, v in data.get("env", {}).items()},
                tuple(str(e) for e in data.get("entry_points", [])),
                str(data.get("source", "store")),
                str(data.get("answered", "")),
                {str(k): str(v) for k, v in data.get("stamp", {}).items()},
            )
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError(f"{path}: not a receipt ({error})") from None


def _runtimes_first(
    wanted: tuple[str, ...], lock: Lock, listing: Catalogue
) -> tuple[str, ...]:
    """*wanted* with each runtime ahead of the tools that run on it.

    An `npm` tool is installed through its runtime's executable, so the
    runtime is supplied first and its path handed on; a runtime the
    lock holds joins a narrowed *wanted* that names such a tool without
    it.
    """
    needed: list[str] = []
    for name in wanted:
        runtime = runtime_of(listing.listed(name))
        if runtime and runtime not in needed:
            needed.append(runtime)
    if not needed:
        return wanted
    rest = tuple(name for name in wanted if name not in needed)
    return (*(runtime for runtime in needed if runtime in lock.tools), *rest)


def receipts_dir(root: Path) -> Path:
    """The checkout's receipts directory."""
    return root / RECEIPTS


def bin_dir(root: Path) -> Path:
    """The checkout's bin directory."""
    return root / BIN


def receipts(root: Path) -> dict[str, Receipt]:
    """Every receipt the checkout holds, by tool.

    Raises:
        ValueError: for a file under the receipts directory that is not
            a receipt, naming it.
    """
    directory = receipts_dir(root)
    if not directory.is_dir():
        return {}
    found = {}
    for path in sorted(directory.glob("*.json")):
        receipt = Receipt.load(path)
        found[receipt.tool] = receipt
    return found


def mode_of(
    root: Path, name: str, kind: str, declared: str = "", *, paths: tuple[str, ...] = ()
) -> str:
    """The mode *name* materialises in: the project's say, the record's, the kind's.

    *paths* are the deployment's, which decide a download's default.
    Raises a refusal naming the override when it is not one of `MODES`.
    """
    from livery.toolroom.store import MODES, default_mode

    overrides = cast(
        "dict[str, str]", tools_table(root / "workshop.toml").get("modes", {})
    )
    chosen = overrides.get(name, declared) or default_mode(kind, paths)
    if chosen not in MODES:
        fail(
            f"workshop.toml: [toolroom] modes names {chosen!r} for {name}; the modes"
            f" are {', '.join(MODES)}"
        )
    return str(chosen)


def sources(root: Path) -> tuple[Source, ...]:
    """The tiers consulted before an origin, `[toolroom] sources`: folders or URLs."""
    from livery.strongroom import FolderSource, HttpSource

    declared = cast("list[str]", tools_table(root / "workshop.toml").get("sources", []))
    found: list[Source] = []
    for entry in declared:
        if "://" in entry:
            found.append(HttpSource(entry))
        else:
            found.append(FolderSource(root / entry))
    return tuple(found)


@dataclass(frozen=True)
class Materialised:
    """What one materialisation did to one tool.

    Attributes:
        receipt: The receipt written, or `None` when the tool could not
            be supplied and *strict* was off.
        installed: Whether the store installed it on this call.
        failure: Why the tool could not be supplied; empty when it was.
        note: What the sync should say beyond the receipt: why a host
            copy was passed over for the store's version; empty
            otherwise.
    """

    receipt: Receipt | None
    installed: bool
    failure: str = ""
    note: str = ""


def _stamp(found: Path) -> dict[str, str]:
    """The identity of *found* a later sync compares before probing; empty when gone."""
    try:
        stat = found.stat()
    except OSError:
        return {}
    return {
        "path": str(found),
        "size": str(stat.st_size),
        "mtime": str(stat.st_mtime_ns),
    }


def _fresh_host_receipt(root: Path, name: str) -> Receipt | None:
    """*name*'s receipt when a host copy served and the same file is still there."""
    path = receipts_dir(root) / f"{name}.json"
    if not path.is_file():
        return None
    try:
        receipt = Receipt.load(path)
    except ValueError:
        return None
    if receipt.source != "host" or not receipt.stamp.get("path"):
        return None
    if _stamp(Path(receipt.stamp["path"])) != receipt.stamp:
        return None
    return receipt


def site_floors(root: Path) -> dict[str, tuple[str, str]]:
    """The highest floor the sites declare per tool, with the site that asked.

    A tool no site floors is absent. The lock already honours these
    floors when it chooses a version; the check of a system tool on a
    machine honours them here, since the machine's own copy is never
    the locked version.
    """
    from livery.toolroom.store import version_key

    highest: dict[str, tuple[str, str]] = {}
    for requirement in requirements(root):
        if not requirement.floor:
            continue
        held = highest.get(requirement.name)
        if held is None or version_key(requirement.floor) > version_key(held[0]):
            highest[requirement.name] = (requirement.floor, requirement.site)
    return highest


def materialise(
    root: Path,
    names: tuple[str, ...] = (),
    *,
    offline: bool = False,
    strict: bool = True,
) -> tuple[Materialised, ...]:
    """Supply every locked tool, or *names* alone, and write their receipts.

    The bundle is what the sites require: every tool the lock holds
    for this host; one locked for other hosts alone is skipped, and
    named it refuses. A downloaded kind is supplied from the
    catalogue's deployment for
    this host through the store's sources and the origin; a delegated
    kind through its installer. A system tool is the machine's own,
    held to the highest of the record's floor and the sites' floors;
    the refusal names the site whose floor it is under. A tool the
    lock marks `allow-host` is looked for on PATH first: a copy that
    satisfies the floor serves, its receipt saying `host` and the
    version that answered, and the store installs nothing for it; a
    copy that is absent or below the floor is passed over with a
    note, and the locked version is supplied as for any other tool. A
    host receipt whose file is unchanged since it was written is
    reused without a probe. Tools in
    `link` mode are linked into the checkout's bin directory together,
    so a name two tools offer goes to the first. A receipt is written
    per tool supplied, and on a materialise of the whole bundle the
    receipt of a tool the lock no longer holds is removed, so its
    paths leave the environment with it.

    Raises a refusal naming the tool when the lock does not hold it.
    A tool the store cannot supply refuses too, naming the reason,
    unless *strict* is off: then it is reported in its outcome and the
    others are supplied, which is what `sync` wants, since one tool's
    origin being unreachable must not stop the environment from
    entering. A tool the lock marks optional is reported and never
    refused, strict or not: the verbs that need it name its absence
    when they run.
    """
    from livery.toolroom.store import (
        DOWNLOAD_KINDS,
        LOCK_FILE,
        RUNTIMES,
        CatalogueError,
        Store,
        StoreError,
        version_key,
    )

    lock = current_lock(root)
    if lock is None:
        fail(f"no {LOCK_FILE}: lock the tools first with `{prog()} toolroom.lock`")
    listing = catalogue(root, offline=offline)
    store = Store(_home(), sources=sources(root), offline=offline)
    host = store.host
    supported = supported_hosts(root)
    if host not in supported:
        # A runner's label is free text on every forge, so the host
        # itself is where an unsupported runner can be told apart.
        reason = (
            f"this host ({host}) is not one the workspace supports;"
            f" [workspace] hosts supports {', '.join(supported)}"
        )
        if strict or os.environ.get("CI"):
            fail(reason)
        print(f"  {reason}")
    here = lock.on_host(host)
    wanted = names or here
    if not names:
        for name, entry in sorted(lock.tools.items()):
            if entry.optional and name not in here:
                print(
                    f"  {name}: optional, and not locked for this host ({host});"
                    " the verbs that need it say so"
                )
    for name in wanted:
        if name not in lock.tools:
            fail(
                f"{name} is not in {LOCK_FILE}; the lock holds"
                f" {', '.join(sorted(lock.tools)) or 'nothing'}"
            )
        if name not in here:
            fail(
                f"{name} is locked for {', '.join(lock.tools[name].on)} and not"
                f" for this host ({host}); the sites require it there alone"
            )
    floors = site_floors(root)
    done: list[Materialised] = []
    linked: list[tuple[Receipt, Ensured]] = []
    wanted = _runtimes_first(wanted, lock, listing)
    runtimes: dict[str, Path] = {}
    for name in wanted:
        locked = lock.tools[name]
        listed = listing.listed(name)
        floor, site = listed.min_version, ""
        asked = floors.get(name) if listed.kind == "system-check" else None
        if asked is not None and (
            not floor or version_key(asked[0]) > version_key(floor)
        ):
            floor, site = asked
        note = ""
        if locked.allow_host:
            fresh = _fresh_host_receipt(root, name)
            if fresh is not None:
                if name in RUNTIMES:
                    runtimes[name] = Path(fresh.stamp["path"])
                done.append(Materialised(fresh, False))
                continue
            asked = floors.get(name)
            host_floor = asked[0] if asked is not None else listed.min_version
            try:
                served = store.supply(
                    name, "system-check", locked.version, None, min_version=host_floor
                )
            except StoreError as error:
                note = f"{error}; the store's {locked.version} serves"
            else:
                found = served.tool_dir / exe(name)
                if name in RUNTIMES:
                    runtimes[name] = found
                receipt = Receipt(
                    name,
                    locked.version,
                    host,
                    listed.kind,
                    "none",
                    tool_dir=str(served.tool_dir),
                    source="host",
                    answered=served.answered,
                    stamp=_stamp(found),
                )
                done.append(Materialised(receipt, False))
                continue
        deployment: Deployment | None = None
        if listed.kind in DOWNLOAD_KINDS:
            try:
                deployment = listing.deployment(name, locked.version, host)
            except CatalogueError as error:
                # A host outside the lock's set, or one the version has
                # no build for: the tool is reported and the others are
                # supplied, as any tool the store cannot supply is.
                reason = f"{name} {locked.version}: not locked for {host}: {error}"
                if strict and not locked.optional:
                    fail(reason)
                done.append(Materialised(None, False, reason))
                continue
        try:
            ensured = store.supply(
                name,
                listed.kind,
                locked.version,
                deployment,
                package=listed.package,
                min_version=floor,
                runtime=listed.runtime,
                runtime_exe=runtimes.get(runtime_of(listed)),
                graph=_graph_path(root, locked),
            )
        except StoreError as error:
            reason = str(error)
            if site and "below the floor" in reason:
                reason += f"; {site} requires {name}>={floor}"
            # An optional tool the host cannot supply is named, never
            # refused: the verbs that need it say so when they run.
            if strict and not locked.optional:
                fail(reason)
            done.append(Materialised(None, False, reason))
            continue
        if name in RUNTIMES and ensured.deployment.entry_points:
            runtimes[name] = ensured.tool_dir / ensured.deployment.entry_points[0]
        mode = mode_of(
            root,
            name,
            listed.kind,
            listed.mode,
            paths=deployment.paths if deployment is not None else (),
        )
        receipt = Receipt(
            name,
            locked.version,
            host,
            listed.kind,
            mode,
            str(deployment.digest()) if deployment is not None else "",
            str(ensured.tool_dir),
            tuple(str(p) for p in ensured.paths) if mode == "path" else (),
            ensured.env,
            tuple(Path(e).name for e in ensured.deployment.entry_points)
            if mode in ("link", "path")
            else (),
        )
        if mode == "link":
            linked.append((receipt, ensured))
        done.append(Materialised(receipt, ensured.installed, note=note))
    if linked or bin_dir(root).is_dir():
        store.link([ensured for _, ensured in linked], bin_dir(root))
    receipts_dir(root).mkdir(parents=True, exist_ok=True)
    for made in done:
        if made.receipt is None:
            continue
        path = receipts_dir(root) / f"{made.receipt.tool}.json"
        path.write_text(
            json.dumps(made.receipt.to_json(), indent=2) + "\n", encoding="utf-8"
        )
    if not names:
        # A tool that left the lock, or is locked for other hosts
        # alone, leaves the environment: a receipt kept past its tool
        # would keep the tool's directory on PATH ahead of whatever
        # replaced it.
        for stale in receipts_dir(root).glob("*.json"):
            if stale.stem not in here:
                stale.unlink()
    return tuple(done)


def typings_dir(root: Path) -> Path:
    """The typings directory the stubs are written under."""
    return root / TYPINGS


def stubs_dir(root: Path) -> Path:
    """The `stubs` package under the typings directory's `livery.toolroom`."""
    return typings_dir(root).joinpath(*TYPINGS_PACKAGE) / "stubs"


def stubs_present(root: Path) -> int:
    """How many tool stubs the typings directory holds."""
    return sum(1 for p in stubs_dir(root).glob("*.pyi") if p.stem != "__init__")


@dataclass(frozen=True)
class Stubbed:
    """What `write_stubs` did.

    Attributes:
        written: The tools whose stub changed on disk, or was new.
        kept: The tools whose stub was already what the catalogue holds.
        skipped: Per tool the catalogue lists and no stub was written
            for, why.
        removed: Stubs of tools the catalogue no longer lists.
    """

    written: tuple[str, ...]
    kept: tuple[str, ...]
    skipped: dict[str, str]
    removed: tuple[str, ...]


def _no_command(listing: Catalogue, name: str, version: str) -> bool:
    """Whether *name* is a download with no entry point: no command, so no stub."""
    import platform

    from livery.toolroom.store import CatalogueError, RecordError, host_key

    if listing.listed(name).kind != "download":
        return False
    try:
        deployment = listing.deployment(
            name, version, host_key(platform.system(), platform.machine())
        )
    except (CatalogueError, RecordError):
        return False
    return not deployment.entry_points


def write_stubs(root: Path, *, offline: bool = False) -> Stubbed:
    """Write the stubs of the locked tools into the typings directory.

    One stub per tool `toolroom.lock` holds, rendered by the store from the
    locked version's own surface, from the records or from the index:
    a tool the workspace does not deploy gets no stub, and its handle
    types as a bare `Tool`. The `handles` module beside the stubs
    declares the handles, one import and one `name: Class[Result]` per
    stub written, and the installed tools package's index imports every
    name from it. The handles cannot live in the stubs package's own
    index: a package's index binds its submodules, and `uv` would name
    `uv.pyi` rather than the handle. Nothing is written inside the
    tools package's own directory, and a tree left there is removed:
    it shadows the package for a checker run on explicit paths. Without
    a lock nothing is written.

    A receipt under `.workshop/state/` names the lock and the source
    the last write rendered from, a records directory by its stat
    fingerprint and an index by its pointer. When both stand and every file it
    wrote is there, nothing is read and nothing is written, which is
    the steady state of every sync; otherwise a stub already on disk
    as the source renders it is kept, so a checker's cache stands.
    """
    from livery.toolroom.store import CatalogueError

    source = index_source(root)
    lock = current_lock(root)
    locked = dict(lock.tools) if lock is not None else {}
    shutil.rmtree(
        typings_dir(root).joinpath(*TYPINGS_PACKAGE, "tools"), ignore_errors=True
    )
    directory = stubs_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    seen = None
    if locked:
        try:
            seen = source_mark(source)
        except CatalogueError as error:
            fail(str(error))
        standing = _stubs_standing(root, seen, sorted(locked))
        if standing is not None:
            return standing
    listing = _read_catalogue(source, offline=offline) if locked else None
    written: list[str] = []
    kept: list[str] = []
    skipped: dict[str, str] = {}
    declared: list[str] = []
    for name in sorted(locked):
        version = locked[name].version
        assert listing is not None
        if _no_command(listing, name, version):
            continue
        try:
            text = listing.stub(name, version)
        except CatalogueError as error:
            skipped[name] = str(error)
            continue
        path = directory / f"{name}.pyi"
        if path.is_file() and path.read_text(encoding="utf-8") == text:
            kept.append(name)
        else:
            path.write_text(text, encoding="utf-8")
            written.append(name)
        declared.append(name)
    removed: list[str] = []
    for stale in sorted(directory.glob("*.pyi")):
        if stale.stem != "__init__" and stale.stem not in declared:
            stale.unlink()
            removed.append(stale.stem)
    _write_if_changed(directory / "__init__.pyi", "")
    _write_if_changed(handles_path(root), handles_index(declared))
    if seen is not None:
        _write_stubs_receipt(root, seen, sorted(locked), declared)
    return Stubbed(tuple(written), tuple(kept), skipped, tuple(removed))


def source_mark(source: str) -> str:
    """What the catalogue source is right now, in one digest, without reading it whole.

    A directory of records is its stat fingerprint; an index, by
    directory or URL, is the digest of its pointer document.
    """
    from livery.strongroom import canonical, digest_of
    from livery.toolroom.store import read_pointer, tree_fingerprint

    if _is_records(source):
        return tree_fingerprint([source])
    return str(digest_of(canonical(read_pointer(source))))


STUBS_RECEIPT = ".workshop/state/stubs.json"
"""The receipt naming the lock and the source the last stub write rendered from.

Under `state/` rather than `receipts/`: that directory is the tool
receipts, which `receipts()` reads as a set and refuses a file of
another shape in. Each kind of thing under `.workshop/` keeps its own
directory, so no walk of one kind can reach another.
"""


def _stubs_receipt_path(root: Path) -> Path:
    return root / STUBS_RECEIPT


def _lock_digest(root: Path) -> str:
    return "sha256:" + hashlib.sha256(lock_path(root).read_bytes()).hexdigest()


def _stubs_standing(root: Path, stubs: str, locked: list[str]) -> Stubbed | None:
    """The last write's answer when the lock, the source and the files stand."""
    try:
        held = json.loads(_stubs_receipt_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(held, dict) or held.get("schema") != 1:
        return None
    if held.get("source") != stubs or held.get("lock") != _lock_digest(root):
        return None
    if held.get("locked") != locked:
        return None
    written = held.get("written")
    if not isinstance(written, list) or not all(isinstance(n, str) for n in written):
        return None
    # A locked tool with no stub is named on every write, so a write that
    # skipped one is never answered from the receipt.
    if written != locked:
        return None
    present = {p.stem for p in stubs_dir(root).glob("*.pyi")} - {"__init__"}
    if present != set(written) or not handles_path(root).is_file():
        return None
    return Stubbed((), tuple(written), {}, ())


def _write_stubs_receipt(
    root: Path, stubs: str, locked: list[str], written: list[str]
) -> None:
    path = _stubs_receipt_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "schema": 1,
        "source": stubs,
        "lock": _lock_digest(root),
        "locked": locked,
        "written": written,
    }
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def handles_path(root: Path) -> Path:
    """The `handles.pyi` module beside the stubs, declaring the typed handles."""
    return typings_dir(root).joinpath(*TYPINGS_PACKAGE) / "handles.pyi"


def handles_index(names: list[str]) -> str:
    """The `handles` module declaring the handles of *names*, in order.

    The installed tools package's own index imports every name from
    this module, so the handle `uv` types as `Uv[Result]` exactly
    when `stubs/uv.pyi` is beside it.
    """
    from livery.toolroom.store import class_name

    lines = [
        f"# Rendered by `{prog()} toolroom.restub`: the handles this workspace",
        "# locks. Do not edit by hand.",
        "from livery.toolroom.tools import Result",
    ]
    lines += [
        f"from livery.toolroom.stubs.{name} import"
        f" {class_name(name)} as {class_name(name)}"
        for name in names
    ]
    lines.append("")
    lines += [f"{name}: {class_name(name)}[Result]" for name in names]
    return "\n".join(lines) + "\n"


def _write_if_changed(path: Path, text: str) -> None:
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return
    path.write_text(text, encoding="utf-8")


def stub_lines(root: Path, *, offline: bool = False, strict: bool = True) -> list[str]:
    """Write the stubs and say what happened, as `sync` and the lock verbs print it.

    A refusal is raised when *strict*; otherwise it is the one line
    returned, naming the reason, since a lock written or a bundle
    materialised stands whether or not the stubs could follow.
    """
    if strict:
        made = write_stubs(root, offline=offline)
    else:
        try:
            made = write_stubs(root, offline=offline)
        except Failed as refusal:
            return [f"  stubs: not written: {refusal}"]
    held = len(made.written) + len(made.kept)
    line = f"  stubs: {held} in {TYPINGS}/"
    if made.written:
        line += f", wrote {len(made.written)}"
    if made.removed:
        line += f", removed {', '.join(made.removed)}"
    lines = [line]
    lines += [f"  stubs: {name}: {why}" for name, why in made.skipped.items()]
    return lines


def emission(root: Path) -> tuple[tuple[str, ...], dict[str, str]]:
    """What the receipts add to an entered shell: PATH entries and variables.

    The checkout's bin directory first when any receipt links into it,
    then each `path` receipt's directories in tool order, then the
    variables, a later receipt's value winning.
    """
    held = receipts(root)
    paths: list[str] = []
    env: dict[str, str] = {}
    if any(r.mode == "link" for r in held.values()):
        paths.append(str(bin_dir(root)))
    for receipt in held.values():
        for entry in receipt.paths:
            if entry not in paths:
                paths.append(entry)
        env.update(receipt.env)
    return tuple(paths), env


def drift(root: Path) -> dict[str, str]:
    """Per tool the sites require, what `fm env.check` says of its receipt.

    An empty string is a tool whose receipt matches the lock; anything
    else names the problem: no receipt, a receipt at another version
    than the lock, or a deployment the lock has moved under it.
    """
    lock = current_lock(root)
    held = receipts(root)
    found: dict[str, str] = {}
    for name in tool_names(root, this_host()):
        if lock is None or name not in lock.tools:
            found[name] = f"not locked; run `{prog()} toolroom.lock`"
            continue
        locked = lock.tools[name]
        receipt = held.get(name)
        if receipt is None:
            found[name] = (
                f"no receipt for {locked.version}; run `{prog()} sync` to materialise"
            )
            continue
        if receipt.version != locked.version:
            found[name] = (
                f"receipt {receipt.version}, lock {locked.version}; run `{prog()} sync`"
            )
            continue
        expected = locked.hosts.get(receipt.host)
        if receipt.source == "host":
            continue
        if expected is not None and str(expected) != receipt.deployment:
            found[name] = (
                f"DRIFT: the deployment of {locked.version} on {receipt.host} moved"
                f" under the receipt ({receipt.deployment[:19]}... is now"
                f" {str(expected)[:19]}...); run `{prog()} sync`"
            )
            continue
        found[name] = ""
    return found
