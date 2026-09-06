"""Phrase manifest nodes for humans: labels, usage lines, examples.

The one home for turning a task's manifest entry into words, shared by the
help renderer (`_app`) and the markdown exporter (`markdown`) so the two can
never drift. Everything here is a pure function over manifest dicts — no
registry, no I/O.
"""

from __future__ import annotations

import dataclasses
import datetime
import decimal
import enum
import json
import re
import unicodedata
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path, PurePath
from typing import Any

TYPE_WORD = {
    "bool": "true/false",
    "int": "an integer",
    "float": "a number",
    "path": "a path",
    "str": "text",
}


# --- the palette --------------------------------------------------------------
# One visual language for the whole CLI: bold for names and headers, dim for
# mechanics and secondary text, cyan for footman's own numbers and accents,
# green/red for verdicts. Every helper is a no-op when *on* is False, and
# every surface gates *on* by its own stream's tty-ness — piped output stays
# byte-clean.


def wants_color(stream: Any, mode: str = "auto") -> bool:
    """Whether to paint output for *stream* under a resolved colour *mode*.

    `"always"`/`"never"` are the explicit tri-state answers (`--color=…`, config,
    `FORCE_COLOR`/`NO_COLOR`); `"auto"` (the default) falls back to the stream's
    own tty-ness, honouring `NO_COLOR` and a dumb terminal.
    """
    if mode == "never":
        return False
    if mode == "always":
        return True
    import os as _os

    return (
        ansi_capable(stream)
        and "NO_COLOR" not in _os.environ
        and _os.environ.get("TERM") != "dumb"
    )


def ansi_capable(stream: Any) -> bool:
    """tty-ness plus, on Windows, a console that interprets ANSI.

    A Windows tty is not yet an ANSI surface: legacy conhost prints the
    escapes as `←[1m` noise. Ask the console for VT processing the way
    every modern CLI does — a no-op where it is already on (Windows
    Terminal), an upgrade where it is off but available (Windows 10's
    conhost), and a refusal exactly where the escapes would show as noise.
    """
    try:
        tty = bool(stream.isatty())
    except Exception:
        return False
    import os as _os

    if not tty or _os.name != "nt":
        return tty
    try:
        fileno = stream.fileno()
    except Exception:
        # No OS handle to interrogate: a tty-claiming stream without one is
        # a wrapper (or a test fake) that answers for its own rendering, so
        # its claim stands. Every real console stream has a fileno, so the
        # probe below still covers the console that cannot paint.
        return True
    try:
        import ctypes
        import msvcrt

        # getattr with a default, not attribute access: both exist only on
        # Windows, and the checkers read this file on every platform.
        get_handle = getattr(msvcrt, "get_osfhandle", None)
        windll = getattr(ctypes, "windll", None)
        if get_handle is None or windll is None:
            return False
        handle = get_handle(fileno)
        kernel32 = windll.kernel32
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        vt = 0x0004  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        if mode.value & vt:
            return True
        return bool(kernel32.SetConsoleMode(handle, mode.value | vt))
    except Exception:
        return False


def link(text: str, url: str | None, on: bool) -> str:
    """*text* as a terminal hyperlink (OSC 8), when *on* and a URL exists.

    Zero visible width, so band layouts that track plain lengths stay
    untouched. Rides the same switch as colour: piped output never carries
    it, and a terminal without OSC 8 support ignores the sequence.
    """
    if not on or not url:
        return text
    return f"\033]8;;{url}\033\\{text}\033]8;;\033\\"


_DOCS_PLACEHOLDERS = ("path", "slug")


def docs_url_error(template: object) -> str | None:
    """The refusal a broken `docs_url` template earns, or None for a sound
    one — a link scheme that silently pointed nowhere would be worse than
    no links at all."""
    from livery.footman import _paths

    if not isinstance(template, str) or not template.strip():
        return (
            f"the [tool.{_paths.config_table()}] docs_url key is a URL "
            "template, e.g. "
            '"https://docs.example.dev/tasks/{path}/" — {path} is the '
            "slash-joined task address, {slug} the dash-joined one"
        )
    unknown = [
        m for m in re.findall(r"\{([^{}]*)\}", template) if m not in _DOCS_PLACEHOLDERS
    ]
    if unknown:
        return (
            f"docs_url: unknown placeholder {{{unknown[0]}}} — use {{path}} "
            f"(slash-joined address) or {{slug}} (dash-joined)"
        )
    return None


def docs_url_for(template: str | None, address: str) -> str | None:
    """*address*'s documentation URL under *template*, or None without one.

    `{path}` walks the generated per-task pages (`docs.site`: one file per
    task, directories per group); `{slug}` jumps to the one-page export's
    anchors (`docs.page`: `{ #docs-build }`). Plain replacement, not
    `format` — a URL's own braces are already refused by validation.
    """
    if not template or not address:
        return None
    return template.replace("{path}", address.replace(".", "/")).replace(
        "{slug}", address.replace(".", "-")
    )


def bold(text: str, on: bool) -> str:
    return f"\033[1m{text}\033[0m" if on else text


