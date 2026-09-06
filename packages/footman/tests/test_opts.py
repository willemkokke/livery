"""Per-use option overrides: `task.opts(...)` and `group.opts(...)`."""

from __future__ import annotations

import inspect
import sys
from typing import assert_type

import pytest

from livery.footman import _manifest
from livery.footman._executor import run_chain
from livery.footman._schedule import resolve_keep_going, run_plan
from livery.footman._split import split_chain
from livery.footman.params import Forward
from livery.footman.registry import (
    Group,
    is_atomic,
    is_interactive,
    keeps_going,
    wants_progress,
)


def _tree(build):
    reg = Group("root")
    build(reg)
    return reg, _manifest.build_manifest(reg)["tree"]


# --- the mechanism -----------------------------------------------------------


def test_opts_overrides_are_read_by_the_accessors():
    reg = Group("root")

    @reg.task
    def t(): ...

    assert is_atomic(t) is False  # the registered task is untouched
    opted = t.opts(atomic=True, keep_going=True, interactive=True, progress=False)
    assert is_atomic(opted) is True
    assert keeps_going(opted) is True
    assert is_interactive(opted) is True
    assert wants_progress(opted) is False
    assert is_atomic(t) is False  # ...and stays untouched — the override is per-use


def test_task_signature_is_forwarded_to_the_type_checker():
    # basedpyright (the gate) validates the assert_type / call-site checks below;
    # at runtime assert_type is a no-op. This pins that `@task` forwards the
    # wrapped signature — a decorated task is not erased to `Callable[..., Any]`.
    reg = Group("root")

    @reg.task
    def build(target: str, release: bool = False) -> int:
        return 1

    assert_type(build("web", release=True), int)  # parameters + return forwarded
    opted = build.opts(atomic=True)
    assert_type(opted("web", release=True), int)  # an opted call keeps the signature
    chained = opted.opts(keep_going=True)
    assert_type(chained("web"), int)  # ...and so does a chained .opts()


def test_opts_is_a_transparent_proxy():
    reg = Group("root")

    @reg.task
    def build(target: str, release: bool = False):
        return (target, release)

    opted = build.opts(atomic=True)
    assert opted.__name__ == "build"  # same identity for labels
    assert list(inspect.signature(opted).parameters) == ["target", "release"]
    assert opted("web", release=True) == ("web", True)  # a call delegates to the task


def test_opts_rejects_unknown_options():
    reg = Group("root")

    @reg.task
    def t(fix: bool = False): ...

    with pytest.raises(TypeError, match=r"unknown option"):
        # A static error too (TaskOpts closes the set) — suppressed because
        # this test pins the *runtime* rail a dynamic caller still hits.
        t.opts(fix=True)  # type: ignore[call-arg]


def test_opts_rejects_an_unhashable_value():
    reg = Group("root")

    @reg.task
    def t(): ...

    with pytest.raises(TypeError, match=r"hashable"):
        # Statically a wrong type as well — kept for the runtime rail, which
        # guards dedup hashability for callers the checker never sees.
        t.opts(confirm=["not", "hashable"])  # type: ignore[arg-type]


def test_task_opts_matches_opts_attrs():
    # TaskOpts (the typed .opts()/set_opts surface) and _OPTS_ATTRS (the
    # runtime validator) are the same closed set, or completion and the
    # taught error drift apart.
    from livery.footman.registry import _OPTS_ATTRS, TaskOpts

    declared = set(TaskOpts.__optional_keys__) | set(TaskOpts.__required_keys__)
    assert declared == set(_OPTS_ATTRS)


def test_opts_chains_later_wins():
    reg = Group("root")

    @reg.task
    def t(): ...

    opted = t.opts(keep_going=True, atomic=True).opts(keep_going=False)
    assert keeps_going(opted) is False  # a later .opts() wins
    assert is_atomic(opted) is True  # an earlier override is preserved


# --- through the scheduler ---------------------------------------------------


def test_opts_keep_going_on_a_prerequisite_reaches_run_wide_resolution():
    reg = Group("root")

    @reg.task
    def lint(): ...

    @reg.task(pre=[lint.opts(keep_going=True)])
    def check(): ...

    tree = _manifest.build_manifest(reg)["tree"]
    _, segs = split_chain(tree, ["check"])
    assert resolve_keep_going(reg, segs, None) is True  # the prereq opts keep-going
    assert resolve_keep_going(reg, segs, False) is False  # CLI --fail-fast still wins


