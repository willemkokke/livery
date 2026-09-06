# pyright: reportUnnecessaryTypeIgnoreComment=true
"""Misuse of the public API that MUST be a static error — the negative half
of `typecheck_api.py`.

Never executed; checked by basedpyright and mypy (ty and pyrefly do not
honour per-line ignores, so this file stays out of their scope). Every
line below is a deliberate type error carrying `# type: ignore[code]`:
if a signature loosens and the error disappears, the ignore goes stale
and BOTH checkers turn the line red — mypy through `warn_unused_ignores`,
basedpyright through the line-1 pragma. The ignore is the assertion.
"""

from livery.footman import Runner, parallel, run, select, task, track
from livery.footman.registry import requires_tool


def _policy_options_are_a_closed_typed_set() -> None:
    @task
    def build(target: str, release: bool = False) -> int:
        return 0

    build.opts(bogus=True)  # type: ignore[call-arg]  # not a policy option
    build.opts(confirm=3)  # type: ignore[arg-type]  # confirm is a str
    build.opts(atomic=True).opts(nope=1)  # type: ignore[call-arg]  # chained too


def _task_calls_check_like_the_plain_function() -> None:
    @task
    def build(target: str, release: bool = False) -> int:
        return 0

    build()  # type: ignore[call-arg]  # target is required
    build(7)  # type: ignore[arg-type]  # target is a str
    build("web", releese=True)  # type: ignore[call-arg]  # misspelt keyword


def _context_surfaces_reject_wrong_shapes() -> None:
    @task
    def wrong() -> None:
        run(42)  # type: ignore[arg-type]  # a command is a str or argv list
        parallel(7)  # type: ignore[call-overload]  # calls are callables
        select("pick", [1, 2])  # type: ignore[list-item]  # strings or pairs
        for _ in track(5):  # type: ignore[arg-type]  # not an iterable
            pass


def _decorators_and_testing_reject_wrong_shapes() -> None:
    requires_tool(123)  # type: ignore[arg-type]  # tools are named by string

    Runner().invoke(42)  # type: ignore[arg-type]  # a line or argv list
