"""Keep the `tools.*` stubs honest — `fm tools …`.

The bridge never goes stale, because it transcribes nothing. Its *stub*
can: a stub is a description of a tool at a version, and tools move. These
tasks close that gap by regenerating the description from the installed
tools and by failing a check when the two disagree.

    fm tools.list                  what footman curates, and what's installed
    fm tools.spec ruff             what one tool says about itself, right now
    fm tools.sync                  rewrite the stubs from the installed tools
    fm tools.audit                 which tools have moved past their snapshot
    fm tools.color                 how footman forces colour, per tool

A stub is a snapshot, not a contract: `sync` takes one, `audit` says which
tools have released a newer version since. Being behind is news rather than
a fault, so `audit` reports and exits zero unless you ask for `--strict`,
and the snapshots are retaken at release time rather than the moment a tool
ships. A snapshot only ever moves forward: a tool that isn't installed, is
missing from a `--prefix`, or reads older than the stub already records is
named and left alone — a check that quietly covered three of thirteen would
be worse than no check.
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
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, cast

from livery.toolroom._machinery import _drivers, _stubgen, _toolhistory, _toolspec

if TYPE_CHECKING:
    from types import ModuleType

    from livery.toolroom._machinery import _provision, _toolfetch
from livery.footman._describe import bold, cyan, wants_color
from livery.footman.context import current, data_dir
from livery.footman.params import doc
from livery.footman.registry import Group
from livery.toolroom import version_tuple as _version_tuple

tasks: Group = Group("tools", help="Keep the tools.* stubs honest")

_STUBS = Path(__file__).resolve().parents[1] / "_stubs"
# Repo-only, deliberately outside `src/`: generation reads the history and
# generation is a maintainer task run from a checkout, while users read the
# stubs — which already carry everything the log is for. Shipping it would
# make every install pay for history nobody reads.
# The curated readings ship inside the package: the stubs regenerate
# from them, and the release legs run the suite against the installed
# copy, where a checkout-relative anchor points at nothing.
_HISTORY = Path(__file__).resolve().parent / "_history"


class _Ambiguous(Exception):
    """Two readings whose versions the comparator cannot separate.

    Raised rather than resolved, because every resolution would be a guess:
    see `_observe`. The caller names the tool and leaves its stub alone.
    """

    def __init__(self, key: str, reading: str, base: str) -> None:
        super().__init__(f"{key}: cannot tell {reading} from the recorded {base}")
        self.key, self.reading, self.base = key, reading, base


def _stub_path(key: str) -> Path:
    return _STUBS / f"{key}.pyi"


def _history_path(key: str) -> Path:
    return _HISTORY / f"{key}.json"


def default_prefix() -> Path:
    """Where provisioned tools live when no `--prefix` names another place.

    A `toolroom` room in footman's data directory — durable, never touched
    by the cache collector, and moved wholesale by `FOOTMAN_DATA_DIR` (or
    `XDG_DATA_HOME`). One machine-level home, instead of a `.tools-latest`
    in whichever directory the command happened to run from.
    """
    return data_dir() / "toolroom"


def _resolve_prefix(prefix: str | Path) -> Path | None:
    """The tree a reading works from: an explicit *prefix* wins; empty falls
    back to `default_prefix()` when it has been provisioned, else `None` —
    the host's own PATH, exactly as an empty prefix always read.
    """
    if prefix:
        return Path(prefix).expanduser().resolve()
    home = default_prefix()
    return home if home.is_dir() else None


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


def _extract(driver: _drivers.Driver, home: Path | None = None) -> _toolspec.ToolSpec:
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
    from livery.toolroom._machinery import _toolfetch

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
    from livery.toolroom._machinery import _toolfetch

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
    shims = _node_shim(scratch)
    values = {
        "UV_CACHE_DIR": str(scratch / "cache"),
        "UV_PYTHON_INSTALL_DIR": str(scratch / "pythons"),
        "UV_NO_CACHE": "1",
    }
    if shims is not None:
        import os

        values["PATH"] = f"{shims}{os.pathsep}{os.environ.get('PATH', '')}"
    with _overlay(**values):
        yield


def _node_shim(scratch: Path) -> Path | None:
    """A `node` that is really bun, for the duration of the walk.

    The npm tier installs through bun, and bun runs the packages — but what
    it *installs* is a launcher beginning `#!/usr/bin/env node`. Nothing in a
    provisioned prefix answers to that name: bun stands in for node when bun
    itself runs a script, and the extractor spawns the launcher as a
    subprocess, where the shebang is resolved by the operating system with
    bun nowhere in the chain.

    So on a machine without node the whole tier reads as prose — a Linux box
    observed twelve cspell releases and eleven markdownlint releases as
    `/usr/bin/env: 'node': No such file or directory`. A CI runner has no
    node either, which would have cost the weekly matrix those two tools on
    every leg, on every platform, indefinitely.

    The shim lives in the scratch directory, so it goes when the walk does.
    `provision` writes the same one into the prefix it builds, which is what
    covers the readers that never open a scratch directory at all — `sync`
    among them, where the omission used to cost a poisoned chain.
    """
    import shutil

    from livery.toolroom._machinery._provision import write_node_shim

    bun = shutil.which("bun")
    if bun is None:
        return None
    shims = scratch / "shims"
    return shims if write_node_shim(shims, Path(bun)) is not None else None


def _windows() -> bool:
    return sys.platform == "win32"


def _platform() -> str:
    return {"darwin": "macOS", "win32": "Windows"}.get(sys.platform, "Linux")


def _generate(driver: _drivers.Driver) -> str:
    """The stub text for one installed tool, formatted the way ruff would.

    The reading goes into the history first, and the stub is rendered from
    *that* — so what ships is a view of the record rather than a second
    record that can disagree with it.
    """
    spec = _extract(driver)
    doc = _observe(driver, spec)
    return _stub_from(driver, doc, in_process=spec.in_process)


def _stub_from(
    driver: _drivers.Driver, doc: dict[str, Any], *, in_process: bool = False
) -> str:
    """The stub text for a tool's history.

    Rendered from the *union*, not the newest release: a flag the tool has
    since dropped stays completable, because the reader may be running a
    version that still has it, and its docstring says when it went. With a
    history of one release the union is that release, so nothing is claimed
    that has not been observed.

    The header reports the base observation's own platform rather than this
    machine's — the file says what was read, and a prime run elsewhere must
    not rewrite that claim.
    """
    base = doc["base"]
    spec = _toolhistory.union(doc, name=driver.name, in_process=in_process)
    return _formatted(
        _stubgen.render(
            spec,
            # Every platform that read it, not the first alphabetically: a
            # base observed on two says so, or the header credits one and
            # quietly disowns the other's evidence.
            platform=_and(base.get("platforms") or [_platform()]),
            class_name=_class_name(driver.key),
            in_process=_mode(driver, spec),
        )
    )


def _observe(driver: _drivers.Driver, spec: _toolspec.ToolSpec) -> dict[str, Any]:
    """Record this reading in the tool's history, and return the history.

    Three cases, and the third is the one the format exists for: a first
    reading opens the file; re-reading the release the base already holds
    updates it in place; a *newer* release becomes the base and demotes the
    old one to a delta — one entry rewritten, the rest untouched.
    """
    path = _history_path(driver.key)
    surface = _toolhistory.surface_of(spec)
    version = spec.version or "unknown"
    doc = _toolhistory.load(path)
    if doc is None:
        doc = _toolhistory.new(
            driver.key,
            version=version,
            date=_today(),
            surface=surface,
            platforms=[_platform()],
        )
    elif doc["base"]["version"] == version:
        # A reading of the release the base already holds — a re-sync on this
        # machine, or another platform's first look. Merged, never written
        # over: overwriting replaced a multi-platform base with one
        # platform's reading, erasing every recorded absence while
        # `platforms` went on claiming those platforms had looked. It also
        # left the first delta describing a step from a surface that no
        # longer existed, so everything below replayed wrong — silently, and
        # already possible today whenever an extractor improvement changed
        # the words.
        doc["base"]["extractor"] = _toolhistory.EXTRACTOR
        _toolhistory.merge(
            doc, version=version, surface=surface, platforms=[_platform()]
        )
    elif _version_tuple(version) == _version_tuple(doc["base"]["version"]):
        # Two builds of one base — eclint's `0.6.0-wk.3` against its
        # `-wk.5`. The comparator cannot separate them and the dates cannot
        # help, because an incoming reading is stamped today whatever build
        # it holds. Ordering a chain breaks such a tie on publication date;
        # here there is no such date, so the base does not move. Declining is
        # the only answer that cannot be wrong, and it is what "a snapshot
        # only ever moves forward" means when forward is unknowable.
        raise _Ambiguous(driver.key, version, doc["base"]["version"])
    elif _version_tuple(version) < _version_tuple(doc["base"]["version"]):
        # An *older* reading is an older observation, not a new head. Demoting
        # on any change let a machine with a stale tool rewrite the base and
        # push the newer release down the chain as though it came first — the
        # base only ever moves forward, exactly as a snapshot does.
        _toolhistory.extend(
            doc,
            version=version,
            date=_today(),
            surface=surface,
            platforms=[_platform()],
        )
    else:
        _toolhistory.promote(
            doc,
            version=version,
            date=_today(),
            surface=surface,
            platforms=[_platform()],
        )
    _toolhistory.save(doc, path)
    return doc


def _today() -> str:
    """The observation date. A release's own date belongs to the release, and
    the fetchers will carry it; a live reading only knows when it looked.
    """
    import datetime

    return datetime.date.today().isoformat()


def _render(driver: _drivers.Driver, spec: _toolspec.ToolSpec) -> str:
    return _stubgen.render(
        spec,
        platform=_platform(),
        class_name=_class_name(driver.key),
        in_process=_mode(driver, spec),
    )


def _mode(driver: _drivers.Driver, spec: _toolspec.ToolSpec) -> str:
    """How this tool runs: in footman's process by default, or on request.

    A Python tool publishes a `[console_scripts]` entry point, which is
    what `Tool.__call__` resolves — so the capability is detected, not
    listed. Whether footman *prefers* it is the driver's business.
    """
    if driver.in_process:
        return "default"
    return "available" if spec.in_process else "no"


def _class_name(key: str) -> str:
    return "".join(part.title() for part in key.split("_"))


def _formatted(text: str) -> str:
    """Run the generated text through the linter and formatter that guard the
    repo.

    Generated code lands in `src/`, where `ruff check` and `ruff format
    --check` run on every commit — so it has to satisfy both by construction,
    not by a follow-up nobody remembers. Import sorting is the half a
    formatter cannot do: the generator writes one `from livery.toolroom import
    …` line and ruff's isort has its own opinion about aliased members.
    """
    import subprocess

    for argv in (
        [
            "ruff",
            "check",
            "--fix",
            "--select",
            "I",
            "--stdin-filename",
            "stub.pyi",
            "-",
        ],
        ["ruff", "format", "--stdin-filename", "stub.pyi", "-"],
    ):
        try:
            done = subprocess.run(
                argv,
                input=text,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            return text
        text = done.stdout or text
    return text


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
    for driver in _drivers.DRIVERS:
        here = _drivers.installed(driver)
        if show == "missing" and here:
            continue
        if show == "installed" and not here:
            continue
        version = why = ""
        if here:
            version, why = _drivers._read_version(driver.name)
        capable = _drivers.in_process_capable(driver.name) if here else False
        mode = "in-process" if driver.in_process else ("capable" if capable else "—")
        stub = "yes" if _stub_path(driver.key).exists() else "no"
        blank = f"unreadable ({why})" if here else "not installed"
        rows.append((driver.key, version or blank, mode, stub))
    width = max((len(r[0]) for r in rows), default=4)
    print(bold(f"{'tool'.ljust(width)}  version      in-process  stub", on))
    for key, version, mode, stub in rows:
        print(f"{key.ljust(width)}  {version:<12} {mode:<11} {stub}")


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
    from livery.toolroom._machinery import _toolhelp

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
    stub = _stub_path(driver.key)
    if not stub.exists():
        return ""
    recorded = _header(stub)[0].partition(" ")[0]
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
    """Rewrite the stubs from the tools installed on this machine.

    A stub is a *snapshot*: what one tool accepted at one version, on one
    machine. Point `--prefix` at a `fm tools.provision` directory to take
    that snapshot from the isolated latest set instead of whatever this
    machine has — a dev environment's pytest carries its plugins' flags
    too, and those do not belong in a stub whose driver never asked for
    them.

    A tool that isn't installed keeps the stub that is checked in — there
    is nothing to read it from, and a stub that exists beats one that was
    deleted because a laptop happened to be missing a binary.
    """
    with _on_path(prefix):
        _sync(only, _prefix_root(prefix))


def _sync(only: str, root: Path | None = None) -> None:
    _STUBS.mkdir(exist_ok=True)
    wrote, skipped = [], []
    for driver in _drivers.DRIVERS:
        if only and driver.key != only:
            continue
        if driver.source == "manual":
            continue  # hand-written stub — never extracted or overwritten
        if reason := _ignore(driver, root):
            # A snapshot only ever moves forward: a reading worth less than
            # the checked-in one leaves the stub exactly as it is.
            skipped.append(f"{driver.key} ({reason})")
            continue
        try:
            text = _generate(driver)
        except _Ambiguous as ambiguous:
            skipped.append(f"{driver.key} ({ambiguous.reading} vs {ambiguous.base})")
            continue
        except _stubgen.NameCollision as clash:
            # A verb that would shadow a stub import: keep the checked-in
            # stub and say why, rather than write one that means the wrong
            # thing (see NameCollision).
            skipped.append(f"{driver.key} ({clash})")
            continue
        path = _stub_path(driver.key)
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
            wrote.append(driver.key)
    print(f"wrote {len(wrote)} stub(s): {', '.join(wrote) or 'none changed'}")
    if skipped:
        print(f"left alone: {', '.join(skipped)}")


@tasks.task
def restub(
    only: Annotated[str, doc("re-render just this tool")] = "",
) -> dict[str, object]:
    """Re-render every stub from the checked-in history — no tools, no network.

    A stub is a *rendering* of a stored surface, and the two move for
    different reasons. `sync` re-reads the tools and can change what is
    stored; this changes only how what is already stored is written out.
    Reach for it when the **renderer** moved — a new method on every verb, a
    widened signature — where the readings themselves are exactly as they
    were.

    That separation is what keeps a delta readable. Regenerating through
    `sync` would fold this machine's tool versions into the answer, so a
    template change would arrive mixed with version drift and neither could
    be reviewed. Here the store is opened read-only: whatever comes out
    differs from what is checked in *only* because the renderer does.

    The six shells (`bash`, `zsh`, `fish`, `nu`, `pwsh`, `cmd`) have
    hand-written stubs and no history to render from, so they are listed as
    left alone and edited by hand.
    """
    wrote, unchanged, no_history = [], [], []
    for driver in _drivers.DRIVERS:
        if only and driver.key != only:
            continue
        if driver.source == "manual":
            no_history.append(driver.key)
            continue
        doc_ = _toolhistory.load(_history_path(driver.key))
        if doc_ is None:
            no_history.append(driver.key)
            continue
        path = _stub_path(driver.key)
        # Exactly `assemble`'s call, so a re-render is byte-identical to what
        # the refresh workflow would have written from the same history.
        try:
            text = _stub_from(driver, doc_)
        except _stubgen.NameCollision as clash:
            print(f"refused {driver.key}: {clash}")
            continue
        if path.exists() and path.read_text(encoding="utf-8") == text:
            unchanged.append(driver.key)
            continue
        path.write_text(text, encoding="utf-8")
        wrote.append(driver.key)
    print(f"re-rendered {len(wrote)}: {', '.join(wrote) or 'none changed'}")
    if unchanged:
        print(f"unchanged: {len(unchanged)}")
    if no_history:
        print(f"no history (hand-written): {', '.join(no_history)}")
    return {"rendered": wrote, "unchanged": unchanged, "no_history": no_history}


@tasks.task
def audit(
    only: Annotated[str, doc("check just this tool")] = "",
    fix: Annotated[bool, doc("take a fresh snapshot instead of reporting")] = False,
    prefix: Annotated[str, doc("read binaries from this prefix's bin/")] = "",
    strict: Annotated[bool, doc("exit non-zero when a snapshot is behind")] = False,
) -> dict[str, object]:
    """Report which tools have moved on since their stub snapshot.

    A stub records what one tool accepted at the version it was read from.
    Tools keep releasing, and footman promises no particular speed at
    following them — so a tool showing up here means a newer version exists,
    **not** that anything is wrong. Every stubbed verb ends in
    `**flags: Any`, so the bridge already speaks a flag the stub has never
    heard of; only the *hint* is behind. `--fix` takes a fresh snapshot,
    `--strict` gives automation something to trip on, and `--prefix` asks
    the question against a provisioned latest set rather than this machine.

    A snapshot only ever moves **forward**, so two readings are worth less
    than the file already checked in and are named and left alone: a tool
    missing from `--prefix` (a partial provision must not read as drift,
    and the host's copy is not the answer), and one whose version is older
    than the stub records (a machine behind the one that took the snapshot
    has nothing to add). Neither counts as behind — they are unanswered.

    One finding here *is* a fault, and always exits non-zero: footman's
    negation and wrapper tables are read by the runtime, so a disagreement
    there means a task emits the wrong command today.
    """
    with _on_path(prefix):
        return _audit(only, fix, strict, _prefix_root(prefix))


def _audit(
    only: str, fix: bool, strict: bool, root: Path | None = None
) -> dict[str, object]:
    from livery import toolroom as _bridge

    stale, skipped, wrong, checked = [], [], [], 0
    for driver in _drivers.DRIVERS:
        if only and driver.key != only:
            continue
        if driver.source == "manual":
            continue  # hand-written stub — nothing to compare against
        if reason := _ignore(driver, root):
            # Nothing to say about a tool this machine can't read *better*
            # than the snapshot already did — it is not behind, it is
            # unanswered, and the difference matters to a release job.
            skipped.append(f"{driver.key} ({reason})")
            continue
        path = _stub_path(driver.key)
        spec = _extract(driver)
        fresh = _formatted(_render(driver, spec))
        checked += 1
        if not path.exists() or path.read_text(encoding="utf-8") != fresh:
            stale.append(driver.key)
            if fix:
                path.write_text(fresh, encoding="utf-8")
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
    from livery.toolroom._machinery import _colorprobe

    installed: list[tuple[str, str, str, _toolspec.ToolSpec]] = []
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
        spec: _toolspec.ToolSpec = (
            _extract(driver) if needs_spec else _toolspec.ToolSpec(name=driver.name)
        )
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
    from livery.toolroom import _colordata

    data = Path(__file__).resolve().parents[1] / "_colordata.py"
    folded = _colorprobe.merged(_colordata.COLOUR, results)
    data.write_text(_formatted(_colorprobe.render(folded)), encoding="utf-8")
    print(f"\nwrote {data.name} ({len(folded)} tools, {len(results)} probed here)")


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
    from livery.toolroom import _colordata

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

    from livery.toolroom._machinery import _toolfetch

    _bounce_bare_call("prime")
    scratch = Path(tempfile.mkdtemp(prefix="footman-prime-"))
    lines: list[str] = []
    try:
        with _on_path(prefix), _sandboxed(scratch):
            drivers, skipped = _curated(only, _toolfetch)
            listings, unreachable = _list_phase(drivers, _toolfetch)
            skipped += [f"{key} ({why})" for key, why in sorted(unreachable.items())]

            plans: dict[str, list[_toolfetch.Release]] = {}
            docs: dict[str, dict[str, Any]] = {}
            for driver in drivers:
                if driver.key not in listings:
                    continue
                doc_ = _toolhistory.load(_history_path(driver.key))
                if doc_ is None:
                    skipped.append(f"{driver.key} (no history — run `sync` first)")
                    continue
                planned, refused = _plan_prime(doc_, listings[driver.key], count)
                if refused:
                    lines.append(
                        f"{driver.key} +0 (from {doc_['observed_from']}) — {refused}"
                    )
                    continue
                docs[driver.key] = doc_
                plans[driver.key] = planned

            work = [(d, r) for d in drivers if d.key in plans for r in plans[d.key]]
            surfaces = _gather(work, scratch)
            for driver in drivers:
                if driver.key not in plans:
                    continue
                fresh, holes = _assemble(
                    driver, docs[driver.key], plans[driver.key], surfaces
                )
                note = f" — holes: {', '.join(holes)}" if holes else ""
                lines.append(
                    f"{driver.key} +{len(fresh)}"
                    f" (from {docs[driver.key]['observed_from']}){note}"
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

    from livery.toolroom._machinery import _toolfetch

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
        doc = _toolhistory.load(_history_path(driver.key))
        if doc is None:
            skipped.append(f"{driver.key} (no history — run `sync` first)")
            continue
        work += [(driver, r) for r in _plan_gather(doc, listings[driver.key], count)]
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
    """Tools with no index to read, or no history to add to."""

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
    from livery.toolroom._machinery import _toolfetch

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
    doc: dict[str, Any], listing: list[_toolfetch.Release], count: int
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
    known = set(_toolhistory.observed(doc))
    floor = doc["observed_from"]
    wanted = [
        release
        for release in listing
        if release.version not in known
        and _version_tuple(release.version) >= _version_tuple(floor)
    ]
    for release in listing:
        entry = _toolhistory.entry_of(doc, release.version)
        if entry is None:
            continue
        if (
            here not in entry.get("platforms", [])
            or entry.get("extractor", 0) < _toolhistory.EXTRACTOR
        ):
            wanted.append(release)
    if count:
        wanted += _plan_prime(doc, listing, count)[0]
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
        doc = _toolhistory.load(_history_path(key)) or {}
        span = _toolhistory.changes(
            doc, since=_predecessor(doc, versions[0]), until=versions[-1]
        )
        if span.get("add"):
            return False
    return True


def _finish(found: Refreshed, changelog: bool) -> Refreshed:
    """Write the note, say what happened, and refuse to call ignorance news.

    Shared by `assemble` and `refresh` rather than one calling the other: a
    nested task buffers its own output, and the report belongs to whichever
    of the two the caller actually asked for.
    """
    if found.events:
        found = replace(found, additions_only=_additions_only(found.events))
    if changelog and found.events:
        entries = [
            _entry_for(key, _toolhistory.load(_history_path(key)) or {}, versions)
            for key, versions in sorted(found.events.items())
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
    for tool, versions in sorted(by_release.items()):
        driver = _drivers.find(tool)
        doc_ = _toolhistory.load(_history_path(tool))
        if driver is None or doc_ is None:
            skipped.append(f"{tool} (no history — run `sync` first)")
            continue
        # What the chain already reached, before this fold. A release above
        # it is news; one below it is history being filled in.
        highest = (_toolhistory.observed(doc_) or [""])[0]
        fresh, touched = _fold_into(doc_, tool, versions, meta[tool])
        if fresh:
            read[tool] = fresh
        if touched:
            # Saved for a widened coverage too, not only a new release: a
            # week where three platforms merely agreed about what they see
            # is exactly the week whose findings would otherwise be
            # recomputed from scratch every Monday.
            _toolhistory.save(doc_, _history_path(tool))
            _stub_path(tool).write_text(_stub_from(driver, doc_), encoding="utf-8")
        if moved := _events_of(doc_, fresh, above=highest):
            events[tool] = moved
    return Refreshed(
        read=read,
        events=events,
        unreachable=unreachable,
        skipped=skipped,
        holes=holes,
    )


def _fold_into(
    doc: dict[str, Any],
    tool: str,
    versions: dict[str, dict[str, dict[str, Any]]],
    meta: dict[str, dict[str, Any]],
) -> tuple[list[str], bool]:
    """Fold every platform's reading of each release into one chain.

    Oldest first, so a release's delta is computed against the release that
    actually precedes it. Returns what the chain *gained* and whether
    anything moved at all: widened coverage is not news — it must never read
    as a new release — but it is still a finding, and a finding that is not
    written down is one every Monday pays for again.
    """
    order = sorted(versions, key=lambda v: (_version_tuple(v), v))
    fresh: list[str] = []
    touched = False
    for version in order:
        surface, absent = _toolhistory.fold(versions[version])
        platforms = sorted(versions[version])
        if (entry := _toolhistory.entry_of(doc, version)) is not None:
            was = json.dumps(entry, sort_keys=True)
            moved = _toolhistory.merge(
                doc,
                version=version,
                surface=surface,
                platforms=platforms,
                absent=absent,
            )
            # Read back rather than reused: a surface change *replaces* a
            # delta's entry, and only the base is rewritten in place. The
            # reference taken above therefore goes stale exactly when the
            # fold did the most work, and comparing it answered "nothing
            # moved" for every delta a better extractor had just corrected —
            # so the correction was computed, believed, and dropped unsaved.
            entry = _toolhistory.entry_of(doc, version) or {}
            # This release has now been read by today's extractor, whatever
            # that reading found. Unstamped, `_plan_gather` offers it again
            # every run: the walk that heals the store never records that it
            # healed it, and every gather pays for all of it again.
            entry["extractor"] = _toolhistory.EXTRACTOR
            touched = touched or moved or json.dumps(entry, sort_keys=True) != was
            continue
        if _toolhistory.insert(
            doc,
            version=version,
            date=meta[version]["date"],
            surface=surface,
            platforms=platforms,
        ):
            placed = _toolhistory.entry_of(doc, version) or {}
            if absent:
                placed["absent"] = absent
            fresh.append(version)
            touched = True
    return fresh, touched


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
    """Tools with no index to read, or no history to add to."""
    holes: dict[str, list[str]] = field(default_factory=dict)
    """Releases that were listed but could not be observed — an install that
    failed, a binary that would not describe itself. A hole is not an error:
    the chain stays contiguous by construction, a later run fills it via
    `insert`, and until then a change the missing release carried reads as
    arriving at the next release actually read."""
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
    to a tool's surface — nothing dropped, no verb withdrawn. The graded
    release trigger's green light: additions cannot break a caller, so a
    graded refresh ships them unattended and holds anything else for a
    human. False when there are no events at all (nothing to ship is not
    "safe to ship"). Rewordings count as additions-safe: they change what a
    stub *says*, never what a tool accepts."""

    def __post_init__(self) -> None:
        object.__setattr__(self, "release", any(self.events.values()))


@tasks.task
def refresh(
    only: Annotated[str, doc("refresh just this tool")] = "",
    prefix: Annotated[str, doc("drive the tiers from this prefix's bin/")] = "",
    changelog: Annotated[bool, doc("write the events into CHANGELOG.md")] = True,
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
    """
    found = gather(only=only, prefix=prefix)
    return _finish(_assemble_documents([found.document()]), changelog)


# The changelog stays a checkout fact: the release-note writer edits
# the repository, never an installed copy.
_CHANGELOG = Path(__file__).resolve().parents[4] / "CHANGELOG.md"


def _entry_for(key: str, doc: dict[str, Any], versions: list[str]) -> str:
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
    since = _predecessor(doc, versions[0])
    span = _toolhistory.changes(doc, since=since, until=versions[-1])
    newest = versions[-1]
    # Two keys can share a spelling — a flag on the bare command and on one
    # of its verbs — and a reader wants to be told about `--glob` once.
    added = sorted(
        set(_toolhistory.spellings(doc, newest, span.get("drop", ())).values())
    )
    dropped = sorted(
        set(_toolhistory.spellings(doc, since, span.get("add", {})).values())
    )
    # `None` means the newer release added the verb. Anything else is a verb
    # the step back restores or amends, and which of those it is says so in
    # the newer surface rather than in the shape of the payload.
    now = (_toolhistory.at(doc, newest) or {}).get("verbs", {})
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


def _predecessor(doc: dict[str, Any], version: str) -> str:
    """The observed release just older than *version*, or the oldest there is."""
    chain = _toolhistory.observed(doc)  # newest first
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

    Written rather than printed because the refresh already edits
    `tool-history/` and the stubs and has to land through a PR either way —
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
        if driver.provision.kind == "node" and shutil.which("bun") is None:
            # The same distinction, one tier over. bun is how the node tier
            # installs, so without it *every* release of every node tool
            # fails to install — and each failure was recorded as a hole,
            # which says those releases could not be had. A macOS gather
            # reported 23 of them across cspell and markdownlint; with bun
            # in a prefix the same walk read all 23 with none missing.
            skipped.append(f"{driver.key} (no bun to install with)")
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
    doc: dict[str, Any], listing: list[_toolfetch.Release]
) -> list[_toolfetch.Release]:
    """Every listed release the chain does not hold, down to its floor.

    Not just the ones above the base: an interior gap — a hole a previous
    gather reported — is this walk's to fill too, or nobody fills it. Below
    the floor stays `prime`'s business, because depth is a budget and a
    refresh must not silently spend it.
    """
    known = set(_toolhistory.observed(doc))
    floor = doc["observed_from"]
    return [
        release
        for release in listing
        if release.version not in known
        and _version_tuple(release.version) >= _version_tuple(floor)
    ]


def _plan_prime(
    doc: dict[str, Any], listing: list[_toolfetch.Release], count: int
) -> tuple[list[_toolfetch.Release], str]:
    """Up to *count* releases below the floor — the backward walk's work.

    The floor is positioned in the *listing*, never compared by date: a base
    carries the date it was observed, so on a first prime a date test admits
    every release ever published. A floor the listing cannot place refuses
    the tool with directions rather than guessing where it belongs.
    """
    known = set(_toolhistory.observed(doc))
    floor = doc["observed_from"]
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
    business — `_toolhistory.insert` assembles the chain from whatever order
    these finish in.

    A real task deliberately, not a helper. The task boundary is what buys
    each observation its own environment: a body call copies the caller's
    overlay, so the `PATH` written around extraction here is this
    observation's alone, while the sandbox variables and prefix `PATH` the
    caller set flow in — and on into every subprocess the tiers spawn.

    `None` — a release that would not install, or a binary that would not
    describe itself — is a hole for the caller to report, never an error:
    the chain stays contiguous by construction, and a later run fills it.
    """
    from livery.toolroom._machinery import _toolfetch

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
    return _toolhistory.surface_of(spec)


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


def _describes_itself(spec: _toolspec.ToolSpec) -> bool:
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
    doc: dict[str, Any],
    planned: list[_toolfetch.Release],
    surfaces: dict[str, dict[str, dict[str, Any] | None]],
) -> tuple[list[str], list[str]]:
    """Insert whatever the gather brought home; say what is missing.

    Single-threaded on purpose: the arithmetic is microseconds against the
    installs, the doc is mutated in place, and one writer per file means the
    atomic save needs no coordination. Returns the fresh releases oldest
    first — the order a reader tells the story in — and the holes.
    """
    observed_here = surfaces.get(driver.key, {})
    fresh: list[str] = []
    holes: list[str] = []
    for release in planned:
        surface = observed_here.get(release.version)
        if surface is None:
            holes.append(release.version)
            continue
        if _toolhistory.insert(
            doc,
            version=release.version,
            date=release.date,
            surface=surface,
            platforms=[_platform()],
        ):
            fresh.append(release.version)
    if fresh:
        chain = _toolhistory.observed(doc)  # newest first
        fresh.sort(key=chain.index, reverse=True)  # oldest first
        _toolhistory.save(doc, _history_path(driver.key))
        # The stub is a rendering of the record, so it follows the record
        # rather than waiting for someone to remember a `sync`.
        _stub_path(driver.key).write_text(_stub_from(driver, doc), encoding="utf-8")
    return fresh, holes


def _events_of(doc: dict[str, Any], fresh: list[str], *, above: str = "") -> list[str]:
    """Which of *fresh* changed the tool's surface — the release decision.

    Answered from the assembled chain rather than remembered from arrival
    order: a release's own changes live in the delta keyed by its
    predecessor, the step back *from* it. A hole just below a release makes
    that delta span the gap, so the change is attributed to the release
    actually read — the chain's standing imprecision, reported as the hole.

    Only releases newer than *above* — the newest the chain held before
    this fold — are considered. A walk that reaches backwards changes the
    surface at every step it takes, and every one of those steps is a
    change the tool made years ago: filling git's history announced that
    2.44.0 "adds `--no-checkout`" as though it had happened this week. What
    a changelog reports is a release nobody had seen before, not a release
    footman had not got around to reading.
    """
    chain = _toolhistory.observed(doc)  # newest first
    ceiling = _version_tuple(above) if above else ()
    changed: list[str] = []
    for version in fresh:
        if ceiling and _version_tuple(version) <= ceiling:
            continue  # history being filled in, not news
        spot = chain.index(version)
        if spot + 1 >= len(chain):
            continue  # the floor: nothing below to have changed from
        step = doc["deltas"][chain[spot + 1]]
        if any(key not in ("date", "platforms", "extractor") for key in step):
            changed.append(version)
    return changed


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


@dataclass(frozen=True)
class Prepared:
    """A release rolled, as data — what the tag step needs to know."""

    version: str
    """The version the tree now claims, `X.Y.Z`."""
    previous: str
    """What it claimed before, for the compare link and for a sanity check."""
    entries: int
    """How many bullets moved out of `[Unreleased]`."""


@tasks.task(name="prepare-release")
def prepare_release(
    bump: Annotated[Literal["patch", "minor"], doc("which part to raise")] = "patch",
) -> Prepared:
    """Roll the version and the changelog, the way the runbook does by hand.

    A stub-only release is a patch bump — the tools moved, toolroom did
    not — which is why `patch` is the default and the automatic path never
    chooses anything else.

    Two files must agree or the release workflow refuses the tag
    (`pyproject.toml` and `__init__.__version__`). The README's beta note
    rolls with them: its minor-pin example (`toolroom~=X.Y.0`) follows the
    released minor, so a minor bump rewrites it and a patch bump leaves it
    — the pin already admits the patch (`tests/test_docs_drift.py` fails
    the gate if it goes stale).

    `[Unreleased]` becomes `[X.Y.Z]` dated today, with the compare links
    repointed. Refuses rather than guesses when there is nothing to release.
    """
    from livery.footman import fail

    root = _HISTORY.parent
    current = _re.search(
        r'^version = "([^"]+)"', (root / "pyproject.toml").read_text("utf-8"), _re.M
    )
    if current is None:  # pragma: no cover - pyproject always carries one
        fail("pyproject.toml has no version to raise", code=64)
    previous = current[1]
    major, minor, patch = (int(part) for part in previous.split(".")[:3])
    version = (
        f"{major}.{minor}.{patch + 1}" if bump == "patch" else f"{major}.{minor + 1}.0"
    )

    moved = _roll_changelog(root / "CHANGELOG.md", version, previous)
    if not moved:
        fail(
            "nothing under [Unreleased] to release — the tree is already "
            "where the last tag left it",
            code=64,
        )
    for path, pattern, replacement in (
        (
            root / "pyproject.toml",
            rf'^version = "{previous}"',
            f'version = "{version}"',
        ),
        (
            root / "src" / "toolroom" / "__init__.py",
            rf'^__version__ = "{previous}"',
            f'__version__ = "{version}"',
        ),
        (
            root / "README.md",
            r"toolroom~=\d+\.\d+\.0",
            "toolroom~={}.{}.0".format(*version.split(".")[:2]),
        ),
    ):
        text = path.read_text(encoding="utf-8")
        path.write_text(
            _re.sub(pattern, replacement, text, count=1, flags=_re.M), "utf-8"
        )
    print(f"prepared {previous} -> {version} ({moved} entries)")
    return Prepared(version=version, previous=previous, entries=moved)


def _roll_changelog(path: Path, version: str, previous: str) -> int:
    """`[Unreleased]` becomes the new release, and the links follow.

    Returns how many bullets moved, so a caller can refuse to cut a release
    out of an empty section rather than tagging a no-op.
    """
    import datetime

    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    start = next(
        i for i, line in enumerate(lines) if line.startswith("## [Unreleased]")
    )
    end = next(
        (
            i
            for i, line in enumerate(lines[start + 1 :], start + 1)
            if line.startswith("## [")
        ),
        len(lines),
    )
    entries = sum(1 for line in lines[start:end] if line.startswith("- "))
    if not entries:
        return 0
    today = datetime.datetime.now(tz=datetime.UTC).date().isoformat()
    lines[start : start + 1] = [
        "## [Unreleased]",
        "",
        f"## [{version}] — {today}",
    ]
    rolled = "\n".join(lines)
    repo = "https://github.com/willemkokke/toolroom"
    rolled = rolled.replace(
        f"[Unreleased]: {repo}/compare/v{previous}...HEAD",
        f"[Unreleased]: {repo}/compare/v{version}...HEAD\n"
        f"[{version}]: {repo}/compare/v{previous}...v{version}",
    )
    path.write_text(rolled, encoding="utf-8")
    return entries


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

    The stubs are read from installed binaries, so syncing against the newest
    release means having it on PATH. This gathers the latest of every curated
    tool under one isolated prefix — `uv tool install` for the PyPI wheels
    (the Rust and C++ tools included), bun's own release then `bun add` for the
    node CLIs, a release asset for the Go ones — touching nothing outside it.
    Omitted, the prefix is the `toolroom` room in footman's data directory,
    which every empty-prefix reading looks in first, so provisioning once
    serves every later `sync`/`audit` on this machine. `--sync` rewrites the
    stubs against the prefix; `--clean` deletes it, which is the whole undo.

    `--strict` turns a failed tier into a failed run. Without it the table
    names what did not arrive and the run still succeeds, which is right
    for a person deciding what to do next and wrong for a job that will
    read the prefix and believe it.
    """
    from livery.toolroom._machinery import _provision

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
_READ_FROM = _re.compile(
    r"Read from (?P<tool>\S+) (?P<version>\S+) on (?P<platform>[^.]+)\."
    r"(?: In-process: (?P<mode>\w+)\.)?"
)

_INDEX = """\
---
icon: lucide/layout-grid
---

# Tools

Import a tool by name — `from livery.toolroom import git` — and call it,
`git.commit(…)`. No declaration needed: [the bridge](../../usage.md)
translates keyword arguments into flags mechanically, and every tool on
your PATH already works. These pages document the **stubs**: what each
curated tool accepted at the version footman last read it from, with that
tool's own help text per flag.

Nothing here is a wrapper. The stubs are generated by `fm tools.sync`,
which asks the installed binaries what they take, and `fm tools.audit`
reports which tools have released a newer version since. A flag missing
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


def _header(path: Path) -> tuple[str, str]:
    """`(read from, in-process)` as a checked-in stub records them.

    The table is built from the files rather than from the tools, so
    building the docs needs nothing on PATH and the page says exactly what
    ships — including for the tools this machine cannot ask.
    """
    head = path.read_text(encoding="utf-8")[:600].replace("\n# ", " ")
    match = _READ_FROM.search(head)
    if not match:
        # A hand-written stub exists precisely because the tool is not a
        # Python package to extract from (the shells, cmd): there is no
        # entry point to call, so in-process is structurally "no" — not
        # unknown.
        return "hand-written", "no"
    return f"{match['version']} ({match['platform']})", match["mode"] or "unknown"


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
        Path | None, doc("a config whose Tools nav block to rewrite")
    ] = None,
) -> None:
    """Write one reference page per tool, plus the index table.

    Built from the checked-in stubs rather than from the installed tools, so
    the docs build needs nothing on PATH and says exactly what ships. Tools
    are ordered alphabetically. With *nav*, the tool entries of that config's
    Tools list are regenerated too (between markers), so the sidebar can never
    fall behind the drivers again.
    """
    out.mkdir(parents=True, exist_ok=True)
    stubbed = sorted(
        (d for d in _drivers.DRIVERS if _stub_path(d.key).exists()),
        key=lambda d: d.key,
    )
    rows = ["| Tool | Read from | In-process | Verbs |", "| --- | --- | --- | --- |"]
    for driver in stubbed:
        rows.append(_row(driver, _stub_path(driver.key)))
        (out / f"{driver.key}.md").write_text(_page(driver), encoding="utf-8")
    (out / "index.md").write_text(
        _INDEX.format(table="\n".join(rows)), encoding="utf-8"
    )
    if nav is not None:
        write_tools_nav(nav, [d.key for d in stubbed])
    print(f"wrote {len(stubbed)} tool page(s) into {out}")


