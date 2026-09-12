"""The store's refusals, one class per thing a caller decides on.

Every error message names what was found and what was expected, so a
caller prints it and a reader knows the next action. A caller that
only wants to know "did the store refuse" catches
[livery.strongroom.StoreError][]; one that retries on a lost race
catches [livery.strongroom.RefConflict][].
"""

from __future__ import annotations


class StoreError(Exception):
    """The store refused an operation; the message says why."""


class ManifestError(StoreError):
    """The root manifest is missing, malformed, or of another store."""


class IntegrityError(StoreError):
    """Bytes do not match their name: a landing mismatch or a corrupt object."""


class MissingObject(StoreError):
    """The named object is not in the store."""


class ErasedObject(StoreError):
    """The named object was erased; its tombstone says why."""


class UnknownNamespace(StoreError):
    """A ref names a namespace the store was not opened with."""


class RefConflict(StoreError):
    """A compare-and-swap lost: the ref does not name the expected previous digest."""


class WriteOnceRefused(RefConflict):
    """A write-once ref already names a different digest."""


class NotFastForward(RefConflict):
    """A monotone ref's new target does not descend from its current one."""


class RefTampered(StoreError):
    """A ref and its record disagree: an out-of-band edit or a torn update."""


class LockTimeout(StoreError):
    """A live lock on the ref was not released within the timeout."""


class NoSuchPending(StoreError):
    """The named pending publish does not exist: retired, committed, or never begun."""


class NotAGroup(StoreError):
    """The pending ref is a single publish's, not a group's."""


class GroupHalfApplied(StoreError):
    """A commit already applied part of the group; only a commit finishes it."""


class RefProtected(StoreError):
    """The ref's mutation class does not allow dropping it."""
