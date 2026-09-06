# Dotted task addressing — design note

**Status:** **BUILT — all phases in main** (decided GO by Willem
2026-07-25; landed same day as PRs #56 grammar, #58 completion
generosities, #59 default positionals + notices, #60 default-as-child,
#61 composition verbs, #62 dotted cherry-picking). This note is now the
historical spec; the shipped behaviour is documented in docs/ and
CHANGELOG. The case, as argued:
migration cost is moot (pre-1.0, single user); DX outranks code simplicity;
completion is a real depth-independent win; and the dot matches the `tools.*`
Python API (`tools.git.commit` ↔ `fm docs.serve`), so "being different" is
*internal consistency*, not novelty for its own sake. Buildable spec below.
Code references are current as of this writing — re-check before implementation.

## The problem — three spellings for "which task"

Today a task's *address* is spelled differently depending on the surface, and the
three don't agree:

| Surface | space `docs serve` | dotted `docs.serve` | quoted `"docs serve"` |
|---|---|---|---|
| **run** `fm docs serve` | the only way | ✗ unknown task | ✗ unknown task |
| **`--help`** | native | ✓ (added in #15) | ✓ (added in #15) |
| **`--where`** | ✗ dangles | canonical | ✓ |
| **`include(only=…)`** | — | ✗ (top-level names only) | ✗ |

The reason `--where` forces a single token is mechanical: it is a *valued global*
(`_GLOBAL_VALUE`), so it consumes exactly one next word — `_where` then does
`dotted.replace(".", " ").split()` and prints results dotted
(`name.replace(" ", ".")`). Nested addressing therefore *had* to become
one-token there. `--help`'s new tolerance (`_expand_help_path`) was added to match
that instinct. Run stays space-only. So the same concept has three grammars.

## Proposal — dotted-only addressing

A task address is **always a single dotted token**, on every surface:

```
fm docs.serve            # was: fm docs serve
fm dist.build            # was: fm dist build
fm footman.tools.sync    # was: fm footman tools sync
fm build lint test       # unchanged — flat tasks are already single tokens
```

Space stops being a path separator. `.` is the one separator, everywhere:
run, `--help`, `--where`, completion, and the composition verbs
(`plugin()`/`include()`) — source strings and `only=`/`exclude=` filters
alike.

## Surface-by-surface

- **run / chain.** A chain is still separator-free (`fm build lint test` = three
  segments). The change is only that a *nested* segment's task is one dotted
  token instead of a token walk. The first token of every segment is now,
  unambiguously, a complete task address — no group-descent walk needed to
  recognise it.
- **`--help` / `--where`.** Both already take the dotted single token; they simply
  become the *only* form (drop the space walk in `_help_targets`, keep the
  `dotted.replace(".", " ").split()` resolve). One resolver, everywhere.
- **completion.** See the next section — this is where dotted pays out most.
- **`@group.default`.** The "no positional parameter" rule can be **dropped** (see
  *Consequences*).
- **composition.** Two typed sibling verbs over one engine — `plugin()`
  (entry points only) and `include()` (modules only); the `plugins=` config
  key dies, and a source string is itself a dotted address: a
  registry-resolved prefix plus a subpath into that tree (own section:
  *Composing trees*). `only=`/`exclude=` take dotted task addresses too,
  closing the problem table's last row (*Dotted cherry-picking* — sequenced
  last, severable).

## Completion model — the centerpiece

Task completion becomes **path completion**: the `.` is footman's `/`. The
manifest already knows, per node, *is it runnable* (a task, or a group with a
default) and *does it have children* — the same data behind
`fm footman` → "expected a task name… know: docs, tools". The completer reads it:

The completer branches on the node's **runnable-ness**, and the `.`-vs-`space`
choice *is* "descend vs run":

- **Namespace group** (no default — footman's `footman`, `docs`, `dist`) → you
  cannot stop here, so completion **forces the descent**: emit `footman.` with
  **nospace**, and offer only what it exposes. `foo<TAB>` → `footman.`;
  `footman.<TAB>` → `footman.docs.`, `footman.tools.` (children that are
  themselves namespace groups keep their dot — see *Mechanics*).
- **Runnable group** (has a default — a `lint` that lints everything) →
  complete the token to `lint` (append **nothing**, `nospace`) and offer **both**:
  a trailing **space** (run `lint`'s default, ready for `--fix`) *and* `.python`,
  `.markdown`, … (a `.` descends to one surface). `li<TAB>` → `lint`, then
  `{ " ", ".python", ".markdown", … }`.
- **Task** (runnable leaf) → **terminal**: complete it, add a trailing **space**,
  ready for args. `footman.tools.sy<TAB>` → `footman.tools.sync `.

This runnable-group case is **common**, not hypothetical — the `lint`/`format`
groups in the projects this came from have defaults. The point: the
`.`-vs-`space` distinction lets the completer offer "stop or keep going" as
one in-word choice, which the space grammar can't — once
the shell breaks at ` `, `fm lint ⇥` has already committed to "inside lint."

A full descent reads `foo<TAB>` → `footman.` → `<TAB>` → `footman.tools.` →
`<TAB>` → `footman.tools.sync ` — literally `cd Doc<TAB>/…`.

**Why this needs dotted.** The fluent in-word descent only works because the whole
address is one shell word. With spaces, `fm footman <TAB>` — the shell has already
word-broken at the space, so the completer can only hand you the next segment as a
fresh word; it cannot "append the separator and keep you tabbing inside the same
token." Dotted turns task completion into the most familiar completion idiom
there is. Driven by the standard `nospace` mechanism every shell supports for `/`.

**Mechanics are candidate-shape, not nospace — one emission rule, `ls -F`
style.** Not every shell has a per-candidate no-space flag (bash/zsh do; fish
and nushell do not — nushell appends a space after a unique match). The
portable mechanism is what gets *emitted*, and it is a single rule: candidates
sit **one segment beyond the typed prefix** (directory-listing collapse —
never the flat index), and a **namespace-group candidate always carries its
trailing dot** (`docs.` `dist.` `build` `test`) the way `ls -F` appends `/` to
directories; runnable groups and tasks are bare. The dot-marking does two
jobs: it gives bash users the descend-vs-run signal (bash renders no
description column), and even a *unique* namespace match in a space-appending
shell lands the cursor after the dot — worst case the shell adds a stray space
after `fm docs.`, which the strict resolver answers by listing the children;
without the dot the same shell would strand the user at a bare `fm docs` plus
space. On a unique namespace match, additionally **skip
ahead**: emit its children as full addresses (`footman.docs.`,
`footman.tools.`) so the candidate set stays non-unique and no shell forces a
space at all — every shell's common-prefix machinery stops at `footman.`. For
a runnable group, emit the group itself *plus* its dotted children, so the
common prefix is `lint` and the user's next keystroke (space or `.`) is the
stop-or-descend choice — no shell can literally offer "a trailing space" as a
candidate. A single-child namespace group auto-descends (complete straight
through, as zsh does for lone subdirectories). Spec these as per-node emission
rules and verify them in `test_shellcomp.py` against all five shells; prefer
non-unique candidate sets over relying on a no-space flag wherever a shell
lacks one.

**Segment-wise abbreviation — the other half of the `cd` idiom.** zsh expands
`/u/l/b⇥` to `/usr/local/bin`; the completer does the same over the tree:
`fm f.t.sy⇥` → `footman.tools.sync`. Each typed segment prefix-matches its
tree level; when the whole word resolves uniquely, emit the one expanded
candidate (when a segment is ambiguous, expand up to it and list that level's
matches). **Completion-only** — the runtime resolver stays strict, so an
abbreviation that runs today cannot change meaning when a new task lands, and
scripts cannot rot. Because footman generates the candidates itself, every
shell gets the expansion, not just zsh. This mostly erases the "dotted
addresses are long to type" tax.

**Leaf-name fallback.** When the typed token prefix-matches **no** top-level
segment, complete against *last* segments over the flat index instead:
`fm serve⇥` → `docs.serve`. This rescues the "I know the task, not where it
lives" user — the one discoverability regression dotted introduces versus a
flat listing. The zero-top-level-matches guard means the fallback can never
pollute a first tab or a valid descent.

**Descriptions ride along.** The completion wire format already carries
`name\tsummary` per candidate (`_describe` in `_complete.py`), and the shells
that render descriptions show them today. Two dotted-specific rules: a
runnable group's *self* candidate carries its **default's** summary, so the
stop-or-descend menu explains what "stop" runs; a namespace candidate
(`docs.`) carries the group's help line.

**User docs stay out of these vagaries.** `completion.md` describes the
path-style model shell-neutrally, with one line — all five shells supported to
the best of each shell's ability — linking to a new advanced page ("Shell
differences", last entry in the existing Completion nav group) that covers
observable per-shell differences only. The emission rules themselves live here
and in the tests.

## Consequences — the cascade (each reinforces the last)

1. **One address spelling everywhere.** run == `--help` == `--where` ==
   `include()` sources == `only=`/`exclude=` filters. The problem table
   collapses to one column — all four rows, no residue. One resolver in the
   code.
2. **Path-style completion** (above) — a real UX win the space model structurally
   cannot match, not merely "consistent spelling."
3. **Group defaults can take positionals.** The `@group.default` "no positional"
   rule exists *only* because `fm lint foo` is ambiguous today (is `foo` the
   `lint` default's arg or the `lint.foo` subtask?). Dotted makes the subtask
   `lint.foo`, so `fm lint foo` is unambiguously the default with positional
   `foo`. The rule dissolves; several splitter tie-breaks go with it. The
   sharpest framing of why this *requires* dotted: under spaces, a
   positional-wins rule would **orphan the child** (`fm lint python` could
   never mean the subtask — it has no other spelling); under dotted every node
   keeps an unambiguous address, so positional-wins costs nothing. The two
   features need each other.
4. **Identity and address, cleanly split.** A source string is an address
   in the *provider's* space — identity prefix plus a path in the advertised
   tree, dots continuing seamlessly (`plugin("acme.deploy.staging")`); the
   mounted address is the *consumer's*, and the node's own name is the
   bridge. `plugin("acme.devkit")` — one line — is a whole devkit CLI, its
   groups landing at top level and tracking the package. (See *Composing
   trees*.)
5. **The reserved-namespace question dissolves.** Built-ins are ordinary
   opt-in plugins; whoever writes the pull line picks placement
   (`plugin("footman.tools", into="acme.tools")` brands a white-label CLI in
   one line). No prog-mirror machinery, no script-name alias. See
   *Composing trees*.

## The reserved namespace — dissolved

Earlier drafts weighed fixed-`fm` vs prog-mirror (built-ins at `<prog>.`,
plus a two-binary alias so `fm.` and `footman.` both resolve). The
composition rework dissolves the question: built-ins are **ordinary opt-in
plugins** — nothing auto-mounts (verified: `_app` mounts only config-listed
names today, and the config key is gone) — so the author who writes
`plugin("footman.tools")` picks placement, and a branded CLI writes
`plugin("footman.tools", into="acme.tools")`. Branding is a one-line
authoring choice, not framework machinery. Prog-mirror, the alias
amendment, and the reserved namespace itself all delete. (If built-ins
should ever become omnipresent, that is one internal bootstrap pull in
framework code — still the same user-facing mechanism.)

## Two registries, one grammar (was: three kinds of dots)

Both verbs share one string grammar — resolve the **longest prefix in the
verb's registry**, then walk the remainder inside the resulting tree — and
each verb reads exactly **one** registry: `plugin()` the installed
`footman.tasks` entry points (metadata, side-effect-free), `include()` the
importable modules (imported under capture). No string is ever resolved
against both registries, so there is no precedence, no both-viable
ambiguity, and no silent re-pointing when a new package lands — the old
"do not conflate the three dot-namespaces" warning dissolves into typing.
Every dot a user types in a task context is a task address; the Python
import path appears in exactly two author-owned places: `include()`'s
module prefix, and the right-hand side of an entry-point declaration
(`acme.ci = "acme_ci:tasks"`), where the `=` separates the identity being
claimed (left) from the private import path (right).

## Costs, and what this does NOT solve

**The one residual cost — smaller than first framed.** The first framing
("footman becomes the one task runner spelling subcommands with dots —
git/docker/kubectl/cargo all use spaces") compared against the wrong category:
those are subcommand CLIs, not task runners. Among **task runners**, joined
single-token addresses are the *norm* — rake `db:migrate`, gradle
`:app:build`, go-task `lint:fix`, just modules `foo::bar`, npm's `build:watch`
convention. Space-nested addressing — the current model — is the category
outlier. footman differs only in **glyph**, and the glyph is defensible twice
over: `.` matches the `tools.*` API, and it is mechanically the best joiner
for a completion-centred design — `:` sits in bash's default `COMP_WORDBREAKS`
(the reason bash-completion ships `__ltrim_colon_completions`) and doubles as
zsh's candidate:description separator, making the task-runner-conventional
colon the worst completion citizen of the plausible joiners; `/` collides with
file-path positionals and their completion. This paragraph is the ready-made
FAQ answer to "why not `:` like rake?".

With **no users to break** (pre-1.0, single user), what remains is a
**newcomer first-impression** cost: someone typing `fm lint python` out of
git-habit. That is neutralised by the permanent teaching error (see
*Migration*): the space form is *taught against*, once, helpfully — not
supported. Nesting depth is a non-factor — dotted reads and works the same at
`a.b` or `a.b.c.d`; the decision does not depend on how shallow or deep anyone
nests.

Weighed against that single, mitigable stumble: consistent addressing everywhere,
depth-independent path-style completion, config==CLI parity, group-default
positionals, address-vs-arg disambiguation, and the `tools.*` API match. The DX
ledger is not close.

**A space fallback would forfeit the win.** Accepting both space *and* dotted keeps
the splitter's group-walk and the positional ambiguity — all the complexity plus
more, none of the simplification. If we do this, it is dotted-*only*; the space
form is a taught error, never a working path. (Hybrid is rejected on purpose.)

**It does not subsume #9.** The optional-`Arg[T]` ambiguity is `fm files build` —
is `build` the pattern or the next task? Both are *top-level* single tokens, so
dotting doesn't disambiguate them; #9 still keeps "no task-name peeking" + the
`+` boundary. Dotted helps *nested* boundaries, not the optional-positional one.

**The arity problem stays.** "Where do a task's positionals end and the next task
begin" still needs the manifest's arity knowledge. Dotted simplifies *path
recognition*, not *argument counting*.

## Implementation sketch (what changes / deletes)

- **`split.py`** — the chain splitter drops the group-descent loop
  (`split_chain` lines ~259–262): a segment's leading token is resolved as one
  dotted address instead of walked token-by-token. This is a **modest trim** (a
  handful of lines), *not* a sweep — the six rules are all about argument parsing
  (arity, positionals, options, globals) and are untouched, as is the `+`
  boundary. Also detect a bare `group task` pair here to raise the **teaching
  error** ("nested tasks use dots: `fm group.task`"). Dotted *filenames* in
  chains (`fm build test.py`) resolve by arity exactly as today — no new silent
  behaviour — but segment-boundary errors should state both readings ("`test.py`
  is not a task; if it was a file for `build`, its positionals were already
  full").
- **`_complete.py`** — the endpoint-aware completer (see *Completion model*): a
  prefix matcher over the flat dotted-address index that reads runnable-ness to
  choose the candidate shape per node (namespace → dot-marked children,
  runnable group → self + children, task → terminal), plus segment-wise
  abbreviation expansion and the zero-match leaf-name fallback (both specced
  in *Completion model*). This is where the real DX lives; whether it
  also *simplifies* the code (drops "which group am I in") is secondary and
  unverified — DX outranks it either way. Also **crash-proof the transition
  TAB**: `complete_cli` today validates only that `tree` is a dict, then walks
  with direct indexing — a reshaped tree read from a pre-upgrade cache would
  traceback into the prompt. One comparison (`data.get("schema") != 1`,
  duplicated literal — the hot path can't import `manifest.py`) routes a
  mismatched cache into the existing `_cold_build`, so the first post-upgrade
  TAB serves correct dotted candidates. Not compat machinery (real runs re-sync
  the manifest anyway); it pays again at every future schema change.
- **`_app.py`** — `_help_targets` / `_expand_help_path` collapse to a single
  dotted resolve (drop the space walk); `_where` already resolves dotted. One
  shared `resolve(dotted)` for run / `--help` / `--where`. Help/list/tree
  output prints **full dotted addresses** for nested tasks (`docs.serve`, not
  an indented bare `serve`) so every listed task is copy-paste-runnable; error
  listings ("expected a task name… know:") do the same.
- **`registry.py`** — `@group.default` drops the positional-parameter rejection
  (consequence 3); `#5`'s `RegistrationError` for a positional goes away (its
  message — "a bare word after a group names a child" — is the exact sentence
  dotted falsifies; unwinding it, including the `--` passthrough advice, is the
  tripwire that consequence 3 is fully done). `@group.default` additionally
  registers the task as the child named `default`, with `default_task`
  derived from it; any task registered under that name routes through the
  same validations, decorator or not — the name is the mechanism (see
  *Dotted cherry-picking* — the default self-excludes from its own fan-out;
  only a *group* named `default` is illegal). Also **name legality** (new):
  `cli_name` normalises `_` but never rejects a dot — with `.` as the
  separator, `group("v2.0")` or a dotted `@task(name=…)` would alias into fake
  nesting or become unreachable. Reject `.` (and whitespace) in task names,
  group names, and mounted plugin path segments at load time. The shared
  resolver is **strict**: `docs..serve`, `.docs`, and trailing `docs.` are
  errors (trailing dot = "incomplete address", list the children), never
  silently normalised away by the replace/split.
- **`compose.py` / `config.py`** — the composition rework (own section
  *Composing trees*): `mount_plugins` and the `plugins=` config key delete;
  `plugin()` is reborn as `include()`'s entry-point-only **sibling verb**
  (one shared engine — resolve, walk, land, filter, merge — with only
  resolution differing); node-lands-under-its-own-name plus container splat
  replace name==path; dotted-string `into=` (auto-vivifying);
  provenance-based collisions with the source identity recorded on every
  imported node; the `_overlay`-style recursive leaf merge lands here once.
  `fm --plugins` lists installed entry points with pulled-or-not provenance
  and two-tier descriptions (dist `Summary` cold, tree help warm). Then,
  riding the merge: dotted `only=`/`exclude=` (own section — last phase,
  severable). Whether `Group`/`ModuleType` survive as programmatic
  `include()` sources (tests) is a build-time call — not an export
  mechanism either way. footman's own pyproject swaps its `plugins=` list
  for two `plugin()` lines in `tasks.py`.
- **Manifest** — already carries runnable/children/arity; dotted needs no new
  data, just a flat address index for completion.
- **Docs** — `typing.md` (the core mapping still holds), a new addressing section,
  `monorepos.md` (nested tasks read dotted), completion pages, migration note.
  User pages stay **shell-neutral** on completion mechanics: `completion.md`
  gets the path-style model plus one line ("all five shells, to the best of
  each shell's ability") linking to a new advanced "Shell differences" page —
  last entry in the existing Completion nav group — covering observable
  per-shell differences only. Emission rules live in this note and
  `test_shellcomp.py`, not user docs.

## Composing trees — two typed verbs over one engine

Converged (2026-07-25, over three review waves with Willem). The
`plugins = [...]` config key **dies**; composition is **two sibling verbs
sharing one engine** — resolution differs, everything after it (walk, land,
filter, merge) is identical code:

- **`plugin("acme.devkit.lint", …)`** — **entry points only**: the longest
  installed `footman.tasks` prefix is the identity; the remainder walks the
  advertised tree.
- **`include("mytasks.lint", …)`** — **modules only**: the longest
  importable prefix (imported under capture, as today); the remainder walks
  the captured tree.

The type tag lives in the verb. That restores typed-source safety — no
string is ever resolved against two registries, so the precedence and
silent-re-pointing hazards never exist and need no warning — without the
old nesting (`include(plugin("x"))` → `plugin("x")`). Both verbs take
`into=`, `only=`, `exclude=`, `override=`, and return the grafted target.
An unresolvable string errors against its own registry: `plugin()` lists
the installed entry points (today's typo protection, plus the
claimed-by-two-distributions check); `include()` reports the import
failure.

**The model is Python imports.** `plugin("acme.devkit.lint")` is
`from acme_devkit import lint` for task trees; `plugin("acme.devkit")` is
the `import *` — safe here, because local definitions silently win and
imported-vs-imported clashes are loud; `include("mytasks.lint")` is the
same grammar over your own modules. The identity finds the tree, then
steps aside.

**Landing: a node lands under its own name; identity never becomes an
address.** The pulled node lands in the target (default: root) under its
*own* name — `plugin("acme.devkit.lint")` → `fm lint`. A module capture's
root is an anonymous container, so pulling it lands its children — the
splat: `plugin("acme.devkit")` puts every devkit group at top level, one
line, and a devkit update that adds a group just appears on the next sync —
advertised, pulled, zero consumer edits. The entry-point identity is
consumed at resolve time and retained as **provenance**: recorded on every
imported node (TaskView's provenance fields), cited by collision messages
("`lint` claimed by both `acme.devkit` and `other.kit`"), surfaced by
`--plugins` and `--where`. Placement is always the consumer's — `into=` (a
dotted address string, auto-vivifying) retargets; there is still **no
rename**, so the mounted name is always the node's own. `only=`/`exclude=`
are **relative to the pulled node**:
`plugin("acme.devkit.lint", only=["python"])` keeps `lint.python`. The
subpath and filter forms converge: `plugin("acme.ci", only=["deploy"])` and
`plugin("acme.ci.deploy")` build the same tree.

**Entry points export; modules are local reach.** An entry point is the
console-script of task trees: an installed package's stable identity for a
Group it offers — name public, import path private, enumerable, inert until
pulled. It is the only way to export tasks *across a distribution
boundary*. Module includes are composition within your own reach —
file-splitting, monorepo-local sharing — which was never "export", so the
boundary principle holds and there is no packaging floor for local code.
And zero-code mounting is not a lost feature — **it never existed**: `fm`
refuses to *run* without a tasks file (`_discover` exits 2, "no tasks file
found"; bare `fm`/`--list`/`--help` are deliberate warm empty states), and
`mount_plugins` only ever ran after discovery succeeded. Tested:
`test_app.py` covers the refusal, the warm bare state, `--help`-with-note,
and the JSON envelope.

**`fm --plugins` (approved).** Lists installed `footman.tasks` entry
points, marked pulled-or-not and where they landed (the provenance
cross-ref) — "installed but nobody pulled it" becomes visible.
Descriptions: the entry-point record itself **cannot** carry one (the
packaging spec is strictly `name = "module:attr"`), so the listing is
two-tier — an unpulled plugin shows its **distribution's `Summary`**
(pyproject `description=`, read from `ep.dist` metadata, zero imports); a
pulled plugin shows its **tree's own help**, already in the manifest. To
make devkit-style containers describe themselves, adopt the advertised
module's docstring as the container root's help line. One entry point per
package (the convention) makes Summary ≈ plugin description;
multi-entry-point packages share it.

**Publisher shape convention (docs).** Advertise either **one named group**
(ecosystem plugin — lands as `fm ci`) or **a container of groups** (devkit —
splats as top-level groups). Loose tasks in a published container are a
smell: the splat drops them straight into every consumer's top level.
Entry-point *names* stay vendor-prefixed (`acme.devkit`) for uniqueness in
the shared registry — identity hygiene now, not address design.

**Collisions are provenance-based, not order-based.** Local-vs-imported:
the local definition silently wins, whatever the file order — the cascade's
"user names shadow plugins" principle, carried by provenance (TaskView
already records `defining_dir`/`shadowed`/`shadow_chain`).
Imported-vs-imported: loud `RegistrationError` unless `override=True` —
every pull is authored, so a clash is a bug with a one-line fix
(`exclude=`/`into=`), and loud beats silently running the wrong task.
Local-vs-local: loud, as ever. The config era's warn-plus-last-mount-wins
machinery deletes. The `_overlay`-style recursive leaf merge is specced
once, here: two pulls into one subtree compose all the way down; only a
same-address leaf (task-vs-task or type clash) conflicts, and two runnable
groups meeting clash at `x.default` by construction (see *Dotted
cherry-picking*). Overlapping re-pulls clash loudly at their addresses;
disjoint re-pulls of one source (two filters) compose.

**Built-ins are ordinary opt-in plugins.** footman's own tooling is
`plugin("footman.tools")` in the consumer's tasks.py — footman's own repo
included (its pyproject `plugins=` list becomes two `plugin()` lines in its
`tasks.py`). Branding is `into="acme.tools"`. See *The reserved namespace —
dissolved*.

## Dotted cherry-picking in `plugin()`/`include()` — in scope

The last disagreeing surface from the problem table (decided in scope,
2026-07-25). `only=`/`exclude=` accept full dotted addresses; matching stays
exact — no globs, because the whole-group spelling `only=["docs"]` *is* the
glob:

```python
plugin("acme.shared", only=["docs.build", "fmt"])  # one nested task + one flat
plugin("acme.ci", exclude=["deploy.prod"])  # everything but one leaf
plugin("acme.ci.deploy")  # subpath form: one subtree
include("mytasks.lint", only=["python"])  # same grammar, module registry
```

Semantics — each rule reuses one the plan already has:

- **Grafting a nested address materialises its path.** `only=["docs.build"]`
  forks the source (as the engine already does), prunes `docs` to just
  `build`, and grafts via the same `_overlay`-style recursive merge as every
  pull — an existing target `docs` group composes rather than being
  clobbered. Intermediate group nodes are the source's own forked copies
  (`_fork` carries every Group field), so group help text and flags ride
  along.
- **Collisions follow the one collision model.** Depth changes where a clash
  can happen, not what happens: a leaf conflict at any depth (task-vs-task,
  or task-vs-group type clash) is the same loud `RegistrationError` unless
  `override=True`; coexisting leaves are composition, silent — exactly the
  imported-vs-imported rule (see *Composing trees*).
- **Default-ness survives only if the default survives — literally, because
  the default is the child named `default`** (next paragraph). Cherry-picking
  `lint.python` grafts a default-less `lint` (bare `fm lint` gets the taught
  no-default error) — keeping the default would resurrect a task the caller
  chose not to include. `only=["lint"]` (whole group) keeps it, as today.
  And the two node-granular spellings exist, readable without ever opening
  the source: `only=["lint.default"]` grafts *just the default* (a runnable
  `lint` with no other children); `exclude=["lint.default"]` grafts
  everything *but* the default. No pointer bookkeeping — dropping the
  `default` child *is* dropping default-ness; a group pruned empty is
  dropped entirely, not grafted as a shell.
- **Union semantics.** `only=["docs", "docs.build"]` is redundant, not an
  error — the whole group subsumes the leaf.
- **Validation stays loud, now per-segment.** The existing typo protection
  resolves each address segment-wise: `only=["docs.buidl"]` → "no task or
  group at 'docs.buidl' (docs has: build, serve)". The interim "teach the
  limitation" error (below) dissolves into real support.
- **One grammar.** In `plugin("acme.shared", only=["docs.build"])` the
  source string and the filters live in the same address space — the source
  names the root, the filters are addresses beneath the pulled node (see
  *Two registries, one grammar*).

**The default needs an address — it is the child named `default` (amends the
landed #19 design; the one item in this plan that changes an existing
feature).** Today the default is stored only as a field (`registry.py`:
`self.default_task = fn`, never entered in `group.tasks`), so it is the one
piece of runnable code in the tree with no address — an anomaly under "every
node has exactly one spelling", and it makes "just the default" / "everything
but the default" unspellable. Fix: `@group.default` registers the task as the
child **`default`** — a fixed, well-known name, *not* the function's cli name.
The decorator you wrote is the address you type (`@lint.default` ↔
`fm lint.default`); a consumer can spell `only=["lint.default"]` without
reading the plugin's source; and the author's function name stays private —
it never becomes public API, so renaming it is free. Bare `fm lint` stays the
idiomatic invocation — `GET /` → `/index.html`, and note the analogy's second
half: the default document has a *well-known name*, not whatever the author
called the file. Default-ness stops being state: `default_task` becomes
derived (`tasks.get("default")`), so fork/prune/merge can never desync a
pointer. The fixed name is also what makes the plugin-merge story airtight:
two runnable groups merging clash at `lint.default` *by construction* — an
ordinary same-address leaf clash, the one loud warning, last mount wins.
(Under fn-name registration the two defaults could land at different
addresses, silently coexist as "composition", and the which-is-default
conflict would be unreportable — the special case would survive.) And
`default` is not a *reserved* name but a **meaningful** one — the name is
the mechanism (next paragraph); the single illegal spelling is a *group*
named `default`, because a group-typed default is incoherent (bare `fm lint`
would resolve to another bare group — turtles). Wrinkles: an empty-body
fan-out default excludes
the `default` child (itself) from its own fan-out set; completion and help
gain the entry (`lint.default` in the menu is self-describing, and the
description column carries the summary).

**The name *is* the mechanism — `@group.default` is sugar (decided: the
"works correctly" branch, not the "forbid" branch).** Any *task* that ends
up named `default` — via the decorator, `@task(name="default")`, or arriving
through a pull (`plugin()`/`include()`) — simply *is* its group's default. Registration under
the name routes through the decorator's validations either way (empty body ⇒
the fan-out flag, the interactive check), so there is one code path and no
second-class defaults. This makes "add a default to a group that never
declared one" a first-class move: a provider can ship a task named `default`
precisely so `plugin("acme.linters", into="lint")` makes `lint` runnable —
and an empty-body one fans out *the group it lands in*, because default-ness
is **parent-relative**; a name-preserving move re-roles it correctly.
(Implementation note: the `_DEFAULT_GROUP` back-reference on the fn must
become derived/parent-relative, or a moved fan-out default would fan out its
*old* group.) This leans on a stance now deliberate: footman has **no
generic rename** — the composition verbs move tasks *between groups* with names
intact, and a name changes only where it is declared; so "rename to
`default`" is always spelled as declaration or as moving an
already-`default`-named task, never as a rename operation. A *group* named
`default` stays illegal — a group-typed
default is incoherent (bare `fm lint` resolving to another bare group,
turtles) — which is also why `into="lint.default"` is a load-time type error
(`into=` names a *group* to graft into), teaching the two working spellings:
declare `@lint.default`, or pull a `default`-named task into `lint` —
`plugin("acme.linters.default", into="lint")`, the subpath form making
adoption of a provider's default a one-liner. Collisions need no new rules: an incoming `default` meeting an
existing one is the ordinary loud leaf clash (`override=True` = adopt
theirs; imported-vs-imported = loud; a local `@lint.default` over a
plugin's = provenance local-wins). One honest footgun to document: naming a
task `default` *means something* — the group containing it becomes runnable —
so the docs say the name is meaningful, exactly like `tasks.py` itself is.

**Sequencing: last, and severable.** This rides the recursive-merge machinery
from *Composing trees*, so it lands after it (the default-as-child
registration change, which the merge needs, lands *with* the composition
work, not with cherry-picking). If dotted ships before cherry-picking is done, the
interim behaviour is the dotted-aware taught error ("`only=` filters the
source's top level; dotted cherry-picking is coming"), never a silent
no-match.

## Migration & the teaching error

Pre-1.0, single user — the one-time rewrite of the consuming projects'
addresses is
a find-replace, not an event. `fm docs serve` → `fm docs.serve`; flat usage
unchanged.

The **teaching error is permanent**, not a transitional aid: when the splitter
sees two bare tokens that spell a known `group task` pair, it refuses with *"nested
tasks use dots: `fm docs.serve`"*. This is the whole mitigation for the newcomer
first-impression cost — a git-habit `fm lint python` becomes a one-time lesson,
not a cryptic failure. It lives in the **error path only** (a lookahead when the
first token names a group and the next names a child of it), so it never
reintroduces the happy-path group-walk. The space form is *taught against*, never
*supported*. Make the lookahead **longest-path**, so `fm footman tools sync`
teaches the full `fm footman.tools.sync` and `fm docs build lint` teaches
`fm docs.build lint` (stop at the longest resolvable prefix, keep the rest).

**The teaching error is not the whole mitigation — consequence 3 carves a hole
in it.** It only exists where no valid parse does, i.e. a *namespace* group. For
a **runnable group whose default takes positionals**, `fm lint python` *is* a
valid parse (the default, positional `python`), so the git-habit form is
**silently reinterpreted**, not taught — the worst outcome, especially for
path/pattern positionals where nothing errors. Mitigation: a **child-collision
notice** — when a bare positional value handed to a group default exactly
equals a child name, print one stderr line
(`note: ran lint's default with 'python'; for the subtask, fm lint.python`).
The grammar stays deterministic (positional wins); a legitimate value that
happens to equal a child name (a `python/` directory) still works and just
carries the note.

The notice must cover **near-misses** too, because consequence 3 creates a
brand-new trap in this corner: today `fm lint pyhton` errors ("unknown task");
under positional-wins it becomes a *valid* parse — lint filters on a pattern
matching nothing and **exits 0**. Extend the notice to edit-distance-1 child
matches (`note: 'pyhton' ran as lint's positional; nearest subtask:
fm lint.python`), reusing the did-you-mean machinery. And give the
exact-collision notice a documented **quiet spelling**: a path-shaped value
(`fm lint ./python`) skips it, so a legitimate value that happens to equal a
child name is not nagged on every run forever. (Do not lean on `--` as the
silencer — in the current grammar `--` is passthrough, terminal for the whole
line, not a general positional-forcer.)

Since the error path is now the only teacher, **commit to did-you-mean
suggestions** over the flat dotted index (`fm docs.sevre` → "did you mean
`docs.serve`?") — the flat index makes this nearly free.

## Relationship to #9

Related axis, not the same. #9 (`Arg[T]` optional trailing positional + `+`/`--`)
is about **segment boundaries** — where one task's args end. Dotted addressing is
about **task identity** — how you spell one task's address. They compose: land the
grammar (dotted) first so #9's `+` boundary and completion hints sit on a decided
addressing model rather than beside an undecided one.

## Decisions

1. **Go / no-go on dotted-only** — **GO, decided** (Willem, 2026-07-25;
   DX-driven; migration moot; `tools.*` API match justifies being different).
2. **Reserved namespace** — **dissolved** by the composition rework (see
   *Composing trees*): built-ins are ordinary opt-in plugins; the pull
   author picks placement. Supersedes the earlier prog-mirror +
   script-name-alias decision.
3. **`--help`/`--where`** — **dotted-only too**; drop the space walk, one shared
   `resolve(dotted)`.
4. **Transition** — **hard break + a permanent teaching error** on the space form
   (not a working fallback).

### Still to pin down during the build

- **Config remap — superseded with the config key itself.** Relocation is
  `include(..., into="dotted.path")`. (The `as` list form and the
  TOML-landmine analysis are historical.)
- **Collision model — re-resolved under include()-only: provenance-based.**
  Local-vs-imported = silent local wins, any order (TaskView provenance).
  Imported-vs-imported = loud `RegistrationError` unless `override=True` —
  every mount is authored now, so a clash is a bug with a one-line fix
  (`exclude=`/`into=`), and loud beats silently running the wrong task.
  Local-vs-local = loud, as ever. The config era's warn-plus-last-mount-wins
  is superseded with the key.
- **`fm ⇥` first-tab — resolved by the emission rule**: candidates sit one
  segment beyond the typed prefix (directory-listing collapse), so the first
  tab lists top-level segments only — never the flat set. Deeper reach is
  explicit, not fuzzy: segment-wise abbreviation (`f.t.sy⇥` →
  `footman.tools.sync`) and the zero-match leaf-name fallback (`serve⇥` →
  `docs.serve`).
- **`+`/#9 interaction** — dotted lands first; #9's `+` boundary and optional
  `Arg[T]` sit on top (unchanged by dotted; see *Relationship to #9*).
- **`as`-form micro-rules — superseded with the config key.** What survives
  moved to *Composing trees*: duplicate mounts dissolve into the collision
  model; `into=` targets pass name legality, and `into` naming a task-typed
  address (`into="lint.default"`) is the group-typed-default type error (see
  *Dotted cherry-picking*).
- **Prog-named user groups — dissolved with the reserved namespace.**
  `fm`/`footman` are ordinary group names; nothing mounts anywhere unless an
  include line says so.
- **Entry-point prefix shadowing (build-time):** `plugin("acme.ci.deploy")`
  resolves the longest installed entry-point prefix; if a shorter prefix
  would *also* resolve fully (entry point `acme.ci` with subtree `deploy`,
  plus an installed entry point `acme.ci.deploy`), warn naming both
  readings — silent re-pointing when a new package lands is the thing to
  avoid (the cross-registry hazard is gone: each verb reads one registry).
  Confirm the exact rule at build, alongside whether `Group`/`ModuleType`
  survive as programmatic `include()` sources for tests.
- **Dotted `only=`/`exclude=` — in scope** (own section: *Dotted
  cherry-picking in `plugin()`/`include()`*, decided 2026-07-25). Sequenced
  last and severable: if dotted ships first, the interim is the dotted-aware
  taught error ("`only=` filters the source's top level; dotted
  cherry-picking is coming"), never a silent no-match.
