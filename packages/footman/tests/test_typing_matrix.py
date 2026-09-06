"""The typing surface, as a matrix: every shape against every channel.

Line coverage does not protect this surface. Every bug the 2026-08-07
sweep found had full coverage of the path it took — a `NamedTuple` on
stdin ran the text fall-through, was covered, and returned a string.
Nothing said which branch it *should* have taken.

So this file asserts combinations, not lines. Each case names a shape, a
channel, an input, and the outcome — a bound value, a taught refusal, or
a warning. "Refuses loudly" is a correct answer; silently handing back a
string is not, and would fail as neither.

`test_the_matrix_is_complete` is the part that earns its keep: every
SHAPES x CHANNELS pair must have a case or the run fails naming the gap.
An unlisted combination is how `NamedTuple` on stdin stayed broken —
nobody had thought to test it, and nothing noticed nobody had.

Cells that read `Unsupported` are the pass's own to-do list
(notes/20260807-typing-pass.md): each later step flips some to values, so
a PR's diff here shows exactly which behaviours changed.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, NamedTuple, TypedDict

import pytest

from livery.footman import _manifest, context
from livery.footman._executor import run_chain
from livery.footman._split import split_chain
from livery.footman.params import stdin
from livery.footman.registry import Group

# --- the shapes, at module level where `eval_str` can see them ----------------


class Colour(enum.Enum):
    RED = "red"
    BLUE = "blue"


class Size(NamedTuple):
    width: int
    height: int


class Opts(TypedDict):
    name: str
    port: int


@dataclass
class Point:
    x: float
    y: float


# --- outcome vocabulary -------------------------------------------------------


@dataclass(frozen=True)
class Refused:
    """The channel refuses, and the message teaches. A correct answer."""

    match: str


@dataclass(frozen=True)
class Unsupported:
    """Warns at manifest build and hands the text through unconverted.

    Honest-but-poor: the reader is told. Distinct from `Refused` because
    the value still arrives, and distinct from a bound value because the
    annotation was not honoured.

    The vocabulary outlives the cells: when a gap is found, the honest move
    is to add it here as `Unsupported` and then flip it, rather than leave
    the shape untested because it does not work yet.
    """


CLI = "cli-option"
STDIN = "stdin-document"
CHANNELS = (CLI, STDIN)

SHAPES: tuple[Any, ...] = (
    int,
    float,
    str,
    Colour,
    Literal["a", "b"],
    Path,
    list[int],
    dict[str, int],
    Size,
    Opts,
    Point,
    tuple[int, int],
    tuple[str, ...],
    set[str],
)

# (shape, channel, input, outcome). The input is a command-line fragment
# for CLI and the piped bytes for STDIN.
CASES: tuple[tuple[Any, str, Any, Any], ...] = (
    # --- scalars: the same value however it arrives -------------------------
    (int, CLI, "--v=42", 42),
    (int, STDIN, b"42\n", 42),
    (float, CLI, "--v=1.5", 1.5),
    (float, STDIN, b"1.5\n", 1.5),
    (str, CLI, "--v=web", "web"),
    (str, STDIN, b"web", "web"),  # text keeps its bytes verbatim
    (Colour, CLI, "--v=red", Colour.RED),
    (Colour, STDIN, b"red\n", Colour.RED),
    (Literal["a", "b"], CLI, "--v=a", "a"),
    (Literal["a", "b"], STDIN, b"a\n", "a"),
    (Path, CLI, "--v=/tmp/x", Path("/tmp/x")),
    (Path, STDIN, b"/tmp/x\n", Path("/tmp/x")),
    # --- containers ---------------------------------------------------------
    (list[int], CLI, "--v=1,2", [1, 2]),
    (list[int], STDIN, b"[1, 2]", [1, 2]),
    (dict[str, int], CLI, "--v=a=1", {"a": 1}),
    (dict[str, int], STDIN, b'{"a": 1}', {"a": 1}),
    # --- records ------------------------------------------------------------
    (Size, STDIN, b'{"width": 800, "height": 600}', Size(800, 600)),
    (Opts, STDIN, b'{"name": "web", "port": 8080}', {"name": "web", "port": 8080}),
    (Point, STDIN, b'{"x": 1, "y": 2}', Point(1.0, 2.0)),
    # A fixed-arity shape fills from the grouped stream, whichever way it
    # was spelled. `Opts` stays refused: a TypedDict has no positional
    # arity to group by — its keys are named, so stdin is its channel.
    (Size, CLI, "--v=800,600", Size(800, 600)),
    (Point, CLI, "--v=1,2", Point(1.0, 2.0)),
    (Opts, CLI, "--v=name=web", Refused("not a valid Opts")),
    # `tuple[T, ...]` is a list's grammar with a tuple handed back.
    (tuple[str, ...], CLI, "--v=a,b", ("a", "b")),
    (tuple[str, ...], STDIN, b'["a", "b"]', ("a", "b")),
    (tuple[int, int], CLI, "--v=1,2", (1, 2)),
    # A JSON array is the grouped stream in another dress, so a fixed-arity
    # shape reads the same either way.
    (tuple[int, int], STDIN, b"[1, 2]", (1, 2)),
    # A set is a list's grammar with a different container handed back — and
    # the reason to name one is de-duplication, so a repeat is asserted here.
    (set[str], CLI, "--v=a,b,a", {"a", "b"}),
    (set[str], STDIN, b'["a", "b", "a"]', {"a", "b"}),
)


def _ids() -> list[str]:
    return [f"{getattr(s, '__name__', s)}-{c}" for s, c, _, _ in CASES]


def _run(shape: Any, channel: str, given: Any) -> tuple[Any, list[Any]]:
    """Bind `v` of *shape* through *channel*; the seen value and the rows."""
    seen: dict[str, Any] = {}
    reg = Group("root")

    def task(v=None):
        seen["v"] = v

    # The annotation is set as an *object*, not written as source: under
    # `from __future__ import annotations` a written one becomes the string
    # "shape", and `eval_str` cannot see a local. Setting it directly is
    # also the only way to drive a table of shapes at all.
    ann = Annotated[shape, stdin] if channel is STDIN else shape
    task.__annotations__ = {"v": ann}
    task.__name__ = "probe"
    task.__doc__ = "Bind one value."
    reg.task(task)
    tree = _manifest.build_manifest(reg)["tree"]
    line = "probe" if channel is STDIN else f"probe {given}"
    _, segments = split_chain(tree, line.split())
    results = run_chain(reg, segments)
    return seen.get("v", _UNSET), results


_UNSET = object()


@pytest.fixture(autouse=True)
def _no_ambient_pipe(monkeypatch):
    """Tests never read the harness's own stream."""
    monkeypatch.setattr(context, "_stdin_payload", None)


