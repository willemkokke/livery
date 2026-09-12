/* The footman playground: a real footman in the browser via Pyodide, plus
 * the "run it there" links under every python example in the docs.
 *
 * Loaded on every page (extra_javascript, module + defer). The site uses
 * navigation.instant, so init re-runs on each document$ emission; every
 * step is idempotent and guarded so a failure here can never break a page.
 *
 * Execution model: the browser cannot spawn processes or threads, so the
 * driver installs a sandbox — subprocess children are simulated (exit 0,
 * output labelled `[simulated]`), runs are sequential (`-s`),
 * parallel() degrades to inline calls, run(shell=...) resolves to a
 * stand-in interpreter (the simulated child never executes it), and
 * path requirements (exists/isfile/isdir) pass unchecked, since the
 * page's filesystem holds only the editor's files. A tab named stdin
 * is the run's pipe, never a file on disk. In-process tools are the
 * exception: pytest really runs, inside the page, through the tools
 * bridge. The playground page discloses all of this.
 */

const PYODIDE_URL = "https://cdn.jsdelivr.net/pyodide/v314.0.3/full/pyodide.mjs";
/* CodeMirror, vendored: one bundle, built from the pinned recipe in
 * vendor/codemirror/ (repo root). Loading it as separate CDN modules was tried
 * and measurably fails — each +esm entry point got its own
 * @codemirror/state instance, and CM rejects extensions whose instanceof
 * checks cross instances. Self-hosted also means the editor needs no
 * second CDN. Imported only on the playground page, only when it inits;
 * if the import fails the textarea stays, exactly as before. */
const CM_URL = new URL("vendor/codemirror.js", import.meta.url);
const SITE_ROOT = new URL("..", import.meta.url); // …/assets/ -> site root
const FRAGMENT_MARK = "example: fragment";
const FRESH_MARK = "example: fresh-session";
const REVISION_MARK = "example: revision";

const DEFAULT_FILES = {
  "tasks.py": `from typing import Literal
from livery.footman import fail, run, task
from livery.toolroom.tools import pytest, ruff

@task
def lint(fix: bool = False):
    "Lint the source tree."
    # a typed wrapper: keywords become flags, False is omitted
    ruff.check("src", fix=fix)

@task(serial=True)   # in-process pytest touches the process globals
def test():
    "Run the tests — real pytest, in your browser."
    pytest("-q", "test_demo.py")

@task
def deploy(target: Literal["dev", "staging", "prod"],
           regions: list[Literal["eu", "us", "ap"]] | None = None):
    "Ship to an environment."
    run(f"./rollout.sh {target} --regions={','.join(regions or ['eu'])}")

@task
def audit():
    "Refuses on purpose — try it with -k."
    fail("the gate is red — deliberately", code=3)

@task(pre=[lint, test])
def check():
    "Lint and test; footman schedules the rest."
`,
  "test_demo.py": `def fizzbuzz(n):
    if n % 15 == 0:
        return "fizzbuzz"
    if n % 3 == 0:
        return "fizz"
    if n % 5 == 0:
        return "buzz"
    return str(n)

def test_three():
    assert fizzbuzz(3) == "fizz"

def test_fifteen():
    assert fizzbuzz(15) == "fizzbuzz"

def test_wrong():
    assert fizzbuzz(4) == "fizz"   # deliberately failing — fix it and rerun
`,
};

const DEFAULT_ARGS = "check";

/* ---------- shared helpers ---------- */

/* URL-safe base64: standard `+`/`/` would corrupt the fragment round-trip —
 * URLSearchParams decodes `+` as a space, atob (forgiving-base64) then
 * *strips* the space, and the bit-stream shifts into mojibake from the
 * first `+` on. Encode with `-`/`_`; decode accepts both alphabets and
 * repairs a legacy-mangled link (base64 never contains a real space, so
 * space→`+` is lossless). */

function b64encode(text) {
  const bytes = new TextEncoder().encode(text);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_");
}

function b64decode(b64) {
  const std = b64.replace(/ /g, "+").replace(/-/g, "+").replace(/_/g, "/");
  const bin = atob(std);
  const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

function onEachPage(fn) {
  const run = () => {
    try {
      fn();
    } catch (err) {
      console.warn("footman playground:", err);
    }
  };
  if (window.document$ && typeof window.document$.subscribe === "function") {
    window.document$.subscribe(run); // instant navigation: fires per page
  } else if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", run);
  } else {
    run();
  }
}

/* ---------- "run it there" links ---------- */

function markerOf(block) {
  // The docs steer blocks with an HTML comment directly above the fence —
  // `example: fragment` (illustration only), `example: revision` (revises
  // an earlier definition — in a concatenated session it would be a
  // duplicate task name), or `example: fresh-session` (the page's session
  // restarts here); markdown passes the comment through, so honour it too.
  for (let n = block.previousSibling; n; n = n.previousSibling) {
    if (n.nodeType === Node.TEXT_NODE && !n.textContent.trim()) continue;
    return n.nodeType === Node.COMMENT_NODE ? n.textContent : "";
  }
  return "";
}

/* The prompt a run link opens with. Best effort from a light scan of the
 * example's signatures: the first task in the linked block that runs bare —
 * every parameter defaulted (or a variadic tail) — else the first such task
 * anywhere in the session, else `--list`, which always works and shows what
 * the example defines. First, not last: a page builds toward its composed
 * gate, and the composed task tends to reach real pytest (red in a scratch
 * dir with no tests) where the leaves run simulated and green. */

function splitTopLevel(text, sep) {
  const parts = [];
  let depth = 0;
  let current = "";
  for (const ch of text) {
    if ("([{".includes(ch)) depth++;
    else if (")]}".includes(ch)) depth--;
    if (ch === sep && depth === 0) {
      parts.push(current);
      current = "";
    } else {
      current += ch;
    }
  }
  parts.push(current);
  return parts;
}

function runsBare(params) {
  return splitTopLevel(params, ",").every((p) => {
    const t = p.trim();
    if (!t || t === "/" || t.startsWith("*")) return true;
    return splitTopLevel(t, "=").length > 1; // a top-level `=`: has a default
  });
}

function bareTasks(code) {
  const groups = {}; // decorator variable -> the group's CLI name
  for (const m of code.matchAll(/^(\w+)\s*=\s*group\(\s*["']([^"']+)["']/gm)) {
    groups[m[1]] = m[2];
  }
  const out = [];
  const def =
    /^@(?:(\w+)\.)?(task|default)(?:\([^)]*\))?\s*\ndef\s+(\w+)\s*\(([\s\S]*?)\)\s*(?:->[^:]*)?:/gm;
  for (const m of code.matchAll(def)) {
    const [, owner, kind, name, params] = m;
    if (owner && !(owner in groups)) continue; // an owner this scan can't name
    if (!runsBare(params)) continue;
    out.push(kind === "default" ? groups[owner] : owner ? `${groups[owner]}.${name}` : name);
  }
  return out;
}

function suggestCommand(session) {
  for (const source of [session[session.length - 1], session.join("\n\n")]) {
    const tasks = bareTasks(source);
    if (tasks.length) return tasks[0];
  }
  return "--list";
}

function addRunLinks() {
  if (document.getElementById("fm-playground")) return; // not on the playground
  const article = document.querySelector("article");
  if (!article) return;
  const session = []; // the page-as-session prefix, like the docs tests
  for (const block of article.querySelectorAll("div.language-python.highlight")) {
    const codeEl = block.querySelector("code");
    if (!codeEl) continue;
    const marker = markerOf(block);
    if (marker.includes(FRAGMENT_MARK) || marker.includes(REVISION_MARK)) continue;
    if (marker.includes(FRESH_MARK)) session.length = 0;
    session.push(codeEl.textContent.replace(/\n$/, ""));
    if (block.dataset.fmpLinked) continue; // idempotent re-init
    block.dataset.fmpLinked = "1";
    const href = new URL("playground/", SITE_ROOT);
    href.hash =
      "code=" +
      b64encode(session.join("\n\n") + "\n") +
      "&cmd=" +
      encodeURIComponent(suggestCommand(session));
    const wrap = document.createElement("div");
    wrap.className = "fmp-runlink";
    const a = document.createElement("a");
    a.href = href.toString();
    a.textContent = "run it there ↗";
    a.title = "Open this example (and what it builds on) in the playground";
    wrap.appendChild(a);
    block.after(wrap);
  }
}

/* ---------- the playground page ---------- */

/* The driver. Plain-ASCII python only — this is a JS template literal, so
 * a backslash would be eaten before python ever saw it (chr(10)/chr(0)
 * stand in for the escapes). `_FM_PLAYGROUND_SIM` lets the exact shipped
 * text be rehearsed in CPython. */
