"""footman — a task runner with typed commands and instant completion.

Typed function signatures become real flags and positionals, modules become
nested command groups, and shell completion answers from a cached manifest
without importing your code. Building that manifest does import it, in a
detached subprocess: the first <kbd>Tab</kbd> in a fresh directory, and the
background rebuild once the cache goes stale.

The console-script entry lives here and is deliberately thin: completion must
dispatch to the stdlib-only hot path before importing the framework or the
user's tasks, so `main` checks `--complete` first and everything else is
imported lazily. A bare `from livery import footman` pays for nothing but this module.
"""

from __future__ import annotations

# The literal-False spelling both checkers honour without importing `typing`
# (~1.4 ms) — the completion dispatch runs through this module on every TAB
# press, and a bare `import footman` should pay for nothing it doesn't use.
TYPE_CHECKING = False
if TYPE_CHECKING:
    # Give type-checkers the real types for the lazily re-exported names below;
    # at runtime these are served by `__getattr__` without importing registry
    # on a bare `import footman` (the completion hot path).
    from livery.footman import docstrings as docstrings
    from livery.footman import markdown as markdown
    from livery.footman._fetch import FetchError as FetchError
    from livery.footman._fetch import fetch as fetch
    from livery.footman._globals import Lane as Lane
    from livery.footman._globals import console_lane as console_lane
    from livery.footman._globals import cwd_lane as cwd_lane
    from livery.footman._globals import make_lane as lane
    from livery.footman._step import step as step
    from livery.footman.app import App as App
    from livery.footman.app import Brand as Brand
    from livery.footman.compose import include as include
    from livery.footman.compose import plugin as plugin
    from livery.footman.context import Argv as Argv
    from livery.footman.context import AuditEntry as AuditEntry
    from livery.footman.context import Context as Context
    from livery.footman.context import Failed as Failed
    from livery.footman.context import Result as Result
    from livery.footman.context import ResultView as ResultView
    from livery.footman.context import RunFailed as RunFailed
    from livery.footman.context import Section as Section
    from livery.footman.context import Stream as Stream
    from livery.footman.context import TimedOut as TimedOut
    from livery.footman.context import attended as attended
    from livery.footman.context import cache_dir as cache_dir
    from livery.footman.context import chdir as chdir
    from livery.footman.context import colored as colored
    from livery.footman.context import config_dir as config_dir
    from livery.footman.context import config_file as config_file
    from livery.footman.context import confirm as confirm
    from livery.footman.context import cwd as cwd
    from livery.footman.context import data_dir as data_dir
    from livery.footman.context import dist as dist
    from livery.footman.context import fail as fail
    from livery.footman.context import given as given
    from livery.footman.context import inherited as inherited
    from livery.footman.context import mark as mark
    from livery.footman.context import parallel as parallel
    from livery.footman.context import passthrough as passthrough
    from livery.footman.context import prog as prog
    from livery.footman.context import progress as progress
    from livery.footman.context import project_root as project_root
    from livery.footman.context import prompt as prompt
    from livery.footman.context import run as run
    from livery.footman.context import section as section
    from livery.footman.context import select as select
    from livery.footman.context import stream as stream
    from livery.footman.context import track as track
    from livery.footman.context import tty as tty
    from livery.footman.context import use_context as use_context
    from livery.footman.context import user_tasks_file as user_tasks_file
    from livery.footman.invocation import Invocation as Invocation
    from livery.footman.params import Arg as Arg
    from livery.footman.params import Exists as Exists
    from livery.footman.params import Forward as Forward
    from livery.footman.params import Hidden as Hidden
    from livery.footman.params import IsDir as IsDir
    from livery.footman.params import IsFile as IsFile
    from livery.footman.params import Many as Many
    from livery.footman.params import NoSplit as NoSplit
    from livery.footman.params import Secret as Secret
    from livery.footman.params import Stdin as Stdin
    from livery.footman.params import Stdout as Stdout
    from livery.footman.params import ask as ask
    from livery.footman.params import between as between
    from livery.footman.params import check as check
    from livery.footman.params import default as default
    from livery.footman.params import doc as doc
    from livery.footman.params import env as env
    from livery.footman.params import exists as exists
    from livery.footman.params import forward as forward
    from livery.footman.params import hidden as hidden
    from livery.footman.params import isdir as isdir
    from livery.footman.params import isfile as isfile
    from livery.footman.params import matching as matching
    from livery.footman.params import nosplit as nosplit
    from livery.footman.params import stdin as stdin
    from livery.footman.params import stdout as stdout
    from livery.footman.params import suggest as suggest
    from livery.footman.registry import GlobalOption as GlobalOption
    from livery.footman.registry import Group as Group
    from livery.footman.registry import Tasks as Tasks
    from livery.footman.registry import TaskView as TaskView
    from livery.footman.registry import capture as capture
    from livery.footman.registry import config_section as config_section
    from livery.footman.registry import expose as expose
    from livery.footman.registry import group as group
    from livery.footman.registry import post_task as post_task
    from livery.footman.registry import post_tasks as post_tasks
    from livery.footman.registry import pre_bind as pre_bind
    from livery.footman.registry import pre_record as pre_record
    from livery.footman.registry import pre_task as pre_task
    from livery.footman.registry import pre_tasks as pre_tasks
    from livery.footman.registry import requires as requires
    from livery.footman.registry import requires_dep as requires_dep
    from livery.footman.registry import requires_env as requires_env
    from livery.footman.registry import requires_tool as requires_tool
    from livery.footman.registry import task as task
    from livery.footman.registry import wrap_bind as wrap_bind
    from livery.footman.registry import wrap_task as wrap_task
    from livery.footman.testing import Runner as Runner
    from livery.footman.testing import recording as recording

