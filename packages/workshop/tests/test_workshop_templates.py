"""The render gate: byte-honest, drift-naming, namespace-clean.

The monorepo's own render is judged by the gate's render check on every
leg, never here: a second render of the whole workspace per leg bought
nothing but time.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from livery.workshop._contract import toml_string
from livery.workshop._templates import (
    apply_packages,
    apply_project,
    package_drift,
    project_drift,
    read_answers,
    render,
)

ROOT = Path(__file__).resolve().parents[3]
TEMPLATES = ROOT / "packages/workshop/src/livery/workshop/templates"


def _template_instance(tmp_path: Path) -> Path:
    """A scratch workspace carrying the real template source and answers."""
    shutil.copytree(TEMPLATES, tmp_path / "templates")
    shutil.copy(ROOT / ".copier-answers.yml", tmp_path / ".copier-answers.yml")
    (tmp_path / "workshop.toml").write_text(
        "[workspace]\n"
        'layers = ["livery.workshop"]\n'
        'templates = "templates"\n'
        "\n"
        "[forge]\n"
        'kind = "github"\n'
        'owner = "owner"\n'
        "\n"
        "[ci]\n"
        'runners = ["ubuntu-latest"]\n'
        'required-context = "gate"\n'
    )
    return tmp_path


def _contract_root(
    tmp_path: Path,
    kind: str,
    *,
    url: str = "",
    runners: list[str] | None = None,
    floor: str = "3.11",
) -> Path:
    """A scratch workspace whose contract carries the CI facts."""
    root = tmp_path / f"contract-{kind}"
    root.mkdir(exist_ok=True)
    lines = [
        "[workspace]",
        'layers = ["livery.workshop"]',
        "",
        "[forge]",
        f'kind = "{kind}"',
        'owner = "owner"',
    ]
    if url:
        lines.append(f'url = "{url}"')
    labels = ", ".join(f'"{label}"' for label in (runners or ["ubuntu-latest"]))
    lines += ["", "[ci]", f"runners = [{labels}]", 'required-context = "gate"']
    (root / "workshop.toml").write_text("\n".join(lines) + "\n")
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "scratch"\nrequires-python = ">={floor}"\n'
    )
    return root


def test_the_template_source_is_the_contracts_call(tmp_path: Path) -> None:
    from livery.workshop._templates import (
        DEFAULT_TEMPLATE_SOURCE,
        local_template_dir,
        template_source,
    )

    (tmp_path / "workshop.toml").write_text("[workspace]\n")
    assert template_source(tmp_path) == DEFAULT_TEMPLATE_SOURCE
    assert local_template_dir(tmp_path) is None  # remote: no local dir
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\ntemplates = "my-fork-checkout"\n'
    )
    assert local_template_dir(tmp_path) is None  # declared but absent
    (tmp_path / "my-fork-checkout").mkdir()
    assert local_template_dir(tmp_path) == tmp_path / "my-fork-checkout"
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\ntemplates = "git@example.com:me/fork.git"\n'
    )
    assert local_template_dir(tmp_path) is None


def test_package_drift_judges_only_the_managed_files(tmp_path: Path) -> None:
    # The seeds must NOT be judged: a package writes its own README
    # and changelog the moment it is born, and reporting those as
    # drift would ask a living package to revert to its stub.
    root = _template_instance(tmp_path)
    apply_project(root)
    package = root / "packages" / "thing"
    package.mkdir(parents=True)
    answers = read_answers(ROOT / "packages" / "workshop" / ".copier-answers.yml")
    answers["package_name"] = "livery-thing"
    (package / ".copier-answers.yml").write_text(
        "\n".join(f"{key}: {value!r}" for key, value in answers.items()) + "\n"
    )
    # Nothing rendered yet: the managed file is named as missing.
    assert package_drift(root) == [
        "packages/thing/cliff.toml: rendered, but missing from the repository"
    ]
    assert "packages/thing/cliff.toml" in apply_packages(root)
    assert package_drift(root) == []
    assert apply_packages(root) == []  # idempotent
    # A README the package's authors wrote is not the template's to keep.
    (package / "README.md").write_text("# thing\n\nWritten by its authors.\n")
    assert package_drift(root) == []
    (package / "cliff.toml").write_text("# edited by hand\n")
    assert package_drift(root) == ["packages/thing/cliff.toml: differs from its render"]


def test_a_receipt_without_package_dir_renders_the_right_paths(
    tmp_path: Path,
) -> None:
    # copier omits an answer equal to its default from the receipt,
    # and package_dir defaults to the destination basename, so a
    # receipt may not carry it. The re-render happens in a temp
    # directory: without the explicit override the managed files
    # would carry the temp name.
    root = _template_instance(tmp_path)
    apply_project(root)
    package = root / "packages" / "thing"
    package.mkdir(parents=True)
    answers = read_answers(ROOT / "packages" / "workshop" / ".copier-answers.yml")
    answers["package_name"] = "livery-thing"
    answers.pop("package_dir", None)
    (package / ".copier-answers.yml").write_text(
        "\n".join(f"{key}: {value!r}" for key, value in answers.items()) + "\n"
    )
    assert "packages/thing/cliff.toml" in apply_packages(root)
    body = (package / "cliff.toml").read_text()
    assert 'include_paths = ["packages/thing/**"]' in body
    assert package_drift(root) == []


def test_a_render_is_made_once_per_input_and_again_after_an_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    from livery import toolroom
    from livery.workshop._templates import render

    template = tmp_path / "template"
    template.mkdir()
    (template / "copier.yml").write_text("name:\n  type: str\n  default: x\n")
    (template / "{{ name }}.txt.jinja").write_text("hello {{ name }}\n")
    render(template, tmp_path / "one", {"name": "a"})
    assert (tmp_path / "one" / "a.txt").read_text() == "hello a\n"
    # The same inputs again: a copy of the first render, no copier run.
    real = toolroom.copier

    def _never(*args: object, **kwargs: object) -> object:
        raise AssertionError("copier ran for inputs already rendered")

    monkeypatch.setattr(toolroom, "copier", _never)
    render(template, tmp_path / "two", {"name": "a"})
    assert (tmp_path / "two" / "a.txt").read_text() == "hello a\n"
    # Other data renders; an edited template renders again, however
    # recent the last render was.
    with pytest.raises(AssertionError, match="copier ran"):
        render(template, tmp_path / "three", {"name": "b"})
    time.sleep(0.01)
    (template / "{{ name }}.txt.jinja").write_text("hi {{ name }}\n")
    with pytest.raises(AssertionError, match="copier ran"):
        render(template, tmp_path / "four", {"name": "a"})
    monkeypatch.setattr(toolroom, "copier", real)
    render(template, tmp_path / "four", {"name": "a"})
    assert (tmp_path / "four" / "a.txt").read_text() == "hi a\n"


def test_apply_settles_and_drift_names_the_file(tmp_path: Path) -> None:
    root = _template_instance(tmp_path)
    changed = apply_project(root)
    assert "pyproject.toml" in changed
    # The contract is a birth-time seed: no render owns it.
    assert "workshop.toml" not in changed
    assert project_drift(root) == []
    assert apply_project(root) == []  # idempotent: a clean tree changes nothing
    (root / "pyproject.toml").write_text("# doctored\n")
    drift = project_drift(root)
    assert "pyproject.toml: differs from its render" in drift


def test_template_check_refuses_a_stale_task_nav_block_in_an_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # An instance (no templates/ directory) passes the render check
    # vacuously; its committed task nav is still judged.
    from livery.footman import Failed
    from livery.workshop import _taskref, _templates

    root = tmp_path / "instance"
    root.mkdir()
    (root / "workshop.toml").write_text("[workspace]\n")
    monkeypatch.setattr(_templates, "_root", lambda: root)
    monkeypatch.setattr(_taskref, "stale_task_blocks", lambda _root: [])
    _templates.template_check()
    line = "packages/core/docs/nav.toml: the 'tasks' nav block lags the task tree"
    monkeypatch.setattr(_taskref, "stale_task_blocks", lambda _root: [line])
    with pytest.raises((SystemExit, Failed)) as caught:
        _templates.template_check()
    assert line in str(caught.value)
    assert "template.apply" not in str(caught.value)


def _render_kind(tmp_path: Path, forge_kind: str, **extra: object) -> Path:
    destination = tmp_path / forge_kind
    answers = read_answers(ROOT / ".copier-answers.yml")
    answers.update({"kind": "project", "forge_kind": forge_kind}, **extra)
    answers.update(extra)
    render(TEMPLATES, destination, answers)
    return destination


def test_each_forge_kind_generates_a_ci_definition_that_lints(
    tmp_path: Path,
) -> None:
    # Contract: generate over template. The workflow files are emitted
    # from the answers, never rendered, so the template must produce
    # none and the emitters must produce valid, gate-carrying YAML for
    # every kind.
    import yaml

    from livery.workshop._ci_generate import generate

    answers = read_answers(ROOT / ".copier-answers.yml")

    del answers
    github = _render_kind(tmp_path, "github")
    assert not (github / ".github").exists()  # nothing templated remains
    assert not (github / ".gitea").exists()
    assert not (github / ".gitlab-ci.yml").exists()
    files = generate(_contract_root(tmp_path, "github"))
    ci = yaml.safe_load(files[".github/workflows/ci.yml"])
    assert "gate" in ci["jobs"]
    release = yaml.safe_load(files[".github/workflows/release.yml"])
    assert "publish" in release["jobs"]
    # The trigger is the merge, never a tag: tags are receipts.
    trigger = release[True] if True in release else release["on"]
    # The wave is dispatch-only: the merge point starts it.
    assert list(trigger) == ["workflow_dispatch"]

    files = generate(_contract_root(tmp_path, "gitea", url="https://forge.example.com"))
    ci = yaml.safe_load(files[".gitea/workflows/ci.yml"])
    assert "gate" in ci["jobs"]
    release = yaml.safe_load(files[".gitea/workflows/release.yml"])
    assert "publish" in release["jobs"]

    files = generate(_contract_root(tmp_path, "gitlab"))
    pipeline = yaml.safe_load(files[".gitlab-ci.yml"])
    assert "gate" in pipeline and "release-publish" in pipeline
    assert pipeline["workflow"]["rules"]

    gitea = _render_kind(tmp_path, "gitea", forge_url="https://forge.example.com")
    gitlab = _render_kind(tmp_path, "gitlab")
    for rendered in (github, gitea, gitlab):
        # The contract is a birth-time seed the verb fills, never a
        # rendered file.
        assert not (rendered / "workshop.toml").exists()


def test_a_package_renders_namespace_clean(tmp_path: Path) -> None:
    destination = tmp_path / "scratch"
    answers = read_answers(ROOT / ".copier-answers.yml")
    render(
        TEMPLATES,
        destination,
        {
            "kind": "package-python",
            "package_name": "livery-scratch",
            "package_description": "livery-scratch: a livery workspace package.",
            "namespace_package": "livery",
            "author_name": answers["author_name"],
            "author_email": answers["author_email"],
            "copyright_year": answers["copyright_year"],
            "project_name": answers["project_name"],
        },
    )
    module = destination / "src" / "livery" / "scratch"
    assert (module / "__init__.py").is_file()
    assert (module / "py.typed").is_file()
    assert (destination / "tests" / "test_scratch_package.py").is_file()
    # The namespace stays PEP 420: no livery/__init__.py, ever.
    assert not (destination / "src" / "livery" / "__init__.py").exists()
    assert "livery-scratch" in (destination / "pyproject.toml").read_text()


def test_the_emitters_call_the_running_brand(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import re

    import livery.footman as footman
    from livery.workshop._ci_generate import generate

    monkeypatch.setattr(footman, "prog", lambda: "hse")
    for kind in ("github", "gitea", "gitlab"):
        for path, content in generate(_contract_root(tmp_path, kind)).items():
            spoken = (
                "hse check" in content
                or "hse workflow" in content
                or "hse template.apply" in content
            )
            assert spoken, path
            # No emitted word spells fm under a brand: the meter is
            # env-armed, so not even a module spelling remains.
            # The trace's file name is footman's own, fixed under every
            # brand, so it is the one `fm` a branded emission may carry.
            assert re.search(r"\bfm\b(?!-(profile|gate)\.json)", content) is None, path


def test_the_default_brand_emits_fm(tmp_path: Path) -> None:
    from livery.workshop._ci_generate import generate

    gate = generate(_contract_root(tmp_path, "github"))[".github/workflows/ci.yml"]
    assert "fm ci.run --point=gate --job=gate" in gate


def test_every_gate_leg_runs_profiled_and_uploads_its_trace(tmp_path: Path) -> None:
    # Each check leg runs the gate under --profile and uploads the
    # trace, one artifact per leg. The upload is observational, which
    # two rules pin: it runs on a red gate too, and its own exit never
    # decides the leg, because the gitea lane's action exits 1 after a
    # successful upload. The input it exits over rides along for the
    # day the runner carries it.
    import yaml

    from livery.workshop._ci_generate import generate

    lanes = (
        ("github", ".github/workflows/ci.yml", "actions/upload-artifact@"),
        ("gitea", ".gitea/workflows/ci.yml", "christopherhx/gitea-upload-artifact@"),
    )
    for kind, path, action in lanes:
        check = yaml.safe_load(generate(_contract_root(tmp_path, kind))[path])
        steps = check["jobs"]["check"]["steps"]
        # On both lanes the profiled gate runs inside ci.run.
        spelled = "fm ci.run --point=gate --job=check"
        gates = [step for step in steps if spelled in step.get("run", "")]
        assert len(gates) == 1, kind
        uploads = [
            step
            for step in steps
            if step.get("with", {}).get("path") == "fm-profile.json"
        ]
        assert len(uploads) == 1, kind
        (upload,) = uploads
        assert steps.index(upload) > steps.index(gates[0]), kind
        assert upload["uses"].startswith(action), kind
        assert upload["if"] == "always()", kind
        assert upload["continue-on-error"] is True, kind
        assert upload["with"]["if-no-files-found"] == "ignore", kind
        assert upload["with"]["name"] == "profile-${{ matrix.os }}-${{ matrix.python }}"


def test_the_gitea_shell_is_one_verb_per_job_and_only_event_filters(
    tmp_path: Path,
) -> None:
    # Phase 3's shape on the gitea lane: every job's work is reached
    # through `fm ci.run`, the docs deploy and governance are merge
    # jobs of ci.yml, and #286's census finds only event filters,
    # plus the verdict job's always().
    import yaml

    from livery.workshop._ci_generate import generate

    files = generate(_contract_root(tmp_path, "gitea"))
    assert set(files) >= {
        ".gitea/workflows/ci.yml",
        ".gitea/workflows/release.yml",
        ".gitea/workflows/nightly.yml",
    }
    # The nightly point's shell: the clock, a dispatch entry, one verb
    # per python, no condition, nothing else.
    nightly = yaml.safe_load(files[".gitea/workflows/nightly.yml"])
    assert sorted(nightly[True] if True in nightly else nightly["on"]) == [
        "schedule",
        "workflow_dispatch",
    ]
    (job,) = nightly["jobs"].values()
    verbs = [step["run"] for step in job["steps"] if "ci.run" in step.get("run", "")]
    assert len(verbs) == 1 and "--point=nightly --job=nightly" in verbs[0]
    assert "if" not in job and job["steps"][0]["with"]["fetch-depth"] == 0
    assert ".gitea/workflows/governance.yml" not in files
    assert ".gitea/workflows/docs.yml" not in files
    workflow = yaml.safe_load(files[".gitea/workflows/ci.yml"])
    jobs = workflow["jobs"]
    assert list(jobs) == ["check", "docs", "gate", "deploy", "govern", "dispatch"]
    for name, job in jobs.items():
        runs = [step["run"] for step in job["steps"] if "run" in step]
        # One verb per job: the entry script, then ci.run, nothing else.
        verbs = [run for run in runs if "ci.run" in run]
        assert len(verbs) == 1, name
        assert all("setup.sh" in run or "ci.run" in run for run in runs), (name, runs)
        for step in job["steps"]:
            if "if" in step:
                assert step["if"] == "always()", (name, step)
    check_step = next(s for s in jobs["check"]["steps"] if s.get("name") == "Check")
    assert "fm ci.run --point=gate --job=check" in check_step["run"]
    assert jobs["gate"]["if"] == "always()"
    assert jobs["gate"]["steps"][0]["with"]["fetch-depth"] == 0
    assert jobs["deploy"]["if"] == "github.event_name == 'push'"
    assert jobs["govern"]["if"] == "github.event_name == 'push'"
    assert "if" not in jobs["check"] and "if" not in jobs["docs"]
    assert "needs" not in jobs["check"] and "needs" not in jobs["docs"]
    assert jobs["govern"]["steps"][0]["with"]["fetch-depth"] == 2
    assert jobs["dispatch"]["if"] == "github.event_name == 'push'"
    assert jobs["dispatch"]["needs"] == ["gate"]
    # The wave is dispatch-only: no closed-pull-request trigger, no
    # decision expression, the ref an input the jobs check out.
    release = yaml.safe_load(files[".gitea/workflows/release.yml"])
    assert list(release[True] if True in release else release["on"]) == [
        "workflow_dispatch"
    ]
    assert "if" not in release["jobs"]["publish"]
    assert "${{ inputs.ref }}" in files[".gitea/workflows/release.yml"]
    assert "merge_commit_sha" not in files[".gitea/workflows/release.yml"]
    # The GitHub wave is dispatch-only too: the merge point dispatches
    # it, so a closed pull request is no trigger and no decision.
    gh_files = generate(_contract_root(tmp_path, "github"))
    gh_release = gh_files[".github/workflows/release.yml"]
    assert "merge_commit_sha" not in gh_release
    assert "pull_request" not in gh_release and "startsWith(" not in gh_release
    assert "${{ inputs.ref }}" in gh_release
    # The wave's receipt push is the lane's: receipt tags are protected
    # and the ambient token is bound (measured on the loop).
    for step in release["jobs"]["publish"]["steps"]:
        if step.get("uses", "").startswith("actions/checkout"):
            assert step["with"]["token"] == "${{ secrets.FORGE_TOKEN }}"


def test_the_github_shell_is_one_verb_per_job_for_the_gate_point(
    tmp_path: Path,
) -> None:
    # The GitHub lane's ci.yml is the same shell as the gitea lane's
    # for the gate point: every job's work is reached through
    # `fm ci.run`, the leg uploads its data with its scope marker, the
    # gate job collects every leg's data before its one verb, and the
    # census finds no condition beyond the verdict job's always().
    import yaml

    from livery.workshop._ci_generate import generate

    files = generate(_contract_root(tmp_path, "github"))
    workflow = yaml.safe_load(files[".github/workflows/ci.yml"])
    jobs = workflow["jobs"]
    assert list(jobs) == ["check", "docs", "gate", "deploy", "govern", "dispatch"]
    assert ".github/workflows/governance.yml" not in files
    assert ".github/workflows/docs.yml" not in files
    # The nightly point's shell: the clock, a dispatch, one verb per
    # python, no condition; the tests that declare the point ride it.
    nightly = yaml.safe_load(files[".github/workflows/nightly.yml"])
    assert sorted(nightly[True] if True in nightly else nightly["on"]) == [
        "schedule",
        "workflow_dispatch",
    ]
    (job,) = nightly["jobs"].values()
    verbs = [step["run"] for step in job["steps"] if "ci.run" in step.get("run", "")]
    assert len(verbs) == 1 and "--point=nightly --job=nightly" in verbs[0]
    assert "if" not in job and job["steps"][0]["with"]["fetch-depth"] == 0
    for name, job in jobs.items():
        runs = [step["run"] for step in job["steps"] if "run" in step]
        verbs = [run for run in runs if "ci.run" in run]
        assert len(verbs) == 1, name
        assert all("setup.sh" in run or "ci.run" in run for run in runs), (name, runs)
        assert (
            "if" not in job
            or (name == "gate" and job["if"] == "always()")
            or job["if"] == "github.event_name == 'push'"
        ), name
        for step in job["steps"]:
            if "if" in step:
                assert step["if"] == "always()", (name, step)
    check = jobs["check"]
    # The legs start at once: the title check is the gate job's
    # first entry, and the docs build runs beside the legs.
    assert "needs" not in check and "needs" not in jobs["docs"]
    check_step = next(s for s in check["steps"] if s.get("name") == "Check")
    assert "fm ci.run --point=gate --job=check" in check_step["run"]
    assert (
        '--os="${{ matrix.os }}" --python="${{ matrix.python }}"' in check_step["run"]
    )
    # The gate's own process is never metered: the test runner arms the
    # meter in pytest's environment, so the shell sets no variable.
    assert "env" not in check_step
    assert (
        "COVERAGE_PROCESS_START"
        not in generate(_contract_root(tmp_path, "github"))[".github/workflows/ci.yml"]
    )
    assert not [s for s in check["steps"] if "ci.metrics.leg" in s.get("run", "")]
    # The leg's measured suites ride its per-run ref on the state
    # store: no artifact carries coverage, on the leg or in the gate
    # job, and the profile upload is the leg's one artifact step.
    artifacts = [s for s in check["steps"] if "artifact" in s.get("uses", "")]
    assert [s["name"] for s in artifacts] == ["Upload the run profile"]
    gate = jobs["gate"]
    assert gate["needs"] == ["check", "docs"]
    # The stamp composes a narrowed run with its base tree's record,
    # which needs the merge base a shallow clone lacks.
    assert gate["steps"][0]["with"]["fetch-depth"] == 0
    names = [step.get("name", "") for step in gate["steps"]]
    assert not [s for s in gate["steps"] if "artifact" in s.get("uses", "")]
    verdict = gate["steps"][names.index("Verdict")]
    assert verdict["run"] == "fm ci.run --point=gate --job=gate"
    assert verdict["env"] == {"FORGE_TOKEN": "${{ secrets.GITHUB_TOKEN }}"}
    # Organisation defaults are read-only; the pushes to the store's
    # namespace need the grant declared on both writers.
    assert check["permissions"] == {"contents": "write"}
    assert gate["permissions"] == {"contents": "write"}
    assert jobs["docs"]["steps"][-1]["run"] == "fm ci.run --point=gate --job=docs"
    # The merge point's jobs, on the push alone, as the Gitea shell
    # has them: the admin secret in govern and nowhere else, the
    # pages grant and environment on deploy and nowhere else.
    for name in ("deploy", "govern", "dispatch"):
        assert jobs[name]["if"] == "github.event_name == 'push'", name
    assert jobs["deploy"]["needs"] == ["gate"]
    assert jobs["dispatch"]["needs"] == ["gate"]
    assert jobs["govern"]["steps"][0]["with"]["fetch-depth"] == 2
    text = files[".github/workflows/ci.yml"]
    govern = text.split("  govern:")[1].split("  dispatch:")[0]
    assert "FORGE_ADMIN_TOKEN" in govern
    assert "FORGE_ADMIN_TOKEN" not in text.replace(govern, "")
    deploy = jobs["deploy"]
    assert deploy["permissions"] == {
        "contents": "read",
        "pages": "write",
        "id-token": "write",
    }
    assert deploy["environment"]["name"] == "github-pages"
    assert deploy["concurrency"] == {"group": "pages", "cancel-in-progress": False}
    uses = [step.get("uses", "") for step in deploy["steps"]]
    assert any(u.startswith("actions/upload-pages-artifact") for u in uses)
    assert uses[-1].startswith("actions/deploy-pages")
    assert "pages" not in str(jobs["gate"].get("permissions"))


def test_apply_retires_the_workflows_the_emission_folded_away(tmp_path: Path) -> None:
    # A workspace born before the fold keeps a governance.yml the
    # emitter no longer owns; the apply deletes it and names it.
    from livery.workshop._templates import apply_generated

    root = _contract_root(tmp_path, "gitea")
    stale = root / ".gitea" / "workflows" / "governance.yml"
    stale.parent.mkdir(parents=True)
    stale.write_text("name: governance\n")
    changed = apply_generated(root)
    assert ".gitea/workflows/governance.yml (retired)" in changed
    assert not stale.exists()
    assert (root / ".gitea" / "workflows" / "ci.yml").is_file()
    # A second apply finds nothing to retire.
    assert not any("retired" in name for name in apply_generated(root))
    # The GitHub fold retires its governance and docs workflows, and
    # the release legs the release redesign left behind; a retired
    # file still present is drift the render check names.
    from livery.workshop._templates import project_drift

    root = _template_instance(tmp_path)
    apply_project(root)
    (root / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    for name in ("governance.yml", "docs.yml", "release-legs.yml"):
        (root / ".github" / "workflows" / name).write_text(f"name: {name}\n")
    apply_generated(root)
    for name in ("governance.yml", "docs.yml", "release-legs.yml"):
        (root / ".github" / "workflows" / name).write_text(f"name: {name}\n")
    drift = project_drift(root)
    for name in ("governance.yml", "docs.yml", "release-legs.yml"):
        assert f".github/workflows/{name}: retired, still present" in "\n".join(drift)
    changed = apply_generated(root)
    for name in ("governance.yml", "docs.yml", "release-legs.yml"):
        assert f".github/workflows/{name} (retired)" in changed
        assert not (root / ".github" / "workflows" / name).exists()
    assert not [line for line in project_drift(root) if "retired" in line]


def test_the_rendered_tasks_mount_the_profiler(tmp_path: Path) -> None:
    # The emitted legs run `fm --profile`; the flag exists only where
    # the tasks file mounts footman.profile, so the render and the
    # emitter move together, and the trace a local run writes is
    # ignored like the coverage data beside it.
    rendered = _render_kind(tmp_path, "github")
    tasks = (rendered / "tasks.py").read_text()
    layer = tasks.index('plugin("livery.workshop")')
    profiler = tasks.index('plugin("footman.profile")')
    mount = tasks.index("mount_layers()")
    assert layer < profiler < mount
    assert "fm-profile.json" in (rendered / ".gitignore").read_text()


def test_the_rendered_notes_merge_by_union(tmp_path: Path) -> None:
    # Every change appends to a plan note's decision record, so two
    # changes in flight collide at the same tail; the rendered
    # attributes make git take both sides' lines there instead of
    # stopping the integrate on a conflict.
    rendered = _render_kind(tmp_path, "github")
    attributes = (rendered / ".gitattributes").read_text()
    assert "notes/*.md merge=union" in attributes
    assert "notes/**/*.md merge=union" in attributes


def test_the_rendered_prose_spells_the_brand(tmp_path: Path) -> None:
    from livery.workshop._templates import render

    answers = read_answers(ROOT / ".copier-answers.yml")
    destination = tmp_path / "branded"
    render(
        TEMPLATES,
        destination,
        {**answers, "runner_prog": "hse"},
    )
    tasks = (destination / "tasks.py").read_text()
    assert "Run with ``hse <task>``" in tasks
    assert "``hse check``" in tasks


def test_the_rendered_answers_never_store_the_brand(tmp_path: Path) -> None:
    from livery.workshop._templates import render

    answers = read_answers(ROOT / ".copier-answers.yml")
    destination = tmp_path / "branded"
    render(TEMPLATES, destination, {**answers, "runner_prog": "hse"})
    stored = (destination / ".copier-answers.yml").read_text()
    # The brand belongs to the process; a stored copy would pin the
    # instance to the CLI that happened to render it.
    assert "runner_prog" not in stored
    # The meter comment rides the brand too.
    assert "# hse child a test spawns" in (destination / "pyproject.toml").read_text()


def test_the_shell_and_completion_lines_run_the_brand() -> None:
    from livery.workshop._env_tasks import _COMPLETION_HOOK, _COMPLETION_PWSH
    from livery.workshop._shell import _POSIX_ENTER, _PWSH_ENTER

    for template in (_POSIX_ENTER, _PWSH_ENTER):
        line = template.format(prog="hse", root="'/w s'")
        assert "hse -C=" in line and "fm -C" not in line
    posix = _COMPLETION_HOOK.format(prog="hse")
    assert "$(hse --setup-completion)" in posix
    pwsh = _COMPLETION_PWSH.format(prog="hse")
    assert "Get-Command hse" in pwsh
    assert "(hse --setup-completion=pwsh" in pwsh
    assert "{" in pwsh and "}" in pwsh  # the braces survived the format


def test_the_pipe_guard_recognises_the_brand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import livery.footman as footman
    from livery.workshop._hooks import _runs_runner

    monkeypatch.setattr(footman, "prog", lambda: "hse")
    pattern = _runs_runner()
    assert pattern.search("hse check") is not None
    assert pattern.search("uv run hse check") is not None
    # The stock spellings stay guarded under any brand.
    assert pattern.search("fm check") is not None
    assert pattern.search("footman check") is not None
    assert pattern.search("shse check") is None


def test_the_gitignore_header_speaks_the_brand() -> None:
    from livery.workshop._materialise import _GITIGNORE_HEADER

    assert "`hse sync`" in _GITIGNORE_HEADER.format(prog="hse")


def _instance_from_git_template(tmp_path: Path) -> tuple[Path, Path]:
    """A scratch git template repo and an instance rendered from it."""
    import shutil
    import subprocess

    from livery.workshop import __version__

    repo = tmp_path / "template-repo"
    shutil.copytree(TEMPLATES, repo)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@l"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)
    subprocess.run(["git", "tag", f"v{__version__}"], cwd=repo, check=True)
    instance = tmp_path / "instance"
    answers = read_answers(ROOT / ".copier-answers.yml")
    render(repo, instance, {**answers, "runner_prog": "fm"})
    # The contract is a birth-time seed the render never writes; the
    # fixture stands in for the birth verb.
    (instance / "workshop.toml").write_text(
        "[workspace]\n"
        'layers = ["livery.workshop"]\n'
        'templates = "templates"\n'
        '\n[forge]\nkind = "github"\nowner = "owner"\n'
        '\n[ci]\nrunners = ["ubuntu-latest"]\nrequired-context = "gate"\n'
    )
    # copier update works only in a git-tracked destination, which
    # every real instance is.
    subprocess.run(["git", "init", "-q"], cwd=instance, check=True)
    subprocess.run(["git", "config", "user.email", "t@l"], cwd=instance, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=instance, check=True)
    subprocess.run(["git", "add", "-A"], cwd=instance, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=instance, check=True)
    return repo, instance


def test_the_remote_update_arm_brands_and_reemits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The arm every instance takes: no local template directory, the
    # source is a git repository, and rebranding is exactly this run
    # under the branded CLI.
    import livery.footman as footman
    from livery.workshop import _templates
    from livery.workshop._update import refresh_rendered

    repo, instance = _instance_from_git_template(tmp_path)
    assert "Run with ``fm <task>``" in (instance / "tasks.py").read_text()
    contract = (instance / "workshop.toml").read_text()
    lines = [
        f"templates = {toml_string(str(repo))}"
        if line.startswith("templates = ")
        else line
        for line in contract.splitlines()
    ]
    assert any(line.startswith("templates = ") for line in lines)
    (instance / "workshop.toml").write_text("\n".join(lines) + "\n")
    import subprocess

    subprocess.run(["git", "add", "-A"], cwd=instance, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "point at the repo"], cwd=instance, check=True
    )
    monkeypatch.setattr(_templates, "local_template_dir", lambda _root: None)
    monkeypatch.setattr(footman, "prog", lambda: "hse")
    changed = refresh_rendered(instance)
    assert changed  # the update reported work
    tasks = (instance / "tasks.py").read_text()
    assert "Run with ``hse <task>``" in tasks and "``fm <task>``" not in tasks
    gate = (instance / ".github/workflows/ci.yml").read_text()
    assert (
        "hse ci.run --point=gate --job=gate" in gate
    )  # the workflows re-emitted branded


def test_no_runtime_string_spells_the_default_brand() -> None:
    # The teachings speak the running brand; a literal `fm verb` in a
    # non-docstring string is a regression this pin catches. The
    # docstrings document with the default spelling on purpose.
    import ast
    import io
    import tokenize

    source_dir = ROOT / "packages" / "workshop" / "src" / "livery" / "workshop"
    offenders: list[str] = []
    for path in sorted(source_dir.rglob("*.py")):
        source = path.read_text()
        if "`fm " not in source and "    fm " not in source:
            continue
        docstrings: set[tuple[int, int]] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                body = node.body
                if body and isinstance(body[0], ast.Expr):
                    value = body[0].value
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        docstrings.add((value.lineno, value.col_offset))
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if (
                tok.type == tokenize.STRING
                and "`fm " in tok.string
                and (tok.start[0], tok.start[1]) not in docstrings
            ):
                offenders.append(f"{path.name}:{tok.start[0]}")
            # FSTRING_MIDDLE arrived with 3.12's f-string tokens; on
            # an older tokenize the STRING arm above already covers
            # f-strings whole.
            middle = getattr(tokenize, "FSTRING_MIDDLE", None)
            if middle is not None and tok.type == middle and "`fm " in tok.string:
                offenders.append(f"{path.name}:{tok.start[0]}")
    assert offenders == [], offenders


def _template_repo(tmp_path: Path, *, tagged: bool = True) -> Path:
    """The template source as a git repository, the artifact's shape."""
    import shutil
    import subprocess
    from importlib.metadata import version

    repo = tmp_path / "artifact-repo"
    shutil.copytree(TEMPLATES, repo)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@l"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)
    if tagged:
        subprocess.run(
            ["git", "tag", f"v{version('livery-workshop')}"], cwd=repo, check=True
        )
    return repo


