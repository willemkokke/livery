"""Typed handles for command-line tools — the tool room.

toolroom deliberately does **not** transcribe each tool's flags into
Python parameters. Transcription drifts: the wrapper pins the flag-set
its author copied, the tool moves on, and one day `show_source=True`
emits a flag the installed binary rejects. Instead, keyword arguments
translate *mechanically* — the installed tool's own CLI stays the
single source of truth, at whatever version it is:

- `fix=True` → `--fix` (`False`/`None` → omitted entirely)
- `strict=off` → `--no-strict` (disable a default-on flag; `off` is the
  `toolroom.off` sentinel — `no_strict=True` is the same thing by name)
- `output_format="github"` → `--output-format github`
- `select=["E", "F"]` → `--select E --select F` (an empty list/tuple is
  omitted entirely, so a parameter's default passes straight through)
- `x=1` (single letter) → `-x 1`
- a trailing underscore escapes Python keywords: `import_="x"` → `--import x`

Attribute access chains subcommands (`toolroom.docker.compose.up(detach=True)`
→ `docker compose up --detach`), positional strings pass through verbatim,
and *any* executable works without being declared here:
`toolroom.terraform("plan")` just runs `terraform plan`.

toolroom stands alone: with nothing else installed, a call spawns the
tool with plain `subprocess` and answers in `Result`; a failure raises
`ToolError` unless `nofail`. When [footman](https://willemkokke.github.io/footman/)
is present in the process, the same call routes through footman's
`run()` instead and inherits everything a task run means — capture,
replay-on-failure, dry-run, `recording()`, `--json` receipts — without
either package depending on the other. `_host` is the seam.

`tool.installed_version()` returns the installed binary's version as an
int tuple (cached per process, resolved outside any host so dry-run and
recording can't lie to it) — for the rare case where code must branch
on the tool's actual CLI generation.
"""

from __future__ import annotations

# Every module import is aliased private so `toolroom.<name>` never resolves to
# it: module attribute lookup beats module `__getattr__`, so a public `run`/`sys`
# would make `toolroom.run`/`toolroom.sys` the imported object instead of a Tool —
# typechecking as Tools (per the stub) but crashing at runtime (F50, F53).
import os as _os
import re as _re
import subprocess as _subprocess
import sys as _sys
import threading as _threading
import types as _types
from collections.abc import Iterator, Sequence
from pathlib import Path as _Path
from typing import Any, NamedTuple, TypeAlias
from typing import cast as _cast

# `Argv`/`Result`/`ToolError` are public, unlike the private aliases: every tool
# call returns a Result, the generated stubs import Argv and Tool from here, so
# each must resolve to the class — a real module binding beats `__getattr__`.
# `_host` is the seam: execution, host detection, and the standalone executor.
from livery.toolroom import _host as _host
from livery.toolroom._host import Argv as Argv
from livery.toolroom._host import Result as Result
from livery.toolroom._host import ToolError as ToolError

__version__ = "0.6.2"

_QUIET = {"GH_NO_UPDATE_NOTIFIER": "1"}
"""Told not to phone home while being read — see `_toolhelp.QUIET`."""

_version_cache: dict[str, tuple[int, ...]] = {}

# The one way footman reads a version out of a tool's own words, shared with
# the extractor (`_drivers.version`) so a stub's recorded version and a task's
# `installed_version()` can never disagree about *parsing* — only, deliberately,
# about which binary they asked (see `installed_version`).
#
# A negative lookbehind, not `\b`: a version glued to a `v` prefix (`v0.23.1`)
# has no word boundary before its first digit, so `\b` would skip to the middle
# and read `23.1`. Reject only a preceding digit or dot, so `v0.23.1` -> `0.23.1`
# while `2` inside `1.2.3` still can't start a fresh match. The tail matches the
# build grammars tools really ship (`0.6.0-wk.5`, `1.13.0.git.kitware…`), plus
# OpenSSH's glued patchlevel (`OpenSSH_10.4p1` -> `10.4p1` — without it the
# `\b` fails at `10.4` and the match falls through to LibreSSL's version,
# reporting the wrong library's number as ssh's).
_VERSION = _re.compile(
    r"(?<![\d.])(\d+\.\d+(?:\.\d+)?(?:p\d+)?(?:[-.][A-Za-z0-9]+)*)\b"
)


def read_version(text: str) -> str:
    """The version string inside a tool's `--version` output, or `""`.

    The string is preserved exactly, build tail and all (`0.6.0-wk.5`), for
    anything that records *which* build was read. Use `version_tuple` to
    compare.
    """
    match = _VERSION.search(text)
    return match[1] if match else ""


def version_tuple(version: str) -> tuple[int, ...]:
    """A version string reduced to comparable integers — the CLI generation.

    Only the leading numeric run counts, and the first component carrying a
    build tag ends the read: `0.6.0-wk.5` and `1.13.0.git.kitware.jobserver-1`
    both compare as their base, `(0, 6, 0)` and `(1, 13, 0)`.

    That is the honest answer to the question this is asked — *is the CLI new
    enough* — because a build tail says nothing about which flags exist.
    Scraping its digits would answer it backwards: `0.6.0-wk.5` is a fork
    build *of* 0.6.0, which every version grammar sorts at or before it,
    while `(0, 6, 0, 5)` sorts after.

    So two builds of one base compare **equal**, and every caller has to say
    what it does with that rather than read equality as an answer. Ordering a
    release chain breaks the tie on publication date, which separates a `wk`
    series correctly without this having to know what `wk` means. The
    snapshot guard cannot — an incoming reading is dated today whatever it
    holds — so it declines to move rather than guess.

    An unreadable version yields `()`, which compares lower than everything —
    "can't tell" must never read as "newer".
    """
    parts: list[int] = []
    for piece in version.split("."):
        digits = ""
        for char in piece:
            if not char.isdigit():
                break
            digits += char
        if digits:
            parts.append(int(digits))
        if not piece.isdigit():
            break
    return tuple(parts)


