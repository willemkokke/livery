"""The local forge containers: compose and seeds, as a footman plugin.

`fm forge.dev.up` starts and seeds Gitea (with its act_runner) and
GitLab (with its shell-executor runner) from the compose file shipped
in this package; the minted credentials land key by key in the
shared env file in the runner's config directory
(``.repo.shared.env``), which the cascade and live test runs read.
Every verb is idempotent: re-running it is the recovery procedure, and
every seed probes before acting.

This module is the `footman.tasks` entry point named ``livery.forge``.
A workspace mounts it by listing ``livery.forge`` in its layers; a
repository that does not is never offered these tasks. Unlike the rest
of livery.forge it imports footman and toolroom, which are present by
construction: the only loader is footman's own ``plugin()``, and only
a workshop workspace mounts layers, so livery-forge still declares no
dependency.

``fm forge.fixtures.record`` re-records the conformance cassettes and
registers only in forge's own source checkout, where the test suite
it runs exists; a wheel install never offers it.
"""

from __future__ import annotations

import json
import os
import platform
import secrets
import time
import urllib.error
import urllib.request
from importlib import resources
from pathlib import Path
from typing import Annotated

import livery.footman as footman
from livery.footman import doc, fail, group
from livery.toolroom import tools

forge = group("forge", help="livery.forge development")
dev = forge.group("dev", help="Local forge containers (Gitea and GitLab)")

#: Forge's own test suite, present only in a source checkout: this
#: file is src/livery/forge/_dev/__init__.py, so the package directory
#: holding tests/ is four parents up.
_FORGE_TESTS = Path(__file__).resolve().parents[4] / "tests"

#: The conformance suite of each backend, and the concurrency its
#: server absorbs: one single-node GitLab takes about four concurrent
#: writers before its own internals time out (Gitaly deadlines), and
#: GitHub's scratch organisation the same.
_SUITES: dict[str, tuple[str, tuple[str, ...]]] = {
    "gitea": ("test_gitea_conformance.py", ()),
    "gitlab": ("test_gitlab_conformance.py", ("-n", "4")),
    "github": ("test_github_conformance.py", ("-n", "4")),
}


def _run_suites(
    scenario: str, backend: str, switch: dict[str, str], *, live: bool
) -> None:
    """Run the conformance suites, *switch* in their environment.

    *live* probes each local backend's container first and refuses
    naming the up verb, so a missing container fails before the suite
    spends its start-up on it. GitHub scratch goes to the e2e
    organisation, never the signed-in user's profile.
    """
    backends = (backend,) if backend else tuple(_SUITES)
    for name in backends:
        if name not in _SUITES:
            fail(f"unknown backend {name!r}: gitea, gitlab, or github")
    if live:
        for name in backends:
            if name in _FORGE_PROFILES:
                _require_forge_up(name)
    only = ("-k", scenario) if scenario else ()
    for name in backends:
        suite, workers = _SUITES[name]
        env = {**os.environ, **switch}
        if name == "github":
            env["FORGE_E2E_OWNER"] = "livery-forge-e2e"
        # A child, never in this process: pytest-xdist hands its
        # workers the session's option dict, which in the runner's
        # own process carries footman's argv proxy and cannot be
        # serialised.
        tools.pytest.opts(env=env, capture=False, in_process=False)(
            str(_FORGE_TESTS / suite), *workers, *only
        )


def _require_forge_up(kind: str) -> None:
    """Refuse until *kind*'s local container answers its version endpoint."""
    url, path = (
        (_GITEA_URL, "/api/v1/version")
        if kind == "gitea"
        else (_GITLAB_URL, "/api/v4/version")
    )
    try:
        with urllib.request.urlopen(f"{url}{path}", timeout=5) as answer:
            answer.read()
    except urllib.error.HTTPError:
        return  # answered: GitLab's version endpoint refuses an anonymous read
    except (urllib.error.URLError, OSError):
        fail(
            f"the local {kind} is not up at {url}:"
            f" `{footman.prog()} forge.dev.up --profile={kind}`"
        )


