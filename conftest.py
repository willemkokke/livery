"""Fixtures for every package: no signing, and no mount outlives its test.

The developer's git signing never reaches the suite, and what a mount
of the workshop's extensions writes is put back after every test.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

#: pytest's own plugin for running an inner pytest, which the workshop's
#: plugin tests drive; pytest accepts this declaration in the root
#: conftest alone.
pytest_plugins = ("pytester",)


@pytest.fixture(autouse=True)
def _unsigned_commits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn commit and tag signing off for every git the suite spawns.

    The seeds and the verbs under test commit with the developer's
    global config. A `commit.gpgsign` there hands every one of those
    commits to the signing agent, which fails when it is locked and
    waits for a person when it is not; twelve workers then wait
    together. The overlay reaches every git child through the
    documented `GIT_CONFIG_COUNT` environment; a helper that adds keys
    of its own appends after the ones already set.
    """
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "commit.gpgsign")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "false")
    monkeypatch.setenv("GIT_CONFIG_KEY_1", "tag.gpgsign")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", "false")


@pytest.fixture(autouse=True)
def _mounts_restored() -> Iterator[None]:
    """Put back, after every test, what a mount of the workshop's extensions writes.

    A mount registers the listed extensions' steps, adds their jobs to
    the builtin points, joins the points' jobs with their entries and
    judges, and records what it mounted; a run of the runner keeps its
    task tree. Each is process-global, so a test that mounts would hand
    its extensions to every later test on its worker, and the order a
    worker runs its tests in would decide another test's verdict. Each
    is taken before the test and put back after it. A package whose
    tests run without the workshop installed, as a release leg's may,
    has none to restore.
    """
    try:
        from livery.workshop import _extensions, _points, _steps
    except ImportError:
        yield
        return
    steps, jobs, mounts = _steps.snapshot(), _points.snapshot(), _extensions.snapshot()
    yield
    _steps.restore(steps)
    _points.restore(jobs)
    _extensions.restore(mounts)
