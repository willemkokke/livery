# A release is a publish target, for every kind, and what rides there is named

Status: not started. Six phases, each gate-green and mergeable alone.
Issue #810. Issue #811, the example repositories, waits on phase 2.

## The prompt (Willem)

> In general publishing to releases should be an option for most if not
> all package types, potentially as well as to a registry. Willem,
> 2026-09-27

> Should the forge own the publishing to releases, ideally through the
> same registry interface, that is always available for each forge type?
> Willem, 2026-09-27

And on what proves the bytes are the ones that were published:

> Same as strongroom, sha256 and optionally blake3. Willem, 2026-09-27

> Both should be possible. Because what else are we going to do on gitea
> and gitlab? Willem, 2026-09-27

## What already exists

- **The transport, on every forge.** `Repository.release` answers
  `create`, `get`, `upload_asset`, `assets` and `download_asset`, and
  each of the three backends implements it. Nothing about attaching an
  artifact to a release is forge-specific.
- **One route that uses it.** `publish_to_releases` and
  `restore_from_releases` in
  `packages/workshop/src/livery/workshop/_backends/_cpp_conan.py` are
  the two halves for a conan package, and `ConanRegistry` probes a
  release's assets as its served signal.
- **A ladder that reaches it once.** `resolve_registry` answers the
  repository's releases for conan alone, and only when the forge hosts
  no conan registry. Every other kind falls through to the forge's own
  registry or the ecosystem default.
- **One witness for the bytes.** A restore checks the downloaded asset
  against the digest the forge reports. Only the GitHub backend fills
  `Asset.digest`, so a Gitea or GitLab restore prints that the asset
  carries no digest and proceeds.
- **A digest grammar and registry.** `livery.strongroom` parses
  `<algorithm>:<encoded>` in the OCI grammar and holds the registry of
  algorithms that may name bytes, sha256 required. It is already in the
  installed closure, through `packages/toolroom-store`.
- **An ssh signer under a handle.** `ssh_keygen` is a locked tool with a
  record, so `-Y sign` and `-Y verify` need no new dependency.

## What the goal needs

A kind declares its repository's releases as a publish target, on any
forge, instead of a registry or beside one. Every artifact that lands
there is named by a manifest the publisher wrote, and the manifest
carries a signature whose scheme suits the forge it was published on.

## Ground-truth contracts (do not violate)

1. **`livery.forge` imports only the standard library at module import
   time.** Nothing this plan adds enters forge beyond what the protocol
   already has. Sigstore, strongroom and every signing dependency stay
   above it.
2. **Re-running a publish is the recovery procedure.** Every step
   detects done and walks past it, and a killed wave is completed by
   running it again.
3. **A receipt is the tag plus the proof.** The tag is pushed before the
   assets exist, so a tag alone never ends a member; the probe decides.
   That order stays, and the probe gets stricter, never looser.
4. **The conan route's verification does not weaken.** A mismatch
   refuses today and refuses after the generalisation, and an
   unverifiable restore still says so in one line rather than passing
   silently.
5. **One digest grammar.** `livery.strongroom`'s registry is the
   allowlist and its parser is the parser. A second implementation of
   the grammar is not allowed, and neither is a hand-rolled hash name.
6. **Kebab-case for every TOML key this plan defines.** A wrong spelling
   refuses; no migration code.
7. **Failure reasons print verbatim.** A forge's own words carry, and no
   refusal is reduced to a boolean.
8. **Public is what `__init__` re-exports in `__all__`.** Everything
   this plan adds to the workshop is underscore-named unless the export
   test is updated deliberately.
9. **A declared claim gets its machine check in the same phase.** No
   phase ships a property whose test arrives later, with the single
   exception recorded as an open line below.

## Phase 1: one declaration answers a list of targets

The pinning tests come first. The ladder's current properties are the
contract a caller relies on: the env cascade beats the contract table,
a declaration naming the forge's own registry keeps the lane token, a
foreign address never inherits it, python splits read from publish, and
the refusal names the rungs it tried. Those are pinned before the return
type changes.

