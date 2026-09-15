"""The properties the emitters enforce, pinned per forge and point.

Not bytes: a rendering may differ in every comment and in the order of
its keys. What is pinned is what a run depends on: the files, the
events, the jobs in order, each job's needs and event filter, the one
``fm`` call per job, and the secrets each job names. The pins hold
against the emitters as they are, and against whatever renders the
same shells after them.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

import livery.footman as footman
from livery.workshop._ci_generate import generate

KINDS = ("github", "gitea")


def _root(tmp_path: Path, kind: str) -> Path:
    root = tmp_path / kind
    root.mkdir()
    (root / "workshop.toml").write_text(
        '[workspace]\nlayers = ["livery.workshop"]\n\n[forge]\n'
        f'kind = "{kind}"\nowner = "owner"\n\n[ci]\n'
        'runners = ["ubuntu-latest", "macos-latest"]\n'
        'python-versions = ["3.13", "3.14"]\naffected-legs = true\n'
    )
    (root / "pyproject.toml").write_text(
        '[project]\nname = "scratch"\nrequires-python = ">=3.11"\n'
    )
    return root


def _doc(text: str) -> dict[Any, Any]:
    # Keyed by Any: PyYAML reads a bare ``on`` key as the boolean True.
    loaded = yaml.safe_load(text)
    assert isinstance(loaded, dict)
    return loaded


def _triggers(doc: dict[Any, Any]) -> dict[str, Any]:
    found = doc.get("on", doc.get(True))
    assert isinstance(found, dict), found
    return found


def _secrets(job: dict[str, Any]) -> set[str]:
    return set(re.findall(r"secrets\.([A-Za-z_]+)", yaml.safe_dump(job)))


def _calls(job: dict[str, Any]) -> list[str]:
    """Every runner call in the job's run steps, ``fm`` stripped."""
    prog = footman.prog()
    calls: list[str] = []
    for step in job.get("steps", []):
        run = " ".join(str(step.get("run", "")).split())
        calls.extend(m.strip() for m in re.findall(rf"(?:^|\s){prog} ([^&|;]+)", run))
    return calls


# --- the gate and the merge point ---------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_the_gate_shell_runs_the_declared_jobs_on_the_declared_events(
    tmp_path: Path, kind: str
) -> None:
    doc = _doc(generate(_root(tmp_path, kind))[f".{kind}/workflows/ci.yml"])
    triggers = _triggers(doc)
    assert set(triggers) == {"pull_request", "push", "workflow_dispatch"}
    assert triggers["push"] == {"branches": ["main"]}
    jobs = doc["jobs"]
    assert list(jobs) == ["check", "docs", "gate", "deploy", "govern", "dispatch"]
    # The one call per job, its point and its job.
    assert {name: _calls(job) for name, job in jobs.items()} == {
        "check": [
            'ci.run --point=gate --job=check --os="${{ matrix.os }}"'
            ' --python="${{ matrix.python }}"'
        ],
        "docs": ["ci.run --point=gate --job=docs"],
        "gate": ["ci.run --point=gate --job=gate"],
        "deploy": ["ci.run --point=merge --job=deploy"],
        "govern": ["ci.run --point=merge --job=govern"],
        "dispatch": ["ci.run --point=merge --job=dispatch"],
    }
    # The needs and the only conditions: event filters and the
    # verdict job's always().
    assert {name: job.get("needs", []) for name, job in jobs.items()} == {
        "check": [],
        "docs": [],
        "gate": ["check", "docs"],
        "deploy": ["gate"],
        "govern": [],
        "dispatch": ["gate"],
    }
    assert {name: job.get("if", "") for name, job in jobs.items()} == {
        "check": "",
        "docs": "",
        "gate": "always()",
        "deploy": "github.event_name == 'push'",
        "govern": "github.event_name == 'push'",
        "dispatch": "github.event_name == 'push'",
    }
    # The matrix: every runner by every gate Python, the docs and the
    # verdict on one runner.
    assert jobs["check"]["strategy"]["matrix"] == {
        "os": ["ubuntu-latest", "macos-latest"],
        "python": ["3.13", "3.14"],
    }
    assert jobs["check"]["runs-on"] == "${{ matrix.os }}"
    for name in ("docs", "gate", "deploy", "govern", "dispatch"):
        assert jobs[name]["runs-on"] == "ubuntu-latest", name
    # The secrets each job names, and no other.
    assert {name: _secrets(job) for name, job in jobs.items()} == {
        "check": set(),
        "docs": set(),
        "gate": {"GITHUB_TOKEN"},
        "deploy": {"GITHUB_TOKEN"},
        "govern": {"FORGE_ADMIN_TOKEN"},
        "dispatch": {"GITHUB_TOKEN"},
    }
    # The checkouts a job's verbs need: the legs and the verdict diff
    # against a merge base, the deploy reads tags, govern reads the
    # commit's parent, dispatch reads the stamping commit.
    depth: dict[str, dict[str, Any]] = {
        name: next(
            (
                step.get("with", {})
                for step in job["steps"]
                if str(step.get("uses", "")).startswith("actions/checkout")
            ),
            {},
        )
        for name, job in jobs.items()
    }
    assert depth["check"].get("fetch-depth") == 0
    assert depth["gate"].get("fetch-depth") == 0
    assert depth["dispatch"].get("fetch-depth") == 0
    assert depth["govern"].get("fetch-depth") == 2
    assert depth["docs"] == {}
    # The deploy reads the receipt tags for the release view, on
    # every forge: one declaration renders both.
    assert depth["deploy"].get("fetch-tags") is True
    # The profile trace is kept on the check legs alone, whatever the
    # verdict.
    uploads = {
        name: [
            step
            for step in job["steps"]
            if "upload-artifact" in str(step.get("uses", ""))
        ]
        for name, job in jobs.items()
    }
    assert [name for name, steps in uploads.items() if steps] == ["check"]
    (upload,) = uploads["check"]
    assert upload["if"] == "always()" and upload["continue-on-error"] is True
    assert upload["with"]["path"] == "fm-profile.json"


