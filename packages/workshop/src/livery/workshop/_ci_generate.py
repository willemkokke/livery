"""The CI workflow renderers: the declared points, in each forge's words.

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

from pathlib import Path
from typing import Any

import livery.footman as footman
from livery.workshop._contract import load_contract
from livery.workshop._points import EVENT_NAMES, Job, Point, points
from livery.workshop._pythons import gate_pythons, python_matrix

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
    from livery.workshop._docs import (
        docs_requirements,
        publish_seam,
    )
    from livery.workshop._entry import locked_uv_version
    from livery.workshop._envfile import parse_env_file
    from livery.workshop._layers import layer_entries
    from livery.workshop._templates import templates_artifact
    from livery.workshop._wheels import member_roster, wheel_runners

    contract = load_contract(root / "workshop.toml")
    ci = contract.get("ci") or {}
    publisher = ""
    for layer, dist in layer_entries(root):
        tree = layer_template_tree(root, layer)
        if tree is not None and tree.is_relative_to(root):
            publisher = dist
    return {
        "forge_kind": str((contract.get("forge") or {}).get("kind", "github")),
        "runners": list(ci.get("runners") or ["ubuntu-latest"]),
        "required_context": str(ci.get("required-context", "gate")),
        "python_versions": python_matrix(root),
        "gate_pythons": gate_pythons(root),
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
        # declaring packages' generators can render their trees.
        "publish_seam": publish_seam(root),
        # The committed .repo.env's keys: the offline, deterministic
        # list of which secrets the rung step may carry into a job.
        "env_keys": sorted(parse_env_file(root / ".repo.env")),
        # The publish side: where this home ships its template
        # artifact, and which member layer's release triggers it.
        "templates_artifact": templates_artifact(root),
        "templates_publisher": publisher,
        # The members with their kinds, and the runner labels the
        # platform-wheel members declare: the wheels job exists when
        # the union is non-empty and runs one leg per label.
        "packages": member_roster(root),
        "wheel_runners": wheel_runners(root),
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


DRIVER_DIST = "livery-workshop"
"""The distribution a re-dispatched wave may pin as its driver."""


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


def _wheel_runners(answers: dict[str, Any]) -> list[str]:
    """The runner labels that build platform wheels; empty for a pure workspace.

    The facts carry the union of the members' ``[ci] wheel-platforms``
    declarations. The wheels job is emitted only when it is
    non-empty: a pure workspace releases from one runner, and its
    workflow says so by shape.
    """
    return [str(label) for label in answers.get("wheel_runners", []) or []]


def _gitlab_image(answers: dict[str, Any], python: str) -> str:
    """The pinned uv image for a GitLab job.

    With a lock the tag carries the pin
    (``<pin>-python<minor>-bookworm``), so the job's uv is the lock's
    uv; without one the unversioned tag is the honest fallback.
    """
    pin = str(answers.get("uv_pin", ""))
    prefix = f"{pin}-" if pin else ""
    return f"ghcr.io/astral-sh/uv:{prefix}python{python}-bookworm"


def _comment(text: str, indent: str = "") -> str:
    """*text* as YAML comment lines under *indent*; empty for empty."""
    import textwrap

    if not text:
        return ""
    width = max(40, 72 - len(indent))
    return "".join(f"{indent}# {line}\n" for line in textwrap.wrap(text, width=width))


def _renders(job: Job, answers: dict[str, Any]) -> bool:
    """Whether *job* exists for this workspace: its ``only`` against the facts."""
    if job.only == "wheels":
        return bool(_wheel_runners(answers))
    if job.only == "home":
        return bool(answers.get("templates_artifact")) and bool(
            answers.get("templates_publisher")
        )
    return True


def _points_of(workflow: str, everything: tuple[Point, ...]) -> tuple[Point, ...]:
    """The points among *everything* whose shell *workflow* is, in order."""
    return tuple(point for point in everything if point.workflow == workflow)


def _inherited_jobs(point: Point, everything: tuple[Point, ...]) -> tuple[Job, ...]:
    """The jobs *point* runs before its own, from the point it inherits."""
    if not point.inherits:
        return ()
    return next(other for other in everything if other.name == point.inherits).jobs


def _needs(
    point: Point, job: Job, answers: dict[str, Any], everything: tuple[Point, ...]
) -> list[str]:
    """The jobs *job* waits for that render for this workspace."""
    rendered = {
        other.name
        for other in (*_inherited_jobs(point, everything), *point.jobs)
        if _renders(other, answers)
    }
    return [need for need in job.needs if need in rendered]


def _event_filter(point: Point) -> str:
    """The Actions condition admitting *point*'s events alone."""
    return " || ".join(f"github.event_name == '{event}'" for event in point.events)


