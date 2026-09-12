"""Union types, one-or-many values, and dynamic completion (`suggest`)."""

from __future__ import annotations

import datetime
import enum
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated, Any, NamedTuple

import pytest

from livery.footman import _app, _manifest
from livery.footman._coerce import peel
from livery.footman._complete import complete
from livery.footman._describe import example_parts, listed_params, usage_parts
from livery.footman._executor import EX_USAGE, run_chain
from livery.footman._split import ChainError, split_chain
from livery.footman.params import (
    Arg,
    Exists,
    Forward,
    IsDir,
    IsFile,
    Many,
    NoSplit,
    forward,
    hidden,
    nosplit,
    suggest,
)
from livery.footman.registry import Group


class _StandInMarker:
    """A plugin-shaped marker: an instance, deliberately not callable."""


_PluginMarker = _StandInMarker()

# A module-level completer so `eval_str` can resolve it from a tasks file that
# uses `from __future__ import annotations` (real completers live at module top).
_DEDUP_CALLS: list[int] = []


def _dedup_projects() -> list[str]:
    _DEDUP_CALLS.append(1)
    return ["a", "b"]


class Version:
    """A user type whose constructor takes a string."""

    def __init__(self, text: str) -> None:
        self.text = text

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Version) and other.text == self.text


def build_tree(build):
    reg = Group("root")
    build(reg)
    return reg, _manifest.build_manifest(reg)["tree"]


class Box(NamedTuple):
    width: int
    height: int


@dataclass
class Spot:
    x: float
    y: float


@dataclass
class Port:
    n: int


class Colour(enum.Enum):
    RED = "red"
    BLUE = "blue"


class Urgent(enum.Enum):
    LOW = 1
    HIGH = 2


def run(build, line):
    reg, tree = build_tree(build)
    # The resolver the app builds, so a dynamic parameter validates here
    # exactly as it does on the real path (nothing bakes choices now).
    _, segments = split_chain(tree, line.split(), _app._choices_resolver(reg))
    return run_chain(reg, segments)


# --- union scalar coercion (specificity order) -------------------------------


def test_union_scalar_coercion():
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def go(x: str | int = "d"):
            seen["x"] = x

    run(tasks, "go --x=5")
    # Read into locals per run: mypy's narrowing of `seen["x"]` would
    # otherwise survive the second run() and clash with the new value.
    as_int = seen["x"]
    assert as_int == 5 and type(as_int) is int
    run(tasks, "go --x=hi")
    assert seen["x"] == "hi"


def test_union_specificity_int_before_float():
    seen = {}

    def tasks(reg):
        @reg.task
        def go(x: int | float = 0):
            seen["x"] = x

    run(tasks, "go --x=3")
    assert type(seen["x"]) is int and seen["x"] == 3
    run(tasks, "go --x=3.5")
    assert type(seen["x"]) is float and seen["x"] == 3.5


def test_union_validation_error_lists_both():
    def tasks(reg):
        @reg.task
        def bench(n: int | float = 0): ...

    with pytest.raises(ChainError) as exc:
        run(tasks, "bench --n=abc")
    assert "expects an integer or a number" in str(exc.value)


# --- list[union] / Many ------------------------------------------------------


def test_list_union_option_repeatable():
    seen = {}

    def tasks(reg):
        @reg.task
        def go(vals: list[str | int] | None = None):
            seen["vals"] = vals

    run(tasks, "go --vals=a --vals=3 --vals=b")
    assert seen["vals"] == ["a", 3, "b"]


def test_many_positional_variadic():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(targets: Many[str | int]):
            seen["t"] = targets

    run(tasks, "build a 3 b")
    assert seen["t"] == ["a", 3, "b"]


def test_many_positional_requires_at_least_one():
    def tasks(reg):
        @reg.task
        def build(targets: Many[str]): ...

    with pytest.raises(ChainError, match="missing required positional"):
        run(tasks, "build")


def test_many_single_token_is_still_a_list():
    # D14/F04: Many[T] is exactly list[T] — always a list. A single token does
    # NOT collapse to a scalar (the old doc claim was wrong).
    seen = {}

    def tasks(reg):
        @reg.task
        def build(targets: Many[str]):
            seen["t"] = targets

    run(tasks, "build web")
    assert seen["t"] == ["web"]


# --- list | scalar unions collapse to a plain list ---------------------------


def test_list_or_scalar_union_is_always_a_list():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(x: list[str] | str):
            seen["x"] = x

    run(tasks, "build only")
    assert seen["x"] == ["only"]  # always a list (no scalar-collapse)
    run(tasks, "build a b")
    assert seen["x"] == ["a", "b"]


# --- comma-splitting: on by default for collections, `nosplit` opts out ------


def test_list_splits_on_comma_by_default():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(tags: list[str] | None = None):
            seen["tags"] = tags

    run(tasks, "build --tags=a,b,c")
    assert seen["tags"] == ["a", "b", "c"]  # no marker needed


