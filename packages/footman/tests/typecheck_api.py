"""The public API, type-checked as a consumer writes it — by all four checkers.

Never executed: pytest skips non-`test_` files, and nothing imports this
module. basedpyright and mypy check it with the rest of the tree; ty and
pyrefly include it by name in pyproject — the one tests/ file in their
scope, because it is clean-room consumer code with no test doubles. Every
shape below must type-check as written; the deliberate *mis*-uses live in
`typecheck_api_negative.py`, whose per-line ignores ty and pyrefly do not
honour and which therefore stays out of their scope.

`assert_type` pins the contract where a value's static type IS the API
(a task call's return, `parallel`'s exit codes, `run`'s Result); plain
usage pins the rest (a marker accepted inside `Annotated`, a gate that
keeps the signature it wraps).
"""

from pathlib import Path
from typing import Annotated, assert_type

import livery.footman as footman
from livery.footman import (
    App,
    Arg,
    Brand,
    Context,
    Failed,
    Forward,
    Group,
    IsFile,
    Many,
    NoSplit,
    Result,
    ResultView,
    RunFailed,
    Secret,
    Stdin,
    Stdout,
    ask,
    between,
    check,
    confirm,
    doc,
    env,
    exists,
    fail,
    fetch,
    group,
    inherited,
    nosplit,
    parallel,
    prompt,
    requires_env,
    requires_tool,
    run,
    select,
    suggest,
    task,
    track,
)
from livery.footman.registry import TaskFn, Tasks, TaskView
from livery.footman.testing import InvokeResult, Runner, TaskResult, recording


def _tasks_keep_their_signatures() -> None:
    @task
    def build(target: str, release: bool = False) -> int:
        return 0

    # The decorated task calls with its own parameters and return type…
    assert_type(build("web", release=True), int)
    # …and so does an opted reference, chaining included.
    assert_type(build.opts(atomic=True)("web"), int)
    assert_type(build.opts(atomic=True).opts(keep_going=True)("web"), int)
    # The reference is a TaskFn — the type a consumer can name.
    fn: TaskFn[..., int] = build.opts(serial=True)
    assert_type(fn.__name__, str)

    @task(name="ship", pre=[build], keep_going=True, atomic=True)
    def deploy() -> None: ...

    assert_type(deploy(), None)


def _gates_are_identity_in_types() -> None:
    # A gate hands back exactly what it wrapped, whichever side of @task.
    @task
    @requires_tool("docker")
    def up() -> str:
        return "ok"

    assert_type(up(), str)

    @requires_env("DEPLOY_TOKEN", reason="needs credentials")
    @task
    def release(version: str) -> int:
        return 0

    assert_type(release("1.0"), int)


def _groups_and_defaults() -> None:
    docs: Group = group("docs", help="Documentation")

    @docs.task
    def build(strict: bool = False) -> int:
        return 0

    @docs.default
    def all_docs(strict: bool = False) -> None: ...

    assert_type(build(strict=True), int)
    # The default's own handle keeps its signature — a caller who wants
    # parameter hints calls it directly rather than through the group.
    assert_type(all_docs(strict=True), None)
    # A group's opted default is a task reference with an untracked signature.
    ref = docs.opts(keep_going=True)
    assert_type(ref.__name__, str)


def _markers_vanish_at_the_type_level() -> None:
    @task
    def lint(
        paths: Many[str],
        pattern: Arg[str] = "*",
        fix: Forward[bool] = False,
        names: Annotated[list[str], nosplit] = [],
        also: NoSplit[list[str]] = [],
        jobs: Annotated[int, between(1, 32)] = 4,
        target: Annotated[str, env("DEPLOY_ENV")] = "staging",
        version: Annotated[str, check(lambda v: None)] = "",
        config: Annotated[Path, exists] = Path("x"),
        out: IsFile = Path("y"),
        verbose: Annotated[bool, doc("say more")] = False,
        project: Annotated[str, suggest(lambda: ["a", "b"], strict=False)] = "",
        token: Annotated[Secret, ask(secret=True)] = Secret(""),
    ) -> None:
        # Inside the body every parameter is its plain type — markers are
        # Annotated metadata and never change what the body holds.
        assert_type(paths, list[str])
        assert_type(pattern, str)
        assert_type(fix, bool)
        assert_type(jobs, int)
        assert_type(config, Path)
        assert_type(token, Secret)
        assert_type(token.reveal(), str)

    lint(["src"], "*.py", fix=True)


