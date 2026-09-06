"""The stdin boundary: parameters bound from the piped stream.

The annotation decides the interpretation (`str` text, `bytes` raw,
`stdin("field")` one JSON value, `stdin(lines=True)` one token per line),
precedence is CLI > stdin > env > default > prompt, and the read happens
once at the boundary — never in a task body, so the parallel-stdin guard
is never in play.
"""

from __future__ import annotations

import enum
import json
from pathlib import Path
from typing import Annotated

import pytest

from livery.footman import _manifest, context
from livery.footman._describe import param_detail
from livery.footman._executor import EX_USAGE, resolve_asks, run_chain
from livery.footman._split import ChainError, split_chain
from livery.footman.params import Stdin, ask, between, check, env, isfile, stdin
from livery.footman.registry import Group
from livery.footman.testing import Runner


def build_tree(build):
    reg = Group("root")
    build(reg)
    return reg, _manifest.build_manifest(reg)["tree"]


def run(build, line):
    reg, tree = build_tree(build)
    _, segments = split_chain(tree, line.split())
    return run_chain(reg, segments)


class Shade(enum.Enum):
    RED = "red"
    BLUE = "blue"


@pytest.fixture
def piped(monkeypatch):
    """Inject the boundary payload; `piped(None)` is a terminal."""

    def _set(data: bytes | str | None):
        payload = data.encode() if isinstance(data, str) else data
        monkeypatch.setattr(context, "_stdin_payload", payload)

    _set(None)  # tests never read the harness's real stream
    return _set


# --- text ---------------------------------------------------------------------


def test_text_fills_from_the_pipe(piped):
    seen = {}

    def tasks(reg):
        @reg.task
        def wc(text: Annotated[str, stdin] = ""):
            seen["text"] = text

    piped("hello boundary")
    run(tasks, "wc")
    assert seen["text"] == "hello boundary"


def test_terminal_means_not_provided(piped):
    seen = {}

    def tasks(reg):
        @reg.task
        def wc(text: Annotated[str, stdin] = "fallback"):
            seen["text"] = text

    run(tasks, "wc")
    assert seen["text"] == "fallback"


def test_cli_beats_stdin(piped):
    seen = {}

    def tasks(reg):
        @reg.task
        def wc(text: Annotated[str, stdin] = ""):
            seen["text"] = text

    piped("from the pipe")
    run(tasks, "wc --text=explicit")
    assert seen["text"] == "explicit"


def test_stdin_beats_env(piped, monkeypatch):
    seen = {}

    def tasks(reg):
        @reg.task
        def wc(text: Annotated[str, stdin, env("WC_TEXT")] = ""):
            seen["text"] = text

    monkeypatch.setenv("WC_TEXT", "ambient")
    piped("piped")
    run(tasks, "wc")
    assert seen["text"] == "piped"
    piped(None)
    run(tasks, "wc")
    assert seen["text"] == "ambient"  # no pipe: env still fills


def test_text_normalises_newlines(piped):
    seen = {}

    def tasks(reg):
        @reg.task
        def wc(text: Stdin[str] = ""):
            seen["text"] = text

    piped(b"a\r\nb\rc\n")
    run(tasks, "wc")
    assert seen["text"] == "a\nb\nc\n"


def test_a_coerced_scalar_arrives_typed(piped):
    """`Stdin[int]` is an int, not the text that spelled it.

    The fall-through used to validate without coercing, so a scalar
    reached the body as a string while the annotation — and every type
    checker — said otherwise.
    """
    seen = {}

    def tasks(reg):
        @reg.task
        def port(n: Stdin[int] = 0):
            seen["n"] = n

    piped(b"42")
    run(tasks, "port")
    assert seen["n"] == 42 and isinstance(seen["n"], int)


def test_a_coerced_scalar_ignores_the_shell_s_newline(piped):
    """`echo 42 |` is the ordinary way to pipe a value, and its newline is
    the shell's punctuation rather than part of the value. Text keeps its
    newline (see `test_text_normalises_newlines`); a coerced scalar does
    not, which is what made every validated scalar fail on `echo`."""
    seen = {}

    def tasks(reg):
        @reg.task
        def pick(colour: Stdin[Shade] = Shade.RED):
            seen["colour"] = colour

    piped(b"blue\n")
    run(tasks, "pick")
    assert seen["colour"] is Shade.BLUE