const BOOTSTRAP = `
import json, os, sys, traceback
from pathlib import Path

if sys.platform == "emscripten" or os.environ.get("_FM_PLAYGROUND_SIM"):
    import subprocess
    import threading

    # Bytecode caches key on mtime+size, and an edit-and-rerun in the page
    # can land inside one clock tick — never cache, always recompile.
    sys.dont_write_bytecode = True

    # Pyodide's threading has no native thread ids (the API is documented
    # as platform-dependent); footman stamps results with one.
    if not hasattr(threading, "get_native_id"):
        threading.get_native_id = threading.get_ident

    # A few well-known read commands answer with plausible canned output
    # instead of the [simulated] echo. A dynamic completer that parses a
    # child's stdout -- the homepage's git-branch example -- should offer
    # branch names, not the echo line chopped into words. The table is
    # small on purpose: reads that docs examples parse, nothing that
    # pretends the write side happened.
    _FM_CANNED = (
        ("git branch", "main;develop;feature/checkout-flow".replace(";", chr(10))),
        ("git tag", "v1.0.0;v1.1.0;v2.0.0".replace(";", chr(10))),
        # platform's generic branch shells out uname -p for the processor;
        # a real browser spawn would fail and yield the empty processor,
        # but the simulated echo leaked into platform.platform() as
        # "...wasm32-[simulated]_uname_-p-32bit". Empty is the honest answer.
        ("uname", ""),
    )

    class _SimulatedPopen:
        # The browser cannot spawn processes; every child succeeds and says
        # what it would have been. In-process tools bypass this entirely.
        def __init__(self, argv, **kwargs):
            self.args = argv
            self.pid = 4242
            self.returncode = 0
            cmd = argv if isinstance(argv, str) else " ".join(argv)
            probe = cmd
            if (
                not isinstance(argv, str)
                and len(argv) >= 3
                and argv[1] in ("-c", "-Command")
            ):
                probe = argv[2]  # a shell wrapper: match the line it runs
            out = "[simulated] " + cmd + chr(10)
            for prefix, canned in _FM_CANNED:
                if probe.startswith(prefix):
                    out = canned + chr(10)
                    break
            # A real Popen answers in BYTES unless text mode was asked for
            # -- platform._syscmd_file taught this the hard way, calling
            # .decode on our string and taking platform.platform() down
            # with it. Honour the contract, not just the happy path.
            self._text = bool(
                kwargs.get("text")
                or kwargs.get("universal_newlines")
                or kwargs.get("encoding")
                or kwargs.get("errors")
            )
            self._out = out if self._text else out.encode("utf-8")

        # Keyword-for-keyword what run() calls: it always passes input=
        # (None unless the task feeds the child), so a positional-only
        # signature here breaks every run() in the page.
        def communicate(self, input=None, timeout=None):
            return self._out, ("" if self._text else b"")

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

        def terminate(self):
            pass

        def kill(self):
            pass

        # A real Popen is a context manager; code that spells
        # "with subprocess.Popen(...)" must not crash on the stand-in.
        # (jedi's import does exactly that on py3.11 + Windows.)
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    subprocess.Popen = _SimulatedPopen

    # One thread is all the browser has: parallel() runs its callables
    # inline, in order, and a failure still surfaces after the others ran.
    import livery.footman as footman
    import livery.footman.context

    footman.parallel  # resolve the lazy re-export before overriding it

    def _inline_parallel(*fns):
        failure = None
        for fn in fns:
            try:
                fn()
            except BaseException as exc:
                failure = failure or exc
        if failure is not None:
            raise failure

    footman.context.parallel = _inline_parallel
    footman.__dict__["parallel"] = _inline_parallel

    # The browser has no shells to find, and the simulated child never
    # executes its argv — so run(shell=...) resolves to a stand-in
    # interpreter prefix instead of refusing. The pipeline examples run;
    # what they would have executed shows as the simulated line.
    def _fm_resolve_shell(kind, policy="posix"):
        return ["/bin/sh", "-c"]

    footman.context._resolve_shell = _fm_resolve_shell

    # include("module") in an example imports a sibling editor tab; make
    # the working directory importable the way a terminal's usually is.
    if "" not in sys.path:
        sys.path.insert(0, "")

    # ask()/prompt()/confirm()/select() work: the page IS the terminal.
    # footman's prompts gate on a tty and read the real stdin; here the
    # real stdin's readline() is one browser prompt, and the text the
    # framework wrote to the real stderr since the last read (the
    # question, a menu's numbered lines) becomes that dialog's text.
    # Cancel reads as EOF, so a defaultless ask fails with footman's own
    # taught message instead of looping. Under _FM_PLAYGROUND_SIM the
    # answers come from a canned queue, so rehearsals drive the SAME
    # seam deterministically. isatty on the out-stream stays False,
    # which keeps the live status line out of the dialog text.
    import getpass

    _fm_prompt_queue = []
    if os.environ.get("_FM_PLAYGROUND_PROMPTS"):
        _fm_prompt_queue = json.loads(os.environ["_FM_PLAYGROUND_PROMPTS"])
    _fm_console = {"buf": ""}

    class _FMTerminalOut:
        def write(self, text):
            _fm_console["buf"] += text
            return len(text)

        def flush(self):
            pass

        def isatty(self):
            return False

    class _FMStdin:
        def isatty(self):
            return True

        def readline(self):
            text = _fm_console["buf"].strip() or "footman asks:"
            _fm_console["buf"] = ""
            if sys.platform == "emscripten":
                import js

                answer = js.window.prompt(text)
                if answer is None:
                    return ""  # cancel reads as EOF
                return answer + chr(10)
            if _fm_prompt_queue:
                return str(_fm_prompt_queue.pop(0)) + chr(10)
            return ""  # no canned answer left: EOF, the taught refusal

    _fm_terminal_out = _FMTerminalOut()
    _fm_terminal_in = _FMStdin()
    footman.context._stdin_is_tty = lambda: True
    footman.context.real_stdin = lambda: _fm_terminal_in
    footman.context.real_stderr = lambda: _fm_terminal_out

    def _fm_getpass(prompt="", stream=None):
        # A secret prompt: the browser dialog cannot mask typing, so the
        # value is visible while typed -- the page is a playground, not a
        # vault. It still round-trips as a Secret.
        _fm_console["buf"] += prompt
        line = _fm_terminal_in.readline()
        if line == "":
            raise EOFError
        return line.rstrip(chr(10))

    getpass.getpass = _fm_getpass

    # The page's filesystem holds only the editor's files, so a path
    # requirement (exists / isfile / isdir) could hardly ever be met — an
    # example like Annotated[Path, isfile] would refuse before anything
    # ran. Simulate it the way children are simulated: the check passes;
    # the rest of the validation ladder (types, choices, bounds, check(fn))
    # stays real. Two seats, same funnel: the splitter validates CLI
    # tokens eagerly, the executor validates what the splitter never saw
    # (env fallbacks, variadic and passthrough values).
    import dataclasses
    import livery.footman._executor
    import livery.footman._split

    def _fm_path_passes(where, label, value, req):
        pass

    livery.footman._split._check_path = _fm_path_passes

    _fm_real_validate = livery.footman._executor._validate_value

    def _fm_validate_sans_path(value, peeled, label):
        if peeled.path_req is not None:
            peeled = dataclasses.replace(peeled, path_req=None)
        return _fm_real_validate(value, peeled, label)

    livery.footman._executor._validate_value = _fm_validate_sans_path

def _fm_sandbox_line(line):
    words = line.split()
    if sys.platform == "emscripten":
        if "-s" not in words and "--sequential" not in words:
            line = "-s " + line
        # The pane renders SGR codes; without a tty the tri-state --color
        # would resolve to never and the transcript would be monochrome.
        if not any(w.startswith("--color") for w in words):
            line = "--color=always " + line
    return line

def _fm_invoke(files_json, line, columns=80):
    # The pane's measured width, the way a terminal would report it:
    # shutil.get_terminal_size honours COLUMNS with no tty in sight, so
    # footman's own wrapping and pytest's ruler bars fit the pane.
    os.environ["COLUMNS"] = str(int(columns))
    files = json.loads(files_json)
    # A tab named stdin is the pipe, not a file: its text feeds the
    # invocation's stdin (so Stdin[...] parameters bind from it) and is
    # never written to disk. An empty tab means no pipe at all.
    stdin_text = files.pop("stdin", None)
    if sys.platform == "emscripten" or os.environ.get("_FM_PLAYGROUND_SIM"):
        # A quiet pytest.ini, so the sample's pytest call needs no plugin
        # incantations: no:cacheprovider keeps .pytest_cache out of the
        # page's filesystem (and stale last-failed state out of reruns),
        # no:footman keeps footman's own auto-loaded pytest plugin from
        # joining a run that is already inside a footman task. An explicit
        # pytest.ini in the editor wins over this default.
        files.setdefault(
            "pytest.ini",
            "[pytest]" + chr(10) + "addopts = -p no:cacheprovider -p no:footman" + chr(10),
        )
    for name, content in files.items():
        Path(name).write_text(content, encoding="utf-8")
    try:
        from livery.footman.testing import Runner
        result = Runner().invoke(
            _fm_sandbox_line(line),
            tasks=Path("tasks.py"),
            stdin=stdin_text if stdin_text else None,
        )
        return json.dumps({
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
        })
    except Exception:
        return json.dumps({
            "exit_code": 1,
            "stdout": "",
            "stderr": traceback.format_exc(limit=8),
        })
    finally:
        # In-process pytest imports the editor's files; evict them so the
        # next Run collects what the editor says then, not this run's
        # modules — otherwise rerunning fm test reruns stale code.
        written = {str(Path(name).resolve()) for name in files}
        for mod_name, module in list(sys.modules.items()):
            file = getattr(module, "__file__", None)
            if file and str(Path(file).resolve()) in written:
                del sys.modules[mod_name]

def _fm_wrap_signature(sig):
    # One parameter per line once a signature outgrows one: split at the
    # outermost parens' depth-zero commas. String-level on purpose -- the
    # name branch only ever has the stub's rendered line to work with.
    if len(sig) <= 60:
        return sig
    open_i = sig.find("(")
    close_i = sig.rfind(")")
    if open_i < 0 or close_i <= open_i:
        return sig
    inner = sig[open_i + 1 : close_i]
    parts = []
    depth = 0
    cur = ""
    for ch in inner:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur.strip())
    if len(parts) < 2:
        return sig
    nl = chr(10)
    body = "".join("    " + p + "," + nl for p in parts)
    return sig[: open_i + 1] + nl + body + sig[close_i:]

def _fm_arg_doc(doc, name):
    # The Args-section entry for *name*: its first line and the
    # continuation lines indented beneath it, joined to one paragraph.
    out = []
    grabbing = False
    for ln in doc.split(chr(10)):
        if grabbing:
            if ln.startswith("        ") and ln.strip():
                out.append(ln.strip())
                continue
            break
        if ln.startswith("    " + name + ":"):
            out.append(ln.split(":", 1)[1].strip())
            grabbing = True
    return " ".join(out)

def _fm_returns_doc(doc):
    # The Returns section's text, joined to one paragraph.
    out = []
    grabbing = False
    for ln in doc.split(chr(10)):
        if grabbing:
            if ln.startswith("    ") and ln.strip():
                out.append(ln.strip())
                continue
            break
        if ln.strip() == "Returns:":
            grabbing = True
    return " ".join(out)

def _fm_bracket_name(source, head):
    # The identifier spelled immediately before a call's bracket.
    try:
        bline, bcol = head.bracket_start
    except Exception:
        return ""
    src_lines = source.split(chr(10))
    li = bline - 1
    if not (0 <= li < len(src_lines)):
        return ""
    text = src_lines[li]
    a = bcol
    while a > 0 and (text[a - 1].isalnum() or text[a - 1] == "_"):
        a -= 1
    return text[a:bcol]

def _fm_spelled_head(label, spelled):
    # The label leads with the name the source spells: a tool handle's
    # synthesized signature renders its CLASS (Check) where the code
    # says check. Only the head before the bracket changes.
    paren = label.find("(")
    if paren <= 0 or not spelled:
        return label
    return spelled + label[paren:]

def _fm_help_payload(label, doc, returns):
    # Willem's layout: the prose leads -- summary, what the call
    # returns, then the arguments -- and a signature is the footer,
    # collapsed to one line. A label that is not a signature (a
    # parameter's declaration, a module's name) stays on top.
    footer = "(" in label and bool(doc)
    return json.dumps(
        {
            "label": label if footer else _fm_wrap_signature(label),
            "doc": doc,
            "returns": returns,
            "footer": footer,
        }
    )

def _fm_editor_help(files_json, source, line, column):
    # Hover help, asked of the interpreter: the signature first (jedi
    # get_signatures answers inside a call; help() answers on the name
    # itself), the docstring's opening beneath it. The same in-process
    # jedi world the completer uses; any miss answers nothing.
    try:
        import jedi
    except Exception:
        return json.dumps(None)
    files = json.loads(files_json)
    files.pop("stdin", None)
    for name, content in files.items():
        Path(name).write_text(content, encoding="utf-8")
    try:
        script = jedi.Script(
            code=source,
            path=str(Path("editing.py").resolve()),
            environment=jedi.InterpreterEnvironment(),
        )
        # Hover answers about a SYMBOL under the pointer. No identifier
        # there -- whitespace, a comma, a bracket -- and there is nothing
        # to say: without this the enclosing call's signature answered
        # for every gap between arguments, so resting just right of a
        # comma recited the whole callee. Which call the cursor sits in
        # is the parameter-hints panel's question, not hover's.
        src_all = source.split(chr(10))
        li0 = int(line) - 1
        if not (0 <= li0 < len(src_all)):
            return json.dumps(None)
        t0 = src_all[li0]
        a0 = int(column)
        while a0 > 0 and (t0[a0 - 1].isalnum() or t0[a0 - 1] == "_"):
            a0 -= 1
        b0 = int(column)
        while b0 < len(t0) and (t0[b0].isalnum() or t0[b0] == "_"):
            b0 += 1
        w0 = t0[a0:b0]
        if not w0:
            return json.dumps(None)
        # A quoted or numeric literal has nothing worth a tooltip either.
        quotes = chr(34) + chr(39)
        if w0[0].isdigit():
            return json.dumps(None)
        if (a0 > 0 and t0[a0 - 1] in quotes) or (
            b0 < len(t0) and t0[b0] in quotes
        ):
            return json.dumps(None)
        spelled = w0
        signatures = script.get_signatures(int(line), int(column))
        if signatures:
            head = signatures[0]

            def call_doc():
                return _fm_call_doc(source, head)

            # A keyword argument under the pointer answers with ITS
            # documentation, not the whole signature: the hovered word,
            # followed by a single =, matching one of the call's params.
            src_lines = source.split(chr(10))
            li = int(line) - 1
            word = ""
            keyword_side = False
            if 0 <= li < len(src_lines):
                text = src_lines[li]
                a = int(column)
                while a > 0 and (text[a - 1].isalnum() or text[a - 1] == "_"):
                    a -= 1
                b = int(column)
                while b < len(text) and (text[b].isalnum() or text[b] == "_"):
                    b += 1
                word = text[a:b]
                rest = text[b:].lstrip()
                keyword_side = rest.startswith("=") and not rest.startswith("==")
            pnames = []
            try:
                pnames = [p.name for p in head.params]
            except Exception:
                pnames = []
            if word and keyword_side and word in pnames:
                try:
                    plabel = head.params[pnames.index(word)].to_string()
                except Exception:
                    plabel = word
                pdoc = _fm_arg_doc(call_doc(), word)
                if pdoc or plabel != word:
                    return _fm_help_payload(plabel, pdoc, "")

            label = None  # decided below: symbol first, call as fallback
        # The symbol under the pointer outranks the enclosing call --
        # hovering a variable used as an argument should answer about
        # the variable, not recite the callee's whole signature.
        names = script.help(int(line), int(column))
        names = [n for n in names if n.type != "keyword"]
        # A definition in the user's own buffer outranks the inferred
        # type: a @task-decorated function is statically a TaskFn (the
        # decorator's annotation), but hovering it should answer with
        # ITS signature and ITS docstring, not the protocol's.
        local_def = False
        try:
            here = str(Path("editing.py").resolve())
            local = [
                n
                for n in script.goto(int(line), int(column))
                if n.type == "function" and str(n.module_path or "") == here
            ]
            if local:
                names = local
                local_def = True
        except Exception:
            pass
        if names and names[0].type == "instance" and (
            (names[0].full_name or "").startswith("builtins.")
        ):
            return json.dumps(None)  # a literal: nothing worth saying
        if not names and signatures:
            head = signatures[0]
            label = head.to_string()
            doc = call_doc()
            # The callee's own spelling, not the hovered word.
            spelled = _fm_bracket_name(source, head)
        elif not names:
            return json.dumps(None)
        else:
            head = names[0]
            # A callable name answers with its call signature and the
            # docstring behind it -- for a toolroom stub that reaches
            # through to __call__, whose Args section documents every
            # flag. Only a non-callable falls through to the name's own
            # docstring dance below.
            if head.type == "param":
                # A parameter answers with its declaration, and with the
                # owner's Args entry when the docstring carries one --
                # a bare name told the reader nothing.
                desc = head.description or ""
                if desc.startswith("param "):
                    desc = desc[6:]
                label = desc or (head.name or "")
                doc = ""
                parent = None
                try:
                    parent = head.parent()
                except Exception:
                    parent = None
                if parent is not None:
                    try:
                        doc = _fm_arg_doc(
                            parent.docstring(raw=True) or "", head.name or ""
                        )
                    except Exception:
                        doc = ""
                    owner = parent.name or ""
                    if owner == "__call__":
                        try:
                            owner = parent.parent().name or owner
                        except Exception:
                            pass
                    if not doc and owner:
                        doc = "Parameter of " + owner + "()."
                return _fm_help_payload(label, doc, "")
            if head.type == "module":
                label = head.name or ""
                doc = head.docstring(raw=True) or ""
                paragraphs = [
                    p for p in doc.split(chr(10) + chr(10)) if p.strip()
                ]
                doc = (chr(10) + chr(10)).join(paragraphs[:3])
                return _fm_help_payload(label, doc, "")
            call_sigs = []
            try:
                if not local_def:
                    call_sigs = head.get_signatures()
            except Exception:
                call_sigs = []
            if call_sigs and (call_sigs[0].name or "").startswith("_"):
                call_sigs = []  # a private synthesized callee teaches nothing
            if call_sigs:
                label = call_sigs[0].to_string()
                # A builtin type keeps its own name: the spelled head is
                # for callables the source names, and renaming bool( to
                # fix( would imply the variable were one.
                if (call_sigs[0].full_name or "").startswith("builtins."):
                    spelled = ""
                doc = call_sigs[0].docstring(raw=True) or ""
                if not doc:
                    # The synthesized instance-call signature drops the
                    # docstring a stub puts on __call__ -- asking for
                    # name.__call__ literally retrieves it (measured), so
                    # rewrite the hovered expression and ask again.
                    src_lines = source.split(chr(10))
                    li = int(line) - 1
                    if 0 <= li < len(src_lines):
                        text = src_lines[li]
                        end = int(column)
                        while end < len(text) and (
                            text[end].isalnum() or text[end] == "_"
                        ):
                            end += 1
                        probed = text[:end] + ".__call__" + text[end:]
                        probe_src = chr(10).join(
                            src_lines[:li] + [probed] + src_lines[li + 1 :]
                        )
                        try:
                            calls = jedi.Script(
                                code=probe_src,
                                environment=jedi.InterpreterEnvironment(),
                            ).help(int(line), end + 5)
                            if calls:
                                doc = calls[0].docstring(raw=True) or ""
                        except Exception:
                            pass
                # Read the Returns section before the doc is trimmed.
                returns = _fm_returns_doc(doc)
                paragraphs = [
                    p for p in doc.split(chr(10) + chr(10)) if p.strip()
                ]
                doc = (chr(10) + chr(10)).join(paragraphs[:3])
                lines = doc.split(chr(10))
                if len(lines) > 200:
                    doc = chr(10).join(lines[:200]) + chr(10) + "…"
                return _fm_help_payload(
                    _fm_spelled_head(label, spelled), doc, returns
                )
            # A Name's docstring() leads with the signature -- raw=True
            # strips it, which is how the tooltip once showed a bare
            # "check". The signature may wrap over several physical
            # lines, so take the paren-balanced head, not just line one.
            full = head.docstring() or ""
            lines = full.split(chr(10))
            sig_lines = []
            depth = 0
            seen = False
            for ln in lines:
                sig_lines.append(ln)
                for ch in ln:
                    if ch in "([{":
                        depth += 1
                        seen = True
                    elif ch in ")]}":
                        depth -= 1
                if seen and depth <= 0:
                    break
            if seen and depth <= 0:
                label = " ".join(ln.strip() for ln in sig_lines).strip()
                doc = chr(10).join(lines[len(sig_lines):]).strip()
            else:
                first = lines[0].strip() if lines else ""
                label = first if first else (head.name or "")
                doc = chr(10).join(lines[1:]).strip()
            if label.startswith("_") and (head.name or "") and not (
                head.name.startswith("_")
            ):
                label = head.name  # a private synthesized shape hides the real name
            if local_def and head.line:
                # jedi bakes the inferred type into every render -- a
                # @task def answers TaskFn even through its own
                # docstring -- but the source header has the truth.
                dln = head.line - 1
                if 0 <= dln < len(src_all):
                    header = ""
                    depth = 0
                    seen = False
                    for ln2 in src_all[dln : dln + 12]:
                        header = header + (" " if header else "") + ln2.strip()
                        for ch in ln2:
                            if ch in "([{":
                                depth += 1
                                seen = True
                            elif ch in ")]}":
                                depth -= 1
                        if seen and depth <= 0:
                            break
                    if header.startswith("def "):
                        header = header[4:]
                    header = header.rstrip(":").strip()
                    if header:
                        label = header
        # The tooltip scrolls, so the budget is generous: an Args section
        # documenting thirty flags is the payload, not an overflow.
        returns = _fm_returns_doc(doc)
        paragraphs = [p for p in doc.split(chr(10) + chr(10)) if p.strip()]
        doc = (chr(10) + chr(10)).join(paragraphs[:3])
        lines = doc.split(chr(10))
        if len(lines) > 200:
            doc = chr(10).join(lines[:200]) + chr(10) + "…"
        if not label and not doc:
            return json.dumps(None)
        return _fm_help_payload(_fm_spelled_head(label, spelled), doc, returns)
    except Exception:
        if os.environ.get("_FM_PLAYGROUND_SIM"):
            traceback.print_exc(file=sys.stderr)
        return json.dumps(None)

def _fm_editor_sighelp(files_json, source, line, column):
    # Signature help, the IDE gesture: typing ( or , inside a call asks
    # which signature encloses the cursor and which parameter it is on.
    # jedi's Signature.index is exactly that; the active parameter's
    # Args prose rides along, reprobed through __call__ like the hover.
    try:
        import jedi
    except Exception:
        return json.dumps(None)
    files = json.loads(files_json)
    files.pop("stdin", None)
    for name, content in files.items():
        Path(name).write_text(content, encoding="utf-8")
    try:
        # A comma inside a string literal is prose, not an argument
        # boundary: an odd number of quotes left of the cursor means
        # the cursor sits inside one, and the panel stays shut.
        src_all = source.split(chr(10))
        li0 = int(line) - 1
        if 0 <= li0 < len(src_all):
            left = src_all[li0][: int(column)]
            for q in (chr(34), chr(39)):
                if left.count(q) % 2 == 1:
                    return json.dumps(None)
        script = jedi.Script(
            code=source,
            path=str(Path("editing.py").resolve()),
            environment=jedi.InterpreterEnvironment(),
        )
        signatures = script.get_signatures(int(line), int(column))
        if not signatures:
            return json.dumps(None)
        head = signatures[0]
        label = ""
        try:
            label = head.to_string() or ""
        except Exception:
            label = ""
        if not label:
            return json.dumps(None)
        # The callee as the user spelled it: a tool handle's synthesized
        # signature renders its class (Check, not check), so the head is
        # rebuilt from the identifier before the bracket in the source.
        try:
            bline, bcol = head.bracket_start
            btxt = src_all[bline - 1]
            e0 = bcol
            a1 = e0
            while a1 > 0 and (btxt[a1 - 1].isalnum() or btxt[a1 - 1] == "_"):
                a1 -= 1
            spelled = btxt[a1:e0]
            paren = label.find("(")
            if spelled and paren > 0:
                label = spelled + label[paren:]
        except Exception:
            pass
        active = ""
        doc = ""
        idx = head.index
        params = list(head.params)
        prose = _fm_call_doc(source, head)
        # The docstring's opening paragraph: the fallback when the
        # active parameter carries no Args entry of its own (a variadic
        # positional usually rides on the summary line).
        summary = prose.split(chr(10) + "Args:")[0].strip()
        summary = summary.split(chr(10) + chr(10))[0].strip()
        if idx is not None and 0 <= idx < len(params):
            active = params[idx].name or ""
            if active:
                star = chr(42)
                doc = (
                    _fm_arg_doc(prose, active)
                    or _fm_arg_doc(prose, star + active)
                    or _fm_arg_doc(prose, star + star + active)
                )
        # The signature stays one line -- the panel's footer; the prose
        # leads. The active parameter travels as its span so the page
        # highlights it inline instead of by line.
        before = label
        mid = ""
        after = ""
        if active and idx is not None:
            frag = ""
            try:
                frag = params[idx].to_string() or ""
            except Exception:
                frag = ""
            at = label.find(frag) if frag else -1
            if at < 0:
                frag = active
                at = label.find(frag)
            if at >= 0:
                before = label[:at]
                mid = frag
                after = label[at + len(frag) :]
        return json.dumps(
            {
                "label": label,
                "sig": [before, mid, after],
                "active": active,
                "doc": doc,
                "summary": summary,
                "returns": _fm_returns_doc(prose),
            }
        )
    except Exception:
        if os.environ.get("_FM_PLAYGROUND_SIM"):
            traceback.print_exc(file=sys.stderr)
        return json.dumps(None)

def _fm_call_doc(source, head):
    # The call's docstring, reaching through to __call__ when the
    # synthesized signature dropped it: rewrite the callee at its
    # bracket and ask again. Shared by hover help and completion.
    import jedi
    d = head.docstring(raw=True) or ""
    if d:
        return d
    try:
        bline, bcol = head.bracket_start
    except Exception:
        return ""
    src_lines = source.split(chr(10))
    li = bline - 1
    if not (0 <= li < len(src_lines)):
        return ""
    text = src_lines[li]
    probed = text[:bcol] + ".__call__" + text[bcol:]
    probe_src = chr(10).join(src_lines[:li] + [probed] + src_lines[li + 1 :])
    try:
        calls = jedi.Script(
            code=probe_src,
            environment=jedi.InterpreterEnvironment(),
        ).help(bline, bcol + 5)
        if calls:
            return calls[0].docstring(raw=True) or ""
    except Exception:
        pass
    return ""

def _fm_editor_complete(files_json, source, line, column):
    # Editor completion, asked of the interpreter: jedi over the buffer,
    # with footman and toolroom importable -- so ruff.che<Tab> completes
    # from the actual typed stubs and carries their docstrings. The other
    # tabs are written first, so a sibling module import resolves. jedi
    # is installed by the page on first use (like pytest); if it is not
    # importable yet, the answer is simply empty.
    try:
        import jedi
    except Exception:
        if os.environ.get("_FM_PLAYGROUND_SIM"):
            traceback.print_exc(file=sys.stderr)
        return json.dumps([])
    if os.environ.get("_FM_PLAYGROUND_SIM"):
        sys.stderr.write(
            "jedi " + jedi.__version__ + " on " + sys.version.split()[0] + chr(10)
        )
    files = json.loads(files_json)
    files.pop("stdin", None)
    for name, content in files.items():
        Path(name).write_text(content, encoding="utf-8")
    try:
        # InterpreterEnvironment: jedi's in-process world. The default
        # environment inference shells out to a python subprocess to learn
        # sys.path -- the simulated child would feed it garbage here, and
        # the browser has no subprocesses at all.
        script = jedi.Script(
            code=source,
            path=str(Path("editing.py").resolve()),
            environment=jedi.InterpreterEnvironment(),
        )
        found = script.complete(int(line), int(column))
    except Exception:
        # The page degrades to no candidates; the rehearsal says why.
        if os.environ.get("_FM_PLAYGROUND_SIM"):
            traceback.print_exc(file=sys.stderr)
        return json.dumps([])
    # Relevance over bulk: private and dunder names go (unless nothing
    # else answered -- the user typed the underscore, so keep them), and
    # the list caps at 20 with every kept entry carrying its docstring.
    # A hunt through fifty names was noise, not help.
    public = [c for c in found if not c.name.startswith("_")]
    if public:
        found = public
    out = []
    # Inside a call, the call's own keyword parameters lead the list --
    # boosted above everything, each carrying its declaration and its
    # Args entry. An alphabetical soup of builtins was the old answer.
    try:
        sigs = script.get_signatures(int(line), int(column))
    except Exception:
        sigs = []
    if sigs:
        sig_doc = _fm_call_doc(source, sigs[0])
        seen_params = set()
        for prm in sigs[0].params:
            pname = prm.name or ""
            decl = ""
            try:
                decl = prm.to_string()
            except Exception:
                decl = pname
            if not pname or pname in seen_params or decl.startswith("*"):
                continue
            seen_params.add(pname)
            entry = {"label": pname + "=", "type": "keyword", "boost": 2}
            info = decl
            extra = _fm_arg_doc(sig_doc, pname)
            if extra:
                info = decl + chr(10) + chr(10) + extra
            entry["info"] = info
            out.append(entry)
    taken = {e["label"] for e in out}
    found = [
        c for c in found if c.name not in taken and c.name + "=" not in taken
    ]
    # Scope-aware ranking: a name defined in the user's own tabs beats
    # an import, and builtins sink below both -- the Pylance order.
    here_dir = str(Path(".").resolve())
    for c in found[:20]:
        entry = {"label": c.name, "type": c.type}
        try:
            mp = str(c.module_path or "")
            if mp.startswith(here_dir):
                entry["boost"] = 1
            elif c.in_builtin_module():
                entry["boost"] = -1
        except Exception:
            pass
        if len(out) < 25:
            try:
                doc = c.docstring()
            except Exception:
                doc = ""
            if doc:
                head = doc.split(chr(10) + chr(10))[0]
                lines = head.split(chr(10))
                entry["info"] = chr(10).join(lines[:6])
        out.append(entry)
    return json.dumps(out)

_fm_manifest = {"code": None, "tree": None, "root": None}

def _fm_dynamic(root, partial, prefix, param, seg_path):
    # A dynamic completer, run fresh — the page's stand-in for the
    # _suggest child a real shell would respawn. Same walk, same muting,
    # same emission: prefix + value, filtered by the typed partial; any
    # miss answers nothing, never an error. "Fresh" is simply a call
    # here, because the interpreter holding the user's code is this one.
    import contextlib, inspect, io
    from livery.footman import _coerce, _manifest as manifest, registry
    completer = None
    if seg_path:
        node = root
        for name in seg_path[:-1]:
            node = node.groups.get(name) if node else None
        task = node.tasks.get(seg_path[-1]) if node else None
        if task is not None:
            for p in manifest.resolved_signature(task).parameters.values():
                if registry.cli_name(p.name) != param:
                    continue
                if p.annotation is inspect.Parameter.empty:
                    continue
                completer = _coerce.peel(p.annotation).completer
                break
    else:
        for opt in root.contributions.get("globals", ()):
            if opt.name == param:
                completer = _coerce.peel(opt.annotation).completer
                break
    if completer is None:
        return []
    try:
        sink_out, sink_err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(sink_out), contextlib.redirect_stderr(sink_err):
            values = [str(v) for v in completer.fn()]
    except Exception:
        return []
    return [prefix + v for v in values if v.startswith(partial)]

def _fm_complete(files_json, line):
    # The real completion hot path over the editor's files: build the
    # manifest tree once per tasks.py text, then every Tab is a pure walk —
    # the same complete() a shell hook consults. The other tabs are
    # written first, so a dynamic completer that reads a file sees what
    # the editor says now, not what the last Run left behind.
    import types
    from livery.footman import _manifest as manifest, registry
    from livery.footman._complete import _DYNAMIC, complete
    files = json.loads(files_json)
    files.pop("stdin", None)  # the pipe, not a file
    code = files.get("tasks.py", "")
    for name, content in files.items():
        if name != "tasks.py":
            Path(name).write_text(content, encoding="utf-8")
    if _fm_manifest["code"] != code:
        module = types.ModuleType("tasks")
        sys.modules["tasks"] = module
        try:
            with registry.capture() as root:
                exec(compile(code, "tasks.py", "exec"), module.__dict__)
            _fm_manifest["tree"] = manifest.build_manifest(root)["tree"]
            _fm_manifest["root"] = root
            _fm_manifest["code"] = code
        except Exception:
            return json.dumps([])
        finally:
            sys.modules.pop("tasks", None)
    words = line.split()
    if not line or line.endswith(" "):
        words.append("")
    out = complete(_fm_manifest["tree"], words)
    # The comma-continuation marker (exit 102 on a real shell) rides to the
    # page as a leading element the JS strips into its glue. Compared
    # literally — never imported — so the released wheel behind this page
    # keeps completing whether or not it knows the marker yet.
    more = [chr(0) + "more"] if out and out[0] == chr(0) + "more" else []
    if more:
        out = out[1:]
    if out and out[0] == _DYNAMIC:
        fresh = _fm_dynamic(_fm_manifest["root"], out[1], out[2], out[3], out[4:])
        return json.dumps(more + fresh if fresh else [])
    if out and out[0].startswith(chr(0)):
        # A file handoff needs a real shell's filename completion; the
        # elements after the marker are protocol payload, not candidates.
        return json.dumps([])
    return json.dumps(more + out if out else [])
`;

