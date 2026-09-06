"""Runnable groups: `@group.default` makes `fm <group>` run an action."""

from __future__ import annotations

from typing import Any

import pytest

from livery.footman import _describe, _manifest, registry
from livery.footman._complete import complete
from livery.footman._executor import run_chain
from livery.footman._split import ChainError, split_chain
from livery.footman.params import Forward
from livery.footman.registry import (
    Group,
    RegistrationError,
    is_atomic,
    is_interactive,
    keeps_going,
    pre_deps,
    task_confirm,
)


def drive(build, line):
    reg = Group("root")
    build(reg)
    tree = _manifest.build_manifest(reg)["tree"]
    _, segments = split_chain(tree, line.split())
    run_chain(reg, segments)
    return [s.task for s in segments]


def _lint(reg):
    seen = reg._seen = {}
    lint = reg.group("lint")

    @lint.task
    def markdown(fix: bool = False):
        seen["markdown"] = fix

    @lint.default
    def lint_all(fix: Forward[bool] = False):
        seen["default"] = fix

    return seen


def test_bare_group_runs_its_default():
    reg = Group("root")
    seen = _lint(reg)
    tree = _manifest.build_manifest(reg)["tree"]
    _, segs = split_chain(tree, ["lint"])
    run_chain(reg, segs)
    assert seen == {"default": False}
    assert [s.task for s in segs] == ["lint"]


def test_group_flag_reaches_the_default():
    reg = Group("root")
    seen = _lint(reg)
    tree = _manifest.build_manifest(reg)["tree"]
    _, segs = split_chain(tree, ["lint", "--fix"])
    run_chain(reg, segs)
    assert seen == {"default": True}


def test_targeting_a_child_runs_the_child_not_the_default():
    reg = Group("root")
    seen = _lint(reg)
    tree = _manifest.build_manifest(reg)["tree"]
    _, segs = split_chain(tree, ["lint.markdown", "--fix"])
    run_chain(reg, segs)
    assert seen == {"markdown": True}  # the default never ran
    assert [s.task for s in segs] == ["lint.markdown"]


def test_a_trailing_target_opens_a_new_segment_after_the_default():
    ran = []

    def tasks(reg):
        lint = reg.group("lint")

        @lint.default
        def lint_all(fix: Forward[bool] = False):
            ran.append("lint")

        @reg.task
        def test():
            ran.append("test")

    segs = drive(tasks, "lint test")
    assert segs == ["lint", "test"]
    assert ran == ["lint", "test"]


def test_a_group_without_a_default_is_still_a_taught_error():
    def tasks(reg):
        plain = reg.group("plain")

        @plain.task
        def sub(): ...

    with pytest.raises(ChainError, match=r"is a group, not a task"):
        drive(tasks, "plain")


# --- empty-body fan-out + forward threading ----------------------------------


def _surfaces(reg):
    seen: dict[str, object] = {}
    lint = reg.group("lint")

    @lint.task
    def python(fix: bool = False):
        seen["python"] = fix

    @lint.task
    def markdown(fix: bool = False):
        seen["markdown"] = fix

    @lint.task
    def spelling():  # no fix parameter
        seen["spelling"] = "ran"

    @lint.default
    def lint_all(fix: Forward[bool] = False):  # empty body -> fan out
        """Lint everything."""

    return seen


def test_empty_body_default_fans_out_the_groups_tasks():
    reg = Group("root")
    seen = _surfaces(reg)
    tree = _manifest.build_manifest(reg)["tree"]
    _, segs = split_chain(tree, ["lint"])
    run_chain(reg, segs)
    assert seen == {"python": False, "markdown": False, "spelling": "ran"}


def test_fan_out_threads_the_flag_only_to_surfaces_that_declare_it():
    reg = Group("root")
    seen = _surfaces(reg)
    tree = _manifest.build_manifest(reg)["tree"]
    _, segs = split_chain(tree, ["lint", "--fix"])
    run_chain(reg, segs)
    # fix reaches python/markdown; spelling has no such parameter and just runs.
    assert seen == {"python": True, "markdown": True, "spelling": "ran"}


