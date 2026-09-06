"""The completion hot path: group descent, options, and choice values."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Annotated, Literal
from unittest import mock

import pytest

from livery.footman import _complete, _manifest, _paths, registry, task
from livery.footman._complete import _tasks_file_from, complete, complete_cli
from livery.footman.params import Many, doc, matching, nosplit, suggest


@pytest.fixture(autouse=True)
def _generous_cold_budget(monkeypatch):
    """The product's cold-TAB budget is tight on purpose (a keystroke must
    never hang); the *test* budget is generous, because a loaded CI runner —
    Windows especially, free-threaded builds worst — can take many seconds
    to spawn and import the detached builder. Every cold-path test in this
    file asserts builds-and-serves, never how fast the runner's disk is;
    the flake kept hopping between whichever cold test ran on the slow box.
    The dynamic budget gets the same treatment: the fresh completer spawns a
    framework-importing subprocess, and on timeout serves *nothing* on
    purpose — so a slow spawn reads as an empty candidate list.
    """
    monkeypatch.setattr(_complete, "_COLD_TIMEOUT", 30.0)
    monkeypatch.setattr(_complete, "_DYNAMIC_TIMEOUT", 30.0)


def _cache_state(cache_dir) -> str:
    """What the cold build left behind.

    A manifest present means "landed too late", a stray `.pid.tmp` means the
    write couldn't replace its destination, and an empty (or missing)
    directory means the child never got that far.
    """
    root = Path(cache_dir)
    if not root.exists():
        return f"cache dir {root} does not exist"
    files = sorted(f"{p.name} ({p.stat().st_size}b)" for p in root.rglob("*"))
    return f"cache dir {root} holds: {files or 'nothing'}"


def _child_argv(override: str | None = None) -> list[str] | None:
    """The exact argv `_spawn_refresh` would detach — recorded, not retyped.

    Standing in for `Popen` reads the command out of the product, so this can
    never drift from what a real TAB spawns, and the hot path keeps its own
    spelling of the spawn (no seam added there for the tests' benefit).
    Returns None if nothing was spawned at all.
    """
    seen: list[list[str]] = []

    def _record(cmd, **kwargs):
        seen.append(list(cmd))
        return None

    with mock.patch.object(subprocess, "Popen", _record):
        _complete._spawn_refresh(override)
    return seen[0] if seen else None


# Asks the *child* where it stands, in its own words. `_rebuild` returns
# silently when `task_files` comes back empty, which is exactly what an
# unexpected cwd looks like from outside: a child that ran, said nothing, and
# wrote nothing. Only the child can tell those apart from "never started".
# Plain `key=value`, not JSON: json.dumps escapes, and a Windows path comes
# back with every backslash doubled — unreadable on the one platform this is
# for.
_PROBE = (
    "import sys; from pathlib import Path; from footman import _paths; "
    "cwd = Path.cwd(); ceiling = _paths.find_repo_root(cwd); "
    "files = ', '.join(str(f) for f in _paths.task_files(cwd, ceiling)) or 'none'; "
    "print('cwd=%s; ceiling=%s; task_files=%s; manifest=%s; exe=%s' "
    "% (cwd, ceiling, files, _paths.manifest_path(cwd), sys.executable))"
)


def _child_view() -> str:
    """The child's own answer to "where am I, and can I see any tasks?"."""
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _PROBE],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=120,
        )
    except Exception as exc:
        return f"child view unavailable: {exc!r}"
    return f"child sees {proc.stdout.strip() or '(nothing)'} {proc.stderr.strip()}"


def _cold_evidence(cache_dir, override: str | None = None) -> str:
    """Why the cold build produced nothing — assembled *after* a failure.

    These tests fail on loaded CI runners (py3.12 · windows-latest, hopping
    between whichever cold test lands on the slow box) and the assertion alone
    can't say why: an empty candidate list means the detached builder never
    landed, but not whether it was slow, dead, or standing somewhere it could
    find no tasks. Two things hide the answer in production, both deliberate:
    `_spawn_refresh` sends the child's output to DEVNULL, and `refresh_cwd`
    suppresses every exception — a background refresh must never crash or
    print at a user.

    So the *test* re-runs the very same child, synchronously and with its
    output captured, and reports what it said (or its silence) alongside the
    ground the builder stands on: cwd, interpreter, the task files
    discoverable from there, and the manifest path the hot path polled. None
    of it runs on the happy path — an assertion has already failed by the
    time this is called — and none of it touches the product's spawn.
    """
    from livery.footman import _paths

    lines = [_cache_state(cache_dir)]
    try:
        cwd = Path.cwd()
        ceiling = _paths.find_repo_root(cwd)
        # Joined, never a list repr: `repr` escapes, so an embedded list of
        # Windows paths reads back with every backslash doubled — the one
        # platform this diagnostic is for.
        found = ", ".join(str(p) for p in _paths.task_files(cwd, ceiling)) or "none"
        target = (
            _paths.source_manifest_path(cwd, Path(override))
            if override
            else _paths.cwd_manifest_path()
        )
        lines += [
            f"cwd {cwd}",
            f"sys.executable {sys.executable}",
            f"repo root {ceiling}; task files {found}",
            f"manifest {target} exists={target.exists()}",
        ]
    except Exception as exc:  # a diagnostic must not replace the failure
        lines.append(f"context unavailable: {exc!r}")

    lines.append(_child_view())
    try:
        argv = _child_argv(override)
    except Exception as exc:  # never let the diagnostic eat the failure
        return "\n".join([*lines, f"child argv unavailable: {exc!r}"])
    if argv is None:
        return "\n".join([*lines, "child: _spawn_refresh spawned nothing"])
    lines.append(f"child argv {subprocess.list2cmdline(argv)}")  # not a list repr
    started = time.monotonic()
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, errors="replace", timeout=120
        )
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - started
        return "\n".join([*lines, f"child still running after {elapsed:.1f}s"])
    except Exception as exc:
        return "\n".join([*lines, f"child could not be spawned: {exc!r}"])
    return "\n".join(
        [
            *lines,
            f"child exit {proc.returncode} after {time.monotonic() - started:.1f}s",
            f"child stdout: {proc.stdout.strip() or '(silent)'}",
            f"child stderr: {proc.stderr.strip() or '(silent)'}",
            f"after the rerun, {_cache_state(cache_dir)}",
        ]
    )


def _names(result):
    """Candidate names, dropping any `\t`-separated description column (11.2)."""
    return [c.split("\t", 1)[0] for c in result]


def test_top_level_prefix(tree):
    assert _names(complete(tree, ["che"])) == ["check"]


def test_task_names_carry_descriptions(tree):
    # 11.2: a task/group name candidate emits `name\tsummary`; the shell hooks
    # split on the tab to render a description column.
    assert complete(tree, ["che"]) == [
        "check\tRun every check (format, lint, typecheck, test)."
    ]


def test_options_and_choices_have_no_description(tree):
    # Undocumented options and choice values pass through bare (no tab).
    assert complete(tree, ["lint", "--f"]) == ["--fix"]
    assert "\t" not in "".join(complete(tree, ["lint", "--mode="]))


def test_a_valued_option_offers_both_of_its_spellings(tree):
    """Both shapes are legal and the menu shows both.

    `--mode` is the bare mention that stands for the option's default;
    `--mode=` is the only way to pass a value, because every value in this
    grammar is attached. Offering the bare name alone made the value path —
    and any dynamic completer behind it — reachable only by knowing to type
    `=` first, which is the internal knowledge completion exists to spare.
    """
    # And they must not read as the same row twice: the bare one names the
    # value it stands for, which is the whole difference between them.
    assert complete(tree, ["lint", "--mo"]) == [
        "--mode\tdefault: loose",
        "--mode=",
    ]


def test_a_flag_has_one_spelling(tree):
    """A flag takes no value at either default — `--fix=true` is a taught
    refusal and `--no-fix` is the off spelling — so no `=` is offered."""
    assert complete(tree, ["lint", "--f"]) == ["--fix"]
    assert "--fix=" not in complete(tree, ["lint", ""])


def test_the_equals_spelling_still_reaches_the_values(tree):
    """The pair is a menu change, not a grammar change: taking the `=` row
    and pressing TAB again resolves the value exactly as before."""
    assert set(complete(tree, ["lint", "--mode="])) == {"--mode=strict", "--mode=loose"}


def test_doc_marker_becomes_option_description():
    # An option with a doc("...") marker completes with a description column,
    # exactly like task names do.
    with registry.capture() as root:

        @task
        def lint(fix: Annotated[bool, doc("apply fixes in place")] = False): ...

    built = _manifest.build_manifest(root)["tree"]
    assert complete(built, ["lint", "--f"]) == ["--fix\tapply fixes in place"]


def test_docstring_doc_becomes_option_description():
    # No marker needed: a documented docstring parameter reaches the column.
    with registry.capture() as root:

        @task
        def sync(force: bool = False):
            """Sync.

            Args:
                force: skip the freshness check
            """

    built = _manifest.build_manifest(root)["tree"]
    assert complete(built, ["sync", "--f"]) == ["--force\tskip the freshness check"]


def test_empty_partial_lists_everything(tree):
    # Path-style: candidates sit one segment beyond the typed prefix, and a
    # namespace group carries its trailing dot, `ls -F` style.
    out = _names(complete(tree, [""]))
    assert "check" in out
    assert "docs." in out
    assert "workspace." in out


