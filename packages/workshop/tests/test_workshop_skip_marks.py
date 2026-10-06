"""A proof that a skipped gate is an event on the timeline, not silence."""

from __future__ import annotations

import json
import textwrap

from livery.footman.testing import Runner

TASKS = textwrap.dedent(
    """
    import livery.footman.api as footman
    from livery.footman.api import task
    from livery.footman._compose import plugin

    plugin("footman.profile")

    from livery.workshop._quality import say_skipped

    @task
    def gated():
        say_skipped("gate: tree abc123abc123 proved green by fm check; skipping")
    """
)


def test_a_skip_is_an_event_on_the_timeline(tmp_path, monkeypatch, capsys):
    """A gate that skipped and a gate that flew look alike without this.

    The line is what a log has always shown. The mark is what an account of
    the run shows, so "why was this leg quick" is answered inside the
    picture.
    """
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "tasks.py"
    src.write_text(TASKS)
    result = Runner().invoke("--profile gated", tasks=src)
    assert result.ok, result.stderr
    assert "proved green by fm check; skipping" in result.stdout
    events = json.loads((tmp_path / "fm-profile.json").read_text())["traceEvents"]
    marks = [e for e in events if e.get("cat") == "mark"]
    assert [e["name"] for e in marks] == [
        "skipped: gate: tree abc123abc123 proved green by fm check; skipping"
    ]
    assert marks[0]["ph"] == "i" and "dur" not in marks[0]


def test_a_skip_outside_a_run_prints_and_nothing_more(capsys):
    """The helper is called from verbs a test drives directly too."""
    from livery.workshop._quality import say_skipped

    say_skipped("nothing affected: only prose changed")
    assert "nothing affected: only prose changed" in capsys.readouterr().out
