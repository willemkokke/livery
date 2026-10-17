# livery-extensions-coverage

Each package's coverage floor, judged on the union of what every CI leg
measured, the record that carries each suite's parts across runs, and
the site's coverage pages. A workspace turns it on by listing the
extension:

```toml
[workspace]
extensions = ["coverage"]
```

The test extensions measure for it, each through its `[for.coverage]`
table: `pytest` the python suites, `cmake` the native builds. A
workspace that lists neither has coverage dormant.

## The floors

A package states its coverage policy in the `[coverage]` table of its
`workshop.toml`, a table the coverage extension declares: while
`[workspace] extensions` does not list `coverage`, the table refuses,
naming the extension. `floor` is a percentage of statements and branches
the gate enforces, or the mode `"auto-ratchet"`, under which the floor
is the package's mark on the state store; a package with tests that
names no floor is judged at 100. Both modes pass at the floor minus the
package's `epsilon` (percentage points, 0.5 when absent).
`required = false` leaves a package unjudged on purpose: it gets no
verdict and no mark, its coverage line says so, and a `floor` beside it
refuses. Under auto-ratchet the first gated run records the mark, a run
that clears it by more than epsilon raises it, and a store that cannot
be read falls open with its reason and writes nothing. Lowering a mark
is a person's act: `fm coverage.accept <package> <value> --reason=<why>`
writes a row naming who and why, and refuses without a reason, at or
above the current mark, or for a package not under auto-ratchet. The
number that is judged is the CI union: on every leg the tests run
measured, each process a test starts included and the gate's own driver
never, so a line counts only when a test reached it, and the gate job
combines all platforms before enforcing, so the floors are deterministic
per change and never depend on one machine's view.

## Its CI

The extension brings its own CI: its entry `coverage.leg` joins each
check leg before the leg's own write, and its judge `coverage.union`
joins the gate job before the run's rows are collected, so a package
below its floor stops the job before the verdict and the stamp. A
workspace that does not list `coverage` runs neither.

Measuring belongs to the test extensions, each through its
`[for.coverage]` table: pytest measures the python suites, writing the
root `.coveragerc` and adding coverage.py and pytest-cov to the dev
group, and cmake instruments and measures the native gate builds. A
workspace that lists one of them without coverage runs its tests with no
meter and prints no preview. A workspace that lists coverage and nothing
that measures has coverage dormant: no entry, no judge, and
`fm extensions` and `fm doctor` name what would wake it.

## What is measured

Measurement is the measurer's, reduced to one shape before anything is
stored: parts in the coverage extension's line format, one per suite and
measurer, each file's statements and the lines that ran, with its
branches, functions and classes where the measurer names them. A python
suite's part is the pytest extension's reduction of coverage.py's data,
made on the check leg once for each suite. A native suite's part is the
cmake extension's, a distribution of its own, `livery-extensions-cmake`:
it instruments the gate build for the compiler family CMake detects,
reads the run with the measurer beside that compiler, and leaves one
part per package under `.workshop/coverage/parts/`, named after its
measurer. Its page says what each measurer gives. The union across legs
merges each file's parts by the line format's rules: the readings at the
content the checkout holds and by one measurer merge, and a reading of
other content or by another measurer is named and left out, so two
compilers' readings never merge. A package's figure counts its `source`
category's files, each by what its measurer judges: a native measurer's
lines alone, coverage.py's statements and branches. A package with a
floor that no leg measured refuses by name and never passes.

## The record

Coverage stays global under the affected mode: in a check leg's one
measured run every test records under a context named by its node id
(the pytest extension's plugin, quiet outside a measured run), the leg
reduces the run's data to a part per suite and leaves the parts for the
leg's one write, which puts them on its per-run ref of the state store,
with the identity of the suite's dependency closure (the tree ids of the
package and of every package it depends on, plus the root's
`pyproject.toml` and `uv.lock`). Main has a coverage record, and so does
every branch with a pull request run: per check leg, one row per suite,
its parts as the last run judged them and the closure each was measured
at. A row keeps what ran, a python suite's with the tests that reached
each line: the suite's own tests, and the code outside every test. The
row numbers those tests, and each write numbers them as the row it
replaces did, so a test keeps its number from run to run, a new test
takes the next, and a test that is gone keeps its number with no line
naming it; one run's writes number a new test alike on every leg. Two
rows of a suite a run apart, or of two legs, then keep a line's set of
tests byte for byte wherever the same tests reached it, and git can
store the second as a small difference from the first. A row writes each
line's set of tests as whole numbers in decimal, comma-separated: the
first test's number, then for each next test how many numbers it skips.
The sets hold digits and commas alone. A forge's push protection refuses
a push whose text matches the shape of an access token, and every such
shape holds letters, so the sets never match one; a leg's thousands of
sets, written in an encoding with letters such as base64, can match one
by chance. A row whose parts are of another version of the line format
is skipped and named, and its suite is measured afresh. Each file's
fixed facts, what its content and its measurer decide whatever ran (its
statements, its branches, its functions, its ranges, and the sentence of
each branch), are a file of their own beside the rows, named by their
digest and held once however many rows name them, and a write drops the
facts no row names. A read of a record parses its current rows and the
facts they name alone, and a row naming facts the record lacks is
skipped, naming them and the content, so its suite is measured afresh.

