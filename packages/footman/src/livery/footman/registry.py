"""The task registry: the `@task` / `group()` decorator surface.

Users build their command tree in a tasks file (`tasks.py` by default):

```python
from livery.footman import task, group

@task
def lint(fix: bool = False):
    "Run ruff over the project."

docs = group("docs", help="Documentation")

@docs.task
def serve(port: int = 8000):
    "Serve the docs locally."
```

A module of functions becomes a flat set of commands; each `group` opens
a nested command group. Command names are the function/group name with
underscores turned into hyphens (`add_word` -> `add-word`).

This module holds only the tree structure. Turning it into the manifest (which
pays the cost of `inspect`) lives in `footman._manifest`, and the
completion hot path never imports either.
"""

from __future__ import annotations

import contextlib
import functools
import os
import threading
from collections.abc import Callable, Generator, Iterator, Sequence
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Final,
    ParamSpec,
    Protocol,
    TypedDict,
    TypeVar,
    Unpack,
    cast,
    overload,
)

if TYPE_CHECKING:
    from livery.footman._globals import Lane
    from livery.footman.context import ResultView

Task = Callable[..., Any]
Hook = Callable[..., object]
"""A lifecycle hook. The moments differ by arity: `pre_tasks(inv)`,
`pre_task(inv, task)`, `post_task(inv, task, result)`."""

# The hook kinds a provider module can contribute alongside (or instead of)
# tasks. Each kind is one bucket in `Group.contributions`, and the whole
# carriage — `capture()`/`reset()` here, `_fork`/`_pull` in compose,
# `load_tree`'s collection in discover — walks the dict generically, so a
# future hook kind is one entry here plus its own run semantics.
CONTRIBUTION_KINDS: tuple[str, ...] = (
    "pre_tasks",
    "pre_bind",
    "pre_task",
    "post_task",
    "post_tasks",
    "globals",
)

_CONTRIBUTED = "_footman_contributed"
"""The (kind, item) pairs a hook decorator registered, written on the
decorated fn. A module imported outside any proper load has spent its
decorators against a registry that is gone, but the decorated functions
still sit in the module's namespace — this marker is what lets
`compose._reconstruct` rebuild the contributions from there, wrapper
objects included."""


def _note_contribution(fn: Any, kind: str, item: Any) -> None:
    """Record on *fn* that decorating it registered *item* under *kind*.

    The ledger is faithful, not deduplicated: decorating twice registers
    twice, and the ledger says so. A callable that refuses attributes
    simply never reconstructs."""
    with contextlib.suppress(AttributeError, TypeError):
        fn.__dict__.setdefault(_CONTRIBUTED, []).append((kind, item))


class GlobalOption:
    """A plugin's own global option — `--env-file=PATH` beside `--jobs=N`.

    Constructing one **is** registering it: a module-level singleton in the
    provider, stamped with the module that defined it, riding the same
    carriage as lifecycle hooks — so it reaches a run only when its owner is
    mounted, and an unmounted owner's option is an unknown option, taught.
    Long-form only, and the value is `=`-attached like every option's.

        ENV_FILE = GlobalOption("env-file", Path, help="load this .env file")

    A `bool` annotation makes it a flag; anything else takes a value, coerced
    and validated through the same pipeline as a task parameter — `Literal`
    choices, `Path` file completion, `Annotated[..., suggest(...)]` all work,
    because the manifest describes it with the same machinery. Read it
    anywhere in-run as `OPT.value` (frozen after parse); cross-plugin use is
    an ordinary import of the singleton.

    A mention with no value is legal — `--profile` beside `--profile=out.json`
    — and carries presence rather than a value: `.value` is then whatever the
    option would have had anyway, and `.given` says someone asked. That is what
    lets one declared value cover three outcomes (off, the default file, a named
    file) where a second declared value used to be needed.

    `config=True` gives the option a project-config rung — the one ladder,
    **CLI > `env()` > config > `default(fn)` > declared** — reading the key
    named like the option from this provider's own section under the brand
    table's reserved `plugins.` child: `[tool.footman.plugins.acme-devkit]`
    for the `acme.devkit` entry point (the dot becomes a hyphen, because
    TOML's dot is its nesting operator). `config="key"` names the key
    instead, for a flag renamed around a collision — flag and key are
    different namespaces, and only the flag's is shared. The section is the
    entry point's, de-dotted; `footman.config_section("...")` names it
    explicitly where there is nothing to derive from (an `include()`d
    module) or the derivation reads wrong.
    """

    __slots__ = (
        "_frozen",
        "_given",
        "_mounted",
        "_reads",
        "_section",
        "_value",
        "annotation",
        "config",
        "default",
        "help",
        "name",
        "owner",
    )

    name: str
    annotation: Any
    default: Any
    config: bool | str
    help: str
    owner: str

    # Where a config-backed option's key lives: core's own declarations read
    # the brand table directly ("root"); a plugin's read its section under
    # the reserved `plugins.` child.
    _config_scope = "plugins"

    def __init__(
        self,
        name: str,
        annotation: Any = bool,
        *,
        default: Any = None,
        config: bool | str = False,
        help: str = "",  # matches the manifest's vocabulary
    ) -> None:
        if name.startswith("-"):
            raise RegistrationError(
                f"GlobalOption({name!r}): give the bare name — the dashes are "
                f"the grammar's (and plugin globals are long-form only)"
            )
        self.name = cli_name(name)
        self.annotation = annotation
        self.default = default if annotation is not bool else bool(default)
        self.config = config
        self.help = help
        # Provenance the mount writes down (the entry-point identity), and
        # the config section resolved from it at discovery — the derivation
        # `config=` rides on.
        self._mounted: str | None = None
        self._section: str | None = None
        # The DEFINING module, never the importing capture: what a collision
        # names, what pairing and provenance key on.
        import sys as _sys

        frame = _sys._getframe(1)
        self.owner = frame.f_globals.get("__name__", "<unknown>")
        self._value: Any = _UNBOUND
        self._given = False
        self._frozen = False
        self._reads: set[str] = set()
        self._register()

    def _register(self) -> None:
        # The plugin carriage: constructing IS registering, so the option
        # reaches a run exactly when its owner is mounted. Core's own
        # declarations (`_split._CoreOption`) override this to stay off it —
        # they are not contributions, they are the runner, and the collision
        # law reads them from the derived grammar table instead.
        root.contributions["globals"].append(self)

    @property
    def value(self) -> Any:
        """The parsed value for this run — the flag's presence, the option's
        coerced value, or the default when the line didn't carry it. Frozen
        after parse; reading it outside a run is a taught error, because
        there is no invocation to have carried it."""
        if not self._frozen:
            raise RuntimeError(
                f"--{self.name} has no value here — a global option is parsed "
                f"from the command line, so read {type(self).__name__}.value "
                f"inside a task or lifecycle hook, during a run"
            )
        self._mark_read()
        return self._value

    @property
    def given(self) -> bool:
        """Whether this option was named on the command line, with or without a
        value — the twin of `value`, and the half a value cannot express.

        ```python
        PROFILE = GlobalOption("profile", Path, default=Path("fm-profile.json"))
        if PROFILE.given:            # `--profile` writes the default file,
            write_trace(PROFILE.value)   # no `--profile` writes nothing
        ```

        Three outcomes from one declared value: absent, named, named with a
        value. `value` answers *what*, `given` answers *whether anyone asked* —
        and an `env` fallback fills the first without ever touching the second,
        so a caller who wants the environment alone to count can say so, and one
        who does not can say that instead.
        """
        if not self._frozen:
            raise RuntimeError(
                f"--{self.name} has no answer here — a global option is parsed "
                f"from the command line, so read {type(self).__name__}.given "
                f"inside a task or lifecycle hook, during a run"
            )
        self._mark_read()
        return self._given

    def _mark_read(self) -> None:
        # Read-marking for the notes lane: an in-task read is attributed to
        # the task; a task that never declared `uses=` gets a taught note
        # (once), because help and provenance can only describe what is said.
        from livery.footman import context

        ctx = context._current.get()
        if ctx is None or not ctx.in_task or ctx.fn is None:
            return
        self._reads.add(ctx.task or "?")
        if any(u is self for u in task_uses(ctx.fn)):
            return
        from livery.footman import _globals

        _globals._note(
            f"global-read:{self.name}",
            f"task {ctx.task or '?'} reads --{self.name} without declaring "
            f"it — say @task(uses=[...]) so its help and provenance show it",
        )

    def _as_parameter(self) -> Any:
        """This option as the synthetic keyword-only parameter the task-side
        machinery reasons about — the ONE construction, so the manifest's
        description (`_global_spec`) and the binder's absent-option ladder
        (`_absent_global`) can never drift apart; both used to hand-build
        their own copy."""
        import inspect

        return inspect.Parameter(
            self.name.replace("-", "_"),
            inspect.Parameter.KEYWORD_ONLY,
            annotation=self.annotation,
            default=self.default,
        )

    def __repr__(self) -> str:
        state = repr(self._value) if self._frozen else "unbound"
        return f"<GlobalOption --{self.name} from {self.owner}: {state}>"


_UNBOUND = object()

_USES = "_footman_uses"

# The mount writes this in place of an identity when one singleton is reached
# through two *different* mounts: its derived section would depend on mount
# order, so a config-backed option carrying it must name a section instead.
_MANY_MOUNTS = "<multiple mounts>"

# Module name -> the config section that module's options claim, declared by
# `config_section()` beside the options themselves. Keyed the way `owner` is
# stamped, so resolution needs no second identity.
_CONFIG_SECTIONS: dict[str, str] = {}


def config_section(name: str) -> None:
    """Name this provider module's config section — the `<name>` of
    `[tool.footman.plugins.<name>]` — instead of the entry-point derivation.

    Module-level, beside the module's `GlobalOption`s. For an `include()`d
    module (no entry point to derive from), or a derivation that reads
    wrong or ugly. The name is claimed at discovery, so two providers
    naming one section refuse loudly, never resolve by order."""
    import sys as _sys

    module = _sys._getframe(1).f_globals.get("__name__", "<unknown>")
    _CONFIG_SECTIONS[module] = cli_name(name)


def _note_if_ephemeral(group: Group, key: str, previous: Task | None) -> None:
    """Record a task that was defined *while a run is in flight*, so the run
    can take it back out again.

    A `@task` inside a task body is ordinary Python — the decorator runs when
    the body does — and the task it makes is genuinely callable. What it must
    not do is outlive the run: the manifest was written before any of this
    happened, so a task nobody can see in a listing would go on shadowing the
    tree for every later run in the process. Registered, used, swept.
    """
    from livery.footman import _futures

    run = _futures.active_session()
    if run is not None:
        run.ephemeral.append((group, key, previous))


def task_uses(fn: Task) -> tuple[GlobalOption, ...]:
    """The global options *fn* declared it reads — `@task(uses=[OPT])`."""
    return tuple(getattr(fn, _USES, ()))


def validate_global_options(options: Sequence[GlobalOption]) -> str | None:
    """The collision law for plugin globals, applied to the merged tree.

    A name owned by footman itself is refused naming footman; two plugins
    claiming one name are refused naming both. A bool option also claims its
    `--no-x` spelling, so a literal `no-x` beside a bool `x` is a clash too —
    loud at discovery, never order-dependent at parse. The same singleton
    reached through two mounts is one option, not a clash. Returns the
    teaching message, or `None` when the set is sound."""
    from livery.footman._split import _GLOBAL_KIND

    seen: dict[str, tuple[GlobalOption, str]] = {}
    for opt in options:
        spellings = [f"--{opt.name}"]
        if opt.annotation is bool:
            spellings.append(f"--no-{opt.name}")
        for flag in spellings:
            derived = (
                ""
                if flag == f"--{opt.name}"
                else f" (the off spelling of --{opt.name})"
            )
            if flag in _GLOBAL_KIND:
                return (
                    f"{flag}{derived} (from {opt.owner}) collides with "
                    f"footman's own global option — plugin globals need "
                    f"their own names"
                )
            other = seen.get(flag)
            if other is not None and other[0] is not opt:
                return (
                    f"{flag} is claimed by both {other[0].owner}{other[1]} "
                    f"and {opt.owner}{derived} — two plugins, one spelling; "
                    f"rename one, or mount only one of them"
                )
            seen[flag] = (opt, derived)
    return _resolve_config_sections(options)


