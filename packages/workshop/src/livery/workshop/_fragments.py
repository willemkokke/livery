"""The configuration a check owns, composed into the files the render writes.

A check record carries one fragment per rendered file it has
something to say in: its tables in ``pyproject.toml``, its lines in
``.vscode/settings.json``, its ``.clang-tidy`` for the native kinds.
The render composes the base template with the registered fragments
in check-name order, so two machines write the same bytes, and the
drift gate judges the result. A fragment is jinja text rendered with
the same data the template reads. A project file's fragment applies
once per workspace, with per-package variation spelled inside it the
tool's own way; a package file's fragment applies per package, the
nearest kind's down the chain winning, which is how one tool wants
different defaults under different kinds. A fragment leaves the render
with its record: unregistering the check removes the section from the
next render, and a per-package file it wrote alone goes when its
bytes are the bytes the render last wrote.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: The project files a fragment may address; a fragment naming another
#: file refuses at registration.
PROJECT_FILES = ("pyproject.toml", ".vscode/settings.json", ".vscode/extensions.json")

#: The per-package files a fragment may address, rendered per kind.
PACKAGE_FILES = (".clang-format", ".clang-tidy")


@dataclass(frozen=True)
class Fragment:
    """One check's configuration for one rendered file.

    Attributes:
        file: The rendered file, a project file relative to the root
            or a per-package file name.
        text: The fragment's jinja text, rendered with the file's own
            render data.
        kind: For a per-package file, the package kind the fragment is
            for; a derived kind inherits it down the chain. Empty for a
            project file.
    """

    file: str
    text: str
    kind: str = ""


def verify(fragments: tuple[Fragment, ...], check: str) -> None:
    """Refuse a fragment naming a file the render does not write.

    Raises:
        ValueError: naming the check, the file, and the files a
            fragment may address.
    """
    for fragment in fragments:
        if fragment.file in PROJECT_FILES:
            if fragment.kind:
                raise ValueError(
                    f"check {check!r}: a fragment for {fragment.file} applies to"
                    " the whole workspace and names no kind"
                )
            continue
        if fragment.file in PACKAGE_FILES:
            if not fragment.kind:
                raise ValueError(
                    f"check {check!r}: a fragment for {fragment.file} is rendered"
                    " per package and names the kind it is for"
                )
            continue
        raise ValueError(
            f"check {check!r}: a fragment for {fragment.file!r} names a file the"
            f" render does not write; the project files are"
            f" {', '.join(PROJECT_FILES)} and the package files"
            f" {', '.join(PACKAGE_FILES)}"
        )


def _render(text: str, data: dict[str, Any]) -> str:
    """*text* rendered with *data*, the way the template reads it."""
    import jinja2

    # Lenient on an undefined name, as copier's own environment is,
    # so a render over partial data draws the same file the template
    # would; the drift gate reads the full data and judges the bytes.
    environment = jinja2.Environment(keep_trailing_newline=True)
    return environment.from_string(text).render(**data)


def compose_project(data: dict[str, Any]) -> dict[str, str]:
    """Each project file's composed fragments, by file, in check-name order.

    A file no check has a fragment for maps to the empty string, so a
    template's ``{{ fragments['...'] }}`` always resolves.
    """
    from livery.workshop._checks import checks_by_name

    found: dict[str, str] = {}
    for file in PROJECT_FILES:
        found[file] = ""
    for name in sorted(checks_by_name()):
        record = checks_by_name()[name]
        for fragment in record.fragments:
            if fragment.file in PROJECT_FILES:
                rendered = _render(fragment.text, data)
                if found[fragment.file] and not found[fragment.file].endswith("\n\n"):
                    found[fragment.file] += "\n"
                found[fragment.file] += rendered
    return found


def package_fragment(kind_name: str, file: str) -> tuple[str, str] | None:
    """The fragment text a package of *kind_name* renders as *file*, and its check.

    The nearest kind down the chain wins; None when no check has one,
    which is what a withdrawn check looks like to the apply.
    """
    from livery.workshop._checks import checks_by_name
    from livery.workshop._kinds import kind_chain, kind_names

    if kind_name not in kind_names():
        return None
    for kind in kind_chain(kind_name):
        for name in sorted(checks_by_name()):
            for fragment in checks_by_name()[name].fragments:
                if fragment.file == file and fragment.kind == kind.name:
                    return fragment.text, name
    return None


def compose_package(kind_name: str, file: str, data: dict[str, Any]) -> str | None:
    """*file* rendered for a package of *kind_name*, or None when no check owns it."""
    found = package_fragment(kind_name, file)
    if found is None:
        return None
    text, _check = found
    return _render(text, data)


# The builtin fragments, moved from the base template verbatim: each
# tool's table where the tool reads one file per project.

RUFF_BASE = r"""[tool.ruff]
line-length = 88
target-version = "py{{ python_floor | replace('.', '') }}"
src = [{% for package in py %}"packages/{{ package.dir }}/src", "packages/{{ package.dir }}/tests", {% endfor %}"tests"]
"""

RUFF_LINT = r"""[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "C4", "RUF", "D"]

