"""The user-level `builtin` key: the ladder, outside a project and inside one."""

from __future__ import annotations

import json
import sys
import textwrap

import pytest

from livery.footman import _config, _paths
from livery.footman.app import App
from livery.footman.testing import Runner

# A distribution that ships a `footman.tasks` entry point, installed into the
# runner's own environment. Faked at the entry-point layer: what matters is
# the ladder, not importlib.metadata's plumbing.
#
# `login` says `expose="always"` because the built-in rung defaults the
# other way — a package's tasks expose nothing outside a project until one
# says it makes sense there, which is exactly the opt-in this ladder is for.
PROVIDER = textwrap.dedent(
    '''
    from livery.footman import task

    @task(expose="always")
    def login():
        """Log in to the service."""
        print("logged in")

    @task
    def deploy():
        """Deploy this project."""
        print("deployed")

    @task(expose="global_only")
    def bootstrap():
        """Start a project. Meaningless once you have one."""
        print("bootstrapped")
    '''
)

# Stock `fm`'s own shape: a brand that declares built-ins of its own, so the
# tests can watch the user's set land *beside* them rather than instead.
STOCK = ("footman.self",)


def stock_runner():
    return Runner(App(dist="footman", builtin=STOCK))


@pytest.fixture
def provider(tmp_path, monkeypatch):
    """`acme.tasks` importable, and advertised as an installed entry point."""
    pkg = tmp_path / "site"
    pkg.mkdir()
    (pkg / "acme_tasks.py").write_text(PROVIDER, encoding="utf-8")
    monkeypatch.syspath_prepend(str(pkg))
    monkeypatch.delitem(sys.modules, "acme_tasks", raising=False)

    class FakeEP:
        name = "acme_tasks"
        dist = None

        def load(self):
            # By name, not a static import: the module is written into a
            # tmp dir at fixture time, so no checker could resolve it.
            import importlib

            return importlib.import_module("acme_tasks")

    import importlib.metadata

    from livery.footman import compose

    real = importlib.metadata.entry_points

    def fake_entry_points(group=None):
        # The real set plus ours: footman's own entry points have to stay
        # visible, or the brand's built-ins stop mounting mid-test.
        found = list(real(group=group)) if group else []
        return [*found, FakeEP()] if group == compose.ENTRY_POINT_GROUP else found

    monkeypatch.setattr(importlib.metadata, "entry_points", fake_entry_points)
    return tmp_path


@pytest.fixture
def user_config(tmp_path, monkeypatch):
    """A user-level config file this test owns."""
    path = tmp_path / "user.toml"
    monkeypatch.setenv("FOOTMAN_CONFIG", str(path))
    monkeypatch.setenv("FOOTMAN_CONFIG_DIR", str(tmp_path / "cfgdir"))
    return path


# --- the three sources and the four modes -------------------------------------


def _discovered(names):
    """Stand in for what `fm self.*` writes for *this* environment."""
    _config.write_discovered(names, sys.prefix)


def test_the_user_list_is_yours_alone(user_config, provider):
    assert _config.user_builtin() == ()  # absent means empty, not unset
    user_config.write_text("[builtins]\nuser = []\n", encoding="utf-8")
    assert _config.user_builtin() == ()
    user_config.write_text('[builtins]\nuser = ["acme_tasks"]\n', encoding="utf-8")
    assert _config.user_builtin() == ("acme_tasks",)


def test_a_user_list_that_is_not_a_list_of_names_is_refused(user_config):
    user_config.write_text('[builtins]\nuser = "acme_tasks"\n', encoding="utf-8")
    with pytest.raises(_config.BuiltinError, match="a list of"):
        _config.user_builtin()
    user_config.write_text("[builtins]\nuser = [1, 2]\n", encoding="utf-8")
    with pytest.raises(_config.BuiltinError):
        _config.user_builtin()


def test_the_discovered_list_lives_in_the_data_dir(user_config, tmp_path, monkeypatch):
    # Machine-owned by *location*, so "don't hand-edit this" is true by
    # construction rather than by comment — and the config file stays yours.
    monkeypatch.setenv("FOOTMAN_DATA_DIR", str(tmp_path / "data"))
    assert _config.discovered_builtin() == ()  # nothing discovered yet
    _discovered(["acme_tasks"])
    assert _config.discovered_path().parent == tmp_path / "data"
    assert _config.discovered_builtin() == ("acme_tasks",)


