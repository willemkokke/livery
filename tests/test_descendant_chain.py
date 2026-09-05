"""The proof chain (contract 22): create, customise, inherit.

The dummy descendant, end to end on the local Gitea: a layer home is
born and self-hosts, populates its overlay and content, releases its
composed artifact; the branded App begets a child from that
artifact; a core improvement dev-ships as a base wheel bump and
reaches the child through the home's recompose, while the
overlay-replaced file stays the brand's, the named forfeit. The
chain creates and destroys its own repositories, and a second run
resumes quietly.

Heavyweight and network-bound, so it arms with
WORKSHOP_CONFORMANCE_DRIVE=1: the merge path waits on nothing
outside the repository, and the committed unit suites are the fast
subset. Local wheels stand in for the index, the conformance
drive's own stand-in.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "packages/workshop/src/livery/workshop/templates"

GITEA = "http://localhost:3000"
OWNER = "livery-admin"
BRAND = "brandx"


def _token() -> str:
    shared = Path.home() / ".config" / "footman" / ".repo.shared.env"
    if not shared.is_file():
        pytest.skip("no rig credentials: run `fm forge.dev.up` first")
    for line in shared.read_text().splitlines():
        if line.startswith("GITEA_TOKEN="):
            return line.partition("=")[2]
    pytest.skip("no GITEA_TOKEN in the shared env: run `fm forge.dev.up`")


def _api(
    token: str, method: str, path: str, body: dict[str, object] | None = None
) -> object:
    request = urllib.request.Request(
        f"{GITEA}/api/v1{path}",
        method=method,
        headers={
            "Authorization": f"token {token}",
            "Content-Type": "application/json",
        },
        data=json.dumps(body).encode() if body is not None else None,
    )
    with urllib.request.urlopen(request) as response:
        raw = response.read()
    return json.loads(raw) if raw else None


def _destroy(token: str, name: str) -> None:
    with contextlib.suppress(OSError):
        _api(token, "DELETE", f"/repos/{OWNER}/{name}")


def _run(
    cmd: list[str], cwd: Path, env: dict[str, str], *, check: bool = True
) -> subprocess.CompletedProcess[str]:
    done = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if check and done.returncode != 0:
        raise AssertionError(
            f"{' '.join(cmd)} exited {done.returncode}:\n{done.stdout}\n{done.stderr}"
        )
    return done


def _hermetic(base: dict[str, str], venv: Path) -> dict[str, str]:
    """*base* with PATH scoped to one venv's bin and the system.

    The chain runner's own venv must never leak into a child
    workspace's gate: its tools would carry the runner's code, not
    the wheels under test. *venv* is the environment root (a
    workspace's ``.venv``, or the brand tool's own).
    """
    system = "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin"
    home_bin = str(Path.home() / ".local" / "bin")
    return {
        **base,
        "PATH": f"{venv / 'bin'}:{home_bin}:{system}",
    }


def _build_wheels(source_root: Path, wheelhouse: Path, env: dict[str, str]) -> None:
    wheelhouse.mkdir(exist_ok=True)
    for member in ("packages/workshop", "packages/forge"):
        _run(
            ["uv", "build", "--wheel", "-o", str(wheelhouse), member],
            source_root,
            env,
        )


@pytest.mark.skipif(
    not os.environ.get("WORKSHOP_CONFORMANCE_DRIVE"),
    reason="set WORKSHOP_CONFORMANCE_DRIVE=1 to run the chain: it"
    " builds venvs and drives the local Gitea",
)
def test_the_chain_creates_customises_and_inherits(tmp_path: Path) -> None:
    token = _token()
    base_env = {
        **os.environ,
        "FORGE_TOKEN": token,
        "FORGE_ADMIN_TOKEN": token,
        "VIRTUAL_ENV": "",
    }
    fm = str(ROOT / ".venv" / "bin" / "fm")
    for name in ("dummy", "child", f"{BRAND}-templates"):
        _destroy(token, name)
    try:
        _chain(tmp_path, token, fm, base_env, resumed=False)
        # The chain re-run: the second pass resumes and no-ops.
        _chain(tmp_path, token, fm, base_env, resumed=True)
    finally:
        if not os.environ.get("WORKSHOP_CHAIN_KEEP"):
            for name in ("dummy", "child", f"{BRAND}-templates"):
                _destroy(token, name)


def _chain(
    tmp_path: Path, token: str, fm: str, base_env: dict[str, str], *, resumed: bool
) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    if not resumed:
        _build_wheels(ROOT, wheelhouse, base_env)
    env = {**base_env, "UV_FIND_LINKS": str(wheelhouse)}

    # Above any project stock fm mounts only footman's own builtins
    # (the footman#536 gap), so the chain bridges through a scratch
    # config dir: the documented user-rung tasks file, scoped to
    # these invocations, never the machine's real config.
    bridge = tmp_path / "bridge-config"
    (bridge / "footman").mkdir(parents=True, exist_ok=True)
    (bridge / "footman" / "tasks.py").write_text(
        'from footman import plugin\n\nplugin("livery.workshop")\n'
    )
    bridged = {**env, "XDG_CONFIG_HOME": str(bridge)}

    # -- 1. the home is born, self-hosting its brand ----------------
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    birth = _run(
        [
            fm,
            "new.project",
            "dummy",
            f"--layer={BRAND}",
            "--forge=gitea",
            f"--owner={OWNER}",
            f"--url={GITEA}",
            f"--templates={TEMPLATES}",
            "--namespace=dummy",
        ],
        work,
        bridged,
    )
    home = work / "dummy"
    assert "done: merge the setup PR" in birth.stdout
    if resumed:
        assert "already scaffolded" in birth.stdout
        assert "workshop.toml: already seeded" in birth.stdout
    contract = (home / "workshop.toml").read_text()
    assert 'layers = ["livery.workshop", "dummy.brandx"]' in contract
    # The docs seeds arrived at birth: the workspace's and the
    # member package's.
    assert (home / "docs" / "index.md").is_file()
    assert (home / "packages" / BRAND / "docs" / "index.md").is_file()

    # -- 2. populate: overlay replace, fragment line, a skill --------
    member = home / "packages" / BRAND
    overlay = member / "src" / "dummy" / BRAND / "templates"
    if not resumed:
        (overlay / "project").mkdir(parents=True, exist_ok=True)
        shutil.copy2(
            TEMPLATES / "project" / ".gitignore.jinja",
            overlay / "project" / ".gitignore.jinja",
        )
        with (overlay / "project" / ".gitignore.jinja").open("a") as handle:
            handle.write("brandx-build/\n")
        (overlay / "overlay.toml").write_text(
            '[[replace]]\npath = "project/.gitignore.jinja"\n'
            'reason = "the brand ignores its own build tree"\n'
        )
        fragment = member / "src" / "dummy" / BRAND / "content" / "fragments"
        with (fragment / f"CLAUDE.{BRAND}.md").open("a") as handle:
            handle.write("\nAlways speak plainly.\n")
        skill = member / "src" / "dummy" / BRAND / "content" / "skills" / "hello"
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text(
            "<!-- Shipped as dummy.brandx layer content, delivered to the workspace\n"
            "     by the sync verb. Edit this copy in the layer and release it;\n"
            "     an edited delivered copy is a local override, kept and named.\n"
            "-->\n# hello\n\nSay hello.\n"
        )
        # The generator seam's in-repo consumer: the member declares
        # a docs generator (a task its layer plugin already ships the
        # group for) that writes a page and rewrites its nav block.
        with (member / "src" / "dummy" / BRAND / "_tasks.py").open("a") as handle:
            handle.write(
                f'\n\n@{BRAND}.task(name="docsgen")\n'
                "def docsgen() -> None:\n"
                '    """Generate the tools page and its nav block."""\n'
                "    from pathlib import Path\n"
                "\n"
                "    from livery.workshop import rewrite_nav_block\n"
                "\n"
                f'    docs = Path("packages/{BRAND}/docs")\n'
                '    out = docs / "_generated"\n'
                "    out.mkdir(parents=True, exist_ok=True)\n"
                '    (out / "tools.md").write_text("# Tools\\n\\nGenerated.\\n")\n'
                "    rewrite_nav_block(\n"
                '        docs / "nav.toml",\n'
                '        "tools",\n'
                '        [\'{ "Tools" = "_generated/tools.md" },\'],\n'
                "    )\n"
            )
        with (member / "workshop.toml").open("a") as handle:
            handle.write(f'\n[docs]\ngenerators = ["{BRAND}.docsgen"]\n')
        nav_file = member / "docs" / "nav.toml"
        nav_text = nav_file.read_text().replace(
            '    { "Index" = "index.md" },\n]',
            '    { "Index" = "index.md" },\n'
            "    # nav:begin tools\n    # nav:end tools\n]",
        )
        assert "nav:begin tools" in nav_text
        nav_file.write_text(nav_text)
        # The generator fills its nav block first, the render then
        # carries the filled block into zensical.toml, and the commit
        # captures both, so later builds are no-ops on a clean tree.
        _run([fm, f"{BRAND}.docsgen"], home, _hermetic(env, home / ".venv"))
        _run([fm, "template.apply"], home, _hermetic(env, home / ".venv"))
        _run(["git", "add", "-A"], home, env)
        _run(["git", "commit", "-qm", "feat: populate the brand"], home, env)

    # The seam end to end: the declared generator ran, its page is in
    # the built site, and a (re)build leaves the tree clean.
    home_build = _run([fm, "docs.build"], home, _hermetic(env, home / ".venv"))
    assert f"{BRAND}.docsgen" in home_build.stdout
    generated_page = (
        home / "site" / "_generated" / "packages" / BRAND / "_generated" / "tools"
    )
    assert (generated_page / "index.html").is_file()
    clean = _run(["git", "status", "--porcelain"], home, env)
    assert clean.stdout.strip() == ""

    # -- 3. the home releases: wheel and composed artifact -----------
    _api(
        token,
        "POST",
        "/user/repos",
        # Public, livery's own artifact stance: instances clone it
        # with no credential; a private one is git credential
        # machinery's business, never a contract byte's.
        {"name": f"{BRAND}-templates", "private": False, "auto_init": False},
    ) if not resumed else None
    _run(
        ["uv", "build", "--wheel", "-o", str(wheelhouse), f"packages/{BRAND}"],
        home,
        env,
    )
    release = _run(
        [
            fm,
            "release.templates",
            f"--remote={GITEA}/{OWNER}/{BRAND}-templates.git",
        ],
        home,
        _hermetic(env, home / ".venv"),
    )
    assert ("published v0.0.0" in release.stdout) or (
        "already published with this content" in release.stdout
    )
    if resumed:
        assert "already published with this content" in release.stdout

    # -- 4. the branded App begets the child --------------------------
    tool = tmp_path / "brand-tool"
    if not resumed:
        _run(["uv", "venv", str(tool)], tmp_path, env)
        _run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(tool / "bin" / "python"),
                "--find-links",
                str(wheelhouse),
                f"dummy-{BRAND}",
                "uv",
            ],
            tmp_path,
            env,
        )
    brand_cli = tool / "bin" / BRAND
    assert brand_cli.is_file()
    child_work = tmp_path / "child-work"
    child_work.mkdir(exist_ok=True)
    child_birth = _run(
        [
            str(brand_cli),
            "new.project",
            "child",
            "--forge=gitea",
            f"--owner={OWNER}",
            f"--url={GITEA}",
            f"--templates=git+{GITEA}/{OWNER}/{BRAND}-templates.git",
            "--namespace=kid",
        ],
        child_work,
        _hermetic(env, tool),
    )
    child = child_work / "child"
    assert "done: merge the setup PR" in child_birth.stdout
    # The stack is the App's own (contract 19), and the workflows are
    # branded: the emitted gate calls the brand by name.
    child_contract = (child / "workshop.toml").read_text()
    assert 'layers = ["livery.workshop", "dummy.brandx"]' in child_contract
    gate = (child / ".gitea" / "workflows" / "ci.yml").read_text()
    assert f"{BRAND} check" in gate
    # The brand's overlay reached the child's managed render.
    assert "brandx-build/" in (child / ".gitignore").read_text()
    # The brand's content arrived through sync.
    assert (
        "Always speak plainly."
        in (child / ".workshop" / f"CLAUDE.{BRAND}.md").read_text()
    )
    assert (child / ".claude" / "skills" / "hello" / "SKILL.md").exists()

    # -- 5. phase 12's rung, live: a secret overrides a declared key --
    _run(
        [
            str(brand_cli),
            "env.set",
            "PYTHON_PUBLISH_INDEX",
            "--value=https://committed.example/pypi",
            "--scope=repo",
        ],
        child,
        _hermetic(env, tool),
    )
    _run(
        [
            str(brand_cli),
            "env.set",
            "PYTHON_PUBLISH_INDEX",
            "--value=https://secret.example/pypi",
            "--scope=ci",
        ],
        child,
        _hermetic({**env, "FORGE_ADMIN_TOKEN": token}, tool),
        check=not resumed,
    )
    _run([str(brand_cli), "template.apply"], child, _hermetic(env, tool), check=False)
    rendered_gate = (child / ".gitea" / "workflows" / "ci.yml").read_text()
    assert "Environment rung" in rendered_gate
    assert "RUNG_PYTHON_PUBLISH_INDEX" in rendered_gate

    # -- 5b. the docs toolchain, through the gradient -----------------
    # The layer at its installed version renders the child's site
    # config and builds the site; no template re-render happens, and
    # the site speaks the child's name, never the base's.
    child_config = (child / "zensical.toml").read_text()
    assert 'site_name = "child"' in child_config
    docs_build = _run([str(brand_cli), "docs.build"], child, _hermetic(env, tool))
    assert "site built" in docs_build.stdout
    assert (child / "site" / "index.html").is_file()
    assert "child" in (child / "site" / "index.html").read_text()

    # -- 5c. both kinds arrive through the gradient -------------------
    # The child creates a C/C++ library and an extension depending
    # on it with the brand's own verbs: no template re-render, the
    # honest skips in the gate, and the tool profile grown only
    # here. The dependency is declared (the edge and the agreeing
    # conan requirement); compile-time consumption of the conan
    # package from the extension's build is its own future cut.
    # ty refuses an empty-but-set VIRTUAL_ENV, so the child's gate
    # runs with the variable naming the child's own venv.
    child_env = {
        **_hermetic(env, child / ".venv"),
        "VIRTUAL_ENV": str(child / ".venv"),
    }
    if not resumed:
        for member_name, kind in (
            ("geometry", "package-cpp-conan"),
            ("ext", "package-python-nanobind"),
        ):
            _run(
                [str(brand_cli), "new.package", member_name, f"--kind={kind}"],
                child,
                _hermetic(env, tool),
            )
        ext_contract = child / "packages" / "ext" / "workshop.toml"
        with ext_contract.open("a") as handle:
            handle.write(
                "\n[[depends]]\n"
                'path = "packages/geometry"\n'
                'kind = "build"\n'
                'floor = "0.0.1"\n'
            )
        (child / "packages" / "ext" / "conanfile.py").write_text(
            '"""The extension\'s conan requirements."""\n\n'
            'requires = "kid-geometry/[>=0.0.1]"\n'
        )
        _run(["git", "add", "-A"], child, env)
        _run(
            ["git", "commit", "-qm", "feat: the native library and the extension"],
            child,
            env,
        )
    else:
        # The wiring is idempotent by refusal: the resumed pass
        # names the existing directory and changes nothing.
        again = _run(
            [str(brand_cli), "new.package", "geometry", "--kind=package-cpp-conan"],
            child,
            _hermetic(env, tool),
            check=False,
        )
        assert "already exists" in (again.stdout + again.stderr)
    child_fm = child / ".venv" / "bin" / "fm"
    child_gate = _run([str(child_fm), "check"], child, child_env)
    assert (
        "packages/geometry (cpp-conan): configure, build, ctest run"
        in child_gate.stdout
    )
    assert "typecomplete: packages/geometry skips (cpp-conan kind)" in child_gate.stdout
    # The profile grows by discovery, and only here: the home stays
    # pure python.
    probe = (
        "from pathlib import Path\n"
        "from livery.workshop._env_tasks import tool_profile\n"
        "print(','.join(tool_profile(Path.cwd())))\n"
    )
    child_profile = _run(
        [str(child / ".venv" / "bin" / "python"), "-c", probe], child, child_env
    ).stdout
    assert "cmake" in child_profile and "conan" in child_profile
    home_profile = _run(
        [str(home / ".venv" / "bin" / "python"), "-c", probe],
        home,
        _hermetic(env, home / ".venv"),
    ).stdout
    assert "cmake" not in home_profile and "conan" not in home_profile

    # -- 6. the inheritance proof -------------------------------------
    if resumed:
        return
    improved = tmp_path / "base-improved"
    if improved.exists():
        shutil.rmtree(improved)
    shutil.copytree(ROOT / "packages" / "workshop", improved)
    templates2 = improved / "src" / "livery" / "workshop" / "templates"
    with (templates2 / "project" / "tasks.py.jinja").open("a") as handle:
        handle.write("\n# The core teaches: run the gate before every commit.\n")
    with (templates2 / "project" / ".gitignore.jinja").open("a") as handle:
        handle.write("core-scratch/\n")
    with (
        improved
        / "src"
        / "livery"
        / "workshop"
        / "content"
        / "fragments"
        / "CLAUDE.workshop.md"
    ).open("a") as handle:
        handle.write("\nThe gate's verdict is its exit code.\n")
    pyproject = improved / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text().replace('version = "0.1.0"', 'version = "0.1.1"')
    )
    _run(["uv", "build", "--wheel", "-o", str(wheelhouse)], improved, env)

    # The home takes the base bump the real way: the lock moves to the
    # new wheel, sync refreshes the environment (the bumped member's
    # own metadata included), then recompose and re-release.
    brand_pyproject = member / "pyproject.toml"
    brand_pyproject.write_text(
        brand_pyproject.read_text().replace('version = "0.0.0"', 'version = "0.0.1"')
    )
    _run(
        ["uv", "lock", "--upgrade-package", "livery-workshop"],
        home,
        env,
    )
    _run(["uv", "sync"], home, env)
    home_fm = home / ".venv" / "bin" / "fm"
    _run([str(home_fm), "sync"], home, _hermetic(env, home / ".venv"), check=False)
    _run([str(home_fm), "template.apply"], home, _hermetic(env, home / ".venv"))
    _run(
        ["uv", "build", "--wheel", "-o", str(wheelhouse), f"packages/{BRAND}"],
        home,
        env,
    )
    rerelease = _run(
        [
            str(home_fm),
            "release.templates",
            f"--remote={GITEA}/{OWNER}/{BRAND}-templates.git",
        ],
        home,
        _hermetic({**env, "FORGE_TOKEN": token}, home / ".venv"),
    )
    assert "published v0.0.1" in rerelease.stdout

    # The child updates: new brand and base wheels arrive, then the
    # rendered files move to the recomposed artifact's tag.
    _run(
        [
            "uv",
            "lock",
            "--upgrade-package",
            f"dummy-{BRAND}",
            "--upgrade-package",
            "livery-workshop",
        ],
        child,
        env,
    )
    _run(["uv", "sync"], child, env)
    child_fm = child / ".venv" / "bin" / "fm"
    _run([str(child_fm), "sync"], child, _hermetic(env, child / ".venv"), check=False)
    # The fragment improvement arrived through the wheel and sync.
    assert (
        "The gate's verdict is its exit code."
        in (child / ".workshop" / "CLAUDE.workshop.md").read_text()
    )
    # The engine refuses a dirty tree rather than guessing; the
    # child's customisation commits before the wave, as a person's
    # would.
    _run(["git", "add", "-A"], child, env)
    _run(
        ["git", "commit", "-qm", "chore: settle before the update"],
        child,
        env,
        check=False,
    )
    child_env = _hermetic({**env, "GIT_TERMINAL_PROMPT": "0"}, child / ".venv")
    updated = _run(
        [str(child_fm), "workflow.update.templates"],
        child,
        child_env,
        check=False,
    )
    branch = _run(["git", "branch", "--list", "workflow/update/templates"], child, env)
    assert "workflow/update/templates" in branch.stdout, updated.stdout + updated.stderr
    files = _run(
        ["git", "show", "workflow/update/templates:tasks.py"], child, env
    ).stdout
    # The core improvement reached the grandchild through the gradient.
    assert "run the gate before every commit" in files, updated.stdout + updated.stderr
    ignored = _run(
        ["git", "show", "workflow/update/templates:.gitignore"], child, env
    ).stdout
    # The overlay-replaced file did not move: the named forfeit is the
    # brand's declared replace, and the base's new line stays out.
    assert "brandx-build/" in ignored
    assert "core-scratch/" not in ignored
    # No layer-owned line changed: the brand's overlay content stands.
    assert (
        "Always speak plainly."
        in (child / ".workshop" / f"CLAUDE.{BRAND}.md").read_text()
    )
