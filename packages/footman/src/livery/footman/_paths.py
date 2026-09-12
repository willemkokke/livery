"""Filesystem locations shared by the execution path and the completion hot
path.

Stdlib-only and deliberately import-light: the completion hot path imports this
module on every TAB press, so it must never reach for the framework or the
user's code.
"""

from __future__ import annotations

import hashlib
import os

# No module-level pathlib: importing it costs ~5 ms (it drags glob and re
# along), which is real money against the TAB press's ~30 ms budget. The
# warm completion path runs on the `_*_str` string core below (os.path
# only); every Path-returning function imports pathlib at call time, which
# the cold paths — a build already costing 100 ms+ — never notice.
# `test_a_warm_tab_pays_for_no_heavyweight_stdlib` pins the diet.
TYPE_CHECKING = False
if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

# Ancestor markers that identify the project root. The manifest cache is keyed
# by cwd, but these still bound a lone-file lookup when there is no repo root.
# Only the brand-independent one lives here; `project_markers()` adds this
# brand's config and tasks filenames. No VCS entry belongs here, see below.
PROJECT_MARKERS = ("pyproject.toml",)

# Marks the ceiling of the upward walk — the repo root where the task cascade
# starts and the config search stops: the version-control boundary, whichever
# system drew it. footman never runs these tools or reads their metadata; it
# notices a directory entry, which is why supporting four costs nothing.
REPO_MARKERS = (".git", ".jj", ".hg", ".svn")

# Default name of the tasks file, looked for in every folder of the cascade.
DEFAULT_TASKS_FILE = "tasks.py"


def project_markers() -> tuple[str, ...]:
    """The files that mark a project root — exactly the files footman reads.

    Two of the three are this brand's: `acme.toml` marks an `acme` project
    root as `footman.toml` marks footman's, and `acmetasks.py` marks one as
    `tasks.py` does.

    The tasks filename here is the **brand's**, never the `tasks` config key
    that can override it per project. That is not an oversight: the key
    lives in a config file, and finding config needs the ceiling this
    function is in the middle of computing. A brand default is the only
    answer available this early, and any project reachable through the
    config key has a config file marking its root anyway.
    """
    return (*PROJECT_MARKERS, config_basename(), _tasks_file)


def _entries(directory: Path) -> set[str]:
    """The directory's entry names, exactly as spelled on disk."""
    try:
        return set(os.listdir(directory))
    except OSError:
        return set()


def _named(
    directory: Path, name: str, seen: list[tuple[Path, str]] | None = None
) -> bool:
    """Whether *directory* holds an entry spelled exactly *name*.

    Case-exact, because a case-insensitive filesystem answers
    `(d / "tasks.py").exists()` True for a file named `Tasks.py`, and a
    project silently accepted that way stops working the day it reaches a
    Linux box.

    Probe first, and list only to confirm a hit. The listing used to come
    first — one `listdir` beats one `stat` per marker, which is true while
    a directory is small and inverts hard when it is not: the cost is
    O(entries) against O(markers), so an ancestor holding a few thousand
    files cost ~0.3 s *per level* of the walk (a macOS `$TMPDIR` reached
    8,747 entries here and made a bare-directory TAB take 1.5 s). A miss
    is now one `stat` whatever the directory holds, and a hit — rare, and
    the only place the spelling matters — pays the listing to prove
    itself.

    A probe that hits and then fails to confirm *is* the wrong-case file,
    and this is the one moment anything knows it: the listing is already
    in hand, so *seen* collects the sighting for free, at whatever depth
    it happens. Nothing else has to go looking, which is what keeps the
    complaint off any path someone is waiting on.
    """
    if not os.path.exists(os.path.join(directory, name)):
        return False  # cheap and conclusive: no entry answers to that name
    entries = _entries(directory)
    if name in entries:
        return True
    if seen is not None:
        lowered = name.lower()
        # Sorted so the complaint is reproducible rather than however the
        # filesystem happened to order its entries.
        actual = next((e for e in sorted(entries) if e.lower() == lowered), None)
        if actual is not None:
            seen.append((directory, actual))
    return False


