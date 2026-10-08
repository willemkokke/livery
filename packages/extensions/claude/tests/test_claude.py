"""The claude extension: what Claude Code reads, and the hooks it runs."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

import livery.footman as footman
import livery.workshop
from livery.extensions.claude import _tasks
from livery.extensions.claude._tasks import HookEvent, ToolInput, post_edit, stop
from livery.footman import Failed
from livery.workshop._prose import (
    register_fragment,
    repository_fragments,
    unregister_fragment,
)
from livery.workshop._sync import sync_workspace

_FAILURES = (SystemExit, Failed)

#: What the extension ships: its skills, its hook shim and its settings.
CONTENT = Path(_tasks.__file__).parent / "content"

#: What the workshop ships: the guidance fragments the agent reads.
WORKSHOP_CONTENT = Path(livery.workshop.__file__).parent / "content"


def _workspace(tmp_path: Path, extensions: str = '["claude"]') -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "workshop.toml").write_text(f"[workspace]\nextensions = {extensions}\n")
    return tmp_path


@pytest.fixture
def restored() -> Iterator[None]:
    """The prose and check registries, put back after the test."""
    from livery.workshop import _checks, _prose

    prose = _prose.snapshot()
    checks = _checks.snapshot()
    yield
    _prose.restore(prose)
    _checks.restore(checks)


# The refusals first: unlisted, the extension writes nothing.


def test_a_project_without_claude_writes_no_agent_file(tmp_path: Path) -> None:
    from livery.workshop._seeds import plan

    bare = _workspace(tmp_path / "bare", "[]")
    sync_workspace(bare)
    assert not (bare / ".claude").exists()
    assert not (bare / "CLAUDE.md").exists()
    assert not (bare / ".workshop" / "fragments").exists()
    assert "CLAUDE.project.md" not in plan(bare, ("project",), {})
    # Listed, the sync writes the entry file, the guidance and .claude/,
    # and a birth seeds the repository's own facts.
    root = _workspace(tmp_path / "ws")
    sync_workspace(root)
    assert (root / "CLAUDE.md").is_file()
    assert (root / ".workshop" / "fragments" / "voice.interaction.md").is_file()
    assert (root / ".claude" / "settings.json").is_file()
    assert "CLAUDE.project.md" in plan(root, ("project",), {})


def test_two_extensions_shipping_settings_refuse_naming_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.extensions.claude import _agent

    first = tmp_path / "one"
    second = tmp_path / "two"
    for content in (first, second):
        content.mkdir()
        (content / "settings.json").write_text("{}\n")
    monkeypatch.setattr(
        _agent,
        "shipped_content",
        lambda root: (("acme.one", first), ("acme.two", second)),
    )
    with pytest.raises(_FAILURES, match=r"acme\.one and acme\.two both ship it"):
        _agent.claude_files(tmp_path)


def test_claude_files_link_each_entry_but_hidden_ones_and_a_later_one_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.extensions.claude import _agent

    first = tmp_path / "one"
    second = tmp_path / "two"
    for content in (first, second):
        (content / "skills" / "plan").mkdir(parents=True)
    (first / "skills" / ".hidden").mkdir()
    (first / "skills" / "__pycache__").mkdir()
    (second / "hooks").mkdir()
    (second / "hooks" / "shim.sh").write_text("#!/bin/sh\n")
    monkeypatch.setattr(
        _agent,
        "shipped_content",
        lambda root: (("acme.one", first), ("acme.two", second)),
    )
    assert _agent.claude_files(tmp_path) == {
        "skills/plan": second / "skills" / "plan",
        "hooks/shim.sh": second / "hooks" / "shim.sh",
    }


# The hooks.


def _event(**kwargs: object) -> HookEvent:
    return HookEvent(**kwargs)  # type: ignore[arg-type]


def test_post_edit_is_best_effort_and_fixes_only_a_file_that_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import livery.workshop

    calls: list[tuple[tuple[str, ...], bool]] = []

    def fixing(paths: tuple[str, ...], *, safe: bool = True) -> None:
        calls.append((paths, safe))

    monkeypatch.setattr(livery.workshop, "fix_files", fixing, raising=False)
    # A missing file is a silent no-op.
    post_edit(_event(tool_input=ToolInput(file_path=str(tmp_path / "gone.py"))))
    assert calls == []
    # An edit in flight adds the import before the code that uses it,
    # so the fixers run in the safe mode, which removes no code.
    victim = tmp_path / "messy.py"
    victim.write_text("x=1\n")
    post_edit(_event(tool_input=ToolInput(file_path=str(victim))))
    assert calls == [((str(victim),), True)]

    # A fixer that fails never blocks the edit, and says nothing.
    def broken(paths: tuple[str, ...], *, safe: bool = True) -> None:
        raise RuntimeError(f"{paths} {safe}")

    monkeypatch.setattr(livery.workshop, "fix_files", broken, raising=False)
    post_edit(_event(tool_input=ToolInput(file_path=str(victim))))
    assert capsys.readouterr().out == ""


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


#: The hook shim as the extension ships it: the `.claude/hooks/` copy
#: is what a sync delivers, so a checkout that never synced, the release
#: train's fresh clone among them, has none.
_SHIM = CONTENT / "hooks" / "fm-hook.sh"


@pytest.mark.skipif(
    sys.platform == "win32", reason="the hook shim is a POSIX shell script"
)
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


@pytest.mark.skipif(
    sys.platform == "win32", reason="the hook shim is a POSIX shell script"
)
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
    from livery.extensions.claude._tasks import pre_bash

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
    from livery.extensions.claude import _tasks as _hooks

    probes: list[str] = []

    def _conflicts(_repo: object, _ref: str = "HEAD") -> bool:
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


def test_the_pipe_guard_recognises_the_brand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import livery.footman as footman
    from livery.extensions.claude._tasks import _runs_runner

    monkeypatch.setattr(footman, "prog", lambda: "hse")
    pattern = _runs_runner()
    assert pattern.search("hse check") is not None
    assert pattern.search("uv run hse check") is not None
    # The stock spellings stay guarded under any brand.
    assert pattern.search("fm check") is not None
    assert pattern.search("footman check") is not None
    assert pattern.search("shse check") is None


def test_the_stub_imports_the_sections_in_order_then_the_instance(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    sync_workspace(root)
    lines = (root / "CLAUDE.md").read_text().splitlines()
    imports = [line for line in lines if line.startswith("@")]
    # The voice and the standards before any rules, the gate's render
    # after them, then the verbs the composed tasks.py mounts; a
    # workspace with no package and no lock has no kinds or tools to say.
    assert imports == [
        "@.workshop/fragments/voice.interaction.md",
        "@.workshop/fragments/standards.documentation.md",
        "@.workshop/fragments/rules.workshop.md",
        "@.workshop/fragments/gate.checks.md",
        "@.workshop/fragments/verbs.extensions.md",
        "@CLAUDE.project.md",
    ]
    for line in imports[:-1]:
        assert (root / line[1:]).is_file()


def test_the_stub_header_names_the_running_brand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(footman, "prog", lambda: "hse")
    root = _workspace(tmp_path)
    sync_workspace(root)
    assert "`hse sync`" in (root / "CLAUDE.md").read_text()


def test_skills_and_hooks_are_materialised(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    sync_workspace(root)
    assert (root / ".claude" / "skills" / "create-plan" / "SKILL.md").is_file()
    assert (root / ".claude" / "hooks" / "fm-hook.sh").is_file()
    ignore = (root / ".claude" / "skills" / ".gitignore").read_text()
    assert "/create-plan\n" in ignore


def test_a_local_override_is_kept_and_named(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    sync_workspace(root)
    override = root / ".claude" / "skills" / "create-plan"
    os.unlink(override)  # replace the link with a differing real dir
    override.mkdir()
    (override / "SKILL.md").write_text("my own version\n")
    lines = sync_workspace(root)
    assert any("kept" in line and "edited here" in line for line in lines)
    assert (override / "SKILL.md").read_text() == "my own version\n"
    ignore = (root / ".claude" / "skills" / ".gitignore").read_text()
    assert "/create-plan\n" not in ignore  # the override commits normally


_SHIPPED_SETTINGS = CONTENT / "settings.json"


def test_the_shipped_settings_are_json_a_strict_reader_accepts() -> None:
    """The agent runner reads `.claude/settings.json` as JSON, not JSONC.

    A comment there costs every permission rule and hook in the file,
    and the runner says only that the file did not parse. The editor's
    own `.vscode/settings.json` is the JSON the render may comment;
    this one carries no header, which is why `comment_style` exempts
    it, and the source it is copied from must hold to that too.
    """
    import json

    json.loads(_SHIPPED_SETTINGS.read_text(encoding="utf-8"))
    assert not _SHIPPED_SETTINGS.read_text(encoding="utf-8").startswith("//")


def test_settings_json_is_a_copy_even_where_links_work(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    sync_workspace(root)
    target = root / ".claude" / "settings.json"
    assert target.is_file() and not target.is_symlink()
    assert target.read_bytes() == _SHIPPED_SETTINGS.read_bytes()
    from livery.workshop._fragment_engine import local_receipts

    assert ".claude/settings.json" in local_receipts(root)
    ignore = (root / ".claude" / ".gitignore").read_text()
    assert "/settings.json\n" in ignore
    assert sync_workspace(root) == []  # idempotent and quiet


def test_an_edited_settings_json_is_kept_and_named(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    sync_workspace(root)
    target = root / ".claude" / "settings.json"
    target.write_text('{"hooks": {}}\n')
    lines = sync_workspace(root)
    assert any("kept" in line and "edited here" in line for line in lines)
    assert target.read_text() == '{"hooks": {}}\n'
    # The override commits normally: the self-scoped ignore drops it, and
    # with nothing else of the engine's there, the ignore file goes.
    ignore = root / ".claude" / ".gitignore"
    assert not ignore.exists() or "/settings.json\n" not in ignore.read_text()


def test_a_stale_settings_copy_refreshes(tmp_path: Path) -> None:
    import hashlib

    root = _workspace(tmp_path)
    sync_workspace(root)
    target = root / ".claude" / "settings.json"
    # An older ship: the copy and its record agree with each other and
    # disagree with what the extension ships now.
    stale = b'{"hooks": {"old": true}}\n'
    target.write_bytes(stale)
    import json

    from livery.workshop._fragment_engine import LOCAL_RECEIPT

    digest = hashlib.sha256(stale).hexdigest()
    receipt = root / LOCAL_RECEIPT
    receipts = json.loads(receipt.read_text())
    receipts[".claude/settings.json"] = digest
    receipt.write_text(json.dumps(receipts))
    lines = sync_workspace(root)
    assert "  updated .claude/settings.json" in lines
    assert target.read_bytes() == _SHIPPED_SETTINGS.read_bytes()


def test_a_committed_identical_settings_copy_is_adopted(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    claude = root / ".claude"
    claude.mkdir()
    (claude / "settings.json").write_bytes(_SHIPPED_SETTINGS.read_bytes())
    lines = sync_workspace(root)
    # Equal to what ships, it is the engine's from now on: quiet, receipted.
    assert not any(".claude/settings.json" in line for line in lines)
    from livery.workshop._fragment_engine import local_receipts

    assert ".claude/settings.json" in local_receipts(root)
    assert sync_workspace(root) == []


def test_a_settings_link_is_replaced_by_a_copy(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    claude = root / ".claude"
    claude.mkdir()
    # A link would take a settings editor's write into the wheel.
    os.symlink(_SHIPPED_SETTINGS, claude / "settings.json")
    lines = sync_workspace(root)
    assert any("in place of a link" in line for line in lines)
    target = claude / "settings.json"
    assert target.is_file() and not target.is_symlink()


def test_a_fragment_a_extension_stopped_shipping_is_removed(tmp_path: Path) -> None:
    """And inside its own directory the sweep still does its job."""
    root = _workspace(tmp_path)
    sync_workspace(root)
    import hashlib
    import json

    from livery.workshop._fragment_engine import LOCAL_RECEIPT

    # A fragment an earlier sync delivered: on disk, and in the receipt.
    withdrawn = root / ".workshop" / "fragments" / "old-guidance.md"
    withdrawn.write_bytes(b"shipped once\n")
    receipt = root / LOCAL_RECEIPT
    receipts = json.loads(receipt.read_text())
    receipts[".workshop/fragments/old-guidance.md"] = hashlib.sha256(
        b"shipped once\n"
    ).hexdigest()
    receipt.write_text(json.dumps(receipts))
    lines = sync_workspace(root)
    assert not withdrawn.exists()
    assert (
        "  removed .workshop/fragments/old-guidance.md: no listed extension ships it"
        in lines
    )
    # A file the engine never delivered there is someone's own, and stays.
    stray = root / ".workshop" / "fragments" / "mine.md"
    stray.write_text("my note\n")
    sync_workspace(root)
    assert stray.exists()


def test_a_shipped_fragment_lands_byte_for_byte_and_an_edit_is_kept_and_named(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    sync_workspace(root)
    delivered = root / ".workshop" / "fragments" / "voice.interaction.md"
    source = WORKSHOP_CONTENT / "fragments" / "voice.interaction.md"
    assert delivered.read_bytes() == source.read_bytes()
    assert sync_workspace(root) == []
    delivered.write_text("# My voice\n")
    lines = sync_workspace(root)
    assert any(
        "kept .workshop/fragments/voice.interaction.md: edited here" in line
        for line in lines
    )
    assert delivered.read_text() == "# My voice\n"
    # Deleting the override takes the shipped copy again.
    delivered.unlink()
    lines = sync_workspace(root)
    assert "  wrote .workshop/fragments/voice.interaction.md" in lines
    assert delivered.read_bytes() == source.read_bytes()


def test_a_rendered_fragment_lands_under_its_header_and_leaves_with_its_record(
    tmp_path: Path, restored
) -> None:
    root = _workspace(tmp_path)
    register_fragment(
        "rules",
        "acme",
        lambda root, audience: f"# Acme\n\nfor {audience}\n",
        extension="acme.brand",
    )
    register_fragment(
        "rules", "quiet", lambda root, audience: "", extension="acme.brand"
    )
    sync_workspace(root)
    delivered = root / ".workshop" / "fragments" / "rules.acme.md"
    text = delivered.read_text()
    assert text.startswith(
        "<!-- Rendered by `fm sync` from the registries acme.brand fills;"
    )
    assert text.endswith("# Acme\n\nfor agent\n")
    # An empty render is left out, not delivered empty.
    assert not (root / ".workshop" / "fragments" / "rules.quiet.md").exists()
    assert sync_workspace(root) == []
    unregister_fragment("rules.acme.md", by="acme.brand")
    unregister_fragment("rules.quiet.md", by="acme.brand")
    lines = sync_workspace(root)
    assert (
        "  removed .workshop/fragments/rules.acme.md: no listed extension ships it"
        in lines
    )
    assert not delivered.exists()
    from livery.workshop._fragment_engine import local_receipts

    assert ".workshop/fragments/rules.acme.md" not in local_receipts(root)


def test_the_entry_file_imports_the_sections_in_order_then_the_repository_s_own(
    tmp_path: Path, restored
) -> None:
    root = _workspace(tmp_path)
    own = root / "fragments"
    own.mkdir()
    (own / "identity.acme.md").write_text("# Acme\n")
    register_fragment(
        "workflow", "acme", lambda root, audience: "# W\n", extension="acme.brand"
    )
    sync_workspace(root)
    imports = [
        line
        for line in (root / "CLAUDE.md").read_text().splitlines()
        if line.startswith("@")
    ]
    assert imports == [
        "@.workshop/fragments/voice.interaction.md",
        "@.workshop/fragments/standards.documentation.md",
        "@.workshop/fragments/rules.workshop.md",
        "@.workshop/fragments/workflow.acme.md",
        "@.workshop/fragments/gate.checks.md",
        "@.workshop/fragments/verbs.extensions.md",
        "@fragments/identity.acme.md",
        "@CLAUDE.project.md",
    ]
    # The repository's own fragment is read where it is, never copied.
    assert not (root / ".workshop" / "fragments" / "identity.acme.md").exists()
    assert [p.name for p in repository_fragments(root)] == ["identity.acme.md"]


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _commit(clone: Path, name: str, text: str) -> None:
    (clone / name).write_text(text)
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", f"{name}: {text.strip()}")


def test_the_push_guard_asks_git_itself(tmp_path: Path) -> None:
    from livery.extensions.claude._tasks import _push_conflicts

    # The fallback first: a repository with no origin/main is not the
    # guard's business, and passes.
    lonely = tmp_path / "lonely"
    lonely.mkdir()
    _git(lonely, "init", "-q", "-b", "main")
    _git(lonely, "config", "user.email", "dev@acme.test")
    _git(lonely, "config", "user.name", "Acme")
    _commit(lonely, "f", "x\n")
    assert _push_conflicts(str(lonely)) is False
    # A branch that merges cleanly passes, and one that conflicts is caught.
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "--bare", "-q", "-b", "main")
    _git(tmp_path, "clone", "-q", str(origin), "clone")
    clone = tmp_path / "clone"
    _git(clone, "config", "user.email", "dev@acme.test")
    _git(clone, "config", "user.name", "Acme")
    _commit(clone, "f", "base\n")
    _git(clone, "push", "-q", "origin", "main")
    _git(clone, "checkout", "-q", "-b", "clean")
    _commit(clone, "g", "new\n")
    assert _push_conflicts(str(clone), "clean") is False
    _git(clone, "checkout", "-q", "main")
    _commit(clone, "f", "main's\n")
    _git(clone, "push", "-q", "origin", "main")
    _git(clone, "checkout", "-q", "-b", "feature", "HEAD~1")
    _commit(clone, "f", "feature's\n")
    assert _push_conflicts(str(clone), "feature") is True


def test_a_push_the_guard_finds_no_conflict_in_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_tasks, "_push_conflicts", lambda repo, ref="HEAD": False)
    _tasks.pre_bash(_event(tool_input=ToolInput(command="git push origin feat/x")))


def test_the_transcript_reader_takes_each_shape_the_harness_writes(
    tmp_path: Path,
) -> None:
    import json

    from livery.extensions.claude._tasks import _last_bash_result

    # The fallback first: a path it cannot read answers nothing.
    assert _last_bash_result(tmp_path) == ""
    lines = [
        {"message": {"content": "a plain string"}},
        {"message": {"content": ["not a block", {"type": "text", "text": "prose"}]}},
        {"message": {"content": [{"type": "tool_use", "name": "Bash", "id": "b1"}]}},
        {
            "message": {
                "content": [{"type": "tool_result", "tool_use_id": "b1", "content": 7}]
            }
        },
        {
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "b1",
                        "content": [
                            {"type": "text", "text": "FAIL check"},
                            "skipped",
                            {"text": "(0.1s)"},
                        ],
                    }
                ]
            }
        },
    ]
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("\n".join(json.dumps(line) for line in lines))
    assert _last_bash_result(transcript) == "FAIL check (0.1s)"