def dim(text: str, on: bool) -> str:
    return f"\033[2m{text}\033[0m" if on else text


def cyan(text: str, on: bool) -> str:
    return f"\033[36m{text}\033[0m" if on else text


def bold_cyan(text: str, on: bool) -> str:
    return f"\033[1;36m{text}\033[0m" if on else text


def red(text: str, on: bool) -> str:
    return f"\033[31m{text}\033[0m" if on else text


# --- columns ------------------------------------------------------------------
# Every aligned surface asks the same question — how wide is this on screen? —
# and `len()` answers a different one, in three ways at once: an escape
# sequence is bytes the terminal eats rather than shows, a combining mark
# rides on the character before it, and an East-Asian wide character (or an
# emoji) takes two cells. One helper, so a column is a column everywhere.

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _cell(ch: str) -> int:
    if unicodedata.category(ch) in ("Mn", "Me", "Cf"):
        return 0  # combining marks and format codes ride on their neighbour
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def display_width(text: str) -> int:
    """How many terminal cells *text* occupies."""
    if text.isascii() and "\033" not in text:
        # Plain ASCII is one cell per character and carries no escapes — two
        # C-speed checks so the common case (every column footman prints
        # about itself) costs what `len()` cost before.
        return len(text)
    return sum(_cell(ch) for ch in _ANSI.sub("", text))


def pad_to(text: str, width: int) -> str:
    """*text* followed by spaces up to *width* cells — `str.ljust` counting
    what the terminal counts."""
    return text + " " * max(width - display_width(text), 0)


def fit(text: str, width: int) -> str:
    """*text* cut to *width* cells, escape sequences kept whole.

    Never cuts inside an escape (which would print its tail as literal
    gibberish) and never splits a wide character down the middle; whatever
    styling was still open at the cut is closed, so a truncated status line
    cannot leave the terminal wearing a colour nobody chose.
    """
    if display_width(text) <= width:
        return text
    out: list[str] = []
    used = 0
    styled = False

    def take(run: str) -> bool:
        nonlocal used
        for ch in run:
            cells = _cell(ch)
            if used + cells > width:
                return True
            out.append(ch)
            used += cells
        return False

    pos = 0
    for match in _ANSI.finditer(text):
        if take(text[pos : match.start()]):
            break
        out.append(match.group())
        styled = match.group() != "\033[0m"
        pos = match.end()
    else:
        take(text[pos:])
    return "".join(out) + ("\033[0m" if styled else "")


def value_hint(p: dict[str, Any]) -> str:
    """The value placeholder shown for an option/argument in help output."""
    if p.get("mapping"):
        return "KEY=VALUE"
    if group := p.get("group"):
        # `--size=width,height` beats `--size=VALUE`: the shape named its own
        # slots, so the help can too.
        return str(group["label"])
    choices = p.get("choices")
    if choices:
        return "{" + "|".join(choices) + "}"
    types = p.get("types")
    if types:
        return "|".join(t.upper() for t in types)
    return "VALUE"


def _repeats(p: dict[str, Any]) -> bool:
    """Whether the parameter takes a *stream* of values rather than one.

    A grouped shape is `multiple` to the splitter because its values
    accumulate the same way, but `--size=width,height` takes one size — the
    `...` belongs only to a container of them.
    """
    if group := p.get("group"):
        return bool(group["many"])
    return bool(p.get("multiple"))


def usage_fragment(p: dict[str, Any]) -> str:
    kind = p["kind"]
    required = p.get("required")
    if kind == "stdin":
        return ""  # a whole-document parameter has no token spelling
    if kind == "flag":
        name = f"no-{p['name']}" if p.get("default") is True else p["name"]
        return f"--{name}" if required else f"[--{name}]"
    if kind == "option":
        core = f"--{p['name']}={value_hint(p)}"
        if _repeats(p) or p.get("mapping"):
            core += " ..."
        return core if required else f"[{core}]"
    if kind == "variadic":
        return f"[<{p['name']}> ...]"
    suffix = "..." if p.get("multiple") else ""
    return f"<{p['name']}>{suffix}"


def param_label(p: dict[str, Any]) -> str:
    kind = p["kind"]
    if kind == "flag":
        return f"--no-{p['name']}" if p.get("default") is True else f"--{p['name']}"
    if kind == "option":
        return f"--{p['name']}={value_hint(p)}"
    suffix = "..." if kind == "variadic" or p.get("multiple") else ""
    return f"<{p['name']}>{suffix}"


def param_detail(p: dict[str, Any]) -> str:
    doc, mechanics = param_detail_parts(p)
    return "; ".join(bit for bit in (doc, mechanics) if bit)


def param_detail_parts(p: dict[str, Any]) -> tuple[str, str]:
    """(author's doc, the mechanical suffix) — split so help can dim the
    mechanics under the author's words."""
    return p.get("doc", ""), _mechanics(p)


