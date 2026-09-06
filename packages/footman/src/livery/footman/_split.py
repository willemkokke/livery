"""Separator-free chain splitting, driven purely by the manifest.

`fm build lint --fix test` is split into three independent segments with no
separator at all — duty's muscle memory, but with real flags and positionals.
A task's *address* is always a single dotted token (`fm docs.serve`), so the
first word of every segment names its task completely — no group descent.
The manifest gives the splitter exact knowledge of every task's shape, which
makes the split deterministic under six rules (see `NOTES`):

1. params with defaults are options, never positionals (the load-bearing rule);
2. required positionals are consumed by exact arity, eagerly validated;
3. options bind to their own segment, and a value is always `=`-attached
   (`--target=prod`, `-j=4`) — a bare word is a task or a positional, a bare
   `--x`/`-x` is a flag, so every token reads without arity knowledge;
4. list options repeat the flag (`--tag=a --tag=b`) or comma-join (`--tag=a,b`);
5. variadic / `--` passthrough segments are terminal; `+` is the always
   available explicit boundary;
6. globals precede the first task name.

Every error names the task, states the expectation, and proposes the fix —
error messages are product surface here, not diagnostics. The space form of
a nested address (`fm docs serve`) is permanently *taught against*, never
parsed: the error path detects it and answers with the dotted spelling.
"""

from __future__ import annotations

import contextlib
import difflib
import os
from collections.abc import Callable, Generator, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal

from livery.footman import _coerce, registry
from livery.footman.params import between as _between
from livery.footman.params import default as _computed


def _close1(a: str, b: str) -> bool:
    """Damerau-Levenshtein distance exactly 1: one substitution, insertion,
    deletion, or adjacent transposition — the "pyhton" shapes a hurried hand
    actually types. Bounded and cheap; called only on a group default's
    positional values against its child names."""
    if a == b:
        return False
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        diffs = [i for i in range(la) if a[i] != b[i]]
        if len(diffs) == 1:
            return True  # substitution
        return (
            len(diffs) == 2
            and diffs[1] == diffs[0] + 1
            and a[diffs[0]] == b[diffs[1]]
            and a[diffs[1]] == b[diffs[0]]
        )  # adjacent transposition
    short, long_ = (a, b) if la < lb else (b, a)
    i = 0
    while i < len(short) and short[i] == long_[i]:
        i += 1
    return short[i:] == long_[i + 1 :]  # one insertion/deletion


def _did_you_mean(word: str, known: Iterable[str]) -> str:
    """A ` — did you mean 'x'?` suffix when *word* closely matches a known name.

    Empty when nothing is close, so a genuine typo never gets false-confident
    advice. The one idiom behind every not-found message (task, option, choice).
    """
    close = difflib.get_close_matches(word, list(known), n=1)
    return f" — did you mean {close[0]!r}?" if close else ""


def _owners_of(tree: dict[str, Any], flag: str) -> list[str]:
    """Every task address whose own options include *flag*.

    Walked only when a refusal is already certain, so the common path
    never pays for it."""
    found: list[str] = []

    def walk(node: dict[str, Any], prefix: str) -> None:
        for name, task in node["tasks"].items():
            if any(
                "--" + str(p["name"]) == flag
                for p in task["params"]
                if p["kind"] in ("flag", "option")
            ):
                found.append(f"{prefix}{name}")
        for name, sub in node["groups"].items():
            walk(sub, f"{prefix}{name}.")

    walk(tree, "")
    return found


def _unknown_global(
    name: str,
    known: dict[str, str],
    tree: dict[str, Any] | None = None,
    rest: Sequence[str] = (),
) -> str:
    """The teaching for a dash token that is not a global option.

    Two shapes are muscle memory rather than typos, and both deserve their
    own sentence instead of "unknown option". A short option wearing its
    value (`-j1`, the `make -j4` habit) gets the same teaching the spaced
    form already gets — one canonical spelling, taught from whichever way a
    hand reaches for it. Combined shorts (`-sq`) get told that footman does
    not combine them, rather than being read as a name nobody wrote.
    """
    if len(name) > 2 and name[0] == "-" and name[1] != "-":
        if f"-{name}" in known:
            # `-jobs` is a long name missing a dash, and reading it as the
            # short `-j` fused with `obs` produced the comic hint
            # "did you mean -j=obs?" — name the spelling the hand meant.
            return f"{name} is missing a dash — did you mean -{name}?"
        head, tail = name[:2], name[2:]
        kind = known.get(head)
        if kind == "option":
            return f"{head} takes its value attached — did you mean {head}={tail}?"
        if kind == "flag" and all(f"-{c}" in known for c in name[1:]):
            spelled = " ".join(f"-{c}" for c in name[1:])
            return (
                f"{name} combines short options, which footman does not read "
                f"— write them apart: {spelled}"
            )
    if provider := _own_plugin_flags().get(name):
        # Not unknown — unmounted. The generic sentence sends someone hunting
        # for a spelling mistake in a flag they spelled correctly.
        return (
            f"{name} comes from {provider}, which this project has not "
            f'mounted — add plugin("{provider}") to tasks.py'
        )
    if tree is not None and (owners := _owners_of(tree, name)):
        # Not a global at all: a task option, written where globals live.
        # The generic hint points the wrong way here — the fix is to move it
        # RIGHT, past the task that owns it. Name the task they actually
        # typed when one of the owners is in the line; several owners and
        # none of them typed is the only case that has to stay a list.
        typed = [owner for owner in owners if owner in rest]
        whose = typed[0] if typed else owners[0] if len(owners) == 1 else ""
        if whose:
            return (
                f"{name} is an option of {whose}, not a global — it goes "
                f"after the task name: {whose} {name}"
            )
        listed = ", ".join(owners[:3]) + (", …" if len(owners) > 3 else "")
        return (
            f"{name} is a task option, not a global — it goes after the task "
            f"that takes it ({listed})"
        )
    return f"unknown global option {name} (global options go before the first task)"


def _unexposed(dotted: str, tree: dict[str, Any]) -> str:
    """Why a real task refuses from here — resolved only once it has.

    Which side the refusal comes from is knowable without storing it: a
    manifest built for a project can only be withholding a `global_only`
    task, one built outside can only be withholding a `project_only` one.

    The generic form on purpose: footman knows the task does not belong
    here, and it does not know what a particular brand would have you do
    about it. Outside a project it names the file it looked for, which is
    the fact that actually resolves the confusion, because the reader is
    usually a tool that started in the wrong directory rather than a person
    wondering how to make a project.
    """
    from livery.footman import _app

    if tree.get("project"):
        return f"{dotted} runs only outside a project"
    return (
        f"{dotted} needs a project — no {_app._brand.tasks_file} found here "
        f"or in any parent of {os.getcwd()}"
    )


