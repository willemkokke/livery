"""Set the environment variables the workspace's extensions declare.

An extension declares each environment variable it needs in an
``[env.<NAME>]`` table of its ``extension.toml``: the ``value``,
``always`` when that value beats one the environment already carries,
and ``doc``, what the variable is for. The value takes the placeholder
grammar of a step in words, ``{name}`` with lower-case words joined by
dashes, and answers one placeholder, ``{place}``: a directory of the
variable's own. Every other brace form refuses when the declaration is
read, so a placeholder added later changes the meaning of no declared
value.

Each place that sets the variables reads the declarations of the
extensions of the workspace's installed set, and names the directory
``{place}`` stands for:

- every ``fm`` run, through the cascade's hook: on a CI job, the job's
  own directory (`job_place`); on a desk, none;
- the entry script, ``setup.sh``, for the steps before ``fm``: the same
  directory on a CI job, and none off one;
- ``fm env.emit --github``, which persists the variables for a job's
  later steps: the runner's temporary directory;
- a pass of the loop: the directory of the development environment it
  runs on;
- the scale fixture: the fixture's directory.

A value naming ``{place}`` is not set where nothing names a directory.
Without ``always``, a variable the environment already carries keeps
its value, as the cascade's variables do. With it, the declared value
is set whatever the environment carries, since the environment's may
be one a runner sets for every job it takes.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from livery.footman import fail

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from livery.workshop._declaration import DeclaredVariable

#: The one placeholder an ``[env]`` value answers: a directory of the
#: variable's own.
PLACE_WORD = "{place}"

#: A name a shell exports: letters, digits and underscores, not starting
#: with a digit.
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

#: Any brace form in a value: a placeholder of any spelling, an opening
#: brace with no close, or a closing brace alone.
_BRACES = re.compile(r"\{[^{}]*\}?|\}")

#: Where a CI job keeps its own directory, by the entry's mode: the
#: variable the runner names it in, and the directory under it that
#: ``{place}`` stands for, empty for the directory itself. A GitHub or
#: Gitea job's is its temporary directory. A GitLab job's is
#: ``.workshop`` under its checkout: GitLab clones each of the jobs
#: running at once into a path of its own, and git ignores ``.workshop/``.
JOB_DIRECTORIES: dict[str, tuple[str, str]] = {
    "github": ("RUNNER_TEMP", ""),
    "gitlab": ("CI_PROJECT_DIR", ".workshop"),
}

#: The variable each CI kind sets on its jobs, by the entry's mode.
_JOB_MARKERS = {"github": "GITHUB_ACTIONS", "gitlab": "GITLAB_CI"}


def unanswered(value: str) -> tuple[str, ...]:
    """Each brace form in *value* other than ``{place}``, in order, once each."""
    return tuple(
        dict.fromkeys(form for form in _BRACES.findall(value) if form != PLACE_WORD)
    )


def declared_variables(extensions: Iterable[str]) -> tuple[DeclaredVariable, ...]:
    """The variables the installed declarations of *extensions* declare, in their order.

    An extension no installed distribution declares declares none.

    Raises:
        Failed: when two of the extensions declare one variable, naming
            both, since the two values would race for it.
    """
    from livery.workshop._extensions import installed_declaration

    found: dict[str, DeclaredVariable] = {}
    for extension in extensions:
        declared = installed_declaration(extension)
        if declared is None:
            continue
        for variable in declared.env:
            held = found.get(variable.name)
            if held is not None:
                fail(
                    f"{held.extension} and {variable.extension} both declare the"
                    f" environment variable {variable.name}; one extension"
                    " declares a variable"
                )
            found[variable.name] = variable
    return tuple(found.values())


def workspace_variables(root: Path) -> tuple[DeclaredVariable, ...]:
    """The variables the extensions of *root*'s installed set declare.

    Raises:
        Failed: when two of them declare one variable, naming both.
    """
    from livery.workshop._extensions import installed_set

    return declared_variables(installed_set(root))


def placed(
    variables: Iterable[DeclaredVariable],
    place: str | None,
    environ: Mapping[str, str],
) -> dict[str, str]:
    """The value each of *variables* takes against *environ*, by name.

    A value naming ``{place}`` takes *place* there, and is left out
    when *place* is None. A variable *environ* already carries keeps
    that value unless it is ``always``. An empty value counts as none,
    as in the cascade.
    """
    found: dict[str, str] = {}
    for variable in variables:
        if variable.placed and place is None:
            continue
        held = environ.get(variable.name, "")
        if held and not variable.always:
            found[variable.name] = held
        else:
            found[variable.name] = variable.render(place or "")
    return found


def job_place(environ: Mapping[str, str]) -> str | None:
    """The directory a CI job's variables take for ``{place}``; None off a CI job.

    A GitHub or Gitea job, which sets ``GITHUB_ACTIONS``, takes its
    temporary directory, ``RUNNER_TEMP``. A GitLab job, which sets
    ``GITLAB_CI``, takes ``.workshop`` under its checkout,
    ``CI_PROJECT_DIR`` (`JOB_DIRECTORIES`). None as well when the runner
    names no such directory, or one that does not exist: the entry
    script refuses that job before any ``fm`` runs.
    """
    for mode, marker in _JOB_MARKERS.items():
        if environ.get(marker):
            variable, under = JOB_DIRECTORIES[mode]
            directory = environ.get(variable, "")
            if not directory or not Path(directory).is_dir():
                return None
            return f"{directory}/{under}" if under else directory
    return None


def declarers(variables: Iterable[DeclaredVariable]) -> str:
    """Who declares *variables*, for a message: ``acme declares ACME_HOME``."""
    return " and ".join(
        f"{variable.extension} declares {variable.name}" for variable in variables
    )
