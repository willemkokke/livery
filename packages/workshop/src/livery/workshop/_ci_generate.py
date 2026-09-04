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
    from livery.workshop._compose import layer_template_tree
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

  # The strict site build: broken links and orphan pages go red
  # here, required through the gate context below, never inside the
  # local check.
  docs:
    runs-on: ubuntu-latest
    steps:
      - uses: {CHECKOUT}
      - uses: {SETUP_UV}
        with:
          cache-suffix: docs
      - name: Sync (locked)
        run: uv sync --locked
      - name: Build the site, strict
        run: uv run --no-sync {prog} docs.build

  # The one required context. Branch protection points here, so the
  # matrix can grow or shrink without touching repository settings.
  # It also owns the one coverage enforcement: every leg's data,
  # combined across platforms, judged against the committed floors.
  {context}:
    if: always()
    needs: [check, docs]
    runs-on: ubuntu-latest
    steps:
      - name: Verdict
        run: |
          test "${{{{ needs.check.result }}}}" = "success"
          test "${{{{ needs.docs.result }}}}" = "success"
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
      - uses: {SETUP_UV}
      - name: Sync (locked)
        run: uv sync --locked
      - name: Build this platform's wheels
        run: >-
          uv run --no-sync {prog} release.wheels
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
      - uses: {SETUP_UV}
      - name: Sync (locked)
        run: uv sync --locked
{collect_step}{rung}      # The ambient job token suffices here: the wave reads the forge
      # and pushes receipt tags, and a tag is never a trigger, so the
      # suppressed-workflow-events limit cannot bite by construction.
      - name: Publish the wave
        id: wave
        env:
          FORGE_TOKEN: ${{{{ github.token }}}}
        run: >-
          uv run --no-sync {prog} workflow.release.publish{prebuilt_flag}
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
      - uses: {SETUP_UV}
      - name: Sync (locked)
        run: uv sync --locked
      - name: Deploy key
        run: |
          mkdir -p ~/.ssh
          printf '%s\\n' "${{{{ secrets.WORKSHOP_TEMPLATES_DEPLOY_KEY }}}}" > ~/.ssh/templates_deploy
          chmod 600 ~/.ssh/templates_deploy
      - name: Publish the template artifact
        env:
          GIT_SSH_COMMAND: ssh -i ~/.ssh/templates_deploy -o StrictHostKeyChecking=accept-new
          FORGE_TOKEN: ${{{{ secrets.FORGE_TOKEN }}}}
        run: uv run --no-sync {prog} release.templates
"""
    )


def _gitea_gate(answers: dict[str, Any], prog: str) -> str:
    context = answers.get("required_context", "gate")
    runners = _csv(list(answers.get("runners", ["ubuntu-latest"])))
    pythons = _csv(list(answers.get("python_versions", ["3.11"])), quoted=True)
    first = next(iter(answers.get("runners", ["ubuntu-latest"])))
    rung = _rung_step(answers)
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
{rung}      - name: Gate
        run: $HOME/.local/bin/uv run --no-sync {prog} check

  # The strict site build, required through the gate context below.
  docs:
    runs-on: {first}
    steps:
      - uses: actions/checkout@v4
      - name: Install uv
        run: curl -LsSf https://astral.sh/uv/install.sh | sh
      - name: Sync (locked)
        run: $HOME/.local/bin/uv sync --locked
      - name: Build the site, strict
        run: $HOME/.local/bin/uv run --no-sync {prog} docs.build

  # The one required context. Branch protection points here, so the
  # matrix can grow or shrink without touching repository settings.
  {context}:
    if: always()
    needs: [check, docs]
    runs-on: {first}
    steps:
      - name: Verdict
        run: |
          test "${{{{ needs.check.result }}}}" = "success"
          test "${{{{ needs.docs.result }}}}" = "success"
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
    rung = _rung_step(answers)
    wheels = _wants_wheel_matrix(answers)
    runners = _csv(list(answers.get("runners", ["ubuntu-latest"])))
    train_if = """>-
      github.event.pull_request.merged == true &&
      startsWith(github.event.pull_request.head.ref, 'workflow/release/')"""
    wheels_job = (
        f"""  # Every declared runner's wheels, built before the wave and
  # collected as artifacts. linux arm waits on a docker-capable
  # runner, the container seam's known constraint.
  wheels:
    if: {train_if}
    strategy:
      fail-fast: false
      matrix:
        runner: [{runners}]
    runs-on: ${{{{ matrix.runner }}}}
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{{{ github.event.pull_request.merge_commit_sha }}}}
          fetch-depth: 0
      - name: Install uv
        run: curl -LsSf https://astral.sh/uv/install.sh | sh
      - name: Sync (locked)
        run: $HOME/.local/bin/uv sync --locked
      - name: Build this platform's wheels
        run: >-
          $HOME/.local/bin/uv run --no-sync {prog} release.wheels
          --ref="${{{{ github.event.pull_request.merge_commit_sha }}}}"
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

# The merge-triggered train, token publishing (Gitea has no trusted
# publishing): the wave publishes the squash and cuts receipt tags
# after the index confirms each member.
on:
  pull_request:
    types: [closed]

