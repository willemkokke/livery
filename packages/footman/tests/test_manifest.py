"""Signature introspection, manifest caching, and staleness."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, Literal, Optional

import pytest

from livery.footman import _manifest, _paths, compose, registry
from livery.footman.params import doc


def specs(fn):
    sig = _manifest.resolved_signature(fn)
    return [_manifest.param_spec(p) for p in sig.parameters.values()]


def test_flag():
    def f(x: bool = False): ...

    assert specs(f) == [{"name": "x", "default": False, "kind": "flag"}]


def test_a_basic_default_types_an_unannotated_parameter():
    """An unannotated `port=8000` records `int` — which is what buys the
    eager refusal, the catalog's declared type, and the coercion at bind
    time. `str` is omitted like every other str-tagged spec: it is the
    shape a command-line value already has."""

    def f(port=8000, ratio=1.5, name="app"): ...

    assert specs(f) == [
        {"name": "port", "default": 8000, "kind": "option", "types": ["int"]},
        {"name": "ratio", "default": 1.5, "kind": "option", "types": ["float"]},
        {"name": "name", "default": "app", "kind": "option"},
    ]


def test_inference_declines_where_a_type_checker_declines():
    """`None` and containers carry no inferred type, so they behave as they
    did before inference: the raw string reaches the body."""

    def f(target, out=None, paths=(), flag=False): ...

    assert specs(f) == [
        {"name": "target", "kind": "positional"},
        {"name": "out", "default": None, "kind": "option"},
        {"name": "paths", "default": [], "kind": "option"},
        {"name": "flag", "default": False, "kind": "flag"},
    ]


def test_doc_marker_lands_in_spec():
    def f(fix: Annotated[bool, doc("apply fixes in place")] = False): ...

    assert specs(f) == [
        {"name": "fix", "default": False, "kind": "flag", "doc": "apply fixes in place"}
    ]


def node(fn):
    """Build one task's manifest node the way the real tree does."""
    from livery.footman import registry

    with registry.capture() as root:
        registry.task(fn)
    return _manifest.build_manifest(root)["tree"]["tasks"][
        fn.__name__.replace("_", "-")
    ]


def test_docstring_params_fill_doc():
    def deploy(target: str, fix: bool = False):
        """Deploy.

        Args:
            target: where to deploy
            fix: apply fixes in place
        """

    by_name = {p["name"]: p for p in node(deploy)["params"]}
    assert by_name["target"]["doc"] == "where to deploy"
    assert by_name["fix"]["doc"] == "apply fixes in place"


def test_doc_marker_beats_docstring():
    def lint(fix: Annotated[bool, doc("the marker text")] = False):
        """Lint.

        Args:
            fix: the docstring text
        """

    (p,) = node(lint)["params"]
    assert p["doc"] == "the marker text"


def test_docstring_long_lands_in_node():
    def build():
        """Build.

        The long story,
        over two lines.
        """

    n = node(build)
    assert n["help"] == "Build."
    assert n["long"] == "The long story,\nover two lines."


def test_docstring_long_absent_when_empty():
    def plain():
        "Just the one line."

    assert "long" not in node(plain)


def test_docstring_unknown_param_warns(capsys):
    def run_it(a: int = 0):
        """Run.

        Args:
            a: real
            ghost: not a parameter
        """

    _manifest._warned.clear()
    node(run_it)
    err = capsys.readouterr().err
    assert "ghost" in err
    assert "UserWarning" not in err  # one clean line, not Python's dressing


def test_numpy_and_sphinx_docstrings_reach_the_spec():
    def np_style(a: int = 0):
        """S.

        Parameters
        ----------
        a : int
            From numpy.
        """

    def sp_style(a: int = 0):
        """S.

        :param a: from sphinx
        """

    assert node(np_style)["params"][0]["doc"] == "From numpy."
    assert node(sp_style)["params"][0]["doc"] == "from sphinx"