def test_list_also_accepts_repeat_and_mixes():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(tags: list[str] | None = None):
            seen["tags"] = tags

    run(tasks, "build --tags=a,b --tags=c")
    assert seen["tags"] == ["a", "b", "c"]


def test_split_coerces_and_validates_each_part():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(nums: list[int] | None = None):
            seen["nums"] = nums

    run(tasks, "build --nums=1,2,3")
    assert seen["nums"] == [1, 2, 3]
    with pytest.raises(ChainError, match="expects an integer"):
        run(tasks, "build --nums=1,x,3")


def test_split_skips_empty_parts():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(tags: list[str] | None = None):
            seen["tags"] = tags

    run(tasks, "build --tags=a,,b,")
    assert seen["tags"] == ["a", "b"]


def test_nosplit_keeps_comma_literal():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(names: Annotated[list[str], nosplit] | None = None):
            seen["names"] = names

    run(tasks, "build --names=a,b --names=c")
    assert seen["names"] == ["a,b", "c"]  # nosplit: only the repeated flag adds items


# --- forward marker ----------------------------------------------------------


def test_forward_marker_is_peeled():
    # Both spellings mark the parameter for forwarding; peel surfaces it.
    assert peel(Annotated[bool, forward]).forward is True
    assert peel(Forward[bool]).forward is True
    assert peel(bool).forward is False  # unmarked


def test_forward_alias_expands_to_annotated():
    # `Forward[T]` is exactly `Annotated[T, forward]`, like `Many[T]` is a list.
    # (Widened: mypy types the two typing-form expressions differently and
    # would call this runtime equality non-overlapping.)
    expanded: object = Annotated[bool, forward]
    assert Forward[bool] == expanded
    # A marker rides alongside the type without disturbing the peel of that type.
    peeled = peel(Forward[list[str]])
    assert peeled.multiple is True and peeled.forward is True


def test_bare_marker_aliases_peel_like_their_markers():
    # Terse aliases for the bare markers: generic `NoSplit[T]`, and the
    # Path-fixed `Exists`/`IsFile`/`IsDir` (no subscript needed).
    assert peel(NoSplit[list[str]]).nosplit is True
    assert peel(Exists).path_req == "exists"
    assert peel(IsFile).path_req == "file"
    assert peel(IsDir).path_req == "dir"


# --- dict[K, V] mappings -----------------------------------------------------


def test_dict_str_str():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(env: dict[str, str] | None = None):
            seen["env"] = env

    run(tasks, "build --env=A=1 --env=B=2")
    assert seen["env"] == {"A": "1", "B": "2"}


def test_dict_typed_value_union_splits_by_default():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(opt: dict[str, int | str] | None = None):
            seen["opt"] = opt

    run(tasks, "build --opt=x=1,bla=haha")
    assert seen["opt"] == {"x": 1, "bla": "haha"}  # 1 -> int, haha -> str


def test_dict_value_type_validated():
    def tasks(reg):
        @reg.task
        def build(nums: dict[str, int] | None = None): ...

    with pytest.raises(ChainError, match="value expects an integer"):
        run(tasks, "build --nums=a=x")


def test_dict_missing_equals_is_taught():
    def tasks(reg):
        @reg.task
        def build(env: dict[str, str] | None = None): ...

    with pytest.raises(ChainError, match="expects KEY=VALUE"):
        run(tasks, "build --env=justkey")


def test_dict_value_may_contain_equals():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(env: dict[str, str] | None = None):
            seen["env"] = env

    run(tasks, "build --env=URL=a=b")
    assert seen["env"] == {"URL": "a=b"}  # split on first '=' only


def test_dict_scalar_value_last_wins():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(env: dict[str, str] | None = None):
            seen["env"] = env

    run(tasks, "build --env=X=1 --env=X=2")
    assert seen["env"] == {"X": "2"}


def test_dict_of_list_appends_on_repeated_key():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(label: dict[str, list[int]] | None = None):
            seen["label"] = label

    run(tasks, "build --label=ports=8080 --label=ports=8443 --label=mem=512")
    assert seen["label"] == {"ports": [8080, 8443], "mem": [512]}


def test_dict_manifest_spec():
    def tasks(reg):
        @reg.task
        def build(nums: dict[str, int] | None = None): ...

    _, tree = build_tree(tasks)
    spec = tree["tasks"]["build"]["params"][0]
    assert spec["mapping"] is True
    assert "nosplit" not in spec  # collections split by default
    assert spec["value_types"] == ["int"]


def test_nosplit_manifest_spec():
    def tasks(reg):
        @reg.task
        def build(env: Annotated[dict[str, str], nosplit] | None = None): ...

    _, tree = build_tree(tasks)
    spec = tree["tasks"]["build"]["params"][0]
    assert spec["mapping"] is True
    assert spec["nosplit"] is True


