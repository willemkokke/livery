"""The proof chain (contract 22): create, customise, inherit.

The dummy descendant, end to end on the local Gitea: an extension home is
born and self-hosts, populates its content (a declared replacement of a
base file, a fragment line, a skill) and builds its wheel; the branded
App begets a child that installs that wheel; a core improvement
dev-ships as a base wheel bump and reaches the child through its
sync, while the replaced file stays the brand's, the named forfeit. The
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
BASE_ROOT = ROOT / "packages/workshop/src/livery/workshop/content/root"

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


def _link_the_library(extension: Path, library: Path) -> None:
    """Make the rendered extension call the library beside it.

    The template's own module already calls one Conan Center symbol
    (fmt). This adds the sibling: a find_package, the link, the
    header, and a binding that answers from the library's own
    source, so one compiled module carries both sides of the graph.
    The names are read from what the render produced, never spelled
    twice.
    """
    package = _conan_name(library)
    header = next(library.glob("src/*.hpp"))
    namespace = header.stem
    recipe = extension / "conanfile.py"
    text = recipe.read_text()
    assert 'requires = ("fmt/[>=11.0]",)' in text, text
    recipe.write_text(
        text.replace(
            'requires = ("fmt/[>=11.0]",)',
            f'requires = ("fmt/[>=11.0]", "{package}/[>=0.0.0]")',
        )
    )
    cmake = extension / "CMakeLists.txt"
    cmake.write_text(
        cmake.read_text()
        .replace(
            "find_package(fmt REQUIRED)",
            f"find_package(fmt REQUIRED)\nfind_package({package} REQUIRED)",
        )
        .replace(
            "target_link_libraries(_native PRIVATE fmt::fmt)",
            f"target_link_libraries(_native PRIVATE fmt::fmt {package}::{package})",
        )
    )
    native = next(extension.glob("src/**/_native.cpp"))
    module = 'NB_MODULE(_native, m) { m.def("native_hello", &native_hello); }'
    source = native.read_text()
    assert module in source, "the seed's module line moved"
    # Written as clang-format writes it, so the child's gate finds the
    # source formatted.
    native.write_text(
        source.replace(
            "#include <fmt/format.h>",
            f"#include <fmt/format.h>\n#include <{header.name}>",
        ).replace(
            module,
            "NB_MODULE(_native, m) {\n"
            '    m.def("library_version",'
            f" []() {{ return std::string({namespace}::version()); }});\n"
            '    m.def("native_hello", &native_hello);\n'
            "}",
        )
    )
    stub = next(extension.glob("src/**/_native.pyi"))
    stub.write_text(
        stub.read_text().rstrip("\n") + "\n\ndef library_version() -> str: ...\n"
    )
    # The proof is the member's own test, which the child's gate runs:
    # one call into the Conan Center package the template already
    # links, one into the sibling built from its source.
    suite = next(extension.glob("tests/test_*_package.py"))
    suite.write_text(
        suite.read_text()
        + "\n\ndef test_both_sides_of_the_graph_are_linked() -> None:\n"
        "    from kid.ext._native import library_version, native_hello\n\n"
        '    assert native_hello() == "native hello from kid-ext"\n'
        '    assert library_version() == "0.0.0"\n'
    )


def _conan_name(library: Path) -> str:
    """The conan package name the rendered recipe declares."""
    import re

    text = (library / "conanfile.py").read_text()
    match = re.search(r'^\s*name = "([^"]+)"$', text, re.M)
    assert match, f"{library}/conanfile.py declares no name"
    return match.group(1)


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


def _name_the_tool_index(workspace: Path, index: Path) -> None:
    """Point *workspace* at *index* for its tools, once.

    A workspace locks and materialises the tools its kinds need
    against an index it names. A newborn names none (livery#765), so
    the chain names one for it, the way a consumer names the
    published index: here the source's own records directory, so the
    child locks against what this branch carries.
    """
    contract = workspace / "workshop.toml"
    text = contract.read_text()
    if "[tools]" in text:
        return
    contract.write_text(f'{text}\n[tools]\nindex = "{index}"\n')


def _entered(fm: Path, workspace: Path, env: dict[str, str]) -> dict[str, str]:
    """*env* with *workspace*'s own tool environment applied.

    `fm env.emit posix` prints what a shell would evaluate before
    running a tool by hand. A child process that runs uv or conan
    directly needs those variables, since only the verbs enter on
    their own.
    """
    emitted = _run([str(fm), "env.emit", "posix"], workspace, env).stdout
    applied = dict(env)
    for line in emitted.splitlines():
        if not line.startswith("export "):
            continue
        key, _, value = line[len("export ") :].partition("=")
        value = value.replace('"$PATH"', applied.get("PATH", "")).replace("'", "")
        applied[key] = value
    return applied


def _build_wheels(source_root: Path, wheelhouse: Path, env: dict[str, str]) -> None:
    """Build every member of this workspace into *wheelhouse*.

    The whole stack, discovered rather than listed: the home resolves
    the workshop's dependency graph from these wheels, and one member
    missing from the set sends the resolution to the index for an
    older release that still fits, which then fails on an import the
    current source made.
    """
    wheelhouse.mkdir(exist_ok=True)
    # A member at either depth: packages/<name>/ or packages/<group>/<name>/.
    packages = source_root / "packages"
    members = sorted(
        path.parent
        for path in [
            *packages.glob("*/workshop.toml"),
            *packages.glob("*/*/workshop.toml"),
        ]
    )
    assert members, f"no members under {source_root}/packages"
    for member in members:
        _run(
            ["uv", "build", "--wheel", "-o", str(wheelhouse), str(member)],
            source_root,
            env,
        )


def _pin_the_wheelhouse(wheelhouse: Path) -> Path:
    """Pin every built member to its own build; the override file.

    A member's declared floor may name a version the release train
    has not published yet, which a member still at its newborn
    version cannot satisfy. The resolution would then reach past the
    wheelhouse for an older release that fits, and the chain would
    test that release instead of this source. Every wheel built here
    wins outright.
    """
    newest: dict[str, tuple[tuple[int, ...], str]] = {}
    for wheel in sorted(wheelhouse.glob("*.whl")):
        dist, version = wheel.name.split("-")[:2]
        name = dist.replace("_", "-")
        key = tuple(int(part) for part in version.split(".") if part.isdigit())
        if name not in newest or key > newest[name][0]:
            newest[name] = (key, version)
    path = wheelhouse / "overrides.txt"
    path.write_text(
        "\n".join(f"{name}=={version}" for name, (_key, version) in newest.items())
        + "\n"
    )
    return path


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
        # Every workspace the chain builds registers its native members
        # as conan editables: in the chain's own home, never the machine's.
        "CONAN_HOME": str(tmp_path / "conan-home"),
    }
    # The chain plays a person at a workstation. The suite's own rig
    # marks this session as a CI run, and a verb that behaves
    # differently there would be exercised in the wrong mode: the
    # gate refuses `--fix` inside CI, which is right there and wrong
    # here, where the update verb asks for exactly that.
    for marker in ("CI", "GITHUB_ACTIONS", "GITHUB_RUN_ID", "GITEA_ACTIONS"):
        base_env.pop(marker, None)
    # Nor does it inherit this suite's own instrumentation. The
    # workspaces it builds run their own gates, and a child that
    # starts coverage under the outer configuration writes rows for
    # files that exist only in its temporary tree, which the outer
    # report then cannot resolve.
    for measured in [name for name in base_env if name.startswith("COVERAGE_")]:
        base_env.pop(measured, None)
    # Nor the runner's loop belt. A global `fm` that handed this suite
    # to `uv run --project` set it, and a child `fm` that inherits it
    # skips its own handoff and runs this workspace's footman against
    # the born project's tasks file, where that project's extensions are
    # not importable (livery#865).
    for belt in ("FOOTMAN_UV_REEXEC", "FOOTMAN_NO_UV"):
        base_env.pop(belt, None)
    fm = str(ROOT / ".venv" / "bin" / "fm")
    for name in ("dummy", "child"):
        _destroy(token, name)
    try:
        _chain(tmp_path, token, fm, base_env, resumed=False)
        # The chain re-run: the second pass resumes and no-ops.
        _chain(tmp_path, token, fm, base_env, resumed=True)
    finally:
        if not os.environ.get("WORKSHOP_CHAIN_KEEP"):
            for name in ("dummy", "child"):
                _destroy(token, name)


def _chain(
    tmp_path: Path, token: str, fm: str, base_env: dict[str, str], *, resumed: bool
) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    if not resumed:
        _build_wheels(ROOT, wheelhouse, base_env)
    # The tool store is a content-addressed cache every checkout on
    # this machine shares, and the workspaces the chain builds reach
    # for the pinned checkers like any other. The suite's isolation
    # points the data directory at a scratch home, where a chain run
    # would download every tool again, so the children are given the
    # machine's own: what a second workspace here actually uses.
    from livery.footman import _paths  # pyright: ignore[reportPrivateUsage]

    env = {
        **base_env,
        "UV_FIND_LINKS": str(wheelhouse),
        "UV_OVERRIDE": str(_pin_the_wheelhouse(wheelhouse)),
        "FOOTMAN_DATA_DIR": str(_paths.data_home() / "footman"),
    }

    # Above any project stock fm mounts only footman's own builtins
    # (the footman#536 gap), so the chain bridges through a scratch
    # config dir: the documented user-rung tasks file, scoped to
    # these invocations, never the machine's real config. The
    # config-dir variable names it, not XDG_CONFIG_HOME: the suite's
    # isolation plugin already points that variable at a scratch home
    # of its own, and the variable beats XDG.
    bridge = tmp_path / "bridge-config"
    (bridge / "footman").mkdir(parents=True, exist_ok=True)
    (bridge / "footman" / "tasks.py").write_text(
        'from livery.footman.api import plugin\n\nplugin("livery.workshop")\n'
    )
    bridged = {
        **env,
        "XDG_CONFIG_HOME": str(bridge),
        "FOOTMAN_CONFIG_DIR": str(bridge / "footman"),
    }

    # -- 1. the home is born, self-hosting its brand ----------------
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    birth = _run(
        [
            fm,
            "new.project",
            "dummy",
            f"--extension={BRAND}",
            "--forge=gitea",
            f"--owner={OWNER}",
            f"--url={GITEA}",
            "--namespace=dummy",
            # A second birth in the same folder is a resume.
            *(["--resume"] if resumed else []),
        ],
        work,
        bridged,
    )
    home = work / "dummy"
    assert "done: merge the setup PR" in birth.stdout
    if resumed:
        assert "already scaffolded" in birth.stdout
        assert "seeds: already written" in birth.stdout
    contract = (home / "workshop.toml").read_text()
    # Stock fm's births list the stock extensions; the home adds itself.
    assert (
        'extensions = ["docs", "ruff", "basedpyright", "pytest", "dummy.brandx"]'
        in contract
    )
    # The docs seeds arrived at birth: the workspace's and the
    # member package's.
    assert (home / "docs" / "index.md").is_file()
    assert (home / "packages" / BRAND / "docs" / "index.md").is_file()

    # -- 2. populate: a declared replace, fragment line, a skill ------
    member = home / "packages" / BRAND
    package = member / "src" / "dummy" / BRAND
    if not resumed:
        ignore = package / "content" / "root" / ".gitignore"
        ignore.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(BASE_ROOT / ".gitignore", ignore)
        with ignore.open("a") as handle:
            handle.write("brandx-build/\n")
        with (package / "_extension.py").open("a") as handle:
            handle.write(
                "\n#: The base files this extension ships in place of the base's.\n"
                "REPLACES = {\n"
                '    "livery.workshop:.gitignore": "the brand ignores its own'
                ' build tree",\n'
                "}\n"
            )
        fragment = member / "src" / "dummy" / BRAND / "content" / "fragments"
        with (fragment / f"rules.{BRAND}.md").open("a") as handle:
            handle.write("\nAlways speak plainly.\n")
        skill = member / "src" / "dummy" / BRAND / "content" / "skills" / "hello"
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text(
            "<!-- Shipped as dummy.brandx extension content, delivered to the"
            " workspace\n"
            "     by the sync verb. Edit this copy in the extension and release it;\n"
            "     an edited delivered copy is a local override, kept and named.\n"
            "-->\n# hello\n\nSay hello.\n"
        )
        # The generator seam's in-repo consumer: the member declares
        # a docs generator (a task its extension plugin already ships the
        # group for) that writes a page and rewrites its nav block.
        with (member / "src" / "dummy" / BRAND / "_tasks.py").open("a") as handle:
            handle.write(
                f'\n\n@{BRAND}.task(name="docsgen")\n'
                "def docsgen() -> None:\n"
                '    """Generate the tools page and its nav block."""\n'
                "    from pathlib import Path\n"
                "\n"
                "    from livery.workshop.api import rewrite_nav_block\n"
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
        # The generator emits its nav block beside its pages; the build
        # assembles the config from it, so nothing committed changes and
        # later builds are no-ops on a clean tree.
        _run([fm, f"{BRAND}.docsgen"], home, _hermetic(env, home / ".venv"))
        _run([fm, "drift.check", "--fix"], home, _hermetic(env, home / ".venv"))
        _run(["git", "add", "-A"], home, env)
        _run(["git", "commit", "-qm", "feat: populate the brand"], home, env)

    # The seam end to end: the declared generator ran, its page is in
    # the built site, and a (re)build leaves the tree clean.
    home_build = _run([fm, "docs.build"], home, _hermetic(env, home / ".venv"))
    assert f"{BRAND}.docsgen" in home_build.stdout
    generated_page = home / "site" / "packages" / BRAND / "tools"
    assert (generated_page / "index.html").is_file()
    clean = _run(["git", "status", "--porcelain"], home, env)
    assert clean.stdout.strip() == ""

    # -- 3. the home releases its wheel -------------------------------
    _run(
        ["uv", "build", "--wheel", "-o", str(wheelhouse), f"packages/{BRAND}"],
        home,
        env,
    )
    _pin_the_wheelhouse(wheelhouse)

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
    assert (
        'extensions = ["docs", "ruff", "basedpyright", "pytest", "dummy.brandx"]'
        in child_contract
    )
    gate = (child / ".gitea" / "workflows" / "ci.yml").read_text()
    assert f"{BRAND} ci.run --point=gate --job=check" in gate
    # The brand's replacement reached the child's composed file.
    assert "brandx-build/" in (child / ".gitignore").read_text()
    # The brand's content arrived through sync.
    assert (
        "Always speak plainly."
        in (child / ".workshop" / "fragments" / f"rules.{BRAND}.md").read_text()
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
    _run(
        [str(brand_cli), "drift.check", "--fix"],
        child,
        _hermetic(env, tool),
        check=False,
    )
    rendered_gate = (child / ".gitea" / "workflows" / "ci.yml").read_text()
    assert "Environment rung" in rendered_gate
    assert "RUNG_PYTHON_PUBLISH_INDEX" in rendered_gate

    # -- 5b. the docs toolchain, through the gradient -----------------
    # The extension at its installed version renders the child's site
    # config and builds the site; no template re-render happens, and
    # the site speaks the child's name, never the base's.
    docs_build = _run([str(brand_cli), "docs.build"], child, _hermetic(env, tool))
    assert "site built" in docs_build.stdout
    child_config = (child / "zensical.toml").read_text()
    assert 'site_name = "child"' in child_config
    assert (child / "site" / "index.html").is_file()
    assert "child" in (child / "site" / "index.html").read_text()

    # -- 5c. both kinds arrive through the gradient -------------------
    # The child creates a C/C++ library and an extension depending
    # on it with the brand's own verbs: no template re-render, the
    # honest skips in the gate, and the tool profile grown only
    # here. The extension links both sides of the dependency graph,
    # the sibling library at HEAD and a package from Conan Center,
    # and the compiled module answers from each.
    # ty refuses an empty-but-set VIRTUAL_ENV, so the child's gate
    # runs with the variable naming the child's own venv.
    child_env = {
        **_hermetic(env, child / ".venv"),
        "VIRTUAL_ENV": str(child / ".venv"),
        # The child registers its library editable and builds packages
        # into a conan home: the chain's own, never the machine's,
        # which would keep an editable pointing at a temporary tree
        # long after the run.
        "CONAN_HOME": str(tmp_path / "conan-home"),
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
                # A newborn's first version, which is what the library
                # beside it carries until its own first release.
                'floor = "0.0.0"\n'
            )
        _link_the_library(child / "packages" / "ext", child / "packages" / "geometry")
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
    _name_the_tool_index(child, ROOT / "records")
    # The lock is the newborn's first: the sync materialises what a
    # lock names, and a workspace that has never locked has nothing
    # to materialise (livery#765).
    _run([str(child_fm), "tools.lock"], child, child_env)
    # The sync materialises the native tools the two members grew into
    # the profile, registers the library editable, and builds the
    # extension against it: conan resolves fmt from Conan Center and
    # the sibling from its source tree at HEAD.
    _run([str(child_fm), "sync"], child, child_env)
    # The module compiled when the member was born does not rebuild
    # when its sources change (livery#766), so the extension is
    # reinstalled once the wiring is in place. It runs in the
    # workspace's own environment, where conan and the CMake provider
    # are: only the verbs enter on their own.
    _run(
        ["uv", "sync", "--reinstall-package", "kid-ext"],
        child,
        _entered(child_fm, child, child_env),
    )
    child_gate = _run([str(child_fm), "check"], child, child_env)
    # The kind's checks, one line each: the registry walks a native
    # package's records in order, and a role the kind lacks skips by
    # name.
    for check in (
        "format.clang-format",
        "build.configure",
        "build.compile",
        "test.ctest",
        "lint.clang-tidy",
    ):
        assert (
            f"  {check}: packages/geometry runs (cpp-conan kind)" in child_gate.stdout
        )
    assert (
        "typecheck.basedpyright: packages/geometry skips (cpp-conan kind)"
        in child_gate.stdout
    )
    # One compiled module, both sides of the graph: the member's own
    # test calls fmt through the greeting and the sibling library
    # through its version, and the gate above ran it. Four tests,
    # the template's three and this one.
    assert "speed packages/ext: " in child_gate.stdout
    assert "over 4 tests" in child_gate.stdout
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
    content2 = improved / "src" / "livery" / "workshop" / "content" / "root"
    with (content2 / "tasks.py.jinja").open("a") as handle:
        handle.write("\n# The core teaches: run the gate before every commit.\n")
    with (content2 / ".gitignore").open("a") as handle:
        handle.write("core-scratch/\n")
    with (
        improved
        / "src"
        / "livery"
        / "workshop"
        / "content"
        / "fragments"
        / "rules.workshop.md"
    ).open("a") as handle:
        handle.write("\nThe gate's verdict is its exit code.\n")
    # The bump is computed from what the member carries: a spelled
    # version here goes stale the day the base is released again, and
    # a build that lands on the same version is no upgrade at all, so
    # the improvement would never travel.
    import re as _re

    pyproject = improved / "pyproject.toml"
    text = pyproject.read_text()
    found = _re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"$', text, _re.M)
    assert found, "the base declares no version to bump"
    major, minor, patch = (int(part) for part in found.groups())
    pyproject.write_text(
        text.replace(found.group(0), f'version = "{major}.{minor}.{patch + 1}"', 1)
    )
    _run(["uv", "build", "--wheel", "-o", str(wheelhouse)], improved, env)
    _pin_the_wheelhouse(wheelhouse)

    # The home takes the base bump the real way: the lock moves to the
    # new wheel, sync refreshes the environment (the bumped member's
    # own metadata included), then rebuilds the brand's wheel.
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
    _run(
        ["uv", "build", "--wheel", "-o", str(wheelhouse), f"packages/{BRAND}"],
        home,
        env,
    )
    _pin_the_wheelhouse(wheelhouse)

    # The child updates: new brand and base wheels arrive, then the
    # composed files move to what they ship.
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
        in (child / ".workshop" / "fragments" / "rules.workshop.md").read_text()
    )
    # So did the composed files: a sync ends by writing them from the
    # wheels it just installed. The core improvement reached the
    # grandchild through the gradient.
    assert "run the gate before every commit" in (child / "tasks.py").read_text()
    ignored = (child / ".gitignore").read_text()
    # The replaced file did not move: the named forfeit is the brand's
    # declared replace, and the base's new line stays out.
    assert "brandx-build/" in ignored
    assert "core-scratch/" not in ignored
    # No extension-owned line changed: the brand's content stands.
    assert (
        "Always speak plainly."
        in (child / ".workshop" / "fragments" / f"rules.{BRAND}.md").read_text()
    )
