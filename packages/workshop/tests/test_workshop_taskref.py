"""The per-package task reference. Refusals and fallbacks first.

The section documents the package's whole offering: each advertised
provider renders in isolation, so workspace composition, shadowing,
and disabling can never change what a package's docs say.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from livery.workshop._packages import discover_packages
from livery.workshop._taskref import (
    advertised_providers,
    generate_task_reference,
    provider_tree,
)


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "workshop.toml").write_text("[workspace]\n")
    (root / "pyproject.toml").write_text('[project]\nname = "acme-home"\n')
    (root / "docs").mkdir()
    (root / "docs" / "index.md").write_text("# Home\n")
    for name, points in (("core", ["acme.core"]), ("bare", [])):
        member = root / "packages" / name
        (member / "src" / "acme" / name).mkdir(parents=True)
        (member / "src" / "acme" / name / "__init__.py").write_text("")
        (member / "workshop.toml").write_text(
            f'type = "python"\nname = "acme-{name}"\n'
        )
        table = "".join(
            f'[project.entry-points."footman.tasks"]\n"{point}" = "x"\n'
            for point in points
        )
        (member / "pyproject.toml").write_text(
            f'[project]\nname = "acme-{name}"\n{table}'
        )
        (member / "docs").mkdir()
        (member / "docs" / "index.md").write_text(f"# {name}\n")
        (member / "docs" / "nav.toml").write_text(
            'nav = [\n    { "Index" = "index.md" },\n'
            "    # nav:begin tasks\n    # nav:end tasks\n]\n"
        )
    return root


def _row() -> dict[str, object]:
    return {"help": "One line.", "params": []}


def test_a_package_without_the_table_provides_nothing(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    bare = next(p for p in discover_packages(root) if p.directory.name == "bare")
    assert advertised_providers(bare) == []
    core = next(p for p in discover_packages(root) if p.directory.name == "core")
    assert advertised_providers(core) == ["acme.core"]


def test_a_missing_runner_refuses_with_the_remedy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil as shutil_module

    monkeypatch.setattr(shutil_module, "which", lambda name: None)
    with pytest.raises(BaseException, match=r"setup\.sh"):
        provider_tree(tmp_path, "acme.core")


def test_a_failing_probe_names_the_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil as shutil_module

    import livery.footman as footman

    monkeypatch.setattr(shutil_module, "which", lambda name: "/stub/fm")
    monkeypatch.setattr(footman, "run", lambda *a, **k: 3)
    with pytest.raises(BaseException, match=r"acme\.core.*exited 3"):
        provider_tree(tmp_path, "acme.core")


def test_the_probe_mounts_only_the_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil as shutil_module

    import livery.footman as footman

    monkeypatch.setattr(shutil_module, "which", lambda name: "/stub/fm")
    probes: list[str] = []

    class _Result(int):
        stdout = json.dumps({"tree": {"groups": {}, "tasks": {}}})

    def _record(argv: list[str], **kwargs: object) -> int:
        # The probe file's content is the isolation: one mount, no
        # cascade, no base.
        probe = next(a for a in argv if a.startswith("--tasks-file="))
        probes.append(Path(probe.removeprefix("--tasks-file=")).read_text())
        assert "--json" in argv and "--list" in argv
        return _Result(0)

    monkeypatch.setattr(footman, "run", _record)
    provider_tree(tmp_path, "acme.core")
    assert probes == ['from livery.footman import plugin\n\nplugin("acme.core")\n']


def test_missing_markers_refuse_naming_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _taskref

    root = _workspace(tmp_path)
    (root / "packages/core/docs/nav.toml").write_text(
        'nav = [\n    { "Index" = "index.md" },\n]\n'
    )
    monkeypatch.setattr(
        _taskref,
        "provider_tree",
        lambda _root, _identity: {
            "groups": {"docs": {"help": "", "tasks": {"build": _row()}, "groups": {}}},
            "tasks": {},
        },
    )
    with pytest.raises(BaseException, match="tasks"):
        generate_task_reference(root)


def test_the_reference_renders_the_advertised_tree_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _taskref

    root = _workspace(tmp_path)
    tree: dict[str, object] = {
        "help": "",
        "groups": {
            "docs": {
                "help": "Docs.",
                "tasks": {"build": _row()},
                "groups": {},
            },
            "forge": {
                "help": "Forge.",
                "tasks": {},
                "groups": {
                    "dev": {"help": "Rigs.", "tasks": {"up": _row()}, "groups": {}}
                },
            },
        },
        "tasks": {"sync": _row()},
    }
    monkeypatch.setattr(_taskref, "provider_tree", lambda _root, _identity: tree)
    assert generate_task_reference(root) == ["core"]
    tasks = root / "packages/core/docs/_generated/tasks"
    assert (tasks / "docs" / "build.md").is_file()
    assert (tasks / "forge" / "dev" / "up.md").is_file()
    assert (tasks / "sync.md").is_file()
    assert (tasks / "index.md").is_file()
    nav = (root / "packages/core/docs/nav.toml").read_text()
    assert '"dev.up" = "_generated/tasks/forge/dev/up.md"' in nav
    assert '{ "sync" = "_generated/tasks/sync.md" },' in nav
    alias = (root / "docs/_generated/tasks/forge-dev-up.md").read_text()
    assert "../../packages/core/_generated/tasks/forge/dev/up/" in alias
    assert "window.location.replace" in alias
    # Idempotent: the same trees rewrite the same bytes.
    before = (root / "packages/core/docs/nav.toml").read_bytes()
    generate_task_reference(root)
    assert (root / "packages/core/docs/nav.toml").read_bytes() == before


def test_a_shared_verb_declared_twice_runs_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil as shutil_module

    import livery.footman as footman
    from livery.workshop._docs import run_generators

    root = _workspace(tmp_path)
    for name in ("core", "bare"):
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