def test_kwargs_is_a_spec_error():
    def f(**opts): ...

    with pytest.raises(_manifest.SpecError, match=r"\*\*opts"):
        specs(f)


def test_help_option_is_a_reserved_name():
    # A flag or option named `help` would map to --help, which footman
    # intercepts anywhere on the line — so it can never bind. Reject it loudly
    # at load time rather than silently shadowing the parameter.
    def with_flag(help: bool = False): ...

    def with_option(help: str = ""): ...

    for fn in (with_flag, with_option):
        with pytest.raises(_manifest.SpecError, match=r"'help' is a reserved"):
            node(fn)


def test_help_is_allowed_when_it_is_not_an_option():
    # A required positional <help> and a variadic *help never produce --help,
    # so they stay legal — the reservation is precise, not a blanket ban.
    def positional(help: str): ...

    def variadic(*help: str): ...

    assert node(positional)["params"][0]["kind"] == "positional"
    assert node(variadic)["params"][0]["kind"] == "variadic"


def test_no_default_dict_is_a_required_option():
    def f(vars: dict[str, str]): ...

    assert specs(f) == [
        {"name": "vars", "kind": "option", "mapping": True, "required": True}
    ]


def test_no_default_bool_is_a_required_flag():
    def f(prod: bool): ...

    assert specs(f) == [{"name": "prod", "kind": "flag", "required": True}]


def test_str_option_and_required_argument():
    def g(opt: str = "a"): ...

    def h(req): ...

    assert specs(g) == [{"name": "opt", "default": "a", "kind": "option"}]
    assert specs(h) == [{"name": "req", "kind": "positional"}]


def test_keyword_only_without_default_is_a_required_option():
    # Python's `*` already says "must be named" — the grammar honours it,
    # the same shape defaultless dicts and flags take.
    def f(*, out: Path): ...

    def g(*args: str, dest: str): ...

    def h(*, plain): ...  # un-annotated keyword-only: same rule

    assert specs(f) == [
        {"name": "out", "kind": "option", "required": True, "types": ["path"]}
    ]
    assert specs(g)[1] == {"name": "dest", "kind": "option", "required": True}
    assert specs(h) == [{"name": "plain", "kind": "option", "required": True}]


def test_keyword_only_with_default_stays_a_plain_option():
    def f(*args: str, title: str = ""): ...

    assert specs(f)[1] == {"name": "title", "default": "", "kind": "option"}


def test_typed_option():
    def f(n: int = 3, ratio: float = 1.0): ...

    assert specs(f) == [
        {"name": "n", "default": 3, "kind": "option", "types": ["int"]},
        {"name": "ratio", "default": 1.0, "kind": "option", "types": ["float"]},
    ]


def test_literal_choices_positional():
    def f(env: Literal["a", "b"]): ...

    assert specs(f) == [{"name": "env", "kind": "positional", "choices": ["a", "b"]}]


def test_repeatable_path_option():
    def f(paths: list[Path] | None = None): ...

    assert specs(f) == [
        {
            "name": "paths",
            "default": None,
            "multiple": True,
            "kind": "option",
            "types": ["path"],
        }
    ]


def test_variadic():
    def f(*cmd: str): ...

    assert specs(f) == [{"name": "cmd", "kind": "variadic"}]


def test_underscore_becomes_hyphen():
    def f(fail_under: int = 80): ...

    assert specs(f)[0]["name"] == "fail-under"


def test_optional_is_unwrapped():
    def f(x: Optional[int] = None): ...  # noqa: UP045 - exercises typing.Optional

    def g(y: int | None = None): ...

    assert specs(f) == [
        {"name": "x", "default": None, "kind": "option", "types": ["int"]}
    ]
    assert specs(g) == [
        {"name": "y", "default": None, "kind": "option", "types": ["int"]}
    ]


