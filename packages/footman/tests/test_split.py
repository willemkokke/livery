"""The chain grammar: separator-free splitting, arity, validation, errors."""

from __future__ import annotations

from typing import NamedTuple

import pytest

from livery.footman import _manifest
from livery.footman._split import ChainError, split_chain
from livery.footman.registry import Group


class Box(NamedTuple):
    width: int
    height: int


class Tag(NamedTuple):
    # not `count`: that name is a tuple method, and the checkers refuse the
    # override
    copies: int
    text: str


def build_tree(build):
    reg = Group("root")
    build(reg)
    return reg, _manifest.build_manifest(reg)["tree"]


def segs(tree, line):
    _, segments = split_chain(tree, line.split())
    return segments


def globs(tree, line):
    globals_, _ = split_chain(tree, line.split())
    return globals_


def test_single_task(tree):
    (seg,) = segs(tree, "check")
    assert seg.task == "check"
    assert seg.values == {}
    assert seg.variadic == []
    assert seg.passthrough is None


def test_flags_and_options_split_into_segments(tree):
    result = segs(tree, "format --fix lint --fix --mode=strict typecheck test")
    assert [s.task for s in result] == ["format", "lint", "typecheck", "test"]
    assert result[0].values == {"fix": True}
    assert result[1].values == {"fix": True, "mode": "strict"}
    assert result[2].values == {}
    assert result[3].values == {}


def test_repeated_option_collects_a_list(tree):
    (seg,) = segs(
        tree, "test --marker=slow --path=tests/unit --path=tests/e2e --coverage"
    )
    assert seg.values == {
        "marker": "slow",
        "path": ["tests/unit", "tests/e2e"],
        "coverage": True,
    }


def test_leading_globals(tree):
    assert globs(tree, "-k -q format lint test") == ["--keep-going", "--quiet"]
    assert [s.task for s in segs(tree, "-k -q format lint test")] == [
        "format",
        "lint",
        "test",
    ]


def test_interactivity_globals(tree):
    # --yes / --no-input parse as leading globals; -y canonicalises to --yes.
    assert globs(tree, "-y --no-input format") == ["--yes", "--no-input"]
    assert [s.task for s in segs(tree, "-y --no-input format")] == ["format"]


def test_color_global_takes_a_value(tree):
    # --color is a valued global; --no-color stays a bare flag beside it.
    assert globs(tree, "--color=always format") == ["--color=always"]
    assert globs(tree, "--color=never format") == ["--color=never"]
    assert globs(tree, "--no-color format") == ["--no-color"]
    assert [s.task for s in segs(tree, "--color=always format")] == ["format"]


def test_dotted_address_and_typed_option(tree):
    (seg,) = segs(tree, "docs.serve --port=8001")
    assert seg.task == "docs.serve"
    assert seg.path == ["docs", "serve"]
    assert seg.values == {"port": "8001"}


def test_required_positional_then_option(tree):
    a, b = segs(tree, "docs.build --strict deploy staging --version=2026.07.16")
    assert a.task == "docs.build"
    assert a.values == {"strict": True}
    assert b.task == "deploy"
    assert b.values == {"env": "staging", "version": "2026.07.16"}


def test_two_required_positionals_repeated_task(tree):
    a, b = segs(
        tree,
        "render templates/report.j2 out/report.html "
        "render templates/index.j2 out/index.html",
    )
    assert a.values == {"template": "templates/report.j2", "output": "out/report.html"}
    assert b.values == {"template": "templates/index.j2", "output": "out/index.html"}


def test_explicit_plus_boundary_before_variadic(tree):
    a, b = segs(tree, "deps.add requests rich typer + lint --fix")
    assert a.task == "deps.add"
    assert a.variadic == ["requests", "rich", "typer"]
    assert b.task == "lint"
    assert b.values == {"fix": True}


def test_variadic_consumes_rest_of_segment(tree):
    (seg,) = segs(tree, "run ruff check src")
    assert seg.variadic == ["ruff", "check", "src"]


def test_passthrough_is_terminal(tree):
    (seg,) = segs(tree, "test --marker=unit -- -k manifest_or_split -x")
    assert seg.values == {"marker": "unit"}
    assert seg.passthrough == ["-k", "manifest_or_split", "-x"]


