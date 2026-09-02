"""GitLab's licence-gated approvals and the schedule-events read, replayed.

The unlicensed arms replay exchanges observed against the CE dev
container. The licensed arms replay the documented API shapes: no
licensed surface exists in the rig yet, so these are the pinned
expectation a future live recording must confirm (the plan carries
that debt).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from livery.forge import GitlabForge, RepoConfig, Unsupported
from livery.forge.testing import Cassette, Exchange, ReplayOpener

BASE = "http://gitlab.local/api/v4"
PROJECT = "/projects/acme%2Fws"


def _exchange(
    method: str, url: str, status: int, body: object, *, request_body: str = ""
) -> Exchange:
    return Exchange(
        method=method,
        url=url,
        request_body=request_body,
        status=status,
        reason="",
        content_type="application/json",
        response_body=body if isinstance(body, str) else json.dumps(body),
    )


def _forge(*exchanges: Exchange) -> GitlabForge:
    cassette = Cassette()
    cassette.exchanges.extend(exchanges)
    return GitlabForge(
        BASE, token="t", opener=ReplayOpener(cassette, secrets=("dummy",))
    )


_PROBE_PROJECTS = _exchange(
    "GET",
    f"{BASE}/projects?membership=true&per_page=1",
    200,
    [{"id": 7, "path_with_namespace": "acme/ws"}],
)


def _probe(licensed: bool) -> tuple[Exchange, Exchange]:
    answer: tuple[int, object] = (200, []) if licensed else (404, {"error": "404"})
    return (
        _PROBE_PROJECTS,
        _exchange("GET", f"{BASE}/projects/7/approval_rules?per_page=1", *answer),
    )


def test_an_unlicensed_instance_answers_no_and_the_answer_is_cached() -> None:
    # The 404 is the observed CE answer; the second ask must not
    # reach the wire (the cassette has no second probe to serve).
    forge = _forge(*_probe(licensed=False))
    assert forge.supports("min_approvals") is False
    assert forge.supports("min_approvals") is False


def test_a_licensed_instance_answers_yes() -> None:
    forge = _forge(*_probe(licensed=True))
    assert forge.supports("min_approvals") is True


def test_configure_on_an_unlicensed_instance_declines_before_acting() -> None:
    forge = _forge(*_probe(licensed=False))
    repo = forge.repository("acme", "ws")
    with pytest.raises(Unsupported) as caught:
        repo.configure(RepoConfig(min_approvals=1))
    assert "licence" in str(caught.value)
    assert "min_approvals" in str(caught.value)
    # The cassette held only the probe: nothing else went on the wire.


def test_configure_creates_the_rule_and_protects_the_codeowner_field() -> None:
    forge = _forge(
        *_probe(licensed=True),
        _exchange("GET", f"{BASE}{PROJECT}/approval_rules", 200, []),
        _exchange(
            "POST",
            f"{BASE}{PROJECT}/approval_rules",
            201,
            {"id": 1, "rule_type": "any_approver", "approvals_required": 2},
            request_body=json.dumps(
                {
                    "name": "Any approver",
                    "rule_type": "any_approver",
                    "approvals_required": 2,
                }
            ),
        ),
        _exchange(
            "GET",
            f"{BASE}{PROJECT}",
            200,
            {"path_with_namespace": "acme/ws", "default_branch": "main"},
        ),
        _exchange("GET", f"{BASE}{PROJECT}/protected_branches/main", 404, {}),
        _exchange(
            "POST",
            f"{BASE}{PROJECT}/protected_branches",
            201,
            {"name": "main", "code_owner_approval_required": True},
            request_body=json.dumps(
                {"name": "main", "code_owner_approval_required": True}
            ),
        ),
    )
    repo = forge.repository("acme", "ws")
    repo.configure(RepoConfig(min_approvals=2, require_codeowner_review=True))


def test_configure_rewrites_nothing_already_true() -> None:
    # Probe before act: matching state produces reads only, so the
    # cassette carries no write to serve.
    forge = _forge(
        *_probe(licensed=True),
        _exchange(
            "GET",
            f"{BASE}{PROJECT}/approval_rules",
            200,
            [{"id": 1, "rule_type": "any_approver", "approvals_required": 2}],
        ),
        _exchange(
            "GET",
            f"{BASE}{PROJECT}",
            200,
            {"path_with_namespace": "acme/ws", "default_branch": "main"},
        ),
        _exchange(
            "GET",
            f"{BASE}{PROJECT}/protected_branches/main",
            200,
            {"name": "main", "code_owner_approval_required": True},
        ),
    )
    repo = forge.repository("acme", "ws")
    repo.configure(RepoConfig(min_approvals=2, require_codeowner_review=True))


def test_configure_updates_a_drifted_rule_in_place() -> None:
    forge = _forge(
        *_probe(licensed=True),
        _exchange(
            "GET",
            f"{BASE}{PROJECT}/approval_rules",
            200,
            [{"id": 5, "rule_type": "any_approver", "approvals_required": 1}],
        ),
        _exchange(
            "PUT",
            f"{BASE}{PROJECT}/approval_rules/5",
            200,
            {"id": 5, "approvals_required": 3},
            request_body=json.dumps({"approvals_required": 3}),
        ),
    )
    repo = forge.repository("acme", "ws")
    repo.configure(RepoConfig(min_approvals=3))


def test_protection_reads_the_highest_rule_count() -> None:
    forge = _forge(
        _exchange(
            "GET",
            f"{BASE}{PROJECT}/protected_branches/main",
            200,
            {"name": "main", "code_owner_approval_required": True},
        ),
        _exchange(
            "GET",
            f"{BASE}{PROJECT}/approval_rules",
            200,
            [
                {"id": 1, "rule_type": "any_approver", "approvals_required": 2},
                {"id": 2, "rule_type": "regular", "approvals_required": 1},
            ],
        ),
    )
    protection = forge.repository("acme", "ws").protection("main")
    assert protection is not None
    assert protection.required_approvals == 2
    assert protection.require_codeowner_review is True


def test_protection_on_an_unlicensed_instance_reads_inert() -> None:
    # The observed CE answers: the record exists, the rules 404.
    forge = _forge(
        _exchange(
            "GET",
            f"{BASE}{PROJECT}/protected_branches/main",
            200,
            {"name": "main"},
        ),
        _exchange("GET", f"{BASE}{PROJECT}/approval_rules", 404, {"error": "404"}),
    )
    protection = forge.repository("acme", "ws").protection("main")
    assert protection is not None
    assert protection.required_approvals == 0
    assert protection.require_codeowner_review is None


def _note(body: str, created: str, *, system: bool = True) -> dict[str, Any]:
    return {
        "system": system,
        "body": body,
        "created_at": created,
        "author": {"username": "root"},
    }


def test_schedule_events_reconstruct_from_notes_and_state_events() -> None:
    # The note wordings are the observed 18.9 answers on the dev
    # container; the parsing is a contract with them.
    forge = _forge(
        _exchange(
            "GET",
            f"{BASE}{PROJECT}/merge_requests/1/notes?page=1&per_page=50&sort=asc",
            200,
            [
                _note("a plain comment", "2026-09-02T10:00:00Z", system=False),
                _note(
                    "enabled an automatic merge when all merge checks for b9a3a8c pass",
                    "2026-09-02T10:33:33Z",
                ),
                _note("canceled the automatic merge", "2026-09-02T10:33:34Z"),
                _note("added 1 commit", "2026-09-02T10:33:40Z"),
            ],
        ),
        _exchange(
            "GET",
            f"{BASE}{PROJECT}/merge_requests/1/resource_state_events"
            "?page=1&per_page=50",
            200,
            [
                {
                    "state": "merged",
                    "created_at": "2026-09-02T10:34:00Z",
                    "user": {"username": "root"},
                }
            ],
        ),
    )
    events = forge.repository("acme", "ws").pr.schedule_events(1)
    assert [event.kind for event in events] == [
        "scheduled",
        "unscheduled",
        "pushed",
        "merged",
    ]
    assert events[0].actor == "root"
    assert forge.supports("schedule_events") is True