def test_dotted_descent(tree):
    assert set(_names(complete(tree, ["docs."]))) == {"docs.serve", "docs.build"}
    assert _names(complete(tree, ["docs.ser"])) == ["docs.serve"]


def test_unique_namespace_match_skips_ahead(tree):
    # `rel<TAB>` matches only the `release` group: completion descends
    # straight through it, so no shell ever forces a space after `release.`.
    out = _names(complete(tree, ["rel"]))
    assert set(out) == {"release.prepare", "release.publish"}


def test_a_bare_group_word_is_a_dead_end(tree):
    # `fm docs <TAB>` — the space form has no valid continuation; silence
    # mirrors the splitter's refusal.
    assert complete(tree, ["docs", ""]) == []


def test_task_options(tree):
    out = _names(complete(tree, ["lint", ""]))
    assert {"--fix", "--mode=", "--paths="} <= set(out)
    assert "check" in out  # separator-free chains: the next task completes too
    assert complete(tree, ["lint", "--"]) != []  # option-shaped partial: options only
    assert all(c.startswith("--") for c in complete(tree, ["lint", "--"]))


def test_option_value_choices(tree):
    # The value position is `=`-attached. A detached word is never a value:
    # a bare `--mode` marks the option given and the walk moves on.
    assert set(complete(tree, ["lint", "--mode="])) == {"--mode=strict", "--mode=loose"}
    assert "strict" not in complete(tree, ["lint", "--mode", ""])


def test_nested_option_value_choices(tree):
    out = complete(tree, ["workspace.mount", "--share="])
    assert set(out) == {"--share=main", "--share=scratch", "--share=archive"}


def test_positional_choices_offered_alongside_options(tree):
    out = complete(tree, ["deploy", ""])
    assert "--version=" in out
    assert {"dev", "staging", "prod"} <= set(out)


def test_required_choice_positional(tree):
    assert set(complete(tree, ["version", ""])) == {"major", "minor", "patch"}


def test_unknown_prefix_completes_to_nothing(tree):
    assert complete(tree, ["zzz"]) == []


def test_attached_opt_value_zsh_fish(tree):
    # F49: shells that don't split on `=` pass one word `--mode=st`; complete it
    # to full `--mode=…` tokens.
    assert complete(tree, ["lint", "--mode=st"]) == ["--mode=strict"]
    assert set(complete(tree, ["lint", "--mode="])) == {"--mode=strict", "--mode=loose"}


def test_split_opt_value_bash(tree):
    # F49: bash splits `--mode=st` into words `--mode`, `=`, `st`. The `=` must
    # not disarm the pending value, and a leading `=` partial is stripped.
    assert complete(tree, ["lint", "--mode", "=", "st"]) == ["strict"]
    assert set(complete(tree, ["lint", "--mode", "="])) == {"strict", "loose"}
    assert set(complete(tree, ["lint", "--mode", "=", ""])) == {"strict", "loose"}


def test_leading_global_value_not_read_as_task(tree):
    # F61: `-C docs <TAB>` — `docs` is -C's value, so completion offers the
    # top-level names, not the `docs` group's tasks.
    top = set(complete(tree, [""]))
    assert set(complete(tree, ["-C=docs", ""])) == top
    assert set(complete(tree, ["-C=anydir", ""])) == top


def test_install_completion_completes_shells(tree):
    # F61: --install-completion's optional value is one of the shells.
    assert set(complete(tree, ["--install-completion="])) == {
        "--install-completion=bash",
        "--install-completion=zsh",
        "--install-completion=fish",
        "--install-completion=pwsh",
        "--install-completion=nushell",
    }
    assert complete(tree, ["--install-completion=z"]) == ["--install-completion=zsh"]
    # bash splits the token: the shell completes the bare value word
    assert complete(tree, ["--install-completion", "=", "z"]) == ["zsh"]


def test_setup_completion_completes_shells(tree):
    # --setup-completion mirrors --install-completion: its value is a shell.
    assert set(complete(tree, ["--setup-completion="])) == {
        "--setup-completion=bash",
        "--setup-completion=zsh",
        "--setup-completion=fish",
        "--setup-completion=pwsh",
        "--setup-completion=nushell",
    }
    assert complete(tree, ["--setup-completion=fi"]) == ["--setup-completion=fish"]


def test_leading_flag_global_then_task(tree):
    # A leading flag global (-s) is consumed; the walk still completes tasks.
    assert "check" in _names(complete(tree, ["-s", "che"]))


def test_root_flag_partial_offers_globals(tree):
    # A flag-shaped partial at the root offers fm's own globals — each with
    # its own line beside it now, so `_names` is what asks about the names.
    dd = _names(complete(tree, ["--"]))
    # Both spellings of a defaulted option, only `=` for a value-required
    # one: bare `--config` is a taught refusal, and a menu must not offer
    # what the grammar refuses.
    assert {"--help", "--list", "--install-completion", "--config="} <= set(dd)
    assert "--config" not in dd
    assert {"--color", "--color="} <= set(dd)
    assert set(_names(complete(tree, ["--inst"]))) == {
        "--install-completion",
        "--install-completion=",
    }
    # A single dash reaches the short aliases too — `-C` value-required,
    # so only its attached spelling is offered.
    assert {"-C=", "-h", "-s"} <= set(_names(complete(tree, ["-"])))
    assert "-C" not in _names(complete(tree, ["-"]))


def test_value_taking_globals_follow_the_both_spellings_rule():
    # The documented rule: completing an option offers both of its
    # spellings — the bare mention (standing for its default) and the
    # attached `--opt=` (the only way to pass a value). Task options and
    # plugin globals always followed it; the built-in globals now do too.
    reg = registry.Group("root")

    @reg.task
    def build(): ...

    tree = _manifest.build_manifest(reg)["tree"]
    names = set(_names(complete(tree, ["--"])))
    for defaulted in ("--color", "--jobs", "--describe"):
        assert {defaulted, defaulted + "="} <= names
    for required in ("--where", "--directory", "--tasks-file", "--config"):
        assert required + "=" in names
        assert required not in names
    for flag in ("--json", "--quiet", "--keep-going"):
        assert flag in names
        assert flag + "=" not in names


def test_root_globals_offered_after_a_leading_global(tree):
    # `fm -s --<TAB>` — -s is consumed, more globals are still on offer.
    assert "--json" in _names(complete(tree, ["-s", "--"]))


def test_bare_tab_omits_globals(tree):
    # An empty partial lists tasks only — globals there would be noise.
    out = _names(complete(tree, [""]))
    assert "check" in out
    assert not any(c.startswith("-") for c in out)


def test_globals_not_offered_past_a_group_or_task(tree):
    # Globals bind before the first task; a flag partial inside a group or after
    # a task is not a global position.
    assert "--help" not in _names(complete(tree, ["docs", "--"]))
    assert "--help" not in _names(complete(tree, ["lint", "--"]))


def test_completion_globals_mirror_split():
    # Drift pin: the hot-path mirror must match the core declarations exactly,
    # so renaming, re-typing, re-hinting or re-choicing a global fails CI
    # instead of silently misparsing. Names, files AND choices — a mirror only
    # pinned by name is how `--color`'s choices could drift without a red test.
    from livery.footman import _complete, _shellcomp, _split

    names: set[str] = set()
    path_valued: set[str] = set()
    for name, alias, _kind, hint, _default, _help in _split.GLOBALS:
        spellings = {name} | ({alias} if alias else set())
        names |= spellings
        if hint == "PATH":
            path_valued |= spellings
    assert names == _complete._GLOBALS
    # File completion is exactly the PATH-hinted options, every spelling.
    assert path_valued == _complete._GLOBAL_FILES
    # Choices: the declarations' own, plus the shell trio pinned to the one
    # list `_shellcomp` owns (never duplicated into the declarations).
    declared = {f"--{o.name}": o.choices for o in _split.CORE_OPTIONS if o.choices}
    shell_valued = {n for n, _a, _k, h, _d, _h in _split.GLOBALS if h == "[SHELL]"}
    assert set(_complete._GLOBAL_CHOICES) == set(declared) | shell_valued
    for flag, choices in declared.items():
        assert _complete._GLOBAL_CHOICES[flag] == choices
    for flag in shell_valued:
        assert _complete._GLOBAL_CHOICES[flag] == tuple(_shellcomp.SHELLS)


# --- -f/--tasks-file completion (keyed by cwd + file) -------------------------


def test_tasks_file_from_leading_globals():
    assert _tasks_file_from(["-f=x.py", ""]) == "x.py"
    assert _tasks_file_from(["--tasks-file=x.py", "build"]) == "x.py"
    assert _tasks_file_from(["-f", "=", "x.py"]) == "x.py"  # bash-split form
    assert _tasks_file_from(["-C=sub", "-f=x.py"]) == "x.py"  # skip another option
    assert _tasks_file_from(["-k", "-f=x.py"]) == "x.py"  # skip a flag
    assert _tasks_file_from(["-f", "x.py"]) is None  # detached: not a value
    assert _tasks_file_from(["build", "-f=x.py"]) is None  # after a task: not global
    assert _tasks_file_from(["build"]) is None
    assert _tasks_file_from([""]) is None


