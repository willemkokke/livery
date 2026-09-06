"""Canned answers for bridge calls — toolroom's own test double.

The handles under test stay real. `answers()` swaps nothing above the
seam, so chaining, flag translation, the `off` sentinel, secrets
redaction, colour switches and `input=` consumption are all the
bridge's own, exercised exactly as a live call would exercise them.
Only execution is replaced: every call leaves through `_host.run` and
every version read through `_host.probe`, and for the span of the block
those two doors answer from a table instead of spawning.

    from livery.toolroom.testing import answers

    with answers({
        ("uv", "tool", "list"): "hse-devkit v0.0.18\\n- hse\\n",
        ("git", "push"): 1,
    }) as calls:
        release()                       # code under test, real handles

    assert calls[0].argv[:3] == ["uv", "tool", "list"]

Keys are argv prefixes, matched against the *name-led* spelling of each
call (`git …`, `python …` — never the resolved interpreter path); the
longest matching prefix wins, and a string key is split on whitespace
as a convenience. Values are the answer: a `str` is stdout with exit 0,
an `int` is an exit code, a `Result` sets the code and both streams. An
unmatched call succeeds silently — exit 0, empty streams — so
`answers()` with no table is a pure call recorder. A non-zero answer
takes the failing lane honestly: returned under `nofail`, raised
otherwise.

This is a different instrument from footman's `recording()`: a
recording is a rehearsal inside the real world (nothing executes,
everything succeeds, value reads opted `recorded=False` still answer
truthfully), where `answers()` *replaces* the world — version probes
included, which is why an unmatched probe refuses loudly instead of
answering empty. Nested inside a `recording()` block, `answers()` wins:
it intercepts upstream of footman, so its answers stand and the
recording sees nothing.
"""

from __future__ import annotations

import contextlib
import warnings
from collections.abc import Generator, Mapping
from pathlib import Path
from typing import Any

from livery import toolroom as _toolroom
from livery.toolroom import _host
from livery.toolroom._host import Argv, Result, ToolError

__all__ = ["Call", "UnservedAnswers", "answers"]


class UnservedAnswers(Warning):
    """A canned answer matched no call by the end of an `answers()` block.

    An unmatched *call* is often incidental — the recorder default
    exists for exactly that — but an unmatched *table entry* is a test
    bug in the making: an answer was canned to steer the code under
    test and never served, usually because the key was mis-spelt,
    path-led where matching is name-led, or split into the wrong
    tokens, and the test passes vacuously as if the entry did not
    exist. A warning rather than an error so a deliberately shared
    table (one fixture serving several tests, each exercising a subset)
    can filter it; escalate it to a failure with `-W error` or a
    `filterwarnings` mark where the table is per-test. A block that
    exits on an exception is not warned — it already failed loudly.
    """


# What a table maps a prefix to: stdout, an exit code, or a full Result.
Answer = str | int | Result


class Call:
    """One intercepted call: what would have run, and what was handed.

    `argv` is the name-led spelling — raw tokens, assert-friendly.
    `raw` is what would actually have spawned: resolved interpreter
    path, forced colour switch and all (the same pairing footman's
    steps keep as `.command`/`.raw`). `command` is the shown line,
    values quoted and secrets redacted.

    `args` and `kwargs` are the call *as the caller wrote it*,
    pre-render: positionals verbatim (a `Path` stays a `Path`), and the
    call keywords with **False values included** — the bridge omits a
    False flag from the argv, the record must not, because "never
    handed" and "handed, as False" are different facts and tests assert
    on both. The rule of thumb: assert on `kwargs` for what the code
    decided to pass, on `argv` for what would have run. `.flags()`
    keywords are part of the handle, rendered into the verb path before
    any call exists, so they show only in `argv`/`raw`; run policy
    stays in `opts`.

    `env` is the environment the call handed the seam (`None`
    inherits); `opts` carries the rest of the run policy (`nofail`,
    `capture`, `input`, `cwd`, `timeout`, …) exactly as it crossed.
    `probe` marks a version read — a probe has no call of its own, so
    its `args`/`kwargs` are `()` and `{}` by construction.
    """

    __slots__ = ("args", "argv", "command", "env", "kwargs", "opts", "probe", "raw")

    argv: Argv
    raw: Argv
    command: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    env: dict[str, str] | None
    opts: dict[str, Any]
    probe: bool

    def __init__(
        self,
        *,
        argv: Argv,
        raw: Argv,
        command: str,
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
        env: dict[str, str] | None,
        opts: dict[str, Any],
        probe: bool = False,
    ) -> None:
        self.argv = argv
        self.raw = raw
        self.command = command
        self.args = args
        self.kwargs = {} if kwargs is None else kwargs
        self.env = env
        self.opts = opts
        self.probe = probe

    def __repr__(self) -> str:
        kind = "probe" if self.probe else "call"
        return f"Call({self.command!r}, {kind})"


