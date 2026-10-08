"""``fm devenv``: local development environments by name, in docker or on the host.

An environment is a forge with its runners and its seeded accounts,
brought up by name, kept until removed, and removed whole by one
verb. Its state is one directory under the runner's data directory,
`forge-dev/envs/<name>/`: the forge's data, the runners' registrations
and working directories, the seed's credentials, the port it took and
the pid files of its processes. The caches every environment shares,
the tool store, uv's cache, conan's home and footman's data for the
jobs, live beside them in `forge-dev/cache/`, so a new environment is
warm from its first job and removing one removes no cache.

Host mode runs Gitea and its runners as processes of this machine,
from the `gitea` and `gitea_runner` records the store supplies; no
system service, no daemon registered with the operating system, so
`fm devenv.rm` leaves no trace. Docker mode is the compose rig
`fm forge.dev.up` runs, the one environment named `dev`, until the
rig takes a name of its own. A runner is labelled
`<env>-<host>-<arch>-<nn>`, the host and architecture its own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import livery.footman as footman
from livery.footman import doc, fail, group

devenv = group("devenv", help="Local development environments by name")

MODES = ("host", "docker")
"""Where an environment runs: as processes of this machine, or in the compose rig."""

DEFAULT_ENV = "dev"
"""The environment a verb addresses when none is named."""

GITEA_VERSION = "28.0.0"
"""The Gitea release the `gitea` record supplies for host mode."""

RUNNER_VERSION = "4.0.0"
"""The runner release the `gitea_runner` record supplies for host mode."""

ADMIN = "livery-admin"
"""The seeded administrator, the same on every environment."""

ORG = "livery"
"""The seeded organisation the loop's repositories live in."""

CACHES = ("footman", "uv", "conan", "actcache")
"""The rig's shared caches under `forge-dev/cache/`, one directory each."""

HEALTH_TIMEOUT = 60.0
"""How long `up` waits for Gitea to answer its health endpoint."""

TASKLIST = "tasklist"
"""Windows' process lister, for the liveness probe."""

TASKKILL = "taskkill"
"""Windows' process killer, for `down`."""


def rig_dir() -> Path:
    """The rig's own directory under the runner's data directory."""
    from livery.footman import data_dir

    return data_dir() / "forge-dev"


def envs_dir() -> Path:
    """Where the environments live, one directory each."""
    return rig_dir() / "envs"


def cache_dir() -> Path:
    """The caches every environment shares."""
    return rig_dir() / "cache"


def this_host() -> str:
    """This machine's host key, `macos-arm`."""
    from livery.workshop._tools import this_host as here

    return here()


def runner_label(env: str, host: str, number: int) -> str:
    """The label a runner registers under: `<env>-<host>-<arch>-<nn>`."""
    return f"{env}-{host}-{number:02d}"


def parse_runners(spec: str) -> tuple[str, ...]:
    """The runner shapes *spec* names, one per runner, in order.

    A comma-separated list of `host`, or `host*<n>` for that many.
    Container shapes come with the container setups and refuse here
    by name.
    """
    shapes: list[str] = []
    for token in (part.strip() for part in spec.split(",")):
        if not token:
            continue
        shape, _, count = token.partition("*")
        if shape != "host":
            fail(
                f"--runners names {shape!r}; the host shape is the one this"
                " machine runs, and a container shape comes with the container"
                " setups"
            )
        times = 1
        if count:
            if not count.isdigit() or int(count) < 1:
                fail(f"--runners: {token!r} is not host*<n> with n at least 1")
            times = int(count)
        shapes.extend([shape] * times)
    if not shapes:
        fail("--runners names no runner; the default is one host runner")
    return tuple(shapes)


