"""Prose fragments: the refusals first, then each reader's set and the delivery."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import livery.workshop
from livery.workshop import _prose, _steps, _tools
from livery.workshop._phases import PhaseContext
from livery.workshop._prose import (
    AGENT,
    HUMAN,
    ProseError,
    fragments,
    guidance,
    parse_name,
    register_fragment,
    register_section,
    render_compositions,
    render_gate,
    render_tools,
    render_verbs,
    sections,
    shipped,
    unregister_fragment,
    unregister_section,
)
from livery.workshop._steps import StepRecord, register_step
from workshop_cmake_declared import cmake_steps_fixture  # noqa: F401
from workshop_python_steps import python_steps_fixture  # noqa: F401

WORKSHOP_CONTENT = Path(livery.workshop.__file__).resolve().parent / "content"


@pytest.fixture
def restored():
    prose = _prose.snapshot()
    steps = _steps.snapshot()
    yield
    _prose.restore(prose)
    _steps.restore(steps)


def _workspace(tmp_path: Path, *extensions: str) -> Path:
    listed = ", ".join(f'"{extension}"' for extension in extensions)
    (tmp_path / "workshop.toml").write_text(f"[workspace]\nextensions = [{listed}]\n")
    return tmp_path


def _extension(tmp_path: Path, name: str, **files: str) -> Path:
    """A fake extension's content directory, its fragments written from *files*."""
    content = tmp_path / "extensions" / name / "content"
    for filename, text in files.items():
        name = (
            filename
            if filename.endswith(".md")
            else filename.replace("__", ".") + ".md"
        )
        target = content / "fragments" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return content


def _member(root: Path, name: str, *extensions: str) -> None:
    member = root / "packages" / name
    member.mkdir(parents=True)
    listed = ", ".join(f'"{extension}"' for extension in extensions)
    held = f"extensions = [{listed}]\n" if extensions else ""
    (member / "workshop.toml").write_text(f'name = "acme-{name}"\n{held}')
    if "cmake" not in extensions:
        (member / "pyproject.toml").write_text(
            f'[project]\nname = "acme-{name}"\nversion = "0.1.0"\n'
        )


def _names(root: Path, listed: list[_prose.Prose], audience: str | None) -> list[str]:
    """The shipped names in a reader's set, the rendered ones left out."""
    return [p.name for p in fragments(root, listed, audience) if p.source is not None]


def _noop(ctx: PhaseContext) -> None:
    del ctx


def test_a_name_outside_the_convention_refuses_naming_the_file(tmp_path: Path) -> None:
    for filename in ("notes.md", "rules..md", "a.b.c.d.e.md", "rules.workshop.txt"):
        with pytest.raises(
            ProseError, match=f"{filename}: a fragment is named <section>"
        ):
            parse_name(filename)
    content = _extension(tmp_path, "acme.brand", **{"notes.md": "# Notes\n"})
    with pytest.raises(
        ProseError, match=r"extensions/acme\.brand/content/fragments/notes\.md: a"
    ):
        shipped("acme.brand", content)


def test_an_unknown_section_or_a_kind_in_the_name_refuses_naming_the_file() -> None:
    with pytest.raises(
        ProseError, match=r"lore\.acme\.md: section 'lore' is not one the registry"
    ):
        parse_name("lore.acme.md")
    with pytest.raises(ProseError, match="the sections are identity, voice, standards"):
        parse_name("lore.acme.md")
    # A third part spells an audience; any other part beside the topic
    # refuses, a kind's name among them.
    for filename in ("rules.python.wheels.md", "rules.python.wheels.human.md"):
        with pytest.raises(
            ProseError,
            match=rf"{filename}: a fragment is named <section>\.<topic>\[",
        ):
            parse_name(filename)
    assert parse_name("rules.house.agent.md") == ("rules", "house", "agent")
    assert parse_name("rules.house.md") == ("rules", "house", "")


