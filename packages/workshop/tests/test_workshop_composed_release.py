"""The composed artifact release: contract 21 built and forced."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
TEMPLATES = ROOT / "packages/workshop/src/livery/workshop/templates"

_FAILURES = (BaseException,)


def _home(tmp_path: Path) -> Path:
    """A layer home with one overlay addition and a declared replace."""
    root = tmp_path / "home"
    member = root / "packages" / "brand"
    overlay = member / "src" / "acme" / "brand" / "templates"
    (overlay / "project").mkdir(parents=True)
    (root / "workshop.toml").write_text(
        "[workspace]\n"
        'layers = ["livery.workshop", "acme.brand"]\n'
        'templates_artifact = ""\n'
        "\n"
        '[forge]\nkind = "gitea"\nowner = "acme"\nurl = "https://forge.acme.example"\n'
        "\n"
        '[ci]\nrunners = ["ubuntu-latest"]\nrequired_context = "gate"\n'
    )
    (root / "pyproject.toml").write_text(
        '[project]\nname = "home"\nrequires-python = ">=3.11"\n'
    )
    (member / "workshop.toml").write_text('type = "python"\nname = "acme-brand"\n')
    (member / "pyproject.toml").write_text('[project]\nname = "acme-brand"\n')
    (overlay / "project" / "BRAND.md.jinja").write_text("# {{ project_name }}\n")
    (root / ".copier-answers.yml").write_text(
        "_src_path: whatever\nkind: project\nproject_name: home\n"
        "author_name: A\nauthor_email: a@e\ncopyright_year: '2026'\n"
        "namespace_package: acme\npackages: []\n"
    )
    return root


def _artifact_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "artifact.git"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "-q", "--bare", "--initial-branch=main"], cwd=repo, check=True
    )
    return repo


@pytest.fixture(autouse=True)
def _versions(monkeypatch: pytest.MonkeyPatch) -> None:
    from importlib.metadata import version as real_version

    def fake_version(dist: str) -> str:
        if dist == "acme-brand":
            return "0.5.0"
        return real_version(dist)

    monkeypatch.setattr("importlib.metadata.version", fake_version)


def test_template_ref_is_the_topmost_tree_shipping_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._templates import template_ref

    root = _home(tmp_path)
    # In the home the brand ships its tree from the workspace.
    assert template_ref(root) == "v0.5.0"
    # A plain instance's topmost tree-shipper is the base layer.
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "workshop.toml").write_text('[workspace]\nlayers = ["livery.workshop"]\n')
    from importlib.metadata import version

    assert template_ref(plain) == "v" + version("livery-workshop")


def test_the_home_publishes_its_composed_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from importlib.metadata import version

    from livery.workshop._release import release_templates

    root = _home(tmp_path)
    repo = _artifact_repo(tmp_path)
    monkeypatch.setattr(
        "livery.workshop._release.workspace_root", lambda start=None: root
    )
    release_templates(remote=str(repo))
    out = capsys.readouterr().out
    assert "published v0.5.0" in out

    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(repo), str(clone)], check=True)
    subprocess.run(["git", "checkout", "-q", "v0.5.0"], cwd=clone, check=True)
    assert (clone / "project" / "BRAND.md.jinja").is_file()
    assert (clone / "project" / "pyproject.toml.jinja").is_file()  # the base rode along
    record = (clone / "composition.toml").read_text()
    assert 'base = "livery-workshop"' in record
    assert f'base_version = "{version("livery-workshop")}"' in record
    assert 'publisher = "acme-brand"' in record

    # Idempotent: the same content at the same tag is a quiet success.
    release_templates(remote=str(repo))
    assert "already published with this content" in capsys.readouterr().out

    # W6: same version, different content refuses before push.
    overlay = root / "packages/brand/src/acme/brand/templates/project"
    (overlay / "BRAND.md.jinja").write_text("# changed\n")
    with pytest.raises(_FAILURES) as caught:
        release_templates(remote=str(repo))
    assert "immutable" in str(caught.value)


def test_an_ordinary_instance_refuses_to_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._release import release_templates

    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "workshop.toml").write_text('[workspace]\nlayers = ["livery.workshop"]\n')
    monkeypatch.setattr(
        "livery.workshop._release.workspace_root", lambda start=None: plain
    )
    with pytest.raises(_FAILURES) as caught:
        release_templates()
    assert "templates_artifact" in str(caught.value)


def test_the_emitters_gate_the_artifact_job_on_the_home_shape(
    tmp_path: Path,
) -> None:
    from livery.workshop._ci_generate import generate

    root = _home(tmp_path)
    contract = (root / "workshop.toml").read_text()
    (root / "workshop.toml").write_text(
        contract.replace(
            'templates_artifact = ""',
            'templates_artifact = "https://forge.acme.example/acme/brand-templates.git"',
        )
    )
    release = generate(root)[".gitea/workflows/release.yml"]
    assert "Publish the template artifact" in release
    assert "release.templates" in release
    assert "FORGE_TOKEN: ${{ secrets.FORGE_TOKEN }}" in release

    # Without the declaration, no job: an instance publishes nothing.
    (root / "workshop.toml").write_text(contract)
    release = generate(root)[".gitea/workflows/release.yml"]
    assert "Publish the template artifact" not in release


def test_a_child_renders_from_the_composed_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop._release import release_templates
    from livery.workshop._templates import new_package

    home = _home(tmp_path)
    repo = _artifact_repo(tmp_path)
    monkeypatch.setattr(
        "livery.workshop._release.workspace_root", lambda start=None: home
    )
    release_templates(remote=str(repo))
    capsys.readouterr()

    child = tmp_path / "child"
    child.mkdir()
    (child / "workshop.toml").write_text(
        "[workspace]\n"
        'layers = ["livery.workshop", "acme.brand"]\n'
        f'templates = "git+file://{repo}"\n'
        "\n"
        '[forge]\nkind = "gitea"\nowner = "kid"\nurl = "https://forge.acme.example"\n'
        "\n"
        '[ci]\nrunners = ["ubuntu-latest"]\nrequired_context = "gate"\n'
    )
    (child / "pyproject.toml").write_text(
        '[project]\nname = "child"\nrequires-python = ">=3.11"\n'
    )
    (child / ".copier-answers.yml").write_text(
        "_src_path: whatever\nkind: project\nproject_name: child\n"
        "author_name: A\nauthor_email: a@e\ncopyright_year: '2026'\n"
        "namespace_package: kid\npackages: []\n"
    )
    # The child's brand layer arrives installed, not as a member: the
    # wheel arm of the tree probe answers for it.
    from livery.workshop import _compose

    real_tree = _compose.layer_template_tree

    def wheel_arm(root: Path, layer: str) -> Path | None:
        if layer == "acme.brand" and root == child:
            return tmp_path / "not-a-member"  # any non-member path
        return real_tree(root, layer)

    monkeypatch.setattr(
        "livery.workshop._templates.workspace_root", lambda start=None: child
    )
    monkeypatch.setattr("livery.workshop._uv.run_uv", lambda *args, root: None)
    monkeypatch.setattr("livery.workshop._compose.layer_template_tree", wheel_arm)
    new_package("thing")
    assert (child / "packages" / "thing" / "cliff.toml").is_file()
    # The anchor was the brand's tag, never the base's.
    out = capsys.readouterr().out
    assert "rendered, wired, and installed" in out


@pytest.mark.parametrize("kind", ["github", "gitea", "gitlab"])
@pytest.mark.parametrize("artifact", ["", "https://forge.acme.example/acme/t.git"])
def test_every_generated_workflow_parses_as_yaml(
    tmp_path: Path, kind: str, artifact: str
) -> None:
    # The drift gate compares the committed files to the emitters byte
    # for byte, so an emitter that writes invalid YAML passes the gate
    # and every triggered run dies at startup, reported by the forge
    # as a zero-second failure no required check surfaces. Parsing is
    # the only guard that catches the emitted text being unusable.
    import yaml

    from livery.workshop._ci_generate import generate

    root = _home(tmp_path)
    contract = (root / "workshop.toml").read_text()
    contract = contract.replace('kind = "gitea"', f'kind = "{kind}"')
    contract = contract.replace(
        'templates_artifact = ""', f'templates_artifact = "{artifact}"'
    )
    (root / "workshop.toml").write_text(contract)
    for path, content in generate(root).items():
        if not path.endswith((".yml", ".yaml")):
            continue  # the docs config is TOML; only workflows are YAML
        parsed = yaml.safe_load(content)
        assert isinstance(parsed, dict), path
