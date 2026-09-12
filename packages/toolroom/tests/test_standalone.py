"""The standalone lane: no footman host, plain subprocess, toolroom's own vocabulary.

footman is importable in this suite (it is the dev dependency that runs
the hosted tests), so standalone behaviour is reached the honest way the
seam defines it: `hosted()` answers False and every call takes the
subprocess executor. Patching that one function *is* the standalone
process, as far as the bridge can tell.
"""

from __future__ import annotations

import subprocess
import sys
from typing import cast

import pytest

from livery.toolroom import tools
from livery.toolroom.tools import Argv, Result, ToolError, _host


@pytest.fixture(autouse=True)
def standalone(monkeypatch):
    monkeypatch.setattr(_host, "hosted", lambda: False)


def test_spawn_returns_result_with_streams_and_tokens():
    r = tools.python("-c", "import sys; print('out'); print('err', file=sys.stderr)")
    assert isinstance(r, Result)
    assert r.ok and int(r) == 0
    assert r.stdout.strip() == "out"
    assert r.stderr.strip() == "err"
    assert r.to_argv()[0] == sys.executable
    assert sys.executable in r.command or r.command.startswith("'")


def test_failure_raises_toolerror_carrying_the_result():
    with pytest.raises(ToolError) as err:
        tools.python("-c", "import sys; print('why', file=sys.stderr); sys.exit(3)")
    result = err.value.result
    assert int(result) == 3
    assert "why" in str(err.value)
    assert "exited 3" in str(err.value)


def test_nofail_returns_the_failing_result():
    r = tools.python.opts(nofail=True)("-c", "raise SystemExit(2)")
    assert not r.ok
    assert int(r) == 2


def test_capture_false_leaves_streams_empty():
    r = tools.python.opts(capture=False)("-c", "print('straight through')")
    assert r.ok
    assert r.stdout == "" and r.stderr == ""


def test_env_is_the_childs_whole_environment(monkeypatch):
    # The hosted contract, kept standalone: what you pass is what the
    # child gets — never a merge over the parent's environment. hse's
    # portability sweep caught the divergence; this pins the repair.
    monkeypatch.setenv("TOOLROOM_INHERITED", "leaks")
    r = tools.python.opts(env={"TOOLROOM_PROBE": "42"})(
        "-c",
        "import os; print(os.environ.get('TOOLROOM_PROBE'), "
        "os.environ.get('TOOLROOM_INHERITED'))",
    )
    assert r.stdout.strip() == "42 None"


def test_cwd_and_rel_root_the_call(tmp_path):
    sub = tmp_path / "inner"
    sub.mkdir()
    r = tools.python.opts(cwd=tmp_path, rel="inner")(
        "-c", "import os; print(os.getcwd())"
    )
    assert r.stdout.strip().endswith("inner")


def test_input_feeds_stdin():
    r = tools.python.opts(input="fed line\n")(
        "-c", "import sys; print(sys.stdin.read().strip().upper())"
    )
    assert r.stdout.strip() == "FED LINE"


def test_input_is_consumed_exactly_once():
    handle = tools.python.opts(input="once\n")
    handle("-c", "import sys; sys.stdin.read()")
    with pytest.raises(TypeError, match="already fed"):
        handle("-c", "import sys; sys.stdin.read()")


def test_in_process_demand_needs_a_host():
    with pytest.raises(ValueError, match="needs a footman host"):
        tools.coverage.opts(in_process=True)("--version")


def test_in_process_preference_degrades_to_spawn():
    # coverage is constructed in_process=True; standalone it must simply spawn
    # and answer correctly.
    r = tools.coverage("--version")
    assert r.ok
    assert "coverage" in r.stdout.lower() or r.stdout.strip()


def test_timeout_propagates():
    with pytest.raises(subprocess.TimeoutExpired):
        tools.python.opts(timeout=0.2)("-c", "import time; time.sleep(10)")


def test_argv_twin_is_an_ordinary_list():
    built = tools.git.push.argv(force=True)
    assert isinstance(built, Argv)
    assert built == ["git", "push", "--force"]
    assert built.posix() == "git push --force"
    assert built.windows() == "git push --force"


def test_argv_positional_is_a_taught_refusal():
    cmd = tools.git.status.argv()
    with pytest.raises(TypeError, match=r"built command line \(Argv\)"):
        # The cast is the point: the stub already refuses this spelling
        # statically; the test asserts the runtime teaches it too.
        tools.uv.run(cast("str", cmd))


