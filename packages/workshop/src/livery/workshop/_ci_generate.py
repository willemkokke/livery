"""The CI workflow emitters: forge-dependent mechanics, generated.

Workflow files are mechanical forge knowledge, emitted here from the
workspace contract (``workshop.toml``) and the derived Python matrix,
written as managed generated artifacts; the templates carry none of
it, and the render gate compares the committed files against these
same pure functions, offline. Change an emitter, run
``fm template.apply``, and every kind's files move together; a
template update is never the vehicle.

The release workflows are the merge-triggered train: publishing runs
where the release PR's squash lands, and the receipt tags are cut by
``fm workflow.release.publish`` after the index confirms each
member, so a tag is a receipt, never a trigger.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import footman

from livery.workshop._pythons import python_matrix

#: Pinned action shas, one place; version comments ride each use.
CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1"
SETUP_UV = "astral-sh/setup-uv@20cfd1bf945f4377ade1205e4dbc17946fc9a30d # v10.0.1"
UPLOAD = "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1"
DOWNLOAD = "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1"


def _facts(root: Path) -> dict[str, Any]:
    """What the emitters read: the contract's CI facts, matrix derived.

    Runners, the required context, and the forge kind come from
    ``workshop.toml``; the Python matrix from the root
    ``pyproject.toml``'s floor and the workshop's newest supported
    minor. Nothing here is an answer: the answers hold identity
    alone.
    """
    contract = tomllib.loads((root / "workshop.toml").read_text("utf-8"))
    ci = contract.get("ci") or {}
    return {
        "forge_kind": str((contract.get("forge") or {}).get("kind", "github")),
        "runners": list(ci.get("runners") or ["ubuntu-latest"]),
        "required_context": str(ci.get("required_context", "gate")),
        "python_versions": python_matrix(root),
    }


def _csv(values: list[Any], *, quoted: bool = False) -> str:
    return ", ".join(f'"{v}"' if quoted else str(v) for v in values)


def _github_gate(answers: dict[str, Any], prog: str) -> str:
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
          uv run --no-sync {prog} check
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
          uv run --no-sync {prog} coverage.enforce
  release-title:
    if: startsWith(github.head_ref, 'workflow/release/')
    runs-on: ubuntu-latest
    steps:
      - uses: {CHECKOUT}
        with:
          # check-title compares against origin/main, which a shallow
          # checkout does not have.
          fetch-depth: 0
      - uses: {SETUP_UV}
      - name: Sync (locked)
        run: uv sync --locked
      - run: uv run --no-sync {prog} workflow.release.check-title --title "$TITLE"
        env:
          TITLE: ${{{{ github.event.pull_request.title }}}}
"""


def _github_release(answers: dict[str, Any], prog: str, *, templates_here: bool) -> str:
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
          uv run --no-sync {prog} workflow.release.publish
          --ref="${{{{ inputs.ref || github.event.pull_request.merge_commit_sha }}}}"
"""
    if not templates_here:
        return workflow
    # Only the workspace that carries the template source publishes
    # the snapshot; an instance's release has no templates to ship.
    return (
        workflow
        + f"""
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
          uv run --no-sync {prog} release.templates
          "$(uv run --no-sync python -c 'import livery.workshop as w; print(w.__version__)')"
"""
    )


def _gitea_gate(answers: dict[str, Any], prog: str) -> str:
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
        run: $HOME/.local/bin/uv run --no-sync {prog} check

  # The one required context. Branch protection points here, so the
  # matrix can grow or shrink without touching repository settings.
  {context}:
    if: always()
    needs: [check]
    runs-on: {first}
    steps:
      - name: Verdict
        run: test "${{{{ needs.check.result }}}}" = "success"
  release-title:
    if: startsWith(github.head_ref, 'workflow/release/')
    runs-on: {first}
    steps:
      - uses: actions/checkout@v4
        with:
          # check-title compares against origin/main, which a shallow
          # checkout does not have.
          fetch-depth: 0
      - name: Install uv
        run: curl -LsSf https://astral.sh/uv/install.sh | sh
      - name: Sync (locked)
        run: $HOME/.local/bin/uv sync --locked
      - run: >-
          $HOME/.local/bin/uv run --no-sync
          {prog} workflow.release.check-title --title "$TITLE"
        env:
          TITLE: ${{{{ github.event.pull_request.title }}}}
"""


