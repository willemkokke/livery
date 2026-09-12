"""Composition: @requires availability gates, include(), plugin entry points."""

from __future__ import annotations

import importlib
import sys
import textwrap

import pytest

from livery.footman import _manifest, compose, registry
from livery.footman._executor import EX_USAGE
from livery.footman.registry import (
    Group,
    RegistrationError,
    requires,
    requires_dep,
    requires_env,
    requires_tool,
)
from livery.footman.testing import Runner

# --- @requires availability gates -----------------------------------------------


def _tree(build):
    reg = Group("root")
    build(reg)
    return reg, _manifest.build_manifest(reg)["tree"]


def test_requires_false_predicate_is_listed_but_disabled():
    def tasks(reg):
        @reg.task(name="release")
        @requires(lambda: False, reason="CI only")
        def release(): ...

    _, tree = _tree(tasks)
    assert tree["tasks"]["release"]["disabled"] == "CI only"


def test_requires_predicate_is_reevaluated_live():
    gate = {"open": False}

    def tasks(reg):
        @reg.task(name="guarded")
        @requires(lambda: gate["open"], reason="gate closed")
        def guarded():
            print("ran")

    reg, _ = _tree(tasks)
    runner = Runner()

    result = runner.invoke("guarded", tasks=reg)
    assert result.exit_code == EX_USAGE
    assert "gate closed" in str(result.results[0].error)

    gate["open"] = True  # the manifest is stale now — execution must not care
    result = runner.invoke("guarded", tasks=reg)
    assert result.ok
    assert "ran" in result.stdout


def test_requires_raising_predicate_reads_as_unavailable():
    def tasks(reg):
        @reg.task(name="guarded")
        @requires(lambda: 1 / 0, reason="broken gate")
        def guarded(): ...

    _, tree = _tree(tasks)
    assert "broken gate (ZeroDivisionError" in tree["tasks"]["guarded"]["disabled"]
    reg, _ = _tree(tasks)
    result = Runner().invoke("guarded", tasks=reg)
    assert result.exit_code == EX_USAGE


def test_requires_dep_present_is_available():
    def tasks(reg):
        @reg.task(name="publish")
        @requires_dep("io")  # a stdlib module: always importable
        def publish(): ...

    _, tree = _tree(tasks)
    assert "disabled" not in tree["tasks"]["publish"]


def test_requires_dep_missing_is_listed_but_disabled():
    def tasks(reg):
        @reg.task(name="publish")
        @requires_dep("stripe_nope", "google_nope")
        def publish(): ...

    _, tree = _tree(tasks)
    assert tree["tasks"]["publish"]["disabled"] == "requires stripe_nope, google_nope"

    reg, _ = _tree(tasks)
    result = Runner().invoke("publish", tasks=reg)
    assert result.exit_code == EX_USAGE
    assert "requires stripe_nope" in str(result.results[0].error)


def test_requires_dep_custom_reason():
    def tasks(reg):
        @reg.task(name="publish")
        @requires_dep("stripe_nope", reason="pip install devkit[release]")
        def publish(): ...

    _, tree = _tree(tasks)
    assert tree["tasks"]["publish"]["disabled"] == "pip install devkit[release]"


def test_requires_dep_does_not_import(monkeypatch):
    # Availability is find_spec-only: building the manifest for a task that
    # requires a module must never import that module.
    import sys

    calls = []
    real = __import__

    def tracking_import(name, *a, **k):
        if name == "textwrap":
            calls.append(name)
        return real(name, *a, **k)

    # monkeypatch.delitem evicts textwrap AND restores it on teardown — a bare
    # sys.modules.pop here would leak the eviction into the rest of the session.
    monkeypatch.delitem(sys.modules, "textwrap", raising=False)
    monkeypatch.setattr("builtins.__import__", tracking_import)

    def tasks(reg):
        @reg.task(name="publish")
        @requires_dep("textwrap")  # importable, but must stay unimported
        def publish(): ...

    _, tree = _tree(tasks)
    assert "disabled" not in tree["tasks"]["publish"]  # found via find_spec
    assert calls == []  # ...without importing it
    assert "textwrap" not in sys.modules