def test_an_ellipsis_body_default_fans_out_like_pass():
    # `...` is the stub idiom the docs themselves teach, and it used to
    # count as a real body — a `@group.default` written `def all(): ...`
    # silently ran nothing (audit M23). The three stub spellings —
    # docstring, `pass`, `...` — read as empty alike now.
    ran = []

    def tasks(reg):
        lint = reg.group("lint")

        @lint.task
        def python():
            ran.append("python")

        @lint.default
        def lint_all(): ...

    reg = Group("root")
    tasks(reg)
    tree = _manifest.build_manifest(reg)["tree"]
    _, segs = split_chain(tree, ["lint"])
    run_chain(reg, segs)
    assert ran == ["python"]  # fanned out — not a silent nothing


def test_a_custom_body_default_does_not_auto_fan_out():
    ran = []

    def tasks(reg):
        lint = reg.group("lint")

        @lint.task
        def python(fix: bool = False):
            ran.append("python")

        @lint.default
        def lint_all(fix: Forward[bool] = False):
            ran.append("custom")  # a real body is the escape hatch

    drive(tasks, "lint")
    assert ran == ["custom"]  # the surfaces did not run implicitly


def test_forward_chains_through_a_group_used_as_a_prerequisite():
    # `check` forwards --fix into the `lint` group (a pre= target); lint's
    # default re-forwards it to the surfaces that declare it. Declarative check.
    seen: dict[str, object] = {}

    def tasks(reg):
        lint = reg.group("lint")

        @lint.task
        def python(fix: bool = False):
            seen["python"] = fix

        @lint.task
        def spelling():
            seen["spelling"] = "ran"

        @lint.default
        def lint_all(fix: Forward[bool] = False):
            """Lint everything."""

        @reg.task
        def test():
            seen["test"] = "ran"

        @reg.task(pre=[lint, test])
        def check(fix: Forward[bool] = False):
            """Format, lint, test."""

    drive(tasks, "check --fix")
    assert seen == {"python": True, "spelling": "ran", "test": "ran"}


def test_completion_offers_the_default_flags_alongside_children():
    from livery.footman._complete import complete

    reg = Group("root")
    _surfaces(reg)  # lint with python/markdown/spelling + a fix default
    tree = _manifest.build_manifest(reg)["tree"]
    # In-word, the stop-or-descend choice: the group itself plus its dotted
    # children (the common prefix stays `lint`, so no shell forces a space).
    offered = {c.split("\t")[0] for c in complete(tree, ["lin"])}
    assert "lint" in offered
    assert {"lint.python", "lint.markdown", "lint.spelling"} <= offered
    # After the space the default is committed: its flags complete, and the
    # children are no longer reachable in this segment.
    offered = {c.split("\t")[0] for c in complete(tree, ["lint", ""])}
    assert "--fix" in offered  # the default's flag
    assert not {"python", "markdown", "spelling"} & offered
    assert complete(tree, ["lint", "--f"]) == ["--fix"]


# --- @group.default options ---------------------------------------------------


def test_default_takes_task_policy_options():
    reg = Group("root")
    lint = reg.group("lint")

    @lint.default(keep_going=True, atomic=True, confirm="lint everything?")
    def lint_all(fix: Forward[bool] = False):
        """Lint everything."""

    assert keeps_going(lint_all) is True
    assert is_atomic(lint_all) is True
    assert task_confirm(lint_all) == "lint everything?"


def test_default_pre_runs_before_the_default():
    ran = []

    def tasks(reg):
        @reg.task
        def bootstrap():
            ran.append("bootstrap")

        lint = reg.group("lint")

        @lint.default(pre=[bootstrap])
        def lint_all():
            ran.append("lint")

    drive(tasks, "lint")
    assert ran == ["bootstrap", "lint"]


def test_bare_default_still_registers_with_no_options():
    reg = Group("root")
    lint = reg.group("lint")

    @lint.default
    def lint_all(fix: Forward[bool] = False):
        """Lint everything."""

    assert keeps_going(lint_all) is None
    assert pre_deps(lint_all) == []
    # Last: `is` narrows lint_all to default_task's `Task | None` for the
    # rest of the block, so the calls above must come first.
    assert reg.groups["lint"].default_task is lint_all


def test_interactive_on_an_empty_body_default_is_rejected():
    reg = Group("root")
    lint = reg.group("lint")

    @lint.task
    def python(fix: bool = False): ...

    with pytest.raises(RegistrationError, match=r"empty body.*own the terminal"):

        @lint.default(interactive=True)
        def lint_all(fix: Forward[bool] = False):
            """Empty body -> fans out; cannot own the terminal."""


def test_interactive_on_a_custom_body_default_is_allowed():
    reg = Group("root")
    shell = reg.group("shell")

    @shell.default(interactive=True)
    def repl():
        print("would drop into a REPL")

    assert is_interactive(repl) is True


