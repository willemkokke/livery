"""A realistic task surface exercising every signature shape the grammar
supports: flags, str/int/float options, Literal choices, repeatable list
options, required positionals (incl. choice and Path), variadic ``*args``, and
``--`` passthrough. Used as a fixture across the test-suite.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from livery.footman import group, task


@task
def check():
    """Run every check (format, lint, typecheck, test)."""


@task
def format(fix: bool = False, paths: list[Path] | None = None):
    """Format the codebase (repeat --paths for more than one)."""


@task
def lint(
    fix: bool = False,
    mode: Literal["strict", "loose"] = "loose",
    paths: list[Path] | None = None,
):
    """Run ruff over the project."""


@task
def typecheck(strict: bool = False):
    """Run basedpyright."""


@task
def test(
    marker: str = "", path: list[Path] | None = None, coverage: bool = False, *pytest
):
    """Run the pytest suite (extra pytest args after --)."""


@task
def build(sdist: bool = False, wheel: bool = False):
    """Build distributions."""


@task
def clean(deep: bool = False):
    """Remove build artifacts and caches."""


@task
def coverage(fail_under: int = 80):
    """Report test coverage."""


@task
def deploy(env: Literal["dev", "staging", "prod"], version: str = ""):
    """Deploy to an environment."""


@task
def version(part: Literal["major", "minor", "patch"]):
    """Bump the version (required choice positional)."""


@task
def bench(iterations: int = 100, timeout: float = 30.0):
    """Run the benchmark suite."""


@task
def render(template: Path, output: Path):
    """Render a template to a file (two required positionals)."""


@task
def run(*cmd: str):
    """Run an arbitrary command in the project environment (variadic)."""


docs = group("docs", help="Documentation")


@docs.task
def serve(port: int = 8000, live: bool = True):
    """Serve the docs locally."""


@docs.task(name="build")
def docs_build(strict: bool = False):
    """Build the docs site."""


db = group("db", help="Database management")


@db.task
def migrate(revision: str = "head"):
    """Apply migrations up to a revision."""


@db.task
def seed(count: int = 100):
    """Seed the database with sample data."""


docker = group("docker", help="Container images")


@docker.task(name="build")
def docker_build(
    tag: list[str] | None = None,
    platform: Literal["linux/amd64", "linux/arm64"] = "linux/amd64",
):
    """Build the image (repeat --tag for multiple tags)."""


@docker.task
def push(tag: list[str] | None = None):
    """Push image tags."""


deps = group("deps", help="Dependency management")


@deps.task
def add(*packages: str):
    """Add packages (variadic)."""


@deps.task
def update():
    """Update the lockfile."""


ws = group("workspace", help="Mount and manage the workspace")


@ws.task
def mount(share: Literal["main", "scratch", "archive"] = "main", force: bool = False):
    """Mount a share into the workspace."""


@ws.task
def reset(hard: bool = False):
    """Reset workspace state."""


dns = group("dns", help="DNS management")


@dns.task(name="list")
def dns_list(json: bool = False):
    """List DNS records."""


rel = group("release", help="Release management")


@rel.task
def prepare(version: str = ""):
    """Prepare a release PR."""


@rel.task
def publish(dry_run: bool = False):
    """Tag and publish the release."""
