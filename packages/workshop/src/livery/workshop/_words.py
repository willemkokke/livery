"""Checks in words: a tool and its arguments in a declaration, and their engine.

A check whose verdict is its tool's exit code needs no code. Its
declaration names the tool and the words it is called with: ``judge``,
and ``fix`` and ``safe-fix`` for a check that rewrites. The engine
does the rest. It selects what the run reaches, as the check's scope
and narrowing say; appends the words after ``--`` on the check's verb,
then the paths, or nothing when the run is whole; splits a long path
list into the fewest calls under the command-line limit; and runs the
tool through its toolroom handle, which prints the tool's output. A
non-zero exit refuses, and the refusals of every call merge into one.

A word may name a placeholder, ``{name}``, answered per call:
``{cache}``, the check's own directory under ``.workshop/.cache/``;
``{package}``, the directory of the package a call judges;
``{compile-commands}``, the directory of that package's compilation
database, without which the call does not run; and each key of
``matrix``, which runs one call per value in parallel, each its own
verdict. ``env`` gives every call variables over the run's own.

A check the words cannot say, one whose verdict is not its tool's exit
code or whose paths need reshaping first, declares ``run``, a reference
to its code, instead of ``judge``.
"""

from __future__ import annotations

import itertools
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from livery.footman import fail

if TYPE_CHECKING:
    from livery.workshop._checks import CheckRecord, GateContext
    from livery.workshop._packages import Package

#: The placeholders the engine answers, beside a check's matrix keys.
PLACEHOLDERS = ("cache", "package", "compile-commands")

#: The placeholders only a call that judges one package can answer.
PACKAGE_PLACEHOLDERS = ("package", "compile-commands")

#: A placeholder in a word: ``{name}``, lower-case words joined by dashes.
_PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9-]*)\}")


@dataclass(frozen=True)
class Words:
    """A check's words, as its declaration spells them.

    Attributes:
        judge: The tool's name, as the check's ``tools`` name it, and
            the arguments that judge.
        fix: The tool and the arguments that rewrite under ``--fix``;
            empty for a check that only judges.
        safe_fix: The tool and the arguments that rewrite under
            ``--safe-fix``, removing no code; empty to rewrite with
            ``fix``.
        env: The variables every call gets, over the run's own, by
            name.
        matrix: Each placeholder the calls vary, with its values: one
            call per combination.
    """

    judge: tuple[str, ...]
    fix: tuple[str, ...] = ()
    safe_fix: tuple[str, ...] = ()
    env: tuple[tuple[str, str], ...] = ()
    matrix: tuple[tuple[str, tuple[str, ...]], ...] = ()

    @property
    def tool(self) -> str:
        """The tool every call runs, the first word of ``judge``."""
        return self.judge[0]

    def placeholders(self) -> frozenset[str]:
        """Every placeholder the words name, in any mode or variable."""
        texts = [*self.judge, *self.fix, *self.safe_fix]
        texts += [value for _name, value in self.env]
        return frozenset(name for text in texts for name in _PLACEHOLDER.findall(text))

    def unanswered(self) -> tuple[str, ...]:
        """The placeholders neither the engine nor the matrix answers, sorted."""
        known = {*PLACEHOLDERS, *(key for key, _values in self.matrix)}
        return tuple(sorted(self.placeholders() - known))

    def unvaried(self) -> tuple[str, ...]:
        """The matrix keys no word names, in the matrix's order."""
        named = self.placeholders()
        return tuple(key for key, _values in self.matrix if key not in named)


@dataclass(frozen=True)
class Command:
    """One mode of a check in words, called as the check's ``run`` or ``fix``.

    Attributes:
        words: The check's words.
        check: The check's name, ``lint.ruff``, which a run that names
            no check judges by.
        fixing: Whether this is the fix mode, which runs ``safe-fix``
            under ``--safe-fix`` where the words declare it.
    """

    words: Words
    check: str
    fixing: bool = False

    def argv(self, *, safe: bool = False) -> tuple[str, ...]:
        """The words this mode runs: the tool's name, then its arguments."""
        if not self.fixing:
            return self.words.judge
        return self.words.safe_fix if safe and self.words.safe_fix else self.words.fix

    def __call__(self, ctx: GateContext) -> None:
        """Run this mode over what *ctx* reaches; a non-zero exit refuses."""
        run_words(self, ctx)

    def __str__(self) -> str:
        """The words this mode runs, as the declaration spells them."""
        return " ".join(self.argv())


