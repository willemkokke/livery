"""The lifecycle seam: what it answers where a kind has nothing to say."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from livery.workshop import _kinds, _lifecycle
from livery.workshop._kinds import KindRecord
from livery.workshop._packages import Neighbours, Package, graph_problems
from livery.workshop._registries import RegistryTarget
from workshop_extension_fakes import fake_package_extensions, fake_steps

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
    around = Neighbours(owners={}, by_path={package.path: package})
    assert _lifecycle.declared_requirements(package) is None
    assert _lifecycle.module_roots(package) == ()
    assert _lifecycle.plugin_modules(package) == ()
    assert _lifecycle.referenced_siblings(package, around) is None


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


def test_the_layering_check_names_a_kind_that_reads_no_manifest(
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
        '\n[phases.build]\nmain = "{package}._steps:main"\n',
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
        '\n[phases.stamp]\nmain = "{package}._steps:stamp"\nreads = ["version"]\n'
        'provides = { changed = "strs" }\n'
        '\n[phases.build]\nmain = "{package}._steps:build"\nreads = ["epoch"]\n'
        'provides = { dist = "path" }\n'
        '\n[phases.publish]\nmain = "{package}._steps:publish"\n'
        'reads = ["version", "registry-target"]\nprovides = { published = "bool" }\n'
    )
    package = _stepping(tmp_path, monkeypatch, source, phases)
    assert _lifecycle.stamp(package, tmp_path, "2") == ["VERSION=2"]
    assert _lifecycle.build(package, tmp_path, epoch=5) == Path("dist-5")
    target = RegistryTarget(kind="python", url="idx")
    assert _lifecycle.publish(package, tmp_path, version="2", target=target) is True


def test_a_package_its_kind_proves_nothing_for_refuses_a_leg(tmp_path: Path) -> None:
    package = Package(
        directory=tmp_path / "packages" / "native",
        path="packages/native",
        name="acme-native",
        kind="cpp-conan",
        depends=(),
    )
    assert _lifecycle.proves(package) is False
    with pytest.raises(_FAILURES, match="nothing proves its release"):
        _lifecycle.prove(package, tmp_path, resolution="highest", release_dirs=())


def test_a_package_whose_extensions_prove_and_replay_goes_through_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = (
        "def prove(ctx):\n"
        "    dirs = ctx.read('release-dirs')\n"
        "    ctx.provide('resolved', {'acme-base': ctx.read('resolution'),"
        " 'dirs': str(len(dirs))})\n\n\n"
        "def replay(ctx):\n"
        "    ctx.provide('exit-code', 0 if ctx.read('version') == '1.2.0' else 3)\n"
    )
    phases = (
        '\n[phases.prove]\nmain = "{package}._steps:prove"\n'
        'reads = ["resolution", "release-dirs"]\nprovides = { resolved = "table" }\n'
        '\n[phases.replay]\nmain = "{package}._steps:replay"\n'
        'reads = ["version"]\nprovides = { exit-code = "int" }\n'
    )
    package = _stepping(tmp_path, monkeypatch, source, phases)
    assert _lifecycle.proves(package) is True
    resolved = _lifecycle.prove(
        package, tmp_path, resolution="lowest-direct", release_dirs=(tmp_path,)
    )
    assert resolved == {"acme-base": "lowest-direct", "dirs": "1"}
    code = _lifecycle.replay(
        package,
        tmp_path,
        tree=tmp_path,
        version="1.2.0",
        python="3.14",
        index="",
        extras="",
    )
    assert code == 0
