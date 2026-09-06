"""A plugin's own global options: registration, parsing, value, completion."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Annotated

import pytest

from livery.footman import _manifest, registry
from livery.footman._complete import _DYNAMIC, _FILES, complete
from livery.footman.params import env, nosplit, suggest
from livery.footman.registry import GlobalOption, Group, RegistrationError
from livery.footman.testing import Runner

# Module level: `from __future__ import annotations` makes an annotation a
# string resolved against module globals, so a local alias never resolves.
EnvRegion = Annotated[str, env("BUILD_REGION")]


def _adopt(reg: Group, *args, **kwargs) -> GlobalOption:
    """Construct an option and move it onto *reg* — the test-local stand-in
    for the mount that carries a provider's contributions into the tree."""
    opt = GlobalOption(*args, **kwargs)
    registry.root.contributions["globals"].remove(opt)
    reg.contributions["globals"].append(opt)
    return opt


def test_a_global_option_parses_freezes_and_answers(tmp_path):
    # The real path, cascade and all: constructing the option in a tasks
    # file registers it; the leading global binds it; the task reads it.
    src = tmp_path / "tasks.py"
    src.write_text(
        textwrap.dedent(
            """
            from typing import Literal
            from livery.footman import GlobalOption, task

            MODE = GlobalOption(
                "lint-mode", Literal["loose", "strict"],
                default="loose", help="how hard to lint",
            )
            AUDIT = GlobalOption("audit", help="report, change nothing")

            @task(uses=[MODE])
            def build():
                print(f"mode={MODE.value} audit={AUDIT.value}")
            """
        )
    )
    result = Runner().invoke("--lint-mode=strict build", tasks=src, cwd=tmp_path)
    assert result.ok, result.stderr
    assert "mode=strict audit=False" in result.stdout

    result = Runner().invoke("--audit build", tasks=src, cwd=tmp_path)
    assert result.ok, result.stderr
    assert "mode=loose audit=True" in result.stdout  # defaults fill the rest


def test_a_bad_value_is_a_taught_refusal():
    reg = Group("root")
    _adopt(reg, "level", int, default=1)

    @reg.task
    def build(): ...

    result = Runner().invoke("--level=deep build", tasks=reg)
    assert not result.ok
    assert "--level" in result.stderr


def test_a_misplaced_plugin_global_is_taught_by_name():
    # A mounted global after a task name gets the same position teaching a
    # core global gets there — not the generic unknown-option shrug.
    reg = Group("root")
    _adopt(reg, "audit")

    @reg.task
    def build(): ...

    result = Runner().invoke("build --audit", tasks=reg)
    assert not result.ok
    assert "--audit is a global option" in result.stderr
    assert "before the first task name" in result.stderr
    assert "unknown option" not in result.stderr


def test_an_unpulled_option_is_an_unknown_global():
    reg = Group("root")  # nothing mounted: the name reaches no run

    @reg.task
    def build(): ...

    result = Runner().invoke("--lint-mode=strict build", tasks=reg)
    assert not result.ok
    assert "unknown global option --lint-mode" in result.stderr


def test_a_core_name_collision_is_refused_naming_the_owner():
    reg = Group("root")
    _adopt(reg, "jobs", int, default=1)

    @reg.task
    def build(): ...

    result = Runner().invoke("build", tasks=reg)
    assert not result.ok
    assert "--jobs" in result.stderr
    assert "footman's own" in result.stderr


def test_two_plugins_one_name_is_refused_naming_both():
    reg = Group("root")
    first = _adopt(reg, "cache-dir", Path)
    second = _adopt(reg, "cache-dir", Path)
    first.owner, second.owner = "acme.devkit", "other.kit"

    @reg.task
    def build(): ...

    result = Runner().invoke("build", tasks=reg)
    assert not result.ok
    assert "acme.devkit" in result.stderr and "other.kit" in result.stderr


