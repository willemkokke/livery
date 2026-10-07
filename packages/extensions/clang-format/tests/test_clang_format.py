"""The clang-format extension: unlisted, nothing; listed, it formats C and C++."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

import livery.extensions.clang.format._extension as declaration
import livery.toolroom.tools as tools
from livery.extensions.clang.format import _checks
from livery.workshop import GateContext, Package
from livery.workshop import _checks as registry

CONTRACT = (
    "[workspace]\n"
    'name = "acme"\n'
    'namespace = "acme"\n'
    'authors = [{ name = "Acme", email = "dev@acme.test" }]\n'
    'copyright-year = "2026"\n'
)

UNFORMATTED = "int  main( ){return 0;}\n"
FORMATTED = "int main() { return 0; }\n"


@pytest.fixture
def registered() -> Iterator[None]:
    """clang-format's check registered, as the mount registers a listed extension's."""
    from livery.workshop._extensions import register_declared_checks

    state = registry.snapshot()
    register_declared_checks("clang-format", declaration)
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
    (directory / "src" / "native.cpp").write_text(UNFORMATTED)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return Package(
        directory=directory,
        path="packages/native",
        name="acme-native",
        kind="cpp-conan",
        depends=(),
    )


def _clang_format_runs() -> bool:
    try:
        return (
            tools.clang_format.opts(nofail=True, recorded=False)("--version").code == 0
        )
    except (OSError, tools.ToolError):
        return False


# The refusals first: unlisted, the extension does nothing; a source out
# of style is named.


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
        assert "format.clang-format" not in checks_by_name()
        assert "clang_format" not in {t for t, _ in tools_for_kind("cpp-conan")}
        deliver(root)
        assert not (root / "packages" / "native" / ".clang-format").exists()
        # Listed, the mount registers the check under the listed name, the
        # tool joins the native kinds' profile, and the sync writes each
        # native package's style.
        (root / "workshop.toml").write_text(
            CONTRACT + 'extensions = ["clang-format"]\n'
        )
        with footman_registry.capture():
            assert mount_extensions(root) == ("clang-format",)
        assert checks_by_name()["format.clang-format"].extension == "clang-format"
        assert "clang_format" in {t for t, _ in tools_for_kind("python-nanobind")}
        deliver(root)
        style = (root / "packages" / "native" / ".clang-format").read_text()
        assert "the cpp-conan kind" in style and "IndentWidth: 4" in style
    finally:
        registry.restore(state)


def test_the_refusal_names_a_windows_path_whole() -> None:
    # clang-format writes `<file>:<line>:<column>: error: ...`, and a
    # Windows file name carries a colon of its own two characters in;
    # reading the path up to the line number keeps it whole.
    posix = "src/native.cpp:10:2: error: code should be clang-formatted"
    windows = (
        r"D:\a\livery\packages\native\src\native.cpp:10:2:"
        " error: code should be clang-formatted"
    )
    warned = "include/native.hpp:3:1: warning: code should be clang-formatted"
    assert _checks.unformatted("\n".join([posix, windows, warned])) == [
        r"D:\a\livery\packages\native\src\native.cpp",
        "include/native.hpp",
        "src/native.cpp",
    ]
    assert _checks.unformatted("nothing to say here") == []


def test_a_source_out_of_style_is_named_and_the_fix_heals_it(tmp_path: Path) -> None:
    if not _clang_format_runs():
        pytest.skip("clang-format is not supplied on this host")
    package = _native(tmp_path)
    style = _checks.STYLE.replace("{{ kind }}", "cpp-conan")
    (package.directory / ".clang-format").write_text(style)
    source = package.directory / "src" / "native.cpp"
    with pytest.raises(BaseException, match="clang-format would rewrite") as caught:
        _checks.run_format(package, (source,), fix=False)
    assert "native.cpp" in str(caught.value)
    _checks.run_format(package, (source,), fix=True)
    assert source.read_text() == FORMATTED
    _checks.run_format(package, (source,), fix=False)
    _checks.run_format(package, (), fix=False)  # nothing to read: no call


def test_the_words_after_the_dashes_reach_clang_format_after_its_mode(
    tmp_path: Path,
) -> None:
    from livery.toolroom.tools import Result
    from livery.toolroom.tools.testing import answers

    package = _native(tmp_path)
    source = package.directory / "src" / "native.cpp"
    with answers({("clang-format",): Result(0)}) as calls:
        _checks.run_format(package, (source,), fix=False, arguments=("--verbose",))
        _checks.run_format(package, (source,), fix=True, arguments=("--verbose",))
    assert [list(call.argv[1:]) for call in calls] == [
        ["--dry-run", "--Werror", "--verbose", str(source)],
        ["-i", "--verbose", str(source)],
    ]


# The check: the package's files its claims reach, or the files a run names.


def test_the_check_reads_the_package_s_sources_or_the_files_a_run_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: None
) -> None:
    calls: list[tuple[tuple[str, ...], bool]] = []
    handed: list[tuple[str, ...]] = []

    def watched(
        package: Package,
        files: tuple[Path, ...],
        *,
        fix: bool,
        arguments: tuple[str, ...],
    ) -> None:
        calls.append(
            (tuple(p.relative_to(package.directory).as_posix() for p in files), fix)
        )
        handed.append(arguments)

    monkeypatch.setattr(_checks, "run_format", watched)
    package = _native(tmp_path)
    (package.directory / "conanfile.py").write_text("")
    record = registry.check_for("format.clang-format")
    ctx = GateContext(root=tmp_path, packages=(package,), package=package)
    record.run(ctx)
    assert record.fix is not None
    record.fix(ctx)
    # The recipe is python's, never clang-format's.
    assert calls == [(("src/native.cpp",), False), (("src/native.cpp",), True)]
    # The words after -- on the check's own verb reach both modes.
    words = ("--verbose",)
    ctx = GateContext(
        root=tmp_path, packages=(package,), package=package, arguments=words
    )
    record.run(ctx)
    record.fix(ctx)
    assert handed == [(), (), words, words]
