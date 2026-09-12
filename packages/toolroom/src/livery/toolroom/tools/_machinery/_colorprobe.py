"""Probe a tool's colour control by running it — the engine behind
`fm tools.color`.

footman forces colour into the tools it spawns (see `context.color_env`): by the
environment for the modern set, by the tool's own flag for the few that ignore
it. Which is which is a fact about each tool, and the honest way to learn it is
to *run* the tool and look at the bytes. This module does that: for each tool it
forces colour on and off, first by environment then by flag, and records whether
each direction obeys the environment (`env`), needs the flag (`flag`), or can be
forced neither way (`none`).

The result generates `_colordata.py` — read by `tools.py` for its forcing table
and by the docs for the support table. A maintainer runs it against the
provisioned binaries; nothing here is imported on a normal `fm` run.

A tool only colourises when it has something to colour, so each needs a
hardcoded *trigger*: a command (and any fixture files) that produces colourable
output. Figured out once per tool; one without a trigger reports `unprobed`.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import tempfile
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from livery.toolroom.tools._machinery import _toolhelp
from livery.toolroom.tools._machinery._toolspec import ToolSpec

_SGR = re.compile("\x1b\\[")  # a CSI escape — how "it emitted colour" is seen

# Forcing environments, mirroring `context.color_env` (presence/absence): on sets
# the force vars; off is NO_COLOR with every force var *absent*.
_ON_ENV = {"FORCE_COLOR": "1", "CLICOLOR_FORCE": "1", "CLICOLOR": "1"}
_OFF_ENV = {"NO_COLOR": "1"}
_COLOR_VARS = ("FORCE_COLOR", "CLICOLOR_FORCE", "CLICOLOR", "NO_COLOR")


@dataclass(frozen=True)
class Trigger:
    """How to make a tool emit colour: its argv, and the fixture it needs."""

    args: tuple[str, ...]
    files: dict[str, str] = field(default_factory=dict)  # filename -> content
    git: bool = False  # init a repo and commit the files first (for git itself)


# One command per tool that produces colourable output — figured out once each,
# from the tool's own verbs (see its stub). Every curated tool has one, so none
# is left `unprobed`; a tool that then shows no colour by any means is a true
# `none`. Keyed by driver key. `PY_TYPE`/`SPELL`/`HTML`/`MD` are shared fixtures.
_PY_TYPE = "x: int = 'a'\n"  # a type error, for the type checkers
_SPELL = "helllo wrold\n"
TRIGGERS: dict[str, Trigger] = {
    "ruff": Trigger(("check", "bad.py"), {"bad.py": "import os\n"}),
    "ruff_format": Trigger(("format", "--diff", "bad.py"), {"bad.py": "x=1\n"}),
    "basedpyright": Trigger(("bad.py",), {"bad.py": _PY_TYPE}),
    "mypy": Trigger(("bad.py",), {"bad.py": _PY_TYPE}),
    "ty": Trigger(("check", "bad.py"), {"bad.py": _PY_TYPE}),
    "pytest": Trigger(("t.py",), {"t.py": "def test():\n    assert True\n"}),
    "cspell": Trigger(("lint", "bad.txt"), {"bad.txt": _SPELL}),
    "uv": Trigger(("python", "list")),
    "gh": Trigger(("--help",)),
    "bun": Trigger(("--help",)),
    "prek": Trigger(("--help",)),
    "mkdocs": Trigger(("--help",)),  # in-process for footman; probed as subprocess
    "twine": Trigger(("check", "s.py"), {"s.py": "print(1)\n"}),
    "cmake": Trigger(("-S", ".", "-B", "b")),  # configuring an empty dir → error
    "ninja": Trigger((), {"build.ninja": "rule f\n  command = false\nbuild x: f\n"}),
    "build": Trigger(
        ("--sdist",),
        {"pyproject.toml": '[project]\nname="x"\nversion="1"\n'},
    ),
    "git": Trigger(("log", "--oneline", "-1"), {"a.txt": "hi\n"}, git=True),
    "git_cliff": Trigger((), {"a.txt": "hi\n"}, git=True),
    "git_changelog": Trigger((".",), {"a.txt": "hi\n"}, git=True),
    "djlint": Trigger(("bad.html",), {"bad.html": "<div ></div>\n"}),
    "markdownlint": Trigger(("bad.md",), {"bad.md": "#x\n"}),
    "eclint": Trigger(("-h",)),
    "coverage": Trigger(("report",)),
    "python": Trigger(("--version",)),  # the interpreter emits no colour
    "zensical": Trigger(("--help",)),
    # Every other claude verb either reaches the network or starts a session;
    # `--help` is the one that is local, quick and certain to print something.
    "claude": Trigger(("--help",)),
    "tea": Trigger(("--help",)),  # its verbs all want a login and a forge
}

# A tool's own colour switch, when the stub's `color_flags()` can't surface it:
# git spells it as a pre-verb config; cspell/mypy use plain boolean flags, not a
# `--color=always/never` choice. `(on, off, pre_verb)`; auto-detected
# `--color=always` switches still come from the stub (see `flag_candidate`).
_CURATED: dict[str, tuple[tuple[str, ...], tuple[str, ...], bool]] = {
    "git": (("-c", "color.ui=always"), ("-c", "color.ui=never"), True),
    "cspell": (("--color",), ("--no-color",), False),
    "mypy": (("--color-output",), ("--no-color-output",), False),
}

# Pass-through wrappers: their own CLI output doesn't colour, and their job is to
# run *another* program (a container) whose colour is the wrapped program's
# business plus whether the caller passed a tty (`docker run -t`). footman
# neither forces nor suppresses it — it faithfully relays whatever comes through.
# So `env`/`flag`/`none` don't apply; the verdict is `n/a`.
_PASSTHROUGH = frozenset({"docker"})


@dataclass(frozen=True)
class ColourFlag:
    """The tokens that force a tool's colour on/off, and where they go."""

    on: tuple[str, ...] = ()
    off: tuple[str, ...] = ()
    pre_verb: bool = False


