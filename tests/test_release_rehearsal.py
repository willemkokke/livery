"""The armed rehearsal: the release path runs on the true graph.

The release arm aged fifteen findings between its rebuild and its
first real run, because nothing exercised the real path between
releases. This rehearsal is `workflow.release --local` over the
monorepo's actual member graph: real builds, both isolated legs, the
movement guard, publishing nothing and touching no forge. A failure
here is a suite failure instead of a release-day discovery, and it
revalidates the floors and toolchain pins on every armed run.

The rehearsal runs in a clone, so the working tree is never touched;
a probe commit rides in the clone so derivation always has something
to release, even right after a real release.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    not os.environ.get("WORKSHOP_CONFORMANCE_DRIVE"),
    reason="set WORKSHOP_CONFORMANCE_DRIVE=1 to run the rehearsal: it"
    " builds every member and resolves both legs against the index",
)


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return result.stdout


def test_the_release_path_rehearses_on_the_true_graph(tmp_path: Path) -> None:
    clone = tmp_path / "rehearsal"
    _git(ROOT, "clone", "--quiet", str(ROOT), str(clone))
    _git(clone, "config", "user.email", "rehearsal@livery.local")
    _git(clone, "config", "user.name", "Rehearsal")
    # The probe commit: one line per member, so derive_plans always
    # finds unreleased work whatever main's real state is.
    for member in ("forge", "workshop"):
        probe = clone / "packages" / member / "docs" / "rehearsal-probe.md"
        probe.parent.mkdir(exist_ok=True)
        probe.write_text("The rehearsal's probe; never merged.\n")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", "feat: the rehearsal probe rides in the clone")

    from livery.workshop._release_driver import local_release, resolve_set

    members = resolve_set(clone, ("packages/forge", "packages/workshop"))
    local_release(clone, members)

    # The stamps rolled back: the clone's tracked state is untouched.
    assert _git(clone, "status", "--porcelain").strip() == ""
    # The would-be wheels exist for every member.
    for member in ("forge", "workshop"):
        dist = clone / "packages" / member / "dist"
        assert any(dist.glob("*.whl")), f"no wheel for {member}"