def test_empty_pipe_is_a_value_for_text(piped):
    seen = {}

    def tasks(reg):
        @reg.task
        def wc(text: Annotated[str, stdin] = "fallback"):
            seen["text"] = text

    piped(b"")
    run(tasks, "wc")
    assert seen["text"] == ""


def test_required_text_without_a_pipe_refuses(piped):
    def tasks(reg):
        @reg.task
        def wc(text: Annotated[str, stdin]): ...

    results = run(tasks, "wc")
    assert results[0].ok is False and results[0].code == EX_USAGE
    assert "reads stdin" in str(results[0].error)


def test_not_utf8_text_is_a_taught_refusal(piped):
    def tasks(reg):
        @reg.task
        def wc(text: Annotated[str, stdin] = ""): ...

    piped(b"\xff\xfe")
    results = run(tasks, "wc")
    assert results[0].code == EX_USAGE
    assert "UTF-8" in str(results[0].error)


def no_tabs(value: str) -> None:
    """Module-level so `eval_str` can resolve it from the annotation."""
    if "\t" in value:
        raise ValueError("no tabs allowed")


def test_check_validators_run_on_stdin_text(piped):
    def tasks(reg):
        @reg.task
        def wc(text: Annotated[str, stdin, check(no_tabs)] = ""): ...

    piped("a\tb")
    results = run(tasks, "wc")
    assert results[0].code == EX_USAGE and "no tabs" in str(results[0].error)


# --- markers on a piped document ----------------------------------------------
# The binder gives a document its shape; the markers still decide whether it
# may pass. A container arriving by pipe used to skip bounds, path
# requirements and choices entirely, so the two channels disagreed about the
# same value.


def test_bounds_run_on_a_piped_list(piped):
    seen = {}

    def tasks(reg):
        @reg.task
        def scale(sizes: Annotated[list[int], stdin, between(1, 5)] = []):
            seen["sizes"] = sizes

    _, tree = build_tree(tasks)
    with pytest.raises(ChainError, match="between 1 and 5"):
        split_chain(tree, ["scale", "--sizes=9"])  # the CLI channel refuses it

    piped("[1, 9]")
    results = run(tasks, "scale")
    assert results[0].code == EX_USAGE
    assert "between 1 and 5" in str(results[0].error)
    assert seen == {}  # the body never ran

    piped("[1, 5]")
    run(tasks, "scale")
    assert seen["sizes"] == [1, 5]  # an in-range document still binds


def test_bounds_run_on_a_piped_mapping(piped):
    def tasks(reg):
        @reg.task
        def scale(sizes: Annotated[dict[str, int], stdin, between(1, 5)] = {}): ...

    piped('{"a": 1, "b": 9}')
    results = run(tasks, "scale")
    assert results[0].code == EX_USAGE
    assert "between 1 and 5" in str(results[0].error)


def test_a_path_requirement_runs_on_a_piped_list(piped, tmp_path):
    seen = {}

    def tasks(reg):
        @reg.task
        def scan(paths: Annotated[list[Path], stdin, isfile] = []):
            seen["paths"] = paths

    piped('["/nope/one", "/nope/two"]')
    results = run(tasks, "scan")
    assert results[0].code == EX_USAGE
    # The refusal names the offending element, spelled the way the platform
    # spells it — `Path("/nope/one")` is `\nope\one` on Windows, so comparing
    # against the literal would test the separator, not the refusal.
    assert str(Path("/nope/one")) in str(results[0].error)

    real = tmp_path / "one.txt"
    real.write_text("x")
    piped(json.dumps([str(real)]))
    run(tasks, "scan")
    assert seen["paths"] == [real]


# --- bytes --------------------------------------------------------------------


def test_bytes_reads_raw(piped):
    seen = {}

    def tasks(reg):
        @reg.task
        def digest(data: Annotated[bytes, stdin] = b""):
            seen["data"] = data

    piped(b"\xff\x00raw")
    run(tasks, "digest")
    assert seen["data"] == b"\xff\x00raw"


def test_bytes_cli_token_encodes(piped):
    seen = {}

    def tasks(reg):
        @reg.task
        def digest(data: Annotated[bytes, stdin] = b""):
            seen["data"] = data

    run(tasks, "digest --data=abc")
    assert seen["data"] == b"abc"


# --- stdin("field") -----------------------------------------------------------