def test_source_manifest_path_keys_by_cwd_and_file():
    from pathlib import Path

    from livery.footman import _paths

    cwd = Path("/proj/a")
    a = _paths.source_manifest_path(cwd, Path("x.py"))
    assert a == _paths.source_manifest_path(cwd, Path("x.py"))  # stable
    assert a != _paths.source_manifest_path(cwd, Path("y.py"))  # the file matters
    assert a != _paths.source_manifest_path(
        Path("/proj/b"), Path("x.py")
    )  # cwd matters
    assert a != _paths.manifest_path(cwd)  # distinct from the plain-cwd cache


def test_f_completion_reads_the_source_key(tmp_path, monkeypatch, capsys):
    from pathlib import Path

    from livery.footman import _paths

    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "cache"))
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    tf = proj / "custom.py"

    g = registry.Group("root")

    @g.task
    def alpha(): ...

    @g.task
    def beta(): ...

    # Cache the manifest under the (cwd, file) key, exactly as a `-f` run does.
    _manifest.sync_manifest(
        g,
        Path.cwd(),
        completion_max_age=0,
        tasks_file=str(tf),
        path=_paths.source_manifest_path(Path.cwd(), tf),
    )
    complete_cli(["--", f"-f={tf}", ""])
    out = capsys.readouterr().out.split()
    assert "alpha" in out and "beta" in out


def test_f_partial_value_defers_to_file_completion(tmp_path, monkeypatch, capsys):
    from livery.footman._complete import _EXIT_FILES

    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "cache"))
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)

    g = registry.Group("root")

    @g.task
    def alpha(): ...

    # `-f=cust` here is a *partial* being typed, not a finished override, so
    # completion must land on the cwd tree and signal files (exit 100) rather
    # than hunt for a "(cwd, 'cust')" manifest that never existed.
    _manifest.sync_manifest(g, Path.cwd(), completion_max_age=0)
    assert complete_cli(["--", "-f=cust"]) == _EXIT_FILES
    assert capsys.readouterr().out == ""


def test_source_manifest_path_expands_a_tilde():
    # `~/tasks.py` and its expansion are one file, so they must be one key —
    # the refresh child expands before keying, and a hot path that keyed the
    # literal `~` would read a manifest the child never writes.
    from pathlib import Path

    from livery.footman import _paths

    cwd = Path("/proj/a")
    assert _paths.source_manifest_path(
        cwd, Path("~/tasks.py")
    ) == _paths.source_manifest_path(cwd, Path.home() / "tasks.py")


def test_directory_global_completes_the_target_directory(tmp_path, monkeypatch, capsys):
    from livery.footman import _paths

    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "cache"))
    here = tmp_path / "here"
    there = tmp_path / "there"
    here.mkdir()
    there.mkdir()
    monkeypatch.chdir(here)

    local = registry.Group("root")

    @local.task
    def stayhome(): ...

    target = registry.Group("root")

    @target.task
    def alpha(): ...

    @target.task
    def beta(): ...

    # Warm both directories' manifests, exactly as runs in each would.
    _manifest.sync_manifest(local, here, completion_max_age=0)
    _manifest.sync_manifest(
        target, there, completion_max_age=0, path=_paths.manifest_path(there)
    )
    # `-C <dir>` moves the run's whole world there; completion must follow.
    complete_cli(["--", f"-C={there}", ""])
    out = capsys.readouterr().out.split()
    assert "alpha" in out and "beta" in out
    assert "stayhome" not in out


def test_directory_global_missing_target_stays_silent(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "cache"))
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)

    g = registry.Group("root")

    @g.task
    def alpha(): ...

    _manifest.sync_manifest(g, Path.cwd(), completion_max_age=0)
    # A mistyped -C target must not fall back to the invoking directory's
    # tasks — those are answers to a different question.
    assert complete_cli(["--", "-C=/nope/nowhere", ""]) == 0
    assert capsys.readouterr().out == ""


# --- a broken tasks file: exit 103, the reason, instant answers, recovery -----


def _broken_project(tmp_path, monkeypatch):
    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "cache"))
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname='x'\n")
    (proj / "tasks.py").write_text("import footman\n\nthis is a syntax error\n")
    monkeypatch.chdir(proj)
    return proj


def test_a_broken_tasks_file_answers_103_with_the_reason(tmp_path, monkeypatch, capsys):
    _broken_project(tmp_path, monkeypatch)
    rc = complete_cli(["--", ""])
    captured = capsys.readouterr()
    assert rc == _complete._EXIT_BROKEN
    # stdout stays EMPTY: an older hook reads candidates from stdout with
    # stderr discarded, so it shows exactly the silence it always did.
    assert captured.out == ""
    assert "failed to import" in captured.err
    assert "SyntaxError" in captured.err
    # The child failed fast and left a marker in the manifest slot, so the
    # next press answers instantly instead of paying the cold bound again.
    marker = _complete._load_manifest(str(_paths.cwd_manifest_path()))
    assert _complete._broken_line(marker)
    assert complete_cli(["--", ""]) == _complete._EXIT_BROKEN
    capsys.readouterr()
    # `--why` re-asks with the reason on stdout — the channel every shell
    # captures identically — for the hooks' second call.
    assert complete_cli(["--why", "--", ""]) == _complete._EXIT_BROKEN
    asked = capsys.readouterr()
    assert "failed to import" in asked.out
    assert asked.err == ""


def test_a_fixed_tasks_file_recovers_past_the_marker_age(tmp_path, monkeypatch, capsys):
    import os

    from livery.footman import _refresh

    proj = _broken_project(tmp_path, monkeypatch)
    assert complete_cli(["--", ""]) == _complete._EXIT_BROKEN
    capsys.readouterr()
    # Fix the file. The marker still stands but is short-lived: age it out by
    # hand and run the stale-while-revalidate spawn inline, so the dance is
    # deterministic — one stale-served press, then the healthy tree.
    (proj / "tasks.py").write_text("import footman\n\n@footman.task\ndef hi(): ...\n")
    manifest = _paths.cwd_manifest_path()
    old = time.time() - 60
    os.utime(manifest, (old, old))
    monkeypatch.setattr(
        _complete,
        "_spawn_refresh",
        lambda override=None, spawn_in=None: _refresh.refresh_cwd(*_paths.child_args()),
    )
    assert complete_cli(["--", ""]) == _complete._EXIT_BROKEN  # served once, stale
    capsys.readouterr()
    rc = complete_cli(["--", ""])
    out = capsys.readouterr().out.split()
    assert rc == 0
    assert "hi" in out


def test_a_broken_f_file_marks_its_own_key(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "cache"))
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    tf = proj / "custom.py"
    tf.write_text("import footman\n\nthis is a syntax error\n")
    rc = complete_cli(["--", f"-f={tf}", ""])
    captured = capsys.readouterr()
    assert rc == _complete._EXIT_BROKEN
    assert captured.out == ""
    assert "failed to import" in captured.err
    # The marker sits under the (cwd, file) key — a broken -f file must not
    # poison the plain-cwd cache.
    key = _paths.source_manifest_path(Path.cwd(), tf)
    assert _complete._broken_line(_complete._load_manifest(str(key)))
    assert not _paths.cwd_manifest_path().is_file()


# --- a user-level edit is a refresh trigger, not just the clock ---------------
# The age clock alone hid a config or user-tasks edit for a whole `max_age`
# (10 minutes by default) — exactly the window in which someone who just
# changed their config is pressing TAB to see whether it worked.


def _fresh_manifest(tmp_path, stamp):
    """A manifest file written just now, baking *stamp*."""
    import json

    path = tmp_path / "m.json"
    path.write_text(
        json.dumps({"completion_max_age": 600, "user_stamp": stamp}),
        encoding="utf-8",
    )
    return path


def test_user_stamp_notices_arrival_edits_and_removal(tmp_path, monkeypatch):
    config = tmp_path / "user.toml"
    monkeypatch.setenv("FOOTMAN_CONFIG", str(config))
    monkeypatch.setenv("FOOTMAN_CONFIG_DIR", str(tmp_path / "cfgdir"))

    absent = _paths.user_stamp()
    config.write_text("[tool.footman]\nsort = true\n", encoding="utf-8")
    written = _paths.user_stamp()
    assert written != absent  # an absent file is a value: its arrival counts

    config.write_text("[tool.footman]\nsort = false\n", encoding="utf-8")
    assert _paths.user_stamp() != written  # and so does an edit of one byte

    config.unlink()
    assert _paths.user_stamp() == absent  # back where it started


def test_a_user_tasks_file_counts_too(tmp_path, monkeypatch):
    cfgdir = tmp_path / "cfgdir"
    cfgdir.mkdir()
    monkeypatch.setenv("FOOTMAN_CONFIG", str(tmp_path / "user.toml"))
    monkeypatch.setenv("FOOTMAN_CONFIG_DIR", str(cfgdir))
    before = _paths.user_stamp()
    (cfgdir / "tasks.py").write_text("from footman import task\n", encoding="utf-8")
    assert _paths.user_stamp() != before


def test_a_moved_stamp_refreshes_a_manifest_the_clock_calls_fresh(
    tmp_path, monkeypatch
):
    from livery.footman import _complete

    monkeypatch.setenv("FOOTMAN_CONFIG", str(tmp_path / "user.toml"))
    monkeypatch.setenv("FOOTMAN_CONFIG_DIR", str(tmp_path / "cfgdir"))
    path = _fresh_manifest(tmp_path, "stale-stamp")
    spawned: list[object] = []
    monkeypatch.setattr(
        _complete, "_spawn_refresh", lambda o=None, s=None: spawned.append(o)
    )
    import json

    _complete._maybe_refresh(str(path), json.loads(path.read_text()))
    assert spawned  # rebuilt behind the answer, minutes before the clock would


