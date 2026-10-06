"""The content channel: materialised, idempotent, override-respecting."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import livery.footman.api as footman
from livery.workshop._sync import sync_workspace
from workshop_links import link_entries as materialise

ROOT = Path(__file__).resolve().parents[3]


def _workspace(tmp_path: Path) -> Path:
    (tmp_path / "workshop.toml").write_text("[workspace]\nextensions = []\n")
    return tmp_path


def test_sync_is_idempotent(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    first = sync_workspace(root)
    assert first  # the first run has things to say
    assert sync_workspace(root) == []  # the second has nothing


def test_sync_runs_where_git_has_no_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # No repository, then a repository with nothing committed: nothing
    # to bring current, and everything else as anywhere.
    from livery.workshop import _sync

    root = _workspace(tmp_path)
    monkeypatch.chdir(root)
    steps: list[str] = []

    def _step(name: str, result: object = None) -> object:
        def record(*args: object, **kwargs: object) -> object:
            steps.append(name)
            return result

        return record

    def _never(*args: object, **kwargs: object) -> None:
        raise AssertionError("nothing to bring current here")

    monkeypatch.setattr(_sync, "bring_current", _never)
    monkeypatch.setattr(_sync, "require_mounted", _step("mounted"))
    monkeypatch.setattr(_sync, "sweep_residue", _step("sweep", []))
    monkeypatch.setattr(_sync, "fetch_store_lines", _step("store", []))
    monkeypatch.setattr(_sync, "sync_workspace", _step("content", []))
    monkeypatch.setattr(_sync, "conan_editables", _step("conan", []))
    monkeypatch.setattr("livery.workshop._tool_tasks.sync_tools", _step("tools"))
    monkeypatch.setattr("livery.workshop._uv.run_uv", _step("uv"))
    monkeypatch.setattr("livery.workshop._shipped_files.deliver", _step("deliver", []))
    monkeypatch.setattr(
        "livery.workshop._templates.apply_generated", _step("generated", [])
    )
    monkeypatch.setattr("livery.workshop._reconcile.record_receipt", _step("receipt"))
    _sync.sync()
    assert "no git history here: nothing to bring current" in capsys.readouterr().out
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    _sync.sync()
    assert "no git history here: nothing to bring current" in capsys.readouterr().out
    # Every listed extension is mounted before anything renders or locks.
    assert steps == 2 * [
        "mounted",
        "sweep",
        "store",
        "content",
        "tools",
        "conan",
        "uv",
        "deliver",
        "generated",
        "receipt",
    ]


def test_a_moved_checkout_hands_the_sync_to_a_fresh_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The rest of the sync runs on the code now on disk, or says why it cannot.

    The modules loaded before the move are the old code.
    """
    from livery.workshop import _reconcile, _sync

    # Nothing moved: no handoff, no note.
    real_reexec = _reconcile._reexec
    ran: list[tuple[object, str]] = []

    def handed(root: Path, cause: str) -> None:
        ran.append((root, cause))

    monkeypatch.setattr(_reconcile, "_reexec", handed)
    assert _sync.continue_on_moved_code(tmp_path, "a" * 40, "a" * 40) is False
    assert ran == [] and capsys.readouterr().out == ""
    # A re-run that cannot start is the reconcile's own note, and the
    # sync carries on in this process.
    monkeypatch.setenv("WORKSHOP_RECONCILE_REEXEC", _reconcile.MOVED)
    monkeypatch.setattr(_reconcile, "_reexec", real_reexec)
    assert _sync.continue_on_moved_code(tmp_path, "a" * 40, "b" * 40) is True
    captured = capsys.readouterr()
    assert "the checkout moved from aaaaaaaaaaaa to bbbbbbbbbbbb" in captured.out
    assert "re-ran once because the checkout moved" in captured.err
    # A move hands over exactly once, to the re-run on the new code.
    monkeypatch.delenv("WORKSHOP_RECONCILE_REEXEC")
    monkeypatch.setattr(_reconcile, "_reexec", handed)
    assert _sync.continue_on_moved_code(tmp_path, "a" * 40, "b" * 40) is True
    assert ran == [(tmp_path, _reconcile.MOVED)]


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


def test_an_identical_committed_copy_is_reclaimed(tmp_path: Path) -> None:
    source = tmp_path / "content" / "skills"
    (source / "thing").mkdir(parents=True)
    (source / "thing" / "SKILL.md").write_text("shipped\n")
    repo = tmp_path / "repo"
    committed = repo / ".claude" / "skills" / "thing"
    committed.mkdir(parents=True)
    (committed / "SKILL.md").write_text("shipped\n")
    lines = materialise(repo, source, "skills")
    assert any("reclaimed" in line for line in lines)
    assert (repo / ".claude" / "skills" / "thing" / "SKILL.md").is_file()