def test_an_undeclared_read_is_noted_a_declared_one_is_not():
    reg = Group("root")
    opt = _adopt(reg, "region", str, default="eu")

    @reg.task(uses=[opt])
    def declared():
        assert opt.value == "eu"

    @reg.task
    def sneaky():
        assert opt.value == "eu"

    result = Runner().invoke("declared", tasks=reg)
    assert result.ok
    assert "uses=" not in result.stderr  # declared: no note

    result = Runner().invoke("sneaky", tasks=reg)
    assert result.ok
    assert "reads --region without declaring it" in result.stderr


def test_uses_takes_only_singletons():
    reg = Group("root")
    with pytest.raises(RegistrationError, match="GlobalOption singletons"):

        @reg.task(uses=["--region"])  # type: ignore[list-item]
        def build(): ...


def test_reading_outside_a_run_is_taught():
    reg = Group("root")
    opt = _adopt(reg, "region", str, default="eu")
    with pytest.raises(RuntimeError, match="inside a task or lifecycle hook"):
        _ = opt.value  # the read is the assertion


def test_the_manifest_bakes_globals_and_uses(tmp_path):
    reg = Group("root")
    opt = _adopt(
        reg,
        "env-file",
        Path,
        help="load this .env file",
    )
    opt.owner = "livery.footman.env_files"

    @reg.task(uses=[opt])
    def build(): ...

    data = _manifest.build_manifest(reg)
    assert data["schema"] == _manifest.SCHEMA_VERSION
    (entry,) = data["tree"]["globals"]
    assert entry["name"] == "env-file"
    assert entry["kind"] == "option"
    assert "path" in entry["types"]  # typed: completion hands off to files
    assert entry["help"] == "load this .env file"
    assert entry["owner"] == "livery.footman.env_files"
    assert data["tree"]["tasks"]["build"]["uses"] == ["env-file"]


def test_completion_offers_and_completes_plugin_globals():
    from typing import Literal

    reg = Group("root")
    _adopt(reg, "lint-mode", Literal["loose", "strict"], default="loose")
    _adopt(reg, "env-file", Path)

    @reg.task
    def build(): ...

    tree = _manifest.build_manifest(reg)["tree"]
    names = [c.split("\t", 1)[0] for c in complete(tree, ["--l"])]
    assert "--lint-mode" in names
    values = complete(tree, ["--lint-mode=s"])
    assert values == ["--lint-mode=strict"] or values == ["strict"]
    assert complete(tree, ["--env-file=x"]) == [_FILES]


def test_the_json_catalog_carries_the_globals():
    reg = Group("root")
    _adopt(reg, "audit", help="report, change nothing")

    @reg.task
    def build(): ...

    result = Runner().invoke("--json", tasks=reg)
    assert result.ok, result.stderr
    envelope = json.loads(result.stdout)
    (entry,) = envelope["tree"]["globals"]
    assert entry["name"] == "audit" and entry["kind"] == "flag"


# --- dynamic completion: a global's suggest() recomputes fresh ----------------


def _demo_targets():
    return ["prod", "preview"]


def test_a_dynamic_global_signals_recompute():
    reg = Group("root")
    _adopt(reg, "target", Annotated[str, suggest(_demo_targets)], default="")

    @reg.task
    def build(): ...

    tree = _manifest.build_manifest(reg)["tree"]
    # the value is dynamic → defer to a fresh recompute, carrying the partial,
    # the emission prefix (whole-token shells re-attach `--target=`; bash
    # completes the bare value) and the option's name — no segment path: an
    # empty path is what addresses a global.
    assert complete(tree, ["--target=p"]) == [_DYNAMIC, "p", "--target=", "target"]
    assert complete(tree, ["--target", "=", "p"]) == [_DYNAMIC, "p", "", "target"]


def test_fresh_dynamic_addresses_a_global_by_name(monkeypatch):
    import subprocess

    from livery.footman import _complete

    captured: dict[str, list[str]] = {}

    def ok(cmd, **k):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "prod\npreview\n", "")

    monkeypatch.setattr(subprocess, "run", ok)
    assert _complete._fresh_dynamic("target", [], ["--target=", ""]) == [
        "prod",
        "preview",
    ]
    cmd = captured["cmd"]
    assert cmd[cmd.index("--global") + 1] == "target"
    assert "--param" not in cmd and "--path" not in cmd


