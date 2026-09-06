# The tools bridge: what it can't say, and why

A guide to the four gaps we've found, the vocabulary needed to talk about
them, and what closing each one would take. Every claim marked **verified**
was run against the real tool on this machine; nothing here is from memory.

---

## 1. The one-paragraph picture

The bridge turns a Python call into a command line, mechanically. Every gap
we've found is the same shape: **the tool needs a token placed or spelled a
particular way, and footman has no way to say it.** There are only four such
things, and each maps to one fix:

| # | What footman can't express | Consequence | Proposed fix |
|---|---|---|---|
| 1 | *Where* flags go relative to positionals | Global options and wrapped commands break | `.opts()` |
| 2 | Whether a flag and its value are **one** token or **two** | Optional-value options and dash-leading values break | always attach |
| 3 | Whether a flag **repeats** (`-vv`) | No keyword form exists | none — use a positional |
| 4 | What positionals a verb even **accepts** | Stray/misnamed arguments pass silently | `/` and `*` in the stub |

That's the whole picture. The rest of this document is the vocabulary and
the evidence.

---

## 2. Vocabulary

Terms are defined here in the order they're needed. Examples use real tools.

**Command line / argv.** The list of strings a program receives. footman
never goes through a shell, so it builds this list directly — quoting and
word-splitting never enter into it.

```
["ruff", "check", "src", "--fix"]
```

**Positional argument.** A value whose meaning comes from *where* it sits,
not from a name. `src` below is positional — it's the path to check.

```
ruff check src
           ^^^ positional
```

**Option** (also called a **flag**). A named argument introduced by dashes.
Two spellings:

- **Long option** — two dashes, a whole word: `--fix`, `--select`
- **Short option** — one dash, a single letter: `-q`, `-n`

Most options have both, and they mean the same thing: `-q` and `--quiet`.

**Switch** (or **boolean flag**). An option that takes no value. Its presence
*is* the information.

```
ruff check src --fix
               ^^^^^ switch
```

**Value.** What a non-switch option takes.

```
ruff check src --select E
                        ^ the value of --select
```

**Separated form vs attached form.** The two ways to write an option and its
value. This distinction is the whole of gap 2, so it's worth being exact:

| | Long option | Short option |
|---|---|---|
| separated | `--select E` (two tokens) | `-n 1` (two tokens) |
| attached | `--select=E` (one token) | `-n1` (one token, **no `=`**) |

Note the asymmetry: long options attach with `=`, short options attach by
concatenation. **Verified:** `git log -n=1` fails with `fatal: '=1': not an
integer`, while `-n1` and `-n 1` both work.

**Subcommand** (footman calls these **verbs**). A word that selects a mode of
the tool, before any of that mode's options.

```
ruff check src
     ^^^^^ subcommand
docker compose up
       ^^^^^^^ ^^ nested subcommands
```

**Global option.** An option belonging to the *tool*, not to a subcommand.
Some tools require these **before** the subcommand.

```
docker --debug ps
       ^^^^^^^ global — belongs to docker, not to ps
```

**Wrapped command.** A subcommand whose job is to run *another* program.
Everything after it belongs to that other program.

```
uv run pytest -q
       ^^^^^^^^^ belongs to pytest, not to uv
```

**Negatable option.** An option with an explicit "off" spelling. The off
spelling is not always `--no-<name>` — this is why `off` exists and why
footman extracts the real spelling rather than guessing.

```
mkdocs build --clean     # on
mkdocs build --dirty     # off  (NOT --no-clean, which mkdocs rejects)
```

**Optional-value option.** An option whose value *may* be omitted. Tools
write this with brackets. Central to gap 2:

```
git commit -S, --[no-]gpg-sign[=<key-id>]
                             ^^^^^^^^^^^^ the value is optional
```

**Counted option.** An option that repeats to mean "more". Gap 3:

```
pytest -vv        # more verbose than -v
```

**Stub** (`.pyi`). A file describing types only. Your editor and the type
checker read it; Python never imports it at run time. footman generates one
per tool from the tool's own `--help`. **A stub cannot affect what command
gets run** — it only affects what your editor suggests and what the type
checker accepts. Worth holding onto: it explains why some gaps can be fixed
in the stub and others can't.

---

## 3. How footman translates today

The rules, in full:

