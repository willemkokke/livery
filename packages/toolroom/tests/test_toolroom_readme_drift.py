"""What the README promises must match what the tree is.

The mirror of footman's drift guard: version references that live in
prose go stale silently, so the gate reads them back against the
package. `fm tools.prepare-release` rolls what these tests pin.
"""

from __future__ import annotations

import re
from pathlib import Path

from livery.toolroom import tools

_README = Path(__file__).resolve().parents[1] / "README.md"


def test_the_readme_pin_names_the_current_minor():
    # The beta note's advice is a compatible-release pin, so it names the
    # minor with a `.0` tail; a patch release keeps it true, a minor
    # release must roll it (prepare-release does).
    major, minor = tools.__version__.split(".")[:2]
    match = re.search(r"toolroom~=(\d+)\.(\d+)\.0", _README.read_text("utf-8"))
    assert match, "the README's beta note lost its minor-pin example"
    assert (match[1], match[2]) == (major, minor), (
        f"the README pins toolroom~={match[1]}.{match[2]}.0 but the tree is "
        f"{tools.__version__} — roll the pin (prepare-release does this)"
    )
