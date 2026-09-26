"""Keep the tool records honest — `fm tools …`.

The bridge never goes stale, because it transcribes nothing. A tool's
*record* can: it describes the tool at the versions read, and tools
move. These tasks close that gap by reading the installed tools into
the records and by failing a check when a tool and its record disagree.
A stub is a rendering of a record, written by the index build and
materialised into a workspace by `fm tools.restub`; nothing here writes
one into a package.

    fm tools.list                  what footman curates, and what's installed
    fm tools.spec ruff             what one tool says about itself, right now
    fm tools.sync                  read the installed tools into their records
    fm tools.audit                 which tools have moved past their record
    fm tools.color                 how footman forces colour, per tool

A reading is a snapshot, not a contract: `sync` takes one, `audit` says
which tools have released a newer version since. Being behind is news
rather than a fault, so `audit` reports and exits zero unless you ask for
`--strict`, and the readings are retaken by the refresh rather than the
moment a tool ships. A snapshot only ever moves forward: a tool that isn't
installed, is missing from a `--prefix`, or reads older than the record
already holds is named and left alone — a check that quietly covered three
of thirteen would be worse than no check.
"""

from __future__ import annotations

import dataclasses
import json
import re as _re
import shutil
import sys
import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, Protocol, cast

from livery.toolroom.bench import _artifacts, _drivers, _index, _surfaces
from livery.toolroom.store import (
    RECORD_SUFFIX,
    Catalogue,
    Ensured,
    Home,
    Store,
    StoreError,
    ToolSpec,
    class_name,
    records_in,
    render,
)

if TYPE_CHECKING:
    from types import ModuleType

    from livery.toolroom.bench import _provision, _toolfetch
    from livery.toolroom.store import Record

import livery.toolroom.tools as _tools
from livery.footman import fail
from livery.footman._describe import bold, cyan, wants_color
from livery.footman.context import current, data_dir, project_root
from livery.footman.params import doc
from livery.footman.registry import Group
from livery.toolroom.tools import version_tuple as _version_tuple

tasks: Group = Group("tools", help="Keep the tool records honest")

# The records live at the repository root, `records/<tool>/`, the
# authoring site the store reads. The bench writes them from a checkout
# and never from an installed copy, so the directory is resolved per
# call from the run's project root; a test points it elsewhere by
# setting `_RECORDS`.
_RECORDS: Path | None = None


def _records_dir() -> Path:
    if _RECORDS is not None:
        return _RECORDS
    root = project_root()
    return (
        root if root is not None else Path(__file__).resolve().parents[6]
    ) / "records"


class _Ambiguous(Exception):
    """Two readings whose versions the comparator cannot separate.

    Raised rather than resolved, because every resolution would be a guess:
    see `_observe`. The caller names the tool and leaves its stub alone.
    """

    def __init__(self, key: str, reading: str, base: str) -> None:
        super().__init__(f"{key}: cannot tell {reading} from the recorded {base}")
        self.key, self.reading, self.base = key, reading, base


def _record_path(key: str) -> Path:
    return _records_dir() / f"{key}{RECORD_SUFFIX}"


def default_prefix() -> Path:
    """Where provisioned tools live when no `--prefix` names another place.

    A `toolroom-bench` room in footman's data directory: durable, never
    touched by the cache collector, and moved wholesale by
    `FOOTMAN_DATA_DIR` (or `XDG_DATA_HOME`). One machine-level home,
    instead of a `.tools-latest` in whichever directory the command
    happened to run from, and beside the store's own `toolroom` home,
    never inside it: the store's bin directory is not a provisioned
    prefix.
    """
    return data_dir() / "toolroom-bench"


def _resolve_prefix(prefix: str | Path) -> Path | None:
    """The tree a reading works from: an explicit *prefix* wins; empty falls
    back to `default_prefix()` when it has been provisioned, else `None` —
    the host's own PATH, exactly as an empty prefix always read.
    """
    if prefix:
        return Path(prefix).expanduser().resolve()
    home = default_prefix()
    # Provisioned means a bin directory to read from. The room also holds
    # the bench's store (`_bench_store`), which every refresh creates, and
    # a store is not a provisioned set: read as one, every tool the host
    # has would be "not in the prefix".
    return home if (home / "bin").is_dir() else None


@contextmanager
def _on_path(prefix: str | Path) -> Generator[None]:
    """Read binaries from *prefix*`/bin` for the duration — a
    `fm tools.provision` directory, so a task reads the provisioned set
    instead of whatever this machine happens to have.

    Empty *prefix* falls back to `default_prefix()` when that directory has
    been provisioned, and is a no-op otherwise — so every caller can pass
    its parameter straight through.

    Inside a run the overlay goes through `ctx.env`, which scopes it to this
    task and its children: a sibling's `PATH` is untouched, and footman has
    no reason to draw its own note about a raw `os.environ` write. Called
    bare — from a test, or a script importing the task — there is no router
    to serve that overlay, so it patches `os.environ` and restores it, the
    same bare-call fallback `context._process_state` makes.
    """
    root = _resolve_prefix(prefix)
    if root is None:
        yield
        return
    import os

    bindir = root / "bin"
    inherited = os.environ.get("PATH", "")
    overlay = {"PATH": f"{bindir}{os.pathsep}{inherited}"}
    # A provisioned manual is read the way a provisioned binary is: from
    # the prefix, never from the machine. `man` finds pages by manpath
    # rather than by `PATH`, so it takes a variable of its own.
    if (root / "man").is_dir():
        overlay["FOOTMAN_MANPATH"] = str(root / "man")
    with _overlay(**overlay):
        yield


def _extract(driver: _drivers.Driver, home: Path | None = None) -> ToolSpec:
    """Read an installed tool, with its plugins as the fetch left them.

    A plugin is not on `PATH`: the host tool looks for it under the user's
    home, so the machine's own plugins answer for any release the walk
    installs unless the tool is pointed somewhere else. *home* is where the
    caller put this reading's plugins.

    **A caller that knows the home passes it.** It used to be derived —
    resolve the binary, look beside it — and the derivation found the wrong
    one. `shutil.which` reads `os.environ`, while the walk's `PATH` overlay
    goes to `ctx.env`, so the lookup never saw the release's own directory
    and settled on the provisioned prefix, which has a home of its own
    holding the *latest* plugins. Ten docker releases were read with one
    compose between them, and the five that recorded it recorded the same
    surface five times. Nothing failed; the readings were simply of
    something else.

    The overlay is written here rather than in `_drivers.extract` because
    observations run in parallel: inside a run this routes through
    `ctx.env`, which is this task's own copy, while a bare `os.environ`
    write in the extractor would be every thread's.

    A reading with no home given falls back to the derivation, which is
    right where it is used — `sync --prefix` reads the prefix's binary and
    wants the prefix's plugins.
    """
    if home is None:
        home = _plugin_home(driver)
    if home is None:
        return _drivers.extract(driver)
    with _overlay(HOME=str(home), USERPROFILE=str(home)):
        # Handed over, not discovered: this overlay writes to `ctx.env`, so
        # the tool echoes *this* home while the process still reports the
        # machine's own.
        return _drivers.extract(driver, home=home)


def _plugin_home(driver: _drivers.Driver) -> Path | None:
    """The plugin home beside whichever binary this process resolves.

    For a prefix — `sync --prefix`, `audit --prefix` — that is the right
    answer and the only one available. A walk must not use it: see
    `_extract`.
    """
    from livery.toolroom.bench import _toolfetch

    if not driver.plugins:
        return None
    binary = shutil.which(driver.name)
    if binary is None:
        return None
    home = _toolfetch.home_beside(Path(binary).parent)
    wanted = {plugin.path for plugin in driver.plugins}
    return home if any((home / path).is_dir() for path in wanted) else None


def _fetched_home(driver: _drivers.Driver, placed: Path) -> Path | None:
    """The home this observation's own plugins were fetched into."""
    from livery.toolroom.bench import _toolfetch

    if not driver.plugins:
        return None
    home = _toolfetch.home_beside(placed)
    return home if home.is_dir() else None


@contextmanager
def _reading(driver: _drivers.Driver, placed: Path) -> Generator[None]:
    """Point the extractor at what the install actually placed.

    Most tiers place binaries, and reading them means putting that one
    directory first on `PATH`. A manual tier places pages: there is no
    binary, and `man` finds them by manpath rather than by `PATH`. Same
    shape either way — the release's own copy is what answers, never the
    machine's.
    """
    if driver.provision.kind == "man":
        with _overlay(FOOTMAN_MANPATH=str(placed)):
            yield
        return
    with _bin_on_path(placed):
        yield


@contextmanager
def _bin_on_path(bindir: Path) -> Generator[None]:
    """Read binaries from exactly *bindir* for the duration.

    `_on_path` speaks prefixes and appends `/bin` — right for a provision
    directory, wrong for an installed release: a Windows venv keeps its
    binaries in `Scripts`, and uv's interpreter store keeps `python.exe` at
    the store root. A reconstructed `<parent>/bin` exists on neither, so the
    read silently fell through to whatever ambient binary `PATH` held next.
    """
    import os

    with _overlay(PATH=f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}"):
        yield


@contextmanager
def _overlay(**values: str) -> Generator[None]:
    """Set environment variables for the duration, then put them back.

    Inside a run the overlay goes through `ctx.env`, which scopes it to this
    task and its children: a sibling's environment is untouched, and footman
    has no reason to draw its own note about a raw `os.environ` write. Called
    bare — from a test, or a script importing the task — there is no router
    to serve that overlay, so it patches `os.environ` and restores it, the
    same bare-call fallback `context._process_state` makes.
    """
    import os

    from livery.footman import _globals

    target = current().env if _globals.active() else os.environ
    saved = {key: target.get(key) for key in values}
    target.update(values)
    try:
        yield
    finally:
        for key, was in saved.items():
            if was is None:
                target.pop(key, None)
            else:
                target[key] = was


@contextmanager
def _sandboxed(scratch: Path) -> Generator[None]:
    """Keep everything a prime downloads inside *scratch*.

    uv writes to two places of its own accord, and neither is ours to fill:
    a wheel cache, and the store its managed interpreters live in — the one
    holding the pythons this machine actually runs. A prime of CPython's
    releases put 90 interpreters in that store and left them there, because
    nothing in this file had reason to think it owned them.

    Pointing both inside the scratch directory makes the cleanup structural
    rather than a rule someone has to remember: one `rmtree` at the end
    removes every byte the walk caused, and the interpreter you develop
    against is never a candidate for deletion in the first place.

    `UV_NO_CACHE` is the other half, and the half that decides whether a
    walk fits on the disk at all. `_discard` deletes each release once its
    surface is read, but uv had already unpacked that release's wheels —
    and each CPython's tarball — into its cache, where nothing collects
    them until the run ends. Peak disk then tracked how many releases the
    walk performed instead of how many it held at once: a full gather put
    5 GB into `archive-v0` while the interpreter store sat at 110 MB and
    the venvs churned between 1 MB and 361 MB. That is a wall a CI runner
    meets sooner than a laptop does.

    A cache buys nothing here in any case. It lives inside *scratch*, which
    is created for this walk and deleted at the end of it, so no entry
    written has ever been read by a second run. Turning it off costs the
    re-download of shared dependencies within a run and bounds peak disk by
    concurrency — which is the trade to make, because the walk is parallel
    on purpose and throttling it to save disk would be paying for the same
    space with wall-clock.
    """
    values = {
        "UV_CACHE_DIR": str(scratch / "cache"),
        "UV_PYTHON_INSTALL_DIR": str(scratch / "pythons"),
        "UV_NO_CACHE": "1",
    }
    with _overlay(**values):
        yield


def _windows() -> bool:
    return sys.platform == "win32"


def _platform() -> str:
    return {"darwin": "macOS", "win32": "Windows"}.get(sys.platform, "Linux")


def _generate(driver: _drivers.Driver) -> tuple[Record | None, Record]:
    """Read one installed tool into its record: the record before and after.

    The reading goes into the record, and a stub is rendered from *that*
    by the index build, so what a workspace materialises is a view of the
    record rather than a second record that can disagree with it.
    """
    before = _surfaces.load(_record_path(driver.key))
    return before, _observe(driver, _extract(driver))


def _stub_from(
    driver: _drivers.Driver, record: Record, *, in_process: bool = False
) -> str:
    """The stub text for a tool's record.

    Rendered from the *union*, not the newest release: a flag the tool has
    since dropped stays completable, because the reader may be running a
    version that still has it, and its docstring says when it went. With a
    record of one release the union is that release, so nothing is claimed
    that has not been observed.

    The header reports the newest observation's own platforms rather than
    this machine's: the file says what was read, and a prime run elsewhere
    must not rewrite that claim.
    """
    newest = _surfaces.versions(record)[0]
    spec = _surfaces.union(record, name=driver.name, in_process=in_process)
    return render(
        spec,
        # Every platform that read it, not the first alphabetically: a
        # version observed on two says so, or the header credits one
        # and quietly disowns the other's evidence.
        platform=_and(_surfaces.platforms_of(record, newest) or [_platform()]),
        class_name=_class_name(driver.key),
        in_process=_mode(driver, spec),
    )


