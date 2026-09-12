"""The execution path: load tasks, refresh the manifest, run the chain.

This is everything that happens for a real `fm ...` invocation (as opposed to
the completion hot path). It imports the user's tasks file — paying that cost is
fine here — resolves the command line against the freshly-built manifest, and
runs the resulting segments, honouring the global options.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any, NamedTuple

from livery.footman import (
    _coerce,
    _config,
    _describe,
    _discover,
    _executor,
    _manifest,
    _notes,
    _paths,
    _progress,
    _schedule,
    _script,
    _signals,
    _split,
    context,
    invocation,
    registry,
)
from livery.footman._executor import EX_USAGE
from livery.footman.app import DEFAULT_BRAND, Brand

# The brand (names + version) in effect for the current invocation. Set at the
# top of `run()`; a CLI is one invocation per process, so a module global is
# the simplest way to reach it from the error/version helpers. The colour
# flags follow the same pattern: one per stream, resolved once from the
# stream's tty-ness, --no-color, NO_COLOR, and TERM.
_brand: Brand = DEFAULT_BRAND
_color_out: bool = False
_docs_url: str | None = None  # the run's docs_url template, config-set


def _builtin() -> tuple[str, ...]:
    """This invocation's built-in set: the brand's, plus the user's own.

    Every consumer of the built-in ladder asks here rather than reading
    `_brand.builtin` — global mode, the base mount, `--plugins`, the
    "it's built in, mount it" remedy — so a user's declarations are built
    in wherever the brand's are, which is the whole of the feature.
    A broken `builtin` key is refused where it can be reported (the
    execution path); the quieter surfaces fall back to the brand's own.
    """
    try:
        return _config.effective_builtin(_brand.builtin)
    except _config.ConfigError:
        return _brand.builtin


def _task_docs_url(address: str) -> str | None:
    """*address*'s documentation URL under this run's template, or None."""
    return _describe.docs_url_for(_docs_url, address)


_color_err: bool = False


_COLOR_MODES = ("auto", "always", "never")


def _resolve_color(g: dict[str, object], *, bound: bool = False) -> str:
    """The run-wide colour mode: `auto` | `always` | `never`.

    Precedence, highest first: an explicit `--color=…` / `--no-color` on the
    command line, then the option's own ladder — the `color` config key, then
    the declared computed default, which reads the ambient protocol
    variables (`NO_COLOR` → never, `FORCE_COLOR` → always), else `auto`.
    *bound* is False for the pre-run colouring of `--version`/errors, where
    no config exists yet and the declared default answers directly; the run
    path re-resolves bound and repaints.
    """
    cli = g.get("color")
    if isinstance(cli, str) and cli in _COLOR_MODES:
        return cli
    if g.get("no_color"):
        return "never"
    if bound:
        return str(_split.COLOR.value)
    return _split._color_from_env()


def _set_colors(mode: str) -> None:
    global _color_out, _color_err
    _color_out = _describe.wants_color(sys.stdout, mode)
    _color_err = _describe.wants_color(sys.stderr, mode)


def _error(message: str) -> None:
    prog = _describe.red(_brand.prog, _color_err)
    sys.stderr.write(f"{prog}: {message}\n")


def _refuse(json_mode: bool, message: str, code: int = EX_USAGE) -> int:
    """Report a refusal on stderr — and when `--json` promised an envelope,
    keep stdout a single JSON document describing the same refusal, so a
    machine consumer never has to parse two formats. Refusals exit
    `EX_USAGE` (64), never a code a task could mean on purpose; the
    non-refusal callers pass 130 for an interrupt and 143/129 for a stop
    signal."""
    _error(message)
    if json_mode:
        envelope = {"schema": 1, "error": {"code": code, "message": message}}
        print(json.dumps({**envelope, "items": []}, indent=2))
    return code


def _wants_json(argv: list[str]) -> bool:
    """Whether the leading globals include `--json`, tolerant of a malformed
    line — the refusal for `fm --json --nope` must still honour the envelope
    `--json` already promised. Mirrors `_parse_globals`' walk, minus raising.
    """
    i = 0
    while i < len(argv) and argv[i].startswith("-") and argv[i] != "--":
        if argv[i].split("=", 1)[0] == "--json":
            return True
        i += 1  # every global is one self-contained token (values `=`-attach)
    return False


def _print_version(json_mode: bool) -> int:
    if json_mode:
        payload = {"schema": 1, "name": _brand.name, "version": _brand.version}
        print(json.dumps(payload, indent=2))
    else:
        print(f"{_brand.name} {_brand.version}")
    return 0


def _globals_to_dict(tokens: list[str]) -> dict[str, object]:
    """Interpret the splitter's canonical global tokens into a flat mapping."""
    result: dict[str, object] = {}
    for tok in tokens:
        name = tok.split("=", 1)[0]
        if not name.startswith("--") and name not in _split._GLOBAL_KIND:
            # A lenient pre-discovery walk carries unknown dash tokens
            # through for the authoritative parse to teach — but stripping
            # the dashes here quietly made the misspelling ACT first:
            # `-version=1` drove `--version` and exited 0, and
            # `-install-completion=zsh` would have edited a shell rc, all
            # before the parse that refuses `-version` ever ran. An unknown
            # spelling populates nothing; the refusal stays the answer.
            continue
        key = name.lstrip("-").replace("-", "_")
        if "=" in tok:  # a value attached by the splitter (--name=value)
            result[key] = tok.split("=", 1)[1]
        elif _split._GLOBAL_KIND.get(name) == "flag":
            result[key] = True
        else:
            # A value-taking global named bare. It carries presence and no
            # value, so it reads as the empty string and the consumer resolves
            # what that means — `--describe` the whole tree, `--color` the
            # ambient decision. It used to read as `True`, which made every
            # consumer test the *type* of the value to learn how it was
            # spelled; that union is what this removes.
            result[key] = ""
    return result


def _uv_wanted(g: dict[str, object], cfg: dict[str, Any]) -> bool:
    """`--uv` / `--no-uv` / the `uv` config key, resolved lexically:
    **CLI > config > on by default**.

    The one config-backed option read before any bind: the uv handoff runs
    first and may replace this process, so `_split.UV`'s declared ladder
    cannot answer yet. Same order, hand-held — the stated early consumer.
    """
    if g.get("uv"):
        return True
    if g.get("no_uv"):
        return False
    value = cfg.get("uv")
    return True if not isinstance(value, bool) else value


def _config_arg(g: dict[str, object]) -> str | None:
    """The --config global as the string it is (or None) — the globals dict
    is object-valued, and this is the one place that narrows it."""
    value = g.get("config")
    return value if isinstance(value, str) else None


class Discovery(NamedTuple):
    """What one discovery walk found — resolved once, shared verbatim by the
    run, the handoff probe, and the completion child, so they cannot
    disagree about what loads."""

    files: list[Path]
    """Everything `load_tree` mounts, outermost rung first: the user tasks
    file when present, then the project cascade root-down. Nearest wins on
    a name, so a project task shadows a same-named user task — the reading
    the cascade always had, extended one rung outward."""
    cfg: dict[str, object]
    root: str
    """The project cascade's top — and `""` outside a project. The user
    rung never claims it: a root is the *project's* top, and footman
    invents none where there is no project ("empty means global mode")."""
    user: Path | None
    """The user tasks file, when it joined `files`."""


def resolve_task_files(
    g: dict[str, object],
    *,
    on_warning: Callable[[str], None] | None = None,
    on_note: Callable[[str], None] | None = None,
    cwd: Path | None = None,
) -> Discovery:
    """The task files, merged config, and project root for the cwd + globals
    — the pure core of `_discover_files`, shared with the completion
    subprocess (`_suggest`) so both discover exactly the same tasks.

    `-f/--tasks-file` loads exactly one file, no cascade and no user rung
    (total control); otherwise the user tasks file leads as the cascade's
    outermost rung, then every `tasks.py` along the walk down to the cwd.
    The walk's reach is the cascade mode (user-level `cascade` key,
    `FOOTMAN_CASCADE` override): the cwd alone (`none`), the repo root
    (`repo`, default), or the whole ancestor path (`filesystem`) — and
    config search follows the same walk, so the two cascades stay one
    concept. Raises `_config.ConfigError` on a bad `--config` or an unknown
    cascade mode (`_config.CascadeError`); an empty `files` means nothing
    matched. The caller owns how either outcome is surfaced.

    *cwd* answers from somewhere other than the process directory: the
    handoff resolves against its `-C` probe *before* the chdir happens, and
    must see exactly what the run will.
    """
    cwd = cwd or Path.cwd()
    mode = _config.cascade_mode(_config_arg(g))
    if mode == "none":
        ceiling = cwd
    elif mode == "filesystem":
        ceiling = Path(cwd.anchor)
    else:
        ceiling = _paths.find_repo_root(cwd)
    cfg = _config.load_config(
        cwd,
        ceiling,
        _config_arg(g),
        on_warning=on_warning,
        on_note=on_note,
    )
    override = g.get("tasks_file")
    if override:
        one = Path(str(override)).expanduser()
        if not one.is_absolute():
            one = cwd / one  # identical to the plain relative read when cwd is the cwd
        files = [one] if one.is_file() else []
        return Discovery(files, cfg, str(files[0].parent) if files else "", None)
    filename = cfg.get("tasks")
    if filename is not None and not isinstance(filename, str):
        # The option-backed keys refuse a wrong TOML type loudly; this
        # config-only key silently fell back to the default instead —
        # `tasks = 123` behaved as if unset, which reads as footman
        # ignoring the setting rather than refusing it.
        raise _config.ConfigError(
            f"config key 'tasks' expects a filename string (got {filename!r})"
        )
    name = filename if isinstance(filename, str) else _brand.tasks_file
    miscased: list[tuple[Path, str]] = []
    files = _paths.task_files(cwd, ceiling, name, miscased)
    if on_warning is not None:
        for directory, actual in miscased:
            # Unconditional: a wrong-case file is worth saying whether or not
            # the walk found a real one elsewhere. The person wrote a tasks
            # file and footman is not loading it — silence is the bug, and
            # the builtin base answering in its place is exactly when the
            # silence is most convincing.
            on_warning(
                f"{directory / actual} is not {name} — the name is "
                f"case-sensitive, so this file is not being loaded. "
                f"Rename it to {name}."
            )
    root = str(files[0].parent) if files else ""
    if _config_arg(g):
        # An explicit --config is total control, the user rung included:
        # the user named exactly what applies.
        user_name = name
    else:
        # The user rung's own name comes from the user's own writing — the
        # user-level file — never from a project's `tasks` key, which
        # renames the *project's* file and stops there. Steered by the
        # project, the walk looked for a personal file the user never
        # wrote, and the personal rung silently vanished under any project
        # that renames its tasks file.
        setting = _config.user_level_value("tasks")
        user_name = setting if isinstance(setting, str) else _brand.tasks_file
    user = _paths.user_tasks_file(user_name)
    if user.is_file():
        # The cascade's outermost rung: personal tasks ride everywhere, and
        # anything nearer shadows them — project > user, the nearest-wins
        # reading the cascade already has, extended one rung outward. A
        # project that wants a personal task's name owns it; `inherited()`
        # still reaches what it shadowed.
        return Discovery([user, *files], cfg, root, user)
    return Discovery(files, cfg, root, None)


def _base_tree(names: tuple[str, ...], json_mode: bool) -> registry.Group | int:
    """The built-in base: each `footman.tasks` entry point mounted into a
    fresh tree, exactly as a tasks file's `plugin(...)` would mount it — so
    a project that wants the same set mounts it the ordinary way.

    Built only when discovery found no project task files. A name that does
    not mount is a warning naming *whoever declared it*, and the base
    carries on with what mounted: the brand's own families are installed
    by the workspace's sync, and refusing here would refuse the sync that
    installs them; a discovered built-in is a record footman can rebuild.
    The one refusal left is the user's config naming a plugin that does
    not mount, since that is their spelling or install to fix."""
    from livery.footman import compose

    declared_by_user = set(_config.user_builtin())
    with registry.capture() as base:
        for name in names:
            try:
                compose.plugin(name)
            except Exception as exc:
                if name in declared_by_user and name not in _brand.builtin:
                    return _refuse(
                        json_mode,
                        f"builtins.user in {_paths.footman_config_file()} "
                        f"names {name!r}, which did not mount: {exc}",
                    )
                if name not in _brand.builtin:
                    # Discovery put it there, and discovery can be wrong
                    # about a package that has since been removed by hand.
                    # A note and carry on, never a refusal: this list is a
                    # record footman writes and can always rebuild, which
                    # is already why a missing or malformed one means
                    # "nothing discovered" instead of an error. An entry
                    # that no longer resolves is the same thing one entry
                    # at a time. Refusing also made the remedy the message
                    # names unreachable — `self.add` mounts this same base
                    # on its way in, so the way out refused too.
                    _error(
                        f"a discovered built-in, {name!r}, did not mount: "
                        f"{exc} — skipping it; run {_brand.prog} self.add to "
                        f"bring the discovered list back in step with what "
                        f"is installed"
                    )
                    continue
                # The brand's own family, not installed in this venv yet:
                # a checkout whose source declares a family its venv has
                # not synced. The verbs that mounted still run, the sync
                # among them, so the remedy stays reachable.
                _error(
                    f"{_brand.name} declares built-in tasks from {name!r}, "
                    f"which did not mount: {exc} — carrying on without them; "
                    f"install the package that provides it (a workspace's "
                    f"`{_brand.prog} sync` does)"
                )
                continue
    # A built-in was defined by no tasks file, so it must carry no folder —
    # and the stamp lives on the function, which is the same object every
    # time it is mounted. Without this, an earlier in-process invocation that
    # mounted the same provider from inside a project leaves that project's
    # directory on it, and nothing here overwrites it: the base exists only
    # when discovery found no task files, so no overlay ever runs.
    _discover.untag(base)
    # Before the cascade overlays the user's own tasks into this group: after
    # that, nothing tells the brand's tasks from the person's, and the two
    # have opposite defaults.
    registry.seal_expose(base)
    return base