def test_an_unreadable_discovered_list_is_simply_empty(tmp_path, monkeypatch):
    # A record footman can always rebuild, never a declaration whose loss
    # should refuse a run.
    monkeypatch.setenv("FOOTMAN_DATA_DIR", str(tmp_path / "data"))
    _config.discovered_path().parent.mkdir(parents=True, exist_ok=True)
    _config.discovered_path().write_text("{not json", encoding="utf-8")
    assert _config.discovered_builtin() == ()


def test_the_discovered_list_is_keyed_by_environment(tmp_path, monkeypatch):
    # What it records — which entry points are importable — is a property
    # of one Python environment, not of the machine. Keyed by brand alone,
    # the record a `self.install` wrote from the *tool* environment was
    # applied by every other footman on the machine: a project venv read
    # it, could not import a package never installed there, and refused.
    monkeypatch.setenv("FOOTMAN_DATA_DIR", str(tmp_path / "data"))
    _discovered(["acme_tasks"])
    assert _config.discovered_builtin() == ("acme_tasks",)

    # The same data directory, a different environment: a different entry,
    # so one environment's record can never answer for another's.
    other = _config.discovered_path("/somewhere/else/venv")
    assert other != _config.discovered_path()
    assert other.parent == _config.discovered_path().parent
    monkeypatch.setattr(_config.sys, "prefix", "/somewhere/else/venv")
    assert _config.discovered_builtin() == ()


def test_an_unkeyed_legacy_record_is_simply_not_found(tmp_path, monkeypatch):
    # A record written before the key existed names no environment, and
    # applying it to every environment is the bug. Not-found already means
    # "nothing discovered yet", so one `self.install` writes the keyed one.
    monkeypatch.setenv("FOOTMAN_DATA_DIR", str(tmp_path / "data"))
    legacy = _paths.footman_data_dir() / "builtins.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps({"schema": 1, "builtin": ["stale"]}), encoding="utf-8")
    assert _config.discovered_builtin() == ()


def test_discovery_mode_defaults_to_auto_and_refuses_a_typo(user_config):
    assert _config.discovery_mode() == "auto"
    user_config.write_text('[builtins]\ndiscovery_mode = "manual"\n', encoding="utf-8")
    assert _config.discovery_mode() == "manual"
    user_config.write_text('[builtins]\ndiscovery_mode = "auto_"\n', encoding="utf-8")
    with pytest.raises(_config.BuiltinError, match="one of"):
        _config.discovery_mode()


def test_the_modes_select_which_sources_contribute(user_config, tmp_path, monkeypatch):
    monkeypatch.setenv("FOOTMAN_DATA_DIR", str(tmp_path / "data"))
    _discovered(["discovered_pkg"])
    brand = ("footman.self",)

    def mode(name):
        user_config.write_text(
            f'[builtins]\ndiscovery_mode = "{name}"\nuser = ["mine"]\n',
            encoding="utf-8",
        )
        return _config.effective_builtin(brand)

    # The brand's own always leads; `user` is honoured in every mode.
    assert mode("auto") == ("footman.self", "discovered_pkg", "mine")
    assert mode("manual") == ("footman.self", "discovered_pkg", "mine")
    assert mode("internal") == ("footman.self", "mine")
    assert mode("none") == ("mine",)  # nothing automatic, only what I named


def test_none_leaves_a_way_back_in(user_config, tmp_path, monkeypatch):
    # `none` is not "off": naming the runner's own group restores it, which
    # is what keeps the mode from locking anyone out of `fm self.*`.
    monkeypatch.setenv("FOOTMAN_DATA_DIR", str(tmp_path / "data"))
    user_config.write_text(
        '[builtins]\ndiscovery_mode = "none"\nuser = ["footman.self"]\n',
        encoding="utf-8",
    )
    assert _config.effective_builtin(("footman.self",)) == ("footman.self",)


def test_a_name_is_never_mounted_twice(user_config, tmp_path, monkeypatch):
    monkeypatch.setenv("FOOTMAN_DATA_DIR", str(tmp_path / "data"))
    _discovered(["footman.self"])
    user_config.write_text('[builtins]\nuser = ["footman.self"]\n', encoding="utf-8")
    assert _config.effective_builtin(("footman.self",)) == ("footman.self",)


# --- the ladder, end to end ---------------------------------------------------


@pytest.fixture
def bare(tmp_path, monkeypatch):
    """A directory with no project in sight."""
    where = tmp_path / "bare"
    where.mkdir()
    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "cache"))
    return where


