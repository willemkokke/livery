<!-- Seeded from the package-python seeds at
     birth; this file is the workspace's own. Edit it directly:
     nothing rewrites it.
-->
# livery-extensions-clang-tidy

clang-tidy as a check of the livery workshop. Listed as `clang-tidy` in a
workspace's `[workspace] extensions`, it registers `lint.clang-tidy`, which
lints each cpp package's sources over the compilation database its build
writes, and writes the `.clang-tidy` each native package carries.
