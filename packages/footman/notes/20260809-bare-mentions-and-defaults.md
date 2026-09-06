# Bare mentions, declared defaults, and the end of `bare=`

*Status: BUILT 2026-08-09, on `worktree-bare-mentions-and-defaults`. The plan
below is what was agreed; "What the build changed" at the end records where
reality disagreed with it.*

## The report that opened it

> footman requires `--agent=VALUE` for a string option — bare `--agent` errors
> with `--agent expects a value, attached: --agent=VALUE`. Optional-value only
> exists for footman's own globals (`--install-completion=[SHELL]`), not task
> parameters. So `--agent` meaning "the default agent" isn't expressible today;
> the spelling has to be `--agent=claude`.

## Where the restriction came from

0.22.0 made every value `=`-attached and deleted the splitter's
value-consumption states — "a chain reads token by token with no arity table".
Before that, `--agent VALUE` was legal, so a bare `--agent` was genuinely
ambiguous: greedy consumption would eat the next task.

That ambiguity died with the space form. The refusal outlived it. **`--agent gpt`
cannot be an option carrying a value, because that spelling is not in the
grammar** — it is two tokens with two independent meanings, and a bare mention
needs no lookahead to be unambiguous.

## The rule

**A bare mention of a value-bearing option is accepted and records no value.
Binding then proceeds exactly as if the option were absent.**

One branch in `_consume_option`. Everything downstream is automatic: the
binder's existing ladder runs untouched, so `fm review --agent` and `fm review`
produce the same value.

- **Who may appear bare**: any option where absence is legal. The manifest
  already records that as `required` — no new key, no schema bump, completion
  hot path untouched.
- **Required options still refuse**, keeping today's wording rather than
  degrading to "missing required option".
- **No lookahead.** The "did you mean `--agent=gpt`?" teaching was a diagnostic
  for a spelling outside the grammar. It is not deleted, but demoted: **whenever
  a token immediately following a bare mention causes a parse error, that error
  carries `— did you mean --opt=<token>?`.** It never turns a working line into
  a refusal.

  Whether a following token errors depends on whether the task still has a
  positional slot open, so the shapes matter — `review(*, agent: str =
  "claude")` has none, `deploy(env: Literal["dev", "staging", "prod"], *,
  target: str = "blue")` has one:

  | line | result |
  | ---- | ------ |
  | `fm review --agent` | `agent="claude"` |
  | `fm review --agent gpt` | `expected a task name, got 'gpt'` **+ hint** |
  | `fm deploy --target prod` | `env="prod"`, `target="blue"` — parses, no hint |
  | `fm deploy --target lint` | `<env> must be one of dev\|staging\|prod` **+ hint** |
  | `fm deploy --target prod lint` | `deploy prod`, then `lint` — two segments |

  `fm deploy --target prod` deserves a word, because it looks like a trap and
  is not one. `prod` is a positional and binds as one. The space form was never
  a value spelling in this grammar, so nothing is being silently mis-read — the
  line means exactly what its tokens say, and a warning there would be footman
  second-guessing a valid invocation. All five lines go into `test_split`, the
  two erroring ones into `ERROR_CASES`.

  Not new machinery: `_split.py:509-525` already does exactly this for bare
  value-optional globals ("teach the attachment, never 'unknown task zsh'").
  Extending it to task options means `_resolve_head` needs the previous
  segment's options in hand — the one non-trivial bit of plumbing in the
  splitter change.

### Rejected: a `bare(...)` marker for task parameters

The first design gave task parameters their own `Annotated[str, bare("claude")]`,
mirroring `GlobalOption(bare=…)`. Rejected: the default already names the value.
A marker would have been a second way to say what the signature says.

### Rejected: the follower guard

An intermediate design accepted a bare mention only when nothing value-shaped
followed, to preserve the space-form teaching. This was wrong — it reintroduced
exactly the lookahead 0.22.0 deleted, and it refused lines that have a single
valid parse. Recorded because it was argued for at length before being dropped.

