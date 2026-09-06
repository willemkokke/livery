"""The validation markers: exists/isfile/isdir, between/range, env(), check()."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import pytest

from livery.footman import _manifest
from livery.footman._executor import run_chain
from livery.footman._split import ChainError, split_chain
from livery.footman.params import between, check, default, env, exists, isdir, isfile
from livery.footman.registry import Group

# Module level, or `from __future__ import annotations` leaves the name
# unresolvable when the spec is built.
_tag_calls: list[int] = []


def _next_tag() -> str:
    _tag_calls.append(len(_tag_calls) + 1)
    return f"call-{len(_tag_calls)}"


ComputedTag = Annotated[str, default(_next_tag)]

# A default reading its left siblings: `shell` and the variadic `keys` both
# precede `title` in the signature, so both are resolved by the time it runs.
SiblingTitle = Annotated[
    str, default(lambda p: f"{p['shell']} · {','.join(p['keys'])}")
]

EchoesTag = Annotated[str, default(lambda p: f"about {p['tag']}")]

# A validator that records the sibling view it was handed, so a test can assert
# on the view itself rather than on a message about it.
_views: list[dict[str, Any]] = []


def _record_view(_value: Any, params: dict[str, Any]) -> None:
    _views.append(dict(params))


Watched = Annotated[str, check(_record_view)]
WatchedList = Annotated[list[str], check(_record_view)]


ReadsRight = Annotated[str, check(lambda _v, p: p["later"])]
GetsRight = Annotated[str, check(lambda _v, p: p.get("later"))]
ReadsSelf = Annotated[str, check(lambda _v, p: p["me"])]
ReadsNothing = Annotated[str, check(lambda _v, p: p["no_such_parameter"])]


def _raises_type_error(_value: str) -> None:
    raise TypeError("a bug inside the validator, not an arity mismatch")


def _even(v: int) -> None:
    if v % 2:
        raise ValueError(f"{v} must be even")


def build_tree(build):
    reg = Group("root")
    build(reg)
    return reg, _manifest.build_manifest(reg)["tree"]


def run(build, line):
    reg, tree = build_tree(build)
    _, segments = split_chain(tree, line.split())
    return run_chain(reg, segments)


# --- path requirements (eager) -------------------------------------------------


def test_isfile_accepts_a_real_file(tmp_path):
    real = tmp_path / "cfg.toml"
    real.write_text("x = 1\n")
    seen = {}

    def tasks(reg):
        @reg.task
        def render(template: Annotated[Path, isfile]):
            seen["t"] = template

    run(tasks, f"render {real}")
    assert seen["t"] == real


def test_path_requirements_teach_eagerly(tmp_path):
    # Markers must be module-level names: `from __future__ import annotations`
    # stringifies these, and eval_str resolves them in module globals.
    def tasks(reg):
        @reg.task
        def rm(target: Annotated[Path, exists]): ...

        @reg.task
        def render(template: Annotated[Path, isfile]): ...

        @reg.task
        def clean(build_dir: Annotated[Path, isdir]): ...

    _, tree = build_tree(tasks)
    missing = str(tmp_path / "missing")
    with pytest.raises(ChainError, match="existing path"):
        split_chain(tree, ["rm", missing])
    with pytest.raises(ChainError, match="existing file"):
        split_chain(tree, ["render", missing])
    with pytest.raises(ChainError, match="existing directory"):
        split_chain(tree, ["clean", missing])


def test_isdir_rejects_a_file(tmp_path):
    f = tmp_path / "afile"
    f.write_text("")

    def tasks(reg):
        @reg.task
        def clean(build_dir: Annotated[Path, isdir]): ...

    _, tree = build_tree(tasks)
    with pytest.raises(ChainError, match="existing directory"):
        split_chain(tree, ["clean", str(f)])


# --- numeric bounds (eager) ------------------------------------------------------


def test_between_bounds_are_inclusive():
    seen = {}

    def tasks(reg):
        @reg.task
        def test_(jobs: Annotated[int, between(1, 32)] = 4):
            seen["jobs"] = jobs

    run(tasks, "test --jobs=32")
    assert seen["jobs"] == 32
    run(tasks, "test --jobs=1")
    assert seen["jobs"] == 1


def test_between_teaches_out_of_range():
    def tasks(reg):
        @reg.task
        def test_(jobs: Annotated[int, between(1, 32)] = 4): ...

    _, tree = build_tree(tasks)
    with pytest.raises(ChainError, match="between 1 and 32"):
        split_chain(tree, ["test", "--jobs=99"])


def test_a_marker_inside_a_container_element_applies_per_element(capsys):
    """`list[Annotated[int, between(1, 5)]]` used to reach the manifest
    unpeeled, warn "is not a usable type", and pass the values through as
    text. The element's markers now peel exactly as a dict value's do."""
    seen = {}

    def tasks(reg):
        @reg.task
        def take(nums: list[Annotated[int, between(1, 5)]] = ()):  # type: ignore[assignment]
            seen["nums"] = nums

    _, tree = build_tree(tasks)
    assert "usable type" not in capsys.readouterr().err
    with pytest.raises(ChainError, match="between 1 and 5"):
        split_chain(tree, ["take", "--nums=2,9"])
    run(tasks, "take --nums=2,3")
    assert seen["nums"] == [2, 3]


def test_an_outer_marker_wins_over_the_element_marker():
    # The same rule the dict branch keeps: a marker on the whole collection
    # beats one on the element when both are present.
    def tasks(reg):
        @reg.task
        def take(
            nums: Annotated[list[Annotated[int, between(1, 5)]], between(0, 3)] = (),  # type: ignore[assignment]
        ): ...

    _, tree = build_tree(tasks)
    with pytest.raises(ChainError, match="between 0 and 3"):
        split_chain(tree, ["take", "--nums=4"])
    split_chain(tree, ["take", "--nums=0"])  # legal under the outer bounds


def test_a_completer_on_the_element_serves_the_collection():
    from livery.footman import _coerce
    from livery.footman.params import suggest

    peeled = _coerce.peel(list[Annotated[str, suggest(_next_tag)]])
    assert peeled.element is str
    assert peeled.completer is not None and peeled.completer.fn is _next_tag


def test_nan_is_rejected_by_bounds():
    def tasks(reg):
        @reg.task
        def mix(ratio: Annotated[float, between(0.0, 1.0)] = 0.5): ...

    _, tree = build_tree(tasks)
    with pytest.raises(ChainError, match=r"between 0\.0 and 1\.0"):
        split_chain(tree, ["mix", "--ratio=nan"])
    split_chain(tree, ["mix", "--ratio=0.5"])  # a real value still binds


def test_env_nan_is_rejected_by_bounds(monkeypatch):
    def tasks(reg):
        @reg.task
        def mix(ratio: Annotated[float, between(0.0, 1.0), env("RATIO")] = 0.5): ...

    monkeypatch.setenv("RATIO", "nan")
    results = run(tasks, "mix")
    assert not results[0].ok
    assert "between 0.0 and 1.0" in str(results[0].error)


def test_bare_range_is_half_open():
    def tasks(reg):
        @reg.task
        def shard(index: Annotated[int, range(0, 8)] = 0): ...

    _, tree = build_tree(tasks)
    split_chain(tree, ["shard", "--index=7"])  # ok: 0..7
    with pytest.raises(ChainError, match="between 0 and 7"):
        split_chain(tree, ["shard", "--index=8"])


def test_open_ended_bound():
    def tasks(reg):
        @reg.task
        def retry(times: Annotated[int, between(1, None)] = 1): ...

    _, tree = build_tree(tasks)
    with pytest.raises(ChainError, match="at least 1"):
        split_chain(tree, ["retry", "--times=0"])


def test_bounds_apply_to_positionals_and_lists():
    def tasks(reg):
        @reg.task
        def pick(shards: Annotated[list[int], between(0, 3)] | None = None): ...

    _, tree = build_tree(tasks)
    split_chain(tree, ["pick", "--shards=0,3"])
    with pytest.raises(ChainError, match="between 0 and 3"):
        split_chain(tree, ["pick", "--shards=0,4"])


# --- env() fallback (late, validated) --------------------------------------------


def test_env_fallback_precedence(monkeypatch):
    seen = {}

    def tasks(reg):
        @reg.task
        def deploy(target: Annotated[str, env("DEPLOY_ENV")] = "staging"):
            seen["target"] = target

    run(tasks, "deploy")
    assert seen["target"] == "staging"  # no env, no flag -> default

    monkeypatch.setenv("DEPLOY_ENV", "prod")
    run(tasks, "deploy")
    assert seen["target"] == "prod"  # env beats default

    run(tasks, "deploy --target=edge")
    assert seen["target"] == "edge"  # CLI beats env


def test_env_value_is_coerced_and_bounded(monkeypatch):
    seen = {}

    def tasks(reg):
        @reg.task
        def test_(jobs: Annotated[int, between(1, 32), env("JOBS")] = 4):
            seen["jobs"] = jobs

    monkeypatch.setenv("JOBS", "8")
    run(tasks, "test")
    assert seen["jobs"] == 8  # coerced to int

    monkeypatch.setenv("JOBS", "99")
    results = run(tasks, "test")
    assert not results[0].ok  # bounds enforced for env values too
    assert "between 1 and 32" in str(results[0].error)


def test_env_uncoercible_value_is_rejected(monkeypatch):
    def tasks(reg):
        @reg.task
        def test_(jobs: Annotated[int, between(1, 32), env("JOBS")] = 4): ...

    monkeypatch.setenv("JOBS", "abc")
    results = run(tasks, "test")
    assert not results[0].ok  # no longer binds the raw string
    assert "expects an integer" in str(results[0].error)
    assert "$JOBS" in str(results[0].error)


def test_env_bool_uncoercible_is_rejected(monkeypatch):
    def tasks(reg):
        @reg.task
        def deploy(prod: Annotated[bool, env("PROD")] = False): ...

    monkeypatch.setenv("PROD", "maybe")
    results = run(tasks, "deploy")
    assert not results[0].ok  # never binds a truthy "maybe"
    assert "true or false" in str(results[0].error)


# --- variadic (*args) validation ---------------------------------------------


def test_variadic_annotated_bounds_are_enforced():
    seen = {}

    def tasks(reg):
        @reg.task
        def add(*nums: Annotated[int, between(0, 10)]):
            seen["sum"] = sum(nums)

    run(tasks, "add 2 3")
    assert seen["sum"] == 5

    _, tree = build_tree(tasks)
    with pytest.raises(ChainError, match="between 0 and 10"):
        split_chain(tree, ["add", "2", "99"])


def test_variadic_type_error_is_taught():
    def tasks(reg):
        @reg.task
        def add(*nums: int): ...

    _, tree = build_tree(tasks)
    with pytest.raises(ChainError, match="expects an integer"):
        split_chain(tree, ["add", "2", "x"])


def test_variadic_annotated_manifest_has_types_and_bounds():
    def tasks(reg):
        @reg.task
        def add(*nums: Annotated[int, between(0, 10)]): ...

    _, tree = build_tree(tasks)
    spec = tree["tasks"]["add"]["params"][0]
    assert spec["types"] == ["int"]
    assert spec["min"] == 0 and spec["max"] == 10


# --- dict value markers ------------------------------------------------------


def test_dict_value_bounds_are_enforced():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(opts: dict[str, Annotated[int, between(1, 5)]] | None = None):
            seen["opts"] = opts

    run(tasks, "build --opts=x=3")
    assert seen["opts"] == {"x": 3}

    _, tree = build_tree(tasks)
    with pytest.raises(ChainError, match="between 1 and 5"):
        split_chain(tree, ["build", "--opts=x=99"])


def test_dict_value_check_runs_per_value():
    def tasks(reg):
        @reg.task
        def build(counts: dict[str, Annotated[int, check(_even)]] | None = None): ...

    results = run(tasks, "build --counts=x=3")  # 3 is odd -> check fails at bind
    assert not results[0].ok
    assert isinstance(results[0].error, ValueError)


def test_env_list_comma_splits(monkeypatch):
    seen = {}

    def tasks(reg):
        @reg.task
        def build(tags: Annotated[list[str], env("TAGS")] | None = None):
            seen["tags"] = tags

    monkeypatch.setenv("TAGS", "a,b,c")
    run(tasks, "build")
    assert seen["tags"] == ["a", "b", "c"]


def test_default_fn_is_computed_at_bind_not_import():
    _tag_calls.clear()
    seen: dict[str, Any] = {}

    def tasks(reg):
        @reg.task
        def build(*, tag: ComputedTag = "") -> None:
            seen["tag"] = tag

    run(tasks, "build")
    first = seen["tag"]
    run(tasks, "build")
    # A fresh answer per run, not one frozen at import — which is the whole
    # reason to have it: a Python default could never say which shell this is.
    assert seen["tag"] != first

    run(tasks, "build --tag=explicit")
    assert seen["tag"] == "explicit"  # the command line still outranks it


def test_default_fn_is_computed_for_a_body_call_too():
    seen: dict[str, Any] = {}

    def tasks(reg):
        @reg.task
        def build(*, tag: ComputedTag = "") -> None:
            seen["tag"] = tag

        @reg.task
        def wrap() -> None:
            build()

    run(tasks, "wrap")
    # A parameter whose only marker is `default(fn)` has nothing to validate,
    # so it never entered the call plan and a body call skipped the ladder
    # entirely — handing the body the sentinel the marker exists to replace.
    # footman's own `docs.build` found this by calling `docs.shots`.
    assert seen["tag"] != ""


def test_default_fn_reads_its_left_siblings():
    seen: dict[str, Any] = {}

    def tasks(reg):
        @reg.task
        def cast(*keys: str, shell: str = "zsh", title: SiblingTitle = "") -> None:
            seen["title"] = title

    run(tasks, "cast a b")
    assert seen["title"] == "zsh · a,b"  # shell and the variadic, both to its left
    run(tasks, "cast --shell=fish")
    assert seen["title"] == "fish · "
    run(tasks, "cast --title=mine")
    assert seen["title"] == "mine"  # an explicit value still outranks it


def test_a_sibling_reading_default_is_not_shown_in_help():
    # There is no invocation to read at manifest time, so help shows no default
    # rather than one it would have to invent — and the *declared* one is a
    # sentinel that exists so a plain Python call still binds, so printing it
    # would advertise exactly what the marker replaces.
    def tasks(reg):
        @reg.task
        def cast(*keys: str, shell: str = "zsh", title: SiblingTitle = "") -> None: ...

    _, tree = build_tree(tasks)
    spec = next(p for p in tree["tasks"]["cast"]["params"] if p["name"] == "title")
    assert "default" not in spec


def test_default_fn_without_a_declared_default_is_a_spec_error():
    def tasks(reg):
        @reg.task
        def build(*, tag: ComputedTag) -> None: ...

    # A plain Python call outside a run has to keep working, so the marker
    # needs somewhere to fall — the same rule `env()` follows.
    with pytest.raises(_manifest.SpecError, match="needs a declared default"):
        build_tree(tasks)


def test_env_without_default_is_a_spec_error():
    def tasks(reg):
        @reg.task
        def deploy(target: Annotated[str, env("DEPLOY_ENV")]): ...

    with pytest.raises(_manifest.SpecError, match="needs a default"):
        build_tree(tasks)


def test_env_on_dict_is_a_spec_error():
    def tasks(reg):
        @reg.task
        def deploy(opts: Annotated[dict[str, str], env("OPTS")] | None = None): ...

    with pytest.raises(_manifest.SpecError, match="not supported on dict"):
        build_tree(tasks)


# --- check() validators (late) ----------------------------------------------------


def _semver(value: str) -> None:
    parts = value.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"{value!r} is not MAJOR.MINOR.PATCH")


def test_check_accepts_and_rejects():
    seen = {}

    def tasks(reg):
        @reg.task
        def tag(version: Annotated[str, check(_semver)] = "0.0.0"):
            seen["v"] = version

    run(tasks, "tag --version=1.2.3")
    assert seen["v"] == "1.2.3"

    results = run(tasks, "tag --version=nope")
    assert not results[0].ok
    assert "is not MAJOR.MINOR.PATCH" in str(results[0].error)


def test_check_runs_per_element():
    def tasks(reg):
        @reg.task
        def tag(versions: Annotated[list[str], check(_semver)] | None = None): ...

    results = run(tasks, "tag --versions=1.2.3,bad")
    assert not results[0].ok
    assert "bad" in str(results[0].error)


def test_check_applies_to_env_values(monkeypatch):
    def tasks(reg):
        @reg.task
        def tag(version: Annotated[str, check(_semver), env("VERSION")] = "0.0.0"): ...

    monkeypatch.setenv("VERSION", "not-semver")
    results = run(tasks, "tag")
    assert not results[0].ok
    assert "VERSION" in str(results[0].error)


# --- check() sees its left-hand siblings ------------------------------------------


def _newer_than_floor(v: str, params: dict[str, Any]) -> None:
    if int(v) <= int(params["floor"]):
        raise ValueError(f"must exceed floor {params['floor']}")


def _first_sees_nothing(v: str, params: dict[str, Any]) -> None:
    if params:
        raise ValueError(f"first should see no siblings, got {sorted(params)}")


def _second_sees_first(v: str, params: dict[str, Any]) -> None:
    if list(params) != ["a"]:
        raise ValueError(f"expected only 'a' to the left, got {sorted(params)}")


def _reject_mutation(v: str, params: dict[str, Any]) -> None:
    try:
        params["_probe"] = 1  # a read-only view refuses this
    except TypeError:
        return
    raise ValueError("the sibling view was mutable")


def test_check_context_sees_left_sibling():
    def tasks(reg):
        @reg.task
        def release(floor: int, version: Annotated[str, check(_newer_than_floor)]): ...

    assert run(tasks, "release 5 7")[0].ok
    results = run(tasks, "release 5 3")
    assert not results[0].ok
    assert "must exceed floor 5" in str(results[0].error)


def test_check_context_positional_order_and_empty_first():
    # the first parameter's check sees {}; the second sees the first, coerced
    def tasks(reg):
        @reg.task
        def t(
            a: Annotated[str, check(_first_sees_nothing)],
            b: Annotated[str, check(_second_sees_first)],
        ): ...

    assert run(tasks, "t x y")[0].ok


def test_check_context_is_read_only():
    def tasks(reg):
        @reg.task
        def t(a: str, b: Annotated[str, check(_reject_mutation)]): ...

    assert run(tasks, "t x y")[0].ok


def test_check_context_reaches_env_values(monkeypatch):
    def tasks(reg):
        @reg.task
        def release(
            floor: int,
            version: Annotated[str, check(_newer_than_floor), env("FLOORVER")] = "9",
        ): ...

    monkeypatch.setenv("FLOORVER", "3")
    results = run(tasks, "release 5")
    assert not results[0].ok
    assert "must exceed floor 5" in str(results[0].error)


def _echo_channel(v: str, params: dict[str, Any]) -> None:
    raise ValueError(f"channel is {params['channel']}")


def test_check_context_includes_a_defaulted_sibling():
    def tasks(reg):
        @reg.task
        def deploy(
            channel: str = "stable",
            target: Annotated[str, check(_echo_channel)] = "prod",
        ): ...

    # channel unset -> the view carries its default, not a KeyError
    unset = run(tasks, "deploy --target=prod")
    assert not unset[0].ok and "channel is stable" in str(unset[0].error)
    # a provided value overrides that default in the view
    given = run(tasks, "deploy --channel=beta --target=prod")
    assert not given[0].ok and "channel is beta" in str(given[0].error)


def test_wants_context_detects_arity():
    from livery.footman._executor import _wants_context

    assert not _wants_context(lambda v: None)  # one positional -> plain check
    assert _wants_context(lambda v, p: None)  # two positional -> contextual
    assert _wants_context(lambda v, *rest: None)  # *args accepts a second


def test_wants_context_handles_a_signatureless_callable(monkeypatch):
    import inspect

    from livery.footman._executor import _wants_context

    def _no_signature(_fn):
        raise ValueError("no signature")

    monkeypatch.setattr(inspect, "signature", _no_signature)
    assert not _wants_context(lambda v, p: None)  # can't inspect -> plain one-arg


# --- the sibling view: one answer, whichever way the task was reached -------------
#
# `check(fn)` and `default(fn)` both read it, and it is assembled differently on
# each path — from `kwargs` plus the variadic on the command-line side, from
# `bound.arguments` on the body-call side. Every test below pins the two paths
# to the same answer, because the one bug this area has actually had was them
# quietly disagreeing.


def test_reaching_rightwards_is_taught_not_a_bare_key_error():
    def tasks(reg):
        @reg.task
        def build(me: ReadsRight = "x", later: str = "z") -> None: ...

    results = run(tasks, "build --me=x --later=given")
    assert not results[0].ok
    message = str(results[0].error)
    # The parameter exists, so a typo is not the explanation — name the real
    # constraint and the fix, rather than raising `KeyError: 'later'`.
    assert "may only read parameters declared before it" in message
    assert "Move 'later' above 'me'" in message


def test_reaching_rightwards_through_get_is_taught_too():
    # The dangerous spelling: a plain dict answered `.get()` with `None` and the
    # run *succeeded*, feeding the body a value nobody chose.
    def tasks(reg):
        @reg.task
        def build(me: GetsRight = "x", later: str = "z") -> None: ...

    results = run(tasks, "build --me=x --later=given")
    assert not results[0].ok
    assert "comes after" in str(results[0].error)


def test_reading_your_own_value_is_taught():
    def tasks(reg):
        @reg.task
        def build(me: ReadsSelf = "x") -> None: ...

    results = run(tasks, "build --me=x")
    assert not results[0].ok
    assert "cannot read its own value" in str(results[0].error)


def test_an_unknown_name_is_still_an_ordinary_key_error():
    # Only a *declared* parameter earns the taught message; a typo is a
    # different mistake and keeps the mapping's own behaviour.
    def tasks(reg):
        @reg.task
        def build(me: ReadsNothing = "x") -> None: ...

    results = run(tasks, "build --me=x")
    assert isinstance(results[0].error, KeyError)


def test_a_check_runs_on_supplied_values_not_on_the_default():
    _views.clear()

    def tasks(reg):
        @reg.task
        def build(*, tag: Watched = "t") -> None: ...

    run(tasks, "build")
    assert _views == []  # nothing supplied it, so there is nothing to validate
    run(tasks, "build --tag=t")
    assert len(_views) == 1  # the same value, this time because someone said it


def test_the_view_is_the_same_from_a_chain_and_from_a_body_call():
    _views.clear()

    def tasks(reg):
        @reg.task
        def build(*paths: str, mode: str = "fast", tag: Watched = "t") -> None: ...

        @reg.task
        def wrap() -> None:
            build("a", "b", mode="slow", tag="t")

    run(tasks, "build a b --mode=slow --tag=t")
    run(tasks, "wrap")
    assert _views[0] == _views[1] == {"paths": ("a", "b"), "mode": "slow"}


def test_the_variadic_reaches_the_view_from_either_direction():
    _views.clear()

    def tasks(reg):
        @reg.task
        def build(*paths: str, tag: Watched = "t") -> None: ...

        @reg.task
        def wrap() -> None:
            build("x", "y", tag="t")

    run(tasks, "build a b --tag=t")
    run(tasks, "wrap")
    # `*args` lives outside `kwargs` on one path and inside `bound.arguments` on
    # the other, so the same validator used to see the variadic from a call and
    # an empty tuple from a chain.
    assert [v["paths"] for v in _views] == [("a", "b"), ("x", "y")]


def test_an_empty_variadic_is_an_empty_tuple_not_a_missing_key():
    _views.clear()

    def tasks(reg):
        @reg.task
        def build(*paths: str, tag: Watched = "t") -> None: ...

    run(tasks, "build --tag=t")
    assert _views[0]["paths"] == ()  # present and empty, never a KeyError


def test_the_view_stops_at_the_reader():
    _views.clear()

    def tasks(reg):
        @reg.task
        def build(tag: Watched = "t", later: str = "z") -> None: ...

    run(tasks, "build --tag=t --later=given")
    # Leftward only: a value may not depend on one that has not been resolved,
    # so `later` is absent even though the line supplied it.
    assert "later" not in _views[0]


def test_the_view_carries_a_sibling_a_computed_default_filled():
    _views.clear()

    def tasks(reg):
        @reg.task
        def build(*, tag: ComputedTag = "", note: Watched = "n") -> None: ...

    run(tasks, "build --note=n")
    # `default(fn)` resolves into `kwargs` before the next parameter binds, so
    # what a later check reads is the computed value, not the sentinel.
    assert _views[0]["tag"].startswith("call-")


def test_a_computed_default_reads_a_sibling_a_computed_default_filled():
    seen: dict[str, Any] = {}

    def tasks(reg):
        @reg.task
        def build(*, tag: ComputedTag = "", note: EchoesTag = "") -> None:
            seen["note"] = note

    run(tasks, "build")
    assert seen["note"].startswith("about call-")  # defaults chain leftward


def test_the_view_is_read_only_from_a_body_call_too():
    def tasks(reg):
        @reg.task
        def build(
            a: str = "x", b: Annotated[str, check(_reject_mutation)] = "y"
        ) -> None: ...

        @reg.task
        def wrap() -> None:
            build(a="x", b="y")

    assert run(tasks, "wrap")[0].ok


def test_a_validators_own_type_error_is_not_read_as_the_other_arity():
    # Arity is decided by inspecting the signature, never by calling and
    # catching `TypeError` — or a genuine bug inside a one-argument validator
    # would be silently retried as the two-argument form and vanish.
    def tasks(reg):
        @reg.task
        def build(tag: Annotated[str, check(_raises_type_error)] = "t") -> None: ...

    results = run(tasks, "build --tag=t")
    assert not results[0].ok
    assert isinstance(results[0].error, TypeError)


def test_a_contextual_check_on_each_element_of_a_collection():
    _views.clear()

    def tasks(reg):
        @reg.task
        def build(env: str = "dev", tags: WatchedList | None = None) -> None: ...

    run(tasks, "build --tags=a,b")
    # Element-level, so once per value — and each sees the same siblings.
    assert len(_views) == 2
    assert all(v == {"env": "dev"} for v in _views)


# --- manifest spec keys are additive ----------------------------------------------


def test_marker_manifest_keys():
    def tasks(reg):
        @reg.task
        def deploy(
            config: Annotated[Path, isfile],
            jobs: Annotated[int, between(1, 32)] = 4,
            target: Annotated[str, env("DEPLOY_ENV")] = "staging",
            version: Annotated[str, check(_semver)] = "0.0.0",
        ): ...

    _, tree = build_tree(tasks)
    by_name = {p["name"]: p for p in tree["tasks"]["deploy"]["params"]}
    assert by_name["config"]["path"] == "file"
    assert by_name["jobs"]["min"] == 1 and by_name["jobs"]["max"] == 32
    assert by_name["target"]["env"] == "DEPLOY_ENV"
    assert "check" not in by_name["version"]  # functions never serialize


# --- opaque annotations warn -------------------------------------------------------


def test_unresolvable_annotation_warns(capsys):
    def tasks(reg):
        def go(x="d"): ...

        # Set post-hoc so no linter trips: the string never resolves to a
        # type, exactly like a typo'd annotation under PEP 563.
        go.__annotations__ = {"x": "NoSuchType"}
        reg.task(go)

    _manifest._warned.clear()
    build_tree(tasks)
    err = capsys.readouterr().err
    assert "<x>" in err and "did not resolve" in err
    assert "UserWarning" not in err  # one clean line, not Python's dressing
