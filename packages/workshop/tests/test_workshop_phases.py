"""Lifecycle phases: the refusals first, then the one walk over a package's steps."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

from livery.workshop import _extensions, _phases
from livery.workshop._composition import closure
from livery.workshop._declaration import DeclarationError
from livery.workshop._packages import Package
from livery.workshop._phases import PhaseContext, PhaseError, phase_steps, walk_steps
from workshop_extension_fakes import (
    fake_extensions,
    fake_steps,
    package_contract,
    root_contract,
)

#: An extension's identity, the start of every fake here.
PACKAGE = "[extension]\n"

#: A step that says when it runs.
SAYS = """\
def main(ctx):
    print("{name}.main")
"""


def _steps(name: str, *, provides: str = "", reads: str = "", after: str = "") -> str:
    """A declaration adding *name*'s one step, ``build.<name>``, to the build phase."""
    lines = [f"[phases.build.{name}]", 'main = "{package}._steps:main"']
    if provides:
        lines.append(f"provides = {provides}")
    if reads:
        lines.append(f"reads = {reads}")
    if after:
        lines.append(f"after = {after}")
    return PACKAGE + "\n" + "\n".join(lines) + "\n"


def _package(root: Path, *extensions: str) -> Package:
    return Package(
        directory=root / "packages" / "core",
        path="packages/core",
        name="core",
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


def _engine(root: Path, package: Package, phase: str) -> PhaseContext:
    """The context an engine running *phase* over *package* hands the walk."""
    return PhaseContext(root=root, packages=(package,), package=package, phase=phase)


def _run(
    root: Path,
    package: Package,
    phase: str = "build",
    inputs: Mapping[str, object] | None = None,
) -> PhaseContext:
    """Run *phase* over *package* as the train does: the steps of its set.

    *inputs* are the keys the engine gives *phase*.
    """
    lookup = _extensions.installed_declaration
    steps = phase_steps(phase, closure(package.extensions, lookup), lookup)
    given = {} if inputs is None else {phase: inputs}
    return walk_steps(_engine(root, package, phase), steps, inputs=given)


def _said(out: str) -> list[str]:
    """The lines the steps printed, the walk's own and footman's receipts left out."""
    return [
        line for line in out.splitlines() if not line.startswith(("  ", "ok", "FAIL"))
    ]


# The refusals first.


def test_the_engine_refuses_an_input_the_phase_does_not_take(tmp_path: Path) -> None:
    package = _package(tmp_path)
    with pytest.raises(
        PhaseError,
        match="gives wheel to the build phase, which takes configuration, epoch,"
        " isolated from it",
    ):
        walk_steps(
            _engine(tmp_path, package, "build"), (), inputs={"build": {"wheel": "x"}}
        )
    with pytest.raises(
        PhaseError,
        match="gives epoch to the build phase as str, which the phase takes as int",
    ):
        walk_steps(
            _engine(tmp_path, package, "build"), (), inputs={"build": {"epoch": "0"}}
        )
    # The words after -- are the context's arguments, so the run phase
    # takes the executable alone.
    with pytest.raises(PhaseError, match="the run phase, which takes executable from"):
        walk_steps(
            _engine(tmp_path, package, "run"), (), inputs={"run": {"arguments": []}}
        )
    with pytest.raises(PhaseError, match="the sync phase, which takes nothing from it"):
        walk_steps(
            _engine(tmp_path, package, "sync"), (), inputs={"sync": {"epoch": 0}}
        )
    # The inputs name their phase, so a phase nobody declares refuses
    # naming the declared ones, from a context naming none as well.
    listed = ", ".join(_phases.declared_phases())
    with pytest.raises(PhaseError) as raised:
        walk_steps(PhaseContext(root=tmp_path), (), inputs={"bild": {"epoch": 0}})
    assert str(raised.value) == f"bild is not a declared phase; the phases are {listed}"
    # A walk over several phases, the gate's, gives each phase its own.
    ctx = walk_steps(
        PhaseContext(root=tmp_path),
        (),
        inputs={"build": {"isolated": False}, "test": {"isolated": False}},
    )
    assert ctx.phase == ""


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
        ("build", {"isolated": True, "configuration": "release", "epoch": 7}),
        (
            "test",
            {"isolated": True, "resolution": "floor", "release-dirs": [Path("dist")]},
        ),
        (
            "publish",
            {"version": "1.2.3", "registry-target": {"url": "x"}, "prebuilt": True},
        ),
        (
            "verify",
            {
                "version": "1.2.3",
                "tree": Path("tree"),
                "interpreter": "3.14",
                "index": "https://index",
                "extras": "",
            },
        ),
        ("run", {"executable": "serve"}),
    ]
    for phase, inputs in given:
        ctx = walk_steps(_engine(tmp_path, package, phase), (), inputs={phase: inputs})
        assert ctx.phase == phase
        for key, value in inputs.items():
            assert ctx.result(key) == value


