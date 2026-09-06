# Cross-platform observation data for the tool option history

**Status: COMPLETE, and far superseded. This is pre-split history.**

Written 2026-07-27, when the tool-history machinery still lived in
footman (`src/footman/_toolhistory.py`, `_toolspec.py`, `_stubgen.py`,
`tasks/tools.py`, `_provision.py`) — every path it names moved here in
footman's 0.32.0 split, and the surface has moved on considerably since.
Its core ideas did land: `not_on` on `Option`/`Verb` in
`src/machinery/_toolspec.py`, and the absence sidecar as `absent_at()` in
`src/machinery/_toolhistory.py`.

Kept for the reasoning — why a same-version re-read must merge rather
than overwrite, and what "this flag does not exist on Windows" has to be
able to prove before a stub may say it. Read the current modules for how
it actually works; read this for why it works that way.

---

# Cross-platform observation data for the tool option history

## Context

The parallel gather (#132) observes on one platform per run; nothing compares
platforms, and a same-version re-read overwrites the surface. Willem's model,
done properly:

1. An observation comes from ONE platform; every event in it shares that.
2. Observations MERGE into the per-tool JSON (never overwrite).
3. Per version, the store knows which platforms have data (`entry.platforms`,
   already stored — kept).
4. With data from ≥2 platforms, an option missing on one gets `not_on: [...]`
   — exceptions only. A later version observed on that platform showing the
   option clears it: **nothing means cross-platform**.

An adversarial design review (11 findings) reshaped the first draft; the
algebra below incorporates every accepted repair.

## The algebra (post-review)

**The governing rule — store observed facts, derive claims** (the store's own
philosophy: since/until are never stored). `not_on` on an option/verb records
ONLY platforms that observed THAT version and lacked it: invariant
**`not_on ⊆ entry.platforms`**, enforced in tests. Carry-forward ("Windows
lacked it at V, nothing newer known") and clearing ("Windows saw it at X")
are **derived at union() time** by walking each platform's latest verdict —
never written into younger entries. This deletes the stale-tag and
false-drop failure classes instead of patching them (review F1/F5).

**Storage (revised for compactness, Willem's challenge): a per-entry
sidecar, not in-option tags.** Observed absence is a fact about the
*observation*, so it lives beside `date`/`platforms`/`extractor`:
`"absent": {"serve\t--open": ["Windows"]}` — flat `verb\toption` keys
(delta-key style; a bare `"serve\t"` tags the whole verb), written only when
non-empty, invariant `absent-platforms ⊆ entry.platforms`. Wins over
in-option tags: deltas never carry tags (a tag flip touches one small dict
in one entry — no whole-option `revert` duplication, no delta recompute);
F2 (verb replay corruption) and F6 (tag transitions minting events) become
structural non-issues instead of repairs; observed-only is enforced by
where the data lives. Precedence: verb wholly absent on q → verb key only,
its options untouched.

**New version W from platform q** (predecessor V via chain, holes included):
- o in obs(q): in W's surface as read; no sidecar entry.
- o in V, absent in obs(q): if q's latest derived verdict on o was *absent*
  → expected absence: o stays in W's surface (fields carried from V) and
  W's sidecar records `absent[o] += q` — so the V→W delta is empty for o
  and no false drop/`until`/changelog line is minted (F1). If q *had* o (or
  never ruled) → **global drop**, today's semantics — self-correcting: a
  later platform's merge resurrects it with `absent[o] = the observers
  that lacked it`.

**Merging q into already-observed W** (observers Q) — `_toolhistory.merge()`:
- in both: keep; `absent[o] −= {q}`; field divergence resolved by explicit
  priority `("Linux", "macOS", "Windows")` — provenance is *derived* (the
  highest-priority member of `Q − absent[o]`), `q == provenance` may
  overwrite (fresher reading), so the result is order-independent (F8).
  The priority exists purely as an anti-churn tie-break: without a fixed,
  order-independent pick, alternating legs would flip divergent help text
  weekly, each flip a `revert` delta in a store whose gate is "did anything
  change". Never `sorted()` for priority (ASCII puts macOS last).
- in stored, not in obs(q): `absent[o] += {q}` (sidecar only — no delta).
- in obs(q), not in stored: add to surface with **`absent[o] = Q − {q}`**
  (F4).
- entry.platforms = Q ∪ {q}; q ∈ Q = re-observation, same rules.
- Locality (verified by review probe 6): W's surface is referenced by
  exactly `deltas[W]` and `deltas[next-older]` — a merge that changed the
  *surface* recomputes those two (base: `base.surface` + first delta); a
  merge that only touched the sidecar recomputes **nothing**. A sibling
  locality test beside the insert one (F11b).

**`_observe`'s same-version branch becomes `merge()`** (F3): the current
overwrite never recomputes the first delta — a latent replay bug today, and
under this design a single maintainer `fm tools.sync` on macOS would erase a
matrix's worth of tags while `platforms` kept claiming them. Highest-risk
existing code touched; regression tests around the release-runbook sync.

**Events stay honest for free** (F6, structural): the sidecar never enters
delta payloads, so `_events_of` and the changelog cannot see platform
coverage — the release gate fires only when the tool itself changed. No
strip-before-compare logic anywhere.

**union() derivations** (the implementation meat) — per option, per
platform, the latest verdict walking the chain newest-first over entries
that platform observed: in surface and not in `absent` → present; in
`absent` (or the whole surface lacks it) → absent; never observed → no
verdict, which renders as nothing. Then:
- rendered `Option.not_on`/`Verb.not_on` (new `_toolspec` fields, populated
  only at union time) = platforms whose latest verdict is absent.
- `since` suppressed when the first-appearance entry's observers could not
  have seen it earlier — the per-platform floor rule (F7), same honesty as
  "at or before the floor is not a since".
- `until` only when the drop is corroborated by a platform that last held
  the option (F10).
- Stub header names every platform of the base entry ("Read from X 1.2.3 on
  Linux, macOS and Windows"); decide format before the AST-equality tests
  meet it (F11a).

## The distributed workflow

- **`tools.gather`** (new): list + observe on THIS platform → an observation
  document (no store writes). Includes the **base backfill**: observe each
  tool's base when this platform ∉ `base.platforms` — current-version
  coverage converges in one matrix run.
- **`tools.assemble <files...>`** (new): group by (tool, version), fold
  platform surfaces via merge(), insert oldest-first, stubs, changelog,
  release decision. One process, one owner per file — git is not a merge
  engine (F9). `tools.refresh` = gather + assemble in-process (local case
  unchanged).
- **`refresh.yml`**: matrix legs ubuntu + macos + **windows from day one**
  (Willem) — each: provision → gather → upload artifact; one assemble job
  downloads all, assembles, pushes, opens the PR **and enables auto-merge**
  (`gh pr merge --auto --merge`) — Willem: the weekly PRs merge themselves
  once the gate passes; the gate (replay verify, stub AST tests, drift
  tests) is the reviewer. PR opens on ANY tree change; `release` governs
  only the changelog/bump claim. Any leg failure fails the workflow; exit 75
  semantics per leg. Windows risk: provision's python tier symlink needs a
  copy fallback on Windows; dispatch-verify each leg before cron is trusted.
- **PAT answer (Willem asked)**: auto-merge needs **no new permissions** —
  the already-specified fine-grained PAT (contents: write, pull-requests:
  write) covers opening, enabling auto-merge, and the merge itself. One
  repo setting to flip once: **Settings → General → "Allow auto-merge"**.
  Future auto-release also fits the same PAT (tag pushes ride contents:
  write, and a PAT-pushed `v*` tag DOES trigger release.yml, unlike
  GITHUB_TOKEN); the only possible extra gate is the `pypi` environment's
  protection rules.
- **Auto-release: BUILT, default off** (Willem). Toggle: repo variable
  `vars.AUTO_RELEASE` (settable in the UI without touching the workflow).
  When on AND the assemble warranted a release: a new `release.prepare`
  task computes the **patch** bump (decision 9: stub-only release = patch),
  bumps `pyproject.toml` + `__init__.__version__` + the `docs/json.md`
  `--version` example (drift-tested; the `footman~=X.Y.0` README/index pins
  don't move on a patch), rolls `[Unreleased]` → `[X.Y.Z]` with compare
  links — folded into the SAME refresh PR, so it is the release PR the
  runbook describes. After auto-merge completes (the job polls the PR
  state), the job tags the merged commit `vX.Y.Z` and pushes the tag with
  the PAT — a PAT-pushed tag triggers the existing `release.yml`
  (verify-version, build, publish to PyPI) exactly as a manual one does.
  Toggle off: the refresh PR keeps its entries under `[Unreleased]`, as
  now. No extra PAT permissions either way.

## Files to modify

- `src/footman/_toolhistory.py` — `merge()`; the `absent` sidecar
  (read/write helpers, `⊆ platforms` invariant); Rule-A expected-absence
  carry on the insert path; union verdict walk + since/until suppression.
  Surfaces and deltas untouched.
- `src/footman/_toolspec.py` — `Option.not_on`, `Verb.not_on` (render-time
  only).
- `src/footman/_stubgen.py` — "Not available on …" clause; multi-platform
  header.
- `src/footman/tasks/tools.py` — `gather`/`assemble`; refresh = both;
  `_observe` same-version → merge(); observation-document shape;
  `release.prepare` (version/json.md/CHANGELOG roll, patch bump).
- `src/footman/_provision.py` — python-tier copy fallback for Windows.
- `.github/workflows/refresh.yml` — matrix + assemble + auto-merge.
- `tests/test_toolhistory.py` — the algebra rule by rule; F1/F4/F5/F7/F10
  scenarios as named tests; verb-tag round-trip inside the replay tests
  (F11c); merge locality sibling test; not_on ⊆ platforms invariant over
  the checked-in store.
- `notes/20260726-tool-option-history.md` (decision 7 → the algebra),
  `CHANGELOG.md`.

## Verification — local machines first (Willem)

The real platforms come before CI. So the observation document is a
**portable artifact**, not a CI internal: self-describing (schema,
platform, extractor, per-tool version+date+surface), written by
`fm tools.gather --out=<path>`, copied off the machine by hand, and folded
here with `fm tools.assemble a.json b.json c.json`. Windows and Linux are
where the first real divergences will appear, and priming there also
deepens those platforms' coverage below the base.

1. `uv run fm check` throughout.
2. Algebra tests; replay-integrity suite extended to merges.
3. This Mac: `gather` → `assemble` → confirm a no-op against the
   macOS-seeded store (the same-platform re-observation path).
4. **Local Windows + Linux**: `fm tools.provision` then
   `fm tools.gather --out=obs-<platform>.json` (and `tools.prime` there to
   deepen), copy back, assemble all three → inspect the tags, the stub
   text, and that the release gate stayed quiet on coverage-only changes.
   This is also the first real exercise of provision on Windows (python
   tier shim).
5. Only then `workflow_dispatch` the matrix on a branch: three artifacts,
   assembled PR, auto-merge armed — inspected before cron ever fires.

## Decisions on record

1. Three platforms from day one (Willem).
2. Weekly PRs auto-merge; gate is the reviewer; repo "Allow auto-merge"
   setting to flip; no extra PAT permissions (Willem asked — answered).
3. Auto-release built, `vars.AUTO_RELEASE` default off (Willem); same PAT.
4. Store observed absences only, as a per-entry `absent` sidecar — the
   compact shape (Willem's challenge); presence claims and carry-forward
   are derived at union time (review F1/F5).
5. Field priority Linux > macOS > Windows via derived provenance — an
   anti-churn tie-break for divergent prose, nothing more (F8; Willem asked
   — answered).