def test_an_unchanged_stamp_leaves_a_fresh_manifest_alone(tmp_path, monkeypatch):
    from livery.footman import _complete

    monkeypatch.setenv("FOOTMAN_CONFIG", str(tmp_path / "user.toml"))
    monkeypatch.setenv("FOOTMAN_CONFIG_DIR", str(tmp_path / "cfgdir"))
    path = _fresh_manifest(tmp_path, _paths.user_stamp())
    spawned: list[object] = []
    monkeypatch.setattr(
        _complete, "_spawn_refresh", lambda o=None, s=None: spawned.append(o)
    )
    import json

    _complete._maybe_refresh(str(path), json.loads(path.read_text()))
    assert not spawned  # nothing changed: the warm press stays free


def test_a_manifest_without_a_stamp_falls_back_to_the_clock(tmp_path, monkeypatch):
    # Written by an older footman: no stamp to compare, so the age rule owns
    # it exactly as it always did — never a forced rebuild on every press.
    import json

    from livery.footman import _complete

    path = tmp_path / "m.json"
    path.write_text(json.dumps({"completion_max_age": 600}), encoding="utf-8")
    spawned: list[object] = []
    monkeypatch.setattr(
        _complete, "_spawn_refresh", lambda o=None, s=None: spawned.append(o)
    )
    _complete._maybe_refresh(str(path), json.loads(path.read_text()))
    assert not spawned


def test_off_still_means_no_background_rebuilds(tmp_path, monkeypatch):
    # `completion.max_age = off` asks for no background rebuilds at all; a
    # moved stamp is a trigger, not a licence to override that.
    import json

    from livery.footman import _complete

    monkeypatch.setenv("FOOTMAN_CONFIG", str(tmp_path / "user.toml"))
    monkeypatch.setenv("FOOTMAN_CONFIG_DIR", str(tmp_path / "cfgdir"))
    path = tmp_path / "m.json"
    path.write_text(
        json.dumps({"completion_max_age": 0, "user_stamp": "stale-stamp"}),
        encoding="utf-8",
    )
    spawned: list[object] = []
    monkeypatch.setattr(
        _complete, "_spawn_refresh", lambda o=None, s=None: spawned.append(o)
    )
    _complete._maybe_refresh(str(path), json.loads(path.read_text()))
    assert not spawned


def test_the_marker_ages_fast_and_spawns_with_the_override(tmp_path, monkeypatch):
    import os

    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "cache"))
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    tf = proj / "custom.py"
    tf.write_text("import footman\n\nthis is a syntax error\n")
    assert complete_cli(["--", f"-f={tf}", ""]) == _complete._EXIT_BROKEN
    key = _paths.source_manifest_path(Path.cwd(), tf)
    old = time.time() - 60
    os.utime(key, (old, old))
    spawns: list[tuple[str | None, str | None]] = []
    monkeypatch.setattr(
        _complete,
        "_spawn_refresh",
        lambda override=None, spawn_in=None: spawns.append((override, spawn_in)),
    )
    assert complete_cli(["--", f"-f={tf}", ""]) == _complete._EXIT_BROKEN
    # The aged marker spawned a rebuild of ITS OWN key: the override rides.
    assert spawns == [(str(tf), None)]


def test_the_marker_names_the_stale_environment_fix(tmp_path):
    # In a pinned project, a failed *import* is the stale-venv shape: the
    # marker says so instead of leaving a bare ModuleNotFoundError as the
    # whole story. Anything else keeps the plain telling.
    from livery.footman import _refresh

    exc: BaseException = RuntimeError(
        "plugin 'w': failed to import (ModuleNotFoundError: No module named 'yaml')"
    )
    exc.__cause__ = ModuleNotFoundError("No module named 'yaml'")
    where = tmp_path / "m.json"
    _refresh._write_marker(where, exc, project=tmp_path)
    line = _complete._broken_line(_complete._load_manifest(str(where)))
    assert line is not None and "run uv sync" in line
    _refresh._write_marker(where, exc)  # no pinned project: no advice
    line = _complete._broken_line(_complete._load_manifest(str(where)))
    assert line is not None and "uv sync" not in line
    _refresh._write_marker(where, RuntimeError("boom"), project=tmp_path)
    line = _complete._broken_line(_complete._load_manifest(str(where)))
    assert line is not None and "uv sync" not in line


def test_stock_complete_dispatch_keys_the_brand_version(tmp_path, monkeypatch):
    import livery.footman as footman
    from livery.footman import _paths

    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "cache"))
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "tasks.py").write_text("import footman\n\n@footman.task\ndef hi(): ...\n")
    monkeypatch.chdir(proj)
    monkeypatch.setattr(sys, "argv", ["fm", "--complete", "--", ""])
    with pytest.raises(SystemExit):
        footman.main()
    # The dispatch must configure the same (prog, version, builtins) triple
    # the execution path does, or global mode keeps two different manifests.
    assert _paths._brand_version == footman.__version__


# --- file-path completion for Path values ------------------------------------


def test_path_value_globals_signal_file_completion(tree):
    from livery.footman._complete import _FILES, _GLOBAL_FILES, _GLOBALS

    assert _GLOBAL_FILES <= _GLOBALS  # every file-global is a known global
    assert complete(tree, ["-f="]) == [_FILES]
    assert complete(tree, ["--config="]) == [_FILES]
    assert complete(tree, ["-C="]) == [_FILES]
    assert complete(tree, ["-C", "="]) == [_FILES]  # bash-split form
    assert complete(tree, ["--where="]) != [_FILES]  # --where takes a task


def test_complete_cli_exits_files_for_a_path_value(tmp_path, capsys):
    from livery.footman._complete import _EXIT_FILES

    m = tmp_path / "m.json"
    m.write_text(
        f'{{"schema": {_complete._SCHEMA}, "tree": {{"tasks": {{}}, "groups": {{}}}}}}'
    )
    rc = complete_cli(["--manifest", str(m), "--", "-f="])
    assert rc == _EXIT_FILES
    assert capsys.readouterr().out == ""


def test_path_typed_option_value_signals_file_completion():
    from livery.footman._complete import _FILES

    with registry.capture() as root:

        @task
        def fetch(out: Path = Path(".")):
            "Fetch."

    built = _manifest.build_manifest(root)["tree"]
    assert complete(built, ["fetch", "--out="]) == [_FILES]
    # a plain str option value has no such signal — it stays empty, so the
    # shell never bluntly offers files where a name was wanted.
    with registry.capture() as root2:

        @task
        def greet(name: str = "world"):
            "Greet."

    built2 = _manifest.build_manifest(root2)["tree"]
    assert complete(built2, ["greet", "--name="]) == []


def test_path_positional_signals_file_completion():
    from livery.footman._complete import _FILES

    with registry.capture() as root:

        @task
        def deploy(target: Path, *extra: Path):
            "Deploy."

    built = _manifest.build_manifest(root)["tree"]
    assert complete(built, ["deploy", ""]) == [_FILES]  # the Path positional
    assert complete(built, ["deploy", "a", ""]) == [_FILES]  # the Path variadic
    assert complete(built, ["deploy", "-"]) != [_FILES]  # a dash reaches options

    # a plain str positional is not a file
    with registry.capture() as root2:

        @task
        def greet(name: str):
            "Greet."

    built2 = _manifest.build_manifest(root2)["tree"]
    assert complete(built2, ["greet", ""]) != [_FILES]


# --- dynamic completers: recomputed fresh, never the baked snapshot -----------


def _demo_suggest():
    return ["alpha", "beta"]


def test_dynamic_option_signals_recompute():
    from livery.footman._complete import _DYNAMIC

    with registry.capture() as root:

        @task
        def deploy(target: Annotated[str, suggest(_demo_suggest)] = ""):
            "Deploy."

    built = _manifest.build_manifest(root)["tree"]
    # the value is dynamic → defer to a fresh recompute, carrying the
    # partial, the emission prefix (whole-token shells re-attach `--opt=`;
    # bash completes the bare value), the param name, and the task path
    assert complete(built, ["deploy", "--target="]) == [
        _DYNAMIC,
        "",
        "--target=",
        "target",
        "deploy",
    ]
    assert complete(built, ["deploy", "--target", "=", ""]) == [
        _DYNAMIC,
        "",
        "",
        "target",
        "deploy",
    ]


def test_a_runnable_groups_arity_walks_like_the_splitter_reads():
    # Audit M12, verified resolved by the segment collapse: the walk and the
    # splitter now agree on every bare word after a runnable group. A word
    # inside the default's arity is the default's *value* (orchestration.md:
    # "a bare word after the group is the default's value"), so the tail
    # keeps completing the default's options — never the same-named child's.
    # Past the arity, a nested member's bare name is the spelling the
    # splitter refuses ("nested tasks use dots"), so the walk stays silent.
    with registry.capture() as root:
        rg = registry.group("rg")

        @rg.default
        def deploy(target: str, flag: bool = False):
            "Deploy."

        @rg.task
        def status(verbose: bool = False):
            "Status."

        z = registry.group("z")

        @z.default
        def zero(flag: bool = False):
            "No positionals."

        @z.task
        def member(verbose: bool = False):
            "Member."

    built = _manifest.build_manifest(root)["tree"]
    # `rg status` binds status → target: the default's tail, not the child's.
    assert "--flag" in complete(built, ["rg", "status", "--"])
    assert "--verbose" not in complete(built, ["rg", "status", "--"])
    # The dotted spelling is the child.
    assert "--verbose" in complete(built, ["rg.status", "--"])
    # Arity exhausted: `z member` is the refused spelling — silence, like
    # the splitter's ChainError, never the member's (or anyone's) options.
    assert complete(built, ["z", "member", "--"]) == []


