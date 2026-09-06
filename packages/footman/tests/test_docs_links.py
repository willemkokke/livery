"""Task names link to their docs: the template, the surfaces, the gating."""

from __future__ import annotations

import json

import pytest

from livery.footman import _describe
from livery.footman.registry import Group
from livery.footman.testing import Runner

OSC = "\033]8;;"


# --- the pieces ---------------------------------------------------------------


def test_link_wraps_only_when_dressed():
    url = "https://docs.example.dev/tasks/build/"
    linked = _describe.link("build", url, True)
    assert linked == f"\033]8;;{url}\033\\build\033]8;;\033\\"
    assert _describe.link("build", url, False) == "build"  # piped: plain
    assert _describe.link("build", None, True) == "build"  # unconfigured


def test_docs_url_for_expands_both_placeholders():
    per_page = "https://d.dev/tasks/{path}/"
    anchored = "https://d.dev/reference/#{slug}"
    assert (
        _describe.docs_url_for(per_page, "docs.build")
        == "https://d.dev/tasks/docs/build/"
    )
    assert (
        _describe.docs_url_for(anchored, "docs.build")
        == "https://d.dev/reference/#docs-build"
    )
    assert _describe.docs_url_for(per_page, "build") == "https://d.dev/tasks/build/"
    assert _describe.docs_url_for(None, "build") is None
    assert _describe.docs_url_for(per_page, "") is None


def test_docs_url_error_refuses_the_broken_shapes():
    assert _describe.docs_url_error("https://d.dev/tasks/{path}/") is None
    assert _describe.docs_url_error("https://d.dev/#{slug}") is None
    assert _describe.docs_url_error("https://d.dev/plain") is None  # no holes is fine
    error = _describe.docs_url_error("https://d.dev/{page}/")
    assert error is not None and "{page}" in error and "{path}" in error
    assert _describe.docs_url_error(7) is not None
    assert _describe.docs_url_error("   ") is not None


# --- the surfaces, end to end -------------------------------------------------


@pytest.fixture
def linked_project(tmp_path):
    (tmp_path / "tasks.py").write_text(
        "from footman import group, task\n\n"
        "@task\n"
        "def build():\n"
        '    """Build it."""\n'
        "\n"
        "docs = group('docs', help='Documentation')\n\n"
        "@docs.task\n"
        "def serve():\n"
        '    """Serve them."""\n'
    )
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='x'\n[tool.footman]\n"
        'docs_url = "https://docs.example.dev/tasks/{path}/"\n'
    )
    return tmp_path


def test_list_names_carry_hyperlinks_when_dressed(linked_project):
    result = Runner().invoke("--color=always --list", cwd=linked_project)
    assert result.ok, result.stderr
    assert f"{OSC}https://docs.example.dev/tasks/build/" in result.stdout
    assert f"{OSC}https://docs.example.dev/tasks/docs/serve/" in result.stdout


def test_tree_links_tasks_and_groups(linked_project):
    result = Runner().invoke("--color=always --tree", cwd=linked_project)
    assert result.ok, result.stderr
    assert f"{OSC}https://docs.example.dev/tasks/build/" in result.stdout
    # The group row links to its own page under the same scheme.
    assert f"{OSC}https://docs.example.dev/tasks/docs/" in result.stdout


def test_piped_listings_stay_plain(linked_project):
    for line in ("--list", "--tree", "--help build"):
        result = Runner().invoke(line, cwd=linked_project)
        assert result.ok, result.stderr
        assert "\033" not in result.stdout  # no colour, no OSC 8


def test_task_help_prints_the_url_as_visible_text(linked_project):
    # Visible even undressed: a terminal without hyperlinks (or a pipe)
    # still gets something to copy.
    result = Runner().invoke("--help docs.serve", cwd=linked_project)
    assert result.ok, result.stderr
    assert "docs: https://docs.example.dev/tasks/docs/serve/" in result.stdout


def test_group_help_prints_its_own_docs_line(linked_project):
    result = Runner().invoke("--help docs", cwd=linked_project)
    assert result.ok, result.stderr
    assert "docs: https://docs.example.dev/tasks/docs/" in result.stdout


def test_json_rows_carry_docs_url(linked_project):
    result = Runner().invoke("--json docs.serve", cwd=linked_project)
    assert result.ok, result.stderr
    envelope = json.loads(result.stdout)
    (item,) = [i for i in envelope["items"] if i.get("task") == "docs.serve"]
    assert item["docs_url"] == "https://docs.example.dev/tasks/docs/serve/"


def test_an_unknown_placeholder_is_refused_by_name(linked_project):
    (linked_project / "pyproject.toml").write_text(
        "[project]\nname='x'\n[tool.footman]\n"
        'docs_url = "https://docs.example.dev/{page}/"\n'
    )
    result = Runner().invoke("--list", cwd=linked_project)
    assert not result.ok
    assert "{page}" in result.stderr and "{path}" in result.stderr


def test_without_the_key_nothing_changes(tmp_path):
    (tmp_path / "tasks.py").write_text(
        'from footman import task\n\n@task\ndef build():\n    """Build."""\n'
    )
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    dressed = Runner().invoke("--color=always --list", cwd=tmp_path)
    assert dressed.ok, dressed.stderr
    assert OSC not in dressed.stdout
    helped = Runner().invoke("--help build", cwd=tmp_path)
    assert helped.ok and "docs:" not in helped.stdout


def test_an_in_memory_tree_never_inherits_a_template(linked_project):
    # A cwd invocation installs the template; the next Group-mode invocation
    # has no config at all, so it must not leak in.
    assert Runner().invoke("--list", cwd=linked_project).ok
    reg = Group("root")

    @reg.task
    def build():
        """Build."""

    result = Runner().invoke("--color=always --list", tasks=reg)
    assert result.ok, result.stderr
    assert OSC not in result.stdout
