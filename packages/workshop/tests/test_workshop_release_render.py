"""The CI render, byte for byte, of three workspaces on every forge.

A workspace with a nanobind member renders every workflow file on
GitHub, Gitea and GitLab, a workspace with none renders its release,
and a workspace that lists ruff alone renders every workflow file with
no coverage entry at any point; each file is compared with its golden
under ``fixtures/``. The render reads the builtin points, and for the
native workspace the jobs nanobind's declaration adds, as the mount
adds them for a workspace whose packages hold nanobind, with the
runner named ``fm``:
the jobs an earlier mount in the worker contributed and the name a
host started the runner under are no part of a golden, so a golden is
the same bytes on every host. A render that moves fails here until
its golden moves with it, in the same change.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

import livery.footman as footman
from livery.workshop._ci_generate import generate
from workshop_docs_declared import jobs_restored
from workshop_goldens import FIXTURES, match

# The wheels job is nanobind's, added as the mount adds it.
from workshop_nanobind_declared import nanobind_jobs  # noqa: F401

FORGES = ("github", "gitea", "gitlab")

#: A nanobind member that lists nothing and names the runners its
#: wheels build on, beside a C++ library that lists cmake and conan.
NATIVE = {
    "native": (
        'kind = "python-nanobind"\nname = "acme-native"\n\n[nanobind]\n'
        'wheel-platforms = ["ubuntu-latest", "macos-latest"]\n'
    ),
    "lib": 'kind = "cpp-conan"\nname = "acme-lib"\nextensions = ["cmake", "conan"]\n',
}

#: One python member, whose one wheel installs everywhere.
PURE = {"pure": 'kind = "python"\nname = "acme-pure"\n'}


@pytest.fixture(autouse=True)
def builtin_points(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Render the builtin points alone, under the runner name ``fm``."""
    from livery.workshop import _points

    monkeypatch.setattr(footman, "prog", lambda: "fm")
    with jobs_restored():
        _points.restore(_points.snapshot(initial=True))
        yield


def _workspace(
    root: Path, forge: str, members: dict[str, str], extensions: str = ""
) -> Path:
    """A workspace at *root* on *forge* with *members*, each directory to its contract.

    The check runners are not the wheel platforms, so a golden shows
    which of the two each job fans out to. *extensions* is the list's
    entries, spelled as the contract spells them.
    """
    root.mkdir()
    (root / "workshop.toml").write_text(
        f"[workspace]\nextensions = [{extensions}]\n\n[forge]\n"
        f'kind = "{forge}"\nowner = "acme"\n\n[ci]\n'
        'runners = ["ubuntu-latest", "windows-latest"]\n'
        'python-versions = ["3.13", "3.14"]\n'
    )
    (root / "pyproject.toml").write_text(
        '[project]\nname = "acme"\nrequires-python = ">=3.11"\n'
    )
    for directory, contract in members.items():
        member = root / "packages" / directory
        (member / "src").mkdir(parents=True)
        (member / "workshop.toml").write_text(contract)
        (member / "pyproject.toml").write_text(
            f'[project]\nname = "acme-{directory}"\n'
        )
    return root


def _workflows(root: Path) -> dict[str, str]:
    """Each workflow file the render writes for *root*, by its file name."""
    return {
        Path(path).name: text
        for path, text in generate(root).items()
        if path == ".gitlab-ci.yml"
        or path.startswith((".github/workflows/", ".gitea/workflows/"))
    }


@pytest.mark.parametrize("forge", FORGES)
def test_a_pure_workspace_releases_through_the_publish_job_alone(
    tmp_path: Path, forge: str
) -> None:
    # No package holds nanobind, so the mount adds no wheels job and the
    # publish job collects nothing.
    root = _workspace(tmp_path / "workspace", forge, PURE)
    # GitLab's one document carries the release's jobs beside every other.
    name = ".gitlab-ci.yml" if forge == "gitlab" else "release.yml"
    rendered = {name: _workflows(root)[name]}
    match(rendered, FIXTURES / "release-pure" / forge, tmp_path / "render")


@pytest.mark.usefixtures("nanobind_jobs")
@pytest.mark.parametrize("forge", FORGES)
def test_a_native_workspace_renders_every_workflow_as_its_golden(
    tmp_path: Path, forge: str
) -> None:
    root = _workspace(tmp_path / "workspace", forge, NATIVE)
    match(_workflows(root), FIXTURES / "release-native" / forge, tmp_path / "render")


@pytest.mark.parametrize("forge", FORGES)
def test_a_workspace_listing_ruff_alone_renders_no_coverage_entry(
    tmp_path: Path, forge: str
) -> None:
    from livery.workshop import _points
    from livery.workshop._extensions import declaration, register_jobs

    root = _workspace(tmp_path / "workspace", forge, PURE, '"ruff"')
    # The mount adds the jobs of what the workspace lists: ruff alone.
    found = declaration("ruff")
    assert found is not None
    register_jobs("ruff", found.additions.jobs)
    for point in _points.point_by_name(root):
        for job in _points.jobs_of(root, point):
            tasks = [entry.task for entry in _points.entries_for(root, point, job)]
            assert not [task for task in tasks if task.startswith("coverage.")]
    rendered = _workflows(root)
    assert not [name for name, text in rendered.items() if "coverage." in text]
    match(rendered, FIXTURES / "ruff-alone" / forge, tmp_path / "render")