/* ---------- the example gallery ---------- */

/* Curated examples live in examples.json beside this file — dual-read:
 * the page fetches it, the docs tests drive every command line of every
 * entry through the shipped driver in CPython. Files are stored as line
 * arrays (readable JSON) and joined here. */

const DOCS_ENTRY_ID = "__docs";

async function loadExamples() {
  try {
    const res = await fetch(new URL("examples.json", import.meta.url));
    if (!res.ok) return null;
    const data = await res.json();
    return Array.isArray(data.examples) && data.examples.length ? data.examples : null;
  } catch {
    return null; // no gallery — the page still works as a single sample
  }
}

function joinedFiles(entry) {
  return Object.fromEntries(
    Object.entries(entry.files).map(([name, lines]) => [name, lines.join("\n") + "\n"]),
  );
}

let pyodideReady = null; // one load per browser tab, kept across instant nav

function loadRuntime(status) {
  if (!pyodideReady) {
    pyodideReady = (async () => {
      status("loading Python — a few seconds, once per visit…");
      const { loadPyodide } = await import(PYODIDE_URL);
      const pyodide = await loadPyodide();
      status("installing footman + toolroom…");
      await pyodide.loadPackage("micropip");
      const micropip = pyodide.pyimport("micropip");
      await micropip.install(["livery-footman", "livery-toolroom"]);
      pyodide.runPython(BOOTSTRAP);
      return pyodide;
    })();
    pyodideReady.catch(() => {
      pyodideReady = null; // a failed load may be retried
    });
  }
  return pyodideReady;
}

