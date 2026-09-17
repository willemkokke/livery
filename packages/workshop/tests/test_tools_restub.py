"""The stubs under typings/: refusals first, then the writing, the hooks, the entry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from livery.footman.context import Failed
from livery.strongroom import Entry, Store, Tree, canonical
from livery.toolroom.store import Lock, Locked, Record, RecordDelta, Surface
from livery.workshop import _env_tasks, _sync, _tool_tasks, _tools

THREE = ("linux-x64", "macos-arm", "windows-x64")


def _read(*versions: str) -> tuple[RecordDelta, ...]:
    first = Surface(
        ("Linux",),
        1,
        "A tool.",
        {
            "": {
                "help": "",
                "wraps": False,
                "positional": "any",
                "lead": "",
                "options": {},
            }
        },
    )
    return tuple(
        RecordDelta(n, v, "", surface=first if n == 1 else Surface(("Linux",), 1))
        for n, v in enumerate(versions, start=1)
    )


def _index(
    root: Path, tools: dict[str, tuple[str, ...]], stubs: dict[str, tuple[str, ...]]
) -> Path:
    """An index directory listing *tools* with their versions.

    A stub per version *stubs* names, its text `stub <tool> <version>`.
    """
    index = root / "index"
    store = Store.create(index)
    pointer: dict[str, object] = {}
    for name, versions in tools.items():
        record = Record(name, kind="uv-tool", deltas=_read(*versions))
        entries = []
        for key, value in (("tool", record.to_json()), ("versions", list(versions))):
            data = canonical(value)
            entries.append(Entry(key, "blob", store.put(data), len(data)))
        for version in versions:
            data = Tree.of([]).encode()
            entries.append(Entry(version, "tree", store.put(data), len(data)))
        data = Tree.of(entries).encode()
        entry: dict[str, str] = {"tree": str(store.put(data)), "record": "x"}
        if name in stubs:
            blobs = []
            for version in stubs[name]:
                text = f"stub {name} {version}\n".encode()
                blobs.append(Entry(version, "blob", store.put(text), len(text)))
            data = Tree.of(blobs).encode()
            entry["stubs"] = str(store.put(data))
        pointer[name] = entry
    (index / "pointer.json").write_text(json.dumps({"schema": 1, "tools": pointer}))
    return index


def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, contract: str) -> Path:
    from livery.workshop._kinds import KindRecord, kind_for, register_kind

    root = tmp_path / "ws"
    (root / "packages" / "member").mkdir(parents=True)
    # One package of a kind that requires nothing, so the sites'
    # requirements are the contract's alone.
    (root / "packages" / "member" / "workshop.toml").write_text(
        'type = "bare"\nname = "acme-member"\n'
    )
    (root / "packages" / "member" / "pyproject.toml").write_text(
        '[project]\nname = "acme-member"\n'
    )
    (root / "workshop.toml").write_text(contract)
    python = kind_for("python")
    register_kind(
        KindRecord(name="bare", backend=python.backend, template=python.template)
    )
    monkeypatch.setattr(
        "livery.workshop._layers.workspace_root", lambda start=None: root
    )
    monkeypatch.setattr("livery.footman.context.data_dir", lambda: tmp_path / "data")
    return root


# --- the refusals ---------------------------------------------------------------


def test_a_records_source_without_a_build_verb_refuses_naming_the_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(
        tmp_path, monkeypatch, '[workspace]\n\n[tools]\nindex = "records"\n'
    )
    Record("ruff", kind="uv-tool", deltas=_read("1.0.0")).save(
        root / "records" / "ruff"
    )
    with pytest.raises(Failed, match=r"records .*hold no stubs; name the index"):
        _tools.write_stubs(root)
    # sync and the lock verbs carry the refusal as a line instead.
    (line,) = _tools.stub_lines(root, strict=False)
    assert line.startswith("  stubs: not written: [tools] index names records")
    with pytest.raises(Failed, match=r"index-build is not a verb name"):
        (root / "workshop.toml").write_text(
            '[workspace]\n\n[tools]\nindex = "records"\nindex-build = 3\n'
        )
        _tools.write_stubs(root)


def test_a_build_verb_that_fails_or_cannot_run_refuses_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(
        tmp_path,
        monkeypatch,
        '[workspace]\n\n[tools]\nindex = "index"\nindex-build = "tools.index.build"\n',
    )
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(Failed, match=r"fm is not on PATH, so `\[tools\] index-build`"):
        _tools.catalogue(root)
    monkeypatch.setattr("shutil.which", lambda name: "/x/fm")
    monkeypatch.setattr("livery.footman.run", lambda argv, **kw: 3)
    with pytest.raises(
        Failed, match=r"`fm tools.index.build` \(\[tools\] index-build\) exited 3"
    ):
        _tools.catalogue(root)


# --- the writing ------------------------------------------------------------------


def test_the_stubs_are_written_for_the_locked_tools_at_their_locked_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(
        tmp_path,
        monkeypatch,
        '[workspace]\n\n[tools]\nindex = "index"\nrequires = ["ruff", "bare"]\n',
    )
    _index(
        root,
        {"ruff": ("1.0.0", "1.1.0"), "ty": ("0.1.0",), "bare": ("2.0.0",)},
        {"ruff": ("1.0.0", "1.1.0"), "ty": ("0.1.0",)},
    )
    stubs = _tools.stubs_dir(root)
    stubs.mkdir(parents=True)
    (stubs / "gone.pyi").write_text("class Gone: ...\n")
    # A tree inside the tools package's directory shadows the package for
    # a checker run on explicit paths; the writer removes one it finds.
    inside = _tools.typings_dir(root) / "livery" / "toolroom" / "tools"
    inside.mkdir(parents=True)
    (inside / "stale.pyi").write_text("")
    # A lock entry below the newest listed: the lock verbs keep an entry
    # that still satisfies its floor, so ruff stays at 1.0.0.
    Lock(THREE, {"ruff": Locked("1.0.0", {})}).save(_tools.lock_path(root))
    assert _tools.write_lock(root).tools["ruff"].version == "1.0.0"
    made = _tools.write_stubs(root)
    assert made.written == ("ruff",) and made.kept == ()
    assert made.removed == ("gone",)
    assert made.skipped == {"bare": "bare 2.0.0: no stub; the index has none"}
    assert (stubs / "ruff.pyi").read_text() == "stub ruff 1.0.0\n"  # the lock's
    assert not (stubs / "ty.pyi").exists()  # listed, not locked: no stub
    assert (stubs / "__init__.pyi").read_text() == ""
    assert _tools.handles_path(root).read_text() == (
        "# Rendered by `fm tools.restub`: the handles this workspace\n"
        "# locks. Do not edit by hand.\n"
        "from livery.toolroom.tools import Result\n"
        "from livery.toolroom.stubs.ruff import Ruff as Ruff\n"
        "\n"
        "ruff: Ruff[Result]\n"
    )
    # The package's own index is never shadowed: nothing else is written.
    assert sorted(p.name for p in _tools.typings_dir(root).rglob("*.pyi")) == [
        "__init__.pyi",
        "handles.pyi",
        "ruff.pyi",
    ]
    assert not _tools.typings_dir(root).joinpath("livery", "toolroom", "tools").exists()
    assert _tools.stubs_present(root) == 1
    # A second write changes nothing on disk.
    again = _tools.write_stubs(root)
    assert again.written == () and again.kept == ("ruff",)
    assert _tools.stub_lines(root) == [
        "  stubs: 1 in typings/",
        "  stubs: bare: bare 2.0.0: no stub; the index has none",
    ]
    # Without a lock nothing is written, and the stale stub goes.
    _tools.lock_path(root).unlink()
    none = _tools.write_stubs(root)
    assert none.written == () and none.removed == ("ruff",)
    assert _tools.handles_path(root).read_text().endswith("import Result\n\n")


def test_the_lock_verbs_and_sync_write_the_stubs_and_env_check_counts_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _workspace(
        tmp_path,
        monkeypatch,
        '[workspace]\n\n[tools]\nindex = "index"\nrequires = ["ruff"]\n',
    )
    _index(root, {"ruff": ("1.0.0",)}, {"ruff": ("1.0.0",)})
    monkeypatch.setattr(
        "livery.workshop._env_tasks.shutil.which", lambda tool: "/x/" + tool
    )
    monkeypatch.setattr("livery.workshop._env_tasks._uv_drift", lambda root: "")
    monkeypatch.setattr(_tools, "materialise", lambda root, names=(), **kw: ())
    # Before any stub: a problem, naming the verb.
    assert _env_tasks.env_check() == 1
    assert "stubs: MISSING; run `fm tools.restub` to write them into typings/" in (
        capsys.readouterr().out
    )
    _tool_tasks.tools_lock()
    assert "  stubs: 1 in typings/, wrote 1" in capsys.readouterr().out
    _tool_tasks.tools_restub()
    assert capsys.readouterr().out.strip() == "stubs: 1 in typings/"
    _tool_tasks.tools_upgrade(["ruff"])
    assert "  stubs: 1 in typings/" in capsys.readouterr().out
    assert _sync.materialise_tools(root)[-1] == "  stubs: 1 in typings/"
    assert _env_tasks.env_check() == 0
    assert "  stubs: 1 in typings/" in capsys.readouterr().out


def test_a_workspace_whose_source_cannot_render_is_not_asked_for_stubs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _workspace(tmp_path, monkeypatch, "[workspace]\n")
    monkeypatch.setattr(
        "livery.workshop._env_tasks.shutil.which", lambda tool: "/x/" + tool
    )
    monkeypatch.setattr("livery.workshop._env_tasks._uv_drift", lambda root: "")
    assert _env_tasks.env_check() == 0  # no index named at all
    assert "stubs" not in capsys.readouterr().out
    (root / "workshop.toml").write_text('[workspace]\n\n[tools]\nindex = "records"\n')
    Record("ruff", kind="uv-tool", deltas=_read("1.0.0")).save(
        root / "records" / "ruff"
    )
    assert _env_tasks.env_check() == 0  # records, and no verb to build them
    assert "stubs" not in capsys.readouterr().out


def test_the_entry_script_writes_the_stubs_and_survives_their_absence(
    tmp_path: Path,
) -> None:
    from livery.workshop._entry import entry_script

    root = tmp_path / "ws"
    root.mkdir()
    (root / "uv.lock").write_text('[[package]]\nname = "uv"\nversion = "0.11.0"\n')
    script = entry_script(root)
    assert "_run tools.restub >&2" in script
    assert '|| echo "setup: the tool stubs were not written' in script
    assert (
        script.index("uv sync")
        < script.index("tools.restub")
        < script.index("env.emit")
    )
