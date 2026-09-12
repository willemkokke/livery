"""What a command-line tool says about itself — extracted, not transcribed.

The `tools.*` bridge translates keyword arguments mechanically, which is
what keeps it from going stale the way hand-written wrappers do. But two
things about a tool cannot be derived from the call: what its options
*mean*, and how it spells a negation.

The second one is a bug, not a nicety. `off` emits `--no-<name>`, which
is right for most tools and wrong for enough to matter: `mkdocs build
--no-clean` is rejected outright — the flag is `--dirty` — and five of
mkdocs' eight negatable options disagree with the convention. Only the
tool knows, so footman asks it.

Extraction, richest first:

* **click** — `Command.params` carries `opts`, `secondary_opts` (the true
  negation), the default, the help text, and the type, as data. No
  parsing, no guessing.
* **argparse / optparse** — walk the parser's actions (to come).
* **`--help` text** — for the Rust and Go tools, whose output is regular
  and which often spell the negation in prose (clap: "Use
  `--no-unsafe-fixes` to disable"). To come.

Nothing here runs on the completion hot path, and nothing here is
imported by `tools.py` at call time: the extracted facts are baked into
a table and a stub, and the extractor only runs when regenerating them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Option:
    """One option of one tool verb, as the tool describes it."""

    name: str
    """The Python keyword a task writes: `use_directory_urls`."""
    flags: tuple[str, ...] = ()
    """What the tool accepts, longest first: `("--use-directory-urls",)`."""
    negation: str = ""
    """How this tool spells "off" — `--dirty`, not always `--no-<name>`.
    Empty when the option is not a negatable flag."""
    help: str = ""
    """The tool's own one-line description, for the stub's docstring."""
    type_name: str = "str"
    """The Python type the stub declares: bool, str, int, list[str]."""
    default: Any = None
    """The tool's default, when it states one."""
    choices: tuple[str, ...] = ()
    """The closed set of values the tool accepts, when it names one — the
    stub declares these as a `Literal`, so an IDE offers them."""
    since: str = ""
    """The release this option first appears in, when the history knows.

    Empty when it was already there at the oldest release read — the file
    must not claim a `since` it never looked back far enough to see. Derived
    from the history at render time, never stored per release."""
    until: str = ""
    """The release this option stopped appearing in, when it has gone. A
    removed option stays in the stub — completion should work against any
    version the reader might be running — and this is what says so."""
    not_on: tuple[str, ...] = ()
    """The platforms observed to lack this, when any have been. Empty means
    nothing contradicts it — either every platform that looked found it, or
    only one ever looked. Derived from the history at render time like
    `since`/`until`: the store records what each platform saw at the release
    it read, and the standing claim is whatever its newest verdict says."""


@dataclass(frozen=True)
class Verb:
    """A subcommand: `mkdocs build`, `ruff check`."""

    name: str
    help: str = ""
    options: tuple[Option, ...] = ()
    not_on: tuple[str, ...] = ()
    """The platforms observed to lack this whole subcommand. Said once here
    rather than on each of its options, which is both smaller and what a
    reader wants: the command is missing, not forty flags."""
    wraps: bool = False
    """Whether the verb runs *another* command — `uv run`, `docker exec`,
    `coverage run`. Its usage trails a `[COMMAND] [ARG...]` (or "program
    options"), and everything after the verb's own arguments belongs to the
    wrapped program. The bridge must place this call's flags *before* those
    arguments, or they leak past the tool into the child."""
    positional: str = "any"
    """What positionals the verb takes, read from its usage line — the stub
    renders this with `/` and `*`:

    * `"any"` — zero or more (`ruff check [FILES]...`), or unknown. The
      conservative default: `*args`, forbids nothing.
    * `"none"` — the tool declares only options (`mkdocs build [OPTIONS]`),
      so the stub is keyword-only and a stray positional is a type error.
    * `"required"` — a required leading positional (`docker run IMAGE …`),
      named by `lead`; the stub makes it positional-only.
    """
    lead: str = ""
    """The name of the required leading positional, when `positional` is
    `"required"` — `image` for `docker run`, `repo` for `git clone`."""


@dataclass(frozen=True)
class ToolSpec:
    """Everything footman knows about one tool, from the tool itself."""

    name: str
    help: str = ""
    version: str = ""
    """The version this was extracted from — what an audit compares."""
    verbs: tuple[Verb, ...] = field(default_factory=tuple)
    in_process: bool = False
    """Whether the tool can run inside footman's process (it publishes a
    `[console_scripts]` entry point)."""

    def negations(self) -> dict[str, str]:
        """`{option: negation}` for every option whose negation is *not*
        the `--no-<name>` default — the table `off` consults.

        Only the exceptions: a table of things that already work would be
        noise, and would have to be regenerated far more often.
        """
        exceptions: dict[str, str] = {}
        for verb in self.verbs:
            for option in verb.options:
                if not option.negation:
                    continue
                default = "--no-" + option.name.replace("_", "-")
                if option.negation != default:
                    exceptions[option.name] = option.negation
        return exceptions

    def wrappers(self) -> frozenset[str]:
        """The dotted verb paths that wrap a command — what `_WRAPPERS`
        holds, so the bridge places a wrapper's flags before the child's
        argv rather than leaking them into the child.
        """
        return frozenset(verb.name for verb in self.verbs if verb.wraps)

    def color_flags(self) -> dict[str, tuple[str, str, str]]:
        """The colour switch this tool exposes, per verb: `{verb: (flag, on,
        off)}`.

        A verb with a `--color`/`--colour`/`--colors` option taking a closed set
        that includes `always`/`never` — the spelling footman would force to
        make the tool colour past its own non-tty check (or stay quiet past an
        ignored `NO_COLOR`). Both directions fall out of the one `choices` list,
        so detecting the *off* form costs nothing. Empty when the tool has no
        such switch, in which case it is assumed to obey `FORCE_COLOR`/`NO_COLOR`.
        This informs the curated `_COLOR` table (via `fm tools.color`);
        it is not applied automatically — a flag is only added for a tool proven
        to ignore the environment.
        """
        found: dict[str, tuple[str, str, str]] = {}
        for verb in self.verbs:
            for option in verb.options:
                detected = _color_option(option)
                if detected is not None:
                    found[verb.name] = detected
                    break
        return found


