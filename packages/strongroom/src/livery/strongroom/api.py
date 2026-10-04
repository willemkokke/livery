# Seeded from the template channel (package-python kind) at
# birth; this file is the workspace's own. Edit it directly:
# the template never rewrites it.
"""Content-addressed storage: one address space, every tenant.

The store names bytes by their digest, keeps trees and versions as
blobs in specified formats, moves refs by compare-and-swap with a
record beside each, and hands a real path to a program that needs
one. It knows no tool, no call and no dataset: a consumer composes
the formats and owns its namespaces.

This release carries the formats: [livery.strongroom.api.canonical][] for
the one hashed encoding, [livery.strongroom.api.Digest][] for names,
[livery.strongroom.api.Tree][] and [livery.strongroom.api.Version][] for the
two objects with structure, and [livery.strongroom.api.RefRecord][] and
[livery.strongroom.api.Tombstone][] for the two records beside names. The
standard they implement is the `spec/` directory beside this package,
with the golden vectors the tests run.

[livery.strongroom.api.Store][] is the local store over those formats:
objects landed by digest and verified, refs moved by compare-and-swap
under a per-ref lock with a record beside each, and a mutation class
per namespace. Its refusals are the classes under
[livery.strongroom.api.StoreError][]. A store opened with sources
([livery.strongroom.api.FolderSource][], [livery.strongroom.api.HttpSource][],
[livery.strongroom.api.OriginHint][]) fetches what it lacks through them,
verified and in order, with [livery.strongroom.api.Store.fetch][], and
builds a mirror with [livery.strongroom.api.Store.fill][]. The lifecycle
is the store's too: [livery.strongroom.api.Store.publish_begin][] and
[livery.strongroom.api.Store.publish_commit][] for the fail-closed
publish, [livery.strongroom.api.Store.sweep][] for reachability, and
[livery.strongroom.api.Store.erase][] for the tombstone. The materialiser
is [livery.strongroom.api.Store.view][], [livery.strongroom.api.Store.collect][]
and [livery.strongroom.api.Store.drop_view][]: the only route from a digest
to a path, by the cheapest safe rung, under a doctrine about what may
be removed. [livery.strongroom.testing][] runs the conformance
scenarios under `spec/conformance` against any implementation of the
same API; importing this package does not load it.
"""

from __future__ import annotations

