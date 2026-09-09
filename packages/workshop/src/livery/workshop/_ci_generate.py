"""The CI workflow emitters: forge-dependent mechanics, generated.

Workflow files are mechanical forge knowledge, emitted here from the
workspace contract (``workshop.toml``) and the derived Python matrix,
written as managed generated artifacts; the templates carry none of
it, and the render gate compares the committed files against these
same pure functions, offline. Change an emitter, run
``fm template.apply``, and every kind's files move together; a
template update is never the vehicle.

Every job enters through the emitted ``setup.sh`` (the entry
contract: uv at the lock's pin, the venv synced against the lock,
the emission persisted) and then calls the runner bare: no ``uv run``
anywhere in a rendered workflow.

The release workflows are the merge-triggered train: publishing runs
where the release PR's squash lands, and the receipt tags are cut by
``fm workflow.release.publish`` after the index confirms each
member, so a tag is a receipt, never a trigger.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import livery.footman as footman
from livery.workshop._pythons import python_matrix

#: Pinned action shas, one place; version comments ride each use.
CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1"
SETUP_UV = "astral-sh/setup-uv@20cfd1bf945f4377ade1205e4dbc17946fc9a30d # v10.0.1"
UPLOAD = "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1"
DOWNLOAD = "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1"
#: The upload action the gitea lane runs. An act_runner that delivers
#: dashed action inputs empty breaks upstream's v4, which reads
#: `include-hidden-files` with a throwing getter and aborts before
#: uploading anything. This v4.0-era fork predates that input: it
#: uploads on such a runner too, exiting 1 over the empty
#: `if-no-files-found`, which the step's continue-on-error absorbs;
#: a runner that carries the inputs sees it exit 0 (measured on the
#: loop's runner).
GITEA_UPLOAD = "christopherhx/gitea-upload-artifact@v4"


def _facts(root: Path) -> dict[str, Any]:
    """What the emitters read: the contract's CI facts, matrix derived.

    Runners, the required context, and the forge kind come from
    ``workshop.toml``; the Python matrix from the root
    ``pyproject.toml``'s floor and the workshop's newest supported
    minor; the uv pin from the lock. Nothing here is an answer: the
    answers hold identity alone.
    """
    from livery.workshop._compose import layer_template_tree
    from livery.workshop._docs import docs_coverage_declared, docs_requirements
    from livery.workshop._entry import locked_uv_version
    from livery.workshop._envfile import parse_env_file
    from livery.workshop._layers import layer_entries
    from livery.workshop._templates import templates_artifact

    contract = tomllib.loads((root / "workshop.toml").read_text("utf-8"))
    ci = contract.get("ci") or {}
    publisher = ""
    for layer, dist in layer_entries(root):
        tree = layer_template_tree(root, layer)
        if tree is not None and tree.is_relative_to(root):
            publisher = dist
    return {
        "forge_kind": str((contract.get("forge") or {}).get("kind", "github")),
        "runners": list(ci.get("runners") or ["ubuntu-latest"]),
        "required_context": str(ci.get("required_context", "gate")),
        "python_versions": python_matrix(root),
        # The outer uv, the one tool that runs before the lock can
        # speak: pinned to the lock's own uv, so the bootstrap is not
        # the one unpinned link. Empty without a lock, and the
        # bootstrap then stays unpinned rather than inventing a
        # version.
        "uv_pin": locked_uv_version(root),
        # The union of the declared docs-generator requirements: the
        # system tools the docs jobs install before building.
        "docs_requirements": (
            list(docs_requirements(root)) if (root / "packages").is_dir() else []
        ),
        # Whether any package declares coverage reports: the docs jobs
        # then consume the check legs' coverage artifacts, so the
        # declaring packages' generators can render their trees.
        "docs_coverage": docs_coverage_declared(root),
        # The committed .repo.env's keys: the offline, deterministic
        # list of which secrets the rung step may carry into a job.
        "env_keys": sorted(parse_env_file(root / ".repo.env")),
        # The publish side: where this home ships its template
        # artifact, and which member layer's release triggers it.
        "templates_artifact": templates_artifact(root),
        "templates_publisher": publisher,
    }


def _rung_step(answers: dict[str, Any]) -> str:
    """The environment-rung step for GitHub- and Gitea-shaped jobs.

    Each key the committed ``.repo.env`` declares may arrive as a CI
    secret; the matching secrets land in a runner-local file, 0600,
    that the cascade reads as the shared slot
    (``WORKSHOP_SHARED_ENV_FILE``). Non-empty values only, so an
    absent secret cannot mask a committed value, and a fork PR with
    no secrets behaves exactly like a machine without a shared file.
    Never the whole secrets store: ``toJSON(secrets)`` would hand
    every job and every third-party action the lot. Empty when the
    workspace declares no keys.

    Rung before entry, always: the entry step persists the cascade's
    values into the runner's environment, and a real environment
    variable outranks the shared slot, so an emission taken before
    the rung would bake the committed values in over the secrets.
    """
    keys = [str(key) for key in answers.get("env_keys", [])]
    if not keys:
        return ""
    env_lines = "".join(
        f"          RUNG_{key}: ${{{{ secrets.{key} }}}}\n" for key in keys
    )
    writes = "".join(
        f'          if [ -n "$RUNG_{key}" ]; then'
        f' printf \'{key}=%s\\n\' "$RUNG_{key}" >> "$rung"; fi\n'
        for key in keys
    )
    return (
        "      - name: Environment rung\n"
        "        env:\n"
        f"{env_lines}"
        "        run: |\n"
        '          rung="${RUNNER_TEMP:-$(mktemp -d)}/repo-shared.env"\n'
        '          : > "$rung"\n'
        '          chmod 600 "$rung"\n'
        f"{writes}"
        '          echo "WORKSHOP_SHARED_ENV_FILE=$rung" >> "$GITHUB_ENV"\n'
    )


def _setup_uv_step(answers: dict[str, Any], *, cache_suffix: str = "") -> str:
    """The setup-uv step, uv pinned to the lock's own version."""
    pin = str(answers.get("uv_pin", ""))
    lines = [f"      - uses: {SETUP_UV}"]
    if pin or cache_suffix:
        lines.append("        with:")
    if pin:
        lines.append(f'          version: "{pin}"')
    if cache_suffix:
        lines += [
            "          # One cache identity per leg: a shared key makes",
            "          # every leg but the first fail its save with a",
            "          # reservation warning.",
            f"          cache-suffix: {cache_suffix}",
        ]
    return "\n".join(lines) + "\n"


