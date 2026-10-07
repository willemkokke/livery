"""The local forge containers' credentials, for the suites that drive them."""

from __future__ import annotations

from livery.forge.testing import rig_record_path


def rig_credentials() -> dict[str, str]:
    """The containers' URLs and tokens, read from their own record.

    Only the containers' seed writes the record. The cascade's
    environment and the shared env file name the current forge, which a
    local loop environment rewrites with its own Gitea's keys, so a
    suite that read them would drive the loop's Gitea, whose runners do
    not run the conformance workflow. Empty without the record, so a
    live suite skips.
    """
    record = rig_record_path()
    pairs: dict[str, str] = {}
    if record.is_file():
        for line in record.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, _, value = line.partition("=")
                pairs[key.strip()] = value.strip()
    return pairs
