"""The phase verbs: a verb per phase and a sub-task per step, from the registry.

Refusals first: a flag no verb offers, a name that is no task address.
Then the tree the registry generates, the flags each verb offers, the
addresses an existing verb serves, and the generation run again after
the registry changed.
"""

from __future__ import annotations

import inspect
import re
import typing
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from livery.footman import Group
from livery.workshop import _quality, _steps
from livery.workshop._extensions import SELF
from livery.workshop._phases import PhaseContext
from livery.workshop._steps import (
    StepRecord,
    generate_verbs,
    phases,
    register_step,
    step_for,
    unregister_step,
    verb_tree,
)
from workshop_python_steps import python_steps_fixture  # noqa: F401

_FAILURES = (BaseException,)


@pytest.fixture
def registry() -> Iterator[None]:
    """Restore the step registry a test registers into."""
    state = _steps.snapshot()
    yield
    _steps.restore(state)


@pytest.fixture
def empty(registry: None) -> None:
    """An empty step registry, so a generated tree holds the test's steps alone."""
    del registry
    _steps.restore({})


@pytest.fixture
def fake_phases(monkeypatch: pytest.MonkeyPatch) -> None:
    """The phases the probes below belong to, declared beside the base's, with verbs."""
    from livery.workshop import _phases
    from livery.workshop._phases import WORKSPACE_LEVEL, PhaseSpec

    known = dict(_phases.declared_phases())
    for name in ("probe", "quiet", "held", "owned", "divination", "style"):
        known[name] = PhaseSpec(name, {}, WORKSPACE_LEVEL, True, (), True, "acme.fake")
    monkeypatch.setattr(_phases, "declared_phases", lambda: known)


def _idle(ctx: PhaseContext) -> None:
    del ctx


def _flags(group: Group, key: str) -> set[str]:
    """The keyword flags the verb at *key* in *group* offers, each typed.

    The annotations resolve in the verb's module the way footman reads
    them: a flag whose type does not resolve reaches the verb as text.
    The paths carry their help text, which the verb's help prints.
    """
    task = group.tasks[key]
    parameters = inspect.signature(task).parameters.values()
    assert any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in parameters)
    extras = typing.get_type_hints(inspect.unwrap(task), include_extras=True)
    assert extras["paths"] == _steps._Paths  # pyright: ignore[reportPrivateUsage]
    hints = typing.get_type_hints(inspect.unwrap(task))

    flags = {p.name for p in parameters if p.kind is inspect.Parameter.KEYWORD_ONLY}
    for flag in flags:
        assert hints[flag] is (str if flag in ("point", "shard") else bool), flag
    return flags


# The refusals first.


def test_a_flag_no_verb_offers_refuses(registry: None) -> None:
    with pytest.raises(
        _FAILURES,
        match=re.escape(
            "step 'lint.odd' reads verbose, which no verb offers; the flags are"
            " point, shard"
        ),
    ):
        register_step(StepRecord("odd", "lint", _idle, flags=("verbose",)))


def test_a_name_that_is_no_task_address_refuses(registry: None) -> None:
    for record in (
        StepRecord("odd", "lint.deep", _idle),
        StepRecord("odd.one", "lint", _idle),
        StepRecord("", "lint", _idle),
    ):
        with pytest.raises(
            _FAILURES, match="a phase and a step's name are one word each"
        ):
            register_step(record)
    with pytest.raises(
        _FAILURES, match=re.escape("lint.default is the address of the lint verb")
    ):
        register_step(StepRecord("default", "lint", _idle))


