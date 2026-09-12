"""Sources and tiers: refusals first, then the read path that holds."""

from __future__ import annotations

import contextlib
import http.server
import io
import re
import socket
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from livery.strongroom import (
    ALGORITHMS,
    LAYOUT_VERSION,
    MANIFEST_NAME,
    SHA256,
    Algorithm,
    Digest,
    ErasedObject,
    FolderSource,
    HttpSource,
    Manifest,
    ManifestError,
    MissingObject,
    OriginHint,
    Store,
    Subject,
    Tombstone,
    Unreachable,
    _store,
    digest_of,
    fetch_url,
    silent,
)

HELLO = digest_of(b"hello")


def _mirror(root: Path, *objects: bytes) -> Store:
    mirror = Store.create(root)
    for data in objects:
        mirror.put(data)
    return mirror


class _Reports:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, message: str) -> None:
        self.lines.append(message)


@contextlib.contextmanager
def _serve(directory: Path) -> Iterator[str]:
    handler = type(
        "Quiet",
        (http.server.SimpleHTTPRequestHandler,),
        {"log_message": lambda self, *args: None},
    )
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), lambda *args: handler(*args, directory=str(directory))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


# Declaring sources: what is refused before any fetch.


def test_a_folder_source_must_be_a_store_of_the_same_kind(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="not a store"):
        Store.create(tmp_path / "s", sources=[FolderSource(tmp_path / "empty")])
    other = tmp_path / "other"
    other.mkdir()
    (other / MANIFEST_NAME).write_bytes(Manifest(2, "sha256").encode())
    with pytest.raises(
        ManifestError, match="is layout 2, sha256; this store is layout 1"
    ):
        Store.create(tmp_path / "s2", sources=[FolderSource(other)])


def test_a_fill_policy_and_a_url_are_checked_at_declaration(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not copy or reference"):
        FolderSource(tmp_path, "move")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="not http or https"):
        HttpSource("ftp://mirror.example/store")
    with pytest.raises(ValueError, match="not http or https"):
        OriginHint(HELLO, "file:///tmp/hello")
    with pytest.raises(ValueError, match="not http or https"):
        HttpSource("http:///nohost")


# Fetching: what is refused, skipped, and reported.


def test_fetch_refuses_an_erased_object_before_any_source(tmp_path: Path) -> None:
    mirror = _mirror(tmp_path / "m", b"hello")
    store = Store.create(tmp_path / "s", sources=[FolderSource(mirror.root)])
    store.write_tombstone(
        Tombstone(HELLO, "2026-09-11T12:00:00Z", Subject("person", "w"), None, "gone")
    )
    with pytest.raises(ErasedObject, match="gone"):
        store.fetch(HELLO)


def test_a_miss_names_the_digest_and_the_source_count(tmp_path: Path) -> None:
    mirror = _mirror(tmp_path / "m")
    store = Store.create(tmp_path / "s", sources=[FolderSource(mirror.root)])
    with pytest.raises(MissingObject, match="nor at any of its 1 source"):
        store.fetch(HELLO)
    with pytest.raises(MissingObject, match="nor at any of its 1 source"):
        store.fill(tmp_path / "fill", [HELLO])


def test_a_corrupt_mirror_is_refused_and_the_next_source_answers(
    tmp_path: Path,
) -> None:
    bad = _mirror(tmp_path / "bad", b"hello")
    bad.object_path(HELLO).write_bytes(b"hallo")
    good = _mirror(tmp_path / "good", b"hello")
    reports = _Reports()
    store = Store.create(
        tmp_path / "s",
        sources=[FolderSource(bad.root), FolderSource(good.root)],
        progress=reports,
    )
    assert store.fetch(HELLO) == store.object_path(HELLO)
    assert store.read(HELLO) == b"hello"
    assert len(reports.lines) == 1
    assert reports.lines[0].startswith(
        f"refused folder {bad.root} (copy): landing expected"
    )
    assert list(store.root.rglob("*.part")) == []


def test_a_corrupt_reference_folder_is_refused_without_landing(tmp_path: Path) -> None:
    bad = _mirror(tmp_path / "bad", b"hello")
    bad.object_path(HELLO).write_bytes(b"hallo")
    reports = _Reports()
    store = Store.create(
        tmp_path / "s", sources=[FolderSource(bad.root, "reference")], progress=reports
    )
    with pytest.raises(MissingObject):
        store.fetch(HELLO)
    assert reports.lines == [
        f"refused folder {bad.root} (reference): {bad.object_path(HELLO)} holds"
        f" bytes named {digest_of(b'hallo')}, not {HELLO}"
    ]
    assert store.state(HELLO) == "absent"