### Rejected: options must trail positionals

If options had to come after positionals, a bare mention could never precede a
positional-eligible token and the question would not arise. Rejected: it
protects against nothing — `--target prod` is a bare option and a positional,
not a mis-read value — while breaking a freedom that exists today, since
`fm build src/ --tag=a` and `fm build --tag=a src/` are both legal. It would
also put position-dependence back into a grammar that spent a breaking change
in 0.22.0 removing it.

footman already constrains order where ambiguity is genuine: an optional
positional must trail everything positional (`_manifest.py:767-776`), because
"which token is whose" is otherwise unanswerable. Options are self-identifying
by name, so they are never in that situation.

## Presence is the feature

The report asks for `--agent` to mean "the default agent" — something a task can
*act on*, distinct from absence. That is presence. Bare mentions are only the
spelling that makes it typeable. Neither half is worth much alone: without
presence a bare mention is a no-op, and without the bare spelling presence has
no way to say "on, with the default".

**`given` means the caller supplied it — named on the line, or answered at a
prompt.** `env` and the default are the two sources that are *not* the caller:
an env var is ambient, set elsewhere and possibly by a machine, while a prompt
is this human answering this invocation. `.value` respects `env`; `.given`
answers whether the caller supplied it; the caller combines them however it
wants. Willem's model: *"the calling code decides whether an env
presence is enough… if they only want to respect env when an option is passed,
they should check for presence."*

### Task parameters

```python
@task
def review(*, agent: str = "claude") -> None:
    """Review the diff, with an agent when asked for one."""
    if given("agent"):
        review_with(agent)
    else:
        review_plain()
```

`fm review` → off. `fm review --agent` → claude. `fm review --agent=gpt` → gpt.
The signature stays `str` rather than `str | None`, and "claude" is declared
where `--help`, `--describe` and completion all read it, instead of hiding in
the body behind a sentinel.

The information is already computed at both boundaries — `name in seg.values`
on the CLI side, and `bind_call`'s `sig.bind_partial` on the Python side, which
already distinguishes an omitted parameter from its default passed explicitly.
Recording it on the Context is plumbing, not new knowledge, so `review()` reads
as not-given and `review(agent="gpt")` as given.

`footman.given("name")` raises a taught error for a name the running task has no
parameter for, and for a call outside a task — the shape `GlobalOption.value`
already uses.

**Why a string and not `given(agent)`**, since it will be asked again: by the
time the body runs, `agent` *is* the value, and Python has no reference to a
binding. Searching the caller's locals for the object breaks on equal values
(`agent="claude"` and `model="claude"` are the same interned object). Recovering
the expression from source works — `co_positions()` since PEP 657 makes it
robust, stdlib-only — but needs the source present at runtime and is invisible
to a type checker, which is worse than a string, not better. Binding a wrapper
so `agent.given` works (the `Secret(str)` / `Result(int)` trick) cannot be
universal, because `bool` is not subclassable and flags are the most natural
presence case. `GlobalOption.given` escapes all of this only because a global is
a real object — the one asymmetry kept on purpose.

A prompt counts, and so does a piped `stdin` payload — both are the caller
supplying the value rather than footman inferring it. The prompt rules are in
"`ask()` grows a default" below.

### Forwarding carries two channels

- **the value** — resolved exactly as today (CLI > env > `default(fn)` >
  default) and *always* forwarded, so what a prerequisite receives does not
  change
- **presence** — "named on the command line", travelling beside the value, so
  `given` reads as the same sentence in a task, a `pre_task` and a `post_task`

An earlier draft here forwarded only what was specified. It breaks `env()`:
`AGENT=gpt fm review` would forward nothing, and a prereq declaring `agent`
would run on its own default instead of `gpt`. The two-channel model keeps
today's value semantics untouched, so it is not breaking at all.

### Forwarding rescues a defaultless parameter

Today it refuses to (`_executor.py:711`, guarded on `param.default is not
empty`; `forward_map` skips them on the sending side too), on the grounds that
"a prerequisite must still be independently runnable". Both guards go.

