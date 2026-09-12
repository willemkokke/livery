"""Docs examples run — the page-as-session harness.

Every ```python block in the hand-written docs is real code. Blocks on a
page execute in order in one shared namespace, like a doctest session: the
first block carries the imports, later blocks stay minimal and may build on
earlier definitions. Each block runs inside a fresh `registry.capture()` so
a recipe page may redefine a task name, and under an ambient `recording()`
so a top-level `run()`/`tools.*` illustration records instead of executing.

Two tiers:

- `test_page_examples_run` executes the session: a missing import, a stale
  API spelling, an invalid signature, or a marker misuse fails at
  decoration time with the page's own line numbers in the traceback.
- `test_page_examples_resolve_names` stitches the session into one module
  (each line at its real .md line number) and runs pyflakes' undefined-name
  check over it via ruff — task *bodies* never execute above, so this is
  the tier that catches a body using a name the page never defined.

A block that is deliberately an illustration — a stub excerpt, another
runner's API, a module that only exists in prose — opts out with an HTML
comment on the line above its fence:

    <!-- example: fragment -->

A self-contained block that starts a new lesson mid-way restarts the
session (and its namespace) with:

    <!-- example: fresh-session -->

The block carries its own imports and may reuse an earlier task name; a
run-it-there link built from the page starts accumulating there.

A block that *revises* an earlier definition in place — the same task
shown again with one feature changed, leaning on the page's context —
is marked:

    <!-- example: revision -->

It still executes in the page session (fresh capture per block, so the
redefinition is fine) and is still stitched for the name check, but no
run-it-there link carries it: concatenated into one file it would be a
duplicate task name, which footman refuses.

All three markers are invisible on the rendered page and greppable in
the source.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from livery.footman import recording, registry

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

FRAGMENT = "<!-- example: fragment -->"
FRESH = "<!-- example: fresh-session -->"
REVISION = "<!-- example: revision -->"
_OPEN = re.compile(r"^(?P<indent>[ ]*)```python\s*$")


@dataclass
class Block:
    line: int  # 1-based line of the first code line
    code: str  # dedented source
    fresh: bool = False  # the session restarts at this block
    linked: bool = True  # False: a revision — runs in-session, never in a link


def _handwritten_docs() -> list[Path]:
    return sorted(
        p
        for p in DOCS.rglob("*.md")
        if "_generated" not in p.parts and "htmlcov" not in p.parts
    )


def extract_blocks(page: Path) -> list[Block]:
    """The ```python fences of a page, dedented, fragments dropped."""
    lines = page.read_text(encoding="utf-8").splitlines()
    blocks: list[Block] = []
    i = 0
    while i < len(lines):
        m = _OPEN.match(lines[i])
        if not m:
            i += 1
            continue
        indent = m.group("indent")
        prose = next((ln for ln in reversed(lines[:i]) if ln.strip()), "")
        body: list[str] = []
        i += 1
        first = i + 1
        while i < len(lines) and lines[i].strip() != "```":
            body.append(lines[i].removeprefix(indent))
            i += 1
        i += 1  # past the closing fence
        marker = prose.strip()
        if marker != FRAGMENT:
            blocks.append(
                Block(
                    first,
                    "\n".join(body),
                    fresh=marker == FRESH,
                    linked=marker != REVISION,
                )
            )
    return blocks


def sessions(page: Path) -> list[list[Block]]:
    """The page's blocks grouped into sessions — a fresh marker starts one."""
    out: list[list[Block]] = []
    for block in extract_blocks(page):
        if block.fresh or not out:
            out.append([])
        out[-1].append(block)
    return out


def stitch(blocks: list[Block]) -> str:
    """One session as one module, each line at its real .md line."""
    lines: list[str] = []
    for block in blocks:
        lines.extend([""] * (block.line - 1 - len(lines)))
        lines.extend(block.code.splitlines())
    return "\n".join(lines) + "\n"


PAGES = [p for p in _handwritten_docs() if "```python" in p.read_text(encoding="utf-8")]


