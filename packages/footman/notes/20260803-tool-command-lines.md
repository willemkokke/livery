# Getting a tool's command line out of the bridge

Status: design revised 2026-08-05 and built. The first design (`argv(fmt=…)`
as a leading chain modifier) was fully drafted and then replaced before
landing — it is preserved under Rejected with the reasons, because the
revision is best understood as an argument against it.

## What it is

`.argv` slots in right before the parentheses and the call **builds** its
command line instead of running it, using the bridge's ordinary typed
surface:

```python
cmd = mkdocs.gh_deploy.argv(force=True, remote_branch="gh-pages")
ssh("deploy@host", cmd.posix(), p=2222)
```

The governing criterion: **if the command has to be written differently
between invoking it and generating its argv, we are not improving
things.** The edit distance between the two is one word: insert `.argv`
at the parentheses you are already looking at, delete it to run again.
Same verb, same flags, same completion, same type errors — literally the
same signature, since `.argv`'s call *is* the verb's call with a
different return type.

## Why: nesting

A remote command is often itself a remote command. Every hop re-quotes
the whole payload, and POSIX single-quote escaping (`'"'"'`) compounds
per level:

```text
L0  docker compose up --detach --file=/srv/app/compose.yml
L1  ssh app@inner 'docker compose up --detach --file=/srv/app/compose.yml'
L2  ssh jump@edge 'ssh app@inner '"'"'docker compose up …'"'"''
```

With a spaced argument it is worse — `git commit -m 'ship 1.2.0'` at two
hops needs sixteen consecutive quote characters in one run. That line
cannot be written by hand, cannot be reviewed in a diff, and cannot be
edited safely once written. Composing it can be:

```python
inner = docker.compose.up.argv(detach=True, file="/srv/app/compose.yml")
middle = ssh.argv("app@inner", inner.posix())
ssh("jump@edge", middle.posix())
```

Each `.posix()` sits at exactly the machine boundary it quotes for, and
quoting an already-quoted payload quotes it once more — which is exactly
what each further hop needs.

## The surface

`.argv` is a **property**, not a call — there is nothing to configure, so
there are no parentheses of its own, and it composes with the verb's call
in a single pair. It lives wherever a `__call__` does — the tool class and
every verb class, one rule, no exception. 22 of the 36 stubbed tools have
no subcommands at all (`pytest`, `mypy`, `python`, every shell, and `ssh`
itself), so a verb-only `argv` would exclude 61% of the surface including
the tool the design exists to feed.

Position follows `.opts()`'s convention: valid at any point before the
terminal call, propagating down the chain (`docker.argv.compose.up(…)`
builds the same tokens). The docs spell it **right before the
parentheses**, where it reads in command order and where the edit
run→build actually happens. No position needs banning, because there is no
second pair of parentheses anywhere — the earlier design's spelling rules
existed to manage its `fmt` argument, and died with it.

Like `opts`, `flags`, `at` and `version`, the name costs one word in the
subcommand namespace — `__getattr__` chains any attribute as a verb.
Measured across all 208 stubbed verbs, `argv` collides with none.

## The value

An `Argv`: a `list[str]` subclass holding the **raw tokens**, always — the
one shape of a command with no shell in it. It indexes, slices, iterates
and compares exactly like the list it is, and passing it on is plain
Python rather than protocol:

- `run(cmd)` — an `Argv` is precisely `run()`'s input type; zero adapter.
- `uv.run("--", *cmd)` — a wrapper takes the tokens splatted, the spelling
  Python already has for "these are N arguments".
- `shlex.join(cmd)`, `subprocess.list2cmdline(cmd)` — stdlib interop for
  free, because it *is* a list.

Serialisation happens at the boundary, on the value:

| method | contents | for |
|---|---|---|
| `.posix()` | one `str`, `shlex`-quoted | `sh`/`bash`/`zsh` parsing the payload |
| `.windows()` | one `str`, `list2cmdline`-quoted | `CreateProcess`/cmd parsing it |

Two named methods rather than a `fmt=` enum: autocomplete finds them, a
typo is an `AttributeError`, and there is no invalid-value branch to
teach. The tokens stay tokens until the caller says which shell they are
about to cross into — so **one build serves every consumer** (`run(cmd)`
locally, `*cmd` into a wrapper, `.posix()` across a boundary), where a
format chosen at build time bakes the consumer into the value and cannot
be undone (`shlex` cannot reliably take a `list2cmdline` string apart).

