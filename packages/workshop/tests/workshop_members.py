"""Stand in for the runner a template's verb hands its member's wiring to.

``fm new.package.<template>`` writes a member's seeds, then runs
``new.wire <name>`` with the runner the workspace's environment holds,
which a test's workspace does not have. `wired_here` gives the
workspace a runner file and runs the wiring in this process;
`mounted_now` takes the mount a process started now would take, and
`template_verb` is the verb the mount makes for a template. This is not
a conftest, for the reason `workshop_seeds` gives.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from pathlib import Path

import pytest


def mounted_now(root: Path) -> tuple[str, ...]:
    """Mount *root*'s extensions as a process started now would; the names mounted.

    The points' jobs start as a new process has them, with nothing
    added or joined, and the verbs the mount makes land in a throwaway
    tree. The workshop's test guard puts the steps, the jobs and the
    mount's records back after the test.
    """
    from livery.footman import _registry as registry
    from livery.workshop import _points
    from livery.workshop._extensions import mount_extensions

    _points.restore(_points.snapshot(initial=True))
    with registry.capture():
        return mount_extensions(root)


def template_verb(root: Path, template: str) -> Callable[..., None]:
    """The verb ``new.package.<template>`` for the workspace at *root*, unwrapped.

    Made as the mount makes it, from the templates the workspace offers,
    in a throwaway tree.
    """
    from livery.footman import Group
    from livery.workshop._seeds import installed_templates
    from livery.workshop._templates import generate_template_verbs

    tree = Group("root")
    generate_template_verbs(installed_templates(root), into=tree)
    verb: Callable[..., None] = inspect.unwrap(
        tree.groups["new"].groups["package"].tasks[template]
    )
    return verb


def wired_here(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    *,
    starting: Callable[[], object] | None = None,
) -> list[list[str]]:
    """Run the hand-off's ``new.wire`` in this process; the command lines.

    Each command line the hand-off ran is listed, and refused unless it
    is the workspace's runner wiring a member in *root*. *starting* runs
    before the wiring, as a fresh process's start does.
    """
    from livery.workshop._pythons import runner_script
    from livery.workshop._templates import new_wire

    runner = runner_script(root / ".venv")
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text("")
    handed: list[list[str]] = []

    def run(argv: list[str], *, cwd: object = None, **_options: object) -> None:
        assert Path(argv[0]).resolve() == runner.resolve(), argv
        assert argv[1] == "new.wire" and len(argv) == 3, argv
        assert Path(str(cwd)).resolve() == root.resolve(), cwd
        handed.append(list(argv))
        if starting is not None:
            starting()
        new_wire(argv[2])

    monkeypatch.setattr("livery.footman.run", run)
    return handed
