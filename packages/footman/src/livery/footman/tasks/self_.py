"""`fm self.*` — the runner managing its own installation.

Putting the CLI on your PATH, adding packages beside it, dropping them
again, taking the whole thing off, and saying where it keeps things. Four
verbs and a question, because once there is more than one of them a set of
global flags is the wrong container: these are commands, with their own
options, help, completion and `--json`.

Every one of them is `expose="always"`: they are about the *runner*, so
they mean the same thing inside a project and outside one. That is the
whole reason built-ins had to join the cascade — a self-management command
that vanished the moment you stepped into a checkout would be useless
exactly when you reach for it.

uv owns the installation itself; footman only ever composes the command
line and reads back what uv recorded. The receipt
(`<uv tool dir>/<dist>/uv-receipt.toml`) is that record, and reading it is
what makes `add`/`remove` additive instead of replacing your environment
every time.
"""

from __future__ import annotations

import json
import os
import shutil
import tomllib
from pathlib import Path
from typing import Annotated, Any, Literal

from livery.footman import _config, _paths, context
from livery.footman.context import fail, run
from livery.footman.params import doc, suggest
from livery.footman.registry import Group, group

tasks: Group = group("self", help="Manage this runner's own installation")


# --- what uv recorded ---------------------------------------------------------


def _uv() -> str:
    """The uv this runner hands off to — its own environment first, then
    PATH, exactly as the handoffs resolve it."""
    from livery.footman import _script

    found = _script.find_uv()
    if found is None:
        fail(
            "no uv found — this runner's environment has none and PATH has "
            "none; install uv first (https://docs.astral.sh/uv/)"
        )
    return found


def _dist() -> str:
    """The distribution that ships this runner — the same name the uv
    handoffs reason about, so a branded CLI installs itself, not footman."""
    return _paths.dist()


def _tool_dir() -> Path:
    """uv's tools directory, asked of uv rather than guessed."""
    done = run([_uv(), "tool", "dir"], capture=True, nofail=True, recorded=False)
    if done.code != 0 or not done.stdout.strip():
        fail(f"uv could not say where its tools live: {done.stderr.strip()}")
    return Path(done.stdout.strip())


