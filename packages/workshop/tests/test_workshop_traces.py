"""The legs' traces: the channel, the contract, and the run window.

Refusals first: a contract that says no, a contract that says something
wrong, a leg nothing named, a trace that is not there, and origin's own
refusal. Then the push that works, the mirror that brings none of it, and
the window the janitor applies.

Real git against a bare repository standing in for origin, so a refusal is
the transport's own words.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from livery.workshop import _state, _traces
from workshop_seeds import Seeds, _seed_home, seed_copier  # noqa: F401


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return done.stdout


def _seed(base: Path) -> None:
    """A bare origin with one commit on main, and a clone of it."""
    origin = base / "origin.git"
    _git(base, "init", "-q", "--bare", "--initial-branch=main", str(origin))
    work = base / "work"
    _git(base, "clone", "-q", str(origin), str(work))
    _git(work, "config", "user.name", "tester")
    _git(work, "config", "user.email", "tester@example.invalid")
    (work / "README.md").write_text("the repository\n")
    _git(work, "add", "README.md")
    _git(work, "commit", "-qm", "init")
    _git(work, "push", "-q", "-u", "origin", "main")


@pytest.fixture
def work(seeds: Seeds, tmp_path: Path) -> Path:
    """A clone whose origin is a bare repository, with a contract in it."""
    seeds("traces", _seed)
    root = tmp_path / "work"
    _contract(root)
    return root


def _contract(root: Path, **keys: object) -> None:
    """Write a workspace contract naming *keys* under ``[ci]``."""
    lines = ["[project]", 'name = "probe"', "", "[ci]"]
    for key, value in keys.items():
        spelled = str(value).lower() if isinstance(value, bool) else repr(value)
        lines.append(f"{key.replace('_', '-')} = {spelled}")
    root.mkdir(parents=True, exist_ok=True)
    (root / "workshop.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def _outside_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "GITHUB_ACTIONS",
        "GITEA_ACTIONS",
        "GITLAB_CI",
        "GITHUB_RUN_ID",
        "GITHUB_EVENT_NAME",
        "GITHUB_REF",
        "WORKSHOP_LEG",
        "WORKSHOP_SNAPSHOT",
    ):
        monkeypatch.delenv(name, raising=False)


def _in_ci(
    monkeypatch: pytest.MonkeyPatch, *, run: str = "7", leg: str = "check"
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_RUN_ID", run)
    monkeypatch.setenv("WORKSHOP_LEG", leg)


def _trace_file(root: Path, name: str = "fm-profile.json") -> Path:
    path = root / name
    path.write_text(
        json.dumps({"traceEvents": [], "originEpochUs": 1.0}), encoding="utf-8"
    )
    return path


def test_the_contract_answers_its_defaults_when_it_says_nothing(work: Path) -> None:
    kept, why = _traces.policy(work)
    assert why == ""
    assert kept == _traces.Policy(
        legs=_traces.LEGS_DEFAULT,
        window=_traces.WINDOW_DEFAULT,
        into=_traces.INTO_DEFAULT,
    )


def test_a_key_of_the_wrong_type_is_named_and_its_default_stands(work: Path) -> None:
    """A typo must be visible and must not stop a job.

    Whether a timeline is kept is not worth failing a leg over, and a
    key that reads as false by accident would keep nothing at all and
    say nothing about why.
    """
    _contract(work, profile_legs="yes", profile_window="lots", profile_into=17)
    kept, why = _traces.policy(work)
    assert kept.legs is _traces.LEGS_DEFAULT
    assert kept.window == _traces.WINDOW_DEFAULT
    assert kept.into == _traces.INTO_DEFAULT
    assert "[ci] profile-legs is 'yes'; it is true or false" in why
    assert "[ci] profile-window is 'lots'; it is a whole number of runs" in why
    assert "[ci] profile-into is 17; it is a path" in why


def test_a_contract_that_wants_no_traces_pushes_nothing_and_says_nothing(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _contract(work, profile_legs=False)
    _in_ci(monkeypatch)
    assert _traces.push(work, _trace_file(work)) == ""
    assert _state.list_refs(work, _traces.TRACES.prefix) == {}


def test_a_run_that_is_not_ci_keeps_its_trace_on_the_machine(work: Path) -> None:
    assert "not a CI run" in _traces.push(work, _trace_file(work))


def test_a_leg_the_runner_never_named_is_not_pushed(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _in_ci(monkeypatch, leg="")
    monkeypatch.delenv("WORKSHOP_LEG")
    assert "WORKSHOP_LEG names no leg" in _traces.push(work, _trace_file(work))


def test_a_trace_that_was_never_written_is_named(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _in_ci(monkeypatch)
    line = _traces.push(work, work / "fm-profile.json")
    assert "no trace at" in line and "nothing to push" in line


def test_a_push_origin_refuses_is_named_and_decides_nothing(
    work: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The trace is observational: origin's own words, and the job goes on."""
    from livery.workshop._ci_tasks import profile_push_flow

    _in_ci(monkeypatch)
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    _trace_file(work)
    profile_push_flow(work, Path("fm-profile.json"))
    printed = capsys.readouterr().out
    assert "the trace was not pushed" in printed


