# The workshop: instantiation, layers, and inheritance

Status: approved 2026-09-03 after Willem's review; executing.
Subsumes
`notes/20260901-workshop-plan.md`, whose phases 1 to 9 shipped and
whose shipped record, movement analysis, and options registry stay
there; its contracts are carried forward here, and its unshipped tail
(phases 10 to 13) is carried forward renumbered 17 to 20. On approval
the 0901 note gains its pointer line.

The re-plan's reason (Willem, 2026-09-03): the previous plan was
right in content but feature-first in order. Features were landing in
the base while the seams a derived ecosystem needs (recursive
creation, layer overlays, composed delivery) stayed design. Every
feature added before those seams widened the gap the hse bootstrap
has to cross. This plan inverts the order: before 0.1.0, the entire
end-to-end instantiation and customisation story is completely
defined and mostly implemented, proven by a dummy hse-derived
project, and only then do features resume, flowing to descendants
through the seams instead of piling up behind them.

The model in one sentence: every layer's home repository is that
layer's first instance, and creation is recursive. livery hosts the
base layer and creates clean instances; a clean instance created
with a new layer becomes that layer's home; the branded App of a
layer home creates children that carry its stack by default; and a
child may add one more layer and become a home in turn. livery to
hse stops being a special arrangement and becomes the mechanism,
applied once.

This plan is judged against `notes/20260830-development-workflows.md`
(W1 above all) and against the goal: anybody who bases their project
setup on the workshop gets every core improvement (new package
types, language toolchains, workflow verbs) without losing their own
branding and customisation.

## Ground-truth contracts (do not violate)

Contracts 1 to 17 carry forward from the 0901 plan unchanged in
meaning. Their titles are restated here; the 0901 note stays the
home of the full bodies (contract 4's exit-code rule, contract 9's
receipt ordering, contract 17's offline gate-compare, and the
rest), the way contract 11 below points at its movement analysis.
A trimmed clause is not a repealed one.

1. **hse is the first working reference, not sacred.** Before
   building any area hse covers, its implementation is read (not
   recalled) and its shape and guards ported. Improvements are
   named, justified, and ruled in the decision record before they
   are built. A compare-and-contrast against hse closes every phase.
2. **Everything through fm.** A needed exception marks a missing
   verb.
3. **Fallbacks before happy paths.** Every phase ships an edge table
   (edge, guard, test) and every guard has a test that forces it.
4. **Errors teach.** A refusal names the thing that stopped it, then
   lists every available option and when each applies. A parked
   green outcome exits 0.
5. **Idempotency everywhere.** Re-running any verb is its recovery
   procedure.
6. **Human and agent friendly in the same breath.** Completion
   through footman's suggest machinery wherever values enumerate.
7. **Gitea and private repositories are tier 1.** Every
   forge-touching behaviour is exercised against the local
   containers before it ships.
8. **Rendered contracts belong to the template channel.** The render
   gate judges what the template keeps owning; a package's seeds are
   its authors' and are never rewritten.
9. **Per-package tags are the release identity and its receipt.**
10. **No committed monorepo-level artifact serialises releases.**
11. **Main never locks.** The movement analysis stands in the 0901
    note.
12. **Template updates are workspace-atomic.** One template source
    at one version per workspace. In the monorepo the source lives
    at HEAD; the update workflow exists for instances.
13. **Reading is `status`, acting is `ci`.**
14. **One mechanism per act.**
15. **No dev wheels to PyPI, ever.**
16. **A design fork gets an explicit ruling before it is built.**
17. **Generate over template.** Forge-dependent mechanical content
    is emitted from data as managed generated artifacts; templates
    carry only taste surfaces and seeds. Minimising the reasons for
    a template update is an explicit goal.

New contracts, from the 2026-09-03 re-plan:

18. **The core is identity-free.** No instance identity, livery's
    included, appears in any template byte, content fragment, or
    emitter. Identity comes from the answers and the running App.
    The monorepo is an instance like any other, and a conformance
    render of a foreign identity gates that this stays true.
19. **Every layer's home is that layer's first instance.** Creation
    is recursive: a workspace's create verb makes children whose
    default layer stack is its own, and a child created with one
    added layer becomes that layer's home, consuming it at HEAD the
    way livery consumes `livery.workshop` at HEAD.
20. **Improvements flow down the gradient.** A core improvement must
    reach every descendant through a wheel bump or a template
    update. Extension mechanisms are preferred in the order:
    generated from the contract, then unioned registries (verbs,
    tool profiles, content, package types), then extend-stubs, then
    template seeds, then overlay wholesale replace. A wholesale
    replace ends inheritance for that file, so it is narrow and
    carries a reason, like a type suppression.
21. **A layer home publishes its composed template artifact.** Its
    release composes the base templates at the pinned version with
    its own overlay and publishes the result to the home's artifact
    repository. A child anchors `_src_path` to exactly one artifact
    repository with full history; no child ever composes at update
    time.
