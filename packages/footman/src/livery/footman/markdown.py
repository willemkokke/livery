"""Render a task tree as markdown: one page, or a linked site of pages.

Pure functions over manifest tree nodes — the same dicts `--json --list`
emits — phrased through `footman._describe`, the module the `--help`
renderer uses, so pages and help can never drift.

Two flavors:

- `plain` (the default) — CommonMark and pipe tables only. Safe verbatim
  through `pymdownx.snippets` includes and pandoc → PDF/HTML.
- `material` — opts into the extensions a zensical / mkdocs-material site
  already enables: attr_list anchors on headings for stable deep links, and
  an `!!! example` admonition for the synthesized invocation.

`render_page` returns one document (headings start at *heading*, so a page
can nest under a host site's own structure). `render_site` returns
`{relative_path: content}` — one file per task, an `index.md` per group with
relative links — ready to write into a docs tree.
"""

from __future__ import annotations

import json
from typing import Any

from livery.footman import _describe

__all__ = [
    "branded",
    "config_table",
    "globals_table",
    "notes_table",
    "render_page",
    "render_site",
]


def branded(text: str, *, prog: str = "", config: str = "", env: str = "") -> str:
    """Fill the brand placeholders in generated help text.

    `{prog}` is the command someone types, `{config}` the `pyproject.toml`
    table and standalone filename this runner reads, `{env}` its
    environment-variable prefix. Empty arguments resolve from the
    configured brand, so the ordinary call needs none of them.

    This exists because a branded CLI documenting *footman's* table name
    is not a cosmetic slip: a runner reads `[tool.<its own stem>]` and
    nothing else, so telling an `acme` user to write `[tool.footman]`
    documents a table `acme` never opens — settings that silently do
    nothing. Entry-point names (`footman.tasks`, `footman.builtin`) are
    identifiers every brand's providers advertise under, so they carry no
    placeholder and must stay literal.
    """
    from livery.footman import _paths

    return (
        text.replace("{prog}", prog or _paths.prog())
        .replace("{config}", config or _paths.config_table())
        .replace("{env}", env or _paths.env_prefix())
    )


def config_table(*, prog: str = "", config: str = "", env: str = "") -> str:
    """This runner's config keys as a markdown pipe table.

    Rendered from `_config.KEYS`, the same list the runner recognises, so a
    docs page that regenerates this on each build can never describe a key
    set the runner doesn't have — nor miss one, which is how `cwd` stayed
    undocumented for four releases. The brand's own words fill the
    placeholders (`branded`), so a branded CLI's table names the table it
    actually reads.
    """
    from livery.footman import _config

    rows = [
        (
            f"`{name}`",
            values,
            default,
            _cell(branded(help_text, prog=prog, config=config, env=env)),
        )
        for name, values, default, help_text in _config.KEYS
    ]
    widths = [max(len(row[i]) for row in rows) for i in range(3)]
    head = ("key", "values", "default")
    # Annotated: the header rows are all literals, so inference narrows to
    # `list[LiteralString]` and the `+=` of real rows below stops type-checking.
    lines: list[str] = [
        "| "
        + " | ".join(h.ljust(w) for h, w in zip(head, widths, strict=True))
        + " | meaning |",
        "| " + " | ".join("-" * w for w in widths) + " | ------- |",
    ]
    lines += [
        "| "
        + " | ".join(c.ljust(w) for c, w in zip(row[:3], widths, strict=True))
        + f" | {row[3]} |"
        for row in rows
    ]
    return "\n".join(lines) + "\n"


def notes_table(*, prog: str = "", config: str = "") -> str:
    """The note kinds as a markdown pipe table.

    Rendered from `_notes.KINDS`, the same registry the runtime resolves
    levels from and the config validator refuses unknown kinds against, so
    a docs page that regenerates this on each build can never list a kind
    footman lacks — nor miss one, which is what lets a policy author trust
    the page.
    """
    from livery.footman import _notes

    rows = []
    for family, instance, default, help_text in _notes.KINDS:
        kind = f"`{family}:<{instance}>`" if instance else f"`{family}`"
        rows.append(
            (kind, f"`{default}`", _cell(branded(help_text, prog=prog, config=config)))
        )
    widths = [max(len(row[i]) for row in rows) for i in range(2)]
    head = ("kind", "default")
    lines: list[str] = [
        "| "
        + " | ".join(h.ljust(w) for h, w in zip(head, widths, strict=True))
        + " | fires when |",
        "| " + " | ".join("-" * w for w in widths) + " | ---------- |",
    ]
    lines += [
        "| "
        + " | ".join(c.ljust(w) for c, w in zip(row[:2], widths, strict=True))
        + f" | {row[2]} |"
        for row in rows
    ]
    return "\n".join(lines) + "\n"


