"""The workshop's isolation plugin: a test's patches are undone everywhere.

The plugin rides the ``pytest11`` entry point, so the inner sessions carry it
without a conftest.
"""

from __future__ import annotations

from importlib.metadata import entry_points

import pytest

from livery.workshop import _pytest_isolation

pytest_plugins = ("pytester",)


def _run(pytester: pytest.Pytester, *args: str) -> pytest.RunResult:
    """Run the inner session with a base temp of its own.

    Without one the inner session numbers a directory under the
    shared ``pytest-of-<user>`` base and prunes the oldest, which is
    the outer session's: a source a test wrote there is gone when the
    gate's coverage report reads it.
    """
    base = pytester.path / "basetemp"
    return pytester.runpytest("-p", "no:cacheprovider", f"--basetemp={base}", *args)


def test_the_plugin_rides_the_entry_point() -> None:
    names = {point.name for point in entry_points(group="pytest11")}
    assert "livery-workshop-isolation" in names


def test_a_module_first_imported_under_a_patch_keeps_the_original_after_it(
    pytester: pytest.Pytester,
) -> None:
    # A module that copies a function from another (`from owner import
    # name`) and is first imported while a test has the function
    # patched takes the patch; monkeypatch puts the owner back, and the
    # copy kept the patched function for every later test in the
    # worker. The next test finds the original in both places, under
    # whatever name the copy took, and a patch made twice goes back to
    # the first original. The cases left alone come first: a constant
    # of the module's own that is the very object a patch set, and an
    # attribute the patch added, which has no original to go back to.
    pytester.syspathinsert()
    pytester.makepyfile(
        acme_owner="FLAG = False\n\ndef root():\n    return 'real'\n",
        acme_copier=(
            "from acme_owner import extra, root\n"
            "from acme_owner import root as renamed\n"
            "\n"
            "FLAG = True\n"
        ),
    )
    pytester.makepyfile(
        """
        import acme_owner

        def test_patches(monkeypatch):
            monkeypatch.setattr(acme_owner, "FLAG", True)
            monkeypatch.setattr(acme_owner, "extra", [], raising=False)
            monkeypatch.setattr(acme_owner, "root", lambda: "first")
            monkeypatch.setattr("acme_owner.root", lambda: "patched")
            import acme_copier

            assert acme_copier.root() == "patched"
            assert acme_copier.renamed() == "patched"

        def test_after():
            import acme_copier

            assert acme_copier.FLAG is True
            assert acme_copier.extra == []
            assert not hasattr(acme_owner, "extra")
            assert acme_owner.root() == "real"
            assert acme_copier.root() == "real"
            assert acme_copier.renamed() == "real"
        """
    )
    result = _run(pytester)
    result.assert_outcomes(passed=2)


def test_no_fixture_of_the_plugin_asks_for_monkeypatch() -> None:
    # An autouse fixture that asks for `monkeypatch` sets it up before
    # the fixtures of plugins registered later, which reorders their
    # teardown: footman's environment restore then runs before
    # `monkeypatch` writes back a value the test had changed. The
    # plugin's guards read a test's `monkeypatch` from its resolved
    # fixtures instead.
    import inspect

    for name, value in vars(_pytest_isolation).items():
        marker = getattr(value, "_fixture_function_marker", None) or getattr(
            value, "_pytestfixturefunction", None
        )
        if marker is None:
            continue
        unwrap = getattr(value, "_get_wrapped_function", None)
        wrapped = getattr(value, "__wrapped__", None) or (
            unwrap() if unwrap is not None else value
        )
        assert "monkeypatch" not in inspect.signature(wrapped).parameters, name
