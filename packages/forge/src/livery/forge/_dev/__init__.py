"""The local forge containers: compose and seeds, as a footman plugin.

`fm forge.dev.up` starts and seeds Gitea (with its act_runner) and
GitLab (with its shell-executor runner) from the compose file shipped
in this package; the minted credentials land in the gitignored
``.forge.dev.env`` at the workspace root, which live test runs read.
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
import subprocess
import time
import urllib.error
import urllib.request
from importlib import resources
from pathlib import Path
from typing import Annotated

from footman import doc, fail, group
from toolroom import pytest

forge = group("forge", help="livery.forge development")
dev = forge.group("dev", help="Local forge containers (Gitea and GitLab)")

#: Forge's own test suite, present only in a source checkout: this
#: file is src/livery/forge/_dev/__init__.py, so the package directory
#: holding tests/ is four parents up.
_FORGE_TESTS = Path(__file__).resolve().parents[4] / "tests"

if _FORGE_TESTS.is_dir():
    fixtures = forge.group("fixtures", help="Recorded HTTP fixtures (cassettes)")

    @fixtures.task(name="record")
    def fixtures_record() -> None:
        """Re-record the conformance cassettes from the live containers.

        Runs the backend conformance suites against the seeded
        containers (`fm forge.dev.up` first) and rewrites the
        cassettes under forge's tests/cassettes/. Review the diff like
        code: a changed exchange is a changed contract with the
        server.
        """
        os.environ["LIVERY_FORGE_RECORD"] = "1"
        run_tests = pytest.opts(in_process=False)
        run_tests(str(_FORGE_TESTS / "test_gitea_conformance.py"))
        # One single-node GitLab absorbs about four concurrent
        # writers; beyond that its own internals time out (Gitaly
        # deadlines), so the recording run is capped rather than
        # flaky.
        run_tests(str(_FORGE_TESTS / "test_gitlab_conformance.py"), "-n", "4")
        run_tests(str(_FORGE_TESTS / "test_github_conformance.py"), "-n", "4")


_GITEA_URL = "http://localhost:3000"
_GITLAB_URL = "http://localhost:8929"
_ADMIN = "livery-admin"


def _compose_file() -> Path:
    """The compose file this package ships."""
    return Path(str(resources.files(__name__))) / "compose.yaml"


def _workspace_root() -> Path:
    """The nearest ancestor carrying a ``livery.toml``, or fail.

    The dev credentials are workspace state, not package state, so
    they live beside the workspace contract.
    """
    origin = Path.cwd().resolve()
    for candidate in (origin, *origin.parents):
        if (candidate / "livery.toml").is_file():
            return candidate
    fail("no workspace: no livery.toml above the working directory")


def _dev_env_path() -> Path:
    """Where the seeds write the minted credentials. Gitignored."""
    return _workspace_root() / ".forge.dev.env"


def _gitlab_image() -> str:
    """The GitLab CE image for this machine's architecture.

    GitLab ships no official arm64 container but does ship official
    arm64 omnibus packages, which the community image wraps. An
    explicit LIVERY_GITLAB_IMAGE in the environment wins.
    """
    override = os.environ.get("LIVERY_GITLAB_IMAGE", "")
    if override:
        return override
    if platform.machine().lower() in ("arm64", "aarch64"):
        return "yrzr/gitlab-ce-arm64v8:latest"
    return "gitlab/gitlab-ce:latest"


def _compose_env() -> dict[str, str]:
    """The environment every compose invocation runs with."""
    merged = dict(os.environ)
    merged.setdefault("LIVERY_GITEA_RUNNER_TOKEN", "")
    merged.setdefault("LIVERY_GITLAB_IMAGE", _gitlab_image())
    return merged


def _compose_cmd(*args: str) -> list[str]:
    """A docker compose command line against the packaged file.

    The compose file pins its own project name, so where it lives on
    disk never changes which containers it addresses.
    """
    return ["docker", "compose", "-f", str(_compose_file()), *args]


def _compose(*args: str, env: dict[str, str] | None = None) -> str:
    """Run docker compose, returning stdout; a failure is fatal, verbatim."""
    merged = _compose_env()
    if env:
        merged.update(env)
    result = subprocess.run(
        _compose_cmd(*args),
        capture_output=True,
        text=True,
        env=merged,
        check=False,
    )
    if result.returncode != 0:
        fail(
            f"docker compose {' '.join(args)} exited "
            f"{result.returncode}:\n{result.stdout}{result.stderr}"
        )
    return result.stdout


def _gitea_cli(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the gitea CLI inside the container, as the git user."""
    return subprocess.run(
        _compose_cmd("exec", "-T", "-u", "git", "gitea", "gitea", *args),
        capture_output=True,
        text=True,
        env=_compose_env(),
        check=False,
    )


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
    """Merge *updates* into .forge.dev.env, keeping the other keys."""
    pairs = _read_dev_env()
    pairs.update(updates)
    lines = ["# Minted by `fm forge.dev.seed` for the local containers. Gitignored."]
    lines += [f"{key}={value}" for key, value in sorted(pairs.items())]
    _dev_env_path().write_text("\n".join(lines) + "\n")


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
) -> None:
    """Start and seed the local forge containers, then their runners.

    Idempotent: re-running it is the recovery procedure. Each runner
    starts after its seed because the registration token is minted by
    the seed. GitLab is long-lived and takes minutes on first boot.
    """
    if profile not in ("gitea", "gitlab", "all"):
        fail(f"unknown profile {profile}: use gitea, gitlab, or all")
    if profile in ("gitea", "all"):
        _compose("--profile", "gitea", "up", "-d", "--wait", "gitea")
        _seed_gitea()
        token = _read_dev_env().get("LIVERY_GITEA_RUNNER_TOKEN", "")
        _compose(
            "--profile",
            "gitea",
            "--profile",
            "gitea-runner",
            "up",
            "-d",
            "act_runner",
            env={"LIVERY_GITEA_RUNNER_TOKEN": token},
        )
        print(f"  gitea: {_GITEA_URL}  credentials: {_dev_env_path().name}")
    if profile in ("gitlab", "all"):
        _compose("--profile", "gitlab", "up", "-d", "--wait", "gitlab")
        _seed_gitlab()
        _compose(
            "--profile",
            "gitlab",
            "--profile",
            "gitlab-runner",
            "up",
            "-d",
            "gitlab-runner",
        )
        _register_gitlab_runner()
        print(f"  gitlab: {_GITLAB_URL}  credentials: {_dev_env_path().name}")


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

    Probes before every act. A working token in .forge.dev.env is kept;
    a missing or dead one is re-minted.
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
        if created.returncode != 0 and "already exists" not in output:
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
        if minted.returncode != 0:
            fail(f"token mint failed:\n{minted.stdout}{minted.stderr}")
        token = minted.stdout.strip().split()[-1]
    if _gitea_api("/orgs/livery", token) != 200:
        status = _gitea_api("/orgs", token, method="POST", body={"username": "livery"})
        if status not in (201, 422):
            fail(f"org creation answered HTTP {status}")
    runner = _gitea_cli("actions", "generate-runner-token")
    if runner.returncode != 0:
        fail(f"runner token mint failed:\n{runner.stdout}{runner.stderr}")
    _update_dev_env(
        {
            "GITEA_URL": _GITEA_URL,
            "GITEA_TOKEN": token,
            "LIVERY_GITEA_RUNNER_TOKEN": runner.stdout.strip(),
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


def _docker_exec(service: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a command inside a compose service, every profile enabled."""
    return subprocess.run(
        _compose_cmd(
            "--profile",
            "gitlab",
            "--profile",
            "gitlab-runner",
            "exec",
            "-T",
            service,
            *args,
        ),
        capture_output=True,
        text=True,
        env=_compose_env(),
        check=False,
    )


def _seed_gitlab() -> None:
    """Seed the running GitLab: a root PAT and the livery group.

    Probes before every act. A working token in .forge.dev.env is
    kept; a missing or dead one is minted through `gitlab-rails
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
        if minted.returncode != 0:
            fail(f"gitlab PAT mint failed:\n{minted.stdout}{minted.stderr}")
    if _gitlab_api("/groups/livery", token)[0] != 200:
        status, body = _gitlab_api(
            "/groups",
            token,
            method="POST",
            body={"name": "livery", "path": "livery", "visibility": "private"},
        )
        if status not in (201, 409):
            fail(f"gitlab group creation answered HTTP {status}: {body}")
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
    if registered.returncode != 0:
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
    if raised.returncode != 0:
        fail(f"runner concurrency update failed:\n{raised.stdout}{raised.stderr}")
    print("  seed: gitlab runner registered (concurrency 8)")


@dev.task(name="down")
def dev_down(
    wipe: Annotated[bool, doc("also delete the data volumes")] = False,
) -> None:
    """Stop the local forge containers; `--wipe` deletes their data too."""
    args = [
        "--profile",
        "gitea",
        "--profile",
        "gitea-runner",
        "--profile",
        "gitlab",
        "--profile",
        "gitlab-runner",
        "down",
    ]
    if wipe:
        args.append("--volumes")
        _dev_env_path().unlink(missing_ok=True)
    _compose(*args)
