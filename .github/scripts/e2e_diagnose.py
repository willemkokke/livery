"""Dump the scratch repositories' job evidence after a failed local leg.

Reads ``.forge.dev.env`` and prints, per conformance scratch
repository, its recent runs and the tail of each job's log, so a red
compose leg carries its own evidence out of the destroyed runner VM.
Argument: ``gitea`` or ``gitlab``.
"""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path


def _env() -> dict[str, str]:
    pairs = {}
    for line in Path(".forge.dev.env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            pairs[key] = value
    return pairs


def _get(url: str, headers: dict[str, str]) -> object:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
    try:
        return json.loads(body)
    except ValueError:
        return body.decode(errors="replace")


def _gitea() -> None:
    env = _env()
    base, token = env["GITEA_URL"].rstrip("/"), env["GITEA_TOKEN"]
    headers = {"Authorization": f"token {token}"}
    repos = _get(f"{base}/api/v1/orgs/livery/repos?limit=50", headers)
    assert isinstance(repos, list)
    for repo in repos:
        name = str(repo["name"])
        if not name.startswith("conf-"):
            continue
        runs = _get(f"{base}/api/v1/repos/livery/{name}/actions/tasks", headers)
        entries = runs.get("workflow_runs", []) if isinstance(runs, dict) else []
        for run in entries[:4]:
            print(
                f"{name}: task {run.get('id')} {run.get('status')}"
                f" {run.get('name')} head {str(run.get('head_sha'))[:10]}"
            )
        tags = _get(f"{base}/api/v1/repos/livery/{name}/tags?limit=10", headers)
        if isinstance(tags, list):
            print(f"{name}: tags {[t.get('name') for t in tags]}")


def _gitlab() -> None:
    env = _env()
    base, token = env["GITLAB_URL"].rstrip("/"), env["GITLAB_TOKEN"]
    headers = {"PRIVATE-TOKEN": token}
    projects = _get(f"{base}/api/v4/groups/livery/projects?per_page=50", headers)
    assert isinstance(projects, list)
    for project in projects:
        path = str(project["path"])
        if not path.startswith("conf-"):
            continue
        pid = project["id"]
        jobs = _get(f"{base}/api/v4/projects/{pid}/jobs?per_page=6", headers)
        assert isinstance(jobs, list)
        for job in jobs:
            print(f"{path}: job {job['id']} {job['status']} ref {job.get('ref')}")
            quoted = urllib.parse.quote(str(pid), safe="")
            trace = _get(
                f"{base}/api/v4/projects/{quoted}/jobs/{job['id']}/trace", headers
            )
            if isinstance(trace, str) and trace.strip():
                tail = trace.strip().splitlines()[-4:]
                for line in tail:
                    print(f"    {line}")


def main() -> int:
    """Run the dump; never fail the step it runs in."""
    leg = sys.argv[1]
    try:
        _gitea() if leg == "gitea" else _gitlab()
    except Exception as exc:
        print(f"diagnostics failed: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
