"""The tools bridge: mechanical flag translation, subcommands, versions."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from livery.footman.testing import recording
from livery.toolroom import tools


def _one(call) -> str:
    with recording() as steps:
        call()
    assert len(steps) == 1
    return steps[0].command


def test_mechanical_flag_translation():
    cmd = _one(
        lambda: tools.ruff.check(
            "src", "tests", fix=True, select=["E", "F"], output_format="github"
        )
    )
    assert cmd == (
        "ruff check src tests --fix --select E --select F --output-format github"
    )


def test_single_dash_long_flags_for_go_style_tools():
    # A Go flag-package tool (eclint) wants one dash on long flags: `-fix`, not
    # `--fix`. Single-char flags already took one dash; single_dash extends it to
    # long ones too.
    assert _one(lambda: tools.eclint(fix=True)) == "eclint -fix"
    assert _one(lambda: tools.eclint("src", exclude="node_modules")) == (
        "eclint src -exclude node_modules"
    )


def test_single_dash_rides_chaining_flags_and_negation():
    # One dash on the executed argv (raw), not just the shown line — and it holds
    # across chained verbs, .flags(), and the off-sentinel negation.
    with recording() as steps:
        tools.eclint("src", fix=True, color=tools.off)
        tools.eclint.flags(verbose=True).check("src")
    assert steps[0].raw == "eclint src -fix -no-color"
    assert steps[1].raw == "eclint -verbose check src"


def test_git_forces_colour_with_its_own_switch():
    # git ignores FORCE_COLOR and auto-disables over footman's pipe, so a call
    # that wants colour injects its pre-verb switch — into the executed argv
    # only. `.command` (what recording() asserts) stays git's own call; `.raw`
    # shows what actually ran. The decision rides the call, not the context:
    # the assertion is about this argv, so it says so.
    with recording() as steps:
        tools.git.opts(color="always").diff("--stat")
    assert steps[0].command == "git diff --stat"
    assert steps[0].raw == "git -c color.ui=always diff --stat"


def test_git_no_colour_injection_when_monochrome():
    # Monochrome injects nothing: git has no `off` form because its `auto`
    # default is already quiet when piped.
    with recording() as steps:
        tools.git.opts(color="never").diff("--stat")
    assert steps[0].raw == "git diff --stat"


def test_explicit_colour_kwarg_suppresses_injection():
    # A caller who spells colour wins: no switch is forced on top of it.
    with recording() as steps:
        tools.git.opts(color="always").diff("--stat", color="never")
    assert steps[0].raw == "git diff --stat --color=never"


@pytest.mark.parametrize("spelling", ["color", "colour", "colors", "colours"])
def test_colour_override_guard_accepts_every_spelling(spelling):
    # The override guard recognises all four colour spellings — so a caller's
    # explicit choice suppresses the forced switch (`.on` stays empty).
    assert tools._color_tokens("git", ["diff"], {spelling: "never"}, True).on == ()


def test_colour_mode_is_a_taught_error():
    from typing import Literal, cast

    with pytest.raises(ValueError, match=r"not a colour mode"):
        # The cast is the point: the stub already refuses this statically;
        # the test asserts the runtime teaches it too.
        tools.git.opts(color=cast("Literal['auto']", "yes"))


def _ambient(monkeypatch, **vars: str) -> None:
    """Set the process's ambient colour answer, clearing what is already there.

    These tests run *inside* a footman run, which publishes its own answer at
    the run boundary — so a test that only adds a variable is arguing with the
    enclosing run rather than setting the ambient.
    """
    for var in tools._host._COLOR_VARS:
        monkeypatch.delenv(var, raising=False)
    for name, value in vars.items():
        monkeypatch.setenv(name, value)


def test_explicit_colour_beats_an_ambient_no_color(monkeypatch):
    # An instruction aimed at one call outranks an environment-wide
    # preference — the rule that keeps a colour assertion honest on CI.
    _ambient(monkeypatch, NO_COLOR="1")
    with recording() as steps:
        tools.git.opts(color="always").diff("--stat")
    assert steps[0].raw == "git -c color.ui=always diff --stat"


def test_auto_reads_the_ambient_answer(monkeypatch):
    # The default rung: no decision on the call, so the environment answers —
    # which inside a run is the answer that run published at its boundary.
    _ambient(monkeypatch, FORCE_COLOR="1")
    with recording() as steps:
        tools.git.diff("--stat")
    assert steps[0].raw == "git -c color.ui=always diff --stat"
    _ambient(monkeypatch, NO_COLOR="1")
    with recording() as steps:
        tools.git.diff("--stat")
    assert steps[0].raw == "git diff --stat"


def _intercept_run(monkeypatch, *, takes_colour: bool):
    """Stand in for footman's `run()`, with or without the colour keyword.

    The installed footman may be either side of `run(color=)` landing, so the
    pass-through is tested against both shapes rather than against whichever
    one happens to be resolved here.
    """
    from typing import Any

    from livery.footman import context as footman_context

    seen: dict[str, Any] = {}

    def with_colour(target, *, color="auto", **kwargs):
        seen["color"] = color
        seen["target"] = target
        return 0

    def without_colour(target, **kwargs):
        seen.update(kwargs)
        seen["target"] = target
        return 0

    monkeypatch.setattr(
        footman_context, "run", with_colour if takes_colour else without_colour
    )
    monkeypatch.setattr(tools._host, "hosted", lambda: True)
    monkeypatch.setattr(tools._host, "_RUN_COLOUR", None)  # the probe is cached
    return seen


@pytest.mark.parametrize("mode", ["auto", "always", "never"])
def test_the_mode_reaches_footmans_run_unresolved(monkeypatch, mode):
    # `auto` means "follow whoever owns the ambient", and hosted that is the
    # run — so the mode travels, not an answer computed from the environment.
    seen = _intercept_run(monkeypatch, takes_colour=True)
    tools.git.opts(color=mode).diff("--stat")
    assert seen["color"] == mode


def test_an_older_footman_is_not_passed_the_keyword(monkeypatch):
    # The keyword arrived after the floor toolroom names, so the bridge probes
    # for it: against a run() without one, the call must still go through.
    seen = _intercept_run(monkeypatch, takes_colour=False)
    tools.git.opts(color="always").diff("--stat")
    assert "color" not in seen
    assert seen["target"][:3] == ["git", "-c", "color.ui=always"]  # switch still


def test_the_colour_probe_reads_the_signature():
    def with_colour(target, *, color="auto"): ...
    def without(target): ...

    tools._host._RUN_COLOUR = None
    assert tools._host._run_takes_colour(with_colour) is True
    tools._host._RUN_COLOUR = None
    assert tools._host._run_takes_colour(without) is False
    tools._host._RUN_COLOUR = None


def test_verb_scoped_colour_flag_rides_with_the_flags(monkeypatch):
    # A tool that takes `--color=always` (not git's pre-verb global) gets it
    # appended with the call's flags — both directions, keyed by verb.
    entry = {"check": tools._ColorFlag(on=("--color=always",), off=("--color=never",))}
    monkeypatch.setitem(tools._COLOR, "ruff", entry)
    with recording() as steps:
        tools.ruff.opts(color="always").check("src")
    assert steps[0].raw == "ruff check src --color=always"
    assert steps[0].command == "ruff check src"  # shown line stays clean
    with recording() as steps:  # monochrome -> the off direction
        tools.ruff.opts(color="never").check("src")
    assert steps[0].raw == "ruff check src --color=never"


def test_off_sentinel_emits_the_negation():
    from livery.toolroom.tools import off

    # `off` disables a default-on flag; equivalent to naming it directly.
    assert _one(lambda: tools.zensical.build(clean=True, strict=off)) == (
        "zensical build --clean --no-strict"
    )
    assert _one(lambda: tools.mkdocs.build(no_strict=True)) == (
        "mkdocs build --no-strict"
    )


def test_off_can_be_variable_driven():
    from livery.toolroom.tools import off

    def render(directory_urls: bool):
        return _one(lambda: tools.mkdocs.build(directory_urls=directory_urls or off))

    assert render(True) == "mkdocs build --directory-urls"
    assert render(False) == "mkdocs build --no-directory-urls"


def test_false_none_and_empty_collections_are_omitted():
    # Empty lists/tuples vanish like False/None — so a task parameter's
    # default (`select: list[str] = ()`) passes straight through with no
    # `or None` ceremony at the call site.
    cmd = _one(
        lambda: tools.ruff.check("src", fix=False, config=None, select=[], ignore=())
    )
    assert cmd == "ruff check src"


def test_single_letter_kwargs_are_short_flags():
    cmd = _one(lambda: tools.pytest_bin("-q", k="markers"))
    assert cmd == "pytest-bin -q -k markers"


def test_shell_tools_run_a_command_string_through_the_shell(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")  # POSIX display, pinned for Win CI
    # `tools.bash("cmd")` runs `bash -c cmd` — a real shell, so pipes work.
    assert _one(lambda: tools.bash("echo hi | cat")) == "bash -c 'echo hi | cat'"
    assert _one(lambda: tools.nu("ls | length")) == "nu -c 'ls | length'"
    # cmd is a curated shell too — it bakes in /c like the others bake in -c.
    assert _one(lambda: tools.cmd("dir")) == "cmd /c dir"


def test_manual_source_driver_is_never_extracted():
    from livery.toolroom.tools._machinery import _drivers

    bash = _drivers.find("bash")
    assert bash is not None and bash.source == "manual"
    assert _drivers.extract(bash).verbs == ()  # hand-written stub, never read


def test_trailing_underscore_escapes_keywords():
    assert _one(lambda: tools.bun.add("left-pad", global_=True)) == (
        "bun add left-pad --global"
    )


def test_subcommands_chain():
    assert _one(lambda: tools.docker.compose.up(detach=True)) == (
        "docker compose up --detach"
    )


def test_any_executable_is_a_tool():
    # No declaration needed — the module fallback bridges anything on PATH.
    assert _one(lambda: tools.terraform("plan", out="tf.plan")) == (
        "terraform plan --out tf.plan"
    )


def test_shadowing_names_resolve_to_tools_not_imports():
    # F50/F53: `run`, `sys`, `re`, … used to be public module imports, so
    # `tools.run`/`tools.sys` returned the imported object (typechecking as a
    # Tool per the stub, crashing at runtime). Privatized, they now bridge to
    # Tools like any other name.
    for name in ("run", "sys", "re", "subprocess"):
        got = getattr(tools, name)
        assert isinstance(got, tools.Tool) and got._argv0 == name


def test_tools_stub_declares_every_runtime_binding():
    # Freeze the stub: every module-level runtime binding in tools.py must be
    # declared in tools.pyi, so a privatized import can never silently reappear
    # as a public attribute and stop being a Tool.
    import ast
    from pathlib import Path

    def bindings(source: str) -> set[str]:
        names: set[str] = set()
        for node in ast.parse(source).body:
            if isinstance(node, ast.Import):
                names |= {a.asname or a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                names |= {a.asname or a.name for a in node.names}
            elif isinstance(
                node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
            ):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
        return names

    src = Path(tools.__file__)
    runtime = bindings(src.read_text())
    declared = bindings(src.with_suffix(".pyi").read_text())
    missing = runtime - declared - {"annotations"}  # __future__ import allowlisted
    assert not missing, f"tools.pyi is missing runtime bindings: {sorted(missing)}"


def test_tool_opts_stub_mirrors_run_signature():
    # `.opts()` policy is forwarded verbatim to `run()`, so the stub must carry
    # `run()`'s types. The stub used to narrow the four "unset" options
    # (`cwd`/`rel`/`title`/`timeout`), which made a computed `timeout=None` — no
    # bound — a type error against code that runs fine.
    import ast
    import inspect
    from pathlib import Path

    from livery.footman import context

    stub = ast.parse(Path(tools.__file__).with_suffix(".pyi").read_text())
    cls = next(n for n in stub.body if isinstance(n, ast.ClassDef) and n.name == "Tool")
    opts = next(
        n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "opts"
    )
    declared = {
        arg.arg: ast.unparse(arg.annotation)
        .replace("_Path", "Path")
        .replace("_ResultView", "ResultView")
        for arg in opts.args.kwonlyargs
        if arg.annotation is not None
    }
    assert set(declared) == set(tools._TOOL_OPTS)
    run_params = inspect.signature(context.run).parameters
    for name, annotation in declared.items():
        if name in ("in_process", "color"):
            # The bridge's own, with no run() counterpart. `color` is a per-tool
            # question — which half of the forcing this tool needs — so footman
            # has no reason to carry it; the run-wide answer it *does* own
            # reaches the call through the environment instead.
            continue
        assert annotation == str(run_params[name].annotation), (
            f".opts({name}=) is typed {annotation}, run() takes "
            f"{run_params[name].annotation}"
        )


def test_curated_names_map_to_real_executables():
    assert _one(lambda: tools.markdownlint("docs/index.md")) == (
        "markdownlint-cli2 docs/index.md"
    )
    assert _one(lambda: tools.ruff_format("src", check=True)) == (
        "ruff format src --check"
    )


def test_installed_version_is_cached_and_comparable():
    tools._version_cache.clear()
    version = tools.ruff.installed_version()
    assert version >= (0, 1)
    # Keyed on the binary actually run, not the name typed: `tools.python`
    # runs sys.executable, and reporting PATH's python instead would answer
    # about a different interpreter than the one it invokes.
    assert tools._version_cache["ruff"] == version  # second read hits the cache
    assert tools.ruff.installed_version() is not None


def test_installed_version_answers_about_the_binary_it_runs():
    import sys

    tools._version_cache.clear()
    running = sys.version_info[:2]
    assert tools.python.installed_version()[:2] == running


def test_version_spelling_is_declarable():
    """Windows `cmd` has no `--version`; `cmd /c ver` is how it answers. A
    tool that spells it differently says so at construction rather than
    leaving `installed_version()` a special case nobody can reach.
    """
    # object-typed peeks: the stub types every Tool attribute access as the
    # next Tool in the chain, so the checker cannot see the real tuples.
    cmd_spelling: object = tools.cmd._version_argv
    ruff_spelling: object = tools.ruff._version_argv
    assert cmd_spelling == ("/c", "ver")
    assert ruff_spelling == ("--version",)
    # It rides a chain, like every other construction fact.
    chained: object = tools.cmd.anything._version_argv
    assert chained == ("/c", "ver")


def test_read_version_handles_the_grammars_tools_really_ship():
    read = tools.read_version
    assert read("ruff 0.16.0") == "0.16.0"
    assert read("eclint v0.6.0-wk.5") == "0.6.0-wk.5"  # the `v` prefix, whole
    assert read("1.13.0.git.kitware.jobserver-pipe-1") == (
        "1.13.0.git.kitware.jobserver-pipe-1"
    )
    assert read("Microsoft Windows [Version 10.0.19045.3803]") == "10.0.19045.3803"
    # OpenSSH glues its patchlevel: without the `pN` tail the match fails at
    # `10.4` and falls through to LibreSSL's number — the wrong library's.
    assert read("OpenSSH_10.4p1, LibreSSL 4.1.0") == "10.4p1"
    assert read("no numbers here") == ""


def test_one_parser_serves_the_extractor_and_the_bridge():
    """A stub's recorded version and a task's `installed_version()` may
    disagree about *which binary* they asked — `_resolve` prefers a Homebrew
    keg for host-read tools — but never about how a version string reads.
    """
    from livery.toolroom.tools._machinery import _drivers

    assert _drivers.version.__globals__  # imported lazily inside the function
    for text in ("git version 2.55.0", "gh version 2.96.0 (2026-01-01)"):
        assert tools.read_version(text)


def test_installed_version_unreadable_is_taught():
    with pytest.raises((ValueError, FileNotFoundError)):
        tools.Tool("no-such-binary-really").installed_version()


# --- in-process execution ---------------------------------------------------


class _FakeEP:
    """A stand-in console_scripts EntryPoint: `.load()` returns the target
    (and records that the import happened).
    """

    def __init__(self, target, loaded: list[bool] | None = None) -> None:
        self._target = target
        self._loaded = loaded

    def load(self):
        if self._loaded is not None:
            self._loaded.append(True)
        return self._target


def test_dry_run_does_not_import_the_tool(monkeypatch):
    monkeypatch.setattr(tools._host, "hosted", lambda: True)
    # The property duty had: a call you don't execute costs no tool import.
    # Under recording (dry-run), the entry point is resolved (metadata) but
    # never loaded — so the tool's module is never imported.
    loaded: list[bool] = []

    def target(argv=None):
        print("ran")
        return 0

    monkeypatch.setattr(
        tools, "_console_entrypoint", lambda name: _FakeEP(target, loaded)
    )
    with recording() as steps:
        tools.Tool("heavy", in_process=True)("build")
    assert loaded == []  # dry-run imported nothing
    assert steps[0].command == "heavy build"

    tools.Tool("heavy", in_process=True)("build")  # a real run does load it
    assert loaded == [True]


def test_in_process_never_spawns(monkeypatch):
    # coverage ships a console_scripts entry and is installed (pytest-cov);
    # if the subprocess layer is touched, this fails loudly.
    from livery.footman import context

    def boom(*a, **k):
        raise AssertionError("subprocess used for an in-process tool")

    monkeypatch.setattr(context, "_run_subprocess", boom)
    saved_argv = list(sys.argv)
    assert tools.coverage.opts(nofail=True)("--version") == 0
    assert sys.argv == saved_argv  # patched argv is always restored


def test_in_process_demand_without_entry_is_taught(monkeypatch):
    monkeypatch.setattr(tools._host, "hosted", lambda: True)
    with pytest.raises(ValueError, match="no importable in-process entry"):
        tools.Tool("no-such-python-tool").opts(in_process=True)("--version")


def test_in_process_preference_falls_back_to_subprocess():
    # git has no console_scripts entry; a preference (not a demand) must
    # degrade to the normal spawn.
    with recording() as steps:
        tools.Tool("git", in_process=True)("status", s=True)
    assert steps[0].command == "git status -s"


def test_in_process_tools_run_concurrently_with_separate_capture(monkeypatch):
    """Two argument-accepting in-process tools must overlap (the barrier
    times out if they serialise) and must not cross-contaminate captures.
    """
    import threading

    from livery.footman import _manifest, _schedule
    from livery.footman._split import split_chain
    from livery.footman.registry import Group

    barrier = threading.Barrier(2, timeout=5)

    def make_entry(marker):
        def entry(argv=None):  # accepts args -> direct, lock-free path
            barrier.wait()
            print(f"{marker}-OUT")
            return 0

        return entry

    entries = {"fake-a": make_entry("A"), "fake-b": make_entry("B")}
    monkeypatch.setattr(
        tools,
        "_console_entrypoint",
        lambda name: _FakeEP(entries[name]) if name in entries else None,
    )

    reg = Group("root")

    @reg.task
    def a():
        tools.Tool("fake-a", in_process=True)()

    @reg.task
    def b():
        tools.Tool("fake-b", in_process=True)()

    tree = _manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, ["a", "b"])
    results = {r.task: r for r in _schedule.run_plan(reg, segments)}
    assert results["a"].ok and results["b"].ok
    assert "A-OUT" in results["a"].steps[0].output
    assert "B-OUT" not in results["a"].steps[0].output  # no cross-talk
    assert "B-OUT" in results["b"].steps[0].output


def test_in_process_tool_with_foreign_cwd_demotes_to_subprocess(monkeypatch, tmp_path):
    # footman never chdirs in a parallel task, so an in-process tool whose
    # target cwd differs from the live process cwd runs as its subprocess
    # twin instead: same command, right cwd, still fully parallel — the
    # in-process speedup is the only loss.
    from livery.footman import _manifest, _schedule
    from livery.footman._split import split_chain
    from livery.footman.registry import Group

    seen = {}

    def entry(argv=None):
        seen["in_process"] = True
        return 0

    monkeypatch.setattr(
        tools,
        "_console_entrypoint",
        lambda name: _FakeEP(entry) if name == "python" else None,
    )

    reg = Group("root")

    @reg.task
    def go():
        tools.Tool(
            "python",
            "-c",
            "import os; print(os.getcwd())",
            path=sys.executable,
            in_process=True,
        )()

    tree = _manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, ["go"])
    results = {
        r.task: r
        for r in _schedule.run_plan(reg, segments, ctx_config={"cwd": tmp_path})
    }
    assert results["go"].ok, results["go"].error
    assert "in_process" not in seen  # the entry never ran: demoted
    assert results["go"].steps[0].output.strip() == str(tmp_path)


def test_in_process_tool_with_matching_cwd_stays_in_process(monkeypatch):
    # Equal target and live cwd (the common single-package case): no
    # demotion, the in-process speedup is kept.
    from livery.footman import _globals, _manifest, _schedule
    from livery.footman._split import split_chain
    from livery.footman.registry import Group

    seen = {}

    def entry(argv=None):
        seen["in_process"] = True
        return 0

    monkeypatch.setattr(
        tools,
        "_console_entrypoint",
        lambda name: _FakeEP(entry) if name == "cwd-tool" else None,
    )

    reg = Group("root")

    @reg.task
    def go():
        tools.Tool("cwd-tool", in_process=True)()

    tree = _manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, ["go"])
    here = _globals.real_getcwd()
    results = {
        r.task: r
        for r in _schedule.run_plan(reg, segments, ctx_config={"cwd": Path(here)})
    }
    assert results["go"].ok, results["go"].error
    assert seen.get("in_process") is True


def test_tool_opts_rel_roots_the_call(tmp_path):
    # Tool.opts(cwd=, rel=) is the bridge's per-call override — the same
    # policy carrier as nofail/capture, threading straight into run().
    from livery.footman import _manifest, _schedule
    from livery.footman._split import split_chain
    from livery.footman.registry import Group

    (tmp_path / "web").mkdir()
    reg = Group("root")

    @reg.task
    def go():
        t = tools.Tool(
            "python", "-c", "import os; print(os.getcwd())", path=sys.executable
        )
        t.opts(rel="web")()

    tree = _manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, ["go"])
    results = {
        r.task: r
        for r in _schedule.run_plan(reg, segments, ctx_config={"cwd": tmp_path})
    }
    assert results["go"].ok, results["go"].error
    assert results["go"].steps[0].output.strip() == str(tmp_path / "web")


def test_tool_opts_none_means_unset(tmp_path):
    # None is "no opinion" for the four options run() treats that way, so a
    # caller can compute one (`cwd=None if inline else build_dir`) and a later
    # None clears an earlier bound value. The types say so; this says it runs.
    from livery.footman import _manifest, _schedule
    from livery.footman._split import split_chain
    from livery.footman.registry import Group

    (tmp_path / "web").mkdir()
    reg = Group("root")

    @reg.task
    def go():
        t = tools.Tool(
            "python", "-c", "import os; print(os.getcwd())", path=sys.executable
        )
        t.opts(rel="web").opts(rel=None)()  # the bound override, cleared
        t.opts(cwd=None, rel=None, title=None, timeout=None)()

    tree = _manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, ["go"])
    results = {
        r.task: r
        for r in _schedule.run_plan(reg, segments, ctx_config={"cwd": tmp_path})
    }
    assert results["go"].ok, results["go"].error
    assert [s.output.strip() for s in results["go"].steps] == [str(tmp_path)] * 2


def test_zero_arg_entries_fall_back_to_argv_patching(monkeypatch):
    monkeypatch.setattr(tools._host, "hosted", lambda: True)
    seen = {}

    def zero_arg_entry():  # reads sys.argv like an old argparse main
        seen["argv"] = list(sys.argv)
        return 0

    monkeypatch.setattr(
        tools, "_console_entrypoint", lambda name: _FakeEP(zero_arg_entry)
    )
    saved = list(sys.argv)
    assert tools.Tool("legacy", in_process=True)("build", fast=True) == 0
    assert seen["argv"] == ["legacy", "build", "--fast"]
    assert sys.argv == saved


def test_mixed_tool_output_is_never_interleaved(monkeypatch, capsys, tmp_path):
    """Eight virtual tools — half in-process, half real subprocesses of the
    same script — printing name+counter with overlap-forcing sleeps. The
    aggregate stream must be perfectly block-contiguous per tool, and every
    tool's lines strictly incremental.
    """
    import re
    import threading
    import time

    from livery.footman import _manifest, _schedule
    from livery.footman._split import split_chain
    from livery.footman.registry import Group

    lines, tool_count = 20, 8
    script = tmp_path / "vtool.py"
    script.write_text(
        "import sys, time\n"
        "name, count = sys.argv[1], int(sys.argv[2])\n"
        "for i in range(1, count + 1):\n"
        '    print(f"{name} {i}", flush=True)\n'
        "    time.sleep(0.005)\n"
    )

    # Guard against vacuity, structurally rather than by wall clock (which
    # flakes on slow CI runners whose interpreter startup dwarfs the sleeps):
    # all four in-process entries must be running at once to pass this
    # barrier. If the lock-free path ever regresses to serialised, the first
    # entry blocks here holding the serialiser, the rest can never arrive,
    # and the barrier breaks — failing the run on any hardware.
    overlap = threading.Barrier(tool_count // 2, timeout=10)

    def make_entry(name):
        def entry(argv):  # accepts args -> the parallel, lock-free path
            overlap.wait()
            for i in range(1, int(argv[0]) + 1):
                print(f"{name} {i}", flush=True)
                time.sleep(0.005)
            return 0

        return entry

    names = [f"vtool-{i}" for i in range(tool_count)]
    entries = {n: make_entry(n) for i, n in enumerate(names) if i % 2 == 0}
    monkeypatch.setattr(
        tools,
        "_console_entrypoint",
        lambda name: _FakeEP(entries[name]) if name in entries else None,
    )

    reg = Group("root")
    for i, name in enumerate(names):
        if i % 2 == 0:

            def body(n=name):
                tools.Tool(n, in_process=True)(str(lines))

        else:

            def body(n=name):
                tools.python(str(script), n, str(lines))

        reg.task(name=name)(body)

    tree = _manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, names)
    results = _schedule.run_plan(reg, segments, ctx_config={"verbose": True})

    # Level 1: each step captured only its own tool, in strict order.
    by_task = {r.task: r for r in results}
    assert len(by_task) == tool_count and all(r.ok for r in results)
    for name in names:
        got = by_task[name].steps[0].output.strip().splitlines()
        assert got == [f"{name} {i}" for i in range(1, lines + 1)]

    # Level 2: the aggregate stream is block-contiguous — 100% un-interleaved.
    counted = [
        m.groups()
        for line in capsys.readouterr().out.splitlines()
        if (m := re.fullmatch(r"(vtool-\d+) (\d+)", line))
    ]
    assert len(counted) == tool_count * lines
    seen_blocks = [name for name, _ in counted[::lines]]
    assert sorted(seen_blocks) == sorted(names)  # eight blocks, one per tool
    for start in range(0, len(counted), lines):
        block = counted[start : start + lines]
        block_name = block[0][0]
        assert all(name == block_name for name, _ in block)
        assert [int(i) for _, i in block] == list(range(1, lines + 1))


def test_in_process_preference_survives_subcommand_chaining():
    # Chained subcommands keep the mode (checked without executing: real
    # coverage mid-test-session would read the live .coverage data and the
    # project's own fail_under). Plain Tool instances, so the probe goes
    # through __getattr__ like any un-stubbed verb.
    assert tools.Tool("coverage", in_process=True).report._prefer_in_process is True
    assert tools.Tool("mkdocs", in_process=True).build._prefer_in_process is True
    assert tools.Tool("git").status._prefer_in_process is False


# --- how a tool spells "off" ---------------------------------------------------


def test_off_uses_the_tools_own_negation():
    """`off` assumed `--no-<name>`, which is wrong often enough to break
    real commands: `mkdocs build --no-clean` is rejected outright — the
    flag is `--dirty`. The exceptions are extracted from the tools, not
    guessed.
    """
    from livery.toolroom.tools import _flags, off

    assert _flags({"clean": off}, "mkdocs") == ["--dirty"]
    assert _flags({"use_directory_urls": off}, "mkdocs") == ["--no-directory-urls"]
    assert _flags({"strict": off}, "mkdocs") == ["--no-strict"]  # convention holds
    assert _flags({"fix": off}, "ruff") == ["--no-fix"]  # other tools unaffected
    assert _flags({"clean": off}) == ["--no-clean"]  # no tool named: the default


def test_click_extraction_reads_the_real_negations():
    """Click states a negatable flag as opts + secondary_opts — the fact
    `off` needs and cannot infer. This is the extractor that fills the
    table, run against the real mkdocs.
    """
    pytest.importorskip("mkdocs")
    # An optional tool: importorskip above guards the run, and the
    # type-check job installs the shots group, not every tool footman
    # can drive.
    import mkdocs.__main__ as entry

    from livery.toolroom.tools._machinery._toolspec import from_click

    spec = from_click(entry.cli, name="mkdocs")
    assert spec.name == "mkdocs" and spec.in_process is True
    assert {"build", "serve", "gh_deploy"} <= {v.name for v in spec.verbs}
    assert spec.negations() == {
        "clean": "--dirty",
        "use_directory_urls": "--no-directory-urls",
    }
    build = next(v for v in spec.verbs if v.name == "build")
    clean = next(o for o in build.options if o.name == "clean")
    assert clean.type_name == "bool" and clean.negation == "--dirty"
    assert clean.help  # the tool's own words, for the stub's docstring


def test_negation_table_matches_what_the_tools_say():
    """The committed table is a cache of what the tools state; if a tool
    changes its spelling, this fails rather than emitting a flag the tool
    will reject.
    """
    pytest.importorskip("mkdocs")
    # An optional tool: importorskip above guards the run, and the
    # type-check job installs the shots group, not every tool footman
    # can drive.
    import mkdocs.__main__ as entry

    from livery.toolroom.tools import _NEGATIONS
    from livery.toolroom.tools._machinery._toolspec import from_click

    assert from_click(entry.cli, name="mkdocs").negations() == _NEGATIONS["mkdocs"]


# --- structured invocation rendering -----------------------------------------
#
# The command line footman *shows* is built from the same translation it
# *executes*, but spelled for a human: separated flags, shell-quoted values,
# role-tagged for colour. `recording()` sees that shown form (via
# Result.command), which is why these assertions read naturally and stay
# stable even when execution tokenises differently.


def test_shown_values_are_shell_quoted_so_the_line_pastes(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")  # POSIX display, pinned for Win CI
    cmd = _one(lambda: tools.git.commit(message="release: cut it now", signoff=True))
    assert cmd == "git commit --message 'release: cut it now' --signoff"


def test_shown_line_uses_the_off_negation_not_the_keyword():
    cmd = _one(lambda: tools.mkdocs.build(strict=True, clean=tools.off))
    assert cmd == "mkdocs build --strict --dirty"


def test_in_process_call_shows_the_command_not_the_flattened_title():
    # The in-process path used to display `" ".join(argv)`; it now shows the
    # same normalised line as any other call.
    import io
    from contextlib import redirect_stdout

    from livery.footman.context import Context, use_context

    buf = io.StringIO()
    with redirect_stdout(buf), use_context(Context(dry_run=True)):
        tools.coverage.html(directory="htmlcov", skip_covered=True)
    assert "$ coverage html --directory htmlcov --skip-covered" in buf.getvalue()


def test_show_parts_tag_each_token_with_its_role():
    from livery.toolroom.tools import _show_parts

    parts = _show_parts("ruff", ["check"], ("src",), {"fix": True, "select": ["E"]})
    assert parts == (
        ("prog", "ruff"),
        ("group", "check"),
        ("req", "src"),
        ("opt", "--fix"),
        ("opt", "--select"),
        ("value", "E"),
    )


def test_the_shown_form_is_separated_the_executed_form_is_attached():
    # `_emit` is the single source both draw from. `_flags` (executed)
    # attaches long values; `_show_parts` (shown) keeps them separated.
    from livery.toolroom.tools import _emit, _flags, _show_parts

    kwargs = {"select": ["E", "F"], "fix": True}
    assert list(_emit(kwargs, "ruff")) == [
        ("--select", "E"),
        ("--select", "F"),
        ("--fix", None),
    ]
    assert _flags(kwargs, "ruff") == ["--select=E", "--select=F", "--fix"]
    shown = " ".join(t for _, t in _show_parts("ruff", ["check"], (), kwargs))
    assert shown == "ruff check --select E --select F --fix"


def test_execution_attaches_only_where_a_space_would_break():
    from livery.toolroom.tools import _flags, _show_parts

    def shown(**kw):
        return " ".join(t for _, t in _show_parts("git", ["log"], (), kw))

    # A dash-leading value would be read as the next option if separated, so
    # both forms attach — the shown line has to stay a valid paste.
    assert _flags({"format": "-%h"}, "git") == ["--format=-%h"]
    assert shown(format="-%h") == "git log --format=-%h"

    # An optional-value option (git spells `--abbrev[=<n>]`) can't tell its
    # value from a positional across a space; execution attaches, the shown
    # line reads it plainly.
    assert _flags({"abbrev": 4}, "git") == ["--abbrev=4"]
    assert shown(abbrev=4) == "git log --abbrev 4"

    # A short option keeps the space unless the value leads with a dash.
    assert _flags({"k": "expr"}, "git") == ["-k", "expr"]
    assert _flags({"k": "-x"}, "git") == ["-k-x"]


def test_step_result_carries_both_the_shown_and_the_raw_command(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")  # POSIX display, pinned for Win CI
    # `.command` reads well (separated); `.raw` is the exact executed line
    # (attached). Both are valid, copy-pasteable commands.
    with recording() as steps:
        tools.git.commit(message="a b c", signoff=True)
    step = steps[0]
    assert step.command == "git commit --message 'a b c' --signoff"
    assert step.raw == "git commit '--message=a b c' --signoff"


def test_raw_of_a_plain_run_shell_quotes_a_list(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")  # POSIX display, pinned for Win CI
    # A direct `run([...])` (not through the bridge) still gets a raw form:
    # the list, shell-quoted so it pastes, while `.command` reads plainly.
    from livery.footman.context import Context, run, use_context

    ctx = Context(dry_run=True)
    with use_context(ctx):
        run(["echo", "a b"])
    assert ctx.steps[-1].raw == "echo 'a b'"
    assert ctx.steps[-1].command == "echo a b"


# --- tool-level globals via .flags() -----------------------------------------


def test_opts_is_run_control_policy_not_flags():
    # .opts() carries footman policy (closed vocab), never tool flags: it does
    # not appear in the command, it rides the chain, and an unknown key teaches.
    assert _one(lambda: tools.ruff.opts(nofail=True).check("src")) == "ruff check src"
    with pytest.raises(TypeError, match="unknown option"):
        # fix is a tool flag, not policy — a runtime error, and a type error too.
        tools.ruff.opts(fix=True)  # type: ignore[call-arg]
    # capture is unambiguously footman's here; a tool's own --capture is a flag.
    assert _one(lambda: tools.pytest(capture="no")) == "pytest --capture no"


# --- tool-level globals via .flags() -----------------------------------------


def test_flags_places_globals_before_the_verb():
    # cobra tools need their globals ahead of the subcommand.
    assert _one(lambda: tools.docker.flags(host="tcp://x").ps(all=True)) == (
        "docker --host tcp://x ps --all"
    )


def test_flags_composes_through_a_nested_verb():
    cmd = _one(lambda: tools.docker.flags(host="tcp://x").compose.up(detach=True))
    assert cmd == "docker --host tcp://x compose up --detach"


def test_flags_raw_is_the_attached_executed_form():
    with recording() as steps:
        tools.docker.flags(host="tcp://x").run("alpine")
    assert steps[0].command == "docker --host tcp://x run alpine"
    assert steps[0].raw == "docker --host=tcp://x run alpine"


def test_flags_keeps_the_in_process_preference():
    # A tool built in-process stays in-process through .flags().
    tool = tools.Tool("mkdocs", in_process=True)
    assert tool.flags(v=True)._prefer_in_process is True


# --- wrapper-verb flag ordering ----------------------------------------------


def test_wrapper_verb_puts_flags_before_the_wrapped_command():
    # The silent bug: `--frozen` must reach uv, not pytest.
    assert _one(lambda: tools.uv.run("pytest", "-q", frozen=True)) == (
        "uv run --frozen pytest -q"
    )
    assert (
        _one(lambda: tools.coverage.run("-m", "pytest", source=["footman"]))
        == "coverage run --source footman -m pytest"
    )


def test_wrapper_ordering_reaches_nested_verbs():
    assert (
        _one(lambda: tools.docker.compose.run("web", "pytest", no_deps=True))
        == "docker compose run --no-deps web pytest"
    )
    assert _one(lambda: tools.docker.exec("box", "sh", interactive=True)) == (
        "docker exec --interactive box sh"
    )


def test_non_wrapper_verb_keeps_flags_last():
    # Ordinary verbs are unchanged — flags after positionals.
    assert _one(lambda: tools.docker.build(".", tag="x")) == "docker build . --tag x"
    assert _one(lambda: tools.uv.sync(frozen=True)) == "uv sync --frozen"


def test_wrapper_raw_and_shown_agree_on_order():
    with recording() as steps:
        tools.uv.run("pytest", frozen=True)
    # both forms put --frozen before pytest; they differ only in attachment.
    assert steps[0].command == "uv run --frozen pytest"
    assert steps[0].raw == "uv run --frozen pytest"


@pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason="the hand-written _WRAPPERS table is curated against the maintainer's "
    "installed tools; CI runs different tool versions, so accuracy can't be "
    "verified here until CI generates the table too (post-1.0)",
)
def test_wrappers_table_matches_what_the_tools_declare():
    # The runtime table is hand-written; this mirrors `fm toolroom.audit`,
    # so drift fails fast in the local `fm check` gate. Skipped in CI (marker
    # above): CI's tool versions differ from the curated table.
    from livery.toolroom.tools import _WRAPPERS
    from livery.toolroom.tools._machinery import _drivers

    for driver in _drivers.DRIVERS:
        if driver.base or not _drivers.installed(driver):
            continue
        declared = _drivers.extract(driver).wrappers()
        assert declared == _WRAPPERS.get(driver.name, frozenset()), driver.name


def test_git_globals_via_flags_precede_the_verb():
    # git's globals belong before the subcommand; the man page supplies them.
    assert (
        _one(
            lambda: tools.git.flags(git_dir="/r/.git", work_tree="/r").commit(
                message="x"
            )
        )
        == "git --git-dir /r/.git --work-tree /r commit --message x"
    )
    assert _one(lambda: tools.git.flags(no_pager=True).log(n=1)) == (
        "git --no-pager log -n 1"
    )


def test_opts_step_false_runs_the_tool_without_recording_it(capsys):
    # The value-read case: call a tool, read the output, leave no trace. The
    # in-tree modules that dropped to raw subprocess to get this are why it
    # exists (_toolhelp, _provision, _colorprobe, _drivers).
    from livery.footman.context import Context, use_context

    ctx = Context()
    with use_context(ctx):
        result = tools.Tool(sys.executable).opts(recorded=False)(
            "-c", "print('a-value')"
        )
    assert result.stdout.strip() == "a-value"
    assert result.code == 0
    assert ctx.steps == []  # nothing recorded
    assert "a-value" not in capsys.readouterr().out  # nothing shown


def test_opts_step_true_is_the_default():
    from livery.footman.context import Context, use_context

    ctx = Context()
    with use_context(ctx):
        tools.Tool(sys.executable)("-c", "print('recorded')")
    assert len(ctx.steps) == 1


def test_opts_timeout_bounds_a_tool_call():
    from livery.footman.context import Context, RunTimeout, use_context

    with use_context(Context()), pytest.raises(RunTimeout) as caught:
        tools.Tool(sys.executable).opts(timeout=0.5)(
            "-c", "import time; time.sleep(30)"
        )
    assert caught.value.result.code == 124


def test_a_timeout_demotes_an_in_process_tool_to_a_subprocess():
    # A bound needs a process to bound. The bridge demotes rather than
    # refusing — the same choice a foreign cwd forces — so the timeout the
    # caller asked for is the thing that survives.
    from livery.footman.context import Context, RunTimeout, use_context

    tool = tools.Tool(sys.executable, in_process=True)
    with use_context(Context()), pytest.raises(RunTimeout):
        tool.opts(timeout=0.5)("-c", "import time; time.sleep(30)")


def test_opts_input_feeds_the_child_once_and_teaches_on_replay():
    from livery.footman.context import Context, use_context

    reader = "import sys; print(sys.stdin.read().upper(), end='')"
    fed = tools.python.opts(input="one shot\n")
    with use_context(Context()):
        assert fed("-c", reader).stdout == "ONE SHOT\n"
        # stdin is consumable: the payload was delivered; a second call is a
        # taught refusal, not a silently-unfed child hanging on a read.
        with pytest.raises(TypeError, match=r"already fed"):
            fed("-c", reader)


def test_opts_input_is_consumed_across_the_whole_chained_family():
    # Chaining copies the policy dict but not the payload cell: a stored
    # intermediate can't mint fresh, re-armed leaves — one `.opts(input=…)`
    # is one delivery, wherever in the chain the call lands. recording()
    # fakes the execution and still consumes, exactly as the run it
    # predicts would.
    intermediate = tools.uv.opts(input="one payload")
    with recording():
        intermediate.pip.install("-r", "-")
        with pytest.raises(TypeError, match=r"already fed"):
            intermediate.pip.install("-r", "-")


def test_opts_input_rearms_with_a_fresh_payload():
    from livery.footman.context import Context, use_context

    reader = "import sys; print(sys.stdin.read(), end='')"
    fed = tools.python.opts(input="first")
    with use_context(Context()):
        assert fed("-c", reader).stdout == "first"
        assert fed.opts(input="second")("-c", reader).stdout == "second"


def test_opts_env_is_the_childs_environment_and_replays():
    from livery.footman.context import Context, use_context

    probe = "import os; print(os.environ.get('FOOTMAN_OPT_ENV', 'absent'), end='')"
    tool = tools.python.opts(env={**os.environ, "FOOTMAN_OPT_ENV": "yes"})
    with use_context(Context()):
        # Policy, not payload: the environment rides the handle and replays.
        assert tool("-c", probe).stdout == "yes"
        assert tool("-c", probe).stdout == "yes"


def test_at_rebinds_the_executable_for_any_tool():
    # Identity channel: any tool — even one footman never heard of — runs
    # the executable .at() names, while the shown line keeps the tool's own
    # name (the receipt says what the call *is*, the path says what ran).
    from livery.footman.context import Context, use_context

    ghost = tools.Tool("doesnotexist").at(sys.executable)
    with use_context(Context()):
        assert ghost("-c", "print('rebound', end='')").stdout == "rebound"
    with recording() as steps:
        ghost("-c", "pass")
    assert steps[0].command.startswith("doesnotexist ")


def test_at_carries_policy_and_the_typed_surface():
    from livery.footman.context import Context, use_context

    tool = tools.python.opts(nofail=True).at(sys.executable)
    with use_context(Context()):
        result = tool("-c", "raise SystemExit(3)")
    assert result.code == 3  # nofail rode along; the rebind changed nothing else


def test_at_refuses_an_in_process_demand():
    # The in-process lane runs THIS interpreter; .at() names a different
    # executable — the two contradict, and the refusal teaches which to drop.
    from livery.footman.context import Context, use_context

    with (
        use_context(Context()),
        pytest.raises(ValueError, match=r"in_process=True on an .at\(\) handle"),
    ):
        tools.python.at(sys.executable).opts(in_process=True)("-c", "pass")


# --------------------------------------------------------------------- .argv


def test_argv_builds_without_running():
    # The whole point: no context, no subprocess, no Result — a value.
    built = tools.mkdocs.gh_deploy.argv(force=True, remote_branch="gh-pages")
    assert built == ["mkdocs", "gh-deploy", "--force", "--remote-branch=gh-pages"]


def test_argv_sits_anywhere_in_the_chain_like_opts():
    # Documented right before the parentheses, tolerated anywhere earlier —
    # every position builds the same tokens.
    want = ["docker", "compose", "up", "--detach"]
    assert tools.docker.compose.up.argv(detach=True) == want
    assert tools.docker.compose.argv.up(detach=True) == want
    assert tools.docker.argv.compose.up(detach=True) == want


def test_argv_works_on_a_tool_with_no_verbs():
    # 22 of the 36 stubbed tools are bare-call, ssh among them — a verb-only
    # argv would miss the tool this feature exists to feed.
    built = tools.ssh.argv("deploy@host", "uptime", p=2222)
    assert built == ["ssh", "-p", "2222", "deploy@host", "uptime"]
    assert built.posix() == "ssh -p 2222 deploy@host uptime"


def test_the_value_serialises_for_the_shell_the_caller_names():
    # Quoting is chosen by the destination, never sniffed from the machine
    # this test runs on — that is what makes a payload survive the trip.
    built = tools.git.commit.argv(m="a message")
    assert built == ["git", "commit", "-m", "a message"]
    assert built.posix() == "git commit -m 'a message'"
    assert built.windows() == 'git commit -m "a message"'


def test_posix_and_windows_disagree_exactly_where_shells_do():
    # The characters a POSIX shell treats as live are inert to CreateProcess
    # quoting, which is why the local platform's quoting cannot stand in.
    built = tools.git.commit.argv(m="cost $HOME `today` back\\slash")
    assert "'cost $HOME `today` back\\slash'" in built.posix()
    assert '"cost $HOME `today` back\\slash"' in built.windows()


def test_a_built_line_nests_through_two_hops():
    # Each hop serialises once, at the boundary it crosses. Written by hand
    # the outer line is sixteen consecutive quote characters; composed, it is
    # three lines with `.posix()` at each machine boundary.
    inner = tools.docker.compose.up.argv(detach=True)
    middle = tools.ssh.argv("app@inner", inner.posix())
    outer = tools.ssh.argv("jump@edge", middle.posix())
    assert inner.posix() == "docker compose up --detach"
    assert middle.posix() == "ssh app@inner 'docker compose up --detach'"
    assert outer.posix() == (
        "ssh jump@edge 'ssh app@inner '\"'\"'docker compose up --detach'\"'\"''"
    )


def test_argv_is_an_ordinary_list():
    built = tools.git.log.argv(n=5)
    assert isinstance(built, list)
    assert built[0] == "git" and built[-1] == "5" and len(built) == 4
    assert built[1:3] == ["log", "-n"]  # slicing, like any list


def test_a_built_line_leads_with_the_tool_name_not_the_local_path():
    # `tools.python` runs THIS interpreter, an absolute local path that means
    # nothing on the machine the line is being built for.
    assert tools.python.argv("-c", "pass")[0] == "python"


def test_argv_never_consumes_a_pending_input():
    # Building feeds no child, so the one-shot payload must still be armed.
    handle = tools.terraform.opts(input="yes")
    assert handle.argv("apply") == ["terraform", "apply"]
    from livery.footman.context import Context, use_context

    with use_context(Context(dry_run=True, quiet=True)):
        handle("apply")  # the payload is still armed, not spent on the build


def test_tokens_pass_on_with_an_explicit_splat():
    # Raw tokens travel as `*cmd` — plain Python, no recognition anywhere.
    payload = tools.git.log.argv(n=1)
    assert tools.ssh.argv("host", *payload) == [
        "ssh",
        "host",
        "git",
        "log",
        "-n",
        "1",
    ]


def test_a_bare_argv_in_a_positional_is_refused_with_both_spellings():
    # One positional Argv is ambiguous between its two meanings, so the
    # refusal teaches both rather than guessing either.
    payload = tools.git.log.argv(n=1)
    with pytest.raises(TypeError, match=r"splat it \(`\*cmd`\)") as err:
        tools.ssh("host", payload)  # type: ignore[arg-type]
    assert "cmd.posix()" in str(err.value)


@pytest.mark.parametrize(
    ("value", "spread"),
    [(["a", "b"], r"\*"), (("a", "b"), r"\*"), ({"a"}, r"\*"), ({"a": 1}, r"\*\*")],
)
def test_a_bare_container_in_a_positional_is_refused(value, spread):
    # Silently stringifying one produced "['a', 'b']" as a single token, which
    # failed late at the tool with a confusing message.
    with pytest.raises(TypeError, match=rf"Spread it with `{spread}`"):
        tools.git.add.argv(value)


def test_a_path_still_stringifies_in_a_positional():
    # The refusal names concrete containers, never "iterable" — Path and int
    # are what str() is for. (A dynamic tool: the stubbed ones narrow their
    # positionals to str statically, which is its own, deliberate, teaching.)
    assert tools.rsync.argv(Path("src"), 3) == ["rsync", "src", "3"]


def test_a_flag_treats_an_argv_as_the_plain_list_it_is():
    # No argv recognition in the keyword path: a list repeats the flag, and a
    # serialised line is an ordinary string value.
    payload = tools.git.log.argv(n=1)
    assert tools.ruff.check.argv(".", config=payload.posix()) == [
        "ruff",
        "check",
        ".",
        "--config=git log -n 1",
    ]
    assert tools.git.commit.argv(trailer=payload) == [
        "git",
        "commit",
        "--trailer=git",
        "--trailer=log",
        "--trailer=-n",
        "--trailer=1",
    ]


# --- secrets stay out of the shown line ---------------------------------------
#
# footman's Secret is a str subclass whose marker is the type; the bridge
# keeps that type alive to the argv (execution needs the real value, and
# display-time redaction can only redact what still carries the marker) and
# redacts its own shown line. `reveal()` and f-strings still pass in the
# clear — the caller unwrapping is the caller choosing to expose.


def test_a_secret_option_value_is_redacted_in_the_shown_line():
    from livery.footman import Secret

    cmd = _one(lambda: tools.git.commit(message="x", author=Secret("hunter2")))
    assert "hunter2" not in cmd
    assert "--author ***" in cmd


def test_a_secret_positional_is_redacted_in_the_shown_line():
    from livery.footman import Secret

    cmd = _one(lambda: tools.git.add(Secret("s3cret")))
    assert "s3cret" not in cmd
    assert "***" in cmd


def test_a_secret_global_bound_via_flags_is_redacted_wholesale():
    from livery.footman import Secret

    # `.flags()` lands in the chain's base as an attached token; the whole
    # token redacts — hiding the flag name too errs in the safe direction.
    cmd = _one(lambda: tools.docker.flags(token=Secret("hunter2")).ps())
    assert "hunter2" not in cmd
    assert "***" in cmd


def test_a_stringified_secret_passes_in_the_clear():
    from livery.footman import Secret

    cmd = _one(lambda: tools.git.commit(message=f"by {Secret('alice')}"))
    assert "alice" in cmd