def _any_named(directory: Path, names: Sequence[str]) -> bool:
    """Whether *directory* holds any of *names*, case-exactly.

    One listing at most, however many names are asked about: the probes
    are cheap, and the first that hits pays for the confirmation the rest
    can share.
    """
    if not any(os.path.exists(os.path.join(directory, n)) for n in names):
        return False
    entries = _entries(directory)
    return any(name in entries for name in names)


def find_project_root(start: Path | None = None) -> Path:
    """Nearest ancestor of *start* (default: cwd) containing a project marker."""
    from pathlib import Path

    start = (start or Path.cwd()).resolve()
    markers = project_markers()
    for directory in (start, *start.parents):
        if _any_named(directory, markers):
            return directory
    return start


def find_repo_root(start: Path | None = None) -> Path:
    """Ceiling of the cascade: nearest ancestor with a `REPO_MARKERS` entry.

    Git, Jujutsu, Mercurial and Subversion all draw the same boundary, and
    footman only ever asks whether the directory is there — it runs none of
    these tools and reads none of their metadata.

    Falls back to `find_project_root` when there is no VCS boundary, so a
    single-package checkout still has a sensible top. That fallback is also
    why no VCS marker belongs in `PROJECT_MARKERS`: by the time it runs,
    every ancestor has already been searched for every one of them.
    """
    from pathlib import Path

    start = (start or Path.cwd()).resolve()
    for directory in (start, *start.parents):
        if _any_named(directory, REPO_MARKERS):
            return directory
    return find_project_root(start)


def dir_chain(cwd: Path, ceiling: Path) -> list[Path]:
    """Directories from *ceiling* down to *cwd* inclusive (root first).

    If *ceiling* is not an ancestor of *cwd* (unrelated trees), just `[cwd]`.
    """
    cwd = cwd.resolve()
    ceiling = ceiling.resolve()
    chain: list[Path] = []
    for directory in (cwd, *cwd.parents):
        chain.append(directory)
        if directory == ceiling:
            return list(reversed(chain))
    return [cwd]


def task_files(
    cwd: Path,
    ceiling: Path,
    filename: str = DEFAULT_TASKS_FILE,
    seen: list[tuple[Path, str]] | None = None,
) -> list[Path]:
    """Existing task files from *ceiling* down to *cwd* (root first, cwd last).

    One spelling: a `Tasks.py` is not a tasks file, even where the
    filesystem would happily open it under the documented name — it would
    work on the machine that wrote it and vanish on the first Linux box.
    Passing over it is right; passing over it *quietly* was the second
    mistake, so *seen* collects every wrong-case sighting the walk makes
    for the caller to complain about."""
    return [
        f
        for d in dir_chain(cwd, ceiling)
        if _named(d, filename, seen) and (f := d / filename).is_file()
    ]


# The brand's locations, set once by `App.run` before anything reads them —
# the same module-global shape `_app._brand` uses for the brand's *names*.
# Plain strings and a path, never a `Brand`: this module is imported on every
# TAB press and must not reach for the framework.
_prefix = "FOOTMAN"
_cache_dir: Path | None = None
_data_dir: Path | None = None
_config_name = "footman"
_tasks_file = DEFAULT_TASKS_FILE
_prog = "fm"
_brand_version = ""
_builtin: tuple[str, ...] = ()
_dist: str | None = "livery-footman"


class LocationError(Exception):
    """Two locations that must differ were pointed at the same directory."""


