# Documentation standards

How anything a reader outside this repo can see must be written.
Imported from hse's guidance; becomes the workshop base layer's
fragment, so one edit there will move every repo.

## What counts as user-facing

More than the `docs/` tree.

- **Every docstring in `src/`.** The API site will be generated from
  them, for **every** module, including the underscore-private ones. A
  docstring is published the moment it is written.
- **`docs/` markdown**, published as the site once the workshop
  renders it.
- **`README.md`, `CHANGELOG.md`** and anything else a consumer reads
  from the repo or the wheel.
- **Console output**: messages, errors, `--help` text. A reader hits
  these before they open the docs.

`#` comments are not published, but they are held to the same
discipline for a different reason. See Comments below. Only `notes/`
and tests are exempt.

## The rule

**Documentation states what is true now. It never narrates how it got
that way.**

A reader is trying to use the thing. They did not follow the work, they
cannot see the plan, and a reference to it tells them only that there is
something they are missing.

Never write, in anything user-facing:

- plan names, plan dates, or plan file names
- phase numbers, such as "the plan's Phase 2"
- pull request, issue, or commit references
- calendar dates, and "shipped", "landed", "as of", "recently"
- release versions as narrative, such as "0.0.41 renamed these". A
  compatibility statement a caller must act on is fine
- the previous design: "this used to live in X", "formerly `Y`"

Write the current contract in the present tense instead.

## Plain language

Say what the thing does. Do not name the category it belongs to.

- **If a sentence needs a second read, split it.** Usually the cause is
  a clause doing the work of a sentence.
- **Do not stack nouns.** Three nouns in a row usually hide a verb.
- **Drop words that sound technical and say nothing**: machinery,
  plumbing, surface, contract, semantics, story. Replace each with the
  action it stands for.
- **Prefer the verb to the noun built from it.** "installs the pinned
  tools", not "provisioning of the pinned tools".
- **Describe a module by what it does, and by how it differs from its
  nearest neighbour.** If two modules have similar names, the
  docstring for each says which one a reader wants.
- **Do not repeat the module path in the first line of a module
  docstring.** The generated page already prints it as the heading.
- **No idioms, no metaphors, no figures of speech.** "This does not
  cover X", not "this leaves X on the table". Many readers are not
  native English speakers; an idiom is a word they cannot look up. The
  name of an existing mechanism is an identifier, not a figure of
  speech; a mechanism name that is a metaphor gets explained where it
  is introduced. Precise technical terms (starvation, deadlock) stay.
  Coining a new metaphor is what this bans.
- **The plainest verb that carries the meaning.** "has", not "holds";
  "is green", not "sits green". If a shorter verb loses nothing, use
  it. No flourish or euphemism for machines: a container is old or
  gone, never departed.
- **State conditions positively.** "It can be conditional", not "it
  need not be unconditional". Anything shaped like "not un-X" gets
  rewritten.
- **Make timing explicit** with "already", "before", "after" when a
  claim depends on sequence or coincidence.

## Keep the why, drop the when

Cutting history does **not** mean cutting rationale. A constraint whose
reason is invisible gets removed by the next person.

One test decides it: **does a reader maintaining this next year need
it?**

- **Keep it if it constrains future work.** What breaks if this is
  violated. A dependency's actual behaviour. A platform difference. A
  cost. An invariant two files must satisfy together. A deliberate
  choice that looks wrong until explained.
- **Cut it if it only records what happened.** Which release changed it.
  Which pull request or incident prompted it. What it replaced. Which
  plan or phase it came from. When it landed.

**Never cite a bug fix.** If the bug revealed a constraint, state the
constraint and delete the bug. Not "fixed a crash when the path had
brackets", but "a bracketed path is data, not markup, so rendering it
as markup raises".

Assume a reference is stale until it proves otherwise. Deleting it is
the fix, not relocating it.

## Where history does belong

It has homes, and none of them are the code:

- `notes/`: the working record, written for us, where dates and phases
  are the point.
- `CHANGELOG.md`: a release's own narrative.
- Commit messages: the full what and why of one change, kept forever
  and reachable by `git log -S`.

## Comments

A comment's job is the **current state of the code**: what this does,
why it is shaped this way, what it must keep true. The rules above
hold here too.

Comments are also the one place an **exception** is allowed, because
they are read by the person deciding whether they may change the line
beneath them. A historical or bug-fix reference may stay when it gives
that person something to act on:

- **An upstream issue behind a workaround.** Naming it is how someone
  later checks whether the workaround can go. Delete the workaround and
  the comment together, never the comment alone.
- **A non-obvious shape that reads as a mistake.** "This looks
  redundant and is not, because ...". The reason a reviewer would
  otherwise refactor it back.
- **A trap that is expensive to rediscover.** Where the obvious
  alternative is wrong and saying so is cheaper than the rediscovery.

The bar is what the reader can *do* with it. "Fixed in #395" fails:
nothing follows from it. "Upstream returns the ref on failure
(gitea#38969), so gate on the exit code" passes: it explains the
shape, and names the fix that would let someone simplify.

## Docstrings

- Open with one imperative line saying what it does.
- Then, if it needs them: the behaviour worth knowing, the arguments
  that are not obvious, what it returns, what it raises.
- Name the invariants a caller must respect, and the failure mode if
  they do not.
- Describe the thing as it is, not as a diff from what it was.
- A module docstring says what the module is for and what a reader
  should reach for first.

## Markdown

- One sentence per idea; wrap prose at 88 columns.
- Sentence case for headings. No heading numbering.
- Fence every code block with its language.
- Link to the thing you name, once, at first mention.

## Console output

- Say what happened, then what to do about it.
- An error names the thing that failed and the next action.
- Plain ASCII, no decoration that a pipe would mangle.
- Never cite a plan, a phase, or a date to a user standing at a
  terminal.
