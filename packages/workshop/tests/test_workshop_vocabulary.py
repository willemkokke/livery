"""The vocabulary test: the base names no package extension, tool or coverage verb.

The workshop's base (``livery.workshop``) names
no extension that composes a package and no registry kind, the words this test
looks for, and imports no backend: it asks the extensions a package
holds instead. It names no tool a listed extension's steps bring, and
none of the coverage extension's verbs. This test scans the base's
sources for each: the workshop's wheel ships the base alone.

Two lists say what is allowed. ``RUNTIME`` holds the words the base
uses for its own python runtime (the root ``pyproject.toml``, the
interpreter, the venv, the runtime's language and the project file of
a member holding it), each with its reason; those stay. ``ALLOWANCE``
holds what the base still names beyond it, one count per module and
word, each with the change that removes it or the ruling that keeps
it. A count may only fall: a finding above it refuses, and a module
that now finds fewer refuses until its count is lowered here, so the
list always states what is left.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import pytest

from workshop_installed import skip_unless_installed

SRC = Path(__file__).resolve().parents[1] / "src"
ROOT = Path(__file__).resolve().parents[3]
MANIFESTS = ("pyproject.toml", "conanfile.py")

#: The base's own python runtime: (module, word) to (count, reason).
RUNTIME: dict[tuple[str, str], tuple[int, str]] = {
    ("_ci_generate", "python"): (1, "the entry's interpreter matrix"),
    ("_fragments", "pyproject.toml"): (1, "the root pyproject's fragments"),
    ("_packages", "python"): (
        1,
        "the runtime's language: a member holding it is a distribution of it",
    ),
    ("_packages", "pyproject.toml"): (
        1,
        "the project file a member holding the runtime's language carries",
    ),
    ("_points", "python"): (1, "a runner's interpreter version"),
    ("_pythons", "pyproject.toml"): (1, "the root's requires-python"),
    ("_shipped_files", "pyproject.toml"): (
        1,
        "the root project file the engine composes",
    ),
    ("_pythons", "python"): (1, "the venv's interpreter"),
    ("_reconcile", "pyproject.toml"): (2, "the uv workspace the venv follows"),
    ("_speed", "python"): (1, "a runner's interpreter version"),
    ("_steps", "pyproject.toml"): (
        1,
        "a uv member's manifest the sibling fix wrote, which moves the lock",
    ),
    ("_sync", "pyproject.toml"): (2, "the uv workspace the lock follows"),
    ("testing._conformance", "pyproject.toml"): (3, "the root pyproject's fragments"),
}

#: What the base still names beyond its runtime: (module, word) to
#: (count, reason). Each reason names the change that removes the row: a
#: part of the kinds' removal (#1614), an issue, or a phase of the
#: destination note; or the ruling that keeps it. Only falls.
ALLOWANCE: dict[tuple[str, str], tuple[int, str]] = {
    ("_ci_generate", "conanfile.py"): (
        1,
        "a GitHub job's conan cache key, read from the recipes; the CI"
        " placement review, #1534",
    ),
    ("_devenv", "conan"): (1, "a loop environment's conan home; phase 15"),
    ("_e2e", "nanobind"): (
        1,
        "the loop's check that a native member's wheels were built ahead;"
        " phase 15 moves _e2e out of the base",
    ),
    ("_e2e", "pyproject.toml"): (
        1,
        "the loop's root project file, rendered again by the pass; phase 15"
        " moves _e2e out of the base",
    ),
    ("_new_project", "python"): (
        1,
        "fm new.project writes the stock list, which names python for the"
        " workspace's own tests; ruled to stay, #1673 F3",
    ),
    ("_publish", "python"): (1, "the python registry a publish targets; phase 13"),
    ("_registries", "conan"): (3, "the registry tables; phase 13"),
    ("_registries", "container"): (1, "the registry tables; phase 13"),
    ("_registries", "python"): (4, "the registry tables; phase 13"),
    ("_release", "python"): (1, "the python registry a release resolves; phase 13"),
    ("_release_driver", "conanfile.py"): (
        1,
        "the manifests a release stamps and commits; phase 13",
    ),
    ("_release_driver", "pyproject.toml"): (
        1,
        "the manifests a release stamps and commits; phase 13",
    ),
    ("_release_driver", "python"): (
        1,
        "the python registry the wave resolves; phase 13",
    ),
    ("_templates", "python"): (
        1,
        "the python registry the root project file renders; phase 13",
    ),
    ("_update", "pyproject.toml"): (
        1,
        "a sibling's floor bumped in the project file; phase 13",
    ),
}


def _words() -> set[str]:
    """The words the base never names: extensions composing a package, registry kinds.

    Each installed extension that composes something in a package, and
    each kind of registry an artifact is published to.
    """
    from typing import get_args

    from livery.forge import RegistryKind
    from livery.workshop._extensions import composes_per_package, installed_extensions

    extensions = {name for name in installed_extensions() if composes_per_package(name)}
    return extensions | set(get_args(RegistryKind))


def _counts(
    allowance: dict[tuple[str, str], tuple[int, str]],
) -> dict[tuple[str, str], int]:
    """*allowance*'s counts alone, as `judge` takes them."""
    return {key: count for key, (count, _reason) in allowance.items()}


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


