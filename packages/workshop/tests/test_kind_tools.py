"""The three declaration sites and the lock: refusals, resolution, then the verbs."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from livery.footman.context import Failed
from livery.toolroom.store import (
    Artifact,
    Graph,
    Layout,
    Lock,
    Record,
    RecordDelta,
    Surface,
)
from livery.workshop import _tool_tasks, _tools
from workshop_hosts import (  # noqa: F401
    HOSTS,
    lock_for_this_host,
    no_graph_resolution,
)

SHA = "d8b96221828ad6f97ac7ac0ab7e95872341af763001e8803e8267652c2652620"
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


def _bun(*versions: str) -> Record:
    """bun, the archive an npm tool naming it runs on, on the three hosts."""
    return Record(
        "bun",
        kind="download",
        hosts=THREE,
        layout=Layout(entry_points=("bun",), paths=(".",)),
        deltas=tuple(
            RecordDelta(
                n,
                v,
                "",
                {
                    host: Artifact(f"https://x/bun/{v}/{host}.zip", SHA)
                    for host in THREE
                },
            )
            for n, v in enumerate(versions, start=1)
        ),
    )


def _node(*versions: str) -> Record:
    """node, the archive an npm tool runs on by default, on the three hosts."""
    return Record(
        "node",
        kind="download",
        hosts=THREE,
        layout=Layout(entry_points=("bin/node",), paths=("bin",)),
        deltas=tuple(
            RecordDelta(
                n,
                v,
                "",
                {
                    host: Artifact(f"https://x/node/{v}/{host}.tar.gz", SHA)
                    for host in THREE
                },
            )
            for n, v in enumerate(versions, start=1)
        ),
    )


def _records(root: Path, *records: Record) -> None:
    for record in records:
        record.save(root / "records")


def _python_tools(*versions: str) -> list[Record]:
    """A record per tool the python kind requires, at *versions*."""
    return [
        Record(name, kind="pypi", deltas=_read(*versions))
        for name in (
            "git_cliff",
            "uv",
            "ruff",
            "pytest",
            "basedpyright",
            "mypy",
            "ty",
            "pyrefly",
        )
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
        'kind = "python"\nname = "acme-member"\n'
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
        'kind = "python"\nname = "acme-member"\n\n[tools]\nrequires = ["ruff>=9.0"]\n'
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
            kind="download",
            hosts=THREE,
            layout=Layout(file="tea", entry_points=("tea",), paths=(".",)),
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
        "git_cliff",
        "uv",
        "ruff",
        "pytest",
        "basedpyright",
        "mypy",
        "ty",
        "pyrefly",
    }
    # The base kind heads the chain: its tool is declared first, by it.
    assert {r.name: r.site for r in declared}["git_cliff"] == "kind base"
    assert all(r.site == "kind python" for r in declared if r.name != "git_cliff")
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
        'kind = "python"\nname = "acme-member"\n\n[tools]\nrequires = ["cspell>=1.0"]\n'
    )
    _records(
        root,
        Record("git-cliff", kind="pypi", deltas=_read("2.0.0")),
        Record("cspell", kind="npm", runtime="bun", deltas=_read("1.0.0", "2.0.0")),
        _bun("1.3.0"),
    )
    sites = {(r.name, r.site) for r in _tools.requirements(root)}
    assert ("cspell", "packages/member/workshop.toml") in sites
    assert ("git-cliff", "workshop.toml") in sites
    assert ("ruff", "workshop.toml") in sites and ("ruff", "kind python") in sites
    lock = _tools.write_lock(root)
    assert lock.tools["cspell"].version == "2.0.0"
    assert lock.tools["git-cliff"].version == "2.0.0"
    # bun is cspell's dependency: locked, though no site names it.
    assert lock.tools["bun"].version == "1.3.0" and set(lock.tools["bun"].hosts) == set(
        THREE
    )
    # The kinds first, as declared: the base's tool, then python's.
    assert _tools.tool_names(root)[:3] == ("git_cliff", "uv", "ruff")


def test_a_workspace_without_packages_requires_what_python_does(tmp_path: Path) -> None:
    assert _tools.tool_names(tmp_path) == (
        "git_cliff",  # the base kind's, first in the chain
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
    _records(root, Record("git-cliff", kind="pypi", deltas=_read("2.0.0", "2.1.0")))
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
    # Seven of the python kind, the base kind's git_cliff, and this one.
    assert "git-cliff 2.1.0" in out and "tools.lock: 9 tool(s)" in out
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
    _records(root, Record("black", kind="pypi", deltas=_read("1.0.0")))
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
    _records(root, Record("ruff", kind="pypi", deltas=_read("1.0.0", "1.1.0", "1.2.0")))
    _tool_tasks.tools_lock()
    assert Lock.load(root / "tools.lock").tools["ruff"].version == "1.1.0"
    capsys.readouterr()
    _tool_tasks.tools_lock(upgrade_tool=["ruff"])
    out = capsys.readouterr().out
    assert "ruff 1.2.0  moved" in out
    after = Lock.load(root / "tools.lock")
    assert after.tools["ruff"].version == "1.2.0"
    assert {n: e for n, e in after.tools.items() if n != "ruff"} == {
        n: e for n, e in before.tools.items() if n != "ruff"
    }  # one entry moved, the diff names one version
    _tool_tasks.tools_lock(upgrade_tool=["ruff"])
    assert "nothing moved: ruff already at the newest" in capsys.readouterr().out
    with pytest.raises(Failed, match=r"black is not a tool the sites require"):
        _tool_tasks.tools_lock(upgrade_tool=["black"])


def test_the_verbs_refuse_outside_a_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "livery.workshop._layers.workspace_root", lambda start=None: None
    )
    with pytest.raises(Failed, match=r"no workspace"):
        _tool_tasks.tools_lock()


def test_a_tool_no_site_requires_any_more_leaves_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch, tools='requires = ["cspell"]\n')
    _records(
        root,
        Record("cspell", kind="npm", runtime="bun", deltas=_read("2.0.0")),
        _bun("1.3.0"),
    )
    locked = _tools.write_lock(root).tools
    assert "cspell" in locked and "bun" in locked
    (root / "workshop.toml").write_text('[workspace]\n\n[tools]\nindex = "records"\n')
    locked = _tools.write_lock(root).tools
    # bun leaves with the tool it was locked for.
    assert "cspell" not in locked and "bun" not in locked


def test_a_graph_is_written_once_and_kept_until_its_version_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A graph is resolved when a version enters the lock, and kept after.

    A lock names artefacts and hashes, so an installer that moved
    installs the same graph. A file edited or gone since is resolved
    again, and a tool that leaves takes its graph with it.
    """
    from livery.strongroom import digest_of

    root = _workspace(tmp_path, monkeypatch, tools='requires = ["cspell"]\n')
    _records(
        root,
        Record("cspell", kind="npm", deltas=_read("2.0.0", "3.0.0")),
        _node("24.0.0"),
    )
    calls: list[tuple[str, str]] = []

    def resolve(root_: Path, name: str, package: str, version: str, **kwargs: object):
        calls.append((name, version))
        written = _tools.graphs_dir(root_) / f"{name}.json"
        written.parent.mkdir(parents=True, exist_ok=True)
        written.write_text(f"graph of {name} {version}\n", encoding="utf-8")
        made = Graph(written.name, digest_of(written.read_bytes()), by="node 24")
        return made, ""

    monkeypatch.setattr(_tools, "resolve_graph", resolve)
    locked = _tools.write_lock(root).tools["cspell"]
    mine = [c for c in calls if c[0] == "cspell"]
    assert mine == [("cspell", "3.0.0")]
    assert locked.graph is not None and locked.graph.file == "cspell.json"
    # A second lock resolves nothing: the version has not moved.
    assert _tools.write_lock(root).tools["cspell"].graph == locked.graph
    assert [c for c in calls if c[0] == "cspell"] == mine
    # A graph edited by hand no longer matches its digest, so it is written again.
    (_tools.graphs_dir(root) / "cspell.json").write_text("meddled\n", encoding="utf-8")
    assert _tools.write_lock(root).tools["cspell"].graph == locked.graph
    assert [c for c in calls if c[0] == "cspell"] == mine * 2
    # The tool leaves the lock, and its graph file goes with it.
    (root / "workshop.toml").write_text('[workspace]\n\n[tools]\nindex = "records"\n')
    assert "cspell" not in _tools.write_lock(root).tools
    assert not (_tools.graphs_dir(root) / "cspell.json").exists()