def run_words(command: Command, ctx: GateContext) -> None:
    """Run *command* over what the run reaches, as its check's scope and narrowing say.

    A package check calls the tool on the package's files the run
    reaches, from the package's directory, and calls nothing when there
    are none. A workspace check that narrows by paths calls it on the
    paths the run reaches, with none when the run is whole, and not at
    all when the run reaches nothing it reads; one that narrows by
    packages calls it once per package; any other calls it once, with no
    path.

    Raises:
        Failed: when a call exits non-zero, every call's reason merged.
    """
    from livery.workshop._checks import (
        PACKAGE,
        PACKAGES,
        PATHS,
        WHOLE,
        check_for,
        scoped_files,
        scoped_packages,
        scoped_paths,
    )

    record = check_for(ctx.check or command.check)
    ctx = replace(ctx, check=record.name)
    argv = command.argv(safe=ctx.safe)
    if record.scope == PACKAGE:
        if ctx.package is None:
            fail(f"{record.name} judges one package at a time, and this run names none")
        files = tuple(str(path) for path in scoped_files(ctx, record.name))
        if files:
            _calls(record, command.words, argv, ctx, ctx.package, files)
        return
    if record.narrowing == PATHS:
        chosen = scoped_paths(ctx, record.name)
        if not chosen:
            return  # the run reaches nothing the check reads
        _calls(
            record, command.words, argv, ctx, None, () if chosen == WHOLE else chosen
        )
        return
    if record.narrowing == PACKAGES:
        for package in scoped_packages(ctx, record.name):
            _calls(record, command.words, argv, ctx, package, ())
        return
    _calls(record, command.words, argv, ctx, None, ())


def _calls(
    record: CheckRecord,
    words: Words,
    argv: tuple[str, ...],
    ctx: GateContext,
    package: Package | None,
    paths: tuple[str, ...],
) -> None:
    """Run *argv* once per matrix combination, in parallel when there are several."""
    from livery.footman import parallel, step

    cwd = package.directory if package is not None else ctx.root
    found = _answers(record, words, ctx.root, cwd, package)
    if found is None:
        return
    combinations = _combinations(words.matrix)

    def one(varied: dict[str, str]) -> None:
        answers = {**found, **varied}
        arguments = tuple(_expand(record, word, answers) for word in argv[1:])
        variables = {name: _expand(record, value, answers) for name, value in words.env}
        _batched(
            argv[0], arguments, ctx.arguments, paths, cwd=cwd, ctx=ctx, env=variables
        )

    if len(combinations) == 1:
        one(combinations[0])
        return
    parallel(
        *(
            step(one, title="_".join((record.tool, *varied.values())))(varied)
            for varied in combinations
        )
    )


def _answers(
    record: CheckRecord,
    words: Words,
    root: Path,
    cwd: Path,
    package: Package | None,
) -> dict[str, str] | None:
    """The engine's answer to each placeholder the words name; None to run nothing.

    A package with no compilation database answers nothing for
    ``{compile-commands}``, and the call is skipped, saying so.

    Raises:
        Failed: when the words name a package's placeholder in a call
            that judges no package.
    """
    from livery.workshop._lifecycle import compile_commands

    named = words.placeholders()
    answers = {"cache": _relative(root / ".workshop" / ".cache" / record.tool, cwd)}
    for name in PACKAGE_PLACEHOLDERS:
        if name in named and package is None:
            fail(
                f"{record.name} names {{{name}}}, which only a call that judges"
                ' one package answers; declare the check with scope = "package"'
            )
    if package is not None:
        answers["package"] = str(package.directory)
    if "compile-commands" in named and package is not None:
        database = compile_commands(package)
        if database is None or not database.is_file():
            print(
                f"  {record.name}: {package.path} has no compilation database; not run"
            )
            return None
        answers["compile-commands"] = str(database.parent)
    return answers


def _relative(path: Path, cwd: Path) -> str:
    """*path* as a call from *cwd* names it: relative where it can be."""
    try:
        return Path(os.path.relpath(path, cwd)).as_posix()
    except ValueError:  # another drive on Windows: no relative path exists
        return str(path)