def test_a_side_on_a_phase_with_one_side_refuses_in_the_walk(tmp_path: Path) -> None:
    # A step built in code bypasses the declaration's reader, so the
    # walk refuses it before any step runs, naming the phase.
    from livery.workshop._steps import PACKAGE as PER_PACKAGE
    from livery.workshop._steps import StepRecord

    ran: list[str] = []

    def body(ctx: PhaseContext) -> None:
        ran.append(ctx.step)

    record = StepRecord(
        "odd",
        "stamp",
        body,
        scope=PER_PACKAGE,
        extensions=("acme.odd",),
        isolated=False,
    )
    package = _package(tmp_path, "acme.odd")
    with pytest.raises(PhaseError) as raised:
        walk_steps(_engine(tmp_path, package, "stamp"), (record,))
    assert str(raised.value) == (
        "packages/core: the stamp phase: stamp.odd declares isolated, and the"
        " phase has one side: its declaration gives no isolated key"
    )
    assert ran == []


def test_a_step_providing_a_key_the_engine_gives_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(
        tmp_path, monkeypatch, own=_steps("own", provides='{ epoch = "int" }')
    )
    fake_steps(tmp_path, "own", "def main(ctx):\n    ctx.provide('epoch', 1)\n")
    with pytest.raises(PhaseError) as raised:
        _run(tmp_path, _package(tmp_path, "acme.own"), inputs={"epoch": 0})
    assert str(raised.value) == (
        "packages/core: the build phase: build.own provides epoch, which the engine"
        " running the phase provides; a step reads it"
    )


def test_a_step_reading_a_key_the_engine_did_not_give_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(tmp_path, monkeypatch, reader=_steps("reader", reads='["epoch"]'))
    fake_steps(tmp_path, "reader", "def main(ctx):\n    ctx.read('epoch')\n")
    with pytest.raises(
        PhaseError,
        match=r"build\.reader reads epoch, which the engine running the build phase"
        r" did not give it",
    ):
        _run(tmp_path, _package(tmp_path, "acme.reader"))


def test_a_step_reads_the_engines_keys_and_the_engine_reads_back_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(
        tmp_path,
        monkeypatch,
        builder=_steps("builder", provides='{ dist = "path" }', reads='["epoch"]'),
    )
    fake_steps(
        tmp_path,
        "builder",
        "from pathlib import Path\n\n\n"
        "def main(ctx):\n    ctx.provide('dist', Path(str(ctx.read('epoch'))))\n",
    )
    package = _package(tmp_path, "acme.builder")
    ctx = _run(tmp_path, package, inputs={"epoch": 7})
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
        "the build phase: build.a and build.b both provide wheel; one step of a"
        " package provides a key"
    )
    with pytest.raises(PhaseError) as raised:
        _run(tmp_path, package)
    assert str(raised.value) == f"packages/core: {refusal}"
    assert capsys.readouterr().out == ""
    assert _extensions.closure_problems(tmp_path) == [f"packages/core: {refusal}"]


def test_two_extensions_declaring_one_step_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    same = PACKAGE + '\n[phases.build.compile]\nmain = "{package}._steps:main"\n'
    package = _workspace(tmp_path, monkeypatch, a=same, b=same)
    refusal = (
        "the build phase: acme.a and acme.b both declare build.compile; one"
        " extension of a package declares a step"
    )
    with pytest.raises(PhaseError) as raised:
        _run(tmp_path, package)
    assert str(raised.value) == f"packages/core: {refusal}"
    assert _extensions.closure_problems(tmp_path) == [f"packages/core: {refusal}"]


def test_a_key_nobody_provides_and_an_order_with_no_start_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _workspace(tmp_path, monkeypatch, a=_steps("a", reads='["wheel"]'))
    with pytest.raises(PhaseError) as raised:
        _run(tmp_path, package)
    assert str(raised.value) == (
        "packages/core: the build phase: build.a reads wheel, which no step of the"
        " package provides"
    )
    cycle = (
        "packages/core: the build phase: its steps' order has no start: build.a"
        " before build.b before build.a; remove a key read or an after among them"
    )
    _workspace(
        tmp_path,
        monkeypatch,
        a=_steps("a", provides='{ x = "str" }', reads='["y"]'),
        b=_steps("b", provides='{ y = "str" }', reads='["x"]'),
    )
    assert _extensions.closure_problems(tmp_path) == [cycle]
    # Two steps each after the other close the same way.
    _workspace(
        tmp_path,
        monkeypatch,
        a=_steps("a", after='["build.b"]'),
        b=_steps("b", after='["build.a"]'),
    )
    assert _extensions.closure_problems(tmp_path) == [cycle]