/* Per-example micropip installs: an entry's `packages` are fetched on
 * its first run (the way pytest always was), so each example carries its
 * own install cost and the default page stays light. One promise per
 * package per loaded runtime; a failed install may be retried. */
const packagesReady = new Map();

function ensurePackages(pyodide, names, status) {
  return Promise.all(
    names.map((name) => {
      if (!packagesReady.has(name)) {
        packagesReady.set(
          name,
          (async () => {
            status(`installing ${name} — first use only…`);
            const micropip = pyodide.pyimport("micropip");
            await micropip.install(name);
          })().catch((err) => {
            packagesReady.delete(name);
            throw err;
          }),
        );
      }
      return packagesReady.get(name);
    }),
  );
}

/* The output pane renders footman's own SGR colours (the sandbox forces
 * --color=always). Only the styling SGRs footman and pytest emit are
 * mapped — bold/dim/italic/underline and the 16 fg colours — everything
 * else, extended colours included, is consumed and dropped, and any
 * non-SGR escape is stripped rather than shown. */
function ansiFragment(text) {
  const frag = document.createDocumentFragment();
  const escape = /\x1b\[([0-9;]*)m|\x1b\[[0-9;?]*[A-Za-z]/g;
  let state = { bold: false, dim: false, italic: false, underline: false, fg: null };
  let last = 0;
  const flush = (upto) => {
    if (upto <= last) return;
    const chunk = text.slice(last, upto);
    const cls = [];
    if (state.bold) cls.push("fmp-a-b");
    if (state.dim) cls.push("fmp-a-d");
    if (state.italic) cls.push("fmp-a-i");
    if (state.underline) cls.push("fmp-a-u");
    if (state.fg !== null) cls.push(`fmp-a-f${state.fg}`);
    if (cls.length) {
      const span = document.createElement("span");
      span.className = cls.join(" ");
      span.textContent = chunk;
      frag.appendChild(span);
    } else {
      frag.appendChild(document.createTextNode(chunk));
    }
  };
  for (const m of text.matchAll(escape)) {
    flush(m.index);
    last = m.index + m[0].length;
    if (m[1] === undefined) continue; // not an SGR: stripped, never shown
    const codes = m[1] === "" ? [0] : m[1].split(";").map(Number);
    for (let i = 0; i < codes.length; i++) {
      const c = codes[i];
      if (c === 0) {
        state = { bold: false, dim: false, italic: false, underline: false, fg: null };
      } else if (c === 1) state.bold = true;
      else if (c === 2) state.dim = true;
      else if (c === 3) state.italic = true;
      else if (c === 4) state.underline = true;
      else if (c === 22) state.bold = state.dim = false;
      else if (c === 23) state.italic = false;
      else if (c === 24) state.underline = false;
      else if (c >= 30 && c <= 37) state.fg = c - 30;
      else if (c >= 90 && c <= 97) state.fg = c - 90 + 8;
      else if (c === 39) state.fg = null;
      else if (c === 38 || c === 48) i += codes[i + 1] === 5 ? 2 : 4;
    }
  }
  flush(text.length);
  return frag;
}

function initPlayground() {
  const root = document.getElementById("fm-playground");
  if (!root || root.dataset.fmpInit) return;
  root.dataset.fmpInit = "1";

  const code = document.getElementById("fmp-code");
  const args = document.getElementById("fmp-args");
  const runBtn = document.getElementById("fmp-run");
  const clearBtn = document.getElementById("fmp-clear");
  const out = document.getElementById("fmp-out");
  const status = document.getElementById("fmp-status");
  const tabBar = root.querySelector(".fmp-label");
  const galleryBar = root.querySelector(".fmp-gallery");
  const select = document.getElementById("fmp-example");
  const resetBtn = document.getElementById("fmp-reset");
  const blurb = document.getElementById("fmp-blurb");
  const menuBtn = document.getElementById("fmp-menu");
  const cmdMenu = document.getElementById("fmp-cmdmenu");
  const setStatus = (text) => {
    status.textContent = text;
  };

  /* One editor, many files: the tab bar decides which one it shows. The
   * editor starts as the plain textarea and upgrades to CodeMirror when
   * (if) the import lands — everything else talks to `editor`, never to
   * either widget directly. */
  let files = { ...DEFAULT_FILES };
  let currentFile = "tasks.py";

  let editor = {
    get: () => code.value,
    set: (text) => {
      code.value = text;
    },
    focus: () => code.focus(),
  };

  // Prefill tasks.py from a "run it there" link, if one brought us here.
  const hash = new URLSearchParams(window.location.hash.slice(1));
  try {
    if (hash.has("code")) files["tasks.py"] = b64decode(hash.get("code"));
  } catch {
    /* a malformed fragment keeps the default */
  }
  args.value = hash.get("cmd") || DEFAULT_ARGS;
  runBtn.disabled = false;

  // Python loads as soon as the page does — the first Run, Tab, or hover
  // should not pay the download (Willem's call). jedi rides along so the
  // editor helps immediately; the status line narrates, and a failed
  // load may be retried by the next Run exactly as before.
  loadRuntime(setStatus)
    .then((pyodide) => ensurePackages(pyodide, ["jedi>=0.20"], setStatus))
    .then(() => setStatus("ready"))
    .catch(() => {});

  function syncFiles() {
    files[currentFile] = editor.get();
  }

  function renderTabs() {
    tabBar.replaceChildren();
    for (const name of Object.keys(files)) {
      const tab = document.createElement("button");
      tab.type = "button";
      tab.className = "fmp-tab";
      tab.setAttribute("role", "tab");
      tab.textContent = name;
      tab.classList.toggle("fmp-tab-active", name === currentFile);
      tab.addEventListener("click", () => showFile(name));
      tabBar.appendChild(tab);
    }
  }

  function showFile(name) {
    syncFiles();
    currentFile = name;
    editor.set(files[name]);
    for (const tab of tabBar.children) {
      tab.classList.toggle("fmp-tab-active", tab.textContent === name);
    }
    editor.focus();
  }

  editor.set(files[currentFile]);
  renderTabs();

  /* The gallery: a dropdown of curated examples, each with its own file
   * set and a row of command chips. Switching swaps the editor in place —
   * same page, same loaded Pyodide. Edits are remembered per example for
   * the visit; Reset restores the pristine files. */

  const gallery = new Map(); // id -> {title, category, blurb, commands, files}
  const edits = new Map(); // id -> this visit's edited files
  let currentId = null;

  function replaceFiles(next) {
    files = { ...next };
    currentFile = Object.keys(files)[0];
    renderTabs();
    editor.set(files[currentFile]);
  }

  /* The curated commands, one click away, always all of them: the ▾ on
   * the prompt opens a menu of every command the example ships — never
   * filtered by what is typed — and picking one fills the prompt. */

  function renderCommandMenu(commands) {
    cmdMenu.replaceChildren();
    const list = commands ?? [];
    menuBtn.hidden = !list.length;
    cmdMenu.hidden = true;
    for (const command of list) {
      const item = document.createElement("button");
      item.type = "button";
      const strong = document.createElement("strong");
      strong.textContent = command.line;
      item.appendChild(strong);
      if (command.note) {
        const dim = document.createElement("span");
        dim.textContent = command.note;
        item.appendChild(dim);
      }
      item.addEventListener("click", () => {
        args.value = command.line;
        cmdMenu.hidden = true;
        hideCandidates();
        args.focus();
      });
      cmdMenu.appendChild(item);
    }
  }

  menuBtn.addEventListener("click", () => {
    cmdMenu.hidden = !cmdMenu.hidden;
  });
  document.addEventListener("mousedown", (event) => {
    if (cmdMenu.hidden) return;
    if (cmdMenu.contains(event.target) || menuBtn.contains(event.target)) return;
    cmdMenu.hidden = true;
  });

  function adoptEntry(entry) {
    blurb.textContent = entry.blurb ?? "";
    renderCommandMenu(entry.commands);
    select.value = entry.id;
  }

  function switchTo(id) {
    const entry = gallery.get(id);
    if (!entry || id === currentId) return;
    if (currentId !== null) {
      syncFiles();
      edits.set(currentId, { ...files });
    }
    currentId = id;
    replaceFiles(edits.get(id) ?? entry.files);
    args.value = entry.commands?.[0]?.line ?? "--list";
    hideCandidates();
    adoptEntry(entry);
    if (id !== DOCS_ENTRY_ID) {
      history.replaceState(null, "", "#example=" + encodeURIComponent(id));
    }
  }

  function populateSelect() {
    select.replaceChildren();
    const docs = gallery.get(DOCS_ENTRY_ID);
    if (docs) select.appendChild(new Option(docs.title, docs.id));
    const groups = new Map(); // category -> its optgroup, in first-seen order
    for (const entry of gallery.values()) {
      if (entry.id === DOCS_ENTRY_ID) continue;
      if (!groups.has(entry.category)) {
        const group = document.createElement("optgroup");
        group.label = entry.category;
        groups.set(entry.category, group);
        select.appendChild(group);
      }
      groups.get(entry.category).appendChild(new Option(entry.title, entry.id));
    }
  }

  select.addEventListener("change", () => switchTo(select.value));
  resetBtn.addEventListener("click", () => {
    const entry = gallery.get(currentId);
    if (!entry) return;
    edits.delete(currentId);
    replaceFiles(entry.files);
    args.value = entry.commands?.[0]?.line ?? args.value;
  });

  loadExamples().then((examples) => {
    if (!examples) return; // fetch failed: today's single-sample page
    for (const entry of examples) {
      gallery.set(entry.id, { ...entry, files: joinedFiles(entry) });
    }
    const fromDocs = hash.has("code");
    if (fromDocs) {
      // The run-it-there fragment is its own entry, wrapping the files
      // already in the editor — switching away and back keeps it.
      gallery.set(DOCS_ENTRY_ID, {
        id: DOCS_ENTRY_ID,
        title: "From the docs page",
        category: null,
        blurb: "What the run-it-there link brought over. The menu has more.",
        commands: [],
        files: { ...files },
      });
    }
    populateSelect();
    const asked = hash.get("example");
    const wanted = fromDocs
      ? DOCS_ENTRY_ID
      : asked && gallery.has(asked)
        ? asked
        : examples[0].id;
    const entry = gallery.get(wanted);
    currentId = wanted;
    if (!fromDocs && wanted !== examples[0].id) {
      // A deep link to a non-default example: its files replace the
      // default sample the page opened with.
      replaceFiles(entry.files);
      args.value = hash.get("cmd") || entry.commands?.[0]?.line || DEFAULT_ARGS;
    }
    adoptEntry(entry);
    galleryBar.hidden = false;
  });

  /* CodeMirror, if it arrives. `syncFiles` reads through `editor`, so a
   * mid-session upgrade keeps the same files; the textarea stays in the
   * DOM as the fallback and simply hides. */
  /* Editor completion, asked of the interpreter: fires by itself only
   * right after a `.` (member access — where the toolroom stubs shine)
   * and on Ctrl-Space anywhere, so typing never waits on Python. jedi
   * installs on first use, like pytest; until the runtime is loaded the
   * editor simply doesn't offer. */
  async function editorCompletions(context) {
    const word = context.matchBefore(/[\w]*/);
    const before = context.state.sliceDoc(0, word ? word.from : context.pos);
    if (!context.explicit && !before.endsWith(".")) return null;
    // A typed `.` never *starts* the runtime download — only an explicit
    // Ctrl-Space may pay that cost; after that, dots complete freely.
    if (!context.explicit && !pyodideReady) return null;
    const pyodide = await loadRuntime(setStatus);
    await ensurePackages(pyodide, ["jedi>=0.20"], setStatus);
    setStatus("ready");
    syncFiles();
    const pos = context.pos;
    const doc = context.state.doc;
    const lineObj = doc.lineAt(pos);
    const fn = pyodide.globals.get("_fm_editor_complete");
    const raw = fn(
      JSON.stringify(files),
      doc.toString(),
      lineObj.number,
      pos - lineObj.from,
    );
    fn.destroy?.();
    const options = JSON.parse(raw).map((c) => ({
      label: c.label,
      type: c.type,
      ...(c.info ? { info: c.info } : {}),
      ...(c.boost ? { boost: c.boost } : {}),
    }));
    if (!options.length) return null;
    return { from: word ? word.from : pos, options };
  }

  (async () => {
    try {
      const {
        footmanSetup,
        EditorView,
        python,
        footmanTheme,
        autocompletion,
        completionKeymap,
        completionStatus,
        startCompletion,
        keymap,
        hoverTooltip,
        tooltips,
        highlightPython,
        showTooltip,
        StateField,
        StateEffect,
        Prec,
      } = await import(CM_URL.href);

      /* Signature help on hover: rest the pointer on a name (or inside a
       * call's parens) and the interpreter answers — the signature line
       * in code face, the docstring's opening beneath it. Hovering never
       * starts the runtime download; once it is loaded, help is free. */
      const signatureHelp = hoverTooltip(async (view, pos) => {
        if (!pyodideReady) return null;
        const pyodide = await loadRuntime(setStatus);
        await ensurePackages(pyodide, ["jedi>=0.20"], setStatus);
        syncFiles();
        const doc = view.state.doc;
        const lineObj = doc.lineAt(pos);
        const fn = pyodide.globals.get("_fm_editor_help");
        const raw = fn(
          JSON.stringify(files),
          doc.toString(),
          lineObj.number,
          pos - lineObj.from,
        );
        fn.destroy?.();
        const help = JSON.parse(raw);
        if (!help) return null;
        return {
          pos,
          create: () => {
            const dom = document.createElement("div");
            dom.className = "fmp-signature";
            // The signature in the editor's own colours — the same
            // HighlightStyle, via the bundle's highlighter helper. One
            // block per line, so a soft-wrapped parameter hangs at its
            // indent instead of snapping back to column zero.
            const sig = document.createElement("div");
            for (const lineText of help.label.split("\n")) {
              const lineEl = document.createElement("div");
              lineEl.className = "fmp-sig-line";
              lineEl.appendChild(highlightPython(lineText));
              sig.appendChild(lineEl);
            }
            // A signature is the footer, the prose leads (Willem's
            // layout); a label that is not one — a parameter's
            // declaration, a module's name — is the subject and stays
            // on top.
            if (!help.footer) dom.appendChild(sig);
            if (help.doc) {
              const docEl = document.createElement("div");
              docEl.className = "fmp-signature-doc";
              // The summary is whatever precedes the first Google-style
              // section header; Args renders structurally beneath it —
              // each argument a bold name with its description — the way
              // an IDE would show it.
              const sectionAt = help.doc.match(/(^|\n)([A-Z][A-Za-z]*):\n/);
              const summary = (
                sectionAt ? help.doc.slice(0, sectionAt.index) : help.doc
              ).trim();
              if (summary) {
                const sumEl = document.createElement("div");
                sumEl.textContent = summary;
                docEl.appendChild(sumEl);
              }
              if (help.returns) {
                const row = document.createElement("div");
                row.className = "fmp-arg";
                const nameEl = document.createElement("strong");
                nameEl.textContent = "Returns";
                row.appendChild(nameEl);
                row.appendChild(document.createTextNode(" " + help.returns));
                docEl.appendChild(row);
              }
              const argsAt = help.doc.match(/(^|\n)Args:\n/);
              if (argsAt) {
                const headEl = document.createElement("div");
                headEl.className = "fmp-args-head";
                headEl.textContent = "Arguments";
                docEl.appendChild(headEl);
                const block = help.doc.slice(argsAt.index + argsAt[0].length);
                let entry = null;
                const flush = () => {
                  if (!entry) return;
                  const row = document.createElement("div");
                  row.className = "fmp-arg";
                  const nameEl = document.createElement("strong");
                  nameEl.textContent = entry.name;
                  row.appendChild(nameEl);
                  row.appendChild(document.createTextNode(" " + entry.text));
                  docEl.appendChild(row);
                  entry = null;
                };
                for (const lineText of block.split("\n")) {
                  // A section header at column zero ends the table —
                  // without this, "Returns:" landed inside the last
                  // argument's description.
                  if (/^\S/.test(lineText)) break;
                  const m = lineText.match(/^ {4}(\w+): ?(.*)$/);
                  if (m) {
                    flush();
                    entry = { name: m[1], text: m[2] };
                  } else if (entry && lineText.trim()) {
                    entry.text += " " + lineText.trim();
                  }
                }
                flush();
              }
              dom.appendChild(docEl);
            }
            if (help.footer) {
              sig.firstChild.classList.add("fmp-sig-footer");
              dom.appendChild(sig);
            }
            return { dom };
          },
        };
      });

      /* Parameter hints, the IDE gesture: typing ( or , inside a call
       * opens a panel above the cursor — the signature one parameter per
       * line, the active one highlighted, its documentation beneath —
       * and the highlight follows the cursor across commas. Escape
       * closes it; Mod-Shift-Space summons it. A typed paren never
       * starts the runtime download (the dot rule); the explicit
       * keybinding may. */
      const setParamHints = StateEffect.define();
      const paramHintsField = StateField.define({
        create: () => null,
        update(value, tr) {
          for (const e of tr.effects) if (e.is(setParamHints)) value = e.value;
          if (value && (tr.docChanged || tr.selection))
            value = { ...value, pos: tr.state.selection.main.head };
          return value;
        },
        provide: (f) => showTooltip.from(f),
      });
      /* Willem's layout: the prose leads — docstring summary, what the
       * call returns, then the active parameter's own documentation —
       * and the signature is the footer, collapsed to one line with the
       * active parameter highlighted inline. The old signature-first
       * rendering was a thirty-line wall with the documentation below
       * the fold. */
      const buildHintsDom = (help) => {
        const dom = document.createElement("div");
        dom.className = "fmp-signature";
        const prose = [];
        if (help.summary) prose.push(["", help.summary]);
        if (help.returns) prose.push(["Returns", help.returns]);
        if (help.doc && help.active) prose.push([help.active, help.doc]);
        if (prose.length) {
          const docEl = document.createElement("div");
          docEl.className = "fmp-signature-doc";
          for (const [name, text] of prose) {
            const row = document.createElement("div");
            row.className = "fmp-arg";
            if (name) {
              const nameEl = document.createElement("strong");
              nameEl.textContent = name;
              row.appendChild(nameEl);
              row.appendChild(document.createTextNode(" " + text));
            } else {
              row.textContent = text;
            }
            docEl.appendChild(row);
          }
          dom.appendChild(docEl);
        }
        const sigEl = document.createElement("div");
        sigEl.className = "fmp-sig-line";
        if (prose.length) sigEl.classList.add("fmp-sig-footer");
        const [before, mid, after] = help.sig;
        sigEl.appendChild(highlightPython(before));
        if (mid) {
          const midEl = document.createElement("span");
          midEl.className = "fmp-sig-active";
          midEl.appendChild(highlightPython(mid));
          sigEl.appendChild(midEl);
        }
        if (after) sigEl.appendChild(highlightPython(after));
        dom.appendChild(sigEl);
        return dom;
      };
      let hintsSeq = 0;
      const paramHintsProbe = async (view) => {
        const seq = ++hintsSeq;
        const pyodide = await loadRuntime(setStatus);
        await ensurePackages(pyodide, ["jedi>=0.20"], setStatus);
        syncFiles();
        const doc = view.state.doc;
        const pos = view.state.selection.main.head;
        const lineObj = doc.lineAt(pos);
        const fn = pyodide.globals.get("_fm_editor_sighelp");
        const raw = fn(
          JSON.stringify(files),
          doc.toString(),
          lineObj.number,
          pos - lineObj.from,
        );
        fn.destroy?.();
        if (seq !== hintsSeq) return;
        const help = JSON.parse(raw);
        view.dispatch({
          effects: setParamHints.of(
            help
              ? {
                  pos: view.state.selection.main.head,
                  above: true,
                  create: () => ({ dom: buildHintsDom(help) }),
                }
              : null,
          ),
        });
      };
      const paramHintsWatch = EditorView.updateListener.of((update) => {
        const open = update.state.field(paramHintsField, false);
        if (update.docChanged) {
          let typed = "";
          update.changes.iterChanges((fa, ta, fb, tb, ins) => {
            const t = ins.toString();
            if (t) typed = t.slice(-1);
          });
          if ((typed === "(" || typed === ",") && pyodideReady) {
            // Both surfaces, the VS Code way: the completion menu below
            // the cursor (what can I insert), the hints panel above it
            // (what does this slot mean). Same keystroke.
            paramHintsProbe(update.view);
            startCompletion(update.view);
            return;
          }
        }
        if (open && (update.docChanged || update.selectionSet))
          paramHintsProbe(update.view);
      });
      const paramHintsKeys = Prec.high(
        keymap.of([
          {
            key: "Mod-Shift-Space",
            run: (v) => {
              paramHintsProbe(v);
              return true;
            },
          },
          {
            key: "Escape",
            run: (v) => {
              // The menu closes first (its own Escape binding), the
              // panel second — the VS Code order.
              if (completionStatus(v.state)) return false;
              if (!v.state.field(paramHintsField, false)) return false;
              hintsSeq++; // drop any in-flight answer
              v.dispatch({ effects: setParamHints.of(null) });
              return true;
            },
          },
        ]),
      );

      const host = document.createElement("div");
      host.className = "fmp-cm";
      code.after(host);
      const view = new EditorView({
        doc: editor.get(),
        parent: host,
        extensions: [
          footmanSetup,
          python(),
          autocompletion({ override: [editorCompletions] }),
          keymap.of(completionKeymap),
          signatureHelp,
          paramHintsField,
          paramHintsWatch,
          paramHintsKeys,
          // Tooltips live on document.body: fixed positioning alone
          // still clipped at the pane's edges (an ancestor kept acting
          // as the containing block), and a body parent is the one
          // reliable escape. Their styling rides playground.css — the
          // editor theme's scope ends at the editor's own DOM.
          tooltips({ position: "fixed", parent: document.body }),
          // The bundle's theme colours via the site's --md-code-hl-*
          // variables: the editor matches every Pygments block on the
          // site and follows the palette toggle live, no re-mount.
          footmanTheme,
          EditorView.updateListener.of((update) => {
            if (update.docChanged) files[currentFile] = update.state.doc.toString();
          }),
          EditorView.domEventHandlers({
            keydown: (event, v) => {
              if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                event.preventDefault();
                run();
                return true;
              }
              if (event.key === "Tab" && !event.shiftKey) {
                // Four spaces, like the textarea — never a focus escape.
                event.preventDefault();
                const { from, to } = v.state.selection.main;
                v.dispatch({
                  changes: { from, to, insert: "    " },
                  selection: { anchor: from + 4 },
                });
                return true;
              }
              return false;
            },
          }),
        ],
      });
      editor = {
        get: () => view.state.doc.toString(),
        set: (text) => {
          view.dispatch({
            changes: { from: 0, to: view.state.doc.length, insert: text },
          });
        },
        focus: () => view.focus(),
      };
      root.classList.add("fmp-cm-on");
    } catch (err) {
      console.warn("footman playground editor:", err); // the textarea remains
    }
  })();

  code.addEventListener("keydown", (event) => {
    if (event.key === "Tab") {
      event.preventDefault();
      const { selectionStart: s, selectionEnd: e, value } = code;
      code.value = value.slice(0, s) + "    " + value.slice(e);
      code.selectionStart = code.selectionEnd = s + 4;
    }
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) run();
  });
  args.addEventListener("keydown", (event) => {
    if (event.key === "Enter") run();
    if (event.key === "Tab") {
      event.preventDefault();
      completeArgs();
    }
    if (event.key === "Escape") {
      hideCandidates();
      cmdMenu.hidden = true;
    }
  });
  args.addEventListener("input", hideCandidates);
  runBtn.addEventListener("click", run);
  clearBtn.addEventListener("click", () => out.replaceChildren());

  /* The pane is a session transcript: every run appends its prompt line
   * and output, like the terminal it stands in for. Clear starts over. */
  function appendRun(line, body, failed) {
    const entry = document.createElement("div");
    entry.className = "fmp-run" + (failed ? " fmp-run-failed" : "");
    const echo = document.createElement("div");
    echo.className = "fmp-echo";
    const promptEl = document.createElement("span");
    promptEl.textContent = "fm ";
    echo.appendChild(promptEl);
    echo.appendChild(document.createTextNode(line));
    entry.appendChild(echo);
    const body_el = document.createElement("pre");
    body_el.className = "fmp-run-out";
    body_el.appendChild(ansiFragment(body));
    entry.appendChild(body_el);
    out.appendChild(entry);
    out.scrollTop = out.scrollHeight;
  }

  function measureColumns() {
    // The pane's width in character cells of the actual code font.
    const probe = document.createElement("span");
    probe.style.visibility = "hidden";
    probe.style.whiteSpace = "pre";
    probe.textContent = "0".repeat(100);
    out.appendChild(probe);
    const charWidth = probe.getBoundingClientRect().width / 100;
    probe.remove();
    const inner = out.clientWidth - 2 * parseFloat(getComputedStyle(out).paddingLeft);
    const columns = Math.floor(inner / charWidth);
    return Math.max(40, Math.min(columns || 80, 200));
  }

  let running = false;
  async function run() {
    if (running) return;
    running = true;
    runBtn.disabled = true;
    try {
      syncFiles();
      const pyodide = await loadRuntime(setStatus);
      // The example's declared packages, plus the pytest fallback for
      // hand-typed code and docs fragments that never declared anything.
      const wanted = new Set(gallery.get(currentId)?.packages ?? []);
      const everything = Object.values(files).join("\n") + "\n" + args.value;
      if (/\bpytest\b/.test(everything)) wanted.add("pytest");
      if (wanted.size) await ensurePackages(pyodide, [...wanted], setStatus);
      setStatus("running…");
      const invoke = pyodide.globals.get("_fm_invoke");
      const raw = invoke(JSON.stringify(files), args.value, measureColumns());
      invoke.destroy?.();
      const result = JSON.parse(raw);
      let body =
        (result.stdout || "") +
        (result.stderr ? (result.stdout ? "\n" : "") + result.stderr : "");
      if (!body.trim()) body = "(no output)";
      appendRun(args.value, body, result.exit_code !== 0);
      setStatus(`exit code ${result.exit_code}`);
      status.classList.toggle("fmp-status-failed", result.exit_code !== 0);
    } catch (err) {
      appendRun(args.value, String(err), true);
      setStatus("the runtime failed to load — check your connection and retry");
    } finally {
      running = false;
      runBtn.disabled = false;
    }
  }

  /* Tab completion: the same manifest completer a shell hook consults,
   * over whatever the editor currently says. */

  const strip = document.getElementById("fmp-complete");

  function hideCandidates() {
    strip.hidden = true;
    strip.replaceChildren();
  }

  function insertCompletion(name, glued) {
    const cursor = args.selectionStart ?? args.value.length;
    const before = args.value.slice(0, cursor);
    const after = args.value.slice(cursor);
    const partial = before.match(/\S*$/)[0];
    // `glued` mirrors the resolver's comma-continuation marker (exit 102 on
    // a real shell): more list items may follow, so the comma is the next
    // keystroke, not a deletion — the same reading bash/pwsh/nushell give it.
    const glue = glued || name.endsWith("=") ? "" : " ";
    const head = before.slice(0, before.length - partial.length) + name + glue;
    args.value = head + after;
    args.selectionStart = args.selectionEnd = head.length;
    args.focus();
  }

  function commonPrefix(names) {
    let prefix = names[0] ?? "";
    for (const name of names) {
      while (!name.startsWith(prefix)) prefix = prefix.slice(0, -1);
    }
    return prefix;
  }

  async function completeArgs() {
    try {
      syncFiles();
      const pyodide = await loadRuntime(setStatus);
      setStatus("ready");
      const fn = pyodide.globals.get("_fm_complete");
      const cursor = args.selectionStart ?? args.value.length;
      const raw = fn(JSON.stringify(files), args.value.slice(0, cursor));
      fn.destroy?.();
      const candidates = JSON.parse(raw);
      const glued = candidates[0] === "\u0000more";
      if (glued) candidates.shift();
      const names = candidates.map((c) => c.split("\t")[0]);
      hideCandidates();
      if (!names.length) return;
      if (names.length === 1) {
        insertCompletion(names[0], glued);
        return;
      }
      const partial = args.value.slice(0, cursor).match(/\S*$/)[0];
      const prefix = commonPrefix(names);
      if (prefix.length > partial.length) {
        const cut = prefix.length; // keep the menu; extend what's typed
        const before = args.value.slice(0, cursor);
        args.value =
          before.slice(0, before.length - partial.length) +
          prefix +
          args.value.slice(cursor);
        args.selectionStart = args.selectionEnd =
          before.length - partial.length + cut;
      }
      for (const candidate of candidates) {
        const [name, summary] = candidate.split("\t");
        const button = document.createElement("button");
        button.type = "button";
        const strong = document.createElement("strong");
        strong.textContent = name;
        button.appendChild(strong);
        if (summary) {
          const dim = document.createElement("span");
          dim.textContent = summary;
          button.appendChild(dim);
        }
        // mousedown, not click: the input keeps focus and the strip stays.
        button.addEventListener("mousedown", (event) => {
          event.preventDefault();
          insertCompletion(name, glued);
          hideCandidates();
        });
        strip.appendChild(button);
      }
      strip.hidden = false;
    } catch (err) {
      console.warn("footman playground completion:", err);
    }
  }
}

onEachPage(() => {
  addRunLinks();
  initPlayground();
});