The declaration becomes an array of tables, one entry per target, and
that is the only form. The bare-string and single-table forms go in the
same change: no checked-in contract declares `[registries]` at all, so
the cost is this workspace's own tests and two sentences in
`packages/workshop/docs/releases.md`, and three accepted forms for one
key is worse than one break before 1.0.

```toml
[[registries.python]]
url = "https://acme.example/simple"
publish = "https://acme.example/upload"
token-env = "ACME_INDEX_TOKEN"

[[registries.python]]
store = "releases"
```

An entry naming another repository's release store reads from it:

```toml
[[registries.python]]
store = "releases"
repository = "acme/widgets"
```

Deliverables:

- `resolve_registries(root, kind)` answers a tuple of `RegistryTarget`
  in declaration order, and every caller reads the list. A workspace
  declaring one target resolves to a one-member tuple and behaves as it
  does today.
- Each entry says what it is. `store = "releases"` marks the repository's
  own release store and carries no address; the address form is assumed
  when `url` or `publish` is present, and an entry carrying both a
  `store` of releases and an address refuses.
- A release entry may name the repository whose store it is through
  `repository`, defaulting to this workspace's own. The entry is the
  source, which is what lets a verification policy sit on it.
- An entry naming a repository other than this workspace's own is read
  only, and the wave never publishes to it: an asset cannot be attached
  to someone else's release. Read-only entries are not publish targets,
  so "every target serves the version" stays a statement about the ones
  this workspace uploads to.
- Each entry names its own credential through `token-env`. One variable
  per artifact kind cannot serve two registries, so the kind's variable
  stays the default for a lone entry and nothing more.
- Reading has a defined order: the first entry is the index a consumer
  resolves from, the rest are extra indexes. `registry_injections`
  renders every entry rather than flattening to one string, which the
  rendered pyproject's `[[tool.uv.index]]` already holds as an array.
- An address in the env cascade replaces the whole list. A machine
  naming an address means publish there, not there as well.
- conan's last-resort rung becomes an ordinary entry. A forge that hosts
  no conan registry answers its release store through the same code a
  declaration takes, not through a branch of its own.

Acceptance, and what proves each. The tests live in
`packages/workshop/tests/test_workshop_registries.py` unless another
file is named; a test's name stays on one line so `grep` finds it.

- `uv run fm check` exits 0.
- The ladder's existing properties are pinned before the change and
  still pass after it: `test_the_env_cascade_beats_the_contract_table`,
  `test_a_declaration_of_the_forges_own_registry_keeps_the_lane_token`,
  `test_a_foreign_address_never_inherits_the_lane_token`,
  `test_the_refusal_names_every_rung_it_tried`.
- The old forms refuse and the refusal shows the array:
  `test_a_bare_string_declaration_refuses_showing_the_array_form`,
  `test_a_single_table_declaration_refuses_showing_the_array_form`.
- Two registries and a release store answer three targets in order:
  `test_three_entries_answer_three_targets_in_declaration_order`.
- A release entry alone answers one target carrying the flag, with the
  repository's page as its url:
  `test_a_release_entry_alone_answers_the_repositorys_own_store`.
- An entry that is both refuses:
  `test_an_entry_with_a_store_and_an_address_refuses_naming_both`.
- Each entry carries its own credential, and the kind's variable serves
  only a lone entry:
  `test_each_entry_reads_the_credential_its_token_env_names`,
  `test_the_kinds_variable_serves_one_entry_and_not_two`.
- An env address replaces the list rather than joining it:
  `test_an_env_address_replaces_every_declared_entry`.
- A foreign release entry resolves as a read source and never as a
  publish target:
  `test_an_entry_naming_another_repository_is_read_only`,
  `test_the_wave_uploads_to_no_foreign_entry`.
- The render carries every index, in order:
  `test_the_render_injects_every_entry_in_declaration_order`, with
  `packages/workshop/tests/test_workshop_render.py` proving the rendered
  `[[tool.uv.index]]` entries match.