def _dynamic_project(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    (proj / "tasks.py").write_text(
        "from pathlib import Path\n"
        "from typing import Annotated\n"
        "from footman import task\n"
        "from footman.params import suggest\n\n"
        "def _targets():\n"
        "    return Path('targets.txt').read_text().split()\n\n"
        "@task\n"
        "def deploy(target: Annotated[str, suggest(_targets)] = ''):\n"
        "    'Deploy.'\n"
    )
    return proj


def test_suggest_values_runs_the_completer_fresh(tmp_path, monkeypatch):
    from livery.footman import _suggest

    proj = _dynamic_project(tmp_path)
    monkeypatch.chdir(proj)
    (proj / "targets.txt").write_text("gamma\ndelta\n")
    assert _suggest._values("target", ["deploy"], {}) == ["gamma", "delta"]
    # a miss — unknown param or task — is empty, never an error
    assert _suggest._values("nope", ["deploy"], {}) == []
    assert _suggest._values("target", ["ghost"], {}) == []


def test_the_dynamic_child_protocol_holds_from_both_sides(
    tmp_path, monkeypatch, capsys
):
    # Audit M100: the argv the hot path builds and the flags the child
    # parses were each tested alone — renaming a flag on either side left
    # the whole suite green while every dynamic TAB quietly returned
    # nothing. Here the child parses exactly the argv the parent built.
    from livery.footman import _complete, _suggest

    proj = _dynamic_project(tmp_path)
    monkeypatch.chdir(proj)
    (proj / "targets.txt").write_text("gamma\ndelta\n")

    captured: dict[str, list[str]] = {}

    class _Done:
        returncode = 0
        stdout = ""

    def capture_run(cmd, **kw):
        captured["argv"] = list(cmd)
        return _Done()

    monkeypatch.setattr(subprocess, "run", capture_run)
    _complete._fresh_dynamic("target", ["deploy"], ["deploy", "--target="])
    argv = captured["argv"]
    assert argv[1:4] == ["-P", "-m", "livery.footman._suggest"]  # the spawn contract
    assert _suggest.main(argv[4:]) == 0  # the child reads the parent's words
    assert capsys.readouterr().out.splitlines() == ["gamma", "delta"]


def test_import_time_chatter_is_not_served_as_candidates(tmp_path, monkeypatch, capsys):
    # The child's stdout IS the candidate channel, and importing the tasks
    # file happens before candidates are written — a print() at module scope
    # used to reach the shell as a completion the user could insert.
    from livery.footman import _suggest

    proj = _dynamic_project(tmp_path)
    (proj / "tasks.py").write_text(
        "print('loading config...')\n" + (proj / "tasks.py").read_text()
    )
    monkeypatch.chdir(proj)
    (proj / "targets.txt").write_text("gamma\ndelta\n")
    assert _suggest.main(["--param", "target", "--path", "deploy"]) == 0
    assert capsys.readouterr().out == "gamma\ndelta\n"  # candidates, no chatter


def test_a_multiline_completer_value_is_one_candidate(tmp_path, monkeypatch, capsys):
    # The candidate protocol is newline-delimited, so a value carrying a
    # newline split into two bogus candidates (audit L48). A completion
    # token has no legal newline: the value's first line is the candidate.
    from livery.footman import _suggest

    proj = _dynamic_project(tmp_path)
    monkeypatch.chdir(proj)
    (proj / "targets.txt").write_bytes(b"gamma\ndelta broken\nrest\n")
    (proj / "tasks.py").write_text(
        (proj / "tasks.py")
        .read_text()
        .replace(
            "return Path('targets.txt').read_text().split()",
            "return ['gamma', 'delta\\nbroken']",
        )
    )
    assert _suggest.main(["--param", "target", "--path", "deploy"]) == 0
    assert capsys.readouterr().out.splitlines() == ["gamma", "delta"]


def test_suggest_main_swallows_a_failing_completer(tmp_path, monkeypatch, capsys):
    from livery.footman import _suggest

    proj = _dynamic_project(tmp_path)
    monkeypatch.chdir(proj)
    # no targets.txt → the completer raises → no candidates, exit 0
    assert _suggest.main(["--param", "target", "--path", "deploy"]) == 0
    assert capsys.readouterr().out == ""


def test_dynamic_completion_is_fresh_not_baked(tmp_path, monkeypatch, capsys):
    from livery.footman import _app

    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "cache"))
    proj = _dynamic_project(tmp_path)
    monkeypatch.chdir(proj)

    (proj / "targets.txt").write_text("alpha\nbeta\n")
    assert _app.run(["--list"]) == 0  # bake the manifest with alpha/beta
    capsys.readouterr()

    (proj / "targets.txt").write_text("gamma\ndelta\n")  # the world moved on
    complete_cli(["--", "deploy", "--target", "=", ""])
    assert capsys.readouterr().out.split() == ["gamma", "delta"]  # fresh, not baked
    (proj / "targets.txt").write_text("eta\ntheta\n")
    complete_cli(["--", "deploy", "--target="])  # whole-token shells: prefixed
    assert capsys.readouterr().out.split() == ["--target=eta", "--target=theta"]


def test_fresh_dynamic_passes_context_and_falls_back(monkeypatch):
    import subprocess

    from livery.footman import _complete

    captured: dict[str, list[str]] = {}

    def ok(cmd, **k):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "gamma\ndelta\n", "")

    monkeypatch.setattr(subprocess, "run", ok)
    args = ["-f=x.py", "--config=c.toml", "deploy", "--target="]
    assert _complete._fresh_dynamic("target", ["deploy"], args) == ["gamma", "delta"]
    cmd = captured["cmd"]  # the subprocess carries the target and the context
    # `-P` before `-m`, for the same reason the refresh child carries it: a
    # `footman.py` in the completed directory must not answer this import.
    assert cmd[1:3] == ["-P", "-m"]
    assert cmd[cmd.index("--param") + 1] == "target"
    assert cmd[cmd.index("--path") + 1] == "deploy"
    assert cmd[cmd.index("--tasks-file") + 1] == "x.py"
    assert cmd[cmd.index("--config") + 1] == "c.toml"

    def timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="x", timeout=2)

    monkeypatch.setattr(subprocess, "run", timeout)
    assert _complete._fresh_dynamic("target", ["deploy"], ["a", ""]) is None

    def nonzero(*a, **k):
        return subprocess.CompletedProcess("x", 1, "", "")

    monkeypatch.setattr(subprocess, "run", nonzero)
    assert _complete._fresh_dynamic("target", ["deploy"], ["a", ""]) is None


# --- cold cache: build once, don't answer empty ------------------------------


def test_cold_cache_builds_and_serves(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "cache"))
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    (proj / "tasks.py").write_text(
        "from footman import task\n\n@task\ndef lint(): ...\n@task\ndef check(): ...\n"
    )
    monkeypatch.chdir(proj)
    # nothing cached: the first completion builds the manifest and serves it,
    # rather than answering empty until the first real run
    complete_cli(["--", ""])
    out = capsys.readouterr().out.split()
    assert "lint" in out and "check" in out, _cold_evidence(tmp_path / "cache")


def test_a_planted_footman_py_is_never_imported_by_a_tab(tmp_path, monkeypatch, capsys):
    # The builder child runs `python -c "… from footman import _refresh …"`,
    # and `-c` heads sys.path with the directory it was spawned in. A
    # `footman.py` sitting in the completed directory would answer that import
    # first, so one TAB in a directory somebody else wrote would execute its
    # code. `-P` drops the implicit entry, and nothing legitimate loses it:
    # `_discover` inserts a tasks file's own parent itself.
    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "cache"))
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    (proj / "tasks.py").write_text("from footman import task\n\n@task\ndef lint(): ...")
    (proj / "footman.py").write_text(  # the plant, waiting to be imported
        "import pathlib\n\npathlib.Path(__file__).with_name('ran').touch()\n"
    )
    monkeypatch.chdir(proj)

    complete_cli(["--", ""])

    assert not (proj / "ran").exists()  # the plant never ran
    # …and the child reached the *real* footman, so the build still landed
    assert "lint" in capsys.readouterr().out.split(), _cold_evidence(tmp_path / "cache")


def test_cold_f_cache_builds_and_serves(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "cache"))
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    (proj / "other.py").write_text(
        "from footman import task\n\n@task\ndef ship(): ...\n"
    )
    monkeypatch.chdir(proj)
    # a finished `-f <file>` with a cold cache builds that file's (cwd, file)
    # manifest and serves it, the same as a plain cold TAB — not empty
    complete_cli(["--", "-f=other.py", ""])
    out = capsys.readouterr().out.split()
    assert "ship" in out, _cold_evidence(tmp_path / "cache", "other.py")


