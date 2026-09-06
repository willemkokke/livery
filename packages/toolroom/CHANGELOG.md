# Changelog

All notable changes to toolroom are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project follows
[SemVer](https://semver.org/) — pre-1.0, minor versions may include
breaking changes.

## [Unreleased]

### Added

- **An answer canned but never served warns.** A canned prefix that
  matched no call by a clean `answers()` block exit raises
  `UnservedAnswers` (a `Warning`): an unmatched call is often
  incidental, but an unserved table entry is almost always a mis-keyed
  prefix, and the test was passing vacuously without the answer it
  meant to inject. Blocks that exit on an exception stay quiet — they
  already failed loudly — and the category filters or escalates like
  any warning.

## [0.6.1] — 2026-08-21

### Added

- **`Call.args` / `Call.kwargs` — the call as handed.** The testing
  seam's record now carries what the caller wrote, pre-render:
  positionals verbatim and call keywords with False values included,
  where the argv honestly omits them. "Never handed" and "handed, as
  False" are different facts — `"python" not in call.kwargs` versus
  `call.kwargs["frozen"] is False` — and tests can now assert either
  directly instead of over-rendering flags into an argv no real call
  would produce. Probe records keep them empty; `.flags()` keywords
  stay part of the handle and show only in `argv`/`raw`.

### Changed

- **The README's minor-pin example can no longer go stale.**
  `fm tools.prepare-release` rolls the beta note's `toolroom~=X.Y.0`
  alongside the two version files on a minor bump, and a drift test
  fails the gate whenever the pin and the tree disagree.

## [0.6.0] — 2026-08-21

### Added

- **`toolroom.testing` — canned answers for bridge calls.** A test
  wraps the code under test in `with toolroom.testing.answers({...}) as
  calls:` and every bridge call is answered from the table instead of
  spawning: keys are argv prefixes (longest match wins), values are
  canned stdout, an exit code, or a full `Result`, and the yielded list
  records each call's argv, environment and run policy for assertions.
  The handles stay completely real — chaining, flag translation,
  redaction are the bridge's own — only execution is replaced, at the
  `_host` seam both lanes already pass through. A non-zero answer takes
  the failing lane honestly (`ToolError`, or footman's `RunFailed`
  under `answers(hosted=True)`, which simulates the hosted lane for
  code written to run in footman tasks). `installed_version()` is
  covered too: its spawn moved into the seam as `_host.probe()` —
  same semantics, still untouched by `--dry-run` and `recording()` —
  so a test can can a version line while the per-process cache is
  isolated for the block.

## [0.5.3] — 2026-08-20

### Changed

- **gh 2.98.0** adds `--worktree`.
- **Provisioned tools get a durable home.** With no `--prefix`,
  `fm tools.provision` now materialises into the `toolroom` room in
  footman's data directory (`~/.local/share/footman/toolroom`, moved
  wholesale by `FOOTMAN_DATA_DIR` or `XDG_DATA_HOME`) instead of a
  `.tools-latest` in the current directory — and every empty-prefix
  reading (`sync`, `audit`, the tier drives) looks there first when it
  has been provisioned, falling back to the host's PATH exactly as
  before when it hasn't. Provision once, and every later reading on the
  machine uses the isolated set. **Breaking** for anything that relied
  on the old `.tools-latest` default: pass `--prefix=.tools-latest` to
  keep it.

## [0.5.2] — 2026-08-20

### Changed

- **bun 1.4.0** adds `--asset`, `--catalog`, `--define`, `--disable-warning`, `--drop`, `--experimental-stream-iter`, `--extension-order`, `--feature`, `--filter`, `--heap-prof-interval`, `--ignore-dce-annotations`, `--insecure-http-parser`, `--interactive`, `--jsx-factory`, `--jsx-fragment`, `--jsx-import-source`, `--jsx-runtime`, `--jsx-side-effects`, `--loader`, `--main-fields`, `--no-macros`, `--no-warnings`, `--pending-deprecation`, `--preserve-symlinks`, `--preserve-symlinks-main`, `--react-compiler`, `--redirect-warnings`, `--timings`, `--tls-max-v1.2`, `--tls-max-v1.3`, `--tls-min-v1.0`, `--tls-min-v1.1`, `--tls-min-v1.2`, `--tls-min-v1.3`, `--trace-deprecation`, `--trace-warnings`, `--tsconfig-override`, `--update-timings` and `--watch-kill-signal`. It also rewords 8 descriptions and restates its own description.
- **claude 2.1.238** adds `--yes`, over 3 releases. It also rewords 1 description.
- **ssh 10.5p1** adds `-Z`. It also rewords 2 descriptions and restates its own description.
- **ssh_keygen 10.5p1** rewords 1 description. It also restates its own description.
- **ssh_keyscan 10.5p1** rewords 1 description. It also restates its own description.
- **uv 0.12.5** rewords 34 descriptions.

