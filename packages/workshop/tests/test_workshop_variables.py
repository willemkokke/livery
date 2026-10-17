"""The variables an extension declares: the table's refusals, then each place.

An ``[env.<NAME>]`` table refuses what it does not take first: a
missing value or doc, a brace form it does not answer, a value of the
wrong type, a name no shell exports, a table under ``[for]``. Then what
``always`` decides, and the directory ``{place}`` takes in each place
that sets the variables: every ``fm`` run in a CI job, a pass of the
loop on a development environment, and the scale fixture.
"""

from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from livery.footman import Failed
from livery.workshop._declaration import DeclarationError, DeclaredVariable, read
from livery.workshop._variables import (
    declared_variables,
    job_place,
    placed,
    unanswered,
    workspace_variables,
)
from workshop_extension_fakes import fake_extensions

_FAILURES = (SystemExit, Failed)

#: A variable in a directory of its own, as a tool whose cache takes one
#: writer at a time declares it.
DECLARING = """\
[env.ACME_HOME]
value = "{place}/acme"
always = true
doc = "acme's cache takes one writer at a time"
"""

ACME_HOME = DeclaredVariable(
    "ACME_HOME",
    "{place}/acme",
    "acme's cache takes one writer at a time",
    always=True,
    extension="acme.cache",
)


@pytest.fixture
def package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """An importable package ``acme_env`` with no declaration yet."""
    directory = tmp_path / "site" / "acme_env"
    directory.mkdir(parents=True)
    monkeypatch.syspath_prepend(str(tmp_path / "site"))
    importlib.invalidate_caches()
    yield directory
    sys.modules.pop("acme_env", None)


def _refusal(package: Path, text: str) -> str:
    (package / "extension.toml").write_text(text)
    with pytest.raises(DeclarationError) as caught:
        read("acme", "acme_env")
    return str(caught.value)


# The refusals first.


def test_a_variable_without_a_value_or_a_doc_refuses_naming_the_key(
    package: Path,
) -> None:
    where = package / "extension.toml"
    assert _refusal(package, '[env.ACME_HOME]\ndoc = "why"\n') == (
        f"{where}: env.ACME_HOME.value is missing: the variable's value"
    )
    assert _refusal(package, '[env.ACME_HOME]\nvalue = "x"\n') == (
        f"{where}: env.ACME_HOME.doc is missing: what the variable is for"
    )
    assert _refusal(package, '[env.ACME_HOME]\nvalue = "x"\ndoc = " "\n') == (
        f"{where}: env.ACME_HOME.doc is empty: say what the variable is for"
    )


@pytest.mark.parametrize(
    ("value", "named"),
    [
        ("{UPPER}/acme", "{UPPER}"),
        ("{env.HOME}/acme", "{env.HOME}"),
        ("{cache}/acme", "{cache}"),
        ("{place}/{name}", "{name}"),
        ("{place/acme", "{place/acme"),
        ("acme}", "}"),
        ("{{place}}", "{, }"),
    ],
)
def test_a_brace_form_the_table_does_not_answer_refuses_naming_it(
    package: Path, value: str, named: str
) -> None:
    # Every brace form but {place} refuses, so a placeholder added later
    # changes the meaning of no value declared before it.
    refused = _refusal(package, f'[env.ACME_HOME]\nvalue = "{value}"\ndoc = "why"\n')
    assert refused.endswith(
        f"env.ACME_HOME.value names {named}, which an [env] value does not"
        " answer; it answers {place} alone, a directory of the variable's own"
    )