def test_the_example_collections_are_not_empty():
    # Six parametrized cases loop over these collections; empty, every one
    # of them evaporates into silent green (audit, suite pass) — a moved
    # docs/ directory or a broken glob would read as "all examples pass".
    # The floors are deliberately far below reality (a fifty-page site,
    # twenty-odd with runnable python), so only a collapse trips them.
    assert len(_handwritten_docs()) >= 30
    assert len(PAGES) >= 10


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_page_examples_run(page: Path):
    """Execute the page's blocks in order, one namespace per session."""
    for n, session in enumerate(sessions(page)):
        # A real module in sys.modules, not a bare dict: stdlib machinery
        # (dataclasses resolving string annotations, typing's eval_str)
        # looks the defining module up by name.
        module = types.ModuleType(f"docs_{page.stem.replace('-', '_')}_{n}")
        sys.modules[module.__name__] = module
        try:
            for block in session:
                # Pad so tracebacks and SyntaxErrors point at the .md line.
                padded = "\n" * (block.line - 1) + block.code
                code = compile(padded, str(page.relative_to(ROOT)), "exec")
                with registry.capture(), recording():
                    exec(code, module.__dict__)
        finally:
            del sys.modules[module.__name__]


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_run_link_sessions_load(page: Path):
    """Every run-it-there link loads: each cumulative prefix of a session —
    exactly what a link hands the playground — execs under ONE capture, so
    a task name redefined without a fresh-session or revision marker (the
    playground would refuse the duplicate) fails here, with the block's
    .md line. Revisions never ride a link, so they are skipped here."""
    for full in sessions(page):
        session = [b for b in full if b.linked]
        for end in range(1, len(session) + 1):
            module = types.ModuleType(f"docs_link_{page.stem.replace('-', '_')}")
            sys.modules[module.__name__] = module
            try:
                with registry.capture(), recording():
                    for block in session[:end]:
                        padded = "\n" * (block.line - 1) + block.code
                        code = compile(padded, str(page.relative_to(ROOT)), "exec")
                        exec(code, module.__dict__)
            finally:
                del sys.modules[module.__name__]