def _receipt_requirements() -> tuple[str, ...]:
    """Everything the tool environment was installed with, per uv's receipt.

    uv rewrites the environment from the requirements it is *given*, so an
    upgrade that did not repeat your extras would silently drop them. This
    is how `install`/`add`/`remove` stay additive: read what is there, and
    hand the whole set back.

    Empty when nothing is installed yet — which is exactly the state
    `install` and `add` are for.
    """
    receipt = _tool_dir() / _dist() / "uv-receipt.toml"
    try:
        with open(receipt, "rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return ()
    entries = data.get("tool", {}).get("requirements", [])
    names = [e.get("name") for e in entries if isinstance(e, dict)]
    return tuple(n for n in names if isinstance(n, str))


def _extras() -> tuple[str, ...]:
    """The packages *you* added: the receipt minus what the runner needs.

    The distribution itself is the thing being installed, and `uv` is
    bundled so the handoffs work where PATH has none — neither is yours to
    remove, so neither is offered.
    """
    reserved = {_dist(), "uv"}
    return tuple(n for n in _receipt_requirements() if n not in reserved)


def _added() -> list[str]:
    """Completion candidates for `remove`: what is actually there to drop."""
    try:
        return list(_extras())
    except Exception:
        return []  # a completer that cannot answer offers nothing, never raises


# --- installing ---------------------------------------------------------------


def _install(extras: tuple[str, ...]) -> None:
    """One `uv tool install`, carrying the whole requirement set.

    `--upgrade` means the command never has to ask whether this is a first
    install or a move to the latest: both are the same sentence. uv's own
    output is the report; nothing here paraphrases it.
    """
    cmd = [_uv(), "tool", "install", "--upgrade", _dist(), "--with", "uv"]
    for name in sorted(set(extras)):
        cmd += ["--with", name]
    run(cmd)


def _rediscover() -> tuple[str, ...] | None:
    """Refresh the discovered built-in list, when the mode asks for it.

    `auto` is the only mode where footman writes that list; `manual` says
    it is yours, `internal` and `none` ignore it, and rewriting it under
    any of those would be footman editing something it was told not to.

    Candidates come from the **installed** environment, not this one: the
    tool environment is a different world from the process doing the
    installing, so its entry points are only knowable by asking its own
    interpreter.
    """
    if _config.discovery_mode() != "auto":
        return None
    names, prefix = _candidates_in(_tool_dir() / _dist())
    if prefix is None:
        return None  # nothing answered, so there is nothing to record
    _config.write_discovered(names, prefix)
    return names


def _candidates_in(env: Path) -> tuple[tuple[str, ...], str | None]:
    """Every `footman.builtin` entry point in *env*, and *env*'s own prefix.

    A provider declares itself a *candidate* by advertising in that group —
    "I am meant to be mounted as a built-in, not merely mountable". Whether
    a candidate is actually mounted stays the machine owner's call, through
    `builtins.discovery_mode`: a package cannot mount itself just by being
    installed, which matters because in a project environment every
    dependency shares one metadata space.

    The prefix comes back with the names because the record is keyed by the
    environment it *describes*, and that is never this process: the probe
    runs in the tool environment precisely because its entry points are
    only knowable by asking its own interpreter. Keying by the writer's
    `sys.prefix` instead files the answer under whoever happened to type
    the command — the very bug the key was added to fix, wearing a
    different hat. So the interpreter that answers also says who it is,
    rather than this side inferring a prefix from the directory it spawned.
    """
    python = env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        return (), None
    probe = (
        "import json,sys;from importlib.metadata import entry_points;"
        "print(json.dumps([sys.prefix, sorted({e.name for e in "
        "entry_points(group='footman.builtin')})]))"
    )
    done = run([str(python), "-c", probe], capture=True, nofail=True, recorded=False)
    if done.code != 0:
        return (), None
    try:
        prefix, found = json.loads(done.stdout)
    except (ValueError, TypeError):
        return (), None
    if not isinstance(prefix, str) or not isinstance(found, list):
        return (), None
    return tuple(n for n in found if isinstance(n, str)), prefix


@tasks.task(expose="always")
def install() -> None:
    """Put this CLI on your PATH with uv, or bring it up to date.

    Always the latest release from the index — running it from inside a
    project (`uv run fm self.install`) installs the *global* copy at
    latest, never the version it is running itself. Packages you added
    with `self.add` are carried over rather than dropped.
    """
    _install(_extras())
    _rediscover()


@tasks.task(expose="always")
def add(
    *packages: Annotated[str, doc("distributions to install beside the runner")],
) -> None:
    """Install packages alongside the runner, and mount what they offer.

    The one-stop call: it installs the runner if it is not there yet, adds
    the packages to its environment, and — under the default
    `builtins.discovery_mode = "auto"` — records whatever built-in tasks
    they advertise, so their commands answer straight away.
    """
    if not packages:
        fail("name at least one package to add")
    _install(_extras() + tuple(packages))
    if (found := _rediscover()) is not None and found:
        print(f"built-in tasks now available: {', '.join(found)}")


@tasks.task(expose="always")
def remove(
    *packages: Annotated[
        str,
        suggest(_added),
        doc("packages to drop from the runner's environment"),
    ],
) -> None:
    """Drop packages you added beside the runner.

    Completion offers exactly what is there to drop — the runner's own
    distribution and the bundled `uv` are not yours to remove, so they are
    never offered, and a name that was never added is refused rather than
    handed to uv.
    """
    if not packages:
        fail("name at least one package to remove")
    have = _extras()
    for name in packages:
        if name not in have:
            listed = ", ".join(have) or "nothing"
            fail(f"{name} was not added to this runner (added: {listed})")
    _install(tuple(n for n in have if n not in packages))
    _rediscover()


@tasks.task(expose="always")
def uninstall(
    purge: Annotated[bool, doc("also delete your config directory")] = False,
) -> None:
    """Take this CLI off your PATH, and clear what it left behind.

    uv removes the tool environment; this removes what the runner placed
    outside it — the completion hooks (the script *and* its line in your
    shell's rc, or the rc would go on sourcing a file that is gone), the
    cache, and the data directory.

    **Your config is kept** unless you ask for `--purge`: it is your
    writing, and deleting it silently would be the one unforgivable step of
    an uninstall. The data directory is shared, though — a plugin keeping
    state under `data_dir()` loses it here, which is the price of living in
    the runner's home instead of inventing one.
    """
    run([_uv(), "tool", "uninstall", _dist()], nofail=True)
    for line in _remove_hooks():
        print(line)
    gone = []
    for folder in (_paths.footman_cache_dir(), _paths.footman_data_dir()):
        if folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)
            gone.append(str(folder))
    if purge and (config := _paths.footman_config_dir()).is_dir():
        shutil.rmtree(config, ignore_errors=True)
        gone.append(str(config))
    for where in gone:
        print(f"removed {where}")
    if not purge:
        print(f"kept {_paths.footman_config_dir()} (--purge removes it too)")


