# livery-extensions-python

Python distributions, as an extension of the livery workshop. Listed as
`python` in a package's `extensions`, it stamps the version in
`pyproject.toml`, builds the wheel and the sdist, publishes them to a
python index, and tests the installed wheel in a fresh environment for
a release. Its layering rules judge the python sources: no reach into
another distribution's private names, no import a package does not
declare, and no terminal check beside the runner's own.
`fm new.package.python-library <name>` creates a python package while a
workspace lists the extension.