def test_the_github_gate_grants_the_store_writes_and_the_pages_deploy(
    tmp_path: Path,
) -> None:
    # The grants are GitHub's words: organisation defaults are
    # read-only, so a job that pushes says so, and the pages seam is
    # an environment and two actions after the verb.
    jobs = _doc(generate(_root(tmp_path, "github"))[".github/workflows/ci.yml"])["jobs"]
    assert jobs["check"]["permissions"] == {"contents": "write"}
    assert jobs["gate"]["permissions"] == {"contents": "write"}
    assert jobs["deploy"]["permissions"] == {
        "contents": "read",
        "pages": "write",
        "id-token": "write",
    }
    assert jobs["deploy"]["environment"]["name"] == "github-pages"
    uses = [str(step.get("uses", "")) for step in jobs["deploy"]["steps"]]
    assert any(u.startswith("actions/upload-pages-artifact") for u in uses)
    assert any(u.startswith("actions/deploy-pages") for u in uses)
    for name in ("docs", "govern", "dispatch"):
        assert "permissions" not in jobs[name], name


def test_the_gitea_gate_carries_no_grant_and_runs_on_the_first_runner(
    tmp_path: Path,
) -> None:
    doc = _doc(generate(_root(tmp_path, "gitea"))[".gitea/workflows/ci.yml"])
    for name, job in doc["jobs"].items():
        assert "permissions" not in job, name
        assert "environment" not in job, name
    assert doc["jobs"]["docs"]["runs-on"] == "ubuntu-latest"


# --- the nightly --------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_the_nightly_shell_runs_the_clock_and_a_dispatch_on_every_python(
    tmp_path: Path, kind: str
) -> None:
    doc = _doc(generate(_root(tmp_path, kind))[f".{kind}/workflows/nightly.yml"])
    triggers = _triggers(doc)
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert triggers["schedule"] == [{"cron": "17 4 * * *"}]
    jobs = doc["jobs"]
    assert list(jobs) == ["nightly"]
    assert jobs["nightly"]["strategy"]["matrix"] == {"python": ["3.13", "3.14"]}
    assert jobs["nightly"]["runs-on"] == "ubuntu-latest"
    assert _calls(jobs["nightly"]) == [
        'ci.run --point=nightly --job=nightly --python="${{ matrix.python }}"'
    ]
    # The repository's token where there is one, else the job's.
    assert _secrets(jobs["nightly"]) == {"FORGE_TOKEN", "GITHUB_TOKEN"}
    assert "needs" not in jobs["nightly"] and "if" not in jobs["nightly"]


