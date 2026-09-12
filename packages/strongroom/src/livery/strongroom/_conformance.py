"""The conformance harness: the scenarios under `spec/conformance`, run.

A scenario is data: the namespaces a store is opened with and the
steps run against it in order. The harness interprets the steps
against any implementation that speaks the store's API, through
[livery.strongroom.StoreLike][], and reaches the three seams a
scenario needs (a lock written as if held, a platform that refuses
symlinks, a publish that begins mid-sweep) through
[livery.strongroom.Hooks][]. [livery.strongroom.PythonHooks][] is the
Python store's own. A failed step raises
[livery.strongroom.ConformanceFailure][] naming the scenario, the
step and what was found.

Reach for [livery.strongroom.load_scenarios][] and
[livery.strongroom.run_scenario][].
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

import livery.strongroom._groups as groups
import livery.strongroom._lifecycle as lifecycle
import livery.strongroom._rungs as rungs
from livery.strongroom._digest import Digest
from livery.strongroom._errors import (
    ErasedObject,
    GroupHalfApplied,
    LockTimeout,
    MissingObject,
    NoSuchPending,
    NotAGroup,
    NotFastForward,
    RefConflict,
    RefProtected,
    RefTampered,
    UnknownNamespace,
    WriteOnceRefused,
)
from livery.strongroom._fields import Subject
from livery.strongroom._groups import Group
from livery.strongroom._lifecycle import Pending, SweepReport
from livery.strongroom._records import RefRecord, Tombstone
from livery.strongroom._rungs import RungUnavailable
from livery.strongroom._store import Landed, Namespace, ObjectState
from livery.strongroom._tree import Entry, Link, Tree
from livery.strongroom._version import Version
from livery.strongroom._views import DropReport, ViewRecord

LockHolder = Literal["live", "dead", "expired"]
"""Who a scenario says holds a lock: a live process, an exited one, or one long ago."""


class CrashedCommit(Exception):
    """The harness stopped a commit part-way, as a crash would."""


REFUSALS: dict[str, type[Exception]] = {
    "write-once-refused": WriteOnceRefused,
    "not-fast-forward": NotFastForward,
    "conflict": RefConflict,
    "lock-timeout": LockTimeout,
    "tampered": RefTampered,
    "unknown-namespace": UnknownNamespace,
    "missing": MissingObject,
    "erased": ErasedObject,
    "no-such-pending": NoSuchPending,
    "protected": RefProtected,
    "not-a-group": NotAGroup,
    "half-applied": GroupHalfApplied,
    "crashed": CrashedCommit,
}
"""The refusal a scenario may expect, by name, and the class it must raise."""

_WILLEM = Subject("person", "conformance")
_INSTANT = "2026-09-11T12:00:00Z"


class ConformanceFailure(AssertionError):
    """A scenario step did not do what the scenario says."""


class StoreLike(Protocol):
    """What the harness drives: the store's API, as the scenarios use it."""

    root: Path

    def put(self, data: bytes) -> Digest: ...

    def land(self, source: bytes, *, expected: Digest | None = None) -> Landed: ...

    def read(self, digest: Digest) -> bytes: ...

    def state(self, digest: Digest) -> ObjectState: ...

    def path(self, digest: Digest) -> Path: ...

    def erase(
        self, digest: Digest, *, by: Subject, reason: str, receipt: Digest | None = None
    ) -> Tombstone: ...

    def set_ref(
        self,
        namespace: str,
        path: str,
        digest: Digest,
        *,
        previous: Digest | None,
        by: Subject,
    ) -> RefRecord: ...

    def ref(self, namespace: str, path: str) -> Digest | None: ...

    def ref_path(self, namespace: str, path: str) -> Path: ...

    def drop_ref(self, namespace: str, path: str, *, previous: Digest) -> None: ...

    def publish_begin(self, target: Digest, *, by: Subject) -> Pending: ...

    def publish_commit(
        self,
        pending_id: str,
        namespace: str,
        path: str,
        *,
        previous: Digest | None,
        by: Subject,
    ) -> RefRecord: ...

    def retire(self, pending_id: str) -> Digest: ...

    def pendings(self) -> list[str]: ...

    def begin(self, *, by: Subject) -> Group: ...

    def add(
        self,
        group_id: str,
        namespace: str,
        path: str,
        digest: Digest,
        *,
        previous: Digest | None,
    ) -> Group: ...

    def commit(self, group_id: str, *, by: Subject) -> tuple[RefRecord, ...]: ...

    def groups(self) -> list[Group]: ...

    def pin(self, name: str, digest: Digest, *, by: Subject) -> RefRecord: ...

    def unpin(self, name: str) -> Digest: ...

    def sweep(self) -> SweepReport: ...

    def view(self, tree: Digest, at: Path) -> ViewRecord: ...

    def collect(self, at: Path, declared: Iterable[str]) -> Tree: ...

    def drop_view(self, view_id: str) -> DropReport: ...


