"""The project rung: the built-ins a project's direct dependencies offer."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from livery.footman.app import App
from livery.footman.testing import Runner

# Two installed providers: `acme-direct`, which the project names, and
# `acme-deep`, installed only because `acme-direct` depends on it.
DIRECT = textwrap.dedent(
    '''
    from livery.footman.api import task

    @task
    def hello():
        """Greet from the plugin."""
        print("plugin hello")

    @task
    def direct():
        """A verb the direct dependency offers."""
        print("direct ran")
    '''
)
DEEP = textwrap.dedent(
    '''
    from livery.footman.api import task

    @task
    def deep():
        """A verb only a transitive dependency offers."""
        print("deep ran")
    '''
)


def runner() -> Runner:
    return Runner(App(dist="footman", builtin=("footman.self",)))


@pytest.fixture
def providers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Both providers importable, advertised in both entry point groups."""
    site = tmp_path / "site"
    site.mkdir()
    (site / "acme_direct.py").write_text(DIRECT, encoding="utf-8")
    (site / "acme_deep.py").write_text(DEEP, encoding="utf-8")
    monkeypatch.syspath_prepend(str(site))
    for name in ("acme_direct", "acme_deep"):
        monkeypatch.delitem(sys.modules, name, raising=False)

    class FakeEP:
        def __init__(self, name: str, dist: str) -> None:
            self.name = name
            self.value = name
            self.dist = SimpleNamespace(name=dist, metadata=None, version="1.0")

        def load(self) -> object:
            import importlib

            return importlib.import_module(self.name)

    import importlib.metadata

    from livery.footman import compose

    fakes = [FakeEP("acme_direct", "acme-direct"), FakeEP("acme_deep", "acme-deep")]
    real = importlib.metadata.entry_points

    def fake_entry_points(group: str | None = None) -> list[object]:
        found: list[object] = list(real(group=group)) if group else []
        if group in (compose.ENTRY_POINT_GROUP, "footman.builtin"):
            return [*found, *fakes]
        return found

    monkeypatch.setattr(importlib.metadata, "entry_points", fake_entry_points)
    monkeypatch.setenv("FOOTMAN_CONFIG", str(tmp_path / "user.toml"))
    monkeypatch.setenv("FOOTMAN_CONFIG_DIR", str(tmp_path / "cfgdir"))
    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "cache"))
    return tmp_path


def _project(root: Path, pyproject: str, tasks: str = "") -> Path:
    project = root / "proj"
    project.mkdir()
    (project / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    (project / "tasks.py").write_text(
        tasks
        or 'from livery.footman.api import task\n\n@task\ndef build():\n    """Build."""\n',
        encoding="utf-8",
    )
    return project


# The refusals and exclusions first.


def test_a_transitive_builtin_never_mounts(providers: Path) -> None:
    project = _project(
        providers, '[project]\nname = "x"\ndependencies = ["acme-direct>=1"]\n'
    )
    listed = runner().invoke("--list", cwd=project)
    assert listed.ok, listed.stderr
    assert "direct" in listed.stdout
    assert "deep" not in listed.stdout
    refused = runner().invoke("deep", cwd=project)
    assert not refused.ok


def test_a_dependency_group_counts_as_a_direct_dependency(providers: Path) -> None:
    project = _project(
        providers,
        '[project]\nname = "x"\n\n[dependency-groups]\ndev = ["acme_Direct"]\n',
    )
    result = runner().invoke("direct", cwd=project)
    assert result.ok, result.stderr
    assert "direct ran" in result.stdout


def test_builtin_exclude_names_the_exclusion(providers: Path) -> None:
    project = _project(
        providers,
        '[project]\nname = "x"\ndependencies = ["acme-direct"]\n\n'
        '[tool.footman]\nbuiltin-exclude = ["acme_direct"]\n',
    )
    listed = runner().invoke("--list", cwd=project)
    assert listed.ok, listed.stderr
    assert "direct" not in listed.stdout
    plugins = runner().invoke("--plugins", cwd=project)
    assert plugins.ok, plugins.stderr
    assert "acme_direct" in plugins.stdout
    assert "excluded by builtin-exclude" in plugins.stdout
    # A value that is not a list of names refuses, naming the key.
    (project / "pyproject.toml").write_text(
        '[project]\nname = "x"\n\n[tool.footman]\nbuiltin-exclude = "acme_direct"\n'
    )
    bad = runner().invoke("--list", cwd=project)
    assert not bad.ok
    assert "builtin-exclude must be a list" in bad.stderr


# Then the rung itself.


def test_the_plugins_report_names_the_project_rung(providers: Path) -> None:
    project = _project(
        providers, '[project]\nname = "x"\ndependencies = ["acme-direct"]\n'
    )
    plugins = runner().invoke("--plugins", cwd=project)
    assert plugins.ok, plugins.stderr
    assert "built in (a project dependency)" in plugins.stdout


def test_a_root_task_shadows_a_plugin_task_and_inherits_it(providers: Path) -> None:
    project = _project(
        providers,
        '[project]\nname = "x"\ndependencies = ["acme-direct"]\n',
        tasks=textwrap.dedent(
            '''
            from livery.footman.api import inherited, task

            @task
            def hello():
                """Greet from the project, then the plugin."""
                print("project hello")
                inherited()()
            '''
        ),
    )
    result = runner().invoke("hello", cwd=project)
    assert result.ok, result.stderr
    assert result.stdout.index("project hello") < result.stdout.index("plugin hello")
    where = runner().invoke("--where=hello", cwd=project)
    assert where.ok, where.stderr
    first, second = where.stdout.strip().splitlines()[:2]
    assert "tasks.py" in first
    assert "acme_direct.py" in second and "(shadowed)" in second
