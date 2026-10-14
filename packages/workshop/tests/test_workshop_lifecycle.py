"""The lifecycle seam: what it answers where a kind has nothing to say."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from livery.workshop import _kinds, _lifecycle
from livery.workshop._kinds import KindRecord
from livery.workshop._packages import (
    Package,
    ReferencedName,
    SourceReferences,
    graph_problems,
)
from livery.workshop._queries import QueryError
from livery.workshop._registries import RegistryTarget
from workshop_extension_fakes import (
    fake_package_extensions,
    fake_steps,
    package_contract,
    root_contract,
)

if TYPE_CHECKING:
    from livery.workshop._kinds import Backend

_FAILURES = (BaseException,)


class _BuildOnly:
    """A backend that builds and leaves out every function the seam may miss."""

    def build(self, package: Package, root: Path, *, epoch: int = 0) -> Path:
        del root, epoch
        return package.directory / "dist"


def _build_only_kind(monkeypatch: pytest.MonkeyPatch) -> str:
    """Register a kind whose backend is incomplete on purpose; its name."""
    backend = cast("Backend", _BuildOnly())
    record = KindRecord(name="acme-build-only", backend=backend)
    monkeypatch.setitem(_kinds._KINDS, record.name, record)
    return record.name


def _package(tmp_path: Path, kind_name: str) -> Package:
    directory = tmp_path / "packages" / "thing"
    directory.mkdir(parents=True, exist_ok=True)
    return Package(
        directory=directory,
        path="packages/thing",
        name="acme-thing",
        kind=kind_name,
        depends=(),
    )


def _answers_nothing(package: Package) -> None:
    assert _lifecycle.declared_requirements(package) is None
    assert _lifecycle.module_roots(package) == ()
    assert _lifecycle.references(package) == ()


def test_an_abstract_kind_answers_nothing_and_refuses_to_build(
    tmp_path: Path,
) -> None:
    package = _package(tmp_path, "base")
    _answers_nothing(package)
    with pytest.raises(_FAILURES, match="abstract kind and builds nothing"):
        _lifecycle.build(package, tmp_path)


def test_a_backend_that_leaves_a_function_out_answers_nothing_for_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The seam reads these four only where the backend has them.
    package = _package(tmp_path, _build_only_kind(monkeypatch))
    _answers_nothing(package)
    assert _lifecycle.build(package, tmp_path) == package.directory / "dist"


def test_layering_graph_names_a_kind_that_reads_no_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    package = _package(tmp_path, _build_only_kind(monkeypatch))
    assert graph_problems(tmp_path, (package,)) == [
        "packages/thing: kind 'acme-build-only' registers no"
        " declared_requirements extractor, so the lint cannot compare its"
        " edges; add the callable to the backend"
    ]


# The bridge: a package's extensions answer where one of them answers a
# query, and its kind backend answers the rest.

#: A package-level extension's identity, the start of every fake here.
PACKAGE = '[extension]\nlevels = ["package"]\n'


def _listing(tmp_path: Path, *extensions: str) -> Package:
    """A python package whose manifest the kind backend reads, listing *extensions*."""
    directory = tmp_path / "packages" / "core"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "pyproject.toml").write_text(
        '[project]\nname = "acme-core"\nversion = "1.0.0"\n'
        'dependencies = ["acme-base>=0.2"]\n'
    )
    return Package(
        directory=directory,
        path="packages/core",
        name="acme-core",
        kind="python",
        depends=(),
        extensions=extensions,
    )


def test_a_package_listing_no_extension_takes_its_kinds_answers(
    tmp_path: Path,
) -> None:
    package = _listing(tmp_path)
    assert _lifecycle.current_version(package) == "1.0.0"
    assert _lifecycle.declared_requirements(package) == {"acme-base": ">=0.2"}


def test_a_for_table_s_answer_waits_for_its_target_and_the_kind_answers_meanwhile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_package_extensions(
        tmp_path,
        monkeypatch,
        answering=PACKAGE
        + '\n[for."acme.other".queries]\n'
        + 'current-version = "{package}._steps:version"\n',
        other=PACKAGE,
    )
    fake_steps(tmp_path, "answering", "def version(package):\n    return '9.9.9'\n")
    root_contract(tmp_path, "[]")
    package_contract(tmp_path, "core", '["acme.answering"]')
    package = _listing(tmp_path, "acme.answering")
    # The target unlisted: the [for] table's answer is never asked, and
    # the kind's backend answers from the manifest.
    assert _lifecycle.current_version(package) == "1.0.0"
    # Listed, by another package: the [for] table's answer is taken.
    package_contract(tmp_path, "other", '["acme.other"]')
    assert _lifecycle.current_version(package) == "9.9.9"


def test_an_extension_that_answers_no_query_leaves_each_to_the_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_package_extensions(tmp_path, monkeypatch, quiet=PACKAGE)
    package = _listing(tmp_path, "acme.quiet")
    assert _lifecycle.current_version(package) == "1.0.0"
    assert _lifecycle.declared_requirements(package) == {"acme-base": ">=0.2"}


def test_a_package_whose_extensions_answer_takes_their_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queries = "\n".join(
        f'{query} = "{{package}}._steps:{function}"'
        for query, function in (
            ("current-version", "version"),
            ("version-files", "files"),
            ("requirements", "requirements"),
            ("module-roots", "roots"),
            ("public-modules", "roots"),
        )
    )
    fake_package_extensions(
        tmp_path, monkeypatch, answering=PACKAGE + "\n[queries]\n" + queries + "\n"
    )
    fake_steps(
        tmp_path,
        "answering",
        "def version(package):\n    return '9.9.9'\n\n\n"
        "def files(package):\n    return (package.directory / 'VERSION',)\n\n\n"
        "def requirements(package):\n    return {'acme-other': '>=1'}\n\n\n"
        "def roots(package):\n    return ('acme.answered',)\n",
    )
    package = _listing(tmp_path, "acme.answering")
    assert _lifecycle.current_version(package) == "9.9.9"
    assert _lifecycle.version_files(package) == (package.directory / "VERSION",)
    assert _lifecycle.declared_requirements(package) == {"acme-other": ">=1"}
    assert _lifecycle.module_roots(package) == ("acme.answered",)
    assert _lifecycle.public_modules(package) == ("acme.answered",)


# What a package's sources reference: its extensions' answer where one
# of them answers, its kind's otherwise; every file inside the package,
# and one extension answering for each.

#: A fake's declaration answering the references query from its steps.
ANSWERS_REFERENCES = (
    PACKAGE + '\n[queries]\nreferences = "{package}._steps:references"\n'
)


def _references_of(*paths: str) -> str:
    """A fake's steps answering the references query with a record per path."""
    records = ", ".join(f"SourceReferences({path!r})" for path in paths)
    return (
        "from livery.workshop._packages import SourceReferences\n\n\n"
        f"def references(package):\n    return ({records},)\n"
    )


