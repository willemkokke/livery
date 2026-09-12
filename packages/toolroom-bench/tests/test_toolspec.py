"""Reading a tool's self-description, and rendering it as a stub.

The help fixtures below are real output, trimmed: one per family footman
has to read (clap, optparse, cobra, git, commander). They are checked in
rather than captured live so the parser is tested on every machine,
including the ones where docker isn't installed — the tasks that talk to
real binaries are exercised separately, against whatever is present.
"""

from __future__ import annotations

import ast
import os
import pathlib
import shutil
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from livery.footman import _globals
from livery.toolroom.bench import _drivers, _stubgen, _toolhelp, _toolspec
from livery.toolroom.bench._toolspec import Option, ToolSpec, Verb

CLAP = """\
Usage: ruff check [OPTIONS] [FILES]...

Run Ruff on the given files or directories

Arguments:
  [FILES]...
          List of files or directories to check [default: .]

Options:
      --fix
          Apply fixes to resolve lint violations. Use `--no-fix` to disable or
          `--unsafe-fixes` to include unsafe fixes

  -w, --watch
          Run in watch mode by re-running whenever files change

      --fix-only
          Apply fixes, but don't report on leftover violations. Use
          `--no-fix-only` to disable

      --color <WHEN>
          Control when colored output is used

          Possible values:
          - auto:   Display colors if the output goes to an interactive terminal
          - always: Always display colors
          - never:  Never display colors

      --line-length <LINE_LENGTH>
          Set the line-length [default: 88]

Rule selection:
      --select <RULE_CODE>
          Comma-separated list of rule codes to enable

  -h, --help
          Print help
"""

OPTPARSE = """\
Usage: coverage html [options] [modules]

Create an HTML report of coverage results.

Options:
  --contexts=REGEX1,REGEX2,...
                        Only display data from lines covered in the given
                        contexts. Accepts Python regexes, which must be quoted.
  -d DIR, --directory=DIR
                        Write the output files to DIR.
  --fail-under=MIN      Exit with a status of 2 if the total coverage is less
                        than MIN.
  -i, --ignore-errors   Ignore errors while reading source files.
  -h, --help            Get help on this command.
"""

COBRA = """\
Usage:  docker compose up [OPTIONS] [SERVICE...]

Create and start containers

Options:
      --build                  Build images before starting containers
      --no-build               Don't build an image, even if it's missing
  -d, --detach                 Detached mode: Run containers in the background
      --tail string            Number of lines to show (default "all")
      --scale stringArray      Scale SERVICE to NUM instances
      --remove-orphans         Remove containers for services not defined
"""

GIT = """\
usage: git commit [-a | --interactive | --patch] [-s] [-v]
                  [--amend] [--dry-run]

    -q, --[no-]quiet      suppress summary after successful commit
    -F, --[no-]file <file>
                          read message from file

Commit message options
    -m, --[no-]message <message>
                          commit message
    -s, --[no-]signoff    add a Signed-off-by trailer
"""

COMMANDER = """\
Usage: markdownlint-cli2 [options] <glob>

Options:
  -c, --config <file>   configuration file
  -f, --fix             fix violations where possible
  -h, --help            display help for command
"""

SUBCOMMANDS = """\
Usage: mkdocs [OPTIONS] COMMAND [ARGS]...

Commands:
  build      Build the MkDocs documentation
  gh-deploy  Deploy your documentation to GitHub Pages
  serve      Run the builtin development server
"""


def _toolroom_root() -> Path:
    """The toolroom package directory, whose docs and stubs the bench writes."""
    import livery.toolroom.tools as tools

    return Path(tools.__file__).resolve().parents[4]



def flags(verb: Verb) -> dict[str, Option]:
    return {o.name: o for o in verb.options}


def driver(key: str) -> _drivers.Driver:
    found = _drivers.find(key)
    assert found is not None, key
    return found


# --- reading each family --------------------------------------------------


def test_reads_inherit_a_task_env_without_the_interpreter(monkeypatch):
    """A read gets what any task gets — which never includes the caller's
    interpreter.

    `uv run` on Windows exports PYTHONHOME for the environment it launched,
    and a console script from any *other* Python then loads that stdlib
    instead of its own and dies before it can describe itself. The walk reads
    period venvs by design, so every release on a different minor than the
    runner read as a hole — 107 of them, every tool whose launcher was a
    console script rather than a native binary.

    Dropping it is not a rule about reads, though, which is why this asserts
    on the same `base_env()` every task starts from. PYTHONPATH *is* carried:
    a read is no more entitled to second-guess a deliberate `PYTHONPATH=src`
    than any other spawn.
    """
    monkeypatch.setenv("PYTHONHOME", "/somewhere/else")
    monkeypatch.setenv("PYTHONEXECUTABLE", "/somewhere/else/python")
    monkeypatch.setenv("PYTHONPATH", "/deliberate")
    monkeypatch.setenv("KEEP_ME", "yes")

    env = {**_globals.base_env(), **_toolhelp.QUIET}

    assert "PYTHONHOME" not in env
    assert "PYTHONEXECUTABLE" not in env
    assert env["PYTHONPATH"] == "/deliberate"
    assert env["KEEP_ME"] == "yes"
    assert env["GH_NO_UPDATE_NOTIFIER"] == "1"  # no update check mid-read


def test_reads_spawn_off_the_callers_console():
    """A tool that interrogates the terminal at start-up (tea ≤ 0.14.2 sent
    an OSC theme query) hangs a captured read under a VT-capable terminal —
    the hang follows whatever window the walk was launched from. A fresh
    hidden console has default (VT-off) modes and nothing worth
    interrogating, while console-hosted runtimes keep working — fully
    detached, pwsh dies at start-up and git-bash goes mute. POSIX passes 0,
    which subprocess accepts as "no flags".
    """
    import subprocess

    # `sys.platform` in statement form rather than `os.name`: same truth,
    # but a platform guard the type checkers understand, so the win32-only
    # constant is only analysed where it exists.
    if sys.platform == "win32":
        want: int = subprocess.CREATE_NO_WINDOW
    else:
        want = 0
    assert want == _toolhelp.NO_CONSOLE_WINDOW
    # And run_help actually works with it — the read below spawns that way.
    text = _toolhelp.run_help([sys.executable, "-c", "print('usage: x [--ok]')"])
    assert "--ok" in text


def test_the_reader_settles_which_builder_docker_describes():
    """`docker build` is buildx wherever buildx is installed — which is why
    the driver installs it — but with `DOCKER_BUILDKIT` unset the CLI only
    routes `build` that way for a Linux daemon, and a reader has no daemon.

    Linux and macOS read buildx; Windows fell back to the pre-2019 builder,
    and one verb was stored as two surfaces with twelve options recorded as
    a per-platform difference no user has. So the opt-in is pinned for the
    read, like `COLUMNS` and `help.format` before it.
    """
    code = "import os; print('usage: x [--ok]', os.environ.get('DOCKER_BUILDKIT'))"
    assert "1" in _toolhelp.run_help([sys.executable, "-c", code])


def test_run_help_reads_utf8_not_the_locale_codec():
    """git-cliff's help is UTF-8. Decoded with Windows cp1252 the reader
    thread died mid-decode, stdout came back None, and ten releases were
    recorded as holes on a platform where the tool describes itself fine.
    """
    code = (
        "import sys; "
        "sys.stdout.buffer.write('usage: x [--flag] \\u03cf\\n'.encode('utf-8'))"
    )
    text = _toolhelp.run_help([sys.executable, "-c", code])
    assert "Ϗ" in text  # decoded as UTF-8, crashed nothing


def test_clap_options_negations_and_choices():
    verb = _toolhelp.parse_help(CLAP, name="check")
    got = flags(verb)
    assert verb.help == "Run Ruff on the given files or directories"
    # Options live under several headings — `Rule selection:` counts too.
    assert {"fix", "watch", "fix_only", "color", "line_length", "select"} <= set(got)
    # ...and the two every tool has are never worth stubbing.
    assert "help" not in got
    # The negation is stated in prose, which is the only place clap says it.
    assert got["fix"].negation == "--no-fix"
    assert got["fix_only"].negation == "--no-fix-only"
    # `--watch` must NOT inherit the negation of the option printed below it.
    assert got["watch"].negation == ""
    assert got["color"].choices == ("auto", "always", "never")
    assert got["color"].type_name == "choice"
    assert got["line_length"].default == "88"
    assert got["fix"].type_name == "bool"
    assert got["line_length"].type_name == "str"


def test_color_flags_detects_the_switch():
    # The `--color` with an always/never choice set is the spelling footman
    # would force — both directions from the one list.
    verb = _toolhelp.parse_help(CLAP, name="check")
    spec = ToolSpec(name="ruff", verbs=(verb,))
    assert spec.color_flags() == {"check": ("--color", "always", "never")}


def test_color_flags_ignores_colorless_or_choiceless_options():
    # A --color without always/never, or no --color at all, is not a switch.
    spec = ToolSpec(
        name="d",
        verbs=(
            Verb(
                name="",
                options=(
                    Option("color", ("--color",), choices=("16", "256")),
                    Option("colour", ("--colour",)),  # no choices at all
                    Option("fix", ("--fix",), type_name="bool"),
                ),
            ),
        ),
    )
    assert spec.color_flags() == {}


def test_clap_flag_indent_varies_within_one_block():
    """`  -w, --watch` and `      --fix-only` are both flag lines.

    The help column, not the flag column, is the boundary — reading it the
    other way glues each long-only option onto the option above it.
    """
    blocks = _toolhelp._blocks(CLAP.splitlines())
    heads = [head for head, _ in blocks]
    assert "-w, --watch" in heads
    assert "--fix-only" in heads


def test_optparse_two_column_and_attached_values():
    verb = _toolhelp.parse_help(OPTPARSE, name="html")
    got = flags(verb)
    assert set(got) == {"contexts", "directory", "fail_under", "ignore_errors"}
    assert got["directory"].flags == ("--directory", "-d")
    assert got["directory"].type_name == "str"
    assert got["ignore_errors"].type_name == "bool"
    assert got["directory"].help == "Write the output files to DIR"


def test_cobra_go_types_are_values_not_flags():
    verb = _toolhelp.parse_help(COBRA, name="up")
    got = flags(verb)
    assert got["tail"].type_name == "str", "`--tail string` takes a value"
    assert got["scale"].type_name == "list[str]", "`stringArray` repeats"
    assert got["detach"].type_name == "bool"
    assert got["tail"].default == "all"
    # `--no-build` folds into `--build` rather than standing on its own.
    assert "no_build" not in got
    assert got["build"].negation == "--no-build"


def test_git_states_both_spellings_inline():
    verb = _toolhelp.parse_help(GIT, name="commit")
    got = flags(verb)
    assert {"quiet", "file", "message", "signoff"} <= set(got)
    assert got["quiet"].negation == "--no-quiet"
    # The tool *prints* `--[no-]quiet`; it *accepts* `--quiet`.
    assert "--quiet" in got["quiet"].flags
    assert not any("[no-]" in f for f in got["quiet"].flags)
    assert got["file"].type_name == "str"
    assert got["signoff"].help == "add a Signed-off-by trailer"


def test_one_option_in_two_dialects_keys_on_the_python_one():
    """Claude Code prints `--allowedTools, --allowed-tools`: one option, two
    spellings. The keyword is written in Python, so it takes the lowercase
    one — and `allowed_tools=` then round-trips to a flag the tool takes.

    The guard is that sameness folds case *and* dashes away. Two options
    whose help ran into one block are not dialects of each other and must
    keep their own keywords, which is markdownlint-cli2's `--configPointer,
    --config` (its column alignment aligns to the longest option) and git's
    `--column[=<options>], --no-column`.
    """
    text = (
        "Options:\n"
        "  --allowedTools, --allowed-tools <tools...>\n"
        "      Tools to allow\n"
        "  --bg, --background            Run in the background\n"
        "  --configPointer, --config <p>  A JSON pointer\n"
        "  --column[=<options>], --no-column  Paginate\n"
    )
    got = flags(_toolhelp.parse_help(text))
    assert "allowed_tools" in got and "allowedTools" not in got
    assert "--allowedTools" in got["allowed_tools"].flags  # both still recorded
    assert "bg" in got, "already lowercase — the first spelling still wins"
    assert "configPointer" in got, "a neighbour, not a dialect"
    assert "column" in got and "no_column" not in got


def test_optional_value_option_is_neither_switch_nor_required_value():
    # git glues an optional-value placeholder to the flag with no space.
    # Read as a switch, `--gpg-sign[=<key-id>]` would reject a key; read as
    # a required value, it would reject the bare flag. It is both.
    text = (
        "    -S, --[no-]gpg-sign[=<key-id>]\n"
        "                          GPG-sign the commit\n"
        "    -u, --[no-]untracked-files[=<mode>]\n"
        "                          show untracked files\n"
        "    -m, --[no-]message <message>\n"
        "                          commit message\n"
    )
    got = flags(_toolhelp.parse_help(text, name="commit"))
    assert got["gpg_sign"].type_name == "optvalue"
    assert got["gpg_sign"].negation == "--no-gpg-sign"
    assert got["untracked_files"].type_name == "optvalue"
    # A required value (no brackets) stays a plain value, not optvalue.
    assert got["message"].type_name == "str"


