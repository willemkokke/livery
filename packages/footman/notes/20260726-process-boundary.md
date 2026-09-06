# The process boundary — design note

How footman becomes a first-class citizen in a pipeline: stdin bound to typed
parameters, stdout ownable by a task's return value, and an exit code that
distinguishes "I did not understand you" from "the work failed". Claude Code
hooks are the worked example, not the feature.

**Status: APPROVED, nothing built** (2026-07-26). The decisions at the
bottom are settled, each per its recommendation; item 7 (a packaged Claude
layer) is approved as *deferred* — recorded, not planned. The Claude
contract was re-checked against `code.claude.com/docs/en/hooks` on
2026-07-26 and footman's behaviour was probed, not assumed — see the
appendix. Revised the same day after a code-grounded review; its
corrections are folded in and its findings extend the appendix.

Supersedes the first draft (`claude-hooks.md`, same day) on two counts. It
designed the stdin half as "parse a JSON payload" — a hook feature in general
clothing, when the general feature is a pipe target and JSON is one
interpretation of the bytes. And it put stdout ownership in a caller-side
`--emit` flag; it is now a **return annotation**, so a filter is a filter by
declaration and no call site has to remember.

## The problem

footman is not usable in a pipeline. Three gaps, all at the process
boundary:

1. **Nothing can be piped in.** `cat file | fm dosomething` cannot work: a
   read from a task body raises, by design (stdin is the fourth process
   global — guarded, not served). `interactive=True` is the only escape, and
   it is the wrong tool: it hands the task the real terminal and switches
   capture off.
2. **stdout is footman's, never the task's.** `--json` owns it with
   footman's envelope; otherwise it carries whatever the task printed mixed
   with nothing else structured. A task cannot produce *the* document on
   stdout for the next process in the pipe.
3. **The exit code is ambiguous.** Exit 2 means four different things, and
   two of them are "footman refused your command line" while two are "the
   work reported a result". A caller cannot tell them apart, so it cannot
   tell a broken invocation from a real verdict.

Gap 3 is the one that does active harm. Under a caller that reads exit 2 as
"blocking error, here is why" — Claude Code does — a typo in the command
line arrives looking exactly like a considered verdict about the project,
and the caller acts on a problem that does not exist while the real fault
goes unstated. It manufactures plausible work rather than failing visibly.

## Core principle

**footman must not know who is calling it.** Not "footman should avoid
Claude-specific code" as a matter of taste — it must be *unable* to tell a
hook from a shell pipeline from a CI step, because the boundary is the same
boundary in all three cases.

The test of the design is therefore expressiveness, not accommodation: the
standard feature set has to be rich enough to say everything a demanding
caller needs, with nothing named after that caller. Claude Code is a good
demanding caller precisely because its contract is fussy — per-event stream
rules, a structured verdict channel, an exit code that means something
specific — so if the general features express it, they will express others.

A user's own task coupling itself to their own caller is fine and none of
footman's business. `fail("…", code=2)` in a hook someone wrote is good
code. The line is drawn inside `src/footman/`, not inside user tasks.

Enforced, not merely intended: a test greps `src/footman/` for `claude` and
fails on a hit. The repo already polices structure this way (`tools.pyi`
parity, the `_fork` field census).

## Primitive 1 — a refusal is distinguishable from a failure

Today, probed:

| what happened | exit |
| --- | --- |
| unknown task, unknown flag, malformed chain | `2` |
| bad *value* for a known flag (binding-time) | `2` |
| `fail("…", code=2)` or `return 2` from a body | `2` |
| a `run()` subprocess exiting 2 (ruff error, pytest usage error) | `2` |

The first two are footman saying "I did not understand you". The last two
are work reporting its result. A caller needs that line and cannot have it.

**Proposal.** Refusals get their own exit code: **64**, `EX_USAGE` from
`sysexits.h`. BSD-derived, understood, and no realistic task returns it.
`130` for interrupt already sits outside the collision and stays.

Every refusal site moves, and there are more than two layers:

