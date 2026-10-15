"""The content channel: materialised, idempotent, override-respecting."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
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

    The profile step answers the line a replaced profile prints.
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


# The completion rebuild: a sync that changed what the environment
# installs starts footman's detached rebuild of the completion manifest.
# The refusals first: a sync that changed nothing starts nothing, a sync
# no person is there for starts nothing, the setting that turns
# background rebuilds off holds, and a rebuild that cannot start leaves
# the sync's outcome alone. Then the one rebuild. A test's stdin is no
# terminal, so a test that expects a child says a person is there.


def _rebuilds(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Stand in for footman's rebuild; the directories it was asked for."""
    import livery.footman

    asked: list[object] = []
    monkeypatch.setattr(livery.footman, "refresh_completion", asked.append)
    return asked


def _installing(monkeypatch: pytest.MonkeyPatch, root: Path, *dists: str) -> None:
    """Make the sync's uv step install *dists* into *root*'s environment."""
    from workshop_receipts import venv

    def run_uv(*args: object, **kwargs: object) -> None:
        venv(root, *dists)

    monkeypatch.setattr("livery.workshop._uv.run_uv", run_uv)


def _someone_there(monkeypatch: pytest.MonkeyPatch, there: bool = True) -> None:
    """Answer whether a person is there, where footman's refresh asks it."""
    monkeypatch.setattr("livery.footman._context.attended", lambda: there)