## Why the caller names the shell

footman never sniffs the target shell. Three mechanics make that
necessary:

1. footman spawns with `subprocess.Popen(argv, …)` — a list, never
   `shell=` (`context.py`). No local shell parses anything, so the quoting
   in `.raw`/`.command` is display only.
2. `ssh` joins its remaining arguments with single spaces and hands
   **one string** to the remote shell, which re-splits it. Argument
   boundaries do not survive as argv; only characters baked into the
   payload survive.
3. So a remote command must be pre-quoted for the *remote* shell, and
   the only quoting that matters is what is inside the payload string.

footman cannot infer that shell: the same handle can target different
hosts across calls; probing would mean a network round trip during argv
construction; the OS does not determine the shell (Windows OpenSSH's
default is configurable, and a POSIX box may exec something that is not
`sh`); and the payload may reach no shell at all — a forced command in
`authorized_keys`, `ssh -T`, `docker exec`, a systemd unit.

An auto-detect would be wrong in all four cases and would fail silently
in the dangerous direction. `_shell_quote` (`context.py`) branches on the
**local** platform, which is right for a `--verbose` line that pastes
back into the shell you are standing in and wrong for anything crossing a
machine boundary:

| payload | POSIX (`shlex.quote`) | Windows (`list2cmdline`) | at a POSIX remote |
|---|---|---|---|
| `a message with spaces` | `'a message with spaces'` | `"a message with spaces"` | survives |
| `cost $HOME today` | `'cost $HOME today'` | `"cost $HOME today"` | `$HOME` expands |
| ``now `whoami` here`` | ``'now `whoami` here'`` | ``"now `whoami` here"`` | backticks execute |
| `back\slash` | `'back\slash'` | `back\slash` (bare) | `\s` eaten |

`list2cmdline` has no reason to escape `$`, backticks or `\`, because cmd
does not treat them as special. **`.posix()`/`.windows()` never call
`_shell_quote`** — they are `shlex.join` and `subprocess.list2cmdline`,
both stdlib and available on every platform, chosen by the method name
alone.

## The pieces

### 1. The accessor

`Tool.argv` (property) returns an `ArgvTool` — the same handle, class-
propagated down `_sub` so verbs chained through it keep building, whose
`__call__` returns `Argv(self._tokens(args, kwargs))`. `_tokens` is the
**same** translation `__call__` spawns from (`_flags`, `_positionals`,
the wrapper-verb ordering), so a built line cannot drift from what a real
call would run.

Two deliberate divergences from a spawned line, one docstring line each:
the build is colour-free (the forced-colour switch is injected at spawn
time only), and it leads with the tool's *name*, not `self._path` — a
built line is made to be handed somewhere a resolved local path means
nothing. Building consumes nothing: a pending `.opts(input=…)` payload
stays armed, because no child was fed.

### 2. The container refusal

`positionals = list(map(str, args))` used to turn `["src", "tests"]` into
the single token `"['src', 'tests']"`, which survived to the tool and
failed there — late, confusing, far from the call. Now a bare `list`,
`tuple`, `set`, `frozenset` or `dict` in a positional refuses at the call
with a taught error naming the splat spelling. Concrete containers only,
never "iterable" — a `str` is iterable and a looser test would explode it
into characters. Sets also refuse because a splat of an unordered value
would be nondeterministic; `dict` because a mapping has no positional
reading at all.

A bare `Argv` refuses too, with its own lesson: one positional slot is
ambiguous between *N tokens* (`*cmd`, what a wrapper wants) and *one
quoted line* (`cmd.posix()`, what an ssh payload is), and the two
disagree exactly when it hurts — on spaced arguments. Making the
distinction unspellable-by-accident is the safety property: the earlier
design's implicit splat silently did the fragile thing when a raw-token
build met ssh. The refusal is also enforced *statically* for stubbed
tools: positionals stay `*args: str`, and an `Argv` is not a `str`, so
basedpyright rejects the bare spelling with the same meaning as the
runtime error. The same refusal (same shared wording, `context.
container_error`) guards elements of a hand-written `run()` list.

**Flags need no code.** In a keyword slot an `Argv` is the plain list it
is — a list repeats the flag, and a serialised `.posix()` line is an
ordinary string value (`hyperfine(bench.posix(), prepare=clean.posix())`).
No recognition exists in any path, so there is no recognition-ordering
trap to pin with a test.

### 3. `Result.to_argv()`

The executed command of a call that already ran, as the same `Argv` value
— logging, error messages, a receipt pasted into a ticket, reproducing a
failure elsewhere: `r.to_argv().posix()`. `Result` now threads a
`tokens=` tuple through construction; `_raw` cannot stand in, because it
is quoted for the local platform and a `list2cmdline` string cannot be
reliably re-split. In-process calls, Python callables and command
*strings* never had separable tokens, and the error says so rather than
guessing (splitting a string back is platform-dependent guesswork).

Named apart from the handle's `.argv` on purpose: someone who writes
`git.push().argv` meaning to *build* gets an `AttributeError` rather than
a push that quietly happened. And the two can differ by one token: what
ran may carry git's injected `-c color.ui=always`, as `.raw` already
does.

### 4. The stubs

Every verb is now a **class**, nested where it belongs
(`Docker.Compose.Up`), generic over what its call returns:

```python
class Mkdocs(_Tool, Generic[_R]):
    class GhDeploy(_Tool, Generic[_R2]):
        def __call__(  # type: ignore[override]
            self, *args: str, force: _Flag = ..., **flags: Any
        ) -> _R2: ...
        @property
        def argv(self) -> Mkdocs.GhDeploy[_Argv]: ...  # type: ignore[override]

    gh_deploy: GhDeploy[_R]
