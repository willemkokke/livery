"""The three declaration sites and the lock: refusals, resolution, then the verbs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from livery.footman.context import Failed
from livery.toolroom.store import (
    Artifact,
    Layout,
    Lock,
    Record,
    RecordDelta,
    Surface,
)
from livery.workshop import _tool_tasks, _tools

SHA = "d8b96221828ad6f97ac7ac0ab7e95872341af763001e8803e8267652c2652620"
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


def _records(root: Path, *records: Record) -> None:
    for record in records:
        record.save(root / "records" / record.name)


def _python_tools(*versions: str) -> list[Record]:
    """A record per tool the python kind requires, at *versions*."""
    return [
        Record(name, kind="uv-tool", deltas=_read(*versions))
        for name in ("uv", "ruff", "pytest", "basedpyright", "mypy", "ty", "pyrefly")
    ]


def _workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, tools: str = ""
) -> Path:
    """A python workspace with one member and its records beside it."""
    root = tmp_path / "ws"
    (root / "packages" / "member").mkdir(parents=True)
    (root / "workshop.toml").write_text(
        f'[workspace]\n\n[tools]\nindex = "records"\n{tools}'
    )
    (root / "packages" / "member" / "workshop.toml").write_text(
        'type = "python"\nname = "acme-member"\n'
    )
    (root / "packages" / "member" / "pyproject.toml").write_text(
        '[project]\nname = "acme-member"\n'
    )
    _records(root, *_python_tools("1.0.0", "1.1.0"))
    monkeypatch.setattr(
        "livery.workshop._layers.workspace_root", lambda start=None: root
    )
    return root


# --- the refusals ---------------------------------------------------------------


def test_two_floors_that_cannot_both_be_met_refuse_naming_each_and_its_site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch, tools='requires = ["ruff>=1.1"]\n')
    (root / "packages" / "member" / "workshop.toml").write_text(
        'type = "python"\nname = "acme-member"\n\n[tools]\nrequires = ["ruff>=9.0"]\n'
    )
    with pytest.raises(Failed) as refused:
        _tools.write_lock(root)
    assert (
        "ruff: no version satisfies ruff>=9.0 (packages/member/workshop.toml)"
        in str(refused.value)
    )
    assert "the newest version listed is 1.1.0" in str(refused.value)
    assert not (root / "tools.lock").exists()


def test_a_requirement_with_no_record_refuses_naming_the_site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch, tools='requires = ["black"]\n')
    with pytest.raises(
        Failed, match=r"no record of black; .*required by workshop.toml"
    ):
        _tools.write_lock(root)


def test_a_version_on_fewer_hosts_than_the_lock_covers_refuses_naming_the_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch, tools='requires = ["tea>=1.1"]\n')
    _records(
        root,
        Record(
            "tea",
            kind="binary",
            hosts=THREE,
            layout=Layout(exe="tea", entry_points=("tea",), paths=(".",)),
            deltas=(
                RecordDelta(
                    1,
                    "1.0.0",
                    "",
                    {h: Artifact(f"https://x/1/{h}", SHA) for h in THREE},
                ),
                RecordDelta(
                    2,
                    "1.1.0",
                    "",
                    {h: Artifact(f"https://x/1.1/{h}", SHA) for h in THREE[:2]},
                ),
            ),
        ),
    )
    with pytest.raises(Failed) as refused:
        _tools.write_lock(root)
    assert "tea: no version satisfying tea>=1.1 has host windows-x64" in str(
        refused.value
    )
    assert "the first version that has it is 1.0.0" in str(refused.value)
    # Locking for the two hosts that have it resolves.
    (root / "workshop.toml").write_text(
        '[workspace]\n\n[tools]\nindex = "records"\nrequires = ["tea>=1.1"]\n'
        'hosts = ["linux-x64", "macos-arm"]\n'
    )
    assert _tools.write_lock(root).tools["tea"].version == "1.1.0"


def test_a_contract_off_the_shape_refuses_naming_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch, tools='requires = "ruff"\n')
    with pytest.raises(
        Failed, match=r"workshop.toml: \[tools\] requires is not a list"
    ):
        _tools.requirements(root)
    (root / "workshop.toml").write_text(
        '[workspace]\n\n[tools]\nindex = "records"\nrequires = ["ruff<1"]\n'
    )
    with pytest.raises(Failed, match=r"workshop.toml: 'ruff<1' is not a requirement"):
        _tools.requirements(root)
    (root / "workshop.toml").write_text(
        '[workspace]\n\n[tools]\nindex = "records"\nhosts = "linux-x64"\n'
    )
    with pytest.raises(Failed, match=r"\[tools\] hosts is not a list"):
        _tools.locked_hosts(root)
    (root / "workshop.toml").write_text("[workspace]\n")
    with pytest.raises(Failed, match=r"\[tools\] index names no source"):
        _tools.index_source(root)
    (root / "workshop.toml").write_text('[workspace]\n\n[tools]\nindex = "elsewhere"\n')
    with pytest.raises(Failed, match=r"no pointer can be read"):
        _tools.catalogue(root)
    (root / "workshop.toml").write_text('[workspace]\n\n[tools]\nindex = "records"\n')
    (root / "tools.lock").write_text("{not a lock")
    with pytest.raises(Failed, match=r"tools\.lock: not a lock"):
        _tools.current_lock(root)
    with pytest.raises(Failed, match=r"is not a requirement"):
        _tools.declare(root, "ruff<1")


# --- resolution ---------------------------------------------------------------------


def test_a_python_package_with_no_tool_of_its_own_resolves_the_kinds_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    declared = _tools.requirements(root)
    assert {r.name for r in declared} == {
        "uv",
        "ruff",
        "pytest",
        "basedpyright",
        "mypy",
        "ty",
        "pyrefly",
    }
    assert all(r.site == "kind python" for r in declared)
    lock = _tools.write_lock(root)
    assert {name: entry.version for name, entry in lock.tools.items()} == dict.fromkeys(
        {r.name for r in declared}, "1.1.0"
    )
    assert lock.hosts == _tools.DEFAULT_HOSTS
    assert Lock.load(root / "tools.lock") == lock
    # The profile is the sites' names, and nothing in code lists them.
    from livery.workshop._env_tasks import tool_profile

    assert set(tool_profile(root)) == set(lock.tools)
    source = (Path(_tools.__file__).parent / "_env_tasks.py").read_text(
        encoding="utf-8"
    )
    assert '"ruff", "pytest"' not in source


def test_the_three_sites_union_and_each_names_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(
        tmp_path, monkeypatch, tools='requires = ["git-cliff", "ruff>=1.1"]\n'
    )
    (root / "packages" / "member" / "workshop.toml").write_text(
        'type = "python"\nname = "acme-member"\n\n[tools]\nrequires = ["cspell>=1.0"]\n'
    )
    _records(
        root,
        Record("git-cliff", kind="uv-tool", deltas=_read("2.0.0")),
        Record("cspell", kind="bun-install", deltas=_read("1.0.0", "2.0.0")),
    )
    sites = {(r.name, r.site) for r in _tools.requirements(root)}
    assert ("cspell", "packages/member/workshop.toml") in sites
    assert ("git-cliff", "workshop.toml") in sites
    assert ("ruff", "workshop.toml") in sites and ("ruff", "kind python") in sites
    lock = _tools.write_lock(root)
    assert lock.tools["cspell"].version == "2.0.0"
    assert lock.tools["git-cliff"].version == "2.0.0"
    assert _tools.tool_names(root)[:2] == ("uv", "ruff")  # the kind first, as declared


def test_a_workspace_without_packages_requires_what_python_does(tmp_path: Path) -> None:
    assert _tools.tool_names(tmp_path) == (
        "uv",
        "ruff",
        "pytest",
        "basedpyright",
        "mypy",
        "ty",
        "pyrefly",
    )


# --- the verbs -----------------------------------------------------------------------


def test_add_declares_at_the_project_site_and_locks_with_no_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    _records(root, Record("git-cliff", kind="uv-tool", deltas=_read("2.0.0", "2.1.0")))
    import socket

    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("the network was reached")

    monkeypatch.setattr(socket, "create_connection", no_network)
    from livery.toolroom.store import _engine

    def installing(argv: list[str], env: dict[str, str]) -> int:
        bin_dir = Path(env["UV_TOOL_BIN_DIR"])
        bin_dir.mkdir(parents=True, exist_ok=True)
        (bin_dir / "git-cliff").write_text("launcher")
        return 0

    monkeypatch.setattr(_engine, "run_installer", installing)
    monkeypatch.setattr("livery.footman.context.data_dir", lambda: tmp_path / "data")
    _tool_tasks.tools_add("git-cliff>=2.0")
    out = capsys.readouterr().out
    assert "workshop.toml: [tools] requires git-cliff>=2.0" in out
    assert "git-cliff 2.1.0" in out and "tools.lock: 8 tool(s)" in out
    assert "git-cliff 2.1.0: installed at" in out and "receipt written" in out
    assert (root / ".workshop" / "receipts" / "git-cliff.json").is_file()
    contract = (root / "workshop.toml").read_text(encoding="utf-8")
    assert 'requires = ["git-cliff>=2.0"]' in contract
    lock = json.loads((root / "tools.lock").read_text(encoding="utf-8"))
    assert lock["tools"]["git-cliff"] == {"version": "2.1.0", "hosts": {}}

    # Declared again: the contract stands, and says so.
    _tool_tasks.tools_add("git-cliff>=2.0")
    assert "was declared already" in capsys.readouterr().out
    # A second requirement joins the list on the same line.
    _records(root, Record("black", kind="uv-tool", deltas=_read("1.0.0")))
    _tool_tasks.tools_add("black")
    contract = (root / "workshop.toml").read_text(encoding="utf-8")
    assert 'requires = ["git-cliff>=2.0", "black"]' in contract
    with pytest.raises(Failed, match=r"is not a requirement"):
        _tool_tasks.tools_add("black==1")


def test_declare_edits_every_shape_of_the_requires_list(tmp_path: Path) -> None:
    path = tmp_path / "workshop.toml"
    path.write_text("[workspace]\n")
    assert _tools.declare(tmp_path, "ruff")
    assert path.read_text() == '[workspace]\n\n[tools]\nrequires = ["ruff"]\n'
    path.write_text('[tools]\nindex = "records"\n\n[forge]\nkind = "github"\n')
    assert _tools.declare(tmp_path, "ruff")
    expected = (
        '[tools]\nrequires = ["ruff"]\nindex = "records"\n\n[forge]\nkind = "github"\n'
    )
    assert path.read_text() == expected
    path.write_text('[tools]\nrequires = [\n    "ruff",\n]\n')
    assert _tools.declare(tmp_path, "ty>=1")
    assert path.read_text() == '[tools]\nrequires = [\n    "ruff",\n    "ty>=1",\n]\n'
    path.write_text("[tools]\nrequires = []\n")
    assert _tools.declare(tmp_path, "ruff")
    assert path.read_text() == '[tools]\nrequires = ["ruff"]\n'
    assert not _tools.declare(tmp_path, "ruff")


def test_upgrade_moves_one_entry_and_every_package_with_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    _tool_tasks.tools_lock()
    before = Lock.load(root / "tools.lock")
    assert before.tools["ruff"].version == "1.1.0"
    # A newer ruff arrives; the lock stands until asked.
    _records(
        root, Record("ruff", kind="uv-tool", deltas=_read("1.0.0", "1.1.0", "1.2.0"))
    )
    _tool_tasks.tools_lock()
    assert Lock.load(root / "tools.lock").tools["ruff"].version == "1.1.0"
    capsys.readouterr()
    _tool_tasks.tools_upgrade(["ruff"])
    out = capsys.readouterr().out
    assert "ruff 1.2.0  moved" in out
    after = Lock.load(root / "tools.lock")
    assert after.tools["ruff"].version == "1.2.0"
    assert {n: e for n, e in after.tools.items() if n != "ruff"} == {
        n: e for n, e in before.tools.items() if n != "ruff"
    }  # one entry moved, the diff names one version
    _tool_tasks.tools_upgrade(["ruff"])
    assert "nothing moved: ruff already at the newest" in capsys.readouterr().out
    with pytest.raises(Failed, match=r"black is not a tool the sites require"):
        _tool_tasks.tools_upgrade(["black"])


def test_the_verbs_refuse_outside_a_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "livery.workshop._layers.workspace_root", lambda start=None: None
    )
    with pytest.raises(Failed, match=r"no workspace"):
        _tool_tasks.tools_lock()


def test_a_tool_no_site_requires_any_more_leaves_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch, tools='requires = ["git-cliff"]\n')
    _records(root, Record("git-cliff", kind="uv-tool", deltas=_read("2.0.0")))
    assert "git-cliff" in _tools.write_lock(root).tools
    (root / "workshop.toml").write_text('[workspace]\n\n[tools]\nindex = "records"\n')
    assert "git-cliff" not in _tools.write_lock(root).tools


def test_the_catalogue_reads_an_index_directory_through_the_machines_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A consumer names an index, not records; the lock resolves the same."""
    from livery.strongroom import Entry, Store, Tree, canonical

    root = _workspace(tmp_path, monkeypatch)
    index = root / "index"
    store = Store.create(index)
    pointer: dict[str, object] = {}
    for record in _python_tools("1.0.0", "1.1.0"):
        entries = []
        for name, value in (
            ("tool", record.to_json()),
            ("versions", list(record.versions)),
        ):
            data = canonical(value)
            entries.append(Entry(name, "blob", store.put(data), len(data)))
        for delta in record.deltas:
            data = Tree.of([]).encode()
            entries.append(Entry(delta.version, "tree", store.put(data), len(data)))
        data = Tree.of(entries).encode()
        pointer[record.name] = {"tree": str(store.put(data)), "record": "x"}
    (index / "pointer.json").write_text(json.dumps({"schema": 1, "tools": pointer}))
    (root / "workshop.toml").write_text('[workspace]\n\n[tools]\nindex = "index"\n')
    monkeypatch.setattr("livery.footman.context.data_dir", lambda: tmp_path / "data")
    assert _tools.write_lock(root) == _tools.write_lock(root)
    assert _tools.write_lock(root).tools["ruff"].version == "1.1.0"
