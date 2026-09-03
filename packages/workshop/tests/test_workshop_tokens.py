"""The token surface: two names, host-qualified, ladder-ordered."""

from __future__ import annotations

import pytest

from livery.workshop._tokens import admin_token, forge_token, host_qualifier


def test_the_qualifier_spells_host_and_port() -> None:
    assert host_qualifier("gitea", "https://forge.example.com:3000") == (
        "FORGE_EXAMPLE_COM_3000"
    )
    assert host_qualifier("github", "") == "GITHUB_COM"
    assert host_qualifier("gitlab", "") == "GITLAB_COM"
    # Gitea has no public default: its contract always names the server.
    assert host_qualifier("gitea", "") == ""


def test_two_instances_of_one_kind_never_collide(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FORGE_TOKEN__LOCALHOST_3000", "dev-rig")
    monkeypatch.setenv("FORGE_TOKEN__FORGE_PROD_EXAMPLE", "production")
    monkeypatch.delenv("FORGE_TOKEN", raising=False)
    assert forge_token("gitea", "http://localhost:3000") == (
        "dev-rig",
        "FORGE_TOKEN__LOCALHOST_3000",
    )
    assert forge_token("gitea", "https://forge.prod.example") == (
        "production",
        "FORGE_TOKEN__FORGE_PROD_EXAMPLE",
    )


def test_the_bare_name_is_the_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FORGE_TOKEN__GITHUB_COM", raising=False)
    monkeypatch.setenv("FORGE_TOKEN", "everyday")
    assert forge_token("github", "") == ("everyday", "FORGE_TOKEN")
    monkeypatch.setenv("FORGE_TOKEN__GITHUB_COM", "qualified")
    assert forge_token("github", "") == ("qualified", "FORGE_TOKEN__GITHUB_COM")


def test_the_admin_ladder_falls_to_the_everyday_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "FORGE_ADMIN_TOKEN",
        "FORGE_ADMIN_TOKEN__GITHUB_COM",
        "FORGE_TOKEN__GITHUB_COM",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FORGE_TOKEN", "everyday")
    assert admin_token("github", "") == ("everyday", "FORGE_TOKEN")
    monkeypatch.setenv("FORGE_ADMIN_TOKEN", "admin")
    assert admin_token("github", "") == ("admin", "FORGE_ADMIN_TOKEN")
    monkeypatch.setenv("FORGE_ADMIN_TOKEN__GITHUB_COM", "host-admin")
    assert admin_token("github", "") == (
        "host-admin",
        "FORGE_ADMIN_TOKEN__GITHUB_COM",
    )


def test_nothing_resolved_is_two_empties(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "FORGE_TOKEN",
        "FORGE_TOKEN__GITHUB_COM",
        "FORGE_ADMIN_TOKEN",
        "FORGE_ADMIN_TOKEN__GITHUB_COM",
    ):
        monkeypatch.delenv(name, raising=False)
    assert forge_token("github", "") == ("", "")
    assert admin_token("github", "") == ("", "")


def test_the_origin_remote_parses_every_spelling() -> None:
    from livery.workshop._forge_lane import _REMOTE_RE

    for url in (
        "https://github.com/acme/tools.git",
        "https://github.com/acme/tools",
        "http://localhost:3000/livery-admin/envset-ci-e2e.git",
        "http://user:token@localhost:3000/owner/name.git",
        "git@github.com:acme/tools.git",
    ):
        match = _REMOTE_RE.search(url)
        assert match is not None, url
        assert match.group("name") in ("tools", "envset-ci-e2e", "name"), url