def test_a_task_less_directory_answers_at_once(tmp_path, monkeypatch):
    # $HOME is where `fm <TAB>` gets typed most, and there is nothing there to
    # complete: no project cascade, no user tasks file, no built-ins. Say so
    # instantly — never spawn a builder that can only come back empty after
    # the full cold bound, which is what made every such TAB cost 3 seconds.
    from livery.footman import _complete

    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("FOOTMAN_CONFIG_DIR", str(tmp_path / "config"))
    _paths.configure(builtin=())
    bare = tmp_path / "bare"
    bare.mkdir()
    monkeypatch.chdir(bare)
    spawned: list[object] = []
    monkeypatch.setattr(_complete, "_spawn_refresh", lambda o=None: spawned.append(o))

    started = time.monotonic()
    assert _complete.complete_cli(["--", ""]) == 0
    assert not spawned  # nothing to build, so nothing was asked to build
    assert time.monotonic() - started < 0.5  # nowhere near _COLD_TIMEOUT


def test_a_task_less_directory_serves_the_builtins(tmp_path, monkeypatch, capsys):
    # With built-ins declared, the same directory is *global mode*: the walk
    # finds no project, so the shared global manifest answers — one build for
    # every project-less directory, not one per directory.
    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("FOOTMAN_CONFIG_DIR", str(tmp_path / "config"))
    _paths.configure(builtin=("footman.self",))
    bare = tmp_path / "bare"
    bare.mkdir()
    monkeypatch.chdir(bare)

    complete_cli(["--", ""])
    offers = capsys.readouterr().out.split()
    assert any(offer.startswith("self.") for offer in offers), offers


def test_cold_build_times_out_to_none(tmp_path, monkeypatch):
    from livery.footman import _complete

    # accept the override/spawn_in args; still no build ever lands
    monkeypatch.setattr(
        _complete, "_spawn_refresh", lambda override=None, spawn_in=None: None
    )
    monkeypatch.setattr(_complete, "_COLD_TIMEOUT", 0.1)
    assert _complete._cold_build(str(tmp_path / "never.json"), None) is None


def test_cold_build_skips_missing_f_file(tmp_path, monkeypatch):
    from livery.footman import _complete

    # a still-being-typed or missing -f value has no file to build: return None
    # at once, never spawning a builder that would only stall for the timeout
    spawned: list[str | None] = []

    def _spawn(override: str | None = None) -> None:
        spawned.append(override)

    monkeypatch.setattr(_complete, "_spawn_refresh", _spawn)
    got = _complete._cold_build(str(tmp_path / "m.json"), str(tmp_path / "missing.py"))
    assert got is None and spawned == []


def test_child_argv_mirrors_the_real_spawn():
    # The failure diagnostic re-runs the *same* child the hot path detaches,
    # so it reads that command out of `_spawn_refresh` rather than retyping
    # it. This is the drift guard: if the spawn ever stops going through
    # `subprocess.Popen`, the recorder comes back empty and says so here —
    # not on the CI failure the diagnostic exists to explain.
    plain = _child_argv()
    assert plain is not None
    # `-P` ahead of the mode flag: `-c` would otherwise head the child's
    # sys.path with the completed directory, where a planted `footman.py`
    # would answer its own `import footman`.
    assert plain[0] == sys.executable and plain[1:3] == ["-P", "-c"]
    assert "_refresh.refresh_cwd(*sys.argv[1:])" in plain[3]
    # …and the brand's resolved locations behind it, so the detached child
    # writes this CLI's cache rather than stock footman's.
    assert plain[4:] == _paths.child_args()

    override = _child_argv("other.py")
    assert override is not None
    assert override[1:3] == ["-P", "-c"]
    assert "_refresh.refresh_source" in override[3]
    assert override[4] == "other.py" and override[5:] == _paths.child_args()


def test_cold_evidence_reports_the_childs_own_words(tmp_path, monkeypatch):
    # What the next windows-latest failure will actually print. The child is
    # faked here (the point is the report, not the build): its captured
    # stderr, the argv it was given, and the ground it stands on — cwd,
    # interpreter, discoverable task files, polled manifest path.
    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "cache"))
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    (proj / "tasks.py").write_text("from footman import task\n\n@task\ndef go(): ...\n")
    monkeypatch.chdir(proj)

    def fake_run(cmd, **kwargs):
        if "_paths.find_repo_root" in cmd[2]:  # the where-am-I probe
            return subprocess.CompletedProcess(cmd, 0, "cwd=elsewhere", "")
        return subprocess.CompletedProcess(cmd, 1, "", "ImportError: no footman\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    from livery.footman import _paths

    report = _cold_evidence(tmp_path / "cache")
    assert "cache dir" in report and "does not exist" in report
    assert "child sees cwd=elsewhere" in report  # the child's own words
    assert "ImportError: no footman" in report
    assert "child exit 1" in report
    assert "_refresh.refresh_cwd(*sys.argv[1:])" in report
    # Paths land verbatim. A `repr` (an f-string over a list, json.dumps)
    # doubles every backslash, and this report is read on Windows.
    assert sys.executable in report
    assert str(proj / "tasks.py") in report  # the builder can find a task file
    assert str(_paths.cwd_manifest_path()) in report


# --- chain-aware completion -----------------------------------------------------


def test_second_segment_options_are_the_second_tasks(tree):
    # `check` has no --mode; the --mo must complete against lint's options.
    out = complete(tree, ["check", "lint", "--mo"])
    # Both spellings: the bare mention that stands for the default, and the
    # `=` that is the only way to pass a value.
    assert [c.split("\t", 1)[0] for c in out] == ["--mode", "--mode="]


def test_next_task_name_completes_after_a_chain(tree):
    assert _names(complete(tree, ["lint", "--fix", "che"])) == ["check"]


def test_option_value_not_confused_with_next_task(tree):
    # "--mode=" is the value position: its choices complete, not task names.
    out = complete(tree, ["lint", "--mode="])
    assert set(out) == {"--mode=strict", "--mode=loose"}


def test_dotted_descent_in_a_later_segment(tree):
    out = _names(complete(tree, ["lint", "--fix", "docs."]))
    assert set(out) == {"docs.serve", "docs.build"}


def test_plus_resets_the_segment(tree):
    out = _names(complete(tree, ["lint", "+", ""]))
    assert "check" in out and "docs." in out


def test_nothing_after_passthrough(tree):
    assert complete(tree, ["check", "--", "anything", ""]) == []


def test_given_options_are_not_reoffered(tree):
    # `fm lint --fix <TAB>` must not suggest --fix again — a flag binds once.
    out = complete(tree, ["lint", "--fix", ""])
    assert "--fix" not in out
    assert "--mode=" in out  # the unused ones remain
    assert complete(tree, ["lint", "--fix", "--f"]) == []


def test_negated_flag_counts_as_used(tree):
    out = complete(tree, ["lint", "--no-fix", ""])
    assert "--fix" not in out


def test_repeatable_options_stay_offered(tree):
    # --paths is list-valued: repeating it is the grammar, keep offering it.
    out = complete(tree, ["lint", "--paths=a", ""])
    assert "--paths=" in out


def test_used_options_reset_per_segment(tree):
    # --fix bound to the first lint segment; a second task starts fresh.
    out = complete(tree, ["lint", "--fix", "check", "lint", ""])
    assert "--fix" in out


def test_completion_output_is_lf_only(tree, tmp_path, monkeypatch, capsysbinary):
    """The completion protocol is LF, on every platform.

    Windows text-mode stdout would translate to CRLF, and a shell that
    reads lines literally (git-bash's `read`) keeps the carriage return
    and completes `--fix\r` — a stray CR at the user's cursor. Found by
    driving the real git-bash on a Windows runner, pinned here so no
    platform can reintroduce it.
    """
    import json

    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps({"schema": _complete._SCHEMA, "tree": tree}), encoding="utf-8"
    )
    complete_cli(["--manifest", str(manifest), "--", ""])
    out = capsysbinary.readouterr().out
    assert out, "the fixture tree should complete to something"
    assert b"\r" not in out
    assert out.endswith(b"\n")


def test_segment_wise_abbreviation_expands_uniquely(tree):
    # The other half of the `cd` idiom: each typed segment prefix-matches its
    # tree level, and a whole word that resolves uniquely emits the one
    # expanded candidate — `fm w.m⇥` → `workspace.mount`.
    assert _names(complete(tree, ["w.m"])) == ["workspace.mount"]
    assert _names(complete(tree, ["w.mo"])) == ["workspace.mount"]


def test_abbreviation_is_completion_only(tree):
    # The runtime resolver stays strict, so an abbreviation that runs today
    # cannot change meaning when a new task lands.
    import pytest as _pytest

    from livery.footman._split import ChainError, split_chain

    with _pytest.raises(ChainError):
        split_chain(tree, ["w.mount"])


def test_ambiguous_segment_expands_up_to_it_and_lists_matches(tree):
    # `d.` could open db, deps, dns, docker, or docs: expand up to the
    # ambiguous level and list its matches, dot-marked.
    out = set(_names(complete(tree, ["d.serve"])))
    assert out == {"db.", "deps.", "dns.", "docker.", "docs."}


def test_exact_segment_name_beats_abbreviation(tree):
    # `docs.` names a group exactly; deps/dns/… sharing the `d` prefix must
    # not turn it ambiguous.
    assert set(_names(complete(tree, ["docs."]))) == {"docs.serve", "docs.build"}


def test_leaf_name_fallback_rescues_a_known_task_name(tree):
    # Zero top-level matches: complete against last segments over the flat
    # index — the "I know the task, not where it lives" rescue.
    assert _names(complete(tree, ["serve"])) == ["docs.serve"]
    assert set(_names(complete(tree, ["mig"]))) == {"db.migrate"}


