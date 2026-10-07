<!-- Seeded from the package-base seeds at
     birth; this file is the workspace's own. Edit it directly:
     nothing rewrites it.
-->
# livery-extensions-basedpyright

basedpyright as a check of the workshop: `typecheck.basedpyright`
type-checks the python files a run reaches, its warnings gating as errors.
A workspace turns it on by listing the extension:

```toml
[workspace]
extensions = ["basedpyright"]
```

`fm typecheck` then runs it, and `fm check` runs it with the rest of the
gate. It reads the files its claims reach in the python packages: their
sources, tests and test support. A run that reaches every package checks
what the configuration includes, the root's `tasks.py` and tests among it;
a run that reaches some checks their `src` and `tests` directories.
`fm typecheck.basedpyright -- --level error` hands basedpyright the words
after `--`.

## Type completeness

Listed with its option, the extension also registers
`typecomplete.basedpyright`:

```toml
[workspace]
extensions = ["basedpyright[typecomplete]"]
```

It verifies that each package's public API has a fully known type, through
`basedpyright --verifytypes`. A package's public API is what its roots
declare: a root that is a regular package declares it in its `__init__`,
its public packages too (imported under `TYPE_CHECKING` and listed in
`__all__`). A namespace root, one with no `__init__`, has nothing public
and verifies nothing. The verifier reads the `py.typed` at the
distribution's root.

A package turns either check off in its own contract:

```toml
[checks.basedpyright.typecomplete]
enabled = false
```

## The configuration

`fm sync` writes the root `pyrightconfig.json`, and a bare `basedpyright`
in the repository, and the editor's language server, read the same file
the check does. It names what the whole includes and excludes, the
environment in `.venv`, the stubs in `typings/`, the python version the
workspace supports, and the checking mode.

The file is the extension's: an edit outside its region is drift, which
`fm check --fix` puts back. The repository's own settings go inside the
region at the end, each line ending with a comma. A key set there wins
over the same key above it, since basedpyright reads the last one:

```json
  // -- workshop: region settings, yours to edit; the render keeps it --
  "executionEnvironments": [
    { "root": "packages/acme/tests", "reportPrivateImportUsage": false },
  ],
  // -- workshop: end settings --
```

A workspace that lists the extension recommends basedpyright's editor
extension, `detachhead.basedpyright`, in `.vscode/extensions.json`, and
turns the editor's default python language server off in
`.vscode/settings.json` (`"python.languageServer": "None"`): the type
checker that answers in the editor is the one this file configures, and
no second one answers with settings of its own.