def _docs_requirements_step(answers: dict[str, Any], *, sudo: bool = True) -> str:
    """The install step for the declared docs-generator requirements.

    Empty when no package declares any. ``sudo`` off for jobs that
    already run as root (a container image).
    """
    tools = [str(tool) for tool in answers.get("docs_requirements", [])]
    if not tools:
        return ""
    prefix = "sudo " if sudo else ""
    listed = " ".join(tools)
    return (
        "      - name: Docs system requirements\n"
        f"        run: {prefix}apt-get update -q && {prefix}apt-get install"
        f" -y -q {listed}\n"
    )


def _enter_step(*, matrix_python: bool = False) -> str:
    """The entry step: ``setup.sh github`` persists the emission.

    Every step after it calls the runner and the venv tools bare,
    from the persisted PATH. ``UV_PYTHON`` selects the matrix leg's
    interpreter for the entry sync where a matrix exists.
    """
    env = (
        "        env:\n          UV_PYTHON: ${{ matrix.python }}\n"
        if matrix_python
        else ""
    )
    return (
        "      - name: Enter the workspace\n"
        + env
        + "        run: bash setup.sh github\n"
    )


def _csv(values: list[Any], *, quoted: bool = False) -> str:
    return ", ".join(f'"{v}"' if quoted else str(v) for v in values)