def test_optvalue_stub_type_accepts_bare_and_valued():
    from livery.toolroom.bench._toolspec import Option

    assert (
        _stubgen._annotation(Option("gpg_sign", type_name="optvalue")) == "ValuedFlag"
    )
    assert _stubgen._annotation(Option("m", type_name="str")) == "Value"


def test_commander_and_summary_skips_usage():
    verb = _toolhelp.parse_help(COMMANDER)
    got = flags(verb)
    assert set(got) == {"config", "fix"}
    assert got["config"].type_name == "str"
    assert _toolhelp._summary(COMMANDER) == ""


# --- dialect refinements --------------------------------------------------


def test_repeatable_flag_does_not_absorb_its_trailing_ellipsis():
    # clap prints a repeatable flag as `--verbose...`; the dots are not part
    # of the keyword (a greedy `.` once produced `verbose___`).
    text = "Options:\n  -v, --verbose...  Use verbose output\n"
    got = flags(_toolhelp.parse_help(text))
    assert "verbose" in got and "verbose___" not in got


def test_python_colon_gutter_is_neither_help_text_nor_lost():
    # CPython's `--help` separates the flag column from the description with a
    # ` : ` gutter, not the double-space gutter every other dialect uses. The
    # colon must not leak into the help (`-b     : issue warnings` once became
    # `: issue warnings`), and when the columns touch (`-c cmd : program …`)
    # there is no double space at all — the metavar and description must still
    # survive rather than being dropped.
    text = (
        "options:\n"
        "-b     : issue warnings about bytes/str comparisons\n"
        "-c cmd : program passed in as string (terminates option list)\n"
        "-W arg : warning control; arg is action:message:category\n"
    )
    got = flags(_toolhelp.parse_help(text, name="python"))
    assert got["b"].type_name == "bool"
    assert got["b"].help == "issue warnings about bytes/str comparisons"
    assert got["c"].type_name == "str", "`-c cmd` takes a value"
    assert got["c"].help == "program passed in as string (terminates option list)"
    assert got["W"].type_name == "str"
    assert got["W"].help.startswith("warning control")


def test_bare_lowercase_metavar_is_a_value_in_help_but_not_a_man_page():
    # gh names a value with a bare lowercase word (`--assignee login`); a man
    # page's prose (`the --patch option.`) must not read one the same way.
    loose = _toolhelp._option("--assignee login", "")
    strict = _toolhelp._option("--assignee login", "", strict=True)
    assert loose is not None and strict is not None
    assert loose.type_name == "str"  # help mode: bare word is the value
    assert strict.type_name == "bool"  # man mode: prose word ignored


def test_bulleted_options_are_read():
    # markdownlint-cli2 prints options as a bulleted list, `- --fix  …`.
    text = (
        "Optional parameters:\n"
        "- --config       specifies the path to a configuration file\n"
        "- --fix          updates files to resolve fixable issues\n"
    )
    assert {"config", "fix"} <= set(flags(_toolhelp.parse_help(text)))


def test_go_stdlib_flag_format_is_read():
    # Go's `flag`: single-dash long options under `Usage of <prog>:`, each
    # description on the next indented line, and no summary of its own.
    text = (
        "Usage of eclint:\n"
        "  -color string\n"
        '    \tuse color when printing (default "auto")\n'
        "  -fix\n"
        "    \tenable fixing instead of error reporting\n"
    )
    verb = _toolhelp.parse_help(text)
    got = flags(verb)
    assert got["color"].type_name == "str"  # single-dash long, valued
    assert got["fix"].type_name == "bool"
    assert verb.help == ""  # Go flag prints no summary line


def test_stub_escapes_backslashes_in_help_text():
    # mypy's `--exclude '/setup\.py$'` must land as a literal in the docstring,
    # not an invalid `\.` escape a compiler warns on.
    assert _stubgen._esc(r"a \.py$ b") == r"a \\.py$ b"


def test_short_option_policy_modes():
    text = (
        "Options:\n"
        "  -m, --message <msg>  the message\n"
        "  -C <commit>          short-only, reuse the commit\n"
    )
    # "only" (default): a short-only `-C` keys on `C`; `-m, --message` on the
    # long, `message` — not `m`.
    only = flags(_toolhelp.parse_help(text))
    assert "C" in only and "message" in only and "m" not in only
    # "none": no short keyword at all — `-C` (no long) is dropped.
    none = flags(_toolhelp.parse_help(text, shorts="none"))
    assert "message" in none and "C" not in none and "m" not in none
    # "all": also key on the short of a long-having option — `m` appears too.
    every = flags(_toolhelp.parse_help(text, shorts="all"))
    assert {"C", "message", "m"} <= set(every)


def test_subcommands_are_read_from_their_own_section():
    found = _toolhelp.subcommands(SUBCOMMANDS)
    assert found["build"] == "Build the MkDocs documentation"
    assert "gh-deploy" in found


def test_help_of_a_missing_tool_is_empty_not_an_error():
    assert _toolhelp.run_help(["definitely-not-a-real-tool-xyz"]) == ""
    spec = _toolhelp.from_help("definitely-not-a-real-tool-xyz")
    assert spec.verbs == ()


# --- reading click, which hands over structure ----------------------------


def _param(name, opts, **kw):
    """A duck-typed click parameter — the extractor never imports click."""
    choices = kw.pop("choices", None)
    return SimpleNamespace(
        param_type_name="option",
        name=name,
        opts=opts,
        secondary_opts=kw.pop("secondary_opts", []),
        is_flag=kw.pop("is_flag", False),
        multiple=kw.pop("multiple", False),
        default=kw.pop("default", None),
        help=kw.pop("help", ""),
        type=SimpleNamespace(name="choice" if choices else "text", choices=choices),
    )


def _command(help_text, params):
    return SimpleNamespace(help=help_text, params=params, name="build")


def test_click_names_options_after_the_flag_not_the_variable():
    """Click calls a group of exclusive flags after one internal variable.

    mkdocs' `--dirty`, `--clean` and `--dirtyreload` are all `build_type`;
    naming the stub after that would emit three parameters with one name,
    and the bridge would translate a keyword no tool accepts.
    """
    command = _command(
        "Serve the docs.",
        [
            _param("build_type", ["--dirty"], is_flag=True),
            _param("build_type", ["--dirtyreload"], is_flag=True),
            _param("build_type", ["--clean"], is_flag=True),
        ],
    )
    verb = _toolspec._verb_from_click("serve", command)
    assert sorted(flags(verb)) == ["clean", "dirty", "dirtyreload"]


def test_click_secondary_opts_are_the_true_negation():
    command = _command(
        "Build it.",
        [
            _param("clean", ["--clean"], secondary_opts=["--dirty"], is_flag=True),
            _param(
                "strict", ["--strict"], secondary_opts=["--no-strict"], is_flag=True
            ),
            _param("theme", ["--theme"], choices=["material", "readthedocs"]),
        ],
    )
    spec = ToolSpec(
        name="mkdocs", verbs=(_toolspec._verb_from_click("build", command),)
    )
    got = flags(spec.verbs[0])
    assert got["clean"].negation == "--dirty"
    assert got["theme"].choices == ("material", "readthedocs")
    # Only the exceptions are tabled: a table of things that already work
    # would be noise, and would need regenerating far more often.
    assert spec.negations() == {"clean": "--dirty"}


def test_click_group_becomes_one_verb_per_command():
    sub = _command("Build it.", [_param("strict", ["--strict"], is_flag=True)])
    group = SimpleNamespace(
        help="Docs.", name="mkdocs", params=[], commands={"gh-deploy": sub}
    )
    spec = _toolspec.from_click(group, name="mkdocs", version="1.6.1")
    assert [v.name for v in spec.verbs] == ["gh_deploy"]
    assert spec.in_process is True
    assert spec.version == "1.6.1"


# --- rendering the stub ---------------------------------------------------


def _spec(*options: Option, name: str = "demo", verb: str = "build") -> ToolSpec:
    return ToolSpec(
        name=name,
        help="A demo tool.",
        version="1.0",
        verbs=(Verb(name=verb, help="Build it.", options=options),),
    )


def test_rendered_stub_is_valid_python():
    spec = _spec(
        Option(
            "clean",
            ("--clean",),
            negation="--dirty",
            help="Wipe first",
            type_name="bool",
            default=True,
        ),
        Option("select", ("--select",), help="Rules", type_name="list[str]"),
        Option(
            "color",
            ("--color",),
            help="When",
            type_name="choice",
            choices=("auto", "never"),
        ),
    )
    text = _stubgen.render(spec, platform="Linux")
    ast.parse(text)  # a stub that doesn't parse is worse than no stub
    assert "class Demo(ToolBase[_R]):" in text
    assert "class Build(ToolBase[_R2]):" in text  # the verb, as a generic class
    assert "build: Build[_R]" in text  # named under its tool
    assert "def argv(self) -> Demo.Build[Argv]: ..." in text
    assert "**flags: Any" in text, "the stub must never be able to forbid"


def test_rendered_stub_teaches_the_off_spelling():
    spec = _spec(
        Option(
            "clean",
            ("--clean",),
            negation="--dirty",
            help="Wipe first",
            type_name="bool",
            default=True,
        ),
        Option(
            "strict",
            ("--strict",),
            negation="--no-strict",
            help="Be strict",
            type_name="bool",
        ),
    )
    text = _stubgen.render(spec)
    assert "`clean=off` emits `--dirty`" in text
    assert "Defaults on" in text, "a flag that is on by default says so"
    assert "`strict=off` emits `--no-strict`" in text


def test_rendered_stub_imports_only_what_it_uses():
    plain = _stubgen.render(_spec(Option("quiet", ("--quiet",), type_name="bool")))
    assert "Literal" not in plain
    assert ": Value" not in plain, "no value option, so no value alias"
    assert "_Result" not in plain, "nothing returns Result; the TypeVar does"
    # `Tool` alone is aliased — a subcommand becomes a class named after the
    # verb, and `uv tool` would otherwise write `class Tool(Tool)`. The rest
    # import bare: reader-facing names in every hover.
    assert "from livery.toolroom.tools import Argv, Flag, Tool as ToolBase" in plain

    choosy = _stubgen.render(
        _spec(Option("color", ("--color",), type_name="choice", choices=("a", "b")))
    )
    assert "from typing import Any, Literal" in choosy
    assert "Sequence" in choosy


def test_rendered_stub_never_repeats_a_keyword():
    """A duplicate parameter is a syntax error, so the renderer is the last
    line of defence whatever a spec happens to contain.
    """
    spec = _spec(
        Option("dirty", ("--dirty",), type_name="bool"),
        Option("dirty", ("--dirty",), type_name="bool"),
    )
    text = _stubgen.render(spec)
    ast.parse(text)
    assert text.count("dirty: Flag") == 1


def test_a_lone_summary_leaves_room_for_the_quotes_ruff_will_join():
    """A docstring with nothing under it is folded onto one line by the
    formatter — closing quotes included — so the wrap has to leave room for
    six characters rather than three.

    Claude Code's `setup-token` is the one that found this: a summary of
    exactly 71 characters wrapped to a line of 86, and the fold ruff applied
    straight afterwards took it to 89. The stub generator wrote a file its
    own gate then refused.
    """
    lone = "Set up a long-lived authentication token (requires Claude subscription)"
    summary = ToolSpec(name="demo", verbs=(Verb(name="setup_token", help=lone),))
    (line,) = [ln for ln in _stubgen.render(summary).splitlines() if '"""Set up' in ln]
    assert len(line) + len('"""') <= 88

    # An `Args:` block below keeps the closing quotes on their own line, so
    # this summary is never joined and keeps the wider wrap: narrowing it
    # would rewrap docstrings for characters they never see.
    documented = ToolSpec(
        name="demo",
        verbs=(
            Verb(
                name="setup_token",
                help=lone,
                options=(Option("quiet", ("--quiet",), help="Hush", type_name="bool"),),
            ),
        ),
    )
    assert f'"""{lone}' in _stubgen.render(documented)


def test_value_options_accept_a_sequence():
    """`select=["E", "F"]` works at run time, so it must type-check.

    The bridge repeats a flag once per item; whether the tool accepts the
    repetition is the tool's business, not the stub's.
    """
    option = Option("select", ("--select",), type_name="str")
    assert _stubgen._annotation(option) == "Value"
    assert _stubgen._annotation(Option("f", ("--f",), type_name="bool")) == "Flag"


def test_a_tool_with_no_verbs_still_renders():
    spec = ToolSpec(name="lonely", verbs=(Verb(name="", help="Do it."),))
    text = _stubgen.render(spec)
    ast.parse(text)
    assert "def __call__(" in text
    assert "type: ignore[override]" in text


def test_nested_verbs_become_nested_classes():
    spec = ToolSpec(
        name="docker",
        verbs=(
            Verb(name="compose.up", help="Up.", options=()),
            Verb(name="build", help="Build.", options=()),
        ),
    )
    text = _stubgen.render(spec)
    ast.parse(text)
    # Inside `Docker`, not beside it: the group belongs to the tool, the name
    # is not invented, and one docs directive covers the whole tool.
    assert "    class Compose(ToolBase[_R2]):" in text
    assert "    compose: Compose[_R]" in text
    assert "        class Up(ToolBase[_R3]):" in text  # the leaf, one deeper
    assert "        up: Up[_R2]" in text
    assert "DockerCompose" not in text