def _resolve_config_sections(options: Sequence[GlobalOption]) -> str | None:
    """Give every config-backed plugin option its section, or the teaching
    message for one that cannot have a single answer.

    Explicit `config_section(...)` wins; else the entry-point identity the
    mount stamped, de-dotted (`acme.devkit` → `acme-devkit`). Nothing to
    derive from, or two mounts disagreeing, is a refusal naming the remedy —
    loud at discovery, never resolved by mount order. Two providers claiming
    one section refuse naming both, the flag-collision law's habit."""
    claimed: dict[str, GlobalOption] = {}
    for opt in options:
        if not opt.config or opt._config_scope != "plugins":
            continue
        section = _CONFIG_SECTIONS.get(opt.owner)
        if section is None:
            if opt._mounted is None:
                return (
                    f"--{opt.name} (from {opt.owner}) declares config= but "
                    f"nothing names its section — no entry point to derive "
                    f"one from; declare footman.config_section('...') in the "
                    f"defining module"
                )
            if opt._mounted == _MANY_MOUNTS:
                return (
                    f"--{opt.name} declares config= and is reached through "
                    f"more than one mount, so its derived section would "
                    f"depend on mount order — declare "
                    f"footman.config_section('...') in the defining module"
                )
            section = opt._mounted.replace(".", "-")
        other = claimed.get(section)
        if other is not None and other.owner != opt.owner:
            return (
                f"config section 'plugins.{section}' is claimed by both "
                f"{other.owner} and {opt.owner} — two providers, one "
                f"section; footman.config_section('...') renames either"
            )
        claimed[section] = opt
        opt._section = section
    return None


def release_global_options(options: Sequence[GlobalOption]) -> None:
    """Unfreeze after the run: an outside-a-run read goes back to teaching."""
    for opt in options:
        opt._frozen = False
        opt._value = _UNBOUND
        opt._reads.clear()


def orphan_global_options(root: Group) -> list[str]:
    """Warnings for globals nothing is wired to read, one per orphan.

    An option is consumed through a lifecycle hook or a declaring task.
    When its owner contributes no hook and no task in the tree says
    `uses=[...]`, nothing can be seen to read it — a warning, never a
    refusal: an undeclared `.value` read still works (and is noted at run
    time), and a hook in *another* module may read an imported singleton.
    """
    hook_owners = {
        getattr(hook, "__module__", None)
        for kind in CONTRIBUTION_KINDS
        if kind != "globals"
        for hook in root.contributions.get(kind, ())
    }
    declared: set[int] = set()

    def _walk(group: Group) -> None:
        for fn in group.tasks.values():
            for opt in task_uses(fn):
                declared.add(id(opt))
        for sub in group.groups.values():
            _walk(sub)

    _walk(root)
    out: list[str] = []
    seen: list[GlobalOption] = []
    for opt in root.contributions.get("globals", ()):
        if any(o is opt for o in seen):
            continue  # the same singleton mounted twice is one option
        seen.append(opt)
        if opt.owner in hook_owners or id(opt) in declared:
            continue
        out.append(
            f"--{opt.name} (from {opt.owner}) has no reader in sight — "
            f"{opt.owner} contributes no lifecycle hook, and no task "
            f"declares it with @task(uses=[...])"
        )
    return out


# A task stays a plain function; its metadata rides as `_footman_*` attributes.
# These name every key in one place, so the strings appear once and the read
# accessors below are the one way the rest of the framework touches them.
_PRE = "_footman_pre"
_POST = "_footman_post"
_KEEP_GOING = "_footman_keep_going"
_ATOMIC = "_footman_atomic"
_TIMEOUT = "_footman_timeout"
_RETRIES = "_footman_retries"
_INFINITE = "_footman_infinite"
_SHARED = "_footman_shared"
_EXPOSE = "_footman_expose"
"""**Where** a task is offered: everywhere, only in a project, only outside.

Most of what a package ships means nothing without a checkout — `deploy`
with no project is not a shorter `deploy`, it is a lie that exits 0 — and
some of what a runner ships means nothing *with* one: creating a project
from inside a project is the same kind of lie the other way round.

Three answers, because built-in tasks are part of the cascade and so are
reachable from both sides. Whichever side a task does not belong on, it
is not listed, not completed, and refused *by name* when asked for —
never a "no task named" 404, because the task does exist, it just does
not belong here.

`None` inherits the enclosing group, exactly as `hidden` does, so one line
covers a subtree and a child can still say otherwise. The rungs differ
only in their **default**, and each default is that rung's own promise: a
package's tasks are `project_only` until one says otherwise (`seal_expose`),
while a person's own tasks file rides everywhere.
"""

EXPOSE_VALUES = ("always", "global_only", "project_only")
"""What `expose=` accepts. `global_only` means outside a project only."""


_HIDDEN = "_footman_hidden"
_LANES = "_footman_lanes"
_INTERACTIVE = "_footman_interactive"
_PROGRESS = "_footman_progress"
_CONFIRM = "_footman_confirm"
_MOUNTED = "_footman_mounted_from"  # provenance: the plugin identity a mount stamped
_CWD = "_footman_cwd"
_REL = "_footman_rel"
_SERIAL = "_footman_serial"
_EXCLUSIVE = "_footman_exclusive"

# The cwd policy tokens — where a task's working directory roots. Anything
# else passed as `cwd=` must be an absolute path (a relative one is a taught
# error pointing at `rel=`, so base-vs-suffix stays unambiguous).
CWD_TOKENS = ("root", "taskfile", "asinvoked", "unmanaged")


def validate_lanes(value: Any) -> tuple[Any, ...]:
    """Lanes are bindings, never strings: each entry must be a `Lane` handle.

    Checked where lanes are declared, so a string dies as a taught error at
    the declaration site instead of a raw crash in the arbiter mid-run."""
    from livery.footman._globals import Lane

    lanes = tuple(value)
    for entry in lanes:
        if not isinstance(entry, Lane):
            raise TypeError(
                f"lanes= takes Lane handles, not {type(entry).__name__} "
                f"({entry!r}). Declare the resource once — db = "
                f"footman.lane('db', reason='…') — and pass the handle: "
                f"lanes=(db,). Handles are what make a typo an undefined "
                f"name instead of a second, silently distinct lane."
            )
    return lanes


def _validate_cwd(value: str | Path) -> str | Path:
    """A cwd policy value: one of `CWD_TOKENS`, or an absolute path."""
    if isinstance(value, str) and value in CWD_TOKENS:
        return value
    path = Path(value)
    if not path.is_absolute():
        raise TypeError(
            f"cwd={str(value)!r} is relative — cwd takes a policy token "
            f"({', '.join(CWD_TOKENS)}) or an absolute path; a relative "
            f"suffix goes in rel=…"
        )
    return path


def _validate_rel(value: str | Path) -> str:
    """A rel suffix: a relative path, appended to the resolved cwd base.

    Anchored counts as absolute: on Windows a driveless-rooted path
    (`/x` — `is_absolute()` False, `anchor` set) would silently replace
    the base's whole path portion when joined, the opposite of a suffix."""
    rel_path = Path(value)
    if rel_path.is_absolute() or rel_path.anchor:
        raise TypeError(
            f"rel={str(value)!r} is absolute — rel is a suffix appended to the "
            f"resolved cwd base; an absolute directory goes in cwd=…"
        )
    return str(value)


_CHECKS = "_footman_checks"
_DEFAULT_GROUP = "_footman_default_group"
_DEFAULT_FANOUT = "_footman_default_fanout"


class RegistrationError(ValueError):
    """A task or group name collided during registration.

    Subclasses `ValueError` so existing `except ValueError` handlers keep
    working; the app layer matches this type to report a duplicate name as a
    user error rather than an import failure.
    """


def task_name(fn: Any) -> str:
    """The callable's `__name__`. Every task-shaped object carries one (a
    function, a `_TaskFn` handle, an `_Opted` proxy), but the plain
    `Callable` type does not — reading through `Any` keeps the access legal
    under every checker without a suppression."""
    name: str = fn.__name__
    return name


def _checked_expose(value: str) -> str:
    """*value* if it is one of `EXPOSE_VALUES`, else a taught refusal.

    A misspelling here would otherwise read as silence — the task would
    inherit its group's answer and appear somewhere its author meant to
    keep it out of, which is the failure this axis exists to prevent.
    """
    if value not in EXPOSE_VALUES:
        raise RegistrationError(
            f"expose={value!r} is not one of {', '.join(EXPOSE_VALUES)}"
        )
    return value


def expose(where: str) -> Callable[[_F], _F]:
    """Say where a task is offered: `@expose("global_only")`.

    The decorator spelling of `@task(expose=…)`, for authors who prefer the
    gates above the signature — it sets the same one answer, so stacking it
    twice is a contradiction rather than an addition, unlike `@requires_*`.

    ```python
    @task
    @expose("global_only")
    def new(name: str): ...
    ```
    """
    checked = _checked_expose(where)

    def mark(fn: _F) -> _F:
        setattr(fn, _EXPOSE, checked)
        return fn

    return mark


def cli_name(name: str) -> str:
    """Normalise a Python identifier to its command-line spelling.

    A *trailing* underscore is Python's keyword/name-escape idiom (`sync_` to
    avoid shadowing `sync`, `import_`, `class_`); it is stripped, so the flag
    reads `--sync`, not `--sync-`. This is the one place identifiers become CLI
    tokens for task names, group names, *and* parameter flags. (toolroom's
    handles carry their own copy of this rule; parity is held by its
    conformance suite against released footman, not by shared code.)
    """
    return name.rstrip("_").replace("_", "-")


def _isgeneratorfunction(fn: Hook) -> bool:
    """Whether *fn* is a generator function, through any wrapping."""
    import inspect

    return inspect.isgeneratorfunction(inspect.unwrap(fn))


def _check_hook_arity(kind: str, fn: Hook, wanted: int) -> None:
    """Refuse a hook whose signature cannot be called, at registration.

    The lifecycle names its moments, and the moments differ by *arity* —
    `pre_tasks(inv)` against a later `post_task(inv, task, result)`. Checked
    here so a typo is a taught error naming the hook, not a `TypeError` from
    inside the framework at the first task an hour into a run.
    """
    import inspect

    try:
        params = list(inspect.signature(fn).parameters.values())
    except (TypeError, ValueError):
        return  # not introspectable (a builtin, a C callable): let it try
    if any(p.kind is p.VAR_POSITIONAL for p in params):
        return
    positional = [
        p
        for p in params
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        and p.default is p.empty
    ]
    if len(positional) != wanted:
        name = getattr(fn, "__name__", repr(fn))
        shape = "inv" if wanted == 1 else ", ".join(["inv", "task", "result"][:wanted])
        raise RegistrationError(
            f"@{kind} {name!r} takes {len(positional)} argument(s); {kind} is "
            f"handed {wanted}: def {name}({shape})"
        )


def _empty_body(fn: object) -> bool:
    """True when *fn*'s body is only a docstring and/or `pass`.

    This is the signal that a `@group.default` fans out the group's own tasks
    rather than running a body of its own. Source that can't be read (a C
    function, a REPL definition) reads as *not* empty — a body we can't see is
    treated as one we must run.
    """
    import ast
    import textwrap

    try:
        src = textwrap.dedent(task_source(fn))
        mod = ast.parse(src)
    except (OSError, TypeError, SyntaxError):
        return False
    func = mod.body[0] if mod.body else None
    if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
        return False
    stmts = func.body
    if (
        stmts
        and isinstance(stmts[0], ast.Expr)
        and isinstance(stmts[0].value, ast.Constant)
        and isinstance(stmts[0].value.value, str)
    ):
        stmts = stmts[1:]  # drop the docstring

    # `...` counts as empty exactly like `pass`: it is the stub idiom this
    # codebase's own examples teach, and a `@group.default` written
    # `def lint_all(): ...` used to read as a real body — the group then
    # silently ran nothing, which is the one thing a default must not do.
    def inert(stmt: ast.stmt) -> bool:
        return isinstance(stmt, ast.Pass) or (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and stmt.value.value is ...
        )

    return all(inert(s) for s in stmts)


# The orchestration options `.opts()` can override, mapped to their task
# attribute. These are *policy* (how a task runs), kept separate from the task's
# own parameters (the *work*) — which is why they ride in `.opts()` rather than
# the call, the same policy-beside-the-call split toolroom's handles draw.
_OPTS_ATTRS = {
    "keep_going": _KEEP_GOING,
    "atomic": _ATOMIC,
    "timeout": _TIMEOUT,
    "retries": _RETRIES,
    "interactive": _INTERACTIVE,
    "progress": _PROGRESS,
    "confirm": _CONFIRM,
    "infinite": _INFINITE,
    "shared": _SHARED,
    "cwd": _CWD,
    "rel": _REL,
    "serial": _SERIAL,
    "exclusive": _EXCLUSIVE,
    "lanes": _LANES,
}


class TaskOpts(TypedDict, total=False):
    """The orchestration options `.opts()` and `TaskView.set_opts` accept —
    the policy of *how* a task runs, as one closed, typed set. Spelled as a
    TypedDict so option names complete in an editor and a wrong name or type
    is a static error at the call site; `test_task_opts_matches_opts_attrs`
    holds these keys to `_OPTS_ATTRS`. `None` clears a tri-state or a
    declared `cwd`/`rel` policy for that use."""

    keep_going: bool | None
    atomic: bool
    timeout: float | None
    retries: int
    interactive: bool
    progress: bool
    confirm: str
    infinite: bool
    shared: bool | None
    cwd: str | Path | None
    rel: str | Path | None
    lanes: tuple[Lane, ...]
    serial: bool
    exclusive: bool


