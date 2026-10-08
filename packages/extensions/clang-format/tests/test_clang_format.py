"""The clang-format extension: unlisted, nothing; listed, it formats C and C++."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

import livery.toolroom.tools as tools
from livery.footman import Failed
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
    from livery.workshop._extensions import declaration, register_declared

    state = registry.snapshot()
    found = declaration("clang-format")
    assert found is not None
    register_declared("clang-format", found.additions)
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


def test_a_source_out_of_style_refuses_and_the_fix_heals_it(
    tmp_path: Path, registered: None
) -> None:
    if not _clang_format_runs():
        pytest.skip("clang-format is not supplied on this host")
    package = _native(tmp_path)
    from livery.workshop._extensions import declaration

    found = declaration("clang-format")
    assert found is not None
    (fragment,) = (
        f for f in found.additions.checks[0].fragments if f.kind == "cpp-conan"
    )
    style = fragment.text.replace("{{ kind }}", "cpp-conan")
    (package.directory / ".clang-format").write_text(style)
    source = package.directory / "src" / "native.cpp"
    record = registry.check_for("format.clang-format")
    ctx = GateContext(root=tmp_path, packages=(package,), package=package)
    # The refusal names the command, and with it the file.
    with pytest.raises(Failed, match="exited 1") as caught:
        record.run(ctx)
    assert "native.cpp" in str(caught.value)
    assert record.fix is not None
    record.fix(ctx)
    assert source.read_text() == FORMATTED
    record.run(ctx)


def test_a_package_with_no_file_to_read_calls_nothing(
    tmp_path: Path, registered: None
) -> None:
    from livery.toolroom.tools import Result
    from livery.toolroom.tools.testing import answers

    package = _native(tmp_path)
    (package.directory / "src" / "native.cpp").unlink()
    record = registry.check_for("format.clang-format")
    with answers({("clang-format",): Result(0)}) as calls:
        record.run(GateContext(root=tmp_path, packages=(package,), package=package))
    assert [call for call in calls if call.argv[0] == "clang-format"] == []


# The check: the package's files its claims reach, from the package's
# directory, with the words after -- before them.


class _ClangFormat:
    """clang-format's toolroom handle, standing in: each call's words and directory."""

    def __init__(self, calls: list[tuple[tuple[str, ...], object]]) -> None:
        self.calls = calls
        self.cwd: object = None

    def opts(self, **chosen: object) -> _ClangFormat:
        bound = _ClangFormat(self.calls)
        bound.cwd = chosen.get("cwd")
        return bound

    def __call__(self, *argv: str) -> None:
        self.calls.append((argv, self.cwd))


def test_the_check_reads_the_package_s_sources_and_the_words_after_the_dashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: None
) -> None:
    calls: list[tuple[tuple[str, ...], object]] = []
    monkeypatch.setattr(tools, "clang_format", _ClangFormat(calls))
    package = _native(tmp_path)
    (package.directory / "conanfile.py").write_text("")
    source = str(package.directory / "src" / "native.cpp")
    record = registry.check_for("format.clang-format")
    assert record.fix is not None
    ctx = GateContext(root=tmp_path, packages=(package,), package=package)
    words = ("--verbose",)
    spoken = GateContext(
        root=tmp_path, packages=(package,), package=package, arguments=words
    )
    record.run(ctx)
    record.fix(ctx)
    record.run(spoken)
    record.fix(spoken)
    # The recipe is python's, never clang-format's, and every call runs
    # from the package's directory, where its own .clang-format is.
    assert calls == [
        (("--dry-run", "--Werror", source), package.directory),
        (("-i", source), package.directory),
        (("--dry-run", "--Werror", "--verbose", source), package.directory),
        (("-i", "--verbose", source), package.directory),
    ]