def test_a_source_outside_its_package_refuses_naming_the_extension_and_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_package_extensions(tmp_path, monkeypatch, outside=ANSWERS_REFERENCES)
    fake_steps(tmp_path, "outside", _references_of("packages/other/src/x.py"))
    package = _listing(tmp_path, "acme.outside")
    with pytest.raises(
        QueryError,
        match=re.escape(
            "packages/core: references: acme.outside answers for"
            " packages/other/src/x.py, a file outside the package"
        ),
    ):
        _lifecycle.references(package)
    # A path that climbs out of the package is outside it as well.
    fake_steps(tmp_path, "outside", _references_of("packages/core/../other/x.py"))
    with pytest.raises(QueryError, match=re.escape("packages/core/../other/x.py")):
        _lifecycle.references(package)


def test_two_extensions_answering_one_source_refuse_naming_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_package_extensions(
        tmp_path, monkeypatch, one=ANSWERS_REFERENCES, two=ANSWERS_REFERENCES
    )
    fake_steps(tmp_path, "one", _references_of("packages/core/src/x.py"))
    fake_steps(tmp_path, "two", _references_of("packages/core/src/x.py"))
    package = _listing(tmp_path, "acme.one", "acme.two")
    with pytest.raises(
        QueryError,
        match=re.escape(
            "packages/core: references: acme.one and acme.two both answer for"
            " packages/core/src/x.py"
        ),
    ):
        _lifecycle.references(package)