def test_relock_writes_a_graph_again_though_its_version_stands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What an install asks for when the runtime cannot satisfy the graph.

    A resolve is a fresh answer from an index, so it moves the lock,
    and a lock moves when a person says so: naming the tool is that
    saying, and the tools beside it keep the graphs they had.
    """
    from livery.strongroom import digest_of

    root = _workspace(tmp_path, monkeypatch, tools='requires = ["cspell", "eslint"]\n')
    _records(
        root,
        Record("cspell", kind="npm", deltas=_read("2.0.0")),
        Record("eslint", kind="npm", deltas=_read("9.0.0")),
        _node("24.0.0"),
    )
    calls: list[str] = []

    def resolve(root_: Path, name: str, package: str, version: str, **kwargs: object):
        calls.append(name)
        written = _tools.graphs_dir(root_) / f"{name}.json"
        written.parent.mkdir(parents=True, exist_ok=True)
        written.write_text(f"{name} {len(calls)}\n", encoding="utf-8")
        return Graph(written.name, digest_of(written.read_bytes()), by="node 24"), ""

    monkeypatch.setattr(_tools, "resolve_graph", resolve)
    first = _tools.write_lock(root).tools
    calls.clear()
    moved = _tools.write_lock(root, relock=("cspell",)).tools
    assert calls == ["cspell"]  # the one named, and no other
    assert moved["cspell"].graph != first["cspell"].graph
    assert moved["eslint"].graph == first["eslint"].graph


def test_a_graph_that_cannot_be_resolved_leaves_the_tool_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A runtime that is not here yet costs the graph, never the lock.

    The version locks and the note says the graph waits.
    """
    root = _workspace(tmp_path, monkeypatch, tools='requires = ["cspell"]\n')
    _records(root, Record("cspell", kind="npm", deltas=_read("2.0.0")), _node("24.0.0"))
    monkeypatch.setattr(
        _tools,
        "resolve_graph",
        lambda *a, **k: (
            None,
            "node is not materialised here; the next lock writes it",
        ),
    )
    locked = _tools.write_lock(root).tools["cspell"]
    assert locked.version == "2.0.0" and locked.graph is None
    assert (
        "graphs: cspell 2.0.0: node is not materialised here" in capsys.readouterr().out
    )


