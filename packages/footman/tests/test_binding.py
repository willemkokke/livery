"""The executor: coercion, variadic/passthrough, and chain semantics."""

from __future__ import annotations

import enum
import uuid
from pathlib import Path
from typing import Annotated, Literal

import pytest

from livery.footman import _manifest
from livery.footman._executor import run_chain
from livery.footman._split import ChainError, split_chain
from livery.footman.context import Context
from livery.footman.params import env
from livery.footman.registry import Group


class Colour(enum.Enum):
    RED = "red"
    BLUE = "blue"


def _run(build_tasks, line):
    reg = Group("root")
    build_tasks(reg)
    tree = _manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, line.split())
    return reg, run_chain(reg, segments)


def test_scalar_coercion():
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def build(
            n: int = 1, ratio: float = 0.5, out: Path = Path("."), fix: bool = False
        ):
            seen.update(n=n, ratio=ratio, out=out, fix=fix)

    _, results = _run(tasks, "build --n=5 --ratio=2.5 --out=/tmp/x --fix")
    assert results[0].ok
    assert seen == {"n": 5, "ratio": 2.5, "out": Path("/tmp/x"), "fix": True}


def test_a_basic_default_types_an_unannotated_parameter():
    """`port=8000` binds an int, whether or not the flag was passed.

    Without inference the default arrived as `8000` and the supplied value
    as `'99'` — one parameter, two types, decided by whether someone typed
    the flag. Every type checker already reads the default as `int`, so the
    string was the odd one out.
    """
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def build(port=8000, ratio=1.5, name="app"):
            seen.update(port=port, ratio=ratio, name=name)

    _run(tasks, "build --port=99 --ratio=2.5 --name=web")
    assert seen == {"port": 99, "ratio": 2.5, "name": "web"}
    assert [type(v).__name__ for v in seen.values()] == ["int", "float", "str"]


def test_an_unannotated_basic_default_refuses_a_bad_value():
    """The inferred type teaches like a written one."""

    def tasks(reg):
        @reg.task
        def build(port=8000): ...

    with pytest.raises(ChainError, match=r"--port expects an integer \(got 'abc'\)"):
        _run(tasks, "build --port=abc")


def test_inference_declines_where_a_type_checker_declines():
    """`None`, containers and a bare positional stay strings.

    The rule is *infer where the checker infers*: it reads `out=None` as
    `Unknown | None` and a container default as `Unknown`, so footman reads
    them as nothing at all and hands the value over untouched — exactly as
    before inference existed.
    """
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def build(target, out=None, paths=()):
            seen.update(target=target, out=out, paths=paths)

    _run(tasks, "build 42 --out=dist --paths=src")
    assert seen == {"target": "42", "out": "dist", "paths": "src"}


def test_literal_and_list_coercion():
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def go(mode: Literal["a", "b"] = "a", nums: list[int] | None = None):
            seen.update(mode=mode, nums=nums)

    _run(tasks, "go --mode=b --nums=1 --nums=2")
    assert seen == {"mode": "b", "nums": [1, 2]}


def test_enum_coercion():
    seen = {}

    def tasks(reg):
        @reg.task
        def paint(colour: Colour = Colour.RED):
            seen["colour"] = colour

    _run(tasks, "paint --colour=blue")
    assert seen == {"colour": Colour.BLUE}


def test_required_positionals():
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def render(template: Path, output: Path):
            seen.update(template=template, output=output)

    _run(tasks, "render a.j2 out.html")
    assert seen == {"template": Path("a.j2"), "output": Path("out.html")}


def test_keyword_only_required_option_binds_by_name():
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def shot(*argv: str, out: Path, width: int = 72):
            seen.update(argv=argv, out=out, width=width)

    _run(tasks, "shot --out=x.svg --width=80 -- --list --tree")
    assert seen == {"argv": ("--list", "--tree"), "out": Path("x.svg"), "width": 80}


def test_keyword_only_required_option_missing_refuses():
    def tasks(reg):
        @reg.task
        def shot(*argv: str, out: Path):
            del argv, out

    with pytest.raises(ChainError, match=r"required"):
        _run(tasks, "shot -- --list")


def test_variadic_plus_passthrough():
    seen = {}

    def tasks(reg):
        @reg.task
        def run(*cmd: str):
            seen["cmd"] = cmd

    _run(tasks, "run pytest -x -- --maxfail 1")
    assert seen["cmd"] == ("pytest", "-x", "--maxfail", "1")


# --- parameters declared before *args -------------------------------------------
#
# Python's calling convention: once a call passes anything positionally into
# *args, every parameter declared before it must be filled positionally too.
# The executor binds named parameters as keywords, so each of these shapes
# either collided ("got multiple values") or — worse — silently shifted the
# variadic values leftward into the named slots.


