# Autocomplete without the import bill.
#
# This stub is never imported at runtime — the bridge in tools.py stays a
# few dozen mechanical lines — but IDEs and type checkers read it, so the
# common verbs and flags of the curated tools autocomplete like duty's
# hand-written wrappers, at zero runtime cost.
#
# Two rules keep the stub honest:
# - every verb ends in `**flags: Any`, so a stub can *suggest* flags but
#   never forbid one — when a tool grows a flag, the bridge already speaks
#   it and the stub merely hasn't heard of it yet;
# - unknown verbs fall through to `Tool` via `__getattr__`, so nothing the
#   runtime accepts is a type error.
# Flag lists are *generated* from the installed tools — `fm tools.sync`
# writes one file per tool under `_stubs/`, and `fm tools.audit`
# fails when a checked-in stub and its tool disagree. Stub drift
# therefore degrades a hint, never a run.

# The private aliases (`_re`, `_run`, …) mirror tools.py: they keep those names
# out of the public namespace so `tools.run`/`tools.sys`/… resolve to Tools via
# __getattr__, and they satisfy the AST parity test (tools.py bindings ⊆ this
# stub). Only `_re` and `_threading` are referenced here; the rest exist purely
# for parity.
import os as _os
import re as _re
import subprocess as _subprocess  # noqa: F401
import sys as _sys  # noqa: F401
import threading as _threading
import types as _types  # noqa: F401
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path as _Path
from typing import Any, Generic, Literal, NamedTuple, Self, TypeAlias
from typing import cast as _cast  # noqa: F401

from typing_extensions import TypeVar

from livery.toolroom.tools import _host as _host
from livery.toolroom.tools._stubs.basedpyright import Basedpyright as Basedpyright

# One generated file per tool — `fm tools.sync` writes them from
# the installed binaries, and `audit` fails when they drift. They import
# `Tool` and the aliases from here, which a stub may do circularly.
from livery.toolroom.tools._stubs.bash import Bash as Bash
from livery.toolroom.tools._stubs.build import Build as Build
from livery.toolroom.tools._stubs.bun import Bun as Bun
from livery.toolroom.tools._stubs.claude import Claude as Claude
from livery.toolroom.tools._stubs.cmake import Cmake as Cmake
from livery.toolroom.tools._stubs.cmd import Cmd as Cmd
from livery.toolroom.tools._stubs.coverage import Coverage as Coverage
from livery.toolroom.tools._stubs.cspell import Cspell as Cspell
from livery.toolroom.tools._stubs.djlint import Djlint as Djlint
from livery.toolroom.tools._stubs.docker import Docker as Docker
from livery.toolroom.tools._stubs.eclint import Eclint as Eclint
from livery.toolroom.tools._stubs.fish import Fish as Fish
from livery.toolroom.tools._stubs.gh import Gh as Gh
from livery.toolroom.tools._stubs.git import Git as Git
from livery.toolroom.tools._stubs.git_changelog import GitChangelog as GitChangelog
from livery.toolroom.tools._stubs.git_cliff import GitCliff as GitCliff
from livery.toolroom.tools._stubs.markdownlint import Markdownlint as Markdownlint
from livery.toolroom.tools._stubs.mkdocs import Mkdocs as Mkdocs
from livery.toolroom.tools._stubs.mypy import Mypy as Mypy
from livery.toolroom.tools._stubs.ninja import Ninja as Ninja
from livery.toolroom.tools._stubs.nu import Nu as Nu
from livery.toolroom.tools._stubs.prek import Prek as Prek
from livery.toolroom.tools._stubs.pwsh import Pwsh as Pwsh
from livery.toolroom.tools._stubs.pytest import Pytest as Pytest
from livery.toolroom.tools._stubs.python import Python as Python
from livery.toolroom.tools._stubs.ruff import Ruff as Ruff
from livery.toolroom.tools._stubs.ruff_format import RuffFormat as RuffFormat
from livery.toolroom.tools._stubs.ssh import Ssh as Ssh
from livery.toolroom.tools._stubs.ssh_keygen import SshKeygen as SshKeygen
from livery.toolroom.tools._stubs.ssh_keyscan import SshKeyscan as SshKeyscan
from livery.toolroom.tools._stubs.twine import Twine as Twine
from livery.toolroom.tools._stubs.ty import Ty as Ty
from livery.toolroom.tools._stubs.uv import Uv as Uv
from livery.toolroom.tools._stubs.zensical import Zensical as Zensical
from livery.toolroom.tools._stubs.zsh import Zsh as Zsh

# The pre_record callback parameter: hosted, it receives footman's
# ResultView; the stub says Any so type-checking toolroom never requires
# footman installed. The callback is host-only either way.
_ResultView = Any