def test_no_flag_negation(tree):
    (seg,) = segs(tree, "docs.serve --no-live")
    assert seg.values == {"live": False}


def test_option_equals_form(tree):
    (seg,) = segs(tree, "lint --mode=strict")
    assert seg.values == {"mode": "strict"}


def test_a_bare_mention_records_presence_and_no_value(tree):
    # `--mode` alone is legal wherever absence is: it carries no value, so the
    # binder runs the same ladder it would with no mention at all. What it adds
    # is that someone asked, which `given()` reads.
    (seg,) = segs(tree, "lint --mode")
    assert seg.values == {}
    assert seg.bare == {"mode"}


def test_a_bare_mention_before_a_passthrough(tree):
    (seg,) = segs(tree, "lint --mode -- x")
    assert seg.bare == {"mode"}
    assert seg.passthrough == ["x"]


def test_a_bare_mention_does_not_disturb_the_tokens_around_it(tree):
    # Four independent decisions: the task, a bare option, a positional, then a
    # word with nowhere left to go — which starts the next segment.
    first, second = segs(tree, "deploy --version prod lint")
    assert first.task == "deploy"
    assert first.bare == {"version"}
    assert first.values == {"env": "prod"}
    assert second.task == "lint"


def test_the_attachment_hint_is_said_once_when_a_task_option_shadows_a_global():
    # `--jobs` is deploy's own option here and a value-taking global too, so
    # the resolver's refusal for the stranded `4` is already the attachment
    # teaching — the wrapper used to append the same sentence a second time.
    def tasks(reg):
        @reg.task
        def deploy(jobs: str = "one"): ...

        @reg.task
        def ship(mode: str = "fast"): ...

    _, tree = build_tree(tasks)

    with pytest.raises(ChainError) as excinfo:
        split_chain(tree, ["deploy", "--jobs", "4"])
    message = str(excinfo.value)
    assert "--jobs takes its value attached — did you mean --jobs=4?" in message
    assert message.count("did you mean") == 1

    # An option shadowing nothing still gets its one hint, appended as before.
    with pytest.raises(ChainError) as excinfo:
        split_chain(tree, ["ship", "--mode", "quick"])
    message = str(excinfo.value)
    assert message.endswith("did you mean --mode=quick?")
    assert message.count("did you mean") == 1


def test_dash_leading_value_attaches(tree):
    # A value that starts with a dash parses trivially in attached form —
    # the case the space form could never express.
    assert globs(tree, "--jobs=-1 check") == ["--jobs=-1"]
    (seg,) = segs(tree, "bench --timeout=-1.5")
    assert seg.values == {"timeout": "-1.5"}


def test_a_global_default_is_declared_beside_its_metavar(monkeypatch):
    from livery.footman import _split

    # A literal where the default is constant, nothing where the option has no
    # reading without a value — which is the same question a task option
    # answers with `required`.
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    # Computed: the declared default reads the ambient protocol variables.
    assert _split.global_default("--color") == ("auto", True)
    # `None` where the option must be given a value at all.
    assert _split.global_default("--where") == (None, False)
    assert _split.global_default("--directory") == (None, False)
    # And `""` where the reading exists but has no spelling of its own.
    assert _split.global_default("--describe") == ("", False)


def test_a_computed_global_default_resolves_at_the_call_not_at_import():
    from livery.footman import _split

    value, computed = _split.global_default("--jobs")
    # The raw value, not a rendering of it: this is what the *run* uses.
    assert computed and isinstance(value, int) and value >= 2


def test_a_computed_global_default_reports_this_machine(monkeypatch):
    from livery.footman import _progress, _split

    # The point of computing it: `--help` must report the width *this* machine
    # will use, not a number baked when the module was imported (or, worse,
    # when someone else's manifest was written).
    monkeypatch.setattr(_progress, "default_jobs", lambda: 99)
    assert _split.global_default("--jobs") == (99, True)