def _github_gate(answers: dict[str, Any], prog: str) -> str:
    context = answers.get("required_context", "gate")
    runners = _csv(list(answers.get("runners", ["ubuntu-latest"])))
    pythons = _csv(list(answers.get("python_versions", ["3.11"])), quoted=True)
    setup_uv = _setup_uv_step(answers)
    setup_uv_leg = _setup_uv_step(
        answers, cache_suffix="${{ matrix.os }}-${{ matrix.python }}"
    )
    setup_uv_docs = _setup_uv_step(answers, cache_suffix="docs")
    requirements = _docs_requirements_step(answers)
    coverage_declared = bool(answers.get("docs_coverage"))
    # The explicit condition keeps docs alive past a skipped ancestor:
    # release-title is skipped on every non-release event, and GitHub
    # propagates a skip through default conditions transitively.
    docs_needs = (
        "    needs: [check]\n"
        "    if: ${{ !cancelled() && needs.check.result == 'success' }}\n"
        if coverage_declared
        else ""
    )
    coverage_step = (
        f"""      - name: Coverage artifacts for the site
        uses: {DOWNLOAD}
        with:
          pattern: coverage-*
          path: coverage-data
"""
        if coverage_declared
        else ""
    )
    enter = _enter_step()
    enter_leg = _enter_step(matrix_python=True)
    return f"""name: ci

on:
  pull_request:
  push:
    branches: [main]

jobs:
  check:
    # A release PR's title check answers in seconds; the matrix must
    # not burn six legs under a title the merge would refuse anyway.
    # Every other event skips release-title, and the skip passes.
    needs: [release-title]
    if: ${{{{ !cancelled() && (needs.release-title.result == 'success' || needs.release-title.result == 'skipped') }}}}
    # The leg pushes its timing row to the state store's namespace;
    # the ambient token needs the grant declared, since organisation
    # defaults are read-only.
    permissions:
      contents: write
    strategy:
      fail-fast: false
      matrix:
        os: [{runners}]
        python: [{pythons}]
    runs-on: ${{{{ matrix.os }}}}
    steps:
      - uses: {CHECKOUT}
{setup_uv_leg}{enter_leg}      - name: Gate, measured
        env:
          # Coverage's own subprocess contract: the .pth the
          # coverage-enable-subprocess dev dependency installs calls
          # coverage.process_startup() in every python this venv
          # starts, armed by this variable, so the whole {prog}
          # invocation meters from interpreter start with no module
          # spelling and no wrapper. The test step reads the same
          # variable and adds no second meter; the floors are judged
          # once, on the merged union, in the gate job below.
          COVERAGE_PROCESS_START: pyproject.toml
        run: |
          {prog} --profile=fm-profile.json check
          coverage combine
      - name: Leg coverage data
        uses: {UPLOAD}
        with:
          name: coverage-${{{{ matrix.os }}}}-${{{{ matrix.python }}}}
          path: .coverage
          include-hidden-files: true
          if-no-files-found: error
      # The run as a Chrome trace, one artifact per leg: every task,
      # step, lane wait, and pytest test as slices. Observational, so
      # it runs on a red gate too (the run worth reading) and its own
      # exit never decides the leg.
      - name: Upload the run profile
        if: always()
        continue-on-error: true
        uses: {UPLOAD}
        with:
          name: profile-${{{{ matrix.os }}}}-${{{{ matrix.python }}}}
          path: fm-profile.json
          if-no-files-found: ignore
      # The leg's timing row: the gate's tasks from the trace, put on
      # the run's per-leg ref for the gate job to collect. It fails
      # open loudly: a store fault prints its reason and never reddens
      # the leg.
      - name: Record the leg's timings
        if: always()
        run: >-
          {prog} ci.metrics.leg
          --job="check (${{{{ matrix.os }}}}, ${{{{ matrix.python }}}})"
          --label="check-${{{{ matrix.os }}}}-${{{{ matrix.python }}}}"

  # The strict site build: broken links and orphan pages go red
  # here, required through the gate context below, never inside the
  # local check.
  docs:
{docs_needs}    runs-on: ubuntu-latest
    steps:
      - uses: {CHECKOUT}
{setup_uv_docs}{requirements}{coverage_step}{enter}      - name: Build the site, strict
        run: {prog} docs.build

  # The one required context. Branch protection points here, so the
  # matrix can grow or shrink without touching repository settings.
  # It also owns the one coverage enforcement: every leg's data,
  # combined across platforms, judged against the committed floors.
  # And it collects the run's timing rows: every leg's trace half
  # joined with the forge's own job and step times, one file per run
  # on the metrics series. The verdict comes last, so a red run's
  # rows are collected before the job decides.
  {context}:
    if: always()
    needs: [check, docs]
    permissions:
      contents: write
    runs-on: ubuntu-latest
    steps:
      - uses: {CHECKOUT}
{setup_uv}{enter}      - name: Collect the run's timings
        env:
          FORGE_TOKEN: ${{{{ secrets.GITHUB_TOKEN }}}}
        run: {prog} ci.metrics.collect
      - name: Verdict
        run: |
          test "${{{{ needs.check.result }}}}" = "success"
          test "${{{{ needs.docs.result }}}}" = "success"
      - name: Combine every leg
        uses: {DOWNLOAD}
        with:
          pattern: coverage-*
          path: coverage-data
      - name: Enforce the floors on the union
        run: |
          coverage combine coverage-data/*/.coverage
          coverage report --sort=cover
          {prog} coverage.enforce
  release-title:
    if: startsWith(github.head_ref, 'workflow/release/')
    runs-on: ubuntu-latest
    steps:
      - uses: {CHECKOUT}
        with:
          # check-title compares against origin/main, which a shallow
          # checkout does not have.
          fetch-depth: 0
{setup_uv}{enter}      - run: {prog} workflow.release.check-title --title="$TITLE"
        env:
          TITLE: ${{{{ github.event.pull_request.title }}}}
"""


