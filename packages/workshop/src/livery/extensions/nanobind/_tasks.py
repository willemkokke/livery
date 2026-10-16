"""The nanobind extension's verb, loaded through the extension's entry point.

Importing this module registers the ``nanobind`` group and its hidden
``wheels`` verb, which each leg of the release point's wheels job runs.
The job, the key that names its legs and the publish job's wait on it
are declared in the extension's ``extension.toml``; the labels and the
build each leg runs are the extension's ``_ci`` module's. The base's
plugin never imports this module.
"""

from __future__ import annotations

from livery.footman import Context, fail, group
from livery.workshop import workspace_root

nanobind = group("nanobind", help="The platform wheels of nanobind's packages")


def cibw_build_set(pythons: list[str]) -> str:
    """The cibuildwheel build identifiers for *pythons*, space-separated.

    ``3.14`` becomes ``cp314-*`` and a free-threaded ``3.14t`` becomes
    ``cp314t-*``, so a wheels leg builds exactly the interpreters the
    workspace's python matrix names, on every libc flavour of its
    platform, instead of every CPython cibuildwheel knows.
    """
    identifiers = []
    for version in pythons:
        minor, free_threaded = (
            (version[:-1], True) if version.endswith("t") else (version, False)
        )
        major, _, rest = minor.partition(".")
        identifiers.append(f"cp{major}{rest}{'t' if free_threaded else ''}-*")
    return " ".join(identifiers)


@nanobind.task(name="wheels", hidden=True)
def nanobind_wheels(ctx: Context) -> None:
    """Build this runner's native artifacts for the release at HEAD.

    The wheels job's one entry, on each of its legs. The job checks out
    the release squash the wave publishes, so HEAD is the release. The
    build is the extension's host build over the workspace's whole
    python matrix. A release that holds no native package builds
    nothing and says so, so the leg stays green on a pure release.

    Raises:
        Failed: when no workspace contract is above the working
            directory.
    """
    from livery.extensions.nanobind._ci import host_build
    from livery.workshop import python_matrix

    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    # The full set for this platform: the python matrix's interpreters,
    # and both libc flavours kept (an empty CIBW_SKIP reads as no skip,
    # and its presence stops the local narrowing's setdefault). Set on
    # the task's own environment, which every build child inherits; a
    # write to os.environ here would be scoped the same way and is
    # refused as an environment write meant to travel sideways.
    ctx.env.setdefault("CIBW_BUILD", cibw_build_set(python_matrix(root)))
    ctx.env.setdefault("CIBW_SKIP", "")
    host_build(root)
