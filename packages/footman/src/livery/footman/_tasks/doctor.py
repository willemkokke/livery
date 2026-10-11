"""`fm doctor`: check the runner and every plugin's state; fix what can be fixed.

footman checks its own first, the runner's directories and the scratch
of gone processes, then runs every doctor an installed package registers
under the ``footman.doctors`` entry-point group, and prints each finding.
A doctor is a callable taking the keyword arguments ``data_dir``,
``cache_dir``, ``config_dir`` and ``now`` and returning its findings.

A finding has four attributes: ``what``, the check and its answer the way
a receipt names a step; ``found``, what is wrong, empty for a healthy
check; ``fix``, the words a person acts on; and ``repair``, an action the
doctor can run, or ``None``. [livery.footman.Finding][] is footman's own
shape, and a package that never imports footman returns any object
carrying the four, since footman reads them by name.

The plain verb reports and runs nothing. ``--fix`` runs every standing
finding's repair, asks every doctor again, and reports what is healthy
and what still stands with its reason. A doctor that raises is named and
the rest still run; one whose entry point cannot load is named and
skipped; a repair that raises leaves its finding standing with the error.
A finding that stands at the end makes the verb red.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, NamedTuple

from livery.footman import _entries, _gc, _paths
from livery.footman._context import fail
from livery.footman._params import doc
from livery.footman._registry import Group, group

tasks: Group = group(
    "doctor", help="Check the runner and every plugin; fix what can be"
)

#: The entry-point group a package registers its doctor under.
GROUP = "footman.doctors"


class Finding(NamedTuple):
    """One answer of a doctor: what was checked, what is wrong, how to fix it.

    ```python
    def doctor(**facts):
        missing = [p for p in views() if not p.exists()]
        if not missing:
            return [footman.Finding("views: whole")]
        return [footman.Finding("views", f"{len(missing)} missing", "sync", mend)]
    ```
    """

    what: str
    """The check and its answer, short, the way a receipt names a step."""
    found: str = ""
    """What is wrong; empty for a healthy check, which prints `what` alone."""
    fix: str = ""
    """The words a person acts on when the finding stands."""
    repair: Callable[[], None] | None = None
    """An action that mends it, run under `--fix`; `None` when only a person can."""


Doctor = Callable[..., Iterable[Any]]


def _doctors() -> list[tuple[str, Doctor | str]]:
    """Every registered doctor by name, or the reason it did not load."""
    found: list[tuple[str, Doctor | str]] = []
    for point in sorted(_entries.installed_entry_points(GROUP), key=lambda p: p.name):
        try:
            loaded: Any = point.load()
        except Exception as error:
            found.append((point.name, f"{type(error).__name__}: {error}"))
            continue
        found.append((point.name, loaded))
    return found


def _own(facts: dict[str, Any]) -> list[Finding]:
    """footman's own checks: the runner's directories, the scratch of gone processes."""
    findings: list[Finding] = []
    missing: list[tuple[str, Any]] = []
    for name in ("cache", "data"):
        path = facts[f"{name}_dir"]
        if not path.is_dir():
            missing.append((name, path))
    if missing:
        for name, path in missing:
            findings.append(
                Finding(
                    f"{name} directory",
                    f"{path} is missing",
                    "fm doctor --fix creates it; footman creates it on first use too",
                    functools.partial(_make_dir, path),
                )
            )
    else:
        findings.append(Finding("directories: cache and data present"))
    scratch = facts["cache_dir"] / _gc.SCRATCH
    gone = [
        entry
        for entry in (scratch.glob("*") if scratch.is_dir() else ())
        if entry.is_dir()
        and (pid := _gc.owner(entry.name)) is not None
        and not _gc.alive(pid)
    ]
    if gone:
        cache = facts["cache_dir"]
        findings.append(
            Finding(
                "scratch",
                f"{len(gone)} director{'y' if len(gone) == 1 else 'ies'} of gone"
                " processes",
                "fm janitor, or fm doctor --fix, removes them",
                lambda: _collect_cache(cache),
            )
        )
    else:
        findings.append(Finding("scratch: nothing of a gone process"))
    return findings


def _make_dir(path: Path) -> None:
    """Create *path* and its parents; the repair for a missing runner directory."""
    path.mkdir(parents=True, exist_ok=True)


def _text(finding: Any, name: str) -> str:
    """A finding's attribute *name* as text, empty when absent or None."""
    return str(getattr(finding, name, "") or "")