def test_suggest_answers_a_global_by_name(tmp_path, monkeypatch):
    from livery.footman import _suggest

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    (proj / "tasks.py").write_text(
        textwrap.dedent(
            """
            from pathlib import Path
            from typing import Annotated

            from livery.footman import GlobalOption, task
            from livery.footman.params import env, suggest

            def _targets():
                return Path("targets.txt").read_text().split()

            TARGET = GlobalOption(
                "target", Annotated[str, suggest(_targets)], default=""
            )
            AUDIT = GlobalOption("audit")

            @task
            def build(): ...
            """
        )
    )
    monkeypatch.chdir(proj)
    (proj / "targets.txt").write_text("prod preview\n")
    assert _suggest._global_values("target", {}) == ["prod", "preview"]
    # a miss — no completer, or no such option — is empty, never an error
    assert _suggest._global_values("audit", {}) == []
    assert _suggest._global_values("ghost", {}) == []


# --- the wiring advisories ----------------------------------------------------


def test_an_orphan_global_is_warned_and_a_wired_one_is_not():
    reg = Group("root")
    _adopt(reg, "dead-switch", help="nothing reads this")
    (warning,) = registry.orphan_global_options(reg)
    assert "--dead-switch" in warning and "no lifecycle hook" in warning

    used = _adopt(reg, "region", str, default="eu")

    @reg.task(uses=[used])
    def build(): ...

    warnings = registry.orphan_global_options(reg)
    assert len(warnings) == 1  # region is declared; dead-switch still orphaned

    def hook(inv): ...

    reg.contributions["pre_tasks"].append(hook)  # the owner now contributes
    assert registry.orphan_global_options(reg) == []


def test_an_orphan_global_warns_on_the_run():
    reg = Group("root")
    _adopt(reg, "dead-switch")

    @reg.task
    def build(): ...

    result = Runner().invoke("build", tasks=reg)
    assert result.ok, result.stderr
    assert "warning: --dead-switch" in result.stderr


def test_a_declared_unread_global_is_advised_and_teaches_the_idiom():
    # info by default: the false-positive shape (a conditional read) has a
    # one-line idiomatic fix the note itself teaches — read the declared
    # option unconditionally and branch on the value — so "never read this
    # run" only ever means a genuinely stale declaration.
    reg = Group("root")
    opt = _adopt(reg, "region", str, default="eu")

    @reg.task(uses=[opt])
    def build():
        print("built")

    result = Runner().invoke("build", tasks=reg)
    assert result.ok, result.stderr
    assert "info: task build declares --region in uses=" in result.stderr
    assert "read it unconditionally and branch on the value" in result.stderr


def test_a_declared_and_read_global_is_not_advised():
    reg = Group("root")
    opt = _adopt(reg, "region", str, default="eu")

    @reg.task(uses=[opt])
    def build():
        assert opt.value == "eu"

    result = Runner().invoke("--verbose build", tasks=reg)
    assert result.ok, result.stderr
    assert "never read it" not in result.stderr


# --- help shows the declaration ----------------------------------------------


def test_task_help_lists_declared_globals():
    reg = Group("root")
    opt = _adopt(reg, "region", str, default="eu")
    opt.owner = "acme.devkit"

    @reg.task(uses=[opt])
    def deploy(): ...

    result = Runner().invoke("--help deploy", tasks=reg)
    assert result.ok, result.stderr
    assert "reads --region (from acme.devkit)" in result.stdout


# --- bare mentions, presence, and the absent ladder -------------------------


def test_a_bare_mention_is_presence_not_a_second_declared_value():
    reg = Group("root")
    opt = _adopt(reg, "out", Path, default="o.json")

    @reg.task(uses=[opt])
    def build():
        print(f"out={opt.value} given={opt.given}")

    # Three outcomes from ONE declared value, which is what retired `bare=`:
    # absent, named, named with a value.
    absent = Runner().invoke("build", tasks=reg)
    assert "out=o.json given=False" in absent.stdout

    bare = Runner().invoke("--out build", tasks=reg)
    assert "out=o.json given=True" in bare.stdout

    attached = Runner().invoke("--out=x.json build", tasks=reg)
    assert "out=x.json given=True" in attached.stdout


