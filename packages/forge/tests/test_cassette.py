"""Record against a loopback server, scrub, replay with no server at all."""

from __future__ import annotations

import contextlib
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from livery.forge.testing import (
    Cassette,
    CassetteError,
    RecordingOpener,
    ReplayOpener,
)

SECRET = "sekrit-token-value"


class _Handler(BaseHTTPRequestHandler):
    def _reply(self, status: int, payload: dict[str, str]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.startswith("/missing"):
            self._reply(404, {"message": "not found"})
        else:
            # The server echoes the secret so scrubbing of response
            # bodies is exercised, not just URLs.
            self._reply(200, {"login": "someone", "token": SECRET})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        self._reply(201, {"created": "yes"})

    def log_message(self, format: str, *args: object) -> None:
        pass


def _requests(base: str, token: str) -> list[urllib.request.Request]:
    return [
        urllib.request.Request(
            f"{base}/user?token={token}",
            headers={"Authorization": f"token {token}"},
        ),
        urllib.request.Request(
            f"{base}/repos",
            data=json.dumps({"name": "demo", "token": token}).encode(),
            method="POST",
            headers={"Authorization": f"token {token}"},
        ),
        urllib.request.Request(f"{base}/missing"),
    ]


def _record(tmp_path: Path) -> tuple[Path, str]:
    """Record the three exchanges; return the cassette path and the base URL."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        cassette = Cassette()
        opener = RecordingOpener(cassette, secrets=(SECRET,))
        first, second, missing = _requests(base, SECRET)
        assert opener.open(first).status == 200
        assert opener.open(second).status == 201
        with pytest.raises(urllib.error.HTTPError) as refusal:
            opener.open(missing)
        assert refusal.value.code == 404
        path = tmp_path / "session.json"
        cassette.save(path)
        return path, base
    finally:
        server.shutdown()
        thread.join()


def test_recording_scrubs_every_secret(tmp_path: Path) -> None:
    path, _ = _record(tmp_path)
    raw = path.read_text("utf-8")
    assert SECRET not in raw
    assert "Authorization" not in raw
    assert raw.count("REDACTED") >= 3  # the URL, the request body, the response


def test_replay_answers_without_a_server(tmp_path: Path) -> None:
    # The server is gone by now: the recorded base still names it, and
    # nothing answers there. Every response comes from the file.
    path, base = _record(tmp_path)
    replay = ReplayOpener(Cassette.load(path), secrets=("dummy",))
    first, second, missing = _requests(base, "dummy")
    response = replay.open(first)
    assert response.status == 200
    assert json.loads(response.read())["token"] == "REDACTED"
    assert replay.open(second).status == 201
    with pytest.raises(urllib.error.HTTPError) as refusal:
        replay.open(missing)
    assert refusal.value.code == 404
    replay.verify_exhausted()


def test_replay_names_a_mismatched_request(tmp_path: Path) -> None:
    path, base = _record(tmp_path)
    replay = ReplayOpener(Cassette.load(path), secrets=("dummy",))
    with pytest.raises(CassetteError, match="replay mismatch"):
        replay.open(urllib.request.Request(f"{base}/other"))


def test_replay_names_a_request_past_the_end(tmp_path: Path) -> None:
    path, base = _record(tmp_path)
    replay = ReplayOpener(Cassette.load(path), secrets=("dummy",))
    for request in _requests(base, "dummy"):
        with contextlib.suppress(urllib.error.HTTPError):
            replay.open(request)
    with pytest.raises(CassetteError, match="exhausted"):
        replay.open(urllib.request.Request(f"{base}/user?token=dummy"))


def test_an_unreplayed_exchange_is_reported(tmp_path: Path) -> None:
    path, _ = _record(tmp_path)
    replay = ReplayOpener(Cassette.load(path), secrets=("dummy",))
    with pytest.raises(CassetteError, match="never replayed"):
        replay.verify_exhausted()


def test_a_foreign_format_number_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "future.json"
    path.write_text(json.dumps({"format": 999, "exchanges": []}), "utf-8")
    with pytest.raises(CassetteError, match="format"):
        Cassette.load(path)