# --- custom / extended scalar types (coerced via their constructor) ----------


def test_uuid_via_constructor():
    seen = {}
    value = "12345678-1234-5678-1234-567812345678"

    def tasks(reg):
        @reg.task
        def build(id: uuid.UUID | None = None):
            seen["id"] = id

    run(tasks, f"build --id={value}")
    assert seen["id"] == uuid.UUID(value)


def test_datetime_via_fromisoformat():
    seen = {}

    def tasks(reg):
        @reg.task
        def at(when: datetime.datetime | None = None):
            seen["when"] = when

    run(tasks, "at --when=2020-01-02T03:04:05")
    assert seen["when"] == datetime.datetime(2020, 1, 2, 3, 4, 5)


def test_custom_type_via_constructor():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(v: Version | None = None):
            seen["v"] = v

    run(tasks, "build --v=1.2.3")
    assert seen["v"] == Version("1.2.3")


def test_invalid_custom_value_fails_cleanly():
    def tasks(reg):
        @reg.task
        def build(id: uuid.UUID | None = None): ...

    results = run(tasks, "build --id=not-a-uuid")
    assert results[0].ok is False
    assert isinstance(results[0].error, ValueError)
    assert results[0].code == EX_USAGE  # a binding-time refusal, not a task failure


# --- dynamic completion (suggest) --------------------------------------------


def test_dynamic_choices_baked_but_completion_defers():
    from livery.footman._complete import _DYNAMIC

    def tasks(reg):
        @reg.task
        def build(project: Annotated[str, suggest(lambda: ["alpha", "beta"])]): ...

    _, tree = build_tree(tasks)
    spec = tree["tasks"]["build"]["params"][0]
    # Nothing bakes: an absent `choices` says "nobody has asked yet", which
    # is what lets validation and help resolve live and keeps an unrelated
    # invocation from running this completer at all.
    assert "choices" not in spec
    assert spec["dynamic"] == {"strict": True}
    # Completion no longer serves the baked snapshot: it defers to a fresh
    # recompute (a subprocess, exercised end to end in test_complete),
    # returning a sentinel carrying the partial, the emission prefix ("" for
    # a positional; `--opt=` for an attached value), the param name, and the
    # task path.
    assert complete(tree, ["build", ""]) == [_DYNAMIC, "", "", "project", "build"]
    assert complete(tree, ["build", "al"]) == [
        _DYNAMIC,
        "al",
        "",
        "project",
        "build",
    ]


def test_dynamic_strict_validation_rejects_unknown():
    def tasks(reg):
        @reg.task
        def build(project: Annotated[str, suggest(lambda: ["alpha"])]): ...

    with pytest.raises(ChainError, match="must be one of alpha"):
        run(tasks, "build nope")


def test_a_completer_runs_only_where_its_values_are_wanted(tmp_path, monkeypatch):
    # The whole point of not baking: an invocation that never needs a
    # parameter's values never runs its completer. Before, every `fm
    # anything` ran every completer in the tree.
    from livery.footman.testing import Runner

    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "cache"))
    calls = tmp_path / "calls"
    calls.write_text("")
    src = tmp_path / "tasks.py"
    src.write_text(
        "from __future__ import annotations\n"
        "from typing import Annotated\n"
        "from pathlib import Path\n"
        "from livery.footman import task\n"
        "from livery.footman.params import suggest\n\n"
        f"LOG = Path({str(calls)!r})\n\n\n"
        "def branches() -> list[str]:\n"
        "    LOG.write_text(LOG.read_text() + 'x')\n"
        "    return ['main', 'dev']\n\n\n"
        "@task\n"
        "def deploy(branch: Annotated[str, suggest(branches)] = 'main'):\n"
        "    'Deploy.'\n\n\n"
        "@task\n"
        "def build():\n"
        "    'Unrelated.'\n"
    )

    def ran(line: str) -> int:
        calls.write_text("")
        result = Runner().invoke(line, tasks=src)
        assert result.ok or "must be one of" in result.stderr, result.stderr
        return len(calls.read_text())

    assert ran("build") == 0  # another task's line
    assert ran("--list") == 0  # a listing shows no values
    assert ran("--help build") == 0  # a page that does not print them
    assert ran("deploy") == 0  # the option was never given
    assert ran("--help deploy") == 1  # the page prints this one
    assert ran("deploy --branch=dev") == 1  # a value to validate
    assert ran("deploy --branch=nope") == 1  # …and to refuse


