"""Lifecycle phases: the refusals first, then the walk of pre, main and post."""

from __future__ import annotations

from collections.abc import Iterator
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
    fake_extensions(tmp_path, monkeypatch, **declared)
    for name in declared:
        fake_steps(tmp_path, name, SAYS.format(name=name))
    root_contract(tmp_path, "[]")
    listed = "[" + ", ".join(f'"{name}"' for name in names) + "]"
    package_contract(tmp_path, "core", listed)
    return _package(tmp_path, *names)


# The refusals first.


def _engine_reader(provides: str = "") -> str:
    """A declaration whose build main reads the engine's epoch."""
    text = PACKAGE + '\n[phases.build]\nmain = "{package}._steps:main"\n'
    text += 'reads = ["epoch"]\n'
    return text + (f"provides = {provides}\n" if provides else "")


def test_an_extension_providing_a_key_the_engine_gives_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    own = PACKAGE + '\n[phases.build]\nmain = "{package}._steps:main"\n'
    fake_extensions(tmp_path, monkeypatch, own=own + 'provides = { epoch = "int" }\n')
    fake_steps(tmp_path, "own", "def main(ctx):\n    ctx.provide('epoch', 1)\n")
    with pytest.raises(PhaseError) as raised:
        run_phase(
            "build", _package(tmp_path, "acme.own"), tmp_path, inputs={"epoch": 0}
        )
    assert str(raised.value) == (
        "packages/core: the build phase: acme.own provides epoch, which the engine"
        " running the phase provides; an extension reads it"
    )


def test_the_engine_refuses_an_input_the_phase_does_not_take(tmp_path: Path) -> None:
    package = _package(tmp_path)
    with pytest.raises(
        PhaseError,
        match="gives wheel to the build phase, which takes configuration, epoch,"
        " isolated from it",
    ):
        run_phase("build", package, tmp_path, inputs={"wheel": "x"})
    with pytest.raises(
        PhaseError,
        match="gives epoch to the build phase as str, which the phase takes as int",
    ):
        run_phase("build", package, tmp_path, inputs={"epoch": "0"})
    with pytest.raises(
        PhaseError, match="the run phase, which takes arguments, executable from it"
    ):
        run_phase("run", package, tmp_path, inputs={"epoch": 0})
    with pytest.raises(PhaseError, match="the sync phase, which takes nothing from it"):
        run_phase("sync", package, tmp_path, inputs={"epoch": 0})


def test_the_lifecycle_engines_inputs_are_the_keys_their_phases_declare(
    tmp_path: Path,
) -> None:
    # What the train, fm sync, fm clean and fm run hand each phase: each
    # input is a key the phase declares, of the declared type, so a
    # package without steps takes it and runs nothing.
    package = _package(tmp_path)
    given: list[tuple[str, dict[str, object]]] = [
        ("sync", {}),
        ("clean", {}),
        ("stamp", {"version": "1.2.3"}),
        ("build", {"epoch": 7}),
        ("prove", {"resolution": "floor", "release-dirs": [Path("dist")]}),
        ("publish", {"version": "1.2.3", "registry-target": {"url": "x"}}),
        (
            "replay",
            {
                "version": "1.2.3",
                "tree": Path("tree"),
                "interpreter": "3.14",
                "index": "https://index",
                "extras": "",
            },
        ),
        ("run", {}),
    ]
    for phase, inputs in given:
        ctx = run_phase(phase, package, tmp_path, inputs=inputs)
        assert (ctx.phase, ctx.failed) == (phase, False)
        for key, value in inputs.items():
            assert ctx.result(key) == value


def test_a_step_reading_a_key_the_engine_did_not_give_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(tmp_path, monkeypatch, reader=_engine_reader())
    fake_steps(tmp_path, "reader", "def main(ctx):\n    ctx.read('epoch')\n")
    with pytest.raises(
        PhaseError,
        match=r"acme\.reader reads epoch, which the engine running the build phase"
        r" did not give it",
    ):
        run_phase("build", _package(tmp_path, "acme.reader"), tmp_path)