def test_reporting_lane_opts_are_accepted_and_ignored():
    # title/recorded/pre_record are host vocabulary; standalone they must not
    # change execution or crash.
    r = tools.python.opts(title="probe", recorded=False)("-c", "print('ok')")
    assert r.stdout.strip() == "ok"


@pytest.fixture
def uncoloured(monkeypatch):
    """A process with no colour opinion inherited from the outside."""
    for var in _host._COLOR_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("TERM", raising=False)


def test_color_on_reads_the_conventional_environment(monkeypatch, uncoloured):
    monkeypatch.setenv("NO_COLOR", "1")
    assert _host.color_on() is False
    monkeypatch.delenv("NO_COLOR")
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert _host.color_on() is True


def test_color_on_reads_the_whole_force_set(monkeypatch, uncoloured):
    # A tool-facing seam has to speak every spelling the tools do, not just
    # the modern one.
    for var in _host._FORCE_VARS:
        monkeypatch.setenv(var, "1")
        assert _host.color_on() is True, var
        monkeypatch.delenv(var)


def test_force_color_zero_does_not_force(monkeypatch, uncoloured):
    # Consumed by truthiness: `FORCE_COLOR=0` is a request *not* to force,
    # and stdout is not a terminal under pytest's capture either way.
    monkeypatch.setenv("FORCE_COLOR", "0")
    assert _host.color_on() is False


def test_no_color_beats_a_force_variable(monkeypatch, uncoloured):
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("NO_COLOR", "1")
    assert _host.color_on() is False


def test_a_dumb_terminal_is_no_terminal(monkeypatch, uncoloured):
    monkeypatch.setattr(_host.sys, "stdout", _Tty())
    assert _host.color_on() is True
    monkeypatch.setenv("TERM", "dumb")
    assert _host.color_on() is False


class _Tty:
    """Stands in for a terminal on stdout, which pytest's capture is not."""

    def isatty(self) -> bool:
        return True


def test_child_env_writes_the_force_set_and_clears_the_other_side(
    monkeypatch, uncoloured
):
    monkeypatch.setenv("FORCE_COLOR", "1")
    composed = _host.child_env(None, "auto")
    assert composed is not None
    assert all(composed[var] == "1" for var in _host._FORCE_VARS)
    assert "NO_COLOR" not in composed


def test_child_env_forcing_off_leaves_no_inherited_force_variable(
    monkeypatch, uncoloured
):
    # The whole point of clearing: a tool that reads mere presence would
    # honour a stray inherited FORCE_COLOR straight past NO_COLOR.
    monkeypatch.setenv("CLICOLOR_FORCE", "1")
    monkeypatch.setenv("NO_COLOR", "1")
    composed = _host.child_env(None, "auto")
    assert composed is not None
    assert composed["NO_COLOR"] == "1"
    assert not any(var in composed for var in _host._FORCE_VARS)


def test_auto_leaves_an_explicit_environment_alone(monkeypatch, uncoloured):
    # `env=` replaces rather than merges, and ambient is what that environment
    # already carries — so `auto` has nothing to add to it.
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert _host.child_env({"PATH": "/nowhere"}, "auto") == {"PATH": "/nowhere"}


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("always", "FORCE_COLOR"), ("never", "NO_COLOR")],
)
def test_a_decided_colour_merges_over_an_explicit_environment(mode, expected):
    # An instruction aimed at this child outranks the environment it was
    # handed — the same word `run(color=)` gives it hosted. The caller's own
    # variables survive; only the colour ones are overruled.
    composed = _host.child_env({"PATH": "/nowhere", "NO_COLOR": "1"}, mode)
    assert composed is not None
    assert composed["PATH"] == "/nowhere"
    assert composed[expected] == "1"
    if mode == "always":
        assert "NO_COLOR" not in composed
    else:
        assert not any(var in composed for var in _host._FORCE_VARS)


# What the child reports back: the names toolroom *manages*, by exact
# match. A substring test for "COLOR" would also catch the terminal's own
# COLORTERM and COLORFGBG — names toolroom deliberately leaves alone, so a
# developer's terminal would fail an assertion a CI runner passes.
_MANAGED = (
    f"import os; managed = {sorted(_host._COLOR_VARS)!r}; "
    "print(','.join(sorted(k for k in os.environ if k in managed)))"
)


