"""Lifecycle phases: the refusals first, then the walk of pre, main and post."""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.workshop import _extensions
from livery.workshop._declaration import DeclarationError
from livery.workshop._packages import Package
from livery.workshop._phases import PhaseContext, PhaseError, run_phase
from workshop_extension_fakes import (
    fake_extensions,
    fake_steps,
    package_contract,
    root_contract,
)

#: A package-level extension's identity, the start of every fake here.
PACKAGE = '[extension]\nlevels = ["package"]\n'

#: Steps that say when they run; a post says what failed before it.
SAYS = """\
def pre(ctx):
    print("{name}.pre")


def main(ctx):
    print("{name}.main")


def post(ctx):
    print(f"{name}.post failed={{ctx.failed}} by={{ctx.failed_extension or '-'}}")
"""


def _steps(name: str, *, provides: str = "", reads: str = "", only: str = "") -> str:
    """A declaration adding *name*'s steps to the build phase."""
    steps = (only,) if only else ("pre", "main", "post")
    lines = [f'{step} = "{{package}}._steps:{step}"' for step in steps]
    if provides:
        lines.append(f"provides = {provides}")
    if reads:
        lines.append(f"reads = {reads}")
    return PACKAGE + "\n[phases.build]\n" + "\n".join(lines) + "\n"


def _package(root: Path, *extensions: str) -> Package:
    return Package(
        directory=root / "packages" / "core",
        path="packages/core",
        name="core",
        kind="python",
        depends=(),
        extensions=extensions,
    )


def _workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **declared: str
) -> Package:
    """Fakes for *declared*, each saying when it runs, listed by one package."""
    names = [f"acme.{name}" for name in declared]
    compatible = {
        name: [other for other in names if other != f"acme.{name}"] for name in declared
    }
    fake_extensions(
        tmp_path,
        monkeypatch,
        **{
            name: text.replace(
                "[phases.build]",
                f"compatible = {compatible[name]!r}\n\n[phases.build]".replace(
                    "'", '"'
                ),
                1,
            )
            for name, text in declared.items()
        },
    )
    for name in declared:
        fake_steps(tmp_path, name, SAYS.format(name=name))
    root_contract(tmp_path, "[]")
    listed = "[" + ", ".join(f'"{name}"' for name in names) + "]"
    package_contract(tmp_path, "core", listed)
    return _package(tmp_path, *names)


# The refusals first.


def test_two_providers_of_one_key_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    package = _workspace(
        tmp_path,
        monkeypatch,
        a=_steps("a", provides='{ wheel = "path" }'),
        b=_steps("b", provides='{ wheel = "path" }'),
    )
    refusal = (
        "the build phase: acme.a and acme.b both provide wheel; one extension of a"
        " package provides a key"
    )
    with pytest.raises(PhaseError) as raised:
        run_phase("build", package, tmp_path)
    assert str(raised.value) == f"packages/core: {refusal}"
    assert capsys.readouterr().out == ""
    assert _extensions.closure_problems(tmp_path) == [f"packages/core: {refusal}"]


def test_a_key_nobody_provides_and_an_order_with_no_start_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _workspace(tmp_path, monkeypatch, a=_steps("a", reads='["wheel"]'))
    with pytest.raises(PhaseError) as raised:
        run_phase("build", package, tmp_path)
    assert str(raised.value) == (
        "packages/core: the build phase: acme.a reads wheel, which no extension"
        " of the package provides"
    )
    package = _workspace(
        tmp_path,
        monkeypatch,
        a=_steps("a", provides='{ x = "str" }', reads='["y"]'),
        b=_steps("b", provides='{ y = "str" }', reads='["x"]'),
    )
    assert _extensions.closure_problems(tmp_path) == [
        "packages/core: the build phase: its steps' order has no start: acme.a"
        " before acme.b before acme.a; remove a key read, a before or an after"
        " among them"
    ]


