"""The tools a workspace requires: three declaration sites, one lock, receipts.

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

Entering the environment materialises the bundle the sites require, on
`fm sync` and on `fm tools.add`: each locked tool is supplied through
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
deploy gets no stub. `fm tools.restub` writes them, and so do `fm sync`
and every lock verb.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from livery.footman import fail, prog
from livery.footman.context import Failed
from livery.strongroom import FolderSource, HttpSource, Source, canonical, digest_of
from livery.toolroom.store import (
    DOWNLOAD_KINDS,
    LOCK_FILE,
    MODES,
    POINTER,
    Catalogue,
    CatalogueError,
    Deployment,
    Ensured,
    Home,
    Lock,
    LockError,
    RecordError,
    Requirement,
    Store,
    StoreError,
    class_name,
    default_mode,
    host_key,
    read_pointer,
    records_in,
    resolve_lock,
    tree_fingerprint,
    version_key,
)
from livery.workshop._contract import load_contract
from livery.workshop._kinds import kind_chain
from livery.workshop._packages import discover_packages

TOOLS = "tools"
"""The contract table the requirements, the hosts and the index live under."""

DEFAULT_HOSTS = ("linux-x64", "macos-arm", "windows-x64")
"""The hosts locked for unless the contract names others: the gated three."""

TYPINGS = "typings"
"""The directory under the root the stubs are written into, pyright's default."""

TYPINGS_PACKAGE = ("livery", "toolroom")
"""The namespace package the stubs and handles live under, beside the tools package."""


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


def has_index(root: Path) -> bool:
    """Whether the root contract names a catalogue source at all."""
    declared = tools_table(root / "workshop.toml").get("index")
    return isinstance(declared, str) and bool(declared)


def _is_records(source: str) -> bool:
    """Whether *source* is a directory of records rather than an index."""
    if "://" in source:
        return False
    directory = Path(source)
    return not (directory / POINTER).is_file() and bool(records_in(directory))


def catalogue(root: Path, *, offline: bool = False) -> Catalogue:
    """The catalogue the repository resolves against, from `[tools] index`.

    A directory of records is read as the authoring site reads it; an
    index, by URL or directory, through the machine's store, which
    keeps what it fetched so a second read is offline.
    """
    return _read_catalogue(index_source(root), offline=offline)


def _read_catalogue(source: str, *, offline: bool) -> Catalogue:
    """The catalogue at *source*, records or index, with no build first."""
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
    listing = catalogue(root)
    try:
        lock = resolve_lock(
            listing,
            with_bun(tuple(requirements(root)), listing),
            hosts=locked_hosts(root),
            keep=current_lock(root),
            upgrade=upgrade,
        )
    except LockError as error:
        fail(str(error))
    lock.save(lock_path(root))
    return lock


def with_bun(
    found: tuple[Requirement, ...], listing: Catalogue
) -> tuple[Requirement, ...]:
    """*found*, plus bun when a `bun-install` tool is among them and nothing names bun.

    A `bun-install` tool is installed by bun, so bun is its dependency
    and the lock holds it as such: the site named is the tool that
    needs it. A requirement the catalogue does not list is left for
    the resolver to refuse by name.
    """
    if any(requirement.name == "bun" for requirement in found):
        return found
    for requirement in found:
        try:
            kind = listing.listed(requirement.name).kind
        except CatalogueError:
            continue
        if kind == "bun-install":
            site = f"{requirement.name} (bun-install)"
            return (*found, Requirement.parse("bun", site=site))
    return found


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
            )
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError(f"{path}: not a receipt ({error})") from None


def _bun_first(
    wanted: tuple[str, ...], lock: Lock, listing: Catalogue
) -> tuple[str, ...]:
    """*wanted* with bun ahead of any `bun-install` tool, joined when the lock holds it.

    A `bun-install` tool is installed through bun's executable, so bun
    is supplied first and its path handed on; a bun the lock holds
    joins a narrowed *wanted* that names such a tool without it.
    """
    needs_bun = any(listing.listed(name).kind == "bun-install" for name in wanted)
    if not needs_bun:
        return wanted
    rest = tuple(name for name in wanted if name != "bun")
    return ("bun", *rest) if "bun" in lock.tools else rest


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
    overrides = tools_table(root / "workshop.toml").get("modes", {})
    if not isinstance(overrides, dict):
        fail("workshop.toml: [tools] modes is not a table")
    chosen = overrides.get(name, declared) or default_mode(kind, paths)
    if chosen not in MODES:
        fail(
            f"workshop.toml: [tools] modes names {chosen!r} for {name}; the modes"
            f" are {', '.join(MODES)}"
        )
    return str(chosen)