def _observe(driver: _drivers.Driver, spec: ToolSpec) -> Record:
    """Record this reading in the tool's record, and return the record.

    Three cases: a first reading opens the record; a reading of a version
    the record already holds is merged into it; any other version is
    placed at its own position, above the newest or below it. A reading
    the comparator cannot place against the newest is declined.
    """
    path = _record_path(driver.key)
    surface = _surfaces.surface_of(spec)
    version = spec.version or "unknown"
    record = _surfaces.load(path)
    if record is None:
        record = _surfaces.new(
            driver.key,
            kind=driver.provision.record_kind,
            version=version,
            date=_today(),
            surface=surface,
            platforms=[_platform()],
            prime=driver.provision.floor,
            runtime=driver.provision.runtime,
        )
    elif version in record.versions:
        # A reading of a version the record already holds: a re-sync on
        # this machine, or another platform's first look. Merged, never
        # written over: overwriting would replace a multi-platform reading
        # with one platform's, erasing every recorded absence while the
        # platforms went on claiming they had looked.
        record, _moved = _surfaces.merge(
            record, version=version, surface=surface, platforms=[_platform()]
        )
    else:
        chain = _surfaces.versions(record)
        newest = chain[0] if chain else ""
        if newest and _version_tuple(version) == _version_tuple(newest):
            # Two builds of one base, eclint's `0.6.0-wk.3` against its
            # `-wk.5`. The comparator cannot separate them and the dates
            # cannot help, because an incoming reading is stamped today
            # whatever build it holds. Declining is the only answer that
            # cannot be wrong, and it is what "a snapshot only ever moves
            # forward" means when forward is unknowable.
            raise _Ambiguous(driver.key, version, newest)
        # An older reading is an older observation, not a new head: the
        # record places it below, and the newest version stays what it is.
        placed = _surfaces.place(
            record,
            version=version,
            date=_today(),
            surface=surface,
            platforms=[_platform()],
        )
        if placed is not None:
            record = placed
    _surfaces.save(record, path)
    return record


def _today() -> str:
    """The observation date. A release's own date belongs to the release, and
    the fetchers will carry it; a live reading only knows when it looked.
    """
    import datetime

    return datetime.date.today().isoformat()


def _render(driver: _drivers.Driver, spec: ToolSpec) -> str:
    return render(
        spec,
        platform=_platform(),
        class_name=_class_name(driver.key),
        in_process=_mode(driver, spec),
    )


def _mode(driver: _drivers.Driver, spec: ToolSpec) -> str:
    """How this tool runs: in footman's process by default, or on request."""
    return driver.mode(spec.in_process)


def _class_name(key: str) -> str:
    return class_name(key)


@tasks.task(name="list")
def list_(
    show: Annotated[
        Literal["all", "installed", "missing"],
        doc("which tools to list (default: all, present or not)"),
    ] = "all",
) -> None:
    """The curated tools: version, in-process capability, stub state.

    Every curated tool is listed by default, absent ones included — the
    version column says `not installed`. `--show installed` narrows to what
    this machine can actually run, `--show missing` to what it can't.

    A *present* tool whose version will not read is a different fact from an
    absent one, and the column says which: `unreadable (timed out after
    30s)`, never a false `not installed` — the CI contradiction that
    conflation produced (a row both passing the installed filter and
    claiming absence) is exactly what the diagnosis channel exists to
    prevent.
    """
    on = wants_color(sys.stdout)
    rows: list[tuple[str, str, str, str]] = []
    shown = [
        (driver, here)
        for driver in _drivers.DRIVERS
        for here in (_drivers.installed(driver),)
        if not (show == "missing" and here) and not (show == "installed" and not here)
    ]
    # Every version is one spawn, read side by side so the table waits
    # for the slowest tool and not for the sum of them; a stalled read
    # is tried again alone, with a longer budget, before it is a fact.
    present = [driver.name for driver, here in shown if here]
    versions = _drivers.read_versions(present)
    for driver, here in shown:
        version = why = ""
        if here:
            version, why = versions[driver.name]
        capable = _drivers.in_process_capable(driver.name) if here else False
        mode = "in-process" if driver.in_process else ("capable" if capable else "—")
        record = "yes" if _record_path(driver.key).exists() else "no"
        blank = f"unreadable ({why})" if here else "not installed"
        rows.append((driver.key, version or blank, mode, record))
    width = max((len(r[0]) for r in rows), default=4)
    print(bold(f"{'tool'.ljust(width)}  version      in-process  record", on))
    for key, version, mode, record in rows:
        print(f"{key.ljust(width)}  {version:<12} {mode:<11} {record}")


@tasks.task
def spec(
    name: Annotated[str, doc("a curated tool: ruff, uv, mkdocs, …")],
    verb: Annotated[str, doc("one verb, dotted for nesting (compose.up)")] = "",
) -> None:
    """Print what a tool says about itself, as footman reads it."""
    driver = _drivers.find(name)
    if driver is None:
        raise SystemExit(f"no driver for {name!r}; try `fm tools.list`")
    if not _drivers.installed(driver):
        raise SystemExit(f"{driver.name} is not installed")
    on = wants_color(sys.stdout)
    extracted = _extract(driver)
    print(bold(f"{extracted.name} {extracted.version}", on), extracted.help)
    for one in extracted.verbs:
        if verb and one.name != verb:
            continue
        label = one.name or "(the tool itself)"
        print(cyan(f"\n  {label}", on), f"— {len(one.options)} options")
        for option in one.options:
            negation = f"  off → {option.negation}" if option.negation else ""
            print(f"    {option.name:<28} {option.type_name:<10}{negation}")


def _from_prefix(binary: str, root: Path) -> bool:
    """Whether *binary* was reached through the provisioned prefix.

    The launcher in `<prefix>/bin` is what counts, not where it points: the
    node tier's scripts live in a shared `node_modules`, and a provisioned
    interpreter lives in uv's own store, so following the symlink would call
    two properly provisioned tools missing.
    """
    path = Path(binary)
    return path.parent == root / "bin" or path.resolve().is_relative_to(root)


def _ignore(driver: _drivers.Driver, root: Path | None) -> str:
    """Why this tool is left alone, or `""` to read it.

    Two ways a reading is worth less than the snapshot already checked in,
    and in both the honest move is to change nothing:

    * **not in the prefix** — a provisioned tool that failed to fetch (or a
      tier that was skipped) would otherwise fall through to whatever the
      host has, quietly turning a partial provision into "the tools moved".
      Only the `system` tier is *meant* to come from the host.
    * **older than the snapshot** — a host-read tool (git, docker) on a
      machine behind the one that took the snapshot. Reading it would
      rewrite the stub *backwards*, losing flags that exist upstream.
    """
    from livery.toolroom.bench import _toolhelp

    manual = _toolhelp._fetched_manpath() if driver.provision.kind == "man" else ""
    if driver.provision.kind == "man" and root is not None and not manual:
        # The pages are the reading, so a prefix without them is a prefix
        # this tool is not in — the same rule every other tier follows.
        return "not in the prefix"
    binary = _drivers._resolve(driver.name)
    if not manual:
        if binary is None:
            return "not installed"
        if root is not None and not _from_prefix(binary, root):
            return "not in the prefix"
    record = _surfaces.load(_record_path(driver.key))
    if record is None:
        return ""
    recorded = _surfaces.versions(record)[0]
    found = (
        _toolhelp.man_version(Path(manual), driver.name)
        if manual
        else _drivers.version(driver.name)
    )
    # One comparator, shared with the bridge: only the leading numeric run
    # counts, so a build tail can never read as "newer than its own base".
    here, snapshot = _version_tuple(found), _version_tuple(recorded)
    if here and snapshot > here:
        return f"older than the snapshot ({found} < {recorded})"
    return ""


def _prefix_root(prefix: str) -> Path | None:
    """The provisioned tree a reading must come from, or None for "anywhere".

    Resolves exactly as `_on_path` does — the default prefix, when it has
    been provisioned, binds the provenance check too, so a reading claimed
    from the default set really came from it.
    """
    return _resolve_prefix(prefix)


@tasks.task
def sync(
    only: Annotated[str, doc("regenerate just this tool")] = "",
    prefix: Annotated[str, doc("read binaries from this prefix's bin/")] = "",
) -> None:
    """Read the tools installed on this machine into their records.

    A reading is a *snapshot*: what one tool accepted at one version, on
    one machine. Point `--prefix` at a `fm tools.provision` directory to
    take that snapshot from the isolated latest set instead of whatever
    this machine has — a dev environment's pytest carries its plugins'
    flags too, and those do not belong in a record whose driver never
    asked for them.

    A tool that isn't installed keeps the record that is checked in —
    there is nothing to read it from, and a record that exists beats one
    that was emptied because a laptop happened to be missing a binary.
    """
    with _on_path(prefix):
        _sync(only, _prefix_root(prefix))


def _sync(only: str, root: Path | None = None) -> None:
    wrote, skipped = [], []
    for driver in _drivers.DRIVERS:
        if only and driver.key != only:
            continue
        if driver.source == "manual":
            continue  # an authored record — never extracted or overwritten
        if reason := _ignore(driver, root):
            # A snapshot only ever moves forward: a reading worth less than
            # the recorded one leaves the record exactly as it is.
            skipped.append(f"{driver.key} ({reason})")
            continue
        try:
            before, after = _generate(driver)
        except _Ambiguous as ambiguous:
            skipped.append(f"{driver.key} ({ambiguous.reading} vs {ambiguous.base})")
            continue
        if before != after:
            wrote.append(driver.key)
    print(f"recorded {len(wrote)} reading(s): {', '.join(wrote) or 'none changed'}")
    if skipped:
        print(f"left alone: {', '.join(skipped)}")


@tasks.task
def audit(
    only: Annotated[str, doc("check just this tool")] = "",
    fix: Annotated[bool, doc("take a fresh snapshot instead of reporting")] = False,
    prefix: Annotated[str, doc("read binaries from this prefix's bin/")] = "",
    strict: Annotated[bool, doc("exit non-zero when a snapshot is behind")] = False,
) -> dict[str, object]:
    """Report which tools have moved on since their record's newest reading.

    A record holds what one tool accepted at the versions it was read
    from. Tools keep releasing, and footman promises no particular speed
    at following them — so a tool showing up here means a newer version
    exists, **not** that anything is wrong. Every stubbed verb ends in
    `**flags: Any`, so the bridge already speaks a flag the record has
    never heard of; only the *hint* is behind. `--fix` takes a fresh
    reading, `--strict` gives automation something to trip on, and
    `--prefix` asks the question against a provisioned latest set rather
    than this machine.

    A snapshot only ever moves **forward**, so two readings are worth less
    than the record already checked in and are named and left alone: a
    tool missing from `--prefix` (a partial provision must not read as
    drift, and the host's copy is not the answer), and one whose version
    is older than the record holds (a machine behind the one that took
    the snapshot has nothing to add). Neither counts as behind — they are
    unanswered.

    One finding here *is* a fault, and always exits non-zero: footman's
    negation and wrapper tables are read by the runtime, so a disagreement
    there means a task emits the wrong command today.
    """
    with _on_path(prefix):
        return _audit(only, fix, strict, _prefix_root(prefix))


def _audit(
    only: str, fix: bool, strict: bool, root: Path | None = None
) -> dict[str, object]:
    from livery.toolroom import tools as _bridge

    stale, skipped, wrong, checked = [], [], [], 0
    for driver in _drivers.DRIVERS:
        if only and driver.key != only:
            continue
        if driver.source == "manual":
            continue  # an authored record — nothing to read it against
        if reason := _ignore(driver, root):
            # Nothing to say about a tool this machine can't read *better*
            # than the snapshot already did — it is not behind, it is
            # unanswered, and the difference matters to a release job.
            skipped.append(f"{driver.key} ({reason})")
            continue
        record = _surfaces.load(_record_path(driver.key))
        spec = _extract(driver)
        fresh = _render(driver, spec)
        held = _stub_from(driver, record) if record is not None else ""
        checked += 1
        if held != fresh:
            stale.append(driver.key)
            if fix:
                _observe(driver, spec)
        # Two extracted facts the *runtime* reads: the negation table `off`
        # consults, and the wrapper set that decides flag ordering. Both
        # must match the installed tool, or a task emits the wrong command.
        if driver.base:
            continue
        found = spec.negations()
        if found != _bridge._NEGATIONS.get(driver.name, {}):
            wrong.append(f"_NEGATIONS[{driver.name!r}] should be {found}")
        wraps = spec.wrappers()
        if wraps != _bridge._WRAPPERS.get(driver.name, frozenset()):
            wrong.append(f"_WRAPPERS[{driver.name!r}] should be {set(wraps)}")
    if skipped:
        print(f"left alone: {', '.join(skipped)}")
    report: dict[str, object] = {
        "checked": checked,
        "behind": stale,
        "skipped": skipped,
        "resnapshotted": bool(fix and stale),
    }
    if wrong:
        # Not news: these two tables are what the *runtime* reads, so a
        # disagreement means the wrong command goes out today.
        raise SystemExit(
            "tools.py runtime tables disagree with the installed tool(s):\n  "
            + "\n  ".join(wrong)
        )
    if not stale:
        print(f"{checked} stub(s) match the tools they were read from")
        return report
    if fix:
        print(f"took a fresh snapshot of {len(stale)}: {', '.join(stale)}")
        return report
    print(
        f"{len(stale)} tool(s) have released a newer version than the stub "
        f"snapshot: {', '.join(stale)}\n"
        f"nothing is broken — the bridge speaks flags the stub hasn't heard "
        f"of. Take a fresh snapshot with `fm tools.sync` when you want one."
    )
    if strict:
        raise SystemExit(2)
    return report


