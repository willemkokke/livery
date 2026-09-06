"""Fresh values for a dynamic completer — spawned by the completion hot path.

A dynamic completer (`suggest(fn)`) queries live state: git branches, release
candidates, deploy targets. Serving the manifest's *baked* snapshot for a
build-critical answer is as wrong as answering from an empty cache, so when TAB
lands on a dynamic parameter `_complete` spawns this process to run that one
completer fresh.

It lives out of the hot path precisely because it imports the framework and the
user's code — the thing a TAB press must never do. Isolation is the point: a
slow or crashing completer dies here, bounded by the caller's timeout, and the
hot path degrades to no candidates rather than a hung keystroke.
"""

from __future__ import annotations

import contextlib
import inspect
import io
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # runtime imports stay deferred: the TAB path spawns cheap
    from pathlib import Path


def _maybe_reexec(files: list[Path]) -> None:
    """Continue in the environment that owns this completer — the project's
    when the lock rule claims the directory, a script file's when it
    carries its own dependencies. Both rules live in `_script`, shared
    with the refresh child, and are asked in the run path's order:
    project first, script only where no project has spoken. No healing
    here — a keystroke is waiting, and the detached refresh owns that.
    The interpreter flags come along too — `-P` on the spawn and not on
    the re-exec would be a hole, since this replaces the process with the
    argv it is handed."""
    from pathlib import Path

    from livery.footman import _script

    argv = ["-P", "-m", "livery.footman._suggest", *sys.argv[1:]]
    root = _script.project_home(Path.cwd())
    if root is not None:
        _script.project_reexec(root, argv, heal=False)
        return
    _script.maybe_reexec(files, argv)


def _fresh(completer: Any) -> list[str]:
    """Run *completer* with its own stdout/stderr muted, so its chatter can't
    leak into the value channel the hot path reads."""
    with (
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        return [str(v) for v in completer.fn()]


def _values(param: str, path: list[str], g: dict[str, object]) -> list[str]:
    """The fresh output of *param*'s completer on the task at *path*.

    Rediscovers the same tasks the manifest was built from (honouring
    `-f`/`--config`), walks to the task, peels the parameter, and runs its
    completer. Any miss — no tasks file, a renamed task, a plain parameter —
    is an empty list, never an error.
    """
    from livery.footman import _app, _coerce, _discover, _manifest, registry

    found = _app.resolve_task_files(g, on_warning=lambda *a: None, on_note=None)
    files = found.files
    if not files or not path:
        return []
    _maybe_reexec(files)  # before any user code is imported
    # Plugin mounts are authored in the tasks files, so discovery alone
    # rebuilds the whole tree — a completer on a mounted task included.
    base = registry.Group("root")
    root = _discover.load_tree(files, base=base)

    node: registry.Group | None = root
    for name in path[:-1]:  # descend the groups
        node = node.groups.get(name) if node else None
    task = node.tasks.get(path[-1]) if node else None
    if task is None:
        return []
    for p in _manifest.resolved_signature(task).parameters.values():
        if (
            registry.cli_name(p.name) != param
            or p.annotation is inspect.Parameter.empty
        ):
            continue
        completer = _coerce.peel(p.annotation).completer
        if completer is None:
            return []
        return _fresh(completer)
    return []


def _global_values(name: str, g: dict[str, object]) -> list[str]:
    """The fresh output of the completer on the plugin global *name*.

    The globals ride the tree's contributions, so the same discovery that
    finds a task's completer finds an option's: rediscover, match the cli
    name, peel the annotation, run what it carries. Any miss — no tasks
    file, an unmounted owner, a plain option — is an empty list."""
    from livery.footman import _app, _coerce, _discover, registry

    found = _app.resolve_task_files(g, on_warning=lambda *a: None, on_note=None)
    files = found.files
    if not files:
        return []
    _maybe_reexec(files)  # before any user code is imported
    base = registry.Group("root")
    root = _discover.load_tree(files, base=base)
    for opt in root.contributions.get("globals", ()):
        if opt.name != name:
            continue
        completer = _coerce.peel(opt.annotation).completer
        if completer is None:
            return []
        return _fresh(completer)
    return []


def main(argv: list[str]) -> int:
    param: str | None = None
    globopt: str | None = None
    path: list[str] = []
    g: dict[str, object] = {}
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--param" and i + 1 < len(argv):
            param, i = argv[i + 1], i + 2
        elif arg == "--global" and i + 1 < len(argv):
            globopt, i = argv[i + 1], i + 2
        elif arg == "--path" and i + 1 < len(argv):
            path.append(argv[i + 1])
            i += 2
        elif arg == "--tasks-file" and i + 1 < len(argv):
            g["tasks_file"], i = argv[i + 1], i + 2
        elif arg == "--config" and i + 1 < len(argv):
            g["config"], i = argv[i + 1], i + 2
        elif arg == "--where" and i + 1 < len(argv):
            # This CLI's resolved locations (and brand context), handed over
            # by the hot path — a child inherits the environment but not the
            # brand. Length-prefixed: the count leads the words, so growing
            # `child_args` can never eat the flags that follow — the arity
            # drifted once per release until it was written down.
            from livery.footman import _paths

            count = int(argv[i + 1])
            _paths.configure_child(*argv[i + 2 : i + 2 + count])
            i += 2 + count
        else:
            i += 1
    real = sys.stdout
    try:
        # This process's stdout IS the candidate channel, and the calls below
        # import the user's code before any candidate is written — so a tasks
        # file that prints at import time (or a plugin imported alongside it)
        # would have its chatter served to the shell as completions. Muted
        # for the whole computation — the policy `_fresh` already applies to
        # the completer body, applied to the import that precedes it — and
        # only the finished candidates touch the real stream. The re-exec a
        # script file may trigger in there is unaffected: the redirect
        # rebinds `sys.stdout`, never fd 1, and exec replaces the process.
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            if globopt is not None:
                values = _global_values(globopt, g)
            elif param is not None:
                values = _values(param, path, g)
            else:
                return 0
    except Exception:
        return 0  # any failure → no candidates; the hot path falls back to empty
    if values:
        # The candidate protocol is newline-delimited, so a value carrying a
        # newline would split into two bogus candidates — the second being
        # whatever came after the break. A completion token has no legal
        # newline; the value's first line is the whole candidate.
        real.write("\n".join(v.splitlines()[0] for v in values if v.strip()) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
