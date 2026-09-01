"""``workflow.update``: the update family on the engine.

Two verbs side by side and a default that runs both:
``workflow.update.templates`` moves the whole workspace to the
installed workshop's template version (one workspace-atomic act;
there is no per-package template update, one source at one version
has nothing for it to mean), and
``workflow.update.dependencies [names...]`` brings dependencies
current in both of their homes: the lock for external dependencies,
and the floors for workspace siblings, which the lock cannot move
because they resolve from source.

An update prepared while a release flies parks unarmed and waits,
watching the releases; when the last completes it re-runs itself,
picking up the fresh floors, and arms. Ctrl-C is safe: re-entry is
the same state machine. A non-interactive run waits bounded, then
parks at exit 0 with the same prose, parked is the verb having done
its job.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import time
from pathlib import Path
from typing import Annotated

from footman import doc, fail

from livery.forge import Repository
from livery.workshop._git_ops import GitOps
from livery.workshop._packages import discover_packages
from livery.workshop._update import bump_floors, refresh_rendered
from livery.workshop._uv import run_uv
from livery.workshop._workflow_engine import (
    PARKS_UPDATES,
    Submission,
    run_workflow,
)
from livery.workshop._workflow_state import (
    WorkflowKind,
    workflow_states,
)
from livery.workshop._workflow_tasks import workflow

#: How long a non-interactive parked update waits before exit 0.
BOUNDED_WAIT = 15 * 60
WAIT_POLL = 15

#: The loop guard for the fresh-interpreter resubmit.
_REEXEC_FLAG = "LIVERY_UPDATE_REEXEC"


class UpdateDriver:
    """One update flavor's driver for the shared engine."""

    kind = WorkflowKind.UPDATE
    members: tuple[str, ...] = ()

    def __init__(
        self,
        root: Path,
        git: GitOps,
        flavor: str,
        *,
        armed: bool,
        names: tuple[str, ...] = (),
        refresh: bool = False,
    ) -> None:
        self._root = root
        self._git = git
        self._names = names
        self._refresh = refresh
        self.name = f"update/{flavor}"
        self.armed = armed

    @property
    def branch(self) -> str:
        return f"workflow/{self.name}"

    @property
    def base(self) -> str:
        return "main"

    def prepare(self) -> Submission | None:
        """Do the flavor's work, or resume what a killed run committed.

        Committed-but-unsubmitted work on the branch is submitted as
        it stands, redoing nothing: every subject on the branch is the
        driver's own conventional ``chore:`` line, so resuming is
        safe; a foreign subject stops with the options, someone's
        hand-made commits are not silently shipped. A *refresh*
        driver (the re-run after a parked update's releases finish)
        runs the work again on the branch instead, so the floors
        those releases earned land as a second commit.
        """
        git = self._git
        resumed = False
        if git.current_branch() == self.branch or git.local_branch_exists(self.branch):
            if git.current_branch() != self.branch:
                git.switch(self.branch)
            subjects = git.subjects_ahead(self.base)
            if subjects:
                foreign = [s for s in subjects if not s.startswith("chore:")]
                if foreign:
                    listed = "\n".join(f"    {s}" for s in foreign)
                    fail(
                        "the update branch carries commits this driver did"
                        f" not make:\n{listed}\n  submit them yourself with"
                        " `fm submit`, or abort the update with"
                        f" `fm workflow.abort {self.name}` and start over."
                    )
                if not self._refresh:
                    print("  resuming the committed update from its branch")
                    return Submission(title=subjects[-1], body="")
                resumed = True
        else:
            git.create_branch(self.branch)
        toolchain_before = _locked_workshop(self._root)
        notes = self._work()
        if git.is_clean():
            if resumed:
                # A refresh with nothing new: the committed work stands.
                subjects = git.subjects_ahead(self.base)
                return Submission(title=subjects[-1], body="")
            git.switch(self.base)
            git.delete_local_branch(self.branch)
            return None
        run_uv("sync", root=self._root)
        run_gate(self._root)
        title = f"chore: {self.name.replace('/', ' ')}"
        git.commit_all(title + "\n\n" + "\n".join(f"- {n}" for n in notes))
        toolchain_after = _locked_workshop(self._root)
        if (
            toolchain_after != toolchain_before
            and toolchain_after
            and not os.environ.get(_REEXEC_FLAG)
        ):
            # The update just moved the running toolchain itself, and
            # the synced venv now holds newer code than this process.
            # The submit re-runs in a fresh interpreter so a fix the
            # update ships can protect its own submission; the resume
            # path submits the committed branch and redoes nothing.
            self._reexec()
        return Submission(title=title, body="\n".join(f"- {n}" for n in notes))

    def _work(self) -> list[str]:
        if self.name.endswith("/templates"):
            notes = bump_floors(self._root, self._git)
            notes += [f"render: {line}" for line in refresh_rendered(self._root)]
            return notes
        # A named workspace sibling resolves from source, so the lock
        # cannot move it: its movement is the floor, and only the
        # named floors move. External names go to the lock. Bare
        # moves everything in both homes.
        siblings = {p.name for p in discover_packages(self._root)}
        named_siblings = tuple(n for n in self._names if n in siblings)
        external = tuple(n for n in self._names if n not in siblings)
        notes = []
        if not self._names:
            run_uv("lock", "--upgrade", root=self._root)
            notes.append("lock: upgraded every dependency")
            notes += bump_floors(self._root, self._git)
            return notes
        for name in external:
            run_uv("lock", "--upgrade-package", name, root=self._root)
            notes.append(f"lock: upgraded {name}")
        if named_siblings:
            notes += bump_floors(self._root, self._git, only=named_siblings)
        return notes

    def _reexec(self) -> None:
        """Finish in a fresh process running the just-synced toolchain."""
        flavor = self.name.split("/", 1)[1]
        print(
            "  the update moved livery-workshop itself; finishing in a"
            " fresh interpreter running the new code"
        )
        command = ["uv", "run", "fm", f"workflow.update.{flavor}"]
        command += list(self._names)
        if self.armed:
            command.append("--armed")
        code = _spawn(command, self._root, {**os.environ, _REEXEC_FLAG: "1"})
        raise SystemExit(code)

    def on_merged(self) -> None:
        """Merging is an update's completion; nothing follows."""