def flag_candidate(key: str, spec: ToolSpec) -> ColourFlag | None:
    """The colour switch to try for a tool: a curated quirk (git), else a
    `--color=always/never` detected from its stub (`ToolSpec.color_flags`).
    """
    if key in _CURATED:
        on, off, pre = _CURATED[key]
        return ColourFlag(on, off, pre)
    detected = spec.color_flags()
    for _verb, (flag, on_val, off_val) in sorted(detected.items()):
        on = (f"{flag}={on_val}",) if on_val else ()
        off = (f"{flag}={off_val}",) if off_val else ()
        if on or off:
            return ColourFlag(on, off, pre_verb=False)
    return None


@dataclass(frozen=True)
class Verdict:
    """One tool's probed colour control, each direction categorised."""

    on: str  # "env" | "flag" | "none" | "unprobed"
    off: str
    flag: ColourFlag | None = None  # the switch, when a direction needs one


def _fm_run(*args: Any, **kwargs: Any) -> Any:
    """`context.run`, imported at call time (the probe is reachable from the
    stub tooling, which has no interest in the run machinery).
    """
    from livery.footman.context import run

    return run(*args, **kwargs)


@contextlib.contextmanager
def _fixture(binary: str, trigger: Trigger) -> Generator[Path]:
    """A throwaway directory with the trigger's files (and a git repo if asked)."""
    with tempfile.TemporaryDirectory(prefix="fm-color-") as tmp:
        cwd = Path(tmp)
        for name, content in trigger.files.items():
            (cwd / name).write_text(content, encoding="utf-8")
        if trigger.git:
            # Fixture setup, not the probe: unreported, and a bound so a
            # wedged git cannot hang the whole colour walk.
            quiet = {"cwd": cwd, "recorded": False, "nofail": True, "timeout": 60}
            _fm_run([binary, "init", "-q"], **quiet)
            _fm_run([binary, "add", "-A"], **quiet)
            _fm_run(
                [
                    binary,
                    "-c",
                    "user.email=t@t.t",
                    "-c",
                    "user.name=t",
                    # The user's global config must not reach this commit: a
                    # signing setup (gpgsign + an agent that is locked or
                    # asleep) would fail it, and the probe would then read a
                    # colourless empty repo as "this tool never colours".
                    "-c",
                    "commit.gpgsign=false",
                    "commit",
                    "-qm",
                    "x",
                ],
                **quiet,
            )
        yield cwd


def _argv(
    binary: str, trigger: Trigger, pre: tuple[str, ...], post: tuple[str, ...]
) -> list[str]:
    """`binary [pre-verb flags] <trigger args> [trailing flags]`."""
    return [binary, *pre, *trigger.args, *post]


