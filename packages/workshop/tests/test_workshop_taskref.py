"""The per-package task reference. Refusals and fallbacks first."""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.workshop import _env_tasks
from livery.workshop._packages import discover_packages
from livery.workshop._taskref import (
    _group_owners,
    generate_task_reference,
    owning_package,
    task_ownership,
)


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "workshop.toml").write_text("[workspace]\n")
    (root / "pyproject.toml").write_text('[project]\nname = "acme-home"\n')
    (root / "docs").mkdir()
    (root / "docs" / "index.md").write_text("# Home\n")
    for name in ("core", "extra"):
        member = root / "packages" / name
        (member / "src" / "acme" / name).mkdir(parents=True)
        (member / "src" / "acme" / name / "__init__.py").write_text("")
        (member / "workshop.toml").write_text(
            f'type = "python"\nname = "acme-{name}"\n'
        )
        (member / "pyproject.toml").write_text(f'[project]\nname = "acme-{name}"\n')
        (member / "docs").mkdir()
        (member / "docs" / "index.md").write_text(f"# {name}\n")
        (member / "docs" / "nav.toml").write_text(
            'nav = [\n    { "Index" = "index.md" },\n'
            "    # nav:begin tasks\n    # nav:end tasks\n]\n"
        )
    return root


def _stash(root: Path, sources: dict[str, str]) -> None:
    _env_tasks.TASK_SOURCES.clear()
    _env_tasks.TASK_SOURCES.update(
        {name: str(root / path) if path else "" for name, path in sources.items()}
    )


def test_without_a_stash_the_reference_refuses(tmp_path: Path) -> None:
    _env_tasks.TASK_SOURCES.clear()
    with pytest.raises(BaseException, match="real runner invocation"):
        task_ownership(_workspace(tmp_path))


def test_outside_sources_own_nothing(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    packages = discover_packages(root)
    assert owning_package("/usr/lib/python/footman/compose.py", packages) is None
    owner = owning_package(
        str(root / "packages/core/src/acme/core/_tasks.py"), packages
    )
    assert owner is not None and owner.directory.name == "core"


def test_ownership_maps_full_names_and_majorities(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _stash(
        root,
        {
            "docs.build": "packages/core/src/acme/core/_docs.py",
            "docs.serve": "packages/core/src/acme/core/_docs.py",
            "forge.dev.up": "packages/extra/src/acme/extra/_dev.py",
            "sync": "packages/core/src/acme/core/_sync.py",
            "self.install": "",  # a runner builtin: no source, not ours
        },
    )
    owners = task_ownership(root)
    assert owners["forge.dev.up"] == "extra"
    assert "self.install" not in owners
    heads = _group_owners(owners)
    assert heads == {"docs": "core", "forge": "extra", "sync": "core"}


def test_missing_markers_refuse_naming_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _taskref

    root = _workspace(tmp_path)
    (root / "packages/core/docs/nav.toml").write_text(
        'nav = [\n    { "Index" = "index.md" },\n]\n'
    )
    _stash(root, {"docs.build": "packages/core/src/acme/core/_docs.py"})
    monkeypatch.setattr(
        _taskref,
        "_tree",
        lambda _root: {
            "groups": {
                "docs": {
                    "help": "",
                    "tasks": {"build": {"help": "Build.", "params": []}},
                    "groups": {},
                }
            },
            "tasks": {},
        },
    )
    with pytest.raises(BaseException, match="tasks"):
        generate_task_reference(root)


def test_the_reference_renders_pages_nav_and_aliases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _taskref

    root = _workspace(tmp_path)
    _stash(
        root,
        {
            "docs.build": "packages/core/src/acme/core/_docs.py",
            "sync": "packages/core/src/acme/core/_sync.py",
            "forge.dev.up": "packages/extra/src/acme/extra/_dev.py",
        },
    )
    tree = {
        "help": "",
        "groups": {
            "docs": {
                "help": "Docs.",
                "tasks": {"build": {"help": "Build the site.", "params": []}},
                "groups": {},
            },
            "forge": {
                "help": "Forge.",
                "tasks": {},
                "groups": {
                    "dev": {
                        "help": "Dev rigs.",
                        "tasks": {"up": {"help": "Start.", "params": []}},
                        "groups": {},
                    }
                },
            },
        },
        "tasks": {"sync": {"help": "Bring current.", "params": []}},
    }
    monkeypatch.setattr(_taskref, "_tree", lambda _root: tree)
    assert generate_task_reference(root) == ["core", "extra"]
    core_tasks = root / "packages/core/docs/_generated/tasks"
    assert (core_tasks / "docs" / "build.md").is_file()
    assert (core_tasks / "docs" / "index.md").is_file()
    assert (core_tasks / "sync.md").is_file()
    assert (core_tasks / "index.md").is_file()
    extra_tasks = root / "packages/extra/docs/_generated/tasks"
    assert (extra_tasks / "forge" / "dev" / "up.md").is_file()
    nav = (root / "packages/core/docs/nav.toml").read_text()
    assert '{ "Tasks" = [' in nav
    assert '"_generated/tasks/docs/build.md"' in nav
    assert '{ "sync" = "_generated/tasks/sync.md" },' in nav
    extra_nav = (root / "packages/extra/docs/nav.toml").read_text()
    assert '"dev.up" = "_generated/tasks/forge/dev/up.md"' in extra_nav
    # The alias tree: a uniform address redirecting into the owner.
    alias = (root / "docs/_generated/tasks/forge-dev-up.md").read_text()
    assert "../../packages/extra/_generated/tasks/forge/dev/up/" in alias
    assert "window.location.replace" in alias
    # Idempotent: the same tree rewrites the same bytes.
    before = (root / "packages/core/docs/nav.toml").read_bytes()
    generate_task_reference(root)
    assert (root / "packages/core/docs/nav.toml").read_bytes() == before


def test_a_shared_verb_declared_twice_runs_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil as shutil_module

    import footman

    from livery.workshop._docs import run_generators

    root = _workspace(tmp_path)
    for name in ("core", "extra"):
        (root / "packages" / name / "workshop.toml").write_text(
            f'type = "python"\nname = "acme-{name}"\n'
            '[docs]\ngenerators = ["docs.task-reference"]\n'
        )
    monkeypatch.setattr(shutil_module, "which", lambda name: "/stub/fm")
    calls: list[str] = []

    def _record(argv: list[str], **kwargs: object) -> int:
        calls.append(argv[-1])
        return 0

    monkeypatch.setattr(footman, "run", _record)
    assert run_generators(root) == ["docs.task-reference"]
    assert calls == ["docs.task-reference"]