```

One flag block per verb serves both paths: the module binding in
`tools.pyi` fixes `_R` to `Result`, and `argv` re-parameterises the same
class over `Argv`. The mechanics that make it hold together, verified
with basedpyright at `standard` (the repo's mode):

- **A TypeVar per nesting depth** (`_R`, `_R2`, `_R3`): a nested generic
  class cannot rebind its encloser's TypeVar. Underscore-private because
  `ssh` really has a flag named `R`, and a verb class can never lead with
  an underscore.
- **Qualified self-references** (`Mkdocs.GhDeploy[_Argv]`): a method
  annotation never sees the class scopes around it, so the property
  spells the path from module scope.
- **`# type: ignore[override]` on every `__call__` and `argv`**: `-> _R`
  narrows the base's `-> Result`, and re-parameterising the property is
  exactly the narrowing Liskov disallows. The same suppression the old
  stubs already used on root `__call__`.
- Positionals stay `*args: str` — no `str | Argv` widening. The narrow
  type *is* the static half of the container refusal.
- A leaf verb is rendered as a tree whose only entry is a root verb, so
  one recursion in `_stubgen.py` renders tools, groups and verbs alike.
  A bare group's `__call__` stays `*args: Any` wide, so nothing the
  runtime accepts became a type error.

Measured delta: +1.4k lines over the 14.7k-line stub set (~10%). Nothing
renders these classes into the docs (checked: no `:::` directive touches
`footman._stubs`), so the method→class restructure has no docs impact.
It also fixes the bug found along the way: `.opts()` after a verb now
type-checks, where a `MethodType` verb was a dead end — the docs' own
`uv.pip.install.opts(input=…)` example.

The six shells have hand-written stubs (no history to render from); they
were hand-converted to the same generic shape. `fm tools.restub`
(re-render from checked-in history, no tools, no network) regenerated the
other 30, and the emission is `ruff format`-stable, so the refresh
workflow writes exactly what the formatter would keep.

## Implementation constraints

- **`.argv` shares `__call__`'s builder** (`_tokens`), never a parallel
  copy, or the two drift and the accessor starts lying about what a real
  call would spawn.
- **Serialisers never call `_shell_quote`** (local-platform, display
  only).
- **No recognition anywhere**: no splat protocol, no argv special-case in
  flags, `run()`, or positionals. Refusal, not expansion, in the two
  positional paths (bridge call, `run()` list).

## Rejected