def configure(
    *,
    prefix: str = "FOOTMAN",
    cache_dir: Path | None = None,
    data_dir: Path | None = None,
    config_name: str = "footman",
    tasks_file: str = DEFAULT_TASKS_FILE,
    prog: str = "fm",
    brand_version: str = "",
    builtin: tuple[str, ...] = (),
    dist: str | None = "livery-footman",
) -> None:
    """Point this CLI's locations at one brand's world.

    *cache_dir* and *data_dir* are placed by the brand and are unrelated to
    each other; `None` keeps the XDG fallback for that one. *prefix*
    namespaces the environment variables, so a stray `FOOTMAN_CACHE_DIR`
    cannot move another product's cache. *config_name* and *tasks_file* name
    the files this brand's users write, which are also what mark a project
    root. *prog*, *brand_version* and *builtin* key the global-mode manifest
    — the tree they determine is the same in every project-less directory.
    Detached children are handed the resolved values rather than re-deriving
    them. *dist* is the distribution the lock rule reasons about — the
    package a project's `uv.lock` must pin for the invocation to belong to
    that project's environment.
    """
    global _prefix, _cache_dir, _data_dir, _config_name, _tasks_file
    global _prog, _brand_version, _builtin, _dist
    _prefix, _cache_dir, _data_dir = prefix, cache_dir, data_dir
    _config_name, _tasks_file = config_name, tasks_file
    _prog, _brand_version, _builtin = prog, brand_version, builtin
    _dist = dist


def builtin() -> tuple[str, ...]:
    """The brand's declared built-in entry points (empty for stock)."""
    return _builtin


def dist() -> str:
    """The distribution the lock rule pins on — never empty.

    The run path spells the same fallback (`_brand.dist or "livery-footman"`), so
    a completion child and the run it stands in for can never disagree
    about which package makes a project's lockfile authoritative.
    """
    return _dist or "footman"


def declared_dist() -> str | None:
    """The distribution the brand actually declared, or `None`.

    The honest twin of `dist()`. That one falls back to `"footman"`
    because the lock rule needs *some* package to look for; a caller
    asking "what ships this CLI?" must not be told `footman` by a brand
    that never said — `App(dist=…)` is opt-in, and `None` is the true
    answer when it was not taken.
    """
    return _dist


def child_args() -> list[str]:
    """The configured locations as argv words for a detached child.

    Children inherit the environment but not the brand, and re-deriving a
    location from the environment would be re-deriving it *wrongly* — the
    brand placed it. So the parent hands over resolved values instead, and
    the child never reads a variable of its own.
    """
    return [
        _prefix,
        str(_cache_dir) if _cache_dir is not None else "",
        str(_data_dir) if _data_dir is not None else "",
        _config_name,
        _tasks_file,
        _prog,
        _brand_version,
        ",".join(_builtin),
        _dist or "footman",
    ]


def configure_child(
    prefix: str = "",
    cache_dir: str = "",
    data_dir: str = "",
    config_name: str = "",
    tasks_file: str = "",
    prog: str = "",
    brand_version: str = "",
    builtin_csv: str = "",
    dist: str = "",
) -> None:
    """The other side of `child_args` — empty strings mean stock defaults."""
    from pathlib import Path

    configure(
        prefix=prefix or "FOOTMAN",
        cache_dir=Path(cache_dir) if cache_dir else None,
        data_dir=Path(data_dir) if data_dir else None,
        config_name=config_name or "footman",
        tasks_file=tasks_file or DEFAULT_TASKS_FILE,
        prog=prog or "fm",
        brand_version=brand_version,
        builtin=tuple(n for n in builtin_csv.split(",") if n),
        dist=dist or "footman",
    )


def prog() -> str:
    """The command this CLI is invoked as — `fm`, or the brand's own.

    Beside `config_table()` and `env_prefix()`: the three words generated
    help text has to speak in the reader's brand rather than footman's.
    """
    return _prog


def env_prefix() -> str:
    """The configured environment-variable prefix."""
    return _prefix