def _call_step(prog: str, point: Point, job: Job) -> str:
    """The one call: ``ci.run`` for the point and job, the matrix facts passed."""
    call = f"{prog} ci.run --point={point.name} --job={job.name}"
    if job.matrix in ("legs", "declared"):
        return (
            "        run: >-\n"
            f"          {call}\n"
            '          --os="${{ matrix.os }}" --python="${{ matrix.python }}"\n'
        )
    if job.matrix == "pythons":
        return (
            f"        run: >-\n          {call}\n"
            '          --python="${{ matrix.python }}"\n'
        )
    return f"        run: {call}\n"


def _call_env(job: Job, *, forge: str) -> str:
    """What the call sees: the credential, the index token, the deploy key's ssh."""
    lines: list[str] = []
    if job.token == "job":
        lines.append("FORGE_TOKEN: ${{ secrets.GITHUB_TOKEN }}")
    elif job.token == "repository":
        lines.append("FORGE_TOKEN: ${{ secrets.FORGE_TOKEN || secrets.GITHUB_TOKEN }}")
    elif job.token == "secret":
        lines.append("FORGE_TOKEN: ${{ secrets.FORGE_TOKEN }}")
    elif job.token == "admin":
        lines.append("FORGE_ADMIN_TOKEN: ${{ secrets.FORGE_ADMIN_TOKEN }}")
    if job.publishes_index:
        # A token publishes where the repository has one; on GitHub an
        # absent secret arrives empty, which the publish verb drops
        # before it spawns uv, so uv stays on trusted publishing.
        secret = "PYPI_TOKEN" if forge == "github" else "UV_PUBLISH_TOKEN"
        lines.append(f"UV_PUBLISH_TOKEN: ${{{{ secrets.{secret} }}}}")
    if job.deploy_key and forge == "github":
        lines.append(
            "GIT_SSH_COMMAND: ssh -i ~/.ssh/templates_deploy"
            " -o StrictHostKeyChecking=accept-new"
        )
    if not lines:
        return ""
    return "        env:\n" + "".join(f"          {line}\n" for line in lines)


def _checkout_step(point: Point, job: Job, *, forge: str) -> str:
    """The checkout: as deep as the verbs need, at the dispatched ref, able to push."""
    action = CHECKOUT if forge == "github" else "actions/checkout@v4"
    with_lines: list[str] = []
    if point.ref_input:
        with_lines.append(f"          ref: ${{{{ inputs.{point.ref_input} }}}}")
    if job.fetch == "full":
        with_lines.append("          fetch-depth: 0")
    elif job.fetch == "tags":
        with_lines.append("          fetch-tags: true")
    elif job.fetch:
        with_lines.append(f"          fetch-depth: {job.fetch}")
    if job.pushes:
        # The push rides this checkout's credential: the job token may
        # not push a ref whose commit carries a workflow file that
        # differs from the tip's, and receipt tags are protected.
        token = (
            "${{ secrets.FORGE_TOKEN || github.token }}"
            if forge == "github"
            else "${{ secrets.FORGE_TOKEN }}"
        )
        with_lines.append(f"          token: {token}")
    lines = [f"      - uses: {action}\n"]
    if with_lines:
        lines.append("        with:\n")
        lines.extend(line + "\n" for line in with_lines)
    return "".join(lines)


def _collect_step(job: Job, *, forge: str) -> str:
    """The artifacts *job* collects, merged into the packages tree."""
    if not job.collects:
        return ""
    action = DOWNLOAD if forge == "github" else "actions/download-artifact@v4"
    return (
        f"      - uses: {action}\n"
        "        with:\n"
        f"          pattern: {job.collects}-*\n"
        "          path: packages\n"
        "          merge-multiple: true\n"
    )


def _publish_step(job: Job, *, forge: str) -> str:
    """The artifact *job* publishes: this leg's built wheels."""
    if not job.publishes:
        return ""
    action = UPLOAD if forge == "github" else "actions/upload-artifact@v4"
    return (
        f"      - uses: {action}\n"
        "        with:\n"
        f"          name: {job.publishes}-${{{{ matrix.os }}}}\n"
        "          path: packages/*/dist/*\n"
        "          if-no-files-found: ignore\n"
    )


