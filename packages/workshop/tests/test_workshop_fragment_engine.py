"""The fragment engine: composition, refusals, regions, receipts and withdrawal."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from livery.footman import Failed
from livery.workshop import _fragment_engine as engine
from livery.workshop._fragment_engine import Fragment

ORDER = ("livery.workshop", "docs", "cpp", "repository")


def _plan(
    root: Path, *fragments: Fragment, **kwargs: object
) -> tuple[engine.Output, ...]:
    data = kwargs.pop("data", {"name": "acme"})
    return engine.plan(root, fragments, ORDER, data, **kwargs)  # type: ignore[arg-type]


# --- the refusals ---------------------------------------------------------------


def test_an_undeclared_clash_refuses_naming_both_owners(tmp_path: Path) -> None:
    base = Fragment("livery.workshop", "css", "docs/site.css", "a {}\n")
    docs = Fragment("docs", "css", "docs/site.css", "b {}\n")
    with pytest.raises(Failed) as refused:
        _plan(tmp_path, base, docs)
    assert str(refused.value) == (
        "docs/site.css has one owner, and livery.workshop:css and docs:css both"
        " render it; declare `replaces` or `deletes` with a reason on the later one"
    )
    # Declared, the upper one takes the file.
    docs = Fragment(
        "docs",
        "css",
        "docs/site.css",
        "b {}\n",
        replaces="livery.workshop:css",
        reason="the site's own palette",
    )
    (out,) = _plan(tmp_path, base, docs)
    assert out.body == b"b {}\n" and out.owners == ("docs:css",)
    # A JSON key two owners set to different values clashes the same way.
    one = Fragment("livery.workshop", "s", ".vscode/settings.json", '{"a": {"b": 1}}')
    two = Fragment("docs", "s", ".vscode/settings.json", '{"a": {"b": 2}}')
    with pytest.raises(
        Failed, match=r"a\.b is set by both livery.workshop:s and docs:s"
    ):
        _plan(tmp_path, one, two)
    # Tables that would define one table twice do not compose.
    one = Fragment("livery.workshop", "t", "pyproject.toml", "[tool.x]\na = 1\n")
    two = Fragment("docs", "t", "pyproject.toml", "[tool.x]\nb = 2\n")
    with pytest.raises(Failed, match=r"the tables of livery.workshop:t, docs:t do not"):
        _plan(tmp_path, one, two)


def test_a_replace_of_a_missing_fragment_refuses(tmp_path: Path) -> None:
    upper = Fragment(
        "docs", "css", "site.css", "b\n", replaces="livery.workshop:css", reason="x"
    )
    with pytest.raises(Failed) as refused:
        _plan(tmp_path, upper)
    assert str(refused.value) == (
        "fragment docs:css replaces livery.workshop:css, which no listed"
        " extension ships"
    )
    gone = Fragment(
        "docs", "drop", "site.css", deletes="livery.workshop:css", reason="x"
    )
    with pytest.raises(
        Failed, match=r"docs:drop deletes livery.workshop:css, which no"
    ):
        _plan(tmp_path, gone)
    # A declaration without its reason, one reaching upward, and an
    # owner that is not listed refuse too.
    base = Fragment("livery.workshop", "css", "site.css", "a\n")
    bare = Fragment("docs", "css", "site.css", "b\n", replaces="livery.workshop:css")
    with pytest.raises(Failed, match=r"replaces livery.workshop:css without a reason"):
        _plan(tmp_path, base, bare)
    upward = Fragment(
        "livery.workshop", "x", "site.css", "a\n", deletes="docs:css", reason="no"
    )
    with pytest.raises(Failed, match=r"only a later extension deletes an earlier"):
        _plan(tmp_path, upward, Fragment("docs", "css", "other.css", "b\n"))
    with pytest.raises(Failed, match=r"rust is not a listed extension"):
        _plan(tmp_path, Fragment("rust", "x", "a.txt", "a\n"))
    with pytest.raises(Failed, match=r"docs:css is shipped twice"):
        _plan(
            tmp_path, Fragment("docs", "css", "a", ""), Fragment("docs", "css", "b", "")
        )


def test_a_template_that_does_not_render_refuses_naming_the_fragment(
    tmp_path: Path,
) -> None:
    with pytest.raises(Failed) as refused:
        _plan(tmp_path, Fragment("docs", "t", "a.txt", "{{ missing.value }}\n"))
    assert str(refused.value) == "fragment docs:t, line 1: undefined value"
    with pytest.raises(Failed, match=r"fragment docs:u, line 1: syntax error"):
        _plan(tmp_path, Fragment("docs", "u", "b.txt", "{% if %}\n"))


def test_a_withdrawn_extensions_edited_file_is_kept_and_named(tmp_path: Path) -> None:
    kept = Fragment("docs", "kept", "kept.txt", "{{ name }}\n")
    edited = Fragment("docs", "edited", "edited.txt", "edited\n")
    plain = Fragment("docs", "plain", "plain.txt", "plain\n")
    lines = engine.apply(tmp_path, _plan(tmp_path, kept, edited, plain))
    assert lines == ["  wrote edited.txt", "  wrote kept.txt", "  wrote plain.txt"]
    assert (tmp_path / "kept.txt").read_text() == "acme\n"
    (tmp_path / "edited.txt").write_text("mine\n")
    # docs is withdrawn but for one fragment: the unedited file goes, the
    # edited one stays as the repository's, and both receipts go.
    lines = engine.apply(tmp_path, _plan(tmp_path, kept))
    assert lines == [
        "  kept edited.txt: no listed extension renders it, and it was edited here,"
        " so it stays as the repository's own",
        "  removed plain.txt: no listed extension renders it",
    ]
    assert (tmp_path / "edited.txt").read_text() == "mine\n"
    assert not (tmp_path / "plain.txt").exists()
    assert engine.read_rendered(tmp_path) == {
        "kept.txt": engine._digest(b"acme\n")  # pyright: ignore[reportPrivateUsage]
    }
    # Nothing left: the receipt file goes with its last entry.
    engine.apply(tmp_path, ())
    assert not (tmp_path / engine.RENDERED_MANIFEST).exists()
    assert not (tmp_path / "kept.txt").exists()


def test_an_edited_file_is_kept_and_an_unedited_one_follows_the_render(
    tmp_path: Path,
) -> None:
    fragment = Fragment("docs", "a", "a.txt", "{{ name }}\n")
    engine.apply(tmp_path, _plan(tmp_path, fragment))
    # Unedited: a new render rewrites it.
    lines = engine.apply(tmp_path, _plan(tmp_path, fragment, data={"name": "beta"}))
    assert lines == ["  updated a.txt"]
    # Edited: kept and named, and the receipt still says what was written.
    (tmp_path / "a.txt").write_text("mine\n")
    lines = engine.apply(tmp_path, _plan(tmp_path, fragment, data={"name": "gamma"}))
    assert lines == [
        "  kept a.txt: edited here, so it is not rewritten; delete it to take the"
        " rendered file"
    ]
    assert (tmp_path / "a.txt").read_text() == "mine\n"
    # A file the engine never wrote: equal is adopted, different is kept.
    (tmp_path / "b.txt").write_text("b\n")
    (tmp_path / "c.txt").write_text("theirs\n")
    adopt = Fragment("docs", "b", "b.txt", "b\n")
    other = Fragment("docs", "c", "c.txt", "c\n")
    lines = engine.apply(tmp_path, _plan(tmp_path, adopt, other))
    assert lines[-1].startswith("  kept c.txt: edited here")
    assert "b.txt" in engine.read_rendered(tmp_path)
    assert "c.txt" not in engine.read_rendered(tmp_path)


def test_a_crlf_checkout_of_a_rendered_file_is_the_same_file(tmp_path: Path) -> None:
    """A Windows checkout's line endings are neither an edit nor drift."""
    fragment = Fragment("docs", "a", "a.txt", "one\ntwo\n")
    outputs = _plan(tmp_path, fragment)
    engine.apply(tmp_path, outputs)
    (tmp_path / "a.txt").write_bytes(b"one\r\ntwo\r\n")
    assert engine.drift(tmp_path, outputs) == []
    assert engine.apply(tmp_path, outputs) == []
    # Unrendered, it is still unedited, so it goes.
    assert engine.apply(tmp_path, ()) == [
        "  removed a.txt: no listed extension renders it"
    ]


