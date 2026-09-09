"""Which runners build platform wheels, read from the member contracts.

A member whose kind publishes platform wheels declares the runner
labels that build them, ``[ci] wheel-platforms`` in its own
``workshop.toml``; the emitted release workflow's wheels job runs one
leg per label in the union over such members, builds that platform's
wheels through cibuildwheel, and hands them to the publish job. A
member of a pure kind carries no such key: its one wheel installs
everywhere and the publish job builds it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from livery.footman import fail
from livery.workshop._contract import load_contract
from livery.workshop._kinds import kind_for, kind_names
from livery.workshop._packages import Package, discover_packages

#: The member contract key naming the runner labels that build its wheels.
WHEEL_PLATFORMS_KEY = "wheel-platforms"


def publishes_platform_wheels(package: Package) -> bool:
    """Whether *package*'s kind builds wheels tagged for one platform."""
    return (
        package.type in kind_names()
        and kind_for(package.type).wheel_identity == "platform"
    )


def declared_wheel_platforms(package: Package) -> list[str]:
    """The runner labels *package* declares under ``[ci] wheel-platforms``.

    Refuses a declaration that is not a non-empty list of labels,
    naming the member and the key: an empty list would emit a wheels
    job with no leg, and a stray value would reach the forge as a
    runner it cannot schedule. Refuses the key on a member of a pure
    kind, whose wheel no platform leg builds. A platform-wheel member
    without the key refuses too: the labels are the member's own fact,
    never guessed from the workspace's check runners.
    """
    contract = load_contract(package.directory / "workshop.toml")
    ci = contract.get("ci") or {}
    declared: Any = ci.get(WHEEL_PLATFORMS_KEY) if isinstance(ci, dict) else None
    where = f"{package.path}: [ci] {WHEEL_PLATFORMS_KEY}"
    if not publishes_platform_wheels(package):
        if declared is not None:
            fail(
                f"{where} is declared on a {package.type} member, whose one"
                " wheel installs on every platform; remove the key"
            )
        return []
    if declared is None:
        fail(
            f"{where} is missing: a {package.type} member names the runner labels"
            ' that build its wheels, like ["ubuntu-latest", "macos-latest",'
            ' "windows-latest"]'
        )
    if not isinstance(declared, list) or not declared:
        fail(
            f'{where} must be a non-empty list of runner labels, like ["ubuntu-latest"]'
        )
    for label in declared:
        if not isinstance(label, str) or not label.strip():
            fail(f"{where} entry {label!r} is not a runner label")
    return [str(label) for label in declared]


def wheel_runners(root: Path) -> list[str]:
    """The wheels job's matrix: every declared label once, in declaration order.

    Empty when no member publishes platform wheels, which is the
    shape a pure workspace's release workflow takes: no wheels job.
    """
    labels: list[str] = []
    for package in discover_packages(root):
        for label in declared_wheel_platforms(package):
            if label not in labels:
                labels.append(label)
    return labels


def member_roster(root: Path) -> list[dict[str, str]]:
    """Every member as the emitter facts carry it: directory, name, kind."""
    return [
        {"dir": package.directory.name, "name": package.name, "kind": package.type}
        for package in discover_packages(root)
    ]


def cibw_build_set(pythons: list[str]) -> str:
    """The cibuildwheel build identifiers for *pythons*, space-separated.

    ``3.14`` becomes ``cp314-*`` and a free-threaded ``3.14t`` becomes
    ``cp314t-*``, so a wheels leg builds exactly the interpreters the
    workspace's python matrix names, on every libc flavour of its
    platform, instead of every CPython cibuildwheel knows.
    """
    identifiers = []
    for version in pythons:
        minor, free_threaded = (
            (version[:-1], True) if version.endswith("t") else (version, False)
        )
        major, _, rest = minor.partition(".")
        identifiers.append(f"cp{major}{rest}{'t' if free_threaded else ''}-*")
    return " ".join(identifiers)
