<!-- Seeded from the package-python seeds at
     birth; this file is the workspace's own. Edit it directly:
     nothing rewrites it.
-->
# livery-extensions-pytest

pytest as a check of the livery workshop. Listed as `pytest` in a workspace's
`[workspace] extensions`, it registers `test.pytest`, which runs each python
package's suite with its coverage measured, and `examples.pytest`, which runs
each package's documentation examples. It writes the root `pytest.toml` and
`.coveragerc`.
