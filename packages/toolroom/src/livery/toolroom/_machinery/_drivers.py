"""The curated tools, and the little each one needs said about it.

Extraction is generic — `_toolspec` reads a click command's parameters,
`_toolhelp` reads anybody's `--help` — so a driver is not a wrapper. It
carries only what a tool cannot tell you by being asked:

* **which verbs are worth stubbing.** `docker --help` lists forty commands
  and `git` has hundreds; a stub of all of them would be a megabyte nobody
  reads. The list here is the verbs tasks actually call.
* **the quirks.** git's `--help` opens a man page, so it wants `-h`. A tool
  whose real name differs from its attribute (`markdownlint-cli2` is
  `tools.markdownlint`) says so.
* **the default.** Whether `tools.<name>` runs in-process by default, which
  mirrors how it is constructed in `tools.py`.

Everything else — the flags, their help, their types, the negations — comes
from the installed tool, every time the stubs are regenerated.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path

from livery.toolroom._machinery import _toolhelp, _toolspec
from livery.toolroom._machinery._toolspec import ToolSpec, Verb


@dataclass(frozen=True)
class Manual:
    """Where a `kind="man"` tool's per-release pages are published.

    A manual is not a binary: the pages are the reading, so nothing is
    installed and nothing is run — the fetch is a directory listing, an
    archive per release, and a `man -M` tree to unpack it into. Two
    publishers so far: kernel.org's per-release git manpage tarballs, and
    OpenSSH's portable release tarballs (which carry `ssh.1` alongside the
    sources). The pages are the same bytes everywhere — a manual has no
    platform — so machines reading this tier cannot disagree.
    """

    index: str
    """The directory listing the per-release archives appear in."""
    archive: str
    """The archive's filename with a `{version}` slot, joined to *index*."""
    listing: str
    """Regex over the listing naming each release: must bind
    `(?P<version>…)`, and may bind `(?P<day>…)`/`(?P<month>…)`/`(?P<year>…)`
    where the listing shows dates (kernel.org does; OpenSSH's does not, and
    its patchlevels tie under `version_tuple` — `_order` breaks that tie on
    the numeric `pN` suffix)."""
    pages: tuple[str, ...] = ()
    """Member basenames to pull into `man1/` (`("ssh.1",)` from a source
    tarball). Empty means the archive already is a bare man tree, unpacked
    whole (git's manpages tarball)."""


@dataclass(frozen=True)
class Provision:
    """How `fm tools.provision` fetches this tool's *latest* binary.

    Data, like everything else a driver carries. The extractor reads the
    installed tool; this says how to *get* the latest one into a throwaway
    prefix, without touching the machine's own environment.
    """

    kind: str = "uv"
    """`uv` — a PyPI console script, `uv tool install --upgrade`d into an
    isolated prefix (covers the Rust and C++ tools too: ruff, prek, cmake and
    ninja all ship binary wheels). `node` — a package `bun install`s. `bun` —
    bun's own GitHub release, provisioned first because the node tier runs
    through it. `github` / `gitlab` / `gitea` — a prebuilt release asset.
    `docker` — a static build from docker's own per-platform index, which is
    a directory listing rather than an asset list. `man` — a release's
    manual pages, for a tool read from its manual rather than its `-h`.
    `deferred` — parked whole, `note` saying why (tea sat here until its
    0.15.0 release; a per-release boundary wants `floor` instead)."""
    package: str = ""
    """The PyPI or npm package, when it differs from the driver's binary name
    (`markdownlint-cli2`); otherwise the binary name is used."""
    repo: str = ""
    """`owner/repo` for a `github` / `gitlab` release download."""
    note: str = ""
    """Why a `deferred` source is parked — shown by `provision`."""
    floor: str = ""
    """Releases below this are not *offered* — not listed, not walked, and
    so never holes. The precedent is `READ_PYTHON_SINCE`: a hole says "this
    could not be read", and the store should not carry a shrug about a
    release that is simply out of scope. Unlike `deferred` this is per
    release, not per tool — the tool stays curated, its history just starts
    here. The boundary applies on every platform, so set it only where the
    releases below are not worth having anywhere, and say why alongside."""
    plugins: tuple[str, ...] = ()
    """Extra packages to install *alongside* the tool (`uv --with`), so a
    plugin-extended CLI is read whole. pytest's `--cov*` flags come from
    `pytest-cov`; without it a bare provisioned pytest would stub none of them."""
    manual: Manual | None = None
    """Where the pages live, for `kind="man"` — required there, unused
    elsewhere."""

    def target(self, name: str) -> str:
        """What to fetch: the explicit `package`/`repo`, else the tool *name*."""
        return self.package or self.repo or name