def default_text(p: dict[str, Any]) -> str:
    """The declared default, spelled the way the command line spells values, or
    `""` when there is nothing worth printing.

    `None` prints as nothing rather than "None": absence is what it means, and
    "the default is None" tells a reader less than saying nothing does. An empty
    string is printed as `""`, because there it is the value.
    """
    if "default" not in p or p.get("required"):
        return ""
    value = p["default"]
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value) if value else "(none)"
    if isinstance(value, dict):
        return ",".join(f"{k}={v}" for k, v in value.items()) if value else "(none)"
    return str(value) if value != "" else '""'


def _mechanics(p: dict[str, Any]) -> str:
    bits: list[str] = []
    if p["kind"] == "flag":
        # A flag defaulting true is only ever *turned off*, so the spelling
        # that does something leads, and the inert one is the parenthetical.
        if p.get("default") is True:
            bits.append(f"flag (--{p['name']} to enable)")
        else:
            bits.append(f"flag (--no-{p['name']} to disable)")
    choices = p.get("choices")
    if choices:
        # A dynamic parameter's values are what its completer answered just
        # now, not a fixed set — say so, the way a computed default does, so
        # nobody reads this list as the law of the task. A *static* option's
        # set is already in its own spelling (`--mode={dev|prod}`), so
        # repeating it here would print the list twice in one row; only a
        # positional, whose spelling is just its name, still needs the list.
        listed = "one of " + "|".join(choices)
        if p.get("dynamic"):
            bits.append(f"{listed} (dynamic)")
        elif p["kind"] == "positional":
            bits.append(listed)
    elif p.get("types"):
        bits.append(" or ".join(TYPE_WORD.get(str(t), str(t)) for t in p["types"]))
    if p.get("mapping"):
        bits.append("KEY=VALUE pairs (repeat appends)")
    if group := p.get("group"):
        # A grouped shape is `multiple` to the splitter — commas and repetition
        # feed one stream — but saying "comma-split" would describe the wiring
        # rather than the spelling. What a reader needs is the arity.
        if group["many"]:
            bits.append(f"repeatable in groups of {group['max']}")
        elif group["min"] < group["max"]:
            bits.append(f"{group['min']} to {group['max']} values")
    elif p.get("multiple") or p.get("mapping"):
        bits.append("repeatable" if p.get("nosplit") else "repeatable/comma-split")
    if p["kind"] == "variadic":
        bits.append("extra arguments (also receives everything after --)")
    source = p.get("stdin")
    if source:
        if source.startswith("field:"):
            note = f"reads stdin (JSON field {source[6:]!r})"
        elif source == "lines":
            note = "reads stdin (one line per value)"
        elif source == "bytes":
            note = "reads stdin (raw bytes)"
        elif source == "json":
            shape = p.get("shape")
            named = f" → {shape['name']}" if shape else ""
            note = f"reads stdin (JSON document{named})"
        else:
            note = "reads stdin (text)"
        bits.append(note)
    # In ladder order, so the line reads the way resolution happens: the
    # environment answers first, the declared default last. Both were in the
    # manifest all along and neither was ever printed — a help page that knows
    # a parameter falls back to $DEPLOY_ENV and does not say so is holding out.
    if (var := p.get("env")) is not None:
        bits.append(f"from ${var}")
    # A flag's default is already said twice over by the label and its
    # parenthetical, so printing it again would be noise.
    if p["kind"] != "flag":
        shown, computed = default_text(p), p.get("computed")
        if shown and computed:
            # `default: 13` reads as an arbitrary constant. Saying it is
            # computed turns a number a reader might copy into one they know
            # is theirs — the machine's cores, today's date, this shell.
            bits.append(f"default: {shown} (computed)")
        elif shown:
            bits.append(f"default: {shown}")
        elif computed:
            # Computed from the other options, so there is no value to print
            # here: only an invocation knows it. Say that much rather than
            # nothing, or the parameter looks like it has no default at all.
            bits.append("default computed")
    if p.get("required"):
        bits.append("required")
    return "; ".join(bits)


def uses_line(task: dict[str, Any], tree: dict[str, Any]) -> str:
    """The globals a task declared it reads (`@task(uses=[...])`), with
    provenance — the dependency shown where the option will be typed."""
    names = task.get("uses")
    if not names:
        return ""
    owners = {g["name"]: g.get("owner") for g in tree.get("globals", ())}
    bits = [
        f"--{name}" + (f" (from {owner})" if (owner := owners.get(name)) else "")
        for name in names
    ]
    return "reads " + ", ".join(bits)


def sample_value(p: dict[str, Any]) -> str:
    """A realistic value for a param in a synthesised example: its first choice
    when it has one, else an `<name>` placeholder."""
    choices = p.get("choices")
    return choices[0] if choices else f"<{p['name']}>"


# CLI lines (usage, examples) are token lists — (kind, text) — so every
# renderer paints the same structure: `prog` bold, `group` bold cyan (as in
# the tree), `task` bold, `req`/`value` cyan, `opt` dim, `flag` plain.
_CLI_PAINT: dict[str, Callable[[str, bool], str]] = {
    "prog": bold,
    "group": bold_cyan,
    "task": bold,
    "req": cyan,
    "value": cyan,
    "opt": dim,
    "flag": lambda text, on: text,
}


