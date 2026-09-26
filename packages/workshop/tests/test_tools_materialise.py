"""Receipts and materialisation: refusals, then the modes, the emission, the drift."""

from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path

import pytest

from livery.footman.context import Failed
from livery.strongroom import Store as ObjectStore
from livery.strongroom import digest_of
from livery.toolroom.store import (
    Artifact,
    Layout,
    Record,
    RecordDelta,
    Surface,
    _engine,
)
from livery.workshop import _env_tasks, _sync, _tool_tasks, _tools

THREE = ("linux-x64", "macos-arm", "windows-x64")


def _zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


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


def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A python workspace whose kind requires nothing, with one archive and one uv tool.

    The archive `tea` is served from a folder source beside the records,
    never from the network; the uv tool `ruff` through the installer seam.
    """
    from livery.workshop._kinds import KindRecord, kind_for, register_kind

    root = tmp_path / "ws"
    (root / "packages" / "member").mkdir(parents=True)
    (root / "workshop.toml").write_text(
        '[workspace]\n\n[tools]\nindex = "records"\nsources = ["mirror"]\n'
        'requires = ["tea", "ruff"]\n'
    )
    (root / "packages" / "member" / "workshop.toml").write_text(
        'type = "bare"\nname = "acme-member"\n'
    )
    (root / "packages" / "member" / "pyproject.toml").write_text(
        '[project]\nname = "acme-member"\n'
    )
    python = kind_for("python")
    register_kind(
        KindRecord(name="bare", backend=python.backend, template=python.template)
    )
    payload = _zip({"tea": b"#!/bin/sh\necho tea\n", "docs/readme": b"r"})
    digest = digest_of(payload)
    Record(
        "tea",
        kind="download",
        hosts=THREE,
        layout=Layout(entry_points=("tea",), paths=(".",)),
        deltas=(
            RecordDelta(
                1,
                "1.0.0",
                "",
                {
                    host: Artifact(
                        f"https://origin.test/tea/{host}.zip", digest.encoded
                    )
                    for host in THREE
                },
            ),
        ),
    ).save(root / "records")
    Record("ruff", kind="uv-tool", deltas=_read("0.16.0")).save(root / "records")
    mirror = ObjectStore.create(root / "mirror")
    mirror.put(payload)
    monkeypatch.setattr(
        "livery.workshop._layers.workspace_root", lambda start=None: root
    )
    monkeypatch.setattr("livery.footman.context.data_dir", lambda: tmp_path / "data")

    def installing(argv: list[str], env: dict[str, str]) -> int:
        bin_dir = Path(env["UV_TOOL_BIN_DIR"])
        bin_dir.mkdir(parents=True, exist_ok=True)
        (bin_dir / "ruff").write_text("launcher")
        return 0

    monkeypatch.setattr(_engine, "run_installer", installing)

    def no_network(url: str) -> bytes:
        raise AssertionError(f"the network was reached for {url}")

    monkeypatch.setattr(_engine, "download", no_network)
    return root


# --- the refusals ---------------------------------------------------------------


def test_materialising_without_a_lock_or_an_unlocked_tool_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    with pytest.raises(Failed, match=r"no tools\.lock: lock the tools first"):
        _tools.materialise(root)
    assert _sync.materialise_tools(root) == [
        "  tools: no tools.lock; `fm tools.lock` writes one"
    ]
    _tools.write_lock(root)
    with pytest.raises(
        Failed, match=r"black is not in tools\.lock; the lock holds ruff, tea"
    ):
        _tools.materialise(root, ("black",))


def test_a_mode_outside_the_three_and_a_receipt_off_its_shape_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    (root / "workshop.toml").write_text(
        '[workspace]\n\n[tools]\nindex = "records"\nrequires = ["tea"]\n'
        'modes = { tea = "float" }\n'
    )
    with pytest.raises(
        Failed, match=r"modes names 'float' for tea; the modes are link, path, none"
    ):
        _tools.mode_of(root, "tea", "download", paths=("bin",))
    (root / "workshop.toml").write_text(
        '[workspace]\n\n[tools]\nindex = "records"\nmodes = 1\n'
    )
    with pytest.raises(Failed, match=r"\[tools\] modes is not a table"):
        _tools.mode_of(root, "tea", "download", paths=("bin",))
    (root / "workshop.toml").write_text(
        '[workspace]\n\n[tools]\nindex = "records"\nsources = "x"\n'
    )
    with pytest.raises(Failed, match=r"\[tools\] sources is not a list"):
        _tools.sources(root)
    receipts = _tools.receipts_dir(root)
    receipts.mkdir(parents=True)
    (receipts / "tea.json").write_text("{not json")
    with pytest.raises(ValueError, match=r"tea\.json: not a receipt \("):
        _tools.receipts(root)
    (receipts / "tea.json").write_text('{"schema": 9}')
    with pytest.raises(ValueError, match=r"not a receipt of schema 1"):
        _tools.receipts(root)
    (receipts / "tea.json").write_text('{"schema": 1, "tool": "tea"}')
    with pytest.raises(ValueError, match=r"tea\.json: not a receipt \("):
        _tools.receipts(root)


def test_a_store_refusal_and_a_host_the_lock_lacks_refuse_naming_the_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    _tools.write_lock(root)
    monkeypatch.setattr(_engine, "run_installer", lambda argv, env: 7)
    with pytest.raises(
        Failed, match=r"ruff 0\.16\.0: `uv tool install ruff==0\.16\.0` exited 7"
    ):
        _tools.materialise(root, ("ruff",))
    # Sync is not strict: the others are supplied and the failure is named.
    lines = _sync.materialise_tools(root)
    assert lines[0] == "  tools: 1 receipt(s), installed tea"
    assert lines[1].startswith("  tools: could not materialise: ruff 0.16.0: `uv tool")
    assert list(_tools.receipts(root)) == ["tea"]
    monkeypatch.setattr(_engine, "_default_host", lambda: "linux-arm")
    with pytest.raises(Failed, match=r"tea 1\.0\.0: not locked for linux-arm"):
        _tools.materialise(root, ("tea",))


def test_a_system_tool_is_held_to_the_highest_floor_and_the_site_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The highest floor holds, the record's or a site's, and the site is named."""
    root = _workspace(tmp_path, monkeypatch)
    Record(
        "git", kind="system-check", min_version="2.40", deltas=_read("2.40.0", "2.55.0")
    ).save(root / "records")
    contract = root / "workshop.toml"
    contract.write_text(contract.read_text().replace('"ruff"]', '"ruff", "git>=2.50"]'))
    _tools.write_lock(root)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/git")
    monkeypatch.setattr(_engine, "read_version", lambda argv: "git version 2.45.0")
    with pytest.raises(
        Failed,
        match=r"git: /usr/bin/git reports 2\.45\.0, below the floor 2\.50;"
        r" workshop\.toml requires git>=2\.50$",
    ):
        _tools.materialise(root, ("git",))
    lines = _sync.materialise_tools(root)
    assert any("workshop.toml requires git>=2.50" in line for line in lines)
    monkeypatch.setattr(_engine, "read_version", lambda argv: "git version 2.55.0")
    (done,) = _tools.materialise(root, ("git",))
    assert done.receipt is not None and done.receipt.tool == "git"
    # A site floor below the record's: the record's floor holds, unnamed.
    contract.write_text(contract.read_text().replace("git>=2.50", "git>=2.30"))
    monkeypatch.setattr(_engine, "read_version", lambda argv: "git version 2.35.0")
    with pytest.raises(Failed, match=r"below the floor 2\.40$"):
        _tools.materialise(root, ("git",))
    assert _tools.site_floors(root) == {"git": ("2.30", "workshop.toml")}