__version__: str

class Argv(list[str]):
    """A command line built but not run — raw tokens, an ordinary list[str]."""

    def posix(self) -> str: ...
    def windows(self) -> str: ...

class Result(int):
    """One standalone call's outcome: the exit code, plus streams and tokens.

    Hosted calls answer with footman's richer twin; both satisfy this
    surface, which is the one the stubs promise.
    """

    def __new__(
        cls,
        code: int,
        *,
        stdout: str = ...,
        stderr: str = ...,
        tokens: tuple[str, ...] = ...,
    ) -> Self: ...
    @property
    def ok(self) -> bool: ...
    @property
    def code(self) -> int: ...
    @property
    def stdout(self) -> str: ...
    @property
    def stderr(self) -> str: ...
    @property
    def command(self) -> str: ...
    def to_argv(self) -> Argv: ...

class ToolError(RuntimeError):
    """A standalone call exited non-zero without nofail; carries the Result."""

    result: Result
    def __init__(self, result: Result) -> None: ...

_QUIET: dict[str, str]

_argv_lock: _threading.Lock

_version_cache: dict[str, tuple[int, ...]]
_VERSION: _re.Pattern[str]

def read_version(text: str) -> str: ...
def version_tuple(version: str) -> tuple[int, ...]: ...

class _Off: ...

off: _Off

class _Consumed: ...

_CONSUMED: _Consumed
_consume_lock: _threading.Lock

class _StdinPayload:
    def __init__(self, value: str) -> None: ...
    def take(self) -> str | _Consumed: ...

# The three types every generated signature is written in — public and
# reader-facing, because they are what a hover shows. Wide on purpose: the
# bridge stringifies whatever it is handed (so `Path` and `int` work) and
# repeats the flag for each item of a sequence, so a narrower type would
# reject calls that demonstrably work.
#
# A boolean flag: True → --flag, off → the tool's own negation,
# False/None → omitted (which is what lets a task parameter's default flow
# straight through).
Flag: TypeAlias = bool | _Off | None
# An option that takes a value; a sequence repeats the flag per item.
Value: TypeAlias = (
    str
    | int
    | float
    | _os.PathLike[str]
    | Sequence[str | int | float | _os.PathLike[str]]
    | _Off
    | None
)
# An option whose value is *optional* — usable bare (`gpg_sign=True`, sign
# with the default key) or with a value (`gpg_sign="KEY"`). Both spell a
# valid command; the tool prints its placeholder attached to the flag,
# `--gpg-sign[=<key-id>]`, which is how footman tells the two apart.
ValuedFlag: TypeAlias = bool | Value

_NEGATIONS: dict[str, dict[str, str]]
_WRAPPERS: dict[str, frozenset[str]]

class _ColorFlag(NamedTuple):
    on: tuple[str, ...]
    off: tuple[str, ...] = ...
    pre_verb: bool = ...

def _load_color() -> dict[str, dict[str, _ColorFlag]]: ...

_COLOR: dict[str, dict[str, _ColorFlag]]
_COLOUR_MODES: tuple[str, ...]

def _negation(tool: str, key: str) -> str: ...
def _is_wrapper(argv0: str, base: list[str]) -> bool: ...
def _color_flag(argv0: str, base: list[str]) -> _ColorFlag | None: ...
def _color_tokens(
    argv0: str, base: list[str], kwargs: dict[str, Any], on: bool
) -> _ColorFlag: ...
def _emit(
    kwargs: dict[str, Any], tool: str = ...
) -> Iterator[tuple[str, str | None]]: ...
def _spell(flag: str, value: str | None, *, attach_long: bool) -> list[str]: ...
def _attach(prefix: str, value: str) -> str: ...
def _shown(value: str) -> str: ...
def _flags(kwargs: dict[str, Any], tool: str = ...) -> list[str]: ...
def _show_parts(
    argv0: str, base: list[str], args: tuple[Any, ...], kwargs: dict[str, Any]
) -> tuple[tuple[str, str], ...]: ...
def _quote(text: str) -> str: ...
def _console_entrypoint(name: str) -> Any | None: ...
def _accepts_args(entry: Any) -> bool: ...

_TOOL_OPTS: tuple[str, ...]

def _opts_overrides(kwargs: dict[str, Any]) -> dict[str, Any]: ...

_CONTAINERS: tuple[type, ...]

def _positionals(args: tuple[Any, ...], tool: str) -> list[str]: ...