def _wheel_instance(tmp_path: Path, source: str) -> Path:
    """A workspace with no templates/ whose contract points at *source*."""
    root = tmp_path / "instance"
    root.mkdir()
    (root / "workshop.toml").write_text(
        "[workspace]\n"
        'layers = ["livery.workshop"]\n'
        f"templates = {toml_string(str(source))}\n"
        '\n[forge]\nkind = "github"\nowner = "owner"\n'
        '\n[ci]\nrunners = ["ubuntu-latest"]\nrequired-context = "gate"\n'
    )
    (root / "pyproject.toml").write_text(
        '[project]\nname = "instance"\nrequires-python = ">=3.11"\n'
    )
    (root / ".copier-answers.yml").write_text(
        "_src_path: whatever\n"
        "kind: project\n"
        "project_name: instance\n"
        "author_name: A\n"
        "author_email: a@example.com\n"
        "copyright_year: '2026'\n"
        "namespace_package: acme\n"
        "packages: []\n"
    )
    return root


def test_new_package_renders_from_the_artifact_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The known gap carried since the 0831 plan closes: a wheel
    # instance (no templates/ directory) renders a member from the
    # remote source at the resolved tag.
    from livery.workshop._templates import new_package

    repo = _template_repo(tmp_path)
    root = _wheel_instance(tmp_path, f"git+file://{repo}")
    monkeypatch.setattr(
        "livery.workshop._templates.workspace_root", lambda start=None: root
    )
    synced: list[str] = []
    monkeypatch.setattr(
        "livery.workshop._uv.run_uv", lambda *args, root: synced.append(args[0])
    )
    new_package("thing")
    assert (root / "packages" / "thing" / "cliff.toml").is_file()
    assert (root / "packages" / "thing" / "pyproject.toml").is_file()
    assert "acme-thing" in (root / ".copier-answers.yml").read_text()
    assert synced == ["lock", "sync"]
    # The project render resolved remotely too: the roster reached
    # the managed pyproject.
    assert "acme-thing" in (root / "pyproject.toml").read_text()


