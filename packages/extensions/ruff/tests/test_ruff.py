"""The ruff extension: unlisted it does nothing; listed, it runs and configures ruff."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path

import pytest

import livery.toolroom.tools as tools
from livery.footman import Failed
from livery.workshop import GateContext, Package
from livery.workshop import _checks as registry

ROOT = Path(__file__).resolve().parents[4]

CONTRACT = (
    "[workspace]\n"
    'name = "acme"\n'
    'namespace = "acme"\n'
    'authors = [{ name = "Acme", email = "dev@acme.test" }]\n'
    'copyright-year = "2026"\n'
)


@pytest.fixture
def registered() -> Iterator[None]:
    """Ruff's checks registered as the mount registers a listed extension's."""
    from livery.workshop._extensions import declaration, register_declared

    state = registry.snapshot()
    found = declaration("ruff")
    assert found is not None
    register_declared("ruff", found.additions)
    try:
        yield
    finally:
        registry.restore(state)


def _workspace(root: Path, extensions: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "workshop.toml").write_text(CONTRACT + f"extensions = {extensions}\n")
    return root


# The refusal first: installed and not listed, the extension does nothing.


def test_unlisted_it_registers_no_check_requires_no_tool_and_writes_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.footman import _registry as footman_registry
    from livery.workshop._checks import checks_by_name, tools_for_kind
    from livery.workshop._extensions import mount_extensions
    from livery.workshop._shipped_files import deliver

    root = _workspace(tmp_path / "ws", "[]")
    monkeypatch.setattr(
        "livery.workshop._extensions.workspace_root", lambda start=None: root
    )
    state = registry.snapshot()
    try:
        with footman_registry.capture():
            assert mount_extensions(root) == ()
        assert "format.ruff" not in checks_by_name()
        assert "ruff" not in {tool for tool, _ in tools_for_kind("python")}
        deliver(root)
        assert not (root / "ruff.toml").exists()
        # Listed, the mount registers both checks under the listed name,
        # the tool joins the profile, and the sync writes the file.
        (root / "workshop.toml").write_text(CONTRACT + 'extensions = ["ruff"]\n')
        with footman_registry.capture():
            assert mount_extensions(root) == ("ruff",)
        checks = checks_by_name()
        assert {checks[name].extension for name in ("format.ruff", "lint.ruff")} == {
            "ruff"
        }
        assert "ruff" in {tool for tool, _ in tools_for_kind("python")}
        deliver(root)
        written = (root / "ruff.toml").read_text()
        assert 'cache-dir = ".workshop/.cache/ruff"' in written
        assert "charliermarsh.ruff" in (root / ".vscode/settings.json").read_text()
    finally:
        registry.restore(state)


# The checks: ruff's words, which the workshop runs over the paths a run reaches.


class _Ruff:
    """ruff's toolroom handle, standing in: each call's words recorded."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *argv: str) -> None:
        self.calls.append(argv)


def _run(
    name: str,
    root: Path,
    paths: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
    *,
    fix: bool = False,
    safe: bool = False,
    arguments: tuple[str, ...] = (),
) -> None:
    """Run the check *name* over *paths* through the workshop, judging or fixing."""
    monkeypatch.setattr(registry, "scoped_paths", lambda ctx, check: paths)
    record = registry.check_for(name)
    body = record.fix if fix else record.run
    assert body is not None
    body(GateContext(root=root, packages=(), safe=safe, arguments=arguments))