@dataclass(frozen=True)
class Plugin:
    """A separately released program a tool loads as one of its verbs.

    `docker compose` is not part of docker. It is its own project on its
    own release line, shipped as a binary the CLI discovers under the
    user's config directory — so a static docker build has no compose in
    it, and reading `docker compose up --help` reads whatever the *machine*
    happened to have installed. Under a walk that is a lie with a date on
    it: compose's surface of today, recorded as docker 20.10's. Across a
    platform matrix it is worse, because two machines with different
    compose versions look like a genuine per-platform divergence.

    So the plugin is fetched like the tool is, and paired by date: the
    release a user of *that* docker would have had. The verbs keep their
    place — `tools.docker.compose.up(...)` is still how you say it — and
    the pairing is deterministic, so the same walk gives the same answer
    forever.
    """

    name: str
    """The binary as the host tool looks for it (`docker-compose`)."""
    repo: str
    """`owner/repo` of the plugin's own releases."""
    owns: str = ""
    """The verb prefix these releases account for (`compose`)."""
    path: str = ""
    """Where the host tool looks, relative to the user's home
    (`.docker/cli-plugins`)."""
    since: str = ""
    """The first release that was a plugin at all. compose 1.x was a
    standalone program you ran as `docker-compose`; `docker compose` did
    not exist until 2.0. Dropping a 1.x binary into the plugin directory
    would not make it one, so an era before this pairs with nothing and
    the verbs read as absent — which they were."""


@dataclass(frozen=True)
class Driver:
    """One curated tool: what to run, and which verbs to read."""

    name: str
    """The binary as it is invoked."""
    attr: str = ""
    """`tools.<attr>`, when it differs from the binary's name."""
    verbs: tuple[str, ...] = field(default_factory=tuple)
    """The subcommands to stub, dotted for nesting (`compose.up`). Empty
    means the tool is its own command and its options hang off `__call__`."""
    help_flag: str = "--help"
    """git's `--help` opens a man page; `-h` is the help text."""
    in_process: bool = False
    """Whether `tools.<attr>` prefers in-process, as `tools.py` builds it."""
    base: tuple[str, ...] = field(default_factory=tuple)
    """A pre-bound verb: `tools.ruff_format` is `Tool("ruff", "format")`."""
    source: str = "auto"
    """`auto` prefers structure (click) and falls back to `--help`."""
    shorts: str = "only"
    """Short-option policy for the stub: `"none"` never keys on a short,
    `"only"` (default) keys on one *when it is the option's sole spelling*
    (python's `-m`, git's `-C`), and `"all"` also keys on a short that has a
    long form. Read only from `--help`, never a man page (its prose is noisy)."""
    url: str = ""
    """The tool's home, for the reference page's table."""
    version_of: str = ""
    """The sibling binary whose version answers for this tool, when it has
    no version output of its own — ssh-keygen ships in lockstep with ssh,
    and the OpenSSH release is the version of both."""
    man: bool = False
    """Read each verb's *manual* (`git help <verb>`) instead of its terse
    `-h`. git's `-h` omits about half its flags and prints an idiosyncratic
    multi-form usage; the manual is complete and states one SYNOPSIS per
    form, so both options and positional shape come out right. Runs only at
    stub-generation time, so the man-page dependency never reaches a user."""
    provision: Provision = field(default_factory=Provision)
    """How to fetch the latest binary — the default is a PyPI `uv` install."""
    plugins: tuple[Plugin, ...] = field(default_factory=tuple)
    """Companion programs some of the verbs really come from, each released
    on its own line — see `Plugin`."""

    @property
    def key(self) -> str:
        return self.attr or self.name.replace("-", "_")

    @property
    def wanted(self) -> tuple[str, ...]:
        """The verbs to read: a pre-bound tool wants only the one it binds."""
        if self.base:
            return (".".join(self.base),)
        return self.verbs


