"""The options a positional's value declares: `options_of(param, fn)`."""

from __future__ import annotations

import inspect
import json
import subprocess
from typing import Annotated, Any, Literal

import pytest

from livery.footman import _app, _complete, _manifest, fail
from livery.footman._executor import run_chain
from livery.footman._manifest import SpecError
from livery.footman._params import Arg, doc, options_of
from livery.footman._registry import Group
from livery.footman._split import ChainError, split_chain

#: What each kind of site declares: a blog needs a title, a shop takes a
#: currency and a flag, and a page declares nothing.
KINDS: dict[str, list[inspect.Parameter]] = {
    "blog": [
        inspect.Parameter(
            "title",
            inspect.Parameter.KEYWORD_ONLY,
            annotation=Annotated[str, doc("the blog's title")],
        ),
        inspect.Parameter(
            "posts_per_page",
            inspect.Parameter.KEYWORD_ONLY,
            default=10,
            annotation=int,
        ),
    ],
    "shop": [
        inspect.Parameter(
            "currency",
            inspect.Parameter.KEYWORD_ONLY,
            default="EUR",
            annotation=Annotated[Literal["EUR", "USD"], doc("the prices' currency")],
        ),
        inspect.Parameter(
            "tax", inspect.Parameter.KEYWORD_ONLY, default=False, annotation=bool
        ),
        inspect.Parameter(
            "sizes", inspect.Parameter.KEYWORD_ONLY, default=(), annotation=list[int]
        ),
    ],
    "page": [],
}

ASKED: list[str] = []


def kind_options(kind: str) -> list[inspect.Parameter]:
    ASKED.append(kind)
    if kind not in KINDS:
        fail(f"no kind is named {kind}; the kinds are {', '.join(KINDS)}")
    return KINDS[kind]


def _tasks(seen: dict[str, Any]) -> Any:
    def tasks(reg: Group) -> None:
        @reg.task
        def create(
            name: str,
            kind: Arg[str] = "",
            *,
            force: bool = False,
            answers: Annotated[
                dict[str, object] | None,
                options_of("kind", kind_options),
                doc("the kind's own options"),
            ] = None,
        ) -> None:
            """Create a site of a kind."""
            seen.update(name=name, kind=kind, force=force, answers=answers)

    return tasks


def _split(line: str) -> tuple[Group, list[Any]]:
    reg = Group("root")
    _tasks({})(reg)
    tree = _manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(
        tree, line.split(), _app._choices_resolver(reg), _app._options_resolver(reg)
    )
    return reg, segments


def _run(line: str) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    reg = Group("root")
    _tasks(seen)(reg)
    tree = _manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(
        tree, line.split(), _app._choices_resolver(reg), _app._options_resolver(reg)
    )
    assert all(result.ok for result in run_chain(reg, segments))
    return seen


def _refusal(line: str) -> str:
    with pytest.raises(ChainError) as refused:
        _split(line)
    return str(refused.value)


# Refusals first: the value's options are checked before anything runs.


def test_an_option_the_value_does_not_declare_is_an_unknown_option() -> None:
    assert _refusal("create web blog --title=Notes --currency=USD") == (
        "create: unknown option --currency"
        " (<kind> blog takes --title, --posts-per-page)"
    )


def test_a_value_that_declares_nothing_says_so() -> None:
    assert _refusal("create web page --title=Notes") == (
        "create: unknown option --title (<kind> page takes no options)"
    )


def test_an_option_before_its_value_is_named_says_to_name_it() -> None:
    assert _refusal("create web --title=Notes") == (
        "create: unknown option --title"
        " (name <kind> first: its value declares the options it takes)"
    )


def test_a_near_miss_names_the_option_it_resembles() -> None:
    assert _refusal("create web blog --titel=Notes") == (
        "create: unknown option --titel — did you mean '--title'?"
    )


def test_a_missing_required_option_is_named() -> None:
    assert _refusal("create web blog") == (
        "create: missing required option(s) of <kind> blog: --title"
    )


def test_a_value_outside_the_choices_names_them() -> None:
    assert _refusal("create web shop --currency=GBP") == (
        "create: --currency must be one of EUR|USD (got 'GBP')"
    )


def test_a_value_of_the_wrong_type_is_refused() -> None:
    assert _refusal("create web blog --title=Notes --posts-per-page=many") == (
        "create: --posts-per-page expects an integer (got 'many')"
    )


def test_a_value_the_function_refuses_is_the_refusal() -> None:
    assert _refusal("create web wiki") == (
        "create: no kind is named wiki; the kinds are blog, shop, page"
    )


def test_an_option_the_task_declares_itself_cannot_be_a_value_s() -> None:
    clashing = [inspect.Parameter("force", inspect.Parameter.KEYWORD_ONLY, default=1)]
    KINDS["clash"] = clashing
    try:
        assert _refusal("create web clash") == (
            "create: <kind> clash declares the option --force, which create"
            " declares itself; one of them renames it"
        )
    finally:
        del KINDS["clash"]


