# livery.workshop

The devkit: what every repository in the livery ecosystem runs. A
repository depends on the workshop, and footman mounts it through its
project rung: the rendered `tasks.py` mounts nothing, and every verb
below arrives through that dependency.

## The extension model

The workspace contract (`workshop.toml` at the root) lists the
workspace's extensions in `[workspace] extensions`, in precedence
order, and each package's contract lists the package-level extensions
the package is composed of in its own `extensions`. Those lists are
the whole of discovery: a package installed by accident never changes
a repository. The root's list is required; a contract without it
refuses at mount and prints the line to add. The base,
`livery.workshop`, is never listed: importing its plugin registers
its task surface and then mounts the listed extensions, the
package-level ones first and then the root's list in its order.
The instance always wins last: its own files (`CLAUDE.project.md`,
anything below the plugin line in `tasks.py`) are seeded once and
never rewritten.

Contract keys are kebab-case, at the root and in every package's
contract: a key spelled with underscores refuses on read, naming its
spelling and the file to rename it in. Every key is declared by the
extension that reads it, and a contract holds nothing else: a key no
extension declares, a key of an extension no list takes, a value of
the wrong type and a value outside its allowed set each refuse on
read, naming the file, the key, what the table takes, and the nearest
spelling. A workspace extension's keys are taken while the root lists
it; a package-level extension's, in a package's contract while the
package lists it, and in the root's while any package does. The base declares its own keys the way an
extension does, in the `contract.toml` its package ships beside its
code, the keys of `extension.toml` among them.

The declarations compose into one JSON Schema per contract, and the
judge validates a contract against it. `fm sync` writes the same
schemas, one file per contract under `.workshop/schema/`:
`workshop.json` for the root's contract, `package.json` for a
package's, `extension.json` for an `extension.toml`. Each holds the keys
of the base and of the extensions the root contract lists; a key of an
installed extension the root does not list stays in it as a key that
takes no value and names its extension. The files follow what is
installed and listed, and they are written for the checkout alone,
never committed. The composed `.taplo.toml` points Taplo, the language
server behind Even Better TOML and other editors' TOML support, at
each file by path from the root, so an editor completes and validates
a contract as it is typed and refuses what the judge refuses. Each
key's doc is its description there, shown when a person hovers the
key; every key the base and the listed extensions declare carries one. The
schema says what shape a contract has; a rule a schema cannot say
stays with the code that reads the key.

An extension is declared by an entry point in the `workshop.extensions`
group, mapping the name a contract lists to the package that ships it.
Beside that package sits its `extension.toml`, which the mount and the
contract judge read without importing the extension. The file is a
contract like `workshop.toml`: kebab-case keys, every key judged, and a
refusal naming the file, the key, what the table takes and the nearest
spelling. Every key is optional:

- `[extension] api-version`, the extension API it was written for,
  refused at mount when it is not this workshop's;
- `[extension] levels`, where it may be listed: `"workspace"` in
  `[workspace] extensions`, `"package"` in a package's own
  `extensions`; a listing at another level refuses;
- `[extension] plugin`, the footman plugin carrying its verbs, mounted
  in list order;
- `[extension] requires`, the extensions it cannot work without: in
  the root's list, each one before it; for a package-level extension,
  in the package's set, which takes them in. The layering check keeps
  them listed, and its `--fix` writes a missing workspace entry;
- `[extension] compatible`, the package-level extensions it combines
  with on one package where neither requires the other nor contributes
  to it; a claim from either side connects the two;
- `[extension] before` and `after`, the extensions it runs before or
  after in a package's phases, each one it requires, declares
  compatible or contributes to;
- `[phases.<phase>]`, the steps a package-level extension adds to a
  package's lifecycle phase: `pre`, `main` and `post`, each
  `"module:function"` in its own package, and the context keys the
  steps `provides`, each with its type, and `reads`;
- `[queries]`, the questions about a package a package-level extension
  answers, each query's name to the `"module:function"` that answers it;
- `[categories]`, the categories a package-level extension gives its
  packages' paths, each category to the patterns it takes; a package
  whose extensions declare categories is answered by them and the base
  kind's rules, and its own `[categories]` wins over both;
- `[toolroom] requires`, the tools its own verbs need,
  `["docker?>=27"]`, one of the sites the tool profile reads;
- `[contract.<contract>.<table>]`, the contract keys it reads, each a
  table with its `types`, and its `values` and `doc` where it has them;
- `[checks.<tool>.<role>]`, each check it adds to the gate, registered
  under the listed name, which the gate prints beside each;
- `[options]`, each option a workspace may turn on, to what it turns
  on; a check whose `listed-with` names an option registers only when
  the entry turns it on;
- `[contributions]`, the values it puts into slots, by slot;
- `[for.<target>]`, the same tables, read only while the target is
  listed;
- `[replaces]` and `[deletes]`, an earlier extension's shipped file,
  `"<owner>:<name>"`, that its own file of that name replaces or that it
  deletes, to the reason.

A check whose verdict is its tool's exit code is the tool's words, and
needs no code:

```toml
[checks.ruff.format]
judge = ["ruff", "format", "--check", "--force-exclude"]
fix = ["ruff", "format", "--force-exclude"]
narrowing = "paths"
kinds = ["python"]
tools = ["ruff"]
arguments = true
claims = [{ category = "source", suffixes = [".py", ".pyi"] }]
```

`judge` is the tool's name, as `tools` names it, and the arguments that
judge; `fix` and `safe-fix` are the words that rewrite under `--fix` and
`--safe-fix`, `safe-fix` falling back to `fix`. The workshop appends the
words after `--` on the check's verb, then the paths the run reaches, or
nothing when the run is whole, so the tool reads its own configuration;
it splits a long list into the fewest calls under the command-line
limit, runs the tool through its toolroom handle, and a non-zero exit
refuses, every call's reason in one refusal. A package check is called
from the package's directory with the package's files. `env` gives each
call variables over the run's own, and a word may name a placeholder the
workshop answers: `{cache}`, the check's directory under
`.workshop/.cache/`; `{package}`, the package a call judges; and
`{compile-commands}`, that package's compilation database, without which
the call is skipped. `matrix` names a placeholder of its own and its
values, one call per value in parallel, each its own verdict:

```toml
[checks.mypy.typecheck]
judge = ["mypy", "--platform={platform}", "--cache-dir={cache}/{platform}"]
matrix = { platform = ["linux", "darwin", "win32"] }
```

`fm explain typecheck.mypy` prints the command a check runs and what the
workshop appends to it. The conformance kit's `words-are-answerable`
clause refuses a placeholder nothing answers, a matrix key no word
names, a tool the check does not require, and a package's placeholder
in a check that judges the workspace whole.

A check the words cannot say, one whose verdict is not its tool's exit
code or whose paths need reshaping first, names the functions that run
it instead, `run` and `fix` as `"module:function"` in the extension's
own package, each imported when the check runs. A check declares
`judge` or `run`, never both. The mount reads each reference from its
module's source. A module outside the package, or a name the module
does not define at its top level, refuses naming the file and the key
before anything runs.

