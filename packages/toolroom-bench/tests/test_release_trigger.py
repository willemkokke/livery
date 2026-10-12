"""The graded release trigger's classifier: additions ship, drops wait."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from livery.toolroom.bench import _tasks
from livery.toolroom.tools import Result


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


def _git(
    calls: list[tuple[str, ...]], codes: dict[str, int] | None = None
) -> Callable[..., Result]:
    """A git that records each call and answers by its subcommand.

    Origin holds no branch unless *codes* says otherwise: `ls-remote`
    answers 2, git's own exit code for a ref origin does not have.
    Every other subcommand answers 0, and a code *codes* names comes
    with git's kind of complaint on stderr.
    """
    answers = {"ls-remote": 2, **(codes or {})}

    def run(*args: str) -> Result:
        calls.append(args)
        code = answers.get(args[0], 0)
        said = f"fatal: {args[0]} refused" if code not in (0, 2) else ""
        return Result(code, stderr=said)

    return run


def test_a_refresh_that_moved_nothing_submits_nothing():
    calls: list[tuple[str, ...]] = []
    lines = _tasks.submit_refresh(
        _refreshed({}, additions_only=False),
        git=_git(calls),
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
            git=_git([]),
            submit=lambda argv: 3,
            on=date(2026, 9, 14),
        )


def test_an_origin_that_will_not_answer_stops_the_submit_before_any_commit():
    """Asking origin for the day's branch comes first, so a refresh that
    cannot ask makes no branch and no commit: it names origin's answer,
    and the files it wrote stand in the checkout as the walk left them.
    """
    from datetime import date

    from livery.footman import Failed

    calls: list[tuple[str, ...]] = []
    with pytest.raises(
        (Failed, SystemExit),
        match=(
            "could not ask origin for chore/tools-refresh-20260914:"
            " git ls-remote exited 128: fatal: ls-remote refused\n"
            "  nothing is committed"
        ),
    ):
        _tasks.submit_refresh(
            _refreshed({"ruff": ["0.9.0"]}, additions_only=True),
            git=_git(calls, {"ls-remote": 128}),
            submit=lambda argv: 0,
            on=date(2026, 9, 14),
            dry_run=False,
        )
    assert calls == [
        (
            "ls-remote",
            "--exit-code",
            "origin",
            "refs/heads/chore/tools-refresh-20260914",
        )
    ]


def test_a_git_step_that_fails_is_named_in_git_s_own_words():
    from datetime import date

    from livery.footman import Failed

    calls: list[tuple[str, ...]] = []
    with pytest.raises(
        (Failed, SystemExit), match="git switch exited 128: fatal: switch refused"
    ):
        _tasks.submit_refresh(
            _refreshed({"ruff": ["0.9.0"]}, additions_only=True),
            git=_git(calls, {"switch": 128}),
            submit=lambda argv: 0,
            on=date(2026, 9, 14),
            dry_run=False,
        )
    assert [call[0] for call in calls] == ["ls-remote", "fetch", "merge-base", "switch"]


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
        git=_git(calls),
        submit=submit,
        on=date(2026, 9, 14),
        dry_run=False,
    )
    assert calls[0] == (
        "ls-remote",
        "--exit-code",
        "origin",
        "refs/heads/chore/tools-refresh-20260914",
    )
    assert calls[1:3] == [
        ("fetch", "origin", "HEAD"),
        ("merge-base", "--is-ancestor", "HEAD", "FETCH_HEAD"),
    ]
    assert calls[3] == ("switch", "-c", "chore/tools-refresh-20260914")
    assert calls[4] == ("add", "-A")
    assert calls[5][:2] == ("commit", "-m") and "bun, ruff" in calls[5][2]
    assert "bun 1.3.14: every check passed on 2 host(s)" in calls[5][2]  # the summary
    assert len(calls) == 6  # origin holds no branch: nothing to compare with
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
        git=_git([]),
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
        git=_git([]),
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
        git=_git(calls),
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
        kind="download",
        hosts=("linux-x64",),
        layout=Layout(entry_points=("tool",), paths=(".",)),
        deltas=(
            RecordDelta(
                1, "1.0.0", "", {"linux-x64": Artifact("https://x/1.zip", sha)}
            ),
            RecordDelta(
                2, "1.1.0", "", {"linux-x64": Artifact("https://x/2.zip", sha)}
            ),
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


def test_the_submit_starts_the_runner_as_this_interpreter_s_module(monkeypatch):
    # A hosted runner's task environment has no path to the runner's
    # console script, so a spawn by name finds nothing there.
    import subprocess
    import sys
    from types import SimpleNamespace

    seen: list[list[str]] = []

    def _run(command, **kwargs):
        seen.append(list(command))
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr(subprocess, "run", _run)
    monkeypatch.setattr(
        _tasks, "_spawn_context", lambda: (Path("."), {"PATH": "/nowhere"})
    )
    code = _tasks._run_submit(["fm", "submit", "--title=t", "--armed"])
    assert code == 7
    assert seen == [
        [sys.executable, "-m", "livery.footman", "submit", "--title=t", "--armed"]
    ]


def test_the_runner_starts_with_no_fm_on_the_path(tmp_path, monkeypatch):
    """The submit's spawn names this interpreter and the runner's module,
    never the console script, so it starts where the task's environment
    has no `fm` on its PATH, as on a hosted runner.
    """
    import os

    nothing = tmp_path / "bin"
    nothing.mkdir()
    env = {**os.environ, "PATH": str(nothing)}
    monkeypatch.setattr(_tasks, "_spawn_context", lambda: (tmp_path, env))
    assert _tasks._run_submit(["fm", "--version"]) == 0


_DAY = "chore/tools-refresh-20261012"


def _run_git(where: Path, *args: str) -> str:
    """Run git in *where* for a test's own setup or reading; its output."""
    import subprocess

    done = subprocess.run(
        ["git", *args], cwd=where, check=True, capture_output=True, text=True
    )
    return done.stdout.strip()