def test_an_unreachable_source_teaches_source_and_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._templates import new_package

    root = _wheel_instance(tmp_path, "https://127.0.0.1:1/acme/templates.git")
    monkeypatch.setattr(
        "livery.workshop._templates.workspace_root", lambda start=None: root
    )
    with pytest.raises(BaseException) as caught:
        new_package("thing")
    text = str(caught.value)
    assert "https://127.0.0.1:1/acme/templates.git" in text
    assert "v" in text  # the wanted ref is named


def test_a_missing_artifact_tag_names_the_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._templates import new_package

    repo = _template_repo(tmp_path, tagged=False)
    root = _wheel_instance(tmp_path, f"git+file://{repo}")
    monkeypatch.setattr(
        "livery.workshop._templates.workspace_root", lambda start=None: root
    )
    with pytest.raises(BaseException) as caught:
        new_package("thing")
    text = str(caught.value)
    assert "has no v" in text and "release publishes" in text


def test_a_source_without_the_kind_teaches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil
    import subprocess

    from livery.workshop._templates import new_package

    repo = _template_repo(tmp_path)
    shutil.rmtree(repo / "package-python")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "drop the kind"], cwd=repo, check=True)
    subprocess.run(["git", "tag", "-f", _ref()], cwd=repo, check=True)
    root = _wheel_instance(tmp_path, f"git+file://{repo}")
    monkeypatch.setattr(
        "livery.workshop._templates.workspace_root", lambda start=None: root
    )
    with pytest.raises(BaseException) as caught:
        new_package("thing")
    assert "package-python" in str(caught.value)