# The tool entries of the docs nav are regenerated between these markers, so a
# new driver never needs a hand-edit — `nav_keys` reads them back for the test
# that fails when the sidebar falls behind `DRIVERS`.
_NAV_BEGIN = "# nav:begin tools"
_NAV_END = "# nav:end tools"
_NAV_RE = _re.compile(
    _re.escape(_NAV_BEGIN) + r".*?" + _re.escape(_NAV_END), _re.DOTALL
)
_NAV_ENTRY = _re.compile(r'\{\s*"(?P<key>[^"]+)"\s*=\s*"_generated/tools/')


def write_tools_nav(config: Path, keys: list[str]) -> None:
    """Rewrite a zensical/mkdocs Tools nav's tool entries from *keys*."""
    entries = [f'    {{ "{k}" = "_generated/tools/{k}.md" }},' for k in keys]
    block = "\n".join([_NAV_BEGIN, *entries, _NAV_END])
    config.write_text(
        _NAV_RE.sub(lambda _m: block, config.read_text(encoding="utf-8")),
        encoding="utf-8",
    )


def nav_keys(config: Path) -> list[str]:
    """The tool keys the config's generated Tools-nav block lists, in order."""
    match = _NAV_RE.search(config.read_text(encoding="utf-8"))
    return [m["key"] for m in _NAV_ENTRY.finditer(match.group())] if match else []


def _row(driver: _drivers.Driver, path: Path) -> str:
    """One line of the index table: what it is, and what it was read from."""
    verbs = _verbs_of(path)
    listed = ", ".join(f"`{v}`" for v in verbs[:5]) or "the tool itself"
    if len(verbs) > 5:
        listed += f", … ({len(verbs)} in all)"
    version, mode = _header(path)
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
        f"::: livery.toolroom._stubs.{driver.key}.{_class_name(driver.key)}\n"
    )


__all__ = ["tasks"]
