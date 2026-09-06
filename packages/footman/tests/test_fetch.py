"""fetch(): the cache, revalidation, verification, backends, and steps."""

from __future__ import annotations

import http.server
import threading
import time
from pathlib import Path
from typing import ClassVar

import pytest

from livery.footman import _fetch, _paths
from livery.footman.context import Context, use_context

# Big enough to cross `_fetch.CHUNK` and Python's own write buffer: a body
# that fits in one buffered write is atomic by luck, and the whole point of
# the scenarios below is that nothing here runs on luck.
BODY = b"footman fetch payload\n" * 12000
SHA = "0f2c1a8ff1b0e8b2f0b1b7b2c9a0e2b7f6e5d4c3b2a1908070605040302010ff"


class _Handler(http.server.BaseHTTPRequestHandler):
    """Serves BODY with an ETag, answering conditional requests with 304.

    `mode` bends the *next* response without changing the path, because the
    scenarios need one URL to behave two ways: a warm cache is the point of
    a failed refresh, and the cache is keyed by URL.
    """

    protocol_version = "HTTP/1.1"  # the "chunked" mode needs it
    etag = '"v1"'
    mode = ""  # "", "short", "chunked", or "stall"
    body_override = b""  # a different payload, for the changed-upstream case
    hits: ClassVar[list[str]] = []
    stalled = threading.Event()
    release = threading.Event()

    def do_GET(self):  # BaseHTTPRequestHandler's spelling, not ours
        type(self).hits.append(self.headers.get("If-None-Match") or "unconditional")
        if self.headers.get("If-None-Match") == type(self).etag:
            self.send_response(304)
            self.end_headers()
            return
        if self.path.endswith("missing.bin"):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        mode = type(self).mode
        if mode == "chunked":
            # A chunk stream that stops before its terminator. The client's
            # own framing catches this one and raises; the two below are the
            # shapes that arrive looking perfectly well-formed.
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("ETag", type(self).etag)
            self.end_headers()
            half = BODY[: len(BODY) // 2]
            self.wfile.write(f"{len(half):x}\r\n".encode() + half + b"\r\n")
            self.wfile.write(b"ffff\r\n")  # a chunk header with no chunk
            self.close_connection = True
            return
        payload = type(self).body_override or BODY
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("ETag", type(self).etag)
        self.end_headers()
        if mode == "":
            self.wfile.write(payload)
            return
        if mode == "short":
            # Half a body and a dropped connection: the shape CPython reads
            # without a word, and the shape curl calls exit 18.
            self.wfile.write(BODY[: len(BODY) // 2])
            self.close_connection = True
            return
        if mode == "stall":
            self.wfile.write(BODY[: len(BODY) // 2])
            self.wfile.flush()
            type(self).stalled.set()
            type(self).release.wait(20)
            self.wfile.write(BODY[len(BODY) // 2 :])
            return
        self.wfile.write(BODY)

    def log_message(self, format, *args):  # the base class's spelling
        pass  # keep the test output clean


@pytest.fixture
def server(tmp_path, monkeypatch):
    """A local HTTP server plus an isolated footman cache."""
    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "cache"))
    _Handler.hits = []
    _Handler.mode = ""
    _Handler.body_override = b""
    _Handler.etag = '"v1"'
    _Handler.stalled = threading.Event()
    _Handler.release = threading.Event()
    # Threaded: a stalled request must not hold up the one racing it.
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}/file.bin"
    _Handler.release.set()  # never leave a parked handler behind
    httpd.shutdown()


# --- the backend conformance table --------------------------------------------
#
# Naming a backend picks a socket, not a behaviour: all four revalidate with
# the same headers, refuse a body that arrived short, keep a good cached copy
# when a refresh dies, and land the finished file with the same rename. One
# set of scenarios, run against every backend installed here — the missing
# ones skip, and CI installs them all. Nothing exercised curl's revalidation
# before this table, and nothing could: it never stored a validator to send.

BACKENDS = ["urllib", "curl", "httpx", "requests"]


def _skip_unless_available(backend: str) -> None:
    if not _fetch._available(backend):
        pytest.skip(f"{backend} is not available here")


@pytest.mark.parametrize("backend", BACKENDS)
def test_every_backend_downloads(server, backend):
    """Each backend, driven against a real server — the adapter code and
    the library's actual call signature, not a stand-in for either."""
    _skip_unless_available(backend)
    path = _fetch.fetch(server, backend=backend)
    assert path.read_bytes() == BODY


@pytest.mark.parametrize("backend", BACKENDS)
def test_every_backend_verifies_the_checksum(server, backend):
    _skip_unless_available(backend)
    with pytest.raises(_fetch.FetchError, match="sha256 mismatch"):
        _fetch.fetch(server, backend=backend, sha256=SHA)


@pytest.mark.parametrize("backend", BACKENDS)
def test_every_backend_refuses_a_404(server, backend):
    """A missing file is a taught FetchError, whatever fetched it — not a
    library-specific exception leaking through."""
    _skip_unless_available(backend)
    with pytest.raises(_fetch.FetchError, match="fetch: "):
        _fetch.fetch(server.replace("file.bin", "missing.bin"), backend=backend)


@pytest.mark.parametrize("backend", BACKENDS)
def test_every_backend_revalidates_with_the_etag(server, backend):
    """A warm cache revalidates: the second call carries If-None-Match, the
    server answers 304, and the receipt says cached. curl used to be outside
    this — it threw its headers away, so it stored no validator to send and
    could never receive a 304 at all."""
    _skip_unless_available(backend)
    _fetch.fetch(server, backend=backend)
    with use_context(Context(fetch_backend=backend)) as ctx:
        assert _fetch.fetch(server).read_bytes() == BODY
    assert _Handler.hits == ["unconditional", '"v1"']
    assert ctx.steps[-1].stdout == "cached"


# Two ways a transfer dies mid-body: one the client's own framing catches
# and raises about, one it reads to the end and calls a complete file.
DEATHS = ["short", "chunked"]


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("death", DEATHS)
def test_every_backend_refuses_a_truncated_body(server, backend, death):
    """A body that ends early is a failure, not a cache entry. Cached, it
    would be permanent: the ETag off that same response goes in the sidecar
    and the healthy origin answers 304 forever, so the half file is served
    as a hit until someone deletes the cache by hand."""
    _skip_unless_available(backend)
    _Handler.mode = death
    with pytest.raises(_fetch.FetchError, match="fetch: "):
        _fetch.fetch(server, backend=backend)
    assert not _fetch._manifest_path(server).exists()  # nothing published
    assert not list(_fetch.cache_dir().glob("*.bin"))  # and no data landed


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("death", DEATHS)
def test_every_backend_keeps_the_cached_copy_when_a_refresh_dies(
    server, backend, death
):
    """The documented offline fallback, on a transfer that dies mid-body
    rather than one that never connects. The good copy has to survive being
    refreshed onto — and the failure has to arrive as a FetchError, or the
    fallback never gets its say and the library's own exception escapes."""
    _skip_unless_available(backend)
    assert _fetch.fetch(server, backend=backend).read_bytes() == BODY
    _Handler.mode = death
    with use_context(Context(fetch_backend=backend)) as ctx:
        assert _fetch.fetch(server, refresh=True).read_bytes() == BODY
    assert ctx.steps[-1].stdout == "cached"


@pytest.mark.parametrize("backend", BACKENDS)
def test_every_backend_leaves_the_cache_alone_while_downloading(server, backend):
    """Two tasks, one cold URL: the copy the first was handed stays whole
    while the second streams. Downloading onto the cache path truncated it to
    zero the moment the second `open` landed, and the run reported ok —
    `sha256=` then blamed the server for bytes that arrived intact.

    The stall is server-side; when the client reaches its own write is not,
    so the file is watched across a window rather than sampled once.
    """
    _skip_unless_available(backend)
    body = _fetch.fetch(server, backend=backend)
    assert body.read_bytes() == BODY

    _Handler.mode = "stall"
    got: list[object] = []

    def sibling() -> None:
        try:
            got.append(_fetch.fetch(server, backend=backend, refresh=True))
        except Exception as exc:  # surfaced by the assert below, not swallowed
            got.append(exc)

    thread = threading.Thread(target=sibling)
    thread.start()
    try:
        assert _Handler.stalled.wait(20)
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            assert body.read_bytes() == BODY
            time.sleep(0.01)
    finally:
        _Handler.release.set()
        thread.join(20)
    assert got and isinstance(got[0], Path) and got[0].read_bytes() == BODY


@pytest.mark.parametrize("backend", BACKENDS)
def test_every_backend_leaves_no_scratch_behind(server, backend):
    """A download in flight lives in a `.part` file; a finished one does not.
    Nothing that succeeded, 304'd, or failed may leave one lying about."""
    _skip_unless_available(backend)
    _fetch.fetch(server, backend=backend)
    _fetch.fetch(server, backend=backend)  # a 304
    _Handler.mode = "short"
    with pytest.raises(_fetch.FetchError):
        _fetch.fetch(server, backend=backend, refresh=True, sha256=SHA)
    assert list(_fetch.cache_dir().glob("*.part")) == []


# --- the cache, the receipt, and the rest -------------------------------------


def test_fetch_downloads_and_caches(server):
    path = _fetch.fetch(server)
    assert path.read_bytes() == BODY
    assert path.parent == _fetch.cache_dir()


def test_second_fetch_revalidates_instead_of_redownloading(server):
    _fetch.fetch(server)
    path = _fetch.fetch(server)
    assert path.read_bytes() == BODY
    # The second request carried the ETag and got a 304 — cached, honestly.
    assert _Handler.hits == ["unconditional", '"v1"']


def test_refresh_skips_revalidation(server):
    _fetch.fetch(server)
    _fetch.fetch(server, refresh=True)
    assert _Handler.hits == ["unconditional", "unconditional"]


def test_a_returned_path_is_immutable_across_a_changed_upstream(server):
    # The layout's core promise: the data file is content-addressed and
    # never replaced, so a path handed to a caller keeps its bytes even
    # after the upstream changes — the fresh download publishes a new name
    # and only the manifest moves. Under the old <key>.bin layout the
    # refresh rewrote the very file the first caller was still holding.
    first = _fetch.fetch(server)
    assert first.read_bytes() == BODY
    _Handler.etag = '"v2"'
    _Handler.body_override = b"changed payload\n" * 12000
    second = _fetch.fetch(server, refresh=True)
    assert second != first  # a new name for new bytes
    assert first.read_bytes() == BODY  # the old handle kept its bytes
    assert second.read_bytes() == b"changed payload\n" * 12000
    meta = _fetch._load_meta(_fetch._manifest_path(server))
    assert meta["data"] == second.name  # the manifest points at the new one
    assert meta["sha256"] == _fetch._digest(second)  # validators sit beside


def test_into_copies_to_a_chosen_path(server, tmp_path):
    dest = tmp_path / "vendor" / "file.bin"
    path = _fetch.fetch(server, into=dest)
    assert path == dest and dest.read_bytes() == BODY
    # The cache genuinely keeps its copy — the old assertion here ended in
    # `or True` and could not fail (audit, suite pass), and the name it
    # checked was the *destination's*, which the cache never used.
    meta = _fetch._load_meta(_fetch._manifest_path(server))
    cached = _fetch._cached_body(server, meta)
    assert cached is not None and cached.read_bytes() == BODY


def test_sha256_mismatch_is_refused(server):
    with pytest.raises(_fetch.FetchError, match="sha256 mismatch"):
        _fetch.fetch(server, sha256=SHA)


def test_sha256_match_passes(server):
    import hashlib

    digest = hashlib.sha256(BODY).hexdigest()
    assert _fetch.fetch(server, sha256=digest).read_bytes() == BODY


def test_fetch_records_a_step(server):
    with use_context(Context()) as ctx:
        _fetch.fetch(server)
    (step,) = ctx.steps
    assert step.command == f"fetch {server}"
    assert step.code == 0


def test_a_download_is_never_reported_cached(server):
    # `cached` used to derive from "no validators came back", which read a
    # curl download — validator-less back then — as cached on the first-ever
    # fetch. Downloaded and cached are separate facts now, on every backend.
    _skip_unless_available("curl")
    with use_context(Context(fetch_backend="curl")) as ctx:
        _fetch.fetch(server)
        assert ctx.steps[-1].stdout == ""  # a real download says so
        _fetch.fetch(server, refresh=True)
        assert ctx.steps[-1].stdout == ""  # forced past the ETag: still moved


def test_a_revalidated_serve_counts_as_use(server):
    # The collector's idle rule reads mtimes and a 304 writes nothing of its
    # own — the serve itself must keep the pair warm, or a daily-fetched file
    # would age out at IDLE_DAYS and force a pointless re-download.
    import os
    import time as _time

    with use_context(Context()) as ctx:
        body = _fetch.fetch(server)
        manifest = _fetch._manifest_path(server)
        then = _time.time() - 400
        os.utime(body, (then, then))
        os.utime(manifest, (then, then))
        _fetch.fetch(server)
        assert ctx.steps[-1].stdout == "cached"
    assert body.stat().st_mtime > then + 100
    assert manifest.stat().st_mtime > then + 100


def test_dry_run_downloads_nothing(server, capsys):
    with use_context(Context(dry_run=True)) as ctx:
        path = _fetch.fetch(server)
    assert not path.exists()  # nothing downloaded
    assert _Handler.hits == []  # the server was never touched
    assert ctx.steps[0].command == f"fetch {server}"  # but the plan records it
    assert f"$ fetch {server}" in capsys.readouterr().out


def test_unknown_backend_is_taught():
    with pytest.raises(_fetch.FetchError, match="unknown backend"):
        _fetch._resolve_backend("wget")


def test_missing_library_backend_names_the_fix(monkeypatch):
    monkeypatch.setattr(_fetch, "_available", lambda name: name == "urllib")
    with pytest.raises(_fetch.FetchError, match=r"not installed.*pip install httpx"):
        _fetch._resolve_backend("httpx")


def test_auto_picks_the_first_available(monkeypatch):
    monkeypatch.setattr(_fetch, "_available", lambda name: name in ("urllib", "curl"))
    assert _fetch._resolve_backend("auto") == "urllib"  # ahead of curl in order


@pytest.mark.skipif(_fetch.shutil.which("curl") is None, reason="curl is not on PATH")
def test_curl_backend_downloads(server):
    path = _fetch.fetch(server, backend="curl")
    assert path.read_bytes() == BODY


@pytest.mark.skipif(_fetch.shutil.which("curl") is None, reason="curl is not on PATH")
def test_curl_is_footmans_own_spawn_and_draws_no_note(server, capfd):
    # The curl child is footman working on the body's behalf. With the Popen
    # injector armed (a managed parallel task), it used to be attributed to
    # the task — "spawns via raw subprocess — prefer run()" told a fetch()
    # caller to prefer what they never left, and notes are teach-once per
    # task and kind, so the false one swallowed any real one to come.
    from livery.footman import _globals

    _globals.install()
    try:
        with use_context(Context(in_task=True)):
            path = _fetch.fetch(server, backend="curl")
    finally:
        _globals.uninstall()
    assert path.read_bytes() == BODY
    assert "raw subprocess" not in capfd.readouterr().err  # nothing to teach


def test_curl_reads_the_last_response_in_the_dump():
    """A redirect or a retry appends another block; only the last one
    describes the bytes that landed in the file."""
    dump = (
        'HTTP/1.1 301 Moved Permanently\r\nETag: "old"\r\nLocation: /x\r\n\r\n'
        'HTTP/1.1 200 OK\r\nETag: "new"\r\nLast-Modified: Mon, 01 Jan 2035\r\n\r\n'
    )
    code, headers = _fetch._last_response(dump)
    assert code == 200
    assert headers["etag"] == '"new"'
    assert headers["last-modified"] == "Mon, 01 Jan 2035"


def test_backend_comes_from_the_config_ladder(server, monkeypatch):
    seen = {}

    def spy(backend, url, dest, meta):
        seen["backend"] = backend
        dest.write_bytes(BODY)
        return True, {}  # downloaded, no validators — the _download contract

    monkeypatch.setattr(_fetch, "_download", spy)
    with use_context(Context(fetch_backend="curl")):
        assert _fetch.fetch(server).read_bytes() == BODY
    assert seen["backend"] == "curl"  # [fetch] backend, not the default


def test_cached_copy_survives_a_failed_refresh(server, monkeypatch):
    _fetch.fetch(server)  # warm

    def boom(*args, **kwargs):
        raise _fetch.FetchError("fetch: network is down")

    monkeypatch.setattr(_fetch, "_download", boom)
    assert _fetch.fetch(server).read_bytes() == BODY  # offline, still works


def test_fetch_reports_byte_progress(server, monkeypatch):
    reports: list[tuple[int, int]] = []
    monkeypatch.setattr(
        _fetch.context, "progress", lambda done, total=0: reports.append((done, total))
    )
    _fetch.fetch(server)
    assert (len(BODY), len(BODY)) in reports  # counted progress, from bytes
    assert reports[-1] == (0, 0)  # and cleared when the download finished


def test_cache_lives_where_footman_caches(server, tmp_path):
    assert _fetch.cache_dir().is_relative_to(_paths.footman_cache_dir())
    assert Path(_fetch.fetch(server)).is_relative_to(_paths.footman_cache_dir())


# --- the teaching paths -------------------------------------------------------

# fetch()'s refusals and fallbacks for a machine that can't reach the URL
# were never executed by the suite (audit, suite pass): the named-but-missing
# backend, the corporate-proxy teaching on a dead connection, and the
# documented offline behaviour — a cached copy beats a failed refresh.


def test_a_named_but_missing_backend_is_a_taught_refusal(monkeypatch):
    monkeypatch.setattr(_fetch, "_available", lambda name: False)
    with pytest.raises(_fetch.FetchError, match="not installed"):
        _fetch._resolve_backend("httpx")
    with pytest.raises(_fetch.FetchError, match="not on PATH"):
        _fetch._resolve_backend("curl")
    with pytest.raises(_fetch.FetchError, match="choose one of"):
        _fetch._resolve_backend("wget")


def test_a_dead_connection_teaches_the_curl_escape(server, monkeypatch):
    import urllib.error
    import urllib.request

    def down(*a, **kw):
        raise urllib.error.URLError("network unreachable")

    monkeypatch.setattr(urllib.request, "urlopen", down)
    with pytest.raises(_fetch.FetchError) as caught:
        _fetch.fetch(server, backend="urllib")
    said = str(caught.value)
    assert 'backend = "curl"' in said  # the corporate-proxy escape, named
    assert "network unreachable" in said


def test_a_cached_copy_beats_a_failed_refresh(server, monkeypatch):
    import urllib.error
    import urllib.request

    first = _fetch.fetch(server, backend="urllib")
    body = first.read_bytes()

    def down(*a, **kw):
        raise urllib.error.URLError("offline now")

    monkeypatch.setattr(urllib.request, "urlopen", down)
    again = _fetch.fetch(server, backend="urllib", refresh=True)
    assert again.read_bytes() == body
