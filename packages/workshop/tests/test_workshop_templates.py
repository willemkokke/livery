"""The render gate: byte-honest, drift-naming, namespace-clean.

The monorepo's own render is judged by the gate's render check on every
leg, never here: a second render of the whole workspace per leg bought
nothing but time.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.workshop._identity import project_facts
from livery.workshop._templates import (
    apply_project,
    project_drift,
)
from workshop_composed import IDENTITY, compose_into, seed_into

# The site's jobs are the docs extension's, added as the mount adds them.
from workshop_docs_declared import docs_jobs  # noqa: F401
from workshop_seeds import Seeds, _seed_home, seed_copier  # noqa: F401

ROOT = Path(__file__).resolve().parents[3]


def _template_instance(tmp_path: Path) -> Path:
    """A scratch workspace with this repository's identity."""
    (tmp_path / "workshop.toml").write_text(
        "[workspace]\n" + IDENTITY + "extensions = []\n"
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
        "extensions = []",
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


def test_a_members_seeds_are_never_judged(tmp_path: Path) -> None:
    # The seeds must NOT be judged: a package writes its own README
    # and changelog the moment it is born, and reporting those as
    # drift would ask a living package to revert to its stub.
    root = _template_instance(tmp_path)
    apply_project(root)
    package = root / "packages" / "thing"
    package.mkdir(parents=True)
    (package / "workshop.toml").write_text('kind = "python"\nname = "livery-thing"\n')
    (package / "pyproject.toml").write_text('[project]\nname = "livery-thing"\n')
    from livery.workshop._shipped_files import deliver, shipped_drift

    # Nothing composed yet: the base's file is named as missing.
    assert "packages/thing/cliff.toml: missing; `fm sync` writes it" in shipped_drift(
        root
    )
    assert "  wrote packages/thing/cliff.toml" in deliver(root)
    assert shipped_drift(root) == []
    assert deliver(root) == []  # idempotent
    # A README the package's authors wrote is not the base's to keep.
    (package / "README.md").write_text("# thing\n\nWritten by its authors.\n")
    assert shipped_drift(root) == []
    (package / "cliff.toml").write_text("# edited by hand\n")
    assert shipped_drift(root) == [
        "packages/thing/cliff.toml: differs from what livery.workshop:cliff.toml render"
    ]


def test_a_members_files_carry_its_own_path(
    tmp_path: Path,
) -> None:
    # The re-render happens in a temp directory, and package_dir comes
    # from the member's own directory: the managed files carry the
    # member's path, never the temp name.
    root = _template_instance(tmp_path)
    apply_project(root)
    package = root / "packages" / "thing"
    package.mkdir(parents=True)
    (package / "workshop.toml").write_text('kind = "python"\nname = "livery-thing"\n')
    (package / "pyproject.toml").write_text('[project]\nname = "livery-thing"\n')
    from livery.workshop._shipped_files import deliver, shipped_drift

    assert "  wrote packages/thing/cliff.toml" in deliver(root)
    body = (package / "cliff.toml").read_text()
    assert 'include_paths = ["packages/thing/**"]' in body
    assert shipped_drift(root) == []


@pytest.mark.parametrize(
    ("kind", "url", "api_url"),
    [
        ("gitlab", "http://gitlab:8929", "http://gitlab:8929/api/v4"),
        ("gitea", "http://gitea:3000", "http://gitea:3000"),
    ],
)
def test_the_cliff_remote_carries_the_api_prefix_gitlab_alone_needs(
    tmp_path: Path, kind: str, url: str, api_url: str
) -> None:
    # git-cliff completes a Gitea root with /api/v1 itself and a
    # GitLab address with nothing, so the render spells the prefix
    # for GitLab alone; a doubled prefix on Gitea answers 404.
    root = _template_instance(tmp_path)
    contract = root / "workshop.toml"
    contract.write_text(
        contract.read_text().replace(
            '[forge]\nkind = "github"\n', f'[forge]\nkind = "{kind}"\nurl = "{url}"\n'
        )
    )
    apply_project(root)
    package = root / "packages" / "thing"
    package.mkdir(parents=True)
    (package / "workshop.toml").write_text('kind = "python"\nname = "livery-thing"\n')
    (package / "pyproject.toml").write_text('[project]\nname = "livery-thing"\n')
    from livery.workshop._shipped_files import deliver

    assert "  wrote packages/thing/cliff.toml" in deliver(root)
    body = (package / "cliff.toml").read_text()
    assert f"[remote.{kind}]" in body
    assert f'api_url = "{api_url}"' in body


def test_apply_settles_and_drift_names_the_file(tmp_path: Path) -> None:
    root = _template_instance(tmp_path)
    changed = apply_project(root)
    assert "pyproject.toml" in changed
    # The contract is a birth-time seed: no render owns it.
    assert "workshop.toml" not in changed
    assert project_drift(root) == []
    assert apply_project(root) == []  # idempotent: a clean tree changes nothing
    from livery.workshop._shipped_files import shipped_drift

    (root / "tasks.py").write_text("# doctored\n")
    # The composed tasks.py, doctored, is the engine's drift.
    assert (
        "tasks.py: differs from what livery.workshop:tasks.py render"
        in shipped_drift(root)
    )


def _composed(tmp_path: Path, name: str) -> str:
    """*name* as the base composes it into a workspace listing no extension."""
    from livery.workshop._shipped_files import outputs

    workspace = tmp_path / "composed"
    workspace.mkdir(exist_ok=True)
    (workspace / "workshop.toml").write_text("[workspace]\nextensions = []\n")
    (output,) = [o for o in outputs(workspace) if o.path == name]
    return output.body.decode()


def _render_kind(tmp_path: Path, forge_kind: str, **extra: object) -> Path:
    destination = tmp_path / forge_kind
    answers = project_facts(ROOT)
    answers.update({"kind": "project", "forge_kind": forge_kind}, **extra)
    answers.update(extra)
    seed_into(destination, "project", answers)
    return compose_into(destination)


def test_each_forge_kind_generates_a_ci_definition_that_lints(
    tmp_path: Path,
) -> None:
    # Contract: generate over template. The workflow files are emitted
    # from the answers, never rendered, so the template must produce
    # none and the emitters must produce valid, gate-carrying YAML for
    # every kind.
    import yaml

    from livery.workshop._ci_generate import generate

    answers = project_facts(ROOT)

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
    assert "gate" in pipeline and "publish" in pipeline
    assert pipeline["workflow"]["rules"]

    gitea = _render_kind(tmp_path, "gitea", forge_url="https://forge.example.com")
    gitlab = _render_kind(tmp_path, "gitlab")
    for rendered in (github, gitea, gitlab):
        # The contract is a birth-time seed the verb fills, never a
        # rendered file.
        assert not (rendered / "workshop.toml").exists()


def test_a_package_renders_namespace_clean(tmp_path: Path) -> None:
    destination = tmp_path / "scratch"
    answers = project_facts(ROOT)
    seed_into(
        destination,
        "package-python",
        {
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
                or "hse sync" in content
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


def test_a_gate_leg_is_one_verb_and_uploads_no_trace(tmp_path: Path) -> None:
    # A leg's trace leaves through the verb that runs the leg, pushed
    # to a channel of its own, so the job is one line calling one verb
    # on both lanes and no step carries a trace out as an artifact.
    import yaml

    from livery.workshop._ci_generate import generate

    lanes = (
        ("github", ".github/workflows/ci.yml"),
        ("gitea", ".gitea/workflows/ci.yml"),
    )
    for kind, path in lanes:
        rendered = generate(_contract_root(tmp_path, kind))[path]
        assert "fm-profile.json" not in rendered, kind
        check = yaml.safe_load(rendered)
        steps = check["jobs"]["check"]["steps"]
        spelled = "fm ci.run --point=gate --job=check"
        gates = [step for step in steps if spelled in step.get("run", "")]
        assert len(gates) == 1, kind
        assert steps.index(gates[0]) == len(steps) - 1, kind  # and it is the last
        uploads = [
            step for step in steps if "upload-artifact" in str(step.get("uses", ""))
        ]
        assert uploads == [], kind


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
    assert list(jobs) == ["check", "docs", "gate", "govern", "dispatch", "deploy"]
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
    assert list(jobs) == ["check", "docs", "gate", "govern", "dispatch", "deploy"]
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
    # store and its trace goes to the traces' own channel: the leg
    # carries nothing out as an artifact.
    assert not [s for s in check["steps"] if "artifact" in s.get("uses", "")]
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
    for name in ("govern", "dispatch", "deploy"):
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


def test_every_rendered_project_mounts_the_profiler(tmp_path: Path) -> None:
    # The emitted legs run `fm --profile`; the flag exists where
    # footman.profile mounts, which footman declares as a built-in of
    # every project depending on it directly: the render names footman
    # in its dev group, and the trace a local run writes is ignored like
    # the coverage data beside it.
    from importlib.metadata import entry_points

    rendered = _render_kind(tmp_path, "github")
    assert '"livery-footman' in (rendered / "pyproject.toml").read_text()
    builtins = {entry.name for entry in entry_points(group="footman.builtin")}
    assert "footman.profile" in builtins
    assert "plugin(" not in (rendered / "tasks.py").read_text()
    # One trace per entry, so the rule is a pattern.
    ignored = _composed(tmp_path, ".gitignore")
    assert "fm-profile*.json" in ignored
    # The assembled traces land in a directory of their own, and the
    # ignore list follows the constant that names it rather than a
    # copy of it: a run that leaves an untracked directory behind
    # makes every working tree dirty.
    from livery.workshop._traces import INTO_DEFAULT

    root = INTO_DEFAULT.split("/", 1)[0]
    assert f"{root}/" in ignored.splitlines()


def test_every_coverage_data_file_is_ignored_and_its_configuration_tracked(
    tmp_path: Path,
) -> None:
    # A gate writes the data at the root, and a file it leaves untracked
    # makes the tree dirty, which a workflow verb refuses; coverage's own
    # configuration, `.coveragerc`, is a tracked file beside them.
    import subprocess

    from livery.workshop._coverage_lines import PART_PREFIX

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / ".gitignore").write_text(_composed(tmp_path, ".gitignore"))
    data = (".coverage", ".coverage.host.1.2", f"{PART_PREFIX}packages-geometry.json")
    for name in (*data, ".coveragerc"):
        (repo / name).write_text("x\n")

    def ignored(name: str) -> bool:
        done = subprocess.run(["git", "check-ignore", "-q", name], cwd=repo)
        return done.returncode == 0

    assert [name for name in data if not ignored(name)] == []
    assert not ignored(".coveragerc")


def test_the_rendered_notes_merge_by_union(tmp_path: Path) -> None:
    # Every change appends to a plan note's decision record, so two
    # changes in flight collide at the same tail; the rendered
    # attributes make git take both sides' lines there instead of
    # stopping the integrate on a conflict.
    attributes = _composed(tmp_path, ".gitattributes")
    assert "notes/*.md merge=union" in attributes
    assert "notes/**/*.md merge=union" in attributes


def test_the_rendered_attributes_check_out_lf_whatever_autocrlf_says(
    tmp_path: Path,
) -> None:
    """Git for Windows' autocrlf default would check out CRLF; the attributes win."""
    import subprocess

    repo = tmp_path / "checkout"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    git("init", "-q")
    git("config", "core.autocrlf", "true")
    git("config", "commit.gpgsign", "false")
    (repo / ".gitattributes").write_text(_composed(tmp_path, ".gitattributes"))
    (repo / "a.py").write_bytes(b"one\ntwo\n")
    (repo / "run.cmd").write_bytes(b"@echo off\n")
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    for name in ("a.py", "run.cmd"):
        (repo / name).unlink()
    git("checkout", "--", ".")
    assert (repo / "a.py").read_bytes() == b"one\ntwo\n"
    assert (repo / "run.cmd").read_bytes() == b"@echo off\r\n"


def test_the_rendered_prose_spells_the_brand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers = project_facts(ROOT)
    destination = tmp_path / "branded"
    seed_into(destination, "project", {**answers, "runner_prog": "hse"})
    # The composed tasks.py takes the brand from the running process.
    monkeypatch.setattr("livery.footman.prog", lambda: "hse")
    tasks = (compose_into(destination) / "tasks.py").read_text()
    assert "Run with ``hse <task>``" in tasks
    assert "``hse check``" in tasks


def test_the_rendered_answers_never_store_the_brand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers = project_facts(ROOT)
    destination = tmp_path / "branded"
    seed_into(destination, "project", {**answers, "runner_prog": "hse"})
    # The brand belongs to the process: a birth stores no answers at
    # all, so nothing pins the instance to the CLI that wrote it.
    assert not (destination / ".copier-answers.yml").exists()  # no answers file
    # The composed project file takes the brand from the process, as the
    # render does.
    monkeypatch.setattr("livery.footman.prog", lambda: "hse")
    compose_into(destination)
    assert "# Composed by `hse sync`" in (destination / "pyproject.toml").read_text()


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
    from livery.workshop._fragment_engine import (
        _IGNORE_HEADER,  # pyright: ignore[reportPrivateUsage]
    )

    assert "`hse sync`" in _IGNORE_HEADER.format(prog="hse")


def _build_instance(base: Path) -> None:
    """A born instance under the default brand, committed.

    A seed build: the seeds, the composed files and the git processes
    cost the same whoever asks.
    """
    import subprocess

    instance = base / "instance"
    answers = project_facts(ROOT)
    seed_into(instance, "project", {**answers, "runner_prog": "fm"})
    # The contract stands in for the birth verb's.
    (instance / "workshop.toml").write_text(
        "[workspace]\n" + IDENTITY + "extensions = []\n"
        '\n[forge]\nkind = "github"\nowner = "owner"\n'
        '\n[ci]\nrunners = ["ubuntu-latest"]\nrequired-context = "gate"\n'
    )
    compose_into(instance)
    subprocess.run(["git", "init", "-q"], cwd=instance, check=True)
    subprocess.run(["git", "config", "user.email", "t@l"], cwd=instance, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=instance, check=True)
    subprocess.run(["git", "add", "-A"], cwd=instance, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=instance, check=True)


def test_a_write_under_a_brand_rebrands_and_reemits(
    seeds: Seeds, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Rebranding an instance is exactly a write of its composed and
    # generated files under the branded CLI: they take the running brand.
    import livery.footman as footman
    from livery.workshop._templates import apply_project

    instance = seeds("born-instance", _build_instance) / "instance"
    assert "Run with ``fm <task>``" in (instance / "tasks.py").read_text()
    monkeypatch.setattr(footman, "prog", lambda: "hse")
    changed = apply_project(instance)
    assert changed  # the write reported work
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


def _wheel_instance(tmp_path: Path) -> Path:
    """A workspace that takes everything from the installed extensions."""
    root = tmp_path / "instance"
    root.mkdir()
    (root / "workshop.toml").write_text(
        "[workspace]\n"
        'name = "instance"\n'
        'namespace = "acme"\n'
        'authors = [{ name = "A", email = "a@example.com" }]\n'
        'copyright-year = "2026"\n'
        "extensions = []\n"
        '\n[forge]\nkind = "github"\nowner = "owner"\n'
        '\n[ci]\nrunners = ["ubuntu-latest"]\nrequired-context = "gate"\n'
    )
    # A born instance's project file is the one the engine composed.
    return compose_into(root)


def test_new_package_writes_the_installed_seeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The member's seeds come from the installed extensions.
    from livery.workshop._templates import new_package

    root = _wheel_instance(tmp_path)
    monkeypatch.setattr(
        "livery.workshop._templates.workspace_root", lambda start=None: root
    )
    synced: list[str] = []
    monkeypatch.setattr(
        "livery.workshop._uv.run_uv", lambda *args, root: synced.append(args[0])
    )
    monkeypatch.setattr(
        "livery.workshop._tool_tasks.sync_tools", lambda root, **kwargs: None
    )
    new_package("thing")
    assert (root / "packages" / "thing" / "cliff.toml").is_file()
    assert (root / "packages" / "thing" / "pyproject.toml").is_file()
    assert (root / "packages" / "thing" / "docs" / "index.md").is_file()
    assert synced == ["lock", "sync"]
    # The member reached the composed project file.
    assert "acme-thing" in (root / "pyproject.toml").read_text()


def test_a_tree_no_listed_extension_seeds_refuses_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._templates import new_package

    root = _wheel_instance(tmp_path)
    monkeypatch.setattr(
        "livery.workshop._templates.workspace_root", lambda start=None: root
    )
    with pytest.raises(BaseException, match="no listed extension seeds package-x"):
        new_package("thing", kind="package-x")
    assert not (root / "packages" / "thing").exists()


def test_the_release_baseline_reads_the_contract_or_stays_empty(tmp_path):
    # The fallbacks first: no contract, then a contract without the
    # table, both answer empty and the cliff render keeps v0.0.0.
    from livery.workshop._templates import _release_baseline

    package = tmp_path / "packages" / "thing"
    package.mkdir(parents=True)
    assert _release_baseline(package) == ""
    (package / "workshop.toml").write_text('kind = "python"\nname = "thing"\n')
    assert _release_baseline(package) == ""
    (package / "workshop.toml").write_text(
        'kind = "python"\nname = "thing"\n[release]\nbaseline = "0.6.1"\n'
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
    (root / "workshop.toml").write_text("[workspace]\nextensions = []\n")
    assert registry_injections(root) == empty
    (root / "workshop.toml").write_text(
        '[workspace]\nextensions = []\n[registries]\nconan = "x"\n'
    )
    assert registry_injections(root) == empty
    # The string form declares the read index alone.
    (root / "workshop.toml").write_text(
        "[workspace]\n"
        "extensions = []\n"
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
        "extensions = []\n"
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
    # No artifact carries coverage or a trace: the leg's measured
    # suites ride its per-run ref, its trace rides the traces' own
    # channel, and the gate job reads both from the store.
    assert not [step for step in check if "artifact" in step.get("uses", "")]
    gate = workflow["jobs"]["gate"]["steps"]
    assert not [step for step in gate if "artifact" in step.get("uses", "")]
    # The union is a gate entry, never a YAML line: the shell stays plumbing.
    assert "coverage combine" not in generate(root)[".gitea/workflows/ci.yml"]


def test_a_github_job_restores_the_tool_store_and_caches_nothing_of_uv(
    tmp_path: Path,
) -> None:
    # The tool store, restored before the entry materialises it, is the
    # one cache: uv's would hold the workspace's own editable builds and
    # nothing else, and setup-uv's own discards on any dependency
    # change. No shell in the YAML: the placement is the entry script's
    # and the sweep is ci.run's, so the run lines stay the entry and the verb.
    from livery.workshop._ci_generate import generate

    ci = generate(_contract_root(tmp_path, "github"))[".github/workflows/ci.yml"]
    assert "cache-suffix" not in ci
    assert "enable-cache: false" in ci
    assert "UV_CACHE_DIR" not in ci and "sweep" not in ci
    assert "uv-cache" not in ci and "hashFiles('uv.lock')" not in ci
    assert ci.count("uses: actions/cache@") == ci.count("- name: Enter the workspace")
    assert "path: ${{ runner.temp }}/footman/toolroom" in ci
    assert (
        "key: tools-${{ runner.os }}-${{ runner.arch }}-"
        "${{ hashFiles('toolroom.lock') }}" in ci
    )
    assert "restore-keys: tools-${{ runner.os }}-${{ runner.arch }}-" in ci
    store = ci.index("path: ${{ runner.temp }}/footman/toolroom")
    assert store < ci.index("Enter the workspace")


def test_the_other_lanes_emit_no_cache_action(tmp_path: Path) -> None:
    # Gitea's act runner and the GitLab lane have no cache action wired;
    # a step that would fail there is emitted for neither.
    from livery.workshop._ci_generate import generate

    gitea = generate(_contract_root(tmp_path, "gitea", url="https://forge.example.com"))
    gitlab = generate(_contract_root(tmp_path, "gitlab"))
    for files in (gitea, gitlab):
        for content in files.values():
            assert "actions/cache@" not in content


def test_removing_a_member_needs_only_sync(tmp_path: Path) -> None:
    # The composed project file follows discovery: a deleted member's
    # directory takes its every line with it on the next compose. And
    # the runner's handoff enters the environment without syncing, so
    # the stale file cannot fail the `fm sync` that rewrites it.
    import tomllib

    root = tmp_path / "ws"
    for name in ("alpha", "beta"):
        member = root / "packages" / name
        member.mkdir(parents=True)
        (member / "workshop.toml").write_text(
            f'kind = "python"\nname = "livery-{name}"\n'
        )
        (member / "pyproject.toml").write_text(f'[project]\nname = "livery-{name}"\n')
    compose_into(root)
    composed = (root / "pyproject.toml").read_text()
    assert '"packages/beta"' in composed and "livery-beta" in composed
    assert tomllib.loads(composed)["tool"]["footman"]["uv-handoff"] == "enter"
    import shutil

    shutil.rmtree(root / "packages" / "beta")
    compose_into(root)
    composed = (root / "pyproject.toml").read_text()
    assert "beta" not in composed
    assert '"packages/alpha"' in composed
