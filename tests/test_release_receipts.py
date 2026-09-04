"""Contract: a release tag is an annotated train receipt.

The train owns versions, tags, and receipts. A hand-cut tag creates
a state the release derivation does not model, so every
``packages/*`` tag must be an annotated tag whose message the train
wrote. A checkout without the tags (CI's shallow clone) sees an
empty list and proves nothing; the developer gate is where this
bites.
"""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The one pre-rule tag, hand-cut lightweight on a docs commit while
# claiming the name. Tags are immutable once pushed, so it stays as
# it is; nothing may join it.
GRANDFATHERED_LIGHTWEIGHT = {"packages/workshop/v0.0.2"}


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return result.stdout


def _dist_name(package: str) -> str:
    contract = ROOT / "packages" / package / "workshop.toml"
    return str(tomllib.loads(contract.read_text("utf-8")).get("name", ""))


def test_every_release_tag_is_an_annotated_train_receipt() -> None:
    tags = _git("tag", "-l", "packages/*").split()
    for tag in tags:
        kind = _git("cat-file", "-t", _git("rev-parse", tag).strip()).strip()
        if tag in GRANDFATHERED_LIGHTWEIGHT:
            assert kind == "commit", f"{tag}: the grandfather list is stale"
            continue
        assert kind == "tag", f"{tag}: lightweight; the train cuts annotated tags"
        _, package, version = tag.rsplit("/", 2)
        first_line = _git("tag", "-l", "--format=%(contents:lines=1)", tag).strip()
        receipts = {tag, f"{_dist_name(package)} {version.lstrip('v')}"}
        assert first_line in receipts, (
            f"{tag}: message {first_line!r} is not a train receipt"
        )