An entry names its extension, and the options it turns on in brackets:
`"basedpyright[typecomplete]"`, either as the string or as a table
entry's `name`. An entry takes no version, `?` or scope: an
extension's version comes from its wheel, and the lock pins it. An
option the extension does not declare refuses in the layering check,
naming the ones it declares; the mount names it and leaves it off.

A footman plugin that declares no extension is never offered as one.
The mount registers a `[for.<target>]` table once both its owner and its
target are listed, so a house with opinions on several languages
contributes to each only where the language is listed. The layering
check's `--fix` writes the resolved targets into the entry once,
`{ name = "acme.house", for = ["python"] }`; from then on `for` is
the truth: a name deleted from it stays deleted, which is a project's
opt-out from that target's opinions, and a name the list does not
carry refuses. A name the extension declares no contribution for
mounts nothing, and the layering check names the entry.
`fm extensions` prints the base, then each extension in mount order,
who requires it, the packages whose sets hold a package-level one, its
tools, its targets, and each option with whether it is on.

A package's `extensions` names the package-level extensions it is
composed of. Its set is that list and every package-level extension
the list requires, transitively. The set is valid when every pair in
it knows each other: one requires the other, one contributes to the
other through `[for.<target>]`, or one declares the other
`compatible`. The layering check refuses an unconnected pair, naming
both, and a requirement that no list holds at its level. A package
writes its set as its canonical list, the minimal one: no extension
another listed one requires, in composition order. In that order an
extension comes after what it requires and what it names in `after`,
and before what it names in `before`; ties go alphabetically. The
layering check names a list in any other form, and its `--fix`
rewrites it. A package's entry is the extension's name alone: a
package-level extension takes no options.

The mount takes the package-level extensions every package's set
holds first, in composition order, then `[workspace] extensions` in
its order, and composition follows the same order. So a package-level
extension's content, tools and distribution belong to the workspace
while a package lists it. `fm extensions --combinations` prints every
valid set of the installed package-level extensions, each named by its
canonical list joined with `+`, such as `cmake+cpp`.

A package lives through nine lifecycle phases: `create`, `sync`,
`stamp`, `build`, `prove`, `publish`, `replay`, `clean` and `run`. Each
extension of a package's set may add steps to a phase:

```toml
[phases.build]
pre = "acme.native._build:configure"
main = "acme.native._build:compile"
post = "acme.native._build:tidy"
provides = { wheel = "path" }
reads = ["abi"]
```

A phase calls every `pre` in order, then every `main` in order, then
every `post` in reverse order. A `post` runs once its extension's `pre`
ran, whatever failed after it. A failing step stops the `pre` or `main`
steps that follow it, and the phase raises its exception again once the
due `post` steps ran. Each step receives a `PhaseContext`: it writes
the keys its extension provides with `provide`, reads the ones it reads
with `read`, and a `post` learns what failed from `failed`, `failure`
and `failed_extension`. A value of another type than the declared one
(`str`, `int`, `bool`, `path`, `strs`, `paths` or `table`), and a key
the extension does not declare, refuse naming the extension. The order
puts a reader after the provider of each key it reads, then follows
`before` and `after`, ties alphabetical; requirements order nothing
within a phase. Two extensions providing one key, a key nobody
provides, and an order with no start refuse before any step runs, and
the layering check names them for each package.

The workshop gives some phases keys of its own, which a step reads like
any other and no extension provides:

| Phase | Keys the workshop gives | Key the release train reads back |
| --- | --- | --- |
| `stamp` | `version` (`str`), the version to write | `changed` (`strs`), the files written |
| `build` | `epoch` (`int`), the build's source date; 0 takes the build tool's own | `dist` (`path`), where the artifacts are |
| `prove` | `resolution` (`str`), the leg's dependency resolution, and `release-dirs` (`paths`), the co-released set's `dist/` directories | `resolved` (`table`), each distribution the leg installed, to its version |
| `publish` | `version` (`str`) and `registry-target` (`table`), the resolved registry's fields | `published` (`bool`), whether anything uploaded |
| `replay` | `version` (`str`), the released version; `tree` (`path`), checked out at its receipt tag; `interpreter`, `index` and `extras` (`str`) | `exit-code` (`int`), the replay's |

The release train stamps, builds, proves and publishes a package, and
`fm release.replay` replays it, through these phases when its
extensions add steps to them, and through the package's kind
otherwise. A phase whose steps provide nothing under the
key the train reads back refuses, naming the phase and the key.

A package-level extension also answers questions about a package:

```toml
[queries]
public-modules = "acme.native._answers:public_modules"
executables = "acme.native._answers:executables"
```