def paint_cli(parts: list[tuple[str, str]], on: bool) -> str:
    """The one way to print a command line, syntax-lit by token kind."""
    return " ".join(_CLI_PAINT.get(kind, bold)(text, on) for kind, text in parts)


def invocation_parts(prog: str, path: list[str]) -> list[tuple[str, str]]:
    """`prog dotted.task` as tokens — the head of every usage and example.

    The address is one dotted token (`fm docs.build`), never a token walk —
    what help shows must be what the splitter accepts."""
    parts: list[tuple[str, str]] = [("prog", prog)]
    if path:
        parts.append(("task", ".".join(path)))
    return parts


def listed_params(
    task: dict[str, Any], *, show_hidden: bool = False
) -> list[dict[str, Any]]:
    """The parameters a listing shows — everything but the hidden ones.

    The task-level rule, one level down: `hidden` takes a parameter out of
    what a human reads and out of nothing else. It still binds, it still
    completes, and the manifest still carries it marked, because hiding and
    completing are different questions. `--all` shows it.
    """
    return [p for p in task["params"] if show_hidden or not p.get("hidden")]


def usage_parts(
    prog: str, path: list[str], task: dict[str, Any], *, show_hidden: bool = False
) -> list[tuple[str, str]]:
    parts = invocation_parts(prog, path)
    for p in listed_params(task, show_hidden=show_hidden):
        fragment = usage_fragment(p)
        if fragment:
            kind = "opt" if fragment.startswith("[") else "req"
            parts.append((kind, fragment))
    return parts


def example_parts(
    path: list[str], task: dict[str, Any], prog: str
) -> list[tuple[str, str]]:
    """A realistic invocation synthesised straight from the signature — required
    positionals and options with sample values, plus one representative flag.

    Derived, never written, so it can't drift from the task's actual parameters.
    Optional options are skipped as noise; the shape teaches the invocation.
    """
    parts = invocation_parts(prog, path)
    flag_shown = False
    for p in listed_params(task):
        kind = p["kind"]
        if kind in ("positional", "variadic"):
            parts.append(("value", sample_value(p)))
        elif kind == "option" and p.get("required"):
            # One attached token, because that is the only spelling footman
            # accepts: `--out <file>` is exit 64 with "did you mean --out=…?".
            # An example is a line to copy, so it has to be a line that runs.
            parts.append(("value", f"--{p['name']}={sample_value(p)}"))
        elif kind == "flag" and (p.get("required") or not flag_shown):
            parts.append(("flag", f"--{p['name']}"))
            flag_shown = True
    return parts


def example(path: list[str], task: dict[str, Any], prog: str) -> str:
    """The example invocation as plain text (the markdown exporter's form)."""
    return " ".join(text for _, text in example_parts(path, task, prog))


def task_line(task: dict[str, Any]) -> str:
    """A task's one-line description, plus how it ends when that's notable:
    availability if disabled, the Ctrl-C note if it runs until stopped."""
    notes = []
    if task.get("infinite"):
        notes.append("(runs until Ctrl-C)")
    if task.get("disabled"):
        notes.append(f"(unavailable: {task['disabled']})")
    line: str = task["help"]
    if not notes:
        return line
    return f"{line}  {' '.join(notes)}".strip()


def default_line(node: dict[str, Any]) -> str:
    """The one-line description of a runnable group's default action.

    The author's docstring when there is one; otherwise generated from what
    the default actually does — an empty body fans the group's tasks out, a
    custom body runs as written — so an undocumented default is still
    explained, never a blank cell.
    """
    default = node["default"]
    line = task_line(default)
    if line:
        return line
    if node.get("default_fanout"):
        return "run every task in this group"
    return "run this group's default action"


def listed(node: dict[str, Any], *, show_hidden: bool = False) -> bool:
    """Whether a task or group node belongs in a human listing.

    Two things take one out, and they are different in kind. `hidden` is a
    task nobody is meant to *read about*: it stays callable, still completes,
    shows up under `--json` marked, and `--all` reveals it — because a machine
    is exactly who calls it. `unexposed` is a task that does not belong
    *here at all*: nothing outside a project can run it, so listing it would
    be an offer footman cannot honour, and `--all` does not bring it back.
    """
    if node.get("unexposed"):
        return False
    return show_hidden or not node.get("hidden")


def has_listed(node: dict[str, Any], *, show_hidden: bool = False) -> bool:
    """Whether anything under *node* is worth printing.

    Deliberately *not* short-circuited on the group's own `hidden`: hiding a
    group hides everything that inherits from it, but a child that answered
    `hidden=False` is still listed, and it needs its heading to be placed.
    A group with nothing listed under it prints no heading at all, rather
    than one with nothing beneath it.
    """
    if "default" in node and listed(node["default"], show_hidden=show_hidden):
        return True
    return any(
        listed(t, show_hidden=show_hidden) for t in node["tasks"].values()
    ) or any(
        has_listed(sub, show_hidden=show_hidden) for sub in node["groups"].values()
    )


