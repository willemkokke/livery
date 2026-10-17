"""`fm sync` installs a newly listed extension through the workspace's resolution.

Each test runs uv for real on a scratch workspace whose project file
the base renders. The workspace's index is a folder of minimal wheels,
named in its contract's registry, and uv runs offline, so nothing
reaches the network. The process running the test mounts its own
environment, so the mount's reading of what is declared is taken from
the scratch workspace's environment instead, and the handoff to a
fresh process stands in as the exit a successful handoff makes.
"""

from __future__ import annotations

import json
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import url2pathname

import pytest

from livery.footman import Failed
from workshop_wheels import write_wheel

#: The extension the workspace lists, and the distribution that ships it
#: by the base's convention for a short name.
NAME = "probe"
DIST = "livery-extensions-probe"
DECLARES = f"[workshop.extensions]\n{NAME} = livery_extensions_probe\n"

#: What the base's project file names in every workspace's dev group,
#: served by the index as empty wheels.
STOCK = (
    ("uv", "0.0.1"),
    ("livery-footman", "0.52.2"),
    ("livery-toolroom", "0.6.2"),
    ("livery-workshop", "0.0.1"),
)

_END_TABLES = "# -- workshop: end tables --"

#: The files the install writes besides the environment.
WRITTEN = ("pyproject.toml", ".workshop-rendered", "uv.lock")


def _contract(root: Path, index: str, listed: tuple[str, ...]) -> None:
    names = ", ".join(f'"{name}"' for name in listed)
    (root / "workshop.toml").write_text(
        f'[workspace]\nname = "acme"\nextensions = [{names}]\n\n'
        f'[registries]\npython = "{index}"\n'
    )


def _site_packages(root: Path) -> list[Path]:
    venv = root / ".venv"
    return [*venv.glob("lib/python*/site-packages"), *venv.glob("Lib/site-packages")]


def _declared_in(root: Path) -> Callable[[tuple[str, ...]], tuple[str, ...]]:
    """The mount's reading of what is declared, from *root*'s environment."""
    from importlib.metadata import distributions

    from livery.workshop._extensions import GROUP

    def declared_now(names: tuple[str, ...]) -> tuple[str, ...]:
        found = {
            entry.name
            for site in _site_packages(root)
            for dist in distributions(path=[str(site)])
            for entry in dist.entry_points
            if entry.group == GROUP
        }
        return tuple(name for name in names if name in found)

    return declared_now


def _installed(root: Path) -> Path | None:
    """The extension's distribution's dist-info directory in *root*'s environment."""
    module = DIST.replace("-", "_")
    found = [
        info
        for site in _site_packages(root)
        for info in site.glob(f"{module}-*.dist-info")
    ]
    return found[0] if found else None


def _workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    offered: tuple[tuple[str, str], ...],
    sourced: Path | None = None,
) -> Path:
    """A synced workspace that lists nothing yet, then lists the extension.

    The index offers *offered*, each a version and the entry points its
    wheel declares, beside the stock wheels. *sourced* is a wheel the
    project file's own region sources the extension from.
    """
    from livery.workshop import _reconcile
    from livery.workshop._e2e import write_dev_index
    from livery.workshop._shipped_files import deliver_project_file
    from livery.workshop._uv import run_uv

    for variable in (
        "VIRTUAL_ENV",
        "UV_INDEX",
        "UV_DEFAULT_INDEX",
        "UV_INDEX_URL",
        "UV_EXTRA_INDEX_URL",
        "UV_FIND_LINKS",
        "UV_NO_INDEX",
        "UV_PROJECT_ENVIRONMENT",
    ):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("UV_PYTHON", sys.executable)
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))
    wheels = tmp_path / "wheels"
    for name, version in STOCK:
        write_wheel(wheels, name, version)
    for version, entry_points in offered:
        write_wheel(wheels, DIST, version, entry_points=entry_points)
    index = write_dev_index(tmp_path / "index", sorted(wheels.glob("*.whl")))
    root = tmp_path / "acme"
    root.mkdir()
    _contract(root, index.as_uri(), ())
    deliver_project_file(root)
    if sourced is not None:
        # The repository's own source, in the region a render keeps.
        project = root / "pyproject.toml"
        text = project.read_text("utf-8")
        assert _END_TABLES in text
        source = f'[tool.uv.sources.{DIST}]\npath = "{sourced.as_posix()}"\n'
        project.write_text(text.replace(_END_TABLES, source + _END_TABLES), "utf-8")
    run_uv("sync", "--offline", root=root)
    # Listed now, and no installed distribution declares it.
    _contract(root, index.as_uri(), (NAME,))
    monkeypatch.setattr("livery.workshop._extensions.unmounted", lambda start: (NAME,))
    monkeypatch.setattr(
        "livery.workshop._extensions.stale_declarations", lambda start: ()
    )
    monkeypatch.setattr("livery.workshop._extensions.declared_now", _declared_in(root))

    def handoff(root: Path, cause: str) -> None:
        del root, cause
        raise SystemExit(0)

    monkeypatch.setattr(_reconcile, "_reexec", handoff)
    return root