[tool.ruff.lint.pydocstyle]
convention = "google"

[tool.ruff.lint.per-file-ignores]
# Test bodies explain themselves by name and assertion.
"tests/**" = ["D1"]
"**/tests/**" = ["D1"]
# The provenance headers name the template source verbatim, and a
# source URL or path may be long.
"tasks.py" = ["E501"]
"tests/test_workspace_contracts.py" = ["E501"]
"tests/test_docs_drift.py" = ["E501"]
# The CI emitters carry workflow YAML as string content, and the
# check records' fragments carry the rendered files' tables; their
# lines are the generated files' lines, not prose to wrap.
"**/_ci_generate.py" = ["E501"]
"**/_fragments.py" = ["E501"]
"""

TYPECHECKERS = r"""[tool.basedpyright]
include = [{% if packages %}"packages", {% endif %}"tests", "tasks.py"]
# A conan recipe is conan's input, read by conan's own interpreter
# where the conan package lives; the workspace venv never has it, so
# the checker that reads the whole tree skips the recipes. The cpp
# members are skipped whole: their kind type-checks nothing.
exclude = [{% if native %}{% for package in native %}"packages/{{ package.dir }}", {% endfor %}{% endif %}"packages/*/conanfile.py"]
# The IDE and the CLI resolve the same environment: the workspace
# members are editable installs in .venv, so basedpyright must look
# there whatever interpreter the editor has selected.
venvPath = "."
venv = ".venv"
# The tool stubs `{{ runner_prog }} tools.restub` writes; the default stub path,
# named so the four checkers visibly read one directory.
stubPath = "typings"
pythonVersion = "{{ python_floor }}"
typeCheckingMode = "standard"
reportMissingModuleSource = false

[tool.mypy]
# Second gate of the four. {{ namespace_package }}.* is fully strict; tests and tasks.py
# run the usage-checking half (check_untyped_defs, on via strict), so
# every test body type-checks as consumer code without demanding
# `-> None` on every def. Narrow suppressions live inline as
# `# type: ignore[code]` with a reason; pyright-only suppressions use
# `# pyright: ignore` so warn_unused_ignores keeps this checker's set
# honest.
files = [
{% for package in py %}    "packages/{{ package.dir }}/src",
    "packages/{{ package.dir }}/tests",
{% endfor %}    "tests",
    "tasks.py",
]
mypy_path = [
    "typings",
{% for package in py %}    "packages/{{ package.dir }}/src",
    "packages/{{ package.dir }}/tests",
{% endfor %}]
# PEP 420 namespace in a src layout: mypy needs both to derive module
# names without an __init__.py trail.
namespace_packages = true
explicit_package_bases = true
# Deterministic world, whatever host runs the check. linux is the
# bare-run default; the typecheck task adds darwin and win32 runs,
# since mypy has no all-platforms mode.
platform = "linux"
python_version = "{{ python_floor }}"
strict = true
disallow_untyped_defs = false
disallow_incomplete_defs = false
disallow_untyped_calls = false
disallow_untyped_decorators = false
# The public surface's re-export discipline is policed by verifytypes
# and the public-surface test instead.
implicit_reexport = true

[[tool.mypy.overrides]]
module = "{{ namespace_package }}.*"
disallow_untyped_defs = true
disallow_incomplete_defs = true
disallow_untyped_calls = true
disallow_untyped_decorators = true

[[tool.mypy.overrides]]
# The generated tool stubs carry the suppressions their generator
# placed for its own reading of each tool; which of them fire varies
# with flags this config does not share, and an unused one there is
# the generator's business, not drift.
module = "*.toolroom.stubs.*"
warn_unused_ignores = false

[tool.ty]
# Third gate. Scope: the packages themselves; the consumer seam in
# tests is already double-checked by basedpyright and mypy.
[tool.ty.src]
include = [{% if py %}{% for package in py %}"packages/{{ package.dir }}/src"{% if not loop.last %}, {% endif %}{% endfor %}{% else %}"tasks.py"{% endif %}]