def _misplaced_global(token: str) -> str | None:
    """The teaching message when *token* is really one of the GLOBALS.

    A global option found after a task name is a position mistake, not an
    unknown name — so name the real problem and its fix instead of guessing
    at close matches. Only fires for *unknown* task options: a task param
    that shares a global's name still wins by position, as it should.
    """
    name = token.split("=", 1)[0]
    if name not in _GLOBAL_KIND:
        return None
    canon = _CANON.get(name, name)
    label = name if name == canon else f"{name} ({canon})"
    return f"{label} is a global option — it goes before the first task name"


class ChainError(Exception):
    """A malformed command line, carrying a teaching message for the user.

    `unknown` carries the unresolved head token when the failure was a
    task that isn't there — structured, so a catcher can add a remedy (the
    brand's built-ins) without parsing its own error text."""

    unknown: str | None = None


def _default_jobs() -> int:
    """The parallel width nobody chose, imported at the call rather than at
    module scope — `_progress` takes `Segment` from here, so importing it up
    top would close the cycle."""
    from livery.footman._progress import default_jobs

    return default_jobs()


# Global options bind to `fm` itself and must precede the first task name
# (`--help`/`-h` is the one exception: anywhere before `--`, it wins).
#
# Declared as instances of the SAME class plugin globals use — one option
# model — with core constructed off the registration carriage: core options
# are not contributions riding a mount, they are the runner itself. The tuple
# table below is a DERIVED VIEW of these declarations: `_parse_globals`,
# help, and the docs table keep reading (canonical, alias, kind, hint,
# default, help) rows, and the completion hot path's hardcoded mirror is
# pinned against the declarations in CI.

_REQUIRED = object()
# No-default marker for a core declaration: the option has no reading without
# a value (`--where` names a task to locate, and there is no default task),
# so its bare mention refuses and teaches the attachment. Distinct from a
# plugin option's `default=None`, which IS a value — absence hands the owner
# `None`, and the bare form is legal by construction.


class _CoreOption(registry.GlobalOption):
    """A core global's declaration — the plugin class, constructed off the
    carriage (`_register` is a no-op): the collision law must see core as
    "footman's own" through the derived table, not refuse it against itself.

    Carries what the grammar needs beyond the plugin surface: the short
    alias (core's namespace, closed to plugins on purpose), the value hint,
    `config=True` for an option whose config key of the same name sets its
    default, a paired off-spelling's help for a bool that answers to
    `--no-x`, and `_REQUIRED` where a bare mention has no reading. Choices
    derive from a `Literal` annotation, so the hot-path mirror pins against
    the one source. A `bool` annotation is a flag; anything else takes an
    `=`-attached value, exactly as for plugins.
    """

    __slots__ = ("alias", "choices", "hint", "paired_off_help")

    # Core's config keys live directly in the brand table (`[tool.footman]
    # jobs`), not under the `plugins.` child a provider's section gets.
    _config_scope = "root"

    def __init__(
        self,
        name: str,
        annotation: Any = bool,
        *,
        alias: str | None = None,
        hint: str | None = None,
        default: Any = None,
        config: bool = False,
        paired_off_help: str | None = None,
        help: str = "",
    ) -> None:
        self.alias = alias
        self.hint = hint
        self.paired_off_help = paired_off_help
        choices = None
        if annotation is not bool:
            found = _coerce.all_choices(_coerce.peel(annotation).element)
            choices = tuple(found) if found else None
        self.choices = choices
        super().__init__(name, annotation, default=default, config=config, help=help)

    def _register(self) -> None:
        """Off the carriage — see the class docstring."""


def _color_from_env() -> str:
    """`--color`'s declared default: the ambient protocol variables, else
    `auto`. `NO_COLOR` set (to anything) means never; `FORCE_COLOR` set and
    non-zero means always. Living in the *default* rung is what keeps the
    ladder honest — an explicit `--color=` or a project's `color` key
    outranks the environment, because the specific beats the general."""
    if "NO_COLOR" in os.environ:
        return "never"
    forced = os.environ.get("FORCE_COLOR")
    if forced not in (None, "", "0"):
        return "always"
    return "auto"


# The ladder-bearing options, named: their values resolve at bind — CLI >
# `env()` > config > `default(fn)` > declared — and `_app` reads the
# instances (`JOBS.value`), where pure presence flags stay on the parsed
# token dict. A paired bool declares once and derives both spellings; its
# declared default is real (`INPUT` is on unless told otherwise). `UV` is
# config-backed but consumed lexically: the uv handoff runs before any bind
# (it may replace this process) — the one stated early consumer here.
SORT = _CoreOption(
    "sort",
    config=True,
    help="list tasks alphabetically (default: as defined)",
    paired_off_help="list tasks in definition order",
)
SEQUENTIAL = _CoreOption(
    "sequential",
    alias="-s",
    config=True,
    help="run one at a time, parallel() blocks included",
    paired_off_help="run in parallel (undo config)",
)
JOBS = _CoreOption(
    "jobs",
    Annotated[int, _between(1, None), _computed(_default_jobs)],
    alias="-j",
    hint="N",
    config=True,
    help="max parallel tasks",
)
INPUT = _CoreOption(
    "input",
    default=True,
    config=True,
    help="allow prompting (undo config)",
    paired_off_help="never prompt; error if input is required",
)
COLOR = _CoreOption(
    "color",
    Annotated[Literal["always", "never", "auto"], _computed(_color_from_env)],
    hint="WHEN",
    config=True,
    help="when to colour: always|never|auto",
)
PROGRESS = _CoreOption(
    "progress",
    default=True,
    config=True,
    help="progress bar, eta and timing (undo config)",
    paired_off_help="no progress bar, eta, or timing capture",
)
UV = _CoreOption(
    "uv",
    default=True,
    config=True,
    help="take the uv handoffs (undo config)",
    paired_off_help="skip the uv handoffs for this run",
)

# What `bind_global_options` resolves for a run (`UV` stays lexical, above).
CORE_LADDER: tuple[_CoreOption, ...] = (JOBS, COLOR, SORT, SEQUENTIAL, INPUT, PROGRESS)

