"""Docs-drift guards — the "audit, don't transcribe" approach.

Rather than generate prose, these tests fail the gate when the hand-written
docs fall behind the source: a new public symbol that nobody documented, or a
version pin/example that went stale after a release bump. They read the repo's
own files, so they only run meaningfully from a source checkout.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

import livery.footman as footman

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


def _handwritten_docs() -> list[Path]:
    return [
        p
        for p in DOCS.rglob("*.md")
        if "_generated" not in p.parts and "htmlcov" not in p.parts
    ]


def _assignment_docstrings() -> set[str]:
    """Names documented by the source convention IDEs read: an assignment
    (`isfile = …`, `Stdin = Annotated[…]`) followed by a bare string
    literal. Runtime-invisible, hover-visible — jedi and every editor
    surface them, so they count as documented."""
    import ast

    names: set[str] = set()
    for path in (ROOT / "src" / "livery" / "footman").glob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - the gate would be red first
            continue
        body = tree.body
        for i, node in enumerate(body[:-1]):
            targets: list[str] = []
            if isinstance(node, ast.Assign):
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets = [node.target.id]
            follower = body[i + 1]
            if (
                targets
                and isinstance(follower, ast.Expr)
                and isinstance(follower.value, ast.Constant)
                and isinstance(follower.value.value, str)
                and follower.value.value.strip()
            ):
                names.update(targets)
    return names


def test_every_public_symbol_carries_hover_documentation():
    """The docstring sibling of the docs-mention guard below: hover help is
    a public surface (editors, and the playground's tooltip), so every
    name footman exports must answer it — a runtime docstring, or the
    source-level assignment docstring the aliases and marker instances
    use. Public members that exported classes define in footman are held
    to the same bar."""
    import inspect

    source_documented = _assignment_docstrings()
    exported = [n for n in footman.__all__ if not n.startswith("__")]
    missing: list[str] = []
    for name in exported:
        obj = getattr(footman, name)
        runtime_doc = (inspect.getdoc(obj) or "").strip()
        if runtime_doc and not (inspect.isclass(obj) or inspect.isroutine(obj)):
            # An alias or instance inherits its type's docstring — typing
            # boilerplate for an `Annotated` alias — which is not this
            # name's documentation unless the type is footman's own.
            type_doc = (inspect.getdoc(type(obj)) or "").strip()
            type_module = getattr(type(obj), "__module__", "") or ""
            if runtime_doc == type_doc and not type_module.startswith("livery.footman"):
                runtime_doc = ""
        documented = bool(runtime_doc) or name in source_documented
        if not documented:
            missing.append(name)
            continue
        if inspect.isclass(obj):
            for attr, member in vars(obj).items():
                if attr.startswith("_"):
                    continue
                fn = inspect.unwrap(getattr(member, "fget", member))
                module = getattr(fn, "__module__", "") or ""
                if not module.startswith("livery.footman"):
                    continue
                if not callable(fn) and not isinstance(member, property):
                    continue
                if not (inspect.getdoc(member) or "").strip():
                    missing.append(f"{name}.{attr}")
    assert not missing, f"public symbols without hover documentation: {missing}"


def test_every_public_symbol_is_documented():
    """Every name re-exported from `footman` appears somewhere in the docs.
    Catches a new public export that shipped undocumented — the drift the
    reference cheatsheet hit with .opts()/forward/ask. The API page is
    generated (`fm footman.pages`), so its content joins the blob by
    construction rather than from disk — a fresh checkout has no
    docs/_generated/api.md until the docs build runs."""
    from livery.footman import _docsgen

    blob = "\n".join(p.read_text(encoding="utf-8") for p in _handwritten_docs())
    blob += "\n" + _docsgen._api_markdown()
    exported = [n for n in footman.__all__ if not n.startswith("__")]
    missing = [n for n in exported if not re.search(rf"\b{re.escape(n)}\b", blob)]
    assert not missing, f"public symbols undocumented in docs/: {missing}"


def _current_minor_pin() -> str:
    major, minor, *_ = footman.__version__.split(".")
    return f"footman~={major}.{minor}.0"


@pytest.mark.parametrize("rel", ["../README.md", "index.md", "stability.md"])
def test_minor_pin_example_tracks_the_release(rel):
    """The `pin the minor` example (README, docs home, stability page)
    tracks __version__, so it can't sit several minors stale after a bump."""
    text = (DOCS / rel).resolve().read_text(encoding="utf-8")
    pin = _current_minor_pin()
    assert pin in text, f"{rel}: expected the pin example {pin!r} to be current"


def test_json_version_example_is_current():
    """The --version JSON example on the JSON page tracks __version__."""
    text = (DOCS / "json.md").read_text(encoding="utf-8")
    assert f'"version": "{footman.__version__}"' in text


KINDS = frozenset(
    {
        "Added",
        "Changed",
        "Deprecated",
        "Removed",
        "Fixed",
        "Security",
        "Documentation",
    }
)
"""The kinds a release may sort its entries into.

Keep a Changelog's six, plus `Documentation`. A change to the pipeline is
a `Changed`: `CI` was a seventh kind for three entries in two releases of
July 2026, and nothing had used it since.
"""


def test_each_changelog_section_lists_a_kind_once():
    """A release's entries of one kind belong under one heading.

    Sessions append rather than look: `[Unreleased]` reached eight headings
    for four kinds — `Fixed` four times — and `[0.26.0]` shipped with two
    `Changed`. It costs nothing until release, when the runbook moves
    `[Unreleased]` wholesale into a version section, and whatever shape it
    is in is the shape that ships.
    """
    import collections
    import re

    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    for section in re.split(r"^## ", text, flags=re.M)[1:]:
        release = section.splitlines()[0].strip()
        kinds = [
            line[4:].strip() for line in section.splitlines() if line.startswith("### ")
        ]
        repeated = {k: n for k, n in collections.Counter(kinds).items() if n > 1}
        assert not repeated, (
            f"{release} lists {repeated} — merge them under one heading"
        )
        # One heading per kind is only half of it: two names for the same
        # kind divide a release just as effectively. `Docs` and
        # `Documentation` ran side by side for seven releases before
        # anybody noticed they were the same section.
        unknown = [k for k in kinds if k not in KINDS]
        assert not unknown, f"{release} uses {unknown}, not one of {sorted(KINDS)}"


def test_foundations_pages_teach_their_guards():
    """Every process-globals guard has a Foundations page teaching its
    ground. The mapping lives here (runtime error texts don't carry doc
    links yet — making them is a separate, user-visible design call); the
    assert is that each page and its load-bearing lesson exist, so a future
    link always has a stable target."""
    lessons = {
        "foundations-process.md": ["process global", "at spawn"],
        "foundations-cwd.md": ["footman.cwd()", "serial", "anchored"],
        "foundations-env.md": ["scope", "putenv", "ctx.env"],
        "foundations-shell.md": ["shell", "pipes"],
        "foundations-spawning.md": ["fork", "process group", "explicit arguments"],
        "foundations-threads.md": ["GIL", "serial lane"],
        "foundations-deadlocks.md": ["hold-and-wait", "boundary", "declared"],
        "foundations-regimes.md": ["declared", "interactive=True", "exclusive=True"],
    }
    nav = (DOCS / "nav.toml").read_text(encoding="utf-8")
    for name, needles in lessons.items():
        raw = (DOCS / name).read_text(encoding="utf-8")
        text = " ".join(raw.split())  # wrap-proof: prose reflows freely
        assert name in nav, f"{name} exists but is not in the nav"
        for needle in needles:
            assert needle in text, f"{name} lost its lesson: {needle!r}"


# --- the documented jq recipes ------------------------------------------------
# Prose about the JSON envelope is audited above; these run it. A recipe is a
# consumer like any other, and the one thing no reader of a doc can do is
# notice that the key it walks was renamed three commits ago.

_JQ = re.compile(r"jq\s+(?:-[a-zA-Z]+\s+)*'([^']+)'", re.S)


def _documented_jq() -> list[tuple[str, str]]:
    """`(file:line, program)` for every jq recipe in the hand-written docs."""
    found: list[tuple[str, str]] = []
    for path in sorted(_handwritten_docs()):
        text = path.read_text(encoding="utf-8")
        for match in _JQ.finditer(text):
            line = text[: match.start()].count("\n") + 1
            found.append((f"{path.name}:{line}", match.group(1)))
    return found


RECIPES = _documented_jq()

_ENVELOPE_TASKS = """
import livery.footman as footman
from livery.footman import task


@task
def green():
    "A task that shells out."
    footman.run("echo hello")


@task
def red():
    "A task that fails."
    footman.fail("boom", 3)
"""


@pytest.fixture
def envelope(tmp_path, monkeypatch, capsys) -> str:
    """A real `--json` envelope carrying all three row shapes a recipe walks:
    a task that passed, the step it ran, and a task that failed.

    Real rather than a checked-in fixture, because a fixture would have gone
    stale in exactly the way the recipes did.
    """
    from livery.footman import _app, _paths

    (tmp_path / "tasks.py").write_text(_ENVELOPE_TASKS, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    _app.run(["--json", "-k", "green", "red"])
    printed: str = capsys.readouterr().out  # capsys is Any-typed; name it once
    return printed


def _jq(program: str, text: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["jq", program], input=text, capture_output=True, text=True, check=False
    )


def test_the_docs_still_carry_jq_recipes():
    """The regex above is load-bearing: if it stops matching, every test
    below passes by finding nothing."""
    assert len(RECIPES) >= 5, f"only found {RECIPES}"


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is not installed")
@pytest.mark.parametrize(("where", "program"), RECIPES, ids=[w for w, _ in RECIPES])
def test_documented_jq_recipes_run_against_a_real_envelope(where, program, envelope):
    """Every documented recipe walks keys that exist.

    jq exits 2 on a usage problem, 3 on a compile error and 5 on a runtime
    one; `-e` recipes exit 1 to *mean* false, which is an answer rather than
    a failure. So the verdict is stderr — jq names what it could not do
    there — and the three error codes.

    This is the guard the `results` -> `items` rename went past: the docs
    and the refresh workflow both kept reading a key that no longer existed,
    and the first thing to notice was a scheduled job failing on a Monday.
    """
    done = _jq(program, envelope)
    assert not done.stderr, f"{where}: jq refused this recipe — {done.stderr.strip()}"
    assert done.returncode not in (2, 3, 5), f"{where}: jq exited {done.returncode}"


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is not installed")
def test_the_agent_gate_recipe_reports_only_real_failures(envelope):
    """agents.md's hook feeds an agent the tasks that failed, filtering on
    `select(.ok | not)`. The items envelope is one flat list, and a step row
    carries no `ok` at all — `null | not` is true — so without a
    `select(.task)` guard the recipe reports a task called `null`, on a gate
    that was green. Exit codes cannot catch that one: jq is perfectly happy.
    """
    program = next(p for where, p in RECIPES if where.startswith("agents.md"))
    done = _jq(program, envelope)
    assert not done.stderr
    assert "red" in done.stdout, "the task that really failed went unreported"
    assert "null" not in done.stdout, "a step row was reported as a failed task"


# --- the restructure's guards -------------------------------------------------
# Added with the docs restructure (notes/20260805-docs-restructure.md, ruling
# 23): each one fences a class of drift the 2026-08-05 review actually found.


def test_documented_envelope_walk_matches_reality(envelope):
    """The docs teach one Python walk over the envelope (testing.md's golden
    test): task rows discriminated by `"task" in row`, command rows by
    `"command" in row`. Run that walk against a real envelope — and assert
    the flat-model invariant whose violation shipped twice: no items row
    nests a `steps` key."""
    import json

    payload = json.loads(envelope)
    items = payload["items"]
    tasks = [(t["task"], t["ok"]) for t in items if "task" in t]
    commands = [s["command"] for s in items if "command" in s]
    assert ("green", True) in tasks and ("red", False) in tasks
    assert any("echo" in c for c in commands)
    nested = [row for row in items if "steps" in row]
    assert not nested, f"an items row nests 'steps' — the pre-0.28 shape: {nested}"
    blob = "\n".join(p.read_text(encoding="utf-8") for p in _handwritten_docs())
    assert 't["steps"]' not in blob, "a docs example walks the dead nested key"


def test_every_docs_page_is_in_the_nav():
    """A page in docs/ that the nav never mentions is published to nobody —
    color-support.md sat orphaned for a release before anyone noticed."""
    import tomllib

    config = tomllib.loads((DOCS / "nav.toml").read_text(encoding="utf-8"))

    def walk(node) -> set[str]:
        found: set[str] = set()
        if isinstance(node, str) and node.endswith(".md"):
            found.add(node)
        elif isinstance(node, list):
            for item in node:
                found |= walk(item)
        elif isinstance(node, dict):
            for value in node.values():
                found |= walk(value)
        return found

    nav = walk(config["nav"])
    orphans = [
        p.name
        for p in DOCS.glob("*.md")
        if p.name not in nav and not any(p.name in target for target in nav)
    ]
    assert not orphans, f"docs pages missing from the nav.toml nav: {orphans}"


def _slug(heading: str) -> str:
    """The site's heading ids, by python-markdown's own toc algorithm:
    punctuation is *dropped*, then whitespace and hyphens collapse to one
    hyphen. Underscores survive as word characters, which is why
    `#around-every-task-pre_task-and-post_task` is real.

    An earlier version hyphenated punctuation instead of dropping it and
    so demanded `#what-if-i-don-t-…` for a heading the site publishes as
    `#what-if-i-dont-…` — a guard inventing a dead link of its own.
    """
    text = re.sub(r"[^\w\s-]", "", heading.lower())
    return re.sub(r"[-\s]+", "-", text).strip("-")


def test_internal_links_and_anchors_resolve():
    """Every `[text](page.md#anchor)` in the hand-written docs points at a
    page that exists and a heading that page has. Clean when this guard
    landed; unguarded links rot silently."""
    link = re.compile(r"\]\(([\w.-]+\.md)(#[\w-]+)?\)")
    problems: list[str] = []
    for path in _handwritten_docs():
        text = path.read_text(encoding="utf-8")
        for match in link.finditer(text):
            target, anchor = match.group(1), match.group(2)
            target_path = path.parent / target
            if not target_path.exists():
                # The changelog and coverage pages are machine pages the
                # site build writes beside the authored ones; existence is
                # the strict docs build's assertion.
                if target in ("changelog.md", "coverage.md"):
                    continue
                problems.append(f"{path.name}: {target} does not exist")
                continue
            if anchor:
                headings = re.findall(
                    r"^#+ (.+)$", target_path.read_text(encoding="utf-8"), re.M
                )
                slugs = {_slug(h) for h in headings}
                if _slug(anchor[1:]) not in slugs:
                    problems.append(f"{path.name}: {target}{anchor} has no heading")
    assert not problems, "dead internal links:\n" + "\n".join(problems)


def test_every_config_key_is_live_and_documented():
    """`_config.KEYS` is the documented key set, and the docs table renders
    from it — so the interesting drift is no longer "documented?" but
    "real?": a key listed here that the runner never reads again.

    The earlier version of this guard read a prose list in the module
    docstring, which is why it never noticed `cwd`: the key was read by
    `_app.py` and had simply never been written down. Reading the source
    for each name closes that direction.
    """
    from livery.footman import _config

    assert _config.KEYS, "_config.KEYS went empty — update me"
    src = "\n".join(
        p.read_text(encoding="utf-8")
        for p in (ROOT / "src" / "livery" / "footman").glob("*.py")
    )
    # A dotted key is a sub-table: `fetch.backend` is read as `fetch` then
    # `backend`, so check the leaf — the part a `.get()` actually names.
    dead = [name for name, *_ in _config.KEYS if f'"{name.split(".")[-1]}"' not in src]
    assert not dead, f"_config.KEYS lists keys nothing reads: {dead}"
    # The page must actually mount the generated table in; a hand-written
    # copy would drift the moment a key changed.
    page = (DOCS / "configuration.md").read_text(encoding="utf-8")
    assert '--8<-- "packages/footman/_generated/config.md"' in page, (
        "configuration.md stopped including the generated key table"
    )


def test_refusal_exit_code_claims_track_the_constant():
    """Hand-written claims about the refusal exit code follow `EX_USAGE`.
    The pre-0.21 '2' survived on three pages for eleven releases because
    nothing tied the prose to the constant."""
    from livery.footman._executor import EX_USAGE

    blob = " ".join(
        " ".join(p.read_text(encoding="utf-8").split()) for p in _handwritten_docs()
    )
    assert "2 always means footman refused" not in blob
    assert "Exit code 2, nothing executed" not in blob
    assert f"{EX_USAGE} always means footman refused" in blob, (
        "troubleshooting.md no longer states the refusal code plainly"
    )


def test_glossary_inflections_share_their_definition():
    """The abbreviations glossary carries inflected keys (chain/chains/
    chained/…) so tooltips fire on every spelling; the copies must stay
    textually identical to their stem's definition."""
    lines = (DOCS / "includes" / "abbreviations.md").read_text(encoding="utf-8")
    entries = re.findall(r"^\*\[(.+?)\]: (.+)$", lines, re.M)
    assert entries, "the glossary went empty or changed format — update me"
    by_stem: dict[str, set[str]] = {}
    for key, definition in entries:
        by_stem.setdefault(key.lower()[:4], set()).add(definition)
    diverged = {stem: defs for stem, defs in by_stem.items() if len(defs) > 1}
    assert not diverged, f"glossary inflections diverged: {sorted(diverged)}"
    defs = dict(entries)
    # `unshared` is not an inflection of `shared`, it is its negation, so the
    # two definitions must differ. This assertion used to demand they match,
    # which pinned a copy-paste in place: the tooltip on `unshared` read out
    # the definition of `shared`, telling the reader the opposite of the word
    # they had hovered.
    assert defs["shared"] != defs["unshared"], (
        "unshared carries shared's definition — a negation cannot share the "
        "wording of the thing it negates"
    )


def test_our_docstrings_stay_google():
    """footman's own docstrings never use Sphinx fields or RST directives —
    the parser (docstrings.py) legitimately contains them as data, and is
    the one exemption. The rendered API page parses Google style, so a
    violation would render as broken prose."""
    rst = re.compile(r":param |:rtype:|:returns:|^\.\. \w+::", re.M)
    offenders = [
        p.name
        for p in sorted((ROOT / "src" / "livery" / "footman").glob("*.py"))
        if p.name != "docstrings.py" and rst.search(p.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"Sphinx/RST markup in our own source: {offenders}"


def _value_globals(*, mandatory_only: bool) -> list[str]:
    """Value-taking globals, read from GLOBALS itself so the guard below
    tracks the grammar rather than a list someone maintains.

    Every one of them prints `=`-attached in `--help`, optional values
    included (`--describe=[ADDR]`), so notation is held to that
    everywhere. *mandatory_only* narrows to the ones whose next token can
    only ever be their value — `fm --describe lint` is genuinely
    ambiguous, `fm --where lint` is not."""
    from livery.footman._split import GLOBALS

    names: list[str] = []
    for long, short, kind, metavar, _default, _help in GLOBALS:
        if kind != "option" or not metavar:
            continue
        if mandatory_only and metavar.startswith("["):
            continue
        names.append(long)
        if short:
            names.append(short)
    return names


ATTACHED = _value_globals(mandatory_only=True)
_OPT = "|".join(map(re.escape, ATTACHED))
_ANY_OPT = "|".join(map(re.escape, _value_globals(mandatory_only=False)))
# What a *value* looks like, positively: metavar notation (`N`, `PATH`), a
# number, an <angled> placeholder, or a dotted filename. Stated as an
# allowlist rather than "any token" because the docs also quote footman's
# own diagnostics — `--where expects a value, attached: …` — where the
# next word is English prose about the option, not a value for it.
_VALUE = r"(?:[A-Z]+(?![a-z])|\[[A-Z]+\]|\d|<[^>\n]+>|[\w-]+\.[\w]+)"
# Short options attach without a separator too — `-j2` is refused just as
# `-j 2` is. Only a digit counts: the docs also name other tools' glued
# flags (Clang's `-ftime-trace`), and those are none of our business.
_SHORTS = "|".join(re.escape(s) for s in ATTACHED if len(s) == 2)
_SPACE_FORM = re.compile(
    # inside an `fm …` line any following token is the option's value —
    # a quoted diagnostic never opens with `fm`:
    rf"(?:\$ |`)(?:fm|footman)\b[^`\n]*?\s(?:{_OPT})\s+[^\s=`|]"
    # in a bare option span — `-j/--jobs N` writes the short form first —
    # only value-shaped tokens count:
    rf"|`(?:-\w/)?(?:{_ANY_OPT})\s+{_VALUE}"
    # and a short option glued to a numeric value:
    rf"|`(?:{_SHORTS})\d"
)


def test_the_attached_value_detector_works():
    """The regex is load-bearing: if it stops matching, the guard below
    passes by finding nothing."""
    assert ATTACHED, "GLOBALS stopped declaring mandatory-value options"
    assert _SPACE_FORM.search("`fm -j 2 check`"), "detector missed a known-bad line"
    assert _SPACE_FORM.search("$ fm --where mytask"), "detector missed a console line"
    # The two that actually shipped, in the shapes they shipped in:
    assert _SPACE_FORM.search("failures, `-j 2` caps the width."), (
        "detector missed the bare backticked option span"
    )
    assert _SPACE_FORM.search("- source: `fm --where <task>` prints file:line."), (
        "detector missed an <angled> placeholder value"
    )
    assert _SPACE_FORM.search("<kbd>Tab</kbd> after `-f <file>` completes"), (
        "detector missed an <angled> value in a bare span"
    )
    assert _SPACE_FORM.search("a typo like `--config prod.tmol` is reported"), (
        "detector missed a dotted-filename value"
    )
    assert _SPACE_FORM.search("keys on it, so `-j2` runs learn"), (
        "detector missed a short option glued to its value"
    )
    # Metavar notation is held to the same rule, because `--help` prints it
    # attached: a page showing `--jobs N` teaches a refused spelling.
    assert _SPACE_FORM.search("- `-j/--jobs N` caps the width")
    assert _SPACE_FORM.search("`--where TASK` prints a bare file:line")
    assert _SPACE_FORM.search("    `-f/--tasks-file PATH` loads a single file")
    assert _SPACE_FORM.search("`--describe [ADDR]` answers for one task")
    # Quoted diagnostics are prose *about* an option, not invocations of it:
    assert not _SPACE_FORM.search(
        "| `--where expects a value, attached: --where=TASK` | given bare |"
    )
    assert not _SPACE_FORM.search("`--jobs (from $JOBS) must be between 1 and 32`")
    # The good spellings, including alternation notation:
    assert not _SPACE_FORM.search("`fm -j=2 check`"), "detector flags the good form"
    assert not _SPACE_FORM.search("- `-j/--jobs=N` caps the width")
    assert not _SPACE_FORM.search("`-f/--tasks-file=PATH` loads a single file")
    assert not _SPACE_FORM.search("`--describe=[ADDR]` answers for one task")


def test_documented_invocations_attach_their_values():
    """No documented `fm …` line passes a mandatory value across a space.

    footman refuses that form — exit 64, `-j takes its value attached — did
    you mean -j=2?` — so an example spelling it teaches a command line the
    reader cannot run. Four shipped this way (`-j 2` in the cookbook,
    `fm --where <task>` in agents.md, `-f <file>` in monorepos.md,
    `--config prod.tmol` in troubleshooting.md); nothing tied the examples
    to the grammar until this guard. Deliberately conservative on bare
    option spans, because the docs also quote footman's own diagnostics:
    only a number, an <angled> placeholder or a dotted filename counts
    there, so a lowercase prose word after an option is left alone.
    """
    problems: list[str] = []
    for path in sorted(_handwritten_docs()):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = _SPACE_FORM.search(line)
            if match:
                problems.append(f"{path.name}:{number}: {match.group(0).strip()}")
    assert not problems, (
        "documented invocations using the refused space form "
        "(values are always `=`-attached):\n" + "\n".join(problems)
    )


def test_the_newest_changelog_entries_carry_no_relative_links():
    """`_write_latest_changes` lifts the newest release's entries into
    `_generated/latest-changes.md`, which the home page includes but
    which is *validated as its own page* — so a relative `typing.md#…`
    resolves from `_generated/` and does not exist.

    The generator says so in a comment, and nothing enforced it. The trap is
    the timing: an entry can sit under `[Unreleased]` looking fine for weeks,
    because the generator only ever lifts a *released* section — and then the
    release runbook renames that heading and the strict docs build fails,
    mid-release, on prose written long before. Use the full site URL.
    """
    import re

    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    # Both sections, not one: the generator lifts the newest *released*
    # section, and `[Unreleased]` is where the offending entry is written
    # weeks earlier. Checking only the first would have passed here — an
    # empty `[Unreleased]` sits above a freshly rolled release.
    sections = re.split(r"^## \[", text, flags=re.M)[1:3]
    relative = [
        link
        for section in sections
        for link in re.findall(r"\]\((?!https?:)([^)]*\.md[^)]*)\)", section)
    ]
    assert not relative, (
        f"the newest changelog entries link relatively to {relative} — these "
        f"break the generated latest-changes page, which is validated on its "
        f"own. Use https://willemkokke.github.io/footman/<page>/#<anchor>."
    )


def test_the_home_page_advertises_the_current_release():
    """The generated latest-release block names *this* version.

    The one check that reads the real CHANGELOG rather than a fixture, and
    the reason it exists: the generator matched only an em dash between
    version and date, the file moved to the hyphen Keep a Changelog
    specifies, and `search` quietly found the newest heading that still
    matched. The home page advertised a release nine versions old for two
    months. The unit test passed throughout, because its fixture used the
    generator's own spelling — a synthetic changelog cannot catch the
    generator disagreeing with the real one.
    """
    import re

    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    newest = re.search(r"^## \[(\d[^\]]+)\]", text, re.M)
    assert newest is not None, "no released section in CHANGELOG.md"

    from livery.footman import _docsgen

    lifted = re.search(_docsgen._LATEST_HEADING, text, re.M)
    assert lifted is not None, (
        "the latest-changes generator matched no heading in CHANGELOG.md — "
        "the home page would show nothing"
    )
    assert lifted.group(1) == newest.group(1), (
        f"the home page would advertise {lifted.group(1)}, but the newest "
        f"release is {newest.group(1)} — the generator's heading pattern no "
        f"longer matches how releases are written"
    )


def test_every_boolean_config_key_has_both_cli_spellings():
    """A project setting is a *default*, never a one-way door.

    Five keys were settable in one direction only, so config could turn
    something on (or off) with nothing to countermand it for a single run — the
    `progress` key documented itself as disabling the bar *permanently*, which
    was accurate and should not have been. Derived from both tables rather than
    a list someone maintains, because the same gap opened five separate times.
    """
    from livery.footman._config import KEYS, USER_LEVEL_KEYS
    from livery.footman._split import GLOBALS

    # User-level keys are exempt: they govern machine-wide behaviour (the cache
    # collector sweeps one cache for every project), so "for this invocation"
    # is not a thing they can mean. This guard is about *project* policy.
    flags = {name for name, _, kind, _, _, _ in GLOBALS if kind == "flag"}
    missing = [
        spelling
        for name, values, _default, _help in KEYS
        if values == "`true` / `false`" and name not in USER_LEVEL_KEYS
        for spelling in (f"--{name}", f"--no-{name}")
        if spelling not in flags
    ]
    assert not missing, (
        f"boolean config keys with no way to countermand them from the command "
        f"line: {missing}. Declare the option paired in _split (both spellings "
        f"derive from one declaration), so config stays a default."
    )


def test_the_keys_table_agrees_with_the_declarations():
    """The mechanical half of a config-backed KEYS row — that the key exists,
    a bool's values and default, a choice-typed option's choices — comes from
    the declaration, and the table may not disagree.

    Derivation proper was tried twice and shrank on contact both times: six
    keys have no CLI surface at all, and a computed default (`cores - 1`) has
    no machine-independent rendering a docs page could bake. So the prose
    stays editorial — the reference page explains more than a flag's
    one-liner can — and this guard pins exactly the agreement that IS
    derivable."""
    from livery.footman._config import KEYS
    from livery.footman._split import CORE_OPTIONS

    rows = {name: (values, default) for name, values, default, _ in KEYS}
    backed = {o.name: o for o in CORE_OPTIONS if o.config}
    missing = sorted(set(backed) - set(rows))
    assert not missing, f"config-backed options absent from the KEYS table: {missing}"
    for name, opt in backed.items():
        values, default = rows[name]
        if opt.annotation is bool:
            assert values == "`true` / `false`", (name, values)
            assert default == f"`{'true' if opt.default else 'false'}`", (
                name,
                default,
            )
        elif opt.choices:
            assert values == " / ".join(f"`{c}`" for c in opt.choices), (name, values)


def test_every_config_backed_option_resolves_a_documented_key():
    """A `config=True` declaration reads `[tool.footman] <name>`, so every
    name declared config-backed has to be a documented key.

    The `KEYS` table exists because `cwd` went undocumented for four releases.
    It is read *by the docs*, and nothing read it against the code — which is
    how a live key could go unlisted all over again.
    """
    from livery.footman._config import KEYS
    from livery.footman._split import CORE_OPTIONS

    used = {o.name for o in CORE_OPTIONS if o.config}
    documented = {name for name, *_ in KEYS}
    assert used, "no core declaration is config-backed — the guard is inert"
    assert used <= documented, (
        f"resolved from config but absent from the KEYS table, so absent from "
        f"the reference page: {sorted(used - documented)}"
    )


# --- the completion-import claim ----------------------------------------------
# "A keystroke never imports your code" is true of the process that answers the
# <kbd>Tab</kbd> and false of the cache behind it: a cold directory builds the
# manifest by importing your tasks in a detached subprocess. test_complete.py's
# test_cold_cache_builds_and_serves pins that it does — a cold press serves task
# names nothing but an import could know. The claim is a headline, so it is
# repeated across surfaces, and every copy has to carry the caveat beside it.

_IMPORT_CLAIM = re.compile(r"(?:without|never) import(?:ing|s) your code")
_COLD_CAVEAT = "detached subprocess"
_CLAIM_WINDOW = 500

CLAIM_SURFACES = ["../README.md", "index.md", "design.md"]


@pytest.mark.parametrize("rel", CLAIM_SURFACES)
def test_the_no_import_claim_names_the_cold_build(rel):
    """No surface states the no-import claim without naming the build that
    does import. Checked in a window rather than per file, because a caveat
    three sections away from the headline is not a caveat."""
    text = " ".join((DOCS / rel).resolve().read_text(encoding="utf-8").split())
    found = list(_IMPORT_CLAIM.finditer(text))
    assert found, f"{rel}: the claim is gone entirely — retarget or drop me"
    for match in found:
        window = text[match.start() : match.end() + _CLAIM_WINDOW]
        assert _COLD_CAVEAT in window, (
            f"{rel}: {match.group(0)!r} stands unqualified. A cold directory "
            f"builds the manifest by importing your tasks — name the "
            f"{_COLD_CAVEAT!r} that does it, the way docs/completion.md does."
        )


def test_the_threat_model_allows_the_completion_build():
    """The security policy may not classify footman's own documented,
    tested behaviour as a hole.

    The in-scope list named "a completion path that executes something",
    which is precisely what a cold <kbd>Tab</kbd> is: it spawns a build that
    imports your `tasks.py`. So the policy invited a report for the feature
    — while the not-a-vulnerability list, which explains that running a
    task file *is* the product, never mentioned completion at all.
    """
    text = " ".join((ROOT / "SECURITY.md").read_text(encoding="utf-8").split())
    intended, marker, in_scope = text.partition("These **do** count")
    assert marker and in_scope, "SECURITY.md changed shape — update me"
    for needle in ("Completion importing your task files", "same risk posture"):
        assert needle in intended, (
            f"SECURITY.md no longer explains the completion build as intended "
            f"behaviour (missing {needle!r}), so the policy reads as though a "
            f"cold Tab importing your tasks were a vulnerability."
        )
    assert "a completion path that executes something" not in in_scope, (
        "SECURITY.md solicits reports for 'a completion path that executes "
        "something' — the cold-cache build is exactly that, by design."
    )
