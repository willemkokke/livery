# livery-extensions-conan

A C or C++ package's conan recipe, as an extension of the livery
workshop. Listed as `conan` in a package's `extensions`, it stamps the
recipe's version, builds the package into the conan cache with
`conan create`, and releases it to a conan remote, a folder, or the
forge's releases. It requires cpp, which `livery-extensions-cpp` ships.
`fm new.package.cpp-package <name>` creates a C++ package built with
CMake and packaged by conan while a workspace lists the extension.
