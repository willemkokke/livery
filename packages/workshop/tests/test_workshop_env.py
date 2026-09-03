"""The env cascade, its emissions, clean's protections, and the hooks."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from footman import Failed

from livery.workshop._clean import CleanPlan, clean_tree, plan_clean, render_plan
from livery.workshop._env_tasks import (
    EnvDelta,
    agent_delta,
    emit_lines,
    github_persist,
    tool_profile,
    workspace_delta,
)
from livery.workshop._envfile import (
    Source,
    load_cascade,
    member_keys,
    parse_env_file,
    quote_value,
    set_value,
)
from livery.workshop._hooks import HookEvent, ToolInput, post_edit, stop

_FAILURES = (SystemExit, Failed)


@pytest.fixture(autouse=True)
def _isolated_shared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the machine's own shared env file out of every test.

    The cascade defaults its shared dir to the runner's real config
    directory, which on a developer machine holds live tokens.
    """
    home = tmp_path / "shared-home"
    home.mkdir(exist_ok=True)
    monkeypatch.setattr("footman.config_dir", lambda: home)


# --- the cascade: refusals and edge rows first ---


def test_an_unsettling_substitution_keeps_its_raw_text(tmp_path: Path) -> None:
    # A=$B beside B=$A never settles; the bound stops the loop and
    # the raw text is the answer a reader can act on.
    (tmp_path / ".repo.env").write_text('A="$B"\nB="$A"\nC="plain"\n')
    stack = load_cascade(tmp_path, tmp_path, shared_dir=tmp_path, environ={})
    assert stack.values["A"] == "$B"
    assert stack.values["B"] == "$A"
    assert stack.values["C"] == "plain"


def test_quoting_rules_and_the_escaped_dollar(tmp_path: Path) -> None:
    (tmp_path / ".repo.env").write_text(
        'BASE="/opt"\n'
        'DOUBLE="$BASE/bin"\n'
        "SINGLE='$BASE/bin'\n"
        "BARE=value # trailing comment\n"
        'ESCAPED="\\$BASE"\n'
    )
    stack = load_cascade(tmp_path, tmp_path, shared_dir=tmp_path, environ={})
    assert stack.values["DOUBLE"] == "/opt/bin"
    assert stack.values["SINGLE"] == "$BASE/bin"  # single quotes are literal
    assert stack.values["BARE"] == "value"
    assert stack.values["ESCAPED"] == "$BASE"


