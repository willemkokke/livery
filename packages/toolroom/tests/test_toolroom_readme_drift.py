"""What the README promises must match what the tree is.

The mirror of footman's drift guard: version references that live in
prose go stale silently, so the gate reads them back against the
package.
"""

from __future__ import annotations

from pathlib import Path

_README = Path(__file__).resolve().parents[1] / "README.md"
