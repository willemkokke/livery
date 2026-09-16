"""One archive, one tree digest everywhere: the annotation decides the executable bit."""

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
    StoreError,
    _engine,
)
from toolroom_store_archives import make_zip, sha

HOST = "linux-x64"
EXE = ".exe" if sys.platform == "win32" else ""


def _record(name: str, data: bytes, **layout: object) -> Record:
    fields: dict[str, object] = {
        "paths": ("bin",),
        "entry_points": (f"bin/{name}{EXE}",),
    }
    fields.update(layout)
    return Record(
        name,
        hosts=(HOST,),
        layout=Layout(**fields),  # type: ignore[arg-type]
        deltas=(
            RecordDelta(
                1,
                "1.0.0",
                "",
                {HOST: Artifact(f"https://origin.test/{name}.zip", sha(data))},
            ),
        ),
    )


@pytest.fixture
def origin(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    served: dict[str, bytes] = {}

    def download(url: str) -> bytes:
        if url not in served:
            raise OSError(f"no such origin {url}")
        return served[url]

    monkeypatch.setattr(_engine, "download", download)
    return served


def _archive(name: str) -> bytes:
    """A tool whose archive carries modes on more than its entry point."""
    return make_zip(
        {
            f"bin/{name}{EXE}": b"#!/bin/sh\necho hi\n",
            "bin/helper.sh": b"#!/bin/sh\necho helper\n",
            "lib/data": b"d",
            "docs/README": b"r",
        },
        executable=(f"bin/{name}{EXE}", "bin/helper.sh"),
    )


# --- the refusal first ------------------------------------------------------


def test_a_declared_entry_point_the_tree_lacks_is_refused_naming_it_whole(
    tmp_path: Path, origin: dict[str, bytes]
) -> None:
    data = _archive("tool")
    record = _record("tool", data, entry_points=(f"bin/tool{EXE}", "bin/missing"))
    origin["https://origin.test/tool.zip"] = data
    store = Store(Home(tmp_path / "home"), host=HOST)
    with pytest.raises(StoreError) as caught:
        store.ensure(record, "1.0.0")
    message = str(caught.value)
    assert "tool 1.0.0 on linux-x64" in message
    assert "'bin/missing' is not in the extracted tree" in message
    assert store.objects.refs("tools") == []


# --- one digest everywhere ---------------------------------------------------


def _modeless(extract: object) -> object:
    """The extractor as Windows runs it: every member lands without a mode."""

    def stripped(artifact: Path, name: str, into: Path) -> None:
        extract(artifact, name, into)  # type: ignore[operator]
        for path in into.rglob("*"):
            if path.is_file() and not path.is_symlink():
                path.chmod(path.stat().st_mode & ~0o111)

    return stripped


def test_one_archive_lands_one_tree_through_posix_and_windows_extraction(
    tmp_path: Path, origin: dict[str, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    data = _archive("tool")
    record = _record("tool", data)
    origin["https://origin.test/tool.zip"] = data
    posix = Store(Home(tmp_path / "posix"), host=HOST).ensure(record, "1.0.0")
    monkeypatch.setattr(_engine, "_extract", _modeless(_engine._extract))
    windows = Store(Home(tmp_path / "windows"), host=HOST).ensure(record, "1.0.0")
    assert posix.tree == windows.tree
    # The entry point is executable in both views; the helper the
    # archive marked is not, on either: the annotation alone decides.
    for ensured in (posix, windows):
        entry = ensured.tool_dir / "bin" / f"tool{EXE}"
        helper = ensured.tool_dir / "bin" / "helper.sh"
        if sys.platform != "win32":
            assert entry.stat().st_mode & stat.S_IXUSR
            assert not helper.stat().st_mode & stat.S_IXUSR


def test_exclusions_apply_before_collect_and_change_the_tree(
    tmp_path: Path, origin: dict[str, bytes]
) -> None:
    data = _archive("tool")
    origin["https://origin.test/tool.zip"] = data
    whole = _record("tool", data)
    trimmed = _record("tool", data, exclude=("docs", "lib/*"))
    kept = Store(Home(tmp_path / "whole"), host=HOST).ensure(whole, "1.0.0")
    slim = Store(Home(tmp_path / "trimmed"), host=HOST).ensure(trimmed, "1.0.0")
    assert kept.tree != slim.tree
    assert (kept.tool_dir / "docs" / "README").is_file()
    assert not (slim.tool_dir / "docs").exists()
    assert not (slim.tool_dir / "lib" / "data").exists()
    assert (slim.tool_dir / "bin" / f"tool{EXE}").is_file()
