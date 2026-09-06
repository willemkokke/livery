"""The decorator surface: naming, nesting, and collision detection."""

from __future__ import annotations

import types

import pytest

from livery.footman import Context, registry
from livery.footman.params import Forward
from livery.footman.registry import Group, RegistrationError


def test_sample_fixture_stays_out_of_the_global_registry(root):
    # F64: the `root` fixture loads the sample tasks under registry.capture(), so
    # they populate the fixture's group but never leak into the global root.
    assert "check" in root.tasks  # the fixture really did load the sample tasks
    assert "check" not in registry.root.tasks  # ...but not into the process global


def test_task_name_normalised_to_hyphens():
    g = Group("root")

    @g.task
    def add_word(): ...

    assert "add-word" in g.tasks


def test_group_name_normalised_and_returned():
    g = Group("root")
    sub = g.group("my_group", help="h")
    assert sub.name == "my-group"
    assert "my-group" in g.groups
    assert sub.help == "h"


def test_explicit_name_override():
    g = Group("root")

    @g.task(name="build")
    def docs_build(): ...

    assert "build" in g.tasks
    assert "docs-build" not in g.tasks


def test_duplicate_task_rejected():
    g = Group("root")

    @g.task
    def a(): ...

    with pytest.raises(ValueError, match="already has a task"):

        @g.task(name="a")
        def other(): ...


def test_task_group_collision_rejected():
    g = Group("root")
    g.group("x")

    with pytest.raises(ValueError, match="already has a group"):

        @g.task(name="x")
        def x(): ...


def test_collision_is_a_registration_error():
    g = Group("root")

    @g.task
    def build(): ...

    with pytest.raises(RegistrationError, match="already has a task"):
        g.task(name="build")(lambda: None)


def test_infinite_implies_no_progress():
    from livery.footman.registry import Group, is_infinite, wants_progress

    g = Group("root")

    @g.task(infinite=True)
    def serve(): ...

    @g.task
    def plain(): ...

    @g.task(progress=False)
    def repl(): ...

    assert is_infinite(serve) and not wants_progress(serve)  # the implication
    assert not is_infinite(plain) and wants_progress(plain)
    assert not is_infinite(repl) and not wants_progress(repl)  # timing-only opt-out


def test_confirm_and_interactive_stamp_and_read():
    from livery.footman.registry import (
        Group,
        is_interactive,
        task_confirm,
        wants_progress,
    )

    g = Group("root")

    @g.task(confirm="ship it?", interactive=True)
    def deploy(): ...

    @g.task
    def plain(): ...

    assert task_confirm(deploy) == "ship it?"
    assert is_interactive(deploy) and not wants_progress(deploy)  # human-wait
    assert task_confirm(plain) == "" and not is_interactive(plain)
    assert wants_progress(plain)


# --- @group.default ----------------------------------------------------------


def test_group_default_registers_a_flags_only_action():
    reg = Group("root")
    lint = reg.group("lint")

    @lint.default
    def lint_all(fix: Forward[bool] = False): ...

    assert lint.default_task is lint_all


def test_group_default_accepts_a_positional_parameter():
    # The old no-positional rule dissolved with dotted addressing: a bare
    # word after the group is the default's value, never a child address.
    reg = Group("root")
    deploy = reg.group("deploy")

    @deploy.default
    def deploy_all(target: str): ...

    assert deploy.default_task is deploy_all


def test_group_default_allows_the_injected_ctx_param():
    reg = Group("root")
    build = reg.group("build")

    @build.default
    def build_all(ctx: Context, fix: Forward[bool] = False): ...

    assert build.default_task is build_all


def test_task_and_group_names_reject_dots_and_whitespace():
    # `.` is the address separator (`fm docs.serve`): a name containing one
    # would alias into fake nesting or become unreachable; whitespace can
    # never survive shell word-splitting. Both refuse at load time.
    reg = Group("root")

    with pytest.raises(RegistrationError, match=r"not a legal name"):
        reg.group("v2.0")

    with pytest.raises(RegistrationError, match=r"not a legal name"):

        @reg.task(name="docs.build")
        def dotted(): ...

    with pytest.raises(RegistrationError, match=r"not a legal name"):

        @reg.task(name="two words")
        def spaced(): ...


