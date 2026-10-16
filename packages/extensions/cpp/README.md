# livery-extensions-cpp

C and C++ as languages, as an extension of the livery workshop. Listed
as `cpp` in a package's `extensions`, or held through an extension that
requires it, such as conan or nanobind, it says which of the package's
files are sources, tests and test support, answers the module queries
for a C or C++ package, and names the compilers the host must provide,
`cc` and `c++`, which `fm doctor` and `fm env.check` look for.
