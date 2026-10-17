"""The speed lines a run of the python suites prints after them.

On a machine the lines show each package's summed test time and read
no mark; on a check leg they show the leg's mark beside it, and a mark
that cannot be read is named.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from extensions_python_spawns import counting_spawns
from livery.extensions.python._backend import speed_lines
from livery.workshop._speed import write_mark

LEG = "check-ubuntu-latest-3.14"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture
def work(tmp_path: Path) -> Path:
    """A clone of a bare origin, one commit on main, pushed."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "--initial-branch=main", str(origin))
    repo = tmp_path / "work"
    _git(tmp_path, "clone", "-q", str(origin), str(repo))
    (repo / "seed.txt").write_text("seed\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "chore: seed")
    _git(repo, "push", "-q", "-u", "origin", "main")
    return repo


def test_speed_lines_on_a_machine_print_the_sums_and_read_no_mark(
    work: Path, tmp_path: Path
) -> None:
    """The marks are judged on the CI legs, so a machine's run never asks for them."""
    sums = tmp_path / "speed.json"
    assert speed_lines(work, sums) == []  # the plugin did not run
    sums.write_text(json.dumps({"packages/forge": {"seconds": 12.3, "tests": 45}}))
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    with counting_spawns() as spawned:
        assert speed_lines(work, sums) == [
            "  speed packages/forge: 12.3s over 45 tests (judged on the CI legs)"
        ]
    assert spawned["git"] == 0


def test_speed_lines_on_a_check_leg_print_the_sums_beside_the_marks(
    work: Path, tmp_path: Path
) -> None:
    sums = tmp_path / "speed.json"
    sums.write_text(json.dumps({"packages/forge": {"seconds": 12.3, "tests": 45}}))
    assert speed_lines(work, sums, leg=LEG) == [
        "  speed packages/forge: 12.3s over 45 tests"
        " (no mark on check-ubuntu-latest-3.14)"
    ]
    assert (
        write_mark(
            work,
            package="packages/forge",
            leg=LEG,
            seconds=14.0,
            kind="first",
            by="run 1",
        )
        == ""
    )
    assert speed_lines(work, sums, leg=LEG) == [
        "  speed packages/forge: 12.3s over 45 tests"
        " (mark 14.0s on check-ubuntu-latest-3.14)"
    ]
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    lines = speed_lines(work, sums, leg=LEG)
    assert lines[0].startswith("  speed marks: not read (")
    assert lines[1] == "  speed packages/forge: 12.3s over 45 tests (mark unknown)"


def test_speed_sums_that_cannot_be_read_print_nothing_and_a_bad_entry_is_skipped(
    tmp_path: Path,
) -> None:
    # The fallbacks first: a file that is no JSON, a list, and an empty
    # table say nothing about a package.
    sums = tmp_path / "speed.json"
    for text in ("not json", "[]", "{}"):
        sums.write_text(text)
        assert speed_lines(tmp_path, sums) == []
    # An entry that is no table is skipped, and the others print.
    sums.write_text(
        json.dumps({"packages/broken": 3, "packages/forge": {"seconds": 2.0}})
    )
    assert speed_lines(tmp_path, sums) == [
        "  speed packages/forge: 2.0s over 0 tests (judged on the CI legs)"
    ]