# --- the release --------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_the_release_shell_is_dispatched_with_a_ref_and_publishes_it(
    tmp_path: Path, kind: str
) -> None:
    doc = _doc(generate(_root(tmp_path, kind))[f".{kind}/workflows/release.yml"])
    triggers = _triggers(doc)
    assert set(triggers) == {"workflow_dispatch"}
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"ref", "workshop"}
    assert inputs["ref"]["required"] is True
    assert inputs["workshop"]["required"] is False
    assert inputs["workshop"]["default"] == ""
    # A pure workspace that is not a home: the publish job alone, no
    # wheels matrix, no templates job.
    jobs = doc["jobs"]
    assert list(jobs) == ["publish"]
    publish = jobs["publish"]
    assert "needs" not in publish and "if" not in publish
    # One call, through ci.run, at the ref the dispatch named; the
    # driver pin before it decides for itself.
    assert _calls(publish) == [
        'release.driver --workshop="${{ inputs.workshop }}"',
        "ci.run --point=release --job=publish",
    ]
    assert not [step for step in publish["steps"] if "if" in step]
    checkout = next(
        step["with"]
        for step in publish["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout")
    )
    assert checkout["ref"] == "${{ inputs.ref }}"
    assert checkout["fetch-depth"] == 0
    # The receipt push rides the checkout's credential.
    if kind == "github":
        assert checkout["token"] == "${{ secrets.FORGE_TOKEN || github.token }}"
        assert publish["environment"] == "pypi"
        assert publish["permissions"] == {"id-token": "write", "contents": "write"}
        assert _secrets(publish) == {"FORGE_TOKEN", "GITHUB_TOKEN", "PYPI_TOKEN"}
    else:
        assert checkout["token"] == "${{ secrets.FORGE_TOKEN }}"
        assert "permissions" not in publish and "environment" not in publish
        assert _secrets(publish) == {"FORGE_TOKEN", "UV_PUBLISH_TOKEN", "GITHUB_TOKEN"}
    # The wheels are collected before the wave, whether or not a leg
    # built any: the wave decides prebuilt from what it finds.
    uses = [str(step.get("uses", "")) for step in publish["steps"]]
    assert any("download-artifact" in u for u in uses)
    assert not any("upload-artifact" in u for u in uses)


def test_wheel_platforms_render_the_wheels_matrix_before_the_wave(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path, "github")
    member = root / "packages" / "native"
    (member / "src").mkdir(parents=True)
    (member / "workshop.toml").write_text(
        'type = "python-nanobind"\nname = "acme-native"\n\n[ci]\n'
        'wheel-platforms = ["ubuntu-latest", "macos-latest"]\n'
    )
    (member / "pyproject.toml").write_text('[project]\nname = "acme-native"\n')
    text = generate(root)[".github/workflows/release.yml"]
    jobs = _doc(text)["jobs"]
    assert list(jobs) == ["wheels", "publish"]
    wheels = jobs["wheels"]
    assert wheels["strategy"]["matrix"] == {"os": ["ubuntu-latest", "macos-latest"]}
    assert _calls(wheels) == [
        'release.driver --workshop="${{ inputs.workshop }}"',
        "ci.run --point=release --job=wheels",
    ]
    upload = next(
        step["with"]
        for step in wheels["steps"]
        if "upload-artifact" in str(step.get("uses", ""))
    )
    assert upload["name"] == "wheels-${{ matrix.os }}"
    assert upload["path"] == "packages/*/dist/*"
    assert jobs["publish"]["needs"] == ["wheels"]
    # The wave spells no --prebuilt: it decides from the collected dist.
    assert "--prebuilt" not in text


# --- GitLab, one document -----------------------------------------------------


