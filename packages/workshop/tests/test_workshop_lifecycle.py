"""The lifecycle seam: what it answers where a kind has nothing to say."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from livery.workshop import _kinds, _lifecycle
from livery.workshop._kinds import KindRecord
from livery.workshop._packages import Neighbours, Package, graph_problems
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
