"""The kind registry: vocabulary, dispatch, chain, and guards."""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.workshop._kinds import (
    CiContract,
    KindRecord,
    backend_for,
    kind_chain,
    kind_for,
    kind_names,
    kind_tools,
    register_kind,
)
from livery.workshop._packages import Package

_FAILURES = (BaseException,)


@pytest.fixture
def restored_registry():
    from livery.workshop import _kinds

    before = dict(_kinds._KINDS)
    yield
    _kinds._KINDS.clear()
    _kinds._KINDS.update(before)


class _FakeBackend:
    """A future kind's backend, the fake the guards are forced with."""

    def __init__(self) -> None:
        self.built: list[str] = []

    def build(self, package: Package, root: Path, *, epoch: int = 0) -> Path:
        self.built.append(package.name)
        return package.directory / "dist"

    def check(self, package: Package, root: Path) -> None:
        return None


def _package(tmp_path: Path, type_name: str) -> Package:
    directory = tmp_path / "packages" / "thing"
    directory.mkdir(parents=True, exist_ok=True)
    return Package(
        directory=directory,
        path="packages/thing",
        name="acme-thing",
        type=type_name,
        depends=(),
    )


# The refusals first.


def test_an_unknown_kind_refuses_naming_the_vocabulary(tmp_path: Path) -> None:
    with pytest.raises(_FAILURES, match="not a registered package kind"):
        backend_for(_package(tmp_path, "conan"))
    with pytest.raises(_FAILURES, match="python"):
        kind_for("carrier-pigeon")


def test_a_child_of_an_unregistered_parent_refuses(restored_registry) -> None:
    with pytest.raises(_FAILURES, match="register the parent first"):
        register_kind(
            KindRecord(name="python-orphan", backend=_FakeBackend(), parent="cpp")
        )


def test_a_chain_cycle_refuses_naming_it(restored_registry) -> None:
    from livery.workshop import _kinds

    fake = _FakeBackend()
    register_kind(KindRecord(name="a", backend=fake))
    register_kind(KindRecord(name="b", backend=fake, parent="a"))
    _kinds._KINDS["a"] = KindRecord(name="a", backend=fake, parent="b")
    with pytest.raises(_FAILURES, match="cycles"):
        kind_chain("b")


# The fake future kind registers and dispatches.


def test_a_registered_kind_dispatches_and_chains(
    restored_registry, tmp_path: Path
) -> None:
    fake = _FakeBackend()
    register_kind(
        KindRecord(
            name="python-fake",
            backend=fake,
            template="package-python-fake",
            parent="python",
            tools=("faketool",),
            ci=CiContract(check_verbs=("format", "test")),
        )
    )
    assert "python-fake" in kind_names()
    package = _package(tmp_path, "python-fake")
    backend_for(package).build(package, tmp_path)
    assert fake.built == ["acme-thing"]
    chain = kind_chain("python-fake")
    assert [record.name for record in chain] == ["python", "python-fake"]


def test_tools_union_along_the_chain_only_when_present(restored_registry) -> None:
    fake = _FakeBackend()
    register_kind(KindRecord(name="cpp-fake", backend=fake, tools=("cmake", "ninja")))
    register_kind(
        KindRecord(
            name="cpp-fake-child", backend=fake, parent="cpp-fake", tools=("conan",)
        )
    )
    assert kind_tools({"python"}) == ()
    assert kind_tools({"cpp-fake-child"}) == ("cmake", "conan", "ninja")


def test_the_workspace_profile_grows_only_with_the_kind(
    restored_registry, tmp_path: Path
) -> None:
    from livery.workshop._env_tasks import tool_profile

    root = tmp_path / "ws"
    (root / "packages" / "member").mkdir(parents=True)
    (root / "workshop.toml").write_text("[workspace]\n")
    (root / "packages" / "member" / "workshop.toml").write_text(
        'type = "python"\nname = "acme-member"\n'
    )
    (root / "packages" / "member" / "pyproject.toml").write_text(
        '[project]\nname = "acme-member"\n'
    )
    baseline = tool_profile(root)
    assert "cmake" not in baseline
    register_kind(KindRecord(name="cpp-fake", backend=_FakeBackend(), tools=("cmake",)))
    (root / "packages" / "native").mkdir(parents=True)
    (root / "packages" / "native" / "workshop.toml").write_text(
        'type = "cpp-fake"\nname = "acme-native"\n'
    )
    (root / "packages" / "native" / "pyproject.toml").write_text(
        '[project]\nname = "acme-native"\n'
    )
    grown = tool_profile(root)
    assert "cmake" in grown and set(baseline) <= set(grown)


def test_template_chain_orders_parent_first(restored_registry) -> None:
    from livery.workshop._kinds import template_chain

    fake = _FakeBackend()
    register_kind(
        KindRecord(
            name="python-fake",
            backend=fake,
            template="package-python-fake",
            parent="python",
        )
    )
    assert template_chain("package-python-fake") == (
        "package-python",
        "package-python-fake",
    )
    # A template variant the registry does not map renders alone.
    assert template_chain("package-python-layer") == ("package-python-layer",)


def test_managed_files_union_along_the_chain(restored_registry) -> None:
    from livery.workshop._kinds import managed_files
    from livery.workshop._templates import PACKAGE_MANAGED

    fake = _FakeBackend()
    register_kind(
        KindRecord(
            name="python-fake",
            backend=fake,
            parent="python",
            managed=("CMakeLists.txt",),
        )
    )
    assert managed_files("python-fake") == ("CMakeLists.txt", "cliff.toml")
    # The legacy constant stays pinned to the python kind's set.
    assert managed_files("python") == PACKAGE_MANAGED