def test_a_configured_builtin_answers_outside_a_project(user_config, provider, bare):
    user_config.write_text('[builtins]\nuser = ["acme_tasks"]\n', encoding="utf-8")
    result = stock_runner().invoke("login", cwd=bare)
    assert result.ok, result.stderr
    assert "logged in" in result.stdout


def test_it_lists_beside_the_brands_own(user_config, provider, bare):
    user_config.write_text('[builtins]\nuser = ["acme_tasks"]\n', encoding="utf-8")
    result = stock_runner().invoke("--list", cwd=bare)
    assert result.ok, result.stderr
    assert "login" in result.stdout
    assert "self" in result.stdout  # stock footman's own built-in stands


def test_project_only_still_refuses_outside_one(user_config, provider, bare):
    # The rung is unchanged: a task that declares it needs a project is
    # mounted-but-refused out here, by name, rather than missing.
    user_config.write_text('[builtins]\nuser = ["acme_tasks"]\n', encoding="utf-8")
    result = stock_runner().invoke("deploy", cwd=bare)
    assert not result.ok
    assert "project" in result.stderr.lower()


def test_a_project_keeps_the_configured_set_beneath_it(user_config, provider, tmp_path):
    # The built-in set is the cascade's outermost rung, so it follows you
    # into a project — and `expose` decides what that offers there. `login`
    # said `always`, so it stands; `deploy` never spoke, so the rung's own
    # promise (`project_only`) applies and it belongs here too.
    project = tmp_path / "proj"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='x'\n")
    (project / "tasks.py").write_text(
        'from footman import task\n\n@task\ndef build():\n    """Build."""\n'
    )
    user_config.write_text('[builtins]\nuser = ["acme_tasks"]\n', encoding="utf-8")
    result = stock_runner().invoke("--list", cwd=project)
    assert result.ok, result.stderr
    assert "build" in result.stdout  # the project's own
    assert "login" in result.stdout  # and the configured built-in
    assert "deploy" in result.stdout  # which needed a project all along


def test_a_name_that_will_not_mount_blames_the_config(user_config, provider, bare):
    user_config.write_text(
        '[builtins]\nuser = ["not_installed_anywhere"]\n', encoding="utf-8"
    )
    result = stock_runner().invoke("--list", cwd=bare)
    assert not result.ok
    assert "builtins.user" in result.stderr
    assert "not_installed_anywhere" in result.stderr


def test_a_discovered_name_that_will_not_mount_is_skipped_not_refused(
    user_config, provider, bare
):
    # Discovery writes this list and can rebuild it, which is already why a
    # missing or malformed one means "nothing discovered" rather than an
    # error. An entry that no longer resolves is the same thing, one entry
    # at a time — so the runner says so and carries on.
    _discovered(["gone_from_this_environment"])
    result = stock_runner().invoke("--list", cwd=bare)
    assert result.ok  # the runner still runs
    assert "gone_from_this_environment" in result.stderr
    assert "skipping it" in result.stderr


def test_one_stale_discovered_name_does_not_take_the_others_with_it(
    user_config, provider, bare
):
    # Skipping is per name. Refusing was all-or-nothing, and it took the
    # remedy with it: the message says to run `self.add`, and `self.add`
    # mounts this same base on its way in — so the way out refused too,
    # and the only fix left was deleting the record by hand.
    _discovered(["gone_from_this_environment", "acme_tasks"])
    result = stock_runner().invoke("--list", cwd=bare)
    assert result.ok
    assert "gone_from_this_environment" in result.stderr  # named, and skipped
    assert "login" in result.stdout  # the one that does resolve still mounts


def test_a_broken_key_is_refused_before_anything_runs(user_config, provider, bare):
    user_config.write_text('[builtins]\nuser = "acme_tasks"\n', encoding="utf-8")
    result = stock_runner().invoke("--list", cwd=bare)
    assert not result.ok
    assert "builtin" in result.stderr


def test_the_key_is_user_level_only(user_config, provider, tmp_path):
    # In a project config it is ignored, with the advisory `-v` carries —
    # what a machine offers outside every project is the machine's business.
    project = tmp_path / "proj"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        "[project]\nname='x'\n[tool.footman.builtins]\nuser = ['acme_tasks']\n"
    )
    (project / "tasks.py").write_text(
        'from footman import task\n\n@task\ndef build():\n    """Build."""\n'
    )
    result = stock_runner().invoke("-v --list", cwd=project)
    assert result.ok, result.stderr
    assert "user-level" in result.stderr
    assert "login" not in result.stdout


