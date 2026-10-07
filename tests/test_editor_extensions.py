"""Every editor extension this repository's extensions recommend exists.

A recommendation names a marketplace id, and an id the marketplace does
not know installs nothing and says nothing: the editor silently goes
without the checker the gate runs. The marketplace is asked on the
nightly, so a pull request never waits on it.
"""

from __future__ import annotations

import json
import urllib.request
from importlib.metadata import entry_points

import pytest

#: The VS Code Marketplace's query endpoint and the filter that names an
#: extension by its ``publisher.name`` id.
GALLERY = "https://marketplace.visualstudio.com/_apis/public/gallery/extensionquery"
BY_ID = 7


def _recommended() -> list[str]:
    """The editor ids the installed extensions' checks recommend, sorted.

    Read from each extension's declaration, its own checks and those it
    adds to a target alike, without importing the extension.
    """
    from livery.workshop._declaration import read

    found: set[str] = set()
    for entry in entry_points(group="workshop.extensions"):
        declared = read(entry.name, entry.value)
        if declared is None:
            continue
        for additions in (declared.additions, *declared.targets.values()):
            found.update(
                check.editor_extension
                for check in additions.checks
                if check.editor_extension
            )
    return sorted(found)


def _published(extension: str) -> bool:
    """Whether the marketplace knows *extension*, a ``publisher.name`` id."""
    query = {"filters": [{"criteria": [{"filterType": BY_ID, "value": extension}]}]}
    request = urllib.request.Request(
        GALLERY,
        data=json.dumps({**query, "flags": 0}).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json;api-version=3.0-preview.1",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        found = json.load(response)["results"][0]["extensions"]
    return any(
        f"{item['publisher']['publisherName']}.{item['extensionName']}".lower()
        == extension.lower()
        for item in found
    )


@pytest.mark.only_at("nightly")
def test_every_recommended_editor_extension_is_on_the_marketplace() -> None:
    # The refusal first: an id the marketplace does not know reads as absent.
    assert not _published("detachedfork.basedpyright")
    recommended = _recommended()
    assert "detachhead.basedpyright" in recommended  # something to ask about
    assert [name for name in recommended if not _published(name)] == []