def tasks_file_name() -> str:
    """The brand's tasks filename, as `configure`/`configure_child` set it.

    The refresh child reads it here: `child_args` hands the resolved value
    over on spawn, so a branded default IS knowable in the child — the old
    peek at the cached manifest's baked `tasks_file` predated that handoff.
    """
    return _tasks_file


def env_var(suffix: str) -> str:
    """The configured spelling of a variable: `ACME_CACHE_DIR`."""
    return f"{_prefix}_{suffix}"


def data_home() -> Path:
    """Base data directory, honouring `XDG_DATA_HOME`.

    `~/.local/share`, where footman's own completion scripts already live —
    the durable sibling of `cache_home`, for things that must survive a
    cache sweep.
    """
    from pathlib import Path

    xdg = os.environ.get("XDG_DATA_HOME")
    return Path(xdg) if xdg else Path.home() / ".local" / "share"


def config_basename() -> str:
    """The standalone config filename for this brand — `acme.toml`."""
    return f"{_config_name}.toml"


def config_table() -> str:
    """The `pyproject.toml` table for this brand — the `acme` in `[tool.acme]`."""
    return _config_name


def _cache_home_str() -> str:
    """`cache_home`, as a string — the warm path's spelling."""
    xdg = os.environ.get("XDG_CACHE_HOME")
    return xdg if xdg else os.path.join(os.path.expanduser("~"), ".cache")


def cache_home() -> Path:
    """Base cache directory, honouring `XDG_CACHE_HOME`."""
    from pathlib import Path

    return Path(_cache_home_str())


def config_home() -> Path:
    """Base config directory, honouring `XDG_CONFIG_HOME`.

    `~/.config` on every platform — the convention CLI tools (uv, ruff,
    git's own XDG support) follow on macOS and Windows too, and the
    symmetric sibling of `cache_home`.
    """
    from pathlib import Path

    xdg = os.environ.get("XDG_CONFIG_HOME")
    return Path(xdg) if xdg else Path.home() / ".config"


def footman_config_dir() -> Path:
    """This CLI's config directory: `<PREFIX>_CONFIG_DIR`, else
    `<config home>/<name>`.

    The user's own writing lives here — the user-level config file and the
    user tasks file, which travel together. It is never placed by the brand:
    `~/.config/<name>/` is where a user looks for their own files, whatever
    the brand does with its cache and data.

    The environment override exists because the only XDG-level lever is
    `XDG_CONFIG_HOME`, which moves every other application's config too. A
    task runner should be able to relocate its own corner — a second
    installation under a different identity — without dragging the rest of
    the machine along.
    """
    from pathlib import Path

    override = os.environ.get(env_var("CONFIG_DIR"))
    if override:
        return Path(override).expanduser()
    return config_home() / _config_name


def footman_config_file() -> Path:
    """The user-level config file: this brand's `<PREFIX>_CONFIG` (a file
    path, since this is one file — unlike `<PREFIX>_CONFIG_DIR`'s directory),
    else `<config dir>/config.toml`.

    The bottom rung of the precedence ladder: project config cascades over
    it, `--config` replaces it."""
    from pathlib import Path

    override = os.environ.get(env_var("CONFIG"))
    if override:
        return Path(override).expanduser()
    return footman_config_dir() / "config.toml"


def footman_cache_dir() -> Path:
    """This CLI's cache directory: `<PREFIX>_CACHE_DIR`, else where the brand
    placed it, else `<cache home>/<name>`.

    Derived data, safe to delete — the collector sweeps this and nothing else.
    One override moves every cache (completion manifests, timing history), and
    the completion hot path resolves through here too, so TAB follows it with
    no re-install.

    Resolution only: this is on the hot path, so it never creates anything.
    The public `footman.cache_dir()` is what makes the directory.
    """
    from pathlib import Path

    return Path(_footman_cache_dir_str())