def _capture(argv: list[str], cwd: Path, env_add: dict[str, str]) -> str:
    """Run *argv* over a pipe with a clean-plus-*env_add* environment; return
    its combined stdout+stderr (empty on failure to launch).

    A real `TERM` is set when the ambient one is empty or `dumb` — many tools
    (mypy, …) refuse colour without one even under `FORCE_COLOR`, and the probe
    must judge them as they behave from a genuine terminal, not a bare CI env.

    Told not to phone home for a reason of its own: gh's update notice is
    itself coloured, so a tool left free to fetch it can hand the probe
    someone else's escape sequences and be recorded as colouring output it
    never wrote.
    """
    env = {k: v for k, v in os.environ.items() if k not in _COLOR_VARS}
    env.update(_toolhelp.QUIET)
    if env.get("TERM", "") in ("", "dumb"):
        env["TERM"] = "xterm-256color"
    env.update(env_add)
    try:
        done = _fm_run(
            argv,
            cwd=cwd,
            env=env,
            recorded=False,  # the probe reads bytes; it is not the run's business
            nofail=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    combined: str = done.stdout + done.stderr
    return combined


def probe(key: str, binary: str, spec: ToolSpec) -> Verdict:
    """Categorise one tool by running its trigger with colour forced each way."""
    if key in _PASSTHROUGH:  # a wrapper; colour isn't footman's to control
        return Verdict("n/a", "n/a")
    trigger = TRIGGERS.get(key)
    if trigger is None:
        return Verdict("unprobed", "unprobed")
    flag = flag_candidate(key, spec)
    pre_on = flag.on if (flag and flag.pre_verb) else ()
    post_on = flag.on if (flag and not flag.pre_verb) else ()
    pre_off = flag.off if (flag and flag.pre_verb) else ()
    post_off = flag.off if (flag and not flag.pre_verb) else ()

    def coloured(
        env_add: dict[str, str],
        pre: tuple[str, ...] = (),
        post: tuple[str, ...] = (),
    ) -> bool:
        return bool(
            _SGR.search(_capture(_argv(binary, trigger, pre, post), cwd, env_add))
        )

    with _fixture(binary, trigger) as cwd:
        # Force colour maximally (environment and flag). No colour then means
        # the tool can't be forced: `none` if it produced output to colour,
        # `unprobed` if the trigger produced nothing to judge.
        maxed = _capture(_argv(binary, trigger, pre_on, post_on), cwd, _ON_ENV)
        if not _SGR.search(maxed):
            # It produced output but no colour by any means → `none`; footman
            # can't make it colour over its pipe, so there is nothing to suppress
            # either (`off` is `n/a`, not a spurious `env`). No output at all →
            # the trigger judged nothing (`unprobed`).
            return (
                Verdict("none", "n/a")
                if maxed.strip()
                else Verdict("unprobed", "unprobed")
            )

        if coloured(_ON_ENV):
            on = "env"
        elif flag and (pre_on or post_on) and coloured({}, pre_on, post_on):
            on = "flag"
        else:
            on = "none"

        # A tool footman can't force *on* has nothing to turn *off* over a pipe —
        # a monochrome result there is the pipe, not the tool respecting NO_COLOR.
        if on == "none":
            off = "n/a"
        elif not coloured(_OFF_ENV):  # footman's off signal keeps it clean
            off = "env"
        elif flag and (pre_off or post_off) and not coloured({}, pre_off, post_off):
            off = "flag"
        else:
            off = "none"

    needs_flag = "flag" in (on, off)
    return Verdict(on, off, flag if needs_flag else None)


def probe_all(
    installed: list[tuple[str, str, str, ToolSpec]],
) -> dict[str, tuple[str, Verdict]]:
    """Probe each `(key, argv0, binary, spec)`; return `{key: (argv0, verdict)}`."""
    return {
        key: (argv0, probe(key, binary, spec)) for key, argv0, binary, spec in installed
    }


_HEADER = """\
# Generated by `fm tools.color` — do not edit by hand.
#
# Each curated tool's probed colour control: how footman forces it on and off.
# `tools.py` reads this for its forcing table; the docs read it for the support
# table. A tool obeys `env` (FORCE_COLOR/NO_COLOR), needs its own `flag`, or can
# force neither way (`none`); `unprobed` had no trigger.
#
# key -> (argv0, on, off, flag_on, flag_off, pre_verb)
_Row = tuple[str, str, str, tuple[str, ...], tuple[str, ...], bool]

COLOUR: dict[str, _Row] = {
"""


Row = tuple[str, str, str, tuple[str, ...], tuple[str, ...], bool]
"""One stored line of `_colordata.py` — see its `_Row`."""


def merged(
    stored: dict[str, Row], fresh: dict[str, tuple[str, Verdict]]
) -> dict[str, tuple[str, Verdict]]:
    """*fresh* over *stored*: a probe records what it ran and leaves the rest.

    A probe needs a binary, and the man tier provisions pages — so git, ssh
    and their siblings are in no prefix, and a run that wrote only what it
    saw would drop git's `-c color.ui=always` and take the bridge's colour
    forcing out with it. The same shape as a platform's reading folding
    into the option history: what was looked at is recorded, and what was
    not is left exactly as it was.
    """
    kept = {
        key: (
            argv0,
            Verdict(on, off, ColourFlag(f_on, f_off, pre) if f_on or f_off else None),
        )
        for key, (argv0, on, off, f_on, f_off, pre) in stored.items()
        if key not in fresh
    }
    return {**kept, **fresh}


def render(results: dict[str, tuple[str, Verdict]]) -> str:
    """The text of `_colordata.py` for a batch of probe results."""
    lines = [_HEADER]
    for key in sorted(results):
        argv0, v = results[key]
        f_on = v.flag.on if v.flag else ()
        f_off = v.flag.off if v.flag else ()
        pre = v.flag.pre_verb if v.flag else False
        lines.append(
            f"    {key!r}: ({argv0!r}, {v.on!r}, {v.off!r}, "
            f"{f_on!r}, {f_off!r}, {pre!r}),"
        )
    lines.append("}\n")
    return "\n".join(lines)