def test_named_params_bind_beside_variadic_values():
    """The loud face: `config`/`version` bound as keywords collided with the
    variadic values passed positionally before them."""
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def deploy(config: Path, version: str, *overlays: Path):
            seen.update(config=config, version=version, overlays=overlays)

    _, results = _run(tasks, "deploy a.toml 1.2.3 b.toml c.toml")
    assert results[0].ok, results[0].error
    assert seen == {
        "config": Path("a.toml"),
        "version": "1.2.3",
        "overlays": (Path("b.toml"), Path("c.toml")),
    }


def test_a_defaulted_param_keeps_its_default_beside_variadic_values():
    """The silent face: with `marker` absent from the call, the first
    variadic value shifted into it — wrong data under a green exit."""
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def suite(marker: str = "", *pytest_args):
            seen.update(marker=marker, pytest_args=pytest_args)

    _, results = _run(tasks, "suite x y")
    assert results[0].ok, results[0].error
    assert seen == {"marker": "", "pytest_args": ("x", "y")}


def test_passthrough_lands_in_varargs_not_a_named_param():
    """`--` passthrough always has a home in a task's *args — it must never
    bind a named parameter instead. This is getting-started's own example
    (`def test(marker="", *pytest_args)`): `-- -q -x` gave marker='-q'."""
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def suite(marker: str = "", *pytest_args):
            seen.update(marker=marker, pytest_args=pytest_args)

    _, results = _run(tasks, "suite -- -q -x")
    assert results[0].ok, results[0].error
    assert seen == {"marker": "", "pytest_args": ("-q", "-x")}


def test_a_supplied_option_binds_beside_variadic_values():
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def suite(marker: str = "", *pytest_args):
            seen.update(marker=marker, pytest_args=pytest_args)

    _, results = _run(tasks, "suite --marker=slow x y")
    assert results[0].ok, results[0].error
    assert seen == {"marker": "slow", "pytest_args": ("x", "y")}


def test_positional_only_then_named_then_varargs():
    """A leading positional-only run was already passed positionally; a named
    parameter after it still collided with the variadic values."""
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def mixed(config: Path, /, version: str, *overlays: Path):
            seen.update(config=config, version=version, overlays=overlays)

    _, results = _run(tasks, "mixed a.toml 1.2.3 b.toml")
    assert results[0].ok, results[0].error
    assert seen == {
        "config": Path("a.toml"),
        "version": "1.2.3",
        "overlays": (Path("b.toml"),),
    }


def test_a_skipped_defaulted_param_is_filled_to_reach_varargs():
    """`second` was not supplied, but the call must still fill its slot (with
    the default) for the variadic values to land in *rest."""
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def wrap(first: str, second: str = "kept", *rest: str):
            seen.update(first=first, second=second, rest=rest)

    _, results = _run(tasks, "wrap one r1 r2")
    assert results[0].ok, results[0].error
    assert seen == {"first": "one", "second": "kept", "rest": ("r1", "r2")}


def test_an_env_fallback_binds_beside_variadic_values(monkeypatch):
    """env() fills the parameter without a CLI mention — the same keyword
    collision, reached without typing the option."""
    monkeypatch.setenv("SUITE_MARKER", "slow")
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def suite(marker: Annotated[str, env("SUITE_MARKER")] = "", *pytest_args):
            seen.update(marker=marker, pytest_args=pytest_args)

    _, results = _run(tasks, "suite x y")
    assert results[0].ok, results[0].error
    assert seen == {"marker": "slow", "pytest_args": ("x", "y")}


def test_keyword_only_options_stay_keyword_beside_varargs():
    """Parameters after *args are keyword-only and must stay keywords — only
    the ones before it move to positional."""
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def suite(first: str, *rest: str, flag: bool = False):
            seen.update(first=first, rest=rest, flag=flag)

    _, results = _run(tasks, "suite x y --flag")
    assert results[0].ok, results[0].error
    assert seen == {"first": "x", "rest": ("y",), "flag": True}


def test_ctx_named_params_and_varargs_together():
    """run_task injects ctx as the first positional itself, so the drain must
    skip its slot and still fill the named ones after it."""
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def deploy(ctx: Context, version: str, *extras: str):
            seen.update(ctx=type(ctx).__name__, version=version, extras=extras)

    _, results = _run(tasks, "deploy 1.2.3 e1 e2")
    assert results[0].ok, results[0].error
    assert seen == {"ctx": "Context", "version": "1.2.3", "extras": ("e1", "e2")}


def test_passthrough_without_varargs_reaches_context():
    from livery.footman import passthrough

    seen = {}

    def tasks(reg):
        @reg.task
        def build(x: int = 1):
            seen["pt"] = passthrough()

    _run(tasks, "build -- a b")
    assert seen["pt"] == ["a", "b"]  # available even with no *args