# `default` is what a bare mention means — `_REQUIRED` for an option with no
# reading without a value (the question a task option answers with
# `required`); a literal where it is a constant; the annotation's
# `default(fn)` where it is computed (`--jobs`, `--color`); `""` where the
# reading exists but has no spelling of its own: `--describe` means "the
# whole tree", `--install-completion` means "whichever shell is asking".
CORE_OPTIONS: tuple[_CoreOption, ...] = (
    _CoreOption("help", alias="-h", help="help for {prog}, or the named group/task"),
    _CoreOption("version", alias="-V", help="print the version and exit"),
    _CoreOption("list", alias="-l", help="list tasks (flat)"),
    _CoreOption("tree", help="list tasks grouped by command group"),
    SORT,
    _CoreOption("all", alias="-a", help="include hidden tasks in the listings"),
    _CoreOption(
        "where",
        str,
        hint="TASK",
        default=_REQUIRED,
        help="print the task's source file:line",
    ),
    _CoreOption(
        "describe",
        str,
        hint="[ADDR]",
        default="",
        help="print task contracts as JSON",
    ),
    _CoreOption("plugins", help="list installed footman.tasks plugins, mounted or not"),
    _CoreOption(
        "dry-run", alias="-n", help="rehearse: bodies run, footman's work is faked"
    ),
    _CoreOption("keep-going", alias="-k", help="run every branch even if one fails"),
    _CoreOption("fail-fast", help="stop at the first failure"),
    SEQUENTIAL,
    JOBS,
    _CoreOption("yes", alias="-y", help="assume yes to every confirm() gate"),
    INPUT,
    _CoreOption("quiet", alias="-q", help="suppress the per-task summary"),
    _CoreOption("verbose", alias="-v", help="replay captured output even on success"),
    COLOR,
    _CoreOption("no-color", help="disable ANSI colour (same as --color=never)"),
    PROGRESS,
    UV,
    _CoreOption("json", help="stdout is one JSON document (captures output)"),
    _CoreOption("timings", help="show per-task durations"),
    _CoreOption(
        "directory",
        str,
        alias="-C",
        hint="PATH",
        default=_REQUIRED,
        help="run as if launched from PATH",
    ),
    _CoreOption(
        "tasks-file",
        str,
        alias="-f",
        hint="PATH",
        default=_REQUIRED,
        help="only this tasks file, no tasks cascade",
    ),
    _CoreOption(
        "config",
        str,
        hint="PATH",
        default=_REQUIRED,
        help="only this config file, no config cascade",
    ),
    # The bracketed hint marks the value optional: bare `--install-completion`
    # / `--setup-completion` detect the invoking shell. Their choices are
    # `_shellcomp.SHELLS` — pinned there by the mirror test, not duplicated
    # into a third copy here.
    _CoreOption(
        "install-completion",
        str,
        hint="[SHELL]",
        default="",
        help="install shell completion",
    ),
    _CoreOption(
        "setup-completion",
        str,
        hint="[SHELL]",
        default="",
        help="print completion for eval",
    ),
    _CoreOption(
        "uninstall-completion",
        str,
        hint="[SHELL]",
        default="",
        help="remove the completion hook",
    ),
)

GlobalDefault = str | Callable[[], object] | None


def _rows(
    o: _CoreOption,
) -> list[tuple[str, str | None, str, str | None, GlobalDefault, str]]:
    """A declaration as grammar-table rows — two for a paired bool, the
    spelling that acts leading (`--no-x` first when the default is on).
    Flags derive a `None` default: the column answers "what does a bare
    mention of a *value* option mean", and a flag's bare form is simply the
    flag. A value option's column is its declared literal, else the
    `default(fn)` its annotation carries — one source for `--help` and the
    run alike."""
    if o.annotation is bool:
        on = (f"--{o.name}", o.alias, "flag", None, None, o.help)
        if o.paired_off_help is None:
            return [on]
        off = (f"--no-{o.name}", None, "flag", None, None, o.paired_off_help)
        return [off, on] if o.default else [on, off]
    default: GlobalDefault
    if o.default is _REQUIRED:
        default = None
    elif o.default is None:
        marker = _coerce.peel(o.annotation).default_fn
        # The marker's own fn, unwrapped: a global has no siblings for a
        # computed default to read, so the zero-arg call is the contract.
        default = None if marker is None else marker.fn
    else:
        default = o.default
    return [(f"--{o.name}", o.alias, "option", o.hint, default, o.help)]


GLOBALS: list[tuple[str, str | None, str, str | None, GlobalDefault, str]] = [
    row for o in CORE_OPTIONS for row in _rows(o)
]
_GLOBAL_KIND = {name: kind for name, _, kind, _, _, _ in GLOBALS}
_GLOBAL_KIND.update({alias: kind for _, alias, kind, _, _, _ in GLOBALS if alias})
_CANON = {alias: name for name, alias, _, _, _, _ in GLOBALS if alias}
_GLOBAL_HINT = {name: hint for name, _, _, hint, _, _ in GLOBALS if hint}
# A global may be named bare exactly when it has a default — something for the
# mention to mean. That is the same question a task option answers with
# `required`, asked of the table instead of a signature: `--describe` and the
# completion trio have readings, `--where` names a task to locate and there is
# no default task, so a word behind it is taught rather than quietly becoming
# the task to run. The bracketed metavar is help notation now, not the rule.
_GLOBAL_DEFAULT = {name: d for name, _, _, _, d, _ in GLOBALS if d is not None}

# What a *computed* default is, in words — for surfaces that outlive the
# machine they were rendered on. `--help` resolves the value live, which is
# exactly right at a terminal: the reader is on the machine the number
# answers for. A docs build is not: it baked `3` and `never` (the build
# runner's core count, and CI's NO_COLOR) into the published reference,
# machine-specific answers dressed as the product's. Keyed beside the one
# grammar table so a new computed default cannot ship without its phrase —
# `global_default` refuses a computed name missing here when asked to speak
# symbolically.
_COMPUTED_PHRASE = {
    "--jobs": "the machine's cores minus one, never below 2",
    "--color": "auto (or what NO_COLOR/FORCE_COLOR says)",
}
_VALUE_OPTIONAL = frozenset(_GLOBAL_DEFAULT)

# Flag -> entry point, for globals that *would* exist had their provider been
# mounted. Memoised per brand distribution, and never touched on a path that
# is not already refusing (see `_own_plugin_flags`).
_OWN_FLAGS: dict[str, dict[str, str]] = {}


def _vouched_distributions() -> set[str]:
    """The packages footman will import to answer for a flag nobody mounted.

    Two, and never a third. **footman's own**: `--profile` and
    `--env-file` are the framework's, they are useful to every runner built
    on it, and it is already imported by definition. And **the brand's**,
    when a branded CLI named one with `dist=`: a distribution vouches for
    what it packages.

    Everything else stays shut. Teaching a third party's flag would mean
    importing, on a typo, code the project deliberately did not mount.
    """
    from livery.footman import _app
    from livery.footman.app import DEFAULT_BRAND

    # "livery-footman" is the distribution that ships the framework's own
    # plugins, whatever name a project pins on (the stock CLI pins the
    # `footman` compat shim; a brand pins its own). DEFAULT_BRAND is where
    # footman states any dist of its own; reading it here keeps the two
    # from drifting apart.
    return {
        dist
        for dist in ("livery-footman", DEFAULT_BRAND.dist, _app._brand.dist)
        if dist
    }


