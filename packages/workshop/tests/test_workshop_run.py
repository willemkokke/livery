"""fm run: the refusals first, then the development build and the run phase."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from livery.footman import Failed
from livery.workshop import _checks, _extensions, _run
from livery.workshop._checks import CheckRecord, GateContext, register_check
from livery.workshop._declaration import DeclarationError
from livery.workshop._packages import Package, discover_packages
from livery.workshop._run import RECORD, start
from workshop_extension_fakes import fake_extensions, fake_steps

#: The fixture's package-level extension: two executables, and a run main
#: that starts the chosen one.
NATIVE = """\
[extension]
levels = ["package"]

[queries]
executables = "{package}._steps:executables"

[phases.run]
main = "{package}._steps:main"
"""

STEPS = """\
def executables(package):
    return ("serve", "tool")


def main(ctx):
    print("started", ctx.executable, list(ctx.arguments))
    ctx.exit_code = 3
"""


#: What the build checks here judge: the library holds python through its
#: kind, and the app lists the fixture extension.
BOTH = ("python", "acme.native")


def _member(root: Path, name: str, extensions: str, depends: str = "") -> Path:
    directory = root / "packages" / name
    directory.mkdir(parents=True)
    contract = f'kind = "python"\nname = "acme-{name}"\nextensions = {extensions}\n'
    if depends:
        contract += f'\n[[depends]]\npath = "packages/{depends}"\n'
    (directory / "workshop.toml").write_text(contract)
    (directory / "pyproject.toml").write_text(
        f'[project]\nname = "acme-{name}"\nversion = "0.1.0"\n'
    )
    (directory / "source.txt").write_text("one\n")
    return directory


def _workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, git: bool = True
) -> Path:
    """An app listing the fixture extension, on a library it depends on."""
    root = tmp_path / "ws"
    root.mkdir()
    if git:
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / "workshop.toml").write_text("[workspace]\nextensions = []\n")
    _member(root, "lib", "[]")
    _member(root, "app", '["acme.native"]', depends="lib")
    fake_extensions(tmp_path, monkeypatch, native=NATIVE)
    fake_steps(tmp_path, "native", STEPS)
    monkeypatch.chdir(root)
    return root


@pytest.fixture
def built() -> Iterator[list[str]]:
    """A counting build check over the fixture's packages: the paths it built.

    The library lists nothing, so it holds python through its kind; the
    app lists the fixture extension alone.
    """
    state = _checks.snapshot()
    calls: list[str] = []

    def count(ctx: GateContext) -> None:
        assert ctx.package is not None
        calls.append(ctx.package.path)

    register_check(
        CheckRecord("acme", "build", count, scope=_checks.PACKAGE, extensions=BOTH)
    )
    yield calls
    _checks.restore(state)


# The refusals first.


@pytest.mark.parametrize(
    ("target", "where", "refusal"),
    [
        ("ghost", "", "no package is named ghost: the packages are app, lib"),
        (
            "ghost:serve",
            "packages/app",
            "no package is named ghost: the packages are app, lib",
        ),
        (
            "app:ghost",
            "",
            "packages/app has no executable named ghost; its executables are serve,"
            " tool",
        ),
        (
            "app",
            "",
            "packages/app has the executables serve, tool; name one: `fm run"
            " app:serve`",
        ),
        (
            "",
            "",
            "name a package, `fm run <package>[:<executable>]`, or run it inside a"
            " package's directory: the packages are app, lib",
        ),
        (
            "lib",
            "",
            "packages/lib has no executable: no extension of its set answers one",
        ),
    ],
    ids=[
        "no-package",
        "no-package-inside-one",
        "no-executable",
        "several",
        "nothing-named",
        "none-answered",
    ],
)
def test_a_target_naming_nothing_that_runs_refuses_naming_what_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    built: list[str],
    target: str,
    where: str,
    refusal: str,
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    with pytest.raises(Failed) as raised:
        start(root, target, (), root / where)
    assert str(raised.value) == refusal
    assert built == []


def test_executables_with_no_run_main_to_start_them_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(
        tmp_path,
        monkeypatch,
        odd=NATIVE.replace(
            'main = "{package}._steps:main"', 'pre = "{package}._steps:main"'
        ),
    )
    fake_steps(tmp_path, "odd", STEPS)
    with pytest.raises(DeclarationError) as raised:
        _extensions.declaration("acme.odd")
    path = tmp_path / "site" / "acme" / "odd" / "extension.toml"
    assert str(raised.value) == (
        f"{path}: queries.executables names the executables fm run starts, and"
        " [phases.run] names no main to start them; add main to [phases.run]"
    )


def test_a_failed_build_records_nothing_and_the_next_run_builds_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, built: list[str]
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    real = _checks.checks_by_name()["build.acme"].run

    def refuse(ctx: GateContext) -> None:
        real(ctx)
        raise Failed("the library does not compile")

    register_check(
        CheckRecord("acme", "build", refuse, scope=_checks.PACKAGE, extensions=BOTH)
    )
    with pytest.raises(Failed):
        start(root, "app:serve", (), root)
    assert built == ["packages/lib"]
    assert not (root / RECORD).exists()
    register_check(
        CheckRecord("acme", "build", real, scope=_checks.PACKAGE, extensions=BOTH)
    )
    assert start(root, "app:serve", (), root) == 3
    assert built == ["packages/lib", "packages/lib", "packages/app"]


def test_a_checkout_git_cannot_read_builds_every_member_each_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    built: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _workspace(tmp_path, monkeypatch, git=False)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    assert start(root, "app:serve", (), root) == 3
    assert start(root, "app:serve", (), root) == 3
    assert built == ["packages/lib", "packages/app"] * 2
    assert "run: git cannot read this checkout, so every member builds:" in (
        capsys.readouterr().err
    )


def test_inside_a_package_the_workspace_is_the_root_that_holds_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    app = root / "packages" / "app"
    (app / "src").mkdir()
    assert _extensions.workspace_root(app) == root
    assert _extensions.workspace_root(app / "src") == root
    # A group directory holds packages one level further down.
    group = _member(root / "packages", "widgets", "[]").parent
    grouped = group.parent / "extensions" / "widgets"
    grouped.parent.mkdir()
    group.rename(grouped)
    assert _extensions.workspace_root(grouped) == root
    # Without a workspace above it, a package's contract is the nearest.
    alone = tmp_path / "alone" / "packages" / "x"
    alone.mkdir(parents=True)
    (alone / "workshop.toml").write_text('kind = "python"\n')
    assert _extensions.workspace_root(alone) == alone


# The development build and the run phase.


def test_fm_run_builds_the_closure_dependencies_first_then_starts_the_executable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    built: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    assert start(root, "app:serve", ("--port", "8"), root) == 3
    assert built == ["packages/lib", "packages/app"]
    assert "started serve ['--port', '8']\n" in capsys.readouterr().out
    # Nothing changed: it starts at once and no build check runs.
    built.clear()
    assert start(root, "app:serve", (), root) == 3
    assert built == []
    # The library changed: it builds first, then the app that needs it.
    (root / "packages" / "lib" / "source.txt").write_text("two\n")
    assert start(root, "app:serve", (), root) == 3
    assert built == ["packages/lib", "packages/app"]
    # The app alone changed: the library stays as it was built.
    built.clear()
    (root / "packages" / "app" / "source.txt").write_text("two\n")
    assert start(root, "app:tool", (), root) == 3
    assert built == ["packages/app"]
    # Inside the app's directory the package may be left out.
    assert start(root, "tool", (), root / "packages" / "app") == 3
    assert "started tool []\n" in capsys.readouterr().out


def test_a_member_no_build_check_judges_is_never_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, built: list[str]
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    register_check(
        CheckRecord(
            "acme",
            "build",
            lambda ctx: None,
            scope=_checks.PACKAGE,
            extensions=("cmake",),
        )
    )
    app, packages = _app_and_packages(root)
    assert _run.develop(root, app, packages) == []
    assert not (root / RECORD).exists()


def _app_and_packages(root: Path) -> tuple[Package, tuple[Package, ...]]:
    packages = discover_packages(root)
    (app,) = [package for package in packages if package.member == "app"]
    return app, packages


def test_the_verb_hands_the_words_after_the_dashes_and_exits_with_the_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    built: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(_run, "passthrough", lambda: ["-v"])
    assert _run.run("app:serve") == 3
    assert "started serve ['-v']\n" in capsys.readouterr().out
    # Completion offers each executable by its package, and a package
    # with one executable by its name alone.
    assert _run._targets() == ["app:serve", "app:tool"]  # pyright: ignore[reportPrivateUsage]
    fake_steps(tmp_path, "native", STEPS.replace('("serve", "tool")', '("serve",)'))
    assert _run._targets() == ["app", "app:serve"]  # pyright: ignore[reportPrivateUsage]


def test_outside_a_workspace_the_verb_refuses_and_completion_offers_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_run, "passthrough", lambda: [])
    with pytest.raises(Failed) as raised:
        _run.run("app")
    assert str(raised.value) == (
        "no workspace: no workshop.toml above the working directory"
    )
    assert _run._targets() == []  # pyright: ignore[reportPrivateUsage]
    # A workspace with no packages names that in a refusal.
    (tmp_path / "workshop.toml").write_text("[workspace]\nextensions = []\n")
    with pytest.raises(Failed) as raised:
        start(tmp_path, "app", (), tmp_path)
    assert str(raised.value) == "no package is named app: the workspace has no packages"


#: A second extension in the app's set: compatible with the fixture's, its
#: run steps say when they run, and its main must not run.
EXTRA = """\
[extension]
levels = ["package"]
compatible = ["acme.native"]