## [0.5.1] — 2026-08-16

### Fixed

- **A `Secret` passed through the bridge no longer prints in the clear.**
  A credential flag is exactly the shape a bridged call takes
  (`author=Secret("…")` → `--author …`), and the bridge `str()`-ed every
  value on its way to the argv — destroying the type marker footman's
  display-time redaction keys on, and printing the real value in the
  shown line, the step receipt, and the failure message. Three changes,
  none of which imports footman:

  - the execution path passes a `str` through *as its own type* —
    positionals, option values, and the attached `--flag=value` form all
    keep the subclass, so the marker reaches the argv (the child always
    received the real value; `subprocess` stringifies at the syscall
    boundary);
  - toolroom's shown line and the standalone `Result.command` (and with
    it `ToolError`'s message) render any `reveal`-carrying value as
    `***`, duck-typed so the bridge never learns who defined it;
  - a secret bound through `.flags()` redacts as the whole token, flag
    name included — the safe direction.

  `str(secret)`, f-strings, and `reveal()` still pass in the clear:
  the caller unwrapping is the caller choosing to expose, which is
  footman's documented contract. `.to_argv()` and a built `Argv` keep
  real values — they exist to be executed, not shown.

## [0.5.0] — 2026-08-15

### Added

- **`Flag`, `Value`, and `ValuedFlag` are public.** The three types every
  generated signature is written in were private aliases (`_Flag`, …) —
  internals-flavoured names in the one place users look, and
  unimportable for a wrapper that wanted to annotate its own parameters
  with exactly what a call accepts. They now live on `toolroom` directly,
  at runtime too, and every generated stub spells them bare.

### Changed

- **Signatures accept paths.** The bridge has always `str()`-ed what it
  was handed, so `ruff.check(Path("src"))` ran fine and only the types
  called it an error. Positionals are `str | PathLike[str]` now, and
  `Value` takes `PathLike` scalars and sequences alike — the annotations
  caught up with the runtime.
- **Hover signatures shrank to one line per flag.** An option with a
  closed set of values used to inline its `Literal` union twice over
  (`Literal[…] | Sequence[Literal[…]] | None` — ruff's `output_format`
  alone was eight hover lines); each distinct choice set is now a named
  module-level alias in its tool's stub (`OutputFormat`, `TargetVersion`),
  and checkers render the name. Generated classes derive from
  `ToolBase` — the same `Tool`, imported under a name that reads public
  in every class header a hover shows. A verb that would collide with a
  stub import (none does) makes the renderer refuse loudly rather than
  write a stub that means the wrong thing.
- **Flag help stays on one line per entry.** Docstring wraps chosen for
  the .pyi's column limit landed as hard breaks mid-sentence in every
  hover; entries are unwrapped now, so each renderer reflows them to its
  own width.

## [0.4.0] — 2026-08-09

### Changed

- **`.opts(color=)` now reaches a tool's environment hosted, not just its
  flags.** The mode travels to the executor unresolved — `auto` means
  "follow whoever owns the ambient", and inside a run that is the run —
  so footman's `run(color=)` applies it to that one child. Before, a
  per-call colour reached only the tools that take a switch; a tool that
  reads the environment followed the run-wide answer, because footman
  owns the child's environment inside a run. One keyword now reaches both
  halves on both lanes.
- The keyword arrived in a footman later than the floor toolroom names,
  so the bridge reads `run()`'s signature once per process and passes it
  when it is there. Neither package waits on the other's release, and an
  older footman still gets the switch half, with a `--verbose` note
  saying what a decided colour could not reach.
