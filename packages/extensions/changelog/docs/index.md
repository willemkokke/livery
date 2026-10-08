<!-- Seeded from the package-base seeds at
     birth; this file is the workspace's own. Edit it directly:
     nothing rewrites it.
-->
# livery-extensions-changelog

Changelogs for the workshop: the release train asks this extension for
each package's entry, and git-cliff writes it from the commits since
the package's last release. A workspace turns it on by listing the
extension:

```toml
[workspace]
extensions = ["changelog"]
```

Each package then carries a `cliff.toml`, which `fm sync` composes from
the extension's per-package content: the package's tag line, its paths
and the entry's shape. A package born while the extension is listed
starts with a `CHANGELOG.md`; one born before it gets the file at its
first release. A release writes the entry at the top of that file, and
refuses to tag a version whose entry is missing. The lock pins
git-cliff, and the lock refuses a host copy of it, so an entry never
depends on the machine that wrote it.

The entry credits the authors of each change where the forge can name
them: a per-kind variable (`GITHUB_TOKEN`, `GITEA_TOKEN`,
`GITLAB_TOKEN`) when it is set, else the token the workshop connects to
the forge with. Without one, the entry is written without its authors,
and the release says so.

A workspace that does not list the extension releases without notes,
and the release train says so where it would have written them.
