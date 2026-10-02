"""The ``tools`` group: declare a tool, lock the versions, upgrade one, write the stubs.

Every verb resolves against the catalogue `[tools] index` names and
writes `tools.lock` at the root; a lock that cannot be met refuses
naming the tool, each floor with the site that declared it, and the
host no eligible version has. Each lock verb then writes the stubs
under `typings/`, and `restub` writes them alone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from livery.footman.api import doc, fail, group, prog

if TYPE_CHECKING:
    from pathlib import Path

    from livery.toolroom.store.api import Lock

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
    from livery.toolroom.store.api import LOCK_FILE

    for name in sorted(lock.tools):
        mark = "  moved" if name in moved else ""
        scope = lock.tools[name].on
        where = f"  on {', '.join(scope)}" if scope else ""
        print(f"  {name} {lock.tools[name].version}{where}{mark}")
    print(f"  {LOCK_FILE}: {len(lock.tools)} tool(s) on {', '.join(lock.hosts)}")


@tools.task(name="lock")
def tools_lock(
    upgrade: Annotated[
        bool, doc("move every entry to the newest version that satisfies")
    ] = False,
    upgrade_tool: Annotated[
        list[str] | None, doc("move just these entries to their newest")
    ] = None,
    check: Annotated[bool, doc("report whether the lock is current; write nothing")] = (
        False
    ),
    relock: Annotated[
        list[str] | None, doc("delegated tools whose graph is resolved again")
    ] = None,
) -> None:
    """Resolve every site's requirements and write `tools.lock`.

    An entry the lock already holds stands while it still satisfies
    every floor and resolves on every locked host; a tool no site
    requires any more leaves the lock. Nothing on a machine changes:
    the lock says what a checkout installs, and `sync` installs it.

    ``--upgrade`` moves every entry to the newest version that
    satisfies, and ``--upgrade-tool`` moves the ones it names. An
    upgrade is an act on the repository: every package runs the version
    the lock names, so one entry moving moves every package with it.

    ``--check`` answers whether the lock is current and writes nothing,
    exiting non-zero when it would move. A delegated tool's resolved
    graph is written when its version enters the lock and kept after;
    ``--relock`` writes one again though the version stands, which is
    what an install asks for when it refuses because the runtime has no
    build for what the graph pins.
    """
    from livery.toolroom.store.api import LOCK_FILE
    from livery.workshop._tools import (
        current_lock,
        lock_is_current,
        stub_lines,
        tool_names,
        write_lock,
    )

    root = _root()
    named = tuple(upgrade_tool or ())
    if check:
        if upgrade or named or relock:
            fail("--check writes nothing, so it cannot be asked to upgrade or relock")
        current, why = lock_is_current(root)
        if not current:
            fail(f"the lock is not current: {why}; run `{prog()} tools.lock`")
        print(f"  {LOCK_FILE}: current")
        return
    before = current_lock(root)
    moving = tool_names(root) if upgrade else named
    lock = write_lock(root, upgrade=moving, relock=tuple(relock or ()))
    for name in named:
        if name not in lock.tools:
            held = ", ".join(sorted(lock.tools)) or "nothing"
            fail(f"{name} is not a tool the sites require; the lock holds {held}")
    moved = tuple(
        name
        for name in moving
        if before is None
        or name not in before.tools
        or name not in lock.tools
        or before.tools[name].version != lock.tools[name].version
    )
    _report(lock, moved)
    if moving and not moved:
        listed = ", ".join(moving)
        print(f"  nothing moved: {listed} already at the newest eligible version")
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
    from livery.toolroom.store.api import Requirement
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


@tools.task(name="sync")
def tools_sync(
    frozen: Annotated[bool, doc("install the lock as it is; never resolve")] = False,
    locked: Annotated[bool, doc("refuse if the lock is not current")] = False,
    offline: Annotated[
        bool, doc("supply from the machine's store and its sources only")
    ] = False,
) -> None:
    """Match this machine to `tools.lock`, writing the lock first if it must.

    What `sync` does for the tools alone, in uv's shape. By default the
    lock is written when there is none or when the sites' requirements
    have moved past it, and then every tool it names is supplied
    through the store, a receipt per tool says what reached PATH, and
    the stubs follow. A tool the store cannot supply is reported and
    the others are supplied.

    ``--frozen`` installs the lock exactly as it is and resolves
    nothing, which is what a CI job wants: a runner's checkout is
    judged, never re-resolved. ``--locked`` refuses when the lock is
    not current instead of writing it, which is the assertion a gate
    makes. ``--offline`` reads the catalogue and the deployments from
    the machine's store alone.
    """
    if frozen and locked:
        fail(
            "--frozen and --locked are two answers to one question: frozen"
            " installs the lock as it is, locked refuses when it is not"
            " current. Pass one."
        )
    sync_tools(_root(), frozen=frozen, locked=locked, offline=offline)


def sync_tools(
    root: Path, *, frozen: bool = False, locked: bool = False, offline: bool = False
) -> None:
    """Match this machine to *root*'s `tools.lock`, writing the lock first if it must.

    The engine behind ``tools.sync``, taking the workspace root as an
    argument: a birth runs it for the newborn from inside its own
    task, and a task may not change the process directory, so the
    root travels as a value. The flags mean what the task's do.
    """
    from livery.toolroom.store.api import LOCK_FILE
    from livery.workshop._sync import materialise_tools
    from livery.workshop._tools import lock_is_current, write_lock

    if not frozen:
        current, why = lock_is_current(root, offline=offline)
        if not current:
            if locked:
                fail(
                    f"--locked: {why}. Run `{prog()} tools.lock` and"
                    " commit what it writes."
                )
            print(f"  {LOCK_FILE}: {why}; writing it")
            _report(write_lock(root, offline=offline))
    for line in materialise_tools(root, offline=offline):
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