def test_a_package_whose_extensions_read_no_source_references_nothing(
    tmp_path: Path,
) -> None:
    # A C++ package, listing its extensions or not: none of them, and not
    # its kind, reads a source for references, the python one under its
    # src included.
    directory = tmp_path / "packages" / "native"
    (directory / "src").mkdir(parents=True)
    (directory / "src" / "native.cpp").write_text('#include "other/other.hpp"\n')
    (directory / "src" / "helper.py").write_text("import acme.other\n")
    for extensions in ((), ("cmake", "conan")):
        package = Package(
            directory=directory,
            path="packages/native",
            name="acme-native",
            kind="cpp-conan",
            depends=(),
            extensions=extensions,
        )
        assert _lifecycle.module_roots(package) == ()
        assert _lifecycle.references(package) == ()


def test_a_package_that_lists_nothing_answers_through_its_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The fallback first: an installed extension answering the query is
    # never asked for a package that lists nothing; its kind answers.
    fake_package_extensions(tmp_path, monkeypatch, loud=ANSWERS_REFERENCES)
    fake_steps(tmp_path, "loud", _references_of("packages/core/src/answered.py"))
    package = _listing(tmp_path)
    source = package.directory / "src" / "acme" / "core" / "__init__.py"
    source.parent.mkdir(parents=True)
    source.write_text("import json\n")
    assert _lifecycle.references(package) == (
        SourceReferences(
            "packages/core/src/acme/core/__init__.py",
            module="acme.core",
            references=(ReferencedName("json"),),
        ),
    )
    # Listing the extension takes its answer instead.
    listing = _listing(tmp_path, "acme.loud")
    assert _lifecycle.references(listing) == (
        SourceReferences("packages/core/src/answered.py"),
    )


def _stepping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str, phases: str
) -> Package:
    """A python package listing one fake extension, whose steps are *source*."""
    fake_package_extensions(tmp_path, monkeypatch, stepping=PACKAGE + phases)
    fake_steps(tmp_path, "stepping", source)
    return _listing(tmp_path, "acme.stepping")


def test_a_phase_whose_steps_provide_nothing_the_train_reads_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _stepping(
        tmp_path,
        monkeypatch,
        "def main(ctx):\n    del ctx\n",
        '\n[phases.build.step]\nmain = "{package}._steps:main"\n',
    )
    with pytest.raises(
        _FAILURES,
        match="the build phase's steps provide no dist, which the release train"
        " reads from the phase",
    ):
        _lifecycle.build(package, tmp_path)


def test_a_package_whose_extensions_add_steps_goes_through_its_phases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = (
        "from pathlib import Path\n\n\n"
        "def stamp(ctx):\n"
        "    ctx.provide('changed', ['VERSION=' + str(ctx.read('version'))])\n\n\n"
        "def build(ctx):\n"
        "    ctx.provide('dist', Path('dist-' + str(ctx.read('epoch'))))\n\n\n"
        "def publish(ctx):\n"
        "    target = ctx.read('registry-target')\n"
        "    ctx.provide('published', target['url'] + ctx.read('version') == 'idx2')\n"
    )
    phases = (
        '\n[phases.stamp.step]\nmain = "{package}._steps:stamp"\n'
        'reads = ["version"]\nprovides = { changed = "strs" }\n'
        '\n[phases.build.step]\nmain = "{package}._steps:build"\nreads = ["epoch"]\n'
        'provides = { dist = "path" }\n'
        '\n[phases.publish.step]\nmain = "{package}._steps:publish"\n'
        'reads = ["version", "registry-target"]\nprovides = { published = "bool" }\n'
    )
    package = _stepping(tmp_path, monkeypatch, source, phases)
    assert _lifecycle.stamp(package, tmp_path, "2") == ["VERSION=2"]
    assert _lifecycle.build(package, tmp_path, epoch=5) == Path("dist-5")
    target = RegistryTarget(kind="python", url="idx")
    assert _lifecycle.publish(package, tmp_path, version="2", target=target) is True


