"""A package contributes a point: refusals first, then the rendering and the removal.

A ``[[ci.point]]`` table in a package's ``workshop.toml`` declares a
scheduled point with a dispatch entry, one job on the runners and
Pythons it names, running the task it names with the job token and
nothing more. It renders for every forge, and it goes with the package.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from livery.workshop import _points

_FAILURES = (BaseException,)

MOUNTED = ("layers", "doctor", "tools.refresh")


def _mounted(task: str) -> bool:
    return task in MOUNTED


def _workspace(tmp_path: Path, kind: str = "github", *members: tuple[str, str]) -> Path:
    """A workspace with *members*, each ``(directory, contract tail)``."""
    root = tmp_path / kind
    (root / "packages").mkdir(parents=True)
    (root / "workshop.toml").write_text(
        '[workspace]\nlayers = ["livery.workshop"]\n\n[forge]\n'
        f'kind = "{kind}"\nowner = "acme"\n\n[ci]\nrunners = ["ubuntu-latest"]\n'
        'python-versions = ["3.13", "3.14"]\n'
    )
    (root / "pyproject.toml").write_text(
        '[project]\nname = "scratch"\nrequires-python = ">=3.11"\n'
    )
    for directory, tail in members:
        member = root / "packages" / directory
        (member / "src").mkdir(parents=True)
        (member / "workshop.toml").write_text(
            f'type = "python"\nname = "acme-{directory}"\n{tail}'
        )
        (member / "pyproject.toml").write_text(
            f'[project]\nname = "acme-{directory}"\n'
        )
    return root


AUDIT = (
    "audit",
    '\n[[ci.point]]\nname = "host-audit"\ntask = "layers"\nevery = "2w"\n'
    'runners = ["ubuntu-latest", "windows-latest"]\npythons = ["3.12", "3.14"]\n',
)


@pytest.fixture(autouse=True)
def _tasks_mounted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_points, "_mounted", _mounted)


# --- refusals, each naming the package and the point ---------------------------------


def _refusal(tmp_path: Path, tail: str) -> str:
    root = _workspace(tmp_path, "github", ("audit", tail))
    with pytest.raises(_FAILURES) as caught:
        _points.contributed(root, mounted=_mounted)
    return str(caught.value)


def test_a_builtin_name_is_refused(tmp_path: Path) -> None:
    text = _refusal(tmp_path, '\n[[ci.point]]\nname = "gate"\ntask = "layers"\n')
    assert "packages/audit [[ci.point]] entry 1" in text
    assert "'gate' is a builtin point" in text and "adds no job to the gate" in text


def test_a_name_that_is_not_a_filename_is_refused(tmp_path: Path) -> None:
    text = _refusal(tmp_path, '\n[[ci.point]]\nname = "Host Audit"\ntask = "layers"\n')
    assert "'Host Audit' is not a point name" in text


def test_a_name_two_packages_claim_is_refused(tmp_path: Path) -> None:
    tail = '\n[[ci.point]]\nname = "host-audit"\ntask = "layers"\n'
    root = _workspace(tmp_path, "github", ("audit", tail), ("bench", tail))
    with pytest.raises(_FAILURES) as caught:
        _points.contributed(root, mounted=_mounted)
    text = str(caught.value)
    assert "packages/bench [[ci.point]] entry 1" in text
    assert "'host-audit' is already the point packages/audit declares" in text


def test_a_cadence_that_is_not_one_is_refused(tmp_path: Path) -> None:
    text = _refusal(
        tmp_path, '\n[[ci.point]]\nname = "host-audit"\ntask = "layers"\nevery = "3d"\n'
    )
    assert "every '3d' is not a cadence; the cadences are 1w, 2w" in text


def test_a_task_the_runner_does_not_mount_is_refused(tmp_path: Path) -> None:
    text = _refusal(
        tmp_path, '\n[[ci.point]]\nname = "host-audit"\ntask = "audit.hosts"\n'
    )
    assert "the runner mounts no task 'audit.hosts'" in text


def test_a_grant_a_secret_or_an_environment_is_refused(tmp_path: Path) -> None:
    for key in ("permissions", "secrets", "environment"):
        text = _refusal(
            tmp_path / key,
            f'\n[[ci.point]]\nname = "host-audit"\ntask = "layers"\n{key} = "x"\n',
        )
        assert f"declares {key!r}" in text and "a root decision" in text


def test_a_point_without_a_task_and_junk_lists_are_refused(tmp_path: Path) -> None:
    assert "names no task" in _refusal(tmp_path / "a", '\n[[ci.point]]\nname = "x"\n')
    assert "names no point" in _refusal(
        tmp_path / "b", '\n[[ci.point]]\ntask = "layers"\n'
    )
    assert "runners must be a non-empty list of strings" in _refusal(
        tmp_path / "c", '\n[[ci.point]]\nname = "x"\ntask = "layers"\nrunners = []\n'
    )
    assert "args must be strings" in _refusal(
        tmp_path / "d", '\n[[ci.point]]\nname = "x"\ntask = "layers"\nargs = [1]\n'
    )


# --- the point, declared -------------------------------------------------------


def test_a_contributed_point_joins_the_workspace_with_its_defaults(
    tmp_path: Path,
) -> None:
    root = _workspace(
        tmp_path,
        "github",
        ("audit", '\n[[ci.point]]\nname = "host-audit"\ntask = "layers"\n'),
    )
    everything = _points.points(root)
    assert [point.name for point in everything] == [*_points.POINTS, "host-audit"]
    point = everything[-1]
    assert point.workflow == "host-audit.yml"
    assert point.events == ("schedule", "workflow_dispatch")
    assert point.cron == _points.CLOCK
    (job,) = point.jobs
    # The defaults: the root contract's runners, the newest gate Python,
    # the job token and nothing more.
    assert job.matrix == "declared"
    assert job.runners == ("ubuntu-latest",) and job.pythons == ("3.14",)
    assert job.token == "job" and not job.writes and not job.environment
    assert _points.dispatchable(root) == ("gate", "nightly", "host-audit")
    assert _points.jobs_of(root, "host-audit") == ("host-audit",)
    (entry,) = _points.entries_for(root, "host-audit", "host-audit")
    assert entry == _points.Entry(
        "host-audit", "host-audit", "layers", (), source="packages/audit"
    )
    # A root [[ci.schedule]] may attach to the contributed point too.
    (root / "workshop.toml").write_text(
        (root / "workshop.toml").read_text()
        + '\n[[ci.schedule]]\npoint = "host-audit"\ntask = "doctor"\n'
    )
    assert [e.task for e in _points.entries_for(root, "host-audit", "host-audit")] == [
        "layers",
        "doctor",
    ]


def test_the_runner_runs_a_contributed_points_task_on_its_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from datetime import date

    import livery.footman as footman

    monkeypatch.setattr(footman, "prog", lambda: "hse")
    root = _workspace(tmp_path, "github", AUDIT)
    seen: list[list[str]] = []

    def green(argv: list[str], env: dict[str, str]) -> int:
        seen.append(argv)
        return 0

    monkeypatch.setattr(_points, "today", lambda: date(2026, 9, 16))
    _points.run_point(
        root,
        "host-audit",
        "host-audit",
        os_label="windows-latest",
        python="3.12",
        spawn=green,
    )
    assert (
        seen == []
        and "runs every 2w; next on 2026-09-28, skipped" in capsys.readouterr().out
    )
    monkeypatch.setattr(_points, "today", lambda: date(2026, 9, 28))
    _points.run_point(
        root,
        "host-audit",
        "host-audit",
        os_label="windows-latest",
        python="3.12",
        spawn=green,
    )
    assert seen == [["hse", "layers"]]
    assert "host-audit/host-audit: layers (packages/audit)" in capsys.readouterr().out


# --- the rendering, one declaration for every forge ----------------------------------


def _doc(text: str) -> dict[Any, Any]:
    loaded = yaml.safe_load(text)
    assert isinstance(loaded, dict)
    return loaded


@pytest.mark.parametrize("kind", ("github", "gitea"))
def test_the_point_renders_as_its_own_workflow_on_github_and_gitea(
    tmp_path: Path, kind: str
) -> None:
    from livery.workshop._ci_generate import generate

    files = generate(_workspace(tmp_path, kind, AUDIT))
    text = files[f".{kind}/workflows/host-audit.yml"]
    doc = _doc(text)
    triggers = doc.get("on", doc.get(True))
    assert isinstance(triggers, dict)
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert triggers["schedule"] == [{"cron": _points.CLOCK}]
    ((name, job),) = doc["jobs"].items()
    assert name == "host-audit"
    assert job["strategy"]["matrix"] == {
        "os": ["ubuntu-latest", "windows-latest"],
        "python": ["3.12", "3.14"],
    }
    runs = " ".join(" ".join(str(s.get("run", "")).split()) for s in job["steps"])
    assert (
        'ci.run --point=host-audit --job=host-audit --os="${{ matrix.os }}"'
        ' --python="${{ matrix.python }}"' in runs
    )
    # The job token and nothing more: no grant, no environment, no
    # secret beyond the run's own.
    assert "permissions" not in job and "environment" not in job
    assert set(re.findall(r"secrets\.([A-Za-z_]+)", yaml.safe_dump(job))) <= {
        "GITHUB_TOKEN"
    }
    # The builtin shells are untouched by the addition.
    assert {k for k in files if "workflows" in k} == {
        f".{kind}/workflows/ci.yml",
        f".{kind}/workflows/nightly.yml",
        f".{kind}/workflows/release.yml",
        f".{kind}/workflows/host-audit.yml",
    }


def test_the_point_renders_as_a_job_in_the_gitlab_document(tmp_path: Path) -> None:
    from livery.workshop._ci_generate import generate

    doc = _doc(generate(_workspace(tmp_path, "gitlab", AUDIT))[".gitlab-ci.yml"])
    job = doc["host-audit"]
    assert job["stage"] == "check"
    assert [rule["if"] for rule in job["rules"]] == [
        "$CI_COMMIT_TAG",
        '$CI_PIPELINE_SOURCE == "schedule" && $FORGE_WORKFLOW == "host-audit.yml"',
        '$FORGE_WORKFLOW == "host-audit.yml"',
    ]
    assert job["script"][-1] == (
        "fm ci.run --point=host-audit --job=host-audit"
        ' --os="ubuntu-latest" --python="3.12"'
    )
    assert "variables" not in job or "FORGE_ADMIN_TOKEN" not in job["variables"]


# --- the removal ---------------------------------------------------------------


def test_removing_the_package_retires_its_workflow(tmp_path: Path) -> None:
    import shutil

    from livery.workshop._ci_generate import retired_files
    from livery.workshop._templates import apply_generated

    root = _workspace(tmp_path, "gitea", AUDIT)
    changed = apply_generated(root)
    assert ".gitea/workflows/host-audit.yml" in changed
    assert (root / ".gitea/workflows/host-audit.yml").is_file()
    # A person's own workflow beside the generated ones is never touched.
    (root / ".gitea/workflows/mine.yml").write_text("name: mine\non: push\n")
    assert retired_files(root) == ()
    shutil.rmtree(root / "packages" / "audit")
    assert [p.relative_to(root).as_posix() for p in retired_files(root)] == [
        ".gitea/workflows/host-audit.yml"
    ]
    changed = apply_generated(root)
    assert ".gitea/workflows/host-audit.yml (retired)" in changed
    assert not (root / ".gitea/workflows/host-audit.yml").exists()
    assert (root / ".gitea/workflows/mine.yml").is_file()
    assert retired_files(root) == ()