def _normalise(table: Mapping[Any, Answer]) -> dict[tuple[str, ...], Answer]:
    canned: dict[tuple[str, ...], Answer] = {}
    for key, answer in table.items():
        if isinstance(key, str):
            prefix: tuple[Any, ...] = tuple(key.split())
        else:
            try:
                prefix = tuple(key)
            except TypeError:
                prefix = ()
        if not prefix or not all(isinstance(token, str) for token in prefix):
            raise TypeError(
                f"answers(): a key must be a non-empty argv prefix — a tuple "
                f"of tokens or one string to split — not {key!r}"
            )
        canned[prefix] = answer
    return canned


def _match(
    canned: dict[tuple[str, ...], Answer], named: tuple[str, ...]
) -> tuple[str, ...] | None:
    """The longest canned prefix matching *named*, or None."""
    best: tuple[str, ...] | None = None
    for prefix in canned:
        if named[: len(prefix)] == prefix and (best is None or len(prefix) > len(best)):
            best = prefix
    return best


def _resolve(answer: Answer, where: str) -> tuple[int, str, str]:
    """An answer as (code, stdout, stderr), whichever spelling it came in."""
    if isinstance(answer, str):
        return 0, answer, ""
    if hasattr(answer, "stdout"):  # toolroom's Result or footman's — twins
        result: Any = answer
        return int(result), result.stdout, result.stderr
    if isinstance(answer, int):
        return int(answer), "", ""
    raise TypeError(
        f"answers(): the answer for `{where}` must be a str (stdout), an "
        f"int (exit code), or a Result — not {type(answer).__name__}"
    )


