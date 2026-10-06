# Audit evidence as a workshop capability: the investigation

Status: an investigation, opened 2026-09-27 at Willem's request. It is
not a plan and it rules nothing on its own. Its purpose is to hold the
territory, the pain points, and the design forks while the questions in
Open are answered, and then to serve as the research base for the plan
that follows. The rulings Willem has already made are in the decision
record.

## What this is

Any workspace the workshop manages should produce the evidence an audit
asks for as a by-product of the render, rather than as a document
somebody assembles under time pressure. That is the subject: a
capability every consumer gets, not one company's preparation.

The delivery vehicle is a plugin. The seam and the generic kinds ship in
livery; a consumer's own specifics ship in a private layer, which the
kind registry already supports.

## What a general capability must satisfy

Six properties, each a constraint on the design rather than a
preference. They follow from the capability serving workspaces nobody
here has seen.

- **Value with zero declaration.** A workspace that does nothing still
  gets a document generated from the locks it already has. If the first
  step is filling in a register, most consumers never take it.
- **Enforcement opt-in by mounting, not by a switch.** A gate that
  refused undeclared components would break every existing workspace on
  upgrade. The mechanism already exists and needs no new vocabulary:
  the layers list is the only activation record, an installed but
  unlisted layer does nothing and `fm doctor` names it as available.
  Mounting the layer is the opt-in.
- **Honest per kind.** A kind with no enumerator says so, the way the
  gate already skips a verb that does not apply rather than passing
  vacuously. Other people's workspaces will carry kinds nobody has
  written an enumerator for, and a silently empty document is worse than
  no document, because it is handed over believed.
- **No store assumed, no forge assumed.** Retention works for a
  consumer with no strongroom, which means documents attached to
  releases, on every forge kind the workshop renders.
- **The consumer declares intent, not format.** They say what they
  ship; the workshop picks CycloneDX 1.6 and validates it. Nobody
  should have to learn a schema to get a compliant document.
- **Keep the human part small.** Declaration is the part that does not
  scale across consumers, so discovery covers everything it can and
  declaration is reserved for what genuinely cannot be discovered.

## The worked example

One consumer's shape, which motivated the investigation and is used
throughout as the hard case. It is not the scope of the design.

The work repositories are being converted into one monorepo managed by
the workshop. What it will contain, as ruled on 2026-09-27:

- **Python, a large part of it machine learning.** PyTorch, first-party
  models trained on first-party data, and some models built on public
  checkpoints such as wav2vec.
- **Native C and C++ packaged with conan**, which the existing
  `cpp-conan` kind already covers.
- **Unreal plugins and example projects exercising them.** Stock engine
  only, under the revenue threshold. The plugins link `cpp-conan`
  packages.
- **Shipped model weights.** Weights are distributed inside the
  products, not only served.

The property that generalises from it: a consumer who ships anything to
third parties becomes the upstream in somebody else's bill of
materials, and their customers ask them for one. So the evidence is a
deliverable that ships with the product on request, per version, rather
than an artifact produced once under duress. A document handed over
routinely cannot be stale, cannot be quietly incomplete, and has an
owner by construction. That is the better thing to design for, and it
holds for any consumer with customers.

Which regulation governs a given consumer is theirs to know, not the
workshop's. The capability's job is to keep the components, the
obligations and the change record available and current, whichever
regime reads them.

## What the audience requires

### The loops

Each of these is an ongoing loop, not an artifact. The third column is
where a verb has leverage.

- **Intake of a new third-party thing.** A dependency, a model, an
  Unreal plugin, a dataset, a purchased asset. Fails because it arrives
  in a pull request at the end of a day and nobody reads the licence.
  Highest leverage in the whole map: if introduction and declaration are
  the same act, every later document is a read of the record instead of
  a reconstruction. A gate that refuses an undeclared component is most
  of the programme.
- **Inventory maintenance.** Fails by being generated once and stale a
  week later. A generator plus a drift gate fixes it, which is the
  pattern the render already runs.
- **Per-artifact release documentation.** Fails as one document per
  repository when the audience wants one per artifact, per version, per
  variant: per wheel, per platform wheel, per CUDA variant, per engine
  version, per configuration. Retention keyed to the receipt tag is the
  fix.
