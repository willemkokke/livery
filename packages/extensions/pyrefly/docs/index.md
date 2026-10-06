<!-- Seeded from the package-base seeds at
     birth; this file is the workspace's own. Edit it directly:
     nothing rewrites it.
-->
# livery-extensions-pyrefly

pyrefly as a check of the workshop: `typecheck.pyrefly` type-checks the
python packages' sources on every platform at once. A workspace turns it
on by listing the extension:

```toml
[workspace]
extensions = ["pyrefly"]
```

`fm typecheck` then runs it, and `fm check` runs it with the rest of the
gate. A run checks what the configuration includes whatever the run
reaches: a run costs seconds, and the configuration pins the platforms.

## The configuration

`fm sync` writes the root `pyrefly.toml`, and a bare `pyrefly check` in the
repository reads the same file the check does. It includes each python
member's `src` directory, or `tasks.py` in a workspace without one, checks
every platform at the python version the workspace supports, reads the
tool stubs in `typings/`, and holds two error classes at error:
`deprecated` and `unnecessary-type-conversion`. Its preset is `default`:
`strict` asks for `@override`, which `typing` has from Python 3.12 on.

The file is the extension's: an edit outside its region is drift, which
`fm check --fix` puts back. The repository's own tables go inside the
region at the end, after the `[errors]` table the file writes.
