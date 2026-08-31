"""Mint or remove the disposable gitea.com runner for one legs run.

gitea.com grants no shared runners and its UI registration tokens are
one-time, so each run mints its own registration token through the
API (``mint`` prints it for the act_runner container) and deletes the
registered runner afterwards by name (``remove``). The runner itself
is a container the workflow starts; nothing is stored between runs.

Environment: TOKEN, OWNER, URL, RUNNER_NAME.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request


def _request(url: str, token: str, method: str = "GET") -> object:
    request = urllib.request.Request(
        url, method=method, headers={"Authorization": f"token {token}"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
    return json.loads(body) if body else None


def main() -> int:
    """Run the script; the exit code is the verdict."""
    action = sys.argv[1]
    base = os.environ["URL"].rstrip("/")
    token = os.environ["TOKEN"]
    owner = os.environ["OWNER"]
    name = os.environ["RUNNER_NAME"]
    if action == "mint":
        data = _request(
            f"{base}/api/v1/orgs/{owner}/actions/runners/registration-token",
            token,
            method="POST",
        )
        assert isinstance(data, dict)
        print(str(data["token"]))
        return 0
    if action == "remove":
        listing = _request(f"{base}/api/v1/orgs/{owner}/actions/runners", token)
        assert isinstance(listing, dict)
        for runner in listing.get("entries") or []:
            if str(runner.get("name")) == name:
                _request(
                    f"{base}/api/v1/orgs/{owner}/actions/runners/{runner['id']}",
                    token,
                    method="DELETE",
                )
                print(f"removed runner {name}")
                return 0
        print(f"no runner named {name} (already gone)")
        return 0
    print(f"unknown action {action!r}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