def _driver_step(prog: str, point: Point, job: Job, *, forge: str) -> str:
    """The driver pin, through its verb: the released workshop the input names."""
    if not job.driver_pin or not any(item.name == "workshop" for item in point.inputs):
        return ""
    value = "${{ inputs.workshop }}" if forge != "gitlab" else "$workshop"
    return (
        "      - name: Pin the driver\n"
        f'        run: {prog} release.driver --workshop="{value}"\n'
    )


def _deploy_key_step(job: Job) -> str:
    """The deploy key written to disk for a cross-repository push over ssh (GitHub)."""
    if not job.deploy_key:
        return ""
    return (
        "      - name: Deploy key\n"
        "        run: |\n"
        "          mkdir -p ~/.ssh\n"
        f"          printf '%s\\n' \"${{{{ secrets.{job.deploy_key} }}}}\""
        " > ~/.ssh/templates_deploy\n"
        "          chmod 600 ~/.ssh/templates_deploy\n"
    )


def _profile_step(forge: str) -> str:
    """The run's Chrome trace kept as an artifact, whatever the verdict."""
    action = UPLOAD if forge == "github" else GITEA_UPLOAD
    return (
        "      # The run as a Chrome trace, one artifact per leg. Observational,\n"
        "      # so it runs on a red gate too and its own exit never decides\n"
        "      # the leg.\n"
        "      - name: Upload the run profile\n"
        "        if: always()\n"
        "        continue-on-error: true\n"
        f"        uses: {action}\n"
        "        with:\n"
        "          name: profile-${{ matrix.os }}-${{ matrix.python }}\n"
        "          path: fm-profile.json\n"
        "          if-no-files-found: ignore\n"
    )


_PAGES_GRANT = """    permissions:
      contents: read
      pages: write
      id-token: write
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    concurrency:
      group: pages
      cancel-in-progress: false
"""

_PAGES_STEPS = """      - uses: actions/upload-pages-artifact@7b1f4a764d45c48632c6b24a0339c27f5614fb0b # v4.0.0
        with:
          path: site
      - id: deployment
        uses: actions/deploy-pages@d6db90164ac5ed86f2b6aed7e0febac5b3c0c03e # v4.0.5
"""


def _grants(job: Job, *, pages: bool) -> str:
    """GitHub's grants for *job*: organisation defaults are read-only."""
    if pages:
        return _PAGES_GRANT
    lines: list[str] = []
    if job.environment:
        # Trusted publishing: the id token is minted for the named
        # environment.
        lines.append("      id-token: write")
    if job.writes:
        lines.append("      contents: write")
    if not lines:
        return ""
    return "    permissions:\n" + "".join(line + "\n" for line in lines)