def test_keyword_named_flags_take_the_trailing_underscore():
    spec = _spec(Option("global", ("--global",), type_name="bool"))
    text = _stubgen.render(spec)
    ast.parse(text)
    assert "global_: Flag" in text


def test_long_arg_help_stays_on_one_line():
    """Hovers treat a docstring's line breaks as hard breaks, so a wrap
    chosen for the .pyi's column limit used to land mid-sentence in every
    tooltip. An `Args:` entry is one line now — each renderer reflows to
    its own width, and E501 is waived for `_stubs/` in exchange.
    """
    spec = _spec(
        Option(
            "explain",
            ("--explain",),
            help="A very long line of help text " * 6,
            type_name="bool",
        )
    )
    text = _stubgen.render(spec)
    entries = [
        line
        for line in text.splitlines()
        if line.strip().startswith("explain: A very long line")
    ]
    assert len(entries) == 1, "the whole entry, on the one line hovers reflow"


def test_choice_literals_hoist_into_named_aliases():
    """One module-level alias per distinct choice set — a hover shows the
    name once, not the union spelled twice over.
    """
    spec = ToolSpec(
        name="demo",
        verbs=(
            Verb(
                name="build",
                options=(
                    Option(
                        "color",
                        ("--color",),
                        type_name="choice",
                        choices=("auto", "never"),
                    ),
                ),
            ),
            Verb(
                name="serve",
                options=(
                    Option(
                        "color",
                        ("--color",),
                        type_name="choice",
                        choices=("auto", "never"),
                    ),
                ),
            ),
        ),
    )
    text = _stubgen.render(spec)
    ast.parse(text)
    # One alias, shared by both verbs; the annotation reads through it.
    assert text.count("Color: TypeAlias = Literal['auto', 'never']") == 1
    assert text.count("color: Color | Sequence[Color] | None = ...,") == 2


def test_choice_sets_sharing_a_name_get_distinct_aliases():
    spec = ToolSpec(
        name="demo",
        verbs=(
            Verb(
                name="build",
                options=(
                    Option("mode", ("--mode",), type_name="choice", choices=("a", "b")),
                ),
            ),
            Verb(
                name="serve",
                options=(
                    Option("mode", ("--mode",), type_name="choice", choices=("c",)),
                ),
            ),
        ),
    )
    text = _stubgen.render(spec)
    ast.parse(text)
    assert "Mode: TypeAlias = Literal['a', 'b']" in text
    assert "Mode2: TypeAlias = Literal['c']" in text


def test_an_alias_never_collides_with_a_verb_class():
    spec = ToolSpec(
        name="demo",
        verbs=(
            Verb(name="color"),
            Verb(
                name="build",
                options=(
                    Option("color", ("--color",), type_name="choice", choices=("a",)),
                ),
            ),
        ),
    )
    text = _stubgen.render(spec)
    ast.parse(text)
    assert "class Color(ToolBase" in text  # the verb keeps its name
    assert "Color2: TypeAlias = Literal['a']" in text  # the alias counts up


def test_a_verb_that_would_shadow_an_import_is_refused():
    """A verb named `flag` would generate `class Flag`, shadowing the
    imported alias inside its tool's class body — every annotation after it
    would silently mean the wrong thing. No real tool spells such a verb;
    the renderer refuses loudly and sync keeps the checked-in stub.
    """
    spec = ToolSpec(name="demo", verbs=(Verb(name="flag"),))
    with pytest.raises(_stubgen.NameCollision, match="Flag"):
        _stubgen.render(spec)


def test_positionals_accept_paths():
    """`ruff.check(Path("src"))` already ran — the bridge `str()`-s every
    positional — so the annotation must not call it a type error.
    """
    text = _stubgen.render(_spec(Option("quiet", ("--quiet",), type_name="bool")))
    assert "*args: str | PathLike[str]," in text
    assert "from os import PathLike" in text


# --- the driver table -----------------------------------------------------


def test_every_driver_maps_to_a_bridge_attribute():
    from livery.toolroom import tools

    for driver in _drivers.DRIVERS:
        assert isinstance(getattr(tools, driver.key), tools.Tool)


def test_driver_lookup_and_pre_bound_verbs():
    assert driver("ruff_format").base == ("format",)
    assert driver("ruff_format").wanted == ("format",)
    assert driver("ruff").wanted == ("check", "format", "clean")
    assert driver("markdownlint").name == "markdownlint-cli2"
    assert _drivers.find("nope") is None


def test_a_pre_bound_tool_stubs_its_verb_as_call():
    spec = ToolSpec(
        name="ruff",
        verbs=(
            Verb(name="check", options=(Option("fix", ("--fix",), type_name="bool"),)),
            Verb(
                name="format", options=(Option("diff", ("--diff",), type_name="bool"),)
            ),
        ),
    )
    rebased = _drivers._rebase(spec, ("format",))
    assert [v.name for v in rebased.verbs] == [""]
    assert flags(rebased.verbs[0])["diff"].name == "diff"


def test_selecting_verbs_keeps_the_tools_own_options():
    spec = ToolSpec(
        name="uv",
        verbs=(Verb(name=""), Verb(name="sync"), Verb(name="publish")),
    )
    kept = _drivers._select(spec, ("sync",))
    assert [v.name for v in kept.verbs] == ["", "sync"]


def test_version_of_a_missing_tool_is_empty():
    assert _drivers.version("definitely-not-a-real-tool-xyz") == ""


def test_in_process_capability_is_the_entry_point():
    # coverage publishes a console script; a shell builtin never will.
    assert _drivers.in_process_capable("coverage") is True
    assert _drivers.in_process_capable("definitely-not-a-real-tool-xyz") is False


@pytest.mark.parametrize("key", [d.key for d in _drivers.DRIVERS])
def test_every_curated_tool_has_a_checked_in_stub(key):
    from livery.toolroom.bench import _tasks as tools_tasks

    assert tools_tasks._stub_path(key).exists(), f"no stub for {key}"


# --- the tasks that talk to real binaries ---------------------------------


@pytest.fixture
def stubs(tmp_path, monkeypatch):
    """Point the tasks at scratch directories, not the checked-in ones.

    All three: generating a stub also records the reading in the option
    history, and a refresh writes its events into the CHANGELOG. A fixture
    that isolated only `_STUBS` would let a test write this machine's tool
    versions into the repo's history — which is exactly what happened the
    first time this fixture forgot, and again when the CHANGELOG gained a
    writer. Every new write path belongs here in the same commit that adds
    it.
    """
    from livery.toolroom.bench import _tasks as tools_tasks

    monkeypatch.setattr(tools_tasks, "_STUBS", tmp_path)
    monkeypatch.setattr(tools_tasks, "_HISTORY", tmp_path / "history")
    monkeypatch.setattr(tools_tasks, "_CHANGELOG", tmp_path / "CHANGELOG.md")
    return tmp_path


needs_ruff = pytest.mark.skipif(
    shutil.which("ruff") is None, reason="ruff is not on PATH"
)
needs_uv = pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not on PATH")


def test_list_names_every_curated_tool(capsys):
    from livery.toolroom.bench import _tasks as tools_tasks

    tools_tasks.list_()
    out = capsys.readouterr().out
    for expected in ("ruff", "mkdocs", "markdownlint"):
        assert expected in out
    assert "in-process" in out


def test_list_missing_only_shows_what_is_absent(capsys):
    from livery.toolroom.bench import _tasks as tools_tasks

    tools_tasks.list_(show="missing")
    out = capsys.readouterr().out
    for line in out.splitlines()[1:]:
        assert "not installed" in line


def test_list_installed_only_shows_what_is_present(capsys):
    from livery.toolroom.bench import _tasks as tools_tasks

    tools_tasks.list_(show="installed")
    out = capsys.readouterr().out
    for line in out.splitlines()[1:]:
        assert "not installed" not in line


def test_list_says_unreadable_when_a_present_tools_version_wont_read(
    capsys, monkeypatch
):
    # Presence and readability are different facts: a tool that passes the
    # installed filter but whose `--version` stalls must never render the
    # false `not installed` — the contradiction two Windows CI slices caught
    # in one minute when gh's spawn stalled past the probe timeout.
    from livery.toolroom.bench import _drivers
    from livery.toolroom.bench import _tasks as tools_tasks

    monkeypatch.setattr(
        _drivers, "_read_version", lambda name: ("", "timed out after 30s")
    )
    tools_tasks.list_(show="installed")
    out = capsys.readouterr().out
    body = out.splitlines()[1:]
    assert body, "at least one tool is installed wherever the suite runs"
    for line in body:
        assert "not installed" not in line
        assert "unreadable (timed out after 30s)" in line


@needs_ruff
def test_spec_prints_what_the_tool_says(capsys):
    from livery.toolroom.bench import _tasks as tools_tasks

    tools_tasks.spec("ruff", verb="check")
    out = capsys.readouterr().out
    assert "ruff" in out
    assert "check" in out
    assert "--fix" in out or "fix" in out


def test_spec_refuses_an_unknown_or_absent_tool():
    from livery.toolroom.bench import _tasks as tools_tasks

    with pytest.raises(SystemExit, match="no driver"):
        tools_tasks.spec("not-a-curated-tool")


def test_color_probes_git_as_flag_forced(capsys):
    # git is a system tool (this is a git repo). Probed live, it forces colour
    # ON with its own switch (`-c color.ui=always`) and OFF via the environment.
    # `only=` skips the file write, so nothing on disk changes.
    from livery.toolroom.bench import _tasks as tools_tasks

    tools_tasks.color(only="git")
    out = capsys.readouterr().out
    assert "git" in out and "flag" in out and "color.ui=always" in out


def test_colorprobe_categorises_git_and_unprobed():
    from livery.toolroom.bench import _colorprobe, _drivers
    from livery.toolroom.bench._toolspec import ToolSpec

    git = _drivers._resolve("git")
    assert git is not None
    v = _colorprobe.probe("git", git, ToolSpec(name="git"))
    assert v.on == "flag" and v.off == "env"
    assert v.flag is not None and v.flag.on == ("-c", "color.ui=always")
    # a tool with no trigger is `unprobed` — returned without running anything.
    none = _colorprobe.probe("notatool", "notatool", ToolSpec(name="notatool"))
    assert none.on == "unprobed" and none.off == "unprobed"


@needs_ruff
def test_colorprobe_ruff_obeys_the_environment():
    from livery.toolroom.bench import _colorprobe, _drivers

    driver = _drivers.find("ruff")
    assert driver is not None
    spec = _drivers.extract(driver)
    ruff = _drivers._resolve("ruff")
    assert ruff is not None
    v = _colorprobe.probe("ruff", ruff, spec)
    assert v.on == "env" and v.off == "env" and v.flag is None


def test_colorprobe_write_leaves_alone_what_it_could_not_probe():
    """A probe needs a binary, and the man tier provisions pages — so git is
    in no prefix, and a run that wrote only what it saw would drop its
    `-c color.ui=always` and take the bridge's colour forcing with it.
    """
    from livery.toolroom.bench import _colorprobe

    stored: dict[str, _colorprobe.Row] = {
        "git": ("git", "flag", "env", ("-c", "color.ui=always"), (), True),
        "ruff": ("ruff", "none", "n/a", (), (), False),  # a stale verdict
    }
    fresh = {"ruff": ("ruff", _colorprobe.Verdict("env", "env"))}
    folded = _colorprobe.merged(stored, fresh)
    assert folded["ruff"][1].on == "env", "what was probed here wins"
    kept = folded["git"][1]
    assert (kept.on, kept.off) == ("flag", "env")
    assert kept.flag is not None
    assert kept.flag.on == ("-c", "color.ui=always") and kept.flag.pre_verb


def test_colorprobe_render_round_trips():
    from livery.toolroom.bench import _colorprobe

    flag = _colorprobe.ColourFlag(("-c", "color.ui=always"), (), True)
    text = _colorprobe.render(
        {"git": ("git", _colorprobe.Verdict("flag", "env", flag))}
    )
    ns: dict[str, Any] = {}
    exec(text, ns)  # the generated module must be valid, importable Python
    assert ns["COLOUR"]["git"] == (
        "git",
        "flag",
        "env",
        ("-c", "color.ui=always"),
        (),
        True,
    )


@needs_ruff
def test_sync_writes_a_stub_and_audit_then_agrees(stubs, capsys):
    from livery.toolroom.bench import _tasks as tools_tasks

    tools_tasks.sync(only="ruff")
    written = stubs / "ruff.pyi"
    assert written.exists()
    ast.parse(written.read_text())
    assert "class Ruff(ToolBase[_R]):" in written.read_text()
    capsys.readouterr()

    tools_tasks.audit(only="ruff")
    assert "match the tools they were read from" in capsys.readouterr().out


def test_prefix_reads_binaries_from_the_provisioned_set(tmp_path, monkeypatch):
    """`--prefix` is what lets a scheduled job ask "have the tools moved?"
    against isolated latest binaries instead of whatever the runner has.
    """
    import os

    from livery.toolroom.bench import _tasks as tools_tasks

    bindir = tmp_path / "bin"
    bindir.mkdir()
    before = os.environ["PATH"]
    with tools_tasks._on_path(str(tmp_path)):
        assert os.environ["PATH"].startswith(f"{bindir}{os.pathsep}")
    assert os.environ["PATH"] == before  # restored, and scoped to the task

    with tools_tasks._on_path(""):  # empty: every caller passes its param through
        assert os.environ["PATH"] == before