def test_a_global_with_a_default_may_be_named_bare(tree):
    # Bare-legal because it *has* a default — the same rule task options
    # follow, read from the table instead of a signature.
    assert globs(tree, "--jobs check") == ["--jobs"]
    assert globs(tree, "--color check") == ["--color"]
    assert [s.task for s in segs(tree, "--jobs check")] == ["check"]


def test_where_global_takes_a_value(tree):
    assert globs(tree, "--where=docker.build") == ["--where=docker.build"]
    assert segs(tree, "--where=docker.build") == []


ERROR_CASES = [
    ("lint --mode=fast", "lint: --mode must be one of strict|loose (got 'fast')"),
    ("docs.serve --port=http", "docs.serve: --port expects an integer (got 'http')"),
    ("bench --timeout=fast", "bench: --timeout expects a number (got 'fast')"),
    # A value is always `=`-attached, so the space form is two tokens: `--mode`
    # binds its default and `strict` is read as the next task. That is the only
    # reading available — but the line fails, so the failure carries the fix.
    ("lint --mode strict", "did you mean --mode=strict?"),
    ("--color always lint", "did you mean --color=always?"),
    # `--jobs` has a default, so a bare mention is legal and means it — but
    # `4` then has nowhere to go, and the failure carries the fix.
    ("--jobs 4 check", "did you mean --jobs=4?"),
    ("-j 4 check", "did you mean -j=4?"),
    # Bare with nothing attachable following: state the shape instead.
    ("--where lint", "--where takes its value attached — did you mean --where=lint?"),
    # A value-optional global is valid bare — but a detached value behind it
    # still teaches the attachment, never "unknown task 'zsh'".
    (
        "--install-completion zsh",
        "--install-completion takes its value attached — "
        "did you mean --install-completion=zsh?",
    ),
    ("version huge", "version: <part> must be one of major|minor|patch (got 'huge')"),
    (
        "deploy check",
        "deploy: <env> must be one of dev|staging|prod — 'check' looks like "
        "the next task; did you forget <env>?",
    ),
    ("lint test --fix", "test: unknown option --fix"),
    # The space form of a nested address is permanently taught, never parsed.
    ("docs serve", "nested tasks use dots: 'docs.serve', not 'docs serve'"),
    (
        "docs serve --port 8001",
        "nested tasks use dots: 'docs.serve', not 'docs serve'",
    ),
    # A bare namespace group is never a segment target; the answer lists
    # its children as addresses.
    (
        "docs",
        "'docs' is a group, not a task — name one of its tasks "
        "(know: docs.serve, docs.build)",
    ),
    (
        "docs deplo",
        "'docs' is a group, not a task — name one of its tasks "
        "(know: docs.serve, docs.build)",
    ),
    # The teach stops at the longest resolvable prefix — the rest of the
    # line is someone else's segment.
    ("docs build lint", "nested tasks use dots: 'docs.build', not 'docs build'"),
    # Strict addresses: empty segments and hanging dots are taught, never
    # silently normalised.
    ("docs.", "'docs.' is an incomplete address (know: docs.serve, docs.build)"),
    ("docs..serve", "'docs..serve' is not a task address"),
    (".docs", "'.docs' is not a task address"),
    ("check.deep", "'check' is a task, not a group — nothing lives beneath it"),
    ("docs.sevre", "no task named 'docs.sevre' — did you mean 'docs.serve'?"),
    # A misplaced global names the real problem — position — not "unknown".
    (
        "--json lint --quiet",
        "lint: --quiet is a global option — it goes before the first task name",
    ),
    (
        "lint -k",
        "-k (--keep-going) is a global option — it goes before the first task name",
    ),
    (
        "check + --json",
        "--json is a global option — it goes before the first task name",
    ),
    ("render only-one", "render: missing required positional(s): <output>"),
    ("--nope check", "unknown global option --nope"),
    ("--sequential=false lint", "--sequential is a flag and takes no value"),
    ("--json=0 lint", "--json is a flag and takes no value"),
    ("chekc", "did you mean 'check'?"),  # unknown task → nearest name
    ("lint --fux", "did you mean '--fix'?"),  # unknown option → nearest option
    ("lint --mode=strikt", "did you mean 'strict'?"),  # unknown choice value
]