def test_opts_atomic_on_a_prerequisite_survives_fail_fast(tmp_path):
    from livery.footman.context import run

    marker = tmp_path / "finished"
    sleep = [sys.executable, "-c", "import time; time.sleep(0.4)"]
    reg = Group("root")

    @reg.task
    def slow():
        run(sleep)
        marker.write_text("done")  # its subprocess must not be cut off

    @reg.task(pre=[slow.opts(atomic=True)])  # atomic applied at the call site
    def gate(): ...

    @reg.task
    def boom():
        raise SystemExit(1)

    tree = _manifest.build_manifest(reg)["tree"]
    _, segs = split_chain(tree, ["gate", "boom"])
    run_plan(reg, segs, sequential=False)  # fail-fast, but the prereq opts atomic
    assert marker.exists()  # ran to completion despite the failing sibling


def test_group_opts_as_a_prerequisite_resolves_the_default_with_overrides():
    seen = {}

    def build(reg):
        lint = reg.group("lint")

        @lint.task
        def python():
            seen["python"] = "ran"

        @lint.default
        def lint_all():
            """Lint everything."""  # empty body -> fan out

        @reg.task(pre=[lint.opts(keep_going=True)])
        def check(): ...

    reg, tree = _tree(build)
    _, segs = split_chain(tree, ["check"])
    assert resolve_keep_going(reg, segs, None) is True  # opts rides the group default
    run_chain(reg, segs)
    assert seen == {"python": "ran"}  # ...and the group still fans out


def test_opts_is_callable_from_a_body():
    ran = []
    reg = Group("root")

    @reg.task
    def helper(msg: str = "hi"):
        ran.append(msg)

    @reg.task
    def top():
        helper.opts(atomic=True)(msg="from-body")  # opts + explicit call

    tree = _manifest.build_manifest(reg)["tree"]
    _, segs = split_chain(tree, ["top"])
    run_chain(reg, segs)
    assert ran == ["from-body"]


def test_forward_reaches_through_an_opted_target():
    # A forwarded value must thread through _Opted to the base's parameter — the
    # proxy is transparent to signature inspection (resolved_signature follows
    # __wrapped__), so partial-reach forwarding matches on the base's params.
    seen = {}
    reg = Group("root")

    @reg.task
    def target(fix: bool = False):
        seen["target_fix"] = fix

    @reg.task(pre=[target.opts(atomic=True)])
    def check(fix: Forward[bool] = False):
        """Gate."""

    tree = _manifest.build_manifest(reg)["tree"]
    _, segs = split_chain(tree, ["check", "--fix"])
    run_chain(reg, segs)
    assert seen == {"target_fix": True}  # --fix reached target *through* .opts()


# --- deduplication: an opted reference's DAG identity -------------------------


def test_identical_opts_references_share_one_dag_node():
    # Two tasks name the same prerequisite with the *same* override (two inline
    # .opts() calls). Keyed by (base, overrides), they are one node — the shared
    # prerequisite still runs once, the way a bare shared prerequisite would.
    runs = []
    reg = Group("root")

    @reg.task
    def shared():
        runs.append("shared")

    @reg.task(pre=[shared.opts(atomic=True)])
    def a(): ...

    @reg.task(pre=[shared.opts(atomic=True)])
    def b(): ...

    tree = _manifest.build_manifest(reg)["tree"]
    _, segs = split_chain(tree, ["a", "b"])
    run_chain(reg, segs)
    assert runs == ["shared"]  # deduped: one node, one run


def test_a_bare_and_an_opted_reference_are_distinct_nodes():
    # A different policy is a genuinely different invocation, so a bare reference
    # and an opted one do NOT merge — the prerequisite runs once per policy.
    runs = []
    reg = Group("root")

    @reg.task
    def shared():
        runs.append("shared")

    @reg.task(pre=[shared])
    def a(): ...

    @reg.task(pre=[shared.opts(atomic=True)])
    def b(): ...

    tree = _manifest.build_manifest(reg)["tree"]
    _, segs = split_chain(tree, ["a", "b"])
    run_chain(reg, segs)
    assert runs == ["shared", "shared"]  # bare vs opted: two distinct nodes


def test_empty_opts_collapses_onto_the_bare_task():
    # An empty .opts() is no override at all: same (id, frozenset()) key as the
    # bare task, so it merges — no int-vs-tuple asymmetry.
    runs = []
    reg = Group("root")

    @reg.task
    def shared():
        runs.append("shared")

    @reg.task(pre=[shared])
    def a(): ...

    @reg.task(pre=[shared.opts()])  # empty overrides
    def b(): ...

    tree = _manifest.build_manifest(reg)["tree"]
    _, segs = split_chain(tree, ["a", "b"])
    run_chain(reg, segs)
    assert runs == ["shared"]  # empty .opts() == bare: one node, one run