@needs_ruff
def test_audit_reports_a_behind_snapshot_without_failing(stubs, capsys):
    """A tool that has moved on is news, not a fault: the stub is a snapshot
    and footman promises no particular speed at retaking it. Reporting must
    not exit non-zero, or a weekly check reads as a broken build every time
    somebody else ships a release.
    """
    from livery.toolroom.bench import _tasks as tools_tasks

    tools_tasks.sync(only="ruff")
    (stubs / "ruff.pyi").write_text("class Ruff(_Tool): ...\n")
    capsys.readouterr()

    report = tools_tasks.audit(only="ruff")
    out = capsys.readouterr().out
    assert "released a newer version" in out
    assert "nothing is broken" in out
    assert report["behind"] == ["ruff"]

    # ...and --fix takes the fresh snapshot instead of reporting it.
    tools_tasks.audit(only="ruff", fix=True)
    assert "took a fresh snapshot of 1" in capsys.readouterr().out
    fresh = (stubs / "ruff.pyi").read_text()
    assert "class Ruff(ToolBase[_R]):" in fresh
    assert "def __call__(" in fresh


@needs_ruff
def test_audit_strict_gives_automation_something_to_trip_on(stubs, capsys):
    from livery.toolroom.bench import _tasks as tools_tasks

    tools_tasks.sync(only="ruff")
    (stubs / "ruff.pyi").write_text("class Ruff(_Tool): ...\n")
    capsys.readouterr()
    with pytest.raises(SystemExit) as caught:
        tools_tasks.audit(only="ruff", strict=True)
    assert caught.value.code == 2
    # The wording is the same either way — only the exit code differs.
    assert "released a newer version" in capsys.readouterr().out


@needs_ruff
def test_audit_reports_a_runtime_table_that_disagrees(stubs, monkeypatch):
    from livery.toolroom import tools as bridge
    from livery.toolroom.bench import _tasks as tools_tasks

    monkeypatch.setitem(bridge._NEGATIONS, "ruff", {"fix": "--never-fix"})
    with pytest.raises(SystemExit, match=r"_NEGATIONS\['ruff'\]"):
        tools_tasks.audit(only="ruff")


@needs_uv
def test_audit_reports_a_wrappers_table_that_disagrees(stubs, monkeypatch):
    from livery.toolroom import tools as bridge
    from livery.toolroom.bench import _tasks as tools_tasks

    monkeypatch.setitem(bridge._WRAPPERS, "uv", frozenset({"run"}))  # missing tool.run
    with pytest.raises(SystemExit, match=r"_WRAPPERS\['uv'\]"):
        tools_tasks.audit(only="uv")


def test_sync_skips_and_names_the_tools_it_cannot_ask(stubs, capsys):
    """A check that quietly covered three of thirteen would be worse than
    no check, so what was skipped is printed.
    """
    from livery.toolroom.bench import _tasks as tools_tasks

    tools_tasks.sync(only="definitely-not-installed")
    out = capsys.readouterr().out
    assert "wrote 0 stub(s)" in out


def test_formatting_falls_back_when_ruff_cannot_run(monkeypatch):
    from livery.toolroom.bench import _tasks as tools_tasks

    def boom(*args, **kwargs):
        raise OSError("no ruff here")

    monkeypatch.setattr("subprocess.run", boom)
    assert tools_tasks._formatted("class _X: ...\n") == "class _X: ...\n"


# --- the extraction ladder ------------------------------------------------


def test_click_is_preferred_over_help_text():
    """Mkdocs is a click tool, so `--dirty` is known structurally rather
    than hoped for in prose.
    """
    found = _drivers.find("mkdocs")
    assert found is not None
    if not _drivers.installed(found):
        pytest.skip("mkdocs is not installed")
    spec = _drivers.extract(found)
    assert spec.negations()["clean"] == "--dirty"
    assert spec.in_process is True


def test_a_tool_with_no_entry_point_is_not_a_click_tool():
    assert _drivers._from_click(_drivers.Driver("definitely-not-real")) is None


def test_an_entry_point_that_is_not_click_falls_through(monkeypatch):
    from livery.toolroom import tools as bridge

    monkeypatch.setattr(
        bridge, "_console_entrypoint", lambda name: SimpleNamespace(load=lambda: len)
    )
    assert _drivers._from_click(_drivers.Driver("pretend")) is None


def test_an_entry_point_that_will_not_import_is_not_a_spec(monkeypatch):
    from livery.toolroom import tools as bridge

    def explode():
        raise ImportError("that tool is broken")

    monkeypatch.setattr(
        bridge, "_console_entrypoint", lambda name: SimpleNamespace(load=explode)
    )
    assert _drivers._from_click(_drivers.Driver("pretend")) is None


def test_extracting_an_absent_tool_yields_an_empty_spec():
    spec = _drivers.extract(_drivers.Driver("definitely-not-a-real-tool-xyz"))
    assert spec.verbs == ()
    assert (
        _drivers.installed(_drivers.Driver("definitely-not-a-real-tool-xyz")) is False
    )


def test_extraction_replaces_this_machines_home_with_a_tilde(monkeypatch, tmp_path):
    """Docker reports its config default expanded: the maintainer's own home
    directory went into the store and shipped inside `docker.pyi` on PyPI.

    It is also the difference that divides every platform — `/home/runner`
    on Linux, `C:\\Users\\runneradmin` on Windows, for the same option of
    the same release — so each leg of the matrix would overwrite the last
    and the weekly run would report a change nobody made.
    """
    home = tmp_path / "someone"
    monkeypatch.setattr(_drivers.Path, "home", classmethod(lambda cls: home))
    spec = ToolSpec(
        name="docker",
        help=f"config in {home}/.docker",
        verbs=(
            Verb(
                name="",
                help=f"reads {home}/.docker",
                options=(
                    Option(
                        name="config",
                        help=f"Location of config files (default {home}/.docker)",
                        default=f"{home}/.docker",
                    ),
                    Option(name="debug", default=False),
                ),
            ),
        ),
    )
    clean = _drivers._anonymous(spec, home)
    option = clean.verbs[0].options[0]
    assert clean.help == "config in ~/.docker"
    assert clean.verbs[0].help == "reads ~/.docker"
    assert option.default == "~/.docker"
    assert option.help.endswith("(default ~/.docker)")
    assert clean.verbs[0].options[1].default is False  # not a string, untouched


def test_the_scrub_uses_the_home_the_walk_gave_not_this_process_one(
    monkeypatch, tmp_path
):
    """The gap the first Windows gather found.

    Inside a run the overlay that sets `HOME` writes to `ctx.env` — the
    *children's* environment — so the tool being read echoes the throwaway
    home while this process still reports the real one. Asking
    `Path.home()` matched nothing and scrubbed nothing, and docker's config
    default was recorded as a path with a random per-run directory in it,
    which would have disagreed with itself every week and with every other
    platform forever.

    So the home is handed in, and `Path.home()` being something else
    entirely must not matter.
    """
    given = tmp_path / "scratch" / "docker-29.6.2" / "home"
    monkeypatch.setattr(_drivers.Path, "home", classmethod(lambda cls: tmp_path / "e"))
    spec = ToolSpec(
        name="docker",
        verbs=(
            Verb(
                name="",
                options=(
                    Option(name="config", default=f"{given}/.docker"),
                    Option(name="tlscert", help=f"cert at {given}/.docker/c.pem"),
                ),
            ),
        ),
    )
    clean = _drivers._anonymous(spec, given, _drivers.Path.home())
    assert clean.verbs[0].options[0].default == "~/.docker"
    assert clean.verbs[0].options[1].help.endswith("~/.docker/c.pem")


def test_a_short_name_is_the_same_home(tmp_path):
    """Windows has more than one spelling for one directory.

    A gather set `HOME` to a path under `%TEMP%` and docker echoed it back
    as `C:\\Users\\WILLEM~1\\AppData\\Local\\Temp\\…` — the 8.3 short name,
    where the string handed to the scrub had the long one. `str.replace`
    saw two different paths, wrote neither, and the shipped stub carried a
    machine's directory again. Case is the same trap: Windows does not
    distinguish it and a comparison does.
    """
    from pathlib import Path

    long = r"C:\Users\Willem Kokke\AppData\Local\Temp\fm-1\docker-29.6.2\home"
    short = r"C:\Users\WILLEM~1\AppData\Local\Temp\fm-1\docker-29.6.2\home"
    spec = ToolSpec(
        name="docker",
        verbs=(
            Verb(
                name="",
                options=(
                    Option(name="config", default=short + r"\.docker"),
                    Option(name="cert", help="at " + short + r"\.docker\ca.pem"),
                    Option(name="upper", default=short.upper() + r"\.docker"),
                ),
            ),
        ),
    )
    clean = _drivers._anonymous(spec, Path(long))
    got = clean.verbs[0].options
    assert got[0].default == r"~\.docker"
    assert got[1].help == r"at ~\.docker\ca.pem"
    assert got[2].default == r"~\.docker"  # case is not a different path


def test_a_throwaway_home_inside_the_real_one_is_replaced_whole(tmp_path):
    """Longest first, or a nested throwaway home leaves `~/…/home/.docker`
    behind — still a machine-specific path, just a shorter one.
    """
    real = tmp_path
    given = tmp_path / "scratch" / "release" / "home"
    spec = ToolSpec(
        name="docker",
        verbs=(Verb(name="", options=(Option(name="config", default=f"{given}/.d"),)),),
    )
    clean = _drivers._anonymous(spec, given, real)
    assert clean.verbs[0].options[0].default == "~/.d"


def test_no_stub_carries_a_home_directory():
    """The invariant the scrub exists to hold, checked against what ships."""
    import re
    from pathlib import Path

    looks_like_home = re.compile(r"/Users/[a-z]|/home/[a-z]|C:\\\\Users\\\\[a-z]", re.I)
    stubs = Path(_drivers.__file__).parent / "_stubs"
    guilty = {
        path.name
        for path in stubs.glob("*.pyi")
        # `encoding=` is not optional here: a stub carries whatever its tool's
        # help does, and Windows decodes with cp1252 by default — where the
        # UTF-8 tail byte of a man page's U+2010 is simply undefined.
        if looks_like_home.search(path.read_text(encoding="utf-8"))
    }
    assert guilty == set()


def test_a_tool_that_signs_its_help_is_not_describing_itself():
    """Some tools open with a signature: `git-cliff 2.13.1`, or
    markdownlint-cli2's name-and-version plus a project URL. Stored as the
    description it carried the version, so every release read back as a
    reworded summary — and it never said what the tool does anyway.

    Skipping it finds the real description where there is one, and honestly
    reports none where there is not. The byline between the two goes with
    it: reading past the version alone landed on git-cliff's authors, which
    is a worse answer than the banner it replaced.
    """
    # git-cliff's own opening, in clap's order: what it is, who wrote it,
    # what it does. Only the third line is a description.
    signed = (
        "git-cliff 2.13.1\n"
        "git-cliff contributors <git-cliff@protonmail.com>\n"
        "A highly customizable changelog generator\n\nUsage:\n"
    )
    assert _toolhelp._summary(signed) == "A highly customizable changelog generator"

    # markdownlint-cli2 has no description at all: a banner, its URL, then
    # the syntax line and a heading.
    unsigned = (
        "markdownlint-cli2 v0.23.2 (markdownlint v0.41.1)\n"
        "https://github.com/DavidAnson/markdownlint-cli2\n\n"
        "Syntax: markdownlint-cli2 glob0 [glob1]\n\n"
        "Glob expressions (from the globby library):\n"
    )
    assert _toolhelp._summary(unsigned) == ""


def test_prose_that_merely_ends_in_a_version_is_still_prose():
    """The banner rule matches a whole line, so bun — which describes itself
    for every word before its build stamp — is trimmed by `_unstamped`
    rather than thrown away here.
    """
    bun = "Bun is a fast JavaScript runtime, and test runner. (1.4.0+34cbb9a40)"
    assert _toolhelp._summary(f"{bun}\n\nUsage: bun <command>\n") == bun


def test_a_tools_own_build_stamp_is_not_part_of_its_description():
    """Bun signs its help line with its build. Stored, the stamp moves every
    release and an ordinary version bump reads as a reworded description —
    so the refresh reported a change nobody made, every time.
    """
    at = "Bun is a fast JavaScript runtime, package manager, and test runner."
    assert _toolhelp._unstamped(f"{at} (1.3.14+0d9b296af)", "1.3.14") == at
    assert _toolhelp._unstamped(f"{at} (1.4.0+34cbb9a40)", "1.4.0") == at
    # The point of the exercise: two releases, one description.
    assert _toolhelp._unstamped(f"{at} (1.3.14+0d9b296af)", "1.3.14") == (
        _toolhelp._unstamped(f"{at} (1.4.0+34cbb9a40)", "1.4.0")
    )


