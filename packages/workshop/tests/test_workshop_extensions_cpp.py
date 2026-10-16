"""The cpp extension in the wheel: the compilers a package that holds it needs."""

from __future__ import annotations

from pathlib import Path

import pytest

from workshop_installed import skip_unless_installed

#: The root contract of a workspace here, which lists no extension.
ROOT_CONTRACT = '[workspace]\nname = "acme"\nnamespace = "acme"\n'


def _workspace(root: Path, members: dict[str, str]) -> Path:
    """A workspace at *root* with a member of each contract, by its directory."""
    root.mkdir(parents=True)
    (root / "workshop.toml").write_text(ROOT_CONTRACT)
    for member, contract in members.items():
        (root / "packages" / member).mkdir(parents=True)
        (root / "packages" / member / "workshop.toml").write_text(contract)
    return root


def _compiler_findings(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> list[tuple[str, str, str]]:
    """The host doctor's findings on compilers in *root*: what, found and fix."""
    from livery.workshop import _doctor

    monkeypatch.setattr(_doctor, "workspace_root", lambda start=None: root)
    return [
        (finding.what, finding.found, finding.fix)
        for finding in _doctor.host()
        if finding.what.startswith("host:")
    ]


def test_a_workspace_without_cpp_names_no_compiler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._env_tasks import missing_host_tools

    # A host on which no compiler resolves.
    monkeypatch.setattr("shutil.which", lambda *_args, **_kwargs: None)
    assert missing_host_tools(_workspace(tmp_path / "bare", {})) == ()
    root = _workspace(
        tmp_path / "acme",
        {
            # python answers no host tool, and a member listing nothing
            # holds no extension to answer one.
            "echo": 'name = "acme-echo"\nextensions = ["python"]\n',
            "plain": 'name = "acme-plain"\n',
        },
    )
    # The member holding python carries a pyproject.toml.
    for member in ("echo",):
        (root / "packages" / member / "pyproject.toml").write_text(
            f'[project]\nname = "acme-{member}"\n'
        )
    assert missing_host_tools(root) == ()
    assert _compiler_findings(root, monkeypatch) == [
        ("host: every compiler the workspace's packages need is present", "", "")
    ]


def test_a_missing_compiler_is_named_for_a_package_holding_cpp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from livery.workshop import _env_tasks
    from livery.workshop._packages import discover_packages
    from livery.workshop._queries import HOST_TOOLS, answer

    skip_unless_installed("conan")
    root = _workspace(
        tmp_path / "acme",
        {
            "lib": 'name = "acme-lib"\nextensions = ["cpp"]\n',
            # conan requires cpp, so a member listing conan holds it.
            "recipe": 'name = "acme-recipe"\nextensions = ["conan"]\n',
        },
    )
    assert {
        package.member: answer(package, HOST_TOOLS)
        for package in discover_packages(root)
    } == {"lib": ("cc", "c++"), "recipe": ("cc", "c++")}
    # A host that resolves cc and lacks c++: c++ is named, once.
    monkeypatch.setattr(
        "shutil.which",
        lambda tool, *_args, **_kwargs: None if tool == "c++" else f"/usr/bin/{tool}",
    )
    assert _env_tasks.missing_host_tools(root) == ("c++",)
    assert _compiler_findings(root, monkeypatch) == [
        (
            "host: c++",
            "MISSING, a compiler the workspace's packages need",
            "install the platform toolchain",
        )
    ]
    # fm env.check names it among the problems that make it red.
    monkeypatch.setattr(
        "livery.workshop._extensions.workspace_root", lambda start=None: root
    )
    monkeypatch.setattr(_env_tasks, "tool_profile", lambda _root: ())
    assert _env_tasks.env_check() == 1
    assert "  c++: MISSING (a host compiler; " in capsys.readouterr().out
