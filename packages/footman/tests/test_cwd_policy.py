"""The cwd policy ladder: tokens, rel suffixes, overrides, per-call targets."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

from livery.footman import _app, _discover, _executor, _paths, context, registry
from livery.footman._executor import EX_USAGE
from livery.footman.context import Context, current, use_context

# --- helpers -----------------------------------------------------------------


def _task(reg=None, /, **policy) -> Any:
    """A registered no-op task on a private registry, with cwd/rel policy."""
    reg = reg if reg is not None else registry.Group("root")

    @reg.task(**policy)
    def probe():
        pass

    return probe


def _stamped(tmp_path, **policy) -> Any:
    """A task carrying a defining-dir stamp, as the cascade would tag it."""
    fn = _task(**policy)
    setattr(fn, _discover.DEFINING_DIR, str(tmp_path))
    return fn


# --- token resolution --------------------------------------------------------


def test_default_policy_is_the_taskfile(tmp_path):
    fn = _stamped(tmp_path)
    assert _executor.resolve_cwd(fn, Context()) == (tmp_path, False)


def test_taskfile_falls_back_to_root_without_a_stamp(tmp_path):
    fn = _task()  # config-mounted plugins carry no defining dir
    ctx = Context(root_dir=str(tmp_path))
    assert _executor.resolve_cwd(fn, ctx) == (tmp_path, False)


def test_nothing_known_stays_none(tmp_path):
    # Bare calls outside discovery: no stamp, no root — same as today.
    assert _executor.resolve_cwd(_task(), Context()) == (None, False)


def test_root_token(tmp_path):
    fn = _stamped(tmp_path / "pkg", cwd="root")
    ctx = Context(root_dir=str(tmp_path))
    assert _executor.resolve_cwd(fn, ctx) == (tmp_path, False)


def test_asinvoked_is_the_pinned_snapshot(tmp_path):
    fn = _stamped(tmp_path / "pkg", cwd="asinvoked")
    ctx = Context(invoked_dir=str(tmp_path / "launch"))
    assert _executor.resolve_cwd(fn, ctx) == (tmp_path / "launch", False)


def test_unmanaged_pins_live_cwd_and_flags(tmp_path):
    fn = _stamped(tmp_path, cwd="unmanaged")
    resolved, unmanaged = _executor.resolve_cwd(fn, Context())
    assert resolved == Path.cwd()
    assert unmanaged is True


def test_absolute_path_cwd(tmp_path):
    fn = _task(cwd=tmp_path / "elsewhere")
    assert _executor.resolve_cwd(fn, Context()) == (tmp_path / "elsewhere", False)


# --- the ladder --------------------------------------------------------------


def test_config_default_names_a_token(tmp_path):
    fn = _stamped(tmp_path / "pkg")
    ctx = Context(cwd_policy="root", root_dir=str(tmp_path))
    assert _executor.resolve_cwd(fn, ctx) == (tmp_path, False)


def test_config_default_can_be_an_absolute_path(tmp_path):
    fn = _stamped(tmp_path / "pkg")
    ctx = Context(cwd_policy=str(tmp_path / "fixed"))
    assert _executor.resolve_cwd(fn, ctx) == (tmp_path / "fixed", False)


def test_task_cwd_beats_the_config_default(tmp_path):
    fn = _stamped(tmp_path / "pkg", cwd="asinvoked")
    ctx = Context(
        cwd_policy="root",
        root_dir=str(tmp_path),
        invoked_dir=str(tmp_path / "launch"),
    )
    assert _executor.resolve_cwd(fn, ctx) == (tmp_path / "launch", False)


def test_opts_cwd_beats_the_task_declaration(tmp_path):
    fn = _stamped(tmp_path / "pkg", cwd="asinvoked")
    ctx = Context(root_dir=str(tmp_path), invoked_dir=str(tmp_path / "launch"))
    assert _executor.resolve_cwd(fn.opts(cwd="root"), ctx) == (tmp_path, False)


def test_opts_cwd_none_clears_the_task_declaration(tmp_path):
    # None is "no opinion" — what a caller computing an override passes, and
    # the way to drop a declared policy for this one use. It reaches the
    # validators, which must let it through rather than try `Path(None)`.
    fn = _stamped(tmp_path / "pkg", cwd="asinvoked", rel="dist")
    ctx = Context(
        cwd_policy="root",
        root_dir=str(tmp_path),
        invoked_dir=str(tmp_path / "launch"),
    )
    assert _executor.resolve_cwd(fn.opts(cwd=None, rel=None), ctx) == (tmp_path, False)


# --- rel suffixes ------------------------------------------------------------


def test_rel_appends_to_the_base(tmp_path):
    fn = _task(cwd="root", rel="dist")
    ctx = Context(root_dir=str(tmp_path))
    assert _executor.resolve_cwd(fn, ctx) == (tmp_path / "dist", False)


def test_nearer_rel_replaces_farther(tmp_path):
    fn = _task(cwd="root", rel="dist")
    ctx = Context(root_dir=str(tmp_path))
    resolved, _ = _executor.resolve_cwd(fn.opts(rel="web"), ctx)
    assert resolved == tmp_path / "web"  # replaced, never stacked


def test_rel_alone_rides_the_policy_base(tmp_path):
    fn = _stamped(tmp_path, rel="dist")
    assert _executor.resolve_cwd(fn, Context()) == (tmp_path / "dist", False)


# --- taught errors -----------------------------------------------------------


def test_relative_cwd_is_a_taught_error():
    with pytest.raises(TypeError, match="rel="):
        _task(cwd="web")


def test_absolute_rel_is_a_taught_error():
    with pytest.raises(TypeError, match="cwd="):
        _task(rel="/somewhere")


def test_rel_with_unmanaged_is_a_taught_error_at_declaration():
    with pytest.raises(TypeError, match="asinvoked"):
        _task(cwd="unmanaged", rel="dist")


def test_rel_with_unmanaged_is_a_taught_error_in_opts():
    fn = _task()
    with pytest.raises(TypeError, match="asinvoked"):
        fn.opts(cwd="unmanaged", rel="dist")


def test_unmanaged_config_with_task_rel_errors_at_resolve(tmp_path):
    # The cross-rung combination only meets at resolve time.
    fn = _stamped(tmp_path, rel="dist")
    with pytest.raises(ValueError, match="asinvoked"):
        _executor.resolve_cwd(fn, Context(cwd_policy="unmanaged"))


def test_relative_opts_cwd_is_a_taught_error():
    fn = _task()
    with pytest.raises(TypeError, match="rel="):
        fn.opts(cwd="web")


# --- body-call override + dedup ----------------------------------------------


def test_opts_cwd_overrides_a_body_call(tmp_path):
    reg = registry.Group("root")
    seen: dict[str, object] = {}

    @reg.task
    def probe():
        seen["cwd"] = current().cwd

    with use_context(Context()) as ctx:
        probe.opts(cwd=tmp_path)()
        assert seen["cwd"] == tmp_path
        assert ctx.cwd is None  # restored — a save/restore of the field


def test_opts_rel_overrides_a_body_call(tmp_path):
    reg = registry.Group("root")
    seen: dict[str, object] = {}

    @reg.task
    def probe():
        seen["cwd"] = current().cwd

    setattr(probe, _discover.DEFINING_DIR, str(tmp_path))
    with use_context(Context()):
        probe.opts(rel="web")()
        assert seen["cwd"] == tmp_path / "web"


def test_dedup_distinguishes_cwds(tmp_path):
    fn = _task()
    a = fn.opts(cwd="root")
    b = fn.opts(cwd="asinvoked")
    assert a._dedup_key() != b._dedup_key()  # two directories, two nodes
    assert a._dedup_key() == fn.opts(cwd="root")._dedup_key()


# --- per-call run(cwd=, rel=) targets ----------------------------------------


def test_target_cwd_rel_suffixes_ctx_cwd(tmp_path):
    ctx = Context(cwd=tmp_path)
    assert context._target_cwd(ctx, None, "web") == tmp_path / "web"


def test_target_cwd_explicit_cwd_wins(tmp_path):
    ctx = Context(cwd=tmp_path / "task")
    target = context._target_cwd(ctx, tmp_path / "other", "web")
    assert target == tmp_path / "other" / "web"


def test_target_cwd_absolute_rel_raises(tmp_path):
    with pytest.raises(ValueError, match="cwd="):
        context._target_cwd(Context(cwd=tmp_path), None, "/abs")


def test_target_cwd_unmanaged_spawns_from_nowhere(tmp_path):
    ctx = Context(cwd=tmp_path, cwd_unmanaged=True)
    assert context._target_cwd(ctx, None, None) is None  # child inherits live cwd


def test_target_cwd_unmanaged_with_rel_raises(tmp_path):
    ctx = Context(cwd=tmp_path, cwd_unmanaged=True)
    with pytest.raises(ValueError, match="managed base"):
        context._target_cwd(ctx, None, "web")


def test_target_cwd_per_call_unmanaged_token(tmp_path):
    # The task-level token, accepted per call: no base at all for this one
    # call, while ctx.cwd stays managed for every other call the task makes.
    ctx = Context(cwd=tmp_path)
    assert context._target_cwd(ctx, "unmanaged", None) is None


def test_run_callable_per_call_unmanaged_skips_the_check(tmp_path):
    # An in-process callable that touches no paths says so on the call: it
    # runs under the live process cwd, foreign ctx.cwd notwithstanding.
    # run(callable) retired; the token's path lives on under the tools
    # bridge's in-process lane, pinned at the machinery that serves it.
    seen: dict[str, bool] = {}
    with use_context(Context(cwd=tmp_path)) as ctx:
        target = context._target_cwd(ctx, "unmanaged", None)
        context._run_callable(
            lambda: seen.setdefault("ran", True) and 0, (), cwd=target
        )
    assert seen["ran"] is True


def test_run_callable_foreign_cwd_still_refuses_without_the_token(tmp_path):
    # The guard is not weakened: only the explicit per-call token opts out.
    with (
        use_context(Context(cwd=tmp_path)) as ctx,
        pytest.raises(ValueError, match="no longer chdirs"),
    ):
        context._run_callable(lambda: 0, (), cwd=context._target_cwd(ctx, None, None))


def test_run_subprocess_per_call_unmanaged_inherits_live_cwd(tmp_path, monkeypatch):
    # A spawned child gets cwd=None — the live process cwd, not ctx.cwd
    # (which here names a directory that does not even exist). The routers
    # are not armed here, so this is the resolution only; the token under a
    # real run is test_globals.py's
    # `test_run_is_footmans_own_spawn_and_the_injector_leaves_it_alone`.
    monkeypatch.chdir(tmp_path)
    with use_context(Context(cwd=tmp_path / "nowhere")):
        result = context.run(
            [sys.executable, "-c", "import os; print(os.getcwd())"],
            cwd="unmanaged",
        )
    assert Path(result.stdout.strip()).resolve() == tmp_path.resolve()


def test_run_per_call_unmanaged_with_rel_is_a_taught_error(tmp_path):
    with (
        use_context(Context(cwd=tmp_path)) as ctx,
        pytest.raises(ValueError, match="managed base"),
    ):
        context._target_cwd(ctx, "unmanaged", "x")


def test_serial_lane_per_call_unmanaged_is_the_applied_cwd(tmp_path):
    # Under the serial lane the task's cwd is applied with a real chdir, so
    # "the live process cwd" *is* ctx.cwd — the token stays correct there.
    seen: dict[str, str] = {}
    ctx = Context(cwd=tmp_path)
    with use_context(ctx), _executor._serial_globals(ctx):
        target = context._target_cwd(ctx, "unmanaged", None)
        context._run_callable(
            lambda: seen.setdefault("cwd", os.getcwd()) and 0, (), cwd=target
        )
    assert Path(seen["cwd"]).resolve() == tmp_path.resolve()


# --- end to end: the config default threads into a real run ------------------


def test_config_cwd_root_threads_to_the_task(tmp_path, monkeypatch, capsys):
    (tmp_path / ".git").mkdir()
    (tmp_path / "tasks.py").write_text(
        "from livery.footman import task\n", encoding="utf-8"
    )
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "tasks.py").write_text(
        "from livery.footman import task\n"
        "from livery.footman.context import current\n"
        "@task\n"
        "def where():\n"
        "    print(current().cwd)\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("FOOTMAN_CASCADE", raising=False)
    monkeypatch.setenv("FOOTMAN_CONFIG", str(tmp_path / "no-global.toml"))
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    monkeypatch.chdir(pkg)

    assert _app.run(["where"]) == 0
    out = capsys.readouterr().out
    assert str(pkg) in out  # default policy: the defining task file's dir

    (tmp_path / "pyproject.toml").write_text(
        "[tool.footman]\ncwd = 'root'\n", encoding="utf-8"
    )
    assert _app.run(["where"]) == 0
    out = capsys.readouterr().out
    assert str(pkg) not in out  # root now, not the taskfile dir
    assert str(tmp_path) in out


def test_config_cwd_rejects_a_relative_value(tmp_path, monkeypatch, capsys):
    (tmp_path / ".git").mkdir()
    (tmp_path / "tasks.py").write_text(
        "from livery.footman import task\n@task\ndef hi():\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        "[tool.footman]\ncwd = 'somewhere/relative'\n", encoding="utf-8"
    )
    monkeypatch.delenv("FOOTMAN_CASCADE", raising=False)
    monkeypatch.setenv("FOOTMAN_CONFIG", str(tmp_path / "no-global.toml"))
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    monkeypatch.chdir(tmp_path)

    assert _app.run(["hi"]) == EX_USAGE
    err = capsys.readouterr().err
    assert "policy token" in err and "rel=" in err