# `default=` (PEP 696) is why a bare `Tool(...)` construction and a bare
# `Tool` annotation both mean `Tool[Result]` in every checker — running is
# the default, `.argv` is how `Tool[Argv]` arises. `typing_extensions` in a
# stub costs no runtime dependency: a .pyi is never imported.
_R = TypeVar("_R", default=Result)

# Generic over what a call returns. Every binding below fixes it to
# `Result`; `.argv` answers with the same surface over `Argv`. Making the
# parameter live on the *base* is what keeps every generated override —
# `__call__` returning its class TypeVar, `argv` re-parameterising — a
# plain covariant override rather than a Liskov violation needing
# suppression in four type checkers.
class Tool(Generic[_R]):
    _argv0: str
    _base: list[str]
    _prefer_in_process: bool
    _single_dash: bool
    _rebound: bool
    _opts: dict[str, Any]
    def __init__(
        self,
        name: str,
        *base: str,
        in_process: bool = False,
        path: str = ...,
        entry: str = ...,
        single_dash: bool = False,
        version_argv: tuple[str, ...] = ...,
        policy: dict[str, Any] | None = None,
    ) -> None: ...
    def __getattr__(self, verb: str) -> Tool[_R]: ...
    # footman run-control policy — a closed vocabulary that rides beside the
    # call (never a tool flag). Returns Self, so a generated tool keeps its verb
    # completions: `git.opts(nofail=True).push()`.
    #
    # The options are forwarded verbatim to `run()`, so they carry `run()`'s
    # types — the four it treats as "unset" take None, which is what a caller
    # computing one (`timeout=cfg.timeout`, `cwd=None if inline else build_dir`)
    # passes. `test_tools.py` enforces the match.
    def opts(
        self,
        *,
        nofail: bool = ...,
        in_process: bool | None = ...,
        capture: bool = ...,
        input: str | None = ...,
        env: dict[str, str] | None = ...,
        title: str | None = ...,
        cwd: str | _Path | None = ...,
        rel: str | _Path | None = ...,
        recorded: bool = ...,
        timeout: float | None = ...,
        pre_record: Callable[[_ResultView], None] | None = ...,
        color: Literal["auto", "always", "never"] = ...,
    ) -> Self: ...
    # A tool's own global options, bound before the next subcommand
    # (`docker.flags(host="x").ps()`). Generated stubs override it with the
    # tool's typed globals; the base takes any flag.
    def at(self, path: str | _Path) -> Self: ...
    def flags(self, **flags: Any) -> Self: ...
    # Build this call's command line instead of running it — `.argv` slots in
    # right before the parentheses. Generated stubs override it with the
    # tool's own class re-parameterised to return `Argv` (a covariant
    # narrowing, since every generated class derives from `Tool[_R]`), so a
    # built call keeps its flag checking; the base answers with the untyped
    # handle, whose calls all return `Argv`.
    @property
    def argv(self) -> Tool[Argv]: ...
    def __call__(self, *args: Any, **flags: Any) -> _R: ...
    def installed_version(self) -> tuple[int, ...]: ...

# The building handle: a `Tool` whose calls answer in `Argv`. The class adds
# nothing the parameterisation doesn't say — it exists so the runtime has a
# concrete class to chain through `_sub`, and the annotation `Tool[Argv]`
# is how the stubs spell it.
class ArgvTool(Tool[Argv]): ...

# Parameterised by what a call returns: `Result` here, and `.argv` re-spells
# the same class over `Argv` — one flag block serving both the run and the
# build path.
ruff: Ruff[Result]
ruff_format: RuffFormat[Result]
basedpyright: Basedpyright[Result]
uv: Uv[Result]
git: Git[Result]
docker: Docker[Result]
bun: Bun[Result]
mkdocs: Mkdocs[Result]
zensical: Zensical[Result]
coverage: Coverage[Result]
cspell: Cspell[Result]
prek: Prek[Result]
markdownlint: Markdownlint[Result]
claude: Claude[Result]
gh: Gh[Result]
ssh: Ssh[Result]
ssh_keygen: SshKeygen[Result]
ssh_keyscan: SshKeyscan[Result]
eclint: Eclint[Result]
djlint: Djlint[Result]
mypy: Mypy[Result]
ty: Ty[Result]
twine: Twine[Result]
git_changelog: GitChangelog[Result]
git_cliff: GitCliff[Result]
build: Build[Result]
cmake: Cmake[Result]
ninja: Ninja[Result]
pytest: Pytest[Result]
python: Python[Result]
bash: Bash[Result]
zsh: Zsh[Result]
fish: Fish[Result]
pwsh: Pwsh[Result]
nu: Nu[Result]
cmd: Cmd[Result]

def __getattr__(name: str) -> Tool[Result]: ...
