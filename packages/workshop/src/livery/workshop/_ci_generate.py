"""The CI workflow emitters: forge-dependent mechanics, generated.

Workflow files are mechanical forge knowledge, emitted here from the
workspace's answers and written as managed generated artifacts; the
templates carry none of it, and the render gate compares the
committed files against these same pure functions, offline. Change
an emitter, run ``fm template.apply``, and every kind's files move
together; a template update is never the vehicle.

The release workflows are the merge-triggered train: publishing runs
where the release PR's squash lands, and the receipt tags are cut by
``fm workflow.release.publish`` after the index confirms each
member, so a tag is a receipt, never a trigger.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

#: Pinned action shas, one place; version comments ride each use.
CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1"
SETUP_UV = "astral-sh/setup-uv@20cfd1bf945f4377ade1205e4dbc17946fc9a30d # v10.0.1"
UPLOAD = "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1"
DOWNLOAD = "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1"


def _csv(values: list[Any], *, quoted: bool = False) -> str:
    return ", ".join(f'"{v}"' if quoted else str(v) for v in values)


def _github_gate(answers: dict[str, Any]) -> str:
    context = answers.get("required_context", "gate")
    runners = _csv(list(answers.get("runners", ["ubuntu-latest"])))
    pythons = _csv(list(answers.get("python_versions", ["3.11"])), quoted=True)
    return f"""name: ci

on:
  pull_request:
  push:
    branches: [main]