def _footman_cache_dir_str() -> str:
    """`footman_cache_dir`, as a string — the warm path's spelling."""
    override = os.environ.get(env_var("CACHE_DIR"))
    if override:
        return override
    if _cache_dir is not None:
        return os.fspath(_cache_dir)
    # `cache_home` is a seam — the suite re-points it at tmp homes in
    # dozens of places — so a replacement must be honoured here too, or a
    # "redirected" test quietly writes the real user cache. Only while the
    # module's own def stands does the import-free spelling apply.
    if cache_home.__module__ == __name__:
        return os.path.join(_cache_home_str(), _config_name)
    return os.path.join(os.fspath(cache_home()), _config_name)


def footman_data_dir() -> Path:
    """This CLI's data directory: `<PREFIX>_DATA_DIR`, else where the brand
    placed it, else `<data home>/<name>`.

    Durable and machine-local — credentials, tokens, generated assets. Never
    collected, which is exactly what separates it from the cache.

    Resolution only; the public `footman.data_dir()` creates it.
    """
    from pathlib import Path

    override = os.environ.get(env_var("DATA_DIR"))
    if override:
        return Path(override)
    if _data_dir is not None:
        return _data_dir
    return data_home() / _config_name


def check_locations() -> None:
    """Refuse a cache directory that coincides with the data or config one.

    The collector deletes from the cache by age. Pointed at either of the
    other two it would delete durable things — credentials, the user's own
    files — so this is a refusal at startup rather than a surprise ninety
    days later. Data and config may share (nothing destructive runs in
    either); only the cache is dangerous company.
    """
    cache = footman_cache_dir()
    for other, what, var in (
        (footman_data_dir(), "data", "DATA_DIR"),
        (footman_config_dir(), "config", "CONFIG_DIR"),
    ):
        try:
            same = cache.resolve() == other.resolve()
        except OSError:  # unresolvable (a broken symlink); compare as written
            same = cache == other
        if same:
            raise LocationError(
                f"the cache and {what} directories are both {cache} — they "
                f"must differ, because the collector deletes from the cache "
                f"by age and would eventually delete durable {what}. Set "
                f"{env_var('CACHE_DIR')} or {env_var(var)}, or place them "
                f"apart in the App(...)."
            )


def user_stamp() -> str:
    """A fingerprint of the user-level files a manifest was built from — the
    user config and the user tasks file.

    Every manifest depends on both (the config's `cascade` bounds the walk,
    and the user tasks file rides every tree footman builds), yet neither can
    join a cache *key*: the key would then have to be recomputed from a TOML
    read on the hot path. So the manifest bakes this stamp instead, and the
    resolver compares it — two `stat` calls, no parse, no import, and paid
    after the candidates are already written, so the keystroke never waits
    on it. A changed stamp means the same thing an aged manifest means:
    serve what is cached, rebuild behind it.

    An **absent** file is a value, not a gap: writing a user tasks file that
    was not there before changes the tree exactly as editing one does, and
    the stamp has to say so. Size rides along with the timestamp so an edit
    inside one clock tick still registers.
    """
    marks: list[str] = []
    for path in (footman_config_file(), user_tasks_file(tasks_file_name())):
        try:
            info = os.stat(path)
            marks.append(f"{info.st_mtime_ns}-{info.st_size}")
        except OSError:
            marks.append("-")  # absent, and its arrival is a change
    return ":".join(marks)


def user_tasks_file(filename: str = DEFAULT_TASKS_FILE) -> Path:
    """This CLI's user-level tasks file — `<config dir>/<tasks_file>`.

    Beside the user-level config file, because both are the *user's* own
    writing rather than anything the brand places — and both move together
    under `<PREFIX>_CONFIG_DIR`. It is a *fallback*, not a rung: a project's
    cascade wins outright, because there is one way to get tasks into a
    project tree and that is mounting them in a tasks file.
    """
    return footman_config_dir() / filename