@tasks.task
def color(
    only: Annotated[str, doc("probe just this tool")] = "",
    write: Annotated[bool, doc("regenerate src/toolroom/_colordata.py")] = True,
    prefix: Annotated[str, doc("probe binaries from this prefix's bin/")] = "",
) -> None:
    """Probe how footman forces colour for each installed tool, and regenerate
    the colour data.

    footman spawns over pipes (no PTY), so it forces colour into the tools it
    spawns — by the environment (`FORCE_COLOR`/`NO_COLOR`) for the modern set, by
    the tool's own switch for the few that ignore it. Which is which is *probed*,
    not assumed: each tool is run with colour forced on and off, and the bytes
    read, so a direction is recorded `env`, `flag` (like git's
    `-c color.ui=always`), `none`, or `unprobed` (no trigger figured out).

    Writes `src/toolroom/_colordata.py`, which the bridge reads for its forcing
    table and the docs read for the support table — and only ever from a
    `--prefix`, a `fm tools.provision` directory. A verdict is a claim about a
    *release*, so it has to come from a release someone fetched on purpose:
    without a prefix this machine's own binaries answer, at whatever versions
    it happens to carry and missing whatever it never installed. That reads
    fine as a table on a terminal, which is why it still prints one; it is not
    something to check in.
    """
    root = _prefix_root(prefix)
    with _on_path(prefix):
        _color_probe_and_write(only, write, wants_color(sys.stdout), root)


def _color_probe_and_write(
    only: str, write: bool, on: bool, root: Path | None = None
) -> None:
    from livery.toolroom.bench import _colorprobe

    installed: list[tuple[str, str, str, ToolSpec]] = []
    for driver in _drivers.DRIVERS:
        if only and driver.key != only:
            continue
        if driver.source == "manual" or not _drivers.installed(driver):
            continue
        binary = _drivers._resolve(driver.name)
        if binary is None:
            continue
        if root is not None and not _from_prefix(binary, root):
            # The same rule `sync` reads by: a tool the prefix does not have
            # must not quietly fall through to the host's. A probe is worth
            # nothing if it cannot say which build it ran.
            continue
        # Only a triggered, non-curated tool needs its stub read for a `--color`
        # candidate; a curated tool (git) and an untriggered one (→ `unprobed`)
        # skip the sometimes-slow extraction.
        needs_spec = (
            driver.key in _colorprobe.TRIGGERS
            and driver.key not in _colorprobe._CURATED
        )
        spec: ToolSpec = _extract(driver) if needs_spec else ToolSpec(name=driver.name)
        installed.append((driver.key, driver.name, binary, spec))

    results = _colorprobe.probe_all(installed)
    width = max((len(k) for k in results), default=4)
    print(bold(f"{'tool'.ljust(width)}  {'on':<8}  {'off':<8}  switch", on))
    for key in sorted(results):
        _argv0, verdict = results[key]
        switch = " ".join(verdict.flag.on) if verdict.flag else ""
        print(f"{key.ljust(width)}  {verdict.on:<8}  {verdict.off:<8}  {switch}")

    if not write:
        return
    if only or root is None:
        why = "one tool is not the table" if only else "no --prefix: host binaries"
        print(f"\nnot written ({why})")
        return
    # The data lives beside the module tree: `parents[1]` is the
    # importable package. The docs table is rendered from this file
    # on every docs build, so it follows the data without needing
    # the tools on PATH.
    from livery.toolroom.tools import _colordata

    data = Path(_tools.__file__).resolve().parent / "_colordata.py"
    folded = _colorprobe.merged(_colordata.COLOUR, results)
    data.write_text(_ruff_formatted(_colorprobe.render(folded)), encoding="utf-8")
    print(f"\nwrote {data.name} ({len(folded)} tools, {len(results)} probed here)")


