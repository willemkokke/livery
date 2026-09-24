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
imported by `tools.py` at call time: the extracted facts are recorded,
and the extractor only runs when a reading is taken. The data classes
it fills, [livery.toolroom.store.ToolSpec][] and its parts, live in the
store, which renders them; this module is the reading side.
"""

from __future__ import annotations

from typing import Any

from livery.toolroom.store import Option, ToolSpec, Verb


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
