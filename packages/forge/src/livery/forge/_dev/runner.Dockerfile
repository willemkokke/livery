# The dev rig's runners, one image for both. Gitea's runner runs jobs
# in host mode and GitLab's gitlab-runner in its shell executor, each
# inside this container, so the tools the emitted workflows assume
# must exist in it: node for actions/checkout, git for the checkout
# itself, bash for run steps, curl for the uv installer, the docker
# CLI for the jobs that build images through the host's socket
# (mounted by `fm forge.dev.up --with-docker`), and a C++ toolchain
# with gcov so a native member builds and is measured in the gate
# leg. The base is glibc, node's own Debian image, at least as new as
# the hosted ubuntu runner's (glibc 2.39 on ubuntu-latest; trixie has
# 2.41): the tools the store downloads are built against it, a musl
# base cannot run them (nodejs.org ships no arm64 musl node) and an
# older glibc refuses them (pyrefly wants 2.39). The
# runner binaries are pinned by version and taken from their releases
# for the building architecture; the run script is Gitea's own, kept
# beside this file. The FROM digest is the image index, one digest for
# every architecture; move it deliberately.
FROM node:24-trixie@sha256:be40f6a87b9b22215ddb20da0a2320a5c6d583fe3ee3b0024d9fa4f05b40c8fd
ARG TARGETARCH
ARG GITEA_RUNNER_VERSION=3.3.1
ARG GITLAB_RUNNER_VERSION=19.1.1
ARG DOCKER_VERSION=29.8.1
RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/* \
    && case "${TARGETARCH}" in \
         amd64) static=x86_64 ;; \
         arm64) static=aarch64 ;; \
         *) echo "no runner build for ${TARGETARCH}" >&2; exit 1 ;; \
       esac \
    && curl -fsSL "https://dl.gitea.com/gitea-runner/${GITEA_RUNNER_VERSION}/gitea-runner-${GITEA_RUNNER_VERSION}-linux-${TARGETARCH}" \
         -o /usr/local/bin/gitea-runner \
    && curl -fsSL "https://gitlab-runner-downloads.s3.amazonaws.com/v${GITLAB_RUNNER_VERSION}/binaries/gitlab-runner-linux-${TARGETARCH}" \
         -o /usr/local/bin/gitlab-runner \
    && curl -fsSL "https://download.docker.com/linux/static/stable/${static}/docker-${DOCKER_VERSION}.tgz" \
         | tar -xz -C /usr/local/bin --strip-components=1 docker/docker \
    && chmod +x /usr/local/bin/gitea-runner /usr/local/bin/gitlab-runner /usr/local/bin/docker \
    && mkdir -p /builds /data
COPY act_run.sh /usr/local/bin/run.sh
RUN chmod +x /usr/local/bin/run.sh
ENTRYPOINT ["/usr/bin/tini", "--", "run.sh"]