def work_key(
    fn: Task, *, include_shared: bool = False
) -> tuple[int, frozenset[tuple[str, Any]]]:
    """The one `(id, frozenset)` identity every dedup reads — the futures
    memo and the DAG's node key were two spellings of this same shape.

    A different policy is a genuinely different invocation, so a bare
    reference and an `.opts(atomic=True)` one are two pieces of work and
    both run; an empty `.opts()` collapses onto the bare task by
    construction. The default *excludes* the `shared` override: it says "do
    not reuse an answer", not "this is different work", and folding it in
    would put an unshared request in a bucket of its own where no later
    request could ever find its result. The DAG's key (*include_shared*)
    keeps it in — there, a differently-shared reference is a distinct node.
    """
    if isinstance(fn, _Opted):
        base = object.__getattribute__(fn, "_opted_base")
        overrides = object.__getattribute__(fn, "_opted_overrides")
        work = (
            overrides
            if include_shared
            else {k: v for k, v in overrides.items() if k != _SHARED}
        )
        return (id(base), frozenset(work.items()))
    return (id(fn), frozenset())


def _opts_overrides(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Validate `.opts()` kwargs and map them to their task attributes."""
    unknown = sorted(set(kwargs) - set(_OPTS_ATTRS))
    if unknown:
        valid = ", ".join(sorted(_OPTS_ATTRS))
        raise TypeError(
            f".opts() got unknown option(s) {unknown}; valid options are {valid}. "
            f"A task's own parameters go in the call — `t.opts(atomic=True)(x=1)` — "
            f"not in .opts()."
        )
    if kwargs.get("lanes") is not None:
        kwargs["lanes"] = validate_lanes(kwargs["lanes"])
    for name, value in kwargs.items():
        # Override values key the DAG's dedup identity, so they must be hashable.
        # Every real policy value is (bool / str / None); this turns a stray
        # unhashable one into a clear error here, not a cryptic crash at DAG build.
        try:
            hash(value)
        except TypeError:
            raise TypeError(
                f".opts({name}=…) needs a hashable value — options key the run's "
                f"deduplication — but got an unhashable {type(value).__name__}"
            ) from None
    # None is "no opinion" — the value a caller computing an override passes
    # (`cwd=None if inline else build_dir`), and the way to clear a declared
    # one for this use. Readers all `getattr(fn, _CWD, None)`, so a stored None
    # already reads as unset; only the validators need to let it through.
    if kwargs.get("cwd") is not None:
        kwargs["cwd"] = _validate_cwd(kwargs["cwd"])
    if kwargs.get("rel") is not None:
        kwargs["rel"] = _validate_rel(kwargs["rel"])
    if kwargs.get("cwd") == "unmanaged" and kwargs.get("rel"):
        raise TypeError(
            "rel=… needs a managed base and cwd='unmanaged' has none — "
            "use cwd='asinvoked' for a pinned launch-directory base"
        )
    return {_OPTS_ATTRS[k]: v for k, v in kwargs.items()}


class _Opted:
    """A task (or runnable group) reference carrying per-use option overrides.

    `lint.opts(atomic=True)` reads as a task everywhere a bare task does — a
    `pre=`/`post=` target, a body call — but reports the overridden `_footman_*`
    options *for this use*, leaving the registered task untouched. It proxies the
    base transparently: same signature (via `__wrapped__`), same name, same call;
    only the overridden options differ. This is footman's policy-vs-work split —
    the options ride beside the call, not inside its argument list — the same
    split toolroom's handles draw with their `.opts()`.
    """

    _opted_base: Task | Group
    _opted_overrides: dict[str, Any]

    def __init__(self, base: Task | Group, overrides: dict[str, Any]) -> None:
        object.__setattr__(self, "_opted_base", base)
        object.__setattr__(self, "_opted_overrides", overrides)
        object.__setattr__(self, "__wrapped__", base)  # inspect.signature follows

    def __getattr__(self, name: str) -> Any:
        overrides = object.__getattribute__(self, "_opted_overrides")
        if name in overrides:
            return overrides[name]
        return getattr(object.__getattribute__(self, "_opted_base"), name)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        # Route as *this reference*, not the bare task: the overrides are part
        # of the request (`build.opts(shared=False)()` asks for its own run).
        from livery.footman import _futures

        return _futures.call(self, args, kwargs)

    def _plain_call(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        """A direct call with no run around it: today's behaviour, kept for the
        outside-a-run path where there is no task boundary to apply policy."""
        base = object.__getattribute__(self, "_opted_base")
        overrides = object.__getattribute__(self, "_opted_overrides")
        if _CWD in overrides or _REL in overrides:
            # A direct body-call must honour a cwd/rel override: resolve it
            # against the caller's context and install it as `ctx.cwd` around
            # the base invocation — a save/restore of the *field*, never a
            # process chdir. The other options are scheduler-read and inert
            # on a plain call. Lazy import: executor imports registry.
            from livery.footman import _executor
            from livery.footman.context import current

            ctx = current()
            saved, saved_unmanaged = ctx.cwd, ctx.cwd_unmanaged
            ctx.cwd = None  # let the override's ladder re-resolve
            ctx.cwd, ctx.cwd_unmanaged = _executor.resolve_cwd(self, ctx)
            try:
                return base(*args, **kwargs)
            finally:
                ctx.cwd, ctx.cwd_unmanaged = saved, saved_unmanaged
        return base(*args, **kwargs)

    def opts(self, **overrides: Unpack[TaskOpts]) -> _Opted:
        base = object.__getattribute__(self, "_opted_base")
        merged = dict(object.__getattribute__(self, "_opted_overrides"))
        merged.update(_opts_overrides(dict(overrides)))  # a later .opts() wins
        return _Opted(base, merged)

    def _dedup_key(self) -> tuple[int, frozenset[tuple[str, Any]]]:
        """This override's identity for DAG deduplication: the base task plus its
        frozen overrides — the same `(id, frozenset)` shape a bare task uses, so
        an empty `.opts()` collapses onto the bare task and identical overrides
        share a node (a shared prerequisite still runs once). A different policy
        is a distinct node, a genuinely different run. Values are hashable by
        construction — `_opts_overrides` rejects an unhashable one at call time.
        The proxy's internals stay behind this method, so the scheduler never
        reaches into them for identity. (`.opts()` never nests — it merges onto
        the base — so `_opted_base` is always the ultimate task, never `_Opted`.)"""
        return work_key(self, include_shared=True)


class _TaskFn:
    """The object `@task` returns — and the very same object it registers.

    A task's metadata rides as `_footman_*` attributes, and the framework keys
    a great deal on the *identity* of the task object: the DAG's dedup key
    (`schedule._dep_key`), the cascade's "am I shadowing myself?" test, the
    provenance and defining-directory stamps. So there is exactly **one**
    handle per decoration, and it is both registered and returned — a second
    handle over the same function would read as a different task.

    It is deliberately thin. Attribute reads fall through to the function, so
    a marker stamped *before* wrapping (`@requires` written below `@task`) is
    still read back; `functools.update_wrapper` gives it the function's name,
    docstring, and `__wrapped__`, so `inspect.signature`, `inspect.getdoc` and
    `inspect.unwrap` all answer about the function. The two `inspect` readers
    that don't follow `__wrapped__` are wrapped once in `task_source` /
    `task_source_file` below, so no call site carries the caveat.
    """

    def __init__(self, fn: Callable[..., Any]) -> None:
        # Copies __name__/__qualname__/__doc__/__module__/__annotations__ and
        # the function's __dict__ (markers stamped below @task), and sets
        # __wrapped__ — the seam every unwrapping reader follows.
        functools.update_wrapper(self, fn)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        # A call from a task body is a piece of the run: it dedups against the
        # DAG, waits on a copy already running, or runs here with a real task
        # boundary. Outside a run it is the plain function call it looks like.
        # Lazy import: `_futures` reaches back into the _executor.
        from livery.footman import _futures

        return _futures.call(self, args, kwargs)

    def __getattr__(self, name: str) -> Any:
        # Reached only for what neither the instance nor the class carries:
        # the function's own dunders (`__code__`, `__globals__`) and any
        # attribute set on it after wrapping. Read `__wrapped__` out of the
        # instance dict directly — going through `self` would recurse here
        # while `__init__` is still running.
        try:
            fn = object.__getattribute__(self, "__dict__")["__wrapped__"]
        except KeyError:
            raise AttributeError(name) from None
        return getattr(fn, name)

    def opts(self, **overrides: Unpack[TaskOpts]) -> _Opted:
        """Per-use option overrides — `lint.opts(keep_going=True)`. The base is
        this handle, so an opted reference and a bare one agree about which
        task they name (the DAG's dedup key reads `id(base)`)."""
        return _Opted(self, _opts_overrides(dict(overrides)))

    def __repr__(self) -> str:
        return f"<task {getattr(self, '__name__', '?')}>"


# `inspect`, told about task handles. Everything else in `inspect` follows
# `__wrapped__` on its own; these two resolve a *file and line*, which a
# handle doesn't have, so they unwrap to the function first. Wrapped here so
# reading a task's source stays a one-liner wherever it's needed.


def task_source(fn: Any) -> str:
    """The source text of the function behind *fn* (decorators included)."""
    import inspect

    return inspect.getsource(inspect.unwrap(fn))


def task_source_hash(fn: Any) -> str | None:
    """A digest of the task's own body, or `None` when its source can't be read.

    Normalised through the AST rather than taken over the text, so reformatting
    and comments do not move it while a real edit does — `ruff format` sweeping
    the repo must not look like every task changed. Decorator lines are part of
    it, so a changed `pre=` shows up.

    It is a **tripwire, not an identity**: it covers this function's own source
    and nothing it calls, so a helper changing underneath leaves it untouched.
    Good for "warn me if the body moved and nobody said so"; wrong as a cache
    key, which is why nothing here uses it as one.
    """
    import ast
    import hashlib
    import textwrap

    try:
        tree = ast.parse(textwrap.dedent(task_source(fn)))
    except (OSError, TypeError, SyntaxError):
        return None
    # No attributes: line and column numbers would make whitespace significant.
    shape = ast.dump(tree, annotate_fields=True, include_attributes=False)
    return hashlib.sha256(shape.encode("utf-8")).hexdigest()


def task_source_file(fn: Any) -> str | None:
    """The file the function behind *fn* is defined in, if it has one."""
    import inspect

    return inspect.getsourcefile(inspect.unwrap(fn))


def _apply_policy(
    fn: Task,
    *,
    pre: Sequence[Task],
    post: Sequence[Task],
    progress: bool,
    infinite: bool,
    shared: bool | None,
    confirm: str,
    interactive: bool,
    keep_going: bool | None,
    atomic: bool,
    timeout: float | None = None,
    retries: int = 0,
    cwd: str | Path = "",
    rel: str | Path = "",
    serial: bool = False,
    exclusive: bool = False,
    hidden: bool | None = None,
    expose: str | None = None,
    lanes: Sequence[Any] = (),
) -> None:
    """Stamp a task's `_footman_*` policy attributes onto *fn*.

    The single writer of the orchestration markers, shared by `@task` and
    `@group.default` so the two option surfaces cannot drift apart. Only the
    non-default markers are set, so `getattr(fn, _…, default)` reads elsewhere
    stay the source of truth; `pre`/`post` are always written (as lists) so a
    later mutation edits a list this task owns.
    """
    setattr(fn, _PRE, list(pre))
    setattr(fn, _POST, list(post))
    if not progress:
        setattr(fn, _PROGRESS, False)
    if infinite:
        setattr(fn, _INFINITE, True)
    if shared is not None:
        setattr(fn, _SHARED, shared)
    if hidden is not None:
        # Tri-state on purpose: unset inherits the enclosing group's answer,
        # so `hidden=False` on a child of a hidden group is a real override
        # rather than indistinguishable from silence.
        setattr(fn, _HIDDEN, hidden)
    if expose is not None:
        # Tri-state for the same reason `hidden` is: unset inherits, so
        # `expose="always"` on a child of a project-scoped group is a real
        # override rather than indistinguishable from silence.
        setattr(fn, _EXPOSE, _checked_expose(expose))
    if confirm:
        setattr(fn, _CONFIRM, confirm)
    if interactive:
        setattr(fn, _INTERACTIVE, True)
    if keep_going is not None:
        setattr(fn, _KEEP_GOING, keep_going)
    if atomic:
        setattr(fn, _ATOMIC, True)
    if retries:
        if not isinstance(retries, int) or isinstance(retries, bool) or retries < 0:
            raise TypeError(
                f"retries={retries!r} — a whole number of EXTRA attempts after "
                f"the first (retries=2 means up to three runs); omit it for none"
            )
        setattr(fn, _RETRIES, retries)
    if timeout is not None:
        if timeout <= 0:
            raise TypeError(
                f"timeout={timeout!r} — a deadline must be a positive number of "
                f"seconds; omit it for no deadline"
            )
        setattr(fn, _TIMEOUT, timeout)
    if cwd == "unmanaged" and rel:
        raise TypeError(
            "rel=… needs a managed base and cwd='unmanaged' has none — "
            "use cwd='asinvoked' for a pinned launch-directory base"
        )
    if cwd:
        setattr(fn, _CWD, _validate_cwd(cwd))
    if rel:
        setattr(fn, _REL, _validate_rel(rel))
    if serial:
        setattr(fn, _SERIAL, True)
    if exclusive:
        setattr(fn, _EXCLUSIVE, True)
    if lanes:
        setattr(fn, _LANES, validate_lanes(lanes))
    _attach_lifecycle(fn)


_PRE_BIND_HOOKS = "_footman_own_pre_bind"
_PRE_TASK_HOOKS = "_footman_own_pre_task"
_POST_TASK_HOOKS = "_footman_own_post_task"


def own_hooks(fn: Any, attr: str) -> list[Callable[..., Any]]:
    """A task's own hooks for one moment, in attachment order."""
    return list(getattr(fn, attr, ()))


def has_own_hooks(fn: Any) -> bool:
    """Whether *fn* carries any handle-attached lifecycle hooks — the executor
    fires them whether or not any plugin is registered."""
    return bool(
        getattr(fn, _PRE_BIND_HOOKS, None)
        or getattr(fn, _PRE_TASK_HOOKS, None)
        or getattr(fn, _POST_TASK_HOOKS, None)
    )


def _attach_lifecycle(fn: Any) -> None:
    """Give a registered task its handle surface: the task's own lifecycle
    moments, attachable where the task lives.

    `@build.pre_task` runs setup that belongs to build; `@build.pre_record`
    is its reviewer; `@build.post_task` watches its sealed record — code
    local to the task, so a rule about one task never lives in a central
    file that lists everybody (hooks with no task knowledge belong to the
    plugin lane instead). Attachment is permanent — it changes what the
    task does for every requester — and each attacher returns the hook
    unchanged, so the decorators stack and the functions stay callable.
    """

    def _attacher(attr: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def attach(hook: Callable[..., Any]) -> Callable[..., Any]:
            setattr(fn, attr, [*getattr(fn, attr, ()), hook])
            return hook

        return attach

    fn.pre_bind = _attacher(_PRE_BIND_HOOKS)
    fn.pre_task = _attacher(_PRE_TASK_HOOKS)
    fn.post_task = _attacher(_POST_TASK_HOOKS)
    fn.pre_record = _attacher(_PRE_RECORD)

    def wrap_task_sugar(gen_fn: Callable[..., Any]) -> Callable[..., Any]:
        """The pair as one generator, per task: the pre half, then
        `result = yield` where the body runs (resumed with the sealed,
        read-only record), then the post half. Sugar lowered into the
        task's own `pre_task` + `post_task` at attachment."""
        name = getattr(gen_fn, "__name__", repr(gen_fn))
        if not _isgeneratorfunction(gen_fn):
            raise RegistrationError(
                f"@{task_name(fn)}.wrap_task {name!r} must be a generator "
                f"function — write `result = yield` where the body runs"
            )
        # A stack, not a slot: a task whose body reaches itself again runs
        # the inner execution inline on the same thread, and a single slot
        # would hand the outer span's generator to the inner close.
        tls = threading.local()

        def _spans() -> list[Any]:
            spans = getattr(tls, "spans", None)
            if spans is None:
                spans = tls.spans = []
            return spans

        def _pre() -> None:
            gen = gen_fn()
            try:
                next(gen)
            except StopIteration:
                raise RuntimeError(
                    f"@{task_name(fn)}.wrap_task {name!r} returned without "
                    f"yielding — exactly one `result = yield` marks where "
                    f"the body runs"
                ) from None
            _spans().append(gen)

        def _post(result: Any) -> None:
            spans = getattr(tls, "spans", None)
            if not spans:
                return  # the anchor never fired: no span open
            gen = spans.pop()
            try:
                gen.send(result)
            except StopIteration:
                return
            raise RuntimeError(
                f"@{task_name(fn)}.wrap_task {name!r} yielded a second time "
                f"— one yield exactly"
            )

        _pre.__name__ = name
        _post.__name__ = name
        fn.pre_task(_pre)
        fn.post_task(_post)
        return gen_fn

    def wrap_bind_sugar(gen_fn: Callable[..., Any]) -> Callable[..., Any]:
        """The two-yield wrapper, per task: enters at the bind boundary,
        yields once before binding and once where the body runs."""
        name = getattr(gen_fn, "__name__", repr(gen_fn))
        if not _isgeneratorfunction(gen_fn):
            raise RegistrationError(
                f"@{task_name(fn)}.wrap_bind {name!r} must be a generator "
                f"function — two yields: the bind boundary, then the body"
            )
        # Same stack discipline as wrap_task, with one more state per span:
        # whether its bind half has been matched by the body half yet, so a
        # hook firing without its own bind (a body call skips binding) never
        # advances an outer execution's open span.
        tls = threading.local()

        def _spans() -> list[list[Any]]:
            spans = getattr(tls, "spans", None)
            if spans is None:
                spans = tls.spans = []
            return spans

        def _bind() -> None:
            gen = gen_fn()
            try:
                next(gen)
            except StopIteration:
                raise RuntimeError(
                    f"@{task_name(fn)}.wrap_bind {name!r} returned without "
                    f"yielding — two yields exactly"
                ) from None
            _spans().append([gen, False])

        def _enter() -> None:
            spans = getattr(tls, "spans", None)
            if not spans or spans[-1][1]:
                return  # a body call skips binding: the span never opened
            span = spans[-1]
            span[1] = True
            try:
                span[0].send(None)
            except StopIteration:
                spans.pop()
                raise RuntimeError(
                    f"@{task_name(fn)}.wrap_bind {name!r} finished after one "
                    f"yield — two yields exactly (bind, then body)"
                ) from None

        def _post(result: Any) -> None:
            spans = getattr(tls, "spans", None)
            if not spans or not spans[-1][1]:
                return
            gen, _ = spans.pop()
            try:
                gen.send(result)
            except StopIteration:
                return
            raise RuntimeError(
                f"@{task_name(fn)}.wrap_bind {name!r} yielded a third time — "
                f"two yields exactly"
            )

        _bind.__name__ = name
        _enter.__name__ = name
        _post.__name__ = name
        fn.pre_bind(_bind)
        fn.pre_task(_enter)
        fn.post_task(_post)
        return gen_fn

    fn.wrap_task = wrap_task_sugar
    fn.wrap_bind = wrap_bind_sugar


_P = ParamSpec("_P")
_R_co = TypeVar("_R_co", covariant=True)
# Identity-decorator variable: a gate returns exactly what it was given.
_F = TypeVar("_F", bound=Callable[..., Any])


class TaskFn(Protocol[_P, _R_co]):
    """The static type of a `@task`-decorated function: callable with the task's
    *own* signature (parameters and return type forwarded through the `ParamSpec`),
    plus `.opts()` for per-use option overrides. The `_footman_*` markers ride as
    dynamic attributes (read through `getattr`), so they need no declaration here.
    """

    __name__: str

    def __call__(self, *args: _P.args, **kwargs: _P.kwargs) -> _R_co: ...
    def opts(self, **overrides: Unpack[TaskOpts]) -> TaskFn[_P, _R_co]:
        """Per-use option overrides; the reference stays callable with the
        task's own signature, so an opted call type-checks like a bare one."""
        ...

    # The task's own lifecycle, attachable on the handle: code local to the
    # task it governs. Attachment is permanent (it changes what the task
    # does for every requester); each attacher returns the hook unchanged,
    # so the decorators stack and the functions stay callable.
    def pre_bind(self, hook: Callable[[], None], /) -> Callable[[], None]:
        """Before this task's arguments bind — its own setup moment."""
        ...

    def pre_task(self, hook: Callable[[], None], /) -> Callable[[], None]:
        """Before this task's body, innermost — after any plugin's pres."""
        ...

    def pre_record(
        self, hook: Callable[[ResultView], None], /
    ) -> Callable[[ResultView], None]:
        """This task's reviewer: the draft, before the record seals."""
        ...

    def post_task(self, hook: Callable[[Any], None], /) -> Callable[[Any], None]:
        """Watch this task's sealed record — read-only; veto via `fail()`."""
        ...

    def wrap_task(self, fn: _F, /) -> _F:
        """The pre/post pair as one generator: `result = yield` once."""
        ...

    def wrap_bind(self, fn: _F, /) -> _F:
        """The two-yield wrapper: the bind boundary, then the body."""
        ...


class TaskDecorator(Protocol):
    """Register a function as a task: bare (`@task`) or parameterised
    (`@task(serial=True)`). The function's name becomes the command
    (underscores as hyphens), its typed parameters become flags and
    positionals, and its docstring's first line becomes the help text.

    This protocol is the module-level `task`'s static shape — the bound
    `Group.task` of the root registry, whose docstring carries the
    long-form story. `test_registry_aliases_stay_in_sync` holds its
    parameterised form to `Group.task`'s."""

    @overload
    def __call__(self, fn: Callable[_P, _R_co]) -> TaskFn[_P, _R_co]: ...
    @overload
    def __call__(
        self,
        fn: None = None,
        *,
        name: str = "",
        pre: Sequence[Task] = (),
        post: Sequence[Task] = (),
        progress: bool = True,
        infinite: bool = False,
        shared: bool | None = None,
        confirm: str = "",
        interactive: bool = False,
        keep_going: bool | None = None,
        atomic: bool = False,
        timeout: float | None = None,
        retries: int = 0,
        cwd: str | Path = "",
        rel: str | Path = "",
        serial: bool = False,
        exclusive: bool = False,
        hidden: bool | None = None,
        expose: str | None = None,
        lanes: Sequence[Lane] = (),
        uses: Sequence[GlobalOption] = (),
    ) -> Callable[[Callable[_P, _R_co]], TaskFn[_P, _R_co]]:
        """Configure and register a task.

        Args:
            name: Override the command name; the default is the function
                name with underscores as hyphens.
            pre: Tasks that must conclude before this one runs;
                independent prerequisites run in parallel.
            post: Tasks that run after this one concludes.
            progress: `False` for work with no predictable duration (a
                watcher, a network fetch) — no timing history is
                recorded and no determinate bar is shown.
            infinite: The task runs until stopped (a dev server, a
                follow-mode tail); implies `progress=False`, and the run
                notes that Ctrl-C is how this ends.
            shared: `False` runs this task for every request instead of
                sharing one execution per task-and-arguments; it
                propagates down the task's whole subtree.
            confirm: A yes/no question asked before the task and its
                prerequisites run — deny and the subtree is skipped;
                `--yes` auto-answers.
            interactive: The task owns the real terminal — prompts and
                REPLs work, output is uncaptured, the run goes
                sequential, and `--json` refuses it.
            keep_going: Collect failures in this task's subtree instead
                of stopping at the first; `-k` says it for a whole run.
            atomic: Its subprocesses opt out of the fail-fast kill and
                finish on their own.
            cwd: Where the task runs — `"taskfile"` (the defining
                file's directory, the default), `"root"` (the cascade's
                top), `"asinvoked"` (the launch directory), or
                `"unmanaged"` — or an absolute path.
            rel: A relative suffix appended to the resolved `cwd`.
            serial: Owns the process globals — at most one serial task
                at a time, overlapping the parallel pool; a real
                `chdir` is legal inside.
            exclusive: Owns the machine — nothing else in flight while
                it runs.
            hidden: Out of `--list` and help, callable and completable
                as ever; unset inherits the enclosing group's answer.
            expose: Where the task is offered — "always", "global_only"
                (outside a project) or "project_only". Unset inherits the group
                checkout, by name and with a reason.
            lanes: Named resources this task holds while it runs;
                contends only with other claimants of the same lanes.
            uses: Global options this task reads — mounting it puts
                them on the command line.
        """
        ...


class GroupFactory(Protocol):
    """The static shape of the module-level `group` factory (`Group.group`).

    Every parameter `Group.group` takes belongs here, or the module-level
    spelling type-checks differently from the method one — which is exactly
    what a "static shape of" protocol exists to prevent.
    `test_group_factory_matches_group_group` holds the two together.
    """

    def __call__(
        self,
        name: str,
        help: str = "",
        hidden: bool | None = None,
        expose: str | None = None,
    ) -> Group: ...


class HookRegistrar(Protocol):
    """The static shape of a module-level hook registrar (`pre_tasks` and
    friends): an identity decorator — it registers the hook and hands the
    same object back."""

    def __call__(self, fn: _F) -> _F: ...


class Group:
    """A node in the command tree: named tasks and nested sub-groups."""

    def __init__(
        self,
        name: str,
        help: str = "",
        hidden: bool | None = None,
        expose: str | None = None,
    ) -> None:
        self.name = name
        self.help = help
        # Tri-state, like a task's: unset inherits the enclosing group's
        # answer, so a hidden subtree needs saying once at its root and a
        # child can still opt back into the listings with `hidden=False`.
        self.hidden = hidden
        # The same tri-state, for the same reason:
        # `group("ci", expose="project_only")` answers for everything under it,
        # and a child can still say otherwise. `None` asks whoever encloses.
        self.expose = expose
        self.tasks: dict[str, Task] = {}
        self.groups: dict[str, Group] = {}
        # Lifecycle contributions, one bucket per hook kind (root registry
        # only). `pre_tasks` hooks are the only kind today.
        # Hook kinds hold callables; the `globals` kind holds
        # `GlobalOption` singletons — one generic carriage, typed loosely.
        self.contributions: dict[str, list[Any]] = {
            kind: [] for kind in CONTRIBUTION_KINDS
        }
        # Provenance: the plugin identity that mounted this group in, or None
        # for a locally-defined one. Collision messages cite it, `--plugins`
        # reports it, and "local silently wins" is decided by it.
        self.mounted_from: str | None = None

    @property
    def default_task(self) -> Task | None:
        """The default action — what a bare `fm <group>` runs.

        Derived, never stored: the default *is* the child task named
        `default` (`@group.default` ↔ `fm lint.default`), so default-ness
        has one spelling, survives forks and grafts by construction, and
        there is no pointer to desync.
        """
        return self.tasks.get("default")

    def _stamp_default(self, fn: Task, interactive: bool) -> None:
        """The default-action validations and markers, one code path for
        every way a task can come to be named `default` — the decorator,
        `@task(name="default")`, or a mount landing one here."""
        fanout = _empty_body(fn)
        where = self.name if self.name != "root" else "the root group"
        if interactive and fanout:
            raise RegistrationError(
                f"{where}'s default {task_name(fn)!r} is interactive but has "
                f"an empty body, so it fans the group's tasks out in "
                f"parallel — there is no single body to own the terminal. "
                f"Give it a real body, or drop interactive."
            )
        # A back-reference plus the empty-body flag: an empty-body default
        # fans out the group's own tasks (implicit prerequisites at DAG-build
        # time); a custom body is the escape hatch and runs as written.
        setattr(fn, _DEFAULT_GROUP, self)
        setattr(fn, _DEFAULT_FANOUT, fanout)

    def _free_ephemeral_key(self, key: str) -> str:
        """`key`, or the next free `key-2`, `key-3` — but only mid-run.

        A duplicate name written in a tasks file is a mistake, and stays a
        taught error. One made *while a run is in flight* is not: the task is
        ad-hoc, born to be used and swept, and the name is incidental — a
        `lambda` in a loop is `<lambda>` every time, and `task(rmtree)` twice
        in one app is two honest pieces of work. Numbering them keeps both
        callable, and keeps them distinct in the report.
        """
        from livery.footman import _futures

        if _futures.active_session() is None:
            return key
        if key not in self.tasks and key not in self.groups:
            return key
        n = 2
        while f"{key}-{n}" in self.tasks or f"{key}-{n}" in self.groups:
            n += 1
        return f"{key}-{n}"

    def _claim(self, key: str) -> None:
        where = f"group {self.name!r}" if self.name != "root" else "the root"
        # `.` is the address separator (`fm docs.serve`), so a name containing
        # one would alias into fake nesting or become unreachable; whitespace
        # can never survive shell word-splitting. Refuse both at load time.
        if "." in key or any(c.isspace() for c in key):
            raise RegistrationError(
                f"{where}: {key!r} is not a legal name — '.' is the address "
                f"separator and spaces cannot be addressed; use '_' or '-' "
                f"inside a name, or nest a group instead"
            )
        if key in self.tasks:
            raise RegistrationError(f"{where} already has a task named {key!r}")
        if key in self.groups:
            raise RegistrationError(f"{where} already has a group named {key!r}")

    def _shadow_pulled(self, key: str) -> None:
        """Make way for a *local* definition of *key*: a mounted entry yields
        silently, whatever the file order — the cascade's "user names shadow
        plugins" principle, carried by provenance instead of ordering.
        Local-vs-local stays loud in `_claim`."""
        existing_task = self.tasks.get(key)
        if existing_task is not None and mounted_from(existing_task) is not None:
            del self.tasks[key]
        existing_group = self.groups.get(key)
        if existing_group is not None and existing_group.mounted_from is not None:
            del self.groups[key]

    @overload
    def task(self, fn: Callable[_P, _R_co]) -> TaskFn[_P, _R_co]: ...
    @overload
    def task(
        self,
        fn: None = None,
        *,
        name: str = "",
        pre: Sequence[Task] = (),
        post: Sequence[Task] = (),
        progress: bool = True,
        infinite: bool = False,
        shared: bool | None = None,
        confirm: str = "",
        interactive: bool = False,
        keep_going: bool | None = None,
        atomic: bool = False,
        timeout: float | None = None,
        retries: int = 0,
        cwd: str | Path = "",
        rel: str | Path = "",
        serial: bool = False,
        exclusive: bool = False,
        hidden: bool | None = None,
        expose: str | None = None,
        lanes: Sequence[Lane] = (),
        uses: Sequence[GlobalOption] = (),
    ) -> Callable[[Callable[_P, _R_co]], TaskFn[_P, _R_co]]: ...

    def task(
        self,
        fn: Task | None = None,
        *,
        name: str = "",
        pre: Sequence[Task] = (),
        post: Sequence[Task] = (),
        progress: bool = True,
        infinite: bool = False,
        shared: bool | None = None,
        confirm: str = "",
        interactive: bool = False,
        keep_going: bool | None = None,
        atomic: bool = False,
        timeout: float | None = None,
        retries: int = 0,
        cwd: str | Path = "",
        rel: str | Path = "",
        serial: bool = False,
        exclusive: bool = False,
        hidden: bool | None = None,
        expose: str | None = None,
        lanes: Sequence[Lane] = (),
        uses: Sequence[GlobalOption] = (),
    ) -> Task | Callable[[Task], Task]:
        """Register a function as a task.

        Usable bare (`@task`) or parameterised (`@task(name="build")`) to
        override the command name. `pre`/`post` declare dependency tasks (by
        reference) that run before/after this one — the scheduler runs
        independent prerequisites in parallel:

        ```python
        @task(pre=[format, lint, typecheck, test])
        def check(): ...
        ```

        Availability gating lives in the `@requires` decorators — stack
        `@requires`, `@requires_dep`, `@requires_tool`, or `@requires_env`
        above `@task` to list a task as unavailable (with a reason) where it
        can't run, rather than hide it.

        Three different things, worth keeping apart:

        * `hidden=True` — **out of the listings, callable as ever.** The
          task drops out of `--list`, `--tree` and group help (`--all`
          brings it back), while `fm <name>` runs it exactly as before,
          <kbd>Tab</kbd> completes it, and the did-you-mean index knows it:
          a listing is prose about the project, completion is help with a
          name you are already typing. For the tasks a machine calls and a
          human never types: a CI entry point, a release step another task
          drives. It is presentation only — prerequisites still run it, a
          group's empty-body fan-out still includes it, and `--json` reports
          it *marked* rather than missing, because a machine is who calls it.
          Unset inherits the enclosing group's answer, so
          `group("internal", hidden=True)` hides a whole subtree and a
          child can still say `hidden=False`.
        * `@requires…` — listed *with a reason* it can't run here.
        * `if sys.platform == "darwin": @task ...` — plain Python, and the
          task does not **exist**: nothing to list, nothing to call, no
          address at all. Reach for it when the task is meaningless on this
          machine, not when it is merely uninteresting to type.

        `progress=False` marks a task whose duration has no rhyme or
        reason (a REPL, a watcher, a network fetch): any run containing it
        never records timing history and never shows a determinate
        progress bar — the indeterminate pulse still does.

        `infinite=True` marks a task that runs until *stopped* — a dev
        server, a follow-mode tail. It implies `progress=False`, and the
        run swaps the status line for a one-time hint that Ctrl-C is how
        this ends. Listings and help carry the same note.

        `shared=False` says this task is **never shared**: every request for
        it runs. A run normally performs one execution per task and arguments,
        whoever asks — `pre=[build]` then `build()` in a body is one build —
        which is wrong for work whose whole point is to happen again, like a
        notification or a timestamp. One rule covers every spelling, so nobody
        has to remember whether a task was reached by declaration or by call:
        two dependents each get their own run, just as two calls do.

        Sharing is a property of the **request**, resolved by a ladder: this
        reference's own `.opts(shared=…)`, then the task's declaration, then
        whatever asked for it, then shared. It propagates *down* — an unshared
        request asks unshared for everything it needs, or the promise would be
        a half-truth — which is worth knowing before reaching for it, because
        one `shared=False` unshares that task's whole subtree. A step that
        genuinely is reusable pins itself with `shared=True`, which beats an
        inherited answer.

        `.opts(shared=False)` asks for one unshared run without changing the
        task, on a call or on a declared edge alike. Such a run gets its own
        value but never rewrites what the run already remembers: the first
        result stands, so later shared requests stay stable.

        `confirm="…"` gates the task on a yes/no answer asked *before* the
        task and its prerequisites run — deny and the task (and its
        subtree) is skipped; `--yes` auto-answers it. `interactive=True`
        hands the task the real terminal — no output capture, sole stdio —
        so its body can prompt or run a REPL; it can't run under `--json`, and
        because it owns the terminal, a run that contains an interactive task
        goes fully sequential — that task and everything else, one at a time.

        `cwd=` roots the task's working directory: a policy token (`"root"` —
        the highest cascade file's directory, `"taskfile"` — the file the task
        was defined in (the default), `"asinvoked"` — a pinned snapshot of the
        launch directory, `"unmanaged"` — footman stays out entirely) or an
        absolute path. `rel=` appends a relative suffix to the resolved base.
        `ctx.cwd` and every `run()`/tools subprocess follow it.
        """

        def register(fn: Callable[_P, _R_co]) -> TaskFn[_P, _R_co]:
            key = cli_name(name or task_name(fn))
            key = self._free_ephemeral_key(key)
            self._shadow_pulled(key)
            self._claim(key)
            task = _TaskFn(fn)  # one handle: registered *and* returned
            if key == "default":
                # The name *is* the mechanism: any task named `default` is its
                # group's default action — `@group.default` is sugar for this.
                # One validation path, so there are no second-class defaults.
                self._stamp_default(task, interactive)
            _apply_policy(
                task,
                pre=pre,
                post=post,
                progress=progress,
                infinite=infinite,
                shared=shared,
                confirm=confirm,
                interactive=interactive,
                keep_going=keep_going,
                atomic=atomic,
                timeout=timeout,
                retries=retries,
                cwd=cwd,
                rel=rel,
                serial=serial,
                exclusive=exclusive,
                hidden=hidden,
                expose=expose,
                lanes=lanes,
            )
            if uses:
                for used in uses:
                    if not isinstance(used, GlobalOption):
                        raise RegistrationError(
                            f"@task(uses=...) on {key!r} takes GlobalOption "
                            f"singletons — got {type(used).__name__}"
                        )
                setattr(task, _USES, tuple(uses))
            if cli_name(task.__name__) != key:
                # Numbered by `_free_ephemeral_key`: report it under the name
                # it actually answers to, not the one it collided on.
                object.__setattr__(task, "__name__", key)
            previous = self.tasks.get(key)
            self.tasks[key] = task
            _note_if_ephemeral(self, key, previous)
            return cast("TaskFn[_P, _R_co]", task)

        return register(fn) if fn is not None else register

    def group(
        self,
        name: str,
        help: str = "",
        hidden: bool | None = None,
        expose: str | None = None,
    ) -> Group:
        """Create and register a nested command group, returning it.

        `hidden=True` keeps the whole subtree out of the human listings
        (`--list`, `--tree`, help — `--all` shows them) while leaving every
        address in it callable and completable — see `@task(hidden=…)`.

        `expose="project_only"` says the whole subtree belongs to a project:
        outside one it is not listed, not completed, and refused by name.
        `"global_only"` says the opposite, `"always"` says both. A child may
        still answer for itself — the same tri-state `hidden` has.
        """
        key = cli_name(name)
        if key == "default":
            # `default` is a meaningful *task* name — the group's default
            # action. A group-typed default is incoherent (a bare `fm lint`
            # resolving to another bare group — turtles), so the name is
            # refused for groups at load time.
            raise RegistrationError(
                f"a group cannot be named 'default': the name means \"this "
                f"group's default action\" and belongs to a task — declare "
                f"@{self.name}.default, or name a task 'default'"
            )
        adopted = self.groups.get(key)
        if adopted is not None and adopted.mounted_from is not None:
            # A local definition over a *mounted* group adopts it rather than
            # evicting it: claiming the name means adding to it — exactly
            # what mounting after the definition produces. Local leaves still
            # shadow mounted ones (task-level `_shadow_pulled`), so definition
            # order stops mattering: either way the union, local wins per
            # leaf, and only the listing order follows the file.
            if help:
                adopted.help = help
            if hidden is not None:
                adopted.hidden = hidden
            if expose is not None:
                adopted.expose = expose
            return adopted
        self._shadow_pulled(key)
        self._claim(key)
        sub = Group(key, help, hidden, expose)
        self.groups[key] = sub
        return sub

    def pre_tasks(self, fn: _F) -> _F:
        """Register the once-per-invocation hook, run before anything else.

        It happens after the whole `tasks.py` cascade is assembled and *before*
        availability gates, the manifest, and any task — the one single-threaded
        moment where the invocation is still editable. It is handed the
        `Invocation`:

            @footman.pre_tasks
            def gate_deploys(inv):
                audit = inv.tasks["audit"]
                for t in inv.tasks:
                    if t.name.startswith("deploy"):
                        t.add_pre(audit)

        Its edits are part of the plan, never a runtime surprise: an added `pre`
        runs and shows in `--dry-run`, a disabled task drops out of listings and
        completion. Setting `os.environ` here is ordinary code — it is pre-DAG
        and single-threaded, so everything downstream sees it, including
        `requires_env` gates and `env()` parameter fallbacks. (Per-*task*
        environment is a different lane: `ctx.env`.)

        Hooks run in cascade order — root's first, the folder nearest your cwd
        last, each seeing the previous ones' edits — the same "local overrides
        global" precedence the task cascade uses. Read and edit each task
        through `TaskView`, never the private `_footman_*` attributes.

        It also runs in the detached child that rebuilds the completion
        manifest, so a gate that depends on what a hook sets is baked into
        completion too. That is why it must be **quick and quiet**, and why
        tree edits derive from files, config and environment — never from
        `inv.cli`, which the child does not have.
        """
        _check_hook_arity("pre_tasks", fn, 1)
        self.contributions["pre_tasks"].append(fn)
        _note_contribution(fn, "pre_tasks", fn)
        return fn

    def pre_bind(self, fn: _F) -> _F:
        """Register the before-binding hook: `pre_bind(inv, task)`.

        The earliest per-task moment: it fires before the task's parameters
        are bound, so what it writes into `task.env` reaches `env()`
        fallbacks, coercion, and `check(fn)` validators — the one moment a
        plugin can influence what the body will be handed:

            @footman.pre_bind
            def credentials(inv, task):
                task.env["DEPLOY_TOKEN"] = vault.read("deploy")

        Because nothing is bound yet, `task.args` is not readable here —
        read the values in `pre_task`, the post-bind moment. Binding happens
        once per *request*: a request that then joins work the run already
        performed still bound first, so `pre_bind` may fire for a request
        whose row ends up `shared`.
        """
        _check_hook_arity("pre_bind", fn, 2)
        self.contributions["pre_bind"].append(fn)
        _note_contribution(fn, "pre_bind", fn)
        return fn

    def post_tasks(self, fn: _F) -> _F:
        """Register the once-per-invocation closing hook: `post_tasks(inv)`.

        The run report's moment, on the main thread, after every task has
        concluded and before the summary or the `--json` envelope prints —
        so a rewrite a hook makes through a result view is what gets
        reported. The invocation now carries the whole story:

            @footman.post_tasks
            def digest(inv):
                failed = [r for r in inv.results if not r.ok]
                slack.post(f"{len(failed)} failed of {len(inv.results)}, "
                           f"{inv.total_ms:.0f} ms")

        `inv.results` is every row, in the order the work was created, as
        result views —
        executions, `shared` rows, refusals, and `skipped` nodes
        (`inv.skipped` is that subset; a `post_task`-only reporter never
        sees what never ran, which is why this moment exists). Under
        `--json` anything a hook prints goes to stderr — the envelope owns
        stdout. Hooks run in cascade order; a raising hook is named and
        fails the invocation rather than passing silently.
        """
        _check_hook_arity("post_tasks", fn, 1)
        self.contributions["post_tasks"].append(fn)
        _note_contribution(fn, "post_tasks", fn)
        return fn

    def wrap_task(self, fn: _F) -> _F:
        """Register a one-yield wrapper around the body: sugar over the pair.

        Write the pre half, `result = yield`, then the post half — locals
        carry your state, and a `try/finally` around the yield closes even
        when the task fails:

            @footman.wrap_task
            def span(inv, task):
                s = tracer.start(task.name, dict(task.args))
                result = yield
                s.end(ok=result.ok)

        It enters at the `pre_task` moment and is resumed with the
        `ResultView` at `post_task` — one engine, both spellings, so the
        rules are the pair's rules: per request (a request satisfied by an
        execution the run already performed is resumed with its `shared`
        row), unwound in reverse plugin order, a raising half failing the
        task, named. The one thing it never sees is a task that failed to
        **bind** — its anchor moment never fires; observe the bind boundary
        with `@wrap_bind`, which enters there.
        """
        name = getattr(fn, "__name__", repr(fn))
        if not _isgeneratorfunction(fn):
            raise RegistrationError(
                f"@wrap_task {name!r} must be a generator function — write "
                f"`result = yield` where the body runs (a plain pair of "
                f"functions is @pre_task + @post_task)"
            )
        _check_hook_arity("wrap_task", fn, 2)
        key = f"__wrap_task_{name}"

        @functools.wraps(fn)
        def _pre(inv: object, task: Any) -> None:
            gen: Any = fn(inv, task)
            try:
                next(gen)  # the pre half, up to the yield
            except StopIteration:
                raise RuntimeError(
                    f"@wrap_task {name!r} returned without yielding — exactly "
                    f"one `result = yield` marks where the body runs"
                ) from None
            setattr(task.state, key, gen)

        @functools.wraps(fn)
        def _post(inv: object, task: Any, result: Any) -> None:
            gen = getattr(task.state, key, None)
            if gen is None:
                return  # the anchor never fired (a bind failure): no span open
            try:
                gen.send(result)  # the post half, to the end
            except StopIteration:
                return
            raise RuntimeError(
                f"@wrap_task {name!r} yielded a second time — one yield "
                f"exactly; @wrap_bind is the two-yield wrapper that enters at "
                f"the bind boundary"
            )

        self.contributions["pre_task"].append(_pre)
        self.contributions["post_task"].append(_post)
        _note_contribution(fn, "pre_task", _pre)
        _note_contribution(fn, "post_task", _post)
        return fn

    def wrap_bind(self, fn: _F) -> _F:
        """Register a two-yield wrapper spanning bind, body and all: sugar
        over `pre_bind` + `pre_task` + `post_task`, one generator per request.

        The first yield sits at the bind boundary and receives the bound
        arguments; the second sits around the body and receives the
        `ResultView`:

            @footman.wrap_bind
            def audit(inv, task):
                started = clock()
                try:
                    bound = yield      # after binding: the real values
                    result = yield     # after the body: the outcome
                finally:
                    log(task.name, clock() - started)

        A failed **bind** arrives as the failure raised at the first yield,
        so a `try/finally` (or `except`) around it closes the span even
        then — the one span `wrap_task` cannot close. Everything else is the
        pair's rules, per request.
        """
        name = getattr(fn, "__name__", repr(fn))
        if not _isgeneratorfunction(fn):
            raise RegistrationError(
                f"@wrap_bind {name!r} must be a generator function — write "
                f"`bound = yield` then `result = yield` (a plain trio of "
                f"functions is @pre_bind + @pre_task + @post_task)"
            )
        _check_hook_arity("wrap_bind", fn, 2)
        key = f"__wrap_bind_{name}"

        @functools.wraps(fn)
        def _bind_pre(inv: object, task: Any) -> None:
            gen: Any = fn(inv, task)
            try:
                next(gen)  # up to the bind boundary
            except StopIteration:
                raise RuntimeError(
                    f"@wrap_bind {name!r} returned without yielding — it "
                    f"takes two: `bound = yield`, then `result = yield`"
                ) from None
            setattr(task.state, key, (gen, "binding"))

        @functools.wraps(fn)
        def _pre(inv: object, task: Any) -> None:
            pair = getattr(task.state, key, None)
            if pair is None:
                return
            gen, _ = pair
            try:
                gen.send(task.args)  # binding done: hand over the values
            except StopIteration:
                raise RuntimeError(
                    f"@wrap_bind {name!r} finished after one yield — it "
                    f"takes two: `bound = yield`, then `result = yield`"
                ) from None
            setattr(task.state, key, (gen, "running"))

        @functools.wraps(fn)
        def _post(inv: object, task: Any, result: Any) -> None:
            pair = getattr(task.state, key, None)
            if pair is None:
                return
            gen, phase = pair
            try:
                if phase == "binding":
                    # Binding never completed: the failure arrives at the
                    # first yield, so the author's try/finally closes the
                    # span. Whatever propagates back out is the same failure
                    # the task already reported.
                    error = result.error
                    if error is None:
                        error = RuntimeError("binding never completed")
                    gen.throw(error)
                else:
                    gen.send(result)  # the closing half, to the end
            except StopIteration:
                return
            except BaseException:
                if phase == "binding":
                    return  # the thrown failure resurfacing: span closed
                raise
            raise RuntimeError(
                f"@wrap_bind {name!r} yielded again after the result — two "
                f"yields exactly"
            )

        self.contributions["pre_bind"].append(_bind_pre)
        self.contributions["pre_task"].append(_pre)
        self.contributions["post_task"].append(_post)
        _note_contribution(fn, "pre_bind", _bind_pre)
        _note_contribution(fn, "pre_task", _pre)
        _note_contribution(fn, "post_task", _post)
        return fn

    def pre_task(self, fn: _F) -> _F:
        """Register the before-each-task hook: `pre_task(inv, task)`.

        Runs on the task's worker thread — in parallel across tasks, for
        every *request*: a chain segment, a prerequisite, a fan-out member, a
        body call. Only the body is shared: a request satisfied by an
        execution the run already performed still gets the pair, opened here
        and closed by `post_task` with its `shared` row — so pairing never
        depends on sharing, and only a reporter that cares reads
        `result.state`. It fires after binding, so `task.args` holds the
        real values the body would receive:

            @footman.pre_task
            def open_span(inv, task):
                task.state.span = tracer.start(task.name, dict(task.args))

        `inv` is the frozen invocation — read it freely, it cannot change under
        you. Per-plugin scratch goes on `task.state` (private to your plugin,
        one namespace per execution, delivered back to your `post_task`);
        per-task environment goes in `task.env` (the task's own overlay —
        never `os.environ`, which is shared with every parallel sibling).
        A raising hook fails the task like a failed prerequisite, named.

        The return value is **reserved**: a future power for a pre that
        supplies the task's result and skips the body. Today a returned value
        is noted and ignored — state belongs on `task.state`.
        """
        _check_hook_arity("pre_task", fn, 2)
        self.contributions["pre_task"].append(fn)
        _note_contribution(fn, "pre_task", fn)
        return fn

    def post_task(self, fn: _F) -> _F:
        """Register the after-each-task hook: `post_task(inv, task, result)`.

        The task-finished event: once a request's ladder opened, it fires
        when the request concludes — an execution whatever its outcome, a
        bind refusal, or a request satisfied by an execution the run already
        performed (`result.state == "shared"`). It fires whether or not your
        plugin registered a pre, and however any pre fared. Posts unwind in
        reverse plugin order, so the first plugin in speaks last.

        `result` reads everything (`ok`, `code`, `returned`, `error`,
        `duration`, `output`, `steps`) and writes one thing: `set_returned`,
        which rewrites the *reported* value — the summary and the `--json`
        envelope — never what a dependent or a body caller received. A raising
        hook fails an otherwise-green task, named: a reporter that crashed
        must not pass silently.

            @footman.post_task
            def close_span(inv, task, result):
                task.state.span.end(ok=result.ok)
        """
        _check_hook_arity("post_task", fn, 3)
        self.contributions["post_task"].append(fn)
        _note_contribution(fn, "post_task", fn)
        return fn

    @overload
    def default(self, fn: Callable[_P, _R_co]) -> TaskFn[_P, _R_co]: ...
    @overload
    def default(
        self,
        fn: None = None,
        *,
        pre: Sequence[Task] = (),
        post: Sequence[Task] = (),
        progress: bool = True,
        infinite: bool = False,
        shared: bool | None = None,
        confirm: str = "",
        interactive: bool = False,
        keep_going: bool | None = None,
        atomic: bool = False,
        timeout: float | None = None,
        retries: int = 0,
        cwd: str | Path = "",
        rel: str | Path = "",
        serial: bool = False,
        exclusive: bool = False,
        hidden: bool | None = None,
        expose: str | None = None,
    ) -> Callable[[Callable[_P, _R_co]], TaskFn[_P, _R_co]]: ...

    def default(
        self,
        fn: Task | None = None,
        *,
        pre: Sequence[Task] = (),
        post: Sequence[Task] = (),
        progress: bool = True,
        infinite: bool = False,
        shared: bool | None = None,
        confirm: str = "",
        interactive: bool = False,
        keep_going: bool | None = None,
        atomic: bool = False,
        timeout: float | None = None,
        retries: int = 0,
        cwd: str | Path = "",
        rel: str | Path = "",
        serial: bool = False,
        exclusive: bool = False,
        hidden: bool | None = None,
        expose: str | None = None,
    ) -> Task | Callable[[Task], Task]:
        """Register *fn* as this group's default action — what a bare
        `fm <group>` runs, and what the group returns when called.

        Usable bare (`@group.default`) or parameterised
        (`@group.default(keep_going=True)`). It takes the same orchestration
        options as `@task` — `pre`/`post`, `progress`, `infinite`, `confirm`,
        `interactive`, `keep_going`, `atomic` — with no `name` (the group
        already names it).

        The function's signature *is* the group's whole CLI surface,
        positionals included: `fm lint src/` hands `src/` to the default the
        way any task takes an argument. A nested task always keeps its own
        dotted address (`fm lint.python`), so a bare word after the group is
        unambiguously the default's value — when a value happens to equal a
        child's name, the run carries a one-line stderr note pointing at the
        dotted spelling.

        An **empty-body** default fans the group's own tasks out in parallel, so
        `interactive=True` on one is rejected — there is no single body to own
        the terminal. Give the default a real body to make it interactive.

        The default registers as the child task named **`default`** — a
        fixed, well-known name, not the function's own (which stays private,
        so renaming it is free). The decorator you wrote is the address you
        type: `@lint.default` ↔ `fm lint.default`; bare `fm lint` stays the
        idiomatic spelling, the way `GET /` serves `/index.html`. The name is
        the mechanism — `@group.default` is sugar for a task named `default`.
        """

        def register(fn: Callable[_P, _R_co]) -> TaskFn[_P, _R_co]:
            self._claim("default")
            task = _TaskFn(fn)  # one handle: registered *and* returned
            self._stamp_default(task, interactive)
            _apply_policy(
                task,
                pre=pre,
                post=post,
                progress=progress,
                infinite=infinite,
                shared=shared,
                confirm=confirm,
                interactive=interactive,
                keep_going=keep_going,
                atomic=atomic,
                timeout=timeout,
                retries=retries,
                cwd=cwd,
                rel=rel,
                serial=serial,
                exclusive=exclusive,
                hidden=hidden,
                expose=expose,
            )
            self.tasks["default"] = task
            return cast("TaskFn[_P, _R_co]", task)

        return register(fn) if fn is not None else register

    def opts(self, **overrides: Unpack[TaskOpts]) -> TaskFn[..., Any]:
        """Per-use option overrides for this group's default action, the same
        `.opts()` a task has — `pre=[lint.opts(keep_going=True)]`. Overrides ride
        the group's default when it runs (bare, as a `pre=`, or called). The
        static type is a task reference with an untracked signature — `Group`
        doesn't carry its default's parameters the way `TaskFn` carries a
        task's."""
        return cast("TaskFn[..., Any]", _Opted(self, _opts_overrides(dict(overrides))))

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Run this group's default action — the imperative mirror of a bare
        `fm <group>` and of `pre=[group]`.

        A runnable group (one with an `@group.default`) is callable from a task
        body the way a task is: a `check` task can call `lint(fix=fix)`. It runs
        the default's action synchronously and in order — a custom body as
        written, or, for an empty-body default, the group's own tasks, each
        handed the arguments it declares (partial reach, by name). Like every
        body call it forwards arguments explicitly and runs to completion before
        the next statement; prerequisites and parallelism stay the scheduler's
        job — reach for a real chain, `pre=`, or `parallel()` for those.
        """
        if self.default_task is None:
            raise TypeError(
                f"group {self.name!r} is not runnable: it has no "
                f"@{self.name}.default, so there is no action to call. Add a "
                f"default action, or call a task inside the group directly."
            )
        if not fans_out(self.default_task):
            return self.default_task(*args, **kwargs)  # custom body: as written
        # Empty-body default: fan out the group's own tasks, handing each only
        # the arguments it declares — the imperative echo of `fm <group>`.
        # Sequential, like any body call; wrap the call in parallel() to overlap.
        # The `default` child is the fan-out itself: excluded from its own set.
        from livery.footman._manifest import resolved_signature

        for name, child in self.tasks.items():
            if name == "default":
                continue
            accepts = set(resolved_signature(child).parameters)
            child(**{k: v for k, v in kwargs.items() if k in accepts})
        return None


# The implicit root registry populated by the module-level `task`/`group`
# aliases (re-exported from `footman`). Constructing an explicit `Group` is
# always an option and keeps tests free of global state.
root: Final[Group] = Group("root")
task: Final[TaskDecorator] = root.task
group: Final[GroupFactory] = root.group
pre_tasks: Final[HookRegistrar] = root.pre_tasks
post_tasks: Final[HookRegistrar] = root.post_tasks
pre_bind: Final[HookRegistrar] = root.pre_bind
pre_task: Final[HookRegistrar] = root.pre_task
post_task: Final[HookRegistrar] = root.post_task
wrap_task: Final[HookRegistrar] = root.wrap_task
wrap_bind: Final[HookRegistrar] = root.wrap_bind


def reset() -> None:
    """Clear the global `root` registry (used by the test-suite, and by
    `_discover` between file imports).

    `_CONFIG_SECTIONS` deliberately survives: it is keyed by module name —
    a re-import overwrites its own entry — and the cascade resets between
    files, so clearing here would erase one file's declaration while the
    next imports."""
    root.tasks.clear()
    root.groups.clear()
    for bucket in root.contributions.values():
        bucket.clear()


def _importable(module: str) -> bool:
    """True if *module* is importable, via `find_spec`.

    `find_spec` doesn't import the module itself, but a dotted name imports its
    parent packages to locate the child — so a parent whose `__init__` raises
    (any exception, not just ImportError/ValueError) must read as
    not-importable, never crash `fm --list` with a traceback.
    """
    import importlib.util

    try:
        return importlib.util.find_spec(module) is not None
    except Exception:
        return False


def pre_deps(fn: Task) -> list[Task]:
    """The prerequisites declared to run before *fn* (`@task(pre=…)`)."""
    return getattr(fn, _PRE, [])


def post_deps(fn: Task) -> list[Task]:
    """The tasks declared to run after *fn* (`@task(post=…)`)."""
    return getattr(fn, _POST, [])


def mounted_from(node: Task | Group) -> str | None:
    """The plugin identity that mounted *node* in, or None for local code.

    Groups carry it as a real field (each graft gets fresh Group objects);
    task functions carry it as a marker attribute — fns are shared between
    forks on purpose, so the stamp is the *identity*, which is the same
    everywhere the same provider's fn lands.
    """
    if isinstance(node, Group):
        return node.mounted_from
    return getattr(node, _MOUNTED, None)


def default_group(fn: Task) -> Group | None:
    """The group *fn* is the `@group.default` action of, or `None`."""
    return getattr(fn, _DEFAULT_GROUP, None)


def fans_out(fn: Task) -> bool:
    """Whether *fn* is an empty-body `@group.default` that fans out its group's
    own tasks (they become its implicit prerequisites) rather than run a body."""
    return getattr(fn, _DEFAULT_FANOUT, False) is True


def wants_progress(fn: Task) -> bool:
    """Whether *fn* consented to timing: `@task(progress=False)` opts out,
    and `infinite=True`/`interactive=True` imply it — a duration that never
    arrives, or one spent waiting on a human, is not history."""
    if getattr(fn, _INFINITE, False):
        return False
    if getattr(fn, _INTERACTIVE, False):
        return False
    return getattr(fn, _PROGRESS, True) is not False


def is_infinite(fn: Task) -> bool:
    """Whether *fn* runs until stopped: `@task(infinite=True)`."""
    return getattr(fn, _INFINITE, False) is True


def sharing(fn: Task) -> bool | None:
    """*fn*'s declared sharing policy: `@task(shared=True/False)`, or `None`
    when it left the choice to whoever asks for it.

    Sharing is a property of a *request*, resolved by a ladder — the request's
    own `.opts(shared=…)`, then the task's declaration, then whatever it was
    requested by (it propagates down a dependency subtree), then shared. This
    reader is the declaration rung; `schedule` resolves the rest, exactly as it
    does for `keep_going`.
    """
    return getattr(fn, _SHARED, None)


def task_body(fn: Task) -> Task:
    """The callable that runs *fn*'s own body — never the machinery.

    A task is a handle whose `__call__` *is* the body-call machinery, so the
    executor asks for this instead: the author's function, reached through the
    handle and through any `.opts()` reference around it (whose policy the
    executor has already applied at the task boundary). Calling the handle here
    would route the scheduler's own invocation back into the memo it is meant
    to be filling.
    """
    if isinstance(fn, _Opted):
        base = object.__getattribute__(fn, "_opted_base")
        if isinstance(base, Group):
            base = base.default_task
        return task_body(cast("Task", base))
    if isinstance(fn, _TaskFn):
        return cast("Task", fn.__wrapped__)
    return fn


def declared_hidden(fn: Task) -> bool | None:
    """*fn*'s own answer to "list me?", or `None` when it never said.

    `None` is the whole point: the manifest resolves it against the enclosing
    group, so hiding a subtree is one declaration at its root and a child can
    still say `hidden=False` to come back.
    """
    value = getattr(fn, _HIDDEN, None)
    return value if value is None else bool(value)


def declared_expose(fn: Task) -> str | None:
    """*fn*'s own answer to "where am I offered?", or `None` when it never
    said — so the enclosing group answers for it, exactly as `hidden` does."""
    value = getattr(fn, _EXPOSE, None)
    return value if isinstance(value, str) else None


def seal_expose(group: Group, default: str = "project_only") -> None:
    """Give every unmarked task in *group* an explicit `expose`.

    Called on a rung the moment it is assembled, because that is the only
    place the answer is known: a package's tasks are `project_only` until
    one says otherwise, while a person's own tasks file rides everywhere.

    It has to happen *before* the rungs merge, not after. They are overlaid
    into one tree, and once merged nothing distinguishes "the package
    shipped this" from "the person wrote this" by position — while their
    defaults differ. Sealing here means everything downstream reads one
    resolved value.
    """

    def walk(node: Group, inherited: str | None) -> None:
        mine = node.expose if node.expose is not None else inherited
        for fn in node.tasks.values():
            if declared_expose(fn) is None:
                setattr(fn, _EXPOSE, default if mine is None else mine)
        for sub in node.groups.values():
            walk(sub, mine)

    walk(group, group.expose)


def is_interactive(fn: Task) -> bool:
    """Whether *fn* owns the real terminal: `@task(interactive=True)` — no
    output capture, sole stdio, so its body may prompt or run a REPL."""
    return getattr(fn, _INTERACTIVE, False) is True


def keeps_going(fn: Task) -> bool | None:
    """*fn*'s declared failure policy: `@task(keep_going=True/False)`, or `None`
    when it left the choice to the command line / the built-in default."""
    return getattr(fn, _KEEP_GOING, None)


def is_atomic(fn: Task) -> bool:
    """Whether *fn*'s subprocesses opt out of fail-fast's kill:
    `@task(atomic=True)` — they run to completion rather than be cut off."""
    return getattr(fn, _ATOMIC, False) is True


def task_timeout(fn: Task) -> float | None:
    """The `@task(timeout=…)` deadline in seconds, or `None` for no deadline.

    A duration here, an instant on the context: the scheduler turns one into
    the other when the body starts, because everything downstream asks how
    long is left rather than how long was allowed."""
    value = getattr(fn, _TIMEOUT, None)
    return None if value is None else float(value)


def task_retries(fn: Task) -> int:
    """Extra attempts after the first — `@task(retries=2)` means up to three
    runs. `0` (the default) means the first attempt is the only one.

    A count of *retries*, never of attempts: "retry twice" is what an author
    says out loud, and reading it as a total would silently halve every
    declaration."""
    value = getattr(fn, _RETRIES, 0)
    return value if isinstance(value, int) else 0


def task_confirm(fn: Task) -> str:
    """The `@task(confirm="…")` prompt gating this task, or `""` if none."""
    return getattr(fn, _CONFIRM, "")


def task_cwd(fn: Task) -> str | Path | None:
    """The task's declared cwd policy — a token from `CWD_TOKENS` or an
    absolute `Path` — or `None` when undeclared (the config ladder decides)."""
    return getattr(fn, _CWD, None)


def task_rel(fn: Task) -> str | None:
    """The task's declared rel suffix (appended to the resolved cwd base)."""
    return getattr(fn, _REL, None)


def task_lane(fn: Task) -> str | None:
    """The task's declared arbiter lane: `"exclusive"` (runs with nothing
    else in flight), `"serial"` (owns the process globals, one at a time,
    overlapping the parallel pool), or `None` (the parallel regime)."""
    if getattr(fn, _EXCLUSIVE, False):
        return "exclusive"
    if getattr(fn, _SERIAL, False):
        return "serial"
    return None


Check = Callable[[], str | None]
"""One availability gate: the reason it fails, or `None` when it passes."""


def _gate(check: Check) -> Callable[[_F], _F]:
    """Stack *check* onto a task's availability gates, read live by `availability`.

    The decorator is identity in types — a gate marks the function and hands
    the same object back, so whatever it wraps (a plain function below
    `@task`, or a `TaskFn` when stacked above it) keeps its static type."""

    def decorate(fn: _F) -> _F:
        setattr(fn, _CHECKS, [*getattr(fn, _CHECKS, ()), check])
        return fn

    return decorate


_PRE_RECORD = "_footman_pre_record"


def task_lanes(fn: Any) -> tuple[Any, ...]:
    """The named lanes this task claims at its boundary, or ()."""
    return tuple(getattr(fn, _LANES, ()))


def pre_record(hook: Callable[[Any], None]) -> Callable[[_F], _F]:
    """Attach a reviewer to a task: `@pre_record(fn)` stacked on the `def`.

    The reviewer receives the task's record while it is still a draft — after
    the body concluded, before the record is sealed, observed, or reported.
    It reads the draft (a `ResultView`) and may set `title` and `code`; the
    row's verdict follows what the review leaves behind, and the record's
    audit names the reviewer and what it did.

    Reviewers run from the inside out: the hook written closest to the
    function sees the draft first, and each one above it sees what the
    previous reviewers left. Stacks above or below `@task` alike — the
    decorated function (and a `TaskFn` when stacked above the lifter) keeps
    its static type. A reviewer that raises fails the task with its own
    error: a broken reviewer is a broken gate.
    """

    def decorate(fn: _F) -> _F:
        setattr(fn, _PRE_RECORD, [*getattr(fn, _PRE_RECORD, ()), hook])
        return fn

    return decorate


def task_reviewers(fn: Any) -> list[Callable[[Any], None]]:
    """The reviewers attached to *fn*, in execution order (inside-out:
    decorators apply bottom-up, so the nearest attachment appended first)."""
    return list(getattr(fn, _PRE_RECORD, ()))


def requires(
    predicate: Callable[[], object], *, reason: str = ""
) -> Callable[[_F], _F]:
    """Gate a task on a live *predicate* — available only while it is truthy.

    The generic gate the three specialisations build on. A predicate that
    raises reads as unavailable, the exception named:

    ```python
    @task
    @requires(lambda: Path("config.toml").exists(), reason="needs config.toml")
    def publish(): ...
    ```
    """

    def check() -> str | None:
        try:
            ok = predicate()
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            return f"{reason} ({detail})" if reason else detail
        return None if ok else (reason or "unavailable here")

    return _gate(check)


def requires_dep(*modules: str, reason: str = "") -> Callable[[_F], _F]:
    """Gate a task on Python *modules* being importable (`find_spec`, no import).

    Keep the real `import` in the body; this only checks availability, so a
    missing optional dependency lists as a clean reason, never an import crash.
    """

    def check() -> str | None:
        missing = [m for m in modules if not _importable(m)]
        if not missing:
            return None
        return reason or f"requires {', '.join(missing)}"

    return _gate(check)


def requires_tool(*commands: str, reason: str = "") -> Callable[[_F], _F]:
    """Gate a task on command-line tools being on `PATH` (`shutil.which`)."""

    def check() -> str | None:
        import shutil

        missing = [c for c in commands if shutil.which(c) is None]
        if not missing:
            return None
        return reason or f"requires {', '.join(missing)} on PATH"

    return _gate(check)


def requires_env(*names: str, reason: str = "") -> Callable[[_F], _F]:
    """Gate a task on environment variables being set (`in os.environ`)."""

    def check() -> str | None:
        missing = [v for v in names if v not in os.environ]
        if not missing:
            return None
        return reason or f"set {', '.join(missing)}"

    return _gate(check)


def availability(fn: Task) -> str | None:
    """The reason(s) a task is unavailable here, or `None` if it can run.

    Every `@requires` gate on the task is evaluated **live** — never from the
    cached manifest, so `DOCKER_HOST=… fm up` works the moment the environment
    does — and **all** failures are collected, each in its own words, so a task
    gated on both a missing tool and a missing variable says both. A gate whose
    predicate raises reads as unavailable with the exception named, scoped to
    that one gate.
    """
    reasons = [r for check in getattr(fn, _CHECKS, ()) if (r := check()) is not None]
    return "; ".join(reasons) if reasons else None


def _as_fn(t: TaskView | Task) -> Task:
    """Unwrap a `TaskView` to its function; pass a raw function through."""
    return t.fn if isinstance(t, TaskView) else t


class TaskView:
    """A hook's handle on one task: read its wiring, its policy flags, and
    its cascade provenance (where it was defined, what it overrode), and edit it
    here — never through the private `_footman_*` attributes."""

    def __init__(
        self,
        fn: Task,
        name: str,
        group: Group | None = None,
        *,
        path: tuple[str, ...] | None = None,
    ) -> None:
        self.fn = fn
        """The task function itself — the escape hatch past the view."""
        self.name = name
        """The task's leaf name, e.g. `build`. The whole spelling is
        `address` — two tasks in different groups share this one."""
        self.group = group
        """The group this task lives in, or `None` for a top-level task — its
        `.name` is the group's command-line spelling (e.g. `docs`)."""
        # A group has no parent pointer, deliberately: `include()`/`plugin()`
        # can mount one group object in more than one place, so an address
        # belongs to the *view* the walk yields, not to the group or the
        # function. The walk passes it; the fallback is for a hand-built view
        # (a test), where the immediate group is all there is to go on.
        self._path = path or ((group.name, name) if group else (name,))

    @property
    def path(self) -> tuple[str, ...]:
        """Every segment of the task's address, leaf last — `("forge", "dev",
        "up")`. `address` is the same thing as one string."""
        return self._path

    @property
    def address(self) -> str:
        """The task's full dotted address, exactly as the command line spells
        it: `forge.dev.up`. Unique across the tree — a name is a task or a
        group at each level, never both — so this is the key to hold a task
        by, and what `Tasks.get` looks up."""
        return ".".join(self._path)

    @property
    def mounted_from(self) -> str | None:
        """Which provider this task came from — the `footman.tasks`
        entry-point identity for `plugin()`, the module name for
        `include()` — or `None` when the tasks file defines it itself,
        which is the honest answer: the project owns it.

        Language-neutral by construction: it names a provider, not a file,
        so it keeps answering for a task that is one day not a Python
        function. A group carries the same field only when it was grafted
        whole; the moment a mount composes into a group that already
        exists, the group is shared and answers `None` while every task
        under it still answers exactly. Read ownership here."""
        return mounted_from(self.fn)

    @property
    def pre(self) -> tuple[Task, ...]:
        """The prerequisites that run before this task."""
        return tuple(pre_deps(self.fn))

    @property
    def post(self) -> tuple[Task, ...]:
        """The tasks that run after this one."""
        return tuple(post_deps(self.fn))

    @property
    def disabled(self) -> str | None:
        """Why the task is unavailable here, or `None` if it can run."""
        return availability(self.fn)

    # Policy flags (read-only — declared at decoration).

    @property
    def keep_going(self) -> bool | None:
        """The task's declared failure policy (`@task(keep_going=…)`), or `None`
        when it left the choice to the command line / the built-in default."""
        return keeps_going(self.fn)

    @property
    def atomic(self) -> bool:
        """Whether the task's subprocesses opt out of fail-fast's kill."""
        return is_atomic(self.fn)

    @property
    def infinite(self) -> bool:
        """Whether the task runs until stopped (`@task(infinite=True)`)."""
        return is_infinite(self.fn)

    @property
    def interactive(self) -> bool:
        """Whether the task owns the real terminal (`@task(interactive=True)`)."""
        return is_interactive(self.fn)

    @property
    def hidden(self) -> bool | None:
        """The task's own `hidden=` answer, or `None` when it inherits one.

        Read the *declaration*, not the resolved answer: a hook that
        hides a group wants to know whether this task overrode it.
        """
        return declared_hidden(self.fn)

    @property
    def timed(self) -> bool:
        """Whether the task records timing history / shows a determinate bar —
        `@task(progress=False)` (and `infinite`/`interactive`) opt out."""
        return wants_progress(self.fn)

    @property
    def confirm(self) -> str:
        """The `@task(confirm="…")` prompt gating the task, or `""` if none."""
        return task_confirm(self.fn)

    # Cascade provenance (read-only) — for hooks making decisions by
    # where a task came from and what it overrode.

    @property
    def defining_dir(self) -> str | None:
        """The folder the task was defined in, or `None` when the cascade did
        not tag it (a plugin- or `include()`-composed task, not a cascade file).
        Use it to act on tasks from one subtree of a monorepo."""
        from livery.footman import _discover

        return _discover.defining_dir(self.fn)

    @property
    def shadowed(self) -> Task | None:
        """The task this one overrides — same name, one cascade level up — or
        `None` if it shadows nothing."""
        from livery.footman import _discover

        return _discover.shadowed(self.fn)

    @property
    def shadow_chain(self) -> tuple[Task, ...]:
        """This task and every task it shadows, nearest (this one) first."""
        from livery.footman import _discover

        return tuple(_discover.shadow_chain(self.fn))

    @property
    def source_file(self) -> str | None:
        """The file the task's function is defined in, or `None` when it can't
        be located (a built-in or dynamically constructed function)."""
        try:
            return task_source_file(self.fn)
        except TypeError:
            return None

    def add_pre(self, *tasks: TaskView | Task) -> None:
        """Prepend prerequisites (views or functions), skipping any already set."""
        have = list(pre_deps(self.fn))
        setattr(
            self.fn,
            _PRE,
            [*(f for t in tasks if (f := _as_fn(t)) not in have), *have],
        )

    def add_post(self, *tasks: TaskView | Task) -> None:
        """Append post-tasks (views or functions), skipping any already set."""
        have = list(post_deps(self.fn))
        setattr(
            self.fn,
            _POST,
            [*have, *(f for t in tasks if (f := _as_fn(t)) not in have)],
        )

    def disable(self, reason: str) -> None:
        """Mark the task unavailable — listed with *reason*, refused if run."""
        _gate(lambda: reason)(self.fn)

    def set_opts(self, **overrides: Unpack[TaskOpts]) -> None:
        """Set orchestration options on the task **permanently, for every use** —
        the discovery-time counterpart to a per-use `.opts()`. Takes the same
        options (`TaskOpts`, the whole `.opts()` set) and rejects a task
        parameter with the same taught error; the difference is that it edits
        the registered task rather than a per-use proxy, so a hook can set a
        policy across a whole class of tasks. A command-line `-k`/`--fail-fast`
        still wins over a set `keep_going`."""
        for attr, value in _opts_overrides(dict(overrides)).items():
            setattr(self.fn, attr, value)


class Tasks:
    """A hook's view of the merged command tree: iterate every task, or
    look one up by its address, each as a `TaskView`."""

    def __init__(self, root: Group) -> None:
        self._root = root

    def __iter__(self) -> Iterator[TaskView]:
        yield from _task_views(self._root)

    def get(self, name: str) -> TaskView | None:
        """The task at *name* — its full dotted address — or `None`.

        Addresses are unique by construction (a name is a task or a group
        at each level, never both), so this either finds one task or finds
        none: there is nothing here to be ambiguous about. A bare leaf is
        not an address — `get("build")` finds a top-level `build`, never
        `docs.build`. To search by leaf, iterate: two tasks may answer to
        one, and picking between them is the caller's to make, not
        footman's to guess.

        A runnable group answers whatever its default answers, so
        `get("lint")` is `get("lint.default")` — the bare group name is
        that action's other spelling on the command line, and lookup would
        otherwise be the one place that isn't true.
        """
        by_address = {v.address: v for v in self}
        if (view := by_address.get(name)) is not None:
            return view
        return by_address.get(f"{name}.default")

    def __getitem__(self, name: str) -> TaskView:
        if (view := self.get(name)) is None:
            raise KeyError(name)
        return view

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and self.get(name) is not None


def _task_views(
    g: Group, owner: Group | None = None, prefix: tuple[str, ...] = ()
) -> Iterator[TaskView]:
    for name, fn in g.tasks.items():
        yield TaskView(fn, name, owner, path=(*prefix, name))
    for sub in g.groups.values():
        yield from _task_views(sub, sub, (*prefix, sub.name))


@contextlib.contextmanager
def capture() -> Generator[Group]:
    """Redirect module-level `@task`/`group` registration into a fresh tree.

    The seam `include()` uses to import a provider module without letting its
    decorators land in the current registry: `root.tasks`/`root.groups` are
    swapped for fresh dicts for the duration and the captured tree is yielded.
    Reentrant — a provider may itself `include()` another provider.
    """
    captured = Group("root")
    saved_tasks, saved_groups = root.tasks, root.groups
    saved_contributions = root.contributions
    root.tasks, root.groups = captured.tasks, captured.groups
    root.contributions = captured.contributions
    try:
        yield captured
    finally:
        root.tasks, root.groups = saved_tasks, saved_groups
        root.contributions = saved_contributions
