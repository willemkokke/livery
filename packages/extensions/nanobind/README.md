# livery-extensions-nanobind

A python package whose extension module is C++, bound to python through
nanobind, as an extension of the livery workshop. Listed as `nanobind`
in a package's `extensions`, it builds the package's platform wheels
through cibuildwheel and its sdist for a release, one leg of the
release's wheels job on each runner the package names under
`[nanobind] wheel-platforms`. It requires python for the distribution,
cpp for the extension module's sources, and conan for the provider that
resolves their dependencies. `fm new.package.nanobind-module <name>`
creates such a package while a workspace lists the extension.
