"""The release render, byte for byte: a workspace with a native member, and a pure one.

A workspace with a nanobind member renders every workflow file on
GitHub, Gitea and GitLab, and a workspace with none renders its
release; each file is compared with its golden under ``fixtures/``.
The render reads the builtin points alone, with the runner named
``fm``: the jobs an earlier mount in the worker contributed and the
name a host started the runner under are no part of a golden, so a
golden is the same bytes on every host. A render that moves fails
here until its golden moves with it, in the same change.
"""

from __future__ import annotations

import difflib
from collections.abc import Iterator
from pathlib import Path

import pytest

import livery.footman as footman
from livery.workshop._ci_generate import generate
from workshop_docs_declared import jobs_restored

#: The goldens, one directory per workspace and forge.
FIXTURES = Path(__file__).resolve().parent / "fixtures"

FORGES = ("github", "gitea", "gitlab")

#: A nanobind member that lists nothing and names the runners its
#: wheels build on, beside a C++ library that lists cmake and conan.
NATIVE = {
    "native": (
        'kind = "python-nanobind"\nname = "acme-native"\n\n[ci]\n'
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
        _points._CONTRIBUTED_JOBS.clear()
        _points._JOINED_JOBS.clear()
        yield


def _workspace(root: Path, forge: str, members: dict[str, str]) -> Path:
    """A workspace at *root* on *forge* with *members*, each directory to its contract.

    The check runners are not the wheel platforms, so a golden shows
    which of the two each job fans out to.
    """
    root.mkdir()
    (root / "workshop.toml").write_text(
        "[workspace]\nextensions = []\n\n[forge]\n"
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


def _match(rendered: dict[str, str], goldens: Path, scratch: Path) -> None:
    """Fail unless *rendered* is the files in *goldens*, name for name, byte for byte.

    Each file that moved is written to *scratch*, the file a deliberate
    render change copies over its golden, and its difference is part
    of the failure.
    """
    where = goldens.relative_to(FIXTURES).as_posix()
    moved: list[str] = []
    for name, text in sorted(rendered.items()):
        golden = goldens / name
        expected = golden.read_bytes().decode("utf-8") if golden.is_file() else ""
        if text == expected:
            continue
        scratch.mkdir(exist_ok=True)
        (scratch / name).write_bytes(text.encode("utf-8"))
        moved.append(
            "".join(
                difflib.unified_diff(
                    expected.splitlines(keepends=True),
                    text.splitlines(keepends=True),
                    f"{where}/{name}",
                    f"{scratch / name}",
                )
            )
        )
    assert not moved, (
        f"the render moved under {where}; a deliberate change copies the files"
        f" in {scratch} over the goldens in the same change:\n" + "\n".join(moved)
    )
    kept = sorted(path.name for path in goldens.iterdir())
    assert kept == sorted(rendered), f"{where} holds goldens no render writes"


@pytest.mark.parametrize("forge", FORGES)
def test_a_pure_workspace_releases_through_the_publish_job_alone(
    tmp_path: Path, forge: str
) -> None:
    root = _workspace(tmp_path / "workspace", forge, PURE)
    # GitLab's one document carries the release's jobs beside every other.
    name = ".gitlab-ci.yml" if forge == "gitlab" else "release.yml"
    rendered = {name: _workflows(root)[name]}
    _match(rendered, FIXTURES / "release-pure" / forge, tmp_path / "render")


@pytest.mark.parametrize("forge", FORGES)
def test_a_native_workspace_renders_every_workflow_as_its_golden(
    tmp_path: Path, forge: str
) -> None:
    root = _workspace(tmp_path / "workspace", forge, NATIVE)
    _match(_workflows(root), FIXTURES / "release-native" / forge, tmp_path / "render")
