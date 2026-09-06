---
icon: lucide/joystick
hide:
  - navigation
  - toc
---

# Playground

The tool room, live in your browser. The editor below is a `tasks.py`
whose tool calls all go through toolroom's typed handles; the prompt is
`fm` — [footman](https://willemkokke.github.io/footman/), toolroom's
host, which the handles detect and route through on every call. Python
(and both packages) load into the page via
[Pyodide](https://pyodide.org) on first use — nothing is installed on
your machine, and nothing you type leaves it.

The browser sandbox, said plainly: it has no processes and one thread,
so every spawned child is **simulated** — it succeeds and its output
says `[simulated]` — and runs are sequential. Everything else is the
real thing: the flag translation, `.argv` building whole command lines
without running anything, taught errors, `--json`, `--dry-run` plans —
and **`fm test` runs the real pytest**, in-process through the handles,
right here in the page. The prompt completes too: press <kbd>Tab</kbd>
and the candidates come from footman's manifest walk, rebuilt from
whatever the editor says.

Press **Run**. The gate fails — one of the tests is wrong on purpose.
Read pytest's diff, fix `fizzbuzz` (or the test), and run it green.
Then try `fm ship` to watch `.argv` build a `docker compose` line
without a docker in sight, `--dry-run lint --fix` to see the exact
command a flag becomes, and `deploy produ` to read a taught error.

<div id="fm-playground" markdown="0">
  <div class="fmp-pane fmp-editor-pane">
    <div class="fmp-label" role="tablist">
      <button class="fmp-tab" role="tab" data-file="tasks.py">tasks.py</button>
      <button class="fmp-tab" role="tab" data-file="test_demo.py">test_demo.py</button>
    </div>
    <textarea id="fmp-code" spellcheck="false" autocomplete="off"
      autocapitalize="off" aria-label="editor"></textarea>
  </div>
  <div class="fmp-pane fmp-output-pane">
    <div class="fmp-toolbar">
      <span class="fmp-prompt">fm</span>
      <input id="fmp-args" spellcheck="false" autocomplete="off"
        autocapitalize="off" aria-label="fm command line" />
      <button id="fmp-run" disabled>Run</button>
    </div>
    <div id="fmp-complete" hidden></div>
    <pre id="fmp-out" aria-live="polite"></pre>
    <div class="fmp-status" id="fmp-status">Python loads when you first run —
      a few seconds and ~15&nbsp;MB, once per visit.</div>
  </div>
</div>
