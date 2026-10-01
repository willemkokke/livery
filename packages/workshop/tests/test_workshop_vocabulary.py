"""The base names no package kind: the vocabulary test of contract 10.

The workshop's base (``livery.workshop`` outside ``_backends`` and
``layers``) reaches a kind through the kind registry, never by
importing a backend or by spelling the kind's words. This test scans
the base's sources for both, and for every layer's import of a
backend.

Two lists say what is allowed. ``RUNTIME`` holds the words the base
uses for its own python runtime (the root ``pyproject.toml``, the
interpreter, the venv), each with its reason; those stay. ``ALLOWANCE``
holds what the base still knows of a kind today, one count per module
and word. A count may only fall: a finding above it refuses, and a
module that now finds fewer refuses until its count is lowered here,
so the list always states what is left.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
MANIFESTS = ("pyproject.toml", "conanfile.py")
BACKEND_IMPORT = "import a backend"

#: The base's own python runtime: (module, word) to (count, reason).
RUNTIME: dict[tuple[str, str], tuple[int, str]] = {
    ("_checks", "pyproject.toml"): (2, "the ruff fragments of the root pyproject"),
    ("_ci_generate", "python"): (1, "the entry's interpreter matrix"),
    ("_coverage_store", "pyproject.toml"): (1, "the root pins the gate reads"),
    ("_docs_contract", "container"): (2, "a docs publish seam, not an artifact"),
    ("_fragments", "pyproject.toml"): (1, "the root pyproject's fragments"),
    ("_new_project", "pyproject.toml"): (1, "the root pyproject of a birth"),
    ("_points", "python"): (1, "a runner's interpreter version"),
    ("_provenance", "pyproject.toml"): (1, "the root pyproject's header"),
    ("_pythons", "pyproject.toml"): (1, "the root's requires-python"),
    ("_pythons", "python"): (1, "the venv's interpreter"),
    ("_reconcile", "pyproject.toml"): (2, "the uv workspace the venv follows"),
    ("_speed", "python"): (1, "a runner's interpreter version"),
    ("_sync", "pyproject.toml"): (2, "the uv workspace the lock follows"),
    ("testing._conformance", "pyproject.toml"): (3, "the root pyproject's fragments"),
}

#: What the base still knows of a kind: (module, word) to count. Only falls.
ALLOWANCE: dict[tuple[str, str], int] = {
    ("_checks", BACKEND_IMPORT): 1,
    ("_checks", "cpp-conan"): 6,
    ("_checks", "pyproject.toml"): 6,
    ("_checks", "python"): 2,
    ("_checks", "python-nanobind"): 1,
    ("_ci_generate", "conanfile.py"): 1,
    ("_dev_release", "pyproject.toml"): 1,
    ("_devenv", "conan"): 2,
    ("_e2e", BACKEND_IMPORT): 1,
    ("_e2e", "conan"): 4,
    ("_e2e", "pyproject.toml"): 1,
    ("_kinds", BACKEND_IMPORT): 1,
    ("_kinds", "conan"): 3,
    ("_kinds", "cpp-conan"): 2,
    ("_kinds", "python"): 5,
    ("_kinds", "python-nanobind"): 1,
    ("_packages", "pyproject.toml"): 1,
    ("_publish", "conan"): 4,
    ("_publish", "python"): 1,
    ("_quality", BACKEND_IMPORT): 3,
    ("_registries", "conan"): 3,
    ("_registries", "container"): 1,
    ("_registries", "python"): 4,
    ("_release", BACKEND_IMPORT): 1,
    ("_release", "conan"): 1,
    ("_release", "python"): 1,
    ("_release_driver", BACKEND_IMPORT): 2,
    ("_release_driver", "conan"): 2,
    ("_release_driver", "conanfile.py"): 2,
    ("_release_driver", "pyproject.toml"): 2,
    ("_release_driver", "python"): 2,
    ("_replay", "python"): 2,
    ("_sync", "cpp-conan"): 1,
    ("_templates", "python"): 5,
    ("_tools", "python"): 2,
    ("_update", "pyproject.toml"): 1,
}


def _words() -> set[str]:
    from livery.workshop._kinds import all_kinds

    concrete = {record.name for record in all_kinds() if not record.abstract}
    return concrete | {"conan", "container"}


def _docstrings(tree: ast.AST) -> set[int]:
    found: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(
                node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            )
            and node.body
        ):
            first = node.body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                found.add(id(first.value))
    return found


def _imports_a_backend(node: ast.AST) -> bool:
    names: list[str] = []
    if isinstance(node, ast.ImportFrom) and node.module:
        names = [node.module, *(f"{node.module}.{a.name}" for a in node.names)]
    elif isinstance(node, ast.Import):
        names = [alias.name for alias in node.names]
    return any(name.startswith("livery.workshop._backends._") for name in names)


def _word_of(value: str, words: set[str]) -> str:
    if value in words:
        return value
    for manifest in MANIFESTS:
        if manifest in value.split("/"):
            return manifest
    return ""


def scan(src: Path, words: set[str]) -> tuple[Counter[tuple[str, str]], list[str]]:
    """The base's findings by (module, word), and every layer's backend import."""
    base: Counter[tuple[str, str]] = Counter()
    layers: list[str] = []
    for path in sorted(src.rglob("*.py")):
        relative = path.relative_to(src)
        if "templates" in relative.parts:
            continue
        dotted = ".".join(relative.with_suffix("").parts).removesuffix(".__init__")
        if not dotted.startswith("livery.workshop."):
            continue
        module = dotted.removeprefix("livery.workshop.")
        if module.startswith("_backends"):
            continue
        tree = ast.parse(path.read_text("utf-8"))
        if module.startswith("layers."):
            layers += [
                f"{module}:{node.lineno}"
                for node in ast.walk(tree)
                if isinstance(node, (ast.Import, ast.ImportFrom))
                and _imports_a_backend(node)
            ]
            continue
        skip = _docstrings(tree)
        for node in ast.walk(tree):
            if _imports_a_backend(node):
                base[(module, BACKEND_IMPORT)] += 1
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in skip
                and (word := _word_of(node.value, words))
            ):
                base[(module, word)] += 1
    return base, layers


def judge(
    found: Counter[tuple[str, str]],
    runtime: dict[tuple[str, str], tuple[int, str]],
    allowance: dict[tuple[str, str], int],
) -> list[str]:
    """Each way *found* departs from the runtime list and the allowance."""
    problems: list[str] = []
    for key in sorted(set(found) | set(allowance) | set(runtime)):
        module, word = key
        exempt = runtime.get(key, (0, ""))[0]
        if found[key] < exempt:
            problems.append(
                f"{module}: {word!r} {found[key]} time(s) now, {exempt} in the"
                " runtime list; lower it to what is left"
            )
            continue
        kind_knowledge = found[key] - exempt
        allowed = allowance.get(key, 0)
        if kind_knowledge > allowed:
            problems.append(
                f"{module}: {word!r} {kind_knowledge} time(s), {allowed} allowed;"
                " the base names no package kind, so reach the kind through"
                " its registration"
            )
        elif kind_knowledge < allowed:
            problems.append(
                f"{module}: {word!r} {kind_knowledge} time(s) now, {allowed} in"
                " the allowance; lower it to what is left"
            )
    return problems


def test_the_base_names_no_kind_beyond_its_allowance() -> None:
    found, _ = scan(SRC, _words())
    assert judge(found, RUNTIME, ALLOWANCE) == []


def test_no_layer_imports_a_backend() -> None:
    _, layers = scan(SRC, _words())
    assert layers == []


def _base_module(tmp_path: Path, name: str, text: str) -> Path:
    module = tmp_path / "livery" / "workshop" / name
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(text, encoding="utf-8")
    return tmp_path


def test_a_base_module_importing_a_backend_refuses(tmp_path: Path) -> None:
    src = _base_module(
        tmp_path, "_fresh.py", "from livery.workshop._backends import _python\n"
    )
    found, _ = scan(src, {"python"})
    (problem,) = judge(found, {}, {})
    assert problem.startswith("_fresh: 'import a backend' 1 time(s), 0 allowed")


def test_a_new_kind_literal_in_the_base_refuses_naming_the_module(
    tmp_path: Path,
) -> None:
    src = _base_module(
        tmp_path,
        "_fresh.py",
        '"""A docstring naming conan is prose."""\nKIND = "conan"\n'
        'HOME = "packages/x/pyproject.toml"\n',
    )
    found, _ = scan(src, {"conan"})
    assert judge(found, {}, {}) == [
        "_fresh: 'conan' 1 time(s), 0 allowed; the base names no package kind,"
        " so reach the kind through its registration",
        "_fresh: 'pyproject.toml' 1 time(s), 0 allowed; the base names no"
        " package kind, so reach the kind through its registration",
    ]


def test_the_allowance_only_falls(tmp_path: Path) -> None:
    src = _base_module(tmp_path, "_fresh.py", 'A = "conan"\nB = "conan"\n')
    found, _ = scan(src, {"conan"})
    assert judge(found, {}, {("_fresh", "conan"): 2}) == []
    assert judge(found, {}, {("_fresh", "conan"): 1}) == [
        "_fresh: 'conan' 2 time(s), 1 allowed; the base names no package kind,"
        " so reach the kind through its registration"
    ]
    assert judge(found, {}, {("_fresh", "conan"): 3}) == [
        "_fresh: 'conan' 2 time(s) now, 3 in the allowance; lower it to what is left"
    ]


def test_the_runtime_list_exempts_only_its_count(tmp_path: Path) -> None:
    src = _base_module(
        tmp_path,
        "_fresh.py",
        'ROOT = "pyproject.toml"\nMEMBER = "packages/x/pyproject.toml"\n',
    )
    found, _ = scan(src, set())
    runtime = {("_fresh", "pyproject.toml"): (1, "the root")}
    (problem,) = judge(found, runtime, {})
    assert problem.startswith("_fresh: 'pyproject.toml' 1 time(s), 0 allowed")
    stale = {("_fresh", "pyproject.toml"): (3, "the root")}
    assert judge(found, stale, {}) == [
        "_fresh: 'pyproject.toml' 2 time(s) now, 3 in the runtime list; lower"
        " it to what is left"
    ]


@pytest.mark.parametrize("key", sorted(RUNTIME))
def test_every_runtime_entry_states_its_reason(key: tuple[str, str]) -> None:
    count, reason = RUNTIME[key]
    assert count > 0
    assert reason


def test_a_layer_importing_a_backend_is_named(tmp_path: Path) -> None:
    src = _base_module(
        tmp_path,
        "layers/site/_pages.py",
        "def f():\n    from livery.workshop._backends._python import x\n",
    )
    _, layers = scan(src, set())
    assert layers == ["layers.site._pages:2"]


def _loaded_by(statement: str, prefixes: tuple[str, ...]) -> list[str]:
    import subprocess
    import sys

    # A fresh interpreter, so this suite's own imports do not count.
    script = (
        f"import sys; {statement};"
        " print('\\n'.join(sorted(m for m in sys.modules"
        f" if m.startswith({prefixes!r}))))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    return result.stdout.split()


#: What a verb that reaches no forge and no tool store must not load:
#: the forge's backends and the store's engine with strongroom's store.
HEAVY = (
    "livery.forge._github",
    "livery.forge._gitea",
    "livery.forge._gitlab",
    "livery.forge._registry",
    "livery.toolroom.store._engine",
    "livery.strongroom._store",
)


def test_mounting_the_workshop_loads_no_forge_backend_and_no_store_engine() -> None:
    assert _loaded_by("import livery.workshop._tasks", HEAVY) == []
