# syntax=docker/dockerfile:1.7
#
# Batch image for the scheduled capacity ingest (`mks capacity-ingest`).
# Not a server: no port, no healthcheck — it runs, writes to Postgres, exits.
#
# kubectl is deliberately absent. The capabilities that shell out to kubectl
# (hygiene-report, workload-efficiency, …) will not work in this image; the
# scheduled path only speaks HTTP to Prometheus and Postgres.

# Docker Hardened Images. `-dev` has a shell, root and pip for the build stage;
# the runtime tag is the same image with all of that stripped and USER 65532
# baked in. Same OS on both sides — the venv is copied across, so the
# interpreter it points at must be the identical build (/usr/bin/python3).
ARG PYTHON_BUILD_IMAGE=dhi.smile.fr/python:3.13-debian13-dev
ARG PYTHON_RUNTIME_IMAGE=dhi.smile.fr/python:3.13-debian13
ARG UV_VERSION=0.9.6

FROM ${PYTHON_BUILD_IMAGE} AS builder

ARG UV_VERSION

# Build the venv at its final runtime path. Console scripts bake an absolute
# shebang, so a venv built in /src and copied to /app would ship a `mks` that
# points at a python which no longer exists.
#
# Copy rather than hardlink: the venv crosses a stage boundary. Bytecode is
# compiled at build time so the job does not pay for it on every run.
ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /src

RUN pip install --no-cache-dir "uv==${UV_VERSION}"

# Dependency layer first: it only rebuilds when the lock file moves.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-default-groups --no-install-project

COPY src ./src
COPY config ./config
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-default-groups --no-editable

FROM ${PYTHON_RUNTIME_IMAGE}

ARG VERSION=dev
ARG COMMIT=unknown
ARG BUILD_DATE=unknown

LABEL org.opencontainers.image.title="mks-audit" \
      org.opencontainers.image.description="OVH MKS capacity audit toolkit" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${COMMIT}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.source="https://git.smile.fr/galaxy/toolbox/mks"

WORKDIR /app

COPY --from=builder --chown=65532:65532 /app/.venv /app/.venv
COPY --from=builder --chown=65532:65532 /src/config /app/config

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER 65532:65532

ENTRYPOINT ["/app/.venv/bin/mks"]
CMD ["--help"]