# --- materialisation, the modes and the receipts ------------------------------------


def test_sync_materialises_the_bundle_from_the_folder_source_with_no_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    _tools.write_lock(root)
    lines = _sync.materialise_tools(root)
    assert lines[0] == "  tools: 2 receipt(s), installed ruff, tea"
    # The stubs follow the bundle: ruff was read, tea only ever downloaded.
    assert lines[1] == "  stubs: 1 in typings/, wrote 1"
    assert lines[2].startswith("  stubs: tea: tea 1.0.0: never read")
    held = _tools.receipts(root)
    assert set(held) == {"ruff", "tea"}
    tea = held["tea"]
    assert (tea.version, tea.kind, tea.mode) == ("1.0.0", "download", "path")
    assert tea.host == _engine._default_host()
    lock = _tools.current_lock(root)
    assert lock is not None and tea.deployment == str(lock.tools["tea"].hosts[tea.host])
    assert tea.paths == (tea.tool_dir,) and Path(tea.tool_dir, "tea").is_file()
    ruff = held["ruff"]
    assert (ruff.kind, ruff.mode, ruff.deployment) == ("uv-tool", "path", "")
    assert ruff.paths == (str(Path(ruff.tool_dir) / "bin"),)
    # A second sync finds everything present.
    assert _sync.materialise_tools(root)[0] == "  tools: 2 receipt(s), all present"
    written = json.loads((_tools.receipts_dir(root) / "tea.json").read_text())
    # The receipt names what reached PATH, in path mode too: a check
    # resolves the tool by these names, never by its lock name.
    assert written["schema"] == 1 and written["entry_points"] == ["tea"]