@dataclass(frozen=True)
class Environment:
    """One environment as its directory holds it.

    Attributes:
        name: The environment's name, its directory's.
        mode: `host` or `docker`.
        directory: Where its state lives.
    """

    name: str
    mode: str
    directory: Path

    @property
    def env_file(self) -> Path:
        """The environment's own `KEY=VALUE` file: its port and credentials."""
        return self.directory / "env"

    @property
    def gitea_dir(self) -> Path:
        """Gitea's work path: its configuration, data and log."""
        return self.directory / "gitea"

    @property
    def app_ini(self) -> Path:
        """Gitea's configuration file."""
        return self.gitea_dir / "custom" / "conf" / "app.ini"

    @property
    def runners_dir(self) -> Path:
        """One directory per runner, numbered from `01`."""
        return self.directory / "runners"

    def runner_dir(self, number: int) -> Path:
        """Runner *number*'s directory."""
        return self.runners_dir / f"{number:02d}"

    def values(self) -> dict[str, str]:
        """The env file's pairs; empty when there is none yet."""
        if not self.env_file.is_file():
            return {}
        pairs: dict[str, str] = {}
        for line in self.env_file.read_text("utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                key, _, value = line.partition("=")
                pairs[key.strip()] = value.strip()
        return pairs

    def update(self, updates: dict[str, str]) -> None:
        """Set *updates* in the env file, key by key, keeping the rest."""
        pairs = self.values()
        pairs.update(updates)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.env_file.write_text(
            "".join(f"{key}={value}\n" for key, value in sorted(pairs.items())),
            encoding="utf-8",
        )

    @property
    def url(self) -> str:
        """Gitea's URL, empty before the first `up`."""
        return self.values().get("GITEA_URL", "")

    @property
    def token(self) -> str:
        """The seeded API token, empty before the first `up`."""
        return self.values().get("GITEA_TOKEN", "")


def environment(name: str) -> Environment:
    """The environment *name* as its directory records it; a refusal for a bad name."""
    if not name or not all(c.isalnum() or c == "-" for c in name) or name[0] == "-":
        fail(f"{name!r} is not an environment name: letters, digits and dashes")
    directory = envs_dir() / name
    mode = Environment(name, "", directory).values().get("MODE", "")
    return Environment(name, mode, directory)


def environments() -> tuple[Environment, ...]:
    """Every environment the rig holds, by name."""
    if not envs_dir().is_dir():
        return ()
    return tuple(
        environment(child.name)
        for child in sorted(envs_dir().iterdir())
        if child.is_dir() and (child / "env").is_file()
    )


# --- processes -------------------------------------------------------------------


def spawn_detached(argv: Sequence[str], *, cwd: Path, log: Path) -> int:
    """Start *argv* as a process of its own that outlives this one; its pid.

    Stdout and stderr go to *log*, appended; stdin is closed. The
    child leads its own session (its own process group on Windows), so
    the shell that ran the verb never owns it and `down` can stop it
    by its pid alone. The environment is this process's own, passed
    on purpose: a daemon inherits the entered environment, the store's
    node on PATH included, which the runner's JavaScript actions need.
    """
    log.parent.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    with log.open("ab") as sink:
        if sys.platform == "win32":
            flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
                subprocess, "CREATE_NO_WINDOW", 0
            )
            child = subprocess.Popen(
                list(argv),
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=sink,
                stderr=subprocess.STDOUT,
                creationflags=flags,
                env=environment,
            )
        else:
            child = subprocess.Popen(
                list(argv),
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=sink,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=environment,
            )
    return child.pid


def alive(pid: int) -> bool:
    """Whether a process with *pid* exists."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        listed = subprocess.run(
            [TASKLIST, "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        return str(pid) in listed.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def stop(pid: int, *, timeout: float = 15.0) -> bool:
    """Stop the process *pid* and wait for it to go; whether it went."""
    if not alive(pid):
        return True
    if sys.platform == "win32":
        subprocess.run(
            [TASKKILL, "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            check=False,
        )
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return True
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not alive(pid):
            return True
        time.sleep(0.2)
    if sys.platform != "win32":
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            return True
        time.sleep(0.5)
    return not alive(pid)


#: The seams a test replaces: how a process is started, whether one
#: lives, and how one is stopped.
SPAWN: Callable[..., int] = spawn_detached
ALIVE: Callable[[int], bool] = alive
STOP: Callable[..., bool] = stop


def _read_pid(path: Path) -> int:
    try:
        return int(path.read_text("utf-8").strip() or "0")
    except (OSError, ValueError):
        return 0


def _pid_files(env: Environment) -> list[Path]:
    """Every pid file the environment's processes left, Gitea's first."""
    found = [env.directory / "gitea.pid"]
    if env.runners_dir.is_dir():
        found += sorted(env.runners_dir.glob("*/runner.pid"))
    return found


def free_port() -> int:
    """A TCP port nothing listens on right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


# --- the binaries ----------------------------------------------------------------


