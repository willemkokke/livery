"""Claims: the refusal first, then what a check judges and the ignores it renders."""

from __future__ import annotations

from pathlib import Path

import pytest

import livery.toolroom.tools.api as tools
from livery.workshop import _checks
from livery.workshop._backends import _python
from livery.workshop._checks import (
    CheckRecord,
    Claim,
    GateContext,
    check_for,
    claimants,
    judged_files,
    per_file_ignores,
    register_check,
)
from livery.workshop._packages import Package

_FAILURES = (BaseException,)


@pytest.fixture
def restored_checks():
    state = _checks.snapshot()
    yield
    _checks.restore(state)


def _noop(ctx: GateContext) -> None:
    del ctx


def _files(directory: Path, *paths: str) -> None:
    for path in paths:
        target = directory / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x\n")


def _native(root: Path) -> Package:
    """A cpp-conan member in a fresh repository at *root*, its files untracked."""
    tools.git.opts(cwd=root)("init", "-q")
    native = Package(
        root / "packages" / "cpp", "packages/cpp", "acme-cpp", "cpp-conan", ()
    )
    _files(
        native.directory,
        "src/native.cpp",
        "tests/test_native.cpp",
        "conanfile.py",
        "CMakeLists.txt",
    )
    return native


def test_a_claim_on_a_category_no_table_knows_refuses(restored_checks) -> None:
    with pytest.raises(
        _FAILURES, match="claims 'vendored', which no category table knows"
    ):
        register_check(CheckRecord("acme", "lint", _noop, claims=(Claim("vendored"),)))


def test_a_file_the_repository_ignores_is_beyond_every_claim(tmp_path: Path) -> None:
    native = _native(tmp_path)
    (tmp_path / ".gitignore").write_text("build/\n")
    # A build tree's python file is configuration by category and
    # python by suffix, and still no claim reaches it: git ignores it.
    _files(native.directory, "build/gate/generated.py")
    assert judged_files(check_for("lint.ruff"), native) == ("conanfile.py",)


def test_a_check_judges_the_files_its_claims_reach_and_no_other(
    tmp_path: Path, restored_checks
) -> None:
    native = _native(tmp_path)
    python = Package(
        tmp_path / "packages" / "py", "packages/py", "livery-py", "python", ()
    )
    _files(
        python.directory,
        "src/livery/py/mod.py",
        "src/livery/py/py.typed",
        "tests/test_mod.py",
        "tests/conftest.py",
        "tests/cassettes/first.yaml",
        "pyproject.toml",
        "docs/index.md",
        "docs/examples/first.py",
    )
    register_check(
        CheckRecord(
            "acme-sources",
            "lint",
            _noop,
            kinds=("python",),
            extension="acme.brand",
            claims=(Claim("source", suffixes=(".py",)), Claim("example")),
        )
    )
    assert judged_files(check_for("lint.acme-sources"), python) == (
        "docs/examples/first.py",
        "src/livery/py/mod.py",
    )
    # A python tool's claim on the tests stops at the python files; the
    # test check's does not, since pytest reads the cassettes too.
    for tool in ("basedpyright", "mypy", "ty", "pyrefly"):
        assert judged_files(check_for(f"typecheck.{tool}"), python) == (
            "src/livery/py/mod.py",
            "tests/conftest.py",
            "tests/test_mod.py",
        )
    assert "tests/cassettes/first.yaml" in judged_files(
        check_for("test.pytest"), python
    )
    # A check without claims judges nothing by this measure.
    assert judged_files(check_for("drift.check"), python) == ()
    # A category is a role, not a language: ruff claims the native
    # package's configuration and reaches its conanfile.py alone, while
    # its C++ sources are the clang checks' and CMakeLists.txt is nobody's.
    assert judged_files(check_for("lint.ruff"), native) == ("conanfile.py",)
    assert judged_files(check_for("format.ruff"), native) == ("conanfile.py",)
    assert judged_files(check_for("lint.clang-tidy"), native) == (
        "src/native.cpp",
        "tests/test_native.cpp",
    )
    assert claimants(native, "conanfile.py") == ("format.ruff", "lint.ruff")
    # The tests measure the source, so the test checks claim it too.
    assert claimants(native, "src/native.cpp") == (
        "format.clang-format",
        "lint.clang-tidy",
        "test.ctest",
    )
    assert claimants(native, "CMakeLists.txt") == ()
    assert claimants(python, "src/livery/py/mod.py") == (
        "format.ruff",
        "lint.acme-sources",
        "lint.ruff",
        "test.pytest",
        "typecheck.basedpyright",
        "typecheck.mypy",
        "typecheck.pyrefly",
        "typecheck.ty",
        "typecomplete.basedpyright",
    )
    assert claimants(python, "src/livery/py/py.typed") == ()
    assert claimants(python, "docs/index.md") == ()


def test_a_scoped_run_hands_a_native_member_the_files_its_claims_reach(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    native = _native(tmp_path)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(_python, "run_format", lambda **kwargs: calls.append(kwargs))
    ctx = GateContext(root=tmp_path, packages=(native,), subset=(native,))
    check_for("format.ruff").run(ctx)
    # No directory of the native member is ruff's to walk, so its
    # conanfile.py comes by name and nothing else does.
    assert calls == [{"check": True, "paths": ("packages/cpp/conanfile.py",)}]


def test_two_checks_claiming_one_category_under_different_rules_share_one_entry(
    restored_checks,
) -> None:
    register_check(
        CheckRecord(
            "acme-lint",
            "lint",
            _noop,
            kinds=("python",),
            extension="acme.brand",
            claims=(Claim("test", ignore=("E501",)),),
        )
    )
    first = per_file_ignores(("python",))
    assert first == per_file_ignores(("python",))  # stable across renders
    entries = dict(first)
    assert entries["packages/*/tests/**/test_*.py"] == ("D1", "E501")
    assert entries["packages/*/tests/**"] == ("D1",)
    assert entries["tests/**/test_*.py"] == ("D1", "E501")  # the workspace's own unit
    # The native kind's test patterns name C++ files a python claim
    # never reads, so they render no ignore; its tests directory does,
    # in the one entry the python kind's shares.
    native = per_file_ignores(("cpp-conan",))
    assert not any(pattern.endswith(".cpp") for pattern, _ in native)
    assert dict(native)["packages/*/tests/**"] == ("D1",)


def test_this_workspace_renders_its_ignores_from_the_claims() -> None:
    from livery.workshop._identity import project_facts
    from livery.workshop._templates import render_injections

    root = Path(__file__).resolve().parents[3]
    injected = render_injections(root, project_facts(root))
    composed = injected["fragments"]["pyproject.toml"]
    assert '"packages/*/tests/**" = ["D1"]' in composed
    assert '"tests/**" = ["D1"]' in composed
    rendered = (root / "pyproject.toml").read_text()
    assert '"packages/*/tests/**" = ["D1"]' in rendered
    assert (
        '"tests/**" = ["D1"]'
        not in rendered.split("[tool.ruff.lint.per-file-ignores]")[0]
    )