def test_a_tool_that_left_the_lock_takes_its_receipt_with_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A receipt kept past its tool keeps the tool's paths on PATH.

    A narrowed materialise touches no other tool's receipt.
    """
    root = _workspace(tmp_path, monkeypatch)
    _tools.write_lock(root)
    _tools.materialise(root)
    assert set(_tools.receipts(root)) == {"ruff", "tea"}
    (root / "workshop.toml").write_text(
        '[workspace]\n\n[tools]\nindex = "records"\nrequires = ["tea"]\n'
    )
    _tools.write_lock(root)
    _tools.materialise(root, ("tea",))
    assert set(_tools.receipts(root)) == {"ruff", "tea"}  # narrowed: untouched
    _tools.materialise(root)
    assert set(_tools.receipts(root)) == {"tea"}


def test_the_materialise_verb_supplies_the_bundle_and_writes_the_stubs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """What the entry script runs on a runner: receipts and stubs, nothing else."""
    root = _workspace(tmp_path, monkeypatch)
    _tool_tasks.tools_materialise()
    assert "tools: no tools.lock; `fm tools.lock` writes one" in capsys.readouterr().out
    _tools.write_lock(root)
    _tool_tasks.tools_materialise()
    out = capsys.readouterr().out
    assert "tools: 2 receipt(s), installed ruff, tea" in out
    assert "stubs: 1 in typings/" in out
    assert set(_tools.receipts(root)) == {"ruff", "tea"}


def test_an_npm_install_is_supplied_after_node_through_its_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Node is the tool's locked dependency, materialised first, its path handed on."""
    root = _workspace(tmp_path, monkeypatch)
    (root / "workshop.toml").write_text(
        '[workspace]\n\n[tools]\nindex = "records"\nsources = ["mirror", "mirror2"]\n'
        'requires = ["tea", "ruff", "basedpyright"]\n'
    )
    # One archive serves the three hosts: node under bin with npm's
    # script beside it, the way node publishes for POSIX.
    payload = _zip(
        {
            "bin/node": b"#!/bin/sh\necho node\n",
            "bin/node.exe": b"MZ",
            "lib/node_modules/npm/bin/npm-cli.js": b"// npm\n",
        }
    )
    digest = digest_of(payload)
    Record(
        "node",
        kind="download",
        hosts=THREE,
        layout=Layout(entry_points=("bin/node",), paths=("bin",)),
        deltas=(
            RecordDelta(
                1,
                "24.0.0",
                "",
                {
                    host: Artifact(
                        f"https://origin.test/node/{host}.zip", digest.encoded
                    )
                    for host in THREE
                },
            ),
        ),
    ).save(root / "records")
    Record("basedpyright", kind="npm", deltas=_read("1.39.0")).save(root / "records")
    ObjectStore.create(root / "mirror2").put(payload)
    lock = _tools.write_lock(root)
    # node joins the lock as basedpyright's dependency, though no site names it.
    assert set(lock.tools) == {"tea", "ruff", "basedpyright", "node"}
    calls: list[tuple[list[str], dict[str, str]]] = []

    def installing(argv: list[str], env: dict[str, str]) -> int:
        calls.append((argv, env))
        if "--global" in argv:
            prefix = Path(
                next(a for a in argv if a.startswith("--prefix=")).split("=", 1)[1]
            )
            bin_dir = prefix if prefix.name == "bin" else prefix / "bin"
            bin_dir.mkdir(parents=True, exist_ok=True)
            (bin_dir / "basedpyright").write_text("#!/usr/bin/env node\n")
        else:
            bin_dir = Path(env["UV_TOOL_BIN_DIR"])
            bin_dir.mkdir(parents=True, exist_ok=True)
            (bin_dir / "ruff").write_text("launcher")
        return 0

    monkeypatch.setattr(_engine, "run_installer", installing)
    _tools.write_stubs(root)
    done = _tools.materialise(root)
    names = [m.receipt.tool for m in done if m.receipt is not None]
    assert names[0] == "node" and "basedpyright" in names
    held = _tools.receipts(root)
    node = held["node"]
    assert Path(node.tool_dir, "bin", "node").is_file()
    argv, env = next(c for c in calls if "--global" in c[0])
    assert argv[:2] == [
        str(Path(node.tool_dir, "bin", "node")),
        str(Path(node.tool_dir, "lib", "node_modules", "npm", "bin", "npm-cli.js")),
    ]
    assert argv[2:4] == ["install", "--global"] and argv[-1] == "basedpyright@1.39.0"
    assert env["PATH"].split(os.pathsep)[0] == str(Path(node.tool_dir, "bin"))
    assert held["basedpyright"].kind == "npm"
    assert held["basedpyright"].entry_points == ("basedpyright",)
    # A basedpyright asked for alone still brings node, which it runs on.
    calls.clear()
    (Path(held["basedpyright"].tool_dir) / "bin" / "basedpyright").unlink()
    made = [
        m.receipt.tool for m in _tools.materialise(root, ("basedpyright",)) if m.receipt
    ]
    assert made == ["node", "basedpyright"]
    assert calls and calls[0][0][2:4] == ["install", "--global"]