def _spawn(command: list[str], root: Path, env: dict[str, str]) -> int:
    """Run *command* in *root* with *env*; the exit code."""
    child = subprocess.run(command, cwd=root, env=env, check=False)
    return child.returncode


def run_gate(root: Path) -> None:
    """Run the gate with ``--fix`` over the update's changes; red stops.

    The gate's output streams to the terminal and its exit code is
    the verdict. A red gate leaves the changes uncommitted on the
    workflow branch: fix the tree there and run the same update verb
    again, it resumes from that state.
    """
    result = subprocess.run(
        ["uv", "run", "fm", "check", "--fix"], cwd=root, check=False
    )
    if result.returncode != 0:
        fail(
            f"the gate is red on the update's changes (exit"
            f" {result.returncode}). The changes are uncommitted on this"
            " branch: fix the tree, then run the same update verb again"
            " to resume."
        )


def _live_releases(repo: Repository, git: GitOps) -> tuple[str, ...]:
    """The release workflows currently parking updates, by name."""
    return tuple(
        wf.name
        for wf in workflow_states(repo, git)
        if wf.kind is WorkflowKind.RELEASE and wf.state in PARKS_UPDATES
    )


def wait_for_releases(
    repo: Repository,
    git: GitOps,
    *,
    interactive: bool,
    timeout: float = BOUNDED_WAIT,
    poll: float = WAIT_POLL,
) -> bool:
    """Wait while releases fly; True when none remains in flight.

    The parked update's promise: it finishes itself once the last
    release completes. Interactive runs wait indefinitely, Ctrl-C
    always safe (re-entry resumes); non-interactive runs wait
    *timeout* then answer False, and the caller parks at exit 0.
    """
    deadline = time.monotonic() + timeout
    announced = False
    while True:
        live = _live_releases(repo, git)
        if not live:
            return True
        if not announced:
            announced = True
            listed = ", ".join(live)
            print(
                f"  Release(s) in progress: {listed}. This update will be"
                " finished automatically once they all finish. Ctrl-C is"
                " safe; run `fm workflow.update` any time to continue"
                " later."
            )
        if not interactive and time.monotonic() >= deadline:
            return False
        time.sleep(poll)