def test_field_binds_one_json_value(piped):
    seen = {}

    def tasks(reg):
        @reg.task
        def submit(prompt: Annotated[str, stdin("prompt")] = ""):
            seen["prompt"] = prompt

    piped('{"prompt": "hello", "noise": 1}')
    run(tasks, "submit")
    assert seen["prompt"] == "hello"


def test_field_coerces_like_a_token(piped):
    seen = {}

    def tasks(reg):
        @reg.task
        def jobs(count: Annotated[int, stdin("count")] = 0):
            seen["count"] = count

    piped('{"count": 5}')
    run(tasks, "jobs")
    assert seen["count"] == 5


def test_field_fills_a_flag(piped):
    seen = {}

    def tasks(reg):
        @reg.task
        def gate(active: Annotated[bool, stdin("stop_hook_active")] = False):
            seen["active"] = active

    piped('{"stop_hook_active": true}')
    run(tasks, "gate")
    assert seen["active"] is True


def test_missing_field_falls_to_the_default(piped):
    seen = {}

    def tasks(reg):
        @reg.task
        def submit(prompt: Annotated[str, stdin("prompt")] = "fallback"):
            seen["prompt"] = prompt

    piped('{"other": 1}')
    run(tasks, "submit")
    assert seen["prompt"] == "fallback"


def test_missing_field_on_a_required_param_refuses(piped):
    def tasks(reg):
        @reg.task
        def submit(prompt: Annotated[str, stdin("prompt")]): ...

    piped('{"other": 1}')
    results = run(tasks, "submit")
    assert results[0].code == EX_USAGE
    assert "'prompt'" in str(results[0].error)


def test_empty_pipe_refuses_a_json_field(piped):
    def tasks(reg):
        @reg.task
        def submit(prompt: Annotated[str, stdin("prompt")] = ""): ...

    piped(b"")
    results = run(tasks, "submit")
    assert results[0].code == EX_USAGE and "empty" in str(results[0].error)


def test_malformed_json_is_a_taught_refusal(piped):
    def tasks(reg):
        @reg.task
        def submit(prompt: Annotated[str, stdin("prompt")] = ""): ...

    piped("not json")
    results = run(tasks, "submit")
    assert results[0].code == EX_USAGE and "not JSON" in str(results[0].error)


def test_a_json_array_refuses_the_field_form(piped):
    def tasks(reg):
        @reg.task
        def submit(prompt: Annotated[str, stdin("prompt")] = ""): ...

    piped("[1, 2]")
    results = run(tasks, "submit")
    assert results[0].code == EX_USAGE and "object" in str(results[0].error)


def test_a_structured_field_value_refuses(piped):
    def tasks(reg):
        @reg.task
        def submit(prompt: Annotated[str, stdin("prompt")] = ""): ...

    piped('{"prompt": {"nested": true}}')
    results = run(tasks, "submit")
    assert results[0].code == EX_USAGE and "single value" in str(results[0].error)


# --- stdin(lines=True) --------------------------------------------------------


def test_lines_bind_a_list(piped):
    seen = {}

    def tasks(reg):
        @reg.task
        def each(paths: Annotated[list[Path], stdin(lines=True)] = ()):  # type: ignore[assignment]
            seen["paths"] = paths

    piped("a.txt\nb/c.txt\n")
    run(tasks, "each")
    assert seen["paths"] == [Path("a.txt"), Path("b/c.txt")]


def test_lines_coerce_each_line_as_a_token(piped):
    def tasks(reg):
        @reg.task
        def total(numbers: Annotated[list[int], stdin(lines=True)] = ()): ...  # type: ignore[assignment]

    piped("1\ntwo\n")
    results = run(tasks, "total")
    assert results[0].code == EX_USAGE and "integer" in str(results[0].error)


def test_empty_pipe_means_no_lines(piped):
    seen = {}

    def tasks(reg):
        @reg.task
        def each(names: Annotated[list[str], stdin(lines=True)] = ("d",)):  # type: ignore[assignment]
            seen["names"] = names

    piped(b"")
    run(tasks, "each")
    assert seen["names"] == []


# --- the shared read ----------------------------------------------------------


def test_a_chain_shares_the_one_read(piped):
    seen = {}

    def tasks(reg):
        @reg.task
        def first(text: Annotated[str, stdin] = ""):
            seen["first"] = text

        @reg.task
        def second(text: Annotated[str, stdin] = ""):
            seen["second"] = text

    piped("shared")
    run(tasks, "first second")
    assert seen == {"first": "shared", "second": "shared"}