def test_the_gitlab_document_names_its_pipelines_and_runs_every_declared_job(
    tmp_path: Path,
) -> None:
    prog = footman.prog()
    text = generate(_root(tmp_path, "gitlab"))[".gitlab-ci.yml"]
    doc = _doc(text)
    # Every pipeline is named after its workflow file: a dispatch or
    # the clock carries the variable, and a merge request or a push to
    # main sets it to the gate's file through the rule that admits it.
    assert doc["workflow"]["name"] == "$FORGE_WORKFLOW"
    assert doc["workflow"]["rules"] == [
        {"if": "$FORGE_WORKFLOW"},
        {
            "if": '$CI_PIPELINE_SOURCE == "merge_request_event"',
            "variables": {"FORGE_WORKFLOW": "ci.yml"},
        },
        {
            "if": '$CI_COMMIT_BRANCH == "main"',
            "variables": {"FORGE_WORKFLOW": "ci.yml"},
        },
    ]
    jobs = {k: v for k, v in doc.items() if k not in ("workflow", "stages")}
    # The declared jobs in order, the deploy as GitLab Pages' own job,
    # and the wave as it is until its jobs are entries.
    assert list(jobs) == [
        "check",
        "docs",
        "gate",
        "pages",
        "govern",
        "dispatch",
        "nightly",
        "publish",
    ]
    scripts = {
        name: [line for line in job["script"] if line.startswith(prog)]
        for name, job in jobs.items()
    }
    assert scripts == {
        "check": [
            f'{prog} ci.run --point=gate --job=check --os="ubuntu-latest"'
            ' --python="3.13"'
        ],
        "docs": [f"{prog} ci.run --point=gate --job=docs"],
        "gate": [f"{prog} ci.run --point=gate --job=gate"],
        "pages": [f"{prog} ci.run --point=merge --job=deploy"],
        "govern": [f"{prog} ci.run --point=merge --job=govern"],
        "dispatch": [f"{prog} ci.run --point=merge --job=dispatch"],
        "nightly": [f'{prog} ci.run --point=nightly --job=nightly --python="3.13"'],
        "publish": [
            f'{prog} release.driver --workshop="$workshop"',
            f"{prog} ci.run --point=release --job=publish",
        ],
    }
    assert {name: job["stage"] for name, job in jobs.items()} == {
        "check": "check",
        "docs": "check",
        "gate": "check",
        "pages": "release",
        "govern": "release",
        "dispatch": "release",
        "nightly": "check",
        "publish": "release",
    }
    assert jobs["gate"]["needs"] == ["check", "docs"]
    assert jobs["dispatch"]["needs"] == ["gate"]

    # The rules: a tag never; the gate's jobs on a merge request, a
    # dispatch and main's push (the merge point inherits them); the
    # merge point's own on the push alone; the nightly on the clock
    # and a dispatch, both routed by the pipeline's variable.
    def conditions(name: str) -> list[str]:
        return [rule["if"] for rule in jobs[name]["rules"]]

    tag_never = "$CI_COMMIT_TAG"
    gate_rules = [
        tag_never,
        '$CI_PIPELINE_SOURCE == "merge_request_event"',
        '$FORGE_WORKFLOW == "ci.yml"',
        '$CI_COMMIT_BRANCH == "main" && $CI_PIPELINE_SOURCE == "push"',
    ]
    assert conditions("check") == gate_rules
    assert conditions("gate") == gate_rules
    assert jobs["gate"]["rules"][1]["when"] == "always"
    assert conditions("govern") == [
        tag_never,
        '$CI_COMMIT_BRANCH == "main" && $CI_PIPELINE_SOURCE == "push"',
    ]
    assert conditions("nightly") == [
        tag_never,
        '$CI_PIPELINE_SOURCE == "schedule" && $FORGE_WORKFLOW == "nightly.yml"',
        '$FORGE_WORKFLOW == "nightly.yml"',
    ]
    assert conditions("pages") == [
        tag_never,
        '$CI_COMMIT_BRANCH == "main" && $CI_PIPELINE_SOURCE == "push"',
    ]
    # The pages seam and the wave's push, GitLab's own plumbing: the
    # site moved to public/ as the artifact, and origin rewritten with
    # the push token after the dispatched ref is checked out. The
    # commit-title regex that once started the wave is gone: the merge
    # point's dispatch job starts it through the API.
    assert jobs["pages"]["script"][-1] == "mv site public"
    assert jobs["pages"]["artifacts"] == {"paths": ["public"]}
    publish_script = jobs["publish"]["script"]
    assert publish_script[0] == 'git checkout --quiet "$ref"'
    assert publish_script[1] == "git fetch --tags"
    assert "GITLAB_PUSH_TOKEN" in publish_script[2]
    assert jobs["publish"]["rules"] == [
        {"if": "$CI_COMMIT_TAG", "when": "never"},
        {"if": '$FORGE_WORKFLOW == "release.yml"'},
    ]
    assert "CI_COMMIT_TITLE" not in text

    # The job token cannot push: every job that writes the store or
    # pushes rewrites origin with the push token before its call, and
    # no other job does.
    def rewrites(name: str) -> bool:
        return any("GITLAB_PUSH_TOKEN" in line for line in jobs[name]["script"])

    assert {name for name in jobs if rewrites(name)} == {"check", "gate", "publish"}
    assert jobs["check"]["script"][0].startswith("git remote set-url origin")
    # The admin token reaches govern alone; the checkouts are as deep
    # as the verbs need.
    assert jobs["govern"]["variables"] == {
        "GIT_DEPTH": "2",
        "FORGE_ADMIN_TOKEN": "$FORGE_ADMIN_TOKEN",
    }
    assert jobs["check"]["variables"] == {"GIT_DEPTH": "0"}
    assert "variables" not in jobs["docs"]