# The literal-False spelling the checkers honour without importing
# `typing`: the names below are what a checker sees, and at runtime
# `__getattr__` serves each from its module on first use, so a program
# that needs one name never pays for the modules it does not touch.
TYPE_CHECKING = False
if TYPE_CHECKING:
    from livery.strongroom._canonical import FormatError as FormatError
    from livery.strongroom._canonical import Value as Value
    from livery.strongroom._canonical import canonical as canonical
    from livery.strongroom._digest import ALGORITHMS as ALGORITHMS
    from livery.strongroom._digest import SHA256 as SHA256
    from livery.strongroom._digest import Algorithm as Algorithm
    from livery.strongroom._digest import Digest as Digest
    from livery.strongroom._digest import HashConstructor as HashConstructor
    from livery.strongroom._digest import Hasher as Hasher
    from livery.strongroom._digest import digest_of as digest_of
    from livery.strongroom._digest import digest_stream as digest_stream
    from livery.strongroom._errors import ErasedObject as ErasedObject
    from livery.strongroom._errors import GroupHalfApplied as GroupHalfApplied
    from livery.strongroom._errors import IntegrityError as IntegrityError
    from livery.strongroom._errors import LockTimeout as LockTimeout
    from livery.strongroom._errors import ManifestError as ManifestError
    from livery.strongroom._errors import MissingObject as MissingObject
    from livery.strongroom._errors import NoSuchPending as NoSuchPending
    from livery.strongroom._errors import NotAGroup as NotAGroup
    from livery.strongroom._errors import NotFastForward as NotFastForward
    from livery.strongroom._errors import RefConflict as RefConflict
    from livery.strongroom._errors import RefProtected as RefProtected
    from livery.strongroom._errors import RefTampered as RefTampered
    from livery.strongroom._errors import StoreError as StoreError
    from livery.strongroom._errors import UnknownNamespace as UnknownNamespace
    from livery.strongroom._errors import WriteOnceRefused as WriteOnceRefused
    from livery.strongroom._fields import Subject as Subject
    from livery.strongroom._fields import SubjectKind as SubjectKind
    from livery.strongroom._fields import check_timestamp as check_timestamp
    from livery.strongroom._groups import Group as Group
    from livery.strongroom._groups import Move as Move
    from livery.strongroom._groups import Transaction as Transaction
    from livery.strongroom._lifecycle import PENDING as PENDING
    from livery.strongroom._lifecycle import PINS as PINS
    from livery.strongroom._lifecycle import Pending as Pending
    from livery.strongroom._lifecycle import SweepReport as SweepReport
    from livery.strongroom._records import RefRecord as RefRecord
    from livery.strongroom._records import Tombstone as Tombstone
    from livery.strongroom._rungs import RUNGS as RUNGS
    from livery.strongroom._rungs import MadeRung as MadeRung
    from livery.strongroom._rungs import Rung as Rung
    from livery.strongroom._rungs import RungUnavailable as RungUnavailable
    from livery.strongroom._sources import FillPolicy as FillPolicy
    from livery.strongroom._sources import FolderSource as FolderSource
    from livery.strongroom._sources import HttpSource as HttpSource
    from livery.strongroom._sources import OriginHint as OriginHint
    from livery.strongroom._sources import Progress as Progress
    from livery.strongroom._sources import Source as Source
    from livery.strongroom._sources import Unreachable as Unreachable
    from livery.strongroom._sources import fetch_url as fetch_url
    from livery.strongroom._store import LAYOUT_VERSION as LAYOUT_VERSION
    from livery.strongroom._store import MANIFEST_NAME as MANIFEST_NAME
    from livery.strongroom._store import MUTATION_CLASSES as MUTATION_CLASSES
    from livery.strongroom._store import OWNED as OWNED
    from livery.strongroom._store import Clock as Clock
    from livery.strongroom._store import Landed as Landed
    from livery.strongroom._store import Manifest as Manifest
    from livery.strongroom._store import MutationClass as MutationClass
    from livery.strongroom._store import Namespace as Namespace
    from livery.strongroom._store import ObjectState as ObjectState
    from livery.strongroom._store import ScrubReport as ScrubReport
    from livery.strongroom._store import Store as Store
    from livery.strongroom._store import now as now
    from livery.strongroom._store import silent as silent
    from livery.strongroom._tree import NAME_BUDGET as NAME_BUDGET
    from livery.strongroom._tree import Entry as Entry
    from livery.strongroom._tree import EntryKind as EntryKind
    from livery.strongroom._tree import Link as Link
    from livery.strongroom._tree import Tree as Tree
    from livery.strongroom._tree import check_name as check_name
    from livery.strongroom._tree import check_target as check_target
    from livery.strongroom._version import Version as Version
    from livery.strongroom._views import ENTRY_RUNGS as ENTRY_RUNGS
    from livery.strongroom._views import PATH_BUDGET as PATH_BUDGET
    from livery.strongroom._views import DropReport as DropReport
    from livery.strongroom._views import EntryRung as EntryRung
    from livery.strongroom._views import ShedReport as ShedReport
    from livery.strongroom._views import ViewEntry as ViewEntry
    from livery.strongroom._views import ViewRecord as ViewRecord

