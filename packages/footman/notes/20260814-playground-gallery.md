# The playground gallery

The playground works and is honest about its sandbox, but it is a demo:
one hardcoded sample, a plain textarea, an output pane that forgets the
previous run. This note plans the walk from demo to gallery — a curated,
categorised set of examples covering every feature footman has, each with
command lines that make its point, all tested against every commit like
the rest of the docs.

Decided with Willem 2026-08-14. Phases land separately; the note is law
for scope and vocabulary until it says otherwise.

## The shape

An **examples registry** — `docs/assets/examples.json` — of self-contained
entries:

```json
{
  "id": "validation/checks",
  "title": "Belt-and-braces deploy",
  "category": "Validation",
  "blurb": "Markers stack; each refusal is a taught error.",
  "files": {"tasks.py": "…"},
  "commands": [
    {"line": "deploy config.toml 1.2.3", "note": "everything passes"},
    {"line": "deploy config.toml nope", "note": "check(fn) refuses"}
  ]
}
```

The page renders a grouped dropdown; selecting an example swaps the editor
tabs and shows its command lines as clickable chips under the prompt, each
chip carrying its note. No navigation, so the loaded Pyodide instance is
reused. JSON, not a JS module, because two consumers read it: the page
(fetch, same-origin) and pytest (`json.loads`, no regex extraction).

"Run it there" fragment links coexist: a `#code=` fragment shows as a
"from the docs" pseudo-entry; gallery entries are linkable as
`#example=<id>` so docs pages can deep-link into them.

**Testing is the point, not an afterthought**: the `_FM_PLAYGROUND_SIM`
rehearsal harness drives every entry's every command line through the
shipped driver in CPython, asserting exit codes and output markers. A
**coverage guard** maps a feature checklist to example ids and fails when
a feature has no example — "cover everything" enforced, not aspirational.

## Feature categories to cover

1. **Basics** — tasks, groups, `@group.default`, docstrings → `--list` /
   `--help` / `--describe`, dotted addressing.
2. **Typing** — int/bool/Path/enum/Literal, unions, comma-split lists,
   dict params, `Arg[T]`.
3. **Validation** — `between`, `check(fn)` + siblings, `env()`,
   `default(fn)`, `doc()`, path requirements (blurb discloses the sandbox
   passes them).
4. **Variadic & forwarding** — `*args`, `--` passthrough, `forward` /
   `Forward[T]`.
5. **Scheduling** — pre/post, chains, `-k` keep-going, `.opts()`,
   fail-fast.
6. **Run & tools** — `run()`, toolroom wrappers, `--dry-run`, `fail()`,
   exit codes, `shell=True` (needs the shell-resolution dummy below).
7. **Structured results** — returned values, `Stdout[T]`, `--json`,
   stdin binding (needs the stdin tab below).
8. **Config** — a `footman.toml` tab showing the cascade.
9. **Composition** — `include()` across two file tabs.
10. **Completion** — static choices/lists in-page; dynamic `suggest()`
    once the spike below lands.

## Sandbox extensions

- **Shell resolution dummy** (like the path-requirement dummy): the
  simulated child never executes, so `_resolve_shell` can be patched to a
  fixed `["/bin/sh", "-c"]` under emscripten and `shell=True` examples
  run with `[simulated]` output.
- **stdin as a file tab** (Willem: yes): a `stdin` tab, prepopulated with
  JSON payloads by examples that bind from the boundary; `_fm_invoke`
  passes its content as the invocation's stdin. Empty tab = no pipe.