def test_precedence_is_kind_major_and_nearest_wins(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    nested = root / "packages" / "deep"
    nested.mkdir(parents=True)
    shared = tmp_path / "home"
    shared.mkdir()
    (root / ".repo.env").write_text("KEY=repo-far\nONLY_REPO=repo\n")
    (nested / ".repo.env").write_text("KEY=repo-near\n")
    (shared / ".repo.shared.env").write_text("KEY=shared\nONLY_SHARED=s\n")
    (root / ".repo.env.local").write_text("KEY=local-far\n")
    (nested / ".repo.env.local").write_text("KEY=local-near\n")
    stack = load_cascade(root, nested, shared_dir=shared, environ={})
    assert stack.values["KEY"] == "local-near"
    assert stack.sources["KEY"] is Source.local
    assert stack.values["ONLY_SHARED"] == "s"
    assert stack.values["ONLY_REPO"] == "repo"
    # The pre-existing environment beats every file.
    stack = load_cascade(root, nested, shared_dir=shared, environ={"KEY": "from-shell"})
    assert stack.values["KEY"] == "from-shell"
    assert stack.sources["KEY"] is Source.environment
    assert stack.managed().get("KEY") is None


def test_the_shared_dir_defaults_to_the_runners_config_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A footman plugin owns no home: the shared file lives in the
    # runner's config directory, asked of footman.
    root = tmp_path / "repo"
    root.mkdir()
    home = tmp_path / "runner-config"
    home.mkdir()
    (home / ".repo.shared.env").write_text("FROM_SHARED=yes\n")
    monkeypatch.setattr("footman.config_dir", lambda: home)
    stack = load_cascade(root, root, environ={})
    assert stack.values["FROM_SHARED"] == "yes"


def test_quote_value_round_trips_and_refuses_line_breaks(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="line break"):
        quote_value("a\nb")
    path = tmp_path / ".repo.env"
    set_value(path, "SPACED", " padded ")
    set_value(path, "COMMENTED", "a#b")
    set_value(path, "PLAIN", "x")
    set_value(path, "PLAIN", "y")  # replaces in place
    parsed = parse_env_file(path)
    assert parsed["SPACED"] == " padded "
    assert parsed["COMMENTED"] == "a#b"
    assert parsed["PLAIN"] == "y"
    assert path.read_text().count("PLAIN=") == 1


def test_member_keys_enumerates_every_layer(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    shared = tmp_path / "home"
    shared.mkdir()
    (root / ".repo.env").write_text("A=1\n")
    (root / ".repo.env.local").write_text("B=2\n")
    (shared / ".repo.shared.env").write_text("C=3\n")
    assert member_keys(root, root, shared) == {"A", "B", "C"}


# --- the emissions ---


def test_emit_refuses_what_cannot_be_quoted() -> None:
    with pytest.raises(_FAILURES) as caught:
        emit_lines(EnvDelta(values={"BAD NAME": "x"}), "posix")
    assert "exportable" in str(caught.value)
    with pytest.raises(_FAILURES) as caught:
        emit_lines(EnvDelta(values={"KEY": "a\nb"}), "posix")
    assert "line break" in str(caught.value)


def test_emit_quotes_both_dialects_and_prepends_path_once() -> None:
    delta = EnvDelta(
        values={"TOKEN_X": "it's"},
        paths=("/w/.venv/bin", "/extra"),
    )
    posix = emit_lines(delta, "posix")
    assert posix[0] == "export TOKEN_X='it'\\''s'"
    assert posix[-1] == "export PATH='/w/.venv/bin':'/extra':\"$PATH\""
    pwsh = emit_lines(delta, "pwsh")
    assert pwsh[0] == "$env:TOKEN_X = 'it''s'"
    assert pwsh[-1] == "$env:PATH = '/w/.venv/bin;/extra;' + $env:PATH"
    assert sum("PATH" in line for line in posix) == 1


def test_the_workspace_delta_carries_the_cascade_and_the_venv(
    tmp_path: Path,
) -> None:
    (tmp_path / ".repo.env").write_text("SOME_FLAG=on\n")
    delta = workspace_delta(tmp_path, tmp_path)
    assert delta.values["SOME_FLAG"] == "on"
    assert delta.values["VIRTUAL_ENV"] == str(tmp_path / ".venv")
    assert delta.paths == (str(tmp_path / ".venv" / "bin"),)


def test_the_agent_delta_selects_by_membership_secrets_included(
    tmp_path: Path,
) -> None:
    (tmp_path / ".repo.env.local").write_text("GITEA_TOKEN=secret\nPATH=/evil\n")
    environ = {"GITEA_TOKEN": "live-secret", "UNRELATED": "x"}
    delta = agent_delta(tmp_path, tmp_path, environ)
    # Membership selects, the live value wins, the PATH family never
    # rides, and a variable no file defines stays the session's own.
    assert delta.values["GITEA_TOKEN"] == "live-secret"
    assert "PATH" not in delta.values
    assert "UNRELATED" not in delta.values
    assert delta.values["VIRTUAL_ENV"] == str(tmp_path / ".venv")


def test_github_persist_needs_actions_and_filters_secrets(
    tmp_path: Path,
) -> None:
    delta = EnvDelta(values={"PLAIN": "1", "API_TOKEN": "s"}, paths=("/w/.venv/bin",))
    with pytest.raises(_FAILURES) as caught:
        github_persist(delta, {})
    assert "GITHUB_ENV" in str(caught.value)
    env_file = tmp_path / "env"
    path_file = tmp_path / "path"
    written = github_persist(
        delta,
        {
            "GITHUB_ACTIONS": "true",
            "GITHUB_ENV": str(env_file),
            "GITHUB_PATH": str(path_file),
        },
    )
    assert env_file.read_text() == "PLAIN=1\n"  # the secret never lands
    assert path_file.read_text() == "/w/.venv/bin\n"
    assert "PLAIN" in written and "API_TOKEN" not in written


def test_the_tool_profile_derives_from_package_types(tmp_path: Path) -> None:
    profile = tool_profile(tmp_path)  # no packages: the base profile
    assert "uv" in profile and "ruff" in profile and "pyrefly" in profile


# --- clean: the protections before the removals ---


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "--initial-branch=main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@l"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / "tracked.txt").write_text("original\n")
    (root / ".gitignore").write_text("ignored.log\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "chore: seed"], cwd=root, check=True)
    return root


def test_a_secret_inside_an_untracked_directory_is_kept(tmp_path: Path) -> None:
    # The untracked listing collapses a directory into one entry; a
    # secret nested inside it must not be removed with its parent.
    root = _repo(tmp_path)
    nest = root / "scratch" / "deep"
    nest.mkdir(parents=True)
    (nest / "creds.env.local").write_text("TOKEN=x\n")
    (nest / "junk.txt").write_text("junk\n")
    (root / "scratch" / "other.txt").write_text("junk\n")
    plan = plan_clean(root, everything=False)
    assert plan.protected == ("scratch/deep/creds.env.local",)
    assert set(plan.untracked) == {"scratch/deep/junk.txt", "scratch/other.txt"}
    clean_tree(root, assume_yes=True)
    assert (nest / "creds.env.local").is_file()
    assert not (nest / "junk.txt").exists()


def test_clean_restores_tracked_and_mirrors_the_all_scope(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "tracked.txt").write_text("changed\n")
    (root / "stray.txt").write_text("stray\n")
    (root / "ignored.log").write_text("build output\n")
    # Without --all the gitignored file is not a candidate.
    plan = plan_clean(root, everything=False)
    assert "ignored.log" not in plan.untracked
    clean_tree(root, assume_yes=True)
    assert (root / "tracked.txt").read_text() == "original\n"
    assert not (root / "stray.txt").exists()
    assert (root / "ignored.log").is_file()
    # With --all the removal's -x mirrors the plan, so the gitignored
    # file both lists and actually goes.
    (root / "ignored.log").write_text("build output\n")
    plan = plan_clean(root, everything=True)
    assert "ignored.log" in plan.untracked
    clean_tree(root, everything=True, assume_yes=True)
    assert not (root / "ignored.log").exists()


def test_an_empty_plan_says_so_and_names_protected_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _repo(tmp_path)
    (root / ".repo.env.local").write_text("TOKEN=x\n")
    clean_tree(root, assume_yes=True)
    out = capsys.readouterr().out
    assert "Nothing to clean" in out
    assert "KEEP (secret)    .repo.env.local" in out
    assert (root / ".repo.env.local").is_file()


def test_render_plan_lists_all_three_kinds() -> None:
    plan = CleanPlan(
        modified=("a.txt",), untracked=("b.txt",), protected=("c.env.local",)
    )
    lines = render_plan(plan)
    assert lines[0].startswith("  discard changes  a.txt")
    assert lines[1].startswith("  remove           b.txt")
    assert lines[2].startswith("  KEEP (secret)    c.env.local")


# --- the hooks: the guard that cannot fire, then the ones that do ---


def _event(**kwargs: object) -> HookEvent:
    return HookEvent(**kwargs)  # type: ignore[arg-type]


def test_post_edit_is_best_effort_and_touches_only_python(
    tmp_path: Path,
) -> None:
    victim = tmp_path / "messy.py"
    victim.write_text("x=1\n")
    post_edit(_event(tool_input=ToolInput(file_path=str(victim))))
    assert victim.read_text() == "x = 1\n"  # ruff format ran
    # A missing file and a non-Python file are silent no-ops.
    post_edit(_event(tool_input=ToolInput(file_path=str(tmp_path / "gone.py"))))
    other = tmp_path / "notes.md"
    other.write_text("#Heading\n")
    post_edit(_event(tool_input=ToolInput(file_path=str(other))))
    assert other.read_text() == "#Heading\n"


def _transcript(tmp_path: Path, result_text: str) -> Path:
    import json

    transcript = tmp_path / "t.jsonl"
    lines = [
        {"message": {"content": [{"type": "tool_use", "name": "Bash", "id": "b1"}]}},
        {
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "b1",
                        "content": result_text,
                    }
                ]
            }
        },
    ]
    transcript.write_text("\n".join(json.dumps(line) for line in lines))
    return transcript


