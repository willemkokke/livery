"""Compare what a render writes with its goldens under ``fixtures/``.

A golden is the file a test expects a render to write, byte for byte.
When the render moves, `match` fails naming each file that moved and
its difference, and writes the new file into the test's own temporary
directory: a deliberate change copies those files over the goldens in
the same change. This is not a conftest, for the reason
`workshop_seeds` gives.
"""

from __future__ import annotations

import difflib
from collections.abc import Mapping
from pathlib import Path

#: Where the goldens of the workshop's tests live.
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def match(
    rendered: Mapping[str, str], goldens: Path, scratch: Path, *, suffix: str = ""
) -> None:
    """Fail unless *rendered* is the files under *goldens*, each byte for byte.

    *rendered* maps each file's path under *goldens*, spelled with
    ``/``, to its text. A file's golden is that path with *suffix*
    appended, so a golden that a tool would otherwise read as the file
    it stands for (a test module pytest collects, a ``.gitignore`` git
    applies) carries a suffix no tool reads. Each file that moved or
    has no golden is written under *scratch* by its golden's name, and
    its difference is part of the failure. A golden no rendered file
    stands for fails too.
    """
    where = goldens.relative_to(FIXTURES).as_posix()
    moved: list[str] = []
    for name, text in sorted(rendered.items()):
        golden = goldens / f"{name}{suffix}"
        expected = golden.read_bytes().decode("utf-8") if golden.is_file() else None
        if text == expected:
            continue
        written = scratch / f"{name}{suffix}"
        written.parent.mkdir(parents=True, exist_ok=True)
        written.write_bytes(text.encode("utf-8"))
        # A missing golden is named on its own line: an empty file has no
        # difference to show.
        missing = f"{where}/{name}{suffix}: no golden\n" if expected is None else ""
        moved.append(
            missing
            + "".join(
                difflib.unified_diff(
                    (expected or "").splitlines(keepends=True),
                    text.splitlines(keepends=True),
                    f"{where}/{name}{suffix}",
                    str(written),
                )
            )
        )
    assert not moved, (
        f"the render moved under {where}; a deliberate change copies the files"
        f" in {scratch} over the goldens in the same change:\n" + "\n".join(moved)
    )
    kept = sorted(
        path.relative_to(goldens).as_posix()
        for path in goldens.rglob("*")
        if path.is_file()
    )
    wanted = sorted(f"{name}{suffix}" for name in rendered)
    assert kept == wanted, f"{where} holds goldens no render writes"
