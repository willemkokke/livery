# livery-extensions-mkdocstrings

The python API reference on the livery workshop's docs site. Listed as
`mkdocstrings` in a workspace's `[workspace] extensions`, it writes a
page per module of each python package, adds the python handler's
tables to the site's config, and judges the docstrings the reference
publishes and the cross-references in them. It requires the docs
extension, which the workshop's wheel ships.