def test_each_runtime_is_locked_once_for_the_tools_that_run_on_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(
        tmp_path, monkeypatch, tools='requires = ["cspell", "basedpyright", "eslint"]\n'
    )
    _records(
        root,
        Record("cspell", kind="npm", runtime="bun", deltas=_read("2.0.0")),
        Record("basedpyright", kind="npm", deltas=_read("1.39.0")),
        Record("eslint", kind="npm", deltas=_read("9.0.0")),
        _bun("1.3.0"),
        _node("24.0.0"),
    )
    locked = _tools.write_lock(root).tools
    # Both runtimes, each once: node serves two tools, bun one.
    assert {"cspell", "basedpyright", "eslint", "bun", "node"} <= set(locked)
    (root / "workshop.toml").write_text(
        '[workspace]\n\n[tools]\nindex = "records"\nrequires = ["eslint"]\n'
    )
    locked = _tools.write_lock(root).tools
    # bun leaves with the tool it was locked for; node stays, since the
    # python kind's basedpyright still runs on it.
    assert "bun" not in locked and "cspell" not in locked and "node" in locked


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


def test_a_workspace_with_no_lock_is_told_what_to_declare_and_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal a newborn meets, and it names both halves.

    A workspace requires tools whatever its contract says, because a
    kind brings its own: a project with no packages still runs on the
    python kind. Nothing is supplied until a lock names versions, and
    nothing is locked until the contract names a catalogue, so the
    answer has to carry both or a reader fixes one and hits the other.
    """
    root = tmp_path / "newborn"
    root.mkdir()
    (root / "workshop.toml").write_text('[workspace]\nlayers = ["livery.workshop"]\n')
    told = _tools.store_cannot_supply(root)
    # What is missing, in the tools' own names.
    assert "are not locked" in told
    for name in ("ruff", "pytest"):
        assert name in told
    # The declaration, then the two verbs, in the order a reader runs them.
    assert "[tools]" in told and "index =" in told
    assert told.index("tools.lock") < told.index("sync")
    # A contract that already names a catalogue is told only what is left.
    (root / "workshop.toml").write_text(
        '[workspace]\n\n[tools]\nindex = "https://example.test/index"\n'
    )
    named = _tools.store_cannot_supply(root)
    assert "index =" not in named
    assert "tools.lock" in named
    # A lock answers the question, so nothing is said.
    _records(root, *_python_tools("1.0.0"))
    monkeypatch.setattr(
        "livery.workshop._layers.workspace_root", lambda start=None: root
    )
    (root / "workshop.toml").write_text('[workspace]\n\n[tools]\nindex = "records"\n')
    _tool_tasks.tools_lock()
    assert _tools.current_lock(root) is not None
    assert _tools.store_cannot_supply(root) == ""
    # And a workspace that requires nothing at all has nothing to say.
    monkeypatch.setattr(_tools, "tool_names", lambda _root: ())
    (root / "tools.lock").unlink()
    assert _tools.store_cannot_supply(root) == ""


def test_a_receipt_is_checked_against_what_it_claims(tmp_path: Path) -> None:
    """The store's own record is what gets checked, claim by claim.

    Looking for an executable by a tool's name answers a different
    question and answers it wrongly twice over: a tool whose binary is
    spelled differently reads as missing, and a tool that is no program
    at all can never be found. What a receipt claims is what has to hold.
    """
    bin_dir = tmp_path / "venv" / "bin"
    bin_dir.mkdir(parents=True)
    held = tmp_path / "store" / "tool" / "bin"
    held.mkdir(parents=True)

    def receipt(**fields: object) -> _tools.Receipt:
        body: dict[str, object] = {
            "tool": "tea",
            "version": "1.0.0",
            "host": "linux-x64",
            "kind": "download",
            "mode": "path",
            "deployment": "sha256:" + "0" * 64,
            "tool_dir": str(held.parent),
        }
        body.update(fields)
        return _tools.Receipt(**body)  # type: ignore[arg-type]

    # The refusals first. A path the receipt claims and the store lost.
    gone = _tools.receipt_gap(
        "tea", receipt(paths=(str(tmp_path / "vanished"),)), bin_dir
    )
    assert "is not there" in gone
    # An entry point that runs from nowhere.
    silent = _tools.receipt_gap(
        "tea", receipt(paths=(str(held),), entry_points=("teapot",)), bin_dir
    )
    assert "teapot" in silent and "does not run" in silent
    # An environment value pointing at a file the store no longer holds.
    absent = _tools.receipt_gap(
        "tea",
        receipt(mode="none", env={"TEA_PROVIDER": str(tmp_path / "provider.cmake")}),
        bin_dir,
    )
    assert "TEA_PROVIDER" in absent and "is not there" in absent
    # A receipt claiming nothing was verified on the host, so the host
    # has to answer for it now.
    assert "not on PATH now" in _tools.receipt_gap("tea", receipt(mode="none"), bin_dir)

    # And what holds, in each of the three shapes.
    (held / "tea-real").touch()
    assert (
        _tools.receipt_gap(
            "tea", receipt(paths=(str(held),), entry_points=("tea-real",)), bin_dir
        )
        == ""
    )
    (bin_dir / "tea-venv").touch()
    assert (
        _tools.receipt_gap(
            "tea", receipt(paths=(str(held),), entry_points=("tea-venv",)), bin_dir
        )
        == ""
    )
    provider = tmp_path / "provider.cmake"
    provider.write_text("# a file CMake reads\n")
    assert (
        _tools.receipt_gap(
            "tea", receipt(mode="none", env={"TEA_PROVIDER": str(provider)}), bin_dir
        )
        == ""
    )


def _no_install(monkeypatch: pytest.MonkeyPatch, seen: list[bool]) -> None:
    """Stand in for the store: the modes are about the lock, not the install."""

    def stood_in(root: Path, offline: bool = False) -> list[str]:
        seen.append(offline)
        return ["  tools: stood in for"]

    monkeypatch.setattr("livery.workshop._sync.materialise_tools", stood_in)


def test_the_sync_modes_refuse_before_anything_is_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The refusals first: two answers to one question, and a lock that moved.

    `--frozen` installs the lock as it is and `--locked` refuses when it
    is not current, so asking for both asks for opposite things.
    """
    root = _workspace(tmp_path, monkeypatch)
    installs: list[bool] = []
    _no_install(monkeypatch, installs)
    with pytest.raises(Failed, match="two answers to one question"):
        _tool_tasks.tools_sync(frozen=True, locked=True)
    # Nothing is locked yet, so --locked refuses and names what is missing.
    with pytest.raises(Failed, match=re.escape("there is no tools.lock")):
        _tool_tasks.tools_sync(locked=True)
    assert installs == []  # neither refusal reached the store
    # Frozen installs what the lock says and never resolves, so with no
    # lock it has nothing to install and says so.
    _tool_tasks.tools_sync(frozen=True)
    assert installs == [False]
    assert _tools.current_lock(root) is None