@contextlib.contextmanager
def answers(
    table: Mapping[Any, Answer] | None = None,
    *,
    hosted: bool = False,
) -> Generator[list[Call]]:
    """Answer every bridge call in the block from *table*, spawning nothing.

    Yields the live list of `Call` records — assert on it after (or
    during) the block. With `hosted=True` the block simulates the
    hosted lane: `_host.hosted` answers True (so the bridge takes its
    hosted paths — an in-process tool's callable is recorded, never
    invoked), successes come back as footman's `Result`, and failures
    raise footman's `RunFailed` — what code written for a footman task
    returns and catches. The default speaks toolroom's own vocabulary:
    `Result` back, `ToolError` raised.

    The per-process `installed_version` cache is cleared for the block
    and restored after it, so a canned version can neither be pre-empted
    by a real read earlier in the process nor leak into later tests.
    Not thread-safe: the seam is swapped module-wide for the duration.

    A canned prefix that matched nothing by a *clean* block exit warns
    (`UnservedAnswers`): an answer canned but never served is almost
    always a mis-keyed prefix, and without the warning the test passes
    vacuously. A block that exits on an exception is not warned — it
    already failed loudly.
    """
    canned = _normalise(table or {})
    fm_result: Any = None
    fm_failed: Any = None
    if hosted:
        try:
            from livery.footman.context import Result as fm_result
            from livery.footman.context import RunFailed as fm_failed
        except ImportError:
            raise ImportError(
                "answers(hosted=True) simulates the hosted lane, which is "
                "footman vocabulary (footman.Result back, RunFailed raised) — "
                "install footman, or drop hosted= to test the standalone lane."
            ) from None
    calls: list[Call] = []
    served: set[tuple[str, ...]] = set()

    def fake_run(
        target: list[str] | Any,
        *,
        parts: tuple[tuple[str, str], ...],
        exact: tuple[str, ...],
        handed: tuple[tuple[Any, ...], dict[str, Any]] | None = None,
        nofail: bool = False,
        capture: bool = True,
        input: str | None = None,
        env: dict[str, str] | None = None,
        title: str | None = None,
        pre_record: Any = None,
        recorded: bool = True,
        timeout: float | None = None,
        cwd: str | Path | None = None,
        rel: str | Path | None = None,
        color: str = "auto",
    ) -> Any:
        named = (parts[0][1], *exact[1:])
        command = " ".join(token for _role, token in parts)
        call_args, call_kwargs = handed if handed is not None else ((), {})
        calls.append(
            Call(
                argv=Argv(named),
                raw=Argv(exact),
                command=command,
                args=call_args,
                kwargs=call_kwargs,
                env=env,
                opts={
                    "nofail": nofail,
                    "capture": capture,
                    "input": input,
                    "title": title,
                    "pre_record": pre_record,
                    "recorded": recorded,
                    "timeout": timeout,
                    "cwd": cwd,
                    "rel": rel,
                    "color": color,
                },
            )
        )
        matched = _match(canned, named)
        if matched is None:
            code, stdout, stderr = 0, "", ""
        else:
            served.add(matched)
            code, stdout, stderr = _resolve(canned[matched], command)
        if hosted:
            result: Any = fm_result(
                code, command=command, stdout=stdout, stderr=stderr, tokens=exact
            )
            if code != 0 and not nofail:
                raise fm_failed(result)
            return result
        result = Result(code, stdout=stdout, stderr=stderr, tokens=exact)
        if code != 0 and not nofail:
            raise ToolError(result)
        return result

    def fake_probe(
        argv: list[str],
        *,
        shown: tuple[str, ...],
        env: dict[str, str] | None,
        timeout: float | None,
    ) -> Result:
        command = " ".join(shown)
        calls.append(
            Call(
                argv=Argv(shown),
                raw=Argv(argv),
                command=command,
                env=env,
                opts={"timeout": timeout},
                probe=True,
            )
        )
        matched = _match(canned, shown)
        if matched is None:
            # An empty answer would surface as installed_version's own
            # "could not read a version" ValueError — which reads as a bug
            # in the code under test. Name the missing entry instead: a
            # version read is never incidental, code branches on it.
            raise LookupError(
                f"answers(): no canned answer for the version read "
                f"`{command}` — installed_version() under answers() needs "
                f"one, e.g. {{{tuple(shown)!r}: '1.2.3'}}"
            )
        served.add(matched)
        code, stdout, stderr = _resolve(canned[matched], command)
        return Result(code, stdout=stdout, stderr=stderr, tokens=tuple(argv))

    saved_run, saved_probe, saved_hosted = _host.run, _host.probe, _host.hosted
    snapshot = dict(_toolroom._version_cache)
    _toolroom._version_cache.clear()
    # The host seam is swapped by design; the checker reads a def as
    # non-reassignable.
    _host.run = fake_run  # ty: ignore[invalid-assignment]
    _host.probe = fake_probe  # ty: ignore[invalid-assignment]
    if hosted:

        def _always_hosted() -> bool:
            return True

        _host.hosted = _always_hosted  # ty: ignore[invalid-assignment]
    try:
        yield calls
    finally:
        _host.run, _host.probe, _host.hosted = saved_run, saved_probe, saved_hosted
        _toolroom._version_cache.clear()
        _toolroom._version_cache.update(snapshot)
    # Reached only on a clean exit: an exception leaving the block is
    # already a loud failure, and a warning behind it would only mask it.
    unserved = [prefix for prefix in canned if prefix not in served]
    if unserved:
        rendered = "; ".join(" ".join(prefix) for prefix in unserved)
        warnings.warn(
            f"answers(): these canned answers matched no call: {rendered} — "
            f"an unserved entry is almost always a mis-keyed prefix "
            f"(matching is name-led, never a resolved path, and by argv "
            f"prefix), and the test passes vacuously without it",
            UnservedAnswers,
            stacklevel=3,
        )