The workshop defines each query, typed by its answer, and the rule
that combines the answers of a package's extensions; `answer(package,
query)` asks each extension of the package's set that answers, in
composition order, and combines them:

| Query | Answer | Combined |
| --- | --- | --- |
| `public-modules` | the modules that declare the public API | the union |
| `module-roots` | the names other packages reference the code by | the union |
| `version-files` | the files `stamp` may write a version into | the union |
| `current-version` | the version the manifests declare | one value every answer agrees on |
| `requirements` | each required distribution to its constraint | the union; a name valued twice refuses |
| `distributions` | each released distribution's ecosystem and name | the union |
| `compile-commands` | the build's compilation database | the answer of the extension no other answering one requires |
| `executables` | the executables `fm run` starts | the union; a name two extensions answer refuses |

A name the workshop does not define refuses with the nearest one, and
an extension listed at the workspace alone answers none.

`fm run <package>[:<executable>] -- <arguments>` starts one of the
executables a package's extensions answer under `executables`, and
exits with its exit code. A package with one executable needs no name,
and inside a package's directory the package may be left out;
completion offers each `package:executable`. First the gate's `build`
role runs over the package's dependency closure, dependencies first,
and passes over each package whose files and dependencies are
unchanged since its last good build in this checkout, so a second run
with nothing changed starts at once. Then the `run` phase walks every
extension's `pre`, the `main` of the extension that answers the
executable alone, which starts it with the context's `executable` and
`arguments` and sets `exit_code`, and every `post` in reverse. An
extension that answers `executables` declares that `main`, or its
declaration refuses.

From any directory inside a workspace, a verb resolves the workspace
at the directory that holds the root's `workshop.toml`: a package's own
contract below it is passed over, and nothing is written into the
package's directory.

An extension's wheel carries its relations. Each `requires` target is
a dependency of the wheel, and each `compatible` or `[for.<target>]`
target is an extra of it that requires the target's distribution. The
composed dev group installs an extension with the extras its listed
targets put in use, so the version ranges its wheel states hold. The
conformance kit's `requirements-in-metadata` clause judges the wheel's
metadata against the declaration.

The workshop asks two questions about a path, and `fm explain <path>`
prints both answers with the extension that supplied each. Its
**category** says what the file is to its package, `source`, `test`,
`test-support`, `configuration`, and what an extension adds (`prose`,
`example`, `asset`, `nav`); a kind or an extension registers a pattern
table for a kind, `register_categories("python", [("src/**",
"source"), ...])` in `livery.workshop._categories`, a derived kind
inherits its parents' tables, the most specific pattern wins, and two
patterns of one specificity claiming one path refuse naming both. A
package reassigns its own paths in its contract, on this axis alone:

```toml
[categories]
vendored = ["docs/assets/vendor/**"]
```

Its **channel** says who wrote the file and where to edit it,
answered by ranked rules an extension may add to. The workspace root is a
unit of its own, with `notes/`, the site's files and `README.md`
categorised beside its tests, and a `src/` at the root refuses in the
layering check, since the root is never a package. A CI job may
declare the files its entries read as `inputs`, the table a check
declares: a pull request's run that changed none of them, nor the
sources of the extension that contributed the job, skips the job's
entries with a line saying so. The docs extension's gate job reads the
workspace's `docs/`, `zensical.toml` and `README.md` and each package's
pages, nav, assets, examples and generated pages, so a pull request
that changes only `notes/` skips the site build, and `fm explain`
prints `claimed by: gate/docs` on a file the build reads. On a
contract it also names the schema the contract is judged by, and the
owners whose keys that schema holds.

A check also says what its tool needs of the workspace. Its `tools`
reach the tool profile for every kind the check judges, each
requirement naming `check <name>` as its site, so unregistering a
check removes its tool. An extension's `[contributions]` fill
**slots**, the holes the base template leaves: the `dev` dependency
group's tool lines and pytest's `addopts` are the first two, a list
composing as the union in contribution order and a scalar taking the
nearest contribution. The
site's private-members policy is the third, `docs.members`: `public`
keeps each extractor's default filter, `all` documents every member,
and any other value refuses naming both. The theme block is the
fourth, `docs.theme`: a table of the block's values (`language`,
`font.text`, `font.code`, `features`, `palette`) a theme extension
contributes in part, merged key by key in contribution order over the
base's block, an unknown key refusing. An extension's site css ships in
its wheel under `content/docs/assets/`; the build stages it under
`docs/_extensions/` and lists it in `extra_css` in extension order, before
the packages' declared sheets and the workspace's own
`docs/assets/site.css`, which loads last. A
check an extension withdraws takes its lines with it.

A check is named by its role and its tool, `test.pytest`, and that
name is everything a person types: every role is a verb and every
check a sub-task of it, generated from the registry. `fm test` runs
every check of the test role and `fm test.pytest` the one, over the
named paths or the workspace; a check with a second role answers
under both. A verb offers exactly the flags its checks read:
`--fix` and `--safe-fix` where a check can fix, `--point` where one
selects tests by CI point, so `fm test --point=nightly` runs the
nightly's tests and `fm test.ctest` offers no point at all. A role
whose verb already exists is served by it: the provenance check is
`fm provenance`.

A check's own verb hands the tool it wraps the words after `--`:
`fm test.pytest packages/strongroom -- -k slow` runs that package's
suite with pytest's `-k slow`, and `fm lint.ruff -- --statistics` gives
ruff its flag. The words before `--` stay paths. A role verb runs
several tools, so it refuses words after `--` and names the checks'
own verbs that take them; `fm check` refuses them the same way. A
check that wraps no tool, such as `fm drift.check`, refuses them too.
A check says it takes them with `arguments = true` in its table, and
its body reads them from the context's `arguments`.

A check's `options` are what a package may set in its own contract,
each with a type and a default; every check carries `enabled`, and
pytest's test check carries `parallel`. Three tables reach them:

```toml
[checks.basedpyright]              # every check of the tool
[checks.basedpyright.typecomplete] # the tool's check of that role
enabled = false                    # skipped by name in the gate's output

[roles.test]                       # every check of the role, whatever its tool
parallel = false                   # the suites run under -n 0, each in a run of its own
```

The deeper table wins key by key: a role's table, then the tool's,
then the check's. An option no check the table reaches declares, a
tool or role no registered check has, and a value of the wrong type
each refuse, naming the vocabulary; a table spelled role first,
`[checks.<role>.<tool>]`, names the address that replaces it.

An extension's kinds and checks are judged by the conformance kit,
`livery.workshop.testing`: a `Subject` names what the extension registers,
each clause in `CLAUSES` returns the violations the subject commits,
each naming its clause, and an extension's own suite runs every clause on
its subject. The clauses so far: a concrete kind's backend takes every
call of the backend protocol; a per-package configuration file resolves
to the nearest kind's fragment, one owner per kind and file; a path's
category is the most specific rule's, the nearer kind winning a tie
between kinds and two rules of one kind never tying; every check a
check runs `after` is registered, and following them never leads back
to it; an extension's `extension.toml` holds only declared keys, each of
its type, and every reference in it names a function its module
defines; importing a module a reference names registers nothing, which
the kit proves by importing each in a process of its own; every task a
CI job's entries name is one the extension defines, read from its
sources; every category a check's claims name is one an extension
registers, every suffix starting with a dot; the plugin an
`extension.toml` names is an installed `footman.tasks` entry point;
every contract key an extension declares carries a doc; a check's
fragments render with the kit's probe answers, the
composed `pyproject.toml` still parses, and a per-package file matches
its render until a person edits it, when the drift gate names it; and
once a check is withdrawn and no other check renders its per-package
file for the kind, an unedited copy is removed and an edited one kept;
and in the gate's walk over a probe workspace, with the check bodies
recording instead of running, every check that can fix rewrites
before any judge starts and is not judged again, every check names the
extension that registered it, which the gate prints, and a check with no
file to read is named and never started. The workshop's own kinds and
checks pass the same clauses in its test suite.

The documentation site is an extension inside the workshop wheel,
`livery.extensions.docs`, listed in `[workspace] extensions` as a
table naming `livery-workshop` as its distribution. It owns the site's
assembly, the `docs` verbs, the `docs.members` and `docs.theme` slots,
the staged extension css, the generators a package declares and the
seam the site publishes through, and it arrives through its own task
entry point. The base keeps what it reads of a package's docs for its
own reasons (a package's `[docs] api` and the layout of the `docs/`
tree) and the nav blocks generators write; it imports no extension,
which the layering check enforces. It lives under `livery.extensions`,
a namespace any distribution can add an extension to.
The root contract's `[docs]` table is the extension's, `site-url`
among its keys. A `pre_tasks` hook of the extension links each task
name in `--help`, `--list` and `--json` rows to the task's page on the
site, through the redirect page `tasks/<name>/` under `site-url`,
where `<name>` is the task's address with its dots as dashes. A
workspace whose pages live elsewhere sets a URL template in
`[workspace] docs-url` instead, which the base's own hook hands to
footman: `{path}` is the task's address joined by slashes, `{slug}`
joined by dashes. That key comes before the site's, and footman's own
configured `docs-url` before both.
The site's two CI jobs come with the extension: its `extension.toml`
declares the gate point's `docs` job, which the verdict waits for, and
the merge point's `deploy` job, as `[ci.jobs.gate.docs]` and
`[ci.jobs.merge.deploy]`, each with the tasks it runs; a workspace that
does not list the extension renders neither job. Each job names the
function that lists the system packages it installs, the deploy names
the function that answers its publish seam, and the gate job declares
the files the site reads, so the CI render and the shell's skip rule
work without reading the `[docs]` table. A contributed
job sits before the point's verdict job, or last on a point without
one. The extension
renders the site's development section from the prose fragments
the mounted extensions ship for a human reader, one page per section
under `development/`, and renders each kind's API extractor from its
data: the handler's name, a package's pages and search paths, the
inventories, and the handler's options as a table.

The formatter and the linter are an extension with a distribution of
its own, `livery-extensions-ruff`, listed as `ruff`. Listed, it
registers `format.ruff` and `lint.ruff`, which join the `format` and
`lint` roles, and `fm sync` writes the root `ruff.toml` they read,
which a bare `ruff` reads too. A package's own settings go in its own
`ruff.toml`, which extends the root one with `extend = "../../ruff.toml"`;
its per-file ignores go under `[lint.extend-per-file-ignores]`, because
a `per-file-ignores` table of its own replaces the workspace's.
Unlisted, the extension registers nothing and writes nothing.

The type checker basedpyright is an extension of its own too,
`livery-extensions-basedpyright`, listed as `basedpyright`. Listed, it
registers `typecheck.basedpyright`, and `fm sync` writes the root
`pyrightconfig.json`, which the editor and a bare `basedpyright` read;
the repository's own settings go in its region at the end. Listed as
`basedpyright[typecomplete]`, it also registers
`typecomplete.basedpyright`, which verifies that each package's public
API is type-complete. `fm new.project` lists `docs`, `ruff` and
`basedpyright`.

mypy is an extension of its own as well, `livery-extensions-mypy`,
listed as `mypy`. Listed, it registers `typecheck.mypy`, which checks
linux, darwin and win32 with a cache each under `.workshop/.cache/mypy/`,
and `fm sync` writes the root `mypy.ini`, which a bare `mypy` reads. A
new project does not list it.

A package's documentation examples are files under `docs/examples/`,
python files a page shows whole or by named section through the
snippets extension: a fence whose one line is the snippet marker
followed by the quoted repository-relative path of the file, with
`:part-1` after the path for one part, and `# --8<-- [start:part-1]`
and `# --8<-- [end:part-1]` lines around that part in the file. The
pytest extension's `examples.pytest` check runs each file as one test
through the kind's runner, pytest for python, from the workspace root;
a failure reports the example's own file and line. An example is
never a test module: pytest's own collector leaves it alone, named or
not, so a `test_*` function in it is code a reader sees and never a
test the workspace runs. A run over named files runs the named
examples; a whole walk runs the package's directory. A `conftest.py` beside the examples is
the package's setup around them, never an example: it reaches every
example item through the `example` marker, as footman's does to run
each inside a captured registry, and naming it runs every example.
The `example` category is claimed by the ruff extension's `lint.ruff`,
for names only, by `examples.pytest` and by the site; a page's prose reaches the site
build and no test, and an example file reaches the examples check and
the site.