def test_a_returned_object_that_is_no_option_is_refused() -> None:
    KINDS["odd"] = [inspect.Parameter("title", inspect.Parameter.POSITIONAL_ONLY)]
    KINDS["mapped"] = [
        inspect.Parameter(
            "labels",
            inspect.Parameter.KEYWORD_ONLY,
            default=None,
            annotation=dict[str, str] | None,
        )
    ]
    try:
        with pytest.raises(SpecError, match="each option is a keyword-only"):
            _split("create web odd")
        with pytest.raises(SpecError, match="which is no option"):
            _split("create web mapped")
    finally:
        del KINDS["odd"]
        del KINDS["mapped"]


def test_the_marker_needs_a_dict_with_a_default_after_a_positional() -> None:
    def no_dict(reg: Group) -> None:
        @reg.task
        def create(
            kind: str,
            answers: Annotated[str, options_of("kind", kind_options)] = "",
        ) -> None: ...

    def no_default(reg: Group) -> None:
        @reg.task
        def create(
            kind: str,
            *,
            answers: Annotated[dict[str, object], options_of("kind", kind_options)],
        ) -> None: ...

    def no_positional(reg: Group) -> None:
        @reg.task
        def create(
            kind: str = "",
            answers: Annotated[
                dict[str, object] | None, options_of("kind", kind_options)
            ] = None,
        ) -> None: ...

    said = {
        "no_dict": "options_of() marks a dict parameter",
        "no_default": "options_of() needs a default",
        "no_positional": "options_of('kind', …) names no positional",
    }
    for build in (no_dict, no_default, no_positional):
        reg = Group("root")
        build(reg)
        with pytest.raises(SpecError) as refused:
            _manifest.build_manifest(reg)
        assert said[build.__name__] in str(refused.value)


# What the body receives.


def test_the_body_receives_the_given_options_coerced_by_name() -> None:
    seen = _run("create --force web blog --title=Notes --posts-per-page=5")
    assert seen == {
        "name": "web",
        "kind": "blog",
        "force": True,
        "answers": {"title": "Notes", "posts_per_page": 5},
    }


def test_options_may_come_before_the_positional_that_declares_them() -> None:
    seen = _run("create --currency=USD --tax web shop")
    assert seen["answers"] == {"currency": "USD", "tax": True}


def test_a_list_option_coerces_each_of_its_values() -> None:
    assert _run("create web shop --sizes=1,2 --sizes=3")["answers"] == {
        "sizes": [1, 2, 3]
    }


def test_an_option_the_line_does_not_give_is_absent() -> None:
    assert _run("create web shop")["answers"] is None
    assert _run("create web shop --no-tax")["answers"] == {"tax": False}
    # A bare mention stands for the declared default.
    assert _run("create web shop --currency")["answers"] == {"currency": "EUR"}


def test_the_function_runs_once_per_line_and_only_for_a_given_value() -> None:
    ASKED.clear()
    _run("create web shop --currency=USD")
    assert ASKED == ["shop"]
    ASKED.clear()
    _run("create web")
    assert ASKED == []


def test_the_manifest_names_the_positional_and_carries_no_options() -> None:
    reg = Group("root")
    _tasks({})(reg)
    tree = _manifest.build_manifest(reg)["tree"]
    (spec,) = [p for p in tree["tasks"]["create"]["params"] if p["name"] == "answers"]
    assert spec == {
        "name": "answers",
        "kind": "chosen",
        "of": "kind",
        "doc": "the kind's own options",
    }


def test_help_names_where_the_options_come_from(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.footman.testing import Runner

    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "cache"))
    src = tmp_path / "tasks.py"
    src.write_text(_TASKS_FILE)
    out = Runner().invoke("--help create", tasks=src).stdout
    assert "[<kind>'s options ...]" in out
    assert "<kind>'s options" in out
    assert "the options the value of <kind> declares" in out


# Completion: the options and their choices, asked fresh in a subprocess.

_TASKS_FILE = (
    "import inspect\n"
    "from typing import Annotated, Literal\n"
    "from livery.footman import Arg, doc, options_of, task\n\n"
    "def kind_options(kind):\n"
    "    if kind != 'shop':\n"
    "        return []\n"
    "    return [inspect.Parameter('currency', inspect.Parameter.KEYWORD_ONLY,\n"
    "        default='EUR',\n"
    "        annotation=Annotated[Literal['EUR', 'USD'], doc('the currency')])]\n\n"
    "@task\n"
    "def create(name: str, kind: Arg[str] = '', answers: Annotated[\n"
    "    dict[str, object] | None, options_of('kind', kind_options)] = None):\n"
    "    'Create a site of a kind.'\n"
)


def _completion_tree() -> dict[str, Any]:
    reg = Group("root")
    _tasks({})(reg)
    tree: dict[str, Any] = _manifest.build_manifest(reg)["tree"]
    return tree