def ordered_tasks(node: dict[str, Any]) -> dict[str, Any]:
    """A group's tasks with its `default` first, then declaration order.

    The default *is* the group — `fm db` runs it, and the group's own row is
    described by it — so a listing that showed it wherever it happened to be
    written put the group's headline act somewhere in the middle, or at the
    bottom. Where it sits in the file is the author's business; where it sits
    in a listing is footman's.
    """
    tasks: dict[str, Any] = node["tasks"]
    if "default" not in tasks:
        return tasks
    return {"default": tasks["default"], **tasks}


def walk(
    node: dict[str, Any],
    prefix: str = "",
    depth: int = 0,
    *,
    show_hidden: bool = False,
    dedupe_defaults: bool = False,
    _covered: bool = False,
) -> Iterator[tuple[int, str, str, str, str]]:
    """The one traversal every human listing reads — `--list`, `--tree`, group
    help, and the did-you-mean index.

    Yields `(depth, address, leaf, help, kind)` per row, parents before their
    children, hidden nodes and empty groups already gone: `--list` renders the
    address and ignores the depth, `--tree` renders the leaf and indents by it.
    Two views of one walk, so a rule about what is listed cannot be true of
    one and false of the other. *show_hidden* — `--all`, and the did-you-mean
    index, which owes an answer about every address a human can type — keeps
    the hidden rows in.

    *dedupe_defaults* is the listings' rule: a runnable group's bare row IS
    its default action, so the `x.default` child row would be one action
    wearing two lines — skipped wherever the bare row was just emitted
    (*_covered* carries that fact into the recursion). The did-you-mean
    index leaves it off: `x.default` is an address a human can type, and a
    typo of it deserves the real spelling back.
    """
    for name, task in ordered_tasks(node).items():
        if _covered and name == "default":
            continue  # the caller's bare-group row already says this
        if listed(task, show_hidden=show_hidden):
            yield depth, f"{prefix}{name}", name, task_line(task), "task"
    for name, sub in node["groups"].items():
        if not has_listed(sub, show_hidden=show_hidden):
            continue
        # A runnable group is itself a *runnable address* — the bare
        # `fm <group>` spelling, described by its default action — so it earns
        # a row in the flat listing. A plain group is only a heading.
        runnable = (
            listed(sub, show_hidden=show_hidden)
            and "default" in sub
            and listed(sub["default"], show_hidden=show_hidden)
        )
        yield (
            depth,
            f"{prefix}{name}",
            name,
            default_line(sub) if runnable else sub["help"],
            "runnable-group" if runnable else "group",
        )
        yield from walk(
            sub,
            f"{prefix}{name}.",
            depth + 1,
            show_hidden=show_hidden,
            dedupe_defaults=dedupe_defaults,
            _covered=dedupe_defaults and runnable,
        )


def iter_tasks(
    node: dict[str, Any],
    prefix: str = "",
    *,
    show_hidden: bool = False,
    dedupe_defaults: bool = False,
    covered: bool = False,
) -> Iterator[tuple[str, str]]:
    """`walk()` as the flat listing sees it: `(address, help)` for everything
    you can actually type, headings dropped. *covered* says the caller has
    already shown a row standing in for *node*'s own `default` (group help
    inserts the bare-group row itself)."""
    for _depth, address, _leaf, help_text, kind in walk(
        node,
        prefix,
        show_hidden=show_hidden,
        dedupe_defaults=dedupe_defaults,
        _covered=dedupe_defaults and covered,
    ):
        if kind != "group":
            yield address, help_text


def sort_tree(node: dict[str, Any]) -> dict[str, Any]:
    """A copy of *node* with tasks and groups each in name order, recursively.

    The listing shape survives — tasks still come before groups at every
    level, and a group's `default` still leads it — so `[tool.footman] sort =
    true` changes only where names fall, never the two-band layout."""
    copy = dict(node)
    by_name = {name: node["tasks"][name] for name in sorted(node["tasks"])}
    copy["tasks"] = ordered_tasks({"tasks": by_name})
    copy["groups"] = {
        name: sort_tree(sub) for name, sub in sorted(node["groups"].items())
    }
    return copy


def iter_group_paths(node: dict[str, Any], prefix: str = "") -> Iterator[str]:
    for name, sub in node["groups"].items():
        yield f"{prefix}{name}"
        yield from iter_group_paths(sub, f"{prefix}{name}.")


def json_default(value: object) -> object:
    """JSON forms for the types footman coerces *in* — Path, Enum, datetime,
    UUID, Decimal, dataclasses, sets — so a task may return what it accepts.
    Anything else raises TypeError; the caller turns that into a
    `returned_error` note rather than a broken envelope."""
    if isinstance(value, PurePath):
        return str(value)
    if isinstance(value, enum.Enum):
        # The one output face: the member's token. Int/str-subclassed enums
        # never reach this hook (they ride the encoder's fast paths), which
        # is why the `redact` pre-walk transforms them first — this branch
        # covers the plain-Enum stragglers that arrive through `default`.
        from livery.footman import _coerce

        return _coerce.token_of(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, decimal.Decimal):
        return str(value)  # str, not float: Decimal exists to keep precision
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=repr)  # deterministic order for golden tests
    raise TypeError(f"{type(value).__name__} is not JSON-serialisable")