def _ref() -> str:
    from importlib.metadata import version

    return f"v{version('livery-workshop')}"


def test_repeated_render_at_one_tag_is_byte_identical(tmp_path: Path) -> None:
    import filecmp

    repo = _template_repo(tmp_path)
    data = {
        "kind": "project",
        "project_name": "acme-tools",
        "namespace_package": "acme",
        "packages": [],
        "runner_prog": "fm",
    }
    first = tmp_path / "one"
    second = tmp_path / "two"
    render(f"git+file://{repo}", first, dict(data), ref=_ref())
    render(f"git+file://{repo}", second, dict(data), ref=_ref())
    comparison = filecmp.dircmp(str(first), str(second))
    assert not comparison.left_only and not comparison.right_only
    # The answers file is a receipt: it records the destination's own
    # name, so it is provenance, not rendered content.
    names = [n for n in comparison.common_files if n != ".copier-answers.yml"]
    mismatch, errors = filecmp.cmpfiles(str(first), str(second), names, shallow=False)[
        1:
    ]
    assert not mismatch and not errors


def test_a_declared_but_absent_local_source_teaches(tmp_path: Path) -> None:
    from livery.workshop._templates import resolve_source

    root = _wheel_instance(tmp_path, "my-fork-checkout")
    with pytest.raises(BaseException) as caught:
        resolve_source(root)
    text = str(caught.value)
    assert "my-fork-checkout" in text and "no such" in text


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="git for Windows reads a file URL that carries userinfo as a path",
)
def test_a_credentialled_source_never_reaches_a_rendered_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Tokens are environment facts and should never sit in the
    # contract; when one does anyway, the rendered headers and the
    # refusals must not repeat it.
    from livery.workshop._templates import new_package, redacted_source

    assert redacted_source("http://user:secret@host/o/r.git") == "http://host/o/r.git"
    assert redacted_source("git+file:///tmp/repo") == "git+file:///tmp/repo"
    repo = _template_repo(tmp_path)
    root = _wheel_instance(
        tmp_path, f"git+file://user:sekrit@/{repo.as_posix().lstrip('/')}"
    )
    monkeypatch.setattr(
        "livery.workshop._templates.workspace_root", lambda start=None: root
    )
    monkeypatch.setattr("livery.workshop._uv.run_uv", lambda *args, root: None)
    new_package("thing")
    for path in sorted(root.rglob("*")):
        # The contract carries the caller's own value; everything the
        # machinery wrote must be clean.
        if path.is_file() and path.name != "workshop.toml":
            assert "sekrit" not in path.read_text(errors="ignore"), path


