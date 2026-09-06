"""Public application entry: build a custom-branded CLI on top of footman.

footman's own `fm` / `footman` commands are just the default-branded
`App`. Point your own console script at an `App` carrying your
project's names and version, and every message the user sees — errors,
`--version`, the completion hint — uses them:

```python
# acme/cli.py
from livery.footman import App

app = App(name="Acme", prog="acme", version="1.4.0")

def main() -> None:
    raise SystemExit(app.run())
```

```toml
# your pyproject.toml
[project.scripts]
acme = "acme.cli:main"
```

Tasks are discovered exactly as they are for `fm`: the `tasks.py` cascade
from the repo root down to the current directory.

This module is kept import-light so the completion hot path stays fast: nothing
here imports the registry, the manifest, or the execution layer at module load —
those are deferred into `App.run`.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from livery.footman import __version__


def _prefix_from(prog: str) -> str:
    """An environment-variable prefix from the command name.

    `prog`, not the display name: it is the word your users type, so
    `acme` reading `ACME_CACHE_DIR` needs no explaining — and a command
    name is already shell-safe, where a display name is free text that
    would need sanitising to be a variable.

    Uppercased, every run of non-alphanumerics collapsed to one `_`, and a
    leading digit padded. Written with `str` methods rather than `re`,
    which this import-light module should not pay for.
    """
    out: list[str] = []
    for char in prog:
        if char.isalnum() and char.isascii():
            out.append(char.upper())
        elif out and out[-1] != "_":
            out.append("_")
    cleaned = "".join(out).strip("_")
    if not cleaned:
        return "FOOTMAN"
    return f"_{cleaned}" if cleaned[0].isdigit() else cleaned


@dataclass(frozen=True)
class Brand:
    """The names a CLI shows the user, and where it keeps its things.

    `name` is the long / display name (the `--version` banner); `prog` is
    the short command name (the error prefix and hints); `version` is *your*
    version string; `tasks_file` is the filename your users write tasks in
    (config `tasks` still overrides it per project).

    `dist` is the distribution that ships your CLI — the name a user would
    `pip install`. It is what the handoffs reason about: which package a
    project's lockfile must pin, and which one a tasks file carrying its
    own PEP 723 dependencies must declare. Left `None` (the default for a
    custom brand), both handoffs simply stay out of your way.

    `cache_dir` and `data_dir` are the two folders this CLI keeps things
    in, and **you** place them, so footman never guesses at your product's
    layout:

    ```python
    acme_home = Path(os.environ.get("ACME_HOME", Path.home() / ".acme"))
    cache_dir=acme_home / ".cache" / "acme-cli",
    data_dir=acme_home / "acme-cli",
    ```

    They are not anchored to each other — put them wherever each belongs in
    your world. **Cache** is derived data, safe to delete, and the
    collector sweeps it; **data** is durable and machine-local (credentials,
    tokens, generated assets) and is never collected. footman refuses to
    start if the two name the same directory, because that would point the
    collector at things it must not delete.

    `<PREFIX>_CACHE_DIR` and `<PREFIX>_DATA_DIR` override them at run time,
    which is how two installations run side by side under different
    identities. Left unset with those variables unset, both fall back to
    XDG — `~/.cache/acme` and `~/.local/share/acme`.

    Task authors never see any of this: they call `footman.cache_dir()` and
    `footman.data_dir()` and get a directory that exists.

    `env_prefix` is the namespace for every variable this CLI reads, and
    defaults to `prog` uppercased: the command is `acme`, so the variables
    are `ACME_*`. A branded CLI reads only its own prefix, so a stray
    `FOOTMAN_CACHE_DIR` can never move someone else's product.

    Set it explicitly for either of two reasons. **A terse command makes a
    poor variable**: footman's own is `fm`, but its variables are
    `FOOTMAN_*`, because `FOOTMAN_CACHE_DIR` tells a reader who has never
    heard of the tool what it belongs to and `FM_CACHE_DIR` tells them
    nothing. **Or the namespace collides with your product's own** —
    keeping it clear is yours to arrange.

    `config_name` names the standalone config file (`acme.toml`), the table
    inside `pyproject.toml` (`[tool.acme]`), and the user-level config
    corner (`~/.config/acme/`), from one field so the three cannot drift
    apart. It defaults to `prog` — the machine word your users already type,
    the same rule the env prefix follows — never the display name, which is
    free text (`[tool."Acme DevKit"]` is nothing anyone would write, and a
    capitalised table is one nobody would find). Set it when the identity
    your users should write isn't the command: footman's own command is
    `fm`, but its config is `footman.toml`, pinned for the same reason its
    variables are `FOOTMAN_*`. The *user-level* config file is not placed
    by the brand: it stays at `~/.config/acme/config.toml`, where a user
    looks for their own settings, with `<PREFIX>_CONFIG` naming another.
    """

    name: str = "footman"
    prog: str = "fm"
    version: str = __version__
    tasks_file: str = "tasks.py"
    dist: str | None = "footman"
    cache_dir: Path | None = None
    data_dir: Path | None = None
    env_prefix: str | None = None
    config_name: str | None = None
    builtin: tuple[str, ...] = ()
    """The brand's own task surface where there is no project — a tuple of
    `footman.tasks` entry-point names, mounted as the base of the tree
    exactly when discovery finds no project task files. The ladder is
    project > user > built-in: a project ignores the base outright (its
    tasks file mounts what it wants — nothing is privileged, nothing lost),
    and the user tasks file overlays it. Strings, never live objects: the
    detached refresh child rebuilds manifests in a bare process, and a name
    rides argv where an object cannot. Curate a small global surface — what
    does this CLI offer someone with no project? — rather than aiming it at
    the everyday tasks, which belong where their probes work."""

    @property
    def prefix(self) -> str:
        """The environment-variable namespace — `env_prefix`, else from `prog`."""
        return self.env_prefix or _prefix_from(self.prog)

    @property
    def config_stem(self) -> str:
        """The config identity — `config_name`, else `prog`: the `acme` of
        `acme.toml`, `[tool.acme]` and `~/.config/acme/`."""
        return self.config_name or self.prog

    def env(self, suffix: str) -> str:
        """This brand's spelling of an environment variable: `ACME_CACHE_DIR`."""
        return f"{self.prefix}_{suffix}"

    def config_file(self) -> str:
        """This brand's standalone config filename — `acme.toml`."""
        return f"{self.config_stem}.toml"

    def install(self) -> None:
        """Point the process's locations at this brand — the one call the
        real entry (`App.run`) and the embedded harness (`Runner.invoke`)
        both make, so the two can never configure different worlds (they
        once each listed the fields by hand, and grew apart by three)."""
        from livery.footman import _paths

        _paths.configure(
            prefix=self.prefix,
            cache_dir=self.cache_dir,
            data_dir=self.data_dir,
            config_name=self.config_stem,
            tasks_file=self.tasks_file,
            prog=self.prog,
            brand_version=self.version,
            builtin=self.builtin,
            dist=self.dist,
        )