def test_stop_blocks_a_red_verdict_and_passes_everything_else(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The real shape: footman pads the verdict word, so FAIL is
    # followed by a single space.
    red = _transcript(tmp_path, "FAIL check   (0.1s)\ngate exit: 1")
    assert stop(_event(transcript_path=str(red))) == 2
    assert "stop again to proceed" in capsys.readouterr().err
    # The retry marker always passes: one nudge, never a loop.
    assert stop(_event(transcript_path=str(red), stop_hook_active=True)) == 0
    green = _transcript(tmp_path, "ok   check  (0.1s)\nall green")
    assert stop(_event(transcript_path=str(green))) == 0
    # A missing or unreadable transcript passes: the guard that
    # cannot run must not deny.
    assert stop(_event(transcript_path=str(tmp_path / "absent.jsonl"))) == 0
    mangled = tmp_path / "m.jsonl"
    mangled.write_text("not json at all\n")
    assert stop(_event(transcript_path=str(mangled))) == 0


def test_env_check_red_prints_the_breakdown_and_the_remedy(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from livery.workshop import _env_tasks

    monkeypatch.setattr(
        "livery.workshop._layers.workspace_root", lambda start=None: tmp_path
    )
    monkeypatch.setattr("livery.workshop._env_tasks.shutil.which", lambda _tool: None)
    assert _env_tasks.env_check() == 1
    out = capsys.readouterr().out
    assert "uv: MISSING" in out
    assert "PATH:" in out and "sync" in out
    # With tools resolving, the same shell reports ok.
    monkeypatch.setattr(
        "livery.workshop._env_tasks.shutil.which", lambda _tool: "/usr/bin/tool"
    )
    assert _env_tasks.env_check() == 0


def test_apply_cascade_defaults_absent_keys_and_never_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _env_tasks

    (tmp_path / ".repo.env").write_text("CASCADE_FLAG=file\nPRESET=file\n")
    monkeypatch.setattr(
        "livery.workshop._layers.workspace_root", lambda start=None: tmp_path
    )
    monkeypatch.setattr(_env_tasks, "_APPLIED", {})
    monkeypatch.setenv("PRESET", "shell")
    monkeypatch.delenv("CASCADE_FLAG", raising=False)
    from typing import cast

    import footman

    _env_tasks.apply_cascade(cast(footman.Invocation, None))
    try:
        assert os.environ["CASCADE_FLAG"] == "file"
        assert os.environ["PRESET"] == "shell"  # the environment wins
        assert _env_tasks._APPLIED == {"CASCADE_FLAG": "file"}
        # The emitter merges the applied keys back, so the hook's own
        # contribution is never misread as the shell's.
        delta = workspace_delta(tmp_path, tmp_path)
        assert delta.values["CASCADE_FLAG"] == "file"
    finally:
        os.environ.pop("CASCADE_FLAG", None)


def test_a_non_ascii_untracked_path_is_planned_and_removed(
    tmp_path: Path,
) -> None:
    # `git clean -n` would C-quote this name and a locale could hide
    # it entirely; the NUL-separated listing carries it as data.
    root = _repo(tmp_path)
    (root / "café.txt").write_text("x\n")
    plan = plan_clean(root, everything=False)
    assert "café.txt" in plan.untracked
    clean_tree(root, assume_yes=True)
    assert not (root / "café.txt").exists()


_SHIM = Path(__file__).resolve().parents[3] / ".claude" / "hooks" / "fm-hook.sh"


def test_the_shim_turns_infrastructure_failure_into_a_pass(
    tmp_path: Path,
) -> None:
    # A guard that cannot run must not deny: with neither fm nor uv
    # resolvable, the shim answers 0 and the session lives.
    result = subprocess.run(
        ["/bin/bash", str(_SHIM), "pre-bash"],
        env={"PATH": str(tmp_path)},
        input="",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0


def test_the_shim_propagates_only_the_hooks_own_refusal(tmp_path: Path) -> None:
    for code, expected in ((2, 2), (1, 0), (3, 0)):
        fake = tmp_path / "fm"
        fake.write_text(f"#!/bin/sh\nexit {code}\n")
        fake.chmod(0o755)
        result = subprocess.run(
            ["/bin/bash", str(_SHIM), "stop"],
            env={"PATH": f"{tmp_path}:/usr/bin:/bin"},
            input="",
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == expected, (code, result.returncode)


# --- the audit-close forcing tests ---


def test_pre_bash_blocks_pipes_and_only_pipes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from livery.workshop._hooks import pre_bash

    def _blocked(command: str) -> bool:
        try:
            pre_bash(_event(tool_input=ToolInput(command=command)))
        except _FAILURES as caught:
            assert "piping" in str(caught)
            return True
        return False

    assert _blocked("uv run fm check | tail -4")
    assert _blocked("fm check |& head")  # bash's pipe-with-stderr
    assert not _blocked("fm check && echo done | tail")
    assert not _blocked('rg "fm check" | head')
    assert not _blocked("ls | head")


def test_pre_bash_push_guard_blocks_conflicts_and_exempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from livery.workshop import _hooks

    probes: list[str] = []

    def _conflicts(_repo: object) -> bool:
        probes.append("probed")
        return True

    monkeypatch.setattr(_hooks, "_push_conflicts", _conflicts)
    with pytest.raises(_FAILURES) as caught:
        _hooks.pre_bash(_event(tool_input=ToolInput(command="git push origin feat/x")))
    assert "conflicts with origin/main" in str(caught.value)
    # Deletions, tags, and main itself pass without probing.
    probes.clear()
    for exempt in (
        "git push origin --delete feat/x",
        "git push --tags",
        "git push origin main",
    ):
        _hooks.pre_bash(_event(tool_input=ToolInput(command=exempt)))
    assert probes == []


def test_env_set_shadow_warnings_are_honest(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from livery.workshop import _env_tasks

    monkeypatch.setattr(
        "livery.workshop._layers.workspace_root", lambda start=None: tmp_path
    )
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".repo.env").write_text("CASCADE_KEY=from-file\n")
    # The hook exported the cascade: that is not the shell's own
    # export, so a local write warns about nothing and takes effect.
    monkeypatch.setattr(_env_tasks, "_APPLIED", {"CASCADE_KEY": "from-file"})
    monkeypatch.setenv("CASCADE_KEY", "from-file")
    _env_tasks.env_set("CASCADE_KEY", value="new", scope="local")
    out = capsys.readouterr().out
    assert "shell's own environment" not in out
    # A genuinely shell-exported key still warns.
    monkeypatch.setattr(_env_tasks, "_APPLIED", {})
    _env_tasks.env_set("CASCADE_KEY", value="newer", scope="local")
    assert "shell's own environment" in capsys.readouterr().out
    # A repo-scope write shadowed by the local file names the winner.
    monkeypatch.delenv("CASCADE_KEY")
    _env_tasks.env_set("CASCADE_KEY", value="repo-value", scope="repo")
    out = capsys.readouterr().out
    assert "also defined in" in out and ".repo.env.local" in out


def test_env_set_delete_is_confirmed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from livery.workshop import _env_tasks

    monkeypatch.setattr(
        "livery.workshop._layers.workspace_root", lambda start=None: tmp_path
    )
    monkeypatch.chdir(tmp_path)
    local = tmp_path / ".repo.env.local"
    local.write_text("DOOMED_KEY=x\n")
    monkeypatch.setattr(
        "livery.workshop._env_tasks.footman.confirm", lambda *a, **k: False
    )
    _env_tasks.env_set("DOOMED_KEY", scope="local")
    assert "Left alone" in capsys.readouterr().out
    assert "DOOMED_KEY" in local.read_text()
    monkeypatch.setattr(
        "livery.workshop._env_tasks.footman.confirm", lambda *a, **k: True
    )
    _env_tasks.env_set("DOOMED_KEY", scope="local")
    assert "DOOMED_KEY" not in local.read_text()


def test_env_show_keeps_file_provenance_and_flags_stale(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from livery.workshop import _env_tasks

    monkeypatch.setattr(
        "livery.workshop._layers.workspace_root", lambda start=None: tmp_path
    )
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".repo.env").write_text("SHOWN_KEY=file-value\nOLD_KEY=new-value\n")
    # SHOWN_KEY was exported by the hook: it must still read (repo).
    monkeypatch.setattr(_env_tasks, "_APPLIED", {"SHOWN_KEY": "file-value"})
    monkeypatch.setenv("SHOWN_KEY", "file-value")
    # OLD_KEY carries a stale shell export the hook did not write.
    monkeypatch.setenv("OLD_KEY", "old-value")
    _env_tasks.env_show(full=True)
    out = capsys.readouterr().out
    assert "SHOWN_KEY" in out and "(repo)" in out
    shown_line = next(line for line in out.splitlines() if "SHOWN_KEY" in line)
    assert "(repo)" in shown_line
    old_line = next(line for line in out.splitlines() if "OLD_KEY" in line)
    assert "(environment)" in old_line and "stale" in old_line


def test_env_check_reports_uv_drift_against_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._env_tasks import _uv_drift

    running = subprocess.run(
        ["uv", "--version"], capture_output=True, text=True, check=False
    ).stdout.split()[1]
    lock = tmp_path / "uv.lock"
    lock.write_text(f'[[package]]\nname = "uv"\nversion = "{running}"\n')
    assert _uv_drift(tmp_path) == ""
    lock.write_text('[[package]]\nname = "uv"\nversion = "0.0.1"\n')
    drift = _uv_drift(tmp_path)
    assert "DRIFT" in drift and "0.0.1" in drift
    assert _uv_drift(tmp_path / "absent") == ""  # no lock, no pin to judge


def test_emit_appends_the_dialects_own_completion_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _env_tasks

    monkeypatch.setattr(
        "livery.workshop._layers.workspace_root", lambda start=None: tmp_path
    )
    monkeypatch.chdir(tmp_path)
    posix = _env_tasks.env_emit("")
    assert "case $-" in posix  # the interactive guard
    pwsh = _env_tasks.env_emit("pwsh")
    assert "MenuComplete" in pwsh and "case $-" not in pwsh
    agent = _env_tasks.env_emit("", agent=True)
    assert "case $-" not in agent  # an env file evaluates no hooks


def test_clean_declined_confirm_leaves_everything_alone(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _repo(tmp_path)
    (root / "stray.txt").write_text("stray\n")
    monkeypatch.setattr("livery.workshop._clean.footman.confirm", lambda *a, **k: False)
    clean_tree(root)
    assert "Left alone" in capsys.readouterr().out
    assert (root / "stray.txt").is_file()


def test_clean_reports_restore_and_remove_failures(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from livery.workshop import _clean

    root = _repo(tmp_path)
    (root / "tracked.txt").write_text("changed\n")
    (root / "stray.txt").write_text("stray\n")
    real_query = _clean._query

    def _sabotaged(target: Path, *args: str) -> object:
        if args[0] in ("checkout", "clean"):
            return SimpleNamespace(code=1, stdout="", stderr="sabotaged")
        return real_query(target, *args)

    monkeypatch.setattr(_clean, "_query", _sabotaged)
    with pytest.raises(_FAILURES) as caught:
        clean_tree(root, assume_yes=True)
    assert "could not restore" in str(caught.value)

    # With only untracked files, a failing removal is a per-path
    # report, never a silent claimed removal.
    (root / "tracked.txt").write_text("original\n")
    plan_before = plan_clean(root, everything=False)
    assert plan_before.modified == ()
    clean_tree(root, assume_yes=True)
    out = capsys.readouterr().out
    assert "could not remove stray.txt" in out
    assert (root / "stray.txt").is_file()


def test_the_ci_rung_is_the_shared_slot(tmp_path: Path) -> None:
    from livery.workshop._envfile import Source, load_cascade

    root = tmp_path / "ws"
    root.mkdir()
    (root / ".repo.env").write_text("PYTHON_PUBLISH_INDEX=committed\n")
    rung = tmp_path / "runner" / "repo-shared.env"
    rung.parent.mkdir()
    rung.write_text("PYTHON_PUBLISH_INDEX=from-secret\n")
    stack = load_cascade(
        root,
        root,
        shared_dir=tmp_path / "nowhere",
        environ={"WORKSHOP_SHARED_ENV_FILE": str(rung)},
    )
    assert stack.values["PYTHON_PUBLISH_INDEX"] == "from-secret"
    assert stack.sources["PYTHON_PUBLISH_INDEX"] is Source.shared


def test_an_absent_rung_cannot_mask_the_committed_value(tmp_path: Path) -> None:
    from livery.workshop._envfile import load_cascade

    root = tmp_path / "ws"
    root.mkdir()
    (root / ".repo.env").write_text("PYTHON_PUBLISH_INDEX=committed\n")
    # The emitted step writes non-empty values only; a job with no
    # secrets points the slot at an empty file.
    rung = tmp_path / "runner" / "repo-shared.env"
    rung.parent.mkdir()
    rung.write_text("")
    stack = load_cascade(
        root,
        root,
        shared_dir=tmp_path / "nowhere",
        environ={"WORKSHOP_SHARED_ENV_FILE": str(rung)},
    )
    assert stack.values["PYTHON_PUBLISH_INDEX"] == "committed"


def test_env_set_ci_writes_through_the_protocol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.forge import RepoConfig, Unsupported
    from livery.workshop._env_tasks import env_set

    stored: dict[str, str] = {}

    class _Repo:
        def configure(self, config: RepoConfig) -> None:
            assert config.secrets is not None
            stored.update(config.secrets)

    root = tmp_path / "ws"
    root.mkdir()
    (root / "workshop.toml").write_text('[workspace]\nlayers = ["livery.workshop"]\n')
    monkeypatch.setattr("livery.workshop._env_tasks._workspace", lambda: (root, root))
    monkeypatch.setattr(
        "livery.workshop._forge_lane.admin_repository",
        lambda _root: (_Repo(), ""),
    )
    env_set("PYTHON_PUBLISH_INDEX", "https://index.example/pypi", scope="ci")
    assert stored == {"PYTHON_PUBLISH_INDEX": "https://index.example/pypi"}
    # Write-only, no delete: an empty value is a taught refusal.
    with pytest.raises(BaseException, match="write-only"):
        env_set("PYTHON_PUBLISH_INDEX", "", scope="ci")

    class _Declines:
        def configure(self, config: RepoConfig) -> None:
            raise Unsupported("no secret store (capability: ci_secrets)")

    monkeypatch.setattr(
        "livery.workshop._forge_lane.admin_repository",
        lambda _root: (_Declines(), ""),
    )
    with pytest.raises(BaseException, match="cannot store CI secrets"):
        env_set("KEY", "value", scope="ci")