@pytest.mark.parametrize(
    ("declared", "refusal"),
    [
        (
            PACKAGE + '\n[phases.biuld]\nmain = "{package}._steps:main"\n',
            "phases.biuld names no phase; the phases are create, sync, stamp, build,"
            " prove, publish, replay, clean, run; did you mean 'build'?",
        ),
        (
            PACKAGE + '\n[phases.build]\nreads = ["x"]\n',
            "phases.build names no step: a phase's table takes pre, main or post",
        ),
        (
            '[phases.build]\nmain = "{package}._steps:main"\n',
            "phases.build adds steps to a package's phase, and this extension's"
            " levels are workspace; add 'package' to levels, or drop the table",
        ),
        (
            PACKAGE + '\n[phases.build]\nmain = "{package}._steps:main"\n'
            'provides = { x = "str" }\nreads = ["x"]\n',
            "phases.build.reads names x, which its provides names too; a step reads"
            " what another extension provides",
        ),
        (
            PACKAGE + '\n[phases.build]\nmain = "{package}._steps:mian"\n',
            "phases.build.main names mian, which acme.odd._steps does not define at"
            " its top level; did you mean 'main'?",
        ),
    ],
    ids=["unknown-phase", "no-step", "workspace-level", "reads-its-own", "no-function"],
)
def test_a_phase_table_the_file_alone_shows_wrong_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, declared: str, refusal: str
) -> None:
    fake_extensions(tmp_path, monkeypatch, odd=declared)
    fake_steps(tmp_path, "odd", SAYS.format(name="odd"))
    with pytest.raises(DeclarationError) as raised:
        _extensions.declaration("acme.odd")
    path = tmp_path / "site" / "acme" / "odd" / "extension.toml"
    assert str(raised.value) == f"{path}: {refusal}"


def test_a_post_runs_after_a_failed_main_and_sees_the_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    package = _workspace(tmp_path, monkeypatch, a=_steps("a"), b=_steps("b"))
    fake_steps(
        tmp_path,
        "b",
        SAYS.format(name="b").replace(
            'print("b.main")', 'print("b.main")\n    raise RuntimeError("b broke")'
        ),
    )
    with pytest.raises(RuntimeError, match=r"^b broke$"):
        run_phase("build", package, tmp_path)
    assert capsys.readouterr().out.splitlines() == [
        "a.pre",
        "b.pre",
        "a.main",
        "b.main",
        "b.post failed=True by=acme.b",
        "a.post failed=True by=acme.b",
    ]


def test_a_failed_pre_stops_the_walk_and_only_the_entered_posts_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    package = _workspace(
        tmp_path, monkeypatch, a=_steps("a"), b=_steps("b"), c=_steps("c")
    )
    fake_steps(
        tmp_path,
        "b",
        SAYS.format(name="b").replace(
            'print("b.pre")', 'print("b.pre")\n    raise RuntimeError("b refused")'
        ),
    )
    with pytest.raises(RuntimeError, match=r"^b refused$"):
        run_phase("build", package, tmp_path)
    assert capsys.readouterr().out.splitlines() == [
        "a.pre",
        "b.pre",
        "b.post failed=True by=acme.b",
        "a.post failed=True by=acme.b",
    ]


def test_a_post_failing_after_another_step_is_named_and_the_first_failure_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    package = _workspace(tmp_path, monkeypatch, a=_steps("a"), b=_steps("b"))
    fake_steps(
        tmp_path,
        "a",
        SAYS.format(name="a") + '\n\ndef post(ctx):\n    raise RuntimeError("a too")\n',
    )
    fake_steps(
        tmp_path,
        "b",
        SAYS.format(name="b").replace(
            'print("b.main")', 'raise RuntimeError("b broke")'
        ),
    )
    with pytest.raises(RuntimeError, match=r"^b broke$"):
        run_phase("build", package, tmp_path)
    assert capsys.readouterr().err == (
        "  note: acme.a's step acme.a._steps:post failed as well, after acme.b's:"
        " a too\n"
    )