class Hooks(Protocol):
    """The seams a scenario reaches past the API.

    Each returns a callable that undoes it, run when the scenario
    ends whatever happened.
    """

    def write_lock(
        self, store: StoreLike, namespace: str, path: str, holder: LockHolder
    ) -> None: ...

    def unlock(self, store: StoreLike, namespace: str, path: str) -> None: ...

    def refuse_symlinks(self) -> Callable[[], None]: ...

    def begin_during_sweep(
        self, store: StoreLike, target: Digest
    ) -> Callable[[], None]: ...

    def crash_commit_after(self, applied: int) -> Callable[[], None]: ...


class PythonHooks:
    """The Python store's seams: its lock file, its rungs module, its sweep hook."""

    def write_lock(
        self, store: StoreLike, namespace: str, path: str, holder: LockHolder
    ) -> None:
        """Write the ref's lock file as *holder* would have left it."""
        if holder == "live":
            content = {"pid": os.getpid(), "at": time.time()}
        elif holder == "expired":
            content = {"pid": os.getpid(), "at": time.time() - 3600}
        else:
            child = subprocess.Popen([sys.executable, "-c", "pass"])
            child.wait()
            content = {"pid": child.pid, "at": time.time()}
        lock = _lock_path(store, namespace, path)
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(json.dumps(content))

    def unlock(self, store: StoreLike, namespace: str, path: str) -> None:
        """Remove the ref's lock file."""
        _lock_path(store, namespace, path).unlink()

    def refuse_symlinks(self) -> Callable[[], None]:
        """Make the symlink rung refuse until the returned callable runs."""
        original = rungs.symlink

        def refuse(
            target: str | Path, destination: Path, *, directory: bool = False
        ) -> None:
            raise RungUnavailable(1, "refused by the conformance harness")

        rungs.symlink = refuse

        def restore() -> None:
            rungs.symlink = original

        return restore

    def begin_during_sweep(
        self, store: StoreLike, target: Digest
    ) -> Callable[[], None]:
        """Make the next sweep begin a publish of *target* mid-way, before deleting."""
        original = lifecycle.after_mark

        def begin() -> None:
            store.publish_begin(target, by=_WILLEM)

        lifecycle.after_mark = begin

        def restore() -> None:
            lifecycle.after_mark = original

        return restore

    def crash_commit_after(self, applied: int) -> Callable[[], None]:
        """Make the next commit stop after *applied* moves, as a crash would."""
        original = groups.after_apply

        def crash(count: int) -> None:
            if count == applied:
                raise CrashedCommit(f"the harness stopped the commit after {count}")

        groups.after_apply = crash

        def restore() -> None:
            groups.after_apply = original

        return restore


def _lock_path(store: StoreLike, namespace: str, path: str) -> Path:
    target = store.ref_path(namespace, path)
    return target.with_name(target.name + ".lock")