def test_a_bun_install_is_supplied_after_bun_through_its_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bun is the tool's locked dependency, materialised first, its path handed on."""
    root = _workspace(tmp_path, monkeypatch)
    (root / "workshop.toml").write_text(
        '[workspace]\n\n[tools]\nindex = "records"\nsources = ["mirror", "mirror2"]\n'
        'requires = ["tea", "ruff", "cspell"]\n'
    )
    # Both spellings, since one archive serves the three hosts and the
    # Windows shim is a copy of bun.exe.
    payload = _zip({"bun": b"#!/bin/sh\necho bun\n", "bun.exe": b"MZ"})
    digest = digest_of(payload)
    Record(
        "bun",
        kind="download",
        hosts=THREE,
        layout=Layout(entry_points=("bun",), paths=(".",), shims={"node": "bun"}),
        deltas=(
            RecordDelta(
                1,
                "1.3.0",
                "",
                {
                    host: Artifact(
                        f"https://origin.test/bun/{host}.zip", digest.encoded
                    )
                    for host in THREE
                },
            ),
        ),
    ).save(root / "records")
    Record("cspell", kind="npm", runtime="bun", deltas=_read("9.0.0")).save(
        root / "records"
    )
    ObjectStore.create(root / "mirror2").put(payload)
    lock = _tools.write_lock(root)
    # bun joins the lock as cspell's dependency, though no site names it.
    assert set(lock.tools) == {"tea", "ruff", "cspell", "bun"}
    calls: list[tuple[list[str], dict[str, str]]] = []

    def installing(argv: list[str], env: dict[str, str]) -> int:
        calls.append((argv, env))
        if "BUN_INSTALL" in env:
            bin_dir = Path(env["BUN_INSTALL"]) / "bin"
            bin_dir.mkdir(parents=True, exist_ok=True)
            (bin_dir / "cspell").write_text("#!/usr/bin/env node\n")
        else:
            bin_dir = Path(env["UV_TOOL_BIN_DIR"])
            bin_dir.mkdir(parents=True, exist_ok=True)
            (bin_dir / "ruff").write_text("launcher")
        return 0

    monkeypatch.setattr(_engine, "run_installer", installing)
    _tools.write_stubs(root)
    done = _tools.materialise(root)
    names = [m.receipt.tool for m in done if m.receipt is not None]
    assert names[0] == "bun" and "cspell" in names
    held = _tools.receipts(root)
    bun = held["bun"]
    assert Path(bun.tool_dir, "bun").is_file()
    assert any(Path(bun.tool_dir, n).exists() for n in ("node", "node.exe"))  # the shim
    argv, env = next(c for c in calls if "BUN_INSTALL" in c[1])
    assert argv == [str(Path(bun.tool_dir, "bun")), "add", "--global", "cspell@9.0.0"]
    assert env["PATH"].split(os.pathsep)[0] == bun.tool_dir
    assert held["cspell"].kind == "npm"
    assert held["cspell"].entry_points == ("cspell",)
    # A cspell asked for alone still brings bun, which it is installed through.
    calls.clear()
    (Path(held["cspell"].tool_dir) / "bin" / "cspell").unlink()
    made = [m.receipt.tool for m in _tools.materialise(root, ("cspell",)) if m.receipt]
    assert made == ["bun", "cspell"]
    assert calls and calls[0][0][1:] == ["add", "--global", "cspell@9.0.0"]