def _word_of(value: str, words: set[str]) -> str:
    if value in words:
        return value
    for manifest in MANIFESTS:
        if manifest in value.split("/"):
            return manifest
    return ""


def scan(src: Path, words: set[str]) -> Counter[tuple[str, str]]:
    """The base's findings by (module, word)."""
    base: Counter[tuple[str, str]] = Counter()
    for path in sorted(src.rglob("*.py")):
        relative = path.relative_to(src)
        if "templates" in relative.parts:
            continue
        dotted = ".".join(relative.with_suffix("").parts).removesuffix(".__init__")
        module = dotted.removeprefix("livery.workshop.")
        tree = ast.parse(path.read_text("utf-8"))
        skip = _docstrings(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in skip
                and (word := _word_of(node.value, words))
            ):
                base[(module, word)] += 1
    return base


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
        beyond = found[key] - exempt
        allowed = allowance.get(key, 0)
        if beyond > allowed:
            problems.append(
                f"{module}: {word!r} {beyond} time(s), {allowed} allowed;"
                " the base names no extension that composes a package and no"
                " registry kind, so ask the extensions a package holds"
            )
        elif beyond < allowed:
            problems.append(
                f"{module}: {word!r} {beyond} time(s) now, {allowed} in"
                " the allowance; lower it to what is left"
            )
    return problems


def test_the_words_are_the_package_level_extensions_and_the_registry_kinds() -> None:
    skip_unless_installed("cmake", "nanobind")
    # The scan has something to look for: every extension a package may
    # list, and the registry kind no extension is named after.
    assert {"cmake", "conan", "cpp", "nanobind", "python", "container"} <= _words()


def test_the_base_names_no_word_beyond_its_allowance() -> None:
    skip_unless_installed("cmake", "nanobind")
    found = scan(SRC, _words())
    assert judge(found, RUNTIME, _counts(ALLOWANCE)) == []


@pytest.mark.parametrize("key", sorted(ALLOWANCE))
def test_every_allowance_entry_states_its_reason(key: tuple[str, str]) -> None:
    count, reason = ALLOWANCE[key]
    assert count > 0
    assert reason


#: The tools an extension's steps and tables bring that the base still
#: names: (module, tool) to (count, reason). A row that goes names what
#: removes it: a part of the kinds' removal (#1614), an issue, or a phase
#: of the destination note.
TOOL_ALLOWANCE: dict[tuple[str, str], tuple[int, str]] = {
    ("_ci_generate", "conan"): (
        1,
        "a GitHub job's conan home; the CI placement review, #1534",
    ),
    ("_devenv", "conan"): (1, "a loop environment's conan home; phase 15"),
    ("_e2e", "cmake"): (
        1,
        "the loop's host allowance; phase 15 moves _e2e out of the base",
    ),
    ("_e2e", "conan"): (1, "the loop's conan home; phase 15"),
    ("_e2e", "ninja"): (
        1,
        "the loop's host allowance; phase 15 moves _e2e out of the base",
    ),
    ("_entry", "conan"): (
        1,
        "the entry script's note on what a native member builds with, and"
        " the job's own conan home; the toolchains, phase 14",
    ),
    ("_env_tasks", "conan"): (
        3,
        "a GitHub job's conan home, and the two refusals of a job with no"
        " temp of its own to keep it in (#1715); the CI placement review, #1534",
    ),
    ("_registries", "conan"): (6, "the registry tables; phase 13"),
    ("_new_project", "ruff"): (1, "fm new.project writes the stock list, as ruled"),
    ("_new_project", "basedpyright"): (
        1,
        "fm new.project writes the stock list, as ruled",
    ),
    ("_new_project", "pytest"): (1, "fm new.project writes the stock list, as ruled"),
    ("_new_project", "mkdocstrings"): (
        1,
        "fm new.project writes the stock list, as ruled",
    ),
}


