"""A running program starts the completion rebuild: the refusals first.

`refresh_completion` starts the detached child a stale manifest starts
behind a TAB press. It starts nothing when no person is there, nothing
where the configuration turns the background rebuild off or cannot be
read, swallows a child that cannot start, and then, on the happy path,
starts that one child in the directory it was given. Every test past
the first two says whether a person is there, since a test's stdin is
no terminal.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import livery.footman as footman
from livery.footman import _complete, _context, _manifest, _notes, _paths, _refresh
from livery.footman._complete import complete_cli
from livery.footman._executor import run_chain
from livery.footman._registry import Group
from livery.footman._split import split_chain


def _started(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    """Stand in for the spawn; the directory each rebuild would stand in."""
    started: list[str | None] = []

    def spawn(
        override: str | None = None,
        spawn_in: str | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        assert override is None  # the cwd cascade, never a `-f` file's
        assert env is not None  # handed over whole, never filled in by a guard
        started.append(spawn_in)

    monkeypatch.setattr(_complete, "_spawn_refresh", spawn)
    return started


def _someone_there(monkeypatch: pytest.MonkeyPatch, there: bool = True) -> None:
    """Answer whether a person is there, where the refresh asks it."""
    monkeypatch.setattr(_context, "attended", lambda: there)


def _project(where: Path, settings: str = "") -> Path:
    """A project directory at *where*, its own repository, with *settings*."""
    where.mkdir(parents=True, exist_ok=True)
    (where / ".git").mkdir()  # the cascade's ceiling: nothing above it reads
    (where / "pyproject.toml").write_text(
        f'[project]\nname = "p"\n\n[tool.footman]\n{settings}', encoding="utf-8"
    )
    (where / "tasks.py").write_text(
        "import livery.footman as footman\n\n\n@footman.task\ndef installed(): ...\n",
        encoding="utf-8",
    )
    return where


def test_no_person_there_starts_no_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # max-age is on (the default): the person's absence alone decides.
    started = _started(monkeypatch)
    _someone_there(monkeypatch, there=False)
    footman.refresh_completion(_project(tmp_path))
    assert started == []


def test_no_input_and_dry_run_mean_no_person_at_a_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The refresh asks footman's one answer, not the terminal: a run under
    # --no-input or --dry-run starts nothing even with a terminal attached.
    project = _project(tmp_path)
    started = _started(monkeypatch)
    monkeypatch.setattr(_context, "_stdin_is_tty", lambda: True)
    for declared in (_context.Context(no_input=True), _context.Context(dry_run=True)):
        with _context.use_context(declared):
            footman.refresh_completion(project)
    assert started == []
    with _context.use_context(_context.Context()):
        footman.refresh_completion(project)
    assert started == [str(project)]


def test_max_age_off_starts_no_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _someone_there(monkeypatch)
    started = _started(monkeypatch)
    footman.refresh_completion(_project(tmp_path, 'completion.max-age = "off"\n'))
    footman.refresh_completion(_project(tmp_path / "zero", "completion.max-age = 0\n"))
    assert started == []


def test_a_nearer_off_wins_over_the_user_level_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _someone_there(monkeypatch)
    # The configuration that applies in the directory decides, the way a
    # run there reads it: the user-level file first, the project over it.
    user = tmp_path / "user.toml"
    user.write_text('[tool.footman]\ncompletion.max-age = "1h"\n', encoding="utf-8")
    monkeypatch.setenv("FOOTMAN_CONFIG", str(user))
    started = _started(monkeypatch)
    footman.refresh_completion(
        _project(tmp_path / "proj", 'completion.max-age = "off"\n')
    )
    assert started == []
    # Where the project says nothing, the user-level setting stands.
    plain = _project(tmp_path / "plain")
    footman.refresh_completion(plain)
    assert started == [str(plain)]


def test_a_configuration_that_cannot_be_read_starts_nothing_and_says_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _someone_there(monkeypatch)
    started = _started(monkeypatch)
    monkeypatch.setenv("FOOTMAN_CASCADE", "sideways")  # no such cascade mode
    footman.refresh_completion(_project(tmp_path))
    assert started == []
    assert capsys.readouterr() == ("", "")


def test_a_spawn_that_fails_is_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _someone_there(monkeypatch)
    project = _project(tmp_path)

    def no_fork(*args: object, **kwargs: object) -> None:
        raise OSError("no fork today")

    monkeypatch.setattr(subprocess, "Popen", no_fork)
    footman.refresh_completion(project)  # must not raise

    def broken(
        override: str | None = None,
        spawn_in: str | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        raise RuntimeError("anything else the spawn path raises")

    monkeypatch.setattr(_complete, "_spawn_refresh", broken)
    footman.refresh_completion(project)  # must not raise either
    assert capsys.readouterr() == ("", "")


def test_a_task_that_starts_the_rebuild_earns_no_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The spawn is footman's own work on the task's behalf: it passes the
    # directory and the environment itself, so the Popen guard fills in
    # nothing and a workspace that turns notes into errors still passes.
    _someone_there(monkeypatch)
    project = _project(tmp_path)
    spawned: list[dict[str, object]] = []

    def recorded(self: subprocess.Popen[bytes], args: object, **kwargs: object) -> None:
        # Raising here leaves the class's own `_child_created = False`
        # standing, which is what Popen's `__del__` reads.
        spawned.append(kwargs)
        raise OSError("recorded, not started")

    monkeypatch.setattr(subprocess.Popen, "__init__", recorded)
    monkeypatch.setenv("REFRESH_PROOF_VARIABLE", "seen")
    _notes.reset()
    _notes.install_policy({"*": "error"})
    try:
        group = Group("root")

        @group.task
        def install() -> None:
            footman.refresh_completion(project)

        tree = _manifest.build_manifest(group)["tree"]
        _, segments = split_chain(tree, ["install"])
        [result] = run_chain(group, segments)
    finally:
        _notes.install_policy(None)
        _notes.reset()
    assert result.ok
    assert [note.kind for note in result.notes] == []
    [given] = spawned
    assert given["cwd"] == str(project)
    env = given["env"]
    assert isinstance(env, dict)
    assert env.get("REFRESH_PROOF_VARIABLE") == "seen"


def test_the_rebuild_is_the_child_a_stale_manifest_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _someone_there(monkeypatch)
    project = _project(tmp_path)
    captured: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(
        subprocess, "Popen", lambda cmd, **kwargs: captured.append((cmd, kwargs))
    )
    footman.refresh_completion(project)
    [(cmd, kwargs)] = captured
    assert "_refresh.refresh_cwd(*sys.argv[1:])" in " ".join(cmd)
    assert cmd[1] == "-P"  # the child's own imports stay honest, as TAB's do
    assert kwargs["cwd"] == str(project)
    assert kwargs["stdout"] is subprocess.DEVNULL


def test_the_next_tab_offers_what_the_rebuild_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _someone_there(monkeypatch)
    # The child runs inline here, standing where the spawn would stand it,
    # so the press after it is deterministic.
    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "cache"))
    project = _project(tmp_path / "proj")
    built: list[str | None] = []

    def inline(
        override: str | None = None,
        spawn_in: str | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        built.append(spawn_in)
        assert spawn_in is not None
        monkeypatch.chdir(spawn_in)
        _refresh.refresh_cwd(*_paths.child_args())

    monkeypatch.setattr(_complete, "_spawn_refresh", inline)
    footman.refresh_completion(project)
    assert built == [str(project)]
    monkeypatch.chdir(project)
    assert _paths.cwd_manifest_path().is_file()
    capsys.readouterr()
    assert complete_cli(["--", "inst"]) == 0
    assert "installed" in capsys.readouterr().out