def test_a_flag_reads_as_given_when_it_is_named():
    reg = Group("root")
    opt = _adopt(reg, "audit", bool)

    @reg.task(uses=[opt])
    def build():
        print(f"audit={opt.value} given={opt.given}")

    assert "audit=False given=False" in Runner().invoke("build", tasks=reg).stdout
    assert "audit=True given=True" in Runner().invoke("--audit build", tasks=reg).stdout


def test_a_global_option_honours_its_env_fallback(monkeypatch):
    # The marker reached the manifest and was never applied, so help would have
    # advertised a fallback that did not happen.
    monkeypatch.setenv("BUILD_REGION", "us")
    reg = Group("root")
    opt = _adopt(reg, "region", EnvRegion, default="eu")

    @reg.task(uses=[opt])
    def build():
        print(f"region={opt.value} given={opt.given}")

    # The environment supplies the value and claims nothing about presence.
    assert "region=us given=False" in Runner().invoke("build", tasks=reg).stdout
    attached = Runner().invoke("--region=ap build", tasks=reg)
    assert "region=ap given=True" in attached.stdout


def test_a_word_after_a_bare_mention_teaches_attachment():
    reg = Group("root")
    _adopt(reg, "out", Path, default="o.json")

    @reg.task
    def build(): ...

    result = Runner().invoke("--out o.json build", tasks=reg)
    assert not result.ok
    assert "--out=o.json" in result.stderr


def test_a_collection_global_accumulates_like_a_task_option():
    # `--tag=a --tag=b` and `--tag=a,b` are one list: mentions accumulate and
    # each value comma-splits, the reading every task option already has.
    # These used to bind last-mention-wins with the commas kept — a latent
    # bug the bare-mentions note recorded, not a decision.
    reg = Group("root")
    opt = _adopt(reg, "tag", list[str])

    @reg.task(uses=[opt])
    def build():
        print(f"tags={opt.value} given={opt.given}")

    repeated = Runner().invoke("--tag=a --tag=b build", tasks=reg)
    assert repeated.ok, repeated.stderr
    assert "tags=['a', 'b'] given=True" in repeated.stdout
    joined = Runner().invoke("--tag=a,b build", tasks=reg)
    assert "tags=['a', 'b']" in joined.stdout
    absent = Runner().invoke("build", tasks=reg)
    assert "tags=None given=False" in absent.stdout


def test_nosplit_keeps_a_collection_globals_commas():
    reg = Group("root")
    opt = _adopt(reg, "header", Annotated[list[str], nosplit])

    @reg.task(uses=[opt])
    def build():
        print(f"headers={opt.value}")

    result = Runner().invoke("--header=a,b --header=c build", tasks=reg)
    assert result.ok, result.stderr
    assert "headers=['a,b', 'c']" in result.stdout


def test_a_mapping_global_binds_pairs_and_teaches():
    reg = Group("root")
    opt = _adopt(reg, "define", dict[str, int])

    @reg.task(uses=[opt])
    def build():
        print(f"defines={opt.value}")

    result = Runner().invoke("--define=k=1 --define=j=2,i=3 build", tasks=reg)
    assert result.ok, result.stderr
    assert "defines={'k': 1, 'j': 2, 'i': 3}" in result.stdout

    pairless = Runner().invoke("--define=k build", tasks=reg)
    assert not pairless.ok
    assert "KEY=VALUE" in pairless.stderr
    badvalue = Runner().invoke("--define=k=deep build", tasks=reg)
    assert not badvalue.ok
    assert "--define value" in badvalue.stderr and "'deep'" in badvalue.stderr


def test_a_repeated_scalar_global_is_last_wins():
    # Not a collection: repeating a scalar means the later mention, exactly
    # as a repeated task option reads.
    reg = Group("root")
    opt = _adopt(reg, "level", int, default=1)

    @reg.task(uses=[opt])
    def build():
        print(f"level={opt.value}")

    result = Runner().invoke("--level=2 --level=3 build", tasks=reg)
    assert result.ok, result.stderr
    assert "level=3" in result.stdout