def _extension_tools() -> set[str]:
    """The tools the installed extensions' steps name and their tables require.

    A step names the tools it runs, and a step named for its extension
    is named for its tool, as ``format.ruff`` and ``docs.mkdocstrings``
    are; another step's name, cmake's ``build.configure``, names no
    tool. The tables are the top level's ``[toolroom]`` and each
    ``[for.<target>]`` table's.
    """
    from importlib.metadata import entry_points

    from livery.toolroom.store import Requirement
    from livery.workshop._declaration import read

    tools: set[str] = set()
    for entry in entry_points(group="workshop.extensions"):
        found = read(entry.name, entry.value)
        if found is None:
            continue
        for record in found.additions.steps:
            tools.update(record.tools)
            if record.step == entry.name:
                tools.add(record.step)
        declared = [*found.tools]
        for required in found.target_tools.values():
            declared += required
        tools.update(Requirement.parse(text, site=entry.name).name for text in declared)
    return tools


def tool_scan(src: Path, tools: set[str]) -> Counter[tuple[str, str]]:
    """The base's string constants naming one of *tools*, by (module, tool).

    A constant names a tool when the tool is one of its words: ``ruff``,
    ``ruff>=0.16`` and ``format.ruff`` all do. Docstrings are prose and
    left out, as the kind scan leaves them.
    """
    import re

    found: Counter[tuple[str, str]] = Counter()
    for path in sorted((src / "livery" / "workshop").rglob("*.py")):
        module = ".".join(
            path.relative_to(src / "livery" / "workshop").with_suffix("").parts
        )
        tree = ast.parse(path.read_text("utf-8"))
        skip = _docstrings(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in skip
            ):
                words = set(re.split(r"[^A-Za-z0-9_-]+", node.value))
                for tool in tools & words:
                    found[(module, tool)] += 1
    return found


def test_the_base_names_no_tool_an_extension_brings() -> None:
    skip_unless_installed("cmake", "conan")
    import livery.workshop

    if not Path(livery.workshop.__file__).resolve().is_relative_to(ROOT):
        # The release train's isolated leg installs the workshop's wheel
        # alone, and no extension that depends on the workshop is there
        # to say which tools it brings.
        pytest.skip("the scan reads the extensions this checkout installs")
    tools = _extension_tools()
    # The scan has something to look for: a step's tool, a table's, and
    # a [for] table's, the measurer cmake requires while coverage is
    # listed.
    assert {"ruff", "git_cliff", "dotnet_coverage"} <= tools
    found = tool_scan(SRC, tools)
    allowed = {key: count for key, (count, _reason) in TOOL_ALLOWANCE.items()}
    assert dict(found) == allowed


@pytest.mark.parametrize("key", sorted(TOOL_ALLOWANCE))
def test_every_tool_allowance_entry_states_its_reason(key: tuple[str, str]) -> None:
    count, reason = TOOL_ALLOWANCE[key]
    assert count > 0
    assert reason


#: The coverage extension's verbs the base still names:
#: (module, verb) to (count, reason).
VERB_ALLOWANCE: dict[tuple[str, str], tuple[int, str]] = {
    ("_e2e", "coverage.accept"): (
        1,
        "the loop's ratchet scenario lowers a mark through the extension's verb",
    ),
}


