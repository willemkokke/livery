"""The store's doctor: a damaged view is named and mended from its tree alone."""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

from livery.toolroom.store import (
    Artifact,
    Home,
    Layout,
    Record,
    RecordDelta,
    Store,
    _doctor,
    _engine,
)
from toolroom_store_archives import make_zip, sha

EXE = ".exe" if sys.platform == "win32" else ""


def _supplied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A store under a data directory, one tool supplied: the data dir, the tool."""
    data = tmp_path / "data"
    store = Store(Home.under(data))
    host = store.host
    files = {
        f"tool-1.0.0/bin/tool{EXE}": b"#!/bin/sh\necho hi\n",
        "tool-1.0.0/README": b"r",
    }
    zipped = make_zip(files, executable=(f"tool-1.0.0/bin/tool{EXE}",))
    url = f"https://origin.test/tool/1.0.0/{host}.zip"
    record = Record(
        "tool",
        kind="download",
        hosts=(host,),
        layout=Layout(
            root="tool-1.0.0", entry_points=(f"bin/tool{EXE}",), paths=("bin",)
        ),
        deltas=(RecordDelta(1, "1.0.0", "", {host: Artifact(url, sha(zipped))}),),
    )
    monkeypatch.setattr(
        _engine, "download", lambda asked: zipped if asked == url else b""
    )
    ensured = store.ensure(record, "1.0.0")
    return data, ensured.tool_dir / "bin" / f"tool{EXE}"


def _offline(asked: str) -> bytes:
    raise OSError(f"offline: {asked}")


def test_without_a_store_there_is_nothing_to_audit(tmp_path: Path) -> None:
    found = _doctor.doctor(data_dir=tmp_path / "data")
    assert [f.what for f in found] == ["views: no tool store here"]
    assert not any(f.found for f in found)


def test_a_damaged_view_is_named_and_the_repair_views_the_tree_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data, tool = _supplied(tmp_path, monkeypatch)
    whole = ["views: every tool's view is whole"]
    assert [f.what for f in _doctor.doctor(data_dir=data)] == whole
    tool.chmod(tool.stat().st_mode | stat.S_IWUSR)
    tool.unlink()
    # The origin is gone: the mend needs nothing from it.
    monkeypatch.setattr(_engine, "download", _offline)
    found = _doctor.doctor(data_dir=data)
    assert [f.what for f in found] == ["views: tool 1.0.0"]
    assert "file(s) missing: bin/tool" in found[0].found
    assert "fm doctor --fix" in found[0].fix and found[0].repair is not None
    found[0].repair()
    assert tool.exists()
    assert [f.what for f in _doctor.doctor(data_dir=data)] == whole