def test_link_mode_fills_the_checkouts_bin_directory_and_the_emission_leads_with_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    (root / "workshop.toml").write_text(
        '[workspace]\n\n[tools]\nindex = "records"\nsources = ["mirror"]\n'
        'requires = ["tea", "ruff"]\nmodes = { tea = "link", ruff = "none" }\n'
    )
    _tools.write_lock(root)
    _tools.materialise(root)
    held = _tools.receipts(root)
    assert held["tea"].mode == "link" and held["tea"].entry_points == ("tea",)
    assert held["tea"].paths == ()
    assert (_tools.bin_dir(root) / "tea").exists()
    assert held["ruff"].mode == "none" and held["ruff"].paths == ()
    paths, env = _tools.emission(root)
    assert paths == (str(_tools.bin_dir(root)),) and env == {}
    delta = _env_tasks.workspace_delta(root, root)
    assert delta.paths == (str(_env_tasks.venv_bin(root)), str(_tools.bin_dir(root)))
    agent = _env_tasks.agent_delta(root, root, {})
    assert agent.paths == delta.paths


def test_path_mode_puts_the_tools_directories_on_path_in_tool_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    _tools.write_lock(root)
    _tools.materialise(root)
    paths, _env = _tools.emission(root)
    held = _tools.receipts(root)
    assert paths == (held["ruff"].paths[0], held["tea"].paths[0])
    assert not _tools.bin_dir(root).exists()


def test_add_declares_locks_and_writes_a_receipt_with_no_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    (root / "workshop.toml").write_text(
        '[workspace]\n\n[tools]\nindex = "records"\nsources = ["mirror"]\n'
    )
    _tool_tasks.tools_add("tea")
    out = capsys.readouterr().out
    assert "tea 1.0.0: installed at" in out and "receipt written" in out
    assert list(_tools.receipts(root)) == ["tea"]
    lock = json.loads((root / "tools.lock").read_text())
    assert list(lock["tools"]) == ["tea"]


# --- drift ------------------------------------------------------------------------