def _coverage_verbs() -> set[str]:
    """The coverage extension's verbs, read as data from its plugin's task tree.

    The plugin the extension's declaration names is a ``footman.tasks``
    entry point. A fresh interpreter loads that entry point alone and
    prints the tree footman built from it, so this suite imports no
    module of the extension. Skips while coverage is not installed.
    """
    import json
    import subprocess
    import sys
    from importlib.metadata import entry_points

    from livery.workshop._extensions import installed_declaration

    skip_unless_installed("coverage")
    declared = installed_declaration("coverage")
    assert declared is not None and declared.plugin
    (entry,) = entry_points(group="footman.tasks", name=declared.plugin)
    probe = (
        "import json, sys\n"
        "from importlib import import_module\n"
        "import livery.footman as footman\n"
        "import_module(sys.argv[1])\n"
        "groups = footman.task_tree()['groups']\n"
        "print(json.dumps({name: sorted(group['tasks'])"
        " for name, group in groups.items()}))\n"
    )
    run = subprocess.run(
        [sys.executable, "-c", probe, entry.value],
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    groups: dict[str, list[str]] = json.loads(run.stdout)
    return {f"{name}.{task}" for name, tasks in groups.items() for task in tasks}


def verb_scan(src: Path, verbs: set[str]) -> Counter[tuple[str, str]]:
    """The base's string constants naming one of *verbs*, by (module, verb).

    A constant names a verb when the verb's dotted name is one of its
    words: ``coverage.union`` and ``fm coverage.accept packages/x`` do,
    ``the coverage union`` does not. Docstrings are prose and left out.
    """
    import re

    found: Counter[tuple[str, str]] = Counter()
    for path in sorted((src / "livery" / "workshop").rglob("*.py")):
        module = ".".join(
            path.relative_to(src / "livery" / "workshop").with_suffix("").parts
        )
        tree = ast.parse(path.read_text("utf-8"))
        skip = _docstrings(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in skip
            ):
                words = {
                    word.rstrip(".")
                    for word in re.split(r"[^A-Za-z0-9_.-]+", node.value)
                }
                for verb in verbs & words:
                    found[(module, verb)] += 1
    return found


def test_the_base_names_no_verb_of_the_coverage_extension() -> None:
    verbs = _coverage_verbs()
    # The scan has something to look for: the leg's entry and the judge.
    assert {"coverage.leg", "coverage.union", "coverage.accept"} <= verbs
    found = verb_scan(SRC, verbs)
    allowed = {key: count for key, (count, _reason) in VERB_ALLOWANCE.items()}
    assert dict(found) == allowed


def test_a_base_module_naming_a_coverage_verb_refuses(tmp_path: Path) -> None:
    src = _base_module(
        tmp_path,
        "_fresh.py",
        '"""Prose naming coverage.union is left out."""\n'
        'ENTRY = "coverage.union"\nPROSE = "the coverage union"\n',
    )
    assert dict(verb_scan(src, {"coverage.union"})) == {("_fresh", "coverage.union"): 1}


def _base_module(tmp_path: Path, name: str, text: str) -> Path:
    module = tmp_path / "livery" / "workshop" / name
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(text, encoding="utf-8")
    return tmp_path


def test_a_new_word_literal_in_the_base_refuses_naming_the_module(
    tmp_path: Path,
) -> None:
    src = _base_module(
        tmp_path,
        "_fresh.py",
        '"""A docstring naming conan is prose."""\nNAME = "conan"\n'
        'HOME = "packages/x/pyproject.toml"\n',
    )
    found = scan(src, {"conan"})
    assert judge(found, {}, {}) == [
        "_fresh: 'conan' 1 time(s), 0 allowed; the base names no extension that"
        " composes a package and no registry kind, so ask the extensions a package"
        " holds",
        "_fresh: 'pyproject.toml' 1 time(s), 0 allowed; the base names no"
        " extension that composes a package and no registry kind, so ask the"
        " extensions a package holds",
    ]


def test_the_allowance_only_falls(tmp_path: Path) -> None:
    src = _base_module(tmp_path, "_fresh.py", 'A = "conan"\nB = "conan"\n')
    found = scan(src, {"conan"})
    assert judge(found, {}, {("_fresh", "conan"): 2}) == []
    assert judge(found, {}, {("_fresh", "conan"): 1}) == [
        "_fresh: 'conan' 2 time(s), 1 allowed; the base names no extension that"
        " composes a package and no registry kind, so ask the extensions a package"
        " holds"
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
    found = scan(src, set())
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