def test_only_a_parenthetical_holding_this_version_is_a_stamp():
    """Narrow on purpose: a description that genuinely ends in brackets keeps
    them, a manual's `SSH(1)` is not a stamp, and a tool that names some
    *other* project's version is left alone rather than mangled.
    """
    keep = "A tool (with a real parenthetical)"
    assert _toolhelp._unstamped(keep, "1.2.3") == keep
    assert _toolhelp._unstamped("SSH(1) General Commands Manual", "10.5p1") == (
        "SSH(1) General Commands Manual"
    )
    other = "markdownlint-cli2 v0.23.2 (markdownlint v0.41.1)"
    assert _toolhelp._unstamped(other, "0.23.2") == other
    # No version read, nothing to strip.
    assert _toolhelp._unstamped(f"{keep} (1.2.3)", "") == f"{keep} (1.2.3)"


def test_a_manual_names_itself_with_whichever_dash_its_format_uses():
    """Groff pages write `git - the stupid content tracker`; mdoc pages —
    every BSD manual, so the whole ssh family — write an en dash. Reading
    only the hyphen left ssh with no NAME match at all.
    """
    groff = "NAME\n       git - the stupid content tracker\n\nSYNOPSIS\n"
    mdoc = "NAME\n     ssh \u2013 OpenSSH remote login client\n\nSYNOPSIS\n"
    emdash = "NAME\n     ssh \u2014 OpenSSH remote login client\n\nSYNOPSIS\n"
    assert _toolhelp._summary(groff) == "the stupid content tracker"
    assert _toolhelp._summary(mdoc) == "OpenSSH remote login client"
    assert _toolhelp._summary(emdash) == "OpenSSH remote login client"


def test_a_name_line_that_wrapped_is_read_whole():
    """A manual is justified to the width it was rendered at, so a long NAME
    line wraps — and reading only as far as the line ended truncated the
    description mid-sentence. git-describe arrived as "based on an", which
    is not a smaller answer but a wrong one.

    The block is read whole and unwrapped, so where the break fell stops
    mattering, which is the whole point of not depending on the width.
    """
    wrapped = (
        "NAME\n"
        "       git-describe - Give an object a human readable name based on an\n"
        "       available ref\n"
        "\nSYNOPSIS\n"
    )
    one_line = (
        "NAME\n"
        "       git-describe - Give an object a human readable name based on an "
        "available ref\n\nSYNOPSIS\n"
    )
    whole = "Give an object a human readable name based on an available ref"
    assert _toolhelp._summary(wrapped) == whole
    assert _toolhelp._summary(one_line) == whole

    # A description of its own may hold " - "; the name half stops at the
    # first one, so the rest survives intact.
    dashed = "NAME\n     tool - do a thing - and another\n\nSYNOPSIS\n"
    assert _toolhelp._summary(dashed) == "do a thing - and another"


def test_a_summary_does_not_depend_on_the_width_it_was_rendered_at():
    """A manual is justified to whatever width the reader had, and the
    padding is not the tool's words. Stored raw, the same page read at two
    widths made two strings and the refresh called it a rewording.

    The running header is the case that bit: with no NAME match it became
    the summary, tabs and all.
    """
    narrow = "SSH(1)      General Commands Manual      SSH(1)\n\nDESCRIPTION\n"
    wide = "SSH(1)\t\t\t\tGeneral Commands Manual\t\t\t\tSSH(1)\n\nDESCRIPTION\n"
    assert _toolhelp._summary(narrow) == _toolhelp._summary(wide)
    assert _toolhelp._summary(narrow) == "SSH(1) General Commands Manual SSH(1)"

    # And a NAME line that groff wrapped keeps its single spaces too.
    named = "NAME\n     ssh \u2013 OpenSSH   remote     login client\n"
    assert _toolhelp._summary(named) == "OpenSSH remote login client"


def test_a_summary_is_found_past_a_wrapped_usage():
    """A wrapped usage stands between the `usage:` line and the
    description, and what it wraps onto decided what was found: a
    continuation opening `[--sdist…` reads as prose and became the summary,
    one opening `--config-json…` reads as an option and ended the search.

    Two platforms wrapping differently disagreed about `build`'s
    description for that reason, and neither had found it — the tool says
    `A simple, correct Python build frontend.` two lines further down.
    """
    text = (
        "usage: pyproject-build [-h] [--outdir PATH] [\n"
        "                       --config-json JSON] [--installer {pip,uv}]\n"
        "                       [srcdir]\n"
        "\n"
        "    A simple, correct Python build frontend.\n"
        "\n"
        "options:\n"
        "  --outdir PATH   where to put it\n"
    )
    assert _toolhelp._summary(text) == "A simple, correct Python build frontend."

    # and the other wrap, which used to yield the fragment itself
    other = text.replace(
        "[\n                       --config-json JSON]", "\n     [--config-json JSON]"
    )
    assert _toolhelp._summary(other) == "A simple, correct Python build frontend."


def test_a_wrapped_usage_line_is_not_an_option_row():
    """`build`\'s usage wraps at any width, and its continuation is an
    indented line of bracketed flags — the shape of an option row.

    The section-title filter cannot help here: the usage sits in the
    untitled preamble, so there is no `Usage:` heading to skip. Every flag
    on the continuation was swept into whichever option came first,
    leaving one entry carrying six flags and no help, and the other five
    absent. A weekly refresh read it that way and opened a PR to record it.
    """
    text = (
        # The bracket is stranded on the line above, so the continuation
        # opens with a bare `--flag`: exactly an option row's shape.
        "usage: pyproject-build [-h] [--version] [--outdir PATH] [\n"
        "                       --config-json JSON] [--installer {pip,uv} |\n"
        "                       --no-isolation]\n"
        "                       [--env-dir PATH] [--skip-dependency-check]\n"
        "\n"
        "A simple, correct build frontend\n"
        "\n"
        "options:\n"
        "  --config-json JSON    settings for the backend\n"
        "  --installer {pip,uv}  installer to use\n"
        "  --env-dir PATH        where to build\n"
    )
    verb = _toolhelp.parse_help(text, name="")
    by_name = {o.name: o for o in verb.options}
    assert set(by_name) == {"config_json", "installer", "env_dir"}
    assert list(by_name["config_json"].flags) == ["--config-json"]
    assert by_name["config_json"].help == "settings for the backend"
    # and nothing swallowed its neighbours
    assert all(len(o.flags) <= 2 for o in verb.options)


def test_a_verb_that_answers_with_the_root_help_is_not_that_verb(monkeypatch):
    """Asked for a subcommand it does not have, docker prints its own help
    and exits 0 — so the reading looked successful and `compose up` was
    recorded with docker's global options and docker's summary. Nothing
    downstream could tell: it is a real help text, just not this verb's.
    """
    from livery.toolroom.bench import _toolhelp

    root = "Usage:  docker [OPTIONS] COMMAND\n\nA runtime\n\nOptions:\n  --debug   On\n"
    own = (
        "Usage:  docker ps [OPTIONS]\n\nList containers\n\n"
        "Options:\n  --all   Show all\n"
    )

    def run_help(argv, **_kw):
        if argv[1:] == ["ps"]:
            return own
        return root  # `compose up` with no plugin installed

    monkeypatch.setattr(_toolhelp, "run_help", run_help)
    spec = _toolhelp.from_help("docker", verbs=("ps", "compose.up"))
    assert [verb.name for verb in spec.verbs] == ["", "ps"]


def test_a_tool_with_plugins_is_read_under_the_home_they_were_fetched_into(
    monkeypatch, tmp_path
):
    """A plugin is not on `PATH` — the host tool looks for it under the
    user's home, so without this the machine's own compose answers for
    every release a walk installs.
    """
    from livery.toolroom.bench import _tasks as task_module

    bindir = tmp_path / "bin"
    bindir.mkdir()
    binary = bindir / "docker"
    binary.write_text("#!/bin/sh\n")
    (tmp_path / "home" / ".docker" / "cli-plugins").mkdir(parents=True)
    monkeypatch.setattr(task_module.shutil, "which", lambda _name: str(binary))

    seen: dict[str, object] = {}

    def fake_extract(driver, home=None):
        seen.update(env=os.environ.get("HOME"), given=home)
        return ToolSpec(name="d")

    monkeypatch.setattr(task_module._drivers, "extract", fake_extract)
    driver = task_module._drivers.find("docker")
    assert driver is not None
    task_module._extract(driver)
    assert seen["env"] == str(tmp_path / "home")
    # Handed over as well as overlaid: inside a run the overlay reaches the
    # children's environment, never this process's, so the scrub cannot
    # discover it — see `_drivers._anonymous`.
    assert seen["given"] == tmp_path / "home"


def test_the_walk_reads_the_home_it_made_not_the_one_on_path(monkeypatch, tmp_path):
    """The bug a three-platform fold found.

    `_plugin_home` resolved the binary with `shutil.which` and looked
    beside it — but `which` reads `os.environ`, while the walk's `PATH`
    overlay goes to `ctx.env`. So it never saw the release's own directory
    and settled on the provisioned prefix, which has a home of its own
    holding the *latest* plugins: ten docker releases read with one compose
    between them, and the five that recorded it recorded the same surface
    five times.

    A caller that knows where it put things hands the home over.
    """
    from livery.toolroom.bench import _tasks as task_module

    # What a lookup would find: the prefix, plugins and all.
    decoy = tmp_path / "prefix"
    (decoy / "bin").mkdir(parents=True)
    (decoy / "bin" / "docker").write_text("#!/bin/sh\n")
    (decoy / "home" / ".docker" / "cli-plugins").mkdir(parents=True)
    monkeypatch.setattr(
        task_module.shutil, "which", lambda _name: str(decoy / "bin" / "docker")
    )
    # What this observation actually fetched.
    mine = tmp_path / "release" / "home"
    (mine / ".docker" / "cli-plugins").mkdir(parents=True)

    seen: dict[str, object] = {}

    def fake_extract(driver, home=None):
        seen.update(env=os.environ.get("HOME"), given=home)
        return ToolSpec(name="d")

    monkeypatch.setattr(task_module._drivers, "extract", fake_extract)
    driver = task_module._drivers.find("docker")
    assert driver is not None
    task_module._extract(driver, home=mine)
    assert seen["given"] == mine
    assert seen["env"] == str(mine)
    assert str(decoy) not in str(seen["given"])


def test_the_fetched_home_is_the_one_beside_what_was_installed(tmp_path):
    """And nothing is claimed when the fetch made no home — a tool with no
    plugins, or a tier that places bare binaries.
    """
    from livery.toolroom.bench import _tasks as task_module

    placed = tmp_path / "release" / "bin"
    placed.mkdir(parents=True)
    docker = task_module._drivers.find("docker")
    ruff = task_module._drivers.find("ruff")
    assert docker is not None and ruff is not None

    assert task_module._fetched_home(docker, placed) is None  # not made yet
    (tmp_path / "release" / "home").mkdir()
    assert task_module._fetched_home(docker, placed) == tmp_path / "release" / "home"
    assert task_module._fetched_home(ruff, placed) is None  # no plugins to hold


def test_a_tool_with_no_plugin_home_is_read_exactly_as_before(monkeypatch, tmp_path):
    """The host's own docker, or a prefix from before this existed."""
    from livery.toolroom.bench import _tasks as task_module

    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "docker").write_text("#!/bin/sh\n")
    monkeypatch.setattr(
        task_module.shutil, "which", lambda _name: str(bindir / "docker")
    )
    before = os.environ.get("HOME")
    seen: dict[str, object] = {}

    def fake_extract(driver, home=None):
        seen.update(home=os.environ.get("HOME"))
        return ToolSpec(name="d")

    monkeypatch.setattr(task_module._drivers, "extract", fake_extract)
    driver = task_module._drivers.find("docker")
    assert driver is not None
    task_module._extract(driver)
    assert seen["home"] == before


def test_rebasing_a_verb_that_is_not_there():
    spec = ToolSpec(name="x", verbs=(Verb(name="check"),))
    assert _drivers._rebase(spec, ("format",)).verbs == ()


# --- the reference pages --------------------------------------------------


def test_pages_writes_one_per_tool_plus_an_index(tmp_path):
    from livery.toolroom.bench import _tasks as tools_tasks

    tools_tasks.pages(tmp_path)
    index = (tmp_path / "index.md").read_text()
    for driver in _drivers.DRIVERS:
        page = tmp_path / f"{driver.key}.md"
        assert page.exists(), driver.key
        body = page.read_text()
        # mkdocstrings renders the class out of the stub, so the page is a
        # pointer rather than a copy — nothing to drift.
        assert f"::: livery.toolroom.tools._stubs.{driver.key}." in body
        assert f"({driver.key}.md)" in index
        if driver.url:
            assert driver.url in index, "the table links out to the tool itself"


def test_pages_regenerates_the_tools_nav_between_markers(tmp_path):
    from livery.toolroom.bench import _tasks as tools_tasks

    config = tmp_path / "z.toml"
    config.write_text(
        'nav = [{ "Tools" = [\n'
        "    # nav:begin tools\n"
        '    { "stale" = "_generated/tools/stale.md" },\n'
        "    # nav:end tools\n] }]\n"
    )
    tools_tasks.pages(tmp_path / "out", nav=config)
    keys = tools_tasks.nav_keys(config)
    assert "stale" not in keys  # the old hand-entry is replaced
    assert keys == sorted(keys)  # alphabetical
    assert {"bash", "python", "ruff"} <= set(keys)  # the real drivers, sorted in


