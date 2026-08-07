#!/bin/sh
# Scan the pushed image with dockle.
#
# dockle pulls the image from Harbor over TLS, so it needs the same internal CA
# the build job needs — hence setup_ca here too, rather than only where the push
# happens. dockle is a Go binary and honours SSL_CERT_FILE.
set -eu
. "$(dirname "$0")/lib.sh"

: "${IMAGE_NAME:?IMAGE_NAME is required}"

TAG="${CI_COMMIT_TAG:-${CI_COMMIT_SHORT_SHA:-latest}}"

setup_ca

# GitLab hands registry credentials over as a docker config blob.
if [ -n "${DOCKER_AUTH_CONFIG:-}" ]; then
    _docker_dir="${DOCKER_CONFIG:-${HOME:-/root}/.docker}"
    mkdir -p "${_docker_dir}"
    printf '%s' "${DOCKER_AUTH_CONFIG}" >"${_docker_dir}/config.json"
    log "registry credentials written to ${_docker_dir}/config.json"
else
    log "DOCKER_AUTH_CONFIG unset — pulling anonymously"
fi

log "scanning ${IMAGE_NAME}:${TAG}"
exec dockle "${IMAGE_NAME}:${TAG}"
