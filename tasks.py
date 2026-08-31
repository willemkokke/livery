"""livery's dev loop, temporary until livery.workshop provides it.

Run with ``uv run fm <task>``. ``fm check`` is the whole local gate; CI
runs the same command. Coverage joins the gate with the first real
code: an empty package has nothing honest to measure.
"""

from __future__ import annotations

import dataclasses
import json
import os
import platform
import re
import secrets
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from footman import (
    RunFailed,
    doc,
    fail,
    group,
    parallel,
    plugin,
    run,
    stdin,
    step,
    task,
)
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
    """Verify the public APIs are 100% type-complete (pyright --verifytypes).

    The exit code is the verdict: 0 only when every public symbol has a
    fully known type. A new unannotated export fails the gate here
    before a consumer's checker ever sees it.
    """
    basedpyright(verifytypes="livery.forge", ignoreexternal=True)
    basedpyright(verifytypes="livery.workshop", ignoreexternal=True)


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
_GITLAB_URL = "http://localhost:8929"
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


def _compose_env() -> dict[str, str]:
    """The environment every compose invocation runs with."""
    merged = dict(os.environ)
    merged.setdefault("LIVERY_GITEA_RUNNER_TOKEN", "")
    merged.setdefault("LIVERY_GITLAB_IMAGE", _gitlab_image())
    return merged


def _compose(*args: str, env: dict[str, str] | None = None) -> str:
    """Run docker compose, returning stdout; a failure is fatal, verbatim."""
    merged = _compose_env()
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
    return subprocess.run(
        ["docker", "compose", "exec", "-T", "-u", "git", "gitea", "gitea", *args],
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


def _update_dev_env(updates: dict[str, str]) -> None:
    """Merge *updates* into .forge.dev.env, keeping the other keys."""
    pairs = _read_dev_env()
    pairs.update(updates)
    lines = ["# Minted by `fm forge.dev.seed` for the local containers. Gitignored."]
    lines += [f"{key}={value}" for key, value in sorted(pairs.items())]
    _DEV_ENV.write_text("\n".join(lines) + "\n")


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
):
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
        print(f"  gitea: {_GITEA_URL}  credentials: {_DEV_ENV.name}")
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
        print(f"  gitlab: {_GITLAB_URL}  credentials: {_DEV_ENV.name}")


@dev.task(name="seed")
def dev_seed(
    profile: Annotated[str, doc("which forges: gitea, gitlab, or all")] = "all",
):
    """Seed the running containers; see the per-forge helpers."""
    if profile in ("gitea", "all"):
        _seed_gitea()
    if profile in ("gitlab", "all"):
        _seed_gitlab()


def _seed_gitea():
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
    print(f"  seed: gitea credentials written to {_DEV_ENV.name}")


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
        [
            "docker",
            "compose",
            "--profile",
            "gitlab",
            "--profile",
            "gitlab-runner",
            "exec",
            "-T",
            service,
            *args,
        ],
        capture_output=True,
        text=True,
        env=_compose_env(),
        check=False,
    )


def _seed_gitlab():
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
    print(f"  seed: gitlab credentials written to {_DEV_ENV.name}")


def _register_gitlab_runner():
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
    # One single-node GitLab absorbs about four concurrent writers;
    # beyond that its own internals time out (Gitaly deadlines), so
    # the recording run is capped rather than flaky.
    pytest.opts(in_process=False)(
        "packages/forge/tests/test_gitlab_conformance.py", "-n", "4"
    )
    pytest.opts(in_process=False)(
        "packages/forge/tests/test_github_conformance.py", "-n", "4"
    )


@dev.task(name="down")
def dev_down(wipe: Annotated[bool, doc("also delete the data volumes")] = False):
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
        _DEV_ENV.unlink(missing_ok=True)
    _compose(*args)


@dataclass
class ToolInput:
    """What the agent handed the tool, as the hook event carries it."""

    file_path: str = ""
    command: str = ""  # Bash only: what the agent is about to run


@dataclass
class HookEvent:
    """One Claude Code hook event, parsed from stdin."""

    tool_input: ToolInput = dataclasses.field(default_factory=ToolInput)
    stop_hook_active: bool = False
    session_id: str = ""


agent_hooks = group("hooks", hidden=True, help="Agent lifecycle hooks (stdin-driven)")

