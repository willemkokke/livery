"""livery's dev loop: the workshop, then this repository's own tasks.

Run with ``uv run fm <task>``. ``fm check`` is the whole local gate;
CI runs the same command. The tree comes from the mounted layers; the
template seeds this file once and never rewrites it, so everything
below the plugin line is this repository's own.
"""

import os

from footman import group, plugin
from toolroom import pytest

plugin("livery.workshop")

_fixtures = group("forge").group("fixtures", help="Recorded HTTP fixtures (cassettes)")


@_fixtures.task(name="record")
def fixtures_record() -> None:
    """Re-record the conformance cassettes from the live containers.

    Runs the backend conformance suites against the seeded containers
    (`fm forge.dev.up` first) and rewrites the cassettes under
    packages/forge/tests/cassettes/. Review the diff like code: a
    changed exchange is a changed contract with the server.
    """
    os.environ["LIVERY_FORGE_RECORD"] = "1"
    pytest.opts(in_process=False)("packages/forge/tests/test_gitea_conformance.py")
    # One single-node GitLab absorbs about four concurrent writers;
    # beyond that its own internals time out (Gitaly deadlines), so
    # the recording run is capped rather than flaky.
    pytest.opts(in_process=False)(
        "packages/forge/tests/test_gitlab_conformance.py", "-n", "4"
    )
    pytest.opts(in_process=False)(
        "packages/forge/tests/test_github_conformance.py", "-n", "4"
    )
