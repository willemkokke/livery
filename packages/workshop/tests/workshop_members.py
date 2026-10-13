"""Stand in for the runner ``new.package`` hands its member's wiring to.

``fm new.package`` writes a member's seeds, then runs ``new.package
<name> --resume`` with the runner the workspace's environment holds,
which a test's workspace does not have. `wired_here` gives the
workspace a runner file and runs the resumed wiring in this process;
`mounted_now` takes the mount a process started now would take. This is
not a conftest, for the reason `workshop_seeds` gives.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest


def mounted_now(root: Path) -> tuple[str, ...]:
    """Mount *root*'s extensions as a process started now would; the names mounted.

    The points' jobs start as a new process has them, with nothing
    added or joined, and the verbs the mount makes land in a throwaway
    tree. The isolation plugin puts the steps, the jobs and the mount's
    records back after the test.
    """
    from livery.footman import _registry as registry
    from livery.workshop import _points
    from livery.workshop._extensions import mount_extensions

    _points.restore(_points.snapshot(initial=True))
    with registry.capture():
        return mount_extensions(root)


def wired_here(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    *,
    starting: Callable[[], object] | None = None,
) -> list[list[str]]:
    """Run the hand-off's ``new.package --resume`` in this process; the command lines.

    Each command line the hand-off ran is listed, and refused unless it
    is the workspace's runner resuming a member in *root*. *starting*
    runs before the resumed wiring, as a fresh process's start does.
    """
    from livery.workshop._pythons import runner_script
    from livery.workshop._templates import new_package

    runner = runner_script(root / ".venv")
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text("")
    handed: list[list[str]] = []

    def run(argv: list[str], *, cwd: object = None, **_options: object) -> None:
        assert Path(argv[0]).resolve() == runner.resolve(), argv
        assert argv[1] == "new.package" and argv[3:] == ["--resume"], argv
        assert Path(str(cwd)).resolve() == root.resolve(), cwd
        handed.append(list(argv))
        if starting is not None:
            starting()
        new_package(argv[2], resume=True)

    monkeypatch.setattr("livery.footman.run", run)
    return handed