A check record also owns its configuration. Its `fragments`, one per
rendered file, are what the render writes for it: a `[tool.*]` table
of the root `pyproject.toml`, composed in check-name order, which a
check an extension withdraws takes with it. A tool that reads one
file per project gets a file of its own instead, written by its
extension under the name the tool looks for: ruff's `ruff.toml`,
pytest's `pytest.toml` and coverage's `.coveragerc`. A tool that
searches upward from each file, clang-format and clang-tidy in a
native package, gets a managed file where it looks,
rendered from the record's fragment for the package's kind and judged
by the drift gate, with a `.workshop-rendered` receipt beside it so a
withdrawn check's file goes only when nobody edited it and an edited
one is kept as a local override. A package's own lines ride the tool's
inheritance, a deeper file with `InheritParentConfig`. The editor
follows the same set: `.vscode/settings.json` takes each record's
lines, and `.vscode/extensions.json` recommends the extension ids the
records carry and nothing else, with a region for the repository's
own.

A check record also says which files it judges: its `claims`, named
in categories rather than as globs, each with the rules the check
withholds there and the suffixes it reads. A category is a role, not
a language, so ruff claims a native package's configuration and
reaches its `conanfile.py` alone, which a scoped gate hands it by
name. `fm explain` prints the checks whose claims reach a file, and
the per-file ignores in `ruff.toml` render from the claims over the
present kinds' tables, so the docstring rules stop at
the tests of every kind without a table typed by hand.

An extension's prose, its voice, its standards, its rules, is a set of
fragments in sections the base orders: identity, voice, standards,
rules, workflow, gate, verbs, kinds, tools. A fragment is a file in the
extension's `content/fragments/` named
`<section>.[<kind>.]<topic>[.<audience>].md`, or a registration with a
render that answers each audience from the registries; a kind in the
name delivers it only while a package of that kind, or one deriving
from it, is present, and two fragments of one name refuse at sync
naming both files. `fm sync` delivers the agent's set flat under
`.workshop/fragments/`, a shipped file byte for byte and an edited copy
kept and named, and writes the entry file from it in section order,
the repository's own `fragments/` last. The gate, verbs, kinds and
tools sections render from the registries, so no fragment names a
checker by hand.

The layering check parses every python source once per gate and
memoises the parse by the file's bytes, and a kind or an extension may
register a rule over that parse beside the builtin three (the
runner's one-answer rule, the forge's stdlib rule, the sibling
references): `register_ast_rule(AstRule(name, judge, fix=...))` in
`livery.workshop._ast_rules`. A rule's fix runs inside the check's
rewrite and its judgment inside the check's judge, and each problem
it reports carries the rule's name.

Each extension may carry a `content/` directory; `fm sync` delivers it:
guidance fragments into `.workshop/fragments/`, skills and hooks into
`.claude/` as links (a local override is kept and named), and the
managed `CLAUDE.md` stub whose imports end at the instance's own
`CLAUDE.project.md`.

While the workspace has a member whose kind packages with conan,
`fm sync` also writes `conanws.yml` at the root, listing each such
member by its path. A conan command run inside the checkout resolves
a member from its source tree through that file, and nothing is
registered in the conan home. So each checkout of a repository, a
worktree included, resolves to its own sources, and all of them share
one package cache. The file goes with the last such member, and the
drift check judges it like any composed file.

A composed file is the extensions', judged byte for byte, and some of
them carry lines of your own. Those lines live in a region: a pair of
marker comments the composition writes, such as
`# -- workshop: region tables, yours to edit; the render keeps it --`
and `# -- workshop: end tables --`. `fm sync` reads what stands
between the markers from the committed file and writes it back in
place, so it keeps your lines and `fm drift` still compares the whole
file: an edit inside a region is yours, an edit outside it is drift,
and a removed marker is drift too. The root
`pyproject.toml` carries a `tables` region for your own tables, as
does the root `ruff.toml`; the root `.gitignore` carries a `rules`
region, `.vscode/settings.json` a `settings` region, `tasks.py` a
`tasks` region below the mount, and the changelog extension's
`cliff.toml` in each package an `own` region. `fm explain <file>` names a
file's regions and their lines. A managed file whose format has no
comments keeps your lines as a tail after the lines the render owns.

## The tools a workspace requires