def test_build_manifest_shape(tree):
    assert "check" in tree["tasks"]
    assert set(tree["groups"]) >= {"docs", "db", "docker", "workspace"}
    lint = tree["tasks"]["lint"]
    assert lint["help"] == "Run ruff over the project."
    kinds = {p["name"]: p["kind"] for p in lint["params"]}
    assert kinds == {"fix": "flag", "mode": "option", "paths": "option"}


def test_footman_cache_dir_overrides_every_cache_path(tmp_path, monkeypatch):
    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "elsewhere"))
    assert _paths.manifest_path(tmp_path).parent == tmp_path / "elsewhere"
    assert _paths.times_path(tmp_path).parent == tmp_path / "elsewhere"
    assert _paths.times_path(tmp_path).name.endswith(".times.json")


def _provider() -> registry.Group:
    g = registry.Group("lint")

    def strict() -> None:
        """Be strict."""

    def _default() -> None:
        """Lint everything."""

    g.tasks["strict"] = registry.task(strict)
    g.tasks["default"] = registry.task(_default)
    return g


def test_manifest_task_rows_carry_the_provider():
    # Ownership as a provider identity, not a source path: a path answers
    # only for a Python function and makes the reader infer ownership from
    # prefixes. This names the owner outright.
    with registry.capture() as root:
        compose.include(_provider())
        tree = _manifest.build_manifest(root)["tree"]
    lint = tree["groups"]["lint"]
    assert lint["tasks"]["strict"]["mounted_from"] == "lint"
    # The default is a task named `default`, so it carries it like any other
    # — through both spellings the manifest emits it under.
    assert lint["tasks"]["default"]["mounted_from"] == "lint"
    assert lint["default"]["mounted_from"] == "lint"
    # Grafted onto a free name, so the whole group is the provider's.
    assert lint["mounted_from"] == "lint"


def test_a_shared_group_claims_no_provider_while_its_tasks_still_do():
    # The asymmetry a consumer will otherwise misread. A mount composing
    # into a group that already exists leaves the *destination* group in
    # place — shared, so it has no single owner and says so — while every
    # task under it still answers exactly. Ownership is read off task rows.
    with registry.capture() as root:
        local = registry.group("lint")

        @local.task
        def fast() -> None:
            """Quick pass."""

        compose.include(_provider())
        tree = _manifest.build_manifest(root)["tree"]
    lint = tree["groups"]["lint"]
    assert "mounted_from" not in lint  # mixed provenance: no single owner
    assert lint["tasks"]["strict"]["mounted_from"] == "lint"  # exact, per task
    assert "mounted_from" not in lint["tasks"]["fast"]  # locally written


def test_a_task_the_project_writes_claims_no_provider(tree):
    # Omitted, not null: the project owns what its own tasks file defines.
    assert all("mounted_from" not in t for t in tree["tasks"].values())


def test_write_load_roundtrip(root, tmp_path):
    m = _manifest.build_manifest(root)
    path = tmp_path / "manifest.json"
    _manifest.write_manifest(m, path)
    assert _manifest.load_manifest(path) == m


def test_write_retries_when_a_reader_holds_the_destination(root, tmp_path, monkeypatch):
    """Windows refuses to replace a file another process has open, and a
    reader holding it open is the design: completion polls this manifest
    every few milliseconds while a detached refresh rewrites it. Losing that
    race silently means the rebuild never lands (the child swallows its
    errors), so the write retries instead."""
    m = _manifest.build_manifest(root)
    path = tmp_path / "manifest.json"
    real_replace = os.replace
    denials = [PermissionError(5, "being used by another process")] * 3

    def flaky(src, dst):
        if denials:
            raise denials.pop()
        return real_replace(src, dst)

    monkeypatch.setattr(_manifest, "_REPLACE_PAUSE", 0)
    monkeypatch.setattr(os, "replace", flaky)
    _manifest.write_manifest(m, path)
    assert _manifest.load_manifest(path) == m
    assert not denials  # every denial was actually met with a retry