@pytest.mark.parametrize(("shape", "channel", "given", "expected"), CASES, ids=_ids())
def test_the_typing_matrix(shape, channel, given, expected, monkeypatch, recwarn):
    if channel is STDIN:
        monkeypatch.setattr(context, "_stdin_payload", given)

    if isinstance(expected, Refused):
        _, results = _run(shape, channel, given)
        assert results[0].code != 0, "expected a refusal, got a clean run"
        assert expected.match in str(results[0].error)
        return

    value, results = _run(shape, channel, given)

    if isinstance(expected, Unsupported):
        # Honest-but-poor: the reader is warned and the text passes through.
        # The warning is the contract — a silent string would be the bug.
        assert isinstance(value, str) or results[0].code != 0, (
            f"{shape} on {channel} produced {value!r}: neither a warned "
            f"pass-through nor a refusal. If this now works, the cell is "
            f"stale — replace Unsupported() with the value."
        )
        return

    assert results[0].code == 0, f"unexpected failure: {results[0].error}"
    assert value == expected, f"{shape} on {channel}: {value!r} != {expected!r}"
    assert type(value) is type(expected), (
        f"{shape} on {channel}: bound a {type(value).__name__} where the "
        f"annotation says {type(expected).__name__} — the failure class the "
        f"2026-08-07 sweep found three times, always silently."
    )


def test_the_matrix_is_complete():
    """Every shape x channel has a case.

    The guard that earns this file's keep: `NamedTuple` on stdin was broken
    because nobody had written the case, and nothing noticed nobody had.
    """
    covered = {(s, c) for s, c, _, _ in CASES}
    missing = [
        f"{getattr(s, '__name__', s)} x {c}"
        for s in SHAPES
        for c in CHANNELS
        if (s, c) not in covered
    ]
    assert not missing, "typing-matrix cells with no case:\n  " + "\n  ".join(missing)
