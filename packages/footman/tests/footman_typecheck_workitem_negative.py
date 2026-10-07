# pyright: reportUnnecessaryTypeIgnoreComment=true
"""Misuse the work-item model makes STRUCTURAL — the negative half.

Checked by basedpyright and mypy only (ty and pyrefly do not honour
per-line ignores). Every line is a deliberate type error carrying
`# type: ignore[code]`: if the surface loosens and the error disappears,
the ignore goes stale and both checkers turn the line red. The ignore is
the assertion. Runtime-taught refusals (a step yielding a value, a
sealed record's writes, `confirm=` on a step maker) are pinned by the
executed suites instead.
"""

from footman_typecheck_workitem import lint
from livery.footman import Result, ResultView, parallel, step


def _declared_means_recorded() -> None:
    # walk 4 / I13: declared ⟹ recorded — the keyword does not exist at
    # task grain, so hiding a declared row is unspellable.
    lint.opts(recorded=False)  # type: ignore[call-arg]  # a task is part of the story


def _the_forged_receipt_is_unspellable() -> None:
    # I3: no record without work — a plain call minting a titled verdict
    # matches no form of step().
    step("done", code=0)  # type: ignore[call-overload]  # a record needs work


def _the_verdict_follows_the_code() -> None:
    # I2: ok derives from code — code=1 with ok=True cannot be written,
    # and review never edits what was captured.
    def hook(view: ResultView) -> None:
        view.ok = True  # type: ignore[misc]  # write the code; ok follows
        view.stdout = ""  # type: ignore[misc]  # review sees what was captured

    del hook


def _the_sealed_record_is_sealed_statically_too() -> None:
    # I5 by type, at both grains: a committed record's fields are read-only
    # properties, so an observer's write dies at check time, before runtime
    # would have taught it.
    def observer(result: Result) -> None:
        result.command = "rewritten"  # type: ignore[misc]  # sealed

    del observer


def _bare_callables_are_refused() -> None:
    # decision 8's ban, static before it is runtime: a lambda has no
    # chosen grain, so the payload union refuses it.
    parallel(lambda: 0)  # type: ignore[call-overload]  # lift it: step(fn)