@pytest.mark.parametrize(
    ("declared", "refusal"),
    [
        (
            PACKAGE + '\n[phases.biuld.odd]\nmain = "{package}._steps:main"\n',
            "phases.biuld names no phase; the phases are {phases}; did you mean"
            " 'build'?",
        ),
        (
            PACKAGE + '\n[phases.build.odd]\nmain = "{package}._steps:main"\n'
            'provides = { x = "str" }\nreads = ["x"]\n',
            "phases.build.odd.reads names x, which its provides names too; a step"
            " reads what another step provides",
        ),
        (
            PACKAGE + '\n[phases.build.odd]\nmain = "{package}._steps:mian"\n',
            "phases.build.odd.main names mian, which acme.odd._steps does not define"
            " at its top level; did you mean 'main'?",
        ),
        (
            PACKAGE + '\n[phases.build.odd]\nmain = "{package}._steps:main"\n'
            'scope = "workspace"\n',
            "phases.build.odd.scope is not read on a step of the build phase, which"
            " runs once per package whose set holds the extension, as the phase's"
            " level says",
        ),
        (
            PACKAGE + '\n[phases.stamp.odd]\nmain = "{package}._steps:main"\n'
            "isolated = true\n",
            "phases.stamp.odd.isolated is not read on a step of the stamp phase,"
            " which has one side: its declaration gives no isolated key",
        ),
    ],
    ids=[
        "unknown-phase",
        "reads-its-own",
        "no-function",
        "scope",
        "side-of-a-one-sided-phase",
    ],
)
def test_a_phase_table_the_file_alone_shows_wrong_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, declared: str, refusal: str
) -> None:
    from livery.workshop._phases import declared_phases

    fake_extensions(tmp_path, monkeypatch, odd=declared)
    fake_steps(tmp_path, "odd", SAYS.format(name="odd"))
    with pytest.raises(DeclarationError) as raised:
        _extensions.declaration("acme.odd")
    path = tmp_path / "site" / "acme" / "odd" / "extension.toml"
    # The installed extensions' phases follow the base's.
    phases = ", ".join(declared_phases())
    assert str(raised.value) == f"{path}: {refusal.replace('{phases}', phases)}"


def test_a_failed_provider_stops_its_reader_and_is_the_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The reader is not run and says why; a step depending on nothing
    # failed still runs, as footman runs a block; the provider's failure
    # is what the walk raises.
    package = _workspace(
        tmp_path,
        monkeypatch,
        a=_steps("a", reads='["wheel"]'),
        c=_steps("c"),
        z=_steps("z", provides='{ wheel = "path" }'),
    )
    fake_steps(tmp_path, "z", "def main(ctx):\n    raise RuntimeError('z broke')\n")
    fake_steps(
        tmp_path, "a", "def main(ctx):\n    print('a read', ctx.read('wheel'))\n"
    )
    with pytest.raises(RuntimeError, match=r"^z broke$"):
        _run(tmp_path, package)
    out = capsys.readouterr().out
    assert _said(out) == ["c.main"]
    assert "build.a packages/core: not run, since build.z packages/core failed" in out


@pytest.mark.parametrize(
    ("body", "refusal"),
    [
        (
            'ctx.provide("other", "x")',
            "build.z provides other, and its [phases.build.z] table does not name it"
            " in provides",
        ),
        (
            'ctx.provide("wheel", "dist/z.whl")',
            "build.z provides wheel as str, and its [phases.build.z] table declares"
            " it path",
        ),
        (
            'ctx.read("wheel")',
            "build.z reads wheel, and its [phases.build.z] table does not name it in"
            " reads",
        ),
    ],
    ids=["undeclared-provide", "wrong-type", "undeclared-read"],
)
def test_a_step_misusing_the_context_refuses_naming_its_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str, refusal: str
) -> None:
    package = _workspace(
        tmp_path, monkeypatch, z=_steps("z", provides='{ wheel = "path" }')
    )
    fake_steps(tmp_path, "z", f"def main(ctx):\n    {body}\n")
    with pytest.raises(PhaseError) as raised:
        _run(tmp_path, package)
    assert str(raised.value) == refusal


def test_a_read_its_provider_never_wrote_refuses_naming_the_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _workspace(
        tmp_path,
        monkeypatch,
        a=_steps("a", reads='["wheel"]'),
        z=_steps("z", provides='{ wheel = "path" }'),
    )
    fake_steps(tmp_path, "z", "def main(ctx):\n    del ctx\n")
    fake_steps(tmp_path, "a", 'def main(ctx):\n    ctx.read("wheel")\n')
    with pytest.raises(PhaseError) as raised:
        _run(tmp_path, package)
    assert str(raised.value) == (
        "build.a reads wheel, which build.z has not provided in the build phase"
    )


