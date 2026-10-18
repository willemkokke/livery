"""Every pytest with livery-git installed gives its git a config of the session's own.

Registered through the `livery-git-isolation` `pytest11` entry point,
so every session in an environment with livery-git carries it. Before
the first test, every test's git reads `GIT_CONFIG` as its global
config, from a fresh directory in the system's temporary folder: one
identity, so a repository a test creates commits without a
config of its own, and none of the desk's settings, a signer among
them. A `GIT_CONFIG_GLOBAL` the outer environment already set is kept,
since an outer redirect is a redirect. The identity variables git reads
ahead of every config leave the session's environment, so a
colleague's clone a test configures commits as the colleague. The
session's end removes the directory and the variable it set; a session
that is killed leaves the directory in the temporary folder.

`-p no:livery-git-isolation` turns the plugin off for one session.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

#: The global git config every test's git reads in place of the
#: developer's. A test that needs a second author, a colleague's
#: clone, configures that clone, and the clone's own config wins over
#: this one as it would over a person's.
GIT_CONFIG = "[user]\n\tname = tester\n\temail = tester@example.invalid\n"

#: The identity variables git reads ahead of every config. livery-git's
#: footman plugin sets them for a verb's task when the machine has no
#: identity, and the session's children would inherit them, where they
#: would beat the config a test gives a colleague's clone.
IDENTITY_VARIABLES = (
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
)

#: The session's directory, for the session's end.
_HOME = pytest.StashKey[str]()


def pytest_configure(config: pytest.Config) -> None:
    """Drop the identity variables, and point git at the session's config."""
    for name in IDENTITY_VARIABLES:
        os.environ.pop(name, None)
    if "GIT_CONFIG_GLOBAL" in os.environ:
        return
    home = tempfile.mkdtemp(prefix="livery-git-isolation-")
    gitconfig = Path(home) / "gitconfig"
    gitconfig.write_text(GIT_CONFIG, "utf-8")
    os.environ["GIT_CONFIG_GLOBAL"] = str(gitconfig)
    config.stash[_HOME] = home


def pytest_unconfigure(config: pytest.Config) -> None:
    """Remove the session's config, and the variable that named it."""
    home = config.stash.get(_HOME, "")
    if home:
        os.environ.pop("GIT_CONFIG_GLOBAL", None)
        shutil.rmtree(home, ignore_errors=True)