def _drive(
    flavor: str,
    *,
    armed: bool,
    names: tuple[str, ...] = (),
) -> None:
    from livery.workshop._forge_lane import this_repository
    from livery.workshop._layers import workspace_root

    root = workspace_root()
    if root is None:
        fail("no workspace: no livery.toml above the working directory")
    git = GitOps(root)
    repo = this_repository(root)
    driver = UpdateDriver(root, git, flavor, armed=armed, names=names)
    run_workflow(driver, repo, git)
    if not armed:
        return
    # Parked by a live release: wait, then finish ourselves. The
    # re-run re-prepares (fresh floors included) and arms.
    if not _live_releases(repo, git):
        return
    interactive = sys.stdin.isatty()
    # timeout and poll read at call time so a bounded test can move them.
    if wait_for_releases(
        repo, git, interactive=interactive, timeout=BOUNDED_WAIT, poll=WAIT_POLL
    ):
        fresh = UpdateDriver(root, git, flavor, armed=armed, names=names, refresh=True)
        run_workflow(fresh, repo, git)
        return
    print(
        "  still parked: the release outlasted the wait. Run"
        " `fm workflow.update` any time to continue; the re-run also"
        " raises floors to whatever released meanwhile."
    )


update_group = workflow.group("update", help="Bring the workspace current")


@update_group.task(name="templates")
def update_templates(
    armed: Annotated[bool, doc("arm the update's PR to merge on green")] = False,
) -> None:
    """Move the workspace to the installed workshop's template version.

    One workspace-atomic act: the project render and every package's
    managed files together, plus the sibling floors the releases
    since last time earned. In the monorepo the source is HEAD, so
    this reduces to floors and environment; template edits there are
    ordinary feature branches.
    """
    _drive("templates", armed=armed)


@update_group.task(name="dependencies")
def update_dependencies(
    *names: str,
    armed: Annotated[bool, doc("arm the update's PR to merge on green")] = False,
) -> None:
    """Bring dependencies current in both of their homes.

    Bare upgrades every external dependency through the lock and
    raises every sibling floor to its latest release; naming
    dependencies scopes which move, never where, one workspace lock
    is one resolution. A named sibling moves its floor and only its
    floor, since the lock cannot move what resolves from source.
    """
    _drive("dependencies", armed=armed, names=names)


@update_group.default
def update_default(
    armed: Annotated[bool, doc("arm the update's PR to merge on green")] = False,
) -> None:
    """Both updates: templates, then dependencies, one branch each."""
    _drive("templates", armed=armed)
    _drive("dependencies", armed=armed)


def _locked_workshop(root: pathlib.Path) -> str:
    """livery-workshop's locked registry version; empty when from source.

    The monorepo consumes the workshop from source, where the lock
    entry carries no registry version and the running code is always
    the tree's; only an instance's locked wheel can go stale under
    its own update.
    """
    lock = root / "uv.lock"
    if not lock.is_file():
        return ""
    text = lock.read_text("utf-8")
    marker = 'name = "livery-workshop"'
    start = text.find(marker)
    if start == -1:
        return ""
    block = text[start : start + 400]
    if "registry" not in block:
        return ""
    for line in block.splitlines():
        if line.startswith("version = "):
            return line.split('"')[1]
    return ""