def _gitea_release(answers: dict[str, Any], prog: str) -> str:
    first = next(iter(answers.get("runners", ["ubuntu-latest"])))
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
      # PYTHON_PUBLISH_INDEX and PYTHON_REGISTRY_URL come from the
      # committed .repo.env through the env cascade; only the secret
      # is mounted here.
      - name: Publish the wave
        env:
          UV_PUBLISH_TOKEN: ${{{{ secrets.UV_PUBLISH_TOKEN }}}}
        run: >-
          $HOME/.local/bin/uv run --no-sync {prog} workflow.release.publish
          --ref="${{{{ github.event.pull_request.merge_commit_sha }}}}"
"""


def _gitlab_pipeline(answers: dict[str, Any], prog: str) -> str:
    context = answers.get("required_context", "gate")
    python = next(iter(answers.get("python_versions", ["3.11"])))
    return f"""# The gate and the train, GitLab-shaped, generated by the workshop.
# One pipeline definition: workflow rules admit merge requests, main,
# and FORGE_WORKFLOW-routed manual pipelines.
workflow:
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    - if: $CI_COMMIT_BRANCH == "main"
    - if: $FORGE_WORKFLOW

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
    - uv run --no-sync {prog} check

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
    - uv run --no-sync {prog} workflow.release.publish --ref="$CI_COMMIT_SHA"
  # PYTHON_PUBLISH_INDEX and PYTHON_REGISTRY_URL come from the
  # committed .repo.env through the env cascade.
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
    del answers
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
      - uses: {SETUP_UV}
      - name: Sync (locked)
        run: uv sync --locked
      - run: uv run --no-sync {prog} workflow.configure
        env:
          GITHUB_ADMIN_TOKEN: ${{{{ secrets.FORGE_ADMIN_TOKEN }}}}
"""


def _gitea_governance(answers: dict[str, Any], prog: str) -> str:
    """Gitea's spelling of the same path-filtered apply job.

    act_runner host mode, like the gate: no setup actions, uv from
    its installer, on the configured runner label.
    """
    first = next(iter(answers.get("runners", ["ubuntu-latest"])))
    return f"""name: governance
on:
  push:
    branches: [main]
    paths:
      - workshop.toml
      - packages/*/workshop.toml
      - {_CODEOWNERS_PATH["gitea"]}
jobs:
  apply:
    runs-on: {first}
    steps:
      - uses: actions/checkout@v4
      - name: Install uv
        run: curl -LsSf https://astral.sh/uv/install.sh | sh
      - name: Sync (locked)
        run: $HOME/.local/bin/uv sync --locked
      - run: $HOME/.local/bin/uv run --no-sync {prog} workflow.configure
        env:
          GITEA_ADMIN_TOKEN: ${{{{ secrets.FORGE_ADMIN_TOKEN }}}}
"""


def _gitlab_governance(answers: dict[str, Any], prog: str) -> str:
    """GitLab's spelling: a pipeline job on the same pinned image."""
    python = next(iter(answers.get("python_versions", ["3.11"])))
    return f"""
governance-apply:
  stage: release
  image: ghcr.io/astral-sh/uv:python{python}-bookworm
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
      changes:
        - workshop.toml
        - packages/*/workshop.toml
        - {_CODEOWNERS_PATH["gitlab"]}
  script:
    - uv sync --locked
    - uv run --no-sync {prog} workflow.configure
  variables:
    GITLAB_ADMIN_TOKEN: $FORGE_ADMIN_TOKEN
"""


def generate(root: Path) -> dict[str, str]:
    """Every generated CI file for *root*'s forge kind, by path.

    The workflows call the CLI by the name this process runs under,
    so a branded runner emits workflows that call itself and needs
    no configuration. The template-snapshot job is emitted only
    where the template source is a local directory: an instance's
    release has no templates to publish.
    """
    from livery.workshop._provenance import generated_header
    from livery.workshop._templates import local_template_dir

    prog = footman.prog()
    facts = _facts(root)
    kind = str(facts["forge_kind"])
    header = generated_header("#")
    if kind == "github":
        files = {
            ".github/workflows/ci.yml": _github_gate(facts, prog),
            ".github/workflows/release.yml": _github_release(
                facts, prog, templates_here=local_template_dir(root) is not None
            ),
            ".github/workflows/governance.yml": _github_governance(facts, prog),
        }
    elif kind == "gitea":
        files = {
            ".gitea/workflows/ci.yml": _gitea_gate(facts, prog),
            ".gitea/workflows/release.yml": _gitea_release(facts, prog),
            ".gitea/workflows/governance.yml": _gitea_governance(facts, prog),
        }
    else:
        files = {
            ".gitlab-ci.yml": _gitlab_pipeline(facts, prog)
            + _gitlab_governance(facts, prog)
        }
    return {path: header + content for path, content in files.items()}


def generated_files(root: Path) -> dict[Path, str]:
    """The generated artifacts as absolute paths under *root*."""
    return {root / relative: content for relative, content in generate(root).items()}
