<!-- Seeded from the package-base seeds at
     birth; this file is the workspace's own. Edit it directly:
     nothing rewrites it.
-->
# livery-extensions-ty

ty as a check of the workshop: `typecheck.ty` type-checks the python
packages' sources on every platform at once. A workspace turns it on by
listing the extension:

```toml
[workspace]
extensions = ["ty"]
```

`fm typecheck` then runs it, and `fm check` runs it with the rest of the
gate. A run checks what the configuration includes whatever the run
reaches: a run costs seconds, and the configuration pins the platforms.

## The configuration

`fm sync` writes the root `ty.toml`, and a bare `ty` in the repository
reads the same file the check does. It includes each python member's
`src` directory, or `tasks.py` in a workspace without one, checks every
platform at the python version the workspace supports, and reads the
tool stubs in `typings/`.

The file is the extension's: an edit outside its region is drift, which
`fm check --fix` puts back. The repository's own tables go inside the
region at the end:

```toml
# -- workshop: region tables, yours to edit; the render keeps it --
[rules]
possibly-unresolved-reference = "warn"
# -- workshop: end tables --
```

A workspace that lists the extension recommends ty's editor extension,
`astral-sh.ty`, in `.vscode/extensions.json`.