def _locked(root: Path) -> dict[str, Any]:
    """The extension's distribution's entry in *root*'s ``uv.lock``."""
    lock = tomllib.loads((root / "uv.lock").read_text("utf-8"))
    return next(entry for entry in lock["package"] if entry["name"] == DIST)


def _held(root: Path) -> dict[str, bytes | None]:
    return {
        name: (root / name).read_bytes() if (root / name).is_file() else None
        for name in WRITTEN
    }


# The fallbacks first: an install that fails, and one that installs a
# distribution declaring nothing.


def test_an_install_that_fails_puts_the_project_file_and_the_lock_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop._sync import require_mounted

    # Neither a source nor the index offers the distribution.
    root = _workspace(tmp_path, monkeypatch, offered=())
    before = _held(root)
    capsys.readouterr()
    with pytest.raises(Failed) as failed:
        require_mounted(root, ("--offline",))
    # uv's own words, which name the distribution it could not find.
    assert str(failed.value).startswith("uv sync --offline exited ")
    assert DIST in str(failed.value)
    assert _held(root) == before
    out = capsys.readouterr().out
    assert "  updated pyproject.toml" in out
    assert (
        "  put back as they were before the install: pyproject.toml,"
        " .workshop-rendered" in out
    )
    assert _installed(root) is None


def test_an_install_whose_distribution_declares_nothing_refuses_and_puts_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop._sync import require_mounted

    # The index's placeholder: the distribution's name, and no extension.
    root = _workspace(tmp_path, monkeypatch, offered=(("0.0.0.dev0", ""),))
    before = _held(root)
    capsys.readouterr()
    with pytest.raises(Failed) as refused:
        require_mounted(root, ("--offline",))
    assert str(refused.value) == (
        f"no installed distribution declares {NAME} (listed in [workspace]"
        " extensions), and installing it found no distribution declaring it:"
        " install the distribution that ships each, or remove the entry."
        " Nothing was rendered or locked"
    )
    assert _held(root) == before
    assert (
        "  put back as they were before the install: pyproject.toml,"
        " .workshop-rendered, uv.lock" in capsys.readouterr().out
    )


def test_an_extension_sourced_from_a_path_installs_from_it_though_the_index_offers_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._sync import require_mounted

    local = write_wheel(tmp_path / "local", DIST, "1.0.0", entry_points=DECLARES)
    # The index offers a newer version, which a resolution that read no
    # source would take.
    root = _workspace(
        tmp_path, monkeypatch, offered=(("9.0.0", DECLARES),), sourced=local
    )
    with pytest.raises(SystemExit):
        require_mounted(root, ("--offline",))
    info = _installed(root)
    assert info is not None
    assert info.name == f"{DIST.replace('-', '_')}-1.0.0.dist-info"
    origin = json.loads((info / "direct_url.json").read_text("utf-8"))
    installed_from = Path(url2pathname(urlparse(origin["url"]).path))
    assert installed_from.resolve() == local.resolve()
    # The dev group names it, and the lock holds it from its source.
    assert f'"{DIST}",' in (root / "pyproject.toml").read_text("utf-8")
    assert Path(_locked(root)["source"]["path"]).name == local.name


