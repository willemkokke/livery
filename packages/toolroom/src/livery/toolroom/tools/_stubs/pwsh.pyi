# Hand-written: a shell is invoked to run a command *string*, not for its own
# flags, so `fm tools.sync` never touches this file (its driver is
# `source="manual"`).
from typing import Any, TypeVar

from livery.toolroom.tools import Argv, Tool

_R = TypeVar("_R")

class Pwsh(Tool[_R]):
    def __call__(  # type: ignore[override]
        self,
        command: str,
        /,
        **flags: Any,
    ) -> _R:
        """Run a command string in PowerShell — `pwsh -c "<command>"`.

        A real shell: pipes, redirects, globbing and `$VAR` all work. Reach
        for this when you deliberately want a shell; `run("…")` stays
        shell-free.

        Args:
            command: the command line to run in PowerShell.
        """
        ...
    @property
    def argv(self) -> Pwsh[Argv]: ...