def test_leaf_fallback_never_fires_on_a_valid_descent(tree):
    # `bu` matches the top-level `build` task, so the fallback must not add
    # docs.build alongside it.
    assert _names(complete(tree, ["bu"])) == ["build"]


def test_completion_schema_mirrors_manifest():
    # Drift pin: the hot path's schema literal must match the manifest's, so
    # bumping SCHEMA_VERSION without teaching the completer fails CI.
    from livery.footman import _complete as hot
    from livery.footman import _manifest as cold

    assert hot._SCHEMA == cold.SCHEMA_VERSION


def test_complete_cli_rejects_a_mismatched_schema(tree, tmp_path, capsys):
    # A cache baked by a different footman is never walked: the --manifest
    # path (no rebuild machinery) stays silent instead of tracebacking.
    import json as _json

    path = tmp_path / "m.json"
    path.write_text(_json.dumps({"schema": 999, "tree": tree}))
    assert complete_cli(["--manifest", str(path), "--", "che"]) == 0
    assert capsys.readouterr().out == ""


def test_stale_schema_cache_rebuilds_instead_of_walking(tmp_path, monkeypatch, capsys):
    # The transition TAB: a cwd cache written by a pre-upgrade footman (wrong
    # schema) routes into the cold build, so the first post-upgrade TAB serves
    # correct candidates instead of tracebacking on a reshaped tree.
    import json as _json

    from livery.footman import _paths

    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "cache"))
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    (proj / "tasks.py").write_text(
        "from footman import task\n\n@task\ndef lint(): ...\n"
    )
    monkeypatch.chdir(proj)
    stale = _paths.cwd_manifest_path()
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text(_json.dumps({"schema": 0, "tree": {"bogus": True}}))
    complete_cli(["--", "li"])
    out = capsys.readouterr().out.split()
    assert "lint" in out, _cold_evidence(tmp_path / "cache")


# --- comma-splitting values: the tail item completes on its own ---------------


def _csv_tree():
    with registry.capture() as root:

        @task
        def lint(
            paths: list[Path] | None = None,
            tags: list[Literal["alpha", "beta", "gamma"]] | None = None,
            names: Annotated[list[str], nosplit] | None = None,
            projects: Annotated[list[str], suggest(_demo_suggest)] | None = None,
        ):
            "Lint."

        @task
        def sweep(paths: Many[Path]):
            "Sweep."

        @task
        def stage(envs: Many[Literal["dev", "prod"]]):
            "Stage."

    return _manifest.build_manifest(root)["tree"]


def test_csv_path_option_signals_csv_files():
    from livery.footman._complete import _FILES, _FILES_CSV

    tree = _csv_tree()
    # Before any comma the plain signal serves — a hook from an older install
    # keeps its behaviour; mid-list the csv signal completes the tail item.
    assert complete(tree, ["lint", "--paths="]) == [_FILES]
    assert complete(tree, ["lint", "--paths=src,"]) == [_FILES_CSV]
    assert complete(tree, ["lint", "--paths", "=", "src,tes"]) == [_FILES_CSV]


def test_nosplit_value_keeps_commas_literal_in_completion():
    from livery.footman._complete import _FILES, _FILES_CSV

    tree = _csv_tree()
    # nosplit: the comma is part of the value, never a list separator.
    assert complete(tree, ["lint", "--names=a,"]) not in ([_FILES], [_FILES_CSV])


def test_csv_choice_option_completes_the_tail_item():
    from livery.footman._complete import _MORE

    tree = _csv_tree()
    # Whole-token shells re-attach the head; the typed item is not re-offered.
    # The reply leads with the continuation marker: these candidates are
    # elements of a comma-splitting value, so more may follow.
    assert set(complete(tree, ["lint", "--tags=alpha,"])) == {
        _MORE,
        "--tags=alpha,beta",
        "--tags=alpha,gamma",
    }
    assert complete(tree, ["lint", "--tags=alpha,g"]) == [_MORE, "--tags=alpha,gamma"]
    # bash's `=`-split word completes bare values, the head still attached.
    assert complete(tree, ["lint", "--tags", "=", "alpha,g"]) == [
        _MORE,
        "alpha,gamma",
    ]


def test_csv_dynamic_option_recomputes_the_tail():
    from livery.footman._complete import _DYNAMIC, _MORE

    tree = _csv_tree()
    # The typed head folds into the emission prefix; the fresh recompute
    # filters on the tail item alone. The continuation marker composes with
    # the dynamic sentinel — a dynamic list value is comma-continuable too.
    assert complete(tree, ["lint", "--projects=alpha,b"]) == [
        _MORE,
        _DYNAMIC,
        "b",
        "--projects=alpha,",
        "projects",
        "lint",
    ]
    assert complete(tree, ["lint", "--projects", "=", "alpha,b"]) == [
        _MORE,
        _DYNAMIC,
        "b",
        "alpha,",
        "projects",
        "lint",
    ]


def test_csv_path_positional_signals_csv_files():
    from livery.footman._complete import _FILES, _FILES_CSV

    tree = _csv_tree()
    assert complete(tree, ["sweep", ""]) == [_FILES]
    assert complete(tree, ["sweep", "src,"]) == [_FILES_CSV]


def test_csv_choice_positional_completes_the_tail_item():
    from livery.footman._complete import _MORE

    tree = _csv_tree()
    out = complete(tree, ["stage", "dev,"])
    assert "dev,prod" in out
    assert "dev,dev" not in out  # the typed item is not re-offered
    assert out[0] == _MORE  # mid-list the menu is pure, so it is marked


# --- the continuation marker: where it rides and where it must not ------------
#
# `\x00more` leads a reply whose candidates are elements of a comma-splitting
# value — `complete_cli` strips it into exit 102, the playground into its
# glue. Marked on shape (`multiple` and not `nosplit`), never on items
# remaining; and only on replies that are provably pure value menus.


def test_csv_choice_option_marked_from_the_first_element():
    from livery.footman._complete import _MORE

    tree = _csv_tree()
    # The attached reply is pure — nothing but this value's candidates — so
    # the first element is already continuable.
    out = complete(tree, ["lint", "--tags="])
    assert out[0] == _MORE
    assert set(out[1:]) == {"--tags=alpha", "--tags=beta", "--tags=gamma"}


def test_scalar_choice_value_is_never_marked():
    from livery.footman._complete import _MORE

    with registry.capture() as root:

        @task
        def paint(colour: Literal["red", "green"] = "red"):
            "Paint."

    tree = _manifest.build_manifest(root)["tree"]
    assert _MORE not in complete(tree, ["paint", "--colour="])
    assert _MORE not in complete(tree, ["paint", "--colour=r"])


def test_nosplit_value_is_never_marked():
    from livery.footman._complete import _MORE

    tree = _csv_tree()
    # nosplit: commas are part of the value, so there is no list to continue.
    assert _MORE not in complete(tree, ["lint", "--names=a"])
    assert _MORE not in complete(tree, ["lint", "--names=a,b"])


def test_positional_first_element_is_not_marked():
    from livery.footman._complete import _MORE

    with registry.capture() as root:

        @task
        def stage(envs: Many[Literal["dev", "prod"]], fix: bool = False):
            "Stage."

    tree = _manifest.build_manifest(root)["tree"]
    # The first element of a positional shares its menu with option rows —
    # a per-reply marker must not glue those. Mid-list the menu is pure.
    out = complete(tree, ["stage", ""])
    assert _MORE not in out
    assert "dev" in out and "--fix" in out  # the mixed menu, proven mixed
    assert complete(tree, ["stage", "dev,"])[0] == _MORE  # pure mid-list


def test_empty_reply_is_not_marked():
    from livery.footman._complete import _MORE

    tree = _csv_tree()
    # Everything already given: nothing to offer, so nothing to mark.
    assert _MORE not in complete(tree, ["lint", "--tags=alpha,beta,gamma,"])


def test_csv_files_stay_a_files_signal_not_a_marked_reply():
    from livery.footman._complete import _FILES_CSV, _MORE

    tree = _csv_tree()
    # Path values keep their own protocol (exit 100/101): the shell walks
    # the filesystem there, and the hooks' file branches already glue.
    out = complete(tree, ["lint", "--paths=src,"])
    assert out == [_FILES_CSV]
    assert _MORE not in out


def test_complete_cli_exits_more_with_clean_candidates(tmp_path, capsys):
    import json

    from livery.footman._complete import _EXIT_MORE

    with registry.capture() as root:

        @task
        def stage(envs: Many[Literal["dev", "prod"]]):
            "Stage."

    m = tmp_path / "m.json"
    m.write_text(json.dumps(_manifest.build_manifest(root)))
    rc = complete_cli(["--manifest", str(m), "--", "stage", "dev,"])
    assert rc == _EXIT_MORE
    out = capsys.readouterr().out
    # The marker leaves as the exit code alone: stdout is candidates only,
    # so a hook that has never heard of 102 parses them exactly as before.
    assert out.splitlines() == ["dev,prod"]
    assert chr(0) not in out


def test_complete_cli_unmarked_reply_still_exits_zero(tmp_path, capsys):
    import json

    with registry.capture() as root:

        @task
        def stage(envs: Many[Literal["dev", "prod"]]):
            "Stage."

    m = tmp_path / "m.json"
    m.write_text(json.dumps(_manifest.build_manifest(root)))
    assert complete_cli(["--manifest", str(m), "--", "sta"]) == 0
    assert "stage" in capsys.readouterr().out


