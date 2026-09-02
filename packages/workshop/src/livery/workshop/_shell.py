"""The entered-shell entry: a fully configured interactive shell.

``fm shell [kind]`` makes the target shell itself evaluate
``fm -C=<root> --quiet env.emit <dialect>`` at its own startup,
injected through a dialect-specific rc mechanism. The completion
hook is a shell function that never survives an exec, so evaluating
the emission in a wrapper and exec'ing the target would lose it;
evaluated inside the target, environment and completion both land in
the final interactive shell. A cold checkout fails loudly inside the
opened shell: the emission's refusal prints to stderr, never a shell
that merely looks entered.

The launch decisions are data (``shell_launch_plan`` answers
argv, environment, and rc files) so every branch tests as a plain
call; the task body only writes and execs them.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from footman import Arg, Stdout, group

_KINDS = ("bash", "zsh", "pwsh")

# Evaluated INSIDE the launched shell: environment, PATH, and the
# interactive completion hook, all from the one emission. --quiet
# drops the receipt rows; stderr stays live so a cold refusal
# teaches. {root} arrives already quoted for the dialect: a checkout
# path may hold a quote or a space, and spliced raw it ends the
# string early and breaks the whole eval.
_POSIX_ENTER = 'eval "$(fm -C={root} --quiet env.emit posix)"'
_PWSH_ENTER = '(fm -C={root} --quiet env.emit pwsh) -join "`n" | Invoke-Expression'


def _posix_root(root: Path) -> str:
    """*root* as one POSIX shell word."""
    return shlex.quote(str(root))


def _pwsh_root(root: Path) -> str:
    """*root* as one PowerShell single-quoted string.

    PowerShell escapes a single quote by doubling it; there is no
    backslash escape inside single quotes.
    """
    return "'" + str(root).replace("'", "''") + "'"


@dataclass
class ShellLaunch:
    """Everything needed to open the entered shell, as pure data."""

    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)
    files: dict[Path, str] = field(default_factory=dict)


def default_kind() -> str:
    """The platform's interactive shell: ``$SHELL``'s basename, else pwsh/bash."""
    if sys.platform == "win32":
        return "pwsh"
    name = Path(os.environ.get("SHELL", "")).name
    return name if name in _KINDS else "bash"


def _git_bash() -> str | None:
    r"""The Git-Bash executable on Windows, or None.

    A vendored install exports ``CLAUDE_CODE_GIT_BASH_PATH``; a
    system Git for Windows puts ``<root>/cmd/git.exe`` on PATH but
    not ``<root>/bin/bash.exe``, so one derives from the other.
    Never a bare ``bash`` fallback: that finds WSL's
    ``System32\bash.exe``.
    """
    vendored = os.environ.get("CLAUDE_CODE_GIT_BASH_PATH", "")
    if vendored and os.path.isfile(vendored):
        return vendored
    git = shutil.which("git")
    if git:
        candidate = os.path.join(
            os.path.dirname(os.path.dirname(git)), "bin", "bash.exe"
        )
        if os.path.isfile(candidate):
            return candidate
    return None


def _resolve_binary(kind: str) -> str:
    """The concrete shell executable for *kind*, or a taught refusal."""
    if sys.platform == "win32":
        if kind == "bash":
            found = _git_bash()
        elif kind == "pwsh":
            found = shutil.which("pwsh") or shutil.which("powershell")
        else:
            found = shutil.which(kind)
    else:
        found = shutil.which(kind)
    if not found:
        raise SystemExit(f"no {kind} shell available on this machine")
    return found


def shell_launch_plan(kind: str, *, root: Path, tmp_dir: Path) -> ShellLaunch:
    """The launch plan for an entered *kind* shell rooted at *root*.

    bash takes the injection through ``--rcfile`` (which replaces
    ``~/.bashrc``, so the rc sources it first; ``/etc`` rc files
    load regardless); zsh through a throwaway ``ZDOTDIR`` whose
    ``.zshenv`` and ``.zshrc`` chain to the user's own (``ZDOTDIR``
    resets to the home inside, so nested shells behave); pwsh
    through ``-NoExit -Command`` with the user profile still
    loading. cmd is refused: it has no startup file to inject, so a
    launched cmd only inherits a cold caller and looks entered
    without being entered; pwsh delivers the full contract on
    Windows.
    """
    if kind == "cmd":
        raise SystemExit(
            "cmd has no startup file to evaluate the emission, so it"
            " cannot be entered reliably; use pwsh on Windows."
        )
    if kind not in _KINDS:
        raise SystemExit(f"unknown shell kind '{kind}' - one of: {', '.join(_KINDS)}")
    binary = _resolve_binary(kind)
    enter = _POSIX_ENTER.format(root=_posix_root(root))
    if kind == "bash":
        rc = tmp_dir / "livery-shell-bashrc"
        content = f'[ -f "$HOME/.bashrc" ] && . "$HOME/.bashrc"\n{enter}\n'
        return ShellLaunch(
            argv=[binary, "--rcfile", str(rc), "-i"], files={rc: content}
        )
    if kind == "zsh":
        zdot = tmp_dir / "livery-shell-zdot"
        zshenv = '[ -f "$HOME/.zshenv" ] && . "$HOME/.zshenv"\n'
        zshrc = (
            'ZDOTDIR="$HOME"\n'
            + '[ -f "$HOME/.zshrc" ] && . "$HOME/.zshrc"\n'
            + enter
            + "\n"
        )
        return ShellLaunch(
            argv=[binary, "-i"],
            env={"ZDOTDIR": str(zdot)},
            files={zdot / ".zshenv": zshenv, zdot / ".zshrc": zshrc},
        )
    return ShellLaunch(
        argv=[
            binary,
            "-NoLogo",
            "-NoExit",
            "-Command",
            _PWSH_ENTER.format(root=_pwsh_root(root)),
        ]
    )


def _realise(kind: str) -> ShellLaunch:
    """The plan for *kind*, with its rc files written to disk."""
    import footman

    resolved = kind or default_kind()
    tmp_dir = Path(tempfile.mkdtemp(prefix="livery-shell-"))
    plan = shell_launch_plan(resolved, root=footman.cwd(), tmp_dir=tmp_dir)
    for path, content in plan.files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return plan


def launch_shell(kind: str = "") -> None:
    """Open the entered shell; never returns on POSIX, waits on Windows.

    POSIX execs, nothing left behind. Windows has no real exec, so
    the launcher waits and forwards the exit code: the console needs
    an owner.
    """
    plan = _realise(kind)
    env = {**os.environ, **plan.env}
    if sys.platform == "win32":
        result = subprocess.run(plan.argv, env=env, cwd=os.getcwd(), check=False)
        raise SystemExit(result.returncode)
    os.execvpe(plan.argv[0], plan.argv, env)


shell = group("shell", help="Open a fully entered interactive shell")


@shell.default(interactive=True, shared=False)
def shell_default(
    kind: Arg[str] = "",
) -> None:
    """Open an entered shell of *kind* (bash, zsh, or pwsh).

    The launched shell evaluates ``fm env.emit`` at its own startup,
    so environment, PATH, and completion land inside it. Bare picks
    ``$SHELL``'s kind, pwsh on Windows.
    """
    launch_shell(kind)


@shell.task(name="prepare", hidden=True)
def shell_prepare(
    kind: Arg[str] = "",
) -> Stdout[list[str]]:
    """Prepare an entered launch: write its rc files, answer its argv.

    The handoff for a launcher that must not keep a resident Python:
    the caller launches the argv itself. Writing the rc files here
    is the job, not a side errand; an argv naming a missing rc opens
    a shell that looks entered and is not. A plan that needs
    environment variables (zsh's throwaway ``ZDOTDIR``) is refused
    rather than returned half-working.
    """
    plan = _realise(kind)
    if plan.env:
        names = ", ".join(sorted(plan.env))
        raise SystemExit(
            f"{kind or default_kind()} cannot be handed off as an argv:"
            f" it needs {names} set in the environment"
        )
    return plan.argv