class _Off:
    """The value that disables a flag: `flag=off` → `--no-flag`.

    `False`/`None` mean *omit* — so a task parameter's default flows through
    untouched — which leaves no way to spell a negation. Hence an explicit
    sentinel: `strict=off` turns a default-on flag off. Equivalent to naming
    the negation directly (`no_strict=True`), but reads as intent and lets a
    variable drive it (`strict=on_by_default and off`).
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "off"


off = _Off()


# The three types every generated signature is written in — public, because
# they are what a reader hovers and what a wrapper's own parameters want to
# be annotated with. Wide on purpose: the bridge `str()`-s whatever it is
# handed (so `Path` and `int` work) and repeats the flag for each item of a
# sequence, so a narrower type would reject calls that demonstrably work.

Flag: TypeAlias = bool | _Off | None
"""A boolean flag: `True` → `--flag`, `off` → the tool's own negation,
`False`/`None` → omitted (which is what lets a task parameter's default
flow straight through)."""

Value: TypeAlias = (
    str
    | int
    | float
    | _os.PathLike[str]
    | Sequence[str | int | float | _os.PathLike[str]]
    | _Off
    | None
)
"""An option that takes a value. Scalars are `str()`-ed — a `Path` passes
straight through — and a sequence repeats the flag once per item."""

ValuedFlag: TypeAlias = bool | Value
"""An option whose value is *optional* — usable bare (`gpg_sign=True`, sign
with the default key) or with a value (`gpg_sign="KEY"`). Both spell a
valid command; the tool prints its placeholder attached to the flag,
`--gpg-sign[=<key-id>]`, which is how the extractor tells the two apart."""


# How a tool spells "off" when it is *not* `--no-<name>`. Only the
# exceptions live here, extracted from the tools themselves (click states
# it as `secondary_opts`) rather than assumed: `mkdocs build --no-clean`
# is rejected outright — the flag is `--dirty`. `fm tools.audit` reports the
# table this should hold, read from the installed tool.
_NEGATIONS: dict[str, dict[str, str]] = {
    "mkdocs": {
        "clean": "--dirty",
        "use_directory_urls": "--no-directory-urls",
    },
    # git add --all turns off as --ignore-removal (aka --no-all).
    "git": {
        "all": "--ignore-removal",
    },
}


def _negation(tool: str, key: str, *, single_dash: bool = False) -> str:
    """The flag that turns *key* off for *tool*.

    A single-dash tool (Go's `flag` package: `-fix`, not `--fix`) negates with a
    single dash too (`-no-fix`); a tool that spells it otherwise lives in
    `_NEGATIONS`, which wins here regardless of dash style.
    """
    known = _NEGATIONS.get(tool, {})
    if key in known:
        return known[key]
    dash = "-no-" if single_dash else "--no-"
    return dash + key.rstrip("_").replace("_", "-")


# Verbs that run *another* command: a wrapper's flags belong before the
# child's argv, or they leak past the tool into the child — `uv run
# --frozen pytest`, not `uv run pytest --frozen` (which hands `--frozen`
# to pytest). Dotted for nesting; extracted from each verb's usage line
# and checked by `fm tools.audit`.
_WRAPPERS: dict[str, frozenset[str]] = {
    "uv": frozenset({"run", "tool.run"}),
    "coverage": frozenset({"run"}),
    "docker": frozenset({"run", "exec", "compose.run", "compose.exec"}),
    # python's own root is a wrapper: `python -v script.py` puts the
    # interpreter's options before the script, whose own args follow it.
    "python": frozenset({""}),
    # ssh forwards everything after `destination` to the remote shell: its
    # flags must precede the positionals or they land on the remote command.
    "ssh": frozenset({""}),
}


def _is_wrapper(argv0: str, base: list[str]) -> bool:
    """Whether the verb reached by *base* forwards to a wrapped command."""
    verbs = ".".join(token for token in base if not token.startswith("-"))
    return verbs in _WRAPPERS.get(argv0, frozenset())


# --- colour: force a tool's own switch when the environment isn't enough ------
#
# `_host.child_env` already writes the force set into every child, which covers
# the modern tools. This table is only for the ones that ignore the environment
# and take a flag instead (git). It is *probed*, not hand-written: `fm
# tools.color` runs each tool with colour forced on and off and records the
# verdict in `_colordata.py`, which is loaded below.


class _ColorFlag(NamedTuple):
    """How one tool forces colour on (or off) with its own switch.

    `on`/`off` are the tokens to add for each direction — either may be empty
    when a tool needs telling only one way (git's `auto` default is already
    monochrome when piped, so it has no `off`). `pre_verb` places the tokens
    right after the executable and before the verb (git's `-c color.ui=…` is a
    global, not a `diff` option); otherwise they ride with the call's flags.
    """

    on: tuple[str, ...]
    off: tuple[str, ...] = ()
    pre_verb: bool = False


def _load_color() -> dict[str, dict[str, _ColorFlag]]:
    """Build the forcing table from probed `_colordata.py`, keyed
    `{argv0: {verb: flag}}` (verb `""` = tool-wide). Only a tool that a
    direction reports `flag` for gets an entry; everyone else obeys the
    environment. A missing data file degrades to no flag-forcing (env only).
    """
    try:
        from livery.toolroom import _colordata
    except ImportError:  # not yet generated — env forcing still works
        return {}
    table: dict[str, dict[str, _ColorFlag]] = {}
    for argv0, on, off, flag_on, flag_off, pre_verb in _colordata.COLOUR.values():
        if on == "flag" or off == "flag":
            table.setdefault(argv0, {})[""] = _ColorFlag(
                on=flag_on if on == "flag" else (),
                off=flag_off if off == "flag" else (),
                pre_verb=pre_verb,
            )
    return table


_COLOR: dict[str, dict[str, _ColorFlag]] = _load_color()


def _color_flag(argv0: str, base: list[str]) -> _ColorFlag | None:
    """The colour switch for the verb reached by *base*, or None.

    An exact verb match wins; a `""` entry is the tool-wide fallback (git forces
    colour the same way for every subcommand).
    """
    table = _COLOR.get(argv0)
    if not table:
        return None
    verb = ".".join(token for token in base if not token.startswith("-"))
    return table.get(verb) or table.get("")


def _color_tokens(
    argv0: str, base: list[str], kwargs: dict[str, Any], on: bool
) -> _ColorFlag:
    """The colour tokens to inject for this call — `_ColorFlag((), ())` for none.

    Injected into the *executed* argv only. Hosted, that keeps it out of the
    shown line — `.command` (what `recording()` asserts) stays the tool's own
    call, while `.raw` / `--verbose` show the literal `git -c color.ui=always …`
    that ran. Standalone there is no second spelling to keep, so a `Result`
    renders the argv it spawned. Skipped when the caller spells colour
    themselves — `color=`/`colour=`/`colors=`/`colours=` — so a deliberate
    choice always wins.
    """
    if any(k.rstrip("_") in ("color", "colour", "colors", "colours") for k in kwargs):
        return _ColorFlag((), ())
    flag = _color_flag(argv0, base)
    if flag is None:
        return _ColorFlag((), ())
    tokens = flag.on if on else flag.off
    return _ColorFlag(tokens, (), flag.pre_verb) if tokens else _ColorFlag((), ())


def _emit(
    kwargs: dict[str, Any], tool: str = "", *, single_dash: bool = False
) -> Iterator[tuple[str, str | None]]:
    """The one translation: keyword arguments → `(flag, value)` tokens.

    `value` is None for a switch (`--fix`) or a negation (`--dirty`); a
    string for a valued option; and the pair repeats for each item of a
    list. Both the executed argv (`_flags`) and the shown command line
    (`_show_parts`) are built from this, so they can never disagree about
    what a call means — only about how to spell it.

    *single_dash* spells every long flag with one dash (`-fix`, not `--fix`) for
    Go-style tools whose `flag` package rejects the double-dash form.
    """
    for key, value in kwargs.items():
        if value is None or value is False:
            continue
        name = key.rstrip("_").replace("_", "-")
        if value is off:
            yield _negation(tool, key, single_dash=single_dash), None
            continue
        flag = f"-{name}" if single_dash or len(name) == 1 else f"--{name}"
        if value is True:
            yield flag, None
            continue
        values = value if isinstance(value, (list, tuple)) else [value]
        for item in values:
            # A str passes through *as its own type*: a subclass that marks
            # itself (footman's Secret) must reach the argv intact, or
            # display-time redaction has nothing left to redact.
            yield flag, item if isinstance(item, str) else str(item)


def _spell(flag: str, value: str | None, *, attach_long: bool) -> list[str]:
    """One option as argv tokens — the shared placement rule.

    A long option and its value can be one token (`--select=E`) or two
    (`--select E`). Two reads better, but three cases force *one*:

    * a value that starts with a dash would be read as the next option —
      `--format -%h` fails, `--format=-%h` works;
    * an optional-value option can't tell its value from the next
      positional across a space — `--abbrev 4` is ambiguous, `--abbrev=4`
      is not;
    * a short option's value, when it starts with a dash, must be
      concatenated (`-k-expr`), never `-k=expr`, which most tools reject.

    Execution attaches every long option (`attach_long=True`) so the second
    case is covered for tools footman can't inspect; the shown line only
    attaches where a space would actually break it, staying readable.
    """
    if value is None:
        return [flag]
    long = flag.startswith("--")
    dash = value.startswith("-")
    if long and (attach_long or dash):
        return [_attach(f"{flag}=", value)]
    if not long and dash:
        return [_attach(flag, value)]
    return [flag, value]


def _attach(prefix: str, value: str) -> str:
    """`--flag=value` (or `-kvalue`) as one token, keeping the value's type.

    A value that redacts itself — footman's `Secret`, duck-typed on
    `reveal` so toolroom never imports it — must survive the join: the
    attached token is what execution carries, and display-time redaction
    can only redact what still holds the marker. The whole token turns
    secret, which errs in the safe direction — a redactor shows `***`
    where `--token=…` stood. A subclass that cannot be rebuilt from one
    string falls back to the plain join rather than fail the call.
    """
    joined = prefix + value
    if not hasattr(value, "reveal"):
        return joined
    try:
        # Deliberately re-wrap in the VALUE's own type: a Secret-like
        # str subclass keeps its reveal() behaviour on the joined
        # result, and for a plain str this is the identity the
        # checker sees.
        return type(value)(joined)  # pyrefly: ignore[unnecessary-type-conversion]
    except Exception:
        return joined


def _flags(
    kwargs: dict[str, Any], tool: str = "", *, single_dash: bool = False
) -> list[str]:
    """Translate keyword arguments into CLI flags, for execution.

    Long options attach their value (`--select=E`) so an optional-value or
    dash-leading value can never be misread; short options stay separated
    unless the value forces concatenation. The shown line (`_show_parts`)
    spells the same call more readably; only the tokens differ.
    """
    argv: list[str] = []
    for flag, value in _emit(kwargs, tool, single_dash=single_dash):
        argv += _spell(flag, value, attach_long=True)
    return argv


def _show_parts(
    argv0: str,
    base: list[str],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    single_dash: bool = False,
) -> tuple[tuple[str, str], ...]:
    """The invocation as role-tagged tokens, for a readable, painted line.

    The same call the runtime executes, spelled for a human: options in
    their separated form (`--select E`, not `--select=E`) where a space is
    safe, attached only where separating would break the paste; values
    shell-quoted; every token tagged with its role so `_describe.paint_cli`
    can colour it the way `--help` colours a usage line.
    """
    parts: list[tuple[str, str]] = [("prog", argv0)]
    for token in base:
        # `base` holds the verb path, and — from `.flags()` — global flags
        # bound before a verb. A token that redacts itself (a Secret global,
        # kept whole by `_attach`) is checked *before* the partition below:
        # string operations on it answer in plain str, marker gone.
        if hasattr(token, "reveal"):
            parts.append(("opt", "***"))
        elif token.startswith("--") and "=" in token:
            flag, _, value = token.partition("=")
            parts.append(("opt", flag))
            parts.append(("value", _quote(value)))
        elif token.startswith("-"):
            parts.append(("opt", token))
        else:
            parts.append(("group", token))
    arg_parts = [("req", _shown(token)) for token in _positionals(args, argv0)]
    flag_parts: list[tuple[str, str]] = []
    for flag, value in _emit(kwargs, argv0, single_dash=single_dash):
        if value is None:
            flag_parts.append(("opt", flag))
            continue
        # Decide placement on the raw value (a dash leads), quote for the
        # shown text. Readable where a space is safe; attached only where
        # separating would produce a line that doesn't run.
        quoted = _shown(value)
        if value.startswith("-"):
            glue = "=" if flag.startswith("--") else ""
            flag_parts.append(("opt", f"{flag}{glue}{quoted}"))
        else:
            flag_parts.append(("opt", flag))
            flag_parts.append(("value", quoted))
    # A wrapper's flags come before the wrapped argv, mirroring execution.
    if _is_wrapper(argv0, base):
        parts += flag_parts + arg_parts
    else:
        parts += arg_parts + flag_parts
    return tuple(parts)


def _shown(value: str) -> str:
    """One caller value as the shown line spells it.

    A value that must be asked for explicitly is not shown by default: a
    `reveal`-carrying value (footman's `Secret`, duck-typed so toolroom
    never imports it) reads back as `***` — unquoted, because `***` needs
    no quoting and the real value never reaches the quoting. `str(secret)`
    and f-strings still pass in the clear: that is the caller unwrapping,
    which is footman's documented way to mean it.
    """
    return "***" if hasattr(value, "reveal") else _quote(value)


def _quote(text: str) -> str:
    """Quote a token so the shown line round-trips through a paste — POSIX via
    `shlex.quote`, Windows via stdlib `subprocess.list2cmdline` (which cmd and
    PowerShell can read, unlike shlex's POSIX single-quotes).
    """
    if _sys.platform == "win32":
        return _subprocess.list2cmdline([text])
    import shlex

    return shlex.quote(text)


def _positionals(args: tuple[Any, ...], tool: str) -> list[str]:
    """Positional arguments as argv tokens.

    A bare container is refused — `Argv` included, since a built command
    line in one positional slot is ambiguous between its two meant
    spellings, `*cmd` (tokens) and `cmd.posix()` (one quoted line).
    Everything else is `str()`-ed, which is what `Path` and `int` want —
    except a str, which passes as its own type: a subclass that marks
    itself (footman's Secret) must reach the argv intact for display-time
    redaction to have anything left to redact.
    """
    out: list[str] = []
    for arg in args:
        if isinstance(arg, _CONTAINERS):
            spread = "**" if isinstance(arg, dict) else "*"
            raise TypeError(
                _host.container_error(arg, tool, example=f"{tool}({spread}value)")
            )
        out.append(arg if isinstance(arg, str) else str(arg))
    return out


# Concrete containers only — never `Iterable`, which would catch `str` and
# explode it into characters (the same tuple `run()` refuses; the wording
# lives with it in `context.container_error`).
_CONTAINERS = (list, tuple, set, frozenset, dict)


def _console_entrypoint(name: str) -> Any | None:
    """The `[console_scripts]` EntryPoint named *name*, UNLOADED, or None.

    Returning the EntryPoint rather than its target keeps the tool's import
    deferred: the module is only imported when `.load()` is called, inside
    the callable footman runs. So a dry-run — or a branch you never take —
    imports nothing, while the existence check here (pure metadata, no tool
    code) is still cheap enough to decide subprocess-vs-in-process eagerly.
    """
    from importlib.metadata import entry_points

    for ep in entry_points(group="console_scripts", name=name):
        return ep
    return None


def _accepts_args(entry: Any) -> bool:
    """Can *entry* take the argument list directly (no sys.argv patching)?

    Click commands (`cli(args)`) and argv-parameter mains
    (`main(argv=None)`) both can — their first parameter is positional. Only
    a true zero-arg `main()` needs a `sys.argv` view, which the argv router
    serves per call inside a run — outside one it is patched process-
    global and therefore serialised.
    """
    import inspect

    try:
        sig = inspect.signature(entry)
    except (ValueError, TypeError):
        return False
    positional = (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.VAR_POSITIONAL,
    )
    return any(p.kind in positional for p in sig.parameters.values())


# Only the bare-call (outside-a-run) sys.argv patch needs serialising; inside
# a run the argv router gives each call its own view, and argument-accepting
# entries (the overwhelming majority) run fully in parallel.
_argv_lock = _threading.Lock()


# The run-control policy a tool's `.opts()` accepts — footman options that ride
# *beside* the call, never translated into tool flags. A closed vocabulary, like a
# task's `.opts()`, so `capture` here is unambiguously footman's (not a tool's own
# `--capture`, e.g. pytest's); a tool's own flags go in the call or `.flags()`.
_TOOL_OPTS = (
    "nofail",
    "in_process",
    "capture",
    "title",
    "cwd",
    "rel",
    "recorded",
    "timeout",
    "pre_record",
    "input",
    "env",
    "color",
)

# The colour ladder: an explicit `always`/`never` decides the call outright,
# `auto` reads the ambient tier (see `_host.color_on`). Explicit *beats*
# `NO_COLOR`, matching how a `--color=always` outranks it: an instruction aimed
# at this call is not the same kind of thing as an environment-wide preference,
# and a test that asserts on colour must not go grey on CI that exports it.
_COLOUR_MODES = ("auto", "always", "never")


class _Consumed:
    """The tombstone a fed `input=` leaves behind — see `_StdinPayload`."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<input consumed>"


_CONSUMED = _Consumed()
_consume_lock = _threading.Lock()


class _StdinPayload:
    """One `.opts(input=…)` is one payload, however the handle is used after.

    Chaining copies the policy *dict* (`_sub` → the constructor's
    `dict(policy)`), so the payload rides in this cell, shared **by
    reference** through every derived tool — the stored intermediate, each
    chained verb, the leaf that finally runs. Delivery is exactly-once
    across that whole family, taken atomically so parallel tasks sharing a
    handle can't both feed; whoever comes second meets the tombstone, and
    the caller turns it into a taught refusal rather than a silently-unfed
    child hanging on a stdin that never comes.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value: str | _Consumed = value

    def take(self) -> str | _Consumed:
        with _consume_lock:
            value, self._value = self._value, _CONSUMED
        return value


def _opts_overrides(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Validate `.opts()` policy kwargs against the closed tool-option vocabulary."""
    unknown = sorted(set(kwargs) - set(_TOOL_OPTS))
    if unknown:
        valid = ", ".join(_TOOL_OPTS)
        raise TypeError(
            f".opts() got unknown option(s) {unknown}; valid options are {valid}. A "
            f"tool's own flags go in the call — tools.ruff(fix=True) — or before a "
            f"verb via .flags()."
        )
    mode = kwargs.get("color")
    if mode is not None and mode not in _COLOUR_MODES:
        modes = ", ".join(repr(m) for m in _COLOUR_MODES)
        raise ValueError(
            f".opts(color={mode!r}) is not a colour mode; say one of {modes}. "
            f"A tool's own colour flag goes in the call — "
            f"tools.ruff.check(color='always')."
        )
    out = dict(kwargs)
    if out.get("input") is not None:
        # Into the shared cell, so the exactly-once promise survives chaining
        # (the policy dict is copied per sub-tool; the cell is not).
        out["input"] = _StdinPayload(out["input"])
    return out


class Tool:
    """One command-line tool; see the module docstring for the grammar.

    `in_process` (footman run-control set via `.opts()`, like `nofail`) runs a
    Python tool inside footman's process instead of spawning: the tool's own
    `[console_scripts]` entry point is resolved and invoked — the same
    no-transcription contract, minus the interpreter spawn. Beyond speed
    this matters for correctness: on macOS, SIP strips `DYLD_*` from child
    processes, so a tool that needs Homebrew's native libraries (mkdocs
    with cairo, say) can only see them in-process, where an env var set
    before the import sticks. Tools constructed with `in_process=True`
    default to it and fall back to a subprocess when no entry point is
    installed; `.opts(in_process=True)` is a demand and errors if the entry
    can't be found. Parallelism survives: entries that accept an
    argument list (click commands, `main(argv=None)` — detected from the
    signature) are called directly and capture through the per-task stdout
    router; even a legacy zero-arg `main()` parallelises — the argv router
    serves each call its own view of `sys.argv`.

    The in-process lane is a host feature — those routers are footman's.
    Standalone, a tool constructed with `in_process=True` simply spawns
    (same command, same semantics), and `.opts(in_process=True)` — a
    demand — is a taught refusal.
    """

    # The stub declares Tool generic over what a call returns, so a task
    # annotation may say `Tool[Result]` — and the binder evaluates
    # annotations (`eval_str`), so the subscript must be legal here too.
    __class_getitem__ = classmethod(_types.GenericAlias)

    def __init__(
        self,
        name: str,
        *base: str,
        in_process: bool = False,
        path: str = "",
        entry: str = "",
        single_dash: bool = False,
        version_argv: tuple[str, ...] = ("--version",),
        policy: dict[str, Any] | None = None,
    ) -> None:
        self._argv0 = name  # the name shown, and the console script looked up
        self._base = list(base)
        self._prefer_in_process = in_process
        # Run-control policy set via `.opts()` (nofail/in_process/capture/title),
        # resolved at call time. Rides the chain, so `git.opts(nofail=True).push()`
        # works. Kept apart from the tool's flags: policy vs work.
        self._opts = dict(policy or {})
        self._rebound = False
        # The executable actually run, when it isn't the name: `tools.python`
        # runs `sys.executable`, not whatever `python` is on PATH.
        self._path = path or name
        # An in-process callable to prefer over the console script, spelled
        # `module:attr`. pytest's console entry is the private zero-arg
        # `_console_main`, whose broken-pipe branch dup2s /dev/null over the
        # process's real stdout — footman's, and every sibling task's.
        # `pytest.main` is the public argument-accepting API and does none of
        # that, so it is recorded here.
        self._entry = entry
        # A Go-style tool whose `flag` package wants one dash on long flags
        # (`eclint -fix`, not `--fix`). Tool-wide: Go's flag package is uniform,
        # so this rides every flag the tool emits, chained subcommands included.
        self._single_dash = single_dash
        # How this tool is asked its version. Nearly everything answers
        # `--version`; Windows `cmd` has no such flag and spells it `cmd /c ver`.
        self._version_argv = tuple(version_argv)

    def _sub(self, *tail: str, cls: type[Tool] | None = None) -> Tool:
        """A chained tool sharing this one's executable, entry, mode, and policy.

        The class rides along by default, so a verb chained off an `ArgvTool`
        keeps building rather than quietly reverting to a running handle.
        """
        t = (cls or type(self))(
            self._argv0,
            *self._base,
            *tail,
            in_process=self._prefer_in_process,
            path=self._path,
            entry=self._entry,
            single_dash=self._single_dash,
            version_argv=self._version_argv,
            policy=self._opts,
        )
        t._rebound = self._rebound
        return t

    def __getattr__(self, verb: str) -> Tool:
        if verb.startswith("_"):
            raise AttributeError(verb)
        return self._sub(verb.replace("_", "-"))

    def opts(self, **overrides: Any) -> Tool:
        """Set footman run-control policy for the call — the same `.opts()` a task
        has, mirroring its policy-vs-work split. A closed vocabulary
        (`nofail`, `in_process`, `capture`, `title`, `step`) that rides *beside*
        the call, never becoming a tool flag:

            git.opts(nofail=True).push()          # tolerate a non-zero exit
            pytest.opts(capture=False)("-s")      # stream this run live
            git.opts(recorded=False).rev_parse("HEAD")  # a value read, not an event

        `recorded=False` is the one that changes what the *run* sees rather than
        how the tool is invoked: the call runs in the task's directory and
        environment as always, but reports nothing — no receipt, no row in
        `--json`, no `recording()` entry — and hands back its `Result`. Reach
        for it when the call is how the task *knows* something rather than
        something the task *did*.

        Because it is a fixed set, `capture` here is unambiguously footman's — a
        tool's own `--capture` (pytest's) still goes in the call. The overridden
        options ride the chain and win at call time. For a tool's *own* global
        options that must precede a verb, use `.flags()`.

        `color="always"|"never"` decides this call's colour outright, and
        `"auto"` (the default) takes the ambient answer — the run's inside a
        footman run, the user's environment outside one. An explicit choice
        beats `NO_COLOR`, which is what makes a colour assertion hold on CI
        that exports it:

            git.opts(color="always").diff()       # colour, whatever the ambient

        The decision travels on the call rather than through the environment
        because `os.environ` is one per process and calls are not — two
        threads wanting different answers can both have one this way. It
        drives whichever half of the forcing each tool needs: the variables
        for those that read them, their own switch for those that don't. For
        a tool's own colour flag, spell it in the call
        (`ruff.check(color="always")`) and toolroom keeps out of the way.

        `env=` is the child's environment exactly as `run(env=…)` means it —
        what you pass is what the child gets — and like the rest of the set it
        rides the chain and replays. `input=` feeds the child's standard input,
        and unlike the rest it is **consumed**: stdin is consumable, so the
        payload is delivered exactly once however the handle is chained or
        shared, and a second call is a taught refusal rather than a
        silently-unfed child hanging on a stdin that never comes. Re-opt with
        a fresh payload per call:

            uv.pip.install.opts(input=requirement)("-r", "-")
        """
        t = self._sub()
        t._opts = {**self._opts, **_opts_overrides(overrides)}
        return t

    def at(self, path: str | _Path) -> Tool:
        """Rebind this handle to an executable — the *identity* channel,
        beside `.opts()` (policy) and `.flags()` (the tool's own argv).

        Everything else rides along: verbs, bound flags, policy — including
        a pending `input=` payload, whose cell is shared, not copied. What
        changes is *what runs*: `tools.python.at(venv_python)` is that
        venv's interpreter carrying python's whole typed surface, and the
        shown command line keeps the tool's own name.

        The in-process lane runs *this* interpreter, which is exactly what
        an `.at()` handle says not to do — so the handle always spawns, and
        an explicit `in_process=True` on one is a taught refusal.
        """
        t = type(self)(
            self._argv0,
            *self._base,
            in_process=False,
            path=str(path),
            entry=self._entry,
            single_dash=self._single_dash,
            version_argv=self._version_argv,
            policy=self._opts,
        )
        t._rebound = True
        return t

    def flags(self, **kwargs: Any) -> Tool:
        """Bind a tool's own global options *before* the next subcommand.

        Some flags belong to the tool, not the verb, and must precede it:
        `docker --host tcp://x ps` works, `docker ps --host tcp://x` does
        not. `flags` places them at the current point in the chain, so they
        land ahead of whatever verb follows and ahead of its arguments:

            tools.docker.flags(host="tcp://x").ps(all=True)
            #  -> docker --host=tcp://x ps --all

        The flags are translated by the same rules as any call, and the
        returned tool keeps chaining, so `.flags(...)` composes mid-stream.
        (footman run-control — `nofail`, `capture`, … — goes in `.opts()`.)
        """
        return self._sub(*_flags(kwargs, self._argv0, single_dash=self._single_dash))

    @property
    def argv(self) -> ArgvTool:
        """Build this call's command line instead of running it.

        Insert `.argv` right before the parentheses — the call is otherwise
        spelled exactly as it would be to run it, same verb, same flags,
        same completion — and it hands back an `Argv` of raw tokens rather
        than executing:

            mkdocs.gh_deploy.argv(force=True)
            #  -> Argv(['mkdocs', 'gh-deploy', '--force'])

        The tokens serialise at the point they cross into a shell —
        `cmd.posix()` / `cmd.windows()` — or splat on as tokens (`*cmd`):

            inner = docker.compose.up.argv(detach=True)
            ssh("app@host", inner.posix())

        Like `.opts()`, it may sit anywhere earlier in the chain
        (`docker.argv.compose.up(…)` builds too); before the parentheses is
        where it reads best and how the docs spell it. The line it builds is
        colour-free: the forced-colour switch a few tools need is injected
        when spawning, so what runs may carry one more flag than this
        returns.
        """
        return _cast("ArgvTool", self._sub(cls=ArgvTool))

    def _tokens(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> list[str]:
        """This call as argv tokens, without running it.

        The same translation and the same placement rule `__call__` uses —
        `_flags`, `_positionals`, and the wrapper-verb ordering — so a built
        line cannot drift from what a real call would spawn.

        The line leads with the tool's *name*, not `self._path`: a built
        command line is made to be handed somewhere else, where a local
        absolute path (`tools.python` resolves to this interpreter) means
        nothing. That matches the shown line, which keeps the tool's own
        name for `.at()` handles too.
        """
        flags = _flags(kwargs, self._argv0, single_dash=self._single_dash)
        positionals = _positionals(args, self._argv0)
        if _is_wrapper(self._argv0, self._base):
            return [self._argv0, *self._base, *flags, *positionals]
        return [self._argv0, *self._base, *positionals, *flags]

    def __call__(self, *args: Any, **kwargs: Any) -> Result:
        # Run-control comes from `.opts()` (policy), never the call — so every
        # kwarg here is a tool flag, with no reserved name to collide with a real
        # option (pytest's own `--capture`, say).
        nofail = self._opts.get("nofail", False)
        capture = self._opts.get("capture", True)
        title = self._opts.get("title", None)
        in_process = self._opts.get("in_process", None)
        cwd_opt = self._opts.get("cwd", None)
        rel_opt = self._opts.get("rel", None)
        recorded = self._opts.get("recorded", True)
        timeout = self._opts.get("timeout", None)
        pre_record = self._opts.get("pre_record", None)
        env_opt = self._opts.get("env", None)
        # The mode travels to the executor unresolved — `auto` means "follow
        # whoever owns the ambient", and hosted that is the run. Only the argv
        # half is decided here, because a switch has to be a switch.
        colour_mode = self._opts.get("color", "auto")
        colour_on = _host.colour_wanted(colour_mode)
        # `input=` is consumed *at entry*: one `.opts(input=…)` is one
        # delivery, wherever in the chain the call lands. Entry rather than
        # after execution, so a dry-run or `recording()` rehearsal consumes
        # exactly as the run it predicts would. `.opts(input=…)` re-arms.
        cell = self._opts.get("input", None)
        input_: str | None = None
        if cell is not None:
            taken = cell.take()
            if isinstance(taken, _Consumed):
                raise TypeError(
                    f"{self._argv0}: this handle's input= was already fed — "
                    f"stdin is consumable, so a payload is delivered exactly "
                    f"once. Re-opt with a fresh payload per call: "
                    f"{self._argv0}.opts(input=…)(…)"
                )
            input_ = taken
        flags = _flags(kwargs, self._argv0, single_dash=self._single_dash)
        positionals = _positionals(args, self._argv0)
        wrapper = _is_wrapper(self._argv0, self._base)

        def _tail(fl: list[str]) -> list[str]:
            # A wrapper verb (`uv run`, `docker exec`) forwards everything after
            # its own arguments to a child, so this call's flags must precede the
            # positionals — otherwise `--frozen` lands on `pytest`, not `uv`.
            if wrapper:
                return [*self._base, *fl, *positionals]
            return [*self._base, *positionals, *fl]

        # `parts` is the shown/recorded command line and never carries a forced
        # colour switch, so `.command`/`recording()` stay the tool's own call.
        parts = _show_parts(
            self._argv0, self._base, args, kwargs, single_dash=self._single_dash
        )
        # The call as the caller wrote it, pre-render — a False keyword the
        # argv omits, a Path still a Path. The dict is copied at this
        # boundary so a later mutation cannot rewrite the record.
        handed = (args, dict(kwargs))
        # The in-process argv/tail are colour-free: an in-process tool reads the
        # run-wide colour from the environment (set once at the run boundary), so
        # only a *spawned* tool that ignores the environment (git) needs its own
        # switch. Execution runs the real executable (`python` → sys.executable).
        tail = _tail(flags)
        argv = [self._path, *tail]

        def _spawn() -> Result:
            # The forced colour switch, subprocess-only, into the executed argv:
            # a pre-verb global (`git -c color.ui=always`) leads; a verb-scoped
            # flag rides with the others. `.raw`/`--verbose` show what ran.
            colour = _color_tokens(self._argv0, self._base, kwargs, colour_on)
            if not colour.on:
                spawned = argv
            elif colour.pre_verb:
                spawned = [self._path, *colour.on, *tail]
            else:
                spawned = [self._path, *_tail([*flags, *colour.on])]
            return _host.run(
                spawned,
                parts=parts,
                exact=tuple(spawned),
                handed=handed,
                nofail=nofail,
                capture=capture,
                input=input_,
                env=env_opt,
                title=title,
                pre_record=pre_record,
                recorded=recorded,
                timeout=timeout,
                cwd=cwd_opt,
                rel=rel_opt,
                color=colour_mode,
            )

        wanted = self._prefer_in_process if in_process is None else in_process
        if wanted and self._rebound:
            raise ValueError(
                f"{self._argv0}: in_process=True on an .at() handle — the "
                f"in-process lane runs this interpreter, and .at() names a "
                f"different executable. Drop one of them."
            )
        if wanted and timeout is not None:
            # A bound needs a process to bound: an in-process call has no
            # child to signal and no safe way to unwind a thread. Demote to
            # the subprocess twin — the same choice a foreign cwd forces
            # below, and the timeout is the thing the caller actually asked
            # for.
            _host.note(
                f"note: {self._argv0}: ran as subprocess — a timeout needs "
                f"a process to bound\n"
            )
            return _spawn()
        if wanted and not _host.hosted():
            # The in-process lane is a host feature: it needs footman's
            # stdout router and argv router to capture and parallelise.
            # Standalone, a *preference* degrades to the subprocess twin —
            # same command, same semantics — and a *demand* is a taught
            # refusal rather than an uncaptured surprise.
            if in_process is True:
                raise ValueError(
                    f"{self._argv0}: in_process=True needs a footman host — "
                    f"standalone toolroom always spawns. Drop in_process, "
                    f"or run under footman."
                )
            return _spawn()
        if wanted:
            from livery.footman import _globals as _pg
            from livery.footman.context import _target_cwd as _target_cwd_of
            from livery.footman.context import current as _current

            target = _target_cwd_of(_current(), cwd_opt, rel_opt)
            if target is not None and target.resolve() != _Path(_pg.real_getcwd()):
                # In-process can't apply a foreign cwd (footman never chdirs
                # in a parallel task): demote to the subprocess twin — same
                # command, same semantics, still fully parallel; the
                # in-process speedup is the only loss. A serial task's cwd is
                # really applied, so it compares equal and stays in-process.
                _host.note(
                    f"note: {self._argv0}: ran as subprocess — in-process "
                    f"can't apply cwd in parallel\n"
                )
                return _spawn()
            loader = self._inprocess_loader()  # metadata only — no import
            if loader is None:
                if in_process is True:  # a demand can't be met — fail fast
                    raise ValueError(
                        f"{self._argv0}: in_process=True, but no importable "
                        f"in-process entry ({self._entry or self._argv0!r})"
                    )
                return _spawn()  # prefer → subproc

            def _invoke() -> Any:
                entry = loader()  # the tool's import — deferred to execution,
                # so a dry-run of this call imports nothing.
                if _accepts_args(entry):
                    return entry(tail)  # click / main(argv): lock-free, parallel
                if _pg.active():
                    # The argv router: this call gets its own sys.argv view —
                    # lock-free, so even a legacy zero-arg main() parallelises.
                    with _pg.argv_override(argv):
                        return entry()
                with _argv_lock:  # bare calls outside a run: classic patch
                    saved = _sys.argv
                    _sys.argv = argv
                    try:
                        return entry()
                    finally:
                        _sys.argv = saved

            return _host.run(
                _invoke,
                parts=parts,
                exact=tuple(argv),
                handed=handed,
                nofail=nofail,
                capture=capture,
                # Forwarded so run()'s refusal teaches: an in-process tool
                # has no standard input to feed.
                input=input_,
                env=env_opt,
                title=title,
                pre_record=pre_record,
                recorded=recorded,
                timeout=timeout,
                cwd=cwd_opt,
                rel=rel_opt,
            )
        return _spawn()

    def _inprocess_loader(self) -> Any | None:
        """A callable that imports and returns the in-process target — or None
        when there is nothing to run in-process (so the call spawns instead).

        A recorded `entry` override wins over the console script: pytest's
        console entry is the private `_console_main`, whose broken-pipe branch
        redirects the process's real stdout, so `pytest.main` — pytest's
        public, argument-accepting API — is recorded as `pytest:main` and run
        instead. Availability is checked without importing (a dry-run of the
        call must import nothing); the import itself is deferred to the loader.
        """
        if self._entry:
            import importlib.util

            module = self._entry.partition(":")[0]
            try:
                if importlib.util.find_spec(module) is None:
                    return None
            except (ImportError, ValueError):
                return None

            def load() -> Any:
                import importlib

                mod, _, attr = self._entry.partition(":")
                return getattr(importlib.import_module(mod), attr)

            return load
        ep = _console_entrypoint(self._argv0)
        return ep.load if ep is not None else None

    def installed_version(self) -> tuple[int, ...]:
        """The version of the binary *this tool runs*, as a comparable int tuple.

        Asks the executable itself — `<tool> --version`, or whatever spelling
        the tool was declared with (`cmd /c ver`) — and reads the answer with
        the same parser the stub extractor uses, so the two can never disagree
        about a version string's grammar. Runs outside the task context, so
        `--dry-run` and `recording()` can't lie to it, and caches per process.

        It answers "what will this task actually invoke", which is *not* the
        version a stub header records. A stub says what extraction read, on
        whatever machine synced it, from whatever binary it resolved — the
        two differ whenever `PATH` and the extractor disagree (a Homebrew keg
        against `/usr/bin/git`, say). This one is the end-user question; the
        stub's is maintainer bookkeeping.
        """
        key = self._path
        if key not in _version_cache:
            argv = [self._path, *self._version_argv]
            out = _host.probe(
                argv,
                shown=(self._argv0, *self._version_argv),
                # Asking a tool its version must not make it check for a
                # newer one: gh does that from any command unless told not
                # to, so a task that guards on a version paid for a network
                # round trip to find out.
                env={**_os.environ, **_QUIET},
                timeout=30,
            )
            found = read_version(out.stdout or out.stderr)
            if out.code != 0 or not found:
                raise ValueError(f"could not read a version from `{' '.join(argv)}`")
            _version_cache[key] = version_tuple(found)
        return _version_cache[key]


class ArgvTool(Tool):
    """What `.argv` hands back: the same handle, whose call **builds**.

    A `Tool` in every other respect — verbs chain, `.opts()`/`.flags()`
    still apply, and `_sub` carries the class down the chain so a verb
    reached through `.argv` keeps building rather than quietly reverting to
    a running handle. It is a real class rather than a flag on `Tool` so
    the typing stub can say what a built call returns without pretending a
    `Tool` sometimes returns one thing and sometimes another.
    """

    __slots__ = ()

    def __call__(self, *args: Any, **kwargs: Any) -> Result:
        # Builds and answers before anything with a side effect happens —
        # notably before a pending `input=` is consumed, since building a
        # command line feeds no child. The cast keeps the inherited
        # signature: the stub is where the return type tells the truth.
        return _cast("Result", Argv(self._tokens(args, kwargs)))


# Curated instances — the ones with a non-obvious executable name live here;
# everything else works through the module fallback below.
ruff = Tool("ruff")
ruff_format = Tool("ruff", "format")
basedpyright = Tool("basedpyright")
uv = Tool("uv")
git = Tool("git")
docker = Tool("docker")
bun = Tool("bun")
mkdocs = Tool("mkdocs", in_process=True)  # macOS: DYLD_* only survives in-process
zensical = Tool("zensical", in_process=True)
coverage = Tool("coverage", in_process=True)
cspell = Tool("cspell")
prek = Tool("prek")
markdownlint = Tool("markdownlint-cli2")
claude = Tool("claude")
gh = Tool("gh")
# The remote command is a positional: transport, then payload. `-V` is the
# whole version surface (`--version` is an illegal option), answered on stderr.
ssh = Tool("ssh", version_argv=("-V",))
ssh_keygen = Tool("ssh-keygen")  # no version output of its own; ssh speaks for it
ssh_keyscan = Tool("ssh-keyscan")  # same: the OpenSSH release answers for it
eclint = Tool("eclint", single_dash=True)  # Go flag package: `-fix`, not `--fix`
djlint = Tool("djlint")
mypy = Tool("mypy")
ty = Tool("ty")
twine = Tool("twine")
git_changelog = Tool("git-changelog")
git_cliff = Tool("git-cliff")
build = Tool("pyproject-build")  # the `build` package's console script
cmake = Tool("cmake")
ninja = Tool("ninja")


# pytest runs in-process through the public, arg-accepting `pytest.main`, not
# its private `_console_main` console script (which redirects the process's
# real stdout to /dev/null on a broken pipe). python always targets the
# running interpreter, whatever `python`/`python3` is (or isn't) on PATH; its
# stub is read from provisioned interpreters. There is no `sh`: a command as a
# single string is `run("…")` — footman splits and runs it (no shell). When you
# want a shell, `run(..., shell=True)` is the front door (it resolves the
# interpreter per policy); these tools are the low-level primitive it builds on.
pytest = Tool("pytest", in_process=True, entry="pytest:main")
python = Tool("python", path=_sys.executable)

# The shells, invoked to run a command *string*: `tools.bash("echo $X | grep y")`
# runs `bash -c "…"`, so pipes, redirects, globbing and `$VAR` all work — the
# low-level "I want *this* shell" primitive (`run(..., shell="bash")` is the
# ergonomic front door). `-c` is the run-a-string flag for every one of them
# (pwsh takes it as an alias for -Command); Windows `cmd` uses `/c` and is
# Windows-only. footman autocompletes for all but cmd (cmd has no completion).
bash = Tool("bash", "-c")
zsh = Tool("zsh", "-c")
fish = Tool("fish", "-c")
pwsh = Tool("pwsh", "-c")
nu = Tool("nu", "-c")
cmd = Tool("cmd", "/c", version_argv=("/c", "ver"))  # no --version; `ver` is it


def __getattr__(name: str) -> Tool:
    # Any executable is a tool: `tools.terraform("plan")` needs no declaration.
    if name == "testing":
        # The one attribute that is a module, not a tool. `from livery.toolroom
        # import testing` consults this hook *before* the import system
        # falls back to the submodule, so without the redirect it would
        # mint a Tool("testing") and quietly shadow the test helpers.
        import importlib

        return _cast("Tool", importlib.import_module("livery.toolroom.testing"))
    if name.startswith("_"):
        raise AttributeError(name)
    return Tool(name.replace("_", "-"))
