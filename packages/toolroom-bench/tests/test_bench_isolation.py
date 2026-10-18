"""Every session with the bench installed runs apart from the machine's runner state.

The bench's tasks read the machine's provisioned prefix and its store
through the runner's data, cache and config directories. footman's
pytest plugin, which the bench installs with footman, points each of
them away for every session: in the workspace, and in a release leg
that installs the bench alone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest_plugins = ("pytester",)

#: The runner's directories the bench's tasks read the machine through.
DIRECTORIES = ("FOOTMAN_DATA_DIR", "FOOTMAN_CACHE_DIR", "FOOTMAN_CONFIG_DIR")


def test_every_directory_the_bench_reads_points_away_in_a_session(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Cleared, the three are the session's to set; the cache home moves
    # into this test's directory, so the session's home, which lands in
    # the cache the session starts with, lands there and nowhere else.
    for name in DIRECTORIES:
        monkeypatch.delenv(name, raising=False)
    cache_home = tmp_path / "cache-home"
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))
    pytester.makepyfile(
        f"""
        import os
        from pathlib import Path

        def test_away():
            for name in {DIRECTORIES!r}:
                value = Path(os.environ[name])
                assert value.is_dir() and value.is_relative_to({str(cache_home)!r}), name
        """
    )
    base = pytester.path / "basetemp"
    result = pytester.runpytest("-p", "no:cacheprovider", f"--basetemp={base}")
    result.assert_outcomes(passed=1)
