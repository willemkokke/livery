"""Process-globals routers — the parallel regime made real for env (and kin).

In the parallel regime nobody mutates process globals: cwd and env are
context data (`ctx.cwd` / `ctx.env`), applied per child at spawn. The
routers here make that regime hold for code that has never heard of it,
the same way the output router does for `print()`:

- **os.environ is virtualised for the run.** Reads see the run-start
  snapshot plus the current task's overlay — exactly what the subprocess
  branch of the same call would inject as `env=`, closing the
  in-process/subprocess parity hole. Writes from a task body scope to the
  task's own overlay (`ctx.env`): visible to its own reads and every child
  it spawns, invisible to siblings — with a task-attributed teach-once
  note naming the deliberate spelling. Deletion has no additive spelling,
  so it is a taught error.

The class methods of `os._Environ` are wrapped (never the object
replaced), so every alias — `from os import environ`, `os.getenv` — is
covered, and `os.environb` (a different instance) passes through
untouched. Installed at the run boundary (`run_plan`), refcounted for
nested runs, delegating to the originals outside a run — a bare
`import footman` never pays for any of this.

Originals are also re-exported (`real_getcwd`, …) so footman's own
internals keep touching the real process state: the routers exist for
*task* code, not for the framework.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

# Originals, captured at import time. Internals use these; the guards and
# routers exist for task code, and footman warning about itself would be
# noise with no lesson in it.
real_getcwd = os.getcwd
real_chdir = os.chdir

_lock = threading.Lock()
_installs = 0  # refcount: nested runs share one install
_snapshot: dict[str, str] = {}  # the run-start environment, pinned
_environ_saved: dict[str, Any] = {}  # the wrapped class's original methods


def active() -> bool:
    """Whether the routers are installed (a run is in flight)."""
    return _installs > 0


# Never inherited: the caller's interpreter is never a spawned tool's
# interpreter. On Windows `uv run` exports PYTHONHOME pointing at the
# environment it launched — footman's own — and every child inherits it, so a
# console script belonging to any *other* Python loads that stdlib instead of
# its own and dies before it can say anything ("Could not import
# runpy._run_module_as_main"). Measured: 107 tools read as holes in one walk,
# every one whose launcher was a console script rather than a native binary.
# Confirmed on Windows under uv 0.11.1 and 0.11.31; on macOS and Linux the
# variable is simply absent, so dropping it costs those platforms nothing.
#
# Dropped from the *inherited* copy, not at spawn: a task that deliberately
# sets PYTHONHOME — or hands one to `run(env=…)` — still gets exactly what it
# asked for. Inheritance is the thing nobody chose.
#
# PYTHONPATH is deliberately NOT here. People export it on purpose
# (`PYTHONPATH=src`) meaning it for what their tasks run, it is harmless when
# merely present (a directory that isn't on it changes nothing), and footman
# cannot tell a deliberate one from an inherited one. The tool walk strips it
# separately, where reading other-era venvs makes it genuinely dangerous.
_NEVER_INHERITED = frozenset({"PYTHONHOME", "PYTHONEXECUTABLE"})


def _inheritable(env: dict[str, str]) -> dict[str, str]:
    """*env* minus the variables that describe our own interpreter."""
    return {k: v for k, v in env.items() if _norm(k) not in _NEVER_INHERITED}


def base_env() -> dict[str, str]:
    """The environment a fresh task starts from — a real dict, never a diff.

    Inside a run that is the pinned run-start snapshot, so every task begins
    from the same base whatever a sibling has done to its own. Outside one it
    is the live process environment, read through the *saved* method so an
    installed router cannot answer about some other task's view.

    Either way it arrives without `_NEVER_INHERITED`.
    """
    if _installs:
        return _inheritable(_snapshot)
    if "copy" in _environ_saved:  # router installed but not for this read
        return _inheritable(_environ_saved["copy"](os.environ))
    return _inheritable(dict(os.environ))


def _norm(key: str) -> str:
    """Windows environment lookups are case-insensitive; mirror that."""
    return key.upper() if os.name == "nt" else key


def _merged() -> dict[str, str]:
    """The virtual environment: simply the task's own, which is a whole one.

    It used to be `snapshot + overlay`, merged here and spelled two other ways
    elsewhere. A task now owns its environment outright, so there is one value
    and nothing to reconcile — which is what makes deletion ordinary.
    """
    from livery.footman.context import current

    env = current().env
    if os.name == "nt":
        return {k.upper(): v for k, v in env.items()}
    return dict(env)


def _note(kind: str, text: str) -> None:
    """Fire a task-attributed note — the door every interception uses.

    Levels, dedup, the site, the record and the policy all live in
    `_notes.emit`; this stays as the one spelling the routers import."""
    from livery.footman import _notes

    _notes.emit(kind, text)


def _in_task() -> bool:
    from livery.footman.context import current

    return current().in_task


def _install_environ() -> None:
    env_cls = type(os.environ)
    names = (
        "__getitem__",
        "__setitem__",
        "__delitem__",
        "__iter__",
        "__len__",
        "__contains__",
        "copy",
        "setdefault",
    )
    for name in names:
        _environ_saved[name] = getattr(env_cls, name)
    # The originals, typed as the unbound methods they are — the wrappers
    # below both call them and must honour their signatures.
    orig_get: Callable[[Any, str], str] = _environ_saved["__getitem__"]
    orig_set: Callable[[Any, str, str], None] = _environ_saved["__setitem__"]
    orig_del: Callable[[Any, str], None] = _environ_saved["__delitem__"]
    orig_iter: Callable[[Any], Any] = _environ_saved["__iter__"]
    orig_len: Callable[[Any], int] = _environ_saved["__len__"]
    orig_contains: Callable[[Any, object], bool] = _environ_saved["__contains__"]
    orig_copy: Callable[[Any], Any] = _environ_saved["copy"]
    orig_setdefault: Callable[[Any, str, str], str] = _environ_saved["setdefault"]

    def _virtual(self: Any) -> bool:
        if self is not os.environ or not _installs:
            return False
        from livery.footman.context import current

        # A serial/exclusive task owns the real globals: pass through.
        return not current().serial_active

    def __getitem__(self: Any, key: str) -> str:
        if not _virtual(self):
            return orig_get(self, key)
        return _merged()[_norm(key)]

    def __setitem__(self: Any, key: str, value: str) -> None:
        if not _virtual(self) or not _in_task():
            return orig_set(self, key, value)
        if not isinstance(key, str) or not isinstance(value, str):
            raise TypeError("str expected for environment keys and values")
        from livery.footman.context import current

        ctx = current()
        ctx.env[key] = value
        # The variable is the instance: one note per variable, so every
        # issue surfaces in one run and a known-harmless write can be
        # classified by name without hiding the next variable.
        _note(
            f"environ-write:{_norm(key)}",
            f"task {ctx.task or key} sets {key} via os.environ — footman "
            f"scoped it to this task (children see it, siblings don't). "
            f"Say it on purpose with env= / ctx.env.",
        )

    def __delitem__(self: Any, key: str) -> None:
        if not _virtual(self) or not _in_task():
            return orig_del(self, key)
        from livery.footman.context import current

        # Ordinary, because a task owns its environment outright: the key goes
        # from this task's copy and from every child it spawns after, while a
        # sibling's copy is untouched. This used to be a taught error for any
        # key the task had not set itself — the overlay had no way to say
        # "absent" — which left presence-semantics variables (NO_COLOR, CI)
        # impossible to hide from an in-process tool.
        ctx = current()
        hits = [k for k in ctx.env if _norm(k) == _norm(key)]
        if not hits:
            raise KeyError(key)  # absent: Python's own answer
        for k in hits:
            del ctx.env[k]

    def __iter__(self: Any) -> Any:
        if not _virtual(self):
            return orig_iter(self)
        return iter(_merged())

    def __len__(self: Any) -> int:
        if not _virtual(self):
            return orig_len(self)
        return len(_merged())

    def __contains__(self: Any, key: object) -> bool:
        if not _virtual(self):
            return orig_contains(self, key)
        return isinstance(key, str) and _norm(key) in _merged()

    def copy(self: Any) -> Any:
        if not _virtual(self):
            return orig_copy(self)
        return _merged()

    def setdefault(self: Any, key: str, value: str) -> str:
        if not _virtual(self):
            return orig_setdefault(self, key, value)
        try:
            found: str = self[key]
        except KeyError:
            self[key] = value
            return value
        return found

    # Deliberate runtime patching — the router IS a monkeypatch, and no
    # checker has a way to bless a static spelling of one (ty refuses even
    # signature-identical patches, and which os.* names exist depends on
    # the platform being checked). setattr says what is happening — a
    # dynamic write — and needs no suppression on any checker or platform;
    # ruff's B010 is excused for this file in pyproject.
    setattr(env_cls, "__getitem__", __getitem__)
    setattr(env_cls, "__setitem__", __setitem__)
    setattr(env_cls, "__delitem__", __delitem__)
    setattr(env_cls, "__iter__", __iter__)
    setattr(env_cls, "__len__", __len__)
    setattr(env_cls, "__contains__", __contains__)
    setattr(env_cls, "copy", copy)
    setattr(env_cls, "setdefault", setdefault)


def _restore_environ() -> None:
    env_cls = type(os.environ)
    for name, orig in _environ_saved.items():
        setattr(env_cls, name, orig)
    _environ_saved.clear()


# --- the Popen injection ------------------------------------------------------

_popen_saved: Any = None


def _managed_task() -> tuple[Any, bool]:
    """(ctx, guarded) — guarded only inside a managed *parallel* task body.

    `unmanaged` is the one off-switch (that token *means* footman stays
    out), and a serial/exclusive task owns the real globals legitimately.
    Work footman does on the body's behalf (`internal()`) is never guarded
    either: it has already worked out whatever a guard would fill in, and a
    note about it would name a line the author never wrote."""
    from livery.footman.context import current

    ctx = current()
    guarded = (
        bool(_installs)
        and not _is_internal()
        and ctx.in_task
        and not ctx.cwd_unmanaged
        and not ctx.serial_active
    )
    return ctx, guarded


def _install_popen() -> None:
    import subprocess

    global _popen_saved
    _popen_saved = subprocess.Popen.__init__
    orig = _popen_saved

    def __init__(self: Any, args: Any, *pa: Any, **kw: Any) -> None:
        ctx, guarded = _managed_task()
        # Inject only for a managed parallel task, only into kwargs left at
        # their defaults, and never when positionals reach as far as cwd
        # (the 10-positional spelling is antique; leave it alone entirely).
        if guarded and len(pa) < 9:
            filled = []
            if kw.get("cwd") is None and ctx.cwd is not None:
                kw["cwd"] = ctx.cwd
                filled.append("cwd")
            if kw.get("env") is None:
                kw["env"] = dict(ctx.env)  # the task's own, handed over whole
                filled.append("env")
            if filled:
                from livery.footman import _notes

                _note(
                    f"popen-inject:{_notes.program_name(args)}",
                    f"task {ctx.task or '?'} spawns via raw subprocess — "
                    f"footman filled in {' and '.join(filled)} from the task "
                    f"context. Prefer run() for capture and reporting, or "
                    f"pass cwd=/env= to make it deliberate.",
                )
        orig(self, args, *pa, **kw)

    setattr(subprocess.Popen, "__init__", __init__)


def _restore_popen() -> None:
    global _popen_saved
    if _popen_saved is not None:
        import subprocess

        setattr(subprocess.Popen, "__init__", _popen_saved)
        _popen_saved = None


# --- the os guards ------------------------------------------------------------

_guard_saved: dict[str, Any] = {}


_tempdir_warmed = False
# Set while footman itself is the one asking a guarded question. Thread-local
# on purpose: a sibling task reading the cwd for real, in its own thread, still
# earns its note while this one is resolving.
_internal = threading.local()


def _is_internal() -> bool:
    return bool(getattr(_internal, "on", False))


def internal() -> Any:
    """Mark this thread as footman working on the body's behalf, so the
    guards and the Popen injector stay out of the way. Saves and restores
    rather than clearing, so a nested region leaves the outer one standing."""
    import contextlib

    @contextlib.contextmanager
    def _internal_cm() -> Any:
        was = _is_internal()
        _internal.on = True
        try:
            yield
        finally:
            _internal.on = was

    return _internal_cm()


def warm_tempdir(cwd: Any) -> None:
    """Resolve `tempfile`'s temp directory before a task whose cwd has shifted.

    `tempfile.gettempdir()` builds its candidate list by appending
    `os.getcwd()` — eagerly, despite the stdlib comment calling it a last
    resort — so the *first* `mkdtemp()` in a run reads the process cwd. The
    getcwd guard would then attribute that read to the task, which never made
    it: "task X reads the process cwd" about a line the author did not write.

    Resolving it here rather than at `install()` is the whole point. The note
    only fires when the task's directory differs from the process directory,
    so a run started where the tasks file lives — the common case — has
    nothing to prevent and should not pay ~3 ms to prevent it.

    Called with `ctx.cwd` known and `ctx.in_task` still False, so `_managed_task`
    reads unguarded and this resolution cannot trip the very note it exists to
    avoid. Idempotent: `tempfile` caches its answer for the process, so the
    flag only spares the repeat call.
    """
    global _tempdir_warmed
    if _tempdir_warmed or cwd is None:
        return
    if str(cwd) == real_getcwd():
        return  # the answer would have been right anyway
    _tempdir_warmed = True
    import tempfile

    # `run_task` sets `in_task` before the cwd is even resolved, so this
    # resolution is guarded like a body's. It is not a body's: mark it as
    # footman's own, or the warm earns the note it exists to prevent.
    with internal():
        tempfile.gettempdir()


def _install_os_guards() -> None:
    _guard_saved["chdir"] = os.chdir
    _guard_saved["getcwd"] = os.getcwd
    _guard_saved["putenv"] = getattr(os, "putenv", None)
    _guard_saved["unsetenv"] = getattr(os, "unsetenv", None)
    _guard_saved["fchdir"] = getattr(os, "fchdir", None)
    _guard_saved["fork"] = getattr(os, "fork", None)

    def _chdir_error(ctx: Any) -> RuntimeError:
        return RuntimeError(
            f"task {ctx.task or '?'} changes the process directory in a "
            f"parallel task — the cwd belongs to no one there. The ladder: "
            f"build paths from footman.cwd() (no chdir at all); or claim "
            f"lanes=(cwd_lane,) — footman roots the real directory at the "
            f"task's cwd for the hold; or mark the task serial to own the "
            f"real globals and chdir freely."
        )

    orig_chdir: Callable[[Any], None] = _guard_saved["chdir"]

    def chdir(path: Any) -> None:
        ctx, guarded = _managed_task()
        if guarded:
            # A chdir to where the process already is changes nothing for
            # anyone — the defensive restore pattern (pytest's wrap_session
            # re-chdirs to its startpath on the way out). Only a real move
            # is refused.
            try:
                same = os.path.realpath(path) == os.path.realpath(real_getcwd())
            except (OSError, TypeError):  # a dir fd, an unstatable path
                same = False
            if not same:
                raise _chdir_error(ctx)
        orig_chdir(path)

    setattr(os, "chdir", chdir)

    if _guard_saved["fchdir"] is not None:
        orig_fchdir: Callable[[int], None] = _guard_saved["fchdir"]

        def fchdir(fd: Any) -> None:
            ctx, guarded = _managed_task()
            if guarded:
                raise _chdir_error(ctx)
            orig_fchdir(fd)

        setattr(os, "fchdir", fchdir)

    orig_getcwd: Callable[[], str] = _guard_saved["getcwd"]

    def getcwd() -> str:
        here = orig_getcwd()
        if _is_internal():
            return here  # footman resolving something on the task's behalf
        ctx, guarded = _managed_task()
        # Only when the answer is actually misleading. A task whose directory
        # *is* the process directory — the common case, the runner started
        # where the tasks file lives — gets a correct answer, and saying
        # otherwise teaches people to ignore notes. The sibling chdir guard
        # learned the same lesson: a move to where the process already is
        # changes nothing for anyone, so it stopped being refused. A string
        # compare against the already-resolved ctx.cwd, because getcwd can be
        # polled in a loop where chdir can afford a realpath.
        if guarded and ctx.cwd is not None and str(ctx.cwd) != here:
            _note(
                "getcwd",
                f"task {ctx.task or '?'} reads the process cwd, which is not "
                f"this task's directory ({ctx.cwd}) — a parallel run never "
                f"chdirs. cwd() is the directory you want.",
            )
        return here

    setattr(os, "getcwd", getcwd)

    def _env_bypass_error(name: str) -> RuntimeError:
        return RuntimeError(
            f"os.{name} bypasses env scoping even in plain Python (it never "
            f"updates os.environ) — assign through os.environ (scoped to "
            f"this task), or pass env= to the call."
        )

    if _guard_saved["putenv"] is not None:
        orig_putenv = _guard_saved["putenv"]

        def putenv(name: str, value: str) -> None:
            _, guarded = _managed_task()
            if guarded:
                raise _env_bypass_error("putenv")
            orig_putenv(name, value)

        setattr(os, "putenv", putenv)

    if _guard_saved["unsetenv"] is not None:
        orig_unsetenv = _guard_saved["unsetenv"]

        def unsetenv(name: str) -> None:
            _, guarded = _managed_task()
            if guarded:
                raise _env_bypass_error("unsetenv")
            orig_unsetenv(name)

        setattr(os, "unsetenv", unsetenv)

    if _guard_saved["fork"] is not None:
        orig_fork: Callable[[], int] = _guard_saved["fork"]

        def fork() -> int:
            ctx, guarded = _managed_task()
            if guarded:
                _note(
                    "fork",
                    f"task {ctx.task or '?'} forks — forking a threaded "
                    f"process is unsafe (the child can inherit locks "
                    f"mid-hold). Prefer run()/subprocess, or mark the task "
                    f"serial.",
                )
            return orig_fork()

        setattr(os, "fork", fork)


def _restore_os_guards() -> None:
    if not _guard_saved:
        return
    setattr(os, "chdir", _guard_saved["chdir"])
    setattr(os, "getcwd", _guard_saved["getcwd"])
    if _guard_saved["fchdir"] is not None:
        setattr(os, "fchdir", _guard_saved["fchdir"])
    if _guard_saved["putenv"] is not None:
        setattr(os, "putenv", _guard_saved["putenv"])
    if _guard_saved["unsetenv"] is not None:
        setattr(os, "unsetenv", _guard_saved["unsetenv"])
    if _guard_saved["fork"] is not None:
        setattr(os, "fork", _guard_saved["fork"])
    _guard_saved.clear()


# --- multiprocessing detection ------------------------------------------------

_mp_saved: Any = None


def _install_multiprocessing() -> None:
    global _mp_saved
    if "multiprocessing.process" not in sys.modules:
        # Nothing has imported multiprocessing, so no task can have spawned a
        # worker for the note to describe. Importing it here purely to install
        # a diagnostic costs ~2.5 ms of every run's startup.
        return
    try:
        from multiprocessing import process as mp_process
    except Exception:  # a stripped-down build without multiprocessing
        return
    _mp_saved = mp_process.BaseProcess.start
    orig: Callable[[Any], None] = _mp_saved

    def start(self: Any) -> None:
        ctx, guarded = _managed_task()
        if guarded:
            _note(
                "mp-start",
                f"task {ctx.task or '?'} spawns worker processes in-process "
                f"— they inherit the real environment (run-wide state like "
                f"colour is published there), but not the task's overlay. A "
                f"tool that parallelises itself loses little in the serial "
                f"lane: mark the task serial.",
            )
        return orig(self)

    setattr(mp_process.BaseProcess, "start", start)


def _restore_multiprocessing() -> None:
    global _mp_saved
    if _mp_saved is not None:
        from multiprocessing import process as mp_process

        setattr(mp_process.BaseProcess, "start", _mp_saved)
        _mp_saved = None


# --- the argv router ----------------------------------------------------------

_argv_saved: Any = None
_argv_ctx: ContextVar[list[str] | None] = ContextVar("footman_argv", default=None)


class _ArgvProxy(list[str]):
    """The run's `sys.argv` — a real list underneath, with every Python-level
    operation consulting the current call's override first.

    This is what lets a legacy zero-argument `main()` run in parallel: each
    in-process call sees its *own* argv through the same `sys.argv` object,
    no lock, no global patch. A C extension reading the list storage directly
    sees the base (the process's real argv) and degrades to the old
    behaviour; direct reassignment (`sys.argv = […]`) replaces the proxy and
    stays process-global — both documented edges, neither a crash."""

    def _ov(self) -> list[str] | None:
        return _argv_ctx.get()

    def __getitem__(self, i: Any) -> Any:
        ov = self._ov()
        return ov[i] if ov is not None else super().__getitem__(i)

    def __setitem__(self, i: Any, v: Any) -> None:
        ov = self._ov()
        if ov is not None:
            ov[i] = v
        else:
            super().__setitem__(i, v)

    def __delitem__(self, i: Any) -> None:
        ov = self._ov()
        if ov is not None:
            del ov[i]
        else:
            super().__delitem__(i)

    def __iter__(self) -> Any:
        ov = self._ov()
        return iter(ov) if ov is not None else super().__iter__()

    def __len__(self) -> int:
        ov = self._ov()
        return len(ov) if ov is not None else super().__len__()

    def __contains__(self, x: Any) -> bool:
        ov = self._ov()
        return x in ov if ov is not None else super().__contains__(x)

    def __repr__(self) -> str:
        ov = self._ov()
        return repr(ov) if ov is not None else super().__repr__()

    def __eq__(self, other: Any) -> bool:
        ov = self._ov()
        return (ov == other) if ov is not None else super().__eq__(other)

    __hash__ = None  # lists are unhashable

    def __add__(self, other: Any) -> Any:
        ov = self._ov()
        return (ov + other) if ov is not None else list(self) + other

    def append(self, v: Any) -> None:
        ov = self._ov()
        ov.append(v) if ov is not None else super().append(v)

    def extend(self, it: Any) -> None:
        ov = self._ov()
        ov.extend(it) if ov is not None else super().extend(it)

    def insert(self, i: Any, v: Any) -> None:
        ov = self._ov()
        ov.insert(i, v) if ov is not None else super().insert(i, v)

    def pop(self, i: Any = -1) -> Any:
        ov = self._ov()
        return ov.pop(i) if ov is not None else super().pop(i)

    def remove(self, v: Any) -> None:
        ov = self._ov()
        ov.remove(v) if ov is not None else super().remove(v)

    def clear(self) -> None:
        ov = self._ov()
        ov.clear() if ov is not None else super().clear()

    def index(self, *a: Any) -> int:
        ov = self._ov()
        return ov.index(*a) if ov is not None else super().index(*a)

    def count(self, v: Any) -> int:
        ov = self._ov()
        return ov.count(v) if ov is not None else super().count(v)

    def copy(self) -> list[str]:
        ov = self._ov()
        return list(ov) if ov is not None else list(self)

    # In-place mutators. Unoverridden these fall through to `list`, which
    # edits the proxy's own base storage — so the change vanishes from the
    # caller (reads consult the override) *and* leaks into every call that
    # has none. A legacy `main()` appending a default (`sys.argv += [...]`)
    # is the realistic one; the rest are here so the set has no holes.
    def __iadd__(self, other: Any) -> Any:
        ov = self._ov()
        if ov is None:
            return super().__iadd__(other)
        ov.extend(other)
        return self

    def __imul__(self, n: Any) -> Any:
        ov = self._ov()
        if ov is None:
            return super().__imul__(n)
        ov *= n
        return self

    def __mul__(self, n: Any) -> Any:
        ov = self._ov()
        return (ov * n) if ov is not None else list(self) * n

    __rmul__ = __mul__

    def __reversed__(self) -> Any:
        ov = self._ov()
        return reversed(ov) if ov is not None else super().__reversed__()

    def sort(self, **kw: Any) -> None:
        ov = self._ov()
        ov.sort(**kw) if ov is not None else super().sort(**kw)

    def reverse(self) -> None:
        ov = self._ov()
        ov.reverse() if ov is not None else super().reverse()


def argv_override(args: list[str]) -> Any:
    """A context manager giving this thread its own `sys.argv` view for the
    block — served by the argv router, lock-free. toolroom's bridge wraps a
    legacy zero-argument `main()` in one, so those calls parallelise like
    their argument-accepting siblings."""
    import contextlib

    @contextlib.contextmanager
    def _cm() -> Any:
        token = _argv_ctx.set(list(args))
        try:
            yield
        finally:
            _argv_ctx.reset(token)

    return _cm()


def _install_argv() -> None:
    import sys as _sys

    global _argv_saved
    _argv_saved = _sys.argv
    _sys.argv = _ArgvProxy(_argv_saved)


def _restore_argv() -> None:
    import sys as _sys

    global _argv_saved
    if _argv_saved is not None:
        _sys.argv = _argv_saved
        _argv_saved = None


# --- the stdin router ---------------------------------------------------------

_stdin_saved: Any = None


class _GuardedStdin:
    """The parallel regime's stdin — the fourth global, guarded not served.

    A read from a plain parallel task body is a taught error (one terminal,
    consumed rather than mutated: it belongs to no one there); the
    framework's own boundary prompts run outside task bodies, an
    `interactive=True` or serial task owns the terminal legitimately, and
    everything outside a run passes through untouched. `input()` is caught
    too: with `sys.stdin` replaced, Python falls back to
    `sys.stdin.readline()` instead of the C readline path.
    """

    def __init__(self, real: Any) -> None:
        self._real = real

    def _guard(self) -> None:
        from livery.footman.context import current

        ctx = current()
        if _installs and ctx.in_task and not ctx.interactive and not ctx.serial_active:
            raise RuntimeError(
                f"task {ctx.task or '?'} reads stdin in a parallel task — "
                f"declare the value with ask(), or mark the task "
                f"interactive=True to own the terminal."
            )

    def read(self, *a: Any) -> Any:
        self._guard()
        return self._real.read(*a)

    def readline(self, *a: Any) -> Any:
        self._guard()
        return self._real.readline(*a)

    def readlines(self, *a: Any) -> Any:
        self._guard()
        return self._real.readlines(*a)

    def __iter__(self) -> Any:
        self._guard()
        return iter(self._real)

    def __getattr__(self, name: str) -> Any:  # fileno, isatty, encoding, …
        return getattr(self._real, name)


def _install_stdin() -> None:
    import sys as _sys

    global _stdin_saved
    _stdin_saved = _sys.stdin
    _sys.stdin = _GuardedStdin(_stdin_saved)


def _restore_stdin() -> None:
    import sys as _sys

    global _stdin_saved
    if _stdin_saved is not None:
        _sys.stdin = _stdin_saved
        _stdin_saved = None


# --- the arbiter lanes --------------------------------------------------------
#
# Serialisation in the new regime is *declared* (serial= / exclusive=) and
# acquired at task boundaries, where it can be scheduled instead of contended
# for. The serial lane holds at most one owner and overlaps the parallel
# pool; exclusive drains the world. A parent parked in a pool wait on its own
# children is exempt from the drain (it is blocked in footman code and cannot
# touch globals), and a child of a lane holder inherits the lane through its
# context (`serial_active`), bypassing every bar — a lineage extends a hold,
# it never contends with it.

_arb_cv = threading.Condition(threading.Lock())
_running = 0  # task bodies in flight (scheduler nodes + parallel() children)
_parked = 0  # of those, parked waiting on their own children
_serial_holder: str | None = None
_excl_holder: str | None = None
_console_holder: str | None = None
_excl_waiting = 0


class Lane:
    """A serialised claim on one named resource: one holder at a time,
    contending only with claimants of the SAME lane — unrelated work runs
    untouched (nothing drains; that is `exclusive=`, a different regime).

    Made by `footman.lane()` and claimed by handing the handle to a task's
    or a step maker's `lanes=`: the import system is the registry, so a
    misspelt lane is an undefined name, and sharing a resource across
    modules is importing its binding. footman itself ships exactly two —
    `cwd_lane` and `console_lane` — built with the same call.
    """

    __slots__ = ("name", "reason")

    name: str
    """The resource's one name — what the live line shows a wait on."""
    reason: str | None
    """Optional documentation on the declaration."""

    def __init__(self, name: str, reason: str | None) -> None:
        self.name = name
        self.reason = reason

    def __repr__(self) -> str:
        return f"<lane {self.name}>"


_lane_sites: dict[str, str] = {}  # lane name -> the declaration site


def make_lane(name: str, *, reason: str | None = None) -> Lane:
    """Declare a named resource to serialise on. One binding per resource:
    re-declaring a taken name is a refusal naming both sites — reuse is
    spelled by importing the handle, never by re-declaring. (The same
    site re-executing — a module re-imported — is the same declaration.)
    """
    # `sys._getframe`, not `inspect.stack()`: the site is one filename and one
    # line number, and `inspect.stack()` builds a whole FrameInfo for every
    # frame below — reading source files to do it. Measured at 2.1 ms a call
    # from a tasks file, against 1.1 µs here, and footman declares two lanes
    # at import time, so it was ~6 ms on the execution path before anything
    # ran. Same answer, two thousand times cheaper.
    frame = sys._getframe(1)
    site = f"{frame.f_code.co_filename}:{frame.f_lineno}"
    taken = _lane_sites.get(name)
    if taken is not None and taken != site:
        raise ValueError(
            f"lane {name!r} is already declared at {taken} — one binding per "
            f"resource. Share it by importing that handle; a second "
            f"declaration would make two lanes that never contend."
        )
    _lane_sites[name] = site
    return Lane(name, reason)


_named_holders: dict[str, str] = {}  # lane name -> holder, under _arb_cv


def named_lanes(lanes: tuple[Lane, ...], name: str = "") -> Any:
    """Hold *lanes* around one piece of work — a step's claims.

    Granted atomically in a single predicate under the arbiter's one
    condition variable (all lanes at once, no partial holds, so
    hold-and-wait between lanes cannot be spelled), and released at the
    one place the hold ends.
    """
    import contextlib

    @contextlib.contextmanager
    def _hold() -> Any:
        global _parked
        if not lanes:
            yield []
            return
        wanted = {ln.name for ln in lanes}
        stalled = False
        # The lineage exemption `lane()` already applies, applied here too: a
        # serial/exclusive task *is* the holder of the lane this claim would
        # wait for, so waiting on it is waiting for itself. The hold is
        # already total — it conflicts with every named lane — so a step
        # inside it needs no further grant, and asking for one deadlocks the
        # run deterministically.
        from livery.footman.context import current

        mine = bool(getattr(current(), "serial_active", False))
        with _arb_cv:
            # Inside the lock on purpose: the clock must cover only the
            # predicate wait. Started outside, a claim granted on arrival
            # would book the mutex acquisition as a lane wait — a phantom
            # row whenever the arbiter is briefly busy.
            entered = time.monotonic()
            # Parked across the wait, the same exemption a pool wait takes:
            # this body is blocked in footman's own code and can touch nothing,
            # so it must not hold the exclusive drain open. Without it the two
            # predicates close a circle — the drain waits for every body to
            # finish, and this claim waits for the queued drain — and the run
            # hangs with nothing shared between the two tasks. Counted here
            # rather than through `parked()`, which takes the arbiter lock this
            # frame already holds.
            #
            # `_excl_waiting` stays in the predicate below: a queued exclusive
            # must still block *new* claims, or a stream of them starves it.
            _parked += 1
            _arb_cv.notify_all()
            try:
                while any(w in _named_holders for w in wanted) or (
                    not mine
                    and (
                        _serial_holder is not None
                        or _excl_holder is not None
                        or _excl_waiting
                    )
                ):
                    stalled = True
                    busy = next((w for w in wanted if w in _named_holders), None)
                    _wait_note(
                        name,
                        busy or "exclusive",
                        f"the {busy} lane" if busy else "the exclusive drain",
                        _named_holders.get(busy or "")
                        or _serial_holder
                        or _excl_holder,
                    )
            finally:
                _parked -= 1
            for w in wanted:
                _named_holders[w] = name or "?"
        try:
            yield _waited(",".join(sorted(wanted)), entered) if stalled else []
        finally:
            with _arb_cv:
                for w in wanted:
                    _named_holders.pop(w, None)
                _arb_cv.notify_all()

    return _hold()


def lane(
    policy: str | None,
    name: str = "",
    inherited: bool = False,
    console: bool = False,
    named: tuple[Lane, ...] = (),
) -> Any:
    """A context manager holding *policy*'s lane around one task body.

    `None` is the parallel regime: it only counts the body for the drain
    and yields to a waiting exclusive first. `console=True` additionally
    claims the terminal (an `interactive=True` body): one owner at a time,
    granted **atomically with the policy lane** in a single predicate, so a
    partial hold can never chain into hold-and-wait between lanes.
    `inherited` marks a lineage child of a lane holder: it bypasses every
    bar (the holder is waiting on it) and only counts.
    """
    import contextlib

    @contextlib.contextmanager
    def _lane() -> Any:
        global _running, _serial_holder, _excl_holder, _excl_waiting, _console_holder
        if not _installs:
            yield []
            return
        wanted = {ln.name for ln in named}
        if inherited:
            label = ""  # a lineage bypasses every bar, so it never waits
        elif policy in ("serial", "exclusive"):
            label = policy
        elif wanted:
            label = ",".join(sorted(wanted))
        else:
            label = "console" if console else "exclusive"
        stalled = False
        with _arb_cv:
            # Inside the lock on purpose: the clock must cover only the
            # predicate wait, or a claim granted on arrival books the mutex
            # acquisition as a lane wait (a phantom row on a busy arbiter).
            entered = time.monotonic()
            if inherited:
                pass  # a lineage extends every hold, the console included
            elif policy == "serial":
                # serial= IS the all-lanes claim: it conflicts with every
                # named holder, and every named claim waits on it.
                while (
                    _serial_holder is not None
                    or _excl_holder is not None
                    or _excl_waiting
                    or _named_holders
                    or (console and _console_holder is not None)
                ):
                    stalled = True
                    _wait_note(
                        name,
                        "serial",
                        "the serial lane",
                        _serial_holder or _excl_holder,
                    )
                _serial_holder = name or "?"
            elif policy == "exclusive":
                _excl_waiting += 1
                try:
                    while (
                        _excl_holder is not None
                        or _serial_holder is not None
                        or (_running - _parked) > 0
                        or _named_holders
                        or (console and _console_holder is not None)
                    ):
                        stalled = True
                        _wait_note(
                            name, "exclusive", "the exclusive drain", _serial_holder
                        )
                finally:
                    _excl_waiting -= 1
                _excl_holder = name or "?"
            else:
                while (
                    _excl_holder is not None
                    or _excl_waiting
                    or (console and _console_holder is not None)
                    or any(w in _named_holders for w in wanted)
                ):
                    stalled = True
                    busy = next((w for w in wanted if w in _named_holders), None)
                    what = (
                        f"the {busy} lane"
                        if busy
                        else ("the console" if console else "the exclusive drain")
                    )
                    lane = busy or ("console" if console else "exclusive")
                    _wait_note(name, lane, what, _console_holder or _excl_holder)
            if console and not inherited:
                _console_holder = name or "?"
            for w in wanted:
                _named_holders[w] = name or "?"
            _running += 1
        claimed_console = console and not inherited
        if claimed_console:
            _suspend_status(True)  # outside the cv: lock order is arb → status
        try:
            yield _waited(label, entered) if stalled and label else []
        finally:
            with _arb_cv:
                _running -= 1
                if not inherited:
                    if policy == "serial":
                        _serial_holder = None
                    elif policy == "exclusive":
                        _excl_holder = None
                    if console:
                        _console_holder = None
                for w in wanted:
                    _named_holders.pop(w, None)
                _arb_cv.notify_all()
            if claimed_console:
                _suspend_status(False)

    return _lane()


def _suspend_status(on: bool) -> None:
    """Pause/resume the live status line around console ownership — its
    repaints and a wizard's prompt would fight for the one terminal."""
    from livery.footman.context import active_status

    status = active_status()
    if status is None:
        return
    (status.suspend if on else status.resume)()


def console_gate() -> Any:
    """Hold a real-terminal write until no console owner is active.

    A captured sibling finishing mid-wizard must not splat its buffered
    block over the wizard's screen: the flush queues here and lands when
    the terminal frees. Best-effort by design — a wizard *starting* between
    the gate and the write is the same race as one starting just after it,
    and the write is a single atomic block either way."""
    import contextlib

    @contextlib.contextmanager
    def _gate() -> Any:
        if not _installs:
            yield
            return
        with _arb_cv:
            while _console_holder is not None:
                _arb_cv.wait(timeout=1.0)
        yield

    return _gate()


def _wait_note(name: str, lane: str, what: str, holder: str | None) -> None:
    """One bounded wait step, with a one-time visibility note — a lane wait
    must never be a silent hang. The *lane* is the kind's instance (what
    is being waited for); the waiting task is the dedup's other half
    already, so it never rides the kind."""
    if not _arb_cv.wait(timeout=2.0):
        held = f" (held by {holder})" if holder else ""
        _note(f"lane-wait:{lane}", f"task {name or '?'} waiting for {what}{held}")


def _waited(label: str, entered: float) -> list[tuple[str, float]]:
    """What this claim paid at the bar, for the run report.

    Called only after the claim's predicate genuinely stalled; the clock
    started inside the arbiter lock, so mutex acquisition never counts. One
    `(label, seconds)` row, unless the stall resolved inside a millisecond —
    that is arrival, not serialisation. The claim's own label, not the
    blocker-of-the-moment: a wait can pass through several holders, and what
    the report answers is "what serialised *this*"."""
    waited = time.monotonic() - entered
    return [(label, waited)] if waited >= 0.001 else []


def parked() -> Any:
    """Mark the current body parked (a pool wait on its own children): it is
    blocked in footman code and cannot touch globals, so the exclusive drain
    exempts it — this is the ancestry exemption."""
    import contextlib

    @contextlib.contextmanager
    def _parked_cm() -> Any:
        global _parked
        if not _installs:
            yield
            return
        with _arb_cv:
            _parked += 1
            _arb_cv.notify_all()
        try:
            yield
        finally:
            with _arb_cv:
                _parked -= 1

    return _parked_cm()


_gc_deferred = False


def defer_gc() -> None:
    """Stop collecting until the run reaches task bodies.

    Startup allocates a great deal and discards almost none of it: the
    framework's own modules, then the user's tasks file. Left alone, CPython
    runs about eleven collections getting there, each one walking objects that
    are all still live. Only the console-script entry calls this, because only
    it owns the whole process; `Runner` drives a run inside somebody else's
    process and must not touch a global switch.
    """
    global _gc_deferred
    import gc

    gc.disable()
    _gc_deferred = True


def _resume_gc() -> None:
    """Hand startup's objects to the permanent generation, then collect again.

    Paired with `defer_gc`, and a no-op without it. The freeze is what makes
    re-enabling cheap: everything allocated up to here is live and will stay
    live for the few milliseconds this process has left, so it should never be
    traversed again — while a task body that makes real garbage still gets a
    real collector. Forcing a collection here instead measured *slower* than
    plain: the traversal costs more than the garbage is worth.
    """
    global _gc_deferred
    if not _gc_deferred:
        return
    import gc

    gc.freeze()
    gc.enable()
    _gc_deferred = False


def install() -> None:
    """Arm the routers for a run. Refcounted; the first install pins the
    environment snapshot (so anything published at the run boundary —
    colour, say — is in it)."""
    global _installs
    with _lock:
        _installs += 1
        if _installs > 1:
            return
        # The last thing before task bodies run, and the reason `install` is
        # where this lives: everything the run needed to get here is loaded.
        _resume_gc()
        _snapshot.clear()
        _snapshot.update(os.environ)
        from livery.footman import _notes

        _notes.reset()  # every note may teach once more
        _install_environ()
        _install_popen()
        _install_os_guards()
        _install_multiprocessing()
        _install_stdin()
        _install_argv()


def uninstall() -> None:
    """Disarm after a run; the last uninstall restores the originals."""
    global _installs
    with _lock:
        if _installs == 0:
            return
        _installs -= 1
        if _installs > 0:
            return
        _restore_argv()
        _restore_stdin()
        _restore_multiprocessing()
        _restore_os_guards()
        _restore_popen()
        _restore_environ()
        _snapshot.clear()


# The two lanes footman itself ships — the only core lanes there will ever
# be, declared with the very call plugins and tasks files use. cwd is
# opt-in: claiming it is knowingly giving up some parallelism, and the
# import at the top of the file says so where reviewers read. The console
# is claimed implicitly by `interactive=` and explicitly here.
cwd_lane: Lane = make_lane("cwd", reason="the one real working directory")
console_lane: Lane = make_lane("console", reason="the one terminal")