def test_a_value_of_the_wrong_type_refuses_through_the_schema(package: Path) -> None:
    refused = _refusal(
        package, '[env.ACME_HOME]\nvalue = "x"\ndoc = "why"\nalways = "yes"\n'
    )
    assert "env.ACME_HOME.always is a string ('yes'); it takes true or false" in (
        refused
    )
    refused = _refusal(package, '[env.ACME_HOME]\nvalue = 1\ndoc = "why"\n')
    assert "env.ACME_HOME.value is an integer (1); it takes a string" in refused
    refused = _refusal(package, '[env]\nACME_HOME = "x"\n')
    assert "env.ACME_HOME is a string ('x'); it takes a table" in refused
    refused = _refusal(
        package, '[env.ACME_HOME]\nvalue = "x"\ndoc = "why"\ndefault = "y"\n'
    )
    assert "[env.ACME_HOME] has no key 'default'" in refused


def test_a_name_no_shell_exports_and_a_line_break_refuse(package: Path) -> None:
    refused = _refusal(package, '[env."1ACME"]\nvalue = "x"\ndoc = "why"\n')
    assert refused.endswith(
        "env.1ACME is no variable name: letters, digits and underscores, not"
        " starting with a digit, as a shell exports one"
    )
    refused = _refusal(package, '[env.ACME_HOME]\nvalue = "a\\nb"\ndoc = "why"\n')
    assert refused.endswith(
        "env.ACME_HOME.value holds a line break, which no export can carry"
    )


def test_a_for_table_declares_no_variable(package: Path) -> None:
    # A variable an extension needs does not depend on what else is listed.
    refused = _refusal(package, '[for.docs.env.ACMEHOME]\nvalue = "x"\ndoc = "why"\n')
    assert "[for.docs] has no key 'env'" in refused


def test_two_extensions_declaring_one_variable_refuse_naming_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(tmp_path, monkeypatch, cache=DECLARING, other=DECLARING)
    with pytest.raises(_FAILURES) as caught:
        declared_variables(("acme.cache", "acme.other"))
    assert str(caught.value) == (
        "acme.cache and acme.other both declare the environment variable"
        " ACME_HOME; one extension declares a variable"
    )


# The read, and what always decides.


def test_the_read_keeps_each_variable_in_the_file_s_order(package: Path) -> None:
    (package / "extension.toml").write_text(
        DECLARING + '\n[env.ACME_MODE]\nvalue = "quiet"\ndoc = "how acme reports"\n'
    )
    found = read("acme", "acme_env")
    assert found is not None
    assert found.env == (
        replace(ACME_HOME, extension="acme"),
        DeclaredVariable("ACME_MODE", "quiet", "how acme reports", extension="acme"),
    )
    assert [variable.placed for variable in found.env] == [True, False]
    assert unanswered("{place}/a/{place}") == ()


def test_without_always_the_environment_s_value_wins() -> None:
    shared = replace(ACME_HOME, always=False)
    assert placed((shared,), "/job", {"ACME_HOME": "/runner/acme"}) == {
        "ACME_HOME": "/runner/acme"
    }
    # An empty value is no value, as in the cascade.
    assert placed((shared,), "/job", {"ACME_HOME": ""}) == {"ACME_HOME": "/job/acme"}


def test_with_always_the_declared_value_beats_a_runner_wide_one() -> None:
    assert placed((ACME_HOME,), "/job", {"ACME_HOME": "/runner/acme"}) == {
        "ACME_HOME": "/job/acme"
    }


def test_a_value_naming_a_place_is_not_set_where_none_is_named() -> None:
    plain = DeclaredVariable("ACME_MODE", "quiet", "how acme reports", extension="a")
    assert placed((ACME_HOME, plain), None, {}) == {"ACME_MODE": "quiet"}


# The directory each place names.


def test_a_ci_job_s_place_is_its_own_directory_and_a_desk_names_none(
    tmp_path: Path,
) -> None:
    temp = tmp_path / "_temp"
    temp.mkdir()
    # Off a CI job, and on one whose runner names no directory that
    # exists, nothing is named: the entry refuses that job first.
    assert job_place({"RUNNER_TEMP": str(temp)}) is None
    assert job_place({"GITHUB_ACTIONS": "true"}) is None
    gone = str(tmp_path / "gone")
    assert job_place({"GITHUB_ACTIONS": "true", "RUNNER_TEMP": gone}) is None
    assert job_place({"GITHUB_ACTIONS": "true", "RUNNER_TEMP": str(temp)}) == str(temp)
    assert job_place({"GITLAB_CI": "true", "CI_PROJECT_DIR": str(tmp_path)}) == (
        f"{tmp_path}/.workshop"
    )