@dataclass(frozen=True)
class Scenario:
    """One scenario: where it came from, its namespaces, its steps.

    Attributes:
        file: the conformance file's stem, such as `refs`.
        id: the scenario's id within the file.
        namespaces: the namespaces the store is opened with.
        steps: the steps, in order.
    """

    file: str
    id: str
    namespaces: tuple[Namespace, ...]
    steps: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def name(self) -> str:
        """`<file>:<id>`, the scenario's name in a report."""
        return f"{self.file}:{self.id}"


def load_scenarios(directory: Path) -> list[Scenario]:
    """Every scenario in every `*.json` under *directory*, files sorted."""
    found: list[Scenario] = []
    for file in sorted(directory.glob("*.json")):
        loaded = json.loads(file.read_text("utf-8"))
        for scenario in loaded["scenarios"]:
            namespaces = tuple(
                Namespace(ns["name"], ns["mutation"]) for ns in scenario["namespaces"]
            )
            found.append(
                Scenario(
                    file.stem, scenario["id"], namespaces, tuple(scenario["steps"])
                )
            )
    return found


def run_scenario(
    scenario: Scenario,
    open_store: Callable[[tuple[Namespace, ...]], StoreLike],
    hooks: Hooks,
    base: Path,
) -> None:
    """Run one scenario against a fresh store from *open_store*.

    Args:
        scenario: the scenario.
        open_store: creates the store under test with the scenario's
            namespaces declared.
        hooks: the implementation's seams.
        base: a directory for the views the scenario fills.

    Raises:
        ConformanceFailure: naming the scenario, the step and what was
            found, at the first step that does not do what the
            scenario says.
    """
    store = open_store(scenario.namespaces)
    run = _Run(scenario, store, hooks, base)
    try:
        for index, step in enumerate(scenario.steps):
            run.step(index, step)
    finally:
        run.restore()


