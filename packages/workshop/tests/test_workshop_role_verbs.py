"""The role verbs: a verb per role and a sub-task per check, from the registry.

Refusals first: a flag no verb offers, a name that is no task address,
two checks at one address. Then the tree the registry generates, the
flags each verb offers, the addresses an existing verb serves, and the
generation run again after the registry changed.
"""

from __future__ import annotations

import inspect
import re
import typing
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from livery.footman.api import Group
from livery.workshop import _checks, _quality
from livery.workshop._checks import (
    CheckRecord,
    GateContext,
    check_for,
    generate_verbs,
    option_problems,
    register_check,
    roles,
    unregister_check,
    verb_tree,
)
from livery.workshop._extensions import SELF
from livery.workshop._packages import discover_packages
from workshop_python_checks import python_checks_fixture  # noqa: F401

_FAILURES = (BaseException,)


@pytest.fixture
def registry() -> Iterator[None]:
    """Restore the check registry a test registers into."""
    state = _checks.snapshot()
    yield
    _checks.restore(state)


@pytest.fixture
def empty(registry: None) -> None:
    """An empty check registry, so a generated tree holds the test's checks alone."""
    del registry
    _checks.restore(({}, {}))


def _idle(ctx: GateContext) -> None:
    del ctx


def _flags(group: Group, key: str) -> set[str]:
    """The keyword flags the verb at *key* in *group* offers, each typed.

    The annotations resolve in the verb's module the way footman reads
    them: a flag whose type does not resolve reaches the verb as text.
    """
    task = group.tasks[key]
    parameters = inspect.signature(task).parameters.values()
    assert any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in parameters)
    hints = typing.get_type_hints(inspect.unwrap(task))
    flags = {p.name for p in parameters if p.kind is inspect.Parameter.KEYWORD_ONLY}
    for flag in flags:
        assert hints[flag] is (str if flag == "point" else bool), flag
    return flags


# The refusals first.


def test_a_flag_no_verb_offers_refuses(registry: None) -> None:
    with pytest.raises(
        _FAILURES,
        match=re.escape(
            "check 'lint.odd' reads verbose, which no verb offers; the flags are point"
        ),
    ):
        register_check(CheckRecord("odd", "lint", _idle, flags=("verbose",)))


def test_a_name_that_is_no_task_address_refuses(registry: None) -> None:
    for record in (
        CheckRecord("odd", "lint.deep", _idle),
        CheckRecord("odd.one", "lint", _idle),
        CheckRecord("", "lint", _idle),
        CheckRecord("odd", "lint", _idle, roles=("style.deep",)),
    ):
        with pytest.raises(_FAILURES, match="a role and a tool are one word each"):
            register_check(record)
    with pytest.raises(
        _FAILURES, match=re.escape("lint.default is the address of the lint verb")
    ):
        register_check(CheckRecord("default", "lint", _idle))


def test_two_checks_at_one_address_refuse(registry: None) -> None:
    register_check(CheckRecord("acme", "lint", _idle, roles=("style",)))
    with pytest.raises(
        _FAILURES,
        match=re.escape(
            "check 'style.acme' answers to style.acme, which the check"
            " 'lint.acme' answers to already: one address runs one check"
        ),
    ):
        register_check(CheckRecord("acme", "style", _idle))
    with pytest.raises(
        _FAILURES,
        match=re.escape(
            "check 'format.acme' answers to style.acme, which the check"
            " 'lint.acme' answers to already"
        ),
    ):
        register_check(CheckRecord("acme", "format", _idle, roles=("style",)))
    # Registering the same name again replaces the record, roles and all.
    register_check(CheckRecord("acme", "lint", _idle, roles=("style",)))


def test_a_further_roles_table_reaches_the_check(
    tmp_path: Path, registry: None
) -> None:
    from livery.workshop._checks import check_for, option_value

    register_check(CheckRecord("acme", "lint", _idle, roles=("style",)))
    member = tmp_path / "packages" / "x"
    member.mkdir(parents=True)
    member.joinpath("workshop.toml").write_text(
        'kind = "python"\nname = "livery-x"\n[checks.acme.style]\nenabled = false\n'
    )
    member.joinpath("pyproject.toml").write_text('[project]\nname = "livery-x"\n')
    packages = discover_packages(tmp_path)
    assert option_problems(packages) == []
    assert option_value(check_for("lint.acme"), packages[0], "enabled") is False