Six sites declare tool requirements, each in the one requirement
grammar, `name?>=floor@scope`: `ruff`, `ruff>=0.16`,
`dotnet_coverage@windows`, `tea>=1.1@linux,macos-arm`. A scope names
platforms (every locked host of one) or host keys, each excluded with a
leading `!`, so `tea@!windows-arm` is every locked host but one. `?`
marks a tool optional: the lock takes it where the catalogue can serve
it and never refuses it, its entry says `optional`, and what it left
out is printed when the lock is written; `fm toolroom.sync` names an
optional tool its host lacks. The sites: a package kind, in its record,
for what operates it, uv for the python kind; the checks that judge a
kind, each naming its tools, which is how ruff, pytest and the checkers
reach a python workspace; a listed extension, as `[toolroom] requires`
in its `extension.toml`, for what its own verbs need; a plugin the project mounts
through its direct dependencies, as a tuple in a data module its
`workshop.tools` entry point names under the plugin's own name, loaded
without the plugin's tasks, which is how forge's dev verbs bring
`docker?`; a package instance, in its
`workshop.toml` under `[toolroom] requires`, for what its kind cannot
know; and the project, in the root contract's `[toolroom] requires`, for
what belongs to the repository. The root contract's `[toolroom] index` names where the
catalogue is read from, the published index's URL or a directory
holding the index or the records that build one. The repository that
authors the records reads them directly.

`[workspace] hosts` names the hosts the workspace supports, in the
scope tokens a requirement takes after `@`: a platform (`macos`), a
host key (`linux-arm`), and either with `!` to remove it. Without the
key every host key is supported. The lock covers every supported host,
so a requirement without a scope must resolve on all of them. `fm
sync` on another host says so once; in CI, and from `fm toolroom.add`, it
refuses, naming the host and the supported set.

```toml
[workspace]
hosts = ["macos", "linux-x64", "windows-x64"]
```

`fm toolroom.lock` resolves every site's requirements against the
catalogue and writes `toolroom.lock` at the root: one version per tool for
the whole repository, the newest that satisfies every floor and
resolves on every host the tool is required on, refusing by name
otherwise. A tool required on some of the locked hosts alone is
locked on those, its entry names them, and a scope no locked host
matches locks nothing, which the lock says per requirement. `fm
toolroom.add <requirement>` declares a tool at the project site, locks it
and materialises it; `fm toolroom.lock --upgrade-tool=<tool>` moves one entry to the
newest eligible version, and every package with it, since no package
runs a version of its own.

Entering the environment materialises the bundle the sites require:
`fm sync` supplies every tool locked for this host through the
machine's store, skipping one locked for other hosts alone, the
downloaded kinds from the catalogue's deployment for this host through
`[toolroom] sources` (folders or URLs in the store's layout, consulted
before the origin) and the delegated kinds through their installer, in
one of three modes per tool: `link` puts its entry points in the
checkout's `.workshop/bin`, `path` its own directories on PATH, `none`
neither, for a tool reached only through a typed handle or its env.
A download with paths takes `path`, one with none and a system tool
take `none`, unless the record or the root contract's `[toolroom] modes`
says otherwise; every installer's kind takes `path`. A system tool is the machine's own: the store installs
nothing for it and holds the copy on PATH to the highest of the
record's floor and the sites' floors, naming the site whose floor it
is under. A tool named in the root contract's `[toolroom] host-allowed`,
or in a kind's own allowance, may be served by a copy already on the
machine: `fm sync` looks on PATH first, and a copy that satisfies the
floor (the requirement's, else the record's minimum, else any
version) serves with nothing installed, while one that is absent or
below the floor is passed over with a note and the locked version
serves. A tool a check reads its verdict from (format, lint,
typecheck, typecomplete, test), `uv`, and a tool an extension writes
the release notes with take no allowance, and the lock refuses one
by name: a linter that varies by machine makes the gate disagree with
CI, and a release's entries must not depend on the machine. The lock's entry carries
`allow-host`, so every checkout agrees on which tools may vary. Each
tool leaves a receipt under `.workshop/receipts/`, this checkout's
statement of what it installed: the exact version, the host, the
deployment's digest and what reached PATH, and for a host-served
tool `source: host` with the version its copy printed, as the release
train's receipt is a release's statement. `fm env.emit` carries the
receipts' paths and variables, and `fm env.check` names each required
tool's receipt and the drift when the lock's deployment has moved
under it, a tool with no receipt that still resolves from PATH being
named and not a problem. `fm` enters the environment for itself as
well: every invocation applies the same PATH entries and variables to
its own process before any task runs, so the locked tools resolve from
any shell, an agent's, a CI step's or a bare terminal's, and an entered
shell adds only completion. The rendered `.vscode/settings.json` makes
the editor's integrated terminal an entered shell from its first
prompt, one profile per platform. A sync whose first act moved the checkout hands the rest to a
fresh process on the code now on disk, since the modules it loaded
are the old code; a re-run that cannot start is named and the sync
continues on the loaded code.

The stubs the four type checkers read are materialised too. `fm
toolroom.restub` writes them into `typings/` at the root, pyright's
default stub path and a search path the rendered configuration hands
mypy, ty and pyrefly: one stub per tool the lock holds, at the locked
version, as `livery.toolroom.stubs` modules with `livery.toolroom.handles`
beside them declaring the handles for the installed tools package's
index to import, under the namespace package and never inside the tools
package's own directory; a tool the
workspace does not deploy gets no stub. `fm sync`, the lock verbs
and the entry script write them as well, so a checkout and a CI runner
type against the same stubs. The store renders each stub from the
locked version's own surface, from the records or the index, so no
source holds a stub; a receipt under `.workshop/state/` names the lock and
the source the last write rendered from, and while both stand nothing
is read or written. `fm env.check` counts them and names their
absence.

## The task surface