def test_the_trace_lands_on_the_run_s_ref_and_no_mirror_brings_it(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _in_ci(monkeypatch, run="41", leg="check-ubuntu-latest-3.14")
    trace = _trace_file(work)
    line = _traces.push(work, trace)
    ref = _traces.TRACES.series("41", "check-ubuntu-latest-3.14").ref
    # The family spells every key part ref-safe, so the leg's dotted
    # python arrives as a dash.
    assert ref == "refs/workshop-trace/run/41/check-ubuntu-latest-3-14"
    assert ref in line
    found = _state.read(work, ref)
    assert not found.failed and found.files is not None
    assert json.loads(found.files[_traces.TRACE_FILE])["originEpochUs"] == 1.0
    # The one refspec a sync mirrors is the store's own namespace, so a
    # fetch brings no trace and the gate reads none.
    assert _state.fetch_store(work) == (0, "")
    assert _state.list_refs(work, _state.FETCHED_NAMESPACE) == {}


def test_the_janitor_keeps_the_window_s_newest_runs_and_drops_the_rest(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The count is the whole bound: the files are traces, not rows.

    Ranked by the run id, which every forge hands out increasing, so
    the janitor never fetches a trace to learn how old it is.
    """
    _contract(work, profile_window=2)
    trace = _trace_file(work)
    for run in ("9", "10", "11"):
        for leg in ("check", "docs"):
            _in_ci(monkeypatch, run=run, leg=leg)
            assert _traces.TRACES.series(run, leg).ref in _traces.push(work, trace)
    lines = _state.sweep(work, (_traces.TRACES,), remote=True)
    held = _state.list_refs(work, _traces.TRACES.prefix)
    assert held is not None
    assert sorted(ref.split("/")[-2] for ref in held) == ["10", "10", "11", "11"]
    assert any("beyond the newest 2 run(s) kept" in line for line in lines)
    # And a second sweep finds nothing left to do.
    assert not [
        line
        for line in _state.sweep(work, (_traces.TRACES,), remote=True)
        if "dropped" in line
    ]


def test_a_good_into_is_read_and_a_wrong_key_stops_the_push(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A contract that names where an assembled file lands is read.

    And a key of the wrong type stops the push rather than pushing
    against a policy nobody meant: the line names the key, so the
    workspace can be corrected before the next run.
    """
    _contract(work, profile_into="build/traces")
    kept, why = _traces.policy(work)
    assert (kept.into, why) == ("build/traces", "")
    _contract(work, profile_window=-4)
    _in_ci(monkeypatch)
    line = _traces.push(work, _trace_file(work))
    assert line == "profile: [ci] profile-window is -4; it is a whole number of runs"
    assert _state.list_refs(work, _traces.TRACES.prefix) == {}
