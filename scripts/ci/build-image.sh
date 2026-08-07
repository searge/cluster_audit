#!/bin/sh
# Build and push the container image with rootless BuildKit.
#
# Runs in moby/buildkit:rootless (Alpine/busybox). Reads its inputs from the
# environment so the same script works from .gitlab-ci.yml and by hand.
set -eu
. "$(dirname "$0")/lib.sh"

: "${IMAGE_NAME:?IMAGE_NAME is required}"
: "${HARBOR_REGISTRY:?HARBOR_REGISTRY is required}"

CACHE_IMAGE="${CACHE_IMAGE:-${IMAGE_NAME}:buildcache}"
TAG="${CI_COMMIT_TAG:-${CI_COMMIT_SHORT_SHA:-dev}}"
CONTEXT="${CI_PROJECT_DIR:-$PWD}"

setup_ca

# Only teach buildkitd about a custom CA when there is one. An empty `ca = []`
# entry is worse than no entry at all.
BUILDKITD_FLAGS="${BUILDKITD_FLAGS:---oci-worker-no-process-sandbox}"
if [ -n "${CI_CA_BUNDLE:-}" ]; then
    _conf="${CONTEXT}/.buildkit/buildkitd.toml"
    mkdir -p "$(dirname "${_conf}")"
    cat >"${_conf}" <<EOF
[registry."${HARBOR_REGISTRY}"]
  ca = ["${CI_CA_BUNDLE}"]
EOF
    BUILDKITD_FLAGS="${BUILDKITD_FLAGS} --config ${_conf}"
    log "buildkitd will trust ${CI_CA_BUNDLE} for ${HARBOR_REGISTRY}"
fi
export BUILDKITD_FLAGS

log "building ${IMAGE_NAME}:${TAG}"

exec buildctl-daemonless.sh build \
    --progress=plain \
    --frontend dockerfile.v0 \
    --local context="${CONTEXT}" \
    --local dockerfile="${CONTEXT}" \
    --opt filename=Dockerfile \
    --opt build-arg:PYTHON_BUILD_IMAGE="${PYTHON_BUILD_IMAGE}" \
    --opt build-arg:PYTHON_RUNTIME_IMAGE="${PYTHON_RUNTIME_IMAGE}" \
    --opt build-arg:UV_VERSION="${UV_VERSION}" \
    --opt build-arg:VERSION="${TAG}" \
    --opt build-arg:COMMIT="${CI_COMMIT_SHORT_SHA:-unknown}" \
    --opt build-arg:BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --import-cache type=registry,ref="${CACHE_IMAGE}" \
    --export-cache type=registry,ref="${CACHE_IMAGE}",mode=max \
    --output "type=image,\"name=${IMAGE_NAME}:${TAG},${IMAGE_NAME}:latest\",compression=gzip,force-compression=true,push=true"
