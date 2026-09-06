"""Load a .env file into the run's environment — mounted, never ambient.

`plugin("footman.env_files")` in a tasks file switches it on; unmounted, it is
inert metadata like any other plugin. At the invocation's single-threaded
moment the file is read and every key the environment does not already carry
is set — **env wins**, so a real environment variable always beats the file,
and a run is never surprised by a checkout. Parsing is python-dotenv's (an
optional dependency, taught by name when it is missing), with interpolation
off: a value is the text on its line.

The default file is `.env` in the invocation's directory; `--env-file=PATH`
names another (path-typed, so completion offers files), and a bare
`--env-file` asks for the default out loud. A missing default is nothing to
do — unless someone asked: the bare mention refuses where plain absence
shrugs, and a missing *named* file always refuses. The manifest refresh
child runs this hook too, with no command line, so what completion bakes
reflects the default file — availability is re-checked live at execution
either way.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import livery.footman as footman
from livery.footman import GlobalOption, matching

ENV_FILE = GlobalOption(
    "env-file",
    # `.env`, `.env.local`, `.env.production` — the family, rather than every
    # file in the directory. Completion only: a path typed anyway still binds.
    Annotated[Path, matching(".env*")],
    default=Path(".env"),
    help="the .env file to load",
)


@footman.pre_tasks
def load(inv: footman.Invocation) -> None:
    # pre_tasks runs before global binding, so this reads the lexical CLI —
    # and presence is the question there, never value truthiness: a bare
    # mention arrives as the empty string, and Path("") reads as ".".
    given = "env_file" in inv.cli
    raw = inv.cli.get("env_file")
    named = Path(raw) if isinstance(raw, str) and raw else None
    path = named if named is not None else Path(inv.cwd or ".") / ".env"
    if not path.is_file():
        if given:
            # Named a file, or asked for the default out loud — both are
            # someone asking, so a missing file refuses rather than shrugs.
            footman.fail(f"--env-file: {path} does not exist")
        return  # no .env is nothing to do, not a problem
    try:
        from dotenv import dotenv_values
    except ImportError:
        footman.fail(
            "plugin('footman.env_files') needs the python-dotenv package — "
            "add it to the project's environment (uv add python-dotenv), "
            "or drop the mount"
        )
    for key, value in dotenv_values(path, interpolate=False).items():
        if value is not None:
            os.environ.setdefault(key, value)  # env wins, always
