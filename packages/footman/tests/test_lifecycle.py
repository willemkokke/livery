"""The `pre_tasks` lifecycle hook: the invocation, before anything runs."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Annotated, Any

import pytest

from livery.footman import _discover, _executor, _manifest, compose, registry
from livery.footman.params import between, check, env
from livery.footman.registry import Group, RegistrationError
from livery.footman.testing import Runner


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body))
    return path


def test_a_hook_edits_the_merged_tree(tmp_path):
    src = _write(
        tmp_path / "tasks.py",
        """
        import livery.footman as footman
        from livery.footman import task

        @task
        def audit(): ...

        @task
        def deploy_web(): ...

        @footman.pre_tasks
        def gate(inv):
            audit = inv.tasks["audit"]
            for t in inv.tasks:
                if t.name.startswith("deploy"):
                    t.add_pre(audit)
        """,
    )
    view = registry.Tasks(_discover.load_tree([src]))
    assert view["audit"].fn in view["deploy-web"].pre


def test_a_hook_reaches_a_subfolder_task(tmp_path):
    # A ROOT hook edits a task defined in a subfolder's file — the whole
    # merged tree, not just its own file.
    root = _write(
        tmp_path / "tasks.py",
        """
        import livery.footman as footman
        from livery.footman import task

        @task
        def audit(): ...

        @footman.pre_tasks
        def gate(inv):
            if "ship" in inv.tasks:
                inv.tasks["ship"].add_pre(inv.tasks["audit"])
        """,
    )
    sub = _write(
        tmp_path / "svc" / "tasks.py",
        """
        from livery.footman import task

        @task
        def ship(): ...
        """,
    )
    view = registry.Tasks(_discover.load_tree([root, sub]))
    assert view["audit"].fn in view["ship"].pre


def test_hooks_run_in_cascade_order_specific_last(tmp_path):
    # Root's hook runs first, the folder nearest cwd last — the same
    # "local overrides global" precedence the task cascade uses.
    root = _write(
        tmp_path / "tasks.py",
        """
        import livery.footman as footman
        from livery.footman import task

        @task
        def x(): ...

        @footman.pre_tasks
        def f(inv):
            fn = inv.tasks["x"].fn
            fn._order = [*getattr(fn, "_order", []), "root"]
        """,
    )
    sub = _write(
        tmp_path / "svc" / "tasks.py",
        """
        import livery.footman as footman

        @footman.pre_tasks
        def f(inv):
            fn = inv.tasks["x"].fn
            fn._order = [*getattr(fn, "_order", []), "svc"]
        """,
    )
    tree = _discover.load_tree([root, sub])
    assert getattr(tree.tasks["x"], "_order") == ["root", "svc"]


def test_a_hook_that_raises_is_named(tmp_path):
    bad = _write(
        tmp_path / "tasks.py",
        """
        import livery.footman as footman

        @footman.pre_tasks
        def boom(inv):
            raise ValueError("nope")
        """,
    )
    with pytest.raises(_discover.HookError, match="boom"):
        _discover.load_tree([bad])


def test_tasks_view_iterates_nested_tasks(tmp_path):
    src = _write(
        tmp_path / "tasks.py",
        """
        from livery.footman import task, group

        @task
        def a(): ...

        docs = group("docs")

        @docs.task
        def build(): ...
        """,
    )
    view = registry.Tasks(_discover.load_tree([src]))
    assert sorted(t.name for t in view) == ["a", "build"]


def test_a_hook_disable_reaches_the_manifest(tmp_path):
    # Finalizers run at discovery, before the manifest is built — so a
    # `disable()` shows up in the baked node (`disabled`).
    src = _write(
        tmp_path / "tasks.py",
        """
        import livery.footman as footman
        from livery.footman import task

        @task
        def x(): ...

        @footman.pre_tasks
        def off(inv):
            inv.tasks["x"].disable("off by policy")
        """,
    )
    tree = _discover.load_tree([src])
    node = _manifest.build_manifest(tree)["tree"]["tasks"]["x"]
    assert node["disabled"] == "off by policy"


def test_task_view_add_post_and_read_post(tmp_path):
    src = _write(
        tmp_path / "tasks.py",
        """
        import livery.footman as footman
        from livery.footman import task

        @task
        def deploy(): ...

        @task
        def notify(): ...

        @footman.pre_tasks
        def wire(inv):
            inv.tasks["deploy"].add_post(inv.tasks["notify"])
        """,
    )
    view = registry.Tasks(_discover.load_tree([src]))
    assert view["notify"].fn in view["deploy"].post
    assert "missing" not in view
    with pytest.raises(KeyError):
        _ = view["missing"]


def test_task_view_disabled_reads_the_reason(tmp_path):
    src = _write(
        tmp_path / "tasks.py",
        """
        import livery.footman as footman
        from livery.footman import task

        @task
        def a(): ...

        @task
        def b(): ...

        @footman.pre_tasks
        def off(inv):
            inv.tasks["a"].disable("off by policy")
        """,
    )
    view = registry.Tasks(_discover.load_tree([src]))
    assert view["a"].disabled == "off by policy"
    assert view["b"].disabled is None


def test_task_view_reads_policy_flags(tmp_path):
    src = _write(
        tmp_path / "tasks.py",
        """
        from livery.footman import task

        @task(keep_going=False, atomic=True, confirm="sure?")
        def gated(): ...

        @task
        def plain(): ...
        """,
    )
    view = registry.Tasks(_discover.load_tree([src]))
    gated, plain = view["gated"], view["plain"]
    assert gated.keep_going is False
    assert gated.atomic is True
    assert gated.confirm == "sure?"
    # A plain task reports the neutral defaults, not None-vs-missing noise.
    assert plain.keep_going is None
    assert plain.atomic is False
    assert plain.infinite is False
    assert plain.interactive is False
    assert plain.timed is True
    assert plain.confirm == ""


def test_task_view_infinite_is_untimed(tmp_path):
    src = _write(
        tmp_path / "tasks.py",
        """
        from livery.footman import task

        @task(infinite=True)
        def serve(): ...
        """,
    )
    view = registry.Tasks(_discover.load_tree([src]))
    assert view["serve"].infinite is True
    assert view["serve"].timed is False  # infinite implies no timing history


def test_task_view_address_is_the_whole_spelling(tmp_path):
    # The address is what the command line types, at any depth. `group`
    # only ever named the immediate parent, so a doubly-nested task had no
    # public full spelling at all — the gap that had a consumer walking
    # `Tasks._root` to reconstruct one.
    src = _write(
        tmp_path / "tasks.py",
        """
        from livery.footman import task, group

        @task
        def top(): ...

        forge = group("forge")
        dev = forge.group("dev")

        @dev.task
        def up(): ...
        """,
    )
    view = registry.Tasks(_discover.load_tree([src]))
    deep = view["forge.dev.up"]
    assert deep.path == ("forge", "dev", "up")
    assert deep.address == "forge.dev.up"
    assert deep.name == "up"  # the leaf still spells itself
    assert view["top"].path == ("top",)
    assert view["top"].address == "top"


def test_lookup_is_by_address_not_leaf(tmp_path):
    # Two tasks share a leaf; each address is unique, so lookup either finds
    # one task or finds none — there is nothing to be ambiguous about, and
    # nothing for footman to guess between.
    src = _write(
        tmp_path / "tasks.py",
        """
        from livery.footman import task, group

        docs = group("docs")
        web = group("web")

        @docs.task
        def build(): ...

        @web.task
        def build(): ...
        """,
    )
    view = registry.Tasks(_discover.load_tree([src]))
    assert view["docs.build"].address == "docs.build"
    assert view["web.build"].address == "web.build"
    assert view.get("build") is None  # a leaf is not an address
    assert "build" not in view
    with pytest.raises(KeyError):
        view["build"]
    # Searching by leaf is the caller's, and iteration already does it.
    assert sorted(v.address for v in view if v.name == "build") == [
        "docs.build",
        "web.build",
    ]


def test_a_runnable_group_answers_at_its_bare_address(tmp_path):
    # `fm lint` runs `lint.default`, so the bare group name is that action's
    # other spelling. Lookup honours it, rather than being the one place it
    # is not true.
    src = _write(
        tmp_path / "tasks.py",
        """
        from livery.footman import group

        lint = group("lint")

        @lint.default
        def _all(): ...

        @lint.task
        def python(): ...
        """,
    )
    view = registry.Tasks(_discover.load_tree([src]))
    assert view["lint"].address == "lint.default"  # the same task, either way
    assert view["lint"].fn is view["lint.default"].fn
    assert "lint" in view
    # A group with no default has no bare action, so it answers nothing.
    src2 = _write(
        tmp_path / "plain" / "tasks.py",
        """
        from livery.footman import group

        docs = group("docs")

        @docs.task
        def build(): ...
        """,
    )
    plain = registry.Tasks(_discover.load_tree([src2]))
    assert plain.get("docs") is None


def test_task_view_owning_group(tmp_path):
    src = _write(
        tmp_path / "tasks.py",
        """
        from livery.footman import task, group

        @task
        def top(): ...

        docs = group("docs")

        @docs.task
        def build(): ...
        """,
    )
    view = registry.Tasks(_discover.load_tree([src]))
    assert view["top"].group is None  # top-level task is in no named group
    assert view["docs.build"].group is not None
    assert view["docs.build"].group.name == "docs"
    # The leaf is not an address, and the address is what holds a task.
    assert view.get("build") is None
    assert view["docs.build"].address == "docs.build"


def test_task_view_provenance_single_file(tmp_path):
    src = _write(
        tmp_path / "tasks.py",
        """
        from livery.footman import task

        @task
        def x(): ...
        """,
    )
    view = registry.Tasks(_discover.load_tree([src]))
    x = view["x"]
    assert x.defining_dir == str(src.parent)
    assert x.source_file is not None and x.source_file.endswith("tasks.py")
    assert x.shadowed is None
    assert x.shadow_chain == (x.fn,)


def test_task_view_mounted_from_names_the_provider(tmp_path, monkeypatch):
    # Ownership, as a provider identity rather than a file path: it answers
    # for a task whose body is not Python, and it keeps absolute home paths
    # out of anything a consumer renders or commits.
    (tmp_path / "provkit.py").write_text(
        textwrap.dedent(
            """
            from livery.footman import task, group

            lint = group("lint")

            @lint.task
            def strict(): ...
            """
        )
    )
    src = _write(
        tmp_path / "tasks.py",
        """
        from livery.footman import task, include

        include("provkit")

        @task
        def mine(): ...
        """,
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(compose, "_module_trees", {})
    view = registry.Tasks(_discover.load_tree([src]))
    assert view["lint.strict"].mounted_from == "provkit"
    # A task the tasks file writes itself has no provider — the honest
    # answer, not a hole: the project owns it.
    assert view["mine"].mounted_from is None


def test_task_view_shadow_chain_across_cascade(tmp_path):
    root = _write(
        tmp_path / "tasks.py",
        """
        from livery.footman import task

        @task
        def x():
            "root version"
        """,
    )
    sub = _write(
        tmp_path / "svc" / "tasks.py",
        """
        from livery.footman import task

        @task
        def x():
            "svc version"
        """,
    )
    view = registry.Tasks(_discover.load_tree([root, sub]))
    x = view["x"]  # the winning (nearest-cwd) definition
    assert x.defining_dir == str(sub.parent)
    assert x.fn.__doc__ == "svc version"
    assert x.shadowed is not None and x.shadowed.__doc__ == "root version"
    chain = x.shadow_chain
    assert [t.__doc__ for t in chain] == ["svc version", "root version"]


def test_task_view_set_opts_is_permanent(tmp_path):
    src = _write(
        tmp_path / "tasks.py",
        """
        import livery.footman as footman
        from livery.footman import task

        @task
        def x(): ...

        @footman.pre_tasks
        def policy(inv):
            inv.tasks["x"].set_opts(keep_going=False, atomic=True)
        """,
    )
    view = registry.Tasks(_discover.load_tree([src]))
    assert view["x"].keep_going is False
    assert view["x"].atomic is True
    # It writes the same attributes the policy accessors read.
    assert registry.keeps_going(view["x"].fn) is False


def test_task_view_set_opts_rejects_a_task_parameter(tmp_path):
    src = _write(
        tmp_path / "tasks.py",
        """
        import livery.footman as footman
        from livery.footman import task

        @task
        def x(): ...

        @footman.pre_tasks
        def bad(inv):
            inv.tasks["x"].set_opts(fix=True)  # a task parameter, not a policy option
        """,
    )
    # `set_opts` reuses `.opts()`'s validation, so a stray parameter is a taught
    # error surfaced (named) through the hook.
    with pytest.raises(_discover.HookError, match="bad"):
        _discover.load_tree([src])


def test_a_hook_uses_defining_dir_for_a_cascade_decision(tmp_path):
    # The motivating case: gate every task defined under an `infra/` folder,
    # deciding purely from provenance the hook reads off the view.
    root = _write(
        tmp_path / "tasks.py",
        """
        import livery.footman as footman
        from livery.footman import task

        @task
        def audit(): ...

        @footman.pre_tasks
        def gate_infra(inv):
            audit = inv.tasks["audit"]
            for t in inv.tasks:
                if (t.defining_dir or "").endswith("infra"):
                    t.add_pre(audit)
        """,
    )
    infra = _write(
        tmp_path / "infra" / "tasks.py",
        """
        from livery.footman import task

        @task
        def deploy(): ...
        """,
    )
    view = registry.Tasks(_discover.load_tree([root, infra]))
    assert view["audit"].fn in view["deploy"].pre


# --- what the invocation adds over the tree view it replaced ------------------


def test_a_hook_sets_the_environment_every_task_sees(tmp_path, monkeypatch):
    # The moment is pre-DAG and single-threaded, so os.environ is ordinary code
    # here — and it lands before availability gates, which is the point: a
    # requires_env task is available because a hook supplied the variable.
    monkeypatch.delenv("LIFECYCLE_TOKEN", raising=False)
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n')
    (tmp_path / "tasks.py").write_text(
        textwrap.dedent(
            """
            import os
            import livery.footman as footman
            from livery.footman import task, requires_env

            @footman.pre_tasks
            def supply(inv):
                os.environ["LIFECYCLE_TOKEN"] = "from-the-hook"

            @task
            @requires_env("LIFECYCLE_TOKEN", reason="needs a token")
            def publish():
                print("token:", os.environ["LIFECYCLE_TOKEN"])
            """
        )
    )
    result = Runner().invoke("publish", cwd=tmp_path)
    assert result.ok, result.stderr
    assert "token: from-the-hook" in result.stdout


def test_the_invocation_carries_what_the_line_asked(tmp_path):
    seen: dict[str, object] = {}
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n')
    (tmp_path / "tasks.py").write_text(
        textwrap.dedent(
            """
            import json, pathlib
            import livery.footman as footman
            from livery.footman import task

            @footman.pre_tasks
            def record(inv):
                pathlib.Path("seen.json").write_text(json.dumps({
                    "keep_going": inv.cli.get("keep_going"),
                    "cwd_is_set": bool(inv.cwd),
                    "root_is_set": bool(inv.root),
                    "task_names": sorted(t.name for t in inv.tasks),
                }))

            @task
            def build(): ...
            """
        )
    )
    result = Runner().invoke("-k build", cwd=tmp_path)
    assert result.ok, result.stderr
    seen = json.loads((tmp_path / "seen.json").read_text())
    assert seen["keep_going"] is True  # the line's own globals
    assert seen["cwd_is_set"] and seen["root_is_set"]
    assert seen["task_names"] == ["build"]


def test_a_hook_with_the_wrong_arity_is_refused_at_registration():
    # The lifecycle names its moments and they differ by arity, so a typo is a
    # taught error naming the hook rather than a TypeError at the first task.
    reg = Group("root")

    with pytest.raises(RegistrationError, match=r"pre_tasks is handed 1"):

        @reg.pre_tasks
        def no_args(): ...

    with pytest.raises(RegistrationError, match=r"takes 2 argument"):

        @reg.pre_tasks
        def too_many(inv, extra): ...


def test_finalize_is_gone():
    # Removed outright rather than kept as a refusing shim: the lifecycle has
    # one name per moment, and a retired alias is a second one.
    import livery.footman as footman

    reg = Group("root")
    assert not hasattr(reg, "finalize")
    assert not hasattr(footman, "finalize")
    assert "finalize" not in footman.__all__


def test_the_invocation_refuses_writes_once_frozen():
    from livery.footman.invocation import Frozen, Invocation

    inv = Invocation(cwd="/tmp")
    inv.root = "/tmp"  # editable at pre_tasks
    inv.freeze()
    with pytest.raises(Frozen, match=r"editable only at pre_tasks"):
        inv.root = "/elsewhere"


# --- the per-task ladder: pre_task / post_task -------------------------------


def test_pre_and_post_fire_around_every_execution():
    # A chain segment, a prerequisite and a body call are all executions, and
    # each gets the pair. pre_task fires post-bind, so `task.args` holds what
    # the body receives — defaults included.
    reg = Group("root")
    log: list[tuple[object, ...]] = []

    @reg.pre_task
    def opened(inv, task):
        log.append(("pre", task.name, dict(task.args)))

    @reg.post_task
    def closed(inv, task, result):
        log.append(("post", task.name, _executor.reported_state(result)))

    @reg.task
    def build(target: str = "web") -> str:
        return target.upper()

    @reg.task(pre=[build])
    def publish():
        build()  # satisfied by the prerequisite: a `shared` post, no pre

    result = Runner().invoke("publish", tasks=reg)
    assert result.ok, result.stderr
    assert log == [
        ("pre", "build", {"target": "web"}),
        ("post", "build", "ok"),
        ("pre", "publish", {}),
        # The pair is per request, only the body is shared: the call's
        # request gets its own pre and closes with the `shared` row.
        ("pre", "build", {"target": "web"}),
        ("post", "build", "shared"),
        ("post", "publish", "ok"),
    ]


def test_task_state_is_private_to_the_plugin_and_the_execution():
    # Two plugins each get their own namespace on the same execution, and the
    # same plugin gets a fresh one per execution — nothing leaks sideways or
    # forward.
    reg = Group("root")
    seen: list[tuple[object, ...]] = []

    def a_pre(inv, task):
        assert not vars(task.state)  # fresh per execution
        task.state.token = f"a:{task.name}"

    def a_post(inv, task, result):
        seen.append(("a", task.name, task.state.token, vars(task.state)))

    def b_pre(inv, task):
        assert not vars(task.state)  # b never sees a's writes
        task.state.token = f"b:{task.name}"

    def b_post(inv, task, result):
        seen.append(("b", task.name, task.state.token))

    for fn in (a_pre, a_post):
        fn.__module__ = "plugin_a"
    for fn in (b_pre, b_post):
        fn.__module__ = "plugin_b"
    reg.pre_task(a_pre)
    reg.post_task(a_post)
    reg.pre_task(b_pre)
    reg.post_task(b_post)

    @reg.task
    def one(): ...

    @reg.task
    def two(): ...

    result = Runner().invoke("--sequential one two", tasks=reg)
    assert result.ok, result.stderr
    # Posts unwind in reverse plugin order: b speaks first, a last.
    assert seen == [
        ("b", "one", "b:one"),
        ("a", "one", "a:one", {"token": "a:one"}),
        ("b", "two", "b:two"),
        ("a", "two", "a:two", {"token": "a:two"}),
    ]


def test_a_raising_pre_fails_the_task_and_skips_the_body():
    # A raising pre fails the task and the body never runs — but a post is
    # the task-finished event: once the execution reached the body stage,
    # every registered post fires when it concludes, the raiser's own
    # included, irrespective of how any pre fared. The failure names the
    # plugin and the hook.
    reg = Group("root")
    log: list[str] = []
    ran: list[str] = []

    def a_pre(inv, task):
        log.append("a:pre")

    def a_post(inv, task, result):
        log.append(f"a:post ok={result.ok}")

    def b_pre(inv, task):
        raise ValueError("no thanks")

    def b_post(inv, task, result):
        log.append(f"b:post ok={result.ok}")  # fires: its span still closes

    for fn in (a_pre, a_post):
        fn.__module__ = "plugin_a"
    for fn in (b_pre, b_post):
        fn.__module__ = "plugin_b"
    reg.pre_task(a_pre)
    reg.post_task(a_post)
    reg.pre_task(b_pre)
    reg.post_task(b_post)

    @reg.task
    def build():
        ran.append("body")

    result = Runner().invoke("build", tasks=reg)
    assert not result.ok
    assert ran == []  # a raising pre fails the task like a failed pre-dep
    assert log == ["a:pre", "b:post ok=False", "a:post ok=False"]
    assert "pre_task hook 'b_pre' from plugin_b failed" in result.stderr
    assert "ValueError: no thanks" in result.stderr


def test_a_raising_post_fails_a_green_task():
    reg = Group("root")

    @reg.pre_task
    def opened(inv, task): ...

    @reg.post_task
    def crash(inv, task, result):
        raise RuntimeError("reporter went down")

    @reg.task
    def build() -> str:
        return "fine"

    result = Runner().invoke("build", tasks=reg)
    assert not result.ok  # a reporter that crashed must not pass silently
    assert "post_task hook 'crash'" in result.stderr
    assert "reporter went down" in result.stderr


def test_a_post_failure_never_masks_the_bodys_own():
    # Context-manager semantics: a cleanup error during an exception loses to
    # the original.
    reg = Group("root")

    @reg.post_task
    def crash(inv, task, result):
        raise RuntimeError("reporter went down")

    @reg.task
    def build():
        raise ValueError("the real failure")

    result = Runner().invoke("build", tasks=reg)
    assert not result.ok
    assert "the real failure" in result.stderr


def test_set_returned_rewrites_the_report_never_the_value():
    # The body's return was snapshotted at its exit: a dependent's
    # body call still receives it, while the report and `--json` carry the
    # rewrite. The write lives in the review window now — reviewed and
    # attributed — never in observation.
    import livery.footman as footman

    reg = Group("root")

    def redact(view):
        view.set_returned("[redacted]")

    @reg.task
    @footman.pre_record(redact)
    def build() -> str:
        return "secret-artifact"

    @reg.task(pre=[build])
    def publish():
        assert build() == "secret-artifact"  # the value, not the report

    result = Runner().invoke("publish", tasks=reg)
    assert result.ok, result.stderr
    build_rows = [r.returned for r in result.results if r.task == "build"]
    # The caller's body received the real value; the report — the execution's
    # row AND the `shared` row the body call added — carries the rewrite,
    # because a shared answer is the record REUSED: the later request's row
    # copies what the execution reported. A redaction covers every row that
    # would have leaked the secret.
    assert build_rows == ["[redacted]", "[redacted]"]


def test_a_pre_returning_a_value_is_noted_as_reserved():
    # The return channel is reserved for a future "supply the result, skip
    # the body" power — today it is a note, never a failure.
    reg = Group("root")

    @reg.pre_task
    def eager(inv, task):
        return "something"

    @reg.task
    def build(): ...

    result = Runner().invoke("build", tasks=reg)
    assert result.ok, result.stderr
    text = result.stdout + result.stderr
    assert "reserved" in text
    assert "task.state" in text


def test_env_written_in_pre_reaches_the_body():
    # task.env is the task's own overlay: the environ router serves it to
    # in-body reads, and it never touches the process globals.
    import os as real_os

    reg = Group("root")

    @reg.pre_task
    def inject(inv, task):
        task.env["HOOKED_VALUE"] = "from-pre"

    @reg.task
    def build():
        import os

        print(os.environ.get("HOOKED_VALUE", "missing"))

    result = Runner().invoke("build", tasks=reg)
    assert result.ok, result.stderr
    assert "from-pre" in result.stdout
    assert "HOOKED_VALUE" not in real_os.environ


def test_dry_run_is_a_rehearsal_hooks_included():
    # A dry run rehearses the run: bodies execute, so the lifecycle fires —
    # a hook is part of "what would happen". Only footman's own work (the
    # recorded run() calls, the deferred steps) is faked.
    reg = Group("root")
    fired: list[str] = []

    @reg.pre_task
    def opened(inv, task):
        fired.append(task.name)

    @reg.task
    def build(): ...

    result = Runner().invoke("--dry-run build", tasks=reg)
    assert result.ok, result.stderr
    assert fired == ["build"]


def test_a_post_only_plugin_observes_every_execution():
    # A plugin with no pre has nothing to pair with, so its post rides the
    # moment itself: it fires for every execution that ran.
    reg = Group("root")
    seen: list[tuple[str, bool]] = []

    @reg.post_task
    def report(inv, task, result):
        seen.append((task.name, result.ok))

    @reg.task
    def build(): ...

    result = Runner().invoke("build", tasks=reg)
    assert result.ok, result.stderr
    assert seen == [("build", True)]


def test_hook_arity_is_taught_at_registration():
    reg = Group("root")
    with pytest.raises(RegistrationError, match=r"def lonely\(inv, task\)"):

        @reg.pre_task
        def lonely(inv): ...

    with pytest.raises(RegistrationError, match=r"def eager\(inv, task, result\)"):

        @reg.post_task
        def eager(inv, task): ...


def test_the_handle_is_read_only_where_it_should_be():
    reg = Group("root")
    probed: list[str] = []

    @reg.pre_task
    def probe(inv, task):
        with pytest.raises(AttributeError, match="read-only"):
            task.name = "renamed"
        with pytest.raises(TypeError):
            task.args["x"] = 1  # a mapping proxy, not a dict
        assert task.source_hash is not None  # the tripwire, exposed
        probed.append(task.name)

    @reg.post_task
    def probe_result(inv, task, result):
        with pytest.raises(AttributeError, match="observers see, never judge"):
            result.ok = True
        with pytest.raises(AttributeError, match="set_returned"):
            result.set_returned("nope")  # the write moved to the review window
        probed.append("post")

    @reg.task
    def build(target: str = "web"): ...

    result = Runner().invoke("build", tasks=reg)
    assert result.ok, result.stderr
    assert probed == ["build", "post"]


def test_the_ladder_reaches_a_cascade_file(tmp_path):
    # The disk path: hooks in a tasks.py ride the merged tree's contributions
    # into the run — the same ladder the in-memory path fires.
    src = _write(
        tmp_path / "tasks.py",
        """
        import livery.footman as footman
        from livery.footman import task

        @footman.pre_task
        def opened(inv, task):
            print(f"pre {task.name}")

        @footman.post_task
        def closed(inv, task, result):
            print(f"post {task.name} ok={result.ok}")

        @task
        def build(): ...
        """,
    )
    result = Runner().invoke("build", tasks=src)
    assert result.ok, result.stderr
    assert "pre build" in result.stdout
    assert "post build ok=True" in result.stdout


# --- pre_bind: the bind boundary ---------------------------------------------


def test_pre_bind_env_reaches_env_fallbacks():
    # The headline: the managed window opens before binding, so what pre_bind
    # writes into task.env is what env() fallbacks resolve — the one moment a
    # plugin can influence what the body is handed.
    import os as real_os

    reg = Group("root")

    @reg.pre_bind
    def creds(inv, task):
        task.env["LADDER_TOKEN"] = f"tok-{task.name}"

    @reg.task
    def deploy(
        token: Annotated[str, env("LADDER_TOKEN")] = "anon",
    ) -> str:
        print(token)
        return token

    result = Runner().invoke("deploy", tasks=reg)
    assert result.ok, result.stderr
    assert "tok-deploy" in result.stdout
    assert "LADDER_TOKEN" not in real_os.environ  # scoped, never global


def test_pre_bind_env_reaches_a_body_calls_binding():
    # A body call binds like a segment, so its omitted parameters see the
    # same pre_bind-injected environment the declared path sees.
    reg = Group("root")

    @reg.pre_bind
    def creds(inv, task):
        task.env["CALL_TOKEN"] = f"tok-{task.name}"

    @reg.task
    def build(token: Annotated[str, env("CALL_TOKEN")] = "anon") -> str:
        return token

    @reg.task
    def release():
        assert build() == "tok-build"

    result = Runner().invoke("release", tasks=reg)
    assert result.ok, result.stderr


def test_task_args_is_guarded_at_pre_bind_and_readable_at_pre_task():
    reg = Group("root")
    seen: list[object] = []

    @reg.pre_bind
    def early(inv, task):
        with pytest.raises(RuntimeError, match="pre_task, the post-bind moment"):
            task.args

    @reg.pre_task
    def later(inv, task):
        seen.append(dict(task.args))

    @reg.task
    def build(target: str = "web"): ...

    result = Runner().invoke("build", tasks=reg)
    assert result.ok, result.stderr
    assert seen == [{"target": "web"}]


def test_a_bind_failure_still_fires_the_posts():
    # The attempt concluded — a bind-time span needs closing — so the posts
    # fire with the refusal result, exactly as the finished-event rule says.
    reg = Group("root")
    closed: list[tuple[str, bool, int]] = []

    @reg.pre_bind
    def poison(inv, task):
        task.env["LADDER_JOBS"] = "40"  # out of bounds: binding will refuse

    @reg.post_task
    def observe(inv, task, result):
        closed.append((task.name, result.ok, result.code))

    @reg.task
    def build(
        jobs: Annotated[int, env("LADDER_JOBS"), between(1, 10)] = 1,
    ):
        raise AssertionError("never runs")

    result = Runner().invoke("build", tasks=reg)
    assert not result.ok
    assert "must be between 1 and 10" in result.stderr
    assert closed == [("build", False, 64)]  # EX_USAGE: a refusal, observed


def test_a_raising_pre_bind_fails_the_task_before_binding():
    reg = Group("root")
    closed: list[str] = []

    @reg.pre_bind
    def refuse(inv, task):
        raise ValueError("vault is sealed")

    @reg.post_task
    def observe(inv, task, result):
        closed.append(task.name)

    @reg.task
    def build():
        raise AssertionError("never runs")

    result = Runner().invoke("build", tasks=reg)
    assert not result.ok
    assert "pre_bind hook 'refuse'" in result.stderr
    assert "vault is sealed" in result.stderr
    assert closed == ["build"]  # the attempt concluded: the post still fired


def test_the_ladder_is_per_request_only_the_body_is_shared():
    # A body call whose row ends up `shared` still gets the whole ladder —
    # pre_bind, pre_task, post_task — closed with the `shared` row. Only the
    # body itself is shared, so pairing never depends on sharing.
    reg = Group("root")
    log: list[str] = []

    @reg.pre_bind
    def bound(inv, task):
        log.append(f"bind:{task.name}")

    @reg.pre_task
    def opened(inv, task):
        log.append(f"pre:{task.name}")

    @reg.post_task
    def closed(inv, task, result):
        log.append(f"post:{task.name}:{_executor.reported_state(result)}")

    @reg.task
    def build() -> str:
        return "dist"

    @reg.task(pre=[build])
    def publish():
        build()  # satisfied by the prerequisite's execution

    result = Runner().invoke("publish", tasks=reg)
    assert result.ok, result.stderr
    assert log == [
        "bind:build",
        "pre:build",
        "post:build:ok",
        "bind:publish",
        "pre:publish",
        "bind:build",  # the call's own request: the full ladder…
        "pre:build",
        "post:build:shared",  # …closed with its `shared` row
        "post:publish:ok",
    ]


def _bind_stamp(value):
    # module-level: eval_str resolves annotation names in module globals
    import os

    os.environ["BIND_STAMP"] = "set-by-validator"


def test_an_environ_write_during_bind_is_scoped_not_global():
    # The widened window covers user code that binding runs — a check(fn)
    # validator writing os.environ is captured into the task's overlay, so a
    # parallel sibling never sees it.
    import os as real_os

    reg = Group("root")

    @reg.task
    def build(target: Annotated[str, check(_bind_stamp)] = "web"):
        import os

        print(os.environ.get("BIND_STAMP", "missing"))

    result = Runner().invoke("build --target=web", tasks=reg)
    assert result.ok, result.stderr
    assert "set-by-validator" in result.stdout
    assert "BIND_STAMP" not in real_os.environ


def test_pre_bind_arity_is_taught_at_registration():
    reg = Group("root")
    with pytest.raises(RegistrationError, match=r"def alone\(inv, task\)"):

        @reg.pre_bind
        def alone(inv): ...


def test_a_declared_repeat_gets_the_pair_with_its_shared_row():
    # `fm build build`: the second segment's request is satisfied by the
    # first — and still gets the pair, closed with its `shared` row.
    reg = Group("root")
    log: list[str] = []

    @reg.pre_task
    def opened(inv, task):
        log.append(f"pre:{task.name}")

    @reg.post_task
    def closed(inv, task, result):
        log.append(f"post:{task.name}:{_executor.reported_state(result)}")

    @reg.task
    def build() -> str:
        return "dist"

    result = Runner().invoke("--sequential build build", tasks=reg)
    assert result.ok, result.stderr
    assert log == [
        "pre:build",
        "post:build:ok",
        "pre:build",
        "post:build:shared",
    ]


def test_a_raising_pre_on_a_satisfied_request_fails_only_that_request():
    # The pair is per request, and so are its failures: a pre that flakes on
    # the second request fails that request — the execution it would have
    # shared stays green.
    reg = Group("root")
    seen = {"build": 0}

    def flaky(inv, task):
        if task.name == "build":
            seen["build"] += 1
            if seen["build"] > 1:
                raise ValueError("flaked on the repeat")

    flaky.__module__ = "plugin_flaky"
    reg.pre_task(flaky)

    @reg.task
    def build() -> str:
        return "dist"

    @reg.task(pre=[build])
    def publish():
        build()  # this request's pre flakes; the caller fails

    result = Runner().invoke("publish", tasks=reg)
    assert not result.ok
    assert "flaked on the repeat" in result.stderr
    states = [(r.task, _executor.reported_state(r)) for r in result.results]
    assert ("build", "ok") in states  # the execution itself stayed green


def test_a_crashing_post_on_a_shared_request_fails_the_caller():
    reg = Group("root")

    @reg.post_task
    def report(inv, task, result):
        if _executor.reported_state(result) == "shared":
            raise RuntimeError("reporter tripped on the share")

    @reg.task
    def build() -> str:
        return "dist"

    @reg.task(pre=[build])
    def publish():
        build()

    result = Runner().invoke("publish", tasks=reg)
    assert not result.ok
    assert "reporter tripped on the share" in result.stderr


def test_a_raising_pre_on_a_repeated_segment_fails_that_segment_only():
    reg = Group("root")
    seen = {"n": 0}

    def flaky(inv, task):
        seen["n"] += 1
        if seen["n"] > 1:
            raise ValueError("second request refused")

    flaky.__module__ = "plugin_flaky"
    reg.pre_task(flaky)

    @reg.task
    def build() -> str:
        return "dist"

    # Sequential, so which request is "second" is deterministic; parallel
    # segments would race for the claim. (Row order is the report's business
    # — a request that never began sorts by cause, not by clock.)
    result = Runner().invoke("--sequential build build", tasks=reg)
    assert not result.ok
    states = [(r.task, _executor.reported_state(r)) for r in result.results]
    assert ("build", "ok") in states  # the execution itself stayed green
    assert ("build", "failed") in states  # the refused second request


def test_a_raising_pre_bind_on_a_body_call_fails_the_caller():
    reg = Group("root")
    closed: list[str] = []

    def sealed(inv, task):
        if task.name == "build":
            raise ValueError("vault is sealed")

    sealed.__module__ = "plugin_vault"
    reg.pre_bind(sealed)

    @reg.post_task
    def observe(inv, task, result):
        closed.append(f"{task.name}:{_executor.reported_state(result)}")

    @reg.task
    def build() -> str:
        return "dist"

    @reg.task
    def publish():
        build()

    result = Runner().invoke("publish", tasks=reg)
    assert not result.ok
    assert "pre_bind hook 'sealed' from plugin_vault" in result.stderr
    # The call's attempt concluded before binding — its post fired — and the
    # caller failed with the named hook error.
    assert "build:failed" in closed
    assert "publish:failed" in closed


def test_a_bind_failure_on_a_body_call_fires_the_posts():
    reg = Group("root")
    closed: list[tuple[str, int]] = []

    def poison(inv, task):
        if task.name == "build":
            task.env["CALLBIND_JOBS"] = "40"  # out of bounds: binding refuses

    poison.__module__ = "plugin_poison"
    reg.pre_bind(poison)

    @reg.post_task
    def observe(inv, task, result):
        closed.append((task.name, result.code))

    @reg.task
    def build(
        jobs: Annotated[int, env("CALLBIND_JOBS"), between(1, 10)] = 1,
    ):
        raise AssertionError("never runs")

    @reg.task
    def publish():
        build()

    result = Runner().invoke("publish", tasks=reg)
    assert not result.ok
    assert "must be between 1 and 10" in result.stderr
    assert ("build", 64) in closed  # EX_USAGE: the refusal, observed


def test_a_pre_bind_returning_a_value_is_noted_as_reserved():
    reg = Group("root")

    @reg.pre_bind
    def eager(inv, task):
        return "something"

    @reg.task
    def build(): ...

    result = Runner().invoke("build", tasks=reg)
    assert result.ok, result.stderr
    text = result.stdout + result.stderr
    assert "pre_bind" in text and "reserved" in text


# --- the wrappers: one generator instead of a pair ---------------------------


def test_wrap_task_spans_every_request_shared_included():
    # One yield: pre half, body, post half — locals carry the state, and the
    # wrapper enters per request, so a call satisfied by the prerequisite's
    # execution still opens and closes, resumed with its `shared` row.
    reg = Group("root")
    log: list[str] = []

    @reg.wrap_task
    def span(inv, task):
        log.append(f"open:{task.name}")
        result = yield
        log.append(f"close:{task.name}:{_executor.reported_state(result)}")

    @reg.task
    def build() -> str:
        return "dist"

    @reg.task(pre=[build])
    def publish():
        build()

    result = Runner().invoke("publish", tasks=reg)
    assert result.ok, result.stderr
    assert log == [
        "open:build",
        "close:build:ok",
        "open:publish",
        "open:build",
        "close:build:shared",
        "close:publish:ok",
    ]


def test_wrap_task_never_sees_a_bind_failure_but_a_post_does():
    # The wrapper anchors at pre_task, which a bind failure never reaches —
    # its generator never starts, so there is nothing to unwind. The
    # explicit post still observes the refusal; wrap_bind is the wrapper
    # that enters early enough to see it.
    reg = Group("root")
    log: list[str] = []

    @reg.pre_bind
    def poison(inv, task):
        task.env["WRAPBIND_N"] = "40"

    @reg.wrap_task
    def span(inv, task):
        log.append("open")
        yield
        log.append("close")

    @reg.post_task
    def observe(inv, task, result):
        log.append(f"post:{result.code}")

    @reg.task
    def build(n: Annotated[int, env("WRAPBIND_N"), between(1, 10)] = 1): ...

    result = Runner().invoke("build", tasks=reg)
    assert not result.ok
    assert log == ["post:64"]  # the span never opened; the refusal observed


def test_wrap_task_with_zero_yields_is_taught():
    reg = Group("root")

    @reg.wrap_task
    def eager(inv, task):
        if False:
            yield  # a generator function that returns before yielding

    @reg.task
    def build(): ...

    result = Runner().invoke("build", tasks=reg)
    assert not result.ok
    assert "returned without yielding" in result.stderr


def test_wrap_task_with_a_second_yield_fails_the_task():
    reg = Group("root")

    @reg.wrap_task
    def greedy(inv, task):
        yield
        yield  # one too many

    @reg.task
    def build(): ...

    result = Runner().invoke("build", tasks=reg)
    assert not result.ok
    assert "yielded a second time" in result.stderr


def test_wrap_bind_spans_bind_and_body_and_closes_on_a_bind_failure():
    reg = Group("root")
    log: list[str] = []

    @reg.pre_bind
    def poison(inv, task):
        if task.name == "bad":
            task.env["WRAPSPAN_N"] = "40"

    @reg.wrap_bind
    def audit(inv, task):
        try:
            bound = yield
            log.append(f"bound:{task.name}:{dict(bound)}")
            result = yield
            log.append(f"done:{task.name}:{_executor.reported_state(result)}")
        except ValueError as exc:
            log.append(f"bindfail:{task.name}:{'between 1 and 10' in str(exc)}")
        finally:
            log.append(f"closed:{task.name}")

    @reg.task
    def build(target: str = "web") -> str:
        return target

    @reg.task
    def bad(n: Annotated[int, env("WRAPSPAN_N"), between(1, 10)] = 1): ...

    result = Runner().invoke("build", tasks=reg)
    assert result.ok, result.stderr
    assert log == ["bound:build:{'target': 'web'}", "done:build:ok", "closed:build"]

    log.clear()
    result = Runner().invoke("bad", tasks=reg)
    assert not result.ok
    # The failure arrived at the first yield; except saw it, finally closed.
    assert log == ["bindfail:bad:True", "closed:bad"]


def test_wrap_bind_that_stops_after_one_yield_is_taught():
    reg = Group("root")

    @reg.wrap_bind
    def short(inv, task):
        yield  # takes the bound arguments, then stops

    @reg.task
    def build(): ...

    result = Runner().invoke("build", tasks=reg)
    assert not result.ok
    assert "finished after one yield" in result.stderr


def test_a_wrapper_must_be_a_generator_function():
    reg = Group("root")
    with pytest.raises(RegistrationError, match="must be a generator function"):

        @reg.wrap_task
        def plain(inv, task): ...

    with pytest.raises(RegistrationError, match="must be a generator function"):

        @reg.wrap_bind
        def flat(inv, task): ...


# --- post_tasks: the run report's moment -------------------------------------


def test_post_tasks_receives_the_whole_story():
    reg = Group("root")
    story: dict[str, Any] = {}

    @reg.post_tasks
    def digest(inv):
        story["rows"] = [
            (r.task, _executor.reported_state(r), r.blocked_by) for r in inv.results
        ]
        story["skipped"] = [r.task for r in inv.skipped]
        story["total"] = inv.total_ms
        with pytest.raises(Exception):
            inv.cwd = "elsewhere"  # still frozen: hooks read, never write

    @reg.task
    def lint():
        raise ValueError("broken")

    @reg.task(pre=[lint])
    def build(): ...

    result = Runner().invoke("-k build", tasks=reg)
    assert not result.ok
    assert story["rows"] == [("lint", "failed", ""), ("build", "skipped", "lint")]
    assert story["skipped"] == ["build"]
    assert story["total"] >= 0


def test_the_run_end_hook_reads_sealed_records_and_review_owns_the_rewrite():
    # Sealed means sealed at every altitude: the run-end hook observes, and
    # the reported-value rewrite lives in the review window, where it is
    # attributed. The envelope carries what the review left.
    import json as json_mod

    import livery.footman as footman

    reg = Group("root")
    seen: list[object] = []

    @reg.post_tasks
    def observe(inv):
        for r in inv.results:
            seen.append(r.returned)
            with pytest.raises(AttributeError):
                r.set_returned("nope")  # observers see, never judge

    def redact(view):
        view.set_returned("[redacted]")

    @reg.task
    @footman.pre_record(redact)
    def build() -> str:
        return "secret"

    result = Runner().invoke("--json build", tasks=reg)
    assert result.ok, result.stderr
    envelope = json_mod.loads(result.stdout)
    assert envelope["items"][0]["returned"] == "[redacted]"
    assert seen == ["[redacted]"]  # the observer saw the sealed record


def test_post_tasks_stdout_goes_to_stderr_under_json():
    import json as json_mod

    reg = Group("root")

    @reg.post_tasks
    def chatty(inv):
        print("summary: fine")  # must not corrupt the envelope

    @reg.task
    def build(): ...

    result = Runner().invoke("--json build", tasks=reg)
    assert result.ok
    json_mod.loads(result.stdout)  # stdout is still one valid document
    assert "summary: fine" in result.stderr


def test_a_raising_post_tasks_fails_a_green_invocation():
    reg = Group("root")

    @reg.post_tasks
    def crash(inv):
        raise RuntimeError("reporter went down")

    @reg.task
    def build(): ...

    result = Runner().invoke("build", tasks=reg)
    assert result.exit_code == 1  # must not pass silently
    assert "post_tasks hook 'crash'" in result.stderr
    assert "reporter went down" in result.stderr


def test_a_skipped_row_never_headlines_the_exit_code():
    from livery.footman import context as fm_context

    reg = Group("root")

    @reg.task
    def lint():
        fm_context.fail("broken", code=3)

    @reg.task(pre=[lint])
    def build(): ...

    result = Runner().invoke("-k build", tasks=reg)
    assert result.exit_code == 3  # the cause's code, not the skip's


def test_post_tasks_arity_is_taught():
    reg = Group("root")
    with pytest.raises(RegistrationError, match=r"def wide\(inv\)"):

        @reg.post_tasks
        def wide(inv, extra): ...


def test_a_request_that_waited_on_a_failure_is_blamed_on_it():
    # Sequential repeat of a failing task: the second request joins the
    # first's (failed) cell — genuine prevention, so blocked_by names it and
    # the report seats the consequence after its cause.
    reg = Group("root")

    @reg.task
    def boom():
        raise ValueError("no")

    result = Runner().invoke("--sequential -k boom boom", tasks=reg)
    assert not result.ok
    rows = list(result.results)
    assert [r.blocked_by for r in rows] == ["", "boom"]
    assert rows[1].started is None  # never began: a hole, blamed


def test_launch_latency_is_recorded_and_reported():
    import json as json_mod
    import time as time_mod

    reg = Group("root")

    @reg.task
    def slow():
        time_mod.sleep(0.05)

    @reg.task(pre=[slow])
    def after(): ...

    @reg.task
    def root_task(): ...

    result = Runner().invoke("--json after root-task", tasks=reg)
    assert result.ok, result.stderr
    rows = {r.task: r for r in result.results}
    dependent = rows["after"]
    assert dependent.eligible is not None
    assert dependent.started is not None
    assert dependent.started >= dependent.eligible  # waited, never time-travelled
    assert rows["root_task" if "root_task" in rows else "root-task"].eligible is None
    envelope = json_mod.loads(result.stdout)
    by_name = {e["task"]: e for e in envelope["items"] if "task" in e}
    assert "queued_ms" in by_name["after"]
    assert "queued_ms" not in by_name["root-task"]  # roots have no latency


# --- confirm=, however the task is reached -----------------------------------


def test_a_prerequisites_confirm_is_asked_and_a_denial_blocks_dependents():
    # The documented rule made true for the third reach-path: a segment asks
    # up front, a body call asks at the call, and now a prerequisite asks up
    # front too. Denied, it never runs — and its dependent skips, blamed.
    reg = Group("root")

    @reg.task(confirm="really wipe?")
    def wipe():
        raise AssertionError("must not run")

    @reg.task(pre=[wipe])
    def rebuild():
        raise AssertionError("must not run either")

    result = Runner().invoke("--no-input rebuild", tasks=reg)
    assert not result.ok
    rows = {r.task: r for r in result.results}
    assert "not confirmed" in str(rows["wipe"].error)
    assert _executor.reported_state(rows["rebuild"]) == "skipped"
    assert rows["rebuild"].blocked_by == "wipe"


def test_one_reference_is_asked_once_however_many_ways_it_is_reached(monkeypatch):
    from livery.footman import _schedule

    asked: list[str] = []

    def fake_ask(message, *, no_input):
        asked.append(message)
        return True

    monkeypatch.setattr(_schedule, "_ask_confirm", fake_ask)
    reg = Group("root")

    @reg.task(confirm="deploy?")
    def deploy(): ...

    @reg.task(pre=[deploy])
    def web(): ...

    @reg.task(pre=[deploy])
    def api(): ...

    result = Runner().invoke("deploy web api", tasks=reg)
    assert result.ok, result.stderr
    assert asked == ["deploy?"]  # one question covered segment and prereq


def test_yes_auto_confirms_a_prerequisite():
    reg = Group("root")
    ran: list[str] = []

    @reg.task(confirm="really?")
    def gated():
        ran.append("gated")

    @reg.task(pre=[gated])
    def top():
        ran.append("top")

    result = Runner().invoke("--yes top", tasks=reg)
    assert result.ok, result.stderr
    assert ran == ["gated", "top"]


# --- calling a task from a hook ----------------------------------------------


def test_a_task_call_from_pre_tasks_is_refused(tmp_path):
    # `pre_tasks` runs at discovery — in the manifest child too, where a call
    # would run the task on a Tab press. Refused, naming the moment.
    src = _write(
        tmp_path / "tasks.py",
        """
        import livery.footman as footman
        from livery.footman import task

        @task
        def helper():
            print("HELPER RAN")

        @task
        def main(): ...

        @footman.pre_tasks
        def wire(inv):
            helper()
        """,
    )
    result = Runner().invoke("main", tasks=src)
    assert not result.ok
    assert "cannot be called from @pre_tasks" in result.stderr
    assert "Tab press" in result.stderr  # says why, not just that
    assert "HELPER RAN" not in result.stdout


def test_a_task_call_from_post_tasks_is_refused(tmp_path):
    src = _write(
        tmp_path / "tasks.py",
        """
        import livery.footman as footman
        from livery.footman import task

        @task
        def helper():
            print("HELPER RAN")

        @task
        def main(): ...

        @footman.post_tasks
        def report(inv):
            helper()
        """,
    )
    result = Runner().invoke("main", tasks=src)
    assert not result.ok
    assert "cannot be called from @post_tasks" in result.stderr
    assert "HELPER RAN" not in result.stdout


def test_the_refusal_names_a_task_that_declares_ctx(tmp_path):
    # The old failure was an AttributeError from the first argument landing in
    # the ctx slot; the refusal must come first, and name the task.
    src = _write(
        tmp_path / "tasks.py",
        """
        import livery.footman as footman
        from livery.footman import task

        @task
        def ctxhelper(ctx, word: str = "plain"): ...

        @task
        def main(): ...

        @footman.pre_tasks
        def wire(inv):
            ctxhelper("prod")
        """,
    )
    result = Runner().invoke("main", tasks=src)
    assert not result.ok
    assert "ctxhelper cannot be called from @pre_tasks" in result.stderr
    assert "AttributeError" not in result.stderr


def test_a_manifest_rebuild_never_runs_a_task(tmp_path, monkeypatch):
    # The hazard the refusal exists for: the refresh child runs `pre_tasks`.
    from livery.footman import _paths, _refresh

    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    monkeypatch.chdir(tmp_path)
    _write(
        tmp_path / "tasks.py",
        f"""
        import livery.footman as footman
        from pathlib import Path
        from livery.footman import task

        @task
        def deploy():
            Path({str(tmp_path / "SIDE_EFFECT")!r}).write_text("ran")

        @footman.pre_tasks
        def wire(inv):
            deploy()
        """,
    )
    _refresh.refresh_cwd()  # exactly what a Tab press spawns
    assert not (tmp_path / "SIDE_EFFECT").exists()


def test_the_per_task_moments_still_call_tasks(tmp_path):
    # The four moments inside the run stay fully supported: the call is a real
    # request, with its own row in the report.
    src = _write(
        tmp_path / "tasks.py",
        """
        import livery.footman as footman
        from livery.footman import task

        @task
        def helper() -> str:
            return "helped"

        @task
        def main(): ...

        @footman.pre_task
        def before(inv, t):
            if t.name == "main":
                assert helper() == "helped"

        @footman.post_task
        def after(inv, t, result):
            if t.name == "main":
                assert helper() == "helped"
        """,
    )
    result = Runner().invoke("main", tasks=src)
    assert result.ok, result.stderr
    assert "helper" in result.stderr  # it earned a row, shared between the two


def test_a_plain_call_outside_a_run_is_untouched(tmp_path):
    # The refusal is scoped to the hook moments — importing a tasks module and
    # calling a task is the plain function call it looks like.
    reg = Group("root")

    @reg.task
    def helper() -> str:
        return "helped"

    assert helper() == "helped"


def test_a_stacked_reviewer_amends_the_row_verdict(tmp_path):
    # The review window at task grain: the body's code is the draft, the
    # reviewer's word is the verdict, and the audit names them both.
    (tmp_path / "tasks.py").write_text(
        textwrap.dedent(
            """
            import livery.footman as footman
            from livery.footman import task

            def reformatted_is_fine(view):
                view.title = "fmt: reformatted"
                view.code = 0

            @footman.pre_record(reformatted_is_fine)
            @task
            def fmt():
                return 1  # the tool's honest "I changed files"
            """
        )
    )
    result = Runner().invoke("--json fmt", cwd=tmp_path)
    assert result.ok, result.stderr
    row = json.loads(result.stdout)["items"][0]
    assert row["ok"] is True and row["code"] == 0
    assert row["title"] == "fmt: reformatted"
    assert row["audit"] == [["body", "fmt", 1], ["review", "reformatted_is_fine", 0]]
    assert row["failed_at"] is None  # reviewed green IS green


def test_row_reviewers_run_inside_out_and_the_use_site_wins(tmp_path):
    (tmp_path / "tasks.py").write_text(
        textwrap.dedent(
            """
            import livery.footman as footman
            from livery.footman import task

            def outer(view):
                view.title = view.title + "+outer"

            def inner(view):
                view.title = view.title + "+inner"

            @footman.pre_record(outer)
            @footman.pre_record(inner)
            @task
            def build(): ...
            """
        )
    )
    result = Runner().invoke("--json build", cwd=tmp_path)
    assert result.ok, result.stderr
    row = json.loads(result.stdout)["items"][0]
    # Nearest the def runs first; each outer reviewer sees what it left.
    assert row["title"] == "build+inner+outer"
    assert [e[1] for e in row["audit"]] == ["build", "inner", "outer"]


def test_a_raising_row_reviewer_fails_the_task_with_its_own_error(tmp_path):
    (tmp_path / "tasks.py").write_text(
        textwrap.dedent(
            """
            import livery.footman as footman
            from livery.footman import task

            def broken(view):
                raise KeyError("oops")

            @footman.pre_record(broken)
            @task
            def build(): ...
            """
        )
    )
    result = Runner().invoke("--json build", cwd=tmp_path)
    assert not result.ok
    row = json.loads(result.stdout)["items"][0]
    assert row["ok"] is False
    assert "pre_record hook 'broken'" in (row["error"] or "")
    assert row["audit"][-1] == ["review", "broken", None]  # involved, then broke


def test_a_green_row_vetoed_in_review_keeps_its_earned_code(tmp_path):
    (tmp_path / "tasks.py").write_text(
        textwrap.dedent(
            """
            import livery.footman as footman
            from livery.footman import task

            def too_easy(view):
                view.code = 3

            @footman.pre_record(too_easy)
            @task
            def build(): ...
            """
        )
    )
    result = Runner().invoke("--json build", cwd=tmp_path)
    assert not result.ok
    row = json.loads(result.stdout)["items"][0]
    assert row["code"] == 3 and row["failed_at"] == "review"
    assert row["audit"][0] == ["body", "build", 0]  # the green it earned, kept


def test_an_observer_vetoes_with_fail_and_the_audit_tells_the_story():
    # The veto: an observer cannot rewrite a sealed record, but it can fail
    # the task — loudly, with its own code, attributed to the observe
    # moment. The work's earned green stays visible in the audit.
    import livery.footman as footman

    reg = Group("root")

    @reg.post_task
    def budget(inv, task, result):
        if task.name == "build" and result.ok:
            footman.fail("too easy to be true", code=3)

    @reg.task
    def build(): ...

    result = Runner().invoke("build", tasks=reg)
    assert not result.ok
    row = next(r for r in result.results if r.task == "build")
    assert row.code == 3  # the veto's own code, never a flat 1
    assert row.failed_at == "observe"
    assert row.work_code == 0  # the green the work earned, kept visible
    assert [tuple(e) for e in row.audit] == [
        ("body", "build", 0),
        ("observe", "budget", 3),
    ]


def test_the_task_handle_carries_its_own_lifecycle():
    # Code local to the task, no plugins anywhere: the handle-attached
    # lane fires on its own, in lifecycle order, and the observer holds
    # the sealed record.
    reg = Group("root")
    calls: list[object] = []

    @reg.task
    def build() -> str:
        calls.append("body")
        return "v"

    @build.pre_task
    def warm():
        calls.append("warm")

    @build.pre_record
    def review(view):
        calls.append("review")
        view.title = "built"

    @build.post_task
    def watch(result):
        calls.append(("watch", result.title, result.ok))
        with pytest.raises(AttributeError, match="observers see"):
            result.ok = False

    result = Runner().invoke("build", tasks=reg)
    assert result.ok, result.stderr
    assert calls == ["warm", "body", "review", ("watch", "built", True)]


def test_the_handle_attached_observer_vetoes_like_any_other():
    import livery.footman as footman

    reg = Group("root")

    @reg.task
    def build(): ...

    @build.post_task
    def budget(result):
        footman.fail("not buying it", code=4)

    result = Runner().invoke("build", tasks=reg)
    assert not result.ok
    row = next(r for r in result.results if r.task == "build")
    assert row.code == 4 and row.failed_at == "observe"
    assert [tuple(e) for e in row.audit] == [
        ("body", "build", 0),
        ("observe", "budget", 4),
    ]


def test_plugins_are_the_outer_ring_and_the_tasks_own_hooks_nest_inside():
    reg = Group("root")
    order: list[str] = []

    @reg.pre_task
    def plugin_pre(inv, task):
        order.append("plugin-pre")

    @reg.post_task
    def plugin_post(inv, task, result):
        order.append("plugin-post")

    @reg.task
    def build():
        order.append("body")

    @build.pre_task
    def own_pre():
        order.append("own-pre")

    @build.post_task
    def own_post(result):
        order.append("own-post")

    result = Runner().invoke("build", tasks=reg)
    assert result.ok, result.stderr
    # Plugins are the wider audience — the outer ring; the task's own
    # hooks nest closest to the body, entering last and leaving first.
    assert order == ["plugin-pre", "own-pre", "body", "own-post", "plugin-post"]


def test_wrap_task_spans_nest_when_one_execution_reaches_another_inline():
    # A body call with a different binding is a distinct execution, run
    # inline on the caller's thread — the wrapper's span state is a stack,
    # so the inner close takes the inner span and the outer still closes.
    reg = Group("root")
    spans: list[str] = []

    @reg.task
    def build(again: bool = False) -> int:
        if not again:
            build(again=True)
        return 0

    @build.wrap_task
    def span():
        spans.append("open")
        yield
        spans.append("close")

    result = Runner().invoke("build", tasks=reg)
    assert result.ok, result.stderr
    assert spans == ["open", "open", "close", "close"]


def test_wrap_task_sugar_on_the_handle_spans_one_execution():
    reg = Group("root")
    spans: list[object] = []

    @reg.task
    def build() -> int:
        return 0

    @build.wrap_task
    def span():
        spans.append("open")
        result = yield
        spans.append(("close", result.ok))

    result = Runner().invoke("build", tasks=reg)
    assert result.ok, result.stderr
    assert spans == ["open", ("close", True)]
