"""The package's docs generators: the pages the authored docs include.

Advertised as the ``footman.tasks`` entry point named
``livery.footman`` and loaded only through footman's own ``plugin()``.
``fm footman.pages`` regenerates, from the source of this package:

* the API reference, validated against the export table,
* the errors-and-notes reference page,
* the global-options, config-key, and note-kind tables the guide
  pages snippet-include,
* the rendered example page the taskdocs guide embeds,
* the latest-release admonition the home page includes.

The pty-recorded terminal shots and casts the original site carried
are not generated here yet; the pages that embed them say what the
image shows in their alt text.
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

from livery.footman import fail, group, run

# packages/footman, wherever the checkout lives: the generator runs at
# the workspace root, but anchoring on this file keeps it honest.
_PACKAGE = Path(__file__).resolve().parents[3]

footman_group = group("footman", help="footman's docs generators")


@footman_group.task(name="pages")
def docs_pages() -> None:
    """Write every generated docs page this package's authored pages use.

    Into ``docs/_generated`` (site content: the API and errors pages)
    and ``_generated`` (snippet sources the pages include). Idempotent:
    the same source rewrites the same bytes.
    """
    from livery.footman.tasks.docs import config as taskdocs_config
    from livery.footman.tasks.docs import errors as taskdocs_errors
    from livery.footman.tasks.docs import globals_ as taskdocs_globals
    from livery.footman.tasks.docs import notes as taskdocs_notes

    site_home = _PACKAGE / "docs" / "_generated"
    snippet_home = _PACKAGE / "_generated"
    site_home.mkdir(parents=True, exist_ok=True)
    snippet_home.mkdir(parents=True, exist_ok=True)

    (site_home / "api.md").write_text(_api_markdown(), encoding="utf-8")
    taskdocs_errors(out=site_home / "errors.md")
    taskdocs_globals(out=snippet_home / "globals.md")
    taskdocs_config(out=snippet_home / "config.md")
    taskdocs_notes(out=snippet_home / "notes.md")
    _write_tasks_page(snippet_home / "tasks-page.md")
    _write_latest_changes(snippet_home / "latest-changes.md")
    print(f"  footman docs pages into {site_home} and {snippet_home}")


def _write_tasks_page(out: Path) -> None:
    """Render the taskdocs guide's example page from the real docs family.

    The guide shows ``fm docs.page --target=docs`` over a project that
    mounted ``footman.docs``; this workspace mounts that family nowhere,
    so a child invocation with a one-line tasks file reproduces exactly
    the tree the example documents.
    """
    with tempfile.TemporaryDirectory(prefix="footman-docsgen-") as scratch:
        probe = Path(scratch) / "tasks.py"
        probe.write_text(
            'from livery.footman import plugin\n\nplugin("footman.docs")\n',
            encoding="utf-8",
        )
        # cwd is the scratch directory on purpose: the child must not
        # inherit this workspace's project config (its strict notes gate
        # would judge the example's own internals).
        done = run(
            [
                sys.executable,
                "-m",
                "livery.footman",
                f"--tasks-file={probe}",
                "docs.page",
                "--target=docs",
                "--all",
                "--heading=3",
                "--flavor=material",
                f"--out={out}",
            ],
            cwd=scratch,
            nofail=True,
        )
    if done != 0:
        fail(
            "footman.pages: the example render exited "
            f"{int(done)}:\n{done.stdout}\n{done.stderr}"
        )


# Either separator: Keep a Changelog spells the heading with a hyphen
# and this file used an em dash for its first forty-odd releases. The
# em-dash-only reading did not fail loudly when the convention changed,
# so the home page advertised a release from nine versions back and
# nothing said so. Accepting both keeps the archive parsable and cannot
# silently pick the wrong entry again.
_LATEST_HEADING = r"^## \[(\d[^\]]+)\] [-—] (.+?)$"


def _write_latest_changes(out: Path) -> None:
    """Extract the newest release's section from CHANGELOG.md into a
    collapsed admonition the home page includes: version, date, and the
    entries, straight from the one source of truth. Rolling the
    changelog for a release updates the home page by construction."""
    text = (_PACKAGE / "CHANGELOG.md").read_text(encoding="utf-8")
    head = re.search(_LATEST_HEADING, text, re.M)
    if head is None:  # a fresh fork with only [Unreleased]: skip quietly
        body_block = ""
    else:
        rest = text[head.end() :]
        nxt = re.search(r"^## \[", rest, re.M)
        entries = rest[: nxt.start() if nxt else len(rest)].strip()
        indented = "\n".join(
            f"    {line}" if line else "" for line in entries.splitlines()
        )
        title = f"Latest release: {head.group(1)} — {head.group(2)}"
        # No links in here: the file is validated as its own page, where
        # relative targets differ from the including page's. The home
        # page carries the changelog link itself, right after the
        # include.
        body_block = f'??? info "{title}"\n\n{indented}\n'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body_block, encoding="utf-8")


# --- The API reference page is generated: correct by construction --------
# Input: the typechecked TYPE_CHECKING export table in
# src/livery/footman/__init__.py (a wrong module path there fails
# basedpyright). Presentation: the declaration below. The build refuses
# on divergence in either direction, naming the name: a new export
# cannot ship undocumented, and a stale entry cannot outlive its
# export. Directives use PUBLIC paths (`::: livery.footman.run`), so
# anchors and the objects.inv inventory carry the contract spelling,
# not the defining module.

_API_INTRO = """\
<!-- Generated by 'fm footman.pages'; edit _API_SECTIONS in
     livery/footman/_docsgen.py, not this file. -->