_QUOTED = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")
_RUNS_FM = re.compile(r"^\s*(?:uv run(?: --\S+)* )?f(?:m|ootman)\b")
_TRUNCATES = re.compile(r"\|\s*(?:tail|head)\b")
_PUSHES = re.compile(r"^\s*git\s+(?:-C\s+(\S+)\s+)?push\b")
_PUSH_EXEMPT = re.compile(r"\s(?:--delete|-d|--tags)\b|\bpush\s+(?:\S+\s+)?main\b")


def _push_conflicts(repo: str | None) -> bool:
    """Whether HEAD conflicts with origin/main.

    GitHub's test-merge, run locally in milliseconds, before the push
    can create the silent state.

    Fails open on every uncertainty: an offline fetch probes whatever
    origin/main the clone last saw, and a repo with no such ref is not this
    guard's business. Only a conflict, merge-tree exit 1, distinct from its
    other failures, speaks.
    """
    import contextlib

    git = ["git", *(("-C", repo) if repo else ())]
    with contextlib.suppress(RunFailed):
        # Offline / no remote: the last-seen origin/main still answers.
        run([*git, "fetch", "--quiet", "origin", "main"], capture=True)
    try:
        run([*git, "rev-parse", "--verify", "-q", "origin/main^{commit}"], capture=True)
    except RunFailed:
        return False  # no origin/main at all: not this guard's business
    try:
        run([*git, "merge-tree", "--write-tree", "origin/main", "HEAD"], capture=True)
    except RunFailed as exc:
        # With the ref verified, exit 1 is merge-tree's one honest meaning:
        # "merged, with conflicts". (Unverified, 1 also means "no such ref".)
        return exc.result == 1
    return False


@agent_hooks.task(name="pre-bash")
def pre_bash(event: Annotated[HookEvent, stdin]) -> None:
    """Refuse the Bash commands that succeed while silently breaking state.

    The two: a footman gate piped into tail/head, and a git push of a
    branch that conflicts with origin/main. Ported from footman's own
    loop.

    **The pipe guard.** A gate's exit code is its verdict, and a pipe
    replaces it with the filter's, so `fm check | tail -4` reports 0
    whatever happened and prints the step summary while the failing step
    scrolls past above. A red gate has been reported green here exactly
    that way.

    **The push guard.** Agent sessions share this repository, so main moves
    while a branch is being built, and a branch pushed from a stale base
    opens a conflicting pull request for which GitHub cannot build its
    test-merge and therefore spawns no CI at all: no red X, no checks, just
    an absence nothing points at. The guard runs the same test-merge
    locally (git merge-tree --write-tree) before letting the push through.
    Tag pushes, deletions, and pushes of main itself pass untouched.

    Both are deliberately narrow. Command separators split first, so
    `fm check && echo done | tail` stays legal; quoted spans are data, so
    `rg "fm check" | head` passes. Nudges, not a sandbox.
    """
    segments = re.split(r";|&&|\|\|", event.tool_input.command)
    blind = [_QUOTED.sub('""', segment) for segment in segments]
    if any(
        _TRUNCATES.search(segment[match.end() :])
        for segment in blind
        if (match := _RUNS_FM.search(segment)) is not None
    ):
        fail(
            "piping a footman command into tail/head replaces its exit code "
            "with the filter's and hides the failing step - a red gate has "
            "been reported green here that way. Run it unpiped and read the "
            "exit code; to keep the output short, redirect to a file and "
            "slice the file.",
            code=2,
        )
    for segment in blind:
        push = _PUSHES.search(segment)
        if push is None or _PUSH_EXEMPT.search(segment):
            continue
        repo = push.group(1)  # a quoted -C path was blinded; probe the cwd then
        if _push_conflicts(None if repo in (None, '""') else repo):
            fail(
                "git push refused: this branch conflicts with origin/main. A "
                "conflicting PR spawns no CI at all - GitHub cannot build its "
                "test-merge, so there is no red X, no checks, just silence. "
                "Rebase (git fetch origin && git rebase origin/main), re-run "
                "the gate, then push.",
                code=2,
            )


# The base layer, mounted the way every instance mounts it. The tasks
# defined above migrate into the plugin phase by phase (workshop plan);
# until then the plugin contributes the layer walk and nothing else.
plugin("livery.workshop")