- A decided colour merges over an explicit `env=` rather than deferring
  to it, matching what `run(color=)` does hosted: an instruction aimed at
  one child outranks the environment it was handed. `auto` still leaves
  an explicit environment alone — ambient is what that environment
  already carries.

## [0.3.0] — 2026-08-09

### Added

- **`.opts(color="auto"|"always"|"never")`** — colour, decided per call.
  `"always"`/`"never"` settle that call outright; `"auto"` (the default)
  takes the ambient answer. An explicit choice beats `NO_COLOR`, the same
  way a `--color=always` does: an instruction aimed at one call is not
  the same kind of thing as an environment-wide preference, and it is
  what lets a colour assertion hold on CI that exports one. An unknown
  mode is a taught error.

  The decision travels on the call rather than through the environment
  because `os.environ` is one per process and calls are not — two threads
  wanting different answers can both have one this way. It drives
  whichever half of the forcing each tool needs, so a caller never has to
  know which half that is.

### Changed

- **Colour is toolroom's to arrange, from one bit.** The seam is now two
  environment variable names and nothing else: whoever decides — a
  footman run publishing its answer, a user exporting one, or toolroom
  reading the terminal — spells it `FORCE_COLOR` or `NO_COLOR`, and
  toolroom translates that into what each tool actually needs. Tools that
  read the environment get the whole force set written into the child;
  the few that ignore it get their own switch, as the colour probe found
  (git's `-c color.ui=always`). Forcing off clears any inherited force
  variable rather than setting `"0"`, which a tool reading mere presence
  would honour straight past `NO_COLOR`.
- Standalone, that closes a gap: a spawned tool's stdout is a pipe
  whenever toolroom captures it, and nothing was writing the force set,
  so everything but git and cspell came back monochrome even with colour
  asked for. Forcing over a pipe is what those variables are for.
- The ambient answer is read from the environment, hosted or not:
  `FORCE_COLOR`, `CLICOLOR_FORCE`, `CLICOLOR`, `NO_COLOR`, with
  `FORCE_COLOR` taken by truthiness — so an explicit `FORCE_COLOR=0` no
  longer forces — and a dumb terminal counted as no terminal. Inside a
  footman run that reads the answer the run published at its boundary, so
  nothing about the outcome changes; what goes is toolroom's last import
  of footman, and with it the wart where the same call coloured
  differently depending on whether footman happened to be imported.
- `recording(force_color=True)` no longer influences the argv toolroom
  builds — it never could standalone. Say `.opts(color="always")` on the
  call under test instead, which is the more honest spelling anyway: the
  assertion is about that call.
- An explicit `env=` still replaces rather than merges, colour included —
  the same word it is hosted.
- The footman dev floor moves to 0.36, whose removal of its own tools
  bridge finished the split. The repo's tests reached into
  `footman._toolspec` and `footman._drivers` for the drivers and the
  click reader; both have lived in `machinery/` since the split, and now
  they are read from there.

### Fixed

- A refresh now says *which* platforms skipped a tool, where they did not
  all skip it. The assembler folded three legs' skip lists into one and
  dropped who reported each line, so `git (no man to read the pages
  with)` — true only of Windows, which has no `man` — read in the PR as a
  tier nobody refreshes. git's pages had in fact been read twice over, on
  Linux and macOS, which is all a manual needs: the same bytes
  everywhere. A skip every leg reported still says nothing extra, because
  that one is a fact about the tool.

## [0.2.0] — 2026-08-09

### Added

- **claude** joins the curated tools — Claude Code, read from the
  `@anthropic-ai/claude-code` package on the node tier. The headless
  surface is the root call, so `tools.claude("explain this", print=True,
  output_format="json", model="sonnet")` types and completes; the stubbed
  verbs are the ones a task reaches for around it (`mcp.*`, `plugin.*`,
  `auth.*`, `agents`, `doctor`, `update`).
- **A Colour page**, listing how each curated tool is talked into (and
  out of) colour over a pipe. It renders from the checked-in colour data
  on every docs build, so it needs no tool on PATH and cannot drift from
  what ships — the rule the per-tool pages already follow. The table came
  over from footman, where it was left orphaned by the split: its data
  lives here, so its page does too.
- The docs resolve **footman's symbol inventory**, so a mention of
  `run()` or `Result` links straight into footman's API reference (and
  footman's site resolves toolroom's, the other way).

### Changed

