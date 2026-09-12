"""The public App entry: custom brand (name/prog/version) in all output.

The disk-backed cases run through `footman.testing.Runner` — the suite
dogfoods the same harness users are told to test their branded CLIs with.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from livery.footman import App, Brand, __version__, _paths, registry
from livery.footman._executor import EX_USAGE
from livery.footman.app import DEFAULT_BRAND
from livery.footman.testing import Runner


def test_brand_defaults_to_footman():
    b = Brand()
    assert (b.name, b.prog, b.version) == ("footman", "fm", __version__)


def test_default_app_version(capsys):
    assert App().run(["-V"]) == 0
    assert capsys.readouterr().out.strip() == f"footman {__version__}"


def test_custom_brand_version(capsys):
    app = App(name="Acme", prog="acme", version="1.4.0")
    assert app.run(["-V"]) == 0
    assert capsys.readouterr().out.strip() == "Acme 1.4.0"


def test_custom_brand_error_prefix():
    acme = Runner(App(name="Acme", prog="acme", version="1.4.0"))
    result = acme.invoke("-f /nope/tasks.py whatever")
    assert result.exit_code == EX_USAGE
    assert result.stderr.startswith("acme: ")


def test_custom_version_defaults_to_footman_when_omitted(capsys):
    # prog/name can differ while version falls back to footman's own
    App(name="Acme", prog="acme").run(["-V"])
    assert capsys.readouterr().out.strip() == f"Acme {__version__}"


def test_app_complete_dispatches(capsys, tmp_path, monkeypatch):
    # The --complete hot path returns cleanly even with nothing cached.
    # From a directory outside any locked project: inside this
    # repository the path heals the project environment (uv sync
    # against the real venv), which re-installs a stale editable under
    # the other xdist workers' entry-point scans.
    monkeypatch.chdir(tmp_path)
    assert App().run(["--complete", "--", ""]) == 0


def test_default_app_runs_tasks_like_fm(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(
        "from livery.footman import task\n@task\ndef hi():\n    print('hello')\n"
    )
    result = Runner().invoke("hi", cwd=tmp_path)
    assert result.ok
    assert "hello" in result.stdout


def test_custom_brand_runs_tasks_from_cascade(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "tasks.py").write_text(
        "from livery.footman import task\n@task\ndef ship():\n    print('shipped')\n"
    )
    acme = Runner(App(name="Acme", prog="acme", version="1.4.0"))
    result = acme.invoke("ship", cwd=tmp_path)  # cascade discovery, rebranded
    assert result.ok
    assert "shipped" in result.stdout


def test_help_globals_row_uses_brand(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "tasks.py").write_text(
        "from livery.footman import task\n@task\ndef hi():\n    print('hi')\n"
    )
    acme = Runner(App(name="Acme", prog="acme", version="1.4.0"))
    result = acme.invoke("--help", cwd=tmp_path)
    assert "help for acme" in result.stdout
    assert "help for fm" not in result.stdout


def test_brand_renames_the_default_tasks_file(tmp_path, monkeypatch):
    """A brand's `tasks_file` sets the filename its users write, and the
    cascade honours it without any per-project config."""
    from livery.footman import App, _paths
    from livery.footman.testing import Runner

    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "acmetasks.py").write_text(
        'from livery.footman import task\n\n@task\ndef ship():\n    "Ship it."\n'
    )
    (tmp_path / "tasks.py").write_text(
        'from livery.footman import task\n\n@task\ndef wrong():\n    "Not this one."\n'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    acme = Runner(
        App(name="Acme", prog="acme", version="1.0", tasks_file="acmetasks.py")
    )
    out = acme.invoke("--list").stdout
    assert "ship" in out and "wrong" not in out


def test_brand_tasks_file_rides_in_the_manifest(tmp_path, monkeypatch):
    """The background refresh child can't know the brand — so the filename
    is baked into the manifest it rebuilds from."""
    import json

    from livery.footman import App, _paths
    from livery.footman.testing import Runner

    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "acmetasks.py").write_text(
        'from livery.footman import task\n\n@task\ndef ship():\n    "Ship it."\n'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    Runner(App(prog="acme", tasks_file="acmetasks.py")).invoke("--list")
    # In the brand's own cache corner — `.cache/acme/`, from the config stem
    # deriving from prog. (`.cache/footman/` here used to be the accident of
    # `config_name` defaulting to the display name.)
    manifest = next(
        p
        for p in (tmp_path / ".cache" / "acme").glob("*.json")
        if not p.name.endswith(".times.json")
    )
    baked = json.loads(manifest.read_text(encoding="utf-8"))
    assert baked["tasks_file"] == "acmetasks.py"


# --- the brand's private world -----------------------------------------------
#
# A branded CLI is a product; footman is a dependency inside it. Every
# location and every environment variable it touches must derive from the
# brand — and stock footman must be exactly what it always was.

TASKS = 'from livery.footman import task\n\n@task\ndef ship():\n    "Ship it."\n'


def _project(tmp_path, body: str = "") -> None:
    (tmp_path / "pyproject.toml").write_text(f"[project]\nname='x'\n{body}")
    (tmp_path / "tasks.py").write_text(TASKS)


def test_prefix_is_derived_from_the_command_name():
    # `prog`: the word users type, and shell-safe by construction where a
    # display name is free text.
    assert Brand(prog="acme").env("CACHE_DIR") == "ACME_CACHE_DIR"
    assert Brand(prog="acme-tool").prefix == "ACME_TOOL"
    assert Brand(prog="acme", env_prefix="ACME2").env("NO_GC") == "ACME2_NO_GC"


def test_stock_footman_pins_its_prefix():
    # Not compatibility: `FOOTMAN_CACHE_DIR` says what it belongs to and is
    # searchable, where `FM_CACHE_DIR` is opaque. A two-letter command is
    # exactly when a brand should pin a longer prefix than its prog.
    assert DEFAULT_BRAND.prefix == "FOOTMAN"
    assert DEFAULT_BRAND.env("CACHE_DIR") == "FOOTMAN_CACHE_DIR"


def test_config_name_derives_from_prog():
    # The machine word, exactly as the env prefix does — never the display
    # name, which is free text: `[tool.Acme]` from `name="Acme"` was a
    # silent misconfig for every user the docs taught `[tool.acme]`.
    b = Brand(name="Acme", prog="acme")
    assert b.config_stem == "acme"
    assert b.config_file() == "acme.toml"
    assert App(name="Acme", prog="acme").brand.config_stem == "acme"
    assert Brand(prog="acme", config_name="acme-runner").config_stem == "acme-runner"


def test_stock_footman_pins_its_config_name():
    # The config twin of the pinned prefix: `footman.toml`, not `fm.toml`.
    assert DEFAULT_BRAND.config_stem == "footman"
    assert DEFAULT_BRAND.config_file() == "footman.toml"


def test_a_bare_app_is_stock_footman():
    # Keeping the `fm` command keeps the stock pins: a test-harness
    # `App(tasks_file=...)` reads `[tool.footman]` and `FOOTMAN_*` exactly
    # like the `fm` on PATH — not a derived `[tool.fm]` nobody writes.
    brand = App(tasks_file="jobs.py").brand
    assert brand.config_stem == "footman"
    assert brand.prefix == "FOOTMAN"
    # Moving `prog` ends the pins, and the brand derives from its command.
    assert App(prog="acme").brand.config_stem == "acme"
    assert App(prog="acme").brand.prefix == "ACME"


def test_the_two_folders_are_placed_independently(tmp_path, monkeypatch):
    # The point of two fields rather than one home: a product puts its cache
    # in its own cache area and its data somewhere else entirely.
    cache, data = tmp_path / "prod" / ".cache" / "acme-cli", tmp_path / "prod" / "acme"
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / "xdg-cache")
    Runner(App(name="acme", prog="acme", cache_dir=cache, data_dir=data)).invoke(
        "--list"
    )
    assert list(cache.glob("*.json"))  # its manifest landed here
    assert not (tmp_path / "xdg-cache").exists()  # and nothing under XDG


def test_stock_variables_never_move_a_branded_client(tmp_path, monkeypatch):
    # The whole point: someone debugging `fm` must not relocate a product.
    elsewhere, cache = tmp_path / "stock", tmp_path / "mine"
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(elsewhere))
    Runner(App(name="acme", prog="acme", cache_dir=cache)).invoke("--list")
    assert not elsewhere.exists()
    assert list(cache.glob("*.json"))


def test_a_brands_own_variables_win_over_its_declared_folders(tmp_path, monkeypatch):
    # The environment beats the brand — that is what lets two installations
    # run side by side under different identities.
    theirs = tmp_path / "theirs"
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ACME_CACHE_DIR", str(theirs))
    app = App(name="acme", prog="acme", cache_dir=tmp_path / "declared")
    Runner(app).invoke("--list")
    assert list(theirs.glob("*.json"))
    assert not (tmp_path / "declared").exists()


def test_data_dir_defaults_to_xdg_data_home(tmp_path, monkeypatch):
    # ~/.local/share, where footman's completion scripts already live.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    before = _paths.child_args()
    try:
        _paths.configure(prefix="ACME", config_name="acme")
        assert _paths.footman_data_dir() == tmp_path / "share" / "acme"
        monkeypatch.setenv("ACME_DATA_DIR", str(tmp_path / "elsewhere"))
        assert _paths.footman_data_dir() == tmp_path / "elsewhere"
    finally:
        _paths.configure_child(*before)


def test_the_accessors_create_the_directory(tmp_path, monkeypatch):
    # A task writing into these should not have to mkdir first.
    import livery.footman as footman

    before = _paths.child_args()
    try:
        _paths.configure(
            cache_dir=tmp_path / "c" / "deep", data_dir=tmp_path / "d" / "deep"
        )
        assert footman.cache_dir().is_dir()
        assert footman.data_dir().is_dir()
        if os.name == "posix":
            # The data directory holds credentials, so its leaf is created
            # owner-only, like `~/.ssh`. Creation-time only — an existing
            # directory keeps whatever its owner gave it.
            assert stat.S_IMODE(footman.data_dir().stat().st_mode) == 0o700
    finally:
        _paths.configure_child(*before)


def test_the_cache_and_data_directories_must_differ(tmp_path, monkeypatch):
    # The collector deletes from the cache by age; pointed at the data
    # directory it would eventually delete credentials. Refuse, don't warn.
    same = tmp_path / "both"
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    app = App(name="acme", prog="acme", cache_dir=same, data_dir=same)
    result = Runner(app).invoke("--list")
    assert result.exit_code == EX_USAGE
    assert "must differ" in result.stderr
    assert "ACME_CACHE_DIR" in result.stderr and "ACME_DATA_DIR" in result.stderr


def test_footman_cache_dir_relocates_stock_footman(tmp_path, monkeypatch):
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "fmcache"))
    Runner(App(env_prefix="FOOTMAN")).invoke("--list")
    assert list((tmp_path / "fmcache").glob("*.json"))


def test_the_config_table_is_the_brands(tmp_path, monkeypatch):
    # Two branded CLIs in one repo read their own settings instead of
    # fighting over `[tool.footman]`. The `tasks` key makes the answer
    # visible: whichever table won names the file that got loaded.
    _project(
        tmp_path,
        '[tool.acme]\ntasks = "mine.py"\n[tool.footman]\ntasks = "theirs.py"\n',
    )
    (tmp_path / "mine.py").write_text(TASKS)
    (tmp_path / "theirs.py").write_text(
        'from livery.footman import task\n\n@task\ndef wrong():\n    "Not this one."\n'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    out = Runner(App(name="acme", prog="acme")).invoke("--list").stdout
    assert "ship" in out and "wrong" not in out


def test_the_dedicated_config_file_is_the_brands(tmp_path, monkeypatch):
    _project(tmp_path)
    (tmp_path / "acme.toml").write_text('tasks = "mine.py"\n')
    (tmp_path / "mine.py").write_text(TASKS)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    out = Runner(App(name="acme", prog="acme")).invoke("--list").stdout
    assert "ship" in out


def test_cascade_error_names_the_brands_variable(tmp_path, monkeypatch):
    # Naming FOOTMAN_CASCADE at an acme user teaches a variable that does
    # nothing for them.
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ACME_CASCADE", "sideways")
    result = Runner(App(name="acme", prog="acme")).invoke("ship")
    assert result.exit_code == EX_USAGE
    assert "ACME_CASCADE" in result.stderr and "FOOTMAN_CASCADE" not in result.stderr


def test_the_brands_tasks_file_marks_a_project_root(tmp_path, monkeypatch):
    # The mirror of `acme.toml` marking one. A project whose *only* marker is
    # the brand's tasks file — no pyproject.toml, no checkout, no acme.toml —
    # is the "Docker context with .git ignored" shape, and before this the
    # literal `tasks.py` in PROJECT_MARKERS meant an `acmetasks.py` root went
    # unrecognised and the cascade started in the wrong directory.
    before = _paths.child_args()
    try:
        _paths.configure(config_name="acme", tasks_file="acmetasks.py")
        (tmp_path / "acmetasks.py").write_text(TASKS)
        deep = tmp_path / "a" / "b"
        deep.mkdir(parents=True)
        assert _paths.find_project_root(deep) == tmp_path.resolve()
        assert "acmetasks.py" in _paths.project_markers()
        assert "tasks.py" not in _paths.project_markers()
    finally:
        _paths.configure_child(*before)


def test_a_brands_tasks_file_reaches_the_cascade_ceiling(tmp_path, monkeypatch):
    # End to end: the ceiling found above is what the cascade walks down from,
    # so a task defined at that root is visible from a subdirectory.
    (tmp_path / "acmetasks.py").write_text(TASKS)
    deep = tmp_path / "pkg"
    deep.mkdir()
    monkeypatch.chdir(deep)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    app = App(name="acme", prog="acme", tasks_file="acmetasks.py")
    assert "ship" in Runner(app).invoke("--list").stdout


def test_the_user_tasks_file_answers_where_a_project_has_none(tmp_path, monkeypatch):
    # Beside the user's config file, because both are the user's own writing
    # — not somewhere the brand placed.
    cfg, empty = tmp_path / "cfg", tmp_path / "empty"
    (cfg / "acme").mkdir(parents=True)
    empty.mkdir()
    (cfg / "acme" / "tasks.py").write_text(TASKS)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    monkeypatch.chdir(empty)
    out = Runner(App(name="acme", prog="acme")).invoke("--list").stdout
    assert "ship" in out


def test_a_projects_tasks_key_cannot_rename_the_users_own_file(tmp_path, monkeypatch):
    # The `tasks` key renames the *project's* file and stops there. It used
    # to steer the user rung too, so any project with a renamed tasks file
    # made the walk look for a personal file the user never wrote — and the
    # personal rung silently vanished.
    cfg = tmp_path / "cfg"
    (cfg / "acme").mkdir(parents=True)
    (cfg / "acme" / "tasks.py").write_text(TASKS)  # the user's own, default name
    project = tmp_path / "proj"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "p"\n\n[tool.acme]\ntasks = "chores.py"\n'
    )
    (project / "chores.py").write_text(
        'from livery.footman import task\n\n\n@task\ndef sweep():\n    "Sweep."\n'
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    monkeypatch.chdir(project)
    out = Runner(App(name="acme", prog="acme")).invoke("--list").stdout
    assert "sweep" in out  # the project's renamed file answers…
    assert "ship" in out  # …and the personal rung still rides along


def test_config_dir_moves_the_users_writing_together(tmp_path, monkeypatch):
    # `ACME_CONFIG_DIR` relocates the brand's config corner — config file and
    # user tasks file travel together. The narrow, brand-scoped alternative
    # to `XDG_CONFIG_HOME`, which would move every other application's config
    # along with it: a task runner relocates its own corner, not the desktop.
    identity = tmp_path / "identity-b"
    identity.mkdir()
    # The config file renames the tasks file; the tasks file defines `ship`.
    # `ship` listing proves BOTH were read from the relocated directory.
    (identity / "config.toml").write_text('tasks = "mine.py"\n')
    (identity / "mine.py").write_text(TASKS)
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("ACME_CONFIG_DIR", str(identity))
    monkeypatch.chdir(empty)
    out = Runner(App(name="acme", prog="acme")).invoke("--list").stdout
    assert "ship" in out


def test_the_config_file_variable_is_finer_than_the_dir(tmp_path, monkeypatch):
    # `ACME_CONFIG` names one file and wins over `ACME_CONFIG_DIR`'s
    # config.toml — the specific beats the general, same as everywhere else.
    d = tmp_path / "dir"
    d.mkdir()
    (d / "config.toml").write_text('tasks = "wrong.py"\n')
    finer = tmp_path / "finer.toml"
    finer.write_text('tasks = "mine.py"\n')
    (tmp_path / "mine.py").write_text(TASKS)
    (tmp_path / "wrong.py").write_text(
        'from livery.footman import task\n\n@task\ndef wrong():\n    "Not this one."\n'
    )
    monkeypatch.setenv("ACME_CONFIG_DIR", str(d))
    monkeypatch.setenv("ACME_CONFIG", str(finer))
    monkeypatch.chdir(tmp_path)
    out = Runner(App(name="acme", prog="acme")).invoke("--list").stdout
    assert "ship" in out and "wrong" not in out


def test_the_cache_and_config_directories_must_differ(tmp_path, monkeypatch):
    # Same reasoning as cache vs data: the collector deletes from the cache
    # by age, and the config dir holds the user's own files.
    same = tmp_path / "both"
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ACME_CONFIG_DIR", str(same))
    app = App(name="acme", prog="acme", cache_dir=same)
    result = Runner(app).invoke("--list")
    assert result.exit_code == EX_USAGE
    assert "must differ" in result.stderr
    assert "ACME_CACHE_DIR" in result.stderr and "ACME_CONFIG_DIR" in result.stderr


def test_the_user_rung_merges_and_the_project_shadows(tmp_path, monkeypatch):
    # The cascade's outermost rung: personal tasks ride into a project, and
    # a project task shadows a same-named one — project > user, the
    # nearest-wins reading the cascade always had, one rung further out.
    cfg = tmp_path / "cfg"
    (cfg / "acme").mkdir(parents=True)
    (cfg / "acme" / "tasks.py").write_text(
        'from livery.footman import task\n\n@task\ndef mine():\n    "Personal."\n\n'
        '@task\ndef ship():\n    "Shadowed by the project."\n'
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    out = Runner(App(name="acme", prog="acme")).invoke("--list").stdout
    assert "mine" in out  # the personal task is present in the project tree
    assert "Ship it." in out  # the project's ship wins the name
    assert "Shadowed by the project." not in out


def test_the_user_rung_claims_no_root(tmp_path, monkeypatch):
    # Outside a project, inv.root stays "" — footman invents no root — and
    # the cwd policies read the one-cwd rule: `root` exhausts the ladder to
    # the invocation directory, `taskfile` is the file's real home (the
    # config dir). The shipped fallback accidentally made the config dir the
    # root, which no personal task ever meant.
    import textwrap as _tw

    cfg, somewhere = tmp_path / "cfg", tmp_path / "somewhere"
    (cfg / "acme").mkdir(parents=True)
    somewhere.mkdir()
    (cfg / "acme" / "tasks.py").write_text(
        _tw.dedent(
            """
            import livery.footman as footman
            from livery.footman import pre_tasks, task

            @pre_tasks
            def show(inv):
                print(f"root={inv.root!r}")

            @task(cwd="root")
            def where_root():
                print(f"root-cwd={footman.cwd()}")

            @task(cwd="taskfile")
            def where_file():
                print(f"file-cwd={footman.cwd()}")
            """
        )
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    monkeypatch.chdir(somewhere)
    acme = Runner(App(name="acme", prog="acme"))
    rooted = acme.invoke("where-root")
    assert rooted.ok, rooted.stderr
    assert "root=''" in rooted.stdout
    assert f"root-cwd={somewhere.resolve()}" in rooted.stdout
    filed = acme.invoke("where-file")
    assert f"file-cwd={(cfg / 'acme').resolve()}" in filed.stdout


def test_builtin_tasks_answer_where_nothing_else_does(tmp_path, monkeypatch):
    # Global mode: no project, no user file — the brand's built-ins are the
    # tree. `footman.self` is a real installed entry point whose tasks
    # declare `expose="always"`, so this is the whole path: resolve,
    # mount, list.
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    acme = Runner(App(name="acme", prog="acme", builtin=["footman.self"]))
    out = acme.invoke("--list").stdout
    assert "self" in out


def test_no_builtin_and_no_files_keeps_todays_refusal(tmp_path, monkeypatch):
    # `builtin=()` is byte-identical to before the feature existed.
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    result = Runner(App(name="acme", prog="acme")).invoke("whatever")
    assert result.exit_code == EX_USAGE
    assert "no tasks file found" in result.stderr.lower()


def test_a_project_keeps_the_builtin_base_beneath_it(tmp_path, monkeypatch):
    # The base is the cascade's outermost rung, so a built-in is reachable
    # from inside a project too — it no longer vanishes at the doorstep,
    # which is what forced `plugin(...)` into every project that wanted one.
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    acme = Runner(App(name="acme", prog="acme", builtin=["footman.docs"]))
    out = acme.invoke("--list").stdout
    assert "ship" in out  # the project's own
    assert "docs" in out  # and the built-in, beneath it


def test_a_project_name_shadows_a_builtin_of_the_same_name(tmp_path, monkeypatch):
    # Nothing is privileged: the base is a rung, and everything nearer wins
    # by name exactly as the cascade already resolves its own rungs.
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(
        'from livery.footman import task\n\n@task\ndef docs():\n    "Ours, not theirs."\n'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    acme = Runner(App(name="acme", prog="acme", builtin=["footman.docs"]))
    result = acme.invoke("--help docs")
    assert result.ok, result.stderr
    assert "Ours, not theirs." in result.stdout


def test_the_user_rung_overlays_the_builtins(tmp_path, monkeypatch):
    # The bottom two rungs of project > user > built-in.
    cfg = tmp_path / "cfg"
    (cfg / "acme").mkdir(parents=True)
    (cfg / "acme" / "tasks.py").write_text(
        'from livery.footman import task\n\n@task\ndef mine():\n    "Personal."\n'
    )
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    monkeypatch.chdir(empty)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    acme = Runner(App(name="acme", prog="acme", builtin=["footman.self"]))
    out = acme.invoke("--list").stdout
    assert "mine" in out and "self" in out


def test_an_uninstalled_builtin_warns_naming_the_brand_and_carries_on(
    tmp_path, monkeypatch
):
    """The brand's family is installed by a sync; a refusal would refuse
    the sync too. So the family that did not mount is a warning with the
    remedy, and the families that did mount still answer."""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    acme = Runner(App(name="acme", prog="acme", builtin=["acme.nope", "footman.self"]))
    result = acme.invoke("--list")
    assert result.ok, result.stderr
    assert "acme declares built-in tasks from 'acme.nope'" in result.stderr
    assert "carrying on without them" in result.stderr
    assert "`acme sync`" in result.stderr
    assert "self" in result.stdout


def test_the_plugins_report_shows_built_in(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    acme = Runner(App(name="acme", prog="acme", builtin=["footman.docs"]))
    result = acme.invoke("--plugins")
    assert result.ok, result.stderr
    assert "built in" in result.stdout


def test_a_builtin_describes_itself_inside_a_project(tmp_path, monkeypatch):
    # In a project the built-in is not landed, so its line comes from the
    # tree it advertises — the brand vouches for importing its own — never
    # from the distribution summary repeated beside every sibling.
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    acme = Runner(App(name="acme", prog="acme", builtin=["footman.docs"]))
    result = acme.invoke("--plugins")
    assert result.ok, result.stderr
    assert "built in" in result.stdout
    # The tree's advertised help, not the distribution summary repeated
    # beside every sibling — one voice per row.
    assert "Generate markdown docs" in result.stdout


def test_a_plugin_with_no_tasks_still_reports_mounted(tmp_path, monkeypatch):
    # footman.profile lands hooks and an option, no tasks — the tree walk
    # sees nothing, and the report used to call it "(not mounted)" while its
    # contributions rode every run. The mount stamps every bucket now, and
    # the state is the plain word: nothing to say "at" about.
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(
        'from livery.footman import plugin\n\nplugin("footman.profile")\n'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    result = Runner(App(name="acme", prog="acme")).invoke("--plugins")
    assert result.ok, result.stderr
    assert "mounted" in result.stdout
    assert "Write the run as a profiler trace" in result.stdout
    assert "(not mounted)" in result.stdout  # env_files, genuinely unmounted


def test_a_family_mounted_piecemeal_speaks_with_its_own_voice(tmp_path, monkeypatch):
    # Two pieces of one family land as two top-most nodes; an arbitrary
    # member's docstring must not stand for its siblings, so the row speaks
    # with the family's advertised help instead.
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(
        "from livery.footman import plugin\n\n"
        'plugin("footman.docs.page")\nplugin("footman.docs.site")\n'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    result = Runner(App(name="acme", prog="acme")).invoke("--plugins")
    assert result.ok, result.stderr
    assert "mounted at page, site" in result.stdout
    assert "Generate markdown docs for this project's tasks" in result.stdout


def test_two_projectless_directories_share_one_manifest(tmp_path, monkeypatch):
    # Hash-keyed by the brand, never the cwd: cold once per brand version,
    # not once per directory — and the file bakes no cwd (the collector's
    # idle sweep owns it) but does bake the builtin names the refresh child
    # rebuilds from.
    import json as _json

    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / ".cache"))
    # Through the real door (`App.run` syncs manifests; the in-memory
    # `Runner` path deliberately writes nothing).
    app = App(name="acme", prog="acme", version="1.0", builtin=["footman.docs"])
    monkeypatch.chdir(a)
    assert app.run(["--list"]) == 0
    monkeypatch.chdir(b)
    assert app.run(["--list"]) == 0
    cache = tmp_path / ".cache" / "acme"
    manifests = [p for p in cache.glob("*.json") if not p.name.endswith(".times.json")]
    assert len(manifests) == 1
    assert manifests[0].name.startswith("global-")
    data = _json.loads(manifests[0].read_text(encoding="utf-8"))
    assert "cwd" not in data
    assert data["builtin"] == ["footman.docs"]


def test_the_brand_version_changes_the_global_key(tmp_path, monkeypatch):
    # An upgrade can change the tree, so the version is part of the key.
    empty = tmp_path / "e"
    empty.mkdir()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / ".cache"))
    monkeypatch.chdir(empty)
    one = App(name="acme", prog="acme", version="1.0", builtin=["footman.docs"])
    two = App(name="acme", prog="acme", version="2.0", builtin=["footman.docs"])
    assert one.run(["--list"]) == 0
    assert two.run(["--list"]) == 0
    cache = tmp_path / ".cache" / "acme"
    assert len(list(cache.glob("global-*.json"))) == 2


def test_tab_reads_the_global_manifest_outside_a_project(tmp_path, monkeypatch, capsys):
    # The hot path's one walk on a cwd-manifest miss: no project files means
    # the shared global manifest answers — the same file the run just wrote.
    empty = tmp_path / "e"
    empty.mkdir()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / ".cache"))
    monkeypatch.chdir(empty)
    app = App(name="acme", prog="acme", version="1.0", builtin=["footman.self"])
    assert app.run(["--list"]) == 0  # writes the global manifest, warm read
    capsys.readouterr()  # drop the listing; the completion output is the test
    from livery.footman._complete import complete_cli

    # `App.run` configured the brand's world and never restores (by design);
    # the autouse fixture restores after the test. The hot path walks once,
    # finds no project files, and serves the shared global manifest.
    assert complete_cli(["se"]) == 0
    assert "self" in capsys.readouterr().out


def test_a_user_tasks_cwd_root_means_the_project_it_landed_in(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    (cfg / "acme").mkdir(parents=True)
    (cfg / "acme" / "tasks.py").write_text(
        "from livery import footman\nfrom livery.footman import task\n\n"
        '@task(cwd="root")\ndef whereami():\n    print(f"at={footman.cwd()}")\n'
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    _project(tmp_path)
    sub = tmp_path / "sub"
    sub.mkdir()
    monkeypatch.chdir(sub)
    result = Runner(App(name="acme", prog="acme")).invoke("whereami")
    assert result.ok, result.stderr
    assert f"at={tmp_path.resolve()}" in result.stdout


def test_child_argv_carries_the_resolved_locations(tmp_path):
    # Children inherit the environment but not the brand, so they are told
    # where the folders are rather than re-deriving them wrongly.
    cache, data = tmp_path / "c", tmp_path / "d"
    before = _paths.child_args()
    try:
        _paths.configure(
            prefix="ACME",
            cache_dir=cache,
            data_dir=data,
            config_name="acme",
            tasks_file="acme.py",
        )
        assert _paths.child_args() == [
            "ACME",
            str(cache),
            str(data),
            "acme",
            "acme.py",
            "fm",
            "",
            "",
            "livery-footman",
        ]
        _paths.configure_child(*_paths.child_args())
        assert _paths.env_var("NO_GC") == "ACME_NO_GC"
        assert _paths.footman_cache_dir() == cache
        assert _paths.footman_data_dir() == data
        _paths.configure_child()  # empty words mean stock footman
        assert _paths.env_var("NO_GC") == "FOOTMAN_NO_GC"
        assert _paths.footman_cache_dir() == _paths.cache_home() / "footman"
    finally:
        _paths.configure_child(*before)


def test_a_brand_teaches_footmans_plugins_without_naming_a_distribution(tmp_path):
    """`--profile` and `--env-file` are the framework's, and every runner
    built on footman has footman installed — so a branded CLI teaches them
    whether or not it ever set `dist=`. Basic env loading and a profiler are
    exactly what someone reaches for first."""
    from livery.footman import _split

    (tmp_path / "tasks.py").write_text(
        "from livery.footman import task\n\n@task\ndef go(): ...\n"
    )
    _split._OWN_FLAGS.clear()
    acme = Runner(App(name="Acme", prog="acme", version="1.4.0"))
    result = acme.invoke("--env-file=.env go", cwd=tmp_path)
    assert result.exit_code == EX_USAGE
    assert result.stderr.startswith("acme: ")
    assert "--env-file comes from footman.env_files" in result.stderr
    assert 'add plugin("footman.env_files")' in result.stderr


def test_a_brand_that_names_its_distribution_teaches_its_own_plugins_too(tmp_path):
    """The case `dist=` unlocks: a distribution ships several plugins, a
    tasks file mounts some of them, and a flag from one of the others must
    not read as a spelling mistake. Nothing lists those flags — they are
    scanned from whatever `dist` names, so a brand's new plugin is taught the
    day it ships. (Standing in for a brand's own package here: footman's,
    named as if it were the brand's own.)"""
    from livery.footman import _split

    (tmp_path / "tasks.py").write_text(
        "from livery.footman import task\n\n@task\ndef go(): ...\n"
    )
    _split._OWN_FLAGS.clear()
    acme = Runner(App(name="Acme", prog="acme", version="1.4.0", dist="footman"))
    result = acme.invoke("--profile go", cwd=tmp_path)
    assert result.exit_code == EX_USAGE
    assert "--profile comes from footman.profile" in result.stderr


def test_a_brand_never_speaks_for_a_third_partys_flag(tmp_path):
    """Where it stops. A flag from a package neither footman nor the brand
    ships keeps the plain answer, because teaching it would mean importing,
    on a typo, code this project deliberately did not mount."""
    from livery.footman import _split

    (tmp_path / "tasks.py").write_text(
        "from livery.footman import task\n\n@task\ndef go(): ...\n"
    )
    _split._OWN_FLAGS.clear()
    acme = Runner(App(name="Acme", prog="acme", version="1.4.0", dist="acme-cli"))
    result = acme.invoke("--tf-workspace=prod go", cwd=tmp_path)
    assert result.exit_code == EX_USAGE
    assert "unknown global option --tf-workspace" in result.stderr


def test_an_invocation_puts_the_brand_back(tmp_path):
    """A real entry point runs one brand and never restores the module
    globals — the process *is* that CLI. A test process is the one place
    that isn't true, and `Runner` is documented as saving and restoring
    around each invocation. It did that for `_paths` and not for `_brand`,
    so whichever branded `Runner` ran first in an xdist worker silently
    decided what every later test saw."""
    from livery.footman import _app

    (tmp_path / "tasks.py").write_text(
        "from livery.footman import task\n\n@task\ndef go(): ...\n"
    )
    before = _app._brand
    Runner(App(name="Acme", prog="acme", version="1.4.0", dist="acme-cli")).invoke(
        "go", cwd=tmp_path
    )
    assert _app._brand is before


def test_a_branded_run_does_not_decide_what_the_next_one_teaches(tmp_path):
    """The leak with its consequence attached: an acme-branded invocation
    used to leave `dist="acme-cli"` behind, so the next caller — stock
    footman here — scanned a package it does not ship and taught nothing."""
    from livery.footman import _split

    (tmp_path / "tasks.py").write_text(
        "from livery.footman import task\n\n@task\ndef go(): ...\n"
    )
    Runner(App(name="Acme", prog="acme", version="1.4.0", dist="acme-cli")).invoke(
        "go", cwd=tmp_path
    )
    _split._OWN_FLAGS.clear()
    result = Runner().invoke("--env-file=.env go", cwd=tmp_path)
    assert result.exit_code == EX_USAGE
    assert result.stderr.startswith("fm: ")
    assert "--env-file comes from footman.env_files" in result.stderr


# --- expose: what survives outside a project ----------------------------------
#
# The bug this exists for is not a noisy listing. A project task invoked
# without a project *succeeded and lied* — `files` printing nothing as though
# the project had none, `coverage` reporting no stamp yet, both exiting 0.
# Listing and completion happen before any body or hook runs, so nothing
# downstream can filter them, and footman already owns "is there a project".

PERSONAL = (
    "from livery.footman import task\n\n"
    "@task\n"
    "def scratch():\n"
    '    "Rides everywhere."\n\n'
    '@task(expose="project_only")\n'
    "def sync_repo():\n"
    '    "Needs a checkout."\n'
)


def _user_rung(tmp_path: Path, monkeypatch, body: str = PERSONAL) -> Path:
    """A brand with a personal tasks file and a built-in set, standing in an
    empty directory."""
    cfg = tmp_path / "cfg"
    (cfg / "acme").mkdir(parents=True)
    (cfg / "acme" / "tasks.py").write_text(body, encoding="utf-8")
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    monkeypatch.chdir(empty)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    return empty


def test_a_personal_task_can_require_a_project(tmp_path, monkeypatch):
    """The user rung participates: someone can write a personal task that
    needs a checkout and have it stay out of the way everywhere else."""
    _user_rung(tmp_path, monkeypatch)
    acme = Runner(App(name="acme", prog="acme", builtin=["footman.self"]))
    out = acme.invoke("--list").stdout
    assert "scratch" in out
    assert "sync-repo" not in out


def test_a_personal_task_rides_everywhere_unless_it_says_otherwise(
    tmp_path, monkeypatch
):
    """The rung's promise, unchanged: silence means everywhere. A default of
    "needs a project" would have deleted every existing personal task from
    every project-less directory."""
    _user_rung(tmp_path, monkeypatch)
    out = Runner(App(name="acme", prog="acme")).invoke("--list").stdout
    assert "scratch" in out


def test_a_project_task_is_refused_by_name_not_404ed(tmp_path, monkeypatch):
    """The whole point of marking rather than dropping: "no task named" would
    be a lie — it exists, it has nowhere to stand. Whoever typed it is most
    often a tool in the wrong directory, so the answer says where it looked."""
    empty = _user_rung(tmp_path, monkeypatch)
    result = Runner(App(name="acme", prog="acme")).invoke("sync-repo")
    assert result.exit_code == EX_USAGE
    assert "sync-repo needs a project" in result.stderr
    assert "no tasks.py found here" in result.stderr
    assert str(empty) in result.stderr


def test_a_task_that_needs_a_project_is_never_suggested(tmp_path, monkeypatch):
    """A did-you-mean is a thing to try next. Proposing a name that can only
    refuse is worse than proposing nothing."""
    _user_rung(tmp_path, monkeypatch)
    result = Runner(App(name="acme", prog="acme")).invoke("sync-rep")
    assert "sync-repo" not in result.stderr


def test_inside_a_project_nothing_is_hidden(tmp_path, monkeypatch):
    """Both answers include being inside a project, which is why the question
    only arises outside one — and why no rung needs a rule of its own."""
    cfg = tmp_path / "cfg"
    (cfg / "acme").mkdir(parents=True)
    (cfg / "acme" / "tasks.py").write_text(PERSONAL, encoding="utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname='x'\n")
    (proj / "tasks.py").write_text(
        "from livery.footman import task\n\n@task\ndef build():\n    'Build.'\n"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    out = Runner(App(name="acme", prog="acme")).invoke("--list", cwd=proj).stdout
    assert "scratch" in out and "sync-repo" in out and "build" in out


def test_a_brands_builtins_need_a_project_unless_they_say_otherwise(
    tmp_path, monkeypatch
):
    """The built-in set's default is the opposite of the user rung's, and each
    is that rung's own promise: a package declared `builtin=` exposes nothing
    outside a project until a task says it makes sense there. `footman.docs`
    builds a project's docs; `footman.self` manages the runner itself."""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    acme = Runner(
        App(name="acme", prog="acme", builtin=["footman.docs", "footman.self"])
    )
    out = acme.invoke("--list").stdout
    assert "self" in out
    assert "docs" not in out


def test_a_group_answers_for_its_subtree(tmp_path, monkeypatch):
    """One line covers a subtree, and a child can still say otherwise — the
    same tri-state `hidden` has."""
    body = (
        "from livery.footman import group, task\n\n"
        'ci = group("ci", expose="project_only")\n\n'
        "@ci.task\n"
        "def lint():\n"
        '    "Lint."\n\n'
        '@ci.task(expose="always")\n'
        "def version():\n"
        '    "Print the version."\n'
    )
    _user_rung(tmp_path, monkeypatch, body)
    out = Runner(App(name="acme", prog="acme")).invoke("--list").stdout
    assert "ci.lint" not in out
    assert "ci.version" in out


def test_completion_never_spells_out_what_cannot_run(tmp_path, monkeypatch):
    """Completion is the other half: listing is prose a human reads, but a
    spelled-out address is an offer, and this one footman cannot honour."""
    _user_rung(tmp_path, monkeypatch)
    Runner(App(name="acme", prog="acme")).invoke("--list")  # warm the manifest
    from livery.footman._complete import complete
    from livery.footman._manifest import build_manifest

    reg = registry.Group("root")

    @reg.task(expose="project_only")
    def deploy(): ...

    @reg.task
    def scratch(): ...

    tree = build_manifest(reg, project=False)["tree"]
    offered = {c.split("\t", 1)[0] for c in complete(tree, [""])}
    assert offered == {"scratch"}


# --- expose: the third answer, and the axis's own rules ------------------------


def test_global_only_is_withheld_inside_a_project():
    """The answer the boolean could not express. `new` means nothing inside a
    checkout, and until now nothing could say so — the axis only ever asked
    about being *outside* one."""
    from livery.footman._manifest import build_manifest

    reg = registry.Group("root")

    @reg.task(expose="global_only")
    def new(): ...

    @reg.task(expose="project_only")
    def deploy(): ...

    @reg.task(expose="always")
    def whoami(): ...

    inside = build_manifest(reg, project=True)["tree"]["tasks"]
    assert inside["new"]["unexposed"] is True  # withheld the other way
    assert "unexposed" not in inside["deploy"]
    assert "unexposed" not in inside["whoami"]

    outside = build_manifest(reg, project=False)["tree"]["tasks"]
    assert "unexposed" not in outside["new"]
    assert outside["deploy"]["unexposed"] is True
    assert "unexposed" not in outside["whoami"]


def test_a_global_only_task_refuses_by_name_inside_a_project(tmp_path, monkeypatch):
    # Refused for the true reason, in the direction the refusal actually
    # came from — never a "no task named", because the task does exist.
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(
        "from livery.footman import task\n\n"
        '@task(expose="global_only")\n'
        "def bootstrap():\n"
        '    """Set up a new project."""\n',
        encoding="utf-8",
    )
    result = Runner().invoke("bootstrap", cwd=tmp_path)
    assert not result.ok
    assert "bootstrap runs only outside a project" in result.stderr
    assert "no task named" not in result.stderr
    assert "bootstrap" not in Runner().invoke("--list", cwd=tmp_path).stdout


def test_expose_refuses_a_value_that_is_not_one_of_the_three():
    reg = registry.Group("root")
    with pytest.raises(registry.RegistrationError, match="expose='projectonly'"):

        @reg.task(expose="projectonly")
        def typo(): ...

    with pytest.raises(registry.RegistrationError, match="always"):
        registry.expose("nowhere")


def test_the_decorator_and_the_parameter_say_the_same_thing():
    reg = registry.Group("root")

    @reg.task
    @registry.expose("global_only")
    def decorated(): ...

    @reg.task(expose="global_only")
    def parameterised(): ...

    assert registry.declared_expose(decorated) == "global_only"
    assert registry.declared_expose(parameterised) == "global_only"


def test_sealing_claims_only_what_never_answered():
    # The rung's promise applies to silence, never over a declaration —
    # and a group's answer covers its subtree the way `hidden` does.
    reg = registry.Group("root")

    @reg.task
    def unmarked(): ...

    @reg.task(expose="always")
    def spoke(): ...

    inner = reg.group("inner", expose="global_only")

    @inner.task
    def under_a_group(): ...

    registry.seal_expose(reg)  # the built-in rung's promise
    assert registry.declared_expose(unmarked) == "project_only"
    assert registry.declared_expose(spoke) == "always"
    assert registry.declared_expose(under_a_group) == "global_only"


def test_dash_f_still_means_no_base(tmp_path, monkeypatch):
    """`-f` is total control: one file, no cascade — and so no built-ins
    either, which is the same promise it always made."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "only.py").write_text(
        'from livery.footman import task\n\n@task\ndef solo():\n    "Just me."\n'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    acme = Runner(App(name="acme", prog="acme", builtin=["footman.self"]))
    out = acme.invoke("-f=only.py --list").stdout
    assert "solo" in out
    assert "self" not in out


# --- generated help text speaks the reader's brand ----------------------------
#
# Not cosmetics: a runner reads `[tool.<its own stem>]` and nothing else, so
# a branded CLI whose docs say `[tool.footman]` documents a table that runner
# never opens — settings someone writes that silently do nothing.

# Entry-point names are identifiers every brand's providers advertise under,
# including a branded CLI's own. They are not footman-the-product appearing
# where a brand belongs, and substituting them would break real mounts.
_BRAND_NEUTRAL = ("footman.tasks", "footman.builtin", "footman.self")


def _unbranded(text: str) -> list[str]:
    """Lines still naming footman after the identifiers are set aside."""
    stripped = text
    for identifier in _BRAND_NEUTRAL:
        stripped = stripped.replace(identifier, "«ep»")
    return [
        line
        for line in stripped.splitlines()
        if "footman" in line or "FOOTMAN" in line or "`fm " in line
    ]


def _as_acme(monkeypatch):
    from livery.footman import _paths

    monkeypatch.setattr(_paths, "_prog", "acme", raising=False)
    monkeypatch.setattr(_paths, "_config_name", "acme", raising=False)
    monkeypatch.setattr(_paths, "_prefix", "ACME", raising=False)


def test_the_config_table_speaks_the_brand(monkeypatch):
    from livery.footman import markdown

    _as_acme(monkeypatch)
    table = markdown.config_table()
    assert "[tool.acme.notes]" in table  # the table this runner actually reads
    assert "`acme self.*`" in table  # the command someone types
    assert "ACME_CASCADE" in table  # the variable that exists for them
    assert "acme holds no opinion" in table  # and the product's own name
    assert not _unbranded(table), _unbranded(table)


def test_the_notes_table_speaks_the_brand(monkeypatch):
    from livery.footman import markdown

    _as_acme(monkeypatch)
    table = markdown.notes_table()
    assert "acme scoped the write" in table
    assert not _unbranded(table), _unbranded(table)


def test_stock_footman_still_says_footman():
    # The substitution is per brand, not a rename: stock's own words are
    # right for stock, and `footman` is exactly the longer name it pins
    # against the two-letter command for prose like this.
    from livery.footman import markdown

    table = markdown.config_table()
    assert "[tool.footman.notes]" in table
    assert "footman holds no opinion" in table
    assert "FOOTMAN_CASCADE" in table


def test_the_refusals_name_the_brands_own_table(monkeypatch):
    from livery.footman import _describe, _notes

    _as_acme(monkeypatch)
    assert "[tool.acme]" in (_notes.validate("not a table") or "")
    assert "[tool.acme.notes]" in (_notes.validate({"getcwd": "loud"}) or "")
    assert "[tool.acme.notes]" in (_notes.validate({"nosuchkind": "info"}) or "")
    assert "[tool.acme]" in (_describe.docs_url_error("") or "")


# --- the brand's own words, for code that generates text ----------------------


def test_prog_and_dist_follow_the_installed_brand():
    """A plugin generating a CI workflow has to emit the command its reader
    will type — a branded runner writing `fm check` tells them to run
    something they do not have."""
    import livery.footman as footman

    App(prog="acme", dist="acme-cli").brand.install()
    assert footman.prog() == "acme"
    assert footman.dist() == "acme-cli"

    DEFAULT_BRAND.install()
    assert footman.prog() == "fm"
    assert footman.dist() == "livery-footman"


def test_dist_is_none_when_the_brand_never_declared_one():
    # `App(dist=…)` is opt-in, so "footman" would name the wrong package.
    # The lock rule's own reading falls back on purpose; this one must not.
    import livery.footman as footman

    App(prog="bare").brand.install()
    assert footman.prog() == "bare"
    assert footman.dist() is None
    DEFAULT_BRAND.install()


def test_a_task_reads_the_same_name_from_its_context(tmp_path, monkeypatch):
    # Inside a task the context is the better answer — the invocation's
    # own, not process state — and the two must agree.
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(
        "from livery import footman\nfrom livery.footman import Context, task\n\n"
        "@task\n"
        "def whoami(ctx: Context):\n"
        '    """Say the brand."""\n'
        "    print(f'{ctx.prog} {footman.prog()}')\n"
    )
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    result = Runner(App(prog="acme")).invoke("whoami", cwd=tmp_path)
    assert result.ok, result.stderr
    assert "acme acme" in result.stdout