if _FORGE_TESTS.is_dir():
    fixtures = forge.group("fixtures", help="Recorded HTTP fixtures (cassettes)")

    @fixtures.task(name="record")
    def fixtures_record(
        scenario: Annotated[str, doc("record only this scenario, by its name")] = "",
        backend: Annotated[str, doc("record only gitea, gitlab, or github")] = "",
    ) -> None:
        """Re-record the conformance cassettes from the live containers.

        Runs the backend conformance suites against the seeded
        containers (`fm forge.dev.up` first) and rewrites the
        cassettes under forge's tests/cassettes/. Review the diff like
        code: a changed exchange is a changed contract with the
        server.

        A new scenario needs only its own recording: name it with
        ``--scenario`` (and a backend with ``--backend``) rather than
        re-recording every exchange, which spends live quota and
        rewrites cassettes nothing asked to change.
        """
        # The recording switch travels as explicit child env, never an
        # ambient write.
        _run_suites(scenario, backend, {"FORGE_RECORD": "1"}, live=True)

    @forge.task(name="conformance")
    def conformance(
        scenario: Annotated[str, doc("run only this scenario, by its name")] = "",
        backend: Annotated[str, doc("run only gitea, gitlab, or github")] = "",
        live: Annotated[
            bool, doc("against the seeded containers, the cassettes untouched")
        ] = False,
    ) -> None:
        """Run the conformance suites: the cassettes' replay, or live.

        Replay is the gate's own run, with no network and no
        credential. ``--live`` runs against the seeded containers
        (`fm forge.dev.up` first) and rewrites nothing; a local
        backend that is not up is refused by name before its suite
        starts, and GitHub's live arm reads the e2e organisation's
        credential the way the recorder does.
        """
        switch = {"FORGE_LIVE": "1"} if live else {}
        _run_suites(scenario, backend, switch, live=live)


_GITEA_URL = "http://localhost:3000"
_GITLAB_URL = "http://localhost:8929"
_ADMIN = "livery-admin"


def _compose_file() -> Path:
    """The compose file this package ships."""
    return Path(str(resources.files(__name__))) / "compose.yaml"


def _docker_overlay() -> Path:
    """The compose overlay that mounts the host's docker socket into the runners."""
    return Path(str(resources.files(__name__))) / "compose.docker.yaml"


def _workspace_root() -> Path:
    """The nearest ancestor carrying a ``workshop.toml``, or fail.

    The dev credentials are workspace state, not package state, so
    they live beside the workspace contract.
    """
    origin = Path.cwd().resolve()
    for candidate in (origin, *origin.parents):
        if (candidate / "workshop.toml").is_file():
            return candidate
    fail("no workspace: no workshop.toml above the working directory")


def _dev_env_path() -> Path:
    """The shared env file the cascade reads: the runner's config dir."""
    return footman.config_dir() / ".repo.shared.env"


def _gitlab_image() -> str:
    """The GitLab CE image for this machine's architecture.

    GitLab ships no official arm64 container but does ship official
    arm64 omnibus packages, which the community image wraps. An
    explicit GITLAB_IMAGE in the environment wins.
    """
    override = os.environ.get("GITLAB_IMAGE", "")
    if override:
        return override
    if platform.machine().lower() in ("arm64", "aarch64"):
        return "yrzr/gitlab-ce-arm64v8:latest"
    return "gitlab/gitlab-ce:latest"


def _compose_env() -> dict[str, str]:
    """The environment every compose invocation runs with."""
    merged = dict(os.environ)
    merged.setdefault("GITEA_RUNNER_TOKEN", "")
    merged.setdefault("GITLAB_IMAGE", _gitlab_image())
    return merged


def _compose_cmd(*args: str, with_docker: bool = False) -> list[str]:
    """The docker arguments addressing the packaged compose file.

    The compose file pins its own project name, so where it lives on
    disk never changes which containers it addresses. *with_docker*
    adds the socket overlay, which only an ``up`` of the runners
    needs: a stop, a removal or a restart addresses the containers as
    they were created.
    """
    files = ["-f", str(_compose_file())]
    if with_docker:
        files += ["-f", str(_docker_overlay())]
    return ["compose", *files, *args]


def _docker(*args: str, env: dict[str, str] | None = None) -> tools.Result:
    """Run docker through toolroom with the compose environment.

    The environment is the deliberate compose set (never ambient), and
    the exit code stays the caller's to judge.
    """
    merged = _compose_env()
    if env:
        merged.update(env)
    return tools.docker.opts(env=merged, nofail=True, recorded=False)(*args)


def _compose(
    *args: str, env: dict[str, str] | None = None, with_docker: bool = False
) -> str:
    """Run docker compose, returning stdout; a failure is fatal, verbatim."""
    result = _docker(*_compose_cmd(*args, with_docker=with_docker), env=env)
    if result.code != 0:
        fail(
            f"docker compose {' '.join(args)} exited "
            f"{result.code}:\n{result.stdout}{result.stderr}"
        )
    return result.stdout


