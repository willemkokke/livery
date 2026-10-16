"""The python extension's runner of a package's documentation examples."""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.extensions.python import _backend as _python
from livery.footman import Failed
from livery.workshop._packages import Package
from livery.workshop._pytest_examples import MARKER


def _examples(tmp_path: Path) -> Path:
    base = tmp_path / "packages" / "x" / "docs" / "examples"
    base.mkdir(parents=True)
    (base / "ok.py").write_text(
        "from dataclasses import dataclass\n\n\n@dataclass\nclass Pair:\n"
        "    a: int\n\n\nassert Pair(1).a == 1\n"
    )
    (base / "conftest.py").write_text(
        "import pytest\n\n\n@pytest.hookimpl(wrapper=True)\n"
        "def pytest_runtest_call(item):\n"
        f"    assert item.get_closest_marker({MARKER!r}) is not None\n"
        "    item.config.stash.setdefault('seen', []).append(item.name)\n"
        "    return (yield)\n"
    )
    return base


def _package(tmp_path: Path) -> Package:
    directory = tmp_path / "packages" / "x"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "workshop.toml").write_text(
        'extensions = ["python"]\nname = "acme-x"\n'
    )
    (directory / "pyproject.toml").write_text('[project]\nname = "acme-x"\n')
    from livery.workshop._packages import discover_packages

    (tmp_path / "workshop.toml").write_text("[workspace]\n")
    return next(p for p in discover_packages(tmp_path) if p.path == "packages/x")


class _Result:
    def __init__(self, code: int) -> None:
        self.code = code
        self.stdout = "out\n"
        self.stderr = "err\n"


class _Pytest:
    def __init__(self, code: int) -> None:
        self.code = code
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def opts(self, **kwargs: object) -> _Pytest:
        self.calls.append((("opts",), kwargs))
        return self

    def __call__(self, *args: str) -> _Result:
        self.calls.append((args, {}))
        return _Result(self.code)


def test_the_runner_says_so_without_examples_and_names_a_red_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    package = _package(tmp_path)
    fake = _Pytest(code=1)
    monkeypatch.setattr(_python, "pytest", fake)
    _python.run_examples(package, tmp_path)
    assert "examples: packages/x has none" in capsys.readouterr().out
    assert fake.calls == []
    _examples(tmp_path)
    with pytest.raises(Failed, match="examples of packages/x: pytest exited 1"):
        _python.run_examples(package, tmp_path)
    out = capsys.readouterr().out
    assert "out" in out and "err" in out
    assert fake.calls[-1][0] == ("packages/x/docs/examples",)
    assert fake.calls[-2][1] == {"in_process": False, "cwd": tmp_path, "nofail": True}
    fake.code = 0
    _python.run_examples(package, tmp_path)
    # Named examples run alone; a named conftest is every example's
    # setup, so the directory runs.
    ok = str(tmp_path / "packages/x/docs/examples/ok.py")
    conftest = str(tmp_path / "packages/x/docs/examples/conftest.py")
    _python.run_examples(package, tmp_path, (ok,))
    assert fake.calls[-1][0] == (ok,)
    _python.run_examples(package, tmp_path, (ok, conftest))
    assert fake.calls[-1][0] == ("packages/x/docs/examples",)
    # The words after -- on the step's own verb follow the targets.
    _python.run_examples(package, tmp_path, (ok,), ("-x",))
    assert fake.calls[-1] == ((ok, "-x"), {})