def test_the_rewrite_keeps_copiers_commit_receipt(tmp_path: Path) -> None:
    from livery.workshop._templates import _write_root_answers

    root = tmp_path
    (root / "workshop.toml").write_text(
        '[workspace]\nlayers = ["livery.workshop"]\ntemplates = "templates"\n'
    )
    (root / ".copier-answers.yml").write_text(
        "_commit: v0.0.2\n_src_path: whatever\nproject_name: x\n"
    )
    _write_root_answers(root, {"project_name": "x"})
    text = (root / ".copier-answers.yml").read_text()
    # An update cannot know the old template references without it.
    assert "_commit: v0.0.2" in text


def test_the_release_baseline_reads_the_contract_or_stays_empty(tmp_path):
    # The fallbacks first: no contract, then a contract without the
    # table, both answer empty and the cliff render keeps v0.0.0.
    from livery.workshop._templates import _release_baseline

    package = tmp_path / "packages" / "thing"
    package.mkdir(parents=True)
    assert _release_baseline(package) == ""
    (package / "workshop.toml").write_text('type = "python"\nname = "thing"\n')
    assert _release_baseline(package) == ""
    (package / "workshop.toml").write_text(
        'type = "python"\nname = "thing"\n[release]\nbaseline = "0.6.1"\n'
    )
    assert _release_baseline(package) == "0.6.1"