def test_two_fragments_of_one_name_refuse_naming_both_files(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    first = shipped(
        "acme.brand", _extension(tmp_path, "acme.brand", rules__house="# A\n")
    )
    second = shipped(
        "acme.house", _extension(tmp_path, "acme.house", rules__house="# B\n")
    )
    with pytest.raises(
        ProseError, match=r"two fragments deliver as rules\.house\.md"
    ) as caught:
        fragments(root, first + second, AGENT)
    assert "extensions/acme.brand/content/fragments/rules.house.md (acme.brand)" in str(
        caught.value
    )
    assert "extensions/acme.house/content/fragments/rules.house.md (acme.house)" in str(
        caught.value
    )
    # The repository's own fragment collides the same way, in the set.
    own = root / "fragments" / "rules.workshop.md"
    own.parent.mkdir()
    own.write_text("# Mine\n")
    with pytest.raises(ProseError, match=r"rules\.workshop\.md: .*\(this repository\)"):
        guidance(root, AGENT)


def test_a_fragment_for_both_readers_beside_its_twin_refuses_for_that_reader(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    both_and_agent = shipped(
        "acme.brand",
        _extension(
            tmp_path, "one", rules__house="# both\n", rules__house__agent="# agent\n"
        ),
    )
    with pytest.raises(ProseError, match=r"two fragments deliver as rules\.house\.md"):
        fragments(root, both_and_agent, AGENT)
    # The other reader's twin refuses for that reader.
    both_and_human = shipped(
        "acme.brand",
        _extension(
            tmp_path, "two", rules__house="# both\n", rules__house__human="# human\n"
        ),
    )
    with pytest.raises(ProseError, match=r"two fragments deliver as rules\.house\.md"):
        fragments(root, both_and_human, HUMAN)
    # One file per reader is the pair the convention exists for.
    pair = shipped(
        "acme.brand",
        _extension(
            tmp_path,
            "three",
            rules__house__agent="# agent\n",
            rules__house__human="# human\n",
        ),
    )
    chosen = fragments(root, pair, AGENT)
    shipped_names = [prose.name for prose in chosen if prose.source is not None]
    assert shipped_names == ["rules.house.md"]
    assert "gate.checks.md" in [prose.name for prose in chosen]
    (house,) = [prose for prose in chosen if prose.name == "rules.house.md"]
    assert house.source is not None and house.source.read_text() == "# agent\n"


def test_a_section_registered_twice_or_after_an_unknown_one_refuses(restored) -> None:
    register_section("brand", after="rules", extension="acme.brand")
    assert sections()[3:5] == ("rules", "brand")
    with pytest.raises(
        ProseError, match=r"registers section 'brand', which acme\.brand already"
    ):
        register_section("brand", after="rules", extension="acme.house")
    with pytest.raises(
        ProseError, match="after 'nope', which no extension registered; the sections"
    ):
        register_section("more", after="nope", extension="acme.brand")
    with pytest.raises(
        ProseError, match="withdraws section 'rules', which is the base's"
    ):
        unregister_section("rules", by="livery.workshop")
    with pytest.raises(
        ProseError, match=r"withdraws section 'brand', which acme\.brand registered"
    ):
        unregister_section("brand", by="acme.house")
    with pytest.raises(
        ProseError, match="withdraws section 'lore', which no extension registered"
    ):
        unregister_section("lore", by="acme.brand")
    register_fragment(
        "brand", "thing", lambda root, audience: "# T\n", extension="acme.brand"
    )
    with pytest.raises(ProseError, match=r"while brand\.thing\.md still belongs to it"):
        unregister_section("brand", by="acme.brand")
    unregister_fragment("brand.thing.md", by="acme.brand")
    unregister_section("brand", by="acme.brand")
    assert "brand" not in sections()


def test_a_rendered_fragment_outside_the_vocabulary_or_registered_twice_refuses(
    restored,
) -> None:
    def render(root: Path, audience: str | None) -> str:
        return "# R\n"

    register_fragment("rules", "acme", render, extension="acme.brand")
    with pytest.raises(
        ProseError, match=r"registers fragment rules\.acme\.md, which acme\.brand"
    ):
        register_fragment("rules", "acme", render, extension="acme.house")
    with pytest.raises(
        ProseError, match="section 'lore' is not one the registry knows"
    ):
        register_fragment("lore", "acme", render, extension="acme.brand")
    with pytest.raises(
        ProseError, match="a topic is never named 'agent', an audience is"
    ):
        register_fragment("rules", "agent", render, extension="acme.brand")
    with pytest.raises(
        ProseError, match=r"withdraws fragment rules\.acme\.md, which acme\.brand"
    ):
        unregister_fragment("rules.acme.md", by="acme.house")
    with pytest.raises(
        ProseError, match=r"withdraws fragment rules\.none\.md, which no extension"
    ):
        unregister_fragment("rules.none.md", by="acme.brand")
    unregister_fragment("rules.acme.md", by="acme.brand")


def test_each_reader_gets_its_audience_the_shared_fragments_and_the_section_order(
    tmp_path: Path, restored
) -> None:
    root = _workspace(tmp_path, "acme.brand")
    register_section("brand", after="rules", extension="acme.brand")
    listed = shipped("livery.workshop", WORKSHOP_CONTENT) + shipped(
        "acme.brand",
        _extension(
            tmp_path,
            "acme.brand",
            voice__tone__agent="# terse\n",
            voice__tone__human="# warm\n",
            rules__house="# house\n",
            identity__acme="# acme\n",
            brand__thing="# thing\n",
        ),
    )
    # Sections in the base's order, the registered one after its anchor,
    # the mounted extensions in mount order inside a section.
    assert _names(root, listed, AGENT) == [
        "identity.acme.md",
        "voice.interaction.md",
        "voice.tone.md",
        "standards.documentation.md",
        "rules.workshop.md",
        "rules.house.md",
        "brand.thing.md",
    ]
    assert _names(root, listed, HUMAN) == _names(root, listed, AGENT)
    tone = {p.audience: p for p in fragments(root, listed, HUMAN) if p.topic == "tone"}
    assert list(tone) == [HUMAN]
    tone = {p.audience: p for p in fragments(root, listed, AGENT) if p.topic == "tone"}
    assert list(tone) == [AGENT]
    # No particular reader: only what serves both.
    assert "voice.tone.md" not in _names(root, listed, None)


def _in_play(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **brand: str) -> Path:
    """A workspace stacking the base and acme.brand, with its own fragments."""
    root = _workspace(tmp_path, "acme.brand")
    contents = {
        "livery.workshop": WORKSHOP_CONTENT,
        "acme.brand": _extension(tmp_path, "acme.brand", **brand),
    }
    monkeypatch.setattr(
        "livery.workshop._extensions.stack_names",
        lambda root: ("livery.workshop", "acme.brand"),
    )
    monkeypatch.setattr("livery.workshop._extensions.extension_content", contents.get)
    return root


def test_guidance_refuses_two_fragments_in_play_of_one_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restored
) -> None:
    from livery.workshop import HUMAN, guidance

    root = _in_play(tmp_path, monkeypatch, rules__house="# theirs\n")
    (root / "fragments").mkdir()
    (root / "fragments" / "rules.house.md").write_text("# ours\n")
    with pytest.raises(ValueError, match=r"two fragments deliver as rules\.house\.md"):
        guidance(root, HUMAN)


def test_guidance_is_one_readers_set_of_every_fragment_in_play(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restored
) -> None:
    from livery.workshop import AGENT, HUMAN, Prose, guidance

    root = _in_play(
        tmp_path,
        monkeypatch,
        voice__tone__agent="# terse\n",
        voice__tone__human="# warm\n",
    )
    (root / "fragments").mkdir()
    (root / "fragments" / "rules.own.md").write_text("# ours\n")
    human = guidance(root, HUMAN)
    assert all(isinstance(prose, Prose) for prose in human)
    tone = [p for p in human if p.topic == "tone"]
    assert [p.text(root, HUMAN) for p in tone] == ["# warm\n"]
    # The repository's own comes last in its section, after the base's.
    rules = [p.name for p in human if p.section == "rules" and p.source is not None]
    assert rules == ["rules.workshop.md", "rules.own.md"]
    agent = guidance(root, AGENT)
    assert [p.text(root, AGENT) for p in agent if p.topic == "tone"] == ["# terse\n"]
    # The same set the fragments function gives over what is in play.
    assert human == fragments(root, _prose.in_play(root), HUMAN)


def test_the_gate_fragment_renders_the_steps_for_the_extensions_held_and_the_reader(
    tmp_path: Path, restored, python_steps: object
) -> None:
    root = _workspace(tmp_path)
    register_step(
        StepRecord(
            "acme-native", "lint", _noop, extensions=("cpp",), extension="acme.test"
        )
    )
    # A step of a phase the gate does not run is never among them.
    register_step(StepRecord("acme-tree", "configure", _noop, extension="acme.test"))
    assert "acme-tree" not in render_gate(root, AGENT)
    assert "acme-tree" not in render_gate(root, HUMAN)
    # Without a package only the workspace's own steps are in the gate:
    # neither the test extension's nor the python formatter, which judges
    # no package present.
    agent = render_gate(root, AGENT)
    assert "acme-native" not in agent and "format" not in agent
    assert "- layering.graph: judges the workspace; rewrites under --fix" in agent
    assert "the members hold the package-level extensions none." in agent
    _member(root, "cpp", "cmake", "conan")
    agent = render_gate(root, AGENT)
    assert "- lint.acme-native: judges cpp packages" in agent
    assert (
        "- format.fake: judges python or conan packages; rewrites under --fix" in agent
    )
    assert "typecheck" not in agent
    assert "the members hold the package-level extensions cmake, conan, cpp." in agent
    _member(root, "py", "python")
    agent = render_gate(root, AGENT)
    for tool in ("fake",):
        assert f"- typecheck.{tool}: judges python packages" in agent
    assert (
        "the members hold the package-level extensions cmake, conan, cpp, python."
        in agent
    )
    human = render_gate(root, HUMAN)
    assert human != agent
    assert "| lint.acme-native | none | cpp | no |" in human
    assert "| format.fake | fake | python or conan | yes |" in human


def test_the_verbs_fragment_reads_the_composed_tree_or_stays_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    # No tasks file: nothing to list, the fragment stays out.
    assert render_verbs(root, AGENT) == ""
    tree = {
        "tasks": {
            "check": {"mounted_from": "livery.workshop"},
            "deploy": {},
            "secret": {"hidden": True, "mounted_from": "livery.workshop"},
        },
        "groups": {
            "forge": {
                "mounted_from": "acme.brand",
                "tasks": {"up": {}, "down": {}, "default": {}},
            },
            "hid": {"hidden": True, "tasks": {"x": {}}},
        },
    }
    monkeypatch.setattr(_prose, "composed_tree", lambda root: tree)
    agent = render_verbs(root, AGENT)
    lines = [line for line in agent.splitlines() if line.startswith("- ")]
    # The mounted extension first, then a provider outside the mount, the
    # repository's own last; a hidden task or group is not a verb.
    assert lines == [
        "- livery.workshop: check",
        "- acme.brand: forge, forge.down, forge.up",
        "- this repository: deploy",
    ]
    assert "secret" not in agent and "hid.x" not in agent
    assert render_verbs(root, HUMAN) != agent


def test_the_compositions_and_tools_fragments_render_what_is_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cmake_steps: object
) -> None:
    root = _workspace(tmp_path)
    assert render_compositions(root, AGENT) == ""
    # A package holding no package-level extension, one that lists none,
    # is listed under none.
    _member(root, "odd")
    assert "- none (packages/odd): holds none; gate phases none\n" in (
        render_compositions(root, AGENT)
    )
    _member(root, "cpp", "cmake", "conan")
    _member(root, "nb", "nanobind")
    agent = render_compositions(root, AGENT)
    assert (
        "- cmake+conan (packages/cpp): holds cmake, cpp, conan;"
        " gate phases build, test" in agent
    )
    assert (
        "- nanobind (packages/nb): holds cpp, conan, python, nanobind; gate phases"
        in agent
    )
    assert (
        "| nanobind | cpp, conan, python, nanobind | packages/nb |"
        in render_compositions(root, HUMAN)
    )
    # A step of a phase the gate never runs is no gate phase of a set,
    # whatever extensions it names.
    register_step(
        StepRecord(
            "pages",
            "docs",
            _noop,
            scope="package",
            extensions=("cpp",),
            extension="acme.pages",
        )
    )
    assert (
        "- cmake+conan (packages/cpp): holds cmake, cpp, conan;"
        " gate phases build, test\n" in render_compositions(root, AGENT)
    )
    assert render_tools(root, AGENT) == ""
    lock = SimpleNamespace(
        hosts=("macos-arm", "linux-x64", "windows-x64"),
        tools={
            "ruff": SimpleNamespace(version="0.6.0", on=()),
            "uv": SimpleNamespace(version="0.4.0", on=()),
            "dotnet_coverage": SimpleNamespace(version="18.11.2", on=("windows-x64",)),
        },
    )
    monkeypatch.setattr(_tools, "current_lock", lambda root: lock)
    monkeypatch.setattr(
        _tools,
        "requirements",
        lambda root: (
            SimpleNamespace(name="ruff", site="step format"),
            SimpleNamespace(name="ruff", site="step lint"),
        ),
    )
    agent = render_tools(root, AGENT)
    assert "pinned by `toolroom.lock` for macos-arm, linux-x64, windows-x64" in agent
    assert "- ruff 0.6.0: step format, step lint\n- uv 0.4.0\n" in agent
    # A tool locked for some hosts alone says so.
    assert "- dotnet_coverage 18.11.2 (windows-x64 only)\n" in agent
    assert render_tools(root, HUMAN) != agent


