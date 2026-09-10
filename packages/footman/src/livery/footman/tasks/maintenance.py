"""`fm maintenance.*`: sweep the runner's directories, footman's own and every plugin's.

footman's cache collector runs on its own at most once a day
(`livery.footman._gc`) and touches the cache directory alone. This
family runs it on demand, then every sweeper an installed package
registers under the ``footman.sweepers`` entry-point group, and prints
each line of what happened.

A sweeper is a callable taking the keyword arguments ``data_dir``,
``cache_dir``, ``config_dir``, ``dry_run``, ``unattended``, and ``now``,
and returning the lines it wants printed. ``dry_run`` asks it to say
what would go and remove nothing. ``unattended`` is true when the
daily child runs it, without a person watching: a sweeper then keeps
to what is quick, offline, and certain, and leaves the rules that ask
a forge for the on-demand run. A sweeper that raises is named and the
rest still run; one whose entry point cannot load is named and
skipped. The convention for what a sweeper may touch is footman's own:
its package's files under the directories, never another's.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from importlib.metadata import entry_points
from typing import Annotated, Any

from livery.footman import _gc, _paths
from livery.footman.params import doc
from livery.footman.registry import Group, group

tasks: Group = group("maintenance", help="Sweep the runner's directories")

#: The entry-point group a package registers its sweeper under.
GROUP = "footman.sweepers"

Sweeper = Callable[..., Iterable[str]]


def _sweepers() -> list[tuple[str, Sweeper | str]]:
    """Every registered sweeper by name, or the reason it did not load."""
    found: list[tuple[str, Sweeper | str]] = []
    for point in sorted(entry_points(group=GROUP), key=lambda p: p.name):
        try:
            loaded: Any = point.load()
        except Exception as error:
            found.append((point.name, f"{type(error).__name__}: {error}"))
            continue
        found.append((point.name, loaded))
    return found


def run_sweepers(*, dry_run: bool, unattended: bool) -> list[str]:
    """footman's own collect, then every sweeper; the lines to print.

    Under ``dry_run`` the collector is left to the daily child, since
    it can only remove; the sweepers hear the flag and say what would
    go. Under ``unattended`` the sweepers hear that too.
    """
    lines: list[str] = []
    cache = _paths.footman_cache_dir()
    if dry_run:
        lines.append(
            f"  cache: {cache} is collected by the daily child; nothing removed"
        )
    else:
        removed = _gc.collect(cache)
        lines.append(f"  cache: {removed} file(s) collected from {cache}")
    facts = {
        "data_dir": _paths.footman_data_dir(),
        "cache_dir": cache,
        "config_dir": _paths.footman_config_dir(),
        "dry_run": dry_run,
        "unattended": unattended,
        "now": datetime.now(UTC),
    }
    for name, sweeper in _sweepers():
        if isinstance(sweeper, str):
            lines.append(f"  {name}: could not load ({sweeper}); skipped")
            continue
        try:
            lines.extend(f"  {name}: {line}" for line in sweeper(**facts))
        except Exception as error:
            lines.append(
                f"  {name}: raised {type(error).__name__}: {error};"
                " the other sweepers still ran"
            )
    return lines


@tasks.task(expose="always")
def sweep(
    dry_run: Annotated[bool, doc("say what would go, remove nothing")] = False,
) -> None:
    """Sweep the runner's directories: footman's cache, then every plugin's state.

    Every line names what went or why it stayed. Idempotent: a second
    sweep finds nothing to do.
    """
    for line in run_sweepers(dry_run=dry_run, unattended=False):
        print(line)
