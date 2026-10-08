"""What a tool wrapper asks of the footman run it is called in.

A library that runs tools, toolroom's bridge among them, behaves one way
under footman and another standalone: under footman a call joins the
task's directory, colour and record, and a parallel task's in-process
call cannot move the process's directory. [livery.footman.host][]
answers whether a run is in flight and, when one is, the questions such
a library asks of it. Outside a run it is None, and the library keeps
its standalone behaviour.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from contextlib import AbstractContextManager
    from pathlib import Path


class Host:
    """The footman run a call is made in, as a tool wrapper asks of it.

    A run is in flight when footman's routers are installed
    (``active``), and a task is running when a task's context is current
    (``in_task``); either makes [livery.footman.host][] answer one.
    """

    @property
    def active(self) -> bool:
        """Whether footman's routers are installed: a run is in flight."""
        from livery.footman import _globals

        return _globals.active()

    @property
    def in_task(self) -> bool:
        """Whether a task's context is current: a call joins its directory."""
        from livery.footman._context import _current

        return _current.get() is not None

    def real_cwd(self) -> str:
        """The process's own working directory, which a parallel task never moves."""
        from livery.footman import _globals

        return _globals.real_getcwd()

    def target_cwd(
        self, cwd: str | Path | None = None, relative: str | Path | None = None
    ) -> Path | None:
        """The directory a call runs in: *cwd* or the task's, *relative* under it.

        None when the task runs unmanaged, or for ``cwd="unmanaged"``: the
        call then inherits the process's directory.

        Raises:
            ValueError: when *relative* is absolute, or is given with
                ``cwd="unmanaged"``, which has no base to join it to.
        """
        from livery.footman._context import _target_cwd, current

        return _target_cwd(current(), cwd, relative)

    def argv_override(self, args: list[str]) -> AbstractContextManager[None]:
        """A block in which this thread alone sees *args* as ``sys.argv``.

        A wrapper runs a tool's zero-argument ``main()`` in-process inside
        one, so parallel calls do not share one ``sys.argv``.
        """
        from livery.footman import _globals

        overridden: AbstractContextManager[None] = _globals.argv_override(args)
        return overridden


def host() -> Host | None:
    """The footman run this call is made in; None when no run is in flight.

    One is answered while footman's routers are installed or a task's
    context is current. A library that only imports footman, outside any
    run, gets None and keeps its standalone behaviour.
    """
    import sys

    if "livery.footman._context" not in sys.modules:
        return None
    found = Host()
    return found if found.active or found.in_task else None
