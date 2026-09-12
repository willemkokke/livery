"""The two hard invariants, as tests — violating either fails the gate.

CLAUDE.md declares them; nothing in the suite enforced them (audit H50):
zero runtime dependencies, and a completion hot path that never imports
the framework or the user's code. Both are load-bearing for the pitch, so
both get teeth here. The import-scan half runs static over the source —
an `import requests` anywhere under src/footman/ fails this file before
packaging metadata would ever notice.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import tomllib
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "footman"
PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"

# The lazily-imported optional friends a module may reach for *inside* a
# gated body: the blessed exception in CLAUDE.md — a first-party plugin task
# stacked under @requires_dep may import its optional package at call time.
# Everything named here must appear only under a function, never at module
# level; the module-level scan below stays absolute.
_OPTIONAL = frozenset({"rich"})


def _module_level_imports(path: Path) -> set[str]:
    """Top-level (module-scope) imports of *path*, by root package name."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in tree.body:  # module scope only: function bodies may lazy-load
        if isinstance(node, ast.Import):
            found.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.partition(".")[0])
    return found


def test_zero_runtime_dependencies_is_declared_and_true():
    # The declaration: nothing under [project] dependencies.
    meta = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    assert meta["project"]["dependencies"] == []
    # The practice: every module-level import in the shipped package is the
    # standard library or footman itself. A lazy optional import inside a
    # @requires_dep-gated body is legal (the blessed exception); this walk
    # reads module scope only, so such an import failing HERE means someone
    # hoisted it to the top of the file.
    allowed = set(sys.stdlib_module_names) | {"footman"}
    # One module is imported BY its dependency, never the other way round:
    # the pytest plugin loads through the `pytest11` entry point, so pytest
    # is present by construction whenever this file executes. Nothing else
    # gets the pass — and nothing under footman/ may import pytest_plugin.
    per_file = {"pytest_plugin.py": {"pytest"}}
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        extra = per_file.get(str(path.relative_to(SRC)), set())
        for name in _module_level_imports(path):
            if name not in allowed and name not in extra:
                offenders.append(f"{path.relative_to(SRC)}: import {name}")
    assert not offenders, "\n".join(offenders)
    importers = [
        str(path.relative_to(SRC))
        for path in sorted(SRC.rglob("*.py"))
        if path.name != "pytest_plugin.py"
        and any(
            "pytest_plugin" in ln
            for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.lstrip().startswith(("import ", "from "))
        )
    ]
    assert not importers, importers  # the exemption must stay one-way


def test_the_completion_hot_path_imports_no_framework_and_no_tasks(tmp_path):
    # One real TAB press in a fresh project, in a fresh interpreter. The
    # process answers, then testifies about every module it loaded: no
    # framework internals (registry, _app, _split — the run machinery), and
    # never the user's tasks file. The manifest build that DOES import
    # tasks.py happens in a detached child, so this process stays clean
    # either way — which is exactly the invariant.
    probe = (
        "import json, sys\n"
        "from livery.footman import main\n"
        "try:\n"
        "    main()\n"
        "except SystemExit:\n"
        "    pass\n"
        "loaded = sorted(\n"
        "    n for n in sys.modules\n"
        "    if n == 'footman' or n.startswith('footman.')\n"
        ")\n"
        "print('LOADED ' + json.dumps(loaded))\n"
    )
    # From a directory outside any locked project: run from this
    # repository, the completion path heals its environment (uv sync
    # against the real venv), re-installing editables under the other
    # xdist workers' entry-point scans.
    done = subprocess.run(
        [sys.executable, "-c", probe, "--complete", "--", ""],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=tmp_path,
    )
    line = next(
        (ln for ln in done.stdout.splitlines() if ln.startswith("LOADED ")), None
    )
    assert line is not None, done.stdout + done.stderr
    loaded = set(json.loads(line[len("LOADED ") :]))
    # The hot path's whole allowance. Growing this set is a decision about
    # the ~30 ms budget, not a test to appease — that is why it is exact.
    assert loaded <= {
        "footman",
        "livery.footman._complete",
        "livery.footman._paths",
    }, sorted(loaded)
    forbidden = {
        "livery.footman.registry",
        "livery.footman._app",
        "livery.footman._split",
    }
    assert not (loaded & forbidden), sorted(loaded & forbidden)


def test_a_warm_tab_pays_for_no_heavyweight_stdlib(tmp_path, monkeypatch):
    """A WARM press — manifest on disk — must not import the heavy stdlib.

    pathlib drags glob and re (~5 ms), subprocess ~3 ms, typing ~1.4 ms:
    together they were most of footman's ~11 ms above interpreter startup.
    The spawn paths (a rebuild, a dynamic completer) may import what they
    like — they cost 100 ms+ anyway — but the warm answer is one file read,
    a JSON parse and a tree walk, and its imports must look like it.
    """
    import os

    from livery.footman import _manifest, _paths, registry

    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "cache"))
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname='x'\n")
    monkeypatch.chdir(proj)
    reg = registry.Group("root")

    @reg.task
    def build(): ...

    _manifest.sync_manifest(reg, Path.cwd(), completion_max_age=0)
    assert _paths.cwd_manifest_path().is_file()

    probe = (
        "import json, sys\n"
        "from livery.footman import main\n"
        "try:\n"
        "    main()\n"
        "except SystemExit:\n"
        "    pass\n"
        "heavy = sorted(\n"
        "    n for n in ('pathlib', 'subprocess', 'glob', 'typing', 'fnmatch')\n"
        "    if n in sys.modules\n"
        ")\n"
        "print('HEAVY ' + json.dumps(heavy))\n"
    )
    # Coverage's .pth hook starts itself in every python child when the
    # gate runs under it, and coverage imports pathlib and typing — which
    # would be measured as the hot path's sins. The probe testifies about
    # footman, so it runs uninstrumented. `-S` skips site processing
    # entirely: a dev venv's .pth hooks (coverage-enable-subprocess
    # imports coverage, and with it pathlib and typing, at every
    # interpreter start) would otherwise stand in for footman's own
    # imports. PYTHONPATH hands the child exactly the package a wheel
    # install would put on sys.path.
    env = {
        **os.environ,
        "FOOTMAN_CACHE_DIR": str(tmp_path / "cache"),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
    }
    for key in list(env):
        if key.startswith(("COVERAGE_", "COV_CORE_")):
            del env[key]
    done = subprocess.run(
        [sys.executable, "-S", "-c", probe, "--complete", "--", ""],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=proj,
        env=env,
    )
    line = next(
        (ln for ln in done.stdout.splitlines() if ln.startswith("HEAVY ")), None
    )
    assert line is not None, done.stdout + done.stderr
    assert "build" in done.stdout  # the press actually answered, warm
    heavy = json.loads(line[len("HEAVY ") :])
    assert heavy == [], heavy
