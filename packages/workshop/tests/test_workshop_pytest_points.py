"""A test declares its points: the refusals first, then the selection per point.

The plugin rides the ``pytest11`` entry point, so the inner sessions carry it
without naming it; naming it again would register it twice.
"""

from __future__ import annotations

from importlib.metadata import entry_points

import pytest

from livery.workshop import _pytest_points

pytest_plugins = ["pytester"]


def test_a_marker_without_a_point_or_with_an_unknown_one_refuses(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.only_at()
        def test_bare():
            pass
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider")
    assert result.ret != 0
    result.stderr.fnmatch_lines(["*only_at names no point*"])
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.also_at("weekly")
        def test_odd():
            pass
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider")
    assert result.ret != 0
    result.stderr.fnmatch_lines(["*also_at names weekly, not a point*"])


def test_an_unknown_point_refuses(pytester: pytest.Pytester) -> None:
    pytester.makepyfile("def test_one():\n    pass\n")
    result = pytester.runpytest(
        "-p",
        "no:cacheprovider",
        "--workshop-point",
        "weekly",
    )
    assert result.ret != 0
    result.stderr.fnmatch_lines(["*'weekly' is not a CI point*"])


_SUITE = """
import pytest

def test_default():
    pass

@pytest.mark.only_at("nightly")
def test_nightly_only():
    pass

@pytest.mark.also_at("nightly")
def test_gate_and_nightly():
    pass

@pytest.mark.only_at("release", "nightly")
def test_release_or_nightly():
    pass
"""


def test_an_empty_selection_is_green_beyond_the_default_points_only(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(_pytest_points.POINT_VARIABLE, raising=False)
    # Refusal first: at the gate, a run with nothing to run keeps
    # pytest's own answer, exit 5.
    pytester.makepyfile("def test_default():\n    pass\n")
    result = pytester.runpytest("-p", "no:cacheprovider", "-k", "nothing_matches")
    assert result.ret == pytest.ExitCode.NO_TESTS_COLLECTED
    # The nightly with no test declaring it: nothing to run, green.
    result = pytester.runpytest("-p", "no:cacheprovider", "--workshop-point", "nightly")
    assert result.ret == pytest.ExitCode.OK
    result.stdout.fnmatch_lines(
        ["*no test declares the nightly point: nothing to run there, green*"]
    )
    # A test that does declare it runs, and a failure there is red.
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.only_at("nightly")
        def test_nightly():
            assert False
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider", "--workshop-point", "nightly")
    assert result.ret == pytest.ExitCode.TESTS_FAILED


@pytest.mark.parametrize(
    ("point", "expected"),
    [
        ("gate", {"test_default", "test_gate_and_nightly"}),
        ("merge", {"test_default", "test_gate_and_nightly"}),
        (
            "nightly",
            {"test_nightly_only", "test_gate_and_nightly", "test_release_or_nightly"},
        ),
        ("release", {"test_release_or_nightly"}),
    ],
)
def test_each_point_selects_its_own_tests(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    point: str,
    expected: set[str],
) -> None:
    pytester.makepyfile(_SUITE)
    monkeypatch.setenv(_pytest_points.POINT_VARIABLE, point)
    result = pytester.runpytest("-p", "no:cacheprovider", "-v")
    result.assert_outcomes(passed=len(expected), deselected=4 - len(expected))
    ran = {
        line.split("::")[1].split()[0]
        for line in result.outlines
        if "::test_" in line and "PASSED" in line
    }
    assert ran == expected


def test_the_option_wins_over_the_environment_and_gate_is_the_default(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytester.makepyfile(_SUITE)
    monkeypatch.delenv(_pytest_points.POINT_VARIABLE, raising=False)
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=2, deselected=2)
    monkeypatch.setenv(_pytest_points.POINT_VARIABLE, "gate")
    result = pytester.runpytest(
        "-p",
        "no:cacheprovider",
        "--workshop-point",
        "nightly",
    )
    result.assert_outcomes(passed=3, deselected=1)


def test_the_plugin_rides_the_pytest11_entry_point() -> None:
    names = {ep.value for ep in entry_points(group="pytest11")}
    assert "livery.workshop._pytest_points" in names
