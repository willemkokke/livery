"""footman — a task runner with typed commands and instant completion.

Typed function signatures become real flags and positionals, modules become
nested command groups, and shell completion answers from a cached manifest
without importing your code. Building that manifest does import it, in a
detached subprocess: the first <kbd>Tab</kbd> in a fresh directory, and the
background rebuild once the cache goes stale.

The console-script entry lives here and is deliberately thin: completion must
dispatch to the stdlib-only hot path before importing the framework or the
user's tasks, so `main` checks `--complete` first and everything else is
imported lazily. A bare `import livery.footman as footman` pays for
nothing but this module.
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
    from livery.footman import docs as docs
    from livery.footman import docstrings as docstrings
    from livery.footman import markdown as markdown
    from livery.footman import profile as profile
    from livery.footman import testing as testing
    from livery.footman._application import App as App
    from livery.footman._application import Brand as Brand
    from livery.footman._compose import include as include
    from livery.footman._compose import plugin as plugin
    from livery.footman._config import project_builtins as project_builtins
    from livery.footman._context import Argv as Argv
    from livery.footman._context import AuditEntry as AuditEntry
    from livery.footman._context import CommandView as CommandView
    from livery.footman._context import Context as Context
    from livery.footman._context import Failed as Failed
    from livery.footman._context import Result as Result
    from livery.footman._context import ResultView as ResultView
    from livery.footman._context import RunFailed as RunFailed
    from livery.footman._context import RunTimeout as RunTimeout
    from livery.footman._context import Section as Section
    from livery.footman._context import Stream as Stream
    from livery.footman._context import TimedOut as TimedOut
    from livery.footman._context import attended as attended
    from livery.footman._context import cache_dir as cache_dir
    from livery.footman._context import chdir as chdir
    from livery.footman._context import colored as colored
    from livery.footman._context import config_dir as config_dir
    from livery.footman._context import config_file as config_file
    from livery.footman._context import confirm as confirm
    from livery.footman._context import current as current
    from livery.footman._context import cwd as cwd
    from livery.footman._context import data_dir as data_dir
    from livery.footman._context import dist as dist
    from livery.footman._context import fail as fail
    from livery.footman._context import given as given
    from livery.footman._context import inherited as inherited
    from livery.footman._context import mark as mark
    from livery.footman._context import parallel as parallel
    from livery.footman._context import passthrough as passthrough
    from livery.footman._context import prog as prog
    from livery.footman._context import progress as progress
    from livery.footman._context import project_root as project_root
    from livery.footman._context import prompt as prompt
    from livery.footman._context import real_stderr as real_stderr
    from livery.footman._context import run as run
    from livery.footman._context import section as section
    from livery.footman._context import select as select
    from livery.footman._context import stream as stream
    from livery.footman._context import track as track
    from livery.footman._context import tty as tty
    from livery.footman._context import use_context as use_context
    from livery.footman._context import user_tasks_file as user_tasks_file
    from livery.footman._describe import styled as styled
    from livery.footman._describe import wants_color as wants_color
    from livery.footman._entries import installed_entry_points as installed_entry_points
    from livery.footman._entries import rescan_entry_points as rescan_entry_points
    from livery.footman._executor import handing_off as handing_off
    from livery.footman._fetch import FetchError as FetchError
    from livery.footman._fetch import fetch as fetch
    from livery.footman._globals import Lane as Lane
    from livery.footman._globals import console_lane as console_lane
    from livery.footman._globals import cwd_lane as cwd_lane
    from livery.footman._globals import make_lane as lane
    from livery.footman._host import Host as Host
    from livery.footman._host import host as host
    from livery.footman._invocation import Invocation as Invocation
    from livery.footman._params import Arg as Arg
    from livery.footman._params import Exists as Exists
    from livery.footman._params import Forward as Forward
    from livery.footman._params import Hidden as Hidden
    from livery.footman._params import IsDir as IsDir
    from livery.footman._params import IsFile as IsFile
    from livery.footman._params import Many as Many
    from livery.footman._params import NoSplit as NoSplit
    from livery.footman._params import Secret as Secret
    from livery.footman._params import Stdin as Stdin
    from livery.footman._params import Stdout as Stdout
    from livery.footman._params import ask as ask
    from livery.footman._params import between as between
    from livery.footman._params import check as check
    from livery.footman._params import default as default
    from livery.footman._params import doc as doc
    from livery.footman._params import env as env
    from livery.footman._params import exists as exists
    from livery.footman._params import forward as forward
    from livery.footman._params import hidden as hidden
    from livery.footman._params import isdir as isdir
    from livery.footman._params import isfile as isfile
    from livery.footman._params import matching as matching
    from livery.footman._params import nosplit as nosplit
    from livery.footman._params import stdin as stdin
    from livery.footman._params import stdout as stdout
    from livery.footman._params import suggest as suggest
    from livery.footman._paths import builtins as builtins
    from livery.footman._paths import directory_variable as directory_variable
    from livery.footman._paths import tasks_file_name as tasks_file_name
    from livery.footman._registry import GlobalOption as GlobalOption
    from livery.footman._registry import Group as Group
    from livery.footman._registry import Tasks as Tasks
    from livery.footman._registry import TaskView as TaskView
    from livery.footman._registry import capture as capture
    from livery.footman._registry import config_section as config_section
    from livery.footman._registry import expose as expose
    from livery.footman._registry import group as group
    from livery.footman._registry import post_task as post_task
    from livery.footman._registry import post_tasks as post_tasks
    from livery.footman._registry import pre_bind as pre_bind
    from livery.footman._registry import pre_record as pre_record
    from livery.footman._registry import pre_reexec as pre_reexec
    from livery.footman._registry import pre_task as pre_task
    from livery.footman._registry import pre_tasks as pre_tasks
    from livery.footman._registry import requires as requires
    from livery.footman._registry import requires_dep as requires_dep
    from livery.footman._registry import requires_env as requires_env
    from livery.footman._registry import requires_tool as requires_tool
    from livery.footman._registry import root as root_group
    from livery.footman._registry import task as task
    from livery.footman._registry import wrap_bind as wrap_bind
    from livery.footman._registry import wrap_task as wrap_task
    from livery.footman._step import step as step
    from livery.footman.testing import Runner as Runner
    from livery.footman.testing import recording as recording

__version__ = "0.58.0"

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
    "CommandView",
    "Context",
    "Exists",
    "Failed",
    "FetchError",
    "Forward",
    "GlobalOption",
    "Group",
    "Hidden",
    "Host",
    "Invocation",
    "IsDir",
    "IsFile",
    "Lane",
    "Many",
    "NoSplit",
    "Result",
    "ResultView",
    "RunFailed",
    "RunTimeout",
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
    "builtins",
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
    "current",
    "cwd",
    "cwd_lane",
    "data_dir",
    "default",
    "directory_variable",
    "dist",
    "doc",
    "docs",
    "docstrings",
    "env",
    "exists",
    "expose",
    "fail",
    "fetch",
    "forward",
    "given",
    "group",
    "handing_off",
    "hidden",
    "host",
    "include",
    "inherited",
    "installed_entry_points",
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
    "pre_reexec",
    "pre_task",
    "pre_tasks",
    "profile",
    "prog",
    "progress",
    "project_builtins",
    "project_root",
    "prompt",
    "real_stderr",
    "recording",
    "requires",
    "requires_dep",
    "requires_env",
    "requires_tool",
    "rescan_entry_points",
    "root_group",
    "run",
    "section",
    "select",
    "stdin",
    "stdout",
    "step",
    "stream",
    "styled",
    "suggest",
    "task",
    "tasks_file_name",
    "testing",
    "track",
    "tty",
    "use_context",
    "user_tasks_file",
    "wants_color",
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
                reconfigure(errors="replace", line_buffering=True)
    if tasks_file is not None and not any(
        a.startswith(("-f=", "--tasks-file=")) for a in argv
    ):
        argv = [f"--tasks-file={tasks_file}", *argv]
    # Past the completion hot path, this process exists to run one command and
    # exit, so it should not spend its startup collecting garbage it has not
    # made yet. `_globals` turns the collector back on — with everything
    # loaded by then frozen — at the last moment before task bodies run.
    from livery.footman import _globals
    from livery.footman._application import App

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
    # Lazy re-export: `from livery.footman import task, group` works without
    # paying the registry import on a bare import (the completion hot path).
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
        "pre_reexec",
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
        from livery.footman import _registry as registry

        return getattr(registry, name)
    if name in ("installed_entry_points", "rescan_entry_points"):
        from livery.footman import _entries

        return getattr(_entries, name)
    if name == "handing_off":
        from livery.footman import _executor

        return _executor.handing_off
    if name == "step":
        from livery.footman import _step

        return _step.step
    if name in ("Lane", "cwd_lane", "console_lane", "lane"):
        from livery.footman import _globals

        return _globals.make_lane if name == "lane" else getattr(_globals, name)
    if name in ("Runner", "recording"):
        from livery.footman import testing

        return getattr(testing, name)
    if name in ("docs", "profile", "testing"):
        import importlib

        return importlib.import_module(f"livery.footman.{name}")
    if name == "root_group":
        from livery.footman import _registry as registry

        return registry.root
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
        from livery.footman import _compose as compose

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
        from livery.footman import _params as params

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
        "RunTimeout",
        "Failed",
        "current",
        "real_stderr",
        "TimedOut",
        "fail",
        "use_context",
        "section",
        "stream",
        "mark",
        "Section",
        "Stream",
    ):
        from livery.footman import _context as context

        return getattr(context, name)
    if name in ("App", "Brand"):
        from livery.footman import _application as app

        return getattr(app, name)
    if name == "CommandView":
        from livery.footman import _context as context

        return context.CommandView
    if name in ("Host", "host"):
        from livery.footman import _host

        return getattr(_host, name)
    if name in ("builtins", "directory_variable", "tasks_file_name"):
        from livery.footman import _paths

        return getattr(_paths, name)
    if name == "project_builtins":
        from livery.footman import _config

        return _config.project_builtins
    if name in ("styled", "wants_color"):
        from livery.footman import _describe

        return getattr(_describe, name)
    if name == "Invocation":
        from livery.footman import _invocation as invocation

        return invocation.Invocation
    raise AttributeError(f"module 'livery.footman' has no attribute {name!r}")