def _wants_wheel_matrix(answers: dict[str, Any]) -> bool:
    """Whether a member's kind publishes platform wheels.

    The per-OS wheels matrix is emitted only then: a pure workspace
    releases from one runner, and its workflow says so by shape.
    """
    from livery.workshop._kinds import kind_for, kind_names

    for entry in answers.get("packages", []) or []:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind", "") or "")
        if kind in kind_names() and kind_for(kind).wheel_identity == "platform":
            return True
    return False


def _github_release(answers: dict[str, Any], prog: str) -> str:
    rung = _rung_step(answers)
    setup_uv = _setup_uv_step(answers)
    enter = _enter_step()
    publisher = str(answers.get("templates_publisher", ""))
    wheels = _wants_wheel_matrix(answers)
    train_if = """>-
      github.event_name == 'workflow_dispatch' ||
      (github.event.pull_request.merged == true &&
       startsWith(github.event.pull_request.head.ref, 'workflow/release/'))"""
    wheels_job = (
        f"""  # Every platform's wheels, built before the wave: the matrix
  # feeds the publish job through artifacts, so one release ships
  # the complete set. linux arm waits on a docker-capable arm
  # runner, the container seam's known constraint.
  wheels:
    if: {train_if}
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
    runs-on: ${{{{ matrix.os }}}}
    steps:
      - uses: {CHECKOUT}
        with:
          ref: ${{{{ inputs.ref || github.event.pull_request.merge_commit_sha }}}}
          fetch-depth: 0
{setup_uv}{enter}      - name: Build this platform's wheels
        run: >-
          {prog} release.wheels
          --ref="${{{{ inputs.ref || github.event.pull_request.merge_commit_sha }}}}"
      - uses: {UPLOAD}
        with:
          name: wheels-${{{{ matrix.os }}}}
          path: packages/*/dist/*
          if-no-files-found: ignore
"""
        if wheels
        else ""
    )
    needs_wheels = "    needs: [wheels]\n" if wheels else ""
    collect_step = (
        f"""      - uses: {DOWNLOAD}
        with:
          pattern: wheels-*
          path: packages
          merge-multiple: true
"""
        if wheels
        else ""
    )
    prebuilt_flag = " --prebuilt" if wheels else ""
    workflow = f"""name: release

# The train: a workflow.release PR merges, this publishes its squash,
# and the receipt tags are cut only after the index confirms each
# member. A tag is a receipt, never a trigger. workflow_dispatch with
# --ref is the recovery entry when a publish died mid-wave.
on:
  pull_request:
    types: [closed]
    branches: [main]
  workflow_dispatch:
    inputs:
      ref:
        description: the release squash to publish (recovery)
        required: false
        default: ""

jobs:
{wheels_job}  publish:
    if: {train_if}
{needs_wheels}    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      id-token: write
      contents: write
    outputs:
      members: ${{{{ steps.wave.outputs.members }}}}
    steps:
      - uses: {CHECKOUT}
        with:
          ref: ${{{{ inputs.ref || github.event.pull_request.merge_commit_sha }}}}
          fetch-depth: 0
{setup_uv}{collect_step}{rung}{enter}      # The ambient job token suffices here: the wave reads the forge
      # and pushes receipt tags, and a tag is never a trigger, so the
      # suppressed-workflow-events limit cannot bite by construction.
      - name: Publish the wave
        id: wave
        env:
          FORGE_TOKEN: ${{{{ github.token }}}}
        run: >-
          {prog} workflow.release.publish{prebuilt_flag}
          --ref="${{{{ inputs.ref || github.event.pull_request.merge_commit_sha }}}}"
"""
    if not answers.get("templates_artifact") or not publisher:
        return workflow
    # Only a home publishes: the contract declares the artifact
    # repository and a member layer ships the tree.
    return (
        workflow
        + f"""
  # The home's release aftermath: the (composed) template artifact,
  # tagged in lockstep with the publishing layer's receipt.
  templates:
    needs: [publish]
    if: contains(needs.publish.outputs.members, '{publisher}')
    runs-on: ubuntu-latest
    steps:
      - uses: {CHECKOUT}
        with:
          ref: ${{{{ inputs.ref || github.event.pull_request.merge_commit_sha }}}}
{setup_uv}{enter}      - name: Deploy key
        run: |
          mkdir -p ~/.ssh
          printf '%s\\n' "${{{{ secrets.WORKSHOP_TEMPLATES_DEPLOY_KEY }}}}" > ~/.ssh/templates_deploy
          chmod 600 ~/.ssh/templates_deploy
      - name: Publish the template artifact
        env:
          GIT_SSH_COMMAND: ssh -i ~/.ssh/templates_deploy -o StrictHostKeyChecking=accept-new
          FORGE_TOKEN: ${{{{ secrets.FORGE_TOKEN }}}}
        run: {prog} release.templates
"""
    )