- **Vulnerability monitoring and triage.** Fails at "which shipped
  versions are affected", because nothing retained the closure of
  version 3.2. Also fails on ownership and on the clock: the CRA wants
  an actively exploited vulnerability reported within 24 hours. A verb
  over retained documents answers the mapping; a scheduled point and an
  auto-filed issue give it an owner and a timestamp. Poisoned weights
  and jailbreaks have no CVE feed, so that half stays human.
- **Licence obligation fulfilment.** The most commonly skipped loop.
  Knowing a licence is not discharging it: attribution and NOTICE files
  in the shipped product, source offers for LGPL and MPL components,
  naming obligations on some model families, Epic's attribution
  requirement. Fails as a shipped installer with no notices while the
  spreadsheet says the licences are fine. High leverage and rarely
  automated.
- **Build integrity and reproducibility.** "Rebuild 3.2 with the
  toolchain that built it." Fails because the toolchain moved and the
  machine is gone. Already answered here by the tool record.
- **First-party code provenance.** Employment and contractor IP
  assignment, AI-assisted code, copied snippets, vendored files of
  unknown origin. Mostly human. A verb can detect vendored files by
  digest and refuse unattributed content directories.
- **Access and change control evidence.** Fails on start date more than
  on substance. Largely already rendered from the contracts.
- **Data and model governance.** Lineage from dataset to weights to
  release, lawful basis, PII, retention, evaluation records, model
  cards. Fails as an unrecorded chain, so nobody can say which data
  produced the weights in production. This is the subject of the
  strongroom section below.
- **Secrets hygiene.** An import is when old credentials surface.
  Scanning at import is cheap; history rewriting afterwards is not.
- **Third-party service register.** SaaS, APIs, model providers, their
  data processing terms and subprocessors. Every security review asks
  and no bill of materials covers it. Declaration with no discovery.
- **Seat and entitlement compliance.** Engine seats, per-seat assets,
  commercial model licences. Declaration only, and where acquisition
  findings with a price attached tend to live.
- **End-of-life tracking.** Shipping something unmaintained. Records
  can carry EOL dates and the gate can warn before a customer does.
- **Disclosure process.** A published policy, a contact, a coordinated
  disclosure record, a path to issue advisories. The CRA requires it.

### The failure modes that cut across them

- Declaration after the fact, so every register is archaeology.
- The generator sees only what the package manager sees. Checked-in
  static and shared libraries, blobs downloaded during a build, engine
  third-party trees, model weights pulled by name. The under-report is
  silent, which is the dangerous property.
- No owner, so it rots. Reminders do not work; exit codes do.
- No retention, so historical questions are unanswerable.
- Obligation known, never performed.
- Evidence whose start date is the month before the audit.
- Hand-written documents that do not validate against the schema they
  claim.
- Human-only facts stranded in a spreadsheet that is not linked to the
  code, so they are absent from what gets handed over.
- Duplicate and divergent copies of one component across members. A
  monorepo surfaces this for the first time, and it looks worse before
  it looks better.
- Gate friction on large binaries, so people bypass the gate, which
  destroys the chain.
- Machine learning specifics: the same weights under two names, a
  quantised or merged derivative whose parent is unrecorded, and an
  evaluation set that leaked into training.

## Where the workshop already stands

- Every dependency is pinned by digest on both axes: `uv.lock` for
  Python, `tools.lock` with `tools.graphs/` and `records/` for the
  toolchain, per host. The build-environment inventory answers "which
  compiler produced this binary", which is otherwise unanswerable.
- Change control is rendered from the contracts, not assembled:
  CODEOWNERS and approvals from `[owners]`, one required status
  context, repository settings asserted by `configure`, immutable
  receipt tags, release squashes recognised by their `Mined-At` line.
- One gate command that CI and the desk both run, so "tested before
  merge" is an exit code.
- The store already lands artifacts by digest with lazy invalidation,
  and strongroom is a content-addressed store with trees and versions.

What is absent: no bill of materials is produced anywhere, nothing is
placed in a wheel, and the `uv publish` call in the workshop's
`_publish.py` generates no PEP 740 attestation. The workshop
plan's 2026-08-31 entry already records attestation and signing as
uncovered.

## Component enumeration belongs to the kind

The seam is already open. `Backend` in
`packages/workshop/src/livery/workshop/_kinds.py` carries build,
isolated test, publish, classify and stamp. One more callable,
"enumerate my components", and each kind answers for its own world:

- `python` from the lock, one document per variant.
- `python-nanobind` adding the conan graph it linked and whatever was
  bundled into the wheel, with PEP 770 placement in `.dist-info/sboms/`
  in the same step.