def test_help_shows_dynamic_values_and_says_they_are_dynamic(tmp_path, monkeypatch):
    # Willem's ruling: show the values, and mark them dynamic the way a
    # computed default is marked — so nobody reads the list as fixed.
    from livery.footman.testing import Runner

    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "cache"))
    src = tmp_path / "tasks.py"
    src.write_text(
        "from __future__ import annotations\n"
        "from typing import Annotated\n"
        "from livery.footman import task\n"
        "from livery.footman.params import suggest\n\n\n"
        "def branches() -> list[str]:\n"
        "    return ['main', 'dev']\n\n\n"
        "@task\n"
        "def deploy(branch: Annotated[str, suggest(branches)] = 'main'):\n"
        "    'Deploy.'\n"
    )
    out = Runner().invoke("--help deploy", tasks=src).stdout
    assert "usage: fm deploy [--branch={main|dev}]" in out
    assert "one of main|dev (dynamic)" in out


def test_dynamic_soft_allows_anything():
    seen = {}

    def tasks(reg):
        @reg.task
        def build(project: Annotated[str, suggest(lambda: ["alpha"], strict=False)]):
            seen["p"] = project

    run(tasks, "build anything")
    assert seen["p"] == "anything"


def test_soft_positional_accepts_task_name_collision():
    seen = {}

    def tasks(reg):
        @reg.task
        def lint(): ...

        @reg.task
        def checkout(
            branch: Annotated[str, suggest(lambda: ["main", "dev"], strict=False)],
        ):
            seen["b"] = branch

    run(tasks, "checkout lint")
    assert seen["b"] == "lint"  # a soft completer never hard-rejects a value


# --- required options, Any, bare collections ---------------------------------


def test_required_dict_option_binds_and_is_enforced():
    seen = {}

    def tasks(reg):
        @reg.task
        def env_(vars: dict[str, int | str]):  # trailing _ escapes the env marker
            seen["vars"] = vars

    # The escape underscore is stripped: `env_` is the `env` command, not `env-`.
    run(tasks, "env --vars=port=8080 --vars=name=web")
    assert seen["vars"] == {"port": 8080, "name": "web"}

    with pytest.raises(ChainError, match=r"missing required option\(s\): --vars"):
        run(tasks, "env")


def test_trailing_underscore_param_is_stripped_to_a_clean_flag():
    # `sync_` (the keyword/name-escape idiom) renders as `--sync`/`--no-sync`,
    # not `--sync-`, and binds back — the same stripping toolroom's handles
    # apply on their side of the seam.
    seen = {}

    def tasks(reg):
        @reg.task
        def go(sync_: bool = False):
            seen["sync"] = sync_

    run(tasks, "go --sync")
    assert seen["sync"] is True
    run(tasks, "go --no-sync")
    assert seen["sync"] is False


def test_required_bool_must_be_stated():
    seen = {}

    def tasks(reg):
        @reg.task
        def deploy(prod: bool):
            seen["prod"] = prod

    run(tasks, "deploy --prod")
    assert seen["prod"] is True
    run(tasks, "deploy --no-prod")
    assert seen["prod"] is False

    with pytest.raises(ChainError, match=r"--prod \(or --no-prod\)"):
        run(tasks, "deploy")


def test_any_annotation_passes_through():
    seen = {}

    def tasks(reg):
        @reg.task
        def deploy(payload: Any = ""):
            seen["p"] = payload

    run(tasks, "deploy --payload=hello")
    assert seen["p"] == "hello"


def test_bare_list_is_a_string_list():
    seen = {}

    def tasks(reg):
        @reg.task
        # The bare, unparameterized `list` IS what this test pins.
        def release(tags: list):  # type: ignore[type-arg]
            seen["tags"] = tags

    run(tasks, "release abc")
    assert seen["tags"] == ["abc"]  # not exploded into ['a','b','c']
    run(tasks, "release a b")
    assert seen["tags"] == ["a", "b"]


def test_bare_dict_is_a_required_mapping():
    seen = {}

    def tasks(reg):
        @reg.task
        # The bare, unparameterized `dict` IS what this test pins.
        def envs(vars: dict):  # type: ignore[type-arg]
            seen["vars"] = vars

    run(tasks, "envs --vars=A=1")
    assert seen["vars"] == {"A": "1"}


def test_dynamic_did_you_mean():
    def tasks(reg):
        @reg.task
        def build(project: Annotated[str, suggest(lambda: ["myproject", "core"])]): ...

    with pytest.raises(ChainError, match="did you mean 'myproject'"):
        run(tasks, "build myprojet")


def test_a_bare_callable_is_refused():
    # A bare callable used to mean `suggest(fn)`. One spelling per concept won:
    # the guess swallowed *anything* callable, so a marker of the wrong shape
    # became a mystery completer with no error either way. Refused rather than
    # ignored, because this shape did work — silence would break it invisibly.
    def tasks(reg):
        @reg.task
        def build(project: Annotated[str, (lambda: ["x"])]): ...

    with pytest.raises(_manifest.SpecError, match=r"a bare callable is not a marker"):
        build_tree(tasks)
    # The message names the fix, not just the problem.
    with pytest.raises(_manifest.SpecError, match=r"suggest\(<lambda>\)"):
        build_tree(tasks)