- The conan special case is gone and the answer is unchanged:
  `test_a_forge_without_a_conan_registry_answers_its_release_store`.
- A misspelled key refuses naming the key it wants:
  `test_a_misspelled_store_key_refuses_naming_the_key`.
- No caller reads a single target:
  `test_the_module_exports_only_the_plural_resolution`.

## Phase 2: the python kind publishes to a release, and one probe reads any of them

Deliverables:

- `_python.publish_artifact` gains its releases arm: `dist/*` attach to
  the member's receipt tag, and an asset already attached is walked
  past, the same two halves the conan route has.
- A restore half for python: the assets download through the forge API
  into a directory, and the caller installs from it with
  `--find-links`. No index is involved, so a private repository works
  the same as a public one.
- `_ReleaseRegistry`, one probe for every kind, reading a release's
  assets. `ConanRegistry`'s releases arm delegates to it, so there is
  one implementation of the served signal.
- The wave publishes to every target in the set, and cuts the receipt
  only once every target's probe has seen the version. A failed second
  target leaves the first published and the re-run walks past it.
- `registry_for` stops refusing an artifact kind it has no probe for
  when the target is a release store, because the probe no longer
  depends on the artifact.
- `restore_from_releases` stops requiring that the name be a member of
  this workspace: a name resolves to whichever entry serves it, and the
  refusal names the entries it tried when none does.
- The release driver's `publish_url` refusal learns the route: a
  releases-only python target needs no upload endpoint, and the refusal
  still fires for a read-only declaration with no releases.

Acceptance. Exceptional paths first; the tests live in
`packages/workshop/tests/test_workshop_kind_publish.py` and
`packages/workshop/tests/test_workshop_publish.py`.

- `uv run fm check` exits 0.
- A release carrying nothing reads as not served:
  `test_a_release_with_no_assets_reads_as_not_served`.
- A python declaration with no publish address and no releases refuses
  in the words it uses today:
  `test_a_read_only_python_declaration_still_refuses_the_wave`.
- A releases-only python target needs no upload endpoint:
  `test_a_releases_only_target_passes_the_publish_address_check`.
- A second target whose probe never settles fails the member after the
  first target published, and the re-run walks past the first:
  `test_a_member_waits_on_every_target_and_the_rerun_walks_past_the_first`.
- One probe serves both kinds:
  `test_the_release_probe_answers_a_wheel_and_a_conan_cache_alike`.
- A wheel published to a release is installed from the downloaded
  directory: `test_a_wheel_restored_from_a_release_installs_from_find_links`.
- A name no entry serves refuses naming the entries it tried:
  `test_a_name_no_entry_serves_refuses_naming_every_entry_tried`.

## Phase 3: the manifest names what rode there

Deliverables:

- A manifest asset per release, attached after every other asset and
  rewritten by any re-run that adds one. Its entries name each asset and
  its digest, and it names its algorithm once for all of them. sha256 is
  what is written.
- It binds the repository, the tag, the package, the version and the
  commit the wave released from, so it cannot be lifted onto another
  release.
- The probe's served signal becomes the manifest's presence. Matching an
  asset name against an expected prefix goes, which closes the window
  where a half-finished wave read as served.
- The publisher's walk-past compares digests, not names: bytes that
  match are skipped, anything else uploads again, and a name attached
  whose bytes match nothing local refuses. The digest is computed from
  the buffer that is uploaded, not a second read of the path.
- `verify_digest` widens into the manifest's reader. The forge's own
  `Asset.digest` stays a second witness where the forge reports one, and
  a disagreement between the two refuses. An asset on the release that
  the manifest does not list refuses.
- The grammar and the registry come from `livery.strongroom`, through a
  declared `[[depends]]` edge and the matching pyproject constraint.

Acceptance. Exceptional paths first; the tests live in
`packages/workshop/tests/test_workshop_release_manifest.py` unless
another file is named.

