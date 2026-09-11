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
from livery.strongroom._fields import Subject, SubjectKind, check_timestamp
from livery.strongroom._records import RefRecord, Tombstone
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
    "NAME_BUDGET",
    "SHA256",
    "Algorithm",
    "Digest",
    "Entry",
    "EntryKind",
    "FormatError",
    "HashConstructor",
    "Hasher",
    "Link",
    "RefRecord",
    "Subject",
    "SubjectKind",
    "Tombstone",
    "Tree",
    "Value",
    "Version",
    "__version__",
    "canonical",
    "check_name",
    "check_target",
    "check_timestamp",
    "digest_of",
    "digest_stream",
]

__version__ = "0.0.0"