def test_requires_dep_broken_parent_lists_unavailable_not_crash(tmp_path, monkeypatch):
    # A dotted dep imports parent packages via find_spec; a parent whose
    # __init__ raises must read as unavailable, never crash fm --list.
    pkg = tmp_path / "brokenparent"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("raise RuntimeError('parent boom')\n")
    (pkg / "child.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))

    def tasks(reg):
        @reg.task(name="publish")
        @requires_dep("brokenparent.child")
        def publish(): ...

    _, tree = _tree(tasks)  # must not raise
    assert tree["tasks"]["publish"]["disabled"] == "requires brokenparent.child"


def test_requires_env_gates_on_the_environment(monkeypatch):
    monkeypatch.delenv("FM_GATE_VAR", raising=False)

    def tasks(reg):
        @reg.task(name="publish")
        @requires_env("FM_GATE_VAR")
        def publish(): ...

    _, tree = _tree(tasks)
    assert tree["tasks"]["publish"]["disabled"] == "set FM_GATE_VAR"

    monkeypatch.setenv("FM_GATE_VAR", "1")  # live: set it and it clears
    _, tree = _tree(tasks)
    assert "disabled" not in tree["tasks"]["publish"]


def test_requires_tool_gates_on_path():
    def tasks(reg):
        @reg.task(name="up")
        @requires_tool("no_such_tool_xyz")
        def up(): ...

    _, tree = _tree(tasks)
    assert tree["tasks"]["up"]["disabled"] == "requires no_such_tool_xyz on PATH"


def test_requires_tool_present_is_available(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: f"/usr/bin/{cmd}")

    def tasks(reg):
        @reg.task(name="up")
        @requires_tool("docker")
        def up(): ...

    _, tree = _tree(tasks)
    assert "disabled" not in tree["tasks"]["up"]


def test_requires_collects_all_failures(monkeypatch):
    # No short-circuit: a task gated on a missing dep AND a missing var reports
    # both, each in its own words.
    monkeypatch.delenv("FM_GATE_VAR", raising=False)

    def tasks(reg):
        @reg.task(name="publish")
        @requires_dep("stripe_nope")
        @requires_env("FM_GATE_VAR")
        def publish(): ...

    _, tree = _tree(tasks)
    disabled = tree["tasks"]["publish"]["disabled"]
    assert "requires stripe_nope" in disabled
    assert "set FM_GATE_VAR" in disabled


def test_disabled_prerequisite_fails_the_dependent():
    ran = []

    def tasks(reg):
        @reg.task(name="up")
        @requires(lambda: False, reason="requires docker")
        def up(): ...

        @reg.task(pre=[up])
        def integration():
            ran.append("integration")

    reg, _ = _tree(tasks)
    result = Runner().invoke("integration", tasks=reg)
    assert result.exit_code == EX_USAGE  # hard failure, not a silent skip
    assert ran == []  # the dependent was skipped


def test_disabled_annotation_in_listing(fm_project):
    fm = fm_project(
        """
        from livery.footman import task, requires

        @task
        @requires(lambda: False, reason="requires docker on PATH")
        def up():
            "Start the containers."
        """
    )
    result = fm.invoke("--list")
    assert "unavailable: requires docker on PATH" in result.stdout
    helped = fm.invoke("--help up")
    assert "unavailable here: requires docker on PATH" in helped.stdout


# --- include() -------------------------------------------------------------------


@pytest.fixture
def provider(tmp_path, monkeypatch):
    """A real provider package on sys.path, plus dist-info advertising it."""
    pkg = tmp_path / "shared_tasks.py"
    pkg.write_text(
        textwrap.dedent(
            """
            from livery.footman import task, group

            @task
            def lint(fix: bool = False):
                "Shared lint."
                print(f"lint fix={fix}")

            @task
            def fmt():
                "Shared format."
                print("fmt")

            docs = group("docs", help="Shared docs tasks")

            @docs.task
            def build():
                "Build docs."
                print("docs-build")

            @docs.task
            def serve():
                "Serve docs."
            """
        )
    )
    dist = tmp_path / "shared_tasks-1.0.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: shared-tasks\nVersion: 1.0\n"
    )
    (dist / "entry_points.txt").write_text("[footman.tasks]\nshared = shared_tasks\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(compose, "_module_trees", {})
    sys.modules.pop("shared_tasks", None)
    yield tmp_path
    sys.modules.pop("shared_tasks", None)


def test_include_grafts_all(provider):
    with registry.capture() as captured:
        compose.include("shared_tasks")
    assert set(captured.tasks) == {"lint", "fmt"}
    assert set(captured.groups) == {"docs"}


def test_include_cherry_picks_and_namespaces(provider):
    with registry.capture():
        target = Group("sub")
        compose.include("shared_tasks", into=target, only=["lint"])
    assert set(target.tasks) == {"lint"}
    assert not target.groups


def test_include_unknown_only_name_is_a_typo_error(provider):
    with (
        registry.capture(),
        pytest.raises(RegistrationError, match="no task or group at 'lnt'"),
    ):
        compose.include("shared_tasks", only=["lnt"])


def test_dotted_only_materialises_the_path(provider):
    # only= takes full dotted addresses: the nested pick grafts its path —
    # the intermediate group is the source's own forked copy, help riding
    # along — pruned to just the listed leaf.
    with registry.capture() as captured:
        compose.include("shared_tasks", only=["docs.build", "fmt"])
    assert set(captured.tasks) == {"fmt"}
    docs = captured.groups["docs"]
    assert set(docs.tasks) == {"build"}
    assert docs.help == "Shared docs tasks"  # the fork carried the group's help


def test_dotted_exclude_drops_one_leaf(provider):
    with registry.capture() as captured:
        compose.include("shared_tasks", exclude=["docs.build"])
    assert set(captured.groups["docs"].tasks) == {"serve"}
    assert set(captured.tasks) == {"lint", "fmt"}


def test_a_group_pruned_empty_is_dropped(provider):
    with registry.capture() as captured:
        compose.include("shared_tasks", exclude=["docs.build", "docs.serve"])
    assert "docs" not in captured.groups  # never grafted as a shell


def test_only_union_is_redundant_not_an_error(provider):
    with registry.capture() as captured:
        compose.include("shared_tasks", only=["docs", "docs.build"])
    assert set(captured.groups["docs"].tasks) == {"build", "serve"}  # whole group


def test_dotted_filter_typo_is_taught_per_segment(provider):
    with (
        registry.capture(),
        pytest.raises(
            RegistrationError,
            match=r"no task or group at 'docs.buidl' \(docs has: build, serve\)",
        ),
    ):
        compose.include("shared_tasks", only=["docs.buidl"])
    with (
        registry.capture(),
        pytest.raises(RegistrationError, match=r"'fmt' is a task, not a group"),
    ):
        compose.include("shared_tasks", exclude=["fmt.deep"])


def test_include_missing_module_names_the_call_not_the_file():
    # A missing module names the include() call, never blames the tasks file.
    with (
        registry.capture(),
        pytest.raises(
            RegistrationError,
            match=r"include\('no_such_provider_xyz'\): no importable module",
        ),
    ):
        compose.include("no_such_provider_xyz")


def test_local_definition_silently_beats_a_pull(provider):
    # Local-vs-imported: the local leaf wins, whatever the order — the
    # cascade's "user names shadow plugins", carried by provenance.
    with registry.capture() as captured:

        @registry.task
        def lint(): ...

        compose.include("shared_tasks", only=["lint"])  # silent: local wins
        assert captured.tasks["lint"] is lint

    with registry.capture() as captured:
        compose.include("shared_tasks", only=["lint"])

        @registry.task  # local def AFTER the mount shadows it just the same
        def lint(): ...

        assert captured.tasks["lint"] is lint


def test_overlapping_repulls_clash_loudly_unless_override(provider):
    # Imported-vs-imported: every mount is authored, so a clash is a bug with
    # a one-line fix — loud beats silently running the wrong task. The
    # message cites provenance.
    with registry.capture() as captured:
        compose.include("shared_tasks", only=["lint"])
        with pytest.raises(RegistrationError, match="claimed by both"):
            compose.include("shared_tasks", only=["lint"])
        compose.include("shared_tasks", only=["lint"], override=True)
        assert "lint" in captured.tasks
        # Disjoint re-mounts of one source (two filters) compose.
        compose.include("shared_tasks", only=["fmt"])
        assert "fmt" in captured.tasks


def test_include_forks_provider_tree_no_memo_leak(provider):
    # F38: grafting a provider group hands the project a private copy — a later
    # mutation (as the cascade overlay/tag does) must not leak into the shared
    # _module_trees memo and thus into the next in-process invocation.
    with registry.capture():
        target = Group("proj")
        compose.include("shared_tasks", into=target)  # grafts lint/fmt + docs

    target.groups["docs"].tasks["injected"] = lambda: None
    memo = compose._module_trees["shared_tasks"]
    assert "injected" not in memo.groups["docs"].tasks  # memo untouched
    assert target.tasks["lint"] is memo.tasks["lint"]  # fns still shared


def test_include_memoises_per_module(provider):
    with registry.capture() as a:
        compose.include("shared_tasks", only=["lint"])
    with registry.capture() as b:
        compose.include("shared_tasks", only=["fmt"])  # second include still works
    assert set(a.tasks) == {"lint"} and set(b.tasks) == {"fmt"}


def test_include_two_submodules_of_one_package(tmp_path, monkeypatch):
    # H6: `include("pkg.alpha")` walks *through* `pkg` and memoises its empty
    # capture. The next `include("pkg.beta")` must not stop at that shallow
    # memo and report "no task or group at 'beta'" — the deeper module is
    # importable, so the import walk still owns the answer.
    pkg = tmp_path / "provpkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "alpha.py").write_text(
        "from livery.footman import task\n@task\ndef lint(): ...\n"
    )
    (pkg / "beta.py").write_text(
        "from livery.footman import task\n@task\ndef fmt(): ...\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(compose, "_module_trees", {})
    for name in [n for n in sys.modules if n == "provpkg" or n.startswith("provpkg.")]:
        monkeypatch.delitem(sys.modules, name)

    with registry.capture() as captured:
        compose.include("provpkg.alpha")
        compose.include("provpkg.beta")
    assert set(captured.tasks) == {"lint", "fmt"}

    # A real typo inside a memoised tree keeps its taught message.
    with (
        registry.capture(),
        pytest.raises(RegistrationError, match=r"no task or group at 'gamma'"),
    ):
        compose.include("provpkg.gamma")


def test_a_pre_imported_empty_parent_is_walked_through(tmp_path, monkeypatch):
    # H7: the monorepo shape — constants in the package, tasks in a submodule.
    # `from devkit import REGISTRY` puts `devkit` in sys.modules, and the
    # already-imported branch refused before it ever read `allow_empty`, so
    # `include("devkit.tasks")` died naming `devkit` — a module the caller
    # never wrote — with advice about capturing tasks the package never had.
    # An import that registered nothing is empty, not spent.
    pkg = tmp_path / "devkit"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("REGISTRY = 'ghcr.io/acme'\n")
    (pkg / "tasks.py").write_text(
        "from livery.footman import task\n@task\ndef lint(): ...\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(compose, "_module_trees", {})
    for name in [n for n in sys.modules if n == "devkit" or n.startswith("devkit.")]:
        monkeypatch.delitem(sys.modules, name)

    importlib.import_module("devkit")  # what the tasks file's own import does
    with registry.capture() as captured:
        compose.include("devkit.tasks")
    assert set(captured.tasks) == {"lint"}


def test_a_pre_imported_module_with_tasks_still_refuses(tmp_path, monkeypatch):
    # The other side of the same ladder: this one really was spent. Something
    # is here, and the import that would have captured it is gone — so the
    # refusal stays, and now it only fires when it is true.
    (tmp_path / "spent.py").write_text(
        "from livery.footman import task\n@task\ndef go(): ...\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(compose, "_module_trees", {})
    monkeypatch.delitem(sys.modules, "spent", raising=False)

    importlib.import_module("spent")
    with (
        registry.capture(),
        pytest.raises(RegistrationError, match=r"already imported outside include"),
    ):
        compose.include("spent")


def test_a_pre_imported_empty_module_named_directly_says_it_is_empty(
    tmp_path, monkeypatch
):
    # Named as the terminal module rather than walked through, an empty one is
    # still an error — but the one that describes it: nothing to mount, rather
    # than a story about import order it had no part in.
    (tmp_path / "inert.py").write_text("VERSION = '1.0'\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(compose, "_module_trees", {})
    monkeypatch.delitem(sys.modules, "inert", raising=False)

    importlib.import_module("inert")
    with (
        registry.capture(),
        pytest.raises(RegistrationError, match=r"registered no tasks"),
    ):
        compose.include("inert")


def test_the_mount_failure_paths_teach_and_are_pinned(tmp_path, monkeypatch):
    # Audit M97: the taught refusals below had no test — any of them could
    # regress to a raw traceback (or vanish) with the suite staying green.
    monkeypatch.setattr(compose, "_module_trees", {})
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("prov_ok", None)
    (tmp_path / "prov_ok.py").write_text(
        "from livery.footman import task\n\n\n@task\ndef ping(): ...\n"
    )

    with registry.capture():

        @registry.task
        def fmt(): ...

        with pytest.raises(
            compose.RegistrationError, match=r"'fmt' is a task — into= names a group"
        ):
            compose.include("prov_ok", into="fmt")

    with (
        registry.capture(),
        pytest.raises(compose.RegistrationError, match=r"'default' is a task"),
    ):
        compose.include("prov_ok", into="x.default")

    sys.modules.pop("provpkg", None)
    (tmp_path / "provpkg").mkdir()
    (tmp_path / "provpkg" / "__init__.py").write_text("")
    (tmp_path / "provpkg" / "real.py").write_text(
        "from livery.footman import task\n\n\n@task\ndef pong(): ...\n"
    )
    with (
        registry.capture(),
        pytest.raises(
            compose.RegistrationError,
            match=r"no task or group at 'nosuch'.*has: nothing",
        ),
    ):
        compose.include("provpkg.nosuch")


def test_a_provider_that_raises_on_import_names_the_tasks_file(tmp_path, monkeypatch):
    # The provider's own error is the story; discovery wraps it at the file
    # boundary, so the refusal names the tasks file whose include() pulled
    # the broken module in, with the original riding the chain.
    from livery.footman import _discover

    monkeypatch.setattr(compose, "_module_trees", {})
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("prov_boom", None)
    (tmp_path / "prov_boom.py").write_text('raise RuntimeError("boom at import")\n')
    root = tmp_path / "tasks.py"
    root.write_text("from livery.footman import include\n\ninclude('prov_boom')\n")
    with pytest.raises(_discover.TasksImportError) as caught:
        _discover.load_tree([root])
    assert "boom at import" in str(caught.value) or "boom at import" in str(
        caught.value.__cause__
    )


def test_mounting_one_provider_twice_registers_its_hooks_once(tmp_path, monkeypatch):
    # The mount engine extended the live root's contributions on every
    # mount, and a fork shares the provider's hook functions — so including
    # one provider at two addresses ran its @pre_tasks twice per run,
    # silently, side effects and all.
    from livery.footman import _discover

    monkeypatch.setattr(compose, "_module_trees", {})
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("twice_tasks", None)
    (tmp_path / "twice_tasks.py").write_text(
        textwrap.dedent(
            """
            from livery.footman import task, pre_tasks

            @task
            def ping(): ...

            @pre_tasks
            def hook(inv): ...
            """
        )
    )
    root = tmp_path / "tasks.py"
    root.write_text(
        textwrap.dedent(
            """
            from livery.footman import include

            include("twice_tasks", into="a")
            include("twice_tasks", into="b")
            """
        )
    )
    tree = _discover.load_tree([root])
    assert "ping" in tree.groups["a"].tasks and "ping" in tree.groups["b"].tasks
    hooks = [f.__name__ for f in tree.contributions["pre_tasks"]]
    assert hooks == ["hook"]  # once — not once per address


def test_include_carries_a_providers_hook_to_the_merged_tree(tmp_path, monkeypatch):
    # An included provider's `@pre_tasks` must run over the *merged* tree — the
    # env-guard pattern the cascade relies on. include() moves the provider's
    # hooks onto the live root that discovery collects and runs, so they are
    # not stranded on the forked subtree.
    #
    # Assert on the merged TREE, never a provider module-global: include() imports
    # the provider under capture() as a distinct instance, and `_evict_siblings`
    # drops it from sys.modules — so a re-`import` of it sees a stale copy the
    # grafted hook never touched (the module-aliasing trap).
    from livery.footman import _discover

    monkeypatch.setattr(compose, "_module_trees", {})
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("guard_tasks", None)
    (tmp_path / "guard_tasks.py").write_text(
        textwrap.dedent(
            """
            from livery.footman import task, pre_tasks

            @task
            def shared_audit(): ...

            @pre_tasks
            def gate(inv):
                for t in inv.tasks:
                    if t.name.startswith("deploy"):
                        t.add_pre(inv.tasks["shared-audit"])
            """
        )
    )
    root = tmp_path / "tasks.py"
    root.write_text(
        textwrap.dedent(
            """
            from livery.footman import task, include

            @task
            def deploy_web(): ...

            include("guard_tasks")
            """
        )
    )
    view = registry.Tasks(_discover.load_tree([root]))
    # the provider's hook edited a ROOT task, proving it saw the merged tree.
    assert view["shared-audit"].fn in view["deploy-web"].pre


def test_included_tasks_run_from_the_includers_dir(tmp_path, monkeypatch):
    # F58: an included provider task is stamped with the INCLUDER's directory,
    # not the provider module's. Observe it directly: the task prints ctx.cwd,
    # which must equal the includer's project dir even though the provider lives
    # elsewhere on disk.
    provider_dir = tmp_path / "elsewhere"
    provider_dir.mkdir()
    (provider_dir / "prov.py").write_text(
        "from livery.footman import task\n@task\ndef show(ctx):\n    print(ctx.cwd)\n"
    )
    monkeypatch.syspath_prepend(str(provider_dir))

    project = tmp_path / "proj"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname="x"\n')
    (project / "tasks.py").write_text(
        "from livery.footman import include\ninclude('prov')\n"
    )

    result = Runner().invoke("show", cwd=project)
    assert result.ok
    printed = [ln.strip() for ln in result.stdout.splitlines()]
    assert str(project) in printed  # ctx.cwd = the includer's dir
    assert str(provider_dir) not in result.stdout  # not the provider module's


# --- plugin() / entry points -------------------------------------------------------


def test_plugin_resolves_entry_point(provider):
    tree = compose.plugin("shared")
    assert set(tree.tasks) == {"lint", "fmt"}


def test_plugin_unknown_names_installed(provider):
    with pytest.raises(RegistrationError, match=r"installed: .*shared"):
        compose.plugin("nope")


def test_pull_line_splats_an_anonymous_container(provider, tmp_path):
    # plugin("shared") resolves the entry point; the module capture's root is
    # an anonymous container, so mounting it lands its *children* — the splat.
    project = tmp_path / "proj2"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname="x"\n')
    (project / "tasks.py").write_text(
        "from livery.footman import plugin, task\nplugin('shared')\n@task\ndef own(): ...\n"
    )
    result = Runner().invoke("lint", cwd=project)
    assert result.ok
    assert "lint fix=False" in result.stdout
    listing = Runner().invoke("--list", cwd=project)
    assert "docs.build" in listing.stdout and "own" in listing.stdout


def test_pull_line_into_names_the_consumers_placement(provider, tmp_path):
    # into= is the consumer's remap: a dotted address, created on demand.
    project = tmp_path / "proj2b"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname="x"\n')
    (project / "tasks.py").write_text(
        "from livery.footman import plugin\nplugin('shared', into='vendor.kit')\n"
    )
    result = Runner().invoke("vendor.kit.lint", cwd=project)
    assert result.ok and "lint fix=False" in result.stdout


def test_user_task_shadows_a_pulled_one(provider, tmp_path):
    project = tmp_path / "proj3"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname="x"\n')
    (project / "tasks.py").write_text(
        "from livery.footman import plugin, task\n"
        "plugin('shared')\n"
        "@task\ndef lint():\n    print('mine')\n"
    )
    result = Runner().invoke("lint", cwd=project)
    assert result.ok
    assert "mine" in result.stdout  # the user's name wins, silently


def test_missing_plugin_pull_refuses(tmp_path):
    project = tmp_path / "proj4"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname="x"\n')
    (project / "tasks.py").write_text(
        "from livery.footman import plugin, task\nplugin('ghost')\n@task\ndef own(): ...\n"
    )
    result = Runner().invoke("own", cwd=project)
    assert result.exit_code == EX_USAGE
    assert "ghost" in result.stderr


def test_stale_plugins_config_key_is_taught(tmp_path):
    # The config key died with the composition rework; a leftover key is a
    # taught refusal, never a silent ignore.
    project = tmp_path / "proj4b"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname="x"\n[tool.footman]\nplugins = ["shared"]\n'
    )
    (project / "tasks.py").write_text(
        "from livery.footman import task\n@task\ndef own(): ...\n"
    )
    result = Runner().invoke("own", cwd=project)
    assert result.exit_code == EX_USAGE
    assert "plugins key was removed" in result.stderr


def _advertise(tmp_path, monkeypatch, module, body, entry):
    """Put a provider *module* on sys.path with dist-info advertising *entry*."""
    # utf-8 explicitly: write_text's platform default is cp1252 on Windows,
    # and Python reads source as utf-8 — an em dash in a body would poison
    # the provider module for every Windows runner.
    (tmp_path / f"{module}.py").write_text(textwrap.dedent(body), encoding="utf-8")
    dist = tmp_path / f"{module}-1.0.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {module}\nVersion: 1.0\n"
    )
    (dist / "entry_points.txt").write_text(f"[footman.tasks]\n{entry}\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(compose, "_module_trees", {})
    sys.modules.pop(module, None)


def test_plugin_import_failure_is_taught(tmp_path, monkeypatch):
    # F07: a plugin that fails to import teaches instead of dumping a traceback.
    _advertise(
        tmp_path,
        monkeypatch,
        "broken_plugin",
        "import totally_missing_dep_xyz  # noqa\n",
        "broken = broken_plugin",
    )
    with pytest.raises(RegistrationError, match="failed to import"):
        compose.plugin("broken")


def test_dotted_plugin_name_nests_and_shares_namespace(tmp_path, monkeypatch):
    # A plugin's name is its command path: two dotted names sharing a prefix
    # mount under one auto-created `suite` group, neither owning it.
    _advertise(
        tmp_path,
        monkeypatch,
        "nest_alpha",
        """
        from livery.footman import group

        tasks = group("alpha", help="Alpha tasks")

        @tasks.task
        def go():
            "Go alpha."
            print("alpha-go!")
        """,
        "suite.alpha = nest_alpha:tasks",
    )
    _advertise(
        tmp_path,
        monkeypatch,
        "nest_beta",
        """
        from livery.footman import group

        tasks = group("beta", help="Beta tasks")

        @tasks.task
        def go():
            "Go beta."
        """,
        "suite.beta = nest_beta:tasks",
    )
    project = tmp_path / "proj_nest"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname="x"\n')
    (project / "tasks.py").write_text(
        "from livery.footman import plugin, task\n"
        "plugin('suite.alpha', into='suite')\n"
        "plugin('suite.beta', into='suite')\n"
        "@task\ndef own(): ...\n"
    )
    listing = Runner().invoke("--list", cwd=project)
    assert listing.ok
    # Both leaves live under the one shared `suite` namespace group.
    assert "suite.alpha.go" in listing.stdout and "suite.beta.go" in listing.stdout
    ran = Runner().invoke("suite.alpha.go", cwd=project)
    assert ran.ok and "alpha-go!" in ran.stdout


def test_broken_plugin_pull_refuses(tmp_path, monkeypatch):
    # F07 end-to-end: a broken mounted plugin is a clean refusal, not a raw
    # traceback on every invocation.
    _advertise(
        tmp_path,
        monkeypatch,
        "broken2_plugin",
        "import totally_missing_dep_xyz  # noqa\n",
        "broken2 = broken2_plugin",
    )
    project = tmp_path / "proj_broken"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname="x"\n')
    (project / "tasks.py").write_text(
        "from livery.footman import plugin, task\nplugin('broken2')\n@task\ndef own(): ...\n"
    )
    result = Runner().invoke("own", cwd=project)
    assert result.exit_code == EX_USAGE
    assert "failed to import" in result.stderr


def test_plugin_explicit_group_module_is_adopted(tmp_path, monkeypatch):
    # F08: an entry point naming a *module* that registers nothing but exposes
    # one explicit Group is adopted — the documented provider convention, which
    # previously hit the misleading "already imported outside include()" error.
    _advertise(
        tmp_path,
        monkeypatch,
        "explicit_plugin",
        """
        from livery.footman.registry import Group

        tasks = Group("explicit", "Explicit provider")

        @tasks.task
        def ping():
            print("pong")
        """,
        "explicit = explicit_plugin",
    )
    with registry.capture() as captured:
        compose.plugin("explicit")
    assert set(captured.groups["explicit"].tasks) == {"ping"}


# --- include()/plugin() taught errors -----------------------------------------
# footman markets its error messages; the ones nobody had exercised are
# exactly the ones that can rot. Each of these asserts the *teaching*, not
# just the raising.


def test_a_spent_import_still_mounts_its_contributions(monkeypatch):
    """A bare `import livery.footman.profile` before any proper load used to spend
    the module's one import: the entry point's later `load()` captured
    nothing, so the mount refused and the flag scan went blind — the
    worker-ordering flake of 2026-08-14. The declarations survive as
    module-level names, so the load rebuilds them: options and hooks both.
    Deleting the memo recreates the spent state whatever this worker ran
    first."""
    from livery.footman import profile

    assert profile  # imported — possibly bare, possibly first
    monkeypatch.delitem(compose._module_trees, "livery.footman.profile", raising=False)
    with registry.capture() as captured:
        compose.plugin("footman.profile")
    assert [g.name for g in captured.contributions["globals"]] == ["profile"]
    assert [f.__name__ for f in captured.contributions["pre_tasks"]] == ["arm"]
    assert [f.__name__ for f in captured.contributions["post_tasks"]] == ["write"]


def test_include_rebuilds_a_bare_imported_hooks_only_module(tmp_path, monkeypatch):
    """include() parity for the rebuild: a contributions-only module caught
    by a bare import is not a dead end — its options carry their defining
    module and its hooks carry what their decorators registered, wrapper
    pairs included, so the tree comes back from the module's namespace."""
    (tmp_path / "hooks_only.py").write_text(
        textwrap.dedent(
            """
            '''Hooks-only provider.'''

            import livery.footman as footman
            from livery.footman import GlobalOption

            VERBOSE = GlobalOption("hooks-verbose", help="say more")

            @footman.pre_tasks
            def announce(inv):
                pass

            @footman.wrap_task
            def span(inv, task):
                result = yield
            """
        )
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "hooks_only", raising=False)
    with registry.capture():
        # The wrong way, on purpose — and inside a throwaway capture, so the
        # spent declarations land somewhere include() never gets to see.
        import hooks_only  # type: ignore[import-not-found]

    with registry.capture() as captured:
        compose.include(hooks_only)
    assert [g.name for g in captured.contributions["globals"]] == ["hooks-verbose"]
    assert [f.__name__ for f in captured.contributions["pre_tasks"]] == ["announce"]
    assert [f.__name__ for f in captured.contributions["pre_task"]] == ["span"]
    assert [f.__name__ for f in captured.contributions["post_task"]] == ["span"]


def test_include_of_a_pre_imported_module_teaches(tmp_path, monkeypatch):
    """A module already imported outside include() never had its tasks
    captured — re-executing it would double every side effect, so footman
    refuses with guidance instead of guessing."""
    (tmp_path / "early_tasks.py").write_text(
        "from livery.footman import task\n\n@task\ndef early():\n    'Early.'\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "early_tasks", raising=False)
    import early_tasks  # type: ignore[import-not-found]  # the wrong way, on purpose

    with pytest.raises(RegistrationError, match="already imported outside"):
        compose.include(early_tasks)


def test_include_of_a_module_with_no_tasks_teaches(tmp_path, monkeypatch):
    """Nothing to adopt: the message says what to define, and counts what
    it did find."""
    (tmp_path / "empty_tasks.py").write_text("VALUE = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "empty_tasks", raising=False)
    with pytest.raises(RegistrationError, match="no module-level Group"):
        compose.include("empty_tasks")


def test_include_of_a_module_with_two_groups_teaches(tmp_path, monkeypatch):
    """Ambiguous: two Groups and no tasks means footman cannot know which
    one you meant — it says so, with the count."""
    # Group(...) constructs without registering; group(...) would register
    # and the module would no longer be "no tasks at all".
    (tmp_path / "two_groups.py").write_text(
        "from livery.footman.registry import Group\n\na = Group('a')\nb = Group('b')\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "two_groups", raising=False)
    with pytest.raises(RegistrationError, match="2 Groups"):
        compose.include("two_groups")


def test_include_accepts_a_group_directly(tmp_path):
    """A Group object you already hold lands under its own name."""
    donor = Group("donor")

    @donor.task
    def ship():
        "Ship."

    root = Group("root")
    compose.include(donor, into=root)
    assert "ship" in root.groups["donor"].tasks


def test_plugin_claimed_by_two_distributions_teaches(monkeypatch):
    """Two dists advertising the same plugin name is ambiguous — the error
    names both so the user can uninstall one."""

    class FakeEP:
        def __init__(self, dist):
            self.name = "twice"
            self.dist = dist
            self.group = compose.ENTRY_POINT_GROUP

    import importlib.metadata

    monkeypatch.setattr(
        importlib.metadata,
        "entry_points",
        lambda **kw: [FakeEP("alpha 1.0"), FakeEP("beta 2.0")],
    )
    monkeypatch.setattr(compose, "_module_trees", {})
    with pytest.raises(RegistrationError, match=r"more than one distribution"):
        compose.plugin("twice")


# --- _fork carries every Group field (F: default_task/hooks were dropped) -----


def test_fork_copies_every_group_field():
    # A structural census: _fork must carry EVERY Group field, or a composed
    # group silently loses it — which is exactly how `@group.default` and
    # lifecycle hooks vanished across include(). This fails the moment a field
    # is added to Group.__init__ without teaching _fork (and this test) to copy
    # it, so a new field can't be dropped in silence.
    # `default_task` is no longer a field: the default is the child task
    # named `default`, so it rides the tasks-dict copy and cannot desync.
    assert set(vars(Group("x"))) == {
        "name",
        "help",
        "hidden",
        "expose",
        "tasks",
        "groups",
        "contributions",
        "mounted_from",
    }
    # Hook kinds live inside `contributions`, one bucket per declared kind —
    # _fork copies the dict generically, so a new kind never touches it.
    assert set(Group("x").contributions) == set(registry.CONTRIBUTION_KINDS)


def test_fork_preserves_default_and_hooks():
    src = Group("release", "Release tasks")

    @src.task
    def notes(): ...

    @src.default
    def run(*, armed: bool = False): ...

    def sentinel(tasks): ...

    src.contributions["pre_tasks"].append(sentinel)

    fork = compose._fork(src)
    assert fork.default_task is src.default_task  # shared fn, like the task fns
    assert fork.contributions["pre_tasks"] == [sentinel]
    # fresh buckets — no memo leak
    assert fork.contributions["pre_tasks"] is not src.contributions["pre_tasks"]


@pytest.fixture
def default_provider(tmp_path, monkeypatch):
    """A provider module whose group carries a `@group.default`."""
    (tmp_path / "reltasks.py").write_text(
        textwrap.dedent(
            """
            from livery.footman import group

            release = group("release", help="Release tasks")

            @release.default
            def run(*, armed: bool = False):
                "Cut a release."
                print(f"release armed={armed}")

            @release.task
            def notes():
                "Show the notes."
                print("notes")
            """
        )
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(compose, "_module_trees", {})
    sys.modules.pop("reltasks", None)
    yield tmp_path
    sys.modules.pop("reltasks", None)


def test_include_preserves_group_default(default_provider):
    # F: include() grafted the subtasks but dropped the group's @group.default,
    # so the bare-runnable group broke and its options vanished from the manifest.
    with registry.capture() as captured:
        compose.include("reltasks")
    assert captured.groups["release"].default_task is not None
    tree = _manifest.build_manifest(captured)["tree"]
    node = tree["groups"]["release"]
    assert "default" in node  # the runnable-group node the splitter/help read
    assert [p["name"] for p in node["default"]["params"]] == ["armed"]


def test_included_group_default_runs_end_to_end(default_provider, tmp_path):
    project = tmp_path / "proj_default"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname="x"\n')
    (project / "tasks.py").write_text(
        "from livery.footman import include\ninclude('reltasks')\n"
    )
    result = Runner().invoke("release --armed", cwd=project)
    assert result.ok, result.stderr
    assert "release armed=True" in result.stdout


def test_include_runs_provider_hooks(tmp_path, monkeypatch):
    # A provider's @pre_tasks hook edits the whole tree; include() must surface it
    # on the live root so discovery collects and runs it — it was dropped before.
    (tmp_path / "finmod.py").write_text(
        textwrap.dedent(
            """
            from livery.footman import task, pre_tasks

            @task
            def build():
                "Build it."
                print("build")

            @pre_tasks
            def note(inv):
                inv.tasks["build"].disable("the hook ran")
            """
        )
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(compose, "_module_trees", {})
    sys.modules.pop("finmod", None)

    project = tmp_path / "proj_fin"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname="x"\n')
    (project / "tasks.py").write_text(
        "from livery.footman import include\ninclude('finmod')\n"
    )
    listing = Runner().invoke("--list", cwd=project)
    assert listing.ok
    assert "the hook ran" in listing.stdout  # the hook ran and disabled the task


def test_include_of_a_hooks_only_module_is_a_valid_pull(tmp_path, monkeypatch):
    # A lifecycle-only provider — hooks, not a single task — is a valid mount.
    # Before the relaxation this include() refused with "no module-level
    # Group"; the module's whole contribution is what its hooks do.
    (tmp_path / "hooks_only.py").write_text(
        textwrap.dedent(
            """
            from livery.footman import pre_tasks

            @pre_tasks
            def gate(inv):
                inv.tasks["deploy"].disable("gated by hooks_only")
            """
        )
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(compose, "_module_trees", {})
    sys.modules.pop("hooks_only", None)

    project = tmp_path / "proj_hooks_only"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname="x"\n')
    (project / "tasks.py").write_text(
        "from livery.footman import task, include\n"
        "@task\ndef deploy(): ...\n"
        "include('hooks_only')\n"
    )
    listing = Runner().invoke("--list", cwd=project)
    assert listing.ok, listing.stderr
    assert "gated by hooks_only" in listing.stdout  # the provider's hook ran


def test_plugin_of_a_hooks_only_provider_is_a_valid_pull(tmp_path, monkeypatch):
    # The entry-point doorway accepts a lifecycle-only provider too, and the
    # mount lands its contributions on the live root, not the grafted subtree.
    _advertise(
        tmp_path,
        monkeypatch,
        "hooks_only_plugin",
        """
        from livery.footman import pre_tasks

        @pre_tasks
        def gate(inv): ...
        """,
        "hooksonly = hooks_only_plugin",
    )
    with registry.capture() as captured:
        compose.plugin("hooksonly")
    assert captured.contributions["pre_tasks"]
    assert not captured.tasks and not captured.groups
    # A second mount re-forks the memoised tree: the graft must drain the
    # fork's buckets, never the memo's, or the hook arrives only once.
    with registry.capture() as again:
        compose.plugin("hooksonly")
    assert again.contributions["pre_tasks"]


def test_plugin_entry_point_of_the_wrong_type_teaches(monkeypatch):
    """An entry point resolving to something that isn't a Group (or a
    module of tasks) names the type it got."""

    class FakeEP:
        name = "wrong"
        dist = "wrong 1.0"
        group = compose.ENTRY_POINT_GROUP

        def load(self):
            return 42

    import importlib.metadata

    monkeypatch.setattr(importlib.metadata, "entry_points", lambda **kw: [FakeEP()])
    monkeypatch.setattr(compose, "_module_trees", {})
    with pytest.raises(RegistrationError, match="got int"):
        compose.plugin("wrong")


# --- the two typed verbs over one engine ----------------------------------------


def test_include_subpath_pulls_one_group_out_of_a_module(provider):
    # include("shared_tasks.docs"): the longest importable prefix is the
    # module; the remainder walks the captured tree. The node lands under
    # its own name.
    with registry.capture() as captured:
        compose.include("shared_tasks.docs")
    assert set(captured.groups) == {"docs"}
    assert "build" in captured.groups["docs"].tasks
    assert "lint" not in captured.tasks  # siblings stayed home


def test_plugin_subpath_walks_the_advertised_tree(provider, tmp_path):
    project = tmp_path / "proj_sub"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname="x"\n')
    (project / "tasks.py").write_text(
        "from livery.footman import plugin\nplugin('shared.docs')\n"
    )
    result = Runner().invoke("docs.build", cwd=project)
    assert result.ok and "docs-build" in result.stdout


def test_pull_a_single_task_adopts_it(provider):
    # The subpath can land on a task: plugin("acme.linters.default",
    # into="lint") is the adopt-a-provider's-default one-liner. Module flavour:
    with registry.capture() as captured:
        compose.include("shared_tasks.docs.build", into="site")
    assert "build" in captured.groups["site"].tasks
    with pytest.raises(RegistrationError, match=r"mount it bare"):
        compose.include("shared_tasks.fmt", only=["x"])


def test_filters_are_relative_to_the_pulled_node(provider):
    with registry.capture() as captured:
        compose.include("shared_tasks.docs", only=["build"])
    assert set(captured.groups["docs"].tasks) == {"build"}


def test_into_naming_a_task_is_a_type_error(provider):
    with registry.capture():

        @registry.task
        def site(): ...

        with pytest.raises(RegistrationError, match=r"'site' is a task — into="):
            compose.include("shared_tasks.docs", into="site")


def test_into_default_is_the_group_typed_default_type_error(provider):
    with (
        registry.capture(),
        pytest.raises(RegistrationError, match=r"'default' is a task"),
    ):
        compose.include("shared_tasks.docs", into="lint.default")


def test_provenance_is_stamped_and_reported(provider, tmp_path):
    project = tmp_path / "proj_prov"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname="x"\n')
    (project / "tasks.py").write_text(
        "from livery.footman import plugin\nplugin('shared', into='vendor')\n"
    )
    result = Runner().invoke("--plugins", cwd=project)
    assert result.ok
    assert "shared" in result.stdout
    assert "mounted at vendor" in result.stdout


def test_unmounted_plugin_shows_state_and_the_dist_header_describes(provider, tmp_path):
    # The Summary describes the *package*, so it prints once on the
    # distribution's header line — never repeated identically beside every
    # entry the package ships. An unmounted entry shows its state alone:
    # importing unmounted code could crash a listing.
    project = tmp_path / "proj_unpulled"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname="x"\n')
    (project / "tasks.py").write_text(
        "from livery.footman import task\n@task\ndef t(): ...\n"
    )
    result = Runner().invoke("--plugins", cwd=project)
    assert result.ok
    assert "shared" in result.stdout
    assert "(not mounted)" in result.stdout
    assert 'plugin("' in result.stdout  # the listing ends with the move to make
    summary = "A task runner with typed commands"
    assert result.stdout.count(summary) == 1  # once, on footman's header


def test_two_pulls_compose_one_subtree(provider, tmp_path, monkeypatch):
    # Two mounts into one target compose all the way down — group-vs-group is
    # never a clash, only a same-address leaf is.
    _advertise(
        tmp_path,
        monkeypatch,
        "other_kit",
        """
        from livery.footman import group

        docs = group("docs", help="Other docs")

        @docs.task
        def deploy():
            "Deploy docs."
        """,
        "other = other_kit",
    )
    with registry.capture() as captured:
        compose.include("shared_tasks")  # brings docs.build
        compose.plugin("other")  # brings docs.deploy — composes, no clash
    assert set(captured.groups["docs"].tasks) == {"build", "serve", "deploy"}


def test_leaf_clash_across_identities_cites_both(provider, tmp_path, monkeypatch):
    _advertise(
        tmp_path,
        monkeypatch,
        "rival_kit",
        """
        from livery.footman import group

        docs = group("docs")

        @docs.task
        def build():
            "Rival build."
        """,
        "rival = rival_kit",
    )
    with registry.capture():
        compose.include("shared_tasks")
        with pytest.raises(RegistrationError) as excinfo:
            compose.plugin("rival")
    message = str(excinfo.value)
    assert "'docs.build' claimed by both" in message
    assert "shared_tasks" in message and "rival" in message


def test_module_docstring_becomes_container_help(tmp_path, monkeypatch):
    (tmp_path / "documented_kit.py").write_text(
        '"""A documented kit of tasks.\n\nMore prose.\n"""\n'
        "from livery.footman import task\n\n@task\ndef go(): ...\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(compose, "_module_trees", {})
    sys.modules.pop("documented_kit", None)
    try:
        with registry.capture():
            compose.include("documented_kit", into="kit")
        tree = compose._module_trees["documented_kit"]
        assert tree.help == "A documented kit of tasks."
    finally:
        sys.modules.pop("documented_kit", None)


def test_adopted_default_fans_out_the_group_it_landed_in(tmp_path, monkeypatch):
    # Default-ness is parent-relative: a provider's empty-body default mounted
    # into a consumer group fans out the group it LANDED in, not the one it
    # was declared on — the fn is shared, so the declaration stamp cannot
    # know where the mount placed it.
    _advertise(
        tmp_path,
        monkeypatch,
        "default_kit",
        """
        from livery.footman import group
        from livery.footman.params import Forward

        linters = group("linters")

        @linters.task
        def never():
            print("provider-side surface, must NOT run")

        @linters.default
        def all_of_them(fix: Forward[bool] = False):
            "Run every linter."
        """,
        "defaultkit = default_kit:linters",
    )
    project = tmp_path / "proj_adopt"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname="x"\n')
    (project / "tasks.py").write_text(
        "from livery.footman import group, plugin\n"
        "lint = group('lint')\n"
        "@lint.task\n"
        "def markdown(fix: bool = False):\n"
        "    print(f'markdown fix={fix}')\n"
        "plugin('defaultkit.default', into='lint')\n"
    )
    result = Runner().invoke("lint --fix", cwd=project)
    assert result.ok
    assert "markdown fix=True" in result.stdout  # the CONSUMER group fanned out
    assert "provider-side surface" not in result.stdout


def test_default_survives_only_if_the_default_survives(tmp_path, monkeypatch):
    # Literal, because the default IS the child named `default`: cherry-pick
    # a sibling and the graft is a default-less group; pick the whole group
    # and it stays runnable; and both node-granular spellings work without
    # ever opening the provider's source.
    _advertise(
        tmp_path,
        monkeypatch,
        "runnable_kit",
        """
        from livery.footman import group
        from livery.footman.params import Forward

        lint = group("lint", help="Lint things")

        @lint.task
        def python(fix: bool = False):
            "Lint Python."

        @lint.default
        def lint_all(fix: Forward[bool] = False):
            "Lint everything."
        """,
        "runkit = runnable_kit:lint",
    )
    with registry.capture() as captured:
        compose.plugin("runkit", only=["python"])
    assert captured.groups["lint"].default_task is None  # not resurrected
    assert set(captured.groups["lint"].tasks) == {"python"}

    with registry.capture() as captured:
        compose.plugin("runkit")  # the whole group keeps its default
    assert captured.groups["lint"].default_task is not None

    with registry.capture() as captured:
        compose.plugin("runkit", only=["default"])  # just the default
    assert set(captured.groups["lint"].tasks) == {"default"}
    assert captured.groups["lint"].default_task is not None

    with registry.capture() as captured:
        compose.plugin("runkit", exclude=["default"])  # everything but it
    assert captured.groups["lint"].default_task is None
    assert set(captured.groups["lint"].tasks) == {"python"}


def test_local_group_adopts_a_pulled_one(provider):
    # A local group() over a *mounted* group adopts it — claiming the name
    # means adding to it, exactly what mounting after the definition
    # produces. Either order: the union, local wins per leaf.
    with registry.capture() as captured:
        compose.include("shared_tasks", only=["docs"])  # mount first

        docs = registry.group("docs", help="Mine now")

        @docs.task
        def publish(): ...

        got = captured.groups["docs"]
        assert set(got.tasks) == {"build", "serve", "publish"}
        assert got.help == "Mine now"  # the local definition names it

    with registry.capture() as captured:  # the mirror order, same tree
        docs = registry.group("docs")

        @docs.task
        def publish(): ...

        compose.include("shared_tasks", only=["docs"])
        assert set(captured.groups["docs"].tasks) == {"build", "serve", "publish"}


def test_adopted_group_still_shadows_pulled_leaves(provider):
    with registry.capture() as captured:
        compose.include("shared_tasks", only=["docs"])
        docs = registry.group("docs")

        @docs.task
        def build(): ...  # the same name as a mounted leaf: local wins

        got = captured.groups["docs"]
        assert got.tasks["build"] is build
        assert "serve" in got.tasks  # the rest of the mount survives
