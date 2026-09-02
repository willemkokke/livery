"""The codeowners dialects: pure string building, offline."""

from __future__ import annotations

from livery.forge import CodeownersEntry, GiteaForge, GithubForge, GitlabForge
from livery.forge.testing import Cassette, Exchange, FakeForge, ReplayOpener

ENTRIES = (
    CodeownersEntry(path="/packages/forge/", owners=("alice", "core-team")),
    CodeownersEntry(path="/packages/workshop/", owners=("bob",), min_approvals=2),
)


def _github() -> GithubForge:
    return GithubForge("https://api.github.com", token="")


def _gitea() -> GiteaForge:
    return GiteaForge.connect(url="http://gitea.local", token="t")


def _gitlab() -> GitlabForge:
    return GitlabForge.connect(url="http://gitlab.local", token="t")


def test_github_renders_and_names_its_approximation() -> None:
    rendered = _github().codeowners(ENTRIES)
    assert rendered.path == ".github/CODEOWNERS"
    assert "/packages/forge/ @alice @core-team" in rendered.content
    assert "/packages/workshop/ @bob" in rendered.content
    assert len(rendered.notes) == 1 and "2 approvals" in rendered.notes[0]


def test_gitea_renders_the_same_shape_at_its_location() -> None:
    rendered = _gitea().codeowners(ENTRIES)
    assert rendered.path == ".gitea/CODEOWNERS"
    assert "/packages/forge/ @alice @core-team" in rendered.content
    assert len(rendered.notes) == 1


def test_gitlab_sections_express_the_count_in_the_file() -> None:
    rendered = _gitlab().codeowners(ENTRIES)
    assert rendered.path == ".gitlab/CODEOWNERS"
    assert "[owners-1][2]" in rendered.content
    assert "/packages/workshop/ @bob" in rendered.content
    assert rendered.notes == ()  # nothing approximated


def test_empty_entries_render_an_empty_file() -> None:
    rendered = _github().codeowners(())
    assert rendered.content == "" and rendered.notes == ()


def test_the_fake_speaks_a_dialect_too() -> None:
    rendered = FakeForge().codeowners(ENTRIES)
    assert rendered.path == "CODEOWNERS"
    assert "@alice" in rendered.content and rendered.notes


def _exchange(method: str, url: str, status: int, body: str) -> Exchange:
    return Exchange(
        method=method,
        url=url,
        request_body="",
        status=status,
        reason="",
        content_type="application/json",
        response_body=body,
    )


def test_a_user_namespace_answers_itself_and_no_teams() -> None:
    # The 404 fallback, forced: a personal namespace has no org
    # endpoints, so members is the one login and teams is empty.
    cassette = Cassette()
    cassette.exchanges.extend(
        [
            _exchange(
                "GET",
                "https://api.github.com/orgs/solo/members?per_page=100&page=1",
                404,
                "{}",
            ),
            _exchange(
                "GET",
                "https://api.github.com/orgs/solo/teams?per_page=100&page=1",
                404,
                "{}",
            ),
        ]
    )
    forge = GithubForge(
        "https://api.github.com",
        token="",
        opener=ReplayOpener(cassette, secrets=("dummy",)),
    )
    assert forge.members("solo") == ("solo",)
    assert forge.teams("solo") == ()


def test_gitea_user_namespace_answers_itself_and_no_teams() -> None:
    # Gitea's org endpoints 404 on a personal namespace; the
    # fallback answers the login itself and an empty team list.
    cassette = Cassette()
    cassette.exchanges.extend(
        [
            _exchange(
                "GET",
                "http://gitea.local/api/v1/orgs/solo/members?limit=50&page=1",
                404,
                "{}",
            ),
            _exchange(
                "GET",
                "http://gitea.local/api/v1/orgs/solo/teams?limit=50&page=1",
                404,
                "{}",
            ),
        ]
    )
    forge = GiteaForge(
        "http://gitea.local/api/v1",
        token="t",
        opener=ReplayOpener(cassette, secrets=("dummy",)),
    )
    assert forge.members("solo") == ("solo",)
    assert forge.teams("solo") == ()


def test_gitlab_user_namespace_answers_itself_and_no_teams() -> None:
    # GitLab's group endpoints 404 on a personal namespace; same
    # fallback shape as the other backends.
    cassette = Cassette()
    cassette.exchanges.extend(
        [
            _exchange(
                "GET",
                "http://gitlab.local/api/v4/groups/solo/members?per_page=100&page=1",
                404,
                "{}",
            ),
            _exchange(
                "GET",
                "http://gitlab.local/api/v4/groups/solo/subgroups?per_page=100&page=1",
                404,
                "{}",
            ),
        ]
    )
    forge = GitlabForge(
        "http://gitlab.local/api/v4",
        token="t",
        opener=ReplayOpener(cassette, secrets=("dummy",)),
    )
    assert forge.members("solo") == ("solo",)
    assert forge.teams("solo") == ()


def test_gitlab_sections_cannot_absorb_a_later_plain_entry() -> None:
    # A GitLab section owns every entry after its heading until the
    # next heading, so a plain entry rendered after a counted one
    # would inherit its count. The dialect renders plain entries
    # first and each counted entry as its own trailing section.
    adverse = (
        CodeownersEntry(path="/packages/a/", owners=("alice",), min_approvals=2),
        CodeownersEntry(path="/packages/b/", owners=("bob",)),
    )
    rendered = _gitlab().codeowners(adverse)
    lines = rendered.content.splitlines()
    assert lines[0] == "/packages/b/ @bob"  # plain, before any section
    assert lines[1] == "[owners-1][2]"
    assert lines[2] == "/packages/a/ @alice"
