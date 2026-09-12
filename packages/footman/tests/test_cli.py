"""The console-script entry and the completion CLI dispatch."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

import livery.footman as footman
from livery.footman import _complete
from livery.footman._complete import complete_cli


def _completion_names(out: str) -> set[str]:
    """Candidate names from resolver output, dropping `\t` description columns."""
    return {line.split("\t", 1)[0] for line in out.splitlines() if line}


def test_complete_cli_reads_explicit_manifest(tree, tmp_path, capsys):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"schema": _complete._SCHEMA, "tree": tree}))
    assert complete_cli(["--manifest", str(path), "--", "docs."]) == 0
    assert _completion_names(capsys.readouterr().out) == {"docs.serve", "docs.build"}


def test_complete_cli_missing_manifest_is_silent(tmp_path, capsys):
    assert complete_cli(["--manifest", str(tmp_path / "none.json"), "--", ""]) == 0
    assert capsys.readouterr().out == ""


def test_complete_cli_empty_partial_appends_blank(tree, tmp_path, capsys):
    # F16: pwsh drops the trailing "" arg, so its hook passes --empty-partial and
    # the resolver appends the "" itself — completing the fresh position, not the
    # previous word. `--empty-partial` (no trailing "") == "check" + "".
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"schema": _complete._SCHEMA, "tree": tree}))
    args = ["--manifest", str(path), "--empty-partial", "--", "check"]
    assert complete_cli(args) == 0
    assert "docs." in _completion_names(capsys.readouterr().out)


# --- stale-while-revalidate completion refresh (D18) --------------------------


def _aged_manifest(tree, tmp_path, max_age, age_s=3600):
    path = tmp_path / "m.json"
    path.write_text(
        json.dumps(
            {"schema": _complete._SCHEMA, "tree": tree, "completion_max_age": max_age}
        )
    )
    when = time.time() - age_s
    os.utime(path, (when, when))
    return path


def test_swr_fresh_manifest_does_not_spawn(tree, tmp_path, monkeypatch):
    spawns: list[int] = []
    monkeypatch.setattr(
        _complete,
        "_spawn_refresh",
        lambda override=None, spawn_in=None: spawns.append(1),
    )
    path = tmp_path / "m.json"
    path.write_text(
        json.dumps(
            {"schema": _complete._SCHEMA, "tree": tree, "completion_max_age": 600}
        )
    )  # just now
    complete_cli(["--manifest", str(path), "--", ""])
    assert spawns == []


def test_swr_aged_manifest_spawns_and_bumps_mtime(tree, tmp_path, monkeypatch):
    spawns: list[int] = []
    monkeypatch.setattr(
        _complete,
        "_spawn_refresh",
        lambda override=None, spawn_in=None: spawns.append(1),
    )
    path = _aged_manifest(tree, tmp_path, 600)
    complete_cli(["--manifest", str(path), "--", ""])
    assert spawns == [1]
    assert time.time() - os.stat(path).st_mtime < 60  # mtime bumped to ~now


def test_swr_disabled_never_spawns(tree, tmp_path, monkeypatch):
    spawns: list[int] = []
    monkeypatch.setattr(
        _complete,
        "_spawn_refresh",
        lambda override=None, spawn_in=None: spawns.append(1),
    )
    path = _aged_manifest(tree, tmp_path, None)  # off
    complete_cli(["--manifest", str(path), "--", ""])
    assert spawns == []


def test_swr_rapid_tabs_spawn_exactly_once(tree, tmp_path, monkeypatch):
    spawns: list[int] = []
    monkeypatch.setattr(
        _complete,
        "_spawn_refresh",
        lambda override=None, spawn_in=None: spawns.append(1),
    )
    path = _aged_manifest(tree, tmp_path, 600)
    complete_cli(["--manifest", str(path), "--", ""])  # aged → spawn + bump mtime
    complete_cli(["--manifest", str(path), "--", ""])  # now fresh → no spawn
    assert spawns == [1]


def test_spawn_refresh_posix_is_detached(monkeypatch):
    captured: dict[str, Any] = {}
    monkeypatch.setattr(_complete.os, "name", "posix")
    monkeypatch.setattr(
        subprocess, "Popen", lambda cmd, **kw: captured.update(cmd=cmd, kw=kw)
    )
    _complete._spawn_refresh()
    assert "_refresh" in " ".join(captured["cmd"])
    assert captured["kw"]["start_new_session"] is True


def test_spawn_refresh_windows_uses_creationflags(monkeypatch):
    captured: dict[str, Any] = {}
    monkeypatch.setattr(_complete.os, "name", "nt")
    monkeypatch.setattr(
        subprocess, "Popen", lambda cmd, **kw: captured.update(cmd=cmd, kw=kw)
    )
    _complete._spawn_refresh()
    assert "creationflags" in captured["kw"]
    if sys.platform == "win32":  # the flag constants exist only on Windows
        flags = captured["kw"]["creationflags"]
        # CREATE_NO_WINDOW, never DETACHED_PROCESS: a console-less child is
        # handed a visible terminal window by the Windows 11 default-terminal
        # handoff — one popped over the shell per TAB on a stale manifest.
        assert flags & subprocess.CREATE_NO_WINDOW
        assert not flags & subprocess.DETACHED_PROCESS


def test_spawn_refresh_swallows_oserror(monkeypatch):
    def boom(*a, **k):
        raise OSError("no fork today")

    monkeypatch.setattr(subprocess, "Popen", boom)
    _complete._spawn_refresh()  # must not raise


def test_refresh_cwd_no_tasks_file_builds_nothing(tmp_path, monkeypatch):
    from livery.footman import _manifest, _refresh

    (tmp_path / ".git").mkdir()  # ceiling here, so the cascade can't climb higher
    monkeypatch.chdir(tmp_path)
    built: list[int] = []
    monkeypatch.setattr(_manifest, "sync_manifest", lambda *a, **k: built.append(1))
    _refresh.refresh_cwd()  # no tasks.py in the cascade — nothing built, no crash
    assert built == []


def test_completion_max_age_parsing():
    from livery.footman import _config

    assert _config.completion_max_age({}) == 600  # default
    assert _config.completion_max_age({"completion": {"max_age": "30s"}}) == 30
    assert _config.completion_max_age({"completion": {"max_age": "5m"}}) == 300
    assert _config.completion_max_age({"completion": {"max_age": "1h"}}) == 3600
    assert _config.completion_max_age({"completion": {"max_age": "2d"}}) == 172800
    assert _config.completion_max_age({"completion": {"max_age": "off"}}) is None
    assert _config.completion_max_age({"completion": {"max_age": "none"}}) is None
    assert _config.completion_max_age({"completion": {"max_age": 0}}) is None
    assert _config.completion_max_age({"completion": {"max_age": -5}}) is None
    assert _config.completion_max_age({"completion": {"max_age": 120}}) == 120
    assert _config.completion_max_age({"completion": {"max_age": True}}) == 600
    assert _config.completion_max_age({"completion": {"max_age": False}}) is None
    assert _config.completion_max_age({"completion": {"max_age": "garbage"}}) == 600
    assert (
        _config.completion_max_age({"completion": {"max_age": []}}) == 600
    )  # non-scalar


def test_refresh_cwd_rebuilds_the_manifest(tmp_path, monkeypatch):
    # The background child rebuilds the cwd cascade's manifest end-to-end.
    from livery.footman import _paths, _refresh

    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n')
    (tmp_path / "tasks.py").write_text(
        "from livery.footman import task\n@task\ndef hi(): ...\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    _refresh.refresh_cwd()
    data = json.loads(_paths.manifest_path(tmp_path).read_text())
    assert "hi" in data["tree"]["tasks"]
    assert data["completion_max_age"] == 600  # baked from the default


def test_refresh_cwd_drops_the_manifest_when_the_cascade_empties(tmp_path, monkeypatch):
    # Delete the last tasks file and the cached manifest must go with it.
    # Nothing else rewrites it — global mode writes the *global* manifest —
    # so it would otherwise offer vanished tasks the runner refuses by name,
    # and every aged TAB bumps its mtime past the collector's idle sweep.
    from livery.footman import _paths, _refresh

    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n')
    (tmp_path / "tasks.py").write_text(
        "from livery.footman import task\n@task\ndef hi(): ...\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    _refresh.refresh_cwd()
    assert _paths.manifest_path(tmp_path).is_file()

    (tmp_path / "tasks.py").unlink()
    _refresh.refresh_cwd()
    assert not _paths.manifest_path(tmp_path).exists()


def test_refresh_cwd_keeps_the_manifest_while_a_rung_survives(tmp_path, monkeypatch):
    # A *partial* deletion is a rebuild, not a removal: the cascade still has
    # a rung, so the manifest is rewritten without the vanished subtask.
    from livery.footman import _paths, _refresh

    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n')
    (tmp_path / "tasks.py").write_text(
        "from livery.footman import task\n@task\ndef hi(): ...\n"
    )
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "tasks.py").write_text(
        "from livery.footman import task\n@task\ndef sub(): ...\n"
    )
    monkeypatch.chdir(sub)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    _refresh.refresh_cwd()
    assert "sub" in json.loads(_paths.manifest_path(sub).read_text())["tree"]["tasks"]

    (sub / "tasks.py").unlink()
    _refresh.refresh_cwd()
    tasks = json.loads(_paths.manifest_path(sub).read_text())["tree"]["tasks"]
    assert "hi" in tasks and "sub" not in tasks


def test_refresh_source_rebuilds_the_manifest(tmp_path, monkeypatch):
    # The cold-build child rebuilds one -f file's (cwd, file) manifest — keyed
    # apart from the cwd cascade, with no background refresh (max_age 0).
    from pathlib import Path

    from livery.footman import _paths, _refresh

    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n')
    (tmp_path / "other.py").write_text(
        "from livery.footman import task\n@task\ndef ship(): ...\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    _refresh.refresh_source("other.py")
    data = json.loads(
        _paths.source_manifest_path(tmp_path, Path("other.py")).read_text()
    )
    assert "ship" in data["tree"]["tasks"]
    assert data["completion_max_age"] == 0  # -f: rebuilt on demand, not in the bg
    assert data["tasks_file"] == "other.py"  # baked, keyed apart from the cascade


def test_refresh_source_missing_file_builds_nothing(tmp_path, monkeypatch):
    from livery.footman import _manifest, _refresh

    monkeypatch.chdir(tmp_path)
    built: list[int] = []
    monkeypatch.setattr(_manifest, "sync_manifest", lambda *a, **k: built.append(1))
    _refresh.refresh_source("nope.py")  # the -f value names no file — nothing built
    assert built == []


def test_main_dispatches_complete(tree, tmp_path, monkeypatch, capsys):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"schema": _complete._SCHEMA, "tree": tree}))
    monkeypatch.setattr(
        sys, "argv", ["fm", "--complete", "--manifest", str(path), "--", "che"]
    )
    with pytest.raises(SystemExit) as exc:
        footman.main()
    assert exc.value.code == 0
    assert _completion_names(capsys.readouterr().out) == {"check"}


def test_main_dispatches_version(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["fm", "--version"])
    with pytest.raises(SystemExit) as exc:
        footman.main()
    assert exc.value.code == 0
    assert "footman" in capsys.readouterr().out


def test_main_takes_its_own_file_as_the_tasks_file(tmp_path, monkeypatch, capsys):
    # The shebang pattern: a tasks file ending in `footman.main(__file__)`
    # is its own command, and reads its own tasks whatever the directory.
    script = tmp_path / "deploy.py"
    script.write_text(
        'from livery.footman import task\n\n@task\ndef ship():\n    """Ship."""\n'
        '    print("shipped")\n',
        encoding="utf-8",
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("FOOTMAN_NO_UV", "1")  # this is about argv, not handoffs
    monkeypatch.setattr(sys, "argv", ["deploy.py", "ship"])
    with pytest.raises(SystemExit) as exc:
        footman.main(str(script))
    assert exc.value.code == 0
    assert "shipped" in capsys.readouterr().out


def test_an_explicit_tasks_file_still_wins(tmp_path, monkeypatch, capsys):
    theirs = tmp_path / "theirs.py"
    theirs.write_text(
        'from livery.footman import task\n\n@task\ndef ship():\n    """Ship."""\n'
        '    print("theirs")\n',
        encoding="utf-8",
    )
    mine = tmp_path / "mine.py"
    mine.write_text(
        'from livery.footman import task\n\n@task\ndef ship():\n    """Ship."""\n'
        '    print("mine")\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FOOTMAN_NO_UV", "1")
    monkeypatch.setattr(sys, "argv", ["mine.py", f"-f={theirs}", "ship"])
    with pytest.raises(SystemExit):
        footman.main(str(mine))
    assert "theirs" in capsys.readouterr().out


def test_a_closed_stdout_means_discard_not_a_traceback(tmp_path):
    # The caller closed fd 1 (supervisors and cron shells do), so Python
    # started with `sys.stdout = None` — and the scheduler's isatty, the uv
    # handoff's flush, and any body print became AttributeError tracebacks.
    # A stream nobody connected means "discard": the run works, the output
    # goes nowhere, and a failure still reaches stderr.
    if sys.platform == "win32":
        pytest.skip("preexec_fn is POSIX")
    import subprocess

    (tmp_path / "tasks.py").write_text(
        "from livery.footman import task\n\n\n@task\ndef hello():\n    print('hi')\n"
    )
    env = {
        **os.environ,
        "FOOTMAN_NO_UV": "1",
        "FOOTMAN_CACHE_DIR": str(tmp_path / ".cache"),
    }
    env.pop("VIRTUAL_ENV", None)
    proc = subprocess.run(
        [sys.executable, "-m", "livery.footman", "hello"],
        cwd=tmp_path,
        env=env,
        stderr=subprocess.PIPE,
        text=True,
        preexec_fn=lambda: os.close(1),
    )
    assert proc.returncode == 0, proc.stderr
    assert "AttributeError" not in proc.stderr
    assert "Traceback" not in proc.stderr


def test_a_reader_hanging_up_is_a_calm_cut_not_a_traceback(tmp_path):
    # `fm chatty | head`: once the body writes past the pipe buffer after the
    # reader closed, its next print raises EPIPE. That used to be a raw
    # BrokenPipeError traceback plus "Exception ignored while flushing
    # sys.stdout" — the reader saying "enough" dressed as a crash. Now: one
    # calm reason, exit 128+SIGPIPE like any SIGPIPE-default tool, so
    # `set -o pipefail` still sees the cut and `| head` users see no spam.
    if sys.platform == "win32":
        pytest.skip("pipes and SIGPIPE semantics are POSIX")
    import subprocess

    (tmp_path / "tasks.py").write_text(
        "from livery.footman import task\n\n\n@task\ndef lines():\n"
        "    for i in range(5000):\n"
        "        print('line', i, 'x' * 80)\n"
    )
    env = {
        **os.environ,
        "FOOTMAN_NO_UV": "1",
        "FOOTMAN_CACHE_DIR": str(tmp_path / ".cache"),
    }
    env.pop("VIRTUAL_ENV", None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "livery.footman", "lines"],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None and proc.stderr is not None
    proc.stdout.readline()  # take one line, like `head -1`
    proc.stdout.close()  # …and hang up with >64 KiB still to come
    assert proc.wait(timeout=30) == 141
    err = proc.stderr.read()
    assert "output cut short" in err
    assert "BrokenPipeError" not in err
    assert "Traceback" not in err
    assert "Exception ignored" not in err


def _ascii_stdout(monkeypatch) -> io.BytesIO:
    """Stand in for a legacy console: ascii, errors='strict', not a tty."""
    raw = io.BytesIO()
    monkeypatch.setattr(
        sys, "stdout", io.TextIOWrapper(raw, encoding="ascii", errors="strict")
    )
    return raw


def _ascii_project(tmp_path, monkeypatch, tasks: str) -> None:
    from livery.footman import _paths

    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(tasks, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FOOTMAN_NO_UV", "1")
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")


def test_the_tree_survives_a_non_utf8_stdout(tmp_path, monkeypatch):
    # H39: a console encoding narrower than UTF-8 (PYTHONIOENCODING=ascii,
    # cp1252 on a legacy Windows terminal) encodes with errors='strict', and
    # `--tree` draws its branches with `|- +- |` in box-drawing glyphs — so
    # the listing died half-written with a raw UnicodeEncodeError on
    # *footman's own* strings, with every task name here pure ASCII.
    _ascii_project(
        tmp_path,
        monkeypatch,
        "from livery.footman import group\n\n"
        "sub = group('sub', help='Sub tasks')\n\n"
        "@sub.task\ndef one():\n    'Do one.'\n\n"
        "@sub.task\ndef two():\n    'Do two.'\n",
    )
    raw = _ascii_stdout(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["fm", "--tree"])
    with pytest.raises(SystemExit) as exc:
        footman.main()
    sys.stdout.flush()
    assert exc.value.code == 0
    out = raw.getvalue().decode("ascii")
    assert "Do one." in out and "Do two." in out  # the whole listing, not a prefix
    assert "?" in out  # the branch glyphs degraded rather than killing the row


def test_the_listing_survives_non_ascii_task_help(tmp_path, monkeypatch):
    # The same crash from the other side: `--list` prints only user strings,
    # so one accented docstring was enough to lose the listing.
    _ascii_project(
        tmp_path,
        monkeypatch,
        "from livery.footman import task\n\n"
        "@task\ndef build():\n    'Baké the café.'\n\n"
        "@task\ndef ship():\n    'Ship it.'\n",
    )
    raw = _ascii_stdout(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["fm", "--list"])
    with pytest.raises(SystemExit) as exc:
        footman.main()
    sys.stdout.flush()
    assert exc.value.code == 0
    out = raw.getvalue().decode("ascii")
    assert "Bak? the caf?." in out and "Ship it." in out


def test_task_decorator_protocol_matches_group_task():
    # The module-level `task` alias is declared as a TaskDecorator Protocol so
    # the assignment has a stated type; this pins the Protocol's parameterised
    # overload to `Group.task`'s keyword surface, or the two drift silently.
    import ast as ast_

    source = (Path(footman.__file__).parent / "registry.py").read_text(encoding="utf-8")
    tree = ast_.parse(source)

    def kwonly(fn: ast_.FunctionDef) -> dict[str, tuple[str, str]]:
        args = fn.args
        defaults = args.kw_defaults
        return {
            a.arg: (
                ast_.unparse(a.annotation) if a.annotation else "",
                ast_.unparse(d) if d is not None else "",
            )
            for a, d in zip(args.kwonlyargs, defaults)
        }

    group_task = None
    proto_call = None
    for node in tree.body:
        if isinstance(node, ast_.ClassDef) and node.name == "Group":
            # the implementation def (last `task` def wins: overloads precede)
            for item in node.body:
                if isinstance(item, ast_.FunctionDef) and item.name == "task":
                    group_task = item
        if isinstance(node, ast_.ClassDef) and node.name == "TaskDecorator":
            for item in node.body:
                if (
                    isinstance(item, ast_.FunctionDef)
                    and item.name == "__call__"
                    and item.args.kwonlyargs  # the parameterised overload
                ):
                    proto_call = item
    assert group_task is not None and proto_call is not None
    assert kwonly(proto_call) == kwonly(group_task)


def test_lazy_reexports():
    # F56: every __all__ entry must resolve (via __getattr__ or as a real attr)
    # — a permanent drift guard for the lazy public surface.
    for name in footman.__all__:
        assert getattr(footman, name) is not None, name
    with pytest.raises(AttributeError):
        _ = footman.does_not_exist