- **coverage 7.15.4** rewords 1 description. It also restates its own description.
- **python 3.14.7** rewords 1 description.
- `fm tools.color` reads only what was provisioned. A verdict is a claim
  about a release, so it has to come from a release someone fetched: with
  a `--prefix` a tool the prefix does not carry is skipped rather than
  falling through to the host's copy, and without one the probe still
  prints its table but writes nothing. The write also *folds* into the
  checked-in data instead of replacing it — git and the other tools read
  from their manuals have no binary in any prefix, and a run that wrote
  only what it saw would have dropped git's `-c color.ui=always` and
  taken the bridge's colour forcing with it.

### Fixed

- An option a tool spells two ways now takes its keyword from the
  spelling Python is written in. Claude Code prints `--allowedTools,
  --allowed-tools`, and the first one won, so the stub suggested
  `allowedTools=`. Sameness folds case and dashes away before choosing,
  which is what keeps a *neighbour* from being mistaken for a dialect:
  markdownlint-cli2's `--configPointer, --config` are two different
  options its column alignment runs into one block, and git prints
  `--column[=<options>], --no-column` the same way.
- The stub generator no longer writes a line its own gate refuses. A
  summary with no `Args:` block under it is folded onto one line by the
  formatter, closing quotes and all, and the wrap left room for only the
  opening ones — so a summary landing in a two-character band came out at
  89. An alone summary is now wrapped for both.
- `fm tools.color --write` writes `src/toolroom/_colordata.py` again. It
  had been writing `src/_colordata.py` since the split — the module moved
  into the package and the path did not follow — so a re-probe wrote a
  file nothing imports and left the real data untouched.

## [0.1.1] — 2026-08-05

### Fixed

- `.opts(env=…)` standalone now means what it means hosted: the child's
  **whole** environment, never a merge over the parent's. The divergence
  was a portability trap between the two lanes, caught by a consumer's
  sweep.
- Host detection keys on **orchestration, not presence**: a call routes
  through footman only when a footman context is live (a task body, a
  `parallel()` worker, a `recording()` block). A bare call in a process
  that merely imported footman — a pytest run auto-loading footman's
  plugin, say — now takes the standalone lane deterministically, so which
  exception a failure raises never depends on what some other module
  imported. The same sweep caught this one.

## [0.1.0] — 2026-08-05

### Added

- The bridge, extracted from footman (`footman.tools` → `toolroom`) and
  reworked to stand alone: typed handles for command-line tools with
  mechanical keyword-to-flag translation, subcommand chaining,
  `.flags()` / `.opts()` / `.at()` / `.argv`, the `off` sentinel, and
  `installed_version()`.
- The seam (`toolroom._host`): host detection plus a plain subprocess
  executor. Standalone, calls answer in toolroom's own `Result` and
  failures raise `ToolError` unless `nofail`; with footman present in
  the process, the same calls route through footman's `run()` and
  inherit capture, replay-on-failure, dry-run, `recording()`, and
  `--json` receipts. Neither package depends on the other.
- The typed surface: a hand stub and generated per-tool stubs anchored
  on `toolroom`, with `Argv`, `Result`, and `ToolError` declared
  locally so type-checking toolroom never requires footman installed.

### Changed

- **djlint 1.44.0** adds `--allow-empty-input`.

## [0.0.1] — 2026-08-05

### Added

- Name reservation on PyPI: an empty typed module and the
  trusted-publishing release workflow. Nothing importable of substance.

[Unreleased]: https://github.com/willemkokke/toolroom/compare/v0.6.1...HEAD
[0.6.1]: https://github.com/willemkokke/toolroom/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/willemkokke/toolroom/compare/v0.5.3...v0.6.0
[0.5.3]: https://github.com/willemkokke/toolroom/compare/v0.5.2...v0.5.3
[0.5.2]: https://github.com/willemkokke/toolroom/compare/v0.5.1...v0.5.2
[0.5.1]: https://github.com/willemkokke/toolroom/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/willemkokke/toolroom/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/willemkokke/toolroom/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/willemkokke/toolroom/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/willemkokke/toolroom/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/willemkokke/toolroom/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/willemkokke/toolroom/compare/v0.0.1...v0.1.0
[0.0.1]: https://github.com/willemkokke/toolroom/releases/tag/v0.0.1