def test_default_takes_positionals():
    # Dotted addressing dissolved the old no-positional rule: a bare word
    # after the group is unambiguously the default's value, because every
    # child keeps its own dotted spelling (`fm lint.markdown`).
    reg = Group("root")
    seen: dict[str, object] = {}
    lint = reg.group("lint")

    @lint.task
    def markdown(fix: bool = False):
        seen["markdown"] = fix

    @lint.default(keep_going=True)
    def lint_all(path: str):
        seen["path"] = path

    tree = _manifest.build_manifest(reg)["tree"]
    _, segs = split_chain(tree, ["lint", "src"])
    run_chain(reg, segs)
    assert seen == {"path": "src"}
    assert segs[0].notes == []  # 'src' names no child: nothing to say


def test_positional_matching_a_child_name_wins_and_notes():
    # Deterministic grammar (the positional wins), never silent: an exact
    # child-name value carries a one-line stderr note with the dotted form.
    reg = Group("root")
    seen: dict[str, object] = {}
    lint = reg.group("lint")

    @lint.task
    def markdown(fix: bool = False):
        seen["markdown"] = fix

    @lint.default
    def lint_all(path: str):
        seen["path"] = path

    tree = _manifest.build_manifest(reg)["tree"]
    _, segs = split_chain(tree, ["lint", "markdown"])
    run_chain(reg, segs)
    assert seen == {"path": "markdown"}  # the value, not the subtask
    (note,) = segs[0].notes
    assert "ran lint's default with 'markdown'" in note
    assert "{prog} lint.markdown" in note


def test_near_miss_of_a_child_name_notes_the_nearest_subtask():
    # `fm lint markdwon` used to be an "unknown task" error; under
    # positional-wins it is a valid parse that would silently filter on a
    # pattern matching nothing — so the note names the nearest subtask.
    reg = Group("root")
    lint = reg.group("lint")

    @lint.task
    def markdown(fix: bool = False): ...

    @lint.default
    def lint_all(path: str): ...

    tree = _manifest.build_manifest(reg)["tree"]

    def notes_for(line):
        _, segs = split_chain(tree, line.split())
        return segs[0].notes

    (note,) = notes_for("lint markdwon")
    assert "'markdwon' ran as lint's positional" in note
    assert "{prog} lint.markdown" in note
    # A path-shaped value is the documented quiet spelling — a legitimate
    # value that happens to equal (or nearly equal) a child name is not
    # nagged on every run forever.
    assert notes_for("lint ./markdown") == []
    assert notes_for("lint markdown/x") == []
    # And a value nothing like any child says nothing.
    assert notes_for("lint src") == []


# --- body-callability: a runnable group is callable from a task body ----------


def test_empty_body_group_is_callable_from_a_body_and_fans_out():
    reg = Group("root")
    seen = _surfaces(reg)  # lint: python/markdown (fix) + spelling (no fix)
    reg.groups["lint"](fix=True)  # the imperative echo of `fm lint --fix`
    # Partial reach, by name: fix reaches the surfaces that declare it; spelling
    # runs bare. Same result as the CLI fan-out, driven from a body.
    assert seen == {"python": True, "markdown": True, "spelling": "ran"}


def test_custom_body_group_call_runs_the_body_only():
    reg = Group("root")
    seen = {}
    lint = reg.group("lint")

    @lint.task
    def markdown(fix: bool = False):
        seen["markdown"] = fix

    @lint.default
    def lint_all(fix: Forward[bool] = False):
        seen["default"] = fix  # a real body is the escape hatch

    lint(fix=True)
    assert seen == {"default": True}  # the body ran; the surface stayed untouched


def test_calling_a_group_without_a_default_is_a_taught_error():
    reg = Group("root")
    docs = reg.group("docs")

    @docs.task
    def build(): ...

    with pytest.raises(TypeError, match=r"not runnable"):
        docs()


def test_a_task_body_runs_a_group_and_forwards_through_the_runner():
    # End-to-end: `check --fix` runs through the scheduler, and check's body
    # calls the lint group, which fans out with the forwarded flag.
    reg = Group("root")
    seen = _surfaces(reg)
    lint = reg.groups["lint"]

    @reg.task
    def check(fix: bool = False):
        lint(fix=fix)
        seen["check"] = fix

    tree = _manifest.build_manifest(reg)["tree"]
    _, segs = split_chain(tree, ["check", "--fix"])
    run_chain(reg, segs)
    assert seen == {
        "python": True,
        "markdown": True,
        "spelling": "ran",
        "check": True,
    }