[phases.run]
pre = "{package}._steps:pre"
main = "{package}._steps:main"
post = "{package}._steps:post"
"""

EXTRA_STEPS = """\
def pre(ctx):
    print("extra.pre")


def main(ctx):
    print("extra.main")


def post(ctx):
    print("extra.post")
"""


def _with_extra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: str, steps: str
) -> Path:
    root = _workspace(tmp_path, monkeypatch)
    fake_extensions(tmp_path, monkeypatch, native=NATIVE, extra=extra)
    fake_steps(tmp_path, "native", STEPS)
    fake_steps(tmp_path, "extra", steps)
    app = root / "packages" / "app" / "workshop.toml"
    app.write_text(
        app.read_text().replace('["acme.native"]', '["acme.extra", "acme.native"]')
    )
    return root


def test_the_owners_main_alone_runs_between_every_pre_and_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    built: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _with_extra(tmp_path, monkeypatch, EXTRA, EXTRA_STEPS)
    assert start(root, "app:serve", (), root) == 3
    lines = [
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith(("extra", "started"))
    ]
    assert lines == ["extra.pre", "started serve []", "extra.post"]
    # With one executable, the package alone names it.
    fake_steps(tmp_path, "native", STEPS.replace('("serve", "tool")', '("serve",)'))
    assert start(root, "app", (), root) == 3


def test_answers_or_a_run_phase_that_cannot_run_refuse_through_fm_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, built: list[str]
) -> None:
    # Two extensions of the app's set answer one executable.
    twice = EXTRA.replace(
        "[phases.run]",
        '[queries]\nexecutables = "{package}._steps:executables"\n\n[phases.run]',
    )
    root = _with_extra(
        tmp_path,
        monkeypatch,
        twice,
        EXTRA_STEPS + "\n\ndef executables(package):\n    return ('serve',)\n",
    )
    with pytest.raises(Failed) as raised:
        start(root, "app:serve", (), root)
    assert str(raised.value) == (
        "packages/app: executables: acme.extra and acme.native both answer serve;"
        " one extension of a package answers each"
    )
    assert _run._targets() == []  # pyright: ignore[reportPrivateUsage]
    # The run phase reads a key nothing in the set provides.
    reads = EXTRA.replace('post = "{package}._steps:post"', 'reads = ["port"]')
    (tmp_path / "again").mkdir()
    root = _with_extra(tmp_path / "again", monkeypatch, reads, EXTRA_STEPS)
    with pytest.raises(Failed) as raised:
        start(root, "app:serve", (), root)
    assert str(raised.value) == (
        "packages/app: the run phase: acme.extra reads port, which no extension of"
        " the package provides"
    )


def test_the_closure_holds_each_dependency_once_however_it_is_reached() -> None:
    from livery.workshop._graph import dependencies_closure, order_topologically
    from livery.workshop._packages import Edge

    def member(name: str, *needs: str) -> Package:
        return Package(
            directory=Path(name),
            path=f"packages/{name}",
            name=name,
            kind="python",
            depends=tuple(
                Edge(path=f"packages/{need}", kind="build", floor="") for need in needs
            ),
        )

    packages = (member("app", "util", "lib"), member("lib"), member("util", "lib"))
    closure = order_topologically(dependencies_closure(packages, {"packages/app"}))
    assert [package.member for package in closure] == ["lib", "util", "app"]
