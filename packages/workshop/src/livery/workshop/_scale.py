"""Build a workspace of many members and time the workshop's verbs over it.

The budgets of a sync and a gate are set from a measured baseline,
and judged against the CI store. `fm ci.scale` builds the workspace
that baseline is measured on, times `fm sync`, `fm drift.check`
and `fm check` there, and prints the timings. Inside CI it also
records them as one ``scale`` job on the run's file of the
``metrics`` series ([livery.workshop._metrics][]), so `fm ci.timings`
shows them beside the gate's own rows.

The workspace is a copy of this checkout's committed tree at HEAD,
made a repository of its own with no remote, plus the generated
members. The workshop under test is the copy's own member, so the
timings measure the code being edited, and nothing is fetched from
an index or a forge. The copy keeps this repository's own members
and their source, so every checker reads them, but not their test
suites: those test facts about this repository, which the generated
members change.

Each verb is timed in three states:

- ``cold``: no environment in the copy and no gate record, the gate
  run whole. The machine's uv cache and tool store stay warm.
- ``warm``: again at once, nothing changed.
- ``one changed``: after an edit to the source of one python member
  no other member depends on.

The generated python members form a binary tree of runtime
dependencies: member ``i`` depends on member ``(i - 1) // 2``,
declared as a ``[[depends]]`` edge and as the kind's own
requirement, so the graph the gate walks has depth as well as width.

This is development tooling of this repository, not part of the
workshop a project uses.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import livery.footman.api as footman
import livery.toolroom.tools.api as tools
from livery.footman.api import doc, fail
from livery.workshop._ci_tasks import ci

#: The members a fixture holds by default, per template kind.
DEFAULT_COUNTS: dict[str, int] = {
    "package-python": 240,
    "package-cpp-conan": 40,
    "package-python-nanobind": 20,
}

#: The short tag a generated member's name carries, per template kind.
_TAGS = {
    "package-python": "p",
    "package-cpp-conan": "c",
    "package-python-nanobind": "n",
}

#: The verbs timed, in the order each state runs them.
VERBS: tuple[tuple[str, ...], ...] = (
    ("sync",),
    ("drift.check",),
    ("check",),
)

#: The states each verb is timed in, in order.
STATES = ("cold", "warm", "one changed")


@dataclass(frozen=True)
class Timing:
    """One timed run of a verb in the fixture.

    Attributes:
        name: The metric's name, the verb then the state.
        ms: The wall time, in milliseconds.
        code: The verb's exit code.
    """

    name: str
    ms: float
    code: int


def member_names(counts: dict[str, int]) -> list[tuple[str, str]]:
    """The generated members, as ``(directory name, template kind)`` pairs.

    Names sort by kind tag, then by a zero-padded index, so the
    python members' tree order is their name order.
    """
    out: list[tuple[str, str]] = []
    for kind, count in counts.items():
        tag = _TAGS.get(kind)
        if tag is None:
            fail(f"no scale tag for kind {kind!r}: use one of {', '.join(_TAGS)}")
        out.extend((f"scale-{tag}{index:03d}", kind) for index in range(count))
    return out


def parent_of(index: int) -> int | None:
    """The python member *index* depends on, or ``None`` for the root."""
    return None if index == 0 else (index - 1) // 2


def _git(root: Path, *args: str) -> str:
    """Run git under *root*; stdout, or fail with git's own words."""
    result = tools.git.opts(cwd=root, nofail=True, recorded=False)(*args)
    if result.code != 0:
        spelled = " ".join(args)
        fail(f"git {spelled} exited {result.code}:\n{result.stdout}{result.stderr}")
    return result.stdout


def copy_tree(source: Path, destination: Path) -> None:
    """Copy *source*'s committed tree at HEAD into *destination* as a new repository.

    The copy has one commit and no remote. Its commits are unsigned:
    they are scratch, and a signer that waits for a person would
    stop an unattended run.
    """
    destination.mkdir(parents=True, exist_ok=False)
    archive = destination.parent / f"{destination.name}.tar"
    _git(source, "archive", "--format=tar", f"--output={archive}", "HEAD")
    with tarfile.open(archive) as bundle:
        bundle.extractall(destination, filter="data")
    archive.unlink()
    _git(destination, "init", "--quiet", "--initial-branch=main")
    for key, value in (
        ("user.name", "scale fixture"),
        ("user.email", "scale@fixture.invalid"),
        ("commit.gpgsign", "false"),
        ("tag.gpgsign", "false"),
    ):
        _git(destination, "config", key, value)
    _commit(destination, "chore: the tree at HEAD")


