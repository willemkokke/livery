"""Load the task cascade and merge it into one command tree.

In a monorepo you rarely want a single tasks file. footman collects every
`tasks.py` from the repo root down to your current directory and merges them
top-down: a new name **appends**, a name already present **overrides** (the
folder nearest your cwd wins), and a command group present at both levels
**merges** (its tasks overlaid the same way). Each task remembers the folder of
the file that defined it, so it runs from there regardless of where you stand.

The registry raises on a duplicate name, so the merge can't be done by importing
every file into one registry — each file is imported into a fresh registry and
the resulting trees are overlaid here.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from livery.footman import registry
from livery.footman.invocation import Invocation
from livery.footman.registry import Group, Task

# Attribute stamped on every task fn: the directory of the file that defined
# it. The scheduler uses it as the task's working directory.
DEFINING_DIR = "_footman_dir"
SHADOWED = "_footman_shadowed"


class TasksImportError(Exception):
    """A tasks file failed to import; names the file and keeps the cause."""

    def __init__(self, path: Path, original: BaseException) -> None:
        self.path = path
        self.original = original
        if isinstance(original, SystemExit):
            # `sys.exit()` at import time would otherwise propagate its raw
            # code with no words at all — a silent death wearing footman's
            # exit status.
            message = (
                f"{path}: calls sys.exit({original.code!r}) at import time — "
                f"a task file is imported to list its tasks, so the exit "
                f"belongs inside a task body"
            )
        else:
            message = f"{path}: {type(original).__name__}: {original}"
        super().__init__(message)


class HookError(Exception):
    """A lifecycle hook raised; the message names the hook, never a bare
    traceback. A deliberate stop (`fail("…")`) renders as its reason."""

    def __init__(self, kind: str, name: str, original: BaseException) -> None:
        self.kind = kind
        self.name = name
        self.original = original
        from livery.footman import context

        if context._is_deliberate_stop(original):
            # A hook that *chose* to stop is talking to the user, and its
            # reason is the whole message — naming the hook in front of it
            # leaks machinery into a sentence someone wrote for a person
            # ("@pre_tasks 'load': --env-file: … does not exist"). A hook
            # that *crashed* still gets named, because then the machinery
            # is exactly what a reader needs.
            super().__init__(str(original) or type(original).__name__)
            return
        super().__init__(f"@{kind} {name!r}: {type(original).__name__}: {original}")


def _import_file(path: Path, index: int) -> Group:
    """Import *path* into a fresh registry and return the populated tree."""
    registry.reset()
    spec = importlib.util.spec_from_file_location(f"footman_tasks_{index}", path)
    if spec is None or spec.loader is None:
        raise TasksImportError(path, ImportError("cannot load tasks file"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    parent = str(path.parent)
    # Search this file's own dir first for sibling helpers (move-to-front, not
    # insert-if-absent — a shared dir on sys.path must not shadow it), snapshot
    # sys.path/sys.modules, and evict the direct siblings it imports afterwards.
    # Otherwise two cascade files each doing `import helpers` share whoever
    # imported first (F14/D8). Restoring sys.path also stops it accumulating
    # across the many load_tree calls an in-process runner makes.
    saved_path = sys.path[:]
    before = set(sys.modules)
    if parent in sys.path:
        sys.path.remove(parent)
    sys.path.insert(0, parent)
    try:
        spec.loader.exec_module(module)
    except SystemExit as exc:
        raise TasksImportError(path, exc) from exc
    except Exception as exc:
        raise TasksImportError(path, exc) from exc
    finally:
        sys.path[:] = saved_path
        _evict_siblings(before, Path(parent))
    return registry.root


def _evict_siblings(before: set[str], parent: Path) -> None:
    """Drop modules a cascade file imported that live directly in its dir.

    A sibling `helpers.py` (`parent/helpers.py`) or a package one level down
    (`parent/pkg/__init__.py`) — so the next file gets its own copy rather than
    whoever-imported-first-wins. Deeper imports and editable-installed packages
    live elsewhere on disk and are deliberately left (D8).
    """
    evicted: set[str] = set()
    for name in set(sys.modules) - before:
        file = getattr(sys.modules.get(name), "__file__", None)
        if file is None:
            continue
        f = Path(file)
        sibling = f.parent == parent
        package = f.name == "__init__.py" and f.parent.parent == parent
        if sibling or package:
            evicted.add(name)
    # A package leaves with its whole subtree: dropping `pkg` alone left
    # `pkg.sub` in sys.modules, and the next cascade file's `import pkg.sub`
    # re-imported `pkg` fresh from its own directory and then took the
    # *stale* submodule straight out of sys.modules — the previous file's
    # copy, wearing the new package. Name-prefixed, because a submodule's
    # file lives a level too deep for the directory checks above to see.
    for name in list(sys.modules):
        if name in evicted or any(name.startswith(p + ".") for p in evicted):
            del sys.modules[name]


class AliasError(Exception):
    """One function reached from two addresses that disagree about its folder."""


def _alias_error(
    path: Path, address: str, directory: str, seen_address: str, seen_directory: str
) -> TasksImportError:
    """The refusal for one task mounted twice with two different folders.

    It names the file being loaded *now* — the last one in cascade order, and
    the nearer one to the user. That is deliberately not symmetric: the two
    mounts are equally "the other one", but the farther mount is the shared
    one, serving every sibling directory, so dropping it is the change with
    reach. The local addition is the one to take back.
    """
    task = address.rpartition(".")[2]
    # A RegistrationError, not a bare exception: `_app` renders that shape as
    # "a user mistake, not a crash" and prefixes it with this file, so the
    # message does not repeat the path it is already shown under.
    #
    # The hint answers the goal somebody probably had, not the collision --
    # `asinvoked` never resolves it, and joining the two with "or" would read
    # as though it did.
    return TasksImportError(
        path,
        registry.RegistrationError(
            f"{task!r} is already mounted as {seen_address!r} by "
            f"{seen_directory} — one task cannot have two defining "
            f"directories. Drop this mount.\n  If you mounted it here to run "
            f'it in this directory, set cwd="asinvoked" on {seen_address!r} '
            f"instead."
        ),
    )


def _claim(
    stamps: dict[int, dict[str, str]], fn: Task, address: str, directory: str
) -> None:
    """Record that *address* stamps *fn* with *directory*, or refuse.

    A task runs in the directory of the tasks file that defined it, and that
    is stored on the function — so one function can only ever answer with one
    folder. A cascade may stamp the same function more than once and be
    perfectly well formed: a nearer file *shadowing* a farther one reuses the
    same address, and two providers that both include a common helper land it
    at two addresses with the *same* folder. Neither is a conflict, so the
    claim is keyed by address and compared by folder.

    What cannot work is one function at two addresses whose folders differ:
    whichever stamp lands last wins, and the other address then silently runs
    somewhere its own tasks file never named. The refusal is not conditional
    on the task's `cwd` policy, deliberately — `cwd` is a default, and
    `.opts(cwd=…)` re-resolves it per use, so a task that looks immune today
    is one call site away from caring.
    """
    claims = stamps.setdefault(id(fn), {})
    for seen_address, seen_directory in claims.items():
        if seen_address != address and seen_directory != directory:
            raise AliasError(address, directory, seen_address, seen_directory)
    claims[address] = directory


def _tag(
    group: Group, directory: str, stamps: dict[int, dict[str, str]], prefix: str = ""
) -> None:
    """Stamp every task in *group* (recursively) with its defining directory."""
    for name, fn in group.tasks.items():
        _claim(stamps, fn, f"{prefix}{name}", directory)
        setattr(fn, DEFINING_DIR, directory)
    for name, sub in group.groups.items():
        _tag(sub, directory, stamps, f"{prefix}{name}.")


def _overlay(
    base: Group,
    overlay: Group,
    directory: str,
    stamps: dict[int, dict[str, str]],
    prefix: str = "",
) -> None:
    """Merge *overlay* onto *base* in place: local (overlay) wins by name."""
    for name, fn in overlay.tasks.items():
        _claim(stamps, fn, f"{prefix}{name}", directory)
        setattr(fn, DEFINING_DIR, directory)
        # Keep the task this one shadows reachable: `inherited()` calls it,
        # `--where` lists it, `--help` shows its options. Without this the
        # parent's function is simply dropped and a leaf can only
        # *replace* the root's task, never extend it.
        if (previous := base.tasks.get(name)) is not None and previous is not fn:
            setattr(fn, SHADOWED, previous)
        base.groups.pop(name, None)  # a local task shadows an inherited group
        base.tasks[name] = fn
    for name, sub in overlay.groups.items():
        if name in base.groups:
            base.groups[name].help = sub.help or base.groups[name].help
            _overlay(base.groups[name], sub, directory, stamps, f"{prefix}{name}.")
        else:
            base.tasks.pop(name, None)  # a local group shadows an inherited task
            _tag(sub, directory, stamps, f"{prefix}{name}.")
            base.groups[name] = sub


def load_tree(
    files: list[Path], base: Group | None = None, inv: Invocation | None = None
) -> Group:
    """Import each file (root first) and overlay them into one merged tree.

    *base* seeds the tree (config-mounted plugin groups go there), so
    anything a tasks file defines overlays it — user names win over plugins
    exactly as nearer cascade files win over farther ones.
    """
    merged = base if base is not None else Group("root")
    # Address -> folder, per function, for THIS load only. Not an attribute:
    # the same function legitimately gets a fresh stamp on every load (a new
    # process, the refresh child, a second `Runner` invocation), and only a
    # disagreement *within one cascade* is the contradiction.
    stamps: dict[int, dict[str, str]] = {}
    try:
        for index, path in enumerate(files):
            tree = _import_file(path, index)
            try:
                _overlay(merged, tree, str(path.parent), stamps)
            except AliasError as exc:
                raise _alias_error(path, *exc.args) from None
            # Collect each file's lifecycle contributions in cascade order,
            # before the next _import_file resets the registry. They live on
            # the merged tree from here on, so the run can fire the per-task
            # moments later without re-walking the cascade.
            for kind, bucket in tree.contributions.items():
                merged.contributions[kind].extend(bucket)
    finally:
        # Leave no global state behind — even when a file registered some tasks
        # and then raised, which would otherwise strand ghost tasks in
        # registry.root for the rest of the process (F62).
        registry.reset()
    # The first lifecycle moment, run here so *every* path gets it: a real
    # invocation, the completion refresh child, and `_suggest`. Cascade order
    # (root first, the folder nearest cwd last), each hook seeing the previous
    # edits — so a subfolder refines what root did. Discovery-time, before
    # availability gates and the manifest, so an edit reaches both.
    if inv is None:
        # No invocation supplied means no command line to read — the refresh
        # child's situation, and the reason a tree edit may not depend on one.
        inv = Invocation(cwd=str(Path.cwd()))
    inv.tasks = registry.Tasks(merged)
    from livery.footman._executor import wide_moment

    with wide_moment("pre_tasks"):
        for run in merged.contributions["pre_tasks"]:
            try:
                run(inv)
            except Exception as exc:  # a bad hook names itself, never a traceback
                raise HookError(
                    "pre_tasks", getattr(run, "__name__", repr(run)), exc
                ) from exc
    # The single-threaded moment is over: every later reader — a per-task hook
    # on a pool thread, a body call — sees the same invocation, unwritable.
    inv.freeze()
    return merged


def untag(group: Group) -> None:
    """Drop any defining-directory stamp from *group*'s tasks, recursively.

    A built-in base task was not defined by a tasks file, so it has no folder
    to name and must answer `None`, letting the cwd ladder fall through to
    where `fm` was invoked — which is the whole point of `fm new`.

    It has to be cleared rather than simply left, because the stamp lives on
    the *function*, and a built-in is the same object every time it is
    mounted. An earlier in-process invocation that mounted the same provider
    from inside a project stamps it with that project; the base is built only
    when discovery found no task files, so `load_tree(files=[], base=base)`
    never overlays and nothing overwrites it. `fm new` in an empty directory
    then refuses, believing it is somewhere it is not.
    """
    for fn in group.tasks.values():
        if hasattr(fn, DEFINING_DIR):
            delattr(fn, DEFINING_DIR)
    for sub in group.groups.values():
        untag(sub)


def defining_dir(fn: Task) -> str | None:
    """The folder the task was defined in, if the cascade tagged it."""
    return getattr(fn, DEFINING_DIR, None)


def shadowed(fn: Task) -> Task | None:
    """The task *fn* overrides — the same name, one cascade level up."""
    return getattr(fn, SHADOWED, None)


def shadow_chain(fn: Task) -> list[Task]:
    """*fn* and every task it shadows, nearest first."""
    chain = [fn]
    while (previous := shadowed(chain[-1])) is not None:
        chain.append(previous)
    return chain
