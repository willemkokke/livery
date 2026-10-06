<!-- Seeded from the package-base seeds at
     birth; this file is the workspace's own. Edit it directly:
     nothing rewrites it.
-->
# livery-extensions-clang-tidy

clang-tidy as a check of the workshop: `lint.clang-tidy` judges one
`cpp-conan` package at a time, after its build is configured, and a finding
refuses with clang-tidy's own words. A workspace turns it on by listing the
extension:

```toml
[workspace]
extensions = ["clang-tidy"]
```

`fm lint` then runs it, and `fm check` runs it with the rest of the gate. It
reads the C and C++ sources and tests its claims reach in the package, or
the files a run names, against the compilation database the package's kind
says its build writes. A package not configured yet has no database, and
nothing is linted until it has.

The store's clang-tidy is one static binary with no headers of its own: it
takes the host compiler's builtin headers, and the SDK on macOS. A host
whose compiler does not say where they are skips the lint, saying why.

## The checks

`fm sync` writes a `.clang-tidy` into each native package, and a bare
`clang-tidy` in the package reads the same file: the bug, performance and
portability families, with every finding an error, so the gate's verdict is
the exit code. The file is the extension's, and the gate keeps it matching.
A directory deeper in the package carries its own lines in a `.clang-tidy`
of its own with `InheritParentConfig: true`.
