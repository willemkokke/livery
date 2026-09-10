"""The per-suite coverage store: the misses and refusals first, then the reuse."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from livery.workshop import _coverage_store, _state
from livery.workshop._git_ops import GitError, GitOps
from livery.workshop._packages import Edge, Package

RUN = _state.RunContext(
    "gitea", "1013", "push", "refs/heads/main", leg="check-linux-3.14"
)
LEG = "check-linux-3.14"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def _package(root: Path, name: str, depends: tuple[str, ...] = ()) -> Package:
    directory = root / "packages" / name
    (directory / "src" / name).mkdir(parents=True, exist_ok=True)
    (directory / "tests").mkdir(exist_ok=True)
    (directory / "src" / name / "mod.py").write_text("a = 1\n")
    (directory / "tests" / "test_mod.py").write_text("def test_it():\n    pass\n")
    (directory / "workshop.toml").write_text(
        f'type = "python"\nname = "livery-{name}"\n'
    )
    return Package(
        directory=directory,
        path=f"packages/{name}",
        name=f"livery-{name}",
        type="python",
        depends=tuple(
            Edge(path=f"packages/{dep}", kind="build", floor="0") for dep in depends
        ),
    )


@pytest.fixture
def work(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "--initial-branch=main", str(origin))
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", str(origin), str(work))
    _git(work, "config", "user.name", "tester")
    _git(work, "config", "user.email", "tester@example.invalid")
    (work / "pyproject.toml").write_text("[project]\nname = 'w'\n")
    (work / "uv.lock").write_text("version = 1\n")
    _package(work, "base")
    _package(work, "top", ("base",))
    _git(work, "add", ".")
    _git(work, "commit", "-qm", "init")
    _git(work, "push", "-q", "-u", "origin", "main")
    return work


@pytest.fixture(autouse=True)
def _in_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GITHUB_EVENT_PATH", "GITHUB_SHA", "GITHUB_REF", "GITHUB_JOB"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITEA_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_RUN_ID", RUN.run_id)
    monkeypatch.setenv("WORKSHOP_LEG", LEG)


def _packages(work: Path) -> tuple[Package, Package]:
    return _package(work, "base"), _package(work, "top", ("base",))


# --- the misses and refusals first --------------------------------------------


def test_an_absent_store_and_an_unknown_closure_are_plain_misses(work: Path) -> None:
    base, _top = _packages(work)
    assert _coverage_store.find(work, leg=LEG, package=base, closure_key="a" * 64) == (
        None,
        "",
    )
    why = _coverage_store.stamp(
        work, RUN, leg=LEG, package=base, closure_key="a" * 64, sha="b" * 40, files={}
    )
    assert why == ""
    assert _coverage_store.find(work, leg=LEG, package=base, closure_key="c" * 64) == (
        None,
        "",
    )


def test_a_leg_without_a_label_neither_reads_nor_writes(work: Path) -> None:
    base, _top = _packages(work)
    assert _coverage_store.find(work, leg="", package=base, closure_key="a" * 64) == (
        None,
        "",
    )
    why = _coverage_store.stamp(
        work, RUN, leg="", package=base, closure_key="a" * 64, sha="b" * 40, files={}
    )
    assert "no label" in why


def test_the_stamp_refuses_outside_ci(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base, _top = _packages(work)
    monkeypatch.delenv("GITHUB_ACTIONS")
    why = _coverage_store.stamp(
        work, RUN, leg=LEG, package=base, closure_key="a" * 64, sha="b" * 40, files={}
    )
    assert "only a CI run writes" in why


def test_an_unreadable_store_and_a_foreign_entry_name_their_reason(
    work: Path, tmp_path: Path
) -> None:
    base, _top = _packages(work)
    ref = _coverage_store.suite_ref(LEG, base)
    assert (
        _state.put(
            work,
            ref,
            {"20260101T000000Z--" + "a" * 64: json.dumps({"schema": 99})},
            message="foreign",
        )
        == ""
    )
    found, why = _coverage_store.find(work, leg=LEG, package=base, closure_key="a" * 64)
    assert found is None and "this reader speaks" in why
    assert (
        _state.put(
            work, ref, {"20260102T000000Z--" + "a" * 64: "not json"}, message="garbled"
        )
        == ""
    )
    found, why = _coverage_store.find(work, leg=LEG, package=base, closure_key="a" * 64)
    assert found is None and "does not parse" in why
    # A good stamp is newer than both and wins; a garbled entry newer
    # still is a reason again, never a silent fall-back to the stamp.
    why = _coverage_store.stamp(
        work, RUN, leg=LEG, package=base, closure_key="a" * 64, sha="b" * 40, files={}
    )
    assert why == ""
    found, why = _coverage_store.find(work, leg=LEG, package=base, closure_key="a" * 64)
    assert why == "" and found is not None and found.run == RUN.run_id
    assert (
        _state.put(
            work, ref, {"20991231T000000Z--" + "a" * 64: "not json"}, message="newer"
        )
        == ""
    )
    found, why = _coverage_store.find(work, leg=LEG, package=base, closure_key="a" * 64)
    assert found is None and "does not parse" in why
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    found, why = _coverage_store.find(work, leg=LEG, package=base, closure_key="a" * 64)
    assert found is None and "could not be read" in why


def test_the_closure_id_refuses_a_directory_head_lacks(work: Path) -> None:
    base, _top = _packages(work)
    ghost = Package(
        directory=work / "packages" / "ghost",
        path="packages/ghost",
        name="livery-ghost",
        type="python",
        depends=(),
    )
    with pytest.raises(GitError):
        _coverage_store.closure_id(GitOps(work), (base, ghost), ghost)


# --- the identity -------------------------------------------------------------


def test_the_closure_id_follows_the_closure_and_the_root_pins(work: Path) -> None:
    base, top = _packages(work)
    git = GitOps(work)
    packages = (base, top)
    before = {
        "base": _coverage_store.closure_id(git, packages, base),
        "top": _coverage_store.closure_id(git, packages, top),
    }
    assert _coverage_store.closure(packages, top) == (base, top)
    assert _coverage_store.closure(packages, base) == (base,)
    # A change inside the dependency moves the dependant's identity too.
    (work / "packages" / "base" / "src" / "base" / "mod.py").write_text("a = 2\n")
    _git(work, "commit", "-qam", "base changes")
    after = {
        "base": _coverage_store.closure_id(git, packages, base),
        "top": _coverage_store.closure_id(git, packages, top),
    }
    assert after["base"] != before["base"] and after["top"] != before["top"]
    # A change inside the dependant leaves the dependency's identity alone.
    (work / "packages" / "top" / "src" / "top" / "mod.py").write_text("a = 3\n")
    _git(work, "commit", "-qam", "top changes")
    again = {
        "base": _coverage_store.closure_id(git, packages, base),
        "top": _coverage_store.closure_id(git, packages, top),
    }
    assert again["base"] == after["base"] and again["top"] != after["top"]
    # A pin change is a dependency change for every suite.
    (work / "uv.lock").write_text("version = 2\n")
    _git(work, "commit", "-qam", "lock moves")
    pinned = _coverage_store.closure_id(git, packages, base)
    assert pinned != again["base"]
    # Files outside the closure are not the suite's to store.
    assert _coverage_store.in_closure(packages, top, "packages/base/src/base/mod.py")
    assert not _coverage_store.in_closure(packages, base, "packages/top/src/top/mod.py")
    assert _coverage_store.in_closure(packages, top, "packages\\top\\src\\top\\mod.py")


# --- the reuse ----------------------------------------------------------------


def test_a_stamp_is_found_by_its_closure_newest_first_within_the_window(
    work: Path,
) -> None:
    base, _top = _packages(work)
    key = "a" * 64
    for run_id, lines in (("1", [1]), ("2", [1, 2])):
        run = _state.RunContext("gitea", run_id, "push", "refs/heads/main", leg=LEG)
        why = _coverage_store.stamp(
            work,
            run,
            leg=LEG,
            package=base,
            closure_key=key,
            sha="b" * 40,
            files={"packages/base/src/base/mod.py": lines},
        )
        assert why == ""
    found, why = _coverage_store.find(work, leg=LEG, package=base, closure_key=key)
    assert why == "" and found is not None
    assert found.run == "2"
    assert found.files == {"packages/base/src/base/mod.py": [1, 2]}
    assert found.package == "packages/base" and found.leg == LEG
    # Another leg's measurement is another ref: a miss here.
    other = _coverage_store.find(
        work, leg="check-macos-3.13", package=base, closure_key=key
    )
    assert other == (None, "")
    # The window bounds the ref: the oldest entries go first.
    for extra in range(_coverage_store.WINDOW + 2):
        assert (
            _coverage_store.stamp(
                work,
                RUN,
                leg=LEG,
                package=base,
                closure_key=f"{extra:064d}",
                sha="b" * 40,
                files={},
            )
            == ""
        )
    held = _state.read(work, _coverage_store.suite_ref(LEG, base))
    assert held.files is not None and len(held.files) == _coverage_store.WINDOW
    assert _coverage_store.find(work, leg=LEG, package=base, closure_key=key) == (
        None,
        "",
    )


def test_the_current_keys_are_every_check_leg_with_every_unit_or_none(
    work: Path,
) -> None:
    # No contract at the root: the keys cannot be told, and the janitor
    # drops nothing.
    assert _coverage_store.current_keys(work) is None
    (work / "workshop.toml").write_text(
        '[ci]\nrunners = ["ubuntu-latest", "macos-latest"]\n'
        'python-versions = ["3.14"]\n'
    )
    for name in ("base", "top"):
        (work / "packages" / name / "pyproject.toml").write_text(
            f'[project]\nname = "livery-{name}"\nversion = "0"\n'
        )
    assert _coverage_store.current_keys(work) == {
        ("check-ubuntu-latest-3-14", "livery-base"),
        ("check-ubuntu-latest-3-14", "livery-top"),
        ("check-macos-latest-3-14", "livery-base"),
        ("check-macos-latest-3-14", "livery-top"),
    }


# --- the workspace's own tests, a unit keyed by the whole tree -----------------


def test_the_workspace_tests_are_a_unit_keyed_by_the_tree(work: Path) -> None:
    from livery.workshop._packages import Package

    base, top = _packages(work)
    assert _coverage_store.workspace_suite(work / "nowhere") is None
    (work / "tests").mkdir()
    unit = _coverage_store.workspace_suite(work)
    assert unit is not None and unit.path == "tests" and unit.name == "workspace-tests"
    git = GitOps(work)
    (work / "tests" / "test_all.py").write_text("def test_it():\n    pass\n")
    _git(work, "add", "tests")
    _git(work, "commit", "-qm", "workspace tests")
    before = _coverage_store.closure_id(git, (base, top), unit)
    # Prose leaves the key alone; a package or a pin moves it.
    (work / "notes").mkdir()
    (work / "notes" / "plan.md").write_text("# plan\n")
    _git(work, "add", "notes")
    _git(work, "commit", "-qm", "a note")
    assert _coverage_store.closure_id(git, (base, top), unit) == before
    (work / "packages" / "top" / "src" / "top" / "mod.py").write_text("a = 9\n")
    _git(work, "commit", "-qam", "top changes")
    after = _coverage_store.closure_id(git, (base, top), unit)
    assert after != before
    (work / "uv.lock").write_text("version = 3\n")
    _git(work, "commit", "-qam", "lock moves")
    assert _coverage_store.closure_id(git, (base, top), unit) != after
    # Any file is the unit's to store: its tests reach every package.
    assert _coverage_store.in_closure(
        (base, top), unit, "packages/base/src/base/mod.py"
    )
    assert _coverage_store.in_closure((base, top), unit, "tests/test_x.py")
    # Its ref stands beside the packages' under the one prefix.
    assert _coverage_store.suite_ref(LEG, unit).endswith("/workspace-tests")
    assert isinstance(unit, Package)