# --- defaults are listed, described, and noted end-to-end ----------------------


def test_listings_carry_the_default_with_its_docstring():
    from livery.footman import _describe

    reg = Group("root")
    lint = reg.group("lint", help="Lint things")

    @lint.task
    def markdown(fix: bool = False):
        """Lint Markdown."""

    @lint.default
    def lint_all(fix: Forward[bool] = False):
        """Lint everything."""

    tree = _manifest.build_manifest(reg)["tree"]
    rows = dict(_describe.iter_tasks(tree))
    assert rows["lint"] == "Lint everything."  # the bare-group spelling, described
    assert rows["lint.markdown"] == "Lint Markdown."


def test_undocumented_empty_body_default_gets_generated_help():
    from livery.footman import _describe

    reg = Group("root")
    lint = reg.group("lint")

    @lint.task
    def markdown(fix: bool = False): ...

    @lint.default
    def lint_all(fix: Forward[bool] = False):
        pass

    tree = _manifest.build_manifest(reg)["tree"]
    rows = dict(_describe.iter_tasks(tree))
    assert rows["lint"] == "run every task in this group"


def test_undocumented_custom_body_default_gets_generated_help():
    from livery.footman import _describe

    reg = Group("root")
    lint = reg.group("lint")

    @lint.task
    def markdown(fix: bool = False): ...

    @lint.default
    def lint_all(fix: bool = False):
        markdown(fix=fix)

    tree = _manifest.build_manifest(reg)["tree"]
    rows = dict(_describe.iter_tasks(tree))
    assert rows["lint"] == "run this group's default action"


