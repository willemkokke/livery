"""Prose fragments: the refusals first, then each reader's set and the delivery."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import livery.workshop.api
from livery.workshop import _checks, _prose, _tools
from livery.workshop._checks import CheckRecord, GateContext, register_check
from livery.workshop._prose import (
    AGENT,
    HUMAN,
    ProseError,
    deliver,
    fragments,
    parse_name,
    register_fragment,
    register_section,
    render_gate,
    render_kinds,
    render_tools,
    render_verbs,
    repository_fragments,
    sections,
    shipped,
    unregister_fragment,
    unregister_section,
)
from livery.workshop._sync import sync_workspace

WORKSHOP_CONTENT = Path(livery.workshop.api.__file__).resolve().parent / "content"


@pytest.fixture
def restored():
    prose = _prose.snapshot()
    checks = _checks.snapshot()
    yield
    _prose.restore(prose)
    _checks.restore(checks)


def _workspace(tmp_path: Path, *layers: str) -> Path:
    listed = ", ".join(f'"{layer}"' for layer in ("livery.workshop", *layers))
    (tmp_path / "workshop.toml").write_text(f"[workspace]\nlayers = [{listed}]\n")
    return tmp_path


def _layer(tmp_path: Path, name: str, **files: str) -> Path:
    """A fake layer's content directory, its fragments written from *files*."""
    content = tmp_path / "layers" / name / "content"
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
    content = _layer(tmp_path, "acme.brand", **{"notes.md": "# Notes\n"})
    with pytest.raises(
        ProseError, match=r"layers/acme\.brand/content/fragments/notes\.md: a"
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
    first = shipped("acme.brand", _layer(tmp_path, "acme.brand", rules__house="# A\n"))
    second = shipped("acme.house", _layer(tmp_path, "acme.house", rules__house="# B\n"))
    with pytest.raises(
        ProseError, match=r"two fragments deliver as rules\.house\.md"
    ) as caught:
        fragments(root, first + second, AGENT)
    assert "layers/acme.brand/content/fragments/rules.house.md (acme.brand)" in str(
        caught.value
    )
    assert "layers/acme.house/content/fragments/rules.house.md (acme.house)" in str(
        caught.value
    )
    # The repository's own fragment collides the same way, at sync.
    own = root / "fragments" / "rules.workshop.md"
    own.parent.mkdir()
    own.write_text("# Mine\n")
    with pytest.raises(ProseError, match=r"rules\.workshop\.md: .*\(this repository\)"):
        sync_workspace(root)


def test_a_fragment_for_both_readers_beside_its_twin_refuses_at_delivery(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    both_and_agent = shipped(
        "acme.brand",
        _layer(
            tmp_path, "one", rules__house="# both\n", rules__house__agent="# agent\n"
        ),
    )
    with pytest.raises(ProseError, match=r"two fragments deliver as rules\.house\.md"):
        deliver(root, both_and_agent)
    # The reader's set is validated at the same delivery.
    both_and_human = shipped(
        "acme.brand",
        _layer(
            tmp_path, "two", rules__house="# both\n", rules__house__human="# human\n"
        ),
    )
    with pytest.raises(ProseError, match=r"two fragments deliver as rules\.house\.md"):
        deliver(root, both_and_human)
    # One file per reader is the pair the convention exists for.
    pair = shipped(
        "acme.brand",
        _layer(
            tmp_path,
            "three",
            rules__house__agent="# agent\n",
            rules__house__human="# human\n",
        ),
    )
    delivered = deliver(root, pair)
    assert delivered.delivered == ("rules.house.md", "gate.checks.md")
    assert (
        root / ".workshop" / "fragments" / "rules.house.md"
    ).read_text() == "# agent\n"


def test_a_section_registered_twice_or_after_an_unknown_one_refuses(restored) -> None:
    register_section("brand", after="rules", layer="acme.brand")
    assert sections()[3:5] == ("rules", "brand")
    with pytest.raises(
        ProseError, match=r"registers section 'brand', which acme\.brand already"
    ):
        register_section("brand", after="rules", layer="acme.house")
    with pytest.raises(
        ProseError, match="after 'nope', which no layer registered; the sections"
    ):
        register_section("more", after="nope", layer="acme.brand")
    with pytest.raises(
        ProseError, match="withdraws section 'rules', which is the base's"
    ):
        unregister_section("rules", by="livery.workshop")
    with pytest.raises(
        ProseError, match=r"withdraws section 'brand', which acme\.brand registered"
    ):
        unregister_section("brand", by="acme.house")
    with pytest.raises(
        ProseError, match="withdraws section 'lore', which no layer registered"
    ):
        unregister_section("lore", by="acme.brand")
    register_fragment(
        "brand", "thing", lambda root, audience: "# T\n", layer="acme.brand"
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

    register_fragment("rules", "acme", render, layer="acme.brand")
    with pytest.raises(
        ProseError, match=r"registers fragment rules\.acme\.md, which acme\.brand"
    ):
        register_fragment("rules", "acme", render, layer="acme.house")
    with pytest.raises(
        ProseError, match="section 'lore' is not one the registry knows"
    ):
        register_fragment("lore", "acme", render, layer="acme.brand")
    with pytest.raises(
        ProseError, match="kind 'rust' is not a registered package kind"
    ):
        register_fragment("rules", "cargo", render, kind="rust", layer="acme.brand")
    with pytest.raises(
        ProseError, match="a topic is never named 'agent', an audience is"
    ):
        register_fragment("rules", "agent", render, layer="acme.brand")
    with pytest.raises(
        ProseError, match=r"withdraws fragment rules\.acme\.md, which acme\.brand"
    ):
        unregister_fragment("rules.acme.md", by="acme.house")
    with pytest.raises(
        ProseError, match=r"withdraws fragment rules\.none\.md, which no layer"
    ):
        unregister_fragment("rules.none.md", by="acme.brand")
    unregister_fragment("rules.acme.md", by="acme.brand")


def test_a_kind_gated_fragment_is_delivered_only_while_the_kind_is_present(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    listed = shipped(
        "acme.brand",
        _layer(
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
    register_section("brand", after="rules", layer="acme.brand")
    listed = shipped("livery.workshop", WORKSHOP_CONTENT) + shipped(
        "acme.brand",
        _layer(
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
    # the mounted layers in mount order inside a section.
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


def test_the_gate_fragment_renders_the_checks_for_the_kinds_present_and_the_reader(
    tmp_path: Path, restored
) -> None:
    root = _workspace(tmp_path)
    register_check(
        CheckRecord(
            "acme-native", "lint", _noop, kinds=("cpp-conan",), layer="acme.test"
        )
    )
    # Without a package only the workspace's own checks are in the gate:
    # neither the test layer's nor ruff, which judges no kind present.
    agent = render_gate(root, AGENT)
    assert "acme-native" not in agent and "format" not in agent
    assert "- layering.graph: judges the workspace; rewrites under --fix" in agent
    assert "the package kinds present are none." in agent
    _member(root, "cpp", "cpp-conan")
    agent = render_gate(root, AGENT)
    assert "- lint.acme-native: judges cpp-conan packages" in agent
    assert (
        "- format.ruff: judges python, cpp-conan packages; rewrites under --fix"
        in agent
    )
    assert "typecheck" not in agent
    assert "the package kinds present are cpp-conan." in agent
    _member(root, "py", "python")
    agent = render_gate(root, AGENT)
    for tool in ("basedpyright", "mypy", "ty", "pyrefly"):
        assert f"- typecheck.{tool}: judges python packages" in agent
    assert "the package kinds present are cpp-conan, python." in agent
    human = render_gate(root, HUMAN)
    assert human != agent
    assert "| lint.acme-native | none | cpp-conan | no |" in human
    assert "| format.ruff | ruff | python, cpp-conan | yes |" in human


def test_a_shipped_fragment_lands_byte_for_byte_and_an_edit_is_kept_and_named(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    sync_workspace(root)
    delivered = root / ".workshop" / "fragments" / "voice.interaction.md"
    source = WORKSHOP_CONTENT / "fragments" / "voice.interaction.md"
    assert delivered.read_bytes() == source.read_bytes()
    assert sync_workspace(root) == []
    delivered.write_text("# My voice\n")
    lines = sync_workspace(root)
    assert any("voice.interaction.md: local override kept" in line for line in lines)
    assert delivered.read_text() == "# My voice\n"
    ignore = (delivered.parent / ".gitignore").read_text()
    assert "/voice.interaction.md\n" not in ignore
    assert "/standards.documentation.md\n" in ignore
    # Deleting the override takes the shipped copy again.
    delivered.unlink()
    lines = sync_workspace(root)
    assert any("voice.interaction.md: materialised" in line for line in lines)
    assert delivered.read_bytes() == source.read_bytes()


def test_a_rendered_fragment_lands_under_its_header_and_leaves_with_its_record(
    tmp_path: Path, restored
) -> None:
    root = _workspace(tmp_path)
    register_fragment(
        "rules",
        "acme",
        lambda root, audience: f"# Acme\n\nfor {audience}\n",
        layer="acme.brand",
    )
    register_fragment("rules", "quiet", lambda root, audience: "", layer="acme.brand")
    sync_workspace(root)
    delivered = root / ".workshop" / "fragments" / "rules.acme.md"
    text = delivered.read_text()
    assert text.startswith(
        "<!-- Rendered by `fm sync` from the registries acme.brand fills;"
    )
    assert text.endswith("# Acme\n\nfor agent\n")
    # An empty render is left out, not delivered empty.
    assert not (root / ".workshop" / "fragments" / "rules.quiet.md").exists()
    assert sync_workspace(root) == []
    unregister_fragment("rules.acme.md", by="acme.brand")
    unregister_fragment("rules.quiet.md", by="acme.brand")
    lines = sync_workspace(root)
    assert any("removed rules.acme.md (no layer ships it)" in line for line in lines)
    assert not delivered.exists()
    manifest = (delivered.parent / ".workshop-materialised").read_text()
    assert "rules.acme.md" not in manifest


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
    # The mounted layer first, then a provider outside the mount, the
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
        "- cpp-conan (packages/cpp): derives from nothing; gate roles format, lint"
        in agent
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
    assert "pinned by `tools.lock` for macos-arm, linux-x64, windows-x64" in agent
    assert "- ruff 0.6.0: check format, check lint\n- uv 0.4.0\n" in agent
    # A tool locked for some hosts alone says so.
    assert "- dotnet_coverage 18.11.2 (windows-x64 only)\n" in agent
    assert render_tools(root, HUMAN) != agent


def test_the_entry_file_imports_the_sections_in_order_then_the_repository_s_own(
    tmp_path: Path, restored
) -> None:
    root = _workspace(tmp_path)
    own = root / "fragments"
    own.mkdir()
    (own / "identity.acme.md").write_text("# Acme\n")
    register_fragment(
        "workflow", "acme", lambda root, audience: "# W\n", layer="acme.brand"
    )
    sync_workspace(root)
    imports = [
        line
        for line in (root / "CLAUDE.md").read_text().splitlines()
        if line.startswith("@")
    ]
    assert imports == [
        "@.workshop/fragments/voice.interaction.md",
        "@.workshop/fragments/standards.documentation.md",
        "@.workshop/fragments/rules.workshop.md",
        "@.workshop/fragments/workflow.acme.md",
        "@.workshop/fragments/gate.checks.md",
        "@fragments/identity.acme.md",
        "@CLAUDE.project.md",
    ]
    # The repository's own fragment is read where it is, never copied.
    assert not (root / ".workshop" / "fragments" / "identity.acme.md").exists()
    assert [p.name for p in repository_fragments(root)] == ["identity.acme.md"]