def test_complete_cli_exits_csv_files_mid_list(tmp_path, capsys):
    import json

    from livery.footman._complete import _EXIT_FILES_CSV

    with registry.capture() as root:

        @task
        def lint(paths: list[Path] | None = None):
            "Lint."

    m = tmp_path / "m.json"
    m.write_text(json.dumps(_manifest.build_manifest(root)))
    rc = complete_cli(["--manifest", str(m), "--", "lint", "--paths=a,"])
    assert rc == _EXIT_FILES_CSV
    assert capsys.readouterr().out == ""


# --- every offered word says what it does ------------------------------------
#
# zsh and fish render `value\tdescription` into a right-aligned column and
# honour the user's own list-colors. Three emitters used to drop text footman
# already had, so a Tab on a flag listed names and nothing else.


def _described(offered: list[str]) -> dict[str, str]:
    """`{candidate: description}` — the wire format, unpacked."""
    pairs: list[tuple[str, str]] = []
    for line in offered:
        name, _, summary = line.partition("\t")
        pairs.append((name, summary))
    return dict(pairs)


def _declared(flag: str) -> str:
    """What `CORE_OPTIONS` says about *flag* — the source, not the copy."""
    from livery.footman import _split

    return next(h for n, _a, _k, _hi, _d, h in _split.GLOBALS if n == flag)


def test_a_core_globals_words_ride_in_the_manifest():
    """`_complete` may not import `_split`, so it knows the core flags by name
    and nothing else. Mirroring thirty-five help strings there as well would
    duplicate the one thing that rots; the words travel in the manifest the
    hot path already reads, written from the table that declares them."""
    reg = registry.Group("root")

    @reg.task
    def build(): ...

    tree = _manifest.build_manifest(reg)["tree"]
    offered = _described(complete(tree, ["--jo"]))
    assert offered["--jobs"], "a core global arrived with no description"
    # The bare mention stands for its default, so its line names the
    # resolved value (this manifest belongs to this machine); the attached
    # spelling carries the declared words alone.
    assert offered["--jobs"].startswith(_declared("--jobs") + "; default: ")
    assert offered["--jobs"].endswith("(computed)")
    assert offered["--jobs="] == _declared("--jobs")


def test_an_alias_carries_its_long_forms_words():
    # `-j` and `--jobs` are one option. A column that says what it does beats
    # one that says it has two spellings.
    reg = registry.Group("root")

    @reg.task
    def build(): ...

    tree = _manifest.build_manifest(reg)["tree"]
    offered = _described(complete(tree, ["-j"]))
    assert offered["-j"].startswith(_declared("--jobs") + "; default: ")
    assert offered["-j="] == _declared("--jobs")


def test_prog_is_substituted_before_it_reaches_a_shell():
    # `--help`'s line is written with a `{prog}` placeholder for the brand to
    # fill. Unsubstituted, a Tab would offer "help for {prog}" in braces.
    reg = registry.Group("root")

    @reg.task
    def build(): ...

    tree = _manifest.build_manifest(reg)["tree"]
    described = _described(complete(tree, ["--h"]))["--help"]
    assert "{prog}" not in described
    assert "help for fm" in described


def test_a_plugin_global_offers_the_help_it_declared():
    """The manifest has carried this text all along — `_global_spec` writes
    `spec["help"]` — and the emitter dropped it on the floor."""
    from livery.footman import compose

    reg = registry.Group("root")
    with registry.capture() as captured:
        compose.plugin("footman.env_files", into=captured)
    for opt in captured.contributions["globals"]:
        reg.contributions["globals"].append(opt)

    @reg.task
    def build(): ...

    tree = _manifest.build_manifest(reg)["tree"]
    assert _described(complete(tree, ["--env"]))["--env-file="] == (
        "the .env file to load"
    )


def test_the_off_spelling_says_what_it_turns_off():
    """A bool global answers to `--no-x` too, and the splitter accepts it — so
    completion offers it. One option read from the other end, so it carries
    the same line rather than none."""
    from livery.footman import GlobalOption

    reg = registry.Group("root")
    with registry.capture() as captured:
        GlobalOption("telemetry", bool, default=True, help="send usage pings")
    for opt in captured.contributions["globals"]:
        reg.contributions["globals"].append(opt)

    @reg.task
    def build(): ...

    tree = _manifest.build_manifest(reg)["tree"]
    assert _described(complete(tree, ["--no-tele"]))["--no-telemetry"] == (
        "send usage pings"
    )


def test_a_runnable_groups_default_options_are_described_too():
    """The same parameters, reached through the group instead of the task —
    `fm ci --<Tab>` was the one option position still answering bare."""
    reg = registry.Group("root")
    ci = reg.group("ci")

    @ci.default
    def run_all(strict: bool = False):
        """Run the whole suite.

        Args:
            strict: fail on the first warning
        """

    tree = _manifest.build_manifest(reg)["tree"]
    assert _described(complete(tree, ["ci", "-"])) == {
        "--strict": "fail on the first warning"
    }


def _runnable_group_tree():
    """A runnable group whose default action carries one of each value shape."""
    reg = registry.Group("root")
    ci = reg.group("ci")

    @ci.default
    def run_all(
        mode: Literal["fast", "slow"] = "fast",
        report: Path = Path("out.txt"),
        branch: Annotated[str, suggest(_demo_suggest)] = "main",
    ): ...

    @ci.task
    def lint(): ...

    return _manifest.build_manifest(reg)["tree"]


@pytest.mark.parametrize(
    "partial",
    ["--mode=", "--mode=f", "--report=", "--branch=", "--branch=a"],
)
def test_a_bare_group_completes_its_defaults_values(partial):
    """`fm ci --mode=<Tab>` answers what `fm ci.default --mode=<Tab>` answers.

    The two spellings are the same command to the runner, so they must be the
    same command to TAB. The group spelling used to build no segment at all,
    which is where every value behaviour lives: choices, the file hand-off and
    the fresh-completer signal all came back empty under the bare name and
    full under the dotted one.
    """
    tree = _runnable_group_tree()
    assert complete(tree, ["ci", partial]) == complete(tree, ["ci.default", partial])
    assert complete(tree, ["ci", partial])  # …and neither of them is silence


def test_a_bare_group_still_offers_its_siblings():
    """Opening the default's tail must not close the descent: the group's own
    subtasks are still what a bare word after it could be."""
    tree = _runnable_group_tree()
    offered = set(_names(complete(tree, ["ci", ""])))
    assert {"--mode=", "ci", "ci.lint"} <= offered


# --- matching(): a path value's glob rides out to the shell -------------------


def test_a_path_value_hands_off_its_glob():
    """footman never walks the filesystem to complete a path — the shell
    does. `matching()` is what rides along, so the shell narrows what it
    walks instead of offering every file in the directory."""
    reg = registry.Group("root")

    @reg.task
    def load(env_file: Annotated[Path, matching(".env*")] = Path(".env")): ...

    tree = _manifest.build_manifest(reg)["tree"]
    (out,) = complete(tree, ["load", "--env-file="])
    assert out == _complete._FILES + "\t.env*"


def test_a_path_value_without_one_hands_off_exactly_as_before():
    reg = registry.Group("root")

    @reg.task
    def load(any_file: Path = Path("x")): ...

    tree = _manifest.build_manifest(reg)["tree"]
    assert complete(tree, ["load", "--any-file="]) == [_complete._FILES]


def test_the_glob_survives_a_comma_splitting_value():
    # Mid-list, the hand-off is the CSV one — and still carries the pattern.
    reg = registry.Group("root")

    @reg.task
    def load(paths: Annotated[list[Path], matching("*.json")] = ()): ...  # type: ignore[assignment]

    tree = _manifest.build_manifest(reg)["tree"]
    (out,) = complete(tree, ["load", "--paths=a.json,"])
    assert out == _complete._FILES_CSV + "\t*.json"


def test_a_glob_beginning_with_csv_is_not_read_as_a_comma_value():
    """`_FILES_CSV` starts with `_FILES`, so a concatenated encoding would
    turn `matching("-csv*")` on a plain path into a comma-splitting
    hand-off. The tag is compared exactly instead."""
    reg = registry.Group("root")

    @reg.task
    def load(f: Annotated[Path, matching("-csv*")] = Path("x")): ...

    tree = _manifest.build_manifest(reg)["tree"]
    (out,) = complete(tree, ["load", "--f="])
    assert out.split("\t", 1)[0] == _complete._FILES


def test_the_glob_reaches_the_wire_as_stdout_beside_exit_100(tmp_path, capsys):
    """The contract every hook reads: exit 100 says "a path value", and one
    line of stdout is the pattern to filter by. No stdout means every file —
    what each hook already did before there was a pattern to send, so an
    older hook against a newer footman keeps its old behaviour."""
    import json

    reg = registry.Group("root")

    @reg.task
    def load(env_file: Annotated[Path, matching(".env*")] = Path(".env")): ...

    @reg.task
    def plain(any_file: Path = Path("x")): ...

    m = tmp_path / "m.json"
    m.write_text(json.dumps(_manifest.build_manifest(reg)), encoding="utf-8")

    assert (
        complete_cli(["--manifest", str(m), "--", "load", "--env-file="])
        == _complete._EXIT_FILES
    )
    assert capsys.readouterr().out.strip() == ".env*"

    assert (
        complete_cli(["--manifest", str(m), "--", "plain", "--any-file="])
        == _complete._EXIT_FILES
    )
    assert capsys.readouterr().out == ""
