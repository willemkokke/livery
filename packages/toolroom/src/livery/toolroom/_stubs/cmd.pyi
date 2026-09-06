# Hand-written: a shell is invoked to run a command *string*, not for its own
# flags, so `fm tools.sync` never touches this file (its driver is
# `source="manual"`).
from typing import Any, TypeVar

from livery.toolroom import Argv, Tool

_R = TypeVar("_R")

class Cmd(Tool[_R]):
    def __call__(  # type: ignore[override]
        self,
        command: str,
        /,
        **flags: Any,
    ) -> _R:
        """Run a command string in the Windows command processor — `cmd /c "<command>"`.

        A real shell: pipes, redirects, and `%VAR%` all work. Windows only.
        Reach for this when you deliberately want cmd; `run("…")` stays
        shell-free, and `run(shell="cmd", …)` is the ergonomic front door.

        Args:
            command: the command line to run in cmd.
        """
        ...
    @property
    def argv(self) -> Cmd[Argv]: ...
