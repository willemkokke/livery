"""The markdown renderer: pages, sites, flavors, determinism."""

from __future__ import annotations

from typing import Annotated, Literal

import pytest

from livery.footman import _manifest, markdown, registry, task
from livery.footman.params import doc
from livery.footman.registry import group


@pytest.fixture
def sample_tree():
    with registry.capture() as root:

        @task
        def build(target: Literal["web", "api"], fix: bool = False, jobs: int = 4):
            """Build one target.

            The long story about building.

            Args:
                target: which target to build
                fix: repair as we go
                jobs: parallel workers
            """

        docs = group("docs", help="Documentation")

        @docs.task
        def serve(port: Annotated[int, doc("port to bind")] = 8000):
            "Serve the docs."

    return _manifest.build_manifest(root)["tree"]


def test_page_whole_tree(sample_tree):
    page = markdown.render_page(sample_tree)
    assert page.startswith("# fm tasks\n")
    assert "## build" in page and "## docs" in page and "### docs.serve" in page
    assert "```text\nfm build <target> [--fix] [--jobs=INT]\n```" in page
    assert "| Parameter | Type | Default | Description |" in page
    assert (
        "| `<target>` | `web` \\| `api` | *required* | which target to build |" in page
    )
    assert "| `--jobs=INT` | int | `4` | parallel workers |" in page
    assert "The long story about building." in page
    assert "**Example:** `fm build web --fix`" in page
    assert "port to bind" in page  # the doc() marker text rides along


def test_page_scoped_to_group_and_task(sample_tree):
    group_page = markdown.render_page(sample_tree, path=("docs",))
    assert group_page.startswith("# docs\n")
    assert "Documentation" in group_page and "## docs.serve" in group_page
    assert "build" not in group_page  # scoped: the sibling task is absent

    task_page = markdown.render_page(sample_tree, path=("docs", "serve"))
    assert task_page.startswith("# docs.serve\n")
    assert "fm docs.serve [--port=INT]" in task_page


def test_page_heading_level_nests(sample_tree):
    page = markdown.render_page(sample_tree, path=("docs", "serve"), heading=3)
    assert page.startswith("### docs.serve\n")


def test_page_unknown_target_teaches(sample_tree):
    with pytest.raises(ValueError, match=r"no task or group named 'nope'"):
        markdown.render_page(sample_tree, path=("nope",))


def test_material_flavor_adds_anchors_and_admonition(sample_tree):
    page = markdown.render_page(sample_tree, flavor="material")
    assert "## build { #build }" in page
    assert "### docs.serve { #docs-serve }" in page
    assert "!!! example" in page
    plain = markdown.render_page(sample_tree)
    assert "{ #" not in plain and "!!!" not in plain  # plain stays portable


def test_prog_threads_through(sample_tree):
    page = markdown.render_page(sample_tree, prog="acme")
    assert page.startswith("# acme tasks\n")
    assert "acme build <target>" in page


def test_site_layout_and_links(sample_tree):
    files = markdown.render_site(sample_tree)
    assert set(files) == {"index.md", "build.md", "docs/index.md", "docs/serve.md"}
    index = files["index.md"]
    assert "[`build`](build.md)" in index
    assert "[`docs`](docs/index.md)" in index
    assert "Build one target." in index  # the task's help line in the table
    sub = files["docs/index.md"]
    assert "[`serve`](serve.md)" in sub  # links are relative to their folder
    assert files["docs/serve.md"].startswith("# docs.serve\n")


def test_site_scoped_to_task_is_one_file(sample_tree):
    files = markdown.render_site(sample_tree, path=("docs", "serve"))
    assert set(files) == {"serve.md"}


def test_render_is_deterministic(sample_tree):
    assert markdown.render_page(sample_tree) == markdown.render_page(sample_tree)
    assert markdown.render_site(sample_tree) == markdown.render_site(sample_tree)


def test_globals_table_mirrors_the_grammar():
    from livery.footman import _split

    text = markdown.globals_table()
    lines = text.splitlines()
    assert len(lines) == len(_split.GLOBALS) + 2  # header + rule + one row each
    for (name, alias, _kind, hint, _d, _help), line in zip(_split.GLOBALS, lines[2:]):
        assert f"`{name}" in line
        if alias:
            assert f"`{alias}`" in line
        if hint:
            # Attached, never spaced: the table shipped `--jobs N` while
            # `--help` printed `--jobs=N`, so the page taught a spelling
            # the splitter refuses with exit 64.
            assert f"{name}={hint}" in line, f"{name}: hint must be `=`-attached"


def test_globals_table_carries_defaults_from_the_grammar(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    text = markdown.globals_table()
    jobs = next(line for line in text.splitlines() if "--jobs=N" in line)
    colour = next(line for line in text.splitlines() if "--color=WHEN" in line)
    where = next(line for line in text.splitlines() if "--where=TASK" in line)
    # Same source as `--help`, so the page cannot say one thing while the
    # runner says another — the drift hand-written "(default: …)" prose had.
    # A computed default renders as its *phrase* here, never the build
    # machine's value: the published reference said `--jobs` defaults to `3`
    # and `--color` to `never` — the docs runner's core count and CI's
    # NO_COLOR, machine-specific answers dressed as the product's.
    assert "the machine's cores minus one" in jobs
    assert "default: auto (or what NO_COLOR/FORCE_COLOR says)" in colour
    import re

    assert not re.search(r"default: `\d+`", jobs)  # never a baked number
    assert "default" not in where
    assert "{prog}" not in text  # placeholders always filled


def test_a_computed_default_without_a_phrase_refuses_to_publish():
    # The phrase table lives beside the grammar so a new computed default
    # cannot ship undocumented: asked to speak symbolically about a name it
    # has no phrase for, the resolver refuses instead of baking a value.
    from livery.footman import _split

    monkey = dict(_split._COMPUTED_PHRASE)
    try:
        del _split._COMPUTED_PHRASE["--jobs"]
        with pytest.raises(KeyError, match="no symbolic phrase"):
            _split.global_default("--jobs", resolved=False)
    finally:
        _split._COMPUTED_PHRASE.clear()
        _split._COMPUTED_PHRASE.update(monkey)


def test_globals_table_speaks_the_brand():
    assert "help for fm" in markdown.globals_table()
    assert "help for acme" in markdown.globals_table(prog="acme")
