"""File or extend the nightly-failure issue, through livery.forge itself.

The nightly is the one workflow that creates issues unprompted, so its
deduplication probe is part of the job, not left to chance: search for
the marker first, comment on a hit, create on a miss. This is the
workflows note's W11 running on the protocol it specifies.
"""

from __future__ import annotations

import os

from livery.forge import GithubForge

MARKER = "nightly: released-wheels replay failed"


def main() -> None:
    """Search by the marker; comment the new evidence, or file the issue."""
    run_url = os.environ["NIGHTLY_RUN_URL"]
    forge = GithubForge.connect()
    owner, name = os.environ.get("GITHUB_REPOSITORY", "willemkokke/livery").split("/")
    issues = forge.repository(owner, name).issue
    existing = issues.search(MARKER)
    if existing:
        issues.comment(existing[0].number, f"Failed again: {run_url}")
        print(f"commented on #{existing[0].number}")
        return
    created = issues.create(
        MARKER,
        body=(
            "The released-wheels replay is red: the wheel on the index no"
            " longer passes its own recorded contract in the consumer"
            " configuration.\n\n"
            f"Run: {run_url}\n\n"
            "The job installs the latest livery-forge from PyPI into a plain"
            " virtual environment and replays the conformance cassettes"
            " recorded for that release. A red night therefore means a"
            " dependency release broke the installed configuration, or the"
            " index artifact drifted; the run's logs carry the failing step"
            " verbatim."
        ),
    )
    print(f"created #{created.number}")


if __name__ == "__main__":
    main()