def _actions_job(
    answers: dict[str, Any],
    prog: str,
    point: Point,
    job: Job,
    *,
    forge: str,
    everything: tuple[Point, ...],
) -> str:
    """One job of *point* for GitHub or Gitea, from its declaration.

    A job of a point that shares its file with a point nobody
    inherits from runs on its own point's events alone; the gate's
    jobs carry no filter, since the merge point inherits them.
    """
    runners = [str(runner) for runner in answers.get("runners", ["ubuntu-latest"])]
    first = runners[0]
    pages = forge == "github" and job.deploy and answers.get("publish_seam") == "pages"
    lines = [_comment(job.note, "  "), f"  {job.name}:\n"]
    conditions = []
    if job.always:
        conditions.append("always()")
    shared = [
        other
        for other in _points_of(point.workflow, everything)
        if other.name != point.name
    ]
    if shared and not any(other.inherits == point.name for other in shared):
        conditions.append(_event_filter(point))
    if conditions:
        lines.append(f"    if: {' && '.join(conditions)}\n")
    needs = _needs(point, job, answers, everything)
    if needs:
        lines.append(f"    needs: [{', '.join(needs)}]\n")
    if forge == "github":
        lines.append(_grants(job, pages=pages))
        if job.environment:
            lines.append(f"    environment: {job.environment}\n")
    if job.matrix == "legs":
        pythons = _csv(list(answers.get("gate_pythons", ["3.11"])), quoted=True)
        lines.append(
            "    strategy:\n      fail-fast: false\n      matrix:\n"
            f"        os: [{_csv(runners)}]\n        python: [{pythons}]\n"
            "    runs-on: ${{ matrix.os }}\n"
        )
        cache = "${{ matrix.os }}-${{ matrix.python }}"
    elif job.matrix == "pythons":
        pythons = _csv(list(answers.get("python_versions", ["3.11"])), quoted=True)
        lines.append(
            "    strategy:\n      fail-fast: false\n      matrix:\n"
            f"        python: [{pythons}]\n    runs-on: {first}\n"
        )
        cache = f"{point.name}-${{{{ matrix.python }}}}"
    elif job.matrix == "wheels":
        lines.append(
            "    strategy:\n      fail-fast: false\n      matrix:\n"
            f"        os: [{_csv(_wheel_runners(answers))}]\n"
            "    runs-on: ${{ matrix.os }}\n"
        )
        cache = ""
    elif job.matrix == "declared":
        lines.append(
            "    strategy:\n      fail-fast: false\n      matrix:\n"
            f"        os: [{_csv(list(job.runners))}]\n"
            f"        python: [{_csv(list(job.pythons), quoted=True)}]\n"
            "    runs-on: ${{ matrix.os }}\n"
        )
        cache = "${{ matrix.os }}-${{ matrix.python }}"
    else:
        lines.append(f"    runs-on: {first}\n")
        cache = "docs" if job.docs_tools else ""
    lines.append("    steps:\n")
    lines.append(_checkout_step(point, job, forge=forge))
    if forge == "github":
        lines.append(_setup_uv_step(answers, cache_suffix=cache))
    lines.append(_collect_step(job, forge=forge))
    lines.append(_rung_step(answers))
    if job.docs_tools:
        lines.append(_docs_requirements_step(answers))
    lines.append(
        _enter_step(matrix_python=job.matrix in ("legs", "pythons", "declared"))
    )
    lines.append(_driver_step(prog, point, job, forge=forge))
    if forge == "github":
        lines.append(_deploy_key_step(job))
    lines.append(f"      - name: {job.step or job.name.capitalize()}\n")
    lines.append(_call_env(job, forge=forge))
    lines.append(_call_step(prog, point, job))
    lines.append(_publish_step(job, forge=forge))
    if job.profile:
        lines.append(_profile_step(forge))
    if pages:
        lines.append(_PAGES_STEPS)
    return "".join(lines)


def _actions_workflow(
    answers: dict[str, Any],
    prog: str,
    workflow: str,
    *,
    forge: str,
    everything: tuple[Point, ...] | None = None,
) -> str:
    """The workflow file *workflow* for GitHub or Gitea, from the points that share it.

    *everything* is the workspace's points, the builtin four when
    absent.
    """
    everything = everything if everything is not None else points(None)
    owners_all = _points_of(workflow, everything)
    lines = [f"name: {workflow.removesuffix('.yml')}\n\n"]
    for point in owners_all:
        lines.append(_comment(point.note))
    lines.append("on:\n")
    for event in EVENT_NAMES:
        owners = [point for point in owners_all if event in point.events]
        if not owners:
            continue
        if event == "pull_request":
            lines.append("  pull_request:\n")
        elif event == "push":
            lines.append("  push:\n    branches: [main]\n")
        elif event == "schedule":
            lines.append(f'  schedule:\n    - cron: "{owners[0].cron}"\n')
        else:
            inputs = owners[0].inputs
            if not inputs:
                lines.append("  workflow_dispatch:\n")
                continue
            lines.append("  workflow_dispatch:\n    inputs:\n")
            for item in inputs:
                lines.append(f"      {item.name}:\n")
                lines.append(f"        description: {item.description}\n")
                lines.append(
                    f"        required: {'true' if item.required else 'false'}\n"
                )
                if not item.required:
                    lines.append(f'        default: "{item.default}"\n')
    lines.append("\njobs:\n")
    for point in owners_all:
        for job in point.jobs:
            if _renders(job, answers):
                lines.append(
                    _actions_job(
                        answers, prog, point, job, forge=forge, everything=everything
                    )
                )
    return "".join(lines)