def test_checked_in_tools_nav_lists_every_stubbed_driver():
    # Fails when a driver is added without `fm toolroom.pages` regenerating
    # the sidebar — the drift guard the hardcoded nav never had.
    from pathlib import Path

    from livery.toolroom.bench import _tasks as tools_tasks

    config = _toolroom_root() / "docs" / "nav.toml"
    expected = sorted(
        d.key for d in _drivers.DRIVERS if tools_tasks._stub_path(d.key).exists()
    )
    assert tools_tasks.nav_keys(config) == expected


def test_the_index_states_the_version_each_stub_was_read_from(tmp_path):
    from livery.toolroom.bench import _tasks as tools_tasks

    tools_tasks.pages(tmp_path)
    index = (tmp_path / "index.md").read_text()
    assert "| Tool | Read from | In-process | Verbs |" in index
    # mkdocs is a click tool footman prefers to run in-process.
    row = next(
        line
        for line in index.splitlines()
        if line.startswith("|") and "`mkdocs`" in line
    )
    assert "default" in row
    assert "`build`" in row


def test_a_hand_written_stub_says_so_rather_than_inventing_a_version(tmp_path):
    from livery.toolroom.bench import _tasks as tools_tasks

    stub = tmp_path / "x.pyi"
    stub.write_text("# Hand-written, not generated: x is not installed\n")
    # A hand-written stub exists because there is no Python package to
    # extract from — so in-process is a definite "no", never "unknown".
    assert tools_tasks._header(stub) == ("hand-written", "no")

    stub.write_text(
        "# Generated by `fm toolroom.sync`\n"
        "#\n"
        "# Read from ruff 0.15.0 on Linux. In-process: no.\n"
    )
    assert tools_tasks._header(stub) == ("0.15.0 (Linux)", "no")


def test_in_process_mode_is_detected_not_listed():
    from livery.toolroom.bench import _tasks as tools_tasks

    capable = ToolSpec(name="x", in_process=True)
    plain = ToolSpec(name="x", in_process=False)
    assert (
        tools_tasks._mode(_drivers.Driver("x", in_process=True), capable) == "default"
    )
    assert tools_tasks._mode(_drivers.Driver("x"), capable) == "available"
    assert tools_tasks._mode(_drivers.Driver("x"), plain) == "no"


def test_every_driver_mirrors_how_tools_py_builds_its_tool():
    """A driver's `name`/`in_process` are a *label* — they feed the stub header
    and `fm toolroom list`; `tools.py` is what actually runs. pytest
    drifted once (the Tool was in-process, the driver never said so, so its
    stub read "available"), so the two are pinned to each other here.
    """
    from livery.toolroom import tools

    for driver in _drivers.DRIVERS:
        tool = getattr(tools, driver.key)
        assert tool._argv0 == driver.name, (
            f"tools.{driver.key} runs {tool._argv0!r}, its driver says {driver.name!r}"
        )
        assert tool._prefer_in_process == driver.in_process, (
            f"tools.{driver.key} is built in_process={tool._prefer_in_process}, "
            f"its driver says {driver.in_process}"
        )


# --- positional shape from the usage line ---------------------------------
#
# A wrong shape *forbids a valid call*, so these pin the exact boundary
# between the confident answers (none / required) and the permissive default.


def shape(usage: str) -> tuple[str, str]:
    return _toolhelp._usage_shape(f"Usage: tool {usage}\n\nDo a thing.\n")


def test_shape_none_only_when_the_grammar_is_options_only():
    assert shape("[OPTIONS]") == ("none", "")
    assert shape("[options]") == ("none", "")
    # A positional anywhere means not-none, even alongside options.
    assert shape("[OPTIONS] NAME[:TAG|@DIGEST]") == ("required", "name")


def test_shape_required_for_a_clean_leading_metavar():
    assert shape("[OPTIONS] IMAGE [COMMAND] [ARG...]") == ("required", "image")
    assert shape("[<options>] [--] <repo> [<dir>]") == ("required", "repo")
    assert shape("[options] <pyfile> [program options]") == ("required", "pyfile")


def test_shape_stays_any_where_a_wrong_guess_would_forbid_a_call():
    # An option woven into an alternation — packages OR --requirements.
    assert shape("[OPTIONS] <PACKAGES|--requirements <REQS>>") == ("any", "")
    # A bracketed-optional or variadic leading argument.
    assert shape("[OPTIONS] [COMMAND]") == ("any", "")
    assert shape("[options] [FILES]...") == ("any", "")
    # A numbered metavar is a list written long-hand, not one required arg.
    assert shape("[options] <path1> <path2> ... <pathN>") == ("any", "")


def test_shape_ignores_option_values_scattered_by_whitespace():
    # `<git-dir>` is the value of `--separate-git-dir`, not a positional —
    # depth tracking keeps it out.
    usage = "[-q | --quiet] [--separate-git-dir <git-dir>] [<directory>]"
    assert shape(usage) == ("any", "")  # only [<directory>], which is optional


def test_shape_reads_only_the_first_of_gits_or_forms():
    text = (
        "usage: git branch [<options>] [--list] [<pattern>...]\n"
        "   or: git branch [<options>] [-f] <branchname> [<start-point>]\n"
    )
    # The first form is all-optional; the `or:` create-form is not stitched in.
    assert _toolhelp._usage_shape(text) == ("any", "")


def test_click_arguments_give_the_shape_exactly():
    # click hands arguments over as data — no usage parsing needed.
    none = SimpleNamespace(help="Build.", name="build", params=[])
    assert _toolspec._verb_from_click("build", none).positional == "none"

    arg = SimpleNamespace(
        param_type_name="argument", name="image", required=True, nargs=1
    )
    one = SimpleNamespace(help="Run.", name="run", params=[arg])
    verb = _toolspec._verb_from_click("run", one)
    assert (verb.positional, verb.lead) == ("required", "image")

    variadic = SimpleNamespace(
        param_type_name="argument", name="paths", required=True, nargs=-1
    )
    many = SimpleNamespace(help="Add.", name="add", params=[variadic])
    assert _toolspec._verb_from_click("add", many).positional == "any"


def test_stub_renders_positional_only_and_keyword_only():
    from livery.toolroom.bench._toolspec import Option

    # A keyword-only verb (positional="none") with an option forbids positionals
    # via `*,`; the option must be passed by keyword.
    none = ToolSpec(
        name="x",
        verbs=(
            Verb(
                name="build",
                positional="none",
                options=(Option("target", ("--target",), type_name="str"),),
            ),
        ),
    )
    text = _stubgen.render(none)
    ast.parse(text)
    # Keyword-only in the verb's own signature; the root class keeps its
    # untyped `*args: Any` passthrough, which is not the verb's surface.
    assert "*,\n" in text and "*args: str" not in text

    # With no options, `**flags` alone forbids a positional — no redundant `*,`
    # (which would be a syntax error with nothing keyword-only after it).
    bare = ToolSpec(name="x", verbs=(Verb(name="build", positional="none"),))
    text = _stubgen.render(bare)
    ast.parse(text)
    assert "*args: str" not in text  # the verb still accepts no positional

    req = ToolSpec(
        name="x", verbs=(Verb(name="run", positional="required", lead="image"),)
    )
    text = _stubgen.render(req)
    ast.parse(text)
    assert "image: str | PathLike[str],\n" in text and "/,\n" in text


def test_stub_falls_back_when_the_lead_collides_with_an_option():
    from livery.toolroom.bench._toolspec import Option

    verb = Verb(
        name="pip_install",
        positional="required",
        lead="group",
        options=(Option("group", ("--group",), type_name="str"),),
    )
    text = _stubgen.render(ToolSpec(name="uv", verbs=(verb,)))
    ast.parse(text)  # a duplicate `group` parameter would be a syntax error
    assert "*args: str | PathLike[str]," in text


def test_wraps_detected_from_a_trailing_command_metavar():
    run = _toolhelp.parse_help(
        "Usage: uv run [OPTIONS] [COMMAND]\n\nRun.\n", name="run"
    )
    assert run.wraps is True
    cov = _toolhelp.parse_help(
        "Usage: coverage run [options] <pyfile> [program options]\n\nRun.\n", name="run"
    )
    assert cov.wraps is True
    # A verb that merely takes files is not a wrapper.
    check = _toolhelp.parse_help(
        "Usage: ruff check [OPTIONS] [FILES]...\n\nCheck.\n", name="check"
    )
    assert check.wraps is False


def test_spec_wrappers_lists_the_dotted_wrapper_paths():
    spec = ToolSpec(
        name="docker",
        verbs=(
            Verb(name="run", wraps=True),
            Verb(name="build", wraps=False),
            Verb(name="compose.run", wraps=True),
        ),
    )
    assert spec.wrappers() == frozenset({"run", "compose.run"})


# --- git via its manual (`git help <verb>`) ----------------------------------

GIT_MAN = """\
GIT-CLONE(1)                      Git Manual                      GIT-CLONE(1)

NAME
       git-clone - Clone a repository into a new directory

SYNOPSIS
       git clone [--template=<template-directory>] [-l] [-s] [--no-hardlinks]
                 [-q] [-n] [--bare] [-o <name>] [--depth <depth>]
                 [--filter=<filter-spec>] [--] <repository> [<directory>]

DESCRIPTION
       Clones a repository into a newly created directory.

OPTIONS
       -l, --local
           When the repository to clone from is on a local machine, this
           flag bypasses the normal "Git aware" transport mechanism. Don't
           use it unless you know what you're doing.

       --bare
           Make a bare Git repository. That is, instead of creating
           <directory> and placing the administrative files in
           <directory>/.git, make the <directory> itself the $GIT_DIR.

       --depth <depth>
           Create a shallow clone with a history truncated to the specified
           number of commits.
"""


def test_git_manual_options_and_single_form_synopsis():
    verb = _toolhelp.parse_help(GIT_MAN, name="clone", man=True)
    got = flags(verb)
    assert {"local", "bare", "depth"} <= set(got)
    assert got["depth"].type_name == "str"  # `--depth <depth>` takes a value
    # A single-form SYNOPSIS with a required trailing metavar → required.
    assert (verb.positional, verb.lead) == ("required", "repository")


def test_git_manual_help_is_the_first_sentence_ascii_folded():
    verb = _toolhelp.parse_help(GIT_MAN, name="clone", man=True)
    got = flags(verb)
    # The manual's paragraph is cut to one sentence, curly quotes folded.
    assert got["local"].help == (
        "When the repository to clone from is on a local machine, this flag "
        'bypasses the normal "Git aware" transport mechanism'
    )
    assert got["local"].help.isascii()  # curly quotes were folded


def test_multi_form_synopsis_stays_any():
    text = (
        "SYNOPSIS\n"
        "       git checkout [<options>] <branch>\n"
        "       git checkout [<options>] [--] <pathspec>...\n"
        "\nDESCRIPTION\n       x.\n\n"
        "OPTIONS\n       -q, --quiet\n           Be quiet.\n"
    )
    verb = _toolhelp.parse_help(text, name="checkout", man=True)
    assert verb.positional == "any"  # two forms → no single shape


# --- OpenSSH via its manual (mdoc, all-short options) ------------------------

SSH_MAN = """\
SSH(1)                      General Commands Manual                     SSH(1)

NAME
     ssh - OpenSSH remote login client

SYNOPSIS
     ssh [-46AaCqTv] [-B bind_interface] [-o option] [-p port]
         [-W host:port] [-L address] destination [command [argument ...]]
     ssh [-Q query_option]

DESCRIPTION
     ssh (SSH client) is a program for logging into a remote machine.

     The options are as follows:

     -4      Forces ssh to use IPv4 addresses only.

     -A      Enables forwarding of connections from an authentication agent.

     -B bind_interface
             Bind to the address of bind_interface before attempting to
             connect to the destination host.

     -L [bind_address:]port:host:hostport
     -L [bind_address:]port:remote_socket
     -L local_socket:host:hostport
             Specifies that connections to the given TCP port or Unix socket
             on the local (client) host are to be forwarded.

     -o option
             Can be used to give options in the format used in the
             configuration file.

     -P tag  Specify a tag name that may be used to select configuration in
             ssh_config(5).  Refer to the Tag and Match keywords in
             ssh_config(5) for more information.
     -p port
             Port to connect to on the remote host.

     -W host:port
             Requests that standard input and output on the client be
             forwarded to host on port over the secure channel.  Implies -N,
             -T, ExitOnForwardFailure and ClearAllForwardings, though these
             can be overridden in the configuration file.
"""

KEYGEN_MAN = """\
SSH-KEYGEN(1)               General Commands Manual              SSH-KEYGEN(1)

NAME
     ssh-keygen - OpenSSH authentication key utility

SYNOPSIS
     ssh-keygen [-q] [-b bits] [-C comment]
     ssh-keygen -Y find-principals -s signature_file -f allowed_signers_file

DESCRIPTION
     ssh-keygen generates, manages and converts authentication keys.

     -b bits
             Specifies the number of bits in the key to create.

     -Y find-principals
             Find the principal(s) associated with the public key of a
             signature.
"""


