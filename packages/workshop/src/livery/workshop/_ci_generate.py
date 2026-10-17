"""The CI workflow renderers: the declared points, in each forge's words.

Workflow files are mechanical forge knowledge, emitted here from the
workspace contract (``workshop.toml``) and the derived Python matrix,
written as managed generated artifacts, and the drift check compares
the committed files against these same pure functions, offline.
Change an emitter, run ``fm sync``, and every kind's files move
together.

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

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import livery.footman as footman
from livery.workshop._contract import load_contract
from livery.workshop._points import (
    EVENT_NAMES,
    Job,
    Point,
    emitted_points,
    job_installs,
    job_runners,
    job_seams,
    points,
    pull_request_runners,
    shard_counts,
)
from livery.workshop._pythons import gate_pythons, python_matrix
from livery.workshop._shard import Shard

if TYPE_CHECKING:
    from livery.workshop._composition import Lookup


def _pinned(repo: str, commit: str, version: str) -> str:
    """One ``uses:`` value: *repo* at *commit*, with the *version* the commit is."""
    return f"{repo}@{commit} # {version}"


#: The actions the emitted jobs use, by forge, each pinned to a commit
#: with its version beside it. Keyed by forge because each forge's
#: runners prove their own majors: this repository's CI proves
#: GitHub's, the loop proves Gitea's on the v4 line. A bump moves with
#: its proof. The pin table and nothing else says which commit a job
#: runs, so a rendered ``uses:`` never names a moving tag.
ACTIONS: dict[str, dict[str, str]] = {
    "github": {
        "checkout": _pinned(
            "actions/checkout", "3d3c42e5aac5ba805825da76410c181273ba90b1", "v7.0.1"
        ),
        "upload": _pinned(
            "actions/upload-artifact",
            "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
            "v7.0.1",
        ),
        "download": _pinned(
            "actions/download-artifact",
            "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
            "v8.0.1",
        ),
        "setup-uv": _pinned(
            "astral-sh/setup-uv", "20cfd1bf945f4377ade1205e4dbc17946fc9a30d", "v10.0.1"
        ),
        # The cache action the jobs restore and save through: the tool
        # store, uv's cache and conan's home, each keyed by its lock
        # with a prefix fallback.
        "cache": _pinned(
            "actions/cache", "0057852bfaa89a56745cba8c7296529d2fc39830", "v4.3.0"
        ),
        "upload-pages": _pinned(
            "actions/upload-pages-artifact",
            "7b1f4a764d45c48632c6b24a0339c27f5614fb0b",
            "v4.0.0",
        ),
        "deploy-pages": _pinned(
            "actions/deploy-pages", "d6db90164ac5ed86f2b6aed7e0febac5b3c0c03e", "v4.0.5"
        ),
    },
    "gitea": {
        "checkout": _pinned(
            "actions/checkout", "11d5960a326750d5838078e36cf38b85af677262", "v4.4.0"
        ),
        "upload": _pinned(
            "actions/upload-artifact",
            "ea165f8d65b6e75b540449e92b4886f43607fa02",
            "v4.6.2",
        ),
        "download": _pinned(
            "actions/download-artifact",
            "d3f86a106a0bac45b974a628896c90dbdf5c8093",
            "v4.3.0",
        ),
    },
}
#: The tool store's home on a GitHub job: the data directory the entry
#: places under the runner's temp, the working drive, and the store as
#: its `toolroom`. Literal here because the step runs before any verb
#: can: the venv it would need is what the entry step makes.
STORE_HOME = "${{ runner.temp }}/footman/toolroom"
#: Where a GitHub job keeps conan's home: the working drive, the job's
#: own, the same path the entry and
#: `livery.workshop._env_tasks.runner_placements` export, so the cache
#: step and every conan the job runs agree on one directory.
CONAN_HOME = "${{ runner.temp }}/conan"


def _facts(root: Path, everything: tuple[Point, ...] | None = None) -> dict[str, Any]:
    """What the emitters read: the contract's CI facts, matrix derived.

    Runners, the shards per runner, the required context, and the
    forge kind come from ``workshop.toml``; the Python matrix from the root
    ``pyproject.toml``'s floor and the workshop's newest supported
    minor; the uv pin from the lock; what a job installs, where it
    deploys and the runners it fans out to from the functions the job
    names, over *everything*, the points the files render, *root*'s
    when absent; what the listed extensions add to the checkout step
    from their answers to the ``checkout-inputs`` query. None of it is
    the project's identity, which the project's answers hold alone.
    """
    from livery.workshop._entry import locked_uv_version
    from livery.workshop._envfile import parse_env_file
    from livery.workshop._queries import CHECKOUT_INPUTS, answer

    contract = load_contract(root / "workshop.toml")
    ci = contract.get("ci") or {}
    runners = [str(runner) for runner in ci.get("runners") or ["ubuntu-latest"]]
    narrowed = pull_request_runners(ci)
    shards, pull_request_shards = _shard_facts(ci, runners, narrowed)
    return {
        "forge_kind": str((contract.get("forge") or {}).get("kind", "github")),
        # What the listed extensions add to the checkout step, such as
        # fetching the objects a git filter keeps outside the repository.
        "checkout_inputs": dict(answer(root, CHECKOUT_INPUTS)),
        "runners": runners,
        "pull_request_runners": narrowed,
        # The shards per runner label, one for a runner the tables do
        # not name: every event's over every runner, and a pull
        # request's over its runners.
        "shards": shards,
        "pull_request_shards": pull_request_shards,
        "required_context": str(ci.get("required-context", "gate")),
        "python_versions": python_matrix(root),
        "gate_pythons": gate_pythons(root),
        # The outer uv, the one tool that runs before the lock can
        # speak: pinned to the lock's own uv, so the bootstrap is not
        # the one unpinned link. Empty without a lock, and the
        # bootstrap then stays unpinned rather than inventing a
        # version.
        "uv_pin": locked_uv_version(root),
        # The system packages each job installs before it enters, the
        # seam each deploying job publishes through, and the runner
        # labels each runners job fans out to, by point and job: the
        # declaration of a job knows why.
        "installs": job_installs(root, everything),
        "seams": job_seams(root, everything),
        "job_runners": job_runners(root, everything),
        # The committed .repo.env's keys: the offline, deterministic
        # list of which secrets the entry step may carry into a job.
        "env_keys": sorted(parse_env_file(root / ".repo.env")),
    }


def _secret_lines(answers: dict[str, Any]) -> list[str]:
    """The entry step's environment lines: each declared key's secret, prefixed.

    Each key the committed ``.repo.env`` declares may arrive as a CI
    secret; the entry step carries it as ``RUNG_<KEY>``, and the
    emission the entry runs writes the non-empty ones into a
    runner-local file the cascade reads as the shared slot
    (``WORKSHOP_SHARED_ENV_FILE``). Non-empty values only, so an
    absent secret cannot mask a committed value, and a fork PR with
    no secrets behaves like a machine without a shared file. Never
    the whole secrets store: ``toJSON(secrets)`` would hand every job
    and every third-party action the lot. Empty when the workspace
    declares no keys.
    """
    from livery.workshop._envfile import RUNG_PREFIX

    keys = [str(key) for key in answers.get("env_keys", [])]
    return [f"{RUNG_PREFIX}{key}: ${{{{ secrets.{key} }}}}" for key in keys]


def _setup_uv_step(answers: dict[str, Any]) -> str:
    """The setup-uv step, uv pinned to the lock's own version, its cache off.

    Nothing of uv's is cached: the only wheels the sync builds from
    source are the workspace's own, rebuilt on every commit, and the
    wheels it downloads arrive faster than an archive of them restores.
    The cache is still placed on the runner's working drive by the
    entry, so the sync links wheels into the venv instead of copying
    across drives.
    """
    pin = str(answers.get("uv_pin", ""))
    lines = [f"      - uses: {ACTIONS['github']['setup-uv']}", "        with:"]
    if pin:
        lines.append(f'          version: "{pin}"')
    lines.append("          enable-cache: false")
    return "\n".join(lines) + "\n"


def _store_cache_step() -> str:
    """The tool store restored before the entry materialises it.

    Keyed by the tools lock, the OS and the architecture, with the
    prefix as the restore key, so a changed lock restores everything
    unchanged and the store lands only the digests it lacks. A
    restored folder is a tier the store verifies on access: an old or
    torn archive costs a refetch, never a fault. `ci.run` sweeps the
    store before the post-job save, so the archive carries the trees
    the tools run from and not the artifacts they were extracted from.
    """
    return (
        f"      - uses: {ACTIONS['github']['cache']}\n"
        "        with:\n"
        f"          path: {STORE_HOME}\n"
        "          key: tools-${{ runner.os }}-${{ runner.arch }}"
        "-${{ hashFiles('toolroom.lock') }}\n"
        "          restore-keys: tools-${{ runner.os }}-${{ runner.arch }}-\n"
    )


def _conan_cache_step() -> str:
    """Conan's home restored around a job that builds native packages.

    Keyed by the recipes, the OS and the architecture, with the
    prefix as the restore key: a third-party package from Conan
    Center is built from source once per key and downloaded from the
    cache afterwards. The recipes are the key because they carry the
    requirements; a range that resolves to a newer version inside an
    unchanged recipe reuses the entry and builds that one package.
    The cache is the speed extension and Conan Center the origin, so a
    miss costs time, never a red leg.
    """
    # A forge's glob stops at a slash: a package in a group directory
    # is one level deeper.
    recipes = "packages/*/conanfile.py', 'packages/*/*/conanfile.py"
    return (
        f"      - uses: {ACTIONS['github']['cache']}\n"
        "        with:\n"
        f"          path: {CONAN_HOME}\n"
        "          key: conan-${{ runner.os }}-${{ runner.arch }}"
        f"-${{{{ hashFiles('{recipes}') }}}}\n"
        "          restore-keys: conan-${{ runner.os }}-${{ runner.arch }}-\n"
    )


def _installs(
    answers: dict[str, Any],
    point: Point,
    job: Job,
    joins: Sequence[tuple[Point, Job]] = (),
) -> tuple[str, ...]:
    """The system packages *job* of *point* installs before it enters, *joins* included."""
    found = set(answers.get("installs", {}).get((point.name, job.name), ()))
    for other, joined in joins:
        found.update(answers.get("installs", {}).get((other.name, joined.name), ()))
    return tuple(sorted(found))


def _joins_of(
    point: Point, job: Job, everything: tuple[Point, ...]
) -> list[tuple[Point, Job]]:
    """The joins of *job* of *point*: on the point itself and on the points inheriting it.

    Each with the point it was contributed to, which conditions the
    plumbing it adds: a join on the merge point of a job the gate
    point declares renders on the gate point's job, on a push alone.
    """
    return [
        (other, joined)
        for other in everything
        if other.name == point.name or other.inherits == point.name
        for joined in other.jobs
        if joined.joins and joined.name == job.name
    ]


def _condition(point: Point, other: Point) -> str:
    """The event condition a step *other* adds to a job of *point* carries; none on its own point."""
    return "" if other.name == point.name else _event_filter(other)


def _if_line(condition: str) -> str:
    return f"        if: {condition}\n" if condition else ""


def _artifact_root(everything: tuple[Point, ...], name: str) -> str:
    """The directory a collected artifact *name* unpacks into: its publisher's paths' root."""
    for point in everything:
        for job in point.jobs:
            if job.publishes == name and job.paths:
                return job.paths[0].split("/", 1)[0]
    return "."


def _seam(answers: dict[str, Any], point: Point, job: Job) -> str:
    """The seam *job* of *point* publishes through; ``""`` when it deploys nothing."""
    return str(answers.get("seams", {}).get((point.name, job.name), ""))


def _installs_step(packages: tuple[str, ...]) -> str:
    """The step installing *packages* on a GitHub- or Gitea-shaped job; empty for none."""
    if not packages:
        return ""
    return (
        "      - name: System packages\n"
        "        run: sudo apt-get update -q && sudo apt-get install"
        f" -y -q {' '.join(packages)}\n"
    )


DRIVER_DIST = "livery-workshop"
"""The distribution a re-dispatched wave may pin as its driver."""


def _enter_step(answers: dict[str, Any], *, matrix_python: bool = False) -> str:
    """The entry step: ``setup.sh github`` persists the emission.

    Every step after it calls the runner and the venv tools bare,
    from the persisted PATH. ``UV_PYTHON`` selects the matrix leg's
    interpreter for the entry sync where a matrix exists, and the
    declared keys' secrets ride the step's environment as
    ``RUNG_<KEY>`` for the emission to materialise into the shared
    slot.
    """
    lines: list[str] = []
    if matrix_python:
        lines.append("UV_PYTHON: ${{ matrix.python }}")
    lines.extend(_secret_lines(answers))
    env = "        env:\n" + "".join(f"          {line}\n" for line in lines)
    return (
        "      - name: Enter the workspace\n"
        + (env if lines else "")
        + "        run: bash setup.sh github\n"
    )


def _csv(values: list[Any], *, quoted: bool = False) -> str:
    return ", ".join(f'"{v}"' if quoted else str(v) for v in values)


def _labels(answers: dict[str, Any], point: Point, job: Job) -> list[str]:
    """The runner labels *job* of *point* fans out to; empty when its function names none."""
    found = answers.get("job_runners", {}).get((point.name, job.name), ())
    return [str(label) for label in found]


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


def _renders(point: Point, job: Job, answers: dict[str, Any]) -> bool:
    """Whether *job* of *point* exists for this workspace.

    A ``runners`` job exists when its function names a runner label
    for this workspace; when it names none, the job does not render
    and `_needs` drops a need on it. Every other job always exists.
    """
    return job.matrix != "runners" or bool(_labels(answers, point, job))


def _points_of(workflow: str, everything: tuple[Point, ...]) -> tuple[Point, ...]:
    """The points among *everything* whose shell *workflow* is, in order."""
    return tuple(point for point in everything if point.workflow == workflow)


def _needs(
    point: Point, job: Job, answers: dict[str, Any], everything: tuple[Point, ...]
) -> list[str]:
    """The jobs *job* waits for that render for this workspace."""
    owners = [other for other in everything if other.name == point.inherits]
    rendered = {
        other.name
        for owner in (*owners, point)
        for other in owner.jobs
        if _renders(owner, other, answers)
    }
    return [need for need in job.needs if need in rendered]


def _event_filter(point: Point) -> str:
    """The Actions condition admitting *point*'s events alone."""
    return " || ".join(f"github.event_name == '{event}'" for event in point.events)


