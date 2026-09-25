"""The ``tools`` group: declare a tool, lock the versions, upgrade one, write the stubs.

Every verb resolves against the catalogue `[tools] index` names and
writes `tools.lock` at the root; a lock that cannot be met refuses
naming the tool, each floor with the site that declared it, and the
host no eligible version has. Each lock verb then writes the stubs
under `typings/`, and `restub` writes them alone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from livery.footman import doc, fail, group

if TYPE_CHECKING:
    from pathlib import Path

    from livery.toolroom.store import Lock

tools = group(
    "tools", help="The tools the workspace requires: declare, lock, upgrade, restub"
)


def _root() -> Path:
    from livery.workshop._layers import workspace_root

    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    return root


def _report(lock: Lock, moved: tuple[str, ...] = ()) -> None:
    from livery.toolroom.store import LOCK_FILE

    for name in sorted(lock.tools):
        mark = "  moved" if name in moved else ""
        print(f"  {name} {lock.tools[name].version}{mark}")
    print(f"  {LOCK_FILE}: {len(lock.tools)} tool(s) on {', '.join(lock.hosts)}")


@tools.task(name="lock")
def tools_lock() -> None:
    """Resolve every site's requirements and write `tools.lock`.

    An entry the lock already holds stands while it still satisfies
    every floor and resolves on every locked host; a tool no site
    requires any more leaves the lock. Nothing on a machine changes:
    the lock says what a checkout installs, and `sync` installs it.
    """
    from livery.workshop._tools import stub_lines, write_lock

    root = _root()
    _report(write_lock(root))
    for line in stub_lines(root, strict=False):
        print(line)


@tools.task(name="add")
def tools_add(
    requirement: Annotated[str, doc("the tool, `name` or `name>=floor`")],
) -> None:
    """Declare a tool at the project site, lock it, and materialise it.

    The requirement joins `[tools] requires` in the root `workshop.toml`
    unless it is already there, the lock is resolved with it, and the
    tool is supplied through the store on this machine with its receipt
    written. A spelling that is not a requirement refuses before
    anything is written.
    """
    from livery.toolroom.store import Requirement
    from livery.workshop._tools import declare, materialise, stub_lines, write_lock

    root = _root()
    if declare(root, requirement):
        print(f"  workshop.toml: [tools] requires {requirement}")
    else:
        print(f"  workshop.toml: {requirement} was declared already")
    lock = write_lock(root)
    _report(lock)
    name = Requirement.parse(requirement).name
    (made,) = materialise(root, (name,))
    assert made.receipt is not None  # strict: a refusal never reaches here
    where = made.receipt.tool_dir
    state = "installed" if made.installed else "present"
    print(f"  {name} {made.receipt.version}: {state} at {where}, receipt written")
    for line in stub_lines(root, strict=False):
        print(line)


@tools.task(name="upgrade")
def tools_upgrade(
    names: Annotated[
        list[str], doc("the tools to move to their newest eligible version")
    ],
) -> None:
    """Move the named tools' lock entries to the newest version that satisfies.

    An upgrade is an act on the repository: every package runs the
    version the lock names, so one entry moves and every package with
    it. The other entries stand.
    """
    from livery.workshop._tools import current_lock, stub_lines, write_lock

    root = _root()
    before = current_lock(root)
    lock = write_lock(root, upgrade=tuple(names))
    for name in names:
        if name not in lock.tools:
            held = ", ".join(sorted(lock.tools)) or "nothing"
            fail(f"{name} is not a tool the sites require; the lock holds {held}")
    moved = tuple(
        name
        for name in names
        if before is None
        or name not in before.tools
        or before.tools[name].version != lock.tools[name].version
    )
    _report(lock, moved)
    if not moved:
        listed = ", ".join(names)
        print(f"  nothing moved: {listed} already at the newest eligible version")
    for line in stub_lines(root, strict=False):
        print(line)


@tools.task(name="materialise")
def tools_materialise(
    offline: Annotated[
        bool, doc("supply from the machine's store and its sources only")
    ] = False,
) -> None:
    """Supply every locked tool through the store, write the receipts and the stubs.

    What `sync` does for the tools alone: the bundle the sites require
    lands on this machine, a receipt per tool says what reached PATH,
    and the stubs follow. A tool the store cannot supply is reported
    and the others are supplied. The entry script runs it on every CI
    job before it persists the environment, so the receipts' paths are
    on PATH when the gate runs; a checkout with no `tools.lock` has
    nothing to materialise and says so.
    """
    from livery.workshop._sync import materialise_tools

    for line in materialise_tools(_root(), offline=offline):
        print(line)


@tools.task(name="restub")
def tools_restub(
    offline: Annotated[
        bool, doc("read the index from the machine's store only")
    ] = False,
) -> None:
    """Write the stubs the catalogue offers into `typings/`.

    One stub per tool the lock holds, at the locked version, as
    `livery.toolroom.stubs` modules, and `livery.toolroom.handles`
    beside them declaring the handles for the tools package's index to
    import. The four checkers read `typings/` first,
    so a locked tool's handle completes with its own verbs and flags;
    a tool the workspace does not deploy gets no stub and types as a
    bare `Tool`. `sync` and the lock verbs write them too; this writes
    them alone, offline from the machine's store with `--offline`.
    """
    from livery.workshop._tools import stub_lines

    for line in stub_lines(_root(), offline=offline):
        print(line)
