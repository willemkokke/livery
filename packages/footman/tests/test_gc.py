"""The cache collector: both rules, the rails, and the daily trigger."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from livery.footman import _app, _gc, _paths


def _pair(cache: Path, stem: str, cwd: str | None, age_days: float = 0) -> None:
    """A manifest + times pair, optionally aged and optionally cwd-less."""
    manifest: dict[str, object] = {"schema": 1, "hash": stem, "tree": {}}
    if cwd is not None:
        manifest["cwd"] = cwd
    (cache / f"{stem}.json").write_text(json.dumps(manifest), encoding="utf-8")
    (cache / f"{stem}.times.json").write_text('{"schema": 1}', encoding="utf-8")
    if age_days:
        then = time.time() - age_days * 86400
        for name in (f"{stem}.json", f"{stem}.times.json"):
            os.utime(cache / name, (then, then))


def test_collect_deletes_pairs_whose_directory_is_gone(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    _pair(cache, "dead", str(tmp_path / "no-such-project"))
    _pair(cache, "alive", str(tmp_path))  # tmp_path exists: kept, any age
    removed = _gc.collect(cache)
    assert removed == 2
    assert not (cache / "dead.json").exists()
    assert not (cache / "dead.times.json").exists()
    assert (cache / "alive.json").exists()


def test_collect_ages_out_idle_pairs_without_a_cwd(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    _pair(cache, "old", None, age_days=_gc.IDLE_DAYS + 5)
    _pair(cache, "recent", None, age_days=1)
    _gc.collect(cache)
    assert not (cache / "old.json").exists()
    assert (cache / "recent.json").exists()


def test_collect_never_touches_the_invoking_pair_or_the_stamp(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    _pair(cache, "current", str(tmp_path / "gone"), age_days=400)
    (cache / _gc.STAMP).write_text("", encoding="utf-8")
    then = time.time() - 400 * 86400
    os.utime(cache / _gc.STAMP, (then, then))
    _gc.collect(cache, skip_stem="current")
    assert (cache / "current.json").exists()
    assert (cache / "current.times.json").exists()
    assert (cache / _gc.STAMP).exists()  # not a *.json; asserted anyway


def test_collect_ages_orphan_times_files(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    for stem, days in (("stale", _gc.IDLE_DAYS + 5), ("warm", 1)):
        p = cache / f"{stem}.times.json"
        p.write_text('{"schema": 1}', encoding="utf-8")
        then = time.time() - days * 86400
        os.utime(p, (then, then))
    _gc.collect(cache)
    assert not (cache / "stale.times.json").exists()
    assert (cache / "warm.times.json").exists()


def test_fetch_data_lives_by_reference_not_by_age(tmp_path):
    # The manifest layout: a live manifest pins the data file it names at
    # any age, and a data file no manifest names is unreachable (fetch
    # resolves only through manifests) — garbage immediately, however
    # young. The clock stays only where nothing references the bytes yet.
    cache = tmp_path / "cache"
    room = cache / "fetch"
    room.mkdir(parents=True)
    ancient = time.time() - (_gc.IDLE_DAYS + 5) * 86400
    (room / "aaaa.json").write_text(json.dumps({"url": "u", "data": "aaaa-1111.bin"}))
    (room / "aaaa-1111.bin").write_text("pinned")
    os.utime(room / "aaaa-1111.bin", (ancient, ancient))  # old bytes, live ref
    (room / "bbbb-2222.bin").write_text("orphan")  # written seconds ago

    _gc.collect(cache)

    assert (room / "aaaa-1111.bin").exists()  # referenced: kept at any age
    assert not (room / "bbbb-2222.bin").exists()  # unreferenced: gone now
    assert (room / "aaaa.json").exists()


def test_an_idle_fetch_manifest_releases_its_data(tmp_path):
    # Nobody asked in IDLE_DAYS (a serve would have touched the manifest):
    # the manifest goes, and the data it referenced becomes an orphan the
    # same pass sweeps.
    cache = tmp_path / "cache"
    room = cache / "fetch"
    room.mkdir(parents=True)
    ancient = time.time() - (_gc.IDLE_DAYS + 5) * 86400
    (room / "cccc.json").write_text(json.dumps({"url": "u", "data": "cccc-3333.bin"}))
    (room / "cccc-3333.bin").write_text("bytes")
    for name in ("cccc.json", "cccc-3333.bin"):
        os.utime(room / name, (ancient, ancient))

    _gc.collect(cache)

    assert not (room / "cccc.json").exists()
    assert not (room / "cccc-3333.bin").exists()


def test_collect_ages_the_fetch_room(tmp_path):
    # Rule 3: bodies and sidecars age as pairs, orphan sidecars age alone,
    # and a warm body (a recent serve touched it) survives with its sidecar.
    cache = tmp_path / "cache"
    room = cache / "fetch"
    room.mkdir(parents=True)
    then = time.time() - (_gc.IDLE_DAYS + 5) * 86400
    for name in ("stale.bin", "stale.meta.json", "orphan.meta.json"):
        p = room / name
        p.write_text("x")
        os.utime(p, (then, then))
    (room / "warm.bin").write_text("x")
    (room / "warm.meta.json").write_text("{}")
    os.utime(room / "warm.meta.json", (then, then))  # the pair's newest wins

    removed = _gc.collect(cache)

    assert removed == 3
    assert not (room / "stale.bin").exists()
    assert not (room / "stale.meta.json").exists()
    assert not (room / "orphan.meta.json").exists()
    assert (room / "warm.bin").exists()
    assert (room / "warm.meta.json").exists()


def test_collect_sweeps_abandoned_part_files(tmp_path):
    # A download in flight lives in a `.part` file beside the body it will
    # be renamed onto. One whose process was killed is read by nobody and
    # can be gigabytes, so it ages on PART_DAYS rather than the idle window
    # — and a running download, still writing, keeps its own mtime fresh.
    cache = tmp_path / "cache"
    room = cache / "fetch"
    room.mkdir(parents=True)
    (room / "abandoned.a1b2.part").write_text("half a tarball")
    then = time.time() - (_gc.PART_DAYS + 0.5) * 86400
    os.utime(room / "abandoned.a1b2.part", (then, then))
    (room / "inflight.c3d4.part").write_text("still arriving")

    assert _gc.collect(cache) == 1
    assert not (room / "abandoned.a1b2.part").exists()
    assert (room / "inflight.c3d4.part").exists()


def test_collect_tolerates_garbage_manifests(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "junk.json").write_text("not json", encoding="utf-8")
    _gc.collect(cache)  # unreadable: judged by age alone, and it's fresh
    assert (cache / "junk.json").exists()


def test_collect_reads_only_its_own_manifests_for_the_cwd_rule(tmp_path):
    # A task may keep its own state in `cache_dir()` — the docs invite it — so
    # readable JSON is not automatically a manifest. A coincidental `cwd` key
    # naming a missing path used to condemn such a file at any age, seconds
    # after it was written. Age still owns it; that half is the bargain.
    cache = tmp_path / "cache"
    cache.mkdir()
    body = json.dumps({"cwd": str(tmp_path / "gone"), "entries": []})
    (cache / "index.json").write_text(body, encoding="utf-8")
    (cache / "ancient.json").write_text(body, encoding="utf-8")
    then = time.time() - (_gc.IDLE_DAYS + 5) * 86400
    os.utime(cache / "ancient.json", (then, then))

    assert _gc.collect(cache) == 1
    assert (cache / "index.json").exists()
    assert not (cache / "ancient.json").exists()


# --- the trigger --------------------------------------------------------------


def _trigger(tmp_path, monkeypatch, cfg=None):
    cache = tmp_path / "cache"
    monkeypatch.setattr(_paths, "footman_cache_dir", lambda: cache)
    monkeypatch.delenv("FOOTMAN_NO_GC", raising=False)
    spawns: list[tuple[Path, str]] = []
    monkeypatch.setattr(_app, "_spawn_gc", lambda c, s: spawns.append((c, s)))
    _app._maybe_collect(cfg or {}, skip_stem="")
    return cache, spawns


def test_trigger_plants_the_stamp_on_a_fresh_cache(tmp_path, monkeypatch):
    cache, spawns = _trigger(tmp_path, monkeypatch)
    assert (cache / _gc.STAMP).exists()  # scheduled for tomorrow
    assert spawns == []  # short-lived caches never spawn


def test_trigger_spawns_once_the_stamp_has_aged(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    stamp = cache / _gc.STAMP
    stamp.touch()
    then = time.time() - 2 * 86400
    os.utime(stamp, (then, then))
    _, spawns = _trigger(tmp_path, monkeypatch)
    assert len(spawns) == 1
    assert stamp.stat().st_mtime > then + 86400  # re-touched before spawning


def test_the_collector_child_ignores_the_directory_it_starts_in(tmp_path, monkeypatch):
    # `-c` heads sys.path with the cwd, so without `-P` a `footman.py` in the
    # directory the run started in would answer the collector's own import.
    from livery.footman import _complete

    cmd: list[list[str]] = []
    monkeypatch.setattr(_complete, "detach", lambda c: cmd.append(list(c)))
    _app._spawn_gc(tmp_path / "cache", "stem")
    assert cmd[0][1:3] == ["-P", "-c"]
    assert "_gc.main()" in cmd[0][3]
    assert cmd[0][4:] == [str(tmp_path / "cache"), "stem"]


def test_trigger_respects_a_young_stamp(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / _gc.STAMP).touch()
    _, spawns = _trigger(tmp_path, monkeypatch)
    assert spawns == []


def test_trigger_off_switches(tmp_path, monkeypatch):
    _, spawns = _trigger(tmp_path, monkeypatch, cfg={"gc": False})
    assert spawns == []
    monkeypatch.setenv("FOOTMAN_NO_GC", "1")
    cache = tmp_path / "cache2"
    monkeypatch.setattr(_paths, "footman_cache_dir", lambda: cache)
    _app._maybe_collect({}, skip_stem="")
    assert not cache.exists()  # fully off: not even a stamp


def test_collector_runs_for_real_as_a_detached_child(tmp_path, monkeypatch):
    """The collector end to end: the actual child `_maybe_collect` spawns,
    doing the actual deleting. Everything above this drives `collect()` in
    process or fakes the spawn — this is the only test that proves the
    spawned command line, `_gc.main()`'s argv handling, and the deletion
    all agree, and it is the reason CI ever executes the collector at all.
    """
    cache = tmp_path / "cache"
    cache.mkdir()
    _pair(cache, "dead", str(tmp_path / "no-such-project"))
    _pair(cache, "keep", str(tmp_path))  # a living project: must survive
    monkeypatch.setattr(_paths, "footman_cache_dir", lambda: cache)
    stamp = cache / _gc.STAMP
    stamp.touch()
    then = time.time() - 2 * 86400  # yesterday's stamp: due for collection
    os.utime(stamp, (then, then))

    _app._maybe_collect({}, skip_stem="")  # spawns the real detached child

    # Poll for BOTH files of the pair: the child unlinks them sequentially
    # (manifest, then times), so watching the manifest alone can wake in the
    # window between the two unlinks and flake on the times assert.
    deadline = time.time() + 30

    def pair_exists():
        return (cache / "dead.json").exists() or (cache / "dead.times.json").exists()

    while pair_exists() and time.time() < deadline:
        time.sleep(0.1)
    assert not (cache / "dead.json").exists()  # the child really collected
    assert not (cache / "dead.times.json").exists()
    assert (cache / "keep.json").exists()  # and left the living project alone


def test_main_without_a_cache_dir_is_a_quiet_noop(monkeypatch):
    # A detached child spawned with no argv has nothing to collect from;
    # it must exit silently rather than guess at a directory.
    monkeypatch.setattr(sys, "argv", ["footman-gc"])
    monkeypatch.setattr(
        _gc, "collect", lambda *a: pytest.fail("collected without a cache dir")
    )
    _gc.main()