def sources(root: Path) -> tuple[Source, ...]:
    """The tiers consulted before an origin, `[tools] sources`: folders or URLs."""
    declared = tools_table(root / "workshop.toml").get("sources", [])
    if not isinstance(declared, list) or not all(isinstance(s, str) for s in declared):
        fail("workshop.toml: [tools] sources is not a list of strings")
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
    """

    receipt: Receipt | None
    installed: bool
    failure: str = ""


def site_floors(root: Path) -> dict[str, tuple[str, str]]:
    """The highest floor the sites declare per tool, with the site that asked.

    A tool no site floors is absent. The lock already honours these
    floors when it chooses a version; the check of a system tool on a
    machine honours them here, since the machine's own copy is never
    the locked version.
    """
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

    The bundle is what the sites require: every tool the lock holds.
    A downloaded kind is supplied from the catalogue's deployment for
    this host through the store's sources and the origin; a delegated
    kind through its installer. A system tool is the machine's own,
    held to the highest of the record's floor and the sites' floors;
    the refusal names the site whose floor it is under. Tools in
    `link` mode are linked into the checkout's bin directory together,
    so a name two tools offer goes to the first. A receipt is written
    per tool supplied.

    Raises a refusal naming the tool when the lock does not hold it.
    A tool the store cannot supply refuses too, naming the reason,
    unless *strict* is off: then it is reported in its outcome and the
    others are supplied, which is what `sync` wants, since one tool's
    origin being unreachable must not stop the environment from
    entering.
    """
    lock = current_lock(root)
    if lock is None:
        fail(f"no {LOCK_FILE}: lock the tools first with `{prog()} tools.lock`")
    wanted = names or tuple(sorted(lock.tools))
    for name in wanted:
        if name not in lock.tools:
            fail(
                f"{name} is not in {LOCK_FILE}; the lock holds"
                f" {', '.join(sorted(lock.tools)) or 'nothing'}"
            )
    listing = catalogue(root, offline=offline)
    store = Store(_home(), sources=sources(root), offline=offline)
    host = store.host
    floors = site_floors(root)
    done: list[Materialised] = []
    linked: list[tuple[Receipt, Ensured]] = []
    wanted = _bun_first(wanted, lock, listing)
    bun_exe: Path | None = None
    for name in wanted:
        locked = lock.tools[name]
        listed = listing.listed(name)
        floor, site = listed.min_version, ""
        asked = floors.get(name) if listed.kind == "system-check" else None
        if asked is not None and (
            not floor or version_key(asked[0]) > version_key(floor)
        ):
            floor, site = asked
        deployment: Deployment | None = None
        if listed.kind in DOWNLOAD_KINDS:
            try:
                deployment = listing.deployment(name, locked.version, host)
            except CatalogueError as error:
                fail(f"{name} {locked.version}: not locked for {host}: {error}")
        try:
            ensured = store.supply(
                name,
                listed.kind,
                locked.version,
                deployment,
                package=listed.package,
                min_version=floor,
                bun=bun_exe,
            )
        except StoreError as error:
            reason = str(error)
            if site and "below the floor" in reason:
                reason += f"; {site} requires {name}>={floor}"
            if strict:
                fail(reason)
            done.append(Materialised(None, False, reason))
            continue
        if name == "bun" and ensured.deployment.entry_points:
            bun_exe = ensured.tool_dir / ensured.deployment.entry_points[0]
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
        done.append(Materialised(receipt, ensured.installed))
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

    One stub per tool `tools.lock` holds, rendered by the store from the
    locked version's own surface, from the records or from the index:
    a tool the workspace does not deploy gets no stub, and its handle
    types as a bare `Tool`. The `handles` module beside the stubs
    declares the handles, one import and one `name: Class[Result]` per
    stub written, and the installed tools package's index imports every
    name from it. The handles cannot live in the stubs package's own
    index: a package's index binds its submodules, and `ruff` would name
    `ruff.pyi` rather than the handle. Nothing is written inside the
    tools package's own directory, and a tree left there is removed:
    it shadows the package for a checker run on explicit paths. Without
    a lock nothing is written.

    A receipt under `.workshop/` names the lock and the source the last
    write rendered from, a records directory by its stat fingerprint
    and an index by its pointer. When both stand and every file it
    wrote is there, nothing is read and nothing is written, which is
    the steady state of every sync; otherwise a stub already on disk
    as the source renders it is kept, so a checker's cache stands.
    """
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
    if _is_records(source):
        return tree_fingerprint([source])
    return str(digest_of(canonical(read_pointer(source))))


STUBS_RECEIPT = ".workshop/stubs.json"
"""The receipt naming the lock and the source the last stub write rendered from."""


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
    this module, so the handle `ruff` types as `Ruff[Result]` exactly
    when `stubs/ruff.pyi` is beside it.
    """
    lines = [
        f"# Rendered by `{prog()} tools.restub`: the handles this workspace",
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
    for name in tool_names(root):
        if lock is None or name not in lock.tools:
            found[name] = f"not locked; run `{prog()} tools.lock`"
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
        if expected is not None and str(expected) != receipt.deployment:
            found[name] = (
                f"DRIFT: the deployment of {locked.version} on {receipt.host} moved"
                f" under the receipt ({receipt.deployment[:19]}... is now"
                f" {str(expected)[:19]}...); run `{prog()} sync`"
            )
            continue
        found[name] = ""
    return found
