"""Read a tool's own `--help` and turn it into a `ToolSpec`.

The structural extractors in `_toolspec` are better when they apply — click
and argparse hand over their parameters as data. But most of the tools a
task actually calls are Rust, Go or Node binaries with no Python parser to
introspect: ruff and uv (clap), docker (cobra), cspell and markdownlint
(commander), git (its own). For those, the tool's `--help` is the only
description it offers, and it is far more regular than it looks.

Every one of those help formats prints an option as a line that starts with
a dash, followed by help text that is either on the same line past a run of
spaces, or on the lines below indented deeper:

    clap        --fix
                    Apply fixes to resolve lint violations. Use `--no-fix`
                    to disable

    optparse    -d DIR, --directory=DIR
                        Write the output files to DIR.

    cobra       -f, --file stringArray   Compose configuration files

So one parser reads them all: find the lines that start an option, split the
flag spellings from the prose, and glue on the continuation lines. What
differs between the families is only how they spell a *default* and a
*negation*, and those are small dialects on top (`[default: 3]`,
`(default true)`, "Use `--no-fix` to disable").

Nothing here runs at task time. The extractor runs when a maintainer
regenerates the stubs, and `fm tools.audit` compares what it finds
against what is checked in.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from livery.toolroom.bench._toolspec import Option, ToolSpec, Verb

if TYPE_CHECKING:
    from livery.footman.context import Result


def _run(*args: Any, **kwargs: Any) -> Result:
    """`context.run`, imported at call time.

    A module-level import would be circular — `context` reaches the tools
    bridge, which reaches here — and this module is also imported by the
    stub generator, which has no interest in the run machinery.
    """
    from livery.footman.context import run

    return run(*args, **kwargs)


# An option block opens with a dash at the start of the line's content. The
# indent is captured because it decides what counts as a continuation line;
# it also absorbs a leading `- ` bullet — markdownlint-cli2 (and other
# minimist/meow tools) print options as a bulleted list, `- --fix  updates …`,
# where the flag itself is what follows the bullet.
_OPTION = re.compile(r"^(?P<indent> *(?:- )?)(?P<body>-{1,2}[A-Za-z0-9?].*)$")

# `Options:`, `OPTIONS`, `Flags:`, `Rule selection:` — every family prints
# some variant. The colon (or the shouting) is what makes it a heading: a
# tool's one-line description is also short, unindented and capitalised.
_SECTION = re.compile(r"^(?P<title>[A-Za-z][A-Za-z /-]*):$|^(?P<caps>[A-Z][A-Z /-]+)$")
# Sections that hold something other than options.
_NOT_OPTIONS = re.compile(r"command|example|usage|argument|see also|environment")

# One spelling inside a flag block: `--select <RULE>`, `--directory=DIR`,
# `-j N`, `--fix`. It runs on the flag column only — `_blocks` has already
# split the prose off at the two-space gap — so the compound go-types are
# named explicitly (`stringArray` repeats where `string` does not), and a
# bare lowercase word left in the column is read as a value placeholder
# (gh's `--assignee login`), not as the first word of the description.
# Compound names first: alternation is ordered, so a leading `string`
# would match `stringArray` and stop, losing the fact that it repeats.
_GO_TYPES = (
    r"(?:stringToString|stringArray|stringSlice|intSlice|uintSlice|boolSlice"
    r"|ipSlice|bytesBase64|bytesHex|duration|float32|float64"
    r"|int8|int16|int32|int64|uint8|uint16|uint32|uint64"
    r"|string|int|uint|bool|ip)"
)
# The flag and any attached optional-value placeholder, shared by both forms.
_FLAG = (
    # A flag starts a word: a dash reached mid-word is a hyphen, not a
    # spelling. Without the boundary, `-Y find-principals` (ssh-keygen's
    # verb-word arguments) yields a phantom `-principals` that the Go-style
    # fallback would happily promote to a keyword.
    r"(?<![A-Za-z0-9-])"
    # A dot is allowed only *inside* the name (`--foo.bar`), never trailing:
    # clap prints a repeatable flag as `--verbose...`, and a greedy `.` would
    # swallow the ellipsis into the name (`verbose...` → keyword `verbose___`).
    r"(?P<flag>--?(?:\[no-\])?[A-Za-z0-9](?:[A-Za-z0-9_-]|\.(?=[A-Za-z0-9]))*)"
    # git glues an optional-value placeholder to the flag with no space:
    # `--gpg-sign[=<key-id>]`, `--untracked-files[=<mode>]`. Read as one
    # attached token so the option isn't mistaken for a bare switch.
    r"(?P<attached>\[=[^\]]*\])?"
)
# The value placeholder every dialect agrees on: `<x>`, `[x]`, an UPPERCASE
# metavar, or a cobra go-type (`stringArray`).
_META = (
    r"\[?<[^>]+>(?:\.\.\.)?\]?|\[[^\]]+\]|[A-Z][A-Z0-9_.,|]*(?:\.\.\.)?"
    rf"|{_GO_TYPES}"
)
# cobra and gh also name a value with a bare lowercase word — `--assignee
# login`, `--base branch`, `--memory bytes`. Only trusted in `--help` text,
# where `_blocks` has split the prose off at the two-space gap: a man page's
# description is a paragraph, and "the `--patch` option." there would read
# "option" as `--patch`'s value.
_META_BARE = r"|[a-z][A-Za-z0-9._-]*"
_SPELLING = re.compile(_FLAG + rf"(?:[= ](?P<meta>{_META}{_META_BARE}))?")
_SPELLING_STRICT = re.compile(_FLAG + rf"(?:[= ](?P<meta>{_META}))?")

# The dialects of "this is the default".
_DEFAULT = re.compile(
    r"\[default: (?P<clap>[^\]]*)\]|\(default:? (?P<other>[^)]*)\)", re.IGNORECASE
)
# clap and cobra both print the closed set of values they accept — inline
# when they are short, and as a bulleted list when each one has its own
# gloss. Both forms mean the same thing to a stub.
_CHOICES = re.compile(r"\[possible values: (?P<values>[^\]]+)\]")
_POSSIBLE = re.compile(r"Possible values:\s*(?P<body>.*)$", re.IGNORECASE)
_BULLET = re.compile(r"(?:^|\s)- (?P<name>[A-Za-z0-9][A-Za-z0-9_.-]*):")
# The negation stated in prose, which is the only place some tools say it.
_PROSE_NEGATION = re.compile(
    r"(?:use|pass) [`'\"]?(?P<flag>--no-[A-Za-z0-9-]+|--[A-Za-z0-9-]+)[`'\"]?"
    r"[^.]{0,40}?(?:to disable|to turn (?:it|this) off)",
    re.IGNORECASE,
)
# git's own dialect: `--[no-]quiet` is both spellings on one line.
_INLINE_NEGATION = re.compile(r"^--\[no-\](?P<name>.+)$")
_REPEATABLE = re.compile(
    r"(?:may|can) be (?:used|repeated|specified|passed|given)"
    r"(?: multiple times| more than once| repeatedly)?",
    re.IGNORECASE,
)


def _sections(text: str) -> dict[str, list[str]]:
    """Split help output into `{section title: lines}`.

    Sections matter for two reasons: subcommands live in one of them, and
    an option's *section* is how a tool marks its global flags.
    """
    out: dict[str, list[str]] = {"": []}
    title = ""
    for line in text.splitlines():
        if not line[:1].isspace() and line.strip():
            match = _SECTION.match(line.strip())
            if match and not line.strip().startswith("-"):
                title = (match["title"] or match["caps"]).strip().lower()
                out.setdefault(title, [])
                continue
        out[title].append(line)
    return out


def _blocks(lines: Sequence[str], *, man: bool = False) -> list[tuple[str, str]]:
    """Yield `(spellings, help)` for each option in *lines*.

    The boundary between two options is the *help column*, not the flag
    column. Flags themselves sit at more than one indent — clap prints
    `  -w, --watch` but `      --fix-only`, aligning long flags past the
    short-flag column — so "indented deeper than the last flag" would read
    the second one as prose belonging to the first. Help text is always
    indented deeper still, so a dash at less than the help column opens a
    new option and anything at or past it is that option's prose.

    In *man* mode a block must also start a paragraph. A manual sets its
    options at the same indent as its prose, so a sentence that happens to
    wrap onto a line beginning with a flag is otherwise indistinguishable
    from a block opening — and git's prose is full of them. Read that way,
    "--merged, only branches merged into the named commit … With --no-merged
    only branches not merged" becomes one option carrying both spellings,
    which pairs them as a negation and hides the real `--no-merged` block
    below it. Where the sentence wraps decides whether that happens, so the
    same page read at two widths disagrees about what git accepts: 182
    options at 80 columns that 200 columns lost, on a manual whose bytes are
    identical everywhere. A stacked spelling is still a head, because a
    manual prints `-d, --delete` and `-D` on consecutive lines.
    """
    blocks: list[tuple[str, str]] = []
    pending: tuple[str, list[str]] | None = None
    flag_indent = 0
    help_indent = 0  # 0 until the block's prose reveals the column
    previous = ""
    head_indent: int | None = None
    for line in lines:
        match = _OPTION.match(line.rstrip())
        indent = len(match["indent"]) if match else len(line) - len(line.lstrip())
        if man and match:
            # A stacked spelling continues the block above it; a spelling
            # list that *wrapped* continues the head itself, and the comma
            # left hanging at the end of the line is what says so. Reading
            # the wrapped remainder as a head of its own splits one option
            # in two — git-log's four parent filters share a description and
            # a line, and at 80 columns the line ends `--no-min-parents,`
            # with `--no-max-parents` below it.
            if (
                pending is not None
                and previous.rstrip().endswith(",")
                and head_indent == indent
            ):
                # Join it to the head it belongs to, rather than dropping it:
                # the remainder carries spellings, and the description that
                # follows is the whole block's. Only from the head's own
                # column — a *description* line that happens to end with a
                # comma before a wrapped flag mention ("Implies -N, -T, …")
                # sits at the help indent, and joining it would smuggle the
                # mentioned flags into the head as extra spellings.
                previous = line
                pending = (f"{pending[0]} {match['body']}", pending[1])
                head_indent = indent
                continue
            starts = (
                not previous.strip()
                or head_indent == indent
                # A head in the open block's own flag column is a head even
                # without a blank line before it: mandoc renders ssh's
                # `-p port` flush against `-P tag`'s paragraph, uniquely on
                # the page, and the paragraph rule alone would close the
                # block and drop `-p` on the floor. Prose can't be confused
                # for this — a wrapped sentence lands at the help indent,
                # not the flag column.
                or (pending is not None and indent == flag_indent)
            )
            head_indent = indent if starts else None
            if not starts:
                match = None
        elif man:
            head_indent = None
        previous = line
        opens = indent < help_indent if help_indent else indent <= flag_indent
        if match and (pending is None or opens):
            if pending is not None:
                blocks.append((pending[0], " ".join(pending[1]).strip()))
            flag_indent = indent
            body = match["body"]
            head, _, tail = body.partition("  ")
            # Python's `--help` separates the flag column from the description
            # with a ` : ` gutter (`-b     : issue warnings`, `-c cmd : program
            # passed in`), not the double-space gutter every other dialect
            # uses. Re-split on it so the colon doesn't leak into the help text,
            # and — when the columns touch and the double-space split found
            # nothing — so the metavar and description aren't lost outright.
            if not tail.strip() and " : " in head:
                head, _, tail = body.partition(" : ")
            elif tail.lstrip().startswith(":"):
                tail = tail.lstrip()[1:]
            # Learn the help column from same-line help too, not only from a
            # continuation: cobra prints `-d, --detach` at one indent and
            # `      --tail string` at a deeper one, and without the column
            # the deeper flag reads as prose belonging to the shallower one.
            help_indent = (
                indent + len(head) + 2 + len(tail) - len(tail.lstrip())
                if tail.strip()
                else 0
            )
            pending = (head.strip(), [tail.strip()] if tail.strip() else [])
        elif pending is not None:
            stripped = line.strip()
            if not stripped:
                continue  # a blank line inside a block is just formatting
            if indent <= flag_indent:
                blocks.append((pending[0], " ".join(pending[1]).strip()))
                pending = None
                continue
            help_indent = help_indent or indent
            pending[1].append(stripped)
    if pending is not None:
        blocks.append((pending[0], " ".join(pending[1]).strip()))
    return blocks


def _spellings(
    head: str, *, strict: bool = False, bare_meta: bool = True
) -> tuple[list[str], str, bool]:
    """The flags in an option's left column, its placeholder, and whether
    the value is optional (a `[=…]` glued to the flag).

    *strict* drops the bare-lowercase metavar, for a man page whose prose
    refers to flags mid-sentence: `--patch` there must not read the next
    word as its value.

    *bare_meta* is the same drop for one option in `--help` text, where the
    usage line has already named this flag's value. The bare-word rule reads
    the first word after the flag as a metavar, which is right for gh's
    `--assignee login` and wrong wherever a description begins there instead
    — and it cannot tell them apart, because both are one lowercase word.
    The grammar can. Nothing is lost by dropping it: what the placeholder is
    *called* is never recorded, only that there is one, and that is exactly
    what the usage line has just said.
    """
    flags: list[str] = []
    meta = ""
    optional = False
    pattern = _SPELLING_STRICT if strict or not bare_meta else _SPELLING
    for match in pattern.finditer(head):
        flags.append(match["flag"])
        meta = meta or (match["meta"] or "")
        optional = optional or bool(match["attached"])
    return flags, meta, optional


# A manual's prose is Unicode-typeset (curly quotes, dashes, ellipsis).
# The stub is source that must stay ASCII-clean (ruff RUF002), so fold them.
_TYPOGRAPHY = str.maketrans(
    {
        "\u2019": "'",  # right single quote
        "\u2018": "'",  # left single quote
        "\u201c": '"',  # left double quote
        "\u201d": '"',  # right double quote
        "\u2013": "-",  # en dash
        "\u2014": "-",  # em dash
        "\u2026": "...",  # ellipsis
        "\u00a0": " ",  # no-break space
    }
)  # fmt: skip
# Abbreviations whose period does not end a sentence.
_ABBREV = (" e.g", " i.e", " etc", " vs", " cf", " al", " no")


def _clean(text: str) -> str:
    """The tool's prose, as one clean first sentence.

    A `--help` line is already a sentence; a manual entry is paragraphs, so
    keep only the first — the summary a completion popup can show — folding
    the manual's typographic punctuation to ASCII on the way.
    """
    text = _DEFAULT.sub("", text)
    text = _CHOICES.sub("", text)
    text = _POSSIBLE.sub("", text)
    text = re.sub(r"\[env: [^\]]*\]", "", text)
    text = re.sub(r"\s+", " ", text).translate(_TYPOGRAPHY).strip(" .")
    return _first_sentence(text)


def _first_sentence(text: str) -> str:
    """Up to the first sentence-ending period, skipping `e.g.`/`i.e.`."""
    for match in re.finditer(r"\. ", text):
        head = text[: match.start()]
        if not head.endswith(_ABBREV):
            return head
    return text


def _parse_default(text: str) -> str:
    match = _DEFAULT.search(text)
    if not match:
        return ""
    # Folded before stripping, so a manual's typeset quotes (mdoc curls
    # them around ssh's escape-char default) read as the quotes they are
    # and come off with the ASCII ones — the value is the tilde alone, not
    # a pair of curly marks the stub would then fail ASCII linting over.
    value = (match["clap"] or match["other"] or "").translate(_TYPOGRAPHY)
    return value.strip().strip("\"'")


def _keyed_on(longs: list[str]) -> str:
    """Which of an option's long spellings the Python keyword is taken from.

    The first, unless the tool has printed the *same* option twice in two
    dialects: Claude Code offers `--allowedTools, --allowed-tools`, and a
    keyword is written in Python, so it takes the spelling Python is
    written in. Nothing else moves — `--bg, --background` are both already
    lowercase, and the first stays the keyword.

    Sameness is decided by folding case and dashes away, which is what
    separates a dialect from a neighbour. markdownlint-cli2 prints
    `--configPointer, --config` — two distinct options its column
    alignment ran into one block — and those fold apart, so both keep
    their own keyword. So does a negation printed alongside its positive
    (git's `--column[=<options>], --no-column`), which must never be the
    one keyed on.
    """
    first = longs[0]
    folded = first.lower().replace("-", "")
    for spelling in longs:
        same = spelling.lower().replace("-", "") == folded
        if same and not any(char.isupper() for char in spelling):
            return spelling
    return first


def _option(
    head: str,
    help_text: str,
    *,
    strict: bool = False,
    shorts: str = "only",
    bare_meta: bool = True,
) -> Option | None:
    """One `Option` from one parsed block, or None if it isn't one.

    *shorts* is the short-option policy: `"none"` never keys on a short,
    `"only"` (default) keys on a short *when it is the option's only spelling*
    (python's `-m`, git's `-C`), and `"all"` also keys on a short that has a
    long — `_short_alias` adds the extra keyword for that mode.
    """
    flags, meta, optional = _spellings(head, strict=strict, bare_meta=bare_meta)
    if strict and not meta and not optional and not head.startswith("--"):
        # The manual's flag column names a value with a bare word — mdoc
        # typesets `-B bind_interface`, and rendered it is two plain tokens.
        # Trusted only in that exact shape: one spelling, one following word
        # that is not itself a flag. A prose paragraph misread as a head is a
        # sentence and never that short, so the rule that keeps `--patch`
        # from eating the next word keeps holding everywhere else.
        parts = head.split()
        if len(parts) == 2 and flags and not parts[1].startswith("-"):
            meta = parts[1]
    longs = [f for f in flags if f.startswith("--")]
    if not longs and not strict:
        # Go's stdlib `flag` spells even long options with one dash (`-color`,
        # `-no_gitignore`); read a multi-char single-dash flag as the keyword
        # when there's no `--` form. `--help` text only: a manual never
        # spells Go-style longs, and promoting a manual's stray dash-words
        # fabricates options that were never there.
        longs = [f for f in flags if len(f) > 2 and not f.startswith("--")]
    if not longs and shorts != "none":
        # A short-only option (python's `-m`, ssh's whole surface): the
        # single char is the keyword — the bridge turns `m="build"` into
        # `-m build`. The `shorts` policy alone decides, from `--help` and
        # manual alike; only a letter that forms a valid keyword (`-0`
        # can't, ssh's `-4`/`-6` can't).
        longs = [f for f in flags if len(f) == 2 and f[1:].isidentifier()][:1]
    if not longs:
        return None  # nothing spellable
    if not help_text and not bare_meta:
        # The block never split, because the description sat one space from
        # the flag rather than in a column — markdownlint-cli2 aligns to its
        # longest option, so `--configPointer` alone loses its gap. The whole
        # line arrived as the head, and what follows the flags is the prose.
        # Only where the grammar has already settled arity: elsewhere that
        # first word may genuinely be the value's name.
        help_text = _after_flags(head)
    keyed = _keyed_on(longs)
    inline = _INLINE_NEGATION.match(keyed)
    stem = inline["name"] if inline else keyed.lstrip("-")
    name = stem.replace("-", "_").replace(".", "_")
    default = _parse_default(help_text)
    choices = _choices(help_text)
    # An optional-value option (`--gpg-sign[=<key-id>]`) is neither a plain
    # switch nor a required-value option: it works bare *and* with a value.
    is_flag = not meta and not optional
    repeatable = bool(
        meta.endswith(("...", "...]", "Array", "Slice", "ToString"))
        or _REPEATABLE.search(help_text)
    )
    negation = f"--no-{stem}" if inline else ""
    prose = _PROSE_NEGATION.search(help_text)
    if not negation and is_flag and prose and prose["flag"] != longs[0]:
        negation = prose["flag"]
    return Option(
        name=name,
        flags=tuple(sorted((_spell(f, stem) for f in flags), key=len, reverse=True)),
        negation=negation,
        help=_clean(help_text),
        type_name=_kind(is_flag, repeatable, choices, optional),
        default=_coerce_default(default, is_flag),
        choices=choices,
    )


# `--help` and `--version` are on every tool and belong to no task: the
# bridge would happily emit them, but a stub that suggests them is noise.
_NOISE = frozenset({"help", "version"})


def _choices(text: str) -> tuple[str, ...]:
    """The values a tool says it accepts, from whichever form it printed."""
    inline = _CHOICES.search(text)
    if inline:
        return _values(inline["values"])
    listed = _POSSIBLE.search(text)
    if listed:
        return tuple(m["name"] for m in _BULLET.finditer(listed["body"]))
    return ()


def _spell(flag: str, stem: str) -> str:
    """`--[no-]quiet` is how git *prints* it; `--quiet` is what it takes."""
    return f"--{stem}" if _INLINE_NEGATION.match(flag) else flag


def _values(text: str) -> tuple[str, ...]:
    """The closed set a tool prints, as the stub's `Literal` members."""
    return tuple(v.strip() for v in text.split(",") if v.strip())


def _kind(
    is_flag: bool, repeatable: bool, choices: tuple[str, ...], optional: bool = False
) -> str:
    if is_flag:
        return "bool"
    if optional:
        return "optvalue"  # a switch that also accepts a value
    if choices:
        return "choice[]" if repeatable else "choice"
    return "list[str]" if repeatable else "str"


def _coerce_default(text: str, is_flag: bool) -> object:
    if not text:
        return None
    if is_flag or text in {"true", "false"}:
        return text == "true"
    return text


def _pair_negations(options: list[Option]) -> list[Option]:
    """Fold `--no-x` entries into `x`, and drop them as options of their own.

    Every family that supports negation prints both spellings, so the pair
    is right there in the help — `--fix` and `--no-fix`, `--clean` and
    `--dirty` (that one only says so in prose). Folding them means `off`
    knows the tool's real spelling and the stub stays one keyword per
    concept, the way the tool's own docs read.
    """
    by_name = {o.name: o for o in options}
    folded: list[Option] = []
    negated: set[str] = set()
    for option in options:
        if not option.name.startswith("no_"):
            continue
        positive = by_name.get(option.name.removeprefix("no_"))
        if positive is not None and positive.type_name == "bool":
            negated.add(option.name)
            by_name[positive.name] = _with_negation(positive, option.flags[0])
    for option in options:
        if option.name in negated:
            continue
        folded.append(by_name[option.name])
    return folded


def _with_negation(option: Option, negation: str) -> Option:
    if option.negation:
        return option
    return Option(
        name=option.name,
        flags=option.flags,
        negation=negation,
        help=option.help,
        type_name=option.type_name,
        default=option.default,
        choices=option.choices,
    )


def parse_help(
    text: str, *, name: str = "", man: bool = False, shorts: str = "only"
) -> Verb:
    """One verb's options, from its `--help` output or (with `man`) manual.

    The option grammar is the same either way — the man page states a flag
    and its help exactly as `--help` does. Only the positional shape reads
    from a different place: a `usage:` line normally, the `SYNOPSIS` forms
    for a manual.
    """
    sections = _sections(text)
    # The flags the usage line has already given a value to. Where it has
    # spoken, the block's bare-lowercase-word rule is redundant at best and
    # wrong at worst — see `_spellings`.
    stated = set() if man else _grammar_values(_usage_line(text))
    options: list[Option] = []
    seen: dict[str, int] = {}
    for title, lines in sections.items():
        if _NOT_OPTIONS.search(title):
            continue  # `Commands:`, `Examples:` — dashes there aren't flags
        if not title:
            # The preamble is the one section a title cannot excuse, and the
            # usage line lives there. Wrapped, its continuation is an
            # indented line of bracketed flags — the shape of an option row.
            # `build` wraps at any width, and every flag on the continuation
            # was swept into one option carrying six flags and no help.
            lines = _drop_usage(lines)
        for head, help_text in _blocks(lines, man=man):
            first = _FIRST_LONG.search(head)
            option = _option(
                head,
                help_text,
                strict=man,
                shorts=shorts,
                bare_meta=not (first and first["flag"] in stated),
            )
            if option is None or option.name in _NOISE:
                continue
            if (kept := seen.get(option.name)) is not None:
                # A manual may state one option as several complete forms on
                # consecutive head lines (ssh's `-L` gives four), and only
                # the last carries the description. The first form stays the
                # option; a later twin only donates the help the kept one
                # lacks.
                if not options[kept].help and option.help:
                    options[kept] = replace(options[kept], help=option.help)
                continue
            seen[option.name] = len(options)
            options.append(option)
    if not options:
        # Go's `flag` prints its options under `Usage of <prog>:` — a section
        # `_NOT_OPTIONS` skips. Nothing parsed anywhere else, so scan every
        # section, including that one. Guarded on emptiness, so a tool that
        # parses normally never reaches here and can't regress.
        for _title, lines in sections.items():
            for head, help_text in _blocks(lines, man=man):
                option = _option(head, help_text, strict=man, shorts=shorts)
                if option is not None and option.name not in _NOISE:
                    seen[option.name] = len(options)
                    options.append(option)
        options = list({o.name: o for o in options}.values())
    if shorts == "all":
        options = _with_short_aliases(options)
    if not man:
        options = _arity_from_grammar(options, _usage_line(text))
    positional, lead = _synopsis_shape(text) if man else _usage_shape(text)
    return Verb(
        name=name,
        help=_summary(text),
        options=tuple(sorted(_pair_negations(options), key=lambda o: o.name)),
        positional=positional,
        lead=lead,
        wraps=_synopsis_wraps(text) if man else _wraps(text),
    )


def _arity_from_grammar(options: list[Option], grammar: str) -> list[Option]:
    """Believe the usage line where the option list said nothing.

    Most dialects state arity twice — `--config FILE` in the option block
    and `[--config FILE]` in the usage line — and the block is the richer
    source, so it wins. But a block is free to list a flag with prose and
    no metavar at all, and then the usage line is the *only* statement of
    arity in the whole document:

        Syntax: markdownlint-cli2 glob0 [--config file] [--fix]

        Optional parameters:
        - --config        specifies the path to a configuration file
        - --fix           updates files to resolve fixable issues

    Read alone, that block makes `--config` a switch, and the stub then
    types a path as `bool`. Read together, the tool has said plainly that
    one takes a value and the other does not.

    Only ever *adds* a value to something the block left bare: an option the
    block described with a metavar, a choice list or a default keeps what it
    said, because the block knows things the grammar cannot express. So a
    dialect that omits its options from the usage line loses nothing, and
    one that abbreviates with `[OPTIONS]` says nothing about any of them.
    """
    takes_value = _grammar_values(grammar)
    if not takes_value:
        return options
    out = []
    for option in options:
        bare = option.type_name == "bool" and not option.choices and not option.negation
        if bare and any(flag in takes_value for flag in option.flags):
            option = replace(option, type_name="str")
        out.append(option)
    return out


# `[--config file]`, `[--outdir OUTDIR]`, `[--installer {pip,uv}]` — a flag and
# its value inside the brackets that mark them optional together.
#
# Bracketed only, and that is the whole safety of it. `_usage_line` stitches
# indented continuation lines onto the usage, and a tool whose options block is
# indented under `Usage:` hands back the entire block as one line — so
# basedpyright's `--dependencies    Emit import dependency information` would
# read "Emit" as a metavar and turn nine real switches into strings. Inside
# brackets nothing but the grammar survives.
#
# The value is one token, and never another flag or an alternation bar: build
# groups its switches as `[--quiet | --verbose]`, where reading "| --verbose"
# as --quiet's value makes a string of a switch.
_GRAMMAR_PAIR = re.compile(
    r"\[(?P<flag>--[A-Za-z0-9][A-Za-z0-9_-]*)[ \t]+(?![-|])(?P<value>[^\s\[\]|]+)"
)

# The first long flag opening an option block, for asking the grammar about it.
_FIRST_LONG = re.compile(r"(?P<flag>--[A-Za-z0-9][A-Za-z0-9_-]*)")


def _grammar_values(grammar: str) -> set[str]:
    """The flags the usage line states a value for."""
    return {m["flag"] for m in _GRAMMAR_PAIR.finditer(grammar)} if grammar else set()


def _after_flags(head: str) -> str:
    """The prose left in a flag column after the flags that open it.

    Only the flags that *lead* — a description is free to name a flag mid
    sentence ("a JSON Pointer within the --config file"), and reading to the
    last one anywhere in the line would return "file" as the description.
    Once prose intervenes, the column is over.
    """
    end = 0
    for match in _SPELLING_STRICT.finditer(head):
        if match.start() > end and head[end : match.start()].strip(" ,|"):
            break
        end = match.end()
    return head[end:].strip(" ,")


def _with_short_aliases(options: list[Option]) -> list[Option]:
    """For `shorts="all"`: add a keyword for a short that *also* has a long,
    so `-m, --message` answers to both `message` and `m`. The long-keyed
    option stays; the alias is an extra entry keyed on the single char.
    """
    out = list(options)
    seen = {o.name for o in options}
    for option in options:
        for flag in option.flags:
            char = flag[1:]
            if len(flag) == 2 and char.isidentifier() and char not in seen:
                seen.add(char)
                out.append(replace(option, name=char))
    return out


# A metavar that stands for a *wrapped* command's argv: `uv run [COMMAND]`,
# `docker exec … COMMAND [ARG...]`.
_WRAP_METAVAR = frozenset({"command", "cmd", "args", "arg", "argv"})


def _wraps(text: str) -> bool:
    """Whether the verb forwards everything after its own args to a child.

    Signalled by a trailing command/argv metavar or coverage's literal
    "program options" — the mark of `uv run`, `docker exec`, `coverage run`.
    """
    usage = _usage_line(text)
    if "program option" in usage.lower():
        return True
    for token in _top_level_positionals(usage):
        base = re.split(r"[\[:]", token.strip("[]<>"))[0].lower()
        if base in _WRAP_METAVAR:
            return True
    return False


# The base of a positional metavar, before any `[:TAG]` / `<...>` suffix:
# `IMAGE`, `NAME` from `NAME[:TAG|@DIGEST]`, `repo` from `<repo>`.
_METAVAR = re.compile(r"^<?[A-Za-z][A-Za-z0-9_-]*>?$")


def _is_option_token(token: str) -> bool:
    """A usage token that is an option, a separator, or the `[OPTIONS]` slot
    — not a positional argument.
    """
    bare = token.strip("[]<>").lower()
    return not bare or bare in {"--", "|", "options", "flags"} or bare.startswith("-")


def _top_level_positionals(usage: str) -> list[str]:
    """The positional tokens at bracket depth 0.

    A usage grammar nests option groups in brackets — `[--reason <string>]`,
    `[--separate-git-dir <git-dir>]` — and whitespace-splitting scatters
    their *values* into loose tokens (`<string>]`) that look like bare
    positionals. Tracking depth keeps those out: only a token that starts
    while no bracket is open can be a real argument.
    """
    positional: list[str] = []
    depth = 0
    for token in usage.split():
        if depth == 0 and not _is_option_token(token):
            positional.append(token)
        depth = max(0, depth + token.count("[") - token.count("]"))
    return positional


def _usage_shape(text: str) -> tuple[str, str]:
    """`(positional, lead)` from a verb's `usage:` line.

    Two confident answers, everything else `"any"`:

    * `"none"` when the argument section is *only* options — mkdocs build's
      `[OPTIONS]`. A positional there is a type error.
    * `"required"` when a single clean metavar leads — `docker run IMAGE …`,
      `git clone <repo> …`. The stub makes it positional-only.

    Ambiguity stays `"any"`, because a wrong answer *forbids a valid call*.
    An option woven into an alternation (`<PACKAGES|--requirements …>`), a
    bracketed-optional or variadic first argument, an unfamiliar token — all
    fall through, so a real command is never rejected.
    """
    return _grammar_shape(_usage_line(text))


def _grammar_shape(grammar: str) -> tuple[str, str]:
    """`(positional, lead)` from one argument grammar (no program name)."""
    if not grammar:
        return "any", ""
    positional = _top_level_positionals(grammar)
    if not positional:
        return "none", ""
    first = positional[0]
    if any("--" in token for token in positional):
        return "any", ""  # a `<X|--flag>` alternation — packages OR a flag
    if first.startswith("[") or "..." in first:
        return "any", ""  # optional or variadic leading argument
    base = re.split(r"[\[:]", first.strip("[]<>"))[0]
    if not base or base[-1:].isdigit() or not _METAVAR.match(base):
        return "any", ""  # numbered (`path1`) or unrecognised — don't constrain
    return "required", base.replace("-", "_").lower()


def _synopsis_forms(text: str) -> list[str]:
    """Each complete form a man page's `SYNOPSIS` states, as its grammar.

    The manual restates the command per form, and the command is the page's
    own NAME — verbatim for a single-binary page (`ssh`, `ssh-keygen`), with
    dashes as spaces for a subcommand page (`git-clone` states `git clone`).
    A line that doesn't restate it is a wrapped continuation of the form
    above, and joins it.
    """
    match = re.search(
        r"(?ms)^SYNOPSIS[ \t]*\n(?P<body>.*?)\n(?:[A-Z][A-Z ]+\n|\Z)", text
    )
    if not match:
        return []
    body = match["body"]
    page = re.search(r"(?ms)^NAME[ \t]*\n[ \t]*(?P<name>[A-Za-z0-9._-]+)", text)
    if not page:
        return []
    for prog in dict.fromkeys((page["name"], page["name"].replace("-", " "))):
        pattern = rf"(?m)^[ \t]*{re.escape(prog)}(?=\s|$)"
        if re.search(pattern, body):
            chunks = re.split(pattern, body)
            return [" ".join(chunk.split()) for chunk in chunks[1:]]
    return []


def _synopsis_shape(text: str) -> tuple[str, str]:
    """`(positional, lead)` from a man page's `SYNOPSIS`.

    A verb with a *single* form has one grammar to read (`git clone …
    <repository> [<directory>]` → required); a verb with several — `git
    checkout` lists, detaches, creates, restores; `ssh` connects and
    queries — has no single shape, so it stays `"any"`.
    """
    forms = _synopsis_forms(text)
    if len(forms) != 1:
        return "any", ""  # multi-form (or unrecognised) — don't constrain
    return _grammar_shape(forms[0])


def _synopsis_wraps(text: str) -> bool:
    """`_wraps`, read from the `SYNOPSIS`: a manual has no `usage:` line.

    Any form ending in a command slot marks the wrapper — ssh's main form
    is `ssh … destination [command [argument ...]]`, and the flags of a
    call like that must precede the positionals or they land on the remote
    command instead of on ssh.
    """
    return any(
        re.split(r"[\[:]", token.strip("[]<>"))[0].lower() in _WRAP_METAVAR
        for form in _synopsis_forms(text)
        for token in _top_level_positionals(form)
    )


def _drop_usage(lines: list[str]) -> list[str]:
    """*lines* without the usage block — the line and what it wrapped onto.

    The same extent `_usage_line` stitches together, removed rather than
    joined: there it is the grammar, here it is prose that happens to be
    shaped like options.
    """
    out, skipping = [], False
    for line in lines:
        if line.lower().lstrip().startswith(("usage", "syntax")):
            skipping = True
            continue
        if skipping and line.strip() and line[:1].isspace():
            continue  # a continuation of the usage
        skipping = False
        out.append(line)
    return out


def _usage_line(text: str) -> str:
    """The `usage:` line, minus the program name, joined if it wraps.

    A wrapped usage (git's spans several indented lines) is stitched back
    together; the program name and any leading subcommands are dropped so
    only the argument grammar remains.

    `Syntax:` is the same line under a different name — markdownlint-cli2
    spells it that way, and skipping it cost the whole grammar: no usage
    line meant no arity for `--config`, which the option list states
    without a metavar.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        head = line.lower().lstrip()
        if not head.startswith(("usage", "syntax")):
            continue
        collected = [line]
        for cont in lines[i + 1 :]:
            if not cont.strip() or not cont[:1].isspace():
                break
            # git prints alternative forms as `   or: git branch …`. Only
            # the first form is parsed — stitching the alternatives together
            # would merge incompatible grammars into nonsense.
            if cont.lstrip().lower().startswith("or:"):
                break
            collected.append(cont)
        joined = " ".join(part.strip() for part in collected)
        after = re.sub(r"(?i)^usage:?\s*", "", joined)
        # Drop the program + verbs: everything up to the first bracket or
        # metavar-looking token is the command path, not an argument.
        tokens = after.split()
        rest = []
        seen_arg = False
        for token in tokens:
            if not seen_arg and (token.startswith(("[", "<")) or token.isupper()):
                seen_arg = True
            if seen_arg:
                rest.append(token)
        return " ".join(rest)
    return ""


def _unpadded(text: str) -> str:
    """One line of prose, with the layout that carried it collapsed.

    A manual is justified to whatever width it was rendered at, and the
    runs of spaces and tabs that do the justifying are not the tool's
    words. Left in, they make a reading depend on the reader: the same
    page read at two widths stores two strings, and the refresh reports a
    description that was reworded when only the padding moved.
    """
    return " ".join(text.split())


def _summary(text: str) -> str:
    """A tool's one-line self-description: its help's first prose line.

    A manual says it outright, under NAME — `git - the stupid content
    tracker` — where the first prose line of the page is the running
    header. Read from the machine's `git -h` this landed on a fragment of
    the usage line instead, which described nothing at all.
    """
    # The whole NAME block, not its first line: a page rendered narrow wraps
    # the description onto the next one, and reading to the line end alone
    # cut git-describe off at "based on an".
    named = _MAN_NAME.search(text)
    if named and (described := _MAN_DASH.match(_unpadded(named.group("body")))):
        return described[1]
    if re.match(r"^Usage of \S", text):
        return ""  # Go's `flag` opens with `Usage of <prog>:` and has no summary
    # A wrapped usage stands between the `usage:` line and the description,
    # and what it wraps onto decides what is found: a continuation opening
    # `[--sdist…` reads as prose and became the summary, one opening
    # `--config-json…` reads as an option and ended the search. Two
    # platforms wrapping differently disagreed about `build`'s description
    # for that reason, and neither had found it.
    for line in _drop_usage(text.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith("usage"):
            continue
        if (
            _BANNER.match(stripped)
            or _BARE_URL.match(stripped)
            or _BYLINE.match(stripped)
        ):
            # A signature, not a description: markdownlint-cli2 opens
            # `markdownlint-cli2 v0.23.2 (markdownlint v0.41.1)` and then its
            # project URL, and neither says what the tool does. Stored as the
            # description they also carry the version, so every release read
            # back as a tool rewriting its own summary. Skipped, this one
            # reaches its `Glob expressions:` heading and answers "none",
            # which is the truthful reading — some tools simply do not say.
            continue
        if (
            stripped.startswith("-")
            or _SECTION.match(stripped)
            or stripped.endswith(":")
        ):
            # Reached the options/sections with no summary in between — a tool
            # like python opens straight into `Options …:`, so it has none.
            return ""
        return _unpadded(stripped)
    return ""


# mdoc pages (every BSD manual, so the whole ssh family) separate the name
# from its description with an en dash, groff pages with a hyphen. Matching
# only the hyphen is how `ssh - OpenSSH remote login client` went unread and
# the summary fell through to the running header — page furniture, and
# tab-padded, so what got stored depended on the width the reader happened
# to render at. Spelled as escapes: `re` understands them, and the source
# stays ASCII where ruff reads a literal dash as ambiguous.
# A first line that signs the tool rather than describing it: a name, a
# version, optionally the version of what it wraps. Deliberately anchored
# whole-line — bun's description *ends* in a build stamp and is prose for
# every word before it, which `_unstamped` trims instead.
_BANNER = re.compile(r"^\S+ +v?\d[\w.+-]*(?: +\([^()]*\))?$")
_BARE_URL = re.compile(r"^https?://\S+$")
# Who wrote it, which clap prints between the signature and the description:
# `git-cliff contributors <git-cliff@protonmail.com>`. Skipping the version
# line alone landed on this one, which is a worse answer than the banner was.
_BYLINE = re.compile(r"^[^<>]*<[^<>@\s]+@[^<>\s]+>$")

_MAN_NAME = re.compile(r"^NAME\n(?P<body>(?:[ \t]+\S.*\n?)+)", re.M)
# The name, then the dash its format spells, then what the tool does. Read
# from the unwrapped block, so where the line broke stops mattering. The
# name half is non-greedy: a description may contain " - " of its own, and
# a page may list several names before the dash.
_MAN_DASH = re.compile(r"^.*? [-\u2013\u2014] (.+)$")


def subcommands(text: str) -> dict[str, str]:
    """`{name: summary}` from the `Commands:` section of a tool's help."""
    found: dict[str, str] = {}
    for title, lines in _sections(text).items():
        if not re.search(r"command|subcommand", title):
            continue
        for line in lines:
            match = re.match(
                r"^\s+(?P<name>[a-z][a-z0-9-]*)(?:,\s*[a-z0-9-]+)*"
                r"(?:\s{2,}(?P<help>.*))?$",
                line.rstrip(),
            )
            if match:
                found.setdefault(match["name"], (match["help"] or "").strip())
    return found


def run_help(
    argv: list[str], *, flag: str = "--help", man: bool = False, timeout: float = 30.0
) -> str:
    """`<tool> ... --help`, as text. Empty when the tool isn't installed.

    `argv[0]` is the executable to run — a bare name resolved on `PATH`, or the
    absolute path a caller already resolved (`from_help(..., binary=…)`).

    Help goes to stdout for every tool footman curates, but a few print
    usage to stderr on older versions, so both are read.

    `man` reads the manual instead — `git help <verb>` — for a tool whose
    terse `-h` omits most of its flags (git's `-h` shows about half). It
    runs only at stub-generation time, never at task time, so its heavier
    footprint (a rendered man page) costs a user nothing.
    """
    if man:
        # A manual can be read without the tool: `man` renders the pages a
        # release shipped, and for a fetched tree there is no binary at all.
        return _run_man(argv, timeout)
    if shutil.which(argv[0]) is None:
        return ""
    try:
        # UTF-8 rather than the locale codec, a hidden console, and a bound
        # that kills the tree: all of it is what `run()` does for a captured
        # call now, so the read says only what is particular to it. `recorded=False`
        # keeps a probe out of the run's story.
        done = _run(
            [*argv, flag],
            recorded=False,
            timeout=timeout,
            nofail=True,
            # A wide, dumb, colourless terminal: every family honours one
            # of these, and a narrow wrap costs nothing but re-joined prose
            # while a wide one keeps `[default: …]` on the line it belongs to.
            env={
                **os.environ,
                **QUIET,
                **DECIDED,
                "COLUMNS": "200",
                "TERM": "dumb",
                "NO_COLOR": "1",
            },
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    out, err = done.stdout or "", done.stderr or ""
    return _decoded(out if len(out) > len(err) else err, argv, flag, timeout)


def _decoded(text: str, argv: list[str], flag: str, timeout: float) -> str:
    """*text*, re-read in the machine's own code page if UTF-8 mangled it.

    Captured output is decoded as UTF-8 because dev tools emit it whatever
    the OS code page says — but *whatever* is doing a lot of work there.
    djLint prints its banner's `·` as one cp1252 byte on Windows, and UTF-8
    with `errors="replace"` turned that into U+FFFD: the reading went into
    the store, the delta said the help text had changed, and djlint 1.43.2
    was credited with an event it never had.

    A replacement character is the decoder admitting it lost a byte, so it
    is the signal to ask again with `encoding=None` — the locale codec,
    which is what a tool that ignored UTF-8 was speaking. If that reading
    is clean it wins; if it is not, the caller still sees a U+FFFD and the
    observer refuses to record it rather than inventing a change.
    """
    if "�" not in text:
        return text
    try:
        again = _run(
            [*argv, flag],
            recorded=False,
            timeout=timeout,
            nofail=True,
            encoding=None,  # the locale codec: what the tool actually spoke
            env={
                **os.environ,
                **QUIET,
                **DECIDED,
                "COLUMNS": "200",
                "TERM": "dumb",
                "NO_COLOR": "1",
            },
        )
    except (OSError, subprocess.SubprocessError):
        return text
    out, err = again.stdout or "", again.stderr or ""
    relocalised = out if len(out) > len(err) else err
    return relocalised if "�" not in relocalised else text


# A captured read must not expose the *caller's* console: a tool that
# interrogates the terminal at start-up (tea 0.13.0-0.14.2 sent an OSC
# theme query) blocks forever waiting for a reply no pipe will ever carry —
# so whether a read hung followed whatever window the walk was launched
# from. CREATE_NO_WINDOW gives the read a fresh hidden console instead.
# That does NOT cure a determined interrogator — measured, tea's band
# queries any attached console, VT or not, and hangs against the hidden
# one too (those releases sit below a provision floor for exactly that
# reason). What the flag buys is *determinism*: the same result from every
# terminal, while console-hosted runtimes still get the console they
# need — fully detached, pwsh dies at start-up and git-bash goes mute.
# The same choice footman's own detached children make (`_complete`,
# `_app`), for the sibling reason: console-less, Windows Terminal hands
# each spawn a visible window.
NO_CONSOLE_WINDOW: int = (
    getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
)

# Man renders bold/underline as `c\x08c` / `_\x08c` overstrike; dropping the
# char-then-backspace pair leaves clean text, no `col` binary needed.
_OVERSTRIKE = re.compile(r".\x08")

# groff breaks a long word across lines with U+2010 HYPHEN — the typographic
# one, never the ASCII hyphen-minus a literal hyphen in the source renders
# as. So the character *is* the marker for "I inserted this here", and
# putting the word back together is exact rather than a guess: ssh's page
# read "authenticated en-\ncryption" with that character, and it reached a
# shipped stub. It cost two CI failures — ruff reads U+2010 in a docstring as
# ambiguous (RUF002), and its UTF-8 tail byte 0x90 is undefined in cp1252,
# which is what Windows decodes with when a test forgets `encoding=`. Spelled
# as an escape below, because writing it literally trips RUF001 right here.
_U2010 = "\u2010"  # the character itself; escaped so ruff sees no ambiguity
_SOFT_HYPHEN = re.compile(_U2010 + r"\n\s*")


def _dehyphenate(text: str) -> str:
    """Undo groff's line-breaking hyphenation, and keep the text ASCII.

    A U+2010 anywhere else is still not a hyphen anyone typed, so it becomes
    one: the stubs are read by ruff, by Windows, and by people grepping for
    `--all-files`, and all three want the character on their keyboard.
    """
    return _SOFT_HYPHEN.sub("", text).replace(_U2010, "-")


def man_version(tree: Path, name: str = "git") -> str:
    """The version a fetched manual belongs to, from its own header.

    The installer stamps the tree with the release it fetched, per tool
    (`VERSION-ssh`) because the provision tier merges every manual into one
    tree and git's release is not ssh's — and the stamp is the only
    statement there is for mdoc pages, which say no version anywhere.
    git's older trees predate the stamp and carry it in their `.TH` line
    instead: `.TH "GIT" "1" "2025-06-15" "Git 2\\&.50\\&.1" "Git Manual"`.
    Either way the tree says which release it documents, so a reading
    names its own version the way a binary does, and the guard against
    describing the wrong release works unchanged.
    """
    stamp = tree / f"VERSION-{name}"
    try:
        stamped = stamp.read_text(encoding="utf-8").strip()
    except OSError:
        stamped = ""
    if stamped:
        return stamped
    page = tree / "man1" / "git.1"
    try:
        head = page.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return ""
    found = re.search(r'^\.TH\s+"[^"]*"\s+"[^"]*"\s+"[^"]*"\s+"([^"]*)"', head, re.M)
    if not found:  # pragma: no cover - every page carries a .TH line
        return ""
    title = found.group(1).replace("\\&", "")
    number = _VERSION_IN_TITLE.search(title)
    return number.group(0) if number else ""


_VERSION_IN_TITLE = re.compile(r"\d+(?:\.\d+)+")


def _fetched_manpath() -> str:
    """The manual tree an observation pointed us at, if any.

    Set by the walk around one release's pages. Empty means read the
    machine's own manuals, which is what stub generation did before there
    was anything else to read.
    """
    return os.environ.get("FOOTMAN_MANPATH", "")


def _run_man(argv: list[str], timeout: float) -> str:
    """`<tool> help <verb>`, de-overstruck — the manual as plain text.

    Against a fetched tree it is `man -M <tree> <tool>-<verb>` instead:
    `git help` needs git, and the whole point of reading a manual is that
    the release it documents never has to be installed.
    """
    tree = _fetched_manpath()
    if tree:
        # The page is named for the *tool*, not for the binary a caller
        # resolved: `from_help` passes an absolute path as argv[0], and
        # `man` would go looking for a page called `/opt/homebrew/…/git-add`.
        # `git help git` asks for the tool's own page, which is `git`, not
        # `git-git`.
        tool = Path(argv[0]).name
        rest = [part for part in argv[1:] if part != tool]
        return _render_page(tree, "-".join([tool, *rest]), timeout)

    env = {
        **os.environ,
        **QUIET,
        "GIT_PAGER": "cat",
        "PAGER": "cat",
        "MANPAGER": "cat",
        "MAN_KEEP_FORMATTING": "",
        "COLUMNS": "200",
        # `git help` honours `help.format`, and on Windows it defaults to
        # html — which *opens a browser tab per verb* instead of printing
        # anything. Pin the format to man: a POSIX box reads the same text
        # it always did, and a box with no man viewer fails quietly into
        # the empty-text fallback rather than launching twenty tabs.
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "help.format",
        "GIT_CONFIG_VALUE_0": "man",
    }
    try:
        done = _run(
            [argv[0], "help", *argv[1:]],
            recorded=False,
            timeout=timeout,
            nofail=True,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return _dehyphenate(_OVERSTRIKE.sub("", done.stdout or ""))


def _render_page(tree: str, page: str, timeout: float) -> str:
    """One page of a fetched manual tree, as plain text."""
    if shutil.which("man") is None:
        return ""
    try:
        done = _run(
            # Absolute: `man -M` given a relative manpath finds nothing and
            # says so by rendering an empty page rather than failing, which
            # reads downstream as a release that documented nothing.
            ["man", "-M", str(Path(tree).resolve()), page],
            recorded=False,
            timeout=timeout,
            nofail=True,
            env={
                **os.environ,
                "MANPAGER": "cat",
                "PAGER": "cat",
                "MAN_KEEP_FORMATTING": "",
                "COLUMNS": "200",
            },
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return _dehyphenate(_OVERSTRIKE.sub("", done.stdout or ""))


DECIDED = {"DOCKER_BUILDKIT": "1"}
"""A choice the tool would otherwise take from the machine reading it.

`docker build` is buildx wherever buildx is installed, which is why the
driver installs it — but with `DOCKER_BUILDKIT` unset the CLI only routes
`build` that way for a Linux daemon, and a reader has no daemon at all.
So Linux and macOS read buildx while Windows fell back to the builder
docker shipped with before 2019, and one verb arrived as two surfaces:
`--compress` and `--cpu-shares` from one leg, `--platform` and `--push`
from the others, each recorded as missing on the platforms that saw the
other. Twelve options of a per-platform difference that no user has.

Docker's own notes retire the classic builder for Linux images and name
the exception — native Windows *containers* — which is not the docker
this describes: a Windows user on the usual backend talks to a Linux
daemon and gets buildx like everyone else. Pinning the opt-in reads the
builder that is actually installed, the same way `COLUMNS` and
`help.format` are pinned so a reading describes the tool and not the
machine that took it.
"""

QUIET = {"GH_NO_UPDATE_NOTIFIER": "1"}
"""Tools told not to phone home while being read.

gh runs its update check from *any* command unless told otherwise: a
network call, and a banner it writes alongside the answer. A walk asks
`gh --help` and `gh <verb> --help` once per release, so that is a request
per read and a chance per read for the notice to land in the surface.

Reading a tool must never depend on, or be delayed by, the network.
"""


def _is_the_root_again(text: str, root: str) -> bool:
    """Whether a verb answered with the tool's own help instead of its own.

    A tool asked for a subcommand it does not have does not always fail:
    docker prints its root help and exits, so the reading looked like a
    successful one and `compose up` was recorded with docker's global
    options and docker's own summary. Nothing downstream could tell —
    it is a real help text, just not this verb's — and a walk that lost
    its compose plugin for one release would write that release's compose
    surface as ten global flags.

    Compared on the whole text, which is exact: two verbs of the same tool
    share phrasing but never the entire page.
    """
    return text.strip() == root.strip()


def _unstamped(summary: str, version: str) -> str:
    """A description with the tool's own build stamp taken off.

    Some tools sign their help rather than describe themselves: bun ends
    its line `... and test runner. (1.4.0+34cbb9a40)`. The stamp moves
    every release, so storing it makes an ordinary version bump look like
    the tool rewriting its description, and every refresh reports a change
    nobody made.

    Only a trailing parenthetical *containing the version just read* comes
    off, which is narrow on purpose: a description that genuinely ends in
    brackets keeps them, and a manual page's `SSH(1)` is not a stamp.
    """
    if not version:
        return summary
    stamp = re.compile(rf"\s*\([^()]*{re.escape(version)}[^()]*\)\s*$")
    return stamp.sub("", summary).rstrip()


def from_help(
    name: str,
    *,
    binary: str | None = None,
    verbs: tuple[str, ...] = (),
    version: str = "",
    in_process: bool = False,
    flag: str = "--help",
    man: bool = False,
    shorts: str = "only",
) -> ToolSpec:
    """A `ToolSpec` for *name* by asking the installed binary.

    *binary* is the executable to run (the caller may have resolved it, e.g. to
    a Homebrew keg); it defaults to *name*, resolved on `PATH`. The tool's own
    verb names still ride as `name`/verbs in each argv, only the executable
    differs.

    Each verb costs one `<tool> <verb> --help` (or `<tool> help <verb>`
    with `man`); the root call supplies the tool's summary and its global
    options (verb `""`). With `man`, per-verb manuals are read but the root
    stays on `--help`, which is where a tool prints its verb list.
    """
    cmd = binary or name
    # Against a fetched manual there is no binary to ask for a usage line,
    # and asking the machine's own would describe a different release.
    root = run_help([cmd], flag=flag, man=man and bool(_fetched_manpath()))
    if not root:
        return ToolSpec(name=name, version=version)
    root_verb = parse_help(root, name="", shorts=shorts)
    if verbs:
        # A multi-command tool's bare usage line (`docker [OPTIONS] COMMAND`)
        # describes the subcommand slot, not arguments to `docker` itself —
        # so `tools.docker(...)` must not be constrained by it. Only a
        # single-command tool's root verb carries a real positional shape.
        root_verb = replace(root_verb, positional="any", lead="", wraps=False)
    if man:
        # The terse root help (`git -h`) lists subcommands, not globals; the
        # tool's own manual (`git help git`) lists the options that must
        # precede the verb — what `.opts()` binds. Read them from there.
        manual = run_help([cmd, name], man=True)
        if manual:
            from_manual = parse_help(manual, man=True, shorts=shorts)
            root_verb = replace(root_verb, options=from_manual.options)
            if not verbs:
                # A verb-less manual tool (ssh) *is* its root: the manual's
                # SYNOPSIS is the only statement of its shape, and whether
                # it wraps a trailing command — the terse root read above
                # had no usage line to say either.
                root_verb = replace(
                    root_verb,
                    positional=from_manual.positional,
                    lead=from_manual.lead,
                    wraps=from_manual.wraps,
                )
    parsed = [root_verb]
    for verb in verbs:
        text = run_help([cmd, *verb.split(".")], flag=flag, man=man)
        if text and not _is_the_root_again(text, root):
            # `git rev-parse` is spelled `tools.git.rev_parse(...)`: the
            # bridge turns the underscore back into a dash when it calls.
            parsed.append(
                parse_help(text, name=verb.replace("-", "_"), man=man, shorts=shorts)
            )
    # The tool's own line, and the root verb's when it is the same line —
    # a verb-less tool describes itself once and it lands in both.
    banner = _summary(root)
    summary = _unstamped(banner, version)
    if summary != banner:
        parsed = [
            replace(verb, help=summary) if verb.help == banner else verb
            for verb in parsed
        ]
    return ToolSpec(
        name=name,
        help=summary,
        version=version,
        verbs=tuple(parsed),
        in_process=in_process,
    )