- `cpp-conan` from the conan 2 graph, the only source that knows the
  native closure with its options, settings and profile.
- A machine-learning kind adding models and datasets as components.
- An `unreal-plugin` kind, structurally the same as `python-nanobind`:
  an artifact in a foreign ecosystem whose native half links a
  `cpp-conan` member, built by that ecosystem's own tool. UBT's
  `JsonExport` and `QueryTargets` modes give it a machine-readable
  module and target graph.

Above the kinds, the workspace assembles the members' documents, stamps
the toolchain from `tools.lock` as the build environment, and the
release wave attaches the result to the receipt.

Two properties decide whether this works, and both are rulings:

1. **A kind can require a declaration and refuse without it.**
   Discovery alone silently under-reports exactly where the findings
   are. A component with no declared licence fails the gate.
2. **The generated document validates against its schema in the gate**,
   so the output is machine-checked rather than asserted.

Format: CycloneDX 1.6, which has ML-BOM for models and datasets as
first-class components, satisfies the BSI TR-03183-2 baseline the CRA
gets read against, and covers the CISA 2025 minimum elements including
component hash, licence, tool name and generation context. SPDX 3.0.1
with its AI and Dataset profiles is the alternative.

### What stock-engine-under-threshold deletes

No engine source in the repository, no patch series against an engine
commit, no repository-access constraint from engine source, no royalty
or seat exposure, and none of the engine's third-party trees to
disclose, because the engine is not redistributed. The installed 5.6
tree has 133 directories under `Engine/Source/ThirdParty` and 1134
licence files; those are Epic's to disclose, not ours.

The engine becomes a build-environment fact, and the tool record
already has the kind for it: `system-check`, alongside the platform
SDKs UBT enforces. Two facts need recording with a review trigger
rather than assuming: the revenue threshold and the usage class. Both
are true-until-dated.

### What it sharpens

- The shipped plugin's components are the conan graph, fully
  enumerable.
- Packaging mode decides both the document and the obligation. A static
  library absorbed into the plugin binary, a shared library shipped
  beside it, and a header-only dependency are three different answers
  to "what did I redistribute". Static linking is where copyleft stops
  being theoretical. The record needs the link mode per dependency, not
  only the dependency.
- One document per plugin version, per engine version, per platform,
  per configuration. Same shape as the CUDA variant problem, so it
  wants one solution rather than two.
- `KindRecord.artifact` currently means `python` or `conan`. A plugin
  ships through neither.

### Example projects

Building an example project against the plugin against the engine is
the only real integration test, which makes the examples valuable to
the gate and nearly irrelevant to the audit. One rule keeps them that
way: example content is first-party or CC0 only, no marketplace assets.
That deletes the asset-provenance register, per-seat entitlement
tracking and the transferability problem, and it has to be set before
someone drags in a purchased asset pack.

## Lineage belongs to the fabric, annotations to strongroom

Ruled 2026-09-27, after the investigation first proposed a lineage
format inside strongroom. The proposal is withdrawn and the reasoning
is kept, because the argument that defeated it is the one that decides
where anything of this kind goes.

### Two relations, not one

`packages/strongroom/spec/version.md` defines `parents` as "the
versions this one continues", and then leans on it: parents make
history, diff and common ancestry ordinary tree walks. A trained model
does not continue the dataset it was trained on. Putting a dataset
version or a base checkpoint in `parents` makes diff-against-parent and
common ancestry meaningless, and loses the role of each input, so
nothing distinguishes training data from evaluation data.

History is "model v2 follows model v1", and `parents` already has it.
Derivation is "model v2 was produced from these inputs", which git has
no equivalent of.

### Why derivation is not a store relation

The relation is real and it is needed. It is not strongroom's.

- **A derivation is a call.** Training, conversion, quantisation,
  packaging and building are calls with inputs and outputs, and the
  fabric owns calls.
- **The layering already says so.** `Version.receipt` is "the receipt
  of the producing call": strongroom points at the call and does not
  describe it. `Subject` carries `call` and `receipt` kinds for the
  same reason. Adding inputs to a store format would invert a decision
  the store has already taken.
- **The vocabulary is already spoken for.**
  `derivations/<call key>` is a published namespace convention for an
  evaluator's skip tier. The name collided because the concept was
  already one layer up.