def test_an_unrecognised_non_callable_marker_is_left_alone():
    # The door a plugin's own markers come through: unknown metadata that is
    # not callable rides along untouched, so `Annotated[Path, hashed]` is a
    # plugin's business and footman never has to learn the word. (The house
    # pattern for a marker is a non-callable instance, which is exactly why
    # refusing the callable shape above costs plugins nothing.)
    def tasks(reg):
        @reg.task
        def clean(src: Annotated[str, _PluginMarker] = "x"): ...

    _, tree = build_tree(tasks)
    spec = tree["tasks"]["clean"]["params"][0]
    assert spec["name"] == "src"
    assert "dynamic" not in spec and "choices" not in spec


def test_completer_deduped_per_build():
    _DEDUP_CALLS.clear()

    def tasks(reg):
        @reg.task
        def build(project: Annotated[str, suggest(_dedup_projects)]): ...

        @reg.task
        def deploy(target: Annotated[str, suggest(_dedup_projects)]): ...

    reg = Group("root")
    tasks(reg)
    _manifest.build_manifest(reg)
    assert _DEDUP_CALLS == []  # the ordinary build runs no completer at all

    _manifest.build_manifest(reg, bake_completers=True)
    assert _DEDUP_CALLS == [1]  # baking: one call despite two params sharing it


def test_broken_strict_completer_fails_the_build():
    def tasks(reg):
        @reg.task
        def build(project: Annotated[str, suggest(lambda: 1 / 0)]): ...

    # The build itself no longer runs completers, so the taught refusal
    # surfaces where the values are actually wanted: the docs bake, and the
    # command line that needs them to validate a value.
    with pytest.raises(_manifest.CompleterError, match="ZeroDivisionError"):
        reg = Group("root")
        tasks(reg)
        _manifest.build_manifest(reg, bake_completers=True)
    with pytest.raises(_manifest.CompleterError, match="ZeroDivisionError"):
        run(tasks, "build anything")


def test_broken_soft_completer_degrades_to_no_candidates():
    def tasks(reg):
        @reg.task
        def build(
            project: Annotated[str, suggest(lambda: 1 / 0, strict=False)],
        ): ...

    # A soft completer that raises degrades to no candidates — and now that
    # the ordinary build runs nothing, that shows where the values are
    # wanted: baking it yields `[]`, and a run takes any value regardless.
    reg = Group("root")
    tasks(reg)
    spec = _manifest.build_manifest(reg, bake_completers=True)["tree"]["tasks"][
        "build"
    ]["params"][0]
    assert spec["choices"] == []  # empty -> soft (validation allows anything)
    run(tasks, "build whatever")


# --- bool as a real token type (collections, dicts, unions) ------------------


def test_dict_bool_values_coerce():
    seen = {}

    def tasks(reg):
        @reg.task
        def deploy(flags: dict[str, bool] | None = None):
            seen["flags"] = flags

    run(tasks, "deploy --flags=cache=false,retry=1 --flags=verbose=off")
    assert seen["flags"] == {"cache": False, "retry": True, "verbose": False}


def test_dict_bool_value_validated_eagerly():
    def tasks(reg):
        @reg.task
        def deploy(flags: dict[str, bool] | None = None): ...

    _, tree = build_tree(tasks)
    with pytest.raises(ChainError, match="true or false"):
        split_chain(tree, ["deploy", "--flags=cache=maybe"])


def test_list_of_bool_is_a_repeatable_option_not_a_flag():
    seen = {}

    def tasks(reg):
        @reg.task
        def toggles(switches: list[bool] | None = None):
            seen["switches"] = switches

    _, tree = build_tree(tasks)
    spec = tree["tasks"]["toggles"]["params"][0]
    assert spec["kind"] == "option" and spec.get("multiple") is True
    assert spec["types"] == ["bool"]
    run(tasks, "toggles --switches=true,false --switches=yes")
    assert seen["switches"] == [True, False, True]


def test_scalar_bool_is_still_a_flag():
    def tasks(reg):
        @reg.task
        def lint(fix: bool = False): ...

    _, tree = build_tree(tasks)
    assert tree["tasks"]["lint"]["params"][0]["kind"] == "flag"


def test_bool_in_union_coerces_tokens():
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def go(x: bool | str = "d"):
            seen["x"] = x

    run(tasks, "go --x=true")
    # Locals per run, as above: narrowing would outlive the second run().
    as_bool = seen["x"]
    assert as_bool is True
    run(tasks, "go --x=nope")
    assert seen["x"] == "nope"


