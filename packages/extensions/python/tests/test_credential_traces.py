"""A credential in a command line reaches a profile trace as ``***``.

The upload runs here under a real profile, the trace is read back, and
the index token is absent from it while the program still receives
the value.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import livery.toolroom.tools as tools
from extensions_python_traced import traced
from livery.extensions.python._backend import publish_wheels
from livery.workshop import Package

TOKEN = "s3cret-token-value"


def test_a_publish_token_reaches_a_trace_as_stars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The fault first: uv publish takes the index token as an argument.
    # Python stands in for uv and runs the file named like uv's
    # subcommand, which exits 0; the shown line keeps uv's name.
    member = tmp_path / "thing"
    (member / "dist").mkdir(parents=True)
    (member / "dist" / "thing-0.1.0-py3-none-any.whl").write_bytes(b"")
    (member / "publish").write_text("pass\n")
    monkeypatch.setattr(tools, "uv", tools.uv.at(sys.executable))
    package = Package(
        directory=member,
        path="packages/thing",
        name="thing",
        depends=(),
        extensions=("python",),
    )
    text, names = traced(
        tmp_path,
        lambda: publish_wheels(
            package, index_url="https://index.test/upload", token=TOKEN
        ),
    )
    assert TOKEN not in text
    (shown,) = [name for name in names if name.startswith("uv publish ")]
    assert "--token *** " in shown