def binary(name: str, version: str, *, offline: bool = False) -> Path:
    """The executable of *name* at *version* from the store; supplied when absent.

    The record comes from the workspace's catalogue and the artifact
    through the store's sources and the origin, verified by the
    record's digest; a second call finds it present and downloads
    nothing.
    """
    from livery.toolroom.store import Store
    from livery.workshop._extensions import workspace_root
    from livery.workshop._tools import catalogue, sources, store_home

    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    listing = catalogue(root, offline=offline)
    store = Store(store_home(), sources=sources(root), offline=offline)
    deployment = listing.deployment(name, version, store.host)
    ensured = store.supply(name, "download", version, deployment)
    return ensured.tool_dir / deployment.entry_points[0]


#: The seam a test replaces: where the binaries come from.
BINARY: Callable[..., Path] = binary


# --- gitea in host mode ------------------------------------------------------------


def app_ini(env: Environment, port: int) -> str:
    """Gitea's configuration for *env* on *port*: sqlite, actions on, no ssh."""
    gitea = env.gitea_dir
    return (
        "APP_NAME = livery dev\n"
        "RUN_MODE = prod\n"
        f"WORK_PATH = {gitea}\n"
        "\n"
        "[server]\n"
        "PROTOCOL = http\n"
        "DOMAIN = localhost\n"
        "HTTP_ADDR = 127.0.0.1\n"
        f"HTTP_PORT = {port}\n"
        f"ROOT_URL = http://localhost:{port}/\n"
        "START_SSH_SERVER = false\n"
        "DISABLE_SSH = true\n"
        "LFS_START_SERVER = false\n"
        "OFFLINE_MODE = true\n"
        f"APP_DATA_PATH = {gitea / 'data'}\n"
        "\n"
        "[database]\n"
        "DB_TYPE = sqlite3\n"
        f"PATH = {gitea / 'data' / 'gitea.db'}\n"
        "\n"
        "[repository]\n"
        f"ROOT = {gitea / 'data' / 'repositories'}\n"
        "\n"
        "[security]\n"
        "INSTALL_LOCK = true\n"
        f"SECRET_KEY = {secrets.token_hex(32)}\n"
        f"INTERNAL_TOKEN = {secrets.token_hex(32)}\n"
        "\n"
        "[service]\n"
        "DISABLE_REGISTRATION = true\n"
        "REQUIRE_SIGNIN_VIEW = false\n"
        "\n"
        "[actions]\n"
        "ENABLED = true\n"
        "DEFAULT_ACTIONS_URL = github\n"
        "\n"
        "[packages]\n"
        "ENABLED = true\n"
        "\n"
        "[log]\n"
        "MODE = file\n"
        "LEVEL = Info\n"
        f"ROOT_PATH = {gitea / 'log'}\n"
    )


def runner_config(env: Environment, number: int, *, capacity: int = 4) -> str:
    """The runner's configuration: the rig's caches in its jobs' environment."""
    from livery.footman import directory_variable

    caches = cache_dir()
    return (
        "log:\n"
        "  level: info\n"
        "runner:\n"
        "  file: .runner\n"
        f"  capacity: {capacity}\n"
        "  envs:\n"
        f"    {directory_variable('DATA_DIR')}: {caches / 'footman'}\n"
        f"    UV_CACHE_DIR: {caches / 'uv'}\n"
        f"    CONAN_HOME: {caches / 'conan'}\n"
        "  timeout: 3h\n"
        "  fetch_interval: 2s\n"
        "cache:\n"
        "  enabled: true\n"
        f"  dir: {caches / 'actcache'}\n"
        "host:\n"
        f"  workdir_parent: {env.runner_dir(number) / 'work'}\n"
    )


def _gitea(env: Environment, exe: Path, *args: str) -> footman.Result:
    """One gitea CLI call against *env*'s configuration and work path."""
    return footman.run(
        [
            str(exe),
            "--config",
            str(env.app_ini),
            "--work-path",
            str(env.gitea_dir),
            *args,
        ],
        cwd=env.gitea_dir,
        nofail=True,
        timeout=120.0,
    )