def _collect_cache(cache: Path) -> None:
    """Run the collector over *cache*; the scratch of gone processes goes with it."""
    _gc.collect(cache)


def _collect(facts: dict[str, Any]) -> tuple[list[tuple[str, Any]], list[str]]:
    """Every finding by its doctor's name, and the lines for doctors that failed."""
    findings: list[tuple[str, Any]] = [("footman", f) for f in _own(facts)]
    failed: list[str] = []
    for name, found in _doctors():
        if isinstance(found, str):
            failed.append(f"  {name}: could not load ({found}); skipped")
            continue
        try:
            findings.extend((name, f) for f in found(**facts))
        except Exception as error:
            failed.append(
                f"  {name}: raised {type(error).__name__}: {error}; the other"
                " doctors still ran"
            )
    return findings, failed


def _lines(finding: Any, *, repaired: str = "") -> list[str]:
    """A finding's lines: the healthy one, or what stands and its fix."""
    what = _text(finding, "what")
    found = _text(finding, "found")
    if not found:
        return [f"  {what}"]
    lines = [f"  {what}: {found}{repaired}"]
    if repaired:
        return lines
    fix = _text(finding, "fix")
    if fix:
        lines.append(f"    fix: {fix}")
    return lines


def run_doctors(*, fix: bool) -> tuple[list[str], int]:
    """footman's own checks, then every doctor; the lines, and how many findings stand.

    Under *fix* each standing finding's repair runs, then every doctor
    is asked again and the second answer is what prints, with each
    repair's fate beside the finding it addressed.
    """
    facts: dict[str, Any] = {
        "data_dir": _paths.footman_data_dir(),
        "cache_dir": _paths.footman_cache_dir(),
        "config_dir": _paths.footman_config_dir(),
        "now": datetime.now(UTC),
    }
    findings, failed = _collect(facts)
    lines: list[str] = []
    if fix:
        repaired: list[str] = []
        broken: dict[str, str] = {}
        for _name, finding in findings:
            if (
                not getattr(finding, "found", "")
                or getattr(finding, "repair", None) is None
            ):
                continue
            what = str(getattr(finding, "what", ""))
            try:
                finding.repair()
            except Exception as error:
                broken[what] = f"{type(error).__name__}: {error}"
            else:
                repaired.append(f"  {what}: {finding.found}; repaired")
        lines.extend(repaired)
        findings, failed = _collect(facts)
        for _name, finding in findings:
            what = _text(finding, "what")
            lines.extend(_lines(finding))
            if what in broken and _text(finding, "found"):
                at = len(lines) - 1 if _text(finding, "fix") else len(lines)
                lines.insert(at, f"    repair failed: {broken[what]}")
    else:
        for _name, finding in findings:
            lines.extend(_lines(finding))
    lines.extend(failed)
    standing = sum(1 for _n, f in findings if _text(f, "found")) + len(failed)
    return lines, standing


@tasks.default(expose="always")
def doctor(
    fix: Annotated[bool, doc("run every finding's repair, then check again")] = False,
) -> None:
    """Check the runner's state and every plugin's; with --fix, mend what can be mended.

    Every line is a finding: a healthy check's answer, or what is wrong
    and the fix. A finding that stands at the end is red. Idempotent:
    a second ``--fix`` finds nothing to repair.
    """
    lines, standing = run_doctors(fix=fix)
    for line in lines:
        print(line)
    if standing:
        plural = "" if standing == 1 else "s"
        fail(
            f"{standing} finding{plural} stand{'s' if standing == 1 else ''}:"
            " fix what is named above, or run `fm doctor --fix` for what footman"
            " can mend"
        )
