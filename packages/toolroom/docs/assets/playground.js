/* The footman playground: a real footman in the browser via Pyodide, plus
 * the "run it there" links under every python example in the docs.
 *
 * Loaded on every page (extra_javascript, module + defer). The site uses
 * navigation.instant, so init re-runs on each document$ emission; every
 * step is idempotent and guarded so a failure here can never break a page.
 *
 * Execution model: the browser cannot spawn processes or threads, so the
 * driver installs a sandbox — subprocess children are simulated (exit 0,
 * output labelled `[simulated]`), runs are sequential (`-s`), and
 * parallel() degrades to inline calls. In-process tools are the
 * exception: pytest really runs, inside the page, through the tools
 * bridge. The playground page discloses all of this.
 */

const PYODIDE_URL = "https://cdn.jsdelivr.net/pyodide/v314.0.3/full/pyodide.mjs";
const SITE_ROOT = new URL("..", import.meta.url); // …/assets/ -> site root
const FRAGMENT_MARK = "example: fragment";
const FRESH_MARK = "example: fresh-session";
const REVISION_MARK = "example: revision";

const DEFAULT_FILES = {
  "tasks.py": `from typing import Literal
from footman import fail, run, task
from toolroom import docker, pytest, ruff

@task
def lint(fix: bool = False):
    "Lint the source tree."
    # a typed wrapper: keywords become flags, False is omitted
    ruff.check("src", fix=fix)

@task(serial=True)   # in-process pytest touches the process globals
def test():
    "Run the tests — real pytest, in your browser."
    # a list repeats the flag: -p no:cacheprovider -p no:footman
    pytest("-q", "test_demo.py", p=["no:cacheprovider", "no:footman"])

@task
def deploy(target: Literal["dev", "staging", "prod"],
           regions: list[Literal["eu", "us", "ap"]] | None = None):
    "Ship to an environment."
    run(f"./rollout.sh {target} --regions={','.join(regions or ['eu'])}")

@task
def audit():
    "Refuses on purpose — try it with -k."
    fail("the gate is red — deliberately", code=3)

@task
def ship():
    "Build a command line without running it."
    cmd = docker.compose.up.argv(detach=True)
    print(cmd, "->", cmd.posix())

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

    class _SimulatedPopen:
        # The browser cannot spawn processes; every child succeeds and says
        # what it would have been. In-process tools bypass this entirely.
        def __init__(self, argv, **kwargs):
            self.args = argv
            self.pid = 4242
            self.returncode = 0
            cmd = argv if isinstance(argv, str) else " ".join(argv)
            self._out = "[simulated] " + cmd + chr(10)

        # Keyword-for-keyword what run() calls: it always passes input=
        # (None unless the task feeds the child), so a positional-only
        # signature here breaks every run() in the page.
        def communicate(self, input=None, timeout=None):
            return self._out, ""

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

        def terminate(self):
            pass

        def kill(self):
            pass

    subprocess.Popen = _SimulatedPopen

    # One thread is all the browser has: parallel() runs its callables
    # inline, in order, and a failure still surfaces after the others ran.
    import footman, footman.context

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

def _fm_sandbox_line(line):
    words = line.split()
    if sys.platform == "emscripten" and "-s" not in words and "--sequential" not in words:
        return "-s " + line
    return line

def _fm_invoke(files_json, line, columns=80):
    # The pane's measured width, the way a terminal would report it:
    # shutil.get_terminal_size honours COLUMNS with no tty in sight, so
    # footman's own wrapping and pytest's ruler bars fit the pane.
    os.environ["COLUMNS"] = str(int(columns))
    files = json.loads(files_json)
    for name, content in files.items():
        Path(name).write_text(content, encoding="utf-8")
    try:
        from footman.testing import Runner
        result = Runner().invoke(_fm_sandbox_line(line), tasks=Path("tasks.py"))
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

_fm_manifest = {"code": None, "tree": None}

def _fm_complete(code, line):
    # The real completion hot path over the editor's tasks.py: build the
    # manifest tree once per source text, then every Tab is a pure walk —
    # the same complete() a shell hook consults.
    import types
    from footman import _manifest as manifest, registry
    from footman._complete import complete
    if _fm_manifest["code"] != code:
        module = types.ModuleType("tasks")
        sys.modules["tasks"] = module
        try:
            with registry.capture() as root:
                exec(compile(code, "tasks.py", "exec"), module.__dict__)
            _fm_manifest["tree"] = manifest.build_manifest(root)["tree"]
            _fm_manifest["code"] = code
        except Exception:
            return json.dumps([])
        finally:
            sys.modules.pop("tasks", None)
    words = line.split()
    if not line or line.endswith(" "):
        words.append("")
    out = complete(_fm_manifest["tree"], words)
    if out and out[0].startswith(chr(0)):
        # A sentinel answer (file handoff, dynamic recompute) needs a real
        # shell; the elements after the marker are protocol payload — a
        # partial and an emission prefix — not candidates.
        return json.dumps([])
    return json.dumps(out)
`;

let pyodideReady = null; // one load per browser tab, kept across instant nav
let pytestReady = null;

function loadRuntime(status) {
  if (!pyodideReady) {
    pyodideReady = (async () => {
      status("loading Python — a few seconds, once per visit…");
      const { loadPyodide } = await import(PYODIDE_URL);
      const pyodide = await loadPyodide();
      status("installing footman + toolroom…");
      await pyodide.loadPackage("micropip");
      const micropip = pyodide.pyimport("micropip");
      await micropip.install(["footman", "toolroom"]);
      pyodide.runPython(BOOTSTRAP);
      return pyodide;
    })();
    pyodideReady.catch(() => {
      pyodideReady = null; // a failed load may be retried
    });
  }
  return pyodideReady;
}

function ensurePytest(pyodide, status) {
  if (!pytestReady) {
    pytestReady = (async () => {
      status("installing pytest — first test run only…");
      const micropip = pyodide.pyimport("micropip");
      await micropip.install("pytest");
    })();
    pytestReady.catch(() => {
      pytestReady = null;
    });
  }
  return pytestReady;
}

function initPlayground() {
  const root = document.getElementById("fm-playground");
  if (!root || root.dataset.fmpInit) return;
  root.dataset.fmpInit = "1";

  const code = document.getElementById("fmp-code");
  const args = document.getElementById("fmp-args");
  const runBtn = document.getElementById("fmp-run");
  const out = document.getElementById("fmp-out");
  const status = document.getElementById("fmp-status");
  const tabs = [...root.querySelectorAll(".fmp-tab")];
  const setStatus = (text) => {
    status.textContent = text;
  };

  /* Two files, one textarea: the tab bar decides which one it shows. */
  const files = { ...DEFAULT_FILES };
  let currentFile = "tasks.py";

  // Prefill tasks.py from a "run it there" link, if one brought us here.
  const hash = new URLSearchParams(window.location.hash.slice(1));
  try {
    if (hash.has("code")) files["tasks.py"] = b64decode(hash.get("code"));
  } catch {
    /* a malformed fragment keeps the default */
  }
  args.value = hash.get("cmd") || DEFAULT_ARGS;
  code.value = files[currentFile];
  runBtn.disabled = false;

  function syncFiles() {
    files[currentFile] = code.value;
  }

  function showFile(name) {
    syncFiles();
    currentFile = name;
    code.value = files[name];
    for (const tab of tabs) {
      tab.classList.toggle("fmp-tab-active", tab.dataset.file === name);
    }
    code.focus();
  }

  for (const tab of tabs) {
    tab.addEventListener("click", () => showFile(tab.dataset.file));
  }
  showFile(currentFile);

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
    if (event.key === "Escape") hideCandidates();
  });
  args.addEventListener("input", hideCandidates);
  runBtn.addEventListener("click", run);

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
      const everything = Object.values(files).join("\n") + "\n" + args.value;
      if (/\bpytest\b/.test(everything)) await ensurePytest(pyodide, setStatus);
      setStatus("running…");
      const invoke = pyodide.globals.get("_fm_invoke");
      const raw = invoke(JSON.stringify(files), args.value, measureColumns());
      invoke.destroy?.();
      const result = JSON.parse(raw);
      out.textContent =
        (result.stdout || "") +
        (result.stderr ? (result.stdout ? "\n" : "") + result.stderr : "");
      if (!out.textContent.trim()) out.textContent = "(no output)";
      setStatus(`exit code ${result.exit_code}`);
      status.classList.toggle("fmp-status-failed", result.exit_code !== 0);
    } catch (err) {
      out.textContent = String(err);
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

  function insertCompletion(name) {
    const cursor = args.selectionStart ?? args.value.length;
    const before = args.value.slice(0, cursor);
    const after = args.value.slice(cursor);
    const partial = before.match(/\S*$/)[0];
    const glue = name.endsWith("=") ? "" : " ";
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
      const raw = fn(files["tasks.py"], args.value.slice(0, cursor));
      fn.destroy?.();
      const candidates = JSON.parse(raw);
      const names = candidates.map((c) => c.split("\t")[0]);
      hideCandidates();
      if (!names.length) return;
      if (names.length === 1) {
        insertCompletion(names[0]);
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
          insertCompletion(name);
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
