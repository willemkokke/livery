"""A check in words: the engine runs the tool's words over what the run reaches."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

import livery.toolroom.tools as tools
from livery.footman import Failed, RunFailed
from livery.workshop import GateContext, Package, _checks
from livery.workshop._checks import WHOLE, CheckRecord, register_check
from livery.workshop._declaration import Reference
from livery.workshop._words import Command, Words, explained


@pytest.fixture(autouse=True)
def restored() -> Iterator[None]:
    state = _checks.snapshot()
    yield
    _checks.restore(state)


class _Refused(RunFailed):
    """A non-zero exit, as a toolroom handle raises it."""

    def __init__(self, text: str) -> None:
        Exception.__init__(self, text)


class _Tool:
    """A toolroom handle's stand-in: each call recorded, the named ones refused."""

    def __init__(
        self,
        calls: list[tuple[tuple[str, ...], dict[str, object]]] | None = None,
        refusing: tuple[str, ...] = (),
        chosen: dict[str, object] | None = None,
    ) -> None:
        self.calls = [] if calls is None else calls
        self.refusing = refusing
        self.chosen = chosen or {}

    def opts(self, **chosen: object) -> _Tool:
        return _Tool(self.calls, self.refusing, chosen)

    def __call__(self, *argv: str) -> None:
        self.calls.append((argv, self.chosen))
        if any(word in self.refusing for word in argv):
            raise _Refused(f"`acme {' '.join(argv)}` exited with code 1")


def _check(
    words: Words,
    *,
    scope: str = "workspace",
    narrowing: str = "none",
    arguments: bool = False,
    extension: str = "acme",
    extensions: tuple[str, ...] = (),
) -> CheckRecord:
    record = CheckRecord(
        "acme",
        "lint",
        Command(words, "lint.acme"),
        scope=scope,
        narrowing=narrowing,
        fix=Command(words, "lint.acme", fixing=True) if words.fix else None,
        extension=extension,
        extensions=extensions,
        tools=("acme",),
        arguments=arguments,
        words=words,
    )
    register_check(record)
    return record


def _one_file(ctx: GateContext, name: str) -> tuple[Path, ...]:
    """The package's one C++ file, as the run's selection names it."""
    del name
    assert ctx.package is not None
    return (ctx.package.directory / "a.cpp",)


def _member(root: Path, name: str) -> Package:
    directory = root / "packages" / name
    directory.mkdir(parents=True)
    return Package(
        directory=directory,
        path=f"packages/{name}",
        name=f"acme-{name}",
        kind="python",
        depends=(),
    )


def _paths(monkeypatch: pytest.MonkeyPatch, chosen: tuple[str, ...]) -> None:
    monkeypatch.setattr(_checks, "scoped_paths", lambda ctx, name: chosen)


# The refusals first: a failed call, an unanswered word, a package's word
# outside a package, and a package with no database to read.


def test_a_call_that_exits_non_zero_refuses_and_every_other_call_still_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _Tool(refusing=("--platform=win32",))
    monkeypatch.setattr(tools, "acme", tool, raising=False)
    words = Words(
        ("acme", "--platform={platform}"),
        matrix=(("platform", ("linux", "darwin", "win32")),),
    )
    record = _check(words)
    with pytest.raises(BaseException, match="win32"):
        record.run(GateContext(root=tmp_path, packages=(), check=record.name))
    ran = sorted(argv[0] for argv, _chosen in tool.calls)
    assert ran == ["--platform=darwin", "--platform=linux", "--platform=win32"]


def test_a_refused_call_is_a_check_s_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tools, "acme", _Tool(refusing=("--check",)), raising=False)
    record = _check(Words(("acme", "--check")))
    with pytest.raises(Failed, match=r"`acme --check` exited with code 1"):
        record.run(GateContext(root=tmp_path, packages=(), check=record.name))


def test_a_placeholder_nothing_answers_refuses_naming_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tools, "acme", _Tool(), raising=False)
    record = _check(Words(("acme", "--into={output}")))
    with pytest.raises(
        Failed,
        match=r"lint\.acme names \{output\}, which neither the engine nor its"
        r" matrix answers; the engine answers cache, package, compile-commands",
    ):
        record.run(GateContext(root=tmp_path, packages=(), check=record.name))


