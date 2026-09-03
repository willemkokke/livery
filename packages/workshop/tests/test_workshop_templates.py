"""The render gate: byte-honest, drift-naming, namespace-clean."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from livery.workshop._templates import (
    apply_packages,
    apply_project,
    package_drift,
    project_drift,
    read_answers,
    render,
)

ROOT = Path(__file__).resolve().parents[3]


def _template_instance(tmp_path: Path) -> Path:
    """A scratch workspace carrying the real template source and answers."""
    shutil.copytree(ROOT / "templates", tmp_path / "templates")
    shutil.copy(ROOT / ".copier-answers.yml", tmp_path / ".copier-answers.yml")
    (tmp_path / "workshop.toml").write_text('[workspace]\ntemplates = "templates"\n')
    return tmp_path


def test_the_template_source_is_the_contracts_call(tmp_path: Path) -> None:
    from livery.workshop._templates import (
        DEFAULT_TEMPLATE_SOURCE,
        local_template_dir,
        template_source,
    )

    (tmp_path / "workshop.toml").write_text("[workspace]\n")
    assert template_source(tmp_path) == DEFAULT_TEMPLATE_SOURCE
    assert local_template_dir(tmp_path) is None  # remote: no local dir
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\ntemplates = "my-fork-checkout"\n'
    )
    assert local_template_dir(tmp_path) is None  # declared but absent
    (tmp_path / "my-fork-checkout").mkdir()
    assert local_template_dir(tmp_path) == tmp_path / "my-fork-checkout"
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\ntemplates = "git@example.com:me/fork.git"\n'
    )
    assert local_template_dir(tmp_path) is None


def test_the_monorepo_matches_its_own_render() -> None:
    # The dogfood pin: contract 8 says the monorepo is the workshop's
    # first instance, so a fresh render of the project kind agrees
    # with the committed tree byte for byte.
    assert project_drift(ROOT) == []
    # Each package's managed files answer to the template too.
    assert package_drift(ROOT) == []


def test_package_drift_judges_only_the_managed_files(tmp_path: Path) -> None:
    # The seeds must NOT be judged: a package writes its own README
    # and changelog the moment it is born, and reporting those as
    # drift would ask a living package to revert to its stub.
    root = _template_instance(tmp_path)
    apply_project(root)
    package = root / "packages" / "thing"
    package.mkdir(parents=True)
    answers = read_answers(ROOT / "packages" / "workshop" / ".copier-answers.yml")
    answers["package_name"] = "livery-thing"
    (package / ".copier-answers.yml").write_text(
        "\n".join(f"{key}: {value!r}" for key, value in answers.items()) + "\n"
    )
    # Nothing rendered yet: the managed file is named as missing.
    assert package_drift(root) == [
        "packages/thing/cliff.toml: rendered, but missing from the repository"
    ]
    assert "packages/thing/cliff.toml" in apply_packages(root)
    assert package_drift(root) == []
    assert apply_packages(root) == []  # idempotent
    # A README the package's authors wrote is not the template's to keep.
    (package / "README.md").write_text("# thing\n\nWritten by its authors.\n")
    assert package_drift(root) == []
    (package / "cliff.toml").write_text("# edited by hand\n")
    assert package_drift(root) == ["packages/thing/cliff.toml: differs from its render"]


def test_apply_settles_and_drift_names_the_file(tmp_path: Path) -> None:
    root = _template_instance(tmp_path)
    changed = apply_project(root)
    assert "workshop.toml" in changed and "pyproject.toml" in changed
    assert project_drift(root) == []
    assert apply_project(root) == []  # idempotent: a clean tree changes nothing
    # The drift stays valid TOML: the drift walker itself reads the
    # contract for the template source.
    (root / "workshop.toml").write_text('[workspace]\ntemplates = "templates"\n')
    assert project_drift(root) == ["workshop.toml: differs from its render"]


def _render_kind(tmp_path: Path, forge_kind: str, **extra: object) -> Path:
    destination = tmp_path / forge_kind
    answers = read_answers(ROOT / ".copier-answers.yml")
    answers.update({"kind": "project", "forge_kind": forge_kind}, **extra)
    answers.update(extra)
    render(ROOT / "templates", destination, answers)
    return destination


def test_each_forge_kind_generates_a_ci_definition_that_lints(
    tmp_path: Path,
) -> None:
    # Contract: generate over template. The workflow files are emitted
    # from the answers, never rendered, so the template must produce
    # none and the emitters must produce valid, gate-carrying YAML for
    # every kind.
    import yaml

    from livery.workshop._ci_generate import generate

    answers = read_answers(ROOT / ".copier-answers.yml")

    github = _render_kind(tmp_path, "github")
    assert not (github / ".github").exists()  # nothing templated remains
    assert not (github / ".gitea").exists()
    assert not (github / ".gitlab-ci.yml").exists()
    files = generate({**answers, "forge_kind": "github"})
    ci = yaml.safe_load(files[".github/workflows/ci.yml"])
    assert "gate" in ci["jobs"]
    release = yaml.safe_load(files[".github/workflows/release.yml"])
    assert "publish" in release["jobs"]
    # The trigger is the merge, never a tag: tags are receipts.
    assert "pull_request" in release[True] or "pull_request" in release["on"]

    files = generate(
        {
            **answers,
            "forge_kind": "gitea",
            "publish_index": "https://forge.example.com/api/packages/o/pypi",
        }
    )
    ci = yaml.safe_load(files[".gitea/workflows/ci.yml"])
    assert "gate" in ci["jobs"]
    release = yaml.safe_load(files[".gitea/workflows/release.yml"])
    assert "publish" in release["jobs"]

    files = generate({**answers, "forge_kind": "gitlab"})
    pipeline = yaml.safe_load(files[".gitlab-ci.yml"])
    assert "gate" in pipeline and "release-publish" in pipeline
    assert pipeline["workflow"]["rules"]

    gitea = _render_kind(tmp_path, "gitea", forge_url="https://forge.example.com")
    contract = (gitea / "workshop.toml").read_text()
    assert 'url = "https://forge.example.com"' in contract
    gitlab = _render_kind(tmp_path, "gitlab")
    for rendered in (github, gitea, gitlab):
        assert 'required_context = "gate"' in (rendered / "workshop.toml").read_text()


def test_a_package_renders_namespace_clean(tmp_path: Path) -> None:
    destination = tmp_path / "scratch"
    answers = read_answers(ROOT / ".copier-answers.yml")
    render(
        ROOT / "templates",
        destination,
        {
            "kind": "package-python",
            "package_name": "livery-scratch",
            "package_description": "livery-scratch: a livery workspace package.",
            "namespace_package": "livery",
            "author_name": answers["author_name"],
            "author_email": answers["author_email"],
            "copyright_year": answers["copyright_year"],
            "forge_owner": answers["forge_owner"],
            "project_name": answers["project_name"],
            "python_versions": answers["python_versions"],
        },
    )
    module = destination / "src" / "livery" / "scratch"
    assert (module / "__init__.py").is_file()
    assert (module / "py.typed").is_file()
    assert (destination / "tests" / "test_scratch_package.py").is_file()
    # The namespace stays PEP 420: no livery/__init__.py, ever.
    assert not (destination / "src" / "livery" / "__init__.py").exists()
    assert "livery-scratch" in (destination / "pyproject.toml").read_text()


def test_the_emitters_call_the_running_brand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import re

    import footman

    from livery.workshop._ci_generate import generate

    monkeypatch.setattr(footman, "prog", lambda: "hse")
    answers = read_answers(ROOT / ".copier-answers.yml")
    for kind in ("github", "gitea", "gitlab"):
        for path, content in generate({**answers, "forge_kind": kind}).items():
            assert "hse check" in content or "hse workflow" in content, path
            # No emitted word spells fm under a brand: the meter is
            # env-armed, so not even a module spelling remains.
            assert re.search(r"\bfm\b", content) is None, path


def test_the_default_brand_emits_fm() -> None:
    from livery.workshop._ci_generate import generate

    answers = read_answers(ROOT / ".copier-answers.yml")
    gate = generate({**answers, "forge_kind": "github"})[".github/workflows/ci.yml"]
    assert "fm coverage.enforce" in gate


def test_the_rendered_prose_spells_the_brand(tmp_path: Path) -> None:
    from livery.workshop._templates import render

    answers = read_answers(ROOT / ".copier-answers.yml")
    destination = tmp_path / "branded"
    render(
        ROOT / "templates",
        destination,
        {**answers, "runner_prog": "hse"},
    )
    tasks = (destination / "tasks.py").read_text()
    assert "uv run hse <task>" in tasks
    assert "``hse check``" in tasks


def test_the_rendered_answers_never_store_the_brand(tmp_path: Path) -> None:
    from livery.workshop._templates import render

    answers = read_answers(ROOT / ".copier-answers.yml")
    destination = tmp_path / "branded"
    render(ROOT / "templates", destination, {**answers, "runner_prog": "hse"})
    stored = (destination / ".copier-answers.yml").read_text()
    # The brand belongs to the process; a stored copy would pin the
    # instance to the CLI that happened to render it.
    assert "runner_prog" not in stored
    # The meter comment rides the brand too.
    assert "Every hse child" in (destination / "pyproject.toml").read_text()
    assert "`hse sync`" in (destination / "CLAUDE.md").read_text()


def test_the_shell_and_completion_lines_run_the_brand() -> None:
    from livery.workshop._env_tasks import _COMPLETION_HOOK, _COMPLETION_PWSH
    from livery.workshop._shell import _POSIX_ENTER, _PWSH_ENTER

    for template in (_POSIX_ENTER, _PWSH_ENTER):
        line = template.format(prog="hse", root="'/w s'")
        assert "hse -C=" in line and "fm -C" not in line
    posix = _COMPLETION_HOOK.format(prog="hse")
    assert "$(hse --setup-completion)" in posix
    pwsh = _COMPLETION_PWSH.format(prog="hse")
    assert "Get-Command hse" in pwsh
    assert "(hse --setup-completion=pwsh" in pwsh
    assert "{" in pwsh and "}" in pwsh  # the braces survived the format


def test_the_pipe_guard_recognises_the_brand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import footman

    from livery.workshop._hooks import _runs_runner

    monkeypatch.setattr(footman, "prog", lambda: "hse")
    pattern = _runs_runner()
    assert pattern.search("hse check") is not None
    assert pattern.search("uv run hse check") is not None
    # The stock spellings stay guarded under any brand.
    assert pattern.search("fm check") is not None
    assert pattern.search("footman check") is not None
    assert pattern.search("shse check") is None


def test_the_gitignore_header_speaks_the_brand() -> None:
    from livery.workshop._materialise import _GITIGNORE_HEADER

    assert "`hse sync`" in _GITIGNORE_HEADER.format(prog="hse")


def _instance_from_git_template(tmp_path: Path) -> tuple[Path, Path]:
    """A scratch git template repo and an instance rendered from it."""
    import shutil
    import subprocess

    from livery.workshop import __version__

    repo = tmp_path / "template-repo"
    shutil.copytree(ROOT / "templates", repo)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@l"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)
    subprocess.run(["git", "tag", f"v{__version__}"], cwd=repo, check=True)
    instance = tmp_path / "instance"
    answers = read_answers(ROOT / ".copier-answers.yml")
    render(repo, instance, {**answers, "runner_prog": "fm"})
    # copier update works only in a git-tracked destination, which
    # every real instance is.
    subprocess.run(["git", "init", "-q"], cwd=instance, check=True)
    subprocess.run(["git", "config", "user.email", "t@l"], cwd=instance, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=instance, check=True)
    subprocess.run(["git", "add", "-A"], cwd=instance, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=instance, check=True)
    return repo, instance


def test_the_remote_update_arm_brands_and_reemits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The arm every instance takes: no local template directory, the
    # source is a git repository, and rebranding is exactly this run
    # under the branded CLI.
    import footman

    from livery.workshop import _templates
    from livery.workshop._update import refresh_rendered

    repo, instance = _instance_from_git_template(tmp_path)
    assert "uv run fm <task>" in (instance / "tasks.py").read_text()
    contract = (instance / "workshop.toml").read_text()
    lines = [
        f'templates = "{repo}"' if line.startswith("templates = ") else line
        for line in contract.splitlines()
    ]
    assert any(line.startswith("templates = ") for line in lines)
    (instance / "workshop.toml").write_text("\n".join(lines) + "\n")
    import subprocess

    subprocess.run(["git", "add", "-A"], cwd=instance, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "point at the repo"], cwd=instance, check=True
    )
    monkeypatch.setattr(_templates, "local_template_dir", lambda _root: None)
    monkeypatch.setattr(footman, "prog", lambda: "hse")
    changed = refresh_rendered(instance)
    assert changed  # the update reported work
    tasks = (instance / "tasks.py").read_text()
    assert "uv run hse <task>" in tasks and "uv run fm <task>" not in tasks
    gate = (instance / ".github/workflows/ci.yml").read_text()
    assert "hse coverage.enforce" in gate  # the workflows re-emitted branded


def test_no_runtime_string_spells_the_default_brand() -> None:
    # The teachings speak the running brand; a literal `fm verb` in a
    # non-docstring string is a regression this pin catches. The
    # docstrings document with the default spelling on purpose.
    import ast
    import io
    import tokenize

    source_dir = ROOT / "packages" / "workshop" / "src" / "livery" / "workshop"
    offenders: list[str] = []
    for path in sorted(source_dir.rglob("*.py")):
        source = path.read_text()
        if "`fm " not in source and "    fm " not in source:
            continue
        docstrings: set[tuple[int, int]] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                body = node.body
                if body and isinstance(body[0], ast.Expr):
                    value = body[0].value
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        docstrings.add((value.lineno, value.col_offset))
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if (
                tok.type == tokenize.STRING
                and "`fm " in tok.string
                and (tok.start[0], tok.start[1]) not in docstrings
            ):
                offenders.append(f"{path.name}:{tok.start[0]}")
            # FSTRING_MIDDLE arrived with 3.12's f-string tokens; on
            # an older tokenize the STRING arm above already covers
            # f-strings whole.
            middle = getattr(tokenize, "FSTRING_MIDDLE", None)
            if middle is not None and tok.type == middle and "`fm " in tok.string:
                offenders.append(f"{path.name}:{tok.start[0]}")
    assert offenders == [], offenders