__version__ = "0.52.2"

BUILTIN = ("footman.self", "footman.janitor")
"""Stock footman's built-in task providers — what a project-less `fm` offers.

Named here, beside `main()`, because BOTH doors need it: the `App` the
execution path builds, and the `--complete` dispatch, which configures
`_paths` with it before the hot path decides whether this directory has a
global tree. A branded CLI passes its own through `App(builtin=…)`.
"""
__all__ = [
    "App",
    "Arg",
    "Argv",
    "AuditEntry",
    "Brand",
    "Context",
    "Exists",
    "Failed",
    "FetchError",
    "Forward",
    "GlobalOption",
    "Group",
    "Hidden",
    "Invocation",
    "IsDir",
    "IsFile",
    "Lane",
    "Many",
    "NoSplit",
    "Result",
    "ResultView",
    "RunFailed",
    "Runner",
    "Secret",
    "Section",
    "Stdin",
    "Stdout",
    "Stream",
    "TaskView",
    "Tasks",
    "TimedOut",
    "__version__",
    "ask",
    "attended",
    "between",
    "cache_dir",
    "capture",
    "chdir",
    "check",
    "colored",
    "config_dir",
    "config_file",
    "config_section",
    "confirm",
    "console_lane",
    "cwd",
    "cwd_lane",
    "data_dir",
    "default",
    "dist",
    "doc",
    "docstrings",
    "env",
    "exists",
    "expose",
    "fail",
    "fetch",
    "forward",
    "given",
    "group",
    "hidden",
    "include",
    "inherited",
    "isdir",
    "isfile",
    "lane",
    "main",
    "mark",
    "markdown",
    "matching",
    "nosplit",
    "parallel",
    "passthrough",
    "plugin",
    "post_task",
    "post_tasks",
    "pre_bind",
    "pre_record",
    "pre_task",
    "pre_tasks",
    "prog",
    "progress",
    "project_root",
    "prompt",
    "recording",
    "requires",
    "requires_dep",
    "requires_env",
    "requires_tool",
    "run",
    "section",
    "select",
    "stdin",
    "stdout",
    "step",
    "stream",
    "suggest",
    "task",
    "track",
    "tty",
    "use_context",
    "user_tasks_file",
    "wrap_bind",
    "wrap_task",
]


