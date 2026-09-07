"""``fm ci.e2e``: the CI and release story against a local forge.

The workshop's own development substrate: provision a scratch
repository on the seeded local forge, and drive the emitted
workflows through a real runner against the forge's own package
registry, so the whole second world runs consequence-free before
anything reaches a public forge.

The verb registers only where the workshop develops itself, the way
``fm forge.fixtures.record`` does: a consumer checkout does not
carry it at all.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import livery.footman as footman
from livery.footman import fail
from livery.workshop._ci_tasks import ci

if TYPE_CHECKING:
    from livery.forge import Forge

#: The seeded organisation and the loop's one scratch repository.
E2E_OWNER = "livery"
E2E_REPO = "ci-e2e-loop"

#: The workshop's own test suite, present only in a source checkout:
#: this file is src/livery/workshop/_e2e.py, so the package directory
#: holding tests/ is three parents up.
_WORKSHOP_TESTS = Path(__file__).resolve().parents[3] / "tests"


def _dev_forge(kind: str) -> tuple[Forge, str]:
    """The seeded local forge and its token; refusal teaches.

    The credentials are the ones ``fm forge.dev.up`` writes into the
    shared env file the cascade reads, so a warm machine needs
    nothing beyond the containers being up.
    """
    if kind != "gitea":
        fail(
            f"--forge={kind} is not built: gitea is the one local lane"
            " today, and gitlab follows once gitea is in a good state"
        )
    url = os.environ.get("GITEA_URL", "")
    token = os.environ.get("GITEA_TOKEN", "")
    if not url or not token:
        fail(
            "the local Gitea's credentials are not in the environment."
            f" Run `{footman.prog()} forge.dev.up --profile=gitea`: it"
            " starts and seeds"
            " the containers and writes GITEA_URL and GITEA_TOKEN into"
            " the shared env file the cascade reads"
        )
    from livery.forge import GiteaForge

    return GiteaForge.connect(url=url, token=token), token


def provision(kind: str = "gitea") -> None:
    """Ensure the loop's repository, idempotently, with its secrets.

    The repository is created on the seeded organisation when absent
    and reused when present. ``UV_PUBLISH_TOKEN`` is written as an
    Actions secret with the forge token's value, because the forge's
    own package registry authenticates with the same credential as
    its API. Re-running is the recovery procedure.
    """
    from livery.forge import ForgeError, RepoConfig

    forge, token = _dev_forge(kind)
    try:
        existing = forge.get_repo(E2E_OWNER, E2E_REPO)
    except ForgeError as error:
        fail(
            f"the local forge did not answer: {error}. Is the container"
            f" up? `{footman.prog()} forge.dev.up --profile=gitea`"
        )
    if existing is None:
        forge.create_repo(
            E2E_OWNER,
            E2E_REPO,
            private=False,
            description="The workshop's local CI loop. Scratch; recreated freely.",
        )
        print(f"  created {E2E_OWNER}/{E2E_REPO}")
    else:
        print(f"  reusing {E2E_OWNER}/{E2E_REPO}")
    repo = forge.repository(E2E_OWNER, E2E_REPO)
    repo.configure(RepoConfig(secrets={"UV_PUBLISH_TOKEN": token}))
    print("  secret UV_PUBLISH_TOKEN set: the registry credential")


if _WORKSHOP_TESTS.is_dir():

    @ci.task(name="e2e")
    def e2e(forge: str = "gitea") -> None:
        """Exercise the CI and release story on the local forge.

        Today this provisions: the scratch repository ensured on the
        seeded organisation and the registry credential written as
        an Actions secret, idempotently. The workspace push, the
        workflow verdicts, and the release act into the local
        registry follow in this phase; each lands here as it is
        built, so the verb always says exactly what it covers.
        """
        provision(forge)
        print("  next: the workspace push and the workflow verdicts")
