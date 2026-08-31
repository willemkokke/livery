"""Delete leftover conformance scratch repositories from one account.

Run at the end of every release-legs cloud job, whatever the leg's
verdict: lists the account's repositories and deletes those whose
name carries a conformance scratch prefix. Says what it deleted, and
"nothing to clean" otherwise.

Environment: LEG (github | gitlab | gitea), TOKEN, OWNER, and URL for
the non-github forges.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request

PREFIXES = ("livery-forge-conf-", "conf-", "corpse-")


def _request(url: str, headers: dict[str, str], method: str = "GET") -> object:
    request = urllib.request.Request(url, method=method, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
    return json.loads(body) if body else None


def main() -> int:
    """Run the script; the exit code is the verdict."""
    leg = os.environ["LEG"]
    token = os.environ["TOKEN"]
    owner = os.environ["OWNER"]
    deleted = 0
    if leg == "github":
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        }
        repos = _request(
            f"https://api.github.com/orgs/{owner}/repos?per_page=100", headers
        )
        assert isinstance(repos, list)
        for repo in repos:
            name = str(repo["name"])
            if name.startswith(PREFIXES):
                _request(
                    f"https://api.github.com/repos/{owner}/{name}",
                    headers,
                    method="DELETE",
                )
                print(f"deleted {owner}/{name}")
                deleted += 1
    elif leg == "gitea":
        base = os.environ["URL"].rstrip("/")
        headers = {"Authorization": f"token {token}"}
        repos = _request(f"{base}/api/v1/orgs/{owner}/repos?limit=50", headers)
        assert isinstance(repos, list)
        for repo in repos:
            name = str(repo["name"])
            if name.startswith(PREFIXES):
                _request(
                    f"{base}/api/v1/repos/{owner}/{name}", headers, method="DELETE"
                )
                print(f"deleted {owner}/{name}")
                deleted += 1
    elif leg == "gitlab":
        base = os.environ["URL"].rstrip("/")
        headers = {"PRIVATE-TOKEN": token}
        group = urllib.parse.quote(owner, safe="")
        projects = _request(
            f"{base}/api/v4/groups/{group}/projects?per_page=100", headers
        )
        assert isinstance(projects, list)
        for project in projects:
            name = str(project["path"])
            if name.startswith(PREFIXES):
                _request(
                    f"{base}/api/v4/projects/{project['id']}",
                    headers,
                    method="DELETE",
                )
                print(f"deleted {owner}/{name}")
                deleted += 1
    else:
        print(f"unknown leg {leg!r}")
        return 2
    if not deleted:
        print("nothing to clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
