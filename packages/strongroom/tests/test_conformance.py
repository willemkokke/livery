"""The conformance scenarios under spec/conformance, run against the store."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from livery.strongroom import (
    Digest,
    Entry,
    ErasedObject,
    Link,
    LockTimeout,
    MissingObject,
    Namespace,
    NoSuchPending,
    NotFastForward,
    RefConflict,
    RefProtected,
    RefTampered,
    RungUnavailable,
    Store,
    Subject,
    Tree,
    UnknownNamespace,
    Version,
    ViewRecord,
    WriteOnceRefused,
    _lifecycle,
    _rungs,
)

CONFORMANCE = Path(__file__).resolve().parents[1] / "spec" / "conformance"
WILLEM = Subject("person", "willem")

# Refusals are matched most specific first: every refined conflict is a
# RefConflict too, so the plain class comes last.
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
}


def _scenarios() -> list[Any]:
    found: list[Any] = []
    for file in sorted(CONFORMANCE.glob("*.json")):
        loaded = json.loads(file.read_text("utf-8"))
        for scenario in loaded["scenarios"]:
            found.append(pytest.param(scenario, id=f"{file.stem}:{scenario['id']}"))
    return found


class Harness:
    """Runs one scenario's steps against a fresh store."""

    def __init__(self, store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
        self.store = store
        self.monkeypatch = monkeypatch
        self.names: dict[str, Digest] = {}
        self.pendings: dict[str, str] = {}
        self.views: dict[str, ViewRecord] = {}
        self.base = store.root.parent

    def digest(self, name: str | None) -> Digest | None:
        if name is None:
            return None
        if name == "MISSING":
            return Digest("sha256", "0" * 64)
        return self.names[name]

    def run(self, step: dict[str, Any]) -> None:
        getattr(self, "op_" + step["op"].replace("-", "_"))(step)

    def expecting(self, step: dict[str, Any], action: Any) -> None:
        expect = step["expect"]
        if expect == "ok":
            action()
            return
        with pytest.raises(REFUSALS[expect]):
            action()

    # Objects.

    def op_put(self, step: dict[str, Any]) -> None:
        self.names[step["as"]] = self.store.put(step["data"].encode("utf-8"))

    def op_land(self, step: dict[str, Any]) -> None:
        self.expecting(step, lambda: self.store.land(step["data"].encode("utf-8")))

    def op_tree(self, step: dict[str, Any]) -> None:
        entries: list[Entry | Link] = []
        for name, blob in step["entries"].items():
            digest = self.names[blob]
            entries.append(Entry(name, "blob", digest, len(self.store.read(digest))))
        for name, subtree in step.get("subtrees", {}).items():
            digest = self.names[subtree]
            entries.append(Entry(name, "tree", digest, len(self.store.read(digest))))
        for name, target in step.get("links", {}).items():
            entries.append(Link(name, target))
        self.names[step["as"]] = self.store.put(Tree.of(entries).encode())

    def op_version(self, step: dict[str, Any]) -> None:
        tree = self.names[step["tree"]]
        parents = tuple(self.names[parent] for parent in step["parents"])
        message = step.get("message", "")
        version = Version(tree, parents, WILLEM, "2026-09-11T12:00:00Z", message)
        self.names[step["as"]] = self.store.put(version.encode())

    def op_state(self, step: dict[str, Any]) -> None:
        assert self.store.state(self.names[step["digest"]]) == step["expect"]

    def op_path(self, step: dict[str, Any]) -> None:
        self.expecting(step, lambda: self.store.path(self.names[step["digest"]]))

    def op_erase(self, step: dict[str, Any]) -> None:
        self.store.erase(self.names[step["digest"]], by=WILLEM, reason=step["reason"])

    # Refs.

    def op_set(self, step: dict[str, Any]) -> None:
        digest = self.names[step["digest"]]
        self.expecting(
            step,
            lambda: self.store.set_ref(
                step["namespace"],
                step["path"],
                digest,
                previous=self.digest(step["previous"]),
                by=WILLEM,
            ),
        )

    def op_ref(self, step: dict[str, Any]) -> None:
        if step["expect"] == "tampered":
            with pytest.raises(RefTampered):
                self.store.ref(step["namespace"], step["path"])
        else:
            found = self.store.ref(step["namespace"], step["path"])
            assert found == self.digest(step["expect"])

    def op_drop(self, step: dict[str, Any]) -> None:
        previous = self.digest(step["previous"])
        assert previous is not None
        self.expecting(
            step,
            lambda: self.store.drop_ref(
                step["namespace"], step["path"], previous=previous
            ),
        )

    def op_tamper(self, step: dict[str, Any]) -> None:
        target = self.store.ref_path(step["namespace"], step["path"])
        target.write_text(f"{self.names[step['digest']]}\n")

    def op_lock(self, step: dict[str, Any]) -> None:
        holder = step["holder"]
        if holder == "live":
            content = {"pid": os.getpid(), "at": time.time()}
        elif holder == "expired":
            content = {"pid": os.getpid(), "at": time.time() - 3600}
        else:
            assert holder == "dead", holder
            child = subprocess.Popen([sys.executable, "-c", "pass"])
            child.wait()
            content = {"pid": child.pid, "at": time.time()}
        lock = self.lock_path(step)
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(json.dumps(content))

    def op_unlock(self, step: dict[str, Any]) -> None:
        self.lock_path(step).unlink()

    def lock_path(self, step: dict[str, Any]) -> Path:
        target = self.store.ref_path(step["namespace"], step["path"])
        return target.with_name(target.name + ".lock")

    # The lifecycle.

    def op_begin(self, step: dict[str, Any]) -> None:
        target = self.digest(step["target"])
        assert target is not None

        def begin() -> None:
            self.pendings[step["as"]] = self.store.publish_begin(target, by=WILLEM).id

        self.expecting(step, begin)

    def op_commit(self, step: dict[str, Any]) -> None:
        self.expecting(
            step,
            lambda: self.store.publish_commit(
                self.pendings[step["pending"]],
                step["namespace"],
                step["path"],
                previous=self.digest(step["previous"]),
                by=WILLEM,
            ),
        )

    def op_retire(self, step: dict[str, Any]) -> None:
        self.expecting(step, lambda: self.store.retire(self.pendings[step["pending"]]))

    def op_pending(self, step: dict[str, Any]) -> None:
        expected = sorted(self.pendings[name] for name in step["expect"])
        assert self.store.pendings() == expected

    def op_pin(self, step: dict[str, Any]) -> None:
        self.store.pin(step["name"], self.names[step["digest"]], by=WILLEM)

    def op_unpin(self, step: dict[str, Any]) -> None:
        self.expecting(step, lambda: self.store.unpin(step["name"]))

    def op_sweep(self, step: dict[str, Any]) -> None:
        during = step.get("begin_during")
        if during is not None:
            target = self.names[during]

            def begin_now() -> None:
                self.store.publish_begin(target, by=WILLEM)

            self.monkeypatch.setattr(_lifecycle, "after_mark", begin_now)
        report = self.store.sweep()
        self.monkeypatch.setattr(_lifecycle, "after_mark", lambda: None)
        expected = {self.names[name] for name in step["expect_removed"]}
        assert set(report.removed) == expected

    # The materialiser.

    def op_view(self, step: dict[str, Any]) -> None:
        tree = self.names[step["tree"]]
        at = self.base / step["at"]

        def make() -> None:
            self.views[step["as"]] = self.store.view(tree, at)

        if "expect" in step:
            self.expecting(step, make)
        else:
            make()

    def op_entry(self, step: dict[str, Any]) -> None:
        record = self.views[step["view"]]
        entry = next(e for e in record.entries if e.path == step["path"])
        assert entry.rung in step["expect_rung"], entry

    def op_collect(self, step: dict[str, Any]) -> None:
        tree = self.store.collect(self.base / step["at"], step["declared"])
        assert tree.digest() == self.names[step["expect"]]

    def op_drop_view(self, step: dict[str, Any]) -> None:
        report = self.store.drop_view(self.views[step["view"]].id)
        assert list(report.left) == step["expect_left"]

    def op_stray(self, step: dict[str, Any]) -> None:
        path = self.base / step["at"] / step["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not the view's")

    def op_exists(self, step: dict[str, Any]) -> None:
        path = self.base / step["at"] / step["path"]
        assert (path.is_symlink() or path.exists()) is step["expect"]

    def op_refuse_symlinks(self, step: dict[str, Any]) -> None:
        def refuse(target: Any, destination: Any, **kwargs: Any) -> None:
            raise RungUnavailable(1, "refused by the harness")

        self.monkeypatch.setattr(_rungs, "symlink", refuse)


@pytest.mark.parametrize("scenario", _scenarios())
def test_scenario(
    scenario: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    namespaces = [
        Namespace(ns["name"], ns["mutation"]) for ns in scenario["namespaces"]
    ]
    store = Store.create(
        tmp_path / "store", namespaces=namespaces, lock_timeout=0.1, lock_stale=60
    )
    harness = Harness(store, monkeypatch)
    for step in scenario["steps"]:
        harness.run(step)


def test_every_conformance_file_has_a_scenario() -> None:
    assert sorted(path.name for path in CONFORMANCE.glob("*.json")) == [
        "lifecycle.json",
        "refs.json",
        "views.json",
    ]