@pytest.mark.parametrize("line, message", ERROR_CASES)
def test_teaching_errors(tree, line, message):
    with pytest.raises(ChainError) as excinfo:
        split_chain(tree, line.split())
    assert message in str(excinfo.value)


def test_unmatchable_typo_gets_no_suggestion(tree):
    # No false confidence: a word close to nothing known adds no "did you mean".
    with pytest.raises(ChainError) as excinfo:
        split_chain(tree, ["zzzzzzzz"])
    assert "did you mean" not in str(excinfo.value)


def test_an_empty_listing_says_nothing_rather_than_trailing_off():
    # Every "know:" clause has a tree that can leave it empty, and `(know: )`
    # reads as a bug in footman rather than an answer.
    def tasks(reg):
        reg.group("docs")

        @reg.task
        def check(): ...

    _, tree = build_tree(tasks)

    # A group with no children, named bare and descended into.
    with pytest.raises(ChainError) as excinfo:
        split_chain(tree, ["docs"])
    assert "name one of its tasks (know: nothing)" in str(excinfo.value)
    with pytest.raises(ChainError) as excinfo:
        split_chain(tree, ["docs.x"])
    assert "no task named 'docs.x' (docs has: nothing)" in str(excinfo.value)
    with pytest.raises(ChainError) as excinfo:
        split_chain(tree, ["docs."])
    assert "'docs.' is an incomplete address (know: nothing)" in str(excinfo.value)

    # A tasks file with no @task at all.
    _, bare = build_tree(lambda reg: None)
    with pytest.raises(ChainError) as excinfo:
        split_chain(bare, ["build"])
    assert "no task named 'build' (know: nothing)" in str(excinfo.value)


def test_an_empty_listing_covers_tasks_hidden_by_the_project_question():
    # The tasks are right there in the tree — `_children` leaves them out
    # because there is no project to run them from, which empties the listing
    # just as thoroughly as having no tasks at all.
    reg = Group("root")

    @reg.task(expose="project_only")
    def build(): ...

    tree = _manifest.build_manifest(reg, project=False)["tree"]

    with pytest.raises(ChainError) as excinfo:
        split_chain(tree, ["nope"])
    assert "no task named 'nope' (know: nothing)" in str(excinfo.value)


def test_a_grouped_shape_is_refused_before_anything_runs():
    """The manifest carries a group's arity and per-slot types, so a wrong
    value never needs a run to be found — the same eager treatment every
    other typed parameter gets."""

    def tasks(reg):
        @reg.task
        def render(size: Box = Box(0, 0)): ...

        @reg.task
        def route(points: list[Box] = ()): ...  # type: ignore[assignment]

    _, tree = build_tree(tasks)

    with pytest.raises(ChainError, match=r"height expects an integer"):
        split_chain(tree, ["render", "--size=800,tall"])
    with pytest.raises(ChainError, match=r"--size takes width,height — got 1"):
        split_chain(tree, ["render", "--size=800"])
    with pytest.raises(ChainError, match=r"groups of 2 \(width,height\)"):
        split_chain(tree, ["route", "--points=1,2,3"])
    # Repetition and commas feed one stream, so the arity is only final at
    # the end of the segment — these are the same three values.
    with pytest.raises(ChainError, match=r"leaves 1 over"):
        split_chain(tree, ["route", "--points=1,2", "--points=3"])
    split_chain(tree, ["route", "--points=1,2", "--points=3,4"])  # whole: fine


def test_a_grouped_positional_checks_each_slot_not_slot_zero():
    """The stream index rides into validation for positionals exactly as it
    does for options: each token is checked against its own slot's type, so
    a heterogeneous shape's legal line parses, and a bad value is refused
    naming *its* field. Without the index every token validated as slot 0 —
    `tag 3 hot` refused claiming copies expects an integer."""

    def tasks(reg):
        @reg.task
        def render(size: Box): ...

        @reg.task
        def tag(label: Tag): ...

    _, tree = build_tree(tasks)

    (seg,) = segs(tree, "tag 3 hot")  # a str at slot 1 is simply legal
    assert seg.values == {"label": ["3", "hot"]}
    with pytest.raises(ChainError, match=r"copies expects an integer"):
        split_chain(tree, ["tag", "three", "hot"])
    with pytest.raises(ChainError, match=r"height expects an integer"):
        split_chain(tree, ["render", "800", "tall"])