__all__ = [
    "ALGORITHMS",
    "ENTRY_RUNGS",
    "LAYOUT_VERSION",
    "MANIFEST_NAME",
    "MUTATION_CLASSES",
    "NAME_BUDGET",
    "OWNED",
    "PATH_BUDGET",
    "PENDING",
    "PINS",
    "RUNGS",
    "SHA256",
    "Algorithm",
    "Clock",
    "Digest",
    "DropReport",
    "Entry",
    "EntryKind",
    "EntryRung",
    "ErasedObject",
    "FillPolicy",
    "FolderSource",
    "FormatError",
    "Group",
    "GroupHalfApplied",
    "HashConstructor",
    "Hasher",
    "HttpSource",
    "IntegrityError",
    "Landed",
    "Link",
    "LockTimeout",
    "MadeRung",
    "Manifest",
    "ManifestError",
    "MissingObject",
    "Move",
    "MutationClass",
    "Namespace",
    "NoSuchPending",
    "NotAGroup",
    "NotFastForward",
    "ObjectState",
    "OriginHint",
    "Pending",
    "Progress",
    "RefConflict",
    "RefProtected",
    "RefRecord",
    "RefTampered",
    "Rung",
    "RungUnavailable",
    "ScrubReport",
    "ShedReport",
    "Source",
    "Store",
    "StoreError",
    "Subject",
    "SubjectKind",
    "SweepReport",
    "Tombstone",
    "Transaction",
    "Tree",
    "UnknownNamespace",
    "Unreachable",
    "Value",
    "Version",
    "ViewEntry",
    "ViewRecord",
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

__version__ = "0.3.0"


# The module and attribute each lazily served name comes from.
_EXPORTS: dict[str, tuple[str, str]] = {
    "ALGORITHMS": ("livery.strongroom._digest", "ALGORITHMS"),
    "Algorithm": ("livery.strongroom._digest", "Algorithm"),
    "Clock": ("livery.strongroom._store", "Clock"),
    "Digest": ("livery.strongroom._digest", "Digest"),
    "DropReport": ("livery.strongroom._views", "DropReport"),
    "ENTRY_RUNGS": ("livery.strongroom._views", "ENTRY_RUNGS"),
    "Entry": ("livery.strongroom._tree", "Entry"),
    "EntryKind": ("livery.strongroom._tree", "EntryKind"),
    "EntryRung": ("livery.strongroom._views", "EntryRung"),
    "ErasedObject": ("livery.strongroom._errors", "ErasedObject"),
    "FillPolicy": ("livery.strongroom._sources", "FillPolicy"),
    "FolderSource": ("livery.strongroom._sources", "FolderSource"),
    "FormatError": ("livery.strongroom._canonical", "FormatError"),
    "Group": ("livery.strongroom._groups", "Group"),
    "GroupHalfApplied": ("livery.strongroom._errors", "GroupHalfApplied"),
    "HashConstructor": ("livery.strongroom._digest", "HashConstructor"),
    "Hasher": ("livery.strongroom._digest", "Hasher"),
    "HttpSource": ("livery.strongroom._sources", "HttpSource"),
    "IntegrityError": ("livery.strongroom._errors", "IntegrityError"),
    "LAYOUT_VERSION": ("livery.strongroom._store", "LAYOUT_VERSION"),
    "Landed": ("livery.strongroom._store", "Landed"),
    "Link": ("livery.strongroom._tree", "Link"),
    "LockTimeout": ("livery.strongroom._errors", "LockTimeout"),
    "MANIFEST_NAME": ("livery.strongroom._store", "MANIFEST_NAME"),
    "MUTATION_CLASSES": ("livery.strongroom._store", "MUTATION_CLASSES"),
    "MadeRung": ("livery.strongroom._rungs", "MadeRung"),
    "Manifest": ("livery.strongroom._store", "Manifest"),
    "ManifestError": ("livery.strongroom._errors", "ManifestError"),
    "MissingObject": ("livery.strongroom._errors", "MissingObject"),
    "Move": ("livery.strongroom._groups", "Move"),
    "MutationClass": ("livery.strongroom._store", "MutationClass"),
    "NAME_BUDGET": ("livery.strongroom._tree", "NAME_BUDGET"),
    "Namespace": ("livery.strongroom._store", "Namespace"),
    "NoSuchPending": ("livery.strongroom._errors", "NoSuchPending"),
    "NotAGroup": ("livery.strongroom._errors", "NotAGroup"),
    "NotFastForward": ("livery.strongroom._errors", "NotFastForward"),
    "OWNED": ("livery.strongroom._store", "OWNED"),
    "ObjectState": ("livery.strongroom._store", "ObjectState"),
    "OriginHint": ("livery.strongroom._sources", "OriginHint"),
    "PATH_BUDGET": ("livery.strongroom._views", "PATH_BUDGET"),
    "PENDING": ("livery.strongroom._lifecycle", "PENDING"),
    "PINS": ("livery.strongroom._lifecycle", "PINS"),
    "Pending": ("livery.strongroom._lifecycle", "Pending"),
    "Progress": ("livery.strongroom._sources", "Progress"),
    "RUNGS": ("livery.strongroom._rungs", "RUNGS"),
    "RefConflict": ("livery.strongroom._errors", "RefConflict"),
    "RefProtected": ("livery.strongroom._errors", "RefProtected"),
    "RefRecord": ("livery.strongroom._records", "RefRecord"),
    "RefTampered": ("livery.strongroom._errors", "RefTampered"),
    "Rung": ("livery.strongroom._rungs", "Rung"),
    "RungUnavailable": ("livery.strongroom._rungs", "RungUnavailable"),
    "SHA256": ("livery.strongroom._digest", "SHA256"),
    "ScrubReport": ("livery.strongroom._store", "ScrubReport"),
    "ShedReport": ("livery.strongroom._views", "ShedReport"),
    "Source": ("livery.strongroom._sources", "Source"),
    "Store": ("livery.strongroom._store", "Store"),
    "StoreError": ("livery.strongroom._errors", "StoreError"),
    "Subject": ("livery.strongroom._fields", "Subject"),
    "SubjectKind": ("livery.strongroom._fields", "SubjectKind"),
    "SweepReport": ("livery.strongroom._lifecycle", "SweepReport"),
    "Tombstone": ("livery.strongroom._records", "Tombstone"),
    "Transaction": ("livery.strongroom._groups", "Transaction"),
    "Tree": ("livery.strongroom._tree", "Tree"),
    "UnknownNamespace": ("livery.strongroom._errors", "UnknownNamespace"),
    "Unreachable": ("livery.strongroom._sources", "Unreachable"),
    "Value": ("livery.strongroom._canonical", "Value"),
    "Version": ("livery.strongroom._version", "Version"),
    "ViewEntry": ("livery.strongroom._views", "ViewEntry"),
    "ViewRecord": ("livery.strongroom._views", "ViewRecord"),
    "WriteOnceRefused": ("livery.strongroom._errors", "WriteOnceRefused"),
    "canonical": ("livery.strongroom._canonical", "canonical"),
    "check_name": ("livery.strongroom._tree", "check_name"),
    "check_target": ("livery.strongroom._tree", "check_target"),
    "check_timestamp": ("livery.strongroom._fields", "check_timestamp"),
    "digest_of": ("livery.strongroom._digest", "digest_of"),
    "digest_stream": ("livery.strongroom._digest", "digest_stream"),
    "fetch_url": ("livery.strongroom._sources", "fetch_url"),
    "now": ("livery.strongroom._store", "now"),
    "silent": ("livery.strongroom._store", "silent"),
}


def __getattr__(name: str) -> object:
    """Serve a public name from its module on first use, then keep it."""
    found = _EXPORTS.get(name)
    if found is None:
        raise AttributeError(f"module 'livery.strongroom' has no attribute {name!r}")
    import importlib

    module, attribute = found
    value = getattr(importlib.import_module(module), attribute)
    globals()[name] = value
    return value