def globals_table(*, prog: str = "fm") -> str:
    """The runner's global options as a markdown pipe table.

    Rendered straight from the CLI grammar (`_split.GLOBALS`) — the same
    rows, in the same order, with the same words `--help` prints — so a
    docs page that regenerates this on each build can never drift from the
    runner. *prog* fills the `{prog}` placeholders, so a branded CLI's docs
    speak its own name.
    """
    from livery.footman import _split

    rows = []
    for name, alias, _kind, hint, _default, help_text in _split.GLOBALS:
        # `=`-attached, exactly as `--help` prints it: a value is always
        # attached in this grammar, so notation that shows a space teaches
        # a command line the runner refuses.
        main = f"`{name}={hint}`" if hint else f"`{name}`"
        label = f"`{alias}`, {main}" if alias else main
        effect = help_text.replace("{prog}", prog)
        # The default comes from the grammar too, so the page cannot say one
        # thing while `--help` says another — the drift that hand-written
        # "(default: …)" prose in a help string always ended in. The suffix
        # is composed once, shared with `--help` itself.
        effect += _describe.global_default_suffix(name, code=True, live=False)
        rows.append((label, _cell(effect)))
    width = max(len(label) for label, _ in rows)
    lines = [
        f"| {'option':<{width}} | effect |",
        f"| {'-' * width} | ------ |",
    ]
    lines += [f"| {label:<{width}} | {effect} |" for label, effect in rows]
    return "\n".join(lines) + "\n"


def render_page(
    tree: dict[str, Any],
    *,
    path: tuple[str, ...] = (),
    heading: int = 1,
    flavor: str = "plain",
    prog: str = "fm",
) -> str:
    """One markdown document for the node at *path* (empty = whole tree)."""
    kind, node = _resolve(tree, path)
    if kind == "task":
        parts = _task_page(list(path), node, heading, flavor, prog)
    else:
        parts = _group_page(list(path), node, heading, flavor, prog)
    return "\n".join(parts).rstrip() + "\n"


def render_site(
    tree: dict[str, Any],
    *,
    path: tuple[str, ...] = (),
    flavor: str = "plain",
    prog: str = "fm",
) -> dict[str, str]:
    """A linked set of files for the node at *path*: `index.md` per group
    (name, help, a table of children with relative links), one file per task.
    Keys are POSIX-relative paths."""
    kind, node = _resolve(tree, path)
    if kind == "task":
        name = path[-1]
        page = render_page(tree, path=path, heading=1, flavor=flavor, prog=prog)
        return {f"{name}.md": page}
    files: dict[str, str] = {}
    _site_group(list(path), node, "", files, flavor, prog)
    return files


# --- resolution ---------------------------------------------------------------


def _resolve(tree: dict[str, Any], path: tuple[str, ...]) -> tuple[str, dict[str, Any]]:
    """Walk *path* to its node; ("task"|"group", node). Taught ValueError."""
    node = tree
    for i, name in enumerate(path):
        if name in node["groups"]:
            node = node["groups"][name]
        elif i == len(path) - 1 and name in node["tasks"]:
            return "task", node["tasks"][name]
        else:
            known = list(node["groups"]) + list(node["tasks"])
            where = ".".join(path[:i]) or "the root"
            raise ValueError(
                f"no task or group named {name!r} under {where} "
                f"(know: {', '.join(known) or 'nothing'})"
            )
    return "group", node


# --- one task -----------------------------------------------------------------


def _slug(path: list[str]) -> str:
    return "-".join(path)


def _cell(text: str) -> str:
    """Make *text* safe inside a pipe-table cell."""
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _h(level: int, text: str, path: list[str], flavor: str) -> str:
    anchor = f" {{ #{_slug(path)} }}" if flavor == "material" and path else ""
    return f"{'#' * min(level, 6)} {text}{anchor}"


def _type_cell(p: dict[str, Any]) -> str:
    if p["kind"] == "flag":
        return "flag"
    if p.get("mapping"):
        return "KEY=VALUE"
    bits: list[str] = []
    choices = p.get("choices")
    if choices:
        bits.append(" \\| ".join(f"`{c}`" for c in choices))
    elif p.get("types"):
        bits.append(" \\| ".join(p["types"]))
    if p.get("multiple"):  # a mapping returned early above
        bits.append("repeatable")
    if p["kind"] == "variadic":
        bits.append("variadic")
    return ", ".join(bits)


