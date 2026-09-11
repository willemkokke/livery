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
    LockTimeout,
    Namespace,
    NotFastForward,
    RefConflict,
    RefTampered,
    Store,
    Subject,
    UnknownNamespace,
    Version,
    WriteOnceRefused,
)

CONFORMANCE = Path(__file__).resolve().parents[1] / "spec" / "conformance"
WILLEM = Subject("person", "willem")

# Refusals are matched most specific first: every refined conflict is a
# RefConflict too, so the plain class comes last.
REFUSALS: list[tuple[str, type[Exception]]] = [
    ("write-once-refused", WriteOnceRefused),
    ("not-fast-forward", NotFastForward),
    ("conflict", RefConflict),
    ("lock-timeout", LockTimeout),
    ("tampered", RefTampered),
    ("unknown-namespace", UnknownNamespace),
]


def _scenarios() -> list[Any]:
    loaded = json.loads((CONFORMANCE / "refs.json").read_text("utf-8"))
    return [
        pytest.param(scenario, id=scenario["id"]) for scenario in loaded["scenarios"]
    ]


@pytest.mark.parametrize("scenario", _scenarios())
def test_scenario(scenario: dict[str, Any], tmp_path: Path) -> None:
    namespaces = [
        Namespace(ns["name"], ns["mutation"]) for ns in scenario["namespaces"]
    ]
    store = Store.create(
        tmp_path, namespaces=namespaces, lock_timeout=0.1, lock_stale=60
    )
    names: dict[str, Digest] = {}

    def digest(name: str | None) -> Digest | None:
        return None if name is None else names[name]

    for step in scenario["steps"]:
        op = step["op"]
        if op == "put":
            names[step["as"]] = store.put(step["data"].encode("utf-8"))
        elif op == "version":
            tree = names[step["tree"]]
            parents = tuple(names[parent] for parent in step["parents"])
            version = Version(tree, parents, WILLEM, "2026-09-11T12:00:00Z", "")
            names[step["as"]] = store.put(version.encode())
        elif op == "set":
            _run_set(store, step, digest)
        elif op == "ref":
            if step["expect"] == "tampered":
                with pytest.raises(RefTampered):
                    store.ref(step["namespace"], step["path"])
            else:
                assert store.ref(step["namespace"], step["path"]) == digest(
                    step["expect"]
                )
        elif op == "lock":
            _write_lock(store, step)
        elif op == "unlock":
            _lock_path(store, step).unlink()
        else:
            assert op == "tamper", op
            target = store.ref_path(step["namespace"], step["path"])
            target.write_text(f"{names[step['digest']]}\n")


def _run_set(store: Store, step: dict[str, Any], digest: Any) -> None:
    expect = step["expect"]

    def move() -> None:
        store.set_ref(
            step["namespace"],
            step["path"],
            digest(step["digest"]),
            previous=digest(step["previous"]),
            by=WILLEM,
        )

    if expect == "ok":
        move()
        return
    for name, error in REFUSALS:
        if name == expect:
            with pytest.raises(error):
                move()
            return
    raise AssertionError(f"unknown expectation {expect!r}")


def _lock_path(store: Store, step: dict[str, Any]) -> Path:
    target = store.ref_path(step["namespace"], step["path"])
    return target.with_name(target.name + ".lock")


def _write_lock(store: Store, step: dict[str, Any]) -> None:
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
    lock = _lock_path(store, step)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps(content))