def _gitea_cli(*args: str) -> tools.Result:
    """Run the gitea CLI inside the container, as the git user."""
    return _docker(*_compose_cmd("exec", "-T", "-u", "git", "gitea", "gitea", *args))


def _gitea_api(
    path: str,
    token: str,
    *,
    method: str = "GET",
    body: dict[str, str] | None = None,
) -> int:
    """One API call for the seed's probes; returns the HTTP status."""
    request = urllib.request.Request(
        f"{_GITEA_URL}/api/v1{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={
            "Authorization": f"token {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return exc.code
    except urllib.error.URLError:
        return 0


def _read_dev_env() -> dict[str, str]:
    """The minted credentials, empty when nothing is seeded yet."""
    path = _dev_env_path()
    if not path.is_file():
        return {}
    pairs = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            pairs[key] = value
    return pairs


def _update_dev_env(updates: dict[str, str]) -> None:
    """Set *updates* in the shared env file, key by key.

    The file is the person's own cross-repo config, so nothing here
    may rewrite it wholesale: each key replaces its own line or
    appends, and every other line stays exactly as written.
    """
    path = _dev_env_path()
    lines = path.read_text().splitlines() if path.is_file() else []
    for key, value in sorted(updates.items()):
        entry = f"{key}={value}"
        for index, line in enumerate(lines):
            if line.split("=", 1)[0].strip() == key:
                lines[index] = entry
                break
        else:
            lines.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def _wait_for_gitea() -> None:
    """Block until the container answers /api/healthz, or fail after 90s."""
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{_GITEA_URL}/api/healthz", timeout=2):
                return
        except OSError:
            time.sleep(1)
    fail(f"gitea did not answer {_GITEA_URL}/api/healthz within 90s")


@dev.task(name="up")
def dev_up(
    profile: Annotated[str, doc("which forges: gitea, gitlab, or all")] = "all",
    with_docker: Annotated[
        bool, doc("mount the host's docker socket into the runners")
    ] = False,
) -> None:
    """Start and seed the local forge containers, then their runners.

    Idempotent: re-running it is the recovery procedure. Each runner
    starts after its seed because the registration token is minted by
    the seed. GitLab is long-lived and takes minutes on first boot.
    ``--with-docker`` mounts the host's docker socket into both
    runners, for the jobs that build images: cibuildwheel's build
    containers and the container publish seam. Without it the runners
    have no docker, and a seam declared none skips green; the flag's
    state is the runners' own, so an ``up`` that changes it recreates
    them.
    """
    if profile not in ("gitea", "gitlab", "all"):
        fail(f"unknown profile {profile}: use gitea, gitlab, or all")
    if profile in ("gitea", "all"):
        _compose("--profile", "gitea", "up", "-d", "--wait", "gitea")
        _seed_gitea()
        token = _read_dev_env().get("GITEA_RUNNER_TOKEN", "")
        # --build: the runner image is built from runner.Dockerfile, so
        # a changed toolchain line rebuilds it here instead of running
        # on the stale image under the pinned tag.
        _compose(
            "--profile",
            "gitea",
            "--profile",
            "gitea-runner",
            "up",
            "-d",
            "--build",
            "act_runner",
            env={"GITEA_RUNNER_TOKEN": token},
            with_docker=with_docker,
        )
        print(f"  gitea: {_GITEA_URL}  credentials: {_dev_env_path().name}")
    if profile in ("gitlab", "all"):
        _compose("--profile", "gitlab", "up", "-d", "--wait", "gitlab")
        _seed_gitlab()
        # --build, as for act_runner: the two services share the image.
        _compose(
            "--profile",
            "gitlab",
            "--profile",
            "gitlab-runner",
            "up",
            "-d",
            "--build",
            "gitlab-runner",
            with_docker=with_docker,
        )
        _register_gitlab_runner()
        print(f"  gitlab: {_GITLAB_URL}  credentials: {_dev_env_path().name}")
    if with_docker:
        print("  runners: the host's docker socket is mounted")


@dev.task(name="seed")
def dev_seed(
    profile: Annotated[str, doc("which forges: gitea, gitlab, or all")] = "all",
) -> None:
    """Seed the running containers; see the per-forge helpers."""
    if profile in ("gitea", "all"):
        _seed_gitea()
    if profile in ("gitlab", "all"):
        _seed_gitlab()


def _seed_gitea() -> None:
    """Seed the running Gitea: admin user, API token, org, runner token.

    Probes before every act. A working token in the shared env file
    is kept; a missing or dead one is re-minted.
    """
    _wait_for_gitea()
    existing = _read_dev_env().get("GITEA_TOKEN", "")
    if existing and _gitea_api("/user", existing) == 200:
        token = existing
        print("  seed: existing token still works, keeping it")
    else:
        created = _gitea_cli(
            "admin",
            "user",
            "create",
            "--username",
            _ADMIN,
            "--password",
            "livery-dev-password",
            "--email",
            "admin@livery.local",
            "--admin",
            "--must-change-password=false",
        )
        output = created.stdout + created.stderr
        if created.code != 0 and "already exists" not in output:
            fail(f"admin user creation failed:\n{output}")
        minted = _gitea_cli(
            "admin",
            "user",
            "generate-access-token",
            "--username",
            _ADMIN,
            "--token-name",
            f"seed-{int(time.time())}",
            "--scopes",
            "all",
            "--raw",
        )
        if minted.code != 0:
            fail(f"token mint failed:\n{minted.stdout}{minted.stderr}")
        token = minted.stdout.strip().split()[-1]
    if _gitea_api("/orgs/livery", token) != 200:
        status = _gitea_api("/orgs", token, method="POST", body={"username": "livery"})
        if status not in (201, 422):
            fail(f"org creation answered HTTP {status}")
    runner = _gitea_cli("actions", "generate-runner-token")
    if runner.code != 0:
        fail(f"runner token mint failed:\n{runner.stdout}{runner.stderr}")
    _update_dev_env(
        {
            "GITEA_URL": _GITEA_URL,
            "GITEA_TOKEN": token,
            "GITEA_RUNNER_TOKEN": runner.stdout.strip(),
        }
    )
    print(f"  seed: gitea credentials written to {_dev_env_path().name}")


def _gitlab_api(
    path: str,
    token: str,
    *,
    method: str = "GET",
    body: dict[str, object] | None = None,
) -> tuple[int, str]:
    """One GitLab API call for the seed's probes; (status, body text)."""
    request = urllib.request.Request(
        f"{_GITLAB_URL}/api/v4{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"PRIVATE-TOKEN": token, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return (int(response.status), response.read().decode())
    except urllib.error.HTTPError as exc:
        return (exc.code, exc.read().decode(errors="replace"))
    except urllib.error.URLError:
        return (0, "")


def _docker_exec(service: str, *args: str) -> tools.Result:
    """Run a command inside a compose service, every profile enabled."""
    return _docker(
        *_compose_cmd(
            "--profile",
            "gitlab",
            "--profile",
            "gitlab-runner",
            "exec",
            "-T",
            service,
            *args,
        )
    )


def _seed_gitlab() -> None:
    """Seed the running GitLab: a root PAT and the livery group.

    Probes before every act. A working token in the shared env file
    is kept; a missing or dead one is minted through `gitlab-rails
    runner`, which takes about a minute per invocation.
    """
    env = _read_dev_env()
    token = env.get("GITLAB_TOKEN", "")
    if token and _gitlab_api("/user", token)[0] == 200:
        print("  seed: existing gitlab token still works, keeping it")
    else:
        token = f"livery-{secrets.token_hex(24)}"
        script = (
            "u = User.find_by_username('root'); "
            "t = u.personal_access_tokens.build("
            'scopes: [:api], name: "livery-seed-" + Time.now.to_i.to_s, '
            "expires_at: 365.days.from_now); "
            f"t.set_token('{token}'); t.save!"
        )
        minted = _docker_exec("gitlab", "gitlab-rails", "runner", script)
        if minted.code != 0:
            fail(f"gitlab PAT mint failed:\n{minted.stdout}{minted.stderr}")
    # The group is public: the loop's project publishes packages the
    # runner installs without a credential, as the Gitea org's are
    # served, and a private project inside a public group stays
    # private.
    status, body = _gitlab_api("/groups/livery", token)
    if status != 200:
        status, body = _gitlab_api(
            "/groups",
            token,
            method="POST",
            body={"name": "livery", "path": "livery", "visibility": "public"},
        )
        if status not in (201, 409):
            fail(f"gitlab group creation answered HTTP {status}: {body}")
    elif '"visibility":"public"' not in body.replace(" ", ""):
        status, body = _gitlab_api(
            "/groups/livery", token, method="PUT", body={"visibility": "public"}
        )
        if status != 200:
            fail(f"gitlab group visibility answered HTTP {status}: {body}")
    _update_dev_env({"GITLAB_URL": _GITLAB_URL, "GITLAB_TOKEN": token})
    print(f"  seed: gitlab credentials written to {_dev_env_path().name}")


def _register_gitlab_runner() -> None:
    """Register the shell-executor runner, once.

    A config.toml already naming a runner is kept; otherwise a runner
    is created through the API (registration tokens are gone in
    GitLab 16 and later) and registered with the clone URL pointing at
    the compose service name, because the advertised localhost URL
    names the wrong host inside the runner container.
    """
    existing = _docker_exec(
        "gitlab-runner", "sh", "-c", "cat /etc/gitlab-runner/config.toml || true"
    )
    if '"http://gitlab:8929/"' in existing.stdout or "url = " in existing.stdout:
        print("  seed: gitlab runner already registered, keeping it")
        return
    token = _read_dev_env().get("GITLAB_TOKEN", "")
    status, body = _gitlab_api(
        "/user/runners",
        token,
        method="POST",
        body={
            "runner_type": "instance_type",
            "run_untagged": True,
            "description": "livery-dev",
        },
    )
    if status != 201:
        fail(f"gitlab runner creation answered HTTP {status}: {body}")
    runner_token = str(json.loads(body)["token"])
    registered = _docker_exec(
        "gitlab-runner",
        "gitlab-runner",
        "register",
        "--non-interactive",
        "--url",
        "http://gitlab:8929/",
        "--token",
        runner_token,
        "--executor",
        "shell",
        "--clone-url",
        "http://gitlab:8929",
    )
    if registered.code != 0:
        fail(
            "gitlab runner registration failed:\n"
            f"{registered.stdout}{registered.stderr}"
        )
    # Held conformance jobs occupy a slot each until released or
    # cancelled, so one-job concurrency would deadlock the suite; the
    # runner reloads its config file on change.
    raised = _docker_exec(
        "gitlab-runner",
        "sh",
        "-c",
        "sed -i 's/^concurrent = .*/concurrent = 8/' /etc/gitlab-runner/config.toml",
    )
    if raised.code != 0:
        fail(f"runner concurrency update failed:\n{raised.stdout}{raised.stderr}")
    print("  seed: gitlab runner registered (concurrency 8)")


#: The compose profiles each forge's containers and runner carry, and
#: the runner service a restart discards the jobs of.
_FORGE_PROFILES = {
    "gitea": (("gitea", "gitea-runner"), "act_runner"),
    "gitlab": (("gitlab", "gitlab-runner"), "gitlab-runner"),
}


def _forges(profile: str) -> tuple[str, ...]:
    """The forges *profile* names; refuses a word that is not one."""
    if profile == "all":
        return tuple(_FORGE_PROFILES)
    if profile not in _FORGE_PROFILES:
        fail(f"unknown profile {profile}: use gitea, gitlab, or all")
    return (profile,)


@dev.task(name="down")
def dev_down(
    profile: Annotated[str, doc("which forges: gitea, gitlab, or all")] = "all",
    wipe: Annotated[bool, doc("also delete the data volumes")] = False,
) -> None:
    """Stop the local forge containers; `--wipe` deletes their data too.

    ``--profile`` stops one forge and its runner and leaves the other
    up, matching ``up``. A wipe of one forge deletes its volumes
    alone; the shared env file goes only when every forge is wiped,
    since it carries the other forge's credentials too.
    """
    forges = _forges(profile)
    if wipe and profile == "all":
        _dev_env_path().unlink(missing_ok=True)
    if profile == "all":
        args = []
        for names, _runner in _FORGE_PROFILES.values():
            for name in names:
                args += ["--profile", name]
        args.append("down")
        if wipe:
            args.append("--volumes")
        _compose(*args)
        return
    for forge_kind in forges:
        names, runner = _FORGE_PROFILES[forge_kind]
        args = []
        for name in names:
            args += ["--profile", name]
        # One forge's services alone: `down` would take the whole
        # project, so its containers stop and are removed by name.
        services = [forge_kind, runner]
        _compose(*args, "stop", *services)
        _compose(*args, "rm", "-f", *(["-v"] if wipe else []), *services)
        print(f"  {forge_kind}: stopped" + (", volumes deleted" if wipe else ""))


@dev.task(name="restart")
def dev_restart(
    profile: Annotated[
        str, doc("which forges' runners: gitea, gitlab, or all")
    ] = "all",
) -> None:
    """Restart one forge's runner, discarding the jobs it was running.

    The recovery for a runner grinding the runs a dead pass left
    behind: the runner container restarts, its jobs die with it, and
    it registers again with the same configuration. The forge itself
    stays up.
    """
    for forge_kind in _forges(profile):
        names, runner = _FORGE_PROFILES[forge_kind]
        args = []
        for name in names:
            args += ["--profile", name]
        _compose(*args, "restart", runner)
        print(f"  {forge_kind}: runner {runner} restarted")