# --- composition ----------------------------------------------------------------


def test_a_template_that_reads_contributions_renders_when_none_came(
    tmp_path: Path,
) -> None:
    # The template carries comments, so composing it as a plain JSON part
    # would read them as broken JSON: it renders from an empty merge.
    template = '{\n  // the contributions\n{{ contributed_entries }}  "own": 1\n}\n'
    (output,) = _plan(
        tmp_path,
        Fragment("livery.workshop", "s", ".vscode/settings.json", template),
    )
    assert output.body == b'{\n  // the contributions\n  "own": 1\n}\n'


def test_each_target_type_composes_its_fragments_in_extension_order(
    tmp_path: Path,
) -> None:
    outputs = _plan(
        tmp_path,
        Fragment("docs", "t", "pyproject.toml", "[tool.docs]\nx = 1\n"),
        Fragment(
            "livery.workshop", "t", "pyproject.toml", "[project]\nname = '{{ name }}'\n"
        ),
        Fragment("repository", "i", ".gitignore", "# ours\n/build/\n"),
        Fragment("livery.workshop", "i", ".gitignore", "# base\n/build/\n.venv/\n"),
        Fragment(
            "cpp", "s", ".vscode/settings.json", '{"files": {"b": 2}, "x": [1, 2]}'
        ),
        Fragment(
            "livery.workshop",
            "s",
            ".vscode/settings.json",
            '{"files": {"a": 1}, "x": [1]}',
        ),
    )
    by_path = {output.path: output for output in outputs}
    assert by_path["pyproject.toml"].body == (
        b"[project]\nname = 'acme'\n\n[tool.docs]\nx = 1\n"
    )
    assert by_path["pyproject.toml"].owners == ("livery.workshop:t", "docs:t")
    assert by_path[".gitignore"].body == b"# base\n/build/\n.venv/\n# ours\n"
    assert json.loads(by_path[".vscode/settings.json"].body) == {
        "files": {"a": 1, "b": 2},
        "x": [1, 2],
    }


