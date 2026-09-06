# One typing pass — the same types on every channel

**Status: LANDED, all five steps, 2026-08-07.** Willem ruled the
command-line grammar and the scope in conversation, then ruled the
hidden-parameter half (which this note had left leaned but unruled).
Started as "fixed-arity tuples and hidden parameters" — the last after-1.0
backlog row — and grew when the same question was asked of each input
channel and got three different answers. Renamed from
`20260807-typing-extensions.md`; the date is when the thread opened.

Shipped as PRs #348 (stdin honours its annotation), #349 (the matrix),
#350 (`tuple[T, ...]`), #351 (fixed-arity groups), #352 (sets, and the
last silent stdin row), #353 (the machine-readable document schema),
#354 (`hidden`), #355 (eager grouping + the docs pass). The CHANGELOG
carries what shipped; what follows is the reasoning, kept because the
wrong versions are the tempting ones.

**Two things happened that this plan did not predict**, both worth
keeping:

- **A sixth step appeared mid-flight (#353).** Willem asked whether a
  machine could read the stdin schema from `--describe`. It could not —
  it got a type *name* — and the three record kinds each described
  themselves differently, which was the manifest still holding the
  opinion about records that #348 had already made the binder give up.
  The fix was the same shape as every other fix here: one
  `_coerce.fields_of`, read positionally by `group_of`, by name by the
  binder, and rendered by the manifest. **The lesson: the sweep in this
  note looked at the two *input* channels and never asked what the
  machine-facing *description* of them said.** A channel nobody thought
  to audit is exactly where the third different answer was hiding.
- **The audit found bugs the tests could not.** `group_of` read any
  two-argument constructor as a group, so a `uuid.UUID` parameter
  advertised `--u=hex,bytes,bytes_le,…` and comma-split — bound
  correctly for one token by luck, so nothing failed. And grouped shapes
  refused at *binding* time, printing `ValueError:` like a crash, in a
  page whose first line promises parse-time validation. Both were found
  by reading output, not by a red test. The rule that fixed the first is
  the one this pass rests on: **footman groups a shape it can type.**

Related: [20260726-plugin-architecture.md](20260726-plugin-architecture.md)
(the lexical-grammar ruling — no escaping, no second pass),
[20260726-process-boundary.md](20260726-process-boundary.md) (the stdin
channel), [20260725-dotted-addressing.md](20260725-dotted-addressing.md)
(one spelling per concept).

## Why this is one pass, not three features

footman has **three channels** that speak types: values from the command
line, a document from stdin, and the declared shape of a return. They are
meant to be one vocabulary. They are not, and the gaps are invisible until
they bite — one warns, one is silent, one is a hard error, for the same
annotation.

Measured against 0.33.0, not remembered — a sweep of every combination
that looked plausible, bind-tested rather than trusted to build cleanly:

| shape | return (`--describe`) | stdin (bind) | command line |
| --- | --- | --- | --- |
| `dataclass` | describes | **binds, nested** | hard `ValueError` |
| `list[dataclass]` | describes | **binds** | hard `ValueError` |
| `NamedTuple` | describes as `row` | **silent string** | passes text through |
| `TypedDict` | describes | **silent string** | hard `ValueError` |
| `tuple[X, Y]` | describes as `row` | warns, string | warns, string |
| `tuple[T, ...]` | describes | warns, string | warns, string |
| `set` / `frozenset` | serialises (sorted) | — | **warns**, unusable |
| `Stdin[int]`, `Stdin[Enum]` | — | **silent string** | n/a |
| `Literal[1, 2, 3]` | describes | — | works |
| one-arg custom type | — | — | works (`T(value)`) |

The evidence, verbatim from a scratch project:

- `{"name":"web","at":{"x":1,"y":2},"sizes":[800,600]}` binds to
  `Plan(name='web', at=Point(x=1.0, y=2.0), sizes=[800, 600])` — the stdin
  binder already constructs **nested** custom types.
- The same `Point` from the command line: `ValueError: '1,2' is not a valid
  Point`.
- A `NamedTuple` on stdin returns `'{"width": 800, "height": 600}\n'` — a
  **string**, with **no warning**, and the run reports `ok`. Checked
  specifically for a warning; there is none.
- All three tuple forms warn *"annotation … is not a usable type; values
  are passed through as text"* on both input channels.

- **Scalars on stdin are not coerced at all.** `Annotated[int, stdin]` fed
  `42` yields the *string* `'42'`; fed by `echo`, the string `'42\n'`.
  `Annotated[Colour, stdin]` yields `'red'`, not `Colour.RED`.
- **Validation and coercion have come apart on that path.**
  `Stdin[Colour]` with a trailing newline *does* raise `must be one of red
  (got 'red\n')` — the choices check runs against raw text — but when it
  passes, the body still receives a string. Half the contract is enforced
  and the other half dropped.
- `set[str]` and `frozenset[str]` warn and are unusable as parameters,
  while json.md documents sets as serialising (sorted) on the way out.

### The diagnosis that unifies them

**The command-line path warns on a type it cannot use; the stdin path
silently degrades to a string.** That is why the tuple gaps were noticed
years-of-releases ago and the stdin gaps were not: nothing ever said
anything. The `NamedTuple` and `TypedDict` cases are the worst kind — the
checker concludes `Size`, the body receives `str`, the run reports `ok` —
the same failure class as the basic-default asymmetry fixed in 0.33.0, but
silent rather than warned.

Whatever else this pass does: **the stdin binder must refuse or warn on an
annotation it cannot honour, exactly as the manifest does.** That single
change would have surfaced every silent row in the table above.

### `echo` is the common case, and it is broken

`echo 42 | fm task` keeps the trailing newline, so any *validated* scalar
fails on it (`got 'red\n'`). Piping a value in is the entire point of the
channel. Stripping one trailing newline for coerced scalars is a small fix
with an outsized effect, and it belongs in this pass.

## The shape of the fix

**The types are the contract; each channel fills them its own way.**

- **stdin needs no grammar.** JSON already carries structure, so there is
  no stream, no commas, no `nosplit` on this path — only the binder
  learning `tuple` and `NamedTuple`, which is also the silent-bug fix.
- **The command line needs a grammar**, because it starts from a flat
  string. That is the chunking rule below.
- **The return side already describes all of it** and needs nothing.

So "chunking" is a *command-line* rule, not a type-system rule. Stating it
that way keeps the pass from leaking a CLI concern into the binder.

## The command-line rule, in one sentence

**Values accumulate from commas and repetition into one stream; a declared
fixed arity groups that stream; a remainder is a taught error.**

    tuple[X, Y]              --size=800,600          exactly one group
                             --size=800 --size=600   same stream, same group
    tuple[T, ...]            identical to Many[T]/list[T]
    Many[tuple[X, Y]]        --p=a,b --p=c,d         → [(a,b), (c,d)]
                             --p=a,b,c,d             → [(a,b), (c,d)]
                             --p=a,b,c               → error: groups of 2, got 3
    NoSplit Many[tuple[X,Y]] --p="a,b" --p=c         → [("a,b", "c")]

A bare fixed tuple is the case where the stream must make exactly one
group. Position-wise coercion gives each slot its own type, so the same
token means different things by position: `mytype = Many[tuple[str, int]]`
with `--mytype=1,1` yields `[("1", 1)]`. That is the one-line docs example;
a list cannot express it.

### The arity can come from a constructor

`inspect.signature(T)` returns the same `[(x, float), (y, float)]` for a
dataclass, a `NamedTuple`, and a plain class with `__init__(self, x: float,
y: float)`. They are one case, not three:

| annotation | arity + types from |
| --- | --- |
| `tuple[float, float]` | the subscript |
| `NamedTuple` | its fields |
| `@dataclass` | its fields |
| `Point(x: float, y: float)` | its `__init__` |

So `--at=1,2` builds `Point(1.0, 2.0)`, and the errors take names from the
constructor for free: `--at takes x,y`, `--at: y expects a number (got
'tall')`. It is the house thesis one level down — the typed signature is
the contract, including a constructor's.

**Not new scope; an existing promise made true.** typing.md says *"Any type
with a typed constructor works — footman calls it"*, then implements it as
`T(value)`, silently narrowing "any type" to "any one-parameter type". A
two-parameter type is a hard error today, so the documented claim is
already false and nothing says so.

**Non-breaking, because arity decides.** A one-parameter constructor keeps
today's exact behaviour — the whole token, uncoerced — which is the
documented `Version("1.2.3")` case. Only multi-parameter constructors
change, and those are currently a dead end.

### Why chunking is not guessing

**Guessing would be inferring the arity.** Here the arity is declared by
the annotation, so the grouping is fully determined and a remainder is a
*refusal*, never a fallback. Nothing is inferred, nothing is rounded.

## Constraints the pass must not break

Verified, so the binder work does not quietly regress them:

- **stdin is shared, not exclusive.** The stream is read once, fully, at the
  boundary, and every parameter that asks is served from the same parsed
  document. Two parameters reading different keys both work
  (`name='web' port=8080`); two parameters both claiming the whole document
  each get it; a whole-document parameter and a field parameter coexist.
  No exhaustion, no ordering dependency, no first-come-first-served.
- **CLI beats stdin.** `--port=9999` over a piped `"port":"8080"` yields
  `9999`, per **CLI > stdin > env > default > prompt**. That is what lets
  one signature serve both spellings — pipe in CI, override one field by
  hand — with no sentinel argument and no second code path.

## Hidden parameters — RULED, SHIPPED (#354)

No prior art: no code, no docs, and the `hidden` marker slot is free. The
design should transpose the task-level rule rather than invent one.
`hidden=True` on a task means: out of the listings, *still* completed
(*"Hiding and completing are different questions"*), reported in `--json`
**marked rather than missing**, and revealed by `--all`.

    def deploy(target: str, legacy: Annotated[str, hidden] = ""): ...

- out of `--help` — the listing a human reads
- still bindable; still completes — a long machine-facing flag is the one
  you most want spelled for you, which is the task rule's own argument
- `--json --list` marks it, so an agent still sees it exists
- `--all` reveals it

Uses: a deprecated flag kept working but unadvertised, a debug switch, a
flag a wrapper script passes. The alternative reading — *"not a CLI surface
at all, bound only from env/stdin/default"* — is a different feature and
wants a different name; `hidden` should mean what it already means one
level up.

## How the design got here — four corrections worth keeping

Each was found by a question, not by review. Recorded because the wrong
versions are the tempting ones and will be re-proposed otherwise.

1. **"Repetition is refused on a fixed tuple; comma only."** Wrong —
   repetition is the `nosplit` escape hatch, and refusing it leaves
   comma-bearing strings inexpressible.
2. **"`nosplit` on a tuple is a contradiction → taught error."** Backwards:
   it is precisely the mechanism for the case that objection worried about.
3. **"A tuple's parts come from exactly one source."** A special case
   invented to resolve a conflict that chunking dissolves. With one stream,
   comma and repetition both accumulate as they do for lists, at every
   depth.
4. **"`Many[tuple[str, str]]` with comma-bearing values is inexpressible —
   two levels plus commas needs three separators."** Wrong, and the most
   interesting: **the declared arity is the third separator.** With
   `nosplit`, repetition feeds one flat stream and the arity groups it —
   `--p="a,b" --p=c --p="d,e" --p=f` → `[("a,b","c"), ("d,e","f")]`. The
   hole was an artifact of correction 3.

Each wrong version added a special case; the right one deletes all of them.
That is the usual tell.

### Rejected, and why

- **An escape grammar** (`--pair=a\,b,c`). Breaks the lexical rule that
  dash-tokens are self-contained and values are *read*, not parsed — the
  principle that let the two-pass parser die unbuilt.
- **Refusing `Many[tuple[...]]`** at registration. Too blunt; the shape is
  fine whenever values carry no commas, which is most of the time.
- **Silently chunking a remainder.** That *would* be guessing.
- **Fixing the silent bindings as their own PRs.** Ruled against: one
  pass, one consistent standard, so the channels move together. That
  covers the silent `NamedTuple`/`TypedDict` bindings, uncoerced stdin
  scalars, the trailing newline, and sets.

## Arity ranges, nesting, and `--describe`

Three things the grouping rule needs stated precisely, because each is a
place where "support it all" meets a real edge.

### A variable-arity shape is allowed where there is one group to fill

`Point(x: float, y: float, z: float = 0)` accepts 2 **or** 3 values. That
is only ambiguous when something has to decide where one group ends and
the next begins — which happens **only inside a container**:

    Point(x, y, z=0)   --at=1,2       → Point(1,2)     one group, ≥ required
                       --at=1,2,3     → Point(1,2,3)   one group, ≤ total
                       --at=1         → error: below required
                       --at=1,2,3,4   → error: above total
    Many[Point]        --at=1,2,3,4   → two groups of 2? one of 3 + remainder?

Bare, the stream must make exactly one group, so the *count is the answer*
and every case is decided. In a container it genuinely is not.

**Ruled:** a shape whose arity is a range may be used bare, where its
group size is settled by the count; inside a container the group size must
be **fixed** (every constructor parameter required). The manifest can see
the constructor's signature at load, so `Many[Point]` with an optional
parameter is a **registration-time** taught error naming both ways out —
make the parameter required, or take the value on stdin.

### Non-scalar positions: refused for readability, not for ambiguity

`Line(a: Point, b: Point)` is worth explaining carefully, because the
obvious objection is wrong. With fixed arities all the way down the split
*is* computable: `Line` is 2 positions, each `Point` is 2 floats, so
`--line=1,2,3,4` is deterministically `Line(Point(1,2), Point(3,4))`. No
guessing required.

It is refused for three better reasons:

1. **It stops being readable.** `--line=1,2,3,4` gives a person nothing to
   see the structure by. The design's bar is a command line someone can
   read back; a flat run of four numbers standing for two points is not
   one, however well-defined.
2. **Errors lose the thing that made them good.** The whole argument for
   `NamedTuple` was that `--size: height expects an integer` beats
   `2nd value`. Nested, the honest message is `--line: 3rd value` — third
   of what? Naming it `a.y` needs a path vocabulary the flat grammar does
   not have.
3. **It compounds with the rule above.** One optional parameter anywhere in
   the tree turns the computable split back into a guess, so the exception
   would have to be "nesting is fine unless any level has a default",
   which nobody could hold in their head.

And the alternative already works: **nested structures are what the stdin
channel is for.** `{"a":{"x":1,"y":2},"b":{"x":3,"y":4}}` binds today —
verified — and it is readable. So the refusal is a signpost, not a dead
end: a registration-time taught error naming stdin.

`*args` constructors are refused on the simpler ground that they have no
arity at all.

### `--describe`: reuse `row`, and what it costs

A parameter spec today is a **flat** vocabulary — `name`, `kind`
(`flag`/`option`/`positional`/`variadic`/`stdin`), `types` as a list of
scalar tags, plus `choices`, `multiple`, `mapping`. It has no way to say
"three positions, of these types, with these names".

The return side already does, as the `row` kind, and already renders the
right JSON Schema for it:

    {"type": "array", "prefixItems": [...],
     "minItems": n, "maxItems": n}

**The choice:** embed that same shape document in the parameter spec, or
invent a flatter input-side spelling (say `types: [["float"], ["float"]]`).

**Lean: reuse `row`.** The alternative is a second vocabulary for a concept
that already has one, and one-spelling-per-concept has been the right call
every time it has come up here. Reuse also means an agent reading
`--describe` learns one shape language for inputs and outputs, and gets
`prefixItems` for a *parameter* exactly as it does for a return.

**The cost, stated rather than waved at:** the parameter specs live in the
**manifest**, which is the file the completion hot path reads on every
<kbd>Tab</kbd>. Embedding a recursive shape document per parameter grows
that file. Two things keep it proportionate — the shape appears only for
parameters that have one, which is rare, and the hot path never reads it
(TAB needs "does this take a value" and any `choices`, nothing deeper).
But it is a real addition to the one file with a latency budget, and the
build should measure the manifest before and after rather than assume.

## The docs half — typing.md needs a thorough pass

The page is 2,081 words across ten sections and was written before any of
this. It is not a matter of adding a tuple row; several of its claims stop
being true, and its biggest gap is one it never knew it had.

**Wrong or incomplete after the pass:**

- **The core mapping table** (`## The core mapping`) has no row for a
  fixed-arity value at all. It gains one, and by the `NamedTuple` ruling
  the named form leads — the way `Literal` already precedes a bare `str`
  in that table.
- **`## Custom types`** says *"Any type with a typed constructor works —
  footman calls it"* and then shows `T(value)`. The sentence is false today
  past one parameter and stays subtly wrong after: it needs the arity story
  (one parameter takes the whole token; more are filled from the group) and
  the refusals with their signposts.
- **`## Comma-splitting and `nosplit`** describes splitting for collections
  only. It becomes the general rule — one stream, commas and repetition
  both feeding it — with the arity doing the grouping.
- **`## Unions and one-or-many values`** is where `tuple[T, ...]` belongs
  beside `Many[T]`, with the honest note that they differ only in the
  container handed to the body.

**New sections:**

- **Fixed-arity values.** `NamedTuple` first, then the short caveat Willem
  asked for: a plain tuple behaves *exactly* the same and only its errors
  are poorer (`2nd value` where the named form says `height`).
- **Arity ranges**, and why a shape with optional constructor parameters
  may be used bare but not inside a container — one group means the count
  decides; two groups means a guess.
- **What is refused, and where to go instead.** Non-scalar positions and
  `*args` constructors, each pointing at the stdin channel, which already
  binds nested structures today.

**The gap the page never knew it had: which shapes work on which channel.**
Nothing on the page tells a reader that `list[dataclass]` binds from stdin
but is a hard error on the command line, or that a `NamedTuple` on stdin
silently hands back a string. That asymmetry is undiscoverable today —
there is no page where a reader could have found it. After the pass the
channels agree, so the page's job is smaller: state once that the same
annotation means the same thing wherever the value comes from, and let
[Pipelines](../docs/pipelines.md) keep the details of the boundary.

**Elsewhere:**

- **pipelines.md** gains the coerced-scalar story (`Stdin[int]` really is
  an `int`) and loses nothing.
- **json.md**'s describable-set paragraph needs whatever `--describe`
  settles on for parameter shapes.
- **reference.md**'s marker table gains `hidden`.
- The **"What if I don't like annotating types?"** section added in 0.33.0
  should be re-read once tuples land: it currently says containers stay
  strings, which stays true, but its framing of "the four basic types" sits
  next to a new fixed-arity story and should not read as contradicting it.

## Sequencing

Ruled after reading the code rather than the design, which changed the
shape: the three stdin fixes all live in the same three functions
(`_binder.is_document_target`, `_executor._document_shape`, and the text
fall-through in `_stdin_value`), so they are one change, not three.

**1. The stdin channel keeps its contract.** `is_document_target` learns
`NamedTuple` and `TypedDict`; the text fall-through coerces scalars and
strips one trailing newline; anything still unhonourable **warns like the
manifest does** instead of silently becoming a string. Small, confined,
and it converts every silent row in the table into a loud one — so it
de-risks everything after it and proves the type machinery end to end on
the simpler channel first.

**2. The command line learns fixed arity.** The grouping rule, with arity
read from a subscript, fields, or a constructor; `tuple[T, ...]` via the
existing list path plus a cast; the manifest spec and whatever
`--describe` settles on. The bulk of the work, and the headline.

**3. Sets, on both channels.** Small, and deliberately after 1 and 2 so it
is an addition to established machinery rather than a fourth special case.

**4. Hidden parameters.** Independent of all of the above; sequenced late
only because it competes with step 2 for the same manifest spec code.

**5. The docs pass.** After the behaviour is settled, per the checklist
above.

Each step leaves the tree green and is independently valuable. If the pass
stops early, the value stops in a coherent place: after 1 nothing lies
silently, after 2 the headline feature exists.

## Opens

Everything else is ruled. What remains:

- **Hidden parameters in the generated task docs?** Leaning yes, badged,
  and the live behaviour of hidden *groups* settles most of it: this
  repo's own `hooks` group is `hidden=True`, and `fm --tree` omits it,
  `--tree --all` restores it with its tasks, `fm --help hooks` works
  regardless, and `--json --list` carries it with `hidden: true` —
  **marked, not missing**. A parameter should copy that. What is still
  untested is only the rendering: how a badged parameter row looks in a
  generated task page.
- **Sequencing.** The pass has grown well past its backlog row: silent
  bindings on three shapes, scalar coercion on stdin, the newline, sets,
  the constructor generalisation, the grammar, and hidden parameters. One
  standard, but plausibly two or three PRs. Willem: a sequencing step once
  the plan is done.

## Ruled

- **`NamedTuple` is the preferred form** in every docs example, with a
  short section noting plain tuples behave identically but report
  `2nd value` where the named form says `height`.
- **`hidden` on a parameter means what `hidden` means on a task** — out of
  the listings, still bindable, still completed, marked rather than
  missing in `--json`, revealed by `--all`. Chosen for consistency; the
  narrower "not a CLI surface" reading is a different feature and would
  need a different name.
- **`tuple[T, ...]` is supported**, not taught away in favour of
  `Many[T]` — refusing a shape the return side accepts is the asymmetry
  this note opened with.
- **Variable-arity shapes are bare-only** (above).
- **Non-scalar positions and `*args` constructors are refused**, with a
  taught error pointing at stdin (above).
- **The silent bindings, uncoerced stdin scalars, the trailing newline and
  sets belong to this pass**, not to separate fixes.
