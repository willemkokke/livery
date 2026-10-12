"""The tools' identity, and git settings that ride the environment."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


def tool_identity(prog: str) -> tuple[str, str]:
    """The name and address a tool called *prog* commits under.

    For every commit a verb makes when the person has no identity of
    their own: a release, a loop's scratch, the state store's rows.
    The domain is `.invalid`, reserved for names that resolve nowhere.
    """
    return prog, f"{prog}@{prog}.invalid"


def identity_environment(name: str, email: str) -> dict[str, str]:
    """The variables that make every commit and tag by *name* and *email*.

    Author and committer both: git reads the four variables ahead of
    any config, so a checkout's own identity is left as it is.
    """
    return {
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
    }


def config_environment(
    environ: Mapping[str, str], pairs: Sequence[tuple[str, str]]
) -> dict[str, str]:
    """*pairs* as git configuration riding the environment, after what it carries.

    git reads `GIT_CONFIG_COUNT` with a key and a value per index, so an
    entry the outer environment already carries keeps its index and
    these follow it. The mapping returned holds the new entries and the
    count; merge it over *environ*.
    """
    raw = environ.get("GIT_CONFIG_COUNT", "")
    count = int(raw) if raw.isdigit() else 0
    made: dict[str, str] = {}
    for offset, (key, value) in enumerate(pairs):
        index = count + offset
        made[f"GIT_CONFIG_KEY_{index}"] = key
        made[f"GIT_CONFIG_VALUE_{index}"] = value
    made["GIT_CONFIG_COUNT"] = str(count + len(pairs))
    return made
