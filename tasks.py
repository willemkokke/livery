"""livery's dev loop, temporary until livery.workshop provides it.

Run with ``uv run fm <task>``. ``fm check`` is the whole local gate; CI
runs the same command. Coverage joins the gate with the first real
code: an empty package has nothing honest to measure.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Annotated

from footman import doc, fail, group, parallel, step, task
from toolroom import basedpyright, mypy, pyrefly, pytest, ruff, ruff_format, ty

# The whole repo, as CI lints it. Anything narrower lets a tracked file
# outside src/tests pass the gate and fail the build (footman's lesson).
SRC = (".",)


@task
def lint(fix: Annotated[bool, doc("apply safe fixes in place")] = False):
    """Lint with ruff."""
    ruff.check(*SRC, fix=fix)


@task
def format(check: bool = False):
    """Format with ruff.

    Args:
        check: report instead of rewriting
    """
    ruff_format(*SRC, check=check)


@task
def typecheck():
    """Type-check with all four gating checkers, in parallel.

    basedpyright runs with warnings gating as errors. mypy is strict on
    livery.* and checks every test body as consumer code, once per
    platform (linux from config, darwin and win32 by flag), since mypy
    has no all-platforms mode. ty and pyrefly check every platform at
    once at the scopes pyproject pins. All four gate: a checker livery
    uses is a checker the tree is clean against.
    """

    def based():
        basedpyright(warnings=True)

    # Each mypy run gets its own cache dir: the SQLite cache does not
    # tolerate three concurrent writers on one file.
    def mypy_linux():
        mypy(cache_dir=".mypy_cache/linux")

    def mypy_darwin():
        mypy(platform="darwin", cache_dir=".mypy_cache/darwin")

    def mypy_win32():
        mypy(platform="win32", cache_dir=".mypy_cache/win32")

    def run_ty():
        ty.check()

    def run_pyrefly():
        pyrefly("check")

    parallel(
        step(based, title="basedpyright")(),
        step(mypy_linux)(),
        step(mypy_darwin)(),
        step(mypy_win32)(),
        step(run_ty, title="ty")(),
        step(run_pyrefly, title="pyrefly")(),
    )


@task
def typecomplete():
    """Verify the public API is 100% type-complete (pyright --verifytypes).

    The exit code is the verdict: 0 only when every public symbol has a
    fully known type. A new unannotated export fails the gate here
    before a consumer's checker ever sees it.
    """
    basedpyright(verifytypes="livery.forge", ignoreexternal=True)


@task
def test(*pytest_args: str):
    """Run the test suite.

    Args:
        pytest_args: forwarded to pytest verbatim
    """
    pytest.opts(in_process=False)(*pytest_args)


@task
def check():
    """Run the gate: format check, lint, both type gates, tests, in parallel."""
    with parallel():
        format(check=True)
        lint()
        typecheck()
        typecomplete()
        test()


forge = group("forge", help="livery.forge development")
dev = forge.group("dev", help="Local forge containers (Gitea and GitLab)")

#: Where `forge.dev seed` writes the minted credentials. Gitignored;
#: the live conformance run and the fixture recorder read it.
_DEV_ENV = Path(__file__).parent / ".forge.dev.env"

_GITEA_URL = "http://localhost:3000"
_ADMIN = "livery-admin"


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


def _compose(*args: str, env: dict[str, str] | None = None) -> str:
    """Run docker compose, returning stdout; a failure is fatal, verbatim."""
    merged = dict(os.environ)
    merged.setdefault("LIVERY_GITEA_RUNNER_TOKEN", "")
    merged.setdefault("LIVERY_GITLAB_IMAGE", _gitlab_image())
    if env:
        merged.update(env)
    result = subprocess.run(
        ["docker", "compose", *args],
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
    merged = dict(os.environ)
    merged.setdefault("LIVERY_GITEA_RUNNER_TOKEN", "")
    merged.setdefault("LIVERY_GITLAB_IMAGE", _gitlab_image())
    return subprocess.run(
        ["docker", "compose", "exec", "-T", "-u", "git", "gitea", "gitea", *args],
        capture_output=True,
        text=True,
        env=merged,
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
        headers={"Authorization": f"token {token}", "Content-Type": "application/json"},
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
    if not _DEV_ENV.is_file():
        return {}
    pairs = {}
    for line in _DEV_ENV.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            pairs[key] = value
    return pairs


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
def dev_up():
    """Start and seed the local Gitea container, then its runner.

    Idempotent: re-running it is the recovery procedure. The runner
    starts after the seed because its registration token is minted by
    the seed.
    """
    _compose("--profile", "gitea", "up", "-d", "--wait", "gitea")
    dev_seed()
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
    print(f"  gitea: {_GITEA_URL}  credentials: {_DEV_ENV.name}")


@dev.task(name="seed")
def dev_seed():
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
    _DEV_ENV.write_text(
        "# Minted by `fm forge.dev seed` for the local containers. Gitignored.\n"
        f"GITEA_URL={_GITEA_URL}\n"
        f"GITEA_TOKEN={token}\n"
        f"LIVERY_GITEA_RUNNER_TOKEN={runner.stdout.strip()}\n"
    )
    print(f"  seed: credentials written to {_DEV_ENV.name}")


fixtures = forge.group("fixtures", help="Recorded HTTP fixtures (cassettes)")


@fixtures.task(name="record")
def fixtures_record():
    """Re-record the conformance cassettes from the live containers.

    Runs the backend conformance suites against the seeded containers
    (`fm forge.dev.up` first) and rewrites the cassettes under
    packages/forge/tests/cassettes/. Review the diff like code: a
    changed exchange is a changed contract with the server.
    """
    os.environ["LIVERY_FORGE_RECORD"] = "1"
    pytest.opts(in_process=False)("packages/forge/tests/test_gitea_conformance.py")


@dev.task(name="down")
def dev_down(wipe: Annotated[bool, doc("also delete the data volumes")] = False):
    """Stop the local forge containers; `--wipe` deletes their data too."""
    args = ["--profile", "gitea", "--profile", "gitea-runner", "down"]
    if wipe:
        args.append("--volumes")
        _DEV_ENV.unlink(missing_ok=True)
    _compose(*args)