def test_the_tree_is_listed_once_until_the_tasks_file_or_the_contract_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A listing is a runner process; a sync renders the verbs fragment
    # for two readers on each pass. One listing serves the process.
    import json
    import os
    import shutil

    import livery.footman as footman

    root = _workspace(tmp_path)
    (root / "tasks.py").write_text("")
    runs: list[list[str]] = []

    class _Done:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout

        def __int__(self) -> int:
            return 0

    def running(argv: list[str], **kwargs: object) -> _Done:
        runs.append(argv)
        return _Done(json.dumps({"tree": {"tasks": {"check": {}}, "groups": {}}}))

    monkeypatch.setattr(shutil, "which", lambda name: "/x/" + name)
    monkeypatch.setattr(footman, "run", running)
    _prose.forget_trees()
    first = _prose.composed_tree(root)
    assert first == {"tasks": {"check": {}}, "groups": {}}
    assert _prose.composed_tree(root) is first
    assert len(runs) == 1
    stamp = (root / "tasks.py").stat().st_mtime_ns + 2_000_000_000
    os.utime(root / "tasks.py", ns=(stamp, stamp))
    assert _prose.composed_tree(root) == first
    assert len(runs) == 2
    os.utime(root / "workshop.toml", ns=(stamp, stamp))
    _prose.composed_tree(root)
    assert len(runs) == 3
    _prose.forget_trees()
    _prose.composed_tree(root)
    assert len(runs) == 4


