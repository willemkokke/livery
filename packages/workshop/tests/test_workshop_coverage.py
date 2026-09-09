"""The coverage floors: parent mode, the grace, and the contract read."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from livery.footman import Failed
from livery.workshop import _coverage_store
from livery.workshop._backends import _python
from livery.workshop._packages import Package

_FAILURES = (SystemExit, Failed)


def _record(written: list[dict[str, object]]) -> Callable[..., str]:
    """A stand-in for the store's write: it records the row and reports success."""

    def _write(root: Path, **kw: object) -> str:
        written.append(kw)
        return ""

    return _write


def _package(tmp_path: Path, name: str, extra: str = "") -> Package:
    directory = tmp_path / "packages" / name
    directory.mkdir(parents=True)
    (directory / "workshop.toml").write_text(
        f'type = "python"\nname = "livery-{name}"\n{extra}'
    )
    return Package(
        directory=directory,
        path=f"packages/{name}",
        name=f"livery-{name}",
        type="python",
        depends=(),
    )


def test_the_floor_policy_comes_from_the_contract_and_refuses_nonsense(
    tmp_path: Path,
) -> None:
    bare = _package(tmp_path, "bare")
    assert _python.coverage_policy(bare) is None
    assert _python.coverage_floor(bare) is None
    floored = _package(tmp_path, "floored", "[qa]\ncoverage-floor = 87\n")
    assert _python.coverage_policy(floored) == _python.FloorPolicy(87.0, False, 0.5)
    assert _python.coverage_floor(floored) == 87.0
    ratchet = _package(
        tmp_path,
        "ratchet",
        '[qa]\ncoverage-floor = "auto-ratchet"\ncoverage-epsilon = 1.5\n',
    )
    assert _python.coverage_policy(ratchet) == _python.FloorPolicy(None, True, 1.5)
    assert _python.coverage_floor(ratchet) is None
    for name, extra, words in (
        ("odd", '[qa]\ncoverage-floor = "high"\n', "auto-ratchet"),
        ("tight", '[qa]\ncoverage-floor = 90\ncoverage-epsilon = "tight"\n', "points"),
        ("negative", "[qa]\ncoverage-floor = 90\ncoverage-epsilon = -1\n", "negative"),
    ):
        with pytest.raises(_FAILURES, match=words):
            _python.coverage_policy(_package(tmp_path, name, extra))


def test_enforcement_grants_the_epsilon_and_no_more(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _package(tmp_path, "thing", "[qa]\ncoverage-floor = 90\n")
    measured = {"packages/thing": 89.6}
    monkeypatch.setattr(_python, "measured_coverage", lambda root, packages: measured)
    assert _python.enforce_coverage(tmp_path, (package,)) == measured  # inside
    measured["packages/thing"] = 89.4
    with pytest.raises(_FAILURES) as caught:
        _python.enforce_coverage(tmp_path, (package,))
    assert "below the committed floor" in str(caught.value)
    wide = _package(
        tmp_path, "wide", "[qa]\ncoverage-floor = 90\ncoverage-epsilon = 2\n"
    )
    measured["packages/wide"] = 88.1
    _python.enforce_coverage(tmp_path, (wide,))  # a declared epsilon widens the floor


def _ratchet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, percent: float
) -> Package:
    directory = tmp_path / "packages" / "thing"
    if directory.is_dir():
        package = Package(
            directory=directory,
            path="packages/thing",
            name="livery-thing",
            type="python",
            depends=(),
        )
    else:
        package = _package(tmp_path, "thing", '[qa]\ncoverage-floor = "auto-ratchet"\n')
    monkeypatch.setattr(
        _python, "measured_coverage", lambda root, packages: {"packages/thing": percent}
    )
    return package