def test_unicode_digit_lookalikes_are_taught_errors():
    # "²".isdigit() is true but int("²") raises; the guard must reject it
    # eagerly with a teaching message, not crash at binding time.
    def tasks(reg):
        @reg.task
        def add(a: int, b: int): ...

    _, tree = build_tree(tasks)
    with pytest.raises(ChainError, match="an integer"):
        split_chain(tree, ["add", "²", "3"])


# --- Arg[T]: the optional trailing positional --------------------------------


def test_arg_fills_from_one_token(monkeypatch):
    seen = {}

    def tasks(reg):
        @reg.task
        def files(pattern: Arg[str] = "*"):
            seen["p"] = pattern

    run(tasks, "files src")
    assert seen["p"] == "src"


def test_arg_absent_runs_on_the_default():
    seen = {}

    def tasks(reg):
        @reg.task
        def files(pattern: Arg[str] = "*"):
            seen["p"] = pattern

    run(tasks, "files")
    assert seen["p"] == "*"


def test_arg_is_greedy_and_never_peeks_at_task_names():
    # `files build` gives the token to files — deterministically, even
    # though a task named build exists. The grammar never guesses.
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def files(pattern: Arg[str] = "*"):
            seen["p"] = pattern

        @reg.task
        def build():
            seen["built"] = True

    run(tasks, "files build")
    assert seen["p"] == "build"
    assert "built" not in seen


def test_arg_plus_boundary_says_absent_next_task():
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def files(pattern: Arg[str] = "*"):
            seen["p"] = pattern

        @reg.task
        def build():
            seen["built"] = True

    run(tasks, "files + build")
    assert seen["p"] == "*"  # the boundary spelled "without it"
    assert seen["built"] is True


def test_arg_coerces_and_caps_at_one():
    seen = {}

    def tasks(reg):
        @reg.task
        def scale(n: Arg[int] = 1):
            seen["n"] = n

        @reg.task
        def other():
            seen["other"] = True

    run(tasks, "scale 5 other")
    assert seen["n"] == 5  # one token consumed, coerced
    assert seen["other"] is True  # the next word started a segment


def test_arg_needs_a_default():
    def tasks(reg):
        @reg.task
        def files(pattern: Arg[str]):
            pass

    with pytest.raises(_manifest.SpecError, match="needs a default"):
        build_tree(tasks)


def test_arg_must_trail_required_positionals():
    def tasks(reg):
        @reg.task
        def files(pattern: Arg[str] = "*", where: str = ""): ...

    # `where` defaults, so it's an option — fine. The illegal shape is a
    # *positional* after the Arg:
    def bad(reg):
        @reg.task
        def files2(pattern: Arg[str] = "*", *rest: str): ...

    build_tree(tasks)  # option-after-Arg is legal
    with pytest.raises(_manifest.SpecError, match="must come last"):
        build_tree(bad)


def test_arg_completion_offers_the_boundary():
    from livery.footman._complete import complete

    def tasks(reg):
        @reg.task
        def files(pattern: Arg[str] = "*"): ...

        @reg.task
        def build(): ...

    _, tree = build_tree(tasks)
    assert "+" in complete(tree, ["files", ""])  # the boundary documents itself


def test_a_variadic_tuple_is_a_lists_grammar_with_a_tuple_out():
    """`tuple[T, ...]` takes comma or repetition exactly as `list[T]` does,
    and differs only in the container the body receives — handing back a
    list would name a type the annotation does not."""
    seen = {}

    def tasks(reg):
        @reg.task
        def build(names: tuple[str, ...] = ()):
            seen["names"] = names

    run(tasks, "build --names=a,b")
    assert seen["names"] == ("a", "b") and isinstance(seen["names"], tuple)
    run(tasks, "build --names=a --names=b")
    assert seen["names"] == ("a", "b") and isinstance(seen["names"], tuple)


def test_a_fixed_arity_shape_fills_from_the_grouped_stream():
    """One rule reads four spellings: a subscript, a NamedTuple's fields, a
    dataclass's fields, a plain class's `__init__`. Commas and repetition
    both feed one stream and the declared arity groups it."""
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def plot(at: Spot = Spot(0.0, 0.0), size: Box = Box(0, 0)):
            seen.update(at=at, size=size)

    run(tasks, "plot --at=1,2 --size=800,600")
    assert seen["at"] == Spot(1.0, 2.0)
    assert seen["size"] == Box(800, 600)


def test_a_container_of_groups_chunks_by_arity():
    """`--p=1,2 --p=3,4` and `--p=1,2,3,4` are the same stream, so they are
    the same two points."""
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def route(points: list[Spot] = ()):  # type: ignore[assignment]
            seen["points"] = points

    run(tasks, "route --points=1,2 --points=3,4")
    assert seen["points"] == [Spot(1.0, 2.0), Spot(3.0, 4.0)]
    run(tasks, "route --points=1,2,3,4")
    assert seen["points"] == [Spot(1.0, 2.0), Spot(3.0, 4.0)]