- `uv run fm check` exits 0.
- A manifest naming an asset that is not attached refuses:
  `test_a_manifest_naming_an_absent_asset_refuses`.
- A manifest whose binding names another tag refuses before any digest
  is read: `test_a_manifest_bound_to_another_tag_refuses_on_the_binding`.
- An algorithm outside strongroom's registry refuses by name:
  `test_an_unregistered_algorithm_refuses_naming_it`.
- A re-run over an asset whose bytes differ uploads it again instead of
  walking past: `test_a_rerun_replaces_an_asset_whose_bytes_do_not_match`.
- A forge digest disagreeing with the manifest refuses:
  `test_the_forges_digest_and_the_manifest_disagreeing_refuses`.
- An asset the manifest does not list refuses:
  `test_an_unlisted_asset_on_the_release_refuses`.
- A forge that reports no digest verifies from the manifest alone and
  the line says which witness spoke:
  `test_a_forge_without_digests_verifies_from_the_manifest_alone`.
- The manifest lands last:
  `test_the_manifest_is_attached_after_every_other_asset`.
- The edge is declared both ways, which the layering lint proves:
  `uv run fm check` covers it through
  `packages/workshop/tests/test_workshop_layering.py`.

## Phase 4: a signature, and a policy that names who may sign

Deliverables:

- The publisher signs the manifest through the locked `ssh_keygen`
  tool and attaches the signature as a sibling asset whose name carries
  the scheme.
- Verification takes a policy naming the scheme and the expected
  identity, never a boolean. It sits on the entry, as `verify-scheme`
  and `verify-signers`, because the entry is the source of the bytes. A
  valid signature by an identity the policy does not name refuses.
- What a workspace needs for this scheme, and the docs say so: one
  keypair, its public half in a committed `allowed_signers` file under a
  principal of our choosing, and its private half a forge secret the env
  cascade delivers. No account and no external service, which is why
  this scheme is the one the local loop can prove.
- With no `verify-signers` declared, the signers git already verifies
  commits against are the fallback. They are a different trust set from
  "who may sign a release", so they are a default and never the only
  home.
- An unsigned manifest refuses when a policy is declared. With no policy
  declared the restore verifies integrity only and says so in one line.
- The `allowed_signers` file is read from the consumer's configuration.
  A file fetched from the release it verifies is worth nothing, because
  whoever can replace an asset can replace that file, and the refusal
  says where the file is read from.
- The signing key reaches the publisher through the env cascade as a
  `Secret`, so every shown line redacts it.

Acceptance. Exceptional paths first.

- `uv run fm check` exits 0.
- A signature by a signer the policy does not name refuses:
  `test_a_signature_by_an_unnamed_signer_refuses`.
- A declared policy with no signature on the release refuses:
  `test_a_policy_with_no_signature_attached_refuses`.
- A tampered manifest fails the signature before any digest is read:
  `test_a_tampered_manifest_fails_the_signature_first`.
- A missing `allowed_signers` refuses naming the path it read:
  `test_a_missing_allowed_signers_refuses_naming_the_path`.
- No policy declared: the restore proceeds and one line says integrity
  only: `test_no_policy_verifies_integrity_and_says_so`.
- The key never prints: `test_the_signing_key_is_redacted_in_every_shown_line`.

## Phase 5: the keyless scheme, where the forge has an identity

Deliverables:

- A sigstore bundle as the second scheme, attached under its own asset
  name, signed where the runner's identity is one a public issuer
  trusts. The dependency is an optional extra on `livery-workshop`.
- The policy names the workflow identity: repository, ref and workflow
  path. A bundle whose identity does not match refuses.
- No silent downgrade. A release carrying only the key signature does
  not satisfy a policy that asks for keyless, and the refusal names what
  it found.
- A bundle verifies offline against a pinned trust root, so no restore
  waits on a transparency log.
- What this scheme needs, and the docs say so: no account, an
  `id-token: write` permission on the release job, and network reach to
  the certificate authority and the log at signing time. The policy
  names the issuer and the workflow identity, not a key.