- **The multipurpose evidence pointed the other way.** The non-ML
  consumers offered in favour of a store format, IoStore containers as
  a derivation from the store, a tool view derived from an archive, a
  docs site derived from its sources, are all calls. That is one fabric
  relation seen four times, not a store relation with many users.
- **Non-determinism is a call property.** Training does not replay to
  an identical digest. The fabric already reasons about which calls are
  cacheable, so "not reproducible, and here is what went in" is its
  question.

### Reachability needs no new format

Reachability was the argument that first made a store format look
necessary: a lineage claim whose referent can be collected is worse
than no claim, because the loss is silent and late. The store already
answers it.

`pins/<name>` is in the store-owned namespace table, points at
anything, and exists as "explicit roots for the sweep". The fabric pins
the inputs its receipt cites. The store guarantees retention; the
fabric records why, with the pin's consumer-owned `meta` pointing back
at the receipt. `lifecycle.md` also promises that every ref on disk is
a root in every namespace, whoever declared it, so a receipt published
under the fabric's own ref is rooted by any sweeper, including one that
has never heard of the fabric.

With that, the three questions this investigation had opened, which
direction a lineage record points, what it is called, and whether it
must land before the redesign's freeze, all dissolve. Strongroom needs
no new format for lineage and no new reserved namespace.

### What the fabric owes

Recorded here so the fabric is not designed without this case in view:

- A receipt that names its inputs with roles and its outputs, not only
  a key and a result.
- Receipts retained rather than discarded after a cache hit, for as
  long as the artifacts they explain are supported.
- Signable, since an assertion about an artifact carries an author and
  a date, and several assertions about one output must coexist without
  erasing each other.
- Exportable as an in-toto style statement, which is the shape every
  attestation format already uses: the statement names its subject.
- One receipt may name several outputs, since a training run emits
  weights, a tokenizer, a config and an evaluation report from one
  call.

The scheduling cost, stated plainly: there is no fabric package yet, so
either lineage waits on the fabric or the plugin keeps its own records
until it lands. That is tolerable because the plugin's evidence
generation is sequenced first anyway.

### What stays in strongroom

Origin, not derivation. A purchased corpus or a downloaded checkpoint
was produced by no call of ours; it has a source, a licence and an
acquisition basis. That is a statement about bytes, scoped over tree
paths, checked for coverage against a tree digest, and wanted by
consumers that make no calls at all. Annotations are strongroom's;
derivation is the fabric's. Each side passes its own test, which is
below.

### Travelling with the artifact

Two senses, and both are needed.

Inside the system, lineage is a walk from a shipped artifact through
the receipts that produced it to every input, held by the fabric and
rooted in the store by pins.

When the artifact leaves, it is a closure export: the transitive graph,
flattened, carrying each input's digest and descriptor but not its
bytes, signed, and projectable into a CycloneDX ML-BOM for a customer
who wants the standard format. The in-toto and SLSA shapes are the
same, so no wire format needs inventing.

The property that makes this better than a register: **content
addressing discloses without disclosing.** A customer receives "trained
on proprietary corpus, digest X, with this descriptor", and no data
leaves. If the claim is challenged, the bytes are produced and they
rehash to X. A spreadsheet's claim about training data is
unfalsifiable; a digest's claim is provable on demand and withholdable
until then. For proprietary data that must nonetheless be accounted
for, nothing else has both properties.

### What the graph buys

- **The NOTICE file becomes computed.** Walk a shipped model's
  receipts to its inputs, union their licence and obligation
  annotations, emit the attribution document. That automates the
  obligation loop, and it only automates if both halves, the receipts
  and the annotations, are machine-readable.
- **Train and evaluation contamination is a set intersection over
  digests.** Exact, cheap, and currently unanswerable anywhere.
- **Erasure impact becomes a query.** Weights cannot be unlearned. The
  answerable question is "which shipped models derive from a dataset
  version containing this subject", which is what a regulator asks, and
  the tombstone records the erasure itself.
- **"Are these the weights we shipped in 3.2"** is a digest
  comparison.

A property the receipt must not claim: training does not replay to an
identical digest, unlike the tool graph. A receipt of a training call
is an attestation of inputs, never a build recipe, and the gate can
check completeness but never reproduction. That belongs in the
fabric's prose so no later reader takes it for a promise it cannot
keep.

### Dataset-scale trees and prefix claims

The question was whether a claim can be scoped to a path: everything
under this prefix comes from source X under licence Y. Yes, and it is
the right shape, but two things are being separated.