def test_a_context_outside_a_walk_refuses_its_keys_and_names_no_package(
    tmp_path: Path,
) -> None:
    ctx = PhaseContext(root=tmp_path, step="build.x")
    with pytest.raises(
        PhaseError, match=r"build\.x is not a step of the walk that built this context"
    ):
        ctx.read("epoch")
    with pytest.raises(
        BaseException, match=r"build\.x runs over one package at a time, and this run"
    ):
        _ = ctx.member
    assert ctx.exit_code == 0


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
        tmp_path, monkeypatch, z=_steps("z", provides=f'{{ value = "{kind}" }}')
    )
    source = (
        'from pathlib import Path\n\n\ndef main(ctx):\n    ctx.provide("value", {})\n'
    )
    fake_steps(tmp_path, "z", source.format(good))
    _run(tmp_path, package)
    fake_steps(tmp_path, "z", source.format(bad))
    with pytest.raises(PhaseError, match=f"declares it {kind}$"):
        _run(tmp_path, package)


# The walk.


def test_a_reader_runs_after_its_provider_and_reads_what_it_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    package = _workspace(
        tmp_path,
        monkeypatch,
        a=_steps("a", reads='["wheel"]'),
        z=_steps("z", provides='{ wheel = "path" }'),
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
    ctx = _run(tmp_path, package)
    assert isinstance(ctx, PhaseContext)
    assert _said(capsys.readouterr().out) == [f"a read {Path('dist') / 'z.whl'}"]
    assert ctx.result("wheel") == Path("dist") / "z.whl"
    # A package whose set adds no step to a phase runs nothing.
    assert _run(tmp_path, package, "stamp").result("wheel") is None


def test_after_orders_two_steps_and_independent_steps_run_concurrently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # One extension adds two steps, the second after the first; another
    # extension's step depends on neither, and the first step waits for
    # it at a barrier both sides must reach, so a walk that ran them one
    # at a time would stop there.
    two = (
        PACKAGE + '\n[phases.build.first]\nmain = "{package}._steps:first"\n\n'
        '[phases.build.second]\nmain = "{package}._steps:second"\n'
        'after = ["build.first"]\n'
    )
    package = _workspace(tmp_path, monkeypatch, two=two, other=_steps("other"))
    fake_steps(
        tmp_path,
        "two",
        "import threading\n\nBARRIER = threading.Barrier(2, timeout=10)\n\n\n"
        'def first(ctx):\n    BARRIER.wait()\n    print("two.first")\n\n\n'
        'def second(ctx):\n    print("two.second")\n',
    )
    fake_steps(
        tmp_path,
        "other",
        "from acme.two import _steps\n\n\n"
        'def main(ctx):\n    _steps.BARRIER.wait()\n    print("other.main")\n',
    )
    _run(tmp_path, package)
    said = _said(capsys.readouterr().out)
    assert sorted(said) == ["other.main", "two.first", "two.second"]
    assert said.index("two.first") < said.index("two.second")


def test_a_frozen_view_records_the_exit_code_through_the_shared_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The run phase's step records the exit code with exit(); a write
    # to an attribute of its view refuses, since a view is frozen.
    package = _workspace(
        tmp_path,
        monkeypatch,
        s=PACKAGE + '\n[phases.run.s]\nmain = "{package}._steps:main"\n',
    )
    fake_steps(
        tmp_path,
        "s",
        "import dataclasses\n\n\ndef main(ctx):\n    try:\n"
        '        ctx.phase = "x"\n    except dataclasses.FrozenInstanceError:\n'
        "        ctx.exit(7)\n",
    )
    ctx = _run(tmp_path, package, "run")
    assert ctx.exit_code == 7


def _sided(name: str, side: str, phase: str = "test") -> str:
    """A declaration adding *name*'s one step to *phase*, the test's, on *side*."""
    lines = [f"[phases.{phase}.{name}]", 'main = "{package}._steps:main"']
    if side:
        lines.append(f"isolated = {side}")
    return PACKAGE + "\n" + "\n".join(lines) + "\n"


def test_each_step_runs_on_the_side_of_its_phase_the_engine_gives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    package = _workspace(
        tmp_path,
        monkeypatch,
        iso=_sided("iso", "true"),
        tree=_sided("tree", "false"),
        both=_sided("both", ""),
    )
    # The engine gives nothing, the gate's shape: the tree side.
    _run(tmp_path, package, "test")
    assert sorted(_said(capsys.readouterr().out)) == ["both.main", "tree.main"]
    _run(tmp_path, package, "test", inputs={"isolated": False})
    assert sorted(_said(capsys.readouterr().out)) == ["both.main", "tree.main"]
    # The isolated side: the train's legs.
    _run(tmp_path, package, "test", inputs={"isolated": True})
    assert sorted(_said(capsys.readouterr().out)) == ["both.main", "iso.main"]


def test_a_step_serving_the_isolated_side_alone_registers_no_gate_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The gate runs the tree side, so of a workspace-level phase's steps
    # the isolated one stays out of the registry and in the phase's steps.
    fake_extensions(
        tmp_path, monkeypatch, iso=_sided("iso", "true"), tree=_sided("tree", "false")
    )
    for name in ("iso", "tree"):
        fake_steps(tmp_path, name, SAYS.format(name=name))
    iso = _extensions.declaration("acme.iso")
    tree = _extensions.declaration("acme.tree")
    assert iso is not None and tree is not None
    assert iso.additions.steps == ()
    assert [(r.name, r.isolated) for r in iso.phases["test"]] == [("test.iso", True)]
    assert [(r.name, r.isolated) for r in tree.additions.steps] == [
        ("test.tree", False)
    ]


def test_an_isolated_only_build_step_registers_no_gate_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The gate builds each package on the build phase's tree side, so of
    # that package-level phase's steps the isolated one stays out of the
    # registry and in the phase's steps, as the release's builds do.
    fake_extensions(
        tmp_path,
        monkeypatch,
        iso=_sided("iso", "true", "build"),
        tree=_sided("tree", "false", "build"),
        both=_sided("both", "", "build"),
    )
    for name in ("iso", "tree", "both"):
        fake_steps(tmp_path, name, SAYS.format(name=name))
    iso = _extensions.declaration("acme.iso")
    tree = _extensions.declaration("acme.tree")
    both = _extensions.declaration("acme.both")
    assert iso is not None and tree is not None and both is not None
    assert iso.additions.steps == ()
    assert [(r.name, r.isolated) for r in iso.phases["build"]] == [("build.iso", True)]
    assert [(r.name, r.isolated) for r in tree.additions.steps] == [
        ("build.tree", False)
    ]
    assert [(r.name, r.isolated) for r in both.additions.steps] == [
        ("build.both", None)
    ]
    # The build the release runs: python's serves the isolated side
    # alone, and never reaches the gate.
    declared = _extensions.declaration("python")
    assert declared is not None
    assert [r.isolated for r in declared.phases["build"]] == [True]
    assert [r.name for r in declared.additions.steps if r.phase == "build"] == []
    # A package-level phase the gate never runs, the release's stamp,
    # registers its steps on neither side.
    fake_extensions(
        tmp_path,
        monkeypatch,
        mark=PACKAGE + '\n[phases.stamp.mark]\nmain = "{package}._steps:main"\n',
    )
    fake_steps(tmp_path, "mark", SAYS.format(name="mark"))
    mark = _extensions.declaration("acme.mark")
    assert mark is not None and mark.additions.steps == ()
    assert [r.name for r in mark.phases["stamp"]] == ["stamp.mark"]


# The declared phases: the base's from its contract file, an extension's
# from its declaration file, and the refusals of a phase nobody declares.


@pytest.fixture
def restored_steps() -> Iterator[None]:
    """Put the step registry back after a mount registered steps into it."""
    from livery.workshop import _steps

    state = _steps.snapshot()
    yield
    _steps.restore(state)


#: A workspace-level extension declaring a workspace-level phase, book,
#: with one step of its own.
DECLARES_BOOK = """\
[phases.book]
level = "workspace"
gated = true
after = []
verb = true
keys = { site = "path" }

[phases.book.pages]
tools = ["acme-pages"]
main = ["acme-pages", "build", "{paths}"]
"""

#: A step into the book phase, declared by another extension.
ADDS_BOOK_STEP = """\
[phases.book.index]
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
        "stamp",
        "publish",
        "verify",
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
    # fm sync runs the sync phase once over the workspace, so an
    # extension the root lists adds to it as one a package lists does.
    assert found["sync"] == PhaseSpec("sync", {}, WORKSPACE_LEVEL, False, (), False)
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
        "clean",
        "build",
        "stamp",
        "publish",
        "verify",
        "run",
    )
    # The train's order, by after: stamp, build, test, publish, verify.
    assert {name: spec.after for name, spec in found.items() if spec.after} == {
        "build": ("stamp",),
        "test": ("build",),
        "publish": ("test",),
        "verify": ("publish",),
        "run": ("build",),
    }
    # Each engine's keys, typed with the types a context value has.
    assert {name: dict(spec.keys) for name, spec in found.items() if spec.keys} == {
        "build": {"isolated": "bool", "configuration": "str", "epoch": "int"},
        "test": {"isolated": "bool", "resolution": "str", "release-dirs": "paths"},
        "stamp": {"version": "str"},
        "publish": {"version": "str", "registry-target": "table", "prebuilt": "bool"},
        "verify": {
            "version": "str",
            "tree": "path",
            "interpreter": "str",
            "index": "str",
            "extras": "str",
        },
        "run": {"executable": "str"},
    }
    for spec in found.values():
        assert set(spec.keys.values()) <= set(TYPES)
        assert spec.extension == "livery.workshop"
    # The base's are among every declared phase, as the base declares them.
    declared = declared_phases()
    assert all(declared[name] == spec for name, spec in found.items())


def test_a_run_of_the_registered_steps_refuses_a_context_naming_no_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restored_steps: None
) -> None:
    from livery.workshop._phases import run_registered
    from livery.workshop._steps import StepRecord, register_step

    fake_extensions(
        tmp_path,
        monkeypatch,
        pages="[extension]\napi-version = 1\n\n[phases.acme-pages]\n"
        'level = "workspace"\ngated = false\nverb = false\n',
    )
    with pytest.raises(PhaseError, match=r"this context names none$"):
        run_registered(PhaseContext(root=tmp_path))
    with pytest.raises(PhaseError, match=r"^bkoo is not a declared phase"):
        run_registered(PhaseContext(root=tmp_path, phase="bkoo"))
    # A phase the gate never runs runs its registered steps alone, and one
    # with no registered step runs nothing.
    ran: list[str] = []

    def note(ctx: PhaseContext) -> None:
        ran.append(ctx.step)

    register_step(StepRecord("tree", "configure", note, extension="acme.tree"))
    register_step(StepRecord("other", "lint", note, extension="acme.tree"))
    run_registered(PhaseContext(root=tmp_path, phase="configure"))
    run_registered(PhaseContext(root=tmp_path, phase="acme-pages"))
    assert ran == ["configure.tree"]


#: A workspace-level extension declaring a phase the gate never runs.
DECLARES_PAGES = """\
[phases.pages]
level = "workspace"
"""

#: A step into the pages phase that writes outside every generated tree,
#: from an extension that requires the declarer.
ADDS_PAGES_STEP = """\
[extension]
requires = ["acme.site"]

[phases.pages.write]
main = "{package}._steps:write"
outputs = ["extra/", "package/made.txt"]
"""

#: What the pages phase's step writes: a page at the root, and a file in
#: each package.
WRITES = """\
def write(ctx):
    (ctx.root / "extra").mkdir(exist_ok=True)
    (ctx.root / "extra" / "page.md").write_text("page\\n")
    for package in ctx.packages:
        (package.directory / "made.txt").write_text("made\\n")
"""


def _pages_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, writer: bool = True
) -> Path:
    """A workspace with one member, listing the pages phase's writer, mounted.

    Without *writer*, the writer is not installed and the workspace
    lists the phase's declarer alone.
    """
    from livery.footman import _registry as registry
    from livery.workshop import _steps

    modules = {"site": DECLARES_PAGES}
    if writer:
        modules["writer"] = ADDS_PAGES_STEP
    fake_extensions(tmp_path, monkeypatch, **modules)
    if writer:
        fake_steps(tmp_path, "writer", WRITES)
    root = tmp_path / "ws"
    root.mkdir(exist_ok=True)
    root_contract(root, '["acme.writer"]' if writer else '["acme.site"]')
    package_contract(root, "core", "[]")
    _steps.restore(_steps.snapshot(initial=True))
    with registry.capture():
        _extensions.mount_extensions(root)
    return root


def test_run_phase_refuses_a_phase_nobody_declares_naming_the_declared(
    tmp_path: Path, restored_steps: None
) -> None:
    from livery.workshop import run_phase

    listed = ", ".join(_phases.declared_phases())
    with pytest.raises(PhaseError) as refused:
        run_phase(tmp_path, "bkoo")
    assert (
        str(refused.value) == f"bkoo is not a declared phase; the phases are {listed}"
    )
    # A phase the gate runs refuses too, naming what runs it.
    with pytest.raises(PhaseError, match=r"^the gate runs the lint phase; run_phase"):
        run_phase(tmp_path, "lint")
    # And so does an output it is given that leaves the root or names
    # docs/ itself, before any step runs.
    for output, refusal in (
        ("../out/", "names '../out/', which leaves the root"),
        ("docs/", "names 'docs/', the root's docs/ itself"),
        ("package/docs", "names 'package/docs', each package's docs/ itself"),
        ("package/", "names 'package/', each package itself"),
    ):
        with pytest.raises(PhaseError) as refused:
            run_phase(tmp_path, "configure", outputs=(output,))
        assert str(refused.value).startswith(
            f"an output of the configure phase {refusal}"
        )
    assert not (tmp_path / _phases.OUTPUTS_RECORD).exists()


def test_a_step_whose_extension_is_no_longer_installed_leaves_no_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    restored_steps: None,
) -> None:
    from livery.workshop import run_phase

    root = _pages_workspace(tmp_path, monkeypatch)
    run_phase(root, "pages")
    assert (root / "extra" / "page.md").is_file()
    assert (root / "packages" / "core" / "made.txt").is_file()
    capsys.readouterr()
    # The writer uninstalled and no longer listed: the next run removes
    # what it wrote, at the root and in each package, and says so.
    root = _pages_workspace(tmp_path, monkeypatch, writer=False)
    run_phase(root, "pages")
    assert not (root / "extra").exists()
    assert not (root / "packages" / "core" / "made.txt").exists()
    assert capsys.readouterr().out.splitlines() == [
        "  removed extra/: no running step of the pages phase writes it",
        "  removed packages/core/made.txt: no running step of the pages phase"
        " writes it",
    ]
    # Nothing is recorded any more, so the run after removes nothing.
    (root / "extra").mkdir()
    run_phase(root, "pages")
    assert (root / "extra").is_dir()
    assert capsys.readouterr().out == ""


def test_a_record_that_cannot_be_read_removes_nothing_and_is_written_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restored_steps: None
) -> None:
    import json

    from livery.workshop import run_phase

    root = _pages_workspace(tmp_path, monkeypatch)
    record = root / _phases.OUTPUTS_RECORD
    record.parent.mkdir(parents=True)
    (tmp_path / "outside").mkdir()
    for held in ("not json", '{"schema": 2, "phases": {"pages": ["kept/"]}}'):
        (root / "kept").mkdir(exist_ok=True)
        record.write_text(held)
        run_phase(root, "pages")
        assert (root / "kept").is_dir()
        assert json.loads(record.read_text()) == {
            "schema": 1,
            "phases": {"pages": ["extra/", "package/made.txt"]},
        }
    # A recorded path that leaves the root is never removed.
    record.write_text('{"schema": 1, "phases": {"pages": ["../outside/"]}}')
    run_phase(root, "pages")
    assert (tmp_path / "outside").is_dir()


def test_a_running_steps_output_stays_and_an_idle_steps_output_goes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restored_steps: None
) -> None:
    from livery.workshop import run_phase
    from livery.workshop._steps import StepRecord, register_step

    root = _pages_workspace(tmp_path, monkeypatch)
    run_phase(root, "pages")
    # The running step finds its output as it left it: nothing removes
    # it first.
    (root / "extra" / "earlier.md").write_text("earlier\n")
    # A step that names an extension no package holds does not run, and
    # what it declares goes, recorded or not.
    seen: list[bool] = []

    def idle(ctx: PhaseContext) -> None:
        del ctx

    def look(ctx: PhaseContext) -> None:
        seen.append((ctx.root / "extra" / "earlier.md").is_file())

    register_step(
        StepRecord(
            "idle",
            "pages",
            idle,
            extensions=("acme.nobody",),
            extension="acme.idle",
            outputs=("stale/",),
        )
    )
    register_step(StepRecord("look", "pages", look, extension="acme.look"))
    (root / "stale").mkdir()
    run_phase(root, "pages")
    assert seen == [True]
    assert (root / "extra" / "earlier.md").is_file()
    assert not (root / "stale").exists()


def test_a_phase_no_mounted_extension_declares_refuses_naming_the_declared_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restored_steps: None
) -> None:
    from livery.footman import _registry as registry

    # The reader: a step into a phase no declaration names.
    fake_extensions(tmp_path, monkeypatch, odd=ADDS_BOOK_STEP.replace("book", "bkoo"))
    listed = ", ".join(_phases.declared_phases())
    with pytest.raises(DeclarationError) as raised:
        _extensions.declaration("acme.odd")
    path = tmp_path / "site" / "acme" / "odd" / "extension.toml"
    assert str(raised.value) == (
        f"{path}: phases.bkoo names no phase; the phases are {listed}"
    )
    # The engine: a phase nobody declares cannot run.
    with pytest.raises(PhaseError) as refused:
        walk_steps(_engine(tmp_path, _package(tmp_path), "bkoo"), ())
    assert (
        str(refused.value) == f"bkoo is not a declared phase; the phases are {listed}"
    )
    # The mount: a phase an installed extension declares reads anywhere,
    # and a step into it refuses while the step's extension does not
    # require the declarer, whatever the list holds.
    fake_extensions(tmp_path, monkeypatch, site=DECLARES_BOOK, index=ADDS_BOOK_STEP)
    root_contract(tmp_path, '["acme.index", "acme.site"]')
    found = _extensions.declaration("acme.index")
    assert found is not None and [r.name for r in found.additions.steps] == [
        "book.index"
    ]
    refusal = (
        r"extension 'acme\.index' adds steps to the book phase, which acme\.site"
        r" declares, and does not require acme\.site"
    )
    with pytest.raises(BaseException, match=refusal), registry.capture():
        _extensions.mount_extensions(tmp_path)
    # Requiring it, the list names the step's extension alone: the
    # declarer joins its set, mounts first, and the step registers.
    from livery.workshop import _steps

    fake_extensions(
        tmp_path,
        monkeypatch,
        site=DECLARES_BOOK,
        index='[extension]\nrequires = ["acme.site"]\n' + ADDS_BOOK_STEP,
    )
    root_contract(tmp_path, '["acme.index"]')
    with registry.capture():
        _extensions.mount_extensions(tmp_path)
    assert _steps.steps_by_name()["book.index"].extension == "acme.index"


def test_an_extension_declares_a_phase_with_its_engine_keys_level_placement_and_verb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restored_steps: None
) -> None:
    from livery.footman import _registry as registry
    from livery.workshop import _steps
    from livery.workshop._phases import WORKSPACE_LEVEL, PhaseSpec, phase_spec

    # The declared phases are read through the module: each fake set
    # below reads them afresh.
    def declared_phases() -> dict[str, PhaseSpec]:
        return _phases.declared_phases()

    # The refusals first: a declaration off its shape, read from the
    # installed file before any declaration is judged.
    for text, refusal in (
        (
            DECLARES_BOOK.replace('level = "workspace"', 'level = "sometimes"'),
            "phases.book.level is 'sometimes'; a phase runs at one of package,"
            " workspace",
        ),
        (
            DECLARES_BOOK.replace('{ site = "path" }', '{ site = "folder" }'),
            "phases.book.keys.site is 'folder'; a key's type is one of str, int,"
            " bool, path, strs, paths, table",
        ),
        (
            DECLARES_BOOK.replace("gated = true", 'gated = "yes"'),
            "phases.book.gated is true or false",
        ),
        (
            DECLARES_BOOK.replace("after = []", 'after = "build"'),
            "phases.book.after is a list of the phases this one follows",
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
        tmp_path, monkeypatch, site=DECLARES_BOOK.replace("after = []", 'after = ["x"]')
    )
    with pytest.raises(DeclarationError) as raised:
        declared_phases()
    assert str(raised.value).startswith(
        "acme.site declares the book phase after x, which no extension declares;"
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
    # step into it is read as any extension's is.
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
        '[phases.notes.write]\nmain = "{package}._steps:noop"\n',
    )
    found = _extensions.declaration("acme.site")
    assert found is not None
    assert [record.name for record in found.phases["notes"]] == ["notes.write"]
    # Then the declaration: its keys, level, placement and verb, read from
    # the file and known to every reader.
    fake_extensions(tmp_path, monkeypatch, site=DECLARES_BOOK)
    found = _extensions.declaration("acme.site")
    assert found is not None
    book = PhaseSpec(
        "book", {"site": "path"}, WORKSPACE_LEVEL, True, (), True, "acme.site"
    )
    assert found.phase_specs == {"book": book}
    assert declared_phases()["book"] == book and phase_spec("book") == book
    (record,) = found.additions.steps
    assert (record.name, record.phase, record.step) == ("book.pages", "book", "pages")
    # The one record, by phase as well, whatever the phase's level.
    assert found.phases == {"book": (record,)}
    # Mounted, the step registers, the phase's verb is generated and the
    # gate runs the step.
    root_contract(tmp_path, '["acme.site"]')
    with registry.capture():
        _extensions.mount_extensions(tmp_path)
    registered = _steps.steps_by_name()["book.pages"]
    assert registered.extension == "acme.site"
    assert _steps.verb_tree()["book"] == {"pages": registered}
    assert "book.pages" in _steps.judges(PhaseContext(root=tmp_path, packages=()))
    # Declared without a verb and outside the gate, the step keeps its
    # address and gets neither.
    quiet = DECLARES_BOOK.replace("gated = true", "gated = false").replace(
        "verb = true", "verb = false"
    )
    fake_extensions(tmp_path, monkeypatch, site=quiet)
    with registry.capture():
        _extensions.mount_extensions(tmp_path)
    assert "book.pages" in _steps.steps_by_name()
    assert "book" not in _steps.verb_tree()
    assert "book.pages" not in _steps.judges(PhaseContext(root=tmp_path, packages=()))
    # Two installed extensions declaring one phase refuse naming both; a
    # fake stays in the scan for the test, so this comes last.
    fake_extensions(tmp_path, monkeypatch, other=DECLARES_BOOK)
    with pytest.raises(
        DeclarationError,
        match=r"^the book phase is declared by both acme\.other and acme\.site; one"
        r" extension declares a phase$",
    ):
        declared_phases()
