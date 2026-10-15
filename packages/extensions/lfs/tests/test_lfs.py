"""The lfs extension: unlisted it does nothing; listed, it locks, hooks and fetches."""

from __future__ import annotations

import contextlib
import functools
import importlib
import sys
from collections.abc import Iterator
from importlib.metadata import EntryPoint
from pathlib import Path

import pytest

from livery.footman import Failed, installed_entry_points
from livery.toolroom.tools import Result
from livery.toolroom.tools.testing import answers
from livery.workshop import PhaseContext
from livery.workshop import _extensions as extensions
from livery.workshop import _points as points
from livery.workshop import _steps as registry
from livery.workshop.testing import CLAUSES, Clause, Subject

#: The package the extension's declaration sits in.
PACKAGE = "livery.extensions.lfs"

#: A born workspace's identity, the start of every contract here.
CONTRACT = (
    "[workspace]\n"
    'name = "acme"\n'
    'namespace = "acme"\n'
    'authors = [{ name = "Acme", email = "dev@acme.test" }]\n'
    'copyright-year = "2026"\n'
)

#: The attribute line the test extension routes its PNG files through LFS with.
LINE = "*.png filter=lfs diff=lfs merge=lfs -text"


def _workspace(root: Path, extensions: str) -> Path:
    """The workspace at *root*, its contract listing *extensions*, a TOML list."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "workshop.toml").write_text(CONTRACT + f"extensions = {extensions}\n")
    return root


def _here(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    """Resolve every reader's workspace to *root*, wherever the test runs."""
    monkeypatch.setattr(
        "livery.workshop._extensions.workspace_root", lambda start=None: root
    )


@contextlib.contextmanager
def _mounted(root: Path) -> Iterator[tuple[str, ...]]:
    """Mount *root*'s extensions as a process started there does; the names.

    The registries are put back after the block.
    """
    from livery.footman import _registry as footman_registry

    held = (registry.snapshot(), points.snapshot(), extensions.snapshot())
    try:
        with footman_registry.capture():
            names = extensions.mount_extensions(root)
        yield names
    finally:
        registry.restore(held[0])
        points.restore(held[1])
        extensions.restore(held[2])