def test_group_help_lists_the_default_row(tmp_path, monkeypatch, capsys):
    from livery.footman import _app, _paths

    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(
        "from footman import group\n"
        "lint = group('lint', help='Lint things')\n"
        "@lint.task\n"
        "def markdown(fix: bool = False):\n"
        '    """Lint Markdown."""\n'
        "@lint.default\n"
        "def lint_all(fix: bool = False):\n"
        '    """Lint everything."""\n'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run(["--help", "lint"]) == 0
    out = capsys.readouterr().out
    assert "usage: fm lint[.<task>]" in out
    assert "Lint everything." in out  # the default's own row, described
    assert "lint.markdown" in out
    # And the flat list shows the bare-group spelling as a runnable row.
    assert _app.run(["--list"]) == 0
    listing = capsys.readouterr().out
    assert "Lint everything." in listing


def test_collision_note_reaches_stderr(tmp_path, monkeypatch, capsys):
    from livery.footman import _app, _paths

    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(
        "from footman import group\n"
        "lint = group('lint')\n"
        "@lint.task\n"
        "def markdown(fix: bool = False):\n"
        "    print('subtask ran')\n"
        "@lint.default\n"
        "def lint_all(path: str):\n"
        "    print(f'default ran on {path}')\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    assert _app.run(["lint", "markdown"]) == 0
    captured = capsys.readouterr()
    assert "default ran on markdown" in captured.out
    assert "subtask ran" not in captured.out
    assert "note: ran lint's default with 'markdown'" in captured.err
    assert "fm lint.markdown" in captured.err  # {prog} substituted


# --- the default is the child named `default` ----------------------------------


def test_default_registers_as_the_child_named_default():
    reg = Group("root")
    lint = reg.group("lint")

    @lint.default
    def lint_all(fix: Forward[bool] = False):
        """Lint everything."""

    # Derived, not stored: default-ness IS the child named `default`.
    assert lint.tasks["default"] is lint_all
    assert lint.default_task is lint_all


def test_the_default_has_a_dotted_address():
    # `@lint.default` ↔ `fm lint.default`: the decorator you wrote is the
    # address you type; bare `fm lint` stays the idiomatic spelling.
    reg = Group("root")
    seen = {}
    lint = reg.group("lint")

    @lint.task
    def markdown(fix: bool = False):
        seen["markdown"] = fix

    @lint.default
    def lint_all(fix: bool = False):
        seen["default"] = fix

    tree = _manifest.build_manifest(reg)["tree"]
    _, segs = split_chain(tree, ["lint.default", "--fix"])
    run_chain(reg, segs)
    assert seen == {"default": True}
    assert segs[0].task == "lint.default"


def test_a_task_named_default_is_the_default():
    # The name is the mechanism — `@group.default` is sugar. A task that
    # comes to be named `default` any other way is the group's default too,
    # through the same validation path.
    reg = Group("root")
    seen = {}
    lint = reg.group("lint")

    @lint.task(name="default")
    def anything(fix: bool = False):
        seen["ran"] = fix

    assert lint.default_task is anything
    tree = _manifest.build_manifest(reg)["tree"]
    _, segs = split_chain(tree, ["lint", "--fix"])
    run_chain(reg, segs)
    assert seen == {"ran": True}


def test_an_empty_task_named_default_fans_out():
    # One code path: an empty body registered under the name gets the
    # fan-out flag exactly as the decorator form does.
    reg = Group("root")
    seen = {}
    lint = reg.group("lint")

    @lint.task
    def markdown(fix: bool = False):
        seen["markdown"] = fix

    @lint.task(name="default")
    def lint_all(fix: Forward[bool] = False):
        pass

    tree = _manifest.build_manifest(reg)["tree"]
    _, segs = split_chain(tree, ["lint", "--fix"])
    run_chain(reg, segs)
    assert seen == {"markdown": True}  # fanned out; never ran itself twice


def test_interactive_empty_task_named_default_is_rejected():
    reg = Group("root")
    lint = reg.group("lint")

    with pytest.raises(RegistrationError, match=r"interactive but has\s+an empty body"):

        @lint.task(name="default", interactive=True)
        def lint_all():
            pass


def test_a_group_named_default_is_illegal():
    reg = Group("root")
    lint = reg.group("lint")
    with pytest.raises(RegistrationError, match=r"cannot be named 'default'"):
        lint.group("default")


def test_two_defaults_collide_loudly():
    reg = Group("root")
    lint = reg.group("lint")

    @lint.default
    def lint_all(): ...

    with pytest.raises(RegistrationError, match=r"already has a task named 'default'"):

        @lint.task(name="default")
        def another(): ...


def test_default_is_listed_and_completes_dotted():
    from livery.footman import _describe
    from livery.footman._complete import complete

    reg = Group("root")
    lint = reg.group("lint")

    @lint.task
    def markdown(fix: bool = False): ...

    @lint.default
    def lint_all(fix: Forward[bool] = False):
        """Lint everything."""

    tree = _manifest.build_manifest(reg)["tree"]
    rows = dict(_describe.iter_tasks(tree))
    assert rows["lint.default"] == "Lint everything."
    offered = {c.split("\t")[0] for c in complete(tree, ["lint."])}
    assert "lint.default" in offered


def test_a_listing_shows_one_row_per_action():
    # `lint` and `lint.default` run the same thing, so the flat listing shows
    # one row — the bare group, described by its default. The raw walk keeps
    # the address (the did-you-mean index owes an answer for every typeable
    # spelling), so this is the listings' dedupe mode, not a removal.
    from livery.footman import _describe

    reg = Group("root")
    lint = reg.group("lint")

    @lint.task
    def python(): ...

    @lint.default
    def everything():
        """Run every linter."""

    tree = _manifest.build_manifest(reg)["tree"]
    listed = [a for a, _ in _describe.iter_tasks(tree, dedupe_defaults=True)]
    assert listed == ["lint", "lint.python"]
    index = [a for a, _ in _describe.iter_tasks(tree, show_hidden=True)]
    assert "lint.default" in index  # the typo index keeps the spelling


def test_the_default_is_listed_first_however_late_it_was_declared():
    # The default *is* the group — `fm db` runs it and the group's own row is
    # described by it — so where the author happened to write it must not
    # decide where a listing shows it.
    from livery.footman import _describe

    reg = Group("root")
    db = reg.group("db", help="Database")

    @db.task
    def migrate(): ...

    @db.task
    def seed(): ...

    @db.default
    def status(): ...  # declared last, on purpose

    tree = _manifest.build_manifest(reg)["tree"]
    names = [address for address, _help in _describe.iter_tasks(tree)]
    assert names == ["db", "db.default", "db.migrate", "db.seed"]

    # `--sort` orders by name, and the default still leads its group.
    sorted_names = [
        address for address, _ in _describe.iter_tasks(_describe.sort_tree(tree))
    ]
    assert sorted_names == ["db", "db.default", "db.migrate", "db.seed"]


def test_a_group_without_a_default_is_untouched():
    from livery.footman import _describe

    reg = Group("root")
    db = reg.group("db", help="Database")

    @db.task
    def migrate(): ...

    @db.task
    def alpha(): ...

    tree = _manifest.build_manifest(reg)["tree"]
    names = [address for address, _ in _describe.iter_tasks(tree)]
    assert names == ["db.migrate", "db.alpha"]  # declaration order, as before


def test_a_runnable_group_completes_itself_once():
    """`lint` and `lint.default` are one action wearing two addresses. The
    listings deduped it; completion kept offering the pair, so a TAB at the
    top level showed the same action twice in different words."""
    from livery.footman._complete import complete

    reg = Group("root")
    lint = reg.group("lint")

    @lint.task
    def markdown(fix: bool = False): ...

    @lint.default
    def lint_all():
        """Lint everything."""

    tree = _manifest.build_manifest(reg)["tree"]
    offered = {c.split("\t")[0] for c in complete(tree, [""])}
    assert {"lint", "lint.markdown"} <= offered
    assert "lint.default" not in offered
    # Descending is a different question: at `lint.` the bare row is off the
    # screen, so `lint.default` is the only spelling of that action left.
    assert "lint.default" in {c.split("\t")[0] for c in complete(tree, ["lint."])}


# --- a runnable group's bare name is its default's other spelling -------------


def _outside(build) -> dict[str, Any]:
    """A sealed built-in tree as seen from a directory with no project."""
    reg = registry.Group("root")
    build(reg)
    registry.seal_expose(reg)
    tree: dict[str, Any] = _manifest.build_manifest(reg, project=False)["tree"]
    return tree


def _runnable_group(reg):
    lint = reg.group("lint")

    @lint.default
    def lint_all():
        """Lint everything."""

    @lint.task
    def python():
        """Lint python."""


def test_a_runnable_groups_bare_name_needs_a_project_too():
    """`fm lint` and `fm lint.default` are one action with two spellings, and
    they answered opposite ways: the explicit one refused while the bare one
    ran — printing the very fiction the feature exists to end. Sealing was
    never the bug; the group *node* had no answer to read."""
    tree = _outside(_runnable_group)
    group = tree["groups"]["lint"]
    assert group["unexposed"] is True
    assert group["default"]["unexposed"] is True
    assert group["tasks"]["default"]["unexposed"] is True


def test_both_spellings_refuse_in_the_same_words():
    tree = _outside(_runnable_group)
    messages = []
    for line in (["lint"], ["lint.default"]):
        with pytest.raises(ChainError) as excinfo:
            split_chain(tree, line)
        messages.append(str(excinfo.value))
    assert messages[0].startswith("lint needs a project")
    assert messages[1].startswith("lint.default needs a project")


def test_a_runnable_group_that_needs_a_project_is_not_listed_or_completed():
    tree = _outside(_runnable_group)
    assert not _describe.listed(tree["groups"]["lint"])
    assert "lint" not in {c.split("\t", 1)[0] for c in complete(tree, [""])}


def test_a_defaults_answer_speaks_for_the_default_not_the_subtree():
    """Derived, not inherited: the group's node takes its answer from the
    default so the bare address can be refused, but a sibling task keeps its
    own — `lint.version` is still reachable when `fm lint` is not."""

    def build(reg):
        lint = reg.group("lint")

        @lint.default(expose="project_only")
        def lint_all(): ...

        @lint.task(expose="always")
        def version(): ...

    tree = _manifest.build_manifest(_registered(build), project=False)["tree"]
    group = tree["groups"]["lint"]
    assert group["unexposed"] is True
    assert "unexposed" not in group["tasks"]["version"]
    assert "lint.version" in {c.split("\t", 1)[0] for c in complete(tree, ["lint."])}


def _registered(build) -> registry.Group:
    reg = registry.Group("root")
    build(reg)
    return reg


def test_group_factory_matches_group_group():
    """`GroupFactory` is documented as "the static shape of `Group.group`", so
    a parameter on one and not the other makes the module-level `group(...)`
    type-check differently from `parent.group(...)`. `expose` did
    exactly that: runtime fine, typed consumers blocked."""
    import inspect

    def shape(sig):
        return [
            (p.name, p.kind, p.default)
            for p in sig.parameters.values()
            if p.name != "self"
        ]

    assert shape(inspect.signature(registry.GroupFactory.__call__)) == shape(
        inspect.signature(registry.Group.group)
    )