| You write | footman emits |
|---|---|
| `tools.ruff.check` | `ruff check` (attribute chain → subcommands) |
| `"src"` | `src` (positional strings pass through untouched) |
| `fix=True` | `--fix` |
| `fix=False` or `fix=None` | *nothing* — omitted |
| `fix=off` | that tool's real negation (`--no-fix`, or `--dirty` for mkdocs `clean`) |
| `select="E"` | `--select E` |
| `select=["E", "F"]` | `--select E --select F` (a list repeats the option) |
| `select=[]` | *nothing* — so a task parameter's default flows straight through |
| `k="expr"` | `-k expr` (a single-letter name is a short option) |
| `output_format=…` | `--output-format …` (underscore → dash) |
| `global_=True` | `--global` (trailing underscore escapes Python keywords) |

Worked example:

```python
tools.ruff.check("src", "tests", fix=True, select=["E", "F"])
```
```
["ruff", "check", "src", "tests", "--fix", "--select", "E", "--select", "F"]
  └─tool─┘└verb─┘ └── positionals ──┘ └──────── flags, last ────────┘
```

Note the shape: **base, then positionals, then flags.** That ordering is
gap 1.

---

## 4. The four gaps

### Gap 1 — footman can't say *where* flags go

Flags are always emitted last. Two situations where that's wrong.

**Global options.** Some tools require them before the subcommand.

```python
tools.docker.ps(debug=True)  # → docker ps --debug
```
```
docker --debug ps    ✓ works
docker ps --debug    ✗ unknown flag: --debug          [verified]
```

This is a hole I opened this afternoon: the generated `_Docker` stub
advertises ten globals (`--host`, `--context`, `--debug`, `--tls*`) that are
unreachable through the keyword form it's suggesting. They only work as
literal positional strings: `tools.docker("--debug", "ps")`.

