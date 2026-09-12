---
title: The design, in plain words
---

# Design

!!! note "This page is a contract, and an invitation"

    footman is pre-1.0, and this page is deliberately written *ahead* of
    some of the code. It describes the design the next releases are built
    against; once they ship, an independent reviewer with no memory of the
    discussions checks the implementation against this page, not the
    other way around. So if any argument here fails to convince you, that
    is a finding, not a nuisance: [open an
    issue](https://github.com/willemkokke/footman/issues) or reply
    wherever you were handed this link. It was written to be argued with.

Every tool earns trust in its own way. A test suite earns it by failing
when it should. A task runner earns it the moment you stop
double-checking it, when `fm check` prints green and you merge on that
sentence alone.

That moment is what footman is designed around. Running your tasks,
ordered and parallel and stoppable on every platform, is the easy half, and
plenty of runners do it. The neglected half is what comes after: the
runner tells you what happened, and everything depends on whether that
account is *true*. This page walks through the design one decision at a
time, in plain words, with the reasons attached. By the end you should
be able to predict what footman will do in situations this page never
mentions. That predictability is the design working.

Four commitments run through everything below:

1. **Everything footman runs has a name and leaves a record.**
2. **Records are never fiction.** The machinery will not attest to work
   it did not perform.
3. **Verdicts are decided in the open.** When an exit code needs
   interpreting, the interpretation is attributed and on the record,
   never smuggled in.
4. **Explicit beats implicit at every boundary.** Where that costs you a
   few characters, you get something back for them.

## The front door

Everything below happens *after* footman decides to run something. The
decisions before that moment are older, and they carry more weight than
their size suggests: most of what shipped later cost little precisely
because these were never compromised.

**Zero runtime dependencies.** Installing footman installs footman and
nothing else, ever. A task runner sits underneath every project it
serves, so anything it dragged in would be dragged into all of them; the
rule has no "just a small one" clause, because small is how it starts.
The price is paid inside, where the binder, the coercion and the completion
machinery are all standard library, and the dividend is that
`uv add --dev footman` cannot change what your project depends on.

**The process answering a keystroke never imports your code.** Completion
answers from a baked file (one read, one JSON parse, a tree walk)
and `main()` checks for `--complete` before importing even footman itself.
Your tasks file, its imports, its heavy dependencies: none of it runs *in*
the process that serves the <kbd>Tab</kbd>. Baking the file does import
them, in a detached subprocess — on the first <kbd>Tab</kbd> in a fresh
directory, and in the background once the cache goes stale — which is the
same import a run does, moved off the keystroke rather than skipped. The
budget survives the only way budgets do: every feature that touches
completion is checked against "what does the Tab pay", and a feature that
would make the Tab pay gets built another way.

**The manifest is written from declarations, never executions.** The
baked file the <kbd>Tab</kbd> reads, the manifest, describes the task
tree as your files declare it, and the chain parser reads the same file,
which is what makes the split deterministic. Nothing that happens during
a run can grow the CLI: a task made mid-run earns a place in the report,
never an address on the command line. Completion, `--dry-run`, and
`--help` stay in agreement with each other and with the files in your
repo because they are all views of one declaration.

**One spelling per concept.** A task's address is one dotted token,
`fm docs.serve`, on every surface: running, help, completion, the
report. The space form is taught against, never quietly accepted,
because a second spelling is a second grammar to keep consistent
forever. The same ruling repeats through the design: option values are
`=`-attached, always; each lifecycle moment has one name; sharing within
a run and caching across runs are two words because they are two things.
And every time the rule held, something got simpler downstream: dotted-only
addressing is why a group's default action can take positionals with no
ambiguity rules at all, and `=`-only values are why the parser needs no
arity tables, and a whole two-pass grammar died unbuilt.

**Refusals over guessing, written as teaching.** When a line is
ambiguous or wrong, footman refuses it, exit 64 and before anything runs,
and the refusal names the culprit, states the expectation, and proposes
the fix: `--mode strict` answers "did you mean `--mode=strict`?". An
error message is product surface, the one part of the design every user
eventually reads, and it is written so the reader ends up
knowing more than before the mistake.

## Tasks and steps: one thing, two sizes

You write a task by decorating a function. Its signature becomes a real
command-line interface (typed flags, positionals, completion) and its
body is ordinary Python:

```python
from livery.footman import task, run


@task
def test(coverage: bool = False):
    """Run the test suite."""
    run("pytest --cov" if coverage else "pytest")
    # The typed tool handles live in toolroom (import toolroom as
    # tools), where flags become checked keyword arguments with
    # completion:
    #
    #     tools.pytest(cov=coverage)
    #
    # This page sticks to plain run() commands everyone already knows.
```

That `run()` call makes a **step**: one recorded piece of the task's
work. When the run finishes, the report shows the task, and under it the
step: the command, its outcome, its duration.

Tasks and steps look like two different kinds of thing, and in most
runners they are. In footman they are the same thing wearing different
defaults:

|                | task                        | step                          |
| -------------- | --------------------------- | ----------------------------- |
| name           | yes — callable from the CLI | anonymous                     |
| typed flags    | yes, from the signature     | no                            |
| when asked twice | runs once, answer shared  | every mention is its own work |
| policy         | full control                | inherits from its task        |
| record         | title, verdict, output, duration | the same, exactly        |

Why collapse two familiar ideas into one? Because every wall between two
nearly-identical things makes you build every feature twice, or worse,
build it for one side and leave the other side poorer. We kept catching
the two halves borrowing each other's clothes: steps wanting titles and
review, tasks wanting to be made on the fly, both showing up identically
in the live progress display anyway. Once they are one thing underneath,
a feature built once works at both sizes, and the differences that
remain are exactly the ones that *should* remain: the ones that follow
from having a name. A named task can be tab-completed, shared, and
gated, because a declaration is a commitment. An anonymous step can't be
addressed from the outside, because there is nothing to address it by.
Nothing else divides them.

You can make a step three ways, and all three are the same word:

```python
import shutil

from livery.footman import step


def write_fixtures(): ...  # stand-ins for your own helpers
def build_docs(): ...


@step  # 1. a function that IS a step
def clean():
    shutil.rmtree("build", ignore_errors=True)


with step("prepare fixtures"):  # 2. record a block of your own code
    write_fixtures()

docs = step(build_docs, title="docs")  # 3. wrap an existing function
```

One note, learned from Python itself: calling a step function
**builds** the step, it doesn't run it. `clean()` hands you a piece of
work ready to be scheduled, the same way `range(10)` hands you a range
without counting anything yet. The type checker knows the difference,
your IDE shows it, and the docs say it out loud here so it never has to
be a surprise.

A step can also be a generator, which buys two things with one keyword:

```python
from pathlib import Path


def to_webp(image: Path): ...  # your converter


@step
def convert(images: list[Path]):
    view = yield  # the step's own record, mid-work
    for done, image in enumerate(images, start=1):
        view.title = f"converting {done}/{len(images)}"
        to_webp(image)
        yield  # a checkpoint, once per image
```

Every bare `yield` is a **checkpoint**: a place where footman may
safely cancel the step. Cancellation only ever happens at a
checkpoint, and only for three reasons: the run is failing fast
(another task failed, so this result is no longer wanted), you pressed
Ctrl-C, or the step ran past its own `timeout=`. All three arrive the
same way, in that the loop simply never resumes: any `try`/`finally` around
the yield runs, claims are released, nothing is left half-converted,
and the worst case costs one trip around the loop. Between checkpoints
the step cannot be interrupted, and that is not timidity but Python's
own rule, since a running generator refuses to be closed from outside, by
design. Footman's contract is the same rule: yield where it
is safe to stop, and footman will only ever stop you there.

## Nothing runs anonymously

Hand footman a bare function and it will refuse:

<!-- example: fragment -->
```python
parallel(lint, test, lambda: shutil.rmtree("build"))  # refused
parallel(lint, test, step(clean, title="clear build/"))  # one wrapper fixes it
```

This looks strict, and it is, deliberately. Footman can only schedule,
record, deduplicate, and safely cancel work it *owns*, and a bare
callable is a stranger: no name for the report, no place in the plan, no
way to stop it cleanly. Older versions tried to be accommodating and ran
strangers anyway, and an entire family of guard rails existed just to
manage code footman couldn't vouch for. Refusing at the door deletes the
whole problem, and the wrapper you pay one line for buys the step a
receipt in the report, which is usually what you wanted anyway.

## The record

`run()` does two jobs, and it helps to see them separately. It
**executes**: spawns the program, captures its output, guards the
working directory, times it. And it **records**: keeps a title, a
verdict, the output worth showing, the duration. The rendered form of
that record, the line you see in the report, is called a **receipt**.

The record's verdict *is* the program's exit code. Every program ends by
handing back a number, zero for "it worked" and anything else for "it
didn't", and that number is the oldest, most portable piece of truth in
computing. Footman never wraps it in something fancier: the `Result` you
get back from `run()` literally is that integer (with the captured
output, timing, and command riding along), so `if run(...)`,
`run(...) == 0`, and every shell-shaped habit you already have keep
working. One default worth knowing before you lean on the habit: a
non-zero exit *raises* unless the call says `nofail=True`. The shell
idiom types, but a failure never slips past an unchecked call the way
it does in a shell script.

Sometimes execution is the *only* half you want. A task that reads the
current git hash isn't telling the story of the run; it's learning
something in order to tell it:

<!-- example: fragment -->
```python
head = run("git rev-parse HEAD", recorded=False)  # off the record
```

`recorded=False` is footman under full management, captured and guarded and
timed, with no receipt. It exists for exactly this: how a task learns
something. It is *not* for hiding real work, and it does not exist at
the task level at all: a task is declared, and declared work is part of
the run's public accounting. If a whole task shouldn't be in the story,
the fix is to not run it, with an `if` statement, never to run it
invisibly.

And the reverse, a record with no work behind it, cannot be written at
all. There is no call that mints a receipt for something that didn't
happen; the one primitive that looked like it could was rejected for
exactly that reason. You can of course write an empty task and give it a
grand name — footman polices its own honesty, not yours — but the
machinery itself never writes fiction.

One consequence worth spelling out: `--dry-run` fakes precisely the
things footman owns and records (the recorded subprocess calls, the
deferred steps) and nothing else. Your own inline code still runs,
because it was never footman's to fake; an off-the-record call runs
too, because it is how a task learns something, and faking the learning
would corrupt the recorded story downstream of it.

## The verdict is decided in the open

Here is a real story. One of footman's first users runs `djlint
--reformat` in a formatting gate. That tool exits `1` when it changed
files, which is reasonable for a linter and wrong for this gate, where "I fixed
your files" is success. The old workaround was ugly: run the tool off
the record, inspect its output by hand, then print a receipt manually,
which meant the report showed a record with a fake duration and no
real connection to the work. Two lies to express one true sentence:
*"this tool's idea of failure is not mine."*

The design answer is a **review window**. Between a step finishing and
its record being sealed, a reviewer you name may read the draft and
amend the verdict:

<!-- example: fragment -->
```python
def reformatted_is_fine(view):
    if "reformatted" in view.stdout or view.code == 0:
        view.title = "djlint: reformatted"
        view.code = 0  # the verdict follows the code


djlint.opts(pre_record=reformatted_is_fine).reformat("templates/")
```

One line and a named function, and both old lies die: the receipt shows
the real duration and the real command, and the reinterpretation is
attributed, in your code, in review. The rules of the window are few and
firm: a reviewer sees exactly what was captured (an uncaptured run
reviews the code alone); it may set the title and the code, and `ok`
follows the code so the two can never disagree; a reviewer that raises
*fails* the step with its error, because a broken reviewer is a broken gate,
not a shrug. A reviewer may also rewrite the *reported* return value, the
summary a report shows, useful when the real value is a secret or a wall of
text, but never the value handed to the code that asked for the work. A redaction is a display decision, and display decisions do
not change what a program computed with. Reviewers stack from the inside out; the one written
closest to the function runs first, each outer one sees what the
previous left, and on a tool or a step maker a per-use
`.opts(pre_record=...)` has the final word. A *task's* reviewers attach
on the handle and stay attached, because a task is declared, and so is its
review.

Once the review window closes, the record is **sealed**. Whoever looks
at it afterwards receives the sealed, immutable form. This is enforced
by the type system, not by convention: the observer's object simply has
no way to write. An observer that finds a problem is not powerless,
though: it can *fail* the work, loudly and attributably:

```python
from livery.footman import fail


@test.post_task
def budget(result):
    if result.duration > 60.0:
        fail(f"too slow: {result.duration:.0f}s against the 60s budget")
```

Notice where that lives: on `test`, right next to the knowledge it
encodes. Every task exposes its own lifecycle this way, with
`@test.pre_task` for setup that belongs to it, `@test.pre_record` for
its reviewer and `@test.post_task` for watching it, so a rule about one
task never has to live in some central file that lists everybody.
Named steps carry the same idea at their size: `@clean.pre_record`
for their reviewer, `@clean.post_step` for watching them. The only
moments a step doesn't offer are the ones it genuinely doesn't have:
no arguments are bound and no request is resolved inside a step, and
setup for a body you wrote yourself is simply its first line. Hooks
with *no* task knowledge in them (a tracing exporter, a timing
collector) register globally from a plugin instead, and the line
between the two channels is one sentence: the moment a global hook would
say "if this is task X", it belongs on X.

The distinction matters enough to name: observers may **veto**, never
**forge**. A veto rides the ordinary error channel, so the row fails and the
failure names the observer and the moment, while rewriting the record
to say the *work* concluded something it didn't is simply unspellable.
And `fail()` means fail, everywhere: `fail(code=0)`, "fail successfully",
is a contradiction footman refuses rather than a corner it interprets.

Every record carries the whole story of its verdict, called the
**audit**: each moment that touched the outcome, who acted, and what
they set. The djlint step above, if a budget observer later vetoed it,
reads like this:

```json
{
  "command": "djlint: reformatted",
  "code": 1,
  "failed_at": "observe",
  "audit": [
    ["body",    "djlint --reformat …",  1],
    ["review",  "reformatted_is_fine",  0],
    ["observe", "budget",               1]
  ]
}
```

Read it top to bottom: the tool exited 1, a named reviewer
turned that into a green 0, a named observer failed it anyway for being
slow. Three actors, three moments, nothing hidden. And when anything
fails (a tool, a reviewer, an observer, even the argument-binding
before the body ran) the record says *where* in the lifecycle it
happened, because "it failed" is half an answer.

``` mermaid
graph LR
  subgraph draft["the draft: open to review"]
    body["body runs<br/>(code captured)"] --> review["reviewers,<br/>inside-out"]
  end
  review --> seal["record sealed"]
  subgraph sealed["sealed: observers read, never write"]
    observe["observers<br/>(may veto via fail())"]
  end
  seal --> observe --> report["the report"]
```

## The same work runs once

If two tasks both depend on `build`, `build` runs once and both get the
answer, which is table stakes for anything with a dependency graph. The
question with teeth is: what counts as "the same work"?

Footman's answer: the same declared task, asked with the same resolved
arguments. `build("web")` twice is one build, shared; `build("web")` and
`build("api")` are two, correctly and silently. And the rule holds on
*every* path, whether the request came from the command line, a
dependency list, or a plain call inside another task's body,
`artifact = build("web")` meets the same matching rule and shares the
same single execution. There is one identity rule, not one per entrance.
(Sharing is the default, not a cage: `@task(shared=False)` opts a task
out, declared where the task lives like every other policy.)

Separately from *sharing*, every piece of work in a run has an
**address**, a path built from who asked for what, like
`check/typecheck/mypy`, with a counter when the same name appears twice
at the same spot. Addresses are deterministic: the same tasks.py
produces the same addresses run after run, machine after machine. That
is what will make per-step timing history possible, and, further out,
[skipping work whose inputs haven't changed](#where-this-goes): both
need a stable way to say "this exact step, last time", and neither is
built yet.

## One report, one shape

Everything above ends up in one place. On a terminal you see receipts;
machines read the same truth as JSON: one flat list of records, in the
order the work was created, each carrying its address:

```json
{ "schema": 1, "items": [
  { "address": "check",           "code": 0, "duration_ms": 22799 },
  { "address": "check/lint",      "code": 0, "duration_ms": 55 },
  { "address": "check/typecheck", "code": 0, "duration_ms": 5541 },
  { "address": "check/typecheck/mypy", "code": 0, "duration_ms": 577 }
] }
```

Flat is a choice, and it was measured before it was chosen. We took a
real run of footman's own `check` task and rendered it both ways, as a
nested tree and as this flat list. Every question a consumer actually asks
(*did lint pass? what failed? how long did the typecheck family take?*)
is a one-line filter against the flat list, because an address prefix
*is* a subtree. Against the nested form, the same questions need
recursive descent. And the tree costs nothing to give up: since every
record carries its address, the visual tree is derivable in a dozen
lines, which is exactly what the terminal renderer does. Humans get the
tree; machines get the list; both are views of the same records.

What the terminal shows by default follows one rule: **green is
collapsible, failure is never hidden.** At normal verbosity you see
tasks; `-v` adds every step; but a failed task always expands to its
failing step and its audit line, whatever the verbosity. You should
never have to re-run something louder just to find out what went wrong.

The channel discipline underneath all of this: **stdout is the answer,
stderr is the commentary.** A task's own stdout is routed untouched; the
summary, the status line, every footman remark rides stderr; and under
`--json` the envelope owns stdout outright. Piping footman never captures
its chatter, whichever form the answer takes.

## Sharing one process

Parallel tasks live in one operating-system process, and a process has
exactly one working directory, one terminal, one environment. Threads
share all three, which is why "just chdir in your task" is a classic way
to corrupt a parallel run: every other task chdirs with you. (The
[Foundations](foundations.md) pages tell this story properly; it is
worth the detour.)

Footman's design splits the problem by resource, and the mechanism is
called a **lane**: a claim on one named resource, serialising only the
work that claims it. The environment needs no lane at all, because footman
virtualises it, so tasks read and write their own view without touching
the real one. The terminal is a lane you already use without knowing:
interactive tasks take turns on it. The working directory is a lane you
claim explicitly, `lanes=(cwd_lane,)`, and the spelling is deliberately
visible, because holding the real working directory *is* giving up some
parallelism and the code should say so where reviewers can see it.

Everything else is yours to declare. A lane is created by binding a
name, and claimed by handing that binding around:

```python
from livery import footman

db = footman.lane("database", reason="serialises the shared dev DB")


@task(lanes=(db,))
def migrate(): ...
```

Two details carry the design's weight here. Lanes are **handles, not
strings**: you share one by importing it, and a typo is an undefined
name Python itself refuses, not a silently-new lane that never contends
with anything. And a claim contends **only with claimants of the same
lane**: two `migrate`-style tasks queue behind the database while the
rest of the run proceeds untouched. Claims are made when a piece of work
starts, never midway through, because the pattern where a running task suddenly
escalates to grab a resource is the classic recipe for deadlock, and
footman keeps that door closed by having no way to spell it.

Footman itself will only ever build the two lanes it ships, the working
directory and the terminal, using exactly the mechanism above.
Anything further belongs to plugins and to you. One binding per real
resource also means "two databases" is just two bindings; there is no
special machinery for kinds or instances, and there never needs to be.

## What this design refuses

A design is also the things it says no to, and the reasons deserve to be
on the record:

- **No event loop.** A checkpoint in a generator step is a cancellation
  point, never a scheduling point. Your step is free to *create* work:
  calling another task from its body queues it into the same pool as
  everything else, exactly as it would anywhere. What footman refuses
  is the other direction: it never uses your suspension as an
  opportunity to run something else on your step's thread, and your
  resumption never waits on unrelated work. The moment yields became
  scheduling points, footman would be a badly reinvented asyncio with
  the function-coloring problem dragged in behind it. Concurrency stays
  what it visibly is: threads for tasks, processes for programs. (A
  step that genuinely needs mass concurrent I/O can run a real event
  loop *inside* itself, which is composition, not contradiction.)
- **No hiding switches.** The one "don't record this" spelling is for
  how a task learns things, and stops existing at the task level. Report
  noise is solved by display, collapsing what's green, never by not
  recording; a record that was never made can't answer questions later.
- **No fiction.** No API mints a receipt without work behind it; sealed
  records can't be rewritten; reviewers act before sealing, attributably;
  observers after, read-only. Every write to a verdict has a name in
  the audit.
- **No guessing.** A tool's exit code is never "interpreted" on your
  behalf. You write the reviewer, footman records that you did.
  Unknown lane? Undefined name. Same task, different arguments?
  Different work, no heuristics.
- **No bare callables at the boundaries.** One wrapper word, in
  exchange for everything above applying uniformly to every piece of
  work footman touches.
- **No configurable rightness.** Where one behaviour is correct, footman
  ships it instead of a knob: the refusal code is 64 and not a setting.
  A knob whose right value is knowable hands the decision to every user
  separately, forever.
- **No knowledge of its caller.** Footman never learns who is driving
  it, not an editor, not an agent, not its own companion package. The
  rule is enforced structurally (a test greps the source for caller
  names), which is why integrations stay integrations instead of
  becoming features.

Each refusal is a feature that stays buildable *because* it was refused:
the audit is only trustworthy because nothing writes records
anonymously; cancellation is only safe because nothing runs that footman
doesn't own; the report is only complete because nothing opts out of it.

## Where this goes

Three threads follow from this design, and each one earns its place by
needing nothing the design doesn't already have. They are sequenced
after the work above ships, alongside the standing
[roadmap](roadmap.md):

**A streaming report.** The report is a flat list of sealed records, which
is to say it is already almost a stream. One record per line,
appended the moment each seals, gives CI dashboards and agents a live
feed with the same honesty guarantees as the file: only sealed records
ever stream, so nothing a consumer sees can later change. The
parent-in-the-address choice quietly pays off here: a child's line is
complete before its parent finishes, no joins required.

**Skipping unchanged work.** A typed signature already declares most of
a task's inputs: file parameters, environment variables, the tools it
runs (footman knows their versions). Declare the *outputs* too, and
"inputs unchanged since last time" becomes checkable, per task and opt-in:
build-system-grade caching as a gradient you climb one task at a time,
not a cliff you migrate to. The deterministic addresses above are the
other half: a cache needs a stable name for "this exact work".

**Placing work elsewhere.** A subprocess step is already a serialisable
description: command, environment, directory. A declared task with
typed inputs and outputs is close to one. Neither needs to run on *this*
machine forever. This is the far horizon and nothing about it is
designed yet, on purpose, but every choice above (identity by
declaration-plus-arguments, records as data, lanes as named resources)
was checked against it, so getting there someday requires extending the
model, not breaking it.

How we get there is the same way this page got written: the design is
specified first, in writing; real payloads are walked through it on
paper; a typed skeleton forces the pieces to actually fit; fixed
adversaries attack it; and decisions get made on measurements, in
dependency order. When implementation and this page disagree, one of
them is wrong in public, and the fix, either way, is a visible commit.
That is the deal this page offers: the design in plain words, and the
means to hold it to account.
