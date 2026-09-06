# Completion suffix for comma-continuable values — investigation brief

**BUILT — ruled in and shipped the same day (2026-08-14).** Willem ruled
"let's go" on the proposal as written, all four opens resolved as
recommended: bash takes the nospace trade, fish rides the candidate-side
comma, dynamic values are in v1, and the names are `_MORE` /
`\x00more` / `_EXIT_MORE = 102`. The design below is what was built,
with one precision found during the build: a *dynamic positional* is
marked from its first element too — that branch returns a pure
`_DYNAMIC` reply (never the mixed menu), so the purity argument that
marks attached values from the first element applies to it unchanged.
The CHANGELOG carries what shipped; this note keeps the measurements
and the reasoning.

Original brief: handoff for a session to design and (if ruled in)
build the lane. Willem's constraint, verbatim in spirit: **no
playground-only behaviour** — the feature exists only if at least one
real shell delivers it; the playground then mirrors the same signal.

**2026-08-14, later the same day: every per-shell mechanism below is now
MEASURED** (real interactive shells on a pty, minimal completers mirroring
the hooks' shapes — zsh 5.9, bash 5.3, fish 4.8, nushell 0.114, pwsh 7.5).
The measurements rewrote the itch — two of the five shells never had it.

## The itch

Completing an element of a comma-splitting value ends the token:
`--regions=e<Tab>` → `--regions=eu ` (trailing space). To continue the
list you must delete the space and type the comma. Every shell hook
behaves this way today, and so does the playground prompt. The
completer itself already *knows* the value can continue — mid-list it
filters items already typed (`_choice_tokens`, the `given` filter) and
answers with the remainder.

(Measurement corrected the "every shell" claim: bash, zsh and fish
insert the trailing space; pwsh and nushell never did — see the
per-shell section.)

A page-only fix was built and **reverted** (2026-08-14, this session):
the playground probed the completer with `candidate + ","` — non-empty
answer means continuable — and suppressed its inserted space. Exact
semantics, one probe per menu, ~15 lines. Rejected because it
flattered the playground with behaviour no shell delivers. The probe
idea itself is sound and may return as the *playground's consumer* of
the real signal — or the page can simply read the same protocol marker
the shells will.

## Where everything lives

- `src/footman/_complete.py` — the stdlib-only hot path. Precedents for
  side-channel answers: `_FILES` sentinel + exit 100 (path value →
  shell's file completion), `_FILES_CSV` + exit 101 (comma-splitting
  path mid-list), `_DYNAMIC` sentinel (recompute via `_suggest`).
  `_csv_head()` splits a partial at its last comma; `_choice_tokens()` /
  `_attached_value()` build `head+choice` whole-token candidates and
  filter `given` items. THE fact this feature rides on: the completer
  can tell, at answer time, that candidates are elements of a
  comma-splitting value with items remaining.
- `src/footman/_shellcomp.py` — the five shell hooks. bash already
  conditionally sets `compopt -o nospace` (directory-continuation
  cases, exit 100/101 branches); that is the per-reply mechanism this
  feature would use on bash.
- Playground consumer: `docs/assets/playground.js`, `insertCompletion`
  (the `glue` decision) and `_fm_complete` in the BOOTSTRAP template
  literal (no backticks / `${` in that literal — a guard test enforces
  it).

## Per-shell mechanisms — MEASURED 2026-08-14

Method: each shell interactive on a pty (pyte-rendered screens; the
fish/pwsh/nushell runs rode `footman.tasks.docs`' cast machinery, which
answers the terminal queries fish 4.x blocks on), with a minimal
completer mirroring the hook's exact feeding idiom — zsh through
`_describe` with items carrying descriptions, bash through a
COMPREPLY-building function, the others through their native registration.

- **zsh — the star, confirmed on every count.**
  `_describe -t probe 'probe' items -q -S ','` forwards the compadd
  options through (the hook's own `_describe` call takes them appended,
  no restructuring): accepting `--regions=e⇥` inserts `--regions=eu,`
  **and immediately lists the remaining elements**; a following space
  removes the comma (`--regions=eu ␣`); **Enter removes it too** — the
  executed argv was measured as `--regions=eu`, not `eu,`; typing `,`
  over it does not double it; `⇥u⇥` chains to `--regions=eu,us,`.
  Per-reply application is trivial: append `-q -S ','` only when the
  resolver marked the reply.
- **bash — confirmed, and the trade is smaller than feared.**
  Per-reply `compopt -o nospace` (already the hook's idiom in the
  100/101 branches): unique match inserts `--regions=eu` with the
  cursor glued (typed `X` landed as `euX`), mid-list continuation
  works through the `=`-split word, and the final element indeed gets
  no space either — `--regions=eu,us,apX`. The trade reads fine
  because pwsh and nushell already behave exactly this way for *every*
  completion (below), and nobody calls those shells broken.
- **fish — wrong on both guesses: there IS a mechanism.**
  fish appends a space after a unique match (`--regions=eu ␣`) —
  *unless the candidate itself ends with a comma*: `complete -a
  '--regions=eu,'` inserted `--regions=eu,` with the cursor glued
  (`eu,X`). So fish's spelling of the feature is candidate-side: the
  hook appends `,` to each value when the reply is marked. Costs: the
  pager displays the comma, and accepting a *final* element leaves a
  trailing comma in the line — which the grammar already forgives
  (`_split._values` drops empty parts: `eu,us,` → `["eu","us"]`,
  measured at `_split.py:1386`), so the line still runs.
- **pwsh — the itch never existed here.** PSReadLine inserts a
  completion with no trailing space at all (`--regions=euX`). Nothing
  to change; the user types the comma and tabs again, which already
  works (whole-token candidates). Hook untouched.
- **nushell — same: no trailing space** on unique-match insertion
  (`--regions=euX`, nushell 0.114). Hook untouched. (The menu-selection
  path was not driven; irrelevant while the hook is unchanged.)

"At least one actual shell" is satisfied three times over: zsh
(suffix), bash (nospace), fish (candidate-side comma) — and the
remaining two already behave as bash would after the change.

## Protocol sketch (the starting point, kept for the record)

The hot path's answer for an attached comma-splitting value with
remaining items grows a marker the hooks can read — options:

1. A new sentinel line before the candidates (like `_FILES_CSV`), or
2. a new exit code (like 100/101), or
3. a trailing-comma variant riding each candidate (zsh could use
   `-S ''` + candidates that end `,` — but that pollutes bash's
   display and the playground's menu; probably wrong).

(1) or (2) keep candidates clean and let each hook apply its native
mechanism. The playground reads the same marker for its `glue`.

Where measurement moved this: (2) won outright, and (3) turned out to
be exactly right *for fish alone* — as the hook's local spelling of the
per-reply marker, never as the wire format.

## The design (proposed, awaiting ruling)

**Wire protocol: exit code 102** — "the candidates on stdout are
elements of a comma-continuable value" — next in the 100/101 family.
Candidates stay clean; each hook applies its native mechanism; a hook
that doesn't know 102 falls through to its normal stdout parse, which
is byte-for-byte today's behaviour (verified by reading all five hooks:
every one checks 100/101 explicitly and otherwise parses stdout). An
old resolver never exits 102, so a new hook against an old footman is
also unchanged. Both skews degrade to the status quo.

**Inside `complete()`: a leading sentinel element**, in the `_FILES`/
`_DYNAMIC` family — say `_MORE = "\x00more"` (name open) — prepended to
an otherwise-normal candidate reply. `complete_cli` strips it, emits
the rest, and returns `_EXIT_MORE = 102` (after `_maybe_refresh`, as
the other paths do). The playground reads the *same* sentinel from the
same function for its `glue` — no page-only probe, no second protocol;
its current `out[0].startswith(chr(0))` guard must learn the new
sentinel before the generic bail (same repo, same PR, and self-gating
besides: the page runs the released wheel, so the marker cannot reach
the page before the release that carries it).

**When the reply is marked** — on shape, not on remainder: the
candidates are elements of a `multiple and not nosplit` value. Not "an
item remains after accepting", which the reverted page probe computed:
duplicates are legal by design (a list holds what you put in it), so
"remainder" was never the right question, and zsh's auto-remove makes
over-marking cost nothing anyway. Concretely:

- the attached-value branch (`_attached_value`, choices arm): marked
  from the first element — the reply is pure there (only choice
  tokens). Covers task options and mounted plugin globals alike, and
  both bash-split and whole-token emissions.
- the dynamic arm: the `_DYNAMIC` payload learns the same fact (the
  manifest entry it already holds knows `multiple`/`nosplit`), spelled
  as `[_MORE, _DYNAMIC, ...]` so the two compose; the fresh emission
  then exits 102. Dynamic suggestions are presentation-only (never
  validation), so continuation is always possible — the shape rule is
  exactly right for them.
- the positional/variadic mixed menu: marked only mid-list (a comma
  already typed) — there the reply is provably pure, because option
  names can never prefix-match a partial that contains a comma. The
  first element of a positional stays unmarked (its menu mixes in
  option rows, and a per-reply mechanism must not glue those). The
  asymmetry is deliberate and documented: the `=`-attached spelling is
  the grammar's own value position and gets the full feature.
- never for `nosplit`, scalars, path values (100/101 own those), or
  empty replies.

**Per-shell hook changes** (three edited, two untouched):

- zsh: capture `ret`; `(( ret == 102 ))` appends `-q -S ','` to the
  `_describe` call (an empty array expands to nothing in the normal
  case). Accept inserts the comma, space/Enter remove it.
- bash: `(( ret == 102 )) && compopt -o nospace 2>/dev/null` beside the
  existing per-reply compopt calls. Accept glues the cursor; the user
  types the comma or the space, exactly as pwsh/nushell already read.
- fish: on `test $ret -eq 102`, append `,` to the value half of each
  `value\tdescription` line before printing. Accept inserts the comma
  glued; a final-element comma is forgiven by the grammar (measured).
- pwsh, nushell: no edit — 102 falls through to the normal parse, and
  their insertion is already space-free.

**Playground**: `insertCompletion`'s `glue` becomes `""` when the reply
carried the sentinel (mirroring bash/pwsh/nushell — insert glued, the
user types the comma). Mirroring zsh instead (insert the comma,
auto-remove on space/Enter) is more JS for a nicety the real shells
don't all deliver; default no.

**Tests** (per the conventions section):

- `test_complete.py`: the marked-reply shape from `complete()` —
  attached first element, mid-list, `nosplit` unmarked, scalar
  unmarked, positional first-element unmarked vs mid-list marked,
  dynamic payload composition; `complete_cli` exits 102 with clean
  candidates on stdout.
- `test_shellcomp.py`: static assertions that each edited hook carries
  its 102 idiom (the `_CSV_HANDOFF`-table pattern), plus functional
  pty runs in the cast-machinery style for zsh (the comma lands and
  Enter removes it — the session's probes are the recipe) and fish
  (the comma-suffixed candidate inserts glued). bash's `compopt` is
  a no-op outside a live completion, so its functional proof rides
  the existing cast-style test shape rather than the scripted
  COMP_WORDS harness.
- The docs Completion example advertises the behaviour only after the
  release that carries it (the released-wheel rule).

## Open for Willem's ruling

1. **Ship bash the nospace trade?** Recommend yes: measured cost is
   "final element leaves the cursor glued", which is pwsh's and
   nushell's permanent, uncomplained-about behaviour.
2. **Ship fish the candidate-side comma?** Recommend yes, with eyes
   open on the two cosmetic costs: the pager shows the trailing comma
   on every element, and a final accept leaves `--regions=eu,us,` in
   the line (runs fine; the splitter drops the empty part). If the
   pager comma reads as noise, fish can ship later or never — zsh and
   bash alone satisfy the constraint.
3. **Dynamic values in v1?** Recommend yes (near-free: the emission
   site already holds the manifest entry); choices-only-first is the
   fallback if the payload composition reads as scope creep.
4. **Naming**: `_MORE`/`\x00more`/`_EXIT_MORE = 102` — "more items may
   follow" — vs `_CSV_ITEMS`/similar. Willem's call; nothing hangs on
   it.

## Constraints and tests

- The hot path stays stdlib-only and import-free of the framework; a
  TAB is one file read + JSON parse + tree walk. No new imports.
- `tests/test_shellcomp.py` drives real bash/zsh/fish/nushell/pwsh
  (skip-if-absent locally; CI installs all — `FOOTMAN_REQUIRE_SHELLS`
  makes a missing shell a hard failure). New behaviour needs functional
  coverage there, per shell that adopts it.
- `tests/test_complete.py` / the `tree` fixture + `ERROR_CASES`
  conventions for hot-path unit tests.
- Measured grammar facts that bound the design (memory
  `completion-grammar-facts`): values are always `=`-attached; bare
  `--opt` is always legal (no-default params are positionals); bools
  reject `=`; dynamic `suggest()` completers never bake into the
  manifest (they answer via `_suggest` respawn — decide whether dynamic
  list values also carry the marker, or first ship choices-only).
- The playground gallery may only demonstrate RELEASED behaviour
  (note `20260814-playground-gallery.md`, "the released-wheel gap") —
  the Completion example advertises the suffix only after the release
  that carries it.

## Related, decided elsewhere

- Duplicates in a `list` value are by design (a list holds what you
  put in it; `set[Literal[…]]` folds them — the playground's Completion
  example now uses `set` for exactly this reason). Completion's
  `given` filter deliberately narrows *suggestions*, never validity —
  "a completion filter that quietly became validation" is a recorded
  anti-pattern (`params.py`, matching()'s docstring). The suffix marker
  must follow the same rule: presentation, never grammar.