def _own_plugin_flags() -> dict[str, str]:
    """Every global option the vouched distributions ship, as flag ->
    entry-point name, whether or not this project mounted any of them.

    An entry point cannot advertise its options: the packaging spec is
    strictly `name = "module:attr"`, and a `GlobalOption` registers itself
    by being *constructed*, which happens when its module is imported. So
    the only way to learn an unmounted plugin's flags is to import it, and
    `_vouched_distributions` is the whole list footman will do that for.

    That covers the case this exists for: a distribution ships several
    plugins, a tasks file mounts some of them, and a flag from one of the
    others reads as a spelling mistake — for a branded CLI's own plugins
    and for footman's alike.

    Called only once a refusal is certain, so the imports never touch a
    successful run; memoised, so a process pays once. Every failure — an
    entry point that will not import, a brand whose package ships none — is
    simply a flag this cannot teach.
    """
    from livery.footman import _app

    key = _app._brand.dist or ""
    if (cached := _OWN_FLAGS.get(key)) is not None:
        return cached
    found: dict[str, str] = {}
    try:
        from importlib.metadata import entry_points

        from livery.footman import compose

        vouched = _vouched_distributions()
        for ep in entry_points(group=compose.ENTRY_POINT_GROUP):
            meta = getattr(ep.dist, "metadata", None)
            if not meta or meta.get("Name", "") not in vouched:
                continue
            try:
                # The SAME door mounting uses, not a raw `ep.load()`. A module
                # imports once per process, so whoever calls `load()` first
                # gets the only capture in which its declarations fire —
                # `_load_entry_point` memoises that tree for everyone after.
                # Scanning around it would spend a plugin's one import on an
                # error message and leave the real mount with nothing.
                tree = compose._load_entry_point(ep.name)
            except Exception:
                continue  # a plugin that will not load teaches nothing
            for opt in tree.contributions["globals"]:
                found.setdefault("--" + opt.name, ep.name)
    except Exception:
        found = {}
    _OWN_FLAGS[key] = found
    return found


def global_default(name: str, *, resolved: bool = True) -> tuple[Any, bool]:
    """A global's default **value**, and whether it was computed — resolved
    *now*, so a computed one answers for this machine rather than whichever one
    last wrote a manifest.

    The one source for both readings of a default: what `--help` prints, and
    what the run actually uses when nothing supplied the option. Those used to
    be derived separately — the table said `--jobs` meant cores-minus-one while
    `_app` independently called `default_jobs()` — which agreed only for as long
    as nobody edited one of them.

    `None` when there is no default at all (the option must be given a value);
    `""` where the bare form has a reading with no spelling of its own
    (`--describe` is "the whole tree", `--install-completion` is "whichever
    shell is asking"), which those two say in their help text instead.
    """
    value = _GLOBAL_DEFAULT.get(name)
    if value is None:
        return None, False
    # `isinstance(str)` rather than `callable()`: narrowing on the string side
    # leaves a concrete callable type, where `callable()` widens to "any
    # callable at all" and cannot be called safely.
    if isinstance(value, str):
        return value, False
    if not resolved:
        # The symbolic reading, for pages that outlive this machine: the
        # docs exporter must not bake the build runner's core count into
        # the published reference. Refusing an unphrased computed default
        # keeps the phrase table complete by construction.
        if name not in _COMPUTED_PHRASE:
            raise KeyError(f"computed default {name!r} has no symbolic phrase")
        return _COMPUTED_PHRASE[name], True
    return value(), True


@dataclass
class Segment:
    """One resolved task invocation within a chain."""

    task: str  # dotted path, e.g. "docs.build"
    path: list[str]  # ["docs", "build"]
    values: dict[str, Any] = field(default_factory=dict)  # cli-name -> value
    bare: set[str] = field(default_factory=set)
    """Options named without a value (`--profile`). They carry no value — the
    binder runs the same ladder an absent option would — so what a bare mention
    contributes is *presence*: the caller asked for this parameter and meant
    whatever it would have got anyway, which `given()` reads and a value alone
    cannot say."""
    variadic: list[str] = field(default_factory=list)
    passthrough: list[str] | None = None
    # Advisory stderr lines the app prints before running — `{prog}` is
    # substituted there. Notes never change what runs; the grammar stays
    # deterministic (a positional wins), they just say so out loud.
    notes: list[str] = field(default_factory=list)


def _required_label(p: dict[str, Any]) -> str:
    """Label a required option for the missing-option error — a flag teaches
    both its `--x` and `--no-x` forms."""
    name = f"--{p['name']}"
    return f"{name} (or --no-{p['name']})" if p["kind"] == "flag" else name


def _suggest_only(choices: list[str] | None, dynamic: dict[str, Any] | None) -> bool:
    """Whether a completer only *suggests* (never rejects): a soft completer
    (`strict=False`), or a strict one whose candidate list is empty — the
    completer genuinely returned nothing, and a *failing* strict completer
    aborts the manifest build instead, so rejecting every value would brick
    the task."""
    return bool(dynamic and (not dynamic.get("strict") or not choices))


def _check(
    where: str,
    label: str,
    value: str,
    *,
    choices: list[str] | None = None,
    accepts: list[str] | None = None,
    types: list[str] | None = None,
    dynamic: dict[str, Any] | None = None,
    path: str | None = None,
    bounds: tuple[float | None, float | None] | None = None,
) -> None:
    """Validate one string against choices or type tags; raise a taught error."""
    if choices is not None:
        if _suggest_only(choices, dynamic):
            return
        if value in choices:
            return  # an exact choice needs no further type/bounds checks
        if accepts is not None and value in accepts:
            # The manifest's declared synonym set — an enum's aliases and
            # value faces. Accepted here, taught nowhere: the refusal below
            # and completion both speak `choices` alone.
            return
        # A union like `Literal['fast','slow'] | int` carries both choices and
        # types: accept a value that matches either, and only teach both when
        # neither fits.
        if not (types and _coerce.coerce_scalar(value, types)[0]):
            listing = "|".join(choices) if choices else "(none available)"
            extra = f", or {_coerce.type_phrase(types)}" if types else ""
            hint = _did_you_mean(value, choices)
            raise ChainError(
                f"{where}: {label} must be one of {listing}{extra} "
                f"(got {value!r}){hint}"
            )
        # matched via the type path -> fall through to bounds/path below
    elif types and not _coerce.coerce_scalar(value, types)[0]:
        expected = _coerce.type_phrase(types)
        raise ChainError(f"{where}: {label} expects {expected} (got {value!r})")
    if path is not None:
        _check_path(where, label, value, path)
    if bounds is not None:
        _check_bounds(where, label, value, types, bounds)


_PATH_PHRASE = {
    "exists": ("an existing path", Path.exists),
    "file": ("an existing file", Path.is_file),
    "dir": ("an existing directory", Path.is_dir),
}


def _check_path(where: str, label: str, value: str, req: str) -> None:
    phrase, test = _PATH_PHRASE[req]
    if not test(Path(value)):
        raise ChainError(f"{where}: {label} must be {phrase} (got {value!r})")


def _check_bounds(
    where: str,
    label: str,
    value: str,
    types: list[str] | None,
    bounds: tuple[float | None, float | None],
) -> None:
    ok, number = _coerce.coerce_scalar(value, types or ["int", "float"])
    if not ok or isinstance(number, bool) or not isinstance(number, (int, float)):
        return  # the types check above already taught the type error
    lo, hi = bounds
    # Negated comparisons so NaN (which compares False to everything, so `< lo`
    # and `> hi` are both False) is rejected, not silently accepted; identical
    # to the plain comparisons for every real number.
    if (lo is not None and not (number >= lo)) or (
        hi is not None and not (number <= hi)
    ):
        expect = (
            f"at least {lo}"
            if hi is None
            else f"at most {hi}"
            if lo is None
            else f"between {lo} and {hi}"
        )
        raise ChainError(f"{where}: {label} must be {expect} (got {value!r})")