That was never actually the rule: `ask()` and `stdin` already satisfy
defaultless parameters (`bind` prompts for "a required (defaultless) param
nothing else filled"), and `param_spec` marks both as making one CLI-optional.
Forwarding was simply missing from a category that already exists. It also
pushed authors somewhere worse — to get a value into a prereq you had to give
its parameter a default it did not want, turning an honest required contract
into a fake-defaulted one that accepts a bogus value when run directly. Same
shape as the forwarding plan's earlier ruling that "prereqs run defaulted" was
a limitation, not a principle.

Presence follows the value, and for a *rescued required* parameter it is always
true: the three sources that can fill a defaultless parameter — the line, an
`ask()` prompt, a piped `stdin` payload — all count as the caller supplying it.
Presence false alongside a real value stays reachable on *defaulted* parameters
(a value from `env`, or an `ask()` default taken because nobody could be asked),
which is the case one channel could never express.

The residual wart, and it is the author's to own: a prereq whose required
parameter is only ever supplied by forwarding errors when run directly while
working under its dispatcher. `--help` still states what it requires, so nothing
is hidden.

Docstrings rewritten with it: `bind`'s "never rescues a required one — a
prerequisite must still be independently runnable", and `forward_map`'s "a
required one is never forwarded (matching `bind`)".

### Presence is one mechanism, not two

Both entry points produce the same thing — a `frozenset[str]` of the parameter
names the caller named — and everything downstream reads only that.

| invocation | value | presence |
| ---------- | ----- | -------- |
| `fm review` | default (or env) | absent |
| `fm review --agent` | default | **given** |
| `fm review --agent=gpt` | `"gpt"` | **given** |
| `review()` | default (or env) | absent |
| `review(agent="claude")` | `"claude"` | **given** |

A bare CLI mention is therefore exactly a Python call that passes the resolved
default explicitly, and there is nothing a command line can say about presence
that a call cannot.

The set is derived once per boundary — the segment's keys on the CLI side,
`bind_partial(…).arguments` on the Python side, both already computed today —
and consumed in four places: the Context (for `given()`), the work key, the
scheduler's split key, and the forward channel above.

**The work key is the trap.** `_futures._key` calls `bound.apply_defaults()`
before freezing (`_futures.py:204`), which erases presence by construction:
`review()` and `review(agent="claude")` freeze identically today. Since `given`
changes what a task does, those are different work — so the key takes the
presence set alongside the frozen arguments, computed before `apply_defaults()`,
which is exactly where `bind_call` already reads it.

### And the end of `bare=`

`bare=` exists because `--profile` has three outcomes (off / `fm-profile.json` /
a named path) and one declared value only produces two. Presence supplies the
third without a second value:

```python
PROFILE = GlobalOption("profile", Path, default=Path("fm-profile.json"))
if PROFILE.given:
    ...
```

## `default(fn)`

A marker like `suggest()`, computing a default at bind rather than at import.

- Ladder: **CLI value > forwarded > env > `default(fn)` > the Python default.**
- Needs a Python default to sit on, exactly as `env()` does — a direct call
  outside a run still has to work.
- Applies on the Python-call path too: `bind_call` already consults stdin/env/ask
  for omitted parameters.
- No snapshot caveat (see below).

## The sibling view, and why leftward-only is the safety argument

`check(fn)` could always take a second argument — the parameters resolved so
far. `default(fn)` takes the same view from one argument, because a default is
as often a function of its neighbours as a validator is.

**The view holds *effective* values, and only a left parameter has one.** That
one sentence is the whole design, and everything else follows from it:

| given `probe(*rest, from_cli, from_env_or_default, me, to_my_right)` | the view `me` gets |
| --- | --- |
| `rest`, left, variadic | `('tail',)` |
| `from_cli`, left, supplied | the supplied value — not its declared default |
| `from_env_or_default`, left, unsupplied | its declared default, being what it will be |
| `to_my_right`, right, *even when supplied* | **absent** |

The obvious worry is cycles and evaluation order, and neither is reachable —
not because they are guarded against, but because they cannot be written down.
The signature fixes a total order and binding walks it, so a value can only
depend on one already resolved. There is no order for a command line to vary.

It is *not* "computed values are dangerous, declared ones are safe". Exposing a
right-hand parameter's declared default would introduce no cycle at all, and
would still be wrong: `to_my_right` above was supplied on the line, so serving
`declared-c` under that key hands a reader a value the task will not receive.
Same staleness, different door — which is also why the view must never fall
back to declared defaults for *left* parameters that were supplied.

### Detecting a violation

Reaching rightwards used to fail two ways, and the quieter one was worse:

    p["later"]      → KeyError: 'later'      # true, and teaches nothing
    p.get("later")  → None                   # silent, and the run SUCCEEDS

The second is the real hazard: a defensive `.get()` feeds the body a value
nobody chose and nothing complains. The view is now a small `Mapping` that
knows the parameter order, so both spellings teach:

    'me' may only read parameters declared before it, and 'later' comes after
    — so it has no value yet. Move 'later' above 'me' in the signature.

Raising something other than `KeyError` is what reaches `.get()`, since
`Mapping.get` swallows only that. Reading your own name is named separately
("cannot read its own value"), and an *undeclared* name stays an ordinary
`KeyError` — a typo is a different mistake and does not deserve advice about
ordering.

Detection is dynamic rather than static on purpose. Inspecting a lambda's
constants for literal subscripts would catch `p["later"]` at import, earlier —
but miss `p.get(name)`, computed keys and anything indirect, and a guard that
half-holds is worse than one that holds at the moment of use.

### Where else declaration order matters

Worth knowing, because only the first was ever silent about violations:

- the sibling view (`check`/`default`) — this section;
- **positional consumption**: exact arity in declaration order, which is the
  grammar itself (`render <template> <output>`);
- **optional positionals must trail** (`_manifest.param_spec`) — refused at
  spec time, since "which token is whose" would otherwise be unanswerable;
- **positional-only parameters lead**, and `bind` moves that run into `*args`
  in signature order;
- **`ask()` front-loads its prompts in signature order**, so a person is asked
  left to right;
- **a grouped shape's slots** (`--size=800,600`) follow its field order.

`forward_map` also walks the signature, but each value is independent there, so
its order carries no meaning.

## `ask()` grows a default

`ask()` today prompts only a parameter with **no** default — "a default is the
answer" — making it the last resort for a required value. It generalises: with a
default it still prompts, and enter accepts the default. That makes `ask()` safe
on any parameter, because an interactive run gets asked and an unattended one
gets the default.

- **Off a terminal, under `--no-input`, in `--json`**: defaultless keeps today's
  error naming the flag; defaulted stops erroring and takes the default
  silently. There is an answer, so there is nothing to refuse.
- **Presence**: prompted-and-accepted is *given* — the caller was asked and
  answered, and accepting the offered value is an answer. The non-interactive
  fallback is *not given*, because nobody was asked. So `given` reports whether
  a human was in the loop, which means **the same command line can differ
  between a terminal and CI**. True rather than surprising, but it is the first
  thing in this design that is not purely a function of the invocation. Adopted
  deliberately.
- **Bare mentions**: `fm review --agent` must not prompt — the caller has said
  "the default one", so there is nothing left to ask. Presence resolves it with
  no new concept: bare is *given, no value*; absent is *not given, no value*. So
  the ladder skips the prompt on the first and prompts on the second, and the
  bare spelling keeps meaning what `--help` says it means.
- **`secret=True` never shows the default** — `Password [hunter2]:` defeats the
  point. A secret prompts bare, and enter still accepts.

Precedence: **CLI > env > prompt (default offered) > default**, the prompt
skipped when the option was named bare or when nobody can answer.

## The invariant we both kept forgetting

**The manifest is a serialisation of a tree rebuilt every run. The JSON file is
the completion hot path's cache, and nothing else reads it.**

`sync_manifest` returns `fresh` and writes the file only on a hash change; the
run takes its tree from that return value. `build_manifest`'s own docstring says
it: *"Dynamic completers run here… this is the execution path, so paying to
refresh their cached choices is free."*

Consequences, each of which was mis-stated at least once during the design:

- `--help` never renders a snapshot. Values shown there were computed in that
  same process, so a `default(fn)` displayed in help cannot lie.
- `suggest(strict=True)` does not validate against stale data either.
- `--describe` strips completer choices for *contract stability across
  machines*, not because they are old.

This is why the "stop baking `suggest()` choices" thread dissolved: accuracy was
never at risk. What remains of it is a measurement question — every completer
runs on every execution-path invocation, so `suggest(git_branches)` shells out
on `fm build`. If that isn't free, the answer is laziness, not deletion. Tracked
separately.

## Globals stop being a dialect

- `_VALUE_OPTIONAL` goes. The `[BRACKETED]` hint stops being a grammar signal
  and becomes help notation; every value-taking global is bare-legal on the same
  rule as task options.
- `_globals_to_dict` stops overloading `True` for "bare". The value narrows from
  `bool | str` to `str`, and presence moves to a set of names.
- The five consumers of that union become presence tests: `_format_value`,
  `_describe_contract`, `_resolve_shell`, and the action loop's detached-value
  teaching.
- **GLOBALS gains a default column**, so `--install-completion`'s bare form is a
  declared `default(detect_shell)` like everything else, rather than a body
  branch on `shell is True`.
- **Repeatable and mapping globals bind like task options.** A task option
  accumulates and comma-splits (`_split.py:774-783`); `bind_global_options`
  coerces a single token with `_coerce_extra` and assigns per token, so
  `--tag=a --tag=b` is last-wins and `--tag=a,b` never splits. A `list[str]` or
  `dict[str, str]` GlobalOption is declarable, describes correctly in the
  manifest, parses fine, and binds wrong — a latent bug, not a decision.
- **A bool `GlobalOption` gets `--no-x`.** Task flags negate automatically
  (`_split.py:739-742`) and `--help` teaches both forms; `_parse_globals` has no
  `--no-` handling at all. Core's `--no-color`, `--no-input` and `--no-progress`
  are untouched — they are standalone flags, not negations of a
  `--color`/`--input`/`--progress` flag — but a plugin's bool option should not
  need a second declaration to be turn-off-able.
- **`GlobalOption` honours `env()`.** It accepts the marker today and never
  applies it: `_global_spec` runs the annotation through `param_spec`, so `env`
  reaches the manifest (and, under the new help pass, the screen), but
  `bind_global_options` resolves an absent option straight to `opt.default` and
  never calls `_env_value`. The synthetic-`inspect.Parameter` bridge that
  `_global_spec` already builds lets bind run the identical ladder, so globals
  and task parameters share one path instead of having two.

### Not doing: short aliases

Raised and left alone. For **plugin globals** the plugin note's reason holds: 26
letters, thirteen already claimed by core (`-h -V -l -a -n -k -s -j -y -q -v -C
-f`), so third parties would race for the rest and first-to-claim would win for
everyone — with a discovery-time collision refusal making two plugins mutually
uninstallable. For **task options** there is no principled objection (the
grammar already parses `-j=2`, and position separates `fm -j=2 build` from
`fm build -j=2`), only cost: it would break the "identifier is the spelling"
invariant, where `cli_name` is the single derivational mapping and its own
docstring records that drift was already a problem once. It buys terseness, not
expressiveness. Its own change if ever.

## Help finally shows what it knows

`spec["default"]` and `spec["env"]` are both baked into the manifest and neither
is ever read back. `_mechanics` renders choices, types, arity and `required`,
and stops there.

- Print the value you'd actually get; print nothing when there is no default.
- Ladder order: `; from $AGENT; default: claude`.
- `Secret` defaults already redact through `jsonable`; exotic defaults never
  reach the manifest, so they simply don't print.
- Hand-written `(default: …)` help strings get deleted so they cannot drift —
  starting with `profile.py`.

## Wording

Both "no task" branches unify on **`no task named 'gpt'`** — the top-level
`expected a task name, got 'gpt'` and the dotted `no task at 'docs.sevre'`.
"at" was matching the addressing vocabulary (a dotted address is a location,
and the clause after it reads `(docs has: …)`), but someone who typed
`docs.sevre` thinks of it as a name. "named" over "called" because the code's
own vocabulary is already "task name".

Lowercase, like every other message, because it appears after a prefix:
`acme: no task named 'gpt'`.

The scope clause already carries the distinction the two leads were drawing —
`(know: docs, lint, test)` at the root, `(docs has: build, serve)` inside a
group — so one lead loses nothing there. What *is* dropped: "expected a task
name, got …" made a claim about the position (this slot takes a task name, and
that isn't one), which is information when a stray value lands there
(`fm build 2`). Judged a fair trade — the `(know: …)` list answers either
reading.

Sweep, since nothing guards error prose and a missed quote goes stale silently:
the string in `_split.py`, one assertion in `test_split.py`, three in
`test_app.py`, one in `test_testing.py`, and three docs quotes
(`custom-cli.md:47`, `troubleshooting.md:27`, `json.md:332`).

## Loose end found on the way

`_manifest.py`'s module docstring still documents `mode: str = "loose"` as
`option --mode VALUE` — the space form, refused since 0.22.0. `_SPACE_FORM` in
`tests/test_docs_drift.py` only scans `docs/` and `README.md`, so source
docstrings are unguarded. Fixing the line here; widening the guard is its own
change.

## Config-backed globals

Sequenced after this change, then **folded in** at Willem's call. It did not
land in the shape planned below — see "What the build changed" for why the
derived-`KEYS` idea does not survive contact with the table.

**Every boolean config key should have both CLI spellings**, and five don't:

| key | default | today | wants |
| --- | ------- | ----- | ----- |
| `sequential` | false | `-s` sets true (`_app.py:1807`, an `or`) | `--no-sequential` |
| `sort` | false | `--sort` sets true (`:1723`) | `--no-sort` |
| `progress` | true | `--no-progress` sets false | `--progress` |
| `uv` | true | no flag at all (`:1400`, `:1498`) | both |
| `input` | true (new) | `--no-input` sets false | `--input` |

This change fixes all five by hand, uniformly. The follow-on replaces that
hand-wiring with a declaration.

**Why it is worth doing at all:** a config-backed global is declared twice
today — a row in `KEYS`, a row in `GLOBALS` — and linked by hand in `_app.py` in
three different spellings (`bool(g.get(…)) or bool(cfg.get(…))`,
`cfg.get(…) is not False`, `cfg.get(…) is False`). Three spellings of one idea
is where the five gaps came from.

**The shape:** a global opts in with `config=True` — a config key of the same
name sets its default — and precedence becomes one ladder everywhere, **CLI
value > config value > declared default**, mirroring the bind ladder.

**The payoff is structural:** `KEYS` becomes *derived* from the globals that
declare config-backing rather than hand-maintained, which deletes the drift
instead of policing it with a test — the `cwd`-undocumented-for-four-releases
failure the `KEYS` comment records cannot recur when there is no second list.
Plugin globals get a config story for free; they have none today.

**Three constraints it must carry:**

- **Post-discovery globals only.** Config is loaded during discovery, so a
  config-backed default is circular for `-C`, `-f`, `--config` and `--uv`. That
  is why `uv` is the awkward one above — here it becomes a stated rule.
- **Config values coerce through the same pipeline** `env()` uses, so a bad TOML
  value is taught rather than silently wrong.
- **Opt-in, never automatic.** `--yes` is deliberately not config-settable
  (auto-confirming destructive gates from a file), and `--dry-run`, `--json`,
  `--describe` are per-invocation actions rather than policy. Config-backing is
  a property of the option, not of being a global.

**Why after, not now:** it is the only structurally separable piece in this
thread — declaration plumbing, not presence or bare mentions — and it *stacks*,
consuming the GLOBALS default column this change adds. Landing it here would
put a rewrite of how config and globals are declared into a diff that already
touches five executor paths. Nothing is done twice: the uniform hand-wiring
landed now is exactly what the declaration replaces, and the intermediate state
is already correct.

## What the build changed

Four places where writing it disagreed with planning it.

### Globals are bare-legal when they have a default — not unconditionally

I read "every value-taking global becomes bare-legal" as *remove the parser
restriction*, deleted `_VALUE_OPTIONAL` and its refusal outright, and found
`fm --where lint` parsing as a bare `--where` followed by a task — **silently
running lint** instead of teaching the attachment. `--where` names a task to
locate and there is no default task, so its bare mention has no reading.

Willem meant *if it has a default*, which is the same rule the task side
follows and (with the default column, above) the same words: **bare-legal iff
there is a default.** `_VALUE_OPTIONAL` stays, derived from that column.

Plugin globals *are* all bare-legal, because their owner can always ask
`.given` — so the reading exists by construction.

### The GLOBALS default column, and why deferring it was the mistake

Deferred at first, on the grounds that a column of mostly-empty strings only
earns its place once config backing is declared. That reasoning was wrong, and
Willem caught it: **"every value-taking global becomes bare-legal" always meant
"if it has a default"** — and the column is what makes "has a default"
expressible in a static table. Without it there was nothing to key the rule on,
so the bracketed metavar stood in as a proxy, which is why the rule ended up
sounding like *two* vocabularies.

With the column in, it is one rule on both surfaces: **bare-legal iff there is
a default**. The bracket goes back to being help notation. Two globals change
hands — `--jobs` and `--color` have defaults and are now bare-legal, where the
bracket-proxy refused them.

### The derived `KEYS` table does not survive contact

The plan had `KEYS` *derived* from the globals that declare config backing, so
there would be no second list to drift. Six keys have no CLI counterpart at all
(`tasks`, `cwd`, `gc`, `cascade`, `fetch`, `shell`), so derivation would have
produced a half-derived, half-hand-written table — worse than either.

What landed instead is the guard, in both directions: every boolean *project*
config key has `--x` and `--no-x`, and every `_switch(g, cfg, "name", …)` names
a documented key. It found a sixth one-way key by itself — `gc` — which turned
out to be exempt rather than broken, because `USER_LEVEL_KEYS` govern
machine-wide behaviour and "for this invocation" is not a thing they can mean.
The guard forced that distinction to be stated rather than assumed.

### Presence in the work key has one visible consequence

Narrower than feared, and worth stating: **passing a value equal to a
parameter's default is no longer the same work as omitting it.** Nothing else
about identity moves — different values already keyed differently, and
positional-versus-keyword spelling still names one execution. Two tests
asserted the old property in passing (`assert render() == "WEB"  # the default
is the same work as "web"`).

### The bug the design predicted, found in footman's own plugin

`footman.profile` armed on `if not inv.cli.get("profile")` — truthiness of the
value standing in for presence, which worked only while a bare mention arrived
as `True`. It now asks `if "profile" not in inv.cli`, the question it always
meant. The design's whole premise, sitting in the codebase already.

### Smaller things

- `_thread`'s merge condition must compare presence **only for names the
  dependent has already seen**; comparing unconditionally made every first
  thread look like a conflict and split instead of merging, which the
  forwarding tests caught immediately.
- `test_source_never_names_a_caller` fails if `src/footman/` contains "claude"
  anywhere — the docstring examples here were written around `--agent`
  originally and had to move to `--profile`, which is the better example.
- pyrefly wants `Generator[None]` where basedpyright accepts `Iterator[None]`
  on a `@contextmanager`.
