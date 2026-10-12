"""The uv cache doctor: a bad limit refuses, an oversize cache is pruned under --fix."""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.workshop import _doctor


def _workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: str = ""
) -> Path:
    root = tmp_path / "ws"
    root.mkdir(parents=True)
    line = f'uv-cache-limit = "{limit}"\n' if limit else ""
    root.joinpath("workshop.toml").write_text(
        f'[workspace]\nname = "acme"\nnamespace = "acme"\n{line}'
    )
    monkeypatch.setattr(
        "livery.workshop._extensions.workspace_root", lambda start=None: root
    )
    monkeypatch.setattr(_doctor, "workspace_root", lambda start=None: root)
    return root


def _cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, megabytes: int) -> Path:
    cache = tmp_path / "uv-cache"
    (cache / "archive-v0").mkdir(parents=True)
    for n in range(megabytes):
        (cache / "archive-v0" / f"wheel-{n}").write_bytes(b"\0" * 1024 * 1024)
    monkeypatch.setattr(_doctor, "uv_cache_dir", lambda: cache)
    return cache


def test_sizes_parse_and_show_by_their_unit() -> None:
    with pytest.raises(ValueError, match="not a size"):
        _doctor.parse_size("ten gigs")
    with pytest.raises(ValueError, match="not a size"):
        _doctor.parse_size("10")
    assert _doctor.parse_size("10G") == 10 * 1024**3
    assert _doctor.parse_size("1.5 GB") == int(1.5 * 1024**3)
    assert _doctor.parse_size("500m") == 500 * 1024**2
    assert _doctor.show_size(47 * 1024**3 + 200 * 1024**2) == "47.2G"
    assert _doctor.show_size(725 * 1024**2) == "725.0M"
    assert _doctor.show_size(12) == "12B"


def test_a_bad_limit_in_the_contract_stands_naming_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch, limit="plenty")
    _cache(tmp_path, monkeypatch, 1)
    found = _doctor.uv_cache()
    assert [f.what for f in found] == ["uv cache"]
    assert found[0].found.startswith(
        "[workspace] uv-cache-limit: 'plenty' is not a size"
    )
    assert "workshop.toml" in found[0].fix and found[0].repair is None


def test_an_oversize_cache_stands_with_the_prune_and_the_limit_as_the_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch, limit="3M")
    cache = _cache(tmp_path, monkeypatch, 5)
    pruned: list[str] = []

    def prune() -> None:
        pruned.append("pruned")
        for wheel in sorted(cache.glob("archive-v0/wheel-*"))[:4]:
            wheel.unlink()

    monkeypatch.setattr(_doctor, "prune_uv_cache", prune)
    found = _doctor.uv_cache()
    assert [(f.what, f.found) for f in found] == [
        ("uv cache", "5.0M, over the 3M limit")
    ]
    assert "uv cache prune" in found[0].fix and "uv-cache-limit" in found[0].fix
    assert found[0].repair is not None
    found[0].repair()
    assert pruned == ["pruned"]
    under = [f.what for f in _doctor.uv_cache()]
    assert under == ["uv cache: 1.0M, under the 3M limit"]
    # The default when the contract says nothing, and a raised limit.
    _workspace(tmp_path / "quiet", monkeypatch)
    _cache(tmp_path / "quiet", monkeypatch, 2)
    quiet = [f.what for f in _doctor.uv_cache()]
    assert quiet == ["uv cache: 2.0M, under the 10G limit"]
    _workspace(tmp_path / "raised", monkeypatch, limit="20G")
    raised = [f.what for f in _doctor.uv_cache()]
    assert raised == ["uv cache: 2.0M, under the 20G limit"]


def test_a_uv_that_cannot_say_where_its_cache_is_stands_naming_the_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)

    def missing() -> Path:
        raise RuntimeError("uv cache dir exited 127: not found")

    monkeypatch.setattr(_doctor, "uv_cache_dir", missing)
    found = _doctor.uv_cache()
    assert found[0].found.startswith("uv cache dir exited 127")
    assert "fm sync" in found[0].fix
