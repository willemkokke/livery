# Shipped by the workshop and handed to the gate's configure through
# CMAKE_PROJECT_INCLUDE: the gate build is instrumented for coverage
# by the family of the compiler CMake detected, and the measurer
# beside that compiler reads the run. A release build never includes
# this file.
if(CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
  add_compile_options(--coverage)
  add_link_options(--coverage)
elseif(CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-fprofile-instr-generate -fcoverage-mapping)
  add_link_options(-fprofile-instr-generate)
  if(MSVC)
    # clang-cl on the MSVC ABI: link.exe leaves clang 20's profile
    # names section empty and llvm-profdata refuses the run; lld
    # keeps it, on every clang.
    set(CMAKE_LINKER_TYPE LLD)
  endif()
elseif(MSVC)
  # Microsoft's engine instruments the binary statically after the
  # build, which needs the linker's profile layout.
  add_link_options(/PROFILE)
endif()