def test_a_package_s_placeholder_in_a_workspace_call_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tools, "acme", _Tool(), raising=False)
    record = _check(Words(("acme", "{package}")))
    with pytest.raises(
        Failed, match=r"lint\.acme names \{package\}, which only a call that judges"
    ):
        record.run(GateContext(root=tmp_path, packages=(), check=record.name))


def test_a_package_without_a_compilation_database_is_not_run_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tool = _Tool()
    monkeypatch.setattr(tools, "acme", tool, raising=False)
    monkeypatch.setattr(_checks, "scoped_files", _one_file)
    monkeypatch.setattr(
        "livery.workshop._lifecycle.compile_commands", lambda package: None
    )
    member = _member(tmp_path, "one")
    record = _check(
        Words(("acme", "-p", "{compile-commands}")),
        scope="package",
        extensions=("python",),
    )
    ctx = GateContext(root=tmp_path, packages=(member,), check=record.name)
    record.run(ctx.for_package(member))
    assert tool.calls == []
    assert (
        "lint.acme: packages/one has no compilation database; not run"
        in capsys.readouterr().out
    )


def test_a_run_that_reaches_nothing_the_check_reads_calls_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _Tool()
    monkeypatch.setattr(tools, "acme", tool, raising=False)
    record = _check(Words(("acme", "check")), narrowing="paths")
    _paths(monkeypatch, ())
    record.run(GateContext(root=tmp_path, packages=(), check=record.name))
    assert tool.calls == []


def test_a_directory_with_no_file_the_claims_read_is_never_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._checks import Claim

    tool = _Tool()
    monkeypatch.setattr(tools, "acme", tool, raising=False)
    words = Words(("acme", "check"))
    record = CheckRecord(
        "acme",
        "lint",
        Command(words, "lint.acme"),
        narrowing="paths",
        extensions=("python",),
        tools=("acme",),
        claims=(Claim("source", suffixes=(".py",)), Claim("test", suffixes=(".py",))),
        words=words,
    )
    register_check(record)
    # One member ships data alone under src, its tests in python; the
    # other member keeps the run from reaching the whole.
    one = _member(tmp_path, "one")
    (one.directory / "src" / "acme").mkdir(parents=True)
    (one.directory / "src" / "acme" / "extension.toml").write_text("")
    (one.directory / "tests").mkdir()
    (one.directory / "tests" / "test_one.py").write_text("")
    two = _member(tmp_path, "two")
    ctx = GateContext(
        root=tmp_path, packages=(one, two), subset=(one,), check=record.name
    )
    record.run(ctx)
    assert tool.calls == [(("check", "packages/one/tests"), {})]
    # A member with nothing the check reads at all is not called.
    (one.directory / "tests" / "test_one.py").unlink()
    tool.calls.clear()
    record.run(ctx)
    assert tool.calls == []


# The calls: the words, the words after --, then the paths.


def test_the_words_come_first_then_the_arguments_then_the_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _Tool()
    monkeypatch.setattr(tools, "acme", tool, raising=False)
    record = _check(Words(("acme", "check", "--strict")), narrowing="paths")
    _paths(monkeypatch, ("packages/one/src", "packages/one/tests"))
    ctx = GateContext(root=tmp_path, packages=(), check=record.name)
    record.run(ctx)
    record.run(
        GateContext(root=tmp_path, packages=(), check=record.name, arguments=("-v",))
    )
    assert [argv for argv, _chosen in tool.calls] == [
        ("check", "--strict", "packages/one/src", "packages/one/tests"),
        ("check", "--strict", "-v", "packages/one/src", "packages/one/tests"),
    ]
    # The whole is a call with no path: the tool reads its own configuration.
    tool.calls.clear()
    _paths(monkeypatch, WHOLE)
    record.run(ctx)
    assert tool.calls == [(("check", "--strict"), {})]