def _gitea_gate(answers: dict[str, Any], prog: str) -> str:
    """The gate and merge points' shell: a trigger, a checkout, an enter, one verb.

    Every job runs ``ci.run`` for its point and job; what the job does
    is the schedule (livery.workshop._points), and a push to main
    promotes the gate to the merge point inside the verb. The only
    conditions are event filters and the verdict job's ``always()``.
    """
    context = answers.get("required_context", "gate")
    runners = _csv(list(answers.get("runners", ["ubuntu-latest"])))
    pythons = _csv(list(answers.get("python_versions", ["3.11"])), quoted=True)
    first = next(iter(answers.get("runners", ["ubuntu-latest"])))
    rung = _rung_step(answers)
    requirements = _docs_requirements_step(answers)
    enter = _enter_step()
    enter_leg = _enter_step(matrix_python=True)
    return f"""name: ci

# The gate and merge points: a trigger, a checkout, an enter, and one
# `{prog} ci.run` per job. What a job does is data, the workshop's
# builtin schedule plus [[ci.schedule]] in workshop.toml, and a push
# to main is the merge point, promoted inside the verb. The only
# conditions here are event filters and the verdict job's always().
on:
  pull_request:
  push:
    branches: [main]

jobs:
  check:
    # The title check answers in seconds and is green off a release
    # branch; the matrix waits for it so a refused title burns no legs.
    needs: [release-title]
    strategy:
      fail-fast: false
      matrix:
        os: [{runners}]
        python: [{pythons}]
    runs-on: ${{{{ matrix.os }}}}
    steps:
      - uses: actions/checkout@v4
      # act_runner host mode: no setup actions. The entry script
      # installs the lock's pinned uv itself where the host has none.
{rung}{enter_leg}      - name: Check
        run: >-
          {prog} ci.run --point=gate --job=check
          --os="${{{{ matrix.os }}}}" --python="${{{{ matrix.python }}}}"
      # The run as a Chrome trace, one artifact per leg: every task,
      # step, lane wait, and pytest test as slices. Observational, so
      # it runs on a red gate too (the run worth reading) and its own
      # exit never decides the leg: an act_runner that delivers dashed
      # inputs empty makes the action exit 1 after a successful
      # upload, one that carries them lets it exit 0, and the gate's
      # verdict is the leg's either way.
      - name: Upload the run profile
        if: always()
        continue-on-error: true
        uses: {GITEA_UPLOAD}
        with:
          name: profile-${{{{ matrix.os }}}}-${{{{ matrix.python }}}}
          path: fm-profile.json
          if-no-files-found: ignore

  # The strict site build, required through the gate context below.
  docs:
    runs-on: {first}
    steps:
      - uses: actions/checkout@v4
{requirements}{enter}      - name: Docs
        run: {prog} ci.run --point=gate --job=docs

  # The one required context. Branch protection points here, so the
  # matrix can grow or shrink without touching repository settings.
  # It collects the run's timing rows, then judges the jobs it needs
  # by asking the forge; always(), so a red run is judged too.
  {context}:
    if: always()
    needs: [check, docs]
    runs-on: {first}
    steps:
      - uses: actions/checkout@v4
{rung}{enter}      - name: Verdict
        env:
          FORGE_TOKEN: ${{{{ secrets.GITHUB_TOKEN }}}}
        run: {prog} ci.run --point=gate --job=gate
  release-title:
    runs-on: {first}
    steps:
      - uses: actions/checkout@v4
        with:
          # check-title compares against origin/main, which a shallow
          # checkout does not have.
          fetch-depth: 0
{enter}      - name: Release title
        run: {prog} ci.run --point=gate --job=release-title

  # The merge point's own jobs, on the push alone: the site's deploy
  # through the contract's seam, and the repository settings
  # reconciled when the merge changed a contract or the owners file.
  deploy:
    if: github.event_name == 'push'
    needs: [{context}]
    runs-on: {first}
    steps:
      - uses: actions/checkout@v4
{requirements}{enter}      - name: Deploy
        env:
          FORGE_TOKEN: ${{{{ secrets.GITHUB_TOKEN }}}}
        run: {prog} ci.run --point=merge --job=deploy
  govern:
    if: github.event_name == 'push'
    runs-on: {first}
    steps:
      - uses: actions/checkout@v4
        with:
          # The commit's own file list decides whether anything is
          # governed; a depth of one would read a squash as a root.
          fetch-depth: 2
{enter}      - name: Govern
        env:
          FORGE_ADMIN_TOKEN: ${{{{ secrets.FORGE_ADMIN_TOKEN }}}}
        run: {prog} ci.run --point=merge --job=govern
  # The release wave is dispatched from here, after main's own
  # verdict: the verb reads the manifest at HEAD and the receipts on
  # the remote, and is green unless a merged release is unpublished.
  dispatch:
    if: github.event_name == 'push'
    needs: [{context}]
    runs-on: {first}
    steps:
      - uses: actions/checkout@v4
        with:
          # The commit that stamped the manifest can be far back.
          fetch-depth: 0
{enter}      - name: Dispatch
        env:
          FORGE_TOKEN: ${{{{ secrets.GITHUB_TOKEN }}}}
        run: {prog} ci.run --point=merge --job=dispatch
"""


