"""The CI run this process belongs to, and the state store built with its policy.

The run context reads the runner's environment: GitHub's and Gitea's
``GITHUB_*`` variables, GitLab's ``CI_*``. [livery.workshop._ci_context.state_store][]
builds the [livery.git.StateStore][] every verb reads and writes the
workshop's series through, with the three answers the store takes as
construction data: whether this is a CI run, the identity every commit
carries, and the variable a published listing travels in.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import livery.footman as footman
from livery import git

#: The variable the job runner sets for every entry it spawns, naming
#: the check leg (``check-ubuntu-latest-3.14``): the key of the leg's
#: rows and stamps.
LEG_VARIABLE = "WORKSHOP_LEG"

#: The variable the job runner sets for every entry it spawns, naming
#: the point the job runs at. Its home is here rather than beside the
#: pytest plugin that selects on it, because the verbs read it too and
#: a CLI that never tests must not import pytest to learn its name.
POINT_VARIABLE = "WORKSHOP_POINT"

#: The variable a published snapshot's file is named in: the job
#: runner takes one listing for the whole job and every entry it
#: spawns reads through it instead of listing again.
SNAPSHOT_VARIABLE = "WORKSHOP_SNAPSHOT"


def state_store(root: Path) -> git.StateStore:
    """The state store of the checkout at *root*, with the workshop's policy.

    A CI run may write a ``ci_only`` series and a desk may not; every
    commit the store makes is by the tools' identity
    ([livery.git.tool_identity][]); a published listing travels in
    `SNAPSHOT_VARIABLE`.
    """
    prog = footman.prog()
    return git.StateStore(
        root,
        in_ci=run_context() is not None,
        identity=git.tool_identity(prog),
        snapshot_variable=SNAPSHOT_VARIABLE,
        prog=prog,
    )


@dataclass(frozen=True)
class RunContext:
    """The CI run this process belongs to, as the runner's environment names it.

    Attributes:
        forge: ``github``, ``gitea``, or ``gitlab``.
        run_id: The forge's identifier of the run (GitLab: the pipeline).
        event: What started the run (``push``, ``pull_request``, ...).
        ref: The ref the run is for, as the runner spells it.
        head_sha: The commit the forge files the run under. On a pull
            request the checkout is the merge commit the forge
            synthesised, and the run's own head is the pull request's
            head, which the event payload names; on a push both are
            the same commit. Empty when the runner did not say.
        base_ref: The branch a pull request proposes into, as the event
            payload names it (GitLab: the merge request's target);
            empty on a push or when the runner did not say.
        leg: The check leg this process runs in (``check-ubuntu-latest-3.14``),
            as the job runner names it in ``WORKSHOP_LEG`` for every entry it
            spawns; empty outside a scheduled job.
        head_ref: The branch a pull request comes from, as the event
            payload names it (GitLab: the merge request's source), the
            base of the branch's own coverage record; empty on a push
            or when the runner did not say.
    """

    forge: str
    run_id: str
    event: str
    ref: str
    head_sha: str = ""
    base_ref: str = ""
    leg: str = ""
    head_ref: str = ""


def event_payload(environ: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    """The runner's event payload, or ``None`` when there is none to read.

    GitHub and Gitea write the event that started the run to the file
    ``GITHUB_EVENT_PATH`` names; a payload that is missing or does not
    parse reads as none, so a caller falls back rather than fails.
    GitLab writes no file and carries the same facts as variables, so a
    merge request pipeline answers with the payload's ``pull_request``
    shape built from ``CI_MERGE_REQUEST_*``: the iid as the number, the
    title, the source branch and head sha, and the target branch. Any
    other GitLab pipeline has no payload.
    """
    env = os.environ if environ is None else environ
    path = env.get("GITHUB_EVENT_PATH", "")
    if not path:
        if env.get("GITLAB_CI") == "true" and env.get("CI_MERGE_REQUEST_IID"):
            return {
                "pull_request": {
                    "number": int(env["CI_MERGE_REQUEST_IID"]),
                    "title": env.get("CI_MERGE_REQUEST_TITLE", ""),
                    "head": {
                        "ref": env.get("CI_MERGE_REQUEST_SOURCE_BRANCH_NAME", ""),
                        "sha": env.get("CI_COMMIT_SHA", ""),
                    },
                    "base": {"ref": env.get("CI_MERGE_REQUEST_TARGET_BRANCH_NAME", "")},
                }
            }
        return None
    try:
        payload = json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def dispatch_inputs(
    names: tuple[str, ...], environ: Mapping[str, str] | None = None
) -> dict[str, str]:
    """The dispatch inputs *names*, as the run received them; ``""`` for one it did not.

    GitHub and Gitea carry a dispatch's inputs in the event payload
    under ``inputs``; GitLab carries them as pipeline variables, which
    arrive as environment variables named by the input. A run that no
    dispatch started, or a process outside CI, has none, and every
    name reads empty.
    """
    env = os.environ if environ is None else environ
    payload = event_payload(env)
    carried = (payload or {}).get("inputs") or {}
    found: dict[str, str] = {}
    for name in names:
        if isinstance(carried, dict) and carried.get(name) not in (None, ""):
            found[name] = str(carried[name])
        else:
            found[name] = env.get(name, "")
    return found


def _event_head_sha(env: Mapping[str, str]) -> str:
    """The pull request's head from the event payload, or the pushed commit.

    A payload that is missing or does not parse falls back to the
    pushed commit: the lookup then works on a push and misses on a
    pull request, which the collect names rather than fails on.
    """
    payload = event_payload(env)
    if payload is not None:
        head = (payload.get("pull_request") or {}).get("head") or {}
        if isinstance(head, dict) and head.get("sha"):
            return str(head["sha"])
    return env.get("GITHUB_SHA", "")


def _event_base_ref(env: Mapping[str, str]) -> str:
    """The pull request's base branch from the event payload, or empty."""
    payload = event_payload(env)
    if payload is None:
        return ""
    base = (payload.get("pull_request") or {}).get("base") or {}
    if isinstance(base, dict) and base.get("ref"):
        return str(base["ref"])
    return ""


def _event_head_ref(env: Mapping[str, str]) -> str:
    """The pull request's source branch: the payload's, else the runner's variable."""
    payload = event_payload(env)
    if payload is not None:
        head = (payload.get("pull_request") or {}).get("head") or {}
        if isinstance(head, dict) and head.get("ref"):
            return str(head["ref"])
    return env.get("GITHUB_HEAD_REF", "")


#: GitLab's pipeline sources in the one event vocabulary the verbs
#: read: a merge request pipeline is a pull request run, and an API-,
#: trigger- or web-created pipeline is a dispatch, as the forge
#: backend maps a listed pipeline. A source not named here (``push``,
#: ``schedule``) is already the vocabulary's word.
GITLAB_EVENTS = {
    "merge_request_event": "pull_request",
    "api": "workflow_dispatch",
    "trigger": "workflow_dispatch",
    "web": "workflow_dispatch",
}


def run_context(environ: dict[str, str] | None = None) -> RunContext | None:
    """The CI run this process runs in, or ``None`` outside CI.

    GitHub and Gitea both speak the ``GITHUB_*`` variables (the
    act_runner keeps GitHub's names and adds ``GITEA_ACTIONS``);
    GitLab speaks ``GITLAB_CI`` and ``CI_*``, and its pipeline source
    is read through `GITLAB_EVENTS`, so a merge request pipeline
    narrows, records and checks titles the way a pull request run does.
    """
    env = os.environ if environ is None else environ
    if env.get("GITHUB_ACTIONS") == "true":
        forge = "gitea" if env.get("GITEA_ACTIONS") == "true" else "github"
        return RunContext(
            forge,
            env.get("GITHUB_RUN_ID", ""),
            env.get("GITHUB_EVENT_NAME", ""),
            env.get("GITHUB_REF", ""),
            _event_head_sha(env),
            _event_base_ref(env),
            env.get(LEG_VARIABLE, ""),
            _event_head_ref(env),
        )
    if env.get("GITLAB_CI") == "true":
        source = env.get("CI_PIPELINE_SOURCE", "")
        return RunContext(
            "gitlab",
            env.get("CI_PIPELINE_ID", ""),
            GITLAB_EVENTS.get(source, source),
            env.get("CI_COMMIT_REF_NAME", ""),
            env.get("CI_COMMIT_SHA", ""),
            env.get("CI_MERGE_REQUEST_TARGET_BRANCH_NAME", ""),
            env.get(LEG_VARIABLE, ""),
            env.get("CI_MERGE_REQUEST_SOURCE_BRANCH_NAME", ""),
        )
    return None


def ci_run(environ: dict[str, str] | None = None) -> RunContext | None:
    """The CI run this process belongs to, or None at a desk.

    Read from the runner's environment, *environ* when given, the
    process's own otherwise: GitHub's and Gitea's ``GITHUB_*``
    variables, GitLab's ``CI_*``. An extension that behaves
    differently in CI asks this, never a variable of one forge.
    """
    return run_context(environ)
