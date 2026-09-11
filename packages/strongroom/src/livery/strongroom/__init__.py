# Seeded from the template channel (package-python kind) at
# birth; this file is the workspace's own. Edit it directly:
# the template never rewrites it.
"""Content-addressed storage: one address space, every tenant.

The store names bytes by their digest, keeps trees and versions as
blobs in specified formats, moves refs by compare-and-swap with a
record beside each, and hands a real path to a program that needs
one. It knows no tool, no call and no dataset: a consumer composes
the formats and owns its namespaces.

This release carries the formats: [livery.strongroom.canonical][] for
the one hashed encoding, [livery.strongroom.Digest][] for names,
[livery.strongroom.Tree][] and [livery.strongroom.Version][] for the
two objects with structure, and [livery.strongroom.RefRecord][] and
[livery.strongroom.Tombstone][] for the two records beside names. The
standard they implement is the `spec/` directory beside this package,
with the golden vectors the tests run.

[livery.strongroom.Store][] is the local store over those formats:
objects landed by digest and verified, refs moved by compare-and-swap
under a per-ref lock with a record beside each, and a mutation class
per namespace. Its refusals are the classes under
[livery.strongroom.StoreError][]. A store opened with sources
([livery.strongroom.FolderSource][], [livery.strongroom.HttpSource][],
[livery.strongroom.OriginHint][]) fetches what it lacks through them,
verified and in order, with [livery.strongroom.Store.fetch][], and
builds a mirror with [livery.strongroom.Store.fill][]. The lifecycle
is the store's too: [livery.strongroom.Store.publish_begin][] and
[livery.strongroom.Store.publish_commit][] for the fail-closed
publish, [livery.strongroom.Store.sweep][] for reachability, and
[livery.strongroom.Store.erase][] for the tombstone.
"""

from __future__ import annotations

from livery.strongroom._canonical import FormatError, Value, canonical
from livery.strongroom._digest import (
    ALGORITHMS,
    SHA256,
    Algorithm,
    Digest,
    HashConstructor,
    Hasher,
    digest_of,
    digest_stream,
)
from livery.strongroom._errors import (
    ErasedObject,
    IntegrityError,
    LockTimeout,
    ManifestError,
    MissingObject,
    NoSuchPending,
    NotFastForward,
    RefConflict,
    RefProtected,
    RefTampered,
    StoreError,
    UnknownNamespace,
    WriteOnceRefused,
)
from livery.strongroom._fields import Subject, SubjectKind, check_timestamp
from livery.strongroom._lifecycle import PENDING, PINS, Pending, SweepReport
from livery.strongroom._records import RefRecord, Tombstone
from livery.strongroom._sources import (
    FillPolicy,
    FolderSource,
    HttpSource,
    OriginHint,
    Progress,
    Source,
    Unreachable,
    fetch_url,
)
from livery.strongroom._store import (
    LAYOUT_VERSION,
    MANIFEST_NAME,
    MUTATION_CLASSES,
    OWNED,
    Clock,
    Landed,
    Manifest,
    MutationClass,
    Namespace,
    ObjectState,
    ScrubReport,
    Store,
    now,
    silent,
)
from livery.strongroom._tree import (
    NAME_BUDGET,
    Entry,
    EntryKind,
    Link,
    Tree,
    check_name,
    check_target,
)
from livery.strongroom._version import Version

__all__ = [
    "ALGORITHMS",
    "LAYOUT_VERSION",
    "MANIFEST_NAME",
    "MUTATION_CLASSES",
    "NAME_BUDGET",
    "OWNED",
    "PENDING",
    "PINS",
    "SHA256",
    "Algorithm",
    "Clock",
    "Digest",
    "Entry",
    "EntryKind",
    "ErasedObject",
    "FillPolicy",
    "FolderSource",
    "FormatError",
    "HashConstructor",
    "Hasher",
    "HttpSource",
    "IntegrityError",
    "Landed",
    "Link",
    "LockTimeout",
    "Manifest",
    "ManifestError",
    "MissingObject",
    "MutationClass",
    "Namespace",
    "NoSuchPending",
    "NotFastForward",
    "ObjectState",
    "OriginHint",
    "Pending",
    "Progress",
    "RefConflict",
    "RefProtected",
    "RefRecord",
    "RefTampered",
    "ScrubReport",
    "Source",
    "Store",
    "StoreError",
    "Subject",
    "SubjectKind",
    "SweepReport",
    "Tombstone",
    "Tree",
    "UnknownNamespace",
    "Unreachable",
    "Value",
    "Version",
    "WriteOnceRefused",
    "__version__",
    "canonical",
    "check_name",
    "check_target",
    "check_timestamp",
    "digest_of",
    "digest_stream",
    "fetch_url",
    "now",
    "silent",
]

__version__ = "0.0.0"
