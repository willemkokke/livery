"""The six-host point's task: each failure first, then the tool that installs, runs and reads."""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest

from livery.footman import Failed
from livery.toolroom.bench import _tasks
from livery.toolroom.store import (
    Artifact,
    Ensured,
    Layout,
    Option,
    Record,
    RecordDelta,
    StoreError,
    ToolSpec,
    Verb,
    resolve,
)

SHA = "d8b96221828ad6f97ac7ac0ab7e95872341af763001e8803e8267652c2652620"
HOST = "linux-x64"


class _FakeStore:
    """A store that installs into a directory the test filled, or refuses."""

    host = HOST

    def __init__(self, tool_dir: Path, *, refuse: str = "") -> None:
        self.tool_dir = tool_dir
        self.refuse = refuse
        self.asked: list[tuple[str, str]] = []

    def ensure(self, record: Record, version: str) -> Ensured:
        self.asked.append((record.name, version))
        if self.refuse:
            raise StoreError(self.refuse)
        deployment = resolve(record, version, HOST)
        return Ensured(record.name, version, True, self.tool_dir, deployment, None)


def _records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *hosts: str) -> Path:
    root = tmp_path / "records"
    Record(
        "tea",
        hosts=(HOST, "windows-x64"),
        layout=Layout(file="tea", entry_points=("tea",), paths=(".",)),
        host_layouts={"windows-x64": Layout(file="tea.exe", entry_points=("tea.exe",))},
        deltas=(
            RecordDelta(
                1,
                "0.15.0",
                "",
                {h: Artifact(f"https://x/0.15/{h}", SHA) for h in hosts or (HOST,)},
            ),
            RecordDelta(
                2,
                "0.16.0",
                "",
                {h: Artifact(f"https://x/0.16/{h}", SHA) for h in hosts or (HOST,)},
            ),
        ),
    ).save(root)
    monkeypatch.setattr(_tasks, "_RECORDS", root)
    return root


def _spec(*options: str) -> ToolSpec:
    return ToolSpec(
        name="tea",
        help="Gitea CLI.",
        version="0.16.0",
        verbs=(
            Verb(
                name="",
                help="tea",
                options=tuple(Option(o, (f"--{o}",)) for o in options),
            ),
        ),
    )


def test_a_tool_with_no_build_for_the_host_is_skipped(tmp_path, monkeypatch):
    _records(tmp_path, monkeypatch, "windows-x64")
    store = _FakeStore(tmp_path / "tool")
    (checks,) = _tasks.verify_host(store=store, only="tea")
    assert (checks.outcome, checks.detail) == ("skipped", "no build for linux-x64")
    assert store.asked == []
    assert str(checks) == "  tea: skipped, no build for linux-x64"


def test_an_install_the_store_refuses_is_a_failure_naming_it(tmp_path, monkeypatch):
    _records(tmp_path, monkeypatch)
    store = _FakeStore(
        tmp_path / "tool", refuse="tea@0.16.0: the origin did not answer"
    )
    (check,) = _tasks.verify_host(store=store, only="tea")
    assert (
        check.outcome == "failed"
        and check.detail == "install: tea@0.16.0: the origin did not answer"
    )
    assert store.asked == [("tea", "0.16.0")]  # the newest with a build


def test_an_entry_point_that_fails_or_hangs_or_reads_as_nothing_is_a_failure(
    tmp_path, monkeypatch
):
    _records(tmp_path, monkeypatch)
    tool_dir = tmp_path / "tool"
    tool_dir.mkdir()
    (tool_dir / "tea").write_text("#!/bin/sh\necho tea\n")
    store = _FakeStore(tool_dir)
    argvs: list[list[str]] = []

    def exits(argv: list[str]) -> tuple[int, str]:
        argvs.append(argv)
        return 2, "usage: tea"

    (check,) = _tasks.verify_host(
        store=store, only="tea", run=exits, extract=lambda d: _spec("x")
    )
    assert (
        check.outcome == "failed" and check.detail == "tea --help exited 2: usage: tea"
    )
    assert argvs == [[str(tool_dir / "tea"), "--help"]]

    def hangs(argv: list[str]) -> tuple[int, str]:
        raise subprocess.TimeoutExpired(argv, 120)

    (check,) = _tasks.verify_host(
        store=store, only="tea", run=hangs, extract=lambda d: _spec("x")
    )
    assert check.detail == "tea --help did not return in time"

    def missing(argv: list[str]) -> tuple[int, str]:
        raise OSError("no such file")

    (check,) = _tasks.verify_host(
        store=store, only="tea", run=missing, extract=lambda d: _spec("x")
    )
    assert check.detail == "tea did not start: no such file"
    (check,) = _tasks.verify_host(
        store=store, only="tea", run=lambda a: (0, "ok"), extract=lambda d: _spec()
    )
    assert check.detail == "the surface does not describe the tool"


