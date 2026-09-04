"""The registry resolution ladder, every rung forced."""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.workshop._registries import RegistryTarget, resolve_registry

_FAILURES = (BaseException,)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "PYTHON_REGISTRY_URL",
        "PYTHON_PUBLISH_INDEX",
        "CONAN_REMOTE_URL",
        "CONTAINER_REGISTRY",
    ):
        monkeypatch.delenv(name, raising=False)


def _workspace(tmp_path: Path, contract: str = "[workspace]\n") -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "workshop.toml").write_text(contract)
    return root


# The refusal first, then each rung from the bottom up.


def test_an_unknown_kind_refuses_naming_the_vocabulary(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    with pytest.raises(_FAILURES, match="python, conan, container"):
        resolve_registry(root, "npm")


def test_no_rung_answering_refuses_teaching_the_declaration(
    tmp_path: Path,
) -> None:
    # No env, no table, no forge, and conan has no ecosystem default.
    root = _workspace(tmp_path)
    with pytest.raises(_FAILURES, match="local folder included"):
        resolve_registry(root, "conan")


def test_python_falls_through_to_the_ecosystem_default(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    target = resolve_registry(root, "python")
    assert target == RegistryTarget(
        kind="python", url="https://pypi.org/simple", publish_url=""
    )


def test_the_contract_table_declares_any_host(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path,
        "[workspace]\n"
        '[registries]\nconan = "https://artifactory.example/api/conan/team"\n'
        'container = "harbor.example/acme"\n',
    )
    assert resolve_registry(root, "conan").url == (
        "https://artifactory.example/api/conan/team"
    )
    container = resolve_registry(root, "container")
    assert container.url == "harbor.example/acme" and not container.local


def test_a_folder_declaration_is_first_class(tmp_path: Path) -> None:
    share = tmp_path / "share" / "registry"
    root = _workspace(
        tmp_path,
        f'[workspace]\n[registries]\nconan = "{share}"\n',
    )
    target = resolve_registry(root, "conan")
    assert target.local and target.url == str(share)
    (tmp_path / "two").mkdir()
    root2 = _workspace(
        tmp_path / "two",
        '[workspace]\n[registries]\ncontainer = "file:///mnt/registry/acme"\n',
    )
    target2 = resolve_registry(root2, "container")
    assert target2.local and target2.url == "/mnt/registry/acme"


def test_the_env_declaration_wins_over_the_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(
        tmp_path,
        '[workspace]\n[registries]\nconan = "https://committed.example/conan"\n',
    )
    monkeypatch.setenv("CONAN_REMOTE_URL", "https://machine.example/conan")
    assert resolve_registry(root, "conan").url == "https://machine.example/conan"


def test_a_python_declaration_splits_read_and_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHON_REGISTRY_URL", "https://idx.example/simple")
    monkeypatch.setenv("PYTHON_PUBLISH_INDEX", "https://idx.example/pypi")
    root = _workspace(tmp_path)
    target = resolve_registry(root, "python")
    assert target.url == "https://idx.example/simple"
    assert target.publish_url == "https://idx.example/pypi"


def test_the_forge_rung_serves_when_nothing_is_declared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.forge.testing import FakeForge

    root = _workspace(
        tmp_path, '[workspace]\n[forge]\nkind = "gitea"\nowner = "acme"\n'
    )
    monkeypatch.setattr(
        "livery.workshop._forge_lane.this_forge", lambda _root: FakeForge()
    )
    python = resolve_registry(root, "python")
    assert python.url == "https://fake.example/api/packages/acme/python/simple"
    assert python.publish_url == "https://fake.example/api/packages/acme/python"
    assert resolve_registry(root, "container").url == "registry.fake.example/acme"


def test_an_unreachable_forge_is_a_silent_rung_not_a_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The forge rung not answering continues the ladder with the
    # reason printed; python still resolves through the default. The
    # machine's own rig credentials must not answer for the fixture.
    for name in ("GITEA_URL", "GITEA_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    root = _workspace(
        tmp_path, '[workspace]\n[forge]\nkind = "gitea"\nowner = "acme"\n'
    )
    target = resolve_registry(root, "python")
    assert target.url == "https://pypi.org/simple"
    assert "did not answer" in capsys.readouterr().out