def test_no_longer_shipped_entries_are_pruned(tmp_path: Path) -> None:
    source = tmp_path / "content" / "skills"
    (source / "old").mkdir(parents=True)
    (source / "old" / "SKILL.md").write_text("v1\n")
    repo = tmp_path / "repo"
    materialise(repo, source, "skills")
    (source / "old" / "SKILL.md").unlink()
    (source / "old").rmdir()
    (source / "new").mkdir()
    (source / "new" / "SKILL.md").write_text("v2\n")
    lines = materialise(repo, source, "skills")
    assert any("no listed extension ships it" in line for line in lines)
    assert not (repo / ".claude" / "skills" / "old").exists()
    assert (repo / ".claude" / "skills" / "new" / "SKILL.md").is_file()


def _tracked_state() -> str:
    return subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_the_monorepo_is_in_sync() -> None:
    # The dogfood check. A fresh checkout has no .workshop/ and no
    # materialised links (both gitignored), so the first sync may
    # speak; after it, a second run says nothing and no tracked file
    # changed, so the committed state and the shipped content agree.
    import livery.workshop.api

    if not Path(livery.workshop.api.__file__).resolve().is_relative_to(ROOT):
        # The release train's isolated leg runs this suite against the
        # installed wheel; syncing the monorepo from there would
        # re-point the repository's real links at the scratch venv.
        # The dogfood check means the editable checkout syncing itself.
        pytest.skip("dogfood: only the editable checkout syncs itself")
    from livery.workshop import _checks
    from livery.workshop._extensions import (
        declaration,
        extension_names,
        extension_options,
        register_declared_checks,
    )

    # A sync runs after the mount, which registers the listed
    # extensions' checks and with them what they deliver; the registry
    # is put back after.
    state = _checks.snapshot()
    try:
        options = extension_options(ROOT)
        for name in extension_names(ROOT):
            module = declaration(name)
            if module is not None:
                register_declared_checks(name, module, options.get(name, ()))
        before = _tracked_state()
        first = sync_workspace(ROOT)
        assert sync_workspace(ROOT) == []
        after = _tracked_state()
    finally:
        _checks.restore(state)
    # A failure names what moved, so a runner-only difference is readable
    # from the log alone.
    moved = subprocess.run(
        ["git", "diff", "--", "pyproject.toml", ".workshop-rendered", ".gitignore"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert after == before, f"{first}\n{moved}"


_SHIPPED_SETTINGS = ROOT / "packages/workshop/src/livery/workshop/content/settings.json"


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


def test_the_sweep_reaches_no_further_than_the_fragments(tmp_path: Path) -> None:
    """The refusal first: the sweep walks its own directory and no other.

    `.workshop/` holds this checkout's state beside the extensions'
    fragments. While the two shared a top level the sweep could not
    tell them apart, and deleted the stub receipt that the same sync
    reads a few steps later, so the fast path it exists for had never
    once fired.
    """
    root = _workspace(tmp_path)
    sync_workspace(root)
    workshop = root / ".workshop"
    receipt = workshop / "state" / "stubs.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text('{"schema": 1}\n')
    stranger = workshop / "left-by-an-older-layout.md"
    stranger.write_text("not the sweep's to remove\n")
    lines = sync_workspace(root)
    assert receipt.is_file(), "the sweep reached into state/"
    assert stranger.is_file(), "the sweep reached the top level"
    assert lines == []


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


def test_the_workshop_directory_holds_only_directories(tmp_path: Path) -> None:
    """The convention that makes the sweep safe, pinned.

    Each kind of thing under `.workshop/` keeps its own directory, so
    no walk of one kind can reach another. A state file added at the
    top level would sit beside nothing that owns it, which is how the
    stub receipt came to be deleted on every sync.
    """
    root = _workspace(tmp_path)
    sync_workspace(root)
    loose = [p.name for p in (root / ".workshop").iterdir() if p.is_file()]
    assert loose == [], f"put these under a directory of their own: {loose}"


def test_explain_tells_a_fragment_from_this_checkout_s_own_state(
    tmp_path: Path,
) -> None:
    """A path under `.workshop/` is no longer an extension fragment by default."""
    from livery.workshop._provenance import _materialised

    fragment = _materialised(tmp_path, Path(".workshop/fragments/voice.md"))
    assert fragment is not None and fragment.channel == "extension fragment"
    state = _materialised(tmp_path, Path(".workshop/state/stubs.json"))
    assert state is not None and state.channel == "checkout state"
    receipts = _materialised(tmp_path, Path(".workshop/receipts/conan.json"))
    assert receipts is not None and receipts.channel == "checkout state"
