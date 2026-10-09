"""The lifecycle seam: what it answers where a kind has nothing to say."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from livery.workshop import _kinds, _lifecycle
from livery.workshop._kinds import KindRecord
from livery.workshop._packages import Neighbours, Package, graph_problems

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