**Per-file digests stay. Per-file metadata goes.** The digests are
needed for contamination checks, for erasure queries and for proving
what was used, and the tree is already a Merkle tree over directories,
so an appended dataset version shares every unchanged subtree and no
single object is huge. What does not scale, and would be wrong even if
it did, is a licence field per file: it is unmaintainable and it will
be inaccurate.

The claim is a prefix rule: prefix to source, licence, basis, acquisition
date, and an evidence digest. Design points, each needing a ruling:

- **Coverage is checked, not assumed.** Every entry in the tree must
  match some prefix or the record refuses. Storage is the size of the
  claim set; verification is one walk of the tree at ingest. This is
  the property that prevents silent under-reporting.
- **Longest prefix wins**, like a routing table, so exceptions are
  expressible: all of X under one licence except one subdirectory. The
  alternative is requiring non-overlapping claims, which is easier to
  read and cannot express the exception.
- **The claim set binds to a tree digest.** A prefix claim over a
  mutable directory is worthless, because a file from another source
  lands under a covered prefix and is silently covered. Bound to an
  immutable snapshot, adding files makes a new tree that must be
  re-checked. That turns a standing assumption into a statement about
  one snapshot.
- **Per-file exceptions must exist** for takedowns, opt-outs and
  misattribution, with the set expected to stay small.
- **Evidence by digest**: licence text, terms as of a date, a purchase
  receipt, a consent record, a download manifest. The claim is then an
  assertion with an artifact attached, which is what makes an audit
  short.
- **Layout is a design decision.** Choose the corpus layout so the axes
  that must be queried are prefixes. Speech corpora laid out by speaker
  make erasure a prefix query; any other layout makes it an index.

The redesign's large-data measurement row already names this workload:
public dataset families with revisions, Parquet shards from a versioned
dataset, successive fine-tunes of one checkpoint family. The manifest
cost as a fraction of corpus bytes is a measurement, not a guess, and
it belongs with those runs.

### The general mechanism: path-scoped annotations

The prefix claim above should not be a licence format. The general
shape is a **path-scoped annotation** record: a set of rules, each a
prefix and a map of name to value, bound to the tree digest it was
verified against. The vocabulary belongs to the consumer, as a tree
entry's name does. Licence claims, source, acquisition basis, dataset
cards, classification labels and per-path attribution become one format
with different keys, and a licence-specific format would have violated
"knows no tool, no call and no dataset" in its first line.

The completeness check generalises through a pattern the redesign
already ruled for tree names: everything the format used to enforce
became "a named profile a namespace declares". Required annotation keys
are the same shape. A namespace declares that every path under it must
resolve `source` and `licence`; the store enforces resolution without
knowing what a licence is. The domain requirement is declared by the
consumer and the enforcement stays generic.

Annotations are a separate object from the tree. The decisive reason is
deduplication: annotations inside the tree would make the same bytes
under a different licence a different tree digest, so identical content
would stop sharing. Separation also lets an annotation arrive after the
content without rewriting it.

Open fork: **per-key merge or per-rule replacement.** Nearest ancestor
wins for the whole map is predictable, and forces a subdirectory that
changes only the licence to restate the source. Per-key resolution is
ergonomic and less obvious to a reader. The candidate is per-key, with
a flattened resolved view as the printed and exported form, so storage
stays compact while the evidence a reviewer reads is unambiguous.

### What belongs in an annotation, and what does not

Four questions decide it. A fact belongs in an inherited annotation
inside hashed content when all four hold:

1. It is a statement about the bytes, true for as long as those bytes
   are those bytes.
2. It does not change faster than the content does.
3. It is the same answer in every store that holds the object.
4. Nothing has to enforce it for it to be true.

Passing, and each earning its place: licence, source, acquisition basis
and date, evidence digests, classification, retention class, PII and
special-category flags, consent basis, the attribution text a component
requires, export-control classification, model and dataset card fields.

Failing on 2 and 4 are **policy**: chunker, compression, encryption,
materialiser hints, line-ending and text-versus-binary handling, name
profiles. Every gitattributes-shaped fact lands here. They are
instructions to an implementation, they change while the content does
not, and the namespace already holds them.

Failing on 1 is the category worth naming before it leaks into the
format: **time-varying facts about the world that reference the
bytes.** A known vulnerability, an end-of-life date, a revoked
checkpoint, a source later found to be misattributed. The bytes are
unchanged and the fact moved. These belong in a mutable index keyed by
digest, joined at query time. Baking a scan result into a hashed object
freezes it where it can never be updated.