- **`argv(fmt=…)` as a leading chain modifier — the first design, fully
  drafted.** `mkdocs.argv(fmt="posix").gh_deploy(force=True)` returned a
  one-item `Argv` (the quoted line) or an N-item one (raw tokens), and an
  arity rule let a `__fm_argv__` splat protocol expand either in place:
  one item landed as ssh's single payload token, N items passed through a
  wrapper. It worked, and it lost on every ergonomic axis at once:
  - *Reading and editing order.* The modifier interrupted tool→verb, and
    converting a working invocation meant editing the chain's head while
    looking at its parentheses. The revision's edit is one inserted word.
  - *The value baked in its consumer.* `fmt` chosen at build time made a
    posix build unusable as tokens (unsplittable) and a token build
    fragile at boundaries; one build could not serve `run()`, a wrapper
    and a remote hop. Format is per-boundary information, so it belongs
    at the boundary, on the value.
  - *The arity signal could not catch the real mistake.* A raw-token
    (default-fmt) `Argv` splatted into `ssh` joins on spaces and
    re-splits remotely — silently wrong for spaced arguments — and the
    design could not refuse it, because N tokens into a positional is
    exactly what wrapper verbs legitimately do. The revision makes the
    ambiguous spelling refuse and teaches both meant ones.
  - *Machinery.* The splat protocol, the recognition-before-refusal
    ordering trap (with the test pinning it), `check_fmt`, the
    `str | Argv` stub widening, and `ssh.argv(fmt=…)("host", …)` — the
    one surviving double-parens spelling — all dissolve. Tests stopped
    comparing against one-item lists (`== ["mkdocs gh-deploy --force"]`)
    and compare a string to a string.
  The position rules ("the modifier always leads", the banned trailing
  spelling) were artifacts of `argv` carrying configuration: a property
  has no parentheses, so there is nothing to lead or trail incorrectly.
- **`mkdocs.gh_deploy.argv(force=True, fmt="posix")`** — flags and `fmt`
  sharing the call. Rejected in the first design because `fmt` lands in
  the kwargs namespace the bridge reserves entirely for tool flags (a
  tool with a real `--fmt` would be unreachable) — the reason `.opts()`
  is a separate channel. Once `fmt` moved onto the value, the
  flag-carrying `.argv(…)` call is exactly what shipped; the rejection
  was of `fmt`'s position, not the spelling.
- **`argv(lambda: mkdocs.gh_deploy(force=True), fmt="posix")`** — a
  deferred thunk. Types fine and needs no stub change, but a `lambda` at
  every call site invents a second way to spell a call in a bridge whose
  point is that there is one.
- **`mkdocs.gh_deploy(force=True).argv(…)`** — postfix on the `Result`.
  The nicest to read and impossible: the call **executes** before `.argv`
  is reached, so asking for the command line first deploys the site. The
  marker must precede the parentheses because it is what decides the call
  does not run — the revision moves it as close to them as Python allows.
- **An explicit `.do()` terminator**, every verb returning `Self` until
  `.do()` runs it. The one rejected option that would genuinely work, and
  it dies on its failure mode: a forgotten `.do()` is a *silent no-op* —
  `ruff.check("src", fix=True)` stops linting, hands back a builder
  nobody looks at, and the task reports success. `Pending` teaches on
  every *use* of a queued value, but a discarded builder is never used,
  so there is nothing to intercept. asyncio survives the same hazard only
  because `await` is language-enforced. It also taxes 100% of calls to
  serve the fraction that build.
- **Shell composition** — joining commands into one payload
  (`cd /srv/app && git pull && systemctl restart app`). Too many
  exceptions for the help it gives: the operator is per-shell and
  per-meaning (`&&` / `;` / `||`, and PowerShell's `;` is not cmd's);
  quoting composes badly, since each part is already quoted and any
  outer wrapper needs another pass that knows its own target; `cd` is a
  shell builtin with no executable to name, so half the payload ends up
  an untyped string anyway; one payload means one exit code, so which
  part failed is unrecoverable; and it overlaps `run(shell=…)` and its
  `[tool.footman.shell]` policy. Write three ssh calls, or `shlex.join`
  in your own task.
- **A `with building() as lines:` block.** Needs no stub change and has a
  working precedent (`with parallel()` queues calls), but a block collects
  one entry per call, so `lines` is a `list[Argv]` and cannot be the
  value a single nested payload needs.

## What this does not solve

- `recording()` stays the only way to rehearse a *whole task* and see
  every step it would run.
- `.raw`/`.command` keep their local-platform quoting, which is correct
  for display. The tools-bridge page now says the quiet part: quoted for
  the local shell, do not send across a machine boundary — that is
  `.argv`/`.posix()`'s job, or `to_argv()`'s for a call that ran.