# --- ask() interplay ----------------------------------------------------------


def test_ask_front_loader_skips_a_filled_question(piped):
    def tasks(reg):
        @reg.task
        def release(version: Annotated[str, stdin("version"), ask()]): ...

    reg, tree = build_tree(tasks)
    _, segments = split_chain(tree, ["release"])
    piped('{"version": "1.2.3"}')
    resolve_asks(reg.tasks["release"], segments[0], None)
    assert "version" not in segments[0].values  # stdin fills it at bind


# --- declaration errors -------------------------------------------------------


def test_a_bare_list_reads_a_json_array():
    # The structured rule: a bare-marker list binds a JSON array (the lines
    # idiom is the explicit opt-in). The binding itself is test_binder's.
    def tasks(reg):
        @reg.task
        def each(names: Annotated[list[str], stdin] = ()): ...  # type: ignore[assignment]

    _, tree = build_tree(tasks)
    assert tree["tasks"]["each"]["params"][0]["stdin"] == "json"


def test_lines_on_a_scalar_is_a_spec_error():
    def tasks(reg):
        @reg.task
        def wc(text: Annotated[str, stdin(lines=True)] = ""): ...

    with pytest.raises(_manifest.SpecError, match="list parameter"):
        build_tree(tasks)


def test_field_on_a_list_is_a_spec_error():
    def tasks(reg):
        @reg.task
        def each(names: Annotated[list[str], stdin("names")] = ()): ...  # type: ignore[assignment]

    with pytest.raises(_manifest.SpecError, match="single"):
        build_tree(tasks)


def test_field_or_lines_on_a_dict_is_a_spec_error():
    # A dict reads stdin whole (a JSON object) — the refinements don't apply.
    def tasks(reg):
        @reg.task
        def conf(pairs: Annotated[dict[str, str], stdin("k")] = {}): ...

    with pytest.raises(_manifest.SpecError, match="whole"):
        build_tree(tasks)


def test_field_and_lines_are_exclusive():
    with pytest.raises(ValueError, match="exclusive"):
        stdin("field", lines=True)


# --- the manifest and help ----------------------------------------------------


def test_manifest_marks_the_binding_modes():
    def tasks(reg):
        @reg.task
        def go(
            text: Annotated[str, stdin] = "",
            data: Annotated[bytes, stdin] = b"",
            field: Annotated[str, stdin("prompt")] = "",
            rows: Annotated[list[str], stdin(lines=True)] = (),  # type: ignore[assignment]
        ): ...

    _, tree = build_tree(tasks)
    specs = {p["name"]: p for p in tree["tasks"]["go"]["params"]}
    assert specs["text"]["stdin"] == "text"
    assert specs["data"]["stdin"] == "bytes"
    assert specs["field"]["stdin"] == "field:prompt"
    assert specs["rows"]["stdin"] == "lines"


def test_a_defaultless_stdin_param_is_not_a_required_positional():
    def tasks(reg):
        @reg.task
        def hook(event: Annotated[str, stdin]): ...

    _, tree = build_tree(tasks)
    spec = tree["tasks"]["hook"]["params"][0]
    assert spec["kind"] == "option"
    assert not spec.get("required")


def test_help_says_it_reads_stdin():
    def tasks(reg):
        @reg.task
        def submit(prompt: Annotated[str, stdin("prompt")] = ""): ...

    _, tree = build_tree(tasks)
    spec = tree["tasks"]["submit"]["params"][0]
    assert "reads stdin (JSON field 'prompt')" in param_detail(spec)


# --- the Runner seam ----------------------------------------------------------


def test_runner_injects_a_pipe():
    reg = Group("root")

    @reg.task
    def shout(text: Annotated[str, stdin] = ""):
        print(text.upper())

    result = Runner().invoke("shout", tasks=reg, stdin="quiet words")
    assert result.ok and "QUIET WORDS" in result.stdout


def test_runner_default_is_a_terminal():
    reg = Group("root")

    @reg.task
    def shout(text: Annotated[str, stdin] = "nothing piped"):
        print(text)

    result = Runner().invoke("shout", tasks=reg)
    assert result.ok and "nothing piped" in result.stdout