def test_a_remainder_is_taught_never_rounded():
    """Chunking is not guessing — the arity is declared, so a leftover is a
    refusal rather than a silently dropped value.

    Refused at *parse* time, like every other typed value: the arity is
    knowable from the manifest, so nothing has to run to find out.
    """

    def tasks(reg):
        @reg.task
        def route(points: list[Spot] = ()): ...  # type: ignore[assignment]

    with pytest.raises(ChainError, match=r"groups of 2 \(x,y\)"):
        run(tasks, "route --points=1,2,3")


def test_a_named_shape_names_the_slot_that_is_wrong():
    """The whole argument for preferring `NamedTuple`: a plain tuple can
    only count the position, a named one says which field it is."""

    def tasks(reg):
        @reg.task
        def show(size: Box = Box(0, 0)): ...

    with pytest.raises(ChainError, match=r"height expects an integer"):
        run(tasks, "show --size=800,tall")

    def plain(reg):
        @reg.task
        def show(size: tuple[int, int] = (0, 0)): ...

    with pytest.raises(ChainError, match=r"value 2 expects an integer"):
        run(plain, "show --size=800,tall")


def test_a_set_is_a_list_grammar_with_a_set_handed_back():
    """Every collection accumulates the same way — comma or repetition — and
    differs only in the container the body receives."""
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def tag(names: set[str] = (), ports: frozenset[int] = ()):  # type: ignore[assignment]
            seen.update(names=names, ports=ports)

    run(tasks, "tag --names=a,b,a --ports=80 --ports=8080 --ports=80")
    assert seen["names"] == {"a", "b"}
    assert type(seen["names"]) is set
    assert seen["ports"] == frozenset({80, 8080})
    assert type(seen["ports"]) is frozenset


def test_a_bare_collection_holds_strings():
    """`set` says as much about its element as `list` does — which is
    nothing, so both mean `str`."""
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def tag(names: set = ()):  # type: ignore[assignment,type-arg]
            seen["names"] = names

    run(tasks, "tag --names=x,y")
    assert seen["names"] == {"x", "y"}


def test_a_set_of_an_unhashable_element_is_taught():
    """A plain dataclass has `__hash__ = None`, so `set[Spot]` is a shape
    that cannot exist. The annotation is wrong rather than the value, so the
    refusal says so instead of surfacing a bare TypeError from binding."""

    def tasks(reg):
        @reg.task
        def tag(spots: set[Spot] = ()): ...  # type: ignore[assignment]

    results = run(tasks, "tag --spots=1,2,3,4")
    assert results[0].code == EX_USAGE
    assert "not hashable" in str(results[0].error)


def test_an_untyped_constructor_is_not_a_grouped_shape():
    """`uuid.UUID` takes seven optional arguments, so treating any
    multi-argument constructor as a group advertised a UUID parameter as
    `--u=hex,bytes,bytes_le,…` and comma-split it. A shape footman groups is
    one it can *type*."""
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def ident(u: uuid.UUID = uuid.UUID(int=0), amount: Decimal = Decimal(0)):
            seen.update(u=u, amount=amount)

    _, tree = build_tree(tasks)
    specs = {p["name"]: p for p in tree["tasks"]["ident"]["params"]}
    assert "group" not in specs["u"]
    assert not specs["u"].get("multiple")  # one value, not a comma-split stream
    assert "group" not in specs["amount"]

    run(tasks, "ident --u=12345678-1234-5678-1234-567812345678 --amount=1.50")
    assert seen["u"] == uuid.UUID("12345678-1234-5678-1234-567812345678")
    assert seen["amount"] == Decimal("1.50")


def test_an_enum_option_speaks_tokens_and_accepts_every_face():
    # The ruled seam (formerly pinned as a disagreement): choices speak
    # tokens, and the eager gate accepts the enumerated face set — token
    # and value face — identically to every other door. A raw member NAME
    # is not a face: its one spelling is the token, and the refusal lists
    # tokens, never payload values.
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def grade(level: Colour = Colour.RED):
            seen["level"] = level

    run(tasks, "grade --level=blue")  # the token (== the value, here)
    assert seen["level"] is Colour.BLUE
    with pytest.raises(ChainError, match=r"must be one of red\|blue.*'BLUE'"):
        run(tasks, "grade --level=BLUE")  # a raw NAME is not a face

    def numeric(reg):
        @reg.task
        def rate(urgency: Urgent = Urgent.LOW):
            seen["urgency"] = urgency

    run(numeric, "rate --urgency=high")  # the token — the taught spelling
    assert seen["urgency"] is Urgent.HIGH
    run(numeric, "rate --urgency=2")  # the value face — accepted, not taught
    assert seen["urgency"] is Urgent.HIGH
    with pytest.raises(ChainError, match=r"must be one of low\|high"):
        run(numeric, "rate --urgency=HIGH")  # the refusal lists tokens