def test_a_task_option_written_where_globals_live_points_right(tree):
    # The generic sentence sends someone LEFT ("globals go before the first
    # task") when the fix is to move the word RIGHT, past the task that owns
    # it. Name that task — the one they typed, when they typed one.
    with pytest.raises(ChainError) as excinfo:
        split_chain(tree, ["--fix", "lint"])
    message = str(excinfo.value)
    assert "--fix is an option of lint, not a global" in message
    assert "it goes after the task name: lint --fix" in message


def test_several_owners_and_none_typed_lists_them(tree):
    # --fix belongs to both format and lint; with neither on the line there
    # is nothing to name, so the answer lists rather than guesses.
    with pytest.raises(ChainError) as excinfo:
        split_chain(tree, ["--fix", "check"])
    message = str(excinfo.value)
    assert "--fix is a task option, not a global" in message
    assert "it goes after the task that takes it (" in message
    assert "lint" in message


@pytest.fixture
def brand_dist(monkeypatch):
    """Point `_brand` at a chosen distribution, and clear the scan's memo.

    Both are process-global. `Runner` now puts the brand back after every
    invocation (it always restored `_paths`, never `_brand`, and three macOS
    jobs found that the hard way), so these tests no longer *depend* on the
    pin — but they still state the distribution they are about rather than
    inheriting whatever the process happens to hold, and the memo has to be
    cleared to ask the question twice."""
    import dataclasses

    from livery.footman import _app, _split

    def use(dist: str | None) -> None:
        monkeypatch.setattr(_app, "_brand", dataclasses.replace(_app._brand, dist=dist))
        _split._OWN_FLAGS.clear()

    use("footman")
    return use


def test_an_unmounted_plugin_flag_teaches_its_mount(tree, brand_dist):
    # Spelled perfectly, refused anyway: "unknown" sends someone hunting for
    # a typo that isn't there. The flag exists — the plugin isn't mounted.
    with pytest.raises(ChainError) as excinfo:
        split_chain(tree, ["--env-file=.env", "check"])
    message = str(excinfo.value)
    assert "--env-file comes from footman.env_files" in message
    assert 'add plugin("footman.env_files") to tasks.py' in message

    with pytest.raises(ChainError, match=r"--profile comes from footman\.profile"):
        split_chain(tree, ["--profile", "check"])


def test_the_scan_finds_every_flag_a_vouched_distribution_ships(brand_dist):
    """Discovered, not listed: nothing names `--env-file` or `--profile`
    anywhere in footman's source but the plugins that declare them, so a
    new first-party global is taught the day it ships."""
    from livery.footman import _split

    found = _split._own_plugin_flags()
    assert found["--env-file"] == "footman.env_files"
    assert found["--profile"] == "footman.profile"


def test_the_scan_answers_the_same_once_the_modules_are_imported(brand_dist):
    """A module imports once per process, so its declarations fire in exactly
    one capture — whoever called `load()` first. The scan goes through
    `_load_entry_point`, which memoises that tree, so the answer does not
    depend on who got there first — including when nobody proper got there
    at all: on a worker where these bare imports were the process's first
    touch of the modules, the import was spent outside any capture, and
    this very test blinded the scan for its whole worker (the 2026-08-14
    flake). The load now rebuilds a spent module from its own namespace."""
    from livery.footman import _split, env_files, profile

    assert env_files and profile  # imported, on purpose
    assert _split._own_plugin_flags()["--env-file"] == "footman.env_files"


def test_a_spent_import_does_not_blind_the_scan(brand_dist, monkeypatch):
    """The flake, pinned deterministically: delete the memo for an
    already-imported plugin module and the scan is exactly where a worker
    stood after a bare `import livery.footman.env_files` beat every proper load —
    `load()` captures nothing, there is no tree to reuse, and the scan used
    to drop the flag for the rest of the process. It must rebuild instead,
    whatever this worker happened to run first."""
    from livery.footman import _split, compose, env_files

    assert env_files  # imported — possibly bare, possibly first
    monkeypatch.delitem(
        compose._module_trees, "livery.footman.env_files", raising=False
    )
    found = _split._own_plugin_flags()
    assert found["--env-file"] == "footman.env_files"
    assert found["--profile"] == "footman.profile"