jobs:
{wheels_job}  publish:
    if: {train_if}
{needs_wheels}    runs-on: {first}
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{{{ github.event.pull_request.merge_commit_sha }}}}
          fetch-depth: 0
      - name: Install uv
        run: curl -LsSf https://astral.sh/uv/install.sh | sh
      - name: Sync (locked)
        run: $HOME/.local/bin/uv sync --locked
{collect_step}{rung}      # PYTHON_PUBLISH_INDEX and PYTHON_REGISTRY_URL come from the
      # committed .repo.env through the env cascade; only the secrets
      # are mounted here. Gitea's automatic token serves the wave's
      # forge reads and receipt-tag pushes.
      - name: Publish the wave
        env:
          UV_PUBLISH_TOKEN: ${{{{ secrets.UV_PUBLISH_TOKEN }}}}
          FORGE_TOKEN: ${{{{ secrets.GITHUB_TOKEN }}}}
        run: >-
          $HOME/.local/bin/uv run --no-sync {prog} workflow.release.publish{prebuilt_flag}
          --ref="${{{{ github.event.pull_request.merge_commit_sha }}}}"
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
          ref: ${{{{ github.event.pull_request.merge_commit_sha }}}}
      - name: Install uv
        run: curl -LsSf https://astral.sh/uv/install.sh | sh
      - name: Sync (locked)
        run: $HOME/.local/bin/uv sync --locked
      - name: Publish the template artifact
        env:
          FORGE_TOKEN: ${{{{ secrets.FORGE_TOKEN }}}}
        run: $HOME/.local/bin/uv run --no-sync {prog} release.templates
"""
    )


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

# The pages seam: GitLab Pages serves the artifact of a job named
# ``pages`` publishing ``public/``; main only, after the checks.
pages:
  stage: release
  image: ghcr.io/astral-sh/uv:python{python}-bookworm
  rules:
    - if: $CI_COMMIT_BRANCH == "main"
  script:
    - uv sync --locked
    - uv run --no-sync {prog} docs.build
    - mv site public
  artifacts:
    paths: [public]

# The strict site build: a merge waits on the pipeline, so a broken
# link blocks it here without any aggregation job.
docs:
  stage: check
  image: ghcr.io/astral-sh/uv:python{python}-bookworm
  rules:
    - if: $CI_COMMIT_TAG
      when: never
    - when: on_success
  script:
    - uv sync --locked
    - uv run --no-sync {prog} docs.build

# The merge-triggered train: the squash of a workflow.release PR
# lands on main, its changed changelogs stating the release, and the
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
          FORGE_ADMIN_TOKEN: ${{{{ secrets.FORGE_ADMIN_TOKEN }}}}
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
          FORGE_ADMIN_TOKEN: ${{{{ secrets.FORGE_ADMIN_TOKEN }}}}
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
    FORGE_ADMIN_TOKEN: $FORGE_ADMIN_TOKEN
"""


def _github_docs_deploy(prog: str) -> str:
    return f"""name: docs

# The pages seam: build on main, upload, deploy. What ships is the
# same strict build the required CI job verified.
on:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: false

jobs:
  deploy:
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{{{ steps.deployment.outputs.page_url }}}}
    steps:
      - uses: {CHECKOUT}
      - uses: {SETUP_UV}
      - name: Sync (locked)
        run: uv sync --locked
      - name: Build the site, strict
        run: uv run --no-sync {prog} docs.build
      - uses: actions/upload-pages-artifact@7b1f4a764d45c48632c6b24a0339c27f5614fb0b # v4.0.0
        with:
          path: site
      - id: deployment
        uses: actions/deploy-pages@d6db90164ac5ed86f2b6aed7e0febac5b3c0c03e # v4.0.5
"""


def _gitea_docs_deploy(answers: dict[str, Any], prog: str) -> str:
    first = next(iter(answers.get("runners", ["ubuntu-latest"])))
    return f"""name: docs

# The container seam: the built site pushed as an image to the
# forge's own registry. The job needs a runner with docker; the
# publish verb resolves the seam and the registry from the contract.
on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: {first}
    steps:
      - uses: actions/checkout@v4
      - name: Install uv
        run: curl -LsSf https://astral.sh/uv/install.sh | sh
      - name: Sync (locked)
        run: $HOME/.local/bin/uv sync --locked
      - name: Build the site, strict
        run: $HOME/.local/bin/uv run --no-sync {prog} docs.build
      - name: Publish the site image
        env:
          FORGE_TOKEN: ${{{{ secrets.GITHUB_TOKEN }}}}
        run: $HOME/.local/bin/uv run --no-sync {prog} docs.publish
"""


def generate(root: Path) -> dict[str, str]:
    """Every generated CI file for *root*'s forge kind, by path.

    The workflows call the CLI by the name this process runs under,
    so a branded runner emits workflows that call itself and needs
    no configuration. The template-artifact job is emitted only for
    a home: the contract declares where to publish and a member
    layer ships the tree; an ordinary instance's release has no
    templates to publish.
    """
    from livery.workshop._docs import publish_seam, zensical_config
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
            files[".github/workflows/docs.yml"] = _github_docs_deploy(prog)
    elif kind == "gitea":
        files = {
            ".gitea/workflows/ci.yml": _gitea_gate(facts, prog),
            ".gitea/workflows/release.yml": _gitea_release(facts, prog),
            ".gitea/workflows/governance.yml": _gitea_governance(facts, prog),
        }
        if seam == "container":
            files[".gitea/workflows/docs.yml"] = _gitea_docs_deploy(facts, prog)
    else:
        files = {
            ".gitlab-ci.yml": _gitlab_pipeline(facts, prog)
            + _gitlab_governance(facts, prog)
        }
    files.update(site)
    return {path: header + content for path, content in files.items()}


def generated_files(root: Path) -> dict[Path, str]:
    """The generated artifacts as absolute paths under *root*."""
    return {root / relative: content for relative, content in generate(root).items()}