[tool.ty.environment]
# Every platform at once (ty checks the union), {{ python_floor }} floor.
python-platform = "all"
python-version = "{{ python_floor }}"
# The tool stubs `{{ runner_prog }} tools.restub` writes.
extra-paths = ["typings"]

[tool.pyrefly]
# Fourth gate, same scope as ty. Preset `default`, not `strict`:
# strict demands @override, and `typing.override` is Python 3.12+; a
# zero-dependency 3.11 library cannot spell it without a
# typing_extensions runtime dep. Revisit when 3.11 support ends.
preset = "default"
project-includes = [{% if py %}{% for package in py %}"packages/{{ package.dir }}/src"{% if not loop.last %}, {% endif %}{% endfor %}{% else %}"tasks.py"{% endif %}]
# Both path heuristics off: a checkout under a dot-directory (a
# `.claude/worktrees/` worktree) silently skips every include and the
# gate fails on "no files matched". At the repo root neither setting
# changes anything.
use-ignore-files = false
disable-project-excludes-heuristics = true
# The tool stubs `{{ runner_prog }} tools.restub` writes.
search-path = ["typings"]
# Every platform at once, {{ python_floor }} floor.
python-platform = "all"
python-version = "{{ python_floor }}"

[tool.pyrefly.errors]
# Held at error so they cannot silently accumulate; both classes are
# zero today.
deprecated = "error"
unnecessary-type-conversion = "error"
"""

TESTS = r"""[tool.coverage.run]
# The tests measure, and every process they start, every
# {{ runner_prog }} child a test spawns included: inside CI the test
# runner arms COVERAGE_PROCESS_START in pytest's environment, the
# installed .pth starts the meter in every python under it, and the
# patch cascades through the workers. The gate's own driver is never
# metered, so a line counts only when a test reached it. Parallel
# data files merge per leg, then across legs in the aggregating
# gate job.
patch = ["subprocess"]
parallel = true
relative_files = true
# Branches as well as statements: a conditional's untaken arm is
# uncovered, where line coverage shows the conditional as covered
# once its line ran. The floors judge the combined figure.
branch = true
source = ["{{ namespace_package }}"]
# The C tracer, not sys.monitoring: the sysmon core records a line
# under the first context that reaches it and never again, and the
# check legs split one run's data per suite by context, which needs
# every context complete.
core = "ctrace"

[tool.coverage.paths]
# One identity for a file whichever separator wrote it, so Windows
# and POSIX legs combine as one file set.
packages = ["packages/", "packages\\"]

[tool.pytest.ini_options]
testpaths = [{% if packages %}"packages", {% endif %}"tests"]
# Test modules are named by their path (`--import-mode=importlib`), so
# two packages may both have a tests/test_lifecycle.py. That leaves the
# tests directories off sys.path, so they go on pythonpath for the
# helper modules, which carry their package's name and so never clash.
pythonpath = [{% for package in py %}"packages/{{ package.dir }}/tests", {% endfor %}"tests"]
# `-n auto` fans the suite across cores; `worksteal` because durations
# are uneven (footman measured it; the choice carries until we measure
# here).
addopts = "{{ slots['python.test.addopts'] | join(' ') }}"
"""

#: The native tools search upward from each file for their own
#: configuration, so a package of a native kind carries these files,
#: rendered from the record and judged by the drift gate; a package's
#: own additions ride the tool's inheritance, a deeper file with
#: ``InheritParentConfig``.
CLANG_FORMAT = """\
# Rendered by the template channel for the {{ kind }} kind; the gate keeps
# it matching its render. A file deeper in the tree with
# `BasedOnStyle: InheritParentConfig` carries this package's own lines.
BasedOnStyle: LLVM
IndentWidth: 4
ColumnLimit: 88
PointerAlignment: Left
"""

CLANG_TIDY = """\
# Rendered by the template channel for the {{ kind }} kind; the gate keeps
# it matching its render. A `.clang-tidy` deeper in the tree with
# `InheritParentConfig: true` carries this package's own lines.
#
# The families a gate can hold green from the first commit: the bug
# and portability checks, and the performance ones. readability-* is
# left out on purpose, since its opinions collide with clang-format's
# and with each other. A finding is an error, so the gate's verdict
# stays its exit code.
Checks: >
  bugprone-*,
  performance-*,
  portability-*,
  -bugprone-easily-swappable-parameters
WarningsAsErrors: "*"
HeaderFilterRegex: "^$"
"""

#: The editor: ruff formats python in the editor as in the gate.
RUFF_SETTINGS = """\
  "[python]": {
    "editor.defaultFormatter": "charliermarsh.ruff"
  },
"""
