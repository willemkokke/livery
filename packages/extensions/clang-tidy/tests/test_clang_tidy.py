"""The clang-tidy extension: unlisted, nothing; listed, it lints each cpp package."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

import livery.extensions.clang.tidy._extension as declaration
from livery.extensions.clang.tidy import _checks
from livery.workshop import GateContext, Package
from livery.workshop import _checks as registry

CONTRACT = (
    "[workspace]\n"
    'name = "acme"\n'
    'namespace = "acme"\n'
    'authors = [{ name = "Acme", email = "dev@acme.test" }]\n'
    'copyright-year = "2026"\n'
)


@pytest.fixture
def registered() -> Iterator[None]:
    """clang-tidy's check registered, as the mount registers a listed extension's."""
    from livery.workshop._extensions import register_declared_checks

    state = registry.snapshot()
    register_declared_checks("clang-tidy", declaration)
    try:
        yield
    finally:
        registry.restore(state)


def _native(root: Path) -> Package:
    """A cpp-conan member in a git checkout, one source in it."""
    directory = root / "packages" / "native"
    (directory / "src").mkdir(parents=True)
    (directory / "workshop.toml").write_text(
        'kind = "cpp-conan"\nname = "acme-native"\n'
    )
    (directory / "src" / "native.cpp").write_text("int main() { return 0; }\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return Package(
        directory=directory,
        path="packages/native",
        name="acme-native",
        kind="cpp-conan",
        depends=(),
    )


# The refusals first: unlisted, the extension does nothing; a host that
# cannot give the binary its headers skips, saying why; a finding refuses.


def test_unlisted_it_registers_no_check_requires_no_tool_and_writes_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.footman import _registry as footman_registry
    from livery.workshop._checks import checks_by_name, tools_for_kind
    from livery.workshop._extensions import mount_extensions
    from livery.workshop._shipped_files import deliver

    root = tmp_path / "ws"
    _native(root)
    (root / "workshop.toml").write_text(CONTRACT + "extensions = []\n")
    monkeypatch.setattr(
        "livery.workshop._extensions.workspace_root", lambda start=None: root
    )
    state = registry.snapshot()
    try:
        with footman_registry.capture():
            assert mount_extensions(root) == ()
        assert "lint.clang-tidy" not in checks_by_name()
        assert "clang_tidy" not in {t for t, _ in tools_for_kind("cpp-conan")}
        deliver(root)
        assert not (root / "packages" / "native" / ".clang-tidy").exists()
        (root / "workshop.toml").write_text(CONTRACT + 'extensions = ["clang-tidy"]\n')
        with footman_registry.capture():
            assert mount_extensions(root) == ("clang-tidy",)
        record = checks_by_name()["lint.clang-tidy"]
        assert record.extension == "clang-tidy"
        assert record.after == ("build.configure",)
        assert "clang_tidy" in {t for t, _ in tools_for_kind("cpp-conan")}
        deliver(root)
        checks = (root / "packages" / "native" / ".clang-tidy").read_text()
        assert "the cpp-conan kind" in checks and 'WarningsAsErrors: "*"' in checks
    finally:
        registry.restore(state)


def test_a_host_without_the_headers_skips_saying_why(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    package = _native(tmp_path)
    monkeypatch.setattr(_checks, "_asked", lambda argv: "")
    assert _checks.toolchain_arguments() == (
        [],
        "no compiler here answers where its builtin headers are",
    )
    source = package.directory / "src" / "native.cpp"
    _checks.run_lint(package, (source,), tmp_path / "compile_commands.json")
    assert "acme-native: clang-tidy skips, no compiler here" in capsys.readouterr().out
    # On macOS the SDK is a second question, and a missing one skips too.
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr(
        _checks, "_asked", lambda argv: "/opt/clang/include" if "clang" in argv else ""
    )
    assert _checks.toolchain_arguments() == (
        [],
        "xcrun names no SDK, where this platform keeps its headers",
    )
    monkeypatch.setattr(
        _checks, "_asked", lambda argv: "/sdk" if argv[0] == "xcrun" else "/res"
    )
    assert _checks.toolchain_arguments() == (
        ["--extra-arg=-resource-dir=/res", "--extra-arg=-isysroot/sdk"],
        "",
    )
    # Elsewhere the compiler's headers are the whole answer.
    monkeypatch.setattr("sys.platform", "linux")
    assert _checks.toolchain_arguments() == (["--extra-arg=-resource-dir=/res"], "")


def test_a_question_no_program_answers_reads_as_no_answer() -> None:
    import sys

    assert _checks._asked(["no-such-program-anywhere"]) == ""  # pyright: ignore[reportPrivateUsage]
    asked = [sys.executable, "-c", "print('first'); print('second')"]
    assert _checks._asked(asked) == "first"  # pyright: ignore[reportPrivateUsage]


def test_a_finding_refuses_with_clang_tidy_s_own_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.toolroom.tools import Result
    from livery.toolroom.tools.testing import answers

    package = _native(tmp_path)
    monkeypatch.setattr(_checks, "toolchain_arguments", lambda: (["--extra"], ""))
    source = package.directory / "src" / "native.cpp"
    found = Result(1, stdout="native.cpp:1:1: error: bugprone-branch-clone\n")
    database = tmp_path / "build" / "compile_commands.json"
    with (
        answers({("clang-tidy",): found}) as calls,
        pytest.raises(BaseException, match="clang-tidy found something"),
    ):
        _checks.run_lint(package, (source,), database)
    assert list(calls[0].argv[1:4]) == ["-p", str(database.parent), "--extra"]
    # Nothing found: the lint passes; nothing to read: no call at all.
    with answers({("clang-tidy",): Result(0)}) as calls:
        _checks.run_lint(package, (source,), database)
    assert len(calls) == 1
    _checks.run_lint(package, (), database)
    # The words after -- on the check's own verb go before the files.
    with answers({("clang-tidy",): Result(0)}) as calls:
        _checks.run_lint(package, (source,), database, ("--checks=-*,bugprone-*",))
    assert list(calls[0].argv[1:]) == [
        "-p",
        str(database.parent),
        "--extra",
        "--checks=-*,bugprone-*",
        str(source),
    ]


# The check: the package's files against the database its kind's build writes.


def test_the_check_waits_for_the_database_then_reads_the_package_s_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: None
) -> None:
    calls: list[tuple[tuple[str, ...], Path]] = []
    handed: list[tuple[str, ...]] = []

    def watched(
        package: Package,
        files: tuple[Path, ...],
        database: Path,
        arguments: tuple[str, ...],
    ) -> None:
        names = tuple(p.relative_to(package.directory).as_posix() for p in files)
        calls.append((names, database))
        handed.append(arguments)

    monkeypatch.setattr(_checks, "run_lint", watched)
    package = _native(tmp_path)
    record = registry.check_for("lint.clang-tidy")
    ctx = GateContext(root=tmp_path, packages=(package,), package=package)
    # Not configured yet: no database, nothing to lint against.
    record.run(ctx)
    assert calls == []
    database = package.directory / "build" / "gate" / "compile_commands.json"
    database.parent.mkdir(parents=True)
    database.write_text("[]")
    record.run(ctx)
    assert calls == [(("src/native.cpp",), database)]
    # The words after -- on the check's own verb reach the lint.
    words = ("--checks=-*,bugprone-*",)
    record.run(
        GateContext(
            root=tmp_path, packages=(package,), package=package, arguments=words
        )
    )
    assert handed == [(), words]