def _stdin_stdout_declare_the_process_boundary() -> None:
    @task
    def review(diff: Stdin[str] = "") -> Stdout[dict[str, int]]:
        assert_type(diff, str)
        return {"lines": len(diff)}

    # A body call is unaffected by the boundary declaration.
    assert_type(review("patch"), dict[str, int])


def _run_returns_a_result() -> None:
    @task
    def build() -> None:
        result = run("cc -c main.c")
        assert_type(result, Result)
        assert_type(result.code, int)
        assert_type(result.ok, bool)
        assert_type(result.stdout, str)
        assert_type(result.stderr, str)
        assert_type(result.output, str)
        captured = run(["git", "status"], capture=True, cwd="src", nofail=True)
        assert_type(captured, Result)
        try:
            run("false", timeout=5.0)
        except RunFailed as exc:
            assert_type(exc.result, Result)


def _parallel_is_typed_both_ways() -> None:
    @task
    def lint() -> int:
        return 0

    @task
    def test() -> None: ...

    @task
    def all_checks() -> None:
        records = parallel(lint, test, keep_going=True)
        assert_type(records, list[Result])  # each of which IS its exit code
        with parallel() as p:
            lint()
        results: list[object] = p.results  # heterogeneous by nature
        # The block is its list of records — codes included, by I2.
        assert_type(p[0], Result)
        _ = results


def _context_helpers_state_their_types() -> None:
    @task(interactive=True)
    def wizard() -> None:
        for item in track([1, 2, 3]):
            assert_type(item, int)
        name = prompt("name? ", default="x")
        assert_type(name, str)
        yes = confirm("sure?", default=True)
        assert_type(yes, bool)
        kind = select("kind?", ["library", "app"])
        assert_type(kind, str)
        chosen = select("path?", [("label", Path("a")), ("other", Path("b"))])
        picked_many = select("which?", ["a", "b"], multiple=True)
        assert_type(picked_many, list[str])
        again = inherited()
        again()
        _ = chosen


def _failing_speaks_noreturn() -> None:
    @task
    def deploy(force: bool = False) -> int:
        if not force:
            fail("refusing", code=3)
        return 0  # reachable only past the NoReturn — the checker knows

    try:
        deploy()
    except Failed as exc:
        assert_type(exc.code, int)


def _composition_returns_groups() -> None:
    from livery.footman import include, plugin

    grafted = include("shared_tasks", only=["lint"])
    assert_type(grafted, Group)
    mounted = plugin("footman.docs", into="docs")
    assert_type(mounted, Group)


def _testing_surface() -> None:
    runner = Runner(App(name="acme", prog="acme"))
    result = runner.invoke("build --release", cwd=Path("."))
    assert_type(result, InvokeResult)
    assert_type(result.exit_code, int)
    assert_type(result.ok, bool)
    assert_type(result.stdout, str)
    rows: list[TaskResult] = result.results
    _ = rows
    with recording(code=0) as calls:
        assert_type(calls, list[Result])


def _lifecycle_hooks_keep_their_types() -> None:
    @footman.pre_tasks
    def gate(inv: footman.Invocation) -> None:
        tasks: Tasks | None = inv.tasks
        if tasks is not None:
            view: TaskView | None = tasks.get("deploy")
            if view is not None:
                view.set_opts(keep_going=True)

    # Registration is identity: the hook keeps its own signature.
    assert_type(gate(footman.Invocation()), None)


def _branding_and_fetch() -> None:
    app = App(name="acme", prog="acme", version="1.0")
    assert_type(app.brand, Brand)
    assert_type(app.run(["--version"]), int)
    assert_type(fetch("https://example.com/tool.tgz", sha256="x"), Path)


def _context_is_reachable() -> None:
    @task
    def where(ctx: Context) -> None:
        assert_type(ctx.dry_run, bool)


def _task_handles_expose_their_lifecycle() -> None:
    @task
    def build(target: str = "web") -> int:
        return 0

    @build.pre_task
    def warm() -> None: ...

    @build.pre_record
    def review(view: ResultView) -> None:
        view.title = view.title.strip()
        view.code = 0
        view.set_returned("summarised")

    @build.post_task
    def watch(result: object) -> None: ...

    # Attachment is identity-shaped: the hooks stay plain callables.
    warm()
    assert_type(build("web"), int)  # the handle stays callable as itself