def _assets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Install the test extension ``acme.assets`` for the test alone.

    It ships ``*.svg text`` as its own attribute line, and under
    ``content/for/lfs/`` the line that routes its PNG files through
    LFS, with the ``[for.lfs]`` table that line needs.
    """
    from livery.footman import _entries  # pyright: ignore[reportPrivateUsage]
    from livery.workshop import _contract_keys, _phases, _queries

    package = tmp_path / "site" / "acme" / "assets"
    (package / "content" / "root").mkdir(parents=True)
    (package / "content" / "for" / "lfs" / "root").mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "extension.toml").write_text(
        "[extension]\n"
        'description = "Image files, stored in LFS while the workspace lists lfs."\n'
        "\n[for.lfs]\n"
    )
    (package / "content" / "root" / ".gitattributes").write_text("*.svg text\n")
    (package / "content" / "for" / "lfs" / "root" / ".gitattributes").write_text(
        f"{LINE}\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path / "site"))
    importlib.invalidate_caches()
    for loaded in [key for key in sys.modules if key.split(".")[0] == "acme"]:
        monkeypatch.delitem(sys.modules, loaded, raising=False)
    fake = EntryPoint("acme.assets", "acme.assets", extensions.GROUP)
    scanned = installed_entry_points()
    monkeypatch.setattr(_entries, "_scan", lambda: (*scanned, fake))
    monkeypatch.setattr(_entries, "_SCAN", None)
    # Each is read once per process from the scan; read again for this test.
    for module, name in (
        (_contract_keys, "declarations"),
        (_phases, "declared_phases"),
        (_queries, "declared_queries"),
    ):
        monkeypatch.setattr(
            module, name, functools.cache(getattr(module, name).__wrapped__)
        )


# The fallbacks first: installed and not listed, the extension does nothing,
# and the key the base read before is one the contract does not take.


def test_unlisted_it_installs_no_hooks_locks_no_tool_and_fetches_no_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._ci_generate import generate
    from livery.workshop._lifecycle import sync
    from livery.workshop._tools import requirements

    root = _workspace(tmp_path / "ws", "[]")
    (root / ".git").mkdir()
    _here(monkeypatch, root)
    with _mounted(root) as names:
        assert "lfs" not in names
        assert "sync.lfs" not in registry.steps_by_name()
        with answers() as calls:
            sync((), root)
    assert list(calls) == []
    assert "git_lfs" not in {found.name for found in requirements(root)}
    assert "lfs: true" not in generate(root)[".github/workflows/ci.yml"]


def test_a_contract_writing_lfs_in_its_workspace_table_meets_the_unknown_key_refusal(
    tmp_path: Path,
) -> None:
    from livery.workshop._contract import load_contract

    root = _workspace(tmp_path / "ws", "[]\nlfs = true")
    with pytest.raises(Failed, match=r"\[workspace\] has no key 'lfs': it takes "):
        load_contract(root / "workshop.toml")


def test_the_step_installs_nothing_outside_a_checkout_and_refuses_in_git_lfs_words(
    tmp_path: Path,
) -> None:
    from livery.extensions.lfs._hooks import install

    ctx = PhaseContext(root=tmp_path, packages=())
    with answers() as calls:
        install(ctx)
    assert list(calls) == []
    (tmp_path / ".git").mkdir()
    refused = Result(2, stderr="Not in a Git repository.\n")
    with (
        answers({("git-lfs", "install"): refused}),
        pytest.raises(Failed) as raised,
    ):
        install(ctx)
    assert (
        str(raised.value) == "git lfs install --local failed: Not in a Git repository."
    )


# Listed, it does all three.


def test_listed_it_locks_the_tool_installs_the_hooks_and_fetches_the_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._ci_generate import generate
    from livery.workshop._lifecycle import sync
    from livery.workshop._tools import requirements

    root = _workspace(tmp_path / "ws", '["lfs"]')
    (root / ".git").mkdir()
    _here(monkeypatch, root)
    with _mounted(root) as names:
        assert names == ("lfs",)
        assert registry.steps_by_name()["sync.lfs"].extension == "lfs"
        with answers() as calls:
            sync((), root)
    assert [call.argv for call in calls] == [["git-lfs", "install", "--local"]]
    assert calls[0].opts["cwd"] == root
    (tool,) = [found for found in requirements(root) if found.name == "git_lfs"]
    assert tool.site == "extension lfs"
    assert (
        "        with:\n          lfs: true\n"
        in generate(root)[".github/workflows/ci.yml"]
    )


def test_a_for_lfs_attribute_line_applies_only_while_lfs_is_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._shipped_files import deliver, shipped_drift

    _assets(tmp_path, monkeypatch)
    root = _workspace(tmp_path / "ws", '["acme.assets"]')
    _here(monkeypatch, root)
    # lfs unlisted: the extension's own line, and not the LFS one.
    deliver(root)
    attributes = (root / ".gitattributes").read_text()
    assert "*.svg text\n" in attributes
    assert "filter=lfs" not in attributes
    assert shipped_drift(root) == []
    # lfs listed: the LFS line joins the file.
    _workspace(root, '["acme.assets", "lfs"]')
    deliver(root)
    assert f"{LINE}\n" in (root / ".gitattributes").read_text()
    assert shipped_drift(root) == []
    # The extension's root entry leaves lfs out: the line goes again.
    _workspace(root, '[{ name = "acme.assets", without = ["lfs"] }, "lfs"]')
    deliver(root)
    attributes = (root / ".gitattributes").read_text()
    assert "*.svg text\n" in attributes
    assert "filter=lfs" not in attributes
    assert shipped_drift(root) == []


@pytest.mark.parametrize("clause", CLAUSES, ids=lambda clause: clause.name)
def test_lfs_conforms(
    clause: Clause, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path / "ws", '["lfs"]')
    _here(monkeypatch, root)
    with _mounted(root):
        subject = Subject(PACKAGE, steps=(registry.step_for("sync.lfs"),))
        assert clause.judge(subject) == []
