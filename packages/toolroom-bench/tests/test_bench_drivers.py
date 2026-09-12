"""The drivers, read through the bench, against the shipped handles."""

from __future__ import annotations

import os

import pytest

from livery.toolroom import tools


def test_manual_source_driver_is_never_extracted():
    from livery.toolroom.bench import _drivers

    bash = _drivers.find("bash")
    assert bash is not None and bash.source == "manual"
    assert _drivers.extract(bash).verbs == ()  # hand-written stub, never read


def test_one_parser_serves_the_extractor_and_the_bridge():
    """A stub's recorded version and a task's `installed_version()` may
    disagree about *which binary* they asked — `_resolve` prefers a Homebrew
    keg for host-read tools — but never about how a version string reads.
    """
    from livery.toolroom.bench import _drivers

    assert _drivers.version.__globals__  # imported lazily inside the function
    for text in ("git version 2.55.0", "gh version 2.96.0 (2026-01-01)"):
        assert tools.read_version(text)


def test_click_extraction_reads_the_real_negations():
    """Click states a negatable flag as opts + secondary_opts — the fact
    `off` needs and cannot infer. This is the extractor that fills the
    table, run against the real mkdocs.
    """
    pytest.importorskip("mkdocs")
    # An optional tool: importorskip above guards the run, and the
    # type-check job installs the shots group, not every tool footman
    # can drive.
    import mkdocs.__main__ as entry

    from livery.toolroom.bench._toolspec import from_click

    spec = from_click(entry.cli, name="mkdocs")
    assert spec.name == "mkdocs" and spec.in_process is True
    assert {"build", "serve", "gh_deploy"} <= {v.name for v in spec.verbs}
    assert spec.negations() == {
        "clean": "--dirty",
        "use_directory_urls": "--no-directory-urls",
    }
    build = next(v for v in spec.verbs if v.name == "build")
    clean = next(o for o in build.options if o.name == "clean")
    assert clean.type_name == "bool" and clean.negation == "--dirty"
    assert clean.help  # the tool's own words, for the stub's docstring


def test_negation_table_matches_what_the_tools_say():
    """The committed table is a cache of what the tools state; if a tool
    changes its spelling, this fails rather than emitting a flag the tool
    will reject.
    """
    pytest.importorskip("mkdocs")
    # An optional tool: importorskip above guards the run, and the
    # type-check job installs the shots group, not every tool footman
    # can drive.
    import mkdocs.__main__ as entry

    from livery.toolroom.bench._toolspec import from_click
    from livery.toolroom.tools import _NEGATIONS

    assert from_click(entry.cli, name="mkdocs").negations() == _NEGATIONS["mkdocs"]


@pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason="the hand-written _WRAPPERS table is curated against the maintainer's "
    "installed tools; CI runs different tool versions, so accuracy can't be "
    "verified here until CI generates the table too (post-1.0)",
)
def test_wrappers_table_matches_what_the_tools_declare():
    # The runtime table is hand-written; this mirrors `fm toolroom.audit`,
    # so drift fails fast in the local `fm check` gate. Skipped in CI (marker
    # above): CI's tool versions differ from the curated table.
    from livery.toolroom.bench import _drivers
    from livery.toolroom.tools import _WRAPPERS

    for driver in _drivers.DRIVERS:
        if driver.base or not _drivers.installed(driver):
            continue
        declared = _drivers.extract(driver).wrappers()
        assert declared == _WRAPPERS.get(driver.name, frozenset()), driver.name
