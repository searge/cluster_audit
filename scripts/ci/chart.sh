#!/bin/sh
# Lint the Helm chart, and on `push` package it into Harbor as an OCI artifact.
#
# Runs in alpine/helm (busybox sh). Reads its inputs from the environment so
# the same script works from .gitlab-ci.yml and by hand:
#
#   scripts/ci/chart.sh          # lint + render, no credentials needed
#   scripts/ci/chart.sh push     # package + push to ${CHARTS_OCI}
#
# Auth reuses the DOCKER_AUTH_CONFIG the image jobs already push with: Helm
# accepts the same docker config.json format via HELM_REGISTRY_CONFIG, so the
# chart needs no credential of its own and rotation stays one variable.
set -eu
. "$(dirname "$0")/lib.sh"

CHART_DIR="${CHART_DIR:-k8s/platform-capacity}"
MODE="${1:-lint}"

setup_ca

log "linting ${CHART_DIR}"
helm lint "${CHART_DIR}"
# Render with defaults to catch template errors lint does not reach; the
# output is discarded, only the exit code matters.
helm template ci "${CHART_DIR}" >/dev/null
log "lint and render OK"

[ "${MODE}" = "push" ] || exit 0

: "${HARBOR_REGISTRY:?HARBOR_REGISTRY is required}"
CHARTS_OCI="${CHARTS_OCI:-oci://${HARBOR_REGISTRY}/galaxy/charts}"

if [ -n "${DOCKER_AUTH_CONFIG:-}" ]; then
    _auth_dir="$(mktemp -d)"
    printf '%s' "${DOCKER_AUTH_CONFIG}" >"${_auth_dir}/config.json"
    HELM_REGISTRY_CONFIG="${_auth_dir}/config.json"
    export HELM_REGISTRY_CONFIG
    log "registry credentials taken from DOCKER_AUTH_CONFIG"
else
    log "DOCKER_AUTH_CONFIG unset — pushing with ambient credentials"
fi

_pkg_dir="$(mktemp -d)"
helm package "${CHART_DIR}" --destination "${_pkg_dir}"

# The version pushed is whatever Chart.yaml says. Pushing the same version
# twice overwrites it in Harbor — bump `version:` in Chart.yaml with any
# change that consumers must notice, since Argo CD pins a chart version.
for _tgz in "${_pkg_dir}"/*.tgz; do
    log "pushing ${_tgz##*/} to ${CHARTS_OCI}"
    helm push "${_tgz}" "${CHARTS_OCI}"
done
log "chart pushed"