Not every tool cares. **Verified:** `uv --directory . run` and
`uv run --directory .` both work, because clap (the Rust library uv and ruff
use) propagates globals to subcommands. cobra (docker's library) doesn't.

**Wrapped commands.** Worse, because it fails *silently*.

```python
tools.uv.run("pytest", "-q", frozen=True)  # → uv run pytest -q --frozen
```

**Verified:** `uv run python -c … --frozen` puts `--frozen` in the *child's*
`sys.argv`. uv never sees it, nothing errors, and you simply don't get the
flag. Same shape breaks `coverage run -m pytest --source x` and
`docker run alpine echo hi --rm`.

**The fix — your idea, `tools.uv(frozen=True).run(...)`.** The instinct is
right; the literal spelling collides with `__call__`, which already means
*execute* — `tools.uv(frozen=True)` runs `uv --frozen` today and returns an
exit code. So it needs a different name, but the mechanism already exists for
literal strings (`Tool("docker", "--host", "x")` puts them before the verb):

```python
tools.docker.opts(host="tcp://x").compose.up(detach=True)
# → docker --host tcp://x compose up --detach

tools.docker.compose.opts(progress="plain").up(detach=True)  # mid-chain
tools.uv.run.opts(frozen=True)("pytest", "-q")  # → uv run --frozen pytest -q
```

In the stub, `def opts(self, *, host: _Value = ..., debug: _Flag = ...) -> _Docker`
— so the globals finally complete, and returning the same class keeps the
chain type-checked. Nothing that works today changes.

---

### Gap 2 — footman can't say whether a flag and its value are one token

`_flags` always emits two argv entries. There is no code path producing one.

```python
_flags({"abbrev": 4}, "git")   →  ["--abbrev", "4"]      # never ["--abbrev=4"]
```

Usually the two forms are interchangeable. Two situations where they aren't:

**(a) The option's value is optional.** The parser can't tell whether the
next token is the value or the next positional, so it assumes the flag is
bare and the token is a positional:

```
git log -n 1 --abbrev 4 --format=%h   →  fatal: ambiguous argument '4'   [verified]
git log -n 1 --abbrev=4 --format=%h   →  6ef7                            [verified]
```

**(b) The value starts with a dash.** The parser reads it as another flag:

```
git log -n 1 --format -%h  →  fatal: unrecognized argument: --format     [verified]
git log -n 1 --format=-%h  →  -6ef77c4                                   [verified]
ruff check --select -E     →  error: unexpected argument '-E' found      [verified]
```

**A related extraction bug, also mine.** The extractor doesn't understand the
`[=<x>]` notation either — it expects a space or `=` *separating* the flag
from its placeholder, but git writes the placeholder flush against the flag.
So those options are read as switches, and the stub types them as booleans:

```
gpg_sign: _Flag          # actually takes an optional key-id
untracked_files: _Flag   # actually takes all|normal|no
```

Which means the stub currently **rejects** `untracked_files="all"` — correct
usage. That's the stub forbidding working code, the one thing its contract
says it must never do. This needs fixing whatever we decide about emission.

**The fix.** Two halves, each verified:

- long option with a value → `--flag=value`. Accepted by every family
  tested: ruff and uv (clap), docker (cobra), mkdocs (click), coverage
  (optparse), git. Safe when the value itself contains `=` —
  `docker run --env=FOO=bar` and `--env FOO=bar` both print `bar`, because
  parsers split on the first `=`.
- short option → concatenate, never `=` (`-n1`, not `-n=1`).

**Always** vs **only when needed** decides whether this closes fully:

- *Always attach* closes both cases for **every** tool, including ones
  footman has never heard of (`tools.terraform`, `tools.helm`). One rule, no
  table. Cost: every generated command line changes shape, so footman's own
  assertions (`"--select E"` → `"--select=E"`) churn, and so would any user's
  `recording()` tests.
- *Only when needed* changes no existing command line, but the
  optional-value half needs a baked per-tool table and therefore only ever
  covers the thirteen curated tools. An undeclared tool with `--flag[=x]`
  stays broken forever.

Only the first actually closes it.

---

### Gap 3 — footman can't say that a flag repeats

```
pytest -vv        # works                                    [verified]
```

No keyword produces it. `v=True` gives `-v` once; `v=2` gives `-v 2`, which
**verified** makes pytest treat `2` as a test path and collect nothing.

In practice you'd write `verbose=True` and never meet this, so I'd leave it.
The positional form is the honest answer: `tools.pytest("-vv")`.

---

### Gap 4 — footman can't say what positionals a verb accepts

Everything is `*args: str`, so anything goes:

```python
tools.mkdocs.build("site")  # → mkdocs build site       (mkdocs takes no positionals)
tools.docker.run(image="alpine")  # → docker run --image alpine  (IMAGE is positional!)
```

Both silently produce a wrong command. But the tools *tell us* their shape in
their usage line, and it discriminates cleanly — **verified**, read from the
installed tools:

```
mkdocs build       [OPTIONS]                              → no positionals
uv sync            [OPTIONS]                              → no positionals
ruff check         [OPTIONS] [FILES]...                   → repeated positionals
docker compose up  [OPTIONS] [SERVICE...]                 → repeated positionals
docker run         [OPTIONS] IMAGE [COMMAND] [ARG...]     → required, then a wrapped command
git clone          [<options>] [--] <repo> [<dir>]        → required, then optional
uv run             [OPTIONS] [COMMAND]                    → a wrapped command
coverage run       [options] <pyfile> [program options]   → a wrapped command
```

**The fix — Python's `/` and `*` markers**, which say where positional
arguments stop and keyword arguments begin:

```python
def build(self, *, strict: _Flag = ..., **flags: Any) -> int: ...


#              ^ nothing before this: no positionals accepted


def run(self, image: str, /, *command: str, rm: _Flag = ...) -> int: ...


#                        ^ image must be positional, never image="alpine"
```

**Verified** with basedpyright: a stray positional gives *"Expected 0
positional arguments"*, and `image="alpine"` gives *"Expected 1 more
positional argument"*.

This forbids things — but only things that genuinely don't work, the same
justification as a `Literal` rejecting a value the tool would reject. It must
be conservative: `git commit`'s usage line is
`[-a | --interactive | --patch] [-s] [-v] [-u[<mode>]] [--amend]`, with no
clean shape, so it keeps `*args: str` and forbids nothing. Roughly two-thirds
of sampled verbs parse cleanly.

**A bonus worth noticing.** Look again at the usage lines above: `[COMMAND]`,
`[ARG...]`, `[program options]` mark exactly the verbs that wrap another
program — precisely the set where gap 1 fails silently. So that set can be
*extracted* rather than decided, and the bridge can place flags before
positionals for those verbs only. That's the same pattern that solved `off`:
ask the tool, bake the exception, leave everything else alone.

---

## 5. Where this leaves us

| Gap | Fix | Closes it for | Changes existing behaviour? |
|---|---|---|---|
| 1. Flag placement | `.opts()` + extracted wrapper-verb set | all tools | no — new method only |
| 2. Attachment | always attach long options | all tools | **yes** — every command line's shape |
| 3. Repetition | none; use a positional | — | no |
| 4. Positional shape | `/` and `*` from the usage line | curated tools | no at run time; stub gets stricter |

Two of these need nothing from you — gap 4 and the extraction bug in gap 2
are plainly bugs. Gap 1's `.opts()` is additive. **The only real decision is
gap 2's "always attach"**, because it changes the shape of every command
footman generates.

My recommendation is to take it. Pre-1.0, the churn is footman's own recorded
assertions plus a CHANGELOG note, and it retires an entire category of bug
rather than a list of instances — including for tools footman will never
know about.
