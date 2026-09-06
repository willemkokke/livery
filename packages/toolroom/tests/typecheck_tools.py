# pyright: reportUnnecessaryTypeIgnoreComment=true
"""Type-level tests for the generated `tools.*` stubs. Never executed.

Nothing here is called, and pytest does not collect it (the filename does
not match `test_*.py`) — basedpyright is the test runner. A function body
is fully type-checked whether or not anything calls it, so this file asks
the checker the questions a stub should be able to answer, and CI fails
when the answer changes.

Two kinds of assertion, and the second is the load-bearing one:

* **the positive calls** are the shapes that appear in the docs and in real
  tasks. They must type-check, because a stub whose contract is "suggest,
  never forbid" fails the moment it rejects code that works.

* **the negative calls** are the actual test of coverage. `**flags: Any`
  swallows any keyword the stub has never heard of, so a *passing* call
  proves nothing — misspell a flag and it still passes. But a call that is
  required to *fail* proves the flag is declared and typed: if
  `mkdocs.build(strict=...)` ever stopped being stubbed, `strict="yes"`
  would be swallowed by `**flags`, the error would vanish, and the
  `# type: ignore` above it would become unnecessary — which this file
  turns into an error via the pragma on line 1 (basedpyright honours and
  polices `# type: ignore` too) and via mypy's `warn_unused_ignores`.

So: to assert that a flag exists, pass it something wrong.
"""

from __future__ import annotations

from pathlib import Path

from livery import toolroom as tools
from livery.toolroom import Flag, Tool, Value, off


def _ruff() -> None:
    tools.ruff.check("src", "tests", fix=True, select=["E", "F"])
    tools.ruff.check("src", output_format="github", exit_zero=True)
    tools.ruff.check("src", unsafe_fixes=off, respect_gitignore=off)
    tools.ruff.format("src", check=True, diff=True)
    tools.ruff_format("src", "tests", check=True)
    tools.ruff.opts(nofail=True).check("src")  # run-control rides .opts()
    tools.ruff.check("src", a_flag_ruff_grew_last_week=True)  # never a type error


def _paths() -> None:
    """Positionals and values take a `Path` directly — the bridge `str()`-s
    whatever it is handed — so a wrapper never needs a cast to call through.
    """
    tools.ruff.check(Path("src"), fix=True)
    tools.ruff.check("src", Path("tests"))  # str and Path mix freely
    tools.ruff.check("src", cache_dir=Path(".cache"))
    tools.ruff.check("src", exclude=[Path("build"), "dist"])
    tools.git.add(Path("src"), Path("tests"))
    tools.mkdocs.build(site_dir=Path("site"))


def _reader_facing_aliases(fix: Flag = None, select: Value = None) -> None:
    """The stubs' own vocabulary is importable, so a typed wrapper's
    parameters can be annotated with exactly what the call accepts.
    """
    tools.ruff.check("src", fix=fix, select=select)


def _uv() -> None:
    tools.uv.sync(frozen=True, group=["dev", "docs"])
    tools.uv.run("pytest", "-q")
    tools.uv.lock(check=True)
    tools.uv.build(sdist=True, wheel=True)
    tools.uv.add("httpx", dev=True)
    tools.uv.pip.install("footman", editable=".")
    tools.uv.tool.install("footman")


def _git() -> None:
    tools.git.add("-A")
    tools.git.commit(message="feat: a thing", signoff=True, gpg_sign=off)
    tools.git.push(set_upstream=True, force=off)
    tools.git.tag("v1.0.0", annotate=True, message="release")
    tools.git.status(short=True, branch=True)
    tools.git.switch("main", create=False)
    tools.git.clone("https://example.invalid/x.git", depth=1, quiet=True)
    # An optional-value option works both ways: bare (sign with the default
    # key) and with a value (a specific key). Both must type-check — a stub
    # that typed it bool-only would reject the second, one that typed it
    # value-only would reject the first.
    tools.git.commit(message="signed", gpg_sign=True)
    tools.git.commit(message="signed", gpg_sign="ABCD1234")
    tools.git.status(untracked_files=True)
    tools.git.status(untracked_files="all")
    tools.ruff.check("src", add_noqa=True)
    tools.ruff.check("src", add_noqa="suppressed for release")


def _docker() -> None:
    tools.docker.build(".", tag="app:latest", file="Dockerfile")
    tools.docker.compose.up(detach=True, build=True)
    tools.docker.compose.down(volumes=True, remove_orphans=True)
    tools.docker.compose.logs("web", follow=True, tail="100")
    tools.docker.ps(all=True, quiet=True)


def _docs_tools() -> None:
    tools.mkdocs.build(strict=True, clean=off)
    tools.mkdocs.build(use_directory_urls=off, site_dir="site")
    tools.mkdocs.serve(dirty=True, dev_addr="127.0.0.1:8000")
    tools.mkdocs.gh_deploy(force=True, message="deploy")
    tools.zensical.build(clean=True, strict=True)


def _coverage() -> None:
    tools.coverage.run("-m", "pytest", source=["footman"], parallel_mode=True)
    tools.coverage.report(fail_under=92, show_missing=True)
    tools.coverage.html(directory="htmlcov", skip_covered=True)
    tools.coverage.combine(append=True)
    tools.coverage.xml(quiet=True)


def _node_and_rust() -> None:
    tools.basedpyright("src", outputjson=True)
    tools.bun.add("left-pad", global_=True)
    tools.bun.install(frozen_lockfile=True)
    tools.cspell.lint("**/*.md", quiet=True, gitignore=True)
    tools.prek.run(all_files=True)
    tools.markdownlint("**/*.md", fix=True)


