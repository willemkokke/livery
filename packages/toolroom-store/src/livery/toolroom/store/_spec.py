"""What a command-line tool accepts, as data: the spec a stub is rendered from.

An [livery.toolroom.store.Option][] is one option of one verb as the
tool describes it, a [livery.toolroom.store.Verb][] a subcommand with
its options and positional shape, and a [livery.toolroom.store.ToolSpec][]
the tool whole. The bench extracts these from the tools themselves and
records them; a consumer rebuilds them from a record or from the index
with [livery.toolroom.store.spec_from][] and renders a stub with
[livery.toolroom.store.render][]. Nothing here runs a tool.
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
    """How this tool spells "off": `--dirty`, not always `--no-<name>`.
    Empty when the option is not a negatable flag."""
    help: str = ""
    """The tool's own one-line description, for the stub's docstring."""
    type_name: str = "str"
    """The Python type the stub declares: bool, str, int, list[str]."""
    default: Any = None
    """The tool's default, when it states one."""
    choices: tuple[str, ...] = ()
    """The closed set of values the tool accepts, when it names one. The
    stub declares these as a `Literal`, so an IDE offers them."""
    since: str = ""
    """The release this option first appears in, when the history knows.

    Empty when it was already there at the oldest release read: the file
    must not claim a `since` it never looked back far enough to see. Derived
    from the history at render time, never stored per release."""
    until: str = ""
    """The release this option stopped appearing in, when it has gone. A
    removed option stays in the stub, since completion should work against
    any version the reader might be running, and this is what says so."""
    not_on: tuple[str, ...] = ()
    """The platforms observed to lack this, when any have been. Empty means
    nothing contradicts it: either every platform that looked found it, or
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
    """Whether the verb runs *another* command: `uv run`, `docker exec`,
    `coverage run`. Its usage trails a `[COMMAND] [ARG...]` (or "program
    options"), and everything after the verb's own arguments belongs to the
    wrapped program. The bridge must place this call's flags *before* those
    arguments, or they leak past the tool into the child."""
    positional: str = "any"
    """What positionals the verb takes, read from its usage line. The stub
    renders this with `/` and `*`:

    * `"any"`: zero or more (`ruff check [FILES]...`), or unknown. The
      conservative default: `*args`, forbids nothing.
    * `"none"`: the tool declares only options (`mkdocs build [OPTIONS]`),
      so the stub is keyword-only and a stray positional is a type error.
    * `"required"`: a required leading positional (`docker run IMAGE …`),
      named by `lead`; the stub makes it positional-only.
    """
    lead: str = ""
    """The name of the required leading positional, when `positional` is
    `"required"`: `image` for `docker run`, `repo` for `git clone`."""


@dataclass(frozen=True)
class ToolSpec:
    """Everything footman knows about one tool, from the tool itself."""

    name: str
    help: str = ""
    version: str = ""
    """The version this was extracted from, what an audit compares."""
    verbs: tuple[Verb, ...] = field(default_factory=tuple)
    in_process: bool = False
    """Whether the tool can run inside footman's process (it publishes a
    `[console_scripts]` entry point)."""

    def negations(self) -> dict[str, str]:
        """Map each option to its negation where it is not `--no-<name>`.

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
        """The dotted verb paths that wrap a command.

        The bridge places a wrapper's flags before the child's argv
        instead of passing them into the child.
        """
        return frozenset(verb.name for verb in self.verbs if verb.wraps)

    def color_flags(self) -> dict[str, tuple[str, str, str]]:
        """The colour switch this tool exposes, per verb.

        The value is `{verb: (flag, on, off)}`.

        A verb with a `--color`/`--colour`/`--colors` option taking a closed set
        that includes `always`/`never`: the spelling footman would force to
        make the tool colour past its own non-tty check (or stay quiet past an
        ignored `NO_COLOR`). Both directions fall out of the one `choices` list,
        so detecting the *off* form costs nothing. Empty when the tool has no
        such switch, in which case it is assumed to obey `FORCE_COLOR`/`NO_COLOR`.
        This informs the curated `_COLOR` table (via `fm tools.color`);
        it is not applied automatically: a flag is only added for a tool proven
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
    values it takes from its closed set. Either may be absent, but at least
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