_COLOR_KEYWORDS = frozenset({"color", "colour", "colors", "colours"})


def _color_option(option: Option) -> tuple[str, str, str] | None:
    """`(flag, on, off)` if *option* is a `--color`-style switch, else None.

    The tool's own flag spelling (longest first), and the `always`/`never`
    values it takes from its closed set — either may be absent, but at least
    one must be, or this is not a forcing switch footman can use.
    """
    if option.name not in _COLOR_KEYWORDS or not option.choices:
        return None
    choices = set(option.choices)
    on = "always" if "always" in choices else ""
    off = "never" if "never" in choices else ""
    if not on and not off:
        return None
    flag = option.flags[0] if option.flags else f"--{option.name}"
    return (flag, on, off)


def _type_name(param: Any) -> str:
    """The stub's declared type for a click parameter."""
    if getattr(param, "is_flag", False):
        return "bool"
    kind = getattr(getattr(param, "type", None), "name", "") or ""
    scalar = {
        "integer": "int",
        "float": "float",
        "boolean": "bool",
        "path": "str",
        "filename": "str",
        "directory": "str",
        "text": "str",
        "choice": "str",
    }.get(kind, "str")
    return f"list[{scalar}]" if getattr(param, "multiple", False) else scalar


def from_click(command: Any, *, name: str = "", version: str = "") -> ToolSpec:
    """A `ToolSpec` from a click `Group` or `Command`.

    click models a negatable flag as one parameter with `opts` and
    `secondary_opts` — `--clean` / `--dirty` — which is exactly the fact
    `off` needs and cannot infer.
    """
    tool = name or getattr(command, "name", "") or ""
    commands = getattr(command, "commands", None)
    if commands:
        verbs = tuple(
            _verb_from_click(verb_name, sub)
            for verb_name, sub in sorted(commands.items())
        )
    else:  # a single-command tool: its options hang off the root
        verbs = (_verb_from_click("", command),)
    return ToolSpec(
        name=tool,
        help=_first_line(getattr(command, "help", "") or ""),
        version=version,
        verbs=verbs,
        in_process=True,  # a click tool always has a console_scripts entry
    )


def _verb_from_click(name: str, command: Any) -> Verb:
    options = []
    arguments = []
    for param in getattr(command, "params", ()):
        if getattr(param, "param_type_name", "") == "argument":
            arguments.append(param)  # a positional, for the shape below
            continue
        if getattr(param, "param_type_name", "") != "option":
            continue
        secondary = tuple(getattr(param, "secondary_opts", ()) or ())
        opts: list[str] = list(param.opts)
        options.append(
            Option(
                name=_keyword(param),
                flags=tuple(sorted(opts, key=len, reverse=True)),
                negation=secondary[0] if secondary else "",
                help=_first_line(getattr(param, "help", "") or ""),
                type_name=_type_name(param),
                default=_plain_default(param),
                choices=_click_choices(param),
            )
        )
    unique: dict[str, Option] = {}
    for option in options:
        unique.setdefault(option.name, option)
    positional, lead = _click_positional(arguments)
    return Verb(
        name=name.replace("-", "_"),
        help=_first_line(getattr(command, "help", "") or ""),
        options=tuple(sorted(unique.values(), key=lambda o: o.name)),
        positional=positional,
        lead=lead,
    )


def _click_positional(arguments: list[Any]) -> tuple[str, str]:
    """The positional shape from click's declared arguments.

    click hands these over as data, so the shape is exact: no arguments
    means keyword-only, a required first argument means positional-only.
    """
    if not arguments:
        return "none", ""
    first = arguments[0]
    variadic = getattr(first, "nargs", 1) == -1
    if getattr(first, "required", False) and not variadic:
        return "required", str(getattr(first, "name", "") or "arg")
    return "any", ""


def _click_choices(param: Any) -> tuple[str, ...]:
    """The closed set, when click declares the parameter a `Choice`."""
    choices = getattr(getattr(param, "type", None), "choices", None)
    if not choices:
        return ()
    return tuple(str(c) for c in choices)


def _keyword(param: Any) -> str:
    """The keyword a task writes for this parameter.

    Not `param.name`: click names a group of mutually exclusive flags after
    one internal variable, so mkdocs' `--dirty`, `--clean` and
    `--dirtyreload` are all `build_type` — three parameters with one name.
    The bridge translates a *keyword* into a *flag*, so the flag's own
    spelling is the only name that round-trips.
    """
    flags: list[str] = [o for o in getattr(param, "opts", ()) if o.startswith("--")]
    longest = max(flags, key=len, default="")
    stem = longest.removeprefix("--") if longest else str(param.name)
    return stem.replace("-", "_")


def _plain_default(param: Any) -> Any:
    """The default, when it is a plain value worth showing in a stub."""
    default = getattr(param, "default", None)
    if isinstance(default, (bool, int, float, str)) or default is None:
        return default
    return None  # click sentinels and callables say nothing useful here


def _first_line(text: str) -> str:
    """The tool's own summary: its help's first sentence-ish line."""
    for line in text.strip().splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""