def test_an_unreachable_http_source_is_skipped_and_reported(tmp_path: Path) -> None:
    reports = _Reports()
    dead = HttpSource(f"http://127.0.0.1:{_closed_port()}", connect_timeout=1)
    good = _mirror(tmp_path / "good", b"hello")
    store = Store.create(
        tmp_path / "s", sources=[dead, FolderSource(good.root)], progress=reports
    )
    assert store.fetch(HELLO) == store.object_path(HELLO)
    assert store.read(HELLO) == b"hello"
    assert len(reports.lines) == 1
    assert reports.lines[0].startswith(
        f"skipped http {dead.base_url}: {dead.base_url}/"
    )


def test_an_http_source_of_another_store_is_refused(tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    (other / MANIFEST_NAME).write_bytes(Manifest(LAYOUT_VERSION, "blake3").encode())
    with _serve(other) as base:
        store = Store.create(tmp_path / "s", sources=[HttpSource(base)])
        with pytest.raises(ManifestError, match="is layout 1, blake3; this store"):
            store.fetch(HELLO)


def test_an_http_source_without_a_manifest_is_skipped(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    reports = _Reports()
    with _serve(empty) as base:
        store = Store.create(
            tmp_path / "s", sources=[HttpSource(base)], progress=reports
        )
        with pytest.raises(MissingObject):
            store.fetch(HELLO)
    assert reports.lines == [f"skipped http {base}: {base}/{MANIFEST_NAME}: HTTP 404"]


def test_an_http_404_for_the_object_is_the_source_lacking_it(tmp_path: Path) -> None:
    mirror = _mirror(tmp_path / "m")
    reports = _Reports()
    with _serve(mirror.root) as base:
        store = Store.create(
            tmp_path / "s", sources=[HttpSource(base)], progress=reports
        )
        with pytest.raises(MissingObject):
            store.fetch(HELLO)
        # The manifest is checked once per source, not once per fetch.
        with pytest.raises(MissingObject):
            store.fetch(HELLO)
    assert len(reports.lines) == 2
    assert all(line.endswith("HTTP 404") for line in reports.lines)


def test_offline_never_consults_an_origin_and_names_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    @contextlib.contextmanager
    def never(url: str, **kwargs: Any) -> Iterator[io.BytesIO]:
        calls.append(url)
        yield io.BytesIO(b"hello")

    monkeypatch.setattr(_store, "fetch_url", never)
    hint = OriginHint(HELLO, "https://vendor.example/hello.bin")
    store = Store.create(tmp_path / "s", sources=[hint], offline=True)
    with pytest.raises(
        MissingObject,
        match=re.escape(
            "offline and https://vendor.example/hello.bin would have satisfied it"
        ),
    ):
        store.fetch(HELLO)
    assert calls == []


def test_an_origin_serving_wrong_bytes_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    @contextlib.contextmanager
    def wrong(url: str, **kwargs: Any) -> Iterator[io.BytesIO]:
        yield io.BytesIO(b"hallo")

    monkeypatch.setattr(_store, "fetch_url", wrong)
    reports = _Reports()
    hint = OriginHint(HELLO, "https://vendor.example/hello.bin")
    store = Store.create(tmp_path / "s", sources=[hint], progress=reports)
    with pytest.raises(MissingObject):
        store.fetch(HELLO)
    assert reports.lines[0].startswith(
        "refused origin https://vendor.example/hello.bin"
    )
    assert store.state(HELLO) == "absent"


def test_fetch_url_refuses_every_non_200_and_every_failure(tmp_path: Path) -> None:
    folder = tmp_path / "f"
    folder.mkdir()
    (folder / "ok").write_bytes(b"ok")
    with _serve(folder) as base:
        with (
            pytest.raises(Unreachable, match="HTTP 404"),
            fetch_url(f"{base}/missing", connect_timeout=1, transfer_timeout=1),
        ):
            pass
        with fetch_url(f"{base}/ok?x=1", connect_timeout=1, transfer_timeout=1) as got:
            assert got.read() == b"ok"
    # Windows retries a refused connection for about a second before
    # reporting it, in its own words.
    with (
        pytest.raises(Unreachable, match=r"Connection refused|actively refused"),
        fetch_url(
            f"http://127.0.0.1:{_closed_port()}/", connect_timeout=5, transfer_timeout=1
        ),
    ):
        pass


def test_fill_refuses_a_folder_of_another_algorithm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = Store.create(tmp_path / "s")
    store.put(b"hello")
    other = tmp_path / "other"
    other.mkdir()
    (other / MANIFEST_NAME).write_bytes(Manifest(LAYOUT_VERSION, "blake3").encode())
    # Unregistered, the folder is refused as no store at all.
    with pytest.raises(ManifestError, match="not in the registry"):
        store.fill(other, [HELLO])
    # Registered, it is a store of another address space, and fill
    # refuses to mix: the guard the registry's growth will need.
    monkeypatch.setitem(
        ALGORITHMS, "blake3", Algorithm("blake3", SHA256.encoded, SHA256.constructor)
    )
    with pytest.raises(ManifestError, match="is a blake3 store; this store is sha256"):
        store.fill(other, [HELLO])


# Then the read path that holds.


def test_a_copy_folder_lands_locally_and_a_reference_folder_does_not(
    tmp_path: Path,
) -> None:
    mirror = _mirror(tmp_path / "m", b"hello", b"world")
    world = digest_of(b"world")
    copying = Store.create(tmp_path / "copy", sources=[FolderSource(mirror.root)])
    assert copying.fetch(HELLO) == copying.object_path(HELLO)
    assert copying.state(HELLO) == "present"
    assert copying.fetch(HELLO) == copying.object_path(HELLO)  # local now

    referring = Store.create(
        tmp_path / "ref", sources=[FolderSource(mirror.root, "reference")]
    )
    assert referring.fetch(world) == mirror.object_path(world)
    assert referring.state(world) == "absent"
    # The second fetch trusts the reference mark by size and hashes nothing.
    mirror.object_path(world).write_bytes(b"wor1d")
    assert referring.fetch(world) == mirror.object_path(world)
    mirror.object_path(world).write_bytes(b"world!")
    with pytest.raises(MissingObject):
        referring.fetch(world)


def test_an_http_tier_serves_the_layout(tmp_path: Path) -> None:
    mirror = _mirror(tmp_path / "m", b"hello")
    with _serve(mirror.root) as base:
        store = Store.create(tmp_path / "s", sources=[HttpSource(base + "/")])
        assert store.fetch(HELLO) == store.object_path(HELLO)
        assert store.read(HELLO) == b"hello"
        assert store.verified_size(HELLO) == 5


def test_an_origin_hint_answers_only_its_own_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    @contextlib.contextmanager
    def vendor(url: str, **kwargs: Any) -> Iterator[io.BytesIO]:
        calls.append(url)
        yield io.BytesIO(b"hello")

    monkeypatch.setattr(_store, "fetch_url", vendor)
    hello = OriginHint(HELLO, "https://vendor.example/hello.bin")
    other = OriginHint(digest_of(b"other"), "https://vendor.example/other.bin")
    store = Store.create(tmp_path / "s", sources=[other, hello])
    assert store.fetch(HELLO) == store.object_path(HELLO)
    assert calls == ["https://vendor.example/hello.bin"]
    assert store.read(HELLO) == b"hello"


def test_fill_builds_a_folder_that_serves_as_a_source(tmp_path: Path) -> None:
    store = Store.create(tmp_path / "s")
    digests = [store.put(b"one"), store.put(b"two")]
    target = tmp_path / "mirror"
    landed = store.fill(target, digests)
    assert [each.written for each in landed] == [True, True]
    again = store.fill(target, digests)  # an existing folder store is reused
    assert [each.written for each in again] == [False, False]
    consumer = Store.create(tmp_path / "c", sources=[FolderSource(target)])
    for digest in digests:
        assert consumer.fetch(digest) == consumer.object_path(digest)
    assert sorted(Store.open(target).objects()) == sorted(digests)


def test_the_default_progress_sink_is_silent(
    capsys: pytest.CaptureFixture[str],
) -> None:
    silent("nothing")
    assert capsys.readouterr().out == ""


def test_sources_describe_themselves(tmp_path: Path) -> None:
    assert FolderSource(tmp_path).describe() == f"folder {tmp_path} (copy)"
    assert HttpSource("http://m.example/x/").describe() == "http http://m.example/x/"
    assert HttpSource("http://m.example/x/").url("a") == "http://m.example/x/a"
    assert (
        OriginHint(HELLO, "http://v.example/h").describe()
        == "origin http://v.example/h"
    )
    assert isinstance(Digest.parse(str(HELLO)), Digest)