def test_a_package_target_renders_in_every_package_of_its_owner(tmp_path: Path) -> None:
    presets = Fragment(
        "cpp", "presets", "package/CMakePresets.json", '{"p": "{{ package }}"}'
    )
    outputs = _plan(
        tmp_path,
        presets,
        packages={
            "packages/b": ("cpp",),
            "packages/a": ("cpp", "docs"),
            "packages/c": (),
        },
    )
    assert [(o.path, json.loads(o.body)) for o in outputs] == [
        ("packages/a/CMakePresets.json", {"p": "packages/a"}),
        ("packages/b/CMakePresets.json", {"p": "packages/b"}),
    ]
    # Each package keeps its own receipts; the root keeps none.
    engine.apply(tmp_path, outputs, packages=("packages/a", "packages/b"))
    assert set(engine.read_rendered(tmp_path / "packages/a")) == {"CMakePresets.json"}
    assert not (tmp_path / engine.RENDERED_MANIFEST).exists()


def test_a_dynamic_fragment_returns_template_source_and_each_render_is_made_once(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def dynamic(data: object) -> str:
        calls.append("called")
        return "{{ name }} has {{ count }}\n"

    fragment = Fragment("docs", "d", "d.txt", dynamic=dynamic)
    engine._CACHE.clear()  # pyright: ignore[reportPrivateUsage]
    (out,) = _plan(tmp_path, fragment, data={"name": "acme", "count": 2})
    assert out.body == b"acme has 2\n"
    _plan(tmp_path, fragment, data={"name": "acme", "count": 2})
    _plan(tmp_path, fragment, data={"name": "acme", "count": 3})
    # The source is asked for every plan; a render is made once per
    # content and data.
    assert len(calls) == 3
    assert len(engine._CACHE) == 2  # pyright: ignore[reportPrivateUsage]
    # A file in the wheel renders the same way as a string.
    source = tmp_path / "content.txt"
    source.write_text("{{ name }}\n")
    (out,) = _plan(tmp_path, Fragment("docs", "f", "f.txt", source=source))
    assert out.body == b"acme\n"


def test_the_committed_regions_are_written_back_in_place(tmp_path: Path) -> None:
    template = (
        "[project]\nname = '{{ name }}'\n"
        "# -- workshop: region tables, yours to edit; the render keeps it --\n"
        "# -- workshop: end tables --\n"
        "[tool.after]\nx = 1\n"
    )
    fragment = Fragment("livery.workshop", "p", "pyproject.toml", template)
    (out,) = _plan(tmp_path, fragment)
    engine.apply(tmp_path, (out,))
    committed = (tmp_path / "pyproject.toml").read_text()
    edited = committed.replace(
        "yours to edit; the render keeps it --\n",
        "yours to edit; the render keeps it --\n[tool.mine]\ny = 2\n",
    )
    (tmp_path / "pyproject.toml").write_text(edited)
    (again,) = _plan(tmp_path, fragment, data={"name": "beta"})
    assert again.body.decode() == edited.replace("'acme'", "'beta'")
    # The region is the repository's, so the render matches the file
    # apart from what the render owns.
    assert engine.drift(tmp_path, _plan(tmp_path, fragment)) == []
    assert engine.drift(tmp_path, (again,)) == [
        "  pyproject.toml: differs from what livery.workshop:p render"
    ]


# --- receipts that two sides changed ---------------------------------------------


def test_receipts_merge_key_by_key_and_a_file_both_changed_reads_as_it_stands(
    tmp_path: Path,
) -> None:
    base = {"a.toml": "a0", "b.toml": "b0", "c.toml": "c0", "gone.toml": "g0"}
    ours = {"a.toml": "a1", "b.toml": "b1", "c.toml": "c0", "new.toml": "n1"}
    theirs = {"a.toml": "a0", "b.toml": "b2", "c.toml": "c2", "gone.toml": "g0"}
    # b.toml changed on both sides: its receipt is the digest of the file
    # the merge left, regions left out, so the next render rewrites it.
    (tmp_path / "b.toml").write_text("merged\n")
    merged = engine.merge_receipts(base, ours, theirs, beside=tmp_path)
    assert merged == {
        "a.toml": "a1",  # ours alone changed it
        "b.toml": engine.receipt_of(tmp_path / "b.toml"),
        "c.toml": "c2",  # theirs alone changed it
        "new.toml": "n1",  # ours alone added it
    }  # gone.toml: ours removed it, theirs left it alone
    # A file both changed that the merge left absent takes its receipt with it.
    (tmp_path / "b.toml").unlink()
    assert "b.toml" not in engine.merge_receipts(base, ours, theirs, beside=tmp_path)
    assert engine.receipt_of(tmp_path / "b.toml") is None