def test_under_auto_ratchet_an_unreadable_store_falls_open_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop import _coverage_marks

    package = _ratchet(tmp_path, monkeypatch, 50.0)
    monkeypatch.setattr(_coverage_marks, "marks", lambda root: (None, "remote down"))

    def _never(root: Path, **kw: object) -> str:
        raise AssertionError("an unread store is never written")

    monkeypatch.setattr(_coverage_marks, "write_mark", _never)
    _in_ci(monkeypatch, "check-a")
    assert _python.enforce_coverage(tmp_path, (package,)) == {"packages/thing": 50.0}
    out = capsys.readouterr().out
    assert "auto-ratchet: remote down; the gate falls open and records nothing" in out


def test_the_first_run_records_the_mark_only_in_ci(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop import _coverage_marks

    package = _ratchet(tmp_path, monkeypatch, 80.0)
    monkeypatch.setattr(_coverage_marks, "marks", lambda root: ({}, ""))
    written: list[dict[str, object]] = []
    monkeypatch.setattr(_coverage_marks, "write_mark", _record(written))
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITLAB_CI", raising=False)
    _python.enforce_coverage(tmp_path, (package,))
    out = capsys.readouterr().out
    assert "no mark yet; this run records it" in out
    assert "a CI run records the mark; this run does not" in out
    assert written == []
    _in_ci(monkeypatch, "check-a")
    _python.enforce_coverage(tmp_path, (package,))
    assert "    recorded" in capsys.readouterr().out
    assert written == [
        {
            "package": "packages/thing",
            "value": 80.0,
            "kind": "first",
            "by": "run 7",
            "ci_only": True,
        }
    ]


def test_a_clearing_run_ratchets_a_dip_passes_and_a_fall_names_the_accept_verb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop import _coverage_marks

    mark = _coverage_marks.Mark("packages/thing", 90.0, "first", "run 3", "", "when")
    monkeypatch.setattr(
        _coverage_marks, "marks", lambda root: ({mark.package: mark}, "")
    )
    written: list[dict[str, object]] = []
    monkeypatch.setattr(_coverage_marks, "write_mark", _record(written))
    _in_ci(monkeypatch, "check-a")
    package = _ratchet(tmp_path, monkeypatch, 90.6)
    _python.enforce_coverage(tmp_path, (package,))
    assert "new mark: 90.6%" in capsys.readouterr().out
    assert [kw["kind"] for kw in written] == ["ratchet"]
    _ratchet(tmp_path, monkeypatch, 89.6)
    _python.enforce_coverage(tmp_path, (package,))
    assert len(written) == 1  # a dip inside epsilon passes and moves nothing
    _ratchet(tmp_path, monkeypatch, 89.4)
    with pytest.raises(_FAILURES) as caught:
        _python.enforce_coverage(tmp_path, (package,))
    assert "below its mark of 90.0%" in str(caught.value)
    assert "coverage.accept packages/thing <value> --reason=" in str(caught.value)


class _Result:
    def __init__(self, code: int) -> None:
        self.code = code
        self.stdout = f"suite output (exit {code})\n" if code else ""
        self.stderr = ""


class _FakePytest:
    """Records each call's arguments and the environment it was bound with."""

    def __init__(self, codes: dict[str, int] | None = None) -> None:
        self.calls: list[tuple[tuple[str, ...], dict[str, str] | None]] = []
        self.codes = codes or {}
        self._env: dict[str, str] | None = None

    def opts(self, **kwargs: object) -> _FakePytest:
        env = kwargs.get("env")
        self._env = dict(env) if isinstance(env, dict) else None
        return self

    def __call__(self, *args: str) -> _Result:
        self.calls.append((args, self._env))
        self._env = None
        return _Result(self.codes.get(args[0] if args else "", 0))


def _suite(tmp_path: Path, name: str) -> Package:
    package = _package(tmp_path, name, "[qa]\ncoverage-floor = 1\n")
    (package.directory / "tests").mkdir()
    return package


def test_a_measuring_parent_runs_one_pooled_process_and_adds_no_meter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    thing = _suite(tmp_path, "thing")
    other = _suite(tmp_path, "other")
    fake = _FakePytest()
    monkeypatch.setattr(_python, "pytest", fake)

    def refuse(*_args: object) -> None:
        raise AssertionError("enforcement belongs to the aggregating job")

    monkeypatch.setattr(_python, "enforce_coverage", refuse)
    monkeypatch.setenv("COVERAGE_PROCESS_START", "pyproject.toml")
    _python.run_test(packages=(thing, other), root=tmp_path, scoped=True)
    # One process for every suite, under the leg's own prefix: the
    # plugin names each test's context, and the leg splits the data.
    assert fake.calls == [(("packages/thing/tests", "packages/other/tests"), None)]
    assert not any("--cov" in args for args, _env in fake.calls)


def test_without_a_parent_the_meter_and_the_preview_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _package(tmp_path, "thing", "[qa]\ncoverage-floor = 1\n")
    seen: list[tuple[str, ...]] = []
    enforced: list[Path] = []

    class FakeTool:
        def opts(self, **_kwargs: object) -> FakeTool:
            return self

        def __call__(self, *args: str) -> None:
            seen.append(args)

    monkeypatch.setattr(_python, "pytest", FakeTool())
    monkeypatch.setattr(
        _python, "report_coverage", lambda root, packages: enforced.append(root)
    )
    monkeypatch.delenv("COVERAGE_PROCESS_START", raising=False)
    _python.run_test(packages=(package,), root=tmp_path, scoped=True)
    assert any(arg == "--cov" for arg in seen[0])
    assert "packages/thing/tests" not in seen[0]  # no tests dir exists
    assert enforced == [tmp_path]


# --- the leg: its refusal, then what it stores --------------------------------


def test_a_leg_with_no_metered_data_refuses_naming_the_meter(tmp_path: Path) -> None:
    with pytest.raises(BaseException, match="COVERAGE_PROCESS_START"):
        _python.combine_leg(tmp_path, ())


def _in_ci(monkeypatch: pytest.MonkeyPatch, leg: str) -> None:
    for name in ("GITHUB_EVENT_PATH", "GITHUB_SHA", "GITHUB_REF", "GITHUB_JOB"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITEA_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_RUN_ID", "7")
    monkeypatch.setenv("WORKSHOP_LEG", leg)
    monkeypatch.delenv("COVERAGE_PROCESS_START", raising=False)


def _contextual_part(
    tmp_path: Path, suffix: str, recorded: dict[str, dict[str, list[int]]]
) -> None:
    """A metered part: *recorded* maps a context ("" for none) to files and lines."""
    from coverage import CoverageData

    data = CoverageData(basename=str(tmp_path / ".coverage"), suffix=suffix)
    for context, lines in recorded.items():
        data.set_context(context)
        data.add_lines(lines)
    data.write()


def test_a_leg_stores_each_suite_it_ran_within_its_closure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop._verified import FULL, write_marker

    x = _suite(tmp_path, "x")
    y = _suite(tmp_path, "y")
    x_source = str(_source(tmp_path, "x"))
    y_source = str(_source(tmp_path, "y"))
    # Import time (no context) touched both sources; x's test ran x's lines
    # and, through a shared process, one of y's; y's suite did not run.
    _contextual_part(
        tmp_path,
        "host.1.X",
        {
            "": {x_source: [1], y_source: [1]},
            "packages/x/tests/test_mod.py::test_one|run": {
                x_source: [2, 3],
                y_source: [2],
            },
        },
    )
    _contextual_part(tmp_path, "host.2.X", {"": {x_source: [4]}})
    write_marker(tmp_path, FULL, leg="check-a")
    stamped: list[dict[str, object]] = []

    def _capture(root: Path, run: object, **kw: object) -> str:
        stamped.append(kw)
        return ""

    monkeypatch.setattr(_coverage_store, "closure_id", lambda git, ps, p: "k" * 64)
    monkeypatch.setattr(_coverage_store, "stamp", _capture)
    monkeypatch.setattr(
        "livery.workshop._git_ops.GitOps.head_sha", lambda self: "a" * 40
    )
    _in_ci(monkeypatch, "check-a")
    _python.combine_leg(tmp_path, (x, y))
    out = capsys.readouterr().out
    # Only x's suite ran: its own lines plus the import-time lines of its closure.
    assert [kw["package"].path for kw in stamped] == ["packages/x"]  # type: ignore[attr-defined]
    assert stamped[0]["files"] == {"packages/x/src/livery/x/mod.py": [1, 2, 3, 4]}
    assert (
        "coverage store: packages/x stored for closure kkkkkkkkkkkk on check-a" in out
    )
    assert "packages/y" not in out
    assert (tmp_path / ".coverage").is_file()
    assert not (tmp_path / _python.SUITES_DATA).exists()


# --- the union: its refusals, then the reuse ----------------------------------


def _leg(
    tmp_path: Path,
    name: str,
    scope: str,
    packages: tuple[str, ...] = (),
    lines: dict[str, list[int]] | None = None,
    leg: str = "check-a",
) -> Path:
    """A collected leg: its marker, and its data when *lines* is given."""
    from coverage import CoverageData

    from livery.workshop._verified import write_marker

    folder = tmp_path / "coverage-data" / name
    folder.mkdir(parents=True)
    write_marker(folder, scope, packages, leg=leg)
    if lines is not None:
        data = CoverageData(basename=str(folder / ".coverage"))
        data.add_lines(lines)
        data.write()
    return folder


def _source(tmp_path: Path, name: str) -> Path:
    source = tmp_path / "packages" / name / "src" / "livery" / name / "mod.py"
    source.parent.mkdir(parents=True)
    source.write_text("a = 1\nb = 2\nc = 3\nd = 4\n")
    return source


def _stored(files: dict[str, list[int]], run: str = "7") -> _coverage_store.Stored:
    return _coverage_store.Stored(
        leg="check-a", package="", closure="k" * 64, run=run, sha="a" * 40, files=files
    )


def test_a_union_with_no_collected_leg_refuses_naming_the_directory(
    tmp_path: Path,
) -> None:
    with pytest.raises(BaseException, match="coverage-data"):
        _python.combine_union(tmp_path, ())
    (tmp_path / "coverage-data" / "bare").mkdir(parents=True)
    with pytest.raises(BaseException, match=r"leg bare: no readable fm-gate\.json"):
        _python.combine_union(tmp_path, ())


def test_a_leg_that_ran_its_gate_without_data_refuses_naming_the_leg(
    tmp_path: Path,
) -> None:
    _leg(tmp_path, "leg-a", "full")
    with pytest.raises(BaseException, match="leg leg-a ran its gate 'full'"):
        _python.combine_union(tmp_path, ())
    shutil.rmtree(tmp_path / "coverage-data")
    _leg(tmp_path, "leg-a", "odd", lines={})
    with pytest.raises(BaseException, match=r"leg leg-a: no readable fm-gate\.json"):
        _python.combine_union(tmp_path, ())


def test_a_skipped_suite_the_store_cannot_supply_refuses_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    x = _suite(tmp_path, "x")
    monkeypatch.setattr(_coverage_store, "closure_id", lambda git, ps, p: "k" * 64)
    monkeypatch.setattr(_coverage_store, "find", lambda root, **kw: (None, ""))
    _leg(tmp_path, "leg-a", "verified", lines={})
    with pytest.raises(
        _FAILURES,
        match=(
            "leg leg-a: no stored suite for packages/x at closure kkkkkkkkkkkk"
            " on check-a"
        ),
    ):
        _python.combine_union(tmp_path, (x,))
    monkeypatch.setattr(
        _coverage_store, "find", lambda root, **kw: (None, "remote down")
    )
    with pytest.raises(_FAILURES, match=r"\(remote down\)"):
        _python.combine_union(tmp_path, (x,))
    shutil.rmtree(tmp_path / "coverage-data")
    _leg(tmp_path, "leg-a", "nothing", leg="")
    with pytest.raises(_FAILURES, match="leg leg-a names no label"):
        _python.combine_union(tmp_path, (x,))
    assert not (tmp_path / ".coverage").exists()


def test_a_skipped_leg_reuses_every_suite_from_the_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    x = _suite(tmp_path, "x")
    source = str(_source(tmp_path, "x"))
    monkeypatch.setattr(_coverage_store, "closure_id", lambda git, ps, p: "k" * 64)
    asked: list[tuple[str, str, str]] = []

    def _find(
        root: Path, *, leg: str, package: Package, closure_key: str
    ) -> tuple[_coverage_store.Stored | None, str]:
        asked.append((leg, package.path, closure_key))
        return _stored({source: [1, 2, 3, 4]}), ""

    monkeypatch.setattr(_coverage_store, "find", _find)
    leg = _leg(tmp_path, "leg-a", "verified", lines={})
    assert _python.combine_union(tmp_path, (x,)) == (x,)
    assert asked == [("check-a", "packages/x", "k" * 64)]
    assert leg.is_dir()  # the union's combine consumed its inputs
    out = capsys.readouterr().out
    assert "coverage: packages/x on check-a: reused from run 7 (1 files)" in out
    assert "the union of 0 leg(s) and 1 reused suite(s)" in out
    assert _python.measured_coverage(tmp_path, (x,)) == {"packages/x": 100.0}


def test_a_narrowed_leg_reuses_the_suite_it_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    x = _suite(tmp_path, "x")
    y = _suite(tmp_path, "y")
    z = _package(tmp_path, "z", "[qa]\ncoverage-floor = 1\n")  # no suite of its own
    x_source = str(_source(tmp_path, "x"))
    y_source = str(_source(tmp_path, "y"))
    monkeypatch.setattr(_coverage_store, "closure_id", lambda git, ps, p: "k" * 64)
    monkeypatch.setattr(
        _coverage_store,
        "find",
        lambda root, **kw: (_stored({y_source: [1, 2, 3, 4]}, run="5"), ""),
    )
    _leg(tmp_path, "leg-a", "affected", ("packages/x",), lines={x_source: [1, 2, 3, 4]})
    assert _python.combine_union(tmp_path, (z, y, x)) == (z, y, x)
    out = capsys.readouterr().out
    assert "coverage: packages/y on check-a: reused from run 5 (1 files)" in out
    assert "unjudged" not in out
    assert "the union of 1 leg(s) and 1 reused suite(s)" in out
    assert _python.measured_coverage(tmp_path, (x, y)) == {
        "packages/x": 100.0,
        "packages/y": 100.0,
    }


def test_the_union_of_two_legs_covers_what_each_left_uncovered(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    package = _package(tmp_path, "x", "[qa]\ncoverage-floor = 95\n")
    source = str(_source(tmp_path, "x"))
    _leg(tmp_path, "leg-a", "full", lines={source: [1, 2]})
    _leg(tmp_path, "leg-b", "affected", ("packages/x",), lines={source: [3, 4]})
    _leg(tmp_path, "leg-c", "verified", lines={})
    assert _python.combine_union(tmp_path, (package,)) == (package,)
    assert (tmp_path / ".coverage").is_file()
    assert "the union of 2 leg(s) and 0 reused suite(s)" in capsys.readouterr().out
    measured = _python.measured_coverage(tmp_path, (package,))
    assert measured == {"packages/x": 100.0}
    _python.enforce_coverage(
        tmp_path, (package,)
    )  # each leg alone: 50%; the union: 100%