def _default_cell(p: dict[str, Any]) -> str:
    # A positional argument is required by kind (a default would have made it
    # an option — the grammar's load-bearing rule); flags/options say so.
    if p.get("required") or p["kind"] == "positional":
        return "*required*"
    if "default" in p and p["default"] is not None:
        return f"`{_cell(json.dumps(p['default']))}`"
    return ""


def _returns_section(task: dict[str, Any]) -> list[str]:
    """The output contract on a task page: the `Returns:` prose (or the
    declared shape's phrase), and a field table when the shape has fields."""
    returned = task.get("returned")
    doc = task.get("returned_doc", "")
    if returned is None and not doc:
        return []
    phrase = _describe.returns_phrase(returned) if returned is not None else ""
    if doc and phrase:
        head = f"**Returns:** {_cell(doc)} — {_cell(phrase)}"
    else:
        head = f"**Returns:** {_cell(doc or phrase)}"
    parts = [head, ""]
    if returned is not None and returned.get("fields"):
        parts += ["| Field | Type |", "| --- | --- |"]
        for name, fspec in returned["fields"].items():
            optional = "" if fspec.get("required", True) else " *(optional)*"
            parts.append(
                f"| `{name}` | {_cell(_describe.returns_phrase(fspec))}{optional} |"
            )
        parts.append("")
    return parts


def _task_page(
    path: list[str], task: dict[str, Any], level: int, flavor: str, prog: str
) -> list[str]:
    title = ".".join(path) or prog
    parts = [_h(level, title, path, flavor), ""]
    if task["help"]:
        parts += [task["help"], ""]
    if task.get("long"):
        parts += [task["long"], ""]
    if task.get("hidden"):
        # Documented, and marked: the docs are where you look a machine-facing
        # task up *because* the listings never offer it.
        parts += [
            "*Hidden: this task is not listed by "
            f"`{prog} --list`, `--tree` or completion — it runs like any other "
            "when named.*",
            "",
        ]
    if task.get("disabled"):
        parts += [f"*Unavailable here: {task['disabled']}*", ""]

    fragments = [f for p in task["params"] if (f := _describe.usage_fragment(p))]
    usage = " ".join([prog, ".".join(path), *fragments])
    parts += ["```text", usage, "```", ""]

    if task["params"]:
        parts += [
            "| Parameter | Type | Default | Description |",
            "| --- | --- | --- | --- |",
        ]
        for p in task["params"]:
            label = f"`{_describe.param_label(p)}`"
            doc = _cell(p.get("doc", ""))
            parts.append(f"| {label} | {_type_cell(p)} | {_default_cell(p)} | {doc} |")
        parts.append("")

    parts += _returns_section(task)

    invocation = _describe.example(path, task, prog)
    if flavor == "material":
        parts += [
            "!!! example",
            "",
            "    ```console",
            f"    $ {invocation}",
            "    ```",
            "",
        ]
    else:
        parts += [f"**Example:** `{invocation}`", ""]
    return parts


# --- one group, page mode -----------------------------------------------------


def _group_page(
    path: list[str], node: dict[str, Any], level: int, flavor: str, prog: str
) -> list[str]:
    title = ".".join(path) if path else f"{prog} tasks"
    parts = [_h(level, title, path, flavor), ""]
    if node.get("help"):
        parts += [node["help"], ""]
    for name, task in node["tasks"].items():
        parts += _task_page([*path, name], task, level + 1, flavor, prog)
    for name, sub in node["groups"].items():
        parts += _group_page([*path, name], sub, level + 1, flavor, prog)
    return parts


# --- site mode ----------------------------------------------------------------


def _site_group(
    path: list[str],
    node: dict[str, Any],
    prefix: str,
    files: dict[str, str],
    flavor: str,
    prog: str,
) -> None:
    title = ".".join(path) if path else f"{prog} tasks"
    parts = [_h(1, title, path, flavor), ""]
    if node.get("help"):
        parts += [node["help"], ""]
    rows = [
        (f"[`{name}`]({name}.md)", _describe.task_line(task))
        for name, task in node["tasks"].items()
    ]
    rows += [
        (f"[`{name}`]({name}/index.md)", sub.get("help", ""))
        for name, sub in node["groups"].items()
    ]
    if rows:
        parts += ["| Task | Description |", "| --- | --- |"]
        parts += [f"| {link} | {_cell(text)} |" for link, text in rows]
        parts.append("")
    files[f"{prefix}index.md"] = "\n".join(parts).rstrip() + "\n"

    for name, task in node["tasks"].items():
        page = _task_page([*path, name], task, 1, flavor, prog)
        files[f"{prefix}{name}.md"] = "\n".join(page).rstrip() + "\n"
    for name, sub in node["groups"].items():
        _site_group([*path, name], sub, f"{prefix}{name}/", files, flavor, prog)
