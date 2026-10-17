"""The site's verbs: build, serve and publish, the builder and deploy tools faked.

The refusals first: no workspace, no built site, a builder that exits
non-zero, a container build or push that fails, an ssh step that
fails. Then what each verb does when its tools answer: the preview and
the workspace build, the serve, and the publish through each seam.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

import livery.footman as footman
import livery.toolroom.tools as tools
from livery.extensions.docs import _overrides, _site
from livery.extensions.docs._packages_page import LANDING, MOUNT
from livery.footman import Failed
from livery.workshop import PhaseContext
from livery.workshop._steps import StepRecord, register_step

_FAILURES = (SystemExit, Failed)


def _workspace(tmp_path: Path, *, docs_table: str = "", packages: bool = True) -> Path:
    """A workspace listing docs, its member core with an index page and a changelog."""
    root = tmp_path / "ws"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "index.md").write_text("# Home\n\nThe home page's prose.\n")
    (root / "workshop.toml").write_text(
        f'[workspace]\nextensions = ["docs"]\n{docs_table}'
    )
    (root / "pyproject.toml").write_text('[project]\nname = "acme-home"\n')
    if packages:
        core = root / "packages" / "core"
        (core / "src" / "acme" / "core").mkdir(parents=True)
        (core / "src" / "acme" / "core" / "__init__.py").write_text("")
        (core / "workshop.toml").write_text(
            'extensions = ["python"]\nname = "acme-core"\n'
        )
        (core / "pyproject.toml").write_text('[project]\nname = "acme-core"\n')
        (core / "docs").mkdir()
        (core / "docs" / "index.md").write_text("# core\n\nThe core manual.\n")
        (core / "CHANGELOG.md").write_text("# Changelog\n\n## [0.1.0]\n\n- first\n")
    return root


def _mount(root: Path) -> None:
    """Mount what *root* lists, so each phase's steps register as a verb finds them."""
    from livery.footman import _registry as registry
    from livery.workshop._extensions import mount_extensions

    with registry.capture():
        mount_extensions(root)


class _Fragment:
    """One human-audience fragment of the development section."""

    section = "voice"

    def text(self, root: Path, audience: str) -> str:
        """The fragment's markdown."""
        del root, audience
        return "# Tone\n\nSay it plainly.\n"


class _Zensical:
    """The site builder's stand-in: records each call and answers with *code*.

    A build that answers 0 leaves a site, an ``index.html`` under
    ``site/`` in the directory it ran in, as the builder does.
    """

    def __init__(self, code: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.code = code
        self.stdout = stdout
        self.stderr = stderr
        self.calls: list[tuple[str, Path, dict[str, object]]] = []
        self._cwd = Path()

    def opts(self, **options: object) -> _Zensical:
        """The builder run from the directory *options* names."""
        cwd = options["cwd"]
        assert isinstance(cwd, Path)
        self._cwd = cwd
        return self

    def build(self, **flags: object) -> SimpleNamespace:
        """Record the build; leave a site when it answers 0."""
        self.calls.append(("build", self._cwd, flags))
        if self.code == 0:
            (self._cwd / "site").mkdir(exist_ok=True)
            (self._cwd / "site" / "index.html").write_text("<h1>site</h1>\n")
        return SimpleNamespace(code=self.code, stdout=self.stdout, stderr=self.stderr)

    def serve(self, **flags: object) -> None:
        """Record the serve."""
        self.calls.append(("serve", self._cwd, flags))


@pytest.fixture
def zensical(monkeypatch: pytest.MonkeyPatch) -> _Zensical:
    """The builder faked where the verbs read it, and no CI run around them."""
    fake = _Zensical()
    monkeypatch.setattr(tools, "zensical", fake)
    monkeypatch.setattr("livery.workshop.ci_run", lambda: None)
    # The development section reads no fragment unless a test gives it one.
    monkeypatch.setattr(_site, "guidance", lambda root, audience: [])
    return fake


def _tool(
    answers: dict[str, int], seen: list[tuple[str, ...]]
) -> Callable[..., Callable[..., SimpleNamespace]]:
    """A deploy tool's ``opts``: each call recorded, its code from *answers*.

    The code is the one *answers* holds for the call's first argument,
    0 when it holds none.
    """

    def opts(**options: object) -> Callable[..., SimpleNamespace]:
        del options

        def run(*args: str) -> SimpleNamespace:
            seen.append(args)
            return SimpleNamespace(
                code=answers.get(args[0], 0),
                stdout=f"the {args[0]} output\n",
                stderr="",
            )

        return run

    return opts


# The refusals first.


def test_a_docs_verb_outside_a_workspace_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("livery.workshop.workspace_root", lambda start=None: None)
    with pytest.raises(
        _FAILURES, match=r"no workspace: no workshop\.toml above the working directory"
    ):
        _site.docs_publish()


def test_the_publish_refuses_before_any_step_without_a_built_site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    ran: list[str] = []

    def draw(ctx: PhaseContext) -> None:
        ran.append(ctx.step)

    register_step(StepRecord("draw", "deploy", draw, extension="acme.draw"))
    monkeypatch.chdir(root)
    with pytest.raises(
        _FAILURES, match=r"no built site at .*: run `docs\.build` first"
    ):
        _site.docs_publish()
    assert ran == []


def test_a_failed_preview_build_refuses_with_the_builders_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, zensical: _Zensical
) -> None:
    root = _workspace(tmp_path)
    _mount(root)
    monkeypatch.chdir(root)
    zensical.code, zensical.stderr = 1, "Error: a page is missing\n"
    with pytest.raises(
        _FAILURES, match=r"zensical build exited 1:\nError: a page is missing"
    ):
        _site.docs_build(package="core")