# --- the task handle: `@task` returns the object it registers ------------------
# A task is a `_TaskFn` handle over the decorated function, and the framework
# keys on its identity (DAG dedup, the cascade's self-shadow test, provenance
# stamps) and on `inspect` answering about the *function*. These pin both.


def test_the_decorator_returns_the_registered_handle():
    # One handle per decoration, registered *and* returned: a second handle
    # over the same function would read as a different task everywhere
    # identity is the key.
    reg = Group("root")

    @reg.task
    def build(): ...

    assert reg.tasks["build"] is build
    assert isinstance(build, registry._TaskFn)
    assert build.__wrapped__ is not build  # the function is still in there


def test_the_handle_is_transparent_to_inspection():
    # Every introspection footman does on a task must answer about the
    # function: the command name, help text, the CLI signature, `--where`'s
    # source location, and `inspect.unwrap`.
    import inspect

    reg = Group("root")

    @reg.task
    def greet(name: str, loud: bool = False) -> None:
        """Say hello."""

    assert greet.__name__ == "greet"
    assert greet.__doc__ == "Say hello."
    assert inspect.getdoc(greet) == "Say hello."  # not the handle's class doc
    assert list(inspect.signature(greet).parameters) == ["name", "loud"]
    assert inspect.signature(greet, eval_str=True).parameters["name"].annotation is str
    assert getattr(greet, "__code__").co_name == "greet"  # what `--where` reads
    assert registry.task_source_file(greet) == __file__  # what TaskView reports
    assert "def greet" in registry.task_source(greet)
    body = inspect.unwrap(greet)
    assert body is not greet and isinstance(body, types.FunctionType)
    assert callable(greet)
    assert "greet" in repr(greet)


def test_a_marker_stamped_below_the_task_decorator_is_read_back():
    # `@requires` may be stacked either side of `@task`. Below it, the check
    # lands on the bare function *before* the handle exists — the handle must
    # still report it, or availability silently passes.
    reg = Group("root")

    @reg.task
    @registry.requires(lambda: False, reason="nope")
    def gated(): ...

    assert registry.availability(gated) == "nope"


def test_source_reading_survives_the_handle():
    # `inspect.getsource`/`getsourcefile` do NOT follow `__wrapped__`, which
    # is why reading a task's source goes through `registry.task_source*`. An
    # empty-body default is detected by reading source, so losing the unwrap
    # would silently stop every group fan-out and make source_file None for
    # every task.
    reg = Group("root")
    lint = reg.group("lint")

    @lint.default
    def lint_all():
        """Lint everything."""

    default = lint.default_task
    assert default is not None
    assert registry.fans_out(default)  # the source read still works
    view = registry.Tasks(reg)["lint.default"]
    assert view.source_file is not None and view.source_file.endswith(".py")


def test_the_source_hash_ignores_formatting_but_not_edits():
    # A tripwire for "the body moved": normalised through the AST, so a
    # reformat does not move it (this repo's own gate runs `ruff format`, which
    # would otherwise look like every task changed) while a real edit does.
    import ast
    import hashlib
    import textwrap

    def digest(src: str) -> str:
        shape = ast.dump(ast.parse(textwrap.dedent(src)), include_attributes=False)
        return hashlib.sha256(shape.encode("utf-8")).hexdigest()

    tight = "def build():\n    x = 1\n    return x\n"
    spaced = "def build():\n\n    x = 1  # a comment\n\n    return x\n"
    edited = "def build():\n    x = 2\n    return x\n"
    assert digest(tight) == digest(spaced)  # formatting and comments are free
    assert digest(tight) != digest(edited)  # a real change moves it

    reg = Group("root")

    @reg.task
    def real():
        """Docstring."""
        return 1

    assert registry.task_source_hash(real) == digest(registry.task_source(real))
    assert registry.task_source_hash(lambda: 1) is not None  # readable source
