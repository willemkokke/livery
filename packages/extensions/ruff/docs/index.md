<!-- Seeded from the package-base seeds at
     birth; this file is the workspace's own. Edit it directly:
     nothing rewrites it.
-->
# livery-extensions-ruff

Ruff as a check of the workshop: `format.ruff` formats the python files a
run reaches and `lint.ruff` lints them. A workspace turns them on by
listing the extension:

```toml
[workspace]
extensions = ["ruff"]
```

`fm format` and `fm lint` then run them, and `fm check` runs them with the
rest of the gate: the formatter, then the linter, after the workshop's own
rewriters and before every judge. Each reads the python files its claims
reach: a python package's sources, tests and test support, its
configuration, and a native package's `conanfile.py`. An example file is
judged for its names alone, and a test needs no docstrings.
`fm lint.ruff -- --statistics` hands `ruff check` the words after `--`, and
`fm format.ruff -- --diff` hands them to `ruff format`.

## The configuration

`fm sync` writes the root `ruff.toml`, and a bare `ruff` in the repository
reads the same file the checks do. It sets the line length, the python
version the workspace supports, the rules, and the per-file ignores the
checks' claims render. Ruff's cache is `.workshop/.cache/ruff/`.

The file is the extension's: an edit outside its region is drift, which
`fm check --fix` puts back. The repository's own settings go inside the
region, as whole tables or through a key's `extend-` spelling:

```toml
# -- workshop: region tables, yours to edit; the render keeps it --
[lint.extend-per-file-ignores]
"**/generated.py" = ["E501"]
# -- workshop: end tables --
```

A package that needs settings of its own carries a `ruff.toml` that
extends the root's, with `extend = "../../ruff.toml"`, one `../` per
directory level.

## Fixing

`fm check --fix` and `fm format --fix` rewrite what ruff can rewrite.
`--safe-fix` removes no code: it applies every fix except deleting an
unused import, which an edit in flight adds before the code that uses it.