def _gitea_release(answers: dict[str, Any], prog: str) -> str:
    first = next(iter(answers.get("runners", ["ubuntu-latest"])))
    rung = _rung_step(answers)
    enter = _enter_step()
    wheels = _wants_wheel_matrix(answers)
    runners = _csv(list(answers.get("runners", ["ubuntu-latest"])))
    wheels_job = (
        f"""  # Every declared runner's wheels, built before the wave and
  # collected as artifacts. linux arm waits on a docker-capable
  # runner, the container seam's known constraint.
  wheels:
    strategy:
      fail-fast: false
      matrix:
        runner: [{runners}]
    runs-on: ${{{{ matrix.runner }}}}
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{{{ inputs.ref }}}}
          fetch-depth: 0
{enter}      - name: Build this platform's wheels
        run: >-
          {prog} release.wheels
          --ref="${{{{ inputs.ref }}}}"
      - uses: actions/upload-artifact@v4
        with:
          name: wheels-${{{{ matrix.runner }}}}
          path: packages/*/dist/*
          if-no-files-found: ignore
"""
        if wheels
        else ""
    )
    needs_wheels = "    needs: [wheels]\n" if wheels else ""
    collect_step = (
        """      - uses: actions/download-artifact@v4
        with:
          pattern: wheels-*
          path: packages
          merge-multiple: true
"""
        if wheels
        else ""
    )
    prebuilt_flag = " --prebuilt" if wheels else ""
    workflow = f"""name: release

# The wave, dispatched by the merge point at the commit that stamped
# the release manifest, and by hand as the recovery gesture. Token
# publishing (Gitea has no trusted publishing): the wave publishes
# the ref and cuts receipt tags after the index confirms each member.
on:
  workflow_dispatch:
    inputs:
      ref:
        description: the release squash to publish
        required: true

jobs:
{wheels_job}  publish:
{needs_wheels}    runs-on: {first}
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{{{ inputs.ref }}}}
          fetch-depth: 0
{collect_step}{rung}{enter}      # PYTHON_PUBLISH_INDEX and PYTHON_REGISTRY_URL come from the
      # committed .repo.env through the env cascade; only the secrets
      # are mounted here. Gitea's automatic token serves the wave's
      # forge reads and receipt-tag pushes.
      - name: Publish the wave
        env:
          UV_PUBLISH_TOKEN: ${{{{ secrets.UV_PUBLISH_TOKEN }}}}
          FORGE_TOKEN: ${{{{ secrets.GITHUB_TOKEN }}}}
        run: >-
          {prog} workflow.release.publish{prebuilt_flag}
          --ref="${{{{ inputs.ref }}}}"
"""
    publisher = str(answers.get("templates_publisher", ""))
    if not answers.get("templates_artifact") or not publisher:
        return workflow
    # A cross-repository push needs a real token: the ambient one is
    # scoped to this repository alone.
    return (
        workflow
        + f"""
  # The home's release aftermath: the (composed) template artifact,
  # tagged in lockstep with the publishing layer's receipt.
  templates:
    needs: [publish]
    runs-on: {first}
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{{{ inputs.ref }}}}
{enter}      - name: Publish the template artifact
        env:
          FORGE_TOKEN: ${{{{ secrets.FORGE_TOKEN }}}}
        run: {prog} release.templates
"""
    )


def _gitlab_image(answers: dict[str, Any], python: str) -> str:
    """The pinned uv image for a GitLab job.

    With a lock the tag carries the pin
    (``<pin>-python<minor>-bookworm``), so the job's uv is the lock's
    uv; without one the unversioned tag is the honest fallback.
    """
    pin = str(answers.get("uv_pin", ""))
    prefix = f"{pin}-" if pin else ""
    return f"ghcr.io/astral-sh/uv:{prefix}python{python}-bookworm"