The gate narrows on what the change affects alone; after it, the
coverage leg runs every suite neither its branch's record nor main's
holds at the suite's current closure, measured for its lines, and says
why. The workspace's own `tests/` directory is a unit too, keyed by
every package's tree, while the root's set holds python: every leg that
runs a suite runs it then. A root whose set holds no python runs none of
its own tests, so no leg leaves a part for them and the union expects
none. The gate job unions the legs' parts with the parts of every
skipped suite, carried from the records, before it judges, so the union
is the same global union a full run produces, and writes that union back
onto the branch's record; a suite no record can supply is red by name,
never a smaller union. So is a check leg the run runs that left no parts
on its per-run ref: its write was refused or it died before it, its own
log says which, and the legs that did write never stand in for the lines
only its platform reaches. The legs a run owes are the event's: a pull
request owes the legs `[ci] pull-request-runners` names, and a leg it
does not run is carried whole from the records; a pull request labelled
`ci:every-runner` (`fm submit --every-runner`), a push and a dispatch
owe every leg `[ci] runners` names.

A branch's record outlives the branch by a day, because main's run for
the squash that merged it carries the suites its legs skipped from that
record, minutes after the merge deleted the branch. A row a write
replaces at another closure, or removes, stays a day too: a leg may skip
a suite on main's row minutes before main's run for another merge moves
that row on, and the run's union still finds the row at the closure the
leg skipped on. A kept row is named by the moment it was kept and its
closure, and ends in its suite's own row name, so git can store it as
the difference from its suite's other rows. A leg itself skips on
current rows alone. At the merge main's run finds its tree on the
verified record, which names the branch, skips the gate, and copies the
branch's record into main's without measuring; a squash of a stale
branch has another tree and pays the full gate. A suite neither record
holds is measured on the spot. The janitor drops a branch's record once
the branch is gone from origin.

## The preview

A local `fm check` prints its own lower-biased preview beside the floor,
for information: the same figure over the machine's own run, a file the
meter named with no line run left out as the union leaves it out. The
python extension asks for it as the answer to `python.test.preview`,
which the coverage extension gives while it is listed. Raise a committed
floor as the suite grows; lower it only deliberately, in a reviewed
change or an accepted row. The release legs publish a further,
informational union that includes the live-only code.

## The coverage pages

The extension adds a step to each of the docs extension's two phases in
its `[for.docs]` table, for each package whose `[docs] coverage` names
an `htmlcov` report. It draws each such package's pages itself, from
parts in its line format: per file, the statements and the lines that
ran, the branches with the sentence that says why an untaken one never
ran, the functions and classes, the tests that reached each line, and,
for a measurer that reads columns, each stretch of code with its count,
so a line that ran with code that never did is drawn partial and that
code is marked. The numbers are coverage.py's own, and the source is
highlighted by Pygments, so every language draws alike. In the docs
phase the pages read the data a gate run left on the machine: the parts
a native suite's measurer left in the workspace, and the parts the
extension that answers the `coverage.measurers` query for a measurer
makes of that measurer's data, the pytest extension of coverage.py's. In
the deploy phase they read the parts main's coverage record stores, as
the legs made them. A measured file the checkout no longer has is red in
CI and named on the index on a machine, and a file at other content
keeps its numbers and says so. Each index states the package's floor:
the one its contract names or the default, the mark it ratchets to, read
beside main's record, or that nothing judges it. While nothing listed
measures, the coverage extension is dormant and adds no step, and each
declared report's page states the absence.