def test_env_check_finds_a_tool_by_its_executables_not_its_lock_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`git_cliff` puts `git-cliff` on PATH: the lock name is no binary."""
    root = _workspace(tmp_path, monkeypatch)
    (root / "workshop.toml").write_text(
        '[workspace]\n\n[tools]\nindex = "records"\nsources = ["mirror", "mirror2"]\n'
        'requires = ["tea", "ruff", "cliff_tool"]\n'
    )
    payload = _zip({"cliff-tool": b"#!/bin/sh\necho cliff\n"})
    digest = digest_of(payload)
    Record(
        "cliff_tool",
        kind="download",
        hosts=THREE,
        layout=Layout(entry_points=("cliff-tool",), paths=(".",)),
        deltas=(
            RecordDelta(
                1,
                "1.0.0",
                "",
                {
                    host: Artifact(
                        f"https://origin.test/cliff/{host}.zip", digest.encoded
                    )
                    for host in THREE
                },
            ),
        ),
    ).save(root / "records")
    ObjectStore.create(root / "mirror2").put(payload)
    _tools.write_lock(root)
    _tools.write_stubs(root)
    _tools.materialise(root)
    assert _tools.receipts(root)["cliff_tool"].entry_points == ("cliff-tool",)
    # Nothing on PATH: every tool resolves through its receipt's paths.
    monkeypatch.setattr("livery.workshop._env_tasks.shutil.which", lambda tool: None)
    monkeypatch.setattr("livery.workshop._env_tasks._uv_drift", lambda root: "")
    assert _env_tasks.env_check() == 0
    out = capsys.readouterr().out
    assert "cliff_tool: receipt ok" in out and "tea: receipt ok" in out


def test_env_check_names_each_receipt_and_the_drift_under_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "livery.workshop._env_tasks.shutil.which", lambda tool: "/x/" + tool
    )
    monkeypatch.setattr("livery.workshop._env_tasks._uv_drift", lambda root: "")
    # No lock yet: named, and not a problem while the tools resolve.
    assert _env_tasks.env_check() == 0
    out = capsys.readouterr().out
    assert "tea: on PATH; not locked; run `fm tools.lock`" in out
    _tools.write_lock(root)
    # A lock expects stubs; without them the check names the remedy.
    assert _env_tasks.env_check() == 1
    assert "stubs: MISSING; run `fm tools.restub`" in capsys.readouterr().out
    _tools.write_stubs(root)
    assert _env_tasks.env_check() == 0
    out = capsys.readouterr().out
    assert "ruff: on PATH; no receipt for 0.16.0; run `fm sync`" in out
    assert "stubs: 1 in typings/" in out
    _tools.materialise(root)
    assert _env_tasks.env_check() == 0
    out = capsys.readouterr().out
    assert "tea: receipt ok" in out and "ruff: receipt ok" in out

    # The record moves under the lock: a new artifact for the same version.
    record = Record.load(root / "records" / "tea.jsonl")
    moved = Record(
        "tea",
        kind="download",
        hosts=THREE,
        layout=Layout(entry_points=("tea",), paths=(".",)),
        deltas=(
            RecordDelta(
                1,
                "1.0.0",
                "",
                {
                    host: Artifact(f"https://origin.test/tea/{host}-2.zip", "f" * 64)
                    for host in THREE
                },
            ),
        ),
    )
    moved.save(root / "records")
    _tools.write_lock(root)  # the lock's deployment digest moves with the record
    assert _env_tasks.env_check() == 1
    out = capsys.readouterr().out
    assert "tea: DRIFT: the deployment of 1.0.0 on" in out and "run `fm sync`" in out
    del record

    # A tool not resolving at all is missing, breakdown and remedy printed.
    monkeypatch.setattr("livery.workshop._env_tasks.shutil.which", lambda tool: None)
    (_tools.receipts_dir(root) / "ruff.json").unlink()
    assert _env_tasks.env_check() == 1
    out = capsys.readouterr().out
    assert "ruff: MISSING" in out and "materialise them" in out

    # A receipt at another version than the lock is named too.
    monkeypatch.setattr(
        "livery.workshop._env_tasks.shutil.which", lambda tool: "/x/" + tool
    )
    Record("ruff", kind="uv-tool", deltas=_read("0.16.0", "0.17.0")).save(
        root / "records"
    )
    _tools.materialise(root, ("ruff",))
    _tools.write_lock(root, upgrade=("ruff",))
    assert _tools.drift(root)["ruff"] == "receipt 0.16.0, lock 0.17.0; run `fm sync`"


def test_the_receipt_round_trips_and_the_default_modes_follow_the_kind(
    tmp_path: Path,
) -> None:
    receipt = _tools.Receipt(
        "tea",
        "1.0.0",
        "linux-x64",
        "download",
        "path",
        "sha256:" + "0" * 64,
        "/t",
        ("/t",),
        {"A": "b"},
        (),
    )
    path = tmp_path / "tea.json"
    path.write_text(json.dumps(receipt.to_json()))
    assert _tools.Receipt.load(path) == receipt
    from livery.toolroom.store import default_mode

    assert (
        default_mode("download"),
        default_mode("download", ("bin",)),
        default_mode("uv-tool"),
    ) == (
        "none",
        "path",
        "path",
    )
    assert default_mode("system-check") == "none"