def _commit(root: Path, subject: str) -> None:
    _git(root, "add", "--all")
    _git(root, "commit", "--quiet", "--no-verify", "-m", subject)


def strip_own_suites(root: Path) -> list[str]:
    """Remove the copy's own members' test modules and floors; the members.

    The fixture measures the verbs over the generated members. The
    suites of the members the copy started with test facts about the
    repository they come from, which the generated members change,
    and their time is not the fixture's. Each such member loses its
    ``test_*.py`` modules and keeps its helpers, since the rendered
    checker configuration names its ``tests`` directory; a directory
    left with no module gets one, named after the package. Its
    coverage floor is set to 0. The root ``tests`` directory is the
    project render's and stays as it is.
    """
    from livery.workshop._packages import discover_packages

    stripped: list[str] = []
    for package in discover_packages(root):
        tests = package.directory / "tests"
        if tests.is_dir():
            for module in tests.rglob("test_*.py"):
                module.unlink()
            if not any(tests.rglob("*.py")):
                name = package.directory.name.replace("-", "_")
                (tests / f"{name}_copy.py").write_text(
                    '"""The scale fixture\'s copy keeps no suite of this member."""\n',
                    encoding="utf-8",
                )
        contract = package.directory / "workshop.toml"
        text = contract.read_text("utf-8")
        contract.write_text(
            re.sub(r"(?m)^coverage-floor = \d+", "coverage-floor = 0", text),
            encoding="utf-8",
        )
        stripped.append(package.path)
    _commit(root, "chore: the copy's own suites removed")
    return stripped


def generate(root: Path, counts: dict[str, int]) -> list[tuple[str, str]]:
    """Add the generated members to the workspace at *root*; the members.

    Each member is rendered and added to the roster, then the project
    is rendered once and the result committed. Locking and installing
    are left to the first `fm sync`.
    """
    from livery.workshop._packages import declare_edge, discover_packages
    from livery.workshop._templates import apply_project, render_member

    members = member_names(counts)
    python = [name for name, kind in members if kind == "package-python"]
    for name, kind in members:
        render_member(root, name, kind=kind)
    by_path = {package.path: package for package in discover_packages(root)}
    for index, name in enumerate(python):
        parent = parent_of(index)
        if parent is not None:
            declare_edge(
                by_path[f"packages/{name}"],
                by_path[f"packages/{python[parent]}"],
                kind="runtime",
                floor="0.0.0",
            )
    apply_project(root)
    _commit(root, f"chore: {len(members)} generated members")
    return members


def leaf(members: list[tuple[str, str]]) -> str:
    """The python member the edit touches: the last, which no member depends on."""
    python = [name for name, kind in members if kind == "package-python"]
    if not python:
        fail("the fixture has no python member to edit")
    return python[-1]


def edit_member(root: Path, name: str) -> Path:
    """Append one covered, formatted statement to *name*'s module; the file edited."""
    found = sorted((root / "packages" / name / "src").rglob("__init__.py"))
    if not found:
        fail(f"packages/{name}: no package module to edit")
    module = found[-1]
    with module.open("a", encoding="utf-8") as handle:
        # Two blank lines are formatted after any top-level statement.
        handle.write("\n\nSCALE_EDIT = 1\n")
    return module


def _environment(root: Path) -> dict[str, str]:
    """The children's environment: this one, minus the venv it names.

    The conan home is the fixture's own, beside the workspace: a sync
    registers every native member as a conan editable, and in the
    machine's home those would outlive the fixture and answer for any
    later package of the same name.
    """
    from livery.workshop._e2e import unsigned_environment

    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    env["CONAN_HOME"] = str(root.parent / "conan-home")
    return {**env, **unsigned_environment(env)}


def run_verb(root: Path, verb: tuple[str, ...], *, name: str) -> Timing:
    """Run ``fm <verb>`` in *root* and time it; the timing, printed as it ends.

    The child is this interpreter's footman, which enters the
    workspace's own environment. Its output is printed when it
    exits non-zero, and the timing records the exit code either way.
    """
    start = time.perf_counter()
    result = footman.run(
        [sys.executable, "-m", "livery.footman", "--yes", *verb],
        cwd=root,
        env=_environment(root),
        nofail=True,
        timeout=7200.0,
    )
    ms = round((time.perf_counter() - start) * 1000.0, 1)
    print(f"  {name}: {ms / 1000:.1f}s, exit {result.code}")
    if result.code != 0:
        print(f"{result.stdout}{result.stderr}")
    return Timing(name=name, ms=ms, code=result.code)