def test_plugins_reports_which_rung_declared_it(user_config, provider, bare):
    user_config.write_text('[builtins]\nuser = ["acme_tasks"]\n', encoding="utf-8")
    result = stock_runner().invoke("--plugins", cwd=bare)
    assert result.ok, result.stderr
    assert "built in (your config)" in result.stdout


def test_the_json_catalog_carries_the_configured_tasks(user_config, provider, bare):
    user_config.write_text('[builtins]\nuser = ["acme_tasks"]\n', encoding="utf-8")
    result = stock_runner().invoke("--json", cwd=bare)
    assert result.ok, result.stderr
    catalog = json.loads(result.stdout)
    assert "login" in catalog["tree"]["tasks"]


def test_the_completion_child_builds_the_configured_set(user_config, provider, bare):
    # TAB must describe the tree the run serves: the background rebuild
    # mounts the configured set exactly as the execution path does.
    from livery.footman import _refresh

    user_config.write_text('[builtins]\nuser = ["acme_tasks"]\n', encoding="utf-8")
    _paths.configure(builtin=("footman.self",))
    monkeypatch_cwd = bare
    import os

    saved = os.getcwd()
    os.chdir(monkeypatch_cwd)
    try:
        _refresh.refresh_cwd(*_paths.child_args())
    finally:
        os.chdir(saved)
    built = _paths.global_manifest_path()
    assert built.is_file()
    tree = json.loads(built.read_text(encoding="utf-8"))["tree"]
    assert "login" in tree["tasks"]  # the user's own
    assert "self" in tree["groups"]  # and the brand's, still


@pytest.fixture
def acme(tmp_path, monkeypatch, user_config):
    """A branded CLI that declares no built-ins of its own — and its own
    user-level world, since a brand reads `ACME_CONFIG`, never footman's."""
    monkeypatch.setenv("ACME_CONFIG", str(user_config))
    monkeypatch.setenv("ACME_CONFIG_DIR", str(tmp_path / "cfgdir"))
    monkeypatch.setenv("ACME_CACHE_DIR", str(tmp_path / "cache"))
    return App(prog="acme")


def test_a_brand_with_no_builtins_of_its_own_still_gets_the_users(
    user_config, provider, bare, acme
):
    # The branded half: a CLI that declares no built-ins is exactly where
    # the key does the most work — the user's set is the whole base.
    user_config.write_text('[builtins]\nuser = ["acme_tasks"]\n', encoding="utf-8")
    result = Runner(acme).invoke("login", cwd=bare)
    assert result.ok, result.stderr
    assert "logged in" in result.stdout


def test_tab_reaches_a_configured_set_under_such_a_brand(
    user_config, provider, bare, acme
):
    # The hot path cannot read config (a TOML parse per keystroke), and this
    # brand declares no built-ins of its own — so the manifest a real run
    # left behind is what tells TAB there is a global tree to serve.
    import os

    from livery.footman._complete import complete_cli

    user_config.write_text('[builtins]\nuser = ["acme_tasks"]\n', encoding="utf-8")
    assert Runner(acme).invoke("--list", cwd=bare).ok  # writes the global manifest

    saved = os.getcwd()
    os.chdir(bare)
    _paths.configure(prefix="ACME", prog="acme", builtin=())
    try:
        assert complete_cli(["--", ""]) == 0
    finally:
        os.chdir(saved)


def test_global_only_keeps_a_builtin_out_of_a_project(user_config, provider, tmp_path):
    """What makes joining the cascade safe. A built-in that only makes sense
    before a project exists says so — the rung no longer decides that for it
    by accident of where it was mounted."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='x'\n")
    (project / "tasks.py").write_text(
        'from footman import task\n\n@task\ndef build():\n    """Build."""\n'
    )
    user_config.write_text('[builtins]\nuser = ["acme_tasks"]\n', encoding="utf-8")

    inside = stock_runner().invoke("--list", cwd=project)
    assert inside.ok, inside.stderr
    assert "bootstrap" not in inside.stdout
    refused = stock_runner().invoke("bootstrap", cwd=project)
    assert not refused.ok
    assert "runs only outside a project" in refused.stderr

    bare_dir = tmp_path / "elsewhere"
    bare_dir.mkdir()
    outside = stock_runner().invoke("bootstrap", cwd=bare_dir)
    assert outside.ok, outside.stderr
    assert "bootstrapped" in outside.stdout