def test_each_check_hands_ruff_its_words_in_each_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: None
) -> None:
    ruff = _Ruff()
    monkeypatch.setattr(tools, "ruff", ruff)
    member = Package(
        directory=tmp_path / "packages" / "one",
        path="packages/one",
        name="acme-one",
        kind="python",
        depends=(),
    )
    (member.directory / "src").mkdir(parents=True)
    ctx = GateContext(root=tmp_path, packages=(member,))
    for name in ("format.ruff", "lint.ruff"):
        record = registry.check_for(name)
        record.run(ctx)
        assert record.fix is not None
        record.fix(ctx)
        record.fix(GateContext(root=tmp_path, packages=(member,), safe=True))
    # Every member reached: the whole, a call with no path, ruff's own walk.
    assert ruff.calls == [
        ("format", "--check", "--force-exclude"),
        ("format", "--force-exclude"),
        ("format", "--force-exclude"),
        ("check", "--force-exclude"),
        ("check", "--fix", "--force-exclude"),
        ("check", "--fix", "--unfixable=F401", "--force-exclude"),
    ]
    # The words after -- on a check's own verb reach its call, in each mode.
    ruff.calls.clear()
    ctx = GateContext(root=tmp_path, packages=(member,), arguments=("--statistics",))
    for name in ("format.ruff", "lint.ruff"):
        record = registry.check_for(name)
        record.run(ctx)
        assert record.fix is not None
        record.fix(ctx)
    assert [call[-1] for call in ruff.calls] == ["--statistics"] * 4


def test_the_words_after_the_dashes_reach_ruff_before_the_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: None
) -> None:
    # The file selects F alone, so the long line passes until the words
    # select E501 too; the formatter wraps the list only at the narrower
    # line length the words give it.
    (tmp_path / "ruff.toml").write_text('[lint]\nselect = ["F"]\n')
    wide = tmp_path / "wide.py"
    wide.write_text(f"value = {'x' * 100!r}\n")
    _run("lint.ruff", tmp_path, (str(wide),), monkeypatch)
    with pytest.raises(Failed, match="exited"):
        _run(
            "lint.ruff",
            tmp_path,
            (str(wide),),
            monkeypatch,
            arguments=("--extend-select", "E501"),
        )
    listed = tmp_path / "listed.py"
    listed.write_text("x = [1, 2, 3, 4, 5, 6]\n")
    _run("format.ruff", tmp_path, (str(listed),), monkeypatch)
    with pytest.raises(Failed, match="exited"):
        _run(
            "format.ruff",
            tmp_path,
            (str(listed),),
            monkeypatch,
            arguments=("--line-length", "10"),
        )


def test_a_foreign_file_a_run_names_reaches_no_call(
    tmp_path: Path, registered: None
) -> None:
    # The run names a markdown file and a python file; the check's claims
    # take the python file alone, so ruff never reads the markdown one.
    notes = tmp_path / "notes.md"
    notes.write_text("#Heading\n")
    module = tmp_path / "mod.py"
    module.write_text("x=1\n")
    ctx = GateContext(
        root=tmp_path,
        packages=(),
        files=("notes.md", "mod.py"),
        catalogue={".": (("notes.md", "documentation"), ("mod.py", "source"))},
    )
    record = registry.check_for("format.ruff")
    assert record.fix is not None
    record.fix(ctx)
    assert module.read_text() == "x = 1\n"
    assert notes.read_text() == "#Heading\n"


def test_a_named_file_follows_the_excludes_a_found_one_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: None
) -> None:
    # The gate names each file of a run handed paths; ruff would format
    # a named example and lint a named vendored file without the flag.
    (tmp_path / "ruff.toml").write_text(
        'extend-exclude = ["vendor/**"]\n[format]\nexclude = ["examples/**"]\n'
    )
    (tmp_path / "examples").mkdir()
    shown = tmp_path / "examples" / "shown.py"
    shown.write_text("x=1\n")
    (tmp_path / "vendor").mkdir()
    vendored = tmp_path / "vendor" / "kept.py"
    vendored.write_text("import os\n")
    _run("format.ruff", tmp_path, (str(shown),), monkeypatch, fix=True)
    _run("lint.ruff", tmp_path, (str(vendored),), monkeypatch, fix=True)
    assert shown.read_text() == "x=1\n"
    assert vendored.read_text() == "import os\n"


