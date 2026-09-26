"""The store's reads over HTTP and its unpacking: refusals and retries first."""

from __future__ import annotations

import contextlib
import io
import tarfile
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from livery.strongroom import Unreachable
from livery.toolroom.store import (
    FetchError,
    UnpackError,
    _fetch,
    api_headers,
    fetch_bytes,
    fetch_file,
    fetch_json,
    unpack,
)


class _Answer:
    """What a faked fetch_url yields: a response with a body."""

    def __init__(self, body: bytes) -> None:
        self._body = io.BytesIO(body)

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)


def _fake_fetch(
    monkeypatch: pytest.MonkeyPatch, script: list[object]
) -> list[dict[str, object]]:
    """Replace the network with *script*: an Unreachable to raise, or bytes to answer.

    Returns the calls made, each with the url and headers seen.
    """
    calls: list[dict[str, object]] = []

    @contextlib.contextmanager
    def fetch_url(url: str, **kwargs: object) -> Iterator[_Answer]:
        calls.append({"url": url, "headers": kwargs.get("headers")})
        step = script.pop(0)
        if isinstance(step, Unreachable):
            raise step
        assert isinstance(step, bytes)
        yield _Answer(step)

    monkeypatch.setattr(_fetch, "fetch_url", fetch_url)
    monkeypatch.setattr(_fetch, "sleep", lambda _seconds: None)
    return calls


def test_an_answer_is_final_and_a_transient_failure_is_tried_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A 404 is the source's answer: one call, no retry, the status kept.
    calls = _fake_fetch(monkeypatch, [Unreachable("x: HTTP 404", status=404)])
    with pytest.raises(FetchError, match="HTTP 404") as refused:
        fetch_bytes("https://x/asset")
    assert refused.value.status == 404 and len(calls) == 1
    # A 503, then a dropped connection, then the bytes: three calls.
    calls = _fake_fetch(
        monkeypatch,
        [Unreachable("x: HTTP 503", status=503), Unreachable("x: reset"), b"ok"],
    )
    assert fetch_bytes("https://x/asset") == b"ok"
    assert len(calls) == 3
    # Tries spent: the last cause is named.
    calls = _fake_fetch(monkeypatch, [Unreachable("x: reset")] * 3)
    with pytest.raises(FetchError, match="reset"):
        fetch_bytes("https://x/asset", tries=3)
    assert len(calls) == 3


def test_the_token_rides_only_to_githubs_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """The token is read from either spelling and sent to the API host alone.

    GitHub allows 60 unauthenticated API calls an hour per address and
    5,000 with a token; a shared runner spends the smaller budget on
    strangers. `gh` exports `GH_TOKEN` and Actions `GITHUB_TOKEN`, so both
    are read, `GH_TOKEN` first. No token is an offer declined, never a
    refusal: a fresh clone still reads, on the smaller budget.
    """
    # No token: the smaller budget, and still a read.
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert api_headers("https://api.github.com/rate_limit") == {
        "User-Agent": _fetch.USER_AGENT
    }
    monkeypatch.setenv("GITHUB_TOKEN", "from-actions")
    assert api_headers("https://api.github.com/x")["Authorization"] == (
        "Bearer from-actions"
    )
    monkeypatch.setenv("GH_TOKEN", "t0k")
    assert api_headers("https://api.github.com/repos/a/b/releases") == {
        "User-Agent": _fetch.USER_AGENT,
        "Authorization": "Bearer t0k",
    }
    for elsewhere in (
        "https://github.com/oven-sh/bun/releases/download/bun-v1.3.13/bun.zip",
        "https://objects.githubusercontent.com/whatever",
        "https://gitlab.com/api/v4/projects/x/releases",
        "https://pypi.org/pypi/ruff/json",
        "https://registry.npmjs.org/cspell",
    ):
        assert "Authorization" not in api_headers(elsewhere), elsewhere
    # The headers reach the seam, and a caller's own headers replace them.
    calls = _fake_fetch(monkeypatch, [b"{}", b"{}"])
    fetch_bytes("https://api.github.com/x")
    fetch_bytes("https://api.github.com/x", headers={"User-Agent": "mine"})
    assert calls[0]["headers"] == api_headers("https://api.github.com/x")
    assert calls[1]["headers"] == {"User-Agent": "mine"}


def test_json_that_is_not_json_is_a_fetch_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_fetch(monkeypatch, [b"<html>rate limited</html>"])
    with pytest.raises(FetchError, match="not with JSON"):
        fetch_json("https://api.github.com/x")
    _fake_fetch(monkeypatch, [b'{"assets": []}'])
    assert fetch_json("https://api.github.com/x") == {"assets": []}


def test_a_file_is_cached_by_name_and_a_part_write_is_no_hit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The failure first: the tries spent leave nothing behind.
    _fake_fetch(monkeypatch, [Unreachable("x: reset")] * 3)
    with pytest.raises(FetchError, match="reset"):
        fetch_file("https://x/dl/tool.zip", tmp_path / "cache")
    assert not (tmp_path / "cache" / "tool.zip").exists()
    calls = _fake_fetch(monkeypatch, [b"zip-bytes"])
    got = fetch_file("https://x/dl/tool.zip", tmp_path / "cache")
    assert got == tmp_path / "cache" / "tool.zip" and got.read_bytes() == b"zip-bytes"
    # A second ask is answered from the file: no call.
    assert fetch_file("https://x/dl/tool.zip", tmp_path / "cache") == got
    assert len(calls) == 1
    # An empty file is not a hit.
    got.write_bytes(b"")
    _fake_fetch(monkeypatch, [b"again"])
    assert (
        fetch_file("https://x/dl/tool.zip", tmp_path / "cache").read_bytes() == b"again"
    )


def test_unpack_refuses_what_it_does_not_know_and_extracts_whole(
    tmp_path: Path,
) -> None:
    with pytest.raises(UnpackError, match="not an archive"):
        unpack(tmp_path / "tool.exe", tmp_path / "out")
    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"not a zip")
    with pytest.raises(UnpackError, match="will not extract"):
        unpack(broken, tmp_path / "out")
    tar_path = tmp_path / "t.tgz"
    with tarfile.open(tar_path, "w:gz") as tar:
        for name, data in (
            ("tool-1/bin/tool", b"bin"),
            ("tool-1/bin/_internal/lib.so", b"so"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(data))
    unpack(tar_path, tmp_path / "t")
    assert (
        tmp_path / "t" / "tool-1" / "bin" / "_internal" / "lib.so"
    ).read_bytes() == b"so"
    zip_path = tmp_path / "z.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        member = zipfile.ZipInfo("tool")
        member.external_attr = 0o755 << 16
        zf.writestr(member, b"bin")
    unpack(zip_path, tmp_path / "z")
    assert (tmp_path / "z" / "tool").read_bytes() == b"bin"
    # The name decides the format when the path does not carry it.
    landed = tmp_path / "digest-object"
    landed.write_bytes(zip_path.read_bytes())
    unpack(landed, tmp_path / "named", name="z.zip")
    assert (tmp_path / "named" / "tool").is_file()
