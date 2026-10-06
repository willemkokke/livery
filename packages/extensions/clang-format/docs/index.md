<!-- Seeded from the package-base seeds at
     birth; this file is the workspace's own. Edit it directly:
     nothing rewrites it.
-->
# livery-extensions-clang-format

clang-format as a check of the workshop: `format.clang-format` judges one
native package at a time, a `cpp-conan` or a `python-nanobind` member, and
rewrites under `--fix`. A workspace turns it on by listing the extension:

```toml
[workspace]
extensions = ["clang-format"]
```

`fm format` then runs it, and `fm check` runs it with the rest of the gate.
It reads the C and C++ files its claims reach in the package: its sources,
its tests and their support, a new file not yet added to git among them; a
run over named files reads the ones it names. A file out of style refuses,
naming each file, and `fm check --fix` rewrites them.
`fm format.clang-format -- --verbose` hands clang-format the words after
`--`, in each package's call.

## The style

`fm sync` writes a `.clang-format` into each native package, and a bare
`clang-format --style=file` in the package reads the same file the check
does: LLVM's style, four-space indents, 88 columns, and the pointer beside
the type. The file is the extension's, and the gate keeps it matching. A
directory deeper in the package carries its own lines in a `.clang-format`
of its own that begins with `BasedOnStyle: InheritParentConfig`.