def _combinations(
    matrix: tuple[tuple[str, tuple[str, ...]], ...],
) -> list[dict[str, str]]:
    """Every combination of the matrix's values, by key; one empty one for no matrix."""
    keys = [key for key, _values in matrix]
    return [
        dict(zip(keys, values, strict=True))
        for values in itertools.product(*(values for _key, values in matrix))
    ]


def _expand(record: CheckRecord, word: str, answers: dict[str, str]) -> str:
    """*word* with each placeholder answered.

    Raises:
        Failed: when it names a placeholder nothing answers.
    """

    def answer(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in answers:
            fail(
                f"{record.name} names {{{name}}}, which neither the engine nor its"
                f" matrix answers; the engine answers {', '.join(PLACEHOLDERS)}"
            )
        return answers[name]

    return _PLACEHOLDER.sub(answer, word)


def _batched(
    tool: str,
    words: tuple[str, ...],
    arguments: tuple[str, ...],
    paths: tuple[str, ...],
    *,
    cwd: Path,
    ctx: GateContext,
    env: dict[str, str],
) -> None:
    """Call *tool* with *words*, *arguments*, then *paths*, batched under the limit."""
    from livery.workshop._invoke import run_batched

    def call(batch: tuple[str, ...]) -> None:
        run_tool(tool, (*words, *arguments, *batch), cwd=cwd, ctx=ctx, env=env)

    if paths:
        run_batched(paths, call)
    else:
        call(())


def run_tool(
    tool: str,
    argv: tuple[str, ...],
    *,
    cwd: Path,
    ctx: GateContext,
    env: dict[str, str],
) -> None:
    """Call *tool*'s toolroom handle with *argv*, from *cwd*; a non-zero exit refuses.

    The handle prints the tool's output. A call from the workspace root
    runs in the run's own directory, and one with no variables in the
    run's own environment, so neither is passed.

    Raises:
        Failed: when the tool exits non-zero, with the handle's reason.
    """
    import livery.toolroom.tools as tools
    from livery.footman import RunFailed

    handle = getattr(tools, tool)
    chosen: dict[str, object] = {}
    if cwd != ctx.root:
        chosen["cwd"] = cwd
    if env:
        chosen["env"] = {**os.environ, **env}
    try:
        (handle.opts(**chosen) if chosen else handle)(*argv)
    except (tools.ToolError, RunFailed) as error:
        # A failed call refuses as a check's call does, so a batched
        # run collects every batch's reason, the tool's output in each,
        # before it refuses.
        fail(str(error))


def explained(record: CheckRecord) -> list[str]:
    """The lines ``fm explain <check>`` prints: what the check runs, and over what.

    A check in words shows each mode's command as the tool's handle
    spells it, the placeholders left for the engine to answer, then
    what the engine appends; a check that runs code names its
    references.
    """
    from livery.workshop._checks import PACKAGE, PACKAGES, PATHS

    lines = [f"  {record.name}", f"    extension: {record.extension}"]
    if record.scope == PACKAGE:
        lines.append("    scope: one package at a time, from its directory")
    elif record.narrowing in (PATHS, PACKAGES):
        lines.append(f"    scope: the workspace, narrowed by {record.narrowing}")
    else:
        lines.append("    scope: the workspace, whole every run")
    words = record.words
    if words is None:
        lines.append(f"    run: {record.run} (code)")
        if record.fix is not None:
            lines.append(f"    fix: {record.fix} (code)")
        return lines
    appended = [
        *(["<the words after -->"] if record.arguments else []),
        *(["<the files the run reaches>"] if record.scope == PACKAGE else []),
        *(["<the paths the run reaches>"] if record.narrowing == PATHS else []),
    ]
    for mode, argv in (
        ("judge", words.judge),
        ("fix", words.fix),
        ("safe-fix", words.safe_fix),
    ):
        if argv:
            lines.append(f"    {mode}: {' '.join((*_shown(argv), *appended))}")
    for key, values in words.matrix:
        lines.append(f"    matrix: {key} = {', '.join(values)}, one call each")
    for name, value in words.env:
        lines.append(f"    env: {name}={value}")
    if "cache" in words.placeholders():
        lines.append(f"    {{cache}}: .workshop/.cache/{record.tool}")
    return lines


def _shown(argv: tuple[str, ...]) -> list[str]:
    """*argv* as the tool's handle spells its command, else as written."""
    import livery.toolroom.tools as tools

    handle = getattr(tools, argv[0], None)
    if handle is None:
        return list(argv)
    return [str(word) for word in handle.argv(*argv[1:])]