ChoicesFor = Callable[[str, str], "list[str] | None"]
"""How the app resolves a dynamic parameter's choices, live: given the task's
dotted address and the parameter's CLI name, the values its `suggest()`
completer offers right now, or `None` when it cannot be asked."""


def live_choices(
    where: str, p: dict[str, Any], choices_for: ChoicesFor | None
) -> list[str] | None:
    """A parameter's choices, resolving a dynamic one the first time a value
    actually needs them.

    Nothing bakes these any more, so `choices` absent means "nobody asked
    yet" and a present `[]` still means "the completer ran and offered
    nothing" — the reading `_suggest_only` has always had. The resolved list
    is written back into the spec, which is this invocation's own dict, so a
    repeated option (`--tag=a --tag=b`) asks the completer once.
    """
    if "choices" in p or p.get("dynamic") is None or choices_for is None:
        return p.get("choices")
    fresh = choices_for(where, str(p["name"]))
    if fresh is None:
        return None
    p["choices"] = fresh
    return fresh


def _validate(
    where: str,
    p: dict[str, Any],
    value: str,
    at: int = 0,
    choices_for: ChoicesFor | None = None,
) -> None:
    """Eagerly validate a choice/typed value; raise a taught error if wrong.

    *at* is the value's index in the stream, which only matters to a grouped
    shape: its positions have a type each, so the check has to know which slot
    this token is filling. `--size=800,tall` is refused before anything runs,
    naming `height` — the same eager treatment every other typed parameter
    gets, rather than a crash from inside binding.
    """
    label = (
        f"<{p['name']}>"
        if p["kind"] in ("positional", "variadic")
        else f"--{p['name']}"
    )
    bounds = (p.get("min"), p.get("max")) if "min" in p or "max" in p else None
    if group := p.get("group"):
        slot = at % group["max"]
        names = group["names"]
        _check(
            where,
            f"{label}: {names[slot] if names else f'value {slot + 1}'}",
            value,
            types=group["types"][slot],
        )
        return
    _check(
        where,
        label,
        value,
        choices=live_choices(where, p, choices_for),
        accepts=p.get("accepts"),
        types=p.get("types"),
        dynamic=p.get("dynamic"),
        path=p.get("path"),
        bounds=bounds,
    )


def _check_arity(
    where: str, p: dict[str, Any], group: dict[str, Any], count: int
) -> None:
    """A grouped shape's arity, checked once the whole stream is in.

    Values accumulate from commas and repetition, so the count is only final
    at the end of the segment — but it is knowable from the manifest alone,
    which is what makes it a parse-time refusal rather than a binding crash.
    """
    label = f"--{p['name']}"
    size = group["max"]
    if group["many"]:
        if count % size:
            raise ChainError(
                f"{where}: {label} takes values in groups of {size} "
                f"({group['label']}) — got {count}, which leaves "
                f"{count % size} over"
            )
    elif not group["min"] <= count <= size:
        want = (
            group["label"]
            if group["min"] == size
            else f"{group['min']} to {size} values ({group['label']})"
        )
        raise ChainError(f"{where}: {label} takes {want} — got {count}")


def _follower(argv: list[str], i: int) -> str | None:
    """The bare word right after position *i*, when one rode behind a
    value-bearing option — the token the attachment teaching names. `--`,
    `+` and dash tokens are not values-in-waiting, so they read as None.
    One lookahead for both refusal sites (globals and task options)."""
    nxt = argv[i + 1] if i + 1 < len(argv) else None
    if nxt is None or nxt in ("--", "+") or nxt.startswith("-"):
        return None
    return nxt


def _expects_value(
    where: str | None, given: str, hint: str, follower: str | None
) -> str:
    """The `=`-attachment teaching for a value-bearing option given bare.

    When a bare word follows, name the exact fix with the user's own value
    (`--target prod` → "did you mean --target=prod?") — that word must never
    surface as "unknown task 'prod'". Otherwise state the shape.
    """
    prefix = f"{where}: " if where else ""
    if follower is not None:
        return (
            f"{prefix}{given} takes its value attached — "
            f"did you mean {given}={follower}?"
        )
    return f"{prefix}{given} expects a value, attached: {given}={hint}"


@contextlib.contextmanager
def _hint_attachment(bare: str | None, token: str) -> Generator[None]:
    """Add the `=`-attachment hint to a failure caused by the token that rode
    behind a bare mention.

    The space form is not a value spelling in this grammar — `--mode` binds its
    default and `strict` is simply the next token, which is the only reading
    available — but it is still what a hand types out of habit. So when that
    next token goes on to fail, the failure also says what was probably meant.
    Never on a line that parses: a working invocation has nothing to
    second-guess, and guessing at one would be footman overruling the tokens.

    Never twice, either. When a task option's name shadows a value-taking
    global (`deploy --jobs 4` against the global `--jobs`), the failure the
    token raises is already the attachment teaching, word for word — appending
    it again would say the same sentence to the same person twice.
    """
    if bare is None:
        yield
        return
    try:
        yield
    except ChainError as exc:
        suggestion = f"did you mean {bare}={token}?"
        if suggestion in str(exc):
            raise
        raise ChainError(f"{exc} — {suggestion}") from None


def _parse_globals(
    argv: list[str],
    i: int,
    *,
    tree: dict[str, Any] | None = None,
    plugin: dict[str, str] | None = None,
    lenient: bool = False,
) -> tuple[list[str], int]:
    """Consume the leading globals — purely lexical: every dash token is
    self-contained (a value is `=`-attached), and the first bare word starts
    the task chain.

    A global whose bare form has a reading may be named bare — `_VALUE_OPTIONAL`
    says which, the same question a task option answers with `required`. One
    without a reading still refuses: `--where` names a task to locate, and there
    is no default task, so a word behind it is taught rather than quietly
    becoming the task to run.

    *plugin* maps a mounted plugin's long options (`--env-file`) to their
    kinds: a bool contributes both its spellings as `flag`, and every
    value-taking one is `option?` — bare-legal, because presence is a reading
    its owner can always ask for (`.given`). *lenient* carries an unknown dash
    token through untouched instead of refusing — the pre-discovery walk
    cannot know the plugins yet, so the authoritative post-discovery parse is
    the one that teaches.
    """
    known: dict[str, str] = dict(_GLOBAL_KIND)
    value_optional = set(_VALUE_OPTIONAL)
    if plugin:
        for flag, kind in plugin.items():
            if kind == "option?":
                known[flag] = "option"
                value_optional.add(flag)
            else:
                known[flag] = kind
    globals_: list[str] = []
    while i < len(argv) and argv[i].startswith("-") and argv[i] != "--":
        name = argv[i].split("=", 1)[0]
        if name not in known:
            if lenient:
                globals_.append(argv[i])
                i += 1
                continue
            raise ChainError(_unknown_global(name, known, tree, argv[i + 1 :]))
        canon = _CANON.get(name, name)
        if known[name] == "flag" and "=" in argv[i]:
            raise ChainError(f"{canon} is a flag and takes no value")
        if (
            known[name] == "option"
            and "=" not in argv[i]
            and canon not in value_optional
        ):
            raise ChainError(
                _expects_value(
                    None, name, _GLOBAL_HINT.get(canon, "VALUE"), _follower(argv, i)
                )
            )
        globals_.append(canon + argv[i][len(name) :])
        i += 1
    return globals_, i


