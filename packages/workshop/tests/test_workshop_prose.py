"""Prose fragments: the refusals first, then each reader's set and the delivery."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import livery.workshop
from livery.workshop import _checks, _prose, _tools
from livery.workshop._checks import CheckRecord, GateContext, register_check
from livery.workshop._prose import (
    AGENT,
    HUMAN,
    ProseError,
    fragments,
    guidance,
    parse_name,
    register_fragment,
    register_section,
    render_gate,
    render_kinds,
    render_tools,
    render_verbs,
    sections,
    shipped,
    unregister_fragment,
    unregister_section,
)
from workshop_python_checks import python_checks_fixture  # noqa: F401

WORKSHOP_CONTENT = Path(livery.workshop.__file__).resolve().parent / "content"


@pytest.fixture
def restored():
    prose = _prose.snapshot()
    checks = _checks.snapshot()
    yield
    _prose.restore(prose)
    _checks.restore(checks)


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


def _member(root: Path, name: str, kind: str) -> None:
    member = root / "packages" / name
    member.mkdir(parents=True)
    (member / "workshop.toml").write_text(f'kind = "{kind}"\nname = "acme-{name}"\n')
    if kind != "cpp-conan":
        (member / "pyproject.toml").write_text(
            f'[project]\nname = "acme-{name}"\nversion = "0.1.0"\n'
        )


def _names(root: Path, listed: list[_prose.Prose], audience: str | None) -> list[str]:
    """The shipped names in a reader's set, the rendered ones left out."""
    return [p.name for p in fragments(root, listed, audience) if p.source is not None]


def _noop(ctx: GateContext) -> None:
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


def test_an_unknown_section_or_kind_refuses_naming_the_file() -> None:
    with pytest.raises(
        ProseError, match=r"lore\.acme\.md: section 'lore' is not one the registry"
    ):
        parse_name("lore.acme.md")
    with pytest.raises(ProseError, match="the sections are identity, voice, standards"):
        parse_name("lore.acme.md")
    with pytest.raises(
        ProseError, match="kind 'rust' is not a registered package kind"
    ):
        parse_name("rules.rust.cargo.md")
    # With three parts the last spells an audience, never a kind or a topic.
    assert parse_name("rules.house.agent.md") == ("rules", "", "house", "agent")
    assert parse_name("rules.python.wheels.human.md") == (
        "rules",
        "python",
        "wheels",
        "human",
    )
    assert parse_name("rules.python.wheels.md") == ("rules", "python", "wheels", "")


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
        ProseError, match="kind 'rust' is not a registered package kind"
    ):
        register_fragment("rules", "cargo", render, kind="rust", extension="acme.brand")
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


def test_a_kind_gated_fragment_is_delivered_only_while_the_kind_is_present(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    listed = shipped(
        "acme.brand",
        _extension(
            tmp_path,
            "acme.brand",
            **{
                "rules.cpp-conan.native.md": "# native\n",
                "rules.python.wheels.md": "# wheels\n",
                "rules.everyone.md": "# all\n",
            },
        ),
    )
    # The absent arm first: no package, no kind, the gated fragments stay out.
    assert _names(root, listed, AGENT) == ["rules.everyone.md"]
    _member(root, "cpp", "cpp-conan")
    assert _names(root, listed, AGENT) == [
        "rules.cpp-conan.native.md",
        "rules.everyone.md",
    ]
    # A derived kind counts as its parent: a nanobind package is a python one.
    _member(root, "nb", "python-nanobind")
    assert _names(root, listed, AGENT) == [
        "rules.cpp-conan.native.md",
        "rules.everyone.md",
        "rules.python.wheels.md",
    ]


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


def test_the_gate_fragment_renders_the_checks_for_the_kinds_present_and_the_reader(
    tmp_path: Path, restored, python_checks: object
) -> None:
    root = _workspace(tmp_path)
    register_check(
        CheckRecord(
            "acme-native", "lint", _noop, kinds=("cpp-conan",), extension="acme.test"
        )
    )
    # Without a package only the workspace's own checks are in the gate:
    # neither the test extension's nor the python formatter, which judges
    # no kind present.
    agent = render_gate(root, AGENT)
    assert "acme-native" not in agent and "format" not in agent
    assert "- layering.graph: judges the workspace; rewrites under --fix" in agent
    assert "the package kinds present are none." in agent
    _member(root, "cpp", "cpp-conan")
    agent = render_gate(root, AGENT)
    assert "- lint.acme-native: judges cpp-conan packages" in agent
    assert (
        "- format.fake: judges python, cpp-conan packages; rewrites under --fix"
        in agent
    )
    assert "typecheck" not in agent
    assert "the package kinds present are cpp-conan." in agent
    _member(root, "py", "python")
    agent = render_gate(root, AGENT)
    for tool in ("fake",):
        assert f"- typecheck.{tool}: judges python packages" in agent
    assert "the package kinds present are cpp-conan, python." in agent
    human = render_gate(root, HUMAN)
    assert human != agent
    assert "| lint.acme-native | none | cpp-conan | no |" in human
    assert "| format.fake | fake | python, cpp-conan | yes |" in human


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


def test_the_kinds_and_tools_fragments_render_what_is_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    assert render_kinds(root, AGENT) == ""
    _member(root, "cpp", "cpp-conan")
    _member(root, "nb", "python-nanobind")
    agent = render_kinds(root, AGENT)
    assert (
        "- cpp-conan (packages/cpp): derives from nothing;"
        " gate roles build, test" in agent
    )
    assert "- python-nanobind (packages/nb): derives from python; gate roles" in agent
    assert "| python-nanobind | python | packages/nb |" in render_kinds(root, HUMAN)
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
            SimpleNamespace(name="ruff", site="check format"),
            SimpleNamespace(name="ruff", site="check lint"),
        ),
    )
    agent = render_tools(root, AGENT)
    assert "pinned by `toolroom.lock` for macos-arm, linux-x64, windows-x64" in agent
    assert "- ruff 0.6.0: check format, check lint\n- uv 0.4.0\n" in agent
    # A tool locked for some hosts alone says so.
    assert "- dotnet_coverage 18.11.2 (windows-x64 only)\n" in agent
    assert render_tools(root, HUMAN) != agent
