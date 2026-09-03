"""The header machinery: placement, refresh, and the content lint."""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.workshop._provenance import (
    comment_style,
    content_header,
    content_lint,
    format_header,
    has_header,
    inject,
    strip_header,
)


def test_a_shebang_keeps_the_first_line(tmp_path: Path) -> None:
    script = tmp_path / "hook.sh"
    script.write_text("#!/usr/bin/env bash\necho hi\n")
    header = content_header("livery.workshop", "#")
    inject(script, header)
    lines = script.read_text().splitlines()
    assert lines[0] == "#!/usr/bin/env bash"
    assert lines[1].startswith("# Shipped as livery.workshop")
    assert has_header(script, header)


def test_frontmatter_keeps_its_place(tmp_path: Path) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\nname: thing\n---\n# Thing\n")
    header = content_header("livery.workshop", "html")
    inject(skill, header)
    text = skill.read_text()
    assert text.startswith("---\nname: thing\n---\n<!-- Shipped as")
    assert has_header(skill, header)


def test_a_stale_header_is_replaced_not_stacked(tmp_path: Path) -> None:
    target = tmp_path / "notes.sh"
    target.write_text("echo hi\n")
    inject(target, content_header("old.layer", "#"))
    inject(target, content_header("new.layer", "#"))
    text = target.read_text()
    assert "old.layer" not in text
    assert text.count("layer content") == 1
    assert text.endswith("echo hi\n")


def test_a_human_comment_is_never_stripped(tmp_path: Path) -> None:
    target = tmp_path / "config.toml"
    target.write_text("# my own note\nkey = 1\n")
    assert strip_header(target.read_text(), target) == "# my own note\nkey = 1\n"


def test_comment_hostile_types_have_no_style() -> None:
    assert comment_style(Path("settings.json")) == ""
    assert comment_style(Path("LICENSE")) == ""
    assert comment_style(Path("py.typed")) == ""
    assert comment_style(Path(".gitignore")) == "#"
    assert comment_style(Path("CODEOWNERS")) == "#"


def test_the_content_lint_is_red_then_fix_writes(tmp_path: Path) -> None:
    content = tmp_path / "packages" / "brand" / "src" / "brand" / "layer" / "content"
    (content / "fragments").mkdir(parents=True)
    (content / "fragments" / "rules.md").write_text("# Rules\n")
    (content / "settings.json").write_text("{}\n")  # comment-hostile: exempt
    findings = content_lint(tmp_path)
    assert findings == [
        "packages/brand/src/brand/layer/content/fragments/rules.md: missing its header"
    ]
    fixed = content_lint(tmp_path, fix=True)
    assert any("header written" in line for line in fixed)
    text = (content / "fragments" / "rules.md").read_text()
    assert text.startswith("<!-- Shipped as brand.layer layer content")
    assert content_lint(tmp_path) == []  # green, and idempotent


def test_format_header_shapes() -> None:
    lines = ("one", "two")
    assert format_header(lines, "#") == "# one\n# two\n"
    html = format_header(lines, "html")
    assert html.startswith("<!-- one\n") and html.endswith("-->\n")


def test_the_gate_arm_fails_without_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._provenance import provenance_check

    content = tmp_path / "packages" / "brand" / "src" / "brand" / "layer" / "content"
    content.mkdir(parents=True)
    (content / "rules.md").write_text("# Rules\n")
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nlayers = ["livery.workshop"]\n'
    )
    monkeypatch.setattr(
        "livery.workshop._provenance.workspace_root", lambda start=None: tmp_path
    )
    with pytest.raises(BaseException, match="provenance headers"):
        provenance_check()
    provenance_check(fix=True)
    provenance_check()  # green after the fix
