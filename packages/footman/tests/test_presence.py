"""Presence: what the caller supplied, as against what footman inferred."""

from __future__ import annotations

import io
import sys
from typing import Annotated

import pytest

from livery.footman import _manifest, given
from livery.footman._executor import run_chain
from livery.footman._split import split_chain
from livery.footman.params import ask, env, forward
from livery.footman.registry import Group

# Module-level, because `from __future__ import annotations` turns every
# annotation into a string resolved against module globals — a name local to a
# test or helper silently fails to resolve and the parameter falls back to text.
Target = Annotated[str, env("BUILD_TARGET")]
Fix = Annotated[bool, forward]
Asked = Annotated[str, ask()]
ForwardedTarget = Annotated[str, forward]


def _run(build_tasks, line):
    reg = Group("root")
    build_tasks(reg)
    tree = _manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, line.split())
    return run_chain(reg, segments)


def _string_target(line):
    """`(value, given)` as a plain string-option body saw them."""
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def build(*, target: str = "fallback") -> None:
            seen["value"] = target
            seen["given"] = given("target")

    _run(tasks, line)
    return seen["value"], seen["given"]


def test_an_option_with_a_value_is_given():
    assert _string_target("build --target=prod") == ("prod", True)


def test_an_absent_option_is_not_given():
    assert _string_target("build") == ("fallback", False)


def test_a_value_equal_to_the_default_is_still_given():
    # The whole point: the value cannot tell you, only presence can.
    assert _string_target("build --target=fallback") == ("fallback", True)


def test_a_bare_mention_is_the_default_asked_for():
    # The spelling the whole change exists to allow: `--target` with no value
    # binds what absence would have bound, and says someone wanted it.
    assert _string_target("build --target") == ("fallback", True)


def test_a_flag_is_given_however_it_is_spelled():
    seen: list[tuple[bool, bool]] = []

    def tasks(reg):
        @reg.task
        def build(*, fix: bool = False) -> None:
            seen.append((fix, given("fix")))

    _run(tasks, "build --fix")
    _run(tasks, "build --no-fix")
    _run(tasks, "build")
    assert seen == [(True, True), (False, True), (False, False)]


def _env_target(line):
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def build(*, target: Target = "fallback") -> None:
            seen["value"] = target
            seen["given"] = given("target")

    _run(tasks, line)
    return seen["value"], seen["given"]


def test_an_env_fallback_supplies_a_value_but_not_presence(monkeypatch):
    # Ambient configuration answers for nobody: the value arrives, the claim
    # that someone asked for it does not.
    monkeypatch.setenv("BUILD_TARGET", "from-env")
    assert _env_target("build") == ("from-env", False)


def test_the_command_line_beats_env_on_both_channels(monkeypatch):
    monkeypatch.setenv("BUILD_TARGET", "from-env")
    assert _env_target("build --target=explicit") == ("explicit", True)


def test_a_body_call_says_the_same_thing_as_a_command_line():
    seen: list[tuple[str, bool]] = []

    def tasks(reg):
        @reg.task
        def build(*, target: str = "fallback") -> None:
            seen.append((target, given("target")))

        @reg.task
        def wrap() -> None:
            build(target="explicit")  # named: given
            build()  # omitted: not given

    _run(tasks, "wrap")
    assert seen == [("explicit", True), ("fallback", False)]


def test_a_called_task_does_not_inherit_its_callers_presence():
    seen: dict[str, bool] = {}

    def tasks(reg):
        @reg.task
        def inner(*, target: str = "fallback") -> None:
            seen["inner"] = given("target")

        @reg.task
        def outer(*, target: str = "fallback") -> None:
            seen["outer"] = given("target")
            inner()

    _run(tasks, "outer --target=prod")
    # `dataclasses.replace` copies every field, so this is the regression that
    # would otherwise pass unnoticed: a callee claiming its caller's answer.
    assert seen == {"outer": True, "inner": False}


def test_the_same_value_asked_for_and_not_is_two_pieces_of_work():
    calls: list[bool] = []

    def tasks(reg):
        @reg.task
        def build(*, target: str = "fallback") -> None:
            calls.append(given("target"))

        @reg.task
        def wrap() -> None:
            build()  # the default, nobody asked
            build(target="fallback")  # the same value, asked for

    _run(tasks, "wrap")
    # Keyed on arguments alone these share a cell — `apply_defaults()` makes
    # them identical — and the second is answered by the first, silently doing
    # the wrong thing for a body that branches on presence.
    assert calls == [False, True]


def _forwarding_tasks(reg):
    @reg.task
    def build(*, fix: bool = False) -> None:
        _forwarded["build"] = (fix, given("fix"))

    @reg.task(pre=[build])
    def check(*, fix: Fix = False) -> None:
        _forwarded["check"] = (fix, given("fix"))


_forwarded: dict[str, tuple[bool, bool]] = {}


def test_a_forwarded_value_carries_whether_anyone_asked_for_it():
    _forwarded.clear()
    _run(_forwarding_tasks, "check --fix")
    # Both channels reach the prerequisite: the value, and the fact that a
    # person asked for it — so `given` reads the same sentence at both depths.
    assert _forwarded == {"build": (True, True), "check": (True, True)}


def test_a_forwarded_default_arrives_without_a_claim_that_it_was_asked_for():
    _forwarded.clear()
    _run(_forwarding_tasks, "check")
    # The value still travels — forwarding only what was asked for would strip
    # env-sourced values and leave prerequisites on their own defaults.
    assert _forwarded == {"build": (False, False), "check": (False, False)}


def test_forwarding_satisfies_a_defaultless_parameter():
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def build(*, target: str) -> None:  # required: no default to fall back on
            seen["target"] = target
            seen["given"] = given("target")

        @reg.task(pre=[build])
        def check(*, target: ForwardedTarget) -> None:
            pass

    _run(tasks, "check --target=prod")
    # Refusing this only pushed authors into giving `build.target` a default it
    # did not want, weakening its contract when run on its own.
    assert seen == {"target": "prod", "given": True}


def test_a_bare_mention_skips_the_question(monkeypatch):
    from livery.footman import context

    # Naming the option *is* an answer: asking again would be footman not
    # listening. stdin would supply "typed" if a prompt ever fired.
    monkeypatch.setattr(context, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("typed\n"))
    monkeypatch.setattr(context, "real_stderr", io.StringIO)
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def release(*, version: Asked = "patch") -> None:
            seen["value"] = version
            seen["given"] = given("version")

    _run(tasks, "release --version")
    assert seen == {"value": "patch", "given": True}


def test_given_outside_a_task_is_taught_not_false():
    with pytest.raises(RuntimeError, match=r"given\('target'\) has no answer here"):
        given("target")


def test_an_unknown_parameter_name_is_an_error_not_a_silent_false():
    def tasks(reg):
        @reg.task
        def build(*, target: str = "fallback") -> None:
            given("nope")

    results = _run(tasks, "build")
    error = results[0].error
    assert isinstance(error, ValueError)
    assert "has no parameter 'nope' (it has: target)" in str(error)


def test_a_dashed_spelling_is_taught_back_as_the_parameter_name():
    def tasks(reg):
        @reg.task
        def build(*, dry_run: bool = False) -> None:
            given("dry-run")

    results = _run(tasks, "build")
    assert "did you mean 'dry_run'?" in str(results[0].error)
