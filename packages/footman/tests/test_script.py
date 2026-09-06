"""PEP 723 inline script metadata: reading the block, and the uv commands."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from livery.footman import _script

BLOCK = """\
# /// script
# requires-python = ">=3.11"
# dependencies = ["footman", "rich"]
# ///
from livery.footman import task

@task
def build(): ...
"""


def _write(tmp_path: Path, text: str, name: str = "tasks.py") -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def test_a_block_reads_as_metadata(tmp_path):
    meta, warning = _script.read_block(_write(tmp_path, BLOCK))
    assert warning is None
    assert meta == {"requires-python": ">=3.11", "dependencies": ["footman", "rich"]}


def test_find_uv_falls_back_to_path(tmp_path, monkeypatch):
    # No uv beside this runner's own scripts directory → the PATH answer.
    monkeypatch.setattr("sysconfig.get_path", lambda name: str(tmp_path))
    monkeypatch.setattr("shutil.which", lambda name: "/somewhere/uv")
    assert _script.find_uv() == "/somewhere/uv"


def test_no_block_and_no_file_are_both_simply_not_scripts(tmp_path):
    assert _script.read_block(_write(tmp_path, "x = 1\n")) == (None, None)
    assert _script.read_block(tmp_path / "ghost.py") == (None, None)


def test_a_bare_hash_line_inside_the_block_is_content(tmp_path):
    # PEP 723: `#` alone is an empty line, `# ` prefixes the rest.
    path = _write(
        tmp_path,
        """\
        # /// script
        # dependencies = ["footman"]
        #
        # requires-python = ">=3.11"
        # ///
        """,
    )
    meta, warning = _script.read_block(path)
    assert warning is None
    assert meta == {"dependencies": ["footman"], "requires-python": ">=3.11"}


def test_a_non_script_block_is_not_ours(tmp_path):
    # The fence names its type; only `script` is this feature's.
    path = _write(
        tmp_path,
        """\
        # /// pyproject
        # [tool.whatever]
        # x = 1
        # ///
        """,
    )
    assert _script.read_block(path) == (None, None)


def test_malformed_toml_warns_and_declines(tmp_path):
    path = _write(
        tmp_path,
        """\
        # /// script
        # dependencies = [oops
        # ///
        """,
    )
    meta, warning = _script.read_block(path)
    assert meta is None
    assert warning is not None
    assert "tasks.py" in warning and "running without it" in warning


def test_two_script_blocks_warn_and_decline(tmp_path):
    # Separated by code: two real blocks. (Back to back, the PEP's own
    # regex reads them as one malformed block — warned either way.)
    path = _write(
        tmp_path,
        """\
        # /// script
        # dependencies = ["footman"]
        # ///
        x = 1
        # /// script
        # dependencies = ["rich"]
        # ///
        """,
    )
    meta, warning = _script.read_block(path)
    assert meta is None
    assert warning is not None and "2 script blocks" in warning


def test_back_to_back_blocks_are_a_read_failure_not_a_silent_drop(tmp_path):
    path = _write(
        tmp_path,
        """\
        # /// script
        # dependencies = ["footman"]
        # ///
        # /// script
        # dependencies = ["rich"]
        # ///
        """,
    )
    meta, warning = _script.read_block(path)
    assert meta is None
    assert warning is not None and "can't read" in warning


def test_declares_normalizes_the_requirement_name():
    def meta(*deps):
        return {"dependencies": list(deps)}

    assert _script.declares(meta("footman"), "footman")
    assert _script.declares(meta("Footman"), "footman")  # case
    assert _script.declares(meta("foot_man"), "foot-man")  # separators
    assert _script.declares(meta("footman[docs]>=0.25"), "footman")  # extras+pin
    assert _script.declares(meta("footman @ file:///tmp/x"), "footman")  # direct
    assert _script.declares(meta("rich", "footman ; python_version>'3.10'"), "footman")
    assert not _script.declares(meta("footmanx"), "footman")  # a prefix is not a name
    assert not _script.declares(meta(), "footman")
    assert not _script.declares({}, "footman")
    assert not _script.declares({"dependencies": "footman"}, "footman")  # not a list


def _fake_uv_calls(monkeypatch, *, sync_code=0, find_code=0, python="/env/bin/python"):
    ran: list[list[str]] = []

    class Done:
        def __init__(self, code, out=""):
            self.returncode, self.stdout = code, out

    def fake_run(cmd, **kwargs):
        ran.append(list(cmd))
        return Done(sync_code) if cmd[1] == "sync" else Done(find_code, python + "\n")

    monkeypatch.setattr(_script.subprocess, "run", fake_run)
    monkeypatch.setattr(_script, "find_uv", lambda: "/fake/uv")
    return ran


def test_child_python_never_reaches_for_the_network(tmp_path, monkeypatch):
    path = _write(tmp_path, BLOCK)
    ran = _fake_uv_calls(monkeypatch)
    assert _script.child_python(path) == "/env/bin/python"
    assert "--offline" in ran[0]  # a keystroke builds nothing it must download


def test_child_python_declines_an_unmaterialized_environment(tmp_path, monkeypatch):
    path = _write(tmp_path, BLOCK)
    ran = _fake_uv_calls(monkeypatch, sync_code=1)  # offline sync can't build it
    assert _script.child_python(path) is None
    assert len(ran) == 1  # and it never went on to ask for an interpreter


def test_child_python_declines_when_it_is_already_here(tmp_path, monkeypatch):
    import sys

    path = _write(tmp_path, BLOCK)
    _fake_uv_calls(monkeypatch, python=sys.executable)
    assert _script.child_python(path) is None  # re-execing would only loop


def test_child_python_declines_without_a_block_or_deps(tmp_path, monkeypatch):
    _fake_uv_calls(monkeypatch)
    assert _script.child_python(_write(tmp_path, "x = 1\n")) is None
    bare = _write(
        tmp_path, '# /// script\n# requires-python = ">=3.11"\n# ///\n', "b.py"
    )
    assert _script.child_python(bare) is None


def test_child_python_honours_the_belt_and_the_optout(tmp_path, monkeypatch):
    path = _write(tmp_path, BLOCK)
    _fake_uv_calls(monkeypatch)
    monkeypatch.setenv("FOOTMAN_UV_REEXEC", "1")
    assert _script.child_python(path) is None
    monkeypatch.delenv("FOOTMAN_UV_REEXEC")
    monkeypatch.setenv("FOOTMAN_NO_UV", "1")
    assert _script.child_python(path) is None


def test_reexec_child_carries_the_belt(monkeypatch):
    calls: list[tuple[str, list[str]]] = []

    def fake_execv(f, a):
        calls.append((f, list(a)))

    monkeypatch.setattr(_script.os, "execv", fake_execv)
    monkeypatch.setenv("VIRTUAL_ENV", "/some/other/venv")
    monkeypatch.delenv("FOOTMAN_UV_REEXEC", raising=False)
    _script.reexec_child("/env/bin/python", ["-c", "pass"])
    assert calls == [("/env/bin/python", ["/env/bin/python", "-c", "pass"])]
    import os

    assert os.environ["FOOTMAN_UV_REEXEC"] == "1"  # the child never loops
    assert "VIRTUAL_ENV" not in os.environ  # a script env is not the active one


def test_the_children_reexec_the_same_entry_they_are_spawned_with():
    # Drift guard: the refresh child re-runs *itself* inside the script
    # environment, so the one-liner it re-execs must be the one-liner the
    # hot path spawns. Two places, one string, asserted rather than hoped.
    import inspect

    from livery.footman import _complete, _refresh

    spawn = inspect.getsource(_complete._spawn_refresh)
    child = inspect.getsource(_refresh)
    for entry in (
        "_refresh.refresh_cwd(*sys.argv[1:])",
        "_refresh.refresh_source(*sys.argv[1:])",
    ):
        assert entry in spawn, entry
        assert entry in child, entry


def _prelude(argv: list[str]) -> list[str]:
    """The interpreter words of a child command line: the flags, the mode
    switch, and (for `-m`) the module it names — everything ahead of the
    payload. A leading interpreter path, where there is one, is not one."""
    words = list(argv)
    if words and not words[0].startswith("-"):
        words.pop(0)
    out: list[str] = []
    while words and words[0].startswith("-"):
        word = words.pop(0)
        out.append(word)
        if word == "-c":
            break  # the one-liner behind it is payload, not a flag
        if word == "-m":
            out.append(words.pop(0))  # the module name rides with it
            break
    return out


def test_the_children_reexec_the_same_interpreter_flags(monkeypatch):
    # The other half of the drift guard, and the one a string comparison
    # misses: `reexec_child` *replaces* the process with the argv it is
    # handed, so a flag on the spawn and not on the re-exec is a hole that
    # opens the moment a tasks file carries its own dependencies. `-P` is why
    # this is worth pinning — without it the child's sys.path starts at the
    # directory being completed, where a `footman.py` would answer its import
    # — but the guard is about the flags agreeing, whatever they become.
    import subprocess

    from livery.footman import _complete, _refresh, _suggest

    spawned: list[list[str]] = []
    reexeced: list[list[str]] = []
    monkeypatch.setattr(_script, "child_python", lambda file: "/fake/python")
    monkeypatch.setattr(
        _script, "reexec_child", lambda python, argv: reexeced.append(list(argv))
    )

    def record_popen(cmd, **kwargs):
        spawned.append(list(cmd))

    def record_run(cmd, **kwargs):
        spawned.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "Popen", record_popen)
    _complete._spawn_refresh()
    _refresh._maybe_reexec([Path("tasks.py")], "ENTRY", "arg")
    assert _prelude(spawned[-1]) == _prelude(reexeced[-1]) == ["-P", "-c"]

    monkeypatch.setattr(subprocess, "run", record_run)
    # The project rule would claim this cwd (the suite runs inside footman's
    # own pinned project); stand it down — the script rule is what's pinned.
    monkeypatch.setattr(_script, "project_home", lambda cwd: None)
    _complete._fresh_dynamic("target", ["deploy"], ["a", ""])
    _suggest._maybe_reexec([Path("tasks.py")])
    assert (
        _prelude(spawned[-1])
        == _prelude(reexeced[-1])
        == ["-P", "-m", "livery.footman._suggest"]
    )


def test_the_uv_command_lines():
    file = Path("/tmp/tasks.py")
    assert _script.sync_argv("/fake/uv", file) == [
        "/fake/uv",
        "sync",
        "--script",
        str(file),
        "--quiet",
    ]
    assert _script.sync_argv("/fake/uv", file, quiet=False, offline=True) == [
        "/fake/uv",
        "sync",
        "--script",
        str(file),
        "--offline",
    ]
    assert _script.find_argv("/fake/uv", file) == [
        "/fake/uv",
        "python",
        "find",
        "--script",
        str(file),
    ]


# --- the lock rule, as the children ask it ------------------------------------
# A pinned project owns its directory: whichever runner answered the TAB, the
# manifest must be built by the project's interpreter from the project's
# packages, or completion describes a world the run refuses. These pin the
# children's half of `_uv_handoff`'s verdict: who owns a directory, when a
# child changes interpreter, and that healing stays strictly offline.

_PINNING_LOCK = 'version = 1\n\n[[package]]\nname = "footman"\nversion = "1.0"\n'


def test_project_home_wants_a_lock_that_pins_the_dist(tmp_path):
    assert _script.project_home(tmp_path) is None  # no lock anywhere
    (tmp_path / "uv.lock").write_text(
        'version = 1\n\n[[package]]\nname = "requests"\nversion = "2.0"\n'
    )
    assert _script.project_home(tmp_path) is None  # pins someone else's world
    (tmp_path / "uv.lock").write_text(_PINNING_LOCK)
    assert _script.project_home(tmp_path) == tmp_path
    nested = tmp_path / "pkg" / "deep"
    nested.mkdir(parents=True)
    assert _script.project_home(nested) == tmp_path  # the nearest ancestor


def test_import_caused_walks_the_cause_chain():
    assert _script.import_caused(ImportError("x"))
    wrapped = RuntimeError("outer")
    wrapped.__cause__ = ModuleNotFoundError("No module named 'yaml'")
    assert _script.import_caused(wrapped)
    assert not _script.import_caused(RuntimeError("boom"))
    assert not _script.import_caused(SyntaxError("bad"))


def _project(tmp_path, *, with_python=True):
    (tmp_path / "uv.lock").write_text(_PINNING_LOCK)
    python = _script.venv_python(tmp_path)
    if with_python:
        python.parent.mkdir(parents=True)
        python.write_text("")
    return tmp_path


def _record_uv(monkeypatch, *, check_code=0, sync_code=0):
    ran: list[list[str]] = []

    class Done:
        def __init__(self, code):
            self.returncode = code

    def fake_run(cmd, **kwargs):
        ran.append(list(cmd))
        return Done(check_code if "--check" in cmd else sync_code)

    monkeypatch.setattr(_script.subprocess, "run", fake_run)
    monkeypatch.setattr(_script, "find_uv", lambda: "/fake/uv")
    return ran


def test_a_foreign_child_reexecs_into_the_projects_venv(tmp_path, monkeypatch):
    root = _project(tmp_path)
    ran = _record_uv(monkeypatch)  # env already current: check answers 0
    reexeced: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        _script, "reexec_child", lambda p, a: reexeced.append((p, list(a)))
    )
    _script.project_reexec(root, ["-P", "-c", "ENTRY"], heal=True)
    assert reexeced == [(str(_script.venv_python(root)), ["-P", "-c", "ENTRY"])]
    (check,) = ran  # fresh: the check sufficed, nothing synced
    assert "--offline" in check and "--check" in check


def test_healing_stays_offline_and_only_when_stale(tmp_path, monkeypatch):
    root = _project(tmp_path)
    ran = _record_uv(monkeypatch, check_code=1)  # stale: check says outdated
    monkeypatch.setattr(_script, "reexec_child", lambda p, a: None)
    _script.project_reexec(root, ["-P", "-c", "ENTRY"], heal=True)
    check, sync = ran
    assert "--check" in check
    assert "--check" not in sync
    assert "--offline" in sync  # a keystroke never downloads
    assert "--project" in sync and str(root) in sync


def test_heal_false_runs_no_uv_at_all(tmp_path, monkeypatch):
    root = _project(tmp_path)
    ran = _record_uv(monkeypatch)
    reexeced: list[str] = []
    monkeypatch.setattr(_script, "reexec_child", lambda p, a: reexeced.append(p))
    _script.project_reexec(root, ["-P", "-c", "ENTRY"], heal=False)
    assert ran == []  # the re-exec itself is uv-free, so the opt-out never binds it
    assert reexeced == [str(_script.venv_python(root))]


def test_a_child_already_home_stays_and_never_reexecs(tmp_path, monkeypatch):
    root = _project(tmp_path)
    _record_uv(monkeypatch, check_code=1, sync_code=0)
    monkeypatch.setattr(_script, "inside", lambda venv: True)
    monkeypatch.setattr(
        _script, "reexec_child", lambda p, a: pytest.fail("re-exec at home")
    )
    _script.project_reexec(root, ["-P", "-c", "ENTRY"], heal=True)


def test_project_reexec_honours_the_belt_and_the_optout(tmp_path, monkeypatch):
    root = _project(tmp_path)
    ran = _record_uv(monkeypatch)
    monkeypatch.setattr(
        _script, "reexec_child", lambda p, a: pytest.fail("looped through the belt")
    )
    monkeypatch.setenv("FOOTMAN_UV_REEXEC", "1")
    _script.project_reexec(root, ["-P", "-c", "ENTRY"], heal=True)
    monkeypatch.delenv("FOOTMAN_UV_REEXEC")
    monkeypatch.setenv("FOOTMAN_NO_UV", "1")
    _script.project_reexec(root, ["-P", "-c", "ENTRY"], heal=True)
    assert ran == []


def test_a_missing_venv_interpreter_carries_on_in_place(tmp_path, monkeypatch):
    root = _project(tmp_path, with_python=False)
    _record_uv(monkeypatch, check_code=1, sync_code=1)  # offline sync can't build it
    monkeypatch.setattr(
        _script, "reexec_child", lambda p, a: pytest.fail("no interpreter to exec")
    )
    _script.project_reexec(root, ["-P", "-c", "ENTRY"], heal=True)


def test_the_suggest_child_prefers_the_project_lane(tmp_path, monkeypatch):
    from livery.footman import _suggest

    lanes: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(_script, "project_home", lambda cwd: tmp_path)
    monkeypatch.setattr(
        _script,
        "project_reexec",
        lambda root, argv, heal: lanes.append(("project", list(argv))),
    )
    monkeypatch.setattr(
        _script,
        "maybe_reexec",
        lambda files, argv: pytest.fail("the script rule spoke over the project"),
    )
    _suggest._maybe_reexec([Path("tasks.py")])
    ((lane, argv),) = lanes
    assert lane == "project"
    assert _prelude(argv) == ["-P", "-m", "livery.footman._suggest"]


def test_the_refresh_child_prefers_the_project_lane(tmp_path, monkeypatch):
    from livery.footman import _refresh

    lanes: list[list[str]] = []
    monkeypatch.setattr(_script, "project_home", lambda cwd: tmp_path)
    monkeypatch.setattr(
        _script, "project_reexec", lambda root, argv, heal: lanes.append(list(argv))
    )
    root = _refresh._project_reexec(tmp_path, True, "ENTRY", "arg")
    assert root == tmp_path
    (argv,) = lanes
    assert _prelude(argv) == ["-P", "-c"]
    assert argv[-2:] == ["ENTRY", "arg"]