DRIVERS: tuple[Driver, ...] = (
    Driver(
        "ruff", verbs=("check", "format", "clean"), url="https://docs.astral.sh/ruff/"
    ),
    Driver(
        "ruff",
        attr="ruff_format",
        base=("format",),
        url="https://docs.astral.sh/ruff/formatter/",
    ),
    Driver("basedpyright", url="https://docs.basedpyright.com/"),
    Driver(
        "uv",
        provision=Provision(package="uv"),  # PyPI, `uv tool install uv` — never host
        url="https://docs.astral.sh/uv/",
        verbs=(
            "sync",
            "lock",
            "run",
            "add",
            "remove",
            "build",
            "publish",
            "export",
            "venv",
            "tree",
            "version",
            "pip.install",
            "pip.compile",
            "pip.sync",
            "pip.list",
            "tool.install",
            "tool.run",
            "tool.upgrade",
        ),
    ),
    Driver(
        "git",
        # Read from its manual, and a manual is not a binary: kernel.org
        # publishes the pages per release, so nothing is installed and
        # nothing is run. One tarball of about a megabyte against the fifty
        # a git build would cost, which is why this tier reaches back to
        # 2013 rather than stopping at a horizon someone had to choose.
        provision=Provision(
            kind="man",
            manual=Manual(
                index="https://mirrors.edge.kernel.org/pub/software/scm/git/",
                archive="git-manpages-{version}.tar.gz",
                listing=(
                    r'href="git-manpages-(?P<version>\d+(?:\.\d+)+)\.tar\.gz"'
                    r".*?(?P<day>\d{2})-(?P<month>[A-Z][a-z]{2})-(?P<year>\d{4})"
                ),
            ),
        ),
        url="https://git-scm.com/docs",
        help_flag="-h",
        man=True,
        verbs=(
            "add",
            "commit",
            "push",
            "pull",
            "fetch",
            "clone",
            "init",
            "checkout",
            "switch",
            "branch",
            "tag",
            "status",
            "diff",
            "log",
            "rev-parse",
            "describe",
            "stash",
            "restore",
            "worktree",
        ),
    ),
    Driver(
        "ssh",
        # OpenSSH has no `--help` at all: the manual is the only statement
        # of its surface, and the portable release tarball carries the
        # pages beside the sources. All-short options — the whole surface
        # keys through the default shorts policy.
        provision=Provision(
            kind="man",
            manual=Manual(
                index="https://cdn.openbsd.org/pub/OpenBSD/OpenSSH/portable/",
                archive="openssh-{version}.tar.gz",
                # The date sits in the next row cell, a line below the
                # href — so this crosses newlines where git's listing (one
                # row, one line) does not. Without it every OpenSSH release
                # was stored undated, and a date is what breaks a tie
                # between two builds of one version.
                listing=(
                    r'href="openssh-(?P<version>\d+\.\d+p\d+)\.tar\.gz"'
                    r"[\s\S]*?(?P<day>\d{2})-(?P<month>[A-Z][a-z]{2})-(?P<year>\d{4})"
                ),
                pages=("ssh.1",),
            ),
        ),
        man=True,
        url="https://man.openbsd.org/ssh.1",
    ),
    Driver(
        "ssh-keygen",
        attr="ssh_keygen",
        version_of="ssh",
        provision=Provision(
            kind="man",
            manual=Manual(
                index="https://cdn.openbsd.org/pub/OpenBSD/OpenSSH/portable/",
                archive="openssh-{version}.tar.gz",
                # The date sits in the next row cell, a line below the
                # href — so this crosses newlines where git's listing (one
                # row, one line) does not. Without it every OpenSSH release
                # was stored undated, and a date is what breaks a tie
                # between two builds of one version.
                listing=(
                    r'href="openssh-(?P<version>\d+\.\d+p\d+)\.tar\.gz"'
                    r"[\s\S]*?(?P<day>\d{2})-(?P<month>[A-Z][a-z]{2})-(?P<year>\d{4})"
                ),
                pages=("ssh-keygen.1",),
            ),
        ),
        man=True,
        url="https://man.openbsd.org/ssh-keygen.1",
    ),
    Driver(
        "ssh-keyscan",
        attr="ssh_keyscan",
        version_of="ssh",
        provision=Provision(
            kind="man",
            manual=Manual(
                index="https://cdn.openbsd.org/pub/OpenBSD/OpenSSH/portable/",
                archive="openssh-{version}.tar.gz",
                # The date sits in the next row cell, a line below the
                # href — so this crosses newlines where git's listing (one
                # row, one line) does not. Without it every OpenSSH release
                # was stored undated, and a date is what breaks a tie
                # between two builds of one version.
                listing=(
                    r'href="openssh-(?P<version>\d+\.\d+p\d+)\.tar\.gz"'
                    r"[\s\S]*?(?P<day>\d{2})-(?P<month>[A-Z][a-z]{2})-(?P<year>\d{4})"
                ),
                pages=("ssh-keyscan.1",),
            ),
        ),
        man=True,
        url="https://man.openbsd.org/ssh-keyscan.1",
    ),
    Driver(
        "docker",
        # Docker publishes static per-platform builds of every release, so
        # it is fetched like any other tool rather than read from the host.
        provision=Provision(kind="docker"),
        plugins=(
            Plugin(
                name="docker-compose",
                repo="docker/compose",
                owns="compose",
                path=".docker/cli-plugins",
                since="2.0.0",
            ),
            # `docker build` is buildx wherever buildx is installed, which
            # is everywhere docker itself is these days. Left unpaired, the
            # static binary falls back to the builder docker shipped with
            # before 2019 and the stub grows `--compress` and `--cpu-shares`
            # while losing `--platform` and `--push`.
            Plugin(
                name="docker-buildx",
                repo="docker/buildx",
                owns="build",
                path=".docker/cli-plugins",
            ),
        ),
        url="https://docs.docker.com/reference/cli/docker/",
        verbs=(
            "build",
            "run",
            "push",
            "pull",
            "images",
            "ps",
            "exec",
            "logs",
            "compose.up",
            "compose.down",
            "compose.build",
            "compose.logs",
            "compose.ps",
            "compose.run",
            "compose.exec",
        ),
    ),
    Driver(
        "bun",
        provision=Provision(kind="bun", repo="oven-sh/bun"),
        verbs=("install", "add", "remove", "run", "build", "test", "x"),
        url="https://bun.sh/docs/cli/install",
    ),
    Driver(
        "mkdocs",
        verbs=("build", "serve", "new", "gh-deploy"),
        in_process=True,
        url="https://www.mkdocs.org/",
    ),
    Driver(
        "zensical",
        verbs=("build", "serve", "new"),
        in_process=True,
        url="https://zensical.org/",
    ),
    Driver(
        "coverage",
        url="https://coverage.readthedocs.io/",
        verbs=("run", "report", "html", "xml", "json", "combine", "erase", "annotate"),
        in_process=True,
    ),
    Driver(
        "cspell",
        provision=Provision(kind="node"),
        verbs=("lint", "trace", "check", "suggest"),
        url="https://cspell.org/",
    ),
    Driver(
        "prek",
        verbs=("run", "install", "uninstall", "autoupdate", "clean"),
        url="https://prek.j178.dev/",
    ),
    Driver(
        "markdownlint-cli2",
        attr="markdownlint",
        provision=Provision(kind="node"),
        url="https://github.com/DavidAnson/markdownlint-cli2",
    ),
    Driver(
        "claude",
        # Claude Code's npm package is a launcher for a per-platform native
        # build, so the node tier fetches a release rather than a bundle —
        # a few hundred megabytes each, and several published a day. The
        # walk is priced accordingly: `gather` picks up the week's releases
        # above the floor, and `prime` reaches back only as far as it is
        # asked to.
        provision=Provision(kind="node", package="@anthropic-ai/claude-code"),
        url="https://docs.claude.com/en/docs/claude-code/cli-reference",
        # The headless surface is the root call — `claude -p "…"` with its
        # model, output-format and permission flags — so the verbs here are
        # the ones a task reaches for around it: wiring MCP servers and
        # plugins up, and asking after auth.
        verbs=(
            "agents",
            "auth.login",
            "auth.logout",
            "auth.status",
            "doctor",
            "install",
            "mcp.add",
            "mcp.add-json",
            "mcp.get",
            "mcp.list",
            "mcp.remove",
            "mcp.serve",
            "plugin.disable",
            "plugin.enable",
            "plugin.install",
            "plugin.list",
            "plugin.marketplace.add",
            "plugin.marketplace.list",
            "plugin.marketplace.remove",
            "plugin.marketplace.update",
            "plugin.uninstall",
            "plugin.update",
            "plugin.validate",
            "setup-token",
            "update",
        ),
    ),
    Driver(
        "gh",
        provision=Provision(kind="github", repo="cli/cli"),
        url="https://cli.github.com/manual/",
        verbs=(
            "pr.create",
            "pr.list",
            "pr.view",
            "pr.checkout",
            "pr.merge",
            "issue.create",
            "issue.list",
            "issue.view",
            "release.create",
            "release.upload",
            "release.view",
            "release.list",
            "repo.clone",
            "repo.view",
            "run.list",
            "run.view",
            "run.watch",
            "workflow.run",
            "workflow.list",
            "auth.status",
            "auth.login",
            "api",
            "label.list",
            "label.create",
        ),
    ),
    Driver(
        "tea",
        # Floored at 0.15.0, the release the console hang was fixed in
        # (gitea/tea#1054). Measured under the walk's own spawn, not
        # inferred: 0.13.0-0.14.2 interrogate the console at start-up and
        # hang every captured Windows read — the caller's terminal and a
        # hidden conhost alike; 0.10.1's build is unstamped ("Version:
        # development"), so no reading can prove what it is; 0.9.1 ships no
        # windows asset. What remains below (0.12.0 and older) reads fine,
        # but is not worth a history split by permanent platform holes:
        # tea's curated life starts where the hang ended.
        provision=Provision(kind="gitea", repo="gitea/tea", floor="0.15.0"),
        url="https://gitea.com/gitea/tea",
        verbs=(
            "issues.create",
            "issues.list",
            "issues.close",
            "pulls.create",
            "pulls.list",
            "pulls.checkout",
            "pulls.merge",
            "releases.create",
            "releases.list",
            "releases.assets",
            "repos.create",
            "repos.list",
            "repos.fork",
            "labels.list",
            "labels.create",
            "milestones.list",
            "milestones.create",
            "comments.add",
            "comments.list",
            "branches.list",
            "logins.add",
            "logins.list",
            "whoami",
            "clone",
            "api",
        ),
    ),
    Driver(
        "eclint",
        provision=Provision(kind="gitlab", repo="willemkokke/eclint"),
        url="https://gitlab.com/willemkokke/eclint",
    ),
    Driver("djlint", url="https://www.djlint.com/"),
    Driver("mypy", url="https://mypy.readthedocs.io/"),
    Driver("ty", verbs=("check",), url="https://docs.astral.sh/ty/"),
    Driver("twine", verbs=("upload", "check"), url="https://twine.readthedocs.io/"),
    Driver("git-changelog", url="https://pawamoy.github.io/git-changelog/"),
    Driver("git-cliff", url="https://git-cliff.org/"),
    Driver(
        "pyproject-build",
        attr="build",
        provision=Provision(package="build"),
        url="https://build.pypa.io/",
    ),
    Driver("cmake", url="https://cmake.org/documentation/"),
    Driver("ninja", url="https://ninja-build.org/"),
    Driver(
        "pytest",
        url="https://docs.pytest.org/",
        in_process=True,  # `tools.py` builds it in-process, via `pytest:main`
        provision=Provision(plugins=("pytest-cov",)),  # so --cov* is read too
    ),
    Driver(
        "python",
        provision=Provision(kind="python"),  # unpinned: whatever uv calls newest
        url="https://docs.python.org/3/using/cmdline.html",
    ),
    # The shells footman autocompletes for. Their stubs are hand-written (a
    # `source="manual"` driver is listed and paged but never extracted or
    # re-synced): what matters is `<shell>("command")` -> `<shell> -c command`,
    # not the shell binary's own hundred flags.
    Driver("bash", source="manual", url="https://www.gnu.org/software/bash/"),
    Driver("zsh", source="manual", url="https://www.zsh.org/"),
    Driver("fish", source="manual", url="https://fishshell.com/"),
    Driver("pwsh", source="manual", url="https://learn.microsoft.com/powershell/"),
    Driver("nu", source="manual", url="https://www.nushell.sh/"),
    Driver(
        "cmd",
        source="manual",
        url="https://learn.microsoft.com/windows-server/administration/windows-commands/cmd",
    ),
)