jobs:
  check:
    strategy:
      fail-fast: false
      matrix:
        os: [{runners}]
        python: [{pythons}]
    runs-on: ${{{{ matrix.os }}}}
    steps:
      - uses: {CHECKOUT}
      - uses: {SETUP_UV}
        with:
          # One cache identity per matrix leg: a shared key makes
          # every leg but the first fail its save with a reservation
          # warning.
          cache-suffix: ${{{{ matrix.os }}}}-${{{{ matrix.python }}}}
      - name: Sync (locked)
        run: uv sync --locked --python ${{{{ matrix.python }}}}
      - name: Gate, measured
        env:
          # The parent `coverage run` meters the whole fm invocation;
          # the test step adds no second meter, and the floors are
          # judged once, on the merged union, in the gate job below.
          LIVERY_COVERAGE_PARENT: "1"
        run: |
          uv run --no-sync coverage run -m footman check
          uv run --no-sync coverage combine
      - name: Leg coverage data
        uses: {UPLOAD}
        with:
          name: coverage-${{{{ matrix.os }}}}-${{{{ matrix.python }}}}
          path: .coverage
          include-hidden-files: true
          if-no-files-found: error

  # The one required context. Branch protection points here, so the
  # matrix can grow or shrink without touching repository settings.
  # It also owns the one coverage enforcement: every leg's data,
  # combined across platforms, judged against the committed floors.
  {context}:
    if: always()
    needs: [check]
    runs-on: ubuntu-latest
    steps:
      - name: Verdict
        run: test "${{{{ needs.check.result }}}}" = "success"
      - uses: {CHECKOUT}
      - uses: {SETUP_UV}
      - name: Sync (locked)
        run: uv sync --locked
      - name: Combine every leg
        uses: {DOWNLOAD}
        with:
          pattern: coverage-*
          path: coverage-data
      - name: Enforce the floors on the union
        run: |
          uv run --no-sync coverage combine coverage-data/*/.coverage
          uv run --no-sync coverage report --sort=cover
          uv run --no-sync fm coverage.enforce
"""


def _github_release(answers: dict[str, Any]) -> str:
    return f"""name: release

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
  publish:
    if: >-
      github.event_name == 'workflow_dispatch' ||
      (github.event.pull_request.merged == true &&
       startsWith(github.event.pull_request.head.ref, 'workflow/release/'))
    runs-on: ubuntu-latest
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
      - uses: {SETUP_UV}
      - name: Sync (locked)
        run: uv sync --locked
      - name: Publish the wave
        id: wave
        run: >-
          uv run --no-sync fm workflow.release.publish
          --ref="${{{{ inputs.ref || github.event.pull_request.merge_commit_sha }}}}"

  # The workshop release's aftermath: the template snapshot, tagged in
  # lockstep with packages/workshop's receipt.
  templates:
    needs: [publish]
    if: contains(needs.publish.outputs.members, 'livery-workshop')
    runs-on: ubuntu-latest
    steps:
      - uses: {CHECKOUT}
        with:
          ref: ${{{{ inputs.ref || github.event.pull_request.merge_commit_sha }}}}
      - uses: {SETUP_UV}
      - name: Sync (locked)
        run: uv sync --locked
      - name: Deploy key
        run: |
          mkdir -p ~/.ssh
          printf '%s\\n' "${{{{ secrets.WORKSHOP_TEMPLATES_DEPLOY_KEY }}}}" > ~/.ssh/templates_deploy
          chmod 600 ~/.ssh/templates_deploy
      - name: Publish the template snapshot
        env:
          GIT_SSH_COMMAND: ssh -i ~/.ssh/templates_deploy -o StrictHostKeyChecking=accept-new
        run: >-
          uv run --no-sync fm release.templates
          "$(uv run --no-sync python -c 'import livery.workshop as w; print(w.__version__)')"
"""


def _gitea_gate(answers: dict[str, Any]) -> str:
    context = answers.get("required_context", "gate")
    runners = _csv(list(answers.get("runners", ["ubuntu-latest"])))
    pythons = _csv(list(answers.get("python_versions", ["3.11"])), quoted=True)
    first = next(iter(answers.get("runners", ["ubuntu-latest"])))
    return f"""name: ci

on:
  pull_request:
  push:
    branches: [main]

jobs:
  check:
    strategy:
      fail-fast: false
      matrix:
        os: [{runners}]
        python: [{pythons}]
    runs-on: ${{{{ matrix.os }}}}
    steps:
      - uses: actions/checkout@v4
      # act_runner host mode: no setup actions, POSIX sh, uv from its
      # installer.
      - name: Install uv
        run: curl -LsSf https://astral.sh/uv/install.sh | sh
      - name: Sync (locked)
        run: $HOME/.local/bin/uv sync --locked --python ${{{{ matrix.python }}}}
      - name: Gate
        run: $HOME/.local/bin/uv run --no-sync fm check

  # The one required context. Branch protection points here, so the
  # matrix can grow or shrink without touching repository settings.
  {context}:
    if: always()
    needs: [check]
    runs-on: {first}
    steps:
      - name: Verdict
        run: test "${{{{ needs.check.result }}}}" = "success"
"""


def _gitea_release(answers: dict[str, Any]) -> str:
    first = next(iter(answers.get("runners", ["ubuntu-latest"])))
    index = answers.get("publish_index", "")
    return f"""name: release

# The merge-triggered train, token publishing (Gitea has no trusted
# publishing): the wave publishes the squash and cuts receipt tags
# after the index confirms each member.
on:
  pull_request:
    types: [closed]

jobs:
  publish:
    if: >-
      github.event.pull_request.merged == true &&
      startsWith(github.event.pull_request.head.ref, 'workflow/release/')
    runs-on: {first}
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{{{ github.event.pull_request.merge_commit_sha }}}}
          fetch-depth: 0
      - name: Install uv
        run: curl -LsSf https://astral.sh/uv/install.sh | sh
      - name: Sync (locked)
        run: $HOME/.local/bin/uv sync --locked
      - name: Publish the wave
        env:
          UV_PUBLISH_TOKEN: ${{{{ secrets.UV_PUBLISH_TOKEN }}}}
          LIVERY_PUBLISH_INDEX: "{index}"
          LIVERY_REGISTRY_URL: "{index and index.replace("/pypi", "/pypi/simple")}"
        run: >-
          $HOME/.local/bin/uv run --no-sync fm workflow.release.publish
          --ref="${{{{ github.event.pull_request.merge_commit_sha }}}}"
"""


def _gitlab_pipeline(answers: dict[str, Any]) -> str:
    context = answers.get("required_context", "gate")
    python = next(iter(answers.get("python_versions", ["3.11"])))
    index = answers.get("publish_index", "")
    return f"""# The gate and the train, GitLab-shaped, generated by the workshop.
# One pipeline definition: workflow rules admit merge requests, main,
# and LIVERY_WORKFLOW-routed manual pipelines.
workflow:
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    - if: $CI_COMMIT_BRANCH == "main"
    - if: $LIVERY_WORKFLOW

stages: [check, release]

{context}:
  stage: check
  image: ghcr.io/astral-sh/uv:python{python}-bookworm
  rules:
    - if: $CI_COMMIT_TAG
      when: never
    - when: on_success
  script:
    - uv sync --locked
    - uv run --no-sync fm check

# The merge-triggered train: the squash of a workflow.release PR
# lands on main carrying the release manifest in its title, and the
# wave publishes it, cutting receipt tags after the index confirms
# each member. Token publishing; pushing tags needs GITLAB_PUSH_TOKEN
# (a project access token with write_repository).
release-publish:
  stage: release
  image: ghcr.io/astral-sh/uv:python{python}-bookworm
  rules:
    - if: '$CI_COMMIT_BRANCH == "main" && $CI_COMMIT_TITLE =~ /^chore\\(release\\): released /'
  script:
    - git fetch --tags
    - git remote set-url origin "https://oauth2:${{GITLAB_PUSH_TOKEN}}@${{CI_SERVER_HOST}}/${{CI_PROJECT_PATH}}.git"
    - uv sync --locked
    - uv run --no-sync fm workflow.release.publish --ref="$CI_COMMIT_SHA"
  variables:
    LIVERY_PUBLISH_INDEX: "{index}"
    LIVERY_REGISTRY_URL: "{index and index + "/simple"}"
"""


def generate(answers: dict[str, Any]) -> dict[str, str]:
    """Every generated CI file for *answers*' forge kind, by path."""
    kind = str(answers.get("forge_kind", "github"))
    if kind == "github":
        return {
            ".github/workflows/ci.yml": _github_gate(answers),
            ".github/workflows/release.yml": _github_release(answers),
        }
    if kind == "gitea":
        return {
            ".gitea/workflows/ci.yml": _gitea_gate(answers),
            ".gitea/workflows/release.yml": _gitea_release(answers),
        }
    return {".gitlab-ci.yml": _gitlab_pipeline(answers)}


def generated_files(root: Path, answers: dict[str, Any]) -> dict[Path, str]:
    """The generated artifacts as absolute paths under *root*."""
    return {root / relative: content for relative, content in generate(answers).items()}
