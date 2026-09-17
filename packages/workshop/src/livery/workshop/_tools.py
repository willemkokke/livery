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
the catalogue lists, at the locked version or the newest listed, as the
`_stubs` modules the installed tools package's own index imports, so
the typings directory never shadows the package. `fm tools.restub`
writes them, and so do `fm sync` and every lock verb. A source that is a
directory of records holds no rendering; `[tools] index-build` names the
verb that builds the index there before the catalogue is read.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from livery.footman import fail, prog
from livery.footman.context import Failed
from livery.strongroom import FolderSource, HttpSource, Source
from livery.toolroom.store import (
    LOCK_FILE,
    MODES,
    POINTER,
    TOOL_FILE,
    Catalogue,
    CatalogueError,
    Deployment,
    Ensured,
    Home,
    Lock,
    LockError,
    Requirement,
    Store,
    StoreError,
    default_mode,
    resolve_lock,
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

STUBS_PACKAGE = ("livery", "toolroom", "tools")
"""The package the stubs belong to, as directories under the typings directory."""


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


def stubs_expected(root: Path) -> bool:
    """Whether the source can render stubs: an index, or records with a build verb."""
    if not has_index(root):
        return False
    return not _is_records(index_source(root)) or bool(index_build(root))


def index_build(root: Path) -> str:
    """The verb that builds the index at `[tools] index`, or empty when none is named.

    The bench's `tools.index.build` in the repository that authors the
    records: run before the catalogue is read, so the index the lock and
    the stubs come from is the records as they are.
    """
    declared = tools_table(root / "workshop.toml").get("index-build")
    if declared is None:
        return ""
    if not isinstance(declared, str) or not declared:
        fail("workshop.toml: [tools] index-build is not a verb name")
    return declared


def _is_records(source: str) -> bool:
    """Whether *source* is a directory of records rather than an index."""
    if "://" in source:
        return False
    directory = Path(source)
    return not (directory / POINTER).is_file() and any(
        (child / TOOL_FILE).is_file()
        for child in (directory.iterdir() if directory.is_dir() else ())
    )


def build_index(root: Path) -> str:
    """Run the `[tools] index-build` verb when one is named; the verb run, or empty.

    The verb runs as its own runner invocation at the root, the way a
    docs generator does, so it is exactly what a person would type. A
    verb that exits non-zero refuses, naming it.
    """
    import shutil

    import livery.footman as footman

    verb = index_build(root)
    if not verb:
        return ""
    runner = shutil.which(footman.prog())
    if not runner:
        fail(
            f"{footman.prog()} is not on PATH, so `[tools] index-build`"
            f" ({verb}) cannot run; enter the environment first"
        )
    code = footman.run([runner, verb], cwd=root, nofail=True)
    if int(code) != 0:
        fail(f"`{footman.prog()} {verb}` ([tools] index-build) exited {int(code)}")
    return verb


def catalogue(root: Path, *, offline: bool = False) -> Catalogue:
    """The catalogue the repository resolves against, from `[tools] index`.

    A directory of records is read as the authoring site reads it; an
    index, by URL or directory, through the machine's store, which
    keeps what it fetched so a second read is offline. An index a
    `[tools] index-build` verb builds is built first.
    """
    source = index_source(root)
    if "://" not in source:
        build_index(root)
    if _is_records(source):
        return Catalogue.of_records(Path(source))
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
        entry_points: The names linked in `link` mode; empty otherwise.
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


def mode_of(root: Path, name: str, kind: str, declared: str = "") -> str:
    """The mode *name* materialises in: the project's say, the record's, the kind's.

    Raises a refusal naming the override when it is not one of `MODES`.
    """
    overrides = tools_table(root / "workshop.toml").get("modes", {})
    if not isinstance(overrides, dict):
        fail("workshop.toml: [tools] modes is not a table")
    chosen = overrides.get(name, declared) or default_mode(kind)
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
    kind through its installer. Tools in `link` mode are linked into
    the checkout's bin directory together, so a name two tools offer
    goes to the first. A receipt is written per tool supplied.

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
    done: list[Materialised] = []
    linked: list[tuple[Receipt, Ensured]] = []
    for name in wanted:
        locked = lock.tools[name]
        listed = listing.listed(name)
        deployment: Deployment | None = None
        if listed.kind in ("archive", "binary"):
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
                min_version=listed.min_version,
            )
        except StoreError as error:
            if strict:
                fail(str(error))
            done.append(Materialised(None, False, str(error)))
            continue
        mode = mode_of(root, name, listed.kind, listed.mode)
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
            if mode == "link"
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
    """The `_stubs` directory under the typings directory's tools package."""
    return typings_dir(root).joinpath(*STUBS_PACKAGE) / "_stubs"


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


def write_stubs(root: Path, *, offline: bool = False) -> Stubbed:
    """Write every stub the catalogue offers into the typings directory.

    One stub per tool the catalogue lists, at the version the lock holds
    for it or the newest listed otherwise, so a handle the workspace
    types against but does not require still completes. The installed
    tools package's own index imports each by name and declares the
    handles, so the typings directory holds the `_stubs` modules alone
    and never shadows the package. A stub already on disk as the
    catalogue holds it is kept, so a checker's cache stands.

    Refuses when `[tools] index` names a directory of records and no
    `[tools] index-build` verb: records hold no rendering.
    """
    source = index_source(root)
    if _is_records(source) and not index_build(root):
        fail(
            f"[tools] index names records ({source}), which hold no stubs; name"
            " the index built from them, or `[tools] index-build`, the verb"
            " that builds it"
        )
    listing = catalogue(root, offline=offline)
    lock = current_lock(root)
    directory = stubs_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    kept: list[str] = []
    skipped: dict[str, str] = {}
    for name in sorted(listing.tools):
        listed = listing.tools[name]
        if lock is not None and name in lock.tools:
            version = lock.tools[name].version
        elif listed.versions:
            version = listed.versions[-1]
        else:
            skipped[name] = "no version read"
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
    removed: list[str] = []
    for stale in sorted(directory.glob("*.pyi")):
        if stale.stem != "__init__" and stale.stem not in listing.tools:
            stale.unlink()
            removed.append(stale.stem)
    _write_if_changed(directory / "__init__.pyi", "")
    return Stubbed(tuple(written), tuple(kept), skipped, tuple(removed))


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
