"""The wheels matrix: the labels platform-wheel members declare, and the refusals."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from livery.workshop import _wheels
from livery.workshop._packages import discover_packages


def _member(root: Path, name: str, contract: str) -> None:
    directory = root / "packages" / name
    (directory / "src").mkdir(parents=True)
    (directory / "workshop.toml").write_text(contract)
    (directory / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "0.1.0"\n'
    )


def _refusal(action: Callable[[], object]) -> str:
    with pytest.raises(BaseException) as caught:
        action()
    return str(caught.value)


# --- refusals first ---------------------------------------------------------


def test_a_platform_wheel_member_without_the_key_refuses_naming_it(
    tmp_path: Path,
) -> None:
    _member(tmp_path, "ext", 'type = "python-nanobind"\nname = "ext"\n')
    message = _refusal(lambda: _wheels.wheel_runners(tmp_path))
    assert "packages/ext: [ci] wheel-platforms is missing" in message
    assert "python-nanobind" in message and '"ubuntu-latest"' in message


def test_an_empty_or_malformed_list_refuses(tmp_path: Path) -> None:
    _member(
        tmp_path,
        "ext",
        'type = "python-nanobind"\nname = "ext"\n[ci]\nwheel-platforms = []\n',
    )
    assert "must be a non-empty list of runner labels" in _refusal(
        lambda: _wheels.wheel_runners(tmp_path)
    )
    (tmp_path / "packages" / "ext" / "workshop.toml").write_text(
        'type = "python-nanobind"\nname = "ext"\n[ci]\n'
        'wheel-platforms = "ubuntu-latest"\n'
    )
    assert "must be a non-empty list" in _refusal(
        lambda: _wheels.wheel_runners(tmp_path)
    )
    (tmp_path / "packages" / "ext" / "workshop.toml").write_text(
        'type = "python-nanobind"\nname = "ext"\n[ci]\n'
        'wheel-platforms = ["ubuntu-latest", 3]\n'
    )
    assert "entry 3 is not a runner label" in _refusal(
        lambda: _wheels.wheel_runners(tmp_path)
    )


def test_the_key_on_a_pure_member_refuses(tmp_path: Path) -> None:
    _member(
        tmp_path,
        "lib",
        'type = "python"\nname = "lib"\n[ci]\nwheel-platforms = ["ubuntu-latest"]\n',
    )
    message = _refusal(lambda: _wheels.wheel_runners(tmp_path))
    assert "declared on a python member" in message and "remove the key" in message


# --- the happy paths ---------------------------------------------------------


def test_a_pure_workspace_declares_no_wheel_runners(tmp_path: Path) -> None:
    _member(tmp_path, "lib", 'type = "python"\nname = "lib"\n')
    assert _wheels.wheel_runners(tmp_path) == []
    (package,) = discover_packages(tmp_path)
    assert _wheels.declared_wheel_platforms(package) == []
    assert not _wheels.publishes_platform_wheels(package)


def test_the_matrix_is_the_union_in_declaration_order(tmp_path: Path) -> None:
    _member(
        tmp_path,
        "alpha",
        'type = "python-nanobind"\nname = "alpha"\n[ci]\n'
        'wheel-platforms = ["ubuntu-latest", "macos-latest"]\n',
    )
    _member(
        tmp_path,
        "beta",
        'type = "python-nanobind"\nname = "beta"\n[ci]\n'
        'wheel-platforms = ["windows-latest", "ubuntu-latest"]\n',
    )
    _member(tmp_path, "lib", 'type = "python"\nname = "lib"\n')
    assert _wheels.wheel_runners(tmp_path) == [
        "ubuntu-latest",
        "macos-latest",
        "windows-latest",
    ]
    roster = _wheels.member_roster(tmp_path)
    assert roster == [
        {"dir": "alpha", "name": "alpha", "kind": "python-nanobind"},
        {"dir": "beta", "name": "beta", "kind": "python-nanobind"},
        {"dir": "lib", "name": "lib", "kind": "python"},
    ]


def test_the_emitted_wheels_jobs_run_the_declared_labels(tmp_path: Path) -> None:
    from livery.workshop._ci_generate import _facts, _gitea_release, _github_release

    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nlayers = ["livery.workshop"]\n\n[forge]\nkind = "github"\n'
        'owner = "owner"\n\n[ci]\nrunners = ["ubuntu-latest", "macos-latest"]\n'
        'required-context = "gate"\n'
    )
    _member(
        tmp_path,
        "ext",
        'type = "python-nanobind"\nname = "ext"\n[ci]\n'
        'wheel-platforms = ["ubuntu-latest"]\n',
    )
    facts = _facts(tmp_path)
    assert facts["wheel_runners"] == ["ubuntu-latest"]
    assert facts["packages"] == [
        {"dir": "ext", "name": "ext", "kind": "python-nanobind"}
    ]
    github = _github_release(facts, "fm")
    assert "os: [ubuntu-latest]" in github and "--prebuilt" in github
    gitea = _gitea_release(facts, "fm")
    # The wheels matrix follows the declaration, not the check runners.
    assert (
        "runner: [ubuntu-latest]" in gitea
        and "macos-latest" not in gitea.split("publish:")[0]
    )
    # A pure workspace emits no wheels job at all.
    (tmp_path / "packages" / "ext" / "workshop.toml").write_text(
        'type = "python"\nname = "ext"\n'
    )
    pure = _facts(tmp_path)
    assert pure["wheel_runners"] == []
    assert "wheels:" not in _github_release(pure, "fm")
    assert "wheels:" not in _gitea_release(pure, "fm")


def test_the_one_wheel_skip_follows_the_hosts_libc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from livery.workshop._backends import _python_nanobind as nb

    module = "livery.workshop._backends._python_nanobind"
    monkeypatch.setattr(f"{module}._musl_loader_present", lambda: False)
    monkeypatch.setattr(
        "sysconfig.get_config_var", lambda name: "aarch64-unknown-linux-musl"
    )
    assert nb.host_is_musl()
    monkeypatch.setattr(
        "sysconfig.get_config_var", lambda name: "aarch64-unknown-linux-gnu"
    )
    assert not nb.host_is_musl()
    # The loader is the second signal, for a musl-built python elsewhere.
    monkeypatch.setattr(f"{module}._musl_loader_present", lambda: True)
    assert nb.host_is_musl()


def test_the_build_set_names_the_matrix_interpreters() -> None:
    assert _wheels.cibw_build_set(["3.11", "3.14"]) == "cp311-* cp314-*"
    assert _wheels.cibw_build_set(["3.14t"]) == "cp314t-*"
    assert _wheels.cibw_build_set([]) == ""


def test_the_wheels_verb_sets_the_build_set_on_the_task_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import subprocess
    from types import SimpleNamespace

    from livery.workshop._release import release_wheels

    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nlayers = ["livery.workshop"]\n\n[forge]\nkind = "github"\n'
    )
    _member(
        tmp_path,
        "ext",
        'type = "python-nanobind"\nname = "ext"\n[ci]\n'
        'wheel-platforms = ["ubuntu-latest"]\n',
    )
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@l",
            "-c",
            "user.name=T",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "seed",
        ],
        cwd=tmp_path,
        check=True,
    )
    (package,) = discover_packages(tmp_path)
    built: list[dict[str, str]] = []

    class _Backend:
        def build(self, package: object, root: Path, *, epoch: int = 0) -> Path:
            built.append({"epoch": str(epoch)})
            dist = tmp_path / "packages" / "ext" / "dist"
            dist.mkdir(exist_ok=True)
            (dist / "ext-0.1.0-cp314-cp314-manylinux_2_28_x86_64.whl").write_text("")
            return dist

    monkeypatch.setattr("livery.workshop._backends.backend_for", lambda _p: _Backend())
    monkeypatch.setattr(
        "livery.workshop._publish.discover_release",
        lambda root, git, ref: ((package, "0.1.0"),),
    )
    monkeypatch.chdir(tmp_path)
    ctx = SimpleNamespace(env={})
    release_wheels(ctx)  # type: ignore[arg-type]  # a task context stand-in
    # No root pyproject: the matrix is the default floor and the newest minor.
    assert ctx.env == {"CIBW_BUILD": "cp311-* cp314-*", "CIBW_SKIP": ""}
    assert "CIBW_BUILD" not in os.environ
    assert built and built[0]["epoch"].isdigit()
