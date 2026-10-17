"""The CI run context, and the store's policy the workshop builds it with."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from livery import git
from livery.workshop import _ci_context
from livery.workshop._ci_context import state_store
from workshop_seeds import Seeds, _seed_home, seed_copier  # noqa: F401

REF = git.NAMESPACE + "metrics"


def _git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> str:
    done = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, env=env
    )
    return done.stdout


def _clone(tmp_path: Path, name: str, origin: Path) -> Path:
    work = tmp_path / name
    _git(tmp_path, "clone", "-q", str(origin), str(work))
    return work


def _seed(base: Path) -> None:
    """A bare origin with one commit on main and a clone, built once per session."""
    origin = base / "origin.git"
    _git(base, "init", "-q", "--bare", "--initial-branch=main", str(origin))
    work = _clone(base, "work", origin)
    (work / "README.md").write_text("the repository\n")
    _git(work, "add", "README.md")
    _git(work, "commit", "-qm", "init")
    _git(work, "push", "-q", "-u", "origin", "main")


@pytest.fixture
def repos(seeds: Seeds, tmp_path: Path) -> tuple[Path, Path]:
    """A bare origin with one commit on main, and a working clone of it."""
    seeds("state", _seed)
    return tmp_path / "origin.git", tmp_path / "work"


@pytest.fixture(autouse=True)
def _outside_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "GITHUB_ACTIONS",
        "GITEA_ACTIONS",
        "GITLAB_CI",
        "GITHUB_EVENT_NAME",
        "GITHUB_EVENT_PATH",
        "GITHUB_SHA",
        "GITHUB_RUN_ID",
        "GITHUB_REF",
        "GITHUB_JOB",
    ):
        monkeypatch.delenv(name, raising=False)


def test_put_refuses_a_ci_only_write_from_a_local_run(
    repos: tuple[Path, Path],
) -> None:
    _, work = repos
    why = git.put(state_store(work), REF, {"a.json": "{}"}, message="m", ci_only=True)
    assert "only a CI run writes this series" in why
    assert _git(work, "ls-remote", "origin", REF) == ""


def test_run_context_reads_each_forge_and_is_none_locally() -> None:
    assert _ci_context.run_context({}) is None
    github = _ci_context.run_context(
        {
            "GITHUB_ACTIONS": "true",
            "GITHUB_RUN_ID": "7",
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_REF": "refs/heads/main",
        }
    )
    assert github is not None
    assert github == _ci_context.RunContext("github", "7", "push", "refs/heads/main")
    gitea = _ci_context.run_context(
        {"GITHUB_ACTIONS": "true", "GITEA_ACTIONS": "true", "GITHUB_RUN_ID": "9"}
    )
    assert gitea is not None and gitea.forge == "gitea" and gitea.run_id == "9"
    gitlab = _ci_context.run_context(
        {
            "GITLAB_CI": "true",
            "CI_PIPELINE_ID": "42",
            "CI_PIPELINE_SOURCE": "push",
            "CI_COMMIT_REF_NAME": "main",
        }
    )
    assert gitlab == _ci_context.RunContext("gitlab", "42", "push", "main")


def test_the_run_context_shard_is_the_legs_and_one_without_a_label() -> None:
    from livery.workshop import Shard

    # A label whose tail is outside the form raises naming it, before
    # any shard is read as one.
    broken = _ci_context.RunContext("github", "7", "push", "", leg="check-a-3.14-0of2")
    with pytest.raises(ValueError, match="'0of2' is not a shard"):
        _ = broken.shard
    base = {"GITHUB_ACTIONS": "true", "GITHUB_RUN_ID": "7", "GITHUB_EVENT_NAME": "push"}
    split = _ci_context.run_context(
        {**base, "WORKSHOP_LEG": "check-windows-latest-3.14-2of4"}
    )
    assert split is not None
    assert split.leg == "check-windows-latest-3.14-2of4"
    assert split.shard == Shard(2, 4)
    plain = _ci_context.run_context(
        {**base, "WORKSHOP_LEG": "check-ubuntu-latest-3.14"}
    )
    assert plain is not None and plain.shard == Shard(1, 1)
    unlabelled = _ci_context.run_context(base)
    assert unlabelled is not None and unlabelled.leg == ""
    assert unlabelled.shard == Shard(1, 1)


def test_ci_run_is_the_public_answer_and_none_at_a_desk() -> None:
    from livery.workshop import RunContext, ci_run

    assert ci_run({}) is None
    environ = {"GITLAB_CI": "true", "CI_PIPELINE_ID": "42"}
    found = ci_run(environ)
    assert isinstance(found, RunContext) and found == _ci_context.run_context(environ)


def test_the_run_context_head_falls_back_when_the_payload_is_missing_or_junk(
    tmp_path: Path,
) -> None:
    base = {"GITHUB_ACTIONS": "true", "GITHUB_RUN_ID": "7", "GITHUB_SHA": "a" * 40}
    missing = _ci_context.run_context(
        {**base, "GITHUB_EVENT_PATH": str(tmp_path / "none")}
    )
    assert missing is not None and missing.head_sha == "a" * 40
    junk = tmp_path / "event.json"
    junk.write_text("{not json")
    broken = _ci_context.run_context({**base, "GITHUB_EVENT_PATH": str(junk)})
    assert broken is not None and broken.head_sha == "a" * 40
    junk.write_text(json.dumps({"pull_request": {"head": {}}}))
    headless = _ci_context.run_context({**base, "GITHUB_EVENT_PATH": str(junk)})
    assert headless is not None and headless.head_sha == "a" * 40


def test_the_run_context_head_is_the_pull_requests_on_a_pull_request(
    tmp_path: Path,
) -> None:
    # The checkout is the merge commit; the run is filed under the
    # pull request's head, which only the payload names.
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"head": {"sha": "b" * 40}}}))
    env = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_RUN_ID": "7",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_SHA": "a" * 40,
        "GITHUB_EVENT_PATH": str(event),
    }
    run = _ci_context.run_context(env)
    assert run is not None and run.head_sha == "b" * 40
    event.write_text(json.dumps({"after": "a" * 40}))
    push = _ci_context.run_context({**env, "GITHUB_EVENT_NAME": "push"})
    assert push is not None and push.head_sha == "a" * 40
    gitlab = _ci_context.run_context({"GITLAB_CI": "true", "CI_COMMIT_SHA": "c" * 40})
    assert gitlab is not None and gitlab.head_sha == "c" * 40


def test_the_run_context_head_branch_is_the_pull_requests_and_empty_otherwise(
    tmp_path: Path,
) -> None:
    event = tmp_path / "event.json"
    base = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_RUN_ID": "7",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_EVENT_PATH": str(event),
    }
    # Fallbacks first: a payload without the head names no branch, the
    # runner's own variable stands in, and a push names none.
    event.write_text(json.dumps({"pull_request": {"head": {"sha": "b" * 40}}}))
    bare = _ci_context.run_context(base)
    assert bare is not None and bare.head_ref == ""
    named = _ci_context.run_context({**base, "GITHUB_HEAD_REF": "feat/x"})
    assert named is not None and named.head_ref == "feat/x"
    event.write_text(json.dumps({"after": "a" * 40}))
    push = _ci_context.run_context({**base, "GITHUB_EVENT_NAME": "push"})
    assert push is not None and push.head_ref == ""
    # The payload names the branch, and wins over the variable.
    event.write_text(
        json.dumps({"pull_request": {"head": {"sha": "b" * 40, "ref": "feat/y"}}})
    )
    run = _ci_context.run_context({**base, "GITHUB_HEAD_REF": "feat/x"})
    assert run is not None and run.head_ref == "feat/y"
    gitlab = _ci_context.run_context(
        {"GITLAB_CI": "true", "CI_MERGE_REQUEST_SOURCE_BRANCH_NAME": "feat/z"}
    )
    assert gitlab is not None and gitlab.head_ref == "feat/z"


def test_a_gitlab_merge_request_pipeline_answers_the_pull_request_payload() -> None:
    # The fallbacks first: a GitLab pipeline that no merge request
    # started has no payload, and a merge request pipeline builds the
    # pull request shape GitHub and Gitea write, from the variables.
    assert (
        _ci_context.event_payload({"GITLAB_CI": "true", "CI_PIPELINE_SOURCE": "push"})
        is None
    )
    payload = _ci_context.event_payload(
        {
            "GITLAB_CI": "true",
            "CI_MERGE_REQUEST_IID": "7",
            "CI_MERGE_REQUEST_TITLE": "chore: a member-only change",
            "CI_MERGE_REQUEST_SOURCE_BRANCH_NAME": "chore/scoped-leg",
            "CI_MERGE_REQUEST_TARGET_BRANCH_NAME": "main",
            "CI_COMMIT_SHA": "d" * 40,
        }
    )
    assert payload == {
        "pull_request": {
            "number": 7,
            "title": "chore: a member-only change",
            "head": {"ref": "chore/scoped-leg", "sha": "d" * 40},
            "base": {"ref": "main"},
        }
    }


def test_the_run_context_labels_are_empty_without_a_labelled_payload(
    tmp_path: Path,
) -> None:
    # The fallbacks first: no payload, a payload that does not parse, a
    # pull request payload without the key, and one whose labels are
    # not a list all read as no labels, and nothing is raised.
    base = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_RUN_ID": "7",
        "GITHUB_EVENT_NAME": "pull_request",
    }
    missing = _ci_context.run_context(
        {**base, "GITHUB_EVENT_PATH": str(tmp_path / "none")}
    )
    assert missing is not None and missing.labels == ()
    junk = tmp_path / "event.json"
    junk.write_text("{not json")
    broken = _ci_context.run_context({**base, "GITHUB_EVENT_PATH": str(junk)})
    assert broken is not None and broken.labels == ()
    junk.write_text(json.dumps({"pull_request": {"head": {"sha": "a" * 40}}}))
    keyless = _ci_context.run_context({**base, "GITHUB_EVENT_PATH": str(junk)})
    assert keyless is not None and keyless.labels == ()
    junk.write_text(json.dumps({"pull_request": {"labels": "ci:every-runner"}}))
    odd = _ci_context.run_context({**base, "GITHUB_EVENT_PATH": str(junk)})
    assert odd is not None and odd.labels == ()
    # A push has no pull request, on GitHub and on GitLab alike.
    push = _ci_context.run_context(
        {"GITHUB_ACTIONS": "true", "GITHUB_RUN_ID": "7", "GITHUB_EVENT_NAME": "push"}
    )
    assert push is not None and push.labels == ()
    gitlab_push = _ci_context.run_context(
        {"GITLAB_CI": "true", "CI_PIPELINE_ID": "42", "CI_PIPELINE_SOURCE": "push"}
    )
    assert gitlab_push is not None and gitlab_push.labels == ()
    assert _ci_context.run_context({}) is None


def test_the_run_context_labels_are_the_pull_requests_on_each_forge(
    tmp_path: Path,
) -> None:
    # GitHub and Gitea write each label as an object in the payload;
    # GitLab carries the names comma-joined in one variable.
    payload = tmp_path / "event.json"
    payload.write_text(
        json.dumps(
            {
                "pull_request": {
                    "head": {"sha": "b" * 40, "ref": "feat/x"},
                    "labels": [
                        {"name": "kind/feat", "color": "0000ee"},
                        {"name": "ci:every-runner"},
                        {"color": "no-name"},
                    ],
                }
            }
        )
    )
    for forge_env in ({}, {"GITEA_ACTIONS": "true"}):
        found = _ci_context.run_context(
            {
                "GITHUB_ACTIONS": "true",
                "GITHUB_RUN_ID": "7",
                "GITHUB_EVENT_NAME": "pull_request",
                "GITHUB_EVENT_PATH": str(payload),
                **forge_env,
            }
        )
        assert found is not None
        assert found.labels == ("kind/feat", "ci:every-runner")
    gitlab = _ci_context.run_context(
        {
            "GITLAB_CI": "true",
            "CI_PIPELINE_ID": "42",
            "CI_PIPELINE_SOURCE": "merge_request_event",
            "CI_MERGE_REQUEST_IID": "7",
            "CI_MERGE_REQUEST_LABELS": "kind/feat, ci:every-runner,,",
        }
    )
    assert gitlab is not None
    assert gitlab.event == "pull_request"
    assert gitlab.labels == ("kind/feat", "ci:every-runner")


def test_the_event_payload_is_none_when_missing_junk_or_not_an_object(
    tmp_path: Path,
) -> None:
    assert _ci_context.event_payload({}) is None
    assert (
        _ci_context.event_payload({"GITHUB_EVENT_PATH": str(tmp_path / "none")}) is None
    )
    junk = tmp_path / "event.json"
    junk.write_text("{not json")
    assert _ci_context.event_payload({"GITHUB_EVENT_PATH": str(junk)}) is None
    junk.write_text("[1, 2]")
    assert _ci_context.event_payload({"GITHUB_EVENT_PATH": str(junk)}) is None
    junk.write_text(json.dumps({"action": "opened"}))
    assert _ci_context.event_payload({"GITHUB_EVENT_PATH": str(junk)}) == {
        "action": "opened"
    }


def test_a_ci_run_may_write_a_ci_only_series(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, work = repos
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_RUN_ID", "7")
    assert (
        git.put(state_store(work), REF, {"a.json": "1"}, message="one", ci_only=True)
        == ""
    )


# --- the row extension: refusals first --------------------------------------------


def test_a_ci_only_series_refuses_a_local_put_by_its_rule(
    repos: tuple[Path, Path],
) -> None:
    _, work = repos
    guarded = git.Series("guarded")
    why = guarded.put(state_store(work), {"a": {}}, message="a")
    assert "only a CI run writes this series; local runs read" in why
    assert guarded.rows(state_store(work)) == git.Rows(())


def test_a_local_series_declared_ci_only_is_refused() -> None:
    with pytest.raises(ValueError, match="written by local runs"):
        git.Series("x", local=True)
    with pytest.raises(ValueError, match="written by local runs"):
        git.Keyed("x", ("k",), local=True)


def test_the_run_context_base_is_the_pull_requests_and_empty_otherwise(
    tmp_path: Path,
) -> None:
    event = tmp_path / "event.json"
    env = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_RUN_ID": "7",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_REF": "refs/pull/3/merge",
        "GITHUB_SHA": "a" * 40,
        "GITHUB_EVENT_PATH": str(event),
    }
    # Fallbacks first: no base in the payload, a push, a missing payload.
    event.write_text(json.dumps({"pull_request": {"head": {"sha": "b" * 40}}}))
    run = _ci_context.run_context(env)
    assert run is not None and run.base_ref == ""
    pushed = _ci_context.run_context({**env, "GITHUB_EVENT_NAME": "push"})
    assert pushed is not None and pushed.base_ref == ""
    missing = _ci_context.run_context(
        {**env, "GITHUB_EVENT_PATH": str(tmp_path / "none")}
    )
    assert missing is not None and missing.base_ref == ""
    event.write_text(
        json.dumps(
            {"pull_request": {"head": {"sha": "b" * 40}, "base": {"ref": "main"}}}
        )
    )
    run = _ci_context.run_context(env)
    assert run is not None and run.base_ref == "main"
    gitlab = _ci_context.run_context(
        {
            "GITLAB_CI": "true",
            "CI_PIPELINE_ID": "9",
            "CI_PIPELINE_SOURCE": "merge_request_event",
            "CI_COMMIT_REF_NAME": "feat/x",
            "CI_COMMIT_SHA": "c" * 40,
            "CI_MERGE_REQUEST_TARGET_BRANCH_NAME": "main",
        }
    )
    assert gitlab is not None and gitlab.base_ref == "main"


def test_a_gitlab_pipeline_source_reads_in_the_one_event_vocabulary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A source the map does not name stays its own word, and a
    # GitHub run is untouched: the map is GitLab's alone.
    from livery.workshop._ci_context import run_context

    def event(env: dict[str, str]) -> str:
        run = run_context(env)
        assert run is not None
        return run.event

    base = {"GITLAB_CI": "true", "CI_PIPELINE_ID": "7", "CI_COMMIT_SHA": "abc"}
    assert event({**base, "CI_PIPELINE_SOURCE": "push"}) == "push"
    assert event({**base, "CI_PIPELINE_SOURCE": "schedule"}) == "schedule"
    assert event({**base, "CI_PIPELINE_SOURCE": "pipeline"}) == "pipeline"
    assert (
        event({"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "merge_request_event"})
        == "merge_request_event"
    )
    # A merge request pipeline is a pull request run, with its head and
    # base as the runner names them; a created pipeline is a dispatch.
    merge = run_context(
        {
            **base,
            "CI_PIPELINE_SOURCE": "merge_request_event",
            "CI_MERGE_REQUEST_SOURCE_BRANCH_NAME": "feat/1-work",
            "CI_MERGE_REQUEST_TARGET_BRANCH_NAME": "main",
        }
    )
    assert merge is not None
    assert merge.event == "pull_request"
    assert merge.head_ref == "feat/1-work" and merge.base_ref == "main"
    for source in ("api", "web", "trigger"):
        assert event({**base, "CI_PIPELINE_SOURCE": source}) == "workflow_dispatch"


# --- the fetched mirror: what a machine's gate reads ----------------------------


def test_a_desk_reads_the_row_series_from_the_mirror_and_a_family_fails_naming_it(
    repos: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate's reads, offline: metrics and verified answer, a family never does."""
    from livery.workshop import _metrics, _verified
    from livery.workshop._series import mirrored_series
    from livery.workshop._sync import fetch_store_lines

    _, work = repos
    other = _clone(tmp_path, "other", tmp_path / "origin.git")
    run = _ci_context.RunContext("github", "7", "push", "refs/heads/main")
    monkeypatch.setattr(_ci_context, "run_context", lambda environ=None: run)
    assert (
        _verified.SERIES.put(state_store(other), {"t": {"scope": "full"}}, message="v")
        == ""
    )
    assert (
        _metrics.SERIES.put(state_store(other), {"7": {"wall": 1}}, message="m") == ""
    )
    family = _metrics.RUNS.series("7", "check-a")
    assert (
        family.put(state_store(other), {"row.json": {"job": "check"}}, message="h")
        == ""
    )
    monkeypatch.setattr(_ci_context, "run_context", lambda environ=None: None)
    assert fetch_store_lines(work) == ["  store: 2 ref(s) changed, 2 mirrored"]
    mirrored = git.list_refs(state_store(work), git.FETCHED_NAMESPACE)
    assert mirrored is not None and set(mirrored) == {
        git.FETCHED_NAMESPACE + "metrics",
        git.FETCHED_NAMESPACE + "verified",
    }
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    with git.fetched_snapshot(state_store(work)):
        # A family's series, held by origin, is outside the mirror.
        found = family.rows(state_store(work))
        assert found.failed and family.ref in found.reason
        assert ", ".join(mirrored_series(work)) in found.reason
        assert _metrics.RUNS.listed(state_store(work), "7") is None
        # The row series answer from the mirror, with no network.
        assert [row.name for row in _verified.SERIES.rows(state_store(work)).rows] == [
            "t"
        ]
        assert [row.name for row in _metrics.SERIES.rows(state_store(work)).rows] == [
            "7"
        ]


def test_the_sync_line_says_what_the_fetch_did(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    from livery.workshop._sync import fetch_store_lines

    _, work = repos
    assert fetch_store_lines(work) == ["  store: up to date"]
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    (line,) = fetch_store_lines(work)
    assert line.startswith("  store: origin's state store not fetched (")
    assert line.endswith("); the last snapshot stands")