def flat_addresses(tree: dict[str, Any]) -> list[str]:
    """Every runnable dotted address: tasks at any depth, plus runnable groups.

    The one index behind did-you-mean suggestions for a mistyped address —
    everything in it is copy-paste-runnable, so a suggestion can never
    propose a bare namespace group, and never one that needs a project when
    there is none. A suggestion is a thing to try next; offering a name that
    can only refuse is worse than offering nothing.
    """
    out: list[str] = []

    def walk(node: dict[str, Any], prefix: str) -> None:
        for name, spec in node["tasks"].items():
            if spec.get("unexposed"):
                continue
            out.append(prefix + name)
        for name, sub in node["groups"].items():
            if "default" in sub:
                out.append(prefix + name)
            walk(sub, f"{prefix}{name}.")

    walk(tree, "")
    return out


def _children(node: dict[str, Any], prefix: str) -> str:
    """A node's children as addresses for a "know:" listing — groups keep a
    trailing dot (`docs.`), the `ls -F` idiom, so descend-vs-run is visible;
    tasks are bare and copy-paste-runnable.

    What needs a project is left out where there is none, for the same reason
    the listings leave it out: this is the set of things you could type here,
    and those are not among them.

    Rendered here rather than at each caller, so the empty answer is written
    once: an empty group, a tasks file with no tasks, and a tree whose tasks
    all need a project seen from a directory without one all reach a listing
    with nothing in it, and `(know: )` reads as a bug in footman rather than
    an answer. `nothing` is the word the markdown exporter already uses.
    """
    names = [f"{prefix}{name}." for name in node["groups"]] + [
        f"{prefix}{name}"
        for name, spec in node["tasks"].items()
        if not spec.get("unexposed")
    ]
    return ", ".join(names) or "nothing"


def _resolve_head(
    tree: dict[str, Any],
    argv: list[str],
    i: int,
    prev_group: tuple[str, dict[str, Any]] | None,
    spent: tuple[str, int] | None = None,
) -> tuple[dict[str, Any], list[str], dict[str, Any] | None, int]:
    """Resolve segment head `argv[i]` — one dotted address — to its task.

    Returns `(task, path, group_node, next_i)`; `group_node` is set when the
    address named a runnable group (its default runs), so the caller can teach
    `group.child` if the *next* head turns out to be a child of it. Every
    failure is a taught `ChainError`; the space form of a nested address is
    detected by lookahead and answered with the dotted spelling.
    """
    token = argv[i]
    parts = token.split(".")
    if "" in parts:
        if token.endswith(".") and "" not in token[:-1].split("."):
            # `docs.` — an address left hanging; resolve the prefix so the
            # answer can list what completes it.
            node, path = tree, []
            for part in token[:-1].split("."):
                if part not in node["groups"]:
                    break
                node = node["groups"][part]
                path.append(part)
            else:
                known = _children(node, f"{'.'.join(path)}.")
                raise ChainError(f"{token!r} is an incomplete address (know: {known})")
        raise ChainError(
            f"{token!r} is not a task address — addresses are dot-separated "
            f"names with no empty segments, like 'docs.serve'"
        )

    node, path = tree, []
    for pos, part in enumerate(parts):
        last = pos == len(parts) - 1
        if part in node["groups"]:
            node = node["groups"][part]
            path.append(part)
            continue
        if part in node["tasks"]:
            path.append(part)
            if not last:
                dotted = ".".join(path)
                raise ChainError(
                    f"{token!r}: {dotted!r} is a task, not a group — "
                    f"nothing lives beneath it"
                )
            if node["tasks"][part].get("unexposed"):
                # Found, and refused for the true reason. "No task named" would
                # be a lie — the task exists, it just has nowhere to stand — and
                # whoever typed this is most often a tool in the wrong
                # directory, which is exactly what the answer should say.
                raise ChainError(_unexposed(".".join(path), tree))
            return node["tasks"][part], path, None, i + 1
        # Unknown segment. At the very start this may be a misplaced global,
        # or a child of the runnable group that led the previous segment
        # (`fm lint python` — the space form, taught, not parsed).
        if pos == 0:
            if (misplaced := _misplaced_global(token)) is not None:
                raise ChainError(misplaced)
            if (
                i > 0
                and (prev := argv[i - 1]).startswith("-")
                and "=" not in prev
                and (
                    _GLOBAL_KIND.get(prev) == "option"
                    or any(
                        "--" + g["name"] == prev and g["kind"] != "flag"
                        for g in tree.get("globals", ())
                    )
                )
            ):
                # The word rode behind a bare value-optional global
                # (`--install-completion zsh`, a plugin's `--profile out.json`
                # — required-value globals already refused in _parse_globals):
                # teach the attachment, never "unknown task 'zsh'".
                raise ChainError(
                    _expects_value(None, prev, _GLOBAL_HINT.get(prev, "VALUE"), token)
                )
            if prev_group is not None:
                prev_path, prev_node = prev_group
                if part in prev_node["groups"] or part in prev_node["tasks"]:
                    raise ChainError(
                        f"nested tasks use dots: '{prev_path}.{token}', "
                        f"not '{prev_path} {token}'"
                    )
        bad = ".".join([*path, part])
        hint = _did_you_mean(token, flat_addresses(tree))
        scope = f"{'.'.join(path)} has" if path else "know"
        known = _children(node, f"{'.'.join(path)}." if path else "")
        # One lead for both branches. They used to differ — "no task at" for a
        # dotted address, "expected a task name, got" at the root — but the
        # scope clause after already carries that distinction (`docs has:` vs
        # `know:`), and someone who typed `docs.sevre` thinks of it as a name.
        # A word that is no task, arriving right after a task whose
        # positionals are already full, is far more often one argument too
        # many than a misspelled address — say so, without dropping the
        # address reading, since only the author knows which they meant.
        arity = ""
        if spent is not None and not hint:
            who, takes = spent
            counted = f"{takes} argument{'s' if takes != 1 else ''}"
            arity = f" — or one argument too many for {who}, which takes "
            arity += counted if takes else "none"
        err = ChainError(
            f"no task named {(bad if path else token)!r}{hint} "
            f"({scope}: {known}){arity}"
        )
        err.unknown = token
        raise err

    # The whole token named a group. Runnable — one with `@group.default` —
    # resolves to its default action: `fm lint` / `fm lint --fix` run it,
    # `path` stays the group's. The default's own signature decides what a
    # trailing bare token means: a declared positional consumes it (a value
    # wins — every child keeps its dotted spelling), otherwise it opens a
    # fresh head on the next pass.
    if "default" in node:
        if node["default"].get("unexposed"):
            # The bare spelling of a task that needs one — refused for the
            # same reason and in the same words as `fm lint.default`.
            raise ChainError(_unexposed(".".join(path), tree))
        return node["default"], path, node, i + 1

    # A namespace group is never a segment target. Before refusing, look
    # ahead: if the following words walk to something runnable, the user
    # spelled a nested address with spaces — teach the dotted form, longest
    # resolvable path first (`fm tools sync` → 'tools.sync').
    walk_node, walk_path, j = node, list(path), i + 1
    while j < len(argv):
        nxt = argv[j]
        if nxt in walk_node["groups"]:
            walk_node = walk_node["groups"][nxt]
            walk_path.append(nxt)
            j += 1
            continue
        if nxt in walk_node["tasks"]:
            walk_path.append(nxt)
            spaced = " ".join(walk_path)
            raise ChainError(
                f"nested tasks use dots: '{'.'.join(walk_path)}', not '{spaced}'"
            )
        break
    if len(walk_path) > len(path) and "default" in walk_node:
        spaced = " ".join(walk_path)
        raise ChainError(
            f"nested tasks use dots: '{'.'.join(walk_path)}', not '{spaced}'"
        )
    dotted = ".".join(walk_path)
    known = _children(walk_node, f"{dotted}.")
    raise ChainError(
        f"{dotted!r} is a group, not a task — name one of its tasks (know: {known})"
    )