- Line-level: `_refuse()`'s default `code` in `_app.py`.
- Binding-level: the `TaskResult.code` a coercion failure produces
  (`tests/test_params.py:414` pins this at 2 today, commented "a binding-time
  refusal, not a task failure" — the comment already argues the change).
- The bare `return 2` sites in `_app.py` that never route through
  `_refuse()`: `--where` with an unknown task, and the completion
  install/uninstall/setup failures (~8 sites). `fm --where typo` is exactly
  "footman did not understand you" and would otherwise keep exiting 2,
  quietly breaking the contract table below.

A structural test keeps it swept: `_app.py` may contain no literal
`return 2` / `code=2` outside the one named constant, so the next diagnostic
subcommand cannot regress it. Same policing style as the `tools.pyi` parity
test.

Blast radius: the "12 direct assertions" count predates the bare-site sweep —
re-count when building. Plus the `docs/agents.md` recipes.

**Breaking**, and pre-1.0 so it may land as a minor. The CHANGELOG must say
it plainly, because anyone keying on 2 is silently affected.

Rejected: a `--usage-exit-code=N` global. This should be right, not
configurable.

**A second payoff.** Clearing footman off 2 makes 2 *usable on purpose*. A
task that wants to mean something specific by an exit code — including "2"
to a caller that reads 2 as blocking — can now say so unambiguously. The
value of this step is not only removing a false signal; it is handing the
low exit codes back to tasks.

Two footnotes for the record. Moving off 2 also stops colliding with the
tools tasks most commonly shell out to — argparse, click and pytest usage
errors all say 2; 64's only collision class is sysexits-following tools,
rare in this ecosystem. And the residue does not vanish, it moves: a `run()`
subprocess that itself exits 64 propagates and spoofs "footman refused" —
the same class of residue as today's 2, far rarer, and the `fm:`-prefixed
refusal line on stderr is a serviceable second factor for a caller that
cares.

## Primitive 2 — stdin binds to parameters: footman as a pipe target

Read the stream where footman already reads env vars and prompts — at the
boundary, before the scheduler — and bind it to a parameter. Bodies never
touch stdin, so the guard is never in play, and the value arrives coerced and
validated like any other input.

**The annotation decides how the bytes are interpreted.** This is the whole
of the surface:

```python
def wordcount(text: Annotated[str, stdin]) -> Stdout[int]: ...  # verbatim in
def hash(data: Annotated[bytes, stdin]) -> Stdout[str]: ...  # raw bytes in
def review(diff: Annotated[str, stdin]) -> Stdout[str]: ...  # git diff | fm review
def audit(items: Annotated[list[Row], stdin]) -> None: ...  # JSON → dataclasses
def hook(event: Annotated[StopEvent, stdin]) -> dict: ...  # JSON → dataclass
def raw(doc: Annotated[dict[str, Any], stdin]) -> None: ...  # JSON, unshaped
```

(The filters declare `Stdout[…]` — primitive 3 — and not by accident: a bare
`-> int` is the exit-code channel, so `wordcount` without the declaration
would *exit* with the count and print nothing. Declaration wins; the rule is
stated with the others below.)

- `str` / `bytes` → stdin verbatim, no parsing.
- Anything structured (dataclass, `dict`, `list`) → JSON-decoded, then bound
  through the coercion path.
- **No format zoo.** JSON is the one structured format footman already speaks
  everywhere (the manifest, `--json`, and `Stdout[T]` below). Anything else
  the body parses out of the `str` itself.

A single field of a JSON document, for the cases that want one value:

```python
def submit(prompt: Annotated[str, stdin("prompt")]) -> dict: ...
```

### The rules

- **Precedence: CLI > stdin > env > default**, in the `env` mould. This buys
  the Unix `-` convention with no sentinel argument — one signature serves
  both spellings:

  ```python
  def process(path: Arg[Path] = None, text: Annotated[str, stdin] = ""): ...


  # fm process file.txt   AND   cat file.txt | fm process
  ```

  The full chain, `ask()` included, is **CLI > stdin > env > default >
  prompt** — a piped payload fills an `ask()` parameter before any prompt
  could fire (and a pipe means no TTY, where `ask()` refuses anyway). One
  asymmetry to keep deliberate: `env` is only valid on a parameter with a
  default, but `stdin` is allowed defaultless — absence is a taught refusal,
  so it has somewhere to fall without one.

- **Read once, fully, into memory, and shared.** stdin is consumable, unlike
  env: two tasks in a chain cannot each read it. The boundary reads it once
  and hands the same value to everyone who asks. This is the existing
  doctrine — stdin is the fourth process global and the boundary is its data
  lane — and it keeps stdin-reading tasks fully parallel. The cost is that
  there is no streaming: a huge pipe is buffered. A deliberate limit, stated
  rather than discovered.
- **A terminal means "not provided."** `fm wordcount` with nothing piped
  falls back to the default; it must never block on a terminal read.
  Required-and-absent is a taught refusal naming the parameter — and showing
  the fix in the caller's shape: pipe a document (`git diff | fm review`) or
  `fm review < file`. Under primitive 1 that refusal exits 64, so a
  misconfigured caller is non-blocking and says so.
- **An empty pipe is provided-but-empty for text, a refusal for JSON.**
  `fm task < /dev/null` is not a terminal, so the value was provided: for
  `str`/`bytes` the empty value is a value; for the structured and field
  forms it is a taught refusal ("stdin was empty; expected a JSON
  document"). CI hits this immediately — stdin there is often `/dev/null` —
  so it is defined, not discovered.
- **Several `stdin` parameters share the one read.** A whole-document
  parameter and two field forms in one task all bind from the same bytes,
  each with its own interpretation — the per-task restatement of the chain
  rule.
- **The read is lazy.** Only a run whose plan contains a `stdin` parameter
  touches the stream, so nothing changes for every existing project. The
  flip side, stated next to the buffering limit so it reads as chosen: a
  pipe that never closes blocks at EOF, like `sort` and every other filter.
- **Windows:** the `str` form reads text-mode (universal newlines, `\r\n` →
  `\n`); `bytes` reads the raw buffer; the lines form (open item 6) splits
  with `splitlines()`, which handles both endings.

`ask()` needs no new rule: piped stdin is not a TTY, and `ask()` already
refuses off a terminal rather than hanging. The interaction is already
correct.

### No `interactive`, no `serial` — that is the point

A `stdin`-bound task needs neither, and asking for either is a mistake worth
documenting:

- `_GuardedStdin._guard()` only raises when `ctx.in_task` is true
  (`_globals.py:583`). The boundary read happens outside any task body, so
  the guard is never consulted — not suppressed, not worked around, simply
  not in play.
- `interactive=True` would actively hurt: it hands the task the real terminal
  and switches capture off, and in a pipeline there is no terminal to own.
- `serial`/`exclusive` govern mutation of process globals. Binding a
  parameter mutates nothing, so the task stays fully parallel and can sit in
  a chain beside other work with no regime change.

### The dataclass binder

`coerce.py` has no dataclass support today — its job is string token → type.
Structural JSON object → dataclass is a different kind of coercion and the
largest piece of new machinery here. It pays for itself: it gives a reusable
typed shape for any JSON a pipeline hands over, basedpyright checks the
body's use of it, and nested dataclasses remove any need for dotted field
paths (`tool_input: ToolInput`, not `stdin("tool_input.file_path")`).

It also already has its other half. `_describe.json_default` serialises
dataclasses *out* via `dataclasses.asdict`, so `-> Stdout[SomeDataclass]`
works for free. Dataclass in, dataclass out; only inbound is new.

Rules that matter more than the code:

- **Unknown keys are ignored, never refused.** A producer adds fields over
  time (Claude's payload gained `prompt_id`, `effort`, `background_tasks`
  after the fact). A consumer that breaks when its input gains a field is
  worse than one that ignores it. The single most important rule here.
- **Missing keys follow the dataclass.** A field with a default is optional;
  a defaultless field that is absent is a taught refusal naming it.
- **No aliasing layer.** Keys map to field names directly. This is the
  feature such code usually grows first, and it should not.
- **Recurse** into nested dataclasses, `list[T]` and `T | None`, reusing the
  existing scalar coercion at the leaves so `Path`, `Literal`, enums and
  `datetime` behave as they do for a CLI token.
- **Errors name the JSON path.** `event.tool_input.file_path: expected str,
  got int` — cheap now, expensive to retrofit, and the difference between a
  debuggable hook and a mystery.
- **Scope discipline.** Support exactly the above; refuse the rest with a
  taught error. This is a general JSON→dataclass binder living in a zero-dep
  task runner forever and must not grow into a validation DSL. `check(…)`
  already owns validation.
- **`from __future__ import annotations`** makes annotations strings needing
  `eval_str` against the defining module, so a payload dataclass must be
  module-level. A taught message, not a `NameError`.

Rejected: pydantic, or any third-party binder — ruled out by the zero-dep
invariant before taste enters into it, but the cost comparison earns its
line anyway. At this scope the binder is on the order of 150 lines:
`dataclasses.fields` plus `typing.get_type_hints` for the shape, a
recursive descent for dataclass / `list[T]` / `T | None`, and the leaves
delegate to the scalar coercion that already exists. Unknown-keys-ignored
and missing-key handling are dict lookups; the one fiddly part is
forward-ref resolution, and its rule is written above. What makes such
libraries big is exactly the feature set scope discipline refuses —
aliasing, a validator DSL, coercion policy, arbitrary types. The risk here
is not build cost but scope creep, which is what "refuse the rest with a
taught error" is for: the moment the binder wants one of those features, it
has failed the scope rule, not outgrown its budget.

The binder's supported-type universe (dataclasses, `list[T]`, `T | None`,
scalar leaves) is one spec shared with the outbound side —
`_describe.json_default` today, the post-1.0 structured-results
`returned_schema` later. Inbound binder, outbound serialiser and future
manifest schema agreeing on one type universe is what keeps "dataclass in,
dataclass out" true as it grows; three independently evolved sets is how it
stops being true. Cross-link the structured-results note; nothing to build
there now.

A dataclass-bound parameter is **boundary-only, not a CLI flag**: a whole
document is not one token, and exploding it into per-field flags founders on
nesting. Hand-running stays easy and gets *more* faithful —
`fm dosomething < fixture.json` exercises the real parse. The single-field
form stays a real flag, where it costs nothing.

### The manifest knows, because completion must

Boundary-only is a fact the manifest has to record: completion answers from
the baked manifest alone, and without a marker it would offer `--event` as a
flag. So a manifest schema addition is mandatory regardless of what `--help`
says (open item 5) — a per-parameter note of how the value binds stdin
(text / bytes / JSON / a field). A schema change means the #58 schema guard
and a completion-cache rebuild: the already-paved path. It also flips the
economics of item 5 — once the manifest carries the marker, the help line is
nearly free.

### Testing is part of the boundary

`Runner.invoke` grows a `stdin=` parameter (str or bytes), and the boundary
read gets a seam the Runner injects into — `sys.stdin` under pytest-xdist is
not something to touch. The seam is designed with the feature, not after: it
constrains where the read lives. Two tiers, both documented:
`Runner().invoke("hooks.stop", stdin=json.dumps({...}))` in tests, and
`fm hooks.stop < fixture.json` at the prompt — the replay exercising the
real parse. The pytest fixtures in `pytest_plugin` stay thin shims.

## Primitive 3 — the return annotation owns stdout: footman as a pipe source

A task **declares** that its return value is the document on stdout, in the
signature, where the rest of its contract already lives:

```python
@task
def status() -> Stdout[dict]: ...              # alias form
def transform(...) -> Annotated[dict, stdout]: ...   # underlying form
```

```sh
$ cat a.json | fm transform | jq .
$ fm status
{"branch": "main", "dirty": false}
```

No flag. `fm transform` *is* a filter, the way `sort` and `jq` are filters —
a Unix filter does not need an argument to say it writes to stdout, and
neither should this. The marker pairs with the parameter side by name:
`stdin` sources a parameter, `Stdout[T]` claims the return.

**This replaces the `--emit` flag** from the first draft, which put the
declaration in the caller's hands. Two arguments decided it:

- **The task knows, and the caller should not have to.** Whether a task
  produces a document is a fact about the task, fixed when it is written.
  Making every call site repeat it is ceremony that can be got wrong, and
  silently: forget `--emit` and stdout carries a human summary that the next
  process in the pipe will try to parse.
- **It is one mechanism instead of two.** With the annotation there is no
  flag to keep consistent with it, no precedence between them to define, and
  `--help` can say what the task emits because the information is in the
  signature. It also sits on the road already mapped for return annotations
  becoming manifest schema.

The cost is that the caller cannot ask a *non*-declaring task for a document.
`--json` already covers that case, so nothing is actually lost.

### Rules

- **The type decides the bytes, mirroring `stdin`.** `Stdout[str]` emits the
  string verbatim plus a trailing newline; `Stdout[bytes]` writes raw bytes
  to the buffer; anything structured is JSON. Without the verbatim rule a
  text filter emits a JSON-quoted string (`"line\nline"`, literal `\n`) and
  every shell user is confused. One interpretation table, both directions.
- **JSON dresses for its destination.** Pretty-printed (indent 2) when
  stdout is a terminal, one compact line when piped, a trailing newline
  always, `ensure_ascii=False` — the colour policy's dress-for-destination
  doctrine applied to the document. Encoded via `_describe.json_default`,
  not bare `json.dumps`: dataclasses-out and `Secret` redaction come free,
  so a hook payload echoed into a document cannot leak a secret that
  `--json` would have redacted.
- **`--json` wins.** An explicit `--json` keeps the envelope, and the task's
  document rides inside `results[].returned`, which is where `--json` already
  puts a return value (`_app.py:745`). No collision to arbitrate and no
  information lost — the reason this design is clean rather than fiddly.
- **Only the addressed task emits** — the task the address resolves to. That
  one phrasing covers every surface: `fm lint` addresses the group's default
  (default-as-child, #60), so a declaring default emits; an empty-body
  fan-out's members were not addressed, so they are suppressed; so is a
  declaring task reached as a `pre=`/`post=` dependency. Suppress, not
  refuse: composing a filter into a bigger task must stay legal.
- **Two declaring tasks in one chain is a refusal**, at plan time. "Whose
  document?" has no answer, and silently picking one is worse than saying so.
- **`None` returned → empty stdout, exit 0.** Nothing to say, said nothing.
- **Declaration wins over the exit-code channel.** A bare `-> int` stays the
  exit-code channel, not data — the existing rule. `Stdout[int]` emits the
  int as the document and the exit code stays the run's own. Without this
  line `wordcount` cannot be a filter at all: undeclared, it would *exit*
  with the count.
- **A failed task emits nothing.** stdout stays empty and the exit code talks.
- **Everything that is not the document goes to stderr.** A declaring task
  runs captured, and its captured output is replayed on stderr, where the run
  summary already goes.
- **`Stdout[T]` + `interactive=True` is a registration-time refusal.** An
  interactive task owns the real terminal, uncaptured; a declaring task must
  run captured. The two contracts cannot both hold, so the contradiction is
  taught when the task is declared, not discovered in a pipeline.
- **A body call is unaffected.** `status()` from another task returns the
  value; stdout formatting is a boundary concern and never applies in-process.

The replay-to-stderr rule is what makes per-caller stream policy unnecessary.
One general sentence — a declaring task's stdout belongs to its return value
— replaces any table of which callers read stdout as data. A caller that
wants prose on stdout uses a task that does not declare, which is every task
that exists today. (And the codebase already half-believes it: a
non-declaring task's captured prints replay to *stdout*, and the run summary
goes to stderr precisely so `fm task > file` captures what the task printed.
`Stdout[T]` completes that doctrine rather than inventing one.)

### Not an in-process pipe — rejected, recorded

`fm transform validate` does not feed transform's document to validate's
stdin. Both share the *process's* stdin, and the document goes to the
process's stdout; piping task into task is two invocations,
`fm transform | fm validate`. The docs say this before anyone asks, and the
misconception is recorded as tempting-but-rejected so it does not get
"fixed" later: intra-chain data flow is the forwarding / structured-results
road, not this one.

## The boundary contract

With all three, a caller has a total function over the boundary:

| exit | stdout | meaning |
| --- | --- | --- |
| `0` | a document | the task's result (it declared `Stdout[T]`) |
| `0` | empty | ran, nothing to say |
| `64` | empty | **footman refused** — the command line was wrong |
| other | empty | the task failed; the code is the task's own |

The third row is the one that does not exist today. Two honest footnotes,
so the table is not oversold. A *non-declaring* task's prints replay to
stdout — today's deliberate behaviour, kept — so "exit 0, stdout non-empty"
only means "a document" when the addressed task declares; the caller knows,
because the caller wrote the address. And under `--json` a refusal prints
the promised envelope on stdout, so row three's "empty" reads "the
envelope" there.

## Worked example — Claude Code hooks

A Claude Code hook is a command the harness runs at a lifecycle point: JSON
on stdin, and a verdict returned either as an exit code plus stderr, or as a
JSON control document on stdout when the hook exits 0. Nothing about it needs
naming in footman; it is a pipeline whose producer and consumer happen to be
an agent harness.

Both shapes are legitimate. Pick by what the event needs.

**Exit code plus stderr**, for an event that blocks (`Stop`, `SubagentStop`,
`UserPromptSubmit`):

```python
from dataclasses import dataclass
from typing import Annotated
from footman import RunFailed, fail, stdin, task


@dataclass
class StopEvent:
    stop_hook_active: bool = False
    last_assistant_message: str = ""


@task(hidden=True)
def stop(event: Annotated[StopEvent, stdin]) -> None:
    if event.stop_hook_active:
        return  # already continuing from a stop hook
    try:
        check()  # raises on a red gate
    except RunFailed:
        fail("the gate is red — fix it before stopping", code=2)
```

Wired as `uv run fm hooks.stop 1>&2`. The redirection stays a shell concern:
it is what puts the gate's receipts on the channel this caller reads.

**A structured verdict**, for an event whose answer has no exit-code
spelling — `PreToolUse` (`permissionDecision`, `updatedInput`),
`SessionStart` (`additionalContext`), `PostToolUse` (`updatedToolOutput`):

```python
@task(hidden=True)
def stop(event: Annotated[StopEvent, stdin]) -> Stdout[dict | None]:
    if event.stop_hook_active:
        return None
    gate = run("uv run fm check", nofail=True)
    if gate:  # Result *is* the exit code
        return {
            "decision": "block",
            "reason": f"the gate is red:\n{gate.stdout}{gate.stderr}",
        }
    return None
```

Wired as `uv run fm hooks.stop` — the signature already said it emits, so the
settings line says nothing about it. On exit 0 stderr does **not** reach the
model, so the receipts must travel inside `reason`; that is why this variant
shells out to hold the output as text rather than body-calling `check()`,
which streams and raises instead of handing back a transcript.

(`Stdout[dict | None]` and `Stdout[dict] | None` are mechanically identical —
`coerce.peel` already strips `Annotated` and `Optional` in any order and
nesting, and the return side reuses the idiom. The former is the recommended
house style: marker outermost, like `NoSplit[list[X] | None]`. See open
item 3.)

`hidden=True` keeps the adapter out of `--list`, `--tree` and completion; it
is not a project task. (Landed as #78.)

### The repo's own hooks are the first consumer

footman's `.claude/settings.json` carries both shapes today, and both have
the disease. The `Stop` hook is the `docs/agents.md` recipe verbatim
(`jq`-based loop guard, `|| exit 2` flattening); the `PostToolUse` hook
plucks `.tool_input.file_path` out of the payload with `jq` and cases on
`*.py` before running `fm format lint 1>&2 || exit 2`. Two consequences of
rewriting them as hidden tasks (`hooks.stop`, `hooks.post_edit`):

- **The `jq` host dependency disappears.** footman parses its own payloads —
  a zero-dep tool whose own hook wiring needs a third-party binary on the
  host is not a good look, and after primitive 2 it is also unnecessary.
- **`hooks.post_edit` is the nested binder's live demo**: `tool_input` is a
  nested dataclass, `event.tool_input.file_path` is the read, and no dotted
  field path was needed — exactly the argument the binder section makes,
  running in the repo that ships it.

This is the worked example run in anger, and the end-to-end test the plan
otherwise lacks.

### Notes on this caller, recorded so nobody re-derives them

- `stop_hook_active` **still exists** and is still the documented loop guard,
  so the `docs/agents.md` recipe is sound. There is also a hard cap: the
  harness overrides the hook after 8 consecutive blocks.
- Exit 2 blocks only some events. It blocks `PreToolUse`, `UserPromptSubmit`,
  `Stop`, `SubagentStop`, `PreCompact` and others; for `PostToolUse`,
  `SessionStart`, `Notification` and `SessionEnd` it only prints.
- stdout is read as context for `SessionStart`, `UserPromptSubmit` and
  `UserPromptExpansion`; elsewhere it is transcript-only.

### Docs

`docs/agents.md` needs its recipes replaced. The current one,

```sh
uv run fm format lint 1>&2 || exit 2
```

converts *every* non-zero — footman's own refusal included — into a blocking
verdict, which is gap 3 baked into a recipe.

The naive fix breaks the other way. Claude blocks **only on exit 2**; any
other non-zero is a non-blocking error. Drop the `|| exit 2` and a red gate
exits 1 — and stops blocking, silently. The codes cannot simply be "let
through". The shell recipe that is honest in both directions is

```sh
uv run fm format lint 1>&2 || { c=$?; [ $c -eq 64 ] || c=2; exit $c; }
```

— block on real failure, pass footman's refusal through untouched. The
harness treats 64 as a non-blocking error whose stderr reaches the human:
exactly the right failure mode for a misconfiguration, which is a problem
for the person who wired the hook, not the model. Ugly, and interim: the
clean spelling is the worked-example task with `fail(code=2)`, which needs
primitive 2. So step 1 ships the guard — in the recipes *and* in the repo's
own `.claude/settings.json`, which flattens the same way — and steps 5–6
replace both with the task form.

One doctrine for every docs change in this plan, stated here because the
recipes are its first instance: **pre-1.0 docs are written timeless**. A
page states the behaviour as if it had always been so — "a refusal exits
64", never "refusals now exit 64" or "changed from 2". Nobody reading the
docs should be able to tell the design grew in steps; the change narrative
lives in the CHANGELOG and nowhere else.

### A packaged Claude layer — deliberately not planned

With the primitives, a hook is short enough to write by hand, so a package is
convenience rather than a precondition. Its remaining value is the per-event
table, response builders for the camelCase output shapes, and a generator for
the `.claude/settings.json` block so the wiring cannot drift. That is a second
product.

If it is ever built: `plugin("…")` resolves against installed `footman.tasks`
entry points (`compose.py:39`), so it means a second installable
distribution — raising what it is called on PyPI (the rebrand decides much of
it, since the entry-point name is what users type) and whether its source
lives here or in its own repo. `include(…)` is also available for anyone who
would rather vendor a module than install a package, which lowers the stakes.
None of this needs answering now.

## Sequencing

Each step is a PR, gated, one logical change. `hidden=` (#78) already landed.

1. **Refusal exit code** — all three refusal layers, the structural
   no-literal-2 test, the interim shell guard (above) in *both* places it
   is wired — the `docs/agents.md` recipes and the repo's own
   `.claude/settings.json` hooks, which flatten identically today — and the
   decoupling test (`src/footman/` may not mention Claude — it costs
   nothing and polices every step after it). Breaking; CHANGELOG. The
   smallest step and the one that stops the harmful failure mode.
2. **`stdin` binding — the `str`/`bytes`/field forms**, with the manifest
   marker (completion must not offer boundary-only flags), the `--help`
   note, and `Runner.invoke(stdin=…)` plus the read seam. Small, and makes
   footman a pipe target on its own.
3. **The dataclass binder.** Its own PR, not a second commit: it is the
   piece that can grow and deserves isolated review, and step 2 should not
   wait while its rules are argued.
4. **`Stdout[T]`.** Independent of 2–3, so it can run in parallel.
5. **Dogfood — the repo's own hooks.** Rewrite `.claude/settings.json`'s
   two hooks as hidden tasks (`hooks.stop`, `hooks.post_edit`) per the
   worked example, dropping the `jq` host dependency and the interim
   guard. Needs 2–4 landed; it is the feature's end-to-end test in anger.
6. **Docs**: a pipeline page (footman as pipe target and source), the
   `docs/agents.md` rewrite replacing the interim guard with the task-form
   recipes, and **a sweep of every page that states boundary behaviour**,
   rewritten timeless per the doctrine above. The exit-code story alone
   lives in at least `json.md` (the envelope table and the int-return
   rule), `agents.md`, `ci.md` and the foundations pages; the stream story
   touches `input.md`, `testing.md`, `cookbook.md` and `orchestration.md`.
   Grep, don't remember: any page matching `exit`, `stdin` or `stdout` is a
   candidate.

## Decisions — approved 2026-07-26

Each settled per its recommendation; item 7 is approved as deferred. For
clarity on scope, because the question came up: the dataclass binder is
**not** bundled with the structured-results schema work — the binder builds
now (step 3, runtime JSON→dataclass, no schema); this plan's manifest
extension is only the per-parameter stdin marker; return-annotation →
`returned_schema`/`--describe` stays in the post-1.0 structured-results
plan. Only the type-universe *spec* is shared between them.

1. **Exit code value.** 64 (`EX_USAGE`) is the recommendation, with a second
   argument found in review: argparse, click and pytest usage errors all say
   2, so leaving 2 also stops colliding with the tools tasks most commonly
   shell out to, while 64's collision class (sysexits followers) is rare in
   this ecosystem. Alternatives: 65, or something outside `sysexits`
   entirely.
2. **Binding-level refusals.** Moving them to 64 changes the `code` inside
   `results[]` under `--json` (and `_refuse`'s envelope `error.code`), not
   just the process exit — a second breaking surface. Recommendation: yes,
   in the same release — two breaking surfaces in one pre-1.0 minor beat
   two minors, and the envelope disagreeing with the process exit for the
   same refusal would be incoherent.
3. **Spelling the return marker.** Mostly dissolved by the codebase:
   `coerce.peel` already strips `Annotated` and `Optional` in any order and
   nesting (`coerce.py:115` names exactly this case), so reusing the idiom
   on the return annotation makes `Stdout[dict] | None` and
   `Stdout[dict | None]` mechanically identical. Recommendation: keep
   `Stdout[T]` — it follows the `Arg`/`Forward`/`NoSplit` alias convention,
   and `Emit[T]`/`Document[T]` would break the stdin/stdout pairing, the
   best thing about the naming — and document `Stdout[dict | None]` as
   house style (marker outermost, like `NoSplit[list[X] | None]`). Worth
   considering alongside: a `Stdin[T]` alias for the bare parameter marker
   (`text: Stdin[str]`), same symmetry; the callable `stdin("field")` stays
   alias-less, like `env("VAR")`.
4. **Is the dataclass binder in the first cut of `stdin`, or the second?**
   Recommendation: neither commit-split nor first cut — its own PR
   (sequencing step 3). The `str`/`bytes` forms alone already make footman
   a pipe target and are a fraction of the work.
5. **Does a `stdin` parameter appear in `--help`?** Recommendation: yes —
   the costing flipped in review. Help renders from the manifest, and the
   manifest must carry a boundary-only marker *anyway* (completion would
   otherwise offer `--event` as a flag), so the one-line note ("reads
   stdin: JSON document → StopEvent") is nearly free once the marker
   exists. No dataclass field lists now — those belong to the
   structured-results `--describe` work, sharing the same schema when it
   comes.
6. **`list[str]` from stdin: JSON array, or lines?** Recommendation:
   `stdin(lines=True)`, generalised properly rather than bolted on — lines
   mode means *each line is a CLI token*, flowing through the existing
   scalar coercion, so `Annotated[list[int], stdin(lines=True)]`,
   `list[Path]` and `list[Literal[…]]` work for free and `splitlines()`
   handles both line endings. A pipe of lines ≡ a repeated flag — the
   `xargs` shape — not a str-only convenience. Bare structured types stay
   JSON; no sniffing, ever.
7. **A packaged Claude layer** — build it at all, and if so where. Recorded
   so it is not mistaken for settled. (The decoupling test no longer waits
   on this — it lands in step 1.)

## Appendix — verified, 2026-07-26

Probed against footman at `4e10ea0`/`26063a1`; the Claude contract re-read
from the current docs. Three things commonly believed here are wrong:

- **`interactive=True` does not force a run sequential, and works under
  `--json`.** `schedule.py:616` runs console owners *inside* the parallel
  pool, taking the real terminal through the arbiter's console lane. An
  `interactive=True` task under `--json`, fed a payload on stdin, read it and
  exited 0 with its return value serialised into the envelope. So the reason
  to read at the boundary is coercion, sharing and testability — not that
  `interactive=True` costs too much.
- **`stop_hook_active` still exists** (see above).
- **`check()` does not return a status.** It returns `None` and *raises*:
  `tasks.py:79` ends in `parallel(...)`, which normalises failures to a
  catchable `RunFailed`. Any example calling `check().ok` is wrong.

Also confirmed:

- The stdin guard fires even on a **single-task** run — `ctx.serial_active`
  is false for a plain task, so "it is the only task, therefore serial" does
  not hold.
- A `run()` subprocess exiting 2 propagates to a process exit of 2, so the
  overload bites even a caller that never trips footman's own refusal path.
  This residue survives primitive 1 and is benign: it blocks, and stderr
  honestly describes a real tool failure. The harmful case was stderr
  describing footman's *own command line*.
- `_describe.json_default` already serialises dataclasses out via
  `dataclasses.asdict`. `coerce.py` has no dataclass support inbound, so the
  binder is new machinery, not a wiring job.
- `footman.RunFailed` and `footman.Failed` are both exported.
- `hidden=` is complete as of #78 (`26063a1`) and honoured on all four
  surfaces.

Re-probed in review (2026-07-26, same day):

- `Runner.invoke` has no `stdin=` parameter today (`testing.py:127`) — the
  testing seam is new work, not wiring.
- `_app.py` holds ~8 bare `return 2` sites that never route through
  `_refuse()` (`--where` at :673/:686, completion install/uninstall/setup
  at :828–:870) — the primitive 1 sweep is three layers, not two.
- `coerce.peel` strips `Annotated`/`Optional` in any order and nesting
  (`coerce.py:115`) — the `Stdout[T] | None` composition question is
  already solved on the parameter side.
- A non-declaring task's captured prints replay to **stdout**
  (`schedule.py:286`), and the run summary goes to stderr precisely so
  `fm task > file` captures what the task printed (`_print_summary`'s own
  comment) — stdout already half-belongs to the task; `Stdout[T]` completes
  the doctrine.
- Under `--json`, `_refuse` prints the promised envelope on **stdout**
  (`_app.py:88`) — the contract table's "empty" reads "the envelope" there.
- `_describe.json_default` redacts `Secret` — emitting the document through
  it (not bare `json.dumps`) inherits that, so a declared document cannot
  leak what `--json` would have redacted.
- Help renders from the manifest, not live introspection (`_describe` is
  "pure functions over manifest dicts") — any `--help` note for a `stdin`
  parameter is manifest schema surface, which the completion-exclusion
  marker already pays for.
- The `ask()` docstring pins today's chain at CLI > env > default > prompt
  (`params.py`), and `env` is documented as valid only on a defaulted
  parameter — the two facts the extended precedence rule builds on.