def _spawns(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Record each detached child footman would start; the directories.

    Recorded at footman's one spawn of a detached child, so every other
    process a sync's steps start runs as it always does.
    """
    started: list[object] = []

    def detach(
        cmd: list[str], cwd: str | None = None, env: dict[str, str] | None = None
    ) -> None:
        started.append(cwd)

    monkeypatch.setattr("livery.footman._complete.detach", detach)
    return started


def test_a_sync_that_changed_nothing_installed_starts_no_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _sync
    from workshop_receipts import synced

    root = _workspace(tmp_path)
    monkeypatch.chdir(root)
    synced(root, "something-1.0.0")
    _recorded_steps(monkeypatch)
    asked = _rebuilds(monkeypatch)
    # The receipts say the checkout is current: no local step runs.
    assert _sync.sync(frozen=True, offline=True) is False
    # A plain sync renders and installs again, and installs nothing new.
    assert _sync.sync() is True
    assert asked == []


def test_a_sync_no_person_is_there_for_starts_no_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _sync
    from workshop_receipts import synced

    root = _workspace(tmp_path)
    monkeypatch.chdir(root)
    synced(root, "something-1.0.0")
    _recorded_steps(monkeypatch)
    _installing(monkeypatch, root, "acme_extension-1.0.0")
    _someone_there(monkeypatch, there=False)
    started = _spawns(monkeypatch)
    assert _sync.sync() is True
    assert started == []


def test_max_age_off_holds_for_a_sync_that_installs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _sync
    from workshop_receipts import synced

    root = _workspace(tmp_path)
    monkeypatch.chdir(root)
    synced(root, "something-1.0.0")
    with (root / "pyproject.toml").open("a", encoding="utf-8") as project:
        project.write('\n[tool.footman]\ncompletion.max-age = "off"\n')
    _recorded_steps(monkeypatch)
    _installing(monkeypatch, root, "acme_extension-1.0.0")
    _someone_there(monkeypatch)
    started = _spawns(monkeypatch)
    assert _sync.sync() is True
    assert started == []


def test_a_rebuild_that_cannot_start_never_fails_the_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop import _sync
    from workshop_receipts import synced

    root = _workspace(tmp_path)
    monkeypatch.chdir(root)
    synced(root, "something-1.0.0")
    _recorded_steps(monkeypatch)
    _installing(monkeypatch, root, "acme_extension-1.0.0")
    _someone_there(monkeypatch)
    tried: list[object] = []

    def no_fork(cmd: list[str], **kwargs: object) -> None:
        tried.append(kwargs.get("cwd"))
        raise OSError("no fork today")

    monkeypatch.setattr(subprocess, "Popen", no_fork)
    assert _sync.sync() is True
    assert tried == [str(root)]
    assert "no fork today" not in capsys.readouterr().err


def test_a_sync_that_installs_an_extension_starts_one_rebuild_where_it_was_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import livery.footman as footman
    from livery.workshop import _sync
    from workshop_receipts import synced

    root = _workspace(tmp_path)
    monkeypatch.chdir(root)
    synced(root, "something-1.0.0")
    _recorded_steps(monkeypatch)
    _installing(monkeypatch, root, "acme_extension-1.0.0")
    _someone_there(monkeypatch)
    started = _spawns(monkeypatch)
    # Outside a run there is no typed-in directory: the workspace's root.
    assert _sync.sync() is True
    assert started == [str(root)]
    # Inside one, the directory the command was typed in, which keys the
    # manifest the next Tab there reads.
    started.clear()
    _installing(monkeypatch, root, "acme_other-1.0.0")
    typed = root / "docs"
    typed.mkdir()
    with footman.use_context(footman.Context(invoked_dir=str(typed))):
        assert _sync.sync() is True
    assert started == [str(typed)]


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


def test_the_sync_phase_runs_a_step_once_or_for_each_package_holding_its_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A workspace step runs once; a package step per package holding its extension."""
    from livery.workshop import _lifecycle
    from livery.workshop import _steps as registry
    from livery.workshop._extensions import declaration, register_declared
    from livery.workshop._packages import Package
    from workshop_extension_fakes import fake_extensions, fake_steps

    fake_extensions(
        tmp_path,
        monkeypatch,
        prof=(
            '[extension]\n\n[phases.sync.prof]\nmain = "{package}._steps:main"\n'
            'scope = "package"\nextensions = ["acme.prof"]\n'
        ),
        over='[extension]\nrequires = ["acme.prof"]\n',
        hooks='[extension]\n\n[phases.sync.hooks]\nmain = "{package}._steps:main"\n',
    )
    fake_steps(
        tmp_path,
        "prof",
        """
        def main(ctx):
            print(f"prof.main for {ctx.package.path}")
        """,
    )
    fake_steps(
        tmp_path,
        "hooks",
        """
        def main(ctx):
            print(f"hooks.main over the workspace: {ctx.package is None}")
        """,
    )

    def member(name: str, *extensions: str) -> Package:
        return Package(
            directory=tmp_path / name,
            path=f"packages/{name}",
            name=name,
            depends=(),
            extensions=extensions,
        )

    everyone = (
        member("plain"),
        member("other", "python"),
        member("listed", "acme.prof"),
        member("over", "acme.over"),
    )
    # Nothing first: with no step registered, the phase runs nothing.
    _lifecycle.sync(everyone, tmp_path)
    assert capsys.readouterr().out == ""
    # Registered as the mount registers a listed extension's steps: the
    # workspace step runs once, and the package step for the package
    # listing its extension and for the one listing an extension that
    # requires it.
    state = registry.snapshot()
    try:
        for name in ("acme.prof", "acme.hooks"):
            found = declaration(name)
            assert found is not None
            register_declared(name, found.additions)
        _lifecycle.sync(everyone, tmp_path)
    finally:
        registry.restore(state)
    # The steps' own lines, in any order: independent steps run at once.
    said = sorted(
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith(("prof.main", "hooks.main"))
    )
    assert said == [
        "hooks.main over the workspace: True",
        "prof.main for packages/listed",
        "prof.main for packages/over",
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
    from livery.workshop import _steps
    from livery.workshop._extensions import (
        declaration,
        enabled_steps,
        extension_names,
        register_declared,
    )

    # A sync runs after the mount, which registers the listed
    # extensions' steps and with them what they deliver; the registry
    # is put back after.
    state = _steps.snapshot()
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
        _steps.restore(state)
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


# An install that moves what a mounted extension ships. The process
# found the extension's directory as it mounted it, and the `.pth` file
# an editable install writes is read only when an interpreter starts, so
# after the uv step this process cannot read a moved extension where it
# is now. The fallbacks first: a move, a removal, a move between two
# editable trees that both stay, and a handoff that cannot start. Then
# the installs that leave every extension where it was, which take the
# sync's own path.

_MOVING = "relocating"
_MOVING_PACKAGE = "relocating_ext"
#: The package of a stand-in for the workshop's own distribution.
_BASE_PACKAGE = "relocating_workshop"


class _Handed(Exception):
    """A handoff that started: the process ends there, as an exec ends it."""


def _handoffs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stand in for the handoff to a fresh process; the causes it was asked for."""
    from livery.workshop import _reconcile

    causes: list[str] = []

    def reexec(root: Path, cause: str) -> None:
        del root
        causes.append(cause)
        raise _Handed(cause)

    monkeypatch.setattr(_reconcile, "_reexec", reexec)
    return causes


def _moving_package(directory: Path, label: str, name: str = _MOVING_PACKAGE) -> None:
    """Write the package *name* into *directory*: a declaration and one file."""
    package = directory / name
    (package / "content" / "root").mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "extension.toml").write_text("[extension]\napi-version = 1\n")
    (package / "content" / "root" / "relocating.txt").write_text(
        f"shipped from {label}\n"
    )


def _install(
    site: Path,
    source: Path | None = None,
    *,
    version: str = "0.1",
    name: str = _MOVING_PACKAGE,
) -> None:
    """Install the distribution of package *name* into *site*, as an installer does.

    Without *source*, a wheel: the package in *site*. With *source*, an
    editable install of the project there: the package under its
    ``src``, a ``.pth`` file naming that directory, and the origin in
    ``direct_url.json``. The extension's distribution declares it in
    the extensions' entry point group; the workshop's stand-in declares
    nothing.
    """
    info = site / f"{name}-{version}.dist-info"
    info.mkdir(parents=True)
    distribution = name.replace("_", "-")
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.4\nName: {distribution}\nVersion: {version}\n"
    )
    if name == _MOVING_PACKAGE:
        (info / "entry_points.txt").write_text(
            f"[workshop.extensions]\n{_MOVING} = {_MOVING_PACKAGE}\n"
        )
    if source is None:
        if not (site / name).is_dir():
            _moving_package(site, f"the wheel {version}", name)
        return
    if not (source / "src" / name).is_dir():
        _moving_package(source / "src", source.name, name)
    (site / f"_{name}.pth").write_text(f"{source / 'src'}\n")
    (info / "direct_url.json").write_text(
        json.dumps({"url": source.as_uri(), "dir_info": {"editable": True}})
    )


def _uninstall(site: Path, name: str = _MOVING_PACKAGE) -> None:
    """Remove what an install of *name* put in *site*; an editable tree stays."""
    shutil.rmtree(site / name, ignore_errors=True)
    for info in site.glob(f"{name}-*.dist-info"):
        shutil.rmtree(info)
    (site / f"_{name}.pth").unlink(missing_ok=True)


def _moving_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *paths: Path
) -> Path:
    """A workspace listing the extension, run by a process whose path holds *paths*.

    The process scans the installed distributions afresh and reads the
    extension where it finds it, as its mount does. Returns the root.
    """
    from livery.footman import _entries  # pyright: ignore[reportPrivateUsage]
    from livery.workshop import _extensions
    from workshop_extension_fakes import read_afresh

    for path in paths:
        monkeypatch.syspath_prepend(str(path))
    monkeypatch.setattr(_entries, "_SCAN", None)
    read_afresh(monkeypatch)
    root = tmp_path / "work"
    root.mkdir()
    (root / "workshop.toml").write_text(f'[workspace]\nextensions = ["{_MOVING}"]\n')
    monkeypatch.chdir(root)
    assert _extensions.declaration(_MOVING) is not None
    return root


def _sync_installing(
    monkeypatch: pytest.MonkeyPatch, install: Callable[[], object]
) -> list[str]:
    """Stand in for a sync's steps but two; the steps run, in order.

    The uv step runs *install*, and the delivery after it is the real
    one, which reads every extension's declaration and content.
    """
    from livery.workshop._shipped_files import deliver as delivered

    steps = _recorded_steps(monkeypatch)

    def run_uv(*args: str, root: Path) -> None:
        del args, root
        steps.append("uv")
        install()

    def deliver(root: Path, *, local_only: bool = False) -> list[str]:
        steps.append("deliver")
        return delivered(root, local_only=local_only)

    monkeypatch.setattr("livery.workshop._uv.run_uv", run_uv)
    monkeypatch.setattr("livery.workshop._shipped_files.deliver", deliver)
    return steps


def _moved_and_reinstalled(site: Path, source: Path | None) -> Callable[[], None]:
    """The install that takes the extension out of *site*, then installs *source*."""

    def install() -> None:
        _uninstall(site)
        _install(site, source)

    return install


def test_an_install_that_moves_a_mounted_extension_hands_the_sync_to_a_fresh_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop import _reconcile, _sync

    # A wheel, as an install by name from an index lays it out; the lock
    # names an editable member at another path, whose `src` joins no path
    # of this process.
    site = tmp_path / "site"
    _install(site)
    root = _moving_workspace(tmp_path, monkeypatch, site)
    steps = _sync_installing(
        monkeypatch, _moved_and_reinstalled(site, tmp_path / "member")
    )
    handed = _handoffs(monkeypatch)
    with pytest.raises(_Handed):
        _sync.sync_checkout(root)
    assert handed == [_reconcile.RELOCATED]
    assert "deliver" not in steps
    assert (
        "  the install moved or removed relocating; the sync continues in a fresh"
        " process, which mounts what is installed now" in capsys.readouterr().out
    )


def test_an_install_that_removes_a_mounted_extension_hands_the_sync_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _reconcile, _sync

    # The fresh process mounts without it, and its own mount names it.
    site = tmp_path / "site"
    _install(site)
    root = _moving_workspace(tmp_path, monkeypatch, site)
    steps = _sync_installing(monkeypatch, lambda: _uninstall(site))
    handed = _handoffs(monkeypatch)
    with pytest.raises(_Handed):
        _sync.sync_checkout(root)
    assert handed == [_reconcile.RELOCATED]
    assert "deliver" not in steps


def test_an_install_from_another_tree_hands_the_sync_over_though_the_old_one_stays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _reconcile, _sync

    # An editable install of one tree, replaced by an editable install of
    # another: the first tree is still there to read, and is no longer
    # what the environment installs.
    site = tmp_path / "site"
    first = tmp_path / "first"
    _install(site, first)
    root = _moving_workspace(tmp_path, monkeypatch, site, first / "src")
    steps = _sync_installing(
        monkeypatch, _moved_and_reinstalled(site, tmp_path / "second")
    )
    handed = _handoffs(monkeypatch)
    with pytest.raises(_Handed):
        _sync.sync_checkout(root)
    assert handed == [_reconcile.RELOCATED]
    assert "deliver" not in steps
    assert not (root / "relocating.txt").exists()


@pytest.mark.parametrize(
    "editable", [True, False], ids=["replaced-by-an-editable-tree", "removed"]
)
def test_an_install_that_moves_the_workshop_hands_the_sync_over(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    editable: bool,
) -> None:
    from livery.workshop import _extensions, _reconcile, _sync

    # The base's own files, its contract, its seeds and its fragments,
    # are read from the directory this process imported it from. Its
    # wheel is replaced by an editable tree at another path, or removed;
    # the extension stays where it was.
    site = tmp_path / "site"
    _install(site, name=_BASE_PACKAGE)
    _install(site)
    root = _moving_workspace(tmp_path, monkeypatch, site)
    monkeypatch.setattr(_extensions, "BASE_DISTRIBUTION", "relocating-workshop")
    monkeypatch.setattr(_extensions, "base_directory", lambda: site / _BASE_PACKAGE)

    def install() -> None:
        _uninstall(site, _BASE_PACKAGE)
        if editable:
            _install(site, tmp_path / "workshop", name=_BASE_PACKAGE)

    steps = _sync_installing(monkeypatch, install)
    handed = _handoffs(monkeypatch)
    with pytest.raises(_Handed):
        _sync.sync_checkout(root)
    assert handed == [_reconcile.RELOCATED]
    assert "deliver" not in steps
    assert (
        "  the install moved or removed livery.workshop; the sync continues in a"
        " fresh process, which mounts what is installed now" in capsys.readouterr().out
    )


def test_a_handoff_that_cannot_start_refuses_before_the_moved_extension_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.footman import Failed
    from livery.workshop import _reconcile, _sync

    # The command re-ran once for this cause already: an install that
    # never settles ends in a refusal, never in a third process.
    site = tmp_path / "site"
    _install(site)
    root = _moving_workspace(tmp_path, monkeypatch, site)
    steps = _sync_installing(
        monkeypatch, _moved_and_reinstalled(site, tmp_path / "member")
    )
    monkeypatch.setenv("WORKSHOP_RECONCILE_REEXEC", _reconcile.RELOCATED)
    with pytest.raises(Failed) as refused:
        _sync.sync_checkout(root)
    assert "deliver" not in steps
    assert str(refused.value).startswith(
        "the install moved or removed relocating, and this process cannot mount"
        " what is installed now: run `"
    )
    assert str(refused.value).endswith(
        " sync` again. Nothing was delivered after the install"
    )
    assert (
        "re-ran once because the install moved or removed a mounted extension"
        in capsys.readouterr().err
    )


@pytest.mark.parametrize(
    ("before", "reinstalled", "delivered"),
    [
        # Nothing installed.
        ("wheel", False, "the wheel 0.1"),
        # A newer wheel from the index, in the same directory.
        ("wheel", True, "the wheel 0.2"),
        # The member stamped to another version: its metadata moves to a
        # new dist-info directory, and its package stays where it was.
        ("member", True, "member"),
    ],
    ids=["nothing", "wheel-upgraded-in-place", "member-stamped"],
)
def test_an_install_that_leaves_every_extension_where_it_was_hands_nothing_over(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    before: str,
    reinstalled: bool,
    delivered: str,
) -> None:
    from livery.workshop import _sync

    site = tmp_path / "site"
    member = tmp_path / "member"
    source = member if before == "member" else None
    _install(site, source)
    paths = (site, member / "src") if source is not None else (site,)
    root = _moving_workspace(tmp_path, monkeypatch, *paths)

    def install() -> None:
        if reinstalled:
            _uninstall(site)
            _install(site, source, version="0.2")

    steps = _sync_installing(monkeypatch, install)
    handed = _handoffs(monkeypatch)
    assert _sync.sync_checkout(root) is True
    assert handed == []
    assert steps.index("uv") < steps.index("deliver")
    assert (root / "relocating.txt").read_text() == f"shipped from {delivered}\n"