Three destinations, then: annotations in content, policy on the
namespace, and a mutable index keyed by digest. Authorisation is an
example of the second kind and not the first: it must be revocable, its
principals are local to a deployment, and it is worthless unless
something enforces it. What lives beside the name is the namespace's
root of authority, who may mint grants for its subtree. Each use is
authorised by the capability it carries, verified offline, and
revocation is non-renewal, as articles 1 to 4 of the cryptography
manifesto in `livery-planning` rule; a compare-and-swap revocation list
fits only as an organisation's real-time add-on. Confidentiality in a
content-addressed store comes
from encryption, which the redesign already carries as ciphers with
rotation at compaction.

One edge the separation already handles: a licence can gain a later
grant, through dual licensing or a relicense applied to an existing
release. Because annotations are separate objects bound to a tree
digest, a second record over the same tree states the new grant without
touching content.

## Runtimes, conversion, and why the graph covers both

The inference runtime arrives by three routes, and they differ in who
redistributes what (2026-09-27):

- **Unreal's NNE with the ORT backend.** The runtime is the engine's,
  pinned by engine version, and nothing is redistributed by us. The
  engine's ORT version sets the opset ceiling. Their component to
  disclose, our model to disclose.
- **The onnxruntime Python wheel.** Permissive and enumerable from the
  lock; the execution providers are the part carrying vendor terms.
- **A linked build through conan**, not ruled out. Then we redistribute
  the runtime and its providers, and the CUDA, TensorRT, DirectML and
  CoreML provider terms attach. Link mode decides the obligation, as it
  does for any static library.

CoreML is reached directly from Swift today, which adds a toolchain
surface: `Package.resolved` is a lock with revision pins, Xcode and SDK
versions are `system-check` facts, and Apple system frameworks are
linked but not redistributed.

The consequence for lineage is larger than the licence question.
**CoreML conversion and quantisation are derivations.** The weights
that ship are not the weights that were trained: a `.mlpackage`
produced from a PyTorch checkpoint by a converter at a version is a
derived artifact, and without a conversion edge recorded, "which
training run produced this shipped file" cannot be answered. That is
the audit question itself. So one record shape must cover training,
conversion, quantisation and packaging, which is a further argument for
role-named inputs over anything training-specific.

## On models built on public checkpoints

The licence attaches to the checkpoint, not to the family or the
architecture. The wav2vec 2.0 base and XLSR checkpoints are permissive,
which means attribution and a NOTICE entry. Neighbouring checkpoints
from the same producer are not: some are non-commercial, which in a
shipped plugin is product-blocking rather than untidy. So the gate that
matters is that a base checkpoint recorded without an exact upstream
revision and a licence refuses. Pinning by repository revision with
per-file digests also removes the `from_pretrained` drift problem in
the same stroke.

Two more model-side facts worth recording as first-class, because no
licence file states them: terms of service that forbid training a
competing model on a provider's outputs, and the lawful basis for
first-party training data.

## What no plugin can do

Licence compatibility judgments, asset transferability on a change of
control, whether a contractor assigned their IP, whether training data
was lawfully obtained, and whether a use restriction covers a given
use. The plugin's job is narrower and still worth building: force the
human answer to exist, attach it to the component, timestamp it, and
refuse the build while it is missing.

## Standards worth reading rather than rediscovering

- **NIST SP 800-218 (SSDF)**, and its AI companion: the practice
  checklist US federal buyers ask against.
- **OpenChain, ISO/IEC 5230**: an ISO standard for a licence
  compliance programme, which is the intake, inventory and obligation
  loops above. **ISO/IEC 18974** is its security assurance sibling.
- **CISA 2025 Minimum Elements for an SBOM** for contents, and **BSI
  TR-03183-2** for the format baseline.
- **CycloneDX 1.6 including ML-BOM**, or **SPDX 3.0.1** with its AI and
  Dataset profiles.
- **SLSA v1.0** for build integrity as graded levels, and **in-toto**
  for the attestation shape.
- **ISO/IEC 29147 and 30111** for disclosure and incident handling,
  which the CRA duty maps onto.
- **PEP 740** for index attestations and **PEP 770** for SBOMs inside
  wheels.

## How it is built, and which seams exist

### A layer, activated by being listed

The extensible gate plan already rules the shape: "Layers activate by
being listed. The `[workspace]` table's `layers` list is the only
activation record. Installing a wheel never activates anything: an
installed but unlisted layer is visible to `fm doctor`, which names it
as available, and it does nothing else."