def _api(
    url: str, path: str, token: str, *, method: str = "GET", body: str = ""
) -> int:
    """One API call; the HTTP status, 0 when the server does not answer."""
    request = urllib.request.Request(
        f"{url}/api/v1{path}",
        data=body.encode() if body else None,
        method=method,
        headers={"Authorization": f"token {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return int(response.status)
    except urllib.error.HTTPError as error:
        return error.code
    except (urllib.error.URLError, OSError):
        return 0


def _healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/api/healthz", timeout=2):
            return True
    except (urllib.error.URLError, OSError):
        return False


#: The seams a test replaces: the health probe and the API call.
HEALTHY: Callable[[str], bool] = _healthy
API: Callable[..., int] = _api


def _start_gitea(env: Environment, exe: Path) -> tuple[str, int]:
    """Start Gitea for *env* unless it runs; its URL and its pid."""
    values = env.values()
    port = int(values.get("GITEA_PORT", "0") or 0)
    if not port:
        port = free_port()
    url = f"http://localhost:{port}"
    pid_file = env.directory / "gitea.pid"
    pid = _read_pid(pid_file)
    if pid and ALIVE(pid) and HEALTHY(url):
        return url, pid
    if not env.app_ini.is_file():
        env.app_ini.parent.mkdir(parents=True, exist_ok=True)
        env.app_ini.write_text(app_ini(env, port), encoding="utf-8")
    (env.gitea_dir / "data").mkdir(parents=True, exist_ok=True)
    env.update({"MODE": "host", "GITEA_PORT": str(port), "GITEA_URL": url})
    pid = SPAWN(
        [
            str(exe),
            "--config",
            str(env.app_ini),
            "--work-path",
            str(env.gitea_dir),
            "web",
        ],
        cwd=env.gitea_dir,
        log=env.gitea_dir / "gitea.log",
    )
    pid_file.write_text(f"{pid}\n", encoding="utf-8")
    deadline = time.monotonic() + HEALTH_TIMEOUT
    while time.monotonic() < deadline:
        if HEALTHY(url):
            return url, pid
        if not ALIVE(pid):
            fail(
                f"gitea for {env.name} exited before it answered; its log is"
                f" {env.gitea_dir / 'gitea.log'}"
            )
        time.sleep(0.5)
    fail(
        f"gitea for {env.name} did not answer {url}/api/healthz within"
        f" {HEALTH_TIMEOUT:.0f}s; its log is {env.gitea_dir / 'gitea.log'}"
    )


def _seed(env: Environment, exe: Path, url: str) -> dict[str, str]:
    """Seed *env*'s Gitea: the admin, an API token, the org, a runner token.

    A token that still answers is kept; a missing or dead one is
    minted again. The runner token is minted on every up, since one
    registers one runner.
    """
    token = env.token
    if not (token and API(url, "/user", token) == 200):
        created = _gitea(
            env,
            exe,
            "admin",
            "user",
            "create",
            "--username",
            ADMIN,
            "--password",
            "livery-dev-password",
            "--email",
            "admin@livery.local",
            "--admin",
            "--must-change-password=false",
        )
        said = created.stdout + created.stderr
        if created.code != 0 and "already exists" not in said:
            fail(f"gitea for {env.name}: the admin user was not created:\n{said}")
        minted = _gitea(
            env,
            exe,
            "admin",
            "user",
            "generate-access-token",
            "--username",
            ADMIN,
            "--token-name",
            f"seed-{int(time.time())}",
            "--scopes",
            "all",
            "--raw",
        )
        if minted.code != 0:
            fail(
                f"gitea for {env.name}: no API token minted:"
                f"\n{minted.stdout}{minted.stderr}"
            )
        token = minted.stdout.strip().split()[-1]
    if API(url, f"/orgs/{ORG}", token) != 200:
        status = API(
            url, "/orgs", token, method="POST", body=f'{{"username": "{ORG}"}}'
        )
        if status not in (201, 422):
            fail(f"gitea for {env.name}: the {ORG} org answered HTTP {status}")
    runner = _gitea(env, exe, "actions", "generate-runner-token")
    if runner.code != 0:
        fail(
            f"gitea for {env.name}: no runner token minted:"
            f"\n{runner.stdout}{runner.stderr}"
        )
    values = {
        "GITEA_URL": url,
        "GITEA_TOKEN": token,
        "GITEA_RUNNER_TOKEN": runner.stdout.strip(),
    }
    env.update(values)
    return values


def _start_runner(
    env: Environment, exe: Path, number: int, url: str, registration: str
) -> tuple[str, int]:
    """Register runner *number* once and start it unless it runs; its label and pid."""
    home = env.runner_dir(number)
    home.mkdir(parents=True, exist_ok=True)
    label = runner_label(env.name, this_host(), number)
    pid_file = home / "runner.pid"
    pid = _read_pid(pid_file)
    if pid and ALIVE(pid):
        return label, pid
    if not (home / ".runner").is_file():
        registered = footman.run(
            [
                str(exe),
                "register",
                "--instance",
                url,
                "--token",
                registration,
                "--name",
                label,
                "--labels",
                f"{label}:host",
                "--no-interactive",
            ],
            cwd=home,
            nofail=True,
            timeout=120.0,
        )
        if registered.code != 0:
            fail(
                f"runner {label} was not registered:"
                f"\n{registered.stdout}{registered.stderr}"
            )
    (home / "config.yaml").write_text(runner_config(env, number), encoding="utf-8")
    for name in CACHES:
        (cache_dir() / name).mkdir(parents=True, exist_ok=True)
    pid = SPAWN(
        [str(exe), "daemon", "--config", str(home / "config.yaml")],
        cwd=home,
        log=home / "runner.log",
    )
    pid_file.write_text(f"{pid}\n", encoding="utf-8")
    return label, pid


def _share(values: dict[str, str]) -> None:
    """Make *values* the cascade's current forge: the shared env file, key by key."""
    from livery.footman import config_dir

    path = config_dir() / ".repo.shared.env"
    lines = path.read_text("utf-8").splitlines() if path.is_file() else []
    for key, value in sorted(values.items()):
        entry = f"{key}={value}"
        for index, line in enumerate(lines):
            if line.split("=", 1)[0].strip() == key:
                lines[index] = entry
                break
        else:
            lines.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def up_host(
    env: Environment, runners: tuple[str, ...], *, offline: bool = False
) -> None:
    """Bring *env* up in host mode: Gitea, the seed, then each runner."""
    gitea = BINARY("gitea", GITEA_VERSION, offline=offline)
    runner = BINARY("gitea_runner", RUNNER_VERSION, offline=offline)
    url, _pid = _start_gitea(env, gitea)
    values = _seed(env, gitea, url)
    labels: list[str] = []
    for number, _shape in enumerate(runners, start=1):
        label, _ = _start_runner(env, runner, number, url, values["GITEA_RUNNER_TOKEN"])
        labels.append(label)
    env.update({"RUNNERS": ",".join(runners), "LABELS": ",".join(labels)})
    _share({"GITEA_URL": url, "GITEA_TOKEN": values["GITEA_TOKEN"]})
    print(f"  {env.name}: gitea {url}, host mode, {len(labels)} runner(s)")
    for label in labels:
        print(f"  {env.name}: runner {label}")


def up_docker(env: Environment) -> None:
    """Bring the docker-mode environment up through the compose rig."""
    if env.name != DEFAULT_ENV:
        fail(
            f"docker mode has one environment, {DEFAULT_ENV}, until the compose"
            f" rig takes a name; {env.name!r} is a host-mode name"
        )
    result = footman.run(
        [
            sys.executable,
            "-m",
            "livery.footman",
            "forge.dev.up",
            "--profile=gitea",
            "--with-docker",
        ],
        nofail=True,
        timeout=1800.0,
    )
    print(result.stdout.rstrip("\n"))
    if result.code != 0:
        fail(f"forge.dev.up exited {result.code}:\n{result.stderr}")
    env.update({"MODE": "docker", "GITEA_URL": "http://localhost:3000"})


def down(env: Environment) -> list[str]:
    """Stop *env*'s processes and keep its directory; the lines to print."""
    if env.mode == "docker":
        result = footman.run(
            [
                sys.executable,
                "-m",
                "livery.footman",
                "forge.dev.down",
                "--profile=gitea",
            ],
            nofail=True,
            timeout=600.0,
        )
        if result.code != 0:
            fail(f"forge.dev.down exited {result.code}:\n{result.stderr}")
        return [f"  {env.name}: stopped (docker)"]
    lines: list[str] = []
    for pid_file in _pid_files(env):
        pid = _read_pid(pid_file)
        if pid and ALIVE(pid):
            went = STOP(pid)
            what = "runner" if pid_file.name == "runner.pid" else "gitea"
            lines.append(
                f"  {env.name}: {what} {pid} {'stopped' if went else 'did not stop'}"
            )
        pid_file.unlink(missing_ok=True)
    if not lines:
        lines.append(f"  {env.name}: nothing was running")
    return lines


def remove(env: Environment) -> list[str]:
    """Stop *env* and remove its directory whole; the lines to print."""
    if not env.directory.is_dir():
        return [f"  {env.name}: no such environment"]
    lines = down(env)
    shared_url = env.url
    shutil.rmtree(env.directory, ignore_errors=True)
    if shared_url:
        _forget(shared_url)
    lines.append(f"  {env.name}: removed {env.directory}")
    return lines


def _forget(url: str) -> None:
    """Drop the cascade's forge keys when they name *url*, a forge that is gone."""
    from livery.footman import config_dir

    path = config_dir() / ".repo.shared.env"
    if not path.is_file():
        return
    lines = path.read_text("utf-8").splitlines()
    if f"GITEA_URL={url}" not in lines:
        return
    kept = [
        line
        for line in lines
        if line.split("=", 1)[0].strip() not in ("GITEA_URL", "GITEA_TOKEN")
    ]
    path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")


def purge_cache() -> list[str]:
    """Remove the rig's shared caches, naming each; the lines to print."""
    lines: list[str] = []
    for name in CACHES:
        target = cache_dir() / name
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
            lines.append(f"  cache: {name} removed ({target})")
    if not lines:
        lines.append("  cache: nothing to remove")
    return lines


def describe(env: Environment) -> str:
    """One line on *env*: its mode, its URL, and what runs."""
    values = env.values()
    if env.mode == "docker":
        return f"  {env.name}: docker, {values.get('GITEA_URL', '')}"
    running = sum(1 for pid_file in _pid_files(env) if ALIVE(_read_pid(pid_file)))
    total = len(_pid_files(env))
    labels = values.get("LABELS", "")
    return (
        f"  {env.name}: host, {values.get('GITEA_URL', '')}, {running}/{total}"
        f" process(es) running" + (f", runners {labels}" if labels else "")
    )


# --- the verbs ---------------------------------------------------------------------


@devenv.task(name="up")
def devenv_up(
    env: Annotated[str, doc("the environment's name")] = DEFAULT_ENV,
    mode: Annotated[str, doc("host or docker")] = "host",
    runners: Annotated[str, doc("the runners: host, or host*<n>")] = "host",
    offline: Annotated[bool, doc("supply the binaries from the store alone")] = False,
) -> None:
    """Bring an environment up by name; idempotent, and the recovery procedure.

    Host mode starts Gitea and each runner as processes of this
    machine from the store's records, seeds the administrator, the
    API token and the organisation, registers each runner once under
    its label, and makes the environment the cascade's current forge.
    Docker mode is the compose rig, the one environment named `dev`.
    """
    if mode not in MODES:
        fail(f"--mode={mode!r} is not one of {', '.join(MODES)}")
    found = environment(env)
    if found.mode and found.mode != mode:
        fail(
            f"{env} exists in {found.mode} mode; `{footman.prog()} devenv.rm"
            f" --env={env}` first, or name another environment"
        )
    shapes = parse_runners(runners)
    if mode == "docker":
        up_docker(found)
        return
    up_host(found, shapes, offline=offline)


@devenv.task(name="ls")
def devenv_ls() -> None:
    """List the environments: mode, URL, and what runs."""
    found = environments()
    if not found:
        print(f"  no environments under {envs_dir()}")
        return
    for env in found:
        print(describe(env))


@devenv.task(name="down")
def devenv_down(
    env: Annotated[str, doc("the environment's name")] = DEFAULT_ENV,
) -> None:
    """Stop an environment's processes and keep its state."""
    found = environment(env)
    if not found.mode:
        fail(f"{env}: no such environment; `{footman.prog()} devenv.ls` lists them")
    for line in down(found):
        print(line)


@devenv.task(name="rm")
def devenv_rm(
    env: Annotated[str, doc("the environment's name")] = DEFAULT_ENV,
    purge_caches: Annotated[bool, doc("remove the rig's shared caches too")] = False,
) -> None:
    """Stop an environment and remove its directory; the caches stay unless asked."""
    found = environment(env)
    for line in remove(found):
        print(line)
    if purge_caches:
        for line in purge_cache():
            print(line)