def test_a_tool_that_installs_runs_and_reads_passes_and_the_verb_counts(
    tmp_path, monkeypatch, capsys
):
    _records(tmp_path, monkeypatch)
    tool_dir = tmp_path / "tool"
    tool_dir.mkdir()
    (tool_dir / "tea").write_text("#!/bin/sh\necho tea\n")
    store = _FakeStore(tool_dir)
    seen: list[str] = []

    def extract(driver: Any) -> ToolSpec:
        import os

        seen.append(os.environ.get("PATH", "").split(os.pathsep)[0])
        return _spec("login", "issues")

    (check,) = _tasks.verify_host(
        store=store, only="tea", run=lambda a: (0, "tea 0.16.0"), extract=extract
    )
    assert (check.outcome, check.detail) == ("ok", "tea runs, 2 option(s) read")
    assert seen == [str(tool_dir)]  # the installed tree answers, first on PATH
    monkeypatch.setattr(_tasks, "_bench_store", lambda: store)
    monkeypatch.setattr(_tasks, "_run_entry", lambda argv: (0, "tea"))
    monkeypatch.setattr(_tasks, "_extract", lambda driver, home=None: _spec("login"))
    _tasks.tools_verify_host(only="tea")
    out = capsys.readouterr().out
    assert (
        "  tea 0.16.0: ok, tea runs, 1 option(s) read" in out
        and "  1 of 1 tool(s) pass" in out
    )
    monkeypatch.setattr(_tasks, "_run_entry", lambda argv: (1, "boom"))
    with pytest.raises(
        (Failed, SystemExit), match=r"0 of 1 tool\(s\) pass: tea failed on this host"
    ):
        _tasks.tools_verify_host(only="tea")


def test_the_real_run_helper_reports_the_exit_and_the_last_line():
    code, tail = _tasks._run_entry(["sh", "-c", "echo one; echo two; exit 3"])
    assert (code, tail) == (3, "two")


def test_the_bench_declares_the_six_host_point_fortnightly():
    contract = Path(__file__).resolve().parents[1] / "workshop.toml"
    points = tomllib.loads(contract.read_text(encoding="utf-8"))["ci"]["point"]
    (point,) = points
    assert point["name"] == "tool-hosts" and point["task"] == "tools.verify-host"
    assert point["every"] == "2w"
    assert point["runners"] == [
        "ubuntu-latest",
        "ubuntu-24.04-arm",
        "macos-latest",
        "macos-15-intel",
        "windows-latest",
        "windows-11-arm",
    ]


def test_a_file_that_is_not_a_program_is_checked_for_presence_alone(
    tmp_path, monkeypatch
):
    """cmake-conan's provider is a CMake module: nothing runs, nothing is
    read; missing from the tree it fails, present it passes.
    """
    from livery.toolroom.bench import _drivers

    root = tmp_path / "records"
    Record(
        "tea",
        hosts=(HOST,),
        layout=Layout(file="tea", env={"TEA_FILE": "$package/tea"}),
        deltas=(
            RecordDelta(1, "0.16.0", "", {HOST: Artifact("https://x/0.16/tea", SHA)}),
        ),
    ).save(root)
    monkeypatch.setattr(_tasks, "_RECORDS", root)
    monkeypatch.setattr(_drivers, "DRIVERS", (_drivers.Driver("tea", source="manual"),))
    (tmp_path / "tool").mkdir()
    store = _FakeStore(tmp_path / "tool")
    (check,) = _tasks.verify_host(store=store, only="tea")
    assert check.outcome == "failed" and "not in the installed tree" in check.detail
    (tmp_path / "tool" / "tea").write_text("# a file")
    (check,) = _tasks.verify_host(store=store, only="tea")
    assert check.outcome == "ok" and "a file and not a program" in check.detail
