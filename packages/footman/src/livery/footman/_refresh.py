"""Background completion-manifest refresh — the detached rebuild child.

The completion hot path spawns this detached, two ways:

- `_maybe_refresh` → `refresh_cwd` is the stale-while-revalidate path: when a
  cwd's cached manifest is older than its baked `completion.max_age`, rebuild the
  *cwd cascade's* manifest so dynamic completers (git branches, file lists) don't
  go stale for "time since your last real `fm` command here".
- `_cold_build` → `refresh_source` builds a single `-f <file>`'s (cwd, file)
  manifest the first time it is TAB-completed in a fresh directory, so `fm -f
  <file> <TAB>` answers accurately instead of empty.

Both rebuild exactly as a real run would and are strictly fire-and-forget: they
print nothing and never raise.

A tasks file that carries its own PEP 723 dependencies is rebuilt from inside
its script environment — but only when uv can reach one without the network,
since a keystroke must never download anything. Otherwise the rebuild happens
here, in place, exactly as it always did: often enough to answer, because a
tasks file's *module-level* imports are usually just the runner.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # runtime imports stay deferred: this child spawns cheap
    from pathlib import Path


def _maybe_reexec(files: list[Path], entry: str, *args: str) -> None:
    """Continue this rebuild inside a script file's own environment — the
    rule lives in `_script.maybe_reexec`, shared with the suggest child.
    The re-executed child runs the same one-liner *entry* under the same
    interpreter flags, so the two spawn shapes stay identical apart from the
    interpreter itself — `-P` included, or the re-exec would reopen the hole
    the spawn closed and let a planted `footman.py` win the import."""
    from livery.footman import _script

    _script.maybe_reexec(files, ["-P", "-c", entry, *args])


def _project_reexec(cwd: Path, uv_wanted: bool, entry: str, *args: str) -> Path | None:
    """The lock rule's half of the children's world rule, asked first —
    exactly as the run path asks it (`_app._uv_handoff`): a pinned project
    owns this directory, so the manifest must be built by *its* interpreter
    from *its* packages, whichever runner answered the TAB. Heals a stale
    venv offline on the way, unless the project opted out of uv (the run's
    own retry syncs for real). Returns the project root when one owns the
    directory — the script rule then stays out of the way, as it does on
    the run path — else None.
    """
    from livery.footman import _script

    root = _script.project_home(cwd)
    if root is None:
        return None
    _script.project_reexec(root, ["-P", "-c", entry, *args], heal=uv_wanted)
    return root


def refresh_cwd(*where: str) -> None:
    """Rebuild the current directory's completion manifest, swallowing errors.

    The location words come from the parent (`_paths.child_args`): this child
    inherits the environment but not the brand, so it is *told* where the
    cache is rather than re-deriving it. Taken as `*where` rather than named
    parameters so the two sides cannot disagree about arity — this call is
    inside `suppress`, where a `TypeError` would show up as a manifest that
    silently never appears.
    """
    # A detached background refresh must never crash or print.
    with contextlib.suppress(Exception):
        from livery.footman import _paths

        _paths.configure_child(*where)
        _rebuild()


def _rebuild() -> None:
    from pathlib import Path

    from livery.footman import _config, _discover, _manifest, _paths, registry

    cwd = Path.cwd()
    # The walk's reach is the run's: the user-level `cascade` key (and its
    # FOOTMAN_CASCADE override) bounds this child exactly as it bounds the
    # runner — otherwise TAB offers tasks the runner then refuses.
    mode = _config.cascade_mode()
    if mode == "none":
        ceiling = cwd
    elif mode == "filesystem":
        ceiling = Path(cwd.anchor)
    else:
        ceiling = _paths.find_repo_root(cwd)
    cfg = _config.load_config(cwd, ceiling)
    filename = cfg.get("tasks")
    if not isinstance(filename, str):
        # The brand's default filename rode in on the spawn (`child_args`
        # hands it over; `configure_child` installed it), so the child asks
        # `_paths` — the peek at the cached manifest's baked `tasks_file`
        # predated that handoff and could serve a stale answer.
        filename = _paths.tasks_file_name()
    name = filename
    project_files = _paths.task_files(cwd, ceiling, name)
    if not project_files:
        # The cascade went empty — the last tasks file here was deleted or
        # renamed. Nothing else rewrites the cwd-keyed manifest (global mode
        # writes the *global* one), so without this the cache outlives its
        # project: TAB keeps offering tasks the runner already refuses by
        # name, and every aged TAB bumps the mtime, so the collector's idle
        # sweep never reaches it either. Removing it puts the directory back
        # where a fresh one starts — the hot path falls through to global
        # mode.
        with contextlib.suppress(OSError):
            _paths.manifest_path(cwd).unlink()
    files = project_files
    user = _paths.user_tasks_file(name)
    if user.is_file():
        # The cascade's outermost rung rides here too: the child must
        # rebuild exactly the tree the run serves, or a background refresh
        # strips personal tasks from the completions it answers.
        files = [user, *files]
    # The built-in set the run would mount: the brand's, plus the user's
    # own `builtin` key — a background rebuild that skipped the second
    # would strip exactly the tasks that key exists to offer. A broken key
    # is the run's to report; the child quietly builds what it can.
    try:
        builtin = _config.effective_builtin(_paths.builtin())
    except Exception:
        builtin = _paths.builtin()
    if not files and not builtin:
        return
    # The re-executed child is a fresh interpreter, so it needs telling where
    # the cache is exactly as the first child did. Project rule before script
    # rule, mirroring the run path: a pinned project owns the directory.
    one_liner = (
        "import sys; from livery.footman import _refresh; "
        "_refresh.refresh_cwd(*sys.argv[1:])"
    )
    root = _project_reexec(
        cwd, cfg.get("uv") is not False, one_liner, *_paths.child_args()
    )
    if root is None:
        _maybe_reexec(files, one_liner, *_paths.child_args())

    # The built-in set is the cascade's outermost rung wherever this child
    # stands — project or not — because that is what the run mounts, and a
    # background rebuild that mounted less would answer TAB with a tree the
    # runner does not serve.
    base = registry.Group("root")
    if builtin:
        from livery.footman import compose

        with registry.capture() as base:
            for entry in builtin:
                compose.plugin(entry)
        # Same sealing the app layer does, for the same reason — this child
        # rebuilds the very manifest that answers TAB.
        registry.seal_expose(base)

    if not project_files:
        # Global mode: the shared global manifest, keyed by the brand rather
        # than the cwd, so one build warms every project-less directory.
        try:
            reg = _discover.load_tree(files, base=base)
        except Exception as exc:
            _write_marker(_paths.global_manifest_path(), exc, project=root)
            return
        _manifest.sync_manifest(
            reg,
            cwd,
            completion_max_age=_config.completion_max_age(cfg),
            tasks_file=name,
            path=_paths.global_manifest_path(),
            bake_cwd=False,
            builtin=builtin,
            project=False,
        )
        return

    # Mirror the app layer's cwd cascade build; plugin mounts are authored in
    # the tasks files themselves, so discovery alone rebuilds the whole tree.
    try:
        reg = _discover.load_tree(files, base=base)
    except Exception as exc:
        _write_marker(_paths.manifest_path(cwd), exc, project=root)
        return
    _manifest.sync_manifest(
        reg, cwd, completion_max_age=_config.completion_max_age(cfg), tasks_file=name
    )


# Seconds a broken-tree marker stays authoritative. Short on purpose: within
# the window every TAB answers instantly (no spawn storm against a file that
# is still broken), and past it stale-while-revalidate rebuilds — so a fixed
# file recovers on the press after next, without the marker ever needing to
# watch the file.
_MARKER_MAX_AGE = 5


def _write_marker(path: Path, exc: BaseException, project: Path | None = None) -> None:
    """A broken tree is an answer too.

    Without this, a tasks file that fails to import leaves nothing behind:
    the hot path stays cold, every TAB pays the full cold bound, and the
    silence never says why. The marker rides the manifest slot — same
    schema, a `broken` line instead of a `tree` — so the hot path can say
    what the import said (exit 103) and answer instantly while it does.

    *project* is the pinned project that owned the build, when one did: a
    failed *import* there is the stale-environment shape, and the marker
    names the fix instead of leaving a bare ModuleNotFoundError.
    """
    from livery.footman import _manifest, _script

    # The message alone when it tells the story (discovery errors carry
    # file and cause already); the type name only when there is nothing
    # else to show.
    told = str(exc).strip()
    line = (told.splitlines()[0] if told else type(exc).__name__)[:200]
    if project is not None and _script.import_caused(exc):
        line += " — the environment may be out of date: run uv sync"
    _manifest.write_manifest(
        {
            "schema": _manifest.SCHEMA_VERSION,
            "broken": f"tasks failed to import — {line}",
            "completion_max_age": _MARKER_MAX_AGE,
        },
        path,
    )


def refresh_source(tasks_file: str, *where: str) -> None:
    """Rebuild one `-f <file>`'s (cwd, file) manifest, swallowing errors.

    `*where` for the same reason as `refresh_cwd`: an arity disagreement
    inside `suppress` is a manifest that silently never appears.
    """
    # A detached background rebuild must never crash or print.
    with contextlib.suppress(Exception):
        from livery.footman import _paths

        _paths.configure_child(*where)
        _rebuild_source(tasks_file)


def _rebuild_source(tasks_file: str) -> None:
    from pathlib import Path

    from livery.footman import _config, _discover, _manifest, _paths, registry

    one = Path(tasks_file).expanduser()
    if not one.is_file():
        return  # a typed-but-missing -f value: nothing to build
    cwd = Path.cwd()
    one_liner = (
        "import sys; from livery.footman import _refresh; "
        "_refresh.refresh_source(*sys.argv[1:])"
    )
    # Project rule before script rule, exactly as the run path orders them
    # for a `-f` invocation: the lock rule probes the cwd, not the file.
    # The config read exists only for its `uv` key — the opt-out must bind
    # the child as it binds the run — and a broken config must not take
    # down a rebuild that never needed one.
    try:
        cfg: dict[str, object] = _config.load_config(cwd, _paths.find_repo_root(cwd))
    except Exception:
        cfg = {}
    root = _project_reexec(
        cwd,
        cfg.get("uv") is not False,
        one_liner,
        tasks_file,  # the entry reads it back off argv
        *_paths.child_args(),  # …and the locations behind it
    )
    if root is None:
        _maybe_reexec([one], one_liner, tasks_file, *_paths.child_args())

    # Mirror a real `-f` run (see _app._run): one file, no cascade, cached
    # under the (cwd, file) key with max_age=0 — no background refresh,
    # rebuilt on the next -f run or the next cold TAB.
    base = registry.Group("root")
    try:
        reg = _discover.load_tree([one], base=base)
    except Exception as exc:
        _write_marker(_paths.source_manifest_path(cwd, one), exc, project=root)
        return
    _manifest.sync_manifest(
        reg,
        cwd,
        completion_max_age=0,
        tasks_file=tasks_file,
        path=_paths.source_manifest_path(cwd, one),
    )
