# livery-extensions-docs

The documentation site of the livery workshop. Listed as `docs` in a
workspace's `[workspace] extensions`, it builds, serves and publishes
the site from the workspace's `docs/` and each package's `docs/`
(`fm docs.build`, `fm docs.serve`, `fm docs.publish`), runs the `docs`
phase that writes the generated pages before each build and the
`deploy` phase before each publish, and judges the links in the
authored pages with `lint.doclinks`. An extension that adds to the
site, such as `mkdocstrings`, requires it.
