"""Jobs an extension contributes to a builtin point: refusals, then composition."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

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
    job_installs,
    job_seams,
    point_by_name,
    verdict_needs,
    withdraw_job,
)

# The site's jobs are the docs extension's, added as the mount adds them.
from workshop_docs_declared import docs_jobs  # noqa: F401

_FAILURES = (SystemExit, Failed)


@pytest.fixture
def acme_jobs() -> Iterator[None]:
    yield
    for point in ("gate", "merge", "nightly"):
        for name in ("lint-prose", "publish-book"):
            withdraw_job(point, name)


def test_a_test_leaves_the_jobs_contributed_before_it_in_place(
    acme_jobs: None,
) -> None:
    # A mount earlier in the session contributed lint-prose; a test
    # withdraws it and adds publish-book. Afterwards the jobs are the
    # mount's again.
    from workshop_docs_declared import jobs_restored

    contribute_job("gate", Job("lint-prose"), extension="acme.prose")
    with jobs_restored():
        withdraw_job("gate", "lint-prose")
        contribute_job("nightly", Job("publish-book"), extension="acme.prose")
    assert [item.job.name for item in contributed_jobs("gate")].count("lint-prose") == 1
    assert "publish-book" not in [item.job.name for item in contributed_jobs("nightly")]


def test_a_contribution_to_an_unknown_point_names_the_points(acme_jobs: None) -> None:
    with pytest.raises(
        _FAILURES, match=r"'weekly', which is not a builtin point; the points are gate"
    ):
        contribute_job("weekly", Job("lint-prose"), extension="acme.prose")


def test_a_job_the_point_declares_or_another_extension_contributed_refuses(
    acme_jobs: None,
) -> None:
    with pytest.raises(_FAILURES, match=r"the gate point, which declares it already"):
        contribute_job("gate", Job("check"), extension="acme.prose")
    contribute_job("gate", Job("lint-prose"), extension="acme.prose")
    with pytest.raises(_FAILURES, match=r"which acme.prose contributed already"):
        contribute_job("gate", Job("lint-prose"), extension="acme.other")
    # The same extension may restate its own job.
    contribute_job("gate", Job("lint-prose", fetch="2"), extension="acme.prose")
    assert contributed_jobs("gate")[-1].job.fetch == "2"


def test_an_entry_naming_another_job_refuses(acme_jobs: None) -> None:
    with pytest.raises(_FAILURES, match=r"an entry for merge/deploy; an entry names"):
        contribute_job(
            "gate",
            Job("lint-prose"),
            entries=(Entry("merge", "deploy", "acme.publish"),),
            extension="acme.prose",
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
        extension="acme.prose",
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
        extension="acme.prose",
    )
    nightly = point_by_name(None)["nightly"]
    assert nightly.jobs[-1].name == "publish-book"
    assert Entry("nightly", "publish-book", "acme.book.publish") in builtin_schedule()
    assert nightly.name == composed_points()[2].name


def test_a_job_another_job_of_its_point_waits_for_sits_before_it(
    acme_jobs: None,
) -> None:
    from workshop_docs_declared import jobs_restored

    with jobs_restored():
        # The fallback first: nothing waits for the job and the point has
        # no verdict, so it lands last.
        contribute_job(
            "release",
            Job("book", publishes="book", paths=("book",)),
            extension="acme.prose",
        )
        assert [job.name for job in point_by_name(None)["release"].jobs] == [
            "publish",
            "book",
        ]
        # A join of the publish job on its own point adds its needs to the
        # job's, and the job it waits for moves before it.
        contribute_job(
            "release",
            Job("publish", joins=True, needs=("book",), collects="book"),
            extension="acme.prose",
        )
        release = point_by_name(None)["release"]
        assert [job.name for job in release.jobs if not job.joins] == [
            "book",
            "publish",
        ]
        publish = next(job for job in release.jobs if job.name == "publish")
        assert publish.needs == ("book",)
    assert "book" not in [job.name for job in point_by_name(None)["release"].jobs]


def test_a_join_of_a_job_the_point_lacks_or_one_that_gates_refuses(
    acme_jobs: None,
) -> None:
    with pytest.raises(_FAILURES, match=r"joins the job 'book' of the merge point"):
        contribute_job("merge", Job("book", joins=True), extension="acme.prose")
    with pytest.raises(_FAILURES, match=r"joins the job 'gate' of the merge point and"):
        contribute_job(
            "merge", Job("gate", joins=True), gates=True, extension="acme.prose"
        )


def test_a_join_rides_the_point_and_its_entries_run_before_the_jobs_last(
    acme_jobs: None,
) -> None:
    from workshop_docs_declared import jobs_restored

    with jobs_restored():
        contribute_job(
            "merge",
            Job("docs", joins=True, publishes="book", paths=("book",)),
            extension="acme.prose",
        )
        contribute_job(
            "merge",
            Job("gate", joins=True, collects="book"),
            entries=(Entry("merge", "gate", "acme.book.publish"),),
            extension="acme.prose",
        )
        merge = point_by_name(None)["merge"]
        joined = [job for job in merge.jobs if job.joins and job.name == "gate"]
        assert [job.collects for job in joined][-1] == "book"
        # The merge point still renders no job of that name of its own.
        assert [job.name for job in merge.jobs if not job.joins] == ["govern"]
        gate_entries = [
            entry.task
            for entry in builtin_schedule()
            if (entry.point, entry.job) == ("merge", "gate")
        ]
        # The dispatch first, the joins' entries after it, the janitor last.
        assert gate_entries[0] == "workflow.release.dispatch"
        assert gate_entries[-1] == "janitor"
        assert gate_entries.index("acme.book.publish") < gate_entries.index("janitor")
    assert "acme.book.publish" not in [e.task for e in builtin_schedule()]


def test_the_docs_extension_declares_the_build_and_joins_the_merge_points_jobs(
    tmp_path: Path,
) -> None:
    gate = point_by_name(None)["gate"]
    assert [job.name for job in gate.jobs] == ["check", "docs", "gate"]
    assert gate.jobs[-1].needs == ("check", "docs")
    assert gate.jobs[1].fetch == "tags"
    merge = point_by_name(None)["merge"]
    assert [job.name for job in merge.jobs if not job.joins] == ["govern"]
    joins = {job.name: job for job in merge.jobs if job.joins}
    assert joins["docs"].publishes == "site" and joins["docs"].paths == ("site",)
    assert joins["gate"].collects == "site" and joins["gate"].deploy is not None
    # The functions the jobs name answer from the root contract.
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nextensions = ["docs"]\n[forge]\nkind = "gitea"\nowner = "acme"\n'
    )
    assert job_seams(tmp_path) == {("merge", "gate"): "container"}
    assert job_installs(tmp_path) == {("gate", "docs"): ()}
    entries = builtin_schedule()
    tasks = [(entry.point, entry.job, entry.task) for entry in entries]
    assert ("gate", "docs", "docs.build") in tasks
    gate_tasks = [
        task for point, job, task in tasks if (point, job) == ("merge", "gate")
    ]
    assert gate_tasks == ["workflow.release.dispatch", "docs.publish", "janitor"]
    verdict = next(entry for entry in entries if entry.task == "ci.verdict")
    assert verdict.args == ("--needs=check,docs",)
    assert all(
        entry.source == "docs" for entry in entries if entry.task.startswith("docs.")
    )