def _gitlab_pipeline(answers: dict[str, Any], prog: str) -> str:
    context = answers.get("required_context", "gate")
    python = next(iter(answers.get("python_versions", ["3.11"])))
    image = _gitlab_image(answers, python)
    tools = " ".join(str(t) for t in answers.get("docs_requirements", []))
    install = (
        f"    - apt-get update -q && apt-get install -y -q {tools}\n" if tools else ""
    )
    return f"""# The gate and the train, GitLab-shaped, generated by the workshop.
# One pipeline definition: workflow rules admit merge requests, main,
# and FORGE_WORKFLOW-routed manual pipelines. Every job sources the
# entry script in its own shell, then calls the runner bare.
workflow:
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    - if: $CI_COMMIT_BRANCH == "main"
    - if: $FORGE_WORKFLOW

stages: [check, release]

{context}:
  stage: check
  image: {image}
  rules:
    - if: $CI_COMMIT_TAG
      when: never
    - when: on_success
  script:
    - source setup.sh
    - {prog} check

# The pages seam: GitLab Pages serves the artifact of a job named
# ``pages`` publishing ``public/``; main only, after the checks.
pages:
  stage: release
  image: {image}
  rules:
    - if: $CI_COMMIT_BRANCH == "main"
  script:
{install}    - source setup.sh
    - {prog} docs.build
    - mv site public
  artifacts:
    paths: [public]

# The strict site build: a merge waits on the pipeline, so a broken
# link blocks it here without any aggregation job.
docs:
  stage: check
  image: {image}
  rules:
    - if: $CI_COMMIT_TAG
      when: never
    - when: on_success
  script:
{install}    - source setup.sh
    - {prog} docs.build

# The merge-triggered train: the squash of a workflow.release PR
# lands on main, its changed changelogs stating the release, and the
# wave publishes it, cutting receipt tags after the index confirms
# each member. Token publishing; pushing tags needs GITLAB_PUSH_TOKEN
# (a project access token with write_repository).
release-publish:
  stage: release
  image: {image}
  rules:
    - if: '$CI_COMMIT_BRANCH == "main" && $CI_COMMIT_TITLE =~ /^chore\\(release\\): released /'
  script:
    - git fetch --tags
    - git remote set-url origin "https://oauth2:${{GITLAB_PUSH_TOKEN}}@${{CI_SERVER_HOST}}/${{CI_PROJECT_PATH}}.git"
    - source setup.sh
    - {prog} workflow.release.publish --ref="$CI_COMMIT_SHA"
  # GitLab CI variables arrive as process environment, the cascade's
  # highest rung already: declare PYTHON_PUBLISH_INDEX,
  # PYTHON_REGISTRY_URL, and FORGE_TOKEN as CI variables; no rung
  # step is emitted here. Masking is GitLab's flag-and-constraint
  # model: mark each variable masked where its value allows it.
"""


_CODEOWNERS_PATH = {
    "github": ".github/CODEOWNERS",
    "gitea": ".gitea/CODEOWNERS",
    "gitlab": ".gitlab/CODEOWNERS",
}


def _github_governance(answers: dict[str, Any], prog: str) -> str:
    """The post-merge configure job: only governance paths spawn it.

    The admin secret is mounted here and nowhere else; a failed
    apply is a visible red job on main.
    """
    setup_uv = _setup_uv_step(answers)
    enter = _enter_step()
    return f"""name: governance
on:
  push:
    branches: [main]
    paths:
      - workshop.toml
      - packages/*/workshop.toml
      - {_CODEOWNERS_PATH["github"]}
jobs:
  apply:
    runs-on: ubuntu-latest
    steps:
      - uses: {CHECKOUT}
{setup_uv}{enter}      - run: {prog} workflow.configure
        env:
          FORGE_ADMIN_TOKEN: ${{{{ secrets.FORGE_ADMIN_TOKEN }}}}
"""


def _gitlab_governance(answers: dict[str, Any], prog: str) -> str:
    """GitLab's spelling: a pipeline job on the same pinned image."""
    python = next(iter(answers.get("python_versions", ["3.11"])))
    image = _gitlab_image(answers, python)
    return f"""
governance-apply:
  stage: release
  image: {image}
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
      changes:
        - workshop.toml
        - packages/*/workshop.toml
        - {_CODEOWNERS_PATH["gitlab"]}
  script:
    - source setup.sh
    - {prog} workflow.configure
  variables:
    FORGE_ADMIN_TOKEN: $FORGE_ADMIN_TOKEN
"""


