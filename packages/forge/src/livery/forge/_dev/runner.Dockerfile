# The dev rig's Gitea runner. Jobs run in host mode inside this
# container, so the tools the emitted workflows assume must exist in
# it: node for actions/checkout, git for the checkout itself, bash
# for run steps, curl for the uv installer, the docker CLI for
# cibuildwheel's build containers (the host's socket is mounted by
# compose), and a C++ toolchain with cmake so a platform-wheel
# member's editable install compiles in the gate leg. The FROM digest
# is the same pin compose.yaml carried before the build stanza; move
# both together.
FROM docker.gitea.com/act_runner@sha256:2f54d4df2a1e1b69c4b44db53c70dbd57043594b7f48bcf2e685c3b5bdb738e0
RUN apk add --no-cache nodejs git bash curl docker-cli build-base cmake