- `fm check`: format, lint, four gating type checkers, public-API
  type-completeness, the tests with per-package coverage floors, and
  the render gate. Type-completeness covers what each root declares
  in its `__init__`; a namespace root, one with no `__init__`, declares
  nothing. A public package beneath a root keeps its own import path
  and is declared in the root's `__init__` as well, imported under
  `TYPE_CHECKING` and listed in `__all__`, and the verifier follows the
  declaration into it; `py.typed` sits at the distribution's root. Every gate walks the check registry the same way,
  whole or narrowed, on a machine or on a leg: under `--fix` every
  fixer that applies rewrites first, one at a time, then every judge
  runs in one parallel block, a check that rewrote not judged again;
  a check whose claims reach no file of its scope is named and no
  process starts for it. `fm check <paths>` checks exactly those files,
  or a directory's: every check whose claims reach one of them runs
  over them alone, and nothing is recorded as proved; `--safe-fix`
  fixes without removing code, the post-edit hook's mode; `--point`
  selects a CI point's tests. A named path that is no file or
  directory in the workspace refuses, since checking nothing there
  would pass; the checks pass nothing through to a tool, so a tool's
  own arguments after `--` refuse the same way. On a machine the gate
  is the reflex:
  it runs what the working tree changed since the nearest tree this
  checkout's own green gates proved (the one with the fewest changed
  paths, a green check of the dirty tree included), the packages that
  delta can influence (their dependents' closure over the `[[depends]]` graph),
  and records the working tree as proved, so the tenth commit of a
  branch pays for what the tenth commit touched and a tree the record
  already proves runs nothing; `--full` runs everything. A delta
  confined to a package's test files runs the package's whole suite
  and no dependent's, since nothing imports a test, after the kind's
  gate build when its tests run on a build (a C++ package's ctest); a
  changed conftest or helper, and a source change, run the suite and
  its dependents'. A suite is never narrowed to the changed test
  files: the tests a change reaches are more than the ones it edits.
  A machine's
  test run sets the runner's variables, so a test that reads them is
  judged here as on the legs. The chain of
  records rests on a full gate here or on the nearest tree in HEAD's
  history that CI's record holds: a fresh branch off main starts
  proved, and one cut before main's own run is green pays for that
  merge's changes in its first step. A change outside the packages
  runs everything and roots a new chain, with exceptions. Prose and
  the site's own files, a file under `notes/`, a markdown file
  anywhere, the root `docs/` tree, or the root `zensical.toml`, affect
  no package, and neither do the root files no package's checks read:
  the licence, the CI files and the code-owners file, the editor's
  and the agent's settings, git's own files, `setup.sh`, the release's
  member list, `overrides/`, and the render's receipt. A change that
  adds or removes a package is that package's: the entries naming it
  in the root files, and the lock's entries it moves, reach it alone.
  The workspace's own `tests/` directory is a unit of its own: a
  change under it formats, lints, type-checks, and runs those tests
  alone, and the record supplies every package's suite.
  Each workspace check runs when a file it reads changed, and judges
  those files: `lint.doclinks` the changed pages, and every page when
  a file is deleted or moved or a heading is removed; `lint.docrefs`
  the cross-references in the changed sources' docstrings, resolved as
  the API site resolves them, and every source's when a changed one
  lost a name; `layering.graph`
  only when a contract or a manifest changed; `layering.imports`, the
  rules over the python sources, the changed sources, and every one
  when the graph changed; `drift.check` a tracked composed or
  generated file when it, or what it is made from, changed;
  `provenance.check` the changed content files. A check whose own code
  changed reads everything. A diff that reaches no package and no
  file a check reads runs nothing. A workspace that declares
  `[ci] affected-legs = true` has its CI check legs run that scoped
  gate, measured by the same rule from CI's own record: from the
  proved tree nearest the checkout's, or from the merge base with the
  pull request's base branch when the record proves none. A pull
  request's run on GitHub tests the merge of its head onto the base
  branch, so its row names the two commits merged, and the next push's
  run rebuilds that merge and gates only what changed since it. The
  workspace checks are selected the same way, and the gate job after
  a green verdict stamps the tree it proved on the `workshop/verified`
  record, so a later run of the same tree, such as main's run after
  a squash of a branch on its tip, skips the gate in seconds. A
  narrowed run stamps too when the tree it narrowed against is on
  the record in full: every package it skipped is byte-identical to
  that base tree's, so their verdicts carry over, and the row names
  the base. Without such a base a narrowed run stamps nothing. The
  release train reads the same record before it waits on main's run.
- `fm <role>` and `fm <role>.<tool>`: one role's checks, or one
  check, generated from the check registry and run over the named
  paths or the workspace the way `fm check <paths>` runs them, with
  nothing recorded as proved; each offers exactly the flags its
  checks read. `fm test.pytest packages/strongroom` runs one package's
  suite, and `fm test.pytest packages/strongroom -- -k slow` hands
  pytest the words after `--`.

- The check legs run on every runner the contract names, with the
  newest Python of a derived matrix; the nightly point runs the whole
  matrix, floor included, so the floor's legs cost runner minutes at
  night and no pull request time, and a floor-only failure reaches a
  person the next morning. A declared `[ci] python-versions` runs at
  the gate as declared. On a Windows leg of a GitHub-shaped workspace
  the entry points `TEMP` and `TMP` under the runner's own temp: the
  hosted runners keep the workspace, the uv cache, and that temp on
  the fast working drive and the system temp on the slow system
  drive, so the tests' files and the environments they build share
  the drive with the cache and uv links instead of copying.
  `[ci] windows-temp = "system"` leaves the system temp, for a runner
  without a separate working drive; `"runner"` asks for the move on
  any forge.
- A check leg pushes what it cost. The gate runs profiled, so the leg
  writes a Chrome trace of every task, step and test it ran and pushes
  it to a channel of its own: refs no sync mirrors and no gate reads,
  so a checkout pays for a trace only when someone asks for one. `fm
  ci.profile` then assembles a whole run into one timeline, and a
  profiled local command carries the run it caused. See
  [CI profiles](ci-profiles.md) for the file, the verbs, and the three
  contract keys. The push is observational: origin refusing it is a
  printed line and never a leg's verdict.
- On a GitHub-shaped workspace every job restores the tool store
  before it enters, under the runner's temp, the working drive, where
  the checkout and the venv are and where the store's links into the
  checkout stay links: the data directory the entry places there,
  keyed by `toolroom.lock` with the OS and architecture, falling back to
  the nearest archive under its prefix, so a moved lock restores what
  is unchanged and saves a fresh archive at the end. A restored store
  is a tier the store verifies on access, and `fm ci.run` sweeps it
  before the save, so the archive carries the trees the tools run
  from and not the artifacts they were extracted from. uv's cache is
  placed on the same drive, so the sync links wheels into the venv
  instead of copying, and is not cached: the wheels it would hold
  arrive faster from the index than from an archive. A leg that
  builds native packages also restores conan's home from the same
  drive, keyed by the recipes with the OS and architecture and
  falling back under its prefix, so a third-party package from Conan
  Center is compiled once per key and downloaded from the cache
  afterwards; the cache is the speed extension and Conan Center the
  origin, so a miss costs time, never a red leg. The other lanes
  cache nothing until they have a cache action.
  Tests are namespaced by their path (pytest's importlib mode, set in
  the pytest extension's `pytest.toml`), so two packages may share a
  test file's name; a helper module in a package's `tests/` carries
  the package's name, and the session refuses to start otherwise.
- `fm start`: open the work. An issue number assigns and branches
  `<kind>/<number>-<slug>`, a quoted title files the issue first, and
  `<kind>/<slug>` starts a branch that belongs to no issue, whose
  submit closes nothing. Every form branches from a fetched
  `origin/main` into a worktree under the runner's home, provisioned
  and entered when a person is at the terminal: where the shell hook
  is installed the worktree is entered in that shell, with its
  environment loaded and nothing to leave, and without the hook a
  child shell opens there instead (`--open=code` opens the editor,
  `--open=none` prints the path); `--no-worktree` reuses this
  checkout.
- `fm commit <type> "<subject>"`: a conventional commit on a proved
  tree. It stages the change (`--only` narrows), derives the scope
  from the packages the change touches, runs the affected gate first
  in its fix mode (`--no-check` skips), validates the subject the way
  `fm submit` validates a title, and refuses on `main` and on a
  reserved branch. A package whose only changes are the files the
  engine composes into it stays out of the scope: a template's change
  renders into every package, and its scope is the template's owner.
  `git commit` keeps working.
- `fm integrate`: bring `origin/main` into the branch by merge. A
  merge rewrites nothing, so every other copy of the branch stays
  valid, and the squash erases it at landing; a conflict stops with
  git's own words. When the merge moved `uv.lock`, the root
  manifest, or a member's, the verb matches the environment to it and
  names what moved, so the next gate runs on the merged environment.
  Every command makes that check before it runs: the venv follows the
  lock and the manifests, and one that drifted is synced, then the
  command runs again on the installed code.
- `fm submit`: get the branch onto the remote, verified. Its local
  gate is the reflex; when the chain of this machine's green gates
  proves HEAD's tree, back to a full gate here or a tree in HEAD's
  history that CI's record holds, it skips the gate and names the chain, from a record in
  the checkout's git directory that never leaves the machine. A row
  proves its tree for a week: the tree id covers the pins, not the
  machine's tools. `--armed`
  lets it land, `--fix` heals mechanical gate findings into the
  branch, and the follow classifies the verdict with stable exit
  codes; a follow that sees the merge from a linked worktree removes
  the tree and its branch, naming the directory to go to, and keeps
  a tree holding something the merge did not take. `--no-close`
  submits without the close footer, for preparatory work the merge
  must leave open; a pull request already green when `--armed` asks
  is merged directly, since a forge that arms only a blocked pull
  request refuses. `fm submit.merge` lands a green,
  deliberately-unarmed PR; `fm abandon [branch]` gives a feature up,
  this one or a named one, its worktree with it. `fm status`,
  `fm ci.*`, and `fm doctor` stand beside them, all on
  [livery-forge](https://pypi.org/project/livery-forge/).
- `fm drift` keeps the composed and generated files byte-identical
  to what the listed extensions and the contract write, and names
  `fm sync` as the remedy;
  `fm new.package` renders a member and wires it in.
- `fm release.prepare` and `fm release.verify` run the path-tag
  train (`packages/<pkg>/v<semver>`); a workshop release also
  publishes the template snapshot, tagged in lockstep.
- `fm janitor` sweeps the runner's directories and the state store:
  footman's cache, then what the workshop leaves in the data
  directory (the worktrees of closed issues and merged branches,
  which `fm start` sweeps first, the checkout's local branches
  whose pull request merged, and files and folders no code writes any
  more), then every series the workshop keeps, in the
  scope the run has: the checkout's local series on a machine, the
  remote series too inside CI, where the merge point's gate job runs
  it after every green run. Windows are enforced, aged rows and
  orphaned refs go, anything holding work stays and is named, the
  config directory is reported and never touched, and `--dry-run`
  says what would go. The same work runs wherever it is called from:
  what is safe to remove is decided by the rule that proves it, never
  by who is watching.
- `fm update` brings an instance up to date: floors to the latest
  released tags, content, render, then the submit flow. Nothing
  changed means nothing happens.

## Where a test runs

A test declares the CI points it runs at. Without a marker it runs
at the default points, the gate (a pull request's legs) and the
merge (main's run). `@pytest.mark.only_at("nightly")` runs it at the
named points and nowhere else; `@pytest.mark.also_at("release")`
adds points to the default ones. The job runner names the point to
every child it spawns, and the workshop's pytest plugin deselects
the rest; a local `fm check` selects for the gate, and
`fm test --point=nightly` selects for a point on demand.
A `[[ci.schedule]]` entry in `workshop.toml` attaches a task to a
point's job; `every = "1w"` or `"2w"` runs it on Mondays, or on the
Monday of an even ISO week, and any other run of the point skips it
naming the day it runs next. On Windows, a test whose child exits with
`STATUS_CONTROL_C_EXIT`, the status a console control event leaves,
gets a section in its failure report: the command, the process and
its worker, the run, every control event the process itself saw, and
every process attached to the console, so one sighting carries what
the next reader needs.
The nightly point runs the whole check with its own tests selected
in, and never skips on the verified record or narrows.
`fm ci.dispatch --point=nightly` starts the nightly now, on `main`
unless `--ref` names another, and follows the run to its verdict;
`fm ci.status --point=nightly` and `fm ci.logs --point=nightly` read
the newest nightly run by workflow rather than by commit, so a
failure only the nightly meets reaches a person through `fm`.
A package contributes a point of its own with `[[ci.point]]` in its
`workshop.toml`: `name` (a workflow file name and a job name at once),
`task`, and optionally `args`, `every`, `runners` (the root contract's
when absent) and `pythons` (the newest gate Python when absent). The
point runs on the clock and by hand, one job on those runners and
Pythons calling the task through `fm ci.run`, with the job token and
nothing more: a permission, a secret or an environment in the table
refuses, as does a builtin name, a name two packages claim, a cadence
that is not one, or a task no extension mounts. Removing the package
removes its workflow: `fm drift` reports the file as retired and
`fm sync` deletes it.
On GitLab the clock is a pipeline schedule, a project setting rather
than a line in the pipeline document: `fm workflow.configure` creates
one per point that runs on the clock, named `workshop: <point>`, and
deletes the ones it named for points no longer declared. GitHub and
Gitea keep the clock in the workflow file and reconcile nothing.
`fm ci.dispatch --point=gate` starts the gate the same way. A
dispatched gate is the full gate: the check verb reads the event and
narrows on a pull request alone, and it sets the verified record
aside as the nightly does, so a dispatch proves the tree it checks
out whatever the record already says. The shell spells one call on
every event; nothing passes `--full`.

## Coverage floors

Each package's `workshop.toml` may declare `[qa] coverage-floor`: a
percentage of statements and branches the gate enforces, or the mode
`"auto-ratchet"`, under
which the floor is the package's mark on the state store. Both modes
pass at the floor minus the package's `coverage-epsilon` (percentage
points, 0.5 when absent). Under auto-ratchet the first gated run
records the mark, a run that clears it by more than epsilon raises
it, and a store that cannot be read falls open with its reason and
writes nothing. Lowering a mark is a person's act:
`fm coverage.accept <package> <value> --reason=<why>` writes a row
naming who and why, and refuses without a reason, at or above the
current mark, or for a package with a committed floor. The number
that is judged is the CI union: on every leg the tests run measured,
each process a test starts included and the gate's own driver never,
so a line counts only when a test reached it, and the gate job
combines all platforms before enforcing, so the floors are
deterministic per change and never depend on one machine's view.
Measurement is the kind's answer, reduced to one shape before
anything is stored. A python suite's is coverage.py's arcs. A native
suite's is lines: the gate build is instrumented for the compiler
family CMake detected (gcc with `--coverage`, clang and apple-clang
with `-fprofile-instr-generate -fcoverage-mapping`), ctest runs, and
the measurer beside that compiler reads which lines of each source
file ran, gcov's JSON or llvm-profdata and llvm-cov's lcov. A run
built with MSVC is measured by Microsoft's Code Coverage engine,
`dotnet-coverage`, a tool of the store the workspace requires as
`dotnet_coverage` beside the .NET SDK it runs on: each test
executable is instrumented statically for the run, ctest runs under
the collector, its verdict is read from the report ctest writes, and
the Cobertura report is read per line. On Windows the gate builds
with MSVC unless `CXX` names another compiler, entering the newest
Visual Studio's C++ build tools itself when `cl` is not already on
PATH. The result is one part per package at the workspace root,
beside coverage.py's own. A host with the compiler and without its
measurer refuses on that leg by name. The union across legs is a set union of
lines per file, so two compilers never merge raw profiles, and a
native package's floor is line coverage over its `source` category
while a python package's stays statements and branches. A package
with a floor that no leg measured refuses by name and never passes.
Coverage stays global
under the
affected mode: in a check leg's one measured run every test records
under a context named by its node id (the workshop's own pytest
plugin, quiet outside a measured run), the leg splits the run's data
per suite and puts each suite's lines on its per-run ref of the
state store, with the identity of the suite's dependency closure
(the tree ids of the package and of every package it depends on,
plus the root's `pyproject.toml` and `uv.lock`). Main has a
coverage record, and so does every branch with a pull request run:
per check leg, one row per suite, the arcs the last run judged and
the closure each was measured at. A leg skips a suite only when its
branch's record or main's holds it at the suite's current closure;
otherwise the suite runs, and the leg says why. The workspace's own
`tests/` directory is a unit too, keyed by every package's tree, and
every leg that runs a suite runs it. The gate job unions the legs'
lines with every skipped suite carried from the records before it
judges, so the union is the same global union a full run produces,
and writes that union back onto the branch's record; a suite no
record can supply is red by name, never a smaller union. A branch's
record outlives the branch by a day, because main's run for the
squash that merged it carries the suites its legs skipped from that
record, minutes after the merge deleted the branch. A row a write
replaces at another closure, or removes, stays a day too, under its
closure's name: a leg may skip a suite on main's row minutes before
main's run for another merge moves that row on, and the run's union
still finds the row at the closure the leg skipped on. A leg itself
skips on current rows alone. At the
merge main's run finds its tree on the verified record, which names
the branch, skips the gate, and copies the branch's record into
main's without measuring; a squash of a stale branch has another
tree and pays the full gate. A suite neither record holds is
measured on the spot. The janitor drops a branch's record once the
branch is gone from origin. A local `fm check` prints its own
lower-biased preview beside the floor, for information. Raise a
committed floor as the suite grows; lower it only deliberately, in a
reviewed change or an accepted row. The release legs publish a
further, informational union that includes the live-only code.

## Test speed

The speed marks are off until the contract declares
`[ci] speed-marks = true`: a hosted runner's test time varies by tens
of seconds between two runs of one tree, so a mark taken from such
runs says nothing about the suite. Declare the key on runners that
keep a steady clock. While the key is absent the gate job drops any
marks left from before, so a later opt-in starts from the recorded
timings alone. On, the gate job judges each check leg's summed
test time per package against a mark on the state store. The mark is the median of the
leg's last five green runs for the package, recorded once five are
seen; it moves down when that median beats it by more than five per
cent, so a suite cannot regress slowly. A run over the mark by more
than fifteen per cent and twenty seconds warns in the gate job's
output, naming the leg's slowest tests and the ones that grew most
against their own medians; the second run over in a row on the
ubuntu leg is red, and the other legs warn only. A new heavy test is
a cost someone chose: `fm speed.accept <package> <seconds>
--reason=<why>` raises the mark, on the ubuntu leg unless `--leg`
names another. The marks are judged on the CI legs alone: a
machine's clock is not a leg they were taken on, so `fm check` on a
machine prints each package's summed time and reads no mark, and
`fm speed.judge` is unavailable outside CI. `fm ci.timings` shows
the marks beside the timings.

## The state store

Every row the workshop keeps across runs is a row of the state
store: JSON files on git refs, `refs/workshop/*` on the remote and
`refs/workshop-local/*` in the checkout's git directory. A default
clone or fetch never downloads the remote namespace, no refspec
names the local one, and every row carries the schema the store
stamped and the time it wrote it. A machine's gate never reaches
origin: `fm sync` and `fm start` mirror the remote namespace into
`refs/workshop-origin/*` and stamp the fetch, and `fm check` reads
the store from that mirror alone, so a hung SSH agent cannot hold
the gate. The mirror is what the last sync saw, by design; a
checkout the store was never fetched into reads it as unreachable,
roots its chain on a full gate, and names the sync. The remote series are written by
CI only, `fm coverage.accept` and `fm speed.accept` the exceptions;
a local run reads them and writes only the local ones, which the
checkout's worktrees share and a fresh clone starts without. A read
or a write of a series is a fixed handful of git processes whatever
the series holds: one `cat-file --batch` reads every row, one
`hash-object --stdin-paths` writes every blob, so a series of three
hundred rows costs no more processes than one of three (git 2.38 or
newer). A verb that reads several series takes one snapshot of the
remote namespace: one listing of the store's refs and the branches
together, then one fetch of the refs the checkout lacks, and every
read inside answers from the local object store, so `fm store.ls`
and `fm ci.timings` cost two round trips. The job runner takes one
snapshot for the whole job and publishes it to the entries it runs,
so the gate job lists once for its union, collect, judge, stamp, and
janitor. A write keeps its compare-and-swap on the listed sha, its
push's own report is its verdict, and it records the sha it pushed
for the reads and the entries after it. A check leg writes its
per-run ref once, its timing row beside its measured suites.

| series | a row is | window | writer |
| --- | --- | --- | --- |
| `metrics` | one run: every job's times, the run's wall, the union's percentages | 300 | the gate job |
| `run/<id>/<leg>` | a check leg's half of its timing row, and the suites it measured with the scope it ran, until the gate job collects it | none | the leg |
| `verified` | a tree a green gate proved, with its scope and the base it composed on | 200 | the gate job |
| `coverage/<base>/<leg>` | a record on one leg, main's or a branch's: one row per suite, the arcs its last run judged at the closure each was measured at, replaced in place, a row it replaced or removed kept a day; a branch's goes with the branch | none | main's gate job at a merge; a branch's own pull request runs |
| `coverage/marks` | a package's coverage mark and who set it | 400 | the gate job, `fm coverage.accept` |
| `speed/marks` | a package's test time mark on one check leg and who set it | 400 | the gate job, `fm speed.accept` |
| `gate-record` (local) | a tree this checkout's `fm check` proved green | 200, 7 days | a green local gate |
| `fetched` (local) | when origin's namespace was last mirrored into `refs/workshop-origin/*`, and how many refs | 1 | `fm sync`, `fm start` |
| `diagnostics` (local) | the follow classifier's inputs for an unmerged ending | 20 | every unmerged follow |

`fm store.ls` lists them with what each holds; `fm store.show
<series>` prints a series' rows newest first through the same reader
the verdicts use, `--key=<leg>,<package>` for one series of a family
and `--json` for the rows as they are. `fm ci.timings` renders the
`metrics` series: per job and metric the latest, p50, and p90 over
the window, then the movers. `fm janitor` bounds every series in the
scope it runs in, windows, ages, and orphans, the remote ones from
the merge point's gate job after every green run. The deploy renders
the site's coverage pages from main's coverage record, the union
main's gate judged whatever its legs ran.

## Where a test lives

Every pytest under a workshop venv runs apart from the machine's live
runner state. The workshop's isolation plugin points footman's data,
cache, and config directories at a fresh temporary home for the
session, before the first test and for every child process a test
starts, so a test neither reads the developer's config, tokens,
worktrees, and caches nor writes into them; a variable the outer
environment already set is kept. The one deliberate exception is a
live test reading the dev containers' credentials through
`livery.forge.testing.shared_env_path`, which skips without the file.
The plugin also guards the process-global task registry: a test that
leaves tasks there fails at its teardown naming them, and the next
test never inherits them.

The forge lane belongs to `livery.forge`; the workshop orchestrates
local, git, and forge steps and never hands a raw forge verb to a
user.