def test_a_bool_global_answers_to_no_x():
    # The negation every task flag already has: `--no-audit` is off out
    # loud — given, value False — and last mention wins between spellings.
    reg = Group("root")
    opt = _adopt(reg, "audit")

    @reg.task(uses=[opt])
    def build():
        print(f"audit={opt.value} given={opt.given}")

    off = Runner().invoke("--no-audit build", tasks=reg)
    assert off.ok, off.stderr
    assert "audit=False given=True" in off.stdout
    last = Runner().invoke("--audit --no-audit build", tasks=reg)
    assert "audit=False given=True" in last.stdout
    relit = Runner().invoke("--no-audit --audit build", tasks=reg)
    assert "audit=True given=True" in relit.stdout


def test_a_negation_completes_beside_its_flag():
    reg = Group("root")
    _adopt(reg, "audit")
    _adopt(reg, "level", int, default=1)  # value-taking: no negation offered

    @reg.task
    def build(): ...

    tree = _manifest.build_manifest(reg)["tree"]
    names = [c.split("\t", 1)[0] for c in complete(tree, ["--"])]
    assert "--audit" in names and "--no-audit" in names
    # A valued global offers both spellings and no `--no-` (that is a flag's
    # off switch); a flag offers `--no-` and no `=`.
    assert "--level" in names and "--level=" in names
    assert "--no-level" not in names and "--audit=" not in names


def test_a_literal_no_x_beside_a_bool_x_is_refused():
    # A bool claims its off spelling, so the clash is loud at discovery
    # instead of order-dependent at parse.
    reg = Group("root")
    flag = _adopt(reg, "verify")
    literal = _adopt(reg, "no-verify")
    flag.owner, literal.owner = "acme.devkit", "other.kit"

    @reg.task
    def build(): ...

    result = Runner().invoke("build", tasks=reg)
    assert not result.ok
    assert "--no-verify" in result.stderr
    assert "acme.devkit" in result.stderr and "other.kit" in result.stderr


_SECTIONED = """
import livery.footman as footman
from livery.footman import GlobalOption, task

footman.config_section("devkit")
REGION = GlobalOption("region", str, default="eu", config=True)

@task(uses=[REGION])
def build():
    print(f"region={REGION.value} given={REGION.given}")
"""


def test_a_config_backed_global_reads_its_section(tmp_path):
    # The one ladder's config rung, from the provider's own section under
    # the reserved `plugins.` child — and config-sourced is not `given`,
    # exactly as env-sourced is not.
    (tmp_path / "tasks.py").write_text(_SECTIONED)
    (tmp_path / "pyproject.toml").write_text(
        "[tool.footman.plugins.devkit]\nregion = 'us'\n"
    )
    result = Runner().invoke("build", tasks=tmp_path / "tasks.py", cwd=tmp_path)
    assert result.ok, result.stderr
    assert "region=us given=False" in result.stdout
    cli = Runner().invoke(
        "--region=ap build", tasks=tmp_path / "tasks.py", cwd=tmp_path
    )
    assert "region=ap given=True" in cli.stdout  # the line outranks config


def test_env_outranks_config_for_a_global(tmp_path, monkeypatch):
    # An exported variable aims at this invocation, a project setting at
    # every invocation — the specific beats the general.
    (tmp_path / "tasks.py").write_text(
        textwrap.dedent(
            """
            import livery.footman as footman
            from typing import Annotated
            from livery.footman import GlobalOption, task
            from livery.footman.params import env

            footman.config_section("devkit")
            REGION = GlobalOption(
                "region", Annotated[str, env("BUILD_REGION")],
                default="eu", config=True,
            )

            @task(uses=[REGION])
            def build():
                print(f"region={REGION.value}")
            """
        )
    )
    (tmp_path / "pyproject.toml").write_text(
        "[tool.footman.plugins.devkit]\nregion = 'us'\n"
    )
    monkeypatch.setenv("BUILD_REGION", "ap")
    result = Runner().invoke("build", tasks=tmp_path / "tasks.py", cwd=tmp_path)
    assert result.ok, result.stderr
    assert "region=ap" in result.stdout


