"""Parameter forwarding: a `forward`-marked value threads to dispatched tasks."""

from __future__ import annotations

from typing import Annotated

from livery.footman import _manifest
from livery.footman._executor import forward_map, run_chain
from livery.footman._split import Segment, split_chain
from livery.footman.params import Forward, forward
from livery.footman.registry import Group


def drive(build, line):
    reg = Group("root")
    build(reg)
    tree = _manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, line.split())
    run_chain(reg, segments)


def test_forward_threads_into_a_prerequisite():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(fix: bool = False):
            seen["build"] = fix

        @reg.task(pre=[build])
        def check(fix: Annotated[bool, forward] = False):
            seen["check"] = fix

    drive(tasks, "check --fix")
    assert seen == {"build": True, "check": True}


def test_forward_of_the_default_overrides_the_callee_default():
    # No CLI value given: the caller forwards its *own* default, which still
    # overrides the callee's default (forward supplies a value either way).
    seen = {}

    def tasks(reg):
        @reg.task
        def build(fix: bool = True):  # callee would default True…
            seen["build"] = fix

        @reg.task(pre=[build])
        def check(fix: Forward[bool] = False):  # …but the caller's False wins
            pass

    drive(tasks, "check")
    assert seen["build"] is False


def test_forward_is_partial_untaken_prereqs_run_defaulted():
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def spelling():  # no `fix` parameter — untouched
            seen["spelling"] = "ran"

        @reg.task
        def pyfix(fix: bool = False):
            seen["pyfix"] = fix

        @reg.task(pre=[pyfix, spelling])
        def lint(fix: Forward[bool] = False):
            pass

    drive(tasks, "lint --fix")
    assert seen == {"pyfix": True, "spelling": "ran"}


def test_forward_reaches_post_prerequisites_too():
    seen = {}

    def tasks(reg):
        @reg.task
        def notify(fix: bool = False):
            seen["notify"] = fix

        @reg.task(post=[notify])
        def deploy(fix: Forward[bool] = False):
            pass

    drive(tasks, "deploy --fix")
    assert seen["notify"] is True


def test_diverging_forwards_make_two_nodes_not_a_refusal():
    # One identity rule, everywhere: requests that resolve to different
    # arguments are different work. Two dispatchers forwarding different
    # values to one prerequisite each get the node they meant — silently,
    # correctly — and same-value dispatchers still share one.
    seen: list[bool] = []

    def tasks(reg):
        @reg.task
        def shared(fix: bool = False):
            seen.append(fix)

        @reg.task(pre=[shared])
        def a(fix: Forward[bool] = True):
            pass

        @reg.task(pre=[shared])
        def b(fix: Forward[bool] = False):
            pass

        @reg.task(pre=[shared])
        def c(fix: Forward[bool] = True):
            pass

    drive(tasks, "a b c")
    assert sorted(seen) == [False, True]  # two executions, not three


def test_forward_map_reads_cli_or_default_and_carries_presence():
    # Two channels: the value always travels, and whether anyone asked for it
    # travels beside it. `forward_map` stays side-effect free — it reads the
    # segment and the declared default, never a prompt.
    reg = Group("root")

    @reg.task
    def deploy(target: Forward[str], fix: Forward[bool] = False):
        pass

    # A defaultless parameter contributes when the line carried one: refusing
    # only pushed authors into giving the receiving task a default it did not
    # want. Both were named, so both are asked-for.
    seg = Segment(
        task="deploy", path=["deploy"], values={"target": "prod", "fix": True}
    )
    assert forward_map(deploy, seg) == (
        {"target": "prod", "fix": True},
        frozenset({"target", "fix"}),
    )
    # Bare segment: the defaulted `fix` sends its default and nobody asked for
    # it; the defaultless `target` has nothing to send at all.
    assert forward_map(deploy, Segment(task="deploy", path=["deploy"])) == (
        {"fix": False},
        frozenset(),
    )