def _gitlab_rules(point: Point, everything: tuple[Point, ...]) -> str:
    """The rules admitting *point*'s runs, and those of the points inheriting it."""
    events: list[str] = list(point.events)
    for other in everything:
        if other.inherits == point.name:
            events.extend(event for event in other.events if event not in events)
    lines = ["  rules:\n", "    - if: $CI_COMMIT_TAG\n      when: never\n"]
    for event in events:
        if event == "pull_request":
            condition = '$CI_PIPELINE_SOURCE == "merge_request_event"'
        elif event == "push":
            condition = '$CI_COMMIT_BRANCH == "main" && $CI_PIPELINE_SOURCE == "push"'
        elif event == "schedule":
            condition = (
                '$CI_PIPELINE_SOURCE == "schedule"'
                f' && $FORGE_WORKFLOW == "{point.workflow}"'
            )
        else:
            condition = f'$FORGE_WORKFLOW == "{point.workflow}"'
        lines.append(f"    - if: '{condition}'\n")
    return "".join(lines)


def _gitlab_job(
    answers: dict[str, Any],
    prog: str,
    point: Point,
    job: Job,
    *,
    image: str,
    everything: tuple[Point, ...],
) -> str:
    """One job of *point* in the GitLab document, from its declaration.

    One executor, one image: a ``legs`` matrix is one leg on the first
    runner's label and the first gate Python, so its rows key the same
    leg the union expects; a ``pythons`` or ``wheels`` matrix is one
    job. The deploy is GitLab Pages' own: a job named ``pages``
    publishing ``public/`` is the seam. A job that pushes, or writes
    the store, rewrites origin with the push token first, since the
    job token cannot push.
    """
    tools = " ".join(str(t) for t in answers.get("docs_requirements", []))
    first = str(next(iter(answers.get("runners", ["ubuntu-latest"]))))
    stage = "release" if point.name in ("merge", "release") else "check"
    name = "pages" if job.deploy else job.name
    lines = [
        _comment(job.note),
        f"{name}:\n",
        f"  stage: {stage}\n",
        f"  image: {image}\n",
    ]
    needs = _needs(point, job, answers, everything)
    if needs:
        lines.append(f"  needs: [{', '.join(needs)}]\n")
    variables: list[str] = []
    if job.fetch in ("full", "tags"):
        variables.append('    GIT_DEPTH: "0"')
    elif job.fetch:
        variables.append(f'    GIT_DEPTH: "{job.fetch}"')
    if job.token == "admin":
        variables.append("    FORGE_ADMIN_TOKEN: $FORGE_ADMIN_TOKEN")
    if variables:
        lines.append("  variables:\n" + "".join(v + "\n" for v in variables))
    rules = _gitlab_rules(point, everything)
    if job.always:
        rules = rules.replace("    - if: '", "    - when: always\n      if: '")
    lines.append(rules)
    lines.append("  script:\n")
    if point.ref_input:
        # The pipeline runs on the base branch; the dispatch names the
        # commit to publish in a variable, and the wave reads it there.
        lines.append(f'    - git checkout --quiet "${point.ref_input}"\n')
    if job.pushes:
        lines.append("    - git fetch --tags\n")
    if job.pushes or job.writes:
        # The job token cannot push: a store write, a receipt tag and
        # the template artifact all ride the push token.
        lines.append(
            '    - git remote set-url origin "https://oauth2:${GITLAB_PUSH_TOKEN}'
            '@${CI_SERVER_HOST}/${CI_PROJECT_PATH}.git"\n'
        )
    if job.docs_tools and tools:
        lines.append(f"    - apt-get update -q && apt-get install -y -q {tools}\n")
    lines.append("    - source setup.sh\n")
    driver = _driver_step(prog, point, job, forge="gitlab")
    if driver:
        lines.append(f"    - {driver.split('run: ', 1)[1]}")
    call = f"{prog} ci.run --point={point.name} --job={job.name}"
    if job.matrix == "legs":
        python = str(next(iter(answers.get("gate_pythons", ["3.11"]))))
        call += f' --os="{first}" --python="{python}"'
    elif job.matrix == "declared":
        call += f' --os="{job.runners[0]}" --python="{job.pythons[0]}"'
    elif job.matrix == "pythons":
        python = str(next(iter(answers.get("python_versions", ["3.11"]))))
        call += f' --python="{python}"'
    lines.append(f"    - {call}\n")
    if job.deploy:
        lines.append("    - mv site public\n  artifacts:\n    paths: [public]\n")
    elif job.publishes:
        lines.append('  artifacts:\n    paths: ["packages/*/dist/*"]\n')
    return "".join(lines) + "\n"