def _discover_files(
    g: dict[str, object], wants_help: bool, bare: bool
) -> Discovery | int:
    """Resolve the task files to load and the merged config for this cwd.

    `-f/--tasks-file` is the escape hatch: it loads exactly one file, no
    cascade. Otherwise the user rung leads and footman collects every
    `tasks.py` from the repo root (the `.git` ceiling) down to the cwd.
    Returns the `Discovery` or, when nothing was found, the exit code to
    return (0 for a listing, 2 otherwise).
    """
    try:
        found = resolve_task_files(
            g,
            on_warning=_error,
            on_note=_error if g.get("verbose") else None,
        )
    except _config.CascadeError as exc:
        # Self-describing (names FOOTMAN_CASCADE / the `cascade` key): no
        # `--config:` prefix, which would misattribute an env-var mistake.
        return _refuse(bool(g.get("json")), str(exc))
    except _config.ConfigError as exc:
        return _refuse(bool(g.get("json")), f"--config: {exc}")

    if found.files:
        return found

    if _builtin() and not g.get("tasks_file"):
        # Global mode: the brand's built-ins answer where nothing else does,
        # so listings, help, and runs proceed over the mounted base. The
        # empty-tree teaching below is for a runner with no base at all.
        # (`-f` names a file that wasn't there — total control includes the
        # miss, so it keeps today's teaching rather than a surprise base.)
        return found

    cfg = found.cfg
    looked = g.get("tasks_file") or cfg.get("tasks") or _brand.tasks_file
    # Name the file exactly, and say the name is case-sensitive. That is
    # the whole of what a `Tasks.py` owner needs and it costs nothing to
    # say always: on a filesystem that folds case the walk already
    # complained by name (it had the listing in hand), and on one that does
    # not, this is the only affordable way to teach the rule — finding the
    # variant there would mean listing every level of the chain.
    looked_at = f"{looked} (exactly — the name is case-sensitive)"
    if wants_help:
        # A stuck newcomer asking for help should see the globals (-f/-C are the
        # way out) — not a bare one-liner. Global help over an empty tree, then
        # the "where did I look" note.
        _print_global_help(_manifest.build_manifest(registry.Group("root"))["tree"])
        print(f"\n(no tasks file found — looked for {looked_at})")
        return 0
    if bare or g.get("list") or g.get("tree"):
        # A bare `fm` (like `--list`) is a warm empty state, not a hard error.
        if g.get("json"):  # the catalog envelope, honestly empty
            tree = _manifest.build_manifest(registry.Group("root"))["tree"]
            print(json.dumps({"schema": 1, "tree": tree}, indent=2))
        else:
            print(f"No tasks file found (looked for {looked_at}).")
        return 0
    return _refuse(
        bool(g.get("json")),
        f"no tasks file found (looked for {looked_at}); "
        f"create one or pass -f/--tasks-file.",
    )


# --- rendering ---------------------------------------------------------------


def _print_footer() -> None:
    footer = f"Run `{_brand.prog} --help <task>` for a task's options."
    print(f"\n{_describe.dim(footer, _color_out)}")


def _styled_name(name: str) -> str:
    """A task address for a listing: dim group prefix, bold leaf — and a
    terminal hyperlink to its docs page when the project configured a
    `docs_url` template (zero width, so the band math never notices)."""
    prefix, _, leaf = name.rpartition(".")
    lead = _describe.dim(f"{prefix}.", _color_out) if prefix else ""
    styled = f"{lead}{_describe.bold(leaf, _color_out)}"
    return _describe.link(styled, _task_docs_url(name), _color_out)


def _styled_help(help_text: str) -> str:
    """A help line for a listing: trailing status notes dimmed."""
    for marker in ("(runs until", "(unavailable:"):
        head, sep, note = help_text.partition(marker)
        if sep:
            return f"{head}{_describe.dim(f'{marker}{note}', _color_out)}"
    return help_text


def _print_two_band(rows: list[tuple[str, int, str]]) -> None:
    """The one two-band layout every listing draws: painted name cells on
    the left, descriptions aligned into a single wrapped column on the
    right. Rows are `(cell, cell's plain width, help)` — `--list`, `--tree`
    and group help differ only in how they paint the cell.

    Long descriptions wrap with a hanging indent to the description
    column — the terminal's own wrap would drop continuations to column 0,
    shearing the two-band layout apart.
    """
    import textwrap

    width = max(plain for _, plain, _ in rows)
    desc_col = width + 2
    import shutil  # ~1.7 ms of archive codecs for one terminal width

    avail = max(24, shutil.get_terminal_size().columns - desc_col)
    for cell, plain, help_text in rows:
        if not help_text:
            print(cell.rstrip())
            continue
        pieces = textwrap.wrap(help_text, avail) or [""]
        pad = " " * (width - plain)
        print(f"{cell}{pad}  {_styled_help(pieces[0])}".rstrip())
        for cont in pieces[1:]:
            print(f"{' ' * desc_col}{_styled_help(cont)}".rstrip())


def _address_band(rows: list[tuple[str, str]]) -> list[tuple[str, int, str]]:
    """Two-band rows for a flat address listing (`--list`, group help)."""
    return [
        (f"  {_styled_name(name)}", 2 + _describe.display_width(name), help_text)
        for name, help_text in rows
    ]


def _sifted(node: dict[str, Any], want_global: bool) -> dict[str, Any] | None:
    """*node* keeping only tasks from one side of the cascade, or `None`.

    `None` when nothing on that side survives, so an empty section is not
    a heading over blank space. Groups are kept only for what they still
    hold: a group can straddle the split — a personal `docs.notes` beside
    a project's `docs.build` — so the same group name can head a branch in
    both sections, each carrying its own children.
    """
    tasks = {
        name: t
        for name, t in node.get("tasks", {}).items()
        if bool(t.get("global")) is want_global
    }
    groups = {}
    for name, sub in node.get("groups", {}).items():
        if (kept := _sifted(sub, want_global)) is not None:
            groups[name] = kept
    if not tasks and not groups:
        return None
    sifted = {**node, "tasks": tasks, "groups": groups}
    # A group's default is one of its tasks, so it goes when that task does.
    if "default" in sifted and "default" not in tasks:
        sifted.pop("default", None)
        sifted.pop("default_fanout", None)
    return sifted