def test_the_registry_injections_read_the_contract_or_stay_empty(
    tmp_path: Path,
) -> None:
    # The fallbacks first: no [registries] table, then one without a
    # python entry, both inject nothing and uv keeps the ecosystem
    # default.
    from livery.workshop._templates import registry_injections

    empty = {"python_registry": "", "python_prerelease": ""}
    root = tmp_path
    (root / "workshop.toml").write_text('[workspace]\nlayers = ["livery.workshop"]\n')
    assert registry_injections(root) == empty
    (root / "workshop.toml").write_text(
        '[workspace]\nlayers = ["livery.workshop"]\n[registries]\nconan = "x"\n'
    )
    assert registry_injections(root) == empty
    # The string form declares the read index alone.
    (root / "workshop.toml").write_text(
        "[workspace]\n"
        'layers = ["livery.workshop"]\n'
        "[registries]\n"
        'python = "http://gitea:3000/api/packages/livery/pypi/simple"\n'
    )
    assert registry_injections(root) == {
        "python_registry": "http://gitea:3000/api/packages/livery/pypi/simple",
        "python_prerelease": "",
    }
    # The table form carries the prerelease policy beside the index.
    (root / "workshop.toml").write_text(
        "[workspace]\n"
        'layers = ["livery.workshop"]\n'
        "[registries.python]\n"
        'url = "http://gitea:3000/api/packages/livery/pypi/simple"\n'
        'prerelease = "allow"\n'
    )
    assert registry_injections(root) == {
        "python_registry": "http://gitea:3000/api/packages/livery/pypi/simple",
        "python_prerelease": "allow",
    }