That is the opt-in, and it is better than a level key because an
unlisted layer cannot change any verdict. The same plan rules the
boundary that decides what goes where: facts live in the contract,
opinions live in layers, and a decision is replaced by code in a layer,
never by a settings switch. So what a workspace ships, its support
lifetime and its retention period are facts and belong in
`workshop.toml`. Strictness is an opinion and is the layer's; a
consumer who wants it stricter layers over it.

One line inside that: a verb that only prints belongs in the workshop
core, beside `fm explain` and `fm doctor`, because that is how a
workspace gets value with zero declaration. Anything that refuses, adds
a gate leg, or attaches a release asset belongs in the mounted layer.

### Seams that exist today

- **Layer mounting and verbs.** A layer's footman plugin grafts at
  mount, in precedence order; discovery is the list and nothing else.
- **Kinds from a layer.** `register_kind` at mount, so the per-kind
  enumerators can ship from a layer rather than from core.
- **Contract extension is free.** `_contract.py` refuses only a key
  spelled with an underscore, not an unknown table, so a layer reads
  its own tables with no registration seam.
- **Templates and guidance fragments** from a layer.
- **Scheduled points.** `[[ci.schedule]]` with a point and a task,
  where the periodic vulnerability mapping goes.
- **Forge abstraction** for release assets, already exercised by the
  conan cache tarballs the wave attaches.
- **Retention**, for a consumer that has strongroom: every ref is a
  root, and `pins/` exists for explicit ones.

### Designed, not built

**The check registry.** `_quality.py` hard-codes the verb list and
`_backends/_python.py` hard-codes the tools inside each verb. Phase 2
of [the extensible gate plan](20260905-extensible-gate-plan.md) is
`CheckRecord` and `register_check` beside the kind registry; phase 3 is
layers registering checks with doctor discovering them. Phase 0 landed;
phases 1 to 7 await review. Until they land, a layer cannot add a gate
leg without patching the workshop, so the enforcing half of this
capability is blocked on a plan already in the queue rather than on
anything new.

### Missing entirely

- **A release-wave seam.** Nothing in the workshop's publish or release
  driver lets a layer contribute a step or attach a file to a release.
  Per-artifact documents land there, so it needs designing, and it is
  small: the wave already attaches collected files on the conan route.
- **The enumerator on `Backend`.** A new protocol member touches every
  kind, so it is core work and not a layer's.

### What the seams do to the order

The ordering below survives, and its first four items are nearly
unblocked. The report reads contracts, the tool lock with its per-host
digests, the gate record and the receipts, and prints, so it needs no
seam at all. The dependency document and the attribution artifact
generate today as verbs, and only their attachment to a release waits
on the wave seam. The attestation is a small change inside the
workshop's own publish. Items five to seven are the ones that wait: the
enumerators on the `Backend` member, the enforcing gate on the check
registry, and retention on the wave seam.

## Ordering

By value to an arbitrary consumer per unit of work. The first four need
no declaration from anybody and no new strongroom format, which is
where a capability for other people starts.

1. **A report of what the render already knows.** Owners and approval
   counts, required contexts, the branch protection the configure verb
   asserts, the toolchain pin with its per-host digests, the gate's
   record, the release receipts. Almost no new mechanism, and it answers
   the change-control and build-integrity half of any audit for every
   workspace, including pure-Python ones that ship no weights and no
   native code.
2. **A dependency document per released artifact**, from the lock,
   validated against its schema, attached to the release. Every Python
   consumer gets it on day one.
3. **Licence inventory and a generated attribution artifact**, with
   undeclared licences listed rather than refused until the consumer
   turns the level up.
4. **The attestation in the publish path.** One change in one place,
   and every consumer publishing to an index benefits.
5. **Per-kind enumerators** beyond Python: conan, nanobind, and
   whatever a layer brings.
6. **The declaration register and the enforcing gate**, opt-in, at the
   level the consumer chose.
7. **Vulnerability mapping** over retained documents.

Two things that order protects. The plugin's evidence generation runs
ahead of any new hashed format, so the measurements come from a
consumer that exists rather than from imagination: how many annotation
rules a real corpus needs, whether per-key resolution earns its
complexity, how large the manifests get, and what the fabric's receipt
has to carry. And an audit's calendar never sets the store's format,
which is the only mechanism by which this work could make strongroom
worse.