def test_a_failed_strict_build_refuses_before_the_agent_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, zensical: _Zensical
) -> None:
    root = _workspace(tmp_path)
    _mount(root)
    monkeypatch.chdir(root)
    zensical.code, zensical.stdout = 1, "Error: a link resolves nothing\n"
    with pytest.raises(
        _FAILURES, match=r"zensical build exited 1:\nError: a link resolves nothing"
    ):
        _site.docs_build()
    assert not (root / "site" / "llms.txt").exists()


def test_a_container_publish_refuses_a_failed_build_and_teaches_a_denied_push(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    monkeypatch.setattr(
        "livery.workshop.registry",
        lambda at, kind: SimpleNamespace(url="registry.acme.example/team"),
    )
    monkeypatch.setattr("livery.workshop.forge_repository", lambda at: "Acme/Home")
    answers = {"build": 1}
    seen: list[tuple[str, ...]] = []
    read: list[str] = []
    recorded = _tool(answers, seen)

    def opts(**options: object) -> Callable[..., SimpleNamespace]:
        run = recorded(**options)

        def docker(*args: str) -> SimpleNamespace:
            if args[0] == "build":
                # The Dockerfile the build reads, while the build runs.
                read.append(Path(args[2]).read_text())
            return run(*args)

        return docker

    monkeypatch.setattr(tools, "docker", SimpleNamespace(opts=opts))
    image = "registry.acme.example/team/acme/home-docs:latest"
    # The refusal first: a failed build pushes nothing, and its Dockerfile
    # is gone with it.
    with pytest.raises(_FAILURES, match=r"docker build exited 1:\nthe build output"):
        _site._publish_container(root)
    assert [args[0] for args in seen] == ["build"]
    assert not Path(seen[0][2]).exists()
    answers.update(build=0, push=1)
    with pytest.raises(
        _FAILURES, match=r"wants `docker login registry\.acme\.example` first"
    ):
        _site._publish_container(root)
    assert seen[-1] == ("push", image)
    assert not Path(seen[-2][2]).exists()
    answers.update(push=0)
    _site._publish_container(root)
    build, push = seen[-2:]
    assert build[:2] == ("build", "-f") and build[3:] == ("-t", image, ".")
    assert push == ("push", image)
    assert read == ["FROM nginx:1.27-alpine\nCOPY site /usr/share/nginx/html\n"] * 3
    # Each publish takes its Dockerfile with it, refused or pushed.
    assert not any(Path(args[2]).exists() for args in seen if args[0] == "build")


def test_an_ssh_publish_refuses_a_failed_step_and_ships_once_the_host_is_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    monkeypatch.setenv("DOCS_HOST", "docs.acme.example")
    monkeypatch.setenv("DOCS_USER", "deploy")
    monkeypatch.setenv("DOCS_ROOT", "/srv/docs")
    monkeypatch.setattr("livery.workshop.forge_repository", lambda at: "acme/home")
    destination, target = "deploy@docs.acme.example", "/srv/docs/acme/home"
    answers = {destination: 255}
    prepared: list[tuple[str, ...]] = []
    monkeypatch.setattr(tools, "ssh", SimpleNamespace(opts=_tool(answers, prepared)))
    shipped: list[str] = []
    code = {"ship": 0}

    def ship(command: str, **options: object) -> int:
        shipped.append(command)
        assert options["shell"] is True and options["cwd"] == root
        return code["ship"]

    monkeypatch.setattr(footman, "run", ship)
    with pytest.raises(
        _FAILURES, match=rf"preparing {destination}:{target} exited 255"
    ):
        _site._publish_ssh(root)
    assert shipped == []
    answers[destination] = 0
    code["ship"] = 2
    with pytest.raises(_FAILURES, match="shipping the site exited 2"):
        _site._publish_ssh(root)
    code["ship"] = 0
    _site._publish_ssh(root)
    assert prepared[-1] == (destination, f"rm -rf {target} && mkdir -p {target}")
    command = shipped[-1]
    assert command.startswith("bash -o pipefail -c ")
    assert f'tar -cf - -C site . | ssh {destination} "tar -xf - -C {target}"' in command


# Then what each verb does.


@pytest.mark.parametrize(
    ("seam", "published"),
    [("pages", []), ("none", []), ("ssh", ["ssh"]), ("container", ["container"])],
)
def test_the_publish_goes_through_the_seam_the_contract_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seam: str, published: list[str]
) -> None:
    root = _workspace(tmp_path, docs_table=f'[docs]\npublish = "{seam}"\n')
    (root / "site").mkdir()
    (root / "site" / "index.html").write_text("<h1>site</h1>\n")
    calls: list[str] = []
    monkeypatch.setattr(
        _site, "_publish_container", lambda at: calls.append("container")
    )
    monkeypatch.setattr(_site, "_publish_ssh", lambda at: calls.append("ssh"))
    monkeypatch.chdir(root)
    _site.docs_publish()
    # The pages seam is the forge's own deploy, and none publishes nothing.
    assert calls == published