def test_a_spawned_tool_inherits_the_forced_colour(monkeypatch, uncoloured):
    monkeypatch.setenv("FORCE_COLOR", "1")
    r = tools.python(
        "-c",
        _MANAGED,
    )
    assert r.stdout.strip() == "CLICOLOR,CLICOLOR_FORCE,FORCE_COLOR"


def test_a_spawned_tool_inherits_the_silence(monkeypatch, uncoloured):
    monkeypatch.setenv("NO_COLOR", "1")
    r = tools.python(
        "-c",
        _MANAGED,
    )
    assert r.stdout.strip() == "NO_COLOR"


def test_a_capability_hint_is_left_alone(monkeypatch, uncoloured):
    """`COLORTERM` says what the terminal can *render*, not whether to
    colour, so toolroom writes the four switches and passes the rest
    through — the child sees the hint exactly as this process had it.
    """
    monkeypatch.setenv("COLORTERM", "truecolor")
    monkeypatch.setenv("FORCE_COLOR", "1")
    r = tools.python("-c", "import os; print(os.environ.get('COLORTERM', '<gone>'))")
    assert r.stdout.strip() == "truecolor"


def test_the_flag_half_follows_the_same_bit(monkeypatch, uncoloured):
    # git ignores the environment and takes a switch, so the two halves must
    # answer to one decision: forced on, the switch rides the executed argv.
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert tools.git("--version").to_argv()[:3] == ["git", "-c", "color.ui=always"]
    monkeypatch.delenv("FORCE_COLOR")
    monkeypatch.setenv("NO_COLOR", "1")
    assert tools.git("--version").to_argv() == ["git", "--version"]


def test_standalone_command_shows_what_actually_ran(monkeypatch, uncoloured):
    # Hosted, `.command` is the tool's own call and the forced switch shows
    # only under `--verbose`; standalone there is no second spelling to keep,
    # so a `Result` renders the argv it spawned — colour switch included.
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert tools.git("--version").command == "git -c color.ui=always --version"


def test_container_error_wording_matches_the_doors():
    text = _host.container_error(["a"], "git", example="git(*value)")
    assert "bare list" in text
    assert "git(*value)" in text


# --- secrets: the display redacts, the execution never does -------------------


class _Marked(str):
    """footman's Secret, duck-typed: the marker is the *type*, `reveal()` the
    explicit unwrap the bridge keys on. Local on purpose — this suite proves
    the seam without footman in the picture.
    """

    def reveal(self) -> str:
        return str(self)


def test_a_marked_value_reaches_the_child_intact_and_the_shown_line_redacted():
    # Redaction is of the display, never the execution: the child sees the
    # real token, and everything built from the invocation shows ***.
    r = tools.python("-c", "import sys; print(sys.argv[1])", _Marked("hunter2"))
    assert r.stdout.strip() == "hunter2"
    assert "hunter2" not in r.command
    assert "***" in r.command


def test_marked_values_keep_their_type_through_a_built_argv():
    # The attached form is what execution carries; the subclass must survive
    # the join or a display-time redactor downstream has nothing to key on.
    built = tools.git.commit.argv(author=_Marked("hunter2"))
    (token,) = [t for t in built if "hunter2" in t]
    assert token == "--author=hunter2"
    assert hasattr(token, "reveal"), "the marker survives the attach"

    built = tools.git.add.argv(_Marked("s3cret"))
    assert any(hasattr(t, "reveal") for t in built), "positionals keep it too"


def test_a_marked_error_line_redacts_too():
    with pytest.raises(ToolError) as err:
        tools.python("-c", "import sys; sys.exit(3)", _Marked("hunter2"))
    assert "hunter2" not in str(err.value)
    assert "***" in str(err.value)


def test_plain_values_still_stringify_to_plain_str():
    from pathlib import Path

    built = tools.demo.argv(Path("src"), depth=3)
    assert list(built) == ["demo", "src", "--depth=3"]
    assert all(type(token) is str for token in built)


def test_a_stringified_marked_value_passes_in_the_clear():
    # The caller unwrapping is the caller choosing to expose — string
    # operations answer in plain str, and the bridge respects that.
    r = tools.python("-c", "import sys; print(sys.argv[1])", f"{_Marked('ok')}")
    assert r.stdout.strip() == "ok"
    assert "ok" in r.command
