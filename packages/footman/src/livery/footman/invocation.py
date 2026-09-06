"""The handle a lifecycle hook is given: one object per `fm` line.

A run has three scopes, and they are different animals worth different words:

* the **invocation** — one `fm` line, described by this object;
* a **context** — one task, `ctx`, with its own environment overlay and cwd;
* a **run** — one step, what `run()` spawns.

`Invocation` is the first. It is born knowing what the line asked for and where
it was asked, it is *editable* at `pre_tasks` — the one single-threaded moment
before anything runs — and it is frozen for the parallel window, so a hook on a
pool thread can read it without a lock and cannot race another one writing it.

The determinism rule that shapes it: tree and availability edits derive from
files, config and environment — **never** from `inv.cli`. The manifest is built
by a detached refresh child that has no command line at all, so an edit that
depended on one would make completion disagree with the run. The child is given
an invocation with no `cli`, which turns that rule into something the code
enforces rather than something the docs ask for.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from livery.footman.registry import Tasks


class Frozen(RuntimeError):
    """A write to an invocation that is no longer editable."""


class Invocation:
    """What one `fm` line is doing, handed to every lifecycle hook.

    Editable only at `pre_tasks`; every later moment sees the same object with
    writes refused, so what a task-time hook reads is what the run decided.
    """

    __slots__ = (
        "_frozen",
        "cli",
        "config",
        "cwd",
        "results",
        "root",
        "skipped",
        "tasks",
        "total_ms",
    )

    _frozen: bool
    cli: dict[str, Any]
    config: dict[str, Any]
    root: str
    cwd: str
    tasks: Tasks | None

    def __init__(
        self,
        *,
        cli: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        root: str = "",
        cwd: str = "",
        tasks: Tasks | None = None,
    ) -> None:
        object.__setattr__(self, "_frozen", False)
        self.cli = dict(cli or {})
        """The global options this line carried. Empty in the manifest refresh
        child, which has no command line — so a tree edit that reads this would
        be reading nothing there, which is exactly why it must not."""
        self.config = dict(config or {})
        """The merged `[tool.footman]` configuration."""
        self.root = root
        """The cascade root: the directory of the highest `tasks.py`."""
        self.cwd = cwd
        """Where `fm` was invoked."""
        self.tasks = tasks
        """The merged command tree, as a `Tasks` view — the same surface a
        `TaskView` edit uses. Editable at `pre_tasks`, when a hook may disable a
        task, add a prerequisite, or read provenance."""
        self.results: tuple[Any, ...] = ()
        """The run's rows, in the order the work was created, as result
        views — filled for
        `post_tasks` (the run itself writes past the freeze; hooks read).
        Executions, `shared` rows, refusals and `skipped` nodes alike."""
        self.skipped: tuple[Any, ...] = ()
        """The `skipped` subset of `results` — what never ran because
        something it needed failed."""
        self.total_ms: float = 0.0
        """Wall-clock for the whole run, the envelope's `total_ms` — filled
        for `post_tasks`."""

    def freeze(self) -> None:
        """Close the invocation for writes — called once, before tasks run."""
        object.__setattr__(self, "_frozen", True)

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_frozen", False):
            raise Frozen(
                f"inv.{name} cannot be set now: the invocation is editable only "
                f"at pre_tasks, before anything runs. Per-task state belongs on "
                f"task.state, and per-task environment on ctx.env."
            )
        object.__setattr__(self, name, value)

    def __repr__(self) -> str:
        shape = "frozen" if self._frozen else "editable"
        return f"<Invocation {shape} cwd={self.cwd!r}>"
