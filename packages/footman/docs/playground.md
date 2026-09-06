---
hide:
  - navigation
  - toc
---

# Playground

A real footman, in your browser. The editor below is a `tasks.py`; the
prompt is `fm`. Python (and footman itself) load into the page via
[Pyodide](https://pyodide.org) as soon as you arrive — nothing is installed on your
machine, and nothing you type leaves it.

The browser has no processes and one thread, so
every `run("…")` child is **simulated** — it succeeds and its output says
`[simulated]` — and runs are sequential. A few well-known reads
(`git branch`, `git tag`) answer with plausible canned output instead, so
a completer that parses a child's stdout has real names to offer. `run(shell=True)` resolves to a
stand-in shell, since the simulated child never executes it. The page's
filesystem holds only the editor's files, so a path requirement
(`exists`/`isfile`/`isdir`) **passes without looking** — nothing it could
ask for is there. A tab named `stdin` is the pipe, not a file: its text
feeds the run's stdin, and stdin-bound parameters read it exactly as they
would a real pipe. Everything
else is the real thing: parsing, eager validation, taught errors,
scheduling, `--json`, `--dry-run` plans — and **`fm test` runs the real
pytest**, on toolroom's in-process path, right here in the page (the page
provides a quiet `pytest.ini` that keeps pytest's cache files and
footman's own pytest plugin out of the run; add your own tab to
override it). A browser has no processes to
spawn, so the page is that path's proof by extremes: a true twin,
not a fallback. The prompt completes
too: press <kbd>Tab</kbd> and the candidates come from the same manifest
walk a shell completion hook consults, rebuilt from whatever the editor
says — and a dynamic `suggest()` completer runs **fresh on every Tab**,
in the page, exactly as a shell would respawn it. Prompts work too:
`ask()`, `prompt()`, `confirm()`, and `select()` open a **browser
dialog** standing in for the terminal — a menu's numbered lines become
the dialog text, Cancel reads as end-of-input, and a secret is typed
unmasked (this is a playground, not a vault). The **editor** completes
too: type `.` after a toolroom handle (or press <kbd>Ctrl-Space</kbd>
anywhere) and the menu comes from the interpreter — real methods from
the typed stubs, docstrings included — and **rest the pointer on a
name** for its signature and docstring, no menu required.

The output pane is a session transcript: every run appends its prompt
line and output, in footman's own colours, and **Clear** starts over.

Press **Run**. The gate fails — one of the tests is wrong on purpose.
Read pytest's diff, fix `fizzbuzz` (or the test), and run it green. Then
try `-k check audit` to watch keep-going collect every failure, and
`deploy produ` to read a taught error. Completion knows the grammar,
not just the words: type `deploy prod --regions=eu,` and press
<kbd>Tab</kbd> to watch a comma-separated list complete item by item.

<div id="fm-playground" markdown="0">
  <div class="fmp-gallery" hidden>
    <select id="fmp-example" aria-label="example"></select>
    <button id="fmp-reset" type="button" class="fmp-secondary"
      title="Restore this example's files">Reset</button>
    <span id="fmp-blurb"></span>
  </div>
  <div class="fmp-pane fmp-editor-pane">
    <div class="fmp-label" role="tablist"></div>
    <textarea id="fmp-code" spellcheck="false" autocomplete="off"
      autocapitalize="off" aria-label="editor"></textarea>
  </div>
  <div class="fmp-pane fmp-output-pane">
    <div class="fmp-toolbar">
      <span class="fmp-prompt">fm</span>
      <input id="fmp-args" spellcheck="false"
        autocomplete="off" autocapitalize="off"
        aria-label="fm command line" />
      <button id="fmp-menu" type="button" class="fmp-secondary" hidden
        title="The example's commands" aria-label="curated commands">▾</button>
      <button id="fmp-run" disabled>Run</button>
      <button id="fmp-clear" type="button" class="fmp-secondary"
        title="Clear the transcript">Clear</button>
      <div id="fmp-cmdmenu" hidden></div>
    </div>
    <div id="fmp-complete" hidden></div>
    <div id="fmp-out" aria-live="polite"></div>
    <div class="fmp-status" id="fmp-status">Python is loading — a few
      seconds; ~15&nbsp;MB on your first visit, cached after.</div>
  </div>
</div>

Every Python example in these docs has a **run it there** link that brings
the page's code straight into this editor, the prompt already holding a
command that runs against it — the examples are tested against every
commit, so what you paste is what runs.
