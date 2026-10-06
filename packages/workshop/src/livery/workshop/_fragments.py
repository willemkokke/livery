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
        if fragment.kind:
            continue
        raise ValueError(
            f"check {check!r}: a fragment for {fragment.file!r} names a file the"
            f" render does not write; the project files are"
            f" {', '.join(PROJECT_FILES)}, and a fragment that names a kind is"
            " rendered in each package of it"
        )


def _render(text: str, data: dict[str, Any]) -> str:
    """*text* rendered with *data*."""
    import json

    import minijinja

    # Lenient on an undefined name, so a render over partial data draws
    # the file the full data would, less the missing parts; the drift
    # gate reads the full data and judges the bytes.
    environment = minijinja.Environment(
        keep_trailing_newline=True, undefined_behavior="lenient"
    )
    plain = json.loads(json.dumps(data, default=str))
    return environment.render_str(text, **plain)


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


def package_files() -> tuple[str, ...]:
    """The per-package files the registered checks' fragments render, sorted.

    A fragment that names a kind is rendered in each package of that
    kind's chain, so the files a package of a native kind carries are
    the registered checks' to say, and leave with them.
    """
    from livery.workshop._checks import checks_by_name

    return tuple(
        sorted(
            {
                fragment.file
                for record in checks_by_name().values()
                for fragment in record.fragments
                if fragment.kind
            }
        )
    )


def package_fragment(kind_name: str, file: str) -> tuple[str, str] | None:
    """The fragment text a package of *kind_name* renders as *file*, and its check.

    The nearest kind down the chain wins; None when no check has one,
    which is what a withdrawn check looks like to the apply.
    """
    from livery.workshop._checks import checks_by_name
    from livery.workshop._kinds import kind_chain, kind_names

    if kind_name not in kind_names():
        return None
    for kind in reversed(kind_chain(kind_name)):
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


TESTS = r"""[tool.coverage.run]
# The tests measure, and every process they start, every
# {{ runner_prog }} child a test spawns included: inside CI the test
# runner arms COVERAGE_PROCESS_START in pytest's environment, the .pth
# coverage installs starts the meter in every python under it, and the
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
testpaths = [{% if packages %}"packages"{% if root_tests %}, {% endif %}{% endif %}{% for path in root_tests %}"{{ path }}"{% if not loop.last %}, {% endif %}{% endfor %}]
# Test modules are named by their path (`--import-mode=importlib`), so
# two packages may both have a tests/test_lifecycle.py. That leaves the
# tests directories off sys.path, so they go on pythonpath for the
# helper modules, which carry their package's name and so never clash.
pythonpath = [{% for package in py %}"packages/{{ package.dir }}/tests", {% endfor %}{% for path in root_tests %}"{{ path }}"{% if not loop.last %}, {% endif %}{% endfor %}]
# `-n auto` fans the suite across cores; `worksteal` because durations
# are uneven (footman measured it; the choice carries until we measure
# here).
addopts = "{{ slots['python.test.addopts'] | join(' ') }}"
"""