- The docs say what the log discloses: signing keyless writes the
  repository, the ref and the workflow path into a public append-only
  log, permanently. That is the point for a public repository and a
  disclosure for a private one.
- Nothing about sigstore enters `livery.forge`, which the layering lint
  proves.

Acceptance. Exceptional paths first.

- `uv run fm check` exits 0.
- A bundle whose identity is not the policy's refuses:
  `test_a_bundle_from_another_workflow_refuses_naming_both_identities`.
- A key-signed release does not satisfy a keyless policy:
  `test_a_key_signature_does_not_satisfy_a_keyless_policy`.
- The extra is absent: the refusal names what to install rather than
  raising on import:
  `test_a_keyless_policy_without_the_extra_refuses_naming_it`.
- Verification reads no network:
  `test_a_bundle_verifies_against_the_pinned_root_offline`.

## Phase 6: the loop proves the key path, and the page says what this is

Deliverables:

- The local loop publishes a member to its scratch repository's releases
  on the Gitea lane and restores it, key-signed. Gitea has no keyless
  identity, so the lane that needs the key is the lane the loop
  exercises.
- `packages/workshop/docs/releases.md` gains the section: how a kind
  declares a releases target, what the manifest holds, what a consumer
  configures to verify a signature, and what this route does not cover.

Acceptance:

- `uv run fm check` exits 0.
- `uv run fm ci.e2e` is green with the new assertions, which pin the
  printed lines the loop reads.
- `uv run fm docs.build` renders `packages/workshop/docs/releases.md`
  with no issues, and the page is already in that package's `nav.toml`.
- The keyless path's live proof is an open line below until the first
  example repository of #811 releases through it.

## Temporary, replaced by

| Scaffolding | Replaced by |
| --- | --- |
| Phase 5's keyless proof, a fabricated bundle | A live keyless release from #811 |
| sha256 as the only algorithm written | A second strongroom registry entry |

## Decision record

- 2026-09-27, Willem: publishing to releases is an option for most or
  all package kinds, possibly as well as to a registry. So resolution
  answers a set, and a receipt completes only when every target serves
  the version.
- 2026-09-27, Willem: the forge owns the transport, through the
  interface every forge type has. It already does, so this plan adds no
  forge capability and the generalisation is above it.
- 2026-09-27, Willem: the digest policy is strongroom's, sha256 with
  blake3 optional. blake3 stays a registry entry and nothing writes a
  manifest in it, because a manifest written in an optional dependency's
  algorithm turns a consumer without that dependency into a failed
  restore.
- 2026-09-27, Willem: both signing schemes are possible, because a
  self-hosted Gitea or GitLab has no keyless identity to use. The scheme
  rides in the asset's name and the policy names which schemes it
  accepts.
- 2026-09-27, Willem: a kind may need to upload to several registries,
  so the declaration is a list of targets and a release store is one
  entry in it, never a flag beside an address. A boolean could not carry
  a second credential at all.
- 2026-09-27, Willem: a release entry may name another repository now,
  because the key is cheap and additive. Such an entry is a read source
  only.
- 2026-09-27: the verification policy sits on the entry rather than on
  the workspace or the kind, since the entry is the source of the bytes.
  Git's configured commit signers are the fallback when an entry
  declares none.
- 2026-09-27, Willem: no livery package declares a releases target
  beside PyPI yet. The route is not speculative though: `livery-cbor`
  grows a native implementation and a python extension over it, and a
  native artifact has no registry rung on every forge, so its releases
  are where it lands.
- 2026-09-27: the floor of the new strongroom edge is the higher of the
  intermediary's declared floor and strongroom's newest release tag,
  read from the tag rather than the working tree, so a fix run between a
  prepare's stamp and its receipt cannot write a floor that is not
  servable.

## Open

1. **Whether the keyless scheme is ever a default.** It writes the
   repository, the ref and the workflow path into a public log that
   cannot be edited. Fine for a public repository, a disclosure for a
   private one, so this plan makes it opt-in and never inferred.
   Owner: Willem.