# The walk.


def test_a_reader_runs_after_its_provider_and_reads_what_it_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    package = _workspace(
        tmp_path,
        monkeypatch,
        a=_steps("a", reads='["wheel"]', only="main"),
        z=_steps("z", provides='{ wheel = "path" }', only="main"),
    )
    fake_steps(
        tmp_path,
        "z",
        "from pathlib import Path\n\n\ndef main(ctx):\n"
        '    ctx.provide("wheel", Path("dist") / "z.whl")\n',
    )
    fake_steps(
        tmp_path, "a", 'def main(ctx):\n    print("a read", ctx.read("wheel"))\n'
    )
    ctx = run_phase("build", package, tmp_path)
    assert isinstance(ctx, PhaseContext)
    assert capsys.readouterr().out == f"a read {Path('dist') / 'z.whl'}\n"
    assert not ctx.failed and ctx.failure is None and ctx.failed_extension == ""
    # A package whose set adds no step to a phase runs nothing, whoever
    # answers for the declarations.
    lookup = _extensions.installed_declaration
    assert run_phase("stamp", package, tmp_path, lookup=lookup).failed is False


@pytest.mark.parametrize(
    ("body", "refusal"),
    [
        (
            'ctx.provide("other", "x")',
            "acme.z provides other in the build phase, and its [phases] table does"
            " not name it in provides",
        ),
        (
            'ctx.provide("wheel", "dist/z.whl")',
            "acme.z provides wheel as str, and its [phases] table declares it path",
        ),
        (
            'ctx.read("wheel")',
            "acme.z reads wheel in the build phase, and its [phases] table does not"
            " name it in reads",
        ),
    ],
    ids=["undeclared-provide", "wrong-type", "undeclared-read"],
)
def test_a_step_misusing_the_context_refuses_naming_its_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str, refusal: str
) -> None:
    package = _workspace(
        tmp_path,
        monkeypatch,
        z=_steps("z", provides='{ wheel = "path" }', only="main"),
    )
    fake_steps(tmp_path, "z", f"def main(ctx):\n    {body}\n")
    with pytest.raises(PhaseError) as raised:
        run_phase("build", package, tmp_path)
    assert str(raised.value) == refusal


def test_a_read_its_provider_never_wrote_refuses_naming_the_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _workspace(
        tmp_path,
        monkeypatch,
        a=_steps("a", reads='["wheel"]', only="main"),
        z=_steps("z", provides='{ wheel = "path" }', only="main"),
    )
    fake_steps(tmp_path, "z", "def main(ctx):\n    del ctx\n")
    fake_steps(tmp_path, "a", 'def main(ctx):\n    ctx.read("wheel")\n')
    with pytest.raises(PhaseError) as raised:
        run_phase("build", package, tmp_path)
    assert str(raised.value) == (
        "acme.a reads wheel, which acme.z has not provided in the build phase"
    )


@pytest.mark.parametrize(
    ("kind", "good", "bad"),
    [
        ("str", '"a"', "1"),
        ("int", "3", "True"),
        ("bool", "False", "0"),
        ("strs", '["a", "b"]', '["a", 1]'),
        ("paths", '(Path("a"),)', '["a"]'),
        ("table", '{"a": 1}', '["a"]'),
    ],
)
def test_each_context_type_takes_its_values_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, good: str, bad: str
) -> None:
    package = _workspace(
        tmp_path,
        monkeypatch,
        z=_steps("z", provides=f'{{ value = "{kind}" }}', only="main"),
    )
    source = (
        'from pathlib import Path\n\n\ndef main(ctx):\n    ctx.provide("value", {})\n'
    )
    fake_steps(tmp_path, "z", source.format(good))
    run_phase("build", package, tmp_path)
    fake_steps(tmp_path, "z", source.format(bad))
    with pytest.raises(PhaseError, match=f"declares it {kind}$"):
        run_phase("build", package, tmp_path)
