<!-- Seeded from the package-base seeds at
     birth; this file is the workspace's own. Edit it directly:
     nothing rewrites it.
-->
# livery-extensions-mypy

mypy as a check of the workshop: `typecheck.mypy` type-checks the python
files a run reaches on linux, darwin and win32, since mypy has no
all-platforms mode. A workspace turns it on by listing the extension:

```toml
[workspace]
extensions = ["mypy"]
```

`fm typecheck` then runs it, and `fm check` runs it with the rest of the
gate. It reads the files its claims reach in the python packages: their
sources, tests and test support. A run that reaches every package checks
what the configuration names in `files`, the root's `tasks.py` and tests
among them; a run that reaches some checks their `src` and `tests`
directories. Each platform's run has a cache of its own under
`.workshop/.cache/mypy/`, and each one's verdict gates.

The workspace's namespace is fully strict. The tests and `tasks.py` are
checked as consumer code, so every test body type-checks without a
return annotation on every function.

## The configuration

`fm sync` writes the root `mypy.ini`, and a bare `mypy` in the repository
reads the same file the check does: it checks linux and shares the check's
linux cache. The file names each python member's `src` and `tests` in
`files` and in `mypy_path`: in a namespace package without an
`__init__.py`, mypy derives a module's name from the `mypy_path` entries.

The file is the extension's: an edit outside its region is drift, which
`fm check --fix` puts back. The repository's own sections go inside the
region at the end:

```ini
# -- workshop: region sections, yours to edit; the render keeps it --
[mypy-acme.generated.*]
ignore_errors = True
# -- workshop: end sections --
```

The extension adds mypy to the dev group too, since mypy reads the
members' own stubs from the environment.