def test_a_package_check_calls_from_the_package_with_its_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _Tool()
    monkeypatch.setattr(tools, "acme", tool, raising=False)
    member = _member(tmp_path, "one")
    database = member.directory / "build" / "compile_commands.json"
    database.parent.mkdir()
    database.write_text("[]\n")
    monkeypatch.setattr(_checks, "scoped_files", _one_file)
    monkeypatch.setattr(
        "livery.workshop._lifecycle.compile_commands", lambda package: database
    )
    record = _check(
        Words(("acme", "-p", "{compile-commands}", "--root={package}")),
        scope="package",
        extensions=("python",),
    )
    ctx = GateContext(root=tmp_path, packages=(member,), check=record.name)
    record.run(ctx.for_package(member))
    assert tool.calls == [
        (
            (
                "-p",
                str(database.parent),
                f"--root={member.directory}",
                str(member.directory / "a.cpp"),
            ),
            {"cwd": member.directory},
        )
    ]


def test_fix_runs_the_fix_words_and_safe_fix_its_own_or_the_fix_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _Tool()
    monkeypatch.setattr(tools, "acme", tool, raising=False)
    words = Words(
        ("acme", "--check"), fix=("acme", "--fix"), safe_fix=("acme", "--safe")
    )
    record = _check(words)
    assert record.fix is not None
    ctx = GateContext(root=tmp_path, packages=(), check=record.name)
    record.fix(ctx)
    record.fix(GateContext(root=tmp_path, packages=(), check=record.name, safe=True))
    plain = _check(Words(("acme", "--check"), fix=("acme", "--fix")))
    assert plain.fix is not None
    plain.fix(GateContext(root=tmp_path, packages=(), check=plain.name, safe=True))
    assert [argv for argv, _chosen in tool.calls] == [
        ("--fix",),
        ("--safe",),
        ("--fix",),
    ]


def test_env_reaches_the_call_over_the_run_s_own_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _Tool()
    monkeypatch.setattr(tools, "acme", tool, raising=False)
    monkeypatch.setenv("ACME_KEPT", "yes")
    record = _check(Words(("acme",), env=(("ACME_HOME", "{cache}"),)))
    record.run(GateContext(root=tmp_path, packages=(), check=record.name))
    ((argv, chosen),) = tool.calls
    assert argv == ()
    env = chosen["env"]
    assert isinstance(env, dict)
    assert env["ACME_HOME"] == ".workshop/.cache/acme"
    assert env["ACME_KEPT"] == "yes"
    assert len(env) == len(os.environ) + 1


def test_the_matrix_calls_once_per_value_with_a_cache_of_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _Tool()
    monkeypatch.setattr(tools, "acme", tool, raising=False)
    words = Words(
        ("acme", "--platform={platform}", "--cache-dir={cache}/{platform}"),
        matrix=(("platform", ("linux", "win32")),),
    )
    record = _check(words)
    record.run(GateContext(root=tmp_path, packages=(), check=record.name))
    assert sorted(argv for argv, _chosen in tool.calls) == [
        ("--platform=linux", "--cache-dir=.workshop/.cache/acme/linux"),
        ("--platform=win32", "--cache-dir=.workshop/.cache/acme/win32"),
    ]


# What fm explain prints for a check.


def test_explain_shows_the_words_the_matrix_and_what_the_engine_appends() -> None:
    words = Words(
        ("ruff", "check", "--force-exclude"),
        fix=("ruff", "check", "--fix", "--force-exclude"),
        matrix=(("platform", ("linux", "win32")),),
    )
    record = _check(words, narrowing="paths", arguments=True, extension="ruff")
    assert explained(record) == [
        "  lint.acme",
        "    extension: ruff",
        "    scope: the workspace, narrowed by paths",
        "    judge: ruff check --force-exclude <the words after --> <the paths the"
        " run reaches>",
        "    fix: ruff check --fix --force-exclude <the words after --> <the paths"
        " the run reaches>",
        "    matrix: platform = linux, win32, one call each",
    ]
    coded = CheckRecord("acme", "test", Reference("acme._checks", "judge"))
    assert explained(coded)[2:] == [
        "    scope: the workspace, whole every run",
        "    run: acme._checks:judge (code)",
    ]