def _declaring_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A workspace whose root lists ``acme.cache``, which declares ``ACME_HOME``."""
    fake_extensions(tmp_path / "fakes", monkeypatch, cache=DECLARING)
    root = tmp_path / "ws"
    root.mkdir()
    (root / "workshop.toml").write_text('[workspace]\nextensions = ["acme.cache"]\n')
    return root


def test_every_fm_run_in_a_ci_job_sets_the_declared_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typing import cast

    import livery.footman as footman
    from livery.workshop import _env_tasks

    root = _declaring_root(tmp_path, monkeypatch)
    assert workspace_variables(root) == (ACME_HOME,)
    monkeypatch.setattr(
        "livery.workshop._extensions.workspace_root", lambda start=None: root
    )
    monkeypatch.setattr(_env_tasks, "_APPLIED", {})
    monkeypatch.setattr(_env_tasks, "apply_entry", lambda root, environ: ())
    for name in ("GITHUB_ACTIONS", "GITLAB_CI", "ACME_HOME"):
        monkeypatch.delenv(name, raising=False)
    temp = tmp_path / "_temp"
    temp.mkdir()
    monkeypatch.setenv("RUNNER_TEMP", str(temp))
    # On a desk the home is not set: the person's own stands.
    _env_tasks.apply_cascade(cast(footman.Invocation, None))
    assert "ACME_HOME" not in os.environ and _env_tasks._APPLIED == {}
    # In a job, over the runner's own value, since the declaration says
    # always; the hook remembers what it set, for the emitters.
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("ACME_HOME", "/runner/acme")
    _env_tasks.apply_cascade(cast(footman.Invocation, None))
    assert os.environ["ACME_HOME"] == f"{temp}/acme"
    assert {"ACME_HOME": f"{temp}/acme"} == _env_tasks._APPLIED


def test_a_declaration_the_hook_cannot_read_costs_its_variables_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop import _env_tasks

    fake_extensions(tmp_path / "fakes", monkeypatch, cache=DECLARING, other=DECLARING)
    root = tmp_path / "ws"
    root.mkdir()
    (root / "workshop.toml").write_text(
        '[workspace]\nextensions = ["acme.cache", "acme.other"]\n'
    )
    environ = {"GITHUB_ACTIONS": "true", "RUNNER_TEMP": str(tmp_path)}
    assert _env_tasks.apply_declared(root, environ) == {}
    assert "ACME_HOME" not in environ
    assert "declared variables skipped (acme.cache and acme.other both" in (
        capsys.readouterr().err
    )


def test_a_loop_pass_places_them_in_its_environment_s_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._devenv import Environment

    fake_extensions(tmp_path / "fakes", monkeypatch, cache=DECLARING)
    place = Environment("e2e", "host", tmp_path / "envs" / "e2e")
    assert place.variables(("acme.cache",), {"ACME_HOME": "/person/acme"}) == {
        "ACME_HOME": f"{place.directory}/acme"
    }
    assert place.variables((), {}) == {}


def test_the_scale_fixture_places_them_in_its_own_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _scale

    root = _declaring_root(tmp_path, monkeypatch)
    monkeypatch.setenv("ACME_HOME", "/person/acme")
    monkeypatch.setenv("VIRTUAL_ENV", "/elsewhere/.venv")
    env = _scale._environment(root)  # pyright: ignore[reportPrivateUsage]
    assert env["ACME_HOME"] == f"{tmp_path}/acme"
    assert "VIRTUAL_ENV" not in env
