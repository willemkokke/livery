"""Select the tests a CI point runs: a test declares where it runs.

Registered through livery-workshop's ``pytest11`` entry point, so every
pytest this venv starts carries it. A test without a marker runs at
the default points, the gate and the merge; ``only_at`` names the
points a test runs at and nowhere else; ``also_at`` adds points to
the default ones. The point comes from the job runner's environment
(``WORKSHOP_POINT``, set for every entry it spawns) or from
``--workshop-point`` on the command line, and is the gate when
neither says. Tests outside the point's selection are deselected,
never skipped, so they leave no noise in the summary. A point beyond
the default ones that selects no test at all is green: the nightly
runs the whole check with its own tests selected in, and a workspace
that declares none has nothing to run there, which is not a failure.
"""

from __future__ import annotations

import os

import pytest

#: The variable the job runner sets for every entry it spawns, naming
#: the point the job runs at.
POINT_VARIABLE = "WORKSHOP_POINT"

#: The points a test runs at when it names none.
DEFAULT_POINTS = frozenset({"gate", "merge"})

#: Every point a marker may name.
POINTS = frozenset({"gate", "merge", "nightly", "release"})


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add ``--workshop-point``, the point to select for on a local run."""
    parser.addoption(
        "--workshop-point",
        default=None,
        help="select the tests of this CI point (gate, merge, nightly, release);"
        f" {POINT_VARIABLE} in the environment says the same",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Register the two markers, so an unregistered-marker warning never fires."""
    config.addinivalue_line(
        "markers", "only_at(*points): the test runs at the named CI points only"
    )
    config.addinivalue_line(
        "markers",
        "also_at(*points): the test runs at the named CI points as well as the"
        " default ones (gate, merge)",
    )


def current_point(config: pytest.Config) -> str:
    """The point this run selects for: the option, else the environment, else gate."""
    option = config.getoption("--workshop-point", default=None)
    point = str(option or os.environ.get(POINT_VARIABLE) or "gate")
    if point not in POINTS:
        raise pytest.UsageError(
            f"{point!r} is not a CI point; the points are {', '.join(sorted(POINTS))}"
        )
    return point


def points_of(item: pytest.Item) -> frozenset[str]:
    """The points *item* runs at, from its markers.

    Raises:
        pytest.UsageError: When a marker names no point or an unknown one.
    """
    only = item.get_closest_marker("only_at")
    also = item.get_closest_marker("also_at")
    for marker in (only, also):
        if marker is None:
            continue
        named = {str(point) for point in marker.args}
        if not named:
            raise pytest.UsageError(f"{item.nodeid}: {marker.name} names no point")
        unknown = sorted(named - POINTS)
        if unknown:
            raise pytest.UsageError(
                f"{item.nodeid}: {marker.name} names {', '.join(unknown)}, not a"
                f" point; the points are {', '.join(sorted(POINTS))}"
            )
    if only is not None:
        return frozenset(str(point) for point in only.args)
    if also is not None:
        return DEFAULT_POINTS | frozenset(str(point) for point in also.args)
    return DEFAULT_POINTS


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Keep the tests of the current point; deselect the rest."""
    point = current_point(config)
    kept: list[pytest.Item] = []
    dropped: list[pytest.Item] = []
    for item in items:
        (kept if point in points_of(item) else dropped).append(item)
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = kept


def pytest_sessionfinish(
    session: pytest.Session, exitstatus: int | pytest.ExitCode
) -> None:
    """Turn "no tests collected" into green at a point beyond the default ones.

    pytest exits 5 when nothing ran. At the gate and the merge that
    stands, since a workspace without tests is not proved by an
    empty run. At the nightly or the release point an empty selection
    is the declared state: no test asked for the point, so the point
    has nothing to run and says so.
    """
    if exitstatus != pytest.ExitCode.NO_TESTS_COLLECTED:
        return
    point = current_point(session.config)
    if point in DEFAULT_POINTS:
        return
    session.exitstatus = pytest.ExitCode.OK
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(
            f"no test declares the {point} point: nothing to run there, green"
        )