def test_a_preview_build_runs_the_builder_in_the_preview_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, zensical: _Zensical
) -> None:
    root = _workspace(tmp_path)
    _mount(root)
    monkeypatch.chdir(root)
    _site.docs_build(package="core")
    preview = root / _site.PREVIEW / "core"
    config = preview / "zensical.toml"
    assert zensical.calls == [
        ("build", preview, {"clean": True, "config_file": str(config)})
    ]
    assert (preview / "docs" / "packages" / "core" / "index.md").is_file()
    # The preview is never the site: the workspace has none.
    assert not (root / "site").exists()


def test_the_workspace_build_writes_the_site_its_pages_and_the_agent_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    zensical: _Zensical,
) -> None:
    root = _workspace(tmp_path)
    _mount(root)
    monkeypatch.chdir(root)
    monkeypatch.setattr(_site, "guidance", lambda root, audience: [_Fragment()])
    monkeypatch.setattr(
        "livery.workshop.ci_run", lambda: SimpleNamespace(event="pull_request")
    )
    zensical.stdout = "Build finished in 0.10s\n"
    _site.docs_build()
    assert zensical.calls == [("build", root, {"clean": True, "strict": True})]
    # The development section and the release view are written before it.
    assert (root / "docs" / "development" / "voice.md").is_file()
    assert "No release tags" in (root / _site.RELEASES / "index.md").read_text()
    index = (root / "site" / "llms.txt").read_text()
    assert "- [Voice](/development/voice/)" in index
    assert (root / "site" / "llms-full.txt").is_file()
    # In CI the builder's own lines are the evidence a run leaves.
    assert "    Build finished in 0.10s\n" in capsys.readouterr().out


def test_a_workspace_without_packages_builds_without_a_packages_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zensical: _Zensical,
) -> None:
    root = _workspace(tmp_path, packages=False)
    # No listed extension ships site assets.
    monkeypatch.setattr(_overrides, "shipped_content", lambda start=None: ())
    _mount(root)
    monkeypatch.chdir(root)
    _site.docs_build()
    assert [call[0] for call in zensical.calls] == ["build"]
    # No package: no landing page, no staged assets, no section nav.
    assert not (root / MOUNT / LANDING).exists()
    assert not (root / "docs" / "_extensions").exists()
    assert (root / "site" / "llms.txt").is_file()


def test_the_serve_runs_the_builder_live_on_the_preview_or_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, zensical: _Zensical
) -> None:
    root = _workspace(tmp_path)
    _mount(root)
    monkeypatch.chdir(root)
    _site.docs_serve(package="core")
    preview = root / _site.PREVIEW / "core"
    assert zensical.calls == [
        ("serve", preview, {"config_file": str(preview / "zensical.toml")})
    ]
    zensical.calls.clear()
    _site.docs_serve()
    assert zensical.calls == [("serve", root, {})]
    # The workspace's serve carries the release view the nav points at.
    assert (root / _site.RELEASES / "index.md").is_file()
