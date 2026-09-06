"""The work-item spec's exhibits, typed against the REAL surface.

Move 3 built this file as a stub-only loom: spec types restated locally
so the four checkers could force fragments to meet before any code
existed. The build landed (stages A-G, 2026-08-01), so the stubs are
traded for the shipped names — every exhibit below now types against
footman itself, which is the loom's end state: the spec and the code
answering the same checkers with one voice.

Two reconciliations, accepted and recorded in the spec note rather than
silently absorbed: the spec's `Address` object shipped as a plain
string (a tree-derived NAME — prefix-selection needs nothing richer),
and lifecycle moments travel as strings at runtime (`failed_at: str |
None`), the loom's `Literal` set surviving here as the documented
vocabulary. Never executed; ty and pyrefly include it by name.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Literal, TypeAlias, assert_type

from livery.footman import (
    AuditEntry,
    Context,
    Lane,
    Result,
    ResultView,
    cwd_lane,
    fail,
    lane,
    parallel,
    pre_record,
    step,
    task,
    use_context,
)
from livery.footman._step import StepFn, WorkItem

# The moments' vocabulary — the audit's own words, spec-fixed even though
# the runtime carries them as plain strings.
Phase: TypeAlias = Literal["bind", "enter", "body", "review", "observe"]


# --- the record family -------------------------------------------------


def the_draft_is_the_review_window(view: ResultView) -> None:
    _ = view.stdout + view.stderr  # review sees what was captured
    assert_type(view.ok, bool)  # derives from code — never disagrees
    view.title = "fmt: reformatted"
    view.code = 0  # the verdict follows the code (I2)
    view.set_returned("summarised")  # the display-lane write lives HERE


def the_sealed_record_is_read_only(result: Result) -> None:
    assert_type(result.code, int)
    assert_type(result.ok, bool)
    assert_type(result.address, str)  # the tree-derived name (spec: Address)
    assert_type(result.failed_at, str | None)  # a Phase value, as a string
    assert_type(result.work_code, int | None)  # the earned code, kept visible
    for entry in result.audit:
        assert_type(entry, AuditEntry)
        moment, actor, code = entry
        assert_type(moment, str)
        assert_type(actor, str)
        assert_type(code, int | None)


# --- the lifters and the yield vocabulary ------------------------------


@step
def covered() -> int:
    raise NotImplementedError


@step
def fmt_html(target: str) -> Generator[None, ResultView, int]:
    view = yield  # every yield evaluates to the item's own draft
    view.title = f"fmt {target}"
    yield  # a bare checkpoint: one of the three cancellation windows
    return 0


def building_is_not_running() -> None:
    item = fmt_html("docs/")  # builds the bound item; runs nothing
    assert_type(item, WorkItem[int])
    assert_type(covered(), WorkItem[int])
    assert_type(covered, StepFn[[], int])


def the_block_form_records_where_it_stands() -> None:
    with step("prepare fixtures") as s:
        assert_type(s, ResultView)
        s.title = "prepared 3 fixtures"  # self-review via the own handle


# --- attachment is the dispatch, at every grain ------------------------


def tidy(view: ResultView) -> None:
    view.title = view.title.strip()


@pre_record(tidy)
@task
def lint(fix: bool = False) -> int:
    raise NotImplementedError


@task
def typecheck() -> None:
    raise NotImplementedError


@typecheck.pre_task
def warm() -> None: ...


@typecheck.pre_record
def neat(view: ResultView) -> None:
    view.title = view.title.strip()


@typecheck.post_task
def watch(result: object) -> None: ...


@covered.pre_record
def interpret(view: ResultView) -> None:
    if "reformatted" in view.stdout:
        view.code = 0


@covered.post_step
def budget(result: Result) -> None:
    if result.duration > 60.0:
        fail(f"too slow: {result.duration:.0f}s")


def attachments_are_identity_shaped() -> None:
    warm()  # the hooks stay plain callables
    assert_type(lint(fix=True), int)  # the handle stays callable as itself


# --- the ban, the fan-out, and the records it returns ------------------


def the_fan_out_speaks_records() -> None:
    with use_context(Context()):
        records = parallel(lint, typecheck, covered())
        assert_type(records, list[Result])
        for r in records:
            assert_type(r.ok, bool)  # Results read backward as codes (I2)
            assert_type(r.address, str)


# --- lanes: bindings, never strings ------------------------------------


db: Lane = lane("typecheck-exhibit-db", reason="the shared dev DB")


@task(lanes=(db, cwd_lane))
def migrate() -> None:
    raise NotImplementedError


def step_makers_claim_through_their_opts() -> None:
    pinned = covered.opts(lanes=(db,), recorded=False)
    assert_type(pinned(), WorkItem[int])