# footman's command is `fm`, but its variables are `FOOTMAN_*` and its config
# is `footman.toml` — not for compatibility, but because `FOOTMAN_CACHE_DIR`
# and `[tool.footman]` tell a reader who has never heard of this tool what
# they belong to, and can be searched for. `FM_CACHE_DIR` and `fm.toml` are
# neither. A two-letter command is exactly when to pin the longer words.
DEFAULT_BRAND = Brand(env_prefix="FOOTMAN", config_name="footman")


class App:
    """A branded footman CLI — call `run` from your console-script entry.

    One `App` serves a process: `run` points module-level state (the brand,
    its locations) at this brand and deliberately never restores it — a
    process runs one brand, and `run` may not return at all (the uv handoff
    re-execs). Driving two brands from one process is supported only through
    `footman.testing.Runner`, which saves and restores around each
    invocation.
    """

    brand: Brand

    def __init__(
        self,
        name: str = "footman",
        prog: str = "fm",
        version: str | None = None,
        tasks_file: str = "tasks.py",
        dist: str | None = None,
        cache_dir: Path | str | None = None,
        data_dir: Path | str | None = None,
        env_prefix: str | None = None,
        config_name: str | None = None,
        builtin: Sequence[str] = (),
    ) -> None:
        # The stock pins: an `App` that kept the `fm` command *is* stock
        # footman, and stock pins the long words its two-letter command
        # cannot derive — `FOOTMAN_*` variables, `footman.toml` config. The
        # moment `prog` moves, the pins stop applying and a brand derives
        # from its own command (pin `env_prefix=` / `config_name=` to
        # differ). Without this, a bare `App()` would read `FM_CONFIG` and
        # `[tool.fm]` while the `fm` on PATH reads footman's.
        if prog == "fm":
            env_prefix = env_prefix or "FOOTMAN"
            config_name = config_name or "footman"
        # `dist` is opt-in for a branded CLI: footman cannot guess which
        # distribution ships someone else's runner, and a wrong guess would
        # hand a user's invocation to an environment without it. Unset, the
        # handoffs stay out of the way (documented in custom-cli.md).
        self.brand = Brand(
            name=name,
            prog=prog,
            version=version or __version__,
            tasks_file=tasks_file,
            dist=dist,
            cache_dir=Path(cache_dir).expanduser() if cache_dir is not None else None,
            data_dir=Path(data_dir).expanduser() if data_dir is not None else None,
            env_prefix=env_prefix,
            config_name=config_name,
            builtin=tuple(builtin),
        )

    def run(self, argv: list[str] | None = None) -> int:
        """Resolve and run the CLI, returning the process exit code.

        Handles the stdlib-only `--complete` hot path before importing the
        framework, so completion stays fast even through a custom entry point.
        """
        args = list(sys.argv[1:] if argv is None else argv)
        # Locations first, and on both paths: the completion hot path reads
        # this brand's cache exactly as the execution path writes it.
        self.brand.install()
        if args and args[0] == "--complete":
            from livery.footman._complete import complete_cli

            return complete_cli(args[1:])
        # A process started without a stream (the caller closed the fd;
        # pythonw) gets `None` for it, and every later touch — the
        # scheduler's isatty, the uv handoff's flush, a body's print —
        # becomes an AttributeError traceback. A stream nobody connected
        # means "discard", so it is wired to devnull once, here, and the
        # whole run downstream never has to ask.
        import os as _os

        if sys.stdout is None:
            sys.stdout = open(_os.devnull, "w", encoding="utf-8")  # noqa: SIM115
        if sys.stderr is None:
            sys.stderr = open(_os.devnull, "w", encoding="utf-8")  # noqa: SIM115
        try:
            _os.getcwd()
        except OSError:
            # A shell sitting in a directory another process deleted: every
            # later step needs a cwd (discovery, config, the cache key), and
            # each used to fail as its own raw FileNotFoundError traceback.
            # One taught line, at the door.
            print(
                f"{self.brand.prog}: the working directory no longer exists "
                f"— cd to a real directory and try again",
                file=sys.stderr,
            )
            return 1
        from livery.footman import _app

        code = _app.run(args, brand=self.brand)
        try:
            sys.stdout.flush()
        except BrokenPipeError:
            # The reader hung up mid-run (`fm … | head`). Flushing here, and
            # pointing fd 1 at devnull for the interpreter's own shutdown
            # flush, keeps "Exception ignored while flushing sys.stdout" off
            # the screen; the code is what a SIGPIPE-default tool reports.
            import contextlib

            with contextlib.suppress(Exception):
                devnull = _os.open(_os.devnull, _os.O_WRONLY)
                _os.dup2(devnull, sys.stdout.fileno())
            code = code or 141
        return code