def _ruff_formatted(text: str) -> str:
    """Run generated source through `ruff format` before it lands in `src/`.

    The formatter is one of the gate's checks, so a generated module has
    to satisfy it when written. When ruff cannot run, the text is
    written as rendered and the gate names what is left.
    """
    import subprocess

    cwd, env = _spawn_context()
    try:
        done = subprocess.run(
            ["ruff", "format", "--stdin-filename", "_colordata.py", "-"],
            input=text,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            cwd=cwd,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return text
    return done.stdout or text


class _Installer(Protocol):
    """What the host check asks of a store: its host, and an install by record."""

    @property
    def host(self) -> str: ...

    def ensure(self, record: Record, version: str) -> Ensured: ...


@dataclass(frozen=True)
class HostCheck:
    """One tool's executable check on this host.

    Attributes:
        tool: The record's name.
        version: The version checked, the newest with a build for the host.
        outcome: `ok`, `skipped` for a tool with no build for this host,
            or `failed`.
        detail: What happened, in the words a reviewer acts on.
    """

    tool: str
    version: str
    outcome: str
    detail: str

    def __str__(self) -> str:
        at = f" {self.version}" if self.version else ""
        return f"  {self.tool}{at}: {self.outcome}, {self.detail}"


def verify_host(
    *,
    store: _Installer | None = None,
    only: str = "",
    run: Callable[[list[str]], tuple[int, str]] | None = None,
    extract: Callable[[_drivers.Driver], ToolSpec] | None = None,
) -> list[HostCheck]:
    """The executable checks on this host: each downloaded tool installs, runs, reads.

    For each curated tool whose record is a downloaded kind, the newest
    version with a build for this host is supplied through *store*,
    its first entry point is run with the driver's help flag, and its
    surface is extracted from the installed tree and must describe the
    tool. A download with no entry point is not a program, and the
    presence of the files its env names is the whole check. A tool with
    no build for this host is skipped and says so.
    *store*, *run* and *extract* are the seams the tests drive; the
    defaults are the bench's store, a subprocess with a two-minute
    limit, and the bench's own extractor.
    """
    import subprocess

    from livery.toolroom.store import DOWNLOAD_KINDS

    engine = store or _bench_store()
    checks: list[HostCheck] = []
    for driver in _drivers.DRIVERS:
        if only and driver.key != only:
            continue
        record = _surfaces.load(_record_path(driver.key))
        if record is None or record.kind not in DOWNLOAD_KINDS:
            continue
        version = next(
            (
                delta.version
                for delta in reversed(record.deltas)
                if engine.host in delta.hosts
            ),
            "",
        )
        if not version:
            checks.append(
                HostCheck(driver.key, "", "skipped", f"no build for {engine.host}")
            )
            continue
        try:
            ensured = engine.ensure(record, version)
        except StoreError as error:
            checks.append(HostCheck(driver.key, version, "failed", f"install: {error}"))
            continue
        if not ensured.deployment.entry_points:
            # A download with nothing on PATH (cmake-conan's provider)
            # is reached through its env: the files it names present in
            # the installed tree is the whole check, nothing runs.
            named = [Path(value) for value in ensured.env.values()]
            missing = [path.name for path in named if not path.exists()]
            checks.append(
                HostCheck(
                    driver.key,
                    version,
                    "failed" if missing else "ok",
                    f"{', '.join(missing)} not in the installed tree"
                    if missing
                    else f"{len(named)} file(s) present, a file and not a program",
                )
            )
            continue
        entry = ensured.tool_dir / ensured.deployment.entry_points[0]
        try:
            code, tail = (run or _run_entry)([str(entry), driver.help_flag])
        except OSError as error:
            checks.append(
                HostCheck(
                    driver.key,
                    version,
                    "failed",
                    f"{entry.name} did not start: {error}",
                )
            )
            continue
        except subprocess.TimeoutExpired:
            checks.append(
                HostCheck(
                    driver.key,
                    version,
                    "failed",
                    f"{entry.name} {driver.help_flag} did not return in time",
                )
            )
            continue
        if code != 0:
            checks.append(
                HostCheck(
                    driver.key,
                    version,
                    "failed",
                    f"{entry.name} {driver.help_flag} exited {code}: {tail}",
                )
            )
            continue
        with _bin_on_path(entry.parent):
            spec = (extract or _extract)(driver)
        if not _describes_itself(spec):
            checks.append(
                HostCheck(
                    driver.key,
                    version,
                    "failed",
                    "the surface does not describe the tool",
                )
            )
            continue
        options = sum(len(verb.options) for verb in spec.verbs)
        checks.append(
            HostCheck(
                driver.key,
                version,
                "ok",
                f"{entry.name} runs, {options} option(s) read",
            )
        )
    return checks


def _run_entry(argv: list[str]) -> tuple[int, str]:
    """Run *argv* with a two-minute limit; the exit and the last line of output."""
    import subprocess

    done = subprocess.run(
        argv, capture_output=True, text=True, timeout=120, check=False, errors="replace"
    )
    lines = (done.stdout + done.stderr).strip().splitlines()
    return done.returncode, (lines[-1] if lines else "no output")[:200]


@tasks.task(name="verify-host")
def tools_verify_host(
    only: Annotated[str, doc("check just this tool")] = "",
) -> None:
    """Install, run and read every downloaded tool on this host; red when one fails.

    The six-host verification point's task: for each curated tool of a
    downloaded kind, the newest version with a build for this host
    installs through the bench's store, its entry point runs with the
    driver's help flag, and its surface extracts to a reading that
    describes the tool. A tool with no build for this host is skipped
    and says so.
    """
    checks = verify_host(only=only)
    for check in checks:
        print(check)
    failed = [check for check in checks if check.outcome == "failed"]
    counted = f"{len(checks) - len(failed)} of {len(checks)} tool(s) pass"
    if failed:
        fail(
            f"{counted}: {', '.join(check.tool for check in failed)} failed on this"
            " host"
        )
    print(f"  {counted}")


# How each probed verdict reads in the docs support table.
# `unprobed` is a gap in `_colorprobe.TRIGGERS` rather than a fact about the
# tool — but it is a verdict the store can hold, and a page that raised a
# KeyError on one would take the whole docs build down with it.
_ON_WORD = {
    "env": "environment",
    "none": "— *(no colour over a pipe)*",
    "n/a": "",
    "unprobed": "*(not probed)*",
}
_OFF_WORD = {
    "env": "environment",
    "none": "**can't silence**",
    "n/a": "—",
    "unprobed": "*(not probed)*",
}


_COLOUR_PAGE = """\
<!-- Generated by `fm docs` from src/toolroom/_colordata.py — do not edit. -->

# Colour

A tool decides whether to colour its output by asking whether it is
talking to a terminal. Nothing that captures output is one — footman
spawns over pipes, CI spawns over pipes — so tools go monochrome exactly
when a build log would most benefit from the colour. toolroom forces the
question instead of leaving it to the pipe.

Most tools take the answer from the environment (`FORCE_COLOR`,
`NO_COLOR`). A few ignore it and need their own switch, which is why
this table exists: each direction is **probed**, never assumed. Every
tool below was run with colour forced both ways and the bytes read, so
a cell says what that tool actually did.

Read it as: *environment* — the variables are enough; a `switch` — that
flag is passed for you; *no colour over a pipe* — the tool has no way to
be talked into it; **can't silence** — it colours regardless, an honest
limitation rather than a silent strip (toolroom never rewrites a tool's
bytes, because it cannot tell a stray escape from a deliberate one).

{table}
"""


def colour_page() -> str:
    """The colour support page, rendered from the checked-in colour data.

    Reads `toolroom._colordata`, not a live probe, so a docs build needs
    nothing on PATH and the page says exactly what ships — the same rule
    the per-tool pages follow. `fm tools.color` refreshes the data; the
    page follows on the next build.
    """
    from livery.toolroom.tools import _colordata

    lines = [
        "| Tool | Colour on | Colour off |",
        "| ---- | --------- | ---------- |",
    ]
    for key in sorted(_colordata.COLOUR):
        _argv0, on, off, flag_on, flag_off, _pre = _colordata.COLOUR[key]
        if on == "n/a" and off == "n/a":
            lines.append(f"| `{key}` | *(pass-through wrapper)* | |")
            continue
        on_cell = f"`{' '.join(flag_on)}`" if on == "flag" and flag_on else _ON_WORD[on]
        off_cell = (
            f"`{' '.join(flag_off)}`" if off == "flag" and flag_off else _OFF_WORD[off]
        )
        lines.append(f"| `{key}` | {on_cell} | {off_cell} |")
    return _COLOUR_PAGE.format(table="\n".join(lines))


@tasks.task
def prime(
    only: Annotated[str, doc("prime just this tool")] = "",
    count: Annotated[int, doc("how many releases back to read")] = 20,
    keep: Annotated[bool, doc("leave the throwaway environments behind")] = False,
    prefix: Annotated[str, doc("drive the tiers from this prefix's bin/")] = "",
) -> None:
    """Read past releases into the option history, deepening each chain.

    Reaches below each tool's floor, up to `--count` releases further back.
    Nothing already written is touched, and a release the chain already has
    is skipped — so a prime interrupted by a rate limit is resumed by
    running it again.

    The releases are gathered **in parallel**, a bounded wave at a time —
    installing a release and reading its `--help` depends on no other
    release, and the chain assembles whatever order the observations arrive
    in. A release that will not install, or whose binary will not describe
    itself, is a **hole**: named in the report, filled by a later run, and
    never the end of the tool's walk.

    `--prefix` points at a `fm tools.provision` directory, and the tiers are
    driven from *its* binaries. That is not the same nicety it is on `sync`:
    uv carries CPython's download index inside itself, so a stale uv reports
    a stale newest python and the walk silently starts too low.
    """
    import shutil
    import tempfile

    from livery.toolroom.bench import _toolfetch

    _bounce_bare_call("prime")
    scratch = Path(tempfile.mkdtemp(prefix="footman-prime-"))
    lines: list[str] = []
    try:
        with _on_path(prefix), _sandboxed(scratch):
            drivers, skipped = _curated(only, _toolfetch)
            listings, unreachable = _list_phase(drivers, _toolfetch)
            skipped += [f"{key} ({why})" for key, why in sorted(unreachable.items())]

            plans: dict[str, list[_toolfetch.Release]] = {}
            records: dict[str, Record] = {}
            for driver in drivers:
                if driver.key not in listings:
                    continue
                record = _surfaces.load(_record_path(driver.key))
                if record is None or not _surfaces.versions(record):
                    skipped.append(f"{driver.key} (no record — run `sync` first)")
                    continue
                planned, refused = _plan_prime(record, listings[driver.key], count)
                if refused:
                    lines.append(
                        f"{driver.key} +0 (from {_surfaces.floor(record)}) — {refused}"
                    )
                    continue
                records[driver.key] = record
                plans[driver.key] = planned

            work = [(d, r) for d in drivers if d.key in plans for r in plans[d.key]]
            surfaces = _gather(work, scratch)
            for driver in drivers:
                if driver.key not in plans:
                    continue
                record, fresh, holes = _assemble(
                    driver, records[driver.key], plans[driver.key], surfaces
                )
                note = f" — holes: {', '.join(holes)}" if holes else ""
                lines.append(
                    f"{driver.key} +{len(fresh)} (from {_surfaces.floor(record)}){note}"
                )
    finally:
        if not keep:
            shutil.rmtree(scratch, ignore_errors=True)
    for line in lines:
        print(line)
    if skipped:
        print(f"skipped: {', '.join(skipped)}")


OBSERVATION_SCHEMA = 1
"""The observation document's shape. Bumped when a reader must know."""


@dataclass(frozen=True)
class Gathered:
    """What one platform saw, as data — the document a matrix leg hands on.

    Deliberately portable rather than a CI internal: written by
    `fm tools.gather --out=…`, copied off a Windows box by hand if that is
    how the week goes, and folded by `fm tools.assemble` wherever the store
    lives. Self-describing, because the machine that reads it is not the
    machine that wrote it.
    """

    platform: str
    """Who looked — the one fact every observation in this document shares."""
    observations: dict[str, dict[str, dict[str, Any]]]
    """`tool -> version -> {date, tag, surface}`: what this platform found."""
    holes: dict[str, list[str]] = dataclasses.field(default_factory=dict)
    """Releases this platform meant to read and could not. Carried rather
    than dropped: the assembler reports them, a later run fills them."""
    unreachable: dict[str, str] = dataclasses.field(default_factory=dict)
    """Indexes that would not answer here. Carried so the assembler can tell
    "this leg saw nothing new" from "this leg could not look"."""
    skipped: list[str] = dataclasses.field(default_factory=list)
    """Tools with no index, or no history to add to, on this platform."""

    def document(self) -> dict[str, Any]:
        return {"schema": OBSERVATION_SCHEMA, **dataclasses.asdict(self)}


@tasks.task
def gather(
    only: Annotated[str, doc("gather just this tool")] = "",
    prefix: Annotated[str, doc("drive the tiers from this prefix's bin/")] = "",
    out: Annotated[str, doc("write the observation document here")] = "",
    count: Annotated[int, doc("also reach this many releases below the floor")] = 0,
) -> Gathered:
    """Observe every release this platform has not yet accounted for.

    Half of a refresh, and the half that must happen *on* the platform: a
    Linux box cannot tell you what a tool's `--help` says on Windows. It
    writes no store — only a document saying what this machine saw — so the
    three platforms of a matrix can run at once and nothing races for the
    files.

    What it observes: every release newer than each tool's base, every hole
    the chain reports, and the base itself when this platform has not looked
    at it yet — that last one is what makes a new platform's coverage
    converge on the versions people are actually running, in one pass.
    `--count` also reaches below the floor, for deepening a platform's
    history the way `prime` does.

    `--out` writes the document for another machine to fold; without it the
    document is returned (and printed under `--json`), which is what
    `refresh` uses when both halves run in one process.
    """
    import shutil
    import tempfile

    from livery.toolroom.bench import _toolfetch

    _bounce_bare_call("gather")
    scratch = Path(tempfile.mkdtemp(prefix="footman-gather-"))
    observations: dict[str, dict[str, dict[str, Any]]] = {}
    holes: dict[str, list[str]] = {}
    try:
        with _on_path(prefix), _sandboxed(scratch):
            work, skipped, unreachable = _work_to_do(only, count, _toolfetch)
            surfaces = _gather(work, scratch)
            for driver, release in work:
                surface = surfaces.get(driver.key, {}).get(release.version)
                if surface is None:
                    holes.setdefault(driver.key, []).append(release.version)
                    continue
                observations.setdefault(driver.key, {})[release.version] = {
                    "date": release.date,
                    "tag": release.tag,
                    "surface": surface,
                }
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    found = Gathered(
        platform=_platform(),
        observations=observations,
        holes={key: sorted(v) for key, v in holes.items()},
        unreachable=unreachable,
        skipped=skipped,
    )
    if out:
        target = Path(out).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(found.document(), indent=1, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {target}")
    _report_gather(found)
    return found


def _work_to_do(
    only: str, count: int, fetch: ModuleType
) -> tuple[list[tuple[_drivers.Driver, _toolfetch.Release]], list[str], dict[str, str]]:
    """Every (driver, release) this platform still owes a reading of.

    The listing phase and the plan, with nothing installed and nothing
    read — what `gather` does before it starts working, and all a caller
    needs to know whether there is any work at all.
    """
    drivers, skipped = _curated(only, fetch)
    listings, unreachable = _list_phase(drivers, fetch)
    work = []
    for driver in drivers:
        if driver.key not in listings:
            continue
        record = _surfaces.load(_record_path(driver.key))
        if record is None or not _surfaces.versions(record):
            skipped.append(f"{driver.key} (no record — run `sync` first)")
            continue
        work += [(driver, r) for r in _plan_gather(record, listings[driver.key], count)]
    return work, skipped, unreachable


@dataclass(frozen=True)
class Owed:
    """What a gather would read, without reading any of it."""

    releases: dict[str, list[str]]
    """Versions this platform owes a reading of, per tool."""
    unreachable: dict[str, str]
    """Indexes that would not answer. Not the same as nothing to do — a
    walk that cannot see an index cannot say the index has nothing new."""
    skipped: list[str]
    """Tools with no index to read, or no record to add to."""

    @property
    def total(self) -> int:
        return sum(len(v) for v in self.releases.values())


@tasks.task
def owed(
    only: Annotated[str, doc("ask about just this tool")] = "",
    prefix: Annotated[str, doc("drive the tiers from this prefix's bin/")] = "",
    count: Annotated[int, doc("also reach this many releases below the floor")] = 0,
) -> Owed:
    """What this platform would read, without installing anything.

    A gather provisions every tool and then discovers there is nothing to
    observe — which is most weeks, since a store that is current stays
    current until something ships. Listing is network and nothing else, so
    the question "is there any work" can be answered before the work is
    prepared for.

    One tool must be current for the answer to be true: uv carries
    CPython's download index inside the binary, so a stale uv reports a
    stale newest python and this says "nothing owed" about a release it
    cannot see. Provision uv, ask this, and only then provision the rest.

    An index that would not answer is *not* nothing to do — `unreachable`
    is reported separately for exactly that reason.
    """
    from livery.toolroom.bench import _toolfetch

    _bounce_bare_call("owed")
    with _on_path(prefix):
        work, skipped, unreachable = _work_to_do(only, count, _toolfetch)
    releases: dict[str, list[str]] = {}
    for driver, release in work:
        releases.setdefault(driver.key, []).append(release.version)
    found = Owed(
        releases={k: sorted(v) for k, v in sorted(releases.items())},
        unreachable=unreachable,
        skipped=skipped,
    )
    if found.unreachable:
        for key, why in sorted(found.unreachable.items()):
            print(f"unreachable: {key} ({why})")
    if found.total:
        for tool, versions in found.releases.items():
            print(f"{tool}: {len(versions)} to read")
    print(f"owed: {found.total}")
    return found


def _report_gather(found: Gathered) -> None:
    """Say what this leg saw, and refuse to call a wreck a result.

    The line used to read `wrote obs-linux.json — 33 observations`, which is
    the same line a complete run prints, and the exit status was 0 with 330
    of 363 releases unread. A run that reports success while observing
    almost nothing is worse than one that fails: the document looks
    foldable, and folding it would record a platform where the tools do not
    exist.

    So the counts are always stated together — a truncated read still shows
    both — and a run whose holes outnumber its observations ends non-zero.
    Not any hole: one release whose asset has gone is ordinary, and failing
    on it would teach a weekly job's readers to ignore the exit code. Holes
    in the majority mean the machine, not the tools.
    """
    from livery.footman import fail

    seen = sum(len(v) for v in found.observations.values())
    missed = sum(len(v) for v in found.holes.values())
    print(f"{found.platform}: {seen} observed, {missed} holes")
    for key, versions in sorted(found.holes.items()):
        print(f"  holes in {key}: {', '.join(versions)}")
    for key, why in sorted(found.unreachable.items()):
        print(f"  could not read {key}: {why}")
    if found.skipped:
        print(f"  skipped: {', '.join(found.skipped)}")
    if missed and missed >= seen:
        fail(
            f"{missed} of {missed + seen} releases went unread — this is a "
            "picture of the machine, not of the tools. Fold it and the store "
            "learns that these releases do not exist here.",
            code=75,  # EX_TEMPFAIL: look again, as an unreachable index does
        )


def _plan_gather(
    record: Record, listing: list[_toolfetch.Release], count: int
) -> list[_toolfetch.Release]:
    """Everything this platform still owes an answer on, newest first.

    Four kinds. Releases the chain has never seen; releases it has seen but
    *this* platform has not (the base above all, since that is the version
    people run); releases whose reading predates the current extractor; and
    — when asked — releases below the floor, the way `prime` reaches back.

    The third is what makes the store self-healing. `EXTRACTOR` was recorded
    against every observation from the start and nothing ever read it, so an
    extractor that learned to see more had no way to say so: three twine
    releases sat in the store with no options at all, recorded when the tool
    died before argparse ran, and the only thing that noticed was another
    platform reading them correctly and appearing to disagree. A reading is
    only as good as the extractor that took it, and this is where that is
    acted on rather than merely noted.
    """
    here = _platform()
    known = set(_surfaces.versions(record))
    floor = _surfaces.floor(record)
    wanted = [
        release
        for release in listing
        if release.version not in known
        and _version_tuple(release.version) >= _version_tuple(floor)
    ]
    for release in listing:
        found = _surfaces.observation(record, release.version)
        if found is None:
            continue
        if here not in found.platforms or found.extractor < _surfaces.EXTRACTOR:
            wanted.append(release)
    if count:
        wanted += _plan_prime(record, listing, count)[0]
    seen, unique = set(), []
    for release in wanted:
        if release.version not in seen:
            seen.add(release.version)
            unique.append(release)
    return unique


@tasks.task
def assemble(
    documents: Annotated[list[str], doc("observation documents to fold in")],
    changelog: Annotated[bool, doc("write the events into CHANGELOG.md")] = True,
) -> Refreshed:
    """Fold gathered observations into the store — the single-writer half.

    Every platform's reading of one release is folded into one surface and
    one sidecar *before* the chain is touched, so a matrix run never writes
    the churn of an option being inserted, dropped and resurrected as each
    leg's turn comes. Then releases go in oldest first, and a release the
    chain already holds is merged rather than replaced.

    One process, one owner per file. Three machines committing to
    `tool-history/` on their own would be three whole-file conflicts a week:
    git is not a merge engine for this, and the algebra is.
    """
    return _finish(
        _assemble_documents([_read_document(name) for name in documents]), changelog
    )


def _additions_only(events: dict[str, list[str]]) -> bool:
    """Whether every announced event span only added surface.

    `changes()` is a step back from newest to the predecessor, so its
    `add` key holds what the newer releases *removed* — pure forward
    additions means every span's `add` is empty. Empty events answer
    False: the graded trigger asks "is this safe to ship unattended",
    and nothing to ship is not an answer.
    """
    if not any(events.values()):
        return False
    for key, versions in events.items():
        record = _surfaces.load(_record_path(key))
        if record is None:
            continue
        span = _surfaces.changes(
            record, since=_predecessor(record, versions[0]), until=versions[-1]
        )
        if span.get("add"):
            return False
    return True


def _ingest_events(events: dict[str, list[str]]) -> tuple[dict[str, list[str]], bool]:
    """Verify every event version that has a host; the lines per tool, and the verdict.

    Every host of the version is staged through the machine's store
    from any machine, so the checks run wherever the refresh runs. A
    tool whose event versions have no host contributes nothing and
    passes; a version whose staging fails is a finding, never a
    crash, so the refresh reports it and holds the pull request.
    """
    from livery.toolroom.bench import _ingest

    lines: dict[str, list[str]] = {}
    ok = True
    store: Store | None = None
    for key, versions in sorted(events.items()):
        record = _surfaces.load(_record_path(key))
        if record is None:
            continue
        for version in versions:
            if not record.hosts_of(version):
                continue
            if store is None:
                store = _bench_store()
            report = _ingest.verify(record, version, store=store)
            lines.setdefault(key, []).extend(_ingest.summary(report))
            ok = ok and report.passed
    return lines, ok


def _finish(found: Refreshed, changelog: bool) -> Refreshed:
    """Write the note, say what happened, and refuse to call ignorance news.

    Shared by `assemble` and `refresh` rather than one calling the other: a
    nested task buffers its own output, and the report belongs to whichever
    of the two the caller actually asked for.
    """
    if found.events:
        found = replace(found, additions_only=_additions_only(found.events))
        ingest, ingest_ok = _ingest_events(found.events)
        found = replace(found, ingest=ingest, ingest_ok=ingest_ok)
    if changelog and found.events:
        entries = [
            _entry_for(key, record, versions)
            for key, versions in sorted(found.events.items())
            for record in (_surfaces.load(_record_path(key)),)
            if record is not None
        ]
        found = replace(found, wrote_changelog=_write_changelog(entries))
    _report_refresh(found)
    if found.unreachable:
        from livery.footman import fail

        fail(
            f"{len(found.unreachable)} index(es) would not answer: "
            f"{', '.join(sorted(found.unreachable))}",
            code=75,  # EX_TEMPFAIL: try again, rather than "nothing to do"
        )
    return found


def _read_document(name: str) -> dict[str, Any]:
    """One observation document, refused rather than guessed at when wrong."""
    from livery.footman import fail

    try:
        payload: dict[str, Any] = json.loads(
            Path(name).expanduser().read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as bad:
        fail(f"{name}: not a readable observation document ({bad})", code=64)
    if payload.get("schema") != OBSERVATION_SCHEMA or not payload.get("platform"):
        fail(f"{name}: not an observation document this footman understands", code=64)
    return payload


def _attributed(who_skipped: dict[str, list[str]], legs: int) -> list[str]:
    """Each skip, and — where it was not unanimous — who said it.

    A skip every leg reported is a fact about the tool: the six shells are
    hand-written wherever you ask. A skip only some legs reported is a fact
    about *those boxes*, and reads as the first kind unless it says so.
    Windows has no `man`, so it alone skips the tools read from their
    manuals; unattributed, `git (no man to read the pages with)` in a
    refresh PR looks exactly like a tier nobody is refreshing, when git's
    pages had in fact been read twice over — the same bytes on Linux and
    macOS, which is all a manual has to be read on.
    """
    lines = []
    for line, who in who_skipped.items():
        unanimous = len(who) == legs
        lines.append(line if unanimous else f"{line} — {_and(sorted(who))} only")
    return lines


def _assemble_documents(documents: list[dict[str, Any]]) -> Refreshed:
    """The fold-then-insert core, shared by `assemble` and `refresh`."""
    by_release: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
    meta: dict[str, dict[str, dict[str, Any]]] = {}
    unreachable: dict[str, str] = {}
    who_skipped: dict[str, list[str]] = {}
    holes: dict[str, list[str]] = {}
    for document in documents:
        platform = document["platform"]
        unreachable.update(document.get("unreachable", {}))
        for tool, missing in document.get("holes", {}).items():
            holes[tool] = sorted({*holes.get(tool, []), *missing})
        for line in document.get("skipped", []):
            who_skipped.setdefault(line, []).append(platform)
        for tool, versions in document.get("observations", {}).items():
            for version, seen in versions.items():
                by_release.setdefault(tool, {}).setdefault(version, {})[platform] = (
                    seen["surface"]
                )
                meta.setdefault(tool, {})[version] = seen

    skipped = _attributed(who_skipped, len(documents))
    read: dict[str, list[str]] = {}
    events: dict[str, list[str]] = {}
    artifacts: dict[str, list[str]] = {}
    for tool, versions in sorted(by_release.items()):
        driver = _drivers.find(tool)
        record = _surfaces.load(_record_path(tool))
        if driver is None or record is None:
            skipped.append(f"{tool} (no record — run `sync` first)")
            continue
        # What the record already reached, before this fold. A release
        # above it is news; one below it is history being filled in.
        highest = (_surfaces.versions(record) or [""])[0]
        record, fresh, touched = _fold_into(record, versions, meta[tool])
        if fresh:
            read[tool] = fresh
            if _artifacts.lists_assets(driver) and not driver.base:
                # A verb-bound view (`ruff_format`) reads another driver's
                # binary; that driver's record carries the artifacts.
                record, lines = _record_artifacts(record, driver, fresh, meta[tool])
                artifacts[tool] = lines
        if touched:
            # Saved for a widened coverage too, not only a new release: a
            # week where three platforms merely agreed about what they see
            # is exactly the week whose findings would otherwise be
            # recomputed from scratch every Monday.
            _surfaces.save(record, _record_path(tool))
        if moved := _events_of(record, fresh, above=highest):
            events[tool] = moved
    return Refreshed(
        read=read,
        events=events,
        unreachable=unreachable,
        skipped=skipped,
        holes=holes,
        artifacts=artifacts,
    )


def _record_artifacts(
    record: Record,
    driver: _drivers.Driver,
    fresh: list[str],
    meta: dict[str, dict[str, Any]],
) -> tuple[Record, list[str]]:
    """Record every fresh version's artifacts; the record and the report lines.

    A version whose artifacts cannot be recorded is reported and left
    without them: the reading stands, and the version's hosts arrive
    with a later run or `tools.artifacts`, the way a hole is filled.
    """
    lines: list[str] = []
    for version in fresh:
        tag = str(meta.get(version, {}).get("tag", ""))
        try:
            record, done = _artifacts.record_version(
                record, driver, version, tag, store=_bench_store()
            )
        except _artifacts.ArtifactError as error:
            lines.append(f"  {error}")
            continue
        lines.extend(done.lines(driver.key))
    return record, lines


def _fold_into(
    record: Record,
    versions: dict[str, dict[str, dict[str, Any]]],
    meta: dict[str, dict[str, Any]],
) -> tuple[Record, list[str], bool]:
    """Fold every platform's reading of each release into one record.

    Oldest first, so a release's delta is computed against the release that
    actually precedes it. Returns the record, what it *gained* and whether
    anything moved at all: widened coverage is not news, and must never
    read as a new release, but it is still a finding, and a finding that
    is not written down is one every Monday pays for again. A merge
    stamps the release as read by today's extractor, whatever the reading
    found; unstamped, `_plan_gather` would offer it again every run.
    """
    order = sorted(versions, key=lambda v: (_version_tuple(v), v))
    fresh: list[str] = []
    touched = False
    for version in order:
        surface, absent = _surfaces.fold(versions[version])
        platforms = sorted(versions[version])
        if _surfaces.observation(record, version) is not None:
            before = record
            record, moved = _surfaces.merge(
                record,
                version=version,
                surface=surface,
                platforms=platforms,
                absent=absent,
            )
            touched = touched or moved or record != before
            continue
        placed = _surfaces.place(
            record,
            version=version,
            date=meta[version]["date"],
            surface=surface,
            platforms=platforms,
            absent=absent,
        )
        if placed is not None:
            record = placed
            fresh.append(version)
            touched = True
    return record, fresh, touched


@dataclass(frozen=True)
class Refreshed:
    """What a refresh found — the release decision, as data.

    Returned rather than printed, so `fm --json tools.refresh` hands a
    scheduled job the same answer a person reads.
    """

    read: dict[str, list[str]]
    """Releases newly observed, per tool, oldest first."""
    events: dict[str, list[str]]
    """The subset of those that are *newer than anything seen before* and
    changed the tool's surface. This is the release decision and the
    CHANGELOG line at once — so a backfill, which reaches only downwards,
    is saved and reported but never announced."""
    unreachable: dict[str, str]
    """Indexes that would not answer, and why. Not the same as a tool with
    nothing new — see `_toolfetch.Unreachable`."""
    skipped: list[str]
    """Tools with no index to read, or no record to add to."""
    holes: dict[str, list[str]] = field(default_factory=dict)
    """Releases that were listed but could not be observed — an install that
    failed, a binary that would not describe itself. A hole is not an error:
    the record stays whole by construction, a later run fills it, and until
    then a change the missing release carried reads as arriving at the next
    release actually read."""
    wrote_changelog: bool = False
    """Whether the events reached `CHANGELOG.md`. False with nothing to say,
    and false when the file has no `[Unreleased]` section to write into —
    which a caller should notice rather than assume the notes got written."""
    release: bool = False
    """Whether any tool's surface moved — decision 4, in one line. A field
    rather than a property, and recomputed from `events` on construction:
    `dataclasses.asdict` serialises fields only, and this is the one value
    the scheduled job reads out of `fm --json tools.refresh`."""

    additions_only: bool = False
    """Whether every surface change across every announced event only ADDED
    to a tool's surface — nothing dropped, no verb withdrawn. Half of the
    graded release trigger's green light: additions cannot break a caller.
    False when there are no events at all (nothing to ship is not
    "safe to ship"). Rewordings count as additions-safe: they change what a
    stub *says*, never what a tool accepts."""

    ingest: dict[str, list[str]] = field(default_factory=dict)
    """Per announced tool, the ingest verification's lines: the verdict,
    each finding and each host's structural diff, for every event version
    that has a host ([livery.toolroom.bench._ingest][]). Empty for a tool
    whose versions have no host, since there is nothing to stage."""

    ingest_ok: bool = True
    """Whether every ingest check passed on every host of every event
    version: the other half of the green light. True when nothing had a
    host to check, so a tool read but never downloaded arms as before."""
    artifacts: dict[str, list[str]] = field(default_factory=dict)
    """Per tool read from a forge tier, the artifact step's lines: the
    hosts each fresh version gained, the assets they came from, and any
    version whose artifacts could not be recorded."""

    @property
    def armed(self) -> bool:
        """Whether the pull request arms: additions only and every check passed."""
        return self.additions_only and self.ingest_ok

    def __post_init__(self) -> None:
        object.__setattr__(self, "release", any(self.events.values()))


@tasks.task
def refresh(
    only: Annotated[str, doc("refresh just this tool")] = "",
    prefix: Annotated[str, doc("drive the tiers from this prefix's bin/")] = "",
    changelog: Annotated[bool, doc("write the events into CHANGELOG.md")] = True,
    submit: Annotated[
        bool, doc("commit what moved on a branch and open its pull request")
    ] = False,
) -> Refreshed:
    """Observe what is new on this platform, and fold it into the store.

    `gather` then `assemble`, in one process — the whole job when one
    machine is the whole matrix, and exactly the two halves the weekly
    workflow runs on three machines and one assembler. There is no third
    code path: what runs locally is what runs in CI, with the document
    handed across a function call instead of an artifact.

    Nothing new anywhere means nothing to release, and that is the whole
    exit condition. An index that would not answer is reported and exits
    non-zero rather than counting as "nothing new": a rename or a moved repo
    would otherwise make a tool silently untracked while the job kept
    reporting success.

    With ``--submit``, a refresh that moved a tool's surface is
    committed on a branch of its own and submitted as a pull request:
    armed when every change only added to a surface, the graded
    trigger's green light, and left for a person otherwise. Nothing
    moved means no branch and no pull request.
    """
    found = gather(only=only, prefix=prefix)
    finished = _finish(_assemble_documents([found.document()]), changelog)
    if submit:
        for line in submit_refresh(finished):
            print(line)
    return finished


def submit_refresh(
    found: Refreshed,
    *,
    git: Callable[..., object] | None = None,
    submit: Callable[[list[str]], int] | None = None,
    on: date | None = None,
    dry_run: bool | None = None,
) -> list[str]:
    """Commit a refresh that moved something and open its pull request.

    The branch is ``chore/tools-refresh-<date>``; the pull request is
    armed when `Refreshed.armed` holds, every change an addition and
    every ingest check passed, and unarmed otherwise, so a dropped verb
    or a deployment that does not stand waits for a person. The commit
    body carries the ingest lines, so the pull request's summary is the
    structural diff. A refresh that moved nothing opens nothing and
    says so. Under a dry run nothing is committed or submitted: the
    lines say what would happen, the arming decision included. *git*,
    *submit*, *on* and *dry_run* are the seams the tests drive; the
    defaults are the tool handle, the runner's own submit verb, today,
    and the run's own dry-run flag.

    Returns:
        The lines to print, one per step.
    """
    from livery.toolroom import tools

    if not found.release:
        return ["  nothing moved: no branch, no pull request"]
    when = on or date.today()
    branch = f"chore/tools-refresh-{when:%Y%m%d}"
    moved = ", ".join(sorted(found.events))
    title = f"chore(toolroom): tool refresh {when:%Y-%m-%d}"
    reason = (
        "additions only, every ingest check passed"
        if found.armed
        else "a surface lost something, a person decides"
        if found.ingest_ok
        else "an ingest check found something, a person decides"
    )
    rehearsal = current().dry_run if dry_run is None else dry_run
    if rehearsal:
        return [
            f"  would refresh {moved} on {branch}",
            f"  would submit {'armed' if found.armed else 'unarmed'}: {reason}",
        ]
    run_git = git or tools.git
    body = "\n".join(
        [
            moved,
            *(
                line.strip()
                for key in sorted(found.ingest)
                for line in found.ingest[key]
            ),
        ]
    )
    run_git("switch", "-c", branch)
    run_git("add", "-A")
    run_git("commit", "-m", f"{title}\n\n{body}")
    argv = [_prog(), "submit", f"--title={title}"]
    if found.armed:
        argv.append("--armed")
    code = (submit or _run_submit)(argv)
    if code != 0:
        from livery.footman import fail

        fail(f"the refresh's submit exited {code}; the branch {branch} stands")
    return [
        f"  refreshed {moved} on {branch}",
        f"  submitted {'armed' if found.armed else 'unarmed'}: {reason}",
    ]


def _bench_store() -> Store:
    """The bench's own store, under its room in the runner's data directory."""
    return Store(Home(default_prefix() / "store"))


def _prog() -> str:
    import livery.footman as footman

    return footman.prog()


def _run_submit(argv: list[str]) -> int:
    import subprocess

    cwd, env = _spawn_context()
    return subprocess.run(argv, check=False, cwd=cwd, env=env).returncode


def _spawn_context() -> tuple[Path, dict[str, str]]:
    """The directory and environment a raw spawn runs in, handed over on purpose.

    Inside a run they are the task's own, the overlay included, so a
    provisioned prefix's ruff is the one that formats; outside a run they
    are the process's. Passed explicitly, because a spawn the runner has
    to fill in is a note it refuses.
    """
    import os

    from livery.footman import _globals

    ctx = current()
    if _globals.active():
        return Path(ctx.cwd or Path.cwd()), dict(ctx.env)
    return Path.cwd(), dict(os.environ)


# The changelog stays a checkout fact: the release-note writer edits
# the repository, never an installed copy.
_CHANGELOG = Path(_tools.__file__).resolve().parents[4] / "CHANGELOG.md"


def _entry_for(key: str, record: Record, versions: list[str]) -> str:
    """One CHANGELOG bullet for one tool's refresh.

    Per tool rather than per release: a reader cares that prek gained
    `--glob`, not which patch carried it, and a tool that moved three times
    would otherwise take three bullets to say one thing.

    The span runs from the release *before* the earliest change to the
    newest — a release compared against itself is empty by construction, so
    the predecessor is what makes the first change visible.

    Added and dropped options are named, because they are few and they are
    what someone acts on. Rewordings are counted rather than listed: a
    release can reword half a dozen descriptions without changing what the
    tool accepts, and spelling those out would make the entry a diff dump.
    """
    since = _predecessor(record, versions[0])
    span = _surfaces.changes(record, since=since, until=versions[-1])
    newest = versions[-1]
    # Two keys can share a spelling — a flag on the bare command and on one
    # of its verbs — and a reader wants to be told about `--glob` once.
    added = sorted(
        set(_surfaces.spellings(record, newest, span.get("drop", ())).values())
    )
    dropped = sorted(
        set(_surfaces.spellings(record, since, span.get("add", {})).values())
    )
    # `None` means the newer release added the verb. Anything else is a verb
    # the step back restores or amends, and which of those it is says so in
    # the newer surface rather than in the shape of the payload.
    now = (_surfaces.at(record, newest) or {}).get("verbs", {})
    gained, lost, amended = [], [], 0
    for name, moved in span.get("verbs", {}).items():
        if moved is None:
            gained.append(name)
        elif name not in now:
            lost.append(name)
        else:
            amended += 1
    reworded = len(span.get("revert", {})) + amended

    said: list[str] = []
    if added:
        said.append(f"adds {_names(added)}")
    if dropped:
        said.append(f"drops {_names(dropped)}")
    if gained:
        said.append(
            f"gains the {_names(sorted(gained))} {_plural('command', len(gained))}"
        )
    if lost:
        said.append(
            f"withdraws the {_names(sorted(lost))} {_plural('command', len(lost))}"
        )
    if reworded:
        said.append(f"rewords {reworded} {_plural('description', reworded)}")
    if "help" in span:
        said.append("restates its own description")
    if not said:  # pragma: no cover - only versions with events are offered
        said.append("changes its option surface")

    over = "" if len(versions) == 1 else f", over {len(versions)} releases"
    rest = f" It also {_and(said[1:])}." if len(said) > 1 else ""
    return f"- **{key} {newest}** {said[0]}{over}.{rest}"


def _predecessor(record: Record, version: str) -> str:
    """The observed release just older than *version*, or the oldest there is."""
    chain = _surfaces.versions(record)  # newest first
    if version in chain and chain.index(version) + 1 < len(chain):
        return chain[chain.index(version) + 1]
    return chain[-1]


def _plural(word: str, count: int) -> str:
    return word if count == 1 else f"{word}s"


def _names(items: list[str]) -> str:
    """`a`, `a` and `b`, `a`, `b` and `c` — with the flags in code spans."""
    quoted = [f"`{item}`" for item in items]
    if len(quoted) == 1:
        return quoted[0]
    return f"{', '.join(quoted[:-1])} and {quoted[-1]}"


def _and(clauses: list[str]) -> str:
    if len(clauses) == 1:
        return clauses[0]
    return f"{', '.join(clauses[:-1])} and {clauses[-1]}"


def _write_changelog(entries: list[str], path: Path | None = None) -> bool:
    """Put *entries* under `[Unreleased]` → `### Changed`, in place.

    Written rather than printed because the refresh already edits the
    records and has to land through a PR either way —
    a scheduled job that emitted release notes to stdout would be producing
    them for nobody. `### Changed` because a tool gaining a flag changes
    footman's *stub*; footman itself added nothing.
    """
    path = path or _CHANGELOG
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    lines = text.split("\n")
    try:
        start = next(
            i for i, line in enumerate(lines) if line.startswith("## [Unreleased]")
        )
    except StopIteration:
        return False
    # The next release heading bounds the section; the file may hold only one.
    end = next(
        (
            i
            for i, line in enumerate(lines[start + 1 :], start + 1)
            if line.startswith("## ")
        ),
        len(lines),
    )
    changed = next(
        (
            i
            for i, line in enumerate(lines[start:end], start)
            if line.strip() == "### Changed"
        ),
        -1,
    )
    if changed == -1:
        # Keep a Changelog's order, so a new section lands where a reader
        # expects it rather than at whichever end is easiest to append to.
        after = next(
            (
                i
                for i, line in enumerate(lines[start:end], start)
                if line.strip()
                in ("### Deprecated", "### Removed", "### Fixed", "### Security")
            ),
            end,
        )
        lines[after:after] = ["### Changed", "", *entries, ""]
    else:
        lines[changed + 2 : changed + 2] = entries
    path.write_text("\n".join(lines), encoding="utf-8")
    return True


def _report_refresh(found: Refreshed) -> None:
    """The human-readable half of what `Refreshed` carries."""
    for key, versions in found.read.items():
        moved = found.events.get(key, [])
        note = f" — events in {', '.join(moved)}" if moved else " — no change"
        print(f"{key} +{len(versions)} ({', '.join(versions)}){note}")
    if not found.read:
        print("nothing new")
    print(f"release warranted: {'yes' if found.release else 'no'}")
    for key in sorted(found.artifacts):
        for line in found.artifacts[key]:
            print(line)
    for key in sorted(found.ingest):
        for line in found.ingest[key]:
            print(line)
    if found.events:
        print(
            "ingest checks: every check passed"
            if found.ingest_ok
            else "ingest checks: a finding holds the pull request for a person"
        )
    for key, missing in sorted(found.holes.items()):
        print(f"holes in {key}: {', '.join(missing)} — a later run fills them")
    for key, why in sorted(found.unreachable.items()):
        print(f"could not read {key}: {why}")
    if found.skipped:
        print(f"skipped: {', '.join(found.skipped)}")


def _bounce_bare_call(task: str) -> None:
    """Refuse to gather outside a run, with directions rather than a race.

    Every isolation property the parallel gather leans on — the environ
    router, the per-call env copy at the task boundary, the subprocess env
    injection — belongs to a run. Called bare, all three are absent: the
    observations would share the one real environment across threads, which
    is exactly the cross-contamination this engine exists to remove. One
    implementation, and a bouncer — never a degraded twin.
    """
    from livery.footman import _globals, fail

    if not _globals.active():
        fail(
            f"tools.{task} gathers releases in parallel and needs a run — "
            f"invoke `fm tools.{task}`, or drive it with "
            "livery.footman.testing.Runner in tests"
        )


def _curated(only: str, fetch: ModuleType) -> tuple[list[_drivers.Driver], list[str]]:
    """The drivers a walk can work on, and the ones it names as skipped."""
    chosen: list[_drivers.Driver] = []
    skipped: list[str] = []
    for driver in _drivers.DRIVERS:
        if only and driver.key != only:
            continue
        if not fetch.can_list(driver):
            # A hand-written stub carries the *default* provision kind, so
            # asking it names the `uv` tier for a shell nobody fetches. What
            # makes it unlistable is that nothing reads it at all.
            why = "hand-written" if driver.source == "manual" else driver.provision.kind
            skipped.append(
                f"{driver.key} ({why} tier)"
                if why != "hand-written"
                else f"{driver.key} (hand-written)"
            )
            continue
        if driver.provision.kind == "node" and (
            shutil.which("node") is None
            or (driver.provision.runtime == "bun" and shutil.which("bun") is None)
        ):
            # The same distinction, one tier over. A node-tier package is
            # installed through its runtime and its launcher runs on node,
            # so without them *every* release of the tool fails to install,
            # and each failure was recorded as a hole, which says those
            # releases could not be had. A macOS gather reported 23 of them
            # across cspell and markdownlint; with the runtime in a prefix
            # the same walk read all 23 with none missing.
            missing = "node" if shutil.which("node") is None else "bun"
            skipped.append(f"{driver.key} (no {missing} to install with)")
            continue
        if driver.provision.kind == "man" and shutil.which("man") is None:
            # The pages are the reading, and rendering them takes `man`.
            # Windows has no such thing, and that is not a hole: a hole
            # says a release could not be had, where this says the reader
            # is missing. The pages are the same bytes on every platform,
            # so a box that skips them loses nothing another box records.
            skipped.append(f"{driver.key} (no man to read the pages with)")
            continue
        chosen.append(driver)
    return chosen, skipped


def _list_phase(
    drivers: list[_drivers.Driver], fetch: ModuleType
) -> tuple[dict[str, list[_toolfetch.Release]], dict[str, str]]:
    """Every tool's release listing, fetched concurrently.

    Network-bound and environment-free, so plain thunks are enough.
    `Unreachable` is collected per tool rather than aborting the sweep — the
    tools that could be read still deserve their walk, and the caller
    decides what an unreadable index costs.
    """
    from livery.footman import parallel, step

    listings: dict[str, list[_toolfetch.Release]] = {}
    unreachable: dict[str, str] = {}
    lock = threading.Lock()

    def look(driver: _drivers.Driver) -> Callable[[], None]:
        def call() -> None:
            try:
                found = fetch.releases(driver)
            except fetch.Unreachable as blocked:
                with lock:
                    unreachable[driver.key] = str(blocked)
                return
            with lock:
                listings[driver.key] = found

        call.__name__ = f"list:{driver.key}"
        return call

    calls = [step(look(driver))() for driver in drivers]
    if calls:
        parallel(*calls, keep_going=True)
    return listings, unreachable


def _plan_refresh(
    record: Record, listing: list[_toolfetch.Release]
) -> list[_toolfetch.Release]:
    """Every listed release the chain does not hold, down to its floor.

    Not just the ones above the base: an interior gap — a hole a previous
    gather reported — is this walk's to fill too, or nobody fills it. Below
    the floor stays `prime`'s business, because depth is a budget and a
    refresh must not silently spend it.
    """
    known = set(_surfaces.versions(record))
    floor = _surfaces.floor(record)
    return [
        release
        for release in listing
        if release.version not in known
        and _version_tuple(release.version) >= _version_tuple(floor)
    ]


def _plan_prime(
    record: Record, listing: list[_toolfetch.Release], count: int
) -> tuple[list[_toolfetch.Release], str]:
    """Up to *count* releases below the floor — the backward walk's work.

    The floor is positioned in the *listing*, never compared by date: a base
    carries the date it was observed, so on a first prime a date test admits
    every release ever published. A floor the listing cannot place refuses
    the tool with directions rather than guessing where it belongs.
    """
    known = set(_surfaces.versions(record))
    floor = _surfaces.floor(record)
    below = [index for index, release in enumerate(listing) if release.version == floor]
    if listing and not below:
        return [], f"{floor} is not among the listed releases (sync it forward first)"
    start = below[0] + 1 if below else 0
    return [release for release in listing[start:] if release.version not in known][
        :count
    ], ""


@tasks.task(hidden=True)
def observe(
    tool: str,
    version: str,
    tag: str = "",
    date: str = "",
    published: str = "",
    requires_python: str = "",
    scratch: str = "",
) -> dict[str, Any] | None:
    """Install one release, read what it accepts, and throw it away.

    The unit of the gather, pure in (tool, version): requests for the same
    release dedupe on the futures work key, and arrival order is nobody's
    business: the record places each version at its own position whatever
    order these finish in.

    A real task deliberately, not a helper. The task boundary is what buys
    each observation its own environment: a body call copies the caller's
    overlay, so the `PATH` written around extraction here is this
    observation's alone, while the sandbox variables and prefix `PATH` the
    caller set flow in — and on into every subprocess the tiers spawn.

    `None` — a release that would not install, or a binary that would not
    describe itself — is a hole for the caller to report, never an error:
    the chain stays contiguous by construction, and a later run fills it.
    """
    from livery.toolroom.bench import _toolfetch

    driver = _drivers.find(tool)
    if driver is None or not scratch:  # pragma: no cover - engine-supplied
        return None
    # Every Release field crosses the task boundary, or a fix to the walk
    # silently reverts inside it: the publishing-window cutoff never reached
    # _install_pypi through here, so cmake 4.3.1 kept resolving at the near
    # edge and holing — while the identical install ran clean by hand.
    release = _toolfetch.Release(
        version=version,
        tag=tag,
        date=date,
        published=published,
        requires_python=requires_python,
    )
    placed = _toolfetch.install(driver, release, Path(scratch) / f"{tool}-{version}")
    if placed is None:
        _refuse_a_broken_environment(Path(scratch))
        return None
    try:
        with _reading(driver, placed):
            # The home this walk made, never the one a lookup would find.
            spec = _extract(driver, home=_fetched_home(driver, placed))
    finally:
        _discard(placed)
    if not _describes_itself(spec):
        return None
    if spec.version and not _same_release(spec.version, version):
        # The one lie _describes_itself cannot see: a faithful description
        # of the *wrong* binary. When the release's own directory is missing
        # from PATH (or its install left no binary), the extractor resolves
        # some ambient tool instead and reads it under this release's label —
        # the help-path twin of the guard _from_click already carries. A
        # reading that names a different version is a hole, not an
        # observation.
        return None
    return _surfaces.surface_of(spec)


def _same_release(reported: str, requested: str) -> bool:
    """Whether the binary's self-reported version names *requested*.

    Repack wheels append their own component to the version they wrap —
    PyPI's ninja 1.11.1.4 carries a binary that answers `1.11.1` — so the
    reported version may be a dotted prefix of the requested one. Never the
    other way round: a binary reporting *more* components than the release
    it is supposed to be is some other binary.
    """
    return requested == reported or requested.startswith(f"{reported}.")


_ROOM_TO_WORK = 512 * 1024 * 1024
"""Free space below which an install failure stops meaning anything about
the release it was trying to fetch."""


def _refuse_a_broken_environment(scratch: Path) -> None:
    """Stop the walk when the machine, not the release, is what failed.

    A hole says something specific: *this* release could not be had — its
    asset is gone, its wheel will not build. A disk with no room says
    nothing about any release, and every observation after it fails for the
    same reason. Recorded as holes, that reads as a platform where the tools
    do not exist, and folded, it would encode exactly that lie.

    So the run ends where the disk did, with the same exit code an
    unreachable index uses: try again, rather than believe this.
    """
    import shutil as _shutil

    from livery.footman import fail

    try:
        free = _shutil.disk_usage(scratch).free
    except OSError:  # pragma: no cover - the path we just wrote to
        return
    if free < _ROOM_TO_WORK:
        fail(
            f"only {free // (1024 * 1024)} MB free under {scratch} — an install "
            "failed for want of room, and every release after it would too. "
            "Nothing was recorded: a hole means a release could not be had, "
            "not that the disk ran out.",
            code=75,  # EX_TEMPFAIL, as an unreachable index uses
        )


def _describes_itself(spec: ToolSpec) -> bool:
    """Whether a reading is a description of a tool at all.

    "It printed something" is not the test. A tool whose launcher is missing
    its interpreter still exits with prose on stdout — a Linux box without
    `node` read every npm-tier release as one bare verb, no options, help
    text reading `/usr/bin/env: 'node': No such file or directory` — and the
    extractor faithfully turned that into a surface. Recorded, it says the
    tool accepts nothing, which then folds as an absence on that platform:
    855 options "missing on Linux" for a tool that was never once run.

    So a reading must carry at least one option somewhere, and its prose must
    not be a launcher complaining. Both, because a tool with genuinely no
    options is imaginable while one whose help is an exec error is not.
    """
    if not spec.verbs:
        return False
    if not any(verb.options for verb in spec.verbs):
        return False
    lowered = (spec.help or "").lower()
    return not any(
        broken in lowered
        for broken in (
            "no such file or directory",
            "command not found",
            "is not recognized as an internal or external command",
            "cannot execute",
            "permission denied",
        )
    )


def _gather(
    work: list[tuple[_drivers.Driver, _toolfetch.Release]], scratch: Path
) -> dict[str, dict[str, dict[str, Any] | None]]:
    """Observe every (driver, release) in *work*, a bounded wave at a time.

    Each observation is a body call into `observe` — the task boundary is
    the isolation — and the wave width caps concurrent downloads and peak
    disk in one number: at most that many releases exist on disk at any
    moment. Results land keyed by tool and version, in whatever order the
    pool finishes; an observation that crashes outright simply never
    reports, which reads as a hole exactly like a release that would not
    install, with the traceback in the wave's output.
    """
    from livery.footman import parallel, step
    from livery.footman.context import current

    surfaces: dict[str, dict[str, dict[str, Any] | None]] = {}
    lock = threading.Lock()

    def observing(
        driver: _drivers.Driver, release: _toolfetch.Release
    ) -> Callable[[], None]:
        def call() -> None:
            surface = observe(
                tool=driver.key,
                version=release.version,
                tag=release.tag,
                date=release.date,
                published=release.published,
                requires_python=release.requires_python,
                scratch=str(scratch),
            )
            with lock:
                surfaces.setdefault(driver.key, {})[release.version] = surface

        call.__name__ = f"{driver.key}=={release.version}"
        return call

    calls = [step(observing(driver, release))() for driver, release in work]
    width = current().jobs or 8
    for start in range(0, len(calls), width):
        parallel(*calls[start : start + width], keep_going=True)
    return surfaces


def _assemble(
    driver: _drivers.Driver,
    record: Record,
    planned: list[_toolfetch.Release],
    surfaces: dict[str, dict[str, dict[str, Any] | None]],
) -> tuple[Record, list[str], list[str]]:
    """Place whatever the gather brought home; say what is missing.

    Single-threaded on purpose: the arithmetic is microseconds against the
    installs, and one writer per record means the save needs no
    coordination. Returns the record, the fresh releases oldest first,
    the order a reader tells the story in, and the holes.
    """
    observed_here = surfaces.get(driver.key, {})
    fresh: list[str] = []
    holes: list[str] = []
    for release in planned:
        surface = observed_here.get(release.version)
        if surface is None:
            holes.append(release.version)
            continue
        placed = _surfaces.place(
            record,
            version=release.version,
            date=release.date,
            surface=surface,
            platforms=[_platform()],
        )
        if placed is not None:
            record = placed
            fresh.append(release.version)
    if fresh:
        chain = _surfaces.versions(record)  # newest first
        fresh.sort(key=chain.index, reverse=True)  # oldest first
        _surfaces.save(record, _record_path(driver.key))
    return record, fresh, holes


def _events_of(record: Record, fresh: list[str], *, above: str = "") -> list[str]:
    """Which of *fresh* changed the tool's surface — the release decision.

    Answered from the record rather than remembered from arrival order:
    a release's own changes are its delta against the release read
    before it. A hole just below a release makes that delta span the gap,
    so the change is attributed to the release actually read — the
    record's standing imprecision, reported as the hole.

    Only releases newer than *above* — the newest the record held before
    this fold — are considered. A walk that reaches backwards changes the
    surface at every step it takes, and every one of those steps is a
    change the tool made years ago: filling git's history announced that
    2.44.0 "adds `--no-checkout`" as though it had happened this week. What
    a changelog reports is a release nobody had seen before, not a release
    footman had not got around to reading.
    """
    ceiling = _version_tuple(above) if above else ()
    return [
        version
        for version in fresh
        # Below the ceiling is history being filled in, not news.
        if not (ceiling and _version_tuple(version) <= ceiling)
        and _surfaces.changed(record, version)
    ]


def _discard(bindir: Path) -> None:
    """Delete one release once its surface has been read.

    The walk needs the surface, not the binary, and the surface is in hand by
    the time this is called. Without it a prime holds every release it has
    ever fetched until the run ends — ruff alone would stand up 416
    environments at once — so this is the difference between peak disk being
    one release and being all of them.

    Safe only because `_sandboxed` has put uv's interpreter store inside the
    scratch directory: *bindir*`.parent` is that release's own directory in
    every tier, and for the python tier that would otherwise be an
    interpreter this machine actually uses.
    """
    import shutil

    shutil.rmtree(bindir.parent, ignore_errors=True)


index_tasks = tasks.group("index", help="The published index of the tool records")


@tasks.task(name="verify")
def tools_verify(
    tool: Annotated[str, doc("the curated tool, as its record is named")],
    version: Annotated[
        str, doc("the version to check; the newest with a host when empty")
    ] = "",
) -> None:
    """Run the ingest checks on one version of a tool for every host, from here.

    Each host's artifact is staged through the bench's store and looked
    at; the verdict, each finding and each host's structural diff
    against the version before it print, and a finding is red.
    """
    from livery.toolroom.bench import _ingest

    record = _surfaces.load(_record_path(tool))
    if record is None:
        fail(f"no record of {tool}; the records are {', '.join(_record_names())}")
    chosen = version or next(
        (delta.version for delta in reversed(record.deltas) if delta.hosts), ""
    )
    if not chosen:
        fail(f"{tool} has no version with a host; nothing to stage")
    report = _ingest.verify(record, chosen, store=_bench_store())
    for line in _ingest.summary(report):
        print(line)
    if not report.passed:
        fail(f"{tool} {chosen}: {len(report.findings)} finding(s)")


def _record_names() -> list[str]:
    return [path.stem for path in records_in(_records_dir())]


@tasks.task(name="artifacts")
def tools_artifacts(
    tool: Annotated[str, doc("the curated tool, as its record is named")],
    version: Annotated[str, doc("the version to record; the newest when empty")] = "",
) -> None:
    """Record a version's release artifacts per host, then run the ingest checks.

    For a tool read from a forge tier: the release's asset for each
    host is downloaded once, hashed and written on the version line,
    a host the release has no asset for is left absent, and the nine
    checks then run on every host recorded. The record is saved before
    the checks, so a finding is red with the artifacts in place for
    the fix. A version already read from another index, as a tool has
    before it moves to its own release, is addressed by its version
    when no forge tag is known for it.
    """
    from livery.toolroom.bench import _ingest, _toolfetch

    record = _surfaces.load(_record_path(tool))
    if record is None:
        fail(f"no record of {tool}; the records are {', '.join(_record_names())}")
    driver = _drivers.find(tool)
    if driver is None:
        fail(f"no driver for {tool}")
    if driver.base:
        fail(
            f"{tool} is a view of {driver.name}'s binary bound to"
            f" `{' '.join(driver.base)}`; record {driver.name}"
        )
    chosen = version or (_surfaces.versions(record) or [""])[0]
    if not chosen:
        fail(f"{tool} has no version; read one first")
    tag = ""
    if _artifacts.lists_assets(driver):
        tag = next(
            (r.tag for r in _toolfetch.releases(driver) if r.version == chosen), ""
        )
    store = _bench_store()
    try:
        record, done = _artifacts.record_version(
            record, driver, chosen, tag, store=store
        )
    except _artifacts.ArtifactError as error:
        fail(str(error))
    _surfaces.save(record, _record_path(tool))
    for line in done.lines(tool):
        print(line)
    report = _ingest.verify(record, chosen, store=store)
    for line in _ingest.summary(report):
        print(line)
    if not report.passed:
        fail(f"{tool} {chosen}: {len(report.findings)} finding(s)")


@index_tasks.task(name="build")
def index_build(
    into: Annotated[
        str, doc("the index root; omitted = docs/tools in the workspace")
    ] = "",
    from_genesis: Annotated[
        bool, doc("ignore the build already there and materialise every tool")
    ] = False,
) -> _index.Built:
    """Materialise every record into the index: a strongroom store and its pointer.

    The index is a strongroom store served as static files, one tree per
    tool holding the tool axis, each version's deployment per host and
    its surface as one blob, and beside it `pointer.json` naming each
    tool's current tree. A build into a directory that already
    holds one reads its pointer and reuses every tool whose record did
    not move; `--from-genesis` ignores the pointer and rebuilds every
    tool, and two builds from genesis land the same objects and the same
    pointer.

    Declared as a docs generator, so the site's build writes the index
    under `docs/tools` and the site's deploy serves it at `tools/`.
    """
    target = (
        Path(into).expanduser().resolve()
        if into
        else _records_dir().parent / "docs" / "tools"
    )
    built = _index.build(_records_dir(), target, from_genesis=from_genesis)
    for name in built.rebuilt:
        print(f"built {name} {built.tools[name]}")
    if built.reused:
        print(f"reused {len(built.reused)}: {', '.join(built.reused)}")
    if built.dropped:
        print(f"dropped: {', '.join(built.dropped)}")
    print(f"pointer: {target / _index.POINTER} ({len(built.tools)} tools)")
    return built


# The golden records and the stubs they render to, checked in beside the
# store's tests, since the store renders. A render change fails the gate
# until `fm tools.goldens` moves them in the same change.
_GOLDENS = Path(__file__).resolve().parents[5] / "toolroom-store" / "tests" / "goldens"


def golden_records() -> list[Record]:
    """The golden records, small and shaped to exercise the renderer."""
    return _index.load_records(_GOLDENS / "records")


def golden_path(record: Record, version: str) -> Path:
    """Where *record*'s stub at *version* is checked in."""
    return _GOLDENS / "stubs" / record.name / f"{version}.pyi.golden"


@tasks.task
def goldens(
    check: Annotated[bool, doc("report what differs instead of writing")] = False,
) -> dict[str, list[str]]:
    """Render the golden records, the golden stubs and the records' schema.

    A golden is a small record shaped to exercise the renderer, and its
    stub at every version is checked in, rendered as a consumer renders
    the version it locks. The test compares each render with its golden
    byte for byte, so a change to the renderer fails the gate until this
    verb moves the goldens in the same change, which is what keeps a
    render change deliberate. `records/record.schema.json` is the same
    kind of file, exported from the record's shape and compared by a
    test, so it moves here too. `--check` names what would move and
    writes nothing.
    """
    from livery.toolroom.store import export_schema

    wrote: list[str] = []
    unchanged: list[str] = []
    schema = _records_dir() / "record.schema.json"
    scratch = schema.with_name("record.schema.json.new")
    export_schema(scratch)
    fresh = scratch.read_text(encoding="utf-8")
    scratch.unlink()
    if schema.is_file() and schema.read_text(encoding="utf-8") == fresh:
        unchanged.append("record schema")
    else:
        if not check:
            schema.write_text(fresh, encoding="utf-8")
        wrote.append("record schema")
    catalogue = Catalogue.of_records(_GOLDENS / "records")
    for record in golden_records():
        for version in _surfaces.versions(record):
            path = golden_path(record, version)
            text = catalogue.stub(record.name, version)
            if path.is_file() and path.read_text(encoding="utf-8") == text:
                unchanged.append(f"{record.name} {version}")
                continue
            if not check:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            wrote.append(f"{record.name} {version}")
    verb = "would move" if check else "wrote"
    print(f"{verb} {len(wrote)}: {', '.join(wrote) or 'none'}")
    if unchanged:
        print(f"unchanged: {len(unchanged)}")
    return {"wrote": wrote, "unchanged": unchanged}


@tasks.task
def provision(
    only: Annotated[str, doc("provision just this tool")] = "",
    prefix: Annotated[
        Path | None,
        doc("directory to materialise into; omitted = footman's data dir"),
    ] = None,
    sync_: Annotated[
        bool, doc("run `tools sync` against the prefix afterwards")
    ] = False,
    clean: Annotated[bool, doc("remove the prefix when done")] = False,
    strict: Annotated[bool, doc("fail if any tier could not be provisioned")] = False,
) -> None:
    """Fetch the latest curated tools into an isolated prefix — no pollution.

    The records are read from installed binaries, so syncing against the newest
    release means having it on PATH. This gathers the latest of every curated
    tool under one isolated prefix — `uv tool install` for the PyPI wheels
    (the Rust and C++ tools included), bun's own release then `bun add` for the
    node CLIs, a release asset for the Go ones — touching nothing outside it.
    Omitted, the prefix is the `toolroom-bench` room in footman's data directory,
    which every empty-prefix reading looks in first, so provisioning once
    serves every later `sync`/`audit` on this machine. `--sync` reads the
    records against the prefix; `--clean` deletes it, which is the whole undo.

    `--strict` turns a failed tier into a failed run. Without it the table
    names what did not arrive and the run still succeeds, which is right
    for a person deciding what to do next and wrong for a job that will
    read the prefix and believe it.
    """
    from livery.toolroom.bench import _provision

    # Absolute: bun errors `ReadOnlyFileSystem` on a relative BUN_INSTALL, and
    # an absolute prefix keeps every tier's launchers and env vars unambiguous.
    # Omitted, the binaries land in the default room — the same place every
    # empty-prefix reading looks first.
    prefix = (
        Path(prefix).expanduser().resolve() if prefix is not None else default_prefix()
    )
    outcomes = _provision.provision(_drivers.DRIVERS, prefix, only=only)
    _print_outcomes(outcomes)
    if strict:
        from livery.footman import fail

        # A person at a terminal reads the table and decides what to do
        # next, so a failed tier is named and the rest carries on. A step
        # whose whole purpose is to leave a prefix complete has no such
        # judgement: a run where bun hit a rate limit still said `ok`, and
        # a half-provisioned prefix went into the gather unremarked.
        failed = [out for out in outcomes if out.status == "fail"]
        if failed:
            fail(
                "could not provision "
                + ", ".join(f"{out.key} ({out.detail})" for out in failed),
                code=70,  # EX_SOFTWARE: the prefix is not what was asked for
            )
    if sync_:
        _sync_against(prefix, only)
    else:
        print(
            f'\nput them on PATH:\n  export PATH="{_provision.bin_dir(prefix)}:$PATH"'
        )
    if clean:
        import shutil

        shutil.rmtree(prefix, ignore_errors=True)
        print(f"removed {prefix}")


_MARK = {"ok": "ok", "fail": "FAIL", "skip": "—", "deferred": "parked"}


def _print_outcomes(outcomes: list[_provision.Outcome]) -> None:
    """The provisioning result, one aligned line per tool."""
    width = max((len(o.key) for o in outcomes), default=4)
    for out in outcomes:
        mark = _MARK.get(out.status, out.status)
        print(f"{mark:<6} {out.key.ljust(width)}  {out.kind:<8} {out.detail}")


def _sync_against(prefix: Path, only: str) -> None:
    """Run `sync` with the prefix on PATH, so it reads the fresh binaries."""
    sync(only=only, prefix=str(prefix))


# `platform` is everyone who read the release — "Linux", or "Linux and
# macOS", or all three — so it matches up to the sentence's full stop rather
# than a single word. It was one word when only one machine ever looked, and
# a header naming two silently stopped parsing: every stub read as
# hand-written, which is what the reference table then published.
STUBS_MODULE = "toolroom_stubs"
"""The module the docs pages' stubs are rendered as, for the docs build's renderer."""

_INDEX = """\
---
icon: lucide/layout-grid
---

# Tools

Import a tool by name — `from livery.toolroom.tools import git` — and call it,
`git.commit(…)`. No declaration needed: [the bridge](../../usage.md)
translates keyword arguments into flags mechanically, and every tool on
your PATH already works. These pages document the **stubs**: what each
curated tool accepted at the versions its record was read from, with that
tool's own help text per flag.

Nothing here is a wrapper. The records are read by `fm tools.sync`, which
asks the installed binaries what they take, and `fm tools.audit` reports
which tools have released a newer version since; a workspace materialises
the stubs the records render to with `fm tools.restub`. A flag missing
from a stub still runs — every verb ends in `**flags: Any`, so a stub can
suggest but never forbid.

Where a flag defaults *on*, its documentation names the spelling that
turns it off, because that is the one thing the bridge cannot infer:
`clean=off` emits `mkdocs build --dirty`, not `--no-clean`.

The **In-process** column is a deliberate choice, not a capability dump.
Tasks run concurrently as threads, and a tool call is normally a subprocess —
isolated, trivially parallel. A Python tool with a `[console_scripts]` entry
point *can* run in footman's own process instead, skipping the spawn:

- **default** — footman prefers in-process. `mkdocs` (macOS strips `DYLD_*`
  from subprocesses, so cairo only resolves in-process), `zensical` and
  `coverage` (pure Python) qualify, and their entry points accept an argument
  list, so they stay parallel.
- **available** — an entry point exists but running it in-process buys
  nothing: `basedpyright` ships a Python launcher that just spawns node, so
  footman subprocesses it anyway.
- **no** — a Rust/Go/Node binary with no Python entry point; always a
  subprocess.

See [the footman host page](../../footman.md) for how in-process
tools stay parallel (and the one case that can't).

{table}
"""


def _verb_tree(path: Path) -> dict[str, object]:
    """A stub's verbs, nested the way its classes are.

    A subcommand group is a nested class holding an attribute of that type
    (`class Compose` + `compose: Compose`), so the attribute name is the verb
    and the class is what hangs under it.
    """
    import ast

    def walk(node: ast.ClassDef) -> dict[str, object]:
        classes = {
            item.name: item for item in node.body if isinstance(item, ast.ClassDef)
        }
        out: dict[str, object] = {}
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and not item.name.startswith("_"):
                # `flags` and `argv` are footman's own accessors, written into
                # the classes by the generator — not verbs of the tool, and
                # listing them once per class buries the real ones.
                if item.name not in ("flags", "argv"):
                    out[item.name] = None
            elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                # `build: Build[_R]` — every verb is a class, parameterised by
                # what a call returns, so the annotation is a Subscript.
                ann = item.annotation
                if isinstance(ann, ast.Subscript):
                    ann = ann.value
                if isinstance(ann, ast.Name) and ann.id in classes:
                    # A class with nothing under it is a leaf verb; one that
                    # still holds names is a subcommand group.
                    out[item.target.id] = walk(classes[ann.id]) or None
        return out

    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    return walk(roots[0]) if roots else {}


def _verbs_of(path: Path) -> list[str]:
    """The verbs a stub declares, dotted, for the index table.

    Dotted because that is how they are called: `compose.up`, `pip.install`,
    `tool.install`. Flattened to bare names they read as `up` and collide —
    uv's two `install` verbs are not one verb.
    """
    found: list[str] = []

    def walk(node: dict[str, object], prefix: str) -> None:
        for name, child in node.items():
            if isinstance(child, dict):
                # cast: isinstance narrows to dict[Unknown, Unknown], which
                # invariant-dict checkers refuse to pass on; the tree is
                # str-keyed by construction.
                walk(cast("dict[str, object]", child), f"{prefix}{name}.")
            else:
                found.append(f"{prefix}{name}")

    walk(_verb_tree(path), "")
    return sorted(found)


@tasks.task
def pages(
    out: Annotated[Path, doc("directory to write the reference pages into")],
    nav: Annotated[
        Path | None, doc("the generated docs tree to emit the Tools nav block into")
    ] = None,
    stubs: Annotated[
        Path | None, doc("directory to render the pages' stubs into")
    ] = None,
) -> None:
    """Write one reference page per tool, plus the index table.

    Rendered from the records rather than from the installed tools, so
    the docs build needs nothing on PATH and says exactly what the records
    hold. The stubs the pages point at are written as the module
    `toolroom_stubs` under *stubs*, `<out>/../stubs/toolroom_stubs` by
    default, a search path the docs build adds for its renderer. Tools
    are ordered alphabetically. With *nav*, the Tools nav block is
    emitted into that generated tree too, so the sidebar can never fall
    behind the drivers again.
    """
    out.mkdir(parents=True, exist_ok=True)
    module = stubs if stubs is not None else out.parent / "stubs" / STUBS_MODULE
    module.mkdir(parents=True, exist_ok=True)
    (module / "__init__.pyi").write_text("", encoding="utf-8")
    stubbed: list[tuple[_drivers.Driver, Record]] = []
    for driver in sorted(_drivers.DRIVERS, key=lambda d: d.key):
        record = _surfaces.load(_record_path(driver.key))
        if record is None:
            continue
        (module / f"{driver.key}.pyi").write_text(
            _stub_from(driver, record), encoding="utf-8"
        )
        stubbed.append((driver, record))
    for stale in module.glob("*.pyi"):
        if stale.stem != "__init__" and stale.stem not in {d.key for d, _ in stubbed}:
            stale.unlink()
    rows = ["| Tool | Read from | In-process | Verbs |", "| --- | --- | --- | --- |"]
    for driver, record in stubbed:
        rows.append(_row(driver, record, module / f"{driver.key}.pyi"))
        (out / f"{driver.key}.md").write_text(_page(driver), encoding="utf-8")
    (out / "index.md").write_text(
        _INDEX.format(table="\n".join(rows)), encoding="utf-8"
    )
    if nav is not None:
        write_tools_nav(nav, [d.key for d, _ in stubbed])
    print(f"wrote {len(stubbed)} tool page(s) into {out}")


# The tool entries of the docs nav are emitted as the `tools` nav block
# beside the pages, so a new driver never needs a hand-edit; `nav_keys`
# reads them back for the test that fails when the sidebar falls behind
# `DRIVERS`.
_NAV_ENTRY = _re.compile(r'\{\s*"(?P<key>[^"]+)"\s*=\s*"_generated/tools/')


def write_tools_nav(generated: Path, keys: list[str]) -> Path:
    """Emit the Tools nav block's entries from *keys* into *generated*; the path."""
    from livery.workshop._docs import write_nav_block

    entries = [f'{{ "{k}" = "_generated/tools/{k}.md" }},' for k in keys]
    return write_nav_block(generated, "tools", entries)


def nav_keys(block: Path) -> list[str]:
    """The tool keys the emitted Tools nav block at *block* lists, in order."""
    if not block.is_file():
        return []
    return [m["key"] for m in _NAV_ENTRY.finditer(block.read_text(encoding="utf-8"))]


def _row(driver: _drivers.Driver, record: Record, path: Path) -> str:
    """One line of the index table: what it is, and what it was read from."""
    verbs = _verbs_of(path)
    listed = ", ".join(f"`{v}`" for v in verbs[:5]) or "the tool itself"
    if len(verbs) > 5:
        listed += f", … ({len(verbs)} in all)"
    newest = _surfaces.versions(record)[0]
    version = f"{newest} ({_and(_surfaces.platforms_of(record, newest))})"
    mode = _mode(driver, _surfaces.union(record, name=driver.name))
    home = f" ([docs]({driver.url}))" if driver.url else ""
    return (
        f"| [`{driver.key}`]({driver.key}.md){home} | {version} | {mode} | {listed} |"
    )


def _page(driver: _drivers.Driver) -> str:
    """One tool's reference page — mkdocstrings renders it from the stub.

    One directive is enough: a subcommand group is a *nested* class, which is
    a member, and the renderer walks members. `docker compose up` and its
    flags come along without the page having to name `Docker.Compose`.
    """
    home = f"[{driver.name} documentation]({driver.url})\n\n" if driver.url else ""
    return (
        f"# {driver.key}\n\n{home}"
        f"::: {STUBS_MODULE}.{driver.key}.{_class_name(driver.key)}\n"
        "    options:\n"
        "      show_root_full_path: false\n"
        "      show_source: false\n"
    )


__all__ = ["tasks"]