def _write(where: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = where / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _origin_holding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, earlier: dict[str, str]
) -> tuple[Path, str]:
    """An origin whose day's branch holds an earlier refresh that wrote *earlier*.

    Main moves on after the branch is cut, as it does between a desk's
    refresh and a runner's. Returns a fresh clone of main, made the
    working directory, and the tip of the day's branch on origin.
    """
    for part, value in (("NAME", "Refresh Test"), ("EMAIL", "refresh@example.invalid")):
        monkeypatch.setenv(f"GIT_AUTHOR_{part}", value)
        monkeypatch.setenv(f"GIT_COMMITTER_{part}", value)
    origin, seed, runner = (
        tmp_path / "origin.git",
        tmp_path / "seed",
        tmp_path / "runner",
    )
    origin.mkdir()
    seed.mkdir()
    _run_git(origin, "init", "--bare", "--initial-branch=main")
    _run_git(seed, "init", "--initial-branch=main")
    _write(
        seed, {"records/uv.jsonl": "uv 0.12.5\n", "records/ruff.jsonl": "ruff 0.16.4\n"}
    )
    _run_git(seed, "add", "-A")
    _run_git(seed, "commit", "-m", "seed")
    _run_git(seed, "remote", "add", "origin", str(origin))
    _run_git(seed, "push", "origin", "main")
    _run_git(seed, "switch", "-c", _DAY)
    _write(seed, earlier)
    _run_git(seed, "add", "-A")
    _run_git(seed, "commit", "-m", "chore(toolroom): tool refresh 2026-10-12")
    _run_git(seed, "push", "origin", _DAY)
    _run_git(seed, "switch", "main")
    _write(seed, {"docs.md": "main moves on\n"})
    _run_git(seed, "add", "-A")
    _run_git(seed, "commit", "-m", "docs: main moves on")
    _run_git(seed, "push", "origin", "main")
    _run_git(tmp_path, "clone", str(origin), str(runner))
    monkeypatch.chdir(runner)
    return runner, _run_git(seed, "rev-parse", _DAY)


def _submitting(argvs: list[list[str]]) -> Callable[[list[str]], int]:
    def submit(argv: list[str]) -> int:
        argvs.append(argv)
        return 0

    return submit


def _on_its_own_tree(runner: Path, main: str) -> None:
    """The checkout stayed on its own history: the refresh's commit on main's tip."""
    assert _run_git(runner, "branch", "--show-current") == _DAY
    assert _run_git(runner, "rev-parse", "HEAD~1") == main
    # Main's later commit, which origin's older branch lacks, is still
    # in the working tree the run goes on reading.
    assert (runner / "docs.md").read_text(encoding="utf-8") == "main moves on\n"
    assert _run_git(runner, "status", "--porcelain") == ""