def measure(root: Path, members: list[tuple[str, str]]) -> list[Timing]:
    """Time every verb in every state in the fixture at *root*; the timings."""
    timings: list[Timing] = []
    shutil.rmtree(root / ".venv", ignore_errors=True)
    for state in STATES:
        if state == "one changed":
            edited = edit_member(root, leaf(members))
            print(f"  edited {edited.relative_to(root).as_posix()}")
        for verb in VERBS:
            spelled = " ".join(verb)
            args = (*verb, "--full") if verb == ("check",) and state == "cold" else verb
            timings.append(run_verb(root, args, name=f"{spelled} {state}"))
    return timings


def row(
    timings: list[Timing], members: list[tuple[str, str]], *, job: str = "scale"
) -> dict[str, Any]:
    """The ``scale`` job's row: the timings as tasks, exits and members."""
    counts: dict[str, int] = {}
    for _name, kind in members:
        counts[kind] = counts.get(kind, 0) + 1
    return {
        "job": job,
        "total_ms": round(sum(timing.ms for timing in timings), 1),
        "tasks": {timing.name: timing.ms for timing in timings},
        "codes": {timing.name: timing.code for timing in timings},
        "members": dict(sorted(counts.items())),
    }


def record(root: Path, job_row: dict[str, Any], *, sha: str) -> str:
    """Put *job_row* on the run's file of the metrics series; what happened.

    Outside CI nothing is written and the line says so.
    """
    from livery.workshop._metrics import SERIES, run_file
    from livery.workshop._state import remote_snapshot, run_context

    run = run_context()
    if run is None:
        return "  not a CI run: the timings are recorded by CI only"
    entry: dict[str, Any] = {
        "forge": run.forge,
        "run": run.run_id,
        "event": run.event,
        "sha": run.head_sha or sha,
        "checkout": sha,
        "ref": run.ref,
        "collected_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "jobs": {str(job_row["job"]): job_row},
    }
    with remote_snapshot(root, fetch=("metrics",)):
        why = SERIES.put(
            root,
            {run_file(run.run_id): entry},
            message=f"metrics: scale fixture of run {run.run_id} at {sha[:12]}",
        )
    return (
        f"  {SERIES.ref}: {why}"
        if why
        else f"  {SERIES.ref}: run {run.run_id} recorded"
    )


def _counts(python: int, cpp: int, nanobind: int) -> dict[str, int]:
    return {
        kind: count
        for kind, count in (
            ("package-python", python),
            ("package-cpp-conan", cpp),
            ("package-python-nanobind", nanobind),
        )
        if count > 0
    }


@ci.task(name="scale")
def ci_scale(
    python: Annotated[int, doc("generated python members")] = DEFAULT_COUNTS[
        "package-python"
    ],
    cpp: Annotated[int, doc("generated cpp-conan members")] = DEFAULT_COUNTS[
        "package-cpp-conan"
    ],
    nanobind: Annotated[int, doc("generated nanobind members")] = DEFAULT_COUNTS[
        "package-python-nanobind"
    ],
    keep: Annotated[
        str, doc("build the fixture in this new directory and keep it")
    ] = "",
) -> None:
    """Time sync, drift.check and check over a generated workspace.

    Copies the committed tree at HEAD, adds the generated members,
    and times each verb cold, warm with nothing changed, and after
    one member's source changed. Prints every timing. Inside CI the
    timings are also put on the metrics series as the run's
    ``scale`` job, which `fm ci.timings` shows. The fixture is
    removed afterwards unless *keep* names where to build it.
    """
    from livery.workshop._templates import _root  # pyright: ignore[reportPrivateUsage]

    source = _root()
    counts = _counts(python, cpp, nanobind)
    if keep:
        timings = run_fixture(source, Path(keep).resolve(), counts)
    else:
        with tempfile.TemporaryDirectory(
            prefix="fm-scale-", ignore_cleanup_errors=True
        ) as scratch:
            timings = run_fixture(source, Path(scratch) / "workspace", counts)
    failed = [timing.name for timing in timings if timing.code != 0]
    if failed:
        fail(f"the fixture's verbs exited non-zero: {', '.join(failed)}")


def run_fixture(source: Path, root: Path, counts: dict[str, int]) -> list[Timing]:
    """Build the fixture at *root* from *source*, time it, record it; the timings."""
    copy_tree(source, root)
    stripped = strip_own_suites(root)
    print(f"  {len(stripped)} member(s) of the copy without their suites")
    started = time.perf_counter()
    members = generate(root, counts)
    print(
        f"  generated {len(members)} member(s) in"
        f" {time.perf_counter() - started:.1f}s at {root}"
    )
    timings = measure(root, members)
    sha = _git(source, "rev-parse", "HEAD").strip()
    print(record(source, row(timings, members), sha=sha))
    return timings
