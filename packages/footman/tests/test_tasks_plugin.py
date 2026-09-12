"""The first-party `footman.docs` plugin: nested mounting, page/site
tasks, hot path."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from livery.footman import _app, _paths
from livery.footman._executor import TaskResult


@pytest.fixture
def plugin_project(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tasks.py").write_text(
        "from livery.footman import group, plugin, task\n"
        "# mounted FIRST on purpose: the local group() below adopts the\n"
        "# mounted docs group — order must not matter\n"
        "plugin('footman.docs')\n"
        "\n"
        "@task\n"
        "def greet(name: str = 'world'):\n"
        '    "Say hello."\n'
        "\n"
        "docs = group('docs', help='Documentation')\n"
        "\n"
        "@docs.task\n"
        "def serve(port: int = 8000):\n"
        '    "Serve the docs."\n',
        encoding="utf-8",  # the comment's em dash must survive Windows
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path / ".cache")
    return tmp_path


def test_bare_import_never_loads_first_party_tasks():
    # Hot-path guard: `import footman` must not import the plugin package.
    probe = "import livery.footman, sys; print('livery.footman.tasks' in sys.modules)"
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "False"


def test_plugin_mounts_under_the_prog(plugin_project, capsys):
    assert _app.run(["--list"]) == 0
    out = capsys.readouterr().out
    assert "docs.page" in out and "docs.site" in out


def test_page_prints_the_tree_to_stdout(plugin_project, capsys):
    assert _app.run(["docs.page"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# fm tasks\n")
    assert "## greet" in out and "### docs.serve" in out
    assert "footman" not in out.replace("# fm tasks", "")  # the documenter is absent


def test_page_all_includes_the_documenter(plugin_project, capsys):
    assert _app.run(["docs.page", "--all"]) == 0
    assert "docs.page" in capsys.readouterr().out


def test_page_refuses_a_bad_target_by_teaching_not_traceback(plugin_project, capsys):
    # The resolver's message already carried the menu; it just escaped as a
    # raw ValueError. A wrong --target is a deliberate refusal, delivered as
    # one — named flag, known names, no exception class in sight.
    assert _app.run(["docs.page", "--target=nope"]) == 1
    err = capsys.readouterr().err
    assert "--target" in err
    assert "no task or group named 'nope'" in err and "know:" in err
    assert "ValueError" not in err and "Traceback" not in err


def test_page_scoped_and_written_to_a_file(plugin_project, capsys):
    dest = plugin_project / "build" / "serve.md"
    line = ["docs.page", "--target=docs.serve", f"--out={dest}"]
    collected: list[TaskResult] = []
    assert _app.run(line, collect=collected) == 0
    text = dest.read_text()
    assert text.startswith("# docs.serve\n")
    assert "greet" not in text  # scoped away
    assert collected[0].returned == [str(dest)]
    assert "wrote" in capsys.readouterr().out  # task output (streams merge)


def test_page_unknown_target_is_a_task_failure(plugin_project, capsys):
    assert _app.run(["docs.page", "--target=nope"]) == 1
    assert "no task or group named 'nope'" in capsys.readouterr().err


def test_site_writes_indexes_and_pages(plugin_project, capsys):
    collected: list[TaskResult] = []
    assert _app.run(["docs.site", "pages"], collect=collected) == 0
    root = plugin_project / "pages"
    assert (root / "index.md").exists()
    assert (root / "greet.md").exists()
    assert (root / "docs" / "index.md").exists()
    assert (root / "docs" / "serve.md").exists()
    index = (root / "index.md").read_text()
    assert "[`greet`](greet.md)" in index and "[`docs`](docs/index.md)" in index
    assert "!!! example" in (root / "greet.md").read_text()  # material default
    returned = sorted(Path(p).resolve() for p in collected[0].returned)
    assert returned == sorted(p.resolve() for p in root.rglob("*.md"))


def test_branded_cli_documents_itself(plugin_project):
    # A branded CLI's pages carry its own name with no flag at all: the
    # invoking brand rides the task context, and --prog stays the override.
    from livery.footman import App
    from livery.footman.testing import Runner

    acme = Runner(App(name="Acme", prog="acme", version="1.0"))
    result = acme.invoke("docs.page")
    assert result.ok
    assert result.stdout.startswith("# acme tasks\n")
    assert "acme greet" in result.stdout
    overridden = acme.invoke("docs.page --prog=other")
    assert overridden.stdout.startswith("# other tasks\n")


def test_page_rides_the_json_envelope(plugin_project, capsys):
    assert _app.run(["--json", "docs.page"]) == 0
    payload = json.loads(capsys.readouterr().out)
    (entry,) = [i for i in payload["items"] if "task" in i]
    assert entry["task"] == "docs.page"
    assert "# fm tasks" in entry["output"]  # the markdown, captured


def test_globals_task_prints_the_grammar(plugin_project, capsys):
    assert _app.run(["docs.globals"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("| option")
    assert "`--json`" in out
    assert "help for fm" in out  # {prog} filled with the invoking CLI


def test_globals_task_writes_out(plugin_project, capsys):
    dest = plugin_project / "docs" / "_generated" / "globals.md"
    assert _app.run(["docs.globals", f"--out={dest}"]) == 0
    assert dest.read_text(encoding="utf-8").startswith("| option")


def test_no_docs_parameter_says_only_its_own_type():
    """`--out=PATH  a path` is the label said twice. With no `doc(...)` the
    help line falls back to the type word, which the metavar already
    carried — so the row costs a reader a glance and hands back nothing.
    Every parameter this plugin ships has to say more than its type."""
    from livery.footman import _manifest
    from livery.footman._describe import TYPE_WORD, listed_params, param_detail
    from livery.footman.tasks.docs import tasks

    words = set(TYPE_WORD.values())
    bare: list[str] = []

    def walk(node, path: list[str]) -> None:
        for name, task in node.get("tasks", {}).items():
            for p in listed_params(task):
                detail = param_detail(p)
                # Nothing but type words, unions included ("a path or text").
                if detail and all(bit in words for bit in detail.split(" or ")):
                    bare.append(f"{'.'.join([*path, name])} {p['name']}: {detail}")
        for name, child in node.get("groups", {}).items():
            walk(child, [*path, name])

    walk(_manifest.build_manifest(tasks)["tree"], [])
    assert bare == []


# --- docs shots: pty screenshots, and the @requires_dep dogfood ---------------


def test_shots_lists_unavailable_without_rich(plugin_project, capsys, monkeypatch):
    # The @requires_dep("rich") gate, dogfooded: with rich unimportable the task
    # lists with the taught reason and refuses to run — no ImportError ever.
    from livery.footman import registry

    real = registry._importable
    monkeypatch.setattr(
        registry, "_importable", lambda m: False if m == "rich" else real(m)
    )
    assert _app.run(["--list"]) == 0
    out = capsys.readouterr().out
    assert "docs.shots" in out
    # Substring, not the exact `(unavailable: requires rich)`: on Windows the
    # POSIX-pty gate also fails, so collect-all lists both reasons.
    assert "requires rich" in out
    assert _app.run(["docs.shots", "--out=x.svg"]) != 0
    assert "requires rich" in capsys.readouterr().err


def test_cell_style_translates_pytes_bright_colours():
    """pyte says "brightblack"; rich says "bright_black" and silently
    ignores what it cannot parse — so dim text rendered in the normal
    foreground, and fish's grey autosuggestion read as characters typed
    into the prompt. (`f77` on a Linux runner, where the Fortran `f77`
    command exists for fish to suggest.)"""
    from livery.footman.tasks.docs import _cell_style

    class Cell:
        bold = italics = underscore = reverse = False
        bg = "default"

        def __init__(self, fg):
            self.fg = fg

    assert _cell_style(Cell("brightblack")) == "bright_black"
    assert _cell_style(Cell("brightred")) == "bright_red"
    assert _cell_style(Cell("red")) == "red"  # plain names pass through
    assert _cell_style(Cell("87d7ff")) == "#87d7ff"  # pyte's bare hex gains its #
    assert _cell_style(Cell("default")) == ""


def test_reduce_frames_keeps_only_the_final_repaint():
    from livery.footman.tasks.docs import reduce_frames

    raw = (
        "→ lint    ruff check\r\x1b[Kok   lint    ruff check  (0.1s)\r\n"
        "\r\x1b[K[███░░░] 0.2s  1/2\r\x1b[K\x1b[32m✓\x1b[0m done\r\n"
        "plain\r\n"
    )
    assert reduce_frames(raw) == (
        "ok   lint    ruff check  (0.1s)\n\x1b[32m✓\x1b[0m done\nplain\n"
    )


def test_keystrokes_compiles_text_and_tokens():
    from livery.footman.tasks.docs import _SETTLE, keystrokes

    sends = keystrokes(("hi", "<TAB>", "<WAIT:500>", "<SETTLE>", "<ENTER>"))
    assert [s.data for s in sends] == [b"h", b"i", b"\t", b"", b"", b"\r"]
    assert sends[3].delay == 0.5  # <WAIT:500> is a half-second pause, no bytes
    assert sends[4].delay == _SETTLE  # <SETTLE> waits for output to quiet

    # A recording is a sequence of events, and `snap` marks where one ends:
    # the typed word "hi" is one frame, not two, and a pause is not a frame
    # at all — so the key that opened a menu stays captioned while it is up.
    assert [s.snap for s in sends] == [False, True, True, False, False, True]
    assert [s.caption for s in sends] == ["hi", "hi", "Tab", "", "", "Enter"]


def test_compose_animation_windows_and_shell():
    from livery.footman.tasks.docs import compose_animation

    svgs = ['<svg width="9">A</svg>', '<svg width="9">B</svg>']
    out = compose_animation(svgs, [0.0, 1.0], hold=1.0)
    assert out.startswith("<svg ") and 'width="9"' in out and out.endswith("</svg>")
    assert '<g class="cast-frame cf0">A</g>' in out
    assert '<g class="cast-frame cf1">B</g>' in out
    # visibility rides with opacity: a frame at opacity 0 still hit-tests and
    # still joins a text selection, so devtools and copy-paste both reported
    # on frames nobody could see.
    assert (
        "@keyframes cf0{0%{opacity:1;visibility:visible}"
        "50.000%{opacity:0;visibility:hidden}}" in out
    )
    assert "step-end infinite" in out
    # The frame boundaries are stated, not left to be re-derived: the docs'
    # player steps a reader through one keypress at a time, and the last
    # frame's keyframe carries no closing `opacity:0` (it holds until the
    # loop), so parsing them back out of the CSS lost it.
    assert 'data-cast-frames="0,1000"' in out
    assert 'data-cast-total="2000"' in out


def test_cast_lists_unavailable_without_pyte(plugin_project, capsys, monkeypatch):
    from livery.footman import registry

    real = registry._importable
    monkeypatch.setattr(
        registry, "_importable", lambda m: False if m == "pyte" else real(m)
    )
    assert _app.run(["--list"]) == 0
    # Substring (see the shots test): Windows adds the POSIX-pty reason too.
    assert "requires pyte" in capsys.readouterr().out


@pytest.mark.skipif(
    sys.platform == "win32" or importlib.util.find_spec("rich") is None,
    reason="needs a POSIX pty and rich",
)
def test_shots_renders_a_real_svg(plugin_project, capsys):
    dest = plugin_project / "shot.svg"
    code = _app.run(
        [
            "docs.shots",
            f"--out={dest}",
            "--width=60",
            "--",
            "--list",
        ]
    )
    assert code == 0
    svg = dest.read_text(encoding="utf-8")
    assert svg.startswith("<svg")
    assert "#ff5f57" in svg  # the macOS window chrome
    assert "greet" in svg  # the real listing, captured from the real CLI


def test_errors_page_extracts_the_taught_errors(tmp_path):
    from livery.footman.tasks.docs import errors

    out = tmp_path / "errors.md"
    errors(out=out)
    text = out.read_text(encoding="utf-8")
    # The marquee teachings are present, with runtime holes marked.
    assert "use dots" in text  # dotted's teaching error
    assert "footman.cwd()" in text  # the chdir guard
    assert "reads stdin" in text  # the stdin guard
    assert "scoped it to this task" in text  # the environ note
    assert "⟨" in text  # placeholders survive extraction
    assert text.count("**note**") >= 4  # the teach-once notes are listed too


def test_page_follows_the_sort_setting(plugin_project, capsys):
    # One setting for every listing: the generated pages sort too.
    (plugin_project / "pyproject.toml").write_text(
        "[project]\nname='x'\n[tool.footman]\nsort = true\n"
    )
    (plugin_project / "tasks.py").write_text(
        "from livery.footman import plugin, task\n"
        "plugin('footman.docs')\n"
        "@task\n"
        "def zebra(): ...\n"
        "@task\n"
        "def alpha(): ...\n",
        encoding="utf-8",
    )
    assert _app.run(["docs.page"]) == 0
    out = capsys.readouterr().out
    assert out.index("## alpha") < out.index("## zebra")