def test_a_mangled_read_is_taken_again_in_the_local_code_page(monkeypatch):
    """UTF-8 first, the locale codec when UTF-8 lost bytes.

    Captured output is decoded as UTF-8 because dev tools emit it whatever
    the OS code page says — but djLint prints its banner separator as one
    cp1252 byte on Windows, and `errors="replace"` turned that into U+FFFD.
    A replacement character is the decoder admitting it lost something, so
    it is the signal to ask again with the locale codec.
    """
    bad = "djLint \ufffd HTML template linter and formatter."
    good = "djLint \u00b7 HTML template linter and formatter."
    calls: list[object] = []

    class _Done:
        def __init__(self, out):
            self.stdout, self.stderr = out, ""

    def fake_run(argv, **kwargs):
        calls.append(kwargs.get("encoding", "utf-8"))
        return _Done(good)

    monkeypatch.setattr(_toolhelp, "_run", fake_run)
    assert _toolhelp._decoded(bad, ["djlint"], "--help", 5.0) == good
    assert calls == [None]  # asked again with the locale codec, once

    # A clean read never spawns a second process.
    calls.clear()
    assert _toolhelp._decoded(good, ["djlint"], "--help", 5.0) == good
    assert calls == []

    # And a re-read that is *also* mangled keeps the first answer rather
    # than trading one unreadable reading for another.
    monkeypatch.setattr(_toolhelp, "_run", lambda argv, **kw: _Done(bad))
    assert _toolhelp._decoded(bad, ["djlint"], "--help", 5.0) == bad


def test_groff_hyphenation_is_put_back_together():
    """A word groff broke across lines is one word again, in ASCII.

    U+2010 is groff's own marker for a hyphen it inserted — a literal one in
    the source renders as plain `-` — so rejoining is exact, not a guess.
    ssh's page broke "encryption" across lines with it, and that shipped in
    a stub, where it cost two CI failures: ruff reads the character as
    ambiguous (RUF002), and its UTF-8 tail byte 0x90 is undefined in cp1252,
    which is what Windows decodes with unless a reader says `encoding=`.

    Spelled as an escape throughout — writing it literally trips the very
    rule this test is about.
    """
    hyphen = "\u2010"
    joined = _toolhelp._dehyphenate(f"authenticated en{hyphen}\n            cryption)")
    assert joined == "authenticated encryption)"
    # Anywhere else it is still not a hyphen anyone typed.
    assert _toolhelp._dehyphenate(f"cipher{hyphen}auth") == "cipher-auth"
    assert hyphen not in joined


def test_manual_short_only_options_are_keyed():
    # ssh's whole surface is short-only; the `shorts` policy alone decides,
    # and the default "only" describes exactly this shape.
    got = flags(_toolhelp.parse_help(SSH_MAN, name="ssh", man=True))
    assert {"A", "B", "L", "o", "P", "p", "W"} <= set(got)
    assert "4" not in got  # a digit can't be a keyword; stays unspellable


def test_manual_bare_metavar_read_from_a_two_token_head():
    # mdoc typesets `-B bind_interface`: two plain tokens once rendered. The
    # bare word is the value's name — but only in that exact head shape, so
    # a prose sentence misread as a head still can't eat its next word.
    got = flags(_toolhelp.parse_help(SSH_MAN, name="ssh", man=True))
    assert got["B"].type_name == "str"
    assert got["A"].type_name == "bool"
    assert got["o"].type_name == "list[str]"  # "Can be used to give options…"


def test_manual_head_flush_against_the_previous_block_still_opens():
    # mandoc renders `-p port` with no blank line after `-P tag`'s paragraph
    # (uniquely on the page). A head in the open block's own flag column is
    # a head even without the paragraph break.
    got = flags(_toolhelp.parse_help(SSH_MAN, name="ssh", man=True))
    assert got["p"].help == "Port to connect to on the remote host"
    assert got["P"].help.startswith("Specify a tag name")


def test_manual_multi_form_head_keeps_the_shared_description():
    # `-L` states three complete forms on consecutive head lines; only the
    # last carries the description. The first form stays the option, the
    # twins donate the help it lacks.
    got = flags(_toolhelp.parse_help(SSH_MAN, name="ssh", man=True))
    assert got["L"].help.startswith("Specifies that connections")


def test_manual_comma_wrap_in_prose_does_not_smuggle_spellings():
    # `-W`'s description wraps as "Implies -N,\n-T, …": the comma-join rule
    # is for a *head's* wrapped spelling list, at the head's own indent — a
    # description line ending in a comma must not add `-T` to `-W`.
    got = flags(_toolhelp.parse_help(SSH_MAN, name="ssh", man=True))
    assert got["W"].flags == ("-W",)
    assert "T" not in got  # SSH_MAN never defines -T as a block of its own


def test_manual_word_arguments_fabricate_nothing():
    # `-Y find-principals`: the mid-word dash is a hyphen, not a spelling,
    # and a manual never spells Go-style single-dash longs — without both
    # guards this page yielded `-principals` as an option.
    verb = _toolhelp.parse_help(KEYGEN_MAN, name="ssh_keygen", man=True)
    got = flags(verb)
    assert set(got) == {"b", "Y"}
    assert got["Y"].type_name == "str"  # the verb-word is its value
    assert got["b"].type_name == "str"


def test_manual_multi_form_synopsis_reads_any():
    verb = _toolhelp.parse_help(SSH_MAN, name="ssh", man=True)
    assert verb.positional == "any"  # two SYNOPSIS forms → no single shape


def test_manual_synopsis_marks_the_wrapper():
    # `ssh … destination [command [argument ...]]` forwards to the remote:
    # the SYNOPSIS is the only place a manual says so (no usage line).
    assert _toolhelp.parse_help(SSH_MAN, name="ssh", man=True).wraps
    assert not _toolhelp.parse_help(KEYGEN_MAN, name="ssh_keygen", man=True).wraps
    assert not _toolhelp.parse_help(GIT_MAN, name="clone", man=True).wraps


def test_man_version_prefers_the_installers_stamp(tmp_path):
    # mdoc pages state no version anywhere; the installer stamps what it
    # fetched, per tool, because the merged tree holds several manuals.
    (tmp_path / "VERSION-ssh").write_text("10.4p1", encoding="utf-8")
    assert _toolhelp.man_version(tmp_path, "ssh") == "10.4p1"
    # No stamp for this name: git's own .TH line still answers.
    section = tmp_path / "man1"
    section.mkdir()
    (section / "git.1").write_text(
        '.TH "GIT" "1" "2025-06-15" "Git 2\\&.50\\&.1" "Git Manual"\n',
        encoding="utf-8",
    )
    assert _toolhelp.man_version(tmp_path, "git") == "2.50.1"


def test_first_sentence_skips_abbreviations():
    assert _toolhelp._first_sentence("Use e.g. a value. Then stop.") == (
        "Use e.g. a value"
    )
    assert _toolhelp._first_sentence("No period here") == "No period here"


def test_reserved_flag_name_falls_through_to_the_catchall():
    # git rev-parse has a `--flags` option; it can't be a typed parameter
    # (it would duplicate `**flags`), so it is dropped to the catch-all.
    spec = ToolSpec(
        name="git",
        verbs=(
            Verb(
                name="rev_parse",
                options=(
                    Option("flags", ("--flags",), type_name="bool"),
                    Option("quiet", ("--quiet",), type_name="bool"),
                ),
            ),
        ),
    )
    text = _stubgen.render(spec)
    ast.parse(text)  # a duplicate `flags` parameter would be a syntax error
    assert "quiet: Flag" in text
    assert "flags: Flag" not in text  # the `--flags` option isn't a typed param
    assert "**flags: Any" in text  # it falls through to the catch-all


def test_arg_help_keeps_a_markdown_header_mid_line():
    # git's merge-stage notation (`#2 (ours)`) used to need escaping when a
    # wrap could drop it to the start of a docstring line, where Markdown
    # reads a header. Entries are one line now and always lead with the
    # option's own name, so a `#` can only ever sit mid-line — not a block.
    from livery.toolroom.bench._toolspec import Option

    option = Option(
        "ours",
        ("--ours",),
        type_name="bool",
        help=(
            "When restoring files in the working tree from the index, use stage "
            "#2 (ours) or #3 (theirs) for unmerged paths"
        ),
    )
    (line,) = _stubgen._arg_lines(option)
    assert line.lstrip().startswith("ours: ")
    assert "#2 (ours)" in line  # verbatim — mid-line needs no escape


def test_md_safe_touches_only_leading_header_and_quote():
    safe = _stubgen._md_safe(
        ["            #2 heading", "            > quote", "            mid # hash"]
    )
    assert safe[0].endswith("\\\\#2 heading")
    assert safe[1].endswith("\\\\> quote")
    assert safe[2].endswith("mid # hash")  # a mid-line hash is not a block


def test_resolve_is_path_and_nothing_else(tmp_path, monkeypatch):
    """`_resolve` is `shutil.which`, on every platform.

    There was a host-read tier once, and on macOS it preferred a Homebrew
    keg over PATH so an unlinked build was still the one read. Nothing sits
    on that tier now — docker fetches its own builds, git reads kernel.org's
    manuals — so the branch went with it. Asserted on darwin, where the keg
    rule used to apply and a stale `/opt/homebrew/bin` shim is the thing
    that must never win: a provisioned tool comes from PATH (the prefix, or
    the venv), whatever is installed beside it.
    """
    monkeypatch.setattr(sys, "platform", "darwin")
    keg = tmp_path / "opt" / "ruff" / "bin"
    keg.mkdir(parents=True)
    (keg / "ruff").write_text("#!/bin/sh\n")
    (keg / "ruff").chmod(0o755)
    monkeypatch.setattr(_drivers.shutil, "which", lambda n: f"/venv/bin/{n}")
    assert _drivers._resolve("ruff") == "/venv/bin/ruff"


