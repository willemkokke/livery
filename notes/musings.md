# Musings

Raw material, dated, never a plan: a musing becomes a plan entry
only when it is ruled.

## 2026-09-09: the nanobind kind as its own layer

Willem, on the wheel build test costing every pull request 77 s:
could the nanobind kind and its build proof live in a separate layer
on PyPI, not for now. The registry already takes a kind from a layer
(`register_kind`), so `livery-workshop-nanobind` could ship the
backend, its seed templates, and the build proof in its own suite.
The catch is where it lives: inside the monorepo it would depend on
workshop, and the affected rule runs dependents, so a workshop change
would still run its build; in its own repository the build runs only
on that package's changes, and the loop keeps proving the kind end to
end through `loop-native`, from the layer's released wheel rather
than a dev wheel. Meanwhile the build runs at the nightly point only
(ruled the same evening).