def _claude() -> None:
    """Claude Code's headless surface is the root call, so the prompt is a
    positional and the flags hang off `__call__`; the verbs are what wires a
    session up around it. `continue_` carries footman's trailing underscore —
    `continue` is a Python keyword, and the bridge strips it back off.
    """
    tools.claude("explain this diff", print=True, output_format="json")
    tools.claude("review", model="sonnet", permission_mode="plan", effort="high")
    tools.claude(continue_=True, fork_session=True)
    tools.claude.mcp.add("sentry", "https://mcp.sentry.dev/mcp", transport="http")
    tools.claude.plugin.marketplace.add("owner/repo", scope="project")
    tools.claude.auth.status(json=True)


def _tool_globals_via_flags() -> None:
    """`.flags()` binds a tool's global options before the verb, stays typed,
    and returns the tool so the chain keeps checking.
    """
    tools.docker.flags(host="tcp://x").compose.up(detach=True)
    tools.docker.flags(host="tcp://x", debug=True).ps(all=True)
    tools.docker.flags(context="remote").run("alpine")
    tools.uv.flags(directory="sub", offline=True).sync(frozen=True)
    # git's globals — `git -C x commit` runs in x; `git commit -C x` reuses a
    # commit — so placement changes meaning, and the chain stays _Git-typed.
    tools.git.flags(git_dir="/r/.git", work_tree="/r").commit(message="x")
    tools.git.flags(no_pager=True).log(n=1, oneline=True)
    # A tool with no extracted globals still returns its own class, so the
    # chain after `.flags()` keeps completing.
    tools.coverage.flags().report(fail_under=90)
    # A generic tool still has the untyped `.flags()` from the base class.
    tools.terraform.flags(chdir="infra")("plan")


def _run_control_via_opts() -> None:
    """`.opts()` carries footman run-control — a closed vocabulary, typed on the
    base and returning the tool so the chain keeps checking.
    """
    tools.git.opts(nofail=True).push()
    tools.pytest.opts(capture=False)("-s")
    tools.mkdocs.opts(in_process=False, title="Build the docs").build()


def _undeclared_and_run_control() -> None:
    """A tool footman has never heard of still works, and run-control rides
    `.opts()`.
    """
    tools.terraform("plan", out="tf.plan")
    tools.helm.upgrade("app", "./chart", install=True)
    tools.python("-c", "print(1)")
    tools.pytest.opts(in_process=True)("-q")
    tools.mkdocs.opts(in_process=False, nofail=True).build()
    custom = Tool("helmfile", "--environment", "prod", in_process=False)
    custom("apply", skip_deps=True)


def _a_variable_drives_the_negation(pretty_urls: bool) -> None:
    """The shape from the docs: `True` → the flag, `off` → its negation."""
    tools.mkdocs.build(use_directory_urls=pretty_urls or off)


def _flags_are_declared_and_typed() -> None:
    """Each of these MUST fail to type-check.

    An unnecessary `# type: ignore` is an error here, so if a flag stops
    being declared — and `**flags: Any` starts swallowing it — this file
    fails rather than quietly testing nothing.
    """
    tools.mkdocs.build(strict="yes")  # type: ignore[arg-type]
    tools.mkdocs.build(clean="no")  # type: ignore[arg-type]
    tools.ruff.check(fix="always")  # type: ignore[arg-type]
    tools.ruff.check(output_format="nope")  # type: ignore[arg-type]
    tools.ruff_format(check="yes")  # type: ignore[arg-type]
    tools.uv.sync(frozen="yes")  # type: ignore[arg-type]
    tools.git.commit(signoff="yes")  # type: ignore[arg-type]
    tools.docker.compose.up(detach="yes")  # type: ignore[arg-type]
    tools.coverage.report(show_missing="yes")  # type: ignore[arg-type]
    tools.zensical.build(clean="yes")  # type: ignore[arg-type]
    tools.basedpyright(outputjson="yes")  # type: ignore[arg-type]
    tools.bun.add(dev="yes")  # type: ignore[arg-type]
    tools.cspell.lint(quiet="yes")  # type: ignore[arg-type]
    tools.prek.run(all_files="yes")  # type: ignore[arg-type]
    tools.markdownlint(fix="yes")  # type: ignore[arg-type]
    tools.claude(print="yes")  # type: ignore[arg-type]
    tools.claude(continue_="yes")  # type: ignore[arg-type]
    tools.claude.auth.status(json="yes")  # type: ignore[arg-type]
    tools.docker.flags(debug="yes")  # type: ignore[arg-type]
    tools.git.opts(nofail="yes")  # type: ignore[arg-type]
    tools.git.opts(bogus=True)  # type: ignore[call-arg]


def _positional_shape_is_enforced() -> None:
    """The usage line's positional shape, as type errors.

    `mkdocs build` declares only options, so a positional is wrong;
    `docker run` requires IMAGE positionally, so passing it by keyword — or
    omitting it — is wrong. Each MUST fail, so the shape can't silently
    decay to `*args` without this file noticing.
    """
    tools.mkdocs.build("site")  # type: ignore[call-arg, arg-type]
    tools.uv.sync("extra")  # type: ignore[call-arg, arg-type]
    tools.docker.run(image="alpine")  # type: ignore[call-arg]
    tools.docker.run()  # type: ignore[call-arg]

    # ...and the shapes that DO take positionals still accept them.
    tools.docker.run("alpine", "echo", "hi", detach=True)
    tools.ruff.check("src", "tests")
    tools.git.clone("https://example.invalid/x.git")