def _shard_facts(
    ci: dict[str, Any], runners: Sequence[str], narrowed: Sequence[str]
) -> tuple[dict[str, int], dict[str, int]]:
    """The shards per runner label: every event's, and a pull request's.

    Read as [livery.workshop.check_shards][] and
    [livery.workshop.pull_request_shards][] read them, so the matrix
    and the leg labels agree: ``[ci] shards`` over every runner, one
    for a runner it does not name; ``[ci] pull-request-shards`` over
    the pull request's runners, *narrowed*, or every event's counts for
    those runners while that key is absent. A count outside the
    contract refuses here, through `shard_counts`, before a line
    renders.
    """
    shards = shard_counts(ci, key="shards", runners=runners, listed="[ci] runners")
    if ci.get("pull-request-shards") is None:
        return shards, {runner: shards[runner] for runner in narrowed}
    listed = (
        "[ci] pull-request-runners"
        if ci.get("pull-request-runners") is not None
        else "[ci] runners"
    )
    pull = shard_counts(ci, key="pull-request-shards", runners=narrowed, listed=listed)
    return shards, pull


def _runners(answers: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Every event's runner labels, and a pull request's."""
    runners = [str(runner) for runner in answers.get("runners", ["ubuntu-latest"])]
    narrowed = [str(runner) for runner in answers.get("pull_request_runners", runners)]
    return runners, narrowed


def _shards(
    answers: dict[str, Any], key: str, runners: Sequence[str]
) -> dict[str, int]:
    """The shards per runner the fact *key* holds, one for a runner it does not name."""
    counts: Mapping[str, Any] = answers.get(key) or {}
    return {runner: int(counts.get(runner, 1)) for runner in runners}


def _split(counts: Mapping[str, int]) -> bool:
    """Whether any runner in *counts* runs as more than one shard."""
    return any(count > 1 for count in counts.values())


def _carries_shards(answers: dict[str, Any]) -> bool:
    """Whether the check matrix is ``include`` entries carrying shards.

    It is when any runner runs as more than one shard on any event: the
    entries of a split runner each name their shard, on the check job
    or in both of the legs job's literals, and the one call passes
    ``matrix.shard`` on every event, empty on a runner with one shard.
    """
    runners, narrowed = _runners(answers)
    return _split(_shards(answers, "shards", runners)) or _split(
        _shards(answers, "pull_request_shards", narrowed)
    )


def _narrows(answers: dict[str, Any]) -> bool:
    """Whether a pull request's check legs differ from every other event's.

    They do when the pull request runs on fewer runners, or when a
    runner it runs on runs as another number of shards.
    """
    runners, narrowed = _runners(answers)
    if narrowed != runners:
        return True
    shards = _shards(answers, "shards", runners)
    pull = _shards(answers, "pull_request_shards", narrowed)
    return pull != {runner: shards[runner] for runner in narrowed}


def _entries(
    runners: Sequence[str], pythons: Sequence[str], counts: Mapping[str, int]
) -> list[tuple[str, str, str]]:
    """One ``(os, python, shard)`` per matrix entry, in that order.

    A runner with a count of one has one entry per Python with an empty
    shard: the forge then names its job as the unsplit leg, ``check
    (ubuntu-latest, 3.14)``, the name the runner gives it and keys its
    rows by, and the call's shard reaches the runner empty, which it
    reads as one shard of one.
    """
    entries: list[tuple[str, str, str]] = []
    for runner in runners:
        count = counts.get(runner, 1)
        for python in pythons:
            if count == 1:
                entries.append((runner, python, ""))
                continue
            for index in range(1, count + 1):
                entries.append((runner, python, str(Shard(index, count))))
    return entries


def _literal(
    runners: Sequence[str],
    pythons: Sequence[str],
    counts: Mapping[str, int],
    *,
    sharded: bool,
) -> str:
    """One matrix as the JSON literal the legs job holds.

    The ``include`` shape when *sharded*, each entry of a split runner
    with its shard and an entry of a runner with one shard without the
    key; the ``os`` and ``python`` lists otherwise.
    """
    matrix: dict[str, Any] = (
        {
            "include": [
                {
                    "os": os_label,
                    "python": python,
                    **({"shard": shard} if shard else {}),
                }
                for os_label, python, shard in _entries(runners, pythons, counts)
            ]
        }
        if sharded
        else {"os": list(runners), "python": list(pythons)}
    )
    return json.dumps(matrix, separators=(",", ":"))


def _legs_job(answers: dict[str, Any]) -> str:
    """The job that picks the check legs' matrix by the event, as its output.

    GitHub starts no run for a workflow whose matrix axis is an
    expression, so the two literal matrices sit in this job's
    environment, the event picks one, and the check job reads it with
    ``fromJSON`` from the output: the one documented shape for a matrix
    decided when the run starts. The decision is still the contract's;
    the file carries two literals and which event takes which: a pull
    request takes the narrowed one unless it carries
    [livery.workshop.EVERY_RUNNER_LABEL][], and every other event takes
    the full one. Both literals are ``include`` entries, each naming
    its shard, when any runner runs as more than one shard on either
    event, and the ``os`` and ``python`` lists otherwise. The job checks
    nothing out and enters nothing, so it costs seconds.
    """
    from livery.workshop._points import EVERY_RUNNER_LABEL

    runners, narrowed = _runners(answers)
    pythons = [str(version) for version in answers.get("gate_pythons", ["3.11"])]
    sharded = _carries_shards(answers)
    full = _literal(
        runners, pythons, _shards(answers, "shards", runners), sharded=sharded
    )
    pull = _literal(
        narrowed,
        pythons,
        _shards(answers, "pull_request_shards", narrowed),
        sharded=sharded,
    )
    narrowed_event = (
        "github.event_name == 'pull_request' && !contains("
        f"github.event.pull_request.labels.*.name, '{EVERY_RUNNER_LABEL}')"
    )
    return (
        _comment(
            "The check legs' matrix, two literals: a pull request's runners"
            f" and every runner. A pull request without the {EVERY_RUNNER_LABEL}"
            " label takes the first, every other run the second, and the"
            " check job reads the pick from this job's output.",
            "  ",
        )
        + "  legs:\n"
        f"    runs-on: {runners[0]}\n"
        "    outputs:\n"
        "      matrix: ${{ steps.legs.outputs.matrix }}\n"
        "    steps:\n"
        "      - id: legs\n"
        "        env:\n"
        f"          LEGS: ${{{{ {narrowed_event} && '{pull}' || '{full}' }}}}\n"
        '        run: echo "matrix=$LEGS" >> "$GITHUB_OUTPUT"\n'
    )


def _call_step(prog: str, point: Point, job: Job, *, shard: bool = False) -> str:
    """The one call: ``ci.run`` for the point and job, the matrix facts passed.

    *shard* passes the entry's shard too, on a ``legs`` matrix whose
    entries carry one.
    """
    call = f"{prog} ci.run --point={point.name} --job={job.name}"
    if job.matrix in ("legs", "declared"):
        passed = '          --shard="${{ matrix.shard }}"\n' if shard else ""
        return (
            "        run: >-\n"
            f"          {call}\n"
            '          --os="${{ matrix.os }}" --python="${{ matrix.python }}"\n'
            + passed
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
    if not lines:
        return ""
    return "        env:\n" + "".join(f"          {line}\n" for line in lines)


#: The inputs the checkout step sets from the job's own declaration,
#: which no answer to the ``checkout-inputs`` query may name.
CHECKOUT_OWN = ("ref", "fetch-depth", "fetch-tags", "token")


def checkout_inputs(answers: Mapping[str, Any], lookup: Lookup) -> dict[str, str]:
    """Every input the answers add to the checkout step, each name to its value.

    The rule of the ``checkout-inputs`` query, which the base's
    contract file names. Each answer maps an input of the checkout
    action to its value, written into the workflow as it stands.

    Raises:
        QueryError: when an answer names an input the step sets from the
            job's declaration (``CHECKOUT_OWN``) or gives a value that is
            not a string, naming the extension, or when two answers give
            one input different values, naming both.
    """
    from livery.workshop._queries import QueryError, merged

    for name, value in answers.items():
        for key, held in cast("Mapping[str, object]", value).items():
            if key in CHECKOUT_OWN:
                raise QueryError(
                    f"{name} answers {key}, which the checkout step sets from"
                    " the job's declaration; answer another input"
                )
            if not isinstance(held, str):
                raise QueryError(
                    f"{name} answers {key} with {held!r}, and an input's value"
                    " is a string, as the workflow writes it; quote it"
                )
    return dict(merged(answers, lookup))


def _checkout_step(
    point: Point, job: Job, *, forge: str, inputs: Mapping[str, str] | None = None
) -> str:
    """The checkout: as deep as the verbs need, at the dispatched ref, able to push.

    *inputs* are what the listed extensions add to it, each written
    before the inputs the job's declaration sets.
    """
    action = ACTIONS[forge]["checkout"]
    with_lines = [
        f"          {name}: {value}" for name, value in (inputs or {}).items()
    ]
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


def _collect_step(
    job: Job, *, forge: str, everything: tuple[Point, ...], condition: str = ""
) -> str:
    """The artifact *job* collects, every leg's merged into its publisher's root.

    *condition* is the event condition of the point that added the
    collect to a job of another point, empty on the job's own. A
    collect added that way never stops the job: the artifact is
    missing when its publisher was red, and the job's own entries,
    the verdict among them, still judge the run and say so.
    """
    if not job.collects:
        return ""
    action = ACTIONS[forge]["download"]
    tolerant = "        continue-on-error: true\n" if condition else ""
    return (
        f"      - uses: {action}\n" + _if_line(condition) + tolerant + "        with:\n"
        f"          pattern: {job.collects}*\n"
        f"          path: {_artifact_root(everything, job.collects)}\n"
        "          merge-multiple: true\n"
    )


def _publish_step(
    job: Job, *, forge: str, matrix: bool = False, condition: str = ""
) -> str:
    """The artifact *job* publishes, its paths as declared, the leg's name on a *matrix*.

    *condition* is the event condition of the point that added the
    publish to a job of another point, empty on the job's own.
    """
    if not job.publishes:
        return ""
    action = ACTIONS[forge]["upload"]
    name = job.publishes + ("-${{ matrix.os }}" if matrix else "")
    return (
        f"      - uses: {action}\n" + _if_line(condition) + "        with:\n"
        f"          name: {name}\n"
        "          path: |\n"
        + "".join(f"            {path}\n" for path in job.paths)
        + "          if-no-files-found: ignore\n"
    )


def _driver_step(prog: str, point: Point, *, forge: str) -> str:
    """The driver pin, through its verb: the released workshop the input names.

    Every job of a point that takes a ``workshop`` input pins it before
    its one call, so one workshop drives the whole run; a job of any
    other point pins nothing. The verb decides for itself on an empty
    input, so the shell carries no condition.
    """
    if not any(item.name == "workshop" for item in point.inputs):
        return ""
    value = "${{ inputs.workshop }}" if forge != "gitlab" else "$workshop"
    return (
        "      - name: Pin the driver\n"
        f'        run: {prog} release.driver --workshop="{value}"\n'
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

_PAGES_UPLOAD = f"      - uses: {ACTIONS['github']['upload-pages']}\n"

_PAGES_DEPLOY = f"""      - id: deployment
        uses: {ACTIONS["github"]["deploy-pages"]}
"""


def _pages_upload_step(condition: str = "") -> str:
    """The upload of the built site as the pages artifact, under *condition*."""
    return _PAGES_UPLOAD + _if_line(condition) + "        with:\n          path: site\n"


def _pages_job(answers: dict[str, Any], point: Point, job: Job, other: Point) -> str:
    """The forge's own pages deploy of the site *job* of *point* uploaded, for *other*.

    No checkout and no entry: the one step deploys the artifact the
    joined job uploaded, in the pages environment, on *other*'s events.
    """
    first = str(next(iter(answers.get("runners", ["ubuntu-latest"]))))
    condition = _condition(point, other)
    return (
        _comment(
            f"The forge's pages deploy of the site the {job.name} job uploaded:"
            " no checkout and no entry, the pages environment alone.",
            "  ",
        )
        + "  pages:\n"
        + (f"    if: {condition}\n" if condition else "")
        + f"    needs: [{job.name}]\n"
        + _PAGES_GRANT
        + f"    runs-on: {first}\n    steps:\n"
        + _PAGES_DEPLOY
    )


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
    if job.dispatches:
        # Starting a workflow through the API: the workflow token
        # answers 403 ("Resource not accessible by integration")
        # without it, and the wave never starts.
        lines.append("      actions: write")
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
    pages = forge == "github" and _seam(answers, point, job) == "pages"
    joins = _joins_of(point, job, everything)
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
    if job.matrix == "legs" and _narrows(answers):
        needs = ["legs", *needs]
    if needs:
        lines.append(f"    needs: [{', '.join(needs)}]\n")
    if forge == "github":
        lines.append(_grants(job, pages=pages))
        if job.environment:
            lines.append(f"    environment: {job.environment}\n")
    if job.matrix == "legs":
        gate = [str(version) for version in answers.get("gate_pythons", ["3.11"])]
        if _narrows(answers):
            lines.append(
                "    strategy:\n      fail-fast: false\n"
                "      matrix: ${{ fromJSON(needs.legs.outputs.matrix) }}\n"
                "    runs-on: ${{ matrix.os }}\n"
            )
        elif _carries_shards(answers):
            # One entry per os, python and shard: the forge names each
            # job by the entry's values in order, ``check (windows-latest,
            # 3.14, 2of4)``, so the job carries no name of its own, and
            # a runner with one shard has no shard key, so its job is
            # named as the unsplit leg the runner keys its rows by.
            lines.append("    strategy:\n      fail-fast: false\n      matrix:\n")
            lines.append("        include:\n")
            counts = _shards(answers, "shards", runners)
            for os_label, python, shard in _entries(runners, gate, counts):
                tail = f", shard: {shard}" if shard else ""
                lines.append(
                    f'          - {{os: {os_label}, python: "{python}"{tail}}}\n'
                )
            lines.append("    runs-on: ${{ matrix.os }}\n")
        else:
            lines.append(
                "    strategy:\n      fail-fast: false\n      matrix:\n"
                f"        os: [{_csv(runners)}]\n"
                f"        python: [{_csv(gate, quoted=True)}]\n"
                "    runs-on: ${{ matrix.os }}\n"
            )
    elif job.matrix == "pythons":
        pythons = _csv(list(answers.get("python_versions", ["3.11"])), quoted=True)
        lines.append(
            "    strategy:\n      fail-fast: false\n      matrix:\n"
            f"        python: [{pythons}]\n    runs-on: {first}\n"
        )
    elif job.matrix == "runners":
        lines.append(
            "    strategy:\n      fail-fast: false\n      matrix:\n"
            f"        os: [{_csv(_labels(answers, point, job))}]\n"
            "    runs-on: ${{ matrix.os }}\n"
        )
    elif job.matrix == "declared":
        lines.append(
            "    strategy:\n      fail-fast: false\n      matrix:\n"
            f"        os: [{_csv(list(job.runners))}]\n"
            f"        python: [{_csv(list(job.pythons), quoted=True)}]\n"
            "    runs-on: ${{ matrix.os }}\n"
        )
    else:
        lines.append(f"    runs-on: {first}\n")
    lines.append("    steps:\n")
    lines.append(
        _checkout_step(point, job, forge=forge, inputs=answers.get("checkout_inputs"))
    )
    if forge == "github":
        lines.append(_setup_uv_step(answers))
    lines.append(_collect_step(job, forge=forge, everything=everything))
    for other, joined in joins:
        lines.append(
            _collect_step(
                joined,
                forge=forge,
                everything=everything,
                condition=_condition(point, other),
            )
        )
    lines.append(_installs_step(_installs(answers, point, job, joins)))
    if forge == "github":
        lines.append(_store_cache_step())
        if job.conan_cache:
            lines.append(_conan_cache_step())
    lines.append(
        _enter_step(
            answers, matrix_python=job.matrix in ("legs", "pythons", "declared")
        )
    )
    lines.append(_driver_step(prog, point, forge=forge))
    lines.append(f"      - name: {job.step or job.name.capitalize()}\n")
    lines.append(_call_env(job, forge=forge))
    lines.append(
        _call_step(
            prog,
            point,
            job,
            shard=job.matrix == "legs" and _carries_shards(answers),
        )
    )
    lines.append(_publish_step(job, forge=forge, matrix=bool(job.matrix)))
    for other, joined in joins:
        condition = _condition(point, other)
        lines.append(
            _publish_step(
                joined, forge=forge, matrix=bool(job.matrix), condition=condition
            )
        )
        if forge == "github" and _seam(answers, other, joined) == "pages":
            lines.append(_pages_upload_step(condition))
    if pages:
        lines.append(_pages_upload_step() + _PAGES_DEPLOY)
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
            # The default types plus labeled: a label a verb or a person
            # sets starts a run, whose legs job reads it.
            lines.append(
                "  pull_request:\n    types: [opened, synchronize, reopened, labeled]\n"
            )
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
            if job.joins:
                continue
            if job.matrix == "legs" and _narrows(answers):
                lines.append(_legs_job(answers))
            if _renders(point, job, answers):
                lines.append(
                    _actions_job(
                        answers, prog, point, job, forge=forge, everything=everything
                    )
                )
    if forge == "github":
        for point in owners_all:
            for job in point.jobs:
                for other, joined in _joins_of(point, job, everything):
                    if not job.joins and _seam(answers, other, joined) == "pages":
                        lines.append(_pages_job(answers, point, job, other))
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
    leg the union expects, and it runs as the shards ``[ci] shards``
    gives the first runner through ``parallel: matrix``, each passing
    its ``SHARD``; the one document serves every event, so a pull
    request's counts are not read. A ``pythons`` or ``runners`` matrix
    is one job. A job deploying through the ``pages`` seam is GitLab
    Pages' own: a job named ``pages`` publishing ``public/``. A job
    that pushes, or writes the store, rewrites origin with the push
    token first, since the job token cannot push.
    """
    joins = _joins_of(point, job, everything)
    packages = " ".join(_installs(answers, point, job, joins))
    first = str(next(iter(answers.get("runners", ["ubuntu-latest"]))))
    stage = "release" if point.name in ("merge", "release") else "check"
    pages = _seam(answers, point, job) == "pages"
    name = "pages" if pages else job.name
    lines = [
        _comment(job.note),
        f"{name}:\n",
        f"  stage: {stage}\n",
        f"  image: {image}\n",
    ]
    count = _shards(answers, "shards", [first])[first] if job.matrix == "legs" else 1
    if count > 1:
        spelled = ", ".join(f'"{Shard(index, count)}"' for index in range(1, count + 1))
        lines.append(f"  parallel:\n    matrix:\n      - SHARD: [{spelled}]\n")
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
        # The job token cannot push: a store write and a receipt tag
        # ride the push token. The address
        # is the server's own protocol, host and port: an instance
        # off 443 (the local one) is unreachable at a bare https host.
        lines.append(
            '    - git remote set-url origin "${CI_SERVER_PROTOCOL}://oauth2:'
            "${GITLAB_PUSH_TOKEN}@${CI_SERVER_HOST}:${CI_SERVER_PORT}/"
            '${CI_PROJECT_PATH}.git"\n'
        )
    if packages:
        # Root in the container image: no sudo.
        lines.append(f"    - apt-get update -q && apt-get install -y -q {packages}\n")
    lines.append("    - source setup.sh gitlab\n")
    driver = _driver_step(prog, point, forge="gitlab")
    if driver:
        lines.append(f"    - {driver.split('run: ', 1)[1]}")
    call = f"{prog} ci.run --point={point.name} --job={job.name}"
    if job.matrix == "legs":
        python = str(next(iter(answers.get("gate_pythons", ["3.11"]))))
        call += f' --os="{first}" --python="{python}"'
        if count > 1:
            call += ' --shard="$SHARD"'
    elif job.matrix == "declared":
        call += f' --os="{job.runners[0]}" --python="{job.pythons[0]}"'
    elif job.matrix == "pythons":
        python = str(next(iter(answers.get("python_versions", ["3.11"]))))
        call += f' --python="{python}"'
    lines.append(f"    - {call}\n")
    if pages:
        lines.append("    - mv site public\n  artifacts:\n    paths: [public]\n")
        return "".join(lines) + "\n"
    # The job's own artifact, then what the joins publish and the site
    # a joined pages seam hands to the pages job; a join's artifact
    # expires, since the deploy that reads it follows at once.
    kept = list(job.paths) if job.publishes else []
    joined_paths: list[str] = []
    for other, joined in joins:
        joined_paths += list(joined.paths) if joined.publishes else []
        if _seam(answers, other, joined) == "pages":
            joined_paths.append("site")
    paths = kept + [path for path in joined_paths if path not in kept]
    if paths:
        spelled = ", ".join(f'"{path}"' for path in paths)
        lines.append(f"  artifacts:\n    paths: [{spelled}]\n")
        if joined_paths:
            lines.append("    expire_in: 1 day\n")
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
# write_repository: the job token cannot push, and the state store
# and the receipt tags are pushes) as CI
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
            if job.joins or not _renders(point, job, answers):
                continue
            lines.append(
                _gitlab_job(
                    answers, prog, point, job, image=image, everything=everything
                )
            )
            for other, joined in _joins_of(point, job, everything):
                if _seam(answers, other, joined) == "pages":
                    lines.append(
                        _gitlab_pages_job(
                            job, other, image=image, everything=everything
                        )
                    )
    return "".join(lines)


def _gitlab_pages_job(
    job: Job, other: Point, *, image: str, everything: tuple[Point, ...]
) -> str:
    """GitLab Pages' own job, publishing the site *job* kept, on *other*'s events."""
    return (
        _comment(
            f"GitLab Pages' deploy of the site the {job.name} job kept: no"
            " entry, the artifact moved to public/."
        )
        + f"pages:\n  stage: release\n  image: {image}\n  needs: [{job.name}]\n"
        + _gitlab_rules(other, everything)
        + "  script:\n    - mv site public\n  artifacts:\n    paths: [public]\n\n"
    )


def generate(root: Path) -> dict[str, str]:
    """Every generated CI file for *root*'s forge kind, by path.

    The workflows call the CLI by the name this process runs under,
    so a branded runner emits workflows that call itself and needs
    no configuration. The emitted ``setup.sh`` at the root is the
    entry every workflow's jobs share. The template-artifact job is
    emitted only for a home: the contract declares where to publish
    and a member extension ships the tree; an ordinary instance's
    release has no templates to publish.
    """
    from livery.workshop._entry import entry_script
    from livery.workshop._provenance import generated_header

    prog = footman.prog()
    everything = emitted_points(root)
    facts = _facts(root, everything)
    kind = str(facts["forge_kind"])
    header = generated_header("#")
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
    return {path: header + content for path, content in files.items()}


def generated_files(root: Path) -> dict[Path, str]:
    """The generated artifacts as absolute paths under *root*."""
    return {root / relative: content for relative, content in generate(root).items()}


#: What every generated file opens with, whatever runner wrote it.
GENERATED_MARK = "# Generated by the workshop from workshop.toml"


def retired_files(root: Path) -> tuple[Path, ...]:
    """The generated workflow files under *root* this emission does not own, to delete.

    Every file in the forge's workflow directory that opens with
    `GENERATED_MARK` and is not in this emission is retired: the
    workflow of a point a package contributed and that went with the
    package. A file without the mark is a person's and is never
    touched.
    """
    found: list[Path] = []
    owned = set(generate(root))
    for directory in (".github/workflows", ".gitea/workflows"):
        folder = root / directory
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.yml")):
            relative = path.relative_to(root).as_posix()
            if relative in owned:
                continue
            try:
                head = path.read_text("utf-8", errors="replace")[: len(GENERATED_MARK)]
            except OSError:
                continue
            if head == GENERATED_MARK:
                found.append(path)
    return tuple(found)