def main(tasks_file: str | None = None) -> None:
    """Console-script entry for `footman` and `fm`.

    `tasks_file` makes a tasks file its own command. Ending a file with

        if __name__ == "__main__":
            footman.main(__file__)

    turns it into a runnable script — `./deploy.py build` — reading its own
    tasks whatever the directory, which is what pairs with a PEP 723
    header and a `#!/usr/bin/env -S uv run --script` shebang. An explicit
    `-f` on the command line still wins.
    """
    import sys

    argv = sys.argv[1:]
    if argv and argv[0] == "--complete":
        # Still first: the hot path answers before anything else is decided,
        # and `footman.app` (with its dataclass machinery) stays off a TAB
        # press entirely. `_paths`' module defaults *are* stock footman's
        # locations — except the built-ins, which live on the `App` below
        # and no default can know. Telling `_paths` about them is two
        # attribute writes, and without it the hot path cannot see that a
        # project-less directory has a global tree at all: the fallback is
        # skipped, the refresh child writes nothing, and every TAB in a
        # directory like $HOME pays the full cold bound for empty output.
        from livery.footman import _paths
        from livery.footman._complete import complete_cli

        # `brand_version` too, not just the built-ins: the global-mode
        # manifest is keyed by (prog, version, builtins), and the execution
        # path configures the real version — leaving the default "" here
        # would give completion and execution two different global caches.
        _paths.configure(builtin=BUILTIN, brand_version=__version__)
        raise SystemExit(complete_cli(argv[1:]))
    # Past the hot path, so the TAB press above pays nothing for it (and
    # `_complete` writes bytes anyway): everything from here on prints text,
    # and a locale-encoded stdout defaults to errors='strict'. A task name, a
    # docstring, `--tree`'s own branch glyphs or the em dash in a header is
    # then unencodable on an ascii or cp1252 console, and a listing dies
    # half-written with a raw UnicodeEncodeError. Degrade those glyphs to '?'
    # instead. This is the same reconfigure `context.routing()` installs
    # around a run — hoisted here so the listings, which never start one, are
    # covered too.
    import contextlib

    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            # getattr, not hasattr-then-call: hasattr narrowing is not
            # portable across checkers, the getattr is.
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure is not None:
                reconfigure(errors="replace")
    if tasks_file is not None and not any(
        a.startswith(("-f=", "--tasks-file=")) for a in argv
    ):
        argv = [f"--tasks-file={tasks_file}", *argv]
    # Past the completion hot path, this process exists to run one command and
    # exit, so it should not spend its startup collecting garbage it has not
    # made yet. `_globals` turns the collector back on — with everything
    # loaded by then frozen — at the last moment before task bodies run.
    from livery.footman import _globals
    from livery.footman.app import App

    _globals.defer_gc()

    # `dist` names the distribution this console script ships in — what a
    # project's lockfile pins, and what a tasks file carrying its own
    # dependencies must declare. A branded CLI passes its own.
    #
    # The stock pins — `FOOTMAN_*` variables, `footman.toml` config, the
    # long words a two-letter command cannot derive — are `App`'s own: any
    # `App` that keeps the `fm` command gets them, this one included.
    # `fm self.*` in an empty directory: footman declares its own built-in,
    # the same way any branded CLI would.
    raise SystemExit(App(dist="livery-footman", builtin=BUILTIN).run(argv))


def __getattr__(name: str) -> object:
    # Lazy re-export: `from livery.footman import task, group` works without paying the
    # registry import on a bare `import footman` (the completion hot path).
    if name in (
        "task",
        "group",
        "Group",
        "capture",
        "config_section",
        "GlobalOption",
        "pre_tasks",
        "pre_record",
        "pre_bind",
        "pre_task",
        "post_task",
        "post_tasks",
        "wrap_task",
        "wrap_bind",
        "Tasks",
        "TaskView",
        "expose",
        "requires",
        "requires_dep",
        "requires_env",
        "requires_tool",
    ):
        from livery.footman import registry

        return getattr(registry, name)
    if name == "step":
        from livery.footman import _step

        return _step.step
    if name in ("Lane", "cwd_lane", "console_lane", "lane"):
        from livery.footman import _globals

        return _globals.make_lane if name == "lane" else getattr(_globals, name)
    if name in ("Runner", "recording"):
        from livery.footman import testing

        return getattr(testing, name)
    if name == "docstrings":
        import livery.footman.docstrings

        return livery.footman.docstrings
    if name == "markdown":
        import livery.footman.markdown

        return livery.footman.markdown
    if name in ("fetch", "FetchError"):
        from livery.footman import _fetch

        return getattr(_fetch, name)
    if name in ("include", "plugin"):
        from livery.footman import compose

        return getattr(compose, name)
    if name in (
        "Arg",
        "suggest",
        "Many",
        "nosplit",
        "exists",
        "isfile",
        "isdir",
        "matching",
        "between",
        "env",
        "check",
        "default",
        "doc",
        "ask",
        "forward",
        "Forward",
        "hidden",
        "Hidden",
        "NoSplit",
        "Secret",
        "stdin",
        "Stdin",
        "stdout",
        "Stdout",
        "Exists",
        "IsFile",
        "IsDir",
    ):
        from livery.footman import params

        return getattr(params, name)
    if name in (
        "run",
        "parallel",
        "Context",
        "Argv",
        "Result",
        "ResultView",
        "AuditEntry",
        "inherited",
        "given",
        "passthrough",
        "progress",
        "prompt",
        "chdir",
        "confirm",
        "cache_dir",
        "attended",
        "tty",
        "colored",
        "cwd",
        "data_dir",
        "config_dir",
        "config_file",
        "prog",
        "dist",
        "user_tasks_file",
        "project_root",
        "select",
        "track",
        "RunFailed",
        "Failed",
        "TimedOut",
        "fail",
        "use_context",
        "section",
        "stream",
        "mark",
        "Section",
        "Stream",
    ):
        from livery.footman import context

        return getattr(context, name)
    if name in ("App", "Brand"):
        from livery.footman import app

        return getattr(app, name)
    if name == "Invocation":
        from livery.footman import invocation

        return invocation.Invocation
    raise AttributeError(f"module 'livery.footman' has no attribute {name!r}")