def test_completion_asks_for_the_value_s_options_once_it_is_given() -> None:
    tree = _completion_tree()
    # Before the value, nothing is asked.
    assert not any(
        c.startswith(_complete._CHOSEN)
        for c in _complete.complete(tree, ["create", "web", "--"])
    )
    out = _complete.complete(tree, ["create", "web", "shop", "--tax", "--"])
    sentinel, _, context = out[0].partition("\t")
    assert sentinel == _complete._CHOSEN
    assert json.loads(context) == {
        "partial": "--",
        "bash": False,
        "param": "answers",
        "value": "shop",
        "path": ["create"],
        "used": ["--tax"],
    }
    # The task's own options ride beside it.
    assert "--force" in out[1:]
    value = _complete.complete(tree, ["create", "web", "shop", "--currency=U"])
    assert value[0].startswith(_complete._CHOSEN + "\t")
    assert json.loads(value[0].split("\t", 1)[1])["partial"] == "--currency=U"


def test_completion_turns_the_options_into_candidates() -> None:
    specs = _manifest.chosen_options(options_of("kind", kind_options), "shop")
    context = {"partial": "--", "bash": False, "used": ["--tax"]}
    assert _complete._chosen_rows(specs, context) == [
        "--currency\tdefault: EUR",
        "--currency=\tthe prices' currency",
        "--sizes\tdefault: []",
        "--sizes=",
    ]
    choices = {"partial": "--currency=U", "bash": False, "used": []}
    assert _complete._chosen_rows(specs, choices) == ["--currency=USD"]
    bash = {"partial": "--currency=", "bash": True, "used": []}
    assert _complete._chosen_rows(specs, bash) == ["EUR", "USD"]


def test_a_failed_ask_serves_the_manifest_s_own_candidates(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The ask for a value's options runs a subprocess; when it fails, the
    # menu is what the manifest answers alone, never a hung keystroke.
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps({"schema": _complete._SCHEMA, "tree": _completion_tree()}),
        encoding="utf-8",
    )
    asked: list[dict[str, Any]] = []

    def failed(context: dict[str, Any], args: list[str]) -> None:
        asked.append(context)

    monkeypatch.setattr(_complete, "_fresh_chosen", failed)
    line = ["--manifest", str(manifest), "--", "create", "web", "shop", "--"]
    assert _complete.complete_cli(line) == 0
    assert capsys.readouterr().out.split() == ["--force"]
    assert [context["value"] for context in asked] == ["shop"]
    # Answered, its options join the task's own.
    specs = _manifest.chosen_options(options_of("kind", kind_options), "shop")
    monkeypatch.setattr(_complete, "_fresh_chosen", lambda context, args: specs)
    assert _complete.complete_cli([*line[:-1], "--c"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "--currency\tdefault: EUR",
        "--currency=\tthe prices' currency",
    ]


def test_a_failed_child_is_no_options(monkeypatch: pytest.MonkeyPatch) -> None:
    context = {"param": "answers", "value": "shop", "path": ["create"]}

    def timeout(*_args: Any, **_kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(cmd="x", timeout=2)

    monkeypatch.setattr(subprocess, "run", timeout)
    assert _complete._fresh_chosen(context, ["create", ""]) is None

    def answered(stdout: str) -> Any:
        return lambda *_args, **_kwargs: subprocess.CompletedProcess("x", 0, stdout)

    monkeypatch.setattr(subprocess, "run", answered("not json\n"))
    assert _complete._fresh_chosen(context, ["create", ""]) is None
    monkeypatch.setattr(
        subprocess, "run", lambda *_a, **_k: subprocess.CompletedProcess("x", 1, "")
    )
    assert _complete._fresh_chosen(context, ["create", ""]) is None
    monkeypatch.setattr(subprocess, "run", answered('{"name": "tax"}\n[1]\n'))
    assert _complete._fresh_chosen(context, ["-f=x.py", "create", ""]) == [
        {"name": "tax"}
    ]


def test_the_options_child_protocol_holds_from_both_sides(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.footman import _suggest

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    (proj / "tasks.py").write_text(_TASKS_FILE)
    monkeypatch.chdir(proj)
    captured: dict[str, list[str]] = {}

    class _Done:
        returncode = 0
        stdout = ""

    def capture_run(cmd: list[str], **_: Any) -> _Done:
        captured["argv"] = list(cmd)
        return _Done()

    context = {"param": "answers", "value": "shop", "path": ["create"]}
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "run", capture_run)
        _complete._fresh_chosen(context, ["create", "web", "shop", "--"])
    argv = captured["argv"]
    assert argv[1:4] == ["-P", "-m", "livery.footman._suggest"]
    assert _suggest.main(argv[4:]) == 0
    (line,) = capsys.readouterr().out.splitlines()
    spec = json.loads(line)
    assert (spec["name"], spec["key"], spec["choices"]) == (
        "currency",
        "currency",
        ["EUR", "USD"],
    )
    # A miss is no candidate, never an error.
    assert _suggest._chosen("answers", "page", ["create"], {}) == []
    assert _suggest._chosen("nope", "shop", ["create"], {}) == []