class _Run:
    def __init__(
        self, scenario: Scenario, store: StoreLike, hooks: Hooks, base: Path
    ) -> None:
        self.scenario = scenario
        self.store = store
        self.hooks = hooks
        self.base = base
        self.names: dict[str, Digest] = {}
        self.pendings: dict[str, str] = {}
        self.views: dict[str, ViewRecord] = {}
        self.restores: list[Callable[[], None]] = []
        self.where = ""

    def restore(self) -> None:
        for undo in reversed(self.restores):
            undo()
        self.restores.clear()

    def step(self, index: int, step: dict[str, Any]) -> None:
        op = step["op"]
        self.where = f"{self.scenario.name} step {index} ({op})"
        handler = getattr(self, "op_" + op.replace("-", "_"), None)
        if handler is None:
            raise ConformanceFailure(f"{self.where}: no such operation")
        handler(step)

    def fail(self, message: str) -> ConformanceFailure:
        return ConformanceFailure(f"{self.where}: {message}")

    def check(self, condition: bool, message: str) -> None:
        if not condition:
            raise self.fail(message)

    def digest(self, name: str | None) -> Digest | None:
        if name is None:
            return None
        if name == "MISSING":
            return Digest("sha256", "0" * 64)
        return self.names[name]

    def named(self, name: str) -> Digest:
        digest = self.digest(name)
        if digest is None:
            raise self.fail(f"{name!r} names nothing")
        return digest

    def expecting(self, step: dict[str, Any], action: Callable[[], object]) -> None:
        expect = step["expect"]
        if expect == "ok":
            action()
            return
        refusal = REFUSALS.get(expect)
        if refusal is None:
            raise self.fail(f"unknown expectation {expect!r}")
        try:
            action()
        except refusal:
            return
        except Exception as error:
            raise self.fail(
                f"expected {expect}, got {type(error).__name__}: {error}"
            ) from None
        raise self.fail(f"expected {expect}, nothing was refused")

    # Objects.

    def op_put(self, step: dict[str, Any]) -> None:
        self.names[step["as"]] = self.store.put(step["data"].encode("utf-8"))

    def op_land(self, step: dict[str, Any]) -> None:
        self.expecting(step, lambda: self.store.land(step["data"].encode("utf-8")))

    def op_tree(self, step: dict[str, Any]) -> None:
        entries: list[Entry | Link] = []
        for name, blob in step["entries"].items():
            digest = self.named(blob)
            entries.append(Entry(name, "blob", digest, len(self.store.read(digest))))
        for name, subtree in step.get("subtrees", {}).items():
            digest = self.named(subtree)
            entries.append(Entry(name, "tree", digest, len(self.store.read(digest))))
        for name, target in step.get("links", {}).items():
            entries.append(Link(name, target))
        self.names[step["as"]] = self.store.put(Tree.of(entries).encode())

    def op_version(self, step: dict[str, Any]) -> None:
        tree = self.named(step["tree"])
        parents = tuple(self.named(parent) for parent in step["parents"])
        message = step.get("message", "")
        version = Version(tree, parents, _WILLEM, _INSTANT, message)
        self.names[step["as"]] = self.store.put(version.encode())

    def op_state(self, step: dict[str, Any]) -> None:
        found = self.store.state(self.named(step["digest"]))
        self.check(found == step["expect"], f"state is {found}, not {step['expect']}")

    def op_path(self, step: dict[str, Any]) -> None:
        self.expecting(step, lambda: self.store.path(self.named(step["digest"])))

    def op_erase(self, step: dict[str, Any]) -> None:
        self.store.erase(self.named(step["digest"]), by=_WILLEM, reason=step["reason"])

    # Refs.

    def op_set(self, step: dict[str, Any]) -> None:
        digest = self.named(step["digest"])
        self.expecting(
            step,
            lambda: self.store.set_ref(
                step["namespace"],
                step["path"],
                digest,
                previous=self.digest(step["previous"]),
                by=_WILLEM,
            ),
        )

    def op_ref(self, step: dict[str, Any]) -> None:
        if step["expect"] == "tampered":
            self.expecting(
                step, lambda: self.store.ref(step["namespace"], step["path"])
            )
            return
        found = self.store.ref(step["namespace"], step["path"])
        wanted = self.digest(step["expect"])
        self.check(found == wanted, f"ref names {found}, not {wanted}")

    def op_drop(self, step: dict[str, Any]) -> None:
        previous = self.named(step["previous"])
        self.expecting(
            step,
            lambda: self.store.drop_ref(
                step["namespace"], step["path"], previous=previous
            ),
        )

    def op_tamper(self, step: dict[str, Any]) -> None:
        target = self.store.ref_path(step["namespace"], step["path"])
        target.write_text(f"{self.named(step['digest'])}\n")

    def op_lock(self, step: dict[str, Any]) -> None:
        self.hooks.write_lock(
            self.store, step["namespace"], step["path"], step["holder"]
        )

    def op_unlock(self, step: dict[str, Any]) -> None:
        self.hooks.unlock(self.store, step["namespace"], step["path"])

    # The lifecycle.

    def op_begin(self, step: dict[str, Any]) -> None:
        target = self.named(step["target"])

        def begin() -> None:
            self.pendings[step["as"]] = self.store.publish_begin(target, by=_WILLEM).id

        self.expecting(step, begin)

    def op_commit(self, step: dict[str, Any]) -> None:
        self.expecting(
            step,
            lambda: self.store.publish_commit(
                self.pendings[step["pending"]],
                step["namespace"],
                step["path"],
                previous=self.digest(step["previous"]),
                by=_WILLEM,
            ),
        )

    def op_retire(self, step: dict[str, Any]) -> None:
        self.expecting(step, lambda: self.store.retire(self.pendings[step["pending"]]))

    def op_pending(self, step: dict[str, Any]) -> None:
        expected = sorted(self.pendings[name] for name in step["expect"])
        found = self.store.pendings()
        self.check(found == expected, f"pending are {found}, not {expected}")

    def op_group_begin(self, step: dict[str, Any]) -> None:
        group = self.store.begin(by=_WILLEM)
        self.pendings[step["as"]] = group.id
        if "manifest_as" in step:
            self.names[step["manifest_as"]] = group.manifest

    def op_group_add(self, step: dict[str, Any]) -> None:
        digest = self.named(step["digest"])

        def add() -> None:
            group = self.store.add(
                self.pendings[step["group"]],
                step["namespace"],
                step["path"],
                digest,
                previous=self.digest(step["previous"]),
            )
            if "manifest_as" in step:
                self.names[step["manifest_as"]] = group.manifest

        self.expecting(step, add)

    def op_group_commit(self, step: dict[str, Any]) -> None:
        after = step.get("crash_after")
        if after is not None:
            self.restores.append(self.hooks.crash_commit_after(after))
        try:
            self.expecting(
                step,
                lambda: self.store.commit(self.pendings[step["group"]], by=_WILLEM),
            )
        finally:
            self.restore()

    def op_group_retire(self, step: dict[str, Any]) -> None:
        self.expecting(step, lambda: self.store.retire(self.pendings[step["group"]]))

    def op_groups(self, step: dict[str, Any]) -> None:
        expected = sorted(self.pendings[name] for name in step["expect"])
        found = [group.id for group in self.store.groups()]
        self.check(found == expected, f"groups are {found}, not {expected}")

    def op_pin(self, step: dict[str, Any]) -> None:
        self.store.pin(step["name"], self.named(step["digest"]), by=_WILLEM)

    def op_unpin(self, step: dict[str, Any]) -> None:
        self.expecting(step, lambda: self.store.unpin(step["name"]))

    def op_sweep(self, step: dict[str, Any]) -> None:
        during = step.get("begin_during")
        if during is not None:
            self.restores.append(
                self.hooks.begin_during_sweep(self.store, self.named(during))
            )
        report = self.store.sweep()
        self.restore()
        expected = {self.named(name) for name in step["expect_removed"]}
        found = set(report.removed)
        self.check(
            found == expected,
            f"removed {sorted(map(str, found))}, not {sorted(map(str, expected))}",
        )

    # The materialiser.

    def op_view(self, step: dict[str, Any]) -> None:
        tree = self.named(step["tree"])
        at = self.base / step["at"]

        def make() -> None:
            self.views[step["as"]] = self.store.view(tree, at)

        self.expecting({"expect": step.get("expect", "ok")}, make)

    def op_entry(self, step: dict[str, Any]) -> None:
        record = self.views[step["view"]]
        matching = [e for e in record.entries if e.path == step["path"]]
        self.check(len(matching) == 1, f"no entry {step['path']!r} in the view")
        entry = matching[0]
        self.check(
            entry.rung in step["expect_rung"],
            f"entry {entry.path!r} was made by {entry.rung}, not one of"
            f" {step['expect_rung']}",
        )

    def op_collect(self, step: dict[str, Any]) -> None:
        tree = self.store.collect(self.base / step["at"], step["declared"])
        wanted = self.named(step["expect"])
        self.check(tree.digest() == wanted, f"collected {tree.digest()}, not {wanted}")

    def op_drop_view(self, step: dict[str, Any]) -> None:
        report = self.store.drop_view(self.views[step["view"]].id)
        self.check(
            list(report.left) == step["expect_left"],
            f"drop left {list(report.left)}, not {step['expect_left']}",
        )

    def op_stray(self, step: dict[str, Any]) -> None:
        path = self.base / step["at"] / step["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not the view's")

    def op_exists(self, step: dict[str, Any]) -> None:
        path = self.base / step["at"] / step["path"]
        found = path.is_symlink() or path.exists()
        self.check(found is step["expect"], f"{step['path']!r} exists is {found}")

    def op_refuse_symlinks(self, step: dict[str, Any]) -> None:
        self.restores.append(self.hooks.refuse_symlinks())