- **Dynamic completion** — LANDED (spike 1, 2026-08-14): `_fm_complete`
  takes the whole files payload (writes the non-tasks tabs first, so a
  file-reading completer sees the editor's now), and on the `_DYNAMIC`
  sentinel mirrors the `_suggest` child in place — same owner walk, same
  chatter muting, same prefix + partial-filtered emission. Verified
  against the released 0.40.0 wheel. File-handoff sentinels still answer
  nothing (no filename completion to hand off to). The Completion
  example's `switch` completes from a `branches.txt` tab, live.
- **ask() prompts** — LANDED (spike 2, 2026-08-14): the seams are
  `context._stdin_is_tty` (the gate every prompt consults) and the
  `real_stdin`/`real_stderr` pair — the sandbox's stand-in stream's
  readline() IS one `js.window.prompt()`, with everything the framework
  wrote to the real stderr since the last read (the question, a menu's
  numbered lines) becoming the dialog text. Cancel reads as EOF, so a
  defaultless ask fails with footman's own taught message. getpass is
  patched for secrets (unmasked in a dialog — disclosed). Under
  `_FM_PLAYGROUND_SIM` the answers come from a canned queue
  (`_FM_PLAYGROUND_PROMPTS`), so rehearsals drive the same seam; gallery
  commands may declare `prompts`. The Input example (`release`) shows
  ask-when-unanswered and supplied-means-silent. Verified against the
  released 0.40.0 wheel. An in-page modal (instead of the native
  dialog) remains possible later; the seam stays.
- **Editor completion from the interpreter** — LANDED (spike 3,
  2026-08-14): `autocompletion({override})` wired back into the mount
  with a source that asks jedi over the buffer (footman and toolroom
  importable — `ruff.che` completes from the actual typed stubs,
  docstrings riding as the CM info panel). Latency tamed by triggering:
  auto only right after a `.` (and never before the runtime is loaded —
  a typed dot must not start the Pyodide download), Ctrl-Space anywhere
  and it may pay the load. jedi installs on first use through the
  `packages` machinery. GOTCHA encoded in a comment + test: jedi's
  default environment inference shells out to a python subprocess for
  sys.path — the simulated child feeds it garbage and the browser has
  none — so the source uses `jedi.InterpreterEnvironment()`. Signature
  help on hover remains a possible later layer over the same source.

Out of scope, disclosed on the page: PEP 723 re-exec (no processes),
`fetch()` (no cross-origin network), the profile plugin.

## Polish (phase 1, no content changes)

- **CodeMirror 6**, vendored as one bundle
  (`docs/assets/vendor/codemirror.js`, recipe in `vendor/codemirror/`),
  dynamically imported only on the playground page so the module-eval
  guard test and every other docs page stay untouched. Fallback on load
  failure: the plain textarea, exactly as today. MEASURED: the CDN route
  (three jsdelivr `+esm` entry points, pinned) fails — each got its own
  `@codemirror/state` instance and CM rejected the extension set
  ("Unrecognized extension value… multiple instances of
  @codemirror/state"). One esbuild bundle is one instance set by
  construction, and drops the second runtime CDN.
- **ANSI-coloured output**: the sandbox injects `--color=always` (like it
  injects `-s`, emscripten-only), and the page renders SGR codes to
  spans. The pane stops looking like a log file.
- **Session transcript**: each run appends `fm <line>` + output instead
  of replacing the pane — a real session with scrollback and a Clear
  button.
- **Dynamic tab bar** generated from the example's files (was hardcoded
  to two).
- Per-visit memory of edits per example; a reset-example affordance;
  a share link encoding files+command into the fragment (the b64
  machinery exists).

## Phases

1. **Mechanics** — CM6, dynamic tabs, ANSI pane, transcript. LANDED
   (#429, #430; then the palette unification and the quiet pytest.ini).
2. **Gallery** — registry, dropdown, chips, fragment interplay,
   rehearsal + coverage-guard tests. LANDED (#431), seeded with Basics /
   Validation / Variadic.
3. **Content & sandbox** — the ten categories; shell dummy; stdin tab;
   the spikes (dynamic completion, ask(), editor completion); and a
   **`packages` field per registry entry** (Willem 2026-08-14):
   micropip deps an example declares, installed on its first run the way
   pytest already is — each example carries its own install cost and the
   default page stays light. The rehearsal harness needs the same
   packages in the docs test group, so CI proves each one works in
   CPython before it ships.
4. **Stretch** — branded-prompt example (`App(...)` renames the prompt),
   monorepo cascade tabs, simulated `fetch()`.

## Live-tool menu (parked, all liked — sequence undecided)

Everything pure-Python (or Pyodide-built) runs REAL in the page, like
pytest. Candidates, roughly by wow-per-effort: coverage + pytest-cov
(real missed lines in the browser); hypothesis (a property test finds
the fizzbuzz counterexample live); black / isort `--check --diff` (and
with **FS→editor sync-back** after a run, a formatter visibly rewrites
the open tab — an enabler worth building on its own); pyflakes /
pycodestyle (real linting where ruff, being Rust, cannot); doctest and
unittest (stdlib, zero install); rich (colour tables through the ANSI
transcript; the `@requires_dep` pattern exists); timeit + statistics (a
bench that really measures); ast (a ten-line custom check — no tool
needed, a task is a function); sqlite3 + csv + json pipelines (pairs
with the stdin tab and `Stdout[T]`/`--json`). Not worth chasing: mypy
(wasm-slow), numpy/pandas (heavy, off-topic), anything Rust/binary
with no Pyodide build (ruff, uv).

## The emscripten gap (learned 2026-08-16)

The CPython rehearsals share the sandbox but not the PLATFORM: the
simulated Popen answered str unconditionally, and platform.platform()
only calls the .decode()-ing _syscmd_file on emscripten's generic
branch — green everywhere CI runs, dead in the page. The proof rig
that named it lives in the day's session scratch: **Pyodide in Node**
(npm i pyodide, load the shipped BOOTSTRAP, call _fm_invoke) — real
emscripten, real micropip installs, scriptable. Candidate follow-up: a
browser-parity CI rung on that rig (weigh the pyodide download against
what only it can catch).

Second flavour of the same gap, found the same day: **Pyodide bundles
its own wheels and micropip prefers them over PyPI** — the page ran
jedi 0.19.2 (bundled) while every CPython rehearsal ran 0.20.0, and
0.19.2 resolves footman's annotated `task` instance to a bare
statement, no signature, no doc. The fix is a floor the bundled wheel
cannot satisfy (`jedi>=0.20`), which forces the PyPI line. Rule of
thumb: any package the page installs should pin PAST the Pyodide
distribution's bundled version, or the page and the rehearsals run
different code.

## The released-wheel gap (learned 2026-08-14)

The page installs footman FROM PYPI; the rehearsals run the SOURCE
TREE. The variadic example shipped green against source and broke in
the browser against 0.39.1, which predated the #428 executor fix —
v0.40.0 exists because of it. Standing constraint: **gallery examples
may only depend on released behaviour**, or must wait for the release
that carries their fix. Candidate guard for phase 3: a rehearsal
variant that drives the gallery against the released wheel in a venv.

## Decided

- CodeMirror over a hand-rolled textarea-overlay highlighter: an editor
  has fiddly failure modes (scroll sync, iOS composition) already solved
  upstream, and the page already accepts one pinned CDN runtime.
- Registry is JSON in `docs/assets/`, dual-read by page and pytest.
- ANSI colour and the transcript are core, not garnish.
- stdin arrives as a tab, not a separate widget, so examples can ship
  prepopulated payloads.

## Open

- Whether `#example=` deep links should replace some "run it there"
  fragment links in docs pages, or the two spellings live side by side
  indefinitely.
- Whether the Pyodide instance truly survives instant navigation from a
  docs page (module-level `pyodideReady` says it should; verify).