def test_a_declared_registry_renders_into_the_root_pyproject(
    tmp_path: Path,
) -> None:
    # A roster change re-renders pyproject, and only a rendered index
    # survives that: the contract's declaration must reach the bytes,
    # or the next render locks the workspace back to the default
    # index.
    import tomllib

    root = _template_instance(tmp_path)
    contract = root / "workshop.toml"
    contract.write_text(
        contract.read_text()
        + "\n[registries.python]\n"
        + 'url = "http://gitea:3000/api/packages/livery/pypi/simple"\n'
        + 'prerelease = "allow"\n'
    )
    apply_project(root)
    parsed = tomllib.loads((root / "pyproject.toml").read_text())
    assert parsed["tool"]["uv"]["index"] == [
        {
            "name": "workshop",
            "url": "http://gitea:3000/api/packages/livery/pypi/simple",
        }
    ]
    assert parsed["tool"]["uv"]["prerelease"] == "allow"
    # The scalar stays inside [tool.uv] and the members survive: a
    # misplaced insert would hand both to the index table silently.
    assert parsed["tool"]["uv"]["package"] is False
    assert "members" in parsed["tool"]["uv"]["workspace"]


def test_the_apply_verb_migrates_underscore_keys_before_the_render(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from livery.workshop._templates import template_apply

    root = _template_instance(tmp_path)
    contract = root / "workshop.toml"
    contract.write_text(
        contract.read_text().replace("required-context", "required_context")
    )
    monkeypatch.chdir(root)
    template_apply()
    out = capsys.readouterr().out
    assert "  migrated: workshop.toml: required_context -> required-context" in out
    assert 'required-context = "gate"' in contract.read_text()


def test_the_check_jobs_fetch_history_for_the_merge_base(tmp_path: Path) -> None:
    import yaml

    from livery.workshop._ci_generate import generate

    for kind, path in (
        ("gitea", ".gitea/workflows/ci.yml"),
        ("github", ".github/workflows/ci.yml"),
    ):
        (tmp_path / kind).mkdir()
        root = _contract_root(tmp_path / kind, kind)
        workflow = yaml.safe_load(generate(root)[path])
        checkout = workflow["jobs"]["check"]["steps"][0]
        assert checkout["with"]["fetch-depth"] == 0, kind


def test_the_gitea_lane_meters_its_legs_and_unions_them_in_the_gate_job(
    tmp_path: Path,
) -> None:
    import yaml

    from livery.workshop._ci_generate import generate

    root = _contract_root(tmp_path, "gitea")
    workflow = yaml.safe_load(generate(root)[".gitea/workflows/ci.yml"])
    check = workflow["jobs"]["check"]["steps"]
    run = next(step for step in check if step.get("name") == "Check")
    assert "env" not in run  # the test runner arms the meter, never the shell
    # No artifact carries coverage: the leg's measured suites ride its
    # per-run ref, and the gate job reads them from the store.
    artifacts = [step for step in check if "artifact" in step.get("uses", "")]
    assert [step["name"] for step in artifacts] == ["Upload the run profile"]
    gate = workflow["jobs"]["gate"]["steps"]
    assert not [step for step in gate if "artifact" in step.get("uses", "")]
    # The union is a gate entry, never a YAML line: the shell stays plumbing.
    assert "coverage combine" not in generate(root)[".gitea/workflows/ci.yml"]
