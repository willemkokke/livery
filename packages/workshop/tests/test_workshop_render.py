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
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml

import livery.footman as footman
from livery.workshop._ci_generate import (
    _actions_workflow,
    _facts,
    _gitlab_document,
    generate,
)
from livery.workshop._points import Input, Job, Point

# The site's jobs are the docs extension's, added as the mount adds them.
from workshop_docs_declared import docs_jobs  # noqa: F401
from workshop_installed import skip_unless_installed

# The wheels job is nanobind's, added as the mount adds it.
from workshop_nanobind_declared import nanobind_jobs  # noqa: F401

KINDS = ("github", "gitea")


def _root(tmp_path: Path, kind: str) -> Path:
    root = tmp_path / kind
    root.mkdir()
    (root / "workshop.toml").write_text(
        "[workspace]\nextensions = []\n\n[forge]\n"
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
    skip_unless_installed("docs")
    doc = _doc(generate(_root(tmp_path, kind))[f".{kind}/workflows/ci.yml"])
    triggers = _triggers(doc)
    assert set(triggers) == {"pull_request", "push", "workflow_dispatch"}
    assert triggers["push"] == {"branches": ["main"]}
    jobs = doc["jobs"]
    # The release dispatch and the site's publish are the gate job's
    # entries on a push; GitHub's pages seam alone adds the forge's
    # pages deploy after it, and Gitea's container seam publishes from
    # the gate job itself.
    pages = ["pages"] if kind == "github" else []
    assert list(jobs) == ["check", "docs", "gate", "govern", *pages]
    # The one call per job, its point and its job; the pages deploy
    # calls nothing.
    assert {name: _calls(job) for name, job in jobs.items()} == {
        "check": [
            'ci.run --point=gate --job=check --os="${{ matrix.os }}"'
            ' --python="${{ matrix.python }}"'
        ],
        "docs": ["ci.run --point=gate --job=docs"],
        "gate": ["ci.run --point=gate --job=gate"],
        "govern": ["ci.run --point=merge --job=govern"],
        **{name: [] for name in pages},
    }
    # The needs and the only conditions: event filters and the
    # verdict job's always().
    assert {name: job.get("needs", []) for name, job in jobs.items()} == {
        "check": [],
        "docs": [],
        "gate": ["check", "docs"],
        "govern": [],
        **{name: ["gate"] for name in pages},
    }
    assert {name: job.get("if", "") for name, job in jobs.items()} == {
        "check": "",
        "docs": "",
        "gate": "always()",
        "govern": "github.event_name == 'push'",
        **dict.fromkeys(pages, "github.event_name == 'push'"),
    }
    # The matrix: every runner by every gate Python, the docs and the
    # verdict on one runner.
    assert jobs["check"]["strategy"]["matrix"] == {
        "os": ["ubuntu-latest", "macos-latest"],
        "python": ["3.13", "3.14"],
    }
    assert jobs["check"]["runs-on"] == "${{ matrix.os }}"
    for name in ("docs", "gate", "govern", *pages):
        assert jobs[name]["runs-on"] == "ubuntu-latest", name
    # The secrets each job names, and no other.
    assert {name: _secrets(job) for name, job in jobs.items()} == {
        "check": set(),
        "docs": set(),
        "gate": {"GITHUB_TOKEN"},
        "govern": {"FORGE_ADMIN_TOKEN"},
        **{name: set() for name in pages},
    }
    # The checkouts a job's verbs need: the legs and the verdict diff
    # against a merge base, the docs job reads tags for the release
    # view, govern reads the commit's parent, the pages deploy checks
    # nothing out.
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
    assert depth["govern"].get("fetch-depth") == 2
    assert depth["docs"].get("fetch-tags") is True
    for name in pages:
        assert not [s for s in jobs[name]["steps"] if "checkout" in str(s)], name
    # A leg's trace leaves through the verb that ran the leg. The one
    # artifact the gate workflow uploads is the built site, from the
    # docs job on a push alone, and the gate job collects it on a push
    # alone, into the directory the site was built in.
    uploads = [
        (name, step)
        for name, job in jobs.items()
        for step in job["steps"]
        if "upload-artifact" in str(step.get("uses", ""))
    ]
    assert [(n, s["if"], s["with"]["name"], s["with"]["path"]) for n, s in uploads] == [
        ("docs", "github.event_name == 'push'", "site", "site\n")
    ]
    downloads = [
        (name, step)
        for name, job in jobs.items()
        for step in job["steps"]
        if "download-artifact" in str(step.get("uses", ""))
    ]
    assert [
        (n, s["if"], s["with"]["pattern"], s["with"]["path"]) for n, s in downloads
    ] == [("gate", "github.event_name == 'push'", "site*", "site")]
    # A red docs job on main leaves no site; the verdict still runs.
    assert downloads[0][1]["continue-on-error"] is True


def test_the_github_gate_grants_the_store_writes_and_the_pages_deploy(
    tmp_path: Path,
) -> None:
    skip_unless_installed("docs")
    # The grants are GitHub's words: organisation defaults are
    # read-only, so a job that pushes says so. Starting the wave
    # through the API needs the actions grant on the gate job, which
    # dispatches it after its verdict; the workflow token answers 403
    # without it, and the wave never starts.
    jobs = _doc(generate(_root(tmp_path, "github"))[".github/workflows/ci.yml"])["jobs"]
    assert jobs["check"]["permissions"] == {"contents": "write"}
    assert jobs["gate"]["permissions"] == {"contents": "write", "actions": "write"}
    # The pages seam: the gate job uploads the site it collected and
    # refreshed, on a push alone, and the pages job deploys it with
    # the environment's grant and nothing else.
    upload = next(
        step
        for step in jobs["gate"]["steps"]
        if str(step.get("uses", "")).startswith("actions/upload-pages-artifact")
    )
    assert upload["if"] == "github.event_name == 'push'"
    assert upload["with"] == {"path": "site"}
    assert jobs["pages"]["permissions"] == {
        "contents": "read",
        "pages": "write",
        "id-token": "write",
    }
    assert jobs["pages"]["environment"]["name"] == "github-pages"
    assert jobs["pages"]["concurrency"] == {
        "group": "pages",
        "cancel-in-progress": False,
    }
    uses = [str(step.get("uses", "")) for step in jobs["pages"]["steps"]]
    assert len(uses) == 1 and uses[0].startswith("actions/deploy-pages")
    for name in ("docs", "govern", "pages"):
        assert "actions" not in str(jobs[name].get("permissions")), name
    for name in ("docs", "govern"):
        assert "permissions" not in jobs[name], name


def test_the_gitea_gate_carries_no_grant_and_runs_on_the_first_runner(
    tmp_path: Path,
) -> None:
    skip_unless_installed("docs")
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
    # The job pushes its profile trace to the state store at its end,
    # which GitHub's read-only default token needs the grant for.
    if kind == "github":
        assert jobs["nightly"]["permissions"] == {"contents": "write"}
    else:
        assert "permissions" not in jobs["nightly"]


# --- the release --------------------------------------------------------------


def _no_label(root: Path) -> Sequence[str]:
    """A runners job's function that names no runner label for *root*."""
    del root
    return ()


def _two_labels(root: Path) -> Sequence[str]:
    """A runners job's function naming one label twice and another once."""
    del root
    return ("linux-arm", "linux-arm", "macos")


def _shipping(labels: Callable[[Path], Sequence[str]] | None, *inputs: Input) -> Point:
    """A dispatched point whose runners job builds what its second job uploads."""
    return Point(
        "ship",
        "ship.yml",
        ("workflow_dispatch",),
        inputs=inputs,
        jobs=(
            Job(
                "build",
                matrix="runners",
                labels=labels,
                publishes="out",
                paths=("out/*",),
            ),
            Job("upload", needs=("build",), collects="out"),
        ),
    )


def _rendered(root: Path, kind: str, point: Point) -> dict[str, Any]:
    """The jobs *point* alone renders as on *kind*, by name."""
    everything = (point,)
    facts = _facts(root, everything)
    prog = footman.prog()
    if kind == "gitlab":
        doc = _doc(_gitlab_document(facts, prog, everything=everything))
        return {k: v for k, v in doc.items() if k not in ("workflow", "stages")}
    text = _actions_workflow(
        facts, prog, point.workflow, forge=kind, everything=everything
    )
    jobs: dict[str, Any] = _doc(text)["jobs"]
    return jobs


def _job_calls(job: dict[str, Any]) -> list[str]:
    """Every runner call a rendered job makes, ``fm`` stripped, on any forge."""
    if "script" not in job:
        return _calls(job)
    prog = f"{footman.prog()} "
    return [line.removeprefix(prog) for line in job["script"] if line.startswith(prog)]


@pytest.mark.parametrize("kind", (*KINDS, "gitlab"))
def test_a_runners_job_whose_function_names_no_label_renders_no_job(
    tmp_path: Path, kind: str
) -> None:
    root = _root(tmp_path, kind)
    # No function, and a function naming no label: neither renders the
    # job, and the need on it drops with it.
    for labels in (None, _no_label):
        jobs = _rendered(root, kind, _shipping(labels))
        assert list(jobs) == ["upload"]
        assert "needs" not in jobs["upload"]


@pytest.mark.parametrize("kind", (*KINDS, "gitlab"))
def test_a_job_of_a_point_without_a_workshop_input_pins_no_driver(
    tmp_path: Path, kind: str
) -> None:
    root = _root(tmp_path, kind)
    jobs = _rendered(root, kind, _shipping(_two_labels))
    assert {name: _job_calls(job) for name, job in jobs.items()} == {
        "build": ["ci.run --point=ship --job=build"],
        "upload": ["ci.run --point=ship --job=upload"],
    }
    # Every job of a point that takes the input pins the driver before
    # its one call.
    taken = Input("workshop", "a released livery-workshop version to drive the run")
    pinned = _rendered(root, kind, _shipping(_two_labels, taken))
    value = "$workshop" if kind == "gitlab" else "${{ inputs.workshop }}"
    assert {name: _job_calls(job) for name, job in pinned.items()} == {
        "build": [
            f'release.driver --workshop="{value}"',
            "ci.run --point=ship --job=build",
        ],
        "upload": [
            f'release.driver --workshop="{value}"',
            "ci.run --point=ship --job=upload",
        ],
    }


@pytest.mark.parametrize("kind", (*KINDS, "gitlab"))
def test_a_runners_job_fans_out_to_each_label_its_function_names_once(
    tmp_path: Path, kind: str
) -> None:
    jobs = _rendered(_root(tmp_path, kind), kind, _shipping(_two_labels))
    assert list(jobs) == ["build", "upload"]
    assert jobs["upload"]["needs"] == ["build"]
    if kind == "gitlab":
        # One executor: the job runs once, its artifact kept for the
        # job that needs it.
        assert "strategy" not in jobs["build"]
        assert jobs["build"]["artifacts"] == {"paths": ["out/*"]}
    else:
        assert jobs["build"]["strategy"]["matrix"] == {"os": ["linux-arm", "macos"]}
        assert jobs["build"]["runs-on"] == "${{ matrix.os }}"


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
    # wheels job, no templates job.
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
    # No package holds nanobind, so no job of the run builds wheels for
    # the wave and the publish job collects nothing.
    uses = [str(step.get("uses", "")) for step in publish["steps"]]
    assert not any("download-artifact" in u for u in uses)
    assert not any("upload-artifact" in u for u in uses)


@pytest.mark.usefixtures("nanobind_jobs")
def test_wheel_platforms_render_the_wheels_job_before_the_wave(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path, "github")
    member = root / "packages" / "native"
    (member / "src").mkdir(parents=True)
    (member / "workshop.toml").write_text(
        'name = "acme-native"\nextensions = ["nanobind"]\n\n'
        "[nanobind]\n"
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
    # A forge glob stops at a slash: a package in a group directory is
    # one level deeper.
    assert upload["path"] == "packages/*/dist/*\npackages/*/*/dist/*\n"
    # The publish job waits for every leg and collects each one's artifact
    # into the packages' directories before the wave.
    assert jobs["publish"]["needs"] == ["wheels"]
    download = next(
        step["with"]
        for step in jobs["publish"]["steps"]
        if "download-artifact" in str(step.get("uses", ""))
    )
    assert download == {
        "pattern": "wheels*",
        "path": "packages",
        "merge-multiple": True,
    }
    # The wave spells no --prebuilt: it decides from the collected dist.
    assert "--prebuilt" not in text


# --- GitLab, one document -----------------------------------------------------


def test_a_gitlab_site_published_elsewhere_runs_no_pages_job(tmp_path: Path) -> None:
    skip_unless_installed("docs")
    # The fallback first: a deploy through another seam keeps its own
    # name and leaves GitLab Pages alone.
    root = _root(tmp_path, "gitlab")
    contract = (root / "workshop.toml").read_text()
    (root / "workshop.toml").write_text(
        contract.replace("extensions = []", 'extensions = ["docs"]')
        + '\n[docs]\npublish = "ssh"\n'
    )
    jobs = _doc(generate(root)[".gitlab-ci.yml"])
    assert "pages" not in jobs
    # The gate job publishes through the seam from its own entries and
    # keeps no artifact; the docs job keeps the site it built for it.
    assert "artifacts" not in jobs["gate"]
    assert jobs["gate"]["script"][-1].endswith("ci.run --point=gate --job=gate")
    assert jobs["docs"]["artifacts"] == {"paths": ["site"], "expire_in": "1 day"}


def test_the_gitlab_document_names_its_pipelines_and_runs_every_declared_job(
    tmp_path: Path,
) -> None:
    skip_unless_installed("docs")
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
    # The declared jobs in order, GitLab Pages' own job after the gate
    # job whose site it publishes, and the wave as it is until its
    # jobs are entries.
    assert list(jobs) == [
        "check",
        "docs",
        "gate",
        "pages",
        "govern",
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
        "pages": [],
        "govern": [f"{prog} ci.run --point=merge --job=govern"],
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
        "nightly": "check",
        "publish": "release",
    }
    assert jobs["gate"]["needs"] == ["check", "docs"]
    assert jobs["pages"]["needs"] == ["gate"]

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
    # site the docs job kept and the gate job refreshed, moved to
    # public/ as the pages artifact, and origin rewritten with the
    # push token after the dispatched ref is checked out. The
    # commit-title regex that once started the wave is gone: the gate
    # job's dispatch entry starts it through the API.
    assert jobs["pages"]["script"] == ["mv site public"]
    assert jobs["pages"]["artifacts"] == {"paths": ["public"]}
    assert jobs["docs"]["artifacts"] == {"paths": ["site"], "expire_in": "1 day"}
    assert jobs["gate"]["artifacts"] == {"paths": ["site"], "expire_in": "1 day"}
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
    # pushes rewrites origin with the push token before its call, the
    # nightly for its profile trace among them, and no other job does.
    def rewrites(name: str) -> bool:
        return any("GITLAB_PUSH_TOKEN" in line for line in jobs[name]["script"])

    assert {name for name in jobs if rewrites(name)} == {
        "check",
        "gate",
        "nightly",
        "publish",
    }
    assert jobs["check"]["script"][0].startswith("git remote set-url origin")
    # The address is the server's own protocol, host and port: an
    # instance off 443 is unreachable at a bare https host.
    assert jobs["check"]["script"][0] == (
        'git remote set-url origin "${CI_SERVER_PROTOCOL}://oauth2:'
        "${GITLAB_PUSH_TOKEN}@${CI_SERVER_HOST}:${CI_SERVER_PORT}/"
        '${CI_PROJECT_PATH}.git"'
    )
    # The admin token reaches govern alone; the checkouts are as deep
    # as the verbs need.
    assert jobs["govern"]["variables"] == {
        "GIT_DEPTH": "2",
        "FORGE_ADMIN_TOKEN": "$FORGE_ADMIN_TOKEN",
    }
    assert jobs["check"]["variables"] == {"GIT_DEPTH": "0"}
    # The docs job reads the tags for the release view.
    assert jobs["docs"]["variables"] == {"GIT_DEPTH": "0"}


def _root_narrowing(tmp_path: Path, kind: str, declared: str) -> Path:
    root = _root(tmp_path, kind)
    contract = root / "workshop.toml"
    contract.write_text(contract.read_text() + f"pull-request-runners = {declared}\n")
    return root


@pytest.mark.parametrize("kind", KINDS)
def test_a_pull_request_runner_outside_runners_refuses_naming_both(
    tmp_path: Path, kind: str
) -> None:
    root = _root_narrowing(tmp_path, kind, '["windows-latest"]')
    with pytest.raises(
        footman.Failed,
        match=(
            r"pull-request-runners names windows-latest, which \[ci\] runners"
            r" does not list \(ubuntu-latest, macos-latest\)"
        ),
    ):
        generate(root)


def test_an_empty_pull_request_runners_list_refuses(tmp_path: Path) -> None:
    root = _root_narrowing(tmp_path, "github", "[]")
    with pytest.raises(footman.Failed, match=r"pull-request-runners is empty"):
        generate(root)


@pytest.mark.parametrize("kind", KINDS)
def test_pull_request_runners_narrow_the_check_legs_on_a_pull_request_alone(
    tmp_path: Path, kind: str
) -> None:
    skip_unless_installed("docs")
    root = _root_narrowing(tmp_path, kind, '["ubuntu-latest"]')
    jobs = _doc(generate(root)[f".{kind}/workflows/ci.yml"])["jobs"]
    # A legs job holds both matrices as literals, the event picks one,
    # and the check job reads it from the output: GitHub starts no run
    # for a workflow whose matrix axis is itself an expression.
    assert list(jobs)[:2] == ["legs", "check"]
    assert jobs["legs"]["runs-on"] == "ubuntu-latest"
    assert jobs["legs"]["outputs"] == {"matrix": "${{ steps.legs.outputs.matrix }}"}
    (step,) = jobs["legs"]["steps"]
    assert step["env"]["LEGS"] == (
        "${{ github.event_name == 'pull_request'"
        ' && \'{"os":["ubuntu-latest"],"python":["3.13","3.14"]}\''
        ' || \'{"os":["ubuntu-latest","macos-latest"],"python":["3.13","3.14"]}\' }}'
    )
    assert step["run"] == 'echo "matrix=$LEGS" >> "$GITHUB_OUTPUT"'
    assert jobs["check"]["needs"] == ["legs"]
    assert (
        jobs["check"]["strategy"]["matrix"]
        == "${{ fromJSON(needs.legs.outputs.matrix) }}"
    )
    assert jobs["check"]["runs-on"] == "${{ matrix.os }}"
    # The verdict still waits on the legs it judges, and nothing else moved.
    assert jobs["gate"]["needs"] == ["check", "docs"]


def test_pull_request_runners_equal_to_the_runners_render_the_plain_list(
    tmp_path: Path,
) -> None:
    root = _root_narrowing(tmp_path, "github", '["ubuntu-latest", "macos-latest"]')
    jobs = _doc(generate(root)[".github/workflows/ci.yml"])["jobs"]
    assert "legs" not in jobs
    assert jobs["check"]["strategy"]["matrix"]["os"] == [
        "ubuntu-latest",
        "macos-latest",
    ]
    assert jobs["check"].get("needs", []) == []
