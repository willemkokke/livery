"""The maintenance family: footman's collect, then every plugin's sweeper."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from livery.footman import _gc
from livery.footman.tasks import maintenance


def test_a_raising_or_unloadable_sweeper_never_stops_the_rest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Refusals first: one sweeper's fault is named; the others still run,
    # in name order, with the facts every sweeper gets.
    seen: list[dict[str, Any]] = []

    def good(**facts: Any) -> list[str]:
        seen.append(facts)
        return ["one line", "two lines"]

    def bad(**facts: Any) -> list[str]:
        raise RuntimeError("the sweeper broke")

    monkeypatch.setattr(
        maintenance,
        "_sweepers",
        lambda: [
            ("acme.bad", bad),
            ("acme.good", good),
            ("acme.gone", "ImportError: no module"),
        ],
    )
    monkeypatch.setattr(
        maintenance._paths, "footman_cache_dir", lambda: tmp_path / "cache"
    )
    monkeypatch.setattr(
        maintenance._paths, "footman_data_dir", lambda: tmp_path / "data"
    )
    monkeypatch.setattr(
        maintenance._paths, "footman_config_dir", lambda: tmp_path / "config"
    )
    lines = maintenance.run_sweepers(dry_run=True, unattended=False)
    assert lines[0].startswith("  cache: ") and "nothing removed" in lines[0]
    assert (
        "  acme.bad: raised RuntimeError: the sweeper broke;"
        " the other sweepers still ran" in lines
    )
    assert "  acme.good: one line" in lines and "  acme.good: two lines" in lines
    assert "  acme.gone: could not load (ImportError: no module); skipped" in lines
    (facts,) = seen
    assert facts["dry_run"] is True and facts["unattended"] is False
    assert (
        facts["data_dir"] == tmp_path / "data"
        and facts["config_dir"] == tmp_path / "config"
    )
    assert facts["cache_dir"] == tmp_path / "cache" and facts["now"].tzinfo is not None


def test_the_daily_child_runs_the_sweepers_unattended_and_silently(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[dict[str, Any]] = []

    def sweeper(**facts: Any) -> list[str]:
        seen.append(facts)
        raise RuntimeError("after recording")

    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(maintenance, "_sweepers", lambda: [("acme", sweeper)])
    monkeypatch.setattr(maintenance._paths, "footman_cache_dir", lambda: cache)
    monkeypatch.setattr(
        maintenance._paths, "footman_data_dir", lambda: tmp_path / "data"
    )
    monkeypatch.setattr(
        maintenance._paths, "footman_config_dir", lambda: tmp_path / "config"
    )
    monkeypatch.setattr(sys, "argv", ["gc", str(cache), ""])
    _gc.main()
    assert capsys.readouterr().out == ""
    (facts,) = seen
    assert facts["unattended"] is True and facts["dry_run"] is False


def test_a_sweep_without_sweepers_still_collects_the_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(maintenance, "_sweepers", list)
    monkeypatch.setattr(maintenance._paths, "footman_cache_dir", lambda: cache)
    monkeypatch.setattr(
        maintenance._paths, "footman_data_dir", lambda: tmp_path / "data"
    )
    monkeypatch.setattr(
        maintenance._paths, "footman_config_dir", lambda: tmp_path / "config"
    )
    lines = maintenance.run_sweepers(dry_run=False, unattended=False)
    assert lines == [f"  cache: 0 file(s) collected from {cache}"]