def test_a_one_field_record_reads_its_fields_declared_type():
    # `T(value)` spelling, but the field declares a type and a dataclass
    # constructor validates nothing: `n: int` silently received the string
    # 'abc' — and '5' stayed a string on the happy path. The token is the
    # *field's* value first; untyped constructors (UUID, Decimal) expose no
    # fields and keep the raw token, pinned by the parity test above.
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def serve(p: Port = Port(0)):
            seen["p"] = p

    results = run(tasks, "serve --p=5")
    assert results[0].ok
    assert seen["p"] == Port(5)  # the int, not the string '5'

    seen.clear()
    results = run(tasks, "serve --p=abc")
    assert not results[0].ok
    assert "p" not in seen  # the body never ran on the bad value
    assert "'abc' is not a valid Port" in str(results[0].error)


def test_a_constructor_that_rejects_however_it_likes_is_a_bad_value():
    # `Decimal("abc")` raises `decimal.InvalidOperation` — an ArithmeticError,
    # outside the ValueError/TypeError contract — and the raw exception used
    # to escape to the user where every other custom type taught the value.
    # The token came from a command line, so however the constructor refuses
    # it, it is a bad *value* and reports as one.
    ran: dict[str, bool] = {}

    def tasks(reg):
        @reg.task
        def pay(amount: Decimal = Decimal(0)):
            ran["it"] = True

    results = run(tasks, "pay --amount=abc")
    assert not results[0].ok
    assert not ran.get("it")
    said = str(results[0].error)
    assert "'abc' is not a valid Decimal" in said
    assert "InvalidOperation" not in said


def test_a_hidden_parameter_is_out_of_the_listings_and_nothing_else():
    """`hidden` on a parameter means what `hidden=True` means on a task: out
    of what a human reads, and out of nothing else. It still binds, it still
    completes, and the manifest marks it rather than dropping it — an agent
    reading the contract is exactly who is meant to find it."""
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def deploy(target: str, legacy: Annotated[str, hidden] = ""):
            seen.update(target=target, legacy=legacy)

    _, tree = build_tree(tasks)
    task = tree["tasks"]["deploy"]
    spec = {p["name"]: p for p in task["params"]}["legacy"]
    assert spec["hidden"] is True  # marked, never missing
    assert spec["kind"] == "option"  # an ordinary option in every other way

    shown = [p["name"] for p in listed_params(task)]
    assert shown == ["target"]
    assert [p["name"] for p in listed_params(task, show_hidden=True)] == [
        "target",
        "legacy",
    ]
    assert "--legacy" not in " ".join(f for _, f in usage_parts("fm", ["deploy"], task))
    assert "--legacy" in " ".join(
        f for _, f in usage_parts("fm", ["deploy"], task, show_hidden=True)
    )

    # Hiding and completing are different questions — a long machine-facing
    # flag is the one you most want spelled for you.
    assert "--legacy" in complete(tree, ["fm", "deploy", "x", "--"])

    run(tasks, "deploy prod --legacy=old")
    assert seen == {"target": "prod", "legacy": "old"}


def test_a_hidden_parameter_stays_out_of_the_synthesised_example():
    """The example teaches the invocation, and a hidden parameter is
    deliberately not being taught."""

    def tasks(reg):
        @reg.task
        def deploy(
            target: str, token: Annotated[str, hidden] = "", verbose: bool = False
        ): ...

    _, tree = build_tree(tasks)
    parts = example_parts(["deploy"], tree["tasks"]["deploy"], "fm")
    assert "--token" not in " ".join(f for _, f in parts)
    assert "--verbose" in " ".join(f for _, f in parts)  # the shown flag still is


def test_the_synthesised_example_is_a_line_footman_accepts():
    """An example is printed to be copied, so the only test that means
    anything is running it: everything after the program name goes back
    through the splitter and the executor. A required option's value is
    always `=`-attached — the detached form is EX_USAGE with a "did you
    mean" — so the example has to attach it too."""
    seen: dict[str, object] = {}

    def tasks(reg):
        @reg.task
        def shoot(
            source: str,
            *rest: str,
            out: str,
            width: int = 72,
            force: bool = False,
        ):
            seen.update(source=source, rest=rest, out=out, width=width, force=force)

    reg, tree = build_tree(tasks)
    parts = example_parts(["shoot"], tree["tasks"]["shoot"], "fm")
    line = " ".join(text for kind, text in parts if kind != "prog")
    _, segments = split_chain(tree, line.split(), _app._choices_resolver(reg))
    assert [r.ok for r in run_chain(reg, segments)] == [True]
    # The option and its value are one token, never two.
    assert "--out=<out>" in [text for _, text in parts]
    assert seen == {
        "source": "<source>",
        "rest": ("<rest>",),
        "out": "<out>",
        "width": 72,
        "force": True,
    }