def test_safe_fix_keeps_an_import_an_edit_in_flight_added(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: None
) -> None:
    # The unused import survives, the sortable one still heals, and a
    # plain fix, the stronger one, removes it. The rules are the test's
    # own: a file with no configuration above it reads the one where
    # ruff runs.
    (tmp_path / "ruff.toml").write_text('[lint]\nselect = ["F", "I"]\n')
    victim = tmp_path / "wip.py"
    victim.write_text("import sys\nimport os\n\nprint(sys.path)\n")
    # Lint still reports the withheld import by exiting non-zero; the
    # pin is the healed bytes, not the exit.
    with contextlib.suppress(Failed):
        _run("lint.ruff", tmp_path, (str(victim),), monkeypatch, fix=True, safe=True)
    healed = victim.read_text()
    assert "import os" in healed
    assert healed.index("import os") < healed.index("import sys")
    with contextlib.suppress(Failed):
        _run("lint.ruff", tmp_path, (str(victim),), monkeypatch, fix=True)
    assert "import os" not in victim.read_text()
    # A file safe-fix heals completely passes.
    sortable = tmp_path / "sortable.py"
    sortable.write_text("import sys\nimport os\n\nprint(os.sep, sys.path)\n")
    _run("lint.ruff", tmp_path, (str(sortable),), monkeypatch, fix=True, safe=True)
    assert sortable.read_text().startswith("import os\nimport sys\n")
    # The formatter rewrites, and in check mode reports instead.
    messy = tmp_path / "messy.py"
    messy.write_text("x=1\n")
    with pytest.raises(Failed, match="exited"):
        _run("format.ruff", tmp_path, (str(messy),), monkeypatch)
    _run("format.ruff", tmp_path, (str(messy),), monkeypatch, fix=True)
    assert messy.read_text() == "x = 1\n"


def test_an_edit_the_hook_sees_is_formatted_and_keeps_its_new_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: None
) -> None:
    from livery.workshop._hooks import HookEvent, ToolInput, post_edit

    (tmp_path / "workshop.toml").write_text("[workspace]\n")
    monkeypatch.chdir(tmp_path)
    # A desk's edit: the runner's variables refuse a fix.
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    victim = tmp_path / "wip.py"
    victim.write_text("import os\nx=1\n")
    post_edit(HookEvent(tool_input=ToolInput(file_path=str(victim))))
    healed = victim.read_text()
    assert "import os" in healed  # the not-yet-used import survives
    assert "x = 1" in healed  # everything else heals


# The file: the workshop writes it, and a bare ruff reads what the gate reads.


def test_the_root_tests_directory_is_named_only_while_it_exists(tmp_path: Path) -> None:
    import tomllib

    from livery.workshop._shipped_files import deliver

    bare = _workspace(tmp_path / "bare", '["ruff"]')
    deliver(bare)
    assert "tests" not in tomllib.loads((bare / "ruff.toml").read_text())["src"]
    tested = _workspace(tmp_path / "tested", '["ruff"]')
    (tested / "tests").mkdir()
    deliver(tested)
    assert "tests" in tomllib.loads((tested / "ruff.toml").read_text())["src"]


def test_this_repository_s_configuration_is_what_a_bare_ruff_reads() -> None:
    written = (ROOT / "ruff.toml").read_text()
    # The claims' ignores, a group member's included, and the repository's own.
    assert '"packages/**/tests/**" = ["D1"]' in written
    assert '"tests/**" = ["D1"]' in written
    assert "[lint.extend-per-file-ignores]" in written

    def settings(path: str) -> str:
        shown = tools.ruff.opts(cwd=ROOT, nofail=True, recorded=False)(
            "check", "--show-settings", path
        )
        return shown.stdout

    root_file = settings("packages/workshop/src/livery/workshop/__init__.py")
    assert f'Settings path: "{ROOT / "ruff.toml"}"' in root_file
    cache = f'cache_dir = "{ROOT / ".workshop" / ".cache" / "ruff"}"'
    assert cache in root_file
    # A package's own file extends the root's and shares its cache.
    package_file = settings("packages/footman/src/livery/footman/__init__.py")
    assert f'Settings path: "{ROOT / "packages/footman/ruff.toml"}"' in package_file
    assert cache in package_file
