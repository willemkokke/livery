"""The home: its directories, a store opened twice, another layout refused."""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.strongroom import ManifestError
from livery.toolroom.store import TOOLS, URLS, Home


def test_a_home_whose_store_is_another_layout_is_refused(tmp_path: Path) -> None:
    home = Home(tmp_path / "home")
    home.store.mkdir(parents=True)
    (home.store / "strongroom.json").write_text('{"layout": 99}')
    with pytest.raises(ManifestError):
        home.open_store()


def test_the_home_lays_out_its_directories_and_opens_its_store_twice(
    tmp_path: Path,
) -> None:
    home = Home(tmp_path / "home")
    assert home.tools == home.root / "tools"
    assert home.tool_dir("bun", "1.3.14") == home.root / "tools" / "bun@1.3.14"
    assert home.uv == home.root / "uv" and home.bun == home.root / "bun"
    first = home.open_store()
    assert (home.store / "strongroom.json").is_file()
    assert set(first.namespaces) >= {TOOLS, URLS}
    assert first.namespaces[TOOLS].mutation == "write-once"
    assert first.namespaces[URLS].mutation == "volatile"
    second = home.open_store()
    assert second.root == first.root
