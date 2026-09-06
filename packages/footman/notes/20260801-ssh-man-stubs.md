# tools.ssh / tools.ssh_keygen from the man-page provider

Status: BUILT 2026-08-01 on branch `worktree-ssh-man-stubs` (four
commits, gate + strict docs green), awaiting review/merge. The build
answered the plan's open calls: the parser fix shipped as the re-aimed
strict guard with **no new knob** (ssh runs on the default
`shorts="only"`; the git fixtures stayed clean — "we'll find out"
answered: no noise); the fetch descriptor landed **on `Provision`**
(`Manual`: index/archive/listing/pages). Still open: whether
`ssh-keyscan` joins as a third driver. Found-while-building, beyond the
plan: `_man_tier` replaced the shared tree per driver (a second man
tool erased the first's pages — fixed to per-driver staging + merge);
the `_VERSION` grammar read *LibreSSL's* number as ssh's (glued `pN`
tail added); ssh-keygen has no version output at all
(`Driver.version_of` names ssh as the sibling that answers); mdoc
pages state no version (installer stamps `VERSION-<tool>` per tool,
git's `.TH` stays as fallback); pyrefly's ignore-file/hidden-dir
handling failed the whole gate in any `.claude/worktrees/` checkout
(pinned off — includes are the whole scope).

Below is the plan as it stood before the build. Decisions marked
**open** were Willem's calls at the time.
Origin: hse's post-0.28 audit of raw execution sites — `tools.ssh`
converts their remote-docs site, and `ssh-keygen` rides the same lane.
Related but separate threads: `run(input=)` (the stdin gap), and the
namespace-package split
([20260801-tools-namespace-package.md](20260801-tools-namespace-package.md))
— the stub machinery is dev-side in every variant of that plan, so this
work is unaffected by it.

## The ask

Bake `tools.ssh` and `tools.ssh_keygen`, with stubs generated from
OpenSSH's manual pages via the `kind="man"` provider — the lane git
already uses: fetch a release's pages, render with `man -M`, parse the
rendered text. Nothing installed, nothing run, host never read.

## Why the man provider fits

- OpenSSH has no `--help`; its authoritative option surface is ssh(1) /
  ssh-keygen(1), published per release.
- The portable-release index is the same shape as git's kernel.org one:
  `https://cdn.openbsd.org/pub/OpenBSD/OpenSSH/portable/` is a plain
  href listing (`openssh-9.9p2.tar.gz`, …) — `_manpages_index()`'s
  regex-over-listing approach ports directly.
- The release tarball carries `ssh.1` / `ssh-keygen.1` at its root
  (~1.9 MB download); install extracts just those into `man/man1/`.
- Rendering already works: `_render_page` runs the system `man -M`,
  which renders mdoc as happily as troff, and `_OVERSTRIKE` stripping,
  page naming, the no-`man`-on-Windows skip, and the `FOOTMAN_MANPATH`
  overlay are all shared machinery.

## What the probe measured (macOS system pages, real parser)

Three runs of `parse_help(man=True)` over the rendered pages:

- **As-is**: ssh → **0 options** (man mode is `strict`, and
  `_option` line 360 refuses to key on a short-only flag under
  strict — every ssh option is short-only). ssh-keygen → **5 junk
  options**: `-Y sign`-style word-arguments misread as Go-style
  single-dash longs (`-principals`, `-novalidate`, `-sk`). So the
  current lane is not merely incomplete for OpenSSH pages — it
  fabricates.
- **Shorts trusted** (one-line policy change, simulated): ssh → 42
  options, ssh-keygen → 45 — the full alphabet, but arity wrong
  (nearly all read as switches, because strict also drops the bare
  mdoc metavar: `-B bind_interface` renders as two bare words).
- **Shorts trusted + bare metavar read**: arity essentially right —
  `B=str`, `i=str`, `o=list[str]`, `t=bool`, `D=list[str]`. This is
  the quality bar the stubs need.

Known parser defects the probe surfaced (all real page text, not
edge-case speculation):

1. **`-p` is silently dropped.** mandoc renders `-p port` with no
   blank line after `-P tag`'s paragraph (uniquely — its neighbours
   all get one). Man-mode `_blocks` requires a paragraph start, so the
   line terminates `-P`'s block and then vanishes. Fix: in man mode a
   line *at the established flag indent* that parses as an option head
   starts a block even without a preceding blank.
2. **Multi-form heads lose their help.** `-L` states four complete
   forms on consecutive flag-indent lines before the description;
   consecutive same-flag heads must merge into one option.
3. **`-W` misjoins with `-T`** — to diagnose at build time; likely the
   same adjacency family as (1)/(2).
4. The junk from word-arguments (as-is run) means the Go-long fallback
   (`_option` line 359) must not fire for these pages once shorts are
   trusted — verify the short path wins.
5. `-4`/`-6` are unspellable as kwargs (digits) and correctly dropped;
   positional passthrough covers them at call sites.

## Design

### Parser fix — re-aim the strict guard, no new knob

First lean (superseded): make `shorts="all"` meaningful under man.
Willem's question — *why doesn't `shorts="only"` work when there are
only short options?* — exposed that as the wrong knob. `"only"`
already describes ssh exactly ("key on a short when it is the option's
only spelling"); what blocks it is `_option`'s `not strict` clause
(line 360): man mode always passes `strict=True`, and strict vetoes
short keying *unconditionally*, whatever `shorts` says.

And the veto is misaimed in both directions, measurably: it blocks the
trustworthy single-letter path while the actually-noisy reader — the
Go-style multi-char single-dash fallback (line 355-359) — runs
unguarded in man mode. That fallback is what fabricated ssh-keygen's
junk, helped by `_FLAG` having no left word-boundary: in the head
`-Y find-principals` it matches `-principals` mid-word, and the
fallback promotes it to a keyword.

Corrected design, still **open** for the call:

- `strict` stops vetoing the shorts policy — the `shorts` field alone
  governs short keying, from `--help` and manual alike. ssh and
  ssh-keygen then work with the **default** `shorts="only"`; no driver
  sets anything.
- What strict suppresses instead is the Go-long fallback — a manual
  never spells Go-style longs, and that path is the one the probe
  caught fabricating.
- `_FLAG` gains a left boundary (start-of-string or whitespace) so a
  mid-word dash never spells a flag, in any mode.
- Risk to verify in phase 1: git manuals may now key genuine
  short-only options — a surface *gain* if the blocks are clean, junk
  if prose leaks through. The git fixture walk decides; if it sprouts
  noise, fall back to a driver-gated trust flag rather than
  reinstating the blanket veto.

### Fetch source — design the lane, not a second special case

`kind="man"` is currently git-shaped (`_MANPAGES_INDEX` hardcoded).
Adding an ssh twin would make two special cases; instead the man tier
gets a **source descriptor on the driver** (index URL, filename regex,
member names to extract), and git's kernel.org fetch becomes the first
instance. **Open**: descriptor on `Provision` vs a new `kind` per
source (lean: descriptor — one tier, N manuals).

OpenSSH specifics:

- index regex `openssh-(?P<version>\d+\.\d+p\d+)\.tar\.gz`;
- `version_tuple("9.9p1")` reads `(9, 9)` — patchlevels of one base
  compare equal by design, so the walk's sort key extends with the
  numeric p-suffix (the release-chain date tiebreak precedent);
- extract only `openssh-*/ssh.1` and `ssh-keygen.1` into `man1/`,
  with the same safe-extraction discipline the other tiers use.

### Drivers

```python
Driver("ssh", provision=Provision(kind="man", ...source...),
       man=True, url="https://man.openbsd.org/ssh.1"),
Driver("ssh-keygen", attr="ssh_keygen", provision=..., man=True,
       url="https://man.openbsd.org/ssh-keygen.1"),
# no shorts= — the default "only" is exactly ssh's shape once the
# strict veto is re-aimed (see the parser section)
```

Both verb-less — the walk reads one page each (`ssh`, `ssh-keygen`),
no verb fan-out. Version of the *installed* tool: `ssh -V` prints to
**stderr** — verify `read_version` reads it (build-time check).

### tools.py

- `ssh = Tool("ssh")`, `ssh_keygen = Tool("ssh-keygen")` + `tools.pyi`
  parity lines (AST test enforces).
- `_WRAPPERS` gains a bare-ssh entry: ssh forwards everything after
  `destination` to the remote, so this call's flags must precede the
  positionals — the wrapper placement rule, and it is required for
  correctness, not cosmetics. ssh-keygen is not a wrapper.
- No `_colordata` entries — ssh has no colour lane.
- The remote command stays a positional (shell string when it needs a
  shell): the transport is the tool, the remote command is payload.
  The bridge's job ends at ssh's own argv.

### Stub fallout

- Generated kwargs include `l`, `I`, `O` — ruff E741 (ambiguous names)
  will fire on stub parameters; add `per-file-ignores` for
  `src/footman/_stubs/*.pyi` (section already exists in pyproject).
  Verify at build whether ruff actually lints the generated stubs
  before adding.
- Case-paired options (`-B`/`-b`, `-P`/`-p`, `-O`/`-o`) are distinct
  kwargs — Python keywords are case-sensitive; nothing to do, but the
  docs example should show one to pre-empt "is `B` a typo" reports.

### History / refresh

`tool-history/` gains ssh and ssh-keygen event streams like any other
tool. The pages are platform-free (git precedent: "the same bytes
everywhere"), and the Windows gather already skips man-kind drivers
when `man` is absent (`tasks/tools.py:1667`).

## Out of scope

- `ssh-keyscan` — hse's probe site uses it, and the page is tiny.
  **Open**: include as a third driver now, or leave the probe site raw
  (it is `recorded=False` list-argv already, arguably fine).
- `scp` / `sftp` — same lane, no known demand; not now.
- `run(input=)` — separate thread, separate build.
- Anything that makes the *remote* command structured.

## Build order (each gate-green, mergeable alone)

1. Parser: the `shorts="all"`-under-man policy + the three block/head
   fixes, driven by tests using captured rendered-page fixtures (the
   `-P`/`-p` adjacency, the `-L` multi-form, a keygen `-Y` word-arg).
2. Fetch: the man-source descriptor refactor (git unchanged
   behaviourally, its fixture walk green), then the OpenSSH source.
3. Bake: tools.py + pyi + `_WRAPPERS` + drivers; run
   `tools.provision` → `audit` → `sync` by hand; first stubs land.
4. Docs (tools-bridge page: ssh example with the wrapper placement and
   an `o=(...)` repeated-flag call) + CHANGELOG.