def test_a_verb_refuses_both_fix_modes_and_a_fix_inside_ci(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(_FAILURES, match="Pass one"):
        _quality.run_checks(("layering.imports",), fix=True, safe_fix=True)
    monkeypatch.setenv("CI", "true")
    with pytest.raises(_FAILURES, match="a fix inside CI"):
        _quality.run_checks(("layering.imports",), safe_fix=True)


def test_a_path_that_names_nothing_refuses_the_passthrough_spelling_included(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Checking nothing there would pass: a typo, or pytest's own
    # arguments after --, which a verb receives as paths.
    (tmp_path / "workshop.toml").write_text("")
    (tmp_path / "tasks.py").write_text("x = 1\n")
    monkeypatch.setattr(_quality, "workspace_root", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CI", raising=False)
    with pytest.raises(
        _FAILURES,
        match=r"not a file or directory in the workspace: -k, slow\. Name files or"
        r" directories; the checks pass nothing through to a tool\.",
    ):
        _quality.run_checks(("test.ctest",), ("-k", "slow"))
    outside = str(tmp_path.parent)
    with pytest.raises(
        _FAILURES, match=re.escape(f"in the workspace: gone.py, {outside}.")
    ):
        _quality.check("tasks.py", "gone.py", outside)
    # The post-edit hook names files an edit may already have deleted.
    _quality.fix_files(("gone.py",))


# A further role: the check answers under it, and leaves with it.


def test_a_further_role_answers_and_leaves_with_its_check(registry: None) -> None:
    register_check(CheckRecord("acme", "lint", _idle, roles=("style",)))
    record = check_for("lint.acme")
    assert check_for("style.acme") is record
    assert "style" in roles()
    assert verb_tree()["style"] == {"acme": record}
    unregister_check("lint.acme")
    assert "style" not in roles()
    with pytest.raises(
        _FAILURES, match=re.escape("'style.acme' is not a registered check")
    ):
        check_for("style.acme")


# The tree the registry generates.


def _probes() -> None:
    register_check(CheckRecord("alpha", "probe", _idle, fix=_idle))
    register_check(CheckRecord("beta", "probe", _idle, flags=("point",)))
    register_check(CheckRecord("gamma", "quiet", _idle))


def test_each_role_is_a_verb_and_each_check_a_sub_task_with_its_own_flags(
    empty: None,
) -> None:
    _probes()
    root = Group("root")
    generate_verbs(root)
    assert set(root.groups) == {"probe", "quiet"}
    probe, quiet = root.groups["probe"], root.groups["quiet"]
    assert set(probe.tasks) == {"default", "alpha", "beta"}
    assert set(quiet.tasks) == {"default", "gamma"}
    # The role's verb offers what any of its checks reads; each check's
    # sub-task offers what that check reads and nothing more.
    assert _flags(probe, "default") == {"fix", "safe_fix", "point"}
    assert _flags(probe, "alpha") == {"fix", "safe_fix"}
    assert _flags(probe, "beta") == {"point"}
    assert _flags(quiet, "default") == set()
    assert _flags(quiet, "gamma") == set()


def test_a_verb_runs_its_checks_through_the_walk(
    empty: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[tuple[str, ...], tuple[str, ...], dict[str, object]]] = []

    def run_checks(
        names: tuple[str, ...], paths: tuple[str, ...] = (), **flags: object
    ) -> None:
        calls.append((names, paths, flags))

    monkeypatch.setattr(_quality, "run_checks", run_checks)
    _probes()
    root = Group("root")
    generate_verbs(root)
    probe = root.groups["probe"]

    def body(key: str) -> Callable[..., None]:
        verb: Callable[..., None] = inspect.unwrap(probe.tasks[key])
        return verb

    body("default")("a.py", fix=True)
    body("beta")(point="nightly")
    assert calls == [
        (
            ("probe.alpha", "probe.beta"),
            ("a.py",),
            {"fix": True, "safe_fix": False, "point": ""},
        ),
        (("probe.beta",), (), {"fix": False, "safe_fix": False, "point": "nightly"}),
    ]
    # The role's verb reads its checks when it runs, so a check
    # registered after the generation runs under it too.
    register_check(CheckRecord("delta", "probe", _idle))
    calls.clear()
    body("default")()
    assert calls[0][0] == ("probe.alpha", "probe.beta", "probe.delta")


def test_an_address_an_existing_verb_holds_is_served_by_it(empty: None) -> None:
    _probes()
    register_check(CheckRecord("check", "held", _idle))
    register_check(CheckRecord("new", "held", _idle))
    register_check(CheckRecord("check", "owned", _idle))
    root = Group("root")

    def own() -> None:
        """A verb of the root's own at a check's address."""

    def owned() -> None:
        """A verb of the root's own at a role's address."""

    held = root.group("held", help="A group of another verb's")
    held.task(name="check")(own)
    root.task(name="owned")(owned)
    generate_verbs(root)
    # The group keeps its own task at the check's address and gets no
    # default, since the role's verb is not the generator's to make
    # there; a check at a free address in it still gets its sub-task.
    assert set(held.tasks) == {"check", "new"}
    assert inspect.unwrap(held.tasks["check"]) is own
    # A task at the role's address serves the role: no group is made.
    assert "owned" not in root.groups
    assert inspect.unwrap(root.tasks["owned"]) is owned


def test_a_second_generation_follows_the_registry(empty: None) -> None:
    _probes()
    root = Group("root")
    generate_verbs(root)
    probe, quiet = root.groups["probe"], root.groups["quiet"]
    alpha = probe.tasks["alpha"]
    # Nothing changed: every verb is kept as it was.
    generate_verbs(root)
    assert probe.tasks["alpha"] is alpha
    # A check that fixes joins a role whose verb offered no fix: the
    # role's verb is remade with the flag; a withdrawn check's sub-task
    # goes, and a role left with no check goes with its group.
    register_check(CheckRecord("fixer", "quiet", _idle, fix=_idle))
    unregister_check("probe.beta")
    generate_verbs(root)
    assert _flags(quiet, "default") == {"fix", "safe_fix"}
    assert _flags(quiet, "fixer") == {"fix", "safe_fix"}
    assert set(probe.tasks) == {"default", "alpha"}
    assert _flags(probe, "default") == {"fix", "safe_fix"}
    unregister_check("probe.alpha")
    generate_verbs(root)
    assert "probe" not in root.groups


def test_the_builtin_checks_generate_the_role_verbs(python_checks: object) -> None:
    # The base's own: an extension mounted earlier in this process (the
    # docs extension's lint checks) and the fakes add theirs beside them.
    tree = {
        role: {tool for tool, record in tools.items() if record.extension == SELF}
        for role, tools in verb_tree().items()
    }
    assert tree == {
        "build": {"compile", "configure"},
        "format": set(),
        "layering": {"graph", "imports"},
        "lint": set(),
        "provenance": {"check"},
        "drift": {"check"},
        "test": {"ctest"},
        "typecheck": set(),
    }
    root = Group("root")
    generate_verbs(root)
    test = root.groups["test"]
    # --point where a check reads it, and nowhere else.
    assert _flags(test, "default") == {"point"}
    assert _flags(test, "fake") == {"point"}
    assert _flags(test, "ctest") == set()
    assert _flags(root.groups["layering"], "imports") == {"fix", "safe_fix"}


def test_a_role_with_no_listed_check_has_no_verb(registry: None) -> None:
    # The fallback first: no check of the role, no verb for it.
    root = Group("root")
    generate_verbs(root)
    assert "divination" not in root.groups
    register_check(CheckRecord("crystal", "divination", _idle))
    generate_verbs(root)
    assert "divination" in root.groups
    # Withdrawn, the role's verb goes with its last check.
    unregister_check("divination.crystal")
    generate_verbs(root)
    assert "divination" not in root.groups