def _github_docs_deploy(answers: dict[str, Any], prog: str) -> str:
    setup_uv = _setup_uv_step(answers)
    requirements = _docs_requirements_step(answers)
    enter = _enter_step()
    coverage_declared = bool(answers.get("docs_coverage"))
    if coverage_declared:
        # The deploy consumes exactly its commit's merged coverage, so
        # it runs after that commit's ci completes instead of on the
        # push itself; a dispatch still deploys (its build states the
        # absence on the coverage pages).
        trigger = """on:
  workflow_run:
    workflows: [ci]
    types: [completed]
    branches: [main]
  workflow_dispatch:
"""
        deploy_if = (
            "    if: github.event_name == 'workflow_dispatch' ||"
            " github.event.workflow_run.conclusion == 'success'\n"
        )
        checkout_ref = """        with:
          ref: ${{ github.event.workflow_run.head_sha || github.sha }}
          # The release view reads the receipt tags; a shallow
          # tagless clone renders its no-tags fallback page instead.
          fetch-tags: true
"""
        coverage_step = f"""      - name: Coverage artifacts for the site
        if: github.event_name == 'workflow_run'
        uses: {DOWNLOAD}
        with:
          pattern: coverage-*
          path: coverage-data
          run-id: ${{{{ github.event.workflow_run.id }}}}
          github-token: ${{{{ github.token }}}}
"""
    else:
        trigger = """on:
  push:
    branches: [main]
  workflow_dispatch:
"""
        deploy_if = ""
        checkout_ref = "        with:\n          fetch-tags: true\n"
        coverage_step = ""
    return f"""name: docs

# The pages seam: build on main, upload, deploy. What ships is the
# same strict build the required CI job verified.
{trigger}
permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: false

jobs:
  deploy:
{deploy_if}    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{{{ steps.deployment.outputs.page_url }}}}
    steps:
      - uses: {CHECKOUT}
{checkout_ref}{setup_uv}{requirements}{coverage_step}{enter}      - name: Build the site, strict
        run: {prog} docs.build
      - uses: actions/upload-pages-artifact@7b1f4a764d45c48632c6b24a0339c27f5614fb0b # v4.0.0
        with:
          path: site
      - id: deployment
        uses: actions/deploy-pages@d6db90164ac5ed86f2b6aed7e0febac5b3c0c03e # v4.0.5
"""


def generate(root: Path) -> dict[str, str]:
    """Every generated CI file for *root*'s forge kind, by path.

    The workflows call the CLI by the name this process runs under,
    so a branded runner emits workflows that call itself and needs
    no configuration. The emitted ``setup.sh`` at the root is the
    entry every workflow's jobs share. The template-artifact job is
    emitted only for a home: the contract declares where to publish
    and a member layer ships the tree; an ordinary instance's
    release has no templates to publish.
    """
    from livery.workshop._docs import publish_seam, zensical_config
    from livery.workshop._entry import entry_script
    from livery.workshop._provenance import generated_header

    prog = footman.prog()
    facts = _facts(root)
    kind = str(facts["forge_kind"])
    header = generated_header("#")
    site = {"zensical.toml": zensical_config(root)}
    seam = publish_seam(root)
    if kind == "github":
        files = {
            ".github/workflows/ci.yml": _github_gate(facts, prog),
            ".github/workflows/release.yml": _github_release(facts, prog),
            ".github/workflows/governance.yml": _github_governance(facts, prog),
        }
        if seam == "pages":
            files[".github/workflows/docs.yml"] = _github_docs_deploy(facts, prog)
    elif kind == "gitea":
        # The docs deploy and the governance reconcile are merge-point
        # jobs of ci.yml: two files, whatever the seam.
        files = {
            ".gitea/workflows/ci.yml": _gitea_gate(facts, prog),
            ".gitea/workflows/release.yml": _gitea_release(facts, prog),
        }
    else:
        files = {
            ".gitlab-ci.yml": _gitlab_pipeline(facts, prog)
            + _gitlab_governance(facts, prog)
        }
    files["setup.sh"] = entry_script(root)
    files.update(site)
    rendered = {path: header + content for path, content in files.items()}
    from livery.workshop._docs import overrides_template

    jinja_header = (
        "{#\n"
        + "".join("  " + line.removeprefix("# ") + "\n" for line in header.splitlines())
        + "#}\n"
    )
    rendered["overrides/main.html"] = jinja_header + overrides_template(root)
    return rendered


def generated_files(root: Path) -> dict[Path, str]:
    """The generated artifacts as absolute paths under *root*."""
    return {root / relative: content for relative, content in generate(root).items()}


#: Generated files an earlier emission wrote and this one folds away:
#: the apply deletes them where present, so a workspace never keeps a
#: workflow the emitter no longer owns.
RETIRED = (
    ".gitea/workflows/governance.yml",
    ".gitea/workflows/docs.yml",
)


def retired_files(root: Path) -> tuple[Path, ...]:
    """The retired generated files present under *root*, to delete."""
    return tuple(root / relative for relative in RETIRED if (root / relative).is_file())