def test_an_extension_from_the_index_installs_from_the_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._sync import require_mounted

    root = _workspace(tmp_path, monkeypatch, offered=(("1.2.0", DECLARES),))
    with pytest.raises(SystemExit):
        require_mounted(root, ("--offline",))
    info = _installed(root)
    assert info is not None
    assert info.name == f"{DIST.replace('-', '_')}-1.2.0.dist-info"
    # An index's wheel records no direct origin.
    assert not (info / "direct_url.json").exists()
    assert "registry" in _locked(root)["source"]


def test_a_listed_extension_s_key_in_the_root_waits_for_its_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._contract import load_contract
    from livery.workshop._sync import require_mounted

    root = _workspace(tmp_path, monkeypatch, offered=(("1.2.0", DECLARES),))
    contract = root / "workshop.toml"
    contract.write_text(contract.read_text("utf-8") + f"\n[{NAME}]\nsetting = 1\n")
    # The refusal first: the extension's own key has no declaration to
    # be judged by until the extension is installed, so a judged read
    # refuses it, as every verb but the install does.
    with pytest.raises(Failed, match=f"the top level has no key '{NAME}'"):
        load_contract(contract)
    # The install writes the project file without judging the keys, and
    # installs the distribution; the fresh process it hands over to
    # judges every contract over what is installed then.
    with pytest.raises(SystemExit):
        require_mounted(root, ("--offline",))
    assert _installed(root) is not None
    assert f'"{DIST}",' in (root / "pyproject.toml").read_text("utf-8")


# The project file alone: what the install writes before uv runs.


def test_the_project_file_is_written_alone_and_nothing_is_withdrawn(
    tmp_path: Path,
) -> None:
    from livery.workshop._fragment_engine import RENDERED_MANIFEST, read_rendered
    from livery.workshop._shipped_files import deliver_project_file

    root = tmp_path / "acme"
    root.mkdir()
    _contract(root, "https://index.acme.example/simple", ())
    assert deliver_project_file(root) == ["  wrote pyproject.toml"]
    # A file an earlier delivery wrote, which no listed extension renders
    # now: a whole delivery would withdraw it, this write keeps it.
    (root / "earlier.txt").write_text("written by an earlier delivery\n")
    receipts = read_rendered(root)
    (root / RENDERED_MANIFEST).write_text(
        json.dumps({**receipts, "earlier.txt": "0" * 64}, indent=2) + "\n"
    )
    _contract(root, "https://index.acme.example/simple", (NAME,))
    assert deliver_project_file(root) == ["  updated pyproject.toml"]
    # The dev group names the listed extension's distribution, though no
    # installed distribution declares it.
    assert f'    "{DIST}",\n' in (root / "pyproject.toml").read_text("utf-8")
    assert (root / "earlier.txt").is_file()
    assert read_rendered(root)["earlier.txt"] == "0" * 64
    assert sorted(path.name for path in root.iterdir()) == [
        RENDERED_MANIFEST,
        "earlier.txt",
        "pyproject.toml",
        "workshop.toml",
    ]


def test_an_edited_project_file_is_kept_and_named(tmp_path: Path) -> None:
    from livery.workshop._shipped_files import deliver_project_file

    root = tmp_path / "acme"
    root.mkdir()
    _contract(root, "https://index.acme.example/simple", ())
    deliver_project_file(root)
    project = root / "pyproject.toml"
    edited = project.read_text("utf-8").replace('version = "0.0.1"', 'version = "9"')
    project.write_text(edited, "utf-8")
    _contract(root, "https://index.acme.example/simple", (NAME,))
    assert deliver_project_file(root) == [
        "  kept pyproject.toml: edited here, so it is not rewritten; delete it to"
        " take the rendered file"
    ]
    assert project.read_text("utf-8") == edited