def test_a_verb_refuses_both_fix_modes_and_a_fix_inside_ci(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(_FAILURES, match="Pass one"):
        _quality.run_steps(("layering.sibling-references",), fix=True, safe_fix=True)
    monkeypatch.setenv("CI", "true")
    with pytest.raises(_FAILURES, match="a fix inside CI"):
        _quality.run_steps(("layering.sibling-references",), safe_fix=True)


def test_a_phase_s_own_verb_builds_in_the_debug_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The gate instruments its build; a phase's own verb, a bare
    # `fm build` among them, builds the debug configuration, as
    # `fm run` does.
    seen: list[object] = []

    def walk(ctx: object, **kwargs: object) -> None:
        del ctx
        seen.append(kwargs.get("inputs"))

    (tmp_path / "workshop.toml").write_text("")
    (tmp_path / "tasks.py").write_text("x = 1\n")
    monkeypatch.setattr(_quality, "workspace_root", lambda: tmp_path)
    monkeypatch.setattr(_quality, "walk", walk)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CI", raising=False)
    _quality.run_steps(("build.compile",))
    _quality.run_steps(("build.compile",), ("tasks.py",))
    debug = {"build": {"isolated": False, "configuration": "debug"}}
    assert seen == [debug, debug]
    assert _quality.GATE_INPUTS["build"]["configuration"] == "instrumented"


def test_a_path_that_names_nothing_refuses_a_tool_s_words_among_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Checking nothing there would pass: a typo, or a tool's words given
    # as paths. The refusal says where those words go: after --, on a
    # step's own verb.

    (tmp_path / "workshop.toml").write_text("")
    (tmp_path / "tasks.py").write_text("x = 1\n")
    monkeypatch.setattr(_quality, "workspace_root", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CI", raising=False)
    with pytest.raises(
        _FAILURES,
        match=r"not a file or directory in the workspace: -k, slow\. Name files or"
        r" directories; a step's own verb hands its tool the words after --",
    ):
        _quality.run_steps(("test.ctest",), ("-k", "slow"))
    outside = str(tmp_path.parent)
    with pytest.raises(
        _FAILURES, match=re.escape(f"in the workspace: gone.py, {outside}.")
    ):
        _quality.check("tasks.py", "gone.py", outside)
    # The post-edit hook names files an edit may already have deleted.
    _quality.fix_files(("gone.py",))


# The words after --: a step's own verb hands them to its tool; the
# phase's verb, a step that wraps no tool and the gate refuse them.


def test_the_words_after_the_dashes_split_off_the_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # footman hands the variadic parameter the words after -- as well,
    # last, and names them through passthrough().
    monkeypatch.setattr("livery.footman.passthrough", lambda: ["-k", "a.py"])
    assert _steps.split_arguments(("a.py", "-k", "a.py")) == (
        ("a.py",),
        ("-k", "a.py"),
    )
    monkeypatch.setattr("livery.footman.passthrough", lambda: [])
    assert _steps.split_arguments(("a.py",)) == (("a.py",), ())


def test_only_a_steps_own_verb_hands_its_tool_the_words_after_the_dashes(
    empty: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = []

    def run_steps(
        names: tuple[str, ...], paths: tuple[str, ...], **kwargs: object
    ) -> None:
        arguments = kwargs["arguments"]
        assert isinstance(arguments, tuple)
        calls.append((names, paths, arguments))  # pyright: ignore[reportUnknownArgumentType]

    monkeypatch.setattr(_quality, "run_steps", run_steps)
    register_step(StepRecord("acme", "test", _idle, arguments=True))
    register_step(StepRecord("plain", "test", _idle))
    root = Group("root")
    generate_verbs(root)
    test = root.groups["test"]
    monkeypatch.setattr("livery.footman.passthrough", lambda: ["-k", "slow"])
    # The refusals first: the phase's verb runs several steps, and a
    # step that wraps no tool takes none; neither runs anything.
    with pytest.raises(
        _FAILURES,
        match=r"test` runs several steps, and the words after -- are one"
        r" tool's; give them to the step's own verb: .* test\.acme$",
    ):
        inspect.unwrap(test.tasks["default"])("-k", "slow")
    with pytest.raises(
        _FAILURES,
        match=re.escape("test.plain wraps no tool, so it takes no arguments after --"),
    ):
        inspect.unwrap(test.tasks["plain"])("-k", "slow")
    assert calls == []
    # The step's own verb runs it over its paths, the words its tool's.
    inspect.unwrap(test.tasks["acme"])("tasks.py", "-k", "slow")
    assert calls == [(("test.acme",), ("tasks.py",), ("-k", "slow"))]
    assert "the words after -- go to its tool" in (
        inspect.unwrap(test.tasks["acme"]).__doc__ or ""
    )


def test_the_words_after_the_dashes_reach_the_steps_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, ...]] = []
    monkeypatch.setattr(_quality, "workspace_root", lambda: tmp_path)
    monkeypatch.setattr(_quality, "_packages", lambda: ())
    monkeypatch.setattr(
        _quality, "walk", lambda ctx, **kwargs: seen.append(ctx.arguments)
    )
    monkeypatch.delenv("CI", raising=False)
    _quality.run_steps(("test.acme",), arguments=("-k", "slow"))
    assert seen == [("-k", "slow")]


def test_the_gate_takes_no_words_after_the_dashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("livery.footman.passthrough", lambda: ["-x"])
    with pytest.raises(
        _FAILURES, match=r"check` runs several steps, and the words after --"
    ):
        _quality.check("-x")


# A step answers to its one address, and leaves with its phase.


def test_a_step_answers_to_its_address_and_leaves_with_its_phase(
    registry: None, fake_phases: None
) -> None:
    register_step(StepRecord("acme", "style", _idle))
    record = step_for("style.acme")
    assert "style" in phases()
    assert verb_tree()["style"] == {"acme": record}
    unregister_step("style.acme")
    assert "style" not in phases()
    with pytest.raises(
        _FAILURES, match=re.escape("'style.acme' is not a registered step")
    ):
        step_for("style.acme")


# The tree the registry generates.


def _probes() -> None:
    register_step(StepRecord("alpha", "probe", _idle, fix=_idle))
    register_step(StepRecord("beta", "probe", _idle, flags=("point",)))
    register_step(StepRecord("gamma", "quiet", _idle))


def test_each_phase_is_a_verb_and_each_step_a_sub_task_with_its_own_flags(
    empty: None, fake_phases: None
) -> None:
    _probes()
    root = Group("root")
    generate_verbs(root)
    assert set(root.groups) == {"probe", "quiet"}
    probe, quiet = root.groups["probe"], root.groups["quiet"]
    assert set(probe.tasks) == {"default", "alpha", "beta"}
    assert set(quiet.tasks) == {"default", "gamma"}
    # The phase's verb offers what any of its steps reads; each step's
    # sub-task offers what that step reads and nothing more.
    assert _flags(probe, "default") == {"fix", "safe_fix", "point"}
    assert _flags(probe, "alpha") == {"fix", "safe_fix"}
    assert _flags(probe, "beta") == {"point"}
    assert _flags(quiet, "default") == set()
    assert _flags(quiet, "gamma") == set()


def test_a_verb_runs_its_steps_through_the_walk(
    empty: None, fake_phases: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[tuple[str, ...], tuple[str, ...], dict[str, object]]] = []

    def run_steps(
        names: tuple[str, ...], paths: tuple[str, ...] = (), **flags: object
    ) -> None:
        calls.append((names, paths, flags))

    monkeypatch.setattr(_quality, "run_steps", run_steps)
    _probes()
    root = Group("root")
    generate_verbs(root)
    probe = root.groups["probe"]

    def body(key: str) -> Callable[..., None]:
        verb: Callable[..., None] = inspect.unwrap(probe.tasks[key])
        return verb

    body("default")("a.py", fix=True)
    body("beta")(point="nightly")
    assert calls == [
        (
            ("probe.alpha", "probe.beta"),
            ("a.py",),
            {
                "fix": True,
                "safe_fix": False,
                "point": "",
                "shard": "",
                "arguments": (),
            },
        ),
        (
            ("probe.beta",),
            (),
            {
                "fix": False,
                "safe_fix": False,
                "point": "nightly",
                "shard": "",
                "arguments": (),
            },
        ),
    ]

    # The phase's verb reads its steps when it runs, so a step
    # registered after the generation runs under it too.
    register_step(StepRecord("delta", "probe", _idle))
    calls.clear()
    body("default")()
    assert calls[0][0] == ("probe.alpha", "probe.beta", "probe.delta")


def test_a_verb_offers_the_shard_where_a_step_reads_it(
    empty: None, fake_phases: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []

    def run_steps(
        names: tuple[str, ...], paths: tuple[str, ...] = (), **flags: object
    ) -> None:
        del names, paths
        calls.append(flags)

    monkeypatch.setattr(_quality, "run_steps", run_steps)
    register_step(StepRecord("alone", "probe", _idle, flags=("shard",)))
    register_step(StepRecord("pointed", "probe", _idle, flags=("point", "shard")))
    register_step(StepRecord("fixed", "probe", _idle, fix=_idle, flags=("shard",)))
    register_step(
        StepRecord("whole", "probe", _idle, fix=_idle, flags=("point", "shard"))
    )
    register_step(StepRecord("plain", "probe", _idle, fix=_idle))
    root = Group("root")
    generate_verbs(root)
    probe = root.groups["probe"]
    # --shard where a step reads it, beside whatever else it reads, and
    # nowhere else: a step without the flag refuses --shard as it
    # refuses --point, since its verb does not offer it.
    assert _flags(probe, "alone") == {"shard"}
    assert _flags(probe, "pointed") == {"point", "shard"}
    assert _flags(probe, "fixed") == {"fix", "safe_fix", "shard"}
    assert _flags(probe, "whole") == {"fix", "safe_fix", "point", "shard"}
    assert _flags(probe, "plain") == {"fix", "safe_fix"}
    assert _flags(probe, "default") == {"fix", "safe_fix", "point", "shard"}
    # Every body hands the shard on as it was given.
    for key in ("alone", "pointed", "fixed", "whole", "default"):
        inspect.unwrap(probe.tasks[key])(shard="2of4")
    assert (
        calls
        == [
            {
                "fix": False,
                "safe_fix": False,
                "point": "",
                "shard": "2of4",
                "arguments": (),
            }
        ]
        * 5
    )


def test_an_address_an_existing_verb_holds_is_served_by_it(
    empty: None, fake_phases: None
) -> None:
    _probes()
    register_step(StepRecord("check", "held", _idle))
    register_step(StepRecord("new", "held", _idle))
    register_step(StepRecord("check", "owned", _idle))
    root = Group("root")

    def own() -> None:
        """A verb of the root's own at a step's address."""

    def owned() -> None:
        """A verb of the root's own at a phase's address."""

    held = root.group("held", help="A group of another verb's")
    held.task(name="check")(own)
    root.task(name="owned")(owned)
    generate_verbs(root)
    # The group keeps its own task at the step's address and gets no
    # default, since the phase's verb is not the generator's to make
    # there; a step at a free address in it still gets its sub-task.
    assert set(held.tasks) == {"check", "new"}
    assert inspect.unwrap(held.tasks["check"]) is own
    # A task at the phase's address serves the phase: no group is made.
    assert "owned" not in root.groups
    assert inspect.unwrap(root.tasks["owned"]) is owned


def test_a_second_generation_follows_the_registry(
    empty: None, fake_phases: None
) -> None:
    _probes()
    root = Group("root")
    generate_verbs(root)
    probe, quiet = root.groups["probe"], root.groups["quiet"]
    alpha = probe.tasks["alpha"]
    # Nothing changed: every verb is kept as it was.
    generate_verbs(root)
    assert probe.tasks["alpha"] is alpha
    # A step that fixes joins a phase whose verb offered no fix: the
    # phase's verb is remade with the flag; a withdrawn step's sub-task
    # goes, and a phase left with no step goes with its group.
    register_step(StepRecord("fixer", "quiet", _idle, fix=_idle))
    unregister_step("probe.beta")
    generate_verbs(root)
    assert _flags(quiet, "default") == {"fix", "safe_fix"}
    assert _flags(quiet, "fixer") == {"fix", "safe_fix"}
    assert set(probe.tasks) == {"default", "alpha"}
    assert _flags(probe, "default") == {"fix", "safe_fix"}
    unregister_step("probe.alpha")
    generate_verbs(root)
    assert "probe" not in root.groups


def test_the_builtin_steps_generate_the_phase_verbs(python_steps: object) -> None:
    # The base's own, each over the workspace: an extension mounted
    # earlier in this process (the docs extension's lint steps) and the
    # fakes add theirs beside them, and a package's build and test steps
    # are its extensions', cmake's among them.
    tree = {
        phase: {step for step, record in steps.items() if record.extension == SELF}
        for phase, steps in verb_tree().items()
    }
    assert tree == {
        "format": set(),
        "layering": {"graph", "base-imports-no-extension", "sibling-references"},
        "lint": set(),
        "provenance": {"check"},
        "drift": {"check"},
        "test": set(),
        "typecheck": set(),
    }
    root = Group("root")
    generate_verbs(root)
    test = root.groups["test"]
    # --point and --shard where a step reads them, and nowhere else.
    assert _flags(test, "default") == {"point", "shard"}
    assert _flags(test, "fake") == {"point", "shard"}
    assert _flags(root.groups["layering"], "sibling-references") == {
        "fix",
        "safe_fix",
    }


def test_a_phase_with_no_listed_step_has_no_verb(
    registry: None, fake_phases: None
) -> None:
    # The fallback first: no step of the phase, no verb for it.
    root = Group("root")
    generate_verbs(root)
    assert "divination" not in root.groups
    register_step(StepRecord("crystal", "divination", _idle))
    generate_verbs(root)
    assert "divination" in root.groups
    # Withdrawn, the phase's verb goes with its last step.
    unregister_step("divination.crystal")
    generate_verbs(root)
    assert "divination" not in root.groups


def test_a_phase_declared_without_a_verb_gets_none(empty: None) -> None:
    from livery.workshop._phases import base_phases

    # The base's phases: a step per phase, and a verb for each phase
    # that declares one, the gated phases; the train's and the engines'
    # own phases get none.
    for name in base_phases():
        register_step(StepRecord("acme", name, _idle))
    with_verb = {name for name, spec in base_phases().items() if spec.verb}
    assert with_verb == {
        "build",
        "drift",
        "examples",
        "format",
        "layering",
        "lint",
        "provenance",
        "test",
        "typecheck",
        "typecomplete",
    }
    assert set(verb_tree()) == with_verb
    root = Group("root")
    generate_verbs(root)
    assert set(root.groups) == with_verb
    assert set(root.tasks) == set()
    for name in ("stamp", "publish", "verify", "sync", "clean", "run"):
        assert name not in root.groups, name