22. **The proof chain gates the core.** Once phase 16 lands, the
    dummy descendant's end-to-end suite (create, customise, inherit)
    is part of the workshop's acceptance; a core change that breaks
    a descendant's update flow is red before it merges.
23. **Identity is answered, configuration is declared, environment
    is cascaded.** Identity (who this is) is fixed at birth in the
    answers. Contract facts (what must be identical for every clone
    and every CI run: layers, owners, approvals, required context,
    forge kind) live in the contract file (`workshop.toml` once
    phase 10 renames it), reviewed config-as-code and
    generation input, never overridable. Environment facts (what
    legitimately varies by person, machine, or deployment:
    endpoints, indexes, hosts, mounts, tokens) live in the env
    cascade, where override is the point. The test is
    overridability: nothing reviewed may be overridable, nothing
    per-deployment may need review. No configuration fact is ever a
    copier question; the answers file's non-identity content is
    machine-managed provenance reconciled from the contract.
24. **One forge per instance, spoken abstractly.** The contract
    declares exactly one forge; moving forge is a deliberate
    contract edit, re-render, and reconfigure, never dual
    operation. The token surface is `FORGE_TOKEN` and
    `FORGE_ADMIN_TOKEN`, the only names verbs, teachings, docs,
    and CI speak; everything per-forge (ambient job tokens, secret
    APIs, dialects) lives in the backends. Everyday verbs never
    read the admin name. The forge is a birth-time fact of each
    instance, never inherited from the parent stack: a derivation
    may live on a different forge than its parent, and a layer
    home's artifact repository lives on the home's own forge.
25. **Provenance rides the file, written by the machinery.** Every
    rendered, generated, assembled, or shipped file that supports
    comments opens with a provenance header naming its channel,
    its source, and the edit path: edit here, or edit the named
    source and re-run the named verb. Nobody types one: the render
    and the emitters inject headers into what they write, and
    shipped layer content gets its header from the provenance
    lint, whose `--fix` writes the computed text. Nothing is ever
    injected into a materialised copy: symlink materialisation and
    override detection both depend on the copy staying
    byte-identical to its source. Files that cannot carry
    comments, and overrides, are `fm explain`'s territory alone.
    Headers state the current contract in the present tense, no
    versions and no dates.

## The inheritance gradient

Contract 20's reasoning, kept short. "Access to improvements without
losing customisation" holds or fails per delivery mechanism:

- **Generated from the contract** (CI workflows, CODEOWNERS): a
  wheel bump re-emits; nothing a layer owns is touched. Full
  inheritance.
- **Unioned registries** (verbs by entry point, tool profiles over
  types and layers, content merged in layer order, package types
  once the backend registry opens): additive by construction; a new
  core toolchain appears in every descendant on the next bump. Full
  inheritance.