def _sections(tree: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """The listing's sections, project first, each omitted when empty.

    Project first because it is what the person came for: their own tasks,
    in the directory they are standing in. The global rung — built-ins and
    the personal tasks file — rides everywhere and is the backdrop.
    Outside a project there is only one section, and outside a project
    with no built-ins there are none, which the callers report as before.
    """
    found = [
        (heading, kept)
        for heading, want_global in (("Tasks:", False), ("Global tasks:", True))
        if (kept := _sifted(tree, want_global)) is not None
    ]
    # One section needs no label saying which of two it is.
    if len(found) == 1 and found[0][0] == "Global tasks:":
        return [("Tasks:", found[0][1])]
    return found


def _print_list(tree: dict[str, Any], show_hidden: bool = False) -> None:
    sections = [
        (heading, rows)
        for heading, node in _sections(tree)
        if (
            rows := list(
                _describe.iter_tasks(
                    node, show_hidden=show_hidden, dedupe_defaults=True
                )
            )
        )
    ]
    if not sections:
        print("No tasks defined.")
        return
    for i, (heading, rows) in enumerate(sections):
        if i:
            print()
        print(_describe.bold(heading, _color_out))
        _print_two_band(_address_band(rows))


def _print_tree(tree: dict[str, Any], show_hidden: bool = False) -> None:
    sections = [
        (heading, node)
        for heading, node in _sections(tree)
        if list(_describe.walk(node, show_hidden=show_hidden, dedupe_defaults=True))
    ]
    if not sections:
        # Mirror _print_list rather than printing zero bytes and exiting 0.
        print("No tasks defined.")
        return
    for i, (heading, node) in enumerate(sections):
        if i:
            print()
        print(_describe.bold(heading, _color_out))
        _print_branch(node, show_hidden)


def _print_branch(node: dict[str, Any], show_hidden: bool = False) -> None:
    rows = list(_describe.walk(node, show_hidden=show_hidden, dedupe_defaults=True))
    last = _last_of_each_branch(rows)
    # Leaf names under a drawn branch, not repeated dotted addresses:
    # `--list` is the flat, copy-paste view, and a `--tree` that only
    # indented the same addresses was that listing with worse alignment.
    # Top level carries no connector: those names *are* the root, and a
    # branch drawn from nothing reads as a stray glyph.
    leads: list[str] = []  # per row: stem + joint, plain
    trunk: list[bool] = []  # per ancestor depth: was it its branch's last child?
    for i, (depth, _address, _leaf, _help_text, _kind) in enumerate(rows):
        del trunk[depth:]
        trunk.append(last[i])
        stem = "".join("   " if up else "│  " for up in trunk[1:-1])
        leads.append(stem + ("" if depth == 0 else ("└─ " if last[i] else "├─ ")))
    band: list[tuple[str, int, str]] = []
    for i, (_depth, address, leaf, help_text, kind) in enumerate(rows):
        name = (
            _describe.bold(leaf, _color_out)
            if kind == "task"
            else _describe.bold_cyan(f"{leaf}.", _color_out)
        )
        # Groups link too: their docs pages exist under the same scheme
        # (a directory index, or the group heading's anchor).
        name = _describe.link(name, _task_docs_url(address), _color_out)
        cell = f"{_describe.dim(leads[i], _color_out)}{name}"
        plain = len(leads[i]) + len(leaf) + (kind != "task")  # groups carry a dot
        band.append((cell, plain, help_text))
    _print_two_band(band)


def _last_of_each_branch(rows: Sequence[tuple[Any, ...]]) -> list[bool]:
    """Which rows end their own branch — the `└─` corners.

    A row is last when no later row sits at its depth before the walk climbs
    back out above it."""
    flags = [True] * len(rows)
    for i, row in enumerate(rows):
        for later in rows[i + 1 :]:
            if later[0] < row[0]:
                break
            if later[0] == row[0]:
                flags[i] = False
                break
    return flags


def _print_option_rows(rows: list[tuple[str, str, str]], heading: str) -> None:
    """One aligned option listing, wrapped to the terminal: labels bold on
    the left, details in a hanging-indent column on the right — the
    author's words bright, the mechanics dimmed, and the mechanics moving
    to their own wrapped lines when the pair cannot share one. The globals
    table and every parameter listing draw through here, so no help
    surface leaves the terminal to hard-wrap a column apart.
    """
    if not rows:
        return
    import shutil
    import textwrap

    on = _color_out
    width = max(_describe.display_width(label) for label, _, _ in rows)
    desc_col = width + 4  # two leading spaces + two separating
    avail = max(24, shutil.get_terminal_size().columns - desc_col)
    print(f"\n{_describe.bold(f'{heading}:', on)}")
    for label, doc, mech in rows:
        joined = "; ".join(bit for bit in (doc, mech) if bit)
        if _describe.display_width(joined) <= avail:
            dimmed = _describe.dim(mech, on) if mech else ""
            pieces = ["; ".join(bit for bit in (doc, dimmed) if bit)]
        else:
            pieces = textwrap.wrap(doc, avail) if doc else []
            pieces += [
                _describe.dim(cont, on)
                for cont in (textwrap.wrap(mech, avail) if mech else [])
            ]
            pieces = pieces or [""]
        pad = " " * (width - _describe.display_width(label))
        print(f"  {_describe.bold(label, on)}{pad}  {pieces[0]}".rstrip())
        for cont in pieces[1:]:
            print(f"{' ' * desc_col}{cont}".rstrip())


def _print_param_rows(params: list[dict[str, Any]], heading: str) -> None:
    """One two-band parameter listing — task help and group help draw the
    same rows (author's words bright, mechanics dimmed beneath), and used
    to carry the loop twice."""
    if not params:
        return
    rows = []
    for p in params:
        doc, mech = _describe.param_detail_parts(p)
        rows.append((_describe.param_label(p), doc, mech))
    _print_option_rows(rows, heading)


def _choices_resolver(reg: registry.Group) -> _split.ChoicesFor:
    """Answer a dynamic parameter's choices live, from the registry.

    The completers no longer run when the manifest is built — nothing needed
    them there — so the surfaces that *do* need values ask for them one
    parameter at a time: the splitter for the value it is validating, help
    for the options it is about to print. Memoised per invocation, so a
    parameter asked twice runs its completer once, and a completer nobody's
    line touches never runs at all.
    """
    from livery.footman import _coerce

    seen: dict[tuple[str, str], list[str] | None] = {}

    def resolve(address: str, param: str) -> list[str] | None:
        key = (address, param)
        if key in seen:
            return seen[key]
        found: list[str] | None = None
        try:
            fn = _executor.resolve(reg, address.split("."))
        except (KeyError, IndexError):
            fn = None
        if fn is not None:
            for declared in _manifest.resolved_signature(fn).parameters.values():
                if registry.cli_name(declared.name) != param:
                    continue
                completer = _coerce.peel(declared.annotation).completer
                if completer is not None:
                    found = _manifest._run_completer(completer, {})
                break
        seen[key] = found
        return found

    return resolve


def _print_task_help(
    tree: dict[str, Any],
    path: list[str],
    show_hidden: bool = False,
    choices_for: _split.ChoicesFor | None = None,
) -> None:
    # All phrasing (labels, details, examples) lives in `_describe`, shared
    # with the markdown exporter so help text and pages can never drift.
    node = tree
    for name in path[:-1]:
        node = node["groups"][name]
    task = node["tasks"][path[-1]]
    on = _color_out
    # Resolve before anything renders — the synopsis shows value shapes too.
    # Only what this page prints: doing it here, after the listing filter,
    # is what keeps `fm --help build` from running another task's completer.
    shown = _describe.listed_params(task, show_hidden=show_hidden)
    for spec in shown:
        _split.live_choices(".".join(path), spec, choices_for)
    usage = _describe.paint_cli(
        _describe.usage_parts(_brand.prog, path, task, show_hidden=show_hidden), on
    )
    print(f"usage: {usage}")
    if task["help"]:
        print(f"\n  {task['help']}")
    if task.get("long"):  # the docstring's body, structure preserved
        body = "\n".join(f"  {ln}".rstrip() for ln in task["long"].splitlines())
        print(f"\n{body}")
    if task.get("infinite"):
        print(_describe.dim("\n  runs until you stop it — Ctrl-C", on))
    if task.get("disabled"):
        print(_describe.dim(f"\n  unavailable here: {task['disabled']}", on))
    positionals = [p for p in shown if p["kind"] in ("positional", "variadic")]
    options = [p for p in shown if p["kind"] in ("flag", "option")]
    _print_param_rows(positionals, "positionals")
    _print_param_rows(options, "options")
    returned = task.get("returned")
    returned_doc = task.get("returned_doc", "")
    if returned is not None or returned_doc:
        # The output contract: the author's words bright, the shape dimmed
        # beneath them — the same split every param row makes.
        mech = _describe.returns_phrase(returned) if returned is not None else ""
        detail = "  ".join(
            bit
            for bit in (returned_doc, _describe.dim(mech, on) if mech else "")
            if bit
        )
        print(f"\n{_describe.bold('returns:', on)} {detail}")
    if uses := _describe.uses_line(task, tree):
        # The globals this task declared (`uses=`): mechanics, so dimmed.
        print(_describe.dim(f"\n  {uses}", on))
    example = _describe.paint_cli(_describe.example_parts(path, task, _brand.prog), on)
    print(f"\n{_describe.dim('Example:', on)} {example}")
    if (shadows := task.get("shadows")) is not None:
        # This task overrides one further up the cascade — show the call
        # `inherited()` makes, so the forwarding line can be read off it.
        where = shadows.get("where") or "the cascade"
        print(_describe.dim(f"\nshadows {where} — inherited() calls it", on))
        usage = _describe.paint_cli(
            _describe.usage_parts(_brand.prog, path, shadows, show_hidden=show_hidden),
            on,
        )
        print(f"  {usage}")
    _print_docs_line(".".join(path), on)


def _print_docs_line(address: str, on: bool) -> None:
    """The help pages' pointer at the task's own docs page — the URL as
    visible text (a terminal without hyperlinks still shows something to
    copy), hyperlinked where the terminal dresses up. Nothing without a
    configured `docs_url`."""
    if (url := _task_docs_url(address)) is not None:
        print(f"\n{_describe.dim('docs:', on)} {_describe.link(url, url, on)}")


def _print_group_help(
    tree: dict[str, Any],
    path: list[str],
    show_hidden: bool = False,
    choices_for: _split.ChoicesFor | None = None,
) -> None:
    node = tree
    for name in path:
        node = node["groups"][name]
    on = _color_out
    default = node.get("default")
    dotted = ".".join(path)
    # A runnable group (one with `@group.default`) can run bare — its default —
    # so the task suffix becomes optional. The address stays one dotted token.
    head = f"{dotted}[.<task>]" if default else f"{dotted}.<task>"
    parts = [("prog", _brand.prog), ("group", head), ("opt", "[options]")]
    print(f"usage: {_describe.paint_cli(parts, on)}")
    if node["help"]:
        print(f"\n  {node['help']}")
    if default:
        print(_describe.dim("\n  runs its default when no task is named", on))
    # `covered`: the bare-group row inserted below stands in for `default`.
    rows = list(
        _describe.iter_tasks(
            node,
            f"{dotted}.",
            show_hidden=show_hidden,
            dedupe_defaults=True,
            covered=bool(default),
        )
    )
    if default:
        # The bare-group spelling is itself a runnable, listed address —
        # described by its default action (docstring, or generated).
        rows.insert(0, (dotted, _describe.default_line(node)))
    if rows:
        print(f"\n{_describe.bold('tasks:', on)}")
        _print_two_band(_address_band(rows))
    params = (
        _describe.listed_params(default, show_hidden=show_hidden) if default else []
    )
    for spec in params:  # the default's own options, and only those
        _split.live_choices(f"{dotted}.default", spec, choices_for)
    options = [p for p in params if p["kind"] in ("flag", "option")]
    _print_param_rows(options, "options")
    _print_docs_line(dotted, on)


def _print_global_help(tree: dict[str, Any], show_hidden: bool = False) -> None:
    prog = _brand.prog
    parts = [
        ("prog", prog),
        ("opt", "[globals]"),
        ("req", "<task>"),
        ("opt", "[options]"),
        ("opt", "[<task> ...]"),
    ]
    print(f"usage: {_describe.paint_cli(parts, _color_out)}")
    rows = []
    for name, alias, _kind, hint, _default, help_text in _split.GLOBALS:
        label = f"{alias}, {name}" if alias else f"    {name}"
        if hint:
            label += f"={hint}"  # values are always `=`-attached
        # `.replace` (not `.format`) so a help string containing braces can
        # never crash help output.
        detail = help_text.replace("{prog}", prog)
        # The same thing task parameters gained: say what you get when you say
        # nothing. Computed defaults resolve here, so `--jobs` reports this
        # machine's width rather than a number from the author's. One
        # composition with the docs table (`_describe.global_default_suffix`).
        detail += _describe.global_default_suffix(name)
        rows.append((label, detail, ""))
    _print_option_rows(rows, "globals (before the first task)")
    # Mounted plugin globals ride the same pre-task position, so a help page
    # claiming to list the globals must list these too — with provenance,
    # since "where did --profile come from" is the first question a reader
    # who didn't mount the plugin asks.
    prows = []
    for p in tree.get("globals") or ():
        doc, mech = _describe.param_detail_parts(p)
        doc = doc or p.get("help", "")  # a global's words ride as `help`
        if owner := p.get("owner"):
            mech = f"{mech}; from {owner}" if mech else f"from {owner}"
        prows.append((_describe.param_label(p), doc, mech))
    _print_option_rows(prows, "plugin globals (before the first task)")
    print()
    _print_list(tree, show_hidden)
    _print_footer()


def _wants_help(argv: list[str]) -> bool:
    """`-h`/`--help` anywhere before `--` turns the whole line into a help
    request — asking for help must never execute anything, wherever it lands
    on the line. After `--` it belongs to the passthrough."""
    for tok in argv:
        if tok == "--":
            return False
        if tok in ("-h", "--help"):
            return True
    return False


def _resolve_lenient(tree: dict[str, Any], token: str) -> tuple[str, list[str]] | None:
    """Walk one dotted address to `("task"|"group", path)`, or None.

    The help surface's resolver: never raises — a token that isn't an address
    is someone's argument value or a typo, and the caller decides which.
    """
    parts = token.split(".")
    if "" in parts:
        return None
    node, path = tree, []
    for pos, part in enumerate(parts):
        last = pos == len(parts) - 1
        if part in node["groups"]:
            node = node["groups"][part]
            path.append(part)
            if last:
                return ("group", path)
        elif part in node["tasks"] and last:
            return ("task", [*path, part])
        else:
            return None
    return None


def _help_targets(
    tree: dict[str, Any], argv: list[str], after: int
) -> tuple[list[tuple[str, list[str]]], list[str]]:
    """Group/task addresses mentioned on a `--help` line, resolved leniently —
    plus the bare words that resolved to nothing, so the caller can refuse a
    `--help typo` instead of shrugging.

    The real splitter enforces arity — `--help add` must work even though
    `add` alone would be "missing required positional(s)" — so this resolves
    dotted addresses only and skips every other token (option-shaped tokens
    and, once a target is found, its argument values).
    """
    targets: list[tuple[str, list[str]]] = []
    strays: list[str] = []
    for tok in argv[after:]:
        if tok == "--":
            break
        if tok.startswith("-"):
            continue
        resolved = _resolve_lenient(tree, tok)
        if resolved is not None:
            targets.append(resolved)
        else:
            strays.append(tok)
    return targets, strays


def _print_help(
    tree: dict[str, Any],
    argv: list[str],
    after: int,
    show_hidden: bool = False,
    choices_for: _split.ChoicesFor | None = None,
) -> int:
    """`--help` alone covers fm itself; with names, the named groups/tasks.

    A name that matches nothing is a refusal (exit EX_USAGE) with a suggestion —
    silently degrading to the global listing looked like an answer while
    teaching nothing. With at least one real target found, extra bare words
    stay lenient: they are argument values, not typos.
    """
    targets, strays = _help_targets(tree, argv, after)
    if not targets:
        if strays:
            # Every address a human can type, hidden included: the index
            # answers a typo, and a machine-facing task typo'd by hand
            # deserves the same "did you mean" as any other.
            known = [name for name, _ in _describe.iter_tasks(tree, show_hidden=True)]
            known += _describe.iter_group_paths(tree)
            # Help's *success* output is the one human-only surface; a refusal
            # still honours the envelope `--json` promised.
            return _refuse(
                _wants_json(argv),
                f"--help: unknown task or group {strays[0]!r}"
                f"{_split._did_you_mean(strays[0], known)}",
            )
        _print_global_help(tree, show_hidden)
        return 0
    for index, (kind, path) in enumerate(targets):
        if index:
            print()
        if kind == "task":
            _print_task_help(tree, path, show_hidden, choices_for)
        else:
            _print_group_help(tree, path, show_hidden, choices_for)
    return 0


def _plugins_report(reg: registry.Group) -> int:
    """`--plugins`: installed `footman.tasks` entry points, mounted or not,
    grouped by the distribution that ships them.

    "Installed but nobody mounted it" becomes visible. The entry-point
    record itself cannot carry a description (the packaging spec is strictly
    `name = "module:attr"`), and a distribution's Summary describes the
    *package* — so it prints once, on the package's header line, instead of
    repeating identically beside every entry the package ships. An entry
    then describes itself only where footman genuinely knows it: a mounted
    entry from its landed tree, a declared built-in from its advertised tree
    (the brand vouches for importing its own declarations). A plain
    unmounted entry shows its state alone — importing unmounted third-party
    code could crash a listing, and a repeated package summary taught
    nothing.
    """
    from importlib.metadata import entry_points

    from livery.footman import compose

    eps = sorted(entry_points(group=compose.ENTRY_POINT_GROUP), key=lambda e: e.name)
    if not eps:
        print("No footman.tasks plugins installed.")
        return 0

    landed: dict[str, list[str]] = {}

    def walk(node: registry.Group, prefix: str, inside: bool) -> None:
        # Report the top-most mounted node per identity — its whole subtree
        # shares the provenance, and the top is the copy-paste address.
        for name, fn in node.tasks.items():
            ident = registry.mounted_from(fn)
            if ident is not None and not inside:
                landed.setdefault(ident, []).append(f"{prefix}{name}")
        for name, sub in node.groups.items():
            ident = sub.mounted_from
            if ident is not None and not inside:
                landed.setdefault(ident, []).append(f"{prefix}{name}")
            walk(sub, f"{prefix}{name}.", inside or ident is not None)

    walk(reg, "", False)

    # A plugin with no tasks still mounts — its hooks and options ride every
    # run — and the tree walk cannot see it. Its contributions carry the
    # identity the mount stamped.
    riding: set[str] = set()
    for kind, bucket in reg.contributions.items():
        for item in bucket:
            ident = (
                item._mounted
                if kind == "globals"
                else getattr(item, registry._MOUNTED, None)
            )
            if isinstance(ident, str) and ident != registry._MANY_MOUNTS:
                riding.add(ident)

    def described(addresses: list[str]) -> str:
        node: object = reg
        for part in addresses[0].split("."):
            node = (
                node.groups.get(part, node.tasks.get(part))
                if isinstance(node, registry.Group)
                else None
            )
        if isinstance(node, registry.Group):
            return node.help
        doc = (getattr(node, "__doc__", "") or "").strip()
        return doc.splitlines()[0] if doc else ""

    def advertised(entry: str) -> str:
        """A declared built-in's own line, from the tree it advertises —
        resolved on demand, exactly as the unknown-task remedy resolves it.
        Empty on any failure: the report never crashes."""
        try:
            _ident, node = compose._resolve_plugin(entry)
        except Exception:
            return ""
        if isinstance(node, registry.Group):
            if not node.groups and len(node.tasks) == 1:
                # A single-task plugin: the task's own line beats the
                # module docstring an anonymous capture carries as help.
                (fn,) = node.tasks.values()
                doc = (getattr(fn, "__doc__", "") or "").strip()
                if doc:
                    return doc.splitlines()[0]
            return node.help or ""
        doc = (getattr(node, "__doc__", "") or "").strip()
        return doc.splitlines()[0] if doc else ""

    def mount_points(where: list[str]) -> str:
        tops = sorted(where)
        if len(tops) > 3:
            return f"mounted at {', '.join(tops[:3])} (+{len(tops) - 3} more)"
        return f"mounted at {', '.join(tops)}"

    dists: dict[str, tuple[str, str]] = {}  # name -> (version, summary)
    grouped: dict[str, list[tuple[str, str, str]]] = {}
    for ep in eps:
        meta = getattr(ep.dist, "metadata", None)
        dist_name = (meta.get("Name", "") if meta else "") or "(unknown)"
        dists.setdefault(
            dist_name,
            (
                getattr(ep.dist, "version", "") or "",
                (meta.get("Summary", "") if meta else "") or "",
            ),
        )
        where = landed.get(ep.name)
        if ep.name in _builtin():
            # Built into this runner: part of the product wherever there is
            # no project, and an ordinary mount inside one. Which *rung*
            # declared it matters to the reader — the brand's own set is the
            # product, the user's is their machine's, and only the second is
            # theirs to edit.
            desc = described(where) if where else advertised(ep.name)
            state = (
                "built in" if ep.name in _brand.builtin else "built in (your config)"
            )
            grouped.setdefault(dist_name, []).append((ep.name, state, desc))
        elif where:
            # One landed node speaks for itself; a family mounted piecemeal
            # speaks with its advertised voice — an arbitrary member's
            # docstring must not stand for seven siblings.
            desc = described(where) if len(where) == 1 else advertised(ep.name)
            grouped.setdefault(dist_name, []).append(
                (ep.name, mount_points(where), desc)
            )
        elif ep.name in riding:
            # Mounted with no tasks to land: hooks and options riding every
            # run have no address to name, so the state is the plain word.
            grouped.setdefault(dist_name, []).append(
                (ep.name, "mounted", advertised(ep.name))
            )
        else:
            grouped.setdefault(dist_name, []).append((ep.name, "(not mounted)", ""))
    every = [row for rows in grouped.values() for row in rows]
    name_w = max(_describe.display_width(name) for name, _, _ in every)
    state_w = max(_describe.display_width(state) for _, state, _ in every)
    on = _color_out
    for dist_name in sorted(grouped):
        version, summary = dists[dist_name]
        header = f"{_describe.bold(dist_name, on)} {version}".rstrip()
        if summary:
            header += f"  {_describe.dim(f'— {summary}', on)}"
        print(header)
        for name, state, desc in grouped[dist_name]:
            line = (
                f"  {_describe.pad_to(name, name_w)}"
                f"  {_describe.pad_to(state, state_w)}"
            )
            if desc:
                line += f"  {_describe.dim(f'— {desc}', on)}"
            print(line.rstrip())
    unmounted = [name for name, state, _ in every if state == "(not mounted)"]
    if unmounted:
        # Installed is not asked-for: mounts are authored in the tasks file,
        # and a listing that stops at "(not mounted)" leaves the reader with
        # a state and no verb. One line names the move.
        print(
            _describe.dim(
                f"not mounted = installed but not asked for — mount one from "
                f'your tasks file: plugin("{unmounted[0]}")',
                on,
            )
        )
    return 0


def _where(root: registry.Group, tree: dict[str, Any], dotted: str) -> int:
    # Strict, like every address surface: `docs..serve` or a trailing dot is
    # an error, never silently normalised away.
    path = dotted.split(".")
    try:
        if "" in path:
            raise KeyError(dotted)
        fn = _executor.resolve(root, path)
    except (KeyError, IndexError):
        names = _split.flat_addresses(tree)
        _error(f"--where: unknown task {dotted!r}{_split._did_you_mean(dotted, names)}")
        return EX_USAGE
    chain = _discover.shadow_chain(fn)
    lines = []
    for index, member in enumerate(chain):
        code = getattr(member, "__code__", None)
        if code is None:
            continue
        where = f"{code.co_filename}:{code.co_firstlineno}"
        # The winner first; anything it shadows follows, marked — so
        # "am I overriding something, and where is it?" is one command.
        lines.append(where if index == 0 else f"{where}   (shadowed)")
    if not lines:
        _error(f"--where: cannot locate source for {dotted!r}")
        return EX_USAGE
    print("\n".join(lines))
    return 0


def _iter_task_nodes(
    node: dict[str, Any], prefix: str
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Every task node under *node* with its dotted address — a group's
    `default` included, because it *is* the child task named `default`."""
    for name, task in node["tasks"].items():
        yield prefix + name, task
    for name, sub in node["groups"].items():
        yield from _iter_task_nodes(sub, f"{prefix}{name}.")


def _contract_entry(address: str, task: dict[str, Any]) -> dict[str, Any]:
    """One task's `--describe` entry: the input surface (the param specs,
    volatile completer choices dropped) and the output contract (the declared
    schema rendered as JSON Schema, plus the docstring's `Returns:` prose)."""
    params = []
    for p in task["params"]:
        spec = dict(p)
        if "dynamic" in spec:
            # A dynamic completer's baked choices are runtime data (whatever
            # the completer saw at build time), not contract — a snapshot
            # that pinned them would flap between machines.
            spec.pop("choices", None)
        params.append(spec)
    entry: dict[str, Any] = {"task": address, "help": task["help"], "params": params}
    if task.get("hidden"):
        entry["hidden"] = True
    returned = task.get("returned")
    doc = task.get("returned_doc")
    if returned is not None or doc:
        block: dict[str, Any] = {}
        if returned is not None:
            block["schema"] = _describe.returns_json_schema(returned)
        if doc:
            block["doc"] = doc
        entry["returns"] = block
    return entry


def _describe_contract(tree: dict[str, Any], target: object, argv_rest: bool) -> int:
    """`--describe[=ADDR]`: the input+output contract as one JSON document.

    Bare, it hands an agent the entire API — every task's params and declared
    return schema, sorted by address so a checked-in snapshot is invariant to
    declaration order. A task address answers with that one entry; a group
    address answers for its whole subtree — the prefix-names-a-subtree rule
    every address surface speaks — and a runnable group's default alone is
    its real `group.default` address, the child the bare group runs. Plain
    JSON on stdout either way: like `--where`, the output already is the
    machine format, so `--json` adds nothing.
    """
    if not target:  # named bare: the whole tree's contract
        if argv_rest:
            # `fm --describe check` reads as bare --describe plus a run of
            # `check` — surely not what was meant. Teach the `=` spelling
            # rather than silently describing everything.
            _error("--describe: name the address in the value — --describe=<addr>")
            return EX_USAGE
        entries = [
            _contract_entry(address, node)
            for address, node in sorted(_iter_task_nodes(tree, ""))
        ]
    else:
        dotted = str(target)
        path = dotted.split(".")
        node: dict[str, Any] = tree
        try:
            if "" in path:
                raise KeyError(dotted)
            for name in path[:-1]:
                node = node["groups"][name]
            last = path[-1]
            if last in node["tasks"]:
                entries = [_contract_entry(dotted, node["tasks"][last])]
            else:
                sub = node["groups"][last]
                entries = [
                    _contract_entry(address, task)
                    for address, task in sorted(_iter_task_nodes(sub, f"{dotted}."))
                ]
        except (KeyError, IndexError):
            names = _split.flat_addresses(tree) + list(_describe.iter_group_paths(tree))
            _error(
                f"--describe: unknown task or group {dotted!r}"
                f"{_split._did_you_mean(dotted, names)}"
            )
            return EX_USAGE
    print(json.dumps({"schema": 1, "tasks": entries}, indent=2))
    return 0


def _utf8(text: str) -> bytes:
    """A document's text as UTF-8 bytes, degrading only what cannot be text.

    Strict would trade a silent '?' for a traceback on an already-finished
    task, and the only strings that fail are ones holding lone surrogates —
    the escape hatch for bytes that were never characters, which no encoding
    can carry. Everything a reader would call text survives intact.
    """
    return text.encode("utf-8", "replace")


def _emit_bytes(payload: bytes) -> None:
    """Put a document's bytes on stdout, underneath the text layer's codec.

    A document is a payload footman defines, so it is UTF-8 always — no
    autodetection, no locale. `sys.stdout` encodes with the locale's codec
    and a run reconfigures it to `errors="replace"` so a tool's stray glyph
    can never crash the run; together those turned "café" into b"caf?" on a
    cp1252 console, with exit 0 and nothing on stderr. Writing bytes to the
    underlying buffer skips the translation and pins UTF-8; captured stdout
    (tests, some wrappers) has no buffer, so fall back.
    """
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:  # an embedded runner's text capture has no byte buffer
        sys.stdout.write(payload.decode("utf-8", "replace"))
        return
    # Anything already handed to the text layer belongs ahead of these bytes.
    with contextlib.suppress(Exception):
        sys.stdout.flush()
    buffer.write(payload)
    buffer.flush()


def _emit_document(value: object, inner: object) -> None:
    """The declared document, on stdout: text verbatim (plus a trailing
    newline), bytes raw, anything else JSON — pretty-printed on a terminal,
    one compact line into a pipe, always a trailing newline, and always UTF-8.
    Encoded via `_describe.json_default`, the same encoder `--json` uses, so
    dataclasses serialise and `Secret` redacts identically on both surfaces."""
    mode = _coerce.emission_mode(inner)
    if mode == "bytes" and isinstance(value, (bytes, bytearray)):
        _emit_bytes(bytes(value))
        return
    if mode == "text" and isinstance(value, str):
        _emit_bytes(_utf8(value if value.endswith("\n") else value + "\n"))
        return
    try:
        tty = sys.stdout.isatty()
    except Exception:
        tty = False
    text = json.dumps(
        # The walk `--json` does, which this surface claimed above to share and
        # did not: `json_default` alone cannot redact, because `Secret` is a
        # `str` subclass and rides `json.dumps`' fast path without ever
        # reaching the `default` hook. A document went out in the clear while
        # the envelope for the same task redacted.
        _describe.redact(value),
        default=_describe.json_default,
        ensure_ascii=False,
        indent=2 if tty else None,
        separators=None if tty else (",", ":"),
    )
    _emit_bytes(_utf8(text + "\n"))


def _stack_wanted(verbose: bool) -> bool:
    """`_describe.stack_wanted`, asked about this run's real stderr.

    The *real* stream, not `ctx.tty`, which folds in whether output is being
    captured: `Runner` captures, and a suite is not a log to be rescued.
    """
    return _describe.stack_wanted(verbose, context.real_stderr().isatty())


def _print_summary(
    results: list[_executor.TaskResult],
    *,
    timings: bool,
    verbose: bool = False,
    total: float,
) -> None:
    # The summary is commentary about the run, not the run's output — it goes
    # to stderr so `fm task > file` captures exactly what the task produced.
    # Each receipt is task-shaped (mark · name · time), the same grid as the
    # step lines above it, with the name in cyan — same family, one rank up.
    color = _color_err
    width = max((_describe.display_width(r.task) for r in results), default=0)
    for result in results:
        ok = result.ok
        cancelled = result.cancelled
        state = _executor.reported_state(result)
        if color:
            if state == "shared":
                # Dimmed, not green: nothing ran, the run already had it.
                mark = "\033[2m·\033[0m"
            elif state == "skipped":
                # Dimmed too: nothing ran, and the cause is the story.
                mark = "\033[2m-\033[0m"
            elif ok:
                mark = "\033[32m✓\033[0m"
            elif cancelled:
                mark = "\033[33m○\033[0m"  # cut off by fail-fast, not a failure
            else:
                mark = "\033[31m✗\033[0m"
            name = _describe.bold_cyan(_describe.pad_to(result.task, width), True)
        else:
            word = "ok" if ok else ("cut" if cancelled else "FAIL")
            if state == "shared":
                word = "same"
            elif state == "skipped":
                word = "skip"
            mark = f"{word:<4}"
            name = _describe.pad_to(result.task, width)
        if state == "shared":
            timing = "(already run this run)"
        elif state == "skipped":
            cause = result.blocked_by
            timing = f"(blocked by {cause})" if cause else "(skipped)"
        elif timings:
            timing = f"({result.duration * 1000:.0f} ms)"
        else:
            timing = f"({_progress.fmt_secs(result.duration)})"
        if color:
            timing = f"\033[36m{timing}\033[0m"
        print(f"{mark} {name}  {timing}", file=sys.stderr)
        if cancelled:
            _error(f"{result.task}: cancelled — fail-fast stopped the run")
        elif result.error is not None:
            # A deliberate stop with a reason (`fail("…")` / `sys.exit("…")`)
            # renders verbatim, the way Python prints it — not "Failed: …"; a bare
            # `fail()` with no reason falls back to the code line. A real exception
            # keeps its type, which signals a crash, not a chosen stop.
            err = result.error
            if context._is_deliberate_stop(err):
                detail = str(err) or f"exited with code {result.code}"
            else:
                detail = f"{type(err).__name__}: {err}"
            _error(f"{result.task}: {detail}")
            if not context._was_expected(err):
                # An exception nobody planned: the reader's own bug, and the
                # only question it raises is where. The line used to end above,
                # so a task that raised said what happened and never where —
                # information destroyed rather than merely unformatted.
                #
                # The place by default, the whole stack under -v or when
                # stderr is not a terminal. Not a terminal means a log — CI, a
                # redirect, cron — where there is no second run to add -v to,
                # and the same reasoning already sends the progress estimate
                # down its one-shot path.
                # One place decides, whichever half of the runner the exception
                # came out of: a step keeps its stack in the record and leaves
                # the placing to here, so a task and a step read the same and
                # neither is said twice — including under a quiet capture,
                # where a step's receipt is never displayed at all.
                if _stack_wanted(verbose) and (stack := _describe.user_traceback(err)):
                    print(stack.rstrip("\n"), file=sys.stderr)
                elif where := _describe.user_frame(err):
                    _error(f"       at {where}")
        elif not result.ok and state != "skipped":
            # A skipped row's whole story is its cause, already on the line.
            _error(f"{result.task}: exited with code {result.code}")
    if len(results) > 1:  # one task's receipt already carries the total
        took = f"took {_progress.fmt_secs(total)}"
        if color:
            took = f"\033[2m{took}\033[0m"
        print(took, file=sys.stderr)


def _check_returns(
    root: registry.Group, results: list[_executor.TaskResult]
) -> dict[int, tuple[dict[str, Any], str | None]]:
    """Per-result output contract: `{index: (native spec, mismatch note)}`.

    The producer side of drift protection, paid only by declaring tasks
    (~tens of µs per value): every reported return that has a declared
    schema is walked against it, and a mismatch warns on stderr — in every
    mode, so the rename goes red in the producer's own gate before any
    consumer integrates — and rides the envelope as `returned_mismatch`.
    A payload problem stays a note; the exit code never moves.
    """
    memo: dict[int, dict[str, Any] | None] = {}
    out: dict[int, tuple[dict[str, Any], str | None]] = {}
    for index, r in enumerate(results):
        try:
            fn = _executor.resolve(root, r.task.split("."))
        except (KeyError, IndexError):
            continue  # a synthetic row (a step-only shared node) has no task
        if id(fn) not in memo:
            memo[id(fn)] = _manifest.returned_spec(
                _manifest.resolved_signature(fn).return_annotation
            )
        spec = memo[id(fn)]
        if spec is None:
            continue
        value = r.returned
        note = None
        if value is not None and not (
            isinstance(value, int) and not isinstance(value, bool)
        ):
            note = _describe.returned_mismatch(value, spec)
            if note:
                _error(f"{r.task}: return value breaks its declared shape — {note}")
        out[index] = (spec, note)
    return out


def _print_json(
    results: list[_executor.TaskResult],
    *,
    total: float,
    returns: dict[int, tuple[dict[str, Any], str | None]] | None = None,
) -> None:
    payload = []
    for index, r in enumerate(results):
        entry: dict[str, object] = {
            "task": r.task,
            "address": r.address,
            "ok": r.ok,
            "state": _executor.reported_state(r),
            "cancelled": r.cancelled,
            "code": r.code,
            "duration_ms": round(r.duration * 1000, 3),
            "output": r.output,
            "error": None if r.error is None else str(r.error),
        }
        if r.error is not None and not context._was_expected(r.error):
            # An exception nobody planned, so the reader's own bug: the stack
            # rides along whatever the terminal was doing. A consumer of this
            # envelope is a log or a dashboard, never someone who can re-run
            # with -v, and losing the one thing that places the failure is the
            # problem this whole rule exists to fix. Trimmed of footman's
            # frames like the printed one. Additive to schema 1; absent for a
            # command that exited non-zero or a deliberate stop, which have
            # nothing to place.
            entry["traceback"] = _describe.user_traceback(r.error)
        if r.title:
            # A reviewer's label for the row; absent when no one set one.
            entry["title"] = r.title
        if r.audit:
            # The row was reviewed: its verdict provenance and the derived
            # failing moment, same shape as on steps. Additive to schema 1.
            entry["audit"] = [[e.moment, e.actor, e.code] for e in r.audit]
            entry["failed_at"] = r.failed_at
        if r.blocked_by:
            entry["blocked_by"] = r.blocked_by
        if r.eligible is not None and r.started is not None:
            # Launch latency: how long the node sat ready, waiting for a
            # worker, after its last prerequisite finished. Never part of
            # `duration_ms` — the task wasn't running.
            entry["queued_ms"] = round(max(r.started - r.eligible, 0.0) * 1000, 3)
        if r.lane_waits:
            # Which lanes serialised this task, for how long — present only
            # when the claim actually waited. Additive to schema 1.
            entry["lane_waits"] = [
                {"lane": lane, "waited_ms": round(seconds * 1000, 3)}
                for lane, seconds in r.lane_waits
            ]
        if r.thread:
            # Where it ran: the worker's stable name and OS thread id — the
            # correlation keys a profiler's timeline uses. Absent for a row
            # that executed nothing (a `shared` row, a refusal).
            entry["thread"] = r.thread
            entry["thread_id"] = r.thread_id
        if r.after:
            # The plan's edges into this row, by address — what a profile
            # draws dependency arrows from. Additive to schema 1.
            entry["after"] = list(r.after)
        if (docs := _task_docs_url(r.task)) is not None:
            # The same pointer the terminal links carry, for readers that
            # cannot click — an agent's next move after a confusing row is
            # the task's docs. Additive to schema 1.
            entry["docs_url"] = docs
        if r.notes:
            # What the level system resolved for this execution — every
            # fired note, trace included: the machine channel ignores print
            # gating. Additive to schema 1.
            entry["notes"] = [
                {
                    "kind": n.kind,
                    "level": n.level,
                    "site": n.site,
                    "text": n.text,
                }
                for n in r.notes
            ]
        if r.sections and r.started is not None:
            # Task-authored profiling: `at_ms` places each section relative
            # to the task's own start (negative is legal — a retroactive
            # stream window may predate the task). Additive to schema 1.
            entry["sections"] = [
                {
                    "name": s.name,
                    **({"stream": s.stream} if s.stream else {}),
                    "at_ms": round((s.started - r.started) * 1000, 3),
                    "duration_ms": round(s.duration * 1000, 3),
                }
                for s in r.sections
            ]
        value = r.returned
        # An int return is the exit-code channel (duty's contract), not data;
        # None is "nothing to say". Everything else — bools included — is data.
        if value is not None and not (
            isinstance(value, int) and not isinstance(value, bool)
        ):
            value = _describe.redact(value)  # a Secret never serialises
            try:
                json.dumps(value, default=_describe.json_default)
            except (TypeError, ValueError) as exc:  # ValueError: circular refs
                entry["returned_error"] = str(exc)
                _error(f"{r.task}: --json: return value dropped — {exc}")
            else:
                entry["returned"] = value
        if (contract := (returns or {}).get(index)) is not None:
            spec, note = contract
            # The declared output contract, in the baked native form — data
            # and how to read it, one call. Present whenever the task
            # declares, value or no value (a failed run still has a shape).
            entry["returned_schema"] = spec
            if note:
                # Loud but local: the value still serialises; the note says
                # where it first breaks the declared shape.
                entry["returned_mismatch"] = note
        payload.append(entry)
        # The children stand alone, right after their requester: ONE flat
        # list in creation order, the tree carried by every item's address
        # (a prefix names a subtree, and a child's entry is self-describing
        # before its parent's would even be complete in a stream). A row
        # has "task"; a step has "command" — that is the reader's kind
        # test, and `[.items[] | select(.task == NAME)]` is the name
        # lookup, a LIST by contract: the same label may name distinct
        # work, distinct by address.
        for s in r.steps:
            payload.append(
                {
                    # `shown`, not `command`: a document that leaves the
                    # process is a display, and a `Secret` argument has no
                    # business in a CI log. The record keeps the real line.
                    "command": s.shown,
                    "address": s.address,
                    "code": s.code,
                    "duration_ms": round(s.duration * 1000, 3),
                    # Where the step sits inside its task's span — absent for
                    # a record that never ran (dry-run). Additive to schema 1.
                    **(
                        {"at_ms": round((s.started - r.started) * 1000, 3)}
                        if s.started is not None and r.started is not None
                        else {}
                    ),
                    "stdout": s.stdout,
                    "stderr": s.stderr,
                    "audit": [[e.moment, e.actor, e.code] for e in s.audit],
                    "failed_at": s.failed_at,
                }
            )
    # The stable machine surface: an envelope so post-1.0 additions (metadata,
    # summaries) never have to break consumers of the items list.
    print(
        json.dumps(
            {"schema": 1, "total_ms": round(total * 1000, 3), "items": payload},
            indent=2,
            default=_describe.json_default,
        )
    )


def _resolve_shell(shell: object, flag: str) -> str | None:
    """Resolve *shell* to a supported name for *flag*, or None after `_error`.

    A bare mention (no value) detects the invoking shell; an explicit value
    is lowercased and de-aliased (`nu`→`nushell`, `powershell`→`pwsh`).
    """
    from livery.footman import _shellcomp

    supported = "|".join(_shellcomp.SHELLS)
    if not shell:  # named bare: work out which shell is asking
        name = _shellcomp.detect_shell()
        if name is None:
            _error(
                f"{flag}: could not detect your shell — "
                f"name it explicitly: one of {supported}"
            )
            return None
    else:
        name = str(shell or "").lower()
        name = {"powershell": "pwsh", "nu": "nushell"}.get(name, name)  # muscle-memory
    if name not in _shellcomp.SHELLS:
        got = f" (got {name!r})" if name else ""
        _error(f"{flag} expects one of {supported}{got}")
        return None
    return name


def _completion_action(key: str, shell: object) -> int:
    """The completion trio, one body: install and uninstall touch rc files
    and echo what they did; setup prints the hook for `eval` (so its
    detection note goes to stderr — stdout must stay clean). A bare flag
    detects the invoking shell and says which one it worked out.
    """
    from livery.footman import _shellcomp

    flag = "--" + key.replace("_", "-")
    name = _resolve_shell(shell, flag)
    if name is None:
        return EX_USAGE
    setup = key == "setup_completion"
    if not shell:  # named bare: say which shell we worked out
        print(f"detected shell: {name}", file=sys.stderr if setup else sys.stdout)
    if setup:
        print(_shellcomp.script_for(name, _brand.prog))
        return 0
    act = _shellcomp.install if key == "install_completion" else _shellcomp.uninstall
    try:
        lines = act(name, _brand.prog)
    except _shellcomp.InstallError as exc:
        _error(f"{flag} {name}: {exc}")
        return EX_USAGE
    for line in lines:
        print(line)
    return 0


def run(
    argv: list[str],
    brand: Brand = DEFAULT_BRAND,
    collect: list[_executor.TaskResult] | None = None,
) -> int:
    """Run the CLI; when *collect* is given, extend it with the TaskResults.

    `collect` exists for `footman.testing.Runner`, which needs the structured
    results as well as the exit code and printed output.
    """
    try:
        with _signals.installed():
            return _run(argv, brand, collect)
    except KeyboardInterrupt:
        # In --json mode nothing has reached stdout yet (capture buffers task
        # output), so the envelope contract still holds at 130.
        return _refuse(_wants_json(argv), "interrupted", 130)
    except _signals.Stop as stop:
        # A supervisor asked for a stop — `timeout`, `docker stop`, a
        # cancelled CI job. It is delivered as an exception in the main
        # thread on purpose, so it unwinds through the same abort arms Ctrl-C
        # does: the in-flight process trees are already reaped by the time it
        # lands here, and the envelope holds for the same reason 130's does.
        return _refuse(_wants_json(argv), stop.word, stop.code)


_WINDOWS = os.name == "nt"  # decided at import; a constant tests can steer


GC_INTERVAL_S = 24 * 3600


def _maybe_collect(cfg: dict[str, object], skip_stem: str) -> None:
    """At most daily, and never on a fresh cache, spawn the collector.

    *skip_stem* names this invocation's own manifest — the cwd's, or the
    shared global one in global mode — so the sweep never eats the file the
    run just wrote.

    A missing stamp is *planted*, not acted on — the first run a cache ever
    sees schedules collection for tomorrow, so short-lived caches (a test
    suite's tmp dirs) never spawn anything. An aged stamp is re-touched
    *before* spawning, the refresh idiom: concurrent runs elect one
    collector, and a crashed child costs a day, not correctness.
    """
    if cfg.get("gc") is False or os.environ.get(_paths.env_var("NO_GC")):
        return
    cache = _paths.footman_cache_dir()
    stamp = cache / "gc.stamp"
    try:
        age = time.time() - stamp.stat().st_mtime
    except OSError:
        with contextlib.suppress(OSError):
            cache.mkdir(parents=True, exist_ok=True)
            stamp.touch()
        return
    if age < GC_INTERVAL_S:
        return
    with contextlib.suppress(OSError):
        stamp.touch()
    _spawn_gc(cache, skip_stem)


def _spawn_gc(cache: Path, skip_stem: str) -> None:
    """Detach the collector child through `_complete.detach` — one copy of
    the background-child dance, where its Windows story is pinned by tests,
    instead of the drift-prone verbatim twin this used to carry."""
    from livery.footman import _complete

    _complete.detach(
        [
            sys.executable,
            # `-P`, like the completion children: `-c` would otherwise head
            # sys.path with the directory the run started in, where a
            # `footman.py` would answer the collector's own import.
            "-P",
            "-c",
            "from livery.footman import _gc; _gc.main()",
            str(cache),
            skip_stem,
        ]
    )


def _find_uv() -> str | None:
    """The uv both handoffs use — this runner's own environment, then PATH.

    Lives in `_script` so the completion children, which never import this
    module, resolve uv exactly the same way.
    """
    return _script.find_uv()


def _reexec(cmd: list[str]) -> None:
    """Replace this invocation with *cmd* — the tail every handoff shares.

    On POSIX the process is replaced (`execvp`: tty, signals, stdin and the
    exit code all belong to the child). Windows `exec*` lies — the parent
    exits while the child runs on — so there it spawns and waits, swallowing
    its own Ctrl-C and console break (the console delivered both to the child
    as well, which will exit 130 or 143 on its own terms).
    """
    sys.stdout.flush()
    sys.stderr.flush()
    if _WINDOWS:
        proc = subprocess.Popen(cmd)
        while True:
            try:
                raise SystemExit(proc.wait())
            except (KeyboardInterrupt, _signals.Stop):
                continue
    os.execvp(cmd[0], cmd)


def _script_hint(exc: object) -> str:
    """The sentence a failed import of a *script* tasks file earns.

    A file that declares its own dependencies and still failed to import
    is almost always one whose environment never got built — no uv, or a
    cascade that made the script rule inapplicable. Say so where the
    failure is read, rather than leaving a bare ImportError.
    """
    path = getattr(exc, "path", None)
    if path is None or not isinstance(getattr(exc, "original", None), ImportError):
        return ""
    meta, _warning = _script.read_block(Path(str(path)))
    if meta is None or not meta.get("dependencies"):
        return ""
    return (
        f" — {Path(str(path)).name} declares script dependencies, so it "
        f"expects its own environment: install uv, name the file alone with "
        f"-f, or add the dependencies to this project"
    )


def _inside(venv: Path) -> bool:
    """Whether this interpreter is already running out of *venv* — the rule
    lives in `_script`, shared with the completion children."""
    return _script.inside(venv)


def _locked_project(probe: Path) -> Path | None:
    """The nearest ancestor holding a `uv.lock` — one existence walk, no read.
    Lives in `_script`, shared with the completion children."""
    return _script.locked_project(probe)


def _pins_the_runner(root: Path) -> bool:
    """Whether *root*'s lockfile pins this runner — the question that decides
    whether the invocation belongs to that project's environment. The read
    lives in `_script`, shared with the completion children."""
    return _script.pins_dist(root, _brand.dist or "livery-footman")


def _note_ignored_block(g: dict[str, object], probe: Path) -> None:
    """Under `-v` only: say that a script block is being ignored here.

    A tasks file may carry `# /// script` metadata and still live inside a
    project that pins the runner — a portable file checked into a repo is
    exactly that. The project wins, and the block is a non-event: worth
    seeing when you asked to see everything, never worth a warning.
    """
    if not g.get("verbose"):
        return
    source = _script_source(g, probe)
    if source is None:
        return
    meta, _warning = _script.read_block(source)
    if meta is None or not meta.get("dependencies"):
        return
    print(
        f"{_brand.prog}: {source.name} declares script dependencies; this "
        f"project pins the runner, so they are ignored here",
        file=sys.stderr,
    )


def _script_source(g: dict[str, object], probe: Path) -> Path | None:
    """The single tasks file this invocation would load, or None.

    A script's environment can only be *the* environment, so the rule is
    single-file: an explicit `-f`, or a cascade that found exactly one
    file. Resolved through `resolve_task_files` against the `-C` probe —
    the same walk the run will do, so the handoff can never disagree with
    it — and quiet, because the real run repeats every warning.
    """
    try:
        found = resolve_task_files(g, on_warning=lambda _: None, cwd=probe)
    except (_config.ConfigError, _config.CascadeError):
        return None  # the real run reports these properly
    # The user rung never disables a project's script environment: project
    # files answer first, and only a machine with nothing but the user file
    # runs THAT file's environment — it is a tasks file like any other.
    project = [f for f in found.files if f != found.user]
    candidates = project or ([found.user] if found.user else [])
    return candidates[0] if len(candidates) == 1 else None


def _script_handoff(argv: list[str], g: dict[str, object], probe: Path) -> int | None:
    """Hand off to a tasks file's own PEP 723 script environment.

    A tasks file that declares `dependencies` carries its own world: uv
    materialises it (`uv sync --script`), and this invocation continues
    inside it (`uv python find --script` → `python -m livery.footman …`). One
    portable file then runs anywhere the runner is installed, no project
    needed. Returns `None` to say "not my business, carry on"; an exit code
    when this invocation is over — a refusal, or uv's own failure.

    Never a hard refusal for anything environmental: no uv, a cascade of
    several files, an unreadable block — all fall through to running the
    file as-is, exactly as before this rule existed. The import that then
    fails carries the teaching (`_execute`). The one refusal is a file
    that declares dependencies but not the runner it imports, because that
    environment provably cannot run it.
    """
    if os.environ.get(_paths.env_var("UV_REEXEC")) or os.environ.get(
        _paths.env_var("NO_UV")
    ):
        return None
    source = _script_source(g, probe)
    if source is None:
        return None
    meta, warning = _script.read_block(source)
    if warning is not None:
        _error(f"warning: {warning}")
        return None
    if meta is None or not meta.get("dependencies"):
        return None  # not a script, or a block that asks for no world
    try:
        cfg = _config.load_config(
            probe,
            _paths.find_repo_root(probe),
            str(g["config"]) if g.get("config") else None,
            on_warning=lambda _: None,  # the real run repeats any warning
        )
    except _config.ConfigError:
        return None  # the real run reports the broken --config properly
    if not _uv_wanted(g, cfg):
        return None
    if _brand.dist is None:
        # A branded runner can't know which distribution ships it, so it
        # can't tell whether the script's environment would contain it.
        return None
    if not _script.declares(meta, _brand.dist):
        _error(
            f"{source.name} declares script dependencies but not "
            f"{_brand.dist!r} — the environment it asks for cannot import "
            f"the runner. Add {_brand.dist!r} to its dependencies, or drop "
            f"the script block and run inside a project."
        )
        return EX_USAGE
    # Resolved here, not by the caller: `shutil.which` is a PATH walk (and
    # `shutil` itself ~1.9 ms of archive codecs), and every return above this
    # line is a run that never needed uv at all — which is most of them.
    uv = _find_uv()
    if uv is None:
        return None  # nothing to build the environment with: run as-is
    verbose = bool(g.get("verbose"))
    if verbose:
        print(
            f"{_brand.prog}: handing off to the script environment of "
            f"{source.name} (uv)",
            file=sys.stderr,
        )
    # A script environment is deliberately not the active one: an unrelated
    # VIRTUAL_ENV (a project shell, a `uv run` above us) would only draw a
    # warning from uv and confuse anything the tasks spawn, so it leaves
    # the picture — here and in the child.
    os.environ.pop("VIRTUAL_ENV", None)
    try:
        synced = subprocess.run(_script.sync_argv(uv, source, quiet=not verbose))
        if synced.returncode != 0:
            return synced.returncode  # uv already said why; don't paraphrase it
        found = subprocess.run(
            _script.find_argv(uv, source), capture_output=True, text=True
        )
    except OSError:
        return None  # uv wouldn't start: run as-is rather than refuse
    python = found.stdout.strip()
    if found.returncode != 0 or not python:
        return None
    os.environ[_paths.env_var("UV_REEXEC")] = "1"
    if _brand.dist == "livery-footman":
        _reexec([python, "-m", "livery.footman", *argv])
    else:
        # `-m livery.footman` is the *stock* CLI, so a branded child re-ran this
        # handoff — its belt variable above is scoped to the brand, so the
        # one just set didn't count — and then refused, because the script
        # declares the brand's dist and not 'livery-footman'. The brand's door in
        # the script environment is its own console script; loading the
        # entry point by name runs exactly what that script runs, on any
        # platform, without guessing at the environment's bin layout.
        shim = (
            "import sys;"
            "from importlib.metadata import entry_points;"
            f"[ep] = entry_points(group='console_scripts', name={_brand.prog!r});"
            "sys.exit(ep.load()())"
        )
        _reexec([python, "-c", shim, *argv])
    return None  # unreachable: _reexec replaces or exits this process


def _uv_handoff(argv: list[str], g: dict[str, object]) -> int | None:
    """Hand this invocation to the environment that owns it.

    Two rules, one order. A project whose `uv.lock` pins the runner has
    already declared what `fm` means there, so it wins — and a tasks file's
    own script metadata is simply not that run's business. Only where no
    project has spoken may a tasks file declare its world for itself
    (`_script_handoff`).

    Returns `None` when nothing applied and the caller carries on, or an
    exit code when the invocation is over.

    The lock rule, one sentence: when the project's `uv.lock` pins footman
    and this interpreter is not already inside the project's environment,
    the invocation belongs to `uv run` — the project has declared what `fm`
    means there, version and all. Reached only where tasks would be
    imported: `--version`, completion management, and the TAB hot path
    never arrive here. Opt out with `uv = false` in `[tool.footman]` or
    `FOOTMAN_NO_UV=1`. The child carries `FOOTMAN_UV_REEXEC` as a loop
    belt for projects whose environment lives outside `.venv`.

    The process is replaced through `_reexec`, which owns the POSIX /
    Windows difference.
    """
    if os.environ.get(_paths.env_var("UV_REEXEC")) or os.environ.get(
        _paths.env_var("NO_UV")
    ):
        return None
    try:
        probe = Path(str(g.get("directory") or Path.cwd())).resolve(strict=True)
    except OSError:
        return None  # a missing -C target: _run's own error path reports it
    root = _locked_project(probe)
    if root is not None and _inside(root / ".venv") and not g.get("verbose"):
        # Already running out of that project's environment, which is the
        # overwhelmingly common case — and the one answer that needs no
        # lockfile read at all. Asked first, because reading a real
        # project's `uv.lock` costs ~21 ms and every invocation paid it
        # just to conclude it was already home. Under `-v` the full walk
        # runs anyway: its note about an ignored script block is exactly
        # what someone asking to see everything is asking for.
        return None
    if root is None or not _pins_the_runner(root):
        # Nobody has declared what the runner means here, so a tasks file
        # may declare it for itself.
        return _script_handoff(argv, g, probe)
    # A pinned project has already declared what the runner means here, so
    # a tasks file's own script block is simply not this run's business —
    # not a warning, not a refusal; visible under -v and nowhere else.
    _note_ignored_block(g, probe)
    if _inside(root / ".venv"):
        return None  # already the project's environment (the -v path lands here)
    uv = _find_uv()
    if uv is None:
        return None
    try:
        cfg = _config.load_config(
            probe,
            _paths.find_repo_root(probe),
            str(g["config"]) if g.get("config") else None,
            on_warning=lambda _: None,  # the real run repeats any warning
        )
    except _config.ConfigError:
        return None  # the real run reports the broken --config properly
    if not _uv_wanted(g, cfg):
        return None
    if g.get("verbose"):
        print(
            f"{_brand.prog}: handing off to uv run --project {root}",
            file=sys.stderr,
        )
    os.environ[_paths.env_var("UV_REEXEC")] = "1"
    _reexec([uv, "run", "--project", str(root), _brand.prog, *argv])
    return None  # unreachable: _reexec replaces or exits this process


_SYNC_HINT = " — the project's environment may be out of date: run uv sync"


def _uv_retry(
    argv: list[str], g: dict[str, object], cfg: dict[str, Any], exc: BaseException
) -> str:
    """A failed import in a pinned project may just be a stale environment.

    The lock rule's "already home" answer trusts that the project's venv
    matches its lockfile; a dependency locked after the last sync breaks
    that trust, and the first symptom is exactly this import failure. So:
    hand the invocation to `uv run --project` after all — it syncs, then
    retries — and let a failure *after* that surface unchanged (the child
    carries the loop belt, which doubles as "sync was already tried").

    Re-execs and never returns when the retry applies. Otherwise returns
    the sentence to append to the refusal: the `uv sync` hint when a
    pinned project exists but the retry cannot run (no uv, opted out), or
    nothing at all — a non-import failure, no pinned project, or a run
    that already came through `uv run`.
    """
    if os.environ.get(_paths.env_var("UV_REEXEC")) or os.environ.get(
        _paths.env_var("NO_UV")
    ):
        return ""  # uv run already synced (or was told not to): the failure is real
    if not _script.import_caused(exc):
        return ""  # a syntax error or a raise — no sync would mend it
    root = _locked_project(Path.cwd())
    if root is None or not _pins_the_runner(root):
        return ""
    if not _uv_wanted(g, cfg):
        return _SYNC_HINT  # asked to stay out of uv: advise, don't act
    uv = _find_uv()
    if uv is None:
        return _SYNC_HINT
    if g.get("verbose"):
        print(
            f"{_brand.prog}: import failed; retrying through uv run --project {root}",
            file=sys.stderr,
        )
    os.environ[_paths.env_var("UV_REEXEC")] = "1"
    _reexec([uv, "run", "--project", str(root), _brand.prog, *argv])
    return ""  # unreachable: _reexec replaces or exits this process


def _run(
    argv: list[str],
    brand: Brand,
    collect: list[_executor.TaskResult] | None = None,
    *,
    handoff: bool = True,
) -> int:
    global _brand
    _brand = brand
    try:
        # Before anything writes: a cache and a data directory that are the
        # same place would have the collector deleting durable things.
        _paths.check_locations()
    except _paths.LocationError as exc:
        return _refuse(_wants_json(argv), str(exc))
    try:
        pre_globals, after = _split._parse_globals(argv, 0, lenient=True)
    except _split.ChainError as exc:
        return _refuse(_wants_json(argv), str(exc))
    g = _globals_to_dict(pre_globals)
    _set_colors(_resolve_color(g))
    wants_help = _wants_help(argv)

    if g.get("version"):  # D7: --version wins even over --help
        return _print_version(bool(g.get("json")))
    # Asking for help must never touch the filesystem: `--install-completion
    # fish --help` used to write rc files before printing anything.
    for key in ("install_completion", "setup_completion", "uninstall_completion"):
        if key in g and not wants_help:
            value = g.get(key)
            if not value and after < len(argv) and not argv[after].startswith("-"):
                # A word behind the bare action has nowhere else to go — these
                # end the invocation, so there is no task chain for it to be
                # part of. Teach the `=` form rather than acting on the
                # detected shell and leaving the word unexplained.
                flag = "--" + key.replace("_", "-")
                return _refuse(
                    bool(g.get("json")),
                    _split._expects_value(None, flag, "[SHELL]", argv[after]),
                )
            return _completion_action(key, value)
    # May replace the process (POSIX) or exit with the child's code
    # (Windows); returns quietly whenever the handoff doesn't apply.
    # `handoff=False` is the embedded-harness escape: `Runner.invoke` runs
    # inside a host process (pytest — possibly a pytest-xdist worker whose
    # stdio IS the test-protocol channel), and an execvp there replaces the
    # host itself. An embedded invocation must always run in-process.
    if handoff and (code := _uv_handoff(argv, g)) is not None:
        # A handoff that neither replaced this process nor stayed out of the
        # way has ended the invocation: a refusal, or uv's own failure.
        return code

    if not g.get("directory"):
        return _execute(argv, g, pre_globals, after, wants_help, collect, handoff)

    # -C must not permanently move the process (a `Runner.invoke` shares the
    # host pytest's cwd): chdir, run, then restore in a finally. The original
    # dir may have vanished mid-run, so the restore is best-effort.
    saved_cwd = os.getcwd()
    try:
        os.chdir(str(g["directory"]))
    except OSError as exc:
        return _refuse(bool(g.get("json")), f"-C {g['directory']}: {exc}")
    try:
        return _execute(argv, g, pre_globals, after, wants_help, collect, handoff)
    finally:
        with contextlib.suppress(OSError):
            os.chdir(saved_cwd)


def _execute(
    argv: list[str],
    g: dict[str, object],
    pre_globals: list[str],
    after: int,
    wants_help: bool,
    collect: list[_executor.TaskResult] | None,
    handoff: bool,
) -> int:
    """Discover the cascade, load + sync its manifest, then run the tree.

    Everything after globals/`--version`/`--install-completion`/`-C`: the
    disk-backed half that `run_group` (in-memory) deliberately skips.
    *g* and *after* are the entry point's one lenient parse, passed down —
    argv is never re-parsed on the way in. *handoff* rides along for the
    stale-environment retry: an embedded `Runner.invoke` must never exec.
    """
    # Before discovery, because discovery already consults the built-in
    # set: a `[builtins]` table footman cannot read would otherwise be
    # swallowed into "no tasks file found" — the one answer that hides the
    # mistake instead of naming it.
    try:
        _config.discovery_mode()
        _config.user_builtin()
    except _config.ConfigError as exc:
        return _refuse(bool(g.get("json")), str(exc))
    # "Bare" means no chain was asked for — globals-only lines (`fm --json`,
    # `fm -k`) are listing-shaped, exactly like they are when tasks exist.
    found = _discover_files(g, wants_help, bare=after >= len(argv))
    if isinstance(found, int):
        return found
    files, cfg = found.files, found.cfg
    json_mode = bool(g.get("json"))

    base = registry.Group("root")
    if (base_set := _builtin()) and not g.get("tasks_file"):
        # The built-in set is the cascade's outermost rung — under the user
        # rung, under the project's own files — so the full ladder is
        # project > user > built-in wherever you stand. Everything nearer
        # shadows it by name, exactly as the cascade already resolves its
        # own rungs, which is what lets a project own a name a built-in
        # also uses without either of them being special.
        #
        # `expose` is what keeps this honest in both directions: a package's
        # tasks are `project_only` until one opts out, so joining the
        # cascade offers nothing outside a project that did not say it
        # belonged there. (`-f` still means total control: one file, no
        # cascade, no base.)
        built = _base_tree(base_set, json_mode)
        if isinstance(built, int):
            return built
        base = built
    plugins_cfg = cfg.get("plugins")
    if plugins_cfg and not isinstance(plugins_cfg, dict):
        # The old list-valued key died with the composition rework: mounts are
        # authored in tasks.py, where placement, filtering, and overrides
        # live. A *table* is the reserved sections child instead —
        # `[tool.footman.plugins.<section>]` holds a provider's own settings.
        return _refuse(
            json_mode,
            f"the [tool.{_paths.config_table()}] plugins key was removed — "
            "mount plugins from "
            'tasks.py instead: plugin("footman.docs", into="footman") '
            "(footman.compose.plugin; see the composing docs)",
        )
    # The note-level policy: validated loudly (an ignored policy line is a
    # wall someone thinks is up), then installed for the run — always, so a
    # previous embedded invocation's table never leaks into this one.
    notes_cfg = cfg.get("notes")
    if notes_cfg is not None and (error := _notes.validate(notes_cfg)) is not None:
        return _refuse(json_mode, error)
    _notes.install_policy(notes_cfg)
    # The docs-link template: validated loudly (a link scheme that silently
    # pointed nowhere would be worse than none), installed for the run —
    # always, so a previous embedded invocation's template never leaks.
    global _docs_url
    docs_cfg = cfg.get("docs_url")
    if docs_cfg is not None and (bad := _describe.docs_url_error(docs_cfg)) is not None:
        return _refuse(json_mode, bad)
    _docs_url = docs_cfg if isinstance(docs_cfg, str) else None

    inv = invocation.Invocation(
        cli=g,
        config=cfg,
        # The project cascade's top — never the user rung's directory. `""`
        # is global mode: footman invents no root where there is no project.
        root=found.root,
        cwd=os.getcwd(),
    )
    try:
        reg = _discover.load_tree(files, base=base, inv=inv)
    except _discover.HookError as exc:
        # A hook that raised is a refusal, named — nothing has run yet.
        return _refuse(json_mode, str(exc))
    except _discover.TasksImportError as exc:
        # A stale project environment produces exactly this failure; when
        # the lock rule owns this directory, hand the run to `uv run` (it
        # syncs, then retries) instead of reporting a mystery — or at least
        # name the fix. Never from an embedded Runner: no exec in a host.
        hint = _uv_retry(argv, g, cfg, exc.original) if handoff else ""
        if isinstance(exc.original, registry.RegistrationError):
            # a user mistake, not a crash
            return _refuse(json_mode, f"{exc.path}: {exc.original}{hint}")
        return _refuse(
            json_mode,
            f"failed to import {exc.path}: "
            f"{type(exc.original).__name__}: {exc.original}"
            f"{_script_hint(exc)}{hint}",
        )
    except Exception as exc:  # report import failures cleanly, don't crash
        return _refuse(
            json_mode,
            f"failed to import the task cascade: {type(exc).__name__}: {exc}",
        )
    if (
        clash := registry.validate_global_options(reg.contributions["globals"])
    ) is not None:
        return _refuse(json_mode, clash)
    for orphan in registry.orphan_global_options(reg):
        _error(f"warning: {orphan}")

    # The personal rung's own directory: what tells a user task from a
    # project one when the listings split the two. `None` when the cascade
    # mounted no user file, which simply means nothing can match it.
    user_dir = str(found.user.parent) if found.user is not None else None
    try:
        if g.get("tasks_file"):
            # -f loads one arbitrary file, not the cwd's cascade. Cache its
            # manifest under a (cwd, file) key — separate from the cwd's, so it
            # never poisons plain TAB there — so `fm -f <file> <TAB>` completes
            # that file's tasks. max_age=0: no background refresh (rebuilt on the
            # next -f run); a live refresh is a fast-follow.
            override = str(g.get("tasks_file"))
            tree = _manifest.sync_manifest(
                reg,
                Path.cwd(),
                user_dir=user_dir,
                completion_max_age=0,
                tasks_file=override,
                path=_paths.source_manifest_path(Path.cwd(), Path(override)),
            )["tree"]
        elif config_path := _config_arg(g):
            # An explicit --config reshapes the tree (its own tasks name, its
            # own keys), so its manifest rides a (cwd, config) key — separate
            # from the plain cwd's, which must keep answering plain TAB.
            # max_age=0, like -f: no background refresh, rebuilt on the next
            # such run.
            cfg_tasks = cfg.get("tasks")
            tree = _manifest.sync_manifest(
                reg,
                Path.cwd(),
                user_dir=user_dir,
                completion_max_age=0,
                tasks_file=cfg_tasks
                if isinstance(cfg_tasks, str)
                else _brand.tasks_file,
                path=_paths.source_manifest_path(Path.cwd(), Path(config_path)),
            )["tree"]
        elif not found.root:
            # Global mode: one manifest for every project-less directory,
            # keyed by the brand rather than the cwd — cold once per brand
            # version, not once per directory. No baked cwd (the collector's
            # idle sweep owns it), and the builtin names ride so the refresh
            # child can rebuild a tree it cannot otherwise know.
            cfg_tasks = cfg.get("tasks")
            tree = _manifest.sync_manifest(
                reg,
                Path.cwd(),
                user_dir=user_dir,
                completion_max_age=_config.completion_max_age(cfg, strict=True),
                tasks_file=cfg_tasks
                if isinstance(cfg_tasks, str)
                else _brand.tasks_file,
                path=_paths.global_manifest_path(),
                bake_cwd=False,
                builtin=_builtin(),
                # No project here — the one place the question is asked, so
                # every reader downstream just checks `unexposed`.
                project=False,
            )["tree"]
        else:
            cfg_tasks = cfg.get("tasks")
            tree = _manifest.sync_manifest(
                reg,
                Path.cwd(),
                user_dir=user_dir,
                completion_max_age=_config.completion_max_age(cfg, strict=True),
                tasks_file=cfg_tasks
                if isinstance(cfg_tasks, str)
                else _brand.tasks_file,
            )["tree"]
    except _manifest.ManifestError as exc:  # broken completer, bad markers, …
        return _refuse(json_mode, str(exc))
    except _config.ConfigError as exc:  # a mistyped completion.max_age
        return _refuse(json_mode, str(exc))

    # The `root` policy token's target: the project cascade's top, never the
    # user rung's directory — a personal task's `cwd="root"` means the
    # project it landed in, and outside one the empty root exhausts the
    # ladder to the invocation directory.
    root_dir = found.root
    # Arm the per-task lifecycle for exactly this run: every execution —
    # segment, prerequisite, fan-out member, body call — reaches the same
    # ladder through `run_bound`, and the frozen invocation rides along.
    _executor.install_lifecycle(inv, reg.contributions)
    try:
        code = _run_tree(
            reg, tree, argv, cfg, collect, g, pre_globals, after, root_dir=root_dir
        )
    finally:
        _executor.clear_lifecycle()
        # Core's ladder instances release with the plugins': they are
        # module-level singletons, and a Runner drives many runs in one
        # process — an outside-a-run read goes back to teaching.
        registry.release_global_options(
            (*_split.CORE_LADDER, *reg.contributions["globals"])
        )
    # After the run, so it never adds latency before the user's command —
    # and after the uv handoff by construction (the handoff replaced this
    # process back in _run), so a pinned project's own footman collects.
    _maybe_collect(
        cfg,
        skip_stem=(
            _paths.global_manifest_path().stem
            if not found.root and not g.get("tasks_file")
            else _paths.manifest_path(Path.cwd()).stem
        ),
    )
    return code


def _run_tree(
    reg: registry.Group,
    tree: dict[str, Any],
    argv: list[str],
    cfg: dict[str, object],
    collect: list[_executor.TaskResult] | None,
    g: dict[str, object],
    pre_globals: list[str],
    after: int,
    root_dir: str = "",
    record_times: bool = True,
) -> int:
    """The post-manifest tail: help/where/split/list/tree/dry-run/run/report.

    Shared by the disk path (`_execute`) and the in-memory path (`run_group`),
    so both honour `--help`/`--version`/`--list`/`--tree`/`--json` identically.
    *g* and *after* ride down from the entry point's one lenient parse.
    """
    json_mode = bool(g.get("json"))

    # Core's ladder-bearing options bind first — before the colour repaint,
    # the sort, and the listings, which all read them. One machine with the
    # plugin bind below; a bad value (`--jobs=abc`, `--color=hi`) or a broken
    # config key refuses here, even when a listing would have exited first —
    # eager, like every other parse refusal. Broken config teaches on every
    # invocation, not only the ones it would steer.
    if (
        bad := _executor.bind_global_options(
            _split.CORE_LADDER, pre_globals, config=cfg
        )
    ) is not None:
        return _refuse(json_mode, bad)
    # Config can set the mode too, so re-resolve now that the ladder is in
    # hand and repaint footman's own chrome to match (the pre-run call saw
    # CLI + environment only).
    color_mode = _resolve_color(g, bound=True)
    _set_colors(color_mode)

    # Presentation only: the sorted copy feeds every human-facing walk
    # (--list, --tree, help, the --json catalog). The run resolves through
    # the registry, so execution order never follows this setting.
    if _split.SORT.value:
        tree = _describe.sort_tree(tree)

    show_hidden = bool(g.get("all"))

    live = _choices_resolver(reg)
    if _wants_help(argv):
        return _print_help(tree, argv, after, show_hidden, live)

    if g.get("plugins"):
        return _plugins_report(reg)

    if g.get("where"):
        # Deliberately plain under --json too: `file:line` already is the
        # machine format.
        return _where(reg, tree, str(g["where"]))

    if (describe := g.get("describe")) is not None:
        return _describe_contract(tree, describe, after < len(argv))

    try:
        globals_, segments = _split.split_chain(tree, argv, live)
    except _split.ChainError as exc:
        return _refuse(json_mode, str(exc))
    except _manifest.CompleterError as exc:
        # A strict completer raised while the line was being validated. It
        # used to surface from the manifest build, before any line was read;
        # now it surfaces where its values were actually needed — same taught
        # message, and a broken completer on one task no longer refuses every
        # other task's invocation.
        return _refuse(json_mode, str(exc))

    # A task whose signature claims stdout (`-> Stdout[T]`) makes this a
    # *document run*: stdout carries exactly the addressed task's return
    # value, and every print and run() line replays on stderr instead. Two
    # claimants leave "whose document?" without an answer, so that is a
    # plan-time refusal rather than a silent pick. Only the addressed tasks
    # count — a declaring task reached as a dependency or through a group
    # fan-out is suppressed, not refused, so composing a filter into a
    # bigger task stays legal.
    emitters: list[tuple[_split.Segment, object]] = []
    for seg in segments:
        try:
            seg_fn = _executor.resolve(reg, seg.task.split("."))
        except (KeyError, IndexError):
            continue  # the splitter validated; never refuse twice
        declares, inner = _coerce.emitted(
            _manifest.resolved_signature(seg_fn).return_annotation
        )
        if declares:
            emitters.append((seg, inner))
    if len(emitters) > 1:
        names = " and ".join(s.task for s, _ in emitters)
        return _refuse(
            json_mode,
            f"{names} both declare Stdout[…] — whose document would stdout "
            f"carry? Run them as separate invocations.",
        )

    # Advisory notes from the splitter (a group default's positional value
    # that names — or nearly names — a child task): stderr commentary, ahead
    # of the run, so the plan stays deterministic but never silent. Skipped
    # under --json: the envelope's stdout is the contract and the notes are
    # human-facing teaching, not results.
    if not json_mode:
        for seg in segments:
            for note in seg.notes:
                print(note.replace("{prog}", _brand.prog), file=sys.stderr)

    if not segments:
        if json_mode:
            # The catalog envelope: the manifest tree, params and all — the
            # machine twin of --list/--tree (and of bare `fm`).
            print(json.dumps({"schema": 1, "tree": tree}, indent=2))
            return 0
        if g.get("tree"):
            _print_tree(tree, show_hidden)
        else:
            _print_list(tree, show_hidden)
        if tree["tasks"] or tree["groups"]:
            _print_footer()
        return 0

    # --dry-run is a rehearsal, not a parse echo: the run proceeds with
    # `dry_run` on the context, bodies run, and everything footman owns —
    # recorded run() calls, tools, deferred steps — is faked into honest
    # plan-line receipts. The report shapes (--json included) are the plan.
    dry_run = bool(g.get("dry_run"))
    sequential = bool(_split.SEQUENTIAL.value)

    # The parallel width, from the one ladder: -j/--jobs wins, then config
    # `jobs`, then the declared computed default (cores minus one) — bound
    # above, so what `--help` prints and what the run caps at come from one
    # declaration, and a bad width (`--jobs=abc`, `--jobs=0`, `jobs = 0` in
    # config) was already a taught refusal. Caps both engines (the
    # scheduler's pool and parallel() in task bodies) and is part of the
    # timing key — a -j2 run has a genuinely different duration distribution.
    jobs = int(_split.JOBS.value)

    fetch_cfg = cfg.get("fetch")
    backend = fetch_cfg.get("backend") if isinstance(fetch_cfg, dict) else None
    shell_cfg = cfg.get("shell")
    shell_default = shell_cfg.get("default") if isinstance(shell_cfg, dict) else None
    cwd_cfg = cfg.get("cwd")
    cwd_policy = cwd_cfg if isinstance(cwd_cfg, str) else ""
    if (
        cwd_policy
        and cwd_policy not in registry.CWD_TOKENS
        and not Path(cwd_policy).is_absolute()
    ):
        return _refuse(
            json_mode,
            f"config cwd = {cwd_policy!r} is not a policy token "
            f"({', '.join(registry.CWD_TOKENS)}) or an absolute path — "
            f"a relative suffix belongs on a task's rel=…",
        )
    ctx_config = {
        "fetch_backend": backend if isinstance(backend, str) else "",
        "shell_default": shell_default if isinstance(shell_default, str) else "",
        # The cwd policy ladder's run-wide rungs: the config default, and the
        # two pinned directions tokens resolve against — the highest cascade
        # file's directory (`root`) and the launch cwd snapshot (`asinvoked`).
        "cwd_policy": cwd_policy,
        "root_dir": root_dir,
        "invoked_dir": str(Path.cwd()),
        "quiet": bool(g.get("quiet")),
        # Every step has a row in the envelope, so footman's own receipt
        # lines would only arrive twice — once as chrome inside a task's
        # `output` string, once as the fields a reader actually parses.
        "machine_read": json_mode,
        "verbose": bool(g.get("verbose")),
        # The resolved tri-state, split into the two Context bits: `never` stops
        # all colour, `always` forces it past a non-terminal (the scheduler still
        # gates that off under capture). `auto` leaves both false — tty decides.
        "no_color": color_mode == "never",
        "force_color": color_mode == "always",
        # Tasks can know who invoked them (a branded CLI's prog) — the
        # taskdocs plugin brands its output with this, for one.
        "prog": _brand.prog,
        # The user's -s/config request, so parallel() in task bodies
        # serialises too — not the scheduler's single-node routing.
        "sequential": sequential,
        "jobs": jobs,
        # Interactivity globals: --yes auto-answers confirm() gates, --no-input
        # refuses to prompt (a required prompt errors instead of hanging).
        "assume_yes": bool(g.get("yes")),
        "no_input": not _split.INPUT.value,
        # The rehearsal switch: bodies run, footman's own work is faked.
        "dry_run": dry_run,
    }

    # The timing story: `--no-progress` or `progress = false` in config turns
    # the whole apparatus off, and `--progress` turns it back on for one run —
    # config is a default, never a one-way door. A run is
    # *predictable* when it's on, every task consented, and this is the real
    # cascade (-f runs pollute no cache, times included) — only then do we
    # estimate from history and record the outcome.
    # A rehearsal is near-instant and teaches nothing about durations: no
    # bar, no eta, and (below) no recorded timing to pollute the history.
    progress_on = bool(_split.PROGRESS.value) and not dry_run
    predictable = (
        progress_on
        # Synthetic runs pollute no cache, times included: `-f` runs and
        # in-memory trees (`run_group`) both estimate nothing and record
        # nothing, or a consumer's Runner-driven test suite would write
        # timing history into the real user cache.
        and record_times
        and not g.get("tasks_file")
        and _schedule.dag_wants_progress(reg, segments)
    )
    est = times_key = None
    context.seed_cmd_width(0)  # each run learns (or is seeded) afresh
    if predictable:
        times_key = _progress.chain_key(segments, sequential=sequential, jobs=jobs)
        est = _progress.estimate(_progress.load_runs(Path.cwd(), times_key))
        context.seed_cmd_width(_progress.load_cmd_width(Path.cwd(), times_key))
    if est is not None and not g.get("quiet") and not sys.stderr.isatty():
        # No TTY (CI, a pipe): the one-line version of the bar, up front.
        print(f"  {'eta':>4}  ~{_progress.fmt_secs(est.typical)}", file=sys.stderr)

    # Tri-state on the command line: `-k` forces keep-going, `--fail-fast` forces
    # fail-fast, neither leaves it to the invoked task's declared policy.
    cli_keep_going = True if g.get("keep_going") else None
    if g.get("fail_fast"):
        cli_keep_going = False

    # A mounted plugin's globals bind now — after every listing exit,
    # before anything runs — and freeze for the run; `.value` answers from
    # here. Released by the caller's finally, so an outside-a-run read goes
    # back to teaching.
    if (
        bad := _executor.bind_global_options(
            reg.contributions["globals"], globals_, config=cfg
        )
    ) is not None:
        return _refuse(json_mode, bad)
    start = time.perf_counter()
    try:
        results = _schedule.run_plan(
            reg,
            segments,
            sequential=sequential,
            keep_going=cli_keep_going,  # None = unspecified; run_plan scopes per node
            # A document run buffers exactly as --json does: stdout belongs
            # to the declared return value, so no task print may stream to it.
            capture=json_mode or bool(emitters),
            ctx_config=ctx_config,
            estimate=est,
            progress=progress_on,
            jobs=jobs,
        )
    except _split.ChainError as exc:  # e.g. passthrough with no *args
        return _refuse(json_mode, str(exc))
    total = time.perf_counter() - start

    if collect is not None:
        collect.extend(results)
    # The run report's moment: every row is in, nothing has printed. A
    # rewrite a hook makes through its result view is what gets reported.
    post_error = _executor.run_post_tasks(results, total, json_mode)
    if predictable and times_key and results and all(r.ok for r in results):
        # Green runs teach: the duration, and the step-alignment width.
        _progress.record(Path.cwd(), times_key, total, cmd_width=context.cmd_width())

    # After the post hooks: what a `set_returned` rewrite reported is what
    # the contract check reads, the same value the envelope carries.
    returns_meta = _check_returns(reg, results)

    if json_mode:
        _print_json(results, total=total, returns=returns_meta)
    else:
        if emitters:
            # The document run's receipts: everything that is not the
            # document — prints, run() lines — replays on stderr, where the
            # summary already lives, in dependency order.
            for r in results:
                if r.output:
                    sys.stderr.write(r.output)
            sys.stderr.flush()
        if not g.get("quiet"):
            _print_summary(
                results,
                timings=bool(g.get("timings")),
                verbose=bool(g.get("verbose")),
                total=total,
            )
        if emitters:
            doc_seg, doc_inner = emitters[0]
            doc_result = next((r for r in results if r.task == doc_seg.task), None)
            # A failed task emits nothing (the exit code talks), and a None
            # return means empty stdout: nothing to say, said nothing.
            if (
                doc_result is not None
                and doc_result.ok
                and doc_result.returned is not None
            ):
                _emit_document(doc_result.returned, doc_inner)

    # The exit code is the first genuine failure's — a cancelled task carries
    # only a kill signal and a skipped node only a cause, so those are the
    # fallback, never the headline.
    if post_error is not None:
        # A reporter that crashed must not pass silently: named, non-zero.
        print(f"{_brand.prog}: {post_error}", file=sys.stderr)
    # `retried` is recorded but is never the run's verdict — an attempt with
    # attempts left has not failed yet, so there is nothing for the exit code
    # to report. The terminal attempt carries the outcome, whichever way it
    # went. Filtered here, at the source, so the fallback below cannot pick a
    # retried row up either.
    failed = [r for r in results if not r.ok and r.state != "retried"]
    genuine = next(
        (r.code or 1 for r in failed if not r.cancelled and r.state != "skipped"),
        None,
    )

    def carriable(code: int) -> int:
        # This number's destiny is a process exit status, and POSIX keeps only
        # the low byte: a task's 256 would report *success* to the shell, so
        # `fm deploy || rollback` never rolls back. A failure the shell cannot
        # carry collapses to 1 — the failure survives, and the real number
        # stays on the receipt line and the `--json` row.
        return code if 0 < code < 256 else 1

    if genuine is not None:
        return carriable(genuine)
    code = next((r.code or 1 for r in failed), 0)
    if code:
        return carriable(code)
    return 1 if post_error is not None else 0


def run_group(
    root: registry.Group,
    argv: list[str],
    brand: Brand = DEFAULT_BRAND,
    collect: list[_executor.TaskResult] | None = None,
) -> int:
    """Drive an in-memory Group tree: globals, `--version`, manifest, run.

    The in-memory sibling of `_run`, minus discovery/cascade/config and the
    `-C`/`--install-completion` machinery those imply. No KeyboardInterrupt
    wrapper (D13): a test runner must let Ctrl-C reach pytest. This is the
    single shared surface `footman.testing.Runner` drives, so its Group mode
    can never drift from the real CLI's help/version/list/tree/json behaviour.
    """
    global _brand
    _brand = brand
    try:
        pre_globals, after = _split._parse_globals(argv, 0, lenient=True)
    except _split.ChainError as exc:
        return _refuse(_wants_json(argv), str(exc))
    g = _globals_to_dict(pre_globals)
    _set_colors(_resolve_color(g))

    if g.get("version"):
        return _print_version(bool(g.get("json")))

    # No config here (in-memory trees have no cascade), so the note-level
    # policy is the defaults and there is no docs-link template — installed
    # rather than inherited, or a prior embedded invocation's would leak.
    _notes.install_policy(None)
    global _docs_url
    _docs_url = None
    tree = _manifest.build_manifest(root)["tree"]
    # An in-memory tree still gets the per-task lifecycle — its hooks live on
    # the Group's own contributions. `pre_tasks` stays a discovery-time moment
    # (there is no cascade here), so the invocation arrives already frozen.
    inv = invocation.Invocation(cli=g, cwd=os.getcwd(), tasks=registry.Tasks(root))
    inv.freeze()
    if (
        clash := registry.validate_global_options(root.contributions["globals"])
    ) is not None:
        return _refuse(bool(g.get("json")), clash)
    for orphan in registry.orphan_global_options(root):
        _error(f"warning: {orphan}")
    _executor.install_lifecycle(inv, root.contributions)
    try:
        return _run_tree(
            root, tree, argv, {}, collect, g, pre_globals, after, record_times=False
        )
    finally:
        _executor.clear_lifecycle()
        registry.release_global_options(
            (*_split.CORE_LADDER, *root.contributions["globals"])
        )