def test_a_refresh_that_differs_from_origin_s_branch_stops_for_a_person(
    tmp_path, monkeypatch
):
    """The day's branch on origin holds an earlier refresh that wrote a
    file differently: the refresh submits nothing, names the files, and
    says what a person does. Building on that branch would move the
    checkout onto an older tree under the run that reads it.
    """
    from datetime import date

    from livery.footman import Failed

    runner, _tip = _origin_holding(
        tmp_path, monkeypatch, {"records/uv.jsonl": "uv 0.12.7 read on macOS\n"}
    )
    main = _run_git(runner, "rev-parse", "HEAD")
    _write(runner, {"records/uv.jsonl": "uv 0.12.7 read on Linux\n"})
    argvs: list[list[str]] = []
    with pytest.raises(Failed) as raised:
        _tasks.submit_refresh(
            _refreshed({"uv": ["0.12.7"]}, additions_only=True),
            submit=_submitting(argvs),
            on=date(2026, 10, 12),
            dry_run=False,
        )
    assert str(raised.value) == (
        f"{_DAY} on origin holds an earlier refresh, and this one wrote"
        " records/uv.jsonl differently. Merge or close that pull request, then"
        " run the refresh again; this refresh's commit stands on the local"
        f" branch {_DAY}."
    )
    assert argvs == []
    _on_its_own_tree(runner, main)
    uv = (runner / "records" / "uv.jsonl").read_text(encoding="utf-8")
    assert uv == "uv 0.12.7 read on Linux\n"


def test_a_refresh_origin_already_holds_is_already_submitted(tmp_path, monkeypatch):
    """A second refresh on one day, from a runner or a desk, meets the
    day's branch on origin. When every file it wrote reads the same
    there, it submits nothing and says so, whatever else the branch
    holds; the commits main gained since the branch was cut are not a
    change of its own, and the checkout stays on its own tree.
    """
    from datetime import date

    earlier = {
        "records/uv.jsonl": "uv 0.12.7\n",
        "records/ruff.jsonl": "ruff 0.17.0 read on macOS\n",
    }
    runner, _tip = _origin_holding(tmp_path, monkeypatch, earlier)
    main = _run_git(runner, "rev-parse", "HEAD")
    _write(runner, {"records/uv.jsonl": "uv 0.12.7\n"})
    argvs: list[list[str]] = []
    lines = _tasks.submit_refresh(
        _refreshed({"uv": ["0.12.7"]}, additions_only=True),
        submit=_submitting(argvs),
        on=date(2026, 10, 12),
        dry_run=False,
    )
    assert lines == [
        f"  the day's refresh is already submitted: {_DAY} on origin holds"
        " every file this one wrote, so nothing is submitted"
    ]
    assert argvs == []
    _on_its_own_tree(runner, main)


def test_a_checkout_off_the_default_branch_submits_nothing(tmp_path, monkeypatch):
    """A dispatched nightly runs on the branch it was given. The day's
    branch is cut from HEAD, so a refresh there would carry that
    branch's commits into its pull request: it submits nothing, makes
    no branch and no commit, and the files it wrote stand.
    """
    from datetime import date

    runner, _tip = _origin_holding(
        tmp_path, monkeypatch, {"records/uv.jsonl": "uv 0.12.7\n"}
    )
    _run_git(runner, "switch", "-c", "feat/elsewhere")
    _write(runner, {"feature.py": "x = 1\n"})
    _run_git(runner, "add", "-A")
    _run_git(runner, "commit", "-m", "feat: elsewhere")
    head = _run_git(runner, "rev-parse", "HEAD")
    _write(runner, {"records/uv.jsonl": "uv 0.12.8\n"})
    argvs: list[list[str]] = []
    lines = _tasks.submit_refresh(
        _refreshed({"uv": ["0.12.8"]}, additions_only=True),
        submit=_submitting(argvs),
        on=date(2026, 10, 13),
        dry_run=False,
    )
    assert lines == [
        "  the checkout is not on origin's default branch, so nothing is"
        " submitted: a refresh submits from the default branch alone, and the"
        " files it wrote stand in the checkout"
    ]
    assert argvs == []
    assert _run_git(runner, "branch", "--show-current") == "feat/elsewhere"
    assert _run_git(runner, "rev-parse", "HEAD") == head
    assert _run_git(runner, "status", "--porcelain") == "M records/uv.jsonl"


def test_a_day_origin_has_no_branch_for_starts_one_from_the_checkout(
    tmp_path, monkeypatch
):
    """Git answers 2 for a branch origin does not hold, and the refresh
    then cuts the day's branch from the checkout's own HEAD.
    """
    from datetime import date

    runner, _tip = _origin_holding(
        tmp_path, monkeypatch, {"records/uv.jsonl": "uv 0.12.7\n"}
    )
    main = _run_git(runner, "rev-parse", "HEAD")
    _write(runner, {"records/uv.jsonl": "uv 0.12.8\n"})
    argvs: list[list[str]] = []
    lines = _tasks.submit_refresh(
        _refreshed({"uv": ["0.12.8"]}, additions_only=True),
        submit=_submitting(argvs),
        on=date(2026, 10, 13),
        dry_run=False,
    )
    assert lines[0] == "  refreshed uv on chore/tools-refresh-20261013"
    assert len(argvs) == 1
    assert (
        _run_git(runner, "branch", "--show-current") == "chore/tools-refresh-20261013"
    )
    assert _run_git(runner, "rev-parse", "HEAD~1") == main