def test_scanning_does_not_spend_the_import_a_real_mount_needs(brand_dist):
    """The scan must not cost a plugin its one import. Going around
    `_load_entry_point` with a raw `ep.load()` did exactly that: the module
    body ran inside the scan's own capture, nothing was memoised, and the
    mount that came afterwards landed a plugin with no options at all —
    four `test_env_files` failures that only appeared when the scan happened
    to run first in a worker."""
    from livery.footman import _split, compose, registry

    assert "--env-file" in _split._own_plugin_flags()

    # A GlobalOption registers where the mount's capture can see it, which is
    # the thing a spent import silently takes away.
    with registry.capture() as captured:
        compose.plugin("footman.env_files", into=captured)
    assert [g.name for g in captured.contributions["globals"]] == ["env-file"]


def test_footmans_own_plugins_are_taught_whatever_the_brand_is(brand_dist):
    """`--profile` and `--env-file` are the framework's, useful to every
    runner built on it, and footman is imported by definition — so a brand
    gets them taught whether or not it ever named a distribution of its
    own."""
    from livery.footman import _split

    for dist in (None, "acme-cli", "footman"):
        brand_dist(dist)
        found = _split._own_plugin_flags()
        assert found["--profile"] == "footman.profile", dist
        assert found["--env-file"] == "footman.env_files", dist


def test_a_third_partys_flag_stays_plainly_unknown(brand_dist):
    """The line footman will not cross. A distribution it neither ships nor
    was vouched for by the brand keeps the plain answer: teaching that flag
    would mean importing, on a typo, code the project chose not to mount."""
    from livery.footman import _split

    brand_dist("acme-cli")
    found = _split._own_plugin_flags()
    # Everything discovered came from a package footman may speak for.
    assert set(found.values()) == {"footman.env_files", "footman.profile"}
    with pytest.raises(ChainError, match=r"unknown global option --tf-workspace"):
        split_chain(
            _manifest.build_manifest(Group("root"))["tree"], ["--tf-workspace=prod"]
        )


def test_one_word_too_many_reads_as_arity_not_a_bad_address(tree):
    # `fm render a b spare` is far more often a hand that typed one argument
    # too many than a misspelled task name — both readings, likeliest first.
    with pytest.raises(ChainError) as excinfo:
        split_chain(tree, ["render", "page.md", "out.html", "spare"])
    message = str(excinfo.value)
    assert "no task named 'spare'" in message
    assert "or one argument too many for render, which takes 2 arguments" in message


def test_a_task_that_takes_no_arguments_says_none(tree):
    with pytest.raises(ChainError) as excinfo:
        split_chain(tree, ["check", "spare"])
    assert "one argument too many for check, which takes none" in str(excinfo.value)


def test_a_near_miss_address_keeps_the_spelling_reading(tree):
    # A word close to a task name gets the suggestion, not the arity clause:
    # two competing "did you mean"s in one sentence teach nothing.
    with pytest.raises(ChainError) as excinfo:
        split_chain(tree, ["render", "page.md", "out.html", "chekc"])
    message = str(excinfo.value)
    assert "did you mean 'check'?" in message
    assert "too many" not in message


def test_the_arity_clause_names_the_task_that_just_filled_up(tree):
    # A real chain after a filled task is still a chain — and when a later
    # word fails, the clause speaks for the task it followed, not for
    # whichever one filled up earliest.
    assert [s.task for s in segs(tree, "render page.md out.html check")] == [
        "render",
        "check",
    ]
    with pytest.raises(ChainError) as excinfo:
        split_chain(tree, ["render", "page.md", "out.html", "check", "zzzzzzzz"])
    message = str(excinfo.value)
    assert "too many for check, which takes none" in message
    assert "too many for render" not in message