def test_a_broken_plugin_config_value_teaches_with_the_keys_address(tmp_path):
    (tmp_path / "tasks.py").write_text(
        textwrap.dedent(
            """
            import livery.footman as footman
            from livery.footman import GlobalOption, task

            footman.config_section("devkit")
            LEVEL = GlobalOption("level", int, default=1, config=True)

            @task(uses=[LEVEL])
            def build():
                print(LEVEL.value)
            """
        )
    )
    (tmp_path / "pyproject.toml").write_text(
        "[tool.footman.plugins.devkit]\nlevel = 'deep'\n"
    )
    result = Runner().invoke("build", tasks=tmp_path / "tasks.py", cwd=tmp_path)
    assert not result.ok
    assert "config key 'plugins.devkit.level'" in result.stderr
    # Validated even when the line decides — the sort rule, for plugins too.
    still = Runner().invoke(
        "--level=2 build", tasks=tmp_path / "tasks.py", cwd=tmp_path
    )
    assert not still.ok
    assert "config key 'plugins.devkit.level'" in still.stderr


def test_the_section_derives_from_the_pulled_entry_point():
    # `acme.devkit` de-dots to `acme-devkit` — the mount stamps the identity,
    # the derivation reads it, and `config="key"` renames one option's key.
    reg = Group("root")
    opt = _adopt(reg, "region", str, default="eu", config="zone")
    opt._mounted = "acme.devkit"

    @reg.task(uses=[opt])
    def build():
        print(f"region={opt.value}")

    result = Runner().invoke("build", tasks=reg)
    assert result.ok, result.stderr
    assert opt._section == "acme-devkit"


def test_config_without_a_section_source_is_refused():
    reg = Group("root")
    _adopt(reg, "region", str, default="eu", config=True)

    @reg.task
    def build(): ...

    result = Runner().invoke("build", tasks=reg)
    assert not result.ok
    assert "nothing names its section" in result.stderr
    assert "config_section" in result.stderr


def test_config_through_two_pulls_is_refused():
    reg = Group("root")
    opt = _adopt(reg, "region", str, default="eu", config=True)
    opt._mounted = registry._MANY_MOUNTS

    @reg.task
    def build(): ...

    result = Runner().invoke("build", tasks=reg)
    assert not result.ok
    assert "more than one mount" in result.stderr


def test_two_providers_one_section_is_refused():
    # `acme.devkit` and `acme-devkit` de-dot to one section: loud at
    # discovery naming both, never resolved by mount order.
    reg = Group("root")
    first = _adopt(reg, "region", str, default="eu", config=True)
    second = _adopt(reg, "zone", str, default="eu", config=True)
    first.owner, second.owner = "acme.devkit", "other.kit"
    first._mounted, second._mounted = "acme.devkit", "acme-devkit"

    @reg.task
    def build(): ...

    result = Runner().invoke("build", tasks=reg)
    assert not result.ok
    assert "plugins.acme-devkit" in result.stderr
    assert "acme.devkit" in result.stderr and "other.kit" in result.stderr


def test_global_help_lists_plugin_globals_with_provenance():
    # `fm --help` claims to list the globals; a mounted plugin's ride the
    # same pre-task position, so the page must list them too — under their
    # own heading, with where each came from.
    reg = Group("root")
    opt = _adopt(reg, "env-file", Path, help="load this .env file")
    opt.owner = "livery.footman.env_files"

    @reg.task(uses=[opt])
    def build(): ...

    result = Runner().invoke("--help", tasks=reg)
    assert result.ok, result.stderr
    assert "plugin globals (before the first task):" in result.stdout
    assert "--env-file" in result.stdout
    assert "load this .env file" in result.stdout
    assert "from livery.footman.env_files" in result.stdout


def test_global_help_without_plugin_globals_has_no_plugin_heading():
    reg = Group("root")

    @reg.task
    def build(): ...

    result = Runner().invoke("--help", tasks=reg)
    assert result.ok, result.stderr
    assert "plugin globals" not in result.stdout
