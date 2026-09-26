"""The stubs under typings/: refusals first, then the writing, the hooks, the entry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from livery.strongroom import Entry, Store, Tree, Value, canonical
from livery.toolroom.store import Lock, Locked, Record, RecordDelta, Surface
from livery.workshop import _env_tasks, _sync, _tool_tasks, _tools
from workshop_hosts import HOSTS

THREE = HOSTS


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
    root: Path, tools: dict[str, tuple[str, ...]], read: dict[str, tuple[str, ...]]
) -> Path:
    """An index directory listing *tools* with their versions.

    The versions *read* names carry an observation and a surface with one
    root verb, which the store renders; the others were never read.
    """
    index = root / "index"
    store = Store.create(index)
    pointer: dict[str, object] = {}

    def blob(name: str, value: Value) -> Entry:
        data = canonical(value)
        return Entry(name, "blob", store.put(data), len(data))

    for name, versions in tools.items():
        record = Record(name, kind="uv-tool", deltas=_read(*versions))
        entries = [blob("tool", record.to_json()), blob("versions", list(versions))]
        for version in versions:
            parts: list[Entry] = []
            if version in read.get(name, ()):
                parts.append(
                    blob(
                        "observation",
                        {
                            "date": "",
                            "help": "A tool.",
                            "platforms": ["Linux"],
                            "extractor": 1,
                            "absent": {},
                        },
                    )
                )
                parts.append(
                    blob(
                        "surface",
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
                )
            data = Tree.of(parts).encode()
            entries.append(Entry(version, "tree", store.put(data), len(data)))
        data = Tree.of(entries).encode()
        pointer[name] = {"tree": str(store.put(data)), "record": "x"}
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


def test_a_records_source_renders_and_a_version_never_read_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The authoring site's own records render like an index.

    A lock naming a version nobody read is named tool by tool, and
    nothing is written for it.
    """
    root = _workspace(
        tmp_path,
        monkeypatch,
        '[workspace]\n\n[tools]\nindex = "records"\nrequires = ["ruff"]\n',
    )
    Record("ruff", kind="uv-tool", deltas=_read("1.0.0")).save(root / "records")
    _tools.write_lock(root)
    made = _tools.write_stubs(root)
    assert made.written == ("ruff",) and made.skipped == {}
    text = (_tools.stubs_dir(root) / "ruff.pyi").read_text()
    assert "# Read from ruff 1.0.0 on Linux." in text
    assert "class Ruff(ToolBase[_R]):" in text
    Lock(THREE, {"ruff": Locked("9.9.9", {})}).save(_tools.lock_path(root))
    made = _tools.write_stubs(root)
    assert made.written == () and made.removed == ("ruff",)
    assert made.skipped == {
        "ruff": "ruff 9.9.9: never read; the versions read are 1.0.0"
    }


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
    assert made.skipped["bare"].endswith("bare 2.0.0: the version was never read")
    text = (stubs / "ruff.pyi").read_text()
    assert "# Read from ruff 1.0.0 on Linux." in text  # the lock's version
    assert "class Ruff(ToolBase[_R]):" in text
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
        "  stubs: bare: " + made.skipped["bare"],
    ]

    # A locked tool with no stub is named on every write: the receipt never
    # answers for this lock, so the catalogue is read again.
    assert (root / ".workshop" / "stubs.json").is_file()
    reads: list[str] = []
    real = _tools._read_catalogue

    def counted(source: str, *, offline: bool) -> object:
        reads.append(source)
        return real(source, offline=offline)

    monkeypatch.setattr(_tools, "_read_catalogue", counted)
    assert _tools.write_stubs(root).kept == ("ruff",) and reads
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
    # Before a lock nothing expects a stub: the check names the lock
    # verb and no stubs line.
    assert _env_tasks.env_check() == 0
    out = capsys.readouterr().out
    assert "ruff: on PATH; not locked; run `fm tools.lock`" in out
    assert "stubs:" not in out
    _tool_tasks.tools_lock()
    assert "  stubs: 1 in typings/, wrote 1" in capsys.readouterr().out
    _tool_tasks.tools_restub()
    assert capsys.readouterr().out.strip() == "stubs: 1 in typings/"
    # The receipt gate: with the lock, the index's stubs and the files
    # standing, the next write reads no catalogue at all.
    real = _tools._read_catalogue

    def unread(*args: object, **kwargs: object) -> object:
        raise AssertionError("the catalogue was read while nothing had moved")

    monkeypatch.setattr(_tools, "_read_catalogue", unread)
    again = _tools.write_stubs(root)
    assert again.written == () and again.kept == ("ruff",)
    # A stub gone from disk is written again, through the catalogue.
    monkeypatch.setattr(_tools, "_read_catalogue", real)
    (_tools.stubs_dir(root) / "ruff.pyi").unlink()
    assert _tools.write_stubs(root).written == ("ruff",)
    _tool_tasks.tools_upgrade(["ruff"])
    assert "  stubs: 1 in typings/" in capsys.readouterr().out
    assert _sync.materialise_tools(root)[-1] == "  stubs: 1 in typings/"
    assert _env_tasks.env_check() == 0
    assert "  stubs: 1 in typings/" in capsys.readouterr().out


def test_a_workspace_that_names_no_index_is_not_asked_for_stubs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _workspace(tmp_path, monkeypatch, "[workspace]\n")
    monkeypatch.setattr(
        "livery.workshop._env_tasks.shutil.which", lambda tool: "/x/" + tool
    )
    monkeypatch.setattr("livery.workshop._env_tasks._uv_drift", lambda root: "")
    assert _env_tasks.env_check() == 0  # no index named at all
    assert "stubs" not in capsys.readouterr().out


def test_the_entry_script_materialises_the_tools_before_it_emits(
    tmp_path: Path,
) -> None:
    """The receipts must exist before the emission puts their paths on PATH."""
    from livery.workshop._entry import entry_script

    root = tmp_path / "ws"
    root.mkdir()
    (root / "uv.lock").write_text('[[package]]\nname = "uv"\nversion = "0.11.0"\n')
    script = entry_script(root)
    assert "_run tools.materialise >&2" in script
    assert '|| echo "setup: the tools were not materialised' in script
    assert (
        script.index("uv sync")
        < script.index("tools.materialise")
        < script.index("env.emit")
    )