def test_the_tree_is_read_in_process_for_this_process_s_own_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    import shutil

    import livery.footman as footman

    root = _workspace(tmp_path)
    (root / "tasks.py").write_text("")
    runs: list[list[str]] = []

    class _Done:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout

        def __int__(self) -> int:
            return 0

    def running(argv: list[str], **kwargs: object) -> _Done:
        runs.append(argv)
        return _Done(json.dumps({"tree": {"tasks": {"check": {}}, "groups": {}}}))

    monkeypatch.setattr(shutil, "which", lambda name: "/x/" + name)
    monkeypatch.setattr(footman, "run", running)
    monkeypatch.setattr(
        footman, "task_tree", lambda: {"tasks": {"own": {}}, "groups": {}}
    )
    # Another root first: its tree lives in its own venv's entry points,
    # which only a process in that venv can list, so the runner runs there.
    monkeypatch.setattr(_prose, "_own_root", lambda: tmp_path / "elsewhere")
    _prose.forget_trees()
    assert _prose.composed_tree(root) == {"tasks": {"check": {}}, "groups": {}}
    assert len(runs) == 1
    # This process's own root: the tree it discovered already, no run.
    monkeypatch.setattr(_prose, "_own_root", lambda: root)
    _prose.forget_trees()
    assert _prose.composed_tree(root) == {"tasks": {"own": {}}, "groups": {}}
    assert len(runs) == 1