# --- the output contract ------------------------------------------------------
# Three pure functions over the `returned` spec `_manifest.returned_spec`
# bakes: the JSON Schema renderer (the describe door's interop dialect), the
# producer-side value check, and the phrase help/docs print. The native shape
# never expresses anything JSON Schema cannot say — golden-pair tests hold the
# renderer to that.

_RETURN_SCHEMA: dict[str, dict[str, Any]] = {
    "str": {"type": "string"},
    "int": {"type": "integer"},
    "float": {"type": "number"},
    "bool": {"type": "boolean"},
    "none": {"type": "null"},
    "any": {},
    "path": {"type": "string"},
    "datetime": {"type": "string", "format": "date-time"},
    "date": {"type": "string", "format": "date"},
    "time": {"type": "string", "format": "time"},
    "uuid": {"type": "string", "format": "uuid"},
    "decimal": {"type": "string"},
}


def returns_json_schema(spec: dict[str, Any]) -> dict[str, Any]:
    """Render a baked `returned` spec as JSON Schema (2020-12 vocabulary).

    The describe door's dialect — and a *contract*, not presentation:
    consumer snapshots pin this output, so a change here is envelope-grade
    and belongs in the CHANGELOG, never a cosmetic tweak.
    """
    kind = spec["kind"]
    if kind == "enum":
        schema: dict[str, Any] = {"enum": list(spec["values"])}
    elif kind == "list":
        schema = {"type": "array", "items": returns_json_schema(spec["items"])}
    elif kind == "map":
        schema = {
            "type": "object",
            "additionalProperties": returns_json_schema(spec["values"]),
        }
    elif kind == "object":
        fields: dict[str, Any] | None = spec.get("fields")
        if not fields:
            schema = {"type": "object"}  # no field claims (dict[str, Any])
        else:
            required = [n for n, f in fields.items() if f.get("required", True)]
            schema = {
                "type": "object",
                "properties": {n: returns_json_schema(f) for n, f in fields.items()},
                # A declared shape is the whole claim: a dataclass cannot
                # carry extras, and an undeclared key is exactly the drift
                # the schema exists to catch.
                "additionalProperties": False,
            }
            if required:
                schema["required"] = required
    elif kind == "row":
        items = [returns_json_schema(f) for f in spec["fields"].values()]
        schema = {
            "type": "array",
            "prefixItems": items,
            "minItems": len(items),
            "maxItems": len(items),
        }
    else:
        schema = dict(_RETURN_SCHEMA[kind])
    if "name" in spec and kind in ("object", "row", "enum"):
        schema = {"title": spec["name"], **schema}
    if spec.get("nullable"):
        return {"anyOf": [schema, {"type": "null"}]}
    return schema


def returned_mismatch(
    value: Any, spec: dict[str, Any], path: str = "returned"
) -> str | None:
    """The first place *value* breaks its declared spec, or None.

    The producer-side drift check: validated against the *serialised* shape
    a consumer reads (a Path satisfies "path" because that is a JSON string
    either way), walked recursively, first mismatch wins. A mismatch is a
    loud-but-local note — never an exit-code change."""
    if spec.get("nullable") and value is None:
        return None
    kind = spec["kind"]
    checks: dict[str, tuple[Any, str]] = {
        "str": (str, "text"),
        "bool": (bool, "true/false"),
        "none": (type(None), "null"),
        "path": ((PurePath, str), "a path"),
        "datetime": ((datetime.datetime, str), "a datetime"),
        "time": ((datetime.time, str), "a time"),
        "uuid": ((uuid.UUID, str), "a UUID"),
        "decimal": ((decimal.Decimal, str), "a decimal string"),
    }
    if kind == "any":
        return None
    if kind == "int":
        if isinstance(value, int) and not isinstance(value, bool):
            return None
        return f"{path}: expected an integer, got {_got(value)}"
    if kind == "float":
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return None
        return f"{path}: expected a number, got {_got(value)}"
    if kind == "date":
        # `datetime.datetime` is a `date`; declared date, returned datetime
        # still serialises to an ISO string.
        if isinstance(value, (datetime.date, str)):
            return None
        return f"{path}: expected a date, got {_got(value)}"
    if kind in checks:
        expected, word = checks[kind]
        if isinstance(value, expected):
            return None
        return f"{path}: expected {word}, got {_got(value)}"
    if kind == "enum":
        # The spec speaks tokens, so the member is compared in the same
        # face — validating `.value` against a token spec would refuse
        # every declared member (the wrong-face bug the lockstep exists to
        # prevent). An already-encoded value (a post-walk token string)
        # compares as itself.
        if isinstance(value, enum.Enum):
            from livery.footman import _coerce

            encoded: Any = _coerce.token_of(value)
        else:
            encoded = value
        if any(v == encoded for v in spec["values"]):
            return None
        return f"{path}: {encoded!r} is not one of the declared values"
    if kind == "list":
        if not isinstance(value, (list, tuple, set, frozenset)):
            return f"{path}: expected a list, got {_got(value)}"
        for i, element in enumerate(value):
            if note := returned_mismatch(element, spec["items"], f"{path}[{i}]"):
                return note
        return None
    if kind == "map":
        if not isinstance(value, dict):
            return f"{path}: expected a mapping, got {_got(value)}"
        for key, element in value.items():
            if not isinstance(key, str):
                return f"{path}: key {key!r} is not a string"
            if note := returned_mismatch(element, spec["values"], f"{path}[{key!r}]"):
                return note
        return None
    if kind == "object":
        return _object_mismatch(value, spec, path)
    if kind == "row":
        fields: dict[str, Any] = spec["fields"]
        if not isinstance(value, (list, tuple)):
            return f"{path}: expected {spec['name']} (a row), got {_got(value)}"
        if len(value) != len(fields):
            return (
                f"{path}: expected {len(fields)} values "
                f"({', '.join(fields)}), got {len(value)}"
            )
        for element, (name, fspec) in zip(value, fields.items(), strict=True):
            if note := returned_mismatch(element, fspec, f"{path}.{name}"):
                return note
        return None
    return None  # an unknown kind (a newer manifest) makes no claims here