def test_resolve_off_macos_uses_path(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(_drivers.shutil, "which", lambda n: f"/usr/bin/{n}")
    assert _drivers._resolve("git") == "/usr/bin/git"


# --- a snapshot only ever moves forward -----------------------------------


def test_version_tuple_reads_the_leading_integers_and_stops():
    """One comparator for the whole framework — the snapshot guard and
    `installed_version()` must not disagree about which build is newer.

    A build tail says nothing about which flags exist, and scraping its
    digits answers the question backwards: `0.6.0-wk.5` is a fork build *of*
    0.6.0, so `(0, 6, 0, 5)` would sort it *after* its own base.

    The price is that two builds of one base compare **equal**, and the point
    of pinning it is that equality is a real answer here, not a bug to route
    around: a caller must say what it does with a tie rather than read it as
    "not newer". The chain breaks the tie on publication date; the snapshot
    guard, which has no second date to consult, declines to move.
    """
    from livery.toolroom.tools import version_tuple

    assert version_tuple("2.55.0") == (2, 55, 0)
    assert version_tuple("0.6.0-wk.5") == (0, 6, 0)  # the tail is anybody's grammar
    assert version_tuple("1.13.0.git.kitware.jobserver-1") == (1, 13, 0)
    assert version_tuple("0.6.0-wk.5") <= version_tuple("0.6.0")  # never "newer"
    assert version_tuple("0.6.0-wk.3") == version_tuple("0.6.0-wk.5")  # a real tie
    assert version_tuple("") == ()  # unreadable: the caller must not skip on it
    assert version_tuple("nightly") == ()


@needs_ruff
def test_a_tool_older_than_the_snapshot_is_left_alone(stubs, capsys, monkeypatch):
    """A machine behind the one that took the snapshot has nothing to add.
    Reading it would rewrite the stub *backwards*, dropping flags that exist
    upstream — so audit ignores it and sync leaves the file untouched.
    """
    from livery.toolroom.bench import _drivers
    from livery.toolroom.bench import _tasks as tools_tasks

    tools_tasks.sync(only="ruff")
    written = (stubs / "ruff.pyi").read_text()
    capsys.readouterr()

    # The same tool, one release older than the stub records.
    monkeypatch.setattr(_drivers, "version", lambda name: "0.0.1")
    report = tools_tasks.audit(only="ruff")
    out = capsys.readouterr().out
    assert "older than the snapshot" in out
    assert report["behind"] == []  # not behind — unanswered
    assert report["checked"] == 0

    tools_tasks.sync(only="ruff")
    assert (stubs / "ruff.pyi").read_text() == written  # unchanged


def test_a_tool_missing_from_the_prefix_is_left_alone(stubs, tmp_path, capsys):
    """A partial provision must not read as drift: a provisioned tool that
    isn't in the prefix falls back to nothing, never to the host's copy.
    """
    from livery.toolroom.bench import _tasks as tools_tasks

    empty = tmp_path / "prefix"
    (empty / "bin").mkdir(parents=True)
    report = tools_tasks.audit(only="ruff", prefix=str(empty))
    assert "not in the prefix" in capsys.readouterr().out
    assert report["checked"] == 0 and report["behind"] == []


def test_the_prefix_launcher_counts_not_where_it_points(tmp_path):
    """The node tier's scripts live in a shared node_modules and a provisioned
    interpreter in uv's store, so following the symlink out of the prefix
    would call two properly provisioned tools missing.
    """
    from livery.toolroom.bench._tasks import _from_prefix

    root = tmp_path / "prefix"
    (root / "bin").mkdir(parents=True)
    elsewhere = tmp_path / "store" / "thing"
    elsewhere.parent.mkdir()
    elsewhere.write_text("#!/bin/sh\n")
    launcher = root / "bin" / "thing"
    launcher.symlink_to(elsewhere)

    assert _from_prefix(str(launcher), root)  # via the prefix's bin
    assert not _from_prefix("/usr/bin/thing", root)  # the host's own copy


def test_every_installed_driver_reports_a_readable_version(capsys):
    """A version-keyed history is only as good as this: a tool whose version
    can't be read would append events under an empty key, silently.

    Tools this machine lacks are skipped *and named*, the same doctrine
    `audit` follows — a check that quietly covered three of thirteen would be
    worse than no check.
    """
    from concurrent.futures import ThreadPoolExecutor

    from livery.toolroom.bench import _drivers

    def probe(name: str) -> tuple[str, str]:
        found, why = _drivers._read_version(name)
        if not found and why == "timed out after 30s":
            # Convicted on CI: gh --version hangs past 30s on a fresh
            # Windows runner with its update check disabled — Defender's
            # first-touch scan of a large binary, not a scrape failure. The
            # scan caches, so the second spawn answers; a tool that times
            # out twice has genuinely earned the failure.
            found, why = _drivers._read_version(name)
        return found, why

    read, unreadable, absent = [], [], []
    present = [d for d in _drivers.DRIVERS if _drivers._resolve(d.name) is not None]
    absent = [d.key for d in _drivers.DRIVERS if _drivers._resolve(d.name) is None]
    # One spawn per tool, side by side: the check waits for the slowest
    # tool, not for the sum of them.
    with ThreadPoolExecutor(max_workers=max(1, len(present))) as pool:
        answers = list(pool.map(probe, [d.name for d in present]))
    for driver, (found, why) in zip(present, answers, strict=True):
        if found:
            read.append(f"{driver.key} ({found})")
        else:
            # The diagnosis is the whole point: a bare em-dash here left
            # every hypothesis standing when this tripped on CI.
            unreadable.append(f"{driver.key}: {why}")

    with capsys.disabled():
        print(f"\n  version read from {len(read)}/{len(_drivers.DRIVERS)} drivers")
        if absent:
            print(f"  not installed here: {', '.join(absent)}")
    assert not unreadable, f"no version could be read from: {', '.join(unreadable)}"
    assert read, "no curated tool was installed — this check proved nothing"


def test_subcommand_groups_are_nested_classes():
    """`docker compose up` is not a `DockerCompose` sitting beside `Docker`.
    Nesting says so where it can be said — and it is what lets one
    mkdocstrings directive document the whole tool, since a nested class is
    a member and the renderer walks members.
    """
    import ast

    from livery.toolroom.bench import _drivers
    from livery.toolroom.bench import _tasks as tools_tasks

    source = tools_tasks._stub_path("docker").read_text(encoding="utf-8")
    tree = ast.parse(source)
    roots = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    assert [n.name for n in roots] == ["Docker"]  # one class at module level
    nested = [n.name for n in roots[0].body if isinstance(n, ast.ClassDef)]
    assert "Compose" in nested  # not DockerCompose, and not a sibling
    assert "compose: Compose[_R]" in source  # the attribute names the class
    # The group's leaves nest one level further down.
    compose = next(
        n for n in roots[0].body if isinstance(n, ast.ClassDef) and n.name == "Compose"
    )
    assert "Up" in {n.name for n in compose.body if isinstance(n, ast.ClassDef)}

    # gh nests its eight groups (and now its leaf verbs), all inside Gh.
    gh = ast.parse(tools_tasks._stub_path("gh").read_text(encoding="utf-8"))
    gh_root = next(n for n in gh.body if isinstance(n, ast.ClassDef))
    assert {
        "Auth",
        "Issue",
        "Label",
        "Pr",
        "Release",
        "Repo",
        "Run",
        "Workflow",
    } <= {n.name for n in gh_root.body if isinstance(n, ast.ClassDef)}

    # ...so the page needs exactly one directive, whatever the tool's shape.
    driver = _drivers.find("docker")
    assert driver is not None
    assert tools_tasks._page(driver).count(":::") == 1


def test_a_nested_class_flags_returns_self():
    """A nested class cannot name itself from inside its own body, and `Self`
    is what the chain means anyway: `docker.flags(host=…).compose.up()`.
    """
    source = (
        _toolroom_root() / "src/livery/toolroom/tools/_stubs/docker.pyi"
    ).read_text()
    assert "-> Self:" in source
    assert "-> Docker:" not in source and "-> DockerCompose:" not in source


def test_index_verbs_are_dotted_so_they_read_as_they_are_called():
    """Flattened to bare names, `compose.up` reads as `up` and uv's two
    `install` verbs collapse into one — the index then claims a tool has
    fewer verbs than it has.
    """
    from livery.toolroom.bench import _tasks as tools_tasks

    uv = tools_tasks._verbs_of(tools_tasks._stub_path("uv"))
    assert "pip.install" in uv and "tool.install" in uv
    docker = tools_tasks._verbs_of(tools_tasks._stub_path("docker"))
    assert "compose.up" in docker and "up" not in docker
    # `flags` is footman's own typed-globals accessor, not a verb of the tool.
    assert not any(v.endswith("flags") for v in uv + docker)


def test_click_extraction_requires_the_import_and_the_binary_to_agree(monkeypatch):
    """The entry point loads from this process's environment; the binary
    comes from PATH; nothing ties the two together. During a prime that
    difference is the whole point — the release venv's mkdocs 1.4.0 on PATH,
    ours importable — and the click path used to record OUR surface under
    the RELEASE's label: nine empty deltas in a row, twice over, before the
    guard. A mismatch falls to the help path, which asks the binary itself.
    """
    from types import SimpleNamespace

    from livery.toolroom import tools as bridge

    driver = _drivers.find("mkdocs")
    assert driver is not None

    entry = SimpleNamespace(
        dist=SimpleNamespace(version="1.6.1"),
        load=lambda: pytest.fail("a mismatched click tool must not be imported"),
    )
    monkeypatch.setattr(bridge, "_console_entrypoint", lambda _name: entry)
    monkeypatch.setattr(_drivers, "version", lambda _name: "1.4.0")
    assert _drivers._from_click(driver) is None  # mismatch: the help path's turn

    monkeypatch.setattr(_drivers, "version", lambda _name: "")
    assert _drivers._from_click(driver) is None  # unreadable binary: same answer


def test_a_stub_header_survives_being_read_on_more_than_one_platform():
    """The header is *parsed*, not just displayed — the reference table and
    the drift checks read the tool, version and mode back out of it.

    It named one platform for as long as one machine ever looked. The moment
    a release was observed on two, `on Linux and macOS.` stopped matching a
    single-word pattern, every stub read as hand-written, and the published
    table said so.
    """
    from livery.toolroom.bench import _tasks as tools

    for platform in ("macOS", "Linux and macOS", "Linux, Windows and macOS"):
        header = f"Read from ruff 0.16.0 on {platform}. In-process: no."
        found = tools._READ_FROM.search(header)
        assert found is not None, platform
        assert found["tool"] == "ruff"
        assert found["version"] == "0.16.0"
        assert found["platform"] == platform
        assert found["mode"] == "no"


# --- arity from the usage grammar --------------------------------------------

# The alignment is load-bearing: `--configPointer` is the longest name, so its
# description sits one space away while the others get a column of them.
MARKDOWNLINT = """markdownlint-cli2 v0.23.2 (markdownlint v0.41.1)

Syntax: markdownlint-cli2 glob0 [--config file] [--configPointer pointer] [--fix]

Optional parameters:
- --config        specifies the path to a configuration file
- --configPointer specifies a JSON Pointer within the --config file
- --fix           updates files to resolve fixable issues
- --no-globs      ignores the "globs" property if present
"""

BASEDPYRIGHT = """Usage: basedpyright [options] files...
  Options:
  --createstub <IMPORT>              Create type stub file(s) for import
  --dependencies                     Emit import dependency information
  --outputjson                       Output results in JSON format
  --watch                            Continue to run and watch for changes
"""

PYPROJECT_BUILD = """usage: pyproject-build [-h] [--quiet | --verbose] [--outdir PATH]
                       [--installer {pip,uv} | --no-isolation]
                       [srcdir]

options:
  --quiet       do not show logs
  --verbose     increase verbosity
  --outdir      output directory
  --installer   Python package installer to use
"""


def test_a_flag_with_no_metavar_takes_its_arity_from_the_usage_line():
    """An option block may describe a flag in prose and never name its value.

    markdownlint-cli2 does exactly that, and the usage line — which it calls
    `Syntax:` — is then the only statement of arity in the document. Read the
    block alone and `--config` is a switch, so the stub types a path as bool.
    """
    got = flags(_toolhelp.parse_help(MARKDOWNLINT, name="markdownlint-cli2"))
    assert got["config"].type_name == "str"  # `[--config file]`
    assert got["configPointer"].type_name == "str"  # `[--configPointer pointer]`
    assert got["fix"].type_name == "bool"  # bracketed alone: a switch
    assert got["no_globs"].type_name == "bool"  # absent from the usage line


def test_a_description_one_space_from_its_flag_is_still_a_description():
    """markdownlint-cli2 aligns to its longest option, so `--configPointer`
    sits one space from its prose while the others get a column. The block
    never splits, the whole line arrives as the flag column, and the
    description was dropped outright.

    Recovered only where the usage line has already settled arity — elsewhere
    that first word may genuinely name the value. And only the flags that
    *lead*: this description names `--config` mid-sentence, and reading to the
    last flag on the line would call the description "file".
    """
    got = flags(_toolhelp.parse_help(MARKDOWNLINT, name="markdownlint-cli2"))
    assert got["configPointer"].help.startswith("specifies a JSON Pointer")
    assert got["configPointer"].help.endswith("--config file")
    assert got["config"].help.startswith("specifies the path")


def test_a_stitched_options_block_is_not_read_as_usage_grammar():
    """`_usage_line` joins indented continuations, and a tool whose options
    block is indented under `Usage:` hands the whole block back as one line.
    Unbracketed, so the descriptions there are prose and not metavars —
    otherwise `--dependencies   Emit import dependency information` makes a
    string of a switch, and nine of basedpyright's did.
    """
    got = flags(_toolhelp.parse_help(BASEDPYRIGHT, name="basedpyright"))
    assert got["dependencies"].type_name == "bool"
    assert got["outputjson"].type_name == "bool"
    assert got["watch"].type_name == "bool"
    assert got["createstub"].type_name == "str"  # its block states the metavar


def test_an_alternation_is_not_a_value():
    """`[--quiet | --verbose]` groups two switches; it does not give --quiet a
    value of "| --verbose". `[--installer {pip,uv} | --no-isolation]` does both
    at once — a real value, then an alternative that must not be swallowed.
    """
    got = flags(_toolhelp.parse_help(PYPROJECT_BUILD, name="pyproject-build"))
    assert got["quiet"].type_name == "bool"
    assert got["verbose"].type_name == "bool"
    assert got["installer"].type_name == "str"  # `{pip,uv}`
    assert got["outdir"].type_name == "str"  # `[--outdir PATH]`


# --- a manual's prose is not its option list ---------------------------------

# git-branch, rendered wide enough that a sentence wraps onto a line beginning
# with a flag. The `--merged, only branches …` line is prose from the
# DESCRIPTION; the blocks below it are the real options.
MAN_WIDE = """GIT-BRANCH(1)

DESCRIPTION
       With --contains, shows only the branches that contain the commit. With
       --merged, only merged branches are listed. With --no-merged the rest.

OPTIONS
       --merged [<commit>]
           Only list branches whose tips are reachable from <commit>.

       --no-merged [<commit>]
           Only list branches whose tips are not reachable from <commit>.
"""

# The same page narrow: the spelling list itself wraps, leaving a hanging comma.
MAN_WRAPPED_HEAD = """GIT-LOG(1)

OPTIONS
       --min-parents=<number>, --max-parents=<number>, --no-min-parents,
       --no-max-parents
           Show only commits which have at least that many parent commits.

       --follow
           Continue listing the history of a file beyond renames.
"""


def test_a_manual_sentence_beginning_with_a_flag_is_not_an_option():
    """A manual sets options at the same indent as its prose, so a sentence
    that wraps onto a line starting with a flag is indistinguishable from a
    block opening — except that a block starts a paragraph.

    Read as a block, `--merged, only branches … With --no-merged …` becomes one
    option carrying both spellings, pairs them as a negation, and hides the
    real `--no-merged` below. Where the sentence wraps decides whether that
    happens, so one manual read at two widths disagreed about what git accepts
    — on bytes that are identical on every platform.
    """
    got = flags(_toolhelp.parse_help(MAN_WIDE, name="git-branch", man=True))
    assert set(got) == {"merged", "no_merged"}
    assert got["merged"].help.startswith("Only list branches whose tips are reachable")
    assert got["no_merged"].help.startswith("Only list branches whose tips are not")
    assert got["merged"].negation == ""  # the prose did not pair them


def test_a_wrapped_spelling_list_is_one_head_not_two():
    """A stacked spelling continues the block; a spelling list that *wrapped*
    continues the head, and the hanging comma says which. Read as a head of its
    own, the remainder splits one option in two — and only at the widths where
    the line happens to wrap.
    """
    got = flags(_toolhelp.parse_help(MAN_WRAPPED_HEAD, name="git-log", man=True))
    assert "follow" in got
    assert "no_max_parents" not in got  # a continuation, not a second option
    assert got["min_parents"].help.startswith("Show only commits")
