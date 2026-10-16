# livery-extensions-mkdocstrings

The python API reference on the workshop's docs site. A workspace turns
it on by listing the extension:

```toml
[workspace]
extensions = ["mkdocstrings"]
```

It requires the docs extension, `livery-extensions-docs`, so the
workspace's set takes docs in through it, and the canonical list leaves
docs out.

## The reference pages

The extension's `mkdocstrings` step of the docs phase runs once for
each package whose set holds python and writes the package's reference
into its generated tree: a page per module of each of its roots under
`api/`, public modules before private ones at every level, each page
one directive the handler renders with the module's members, and the
`api` nav block that lists them. The site places that block after the
section's other entries, unless the package's `nav.toml` places it with
a marker pair. A package that declines with `[docs] api = false` gets
neither.

The step's `paths` option names further directories, relative to the
package, the python handler reads modules from after the package's
sources, such as stubs a generator renders:

```toml
[phases.docs.mkdocstrings]
paths = ["docs/_generated/stubs"]
```

## The handler's tables

The extension answers the docs extension's `docs.config` query with
the python handler's tables: the search paths of the packages the
config covers, the inventories, with the published site's own for a
preview, and the handler's options, with an empty filter list when
`docs.members` answers `all`.

## What a workspace installs

A workspace that does not list the extension renders no API reference
and no handler table, and installs no mkdocstrings. The extension
answers `python.docs-group` with `mkdocstrings-python`, the python
handler the site's builder imports, which also brings griffe, the
library `lint.docrefs` reads the sources with.

## The docstring checks

The extension also holds the two lint steps that judge the docstrings
the reference publishes:

- `lint.docrefs`: every cross-reference in a docstring the site
  renders names an anchor the site renders.
- `lint.docstrings`: every name a python package exports has a
  docstring.