def _object_mismatch(value: Any, spec: dict[str, Any], path: str) -> str | None:
    fields: dict[str, Any] | None = spec.get("fields")
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        # Read through `asdict`'s eyes without its deep conversion: the
        # instance's own field names and values, one shallow mapping.
        have = {f.name: getattr(value, f.name) for f in dataclasses.fields(value)}
    elif isinstance(value, dict):
        have = value
    else:
        shape = spec.get("name", "an object")
        return f"{path}: expected {shape}, got {_got(value)}"
    if not fields:
        return None  # no field claims to check
    for name, fspec in fields.items():
        if name not in have:
            if fspec.get("required", True):
                return f"{path}: missing key {name!r}"
            continue
        if note := returned_mismatch(have[name], fspec, f"{path}.{name}"):
            return note
    # The declared shape is the whole claim — an undeclared key IS the
    # silent-rename story, seen from the other side.
    if extra := sorted(set(have) - set(fields)):
        return f"{path}: undeclared key {extra[0]!r}"
    return None


def _got(value: Any) -> str:
    return "null" if value is None else type(value).__name__


_RETURN_WORD: dict[str, tuple[str, str]] = {
    # (singular, plural) — the plural serves "a list of …".
    "str": ("text", "text"),
    "int": ("an integer", "integers"),
    "float": ("a number", "numbers"),
    "bool": ("true/false", "true/false"),
    "none": ("null", "nulls"),
    "any": ("anything", "anything"),
    "path": ("a path", "paths"),
    "datetime": ("a datetime", "datetimes"),
    "date": ("a date", "dates"),
    "time": ("a time", "times"),
    "uuid": ("a UUID", "UUIDs"),
    "decimal": ("a decimal", "decimals"),
}


def returns_phrase(spec: dict[str, Any], *, plural: bool = False) -> str:
    """The one-line phrase for a `returned` spec — the returns line in
    `--help` and the type cells on a docs page."""
    kind = spec["kind"]
    if kind == "enum":
        core = "one of " + "|".join(str(v) for v in spec["values"])
    elif kind == "object" and spec.get("fields"):
        name = spec.get("name", "an object")
        core = f"{name} {{{', '.join(spec['fields'])}}}"
    elif kind == "object":
        core = "an object"
    elif kind == "row":
        core = f"{spec['name']} ({', '.join(spec['fields'])})"
    elif kind == "map":
        core = "a mapping of " + returns_phrase(spec["values"], plural=True)
    elif kind == "list":
        core = ("lists of " if plural else "a list of ") + returns_phrase(
            spec["items"], plural=True
        )
    else:
        singular, plural_word = _RETURN_WORD[kind]
        core = plural_word if plural else singular
    if spec.get("nullable"):
        core += " (or null)"
    return core