# The cache keys hash *resolved* path strings. `os.path.realpath` is what
# `Path.resolve()` calls underneath, so the string core and the Path
# wrappers key byte-identically — a manifest written by a full run is the
# manifest a TAB reads, whichever spelling computed the name.


def _dir_key_str(key_dir: str) -> str:
    return hashlib.sha256(os.path.realpath(key_dir).encode("utf-8")).hexdigest()[:16]


def env_key(prefix: str) -> str:
    """A stable short key for one Python environment.

    `sys.prefix` names it: a venv, a uv tool environment, the system
    interpreter. Hashed like a directory key, and for the same reason —
    the store holds one entry per subject and the subject's own name is
    not a filename.
    """
    return _dir_key_str(prefix)


def _manifest_file(key_dir: str) -> str:
    """`manifest_path`, as a string — the warm path's spelling."""
    return os.path.join(_footman_cache_dir_str(), f"{_dir_key_str(key_dir)}.json")


def manifest_path(key_dir: Path) -> Path:
    """Cached-manifest path for *key_dir* (the cwd), keyed by a path hash.

    The effective task set depends on where you stand in a monorepo — the
    cascade from the repo root down to the cwd — so the cache is per directory.
    """
    from pathlib import Path

    return Path(_manifest_file(os.fspath(key_dir)))


def times_path(key_dir: Path) -> Path:
    """Duration-history path for *key_dir* — beside its manifest, same key."""
    from pathlib import Path

    return Path(
        os.path.join(
            _footman_cache_dir_str(), f"{_dir_key_str(os.fspath(key_dir))}.times.json"
        )
    )


def _source_file(cwd: str, tasks_file: str) -> str:
    """`source_manifest_path`, as a string — the warm path's spelling.

    `expanduser` here, in the one key core, so every keyer agrees:
    resolving alone leaves `~` literal, and a `-f=~/tasks.py` TAB would key
    `<cwd>/~/tasks.py` while the refresh child (which expands before
    loading) keys the home-anchored truth — a manifest that never warms.
    """
    one = os.path.realpath(os.path.expanduser(tasks_file))
    joined = f"{os.path.realpath(cwd)}\0{one}"
    key = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]
    return os.path.join(_footman_cache_dir_str(), f"{key}.json")


def source_manifest_path(cwd: Path, tasks_file: Path) -> Path:
    """Cache path for a `-f <file>` run, keyed by *both* the cwd and the file.

    A `-f` invocation loads that file's tasks *and* the cwd's config plugins,
    so the task set depends on the pair — the same file opened from two projects
    is two caches. A separate key from `manifest_path`, so a `-f` run never
    poisons the plain-cwd completion cache.
    """
    from pathlib import Path

    return Path(_source_file(os.fspath(cwd), os.fspath(tasks_file)))


def _cwd_manifest_file() -> str:
    """`cwd_manifest_path`, as a string — the warm path's spelling."""
    return _manifest_file(os.getcwd())


def cwd_manifest_path() -> Path:
    """Manifest path for the current directory (both hot and cold paths agree)."""
    from pathlib import Path

    return Path(_cwd_manifest_file())


def _global_file() -> str:
    """`global_manifest_path`, as a string — the warm path's spelling."""
    key = hashlib.sha256(
        "\0".join([_prog, _brand_version, *sorted(_builtin)]).encode("utf-8")
    ).hexdigest()[:16]
    return os.path.join(_footman_cache_dir_str(), f"global-{key}.json")


def global_manifest_path() -> Path:
    """Manifest path for global mode — no project task files, the tree from
    the brand's built-ins with the user rung over them.

    Keyed by what determines that tree — prog, the brand's version, the
    sorted builtin names — never by cwd: every project-less directory shares
    one manifest, so the cache is cold once per brand version rather than
    once per directory. The user tasks file needs no place in the key; it is
    machine-global too, and staleness handles its edits.
    """
    from pathlib import Path

    return Path(_global_file())