def test_the_default_sync_writes_the_lock_it_needs_then_installs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Uv's shape: lock when there is none or the declarations moved, then install."""
    root = _workspace(tmp_path, monkeypatch)
    installs: list[bool] = []
    _no_install(monkeypatch, installs)
    _tool_tasks.tools_sync()
    assert _tools.current_lock(root) is not None
    assert "writing it" in capsys.readouterr().out
    assert installs == [False]
    # A second run finds the lock current, writes nothing, installs again.
    held = (root / "tools.lock").read_bytes()
    _tool_tasks.tools_sync()
    assert (root / "tools.lock").read_bytes() == held
    assert "writing it" not in capsys.readouterr().out
    assert installs == [False, False]
    # --locked passes now, and --offline reaches the store's own copy.
    _tool_tasks.tools_sync(locked=True, offline=True)
    assert installs == [False, False, True]


def test_the_lock_check_answers_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--check` is uv's: the answer, and nothing written whichever way it goes."""
    root = _workspace(tmp_path, monkeypatch)
    # The refusals first: no lock at all, and a contradiction.
    with pytest.raises(Failed, match=re.escape("there is no tools.lock")):
        _tool_tasks.tools_lock(check=True)
    with pytest.raises(Failed, match="cannot be asked to upgrade"):
        _tool_tasks.tools_lock(check=True, upgrade=True)
    assert _tools.current_lock(root) is None
    _tool_tasks.tools_lock()
    capsys.readouterr()
    _tool_tasks.tools_lock(check=True)
    assert "current" in capsys.readouterr().out
    # A tool the sites require and the lock does not hold: the check names
    # what would move and writes nothing, so the file stands until
    # someone locks deliberately.
    held = (root / "tools.lock").read_bytes()
    _records(root, Record("tea", kind="pypi", deltas=_read("1.0.0")))
    _tools.declare(root, "tea")
    with pytest.raises(Failed, match="the lock would move: tea"):
        _tool_tasks.tools_lock(check=True)
    assert (root / "tools.lock").read_bytes() == held
    # A requirement the catalogue cannot satisfy at all is the other
    # reason, and it is reported as the resolver put it.
    _tools.declare(root, "coffee")
    with pytest.raises(Failed, match="no record of coffee"):
        _tool_tasks.tools_lock(check=True)
    assert (root / "tools.lock").read_bytes() == held
