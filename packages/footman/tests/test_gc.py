"""The cache collector: both rules, the rails, and the daily trigger."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

import livery.footman as footman
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


def test_collect_sweeps_a_profile_box_its_run_never_took_away(tmp_path):
    # A profiled run's box holds what its children dropped, and the run's
    # writer removes it at the end. A run replaced by an exec, or killed,
    # runs no exit handler and leaves the box: the directory is the clock,
    # and a child writing a fragment into it keeps that clock fresh.
    cache = tmp_path / "cache"
    room = cache / "profiles"
    (room / "abandoned").mkdir(parents=True)
    (room / "abandoned" / "fm-4242-1.json").write_text("{}", encoding="utf-8")
    then = time.time() - (_gc.BOX_DAYS + 0.5) * 86400
    os.utime(room / "abandoned", (then, then))
    (room / "live").mkdir()
    (room / "live" / "fm-4243-1.json").write_text("{}", encoding="utf-8")

    assert _gc.collect(cache) == 1
    assert not (room / "abandoned").exists()
    assert (room / "live" / "fm-4243-1.json").exists()


def test_collect_sweeps_a_scratch_directory_whose_owner_is_gone(tmp_path):
    # Kept first: a live owner's directory, and a name carrying no pid,
    # which is nobody's leftovers footman can vouch for. Then a dead
    # owner's goes whole: a kill or an exec ran no handler to remove it.
    cache = tmp_path / "cache"
    room = cache / _gc.SCRATCH
    live = room / f"shell-{os.getpid()}-abc123"
    live.mkdir(parents=True)
    (live / "rc").write_text("x", encoding="utf-8")
    stranger = room / "notes"
    stranger.mkdir()
    (stranger / "keep").write_text("x", encoding="utf-8")
    ended = subprocess.Popen([sys.executable, "-c", "pass"])
    ended.wait()
    dead = room / f"fm-replay-{ended.pid}-x1y2z3"
    (dead / "tree").mkdir(parents=True)
    (dead / "tree" / "file").write_text("x", encoding="utf-8")

    assert _gc.collect(cache) == 1
    assert (live / "rc").exists() and (stranger / "keep").exists()
    assert not dead.exists()
    # A second sweep finds nothing to do.
    assert _gc.collect(cache) == 0


def test_a_scratch_name_carries_its_owner_or_names_nobody():
    assert _gc.owner("fm-replay-4242-x1y2z3") == 4242
    assert _gc.owner("shell-7-abc") == 7
    assert _gc.owner("notes") is None
    assert _gc.owner("a-b-c") is None
    assert _gc.owner("4242") is None


def test_scratch_dir_lands_under_the_cache_named_for_this_process():
    # The refusal first: a label is a name a reader of the cache can
    # read, and the owner's pid must parse out of it.
    with pytest.raises(ValueError, match="scratch label"):
        footman.scratch_dir("")
    with pytest.raises(ValueError, match="scratch label"):
        footman.scratch_dir("a label/with slash")
    first = footman.scratch_dir("fm-replay")
    second = footman.scratch_dir("fm-replay")
    assert first.is_dir() and second.is_dir() and first != second
    assert first.parent == footman.cache_dir() / _gc.SCRATCH
    assert _gc.owner(first.name) == os.getpid()
    assert first.name.startswith(f"fm-replay-{os.getpid()}-")


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


def test_a_run_whose_directory_is_gone_skips_the_collector(tmp_path, monkeypatch):
    # fm submit merges, removes the worktree it ran from, and reaches
    # the exit path standing in a directory that no longer exists.
    # Reading the working directory raises there; the collection is
    # skipped and the run keeps the verdict it earned.
    scheduled: list[str] = []
    monkeypatch.setattr(
        _app, "_maybe_collect", lambda cfg, stem: scheduled.append(stem)
    )

    def _gone() -> Path:
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(_app.Path, "cwd", staticmethod(_gone))
    _app._collect_after_run({}, global_mode=False)  # returns, raises nothing
    assert scheduled == []
    # The global mode names the shared manifest and never reads the
    # working directory, so it schedules as usual.
    _app._collect_after_run({}, global_mode=True)
    assert scheduled == [_paths.global_manifest_path().stem]


def test_a_run_whose_installation_is_gone_skips_the_collector(tmp_path, monkeypatch):
    # A submit removes the worktree it ran from, editable install included,
    # and the exit path cannot import the spawner any more: the collection
    # is skipped, never a traceback after a merge.
    monkeypatch.setitem(sys.modules, "livery.footman._complete", None)
    monkeypatch.delattr(footman, "_complete", raising=False)
    _app._spawn_gc(tmp_path / "cache", "stem")  # returns, raises nothing


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


def test_a_process_is_alive_while_it_runs_and_gone_once_it_ended():
    # Nobody first: a pid of zero or less names no process. Then a child
    # that ended, then this process.
    assert footman.alive(0) is False
    assert footman.alive(-1) is False
    ended = subprocess.Popen([sys.executable, "-c", "pass"])
    ended.wait()
    assert footman.alive(ended.pid) is False
    assert footman.alive(os.getpid()) is True


def test_the_windows_probe_reads_the_handle_and_the_exit_code():
    class Kernel32:
        def __init__(self, handle: int, code: int | None) -> None:
            self.handle, self.code = handle, code
            self.closed: list[int] = []

        def OpenProcess(self, access: int, inherit: bool, pid: int) -> int:
            assert access == 0x1000 and inherit is False and pid == 4242
            return self.handle

        def GetExitCodeProcess(self, handle: int, out: Any) -> int:
            if self.code is None:
                return 0
            getattr(out, "_obj").value = self.code
            return 1

        def CloseHandle(self, handle: int) -> None:
            self.closed.append(handle)

    def probe(dll: Kernel32, error: int = 0) -> bool:
        return _gc._alive_windows(4242, kernel32=dll, last_error=lambda: error)

    # No handle: access denied is a process that exists; anything else
    # is none.
    assert probe(Kernel32(0, None), error=5) is True
    assert probe(Kernel32(0, None), error=87) is False
    # A handle: still active is alive, any other exit code is gone, a
    # query that fails reads as alive, and the handle closes each time.
    for dll, expected in ((Kernel32(7, 259), True), (Kernel32(7, 0), False)):
        assert probe(dll) is expected
        assert dll.closed == [7]
    failing = Kernel32(7, None)
    assert probe(failing) is True
    assert failing.closed == [7]
    if sys.platform == "win32":
        assert _gc._alive_windows(os.getpid()) is True
    else:
        with pytest.raises(AttributeError):
            _gc._alive_windows(os.getpid())
