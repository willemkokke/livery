"""The graded release trigger's classifier: additions ship, drops wait."""

from __future__ import annotations

from typing import Any

import pytest

from livery.toolroom.bench import _tasks


def _classify(monkeypatch, spans: dict[str, dict[str, Any]]) -> bool:
    monkeypatch.setattr(_tasks._surfaces, "load", lambda path: object())
    monkeypatch.setattr(_tasks, "_predecessor", lambda record, version: "0.0.0")
    monkeypatch.setattr(
        _tasks._surfaces,
        "changes",
        lambda record, *, since, until: spans[until],
    )
    return _tasks._additions_only({k: [k] for k in spans})


def test_pure_additions_light_the_green(monkeypatch):
    # `changes()` steps back: forward additions land under `drop`.
    assert _classify(monkeypatch, {"1.1.0": {"drop": {"": ["fix"]}}}) is True


def _refreshed(
    events: dict[str, list[str]],
    *,
    additions_only: bool,
    ingest_ok: bool = True,
    ingest: dict[str, list[str]] | None = None,
):
    return _tasks.Refreshed(
        read={},
        events=events,
        unreachable={},
        skipped=[],
        additions_only=additions_only,
        ingest_ok=ingest_ok,
        ingest=ingest or {},
    )


def test_a_refresh_that_moved_nothing_submits_nothing():
    calls: list[tuple[str, ...]] = []
    lines = _tasks.submit_refresh(
        _refreshed({}, additions_only=False),
        git=lambda *args: calls.append(args),
        submit=lambda argv: 0,
    )
    assert lines == ["  nothing moved: no branch, no pull request"]
    assert calls == []


def test_a_refused_submit_names_the_branch_that_stands():
    from datetime import date

    from livery.footman import Failed

    with pytest.raises(
        (Failed, SystemExit),
        match="exited 3; the branch chore/tools-refresh-20260914 stands",
    ):
        _tasks.submit_refresh(
            _refreshed({"ruff": ["0.9.0"]}, additions_only=True),
            git=lambda *args: None,
            submit=lambda argv: 3,
            on=date(2026, 9, 14),
        )


def test_additions_submit_armed_and_a_drop_waits_for_a_person():
    from datetime import date

    calls: list[tuple[str, ...]] = []
    argvs: list[list[str]] = []

    def submit(argv: list[str]) -> int:
        argvs.append(argv)
        return 0

    lines = _tasks.submit_refresh(
        _refreshed(
            {"ruff": ["0.9.0"], "bun": ["1.3.14"]},
            additions_only=True,
            ingest={
                "bun": ["  bun 1.3.14: every check passed on 2 host(s), against 1.3.13"]
            },
        ),
        git=lambda *args: calls.append(args),
        submit=submit,
        on=date(2026, 9, 14),
        dry_run=False,
    )
    assert calls[0] == ("switch", "-c", "chore/tools-refresh-20260914")
    assert calls[1] == ("add", "-A")
    assert calls[2][:2] == ("commit", "-m") and "bun, ruff" in calls[2][2]
    assert "bun 1.3.14: every check passed on 2 host(s)" in calls[2][2]  # the summary
    assert argvs[0][1:] == [
        "submit",
        "--title=chore(toolroom): tool refresh 2026-09-14",
        "--armed",
    ]
    assert lines == [
        "  refreshed bun, ruff on chore/tools-refresh-20260914",
        "  submitted armed: additions only, every ingest check passed",
    ]
    argvs.clear()
    lines = _tasks.submit_refresh(
        _refreshed({"ruff": ["0.9.0"]}, additions_only=False),
        git=lambda *args: None,
        submit=submit,
        on=date(2026, 9, 14),
        dry_run=False,
    )
    assert "--armed" not in argvs[0]
    assert lines[1] == "  submitted unarmed: a surface lost something, a person decides"
    # Additions only, but a deployment that does not stand: a person decides.
    argvs.clear()
    lines = _tasks.submit_refresh(
        _refreshed({"ruff": ["0.9.0"]}, additions_only=True, ingest_ok=False),
        git=lambda *args: None,
        submit=submit,
        on=date(2026, 9, 14),
        dry_run=False,
    )
    assert "--armed" not in argvs[0]
    assert (
        lines[1]
        == "  submitted unarmed: an ingest check found something, a person decides"
    )


def test_a_dry_run_says_what_it_would_do_and_touches_nothing():
    from datetime import date

    calls: list[tuple[str, ...]] = []
    lines = _tasks.submit_refresh(
        _refreshed({"ruff": ["0.9.0"]}, additions_only=True, ingest_ok=False),
        git=lambda *args: calls.append(args),
        submit=lambda argv: 99,
        on=date(2026, 9, 14),
        dry_run=True,
    )
    assert calls == []
    assert lines == [
        "  would refresh ruff on chore/tools-refresh-20260914",
        "  would submit unarmed: an ingest check found something, a person decides",
    ]
    assert _refreshed({}, additions_only=True).armed is True
    assert _refreshed({}, additions_only=True, ingest_ok=False).armed is False


def test_the_event_versions_with_a_host_are_verified_and_a_finding_holds(monkeypatch):
    """A tool with no host stages nothing; one with a host is checked per version."""
    from livery.toolroom.bench import _ingest
    from livery.toolroom.store import Artifact, Layout, Record, RecordDelta

    sha = "d8b96221828ad6f97ac7ac0ab7e95872341af763001e8803e8267652c2652620"
    hosted = Record(
        "tool",
        kind="archive",
        hosts=("linux-x64",),
        layout=Layout(entry_points=("tool",), paths=(".",)),
        deltas=(
            RecordDelta(1, "1.0.0", "", {"linux-x64": Artifact("https://x/1", sha)}),
            RecordDelta(2, "1.1.0", "", {"linux-x64": Artifact("https://x/2", sha)}),
        ),
    )
    records = {"tool": hosted}
    monkeypatch.setattr(_tasks._surfaces, "load", lambda path: records.get(path.stem))
    monkeypatch.setattr(_tasks, "_bench_store", lambda: "the store")
    asked: list[tuple[str, str]] = []

    def verify(record, version, *, store):
        asked.append((record.name, version))
        finding = _ingest.Finding(
            "paths", "linux-x64", "path directory '.' is not in the tree"
        )
        return _ingest.Report(
            record.name,
            version,
            "1.0.0",
            ("linux-x64",),
            (finding,) if version == "1.1.0" else (),
        )

    monkeypatch.setattr(_ingest, "verify", verify)
    lines, ok = _tasks._ingest_events({"tool": ["1.1.0"], "missing": ["2.0.0"]})
    assert asked == [("tool", "1.1.0")] and ok is False
    assert lines["tool"][0] == "  tool 1.1.0: 1 finding(s), against 1.0.0"
    assert (
        lines["tool"][1]
        == "    paths on linux-x64: path directory '.' is not in the tree"
    )
    # Nothing with a host: no store is built and the checks pass.
    monkeypatch.setattr(
        _tasks, "_bench_store", lambda: (_ for _ in ()).throw(AssertionError)
    )
    assert _tasks._ingest_events({"missing": ["2.0.0"]}) == ({}, True)


def test_any_removal_holds_for_a_human(monkeypatch):
    assert (
        _classify(
            monkeypatch,
            {"1.1.0": {"drop": {"": ["fix"]}}, "2.0.0": {"add": {"": ["old"]}}},
        )
        is False
    )


def test_no_events_is_not_safe_to_ship():
    assert _tasks._additions_only({}) is False
    assert _tasks._additions_only({"ruff": []}) is False