def _default_notes(
    seg: Segment,
    group_node: dict[str, Any],
    fixed: list[dict[str, Any]],
    rest: dict[str, Any] | None,
) -> None:
    """Advisory notes for a runnable group's positional values.

    The grammar is deterministic — a positional wins — but consequence 3
    carves a hole in the teaching error: `fm lint python` is a *valid* parse
    when lint's default takes a positional, so the git-habit space form runs
    instead of teaching. These stderr notes close the hole: an exact child
    name gets the dotted pointer, and an edit-distance-1 near miss (which
    used to be an "unknown task" error and would now silently filter on a
    pattern matching nothing) names the nearest subtask. A path-shaped value
    (`fm lint ./python`) is the documented quiet spelling — a legitimate
    value that happens to equal a child name is not nagged forever.
    """
    children = set(group_node["groups"]) | set(group_node["tasks"])
    if not children:
        return
    values: list[str] = list(seg.variadic)
    params = list(fixed)
    if rest is not None and rest["kind"] == "positional":
        params.append(rest)
    for p in params:
        got = seg.values.get(p["name"])
        if isinstance(got, str):
            values.append(got)
        elif isinstance(got, list):
            values.extend(v for v in got if isinstance(v, str))
    dotted = seg.task
    for value in values:
        if "/" in value or value.startswith((".", "~")):
            continue  # path-shaped: the quiet spelling
        if value in children:
            seg.notes.append(
                f"note: ran {dotted}'s default with {value!r}; "
                f"for the subtask: {{prog}} {dotted}.{value}"
            )
        elif (
            near := next((c for c in sorted(children) if _close1(value, c)), None)
        ) is not None:
            seg.notes.append(
                f"note: {value!r} ran as {dotted}'s positional; "
                f"nearest subtask: {{prog}} {dotted}.{near}"
            )


def split_chain(
    tree: dict[str, Any],
    argv: list[str],
    choices_for: ChoicesFor | None = None,
) -> tuple[list[str], list[Segment]]:
    """Split *argv* into leading globals and a list of resolved segments."""
    plugin: dict[str, str] = {}
    for g in tree.get("globals", ()):
        if g["kind"] == "flag":
            # A bool answers to both spellings, like every task flag.
            plugin["--" + g["name"]] = "flag"
            plugin["--no-" + g["name"]] = "flag"
        else:
            # Every value-taking plugin global may be named bare: presence
            # is a reading on its own, since the owner can ask `.given`.
            plugin["--" + g["name"]] = "option?"
    globals_, i = _parse_globals(argv, 0, tree=tree, plugin=plugin)
    segments: list[Segment] = []
    prev_group: tuple[str, dict[str, Any]] | None = None
    # The task whose positionals just filled up, alive for one token.
    spent: tuple[str, int] | None = None
    # The option a bare mention just named, alive for exactly the token after
    # it — including across a segment boundary, since a word with nowhere left
    # to go in this segment is tried as the next task's name.
    bare_before: str | None = None

    while i < len(argv):
        with _hint_attachment(bare_before, argv[i]):
            task, path, group_node, i = _resolve_head(tree, argv, i, prev_group, spent)
        spent = None
        bare_before = None
        prev_group = (".".join(path), group_node) if group_node is not None else None

        opts = {
            "--" + p["name"]: p
            for p in task["params"]
            if p["kind"] in ("flag", "option")
        }
        # Exact-arity positionals, then a single trailing consumer for the rest:
        # a typed multiple/one-or-many positional, or a `*args` variadic.
        fixed = [
            p
            for p in task["params"]
            if p["kind"] == "positional" and not p.get("multiple")
        ]
        rest = next(
            (
                p
                for p in task["params"]
                if (p["kind"] == "positional" and p.get("multiple"))
                or p["kind"] == "variadic"
            ),
            None,
        )
        seg = Segment(task=".".join(path), path=list(path))
        filled = 0
        rest_count = 0

        while i < len(argv):
            tok = argv[i]
            if tok == "+":  # explicit segment boundary
                i += 1
                break
            if tok == "--":  # passthrough is terminal for the whole line
                seg.passthrough = argv[i + 1 :]
                i = len(argv)
                break
            if tok.startswith("--"):
                before = len(seg.bare)
                i = _consume_option(seg, opts, argv, i, frozenset(plugin), choices_for)
                # Remember a bare mention for exactly one token. If the word
                # after it goes on to fail, the failure gets the attachment
                # hint — the space form is not a value spelling in this grammar,
                # but it is still what a hand types out of habit, and a line
                # that was going to error anyway can afford to say so.
                bare_before = tok if len(seg.bare) > before else None
                continue
            with _hint_attachment(bare_before, tok):
                if filled < len(fixed):
                    _consume_positional(seg, tree, fixed[filled], tok, choices_for)
                    filled += 1
                    i += 1
                elif rest is not None:
                    if rest["kind"] == "variadic":
                        # eager, like a positional
                        _validate(seg.task, rest, tok, choices_for=choices_for)
                        seg.variadic.append(tok)
                    else:
                        _consume_positional(seg, tree, rest, tok, choices_for)
                    rest_count += 1
                    i += 1
                else:
                    # Arity satisfied: the next word starts a new segment —
                    # or is an argument this task had no room for, which the
                    # address refusal gets to say if the word turns out to
                    # name nothing.
                    spent = (seg.task, len(fixed))
                    break
            bare_before = None

        missing = [f"<{p['name']}>" for p in fixed[filled:] if not p.get("optional")]
        if rest is not None and rest["kind"] == "positional" and rest_count == 0:
            missing.append(f"<{rest['name']}>")
        if missing:
            raise ChainError(
                f"{seg.task}: missing required positional(s): {', '.join(missing)}"
            )

        # Required options — a mapping or bool with no default. (Dicts are only
        # ever options; a bool is a flag, so teach both --x and --no-x forms.)
        missing_opts = [
            _required_label(p)
            for p in task["params"]
            if p.get("required") and p["name"] not in seg.values
        ]
        if missing_opts:
            raise ChainError(
                f"{seg.task}: missing required option(s): {', '.join(missing_opts)}"
            )
        for spec in task["params"]:
            if (group := spec.get("group")) is None:
                continue
            given = seg.values.get(spec["name"])
            if given is None:
                continue  # absent: the default stands
            _check_arity(seg.task, spec, group, len(given))
        if group_node is not None:
            _default_notes(seg, group_node, fixed, rest)
        segments.append(seg)

    return globals_, segments