def _remove_hooks() -> list[str]:
    """Uninstall every shell completion hook this runner installed.

    The hooks do **not** live under `data_dir()` — they sit *beside* it, at
    `<data home>/<prog>/completion.*`, keyed by the command name rather than
    the config stem (`~/.local/share/fm/` next to `~/.local/share/footman/`).
    Clearing the data directory therefore left them in place, and their rc
    lines with them, sourcing a script that no longer existed — which every
    new shell then complained about.

    Only shells that actually have a script are touched: the script is the
    evidence an rc line was written, so nothing edits the rc of a shell this
    runner never installed into.
    """
    from livery.footman import _shellcomp

    prog = _paths.prog()
    said: list[str] = []
    for shell in _shellcomp.SHELLS:
        if _shellcomp.hook_path(shell, prog).is_file():
            said += _shellcomp.uninstall(shell, prog)
    return said


# --- where things are ---------------------------------------------------------

_PLACES: dict[str, Any] = {
    "cache": lambda: _paths.footman_cache_dir(),
    "data": lambda: _paths.footman_data_dir(),
    "config-dir": lambda: _paths.footman_config_dir(),
    "config-file": lambda: _paths.footman_config_file(),
    "user-tasks": lambda: context.user_tasks_file(),
    "builtins": lambda: _config.discovered_path(),
    "project-root": lambda: context.project_root(),
    "tool-dir": lambda: _tool_dir() / _dist(),
}

Place = Literal[
    "cache",
    "data",
    "config-dir",
    "config-file",
    "user-tasks",
    "builtins",
    "project-root",
    "tool-dir",
]


@tasks.task(expose="always")
def path(
    *which: Annotated[Place, doc("locations to print; none means every one")],
) -> dict[str, str] | None:
    """Say where this CLI keeps things — for scripts, and for finding out.

    Named alone it prints one bare line, so `DIR=$(fm self.path data)` is
    the whole idiom. With no name it prints every location, and under
    `--json` it returns them as an object, so a script that wants three of
    them makes one call instead of three.

    A location that does not apply here — `project-root` outside a project
    — is empty rather than absent: the question was answered.
    """
    if which:
        for name in which:
            print(_PLACES[name]() or "")
        return None
    places = {place: str(where() or "") for place, where in _PLACES.items()}
    width = max(len(place) for place in places)
    for place, where in places.items():
        print(f"{place.ljust(width)}  {where}")
    return places