def test_write_gives_up_without_littering_the_cache(root, tmp_path, monkeypatch):
    """A reader that never lets go must not leave `<name>.<pid>.tmp` behind:
    nothing ever reads those, and the cache directory is not a graveyard."""
    m = _manifest.build_manifest(root)
    path = tmp_path / "manifest.json"

    def always_denied(src, dst):
        raise PermissionError(5, "being used by another process")

    monkeypatch.setattr(_manifest, "_REPLACE_PAUSE", 0)
    monkeypatch.setattr(os, "replace", always_denied)
    with pytest.raises(PermissionError):
        _manifest.write_manifest(m, path)
    assert list(tmp_path.glob("*.tmp")) == []


def test_load_missing_or_corrupt_returns_none(tmp_path):
    assert _manifest.load_manifest(tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert _manifest.load_manifest(bad) is None


def test_sync_rewrites_only_on_hash_change(root, tmp_path, monkeypatch):
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path)
    project = tmp_path / "proj"
    project.mkdir()

    writes: list[Path] = []
    real_write = _manifest.write_manifest

    def fake_write_manifest(m, p):
        writes.append(p)
        return real_write(m, p)

    monkeypatch.setattr(_manifest, "write_manifest", fake_write_manifest)

    _manifest.sync_manifest(root, project)
    _manifest.sync_manifest(root, project)  # identical tree -> no rewrite
    assert len(writes) == 1

    @root.task
    def brand_new_task():  # changes the hash
        """A new task."""

    _manifest.sync_manifest(root, project)
    assert len(writes) == 2


def test_sync_rewrites_when_only_the_user_stamp_moved(root, tmp_path, monkeypatch):
    # The stamp has to join the write guard, not just the payload: a rebuild
    # triggered by a user-level edit would otherwise find the tree unchanged,
    # write nothing, leave the OLD stamp on disk — and every later TAB would
    # see the same mismatch and spawn another rebuild, forever.
    import json

    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path)
    config = tmp_path / "user.toml"
    monkeypatch.setenv("FOOTMAN_CONFIG", str(config))
    project = tmp_path / "proj"
    project.mkdir()

    _manifest.sync_manifest(root, project)
    path = _paths.manifest_path(project)
    first = json.loads(path.read_text(encoding="utf-8"))["user_stamp"]

    config.write_text("[tool.footman]\nsort = true\n", encoding="utf-8")
    _manifest.sync_manifest(root, project)  # same tree, moved stamp
    second = json.loads(path.read_text(encoding="utf-8"))["user_stamp"]
    assert second != first  # the file was rewritten, and says so


def test_a_schema_bump_rewrites_an_unchanged_tree(root, tmp_path, monkeypatch):
    # An upgrade bumps the schema while the tree (and so the hash) stands
    # still. The rewrite guard compared only the hash, so the old-schema
    # file lived forever: every TAB refused it, spawned a rebuild that
    # "succeeded" without writing, and paid the full cold bound — in every
    # directory on the machine, on every keystroke, until the tree changed.
    import json

    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path)
    project = tmp_path / "proj"
    project.mkdir()
    _manifest.sync_manifest(root, project)
    path = _paths.manifest_path(project)
    aged = json.loads(path.read_text(encoding="utf-8"))
    aged["schema"] = _manifest.SCHEMA_VERSION - 1  # yesterday's footman wrote it
    path.write_text(json.dumps(aged), encoding="utf-8")

    _manifest.sync_manifest(root, project)  # same tree, same hash
    now = json.loads(path.read_text(encoding="utf-8"))
    assert now["schema"] == _manifest.SCHEMA_VERSION