def test_a_step_reads_the_engines_keys_and_the_engine_reads_back_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(tmp_path, monkeypatch, builder=_engine_reader('{ dist = "path" }'))
    fake_steps(
        tmp_path,
        "builder",
        "from pathlib import Path\n\n\n"
        "def main(ctx):\n    ctx.provide('dist', Path(str(ctx.read('epoch'))))\n",
    )
    package = _package(tmp_path, "acme.builder")
    ctx = run_phase("build", package, tmp_path, inputs={"epoch": 7})
    assert ctx.result("dist") == Path("7")
    assert ctx.result("epoch") == 7
    assert ctx.result("absent") is None


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
        " before acme.b before acme.a; remove a key read among them"
    ]


@pytest.mark.parametrize(
    ("declared", "refusal"),
    [
        (
            PACKAGE + '\n[phases.biuld]\nmain = "{package}._steps:main"\n',
            "phases.biuld names no phase; the phases are sync, clean, build, format,"
            " lint, typecheck, typecomplete, examples, drift, provenance, layering,"
            " test, prove, stamp, publish, replay, configure, run; did you mean"
            " 'build'?",
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


# The declared phases: the base's from its contract file, an extension's
# from its declaration file, and the refusals of a phase nobody declares.


@pytest.fixture
def restored_checks() -> Iterator[None]:
    """Put the check registry back after a mount registered steps into it."""
    from livery.workshop import _checks

    state = _checks.snapshot()
    yield
    _checks.restore(state)


#: A workspace-level extension declaring a workspace-level phase, docs,
#: with one step of its own.
DECLARES_DOCS = """\
[phases.docs]
level = "workspace"
gated = true
after = []
verb = true
keys = { site = "path" }

[phases.docs.pages]
tools = ["acme-pages"]
main = ["acme-pages", "build", "{paths}"]
"""

#: A step into the docs phase, declared by another extension.
ADDS_DOCS_STEP = """\
[phases.docs.index]
tools = ["acme-index"]
main = ["acme-index", "{paths}"]
"""


def test_the_bases_phases_are_read_from_its_contract_file() -> None:
    from livery.workshop._phases import (
        PACKAGE_LEVEL,
        TYPES,
        WORKSPACE_LEVEL,
        PhaseSpec,
        base_phases,
        declared_phases,
        lifecycle_phases,
    )

    found = base_phases()
    assert list(found) == [
        "sync",
        "clean",
        "build",
        "format",
        "lint",
        "typecheck",
        "typecomplete",
        "examples",
        "drift",
        "provenance",
        "layering",
        "test",
        "prove",
        "stamp",
        "publish",
        "replay",
        "configure",
        "run",
    ]
    assert found["build"] == PhaseSpec(
        "build",
        {"isolated": "bool", "configuration": "str", "epoch": "int"},
        PACKAGE_LEVEL,
        True,
        ("stamp",),
        True,
    )
    assert found["format"] == PhaseSpec("format", {}, WORKSPACE_LEVEL, True, (), True)
    assert found["stamp"] == PhaseSpec(
        "stamp", {"version": "str"}, PACKAGE_LEVEL, False, (), False
    )
    # The gated phases are the ones with a verb; the lifecycle phases are
    # the package-level ones, in the file's order.
    gated = {name for name, spec in found.items() if spec.gated}
    assert gated == {
        "build",
        "format",
        "lint",
        "typecheck",
        "typecomplete",
        "examples",
        "drift",
        "provenance",
        "layering",
        "test",
    }
    assert {name for name, spec in found.items() if spec.verb} == gated
    assert lifecycle_phases() == (
        "sync",
        "clean",
        "build",
        "prove",
        "stamp",
        "publish",
        "replay",
        "run",
    )
    # The train's order, by after.
    assert {name: spec.after for name, spec in found.items() if spec.after} == {
        "build": ("stamp",),
        "test": ("build",),
        "prove": ("build",),
        "publish": ("prove",),
        "replay": ("publish",),
        "run": ("build",),
    }
    # Each engine's keys, typed with the types a context value has.
    assert {name: dict(spec.keys) for name, spec in found.items() if spec.keys} == {
        "build": {"isolated": "bool", "configuration": "str", "epoch": "int"},
        "test": {"isolated": "bool", "resolution": "str", "release-dirs": "paths"},
        "prove": {"resolution": "str", "release-dirs": "paths"},
        "stamp": {"version": "str"},
        "publish": {"version": "str", "registry-target": "table"},
        "replay": {
            "version": "str",
            "tree": "path",
            "interpreter": "str",
            "index": "str",
            "extras": "str",
        },
        "run": {"executable": "str", "arguments": "strs"},
    }
    for spec in found.values():
        assert set(spec.keys.values()) <= set(TYPES)
        assert spec.extension == "livery.workshop"
    # The base's are among every declared phase, as the base declares them.
    declared = declared_phases()
    assert all(declared[name] == spec for name, spec in found.items())


def test_a_phase_no_mounted_extension_declares_refuses_naming_the_declared_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restored_checks: None
) -> None:
    from livery.footman import _registry as registry
    from livery.workshop import _phases

    # The reader: a step into a phase no declaration names.
    fake_extensions(tmp_path, monkeypatch, odd=ADDS_DOCS_STEP.replace("docs", "dcos"))
    listed = ", ".join(_phases.declared_phases())
    with pytest.raises(DeclarationError) as raised:
        _extensions.declaration("acme.odd")
    path = tmp_path / "site" / "acme" / "odd" / "extension.toml"
    assert str(raised.value) == (
        f"{path}: phases.dcos names no phase; the phases are {listed}"
    )
    # The engine: a phase nobody declares cannot run.
    with pytest.raises(PhaseError) as refused:
        run_phase("dcos", _package(tmp_path), tmp_path)
    assert (
        str(refused.value) == f"dcos is not a declared phase; the phases are {listed}"
    )
    # The mount: a phase an installed extension declares reads anywhere,
    # and a step into it refuses while the workspace does not list the
    # declarer.
    fake_extensions(tmp_path, monkeypatch, site=DECLARES_DOCS, index=ADDS_DOCS_STEP)
    root_contract(tmp_path, '["acme.index"]')
    found = _extensions.declaration("acme.index")
    assert found is not None and [r.name for r in found.additions.checks] == [
        "docs.index"
    ]
    refusal = (
        r"extension 'acme\.index' adds steps to the docs phase, which acme\.site"
        r" declares and the workspace does not list; list acme\.site before"
        r" acme\.index, or drop the steps"
    )
    with pytest.raises(BaseException, match=refusal), registry.capture():
        _extensions.mount_extensions(tmp_path)
    # Listed before it, the declarer mounts first and the step registers.
    from livery.workshop import _checks

    root_contract(tmp_path, '["acme.site", "acme.index"]')
    with registry.capture():
        _extensions.mount_extensions(tmp_path)
    assert _checks.checks_by_name()["docs.index"].extension == "acme.index"


def test_an_extension_declares_a_phase_with_its_engine_keys_level_placement_and_verb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restored_checks: None
) -> None:
    from livery.footman import _registry as registry
    from livery.workshop import _checks, _phases
    from livery.workshop._checks import GateContext
    from livery.workshop._phases import WORKSPACE_LEVEL, PhaseSpec, phase_spec

    # The declared phases are read through the module: each fake set
    # below reads them afresh.
    def declared_phases() -> dict[str, PhaseSpec]:
        return _phases.declared_phases()

    # The refusals first: a declaration off its shape, read from the
    # installed file before any declaration is judged.
    for text, refusal in (
        (
            DECLARES_DOCS.replace('level = "workspace"', 'level = "sometimes"'),
            "phases.docs.level is 'sometimes'; a phase runs at one of package,"
            " workspace",
        ),
        (
            DECLARES_DOCS.replace('{ site = "path" }', '{ site = "folder" }'),
            "phases.docs.keys.site is 'folder'; a key's type is one of str, int,"
            " bool, path, strs, paths, table",
        ),
        (
            DECLARES_DOCS.replace("gated = true", 'gated = "yes"'),
            "phases.docs.gated is true or false",
        ),
        (
            DECLARES_DOCS.replace("after = []", 'after = "build"'),
            "phases.docs.after is a list of the phases this one follows",
        ),
    ):
        fake_extensions(tmp_path, monkeypatch, site=text)
        with pytest.raises(DeclarationError) as raised:
            declared_phases()
        path = tmp_path / "site" / "acme" / "site" / "extension.toml"
        assert str(raised.value) == f"{path}: {refusal}"
    # An after naming no phase, two declarers of one phase, and an order
    # with no start refuse naming the extensions.
    fake_extensions(
        tmp_path, monkeypatch, site=DECLARES_DOCS.replace("after = []", 'after = ["x"]')
    )
    with pytest.raises(DeclarationError) as raised:
        declared_phases()
    assert str(raised.value).startswith(
        "acme.site declares the docs phase after x, which no extension declares;"
        " the phases are sync, clean,"
    )
    fake_extensions(
        tmp_path,
        monkeypatch,
        site='[phases.one]\nlevel = "workspace"\nafter = ["two"]\n'
        '[phases.two]\nlevel = "workspace"\nafter = ["one"]\n',
    )
    with pytest.raises(
        DeclarationError,
        match=r"^the phases' order has no start: one after two after one; remove an"
        r" after among them$",
    ):
        declared_phases()
    # A package-level phase declared with no step declares alone, and a
    # step into it from a workspace-level extension refuses as any does.
    fake_extensions(
        tmp_path, monkeypatch, site='[phases.notes]\nlevel = "package"\ngated = false\n'
    )
    found = _extensions.declaration("acme.site")
    assert found is not None and found.phases == {}
    assert found.phase_specs == {
        "notes": PhaseSpec("notes", {}, "package", False, (), False, "acme.site")
    }
    fake_extensions(
        tmp_path,
        monkeypatch,
        site='[phases.notes]\nlevel = "package"\n'
        '[phases.notes.write]\nmain = "{package}._checks:noop"\n',
    )
    with pytest.raises(DeclarationError, match="adds steps to a package's phase"):
        _extensions.declaration("acme.site")
    # Then the declaration: its keys, level, placement and verb, read from
    # the file and known to every reader.
    fake_extensions(tmp_path, monkeypatch, site=DECLARES_DOCS)
    found = _extensions.declaration("acme.site")
    assert found is not None
    docs = PhaseSpec(
        "docs", {"site": "path"}, WORKSPACE_LEVEL, True, (), True, "acme.site"
    )
    assert found.phase_specs == {"docs": docs}
    assert declared_phases()["docs"] == docs and phase_spec("docs") == docs
    (record,) = found.additions.checks
    assert (record.name, record.phase, record.step) == ("docs.pages", "docs", "pages")
    assert found.phases == {}
    # Mounted, the step registers, the phase's verb is generated and the
    # gate runs the step.
    root_contract(tmp_path, '["acme.site"]')
    with registry.capture():
        _extensions.mount_extensions(tmp_path)
    registered = _checks.checks_by_name()["docs.pages"]
    assert registered.extension == "acme.site"
    assert _checks.verb_tree()["docs"] == {"pages": registered}
    assert "docs.pages" in _checks.judges(GateContext(root=tmp_path, packages=()))
    # Declared without a verb and outside the gate, the step keeps its
    # address and gets neither.
    quiet = DECLARES_DOCS.replace("gated = true", "gated = false").replace(
        "verb = true", "verb = false"
    )
    fake_extensions(tmp_path, monkeypatch, site=quiet)
    with registry.capture():
        _extensions.mount_extensions(tmp_path)
    assert "docs.pages" in _checks.checks_by_name()
    assert "docs" not in _checks.verb_tree()
    assert "docs.pages" not in _checks.judges(GateContext(root=tmp_path, packages=()))
    # Two installed extensions declaring one phase refuse naming both; a
    # fake stays in the scan for the test, so this comes last.
    fake_extensions(tmp_path, monkeypatch, other=DECLARES_DOCS)
    with pytest.raises(
        DeclarationError,
        match=r"^the docs phase is declared by both acme\.other and acme\.site; one"
        r" extension declares a phase$",
    ):
        declared_phases()
