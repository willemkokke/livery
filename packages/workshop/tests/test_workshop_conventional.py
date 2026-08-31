"""The commit convention: parsing, footman's bump rules, and the entry."""

from __future__ import annotations

from livery.workshop._conventional import (
    Commit,
    changelog_entry,
    next_version,
    parse_commit,
)


def _commit(subject: str, body: str = "", email: str = "t@livery.local") -> Commit:
    return parse_commit("a" * 40, subject, body, "Tester", email)


def test_parsing_reads_type_scope_bang_and_footer() -> None:
    plain = _commit("feat(scope): add the thing (#12)")
    assert (plain.type, plain.scope, plain.breaking) == ("feat", "scope", False)
    bang = _commit("fix!: change the wire format")
    assert bang.breaking and bang.type == "fix"
    footer = _commit("refactor: split the module", "BREAKING CHANGE: renames it")
    assert footer.breaking
    legacy = _commit("Merge things around")
    assert legacy.type == "" and legacy.description == "Merge things around"


def test_bumps_follow_footmans_practice() -> None:
    feat = [_commit("feat: new verb (#1)")]
    fix = [_commit("fix: stop the leak (#2)")]
    breaking = [_commit("feat!: rework the seam (#3)")]
    # Before 1.0: a feature bumps minor, breaks ride along, else patch.
    assert next_version("0.4.2", feat) == "0.5.0"
    assert next_version("0.4.2", breaking) == "0.5.0"
    assert next_version("0.4.2", fix) == "0.4.3"
    # After 1.0: the standard ladder.
    assert next_version("1.4.2", breaking) == "2.0.0"
    assert next_version("1.4.2", feat) == "1.5.0"
    assert next_version("1.4.2", fix) == "1.4.3"
    assert next_version("1.4.2", []) == "1.4.2"


def test_the_entry_groups_links_and_credits() -> None:
    commits = [
        _commit("feat: the new verb (#41)", email="9+willem@users.noreply.github.com"),
        _commit("fix: the crash (#42)"),
        _commit("chore: tidy the tree (#43)"),
    ]
    entry = changelog_entry(
        "0.5.0", "2026-09-01", commits, pr_url_base="https://github.com/o/r/pull"
    )
    assert entry.startswith("## [0.5.0] - 2026-09-01")
    assert (
        entry.index("### Added") < entry.index("### Fixed") < entry.index("### Changed")
    )
    assert "([#41](https://github.com/o/r/pull/41))" in entry
    assert "Contributors: @willem, Tester" in entry


def test_the_entry_without_a_url_keeps_plain_references() -> None:
    entry = changelog_entry("0.5.0", "2026-09-01", [_commit("feat: thing (#7)")])
    assert "(#7)" in entry and "](" not in entry.split("Contributors")[0]
