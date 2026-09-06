"""Structural guards for the process boundary.

Two greps, enforced as tests the way `tools.pyi` parity is:

- Refusals exit `EX_USAGE` (64), and the only spelling of that fact is the
  named constant — a literal 2 refusal must not creep back in through the
  next diagnostic subcommand.
- footman must not know who is calling it: nothing under `src/footman/` may
  mention Claude (or any other caller) by name. The boundary is the same
  boundary for a hook, a shell pipeline, and a CI step.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "livery" / "footman"

# The two files that produce refusal codes. The constant itself lives in
# _executor.py; everything else says the name.
REFUSAL_SITES = ("_app.py", "_executor.py")

_LITERAL_TWO = re.compile(r"\breturn 2\b|\bcode\s*=\s*2\b|_result\(seg, 2\b")


def test_no_literal_refusal_two():
    for name in REFUSAL_SITES:
        source = (SRC / name).read_text(encoding="utf-8")
        for lineno, line in enumerate(source.splitlines(), start=1):
            assert not _LITERAL_TWO.search(line), (
                f"{name}:{lineno} spells a refusal code as a literal 2 — "
                f"use EX_USAGE: {line.strip()}"
            )


def test_source_never_names_a_caller():
    hits = [
        f"{path.relative_to(SRC)}:{lineno}"
        for path in sorted(SRC.rglob("*.py*"))
        if "__pycache__" not in path.parts
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        )
        if "claude" in line.lower()
    ]
    assert not hits, (
        "src/footman/ names a specific caller — the boundary must stay "
        f"caller-blind: {hits}"
    )
