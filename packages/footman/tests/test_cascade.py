"""The monorepo cascade: discovery, merge, defining-dir cwd, config, caching."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from livery.footman import (
    _app,
    _config,
    _discover,
    _executor,
    _paths,
    _refresh,
    registry,
)
from livery.footman._split import Segment
from livery.footman.context import Context

# --- path primitives ---------------------------------------------------------


def test_find_repo_root_stops_at_git(tmp_path):
    (tmp_path / ".git").mkdir()
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    assert _paths.find_repo_root(deep) == tmp_path


def test_find_repo_root_without_git_falls_back(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    deep = tmp_path / "a"
    deep.mkdir()
    assert _paths.find_repo_root(deep) == tmp_path  # via find_project_root


@pytest.mark.parametrize("marker", [".git", ".jj", ".hg", ".svn"])
def test_find_repo_root_stops_at_any_vcs(tmp_path, marker):
    # Every version-control boundary is the same boundary. jj's
    # non-colocated mode has no `.git` at all, so before this a jj checkout
    # fell through to the packaging fallback and took its ceiling from the
    # nearest pyproject.toml — in a monorepo, the wrong directory.
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    repo = tmp_path / "repo"
    (repo / marker).mkdir(parents=True)
    deep = repo / "a" / "b"
    deep.mkdir(parents=True)
    assert _paths.find_repo_root(deep) == repo


def test_no_vcs_marker_is_a_project_marker(tmp_path):
    # `find_project_root` only ever runs as `find_repo_root`'s fallback, by
    # which point every ancestor has been searched for every VCS marker — so
    # one listed in PROJECT_MARKERS too would be unreachable code.
    assert not set(_paths.PROJECT_MARKERS) & set(_paths.REPO_MARKERS)
    # The marker goes in a *parent*: `find_project_root` returns `start` when
    # nothing matched, so asking at the marker's own directory cannot tell
    # "matched here" from "matched nothing".
    repo = tmp_path / "vcs-only"
    (repo / ".git").mkdir(parents=True)
    deep = repo / "a" / "b"
    deep.mkdir(parents=True)
    assert _paths.find_project_root(deep) == deep  # not `repo`


def test_footman_toml_marks_a_project_root(tmp_path):
    # F43: a footman.toml-only root (e.g. a Docker context with .git ignored) is
    # a project root, discoverable from a subdirectory.
    (tmp_path / "footman.toml").write_text("sequential = true\n")
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    assert _paths.find_project_root(deep) == tmp_path


def test_dir_chain_is_root_first(tmp_path):
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    assert _paths.dir_chain(deep, tmp_path) == [tmp_path, tmp_path / "a", deep]


def test_dir_chain_unrelated_ceiling_is_just_cwd(tmp_path):
    other = tmp_path / "sibling"
    other.mkdir()
    cwd = tmp_path / "here"
    cwd.mkdir()
    assert _paths.dir_chain(cwd, other) == [cwd]


def test_task_files_collects_existing_only(tmp_path):
    (tmp_path / "tasks.py").write_text("")
    (tmp_path / "a").mkdir()
    deep = tmp_path / "a" / "b"
    deep.mkdir()
    (deep / "tasks.py").write_text("")  # 'a' has none
    files = _paths.task_files(deep, tmp_path)
    assert files == [tmp_path / "tasks.py", deep / "tasks.py"]


def test_task_files_are_case_exact(tmp_path):
    # On a case-insensitive filesystem `(d / "tasks.py").is_file()` answers
    # True for a file named `Tasks.py`; accepting it means a project that
    # stops working the day it reaches a Linux box. The walk must match the
    # on-disk spelling exactly — on every platform.
    # Two directories, not one file renamed: on a case-insensitive
    # filesystem writing `tasks.py` beside `Tasks.py` opens the SAME file
    # under its first name, and the test would be testing nothing.
    wrong = tmp_path / "wrong"
    right = tmp_path / "right"
    wrong.mkdir()
    right.mkdir()
    (wrong / "Tasks.py").write_text("")
    assert _paths.task_files(wrong, wrong) == []
    (right / "tasks.py").write_text("")
    assert _paths.task_files(right, right) == [right / "tasks.py"]


def test_the_wrong_case_sighting_is_what_the_filesystem_reports(tmp_path):
    """`_named` records a sighting only where the probe hits.

    Both halves, driven directly, so the contract is pinned wherever the
    suite happens to run — a case-folding filesystem answers `exists()`
    for the wrong spelling, which is what makes the confirming listing
    (and therefore the free sighting) happen at all; a case-sensitive one
    answers no, the walk never lists, and there is nothing to report. That
    asymmetry is the design, not a platform accident: it is why the
    not-found message teaches the case rule in words.
    """
    wrong = tmp_path / "wrong"
    wrong.mkdir()
    (wrong / "Tasks.py").write_text("")

    seen: list[tuple[Path, str]] = []
    assert _paths._named(wrong, "tasks.py", seen) is False  # never loaded, either way
    if seen:  # the filesystem folded case: the probe hit, so we know the name
        assert seen == [(wrong, "Tasks.py")]
    else:  # it did not: no listing happened, so there is nothing to have seen
        assert not (wrong / "tasks.py").exists()

    # A right-cased file is never a sighting, on any filesystem.
    right = tmp_path / "right"
    right.mkdir()
    (right / "tasks.py").write_text("")
    seen.clear()
    assert _paths._named(right, "tasks.py", seen) is True
    assert seen == []


def test_project_markers_are_case_exact(tmp_path):
    # Separate trees for the two spellings — see the note above about
    # case-insensitive filesystems collapsing them into one file.
    wrong = tmp_path / "wrong" / "inner"
    right = tmp_path / "right" / "inner"
    wrong.mkdir(parents=True)
    right.mkdir(parents=True)
    (wrong.parent / "PyProject.toml").write_text("")  # not a marker, anywhere
    assert _paths.find_project_root(wrong) == wrong.resolve()
    (right.parent / "pyproject.toml").write_text("")
    assert _paths.find_project_root(right) == right.parent.resolve()


def test_a_directory_without_markers_is_never_listed(tmp_path, monkeypatch):
    """The walk must not scale with how much an ancestor happens to hold.

    Listing came first for years — one `listdir` beats one `stat` per
    marker while a directory is small, and inverts hard when it is not:
    O(entries) against O(markers). A macOS `$TMPDIR` of 8,747 entries made
    a bare-directory TAB take 1.5 s, ~0.3 s per level of the walk. Probing
    first fixed it; this keeps it fixed, by mechanism rather than by
    clock, so it cannot regress on a machine whose temp directory happens
    to be tidy.
    """
    listed: list[Path] = []
    real = _paths._entries
    monkeypatch.setattr(
        _paths,
        "_entries",
        lambda d: listed.append(d) or real(d),  # type: ignore[func-returns-value]
    )

    empty = tmp_path / "nothing" / "here"
    empty.mkdir(parents=True)
    _paths.find_repo_root(empty)
    _paths.task_files(empty, empty)
    assert listed == []  # nothing to find, so nothing was enumerated

    # A hit still pays one listing — that is what proves the spelling.
    (empty / "tasks.py").write_text("")
    assert _paths.task_files(empty, empty) == [empty / "tasks.py"]
    assert listed == [empty]


def test_manifest_path_is_per_directory(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    assert _paths.manifest_path(a) != _paths.manifest_path(b)


# --- merge semantics ---------------------------------------------------------


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def test_cascade_appends_new_names(tmp_path):
    root = _write(
        tmp_path / "tasks.py", "from livery.footman import task\n@task\ndef a():...\n"
    )
    sub = _write(
        tmp_path / "svc" / "tasks.py",
        "from livery.footman import task\n@task\ndef b():...\n",
    )
    merged = _discover.load_tree([root, sub])
    assert set(merged.tasks) == {"a", "b"}


def test_cascade_local_overrides_by_name(tmp_path):
    root = _write(
        tmp_path / "tasks.py",
        "from livery.footman import task\n@task\ndef build():\n    return 1\n",
    )
    sub = _write(
        tmp_path / "svc" / "tasks.py",
        "from livery.footman import task\n@task\ndef build():\n    return 0\n",
    )
    merged = _discover.load_tree([root, sub])
    # the local (svc) build wins, and is tagged with the svc directory
    assert _discover.defining_dir(merged.tasks["build"]) == str(tmp_path / "svc")
    assert merged.tasks["build"]() == 0


def test_cascade_merges_groups(tmp_path):
    root = _write(
        tmp_path / "tasks.py",
        "from livery.footman import group\nd = group('dist')\n@d.task\ndef build():...\n",
    )
    sub = _write(
        tmp_path / "svc" / "tasks.py",
        "from livery.footman import group\nd = group('dist')\n@d.task\ndef deploy():...\n",
    )
    merged = _discover.load_tree([root, sub])
    assert set(merged.groups["dist"].tasks) == {"build", "deploy"}


def test_cascade_isolates_sibling_helpers(tmp_path, capsys):
    # F14: two tasks files each `import helpers` from their own dir — each must
    # bind ITS OWN helpers module, not whoever-imported-first-wins.
    (tmp_path / "helpers.py").write_text("VALUE = 'root'\n")
    root = _write(
        tmp_path / "tasks.py",
        "from livery.footman import task\nimport helpers\n"
        "@task\ndef a():\n    print(helpers.VALUE)\n",
    )
    svc = tmp_path / "svc"
    svc.mkdir()
    (svc / "helpers.py").write_text("VALUE = 'svc'\n")
    sub = _write(
        svc / "tasks.py",
        "from livery.footman import task\nimport helpers\n"
        "@task\ndef b():\n    print(helpers.VALUE)\n",
    )
    merged = _discover.load_tree([root, sub])
    merged.tasks["a"]()
    merged.tasks["b"]()
    out = capsys.readouterr().out
    assert "root" in out and "svc" in out  # each resolved its own sibling


def test_cascade_isolates_sibling_packages_submodules(tmp_path, capsys):
    # H3, F14's package sibling: eviction dropped a sibling package's
    # __init__ but not its *submodules*, so the nested file's
    # `import pkg.sub` re-imported `pkg` fresh and then took `pkg.sub`
    # straight out of sys.modules — the root's copy, silently.
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "sub.py").write_text("VALUE = 'root'\n")
    root = _write(
        tmp_path / "tasks.py",
        "from livery.footman import task\nimport pkg.sub\n"
        "@task\ndef a():\n    print(pkg.sub.VALUE)\n",
    )
    svc = tmp_path / "svc"
    (svc / "pkg").mkdir(parents=True)
    (svc / "pkg" / "__init__.py").write_text("")
    (svc / "pkg" / "sub.py").write_text("VALUE = 'svc'\n")
    sub = _write(
        svc / "tasks.py",
        "from livery.footman import task\nimport pkg.sub\n"
        "@task\ndef b():\n    print(pkg.sub.VALUE)\n",
    )
    merged = _discover.load_tree([root, sub])
    merged.tasks["a"]()
    merged.tasks["b"]()
    out = capsys.readouterr().out
    assert "root" in out and "svc" in out  # each bound its own package


def test_failed_cascade_import_resets_registry(tmp_path):
    # F62: a file that registers a task then raises must not strand ghost tasks
    # in the global registry for the rest of the process.
    bad = _write(
        tmp_path / "tasks.py",
        "from livery.footman import task\n@task\ndef ghost(): ...\n"
        "raise RuntimeError('boom')\n",
    )
    with pytest.raises(_discover.TasksImportError):
        _discover.load_tree([bad])
    assert "ghost" not in registry.root.tasks


def test_sys_exit_at_import_time_is_a_taught_error(tmp_path):
    # A tasks file calling sys.exit() while being imported used to kill the
    # whole invocation with its raw exit code and not a single word.
    bad = _write(tmp_path / "tasks.py", "import sys\nsys.exit(3)\n")
    with pytest.raises(
        _discover.TasksImportError, match=r"sys\.exit\(3\) at import time"
    ):
        _discover.load_tree([bad])


@pytest.mark.skipif(sys.platform == "win32", reason="no mkfifo on Windows")
def test_a_non_regular_config_file_is_refused_not_read(tmp_path):
    # A FIFO would block the read without bound; config is a regular file or
    # it is nothing. Required (an explicit --config) refuses loudly, the
    # cascade skips it silently — the same split every unreadable file gets.
    fifo = tmp_path / "cfg.toml"
    if sys.platform == "win32":
        return  # the skipif above already keeps Windows out; narrows for mypy
    os.mkfifo(fifo)
    with pytest.raises(_config.ConfigError, match="not a regular file"):
        _config._read_toml(fifo, required=True)
    assert _config._read_toml(fifo) is None


def test_explicit_config_keys_its_own_completion_manifest(tmp_path, monkeypatch):
    # A --config run reshapes the tree, and its manifest used to land on the
    # plain cwd key — after which plain TAB offered tasks the plain run
    # refuses. It rides a (cwd, config) key now, exactly as -f does.
    _write(
        tmp_path / "tasks.py",
        "from livery.footman import task\n@task\ndef plainly(): ...\n",
    )
    _write(
        tmp_path / "alt_tasks.py",
        "from livery.footman import task\n@task\ndef otherly(): ...\n",
    )
    (tmp_path / "alt.toml").write_text('tasks = "alt_tasks.py"\n')
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    monkeypatch.chdir(tmp_path)
    assert _app.run(["-l"]) == 0
    assert _app.run(["--config=alt.toml", "-l"]) == 0
    plain = _paths.manifest_path(tmp_path).read_text(encoding="utf-8")
    assert "plainly" in plain and "otherly" not in plain
    keyed = _paths.source_manifest_path(tmp_path, Path("alt.toml"))
    assert "otherly" in keyed.read_text(encoding="utf-8")


def test_background_refresh_honours_the_cascade_setting(tmp_path, monkeypatch):
    # The detached rebuild used to walk to the repo root regardless of the
    # cascade setting, so with cascade="none" TAB offered tasks the runner
    # then refuses by name.
    _write(
        tmp_path / "tasks.py",
        "from livery.footman import task\n@task\ndef above(): ...\n",
    )
    _write(
        tmp_path / "svc" / "tasks.py",
        "from livery.footman import task\n@task\ndef below(): ...\n",
    )
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    monkeypatch.setattr(
        _paths, "user_tasks_file", lambda name: tmp_path / "no-user-tasks.py"
    )
    monkeypatch.setenv("FOOTMAN_CASCADE", "none")
    monkeypatch.chdir(tmp_path / "svc")
    _refresh._rebuild()
    built = _paths.manifest_path(tmp_path / "svc").read_text(encoding="utf-8")
    assert "below" in built and "above" not in built


def test_cascade_tags_defining_dir(tmp_path):
    root = _write(
        tmp_path / "tasks.py", "from livery.footman import task\n@task\ndef a():...\n"
    )
    sub = _write(
        tmp_path / "svc" / "tasks.py",
        "from livery.footman import task\n@task\ndef b():...\n",
    )
    merged = _discover.load_tree([root, sub])
    assert _discover.defining_dir(merged.tasks["a"]) == str(tmp_path)
    assert _discover.defining_dir(merged.tasks["b"]) == str(tmp_path / "svc")


SHARED = "from livery.footman import context, task\n@task\ndef where():\n    pass\n"


def test_one_task_at_two_addresses_with_two_folders_is_refused(tmp_path):
    # The stamp lives on the function, so one function can only answer with
    # one folder. Mounted from two cascade levels under different names, the
    # last stamp wins and the other address silently runs somewhere its own
    # tasks file never named. Refused, because no rule about which stamp wins
    # can be right for both addresses.
    _write(tmp_path / "shared.py", SHARED)
    root = _write(
        tmp_path / "tasks.py",
        "from livery.footman import include\ninclude('shared', into='rootside')\n",
    )
    sub = _write(
        tmp_path / "svc" / "tasks.py",
        "from livery.footman import include\ninclude('shared', into='svcside')\n",
    )
    with pytest.raises(_discover.TasksImportError) as caught:
        _discover.load_tree([root, sub])
    said = str(caught.value.original)
    assert "'rootside.where'" in said  # the mount that was already there
    assert "two defining directories" in said
    assert 'cwd="asinvoked"' in said  # the goal the second mount probably had
    assert caught.value.path == sub  # the nearer file, the one to edit


def test_a_nearer_file_may_shadow_the_same_task_with_itself(tmp_path):
    # The same function, the same address, a new folder: not a conflict but
    # the cascade's whole point — the nearer file wins and its directory is
    # the right answer, because it is the file that defined the address.
    _write(tmp_path / "shared.py", SHARED)
    root = _write(
        tmp_path / "tasks.py", "from livery.footman import include\ninclude('shared')\n"
    )
    sub = _write(
        tmp_path / "svc" / "tasks.py",
        "from livery.footman import include\ninclude('shared')\n",
    )
    merged = _discover.load_tree([root, sub])
    assert _discover.defining_dir(merged.tasks["where"]) == str(tmp_path / "svc")


def test_two_providers_may_share_a_helper(tmp_path):
    # Two addresses for one function, and no disagreement: both providers sit
    # in the same folder, so both stamps say the same thing. Nobody authored
    # this alias and nobody can avoid it, so it must not be refused.
    _write(tmp_path / "common.py", SHARED)
    _write(
        tmp_path / "alpha.py", "from livery.footman import include\ninclude('common')\n"
    )
    _write(
        tmp_path / "beta.py", "from livery.footman import include\ninclude('common')\n"
    )
    root = _write(
        tmp_path / "tasks.py",
        "from livery.footman import include\n"
        "include('alpha', into='alpha')\ninclude('beta', into='beta')\n",
    )
    merged = _discover.load_tree([root])
    assert merged.groups["alpha"].tasks["where"] is merged.groups["beta"].tasks["where"]
    assert _discover.defining_dir(merged.groups["beta"].tasks["where"]) == str(tmp_path)


def test_a_second_load_may_restamp_the_same_function(tmp_path):
    # The claim is per load, not an attribute: a fresh process, the refresh
    # child and a second in-process invocation all legitimately re-stamp the
    # same function. Only a disagreement *within one cascade* is a conflict.
    _write(tmp_path / "shared.py", SHARED)
    root = _write(
        tmp_path / "tasks.py", "from livery.footman import include\ninclude('shared')\n"
    )
    assert _discover.load_tree([root]).tasks["where"] is not None
    merged = _discover.load_tree([root])  # would refuse if the claim persisted
    assert _discover.defining_dir(merged.tasks["where"]) == str(tmp_path)


def test_load_tree_leaves_no_global_state(tmp_path):
    from livery.footman import registry

    root = _write(
        tmp_path / "tasks.py", "from livery.footman import task\n@task\ndef a():...\n"
    )
    _discover.load_tree([root])
    assert registry.root.tasks == {}  # reset after building


# --- defining-dir cwd at execution -------------------------------------------


def test_run_task_uses_defining_dir_as_cwd():
    def fn():
        return 0

    fn._footman_dir = "/some/place"  # type: ignore[attr-defined]
    ctx = Context()
    seg = Segment(task="fn", path=["fn"])
    _executor.run_task(fn, seg, ctx)
    assert ctx.cwd == Path("/some/place")


def test_run_task_respects_explicit_cwd():
    def fn():
        return 0

    fn._footman_dir = "/some/place"  # type: ignore[attr-defined]
    ctx = Context(cwd=Path("/explicit"))
    _executor.run_task(fn, Segment(task="fn", path=["fn"]), ctx)
    assert ctx.cwd == Path("/explicit")  # not overridden


# --- config discovery --------------------------------------------------------


def test_config_nearest_wins(tmp_path):
    _write(tmp_path / "pyproject.toml", "[tool.footman]\ntasks = 'root.py'\n")
    sub = tmp_path / "svc"
    sub.mkdir()
    _write(sub / "footman.toml", "tasks = 'svc.py'\n")
    cfg = _config.load_config(sub, tmp_path)
    assert cfg["tasks"] == "svc.py"  # cwd folder overrides the root


def test_config_footman_toml_beats_pyproject_in_same_dir(tmp_path):
    _write(tmp_path / "pyproject.toml", "[tool.footman]\nsequential = false\n")
    _write(tmp_path / "footman.toml", "sequential = true\n")
    cfg = _config.load_config(tmp_path, tmp_path)
    assert cfg["sequential"] is True


def test_config_cli_path_overrides_all(tmp_path):
    _write(tmp_path / "footman.toml", "tasks = 'a.py'\n")
    override = _write(tmp_path / "custom.toml", "tasks = 'b.py'\n")
    cfg = _config.load_config(tmp_path, tmp_path, str(override))
    assert cfg["tasks"] == "b.py"


def test_config_corrupt_toml_is_ignored(tmp_path):
    _write(tmp_path / "footman.toml", "this is : not [[ valid")
    assert _config.load_config(tmp_path, tmp_path) == {}


def test_config_non_utf8_is_malformed_not_a_crash(tmp_path):
    # One latin-1 byte used to escape as a raw UnicodeDecodeError, so every
    # invocation under that directory died. TOML's spec makes UTF-8
    # mandatory: the file is malformed by the format's own rule, and takes
    # the malformed path — warn, skip, carry on.
    (tmp_path / "footman.toml").write_bytes(b"# caf\xe9\nsequential = true\n")
    warnings: list[str] = []
    assert _config.load_config(tmp_path, tmp_path, on_warning=warnings.append) == {}
    assert any("not valid UTF-8" in w and "re-save" in w for w in warnings)


def test_config_non_utf8_pyproject_does_not_brick_the_cascade(tmp_path):
    # The bad byte need not be anywhere near [tool.footman]: a description
    # nobody asked footman to read makes the whole file undecodable.
    (tmp_path / "pyproject.toml").write_bytes(
        b"[project]\nname='x'\ndescription='caf\xe9'\n"
    )
    warnings: list[str] = []
    assert _config.load_config(tmp_path, tmp_path, on_warning=warnings.append) == {}
    assert any("pyproject.toml" in w and "not valid UTF-8" in w for w in warnings)


def test_config_non_utf8_explicit_file_is_loud(tmp_path):
    # A file named on purpose fails loudly, like any other malformed --config.
    named = tmp_path / "custom.toml"
    named.write_bytes(b"# caf\xe9\nsequential = true\n")
    with pytest.raises(_config.ConfigError, match=r"not valid UTF-8"):
        _config.load_config(tmp_path, tmp_path, str(named))


def test_config_utf8_bom_reads_normally(tmp_path):
    # What a Windows editor writes. A byte-order mark is the one encoding
    # hint that is never a guess, so it is stripped rather than handed to
    # tomllib as a stray glyph on line 1.
    (tmp_path / "footman.toml").write_bytes(b"\xef\xbb\xbfsequential = true\n")
    assert _config.load_config(tmp_path, tmp_path)["sequential"] is True


def test_config_utf16_bom_is_refused_by_name(tmp_path):
    # Detected, never decoded: a UTF-16 config would work here and nowhere
    # else, so the refusal says what it found instead of reading it anyway.
    (tmp_path / "footman.toml").write_bytes("sequential = true\n".encode("utf-16"))
    warnings: list[str] = []
    assert _config.load_config(tmp_path, tmp_path, on_warning=warnings.append) == {}
    assert any("UTF-16 byte-order mark" in w for w in warnings)


def test_config_global_file_is_the_bottom_rung(tmp_path, monkeypatch):
    # The user-level file seeds the merge; every project layer beats it.
    global_file = _write(tmp_path / "global.toml", "uv = false\ntasks = 'g.py'\n")
    monkeypatch.setenv("FOOTMAN_CONFIG", str(global_file))
    project = tmp_path / "proj"
    project.mkdir()
    cfg = _config.load_config(project, project)
    assert cfg == {"uv": False, "tasks": "g.py"}  # global alone applies
    _write(project / "footman.toml", "tasks = 'p.py'\n")
    cfg = _config.load_config(project, project)
    assert cfg["tasks"] == "p.py"  # the cascade wins the contested key
    assert cfg["uv"] is False  # and the uncontested global key survives


def test_config_global_default_location(tmp_path, monkeypatch):
    # Without FOOTMAN_CONFIG / FOOTMAN_CONFIG_DIR (the suite pins the latter
    # for hermeticity), the file lives under XDG config home.
    monkeypatch.delenv("FOOTMAN_CONFIG", raising=False)
    monkeypatch.delenv("FOOTMAN_CONFIG_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    spot = tmp_path / "xdg" / "footman" / "config.toml"
    spot.parent.mkdir(parents=True)
    _write(spot, "sequential = true\n")
    project = tmp_path / "proj"
    project.mkdir()
    assert _config.load_config(project, project)["sequential"] is True


def test_config_malformed_global_warns_and_is_skipped(tmp_path, monkeypatch):
    global_file = _write(tmp_path / "global.toml", "not [[ toml")
    monkeypatch.setenv("FOOTMAN_CONFIG", str(global_file))
    warnings: list[str] = []
    cfg = _config.load_config(tmp_path, tmp_path, on_warning=warnings.append)
    assert cfg == {}
    assert any("malformed" in w for w in warnings)


def test_config_user_level_keys_stripped_from_the_cascade(tmp_path, monkeypatch):
    # `gc` governs the shared cache: a per-project value would lie. It only
    # counts from the user-level file; cascade files get a note (verbose runs
    # wire on_note; others pass None and the strip is silent).
    global_file = _write(tmp_path / "global.toml", "gc = false\n")
    monkeypatch.setenv("FOOTMAN_CONFIG", str(global_file))
    _write(tmp_path / "footman.toml", "gc = true\ntasks = 'x.py'\n")
    notes: list[str] = []
    cfg = _config.load_config(tmp_path, tmp_path, on_note=notes.append)
    assert cfg["gc"] is False  # the global value, not the project's
    assert cfg["tasks"] == "x.py"  # ordinary keys cascade as ever
    assert any("user-level" in n for n in notes)
    assert _config.load_config(tmp_path, tmp_path)["gc"] is False  # silent too


def test_config_cli_path_replaces_global_and_cascade(tmp_path, monkeypatch):
    # --config is total control: the named file is exactly what applies.
    global_file = _write(tmp_path / "global.toml", "uv = false\n")
    monkeypatch.setenv("FOOTMAN_CONFIG", str(global_file))
    _write(tmp_path / "footman.toml", "sequential = true\n")
    override = _write(tmp_path / "custom.toml", "tasks = 'b.py'\n")
    cfg = _config.load_config(tmp_path, tmp_path, str(override))
    assert cfg == {"tasks": "b.py"}  # no uv, no sequential: replaced, not merged


# --- end-to-end through the app ----------------------------------------------


@pytest.fixture
def mono(tmp_path, monkeypatch):
    """A monorepo: .git at the root, tasks at root and in svc/api."""
    (tmp_path / ".git").mkdir()
    _write(
        tmp_path / "tasks.py",
        "from livery.footman import task\n"
        "@task\ndef build():\n    print('root-build')\n"
        "@task\ndef test():\n    print('root-test')\n",
    )
    _write(
        tmp_path / "svc" / "api" / "tasks.py",
        "from livery.footman import task\n"
        "@task\ndef serve():\n    print('api-serve')\n"
        "@task\ndef build():\n    print('api-build')\n",
    )
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    return tmp_path


def test_app_lists_merged_tasks(mono, monkeypatch, capsys):
    monkeypatch.chdir(mono / "svc" / "api")
    assert _app.run(["-l"]) == 0
    out = capsys.readouterr().out
    assert "build" in out and "test" in out and "serve" in out


def test_app_local_override_runs(mono, monkeypatch, capsys):
    monkeypatch.chdir(mono / "svc" / "api")
    assert _app.run(["build"]) == 0
    assert "api-build" in capsys.readouterr().out  # not root-build


def test_app_inherited_task_runs_from_subdir(mono, monkeypatch, capsys):
    monkeypatch.chdir(mono / "svc" / "api")
    assert _app.run(["test"]) == 0  # inherited from root
    assert "root-test" in capsys.readouterr().out


def test_ceiling_excludes_files_above_git(tmp_path, monkeypatch, capsys):
    # A tasks.py ABOVE the .git root must not enter the cascade. The repo
    # nests inside this test's own tmp_path — writing to the *shared*
    # pytest basetemp (a fixture's parent) once poisoned every later test
    # whose ceiling walk reached it, invisibly in alphabetical runs.
    _write(
        tmp_path / "tasks.py",
        "from livery.footman import task\n@task\ndef outside():...\n",
    )
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    _write(
        repo / "svc" / "api" / "tasks.py",
        "from livery.footman import task\n@task\ndef serve():...\n",
    )
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    monkeypatch.chdir(repo / "svc" / "api")
    assert _app.run(["-l"]) == 0
    out = capsys.readouterr().out
    assert "serve" in out  # the repo's own cascade is intact
    assert "outside" not in out


def test_per_cwd_manifest_files_differ(mono, monkeypatch):
    monkeypatch.chdir(mono)
    _app.run(["-l"])
    root_cache = _paths.manifest_path(mono)
    monkeypatch.chdir(mono / "svc" / "api")
    _app.run(["-l"])
    api_cache = _paths.manifest_path(mono / "svc" / "api")
    assert root_cache.exists() and api_cache.exists()
    assert root_cache != api_cache


def test_config_sequential_default(mono, monkeypatch, capsys):
    _write(mono / "footman.toml", "sequential = true\n")
    monkeypatch.chdir(mono)
    # in --json mode, sequential still runs both; assert the run succeeds
    assert _app.run(["--json", "build", "test"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [r["task"] for r in payload["items"] if "task" in r] == ["build", "test"]


def test_config_tasks_filename_in_cascade(mono, monkeypatch, capsys):
    _write(mono / "footman.toml", "tasks = 'jobs.py'\n")
    _write(
        mono / "jobs.py",
        "from livery.footman import task\n@task\ndef custom():\n    print('via-jobs')\n",
    )
    monkeypatch.chdir(mono)
    assert _app.run(["custom"]) == 0
    assert "via-jobs" in capsys.readouterr().out


# --- inherited(): extending an overridden task --------------------------------


def _inherit_repo(tmp_path, monkeypatch, leaf_body: str):
    """A three-level cascade whose leaf overrides `check`."""
    (tmp_path / ".git").mkdir()
    # The bodies print() rather than run("echo …"): the tests are about
    # inherited() chaining, not subprocesses, and `echo` is not a program
    # every machine can run (Windows only has an `echo.exe` when Git's
    # `usr/bin` is on PATH).
    _write(
        tmp_path / "tasks.py",
        "from livery.footman import task\n"
        "@task\ndef check(fix: bool = False):\n"
        '    """Root gate."""\n'
        '    print(f"root fix={fix}")\n',
    )
    _write(
        tmp_path / "svc" / "tasks.py",
        "from livery.footman import inherited, task\n"
        "@task\ndef check(fix: bool = False):\n"
        '    """Mid gate."""\n'
        "    inherited()(fix=fix)\n"
        '    print("mid")\n',
    )
    _write(tmp_path / "svc" / "api" / "tasks.py", leaf_body)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    monkeypatch.chdir(tmp_path / "svc" / "api")
    return tmp_path


LEAF = (
    "from livery.footman import inherited, task\n"
    "@task\ndef check(fix: bool = False, contracts: bool = True):\n"
    '    """Leaf gate."""\n'
    "    inherited()(fix=fix)\n"
    '    if contracts:\n        print("leaf")\n'
)


def test_inherited_walks_the_whole_cascade(tmp_path, monkeypatch, capsys):
    # Three levels deep: the leaf calls the mid, which calls the root —
    # each extending the last, in order.
    _inherit_repo(tmp_path, monkeypatch, LEAF)
    assert _app.run(["check", "--fix"]) == 0
    out = capsys.readouterr().out
    assert out.index("root fix=True") < out.index("mid") < out.index("leaf")


def test_inherited_names_the_task_it_calls(tmp_path, monkeypatch, capsys):
    # functools.wraps keeps the name, so `parallel(inherited(), extra)`
    # labels its live line honestly instead of showing an anonymous call.
    _inherit_repo(tmp_path, monkeypatch, LEAF)
    from livery.footman import Context, _discover, inherited, use_context

    files = _paths.task_files(Path.cwd(), tmp_path)
    tree = _discover.load_tree(files)
    with use_context(Context(fn=tree.tasks["check"])):
        assert inherited().__name__ == "check"


def test_inherited_forwarding_is_explicit(tmp_path, monkeypatch, capsys):
    # The leaf chooses what to pass: the root never sees --contracts, and
    # can be given a different value entirely.
    leaf = (
        "from livery.footman import inherited, task\n"
        "@task\ndef check(fix: bool = False, contracts: bool = True):\n"
        '    """Leaf gate."""\n'
        "    inherited()(fix=False)\n"
    )
    _inherit_repo(tmp_path, monkeypatch, leaf)
    assert _app.run(["check", "--fix"]) == 0
    assert "root fix=False" in capsys.readouterr().out


def test_inherited_without_a_shadow_is_taught(tmp_path, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    _write(
        tmp_path / "tasks.py",
        "from livery.footman import inherited, task\n"
        "@task\ndef solo():\n"
        '    """No parent."""\n'
        "    inherited()\n",
    )
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    monkeypatch.chdir(tmp_path)
    assert _app.run(["solo"]) != 0
    err = capsys.readouterr().err
    assert "does not shadow an inherited task" in err
    assert "--where solo" in err  # the message names the discovery command


def test_where_lists_the_shadow_chain(tmp_path, monkeypatch, capsys):
    _inherit_repo(tmp_path, monkeypatch, LEAF)
    assert _app.run(["--where=check"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 3  # leaf, mid, root
    # os.sep, not "/": --where prints native paths so editors can open them.
    leaf = str(Path("api") / "tasks.py") + ":2"
    assert lines[0].endswith(leaf) and "(shadowed)" not in lines[0]
    assert all("(shadowed)" in line for line in lines[1:])


def test_help_shows_the_inherited_options(tmp_path, monkeypatch, capsys):
    import re

    _inherit_repo(tmp_path, monkeypatch, LEAF)
    assert _app.run(["--help", "check"]) == 0
    out = capsys.readouterr().out
    assert "shadows" in out and "inherited() calls it" in out
    assert "fm check [--fix]" in out  # the parent's options, not the leaf's
    # The *where*: a real file:line, never the "the cascade" fallback the
    # renderer degrades to when the manifest loses the location — which it
    # could do with the suite staying green (audit, suite pass).
    assert re.search(r"shadows .*tasks\.py:\d+", out)
    assert "shadows the cascade" not in out


# --- the cascade walk mode (tri-state) ---------------------------------------


def _three_level_tree(tmp_path):
    """outer/ (tasks.py) above outer/repo/ (.git + tasks.py) above repo/pkg/."""
    outer = tmp_path / "outer"
    repo = outer / "repo"
    pkg = repo / "pkg"
    pkg.mkdir(parents=True)
    (repo / ".git").mkdir()
    for d in (outer, repo, pkg):
        (d / "tasks.py").write_text(
            "from livery.footman import task\n", encoding="utf-8"
        )
    return outer, repo, pkg


@pytest.fixture
def iso_cascade(tmp_path, monkeypatch):
    """Isolate the user-level file and the env override from the machine."""
    monkeypatch.delenv("FOOTMAN_CASCADE", raising=False)
    monkeypatch.setenv("FOOTMAN_CONFIG", str(tmp_path / "global.toml"))


def test_cascade_default_walks_to_the_repo_root(tmp_path, monkeypatch, iso_cascade):
    _, repo, pkg = _three_level_tree(tmp_path)
    monkeypatch.chdir(pkg)
    files = _app.resolve_task_files({}).files
    assert files == [repo / "tasks.py", pkg / "tasks.py"]


def test_cascade_none_limits_discovery_to_the_cwd(tmp_path, monkeypatch, iso_cascade):
    _, _, pkg = _three_level_tree(tmp_path)
    monkeypatch.setenv("FOOTMAN_CASCADE", "none")
    monkeypatch.chdir(pkg)
    files = _app.resolve_task_files({}).files
    assert files == [pkg / "tasks.py"]


def test_cascade_filesystem_crosses_the_repo_boundary(
    tmp_path, monkeypatch, iso_cascade
):
    outer, repo, pkg = _three_level_tree(tmp_path)
    monkeypatch.setenv("FOOTMAN_CASCADE", "filesystem")
    monkeypatch.chdir(pkg)
    files = _app.resolve_task_files({}).files
    assert files[-3:] == [outer / "tasks.py", repo / "tasks.py", pkg / "tasks.py"]


def test_cascade_key_reads_from_the_user_level_file(tmp_path, monkeypatch, iso_cascade):
    _, _, pkg = _three_level_tree(tmp_path)
    (tmp_path / "global.toml").write_text("cascade = 'none'\n", encoding="utf-8")
    monkeypatch.chdir(pkg)
    files = _app.resolve_task_files({}).files
    assert files == [pkg / "tasks.py"]


def test_cascade_env_overrides_the_config_key(tmp_path, monkeypatch, iso_cascade):
    _, _, pkg = _three_level_tree(tmp_path)
    (tmp_path / "global.toml").write_text("cascade = 'filesystem'\n", encoding="utf-8")
    monkeypatch.setenv("FOOTMAN_CASCADE", "none")
    monkeypatch.chdir(pkg)
    files = _app.resolve_task_files({}).files
    assert files == [pkg / "tasks.py"]


def test_cascade_in_a_project_file_is_stripped(tmp_path, monkeypatch, iso_cascade):
    outer, repo, pkg = _three_level_tree(tmp_path)
    (repo / "footman.toml").write_text("cascade = 'filesystem'\n", encoding="utf-8")
    monkeypatch.chdir(pkg)
    files = _app.resolve_task_files({}).files
    assert outer / "tasks.py" not in files  # a project key cannot widen the walk


def test_cascade_config_search_follows_the_mode(tmp_path, monkeypatch, iso_cascade):
    outer, _, pkg = _three_level_tree(tmp_path)
    (outer / "footman.toml").write_text("color = 'never'\n", encoding="utf-8")
    monkeypatch.chdir(pkg)
    cfg = _app.resolve_task_files({}).cfg
    assert "color" not in cfg  # above the repo: invisible under the default walk
    monkeypatch.setenv("FOOTMAN_CASCADE", "filesystem")
    cfg = _app.resolve_task_files({}).cfg
    assert cfg["color"] == "never"  # one walk governs config and task files


def test_cascade_unknown_env_value_is_a_taught_error(
    tmp_path, monkeypatch, iso_cascade
):
    monkeypatch.setenv("FOOTMAN_CASCADE", "galaxy")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(_config.CascadeError, match="galaxy"):
        _app.resolve_task_files({})


def test_cascade_unknown_config_value_names_the_tokens(
    tmp_path, monkeypatch, iso_cascade
):
    (tmp_path / "global.toml").write_text("cascade = 'sideways'\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(_config.CascadeError) as exc:
        _app.resolve_task_files({})
    for token in ("none", "repo", "filesystem"):
        assert token in str(exc.value)