def _resolve(name: str) -> str | None:
    """The executable to read a tool from: plain `shutil.which`, everywhere.

    There was a host-read tier once — tools taken off the machine because
    fetching them per release was not yet possible — and on macOS it
    preferred a Homebrew **keg** over `PATH`, so an intentionally unlinked
    build was still the one read. Nothing is on that tier now: docker fetches
    its own static builds and git reads kernel.org's manuals, so every stub
    footman ships comes from something footman fetched. A resolver that
    consults the host has nothing left to resolve, and one spelling of "which
    binary" is worth more than a branch nobody reaches — a `provision --sync`
    prefix and a venv win, and a stale `/opt/homebrew/bin` console-script
    shim is never picked.
    """
    return shutil.which(name)


def installed(driver: Driver) -> bool:
    """Whether this machine has the tool to ask."""
    return _resolve(driver.name) is not None


def version(name: str) -> str:
    """`<tool> --version`, reduced to the version itself.

    Reads the binary *extraction* resolves, which is not always the one a
    task would run (`_resolve` prefers a Homebrew keg for host-read tools).
    The parsing is shared with `tools.Tool.installed_version` so only the
    choice of binary can ever differ, never the grammar.
    """
    return _read_version(name)[0]


def _read_version(name: str) -> tuple[str, str]:
    """`(version, diagnosis)` — the second names *why* the first is empty.

    An empty version has three very different causes — the spawn failed, the
    spawn hung, the output carried no version token — and a check that can't
    say which teaches nothing when it trips (the CI flake that motivated
    this reported `gh (—)` and left every hypothesis standing).
    """
    from livery import toolroom as tools

    # A suite tool may name a sibling that answers for it: ssh-keygen has no
    # version output at all, and ssh speaks for the OpenSSH release both
    # ship in.
    for sibling in DRIVERS:
        if sibling.name == name and sibling.version_of:
            name = sibling.version_of
            break
    binary = _resolve(name)
    if binary is None:
        return "", "not on PATH"
    # The probe's spelling is the baked tool's own: `ssh` only answers `-V`
    # (`--version` is an illegal option), `cmd` spells it `/c ver`. An
    # unbaked name resolves to a default `Tool`, whose spelling is the
    # `--version` everyone else speaks.
    spelling = getattr(tools, name.replace("-", "_"))._version_argv
    # A version read must never touch the network — see `_toolhelp.QUIET`.
    #
    # Through `run()`: `recorded=False` keeps a probe out of the run's story,
    # `timeout=` kills the tree rather than leaving a hung tool's workers
    # behind, the hidden console comes with any captured spawn, and `env=`
    # hands over exactly what `read_env` built — its subtraction survives the
    # trip, which under the old overlay it could not.
    from footman.context import run as _run

    try:
        done = _run(
            [binary, *spelling],
            recorded=False,
            timeout=30,
            nofail=True,
            env={**os.environ, **_toolhelp.QUIET},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "", f"spawn failed: {type(exc).__name__}: {exc}"
    if done.timed_out:
        return "", "timed out after 30s"
    found = _without_build_tail(tools.read_version(done.stdout or done.stderr))
    if found:
        return found, ""
    lines = (done.stdout or done.stderr).strip().splitlines()
    head = lines[0][:80] if lines else "<no output>"
    return "", f"no version token (exit {done.code}): {head!r}"


_BUILD_TAIL = re.compile(r"\.(?!post\d)[A-Za-z].*$")


def _without_build_tail(version: str) -> str:
    """A vendored build tail dropped, so the reading names a real release.

    PyPI ships `ninja` 1.13.0; the binary in it answers
    `1.13.0.git.kitware.jobserver-pipe-1`. The history keys on what was read,
    so that string became the base — and nothing in the index matches it, so
    ninja could not be primed at all.

    Only a *dot-attached* alphabetic tail goes, which is what a vendored
    build looks like. `0.6.0-wk.5` keeps its hyphenated series, because that
    is eclint's own release identity rather than a build of something else,
    and `.post1` is spared because it names a real published release.
    """
    return _BUILD_TAIL.sub("", version)


def in_process_capable(name: str) -> bool:
    """Whether the tool publishes a `[console_scripts]` entry point.

    That entry point is exactly what `Tool.__call__` resolves to run a tool
    inside footman's process, so its existence *is* the capability — no
    list to maintain, and it answers correctly for a tool footman has never
    heard of.
    """
    from livery import toolroom as tools

    return tools._console_entrypoint(name) is not None


def extract(driver: Driver, home: Path | None = None) -> ToolSpec:
    """Ask the installed tool to describe itself, best source first.

    *home* is the throwaway home the caller gave this reading, when it gave
    one — see `_anonymous` for why it cannot be discovered here.

    click hands over its parameters as data — including `secondary_opts`,
    the negation a `--help` scrape can only find if the tool happens to
    mention it in prose. So structure wins when it is available, and the
    help text covers everyone else.
    """
    spec = ToolSpec(name=driver.name)
    if driver.source in {"auto", "click"}:
        spec = _from_click(driver) or spec
    if not spec.verbs and driver.source in {"auto", "help"}:
        # A fetched manual names its own release, and there is no binary
        # to ask: the whole point of reading the pages is that the version
        # they document never has to be installed.
        tree = _toolhelp._fetched_manpath() if driver.man else ""
        spec = _toolhelp.from_help(
            driver.name,
            binary=_resolve(driver.name),
            verbs=driver.wanted,
            version=(
                _toolhelp.man_version(Path(tree), driver.name)
                if tree
                else version(driver.name)
            ),
            in_process=in_process_capable(driver.name),
            flag=driver.help_flag,
            man=driver.man,
            shorts=driver.shorts,
        )
    return _anonymous(
        _rebase(spec, driver.base) if driver.base else spec,
        *([home] if home else []),
        Path.home(),
    )


def _spellings_of(root: str) -> str:
    """A pattern matching *root* however this platform spells it.

    Windows separates with either slash, and a path that passed through
    `%TEMP%` may carry an 8.3 short name (`WILLEM~1`) where the caller
    holds the long one. Each segment is therefore matched as itself *or*
    as a short name for itself: a leading run of the segment, then `~`,
    then digits.
    """
    parts = []
    for segment in re.split(r"[\\/]+", root):
        if not segment:
            parts.append("")
            continue
        stem = re.escape(segment[:6])
        parts.append(f"(?:{re.escape(segment)}|{stem}~\\d+)")
    return r"[\\/]+".join(parts)


def _anonymous(spec: ToolSpec, *homes: Path) -> ToolSpec:
    """Replace every home directory in *homes* with `~` throughout *spec*.

    The homes are given rather than looked up, because the walk gives a
    tool a home of its own and `Path.home()` cannot see it. Inside a run
    the overlay that sets `HOME` writes to `ctx.env` — the *children's*
    environment — so the tool being read echoes the throwaway home while
    this process still reports the real one. Asking `Path.home()` matched
    nothing and scrubbed nothing, and the first Windows gather recorded
    docker's config default as
    `C:\\Users\\…\\Temp\\footman-gather-2_wvx66g\\docker-29.6.2\\home\\.docker`
    — a path with a *random* directory in it, so every run on every
    platform would have written a different value and disagreed with every
    other platform forever.

    It only ever worked when called bare, which is the path the test took.


    Tools that default an option to a path under `$HOME` report it
    expanded: docker says its config lives in `/Users/willem/.docker`, and
    that string went into the snapshot, the store, and the published stub —
    one machine's home directory shipped to PyPI as if it were docker's
    documented default.

    It is also the one difference guaranteed to divide every platform.
    Linux reads `/home/runner/.docker` and Windows
    `C:\\Users\\runneradmin\\.docker` for the same option of the same
    release, so each leg of the matrix would overwrite the last, every
    weekly run would record a change nobody made, and the release gate —
    which fires on "did anything change" — would never be quiet again.

    `~` is what the tool means and what every platform can agree on.
    """
    roots = [str(home).rstrip("/\\") for home in homes if str(home).strip("/\\")]
    if not roots:  # pragma: no cover - a home of "/" is not a home
        return spec
    # Longest first: a throwaway home nested under the real one must be
    # replaced whole, not left as `~/…/home/.docker`.
    roots.sort(key=len, reverse=True)
    # Matched by pattern, not by equality, because Windows has more than one
    # spelling for the same directory. A gather set `HOME` to a path under
    # `%TEMP%` and docker echoed it back as
    # `C:\Users\WILLEM~1\AppData\Local\Temp\…` — the 8.3 short name, where
    # the string handed to the scrub had the long one. `str.replace` saw two
    # different paths, wrote neither, and a shipped stub carried a
    # machine's directory again. Case is the same trap: Windows does not
    # distinguish it and a comparison does.
    matchers = [re.compile(_spellings_of(root), re.IGNORECASE) for root in roots]

    def scrub(text: str) -> str:
        if not isinstance(text, str):
            return text
        for matcher in matchers:
            text = matcher.sub("~", text)
        return text

    verbs = tuple(
        replace(
            verb,
            help=scrub(verb.help),
            options=tuple(
                replace(opt, help=scrub(opt.help), default=scrub(opt.default))
                for opt in verb.options
            ),
        )
        for verb in spec.verbs
    )
    return replace(spec, help=scrub(spec.help), verbs=verbs)


def _rebase(spec: ToolSpec, base: tuple[str, ...]) -> ToolSpec:
    """A tool bound to one verb calls it directly: `tools.ruff_format(...)`.

    So that verb's options become the stub's `__call__`, and the rest of
    the tool is somebody else's stub.
    """
    wanted = ".".join(base).replace("-", "_")
    for verb in spec.verbs:
        if verb.name == wanted:
            return ToolSpec(
                name=spec.name,
                help=verb.help or spec.help,
                version=spec.version,
                verbs=(Verb(name="", help=verb.help, options=verb.options),),
                in_process=spec.in_process,
            )
    return ToolSpec(name=spec.name, help=spec.help, version=spec.version)


def _from_click(driver: Driver) -> ToolSpec | None:
    """A spec from the tool's click command, when it is a click tool.

    Only when the importable package and the PATH binary are the **same
    release**. The entry point loads from this process's environment while
    the binary comes from `PATH`, and nothing ties the two together: a prime
    reading mkdocs 1.4.0 from a throwaway venv would import *this* venv's
    1.6.1 and record its surface under 1.4.0's label — which is exactly what
    happened, nine empty deltas in a row, before this guard. A mismatch (or
    a binary whose version cannot be read) falls through to the help path,
    which always asks the binary itself.
    """
    from livery import toolroom as tools

    entry = tools._console_entrypoint(driver.name)
    if entry is None:
        return None
    packaged = getattr(getattr(entry, "dist", None), "version", "") or ""
    binary = version(driver.name)
    if not binary or binary != packaged:
        return None
    try:
        command = entry.load()
    except Exception:  # a tool that won't import can't describe itself
        return None
    if not hasattr(command, "params"):
        return None  # not click: argparse mains and plain functions land here
    spec = _toolspec.from_click(command, name=driver.name, version=version(driver.name))
    return _select(spec, driver.wanted)


def _select(spec: ToolSpec, verbs: tuple[str, ...]) -> ToolSpec:
    """Keep the verbs the driver asked for, plus the tool's own options."""
    if not verbs:
        return spec
    wanted = {v.replace("-", "_") for v in verbs} | {""}
    kept = tuple(v for v in spec.verbs if v.name in wanted)
    return ToolSpec(
        name=spec.name,
        help=spec.help,
        version=spec.version,
        verbs=kept,
        in_process=spec.in_process,
    )


def find(key: str) -> Driver | None:
    """The driver for `tools.<key>`."""
    for driver in DRIVERS:
        if driver.key == key:
            return driver
    return None