## Decision record

- 2026-09-27, the driver (Willem). The work repositories become one
  workshop-managed monorepo, and that monorepo is what gets audited.
  livery is the tool, not the subject. The functionality ships as a
  toolroom and workshop plugin that both provides and enforces this.
- 2026-09-27, Unreal scope (Willem). Stock engine only, under the
  revenue threshold. The products are Unreal plugins and example
  projects exercising them. The plugins link `cpp-conan` packages. No
  engine modifications, so no patch series to track.
- 2026-09-27, models ship (Willem). Weights are distributed inside the
  products. Mostly first-party, trained on first-party data; some are
  built on public checkpoints such as wav2vec.
- 2026-09-27, this is an investigation, not musings, and it leads to a
  plan.
- 2026-09-27, mounting is the opt-in (from the extensible gate plan's
  rulings, not a new decision). An unlisted layer changes no verdict,
  so the capability needs no level key. Facts stay in the contract,
  strictness is the layer's, and a print-only verb may sit in core.
- 2026-09-27, the subject is the capability, not one audit (Willem).
  The concern is that a workspace managed by the workshop gets as much
  automatic help in passing an audit as can be provided. One consumer's
  monorepo is the worked example, and its scheduling is not the
  design's input.
- 2026-09-27, the lineage relation is first class (Willem): a
  strongroom format, not a consumer format above it. The format itself
  is not settled and nothing here fixes a field list. **Superseded the
  same day by the fabric entry below**: first class stands, the layer
  does not.
- 2026-09-27, three destinations for a fact (proposed, awaiting
  Willem): annotations in content for statements about the bytes,
  namespace policy for instructions to an implementation, and a
  mutable index keyed by digest for facts about the world that move
  while the bytes do not. The four-question test is above.
- 2026-09-27, lineage belongs to the fabric (Willem). The earlier
  proposal of a first-class lineage format inside strongroom is
  withdrawn. A derivation is a call, the fabric owns calls, and the
  store already points at the call through `Version.receipt` without
  describing it. Strongroom gains no lineage format and no reserved
  namespace; retention comes from `pins/`, which exists for it.
- 2026-09-27, a correction to the entry this replaces. An earlier
  finding here claimed the freeze made a version's key set
  unextendable, reading layout 1's `version.md`. Ruling 5 of the
  redesign supersedes that page: a version carries known and unknown
  header pairs, so it can grow. The freeze argument was wrong, and the
  question is moot now that lineage is not a store format.
- 2026-09-27, the plugin's schedule does not set the store's format
  (proposed, awaiting Willem): evidence generation ships against
  today's formats, and the annotation format is ruled once real corpora
  have been measured.
- 2026-09-27, the runtime routes (Willem): Unreal NNE with the ORT
  backend and an onnxruntime Python wheel today, CoreML reached
  directly from Swift, and a linked runtime not ruled out. Models are
  members of the graph on every platform, so open question 3 is
  closed.
- 2026-10-04, at Willem's request: the authorisation sentence under
  "What belongs in an annotation" follows the cryptography manifesto's
  capability model. Its earlier wording put authorisation beside the
  name with revocation by compare-and-swap, which reads as access
  lists and contradicts revocation by non-renewal.

## Open

1. **Which facts the contract carries** for this capability, given
   that strictness is the layer's and mounting is the switch: what a
   workspace ships, its support lifetime, its retention period, and
   whether anything else qualifies as a fact rather than an opinion.
2. **The annotation format**, in full: the rule shape, the binding to a
   tree digest, and whether required keys are a namespace profile.
   Nothing here fixes a field list.
3. **Per-key merge or per-rule replacement** when annotation rules
   nest, and whether the exported form is always the flattened view.
4. **Whether the three destinations hold** as proposed above, and where
   the mutable index keyed by digest lives.
5. **When the fabric's receipt grows the inputs it owes**, listed
   above, and whether the plugin keeps its own records until then.
6. **Manifest cost at dataset scale**, measured with the redesign's
   large-data runs: manifest bytes as a fraction of corpus bytes, and
   whether a corpus wants a different grouping.
7. **Corpus layout ruling**: lay corpora out so the axes that must be
   queried are prefixes.
8. **The `artifact` vocabulary** for a kind that ships through neither
   a Python nor a conan registry, and whether Swift becomes a kind of
   its own.
9. **Retention period** for release documents, which a consumer
   declares because it follows the support lifetime they promise.