def _member_listing(tmp_path: Path, name: str, *extensions: str) -> Package:
    """A python package at ``packages/<name>`` listing *extensions*."""
    return Package(
        directory=tmp_path / "packages" / name,
        path=f"packages/{name}",
        name=f"acme-{name}",
        kind="python",
        depends=(),
        extensions=extensions,
    )


def test_a_step_deleted_in_a_set_never_runs_there_and_runs_elsewhere(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from livery.workshop._extensions import declaration
    from livery.workshop._phases import PhaseContext, run_phase

    build = (
        '\nmain = "{package}._steps:build"\nreads = ["epoch"]\n'
        'provides = { dist = "path" }\n'
    )
    fake_package_extensions(
        tmp_path,
        monkeypatch,
        base=PACKAGE + "\n[phases.build.base]" + build,
        over=PACKAGE
        + 'requires = ["acme.base"]\n'
        + "\n[phases.build.over]"
        + build
        + "\n[phases.build.over.deletes]\n"
        + '"acme.base:build.base" = "it builds the dist this one builds"\n',
    )
    for name in ("base", "over"):
        fake_steps(
            tmp_path,
            name,
            "from pathlib import Path\n\n\n"
            "def build(ctx):\n"
            f"    print('{name} built ' + ctx.member.path)\n"
            f"    ctx.provide('dist', Path('dist-{name}'))\n",
        )
    plain = _member_listing(tmp_path, "plain", "acme.base")
    over = _member_listing(tmp_path, "over", "acme.over")
    # The release train's build: the set that holds the deleter leaves the
    # deleted step out, and a set that does not runs it.
    assert _lifecycle.build(over, tmp_path) == Path("dist-over")
    assert _lifecycle.build(plain, tmp_path) == Path("dist-base")
    said = capsys.readouterr().out
    assert "over built packages/over" in said
    assert "base built packages/plain" in said
    assert "base built packages/over" not in said
    # A walk over the deleted step itself, as the gate walks a registered
    # step: the package whose set deletes it skips, naming who and why.
    declared = declaration("acme.base")
    assert declared is not None
    ctx = PhaseContext(root=tmp_path, packages=(plain, over), phase="build")
    run_phase(ctx, declared.phases["build"], inputs={"build": {"epoch": 0}})
    said = capsys.readouterr().out
    assert "base built packages/plain" in said
    assert "base built packages/over" not in said
    assert (
        "  build.base: packages/over skips (acme.over deletes it: it builds the"
        " dist this one builds)" in said
    )


def test_a_package_its_kind_tests_nothing_isolated_for_refuses_a_leg(
    tmp_path: Path,
) -> None:
    package = Package(
        directory=tmp_path / "packages" / "native",
        path="packages/native",
        name="acme-native",
        kind="cpp-conan",
        depends=(),
    )
    assert _lifecycle.has_isolated_test(package) is False
    with pytest.raises(_FAILURES, match="nothing tests its release isolated"):
        _lifecycle.isolated_test(
            package, tmp_path, resolution="latest", release_dirs=()
        )


def test_a_set_testing_on_the_tree_side_alone_has_no_isolated_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The fallback first: the set's one test step serves the tree side,
    # so the release falls back to the kind, which the fixture's kind
    # answers with nothing.
    package = _stepping(
        tmp_path,
        monkeypatch,
        "def main(ctx):\n    del ctx\n",
        '\n[phases.test.tree]\nmain = "{package}._steps:main"\nisolated = false\n',
    )
    native = Package(
        directory=package.directory,
        path=package.path,
        name=package.name,
        kind="cpp-conan",
        depends=(),
        extensions=package.extensions,
    )
    assert _lifecycle.has_isolated_test(native) is False


def test_a_package_whose_extensions_test_isolated_and_verify_goes_through_them(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = (
        "def tree(ctx):\n"
        "    print('the tree side ran')\n\n\n"
        "def isolated(ctx):\n"
        "    dirs = ctx.read('release-dirs')\n"
        "    ctx.provide('resolved', {'acme-base': ctx.read('resolution'),"
        " 'dirs': str(len(dirs))})\n\n\n"
        "def verify(ctx):\n"
        "    ctx.provide('exit-code', 0 if ctx.read('version') == '1.2.0' else 3)\n"
    )
    phases = (
        '\n[phases.test.tree]\nmain = "{package}._steps:tree"\nisolated = false\n'
        '\n[phases.test.step]\nmain = "{package}._steps:isolated"\n'
        'isolated = true\nreads = ["resolution", "release-dirs"]\n'
        'provides = { resolved = "table" }\n'
        '\n[phases.verify.step]\nmain = "{package}._steps:verify"\n'
        'reads = ["version"]\nprovides = { exit-code = "int" }\n'
    )
    package = _stepping(tmp_path, monkeypatch, source, phases)
    assert _lifecycle.has_isolated_test(package) is True
    resolved = _lifecycle.isolated_test(
        package, tmp_path, resolution="floor", release_dirs=(tmp_path,)
    )
    assert resolved == {"acme-base": "floor", "dirs": "1"}
    # The isolated side runs the step serving it, and not the tree's.
    assert "the tree side ran" not in capsys.readouterr().out
    code = _lifecycle.verify(
        package,
        tmp_path,
        tree=tmp_path,
        version="1.2.0",
        python="3.14",
        index="",
        extras="",
    )
    assert code == 0


# What a package releases and the registry it releases to: its set's
# answers to the distributions and registry queries, and for a package
# that lists nothing, what its kind stands for.

#: A conan recipe that names a reference, and a consumer's, with
#: requirements alone.
REFERENCE = 'class R:\n    name = "acme-geometry"\n    version = "0.3.0"\n'
CONSUMER = 'class R:\n    requires = ("fmt/[>=11.0]",)\n'

#: The python index and its credential the tests declare through the
#: environment, which the registry ladder reads first.
INDEX = "https://idx.example/simple"
INDEX_TOKEN = "index-token"


def _releasing(
    root: Path, member: str, kind_name: str, *extensions: str, recipe: str = ""
) -> Package:
    """``packages/<member>`` of *root*: *kind_name*, listing *extensions*.

    It carries a ``pyproject.toml``, and *recipe* as its ``conanfile.py``
    when one is given.
    """
    directory = root / "packages" / member
    directory.mkdir(parents=True)
    (directory / "pyproject.toml").write_text(
        f'[project]\nname = "acme-{member}"\nversion = "0.3.0"\n'
    )
    if recipe:
        (directory / "conanfile.py").write_text(recipe)
    return Package(
        directory=directory,
        path=f"packages/{member}",
        name=f"acme-{member}",
        kind=kind_name,
        depends=(),
        extensions=extensions,
    )


def _no_target(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse every registry resolution, so a test sees that none happened."""
    from livery.workshop import _registries

    def _resolved(root: Path, kind: str) -> RegistryTarget:
        raise AssertionError(f"a {kind} target was resolved under {root}")

    monkeypatch.setattr(_registries, "resolve_registry", _resolved)


def _declared(root: Path, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Declare *root*'s python index and a conan folder; each index probe built.

    The python probe stands in for the simple index and records the
    address and credential it was built for; conan's folder probe runs
    for real over ``share/`` beside *root*.
    """
    import livery.forge as forge

    (root / "workshop.toml").write_text("[workspace]\n")
    (root / "share").mkdir()
    monkeypatch.setenv("PYTHON_REGISTRY_URL", INDEX)
    monkeypatch.setenv("PYTHON_REGISTRY_TOKEN", INDEX_TOKEN)
    monkeypatch.delenv("PYTHON_PUBLISH_INDEX", raising=False)
    monkeypatch.setenv("CONAN_REMOTE_URL", str(root / "share"))
    built: list[tuple[str, str]] = []

    class _Index:
        def __init__(self, url: str, *, token: str = "") -> None:
            built.append((url, token))

        def versions(self, name: str) -> tuple[str, ...]:
            return (f"{name} 1.0.0",)

    monkeypatch.setattr(forge, "SimpleRegistry", _Index)
    return built


def test_a_member_answering_two_registries_refuses_naming_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.footman import Failed

    # A python package whose recipe names a conan reference: python
    # releases its distribution and conan its package, and a package
    # releases to one registry. The refusal comes before either probe
    # resolves its target.
    _no_target(monkeypatch)
    package = _releasing(
        tmp_path, "geometry", "python", "python", "conan", recipe=REFERENCE
    )
    assert _lifecycle.distributions(package) == (
        ("conan", "acme-geometry"),
        ("python", "acme-geometry"),
    )
    refusal = (
        "packages/geometry: its extensions answer more than one registry, conan"
        " and python: each releases a distribution of it, and a package releases"
        " to one registry. List the extensions that release it to one of them"
    )
    for asked in (_lifecycle.ecosystem, _lifecycle.registry):
        with pytest.raises(Failed) as refused:
            asked(package)
        assert str(refused.value) == refusal


def test_a_member_whose_extensions_answer_no_probe_of_its_registry_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.footman import Failed

    # An extension that releases the package's distribution and answers
    # no probe of the registry it goes to: the train could upload and
    # never see the upload served, so it refuses before anything uploads.
    fake_package_extensions(
        tmp_path,
        monkeypatch,
        releasing=PACKAGE
        + '\n[queries]\ndistributions = "{package}._steps:distributions"\n',
    )
    fake_steps(
        tmp_path,
        "releasing",
        "def distributions(package):\n    return (('acme', package.name),)\n",
    )
    package = _listing(tmp_path, "acme.releasing")
    assert _lifecycle.ecosystem(package) == "acme"
    with pytest.raises(Failed) as refused:
        _lifecycle.registry(package)
    assert str(refused.value) == (
        "packages/core: its extensions release it to acme and answer no probe of"
        " that registry, so the release train cannot see what it serves"
    )


def test_a_member_listing_nothing_takes_its_kind_s_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The bridge while package kinds remain: a member that lists no
    # extension is asked as holding what its kind stands for. The
    # nanobind member consumes conan and releases its python wheel.
    built = _declared(tmp_path, monkeypatch)
    core = _releasing(tmp_path, "core", "python")
    native = _releasing(tmp_path, "native", "python-nanobind", recipe=CONSUMER)
    geometry = _releasing(tmp_path, "geometry", "cpp-conan", recipe=REFERENCE)
    assert [
        _lifecycle.distributions(member) for member in (core, native, geometry)
    ] == [
        (("python", "acme-core"),),
        (("python", "acme-native"),),
        (("conan", "acme-geometry"),),
    ]
    assert [_lifecycle.ecosystem(member) for member in (core, native, geometry)] == [
        "python",
        "python",
        "conan",
    ]
    # python's probe is the index the ladder resolves, read with its
    # credential; conan's reads the folder the ladder resolves for conan.
    assert _lifecycle.registry(core).versions("acme-core") == ("acme-core 1.0.0",)
    assert _lifecycle.registry(native).versions("acme-native") == ("acme-native 1.0.0",)
    assert built == [(INDEX, INDEX_TOKEN), (INDEX, INDEX_TOKEN)]
    (tmp_path / "share" / "acme-geometry-0.3.0.tgz").touch()
    assert _lifecycle.registry(geometry).versions("acme-geometry") == ("0.3.0",)
    # Listing python, the member is answered by python alone.
    listed = _releasing(tmp_path, "listed", "cpp-conan", "python", recipe=REFERENCE)
    assert _lifecycle.distributions(listed) == (("python", "acme-listed"),)