def test_sync_bakes_the_cwd_and_upgrades_manifests_without_it(
    root, tmp_path, monkeypatch
):
    # The collector's gone-directory rule reads it; pre-existing manifests
    # lacking the key are rewritten once so they gain it.
    monkeypatch.setattr(_paths, "cache_home", lambda: tmp_path)
    project = tmp_path / "proj"
    project.mkdir()
    _manifest.sync_manifest(root, project)
    path = _paths.manifest_path(project)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["cwd"] == str(project)

    del data["cwd"]  # simulate a manifest from before the key existed
    path.write_text(json.dumps(data), encoding="utf-8")
    _manifest.sync_manifest(root, project)  # same tree, but cwd-less: rewrite
    fresh = json.loads(path.read_text(encoding="utf-8"))
    assert fresh["cwd"] == str(project)


def test_infinite_task_carries_the_note_key():
    from livery.footman import registry

    with registry.capture() as root:

        @registry.task(infinite=True)
        def serve():
            "Serve forever."

        @registry.task
        def plain():
            "Ends."

    tree = _manifest.build_manifest(root)["tree"]
    assert tree["tasks"]["serve"]["infinite"] is True
    assert "infinite" not in tree["tasks"]["plain"]  # additive: absent means no


def test_confirm_and_interactive_carry_note_keys():
    from livery.footman import registry

    with registry.capture() as root:

        @registry.task(confirm="to prod?", interactive=True)
        def deploy():
            "Deploy."

        @registry.task
        def plain():
            "Plain."

    tree = _manifest.build_manifest(root)["tree"]
    assert tree["tasks"]["deploy"]["interactive"] is True
    assert tree["tasks"]["deploy"]["confirm"] == "to prod?"
    assert "interactive" not in tree["tasks"]["plain"]
    assert "confirm" not in tree["tasks"]["plain"]


def test_serial_and_exclusive_carry_the_lane_key():
    from livery.footman import registry

    with registry.capture() as root:

        @registry.task(serial=True)
        def legacy():
            "Owns the globals."

        @registry.task(exclusive=True)
        def bench():
            "Owns the machine."

        @registry.task
        def plain():
            "Plain."

    tree = _manifest.build_manifest(root)["tree"]
    assert tree["tasks"]["legacy"]["lane"] == "serial"
    assert tree["tasks"]["bench"]["lane"] == "exclusive"
    assert "lane" not in tree["tasks"]["plain"]


def test_one_broken_annotation_degrades_only_its_parameter(capsys):
    # `eval_str` is all-or-nothing, so the fallback resolves per parameter:
    # the typo'd one passes through as text, the sibling keeps its type —
    # previously one broken name cost the whole task its grammar.
    def f(x="d", flag: bool = False): ...

    f.__annotations__ = {"x": "NoSuchType", "flag": "bool"}  # PEP-563 strings
    _manifest._warned.clear()
    by_name = {s["name"]: s for s in specs(f)}
    err = capsys.readouterr().err
    assert "<x>" in err and "'NoSuchType' did not resolve" in err
    assert "UserWarning" not in err
    assert by_name["flag"]["kind"] == "flag"  # the sibling survived
    assert "types" not in by_name["x"]  # the broken one degraded to text
    assert "choices" not in by_name["x"]


def test_the_broken_annotation_warning_names_task_and_cause(capsys):
    def deploy(tier="post"): ...

    # What ruff once made of a quote-stripped Literal: a subtraction of two
    # names, which is exactly as unresolvable as a typo.
    deploy.__annotations__ = {"tier": "post - merge"}
    _manifest._warned.clear()
    specs(deploy)
    err = capsys.readouterr().err
    assert "deploy" in err and "<tier>" in err and "name 'post'" in err


def test_a_broken_return_annotation_degrades_alone(capsys):
    def f(flag: bool = False): ...

    f.__annotations__ = {"flag": "bool", "return": "Gone"}
    _manifest._warned.clear()
    sig = _manifest.resolved_signature(f)
    assert "<return>" in capsys.readouterr().err
    assert sig.parameters["flag"].annotation is bool
    assert sig.return_annotation == "Gone"
