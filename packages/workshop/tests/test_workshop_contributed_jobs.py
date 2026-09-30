"""Jobs a layer contributes to a builtin point: the refusals, then the composition."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from livery.footman import Failed
from livery.workshop._points import (
    DECLARED,
    Entry,
    Job,
    builtin_schedule,
    composed_points,
    contribute_job,
    contributed_jobs,
    point_by_name,
    verdict_needs,
    withdraw_job,
)

_FAILURES = (SystemExit, Failed)


@pytest.fixture
def acme_jobs() -> Iterator[None]:
    yield
    for point in ("gate", "merge", "nightly"):
        for name in ("lint-prose", "publish-book"):
            withdraw_job(point, name)


def test_a_contribution_to_an_unknown_point_names_the_points(acme_jobs: None) -> None:
    with pytest.raises(
        _FAILURES, match=r"'weekly', which is not a builtin point; the points are gate"
    ):
        contribute_job("weekly", Job("lint-prose"), layer="acme.prose")


def test_a_job_the_point_declares_or_another_layer_contributed_refuses(
    acme_jobs: None,
) -> None:
    with pytest.raises(_FAILURES, match=r"the gate point, which declares it already"):
        contribute_job("gate", Job("check"), layer="acme.prose")
    contribute_job("gate", Job("lint-prose"), layer="acme.prose")
    with pytest.raises(_FAILURES, match=r"which acme.prose contributed already"):
        contribute_job("gate", Job("lint-prose"), layer="acme.other")
    # The same layer may restate its own job.
    contribute_job("gate", Job("lint-prose", fetch="2"), layer="acme.prose")
    assert contributed_jobs("gate")[-1].job.fetch == "2"


def test_an_entry_naming_another_job_refuses(acme_jobs: None) -> None:
    with pytest.raises(_FAILURES, match=r"an entry for merge/deploy; an entry names"):
        contribute_job(
            "gate",
            Job("lint-prose"),
            entries=(Entry("merge", "deploy", "acme.publish"),),
            layer="acme.prose",
        )


# Then the composition: the job sits before the verdict, gates it when it
# says so, and its entries ride the schedule.


def test_a_gating_job_sits_before_the_verdict_and_joins_its_needs(
    acme_jobs: None,
) -> None:
    before = [job.name for job in point_by_name(None)["gate"].jobs]
    contribute_job(
        "gate",
        Job("lint-prose", fetch="2"),
        entries=(Entry("gate", "lint-prose", "acme.prose.lint"),),
        gates=True,
        layer="acme.prose",
    )
    gate = point_by_name(None)["gate"]
    names = [job.name for job in gate.jobs]
    assert names.index("lint-prose") == names.index("gate") - 1
    assert "lint-prose" in verdict_needs("gate")
    assert gate.jobs[-1].needs == verdict_needs("gate")
    entries = builtin_schedule()
    assert Entry("gate", "lint-prose", "acme.prose.lint") in entries
    verdict = next(entry for entry in entries if entry.task == "ci.verdict")
    assert verdict.args == (f"--needs={','.join(verdict_needs('gate'))}",)
    assert "lint-prose" in verdict.args[0]
    # Declared points are untouched; the composition is read each time.
    assert "lint-prose" not in [job.name for job in DECLARED[0].jobs]
    withdraw_job("gate", "lint-prose")
    assert [job.name for job in point_by_name(None)["gate"].jobs] == before


def test_a_job_on_a_point_without_a_verdict_lands_last_with_its_entries(
    acme_jobs: None,
) -> None:
    contribute_job(
        "nightly",
        Job("publish-book", needs=("nightly",)),
        entries=(Entry("nightly", "publish-book", "acme.book.publish"),),
        layer="acme.prose",
    )
    nightly = point_by_name(None)["nightly"]
    assert nightly.jobs[-1].name == "publish-book"
    assert Entry("nightly", "publish-book", "acme.book.publish") in builtin_schedule()
    assert nightly.name == composed_points()[2].name


def test_the_docs_layer_contributes_the_build_and_the_deploy() -> None:
    from livery.workshop.layers.docs import _tasks as docs_tasks

    del docs_tasks
    gate = point_by_name(None)["gate"]
    assert [job.name for job in gate.jobs] == ["check", "docs", "gate"]
    assert gate.jobs[-1].needs == ("check", "docs")
    merge = point_by_name(None)["merge"]
    assert merge.jobs[-1].name == "deploy" and merge.jobs[-1].deploy
    entries = builtin_schedule()
    tasks = [(entry.point, entry.job, entry.task) for entry in entries]
    assert ("gate", "docs", "docs.build") in tasks
    assert ("merge", "deploy", "docs.build") in tasks
    assert ("merge", "deploy", "docs.publish") in tasks
    verdict = next(entry for entry in entries if entry.task == "ci.verdict")
    assert verdict.args == ("--needs=check,docs",)
    assert all(
        entry.source == "livery.workshop.layers.docs"
        for entry in entries
        if entry.task.startswith("docs.")
    )