def _gitlab_document(
    answers: dict[str, Any], prog: str, *, everything: tuple[Point, ...] | None = None
) -> str:
    """The one GitLab document: every point's jobs, the builtin four by default.

    The merge point's dispatch job starts the wave through the API at
    the base branch with the squash in the ``ref`` variable, which the
    document routes on ``FORGE_WORKFLOW`` like a hand dispatch.
    """
    python = str(next(iter(answers.get("python_versions", ["3.11"]))))
    image = _gitlab_image(answers, python)
    lines = [
        """# The gate, the merge point, the nightly and the train, GitLab-shaped,
# generated by the workshop. One pipeline definition: workflow rules
# admit FORGE_WORKFLOW-routed pipelines (a dispatch, the clock), merge
# requests and main, and every pipeline is named after its workflow
# file, so a run reads on every forge the same way and a dispatched
# gate is told from a dispatched wave. Every job sources the entry
# script in its own shell, then calls the runner bare. The clock
# itself is a pipeline schedule, a project setting the governance
# reconcile creates, never a line here. GitLab CI variables arrive as
# process environment, the cascade's highest rung already: declare
# PYTHON_PUBLISH_INDEX, PYTHON_REGISTRY_URL, FORGE_TOKEN,
# UV_PUBLISH_TOKEN and GITLAB_PUSH_TOKEN (a project access token with
# write_repository: the job token cannot push, and the state store,
# the receipt tags and the template artifact are pushes) as CI
# variables, masked where their values allow it.
workflow:
  name: $FORGE_WORKFLOW
  rules:
    - if: $FORGE_WORKFLOW
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
      variables:
        FORGE_WORKFLOW: ci.yml
    - if: $CI_COMMIT_BRANCH == "main"
      variables:
        FORGE_WORKFLOW: ci.yml

stages: [check, release]

"""
    ]
    everything = everything if everything is not None else points(None)
    for point in everything:
        for job in point.jobs:
            if _renders(job, answers):
                lines.append(
                    _gitlab_job(
                        answers, prog, point, job, image=image, everything=everything
                    )
                )
    return "".join(lines)


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
    from livery.workshop._docs import zensical_config
    from livery.workshop._entry import entry_script
    from livery.workshop._provenance import generated_header

    prog = footman.prog()
    facts = _facts(root)
    kind = str(facts["forge_kind"])
    header = generated_header("#")
    site = {"zensical.toml": zensical_config(root)}
    everything = points(root)
    if kind in ("github", "gitea"):
        # One file per workflow: the gate and the merge point share
        # ci.yml, the nightly, the release and every contributed point
        # have their own.
        workflows = sorted({point.workflow for point in everything})
        files = {
            f".{kind}/workflows/{workflow}": _actions_workflow(
                facts, prog, workflow, forge=kind, everything=everything
            )
            for workflow in workflows
        }
    else:
        files = {".gitlab-ci.yml": _gitlab_document(facts, prog, everything=everything)}
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


#: Generated files an earlier emission wrote under other names and
#: this one folds away: the apply deletes them where present.
RETIRED = (
    ".gitea/workflows/governance.yml",
    ".gitea/workflows/docs.yml",
    ".github/workflows/governance.yml",
    ".github/workflows/docs.yml",
    ".github/workflows/release-legs.yml",
)


#: What every generated file opens with, whatever runner wrote it.
GENERATED_MARK = "# Generated by the workshop from workshop.toml"


def retired_files(root: Path) -> tuple[Path, ...]:
    """The generated workflow files under *root* this emission does not own, to delete.

    `RETIRED` names the ones earlier emissions wrote under other names.
    Beyond those, every file in the forge's workflow directory that
    opens with `GENERATED_MARK` and is not in this emission is retired
    too: the workflow of a point a package contributed and that went
    with the package. A file without the mark is a person's and is
    never touched.
    """
    found = [root / relative for relative in RETIRED if (root / relative).is_file()]
    owned = set(generate(root))
    for directory in (".github/workflows", ".gitea/workflows"):
        folder = root / directory
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.yml")):
            relative = path.relative_to(root).as_posix()
            if relative in owned or relative in RETIRED:
                continue
            try:
                head = path.read_text("utf-8", errors="replace")[: len(GENERATED_MARK)]
            except OSError:
                continue
            if head == GENERATED_MARK:
                found.append(path)
    return tuple(found)