def test_page_examples_resolve_names(tmp_path: Path):
    """Every name a page's session uses — in bodies too — is defined by the
    page itself. One ruff F821 pass over the stitched sessions."""
    ruff = shutil.which("ruff")
    if ruff is None:  # pragma: no cover - always present under `uv run`
        pytest.skip("ruff not on PATH")
    for page in PAGES:
        for n, session in enumerate(sessions(page)):
            (tmp_path / f"{page.stem}__{n}.py").write_text(
                stitch(session), encoding="utf-8"
            )
    out = subprocess.run(
        [
            ruff,
            "check",
            "--select",
            "F821",
            "--no-cache",
            "--isolated",
            "--output-format",
            "json",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    undefined = [
        f"{Path(d['filename']).stem.rsplit('__', 1)[0]}.md:"
        f"{d['location']['row']}: {d['message']}"
        for d in json.loads(out.stdout or "[]")
    ]
    assert not undefined, "docs examples use undefined names:\n" + "\n".join(undefined)


def test_playground_module_evaluates(tmp_path: Path):
    """docs/assets/playground.js is a browser ES module built around large
    template literals holding Python source. A stray backtick or `${…}`
    inside one is invisible to every Python-side rehearsal — the Python
    extracts and runs fine — but terminates the literal in JS and kills
    the whole module at load: no run links on any page, a dead playground.
    Evaluate the module the way a browser import does, minimal DOM stubbed.
    """
    # bun is the repo's JS runtime; node is the fallback because GitHub
    # runners preinstall it (and not bun), so the guard still runs in CI.
    runtime = shutil.which("bun") or shutil.which("node")
    if runtime is None:  # pragma: no cover - node is present on every CI runner
        pytest.skip("no JS runtime (bun or node) on PATH")
    probe = tmp_path / "probe.mjs"
    probe.write_text(
        'import { pathToFileURL } from "node:url";\n'
        "globalThis.window ="
        " { document$: { subscribe(fn) { globalThis.cb = fn; } } };\n"
        "globalThis.document = { getElementById: () => null,"
        " querySelector: () => null, addEventListener: () => {},"
        ' readyState: "complete" };\n'
        "globalThis.Node = { TEXT_NODE: 3, COMMENT_NODE: 8 };\n"
        "await import(pathToFileURL(process.argv[2]));\n"
        "globalThis.cb();\n",
        encoding="utf-8",
    )
    out = subprocess.run(
        [runtime, str(probe), str(DOCS / "assets" / "playground.js")],
        capture_output=True,
        text=True,
        # Generous on purpose: a loaded Windows CI runner has taken >60s to
        # cold-start node and evaluate the module (the #295 merge-run flake).
        timeout=120,
        check=False,
    )
    assert out.returncode == 0, out.stderr


def _js_source() -> str:
    return (DOCS / "assets" / "playground.js").read_text(encoding="utf-8")


def _jedi_cache() -> str:
    """One parso cache per test process, warm across that process's probes."""
    cache = Path(tempfile.gettempdir()) / f"fm-playground-jedi-{os.getpid()}"
    cache.mkdir(exist_ok=True)
    return str(cache)


def _js_bootstrap() -> str:
    """The driver: the text of the top-level `const BOOTSTRAP = \\`…\\`;`."""
    match = re.search(r"^const BOOTSTRAP = `(.*?)`;$", _js_source(), re.S | re.M)
    assert match, "playground.js no longer defines BOOTSTRAP"
    return match.group(1)


def _js_default_files() -> dict[str, str]:
    """The editor's opening tabs: `const DEFAULT_FILES = {"name": \\`…\\`, …}`."""
    match = re.search(r"^const DEFAULT_FILES = \{(.*?)^\};$", _js_source(), re.S | re.M)
    assert match, "playground.js no longer defines DEFAULT_FILES"
    return dict(re.findall(r'"([^"]+)": `(.*?)`,', match.group(1), re.S))


def _playground_invoke(
    tmp_path: Path,
    line: str,
    files: dict[str, str] | None = None,
    prompts: list[str] | None = None,
) -> tuple[int, str]:
    """Drive the shipped browser driver — over the shipped default files, or
    over *files* when a rehearsal needs an editor state of its own.

    The exact BOOTSTRAP text runs in CPython under `_FM_PLAYGROUND_SIM`, in
    a subprocess because it monkeypatches `subprocess.Popen` process-wide.
    That sandbox tracks footman's own call surface — a `run()` that grows a
    keyword the simulated child does not take breaks every task in the page
    — and nothing else exercises it, since Pyodide is not a test dependency.
    """
    if files is None:
        files = _js_default_files()
        assert set(files) == {"tasks.py", "test_demo.py"}, files
    probe = tmp_path / "probe.py"
    probe.write_text(
        _js_bootstrap()
        + "\nimport json, sys\n"
        + "print(_fm_invoke(sys.argv[1], sys.argv[2]))\n",
        encoding="utf-8",
    )
    work = Path(tempfile.mkdtemp(dir=tmp_path))  # a fresh cwd per invocation
    env = {**os.environ, "_FM_PLAYGROUND_SIM": "1", "PYTHONPATH": str(work)}
    if prompts is not None:
        # The sandbox's canned answers for the page's prompt seam — what a
        # person would type into the browser dialogs, in order.
        env["_FM_PLAYGROUND_PROMPTS"] = json.dumps(prompts)
    out = subprocess.run(
        [sys.executable, str(probe), json.dumps(files), line],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=work,
        env=env,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout)
    # The page prints both streams into the one output pane; so does this.
    return int(result["exit_code"]), str(result["stdout"]) + str(result["stderr"])


# The browser's own `-s` (one thread, so the page always runs sequentially)
# is injected off `sys.platform == "emscripten"`, which no rehearsal can
# reach — spell it here instead.
def test_playground_default_sample_runs(tmp_path: Path):
    """`fm check` on the default sample: lint green through the simulated
    child, then the deliberately wrong test failing with pytest's own diff —
    the first thing a visitor sees, and what the page's prose promises."""
    code, output = _playground_invoke(tmp_path, "-s check")
    assert code == 1, output
    assert re.search(r"^ok\s+lint", output, re.M), output
    assert re.search(r"^FAIL\s+test", output, re.M), output
    assert "assert '4' == 'fizz'" in output, output
    assert "1 failed, 2 passed" in output, output


def test_playground_default_sample_spells_its_tools(tmp_path: Path):
    """The sample's `ruff.check("src", fix=fix)` builds the command a reader
    would write by hand, in both states of the flag."""
    for line, command in (
        ("-s --dry-run lint", "$ ruff check src"),
        ("-s --dry-run lint --fix", "$ ruff check src --fix"),
    ):
        _, output = _playground_invoke(tmp_path, line)
        assert command in output, output


def test_playground_path_requirements_pass(tmp_path: Path):
    """A path requirement passes in the sandbox: the page's filesystem holds
    only the editor's files, so the cookbook's `Annotated[Path, isfile]`
    example would otherwise refuse before anything ran. Both seats are
    dummied — the splitter's eager CLI-token check and the executor's late
    one (variadic values) — while the rest of the validation ladder stays
    real: the `check(fn)` beside it still refuses."""
    tasks = """\
from pathlib import Path
from typing import Annotated
from livery.footman import task
from livery.footman.params import check, isfile

def semver(value: str) -> None:
    import re
    if not re.fullmatch(r"\\d+\\.\\d+\\.\\d+", value):
        raise ValueError(f"expected MAJOR.MINOR.PATCH, got {value!r}")

@task
def deploy(config: Annotated[Path, isfile],
           version: Annotated[str, check(semver)]):
    "Roll out."
    print(config, version)

@task
def overlay(*paths: Annotated[Path, isfile]):
    "Apply overlays."
    print(*paths)
"""
    files = {"tasks.py": tasks}
    code, output = _playground_invoke(
        tmp_path, "-s deploy missing.toml 1.2.3", files=files
    )
    assert code == 0, output
    code, output = _playground_invoke(tmp_path, "-s overlay a.toml b.toml", files=files)
    assert code == 0, output
    code, output = _playground_invoke(
        tmp_path, "-s deploy missing.toml not-a-version", files=files
    )
    assert code != 0, output
    assert "MAJOR.MINOR.PATCH" in output, output


# --- the example gallery -----------------------------------------------------
#
# docs/assets/examples.json is dual-read: the playground page fetches it for
# its dropdown, these tests drive every command line of every entry through
# the shipped driver. An example that stops doing what its chip promises
# fails here, not in a visitor's browser.


def _gallery_examples():
    data = json.loads((DOCS / "assets" / "examples.json").read_text(encoding="utf-8"))
    return data["examples"]


_GALLERY = _gallery_examples()

GALLERY_FEATURES = {
    # feature -> the example that demonstrates it. The gallery grows toward
    # covering every feature; a feature listed here without a live example
    # fails, so coverage is enforced, not aspirational.
    "pre-tasks scheduled into a gate": "basics/gate",
    "keep-going collects every failure": "basics/gate",
    "dry-run shows the built command": "basics/gate",
    "typed coercion and choices teach": "typing/coercion",
    "stacked validation markers teach": "validation/deploy",
    "passthrough lands in *args": "variadic/bench",
    "prerequisites and chains": "scheduling/pipeline",
    "run() and shell pipelines": "tools/shell",
    "stdin binding and structured returns": "results/stdin-json",
    "the config cascade": "config/cascade",
    "include() mounts shared tasks": "compose/include",
    "grammar-aware completion": "completion/grammar",
    "ask() prompts in the page": "input/ask",
}


def test_gallery_entries_are_wellformed():
    ids = [example["id"] for example in _GALLERY]
    assert len(ids) == len(set(ids)), "duplicate example ids"
    for example in _GALLERY:
        assert example["title"] and example["category"] and example["blurb"], example[
            "id"
        ]
        assert example["files"], example["id"]
        for name, lines in example["files"].items():
            assert isinstance(lines, list), (example["id"], name)
            assert all(isinstance(line, str) for line in lines), (example["id"], name)
        packages = example.get("packages", [])
        assert isinstance(packages, list), example["id"]
        assert all(isinstance(p, str) and p for p in packages), example["id"]
        assert example["commands"], example["id"]
        for command in example["commands"]:
            assert command["line"] and command["note"], (example["id"], command)
            assert isinstance(command["exit"], int), (example["id"], command)
            assert command["shows"], (example["id"], command)
            prompts = command.get("prompts", [])
            assert isinstance(prompts, list), (example["id"], command)
            assert all(isinstance(p, str) for p in prompts), (example["id"], command)


def test_gallery_features_have_examples():
    ids = {example["id"] for example in _GALLERY}
    for feature, example_id in GALLERY_FEATURES.items():
        assert example_id in ids, f"{feature!r} points at missing {example_id!r}"


def test_gallery_gate_is_the_default_sample():
    """The dropdown's first entry IS the page's built-in sample — the
    fallback when examples.json cannot be fetched. Drift here would show
    two different "default" states depending on how the page loaded."""
    gate = _GALLERY[0]
    joined = {n: "\n".join(lines) + "\n" for n, lines in gate["files"].items()}
    assert joined == _js_default_files()
    match = re.search(r'^const DEFAULT_ARGS = "(.*)";$', _js_source(), re.M)
    assert match, "playground.js no longer defines DEFAULT_ARGS"
    assert gate["commands"][0]["line"] == match.group(1)


@pytest.mark.parametrize(
    ("example", "command"),
    [(e, c) for e in _GALLERY for c in e["commands"]],
    ids=[f"{e['id']}:{c['line']}" for e in _GALLERY for c in e["commands"]],
)
def test_gallery_command_lines_run_as_promised(tmp_path, example, command):
    """Every command chip of every entry, driven exactly as the page runs
    it (the leading `-s` mirrors the sandbox's own injection)."""
    files = {n: "\n".join(lines) + "\n" for n, lines in example["files"].items()}
    code, output = _playground_invoke(
        tmp_path,
        "-s " + command["line"],
        files=files,
        prompts=command.get("prompts"),
    )
    assert code == command["exit"], output
    for marker in command["shows"]:
        assert marker in output, (marker, output)


def _playground_complete(tmp_path: Path, files: dict[str, str], line: str) -> list[str]:
    """Drive the shipped completion driver — the page's Tab — in CPython."""
    probe = tmp_path / "complete_probe.py"
    probe.write_text(
        _js_bootstrap()
        + "\nimport sys\n"
        + "print(_fm_complete(sys.argv[1], sys.argv[2]))\n",
        encoding="utf-8",
    )
    work = Path(tempfile.mkdtemp(dir=tmp_path))
    out = subprocess.run(
        [sys.executable, str(probe), json.dumps(files), line],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=work,
        env={**os.environ, "_FM_PLAYGROUND_SIM": "1", "_FM_JEDI_CACHE": _jedi_cache()},
        check=False,
    )
    assert out.returncode == 0, out.stderr
    answer: list[str] = json.loads(out.stdout)
    return [c.split("\t")[0] for c in answer]


def test_playground_simulated_child_honours_the_bytes_contract(tmp_path: Path):
    """A real Popen answers in bytes unless text mode was asked for. The
    stand-in answered str unconditionally, and platform._syscmd_file's
    `.decode()` took platform.platform() down with it in the page — a
    branch macOS rehearsals never take, so this pins the contract
    directly rather than through platform."""
    files = {
        "tasks.py": (
            "import subprocess\n"
            "from livery.footman import task\n"
            "\n"
            "@task\n"
            "def contract():\n"
            '    "Both modes of the simulated child."\n'
            '    b = subprocess.run(["file", "x"], capture_output=True)\n'
            "    t = subprocess.run(\n"
            '        ["file", "x"], capture_output=True, text=True\n'
            "    )\n"
            "    assert isinstance(b.stdout, bytes), type(b.stdout)\n"
            "    assert isinstance(t.stdout, str), type(t.stdout)\n"
            '    print("bytes and text both honoured")\n'
        )
    }
    code, output = _playground_invoke(tmp_path, "-s contract", files=files)
    assert code == 0, output
    assert "bytes and text both honoured" in output, output


def test_playground_completes_the_homepages_git_branches(tmp_path: Path):
    """The homepage's suggest(branches) parses `git branch` output — in the
    page that child is simulated, and the canned world answers with branch
    names, so Tab offers branches instead of the echo line chopped into
    words (Willem's screenshot: --branch=[simulated], --branch=git, …)."""
    files = {
        "tasks.py": (
            "from typing import Annotated\n"
            "from livery.footman import suggest, task\n"
            "from livery.toolroom.tools import git\n"
            "\n"
            "def branches() -> list[str]:\n"
            '    return git.branch(format="%(refname:short)").stdout.split()\n'
            "\n"
            "@task\n"
            "def deploy(branch: Annotated[str, suggest(branches)] = 'main'):\n"
            '    "Ship a branch."\n'
        )
    }
    got = _playground_complete(tmp_path, files, "deploy --branch=")
    assert got == [
        "--branch=main",
        "--branch=develop",
        "--branch=feature/checkout-flow",
    ], got


def test_playground_dynamic_completion_answers_fresh(tmp_path: Path):
    """A suggest() completer runs in the page — the stand-in for the
    _suggest child a real shell respawns — and reads the editor's files
    as they are NOW: two Tabs over different branches.txt contents answer
    differently, which is the whole point of a dynamic completer."""
    entry = next(e for e in _GALLERY if e["id"] == "completion/grammar")
    files = {n: "\n".join(lines) + "\n" for n, lines in entry["files"].items()}

    first = _playground_complete(tmp_path, files, "switch ")
    assert "feature/gallery" in first, first

    edited = dict(files)
    edited["branches.txt"] = "hotfix/one\nhotfix/two\n"
    second = _playground_complete(tmp_path, edited, "switch hot")
    assert second == ["hotfix/one", "hotfix/two"], second

    # The static grammar still answers, and a path value still defers to
    # a real shell's file completion (an empty answer, never candidates).
    static = _playground_complete(tmp_path, files, "deploy staging --regions=e")
    assert any("eu" in c for c in static), static
    deploy = next(e for e in _GALLERY if e["id"] == "validation/deploy")
    vfiles = {n: "\n".join(lines) + "\n" for n, lines in deploy["files"].items()}
    handoff = _playground_complete(tmp_path, vfiles, "deploy conf")
    assert handoff == [], handoff


def _editor_complete(
    tmp_path: Path, source: str, line: int, column: int
) -> tuple[list[dict[str, Any]], str]:
    """Drive the shipped editor completer in CPython. The source travels
    by file — an argv carrying a literal newline does not survive the
    Windows CreateProcess round-trip intact."""
    probe = tmp_path / "editor_probe.py"
    probe.write_text(
        _js_bootstrap()
        + "\nimport sys\nfrom pathlib import Path as _P\n"
        # A parso cache per worker: concurrent probes sharing one cache
        # dir race its non-atomic pickle writes (EOFError on Windows CI),
        # and a worker runs one probe at a time, so its own cache warms
        # across its tests instead of starting cold in every fresh cwd.
        # The page is one process and keeps the default.
        + "import jedi.settings\n"
        + "import os as _os\n"
        + "jedi.settings.cache_directory = _os.environ['_FM_JEDI_CACHE']\n"
        + "a = sys.argv\n"
        + "src = _P(a[2]).read_text(encoding='utf-8')\n"
        + "print(_fm_editor_complete(a[1], src, a[3], a[4]))\n",
        encoding="utf-8",
    )
    src = tmp_path / "buffer.py"
    src.write_text(source, encoding="utf-8")
    work = Path(tempfile.mkdtemp(dir=tmp_path))
    out = subprocess.run(
        [sys.executable, str(probe), "{}", str(src), str(line), str(column)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=work,
        env={**os.environ, "_FM_PLAYGROUND_SIM": "1", "_FM_JEDI_CACHE": _jedi_cache()},
        check=False,
    )
    assert out.returncode == 0, out.stderr
    answer: list[dict[str, Any]] = json.loads(out.stdout)
    return answer, out.stderr


def test_playground_editor_completion_carries_docstrings(tmp_path: Path):
    """The editor's completion asks jedi over the buffer with footman and
    toolroom importable — so a toolroom handle completes its real methods
    and carries their docstrings, which is the whole point. (The shipped
    completer swallows failures — the page must degrade to no candidates —
    but under SIM it prints the traceback, carried into the messages here.)"""
    got, err = _editor_complete(tmp_path, "import json\njson.du", 2, 7)
    labels = [c["label"] for c in got]
    assert "dump" in labels and "dumps" in labels, (labels, err)
    dump = next(c for c in got if c["label"] == "dump")
    assert "info" in dump and "obj" in dump["info"], dump

    got, err = _editor_complete(
        tmp_path, "from livery.toolroom.tools import ruff\nruff.che", 2, 8
    )
    labels = [c["label"] for c in got]
    assert "check" in labels, (labels, err)


def test_playground_editor_completion_is_relevant(tmp_path: Path):
    """Willem's critique, pinned: no hunting through a big list. Private
    and dunder names stay out unless nothing else answers, and the list
    caps at 20 — every kept entry a real candidate."""
    got, err = _editor_complete(tmp_path, "import json\njson.", 2, 5)
    labels = [c["label"] for c in got]
    assert labels, err
    assert len(labels) <= 20, labels
    assert not any(name.startswith("_") for name in labels), labels

    # Typing the underscore is asking for the private names.
    got, err = _editor_complete(tmp_path, "import json\njson._", 2, 6)
    labels = [c["label"] for c in got]
    assert labels and all(name.startswith("_") for name in labels), (labels, err)


def test_playground_editor_completion_ranks_like_an_ide(tmp_path: Path):
    """Willem's screenshot, pinned: Ctrl-Space inside a call answered an
    alphabetical soup starting at `abs`. The IDE order instead: the call's
    own keyword parameters lead (boosted, each carrying its declaration and
    its Args prose through the __call__ reprobe — jedi drops native param
    completions for a stub handle's synthesized __call__), a name defined
    in the user's own buffer outranks an import, and builtins sink."""
    prefix = 'ruff.check("src", '
    got, err = _editor_complete(
        tmp_path, "from livery.toolroom.tools import ruff\n" + prefix, 2, len(prefix)
    )
    labels = [c["label"] for c in got]
    assert "fix=" in labels, (labels, err)
    fix = next(c for c in got if c["label"] == "fix=")
    assert fix.get("boost") == 2 and "fix" in fix.get("info", ""), fix
    params = [c for c in got if c["label"].endswith("=")]
    assert any("\n\n" in c.get("info", "") for c in params), params
    rest = [c for c in got if not c["label"].endswith("=")]
    assert any(c.get("boost") == -1 for c in rest), rest

    src = "from livery.footman import task\n\n@task\ndef build_wheels():\n    pass\n\nbui\n"
    got, err = _editor_complete(tmp_path, src, 7, 3)
    own = next((c for c in got if c["label"] == "build_wheels"), None)
    assert own is not None, (got, err)
    assert own.get("boost") == 1, own


def _editor_help(
    tmp_path: Path, source: str, line: int, column: int
) -> tuple[dict[str, Any] | None, str]:
    """Drive the shipped hover-help function in CPython — source by file,
    for the same Windows argv-newline reason as `_editor_complete`."""
    probe = tmp_path / "help_probe.py"
    probe.write_text(
        _js_bootstrap()
        + "\nimport sys\nfrom pathlib import Path as _P\n"
        # The worker's parso cache, as `_editor_complete` explains.
        + "import jedi.settings\n"
        + "import os as _os\n"
        + "jedi.settings.cache_directory = _os.environ['_FM_JEDI_CACHE']\n"
        + "a = sys.argv\n"
        + "src = _P(a[2]).read_text(encoding='utf-8')\n"
        + "print(_fm_editor_help(a[1], src, a[3], a[4]))\n",
        encoding="utf-8",
    )
    src = tmp_path / "buffer.py"
    src.write_text(source, encoding="utf-8")
    work = Path(tempfile.mkdtemp(dir=tmp_path))
    out = subprocess.run(
        [sys.executable, str(probe), "{}", str(src), str(line), str(column)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=work,
        env={**os.environ, "_FM_PLAYGROUND_SIM": "1", "_FM_JEDI_CACHE": _jedi_cache()},
        check=False,
    )
    assert out.returncode == 0, out.stderr
    answer: dict[str, Any] | None = json.loads(out.stdout)
    return answer, out.stderr


def test_playground_hover_help_answers_signatures(tmp_path: Path):
    """Hover answers about the symbol under the pointer: a name renders
    its signature and the docstring behind it. The toolroom case is the
    point — hover ruff.check and read its stub."""
    on_call = "import json\njson.dumps(1)"
    help_, err = _editor_help(tmp_path, on_call, 2, 7)
    assert help_ is not None, err
    assert "dumps(" in help_["label"], help_

    on_name = "from livery.toolroom.tools import ruff\nruff.check"
    help_, err = _editor_help(tmp_path, on_name, 2, 7)
    assert help_ is not None, err
    # The stub's signature line, not a bare name — a Name's docstring()
    # leads with it, and dropping to `label or doc` once let a bare
    # "check" slide (Willem's screenshot). The label leads with the name
    # the source spells: the handle's synthesized signature renders its
    # CLASS, so an unfixed label said `Check(` where the code says check.
    assert help_["label"].startswith("check("), help_
    # The stub's __call__ docstring rides along: the summary line and the
    # Args section documenting every flag — the whole reason to hover.
    assert "Args:" in help_["doc"], help_
    assert "diff:" in help_["doc"], help_
    # A signature is the panel's footer and stays one line; the prose
    # leads (Willem's layout).
    assert help_["footer"] is True, help_
    assert "\n" not in help_["label"], help_

    # Hovering a keyword ARGUMENT answers about that argument — its
    # declaration as the label, its Args entry as the doc — never the
    # whole signature (Willem: "the documentation for fix").
    # A @task-decorated function in the buffer answers with ITS signature
    # and docstring — not the TaskFn protocol the decorator's static
    # annotation says it is (Willem's screenshot: hovering `test` showed
    # TaskFn(*args, **kwargs) and the protocol's prose).
    own = (
        "from livery.footman import task\n"
        "\n"
        "@task\n"
        "def build(target: str = 'app'):\n"
        '    "Compile the thing."\n'
    )
    help_, err = _editor_help(tmp_path, own, 4, 5)
    assert help_ is not None, err
    assert help_["label"].startswith("build("), help_
    assert "Compile the thing" in help_["doc"], help_

    on_keyword = "from livery.toolroom.tools import ruff\nruff.check('src', fix=True)"
    col = on_keyword.split("\n")[1].index("fix=") + 1
    help_, err = _editor_help(tmp_path, on_keyword, 2, col)
    assert help_ is not None, err
    assert help_["label"].startswith("fix"), help_
    assert "Check(" not in help_["label"], help_
    assert "Apply fixes" in help_["doc"], help_
    # A parameter's declaration is the subject, not a signature: it
    # stays on top rather than becoming a footer.
    assert help_["footer"] is False, help_

    # What the call returns is read out of the Google-style Returns
    # section — the docstring's own answer, previously never surfaced.
    returning = (
        "def build(target: str = 'app') -> str:\n"
        '    """Compile one bundle.\n'
        "\n"
        "    Args:\n"
        "        target: Which bundle to compile.\n"
        "\n"
        "    Returns:\n"
        "        The artifact's path.\n"
        '    """\n'
        "    return target\n"
    )
    help_, err = _editor_help(tmp_path, returning, 1, 5)
    assert help_ is not None, err
    assert help_["returns"] == "The artifact's path.", help_


def test_playground_hover_needs_a_symbol(tmp_path: Path):
    """Willem's screenshot: resting the pointer just right of the comma
    in `ruff.check("src", fix=fix)` recited the callee's whole signature.
    Hover answers about a SYMBOL — no identifier under the pointer means
    no tooltip, the way every IDE behaves. Which call the cursor sits in
    is the parameter-hints panel's question now."""
    src = "from livery.toolroom.tools import ruff\nfix = False\nruff.check('src', fix=fix)"
    line = src.split("\n")[2]

    # A position adjacent to an identifier still belongs to it — the
    # editor reports the gap between two characters, so the right edge
    # of a word must stay hoverable. These three touch no identifier.
    for col, what in (
        (line.index(",") + 1, "the gap right of the comma"),
        (line.index(","), "the comma itself"),
        (len(line), "past the closing bracket"),
    ):
        help_, err = _editor_help(tmp_path, src, 3, col)
        assert help_ is None, (what, help_, err)

    # The identifiers around those gaps still answer.
    help_, err = _editor_help(tmp_path, src, 3, line.index("fix="))
    assert help_ is not None, err
    assert help_["label"].startswith("fix"), help_


def _editor_sighelp(
    tmp_path: Path, source: str, line: int, column: int
) -> tuple[dict[str, Any] | None, str]:
    """Drive the shipped parameter-hints answerer in CPython. Source by
    file for the same Windows argv-newline reason as `_editor_complete`."""
    probe = tmp_path / "sighelp_probe.py"
    probe.write_text(
        _js_bootstrap()
        + "\nimport sys\nfrom pathlib import Path as _P\n"
        # The worker's parso cache, as `_editor_complete` explains.
        + "import jedi.settings\n"
        + "import os as _os\n"
        + "jedi.settings.cache_directory = _os.environ['_FM_JEDI_CACHE']\n"
        + "a = sys.argv\n"
        + "src = _P(a[2]).read_text(encoding='utf-8')\n"
        + "print(_fm_editor_sighelp(a[1], src, a[3], a[4]))\n",
        encoding="utf-8",
    )
    src = tmp_path / "buffer.py"
    src.write_text(source, encoding="utf-8")
    work = Path(tempfile.mkdtemp(dir=tmp_path))
    out = subprocess.run(
        [sys.executable, str(probe), "{}", str(src), str(line), str(column)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=work,
        env={**os.environ, "_FM_PLAYGROUND_SIM": "1", "_FM_JEDI_CACHE": _jedi_cache()},
        check=False,
    )
    assert out.returncode == 0, out.stderr
    answer: dict[str, Any] | None = json.loads(out.stdout)
    return answer, out.stderr


def test_playground_parameter_hints_track_the_cursor(tmp_path: Path):
    """The IDE gesture for positionals: typing ( or , asks which parameter
    the cursor is on (jedi's Signature.index) and the panel answers
    prose-first (Willem's layout): docstring summary, what the call
    returns, the active parameter's Args entry — and the signature as a
    one-line footer, the callee spelled as the user typed it (never the
    tool handle's class), the active parameter's span marked for inline
    highlight. A variadic positional rides on the summary line."""
    pre = "ruff.check("
    help_, err = _editor_sighelp(
        tmp_path, "from livery.toolroom.tools import ruff\n" + pre, 2, len(pre)
    )
    assert help_ is not None, err
    assert help_["label"].startswith("check("), help_
    assert "\n" not in help_["label"], help_  # collapsed to one line
    assert help_["active"] == "args", help_
    assert help_["sig"][1].startswith("*args"), help_
    assert "Run Ruff" in help_["summary"], help_

    # Past the comma the highlight moves to the next parameter, and a
    # documented one answers with its own Args prose.
    src = 'def deploy(target, region="eu"):\n    pass\n\ndeploy("prod", '
    help_, err = _editor_sighelp(tmp_path, src, 4, len('deploy("prod", '))
    assert help_ is not None, err
    assert help_["active"] == "region", help_
    assert help_["sig"] == ["deploy(target, ", 'region="eu"', ")"], help_
    pre = 'ruff.check("src", fix='
    help_, err = _editor_sighelp(
        tmp_path, "from livery.toolroom.tools import ruff\n" + pre, 2, len(pre)
    )
    assert help_ is not None, err
    assert help_["active"] == "fix", help_
    assert "Apply fixes" in help_["doc"], help_

    # A Google-style Returns section surfaces — the panel says what the
    # call answers with, not just what it takes.
    own = (
        "def build(target: str = 'app') -> str:\n"
        '    """Compile one bundle.\n'
        "\n"
        "    Args:\n"
        "        target: Which bundle to compile.\n"
        "\n"
        "    Returns:\n"
        "        The artifact's path.\n"
        '    """\n'
        "    return target\n"
        "\n"
        "build(\n"
    )
    help_, err = _editor_sighelp(tmp_path, own, 12, 6)
    assert help_ is not None, err
    assert "Compile one bundle" in help_["summary"], help_
    assert help_["returns"] == "The artifact's path.", help_
    assert help_["active"] == "target", help_
    assert "Which bundle" in help_["doc"], help_

    # Outside a call there is nothing to say, and a comma inside a
    # string literal is prose, not an argument boundary.
    help_, err = _editor_sighelp(tmp_path, "x = 1\n", 1, 5)
    assert help_ is None, help_
    pre = 'run("git commit -m foo, bar'
    help_, err = _editor_sighelp(
        tmp_path, "from livery.footman import run\n" + pre, 2, len(pre)
    )
    assert help_ is None, help_


def test_example_markers_are_spent():
    """Every example marker sits directly above a ```python fence — a
    marker that drifted away from its fence would silently stop exempting
    (or restarting) anything."""
    for page in _handwritten_docs():
        lines = page.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if line.strip() not in (FRAGMENT, FRESH, REVISION):
                continue
            following = next((ln for ln in lines[i + 1 :] if ln.strip()), "")
            assert _OPEN.match(following), (
                f"{page.name}:{i + 1}: example marker without a python fence"
            )