def _consume_option(
    seg: Segment,
    opts: dict[str, dict[str, Any]],
    argv: list[str],
    i: int,
    plugin_flags: frozenset[str] = frozenset(),
    choices_for: ChoicesFor | None = None,
) -> int:
    tok = argv[i]
    name = tok.split("=", 1)[0]
    negated = False
    p = opts.get(name)
    if p is None and name.startswith("--no-"):
        candidate = "--" + name[len("--no-") :]
        if candidate in opts and opts[candidate]["kind"] == "flag":
            p, negated = opts[candidate], True
    if p is None:
        if (misplaced := _misplaced_global(name)) is not None:
            raise ChainError(f"{seg.task}: {misplaced}")
        if name in plugin_flags:
            # A mounted plugin's global after a task name is the same position
            # mistake a core global makes there — teach it by name, the way
            # `_misplaced_global` does for core, instead of the generic
            # unknown-option shrug the plugin side used to get.
            raise ChainError(
                f"{seg.task}: {name} is a global option — it goes before "
                f"the first task name"
            )
        forms = list(opts) + [
            f"--no-{opts[k]['name']}" for k in opts if opts[k]["kind"] == "flag"
        ]
        hint = _did_you_mean(name, forms) or (
            " (task options come right after their task; "
            "globals go before the first task)"
        )
        raise ChainError(f"{seg.task}: unknown option {name}{hint}")

    cli = p["name"]
    if p["kind"] == "flag":
        if "=" in tok:
            raise ChainError(f"{seg.task}: --{cli} is a flag and takes no value")
        seg.values[cli] = not negated
        return i + 1

    # A value is always `=`-attached, so a bare mention is unambiguous: it
    # cannot be reading the next token, because that spelling is not in this
    # grammar. It is legal wherever absence is legal, and records no value —
    # only presence. A *required* option has no absence to mean, so it still
    # refuses, with the same teaching it always gave.
    if "=" not in tok:
        if p.get("required"):
            raise ChainError(
                _expects_value(seg.task, name, "VALUE", _follower(argv, i))
            )
        seg.bare.add(cli)
        return i + 1
    value = tok.split("=", 1)[1]
    i += 1
    if p.get("mapping"):
        for pair in _values(p, value):
            _consume_pair(seg, p, cli, pair)
    elif p.get("multiple"):
        for part in _values(p, value):
            _validate(
                seg.task,
                p,
                part,
                at=len(seg.values.get(cli, ())),
                choices_for=choices_for,
            )
            seg.values.setdefault(cli, []).append(part)
    else:
        _validate(seg.task, p, value, choices_for=choices_for)
        seg.values[cli] = value
    return i


def _values(p: dict[str, Any], value: str) -> list[str]:
    """Comma-split parts of a list/dict value, unless the param opts out.

    Called only for collection params, so splitting is the default; a `nosplit`
    param (values may contain commas) takes the whole token verbatim.
    """
    if p.get("nosplit"):
        return [value]
    return [part for part in value.split(",") if part] or [value]


def _consume_pair(seg: Segment, p: dict[str, Any], cli: str, pair: str) -> None:
    """Parse and validate one `KEY=VALUE` token for a dict parameter."""
    if "=" not in pair:
        raise ChainError(f"{seg.task}: --{cli} expects KEY=VALUE (got {pair!r})")
    key, value = pair.split("=", 1)
    bounds = (p.get("min"), p.get("max")) if "min" in p or "max" in p else None
    _check(seg.task, f"--{cli} key", key, types=p.get("key_types"))
    _check(
        seg.task,
        f"--{cli} value",
        value,
        choices=p.get("value_choices"),
        types=p.get("value_types"),
        path=p.get("path"),
        bounds=bounds,
    )
    seg.values.setdefault(cli, []).append((key, value))


def _is_address(tree: dict[str, Any], tok: str) -> bool:
    """Whether *tok* walks the tree to a task or group — the "looks like the
    next task" peek for choice-positional errors. Loose on purpose: it only
    shapes an error message, so a namespace group counts too."""
    node = tree
    parts = tok.split(".")
    if "" in parts:
        return False
    for pos, part in enumerate(parts):
        if part in node["groups"]:
            node = node["groups"][part]
        elif part in node["tasks"]:
            return pos == len(parts) - 1
        else:
            return False
    return True


def _consume_positional(
    seg: Segment,
    tree: dict[str, Any],
    p: dict[str, Any],
    tok: str,
    choices_for: ChoicesFor | None = None,
) -> None:
    # Resolve first, so a dynamic positional gets the same "that looks like
    # the next task" reading a static one does.
    known = live_choices(seg.task, p, choices_for)
    if (
        known is not None
        and tok not in known
        and not _suggest_only(known, p.get("dynamic"))
        and not (p.get("types") and _coerce.coerce_scalar(tok, p["types"])[0])
        and _is_address(tree, tok)
    ):
        raise ChainError(
            f"{seg.task}: <{p['name']}> must be one of "
            f"{'|'.join(known)} — {tok!r} looks like the next task; "
            f"did you forget <{p['name']}>?"
        )
    if p.get("multiple"):
        for part in _values(p, tok):
            # The stream index rides along, exactly as the option path passes
            # it: a grouped shape's positions have a type each, and without
            # `at` every token was checked against slot 0 — `plot 1 two`
            # against `(x: int, y: str)` refused naming x, and a bad value
            # that happened to fit slot 0's type escaped to binding.
            _validate(
                seg.task,
                p,
                part,
                at=len(seg.values.get(p["name"], ())),
                choices_for=choices_for,
            )
            seg.values.setdefault(p["name"], []).append(part)
    else:
        _validate(seg.task, p, tok, choices_for=choices_for)
        seg.values[p["name"]] = tok