def test_failure_stops_chain():
    ran = []

    def tasks(reg):
        @reg.task
        def a():
            ran.append("a")
            raise RuntimeError("boom")

        @reg.task
        def b():
            ran.append("b")

    _, results = _run(tasks, "a b")
    assert ran == ["a"]
    assert results[0].ok is False
    assert isinstance(results[0].error, RuntimeError)
    # The stopped segment is accounted for, not silently absent: a `skipped`
    # row, blamed on the failure that stopped the chain.
    assert [(r.task, r.state) for r in results] == [("a", ""), ("b", "skipped")]
    assert results[1].blocked_by == "a"


def test_keep_going_runs_everything():
    ran = []
    reg = Group("root")

    @reg.task
    def a():
        ran.append("a")
        return 1  # non-zero exit code

    @reg.task
    def b():
        ran.append("b")

    tree = _manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, ["a", "b"])
    results = run_chain(reg, segments, keep_going=True)
    assert ran == ["a", "b"]
    assert results[0].code == 1 and results[0].ok is False
    assert results[1].ok is True


def test_int_return_is_exit_code():
    def tasks(reg):
        @reg.task
        def a():
            return 3

    _, results = _run(tasks, "a")
    assert results[0].ok is False
    assert results[0].code == 3


def test_raised_exception_is_exit_code_1():
    def tasks(reg):
        @reg.task
        def a():
            raise RuntimeError("boom")

    _, results = _run(tasks, "a")
    assert results[0].ok is False
    assert results[0].code == 1  # a raised error carries no code -> flat 1
    assert isinstance(results[0].error, RuntimeError)


def test_positional_only_parameter_binds():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(target: str, /):
            seen["target"] = target

    _, results = _run(tasks, "build web")
    assert results[0].ok
    assert seen["target"] == "web"


def test_positional_only_mixed_with_regular():
    seen = {}

    def tasks(reg):
        @reg.task
        def f(a: str, /, b: int = 2):
            seen["ab"] = (a, b)

    _, results = _run(tasks, "f hello --b=5")
    assert results[0].ok
    assert seen["ab"] == ("hello", 5)


def test_positional_only_default_hole_is_filled():
    seen = {}

    def tasks(reg):
        @reg.task
        def f(a: str = "x", b: str = "y", /):
            seen["ab"] = (a, b)

    _, results = _run(tasks, "f --b=z")
    assert results[0].ok
    assert seen["ab"] == ("x", "z")  # skipped `a` filled from its default


# --- mixed unions (choices + types) ------------------------------------------


def test_union_literal_and_int_accepts_either():
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def go(x: Literal["fast", "slow"] | int = 1):
            seen["x"] = x

    _run(tasks, "go --x=fast")
    # Read into locals per run: mypy's narrowing of `seen["x"]` would
    # otherwise survive the second _run() and clash with the new value.
    as_choice = seen["x"]
    assert as_choice == "fast"
    _run(tasks, "go --x=7")
    as_int = seen["x"]
    assert as_int == 7 and type(as_int) is int


def test_union_literal_and_int_rejects_neither():
    def tasks(reg):
        @reg.task
        def go(x: Literal["fast", "slow"] | int = 1): ...

    with pytest.raises(ChainError, match=r"one of fast\|slow, or an integer"):
        _run(tasks, "go --x=nope")


def test_union_literal_and_int_manifest_carries_both():
    def tasks(reg):
        @reg.task
        def go(x: Literal["fast", "slow"] | int = 1): ...

    reg = Group("root")
    tasks(reg)
    spec = _manifest.build_manifest(reg)["tree"]["tasks"]["go"]["params"][0]
    assert spec["choices"] == ["fast", "slow"]
    assert spec["types"] == ["int"]


def test_union_literal_value_coerces_to_int():
    seen = {}

    def tasks(reg):
        @reg.task
        def f(x: Literal[5] | str = "a"):
            seen["x"] = x

    _run(tasks, "f --x=5")
    assert seen["x"] == 5 and type(seen["x"]) is int


def test_union_enum_member_binds():
    seen = {}

    def tasks(reg):
        @reg.task
        def paint(c: Colour | int = 0):
            seen["c"] = c

    _run(tasks, "paint --c=red")
    assert seen["c"] is Colour.RED


def test_union_custom_type_binds_and_is_not_rejected():
    identifier = "550e8400-e29b-41d4-a716-446655440000"
    seen = {}

    def tasks(reg):
        @reg.task
        def rec(id: uuid.UUID | int = 0):
            seen["id"] = id

    _run(tasks, f"rec --id={identifier}")
    assert seen["id"] == uuid.UUID(identifier)
