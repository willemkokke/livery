"""The content channel: materialised, idempotent, override-respecting."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

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


def _recorded_steps(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stand in for every step of a sync; the steps' names, in the order run.

    The profile check answers the line a replaced profile prints.
    """
    from livery.workshop import _sync

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
    monkeypatch.setattr("livery.workshop._tool_tasks.sync_tools", _step("tools"))

    def _prepare(packages: object, root: object) -> None:
        steps.append("prepare")
        print("  conan: the default profile was detected again")

    monkeypatch.setattr("livery.workshop._lifecycle.sync", _prepare)
    monkeypatch.setattr("livery.workshop._uv.run_uv", _step("uv"))
    monkeypatch.setattr("livery.workshop._shipped_files.deliver", _step("deliver", []))
    monkeypatch.setattr(
        "livery.workshop._templates.apply_generated", _step("generated", [])
    )
    monkeypatch.setattr("livery.workshop._reconcile.record_receipt", _step("receipt"))
    return steps


# The receipts decide what a sync does: the off cases first, then the
# current one, then the plain sync that renders regardless.


def test_a_frozen_sync_with_a_receipt_off_runs_its_steps_and_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop import _sync
    from workshop_receipts import LOCK, synced

    root = _workspace(tmp_path)
    monkeypatch.chdir(root)
    synced(root, "something-1.0.0")
    (root / "uv.lock").write_text(LOCK + "\n[[package]]\n")
    steps = _recorded_steps(monkeypatch)
    assert _sync.sync(frozen=True, offline=True) is True
    assert {"content", "tools", "uv", "deliver", "receipt"} <= set(steps)
    out = capsys.readouterr().out
    assert "uv.lock moved past the environment; syncing from what the machine" in out


def test_a_frozen_sync_whose_receipts_cannot_be_read_runs_its_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop import _reconcile, _sync
    from workshop_receipts import synced

    root = _workspace(tmp_path)
    monkeypatch.chdir(root)
    synced(root, "something-1.0.0")
    steps = _recorded_steps(monkeypatch)

    def unreadable(root: Path) -> str:
        raise OSError("locked")

    monkeypatch.setattr(_reconcile, "current", unreadable)
    assert _sync.sync(frozen=True, offline=True) is True
    assert "uv" in steps and "receipt" in steps
    assert "the receipts could not be read (locked); syncing" in capsys.readouterr().out


def test_a_frozen_sync_of_a_current_checkout_runs_no_local_step_and_says_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop import _sync
    from workshop_receipts import synced

    root = _workspace(tmp_path)
    monkeypatch.chdir(root)
    synced(root, "something-1.0.0")
    steps = _recorded_steps(monkeypatch)
    assert _sync.sync(frozen=True, offline=True) is False
    # The mount and the sweep still run: they are what a listed extension
    # and a removed package need, and cost no step.
    assert steps == ["mounted", "sweep"]
    assert capsys.readouterr().out == ""


def test_a_plain_sync_of_a_current_checkout_still_renders_and_installs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A fragment edit is in no receipt, so a plain sync renders again.
    from livery.workshop import _sync
    from workshop_receipts import synced

    root = _workspace(tmp_path)
    monkeypatch.chdir(root)
    synced(root, "something-1.0.0")
    steps = _recorded_steps(monkeypatch)
    assert _sync.sync() is True
    assert {"content", "tools", "uv", "deliver", "receipt"} <= set(steps)


def test_the_sync_phase_runs_after_the_tools_and_before_uv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop import _sync

    root = _workspace(tmp_path)
    monkeypatch.chdir(root)
    steps = _recorded_steps(monkeypatch)
    _sync.sync()
    assert steps.index("tools") < steps.index("prepare") < steps.index("uv")
    assert "the default profile was detected again" in capsys.readouterr().out


def test_the_sync_phase_runs_the_listed_steps_and_the_kinds_bridge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A listed extension's sync step runs; a kind standing for it runs it too."""
    from livery.workshop import _kinds, _lifecycle
    from livery.workshop._packages import Package
    from workshop_extension_fakes import fake_extensions, fake_steps

    fake_extensions(
        tmp_path,
        monkeypatch,
        prof='[extension]\nlevels = ["package"]\n\n[phases.sync.prof]\n'
        'main = "{package}._steps:main"\n',
    )
    fake_steps(
        tmp_path,
        "prof",
        """
        def main(ctx):
            print(f"prof.main for {ctx.package.path}")
        """,
    )

    def member(name: str, kind: str, *extensions: str) -> Package:
        return Package(
            directory=tmp_path / name,
            path=f"packages/{name}",
            name=name,
            kind=kind,
            depends=(),
            extensions=extensions,
        )

    # The bridge: a kind standing for the fake, as nanobind stands for conan.
    monkeypatch.setattr(
        _kinds,
        "kind_stands_for",
        lambda kind: ("acme.prof",) if kind == "python-nanobind" else (),
    )
    # Nothing first: a package listing nothing of a kind standing for
    # nothing, and a package of a kind nothing registers, run no step.
    _lifecycle.sync(
        (member("plain", "python"), member("odd", "acme-unknown")), tmp_path
    )
    assert capsys.readouterr().out == ""
    _lifecycle.sync(
        (member("listed", "python", "acme.prof"), member("native", "python-nanobind")),
        tmp_path,
    )
    # The step's own lines; the walk's and footman's receipts are its.
    said = [
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("prof.main")
    ]
    assert said == [
        "prof.main for packages/listed",
        "prof.main for packages/native",
    ]


def test_sync_runs_where_git_has_no_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # No repository, then a repository with nothing committed: nothing
    # to bring current, and everything else as anywhere.
    from livery.workshop import _sync

    root = _workspace(tmp_path)
    monkeypatch.chdir(root)
    steps = _recorded_steps(monkeypatch)
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
        "prepare",
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
    import livery.workshop

    if not Path(livery.workshop.__file__).resolve().is_relative_to(ROOT):
        # The release train's isolated leg runs this suite against the
        # installed wheel; syncing the monorepo from there would
        # re-point the repository's real links at the scratch venv.
        # The dogfood check means the editable checkout syncing itself.
        pytest.skip("dogfood: only the editable checkout syncs itself")
    from livery.workshop import _checks
    from livery.workshop._extensions import (
        declaration,
        enabled_steps,
        extension_names,
        register_declared,
    )

    # A sync runs after the mount, which registers the listed
    # extensions' checks and with them what they deliver; the registry
    # is put back after.
    state = _checks.snapshot()
    try:
        enabled = enabled_steps(ROOT)
        for name in extension_names(ROOT):
            found = declaration(name)
            if found is not None:
                register_declared(name, found.additions, enabled=enabled)
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


def test_the_uv_sync_step_reads_the_entry_points_again_after_the_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The install moves a stamped member to a new dist-info directory; a
    # reader after the step, the deliver first, must see the move.
    import livery.footman
    from livery.workshop import _sync, _uv

    order: list[str] = []
    monkeypatch.setattr(
        _uv, "run_uv", lambda *args, **kwargs: order.append(f"uv {' '.join(args)}")
    )
    monkeypatch.setattr(
        livery.footman, "rescan_entry_points", lambda: order.append("rescan")
    )
    _sync._uv_sync(tmp_path, ("--frozen",))  # pyright: ignore[reportPrivateUsage]
    assert order == ["uv sync --frozen", "rescan"]