- **Extend-stubs** (a thin committed file pointing into the wheel's
  shared config, hse's ruff shape): the repo holds identity, the
  wheel holds the toolchain. Near-full inheritance.
- **Template seeds** updated by copier: three-way merge, occasional
  conflicts, improvements flow.
- **Overlay wholesale replace**: inheritance ends for that file
  until a human re-derives it. The escape hatch, not a pattern.

Every fact moved up this list is an improvement descendants get for
free. This is why contract 17 exists and why the core's templates
stay near-empty.

## Template composition: the two axes

Carried forward from the 0901 plan, with the build schedule changed:

- **Vertical, layer overlays: built now, phase 14.** The template
  source becomes a stack mirroring the workspace's layers list,
  rendered bottom to top into one destination; an upper layer adds
  files to any kind or replaces a base file wholesale, never edits
  one. The answers file records the stack and each layer's answers,
  or the update wave could not re-apply the render. Overlays apply
  to every template file, seeds included: replacing a seed changes
  what is born and nothing that lives, so its inheritance forfeit
  is confined to future births, while replacing a managed file also
  forfeits the base improvements that would have flowed to existing
  instances. Both carry the declared reason, contract 20; the seed
  case is the cheap, ordinary customisation (a brand's own README
  or LICENSE seed), the managed case is the one to justify.
- **Horizontal, kind hierarchy: still deferred, phase 17.** A kind
  declares a parent; `PACKAGE_MANAGED` is the union along the
  chain. The layer scaffold in phase 14 needs only a flat new kind,
  not the chain. The backend registry opens with the first
  non-python kind, also phase 17, and from that day a layer can
  contribute a package type through the same seam.
- **Types contribute tool profiles.** Unchanged: the required tool
  set is the union over present package types and layers.

## Phase 10: the agnostic core

Contract 18 made true and kept true. Pure debt; blocks nothing and
is blocked by nothing, so it lands first.

Deliverables:

- `templates/project/pyproject.toml.jinja` loses every livery
  literal: the coverage source list and the mypy override module
  derive from the namespace answer; the dev group lists the
  distribution of every layer in the layers answer (the base
  layer's wheel included, so a rendered `tasks.py` can always
  import its plugin) beside the per-package dev entries, a layer
  that is also a workspace member listed once with the roster's
  spelling (its extras kept). A test pins the layers answer and
  the dev group in agreement. How a layers entry (an import path)
  names its distribution is open item 8, ruled before this
  builds. `tasks.py.jinja` derives its `plugin(...)` lines from
  the layers answer and de-brands its docstring;
  `CLAUDE.md.jinja` drops its hardcoded fragment imports the same
  way, or leaves the file to `fm sync` entirely, ruled at the
  cut.
- `.claude/settings.json` becomes layer content, materialised by
  `fm sync` beside the hooks it wires, with the standard
  deliberate-override behaviour. The monorepo's own copy converts
  to the materialised one in the same change. It is a single file
  at the `.claude/` root, a new shape for the materialiser (the
  self-scoped gitignore covers subdirectories today), and it
  materialises copy-first, never as a link: settings editors
  write the file in place, and a write through a link would land
  in the wheel's installed copy.
- `.repo.env.local` joins the template's `.gitignore` and the
  monorepo's in this phase: the engine documents the local rung
  as ignored, nothing ignores it today, and phase 12 makes that
  rung the token store.
- The product's names replace the monorepo's in every
  instance-visible spelling (open item 11, ruled 2026-09-03):
  `livery.toml` becomes `workshop.toml` at the root and per
  package, in the templates and in discovery; the materialise
  manifest becomes `.workshop-materialised`; the base fragment
  becomes `CLAUDE.workshop.md` with a de-branded title. The
  distribution and import names (`livery-workshop`,
  `livery.workshop`, sibling layers) stay: a dependency's name is
  not instance identity. The conformance test's exemption list is
  exactly those names.
- The stranger conformance test: render `kind=project` with a
  foreign identity (a name and namespace that are not livery) into
  a scratch workspace, then drive it: `uv lock`, `uv sync`,
  `fm sync`, `fm template.check`, `fm check`. Runtime decides its
  home at the cut: the gate if it fits, otherwise the nightly with
  a lock-and-import subset in the gate.
- The verb family is spelled once: `new.project`, `new.package`
  (ruled 2026-09-03), and
  `notes/20260830-development-workflows.md` drops its `create.*`
  spelling in the same change.
- The configuration questions retire per contract 23: `forge_kind`,
  `forge_owner`, `forge_url`, `runners`, `required_context`,
  `owner_users`, `owner_teams`, `owner_approvals`, `publish_index`,
  `layers`, `python_versions`, and `templates_source` leave the
  questions. Contract facts stay in the contract file (renamed
  `workshop.toml` this phase), which becomes a
  seed the birth verb fills and template updates never rewrite, so
  the phase 8 governance loop and the template channel stop
  competing for it. Environment facts move to the committed
  `.repo.env`, `publish_index` among them: the emitters stop
  baking it into job env, and the cascade's pre-tasks hook
  supplies it at run time, which holds because every consuming
  step runs through fm. Python versions derive from
  `requires-python`; the bound-to-matrix rule is open item 9,
  ruled at this cut. The
  layers answer's template role is embodied by the composed source
  (contract 21), and a lint checks the contract's list against the
  rendered stack. The answers file reduces to identity plus
  machine-managed receipts (`_src_path`, the `packages` roster)
  reconciled from the contract, never hand-edited.
- The provenance classifier, one function (contract 14): for any
  path, the channel, the owning layer or template or contract, the
  source location, and the edit path, read from what the workshop
  already tracks (the materialise manifests, the managed and
  generated lists, the template trees, the answers roster).
  `fm explain <path>` (ruled 2026-09-03) prints it, completing on
  tracked paths; the header writers below serialise the same
  answer. A workspace test pins that every tracked file
  classifies, with "yours" the honest default. Comment-hostile
  files (`settings.json`, the JSON configs) and detected overrides
  are explain's territory alone.
- Headers are machine-written, contract 25, never typed: the
  render injects them into every rendered file (seed and managed
  wordings differ, and the injector knows the winning source, so
  overlay provenance later costs nothing), the emitters open every
  generated file the same way, and the update wave refreshes
  injected headers after copier's merge, so copier preserves them
  as instance lines and a moved source cannot leave a stale header
  behind. Syntax follows the file type (`#`, `<!-- -->`, none for
  LICENSE and friends, HTML comments in markdown ruled fine);
  a header lands after a shebang or frontmatter.
- The provenance lint, for the one channel injection cannot serve:
  every file under a layer's `content/` opens with its computed
  header, red in `fm check`, written by `fm check --fix`, so a
  layer author never composes one. The phase 14 scaffold seeds its
  content examples with headers already present, so the convention
  is visible from birth and the lint stays the guarantee, not the
  teacher.

Edge table:

| Edge | Guard |
| --- | --- |
| A livery literal returns to a template | the conformance render gates on a foreign identity |
| An instance edits workshop.toml | it is a seed; no update re-renders it, no stale answer resurrects |
| A tracked file no channel claims | explain answers "yours"; the partition test pins it |
| A rendered type without comment syntax | the syntax table skips it; explain still answers |
| A shebang or frontmatter file | the header lands after it, pinned per type |
| A content file lands without its header | the gate is red; check --fix writes the computed header |
| settings.json customised locally | materialise keeps the override, names it, lets it commit |
| A layers entry names an uninstalled distribution | the mount failure teaches the layer name and the dev group |
| Layers answer and dev group disagree | pinned by test; the render derives one from the other |
| A layer is also a workspace member | the dev group lists it once, the roster's extras kept |
| A layer ships no overlay and no content | legal; the stack lint accepts it and it contributes nothing |
| settings.json written in place by an editor | the materialised file is a copy, never a link; the wheel copy cannot take the write |

Acceptance:

- The conformance test red before the fix (run against the current
  template to prove it catches today's literals), green after.
- `rg -i livery templates/` matches only the base layer's own
  distribution name in derivation logic, nothing rendered.
- `fm sync` on the monorepo materialises settings.json; a doctored
  local copy survives as a named override.
- `uv run fm check` green.

## Phase 11: the template channel reads the artifact repository

One resolver for template source and ref, used by every render.
Closes the known gap carried since the 0831 plan: a wheel instance
cannot render a member.

Deliverables:

- A single source resolver: `[workspace] templates` from the
  contract; a local directory renders directly, a git URL renders
  at the installed version of the workspace's topmost layer's
  distribution, the composed artifact's tag (contract 21). In the
  base's own instances that is the workshop version; a layer
  home's child resolves the layer's tag, never the base's.
  `new.package`, `template.apply`, and the update wave all
  resolve through it; `new.package`'s local-only refusal retires.
- The wheel-instance e2e: inside a rendered stranger instance with
  no `templates/`, `fm new.package thing` renders from the artifact
  repository and `uv sync` leaves the workspace consistent.

Edge table:

| Edge | Guard |
| --- | --- |
| Remote source, no network | taught refusal naming the source and the ref it wanted |
| Installed version has no artifact tag | refusal names the missing tag and that a workshop release publishes it |
| A fork source lacks the requested kind | copier's error wrapped taught, naming kind and source |
| Repeated render at one tag | byte-identical; the fetch is idempotent |
| Remote source refuses authentication | taught refusal, distinct from no network: copier clones with git credentials, not FORGE_TOKEN, and the teaching says which lane failed |

Acceptance:

- The wheel-instance e2e green on the local Gitea artifact mirror.
- Each edge row forced.
- `uv run fm check` green.

## Phase 12: the environment store and the forge tokens

Contract 23's CI machinery and contract 24's token surface, landed
before birth so `new.project` speaks the final names from day one.

Deliverables:

- The CI secrets rung: a CI secret replaces the shared file's slot.
  The emitters read the committed `.repo.env`'s declared keys (a
  committed, offline, deterministic generation input) and emit a
  step that materialises a runner-local rung file in the job's temp
  directory, 0600, from the matching secrets, non-empty values
  only; the engine resolves the shared-rung slot to that file under
  CI. Precedence is unchanged (process environment still wins), an
  absent secret cannot mask a committed value, and log masking
  holds because every value came through the secrets context. The
  whole-store `toJSON(secrets)` dump is rejected: it would hand
  every job and every third-party action the entire secret store.
- Tokens are emitter-mounted per job, never rung-declared:
  `FORGE_ADMIN_TOKEN` stays mounted only on the governance job, so
  the least-privilege ruling survives by construction.
- `env.set KEY --scope=ci` writes the forge secret through the
  protocol, per backend plus the fake, with a recorded conformance
  scenario; the `github-secrets` capability path is the GitHub arm.
- Token unification: `FORGE_TOKEN` and `FORGE_ADMIN_TOKEN` become
  the only names the workshop speaks; the per-kind names
  (`GITHUB_TOKEN`, `GITEA_TOKEN`, `GITLAB_TOKEN`, their `_ADMIN_`
  siblings) retire from verbs, teachings, and docs. One mapping
  site survives them: git-cliff's own environment contract wants
  the per-kind names, so the workshop feeds them from
  `FORGE_TOKEN` at that call alone, and the acceptance grep names
  the exemption. The shared rung
  may store host-qualified variants (spelling at the cut, open
  item 7) that the cascade resolves through the contract's forge
  URL, so one machine serves several forges and two instances of
  one kind never collide, which kind-scoped names could not
  express. The admin ladder becomes `FORGE_ADMIN_TOKEN`, then
  `FORGE_TOKEN`, then the taught refusal.
- Backends own ambient-token mapping: each emitter maps its forge's
  ambient job token to `FORGE_TOKEN` where it suffices, a real
  secret overriding it; where the ambient token's limits bite
  (GitHub's suppressed workflow events above all) the backend knows
  and the teaching names the secret to set and why.

Edge table:

| Edge | Guard |
| --- | --- |
| A missing secret renders as empty string | the rung writes non-empty values only; the committed value survives |
| Fork PR or unprotected branch, no secrets | the rung is empty; behaves exactly like a machine without a shared file |
| A secret overrides a committed value silently | env.show names the CI rung per key, value masked |
| Two forges, or two Gitea instances, on one machine | host-qualified storage resolves by the contract's URL |
| The ambient token where a real one is needed | the backend's teaching names FORGE_TOKEN and the limit |
| A key declared while workflows are stale | the drift gate is red until template.apply regenerates |
| GitLab, a value its masker refuses | masking there is flag-and-constraint, not automatic; the teaching names the constraint instead of assuming the mask |

Acceptance:

- On the local Gitea: a declared key overridden by a CI secret,
  observed through env.show in the job log, masked, provenance
  naming the rung; the same job green with the secret absent.
- `env.set --scope=ci` round-trips on the fake and records live on
  all three servers.
- Outside the backends, the dev rig, and the named git-cliff
  mapping site, `rg` finds no per-kind token name in the tree.
- `uv run fm check` green.

## Phase 13: `fm new.project`, birth end to end

W1 as one idempotent verb. The carrier is `livery-workshop` itself,
an `expose="global_only"` task on the builtin surface, so the verb
exists above any repository and every branded App inherits it; the
running App's identity supplies the brand and the default layer
stack (contract 19; the stack-discovery detail is stated at the
cut). footman's builtin `new` task holds the bare address at the
same exposure, and the workshop's `new` group must resolve beside
it above a project; the cut proves that resolution before
anything builds on it.

Deliverables:

- `fm new.project <name>`: render (phase 11's resolver), `uv lock`
  and `uv sync`, `fm sync`, `template.apply`, `git init` and the
  initial commit, `repo.create`, `workflow.configure`, push, and
  the unarmed setup PR, hse's shape ported. Every step detects
  done and walks past it, so re-running is the recovery.
- `--local`: everything that stays on the machine and nothing that
  leaves it, the release `--local` precedent. Render, lock, sync,
  apply, init, commit; the forge steps skipped with the teaching.
- Headless runs never hang: defaults or a taught refusal.
- `CLAUDE.project.md` seeded; README, LICENSE, `docs/`, `tests/`
  seed or not per open item 4's ruling, decided before this phase
  builds.

Edge table:

| Edge | Guard |
| --- | --- |
| Killed between any two steps | re-run resumes; interruption points enumerated in tests |
| Forge unreachable mid-birth | the local half stands; re-run resumes at repo.create |
| Repo name exists, foreign and non-empty | refusal names it; the empty get-or-create case proceeds |
| `--local` | nothing leaves the machine, no confirmation |
| Headless without required answers | refusal lists them; silence never creates |

Acceptance:

- On the local Gitea: one command from nothing to a protected
  repository whose gate is green; the second run a no-op.
- Kill-between-steps forced for every step boundary.
- `uv run fm check` green.

## Phase 14: the layer axis built

Overlays and the layer scaffold; contract 19's `--layer` arm.

Deliverables:

- Overlay composition in `render()`, apply, and the drift gate: the
  stack from the layers list, bottom to top, add or replace
  wholesale only. A lint refuses an overlay whose file partially
  edits a base file (same path, jinja-patched content is
  indistinguishable from replace, so the lint is: a replace is
  declared, never inferred; declaration shape at the cut). The
  answers file records the stack and the leaf kind. Overlay
  location inside a layer package is open item 3, ruled at this
  cut. How `copier.yml` composes is open item 10: wholesale-only
  cannot merge a questions file, so it is generated from data at
  compose time (contract 17) or gets the one ruled exception.
- The `package-python-layer` kind, flat: entry point wiring,
  `content/{fragments,skills,hooks}`, the overlay tree, a `_tasks`
  stub. What an overlay may ask (its own copier questions) is open
  item 5, ruled here.
- `fm new.project <name> --layer=<layer>`: phase 13's birth plus
  the layer package, the workspace listing its own layer last, so
  the home self-hosts at HEAD from the first commit.
- The render gate compares against the composed render; drift
  detection unchanged in shape.

Edge table:

| Edge | Guard |
| --- | --- |
| Overlay edits rather than replaces | the composition lint refuses, naming the file |
| Two layers ship one path | later layer wins; the order is the layers list |
| Overlay targets an unknown kind | refusal names the kinds the stack has |
| The home's own overlay at HEAD | the gate composes with the local overlay, not the released one |

Acceptance:

- A rendered layer home gates green and `fm layers` shows the
  stack.
- Add and replace both forced; the partial-edit lint forced.
- The drift gate red on a doctored composed file, naming the layer
  that owns it.
- `uv run fm check` green.

## Phase 15: the composed artifact release

Contract 21 built.

Deliverables:

- A layer home's release composes base templates at the pinned
  installed version with its overlay and publishes the result to
  the home's own artifact repository, tagged with the layer's
  version; the base version it composed is recorded in the
  artifact. Same-version-different-content refused, the W6 rule.
  livery's own release is the degenerate case, base only,
  unchanged in behaviour.
- A child's `[workspace] templates` is its parent's artifact URL;
  `fm update` moves it through plain `copier update` against that
  one anchor. A base improvement reaches a grandchild in two
  deliberate steps: the layer home updates (taking the new base and
  recomposing at its next release), then the child updates. Editing
  never cascades; delivery does.

Edge table:

| Edge | Guard |
| --- | --- |
| Same tag, different composed content | refused before push |
| Base version missing at compose | refusal names the base tag it needed |
| Child updates while the parent recomposes | the child's anchor is its recorded tag; nothing moves uninvited |

Acceptance:

- On the local Gitea: a layer home releases; `git diff --no-index`
  empty between the artifact tag and the composed render.
- A child created from the artifact updates cleanly to the next
  composed tag.
- `uv run fm check` green.

## Phase 16: the dummy descendant, the proof chain

Willem's ruling (2026-09-03): create a dummy hse-derived project and
prove the whole story on it before features return. The dummy is a
stand-in with a throwaway brand (name at open item 1); the real hse
rebirth follows it and replaces it.

Deliverables:

- The chain, scripted as an e2e suite on the local containers,
  idempotent, creating and destroying its own repositories:
  1. `fm new.project dummy --layer=<brand>`: the layer home.
  2. Populate minimally: the branded App name, one guidance
     fragment, one overlay file (with its declared reason), one
     skill.
  3. The home releases: layer wheel to the local index, composed
     artifact published.
  4. `<brand> new.project child`: the grandchild, gate green,
     branded workflows, the stack in its answers.
  5. The inheritance proof: land a trivial core improvement (a new
     fragment line and a template line), dev-release the workshop
     to the local index, update the home (base arrives, recompose,
     release), update the child. Assert the improvement arrived in
     the child, the overlay-replaced file did not move and the
     report names that forfeit, and no layer-owned line changed.
- The suite joins the acceptance surface per contract 22; runtime
  decides gate versus nightly at the cut, with the same
  fast-subset rule as phase 10.
- The chain also exercises phase 12's rung: the child's CI
  overrides its publish index by secret, no workflow edit.
- 0.1.0 gates on phases 10 to 16 landing, together with the
  already-shipped 1 to 9.

Edge table:

| Edge | Guard |
| --- | --- |
| Core change touches an overlay-replaced file | the proof asserts non-arrival and the named forfeit |
| A layer-owned line moves under update | the e2e diff is red |
| The chain re-run | second run no-ops end to end |

Acceptance:

- The full chain green on Gitea, both runs.
- Each edge row forced.
- `uv run fm check` green.

## After 0.1.0

The 0901 plan's tail, renumbered, resumed only once the chain is
green so each feature ships through the proven seams:

- Phase 17: the kind hierarchy and its validators
  (`package-python-nanobind`, the CMake Maya-plugin kind), the
  backend registry opened to layers, the native-wheel publish
  identity guard. Was 10. The Maya delivery detail (the 0901
  plan's open item 5 residue: conan 2 and the module packaging)
  is ruled at this cut.
- Phase 18: shared tool cache over the type-derived profile. Was 11.
- Phase 19: docs toolchain, including the derived monorepo release
  view. Was 12. Named by Willem as the model add-back: it must
  reach the dummy descendant through the gradient, not through a
  template update.
- Phase 20: sparse checkouts as partial workspaces, and `fm clone`.
  Was 13; its pre-project verb rides the phase 13 carrier instead
  of waiting here.

## Temporary, replaced by

| Temporary | Replaced by |
| --- | --- |
| The dummy brand layer home and child (phase 16) | the real hse layer home when hse migrates onto the workshop |
| `notes/20260901-workshop-plan.md` phases 10-13 | phases 17-20 here |
| Conformance runtime homes chosen ad hoc (phases 10, 16) | one ruling on gate-versus-nightly placement once both exist |

## Decision record

- 2026-09-03: this plan subsumes `notes/20260901-workshop-plan.md`
  (Willem: nothing wrong with the plan, but it focussed on adding
  features without focussing on the core, and every added feature
  made bootstrapping hse on top of the workshop feel harder). The
  old note keeps the shipped record and points here.
- 2026-09-03, the goal restated (Willem): before 0.1.0 the entire
  end-to-end instantiation and customisation story is completely
  defined and mostly implemented; a dummy hse-derived project
  proves it; features then resume, flowing to descendants as
  efficiently as possible, docs generation named as the first
  add-back. Everything non-hse-specific is extracted into an
  agnostic core that Willem can extend (new package types, language
  toolchains) so anybody basing their setup on the workshop gets
  those improvements without losing their own branding and
  customisation.
- 2026-09-03, creation is recursive (Willem's model): create a
  clean identical-shape repo from livery by default; optionally
  with a new prepopulated customisation layer, making that repo the
  layer's home; from that repo create children including the layer
  by default; variants layer again. Recorded as contract 19.
- 2026-09-03, the composed artifact per layer home (derived in
  discussion, follows from contract 12 plus copier's need for
  history; wheels hold one version and no history, so template
  merges cannot ride wheels): each layer home's release publishes
  base-plus-overlay to its own artifact repository; a child anchors
  to exactly one source. Recorded as contract 21. This also
  resolves the 0831 plan's undecided creation-verb carrier: the
  verb lives in livery-workshop as an `expose="global_only"`
  builtin task, branded by the running App, or a branded App could
  not beget.
- 2026-09-03, the inheritance gradient (derived in discussion,
  accepted with the goal): mechanisms ranked by how base
  improvements flow; overlay wholesale replace is the stated end of
  inheritance for a file and is declared with a reason. Recorded as
  contract 20.
- 2026-09-03, the dummy before the real (Willem): the proof runs on
  a throwaway brand so the story is nailed and the DX settled
  before hse itself migrates; the real hse layer home replaces the
  dummy afterwards.
- 2026-09-03, the verb family is `new.*` (Willem): `new.project`
  and `new.package`; the workflows note's `create.*` spelling is
  removed in phase 10. Open item 2 closes.
- 2026-09-03, seeds are overridable in overlays (Willem asked): the
  overlay mechanism draws no line between seed and managed files,
  add or replace wholesale covers both. The difference is what the
  replace costs: a replaced seed diverges only future births, a
  replaced managed file also stops base improvements flowing to
  existing instances. Stated in the composition section.
- 2026-09-03, identity and configuration disentangled (from
  Willem's question whether they are entangled: they were, eleven
  facts lived in both the answers and the contract, and the
  phase 8 governance loop was in tension with the template channel
  re-rendering livery.toml from stale answers): contract 23. The
  answers carry identity and machine-managed receipts only;
  livery.toml becomes a verb-filled seed; no configuration fact is
  a copier question. This lands the question count at hse's with a
  reason instead of an instinct.
- 2026-09-03, environment is the third store (Willem: this is what
  hse's cascade and .repo.env are for, facts stored and edited
  there and available in CI): the cascade is the home of
  deployment-varying facts, and the boundary with the contract is
  overridability, which is why governance facts can never enter
  it. hse itself keeps .required-checks and CODEOWNERS outside
  .repo.env. The port already matches hse's shape (the resolver
  and pre-tasks hook live in the devkit wheel, hse's own layout,
  verified in-repo 2026-09-03); extracting it to its own
  distribution stays possible and nothing forces it.
- 2026-09-03, CI secrets are the shared rung (Willem: each
  variable overridable by a CI secret if it exists, the secret
  store replacing the shared rung; mechanism settled in
  discussion): the emitters materialise a runner-local rung file
  from the committed .repo.env's declared keys, non-empty secrets
  only, below process environment; the whole-store dump is
  rejected for blast radius; an undeclared secret is invisible to
  the rung, so FORGE_ADMIN_TOKEN's governance-job-only mount
  survives by construction; env.set grows --scope=ci writing
  through the protocol.
- 2026-09-03, one forge, two tokens (Willem: every livery-derived
  project runs on a single forge, so FORGE_TOKEN and
  FORGE_ADMIN_TOKEN suffice, the admin token serving CI's
  governance job and local admins alike; the forge configuration
  and public interface as forge-type abstract as possible):
  contract 24. The per-kind token names retire; the shared rung
  stores host-qualified variants resolved through the contract's
  forge URL (derived: kind-scoped names cannot tell two Gitea
  instances apart, the dev rig beside a production Gitea already
  collides); backends map their forge's ambient job token to
  FORGE_TOKEN where it suffices and teach the secret where it
  does not. Refines the phase 8 per-kind ruling; the
  everyday/admin split survives unchanged.
- 2026-09-03, the first derivation crosses forges (Willem: the hse
  derivation will live on Gitea, based on livery on GitHub; the
  main reason the token surface must be abstract): the forge is a
  birth-time contract fact the birth verb writes, never inherited
  from the parent stack; a layer home's artifact repository lives
  on the home's own forge; children fetch templates cross-forge by
  plain git URL, and a private artifact repository needs nothing
  beyond a readable remote. Folded into contract 24.
- 2026-09-03, provenance rides the file (Willem: every generated,
  assembled, seeded, or overridden file that supports comments
  states its provenance at the top, so it is easy to tell whether
  to change it directly or go back to the source; plus a task that
  takes a path and names the source): contract 25 and the phase 10
  deliverables. The one refinement from discussion: headers are
  authored in sources and never injected into materialised copies,
  because differing bytes are exactly how a deliberate override is
  detected; overrides and comment-hostile files belong to the
  query verb.
- 2026-09-03, headers are machine-written and the verb is explain
  (Willem: no magic incantations at the top of files when creating
  a new layer or package type; HTML comments in markdown are
  fine): render-time injection for rendered files, emitter headers
  for generated ones, and a provenance lint with `--fix` for
  shipped layer content, all serialising the one classifier that
  `fm explain` prints. The update wave refreshes injected headers
  after copier's merge. Refines the provenance entry above:
  authored-in-source narrows to layer content, and even there the
  lint writes the text.
- 2026-09-03, the plan reviewed against the tree before approval
  (findings folded in): the phase 11 ref rule is the topmost
  layer's installed distribution version, derived from
  contract 21 (the workshop-version spelling was wrong for a
  layer home's child); settings.json materialises copy-first, a
  write through a link would land in the installed wheel; the
  per-kind token names survive only where a third-party tool's
  environment contract requires them (git-cliff); and
  `.repo.env.local` gains its missing gitignore lines in
  phase 10. Open items 8 to 11 record what the review left to
  rule, and the contracts preamble now says the 0901 note keeps
  the contract bodies.
- 2026-09-03, workshop is the product (Willem, resolving open
  item 11 against the keep recommendation): anyone basing their
  environment on the workshop should see livery nowhere except
  the distribution `livery-workshop` and the import
  `livery.workshop` (and any sibling layer they list). The thing
  being built is workshop; livery is the monorepo it lives in.
  The contract file, the materialise manifest, and the base
  fragment take workshop names in phase 10, while zero external
  instances exist.

## Open

1. The dummy brand's name, and whether its repositories live only
   inside the e2e suite (created and destroyed on the local
   containers) or as standing repositories. Owner: Willem, at the
   phase 16 cut. The suite-only shape is recommended: standing
   repositories rot, the suite re-proves on every run.
2. Resolved 2026-09-03: `new.*`; the `create.*` spelling leaves
   the workflows note in phase 10.
3. Where a layer's template overlay lives inside the layer package
   (beside `content/`, or a sibling `templates/` tree), and the
   declaration shape for a wholesale replace. Owner: the phase 14
   cut.
4. Whether README, LICENSE, `docs/`, and `tests/` become project
   seeds (hse ships them; the workshop renders none today). Owner:
   Willem, before phase 13 builds.
5. What an overlay may parameterise: its own copier questions and
   their recording in the answers file. Owner: the phase 14 cut,
   with the guard that overlay questions follow the same
   defaulted-or-derived lint as the base's.
6. Carried from 0901: the base-CI nudge automation (its open item
   7) and recording GitLab's licensed arms live (its open item 8),
   both unchanged, owners as stated there.
7. The host-qualified spelling for shared-rung tokens (how a forge
   URL becomes a key suffix, and whether any other environment key
   deserves the same qualification), and how the engine learns the
   CI rung file's path (an emitted-step variable, its name bound
   by the no-brand-variables rule). Owner: the phase 12 cut.
8. How a layers entry (an import path, what `mount_layers`
   consumes) names its distribution (what the dev group and the
   uninstalled-distribution teaching need). Derive from installed
   metadata where the environment answers, declare in the entry
   where it cannot. Owner: the phase 10 cut.
9. The `requires-python` bound-to-matrix rule: which minors in
   the range are tested, and what latest means offline and
   deterministically. Owner: the phase 10 cut.
10. How `copier.yml` composes in the composed artifact: generated
    from data at compose time (contract 17, recommended), or the
    one ruled exception to add-or-replace-wholesale. Owner: the
    phase 14 cut, with item 5.
11. Resolved 2026-09-03: neutralise before 0.1.0 (Willem, against
    the keep recommendation). The product is workshop; livery is
    only the monorepo's name. `livery.toml` becomes
    `workshop.toml`, `.livery-materialised` becomes
    `.workshop-materialised`, the base fragment becomes
    `CLAUDE.workshop.md`; `.workshop/` already carries the
    product's name. The only livery spellings an instance sees
    are distribution and import names: `livery-workshop`,
    `livery.workshop`, and any layer it lists, `livery.forge`
    alike. Folded into phase 10.
