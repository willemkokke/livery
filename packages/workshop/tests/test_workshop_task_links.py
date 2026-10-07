"""Task names link to their pages: the workspace's template, the site's, their order."""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.extensions.docs._tasks import link_task_pages
from livery.footman import Invocation
from livery.workshop._tasks import link_task_names

SITE = "https://docs.acme.example/home/"


def _workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    workspace: str = "",
    site: str | None = None,
    extensions: str = '["docs"]',
) -> None:
    (tmp_path / "workshop.toml").write_text(
        f'[workspace]\nname = "acme"\nextensions = {extensions}\n{workspace}'
        + ("" if site is None else f'[docs]\nsite-url = "{site}"\n'),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)


def _linked(inv: Invocation | None = None, *, docs_first: bool = False) -> Invocation:
    """*inv* after the base's hook and the docs extension's ran, in either order."""
    linked = inv or Invocation()
    hooks = [link_task_names, link_task_pages]
    for hook in reversed(hooks) if docs_first else hooks:
        hook(linked)
    return linked


# The refusals first: a template footman cannot fill links nothing, and
# the note says which key to fix.


@pytest.mark.parametrize("docs_first", [False, True])
def test_a_broken_workspace_template_is_named_and_links_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    docs_first: bool,
) -> None:
    # The site's template stays off too: the workspace's key was set.
    _workspace(
        tmp_path,
        monkeypatch,
        workspace='docs-url = "https://acme.example/{name}/"\n',
        site=SITE,
    )
    assert _linked(docs_first=docs_first).docs_url is None
    err = capsys.readouterr().err
    assert "workshop.toml [workspace] docs-url" in err and "{name}" in err


def test_an_empty_workspace_template_is_named_and_links_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _workspace(tmp_path, monkeypatch, workspace='docs-url = ""\n', site=SITE)
    assert _linked().docs_url is None
    assert "workshop.toml [workspace] docs-url" in capsys.readouterr().err


def test_a_template_that_is_no_string_is_left_to_the_judge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The contract's judge names `docs-url = 3` on the next verb's read;
    # the hook neither links it nor names it a second time.
    _workspace(tmp_path, monkeypatch, workspace="docs-url = 3\n", site=SITE)
    assert _linked().docs_url is None
    assert capsys.readouterr().err == ""


def test_a_site_address_with_a_brace_is_named_and_links_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _workspace(tmp_path, monkeypatch, site="https://acme.example/{site}/")
    assert _linked().docs_url is None
    err = capsys.readouterr().err
    assert "workshop.toml [docs] site-url" in err and "{site}" in err


def test_a_contract_that_does_not_parse_links_nothing_and_stops_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The hooks run on every command, fm sync among them: the contract's
    # own judge names a broken file, never these hooks.
    (tmp_path / "workshop.toml").write_text("[workspace\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert _linked().docs_url is None
    assert capsys.readouterr().err == ""


def test_outside_a_workspace_nothing_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert _linked().docs_url is None
    assert capsys.readouterr().err == ""


# Then the order: the configured template, the workspace's, the site's.


def test_a_configured_template_comes_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(
        tmp_path,
        monkeypatch,
        workspace='docs-url = "https://acme.example/tasks/{path}/"\n',
        site=SITE,
    )
    configured = Invocation(docs_url="https://configured.example/{path}/")
    assert _linked(configured).docs_url == "https://configured.example/{path}/"


@pytest.mark.parametrize("docs_first", [False, True])
def test_the_workspace_template_wins_over_the_site_whichever_hook_runs_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, docs_first: bool
) -> None:
    _workspace(
        tmp_path,
        monkeypatch,
        workspace='docs-url = "https://acme.example/tasks/{path}/"\n',
        site=SITE,
    )
    linked = _linked(docs_first=docs_first)
    assert linked.docs_url == "https://acme.example/tasks/{path}/"


def test_the_workspace_template_links_without_the_docs_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(
        tmp_path,
        monkeypatch,
        workspace='docs-url = "https://acme.example/reference/#{slug}"\n',
        extensions="[]",
    )
    inv = Invocation()
    link_task_names(inv)
    assert inv.docs_url == "https://acme.example/reference/#{slug}"


@pytest.mark.parametrize("site", [SITE, SITE.rstrip("/")])
def test_the_site_links_each_task_to_its_alias_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, site: str
) -> None:
    # The alias tree sits at tasks/ under the site, one redirect page per
    # task, named by its dash-joined address: the published addresses.
    _workspace(tmp_path, monkeypatch, site=site)
    assert _linked().docs_url == "https://docs.acme.example/home/tasks/{slug}/"