def redact(value: Any) -> Any:
    """The one redaction walk: any `params.Secret` inside *value* becomes
    `***` on its way to a surface that *shows* it — the `--json` envelope,
    baked manifest defaults, and the command line a step is printed under
    (`context._shown`). A str subclass rides json.dumps' fast path and never
    reaches the `default` hook, so this pre-walk is the only reliable
    interception. Records are walked on the way out, never on the way in:
    what `recording()` and a dependent read is the value that was passed."""
    from livery.footman.params import Secret

    if isinstance(value, Secret):
        return "***"
    if isinstance(value, enum.Enum):
        # The enum wire face, in the same pre-walk and for the same reason
        # as Secret: `IntEnum` and str-valued enums subclass int/str, ride
        # `json.dumps`' fast paths, and never reach the `default` hook —
        # and dict *keys* never consult the hook for any type. The walk is
        # the only reliable interception, values and keys both; a member
        # always speaks its canonical token on a self-describing surface.
        from livery.footman import _coerce

        return _coerce.token_of(value)
    if isinstance(value, dict):
        return {redact(k): redact(v) for k, v in value.items()}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        # The shape a structured return is most likely to arrive in, and the
        # one this walk used to step straight over: `json_default` turns a
        # dataclass into a dict with `asdict`, which deep-copies — so a
        # `Secret` field survived as the str subclass it is, rode `json.dumps`'
        # fast path, and printed in the clear. A dict here is what the encoder
        # would have produced anyway, now with the fields walked first.
        return {
            field.name: redact(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, tuple):
        return tuple(redact(v) for v in value)
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return {redact(v) for v in value}
    return value


def jsonable(value: Any) -> tuple[bool, Any]:
    """(True, encoded) when *value* survives the JSON coercion mirror —
    used to bake parameter defaults into the manifest; (False, None) when it
    doesn't, in which case the key is simply omitted."""
    try:
        return True, json.loads(json.dumps(redact(value), default=json_default))
    except (TypeError, ValueError):
        return False, None


# --- where user code lives ----------------------------------------------------
#
# One answer to "where is this?", for every message that needs it: the refusals
# that name a declaration, the shadow chain in `--help`, and the failure line
# for an exception nobody expected. Kept here, with the rest of the phrasing,
# because three copies of `co_filename:co_firstlineno` is how they drift.

_OURS = str(Path(__file__).resolve().parent)


def source_of(fn: Any) -> str:
    """`file:line` where *fn* is written, or `""` when it cannot be told.

    `unwrap` first: a decorated body should point at what somebody wrote, not
    at the wrapper standing in front of it.
    """
    import inspect

    code = getattr(inspect.unwrap(fn), "__code__", None) if fn is not None else None
    return f"{code.co_filename}:{code.co_firstlineno}" if code is not None else ""


def _is_ours(filename: str) -> bool:
    return str(Path(filename).resolve()).startswith(_OURS)


def user_frame(exc: BaseException) -> str:
    """`file:line in name` for the innermost frame that is the caller's code.

    Innermost *user* frame, not innermost frame: a body that calls `run()` with
    a callable ends its traceback inside footman, and naming that would answer
    a question nobody asked. Empty when every frame is footman's own.
    """
    import traceback

    for frame in reversed(traceback.extract_tb(exc.__traceback__)):
        if not _is_ours(frame.filename):
            return f"{frame.filename}:{frame.lineno} in {frame.name}"
    return ""


def stack_wanted(verbose: bool, is_terminal: bool) -> bool:
    """Whether an unexpected exception should show its whole stack.

    Under `-v`, or whenever the failure is headed somewhere nobody is
    watching: a log, a redirect, CI, cron. There is no second run *there* to
    add `-v` to — the artifact is what you get — and the progress estimate
    already reads the same signal to take its one-shot path.

    Pure, and given both answers rather than finding them, so the one rule can
    live here (with the rest of the phrasing) while its callers sit on either
    side of the import that would otherwise make this circular.
    """
    return verbose or not is_terminal


def user_traceback(exc: BaseException) -> str:
    """The formatted traceback, with footman's own frames taken out.

    Every one of them, not only the leading run. A step's body is reached
    through the pump, so the framework sits in the *middle* of that stack, and
    leaving it there puts `_pump` and `__call__` between two lines the reader
    wrote. Nothing in those frames is ever theirs to fix, and the receipt above
    already says the work was a step.

    The cost is honest to name: two adjacent frames in the result were not
    adjacent in the call. What is bought is that every line printed is one
    somebody can act on.
    """
    import traceback

    kept = [
        frame
        for frame in traceback.extract_tb(exc.__traceback__)
        if not _is_ours(frame.filename)
    ]
    tail = "".join(traceback.format_exception_only(type(exc), exc))
    if not kept:  # nothing but framework: the message is the whole story
        return tail
    body = "".join(traceback.StackSummary.from_list(kept).format())
    return f"Traceback (most recent call last):\n{body}{tail}"


def global_default_suffix(name: str, *, code: bool = False, live: bool = True) -> str:
    """The `; default: …` tail a global's help line carries — one composition
    for `--help` and the docs table, so the two spellings cannot drift.
    Empty when there is nothing to print: no default at all, or a bare
    reading with no spelling of its own. *code* wraps the value in backticks
    for a markdown cell. *live* resolves a computed default on this machine
    — right at a terminal, wrong on a page that outlives it: the docs
    exporter passes False and gets the symbolic phrase instead, so the
    published reference stops baking in the build runner's core count."""
    from livery.footman import _split

    shown, computed = _split.global_default(name, resolved=live)
    if not shown:
        return ""
    if computed and not live:
        # The phrase is prose, never a copyable value: no backticks, and
        # "(computed)" would be redundant under a description of how.
        return f"; default: {shown}"
    value = f"`{shown}`" if code else str(shown)
    return f"; default: {value}{' (computed)' if computed else ''}"