# API reference

Auto-generated from the source via
[mkdocstrings](https://mkdocstrings.github.io/). Everything here is importable
straight from the `livery.footman` package
(`from livery.footman import task, run, App`).
"""

# (section title, intro prose or "", entries). An entry is a dotted path
# under `livery.footman.`; its first component must be a root export, or
# the whole entry sits in _API_EXTRA with a reason.
_API_SECTIONS: list[tuple[str, str, list[str]]] = [
    ("Defining tasks", "", ["task", "group", "Group", "expose"]),
    (
        "Availability gates",
        "Stack these above `@task` to list a task as unavailable (with a "
        "reason) where it can't run. Every availability gate is evaluated "
        "live, and all failures are collected.",
        ["requires", "requires_dep", "requires_tool", "requires_env"],
    ),
    (
        "Running commands",
        "",
        [
            "run",
            "Result",
            "ResultView",
            "AuditEntry",
            "Argv",
            "RunFailed",
            "parallel",
            "step",
            "pre_record",
            "passthrough",
            "inherited",
        ],
    ),
    ("Failing on purpose", "", ["fail", "Failed", "TimedOut"]),
    ("Progress", "", ["progress", "track"]),
    (
        "Profiling from inside a task",
        "",
        ["section", "mark", "stream", "Stream", "Section"],
    ),
    (
        "Asking the person running it",
        "",
        ["prompt", "confirm", "select", "attended", "tty", "colored"],
    ),
    (
        "The process boundary",
        "stdin binds to typed parameters, and a `Stdout[T]` return owns "
        "stdout. The full contract lives on [Pipelines](../pipelines.md) and "
        "[JSON output](../json.md).",
        ["Stdin", "stdin", "Stdout", "stdout"],
    ),
    (
        "The working directory & lanes",
        "",
        ["cwd", "chdir", "Lane", "lane", "cwd_lane", "console_lane"],
    ),
    (
        "Where a task keeps things",
        "Two folders footman writes, both created on access. `cache_dir()` "
        "is derived data the collector sweeps by age; `data_dir()` is "
        "durable and machine-local — credentials, tokens, generated assets "
        "— and is never collected. The rest are places footman *reads*, "
        "created by nobody but you: `config_dir()` and `config_file()` are "
        "your user-level settings, `user_tasks_file()` the personal rung "
        "riding every project, and `project_root()` the top of this "
        "invocation's cascade (`None` outside a project). `fm self.path` "
        "is the same set from the command line. Where each one lands is the "
        "CLI's business, not the task's; see "
        "[Custom CLIs](../custom-cli.md#two-folders-of-your-own).",
        [
            "cache_dir",
            "data_dir",
            "config_dir",
            "config_file",
            "user_tasks_file",
            "project_root",
        ],
    ),
    (
        "Which CLI is running",
        "For output that has to call the CLI back — a generated CI workflow, "
        "a README line — so a branded runner emits its own name instead of "
        "`fm`. `prog()` is the command someone types, `dist()` the package "
        "that ships it (`None` when the brand never declared one). Inside a "
        "task body `ctx.prog` says the same and is the better reach: it is "
        "the invocation's own answer rather than process state.",
        ["prog", "dist"],
    ),
    ("Fetching", "", ["fetch", "FetchError"]),
    (
        "The task context",
        "`given()` answers whether the caller supplied a parameter or footman "
        "filled it in — the difference between asking for the default and "
        "having no opinion, which the value alone cannot tell you.",
        ["Context", "given", "use_context"],
    ),
    ("Composing tasks", "", ["include", "plugin", "capture"]),
    (
        "The invocation, and editing the discovered tree",
        "`@pre_tasks` runs a hook once per invocation, over the fully-merged "
        "cascade and before anything else — see "
        "[Hooks & plugin options](../hooks.md#editing-the-discovered-tree). It is "
        "handed the `Invocation`, whose `tasks` is a `Tasks` view; iterating "
        "or indexing that yields a `TaskView` that reads and edits one task. "
        "The per-task pair — `@pre_task` and `@post_task` — runs around every "
        "execution; see "
        "[Around every task](../hooks.md#around-every-task-pre_task-and-post_task).",
        [
            "pre_tasks",
            "pre_bind",
            "pre_task",
            "post_task",
            "post_tasks",
            "GlobalOption",
            "config_section",
            "wrap_task",
            "wrap_bind",
            "Invocation",
            "Tasks",
            "TaskView",
        ],
    ),
    ("Custom CLI", "", ["App", "Brand", "main"]),
    (
        "Typed-parameter helpers",
        "",
        [
            "Many",
            "Arg",
            "Forward",
            "forward",
            "NoSplit",
            "nosplit",
            "Hidden",
            "hidden",
            "suggest",
            "exists",
            "isfile",
            "isdir",
            "Exists",
            "IsFile",
            "IsDir",
            "matching",
            "between",
            "env",
            "check",
            "default",
            "doc",
            "ask",
            "Secret",
        ],
    ),
    (
        "Docstrings",
        "Standalone (stdlib-only, no footman imports) — reusable outside footman.",
        ["docstrings.parse", "docstrings.Docstring"],
    ),
    (
        "Markdown export",
        "Pure functions over manifest tree nodes — see "
        "[Your tasks, documented](../taskdocs.md) for the task-level surface.",
        ["markdown.render_page", "markdown.render_site"],
    ),
    ("Testing", "", ["Runner", "testing.InvokeResult", "recording"]),
]

# Entries documented beyond the export table — each with its reason, never
# silent — and exports deliberately absent from the page, ditto.
_API_EXTRA: dict[str, str] = {
    "testing.InvokeResult": "Runner.invoke's return type",
}
_API_OMITTED: dict[str, str] = {}


def _api_markdown() -> str:
    """The API page content, validated against the export table.

    Pure, no filesystem writes, so a drift check can join the generated
    page into its blob by construction: a fresh checkout has no
    ``docs/_generated/api.md`` on disk until the docs build runs.
    """
    import ast

    # The module beside this one, not a checkout path: the release
    # legs run the suite against the installed copy, where src/ does
    # not exist.
    src = (Path(__file__).resolve().parent / "__init__.py").read_text("utf-8")
    exported: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.List | ast.Tuple)
            and any(getattr(t, "id", "") == "__all__" for t in node.targets)
        ):
            exported = {ast.literal_eval(e) for e in node.value.elts}
    exported -= {"__version__"}

    entries = [e for _, _, names in _API_SECTIONS for e in names]
    dupes = sorted({e for e in entries if entries.count(e) > 1})
    if dupes:
        fail(f"footman.pages: duplicated entries: {', '.join(dupes)}")
    covered = {e.split(".")[0] for e in entries}
    problems = []
    missing = sorted(exported - covered - set(_API_OMITTED))
    if missing:
        problems.append(
            f"exported but undocumented: {', '.join(missing)} — add each to "
            f"a section in _API_SECTIONS, or to _API_OMITTED with a reason"
        )
    stale = sorted(
        e for e in entries if e.split(".")[0] not in exported and e not in _API_EXTRA
    )
    if stale:
        problems.append(
            f"documented but not exported: {', '.join(stale)} — drop the "
            f"entry, or record it in _API_EXTRA with a reason"
        )
    ghosts = sorted(set(_API_OMITTED) & covered) + sorted(set(_API_OMITTED) - exported)
    if ghosts:
        problems.append(
            f"_API_OMITTED disagrees with the page or the exports: "
            f"{', '.join(dict.fromkeys(ghosts))}"
        )
    if problems:
        fail(
            "footman.pages: the page and the export table disagree — "
            + "; ".join(problems)
        )

    parts = [_API_INTRO]
    for title, intro, names in _API_SECTIONS:
        parts.append(f"\n## {title}\n")
        if intro:
            parts.append(f"\n{intro}\n")
        for name in names:
            parts.append(f"\n::: livery.footman.{name}\n")
    return "".join(parts)
